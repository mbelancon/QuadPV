/**
  ******************************************************************************
  * @file    quadpv.c
  * @brief   QuadPV: corrente de curto-circuito (Isc) e tensão de circuito aberto (Voc)
  *          de 4 módulos fotovoltaicos, com redução de ruído.
  *
  * Hardware de cada módulo
  *   módulo (+) -> dreno do MOSFET (IRLZ44N); source -> shunt (2 ohm) -> GND = módulo (-).
  *   O ADC lê a tensão no shunt (corrente) e a tensão do módulo. Gate em 1: módulo em curto
  *   pelo shunt (Isc). Gate em 0: módulo em aberto (Voc), e o shunt fica sem corrente.
  *
  * Redução de ruído
  *   - O TIM3 dispara o ADC a 6000 Hz exatos (cristal). Cada média tem 6000 varreduras =
  *     1 s = 60 ciclos de 60 Hz: a interferência da rede e das harmônicas (inclusive o piscar
  *     de 120 Hz das lâmpadas) cai num zero da média e se cancela.
  *   - As 9 conversões de cada varredura levam 77 us: os 4 módulos são vistos praticamente no
  *     mesmo instante, e as variações de luz (nuvens) se cancelam nas razões entre módulos.
  *   - A janela de Voc (400 varreduras = 4 ciclos de 60 Hz) também mede o offset dos canais
  *     de corrente (shunt sem corrente), para a interface descontar.
  *   - A Vrefint (canal interno 17) entra em toda varredura: a interface acompanha a tensão
  *     de alimentação do ADC em vez de supor 3,3 V.
  *   - Além da média, o firmware manda o desvio-padrão de cada canal, para a interface mostrar
  *     o ruído e a incerteza de cada leitura.
  *
  * Protocolo (texto, uma linha por mensagem; ver docs/03_Firmware.md)
  *   D n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 ... s9     médias do segundo (contagens, 3 decimais)
  *   V n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 ... s9     janela de Voc (módulos em aberto)
  *   # ...                                             resposta a comandos
  *   n = varreduras promediadas; m = modo (C curto, O aberto); a = shunts; v = módulos;
  *   r = Vrefint; s = desvio-padrão de uma amostra de cada canal (contagens, 2 decimais).
  *
  * Comandos: ? (identificação), V (mede Voc agora), T s (Voc a cada s segundos; 0 = nunca),
  *           M C (curto: mede Isc; padrão), M O (aberto: mede Voc continuamente).
  ******************************************************************************
  */
#include "quadpv.h"
#include "main.h"
#include "usbd_cdc_if.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>

extern ADC_HandleTypeDef hadc1;
extern TIM_HandleTypeDef htim3;
extern USBD_HandleTypeDef hUsbDeviceFS;

/* ------------------------------------------------------------------ Parâmetros */
#define NCH             9         /* a1..a4 (shunts), v1..v4 (módulos), Vrefint */
#define FS_HZ           6000U     /* varreduras por segundo (TIM3) */
#define BLOCK_SCANS     16U       /* varreduras por meia-buffer do DMA (~2,7 ms) */
#define BLOCKS_PER_SEC  (FS_HZ / BLOCK_SCANS)       /* 375 */
#define VOC_SETTLE_BLK  8U        /* 21 ms com os MOSFETs abertos antes de medir */
#define VOC_MEAS_BLK    25U       /* 400 varreduras = 66,7 ms = 4 ciclos de 60 Hz */
#define SWITCH_SETTLE_BLK 8U      /* 21 ms descartados depois de uma troca de modo */
#define FIRST_VOC_S     2U        /* primeira janela de Voc 2 s depois de ligar */
#define TX_TIMEOUT_MS   50U

typedef struct
{
  uint32_t n;
  char     kind;                 /* 'D' ou 'V' */
  char     mode;                 /* 'C' ou 'O' */
  uint32_t sum[NCH];
  uint64_t sumsq[NCH];
} Accum;

/* ------------------------------------------------------------------ Estado */
static uint16_t adc_buf[2U * BLOCK_SCANS * NCH];

