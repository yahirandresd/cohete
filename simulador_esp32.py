"""Simula el ESP32 del cohete para probar la estación sin hardware.

  python simulador_esp32.py                      vuelo sintético en bucle, formato actual a 5 Hz
  python simulador_esp32.py --formato v2 --hz 50 formato v2 (con t_ms) a 50 Hz
  python simulador_esp32.py --corte 15           corta el enlace por encima de 15 m (como en el vuelo real)
  python simulador_esp32.py --csv ../mediciones/telemetria_cohete_20261027_183559.csv   reproduce un CSV real
  python simulador_esp32.py --broadcast          envía a 255.255.255.255 (para que lo vea también el móvil)
  python simulador_esp32.py --destino 127.0.0.1 192.168.20.28    al PC y a un móvil concreto

Los datos del vuelo sintético son INVENTADOS (física simplificada), solo para probar.
"""
from __future__ import annotations

import argparse
import math
import random
import socket
import time

from telemetria import config
from telemetria.fusion import angulos_desde_matriz
from telemetria.parser import cargar_csv

G = config.G


def _matriz_eje(theta: float, giro: float = 0.0) -> list[list[float]]:
    """Cuerpo->mundo con el eje Z del cuerpo apuntando a theta (rad) sobre la horizontal, en el plano XZ."""
    b = math.pi / 2 - theta          # rotación alrededor de Y
    cb, sb = math.cos(b), math.sin(b)
    cg, sg = math.cos(giro), math.sin(giro)
    ry = [[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]]
    rz = [[cg, -sg, 0], [sg, cg, 0], [0, 0, 1]]
    return [[sum(rz[i][k] * ry[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def vuelo_sintetico(dt: float):
    """Genera (t, acc_cuerpo[g], angulos[°], altura[m]) de rampa -> vuelo -> paracaídas -> suelo."""
    t = 0.0
    theta0 = math.radians(45)        # riel a 45°
    reposo_pre, empuje_s, a_empuje = 6.0, 0.20, 12 * G
    k_libre, k_paraca = 0.015, 0.45
    x = z = 0.0
    vx = vz = 0.0
    fase = "rampa"
    t_fase = 0.0
    t_apogeo = None
    while True:
        theta = theta0
        if fase == "rampa":
            a_w = (0.0, 0.0)
            if t >= reposo_pre:
                fase, t_fase = "empuje", t
        elif fase == "empuje":
            a_w = (a_empuje * math.cos(theta0), a_empuje * math.sin(theta0) - G)
            if t - t_fase >= empuje_s:
                fase = "libre"
        elif fase in ("libre", "paraca"):
            k = k_libre if fase == "libre" else k_paraca
            v = math.hypot(vx, vz)
            a_w = (-k * v * vx, -G - k * v * vz)
            if fase == "libre" and vz <= 0 and t_apogeo is None:
                t_apogeo = t
            if fase == "libre" and t_apogeo is not None and t - t_apogeo > 0.4:
                fase = "paraca"
        else:  # suelo
            a_w = (0.0, 0.0)
        if fase in ("empuje", "libre", "paraca"):
            vx += a_w[0] * dt
            vz += a_w[1] * dt
            x += vx * dt
            z += vz * dt
            if fase == "paraca":
                theta = -math.pi / 2 + math.radians(15) * math.sin(2 * math.pi * 0.8 * t)   # cuelga y se balancea
            elif math.hypot(vx, vz) > 0.5:
                theta = math.atan2(vz, vx)        # la nariz sigue la velocidad
            if z <= 0 and fase != "empuje":
                z, vx, vz, fase, t_fase = 0.0, 0.0, 0.0, "suelo", t
        if fase == "suelo":
            theta = 0.0
            if t - t_fase > 6.0:
                return
        m = _matriz_eje(theta)
        f_w = (a_w[0], 0.0, a_w[1] + G)          # fuerza específica en el mundo
        f_b = [sum(m[i][j] * f_w[i] for i in range(3)) / G for j in range(3)]   # R^T · f
        f_b = [max(-16.0, min(16.0, v + random.gauss(0, 0.01))) for v in f_b]
        ang = angulos_desde_matriz(m)
        yield t, f_b, ang, z
        t += dt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--formato", choices=["texto", "v2"], default="texto")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--corte", type=float, default=None, help="altura [m] sobre la que se pierde el enlace")
    ap.add_argument("--csv", default=None, help="reproducir un CSV grabado")
    ap.add_argument("--broadcast", action="store_true")
    ap.add_argument("--destino", nargs="+", default=["127.0.0.1"],
                    help="una o varias IP (p. ej. 127.0.0.1 192.168.20.28 para PC + móvil)")
    ap.add_argument("--una-vez", action="store_true", help="un solo vuelo y salir")
    args = ap.parse_args()

    destinos = ["255.255.255.255"] if args.broadcast else args.destino
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def enviar(t: float, acc, ang) -> None:
        if args.formato == "v2":
            txt = f"{int(t * 1000)},{acc[0]:.3f},{acc[1]:.3f},{acc[2]:.3f},{ang[0]:.1f},{ang[1]:.1f},{ang[2]:.1f},0"
        else:
            txt = (f"ACC[g]: {acc[0]:.2f},{acc[1]:.2f},{acc[2]:.2f} | "
                   f"ANG[deg]: {ang[0]:.1f},{ang[1]:.1f},{ang[2]:.1f}")
        for d in destinos:
            sock.sendto(txt.encode(), (d, config.UDP_PUERTO))

    print(f"Simulador ESP32 -> {', '.join(destinos)}:{config.UDP_PUERTO}  ({args.formato}, {args.hz} Hz). Ctrl+C para salir.")
    try:
        if args.csv:
            muestras = cargar_csv(args.csv)
            t0 = muestras[0].t
            inicio = time.perf_counter()
            for m in muestras:
                espera = (m.t - t0) - (time.perf_counter() - inicio)
                if espera > 0:
                    time.sleep(min(espera, 2.0))
                enviar(m.t - t0, (m.ax, m.ay, m.az), (m.roll, m.pitch, m.yaw))
            return
        dt_fisica = 0.002
        paso = max(1, round(1.0 / args.hz / dt_fisica))
        t_reloj = 0.0
        while True:
            inicio = time.perf_counter()
            for i, (t, acc, ang, z) in enumerate(vuelo_sintetico(dt_fisica)):
                if i % paso:
                    continue
                espera = t - (time.perf_counter() - inicio)
                if espera > 0:
                    time.sleep(espera)
                if args.corte is not None and z > args.corte:
                    continue
                enviar(t_reloj + t, acc, ang)
            t_reloj += t
            if args.una_vez:
                break
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
