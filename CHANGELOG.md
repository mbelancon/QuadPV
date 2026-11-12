# Histórico de versões

## 2.0.0 – nome QuadPV, carga eletrônica, firmware STM32Cube, interface nova
- Projeto renomeado de SolarPower para **QuadPV**.
- **Carga eletrônica** por módulo (MOSFET + resistor de 1 Ω + amplificador operacional), com a
  referência de corrente vinda do PWM do TIM4 (PB6–PB9). Esquema em `docs/02_Hardware_e_Ligacoes.md`.
- **Firmware novo em STM32Cube HAL** (`firmware/quadpv_cube/`): ADC em modo scan com DMA
  (~15 900 médias por segundo), USB CDC, varredura I–V em duas passadas, MPPT (perturba e observa,
  com proteção fora da faixa e nova varredura em bordas de nuvem), modos fixo/aberto/curto, comandos
  pela serial e identificação (`?`). Mantém a linha de médias do firmware antigo.
- Banco de testes do firmware no PC (`firmware/teste_host/`), contra um modelo físico.
- Interface: identificação da placa ao conectar (aceita o QuadPV e o firmware antigo, recusa outras
  placas STM32, como o SOI), controles da carga, aba Curvas I–V, zero automático pelas varreduras,
  parâmetros das varreduras no gráfico, gravação das curvas e simulação do firmware novo.
- Interface nova em PySide6 + pyqtgraph (`interface/quadpv.py`), no padrão do SOI Controller:
  detecção da porta, reconexão automática, modo simulação, gráficos ao vivo com janela ajustável,
  potência por área, energia acumulada, potência e corrente relativas a um módulo de referência,
  zero explícito das correntes, gravação contínua em CSV com as contagens brutas, aba de diagnóstico,
  "Finalizar experimento" e preferências salvas.
- Corrigidos em relação à interface original: incompatibilidade com o pandas 2 (`DataFrame.append`),
  porta fixa `/dev/ttyACM0`, offsets truncados para mV inteiros, zero automático sem aviso,
  colunas de temperatura e umidade sempre nulas, perda de dados se o programa fechasse sem salvar.
- Documentação em `docs/`. A interface original foi para `legado/`; o firmware foi para `firmware/pv/`.

## 1.0 – 2020
- Firmware Arduino (médias de A0–A8) e interface Tkinter/matplotlib.
