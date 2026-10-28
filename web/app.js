"use strict";
/* Estación terrestre CPWR — dashboard. Recibe muestras por WebSocket desde servidor.py. */

const FASES = ["EN RAMPA", "EMPUJE", "VUELO LIBRE", "APOGEO", "DESCENSO", "EN TIERRA"];
const SIGNO_ROLL = -1;          // igual que telemetria/config.py
const EJE_COHETE = 2;           // 0=x 1=y 2=z
const MAX_VIVO = 6000;
const HUECO_S = 1.0;

const $ = (id) => document.getElementById(id);
const estado = {
  modo: "vivo",
  vivo: [],
  revision: [],
  cursor: 0,
  ventana: 30,
  enlace: null,
  enlaceRecibido: 0,
  servidor: false,
  ultimaFase: null,
  ultimoEnLinea: null,
  pendiente: false,
};

/* ---------------- utilidades ---------------- */
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "--" : Number(v).toFixed(d));
const conSigno = (v, d = 2) => (v === null || v === undefined ? "--" : (v >= 0 ? "+" : "") + Number(v).toFixed(d));
function horaAhora() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}
function css(nombre) {
  return getComputedStyle(document.body).getPropertyValue(nombre).trim();
}
function guardar(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* sin almacenamiento */ } }
function leer(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }

function registrar(texto, tipo = "info") {
  const li = document.createElement("li");
  li.dataset.tipo = tipo;
  const t = document.createElement("time");
  t.textContent = horaAhora();
  const s = document.createElement("span");
  s.textContent = texto;
  li.append(t, s);
  const ol = $("registro");
  ol.prepend(li);
  while (ol.children.length > 200) ol.lastChild.remove();
}

/* ---------------- matemáticas de actitud (igual que fusion.py) ---------------- */
function matriz(roll, pitch, yaw) {
  const r = (SIGNO_ROLL * roll * Math.PI) / 180, p = (pitch * Math.PI) / 180, y = (yaw * Math.PI) / 180;
  const cr = Math.cos(r), sr = Math.sin(r), cp = Math.cos(p), sp = Math.sin(p), cy = Math.cos(y), sy = Math.sin(y);
  return [
    [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
    [-sp, cp * sr, cp * cr],
  ];
}

/* ---------------- WebSocket ---------------- */
let ws = null;
function conectar() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => {
    estado.servidor = true;
    registrar("CONECTADO AL SERVIDOR", "ok");
  };
  ws.onclose = () => {
    if (estado.servidor) registrar("SERVIDOR DESCONECTADO · REINTENTANDO", "alarma");
    estado.servidor = false;
    pintarEnlace();
    setTimeout(conectar, 1500);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.tipo === "muestra") nuevaMuestra(msg.m);
    else if (msg.tipo === "historial") {
      estado.vivo = msg.muestras;
      if (estado.vivo.length) estado.ultimaFase = estado.vivo[estado.vivo.length - 1].fase;
      programar();
    } else if (msg.tipo === "enlace") {
      estado.enlace = msg;
      estado.enlaceRecibido = performance.now();
      pintarEnlace();
    } else if (msg.tipo === "respuesta") respuesta(msg);
  };
}
function ordenar(accion) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    registrar("SIN SERVIDOR: LA ORDEN NO SE ENVIÓ", "alarma");
    return;
  }
  ws.send(JSON.stringify({ accion }));
}
function respuesta(r) {
  const textos = {
    calibrar: r.ok ? "CALIBRACIÓN OK · SESGO EN CERO" : "CALIBRACIÓN FALLÓ: DEJA EL COHETE QUIETO 2 S Y REPITE",
    reiniciar_vuelo: "VUELO ARMADO · FASE EN RAMPA, MÁXIMOS EN CERO",
    reiniciar_integracion: "VELOCIDAD Y POSICIÓN EN CERO",
    nueva_sesion: `NUEVA SESIÓN · ${r.archivo}`,
  };
  registrar(textos[r.accion] || `ORDEN ${r.accion}`, r.ok ? "ok" : "alarma");
  if (r.accion === "reiniciar_vuelo" || r.accion === "nueva_sesion") {
    if (r.accion === "nueva_sesion") estado.vivo = [];
    estado.ultimaFase = "EN RAMPA";
  }
}

function nuevaMuestra(m) {
  estado.vivo.push(m);
  if (estado.vivo.length > MAX_VIVO) estado.vivo.splice(0, estado.vivo.length - MAX_VIVO);
  if (m.fase !== estado.ultimaFase) {
    if (estado.ultimaFase !== null) {
      const alarma = m.fase === "SIN DATOS IMU";
      registrar(alarma ? "IMU SIN DATOS (TRAMA EN CEROS)" : `FASE → ${m.fase}`, alarma ? "alarma" : "info");
      if (m.fase === "EMPUJE" || (m.fase === "VUELO LIBRE" && estado.ultimaFase === "EN RAMPA")) registrar("LANZAMIENTO DETECTADO", "ok");
      if (m.fase === "EN TIERRA") registrar(`EN TIERRA · T VUELO ${fmt(m.t_vuelo)} S · ALT EST ${fmt(m.altura_max, 1)} M · ${fmt(m.g_max)} G MÁX`, "ok");
      if (estado.modo === "vivo") destellar();
    }
    estado.ultimaFase = m.fase;
  }
  programar();
}

