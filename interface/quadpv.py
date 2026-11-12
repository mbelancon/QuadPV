# -*- coding: utf-8 -*-
'''
QuadPV - potência de 4 módulos fotovoltaicos idênticos, lado a lado, com STM32
(interface PySide6/PyQt + pyqtgraph, no mesmo padrão do SOI Controller).

Dependências:  pip install -r requirements.txt   (numpy, pyserial, pyqtgraph, PySide6)

O firmware envia, cerca de uma vez por segundo, uma linha de texto com as médias
(em contagens do ADC de 12 bits) dos pinos A0..A8, na ordem:

    A0 A1 A5 A2 A6 A3 A7 A4 A8
    ref I1 V1 I2 V2 I3 V3 I4 V4

  * A0 = sensor de corrente de referência (sem corrente): acompanha a deriva comum
         dos sensores Hall (alimentação, temperatura);
  * A1..A4 = sensores de corrente (WCS2702) dos módulos 1 a 4;
  * A5..A8 = tensão dos módulos 1 a 4 (0 a 3,3 V, direto no ADC).

Os valores podem vir separados por espaço, vírgula ou ponto e vírgula.
'''
__version__ = '2.0.0'      # versão da interface (ver CHANGELOG.md)

import math
import os
import sys
import threading
import time
import zipfile
from collections import deque, namedtuple
from datetime import datetime

import numpy as np
import serial
from serial.tools import list_ports

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

# Executáveis empacotados sem console (PyInstaller --windowed) não têm stdout/stderr.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

# ----------------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------------
APP_ORG, APP_NAME = 'LCTM', 'QuadPV'
BAUD_RATE = 115200          # o USB CDC do STM32 ignora; vale para adaptadores USB-serial
STM32_VID = 0x0483
KNOWN_VIDS = {0x0483, 0x1EAF, 0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}
RECONNECT_INTERVAL = 3.0    # s entre tentativas de reconexão
NO_DATA_TIMEOUT = 5.0       # s sem linha válida -> aviso "sem dados"
IDENTIFY_TIMEOUT = 2.5      # s esperando a resposta ao "?" (ou as médias do firmware antigo)
REJECT_HOLD = 30.0          # s sem tentar de novo uma porta que não é um QuadPV

N_CELLS = 4
N_RAW = 9                   # valores por linha
ADC_COUNTS = 4096.0         # 12 bits
ADC_SAT_LOW, ADC_SAT_HIGH = 10.0, 4085.0
DEFAULT_VREF_MV = 3300.0
DEFAULT_SENS = 0.9          # mV/mA (sensibilidade usada no programa original)
ZERO_FRAMES = 5             # leituras (~5 s) promediadas no zero das correntes
SIM_PERIOD = 1.0            # s entre leituras simuladas (= firmware)

GUI_INTERVAL_MS = 100
PLOT_INTERVAL = 0.5         # s entre redesenhos dos gráficos

CELL_COLORS = ('#0072B2', '#D55E00', '#009E73', '#CC79A7')   # paleta segura para daltonismo
OK_COLOR, WARN_COLOR, BAD_COLOR = '#1a7f1a', '#d07000', '#c00000'

WINDOWS = (('5 min', 300), ('15 min', 900), ('1 h', 3600), ('6 h', 21600),
           ('24 h', 86400), ('Tudo', 0))

QUANTITIES = (
    ('P', 'Potência', 'mW'),
    ('PA', 'Potência por área', 'mW/cm²'),
    ('V', 'Tensão', 'mV'),
    ('I', 'Corrente', 'mA'),
    ('E', 'Energia', 'mWh'),
    ('R', 'Potência relativa', '%'),
    ('IR', 'Corrente relativa', '%'),
    ('SPM', 'Pmax das varreduras', 'mW'),
    ('SPR', 'Pmax relativa (varreduras)', '%'),
    ('SIS', 'Isc das varreduras', 'mA'),
    ('SIR', 'Isc relativa (varreduras)', '%'),
    ('SVO', 'Voc das varreduras', 'mV'),
    ('SFF', 'Fator de preenchimento', '%'),
)
SWEEP_KEYS = {'SPM': 'pmax', 'SPR': 'pmax', 'SIS': 'isc', 'SIR': 'isc', 'SVO': 'voc', 'SFF': 'ff'}
MODES = (('P', 'Máxima potência (MPPT)'), ('F', 'Gate fixo'), ('O', 'Aberto (Voc)'), ('C', 'Curto (Isc)'))

MEDIDAS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False)
    else os.path.dirname(os.path.abspath(__file__)), 'Medidas')

Frame = namedtuple('Frame', 't raw')   # t = time.time(); raw = 9 contagens na ordem A0..A8


# ----------------------------------------------------------------------------
# Protocolo e conversão (funções puras, testáveis sem interface)
# ----------------------------------------------------------------------------
def parse_line(line):
    '''Linha do firmware -> array com as contagens de A0..A8, ou None se inválida.'''
    if isinstance(line, (bytes, bytearray)):
        line = line.decode('ascii', 'ignore')
    tokens = line.replace(',', ' ').replace(';', ' ').split()
    if len(tokens) != N_RAW:
        return None
    try:
        vals = np.array([float(t) for t in tokens])
    except ValueError:
        return None
    if not np.all(np.isfinite(vals)):
        return None
    # ordem de envio: A0 A1 A5 A2 A6 A3 A7 A4 A8  ->  A0..A8
    raw = np.empty(N_RAW)
    raw[0] = vals[0]
    raw[1:5] = vals[1::2]
    raw[5:9] = vals[2::2]
    return raw


class Config:
    '''Parâmetros de conversão e identificação dos módulos.'''

    def __init__(self):
        self.vref_mv = DEFAULT_VREF_MV
        self.use_ref = True                       # descontar o canal A0
        self.names = [f'Módulo {i + 1}' for i in range(N_CELLS)]
        self.area_cm2 = [0.0] * N_CELLS           # 0 = não informada
        self.sens = [DEFAULT_SENS] * N_CELLS      # mV/mA
        self.div = [1.0] * N_CELLS                # fator do divisor de tensão (1 = direto)
        self.load_ohm = 0.0                       # carga (só registrada no CSV)
        self.ref_cell = 0                         # módulo de referência da potência relativa
        self.auto_zero = True                     # zero pelo ponto em aberto das varreduras I-V

    def header_lines(self):
        lines = [f'Vref_ADC={self.vref_mv:g} mV; canal de referencia A0 '
                 f'{"descontado" if self.use_ref else "ignorado"}; carga={self.load_ohm:g} ohm; '
                 f'zero automatico pelas varreduras: {"sim" if self.auto_zero else "nao"}']
        for i in range(N_CELLS):
            lines.append(f'Modulo {i + 1}: nome="{self.names[i]}", area={self.area_cm2[i]:g} cm2, '
                         f'sensibilidade={self.sens[i]:g} mV/mA, divisor={self.div[i]:g}')
        return lines


def current_diff_mv(raw, vref_mv):
    '''Saída dos sensores de corrente em mV: (absoluta, descontado o canal A0).'''
    k = vref_mv / ADC_COUNTS
    absolute = raw[1:5] * k
    return absolute, absolute - raw[0] * k


def to_physical(raw, cfg, offsets):
    '''Contagens -> (V em mV, I em mA, P em mW). offsets = dict com 'abs' e 'ref' (mV).'''
    k = cfg.vref_mv / ADC_COUNTS
    d_abs, d_ref = current_diff_mv(raw, cfg.vref_mv)
    if cfg.use_ref:
        d, off = d_ref, offsets['ref']
    else:
        d, off = d_abs, offsets['abs']
    i_ma = (d - off) / np.asarray(cfg.sens)
    v_mv = raw[5:9] * k * np.asarray(cfg.div)
    p_mw = v_mv * i_ma / 1000.0
    return v_mv, i_ma, p_mw


# ----------------------------------------------------------------------------
# Protocolo do firmware 2.x (o antigo só manda a linha de médias)
# ----------------------------------------------------------------------------
Msg = namedtuple('Msg', 'kind t data')
# kind 'D': médias do segundo (raw A0..A8)          data = array(9)
#      'G': gates e modo da carga                    data = (array(4) de 0..1023, modo)
#      'IV': curva I-V de um módulo                  data = (índice 0..3, array n x 4: ref, I, V, gate)
#      '#': resposta/informação do firmware          data = texto


def parse_message(line, t):
    '''Uma linha recebida -> Msg, ou None se não for reconhecida.'''
    if isinstance(line, (bytes, bytearray)):
        line = line.decode('ascii', 'ignore')
    s = line.strip()
    if not s:
        return None
    if s.startswith('#'):
        return Msg('#', t, s[1:].strip())
    if s.startswith('IV'):
        tok = s.split()
        try:
            m, n = int(tok[1]) - 1, int(tok[2])
            vals = np.array(tok[3:], dtype=float)
        except (ValueError, IndexError):
            return None
        if not 0 <= m < N_CELLS or n < 3 or vals.size != 4 * n or not np.all(np.isfinite(vals)):
            return None
        return Msg('IV', t, (m, vals.reshape(n, 4)))
    if s.startswith('G'):
        tok = s.split()
        if len(tok) != 6:
            return None
        try:
            duties = np.array([int(x) for x in tok[1:5]])
        except ValueError:
            return None
        return Msg('G', t, (duties, tok[5]))
    raw = parse_line(s)
    return None if raw is None else Msg('D', t, raw)


def fw_info(text):
    '''"QuadPV fw 2.0.0 modo=P gate=0 T=60 NF=100" -> dict, ou None se não for do QuadPV.'''
    tok = text.split()
    if len(tok) < 3 or tok[0] != 'QuadPV' or tok[1] != 'fw':
        return None
    info = {'version': tok[2]}
    for t in tok[3:]:
        if '=' in t:
            k, v = t.split('=', 1)
            info[k] = v
    return info


# ----------------------------------------------------------------------------
# Serial em thread
# ----------------------------------------------------------------------------
def candidate_ports(preferred=None):
    '''Portas a testar, em ordem: a escolhida pelo usuário (só ela, se escolhida), a variável
    QUADPV_PORT, dispositivos USB do STM32 (VID 0x0483), outras placas conhecidas e, se houver
    uma só porta USB, ela. Igual ao SOI; a identidade é confirmada depois com o comando "?".'''
    ports = list(list_ports.comports())
    names = {p.device.upper(): p.device for p in ports}
    if preferred:
        return [names[preferred.upper()]] if preferred.upper() in names else []
    out = []

    def add(dev):
        if dev and dev not in out:
            out.append(dev)

    env = os.environ.get('QUADPV_PORT')
    if env and env.upper() in names:
        add(names[env.upper()])
    usb = [p for p in ports if p.vid is not None]
    for p in usb:                                   # nome do produto (Linux/macOS mostram "QuadPV")
        if 'quadpv' in ' '.join(filter(None, [p.product, p.description])).lower():
            add(p.device)
    for vids in ({STM32_VID}, KNOWN_VIDS):
        for p in usb:
            if p.vid in vids:
                add(p.device)
    for p in usb:
        ident = ' '.join(filter(None, [p.description, p.manufacturer])).lower()
        if any(k in ident for k in ('stm32', 'stmicro', 'maple', 'arduino')):
            add(p.device)
    if len(usb) == 1:
        add(usb[0].device)
    return out


def find_port(preferred=None):
    c = candidate_ports(preferred)
    return c[0] if c else None