/* acumulação (só a interrupção do DMA mexe) */
static Accum    acc;
static uint32_t blk = 0;                /* bloco dentro do segundo atual */
static uint32_t settle_left = 0;        /* blocos a descartar antes de recomeçar a contar */
static uint8_t  voc_now = 0;            /* este segundo é uma janela de Voc */
static Accum    acc_voc;

/* resultados prontos (interrupção -> laço principal) */
static Accum    ready_d, ready_v;
static volatile uint8_t ready_d_flag = 0, ready_v_flag = 0;
static volatile uint32_t lost = 0;      /* segundos perdidos (laço principal atrasado) */

/* pedidos (laço principal -> interrupção) */
static volatile uint8_t voc_request = 0;
static volatile uint8_t restart_request = 0;
static volatile char    mode = 'C';

static uint32_t voc_interval_s = 60;
static uint32_t secs_since_voc = 0;

/* comandos recebidos: a interrupção do USB põe os bytes numa fila circular e o laço principal
 * monta as linhas (vários comandos seguidos não se perdem) */
#define RX_RING 256U
static volatile uint8_t rx_ring[RX_RING];
static volatile uint16_t rx_head = 0, rx_tail = 0;
static char    rx_line[48];
static uint8_t rx_len = 0;

/* transmissão */
static char    tx_buf[512];
static uint16_t tx_len = 0;
static uint32_t tx_stalled_until = 0;

/* ------------------------------------------------------------------ Gates */
static void gates(uint8_t on)
{
  HAL_GPIO_WritePin(GATE_GPIO_Port, GATE_ALL_Pins, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

static void gates_for_mode(void)
{
  gates(mode == 'C');
}

/* ------------------------------------------------------------------ ADC / DMA */
static void acc_reset(Accum *a, char kind)
{
  memset(a, 0, sizeof(*a));
  a->kind = kind;
  a->mode = mode;
}

static void acc_add(Accum *a, const uint16_t *p)
{
  for (uint32_t s = 0; s < BLOCK_SCANS; s++, p += NCH)
  {
    for (uint32_t c = 0; c < NCH; c++)
    {
      uint32_t x = p[c];
      a->sum[c] += x;
      a->sumsq[c] += (uint64_t)(x * x);
    }
  }
  a->n += BLOCK_SCANS;
}

static void publish(const Accum *a)
{
  if (a->kind == 'V')
  {
    if (ready_v_flag)
    {
      lost++;
    }
    ready_v = *a;
    ready_v_flag = 1;
  }
  else
  {
    if (ready_d_flag)
    {
      lost++;
    }
    ready_d = *a;
    ready_d_flag = 1;
  }
}

/* Chamado a cada 16 varreduras (meia-buffer), ~2,7 ms */
static void on_block(const uint16_t *p)
{
  if (restart_request)                   /* troca de modo: recomeça o segundo, sem misturar */
  {
    restart_request = 0;
    voc_now = 0;
    blk = 0;
    settle_left = SWITCH_SETTLE_BLK;
    gates_for_mode();
    acc_reset(&acc, 'D');
    return;
  }
  if (settle_left > 0U)
  {
    settle_left--;
    return;
  }

  if (blk == 0U)                         /* início de um segundo */
  {
    acc_reset(&acc, 'D');
    if (voc_request)
    {
      voc_request = 0;
      voc_now = 1;
      gates(0);                          /* abre os 4 módulos ao mesmo tempo */
      acc_reset(&acc_voc, 'V');
      acc_voc.mode = 'O';
    }
  }

  if (voc_now)
  {
    if (blk >= VOC_SETTLE_BLK && blk < VOC_SETTLE_BLK + VOC_MEAS_BLK)
    {
      acc_add(&acc_voc, p);
    }
    else if (blk == VOC_SETTLE_BLK + VOC_MEAS_BLK)
    {
      gates_for_mode();                  /* volta ao modo; o resto do segundo é descartado */
      publish(&acc_voc);
    }
  }
  else
  {
    acc_add(&acc, p);
  }

  blk++;
  if (blk == BLOCKS_PER_SEC)
  {
    blk = 0;
    if (voc_now)
    {
      voc_now = 0;                       /* segundo da janela de Voc: sem linha D */
    }
    else
    {
      publish(&acc);
    }
  }
}

void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *hadc)
{
  if (hadc->Instance == ADC1)
  {
    on_block(&adc_buf[0]);
  }
}

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
  if (hadc->Instance == ADC1)
  {
    on_block(&adc_buf[BLOCK_SCANS * NCH]);
  }
}