/* ---------------- enlace ---------------- */
function pintarEnlace() {
  const cab = $("enlace");
  const e = estado.enlace;
  let est, palabra, edad = "";
  if (!estado.servidor) { est = "servidor"; palabra = "SIN SERVIDOR"; edad = "INICIA servidor.py"; }
  else if (!e || e.paquetes === 0) { est = "espera"; palabra = "ESPERANDO COHETE"; edad = "CONÉCTATE A COHETE_ESP32"; }
  else if (e.en_linea) { est = "linea"; palabra = "EN LÍNEA"; }
  else {
    est = "perdido"; palabra = "ENLACE PERDIDO";
    const s = (e.edad_s || 0) + (performance.now() - estado.enlaceRecibido) / 1000;
    edad = `HACE ${s.toFixed(1)} S`;
  }
  cab.dataset.estado = est;
  $("enlaceTexto").textContent = palabra;
  $("enlaceEdad").textContent = edad;
  document.body.toggleAttribute("data-viejo", est !== "linea" && estado.modo === "vivo");
  if (e) {
    $("pps").textContent = fmt(e.pps, 1);
    $("perdida").textContent = `${Math.round(e.perdida * 100)} %`;
    $("origen").textContent = e.ip || "--";
    $("paquetes").textContent = e.paquetes;
    $("archivo").textContent = e.paquetes ? e.archivo : "(SE CREA CON EL PRIMER PAQUETE)";
  }
  const enLinea = est === "linea";
  if (estado.ultimoEnLinea !== null && enLinea !== estado.ultimoEnLinea && estado.servidor && e && e.paquetes) {
    registrar(enLinea ? `ENLACE RECUPERADO · ${e.ip}` : "ENLACE PERDIDO", enLinea ? "ok" : "alarma");
  }
  if (estado.servidor) estado.ultimoEnLinea = enLinea;
}
setInterval(() => { if ($("enlace").dataset.estado === "perdido") { pintarEnlace(); programar(); } }, 100);

/* ---------------- lecturas ---------------- */
function muestraActual() {
  if (estado.modo === "revision") return estado.revision[estado.cursor];
  return estado.vivo[estado.vivo.length - 1];
}

function pintarLecturas() {
  const m = muestraActual();
  if (!m) return;
  const faseEl = $("fase");
  faseEl.textContent = m.fase;
  const alarma = m.fase === "SIN DATOS IMU";
  faseEl.closest(".fase").toggleAttribute("data-alarma", alarma);
  const idx = FASES.indexOf(m.fase);
  for (const li of $("secuencia").children) {
    const i = FASES.indexOf(li.dataset.fase);
    li.toggleAttribute("data-hecho", idx >= 0 && i < idx);
    if (i === idx) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  }
  $("tVuelo").textContent = fmt(m.t_vuelo);
  $("altMax").textContent = fmt(m.altura_max, 1);
  $("vMax").textContent = fmt(m.v_max, 1);

  $("incl").textContent = fmt(m.inclinacion, 1);
  $("roll").textContent = fmt(m.roll, 1);
  $("pitch").textContent = fmt(m.pitch, 1);
  $("yaw").textContent = fmt(m.yaw, 1);

  $("gAhora").textContent = fmt(m.a_mag_g);
  $("gMax").textContent = fmt(m.g_max);
  $("gBarra").style.transform = `scaleX(${Math.min(m.a_mag_g / 16, 1)})`;
  $("gMarcaMax").style.left = `${Math.min(m.g_max / 16, 1) * 100}%`;
  $("reposo").textContent = m.reposo ? "SÍ" : "NO";
  $("satur").hidden = !m.saturada;
  $("gAhora").closest(".carga").toggleAttribute("data-alto", !!m.saturada);

  const set3 = (ids, v, d) => ids.forEach((id, i) => { $(id).textContent = v ? conSigno(v[i], d) : "--"; });
  set3(["ax", "ay", "az"], [m.ax, m.ay, m.az], 2);
  set3(["lx", "ly", "lz"], m.a_lin, 2);
  set3(["vx", "vy", "vz"], m.v, 2);
  set3(["px", "py", "pz"], m.p, 2);

  const coh = $("coh");
  coh.textContent = m.reposo ? `${fmt(m.coherencia, 1)}°` : "SOLO EN REPOSO";
  coh.toggleAttribute("data-mal", m.reposo && m.coherencia > 5);
  const fi = $("fiable");
  fi.textContent = m.fiable ? "FIABLE" : `NO FIABLE · ${fmt(m.t_sin_reposo, 0)} S SIN REPOSO`;
  fi.toggleAttribute("data-mal", !m.fiable);
  $("calib").textContent = m.calibrado ? "CALIBRADO" : "SIN CALIBRAR";
  $("hora").textContent = m.hora || "--";
}

function destellar() {
  const el = $("fase");
  el.classList.remove("cambio");
  void el.offsetWidth;
  el.classList.add("cambio");
}

