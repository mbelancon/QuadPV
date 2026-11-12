/* Stubs do HAL para rodar quadpv.c no PC (teste_host). */
#ifndef MAIN_STUB_H
#define MAIN_STUB_H
#include <stdint.h>
#include <stddef.h>
typedef enum { HAL_OK = 0, HAL_ERROR, HAL_BUSY } HAL_StatusTypeDef;
typedef enum { GPIO_PIN_RESET = 0, GPIO_PIN_SET } GPIO_PinState;
typedef struct { int Instance; } ADC_HandleTypeDef;
typedef struct { int Instance; } TIM_HandleTypeDef;
#define ADC1 1
#define LED_GPIO_Port 0
#define LED_Pin 0
#define GATE_GPIO_Port 1
#define GATE1_Pin 0x40
#define GATE2_Pin 0x80
#define GATE3_Pin 0x100
#define GATE4_Pin 0x200
#define GATE_ALL_Pins (GATE1_Pin | GATE2_Pin | GATE3_Pin | GATE4_Pin)
uint32_t HAL_GetTick(void);
void HAL_GPIO_WritePin(int port, uint16_t pins, GPIO_PinState s);
#define HAL_GPIO_TogglePin(p, n)
#define __disable_irq()
#define __enable_irq()
HAL_StatusTypeDef HAL_ADCEx_Calibration_Start(ADC_HandleTypeDef *h);
HAL_StatusTypeDef HAL_ADC_Start_DMA(ADC_HandleTypeDef *h, uint32_t *buf, uint32_t n);
HAL_StatusTypeDef HAL_TIM_Base_Start(TIM_HandleTypeDef *h);
void Error_Handler(void);
#endif