class SerialLink(threading.Thread):
    '''Procura a placa, confirma que é um QuadPV, lê as linhas e reconecta sozinha.
    A GUI chama take() para pegar as mensagens e write() para mandar comandos.'''

    def __init__(self):
        super().__init__(daemon=True)
        self.ser = None
        self.connected = False
        self.port = None
        self.preferred = None          # None = automática
        self.last_error = None
        self.bad_lines = 0
        self.fw = None                 # dict do "?" (firmware 2.x) ou None (firmware antigo)
        self.identity = None           # 'QuadPV', 'antigo' ou 'não confirmada'
        self._rejected = {}            # porta -> até quando não tentar de novo (outra placa)
        self._msgs = deque(maxlen=20000)
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._stop_evt = threading.Event()
        self._reconnect_evt = threading.Event()

    # -- API para a GUI --
    def take(self):
        with self._lock:
            msgs = list(self._msgs)
            self._msgs.clear()
        return msgs

    def write(self, text):
        with self._wlock:
            ser = self.ser
            if ser is None:
                return False
            try:
                ser.write((text.strip() + '\n').encode('ascii'))
                return True
            except Exception as e:
                self.last_error = f'Erro ao escrever: {e}'
                self._close_locked()
                return False

    def set_preferred(self, port):
        self.preferred = port or None
        self._rejected.clear()
        self._reconnect_evt.set()

    def stop(self):
        self._stop_evt.set()
        self.join(timeout=3.0)
        with self._wlock:
            self._close_locked()

    # -- internos --
    def _close_locked(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.connected = False

    def _feed(self, data):
        '''Separa as linhas e devolve as mensagens reconhecidas.'''
        self._buf.extend(data)
        if len(self._buf) > 100_000 and b'\n' not in self._buf[-100_000:]:
            self._buf.clear()                       # lixo binário sem quebra de linha
        msgs = []
        while True:
            nl = self._buf.find(b'\n')
            if nl < 0:
                break
            line = bytes(self._buf[:nl])
            del self._buf[:nl + 1]
            msg = parse_message(line, time.time())
            if msg is None:
                if line.strip():
                    self.bad_lines += 1
                continue
            msgs.append(msg)
        return msgs

    def _identify(self, ser, port):
        '''Manda "?" e espera a resposta do QuadPV ou uma linha de médias do firmware antigo.'''
        ser.write(b'?\n')
        t_end = time.monotonic() + IDENTIFY_TIMEOUT
        early = []
        fw, legacy = None, False
        while time.monotonic() < t_end and not self._stop_evt.is_set():
            data = ser.read(max(1, ser.in_waiting))
            if not data:
                continue
            for msg in self._feed(data):
                early.append(msg)
                if msg.kind == '#' and fw_info(msg.data):
                    fw = fw_info(msg.data)
                elif msg.kind in ('D', 'G', 'IV'):
                    legacy = True
            if fw is not None:
                break
            if legacy and time.monotonic() > t_end - IDENTIFY_TIMEOUT + 1.2:
                break                               # médias chegando e nenhuma resposta ao "?"
        if fw is not None:
            return 'QuadPV', fw, early
        if legacy:
            return 'antigo', None, early
        return None, None, early

    def _try_connect(self):
        now = time.monotonic()
        cands = candidate_ports(self.preferred)
        skipped = []
        if not self.preferred:
            skipped = [p for p in cands if self._rejected.get(p, 0) > now]
            cands = [p for p in cands if p not in skipped]
        if not cands:
            if self.preferred:
                self.last_error = f'Porta {self.preferred} não encontrada.'
            elif skipped:
                self.last_error = (f'Placa(s) em {", ".join(skipped)} não responderam como QuadPV '
                                   '(outra placa, como o SOI?). Procurando de novo…')
            else:
                self.last_error = 'Nenhuma placa QuadPV encontrada (procurando STM32, USB VID 0x0483).'
            return
        for port in cands:
            if self._stop_evt.is_set():
                return
            ser = None
            try:
                ser = serial.Serial(port, BAUD_RATE, timeout=0.1)
                if self._stop_evt.wait(0.3):
                    ser.close()
                    return
                ser.reset_input_buffer()
                self._buf.clear()
                identity, fw, early = self._identify(ser, port)
                if identity is None and not self.preferred:
                    ser.close()
                    self._rejected[port] = time.monotonic() + REJECT_HOLD
                    self.last_error = f'{port} não respondeu como QuadPV (outra placa?)'
                    print(self.last_error)
                    continue
                with self._wlock:
                    self.ser, self.port, self.connected = ser, port, True
                    self.fw, self.identity = fw, identity or 'não confirmada'
                    self.last_error = None
                with self._lock:
                    self._msgs.extend(early)
                print(f'Conectado a {port} ({self.identity})')
                return
            except Exception as e:
                self.last_error = f'{port}: {e}'
                print(f'Erro ao abrir {port}: {e}')
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass

    def run(self):
        last_try = 0.0
        while not self._stop_evt.is_set():
            if self._reconnect_evt.is_set():
                self._reconnect_evt.clear()
                with self._wlock:
                    self._close_locked()
                last_try = 0.0
            if self.ser is None:
                now = time.monotonic()
                if now - last_try >= RECONNECT_INTERVAL:
                    self._try_connect()
                    last_try = time.monotonic()
                else:
                    self._stop_evt.wait(0.1)
                continue
            try:
                ser = self.ser
                data = ser.read(max(1, ser.in_waiting))
            except Exception as e:
                print(f'Conexão perdida: {e}')
                self.last_error = str(e)
                with self._wlock:
                    self._close_locked()
                continue
            if data:
                msgs = self._feed(data)
                for msg in msgs:
                    if msg.kind == '#' and fw_info(msg.data):
                        self.fw = fw_info(msg.data)
                        self.identity = 'QuadPV'
                if msgs:
                    with self._lock:
                        self._msgs.extend(msgs)


# ----------------------------------------------------------------------------
# Simulação (sem STM32): imita o firmware 2.x
# ----------------------------------------------------------------------------
class Simulator:
    '''Quatro módulos pequenos (4 células em série, Voc ~2,4 V) sob irradiância que varia devagar
    e com nuvens. Os módulos diferem só na transmitância da cobertura (FACTORS). Cada módulo tem
    a carga eletrônica do firmware 2.x (fonte de corrente com op-amp, ~0,32 mA por contagem do
    PWM): o simulador responde aos mesmos comandos, rastreia o ponto de máxima potência e manda
    as mesmas linhas (médias, gates e curvas I-V), em contagens do ADC.'''

    FACTORS = (1.00, 0.97, 0.93, 1.02)
    ISC_MA = 120.0          # corrente de curto-circuito a 1000 W/m²
    VOC_MV = 2400.0
    N_VT_MV = 4 * 1.3 * 25.7
    MA_PER_COUNT = 3300.0 / 1023.0 / 10.0      # referência / 10 sobre 1 ohm
    R_ON = 1.05                                # ohm: Rs + Rds(on) com o MOSFET saturado
    SENS = DEFAULT_SENS
    OFFSETS_MV = (6.0, -4.0, 11.0, -2.0)       # offsets individuais dos sensores Hall
    NF = 100

    def __init__(self, seed=None):
        self.rng = np.random.default_rng(seed)
        self.reset()

    def reset(self):
        self.t0 = time.time()
        self.cloud = 0.0
        self.covered = False
        self.mode, self.fixed = 'P', 0
        self.interval = 60
        self.duty = np.zeros(N_CELLS, dtype=int)
        self.next_sec = 0.0
        self.next_sweep = self.t0 + 3.0
        self.sweep_now = False
        self.pending = []

    # -- física --
    def irradiance(self, t):
        x = (t - self.t0) / 1200.0                     # "dia" de 20 min para testes
        clear = 0.15 + 0.85 * math.sin(math.pi * x) ** 2
        if self.cloud < 0.02 and self.rng.random() < 0.006:
            self.cloud = self.rng.uniform(0.3, 0.7)        # nuvem passa: queda rápida...
        self.cloud *= 0.85                                 # ...e volta em ~15 s
        return 1000.0 * clear * (1.0 - self.cloud)

    def _i0(self):
        return self.ISC_MA / math.exp(self.VOC_MV / self.N_VT_MV)

    def operating_point(self, isc_ma, i_set=None, r_load=None):
        '''(V mV, I mA) do módulo com uma fonte de corrente i_set (limitada por R_ON) ou um resistor.'''
        if isc_ma <= 1e-6:
            return 0.0, 0.0
        i0 = self._i0()
        lo, hi = 0.0, self.N_VT_MV * math.log(isc_ma / i0 + 1.0)
        for _ in range(50):
            v = 0.5 * (lo + hi)
            i_pv = isc_ma - i0 * (math.exp(v / self.N_VT_MV) - 1.0)
            i_load = v / r_load if r_load else min(i_set, v / self.R_ON)
            if i_pv > i_load:
                lo = v
            else:
                hi = v
        v = 0.5 * (lo + hi)
        return v, (v / r_load if r_load else min(i_set, v / self.R_ON))

    def mpp_duty(self, isc_ma):
        if isc_ma <= 1e-6:
            return 0
        i0 = self._i0()
        i = np.linspace(0, isc_ma * 0.999, 400)
        v = self.N_VT_MV * np.log((isc_ma - i) / i0 + 1.0)
        return int(round(i[np.argmax(v * i)] / self.MA_PER_COUNT))

    def _raw(self, v_i, drift_mv):
        k = DEFAULT_VREF_MV / ADC_COUNTS
        raw = np.empty(N_RAW)
        raw[0] = drift_mv / k
        for c, (v_mv, i_ma) in enumerate(v_i):
            raw[1 + c] = (drift_mv + self.OFFSETS_MV[c] + self.SENS * i_ma) / k
            raw[5 + c] = v_mv / k
        return raw

    # -- firmware imitado --
    def command(self, text):
        s = text.strip()
        if s == '?':
            self.pending.append(self._info())
        elif s[:1].upper() == 'S':
            self.sweep_now = True
            self.pending.append('ok varredura')
        elif s[:1].upper() == 'T':
            try:
                self.interval = max(0, min(86400, int(s[1:])))
                self.next_sweep = time.time() + self.interval if self.interval else float('inf')
            except ValueError:
                pass
            self.pending.append(self._info())
        elif s[:1].upper() == 'M':
            arg = s[1:].split()
            if arg and arg[0].upper() in 'POCF':
                self.mode = arg[0].upper()
                if self.mode == 'F' and len(arg) > 1:
                    self.fixed = max(0, min(1023, int(arg[1])))
            self.pending.append(self._info())

    def _info(self):
        return f'QuadPV fw {__version__} modo={self.mode} gate={self.fixed} T={self.interval} NF={self.NF} (simulado)'

    def _duties(self, iscs):
        if self.mode == 'O':
            return np.zeros(N_CELLS, dtype=int)
        if self.mode == 'C':
            return np.full(N_CELLS, 1023)
        if self.mode == 'F':
            return np.full(N_CELLS, self.fixed)
        return np.array([self.mpp_duty(i) + int(self.rng.integers(-2, 3)) for i in iscs])

    def poll(self, t):
        '''Mensagens que o firmware teria mandado até o instante t.'''
        msgs = [Msg('#', t, p) for p in self.pending]
        self.pending = []
        if t < self.next_sec:
            return msgs
        self.next_sec = t + SIM_PERIOD
        g = 0.0 if self.covered else self.irradiance(t)
        iscs = [self.ISC_MA * g / 1000.0 * f for f in self.FACTORS]
        drift = 1650.0 + 8.0 * math.sin((t - self.t0) / 97.0)       # deriva comum dos Hall
        self.duty = self._duties(iscs)
        vi = [self.operating_point(isc, i_set=d * self.MA_PER_COUNT) for isc, d in zip(iscs, self.duty)]
        raw = self._raw(vi, drift) + self.rng.normal(0.0, 0.05, N_RAW)   # ~16 mil médias por segundo
        msgs.append(Msg('D', t, np.clip(raw, 0.0, ADC_COUNTS - 1)))
        msgs.append(Msg('G', t, (self.duty.copy(), self.mode)))
        if self.sweep_now or t >= self.next_sweep:
            self.sweep_now = False
            self.next_sweep = t + self.interval if self.interval else float('inf')
            for m, isc in enumerate(iscs):
                hi = min(1023, int(1.15 * isc / self.MA_PER_COUNT) + 32) if isc > 2 else 1023
                ds = np.concatenate(([0], np.linspace(0, hi, self.NF).astype(int), [1023]))
                pts = []
                for d in ds:
                    v, i = self.operating_point(isc, i_set=d * self.MA_PER_COUNT)
                    r = self._raw([(v, i)] * N_CELLS, drift) + self.rng.normal(0.0, 0.3, N_RAW)
                    pts.append((r[0], r[1 + m], r[5 + m], d))
                msgs.append(Msg('IV', t, (m, np.array(pts))))
        return msgs

    def frame(self, t):
        '''Só as contagens de um instante (testes): carga no MPPT.'''
        for msg in self.poll(t):
            if msg.kind == 'D':
                return msg.data
        g = 0.0 if self.covered else self.irradiance(t)
        iscs = [self.ISC_MA * g / 1000.0 * f for f in self.FACTORS]
        vi = [self.operating_point(isc, i_set=self.mpp_duty(isc) * self.MA_PER_COUNT) for isc in iscs]
        return self._raw(vi, 1650.0)


# ----------------------------------------------------------------------------
# Curvas I-V
# ----------------------------------------------------------------------------
def iv_offsets(pts, vref_mv):
    '''Offset do sensor (mV) no 1º ponto da varredura (gate 0: MOSFET aberto, corrente nula).'''
    k = vref_mv / ADC_COUNTS
    return (pts[0, 1] - pts[0, 0]) * k, pts[0, 1] * k


def analyze_iv(pts, m, cfg, offsets):
    '''Pontos (ref, I, V, gate em contagens) -> curva em mV/mA e parâmetros do módulo m.'''
    k = cfg.vref_mv / ADC_COUNTS
    if cfg.use_ref:
        i = ((pts[:, 1] - pts[:, 0]) * k - offsets['ref'][m]) / cfg.sens[m]
    else:
        i = (pts[:, 1] * k - offsets['abs'][m]) / cfg.sens[m]
    v = pts[:, 2] * k * cfg.div[m]
    order = np.argsort(v)
    v, i, gate = v[order], i[order], pts[order, 3]
    low_v = v < 0.5 * v.max()
    if np.any(low_v) and np.nanmedian(i[low_v]) < 0:
        i = -i                                    # sensor montado ao contrário
    p = v * i / 1000.0
    voc = float(v.max())
    # Isc: perto do curto a curva é plana. Com a carga de corrente os pontos depois do joelho se
    # juntam em V = I x 1 ohm (~0,1 V); extrapolar uma reta por esse aglomerado amplifica o ruído,
    # então só se extrapola quando os pontos baixos cobrem uma faixa razoável de tensão.
    low = v <= 0.2 * voc
    if np.count_nonzero(low) >= 3 and np.ptp(v[low]) > 0.1 * voc:
        isc = float(np.polyfit(v[low], i[low], 1)[1])
    elif np.any(low):
        isc = float(np.median(i[low]))
    else:
        isc = float(i[np.argmin(v)])
    k_max = int(np.argmax(p))
    pmax = float(p[k_max])
    ff = 100.0 * pmax / (voc * isc / 1000.0) if voc > 0 and isc > 0 else float('nan')
    return {'v': v, 'i': i, 'p': p, 'gate': gate, 'isc': isc, 'voc': voc, 'pmax': pmax,
            'vmp': float(v[k_max]), 'imp': float(i[k_max]), 'gmp': float(gate[k_max]), 'ff': ff}


class SweepHistory:
    '''Parâmetros de cada varredura (uma linha por varredura, uma coluna por módulo).'''

    def __init__(self):
        self.clear()

    def clear(self):
        self.t = []
        self.vals = {k: [] for k in ('isc', 'voc', 'pmax', 'ff')}
        self._filled = set()

    def add(self, t, m, res):
        if not self.t or m in self._filled or t - self.t[-1] > 5.0:
            self.t.append(t)
            for k in self.vals:
                self.vals[k].append(np.full(N_CELLS, np.nan))
            self._filled = set()
        for k in self.vals:
            self.vals[k][-1][m] = res[k]
        self._filled.add(m)

    def series(self, key, ref_cell):
        if not self.t:
            return np.empty(0), np.empty((0, N_CELLS))
        y = np.array(self.vals[SWEEP_KEYS[key]])
        if key in ('SPR', 'SIR'):
            ref = y[:, ref_cell:ref_cell + 1]
            with np.errstate(divide='ignore', invalid='ignore'):
                y = np.where(np.abs(ref) > 1e-6, 100.0 * y / ref, np.nan)
        return np.array(self.t), y


# ----------------------------------------------------------------------------
# Armazenamento das medidas
# ----------------------------------------------------------------------------
class DataStore:
    '''Arrays que crescem por blocos: tempo, V, I, P e energia por módulo, e as contagens brutas.'''

    def __init__(self):
        self.clear()

    def clear(self):
        self.n = 0
        cap = 4096
        self.t = np.empty(cap)
        self.v = np.empty((cap, N_CELLS))
        self.i = np.empty((cap, N_CELLS))
        self.p = np.empty((cap, N_CELLS))
        self.e = np.empty((cap, N_CELLS))
        self.raw = np.empty((cap, N_RAW))
        self.energy = np.zeros(N_CELLS)

    def _grow(self):
        for name in ('t', 'v', 'i', 'p', 'e', 'raw'):
            a = getattr(self, name)
            b = np.empty((a.shape[0] * 2,) + a.shape[1:])
            b[:self.n] = a[:self.n]
            setattr(self, name, b)

    def append(self, t, v, i, p, raw):
        if self.n == self.t.shape[0]:
            self._grow()
        if self.n:
            dt = t - self.t[self.n - 1]
            if 0 < dt < 30:          # buracos longos (desconexão) não entram na integral
                self.energy += 0.5 * (p + self.p[self.n - 1]) * dt / 3600.0
        n = self.n
        self.t[n], self.v[n], self.i[n], self.p[n], self.e[n], self.raw[n] = t, v, i, p, self.energy, raw
        self.n += 1

    def series(self, key, cfg):
        '''(t, matriz n x 4) da grandeza pedida.'''
        n = self.n
        t = self.t[:n]
        if key == 'V':
            return t, self.v[:n]
        if key == 'I':
            return t, self.i[:n]
        if key == 'E':
            return t, self.e[:n]
        p = self.p[:n]
        if key == 'PA':
            area = np.array([a if a > 0 else np.nan for a in cfg.area_cm2])
            return t, p / area
        if key in ('R', 'IR'):
            # IR: com carga fixa e pouca luz o módulo fica perto do curto-circuito e a razão das
            # correntes acompanha a razão das transmitâncias; a de potências exagera (P = R·I²).
            y, lim = (p, 0.05) if key == 'R' else (self.i[:n], 0.5)
            ref = y[:, cfg.ref_cell:cfg.ref_cell + 1]
            with np.errstate(divide='ignore', invalid='ignore'):
                r = np.where(np.abs(ref) > lim, 100.0 * y / ref, np.nan)
            return t, r
        return t, p


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------
class RefreshComboBox(QtWidgets.QComboBox):
    '''QComboBox que atualiza a lista logo antes de abrir.'''

    def __init__(self, refresh):
        super().__init__()
        self._refresh = refresh

    def showPopup(self):
        self._refresh()
        super().showPopup()


class CellCard(QtWidgets.QFrame):
    '''Cartão com as leituras atuais de um módulo.'''

    def __init__(self, idx, color):
        super().__init__()
        self.idx = idx
        self.setObjectName('card')
        self.setStyleSheet(f'#card{{border:1px solid #c8c8c8;border-left:7px solid {color};'
                           f'border-radius:6px;}}')
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)
        self.chk = QtWidgets.QCheckBox()
        self.chk.setChecked(True)
        self.chk.setToolTip('Mostrar este módulo no gráfico')
        head = QtWidgets.QHBoxLayout()
        self.lbl_name = QtWidgets.QLabel()
        self.lbl_name.setStyleSheet(f'font-weight:bold;color:{color};')
        head.addWidget(self.chk)
        head.addWidget(self.lbl_name, 1)
        lay.addLayout(head)
        self.lbl_p = QtWidgets.QLabel('-')
        f = self.lbl_p.font()
        f.setPointSize(f.pointSize() + 10)
        f.setBold(True)
        self.lbl_p.setFont(f)
        lay.addWidget(self.lbl_p)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(0)
        self.vals = {}
        for row, (key, label) in enumerate((('V', 'Tensão'), ('I', 'Corrente'), ('PA', 'P/área'),
                                            ('E', 'Energia'), ('R', 'Relativa'))):
            name = QtWidgets.QLabel(label)
            name.setStyleSheet('color:#666;')
            val = QtWidgets.QLabel('-')
            val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            grid.addWidget(name, row, 0)
            grid.addWidget(val, row, 1)
            self.vals[key] = val
        lay.addLayout(grid)

    def set_name(self, name):
        self.lbl_name.setText(name)

    def show_values(self, v, i, p, pa, e, r, warn=False):
        self.lbl_p.setText(f'{p:.1f} mW')
        self.lbl_p.setStyleSheet(f'color:{BAD_COLOR};' if warn else '')
        self.vals['V'].setText(f'{v:.0f} mV')
        self.vals['I'].setText(f'{i:.1f} mA')
        self.vals['PA'].setText('-' if pa is None or not np.isfinite(pa) else f'{pa:.3f} mW/cm²')
        self.vals['E'].setText(f'{e:.3f} mWh')
        self.vals['R'].setText('-' if r is None or not np.isfinite(r) else f'{r:.1f} %')

    def clear(self):
        self.lbl_p.setText('-')
        for v in self.vals.values():
            v.setText('-')