/* ---------------- gráficas ---------------- */
const TRAZAS = [
  { id: "amag", nombre: "CARGA |A|", u: "G", series: [(m) => m.a_mag_g], min: 0, minMax: 2, ref: [1],
    valor: (m) => `${fmt(m.a_mag_g)} G` },
  { id: "acc", nombre: "ACEL. CUERPO", u: "G", series: [(m) => m.ax, (m) => m.ay, (m) => m.az], leyenda: ["X", "Y", "Z"], minMax: 1.2, ref: [0],
    valor: (m) => `${conSigno(m.ax, 1)} ${conSigno(m.ay, 1)} ${conSigno(m.az, 1)}` },
  { id: "ang", nombre: "ÁNGULOS", u: "°", series: [(m) => m.roll, (m) => m.pitch, (m) => m.yaw], leyenda: ["ROLL", "PITCH", "YAW"], fijo: [-180, 180], ref: [0],
    valor: (m) => `${fmt(m.inclinacion, 0)}° INCL.` },
  { id: "vel", nombre: "VELOCIDAD", u: "M/S", est: true, series: [(m) => m.v_mag, (m) => (m.v ? m.v[2] : null)], leyenda: ["|V|", "VZ"], minMax: 1, ref: [0],
    valor: (m) => `${fmt(m.v_mag, 1)} M/S` },
  { id: "alt", nombre: "ALTURA", u: "M", est: true, series: [(m) => (m.p ? m.p[2] : null)], minMax: 1, ref: [0],
    valor: (m) => `${m.p ? fmt(m.p[2], 1) : "--"} M` },
];
const TRAZOS = [[], [7, 4], [1.5, 3]];

function construirTrazas() {
  const cont = $("trazas");
  for (const tr of TRAZAS) {
    const fila = document.createElement("div");
    fila.className = "traza";
    const leyenda = tr.leyenda
      ? `<span class="traza__leyenda">${tr.leyenda.map((l, i) => `<span><i class="${["", "d", "p"][i]}"></i>${l}</span>`).join("")}</span>`
      : "";
    fila.innerHTML = `<div class="traza__cab"><span class="traza__nombre">${tr.nombre} · ${tr.u}</span>
      <output class="traza__valor" id="val-${tr.id}">--</output>${leyenda}
      ${tr.est ? '<abbr class="traza__est" title="Estimada por integración de la IMU: la deriva crece con el tiempo">ESTIMADA</abbr>' : ""}</div>
      <div class="lienzo"><canvas id="cv-${tr.id}" role="img" aria-label="${tr.nombre}"></canvas></div>`;
    cont.append(fila);
    tr.canvas = fila.querySelector("canvas");
  }
  const ro = new ResizeObserver(() => programar());
  TRAZAS.forEach((tr) => ro.observe(tr.canvas));
  ro.observe($("lienzoActitud"));
  ro.observe($("lienzoMapa"));
}

