# Histórico de versões

## 3.0.0 – Isc e Voc com shunt e chave, foco em ruído
- Hardware simplificado: cada módulo em curto por um shunt de 2 Ω através de um MOSFET IRLZ44N usado
  como chave (PB6–PB9). Sem sensores Hall, op-amp ou carga eletrônica. Filtros RC nas entradas do ADC
  e 470 kΩ para manter o zero dos shunts acima de 0 V.
- Firmware 3.0: ADC disparado pelo TIM3 a 6000 Hz exatos; médias de 6000 varreduras (60 ciclos de
  60 Hz); Vrefint em toda varredura; desvio-padrão por canal; janela de Voc de 400 varreduras que
  também mede o zero dos shunts; comandos em fila (`?`, `V`, `T`, `M C|O`).
- Interface: protocolo novo (linhas `D` e `V`), Isc relativa com erro padrão, média móvel de 1 s a
  15 min, calibração dos módulos em condição idêntica, ΔT estimado pela Voc com desconto do efeito da
  luz, aba de diagnóstico de ruído, gravação de Isc e Voc em dois CSV, simulação do firmware novo,
  identificação da placa (recusa outras placas e avisa firmware incompatível).
- Lançadores `QuadPV.bat` (Windows) e `quadpv.sh` (Linux/macOS): acham o Miniconda, o Anaconda ou outro
  Python já instalado e oferecem instalar as bibliotecas que faltarem. Sem versão portátil por enquanto
  (o empacotamento com PyInstaller foi retirado).
- Repositório renomeado de SolarPower para QuadPV.
- A versão com carga eletrônica (curvas I–V e MPPT) ficou no branch `carga-eletronica`.

## 2.0.0 – nome QuadPV, carga eletrônica, firmware STM32Cube, interface nova (branch `carga-eletronica`)
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
