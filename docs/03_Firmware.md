# 3. Firmware

Há dois firmwares. A interface reconhece qual está na placa e se adapta.

| | Firmware 2.x (STM32Cube) | Firmware antigo |
|---|---|---|
| Pasta | [`firmware/quadpv_cube/`](../firmware/quadpv_cube/) | [`firmware/pv/pv.ino`](../firmware/pv/pv.ino) |
| Plataforma | STM32Cube HAL (CubeIDE ou `make`) | Arduino IDE, núcleo STM32duino |
| Carga | Eletrônica (MOSFET + op-amp): MPPT, gate fixo, aberto ou curto | Resistor fixo |
| Curvas I–V | Sim, a cada T segundos, no botão e quando passa uma nuvem | Não |
| Médias por ponto (1 s) | ~15 900 varreduras dos 9 canais | 192 |
| Identificação | Responde a `?` com `# QuadPV fw x.y.z …` | Nenhuma |

## Firmware 2.x

### Estrutura

```
firmware/quadpv_cube/
├── QuadPV.ioc                      Configuração do STM32CubeMX (pinos, relógios, periféricos)
├── Core/Src/main.c                 Inicialização gerada no formato do CubeMX (só chama QPV_Init/QPV_Loop)
├── Core/Src/quadpv.c               Lógica do QuadPV: aquisição, carga, varredura I–V, MPPT, comandos
├── Core/Src/stm32f1xx_hal_msp.c    Pinos do ADC, DMA e TIM4
├── USB_DEVICE/                     Porta serial virtual (CDC)
├── Drivers/, Middlewares/          HAL, CMSIS e biblioteca USB da ST (cópias, com as licenças)
├── STM32F103C8Tx_FLASH.ld          Mapa de memória (64 KB de flash, 20 KB de RAM)
└── Makefile                        Compilação com o arm-none-eabi-gcc
```

Ocupa ~28 KB de flash e ~10 KB de RAM.

### Compilar e gravar

**Com `make`** (arm-none-eabi-gcc no PATH; vem com o STM32CubeIDE ou o STM32CubeCLT):

```bash
cd firmware/quadpv_cube
make            # gera build/QuadPV.elf, .hex e .bin
make flash      # grava pelo ST-Link (SWD) com o STM32_Programmer_CLI
```

**Com o STM32CubeIDE**: *File > New > STM32 Project from an Existing STM32CubeMX Configuration File (.ioc)*, escolha o `QuadPV.ioc` e gere o código. O CubeMX preserva os blocos `USER CODE`, onde estão as chamadas ao QuadPV. Depois copie `Core/Src/quadpv.c` e `Core/Inc/quadpv.h` para o projeto gerado, se o CubeIDE não os trouxer.

