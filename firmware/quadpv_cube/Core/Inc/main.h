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

void Error_Handler(void);

/* Pinos -------------------------------------------------------------------*/
/* ADC1: PA1..PA4 = tensão nos shunts (corrente dos módulos 1..4);
 *       PA5, PA6, PA7, PB0 = tensão dos módulos 1..4; canal interno 17 = Vrefint */
/* PB6, PB7, PB8, PB9 = gates dos MOSFETs (chaves) dos módulos 1..4: 1 = curto (Isc), 0 = aberto (Voc) */
#define LED_Pin GPIO_PIN_13
#define LED_GPIO_Port GPIOC

/* USER CODE BEGIN Private defines */
/* (dentro do bloco USER CODE para sobreviver à regeneração pelo CubeMX) */
#define GATE_GPIO_Port GPIOB
#define GATE1_Pin GPIO_PIN_6
#define GATE2_Pin GPIO_PIN_7
#define GATE3_Pin GPIO_PIN_8
#define GATE4_Pin GPIO_PIN_9
#define GATE_ALL_Pins (GATE1_Pin | GATE2_Pin | GATE3_Pin | GATE4_Pin)
/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
