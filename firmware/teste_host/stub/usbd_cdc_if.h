#ifndef CDC_STUB_H
#define CDC_STUB_H
#include <stdint.h>
#define USBD_OK 0
#define USBD_BUSY 1
#define USBD_STATE_CONFIGURED 3
typedef struct { volatile uint32_t TxState; } USBD_CDC_HandleTypeDef;
typedef struct { uint8_t dev_state; void *pClassData; } USBD_HandleTypeDef;
uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len);
#endif