function prepararLienzo(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function datosVisibles() {
  if (estado.modo === "revision") {
    const d = estado.revision;
    return { d, t0: d.length ? d[0].t : 0, t1: d.length ? d[d.length - 1].t : 1, rel: (t) => t - (d.length ? d[0].t : 0) };
  }
  const d = estado.vivo;
  let t1 = d.length ? d[d.length - 1].t : 0;
  // sin enlace el reloj sigue corriendo: el hueco crece a la derecha
  if (document.body.hasAttribute("data-viejo") && estado.enlace && estado.enlace.edad_s) {
    t1 += Math.min(estado.enlace.edad_s + (performance.now() - estado.enlaceRecibido) / 1000, estado.ventana);
  }
  const t0 = t1 - estado.ventana;
  let i = d.length - 1;
  while (i > 0 && d[i - 1].t >= t0) i--;
  return { d: d.slice(Math.max(0, i - 1)), t0, t1, rel: (t) => t - t1 };
}

function pintarTrazas() {
  const col = { traza: css("--traza"), rejilla: css("--rejilla"), tinta2: css("--tinta-2"), tinta3: css("--tinta-3"), sombra: css("--sombreado"), alerta: css("--alerta") };
  const { d, t0, t1, rel } = datosVisibles();
  const cur = estado.modo === "revision" ? estado.revision[estado.cursor] : null;
  const ult = muestraActual();
  for (const tr of TRAZAS) {
    $(`val-${tr.id}`).textContent = ult ? tr.valor(ult) : "--";
    const { ctx, w, h } = prepararLienzo(tr.canvas);
    const izq = 44, der = 8, arr = 8, aba = 16;
    const W = w - izq - der, H = h - arr - aba;
    if (W <= 10 || H <= 10) continue;
    // rango Y
    let lo = Infinity, hi = -Infinity;
    if (tr.fijo) [lo, hi] = tr.fijo;
    else {
      for (const m of d) {
        if (m.vacia) continue;
        for (const f of tr.series) { const v = f(m); if (v !== null && v !== undefined) { if (v < lo) lo = v; if (v > hi) hi = v; } }
      }
      if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
      if (tr.min !== undefined) lo = Math.min(tr.min, lo);
      const span = Math.max(hi - lo, tr.minMax || 1);
      const centro = (hi + lo) / 2;
      lo = tr.min !== undefined ? lo : centro - span * 0.55;
      hi = tr.min !== undefined ? lo + span * 1.1 : centro + span * 0.55;
    }
    const X = (t) => izq + ((t - t0) / Math.max(t1 - t0, 1e-6)) * W;
    const Y = (v) => arr + (1 - (v - lo) / (hi - lo || 1)) * H;

    // rejilla
    ctx.lineWidth = 1;
    ctx.strokeStyle = col.rejilla;
    ctx.beginPath();
    for (let k = 0; k <= 4; k++) { const y = Math.round(arr + (k / 4) * H) + 0.5; ctx.moveTo(izq, y); ctx.lineTo(izq + W, y); }
    const pasoT = elegirPaso(t1 - t0);
    for (let s = Math.ceil(rel(t0) / pasoT) * pasoT; s <= rel(t1); s += pasoT) { const x = Math.round(X(s - rel(t0) + t0)) + 0.5; ctx.moveTo(x, arr); ctx.lineTo(x, arr + H); }
    ctx.stroke();
    // etiquetas
    ctx.fillStyle = col.tinta2;
    ctx.font = "500 10px 'JetBrains Mono', monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(fmtEje(hi), izq - 6, arr + 4);
    ctx.fillText(fmtEje(lo), izq - 6, arr + H - 4);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let s = Math.ceil(rel(t0) / pasoT) * pasoT; s <= rel(t1) + 1e-6; s += pasoT) {
      const x = X(s - rel(t0) + t0);
      if (x > izq + 14 && x < izq + W - 10) ctx.fillText(`${s > 0 ? "+" : ""}${Math.round(s * 10) / 10}`, x, arr + H + 3);
    }
    // líneas de referencia
    ctx.strokeStyle = col.tinta3;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    for (const r of tr.ref || []) if (r > lo && r < hi) { const y = Math.round(Y(r)) + 0.5; ctx.moveTo(izq, y); ctx.lineTo(izq + W, y); }
    ctx.stroke();
    ctx.setLineDash([]);

    // huecos (sin datos) sombreados
    ctx.fillStyle = patronRayado(ctx, col.sombra);
    for (let i = 1; i < d.length; i++) {
      const a = d[i - 1], b = d[i];
      if (b.t - a.t > HUECO_S || b.vacia) ctx.fillRect(X(a.t), arr, Math.max(2, X(b.t) - X(a.t)), H);
    }
    if (estado.modo === "vivo" && document.body.hasAttribute("data-viejo") && d.length) {
      const xa = X(d[d.length - 1].t);
      ctx.fillRect(xa, arr, izq + W - xa, H);
    }

    // series
    ctx.save();
    ctx.beginPath();
    ctx.rect(izq, arr, W, H);
    ctx.clip();
    ctx.strokeStyle = col.traza;
    ctx.lineWidth = 1.6;
    ctx.lineJoin = "round";
    tr.series.forEach((f, si) => {
      ctx.setLineDash(TRAZOS[si]);
      ctx.beginPath();
      let abierto = false, prevT = null;
      for (const m of d) {
        const v = m.vacia ? null : f(m);
        if (v === null || v === undefined || (prevT !== null && m.t - prevT > HUECO_S)) abierto = false;
        if (v !== null && v !== undefined) {
          const x = X(m.t), y = Y(v);
          if (abierto) ctx.lineTo(x, y); else ctx.moveTo(x, y);
          abierto = true;
        }
        prevT = m.t;
      }
      ctx.stroke();
    });
    ctx.restore();
    ctx.setLineDash([]);

    // cursor de revisión
    if (cur) {
      const x = Math.round(X(cur.t)) + 0.5;
      ctx.strokeStyle = col.traza;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, arr); ctx.lineTo(x, arr + H); ctx.stroke();
    }
    // marco
    ctx.strokeStyle = col.tinta3;
    ctx.strokeRect(izq + 0.5, arr + 0.5, W - 1, H - 1);
  }
}

const patrones = new Map();
function patronRayado(ctx, color) {
  if (patrones.has(color)) return patrones.get(color);
  const c = document.createElement("canvas");
  c.width = c.height = 8;
  const g = c.getContext("2d");
  g.strokeStyle = color.replace(/[\d.]+\)$/, "0.5)");
  g.lineWidth = 1.5;
  g.beginPath(); g.moveTo(0, 8); g.lineTo(8, 0); g.stroke();
  const p = ctx.createPattern(c, "repeat");
  patrones.set(color, p);
  return p;
}
function elegirPaso(span) {
  for (const p of [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]) if (span / p <= 8) return p;
  return 3600;
}
function fmtEje(v) {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
}

