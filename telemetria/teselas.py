"""Teselas de OpenStreetMap con copia local, para tener mapa en campo sin internet.

Las teselas vistas o descargadas se guardan en estacion_pc/teselas/{z}/{x}/{y}.png.
Política de uso de OSM: User-Agent identificable, pocas descargas en paralelo y
nada de descargas masivas (Caldas a zoom 8–12 + la zona de lanzamiento son unas 500).
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout

URL_OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
AGENTE = "EstacionCPWR/1.0 (Universidad de Caldas, telemetria de cohete de agua)"
CARPETA = Path(__file__).resolve().parent.parent / "teselas"
Z_MAX = 19

# departamento de Caldas (con un margen)
CALDAS = {"lat_min": 4.78, "lat_max": 5.78, "lon_min": -75.98, "lon_max": -74.62}
UCALDAS = (5.0560, -75.4925)


def tesela_de(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    r = math.radians(lat)
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def rango(lat_min: float, lat_max: float, lon_min: float, lon_max: float, z: int) -> list[tuple[int, int, int]]:
    x0, y0 = tesela_de(lat_max, lon_min, z)
    x1, y1 = tesela_de(lat_min, lon_max, z)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def lista_zona(centro: tuple[float, float] | None = None, radio_km: float = 1.5) -> list[tuple[int, int, int]]:
    """Caldas completo a zoom 8–12 y, si hay centro, el detalle a zoom 13–17 alrededor."""
    lista = [t for z in range(8, 13) for t in rango(**CALDAS, z=z)]
    if centro:
        lat, lon = centro
        dlat = radio_km / 111.32
        dlon = radio_km / (111.32 * math.cos(math.radians(lat)))
        for z in range(13, 18):
            lista += rango(lat - dlat, lat + dlat, lon - dlon, lon + dlon, z)
    return lista


def ruta(z: int, x: int, y: int, carpeta: Path = CARPETA) -> Path:
    return carpeta / str(z) / str(x) / f"{y}.png"


def sesion_http() -> ClientSession:
    return ClientSession(headers={"User-Agent": AGENTE}, timeout=ClientTimeout(total=10))


async def bajar(http: ClientSession, z: int, x: int, y: int, limite: asyncio.Semaphore,
                carpeta: Path = CARPETA) -> bool:
    """Baja una tesela si no está guardada. True si queda disponible en disco."""
    destino = ruta(z, x, y, carpeta)
    if destino.exists():
        return True
    async with limite:
        try:
            async with http.get(URL_OSM.format(z=z, x=x, y=y)) as r:
                if r.status != 200:
                    return False
                datos = await r.read()
        except Exception:  # sin internet
            return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_bytes(datos)
    tmp.replace(destino)
    return True
