/* Banco de testes do firmware 3.x no PC.
 * Roda o mesmo quadpv.c contra um modelo: 4 módulos (Isc 120 mA x transmitância da cobertura),
 * shunt de 2 ohm + MOSFET, ADC de 12 bits a 6 kHz com ruído branco (1,5 contagem), offset por
 * canal, interferência de 60 Hz (5 contagens) em todos os canais e cintilação de 120 Hz (2 %)
 * na luz, e o deslocamento de ~8,7 contagens nos canais de corrente (470 k ao 3,3 V). Confere o protocolo, o cancelamento da rede, os offsets na janela de Voc e os comandos.
 * Uso: ./sim saida.txt */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "main.h"
#include "usbd_cdc_if.h"
#include "quadpv.h"

void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *hadc);
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc);

ADC_HandleTypeDef hadc1 = {ADC1};
TIM_HandleTypeDef htim3;
static USBD_CDC_HandleTypeDef cdc;
USBD_HandleTypeDef hUsbDeviceFS = {USBD_STATE_CONFIGURED, &cdc};

static const double K = 3300.0 / 4096.0;              /* mV por contagem */
static const double FACT[4] = {1.00, 0.97, 0.93, 1.02};
static const double OFF[9] = {1.8, -1.2, 0.6, 2.4, -0.9, 1.1, 0.3, -1.6, 0.0};   /* offset do ADC */
static const double BIAS = 4096.0 * 1.0 / 471.0;   /* 470 k ao 3,3 V depois de 1 k: ~8,7 contagens */
static double scan_t = 0;                            /* s */
static uint64_t nscan = 0;
static uint16_t *dma_buf; static uint32_t dma_n; static int running = 0;
static int gate_on = 0; static double conduct = 0;   /* 0 aberto .. 1 curto (com RC) */
static double G = 1000.0;
static FILE *out;
static int noise_on = 1;

static double randn(void)
{
  double u = (rand() + 1.0) / (RAND_MAX + 2.0), v = (rand() + 1.0) / (RAND_MAX + 2.0);
  return sqrt(-2 * log(u)) * cos(2 * M_PI * v);
}

/* corrente e tensão verdadeiras do módulo m */
static void module(int m, double t, double *i_ma, double *v_mv)
{
  double flick = 1.0 + 0.02 * sin(2 * M_PI * 120.0 * t);
  double isc = 120.0 * G / 1000.0 * FACT[m] * flick;
  double voc = 4 * 1.3 * 25.7 * log(isc / 1e-6 + 1.0);              /* ~2,4 V */
  double i_short = isc, v_short = isc * 2.02;                        /* shunt + Rds(on) */
  *i_ma = conduct * i_short;
  *v_mv = conduct * v_short + (1 - conduct) * voc;
}

static void one_scan(uint16_t *p)
{
  double t = scan_t;
  double mains = noise_on ? 5.0 * sin(2 * M_PI * 60.0 * t + 0.3) : 0.0;
  conduct += ((gate_on ? 1.0 : 0.0) - conduct) * (1 - exp(-(1.0 / 6000.0) / 0.0005));
  for (int m = 0; m < 4; m++)
  {
    double i, v;
    module(m, t, &i, &v);
    double a = i * 2.0 / K + BIAS + OFF[m] + mains + (noise_on ? 1.5 * randn() : 0);
    double b = v / K + OFF[4 + m] + mains + (noise_on ? 1.5 * randn() : 0);
    p[m] = (uint16_t)(a < 0 ? 0 : a > 4095 ? 4095 : lround(a));
    p[4 + m] = (uint16_t)(b < 0 ? 0 : b > 4095 ? 4095 : lround(b));
  }
  double r = 1200.0 / K + (noise_on ? 1.0 * randn() : 0);
  p[8] = (uint16_t)lround(r);
}

static void advance_scans(int k)
{
  for (int s = 0; s < k; s++)
  {
    if (!running) { scan_t += 1.0 / 6000.0; nscan++; continue; }
    uint32_t idx = (uint32_t)(nscan % (dma_n / 9));
    one_scan(&dma_buf[idx * 9]);
    nscan++;
    scan_t = nscan / 6000.0;
    if (idx + 1 == dma_n / 9 / 2) HAL_ADC_ConvHalfCpltCallback(&hadc1);
    if (idx + 1 == dma_n / 9) HAL_ADC_ConvCpltCallback(&hadc1);
  }
}

uint32_t HAL_GetTick(void) { advance_scans(1); return (uint32_t)(scan_t * 1000); }
void HAL_GPIO_WritePin(int port, uint16_t pins, GPIO_PinState s) { if (port == GATE_GPIO_Port && pins == GATE_ALL_Pins) gate_on = (s == GPIO_PIN_SET); }
HAL_StatusTypeDef HAL_ADCEx_Calibration_Start(ADC_HandleTypeDef *h) { (void)h; return HAL_OK; }
HAL_StatusTypeDef HAL_ADC_Start_DMA(ADC_HandleTypeDef *h, uint32_t *b, uint32_t n) { (void)h; dma_buf = (uint16_t *)b; dma_n = n; return HAL_OK; }
HAL_StatusTypeDef HAL_TIM_Base_Start(TIM_HandleTypeDef *h) { (void)h; running = 1; return HAL_OK; }
void Error_Handler(void) { fprintf(stderr, "Error_Handler\n"); exit(1); }
uint8_t CDC_Transmit_FS(uint8_t *b, uint16_t n) { fwrite(b, 1, n, out); cdc.TxState = 0; return USBD_OK; }

static void cmd(const char *s) { QPV_OnUsbRx((const uint8_t *)s, strlen(s)); }

int main(int argc, char **argv)
{
  out = fopen(argc > 1 ? argv[1] : "saida.txt", "wb");
  if (argc > 2 && strcmp(argv[2], "sem_ruido") == 0) noise_on = 0;
  srand(1);
  QPV_Init();
  int step = 0;
  while (scan_t < 140.0)
  {
    QPV_Loop();
    advance_scans(1);
    if (step == 0 && scan_t > 3.5) { cmd("?\n"); cmd("T 30\n"); step = 1; }
    if (step == 1 && scan_t > 50) { G = 600; step = 2; }                 /* nuvem */
    if (step == 2 && scan_t > 70) { cmd("V\n"); step = 3; }
    if (step == 3 && scan_t > 80) { cmd("M O\n"); step = 4; }
    if (step == 4 && scan_t > 90) { cmd("M C\n"); step = 5; }
    if (step == 5 && scan_t > 100) { G = 1000; step = 6; }
  }
  fclose(out);
  return 0;
}
