# -*- coding: utf-8 -*-
'''
QuadPV - corrente de curto-circuito (Isc) e tensão de circuito aberto (Voc) de 4 módulos
fotovoltaicos idênticos, lado a lado, para comparar coberturas (interface PySide6/PyQt +
pyqtgraph, no mesmo padrão do SOI Controller).

Dependências:  pip install -r requirements.txt   (numpy, pyserial, pyqtgraph, PySide6)
Firmware: firmware/quadpv_cube (3.x). O protocolo e as contas estão em quadpv_core.py.
'''
import os
import sys
import time
import zipfile
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from quadpv_core import (__version__, N_CELLS, ADC_SAT_HIGH, Config, SerialLink, Simulator, Series,
                         convert, delta_t, fw_info, trailing_mean, window_stats)

# Executáveis empacotados sem console (PyInstaller --windowed) não têm stdout/stderr.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

APP_ORG, APP_NAME = 'LCTM', 'QuadPV'
GUI_INTERVAL_MS = 100
PLOT_INTERVAL = 0.5
NO_DATA_TIMEOUT = 5.0
ISC_CTX_S = 10                 # s de Isc usados junto com cada Voc (estimativa de ΔT)
CELL_COLORS = ('#0072B2', '#D55E00', '#009E73', '#CC79A7')   # paleta segura para daltonismo
OK_COLOR, WARN_COLOR, BAD_COLOR = '#1a7f1a', '#d07000', '#c00000'

WINDOWS = (('5 min', 300), ('15 min', 900), ('1 h', 3600), ('6 h', 21600), ('24 h', 86400), ('Tudo', 0))
AVERAGES = (('1 s', 1), ('10 s', 10), ('1 min', 60), ('5 min', 300), ('15 min', 900))
QUANTITIES = (
    ('IR', 'Isc relativa', '%'),
    ('I', 'Isc', 'mA'),
    ('J', 'Densidade de corrente', 'mA/cm²'),
    ('VOC', 'Voc', 'mV'),
    ('DV', 'ΔVoc em relação à referência', 'mV'),
    ('DT', 'ΔT estimado em relação à referência', 'K'),
)
VOC_KEYS = ('VOC', 'DV', 'DT')

MEDIDAS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False)
    else os.path.dirname(os.path.abspath(__file__)), 'Medidas')


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
    '''Cartão com as leituras de um módulo (médias na janela escolhida).'''

    ROWS = (('IR', 'Isc relativa'), ('J', 'Densidade'), ('VOC', 'Voc'), ('DT', 'ΔT estimado'), ('N', 'Ruído (1 s)'))

    def __init__(self, color):
        super().__init__()
        self.setObjectName('card')
        self.setStyleSheet(f'#card{{border:1px solid #c8c8c8;border-left:7px solid {color};border-radius:6px;}}')
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        self.chk = QtWidgets.QCheckBox()
        self.chk.setChecked(True)
        self.chk.setToolTip('Mostrar este módulo nos gráficos')
        self.lbl_name = QtWidgets.QLabel()
        self.lbl_name.setStyleSheet(f'font-weight:bold;color:{color};')
        head.addWidget(self.chk)
        head.addWidget(self.lbl_name, 1)
        lay.addLayout(head)
        self.lbl_big = QtWidgets.QLabel('-')
        f = self.lbl_big.font()
        f.setPointSize(f.pointSize() + 9)
        f.setBold(True)
        self.lbl_big.setFont(f)
        self.lbl_big.setToolTip('Isc média na janela de média. A Isc absoluta varia com o sol; a incerteza\n'
                                'da medida está em "Ruído (1 s)" e no ± da Isc relativa.')
        lay.addWidget(self.lbl_big)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(0)
        self.vals = {}
        for row, (key, label) in enumerate(self.ROWS):
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

    @staticmethod
    def pm(m, e, fmt, unit):
        if m is None or not np.isfinite(m):
            return '-'
        if e is None or not np.isfinite(e):
            return f'{m:{fmt}} {unit}'
        return f'{m:{fmt}} ± {e:{fmt}} {unit}'

    def clear(self):
        self.lbl_big.setText('-')
        for v in self.vals.values():
            v.setText('-')


