/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : QuadPV - Blue Pill STM32F103C8 (estrutura do STM32CubeMX)
  ******************************************************************************
  */
/* USER CODE END Header */

#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stm32f1xx_hal.h"

/* USER CODE BEGIN Includes */
/* USER CODE END Includes */

void HAL_TIM_MspPostInit(TIM_HandleTypeDef *htim);
void Error_Handler(void);

/* Pinos -------------------------------------------------------------------*/
/* ADC1: PA0 = referência (sensor sem corrente); PA1..PA4 = corrente dos módulos 1..4;
 *       PA5, PA6, PA7, PB0 = tensão dos módulos 1..4 */
/* TIM4: PB6, PB7, PB8, PB9 = PWM do gate dos MOSFETs dos módulos 1..4 */
#define LED_Pin GPIO_PIN_13
#define LED_GPIO_Port GPIOC

/* USER CODE BEGIN Private defines */
/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
