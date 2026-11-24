# Laboratorio Final Integrador - FusibleQuemado

Sistema de instrumentación multivariable IoT/IIoT con arquitectura multinivel y configuración remota.
Instrumentación y Medidas Eléctricas, Ingeniería en Mecatrónica, UTEC Fray Bentos, 2026.

Integrantes: Marcos Casanova, Adriel Castillo, Tiago Corrales.

## Arquitectura

Arduino Mega (esclavo I2C) → ESP8266 (maestro I2C, cliente MQTT) → PC local (Mosquitto + SQLite) → servidor en la nube del grupo 06 (API PHP + MySQL + dashboard).
Los comandos de configuración hacen el camino inverso.

## Estructura

| Carpeta | Contenido |
|---|---|
| `arduino/nodo_edge` | Firmware del Arduino Mega: lectura asíncrona de los 4 sensores con `millis()` y esclavo I2C |
| `esp8266/gateway` | Firmware del ESP8266: maestro I2C y cliente MQTT |
| `pc_local` | Puente MQTT ↔ nube (`pc_local.py`), configuración de Mosquitto y simulador del gateway para probar sin placas (ver `pc_local/README.md`) |
| `nube` | API en PHP, esquema MySQL y dashboard del servidor de la cátedra (ver `nube/README.md`) |
| `docs` | Esquema eléctrico y documentación |

## Sensores

| Variable | Sensor | Pin |
|---|---|---|
| Temperatura | DS18B20 | D2 |
| Humedad relativa | DHT11 | D3 |
| Luminosidad | LDR GL5528 (divisor con 10 kΩ) | A0 |
| Distancia | HC-SR04 | Trig D4, Echo D5 |

## Protocolo I2C (Mega en 0x08, bus directo a 100 kHz, sin adaptador de nivel)

| Comando | Sentido | Trama |
|---|---|---|
| `0x01` SET_PERIOD | ESP → Mega | id de sensor (1 byte) + período en ms (uint32 little endian) |
| `0x02` GET_DATA | Mega → ESP | 4 promedios float (16) + 4 n uint16 (8) + secuencia (1) + checksum (1) = 26 bytes |
| `0x03` GET_CONFIG | Mega → ESP | 4 períodos uint32 (16) + checksum (1) = 17 bytes |

El checksum es la suma de todos los bytes anteriores, módulo 256. Si un sensor no tuvo muestras en la ventana, su promedio viaja como NaN.

## Gateway ESP8266

1. Instalar el soporte de placas ESP8266 y la librería PubSubClient (Nick O'Leary).
2. Copiar `esp8266/gateway/secretos_ejemplo.h` como `secretos.h` y completar la red WiFi y la IP de la PC con Mosquitto.
3. Placa "NodeMCU 1.0 (ESP-12E Module)". El monitor serie va a 115200 baudios.

Cada 10 s el gateway le pide al Mega los promedios y la configuración vigente, y los publica en `utec/lab3/telemetria`. Los comandos que llegan por `utec/lab3/config` los escribe por I2C. A los 150 ms relee la configuración y publica la confirmación en `utec/lab3/config/ack`. Los tópicos y formatos están en `nube/README.md`.

## Probar el nodo Edge solo

1. Instalar las librerías OneWire, DallasTemperature y DHT sensor library (Adafruit) desde el gestor de librerías del Arduino IDE.
2. Cargar `arduino/nodo_edge/nodo_edge.ino` en el Mega.
3. Abrir el monitor serie a 115200 baudios, con fin de línea "Nueva línea".
4. Cambiar el período de un sensor con `P <id> <ms>`, por ejemplo `P 0 5000` (id: 0 temperatura, 1 humedad, 2 LDR, 3 distancia).
