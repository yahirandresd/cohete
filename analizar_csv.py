"""Análisis de una grabación después del vuelo (para el informe y el paper).

  python analizar_csv.py ../mediciones/telemetria_cohete_20261027_183559.csv
  python analizar_csv.py ../mediciones/*.csv            (varios a la vez)

Genera <archivo>_analisis.png (6 gráficas) y <archivo>_resumen.txt junto al CSV.
Velocidad y altura son ESTIMACIONES por integración de la IMU.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from telemetria import config  # noqa: E402
from telemetria.fusion import procesar  # noqa: E402
from telemetria.parser import cargar_csv, segmentar  # noqa: E402


def resumen(nombre: str, muestras, filas) -> str:
    validas = [f for f in filas if not f["vacia"]]
    tramos = segmentar(muestras)
    dur = muestras[-1].t - muestras[0].t if muestras else 0
    dts = sorted(b.t - a.t for a, b in zip(muestras, muestras[1:]) if b.t > a.t)
    tasa = 1 / dts[len(dts) // 2] if dts else 0
    reposo = [f for f in validas if f["reposo"]]
    coh = sorted(f["coherencia"] for f in reposo)
    fin = filas[-1] if filas else {}
    lineas = [
        f"ARCHIVO           {nombre}",
        f"MUESTRAS          {len(muestras)}  (vacías / IMU sin datos: {len(muestras) - len(validas)})",
        f"DURACIÓN          {dur:.1f} s   ·   tasa típica {tasa:.1f} Hz",
        f"TRAMOS CONTINUOS  {len(tramos)}  (cortes por huecos > {config.HUECO_MAX_S} s o tramas vacías)",
        f"G MÁXIMA          {max((f['a_mag_g'] for f in validas), default=0):.2f} g",
        f"INCLINACIÓN       min {min((f['inclinacion'] for f in validas), default=0):.1f}°  max {max((f['inclinacion'] for f in validas), default=0):.1f}°",
        f"FASES             {dict(Counter(f['fase'] for f in filas))}",
        f"T DE VUELO        {fin.get('t_vuelo', 0):.2f} s",
        f"ALTURA MÁX (EST.) {fin.get('altura_max', 0):.2f} m",
        f"VEL MÁX (EST.)    {max((f['v_mag'] for f in validas), default=0):.2f} m/s",
        f"COHERENCIA IMU    mediana {coh[len(coh) // 2]:.1f}° en reposo" if coh else "COHERENCIA IMU    sin tramos en reposo",
        "",
        "Coherencia = ángulo entre la gravedad medida por el acelerómetro y la vertical según los ángulos de la IMU.",
        f"Por encima de {config.COHERENCIA_MAX}° la velocidad/posición estimadas no son fiables (ángulos mal referenciados).",
    ]
    return "\n".join(lineas)


def graficar(ruta: Path, filas) -> Path:
    t0 = filas[0]["t"]
    t = [f["t"] - t0 for f in filas]
    nan = float("nan")
    col = lambda clave, i=None: [nan if f["vacia"] else (f[clave][i] if i is not None else f[clave]) for f in filas]  # noqa: E731

    plt.rcParams.update({"font.family": "monospace", "font.size": 8, "axes.grid": True, "grid.alpha": 0.3})
    fig, ax = plt.subplots(6, 1, figsize=(10, 13), sharex=True)
    ax[0].plot(t, col("a_mag_g"), "k-", lw=1); ax[0].set_ylabel("|a| [g]")
    for k, est in zip(("ax", "ay", "az"), ("-", "--", ":")):
        ax[1].plot(t, col(k), "k" + est, lw=1, label=k)
    ax[1].set_ylabel("acel cuerpo [g]"); ax[1].legend(loc="upper right")
    for k, est in zip(("roll", "pitch", "yaw"), ("-", "--", ":")):
        ax[2].plot(t, col(k), "k" + est, lw=1, label=k)
    ax[2].set_ylabel("ángulos [°]"); ax[2].legend(loc="upper right")
    ax[3].plot(t, col("inclinacion"), "k-", lw=1); ax[3].axhline(45, color="r", lw=0.8, ls="--"); ax[3].set_ylabel("inclinación [°]")
    ax[4].plot(t, col("v_mag"), "k-", lw=1, label="|v|"); ax[4].plot(t, col("v", 2), "k--", lw=1, label="vz")
    ax[4].set_ylabel("vel EST. [m/s]"); ax[4].legend(loc="upper right")
    ax[5].plot(t, col("p", 2), "k-", lw=1); ax[5].set_ylabel("altura EST. [m]"); ax[5].set_xlabel("t [s]")
    # sombrear huecos y tramas vacías
    for a in ax:
        for f0, f1 in zip(filas, filas[1:]):
            if f1["t"] - f0["t"] > config.HUECO_MAX_S or f1["vacia"]:
                a.axvspan(f0["t"] - t0, f1["t"] - t0, color="0.85", lw=0)
    fig.suptitle(f"CPWR · {ruta.name}", fontweight="bold")
    fig.tight_layout()
    salida = ruta.with_name(ruta.stem + "_analisis.png")
    fig.savefig(salida, dpi=130)
    plt.close(fig)
    return salida


def main(rutas: list[str]) -> None:
    if not rutas:
        print(__doc__)
        sys.exit(1)
    for r in rutas:
        ruta = Path(r)
        muestras = cargar_csv(ruta)
        if not muestras:
            print(f"{ruta.name}: sin muestras")
            continue
        filas = procesar(muestras)
        texto = resumen(ruta.name, muestras, filas)
        ruta.with_name(ruta.stem + "_resumen.txt").write_text(texto, encoding="utf-8")
        png = graficar(ruta, filas)
        print(texto)
        print(f"→ {png.name}\n")


if __name__ == "__main__":
    main(sys.argv[1:])
