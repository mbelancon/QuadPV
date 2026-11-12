# -*- coding: utf-8 -*-
'''
QuadPV - núcleo sem interface gráfica: protocolo do firmware 3.x, conversão das contagens,
calibração, estimativa de temperatura, comunicação serial e simulação.

O firmware manda uma linha por segundo e uma por janela de Voc:

    D n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 ... s9     módulos em curto (Isc)
    V n m  a1 a2 a3 a4  v1 v2 v3 v4  r  s1 ... s9     módulos em aberto (Voc)

n = varreduras promediadas (6000 por segundo, 400 na janela de Voc); m = modo (C/O);
a = tensão nos shunts, v = tensão dos módulos, r = Vrefint, s = desvio-padrão de uma amostra
de cada canal; tudo em contagens do ADC de 12 bits.
'''
import math
import os
import threading
import time
from collections import deque, namedtuple

import numpy as np
import serial
from serial.tools import list_ports

__version__ = '3.0.0'

N_CELLS = 4
ADC_COUNTS = 4096.0
ADC_SAT_HIGH = 4085.0
STM32_VID = 0x0483
KNOWN_VIDS = {0x0483, 0x1EAF, 0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}
BAUD_RATE = 115200
RECONNECT_INTERVAL = 3.0
IDENTIFY_TIMEOUT = 2.5
REJECT_HOLD = 30.0
K_B_OVER_Q = 8.617333262e-2       # mV/K (constante de Boltzmann / carga elementar)

Msg = namedtuple('Msg', 'kind t data')
# kind 'D' ou 'V': data = Reading; '#': data = texto


class Reading:
    '''Uma linha D ou V já separada em canais (contagens).'''
    __slots__ = ('kind', 'n', 'mode', 'a', 'v', 'r', 'sa', 'sv', 'sr')

    def __init__(self, kind, n, mode, vals, sig):
        self.kind, self.n, self.mode = kind, n, mode
        self.a, self.v, self.r = vals[0:4], vals[4:8], float(vals[8])
        self.sa, self.sv, self.sr = sig[0:4], sig[4:8], float(sig[8])


def parse_message(line, t):
    '''Uma linha recebida -> Msg, ou None se não for reconhecida.'''
    if isinstance(line, (bytes, bytearray)):
        line = line.decode('ascii', 'ignore')
    s = line.strip()
    if not s:
        return None
    if s.startswith('#'):
        return Msg('#', t, s[1:].strip())
    tok = s.split()
    if tok[0] not in ('D', 'V') or len(tok) != 21:
        return None
    try:
        n = int(tok[1])
        vals = np.array(tok[3:12], dtype=float)
        sig = np.array(tok[12:21], dtype=float)
    except ValueError:
        return None
    if n < 2 or tok[2] not in ('C', 'O') or not (np.all(np.isfinite(vals)) and np.all(np.isfinite(sig))):
        return None
    return Msg(tok[0], t, Reading(tok[0], n, tok[2], vals, sig))


def looks_like_old_firmware(line):
    '''Linhas dos firmwares antigos (médias sem prefixo, G, IV): placa certa, firmware errado.'''
    s = line.decode('ascii', 'ignore').strip() if isinstance(line, (bytes, bytearray)) else line.strip()
    if s.startswith(('G ', 'IV ')):
        return True
    tok = s.replace(',', ' ').split()
    if len(tok) == 9:
        try:
            [float(x) for x in tok]
            return True
        except ValueError:
            return False
    return False


