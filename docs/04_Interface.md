# 4. Interface no PC – QuadPV

Arquivo: [`interface/quadpv.py`](../interface/quadpv.py). PySide6 (ou PyQt) + pyqtgraph, no mesmo padrão do SOI Controller.

## Instalação e execução

```powershell
pip install -r requirements.txt
python interface/quadpv.py
```

O executável para Windows pode ser gerado como no SOI (spec em [`packaging/`](../packaging/) e workflow do GitHub Actions).

## Conexão

A placa é encontrada sozinha, como no SOI:

- **Porta automática**: testa, nesta ordem, a variável de ambiente `QUADPV_PORT` (ex.: `COM7`), um dispositivo USB chamado "QuadPV" (Linux e macOS mostram o nome), os dispositivos com VID `0x0483` (STM32), outras placas conhecidas e, por fim, a única porta USB presente.
- **Confirmação da identidade**: o SOI e o QuadPV usam o mesmo VID/PID do STM32. Ao abrir uma porta, a interface manda `?` e espera até 2,5 s:
  - se a resposta for `# QuadPV fw …`, é o firmware 2.x;
  - se em vez disso chegarem linhas de médias, é o firmware antigo;
  - se não vier nada reconhecível (por exemplo, os dados binários do SOI), a porta é recusada por 30 s e a busca continua nas outras.

  Assim as duas placas podem ficar ligadas no mesmo PC.
- A lista **Porta** permite fixar uma porta, que fica salva; com porta fixa a interface conecta mesmo sem confirmação e avisa na barra de status.
- A barra de status mostra o que foi encontrado: `QuadPV fw 2.0.0 em COM5`, `firmware antigo em COM5` ou o motivo de não conectar.
- Se a conexão cair, a interface tenta reconectar a cada 3 s. Ao reconectar a um firmware 2.x, ela reenvia o modo da carga e o intervalo de varredura escolhidos.
- A leitura serial roda numa thread própria.
- **Sem a placa**, aparece **Simular medidas**: quatro módulos com coberturas de transmitância 100 %, 97 %, 93 % e 102 %, sob um "dia" de 20 min com nuvens. A simulação imita o firmware 2.x, com carga eletrônica, MPPT e curvas I–V.
- As preferências (nomes, áreas, calibração, zero, porta, janela) ficam salvas ao fechar (no Windows, em `HKCU\Software\LCTM\QuadPV`).

## Barra superior

| Controle | Função |
|---|---|
| Amostra | Prefixo de todos os arquivos salvos em `Medidas/` |
| Porta | Automática ou uma porta fixa |
| Configurar módulos… | Nome, área (cm²), sensibilidade do sensor (mV/mA) e divisor de tensão de cada módulo; Vref do ADC; uso do sensor de referência A0; valor da carga (só para o registro) |
| Zerar correntes… | Com os módulos cobertos, mede ~5 s e guarda o offset de cada sensor. Mostra o ruído de cada sensor no zero. **Refaça se mudar a Vref ou o uso do A0.** Com o firmware 2.x não é preciso: o zero é automático (ver abaixo) |
| Gravar / Parar | Grava cada leitura no CSV assim que chega (nada se perde se o programa fechar) |
| Observação | Texto livre que vai para o cabeçalho do CSV |
| Reiniciar gráficos | Limpa os gráficos e zera a energia acumulada exibida |
| Finalizar experimento | Compacta num ZIP os arquivos salvos na sessão |

## Linha da carga (firmware 2.x)

| Controle | Função |
|---|---|
| Carga | **Máxima potência (MPPT)**, **Gate fixo** (com o valor de 0 a 1023), **Aberto (Voc)** ou **Curto (Isc)**. Vale entre as varreduras |
| Varredura I–V a cada | Intervalo das varreduras automáticas (0 = só manual). O firmware também varre quando uma nuvem muda a potência mais de 25 % em 1 s |
| Varrer agora | Mede as curvas dos 4 módulos (~0,7 s) |
| Gates | PWM atual de cada módulo |

