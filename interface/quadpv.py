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
)

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

    def header_lines(self):
        lines = [f'Vref_ADC={self.vref_mv:g} mV; canal de referencia A0 '
                 f'{"descontado" if self.use_ref else "ignorado"}; carga={self.load_ohm:g} ohm']
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
# Serial em thread
# ----------------------------------------------------------------------------
def find_port(preferred=None):
    '''Porta escolhida pelo usuário, variável QUADPV_PORT, STM32 (VID 0483) ou a única USB.'''
    ports = list(list_ports.comports())
    names = {p.device.upper() for p in ports}
    for choice in (preferred, os.environ.get('QUADPV_PORT')):
        if choice and choice.upper() in names:
            return choice
    if preferred:            # porta escolhida não está presente: não pega outra por engano
        return None
    usb = [p for p in ports if p.vid is not None]
    for vids in ({STM32_VID}, KNOWN_VIDS):
        for p in usb:
            if p.vid in vids:
                return p.device
    for p in usb:
        ident = ' '.join(filter(None, [p.description, p.manufacturer])).lower()
        if any(k in ident for k in ('stm32', 'stmicro', 'maple', 'arduino')):
            return p.device
    if len(usb) == 1:
        return usb[0].device
    return None


class SerialLink(threading.Thread):
    '''Lê linhas da porta serial e reconecta sozinha. A GUI chama take() para pegar as leituras.'''

    def __init__(self):
        super().__init__(daemon=True)
        self.ser = None
        self.connected = False
        self.port = None
        self.preferred = None          # None = automática
        self.last_error = None
        self.bad_lines = 0
        self._frames = deque(maxlen=10000)
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._reconnect_evt = threading.Event()

    def take(self):
        with self._lock:
            frames = list(self._frames)
            self._frames.clear()
        return frames

    def set_preferred(self, port):
        self.preferred = port or None
        self._reconnect_evt.set()

    def stop(self):
        self._stop_evt.set()
        self.join(timeout=2.0)
        self._close()

    def _close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.connected = False

    def _try_connect(self):
        port = find_port(self.preferred)
        if port is None:
            self.last_error = (f'Porta {self.preferred} não encontrada.' if self.preferred
                               else 'Nenhuma porta do STM32 (USB VID 0x0483) encontrada.')
            return
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=0.2)
            if self._stop_evt.wait(0.3):
                ser.close()
                return
            ser.reset_input_buffer()
            self._buf.clear()
            self.ser, self.port, self.connected, self.last_error = ser, port, True, None
            print(f'Conectado a {port}')
        except Exception as e:
            self.last_error = f'{port}: {e}'
            print(f'Erro ao abrir {port}: {e}')

    def run(self):
        last_try = 0.0
        while not self._stop_evt.is_set():
            if self._reconnect_evt.is_set():
                self._reconnect_evt.clear()
                self._close()
                last_try = 0.0
            if self.ser is None:
                now = time.monotonic()
                if now - last_try >= RECONNECT_INTERVAL:
                    last_try = now
                    self._try_connect()
                else:
                    self._stop_evt.wait(0.1)
                continue
            try:
                data = self.ser.read(max(1, self.ser.in_waiting))
            except Exception as e:
                print(f'Conexão perdida: {e}')
                self.last_error = str(e)
                self._close()
                continue
            if not data:
                continue
            self._buf.extend(data)
            if len(self._buf) > 100_000:       # lixo sem quebra de linha
                self._buf.clear()
            while True:
                nl = self._buf.find(b'\n')
                if nl < 0:
                    break
                line = bytes(self._buf[:nl])
                del self._buf[:nl + 1]
                raw = parse_line(line)
                if raw is None:
                    if line.strip():
                        self.bad_lines += 1
                    continue
                with self._lock:
                    self._frames.append(Frame(time.time(), raw))