/* ------------------------------------------------------------------ USB TX */
static uint8_t usb_ready(void)
{
  return hUsbDeviceFS.dev_state == USBD_STATE_CONFIGURED && hUsbDeviceFS.pClassData != NULL;
}

static uint8_t tx_busy(void)
{
  USBD_CDC_HandleTypeDef *h = (USBD_CDC_HandleTypeDef *)hUsbDeviceFS.pClassData;
  return h != NULL && h->TxState != 0U;
}

static void tx_flush(void)
{
  if (tx_len == 0U)
  {
    return;
  }
  uint32_t now = HAL_GetTick();
  if (!usb_ready() || (int32_t)(tx_stalled_until - now) > 0)
  {
    tx_len = 0;                               /* ninguém lendo: descarta */
    return;
  }
  uint32_t t0 = now;
  while (CDC_Transmit_FS((uint8_t *)tx_buf, tx_len) == USBD_BUSY)
  {
    if (HAL_GetTick() - t0 > TX_TIMEOUT_MS)
    {
      tx_stalled_until = HAL_GetTick() + 1000U;   /* porta fechada no PC: tenta de novo em 1 s */
      tx_len = 0;
      return;
    }
  }
  while (tx_busy())                           /* o buffer só pode ser reusado no fim */
  {
    if (HAL_GetTick() - t0 > TX_TIMEOUT_MS)
    {
      tx_stalled_until = HAL_GetTick() + 1000U;
      break;
    }
  }
  tx_len = 0;
}

static void tx_put(const char *s)
{
  size_t n = strlen(s);
  while (n > 0U)
  {
    size_t room = sizeof(tx_buf) - tx_len;
    size_t k = n < room ? n : room;
    memcpy(&tx_buf[tx_len], s, k);
    tx_len += (uint16_t)k;
    s += k;
    n -= k;
    if (tx_len == sizeof(tx_buf))
    {
      tx_flush();
    }
  }
}

/* Envia uma linha D ou V: médias com 3 decimais e desvios-padrão com 2 (sem printf de float) */
static void send_accum(const Accum *a)
{
  char b[32];
  if (a->n < 2U)
  {
    return;
  }
  snprintf(b, sizeof(b), "%c %lu %c", a->kind, (unsigned long)a->n, a->mode);
  tx_put(b);
  for (int c = 0; c < NCH; c++)
  {
    uint64_t m1000 = ((uint64_t)a->sum[c] * 1000U + a->n / 2U) / a->n;
    snprintf(b, sizeof(b), " %lu.%03lu", (unsigned long)(m1000 / 1000U), (unsigned long)(m1000 % 1000U));
    tx_put(b);
  }
  for (int c = 0; c < NCH; c++)
  {
    double s = (double)a->sum[c];
    double var = ((double)a->sumsq[c] - s * s / (double)a->n) / (double)(a->n - 1U);
    uint32_t s100 = var > 0.0 ? (uint32_t)(sqrt(var) * 100.0 + 0.5) : 0U;
    snprintf(b, sizeof(b), " %lu.%02lu", (unsigned long)(s100 / 100U), (unsigned long)(s100 % 100U));
    tx_put(b);
  }
  tx_put("\r\n");
  tx_flush();
}

