# 1. Materiais

## Hardware

| Item | Qtd. | Observação |
|---|---|---|
| Blue Pill STM32F103C8T6 | 1 | ADC de 12 bits, 10 entradas analógicas (PA0–PA7, PB0, PB1) |
| Sensor de corrente Hall WCS2702 (módulo) | 5 | 4 medem os módulos; 1 fica sem corrente e serve de referência (A0) |
| N-MOS de nível lógico IRLZ44N (TO-220) | 4 | Carga eletrônica (firmware 2.x) |
| Amplificador operacional MCP6002 (duplo, rail-to-rail) | 2 | Um para cada dois canais |
| Resistores: 1 Ω 1 % 0,5 W; 10 kΩ; 1,1 kΩ; 1 kΩ; 100 Ω; 100 kΩ | 4 de cada | Ver [02](02_Hardware_e_Ligacoes.md#componentes-por-canal) |
| Capacitores: 470 nF; 2,2 nF; 100 nF | 4; 4; 2 + 4 | Filtro da referência, compensação, desacoplamento |
| Resistor de carga *(montagem original)* | 4 | Só na carga fixa. Valor e potência a registrar: *(a completar)* |
| Módulos fotovoltaicos idênticos | 4 | Tensão de circuito aberto abaixo de 3,3 V (ver [02](02_Hardware_e_Ligacoes.md)) |
| Cabo USB | 1 | Alimentação e dados |
| Gravador ST-Link V2 *(opcional)* | 1 | Só se a placa não tiver bootloader USB |

*(a completar: fotos da montagem, fornecedores e custo aproximado.)*

## Software

| Programa | Para quê |
|---|---|
| **STM32CubeIDE** (ou STM32CubeCLT + `make`) e **STM32CubeProgrammer** | Compilar e gravar o firmware 2.x (`firmware/quadpv_cube/`) |
| Arduino IDE com o núcleo STM32duino *(só o firmware antigo)* | Gravar `firmware/pv/pv.ino` |
| Python 3.10 ou superior (Miniconda serve) | Rodar a interface |
| `numpy`, `pyserial`, `pyqtgraph`, `PySide6` | Dependências da interface (`pip install -r requirements.txt`) |
