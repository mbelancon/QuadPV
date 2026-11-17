# CONTEXTO — QuadPV 3.0.1

Contexto base para qualquer tarefa no projeto (para o Claude e para quem mexer no código).
Ler este arquivo antes de começar. O README é para usuários; o `CONTEXT.md` da raiz do repo é a
versão curta em inglês.

| | |
|---|---|
| Repositório | https://github.com/mbelancon/QuadPV (branch `main`) |
| Versão | **3.0.1** (interface 3.0.1, firmware 3.0.0) |
| Commit de referência | `b1fdf4d` — 13/11/2026 — "English documentation and interface; CONTEXT.md (v3.0.1)" |
| Release no GitHub | **Nenhum release nem tag publicado ainda.** Este arquivo descreve o estado atual da `main`, que corresponde à versão 3.0.1 do CHANGELOG |
| Licença | GPL-3.0 (bibliotecas ST/Arm em `Drivers/` e `Middlewares/` mantêm as licenças delas) |

## 1. Objetivo

Comparar materiais de cobertura (vidro novo, vidro reusado, sem cobertura…) sobre **4 módulos
fotovoltaicos pequenos e idênticos** expostos ao mesmo sol.

- Grandeza principal: **razão de Isc entre módulos** (proporcional à luz que atravessa a cobertura).
  A amostragem simultânea dos 4 canais cancela a variação comum (nuvens).
- **Voc** (1×/min por padrão) indica diferença de temperatura entre módulos (estimativa de ΔT,
  descontado o efeito da luz).
- Ruído baixo é o requisito central: com média de 1 min, erro padrão do Isc relativo ≈ 0,003 %
  (em simulação).

## 2. Arquitetura

```
4 módulos FV ──► shunt 2 Ω + MOSFET IRLZ44N (chave) ──► filtros RC ──► ADC do STM32F103 (Blue Pill)
                                                                           │ TIM3 6000 Hz + DMA
                                                                           ▼
                                                      firmware STM32Cube (quadpv.c) ── USB CDC ──►
                                                      interface Python (PySide6 + pyqtgraph) ──► CSV
```

Três camadas, cada uma testável sozinha:
1. **Hardware** analógico simples (sem op-amp, sem Hall, sem carga eletrônica).
2. **Firmware** que entrega médias de 1 s em contagens brutas + desvio padrão, por texto ASCII.
3. **Interface** que converte, calibra, calcula razões/ΔT e grava.

## 3. Hardware

- **Blue Pill STM32F103C8T6**, USB CDC (VID `0x0483` / PID `0x5740`, o mesmo par do projeto SOI).
- Por módulo: shunt **2 Ω 1 %** na fonte de um **IRLZ44N** usado como chave.
  - Gates: **PB6–PB9**. Tensão do shunt: **PA1–PA4**. Tensão do módulo: **PA5–PA7, PB0**.
  - PA0 e PB1 livres (reservados para possível NTC/termopar).
- Entrada de corrente: filtro 1 kΩ + 470 nF; **470 kΩ do 3V3** mantém o zero em ~9 contagens
  (acima de 0 V, para o ADC não cortar o ruído). Entrada de tensão: 100 Ω + 4,7 µF.
- IRLZ44N funciona com 3,3 V no gate porque o shunt está na fonte: Rds(on) não entra na medida.
- Terra em estrela nos shunts, ligação Kelvin, par trançado até os módulos.
- Restrição: todos os sinais em 0–3,3 V → **Voc do módulo < 3,3 V**.
- Detalhes: `docs/01_Materials.md`, `docs/02_Hardware_and_Wiring.md`.

## 4. Firmware (`firmware/quadpv_cube/`, versão 3.0.0)

- **STM32Cube HAL** + USB Device CDC, compilado com **Makefile** (arm-none-eabi-gcc).
  Usa ~27 KB de flash e ~8 KB de RAM. Comentários e mensagens `#` em **português**.
- `QuadPV.ioc` foi **escrito à mão** e ainda não foi aberto no CubeMX.
- Arquivos principais:
  - `Core/Src/quadpv.c` + `Core/Inc/quadpv.h` — toda a lógica do QuadPV (`QPV_Init`, `QPV_Loop`,
    `QPV_OnUsbRx`); versão em `QPV_FW_VERSION`.
  - `Core/Src/main.c` — inicialização no formato CubeMX (chamadas nos blocos `USER CODE`).
  - `Core/Src/stm32f1xx_hal_msp.c` — pinos de ADC, DMA e TIM3.
  - `USB_DEVICE/` — porta serial virtual.
