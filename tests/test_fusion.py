import math
import random
from pathlib import Path

import pytest

from telemetria import config
from telemetria.fusion import (EN_RAMPA, EN_TIERRA, Fusion, acel_lineal_mundo, angulos_desde_matriz,
                               inclinacion, matriz_rotacion, procesar, rotar)
from telemetria.parser import Muestra, cargar_csv

MEDICIONES = Path(__file__).resolve().parents[2] / "mediciones"


def muestra_en_reposo(t, roll, pitch, yaw):
    """Lo que leería la IMU quieta con esos ángulos: la gravedad vista desde el cuerpo."""
    m = matriz_rotacion(roll, pitch, yaw)
    f = [sum(m[i][j] * (0, 0, 1)[i] for i in range(3)) for j in range(3)]
    return Muestra(t, *f, roll, pitch, yaw)


@pytest.mark.parametrize("ang", [(0, 0, 0), (30, 0, 0), (0, 45, 90), (-120, 60, 80), (170, -30, -100)])
def test_reposo_inclinado_sin_aceleracion_lineal(ang):
    a = acel_lineal_mundo(muestra_en_reposo(0, *ang))
    assert all(abs(x) < 1e-9 for x in a)


def test_ida_y_vuelta_angulos():
    for _ in range(50):
        ang = (random.uniform(-179, 179), random.uniform(-89, 89), random.uniform(-179, 179))
        r = angulos_desde_matriz(matriz_rotacion(*ang))
        assert all(abs(a - b) < 1e-6 for a, b in zip(ang, r))


def test_inclinacion_rampa_45():
    # cohete en el riel: eje Z del cuerpo a 45° de la vertical
    assert inclinacion(muestra_en_reposo(0, 0, 45, 0)) == pytest.approx(45, abs=1e-6)


def test_aceleracion_constante_integra_bien():
    """1 m/s^2 hacia arriba durante 2 s, a 100 Hz: v = a·t, p = a·t²/2."""
    f = Fusion()
    a_g = 1.0 / config.G
    dt = 0.01
    for i in range(201):
        f.actualizar(Muestra(i * dt, 0, 0, 1 + a_g, 0, 0, 0))
    assert f.e.v[2] == pytest.approx(2.0, rel=1e-3)
    assert f.e.p[2] == pytest.approx(2.0, rel=1e-2)


def test_zupt_pone_velocidad_en_cero():
    f = Fusion()
    for i in range(50):                       # movimiento
        f.actualizar(Muestra(i * 0.2, 0.3, 0, 1.2, 0, 0, 0))
    assert f.e.v[0] != 0
    for i in range(50, 60):                   # reposo
        f.actualizar(Muestra(i * 0.2, 0, 0, 1.0, 0, 0, 0))
    assert f.e.reposo and f.e.v == (0.0, 0.0, 0.0)


def test_hueco_corta_integracion():
    f = Fusion()
    f.actualizar(Muestra(0, 0, 0, 2, 0, 0, 0))
    f.actualizar(Muestra(0.2, 0, 0, 2, 0, 0, 0))
    f.actualizar(Muestra(5.0, 0, 0, 2, 0, 0, 0))   # 4.8 s sin datos
    assert f.e.v == (0.0, 0.0, 0.0)


def test_vuelo_sintetico_fases_y_altura():
    from simulador_esp32 import vuelo_sintetico
    f = Fusion()
    zmax = 0.0
    fases = []
    for i, (t, a, g, z) in enumerate(vuelo_sintetico(0.002)):
        zmax = max(zmax, z)
        if i % 10:
            continue                            # 50 Hz
        e = f.actualizar(Muestra(t, *a, *g))
        if not fases or fases[-1] != e.fase:
            fases.append(e.fase)
    assert fases[0] == EN_RAMPA and fases[-1] == EN_TIERRA
    assert "APOGEO" in fases
    assert f.e.altura_max == pytest.approx(zmax, rel=0.1)
    assert f.e.g_max > 10


def test_csv_real_en_reposo_queda_quieto():
    filas = procesar(cargar_csv(MEDICIONES / "telemetria_cohete_20261027_144947.csv"))
    reposo = [r for r in filas if r["reposo"]]
    assert len(reposo) > 500
    assert all(r["v_mag"] == 0 for r in reposo)
    # los ángulos de la IMU son coherentes con la gravedad medida
    coh = sorted(r["coherencia"] for r in reposo)
    assert coh[len(coh) // 2] < 2.0
