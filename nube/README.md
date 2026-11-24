# Nube (Nivel 3): API PHP + MySQL

Servidor de la cátedra, **grupo 06**: Apache + PHP + MySQL/MariaDB, con acceso solo por FTP/SFTP. La carpeta del FTP es la que se publica en <https://utecnologica.org/estudiantes/imel/g06/>.

## Puesta en marcha

1. En phpMyAdmin (<https://auth-db581.hstgr.io/>, usuario `u874918252_dbg06`), abrir la base `u874918252_dbg06` y ejecutar `schema.sql` en la pestaña SQL.
2. Copiar `config_ejemplo.php` como `config.php` y completar la contraseña de la base, una `API_KEY` larga y la `PANEL_CLAVE`.
3. Subir por FTP/SFTP (`ftp.utecnologica.org`, usuario `u874918252.imelG06`) a la raíz de la carpeta: `.htaccess`, `config.php`, `index.html` (el dashboard), `chart.umd.min.js` (Chart.js, para no depender de un CDN) y la carpeta `api/`. El hosting no deja crear una carpeta llamada `lib`, por eso Chart.js va en la raíz.
4. Probar desde el navegador: `https://utecnologica.org/estudiantes/imel/g06/api/estado.php` tiene que devolver un JSON con `"ok": true`.

## Dashboard (`index.html`)

Es la página principal del g06: <https://utecnologica.org/estudiantes/imel/g06/>. Tiene:

- **Encabezado:** datos del proyecto e integrantes.
- **Estado:** si el gateway está en línea, el último contacto de la PC local, la última confirmación de configuración y la última ventana recibida.
- **Gráficos:** las 4 variables y el punto de rocío con su banda ± incertidumbre. Debajo de cada variable, las muestras por ventana (n); ahí se ve el cambio de tasa al aplicar un comando.
- **Selector de rango:** 1 h, 6 h, 24 h, 7 días o fechas a elección, y descarga en CSV.
- **Configuración remota:** un período por sensor, protegido con `PANEL_CLAVE`, y la tabla de comandos con su demora hasta la confirmación.

En modo en vivo se actualiza solo: el estado cada 15 s y los datos cada 30 s. Después de aplicar un comando, consulta el estado cada 3 s durante 2 min.

## Endpoints

Todas las fechas viajan como milisegundos epoch UTC. En la base se guardan como `DATETIME(3)` en UTC.

| Endpoint | Método | Quién lo usa | Qué hace |
|---|---|---|---|
| `api/telemetria.php` | POST | PC local, cada 10 min | Sube un lote de ventanas. Reenviar el mismo lote no duplica filas. |
| `api/config_pendiente.php` | GET | PC local, cada 30 s | Devuelve los comandos sin confirmar y registra el polling como latido del Nivel 2. |
| `api/ack.php` | POST | PC local, apenas llega el ack del ESP8266 | Marca el comando como aplicado o fallido. |
| `api/comando.php` | POST | Dashboard ("Aplicar") | Encola un período nuevo. Valida los mínimos del firmware y deja sin efecto los pendientes del mismo sensor. |
| `api/datos.php` | GET | Dashboard | Ventanas entre `desde` y `hasta`, cada valor como `[valor, incertidumbre]`, más el punto de rocío. Si hay más de `max` puntos, los agrupa con promedio ponderado por n. |
| `api/estado.php` | GET | Dashboard | Períodos vigentes, último polling, última subida, último ack y últimos 10 comandos. |

Los tres endpoints de la PC piden el encabezado `X-Api-Key: <API_KEY>`. Si el hosting filtrara ese encabezado, también se acepta `?api_key=`. `comando.php` pide `"clave": <PANEL_CLAVE>` en el cuerpo, salvo que se deje vacía.

### Formatos

```text
POST telemetria.php  {"gateway": "online", "ventanas": [{"ts": 1763659800000, "seq": 12, "err": 0,
                       "v": [25.12, 60.0, 10.3, 50.2], "n": [10, 5, 40, 20], "p": [1000, 2000, 250, 500]}]}
GET  config_pendiente.php?gateway=online   ->  {"ok": true, "comandos": [{"cmd": 17, "sensor": 0, "periodo_ms": 5000}]}
POST ack.php         {"cmd": 17, "ok": true, "periodos": [5000, 2000, 250, 500], "ts": 1763659812345}
POST comando.php     {"sensor": 0, "periodo_ms": 5000, "clave": "..."}
GET  datos.php?desde=<ms>&hasta=<ms>&max=2000
```

Sensores: 0 temperatura (DS18B20, °C), 1 humedad (DHT11, %HR), 2 LDR (kΩ), 3 distancia (HC-SR04, cm). Un valor `null` indica que el sensor no tuvo muestras en la ventana.

## Incertidumbres (`api/_metrologia.php`)

Son las mismas fórmulas de la sección 4 del avance, calculadas para cada ventana:

- DS18B20: ±0,5 °C (±2 °C fuera de −10 a 85 °C), combinado en cuadratura con la mitad de la resolución.
- DHT11: ±5 %HR, combinado con la mitad de la resolución.
- LDR: propagación de δN = 0,5 cuentas y de la incertidumbre de Rf. Rf y su incertidumbre se cargan en `config.php` después de medir con el multímetro. El paso a lux solo se calcula si se carga la calibración (`LDR_R10_KOHM`, `LDR_GAMMA`).
- HC-SR04: δd con la temperatura de la misma ventana. Si la ventana no trae DS18B20, usa 20 ± 5 °C.
- Punto de rocío: Magnus con propagación en cuadratura.

## Cadena de comandos (MQTT del lado local)

| Tópico | Sentido | Contenido |
|---|---|---|
| `utec/lab3/telemetria` | ESP → PC | `{"seq", "err", "rssi", "v": [4], "n": [4], "p": [4]}` cada 10 s. La PC le agrega `ts` al recibirlo. |
| `utec/lab3/config` | PC → ESP | `{"cmd": 17, "sensor": 0, "periodo_ms": 5000}`, con QoS 1. |
| `utec/lab3/config/ack` | ESP → PC | `{"cmd": 17, "ok": true, "sensor": 0, "periodo_ms": 5000, "periodos": [4]}` |
| `utec/lab3/gateway/estado` | ESP → PC | `online` / `offline`, retenido y con last will. |

La PC tiene que reenviar un comando si no recibe su ack en unos 60 s. El ESP descarta los duplicados que ya tiene en cola y, si vuelve a aplicar un período igual al vigente, no pasa nada.
