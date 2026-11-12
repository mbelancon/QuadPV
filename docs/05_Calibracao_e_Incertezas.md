# 5. Conversão, calibração e incertezas

## Conversão

Com `k = Vref / 4096` (mV por contagem; 0,806 mV com Vref = 3300 mV):

```
V_n [mV] = A(4+n) · k · divisor_n
I_n [mA] = ( (A_n − A0) · k − offset_n ) / sensibilidade_n          (n = 1..4)
P_n [mW] = V_n · I_n / 1000
E_n [mWh] = ∫ P_n dt / 3600   (regra do trapézio; intervalos > 30 s não entram)
```

Subtrair `A0` remove a deriva comum dos sensores Hall. O `offset_n` é medido no zero, com os módulos cobertos.

A interface original usava os mesmos números: 0,8056 mV/contagem e 0,9 mV/mA.

## Zero das correntes

Cubra os quatro módulos e clique em **Zerar correntes…**. A interface promedia ~5 leituras e guarda o offset de cada sensor. A mensagem final mostra o desvio-padrão de cada sensor no zero, em mA: é uma boa estimativa do ruído da corrente.

Refaça o zero no início de cada experimento e sempre que mudar a Vref ou a opção do sensor A0.

Com o **firmware 2.x** o zero é automático: o primeiro ponto de cada varredura I–V é tomado com o MOSFET aberto (corrente nula), e a leitura de cada sensor nesse ponto vira o offset. Como as varreduras se repetem (a cada 60 s, por padrão), a deriva dos sensores ao longo do dia também é corrigida.

## Calibração da sensibilidade (recomendada)

A sensibilidade nominal (0,9 mV/mA) vale para todos os canais, mas cada sensor tem a sua. Para calibrar:

1. Ligue uma fonte de corrente (ou uma fonte de tensão com resistor) em série com o sensor e um multímetro.
2. Anote a corrente do multímetro e a corrente indicada pela interface em 3 a 5 pontos dentro da faixa de uso.
3. Nova sensibilidade = sensibilidade atual × (corrente indicada / corrente do multímetro), pela inclinação do ajuste linear.
4. Informe o valor em *Configurar módulos…*.

Meça também o pino 3V3 da Blue Pill com o multímetro e informe como Vref: o ADC usa essa tensão como referência, e o erro dela entra em escala em V e em I.

## Resolução

| Grandeza | Por contagem do ADC | Observação |
|---|---|---|
| Tensão | 0,81 mV | |
| Corrente | 0,90 mA | com 0,9 mV/mA |

Cada ponto é a média de 192 leituras, o que reduz o ruído aleatório (até ~14 vezes) mas não o erro de escala. Em correntes baixas (pouca luz), a resolução relativa piora: 1 mA em 20 mA já é 5 %.

## Qual grandeza comparar

Com **carga resistiva fixa**, o ponto de operação do módulo muda com a irradiância. Com pouca luz o módulo trabalha perto do curto-circuito e a potência na carga vale `P = R·I²`. Por isso a **razão das potências exagera** a diferença entre coberturas: no modelo de diodo único usado na simulação, uma cobertura com 93 % de transmitância aparece com ~86,5 % da potência, em quase toda a faixa de irradiância.

Já a **razão das correntes** fica em ~93 %, igual à razão das correntes de curto-circuito, enquanto a tensão de operação ficar abaixo do joelho da curva I–V. Com a carga fixa, para comparar a transmissão óptica de coberturas, use **Corrente relativa**.

Com a **carga eletrônica** (firmware 2.x), as grandezas certas vêm das varreduras:

| Pergunta | Grandeza |
|---|---|
| Quanto de luz cada cobertura deixa passar? | **Isc relativa** (proporcional à luz que chega às células) |
| Quanta energia cada módulo entregaria a um sistema com MPPT? | **Pmax relativa** e a energia acumulada no modo MPPT |
| A cobertura muda a temperatura ou as perdas do módulo? | **Voc** (cai com a temperatura) e **fator de preenchimento** |

Na simulação, com coberturas de 97 % e 93 %, as varreduras dão Isc relativa de ~96 % e ~92 % e Pmax relativa de ~95 % e ~92 %, dentro do ruído do ADC. Com a carga fixa, a potência relativa do módulo de 93 % aparecia como ~86 %.
