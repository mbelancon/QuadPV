# 1. Materiais

## Hardware

| Item | Qtd. | Observação |
|---|---|---|
| Blue Pill STM32F103C8T6 | 1 | ADC de 12 bits, 10 entradas analógicas (PA0–PA7, PB0, PB1) |
| Sensor de corrente Hall WCS2702 (módulo) | 5 | 4 medem os módulos; 1 fica sem corrente e serve de referência (A0) |
| Resistor de carga | 4 | Um por módulo. Valor e potência a registrar: *(a completar)* |
| Módulos fotovoltaicos idênticos | 4 | Tensão de circuito aberto abaixo de 3,3 V (ver [02](02_Hardware_e_Ligacoes.md)) |
| Cabo USB | 1 | Alimentação e dados |
| Gravador ST-Link V2 *(opcional)* | 1 | Só se a placa não tiver bootloader USB |

*(a completar: fotos da montagem, fornecedores e custo aproximado.)*

## Software

| Programa | Para quê |
|---|---|
| Arduino IDE com o núcleo **STM32duino** e o **STM32CubeProgrammer** | Gravar o firmware atual (`firmware/pv/pv.ino`) |
| Python 3.10 ou superior (Miniconda serve) | Rodar a interface |
| `numpy`, `pyserial`, `pyqtgraph`, `PySide6` | Dependências da interface (`pip install -r requirements.txt`) |