/* ---------------- actitud ---------------- */
function pintarActitud() {
  const cv = $("lienzoActitud");
  const { ctx, w, h } = prepararLienzo(cv);
  const tinta = css("--traza"), t2 = css("--tinta-2"), t3 = css("--tinta-3"), sombra = css("--sombreado");
  const m = muestraActual();
  const suelo = h - 22;
  const cx = w / 2;
  // suelo rayado
  ctx.fillStyle = patronRayado(ctx, sombra);
  ctx.fillRect(0, suelo, w, h - suelo);
  ctx.strokeStyle = tinta;
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(0, suelo + 0.5); ctx.lineTo(w, suelo + 0.5); ctx.stroke();
  // vertical y riel de 45°
  const largo = Math.min(suelo - 14, w * 0.62);
  ctx.lineWidth = 1;
  ctx.strokeStyle = t3;
  ctx.setLineDash([3, 4]);
  ctx.beginPath(); ctx.moveTo(cx, suelo); ctx.lineTo(cx, suelo - largo); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx, suelo); ctx.lineTo(cx + largo * Math.SQRT1_2, suelo - largo * Math.SQRT1_2); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = t2;
  ctx.font = "500 10px 'JetBrains Mono', monospace";
  ctx.textAlign = "left";
  ctx.fillText("RIEL 45°", cx + largo * Math.SQRT1_2 + 4, suelo - largo * Math.SQRT1_2);
  ctx.textAlign = "center";
  ctx.fillText("0°", cx, suelo - largo - 4);
  if (!m || m.vacia) {
    ctx.fillStyle = t2;
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.fillText(m ? "IMU SIN DATOS" : "SIN DATOS", cx, suelo / 2);
    return;
  }
  // dirección del eje del cohete en el mundo
  const R = matriz(m.roll, m.pitch, m.yaw);
  const ex = R[0][EJE_COHETE], ey = R[1][EJE_COHETE];
  const horiz = Math.hypot(ex, ey);
  const incl = m.inclinacion * Math.PI / 180;
  const lado = ex >= 0 ? 1 : -1;
  const ang = lado * incl;                      // desde la vertical, + a la derecha
  // arco de inclinación
  const rArco = largo * 0.42;
  ctx.strokeStyle = tinta;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(cx, suelo, rArco, -Math.PI / 2, -Math.PI / 2 + ang, ang < 0);
  ctx.stroke();
  // cohete
  const L = largo * 0.9, a = Math.max(9, L * 0.09);
  ctx.save();
  ctx.translate(cx, suelo);
  ctx.rotate(ang);
  ctx.fillStyle = tinta;
  ctx.strokeStyle = tinta;
  ctx.fillRect(-a / 2, -L * 0.82, a, L * 0.82);
  ctx.beginPath(); ctx.moveTo(-a / 2, -L * 0.82); ctx.lineTo(0, -L); ctx.lineTo(a / 2, -L * 0.82); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(-a / 2, -L * 0.2); ctx.lineTo(-a * 1.4, 0); ctx.lineTo(-a / 2, 0); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(a / 2, -L * 0.2); ctx.lineTo(a * 1.4, 0); ctx.lineTo(a / 2, 0); ctx.closePath(); ctx.fill();
  ctx.restore();
  // rumbo
  const rumbo = horiz > 0.05 ? ((Math.atan2(ey, ex) * 180) / Math.PI + 360) % 360 : null;
  ctx.fillStyle = t2;
  ctx.textAlign = "left";
  ctx.fillText(rumbo === null ? "RUMBO --" : `RUMBO ${rumbo.toFixed(0)}°`, 6, 14);
  ctx.textAlign = "right";
  ctx.fillText(`${m.inclinacion.toFixed(1)}° DE LA VERTICAL`, w - 6, 14);
}

/* ---------------- posición del equipo (GPS del navegador) ---------------- */
const gps = { estado: "INICIANDO", pos: null, recorrido: [], rampa: null, ultimoEnvio: 0 };
const R_TIERRA = 6371000;

/* lat/lon -> metros este/norte respecto a un origen (plano local, válido para unos km) */
function aMetros(o, p) {
  const lat0 = (o.lat * Math.PI) / 180;
  return {
    x: (((p.lon - o.lon) * Math.PI) / 180) * R_TIERRA * Math.cos(lat0),
    y: (((p.lat - o.lat) * Math.PI) / 180) * R_TIERRA,
  };
}
function rumboTexto(dx, dy) {
  const g = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
  const puntos = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
  return `${g.toFixed(0)}° ${puntos[Math.round(g / 45) % 8]}`;
}

function iniciarGps() {
  if (!("geolocation" in navigator)) { gps.estado = "NAVEGADOR SIN GPS"; return; }
  if (!window.isSecureContext) { gps.estado = "SOLO EN LOCALHOST"; return; }
  try {
    const r = JSON.parse(leer("cpwr.rampa") || "null");
    if (r && Number.isFinite(r.lat)) gps.rampa = r;
  } catch (_) { /* rampa guardada ilegible */ }
  navigator.geolocation.watchPosition((p) => {
    const nuevo = { lat: p.coords.latitude, lon: p.coords.longitude, alt: p.coords.altitude, prec: p.coords.accuracy, t: Date.now() };
    const prev = gps.recorrido[gps.recorrido.length - 1];
    if (!prev || Math.hypot(...Object.values(aMetros(prev, nuevo))) > 1 || nuevo.t - prev.t > 5000) {
      gps.recorrido.push(nuevo);
      if (gps.recorrido.length > 2000) gps.recorrido.shift();
    }
    if (!gps.pos) registrar(`UBICACIÓN OBTENIDA · ±${nuevo.prec.toFixed(0)} M`, "ok");
    gps.pos = nuevo;
    gps.estado = "OK";
    if (ws && ws.readyState === WebSocket.OPEN && nuevo.t - gps.ultimoEnvio > 1000) {
      ws.send(JSON.stringify({ accion: "ubicacion", lat: nuevo.lat, lon: nuevo.lon, alt: nuevo.alt, prec: nuevo.prec }));
      gps.ultimoEnvio = nuevo.t;
    }
    programar();
  }, (err) => {
    gps.estado = err.code === 1 ? "PERMISO DENEGADO" : err.code === 3 ? "SIN SEÑAL GPS" : "UBICACIÓN NO DISPONIBLE";
    programar();
  }, { enableHighAccuracy: true, maximumAge: 1000, timeout: 30000 });
}