> O `QuadPV.ioc` foi escrito à mão e não foi aberto no CubeMX durante o desenvolvimento. O projeto compila sem ele (Makefile). Se o CubeMX reclamar de algo ao abri-lo, confira os pinos com a tabela de [02](02_Hardware_e_Ligacoes.md#pinos).

**Sem ST-Link**: a Blue Pill tem bootloader serial de fábrica. Ligue um adaptador USB-serial em PA9 (TX) e PA10 (RX), ponha o jumper BOOT0 em 1, reinicie e grave o `build/QuadPV.bin` pelo STM32CubeProgrammer (porta UART). Volte o BOOT0 para 0.

**USB**: a placa aparece como porta serial virtual com VID `0x0483` e PID `0x5740` (o par padrão da ST). Windows 10/11, Linux e macOS usam o driver nativo, sem instalar nada. Ao ligar, o firmware puxa o D+ para baixo por 15 ms para o PC reenumerar a placa. Isso contorna um problema conhecido da Blue Pill.

### Funcionamento

- **Aquisição**: o ADC1 converte os 9 canais em sequência, continuamente (71,5 ciclos de amostragem a 12 MHz, 63 µs por varredura). O DMA escreve num buffer circular e, a cada 16 varreduras (~1 ms), uma interrupção soma as amostras em dois acumuladores: o do segundo e o do ponto da varredura I–V.
- **Carga**: o TIM4 gera PWM de 10 bits (70,3 kHz) em PB6–PB9, que vira a referência de corrente de cada canal (ver [02](02_Hardware_e_Ligacoes.md#carga-eletrônica-firmware-2x)).
- **Varredura I–V** (~0,7 s, os 4 módulos ao mesmo tempo):
  1. Passada grossa, com o PWM de 0 a 1023 em 33 passos. Em cada módulo, ela acha a faixa útil, do último ponto com corrente < 3 % de Isc até o primeiro com corrente ≥ 98 % de Isc ou tensão < 3 % de Voc.
  2. Passada fina, com 100 pontos dentro dessa faixa, mais o ponto em aberto (PWM 0) e o de curto (PWM 1023).
  3. Cada ponto espera 3 ms (> 5 constantes de tempo do RC) e faz a média de ~2 ms.
- **MPPT**, uma vez por segundo, com as médias do segundo:
  - perturba e observa, com passo de 2 contagens (~0,6 mA);
  - se a tensão cair abaixo de 50 % da Voc da última varredura, a corrente pedida passou de Isc, e o firmware reduz 16 contagens por segundo; se a tensão passar de 95 % da Voc, ele aumenta 16;
  - se a potência de algum módulo mudar mais de 25 % em 1 s (borda de nuvem), ele refaz a varredura, no máximo uma vez a cada 10 s;
  - depois de cada varredura, o MPPT recomeça do ponto de máxima potência medido.
- **Primeira varredura**: 3 s depois de ligar. Até lá o PWM fica em 0 (módulos em aberto).

### Protocolo serial

Texto ASCII, uma mensagem por linha (`\r\n`).

**Da placa para o PC**

| Linha | Quando | Conteúdo |
|---|---|---|
| `A0 A1 A5 A2 A6 A3 A7 A4 A8` | ~1 s | Médias do segundo em contagens do ADC, 2 decimais. Mesma ordem do firmware antigo: referência, depois (corrente, tensão) de cada módulo |
| `G g1 g2 g3 g4 modo` | ~1 s | PWM atual de cada módulo (0–1023) e o modo (`P`, `F`, `O`, `C`). Vale para a próxima linha de médias |
| `IV m n  ref I V g  ref I V g …` | após cada varredura | Os *n* pontos do módulo *m* (1–4): médias de ~2 ms em contagens e o PWM. O 1º ponto é o aberto (PWM 0) e o último, o curto (PWM 1023) |
| `# …` | resposta a comandos | Ex.: `# QuadPV fw 2.0.0 modo=P gate=0 T=60 NF=100` |

**Do PC para a placa**

| Comando | Efeito |
|---|---|
| `?` | Identificação e estado |
| `S` | Varredura I–V agora |
| `T s` | Varredura automática a cada *s* segundos (0 = só manual; padrão 60) |
| `M P` | Rastreia o ponto de máxima potência (padrão) |
| `M F g` | PWM fixo em *g* (0–1023) nos 4 módulos |
| `M O` | Aberto (PWM 0): mede Voc continuamente |
| `M C` | Curto (PWM 1023): mede ~Isc continuamente |

Dá para testar com qualquer terminal serial (o PuTTY, por exemplo, ou o monitor serial da Arduino IDE).

### Teste sem a placa

[`firmware/teste_host/`](../firmware/teste_host/) compila o mesmo `quadpv.c` para o PC, com o HAL substituído por um modelo físico:

- módulo de diodo único;
- sensor Hall com offset;
- MOSFET com ou sem o op-amp;
- RC da referência;
- ADC de 12 bits com ruído.

A simulação roda 75 s, com uma nuvem aos 30 s e a volta do sol aos 50 s, e compara a potência rastreada com o máximo real:

```bash
cd firmware/teste_host
gcc -O1 -Wall -Istub -I../quadpv_cube/Core/Inc -o sim sim.c ../quadpv_cube/Core/Src/quadpv.c -lm
./sim saida.txt            # com op-amp
./sim saida.txt aberta     # gate direto, sem op-amp
```

A saída (`saida.txt`) tem exatamente as linhas que a placa mandaria.

## Firmware antigo

1. Instale o **STM32CubeProgrammer** e depois a Arduino IDE.
2. Na Arduino IDE, instale o núcleo **STM32 MCU based boards** (STM32duino).
3. Placa: *Generic STM32F1 series*, *BluePill F103C8*; *USB support: CDC (generic Serial supersede U(S)ART)*.
4. Abra `firmware/pv/pv.ino` e grave.

Ele lê A0–A8 em sequência 192 vezes, com 5 ms entre as rodadas, e envia as médias com uma casa decimal, na mesma linha do firmware 2.x. Não aceita comandos.
