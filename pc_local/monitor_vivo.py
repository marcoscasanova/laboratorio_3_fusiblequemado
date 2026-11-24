"""Monitor en vivo (PC local): muestra en el navegador cada lectura del Arduino en el instante en que la toma,
y cada ventana de 10 s que publica el ESP8266, sin pasar por la nube.

- Lee el puerto serie del Arduino Mega (USB) y difunde cada lectura de cada sensor apenas llega.
- Se suscribe a Mosquitto (utec/lab3/#) igual que pc_local.py, sin interferir con el.
- Sirve una pagina en http://localhost:8765 que se actualiza sola.
- Calcula las mismas incertidumbres y el punto de rocio que la API de la nube.

Uso:  python monitor_vivo.py          (busca solo el puerto del Arduino)
      python monitor_vivo.py COM5     (puerto elegido a mano)
Requiere pyserial (pip install pyserial). Al abrir el puerto, el Arduino se reinicia una vez.
Mientras corre, el monitor serie del IDE de Arduino no puede abrir el mismo puerto.
"""

import json
import queue
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

try:
    import serial  # pyserial
    import serial.tools.list_ports
except ImportError:
    serial = None

PUERTO_WEB = 8765  # puerto de la pagina local
BAUDIOS = 115200  # igual que Serial.begin() del nodo_edge
HISTORIAL = 60  # ventanas guardadas (10 min)
LECTURAS = 400  # lecturas instantaneas guardadas (unos 60 s con los periodos por defecto)

# Lineas del monitor serie del nodo_edge: "1776 ms  DS18B20 [C]: 25.12"
RE_LECTURA = re.compile(r"^(\d+) ms\s+(DS18B20|DHT11|LDR|HC-SR04) \[[^\]]+\]: (-?[\d.]+)")
RE_PERIODO = re.compile(r"Periodo del sensor (\d) = (\d+) ms")
SENSOR = {"DS18B20": 0, "DHT11": 1, "LDR": 2, "HC-SR04": 3}

historial = []  # ultimas ventanas recibidas por MQTT
lecturas = []  # ultimas lecturas instantaneas del puerto serie
estado = {"gateway": None, "ack": None, "serie": {"ok": False, "puerto": None, "msg": "buscando el Arduino"}}
clientes = []  # colas de los navegadores conectados
candado = threading.Lock()  # protege las listas, el estado y los clientes


def difundir(evento):
    """Manda un evento a todos los navegadores conectados."""
    with candado:
        for q in clientes:
            q.put(evento)


def ahora_ms():
    return int(time.time() * 1000)


# ── MQTT: ventanas, estado del gateway y confirmaciones ────────────────────────
def al_mensaje(cliente, datos, msg):
    """Callback MQTT: guarda la ventana, el estado o el ack y lo difunde."""
    try:
        texto = msg.payload.decode("utf-8", "replace")
        if msg.topic == "utec/lab3/telemetria":
            v = json.loads(texto)
            v["ts"] = ahora_ms()  # hora de llegada, como en pc_local.py
            with candado:
                historial.append(v)
                del historial[:-HISTORIAL]
            difundir({"tipo": "ventana", "datos": v})
        elif msg.topic == "utec/lab3/gateway/estado":
            with candado:
                estado["gateway"] = {"valor": texto, "ts": ahora_ms()}
            difundir({"tipo": "gateway", "datos": estado["gateway"]})
        elif msg.topic == "utec/lab3/config/ack":
            a = json.loads(texto)
            a["ts"] = ahora_ms()
            with candado:
                estado["ack"] = a
            difundir({"tipo": "ack", "datos": a})
    except (ValueError, KeyError):
        pass  # mensaje con formato inesperado