function marcarRampa() {
  if (!gps.pos) { registrar(`NO SE PUEDE MARCAR LA RAMPA: ${gps.estado}`, "alarma"); return; }
  gps.rampa = { lat: gps.pos.lat, lon: gps.pos.lon };
  guardar("cpwr.rampa", JSON.stringify(gps.rampa));
  registrar(`RAMPA MARCADA · ${gps.rampa.lat.toFixed(6)}, ${gps.rampa.lon.toFixed(6)}`, "ok");
  programar();
}

/* ---------------- teselas OSM (las sirve servidor.py con copia local) ---------------- */
const mapa = { vista: leer("cpwr.vista") === "mapa" ? "mapa" : "tactico", zoom: 0, imgs: new Map(), fallos: 0 };
const UCALDAS = { lat: 5.056, lon: -75.4925 };   // centro por defecto sin GPS
const ANILLOS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000];

function tesela(z, x, y) {
  const k = `${z}/${x}/${y}`;
  const e = mapa.imgs.get(k);
  if (e && (e.ok || !e.fallo || Date.now() - e.fallo < 20000)) return e.ok ? e.img : null;
  const img = new Image();
  const reg = { img, ok: false, fallo: 0 };
  img.onload = () => { reg.ok = true; mapa.fallos = 0; programar(); };
  img.onerror = () => { reg.fallo = Date.now(); mapa.fallos++; programar(); };
  img.src = `/teselas/${k}.png`;
  mapa.imgs.set(k, reg);
  if (mapa.imgs.size > 400) mapa.imgs.delete(mapa.imgs.keys().next().value);
  return null;
}

/* dibuja las teselas con el origen en el centro y la escala esc (px por metro); devuelve cuántas faltan */
function pintarTeselas(ctx, w, h, o, esc) {
  const cosLat = Math.cos((o.lat * Math.PI) / 180);
  const z = Math.min(19, Math.max(3, Math.round(Math.log2(156543.03392 * cosLat * esc))));
  const s = (esc * 156543.03392 * cosLat) / 2 ** z;               // px de pantalla por px de tesela
  const n = 2 ** z, r = (o.lat * Math.PI) / 180;
  const x0 = ((o.lon + 180) / 360) * 256 * n;
  const y0 = ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 256 * n;
  const cx = w / 2, cy = h / 2;
  let faltan = 0;
  ctx.save();
  ctx.filter = document.body.dataset.theme === "noche" ? "grayscale(1) invert(1) brightness(0.85)" : "grayscale(1)";
  for (let ty = Math.floor((y0 - cy / s) / 256); ty <= Math.floor((y0 + cy / s) / 256); ty++) {
    if (ty < 0 || ty >= n) continue;
    for (let tx = Math.floor((x0 - cx / s) / 256); tx <= Math.floor((x0 + cx / s) / 256); tx++) {
      const img = tesela(z, ((tx % n) + n) % n, ty);
      if (!img) { faltan++; continue; }
      const izq = Math.floor(cx + (tx * 256 - x0) * s), arr = Math.floor(cy + (ty * 256 - y0) * s);
      ctx.drawImage(img, izq, arr, Math.floor(cx + ((tx + 1) * 256 - x0) * s) - izq, Math.floor(cy + ((ty + 1) * 256 - y0) * s) - arr);
    }
  }
  ctx.restore();
  return faltan;
}

