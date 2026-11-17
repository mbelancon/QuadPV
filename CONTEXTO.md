# CONTEXTO — QuadPV

Estado vivo do projeto, curto e atualizado. Regras de trabalho ficam no `CLAUDE.md`; o README é para
usuários; retratos de cada release ficam em `docs/context/CONTEXT-<versão>.md`.

**Versão atual:** interface 3.0.1, firmware 3.0.0 (sem release nem tag no GitHub ainda).

## Objetivo
Comparar coberturas (vidro novo, vidro reusado, sem cobertura…) sobre 4 módulos fotovoltaicos
pequenos e idênticos sob o mesmo sol. A grandeza principal é a razão de Isc entre módulos (a
amostragem simultânea cancela nuvens); o Voc (1×/min) estima a diferença de temperatura (ΔT).

## Hardware
- Blue Pill STM32F103C8T6, USB CDC (VID 0x0483 / PID 0x5740, o mesmo do SOI).
- Por módulo: shunt 2 Ω 1 % na fonte de um IRLZ44N usado como chave (gates PB6–PB9); tensão do
  shunt em PA1–PA4, do módulo em PA5–PA7/PB0; PA0 e PB1 livres.
- Corrente: filtro 1 kΩ + 470 nF; 470 kΩ do 3V3 mantém o zero em ~9 contagens. Tensão: 100 Ω + 4,7 µF.
- Terra em estrela nos shunts, ligação Kelvin, par trançado. Voc do módulo < 3,3 V.

## Firmware (`firmware/quadpv_cube/`, 3.0.0)
- TIM3 dispara o ADC a 6000 Hz; média de 1 s = 6000 varreduras = 60 ciclos de 60 Hz; Vrefint em
  toda varredura.
- Janela de Voc a cada T s (padrão 60): MOSFETs abrem, descarta 21 ms, 400 varreduras; dá também o
  zero do shunt. Esse segundo não gera linha `D`.
- Protocolo: `D n m a1..a4 v1..v4 r s1..s9`, `V …`, `# QuadPV fw …`; comandos `?`, `V`, `T s`, `M C|O`.
- `QuadPV.ioc` escrito à mão, nunca aberto no CubeMX.

## Interface (`interface/`, 3.0.1)
- PySide6 + pyqtgraph. Isc relativo com erro padrão, média móvel 1 s–15 min, "Calibrate modules…",
  ΔT estimado, aba de diagnóstico de ruído, modo Simulate.
- Grava `Measurements/<amostra>_QuadPV_<data>.csv` e `…_Voc.csv`; preferências em QSettings.
- Lançadores `QuadPV.bat` / `quadpv.sh` procuram Miniconda/Anaconda/outro Python.

## Estrutura
- `firmware/quadpv_cube/` firmware; `firmware/teste_host/` banco de testes no PC
- `interface/` Python; `QuadPV.bat` / `quadpv.sh` na raiz
- `docs/` 01_Materials … 05_Calibration_and_Uncertainty; `docs/context/` retratos por release
- `firmware/pv/pv.ino`, `legacy/pv-stm32.py` versões de 2020, só referência
- Branch `carga-eletronica`: v2.0.0 com carga eletrônica, curvas I–V e MPPT

## Decisões (não reabrir sem motivo)
- Nome QuadPV (antes SolarPower).
- Isc com MOSFET como chave no lugar de carga eletrônica (menos ruído, mais simples); sem Hall.
- IRLZ44N aceita 3,3 V no gate porque o shunt está na fonte (Rds(on) fora da medida).
- CPT10 (PWM→analógico) e DACs MCP4725/MCP4728 avaliados e descartados.
- Sem versão portátil por enquanto: usa o Python local.
- Cópias também no Google Drive, pasta Claude/QuadPV (zip por versão + planilha
  "QuadPV - lista de compras").

## Pendências
- Nada rodou em hardware real: montar a placa, gravar o firmware, testar detecção de porta e
  `QuadPV.bat` no PC do Marcos.
- Abrir `QuadPV.ioc` no CubeMX e corrigir se preciso.
- Sem testes automatizados nem CI.
- Nenhum release/tag no GitHub.
- O `Simulator` se anuncia como `fw 3.0.1` (usa a versão da interface); o firmware real diz `fw 3.0.0`.
- Próximos passos possíveis: temperatura absoluta (NTC/termopar em PA0/PB1), sensor de irradiância.
