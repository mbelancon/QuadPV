# CLAUDE.md — QuadPV

Regras estáveis para o Claude neste repositório. O estado do projeto (o que existe, o que falta,
decisões) fica em `CONTEXTO.md`, importado abaixo; não repetir aqui.

@CONTEXTO.md

## Antes de começar
- Ler o `CONTEXTO.md` (já importado acima) e, se a tarefa envolver uma versão publicada, o
  `docs/context/CONTEXT-<versão>.md` mais recente.
- Detalhes técnicos estão em `docs/01_…` a `docs/05_…`; ler só o que a tarefa pedir.

## Prioridades de projeto
- **Baixo ruído acima de tudo.** Qualquer mudança no hardware, na aquisição ou no processamento
  deve dizer o efeito esperado no ruído do Isc relativo. Não trocar ruído por conveniência.
- Hardware analógico simples: sem op-amp, sem sensor Hall, sem carga eletrônica (ver Decisões no
  `CONTEXTO.md`). Sinais sempre em 0–3,3 V.

## Firmware
- **STM32Cube HAL** (nunca stm32duino/Arduino) na Blue Pill STM32F103C8T6.
- Código do QuadPV em `Core/Src/quadpv.c` / `Core/Inc/quadpv.h`. Em `main.c` e nos arquivos gerados,
  escrever só dentro dos blocos `/* USER CODE BEGIN … END */`, para o CubeMX poder regenerar.
- Não editar `Drivers/` e `Middlewares/` (bibliotecas ST/Arm).
- Compilar: `cd firmware/quadpv_cube && make` (arm-none-eabi-gcc). Gravar: `make flash`.
- Testar no PC depois de mexer em `quadpv.c`:
  ```bash
  cd firmware/teste_host
  gcc -O2 -Wall -Istub -I../quadpv_cube/Core/Inc -o sim sim.c ../quadpv_cube/Core/Src/quadpv.c -lm
  ./sim saida.txt && ./sim limpo.txt sem_ruido
  ```
  Deve compilar sem avisos.

## Interface
- Estilo do SOI: acha a placa na serial sozinha (confirma com `?`), ignora outras placas, tem modo
  simulado. Manter isso em qualquer mudança.
- Lógica sem GUI em `interface/quadpv_core.py` (testável sozinha); janela em `interface/quadpv.py`.
- Testar sem placa com **Simulate**. Python ≥ 3.10; no PC do Marcos, Miniconda em
  `C:\Users\mbelancon\miniconda3`.

## Convenções
- Idioma: docs, textos da interface e comentários Python em inglês; comentários e mensagens `#` do
  firmware e nomes de colunas do CSV em português. `CLAUDE.md` e `CONTEXTO.md` em português.
- Mudança de protocolo serial exige mudar firmware, `quadpv_core.py` (parser e `Simulator`) e
  `docs/03_Firmware.md` juntos.
- Semver + `CHANGELOG.md`. Versões em `QPV_FW_VERSION` (`quadpv.h`) e `__version__`
  (`quadpv_core.py`).
- Licença GPL-3.0.

## Ao terminar
- Se algo mudou (código, hardware, decisão, pendência), propor ao Marcos a alteração no
  `CONTEXTO.md` como **diff curto**, sem aplicar sem ele ver.
- A cada **release publicado**, criar `docs/context/CONTEXT-<versão>.md` (retrato congelado daquela
  versão, em português) e copiar para a pasta Claude/QuadPV do Google Drive. Esses arquivos não são
  editados depois.
