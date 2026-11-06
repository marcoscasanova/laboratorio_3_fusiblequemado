#include <Wire.h> // bus I2C con el ESP8266
#include <OneWire.h> // protocolo 1-Wire del DS18B20
#include <DallasTemperature.h> // libreria del DS18B20
#include <DHT.h> // libreria del DHT11 (Adafruit)

const uint8_t DIR_I2C = 0x08; // direccion del Arduino como esclavo
const uint8_t PIN_DS = 2; // dato del DS18B20
const uint8_t PIN_DHT = 3; // dato del DHT11
const uint8_t PIN_TRIG = 4; // Trig del HC-SR04
const uint8_t PIN_ECHO = 5; // Echo del HC-SR04
const uint8_t PIN_LDR = A0; // divisor de la LDR

const uint8_t CMD_SET_PERIOD = 0x01; // ESP escribe un periodo nuevo
const uint8_t CMD_GET_DATA = 0x02; // ESP pide los promedios
const uint8_t CMD_GET_CONFIG = 0x03; // ESP pide los periodos vigentes

const uint8_t N_SENS = 4; // 0 temp, 1 humedad, 2 LDR, 3 distancia
const uint32_t PERIODO_MIN[N_SENS] = {750, 2000, 10, 60}; // minimo de cada sensor en ms
const float RF = 10.0; // resistencia fija del divisor en kOhm (medirla con el tester)
const unsigned long TIMEOUT_ECO = 25000UL; // espera maxima del eco en us (unos 4 m)
const bool DEBUG = true; // imprime cada lectura por el monitor serie

OneWire bus_ds(PIN_DS); // bus 1-Wire en D2
DallasTemperature ds(&bus_ds); // objeto del DS18B20
DHT dht(PIN_DHT, DHT11); // objeto del DHT11

uint32_t periodo[N_SENS] = {1000, 2000, 250, 500}; // periodos iniciales en ms
uint32_t ultimo[N_SENS] = {0, 0, 0, 0}; // ultima lectura de cada sensor
float suma[N_SENS] = {0, 0, 0, 0}; // acumulador para el promedio
uint8_t cuenta[N_SENS] = {0, 0, 0, 0}; // cantidad de muestras acumuladas

enum EstadoDS {DS_ESPERANDO, DS_CONVIRTIENDO}; // estados de la lectura del DS18B20
EstadoDS estado_ds = DS_ESPERANDO; // arranca esperando su turno
uint32_t t_pedido = 0; // momento en que se pidio la conversion
float t_aire = 20.0; // ultima temperatura, se usa para la velocidad del sonido

volatile uint8_t comando = CMD_GET_DATA; // ultimo comando recibido por I2C
volatile bool nueva_config = false; // hay un periodo nuevo para aplicar
volatile uint8_t pend_id = 0; // sensor al que se le cambia el periodo
volatile uint32_t pend_periodo = 0; // periodo nuevo en ms
uint8_t secuencia = 0; // contador de tramas enviadas

char linea[24]; // buffer para comandos por el monitor serie
uint8_t largo = 0; // caracteres cargados en el buffer

/* Inicializa los sensores, el bus I2C como esclavo y el monitor serie. */
void setup() {
  Serial.begin(115200); // monitor serie para depurar
  Wire.begin(DIR_I2C); // se une al bus como esclavo 0x08
  Wire.onReceive(alRecibir); // callback cuando el ESP escribe
  Wire.onRequest(alPedir); // callback cuando el ESP lee
  ds.begin(); // inicia el DS18B20
  ds.setResolution(12); // 12 bits, 0,0625 C
  ds.setWaitForConversion(false); // modo asincrono, no bloquea 750 ms
  dht.begin(); // inicia el DHT11
  pinMode(PIN_TRIG, OUTPUT); // Trig como salida
  pinMode(PIN_ECHO, INPUT); // Echo como entrada
  digitalWrite(PIN_TRIG, LOW); // Trig en reposo
  Serial.println(F("Nodo Edge listo. Comando de prueba: P <id> <ms>")); // aviso de arranque
}

