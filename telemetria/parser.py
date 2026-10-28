"""Lectura de la telemetría del ESP32.

Formatos aceptados (una línea por paquete UDP):
  heredado: "ACC[g]: ax,ay,az | ANG[deg]: roll,pitch,yaw"     (firmware actual)
  v2:       "t_ms,ax,ay,az,roll,pitch,yaw[,flags]"            (firmware_sugerido)
            flags: bit0 = dato viejo (no llegó trama nueva de la IMU)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import config


@dataclass
class Muestra:
    t: float                 # segundos (reloj del ESP32 si existe, si no el del PC)
    ax: float                # g, ejes del cuerpo
    ay: float
    az: float
    roll: float              # grados
    pitch: float
    yaw: float
    t_esp_ms: int | None = None
    viejo: bool = False      # el ESP32 reenvió el último valor

    @property
    def vacia(self) -> bool:
        """Trama en ceros = la IMU no está entregando datos."""
        return (self.ax == 0 and self.ay == 0 and self.az == 0
                and self.roll == 0 and self.pitch == 0 and self.yaw == 0)


def _tres(texto: str) -> tuple[float, float, float]:
    a, b, c = (float(v) for v in texto.split(","))
    return a, b, c


def parse_linea(linea: str, t_pc: float) -> Muestra | None:
    """Convierte una línea en Muestra. Devuelve None si no se reconoce."""
    linea = linea.strip()
    if not linea:
        return None
    try:
        if linea.startswith("ACC"):
            acc_txt, ang_txt = linea.split("|")
            ax, ay, az = _tres(acc_txt.split(":", 1)[1])
            r, p, y = _tres(ang_txt.split(":", 1)[1])
            return Muestra(t_pc, ax, ay, az, r, p, y)
        campos = linea.split(",")
        if len(campos) in (7, 8):
            t_ms = int(float(campos[0]))
            ax, ay, az, r, p, y = (float(v) for v in campos[1:7])
            viejo = len(campos) == 8 and (int(campos[7]) & 1) == 1
            return Muestra(t_ms / 1000.0, ax, ay, az, r, p, y, t_esp_ms=t_ms, viejo=viejo)
    except ValueError:
        return None
    return None


def _hora_a_segundos(texto: str) -> float:
    h = datetime.strptime(texto, "%H:%M:%S.%f")
    return h.hour * 3600 + h.minute * 60 + h.second + h.microsecond / 1e6


def cargar_csv(ruta: str | Path) -> list[Muestra]:
    """Lee un CSV de mediciones/ (formato del script original o el de servidor.py).

    Usa la hora del ESP32 cuando está (columna t_esp_ms), si no la del PC.
    Corrige el paso por medianoche. Devuelve TODAS las muestras, incluidas las vacías;
    quien procese decide qué hacer con ellas.
    """
    muestras: list[Muestra] = []
    desfase = 0.0
    t_prev = None
    with open(ruta, newline="", encoding="utf-8", errors="ignore") as f:
        primera = f.readline()
        f.seek(0)
        if primera.startswith("t_ms"):
            # log a bordo del firmware v2: líneas en formato v2 tal cual
            next(f)
            return [m for m in (parse_linea(l, 0.0) for l in f) if m is not None]
        for fila in csv.DictReader(f):
            t = _hora_a_segundos(fila["Timestamp"])
            if t_prev is not None and t + desfase < t_prev - 43200:
                desfase += 86400
            t += desfase
            t_prev = t
            t_esp = fila.get("t_esp_ms")
            if t_esp:
                linea = f"{t_esp},{fila['ax']},{fila['ay']},{fila['az']},{fila['roll']},{fila['pitch']},{fila['yaw']}"
            else:
                linea = f"{fila['Aceleracion_Raw']} | {fila['Inclinacion_Raw']}"
            m = parse_linea(linea, t)
            if m is not None:
                muestras.append(m)
    return muestras


def segmentar(muestras: list[Muestra], hueco_s: float = config.HUECO_MAX_S) -> list[list[Muestra]]:
    """Parte la serie en tramos continuos: corta en huecos > hueco_s y en tramas vacías."""
    tramos: list[list[Muestra]] = []
    actual: list[Muestra] = []
    for m in muestras:
        if m.vacia:
            if actual:
                tramos.append(actual)
            actual = []
            continue
        if actual and m.t - actual[-1].t > hueco_s:
            tramos.append(actual)
            actual = []
        actual.append(m)
    if actual:
        tramos.append(actual)
    return tramos


def hora_texto(t_s: float) -> str:
    """Segundos del día -> 'HH:MM:SS.mmm' (formato de la columna Timestamp)."""
    return (datetime(2000, 1, 1) + timedelta(seconds=t_s % 86400)).strftime("%H:%M:%S.%f")[:-3]
