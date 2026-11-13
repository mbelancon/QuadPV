# CONTEXT — QuadPV

File for Claude (and for whoever works on the code). Short and up to date; the README is for users.

## Goal
Compare cover materials (new glass, reused glass, no cover…) on 4 small, identical photovoltaic
modules under the same sun. The main quantity is the Isc ratio between modules; Voc (once a minute)
indicates temperature differences (ΔT estimate).

## Hardware
- Blue Pill STM32F103C8T6, USB CDC (VID 0x0483 / PID 0x5740, the same as the SOI project)
- Per module: 2 Ω 1 % shunt at the source of an IRLZ44N MOSFET used as a switch (gates PB6–PB9);
  shunt voltage on PA1–PA4, module voltage on PA5–PA7/PB0; PA0 and PB1 free
- Current input: 1 kΩ + 470 nF filter, and 470 kΩ from 3V3 keeps the zero at ~9 counts (above 0 V,
  so the ADC does not clip the noise). Voltage input: 100 Ω + 4.7 µF
- Star ground at the shunts, Kelvin connection, twisted pair to the modules
- All signals within 0–3.3 V (module Voc must stay below 3.3 V)

## Firmware (firmware/quadpv_cube, version 3.0.0)
- STM32Cube HAL + USB CDC, Makefile (arm-none-eabi-gcc); `QuadPV.ioc` written by hand, not yet
  opened in CubeMX. QuadPV logic in `Core/Src/quadpv.c`; CubeMX-style init in `main.c`
- TIM3 triggers the ADC at 6000 Hz: 1 s average = 6000 scans = 60 cycles of 60 Hz; Vrefint in every scan
- Voc window every T s (default 60): MOSFETs open, 21 ms settle, 400 scans; the same window gives
  the shunt zero. That second has no D line
- Protocol: `D n m a1..a4 v1..v4 r s1..s9` (1 s), `V …` (Voc window), `# QuadPV fw …` (info);
  commands `?`, `V`, `T s`, `M C|O`, queued
- `firmware/teste_host/`: the same quadpv.c compiled for the PC against a model (60 Hz, flicker, noise)

## Interface (interface/, version 3.0.1)
- `quadpv_core.py`: protocol, conversion, calibration, ΔT estimate, SerialLink (thread), Simulator;
  no GUI, testable on its own. `quadpv.py`: PySide6 + pyqtgraph window
- Board detection as in the SOI: candidate ports (QUADPV_PORT, "QuadPV" name, VID 0x0483…), then
  `?` to confirm; other boards are skipped for 30 s; firmware 2.x/original = "incompatible"
- Relative Isc (simultaneous sampling cancels clouds), moving average 1 s–15 min with standard
  error, "Calibrate modules…" (Isc ratio and Voc offset under identical conditions), estimated ΔT
- Writes `Measurements/<sample>_QuadPV_<date>.csv` (per second) and `…_Voc.csv`; preferences in
  QSettings (`HKCU\Software\LCTM\QuadPV` on Windows)
- Launchers `QuadPV.bat` / `quadpv.sh` look for QUADPV_PYTHON, `.venv`, Miniconda/Anaconda/Miniforge,
  the official Python and the PATH; offer `pip install -r requirements.txt`

## Structure
- `firmware/quadpv_cube/` — STM32Cube firmware; `firmware/teste_host/` — PC test bench
- `firmware/pv/pv.ino`, `legacy/pv-stm32.py` — original 2020 versions, reference only
- `interface/` — Python; `QuadPV.bat` / `quadpv.sh` at the root
- `docs/` — 01_Materials … 05_Calibration_and_Uncertainty, images in `docs/images/`
- Branch `carga-eletronica` — v2.0.0 with electronic load (op-amp + MOSFET, I–V curves, MPPT)

## Conventions
- Interface in the SOI style: finds the STM32 on the serial port by itself, simulation mode
- Language: documentation, interface texts and Python comments in English; firmware comments,
  firmware `#` messages and CSV column names in Portuguese
- GPL-3.0 license; CHANGELOG + semver (`__version__` in quadpv_core.py, `QPV_FW_VERSION` in quadpv.h)
- No portable version for now (uses the local Python; Miniconda in C:\Users\mbelancon\miniconda3)
- Project copies also go to Google Drive, folder Claude/QuadPV (zip per version + the
  "QuadPV - lista de compras" spreadsheet)

## Decisions
- Name QuadPV (formerly SolarPower); GitHub repository renamed to mbelancon/QuadPV
- Isc measurement with the MOSFET as a switch instead of an electronic load (less noise, simpler);
  no Hall sensors. For cover comparison, Isc is the right optical quantity
- IRLZ44N (through-hole, max threshold 2 V) works with 3.3 V on the gate because the shunt is at the
  source, so Rds(on) is not part of the current measurement
- PWM-to-analog modules (CPT10) and DACs (MCP4725/MCP4728) were considered and dropped
- Calibration between modules through the "Calibrate modules…" button

## Status and pending items
- Nothing has run on real hardware yet: board to be built, firmware to be flashed, port detection
  and the Windows launcher (`QuadPV.bat`) to be tested on the user's PC
- Open `QuadPV.ioc` in CubeMX and fix it if needed
- No automated tests in the repository; the checks so far were done with the PC test bench and the
  interface simulation
- Possible next steps: absolute module temperature (NTC/thermocouple on PA0/PB1), irradiance sensor