function pintarMapa() {
  const cv = $("lienzoMapa");
  const { ctx, w, h } = prepararLienzo(cv);
  const tinta = css("--traza"), t2 = css("--tinta-2"), t3 = css("--tinta-3"), rej = css("--rejilla"), sombra = css("--sombreado");
  const conMapa = mapa.vista === "mapa";
  const p = gps.pos;
  $("gpsCoord").textContent = p ? `${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}` : gps.estado;
  $("gpsPrec").textContent = p ? `±${p.prec.toFixed(0)} M` : "--";
  $("gpsAlt").textContent = p && p.alt !== null ? `${p.alt.toFixed(0)} M` : "--";
  const cx = w / 2, cy = h / 2;
  ctx.font = "500 10px 'JetBrains Mono', monospace";
  if (!p && !conMapa) {
    $("gpsRampa").textContent = gps.rampa ? "MARCADA" : "SIN MARCAR";
    ctx.fillStyle = patronRayado(ctx, sombra);
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = t2;
    ctx.textAlign = "center";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.fillText(gps.estado, cx, cy);
    return;
  }
  // origen del mapa: la rampa si está marcada, si no la posición actual (o la universidad sin GPS)
  const origen = gps.rampa || p || UCALDAS;
  const puntos = gps.recorrido.map((q) => aMetros(origen, q));
  const yo = p ? aMetros(origen, p) : null;
  const alcance = p ? Math.max(p.prec, Math.hypot(yo.x, yo.y), ...puntos.map((q) => Math.hypot(q.x, q.y)), 5) : 1000;
  const radio = Math.min(w, h) / 2 - 14;
  const anillo0 = ANILLOS.find((r) => r * 2 >= alcance) || 50000;
  const esc = (radio / (anillo0 * 2)) * 2 ** mapa.zoom;           // px por metro, con el zoom manual
  const anillo = ANILLOS.find((r) => r * 2 >= radio / esc) || 50000;
  const X = (m) => cx + m * esc, Y = (m) => cy - m * esc;
  let faltan = 0;
  if (conMapa) faltan = pintarTeselas(ctx, w, h, origen, esc);
  // anillos y cruz
  ctx.strokeStyle = conMapa ? t3 : rej;
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, cy + 0.5); ctx.lineTo(w, cy + 0.5); ctx.moveTo(cx + 0.5, 0); ctx.lineTo(cx + 0.5, h); ctx.stroke();
  ctx.strokeStyle = conMapa ? t2 : t3;
  ctx.setLineDash([2, 4]);
  for (const k of [1, 2]) { ctx.beginPath(); ctx.arc(cx, cy, anillo * k * esc, 0, Math.PI * 2); ctx.stroke(); }
  ctx.setLineDash([]);
  ctx.fillStyle = conMapa ? tinta : t2;
  ctx.textAlign = "left";
  const etiqueta = (m) => (m >= 1000 ? `${m / 1000} KM` : `${m} M`);
  ctx.fillText(etiqueta(anillo), cx + anillo * esc + 3, cy - 3);
  if (cx + anillo * 2 * esc + 3 < w - 44) ctx.fillText(etiqueta(anillo * 2), cx + anillo * 2 * esc + 3, cy - 3);
  ctx.textAlign = "center";
  ctx.fillText("N", cx, 10);
  // recorrido
  if (puntos.length > 1) {
    ctx.strokeStyle = conMapa ? tinta : t2;
    ctx.setLineDash([1.5, 3]);
    ctx.beginPath();
    puntos.forEach((q, i) => (i ? ctx.lineTo(X(q.x), Y(q.y)) : ctx.moveTo(X(q.x), Y(q.y))));
    ctx.stroke();
    ctx.setLineDash([]);
  }
  // rampa
  if (gps.rampa) {
    ctx.strokeStyle = tinta;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx - 7, cy - 7); ctx.lineTo(cx + 7, cy + 7); ctx.moveTo(cx + 7, cy - 7); ctx.lineTo(cx - 7, cy + 7); ctx.stroke();
    ctx.lineWidth = 1;
    ctx.fillStyle = conMapa ? tinta : t2;
    ctx.textAlign = "left";
    ctx.fillText("RAMPA", cx + 9, cy + 14);
  }
  if (gps.rampa && yo) {
    const d = Math.hypot(yo.x, yo.y);
    $("gpsRampa").textContent = `${d < 1000 ? d.toFixed(0) + " M" : (d / 1000).toFixed(2) + " KM"} · ${rumboTexto(-yo.x, -yo.y)}`;
  } else {
    $("gpsRampa").textContent = gps.rampa ? "MARCADA" : "SIN MARCAR";
  }
  // yo: círculo de precisión + cuadro
  if (yo) {
    ctx.strokeStyle = conMapa ? tinta : t3;
    ctx.beginPath(); ctx.arc(X(yo.x), Y(yo.y), Math.max(3, p.prec * esc), 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = tinta;
    ctx.fillRect(X(yo.x) - 4, Y(yo.y) - 4, 8, 8);
    ctx.fillStyle = conMapa ? tinta : t2;
    ctx.textAlign = "left";
    ctx.fillText("TÚ", X(yo.x) + 7, Y(yo.y) - 6);
  }
  if (conMapa) {
    ctx.textAlign = "right";
    ctx.fillStyle = t2;
    ctx.fillText("© OPENSTREETMAP", w - 6, h - 6);
    if (faltan && mapa.fallos) {
      ctx.textAlign = "left";
      ctx.fillStyle = tinta;
      ctx.fillText("SIN INTERNET · ZONA NO GUARDADA", 6, h - 6);
    }
  }
}

function cambiarVista(v) {
  mapa.vista = v;
  guardar("cpwr.vista", v);
  document.querySelectorAll("[data-vista]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.vista === v)));
  programar();
}

async function guardarZona() {
  const b = $("btnGuardarMapa");
  if (b.disabled) return;
  b.disabled = true;
  const centro = gps.rampa || gps.pos;
  registrar(`GUARDANDO MAPA DE CALDAS${centro ? " Y DE ESTA ZONA" : ""}…`);
  try {
    const r = await (await fetch("/api/guardar_mapa", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(centro ? { lat: centro.lat, lon: centro.lon } : {}) })).json();
    registrar(`MAPA GUARDADO · ${r.ok} DE ${r.total} TESELAS${r.ok < r.total ? " · FALTAN, ¿SIN INTERNET?" : ""}`, r.ok === r.total ? "ok" : "alarma");
  } catch (_) {
    registrar("GUARDAR MAPA: SIN SERVIDOR", "alarma");
  } finally {
    b.disabled = false;
    mapa.imgs.clear();
    programar();
  }
}

/* ---------------- ciclo de pintado ---------------- */
function programar() {
  if (estado.pendiente) return;
  estado.pendiente = true;
  requestAnimationFrame(() => {
    estado.pendiente = false;
    pintarLecturas();
    pintarTrazas();
    pintarActitud();
    pintarMapa();
  });
}

