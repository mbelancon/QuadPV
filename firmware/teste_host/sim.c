/* Modelo físico para testar quadpv.c sem a placa:
 * módulo (diodo único, 4 células, Voc 2,4 V, Isc 120 mA * G) em série com o sensor Hall e a
 * carga. Carga = "aberta": MOSFET com o gate direto do PWM+RC (lei quadrática, Vth 1,0 V,
 * K 2 A/V^2); "opamp": fonte de corrente com op-amp, I = PWM/1023 * 3300 mV / 10 / 1 ohm,
 * limitada pela queda em Rs + Rds(on) = 1,05 ohm. RC da referência com tau 0,47 ms.
 * O ADC de 12 bits amostra 9 canais com ruído; a cada 1 ms de tempo simulado o "DMA" entrega
 * meia buffer (16 varreduras) ao callback do firmware. */
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include "main.h"
#include "usbd_cdc_if.h"
#include "quadpv.h"

void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *hadc);
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc);

ADC_HandleTypeDef hadc1 = {ADC1};
TIM_HandleTypeDef htim4;
static USBD_CDC_HandleTypeDef cdc;
USBD_HandleTypeDef hUsbDeviceFS = {USBD_STATE_CONFIGURED, &cdc};

static double t_us = 0, next_dma_us = 1000;
static uint16_t *dma_buf; static uint32_t dma_n; static int half = 0;
static double ccr[4], vg[4];
static double G[4] = {1000, 1000, 1000, 1000};
static const double FACT[4] = {1.00, 0.97, 0.93, 1.02};
static const double OFF_MV[4] = {6, -4, 11, -2};
static const double ISC = 120.0, VOC = 2400.0, NVT = 4 * 1.3 * 25.7, VTH = 1.0, K = 2000.0; /* mA/V^2 */
static double i_true[4], v_true[4];
static FILE *out;
static int trace = 0;

static double randn(void)
{
  double u = (rand() + 1.0) / (RAND_MAX + 2.0), v = (rand() + 1.0) / (RAND_MAX + 2.0);
  return sqrt(-2 * log(u)) * cos(2 * M_PI * v);
}

static int OPAMP = 1;
static double i_mos(double vds_mv, double vgs)
{
  if (OPAMP)
  {
    double iset = vgs / 3.3 * 330.0;               /* mA: referência 0..330 mV sobre 1 ohm */
    double ilin = vds_mv / 1.05;                   /* sem folga: MOSFET saturado, Rs + Rds(on) */
    return iset < ilin ? iset : ilin;
  }

  double vov = vgs - VTH, vds = vds_mv / 1000.0;
  if (vov <= 0) return 0;
  if (vds < vov) return K * (2 * vov * vds - vds * vds);
  return K * vov * vov;
}

static void operating_point(int m)
{
  double isc = ISC * G[m] / 1000.0 * FACT[m];
  double i0 = ISC / exp(VOC / NVT);
  double lo = 0, hi = NVT * log(isc / i0 + 1.0);
  if (isc <= 0) { v_true[m] = 0; i_true[m] = 0; return; }
  for (int k = 0; k < 60; k++)
  {
    double v = 0.5 * (lo + hi);
    double ipv = isc - i0 * (exp(v / NVT) - 1);
    if (ipv > i_mos(v, vg[m])) lo = v; else hi = v;
  }
  v_true[m] = 0.5 * (lo + hi);
  i_true[m] = isc - i0 * (exp(v_true[m] / NVT) - 1);
}

