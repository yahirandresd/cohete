"""Guarda el mapa de Caldas (y opcionalmente el detalle del sitio de lanzamiento) para usarlo sin internet.

  python descargar_mapa.py                                  Caldas a zoom 8–12
  python descargar_mapa.py --centro 5.0560,-75.4925         + detalle 1.5 km alrededor (zoom 13–17)
  python descargar_mapa.py --centro 5.0560,-75.4925 --radio-km 3

Hacerlo con internet ANTES de ir a campo; el dashboard usa luego la copia de estacion_pc/teselas/.
"""
from __future__ import annotations

import argparse
import asyncio

from telemetria import teselas


async def principal(centro, radio_km: float) -> None:
    lista = teselas.lista_zona(centro, radio_km)
    limite = asyncio.Semaphore(2)
    ok = 0
    async with teselas.sesion_http() as http:
        for i in range(0, len(lista), 20):
            res = await asyncio.gather(*(teselas.bajar(http, z, x, y, limite) for z, x, y in lista[i:i + 20]))
            ok += sum(res)
            print(f"\r  {min(i + 20, len(lista))}/{len(lista)} teselas", end="", flush=True)
    print(f"\n  listo: {ok} de {len(lista)} guardadas en {teselas.CARPETA}")
    if ok < len(lista):
        print("  faltaron algunas (¿sin internet?). Vuelve a correrlo: solo baja las que faltan.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--centro", help="LAT,LON del sitio de lanzamiento")
    ap.add_argument("--radio-km", type=float, default=1.5)
    a = ap.parse_args()
    c = tuple(float(v) for v in a.centro.split(",")) if a.centro else None
    asyncio.run(principal(c, a.radio_km))
