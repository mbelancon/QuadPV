# 3. Firmware

Folder: [`firmware/quadpv_cube/`](../firmware/quadpv_cube/), STM32Cube HAL. Uses ~27 KB of flash and ~8 KB of RAM. The source comments are in Portuguese.

## Layout

```
firmware/quadpv_cube/
├── QuadPV.ioc                      STM32CubeMX configuration (pins, clocks, peripherals)
├── Core/Src/main.c                 Initialization in CubeMX format (calls QPV_Init/QPV_Loop)
├── Core/Src/quadpv.c               QuadPV logic: averages, Voc window, protocol, commands
├── Core/Src/stm32f1xx_hal_msp.c    ADC, DMA and TIM3 pins
├── USB_DEVICE/                     Virtual serial port (CDC)
├── Drivers/, Middlewares/          ST HAL, CMSIS and USB library (copies, with their licenses)
├── STM32F103C8Tx_FLASH.ld          Memory map
└── Makefile                        Build with arm-none-eabi-gcc
```

## Building and flashing

**With `make`** (arm-none-eabi-gcc on the PATH; it comes with STM32CubeIDE or STM32CubeCLT):

```bash
cd firmware/quadpv_cube
make            # builds build/QuadPV.elf, .hex and .bin
make flash      # flashes through the ST-Link (SWD) with STM32_Programmer_CLI
```

**With STM32CubeIDE**: *File > New > STM32 Project from an Existing STM32CubeMX Configuration File (.ioc)*, choose `QuadPV.ioc` and generate the code. CubeMX keeps the `USER CODE` blocks, which hold the QuadPV calls and the gate pin definitions. Copy `Core/Src/quadpv.c` and `Core/Inc/quadpv.h` into the project if they are not included.

> `QuadPV.ioc` was written by hand and has not been opened in CubeMX yet. The project builds without it (Makefile). If CubeMX complains when opening it, check the pins against the table in [02](02_Hardware_and_Wiring.md#pins).

**Without an ST-Link:** connect a USB-serial adapter to PA9 (TX) and PA10 (RX), set the BOOT0 jumper to 1, reset and flash `build/QuadPV.bin` with STM32CubeProgrammer (UART port). Set BOOT0 back to 0.

**USB:** virtual serial port with VID `0x0483` and PID `0x5740` (the standard ST pair), no extra driver on Windows 10/11, Linux or macOS. At power-up the firmware pulls D+ low for 15 ms so the PC re-enumerates the board (a known Blue Pill issue).

## Acquisition

- **Trigger:** TIM3 generates 6000 triggers per second, derived from the crystal (72 MHz / 12 000). Each trigger makes the ADC convert 9 channels in sequence:
  - the 4 shunts and the 4 module voltages, with 71.5 sampling cycles each;
  - Vrefint, with 239.5 cycles (ST asks for ≥ 17.1 µs).

  One scan takes 77 µs.
- **DMA:** the DMA writes into a circular buffer. Every 16 scans (~2.7 ms) the interrupt adds up each channel and its square (for the standard deviation).
- **1 s averages:** exactly 6000 scans (375 blocks) make a `D` line. Since 6000 scans = 60 cycles of 60 Hz, mains interference and its harmonics (including the 120 Hz flicker of lamps) cancel in the average.
- **Voc window** (every T seconds, 60 by default, or on the `V` command), at the start of a second:
  1. the 4 MOSFETs open together;
  2. 8 blocks (21 ms) are discarded while the voltage settles;
  3. 25 blocks (400 scans = 66.7 ms = 4 cycles of 60 Hz) make the `V` line;
  4. the MOSFETs close, and the rest of the second is discarded.

  That second produces no `D` line. In the `V` line, the current channels give the shunt zero (no current) and the voltage channels give Voc.
- **Mode change:** the current second is discarded, plus 21 ms, so shorted and open readings are not mixed.
- **First Voc window:** 2 s after power-up, so the interface gets the zero early.

## Serial protocol

ASCII text, one message per line (`\r\n`).

**From the board to the PC**

```
D n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 s2 s3 s4 s5 s6 s7 s8 s9
V n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 s2 s3 s4 s5 s6 s7 s8 s9
# QuadPV fw 3.0.0 tipo=Isc-Voc modo=C T=60 fs=6000 perdidos=0
```

| Field | Meaning |
|---|---|
| `D` / `V` | 1 s average (modules in the current mode) / Voc window (modules open) |
| `n` | Number of averaged scans: 6000 in `D`, 400 in `V` |
| `m` | Mode: `C` (shorted, measures Isc) or `O` (open) |
| `a1`…`a4` | Shunt voltages, in counts, 3 decimals |
| `v1`…`v4` | Module voltages, in counts, 3 decimals |
| `r` | Vrefint, in counts, 3 decimals |
| `s1`…`s9` | Standard deviation of a single sample of each channel, same order, in counts, 2 decimals (the error of the mean is s/√n) |
| `perdidos` | Seconds the main loop could not send ("lost"; should stay at 0) |

The keys in the `#` line (`tipo`, `modo`, `perdidos`) are in Portuguese, as in the firmware.

**From the PC to the board**

| Command | Effect |
|---|---|
| `?` | Identification and state |
| `V` | Measures Voc in the next second |
| `T s` | Voc every *s* seconds (0 = only on the `V` command; default 60) |
| `M C` | Shorted: measures Isc continuously (default) |
| `M O` | Open: measures Voc continuously (`D` lines with `m = O`) |

Commands are queued, so several in a row are not lost. Any serial terminal can be used to test them.

## Testing without the board

[`firmware/teste_host/`](../firmware/teste_host/) builds the same `quadpv.c` for the PC, against a model:

- 4 modules with different transmittances;
- shunt + MOSFET;
- 12-bit ADC at 6 kHz with 1.5-count white noise and a per-channel offset;
- 5 counts of 60 Hz on every channel and 2 % of 120 Hz flicker in the light.

```bash
cd firmware/teste_host
gcc -O2 -Wall -Istub -I../quadpv_cube/Core/Inc -o sim sim.c ../quadpv_cube/Core/Src/quadpv.c -lm
./sim saida.txt              # with noise and interference
./sim limpo.txt sem_ruido    # without noise or 60 Hz, for comparison
```

Results with the current version:
- the 1 s averages with and without noise differ by less than 0.05 count (0.02 mA), i.e. the 60 Hz interference cancels;
- the zero measured in the Voc windows matches the true value within ±0.05 count.
