/**
  ******************************************************************************
  * @file    quadpv.c
  * @brief   QuadPV: 4 módulos fotovoltaicos, cada um com um MOSFET como carga.
  *
  * Aquisição
  *   ADC1 em modo scan contínuo (9 canais, ~15,9 mil varreduras/s) com DMA circular.
  *   A interrupção do DMA soma as amostras em dois acumuladores:
  *     - o do segundo (médias enviadas uma vez por segundo, como no firmware antigo);
  *     - o do ponto (médias de ~2 ms usadas na varredura I-V).
  *
  * Carga
  *   TIM4 gera PWM de 10 bits (0..1023) em PB6..PB9. Um filtro RC transforma o PWM numa
  *   referência de 0 a 3,3 V; um divisor a reduz para 0 a ~330 mV e um amplificador
  *   operacional ajusta o gate do MOSFET até a queda no resistor de 1 ohm do source igualar
  *   essa referência. Resultado: carga de corrente constante, linear no PWM
  *   (~0,32 mA por contagem, 0 a ~330 mA): 0 = MOSFET aberto (Voc); valor acima de Isc =
  *   MOSFET saturado (perto do curto-circuito, Isc). Ver docs/02_Hardware_e_Ligacoes.md.
  *
  * Modos (comando M): P = rastreia o ponto de máxima potência (perturba e observa, 1 vez
  *   por segundo); F d = gate fixo em d; O = aberto; C = curto.
  *
  * Varredura I-V (comando S, e a cada T segundos): passada grossa de 0 a 1023 para achar,
  *   em cada módulo, a faixa de gate em que a corrente vai de ~0 a ~Isc; depois passada fina
  *   de QPV_NF pontos dentro dessa faixa. Os quatro módulos são varridos ao mesmo tempo.
  *
  * Protocolo (texto, uma linha por mensagem, ver docs/03_Firmware.md)
  *   a cada ~1 s:  A0 A1 A5 A2 A6 A3 A7 A4 A8      (médias em contagens, mesmo formato antigo)
  *                 G d1 d2 d3 d4 modo                (gate de cada módulo, 0..1023)
  *   após varrer:  IV m n  ref i v d  ref i v d ...  (n pontos do módulo m, contagens)
  *   respostas:    # ...
  ******************************************************************************
  */
#include "quadpv.h"
#include "main.h"
#include "usbd_cdc_if.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

extern ADC_HandleTypeDef hadc1;
extern TIM_HandleTypeDef htim4;
extern USBD_HandleTypeDef hUsbDeviceFS;

/* ------------------------------------------------------------------ Parâmetros */
#define NCH            9          /* canais do ADC: A0 ref, A1..A4 corrente, A5..A8 tensão */
#define NMOD           4
#define DUTY_MAX       1023
#define DMA_SCANS      32         /* varreduras no buffer circular (metade = 16, ~1 ms) */
#define QPV_NF         100        /* pontos da passada fina da varredura I-V */
#define COARSE_STEP    32
#define N_COARSE       (DUTY_MAX / COARSE_STEP + 2)   /* 0, 32, ..., 992, 1023 = 33 pontos */
#define SETTLE_MS      3          /* > 5 constantes de tempo do RC da referência (1 kΩ x 470 nF) */
#define SETTLE_JUMP_MS 12         /* depois de um salto grande do gate */
#define AVG_MS         2          /* média de cada ponto (~32 varreduras do ADC) */
#define MPPT_STEP      2          /* passo do perturba e observa, em contagens do PWM */
#define MPPT_JUMP      16         /* passo grande quando V sai da faixa plausível do MPP */
#define V_MPP_MIN      0.50f      /* V < 50 % de Voc: corrente pedida acima de Isc (curva plana) */
#define V_MPP_MAX      0.95f      /* V > 95 % de Voc: carga leve demais */
#define CLOUD_FRAC     0.25f      /* P muda > 25 % em 1 s: borda de nuvem, refaz a varredura */
#define CLOUD_HOLD_MS  10000U     /* no máximo uma varredura extra a cada 10 s */
#define FRAC_LO        0.03f      /* abaixo de 3 % de Isc: MOSFET ainda sem conduzir */
#define FRAC_HI        0.98f      /* a partir de 98 % de Isc (ou V < 3 % de Voc): curva já plana */
#define FRAC_V         0.03f
#define DARK_I_X16     (3 * 16)   /* corrente < 3 contagens (~2,7 mA) = escuro, sem passada fina */
#define TX_TIMEOUT_MS  50

