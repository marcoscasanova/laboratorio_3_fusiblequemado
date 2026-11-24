#include <ESP8266WiFi.h> // WiFi del NodeMCU
#include <PubSubClient.h> // cliente MQTT
#include <Wire.h> // bus I2C con el Arduino Mega
#include "secretos.h" // WIFI_SSID, WIFI_PASS, MQTT_HOST y MQTT_PORT (copiar secretos_ejemplo.h)

const uint8_t PIN_SDA = 4; // D2 (GPIO4)
const uint8_t PIN_SCL = 5; // D1 (GPIO5)
const uint8_t DIR_ARDUINO = 0x08; // direccion del Mega como esclavo

const uint8_t CMD_SET_PERIOD = 0x01; // escribe un periodo nuevo
const uint8_t CMD_GET_DATA = 0x02; // pide los promedios
const uint8_t CMD_GET_CONFIG = 0x03; // pide los periodos vigentes
const uint8_t LARGO_DATOS = 26; // 4 float + 4 uint16 + secuencia + checksum
const uint8_t LARGO_CONFIG = 17; // 4 uint32 + checksum
const uint8_t N_SENS = 4; // 0 temperatura, 1 humedad, 2 LDR, 3 distancia

const uint32_t T_VENTANA = 10000; // cada cuanto se piden los promedios al Mega, en ms
const uint32_t T_ESPERA_ACK = 150; // espera antes de releer la config (el Mega la aplica en su loop)
const uint32_t T_RECONEXION = 5000; // espera entre intentos de conexion al broker
const uint8_t REINTENTOS = 3; // intentos de escritura de un periodo antes de informar error

const char *TOPICO_DATOS = "utec/lab3/telemetria"; // promedios hacia la PC
const char *TOPICO_CONFIG = "utec/lab3/config"; // comandos desde la PC
const char *TOPICO_ACK = "utec/lab3/config/ack"; // confirmaciones hacia la PC
const char *TOPICO_ESTADO = "utec/lab3/gateway/estado"; // online / offline (retenido, con last will)

WiFiClient red; // conexion TCP
PubSubClient mqtt(red); // cliente MQTT sobre esa conexion

struct Comando { // un cambio de periodo pendiente
  long cmd; // id del comando en la nube
  uint8_t sensor; // sensor a modificar
  uint32_t periodo; // periodo pedido en ms
};
const uint8_t COLA_MAX = 8; // comandos que pueden esperar a la vez
Comando cola[COLA_MAX]; // cola circular de comandos
uint8_t cola_ini = 0, cola_largo = 0; // primer elemento y cantidad

enum EstadoCfg {CFG_LIBRE, CFG_ESPERANDO}; // estados de la aplicacion de un comando
EstadoCfg estado_cfg = CFG_LIBRE; // arranca sin comandos en curso
uint32_t t_escrito = 0; // momento en que se escribio el periodo
uint8_t intentos = 0; // intentos hechos con el comando actual

uint32_t t_ventana = 0; // ultimo pedido de datos
uint32_t t_reconexion = 0; // ultimo intento de conexion al broker
uint32_t errores_i2c = 0; // tramas fallidas desde el arranque
char id_cliente[32]; // id MQTT unico del gateway

/* Conecta al WiFi y prepara el bus I2C y el cliente MQTT. */
void setup() {
  Serial.begin(115200); // monitor serie para depurar
  Wire.begin(PIN_SDA, PIN_SCL); // maestro I2C en D2 y D1
  Wire.setClock(100000); // 100 kHz
  Wire.setClockStretchLimit(20000); // tolera hasta 20 ms de clock stretching (el DHT11 bloquea las interrupciones del Mega)
  WiFi.mode(WIFI_STA); // solo cliente, sin punto de acceso
  WiFi.setAutoReconnect(true); // reconecta solo si se cae
  WiFi.begin(WIFI_SSID, WIFI_PASS); // arranca la conexion sin esperarla
  mqtt.setServer(MQTT_HOST, MQTT_PORT); // broker Mosquitto de la PC local
  mqtt.setCallback(alMensaje); // llegada de comandos
  mqtt.setBufferSize(512); // la telemetria pasa los 256 bytes por defecto
  snprintf(id_cliente, sizeof(id_cliente), "fusiblequemado-gw-%06x", ESP.getChipId()); // id unico
  Serial.println(F("\nGateway listo, conectando al WiFi...")); // aviso de arranque
}

