"""Simulador del gateway ESP8266 + Arduino, para probar la PC local, la nube y el dashboard sin las placas.

Habla el mismo MQTT que gateway.ino:
- cada 10 s publica en utec/lab3/telemetria los promedios de la ventana; el n de cada sensor sale del periodo vigente
- escucha utec/lab3/config, aplica el periodo (respetando los minimos del firmware) y responde en utec/lab3/config/ack

Uso:  python simulador_gateway.py [--host localhost] [--ventana 10]
"""

import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

PERIODO_MIN = [750, 2000, 10, 60]
PERIODO_MAX = 3600000

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="localhost")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--ventana", type=float, default=10, help="segundos entre publicaciones (10 en el ESP8266)")
args = parser.parse_args()

periodos = [1000, 2000, 250, 500]  # mismos valores iniciales que nodo_edge.ino
resto = [0.0] * 4  # fraccion de muestra que pasa a la ventana siguiente
seq = 0


def al_mensaje(cliente, userdata, msg):
    try:
        c = json.loads(msg.payload)
        cmd, sensor, periodo = int(c["cmd"]), int(c["sensor"]), int(c["periodo_ms"])
    except (ValueError, KeyError, TypeError):
        return
    if not 0 <= sensor < 4:
        return
    periodos[sensor] = max(PERIODO_MIN[sensor], min(PERIODO_MAX, periodo))
    resto[sensor] = 0.0
    time.sleep(0.15)  # espera del ESP8266 antes de releer la config
    ack = {"cmd": cmd, "ok": periodos[sensor] == periodo, "sensor": sensor, "periodo_ms": periodo, "periodos": periodos}
    cliente.publish("utec/lab3/config/ack", json.dumps(ack))
    print("ACK", ack, flush=True)


cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="simulador-gateway")
cliente.on_message = al_mensaje
cliente.on_connect = lambda c, u, f, rc, p: (c.subscribe("utec/lab3/config", 1), c.publish("utec/lab3/gateway/estado", "online", retain=True))
cliente.will_set("utec/lab3/gateway/estado", "offline", qos=1, retain=True)
cliente.connect(args.host, args.port)
cliente.loop_start()
print(f"Simulador conectado a {args.host}:{args.port}, publicando cada {args.ventana:.0f} s", flush=True)

try:
    while True:
        time.sleep(args.ventana)
        t = time.time()
        n = []
        for i in range(4):  # muestras que entran en la ventana con el periodo vigente
            x = args.ventana * 1000 / periodos[i] + resto[i]
            n.append(int(x))
            resto[i] = x - int(x)
        temp = 22 + 2 * math.sin(t / 600) + random.gauss(0, 0.05)
        hum = 55 + 5 * math.sin(t / 900) + random.gauss(0, 0.5)
        ldr = 8 + 4 * math.sin(t / 120) + random.gauss(0, 0.2)
        dist = 50 + 10 * math.sin(t / 60) + random.gauss(0, 0.2)
        v = [round(x, 4) if n[i] else None for i, x in enumerate([temp, hum, ldr, dist])]
        msg = {"seq": seq % 256, "err": 0, "rssi": -55, "v": v, "n": n, "p": periodos}
        cliente.publish("utec/lab3/telemetria", json.dumps(msg))
        print("DATOS", msg, flush=True)
        seq += 1
except KeyboardInterrupt:
    cliente.publish("utec/lab3/gateway/estado", "offline", retain=True).wait_for_publish(2)
    cliente.loop_stop()