/* ------------------------------------------------------------------ Estado */
static uint16_t adc_buf[DMA_SCANS * NCH];

static volatile uint32_t acc_sec[NCH], n_sec;
static volatile uint32_t acc_pt[NCH], n_pt;
static volatile uint8_t  pt_on;

static char    mode = 'P';                /* P, F, O, C */
static uint16_t fixed_duty = 0;
static uint16_t duty[NMOD];
static uint16_t voc_x16[NMOD];                 /* Voc da última varredura (x16) */
static int32_t  i_off_x16[NMOD];               /* saída do sensor - ref com o MOSFET aberto */
static uint8_t  have_sweep = 0;
static int8_t   mppt_dir[NMOD] = {1, 1, 1, 1};
static float    mppt_last_p[NMOD];

static uint32_t sweep_interval_s = 60;
static volatile uint8_t sweep_request = 0;
static uint32_t t_last_sec, t_last_sweep, t_boot_sweep;

/* Comandos recebidos (montados na interrupção do USB, tratados no laço principal) */
static char    rx_line[48];
static volatile uint8_t rx_len = 0, rx_ready = 0;

/* Transmissão */
static char    tx_buf[512];
static uint16_t tx_len = 0;
static uint32_t tx_stalled_until = 0;

/* Pontos da varredura (médias x16) */
static uint16_t c_ref[N_COARSE], c_i[NMOD][N_COARSE], c_v[NMOD][N_COARSE];
static uint16_t f_ref[QPV_NF], f_i[NMOD][QPV_NF], f_v[NMOD][QPV_NF];
static uint16_t f_lo[NMOD], f_hi[NMOD];

/* ------------------------------------------------------------------ ADC / DMA */
static void accumulate(const uint16_t *p)
{
  for (uint32_t s = 0; s < DMA_SCANS / 2; s++, p += NCH)
  {
    if (n_sec < 1000000U)                 /* evita estouro se o laço principal atrasar muito */
    {
      for (uint32_t c = 0; c < NCH; c++)
      {
        acc_sec[c] += p[c];
      }
      n_sec++;
    }
    if (pt_on)
    {
      for (uint32_t c = 0; c < NCH; c++)
      {
        acc_pt[c] += p[c];
      }
      n_pt++;
    }
  }
}

void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *hadc)
{
  if (hadc->Instance == ADC1)
  {
    accumulate(&adc_buf[0]);
  }
}

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
  if (hadc->Instance == ADC1)
  {
    accumulate(&adc_buf[(DMA_SCANS / 2) * NCH]);
  }
}

static void reset_sec(void)
{
  __disable_irq();
  for (int c = 0; c < NCH; c++)
  {
    acc_sec[c] = 0;
  }
  n_sec = 0;
  __enable_irq();
}

static void wait_ms(uint32_t ms)
{
  uint32_t t0 = HAL_GetTick();
  while ((HAL_GetTick() - t0) < ms)
  {
  }
}

/* Média de um ponto (x16) depois de esperar o gate assentar */
static void measure_point(uint32_t settle_ms, uint16_t out[NCH])
{
  wait_ms(settle_ms);
  __disable_irq();
  for (int c = 0; c < NCH; c++)
  {
    acc_pt[c] = 0;
  }
  n_pt = 0;
  pt_on = 1;
  __enable_irq();
  wait_ms(AVG_MS);
  while (n_pt < 8U)
  {
  }
  __disable_irq();
  pt_on = 0;
  uint32_t n = n_pt;
  uint32_t a[NCH];
  for (int c = 0; c < NCH; c++)
  {
    a[c] = acc_pt[c];
  }
  __enable_irq();
  for (int c = 0; c < NCH; c++)
  {
    out[c] = (uint16_t)((a[c] * 16U + n / 2U) / n);
  }
}

/* ------------------------------------------------------------------ PWM */
static void apply_duty(void)
{
  __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_1, duty[0]);
  __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_2, duty[1]);
  __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_3, duty[2]);
  __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_4, duty[3]);
}

static void set_all(uint16_t d)
{
  for (int m = 0; m < NMOD; m++)
  {
    duty[m] = d;
  }
  apply_duty();
}

