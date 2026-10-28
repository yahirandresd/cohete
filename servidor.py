"""Estación terrestre: recibe la telemetría UDP del ESP32 y la sirve en un dashboard web.

  python servidor.py            luego abrir http://localhost:8080
                                (o desde el móvil: http://<IP-del-PC>:8080, misma red)

- Escucha UDP en el puerto 8888 (lo mismo que el script de consola original).
- Graba cada sesión en ../mediciones/telemetria_cohete_AAAAMMDD_HHMMSS.csv
  con las columnas originales + las calculadas (compatible con los CSV viejos).
- Envía cada muestra y el estado del enlace al navegador por WebSocket.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import socket
import statistics
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from telemetria import config, teselas
from telemetria.fusion import Fusion, procesar
from telemetria.parser import Muestra, cargar_csv, parse_linea

AQUI = Path(__file__).resolve().parent
WEB = AQUI / "web"
MEDICIONES = Path(os.environ.get("CPWR_MEDICIONES", AQUI.parent / "mediciones"))   # otra carpeta para pruebas

COLUMNAS = ["Timestamp", "IP_Origen", "Aceleracion_Raw", "Inclinacion_Raw",
            "t_esp_ms", "ax", "ay", "az", "roll", "pitch", "yaw",
            "a_mag_g", "ax_lin", "ay_lin", "az_lin", "vx", "vy", "vz", "px", "py", "pz",
            "inclinacion", "fase",
            "gps_lat", "gps_lon", "gps_alt", "gps_prec"]    # ubicación del equipo que mira (no del cohete)
HISTORIAL = 1500


class Estacion:
    def __init__(self) -> None:
        self.fusion = Fusion()
        self.clientes: set[web.WebSocketResponse] = set()
        self.historial: deque[dict] = deque(maxlen=HISTORIAL)
        self.llegadas: deque[float] = deque(maxlen=200)       # time.monotonic() de cada paquete
        self.ultimo_ip = ""
        self.paquetes = 0
        self.ubicacion: dict | None = None      # la manda el navegador con permiso de ubicación
        self.archivo: Path | None = None
        self._f = None
        self._w = None
        self.nueva_sesion()

    # --- grabación -------------------------------------------------------------
    def nueva_sesion(self) -> None:
        if self._f:
            self._f.close()
        MEDICIONES.mkdir(exist_ok=True)
        # el archivo se crea con el primer paquete: abrir el servidor sin cohete no deja CSV vacíos
        self.archivo = MEDICIONES / f"telemetria_cohete_{datetime.now():%Y%m%d_%H%M%S}.csv"
        self._f = None
        self._w = None
        self.fusion = Fusion()
        self.historial.clear()
        self.paquetes = 0

    def _grabar(self, ahora: datetime, ip: str, s: Muestra, e) -> None:
        if self._w is None:
            self._f = open(self.archivo, "w", newline="", encoding="utf-8", buffering=1)
            self._w = csv.writer(self._f)
            self._w.writerow(COLUMNAS)
        self._w.writerow([
            ahora.strftime("%H:%M:%S.%f")[:-3], ip,
            f"ACC[g]: {s.ax:.2f},{s.ay:.2f},{s.az:.2f}", f"ANG[deg]: {s.roll:.1f},{s.pitch:.1f},{s.yaw:.1f}",
            s.t_esp_ms if s.t_esp_ms is not None else "",
            s.ax, s.ay, s.az, s.roll, s.pitch, s.yaw,
            round(e.a_mag_g, 4), *(round(x, 4) for x in e.a_lin), *(round(x, 4) for x in e.v),
            *(round(x, 4) for x in e.p), round(e.inclinacion, 2), e.fase,
            *self._columnas_gps(),
        ])

    def _columnas_gps(self) -> list:
        u = self.ubicacion
        # una ubicación de más de 10 s ya no describe dónde está el equipo
        if not u or time.time() - u["t"] > 10:
            return ["", "", "", ""]
        return [u["lat"], u["lon"], "" if u.get("alt") is None else round(u["alt"], 1), round(u["prec"], 1)]

    # --- recepción -------------------------------------------------------------
    def recibir(self, datos: bytes, ip: str) -> None:
        ahora = datetime.now()
        linea = datos.decode("utf-8", errors="ignore")
        t_pc = time.time()
        s = parse_linea(linea, t_pc)
        if s is None:
            return
        self.paquetes += 1
        self.llegadas.append(time.monotonic())
        self.ultimo_ip = ip
        e = self.fusion.actualizar(s)
        self._grabar(ahora, ip, s, e)
        msg = {"ax": s.ax, "ay": s.ay, "az": s.az, "roll": s.roll, "pitch": s.pitch, "yaw": s.yaw,
               "vacia": s.vacia, "viejo": s.viejo, "hora": ahora.strftime("%H:%M:%S.%f")[:-3],
               "t_pc": round(t_pc, 3), **e.a_dict()}
        self.historial.append(msg)
        self._difundir({"tipo": "muestra", "m": msg})

    def enlace(self) -> dict:
        ahora = time.monotonic()
        edad = ahora - self.llegadas[-1] if self.llegadas else None
        recientes = [t for t in self.llegadas if ahora - t <= 2.0]
        pps = len(recientes) / 2.0
        intervalos = [b - a for a, b in zip(self.llegadas, list(self.llegadas)[1:])]
        nominal = 1.0 / statistics.median(intervalos) if len(intervalos) >= 10 else config.TASA_ESPERADA_HZ
        perdida = max(0.0, min(1.0, 1 - pps / nominal)) if edad is not None else 1.0
        return {
            "tipo": "enlace",
            "en_linea": edad is not None and edad < config.ENLACE_PERDIDO_S,
            "edad_s": None if edad is None else round(edad, 2),
            "pps": round(pps, 1), "nominal_hz": round(nominal, 1), "perdida": round(perdida, 3),
            "ip": self.ultimo_ip, "paquetes": self.paquetes,
            "archivo": self.archivo.name if self.archivo else "",
            "clientes": len(self.clientes),
        }

    def _difundir(self, obj: dict) -> None:
        if not self.clientes:
            return
        texto = json.dumps(obj)
        for ws in list(self.clientes):
            if ws.closed:
                self.clientes.discard(ws)
                continue
            asyncio.ensure_future(ws.send_str(texto))


class ProtocoloUDP(asyncio.DatagramProtocol):
    def __init__(self, estacion: Estacion) -> None:
        self.estacion = estacion

    def datagram_received(self, data: bytes, addr) -> None:
        self.estacion.recibir(data, addr[0])


# --- HTTP / WebSocket ------------------------------------------------------------
async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    est: Estacion = request.app["estacion"]
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    est.clientes.add(ws)
    await ws.send_str(json.dumps({"tipo": "historial", "muestras": list(est.historial)}))
    await ws.send_str(json.dumps(est.enlace()))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                r = ejecutar(est, json.loads(msg.data))
                if r is not None:
                    await ws.send_str(json.dumps(r))
    finally:
        est.clientes.discard(ws)
    return ws


def ejecutar(est: Estacion, orden: dict) -> dict | None:
    accion = orden.get("accion")
    ok = True
    if accion == "ubicacion":
        try:
            est.ubicacion = {"lat": float(orden["lat"]), "lon": float(orden["lon"]),
                             "alt": None if orden.get("alt") is None else float(orden["alt"]),
                             "prec": float(orden.get("prec") or 0), "t": time.time()}
        except (KeyError, TypeError, ValueError):
            pass
        return None    # llega cada segundo: no se responde
    if accion == "calibrar":
        ok = est.fusion.calibrar()
    elif accion == "reiniciar_integracion":
        est.fusion.reiniciar_integracion()
    elif accion == "reiniciar_vuelo":
        est.fusion.reiniciar_vuelo()
    elif accion == "nueva_sesion":
        est.nueva_sesion()
    else:
        ok = False
    return {"tipo": "respuesta", "accion": accion, "ok": ok,
            "archivo": est.archivo.name if est.archivo else ""}


async def api_sesiones(request: web.Request) -> web.Response:
    archivos = sorted(MEDICIONES.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return web.json_response([{"nombre": p.name, "kb": round(p.stat().st_size / 1024, 1),
                               "fecha": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
                              for p in archivos])


async def api_sesion(request: web.Request) -> web.Response:
    nombre = Path(request.query.get("archivo", "")).name
    ruta = MEDICIONES / nombre
    if not nombre or not ruta.exists():
        raise web.HTTPNotFound(text="no existe")
    filas = procesar(cargar_csv(ruta))
    return web.json_response({"nombre": nombre, "muestras": filas})


async def api_descargar_log(request: web.Request) -> web.Response:
    """Baja el log grabado a bordo (firmware v2) desde el ESP32."""
    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as s:
            async with s.get(config.URL_LOG_A_BORDO) as r:
                if r.status != 200:
                    return web.json_response({"ok": False, "error": f"HTTP {r.status}"})
                datos = await r.read()
    except Exception as ex:  # sin firmware v2 o sin conexión al AP
        return web.json_response({"ok": False, "error": f"No se pudo conectar a {config.URL_LOG_A_BORDO}: {ex.__class__.__name__}"})
    ruta = MEDICIONES / f"log_abordo_{datetime.now():%Y%m%d_%H%M%S}.csv"
    ruta.write_bytes(datos)
    return web.json_response({"ok": True, "archivo": ruta.name, "kb": round(len(datos) / 1024, 1)})


async def tesela(request: web.Request) -> web.FileResponse:
    """Tesela de OSM: la copia local si existe; si no, se baja (cuando hay internet) y se guarda."""
    try:
        z, x, y = (int(request.match_info[k]) for k in ("z", "x", "y"))
    except ValueError:
        raise web.HTTPNotFound()
    if not (0 <= z <= teselas.Z_MAX and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise web.HTTPNotFound()
    if not await teselas.bajar(request.app["http"], z, x, y, request.app["limite_teselas"]):
        raise web.HTTPNotFound(text="sin internet y sin copia guardada")
    return web.FileResponse(teselas.ruta(z, x, y), headers={"Cache-Control": "max-age=604800"})


async def api_guardar_mapa(request: web.Request) -> web.Response:
    """Guarda Caldas (zoom 8–12) y el detalle alrededor de la posición recibida (zoom 13–17)."""
    try:
        cuerpo = await request.json()
        centro = (float(cuerpo["lat"]), float(cuerpo["lon"]))
    except Exception:
        centro = None
    lista = teselas.lista_zona(centro)
    res = await asyncio.gather(*(teselas.bajar(request.app["http"], z, x, y, request.app["limite_teselas"]) for z, x, y in lista))
    return web.json_response({"ok": sum(res), "total": len(lista)})


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB / "index.html")


async def latido_enlace(app: web.Application) -> None:
    est: Estacion = app["estacion"]
    while True:
        await asyncio.sleep(0.25)
        est._difundir(est.enlace())


def socket_udp() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", config.UDP_PUERTO))
    return sock


def ips_locales() -> list[str]:
    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        return []


async def al_iniciar(app: web.Application) -> None:
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: ProtocoloUDP(app["estacion"]), sock=socket_udp())
    app["latido"] = asyncio.create_task(latido_enlace(app))
    app["http"] = teselas.sesion_http()
    app["limite_teselas"] = asyncio.Semaphore(2)


async def al_cerrar(app: web.Application) -> None:
    app["latido"].cancel()
    await app["http"].close()


def crear_app() -> web.Application:
    app = web.Application()
    app["estacion"] = Estacion()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/sesiones", api_sesiones)
    app.router.add_get("/api/sesion", api_sesion)
    app.router.add_post("/api/descargar_log", api_descargar_log)
    app.router.add_post("/api/guardar_mapa", api_guardar_mapa)
    app.router.add_get("/teselas/{z}/{x}/{y}.png", tesela)
    app.router.add_static("/static", WEB)
    app.on_startup.append(al_iniciar)
    app.on_cleanup.append(al_cerrar)
    return app


if __name__ == "__main__":
    app = crear_app()
    print("=" * 64)
    print(f"  ESTACIÓN TERRESTRE  ·  UDP {config.UDP_PUERTO}  ·  grabando en {app['estacion'].archivo.name}")
    print(f"  Dashboard:  http://localhost:{config.HTTP_PUERTO}")
    for ip in ips_locales():
        print(f"              http://{ip}:{config.HTTP_PUERTO}   (desde otro equipo en la misma red)")
    print("=" * 64)
    web.run_app(app, host="0.0.0.0", port=config.HTTP_PUERTO, print=None)