# ── Puerto serie: cada lectura del Arduino al instante ────────────────────────
def buscar_puerto():
    """Devuelve el puerto del Arduino: el pasado por argumento o el primero que parezca un Arduino."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    for p in serial.tools.list_ports.comports():
        texto = f"{p.description} {p.manufacturer or ''}".lower()
        if p.vid in (0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4) or "arduino" in texto or "ch340" in texto:
            return p.device
    return None


def cambiar_serie(ok, puerto, msg):
    with candado:
        estado["serie"] = {"ok": ok, "puerto": puerto, "msg": msg}
    difundir({"tipo": "serie", "datos": estado["serie"]})
    print(f"Puerto serie: {msg}", flush=True)


def leer_serie():
    """Hilo que lee el Arduino linea por linea y reintenta si se desconecta."""
    if serial is None:
        cambiar_serie(False, None, "falta pyserial (pip install pyserial)")
        return
    while True:
        puerto = buscar_puerto()
        if not puerto:
            cambiar_serie(False, None, "no se encontro el Arduino por USB")
            time.sleep(3)
            continue
        try:
            with serial.Serial(puerto, BAUDIOS, timeout=1) as s:
                cambiar_serie(True, puerto, f"leyendo {puerto}")
                while True:
                    linea = s.readline().decode("ascii", "replace").strip()
                    if linea:
                        procesar_linea(linea)
        except serial.SerialException as e:
            cambiar_serie(False, puerto, f"{puerto} no disponible ({e.__class__.__name__}); se reintenta")
            time.sleep(3)


def procesar_linea(linea):
    """Convierte una linea del nodo_edge en una lectura o en un cambio de periodo."""
    m = RE_LECTURA.match(linea)
    if m:
        lec = {"s": SENSOR[m.group(2)], "v": float(m.group(3)), "ms": int(m.group(1)), "ts": ahora_ms()}
        with candado:
            lecturas.append(lec)
            del lecturas[:-LECTURAS]
        difundir({"tipo": "lectura", "datos": lec})
        return
    m = RE_PERIODO.search(linea)
    if m:
        difundir({"tipo": "periodo", "datos": {"s": int(m.group(1)), "p": int(m.group(2)), "ts": ahora_ms()}})


# ── Servidor web con Server-Sent Events ───────────────────────────────────────
class Pagina(BaseHTTPRequestHandler):
    """Sirve la pagina y el canal de eventos."""

    def log_message(self, *args):
        pass  # sin log por cada pedido

    def do_GET(self):
        if self.path == "/eventos":
            self.eventos()
            return
        cuerpo = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def eventos(self):
        """Mantiene abierta la conexion y le manda cada evento al navegador."""
        q = queue.Queue()
        with candado:
            inicial = {"tipo": "inicio", "datos": {"historial": list(historial), "lecturas": list(lecturas), **estado}}
            clientes.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.enviar(inicial)
            while True:
                try:
                    self.enviar(q.get(timeout=15))
                except queue.Empty:
                    self.wfile.write(b": latido\n\n")  # mantiene viva la conexion
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # el navegador se cerro
        finally:
            with candado:
                clientes.remove(q)

    def enviar(self, evento):
        self.wfile.write(("data: " + json.dumps(evento) + "\n\n").encode("utf-8"))
        self.wfile.flush()


HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor en vivo</title>
<style>
  :root { --bg:#f4f4f2; --card:#fff; --txt:#1d1d1b; --mut:#6b6b66; --line:#e2e2dd; --acc:#2f6fd6; --ok:#1f9d4c; --mal:#c93a3a; --banda:#2f6fd622; --prom:#e8952e; }
  @media (prefers-color-scheme: dark) { :root { --bg:#17181a; --card:#222427; --txt:#ececea; --mut:#a3a39e; --line:#34363a; --acc:#7aa7f0; --ok:#4cc27a; --mal:#ec6b6b; --banda:#7aa7f033; --prom:#f0b060; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font:14px/1.4 system-ui, sans-serif; }
  main { max-width:1180px; margin:0 auto; padding:20px 16px 32px; }
  h1 { font-size:22px; margin:0; }
  .sub { color:var(--mut); margin:2px 0 16px; }
  .estado { display:flex; flex-wrap:wrap; gap:8px 18px; align-items:center; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin-bottom:16px; }
  .estado b { font-weight:600; font-variant-numeric:tabular-nums; }
  .punto { display:inline-block; width:9px; height:9px; border-radius:50%; background:var(--mut); margin-right:6px; }
  .barra { flex:1 1 120px; height:6px; background:var(--line); border-radius:3px; overflow:hidden; min-width:100px; }
  .barra div { height:100%; width:0; background:var(--prom); }
  .grilla { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px 10px; }
  .card h2 { font-size:14px; margin:0; font-weight:600; }
  .card .sen { color:var(--mut); font-size:12px; }
  .valor { font-size:34px; font-weight:650; font-variant-numeric:tabular-nums; margin:6px 0 0; letter-spacing:-.5px; transition:color .4s; }
  .valor small { font-size:15px; font-weight:400; color:var(--mut); letter-spacing:0; }
  .valor.nuevo { color:var(--acc); transition:none; }
  .meta { color:var(--mut); font-size:12px; font-variant-numeric:tabular-nums; margin:2px 0; min-height:17px; }
  .prom { font-size:12px; font-variant-numeric:tabular-nums; margin:4px 0 6px; padding-top:6px; border-top:1px dashed var(--line); }
  .prom b { color:var(--prom); font-weight:600; }
  canvas { width:100%; height:64px; display:block; }
  .fila2 { display:grid; grid-template-columns:2fr 3fr; gap:12px; margin-top:12px; }
  @media (max-width:760px) { .fila2 { grid-template-columns:1fr; } }
  .caja { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; }
  .caja h3 { font-size:13px; margin:0 0 6px; }
  #log { font:12px/1.5 ui-monospace, Consolas, monospace; height:180px; overflow:hidden; color:var(--mut); }
  #log div:first-child { color:var(--txt); }
  .pie { color:var(--mut); font-size:12px; margin-top:16px; }
  .ley { display:inline-flex; align-items:center; gap:5px; margin-right:12px; }
  .ley i { display:inline-block; width:14px; height:3px; border-radius:2px; }
</style>
</head>
<body>
<main>
  <h1>Monitor en vivo</h1>
  <p class="sub">Lecturas del Arduino en el instante en que se toman (puerto serie) y ventanas de 10 s que publica el ESP8266 (MQTT) · Grupo 06</p>

  <div class="estado">
    <span><span class="punto" id="pse"></span>Arduino <b id="se">–</b></span>
    <span><span class="punto" id="pgw"></span>Gateway <b id="gw">sin datos</b></span>
    <span>Ventana <b id="hace">–</b></span>
    <div class="barra" title="Próxima ventana del ESP8266"><div id="prog"></div></div>
    <span>seq <b id="seq">–</b></span>
    <span>errores I2C <b id="err">–</b></span>
    <span>WiFi <b id="rssi">–</b></span>
  </div>

  <div class="grilla" id="grilla"></div>

  <div class="fila2">
    <div class="caja"><h3>Configuración</h3><div id="ack" style="font-size:13px">Sin cambios de configuración desde que se abrió el monitor.</div></div>
    <div class="caja"><h3>Lecturas del Arduino (monitor serie)</h3><div id="log"></div></div>
  </div>

  <p class="pie">
    <span class="ley"><i style="background:var(--acc)"></i>cada lectura del Arduino (último minuto)</span>
    <span class="ley"><i style="background:var(--prom)"></i>promedio de la ventana de 10 s que va a la nube</span><br>
    El valor grande es la última lectura, con su incertidumbre (mismas fórmulas que la API de la nube). El promedio de la ventana es lo que el ESP8266 publica y la PC sube al dashboard.
  </p>
</main>
<script>
const RF = 10.0, RF_INC = 0.5; // divisor de la LDR [kOhm]
const SENS = [
  { k:"t", nombre:"Temperatura", sen:"DS18B20", u:"°C", dec:2 },
  { k:"h", nombre:"Humedad relativa", sen:"DHT11", u:"%HR", dec:1 },
  { k:"l", nombre:"Luminosidad", sen:"LDR GL5528, resistencia", u:"kΩ", dec:2 },
  { k:"d", nombre:"Distancia", sen:"HC-SR04, compensada en T", u:"cm", dec:1 },
  { k:"r", nombre:"Punto de rocío", sen:"derivada: DS18B20 + DHT11", u:"°C", dec:2 },
];
const NOM = ["temperatura", "humedad", "LDR", "distancia"], CORTO = ["DS18B20", "DHT11", "LDR", "HC-SR04"];
const g = document.getElementById("grilla");
for (const s of SENS) g.insertAdjacentHTML("beforeend",
  `<div class="card"><h2>${s.nombre}</h2><div class="sen">${s.sen}</div>
   <div class="valor" id="v-${s.k}">–</div><div class="meta" id="m-${s.k}">&nbsp;</div>
   <canvas id="g-${s.k}"></canvas><div class="prom" id="p-${s.k}">Ventana: –</div></div>`);

// Incertidumbres (igual que _metrologia.php)
const incT = t => Math.hypot((t >= -10 && t <= 85) ? 0.5 : 2.0, 0.0625 / 2);
const incH = () => Math.hypot(5, 0.5);
function incL(r) { const n = 1024 * RF / (RF + r); return Math.hypot(RF * 1024 / (n * n) * 0.5, (1024 / n - 1) * RF_INC); }
function incD(d, t, it) { const v = 331.3 + 0.606 * t, tv = 2 * (d / 100) / v;
  return Math.sqrt((tv / 2 * 0.606 * it) ** 2 + (v / 2 * 2e-6) ** 2 + 0.003 ** 2) * 100; }
function rocio(t, h) { const it = incT(t), ih = incH(), a = 17.62, b = 243.12, gg = Math.log(h / 100) + a * t / (b + t);
  const td = b * gg / (a - gg), dg = a * b / (a - gg) ** 2;
  return [td, Math.hypot(dg * a * b / (b + t) ** 2 * it, dg / h * ih)]; }
function conInc(k, x, t) {
  if (x == null) return null;
  if (k === "t") return [x, incT(x)];
  if (k === "h") return [x, incH()];
  if (k === "l") return [x, incL(x)];
  if (k === "d") return [x, incD(x, t ?? 20, t == null ? 5 : incT(t))];
}
const fmtP = ms => ms == null ? "–" : ms >= 1000 ? (ms / 1000).toLocaleString("es") + " s" : ms + " ms";
const fmt = (x, d) => x.toLocaleString("es", { minimumFractionDigits:d, maximumFractionDigits:d });

// Estado en el navegador
const VENTANA_MS = 60000; // las graficas muestran el ultimo minuto
let lec = { t:[], h:[], l:[], d:[], r:[] }; // lecturas instantaneas [ts, valor, inc]
let ult = { t:null, h:null }; // ultima temperatura y humedad para el rocio y la compensacion
let ventanas = [], ultimaVentana = 0, ultimoMs = [null, null, null, null], periodo = [null, null, null, null];

function grafica(k) {
  const c = document.getElementById("g-" + k), ctx = c.getContext("2d");
  const dpr = devicePixelRatio || 1, W = c.clientWidth, H = c.clientHeight;
  c.width = W * dpr; c.height = H * dpr; ctx.scale(dpr, dpr); ctx.clearRect(0, 0, W, H);
  const fin = Date.now(), ini = fin - VENTANA_MS;
  const pts = lec[k].filter(p => p[0] >= ini);
  const prom = ventanas.filter(w => w.ts >= ini && w.calc[k]).map(w => [w.ts, w.calc[k][0]]);
  const vals = pts.flatMap(p => [p[1] - p[2], p[1] + p[2]]).concat(prom.map(p => p[1]));
  if (!vals.length) return;
  let lo = Math.min(...vals), hi = Math.max(...vals); if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
  const css = getComputedStyle(document.documentElement);
  const x = ts => (ts - ini) / VENTANA_MS * W, y = v => H - 4 - (v - lo) / (hi - lo) * (H - 8);
  if (pts.length > 1) {
    ctx.fillStyle = css.getPropertyValue("--banda"); ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(x(p[0]), y(p[1] + p[2])) : ctx.moveTo(x(p[0]), y(p[1] + p[2])));
    for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(x(pts[i][0]), y(pts[i][1] - pts[i][2]));
    ctx.fill();
    ctx.strokeStyle = css.getPropertyValue("--acc"); ctx.lineWidth = 1.6; ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(x(p[0]), y(p[1])) : ctx.moveTo(x(p[0]), y(p[1])));
    ctx.stroke();
  }
  ctx.fillStyle = css.getPropertyValue("--prom");
  for (const p of prom) { ctx.beginPath(); ctx.arc(x(p[0]), y(p[1]), 3.5, 0, 7); ctx.fill(); }
}
function dibujarTodo() { SENS.forEach(s => grafica(s.k)); }

function mostrarValor(k, p, dec, u) {
  const e = document.getElementById("v-" + k);
  e.innerHTML = p ? `${fmt(p[0], dec)} <small>± ${fmt(p[1], dec)} ${u}</small>` : "<small>sin lecturas</small>";
  e.classList.add("nuevo"); requestAnimationFrame(() => requestAnimationFrame(() => e.classList.remove("nuevo")));
}
function meta(i) {
  const el = document.getElementById("m-" + SENS[i].k);
  el.textContent = `período ${fmtP(periodo[i])}` + (ultimoMs[i] != null ? ` · t = ${ultimoMs[i].toLocaleString("es")} ms` : "");
}

const logEl = document.getElementById("log"), LOG_MAX = 10;
function alLog(txt) { const d = document.createElement("div"); d.textContent = txt; logEl.prepend(d); while (logEl.children.length > LOG_MAX) logEl.lastChild.remove(); }

function lectura(L, animar) {
  const k = SENS[L.s].k, s = SENS[L.s];
  const p = conInc(k, L.v, ult.t);
  lec[k].push([L.ts, p[0], p[1]]); lec[k] = lec[k].filter(q => q[0] >= Date.now() - VENTANA_MS - 5000);
  if (k === "t") ult.t = L.v; if (k === "h") ult.h = L.v;
  ultimoMs[L.s] = L.ms;
  if (animar) { mostrarValor(k, p, s.dec, s.u); meta(L.s); alLog(`${L.ms} ms  ${CORTO[L.s]}: ${L.v.toFixed(2)}`); }
  if ((k === "t" || k === "h") && ult.t != null && ult.h != null && ult.h > 0) {
    const r = rocio(ult.t, ult.h); lec.r.push([L.ts, r[0], r[1]]); lec.r = lec.r.filter(q => q[0] >= Date.now() - VENTANA_MS - 5000);
    if (animar) mostrarValor("r", r, 2, "°C");
  }
}

function calcVentana(w) {
  const [t, h, l, d] = w.v, o = {};
  o.t = conInc("t", t); o.h = conInc("h", h); o.l = conInc("l", l); o.d = conInc("d", d, t);
  o.r = (t == null || h == null || h <= 0) ? null : rocio(t, h);
  return o;
}
function ventana(w) {
  w.calc = calcVentana(w); ventanas.push(w); ventanas = ventanas.slice(-60); ultimaVentana = w.ts;
  if (w.p) w.p.forEach((p, i) => { periodo[i] = p; meta(i); });
  document.getElementById("seq").textContent = w.seq;
  document.getElementById("err").textContent = w.err;
  document.getElementById("rssi").textContent = w.rssi + " dBm";
  SENS.forEach((s, i) => {
    const p = w.calc[s.k];
    document.getElementById("p-" + s.k).innerHTML = "Ventana 10 s: " + (p ? `<b>${fmt(p[0], s.dec)} ± ${fmt(p[1], s.dec)} ${s.u}</b>` : "<b>sin muestras</b>") + (i < 4 ? ` · n = ${w.n[i]}` : "");
  });
  if (!estadoSerie) SENS.forEach(s => mostrarValor(s.k, w.calc[s.k], s.dec, s.u)); // sin puerto serie, muestra la ventana
}
let estadoSerie = false;
function serie(e) {
  estadoSerie = e.ok;
  document.getElementById("se").textContent = e.msg;
  document.getElementById("pse").style.background = e.ok ? "var(--ok)" : "var(--mal)";
}
function gateway(e) {
  if (!e) return;
  const on = e.valor === "online";
  document.getElementById("gw").textContent = on ? "en línea" : "fuera de línea";
  document.getElementById("pgw").style.background = on ? "var(--ok)" : "var(--mal)";
}
function ack(a) {
  if (!a) return;
  const h = new Date(a.ts).toLocaleTimeString("es");
  document.getElementById("ack").innerHTML = `Último comando recibido: <b>${NOM[a.sensor] ?? a.sensor} → ${fmtP(a.periodo_ms)}</b><br>` +
    (a.ok ? `<span style="color:var(--ok)">✓ aplicado en el Arduino</span>` : `<span style="color:var(--mal)">✗ falló</span>`) + ` a las ${h} (comando #${a.cmd})`;
}

const es = new EventSource("/eventos");
es.onmessage = m => {
  const e = JSON.parse(m.data), d = e.datos;
  if (e.tipo === "inicio") {
    serie(d.serie); gateway(d.gateway); ack(d.ack);
    d.historial.forEach(ventana); d.lecturas.forEach(L => lectura(L, false));
    for (const L of d.lecturas.slice(-LOG_MAX)) alLog(`${L.ms} ms  ${CORTO[L.s]}: ${L.v.toFixed(2)}`);
    SENS.forEach((s, i) => { const u = lec[s.k].at(-1); if (u) mostrarValor(s.k, [u[1], u[2]], s.dec, s.u); if (i < 4) meta(i); });
    dibujarTodo();
  }
  else if (e.tipo === "lectura") lectura(d, true);
  else if (e.tipo === "ventana") ventana(d);
  else if (e.tipo === "periodo") { periodo[d.s] = d.p; meta(d.s); alLog(`Periodo del sensor ${d.s} = ${d.p} ms`); }
  else if (e.tipo === "serie") serie(d);
  else if (e.tipo === "gateway") gateway(d);
  else if (e.tipo === "ack") ack(d);
};
setInterval(() => {
  dibujarTodo();
  if (!ultimaVentana) return;
  const s = (Date.now() - ultimaVentana) / 1000;
  document.getElementById("hace").textContent = `hace ${Math.floor(s)} s`;
  document.getElementById("prog").style.width = Math.min(100, s / 10 * 100) + "%";
}, 250);
</script>
</body>
</html>
"""


def main():
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fusiblequemado-monitor")
    cliente.on_message = al_mensaje
    cliente.on_connect = lambda c, u, f, rc, p: c.subscribe("utec/lab3/#")  # se resuscribe si reconecta
    try:
        cliente.connect("localhost", 1883)
        cliente.loop_start()  # MQTT en su propio hilo
    except OSError:
        print("No se pudo conectar a Mosquitto: solo se muestran las lecturas del puerto serie", flush=True)
    threading.Thread(target=leer_serie, daemon=True).start()  # puerto serie en otro hilo
    servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO_WEB), Pagina)
    servidor.daemon_threads = True
    print(f"Monitor en vivo en http://localhost:{PUERTO_WEB}  (Ctrl+C para cerrar)", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cliente.loop_stop()


if __name__ == "__main__":
    main()
