# 1. Materiais

## Hardware

| Item | Qtd. | Observação |
|---|---|---|
| Blue Pill STM32F103C8T6 | 1 | Cristal de 8 MHz |
| MOSFET IRLZ44N (TO-220) | 4 | Chave de cada módulo |
| Resistor 2 Ω, 1 %, 0,5 W, filme metálico (shunt) | 4 | Meça cada um com o multímetro e informe na interface |
| Resistor 100 Ω (gate) | 4 | |
| Resistor 100 kΩ (pull-down do gate) | 4 | Mantém o MOSFET desligado sem alimentação |
| Resistor 1 kΩ (filtro do canal de corrente) | 4 | |
| Resistor 470 kΩ (deslocamento do zero) | 4 | Do 3V3 à entrada de corrente |
| Resistor 100 Ω (filtro do canal de tensão) | 4 | |
| Capacitor 470 nF cerâmico X7R (filtro de corrente) | 4 | |
| Capacitor 4,7 µF cerâmico X7R ou tântalo (filtro de tensão) | 4 | |
| Capacitores 10 µF e 100 nF (desacoplamento do 3V3) | 1 de cada | Junto aos pinos 3V3/GND da Blue Pill |
| Placa perfurada, bornes de 2 vias, barra de pinos fêmea | | Montagem |
| Cabo par trançado | 4 pares | Dos módulos até a placa |
| 4 módulos fotovoltaicos idênticos | 4 | Voc abaixo de 3,3 V |
| Gravador ST-Link V2 *(opcional)* | 1 | Ou o bootloader serial com um adaptador USB-serial |

A lista de compras, com quantidades e colunas para preço, está na planilha "QuadPV - lista de compras" (pasta Claude/QuadPV no Google Drive).

## Software

| Programa | Para quê |
|---|---|
| **STM32CubeIDE** (ou STM32CubeCLT + `make`) e **STM32CubeProgrammer** | Compilar e gravar o firmware |
| Python 3.10 ou superior (Miniconda serve) | Rodar a interface |
| `numpy`, `pyserial`, `pyqtgraph`, `PySide6` | Dependências da interface (`pip install -r requirements.txt`) |