Com o firmware antigo, esses controles ficam desabilitados.

**Zero automático.** O primeiro ponto de cada varredura é tomado com o MOSFET aberto, quando a corrente é nula. A leitura de cada sensor nesse ponto vira o offset, sem cobrir os módulos. A barra de status mostra `Zero: automático hh:mm:ss`. A opção pode ser desligada em *Configurar módulos…*.

## Aba Medidas

- **Cartões dos módulos**: potência, tensão, corrente, potência por área, energia acumulada e potência relativa ao módulo de referência. A caixa no cartão liga ou desliga a curva no gráfico. A potência fica vermelha se alguma entrada daquele módulo encostar no limite do ADC.
- **Gráfico**: potência, potência por área, tensão, corrente, energia, **potência relativa** ou **corrente relativa** (em % de um módulo escolhido). Com o firmware 2.x, também os parâmetros de cada varredura, como pontos: **Pmax**, **Pmax relativa**, **Isc**, **Isc relativa**, **Voc** e **fator de preenchimento**. Janelas de 5 min a 24 h, ou tudo.
- **Pausar** congela o gráfico e libera zoom e arraste com o mouse; a aquisição e a gravação continuam.
- **Salvar gráfico** grava o CSV e o PNG do trecho visível.
- **Linha de status**: avisa se faltou o zero, se o ADC saturou ou se a placa parou de mandar dados.

## Aba Curvas I–V

![Aba Curvas I–V (modo simulado)](imagens/interface_curvas_iv.png)

- Última curva I–V de cada módulo (linha cheia) e a curva de potência (tracejada, eixo da direita), com o ponto de máxima potência marcado.
- Tabela com Isc, Voc, Pmax, Vmp, Imp, fator de preenchimento, Pmax e Isc relativas ao módulo de referência e Pmax por área.
  - **Isc**: média das correntes perto do curto. Com a carga de corrente, os pontos depois do joelho se juntam em V ≈ Isc × 1 Ω, onde a curva é plana.
  - **Voc**: ponto em aberto.
  - **Pmax**: maior V × I medido.
- **Salvar curvas** grava o CSV (todos os pontos) e o PNG em `Medidas/`.

## Aba Diagnóstico

- Sensor de referência A0 em mV: mostra a deriva comum que está sendo descontada.
- Saída dos sensores de corrente em mV, já sem o offset do zero.
- Offsets em uso, intervalo médio entre leituras e número de linhas inválidas recebidas.

## Formato do CSV de gravação

Cabeçalho com linhas `#` (versão, data, fonte, amostra, observação, configuração de cada módulo, offsets do zero e as fórmulas de conversão), seguido de:

| Coluna | Unidade | Conteúdo |
|---|---|---|
| `datahora` | ISO 8601 | Horário do PC na chegada da leitura |
| `t_s` | s | Tempo desde a primeira leitura gravada |
| `Vn_mV`, `In_mA`, `Pn_mW`, `En_mWh` | | Tensão, corrente, potência e energia (desde o início da gravação) do módulo *n* |
| `A0`…`A8` | contagens | Médias brutas do ADC, na ordem dos pinos |

| `G1`…`G4`, `modo` | 0–1023, letra | PWM da carga e modo (firmware 2.x; vazios com o antigo) |

As contagens brutas permitem refazer toda a conversão depois (outra sensibilidade, outro zero).

Com o firmware 2.x, a gravação cria mais dois arquivos com o mesmo nome:

| Arquivo | Conteúdo |
|---|---|
| `…_IV.csv` | Todos os pontos de todas as varreduras: `datahora, modulo, ponto, gate, V_mV, I_mA, P_mW, ref, A_I, A_V` |
| `…_IVparametros.csv` | Uma linha por módulo e varredura: `Isc_mA, Voc_mV, Pmax_mW, Vmp_mV, Imp_mA, FF_pct, gate_mp` |