static void dma_tick(void)
{
  const double k = 3300.0 / 4096.0;
  uint16_t *p = dma_buf + (half ? dma_n / 2 : 0);
  for (int m = 0; m < 4; m++) operating_point(m);
  for (uint32_t s = 0; s < dma_n / 2 / 9; s++, p += 9)
  {
    double ref = 1650.0;
    double ch[9];
    ch[0] = ref / k;
    for (int m = 0; m < 4; m++)
    {
      ch[1 + m] = (ref + OFF_MV[m] + 0.9 * i_true[m]) / k;
      ch[5 + m] = v_true[m] / k;
    }
    for (int c = 0; c < 9; c++)
    {
      double v = ch[c] + 1.5 * randn();
      p[c] = (uint16_t)(v < 0 ? 0 : v > 4095 ? 4095 : lround(v));
    }
  }
  if (half) HAL_ADC_ConvCpltCallback(&hadc1); else HAL_ADC_ConvHalfCpltCallback(&hadc1);
  half = !half;
}

uint32_t HAL_GetTick(void)
{
  t_us += 5;                                       /* cada leitura do relógio custa 5 us */
  for (int m = 0; m < 4; m++)                      /* RC do gate */
    vg[m] += (ccr[m] / 1023.0 * 3.3 - vg[m]) * (1 - exp(-5 / 470.0));
  if (dma_buf && t_us >= next_dma_us) { next_dma_us += 1000; dma_tick(); }
  cdc.TxState = 0;
  return (uint32_t)(t_us / 1000);
}

void sim_set_ccr(int ch, uint32_t v) { ccr[ch] = v; }
HAL_StatusTypeDef HAL_TIM_PWM_Start(TIM_HandleTypeDef *h, uint32_t ch) { (void)h; (void)ch; return HAL_OK; }
HAL_StatusTypeDef HAL_ADCEx_Calibration_Start(ADC_HandleTypeDef *h) { (void)h; return HAL_OK; }
HAL_StatusTypeDef HAL_ADC_Start_DMA(ADC_HandleTypeDef *h, uint32_t *b, uint32_t n) { (void)h; dma_buf = (uint16_t *)b; dma_n = n; return HAL_OK; }
void Error_Handler(void) { fprintf(stderr, "Error_Handler\n"); exit(1); }
uint8_t CDC_Transmit_FS(uint8_t *b, uint16_t n) { fwrite(b, 1, n, out); return USBD_OK; }

static void cmd(const char *s) { QPV_OnUsbRx((const uint8_t *)s, strlen(s)); }

int main(int argc, char **argv)
{
  out = fopen(argc > 1 ? argv[1] : "saida.txt", "wb");
  OPAMP = !(argc > 2 && strcmp(argv[2], "aberta") == 0);
  srand(1);
  QPV_Init();
  double end_s = 75;
  int sent_q = 0, sent_t = 0, cloud = 0;
  while (t_us < end_s * 1e6)
  {
    QPV_Loop();
    double ts = t_us / 1e6;
    if (!sent_q && ts > 1.5) { cmd("?\n"); sent_q = 1; }
    if (!sent_t && ts > 2.0) { cmd("T 20\n"); sent_t = 1; }
    if (cloud == 0 && ts > 30) { for (int m = 0; m < 4; m++) G[m] = 500; cloud = 1; }  /* nuvem aos 30 s */
    if (cloud == 1 && ts > 50) { for (int m = 0; m < 4; m++) G[m] = 900; cloud = 2; }  /* sol volta aos 50 s */
    if (cloud == 2 && ts > 52) { trace = 1; }
  }
  /* MPP verdadeiro (varrendo o gate no modelo) para comparar com o MPPT */
  for (int m = 0; m < 4; m++)
  {
    double best = 0, bestg = 0, keep = vg[m];
    for (double g = 0.0; g < 3.3; g += 0.0002)
    {
      vg[m] = g; operating_point(m);
      if (v_true[m] * i_true[m] > best) { best = v_true[m] * i_true[m]; bestg = g; }
    }
    vg[m] = keep; operating_point(m);
    fprintf(stderr, "modulo %d: Pmax real %.2f mW; P no fim do MPPT %.2f mW (%.1f %%)\n",
            m + 1, best / 1000, v_true[m] * i_true[m] / 1000, 100 * v_true[m] * i_true[m] / best);
    (void)bestg;
  }
  fclose(out);
  return 0;
}
