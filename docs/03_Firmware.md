# 3. Firmware

Arquivo: [`firmware/pv/pv.ino`](../firmware/pv/pv.ino) (Arduino IDE, núcleo STM32duino).

## O que faz

1. Configura o ADC para 12 bits (`analogReadResolution(12)`) e a serial USB.
2. Lê A0–A8 em sequência, **192 vezes, com 5 ms entre as rodadas** (≈ 0,96 s por ponto, mais o tempo de conversão).
3. Envia as médias, com uma casa decimal, numa linha de texto:

```
A0 A1 A5 A2 A6 A3 A7 A4 A8\r\n
```

ou seja: referência, depois (corrente, tensão) de cada módulo. Exemplo:

```
2048.3 2119.8 1057.9 2105.3 1026.0 2121.2 983.4 2111.0 1079.0
```

A interface aceita os valores separados por espaço, vírgula ou ponto e vírgula e ignora linhas com outro número de campos.

## Gravação

1. Instale o **STM32CubeProgrammer** e depois a Arduino IDE.
2. Na Arduino IDE, instale o núcleo **STM32 MCU based boards** (STM32duino).
3. Placa: *Generic STM32F1 series*, *BluePill F103C8*; *USB support: CDC (generic Serial supersede U(S)ART)*; método de upload conforme o seu gravador (ST-Link ou bootloader).
4. Abra `firmware/pv/pv.ino` e grave.

*(a completar: opções exatas usadas na Arduino IDE.)*

## Limitações conhecidas

- A cadência depende de `delay()` e do tempo de conversão; o PC carimba o horário na chegada de cada linha.
- Não há identificação de versão nem verificação de integridade nas linhas.
- A conversão para mV e mA é feita na interface, não no firmware.

Está planejada uma versão em **STM32Cube** (ADC em modo scan com DMA e temporizador para a cadência), definida junto com a decisão sobre a carga eletrônica (MOSFET).
