# 2. Hardware e ligações

## Um canal

![Um canal de medida de Isc e Voc](imagens/canal_isc_voc.png)

- **Chave:** o MOSFET (IRLZ44N) liga o (+) do módulo ao shunt. Com o gate em 3,3 V ele conduz, e o módulo fica em curto pelo shunt. Com o gate em 0, o módulo fica em aberto.
- **Shunt no source:** a corrente é lida na tensão do shunt (2 Ω), entre o source e o GND. A resistência do MOSFET não entra nessa medida, então não importa que ela só seja garantida a partir de 4 V no gate. Ela só soma um pouco na tensão do módulo durante a Isc: com 120 mA, o módulo fica em ~0,25 V, na parte plana da curva I–V, onde a corrente é a Isc com erro bem menor que 1 %.
- **IRLZ44N com 3,3 V no gate:** o limiar máximo é 2 V. Com ~3 V no gate (3,3 V menos a queda no shunt), ele conduz com folga os 100–300 mA dos módulos pequenos. Evite MOSFETs sem "L" (IRFZ44N, IRF540) e os pequenos 2N7000/BS170, cujo limiar pode chegar a 3–4 V.

## Pinos

| Pino | Sinal |
|---|---|
| PA1–PA4 | Tensão nos shunts (corrente) dos módulos 1–4 |
| PA5, PA6, PA7, PB0 | Tensão dos módulos 1–4 |
| PB6–PB9 | Gates dos MOSFETs dos módulos 1–4 |
| PA0, PB1 | Livres (entradas analógicas; ex.: um sensor de irradiância) |
| PA11, PA12 | USB |
| PA13, PA14 | SWD (ST-Link) |
| PC13 | LED da placa (pisca a cada leitura) |

## Valores e por quê

| Componente | Valor | Motivo |
|---|---|---|
| Shunt | 2 Ω, 1 %, 0,5 W, filme metálico | ~240 mV com 120 mA (~300 contagens do ADC), ainda na parte plana da curva I–V. Filme metálico deriva ~50 ppm/K. Dissipa ~30 mW |
| Filtro da corrente | 1 kΩ + 470 nF | Corta acima de ~340 Hz, antes da amostragem a 6 kHz. A corrente de fuga do pino (até 1 µA) gera no máximo 1 mV nesse resistor, e esse erro entra no zero medido a cada minuto |
| Deslocamento do zero | 470 kΩ do 3V3 à entrada de corrente | Sem corrente, o shunt fica em 0 V, e o ADC não lê tensões negativas: o ruído seria cortado em zero e o offset medido sairia distorcido. Com 470 kΩ e o 1 kΩ do filtro, o zero fica em ~7 mV (~9 contagens). O deslocamento é proporcional ao 3,3 V, fixo em contagens, e é descontado junto com o offset |
| Filtro da tensão | 100 Ω + 4,7 µF | Mesma faixa do filtro de corrente. O resistor é pequeno para a corrente de fuga não somar mais que 0,1 mV à Voc, porque 1 mV de ΔVoc equivale a ~0,1 K de ΔT |
| Gate | 100 Ω em série, 100 kΩ ao GND | O de 100 kΩ mantém o MOSFET desligado com a placa sem alimentação |
| Desacoplamento | 10 µF + 100 nF entre 3V3 e GND | O 3,3 V alimenta o ADC; ruído nele vira ruído na medida |

**Outra faixa de corrente:** escolha o shunt para que Isc × R fique entre 0,15 V e ~10 % da Voc. Abaixo disso a resolução cai; acima, o módulo sai da parte plana da curva e a corrente medida fica abaixo da Isc.

## Montagem contra ruído

- **Terra em estrela:** o (−) de cada módulo, o lado de baixo do shunt e o GND do ADC se encontram num só ponto junto aos shunts, e daí um único fio vai ao GND da Blue Pill. Correntes dos módulos não devem passar pelo fio de terra do ADC.
- **Ligação Kelvin no shunt:** a entrada do ADC (via 1 kΩ) sai direto da perna do shunt, não de um ponto da trilha por onde passa a corrente.
- **Par trançado** dos módulos até a placa, um par por módulo. Fios longos e soltos captam 60 Hz.
- **Filtros junto aos pinos:** os capacitores de 470 nF e 4,7 µF ficam encostados nos pinos da Blue Pill, com o GND no mesmo ponto da estrela.
- **Fiação igual nos quatro canais:** os módulos são comparados entre si, e diferenças de fiação viram diferenças de medida (a calibração corrige as que forem constantes).
- **Alimentação:** um notebook na bateria é a fonte mais limpa. Se o PC estiver na tomada e o ruído incomodar, um isolador USB resolve; a aba Diagnóstico mostra o ruído de cada canal.

## Limites

- **Todas as entradas entre 0 e 3,3 V.** A Voc dos módulos precisa ficar abaixo disso em qualquer condição (dias frios aumentam a Voc). Para módulos maiores, use um divisor resistivo na entrada de tensão e informe o fator em *Configurar módulos…*. A Isc não tem esse problema: a tensão no shunt é pequena.
- A interface avisa quando alguma entrada encosta em 3,3 V.
