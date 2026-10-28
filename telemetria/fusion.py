"""Cálculos sobre la IMU: gravedad, velocidad/posición estimadas y fases de vuelo.

Se usa igual en vivo (servidor.py) y sobre CSV grabados (analizar_csv.py):
se crea un Fusion() y se le pasa cada Muestra con actualizar().

Marco mundo: Z hacia arriba. Velocidad y posición son ESTIMACIONES por integración
(la deriva crece con el tiempo); la ZUPT las pone en cero cada vez que el cohete está quieto.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from . import config
from .parser import Muestra

EN_RAMPA = "EN RAMPA"
EMPUJE = "EMPUJE"
VUELO_LIBRE = "VUELO LIBRE"
APOGEO = "APOGEO"
DESCENSO = "DESCENSO"
EN_TIERRA = "EN TIERRA"
SIN_DATOS = "SIN DATOS IMU"

Vec = tuple[float, float, float]


def matriz_rotacion(roll: float, pitch: float, yaw: float) -> list[list[float]]:
    """Cuerpo -> mundo. R = Rz(yaw)·Ry(pitch)·Rx(SIGNO_ROLL·roll), ángulos en grados."""
    r = math.radians(config.SIGNO_ROLL * roll)
    p = math.radians(pitch)
    y = math.radians(yaw)
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def angulos_desde_matriz(m: list[list[float]]) -> Vec:
    """Inversa de matriz_rotacion (roll, pitch, yaw en grados, en la convención de la IMU)."""
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -m[2][0]))))
    roll = math.degrees(math.atan2(m[2][1], m[2][2]))
    yaw = math.degrees(math.atan2(m[1][0], m[0][0]))
    return config.SIGNO_ROLL * roll, pitch, yaw


def rotar(m: list[list[float]], v: Vec) -> Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def norma(v: Vec) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def acel_lineal_mundo(s: Muestra) -> Vec:
    """Aceleración sin gravedad en el marco mundo [m/s^2]."""
    f = rotar(matriz_rotacion(s.roll, s.pitch, s.yaw), (s.ax, s.ay, s.az))
    return (f[0] * config.G, f[1] * config.G, (f[2] - 1.0) * config.G)


def inclinacion(s: Muestra) -> float:
    """Ángulo [°] entre el eje del cohete y la vertical (0 = apuntando arriba)."""
    m = matriz_rotacion(s.roll, s.pitch, s.yaw)
    col = "xyz".index(config.EJE_COHETE)
    vertical = m[2][col]
    return math.degrees(math.acos(max(-1.0, min(1.0, vertical))))


@dataclass
class Estado:
    t: float = 0.0
    a_mag_g: float = 0.0
    a_lin: Vec = (0.0, 0.0, 0.0)          # m/s^2, mundo, sin gravedad ni sesgo
    v: Vec = (0.0, 0.0, 0.0)              # m/s, ESTIMADA
    p: Vec = (0.0, 0.0, 0.0)              # m, ESTIMADA
    inclinacion: float = 0.0
    coherencia: float = 0.0               # ° entre gravedad medida y vertical (solo en reposo)
    reposo: bool = False
    fase: str = EN_RAMPA
    g_max: float = 0.0
    v_max: float = 0.0
    altura_max: float = 0.0
    t_lanzamiento: float | None = None
    t_vuelo: float = 0.0
    saturada: bool = False
    calibrado: bool = False
    fiable: bool = True                   # la estimación de v/p todavía tiene sentido
    t_sin_reposo: float = 0.0
    muestras: int = 0
    vacias: int = 0

    def a_dict(self) -> dict:
        return {
            "t": round(self.t, 3),
            "a_mag_g": round(self.a_mag_g, 3),
            "a_lin": [round(x, 3) for x in self.a_lin],
            "v": [round(x, 3) for x in self.v],
            "p": [round(x, 3) for x in self.p],
            "v_mag": round(norma(self.v), 3),
            "inclinacion": round(self.inclinacion, 1),
            "coherencia": round(self.coherencia, 1),
            "reposo": self.reposo,
            "fase": self.fase,
            "g_max": round(self.g_max, 2),
            "v_max": round(self.v_max, 2),
            "altura_max": round(self.altura_max, 2),
            "t_vuelo": round(self.t_vuelo, 2),
            "saturada": self.saturada,
            "calibrado": self.calibrado,
            "fiable": self.fiable,
            "t_sin_reposo": round(self.t_sin_reposo, 1),
            "muestras": self.muestras,
            "vacias": self.vacias,
        }


class Fusion:
    def __init__(self) -> None:
        self.e = Estado()
        self._mags: deque[tuple[float, float]] = deque()   # (t, |a|) de la última ventana
        self._sesgo: Vec = (0.0, 0.0, 0.0)
        self._acum_sesgo: list[Vec] = []
        self._prev: Muestra | None = None
        self._a_prev: Vec = (0.0, 0.0, 0.0)
        self._t_reposo_desde: float | None = None

    # --- comandos del operador -------------------------------------------------
    def reiniciar_integracion(self) -> None:
        self.e.v = (0.0, 0.0, 0.0)
        self.e.p = (0.0, 0.0, 0.0)

    def reiniciar_vuelo(self) -> None:
        """Vuelve a EN RAMPA y borra máximos (antes de cada lanzamiento)."""
        guardado = (self._sesgo, self.e.calibrado, self._acum_sesgo, self._prev, self._a_prev, self.e.muestras)
        self.__init__()
        self._sesgo, self.e.calibrado, self._acum_sesgo, self._prev, self._a_prev, self.e.muestras = guardado

    def calibrar(self) -> bool:
        """Toma como sesgo el promedio de a_lin de las muestras recientes en reposo."""
        if len(self._acum_sesgo) < 3:
            return False
        n = len(self._acum_sesgo)
        self._sesgo = tuple(sum(a[i] for a in self._acum_sesgo) / n for i in range(3))  # type: ignore[assignment]
        self.e.calibrado = True
        self.reiniciar_integracion()
        return True

    # --- procesamiento ---------------------------------------------------------
    def actualizar(self, s: Muestra) -> Estado:
        e = self.e
        e.muestras += 1
        if s.vacia:
            e.vacias += 1
            e.fase = SIN_DATOS
            self._prev = None
            return e
        if e.fase == SIN_DATOS:
            e.fase = EN_TIERRA if e.t_lanzamiento is not None else EN_RAMPA

        e.t = s.t
        e.a_mag_g = norma((s.ax, s.ay, s.az))
        e.saturada = max(abs(s.ax), abs(s.ay), abs(s.az)) >= config.RANGO_ACC_G * 0.98
        e.g_max = max(e.g_max, e.a_mag_g)
        e.inclinacion = inclinacion(s)

        a_bruta = acel_lineal_mundo(s)
        a = tuple(a_bruta[i] - self._sesgo[i] for i in range(3))  # type: ignore[assignment]
        e.a_lin = a

        self._mags.append((s.t, e.a_mag_g))
        while self._mags and s.t - self._mags[0][0] > config.REPOSO_VENTANA_S:
            self._mags.popleft()
        e.reposo = self._en_reposo()
        if e.reposo:
            f_z = a_bruta[2] / config.G + 1.0
            e.coherencia = math.degrees(math.acos(max(-1.0, min(1.0, f_z / max(e.a_mag_g, 1e-6)))))
            self._acum_sesgo.append(a_bruta)
            self._acum_sesgo = self._acum_sesgo[-25:]

        # integración trapezoidal (se corta en huecos)
        prev = self._prev
        if prev is not None:
            dt = s.t - prev.t
            if 0 < dt <= config.HUECO_MAX_S:
                v0 = e.v
                e.v = tuple(v0[i] + 0.5 * (self._a_prev[i] + a[i]) * dt for i in range(3))  # type: ignore[assignment]
                e.p = tuple(e.p[i] + 0.5 * (v0[i] + e.v[i]) * dt for i in range(3))  # type: ignore[assignment]
            elif dt > config.HUECO_MAX_S:
                e.v = (0.0, 0.0, 0.0)
        if e.reposo:
            e.v = (0.0, 0.0, 0.0)    # ZUPT
        self._prev = s
        self._a_prev = a

        if prev is not None and not e.reposo:
            e.t_sin_reposo += max(0.0, min(s.t - prev.t, config.HUECO_MAX_S))
        elif e.reposo:
            e.t_sin_reposo = 0.0
        e.fiable = e.coherencia <= config.COHERENCIA_MAX and e.t_sin_reposo <= config.INTEGRACION_MAX_S

        self._fases(s)
        e = self.e      # _fases puede haber rearmado el vuelo (Estado nuevo)
        if e.fase != EN_TIERRA:
            e.v_max = max(e.v_max, norma(e.v))
        if e.t_lanzamiento is not None and e.fase != EN_TIERRA:
            e.altura_max = max(e.altura_max, e.p[2])
        return e

    def _en_reposo(self) -> bool:
        if len(self._mags) < 3 or self._mags[-1][0] - self._mags[0][0] < config.REPOSO_VENTANA_S * 0.7:
            return False
        mags = [m for _, m in self._mags]
        media = sum(mags) / len(mags)
        var = sum((m - media) ** 2 for m in mags) / len(mags)
        return abs(media - 1.0) < config.REPOSO_TOL_G and math.sqrt(var) < config.REPOSO_STD_G

    def _fases(self, s: Muestra) -> None:
        e = self.e
        if e.fase == EN_RAMPA:
            if e.a_mag_g > config.EMPUJE_G or (e.a_mag_g < config.CAIDA_LIBRE_G and not e.reposo):
                # a 5 Hz el empuje puede no verse: la caída libre también marca el lanzamiento
                e.t_lanzamiento = s.t
                e.fase = EMPUJE if e.a_mag_g > config.EMPUJE_G else VUELO_LIBRE
                e.p = (0.0, 0.0, 0.0)    # la posición del vuelo se mide desde la rampa
            return
        if e.fase == EN_TIERRA:
            if e.a_mag_g > config.EMPUJE_G:
                # nuevo lanzamiento sin haber pulsado ARMAR VUELO: se arma solo
                anterior = self.e
                self.reiniciar_vuelo()
                self.e.fase, self.e.t_lanzamiento, self.e.t = EMPUJE, s.t, s.t
                self.e.v, self.e.a_lin, self.e.inclinacion = anterior.v, anterior.a_lin, anterior.inclinacion
                self.e.a_mag_g = self.e.g_max = norma((s.ax, s.ay, s.az))
            return
        if e.t_lanzamiento is not None:
            e.t_vuelo = s.t - e.t_lanzamiento
        if e.fase == EMPUJE and e.a_mag_g < config.EMPUJE_G:
            e.fase = VUELO_LIBRE
        elif e.fase == VUELO_LIBRE and e.v[2] <= 0 and e.t_vuelo > 0.3:
            e.fase = APOGEO
        elif e.fase == APOGEO:
            e.fase = DESCENSO
        if e.fase in (VUELO_LIBRE, APOGEO, DESCENSO):
            if e.reposo:
                if self._t_reposo_desde is None:
                    self._t_reposo_desde = s.t
                elif s.t - self._t_reposo_desde >= config.TIERRA_REPOSO_S:
                    e.fase = EN_TIERRA
                    e.t_vuelo = self._t_reposo_desde - e.t_lanzamiento  # type: ignore[operator]
            else:
                self._t_reposo_desde = None


def procesar(muestras: list[Muestra]) -> list[dict]:
    """Corre la fusión sobre una lista de muestras y devuelve un dict por muestra."""
    f = Fusion()
    filas = []
    for s in muestras:
        e = f.actualizar(s)
        d = e.a_dict()
        d.update(ax=s.ax, ay=s.ay, az=s.az, roll=s.roll, pitch=s.pitch, yaw=s.yaw, vacia=s.vacia)
        filas.append(d)
    return filas
