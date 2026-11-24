"""PC local (Nivel 2): puente entre el ESP8266 (MQTT) y la nube (API PHP del grupo 06).

- Recibe la telemetria del gateway, le pone la hora de llegada y la guarda en SQLite.
- Cada 10 min sube a la nube las ventanas que todavia no se subieron.
- Cada 30 s consulta la nube por comandos de configuracion y los publica al ESP8266.
- Reenvia un comando si el ESP8266 no lo confirma en 60 s.
- Sube cada confirmacion (ack) apenas llega, para que el dashboard la muestre enseguida.

Uso:  python pc_local.py   (lee config_pc.ini de esta misma carpeta)
"""

import configparser
import json
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt

CARPETA = Path(__file__).resolve().parent
TOPICO_DATOS = "utec/lab3/telemetria"
TOPICO_CONFIG = "utec/lab3/config"
TOPICO_ACK = "utec/lab3/config/ack"
TOPICO_ESTADO = "utec/lab3/gateway/estado"
LOTE_MAX = 500  # ventanas por POST
NOMBRES = ["temperatura", "humedad", "LDR", "distancia"]


def log(msg):
    """Imprime con la hora local."""
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def ahora_ms():
    """Hora actual en milisegundos epoch (UTC)."""
    return int(time.time() * 1000)


class Nube:
    """Cliente minimo de la API PHP (solo libreria estandar)."""

    def __init__(self, url, api_key, timeout=20):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _pedir(self, metodo, ruta, cuerpo=None, params=None):
        url = f"{self.url}/{ruta}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(url, data=datos, method=metodo)
        req.add_header("X-Api-Key", self.api_key)
        req.add_header("User-Agent", "FusibleQuemado-PC/1.0")
        if datos is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detalle = e.read().decode(errors="replace")[:200]
            raise RuntimeError(f"HTTP {e.code} en {ruta}: {detalle}") from None

    def subir_ventanas(self, ventanas, gateway):
        return self._pedir("POST", "telemetria.php", {"gateway": gateway, "ventanas": ventanas})

    def comandos_pendientes(self, gateway):
        return self._pedir("GET", "config_pendiente.php", params={"gateway": gateway})["comandos"]

    def subir_ack(self, ack):
        return self._pedir("POST", "ack.php", ack)