def fw_info(text):
    '''"QuadPV fw 3.0.0 tipo=Isc-Voc modo=C T=60 ..." -> dict, ou None se não for do QuadPV.'''
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
# Configuração e conversão
# ----------------------------------------------------------------------------
class Config:
    def __init__(self):
        self.names = [f'Módulo {i + 1}' for i in range(N_CELLS)]
        self.area_cm2 = [0.0] * N_CELLS          # 0 = não informada
        self.r_shunt = [2.0] * N_CELLS           # ohm (meça com o multímetro)
        self.div = [1.0] * N_CELLS               # divisor da tensão (1 = direto)
        self.use_vrefint = True                  # tensão do ADC pela Vrefint (senão, fixa)
        self.vref_mv = 3300.0                    # tensão do ADC, se fixa
        self.vrefint_mv = 1200.0                 # Vrefint (1,16 a 1,24 V; calibrável)
        self.ns = 4                              # células em série por módulo
        self.ideality = 1.3                      # fator de idealidade (estimativa de ΔT)
        self.t_mod_c = 45.0                      # temperatura típica dos módulos (estimativa de ΔT)
        self.dvdt_cell = -2.2                    # mV/K por célula de silício
        self.ref_cell = 0
        self.cal_isc = [1.0] * N_CELLS           # Isc_i / Isc_ref em condição idêntica
        self.cal_voc = [0.0] * N_CELLS           # Voc_i - Voc_ref em condição idêntica (mV)
        self.cal_time = ''                       # quando a calibração foi feita

    def k_mv(self, r):
        '''mV por contagem: pela Vrefint medida, ou pela tensão fixa.'''
        if self.use_vrefint and r > 100:
            return self.vrefint_mv / r
        return self.vref_mv / ADC_COUNTS

    def calibrated(self):
        return any(abs(c - 1.0) > 1e-9 for c in self.cal_isc) or any(abs(c) > 1e-9 for c in self.cal_voc)

    def header_lines(self):
        lines = [f'ADC: {"Vrefint=%g mV" % self.vrefint_mv if self.use_vrefint else "Vref fixa=%g mV" % self.vref_mv}; '
                 f'{self.ns} celulas em serie; idealidade={self.ideality:g}; T modulo tipica={self.t_mod_c:g} C; '
                 f'dVoc/dT={self.dvdt_cell:g} mV/K por celula; referencia: modulo {self.ref_cell + 1}']
        for i in range(N_CELLS):
            lines.append(f'Modulo {i + 1}: nome="{self.names[i]}", area={self.area_cm2[i]:g} cm2, '
                         f'shunt={self.r_shunt[i]:g} ohm, divisor={self.div[i]:g}, '
                         f'calibracao Isc={self.cal_isc[i]:.5f}, Voc={self.cal_voc[i]:+.2f} mV')
        lines.append(f'Calibracao dos modulos: {self.cal_time or "nenhuma"}')
        return lines


def convert(rd, cfg, offsets):
    '''Reading -> dict com corrente (mA), tensões (mV), VDDA e incerteza da média da corrente.
    offsets = contagens dos shunts sem corrente (janela de Voc).'''
    k = cfg.k_mv(rd.r)
    rs = np.asarray(cfg.r_shunt)
    i_raw = (rd.a - offsets) * k / rs                       # mA, sem a calibração dos módulos
    v = rd.v * k * np.asarray(cfg.div)
    i_err = rd.sa / math.sqrt(rd.n) * k / rs                # erro padrão da média (mA)
    return {'i_raw': i_raw, 'i': i_raw / np.asarray(cfg.cal_isc), 'v': v, 'vdda': k * ADC_COUNTS,
            'i_err': i_err, 'k': k}


def vt_mv(cfg):
    return K_B_OVER_Q * (cfg.t_mod_c + 273.15)


def delta_t(voc, isc, cfg):
    '''ΔT de cada módulo em relação ao de referência, pela diferença de Voc descontado o efeito da luz:
       ΔVoc = Ns·n·Vt·ln(Isc_i/Isc_ref) + Ns·(dVoc/dT)·ΔT   ->   ΔT = (ΔVoc - parte da luz) / (Ns·dVoc/dT)'''
    ref = cfg.ref_cell
    voc_c = np.asarray(voc) - np.asarray(cfg.cal_voc)
    isc = np.asarray(isc, dtype=float)
    dvoc = voc_c - voc_c[ref]
    with np.errstate(divide='ignore', invalid='ignore'):
        light = cfg.ns * cfg.ideality * vt_mv(cfg) * np.log(isc / isc[ref])
        dt = (dvoc - light) / (cfg.ns * cfg.dvdt_cell)
    ok = np.isfinite(dt) & (isc > 0.5) & (isc[ref] > 0.5)
    return dvoc, np.where(ok, dt, np.nan)


