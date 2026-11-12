# 2. Hardware e ligações

## Topologia de cada canal

```
 módulo (+) ──┬───────────────────────────────── entrada de tensão (A5..A8)
              │
              └── IP+ [WCS2702] IP− ── carga R ── GND
                        │
                     saída ─────────────────────── entrada de corrente (A1..A4)

 módulo (−) ── GND
```

- O (−) de todos os módulos vai ao **GND da Blue Pill**. A tensão medida é a do terminal (+) do módulo.
- A corrente do módulo passa pelo sensor Hall e pela carga até o GND.
- Um quinto sensor, **sem corrente**, vai em **A0**. Ele acompanha a deriva comum dos sensores (alimentação e temperatura), que a interface desconta das leituras de A1–A4.

## Pinos

| Pino Arduino | Pino STM32 | Sinal |
|---|---|---|
| A0 | PA0 | Sensor de referência (sem corrente) |
| A1–A4 | PA1–PA4 | Corrente dos módulos 1–4 |
| A5–A8 | PA5–PA7, PB0 | Tensão dos módulos 1–4 |

*(a completar: confirmar o mapeamento A5–A8 no núcleo STM32duino usado e a alimentação dos sensores, 3,3 V ou 5 V.)*

## Limites e cuidados

- **Todas as entradas devem ficar entre 0 e 3,3 V.** A tensão de circuito aberto dos módulos precisa ficar abaixo disso em qualquer condição (dias frios aumentam a tensão). Para módulos maiores, use um divisor resistivo e informe o fator em *Configurar módulos…*.
- Sensores Hall têm a saída centrada em metade da alimentação. **Se os WCS2702 forem alimentados com 5 V**, a saída em repouso fica perto de 2,5 V e a corrente máxima medível antes de saturar o ADC (3,3 V) é limitada. Confira com a sensibilidade do sensor. A interface avisa quando alguma entrada encosta em 0 ou em 3,3 V.
- A carga define o ponto de operação. Com carga fixa, o módulo não trabalha no ponto de máxima potência na maior parte do dia (ver [05](05_Calibracao_e_Incertezas.md#qual-grandeza-comparar)).
- Mantenha as fiações dos quatro canais iguais: resistências de fio diferentes mudam o ponto de operação e quebram a comparação entre módulos idênticos.
