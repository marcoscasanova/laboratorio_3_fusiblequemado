# Laboratorio Final Integrador - FusibleQuemado

Sistema de instrumentación multivariable IoT/IIoT con arquitectura multinivel y configuración remota.
Instrumentación y Medidas Eléctricas, Ingeniería en Mecatrónica, UTEC Fray Bentos, 2026.

Integrantes: Marcos Casanova, Adriel Castillo, Tiago Corrales.

## Arquitectura

Arduino Mega (esclavo I2C) → ESP8266 (maestro I2C, cliente MQTT) → PC local (Mosquitto + SQLite) → servidor en la nube (API + base histórica + dashboard).
Los comandos de configuración hacen el camino inverso.

## Estructura

| Carpeta | Contenido |
|---|---|
| `arduino/nodo_edge` | Firmware del Arduino Mega: lectura asíncrona de los 4 sensores con `millis()` y esclavo I2C |
| `esp8266/gateway` | Firmware del ESP8266: maestro I2C y cliente MQTT |
| `pc_local` | Scripts de la PC local: consolidación, base local, envío a la nube y polling de configuración |
| `nube` | API, base histórica y dashboard del servidor en la nube |
| `docs` | Esquema eléctrico y documentación |

## Sensores

| Variable | Sensor | Pin |
|---|---|---|
| Temperatura | DS18B20 | D2 |
| Humedad relativa | DHT11 | D3 |
| Luminosidad | LDR GL5528 (divisor con 10 kΩ) | A0 |
| Distancia | HC-SR04 | Trig D4, Echo D5 |

## Probar el nodo Edge solo

1. Instalar las librerías OneWire, DallasTemperature y DHT sensor library (Adafruit) desde el gestor de librerías del Arduino IDE.
2. Cargar `arduino/nodo_edge/nodo_edge.ino` en el Mega.
3. Abrir el monitor serie a 115200 baudios, con fin de línea "Nueva línea".
4. Cambiar el período de un sensor con `P <id> <ms>`, por ejemplo `P 0 5000` (id: 0 temperatura, 1 humedad, 2 LDR, 3 distancia).