# ----------------------------------------------------------------------------
# Armazenamento
# ----------------------------------------------------------------------------
class Series:
    '''Arrays que crescem por blocos (uma linha por leitura).'''

    def __init__(self, cols):
        self.cols = cols
        self.clear()

    def clear(self):
        self.n = 0
        self.t = np.empty(1024)
        self.d = {k: np.empty((1024, w)) for k, w in self.cols.items()}

    def append(self, t, **vals):
        if self.n == self.t.shape[0]:
            cap = self.n * 2
            t2 = np.empty(cap)
            t2[:self.n] = self.t[:self.n]
            self.t = t2
            for k, a in self.d.items():
                b = np.empty((cap, a.shape[1]))
                b[:self.n] = a[:self.n]
                self.d[k] = b
        self.t[self.n] = t
        for k, v in vals.items():
            self.d[k][self.n] = v
        self.n += 1

    def get(self, k):
        return self.d[k][:self.n]

    def times(self):
        return self.t[:self.n]


def trailing_mean(y, w):
    '''Média móvel das últimas w amostras (ignora NaN); mesma forma de y.'''
    if w <= 1 or len(y) == 0:
        return y.copy()
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    c = np.cumsum(np.where(ok, y, 0.0), axis=0)
    n = np.cumsum(ok, axis=0).astype(float)
    c = np.vstack([np.zeros((1,) + y.shape[1:]), c])
    n = np.vstack([np.zeros((1,) + y.shape[1:]), n])
    idx = np.arange(1, len(y) + 1)
    lo = np.maximum(idx - w, 0)
    s = c[idx] - c[lo]
    m = n[idx] - n[lo]
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(m > 0, s / m, np.nan)


def window_stats(y, w):
    '''Média e erro padrão (desvio/√N) das últimas w linhas de y (n x 4), ignorando NaN.'''
    if len(y) == 0:
        return np.full(N_CELLS, np.nan), np.full(N_CELLS, np.nan)
    tail = y[-w:]
    with np.errstate(invalid='ignore'):
        m = np.nanmean(tail, axis=0)
        cnt = np.sum(np.isfinite(tail), axis=0)
        sd = np.nanstd(tail, axis=0, ddof=1) if len(tail) > 1 else np.full(tail.shape[1], np.nan)
        se = np.where(cnt > 1, sd / np.sqrt(np.maximum(cnt, 1)), np.nan)
    return m, se


# ----------------------------------------------------------------------------
# Serial em thread
# ----------------------------------------------------------------------------
def candidate_ports(preferred=None):
    '''Portas a testar, em ordem (igual ao SOI): a escolhida pelo usuário (só ela, se escolhida),
    a variável QUADPV_PORT, um dispositivo chamado "QuadPV", os STM32 (VID 0x0483), outras placas
    conhecidas e, se houver uma só porta USB, ela. A identidade é confirmada depois com "?".'''
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
    for p in usb:
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


