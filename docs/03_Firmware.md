# 3. Firmware

Pasta: [`firmware/quadpv_cube/`](../firmware/quadpv_cube/), em STM32Cube HAL. Ocupa ~27 KB de flash e ~8 KB de RAM.

## Estrutura

```
firmware/quadpv_cube/
├── QuadPV.ioc                      Configuração do STM32CubeMX (pinos, relógios, periféricos)
├── Core/Src/main.c                 Inicialização no formato do CubeMX (chama QPV_Init/QPV_Loop)
├── Core/Src/quadpv.c               Lógica do QuadPV: médias, janela de Voc, protocolo, comandos
├── Core/Src/stm32f1xx_hal_msp.c    Pinos do ADC, DMA e TIM3
├── USB_DEVICE/                     Porta serial virtual (CDC)
├── Drivers/, Middlewares/          HAL, CMSIS e biblioteca USB da ST (cópias, com as licenças)
├── STM32F103C8Tx_FLASH.ld          Mapa de memória
└── Makefile                        Compilação com o arm-none-eabi-gcc
```

## Compilar e gravar

**Com `make`** (arm-none-eabi-gcc no PATH; vem com o STM32CubeIDE ou o STM32CubeCLT):

```bash
cd firmware/quadpv_cube
make            # gera build/QuadPV.elf, .hex e .bin
make flash      # grava pelo ST-Link (SWD) com o STM32_Programmer_CLI
```

**Com o STM32CubeIDE**: *File > New > STM32 Project from an Existing STM32CubeMX Configuration File (.ioc)*, escolha o `QuadPV.ioc` e gere o código. O CubeMX preserva os blocos `USER CODE`, onde estão as chamadas ao QuadPV e os pinos dos gates. Copie `Core/Src/quadpv.c` e `Core/Inc/quadpv.h` para o projeto, se não vierem.

> O `QuadPV.ioc` foi escrito à mão e ainda não foi aberto no CubeMX. O projeto compila sem ele (Makefile). Se o CubeMX reclamar ao abri-lo, confira os pinos com a tabela de [02](02_Hardware_e_Ligacoes.md#pinos).

**Sem ST-Link:** ligue um adaptador USB-serial em PA9 (TX) e PA10 (RX), ponha o jumper BOOT0 em 1, reinicie e grave o `build/QuadPV.bin` pelo STM32CubeProgrammer (porta UART). Volte o BOOT0 para 0.

**USB:** porta serial virtual com VID `0x0483` e PID `0x5740` (o par padrão da ST), sem driver extra no Windows 10/11, Linux ou macOS. Ao ligar, o firmware puxa o D+ para baixo por 15 ms para o PC reenumerar a placa (problema conhecido da Blue Pill).

## Aquisição

- **Disparo:** o TIM3 gera 6000 disparos por segundo, derivados do cristal (72 MHz / 12 000). Cada disparo faz o ADC converter 9 canais em sequência:
  - os 4 shunts e as 4 tensões, com 71,5 ciclos de amostragem cada;
  - a Vrefint, com 239,5 ciclos (a ST pede ≥ 17,1 µs).

  Uma varredura leva 77 µs.
- **DMA:** o DMA grava num buffer circular. A cada 16 varreduras (~2,7 ms), a interrupção soma cada canal e o seu quadrado (para o desvio-padrão).
- **Médias de 1 s:** 6000 varreduras exatas (375 blocos) formam uma linha `D`. Como 6000 varreduras = 60 ciclos de 60 Hz, a interferência da rede e das harmônicas (inclusive o piscar de 120 Hz das lâmpadas) se cancela na média.
- **Janela de Voc** (a cada T segundos, padrão 60, ou pelo comando `V`), no início de um segundo:
  1. os 4 MOSFETs abrem juntos;
  2. 8 blocos (21 ms) são descartados enquanto a tensão estabiliza;
  3. 25 blocos (400 varreduras = 66,7 ms = 4 ciclos de 60 Hz) formam a linha `V`;
  4. os MOSFETs fecham, e o resto do segundo é descartado.

  Esse segundo não gera linha `D`. Na linha `V`, os canais de corrente dão o zero dos shunts (sem corrente) e os de tensão dão Voc.
- **Troca de modo:** o segundo em curso é descartado, mais 21 ms, para não misturar curto e aberto.
- **Primeira janela de Voc:** 2 s depois de ligar, para a interface ter o zero logo.

## Protocolo serial

Texto ASCII, uma mensagem por linha (`\r\n`).

**Da placa para o PC**

```
D n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 s2 s3 s4 s5 s6 s7 s8 s9
V n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 s2 s3 s4 s5 s6 s7 s8 s9
# QuadPV fw 3.0.0 tipo=Isc-Voc modo=C T=60 fs=6000 perdidos=0
```

| Campo | Significado |
|---|---|
| `D` / `V` | Média de 1 s (módulos no modo atual) / janela de Voc (módulos em aberto) |
| `n` | Varreduras promediadas: 6000 na `D`, 400 na `V` |
| `m` | Modo: `C` (curto, mede Isc) ou `O` (aberto) |
| `a1`…`a4` | Tensão nos shunts, em contagens, 3 decimais |
| `v1`…`v4` | Tensão dos módulos, em contagens, 3 decimais |
| `r` | Vrefint, em contagens, 3 decimais |
| `s1`…`s9` | Desvio-padrão de uma amostra de cada canal, na mesma ordem, em contagens, 2 decimais (o erro da média é s/√n) |
| `perdidos` | Segundos que o laço principal não conseguiu enviar (deve ficar em 0) |

**Do PC para a placa**

| Comando | Efeito |
|---|---|
| `?` | Identificação e estado |
| `V` | Mede Voc no próximo segundo |
| `T s` | Voc a cada *s* segundos (0 = só no comando `V`; padrão 60) |
| `M C` | Curto: mede Isc continuamente (padrão) |
| `M O` | Aberto: mede Voc continuamente (linhas `D` com `m = O`) |

Os comandos entram numa fila, então vários seguidos não se perdem. Dá para testar com qualquer terminal serial.

## Teste sem a placa

[`firmware/teste_host/`](../firmware/teste_host/) compila o mesmo `quadpv.c` para o PC, contra um modelo:

- 4 módulos com transmitâncias diferentes;
- shunt + MOSFET;
- ADC de 12 bits a 6 kHz com ruído branco de 1,5 contagem e offset por canal;
- 60 Hz de 5 contagens em todos os canais e 2 % de cintilação de 120 Hz na luz.

```bash
cd firmware/teste_host
gcc -O2 -Wall -Istub -I../quadpv_cube/Core/Inc -o sim sim.c ../quadpv_cube/Core/Src/quadpv.c -lm
./sim saida.txt              # com ruído e interferência
./sim limpo.txt sem_ruido    # sem ruído nem 60 Hz, para comparar
```

Resultado na última versão:
- as médias de 1 s com e sem ruído diferem em menos de 0,05 contagem (0,02 mA), ou seja, a interferência de 60 Hz se cancela;
- o zero medido nas janelas de Voc acerta o valor verdadeiro dentro de ±0,05 contagem.