class ConfigDialog(QtWidgets.QDialog):
    '''Nome, área, sensibilidade e divisor de cada módulo; Vref, canal A0 e carga.'''

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configurar módulos e calibração')
        lay = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(N_CELLS, 4)
        self.table.setHorizontalHeaderLabels(['Nome', 'Área (cm²)', 'Sensib. (mV/mA)', 'Divisor de V'])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setVerticalHeaderLabels([str(i + 1) for i in range(N_CELLS)])
        self.spins = []
        for r in range(N_CELLS):
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(cfg.names[r]))
            row = []
            for c, (val, lo, hi, dec) in enumerate(((cfg.area_cm2[r], 0, 10000, 2),
                                                     (cfg.sens[r], 0.001, 1000, 4),
                                                     (cfg.div[r], 0.01, 1000, 4)), start=1):
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(lo, hi)
                sp.setDecimals(dec)
                sp.setValue(val)
                if c == 1:
                    sp.setSpecialValueText('não informada')
                self.table.setCellWidget(r, c, sp)
                row.append(sp)
            self.spins.append(row)
        self.table.setMinimumWidth(620)
        self.table.setMinimumHeight(170)
        lay.addWidget(self.table)

        form = QtWidgets.QFormLayout()
        self.sp_vref = QtWidgets.QDoubleSpinBox()
        self.sp_vref.setRange(1000, 5000)
        self.sp_vref.setDecimals(1)
        self.sp_vref.setSuffix(' mV')
        self.sp_vref.setValue(cfg.vref_mv)
        self.sp_vref.setToolTip('Tensão de referência do ADC (3,3 V na Blue Pill). Meça o pino 3V3 '
                                'com um multímetro para maior exatidão.')
        form.addRow('Referência do ADC:', self.sp_vref)
        self.chk_ref = QtWidgets.QCheckBox('Descontar o sensor de referência (A0)')
        self.chk_ref.setChecked(cfg.use_ref)
        self.chk_ref.setToolTip('Subtrai de cada sensor de corrente a leitura do sensor A0, que não tem '
                                'corrente.\nCompensa a deriva comum (alimentação, temperatura). '
                                'Mudar esta opção exige refazer o zero.')
        form.addRow('', self.chk_ref)
        self.chk_auto = QtWidgets.QCheckBox('Zero automático pelas varreduras I–V (firmware 2.x)')
        self.chk_auto.setChecked(cfg.auto_zero)
        self.chk_auto.setToolTip('O 1º ponto de cada varredura é tomado com o MOSFET aberto (corrente nula):\n'
                                 'a leitura de cada sensor nesse ponto vira o offset, sem cobrir os módulos.')
        form.addRow('', self.chk_auto)
        self.sp_load = QtWidgets.QDoubleSpinBox()
        self.sp_load.setRange(0, 100000)
        self.sp_load.setDecimals(2)
        self.sp_load.setSuffix(' Ω')
        self.sp_load.setSpecialValueText('não informada')
        self.sp_load.setValue(cfg.load_ohm)
        self.sp_load.setToolTip('Só para registro no cabeçalho do CSV.')
        form.addRow('Carga:', self.sp_load)
        lay.addLayout(form)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply_to(self, cfg):
        for r in range(N_CELLS):
            item = self.table.item(r, 0)
            cfg.names[r] = (item.text().strip() if item else '') or f'Módulo {r + 1}'
            cfg.area_cm2[r] = self.spins[r][0].value()
            cfg.sens[r] = self.spins[r][1].value()
            cfg.div[r] = self.spins[r][2].value()
        cfg.vref_mv = self.sp_vref.value()
        cfg.use_ref = self.chk_ref.isChecked()
        cfg.auto_zero = self.chk_auto.isChecked()
        cfg.load_ohm = self.sp_load.value()


