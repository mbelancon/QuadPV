# QuadPV: quatro módulos fotovoltaicos medidos lado a lado com STM32

Sistema de baixo custo para comparar **quatro módulos fotovoltaicos pequenos e idênticos** sob as mesmas condições, por exemplo com coberturas de vidro diferentes. Um **STM32F103C8T6 (Blue Pill)** mede a tensão e a corrente de cada módulo, faz médias de cerca de 1 s e envia as leituras por USB. A interface em Python mostra potência, tensão, corrente, energia acumulada e a comparação entre os módulos em tempo real, e grava tudo em CSV.

![Interface, aba Medidas (modo simulado)](docs/imagens/interface_medidas.png)

```
            ┌──────────────── sensor de referência (sem corrente) ── A0
            │
 módulo 1 (+) ──┬──────────────────────────────────────────────── A5 (tensão)
                └── WCS2702 ── carga R ── GND        saída ─────── A1 (corrente)
 módulo 1 (−) ── GND
   ... idem para os módulos 2, 3 e 4: corrente em A2, A3, A4; tensão em A6, A7, A8
                                                     ┌────────┐
                                     A0..A8 ───────► │ STM32  │ ── USB ──► PC: QuadPV
                                                     │ F103   │
                                                     └────────┘
```

## Hardware

| Componente | Função | Ligação |
|---|---|---|
| Blue Pill STM32F103C8T6 | Aquisição (ADC de 12 bits) e envio por USB | USB ao PC |
| 5 × sensor de corrente Hall WCS2702 | Corrente de cada módulo; o quinto fica sem corrente e serve de referência | Saídas em **A0** (referência) e **A1–A4** |
| 4 × carga resistiva | Ponto de operação de cada módulo | Em série com o sensor, entre o (+) do módulo e o GND |
| 4 módulos fotovoltaicos | Objetos de medida, com tensão de circuito aberto abaixo de 3,3 V | (+) em **A5–A8**, (−) no GND |

Detalhes e cuidados em [docs/02_Hardware_e_Ligacoes.md](docs/02_Hardware_e_Ligacoes.md).

## Início rápido

1. Monte o hardware conforme [docs/02_Hardware_e_Ligacoes.md](docs/02_Hardware_e_Ligacoes.md).
2. Grave o firmware [`firmware/pv/pv.ino`](firmware/pv/pv.ino) (ver [docs/03_Firmware.md](docs/03_Firmware.md)).
3. Instale as dependências da interface (Python 3.10 ou superior; com o Miniconda, no Anaconda Prompt):
   ```bash
   pip install -r requirements.txt
   ```
4. Conecte a placa pelo USB e rode:
   ```bash
   python interface/quadpv.py
   ```
   A porta é detectada sozinha. Sem a placa, marque **Simular medidas** para testar a interface.
5. **Antes de medir, cubra os módulos e clique em "Zerar correntes…"**. Depois descubra, digite o nome da amostra e clique em **Gravar**. Os arquivos vão para a pasta `Medidas/`, ao lado do script.

## Documentação

| Documento | Conteúdo |
|---|---|
| [01_Materiais.md](docs/01_Materiais.md) | Hardware e software necessários |
| [02_Hardware_e_Ligacoes.md](docs/02_Hardware_e_Ligacoes.md) | Ligações dos sensores, módulos e cargas; limites do ADC |
| [03_Firmware.md](docs/03_Firmware.md) | O que o firmware faz, protocolo serial e gravação |
| [04_Interface.md](docs/04_Interface.md) | Instalação e funcionalidades da interface; formato do CSV |
| [05_Calibracao_e_Incertezas.md](docs/05_Calibracao_e_Incertezas.md) | Conversão das contagens, zero, calibração dos sensores, resolução e escolha da métrica de comparação |

## Estrutura do repositório

```
├── interface/
│   └── quadpv.py            Interface (PySide6/PyQt + pyqtgraph + NumPy)
├── firmware/pv/pv.ino       Firmware atual (Arduino IDE, núcleo STM32)
├── docs/                    Documentação
├── packaging/               Spec e dependências do PyInstaller (executável Windows)
├── .github/workflows/       Geração automática do executável nas Releases
├── legado/pv-stm32.py       Interface original (Tkinter), mantida só como referência
├── CHANGELOG.md             Histórico de versões
└── requirements.txt         Dependências Python
```

## Versões

O que mudou em cada versão está no [CHANGELOG.md](CHANGELOG.md). O número aparece no título da janela.