# ----------------------------------------------------------------------------
# Simulação (sem STM32)
# ----------------------------------------------------------------------------
class Simulator:
    '''Quatro módulos pequenos (4 células em série, Voc ~2,4 V) numa carga resistiva fixa,
    sob irradiância que varia devagar e com nuvens. Os módulos diferem só na transmitância
    da cobertura (fatores abaixo). Gera as mesmas contagens que o firmware.'''

    FACTORS = (1.00, 0.97, 0.93, 1.02)
    ISC_MA = 120.0          # corrente de curto-circuito a 1000 W/m²
    VOC_MV = 2400.0
    N_VT_MV = 4 * 1.3 * 25.7
    LOAD_OHM = 15.0
    SENS = DEFAULT_SENS
    OFFSETS_MV = (6.0, -4.0, 11.0, -2.0)    # offsets individuais dos sensores Hall

    def __init__(self, seed=None):
        self.rng = np.random.default_rng(seed)
        self.reset()

    def reset(self):
        self.t0 = time.time()
        self.cloud = 0.0
        self.covered = False

    def irradiance(self, t):
        x = (t - self.t0) / 1200.0                     # "dia" de 20 min para testes
        clear = 0.15 + 0.85 * math.sin(math.pi * x) ** 2
        if self.cloud < 0.02 and self.rng.random() < 0.006:
            self.cloud = self.rng.uniform(0.3, 0.7)        # nuvem passa: queda rápida...
        self.cloud *= 0.85                                 # ...e volta em ~15 s
        return 1000.0 * clear * (1.0 - self.cloud)

    def operating_point(self, isc_ma):
        '''Interseção da curva I-V (diodo único) com a reta da carga, por bisseção em V.'''
        if isc_ma <= 1e-6:
            return 0.0, 0.0
        i0 = self.ISC_MA / math.exp(self.VOC_MV / self.N_VT_MV)
        lo, hi = 0.0, self.N_VT_MV * math.log(isc_ma / i0 + 1.0)
        for _ in range(50):
            v = 0.5 * (lo + hi)
            i_diode = isc_ma - i0 * (math.exp(v / self.N_VT_MV) - 1.0)
            if i_diode > v / self.LOAD_OHM:
                lo = v
            else:
                hi = v
        v = 0.5 * (lo + hi)
        return v, v / self.LOAD_OHM

    def frame(self, t):
        k = DEFAULT_VREF_MV / ADC_COUNTS
        g = 0.0 if self.covered else self.irradiance(t)
        drift_mv = 1650.0 + 8.0 * math.sin((t - self.t0) / 97.0)   # deriva comum dos Hall
        raw = np.empty(N_RAW)
        raw[0] = drift_mv / k
        for c in range(N_CELLS):
            v_mv, i_ma = self.operating_point(self.ISC_MA * g / 1000.0 * self.FACTORS[c])
            raw[1 + c] = (drift_mv + self.OFFSETS_MV[c] + self.SENS * i_ma) / k
            raw[5 + c] = v_mv / k
        raw += self.rng.normal(0.0, 0.25, N_RAW)                  # ruído após 192 médias
        return np.clip(raw, 0.0, ADC_COUNTS - 1)


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

        self.link = SerialLink()
        self.sim = Simulator()
        self.simulating = False
        self.next_sim = 0.0

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

        # ---- Aba Diagnóstico
        diag = QtWidgets.QWidget()
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
        self.next_sim = 0.0
        self.last_frame_t = None
        if checked:
            self.flash('Simulação ligada: 4 módulos com coberturas de transmitância diferente.')

    def on_quantity(self, *_):
        key = self.cmb_qty.currentData()
        rel = key in ('R', 'IR')
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
        if not self.zero_ok():
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
        src = 'simulacao' if self.simulating else (self.link.port or '?')
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
        f.write('# I_mA = ((A[1..4] - A0) * Vref/4096 - offset) / sensibilidade; V_mV = A[5..8] * Vref/4096 * divisor\n')
        cols = ['datahora', 't_s']
        for c in range(N_CELLS):
            cols += [f'V{c + 1}_mV', f'I{c + 1}_mA', f'P{c + 1}_mW', f'E{c + 1}_mWh']
        cols += [f'A{k}' for k in range(N_RAW)]
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

    def stop_recording(self):
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
        row += [f'{x:.1f}' for x in raw]
        try:
            self.rec_file.write(','.join(row) + '\n')
            self.rec_file.flush()
            self.rec_lines += 1
        except Exception as e:
            self.flash(f'Erro ao gravar: {e}', 10.0)
            self.btn_rec.setChecked(False)

    def save_view(self):
        key = self.cmb_qty.currentData()
        t, y = self.data.series(key, self.cfg)
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
                if key in ('R', 'IR'):
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
        if self.zero_acc is not None:
            self._zero_feed(raw)
        v, i, p = to_physical(raw, self.cfg, self.offsets)
        self.saturated = bool(np.any(raw < ADC_SAT_LOW) or np.any(raw > ADC_SAT_HIGH))
        self.data.append(t, v, i, p, raw)
        if self.rec_file is not None:
            self._record(t, v, i, p, raw)
        self.dirty = True

    def tick(self):
        now_mono = time.monotonic()
        frames = self.link.take()
        if self.simulating and not self.link.connected:
            if now_mono >= self.next_sim:
                self.next_sim = now_mono + SIM_PERIOD
                t = time.time()
                frames.append(Frame(t, self.sim.frame(t)))
        elif self.simulating and self.link.connected:
            self.chk_sim.setChecked(False)
        for fr in frames:
            self.process_raw(fr.t, fr.raw)
        if frames:
            self.update_cards()
        self.chk_sim.setVisible(not self.link.connected)
        self.update_status()
        if self.dirty and not self.paused and now_mono - self.last_plot >= PLOT_INTERVAL:
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
        elif not self.zero_ok():
            text = (f"<b style='color:{WARN_COLOR}'>Correntes sem zero</b>: cubra os módulos e clique em "
                    f"\"Zerar correntes…\"")
        else:
            text = f"<span style='color:{OK_COLOR}'>Medindo</span> · {self.data.n} leituras"
        if text != self.lbl_status.text():
            self.lbl_status.setText(text)

        if self.link.connected:
            conn = f"<span style='color:{OK_COLOR};'>●</span> STM32 conectado ({self.link.port})"
            tip = f'Linhas inválidas recebidas: {self.link.bad_lines}'
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

        if self.tabs.currentIndex() == 1:
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

    def update_plots(self):
        key = self.cmb_qty.currentData()
        t, y = self.data.series(key, self.cfg)
        for c, cv in enumerate(self.curves):
            if self.cards[c].chk.isChecked() and len(t):
                cv.setData(t, y[:, c], connect='finite')
            else:
                cv.setData([], [])
        pi = self.plot.getPlotItem()
        win = self.cmb_window.currentData()
        if len(t):
            t1 = t[-1]
            t0 = t1 - win if win else t[0]
            pi.setXRange(t0, t1 + 0.02 * max(t1 - t0, 10.0), padding=0)
            pi.enableAutoRange(axis='y')
        n = self.data.n
        if n:
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
