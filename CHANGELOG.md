# Histórico de versões

## 2.0.0 – nome QuadPV, interface nova
- Projeto renomeado de SolarPower para **QuadPV**.
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