static void send_info(void)
{
  char b[112];
  snprintf(b, sizeof(b), "# QuadPV fw %s tipo=Isc-Voc modo=%c T=%lu fs=%lu perdidos=%lu\r\n",
           QPV_FW_VERSION, mode, (unsigned long)voc_interval_s, (unsigned long)FS_HZ,
           (unsigned long)lost);
  tx_put(b);
  tx_flush();
}

/* ------------------------------------------------------------------ Comandos */
void QPV_OnUsbRx(const uint8_t *buf, uint32_t len)
{
  for (uint32_t k = 0; k < len; k++)
  {
    uint16_t next = (uint16_t)((rx_head + 1U) % RX_RING);
    if (next == rx_tail)
    {
      return;                                 /* fila cheia: descarta o resto */
    }
    rx_ring[rx_head] = buf[k];
    rx_head = next;
  }
}

/* Tira bytes da fila; devolve 1 quando uma linha completa está em rx_line */
static uint8_t next_line(void)
{
  while (rx_tail != rx_head)
  {
    char ch = (char)rx_ring[rx_tail];
    rx_tail = (uint16_t)((rx_tail + 1U) % RX_RING);
    if (ch == '\n' || ch == '\r')
    {
      if (rx_len > 0U)
      {
        rx_line[rx_len] = '\0';
        rx_len = 0;
        return 1;
      }
    }
    else if (rx_len < sizeof(rx_line) - 1U)
    {
      rx_line[rx_len++] = ch;
    }
  }
  return 0;
}

static void handle_command(void)
{
  char *p = rx_line;
  while (*p == ' ')
  {
    p++;
  }
  switch (p[0])
  {
    case '?':
      send_info();
      return;
    case 'V':
    case 'v':
      voc_request = 1;
      secs_since_voc = 0;
      tx_put("# ok Voc\r\n");
      break;
    case 'T':
    case 't':
    {
      long v = strtol(p + 1, NULL, 10);
      if (v < 0 || v > 86400)
      {
        tx_put("# erro: T deve ficar entre 0 e 86400 s\r\n");
        break;
      }
      voc_interval_s = (uint32_t)v;
      secs_since_voc = 0;
      send_info();
      return;
    }
    case 'M':
    case 'm':
    {
      char *q = p + 1;
      while (*q == ' ')
      {
        q++;
      }
      char md = (char)(*q & ~0x20);           /* maiúscula */
      if (md != 'C' && md != 'O')
      {
        tx_put("# erro: modos C (curto, Isc) e O (aberto, Voc)\r\n");
        break;
      }
      mode = md;
      restart_request = 1;                    /* a interrupção troca os gates e recomeça o segundo */
      send_info();
      return;
    }
    default:
      tx_put("# erro: comando desconhecido (?, V, T s, M C|O)\r\n");
      break;
  }
  tx_flush();
}

/* ------------------------------------------------------------------ Laço */
void QPV_Init(void)
{
  acc_reset(&acc, 'D');
  gates_for_mode();
  settle_left = SWITCH_SETTLE_BLK;
  if (HAL_ADCEx_Calibration_Start(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_ADC_Start_DMA(&hadc1, (uint32_t *)adc_buf, 2U * BLOCK_SCANS * NCH) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_Base_Start(&htim3) != HAL_OK)       /* começa a disparar o ADC */
  {
    Error_Handler();
  }
  secs_since_voc = (voc_interval_s > FIRST_VOC_S) ? voc_interval_s - FIRST_VOC_S : 0U;
}

void QPV_Loop(void)
{
  while (next_line())
  {
    handle_command();
  }

  if (ready_v_flag)
  {
    Accum a;
    __disable_irq();
    a = ready_v;
    ready_v_flag = 0;
    __enable_irq();
    send_accum(&a);
  }

  if (ready_d_flag)
  {
    Accum a;
    __disable_irq();
    a = ready_d;
    ready_d_flag = 0;
    __enable_irq();
    send_accum(&a);
    HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);

    /* agenda a próxima janela de Voc */
    secs_since_voc++;
    if (voc_interval_s > 0U && secs_since_voc >= voc_interval_s)
    {
      secs_since_voc = 0;
      voc_request = 1;
    }
  }
}
