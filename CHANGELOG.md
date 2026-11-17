# Version history

## Unreleased
- `CLAUDE.md` (working rules for Claude) and `CONTEXTO.md` (current project state, replaces
  `CONTEXT.md`), both in Portuguese; `docs/context/CONTEXT-3.0.1.md` snapshot of version 3.0.1.

## 3.0.1 – English
- Documentation, interface texts and Python code comments translated to English; documentation files
  renamed (`docs/01_Materials.md` … `docs/05_Calibration_and_Uncertainty.md`, `docs/images/`), the
  measurement folder is now `Measurements/` and `legado/` is now `legacy/`. Firmware comments and the
  CSV column names stay in Portuguese.
- `CONTEXT.md`: short project context for developers and AI assistants.
- A bad saved window geometry no longer discards the other saved preferences.

## 3.0.0 – Isc and Voc with shunt and switch, focus on noise
- Simpler hardware: each module shorted through a 2 Ω shunt by an IRLZ44N MOSFET used as a switch
  (PB6–PB9). No Hall sensors, op-amp or electronic load. RC filters on the ADC inputs and 470 kΩ to
  keep the shunt zero above 0 V.
- Firmware 3.0: ADC triggered by TIM3 at exactly 6000 Hz; averages of 6000 scans (60 cycles of
  60 Hz); Vrefint in every scan; standard deviation per channel; 400-scan Voc window that also
  measures the shunt zero; queued commands (`?`, `V`, `T`, `M C|O`).
- Interface: new protocol (`D` and `V` lines), relative Isc with standard error, moving average from
  1 s to 15 min, module calibration under identical conditions, ΔT estimated from Voc with the effect
  of light removed, noise diagnostics tab, Isc and Voc recorded in two CSV files, simulation of the
  new firmware, board identification (rejects other boards and warns about incompatible firmware).
- Launchers `QuadPV.bat` (Windows) and `quadpv.sh` (Linux/macOS): find Miniconda, Anaconda or another
  installed Python and offer to install missing libraries. No portable version for now (the
  PyInstaller packaging was removed).
- Repository renamed from SolarPower to QuadPV; GPL-3.0 license.
- The electronic-load version (I–V curves and MPPT) stays in the `carga-eletronica` branch.

## 2.0.0 – QuadPV name, electronic load, STM32Cube firmware, new interface (`carga-eletronica` branch)
- Project renamed from SolarPower to **QuadPV**.
- **Electronic load** per module (MOSFET + 1 Ω resistor + op-amp), with the current reference from
  the TIM4 PWM (PB6–PB9).
- **New STM32Cube HAL firmware** (`firmware/quadpv_cube/`): ADC in scan mode with DMA (~15 900
  averages per second), USB CDC, two-pass I–V sweep, MPPT (perturb and observe, with out-of-range
  protection and a new sweep at cloud edges), fixed/open/short modes, serial commands and
  identification (`?`). Keeps the averages line of the original firmware.
- Firmware test bench on the PC (`firmware/teste_host/`), against a physical model.
- Interface: board identification on connection (accepts the QuadPV and the original firmware,
  rejects other STM32 boards such as the SOI), load controls, I–V Curves tab, automatic zero from the
  sweeps, sweep parameters in the plot, curve recording and simulation of the new firmware.
- New PySide6 + pyqtgraph interface (`interface/quadpv.py`), in the style of the SOI Controller:
  port detection, automatic reconnection, simulation mode, live plots with adjustable window, power
  per area, accumulated energy, power and current relative to a reference module, explicit current
  zero, continuous CSV recording with raw counts, diagnostics tab, "Finish experiment" and saved
  preferences.
- Fixed compared with the original interface: incompatibility with pandas 2 (`DataFrame.append`),
  hard-coded `/dev/ttyACM0` port, offsets truncated to whole mV, automatic zero without warning,
  temperature and humidity columns always zero, data loss if the program closed without saving.
- Documentation in `docs/`. The original interface moved to `legacy/`; the firmware to `firmware/pv/`.

## 1.0 – 2020
- Arduino firmware (averages of A0–A8) and Tkinter/matplotlib interface.