/* Bucle principal sin delay(): cada sensor se lee cuando se cumple su periodo. */
void loop() {
  uint32_t ahora = millis(); // tiempo actual
  aplicarConfig(); // aplica un periodo nuevo si llego alguno
  leerSerie(); // revisa comandos de prueba por el monitor serie

  if (estado_ds == DS_ESPERANDO && ahora - ultimo[0] >= periodo[0]) { // le toca al DS18B20
    ds.requestTemperatures(); // pide la conversion y sigue de largo
    t_pedido = ahora; // guarda cuando se pidio
    estado_ds = DS_CONVIRTIENDO; // pasa a esperar el resultado
  }
  if (estado_ds == DS_CONVIRTIENDO && ahora - t_pedido >= 750) { // ya termino la conversion
    float t = ds.getTempCByIndex(0); // lee la temperatura
    if (t != DEVICE_DISCONNECTED_C) { // descarta lecturas fallidas
      t_aire = t; // actualiza la temperatura para el HC-SR04
      acumular(0, t); // suma al promedio
      mostrar("DS18B20 [C]", t); // imprime para depurar
    }
    ultimo[0] = ahora; // marca la lectura
    estado_ds = DS_ESPERANDO; // vuelve a esperar su turno
  }

  if (ahora - ultimo[1] >= periodo[1]) { // le toca al DHT11
    float h = dht.readHumidity(); // lee la humedad relativa
    if (!isnan(h)) { // descarta lecturas fallidas
      acumular(1, h); // suma al promedio
      mostrar("DHT11 [%HR]", h); // imprime para depurar
    }
    ultimo[1] = ahora; // marca la lectura
  }

  if (ahora - ultimo[2] >= periodo[2]) { // le toca a la LDR
    int n = analogRead(PIN_LDR); // lectura del ADC, 0 a 1023
    if (n > 0) { // evita dividir por cero con oscuridad total
      float r_ldr = RF * (1024.0 / n - 1.0); // resistencia de la LDR en kOhm
      acumular(2, r_ldr); // suma al promedio
      mostrar("LDR [kOhm]", r_ldr); // imprime para depurar
    }
    ultimo[2] = ahora; // marca la lectura
  }

  if (ahora - ultimo[3] >= periodo[3]) { // le toca al HC-SR04
    float d = medirDistancia(); // distancia en cm, negativa si no hubo eco
    if (d > 0) { // descarta mediciones sin eco
      acumular(3, d); // suma al promedio
      mostrar("HC-SR04 [cm]", d); // imprime para depurar
    }
    ultimo[3] = ahora; // marca la lectura
  }
}

/* Dispara el HC-SR04 y calcula la distancia compensando la velocidad del sonido con la temperatura. */
float medirDistancia() {
  digitalWrite(PIN_TRIG, LOW); // asegura el nivel bajo
  delayMicroseconds(2); // pausa corta antes del pulso
  digitalWrite(PIN_TRIG, HIGH); // inicio del pulso de disparo
  delayMicroseconds(10); // pulso de 10 us que pide la hoja de datos
  digitalWrite(PIN_TRIG, LOW); // fin del pulso
  unsigned long t = pulseIn(PIN_ECHO, HIGH, TIMEOUT_ECO); // duracion del eco en us, 0 si no volvio
  if (t == 0) return -1.0; // sin eco dentro del tiempo limite
  float v = 331.3 + 0.606 * t_aire; // velocidad del sonido en m/s
  return v * t * 1e-4 / 2.0; // ida y vuelta, pasado a cm
}

/* Suma una muestra al promedio del sensor, protegido de la interrupcion del I2C. */
void acumular(uint8_t i, float x) {
  noInterrupts(); // evita que el I2C lea a mitad de la suma
  if (cuenta[i] < 255) { // la cuenta viaja en un byte
    suma[i] += x; // acumula el valor
    cuenta[i]++; // una muestra mas
  }
  interrupts(); // vuelve a habilitar las interrupciones
}

