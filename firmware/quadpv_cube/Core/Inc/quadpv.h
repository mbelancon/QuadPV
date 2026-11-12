/**
  ******************************************************************************
  * @file    quadpv.h
  * @brief   QuadPV: aquisição, carga eletrônica (MOSFET), varredura I-V e MPPT
  ******************************************************************************
  */
#ifndef QUADPV_H
#define QUADPV_H

#include <stdint.h>

#define QPV_FW_VERSION   "2.0.0"

void QPV_Init(void);
void QPV_Loop(void);
void QPV_OnUsbRx(const uint8_t *buf, uint32_t len);   /* chamado pela interrupção do USB */

#endif /* QUADPV_H */
