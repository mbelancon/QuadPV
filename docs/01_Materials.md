# 1. Materials

## Hardware

| Item | Qty | Notes |
|---|---|---|
| Blue Pill STM32F103C8T6 | 1 | 8 MHz crystal |
| IRLZ44N MOSFET (TO-220) | 4 | Switch for each module |
| 2 Ω, 1 %, 0.5 W metal-film resistor (shunt) | 4 | Measure each one with a multimeter and enter the value in the interface |
| 100 Ω resistor (gate) | 4 | |
| 100 kΩ resistor (gate pull-down) | 4 | Keeps the MOSFET off when unpowered |
| 1 kΩ resistor (current-channel filter) | 4 | |
| 470 kΩ resistor (zero shift) | 4 | From 3V3 to the current input |
| 100 Ω resistor (voltage-channel filter) | 4 | |
| 470 nF X7R ceramic capacitor (current filter) | 4 | |
| 4.7 µF X7R ceramic or tantalum capacitor (voltage filter) | 4 | |
| 10 µF and 100 nF capacitors (3V3 decoupling) | 1 each | Next to the 3V3/GND pins of the Blue Pill |
| Perfboard, 2-way terminal blocks, female pin headers | | Assembly |
| Twisted-pair cable | 4 pairs | From the modules to the board |
| 4 identical photovoltaic modules | 4 | Voc below 3.3 V |
| ST-Link V2 programmer *(optional)* | 1 | Or the serial bootloader with a USB-serial adapter |

The shopping list, with quantities and price columns, is in the "QuadPV - lista de compras" spreadsheet (Claude/QuadPV folder on Google Drive).

## Software

| Program | Used for |
|---|---|
| **STM32CubeIDE** (or STM32CubeCLT + `make`) and **STM32CubeProgrammer** | Building and flashing the firmware |
| Python 3.10 or newer (Miniconda works) | Running the interface |
| `numpy`, `pyserial`, `pyqtgraph`, `PySide6` | Interface dependencies (`pip install -r requirements.txt`, or let `QuadPV.bat` install them) |