/* Aplica en el loop el periodo que llego por I2C o por serie, respetando el minimo del sensor. */
void aplicarConfig() {
  if (!nueva_config) return; // no hay nada pendiente
  noInterrupts(); // copia los datos sin que cambien a mitad
  uint8_t id = pend_id; // sensor a modificar
  uint32_t p = pend_periodo; // periodo pedido
  nueva_config = false; // marca como atendido
  interrupts(); // vuelve a habilitar las interrupciones
  if (id >= N_SENS) return; // id invalido, se ignora
  if (p < PERIODO_MIN[id]) p = PERIODO_MIN[id]; // no baja del minimo del sensor
  periodo[id] = p; // aplica el periodo nuevo
  noInterrupts(); // reinicia el acumulador de ese sensor
  suma[id] = 0; // borra la suma
  cuenta[id] = 0; // borra la cuenta
  interrupts(); // vuelve a habilitar las interrupciones
  Serial.print(F("Periodo del sensor ")); // confirma por serie
  Serial.print(id); // sensor modificado
  Serial.print(F(" = ")); // separador
  Serial.print(p); // periodo aplicado
  Serial.println(F(" ms")); // unidad
}

/* Callback de I2C cuando el ESP escribe: solo copia datos y levanta banderas. */
void alRecibir(int n) {
  if (n < 1) return; // trama vacia
  comando = Wire.read(); // primer byte es el comando
  if (comando == CMD_SET_PERIOD && n >= 6) { // comando + id + 4 bytes de periodo
    pend_id = Wire.read(); // sensor a modificar
    uint32_t p = 0; // arma el periodo
    for (uint8_t k = 0; k < 4; k++) p |= (uint32_t)Wire.read() << (8 * k); // little endian
    pend_periodo = p; // guarda el periodo pedido
    nueva_config = true; // avisa al loop
  }
  while (Wire.available()) Wire.read(); // descarta bytes sobrantes
}

/* Callback de I2C cuando el ESP lee: responde segun el ultimo comando recibido. */
void alPedir() {
  uint8_t trama[22]; // buffer de salida (maximo 32 en Wire)
  uint8_t largo_tx = 0; // bytes cargados
  if (comando == CMD_GET_CONFIG) { // pide los periodos vigentes
    memcpy(trama, periodo, 16); // 4 periodos uint32
    largo_tx = 16; // largo sin checksum
  } else { // por defecto manda los datos
    for (uint8_t i = 0; i < N_SENS; i++) { // un promedio por sensor
      float prom = cuenta[i] ? suma[i] / cuenta[i] : NAN; // NAN si no hubo muestras
      memcpy(&trama[4 * i], &prom, 4); // copia el float
      trama[16 + i] = cuenta[i]; // cantidad de muestras usadas
      suma[i] = 0; // reinicia el acumulador
      cuenta[i] = 0; // reinicia la cuenta
    }
    trama[20] = secuencia++; // contador de tramas
    largo_tx = 21; // largo sin checksum
  }
  uint8_t chk = 0; // checksum simple
  for (uint8_t k = 0; k < largo_tx; k++) chk += trama[k]; // suma de todos los bytes
  trama[largo_tx++] = chk; // agrega el checksum al final
  Wire.write(trama, largo_tx); // envia la trama
}

/* Lee el monitor serie sin bloquear. Formato: P <id> <ms>, para probar sin el ESP8266. */
void leerSerie() {
  while (Serial.available()) { // procesa lo que haya llegado
    char c = Serial.read(); // lee un caracter
    if (c == '\n' || c == '\r') { // fin de linea
      linea[largo] = '\0'; // cierra el string
      int id; // sensor
      long ms; // periodo
      if (largo > 0 && sscanf(linea, "P %d %ld", &id, &ms) == 2) { // formato valido
        pend_id = id; // sensor a modificar
        pend_periodo = ms; // periodo pedido
        nueva_config = true; // lo aplica el loop
      }
      largo = 0; // vacia el buffer
    } else if (largo < sizeof(linea) - 1) { // hay lugar en el buffer
      linea[largo++] = c; // guarda el caracter
    }
  }
}

/* Imprime una lectura con su marca de tiempo, para ver el muestreo asincrono. */
void mostrar(const char *nombre, float x) {
  if (!DEBUG) return; // depuracion apagada
  Serial.print(millis()); // marca de tiempo
  Serial.print(F(" ms  ")); // separador
  Serial.print(nombre); // sensor
  Serial.print(F(": ")); // separador
  Serial.println(x, 2); // valor con 2 decimales
}