# ----------------------------------------------------------------------------
# Janela principal
# ----------------------------------------------------------------------------
class QuadPVWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'QuadPV {__version__} - LCTM')
        self.resize(1200, 820)

        self.cfg = Config()
        self.offsets = {'abs': np.zeros(N_CELLS), 'ref': np.zeros(N_CELLS)}
        self.zero_time = None          # quando o zero das correntes foi feito (texto)
        self.zero_vref = None          # Vref e modo usados no zero (refazer se mudarem)
        self.zero_use_ref = None
        self.zero_acc = None           # leituras acumuladas durante o zero

        self.data = DataStore()
        self.last_frame_t = None
        self.last_rx = time.monotonic()
        self.saturated = False
        self.paused = False
        self.last_plot = 0.0
        self.dirty = False

        self.rec_file = None
        self.rec_path = None
        self.rec_lines = 0
        self.session_files = []

        self.fw_info = None
        self.last_raw_t = None
        self.duties = None             # gates atuais (firmware 2.x)
        self.fw_mode = None
        self.iv_last = [None] * N_CELLS
        self.iv_hist = SweepHistory()
        self.iv_files = None           # (curvas, parâmetros) durante a gravação
        self.iv_dirty = False
        self.synced_port = None        # porta para a qual já mandamos o modo e o intervalo

        self.link = SerialLink()
        self.sim = Simulator()
        self.simulating = False

        self._build_ui()
        self.load_settings()
        self.link.start()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(GUI_INTERVAL_MS)

    # ------------------------------------------------------------------ UI
    @staticmethod
    def _bold(text):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet('font-weight:bold;')
        return lbl

    def _build_ui(self):
        pg.setConfigOptions(antialias=True, background='w', foreground='k')
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # Linha 1: amostra, porta, simulação, configuração
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self._bold('Amostra:'))
        self.ed_sample = QtWidgets.QLineEdit()
        self.ed_sample.setPlaceholderText('nome do experimento')
        self.ed_sample.setFixedWidth(180)
        self.ed_sample.setToolTip('Todos os arquivos salvos na pasta Medidas começam com este nome.')
        top.addWidget(self.ed_sample)
        top.addSpacing(10)
        top.addWidget(self._bold('Porta:'))
        self.cmb_port = RefreshComboBox(self.refresh_ports)
        self.cmb_port.setFixedWidth(170)
        self.cmb_port.setToolTip('Automática: procura o STM32 (USB VID 0x0483).')
        self.refresh_ports()
        self.cmb_port.currentIndexChanged.connect(self.on_port)
        top.addWidget(self.cmb_port)
        top.addSpacing(10)
        self.chk_sim = QtWidgets.QCheckBox('Simular medidas')
        self.chk_sim.setStyleSheet('QCheckBox{font-weight:bold;color:#006400;}')
        self.chk_sim.setToolTip('Gera leituras simuladas de 4 módulos quando não há STM32 conectado.')
        self.chk_sim.toggled.connect(self.on_simulate)
        top.addWidget(self.chk_sim)
        top.addStretch(1)
        self.btn_cfg = QtWidgets.QPushButton('Configurar módulos…')
        self.btn_cfg.setToolTip('Nomes, áreas, sensibilidade dos sensores, divisor de tensão, Vref do ADC.')
        self.btn_cfg.clicked.connect(self.open_config)
        top.addWidget(self.btn_cfg)
        self.btn_zero = QtWidgets.QPushButton('Zerar correntes…')
        self.btn_zero.setToolTip('Mede o offset dos sensores de corrente com os módulos no escuro.')
        self.btn_zero.clicked.connect(self.start_zero)
        top.addWidget(self.btn_zero)
        root.addLayout(top)

        # Linha 2: gravação
        rec = QtWidgets.QHBoxLayout()
        self.btn_rec = QtWidgets.QPushButton('●  Gravar')
        self.btn_rec.setCheckable(True)
        self.btn_rec.setMinimumWidth(110)
        self.btn_rec.setStyleSheet(
            'QPushButton{font-weight:bold;padding:4px 14px;border:1px solid #a33;border-radius:4px;}'
            'QPushButton:checked{background:#c62828;color:white;}')
        self.btn_rec.setToolTip('Grava cada leitura no CSV assim que chega (nada se perde se o programa '
                                'fechar).\nO arquivo fica em Medidas/<amostra>_QuadPV_<data>.csv.')
        self.btn_rec.toggled.connect(self.on_record)
        rec.addWidget(self.btn_rec)
        self.lbl_rec = QtWidgets.QLabel('Não gravando')
        self.lbl_rec.setStyleSheet('color:#666;')
        rec.addWidget(self.lbl_rec)
        rec.addSpacing(10)
        rec.addWidget(QtWidgets.QLabel('Observação:'))
        self.ed_note = QtWidgets.QLineEdit()
        self.ed_note.setPlaceholderText('vai no cabeçalho do CSV (céu, orientação, coberturas…)')
        rec.addWidget(self.ed_note, 1)
        self.btn_reset = QtWidgets.QPushButton('Reiniciar gráficos')
        self.btn_reset.setToolTip('Limpa os gráficos e zera a energia acumulada (não afeta o CSV em gravação).')
        self.btn_reset.clicked.connect(self.reset_data)
        rec.addWidget(self.btn_reset)
        self.btn_finish = QtWidgets.QPushButton('Finalizar experimento')
        self.btn_finish.setStyleSheet('QPushButton{font-weight:bold;padding:3px 12px;background:#d4edda;'
                                      'border:1px solid #2e7d32;border-radius:4px;}'
                                      'QPushButton:hover{background:#b8e0c2;}')
        self.btn_finish.setToolTip('Compacta num ZIP os arquivos salvos desde que a interface abriu '
                                   '(ou desde o último "Finalizar experimento").')
        self.btn_finish.clicked.connect(self.finish_experiment)
        rec.addWidget(self.btn_finish)
        root.addLayout(rec)

        # Linha 3: carga eletrônica (firmware 2.x)
        load = QtWidgets.QHBoxLayout()
        load.addWidget(self._bold('Carga:'))
        self.cmb_mode = QtWidgets.QComboBox()
        for key, name in MODES:
            self.cmb_mode.addItem(name, key)
        self.cmb_mode.setToolTip('O que a carga eletrônica (MOSFET) faz entre as varreduras.')
        self.cmb_mode.currentIndexChanged.connect(self.on_mode)
        load.addWidget(self.cmb_mode)
        self.sp_gate = QtWidgets.QSpinBox()
        self.sp_gate.setRange(0, 1023)
        self.sp_gate.setPrefix('gate ')
        self.sp_gate.setToolTip('Gate fixo, 0 a 1023 (~0,32 mA por passo com o op-amp e 1 Ω).')
        self.sp_gate.setKeyboardTracking(False)
        self.sp_gate.valueChanged.connect(self.on_mode)
        load.addWidget(self.sp_gate)
        load.addSpacing(12)
        load.addWidget(QtWidgets.QLabel('Varredura I–V a cada'))
        self.sp_interval = QtWidgets.QSpinBox()
        self.sp_interval.setRange(0, 86400)
        self.sp_interval.setValue(60)
        self.sp_interval.setSuffix(' s')
        self.sp_interval.setSpecialValueText('só manual')
        self.sp_interval.setKeyboardTracking(False)
        self.sp_interval.setToolTip('Intervalo entre varreduras automáticas (0 = só no botão).\n'
                                    'O firmware também varre sozinho quando uma nuvem muda a potência > 25 % em 1 s.')
        self.sp_interval.valueChanged.connect(self.on_interval)
        load.addWidget(self.sp_interval)
        self.btn_sweep = QtWidgets.QPushButton('Varrer agora')
        self.btn_sweep.setToolTip('Mede as curvas I–V dos 4 módulos agora (~0,7 s).')
        self.btn_sweep.clicked.connect(lambda: self.send_cmd('S'))
        load.addWidget(self.btn_sweep)
        load.addSpacing(12)
        self.lbl_gates = QtWidgets.QLabel()
        self.lbl_gates.setStyleSheet('color:#555;')
        load.addWidget(self.lbl_gates, 1)
        root.addLayout(load)
        self.load_widgets = (self.cmb_mode, self.sp_gate, self.sp_interval, self.btn_sweep)

        tabs = QtWidgets.QTabWidget()
        self.tabs = tabs
        root.addWidget(tabs, 1)

        # ---- Aba Medidas
        page = QtWidgets.QWidget()
        tabs.addTab(page, 'Medidas')
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(6, 6, 6, 6)
        self.lbl_status = QtWidgets.QLabel('Inicializando…')
        self.lbl_status.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setTextFormat(QtCore.Qt.TextFormat.RichText)
        lay.addWidget(self.lbl_status)

        cards = QtWidgets.QHBoxLayout()
        self.cards = []
        for c in range(N_CELLS):
            card = CellCard(c, CELL_COLORS[c])
            card.chk.toggled.connect(self._mark_dirty)
            cards.addWidget(card)
            self.cards.append(card)
        lay.addLayout(cards)

        ctl = QtWidgets.QHBoxLayout()
        ctl.addWidget(self._bold('Gráfico:'))
        self.cmb_qty = QtWidgets.QComboBox()
        for key, name, unit in QUANTITIES:
            self.cmb_qty.addItem(f'{name} ({unit})', key)
        self.cmb_qty.currentIndexChanged.connect(self.on_quantity)
        ctl.addWidget(self.cmb_qty)
        self.lbl_refcell = QtWidgets.QLabel('em relação a')
        ctl.addWidget(self.lbl_refcell)
        self.cmb_refcell = QtWidgets.QComboBox()
        self.cmb_refcell.setToolTip('Grandeza de cada módulo dividida pela deste módulo (100 % = igual).')
        self.cmb_refcell.currentIndexChanged.connect(self.on_refcell)
        ctl.addWidget(self.cmb_refcell)
        ctl.addSpacing(10)
        ctl.addWidget(QtWidgets.QLabel('Janela:'))
        self.cmb_window = QtWidgets.QComboBox()
        for name, sec in WINDOWS:
            self.cmb_window.addItem(name, sec)
        self.cmb_window.currentIndexChanged.connect(self._mark_dirty)
        ctl.addWidget(self.cmb_window)
        ctl.addStretch(1)
        self.btn_pause = QtWidgets.QPushButton('Pausar')
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip('Congela o gráfico e libera zoom e arraste com o mouse '
                                  '(a aquisição e a gravação continuam).')
        self.btn_pause.toggled.connect(self.on_pause)
        ctl.addWidget(self.btn_pause)
        self.btn_save = QtWidgets.QPushButton('Salvar gráfico')
        self.btn_save.setToolTip('Grava em Medidas o CSV e o PNG do que está visível no gráfico.')
        self.btn_save.clicked.connect(self.save_view)
        ctl.addWidget(self.btn_save)
        lay.addLayout(ctl)

        self.plot = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem(orientation='bottom')})
        pi = self.plot.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setLabel('bottom', 'Hora')
        pi.getAxis('left').enableAutoSIPrefix(False)
        pi.setMouseEnabled(False, False)
        pi.setMenuEnabled(False)
        pi.hideButtons()
        pi.getViewBox().setAutoVisible(y=True)
        self.curves = [pi.plot(pen=pg.mkPen(CELL_COLORS[c], width=2), name=str(c))
                       for c in range(N_CELLS)]
        for cv in self.curves:
            cv.setDownsampling(auto=True, method='peak')
            cv.setClipToView(True)
        lay.addWidget(self.plot, 1)

        # ---- Aba Curvas I-V
        ivp = QtWidgets.QWidget()
        self.iv_page = ivp
        tabs.addTab(ivp, 'Curvas I–V')
        il = QtWidgets.QVBoxLayout(ivp)
        il.setContentsMargins(6, 6, 6, 6)
        ivctl = QtWidgets.QHBoxLayout()
        self.lbl_iv = QtWidgets.QLabel('Nenhuma varredura ainda. Requer o firmware 2.x (carga eletrônica).')
        ivctl.addWidget(self.lbl_iv, 1)
        self.chk_ivp = QtWidgets.QCheckBox('Mostrar potência')
        self.chk_ivp.setChecked(True)
        self.chk_ivp.toggled.connect(lambda _: self.update_iv())
        ivctl.addWidget(self.chk_ivp)
        self.btn_iv_save = QtWidgets.QPushButton('Salvar curvas')
        self.btn_iv_save.setToolTip('Grava em Medidas o CSV e o PNG das últimas curvas.')
        self.btn_iv_save.clicked.connect(self.save_iv)
        ivctl.addWidget(self.btn_iv_save)
        il.addLayout(ivctl)
        self.plot_iv = pg.PlotWidget()
        piv = self.plot_iv.getPlotItem()
        piv.showGrid(x=True, y=True, alpha=0.3)
        piv.setLabel('bottom', 'Tensão (mV)')
        piv.setLabel('left', 'Corrente (mA)')
        piv.getAxis('left').enableAutoSIPrefix(False)
        piv.getAxis('bottom').enableAutoSIPrefix(False)
        piv.showAxis('right')
        piv.getAxis('right').setLabel('Potência (mW)')
        piv.getAxis('right').enableAutoSIPrefix(False)
        self.iv_pvb = pg.ViewBox()
        piv.scene().addItem(self.iv_pvb)
        piv.getAxis('right').linkToView(self.iv_pvb)
        self.iv_pvb.setXLink(piv)

        def sync_iv():
            self.iv_pvb.setGeometry(piv.vb.sceneBoundingRect())
            self.iv_pvb.linkedViewChanged(piv.vb, self.iv_pvb.XAxis)
        piv.vb.sigResized.connect(sync_iv)
        self.iv_curves = [piv.plot(pen=pg.mkPen(CELL_COLORS[c], width=2)) for c in range(N_CELLS)]
        self.iv_pcurves = []
        for c in range(N_CELLS):
            pc = pg.PlotDataItem(pen=pg.mkPen(CELL_COLORS[c], width=1.5, style=QtCore.Qt.PenStyle.DashLine))
            self.iv_pvb.addItem(pc)
            self.iv_pcurves.append(pc)
        self.iv_mpp = pg.ScatterPlotItem(size=10, pen=pg.mkPen('k'), symbol='o')
        piv.addItem(self.iv_mpp)
        il.addWidget(self.plot_iv, 1)
        self.tbl_iv = QtWidgets.QTableWidget(N_CELLS, 9)
        self.tbl_iv.setHorizontalHeaderLabels(['Isc (mA)', 'Voc (mV)', 'Pmax (mW)', 'Vmp (mV)', 'Imp (mA)',
                                               'FF (%)', 'Pmax rel. (%)', 'Isc rel. (%)', 'Pmax/área (mW/cm²)'])
        self.tbl_iv.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tbl_iv.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_iv.setMaximumHeight(150)
        il.addWidget(self.tbl_iv)

        # ---- Aba Diagnóstico
        diag = QtWidgets.QWidget()
        self.diag_page = diag
        tabs.addTab(diag, 'Diagnóstico')
        dl = QtWidgets.QVBoxLayout(diag)
        dl.setContentsMargins(6, 6, 6, 6)
        self.lbl_diag = QtWidgets.QLabel()
        self.lbl_diag.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.lbl_diag.setWordWrap(True)
        dl.addWidget(self.lbl_diag)
        self.plot_ref = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem(orientation='bottom')})
        self.plot_ref.setTitle('Sensor de referência A0 (deriva comum dos sensores Hall)')
        self.plot_ref.setLabel('left', 'A0 (mV)')
        self.plot_ref.showGrid(x=True, y=True, alpha=0.3)
        self.plot_ref.getAxis('left').enableAutoSIPrefix(False)
        self.curve_ref = self.plot_ref.plot(pen=pg.mkPen('#555', width=1.5))
        self.curve_ref.setDownsampling(auto=True, method='peak')
        dl.addWidget(self.plot_ref, 1)
        self.plot_raw = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem(orientation='bottom')})
        self.plot_raw.setTitle('Sensores de corrente sem o offset do zero (mV), antes de dividir pela sensibilidade')
        self.plot_raw.setLabel('left', 'mV')
        self.plot_raw.showGrid(x=True, y=True, alpha=0.3)
        self.plot_raw.getAxis('left').enableAutoSIPrefix(False)
        self.plot_raw.setXLink(self.plot_ref)
        self.curves_raw = [self.plot_raw.plot(pen=pg.mkPen(CELL_COLORS[c], width=1.5))
                           for c in range(N_CELLS)]
        dl.addWidget(self.plot_raw, 1)

        # Barra de status (como no SOI): mensagens à esquerda, conexão e zero à direita
        sb = self.statusBar()
        self.lbl_sb_zero = QtWidgets.QLabel()
        self.lbl_sb_conn = QtWidgets.QLabel()
        self.lbl_sb_conn.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.lbl_sb_zero.setTextFormat(QtCore.Qt.TextFormat.RichText)
        sb.addPermanentWidget(self.lbl_sb_zero)
        sb.addPermanentWidget(self.lbl_sb_conn)
        self.last_sb_state = None

        self.apply_names()
        self.on_quantity()

    # --------------------------------------------------------- Auxiliares
    def flash(self, text, seconds=4.0):
        self.statusBar().showMessage(text, int(seconds * 1000))

    def _mark_dirty(self, *_):
        self.dirty = True

    def _sample_name(self):
        name = self.ed_sample.text().strip()
        return ''.join('_' if c in '\\/:*?"<>|' or ord(c) < 32 else c for c in name).strip(' .')

    def _stem(self, stem):
        name = self._sample_name()
        return f'{name}_{stem}' if name else stem

    def _session_add(self, *paths):
        for p in paths:
            if p not in self.session_files:
                self.session_files.append(p)

    def apply_names(self):
        for c, card in enumerate(self.cards):
            card.set_name(self.cfg.names[c])
        if hasattr(self, 'tbl_iv'):
            self.tbl_iv.setVerticalHeaderLabels(self.cfg.names)
            self.iv_dirty = True
        cur = self.cfg.ref_cell
        self.cmb_refcell.blockSignals(True)
        self.cmb_refcell.clear()
        self.cmb_refcell.addItems(self.cfg.names)
        self.cmb_refcell.setCurrentIndex(cur)
        self.cmb_refcell.blockSignals(False)
        self.dirty = True

    def zero_ok(self):
        return (self.zero_time is not None and self.zero_use_ref == self.cfg.use_ref
                and self.zero_vref == self.cfg.vref_mv)

    # ----------------------------------------------------------- Controles
    def refresh_ports(self):
        current = self.cmb_port.currentData() if self.cmb_port.count() else None
        self.cmb_port.blockSignals(True)
        self.cmb_port.clear()
        self.cmb_port.addItem('Automática', None)
        for p in sorted(list_ports.comports(), key=lambda p: p.device):
            desc = p.description if p.description and p.description != 'n/a' else ''
            self.cmb_port.addItem(f'{p.device}  {desc}'.strip(), p.device)
        idx = self.cmb_port.findData(current)
        if current and idx < 0:              # porta salva que não está ligada agora
            self.cmb_port.addItem(f'{current} (ausente)', current)
            idx = self.cmb_port.count() - 1
        self.cmb_port.setCurrentIndex(max(idx, 0))
        self.cmb_port.blockSignals(False)

    def on_port(self, *_):
        self.link.set_preferred(self.cmb_port.currentData())

    def on_simulate(self, checked):
        self.simulating = checked
        self.sim.reset()
        self.last_frame_t = None
        self.duties = None
        if checked:
            self.sync_firmware()
            self.flash('Simulação ligada: 4 módulos com coberturas de transmitância diferente '
                       '(100 %, 97 %, 93 % e 102 %), com carga eletrônica e curvas I–V.', 8.0)

    # ------------------------------------------------- Carga eletrônica
    def fw_new(self):
        '''Firmware 2.x (carga eletrônica) conectado, ou simulação.'''
        return (self.simulating and not self.link.connected) or \
            (self.link.connected and self.link.identity == 'QuadPV')

    def send_cmd(self, text):
        if not self.fw_new():
            self.flash('Os comandos da carga exigem o firmware 2.x.')
            return
        if self.link.connected:
            self.link.write(text)
        else:
            self.sim.command(text)

    def _mode_cmd(self):
        key = self.cmb_mode.currentData()
        return f'M F {self.sp_gate.value()}' if key == 'F' else f'M {key}'

    def on_mode(self, *_):
        self.sp_gate.setVisible(self.cmb_mode.currentData() == 'F')
        if self.fw_new():
            self.send_cmd(self._mode_cmd())

    def on_interval(self, *_):
        if self.fw_new():
            self.send_cmd(f'T {self.sp_interval.value()}')

    def sync_firmware(self):
        '''Ao conectar (ou ligar a simulação), manda o modo e o intervalo escolhidos na interface.'''
        self.send_cmd(self._mode_cmd())
        self.send_cmd(f'T {self.sp_interval.value()}')

    def update_load_widgets(self):
        ok = self.fw_new()
        for w in self.load_widgets:
            if w.isEnabled() != ok:
                w.setEnabled(ok)
                w.setToolTip(w.toolTip().split('\n[')[0] + ('' if ok else '\n[Requer o firmware 2.x]'))
        if ok and self.duties is not None:
            txt = 'Gates: ' + '  '.join(str(int(d)) for d in self.duties)
        elif self.link.connected and self.link.identity == 'antigo':
            txt = 'Firmware antigo: carga fixa, sem curvas I–V'
        else:
            txt = ''
        if txt != self.lbl_gates.text():
            self.lbl_gates.setText(txt)

    # ------------------------------------------------- Curvas I-V
    def process_iv(self, t, m, pts):
        if self.cfg.auto_zero:
            ref_mv, abs_mv = iv_offsets(pts, self.cfg.vref_mv)
            self.offsets['ref'][m] = ref_mv
            self.offsets['abs'][m] = abs_mv
            self.zero_time = 'automático ' + datetime.fromtimestamp(t).strftime('%H:%M:%S')
            self.zero_use_ref, self.zero_vref = self.cfg.use_ref, self.cfg.vref_mv
        res = analyze_iv(pts, m, self.cfg, self.offsets)
        res['t'] = t
        res['raw'] = pts
        self.iv_last[m] = res
        self.iv_hist.add(t, m, res)
        self.iv_dirty = True
        self.dirty = True
        if self.rec_file is not None:
            self._record_iv(t, m, res)

    def update_iv(self):
        self.iv_dirty = False
        show_p = self.chk_ivp.isChecked()
        spots = []
        ref = self.iv_last[self.cfg.ref_cell]
        for c in range(N_CELLS):
            r = self.iv_last[c]
            visible = r is not None and self.cards[c].chk.isChecked()
            self.iv_curves[c].setData(r['v'], r['i']) if visible else self.iv_curves[c].setData([], [])
            if visible and show_p:
                self.iv_pcurves[c].setData(r['v'], r['p'])
            else:
                self.iv_pcurves[c].setData([], [])
            if visible:
                spots.append({'pos': (r['vmp'], r['imp']), 'brush': pg.mkBrush(CELL_COLORS[c])})
            vals = [''] * 9
            if r is not None:
                rel = (lambda a, b: f'{100 * a / b:.1f}' if ref is not None and abs(b) > 1e-9 else '-')
                area = self.cfg.area_cm2[c]
                vals = [f"{r['isc']:.2f}", f"{r['voc']:.0f}", f"{r['pmax']:.2f}", f"{r['vmp']:.0f}",
                        f"{r['imp']:.2f}", f"{r['ff']:.1f}",
                        rel(r['pmax'], ref['pmax']) if ref else '-', rel(r['isc'], ref['isc']) if ref else '-',
                        f"{r['pmax'] / area:.3f}" if area > 0 else '-']
            for k, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(v)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.tbl_iv.setItem(c, k, item)
        self.iv_mpp.setData(spots)
        self.plot_iv.getPlotItem().getAxis('right').setVisible(show_p)
        self.iv_pvb.enableAutoRange()
        times = [r['t'] for r in self.iv_last if r is not None]
        if times:
            self.lbl_iv.setText(f"Última varredura: {datetime.fromtimestamp(max(times)).strftime('%H:%M:%S')} · "
                                f"{len(self.iv_hist.t)} varreduras nesta sessão · ● = ponto de máxima potência")

    def save_iv(self):
        if not any(r is not None for r in self.iv_last):
            self.flash('Ainda não há curvas I–V.')
            return
        now = datetime.now()
        os.makedirs(MEDIDAS_DIR, exist_ok=True)
        stem = self._stem(f"CurvasIV_{now.strftime('%Y%m%d_%H%M%S')}")
        csv_path = os.path.join(MEDIDAS_DIR, stem + '.csv')
        png_path = os.path.join(MEDIDAS_DIR, stem + '.png')
        try:
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                f.write(f'# Curvas I-V - QuadPV {__version__} - {now.strftime("%Y-%m-%d %H:%M:%S")}\n')
                for line in self.cfg.header_lines():
                    f.write(f'# {line}\n')
                for c, r in enumerate(self.iv_last):
                    if r is not None:
                        f.write(f"# {self.cfg.names[c]}: varredura {datetime.fromtimestamp(r['t']).isoformat(timespec='seconds')}, "
                                f"Isc={r['isc']:.3f} mA, Voc={r['voc']:.1f} mV, Pmax={r['pmax']:.3f} mW, "
                                f"Vmp={r['vmp']:.1f} mV, Imp={r['imp']:.3f} mA, FF={r['ff']:.2f} %\n")
                f.write('modulo,nome,ponto,gate,V_mV,I_mA,P_mW\n')
                for c, r in enumerate(self.iv_last):
                    if r is None:
                        continue
                    for k in range(len(r['v'])):
                        f.write(f'{c + 1},"{self.cfg.names[c]}",{k},{int(r["gate"][k])},{r["v"][k]:.2f},'
                                f'{r["i"][k]:.4f},{r["p"][k]:.4f}\n')
            self.plot_iv.grab().save(png_path)
            self._session_add(csv_path, png_path)
            self.flash(f'Curvas salvas: {csv_path}', 6.0)
        except Exception as e:
            self.flash(f'Erro ao salvar: {e}')

    def on_quantity(self, *_):
        key = self.cmb_qty.currentData()
        rel = key in ('R', 'IR', 'SPR', 'SIR')
        self.lbl_refcell.setVisible(rel)
        self.cmb_refcell.setVisible(rel)
        name, unit = next((n, u) for k, n, u in QUANTITIES if k == key)
        self.plot.getPlotItem().setLabel('left', f'{name} ({unit})')
        if key == 'PA' and not any(a > 0 for a in self.cfg.area_cm2):
            self.flash('Informe as áreas dos módulos em "Configurar módulos…" para ver a potência por área.')
        self.dirty = True

    def on_refcell(self, idx):
        if idx >= 0:
            self.cfg.ref_cell = idx
            self.dirty = True

    def on_pause(self, checked):
        self.paused = checked
        self.btn_pause.setText('Continuar' if checked else 'Pausar')
        pi = self.plot.getPlotItem()
        pi.setMouseEnabled(checked, checked)
        if not checked:
            self.dirty = True

    def open_config(self):
        dlg = ConfigDialog(self.cfg, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        old_use_ref, old_vref = self.cfg.use_ref, self.cfg.vref_mv
        dlg.apply_to(self.cfg)
        self.apply_names()
        for m, r in enumerate(self.iv_last):
            if r is not None:
                res = analyze_iv(r['raw'], m, self.cfg, self.offsets)
                res['t'], res['raw'] = r['t'], r['raw']
                self.iv_last[m] = res
        self.save_settings()
        if self.zero_time is not None and (self.cfg.use_ref != old_use_ref or self.cfg.vref_mv != old_vref):
            QtWidgets.QMessageBox.information(
                self, 'Refazer o zero',
                'A referência do ADC ou o uso do canal A0 mudou. Refaça o zero das correntes '
                'com os módulos no escuro.')
        if self.rec_file is not None:
            self.flash('A configuração nova vale a partir de agora; o cabeçalho do CSV em gravação '
                       'tem a configuração antiga.', 8.0)

    def start_zero(self):
        if not (self.link.connected or self.simulating):
            self.flash('Conecte o STM32 (ou ligue a simulação) antes de zerar.')
            return
        ans = QtWidgets.QMessageBox.question(
            self, 'Zerar correntes',
            'Cubra todos os módulos (ou desligue as cargas) para que nenhuma corrente circule.\n\n'
            f'Clique OK e aguarde ~{ZERO_FRAMES} s sem mexer.',
            QtWidgets.QMessageBox.StandardButton.Ok | QtWidgets.QMessageBox.StandardButton.Cancel)
        if ans != QtWidgets.QMessageBox.StandardButton.Ok:
            return
        self.zero_acc = []
        self.sim.covered = True
        self.btn_zero.setEnabled(False)
        self.flash('Zerando… mantenha os módulos cobertos.', ZERO_FRAMES + 3)

    def _zero_feed(self, raw):
        self.zero_acc.append(current_diff_mv(raw, self.cfg.vref_mv))
        if len(self.zero_acc) < ZERO_FRAMES:
            return
        d_abs = np.mean([a for a, _ in self.zero_acc], axis=0)
        d_ref = np.mean([r for _, r in self.zero_acc], axis=0)
        spread = np.std([r if self.cfg.use_ref else a for a, r in self.zero_acc], axis=0)
        self.offsets = {'abs': d_abs, 'ref': d_ref}
        self.zero_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.zero_use_ref, self.zero_vref = self.cfg.use_ref, self.cfg.vref_mv
        self.zero_acc = None
        self.sim.covered = False
        self.btn_zero.setEnabled(True)
        self.save_settings()
        noise_ma = spread / np.asarray(self.cfg.sens)
        self.flash('Zero feito. Ruído de cada sensor no zero: '
                   + ', '.join(f'{x:.2f} mA' for x in noise_ma) + '. Pode descobrir os módulos.', 10.0)
        if self.rec_file is not None:
            self.rec_file.write(f'# Zero refeito em {self.zero_time}: offsets (mV) = '
                                + ', '.join(f'{x:.3f}' for x in self._active_offsets()) + '\n')

    def _active_offsets(self):
        return self.offsets['ref'] if self.cfg.use_ref else self.offsets['abs']

    def reset_data(self):
        self.data.clear()
        self.iv_hist.clear()
        self.last_frame_t = None
        for card in self.cards:
            card.clear()
        self.dirty = True
        self.flash('Gráficos e energia reiniciados.')

    # ----------------------------------------------------------- Gravação
    def on_record(self, checked):
        if checked:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        if not self.zero_ok() and not (self.fw_new() and self.cfg.auto_zero):
            ans = QtWidgets.QMessageBox.question(
                self, 'Gravar sem zero',
                'As correntes ainda não foram zeradas com a configuração atual.\n'
                'As contagens brutas vão no CSV (dá para corrigir depois), mas as correntes e potências '
                'gravadas terão o offset dos sensores.\n\nGravar mesmo assim?')
            if ans != QtWidgets.QMessageBox.StandardButton.Yes:
                self.btn_rec.blockSignals(True)
                self.btn_rec.setChecked(False)
                self.btn_rec.blockSignals(False)
                return
        os.makedirs(MEDIDAS_DIR, exist_ok=True)
        now = datetime.now()
        self.rec_path = os.path.join(MEDIDAS_DIR, self._stem(f"QuadPV_{now.strftime('%Y%m%d_%H%M%S')}") + '.csv')
        try:
            f = open(self.rec_path, 'w', encoding='utf-8', newline='')
        except Exception as e:
            self.flash(f'Erro ao criar o arquivo: {e}', 8.0)
            self.btn_rec.blockSignals(True)
            self.btn_rec.setChecked(False)
            self.btn_rec.blockSignals(False)
            return
        if self.link.connected:
            fw = self.link.fw.get('version', '?') if self.link.fw else None
            src = f'{self.link.port}, firmware ' + (fw if fw else 'antigo (sem carga eletronica)')
        else:
            src = 'simulacao'
        f.write(f'# QuadPV {__version__} - inicio {now.strftime("%Y-%m-%d %H:%M:%S")} - fonte: {src}\n')
        f.write(f'# Amostra: {self.ed_sample.text().strip()}\n')
        if self.ed_note.text().strip():
            f.write(f'# Observacao: {self.ed_note.text().strip()}\n')
        for line in self.cfg.header_lines():
            f.write(f'# {line}\n')
        if self.zero_ok():
            f.write(f'# Zero em {self.zero_time}: offsets (mV) = '
                    + ', '.join(f'{x:.3f}' for x in self._active_offsets()) + '\n')
        else:
            f.write('# ATENCAO: correntes sem zero (offsets = 0)\n')
        if self.fw_new() and self.cfg.auto_zero:
            f.write('# Zero automatico: o offset de cada sensor e refeito no ponto em aberto de cada '
                    'varredura I-V (ver o arquivo _IV.csv)\n')
        f.write('# I_mA = ((A[1..4] - A0) * Vref/4096 - offset) / sensibilidade; V_mV = A[5..8] * Vref/4096 * divisor\n')
        f.write('# G1..G4 = gate da carga eletronica (0..1023) e modo (P = MPPT, F = fixo, O = aberto, C = curto); '
                'vazios com o firmware antigo\n')
        cols = ['datahora', 't_s']
        for c in range(N_CELLS):
            cols += [f'V{c + 1}_mV', f'I{c + 1}_mA', f'P{c + 1}_mW', f'E{c + 1}_mWh']
        cols += [f'A{k}' for k in range(N_RAW)]
        cols += [f'G{c + 1}' for c in range(N_CELLS)] + ['modo']
        f.write(','.join(cols) + '\n')
        f.flush()
        self.rec_file = f
        self.rec_t0 = None
        self.rec_e0 = self.data.energy.copy()
        self.rec_lines = 0
        self.btn_rec.setText('■  Parar')
        self.ed_note.setEnabled(False)
        self._session_add(self.rec_path)
        self.flash(f'Gravando em {self.rec_path}', 6.0)

    def _record_iv(self, t, m, res):
        '''Curvas e parâmetros de cada varredura em dois CSV ao lado do arquivo da gravação.'''
        try:
            if self.iv_files is None:
                base = self.rec_path[:-4]
                fc = open(base + '_IV.csv', 'w', encoding='utf-8', newline='')
                fc.write('# Curvas I-V da gravacao ' + os.path.basename(self.rec_path) + '\n')
                fc.write('# ref, A_I, A_V = contagens do ADC (media de ~2 ms); gate = PWM da carga (0..1023)\n')
                fc.write('datahora,modulo,ponto,gate,V_mV,I_mA,P_mW,ref,A_I,A_V\n')
                fp = open(base + '_IVparametros.csv', 'w', encoding='utf-8', newline='')
                fp.write('# Parametros de cada varredura da gravacao ' + os.path.basename(self.rec_path) + '\n')
                fp.write('datahora,modulo,nome,Isc_mA,Voc_mV,Pmax_mW,Vmp_mV,Imp_mA,FF_pct,gate_mp\n')
                self.iv_files = (fc, fp)
                self._session_add(base + '_IV.csv', base + '_IVparametros.csv')
            fc, fp = self.iv_files
            stamp = datetime.fromtimestamp(t).isoformat(timespec='milliseconds')
            raw = res['raw'][np.argsort(res['raw'][:, 2])]          # mesma ordem (por V) da curva
            for k in range(len(res['v'])):
                fc.write(f"{stamp},{m + 1},{k},{int(res['gate'][k])},{res['v'][k]:.2f},{res['i'][k]:.4f},"
                         f"{res['p'][k]:.4f},{raw[k, 0]:.2f},{raw[k, 1]:.2f},{raw[k, 2]:.2f}\n")
            fp.write(f'{stamp},{m + 1},"{self.cfg.names[m]}",{res["isc"]:.4f},{res["voc"]:.2f},{res["pmax"]:.4f},'
                     f'{res["vmp"]:.2f},{res["imp"]:.4f},{res["ff"]:.3f},{int(res["gmp"])}\n')
            fc.flush()
            fp.flush()
        except Exception as e:
            self.flash(f'Erro ao gravar as curvas: {e}', 10.0)

    def stop_recording(self):
        if self.iv_files is not None:
            for f in self.iv_files:
                try:
                    f.close()
                except Exception:
                    pass
            self.iv_files = None
        if self.rec_file is not None:
            try:
                self.rec_file.close()
            except Exception:
                pass
            self.flash(f'{self.rec_lines} leituras gravadas em {self.rec_path}', 8.0)
        self.rec_file = None
        self.btn_rec.setText('●  Gravar')
        self.ed_note.setEnabled(True)
        self.lbl_rec.setText('Não gravando')
        self.lbl_rec.setStyleSheet('color:#666;')

    def _record(self, t, v, i, p, raw):
        if self.rec_t0 is None:             # energia do CSV conta a partir da 1ª leitura gravada
            self.rec_t0 = t
            self.rec_e0 = self.data.energy.copy()
        e = self.data.energy - self.rec_e0
        row = [datetime.fromtimestamp(t).isoformat(timespec='milliseconds'), f'{t - self.rec_t0:.3f}']
        for c in range(N_CELLS):
            row += [f'{v[c]:.2f}', f'{i[c]:.3f}', f'{p[c]:.4f}', f'{e[c]:.5f}']
        row += [f'{x:.2f}' for x in raw]
        if self.duties is not None:
            row += [str(int(d)) for d in self.duties] + [str(self.fw_mode)]
        else:
            row += [''] * (N_CELLS + 1)
        try:
            self.rec_file.write(','.join(row) + '\n')
            self.rec_file.flush()
            self.rec_lines += 1
        except Exception as e:
            self.flash(f'Erro ao gravar: {e}', 10.0)
            self.btn_rec.setChecked(False)

    def save_view(self):
        key = self.cmb_qty.currentData()
        t, y = self.series(key)
        if len(t) == 0:
            self.flash('Não há dados para salvar.')
            return
        x0, x1 = self.plot.getPlotItem().getViewBox().viewRange()[0]
        m = (t >= x0) & (t <= x1)
        now = datetime.now()
        os.makedirs(MEDIDAS_DIR, exist_ok=True)
        stem = self._stem(f"Grafico_{key}_{now.strftime('%Y%m%d_%H%M%S')}")
        csv_path = os.path.join(MEDIDAS_DIR, stem + '.csv')
        png_path = os.path.join(MEDIDAS_DIR, stem + '.png')
        name, unit = next((n, u) for k, n, u in QUANTITIES if k == key)
        try:
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                f.write(f'# {name} ({unit}) - QuadPV {__version__} - {now.strftime("%Y-%m-%d %H:%M:%S")}\n')
                if key in ('R', 'IR', 'SPR', 'SIR'):
                    f.write(f'# relativa a: {self.cfg.names[self.cfg.ref_cell]}\n')
                f.write('datahora,' + ','.join(f'"{n}"' for n in self.cfg.names) + '\n')
                for tt, row in zip(t[m], y[m]):
                    f.write(datetime.fromtimestamp(tt).isoformat(timespec='seconds') + ','
                            + ','.join('' if not np.isfinite(v) else f'{v:.5g}' for v in row) + '\n')
            self.plot.grab().save(png_path)
            self._session_add(csv_path, png_path)
            self.flash(f'Gráfico salvo: {csv_path}', 6.0)
        except Exception as e:
            self.flash(f'Erro ao salvar: {e}')

    def finish_experiment(self):
        if self.rec_file is not None:
            self.btn_rec.setChecked(False)
        files = [p for p in self.session_files if os.path.isfile(p)]
        if not files:
            QtWidgets.QMessageBox.information(
                self, 'Finalizar experimento',
                'Nenhum arquivo foi salvo desde o início da sessão (ou desde o último "Finalizar experimento").')
            return
        zip_path = os.path.join(MEDIDAS_DIR, f"{self._sample_name() or 'Experimento'}_"
                                             f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
                for p in files:
                    z.write(p, os.path.basename(p))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, 'Finalizar experimento', f'Erro ao criar o ZIP:\n{e}')
            return
        self.session_files.clear()
        names = '\n'.join(os.path.basename(p) for p in files)
        QtWidgets.QMessageBox.information(
            self, 'Experimento finalizado',
            f'{len(files)} arquivo(s) compactado(s) em:\n{zip_path}\n\n{names}\n\n'
            'Os originais continuam na pasta Medidas.')

    # --------------------------------------------------------- Laço da GUI
    def process_raw(self, t, raw):
        self.last_rx = time.monotonic()
        self.last_raw_t = t
        if self.zero_acc is not None:
            self._zero_feed(raw)
        v, i, p = to_physical(raw, self.cfg, self.offsets)
        self.saturated = bool(np.any(raw < ADC_SAT_LOW) or np.any(raw > ADC_SAT_HIGH))
        self.data.append(t, v, i, p, raw)
        if self.rec_file is not None:
            self._record(t, v, i, p, raw)
        self.dirty = True

    def handle(self, msg):
        if msg.kind == 'D':
            self.process_raw(msg.t, msg.data)
        elif msg.kind == 'G':
            self.duties, self.fw_mode = msg.data
        elif msg.kind == 'IV':
            self.process_iv(msg.t, *msg.data)
        elif msg.kind == '#':
            info = fw_info(msg.data)
            if info is None:
                self.flash(f'Firmware: {msg.data}', 6.0)
            else:
                self.fw_info = info

    def tick(self):
        now_mono = time.monotonic()
        msgs = self.link.take()
        if self.link.connected:
            if self.simulating:
                self.chk_sim.setChecked(False)
            if self.link.identity == 'QuadPV' and self.synced_port != self.link.port:
                self.synced_port = self.link.port
                self.sync_firmware()
        else:
            self.synced_port = None
            if self.simulating:
                msgs += self.sim.poll(time.time())
        got_data = False
        for msg in msgs:
            self.handle(msg)
            got_data |= msg.kind == 'D'
        if got_data:
            self.update_cards()
        self.chk_sim.setVisible(not self.link.connected)
        self.update_load_widgets()
        self.update_status()
        if not self.paused and now_mono - self.last_plot >= PLOT_INTERVAL:
            if self.iv_dirty:
                self.update_iv()
            if self.dirty:
                self.last_plot = now_mono
                self.dirty = False
                self.update_plots()

    def update_cards(self):
        n = self.data.n
        if n == 0:
            return
        k = n - 1
        p = self.data.p[k]
        ref = p[self.cfg.ref_cell]
        for c, card in enumerate(self.cards):
            a = self.cfg.area_cm2[c]
            pa = p[c] / a if a > 0 else None
            r = 100.0 * p[c] / ref if abs(ref) > 0.05 else None
            raw = self.data.raw[k]
            warn = raw[1 + c] > ADC_SAT_HIGH or raw[5 + c] > ADC_SAT_HIGH or raw[1 + c] < ADC_SAT_LOW
            card.show_values(self.data.v[k, c], self.data.i[k, c], p[c], pa, self.data.e[k, c], r, warn)
        if self.rec_file is not None:
            self.lbl_rec.setText(f'Gravando: {os.path.basename(self.rec_path)}  ({self.rec_lines} leituras)')
            self.lbl_rec.setStyleSheet(f'color:{BAD_COLOR};font-weight:bold;')

    def update_status(self):
        receiving = (self.link.connected or self.simulating) and now_ok(self.last_rx)
        if self.zero_acc is not None:
            text = f"<b style='color:{WARN_COLOR}'>Zerando… {len(self.zero_acc)}/{ZERO_FRAMES}</b>"
        elif not (self.link.connected or self.simulating):
            text = f"<span style='color:{WARN_COLOR}'>Aguardando o STM32…</span>"
        elif not receiving:
            text = (f"<b style='color:{BAD_COLOR}'>Sem dados há mais de {NO_DATA_TIMEOUT:.0f} s</b> "
                    f"(confira o firmware e o cabo)")
        elif self.saturated:
            text = (f"<b style='color:{BAD_COLOR}'>ADC no limite (0 ou 3,3 V)</b>: confira a tensão dos "
                    f"módulos e a saída dos sensores de corrente")
        elif not self.zero_ok() and self.fw_new() and self.cfg.auto_zero:
            text = f"<span style='color:{WARN_COLOR}'>Aguardando a 1ª varredura I–V para o zero automático…</span>"
        elif not self.zero_ok():
            text = (f"<b style='color:{WARN_COLOR}'>Correntes sem zero</b>: cubra os módulos e clique em "
                    f"\"Zerar correntes…\"")
        else:
            text = f"<span style='color:{OK_COLOR}'>Medindo</span> · {self.data.n} leituras"
        if text != self.lbl_status.text():
            self.lbl_status.setText(text)

        if self.link.connected:
            ident = self.link.identity
            if ident == 'QuadPV':
                what = f"QuadPV fw {self.link.fw.get('version', '?')}"
            elif ident == 'antigo':
                what = 'firmware antigo'
            else:
                what = "<span style='color:#d07000;'>identidade não confirmada</span>"
            conn = f"<span style='color:{OK_COLOR};'>●</span> {what} em {self.link.port}"
            tip = (f'Linhas inválidas recebidas: {self.link.bad_lines}' if ident != 'não confirmada' else
                   'A placa não respondeu ao "?" nem mandou médias: confira se é o QuadPV.')
        elif self.simulating:
            conn = "<span style='color:#1f5fbf;'>●</span> Simulação (sem STM32)"
            tip = ''
        else:
            conn = f"<span style='color:{WARN_COLOR};'>●</span> STM32 desconectado, procurando…"
            tip = self.link.last_error or ''
        zero = (f"Zero: {self.zero_time}" if self.zero_ok()
                else f"<span style='color:{WARN_COLOR};'>Sem zero</span>")
        state = (conn, tip, zero)
        if state != self.last_sb_state:
            self.last_sb_state = state
            self.lbl_sb_conn.setText(conn)
            self.lbl_sb_conn.setToolTip(tip)
            self.lbl_sb_zero.setText(zero)

        if self.tabs.currentWidget() is self.diag_page:
            n = self.data.n
            rate = ''
            if n > 10:
                dt = np.diff(self.data.t[max(0, n - 30):n])
                rate = f' · intervalo médio entre leituras: {np.mean(dt):.2f} s'
            off = ', '.join(f'{x:.2f}' for x in self._active_offsets())
            txt = (f"Offsets do zero (mV): {off} · modo: "
                   f"{'descontando A0' if self.cfg.use_ref else 'absoluto'} · Vref = {self.cfg.vref_mv:g} mV"
                   f"{rate} · linhas inválidas: {self.link.bad_lines}")
            if txt != self.lbl_diag.text():
                self.lbl_diag.setText(txt)

    def series(self, key):
        if key in SWEEP_KEYS:
            return self.iv_hist.series(key, self.cfg.ref_cell)
        return self.data.series(key, self.cfg)

    def update_plots(self):
        key = self.cmb_qty.currentData()
        t, y = self.series(key)
        sweep = key in SWEEP_KEYS
        for c, cv in enumerate(self.curves):
            if sweep:
                cv.setSymbol('o')
                cv.setSymbolSize(6)
                cv.setSymbolBrush(CELL_COLORS[c])
                cv.setSymbolPen(None)
            else:
                cv.setSymbol(None)
            if self.cards[c].chk.isChecked() and len(t):
                cv.setData(t, y[:, c], connect='finite')
            else:
                cv.setData([], [])
        pi = self.plot.getPlotItem()
        win = self.cmb_window.currentData()
        n = self.data.n
        t_end = max(self.data.t[n - 1] if n else 0.0, t[-1] if len(t) else 0.0)
        if t_end:
            t_start = self.data.t[0] if n else t[0]
            t0 = t_end - win if win else t_start
            pi.setXRange(t0, t_end + 0.02 * max(t_end - t0, 10.0), padding=0)
            pi.enableAutoRange(axis='y')
        if n:
            t = self.data.t[:n]
            k = self.cfg.vref_mv / ADC_COUNTS
            raw = self.data.raw[:n]
            self.curve_ref.setData(t, raw[:, 0] * k)
            d = raw[:, 1:5] * k - (raw[:, :1] * k if self.cfg.use_ref else 0.0) - self._active_offsets()
            for c, cv in enumerate(self.curves_raw):
                cv.setData(t, d[:, c])
            if len(t):
                self.plot_ref.setXRange(t[0] if not win else max(t[0], t[-1] - win), t[-1], padding=0.02)

    # ---------------------------------------------------- Preferências
    def load_settings(self):
        st = QtCore.QSettings(APP_ORG, APP_NAME)

        def num(name, default):
            try:
                v = st.value(name)
                return default if v is None else float(v)
            except Exception:
                return default

        try:
            c = self.cfg
            c.vref_mv = num('vref_mv', c.vref_mv)
            c.use_ref = str(st.value('use_ref', 'true')).lower() in ('true', '1')
            c.load_ohm = num('load_ohm', c.load_ohm)
            c.ref_cell = int(num('ref_cell', 0)) % N_CELLS
            c.auto_zero = str(st.value('auto_zero', 'true')).lower() in ('true', '1')
            for i in range(N_CELLS):
                c.names[i] = str(st.value(f'name{i}', c.names[i]))
                c.area_cm2[i] = num(f'area{i}', c.area_cm2[i])
                c.sens[i] = num(f'sens{i}', c.sens[i])
                c.div[i] = num(f'div{i}', c.div[i])
            zt = st.value('zero_time')
            if zt:
                self.zero_time = str(zt)
                self.zero_vref = num('zero_vref', None)
                self.zero_use_ref = str(st.value('zero_use_ref', 'true')).lower() in ('true', '1')
                self.offsets = {k: np.array([num(f'off_{k}{i}', 0.0) for i in range(N_CELLS)])
                                for k in ('abs', 'ref')}
            self.ed_sample.setText(str(st.value('sample_name', '')))
            self.cmb_qty.setCurrentIndex(int(num('qty', 0)))
            self.cmb_window.setCurrentIndex(int(num('window', 0)))
            for w, name, default in ((self.cmb_mode, 'load_mode', 0),):
                w.blockSignals(True)
                w.setCurrentIndex(int(num(name, default)))
                w.blockSignals(False)
            for w, name, default in ((self.sp_gate, 'load_gate', 0), (self.sp_interval, 'sweep_interval', 60)):
                w.blockSignals(True)
                w.setValue(int(num(name, default)))
                w.blockSignals(False)
            self.sp_gate.setVisible(self.cmb_mode.currentData() == 'F')
            port = st.value('port')
            if port:
                self.cmb_port.addItem(f'{port} (salva)', str(port))
                self.cmb_port.setCurrentIndex(self.cmb_port.count() - 1)
                self.refresh_ports()
                self.link.preferred = str(port)
            geo = st.value('geometry')
            if geo is not None:
                self.restoreGeometry(geo)
        except Exception as e:
            print(f'Preferências ignoradas ({e})')
        self.apply_names()

    def save_settings(self):
        st = QtCore.QSettings(APP_ORG, APP_NAME)
        c = self.cfg
        st.setValue('vref_mv', c.vref_mv)
        st.setValue('use_ref', c.use_ref)
        st.setValue('load_ohm', c.load_ohm)
        st.setValue('ref_cell', c.ref_cell)
        st.setValue('auto_zero', c.auto_zero)
        st.setValue('load_mode', self.cmb_mode.currentIndex())
        st.setValue('load_gate', self.sp_gate.value())
        st.setValue('sweep_interval', self.sp_interval.value())
        for i in range(N_CELLS):
            st.setValue(f'name{i}', c.names[i])
            st.setValue(f'area{i}', c.area_cm2[i])
            st.setValue(f'sens{i}', c.sens[i])
            st.setValue(f'div{i}', c.div[i])
        if self.zero_time:
            st.setValue('zero_time', self.zero_time)
            st.setValue('zero_vref', self.zero_vref)
            st.setValue('zero_use_ref', self.zero_use_ref)
            for k in ('abs', 'ref'):
                for i in range(N_CELLS):
                    st.setValue(f'off_{k}{i}', float(self.offsets[k][i]))
        st.setValue('sample_name', self.ed_sample.text())
        st.setValue('qty', self.cmb_qty.currentIndex())
        st.setValue('window', self.cmb_window.currentIndex())
        st.setValue('port', self.cmb_port.currentData() or '')
        st.setValue('geometry', self.saveGeometry())

    def warn_not_found(self):
        if not self.link.connected and not self.simulating:
            self.flash('STM32 não encontrado. Conecte a placa ou marque "Simular medidas" para testar.', 10.0)

    def closeEvent(self, event):
        if self.rec_file is not None:
            ans = QtWidgets.QMessageBox.question(
                self, 'Gravação em andamento', 'Há uma gravação em andamento. Parar e fechar?')
            if ans != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_recording()
        try:
            self.save_settings()
        except Exception as e:
            print(f'Preferências não salvas ({e})')
        self.timer.stop()
        self.link.stop()
        super().closeEvent(event)


def now_ok(last_rx):
    return time.monotonic() - last_rx < NO_DATA_TIMEOUT


def main():
    app = pg.mkQApp('QuadPV - LCTM')
    win = QuadPVWindow()
    win.show()
    QtCore.QTimer.singleShot(2500, win.warn_not_found)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