class SerialLink(threading.Thread):
    '''Procura a placa, confirma que é um QuadPV (firmware 3.x), lê as linhas e reconecta sozinha.'''

    def __init__(self):
        super().__init__(daemon=True)
        self.ser = None
        self.connected = False
        self.port = None
        self.preferred = None
        self.last_error = None
        self.bad_lines = 0
        self.fw = None
        self.identity = None           # 'QuadPV', 'incompatível' ou 'não confirmada'
        self._rejected = {}
        self._msgs = deque(maxlen=20000)
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._stop_evt = threading.Event()
        self._reconnect_evt = threading.Event()

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

    def _close_locked(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.connected = False

    def _lines(self, data):
        self._buf.extend(data)
        if len(self._buf) > 100_000 and b'\n' not in self._buf[-100_000:]:
            self._buf.clear()
        out = []
        while True:
            nl = self._buf.find(b'\n')
            if nl < 0:
                break
            out.append(bytes(self._buf[:nl]))
            del self._buf[:nl + 1]
        return out

    def _feed(self, data):
        msgs, old = [], False
        for line in self._lines(data):
            msg = parse_message(line, time.time())
            if msg is None:
                if looks_like_old_firmware(line):
                    old = True
                elif line.strip():
                    self.bad_lines += 1
                continue
            msgs.append(msg)
        return msgs, old

    def _identify(self, ser):
        ser.write(b'?\n')
        t0 = time.monotonic()
        early, fw, data_seen, old = [], None, False, False
        while time.monotonic() - t0 < IDENTIFY_TIMEOUT and not self._stop_evt.is_set():
            data = ser.read(max(1, ser.in_waiting))
            if not data:
                continue
            msgs, o = self._feed(data)
            old |= o
            for msg in msgs:
                early.append(msg)
                if msg.kind == '#' and fw_info(msg.data):
                    fw = fw_info(msg.data)
                elif msg.kind in ('D', 'V'):
                    data_seen = True
            if fw is not None:
                break
            if (data_seen or old) and time.monotonic() - t0 > 1.2:
                break
        if fw is not None:
            ok = fw['version'].split('.')[0] == '3'
            return ('QuadPV' if ok else 'incompatível'), fw, early
        if data_seen:
            return 'QuadPV', None, early
        if old:
            return 'incompatível', None, early
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
                identity, fw, early = self._identify(ser)
                if identity is None and not self.preferred:
                    ser.close()
                    self._rejected[port] = time.monotonic() + REJECT_HOLD
                    self.last_error = f'{port} não respondeu como QuadPV (outra placa?)'
                    continue
                with self._wlock:
                    self.ser, self.port, self.connected = ser, port, True
                    self.fw, self.identity = fw, identity or 'não confirmada'
                    self.last_error = None
                with self._lock:
                    self._msgs.extend(early)
                return
            except Exception as e:
                self.last_error = f'{port}: {e}'
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
                self.last_error = str(e)
                with self._wlock:
                    self._close_locked()
                continue
            if data:
                msgs, old = self._feed(data)
                if old and self.identity != 'QuadPV':
                    self.identity = 'incompatível'
                for msg in msgs:
                    if msg.kind == '#' and fw_info(msg.data):
                        self.fw = fw_info(msg.data)
                        self.identity = 'QuadPV' if self.fw['version'].startswith('3') else 'incompatível'
                if msgs:
                    with self._lock:
                        self._msgs.extend(msgs)


# ----------------------------------------------------------------------------
# Simulação: imita o firmware 3.x
# ----------------------------------------------------------------------------
class Simulator:
    '''Quatro módulos pequenos (4 células, Isc ~120 mA, Voc ~2,4 V) sob um "dia" de 20 min com
    nuvens. As coberturas têm transmitâncias diferentes (TRANS) e aquecem os módulos de forma
    diferente (DT_COVER, a 1000 W/m²). Os módulos não são exatamente iguais (MISMATCH_I/V), o que
    a calibração em condição idêntica corrige. Gera as mesmas linhas do firmware, em contagens,
    com o ruído de uma média de 6000 varreduras.'''

    TRANS = (1.00, 0.97, 0.93, 1.02)
    DT_COVER = (0.0, 1.0, 2.5, -0.5)          # K a mais que o módulo 1, a 1000 W/m²
    MISMATCH_I = (1.000, 1.012, 0.991, 1.004)
    MISMATCH_V = (0.0, 3.0, -2.0, 1.0)        # mV
    OFFSETS = (10.5, 7.5, 9.3, 11.1)          # contagens dos shunts sem corrente (com o 470 k)
    ISC0, VOC0, NS, N_ID = 120.0, 2400.0, 4, 1.3
    R_SHUNT, R_ON = 2.0, 0.02

    def __init__(self, seed=None):
        self.rng = np.random.default_rng(seed)
        self.identical = False                 # True: todos com a mesma cobertura (para calibrar)
        self.reset()

    def reset(self):
        self.t0 = time.time()
        self.cloud = 0.0
        self.mode = 'C'
        self.interval = 60
        self.next_sec = 0.0
        self.since_voc = self.interval - 2
        self.voc_now = False
        self.pending = []
        self.vdd = 3300.0

    def command(self, text):
        s = text.strip()
        c = s[:1].upper()
        if c == '?':
            self.pending.append(self._info())
        elif c == 'V':
            self.voc_now = True
            self.pending.append('ok Voc')
        elif c == 'T':
            try:
                self.interval = max(0, min(86400, int(s[1:])))
                self.since_voc = 0
            except ValueError:
                pass
            self.pending.append(self._info())
        elif c == 'M':
            arg = s[1:].strip().upper()
            if arg in ('C', 'O'):
                self.mode = arg
            self.pending.append(self._info())

    def _info(self):
        return f'QuadPV fw {__version__} tipo=Isc-Voc modo={self.mode} T={self.interval} fs=6000 perdidos=0 (simulado)'

    def irradiance(self, t):
        x = (t - self.t0) / 1200.0
        clear = 0.15 + 0.85 * math.sin(math.pi * x) ** 2
        if self.cloud < 0.02 and self.rng.random() < 0.006:
            self.cloud = self.rng.uniform(0.3, 0.7)
        self.cloud *= 0.85
        return 1000.0 * clear * (1.0 - self.cloud)

    def _state(self, t):
        g = self.irradiance(t)
        trans = (1.0,) * N_CELLS if self.identical else self.TRANS
        dtc = (0.0,) * N_CELLS if self.identical else self.DT_COVER
        isc = np.array([self.ISC0 * g / 1000.0 * tr * mm for tr, mm in zip(trans, self.MISMATCH_I)])
        temp = 25.0 + g / 1000.0 * (20.0 + np.array(dtc))
        vt = K_B_OVER_Q * (temp + 273.15)
        with np.errstate(divide='ignore'):
            voc = (self.VOC0 + self.NS * self.N_ID * vt * np.log(np.maximum(isc, 1e-6) / self.ISC0)
                   + self.NS * (-2.2) * (temp - 25.0) + np.array(self.MISMATCH_V))
        return isc, np.maximum(voc, 0.0)

    def _line(self, t, kind, n, mode, a, v):
        kmv = self.vdd / ADC_COUNTS
        r = 1200.0 / kmv                                    # Vrefint de 1,20 V em contagens
        sd_mean = 2.0 / math.sqrt(n)                        # ruído de uma média de n varreduras
        a = np.asarray(a) + self.rng.normal(0, sd_mean, 4)
        v = np.asarray(v) + self.rng.normal(0, sd_mean, 4)
        r = r + self.rng.normal(0, 1.0 / math.sqrt(n))
        sig = np.concatenate([np.full(4, 2.0), np.full(4, 2.2), [1.05]]) + self.rng.normal(0, 0.02, 9)
        return Msg(kind, t, Reading(kind, n, mode, np.concatenate([a, v, [r]]), sig))

    def poll(self, t):
        msgs = [Msg('#', t, p) for p in self.pending]
        self.pending = []
        if t < self.next_sec:
            return msgs
        self.next_sec = t + 1.0
        self.vdd = 3300.0 + 6.0 * math.sin((t - self.t0) / 300.0)       # deriva lenta do 3,3 V
        kmv = self.vdd / ADC_COUNTS
        isc, voc = self._state(t)
        off = np.array(self.OFFSETS)
        if self.interval and self.since_voc >= self.interval:
            self.voc_now = True
        if self.voc_now:
            self.voc_now = False
            self.since_voc = 0
            msgs.append(self._line(t, 'V', 400, 'O', off, voc / kmv))
            return msgs                                                   # segundo sem linha D
        self.since_voc += 1
        if self.mode == 'O':
            msgs.append(self._line(t, 'D', 6000, 'O', off, voc / kmv))
        else:
            msgs.append(self._line(t, 'D', 6000, 'C', off + isc * self.R_SHUNT / kmv,
                                   isc * (self.R_SHUNT + self.R_ON) / kmv))
        return msgs