/* Bucle principal sin bloqueos: mantiene la conexion, aplica comandos y publica datos. */
void loop() {
  uint32_t ahora = millis(); // tiempo actual
  mantenerConexion(ahora); // WiFi y broker
  mqtt.loop(); // procesa los mensajes entrantes

  if (estado_cfg == CFG_LIBRE && cola_largo > 0) { // hay un comando esperando
    Comando &c = cola[cola_ini]; // el mas antiguo
    if (escribirPeriodo(c.sensor, c.periodo)) { // lo manda al Mega
      t_escrito = ahora; // marca la escritura
      estado_cfg = CFG_ESPERANDO; // espera a que el Mega lo aplique
    } else if (++intentos >= REINTENTOS) { // el bus fallo varias veces
      publicarAck(c, false, nullptr); // informa el error
      sacarDeCola(); // descarta el comando
    }
  }

  if (estado_cfg == CFG_ESPERANDO && ahora - t_escrito >= T_ESPERA_ACK) { // ya paso la espera
    Comando &c = cola[cola_ini]; // comando en curso
    uint32_t periodos[N_SENS]; // config vigente del Mega
    bool leida = leerConfig(periodos); // la relee como confirmacion
    if (leida && periodos[c.sensor] == c.periodo) { // el Mega aplico el valor pedido
      publicarAck(c, true, periodos); // confirma
      sacarDeCola(); // siguiente comando
    } else if (++intentos >= REINTENTOS) { // no se pudo confirmar
      publicarAck(c, false, leida ? periodos : nullptr); // informa el error y el valor vigente
      sacarDeCola(); // descarta el comando
    } else {
      estado_cfg = CFG_LIBRE; // vuelve a escribirlo
    }
  }

  if (ahora - t_ventana >= T_VENTANA) { // cierra una ventana de promedios
    t_ventana = ahora; // marca el pedido
    publicarDatos(); // pide los promedios y los publica
  }
}

/* Reconecta al broker sin bloquear el loop si se perdio la conexion. */
void mantenerConexion(uint32_t ahora) {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return; // sin WiFi no hay nada que hacer, o ya esta conectado
  if (ahora - t_reconexion < T_RECONEXION && t_reconexion != 0) return; // espera entre intentos
  t_reconexion = ahora; // marca el intento
  Serial.print(F("Conectando al broker ")); // aviso
  Serial.println(MQTT_HOST); // direccion del broker
  if (mqtt.connect(id_cliente, TOPICO_ESTADO, 1, true, "offline")) { // last will: offline retenido
    mqtt.publish(TOPICO_ESTADO, "online", true); // avisa que esta conectado
    mqtt.subscribe(TOPICO_CONFIG, 1); // comandos con QoS 1
    Serial.println(F("Conectado")); // aviso
  } else {
    Serial.print(F("Fallo, estado ")); // aviso
    Serial.println(mqtt.state()); // codigo de error de PubSubClient
  }
}

/* Callback MQTT: valida el comando y lo encola; el I2C se hace en el loop. */
void alMensaje(char *topico, byte *datos, unsigned int largo) {
  (void)topico; // solo esta suscripto a un topico
  char txt[160]; // copia del mensaje como texto
  if (largo >= sizeof(txt)) return; // mensaje demasiado largo
  memcpy(txt, datos, largo); // copia el contenido
  txt[largo] = '\0'; // cierra el string
  Serial.print(F("Comando: ")); // aviso
  Serial.println(txt); // mensaje recibido
  long cmd, sensor, periodo; // campos esperados
  if (!campo(txt, "cmd", cmd) || !campo(txt, "sensor", sensor) || !campo(txt, "periodo_ms", periodo)) return; // formato invalido
  if (sensor < 0 || sensor >= N_SENS || periodo <= 0) return; // valores invalidos
  for (uint8_t k = 0; k < cola_largo; k++) // un comando repetido (reenvio de la PC) no se encola dos veces
    if (cola[(cola_ini + k) % COLA_MAX].cmd == cmd) return;
  if (cola_largo == COLA_MAX) return; // cola llena, la PC lo reenvia mas tarde
  cola[(cola_ini + cola_largo) % COLA_MAX] = {cmd, (uint8_t)sensor, (uint32_t)periodo}; // encola
  cola_largo++; // un comando mas
}

/* Busca "clave": <entero> en un JSON plano. Devuelve false si no esta. */
bool campo(const char *json, const char *clave, long &valor) {
  char patron[24]; // "clave" entre comillas
  snprintf(patron, sizeof(patron), "\"%s\"", clave); // arma el patron
  const char *p = strstr(json, patron); // busca la clave
  if (!p) return false; // no esta
  p = strchr(p + strlen(patron), ':'); // dos puntos despues de la clave
  if (!p) return false; // formato invalido
  char *fin; // donde termina el numero
  valor = strtol(p + 1, &fin, 10); // convierte el numero
  return fin != p + 1; // hubo al menos un digito
}

/* Quita el comando atendido de la cola y deja libre la maquina de estados. */
void sacarDeCola() {
  cola_ini = (cola_ini + 1) % COLA_MAX; // avanza el inicio
  cola_largo--; // un comando menos
  intentos = 0; // reinicia los intentos
  estado_cfg = CFG_LIBRE; // listo para el siguiente
}