/* Gate de cada módulo conforme o modo atual */
static void apply_mode(void)
{
  switch (mode)
  {
    case 'O':
      set_all(0);
      break;
    case 'C':
      set_all(DUTY_MAX);
      break;
    case 'F':
      set_all(fixed_duty);
      break;
    default:                                  /* 'P': começa no ponto de máxima potência */
      for (int m = 0; m < NMOD; m++)
      {
        mppt_dir[m] = 1;
        mppt_last_p[m] = 0.0f;
      }
      apply_duty();
      break;
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

/* Valor x16 -> texto com 2 decimais (ex.: 32776 -> "2048.50") */
static void put_x16(uint16_t v)
{
  char b[16];
  snprintf(b, sizeof(b), " %u.%02u", (unsigned)(v >> 4), (unsigned)(((v & 15U) * 100U + 8U) / 16U));
  tx_put(b);
}

/* ------------------------------------------------------------------ Envio por segundo */
static void send_second(void)
{
  uint32_t a[NCH], n;
  __disable_irq();
  for (int c = 0; c < NCH; c++)
  {
    a[c] = acc_sec[c];
    acc_sec[c] = 0;
  }
  n = n_sec;
  n_sec = 0;
  __enable_irq();
  if (n == 0U)
  {
    return;
  }

  /* MPPT com as médias do segundo */
  if (mode == 'P' && have_sweep)
  {
    uint8_t cloud = 0;
    for (int m = 0; m < NMOD; m++)
    {
      /* médias (o número de amostras por segundo varia um pouco) */
      float i = ((float)a[1 + m] - (float)a[0]) / (float)n - (float)i_off_x16[m] / 16.0f;
      float v = (float)a[5 + m] / (float)n;
      float p = (i < 0 ? -i : i) * v;
      float voc = (float)voc_x16[m] / 16.0f;
      float last = mppt_last_p[m];
      if (last > 0.0f && (p < (1.0f - CLOUD_FRAC) * last || p > last / (1.0f - CLOUD_FRAC)))
      {
        cloud = 1;
      }
      int32_t d = (int32_t)duty[m];
      if (v < V_MPP_MIN * voc)
      {
        d -= MPPT_JUMP;                       /* passou do joelho: alivia a carga depressa */
        mppt_dir[m] = -1;
      }
      else if (v > V_MPP_MAX * voc)
      {
        d += MPPT_JUMP;                       /* quase em aberto: aumenta a carga depressa */
        mppt_dir[m] = 1;
      }
      else
      {
        if (p < last)                         /* perturba e observa */
        {
          mppt_dir[m] = (int8_t)-mppt_dir[m];
        }
        d += mppt_dir[m] * MPPT_STEP;
      }
      mppt_last_p[m] = p;
      if (d < 0)
      {
        d = 0;
        mppt_dir[m] = 1;
      }
      if (d > DUTY_MAX)
      {
        d = DUTY_MAX;
        mppt_dir[m] = -1;
      }
      duty[m] = (uint16_t)d;
    }
    apply_duty();
    if (cloud && (HAL_GetTick() - t_last_sweep) >= CLOUD_HOLD_MS)
    {
      sweep_request = 1;
    }
  }

  /* Médias na ordem do firmware antigo: A0 A1 A5 A2 A6 A3 A7 A4 A8, 2 decimais */
  static const uint8_t order[NCH] = {0, 1, 5, 2, 6, 3, 7, 4, 8};
  char b[24];
  for (int k = 0; k < NCH; k++)
  {
    uint64_t v100 = ((uint64_t)a[order[k]] * 100U + n / 2U) / n;
    snprintf(b, sizeof(b), "%s%lu.%02lu", k ? " " : "",
             (unsigned long)(v100 / 100U), (unsigned long)(v100 % 100U));
    tx_put(b);
  }
  snprintf(b, sizeof(b), "\r\nG %u %u", duty[0], duty[1]);
  tx_put(b);
  snprintf(b, sizeof(b), " %u %u %c\r\n", duty[2], duty[3], mode);
  tx_put(b);
  tx_flush();
  HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
}

/* ------------------------------------------------------------------ Varredura I-V */
static int32_t irel(uint16_t i, uint16_t ref, int m)
{
  return (int32_t)i - (int32_t)ref - i_off_x16[m];
}

static void sweep(void)
{
  uint16_t pt[NCH];

  /* Passada grossa: mesmo gate nos 4 módulos */
  for (int k = 0; k < N_COARSE; k++)
  {
    uint16_t d = (k == N_COARSE - 1) ? DUTY_MAX : (uint16_t)(k * COARSE_STEP);
    set_all(d);
    measure_point(k == 0 ? SETTLE_JUMP_MS : SETTLE_MS, pt);
    c_ref[k] = pt[0];
    for (int m = 0; m < NMOD; m++)
    {
      c_i[m][k] = pt[1 + m];
      c_v[m][k] = pt[5 + m];
    }
  }

  /* Faixa útil de cada módulo */
  for (int m = 0; m < NMOD; m++)
  {
    i_off_x16[m] = (int32_t)c_i[m][0] - (int32_t)c_ref[0];     /* MOSFET aberto: corrente nula */
    int32_t isc = irel(c_i[m][N_COARSE - 1], c_ref[N_COARSE - 1], m);
    int32_t isc_abs = isc < 0 ? -isc : isc;
    float voc = (float)c_v[m][0];
    int lo = 0, hi = N_COARSE - 1;
    if (isc_abs > DARK_I_X16)
    {
      for (int k = 0; k < N_COARSE; k++)
      {
        int32_t ik = irel(c_i[m][k], c_ref[k], m);
        if ((float)(ik < 0 ? -ik : ik) < FRAC_LO * (float)isc_abs)
        {
          lo = k;
        }
        else
        {
          break;
        }
      }
      for (int k = lo + 1; k < N_COARSE; k++)
      {
        int32_t ik = irel(c_i[m][k], c_ref[k], m);
        if ((float)(ik < 0 ? -ik : ik) >= FRAC_HI * (float)isc_abs || (float)c_v[m][k] < FRAC_V * voc)
        {
          hi = k + 1 < N_COARSE ? k + 1 : k;     /* um passo além, para incluir o joelho inteiro */
          break;
        }
      }
      if (hi <= lo)
      {
        hi = lo + 1;
      }
    }
    f_lo[m] = (lo == N_COARSE - 1) ? DUTY_MAX : (uint16_t)(lo * COARSE_STEP);
    f_hi[m] = (hi == N_COARSE - 1) ? DUTY_MAX : (uint16_t)(hi * COARSE_STEP);
  }

  /* Passada fina: QPV_NF pontos entre f_lo e f_hi de cada módulo, ao mesmo tempo */
  for (int k = 0; k < QPV_NF; k++)
  {
    for (int m = 0; m < NMOD; m++)
    {
      duty[m] = (uint16_t)(f_lo[m] + ((uint32_t)(f_hi[m] - f_lo[m]) * (uint32_t)k) / (QPV_NF - 1));
    }
    apply_duty();
    measure_point(k == 0 ? SETTLE_JUMP_MS : SETTLE_MS, pt);
    f_ref[k] = pt[0];
    for (int m = 0; m < NMOD; m++)
    {
      f_i[m][k] = pt[1 + m];
      f_v[m][k] = pt[5 + m];
    }
  }

  /* Ponto de máxima potência e faixa do MPPT */
  for (int m = 0; m < NMOD; m++)
  {
    float best = -1.0f;
    uint16_t best_d = f_lo[m];
    for (int k = 0; k < QPV_NF; k++)
    {
      int32_t ik = irel(f_i[m][k], f_ref[k], m);
      float p = (float)(ik < 0 ? -ik : ik) * (float)f_v[m][k];
      if (p > best)
      {
        best = p;
        best_d = (uint16_t)(f_lo[m] + ((uint32_t)(f_hi[m] - f_lo[m]) * (uint32_t)k) / (QPV_NF - 1));
      }
    }
    voc_x16[m] = c_v[m][0];
    if (mode == 'P')
    {
      duty[m] = best_d;
    }
  }
  have_sweep = 1;
  apply_mode();            /* volta ao modo: no MPPT, parte do ponto de máxima potência achado */

  /* Envio: aberto (gate 0), passada fina, curto (gate 1023) */
  char b[40];
  for (int m = 0; m < NMOD; m++)
  {
    snprintf(b, sizeof(b), "IV %d %d", m + 1, QPV_NF + 2);
    tx_put(b);
    put_x16(c_ref[0]);
    put_x16(c_i[m][0]);
    put_x16(c_v[m][0]);
    tx_put(" 0");
    for (int k = 0; k < QPV_NF; k++)
    {
      put_x16(f_ref[k]);
      put_x16(f_i[m][k]);
      put_x16(f_v[m][k]);
      snprintf(b, sizeof(b), " %u",
               (unsigned)(f_lo[m] + ((uint32_t)(f_hi[m] - f_lo[m]) * (uint32_t)k) / (QPV_NF - 1)));
      tx_put(b);
    }
    put_x16(c_ref[N_COARSE - 1]);
    put_x16(c_i[m][N_COARSE - 1]);
    put_x16(c_v[m][N_COARSE - 1]);
    snprintf(b, sizeof(b), " %u\r\n", DUTY_MAX);
    tx_put(b);
    tx_flush();
  }
}

/* ------------------------------------------------------------------ Comandos */
void QPV_OnUsbRx(const uint8_t *buf, uint32_t len)
{
  for (uint32_t k = 0; k < len; k++)
  {
    char ch = (char)buf[k];
    if (rx_ready)
    {
      return;                                 /* comando anterior ainda não tratado */
    }
    if (ch == '\n' || ch == '\r')
    {
      if (rx_len > 0U)
      {
        rx_line[rx_len] = '\0';
        rx_ready = 1;
      }
    }
    else if (rx_len < sizeof(rx_line) - 1U)
    {
      rx_line[rx_len++] = ch;
    }
  }
}

static void send_info(void)
{
  char b[96];
  snprintf(b, sizeof(b), "# QuadPV fw %s modo=%c gate=%u T=%lu NF=%d\r\n",
           QPV_FW_VERSION, mode, fixed_duty, (unsigned long)sweep_interval_s, QPV_NF);
  tx_put(b);
  tx_flush();
}

static void handle_command(void)
{
  char line[sizeof(rx_line)];
  __disable_irq();
  strcpy(line, rx_line);
  rx_len = 0;
  rx_ready = 0;
  __enable_irq();

  char *p = line;
  while (*p == ' ')
  {
    p++;
  }
  switch (p[0])
  {
    case '?':
      send_info();
      return;
    case 'S':
    case 's':
      sweep_request = 1;
      tx_put("# ok varredura\r\n");
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
      sweep_interval_s = (uint32_t)v;
      t_last_sweep = HAL_GetTick();
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
      if (md == 'F')
      {
        long v = strtol(q + 1, NULL, 10);
        if (v < 0 || v > DUTY_MAX)
        {
          tx_put("# erro: gate entre 0 e 1023\r\n");
          break;
        }
        fixed_duty = (uint16_t)v;
      }
      else if (md != 'P' && md != 'O' && md != 'C')
      {
        tx_put("# erro: modos P, F d, O, C\r\n");
        break;
      }
      mode = md;
      apply_mode();
      send_info();
      return;
    }
    default:
      tx_put("# erro: comando desconhecido (?, S, T s, M P|F d|O|C)\r\n");
      break;
  }
  tx_flush();
}

/* ------------------------------------------------------------------ Laço */
void QPV_Init(void)
{
  set_all(0);
  if (HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_1) != HAL_OK ||
      HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_2) != HAL_OK ||
      HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_3) != HAL_OK ||
      HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_4) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_ADCEx_Calibration_Start(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_ADC_Start_DMA(&hadc1, (uint32_t *)adc_buf, DMA_SCANS * NCH) != HAL_OK)
  {
    Error_Handler();
  }
  t_last_sec = HAL_GetTick();
  t_last_sweep = t_last_sec;
  /* primeira varredura 3 s depois de ligar (até lá o MPPT não tem faixa e o gate fica em 0) */
  t_boot_sweep = t_last_sec + 3000U;
  sweep_request = 1;
}

void QPV_Loop(void)
{
  uint32_t now = HAL_GetTick();

  if (rx_ready)
  {
    handle_command();
  }

  if ((now - t_last_sec) >= 1000U)
  {
    t_last_sec += 1000U;
    if ((now - t_last_sec) > 1000U)           /* atrasou (varredura): ressincroniza */
    {
      t_last_sec = now;
    }
    send_second();
  }

  if ((int32_t)(now - t_boot_sweep) < 0)
  {
    return;
  }
  if (sweep_request ||
      (sweep_interval_s > 0U && (now - t_last_sweep) >= sweep_interval_s * 1000U))
  {
    sweep_request = 0;
    sweep();
    t_last_sweep = HAL_GetTick();
    reset_sec();                              /* o segundo em curso misturou pontos da varredura */
    t_last_sec = HAL_GetTick();
  }
}