- **Aquisição**
  - TIM3 dispara o ADC a **6000 Hz** exatos (72 MHz / 12 000). Cada disparo converte 9 canais:
    4 shunts + 4 tensões (71,5 ciclos) + **Vrefint** (239,5 ciclos). Uma varredura = 77 µs.
  - DMA circular; a cada 16 varreduras a interrupção acumula soma e soma dos quadrados.
  - **Média de 1 s = 6000 varreduras = 60 ciclos de 60 Hz** → rede e cintilação de 120 Hz cancelam.
  - **Janela de Voc** a cada T s (padrão 60) ou pelo comando `V`: MOSFETs abrem, descarta 21 ms,
    média de 400 varreduras (4 ciclos de 60 Hz), fecha. A mesma janela dá o **zero do shunt**.
    Esse segundo não gera linha `D`. Primeira janela 2 s após ligar.
  - Troca de modo descarta o segundo corrente + 21 ms.
  - Na partida, D+ é puxado para baixo por 15 ms para forçar a reenumeração (problema conhecido da Blue Pill).
- **Protocolo serial** (ASCII, `\r\n`)
  - Placa → PC: `D n m a1..a4 v1..v4 r s1..s9` (média de 1 s), `V …` (janela de Voc),
    `# QuadPV fw 3.0.0 tipo=Isc-Voc modo=C T=60 fs=6000 perdidos=0` (identificação).
  - PC → placa: `?`, `V`, `T s` (0 = só sob comando), `M C` (curto, Isc), `M O` (aberto). Comandos enfileirados.
- **Banco de testes no PC** (`firmware/teste_host/`): o mesmo `quadpv.c` compilado para o PC contra um
  modelo (4 transmitâncias, ruído branco, offset por canal, 5 contagens de 60 Hz, 2 % de 120 Hz).

## 5. Interface (`interface/`, versão 3.0.1)

- `quadpv_core.py` — protocolo, conversão, calibração, estimativa de ΔT, `SerialLink` (thread),
  `Simulator`. Sem GUI. Versão em `__version__`.
- `quadpv.py` — janela PySide6 + pyqtgraph (~1200 linhas).
- Detecção da placa no estilo do SOI: portas candidatas (`QUADPV_PORT`, nome "QuadPV", VID 0x0483…),
  confirma com `?`; outras placas ignoradas por 30 s; firmware 2.x/original = "incompatível".
- Recursos: Isc relativo com erro padrão, média móvel 1 s–15 min, **"Calibrate modules…"**
  (razão de Isc e offset de Voc em condições idênticas), ΔT estimado, aba de diagnóstico de ruído,
  modo **Simulate**.
- Saída: `Measurements/<amostra>_QuadPV_<data>.csv` (por segundo) e `…_Voc.csv`. Preferências em
  QSettings (`HKCU\Software\LCTM\QuadPV` no Windows).
- Lançadores `QuadPV.bat` (Windows) e `quadpv.sh` (Linux/macOS): procuram `QUADPV_PYTHON`, `.venv`,
  Miniconda/Anaconda/Miniforge, Python oficial e PATH; oferecem `pip install -r requirements.txt`.
  No PC do Marcos: Miniconda em `C:\Users\mbelancon\miniconda3`.
- Textos da interface, docs e comentários Python em **inglês**; nomes de colunas do CSV em **português**.

## 6. Estrutura do repositório

```
QuadPV.bat / quadpv.sh       lançadores da interface
interface/                   quadpv.py (GUI), quadpv_core.py (núcleo sem GUI)
firmware/quadpv_cube/        firmware 3.x STM32Cube (Makefile, QuadPV.ioc, Core/, USB_DEVICE/, Drivers/, Middlewares/)
firmware/teste_host/         banco de testes do firmware no PC (sim.c + stubs)
firmware/pv/pv.ino           firmware Arduino original (2020), só referência
legacy/pv-stm32.py           interface Tkinter original, só referência
docs/                        01_Materials … 05_Calibration_and_Uncertainty + images/
CONTEXT.md, CHANGELOG.md, README.md, LICENSE, requirements.txt
```

Branches remotos: `main` (atual), `carga-eletronica` (v2.0.0 com carga eletrônica, curvas I–V e MPPT),
`copilot/vc-esta-funcionando` (experimento antigo).