/* Escribe por I2C el comando SET_PERIOD: id del sensor + periodo uint32 little endian. */
bool escribirPeriodo(uint8_t sensor, uint32_t periodo) {
  Wire.beginTransmission(DIR_ARDUINO); // inicia la trama
  Wire.write(CMD_SET_PERIOD); // comando
  Wire.write(sensor); // sensor
  for (uint8_t k = 0; k < 4; k++) Wire.write((uint8_t)(periodo >> (8 * k))); // periodo, byte bajo primero
  bool ok = Wire.endTransmission() == 0; // 0 = el Mega respondio ACK
  if (!ok) errores_i2c++; // cuenta el error
  return ok;
}

/* Escribe un comando de lectura y lee la respuesta verificando el largo y el checksum. */
bool pedir(uint8_t comando, uint8_t *buf, uint8_t largo) {
  Wire.beginTransmission(DIR_ARDUINO); // inicia la trama
  Wire.write(comando); // que se quiere leer
  if (Wire.endTransmission() != 0) { errores_i2c++; return false; } // el Mega no respondio
  if (Wire.requestFrom(DIR_ARDUINO, largo) != largo) { errores_i2c++; return false; } // trama incompleta
  uint8_t chk = 0; // checksum calculado
  for (uint8_t k = 0; k < largo; k++) { // lee todos los bytes
    buf[k] = Wire.read(); // guarda el byte
    if (k < largo - 1) chk += buf[k]; // suma todos menos el ultimo
  }
  if (chk != buf[largo - 1]) { errores_i2c++; return false; } // checksum incorrecto
  return true;
}

/* Lee los 4 periodos vigentes del Mega. */
bool leerConfig(uint32_t *periodos) {
  uint8_t buf[LARGO_CONFIG]; // trama de config
  if (!pedir(CMD_GET_CONFIG, buf, LARGO_CONFIG)) return false; // fallo el bus
  memcpy(periodos, buf, 4 * N_SENS); // 4 uint32 little endian (igual que el ESP8266)
  return true;
}

/* Pide los promedios de la ventana, lee la config vigente y publica todo en un JSON. */
void publicarDatos() {
  uint8_t buf[LARGO_DATOS]; // trama de datos
  bool ok = pedir(CMD_GET_DATA, buf, LARGO_DATOS); // pide los promedios (el Mega reinicia sus acumuladores)
  uint32_t periodos[N_SENS]; // periodos con los que se tomo la ventana
  bool ok_cfg = leerConfig(periodos); // config vigente
  if (!ok) { // ventana perdida
    Serial.println(F("Error leyendo los datos del Mega")); // aviso
    return;
  }
  char json[400]; // mensaje a publicar
  int p = snprintf(json, sizeof(json), "{\"seq\":%u,\"err\":%lu,\"rssi\":%d,\"v\":[", buf[24], (unsigned long)errores_i2c, WiFi.RSSI()); // encabezado
  for (uint8_t i = 0; i < N_SENS; i++) { // promedios
    float v; // promedio del sensor
    memcpy(&v, &buf[4 * i], 4); // float little endian
    if (isnan(v)) p += snprintf(json + p, sizeof(json) - p, "%snull", i ? "," : ""); // sin muestras en la ventana
    else p += snprintf(json + p, sizeof(json) - p, "%s%.4f", i ? "," : "", v); // valor
  }
  p += snprintf(json + p, sizeof(json) - p, "],\"n\":["); // cantidad de muestras
  for (uint8_t i = 0; i < N_SENS; i++) p += snprintf(json + p, sizeof(json) - p, "%s%u", i ? "," : "", buf[16 + 2 * i] | (buf[17 + 2 * i] << 8)); // uint16 little endian
  p += snprintf(json + p, sizeof(json) - p, "],\"p\":"); // periodos
  if (ok_cfg) p += snprintf(json + p, sizeof(json) - p, "[%lu,%lu,%lu,%lu]}", (unsigned long)periodos[0], (unsigned long)periodos[1], (unsigned long)periodos[2], (unsigned long)periodos[3]); // periodos vigentes
  else p += snprintf(json + p, sizeof(json) - p, "null}"); // no se pudieron leer
  if (mqtt.connected()) mqtt.publish(TOPICO_DATOS, json); // publica
  Serial.println(json); // copia por serie
}

/* Publica la confirmacion (o el error) de un comando, con los periodos vigentes si se pudieron leer. */
void publicarAck(const Comando &c, bool ok, const uint32_t *periodos) {
  char json[200]; // mensaje a publicar
  int p = snprintf(json, sizeof(json), "{\"cmd\":%ld,\"ok\":%s,\"sensor\":%u,\"periodo_ms\":%lu,\"periodos\":", c.cmd, ok ? "true" : "false", c.sensor, (unsigned long)c.periodo); // encabezado
  if (periodos) snprintf(json + p, sizeof(json) - p, "[%lu,%lu,%lu,%lu]}", (unsigned long)periodos[0], (unsigned long)periodos[1], (unsigned long)periodos[2], (unsigned long)periodos[3]); // config vigente
  else snprintf(json + p, sizeof(json) - p, "null}"); // no se pudo leer
  if (mqtt.connected()) mqtt.publish(TOPICO_ACK, json); // publica
  Serial.println(json); // copia por serie
}