class ConfigDialog(QtWidgets.QDialog):
    '''Nome, área, shunt e divisor de cada módulo; tensão do ADC; parâmetros da estimativa de ΔT.'''

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configurar módulos')
        lay = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(N_CELLS, 4)
        self.table.setHorizontalHeaderLabels(['Nome', 'Área (cm²)', 'Shunt (Ω)', 'Divisor de V'])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setVerticalHeaderLabels([str(i + 1) for i in range(N_CELLS)])
        self.spins = []
        for r in range(N_CELLS):
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(cfg.names[r]))
            row = []
            for c, (val, lo, hi, dec) in enumerate(((cfg.area_cm2[r], 0, 10000, 2), (cfg.r_shunt[r], 0.01, 1000, 4),
                                                     (cfg.div[r], 0.01, 1000, 4)), start=1):
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(lo, hi)
                sp.setDecimals(dec)
                sp.setValue(val)
                if c == 1:
                    sp.setSpecialValueText('não informada')
                if c == 2:
                    sp.setToolTip('Meça cada shunt com o multímetro (4 fios, se puder): o erro do resistor\n'
                                  'entra direto na corrente. A calibração dos módulos também o absorve.')
                self.table.setCellWidget(r, c, sp)
                row.append(sp)
            self.spins.append(row)
        self.table.setMinimumWidth(600)
        self.table.setMinimumHeight(170)
        lay.addWidget(self.table)

        form = QtWidgets.QFormLayout()
        self.chk_vrefint = QtWidgets.QCheckBox('Tensão do ADC pela Vrefint (acompanha variações do 3,3 V)')
        self.chk_vrefint.setChecked(cfg.use_vrefint)
        form.addRow('', self.chk_vrefint)
        self.sp_vrefint = self._dspin(1100, 1300, cfg.vrefint_mv, 2, ' mV')
        self.sp_vrefint.setToolTip('Vrefint do chip (1,16 a 1,24 V, sem calibração de fábrica no F103).\n'
                                   'Use "Calibrar pela tensão do 3V3" para achar o valor do seu chip.')
        self.btn_cal_vref = QtWidgets.QPushButton('Calibrar pela tensão do 3V3…')
        self.btn_cal_vref.clicked.connect(self.cal_vref)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.sp_vrefint)
        row.addWidget(self.btn_cal_vref)
        form.addRow('Vrefint:', row)
        self.sp_vref = self._dspin(1000, 5000, cfg.vref_mv, 1, ' mV')
        form.addRow('Tensão do ADC, se fixa:', self.sp_vref)
        self.sp_ns = QtWidgets.QSpinBox()
        self.sp_ns.setRange(1, 200)
        self.sp_ns.setValue(cfg.ns)
        form.addRow('Células em série por módulo:', self.sp_ns)
        self.sp_n = self._dspin(1.0, 2.5, cfg.ideality, 2, '')
        self.sp_n.setToolTip('Fator de idealidade: desconta de ΔVoc a parte que vem da diferença de luz (Isc).')
        form.addRow('Fator de idealidade:', self.sp_n)
        self.sp_t = self._dspin(-20, 100, cfg.t_mod_c, 1, ' °C')
        form.addRow('Temperatura típica dos módulos:', self.sp_t)
        self.sp_dvdt = self._dspin(-5, -0.5, cfg.dvdt_cell, 2, ' mV/K')
        form.addRow('dVoc/dT por célula:', self.sp_dvdt)
        lay.addLayout(form)
        self.last_r = None
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _dspin(lo, hi, val, dec, suffix):
        sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(dec)
        sp.setValue(val)
        sp.setSuffix(suffix)
        return sp

    def cal_vref(self):
        if not self.last_r:
            QtWidgets.QMessageBox.information(self, 'Vrefint', 'Ainda não há leituras da placa.')
            return
        v, ok = QtWidgets.QInputDialog.getDouble(self, 'Calibrar a Vrefint',
                                                 'Tensão medida com o multímetro entre 3V3 e GND da Blue Pill (mV):',
                                                 3300.0, 2500.0, 3600.0, 1)
        if ok:
            self.sp_vrefint.setValue(v * self.last_r / 4096.0)

    def apply_to(self, cfg):
        for r in range(N_CELLS):
            item = self.table.item(r, 0)
            cfg.names[r] = (item.text().strip() if item else '') or f'Módulo {r + 1}'
            cfg.area_cm2[r] = self.spins[r][0].value()
            cfg.r_shunt[r] = self.spins[r][1].value()
            cfg.div[r] = self.spins[r][2].value()
        cfg.use_vrefint = self.chk_vrefint.isChecked()
        cfg.vrefint_mv = self.sp_vrefint.value()
        cfg.vref_mv = self.sp_vref.value()
        cfg.ns = self.sp_ns.value()
        cfg.ideality = self.sp_n.value()
        cfg.t_mod_c = self.sp_t.value()
        cfg.dvdt_cell = self.sp_dvdt.value()


