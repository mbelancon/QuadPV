/* Stubs do HAL para rodar quadpv.c no PC (teste_host). */
#ifndef MAIN_STUB_H
#define MAIN_STUB_H
#include <stdint.h>
#include <stddef.h>
typedef enum { HAL_OK = 0, HAL_ERROR, HAL_BUSY } HAL_StatusTypeDef;
typedef struct { int Instance; } ADC_HandleTypeDef;
typedef struct { int Instance; } TIM_HandleTypeDef;
#define ADC1 1
#define TIM_CHANNEL_1 0
#define TIM_CHANNEL_2 4
#define TIM_CHANNEL_3 8
#define TIM_CHANNEL_4 12
#define LED_GPIO_Port 0
#define LED_Pin 0
uint32_t HAL_GetTick(void);
void sim_set_ccr(int ch, uint32_t v);
#define __HAL_TIM_SET_COMPARE(h, ch, v) sim_set_ccr((ch) / 4, (v))
#define __disable_irq()
#define __enable_irq()
#define HAL_GPIO_TogglePin(p, n)
HAL_StatusTypeDef HAL_TIM_PWM_Start(TIM_HandleTypeDef *h, uint32_t ch);
HAL_StatusTypeDef HAL_ADCEx_Calibration_Start(ADC_HandleTypeDef *h);
HAL_StatusTypeDef HAL_ADC_Start_DMA(ADC_HandleTypeDef *h, uint32_t *buf, uint32_t n);
void Error_Handler(void);
#endif
