/**
  ******************************************************************************
  * @file    quadpv.h
  * @brief   QuadPV: Isc e Voc de 4 módulos (shunt + MOSFET como chave), com redução de ruído
  ******************************************************************************
  */
#ifndef QUADPV_H
#define QUADPV_H

#include <stdint.h>

#define QPV_FW_VERSION   "3.0.0"

void QPV_Init(void);
void QPV_Loop(void);
void QPV_OnUsbRx(const uint8_t *buf, uint32_t len);   /* chamado pela interrupção do USB */

#endif /* QUADPV_H */