class Base:
    """Base local SQLite: ventanas recibidas y acks pendientes de subir."""

    def __init__(self, ruta):
        self.con = sqlite3.connect(ruta, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.con:
            self.con.executescript("""
                CREATE TABLE IF NOT EXISTS ventanas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    seq INTEGER NOT NULL,
                    json TEXT NOT NULL,
                    subida INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS k_pend ON ventanas (subida);
                CREATE TABLE IF NOT EXISTS acks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    json TEXT NOT NULL,
                    subido INTEGER NOT NULL DEFAULT 0
                );
            """)

    def guardar_ventana(self, ventana):
        with self.lock, self.con:
            self.con.execute("INSERT INTO ventanas (ts_ms, seq, json) VALUES (?, ?, ?)",
                             (ventana["ts"], ventana["seq"], json.dumps(ventana)))

    def ventanas_pendientes(self, limite):
        with self.lock:
            filas = self.con.execute("SELECT id, json FROM ventanas WHERE subida = 0 ORDER BY id LIMIT ?",
                                     (limite,)).fetchall()
        return [f[0] for f in filas], [json.loads(f[1]) for f in filas]

    def marcar_subidas(self, ids):
        with self.lock, self.con:
            self.con.executemany("UPDATE ventanas SET subida = 1 WHERE id = ?", [(i,) for i in ids])

    def guardar_ack(self, ack):
        with self.lock, self.con:
            self.con.execute("INSERT INTO acks (json) VALUES (?)", (json.dumps(ack),))

    def acks_pendientes(self):
        with self.lock:
            filas = self.con.execute("SELECT id, json FROM acks WHERE subido = 0 ORDER BY id").fetchall()
        return [(f[0], json.loads(f[1])) for f in filas]

    def marcar_ack(self, id_):
        with self.lock, self.con:
            self.con.execute("UPDATE acks SET subido = 1 WHERE id = ?", (id_,))

    def contar_pendientes(self):
        with self.lock:
            return self.con.execute("SELECT COUNT(*) FROM ventanas WHERE subida = 0").fetchone()[0]


class PCLocal:
    def __init__(self, cfg):
        self.nube = Nube(cfg["nube"]["url"], cfg["nube"]["api_key"])
        self.base = Base(CARPETA / cfg["local"].get("base", "datos_locales.db"))
        self.t_subida = cfg["tiempos"].getfloat("subida_s", 600)
        self.t_polling = cfg["tiempos"].getfloat("polling_s", 30)
        self.t_reenvio = cfg["tiempos"].getfloat("reenvio_s", 60)
        self.gateway = "desconocido"  # online / offline segun el last will del ESP8266
        self.enviados = {}  # cmd -> (comando, momento del ultimo envio)
        self.lock_cmd = threading.Lock()
        self.hay_ack = threading.Event()  # despierta el bucle para subir un ack al instante
        self.fin = threading.Event()

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fusiblequemado-pc")
        self.mqtt.on_connect = self.al_conectar
        self.mqtt.on_message = self.al_mensaje
        self.mqtt.reconnect_delay_set(1, 30)
        self.broker = (cfg["mqtt"].get("host", "localhost"), cfg["mqtt"].getint("port", 1883))

    # ---------- MQTT (corre en el hilo de paho) ----------

    def al_conectar(self, cliente, userdata, flags, codigo, props):
        if codigo.is_failure:
            log(f"MQTT: el broker rechazo la conexion ({codigo})")
            return
        log(f"MQTT: conectado a {self.broker[0]}:{self.broker[1]}")
        cliente.subscribe([(TOPICO_DATOS, 0), (TOPICO_ACK, 1), (TOPICO_ESTADO, 1)])

    def al_mensaje(self, cliente, userdata, msg):
        texto = msg.payload.decode(errors="replace")
        if msg.topic == TOPICO_ESTADO:
            self.gateway = texto[:32]
            log(f"Gateway: {self.gateway}")
            return
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            log(f"Mensaje invalido en {msg.topic}: {texto[:80]}")
            return
        if msg.topic == TOPICO_DATOS:
            datos["ts"] = ahora_ms()  # la hora la pone la PC al recibir (el ESP8266 no tiene reloj)
            self.base.guardar_ventana(datos)
            n = datos.get("n", [])
            log(f"Ventana seq={datos.get('seq')} n={n} p={datos.get('p')}")
        elif msg.topic == TOPICO_ACK:
            ack = {"cmd": datos.get("cmd"), "ok": bool(datos.get("ok")), "periodos": datos.get("periodos"),
                   "ts": ahora_ms()}
            with self.lock_cmd:
                self.enviados.pop(ack["cmd"], None)
            self.base.guardar_ack(ack)
            sensor = datos.get("sensor")
            nombre = NOMBRES[sensor] if isinstance(sensor, int) and 0 <= sensor < 4 else sensor
            log(f"ACK cmd={ack['cmd']} ({nombre} -> {datos.get('periodo_ms')} ms): "
                f"{'aplicado' if ack['ok'] else 'FALLO'}, periodos={ack['periodos']}")
            self.hay_ack.set()

    # ---------- tareas periodicas (hilo principal) ----------

    def subir_acks(self):
        for id_, ack in self.base.acks_pendientes():
            try:
                self.nube.subir_ack(ack)
                self.base.marcar_ack(id_)
                log(f"Nube: ack del comando {ack['cmd']} subido")
            except Exception as e:  # sin red o error del servidor: se reintenta en la proxima vuelta
                if "HTTP 404" in str(e):
                    self.base.marcar_ack(id_)  # el comando ya no existe en la nube
                log(f"Nube: no se pudo subir el ack {ack['cmd']}: {e}")
                return

    def subir_ventanas(self):
        total = 0
        while True:
            ids, ventanas = self.base.ventanas_pendientes(LOTE_MAX)
            if not ventanas:
                break
            try:
                r = self.nube.subir_ventanas(ventanas, self.gateway)
            except Exception as e:
                log(f"Nube: fallo la subida ({e}); quedan {self.base.contar_pendientes()} ventanas en espera")
                return
            self.base.marcar_subidas(ids)
            total += r.get("insertadas", 0)
            if len(ventanas) < LOTE_MAX:
                break
        log(f"Nube: {total} ventanas subidas")

    def consultar_comandos(self):
        try:
            comandos = self.nube.comandos_pendientes(self.gateway)
        except Exception as e:
            log(f"Nube: fallo el polling ({e})")
            return
        pendientes = {c["cmd"] for c in comandos}
        ahora = time.monotonic()
        with self.lock_cmd:
            for cmd in list(self.enviados):  # reemplazados o confirmados por otra via
                if cmd not in pendientes:
                    del self.enviados[cmd]
            for c in comandos:
                previo = self.enviados.get(c["cmd"])
                if previo and ahora - previo[1] < self.t_reenvio:
                    continue  # ya se envio hace poco, se espera el ack
                info = self.mqtt.publish(TOPICO_CONFIG, json.dumps(c), qos=1)
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    self.enviados[c["cmd"]] = (c, ahora)
                    log(f"{'Reenviado' if previo else 'Enviado'} al gateway: cmd={c['cmd']} "
                        f"{NOMBRES[c['sensor']]} -> {c['periodo_ms']} ms")
                else:
                    log(f"MQTT: no se pudo publicar el comando {c['cmd']} (rc={info.rc})")

    def correr(self):
        self.mqtt.connect_async(*self.broker)
        self.mqtt.loop_start()
        log(f"PC local en marcha: subida cada {self.t_subida:.0f} s, polling cada {self.t_polling:.0f} s")
        proxima_subida = time.monotonic() + 5  # sube lo pendiente de una ejecucion anterior
        proximo_poll = time.monotonic()
        while not self.fin.is_set():
            ahora = time.monotonic()
            self.hay_ack.clear()
            self.subir_acks()
            if ahora >= proximo_poll:
                self.consultar_comandos()
                proximo_poll = ahora + self.t_polling
            if ahora >= proxima_subida:
                self.subir_ventanas()
                proxima_subida = ahora + self.t_subida
            espera = max(0.1, min(proximo_poll, proxima_subida) - time.monotonic())
            self.hay_ack.wait(min(espera, 5))  # se despierta antes si llega un ack
        log("Cerrando: subiendo lo pendiente...")
        self.subir_acks()
        self.subir_ventanas()
        self.mqtt.loop_stop()
        self.mqtt.disconnect()


def main():
    cfg = configparser.ConfigParser()
    archivo = CARPETA / "config_pc.ini"
    if not cfg.read(archivo, encoding="utf-8"):
        raise SystemExit(f"Falta {archivo.name}: copiar config_pc_ejemplo.ini y completar la api_key")
    pc = PCLocal(cfg)
    signal.signal(signal.SIGINT, lambda *_: pc.fin.set())  # Ctrl+C cierra subiendo lo pendiente
    pc.correr()


if __name__ == "__main__":
    main()