# ----------------------------------------------------------------------------
# Janela principal
# ----------------------------------------------------------------------------
class QuadPVWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'QuadPV {__version__} - LCTM')
        self.resize(1250, 860)

        self.cfg = Config()
        self.offsets = None            # contagens dos shunts sem corrente (última janela de Voc)
        self.offsets_t = None
        # uma linha por segundo (módulos em curto)
        self.sec = Series({'i_raw': N_CELLS, 'i_err': N_CELLS, 'vs': N_CELLS, 'vdda': 1,
                           'a': N_CELLS, 'v': N_CELLS, 'r': 1, 'sa': N_CELLS, 'off': N_CELLS})
        # uma linha por medida de Voc
        self.voc = Series({'voc': N_CELLS, 'isc': N_CELLS, 'off': N_CELLS, 'vdda': 1})
        self.last_rx = time.monotonic()
        self.saturated = False
        self.paused = False
        self.last_plot = 0.0
        self.dirty = False
        self.last_r = None
        self.fw_mode = 'C'

        self.rec_files = None          # (segundos, Voc) durante a gravação
        self.rec_path = None
        self.rec_lines = 0
        self.session_files = []
        self.synced_port = None

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

    @staticmethod
    def _date_plot(title, ylabel):
        w = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem(orientation='bottom')})
        if title:
            w.setTitle(title)
        w.setLabel('left', ylabel)
        w.showGrid(x=True, y=True, alpha=0.3)
        w.getAxis('left').enableAutoSIPrefix(False)
        return w

    def _build_ui(self):
        pg.setConfigOptions(antialias=True, background='w', foreground='k')
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # Linha 1: amostra, porta, simulação, configuração, calibração
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
        self.cmb_port.setToolTip('Automática: procura o STM32 (USB VID 0x0483) e confirma com "?" que é um QuadPV.')
        self.refresh_ports()
        self.cmb_port.currentIndexChanged.connect(self.on_port)
        top.addWidget(self.cmb_port)
        top.addSpacing(10)
        self.chk_sim = QtWidgets.QCheckBox('Simular medidas')
        self.chk_sim.setStyleSheet('QCheckBox{font-weight:bold;color:#006400;}')
        self.chk_sim.setToolTip('Gera leituras simuladas quando não há STM32 conectado.')
        self.chk_sim.toggled.connect(self.on_simulate)
        top.addWidget(self.chk_sim)
        self.chk_sim_same = QtWidgets.QCheckBox('coberturas iguais')
        self.chk_sim_same.setToolTip('Simulação: todos os módulos com a mesma cobertura (para testar a calibração).')
        self.chk_sim_same.toggled.connect(lambda c: setattr(self.sim, 'identical', c))
        top.addWidget(self.chk_sim_same)
        top.addStretch(1)
        self.btn_cfg = QtWidgets.QPushButton('Configurar módulos…')
        self.btn_cfg.clicked.connect(self.open_config)
        top.addWidget(self.btn_cfg)
        self.btn_cal = QtWidgets.QPushButton('Calibrar módulos…')
        self.btn_cal.setToolTip('Com todos os módulos na mesma condição (mesma cobertura ou nenhuma),\n'
                                'mede as pequenas diferenças entre eles e passa a descontá-las.')
        self.btn_cal.clicked.connect(self.calibrate)
        top.addWidget(self.btn_cal)
        root.addLayout(top)

        # Linha 2: gravação
        rec = QtWidgets.QHBoxLayout()
        self.btn_rec = QtWidgets.QPushButton('●  Gravar')
        self.btn_rec.setCheckable(True)
        self.btn_rec.setMinimumWidth(110)
        self.btn_rec.setStyleSheet(
            'QPushButton{font-weight:bold;padding:4px 14px;border:1px solid #a33;border-radius:4px;}'
            'QPushButton:checked{background:#c62828;color:white;}')
        self.btn_rec.setToolTip('Grava cada leitura assim que chega: Medidas/<amostra>_QuadPV_<data>.csv\n'
                                'e as medidas de Voc em …_Voc.csv.')
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

        # Linha 3: medida e média
        ms = QtWidgets.QHBoxLayout()
        ms.addWidget(self._bold('Medida:'))
        self.cmb_mode = QtWidgets.QComboBox()
        self.cmb_mode.addItem('Isc contínua (Voc a cada intervalo)', 'C')
        self.cmb_mode.addItem('Voc contínua', 'O')
        self.cmb_mode.currentIndexChanged.connect(self.on_mode)
        ms.addWidget(self.cmb_mode)
        ms.addSpacing(10)
        ms.addWidget(QtWidgets.QLabel('Voc a cada'))
        self.sp_interval = QtWidgets.QSpinBox()
        self.sp_interval.setRange(0, 86400)
        self.sp_interval.setValue(60)
        self.sp_interval.setSuffix(' s')
        self.sp_interval.setSpecialValueText('só manual')
        self.sp_interval.setKeyboardTracking(False)
        self.sp_interval.setToolTip('A cada medida de Voc os módulos ficam abertos ~0,1 s, e o segundo\n'
                                    'correspondente fica sem leitura de Isc. A mesma janela mede o zero dos shunts.')
        self.sp_interval.valueChanged.connect(self.on_interval)
        ms.addWidget(self.sp_interval)
        self.btn_voc = QtWidgets.QPushButton('Medir Voc agora')
        self.btn_voc.clicked.connect(lambda: self.send_cmd('V'))
        ms.addWidget(self.btn_voc)
        ms.addSpacing(16)
        ms.addWidget(self._bold('Média:'))
        self.cmb_avg = QtWidgets.QComboBox()
        for name, n in AVERAGES:
            self.cmb_avg.addItem(name, n)
        self.cmb_avg.setCurrentIndex(2)
        self.cmb_avg.setToolTip('Média móvel dos cartões e dos gráficos de Isc. A Isc relativa aparece como\n'
                                'média ± erro padrão das razões de 1 s dentro da janela.')
        self.cmb_avg.currentIndexChanged.connect(self._refresh_all)
        ms.addWidget(self.cmb_avg)
        ms.addStretch(1)
        root.addLayout(ms)

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
            card = CellCard(CELL_COLORS[c])
            card.chk.toggled.connect(self._refresh_all)
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
        self.lbl_refcell = QtWidgets.QLabel('referência:')
        ctl.addWidget(self.lbl_refcell)
        self.cmb_refcell = QtWidgets.QComboBox()
        self.cmb_refcell.setToolTip('Módulo de referência das grandezas relativas (normalmente o de cobertura padrão).')
        self.cmb_refcell.currentIndexChanged.connect(self.on_refcell)
        ctl.addWidget(self.cmb_refcell)
        ctl.addSpacing(10)
        ctl.addWidget(QtWidgets.QLabel('Janela:'))
        self.cmb_window = QtWidgets.QComboBox()
        for name, sec in WINDOWS:
            self.cmb_window.addItem(name, sec)
        self.cmb_window.currentIndexChanged.connect(self._refresh_all)
        ctl.addWidget(self.cmb_window)
        self.chk_raw = QtWidgets.QCheckBox('mostrar leituras de 1 s')
        self.chk_raw.setToolTip('Desenha também as leituras de 1 s, em traço claro, atrás da média móvel.')
        self.chk_raw.toggled.connect(self._refresh_all)
        ctl.addWidget(self.chk_raw)
        ctl.addStretch(1)
        self.btn_pause = QtWidgets.QPushButton('Pausar')
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip('Congela o gráfico e libera zoom e arraste (a aquisição e a gravação continuam).')
        self.btn_pause.toggled.connect(self.on_pause)
        ctl.addWidget(self.btn_pause)
        self.btn_save = QtWidgets.QPushButton('Salvar gráfico')
        self.btn_save.setToolTip('Grava em Medidas o CSV e o PNG do que está visível no gráfico.')
        self.btn_save.clicked.connect(self.save_view)
        ctl.addWidget(self.btn_save)
        lay.addLayout(ctl)

        self.plot = self._date_plot(None, '')
        pi = self.plot.getPlotItem()
        pi.setLabel('bottom', 'Hora')
        pi.setMouseEnabled(False, False)
        pi.setMenuEnabled(False)
        pi.hideButtons()
        self.curves_raw = []
        self.curves = []
        for c in range(N_CELLS):
            col = QtGui.QColor(CELL_COLORS[c])
            col.setAlpha(70)
            cr = pi.plot(pen=pg.mkPen(col, width=1))
            cr.setDownsampling(auto=True, method='peak')
            cr.setClipToView(True)
            self.curves_raw.append(cr)
        for c in range(N_CELLS):
            cv = pi.plot(pen=pg.mkPen(CELL_COLORS[c], width=2))
            cv.setDownsampling(auto=True, method='peak')
            cv.setClipToView(True)
            self.curves.append(cv)
        lay.addWidget(self.plot, 1)

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
        self.plot_noise = self._date_plot('Incerteza de cada leitura de 1 s (erro padrão da média)', 'mA')
        self.curves_noise = [self.plot_noise.plot(pen=pg.mkPen(CELL_COLORS[c], width=1.5)) for c in range(N_CELLS)]
        dl.addWidget(self.plot_noise, 1)
        self.plot_off = self._date_plot('Zero dos shunts (medido em cada janela de Voc)', 'contagens')
        self.plot_off.setXLink(self.plot_noise)
        self.curves_off = [self.plot_off.plot(pen=pg.mkPen(CELL_COLORS[c], width=1.5), symbol='o', symbolSize=5,
                                              symbolBrush=CELL_COLORS[c], symbolPen=None) for c in range(N_CELLS)]
        dl.addWidget(self.plot_off, 1)
        self.plot_vdd = self._date_plot('Tensão de alimentação do ADC (pela Vrefint)', 'mV')
        self.plot_vdd.setXLink(self.plot_noise)
        self.curve_vdd = self.plot_vdd.plot(pen=pg.mkPen('#555', width=1.5))
        dl.addWidget(self.plot_vdd, 1)

        sb = self.statusBar()
        self.lbl_sb_cal = QtWidgets.QLabel()
        self.lbl_sb_conn = QtWidgets.QLabel()
        self.lbl_sb_cal.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.lbl_sb_conn.setTextFormat(QtCore.Qt.TextFormat.RichText)
        sb.addPermanentWidget(self.lbl_sb_cal)
        sb.addPermanentWidget(self.lbl_sb_conn)
        self.last_sb_state = None
        self.mode_widgets = (self.cmb_mode, self.sp_interval, self.btn_voc)

        self.apply_names()
        self.on_quantity()

    # --------------------------------------------------------- Auxiliares
    def flash(self, text, seconds=4.0):
        self.statusBar().showMessage(text, int(seconds * 1000))

    def _refresh_all(self, *_):
        self.dirty = True
        self.update_cards()

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

    def avg_n(self):
        return int(self.cmb_avg.currentData() or 1)

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

    def board_ok(self):
        return (self.simulating and not self.link.connected) or \
            (self.link.connected and self.link.identity == 'QuadPV')

    # ---------------------------------------------------------- Grandezas
    def isc(self):
        '''Isc calibrada (mA), n x 4.'''
        return self.sec.get('i_raw') / np.asarray(self.cfg.cal_isc)

    def series(self, key):
        '''(t, y n x 4) da grandeza pedida, sem média móvel.'''
        ref = self.cfg.ref_cell
        if key in VOC_KEYS:
            t = self.voc.times()
            voc = self.voc.get('voc')
            if key == 'VOC':
                return t, voc - np.asarray(self.cfg.cal_voc)
            out = np.full_like(voc, np.nan)
            for k in range(len(t)):
                dv, dt = delta_t(voc[k], self.voc.get('isc')[k], self.cfg)
                out[k] = dv if key == 'DV' else dt
            return t, out
        t = self.sec.times()
        i = self.isc()
        if key == 'I':
            return t, i
        if key == 'J':
            area = np.array([a if a > 0 else np.nan for a in self.cfg.area_cm2])
            return t, i / area
        with np.errstate(divide='ignore', invalid='ignore'):
            iref = i[:, ref:ref + 1]
            return t, np.where(np.abs(iref) > 0.5, 100.0 * i / iref, np.nan)

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
        if current and idx < 0:
            self.cmb_port.addItem(f'{current} (ausente)', current)
            idx = self.cmb_port.count() - 1
        self.cmb_port.setCurrentIndex(max(idx, 0))
        self.cmb_port.blockSignals(False)

    def on_port(self, *_):
        self.link.set_preferred(self.cmb_port.currentData())

    def on_simulate(self, checked):
        self.simulating = checked
        self.sim.reset()
        self.sim.identical = self.chk_sim_same.isChecked()
        if checked:
            self.sync_firmware()
            self.flash('Simulação: coberturas de 100 %, 97 %, 93 % e 102 % de transmitância, que aquecem os módulos '
                       'de forma diferente; os módulos também diferem um pouco entre si.', 10.0)

    def send_cmd(self, text):
        if not self.board_ok():
            self.flash('Placa não conectada (ou firmware incompatível).')
            return
        if self.link.connected:
            self.link.write(text)
        else:
            self.sim.command(text)

    def sync_firmware(self):
        self.send_cmd(f'M {self.cmb_mode.currentData()}')
        self.send_cmd(f'T {self.sp_interval.value()}')

    def on_mode(self, *_):
        if self.board_ok():
            self.send_cmd(f'M {self.cmb_mode.currentData()}')

    def on_interval(self, *_):
        if self.board_ok():
            self.send_cmd(f'T {self.sp_interval.value()}')

    def on_quantity(self, *_):
        key = self.cmb_qty.currentData()
        name, unit = next((n, u) for k, n, u in QUANTITIES if k == key)
        self.plot.getPlotItem().setLabel('left', f'{name} ({unit})')
        if key == 'J' and not any(a > 0 for a in self.cfg.area_cm2):
            self.flash('Informe as áreas em "Configurar módulos…" para ver a densidade de corrente.')
        self.chk_raw.setEnabled(key not in VOC_KEYS)
        self.dirty = True

    def on_refcell(self, idx):
        if idx >= 0:
            self.cfg.ref_cell = idx
            self._refresh_all()

    def on_pause(self, checked):
        self.paused = checked
        self.btn_pause.setText('Continuar' if checked else 'Pausar')
        self.plot.getPlotItem().setMouseEnabled(checked, checked)
        if not checked:
            self.dirty = True

    def open_config(self):
        dlg = ConfigDialog(self.cfg, self)
        dlg.last_r = self.last_r
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        dlg.apply_to(self.cfg)
        self.apply_names()
        self.recompute()
        self.save_settings()
        if self.rec_files is not None:
            self.flash('A configuração nova vale a partir de agora; o cabeçalho do CSV em gravação tem a antiga.', 8.0)

    def recompute(self):
        '''Refaz as correntes guardadas com a configuração atual (shunt, Vrefint), a partir das contagens.'''
        n = self.sec.n
        if n == 0:
            self._refresh_all()
            return
        r = self.sec.get('r')[:, 0]
        k = np.where(self.cfg.use_vrefint & (r > 100), self.cfg.vrefint_mv / np.maximum(r, 1), self.cfg.vref_mv / 4096.0)
        rs = np.asarray(self.cfg.r_shunt)
        a, off, sa = self.sec.get('a'), self.sec.get('off'), self.sec.get('sa')
        self.sec.d['i_raw'][:n] = (a - off) * k[:, None] / rs
        self.sec.d['vs'][:n] = self.sec.get('v') * k[:, None] * np.asarray(self.cfg.div)
        self.sec.d['vdda'][:n, 0] = k * 4096.0
        self.sec.d['i_err'][:n] = sa / np.sqrt(6000.0) * k[:, None] / rs
        self._refresh_all()

    def calibrate(self):
        n_av = max(self.avg_n(), 60)
        if self.sec.n < 30:
            QtWidgets.QMessageBox.information(self, 'Calibrar módulos', 'Meça pelo menos 30 s antes de calibrar.')
            return
        i = self.sec.get('i_raw')[-n_av:]
        ref = self.cfg.ref_cell
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio = np.nanmean(i / i[:, ref:ref + 1], axis=0)
        t_lo = self.sec.times()[-n_av:][0]
        tv = self.voc.times()
        sel = tv >= t_lo
        voc_off = None
        if np.any(sel):
            voc = self.voc.get('voc')[sel]
            voc_off = np.nanmean(voc - voc[:, ref:ref + 1], axis=0)
        names = self.cfg.names
        lines = [f'{names[c]}: Isc × {ratio[c]:.4f}' + (f', Voc {voc_off[c]:+.1f} mV' if voc_off is not None else '')
                 for c in range(N_CELLS)]
        msg = (f'Usa os últimos {len(i)} s de Isc' + (f' e {int(np.sum(sel))} medida(s) de Voc' if voc_off is not None
                                                      else ' (nenhuma medida de Voc nesse período: Voc não será calibrado)')
               + f', em relação a "{names[ref]}".\n\nTodos os módulos estavam na mesma condição '
               f'(mesma cobertura ou nenhuma)?\n\n' + '\n'.join(lines) +
               '\n\nSim: aplica.  Redefinir: volta a não calibrar.')
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle('Calibrar módulos')
        box.setText(msg)
        b_yes = box.addButton('Sim, aplicar', QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        b_reset = box.addButton('Redefinir', QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton('Cancelar', QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is b_yes:
            if not np.all(np.isfinite(ratio)):
                self.flash('Calibração não aplicada: algum módulo sem corrente.', 8.0)
                return
            self.cfg.cal_isc = [float(x) for x in ratio]
            if voc_off is not None and np.all(np.isfinite(voc_off)):
                self.cfg.cal_voc = [float(x) for x in voc_off]
            self.cfg.cal_time = datetime.now().strftime('%Y-%m-%d %H:%M') + f' (ref.: {names[ref]})'
        elif box.clickedButton() is b_reset:
            self.cfg.cal_isc = [1.0] * N_CELLS
            self.cfg.cal_voc = [0.0] * N_CELLS
            self.cfg.cal_time = ''
        else:
            return
        self.save_settings()
        self._refresh_all()
        if self.rec_files is not None:
            self.rec_files[0].write(f'# Calibracao alterada em {datetime.now().isoformat(timespec="seconds")}: '
                                    f'Isc={self.cfg.cal_isc}, Voc={self.cfg.cal_voc}\n')

    def reset_data(self):
        self.sec.clear()
        self.voc.clear()
        for card in self.cards:
            card.clear()
        self.dirty = True
        self.flash('Gráficos reiniciados.')

    # ----------------------------------------------------------- Mensagens
    def handle(self, msg):
        if msg.kind == '#':
            info = fw_info(msg.data)
            if info is None:
                self.flash(f'Firmware: {msg.data}', 6.0)
            else:
                self.fw_mode = info.get('modo', self.fw_mode)
            return
        rd = msg.data
        self.last_rx = time.monotonic()
        self.last_r = rd.r
        self.saturated = bool(np.any(rd.a > ADC_SAT_HIGH) or np.any(rd.v > ADC_SAT_HIGH))
        if msg.kind == 'V' or rd.mode == 'O':
            self.process_voc(msg.t, rd)
        else:
            self.process_isc(msg.t, rd)

    def process_voc(self, t, rd):
        self.offsets = rd.a.copy()                 # shunts sem corrente: zero
        self.offsets_t = t
        k = self.cfg.k_mv(rd.r)
        voc = rd.v * k * np.asarray(self.cfg.div)
        i = self.sec.get('i_raw')
        isc_ctx = np.nanmean(i[-ISC_CTX_S:], axis=0) if len(i) else np.full(N_CELLS, np.nan)
        isc_ctx = isc_ctx / np.asarray(self.cfg.cal_isc)
        self.voc.append(t, voc=voc, isc=isc_ctx, off=rd.a, vdda=k * 4096.0)
        if self.rec_files is not None:
            self._record_voc(t, rd, voc, isc_ctx)
        self.dirty = True

    def process_isc(self, t, rd):
        off = self.offsets if self.offsets is not None else np.zeros(N_CELLS)
        c = convert(rd, self.cfg, off)
        self.sec.append(t, i_raw=c['i_raw'], i_err=c['i_err'], vs=c['v'], vdda=c['vdda'],
                        a=rd.a, v=rd.v, r=rd.r, sa=rd.sa, off=off)
        if self.rec_files is not None:
            self._record_sec(t, rd, c, off)
        self.dirty = True

    def tick(self):
        now = time.monotonic()
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
        got = False
        for msg in msgs:
            if msg.kind in ('D', 'V') and self.link.connected and self.link.identity == 'incompatível':
                continue
            self.handle(msg)
            got |= msg.kind in ('D', 'V')
        if got:
            self.update_cards()
        self.chk_sim.setVisible(not self.link.connected)
        self.chk_sim_same.setVisible(not self.link.connected and self.simulating)
        for w in self.mode_widgets:
            w.setEnabled(self.board_ok())
        self.update_status()
        if self.dirty and not self.paused and now - self.last_plot >= PLOT_INTERVAL:
            self.last_plot = now
            self.dirty = False
            self.update_plots()

    # ------------------------------------------------------------ Exibição
    def update_cards(self):
        n_av = self.avg_n()
        names = ('IR', 'J')
        stats = {}
        for key in ('I',) + names:
            t, y = self.series(key)
            stats[key] = window_stats(y, n_av)
        tv, voc = self.series('VOC')
        _, dts = self.series('DT')
        noise = self.sec.get('i_err')[-1] if self.sec.n else np.full(N_CELLS, np.nan)
        for c, card in enumerate(self.cards):
            m, e = stats['I'][0][c], stats['I'][1][c]
            card.lbl_big.setText(f'{m:.3f} mA' if np.isfinite(m) else '-')
            card.vals['IR'].setText(CellCard.pm(stats['IR'][0][c], stats['IR'][1][c], '.3f', '%'))
            card.vals['J'].setText(CellCard.pm(stats['J'][0][c], None, '.4f', 'mA/cm²'))
            card.vals['VOC'].setText(f'{voc[-1, c]:.1f} mV' if len(tv) else '-')
            card.vals['DT'].setText(f'{dts[-1, c]:+.2f} K' if len(tv) and np.isfinite(dts[-1, c]) else '-')
            card.vals['N'].setText(f'{noise[c]:.4f} mA' if np.isfinite(noise[c]) else '-')
        if self.rec_files is not None:
            self.lbl_rec.setText(f'Gravando: {os.path.basename(self.rec_path)}  ({self.rec_lines} leituras)')
            self.lbl_rec.setStyleSheet(f'color:{BAD_COLOR};font-weight:bold;')

    def update_status(self):
        receiving = (self.link.connected or self.simulating) and time.monotonic() - self.last_rx < NO_DATA_TIMEOUT
        if not (self.link.connected or self.simulating):
            text = f"<span style='color:{WARN_COLOR}'>Aguardando a placa…</span>"
        elif self.link.connected and self.link.identity == 'incompatível':
            text = (f"<b style='color:{BAD_COLOR}'>Firmware incompatível</b>: grave o firmware 3.x "
                    f"(firmware/quadpv_cube)")
        elif not receiving:
            text = f"<b style='color:{BAD_COLOR}'>Sem dados há mais de {NO_DATA_TIMEOUT:.0f} s</b> (confira o firmware e o cabo)"
        elif self.saturated:
            text = f"<b style='color:{BAD_COLOR}'>ADC no limite (3,3 V)</b>: confira a tensão dos módulos e os shunts"
        elif self.offsets is None:
            text = f"<span style='color:{WARN_COLOR}'>Aguardando a 1ª medida de Voc para o zero dos shunts…</span>"
        else:
            age = time.time() - self.offsets_t
            text = (f"<span style='color:{OK_COLOR}'>Medindo</span> · {self.sec.n} leituras de Isc, "
                    f"{self.voc.n} de Voc · zero dos shunts há {age:.0f} s")
        if text != self.lbl_status.text():
            self.lbl_status.setText(text)

        if self.link.connected:
            ident = self.link.identity
            if ident == 'QuadPV':
                ver = self.link.fw.get('version', '?') if self.link.fw else '3.x'
                what = f'QuadPV fw {ver}'
            elif ident == 'incompatível':
                what = f"<span style='color:{BAD_COLOR};'>firmware incompatível</span>"
            else:
                what = f"<span style='color:{WARN_COLOR};'>identidade não confirmada</span>"
            conn = f"<span style='color:{OK_COLOR};'>●</span> {what} em {self.link.port}"
            tip = f'Linhas inválidas recebidas: {self.link.bad_lines}'
        elif self.simulating:
            conn, tip = "<span style='color:#1f5fbf;'>●</span> Simulação (sem STM32)", ''
        else:
            conn = f"<span style='color:{WARN_COLOR};'>●</span> Placa desconectada, procurando…"
            tip = self.link.last_error or ''
        cal = (f'Calibração: {self.cfg.cal_time}' if self.cfg.calibrated()
               else f"<span style='color:{WARN_COLOR};'>Módulos não calibrados</span>")
        state = (conn, tip, cal)
        if state != self.last_sb_state:
            self.last_sb_state = state
            self.lbl_sb_conn.setText(conn)
            self.lbl_sb_conn.setToolTip(tip)
            self.lbl_sb_cal.setText(cal)
            self.lbl_sb_cal.setToolTip('Calibração dos módulos em condição idêntica (botão "Calibrar módulos…").')

        if self.tabs.currentWidget() is self.diag_page:
            parts = []
            if self.sec.n:
                vdd = self.sec.get('vdda')[-1, 0]
                parts.append(f'Alimentação do ADC: {vdd:.1f} mV')
                parts.append('ruído de uma amostra (σ, contagens): ' +
                             ', '.join(f'{x:.2f}' for x in self.sec.get('sa')[-1]))
            if self.offsets is not None:
                parts.append('zero dos shunts (contagens): ' + ', '.join(f'{x:.3f}' for x in self.offsets))
            parts.append(f'linhas inválidas: {self.link.bad_lines}')
            txt = ' · '.join(parts)
            if txt != self.lbl_diag.text():
                self.lbl_diag.setText(txt)

    def update_plots(self):
        key = self.cmb_qty.currentData()
        t, y = self.series(key)
        is_voc = key in VOC_KEYS
        n_av = self.avg_n()
        ys = y if is_voc else trailing_mean(y, n_av)
        show_raw = self.chk_raw.isChecked() and not is_voc and n_av > 1
        for c in range(N_CELLS):
            vis = self.cards[c].chk.isChecked() and len(t) > 0
            cv, cr = self.curves[c], self.curves_raw[c]
            if is_voc:
                cv.setSymbol('o')
                cv.setSymbolSize(6)
                cv.setSymbolBrush(CELL_COLORS[c])
                cv.setSymbolPen(None)
            else:
                cv.setSymbol(None)
            cv.setData(t, ys[:, c], connect='finite') if vis else cv.setData([], [])
            cr.setData(t, y[:, c], connect='finite') if vis and show_raw else cr.setData([], [])
        pi = self.plot.getPlotItem()
        win = self.cmb_window.currentData()
        ends = [x for x in (self.sec.times()[-1:], self.voc.times()[-1:]) if len(x)]
        if ends:
            t_end = max(float(x[0]) for x in ends)
            starts = [float(x[0]) for x in (self.sec.times()[:1], self.voc.times()[:1]) if len(x)]
            t0 = t_end - win if win else min(starts)
            pi.setXRange(t0, t_end + 0.02 * max(t_end - t0, 10.0), padding=0)
            pi.enableAutoRange(axis='y')
        # diagnóstico
        ts = self.sec.times()
        err = self.sec.get('i_err')
        for c, cv in enumerate(self.curves_noise):
            cv.setData(ts, err[:, c]) if len(ts) else cv.setData([], [])
        tv = self.voc.times()
        off = self.voc.get('off')
        for c, cv in enumerate(self.curves_off):
            cv.setData(tv, off[:, c]) if len(tv) else cv.setData([], [])
        self.curve_vdd.setData(ts, self.sec.get('vdda')[:, 0]) if len(ts) else self.curve_vdd.setData([], [])

    # ----------------------------------------------------------- Gravação
    def on_record(self, checked):
        if checked:
            self.start_recording()
        else:
            self.stop_recording()

    def _uncheck_rec(self):
        self.btn_rec.blockSignals(True)
        self.btn_rec.setChecked(False)
        self.btn_rec.blockSignals(False)

    def start_recording(self):
        os.makedirs(MEDIDAS_DIR, exist_ok=True)
        now = datetime.now()
        self.rec_path = os.path.join(MEDIDAS_DIR, self._stem(f"QuadPV_{now.strftime('%Y%m%d_%H%M%S')}") + '.csv')
        voc_path = self.rec_path[:-4] + '_Voc.csv'
        try:
            fs = open(self.rec_path, 'w', encoding='utf-8', newline='')
            fv = open(voc_path, 'w', encoding='utf-8', newline='')
        except Exception as e:
            self.flash(f'Erro ao criar os arquivos: {e}', 8.0)
            self._uncheck_rec()
            return
        if self.link.connected:
            ver = self.link.fw.get('version', '?') if self.link.fw else '3.x'
            src = f'{self.link.port}, firmware {ver}'
        else:
            src = 'simulacao'
        head = [f'QuadPV {__version__} - inicio {now.strftime("%Y-%m-%d %H:%M:%S")} - fonte: {src}',
                f'Amostra: {self.ed_sample.text().strip()}']
        if self.ed_note.text().strip():
            head.append(f'Observacao: {self.ed_note.text().strip()}')
        head += self.cfg.header_lines()
        for f in (fs, fv):
            for line in head:
                f.write(f'# {line}\n')
        fs.write('# I = (a - zero) * k / R_shunt / calibracao;  k = Vrefint / r (mV por contagem);  zero = a na ultima janela de Voc\n')
        fs.write('# Ierr = erro padrao da media de 1 s (sigma / raiz(n)); a, v, r, sigma = contagens do ADC\n')
        cols = ['datahora', 't_s'] + [f'I{c + 1}_mA' for c in range(N_CELLS)] + [f'Ierr{c + 1}_mA' for c in range(N_CELLS)]
        cols += [f'Vcurto{c + 1}_mV' for c in range(N_CELLS)] + ['VDDA_mV']
        cols += [f'a{c + 1}' for c in range(N_CELLS)] + [f'v{c + 1}' for c in range(N_CELLS)] + ['r']
        cols += [f'zero{c + 1}' for c in range(N_CELLS)] + [f'sigma_a{c + 1}' for c in range(N_CELLS)] + ['n']
        fs.write(','.join(cols) + '\n')
        fv.write('# Voc com a calibracao dos modulos; dT estimado pela diferenca de Voc descontado o efeito da luz '
                 '(Isc media dos 10 s anteriores)\n')
        cols = ['datahora'] + [f'Voc{c + 1}_mV' for c in range(N_CELLS)] + [f'dVoc{c + 1}_mV' for c in range(N_CELLS)]
        cols += [f'dT{c + 1}_K' for c in range(N_CELLS)] + [f'Isc10s{c + 1}_mA' for c in range(N_CELLS)]
        cols += [f'zero{c + 1}' for c in range(N_CELLS)] + [f'v{c + 1}' for c in range(N_CELLS)] + ['r', 'n']
        fv.write(','.join(cols) + '\n')
        fs.flush()
        fv.flush()
        self.rec_files = (fs, fv)
        self.rec_t0 = None
        self.rec_lines = 0
        self.btn_rec.setText('■  Parar')
        self.ed_note.setEnabled(False)
        self._session_add(self.rec_path, voc_path)
        self.flash(f'Gravando em {self.rec_path}', 6.0)

    def stop_recording(self):
        if self.rec_files is not None:
            for f in self.rec_files:
                try:
                    f.close()
                except Exception:
                    pass
            self.flash(f'{self.rec_lines} leituras gravadas em {self.rec_path}', 8.0)
        self.rec_files = None
        self.btn_rec.setText('●  Gravar')
        self.ed_note.setEnabled(True)
        self.lbl_rec.setText('Não gravando')
        self.lbl_rec.setStyleSheet('color:#666;')

    def _write(self, f, row):
        try:
            f.write(','.join(row) + '\n')
            f.flush()
        except Exception as e:
            self.flash(f'Erro ao gravar: {e}', 10.0)
            self.btn_rec.setChecked(False)

    def _record_sec(self, t, rd, c, off):
        if self.rec_t0 is None:
            self.rec_t0 = t
        row = [datetime.fromtimestamp(t).isoformat(timespec='milliseconds'), f'{t - self.rec_t0:.3f}']
        row += [f'{x:.5f}' for x in c['i']] + [f'{x:.5f}' for x in c['i_err']]
        row += [f'{x:.2f}' for x in c['v']] + [f'{c["vdda"]:.2f}']
        row += [f'{x:.3f}' for x in rd.a] + [f'{x:.3f}' for x in rd.v] + [f'{rd.r:.3f}']
        row += [f'{x:.3f}' for x in off] + [f'{x:.2f}' for x in rd.sa] + [str(rd.n)]
        self._write(self.rec_files[0], row)
        self.rec_lines += 1

    def _record_voc(self, t, rd, voc, isc_ctx):
        dv, dt = delta_t(voc, isc_ctx, self.cfg)
        vc = voc - np.asarray(self.cfg.cal_voc)
        fmt = (lambda x, p: '' if not np.isfinite(x) else f'{x:.{p}f}')
        row = [datetime.fromtimestamp(t).isoformat(timespec='milliseconds')]
        row += [fmt(x, 2) for x in vc] + [fmt(x, 2) for x in dv] + [fmt(x, 3) for x in dt] + [fmt(x, 4) for x in isc_ctx]
        row += [f'{x:.3f}' for x in rd.a] + [f'{x:.3f}' for x in rd.v] + [f'{rd.r:.3f}', str(rd.n)]
        self._write(self.rec_files[1], row)

    def save_view(self):
        key = self.cmb_qty.currentData()
        t, y = self.series(key)
        if len(t) == 0:
            self.flash('Não há dados para salvar.')
            return
        n_av = self.avg_n()
        ys = y if key in VOC_KEYS else trailing_mean(y, n_av)
        x0, x1 = self.plot.getPlotItem().getViewBox().viewRange()[0]
        m = (t >= x0) & (t <= x1)
        now = datetime.now()
        os.makedirs(MEDIDAS_DIR, exist_ok=True)
        stem = self._stem(f"Grafico_{key}_{now.strftime('%Y%m%d_%H%M%S')}")
        csv_path = os.path.join(MEDIDAS_DIR, stem + '.csv')
        png_path = os.path.join(MEDIDAS_DIR, stem + '.png')
        name, unit = next((nm, u) for k, nm, u in QUANTITIES if k == key)
        try:
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                f.write(f'# {name} ({unit}) - QuadPV {__version__} - {now.strftime("%Y-%m-%d %H:%M:%S")}\n')
                if key not in VOC_KEYS:
                    f.write(f'# media movel de {n_av} s; colunas _1s = leituras de 1 s\n')
                f.write(f'# referencia: {self.cfg.names[self.cfg.ref_cell]}; calibracao: {self.cfg.cal_time or "nenhuma"}\n')
                hdr = ['datahora'] + [f'"{nm}"' for nm in self.cfg.names]
                if key not in VOC_KEYS:
                    hdr += [f'"{nm}_1s"' for nm in self.cfg.names]
                f.write(','.join(hdr) + '\n')
                for k in np.nonzero(m)[0]:
                    vals = list(ys[k]) + ([] if key in VOC_KEYS else list(y[k]))
                    f.write(datetime.fromtimestamp(t[k]).isoformat(timespec='seconds') + ','
                            + ','.join('' if not np.isfinite(v) else f'{v:.6g}' for v in vals) + '\n')
            self.plot.grab().save(png_path)
            self._session_add(csv_path, png_path)
            self.flash(f'Gráfico salvo: {csv_path}', 6.0)
        except Exception as e:
            self.flash(f'Erro ao salvar: {e}')

    def finish_experiment(self):
        if self.rec_files is not None:
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
        QtWidgets.QMessageBox.information(self, 'Experimento finalizado',
                                          f'{len(files)} arquivo(s) compactado(s) em:\n{zip_path}\n\n{names}\n\n'
                                          'Os originais continuam na pasta Medidas.')

    # ---------------------------------------------------- Preferências
    def load_settings(self):
        st = QtCore.QSettings(APP_ORG, APP_NAME)

        def num(name, default):
            try:
                v = st.value(name)
                return default if v is None else float(v)
            except Exception:
                return default

        def flag(name, default):
            v = st.value(name)
            return default if v is None else str(v).lower() in ('true', '1')

        try:
            c = self.cfg
            c.use_vrefint = flag('use_vrefint', c.use_vrefint)
            c.vref_mv = num('vref_mv', c.vref_mv)
            c.vrefint_mv = num('vrefint_mv', c.vrefint_mv)
            c.ns = int(num('ns', c.ns))
            c.ideality = num('ideality', c.ideality)
            c.t_mod_c = num('t_mod_c', c.t_mod_c)
            c.dvdt_cell = num('dvdt_cell', c.dvdt_cell)
            c.ref_cell = int(num('ref_cell', 0)) % N_CELLS
            c.cal_time = str(st.value('cal_time', '') or '')
            for i in range(N_CELLS):
                c.names[i] = str(st.value(f'name{i}', c.names[i]))
                c.area_cm2[i] = num(f'area{i}', c.area_cm2[i])
                c.r_shunt[i] = num(f'rshunt{i}', c.r_shunt[i])
                c.div[i] = num(f'div{i}', c.div[i])
                c.cal_isc[i] = num(f'cal_isc{i}', 1.0)
                c.cal_voc[i] = num(f'cal_voc{i}', 0.0)
            self.ed_sample.setText(str(st.value('sample_name', '') or ''))
            for w, name, default in ((self.cmb_qty, 'qty', 0), (self.cmb_window, 'window', 0), (self.cmb_avg, 'avg', 2),
                                     (self.cmb_mode, 'mode', 0)):
                w.blockSignals(True)
                w.setCurrentIndex(int(num(name, default)))
                w.blockSignals(False)
            self.sp_interval.blockSignals(True)
            self.sp_interval.setValue(int(num('voc_interval', 60)))
            self.sp_interval.blockSignals(False)
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
        self.on_quantity()

    def save_settings(self):
        st = QtCore.QSettings(APP_ORG, APP_NAME)
        c = self.cfg
        for name in ('use_vrefint', 'vref_mv', 'vrefint_mv', 'ns', 'ideality', 't_mod_c', 'dvdt_cell', 'ref_cell', 'cal_time'):
            st.setValue(name, getattr(c, name))
        for i in range(N_CELLS):
            st.setValue(f'name{i}', c.names[i])
            st.setValue(f'area{i}', c.area_cm2[i])
            st.setValue(f'rshunt{i}', c.r_shunt[i])
            st.setValue(f'div{i}', c.div[i])
            st.setValue(f'cal_isc{i}', c.cal_isc[i])
            st.setValue(f'cal_voc{i}', c.cal_voc[i])
        st.setValue('sample_name', self.ed_sample.text())
        st.setValue('qty', self.cmb_qty.currentIndex())
        st.setValue('window', self.cmb_window.currentIndex())
        st.setValue('avg', self.cmb_avg.currentIndex())
        st.setValue('mode', self.cmb_mode.currentIndex())
        st.setValue('voc_interval', self.sp_interval.value())
        st.setValue('port', self.cmb_port.currentData() or '')
        st.setValue('geometry', self.saveGeometry())

    def warn_not_found(self):
        if not self.link.connected and not self.simulating:
            self.flash('Placa não encontrada. Conecte a Blue Pill ou marque "Simular medidas" para testar.', 10.0)

    def closeEvent(self, event):
        if self.rec_files is not None:
            ans = QtWidgets.QMessageBox.question(self, 'Gravação em andamento', 'Há uma gravação em andamento. Parar e fechar?')
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


def main():
    app = pg.mkQApp('QuadPV - LCTM')
    win = QuadPVWindow()
    win.show()
    QtCore.QTimer.singleShot(2500, win.warn_not_found)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