/* ---------------- revisión de grabaciones ---------------- */
async function abrirSesiones() {
  const panel = $("sesiones");
  panel.hidden = false;
  $("btnSesiones").setAttribute("aria-expanded", "true");
  const ol = $("listaSesiones");
  ol.innerHTML = '<li class="sesiones__vacio">CARGANDO…</li>';
  try {
    const r = await fetch("/api/sesiones");
    const lista = await r.json();
    ol.innerHTML = "";
    if (!lista.length) { ol.innerHTML = '<li class="sesiones__vacio">NO HAY CSV EN MEDICIONES/ TODAVÍA.</li>'; return; }
    for (const s of lista) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.innerHTML = `<b></b><span></span><span></span><span>ABRIR →</span>`;
      b.children[0].textContent = s.nombre;
      b.children[1].textContent = `${s.kb} KB`;
      b.children[2].textContent = s.fecha;
      b.addEventListener("click", () => cargarRevision(s.nombre));
      li.append(b);
      ol.append(li);
    }
  } catch (_) {
    ol.innerHTML = '<li class="sesiones__vacio">NO SE PUDO LEER LA LISTA: ¿ESTÁ CORRIENDO servidor.py?</li>';
  }
}
function cerrarSesiones() {
  $("sesiones").hidden = true;
  $("btnSesiones").setAttribute("aria-expanded", "false");
}
async function cargarRevision(nombre) {
  try {
    const r = await fetch(`/api/sesion?archivo=${encodeURIComponent(nombre)}`);
    if (!r.ok) throw new Error(r.status);
    const datos = await r.json();
    if (!datos.muestras.length) { registrar(`${nombre} ESTÁ VACÍO`, "alarma"); return; }
    estado.revision = datos.muestras;
    estado.modo = "revision";
    estado.cursor = datos.muestras.length - 1;
    document.body.dataset.modo = "revision";
    document.body.removeAttribute("data-viejo");
    $("revision").hidden = false;
    $("revisionNombre").textContent = nombre;
    const c = $("cursor");
    c.max = String(datos.muestras.length - 1);
    c.value = String(estado.cursor);
    actualizarCursorT();
    cerrarSesiones();
    const f = datos.muestras[datos.muestras.length - 1];
    registrar(`REVISIÓN ${nombre} · ${datos.muestras.length} MUESTRAS · ${fmt(f.g_max)} G MÁX`, "ok");
    programar();
  } catch (e) {
    registrar(`NO SE PUDO ABRIR ${nombre}`, "alarma");
  }
}
function actualizarCursorT() {
  const d = estado.revision;
  if (!d.length) return;
  $("cursorT").textContent = `T ${(d[estado.cursor].t - d[0].t).toFixed(1)} S`;
}
function volverVivo() {
  estado.modo = "vivo";
  document.body.dataset.modo = "vivo";
  $("revision").hidden = true;
  pintarEnlace();
  programar();
}

/* ---------------- controles ---------------- */
function iniciar() {
  construirTrazas();
  document.querySelectorAll("[data-accion]").forEach((b) => b.addEventListener("click", () => ordenar(b.dataset.accion)));
  document.querySelectorAll("[data-ventana]").forEach((b) => b.addEventListener("click", () => {
    estado.ventana = Number(b.dataset.ventana);
    document.querySelectorAll("[data-ventana]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    guardar("cpwr.ventana", b.dataset.ventana);
    programar();
  }));
  const v = leer("cpwr.ventana");
  if (v) document.querySelector(`[data-ventana="${v}"]`)?.click();

  const sol = $("btnSol");
  const aplicarTema = (t) => {
    document.body.dataset.theme = t;
    sol.setAttribute("aria-pressed", String(t === "sol"));
    patrones.clear();
    programar();
  };
  const temaUrl = new URLSearchParams(location.search).get("tema");
  aplicarTema((temaUrl || leer("cpwr.tema")) === "sol" ? "sol" : "noche");
  sol.addEventListener("click", () => {
    const t = document.body.dataset.theme === "sol" ? "noche" : "sol";
    guardar("cpwr.tema", t);
    aplicarTema(t);
  });

  $("btnSesiones").addEventListener("click", () => ($("sesiones").hidden ? abrirSesiones() : cerrarSesiones()));
  $("btnCerrarSes").addEventListener("click", cerrarSesiones);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("sesiones").hidden) cerrarSesiones(); });
  $("btnVivo").addEventListener("click", volverVivo);
  $("cursor").addEventListener("input", (e) => { estado.cursor = Number(e.target.value); actualizarCursorT(); programar(); });

  $("btnLog").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    b.dataset.ocupado = "";
    registrar("PIDIENDO LOG A BORDO AL ESP32…");
    try {
      const r = await (await fetch("/api/descargar_log", { method: "POST" })).json();
      registrar(r.ok ? `LOG A BORDO GUARDADO · ${r.archivo} · ${r.kb} KB` : `LOG A BORDO: ${r.error}. NECESITA FIRMWARE V2.`, r.ok ? "ok" : "alarma");
      if (r.ok) cargarRevision(r.archivo);
    } catch (_) {
      registrar("LOG A BORDO: SIN SERVIDOR", "alarma");
    } finally {
      b.disabled = false;
      delete b.dataset.ocupado;
    }
  });

  $("btnRampa").addEventListener("click", marcarRampa);
  document.querySelectorAll("[data-vista]").forEach((b) => b.addEventListener("click", () => cambiarVista(b.dataset.vista)));
  cambiarVista(mapa.vista);
  $("mapaMas").addEventListener("click", () => { mapa.zoom = Math.min(6, mapa.zoom + 1); programar(); });
  $("mapaMenos").addEventListener("click", () => { mapa.zoom = Math.max(-10, mapa.zoom - 1); programar(); });
  $("btnGuardarMapa").addEventListener("click", guardarZona);

  pintarEnlace();
  programar();
  conectar();
  iniciarGps();
}
document.addEventListener("DOMContentLoaded", iniciar);