## 7. Como compilar e rodar

**Firmware** (arm-none-eabi-gcc no PATH, vem com STM32CubeIDE ou STM32CubeCLT):
```bash
cd firmware/quadpv_cube
make          # build/QuadPV.elf, .hex, .bin
make flash    # grava via ST-Link (SWD) com STM32_Programmer_CLI
```
Alternativas: STM32CubeIDE importando `QuadPV.ioc` (copiar `quadpv.c/.h` se faltarem), ou bootloader
UART (PA9/PA10, BOOT0 = 1) com STM32CubeProgrammer.

**Banco de testes do firmware no PC:**
```bash
cd firmware/teste_host
gcc -O2 -Wall -Istub -I../quadpv_cube/Core/Inc -o sim sim.c ../quadpv_cube/Core/Src/quadpv.c -lm
./sim saida.txt              # com ruído e 60 Hz
./sim limpo.txt sem_ruido    # sem ruído, para comparar
```
Conferido em 13/11/2026: compila sem avisos e gera linhas `D`/`V` coerentes.

**Interface:** `QuadPV.bat` ou `./quadpv.sh` (Python ≥ 3.10; numpy, pyserial, pyqtgraph, PySide6).
Sem placa, marcar **Simulate**.

## 8. Mudanças desta versão (3.0.1, sobre a 3.0.0)

- Documentação, textos da interface e comentários Python traduzidos para inglês; docs renomeados
  (`docs/01_…` a `docs/05_…`, `docs/images/`); pasta de medidas agora `Measurements/`; `legado/` → `legacy/`.
- Novo `CONTEXT.md` (contexto curto para desenvolvedores e assistentes de IA).
- Correção: geometria de janela salva inválida não descarta mais as outras preferências.

Base trazida pela 3.0.0 (mesma semana): hardware shunt + chave IRLZ44N, firmware com TIM3 a 6000 Hz e
janela de Voc, novo protocolo `D`/`V`, calibração entre módulos, estimativa de ΔT, lançadores
`.bat`/`.sh`, fim do empacotamento PyInstaller, licença GPL-3.0, repo renomeado de SolarPower para QuadPV.

## 9. Decisões já tomadas (não reabrir sem motivo)

- Nome QuadPV (antes SolarPower); repo renomeado para `mbelancon/QuadPV`.
- Medida por Isc com MOSFET como chave, no lugar de carga eletrônica (menos ruído, mais simples). Sem sensores Hall.
- Módulos PWM→analógico (CPT10) e DACs (MCP4725/MCP4728) avaliados e descartados.
- STM32Cube HAL (não stm32duino).
- Sem versão portátil por enquanto: usa o Python local.
- CHANGELOG + semver; versões em `__version__` (quadpv_core.py) e `QPV_FW_VERSION` (quadpv.h).
- Cópias do projeto também no Google Drive, pasta Claude/QuadPV (zip por versão + planilha
  "QuadPV - lista de compras").

## 10. Problemas conhecidos e pendências

- **Nada rodou em hardware real ainda**: placa a montar, firmware a gravar, detecção de porta e
  `QuadPV.bat` a testar no PC do Marcos.
- `QuadPV.ioc` nunca foi aberto no CubeMX; pode precisar de ajustes.
- **Sem testes automatizados** no repo nem CI; as verificações foram com o banco `teste_host` e a simulação da interface.
- **Nenhum release/tag no GitHub**; a versão só existe no CHANGELOG e no código.
- Versões divergentes: firmware 3.0.0 e interface 3.0.1. O `Simulator` da interface se anuncia como
  `QuadPV fw 3.0.1` (usa `__version__` da interface), enquanto o firmware real responde `fw 3.0.0`.
- Restrição de hardware: Voc dos módulos precisa ficar abaixo de 3,3 V.
- Possíveis próximos passos: temperatura absoluta dos módulos (NTC/termopar em PA0/PB1), sensor de irradiância.

## 11. Regras de trabalho do projeto

- A cada novo release publicado, criar um arquivo de contexto do release
  (`docs/context/CONTEXT-<versão>.md` no repo, também na pasta Claude do Google Drive).
- Sempre ler o arquivo de contexto mais recente antes de qualquer tarefa do projeto.
- Este arquivo ainda **não está no repo**: entra no próximo PR.
