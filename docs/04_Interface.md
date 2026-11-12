# 4. Interface no PC – QuadPV

Arquivo: [`interface/quadpv.py`](../interface/quadpv.py). PySide6 (ou PyQt) + pyqtgraph, no mesmo padrão do SOI Controller.

## Instalação e execução

```powershell
pip install -r requirements.txt
python interface/quadpv.py
```

O executável para Windows pode ser gerado como no SOI (spec em [`packaging/`](../packaging/) e workflow do GitHub Actions).

## Conexão

- **Porta automática**: a variável de ambiente `QUADPV_PORT` (ex.: `COM7`), se existir; depois um dispositivo USB com VID `0x0483` (STM32); depois outras placas conhecidas; por fim, a única porta USB presente. A lista **Porta** permite fixar uma porta, que fica salva.
- Se a conexão cair, a interface tenta reconectar a cada 3 s. A leitura serial roda numa thread própria.
- **Sem a placa**, aparece **Simular medidas**: quatro módulos com coberturas de transmitância 100 %, 97 %, 93 % e 102 %, sob um "dia" de 20 min com nuvens.
- As preferências (nomes, áreas, calibração, zero, porta, janela) ficam salvas ao fechar (no Windows, em `HKCU\Software\LCTM\QuadPV`).

## Barra superior

| Controle | Função |
|---|---|
| Amostra | Prefixo de todos os arquivos salvos em `Medidas/` |
| Porta | Automática ou uma porta fixa |
| Configurar módulos… | Nome, área (cm²), sensibilidade do sensor (mV/mA) e divisor de tensão de cada módulo; Vref do ADC; uso do sensor de referência A0; valor da carga (só para o registro) |
| Zerar correntes… | Com os módulos cobertos, mede ~5 s e guarda o offset de cada sensor. Mostra o ruído de cada sensor no zero. **Refaça se mudar a Vref ou o uso do A0** |
| Gravar / Parar | Grava cada leitura no CSV assim que chega (nada se perde se o programa fechar) |
| Observação | Texto livre que vai para o cabeçalho do CSV |
| Reiniciar gráficos | Limpa os gráficos e zera a energia acumulada exibida |
| Finalizar experimento | Compacta num ZIP os arquivos salvos na sessão |

## Aba Medidas

- **Cartões dos módulos**: potência, tensão, corrente, potência por área, energia acumulada e potência relativa ao módulo de referência. A caixa no cartão liga ou desliga a curva no gráfico. A potência fica vermelha se alguma entrada daquele módulo encostar no limite do ADC.
- **Gráfico**: potência, potência por área, tensão, corrente, energia, **potência relativa** ou **corrente relativa** (em % de um módulo escolhido). Janelas de 5 min a 24 h, ou tudo.
- **Pausar** congela o gráfico e libera zoom e arraste com o mouse; a aquisição e a gravação continuam.
- **Salvar gráfico** grava o CSV e o PNG do trecho visível.
- **Linha de status**: avisa se faltou o zero, se o ADC saturou ou se a placa parou de mandar dados.

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

As contagens brutas permitem refazer toda a conversão depois (outra sensibilidade, outro zero).
