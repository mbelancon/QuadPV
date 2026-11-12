# 5. Conversão, calibração e incertezas

## Por que Isc

A corrente de curto-circuito é proporcional ao número de fótons absorvidos pelas células, ponderado pela resposta espectral do silício (~300–1100 nm). É exatamente o que a cobertura altera, por transmissão, reflexão e espalhamento. A Isc quase não depende da temperatura (~+0,05 % por kelvin). Já a potência no ponto de máxima potência mistura a parte óptica com temperatura, tensão e perdas internas.

## Conversão

Com `k = Vrefint / r` (mV por contagem; ~0,806 mV com 3,3 V):

```
I_n [mA]  = (a_n − zero_n) · k / R_shunt,n / cal_Isc,n
Voc_n [mV] = v_n · k · divisor_n − cal_Voc,n
```

- `a_n`, `v_n`, `r` são as médias enviadas pelo firmware, em contagens.
- `zero_n` é a leitura do shunt na última janela de Voc (sem corrente).
- `cal_Isc,n` e `cal_Voc,n` vêm da calibração dos módulos (1 e 0 sem calibração).

Com *Tensão do ADC pela Vrefint* desligada, `k = Vref / 4096`.

## Incerteza da Isc relativa

A razão Isc_n / Isc_ref é a grandeza principal. Fatores que **não** a afetam:

- a variação do sol, porque os módulos são amostrados juntos;
- a tensão de referência do ADC (aparece em cima e embaixo e se cancela);
- a interferência de 60 Hz, que se cancela nas médias sincronizadas;
- o offset dos canais, descontado a cada minuto.

O que **sobra**:

| Fonte | Tamanho típico | Como reduzir |
|---|---|---|
| Ruído do ADC | ~0,01 mA em 1 s (σ de 2 contagens / √6000); ~0,003 % da razão com 1 min de média | Janela de média maior |
| Tolerância dos shunts | até 1 % por shunt | Medir cada shunt e informar; ou calibrar os módulos |
| Diferenças entre os módulos "idênticos" | 1–2 % | **Calibrar os módulos** |
| Deriva térmica dos shunts | 50 ppm/K: 0,05 % com 10 K de diferença | Shunts à sombra, perto uns dos outros |
| Corrente perdida na resistência paralela do módulo | V/Rp: ~0,25 V / 500 Ω = 0,5 mA (0,4 %), quase igual nos 4 | Já se cancela em boa parte na razão |
| Não uniformidade da luz, sombra parcial | qualquer | Módulos lado a lado, mesma orientação, sem sombras |

## Calibração dos módulos

Módulos de um mesmo lote diferem em ~1–2 % na Isc e em alguns mV na Voc. Os shunts e a fiação também diferem um pouco. Como o experimento compara coberturas pela razão entre módulos, essas diferenças entrariam como se fossem efeito da cobertura.

Procedimento:

1. Ponha os quatro módulos na **mesma condição**: todos sem cobertura, ou todos com a cobertura de referência. Confira se não há sombra nem sujeira diferente.
2. Meça alguns minutos, de preferência com sol estável. Use **Média** de 5 min ou mais. Pelo menos uma medida de Voc deve cair nesse período.
3. Clique em **Calibrar módulos…**, confira os valores (razões perto de 1 e diferenças de Voc de poucos mV) e aplique.
4. Ponha as coberturas e meça.

Refaça a calibração de tempos em tempos e ao trocar um módulo ou shunt. Uma boa prática é intercalar: calibração, coberturas, calibração de novo. Se as duas calibrações forem iguais dentro do erro, o sistema ficou estável durante a medida. Outra opção é trocar as coberturas de módulo entre medidas: se a razão acompanhar a cobertura, e não o módulo, o efeito é da cobertura.

## Estimativa de ΔT pela Voc

A Voc cai ~2,2 mV por kelvin em cada célula de silício. Com Ns células em série, uma diferença de temperatura ΔT entre dois módulos dá ΔVoc ≈ Ns · (−2,2 mV/K) · ΔT.

A Voc também cresce com a luz: ΔVoc_luz = Ns · n · (kT/q) · ln(Isc_n / Isc_ref). A interface desconta essa parte usando a Isc média dos 10 s anteriores:

```
ΔT_n = (ΔVoc_n − Ns · n · (kT/q) · ln(Isc_n / Isc_ref)) / (Ns · dVoc/dT)
```

Parâmetros em *Configurar módulos…*: Ns (padrão 4), fator de idealidade n (1,3), temperatura típica dos módulos para kT/q (45 °C) e dVoc/dT por célula (−2,2 mV/K).

**Limitações:** o ΔT é uma **estimativa relativa**, útil para ver se uma cobertura aquece mais o módulo que outra. O erro vem de:

- n e dVoc/dT, que dependem da célula: ±20 % no valor de ΔT;
- a calibração de Voc, que precisa ser feita com os módulos à mesma temperatura;
- a resolução: 1 mV de ΔVoc ≈ 0,1 K.

Para temperatura absoluta, cole um termopar ou um NTC atrás de cada módulo. As entradas PA0 e PB1 estão livres.
