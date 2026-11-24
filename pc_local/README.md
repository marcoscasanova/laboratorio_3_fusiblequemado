# PC local (Nivel 2)

`pc_local.py` es el puente entre el ESP8266 y la nube:

- **Telemetría:** recibe por MQTT cada ventana del gateway (cada 10 s), le pone la hora de llegada y la guarda en SQLite (`datos_locales.db`).
- **Subida:** cada 10 min sube a la nube las ventanas que no se subieron. Si no hay internet, quedan guardadas y se suben en la próxima vuelta.
- **Comandos:** cada 30 s consulta a la nube si hay comandos nuevos y los publica al ESP8266. Si no llega la confirmación en 60 s, lo reenvía.
- **Confirmaciones:** sube cada confirmación (ack) del ESP8266 apenas llega, para que el dashboard la muestre enseguida.

## Instalación en Windows

1. **Python 3.9 o superior.** Instalar las dependencias:
   ```
   pip install -r requirements.txt
   ```
2. **Mosquitto.** Instalarlo desde <https://mosquitto.org/download/> (instalador de Windows de 64 bits). El instalador lo deja corriendo como servicio, pero ese servicio solo acepta conexiones desde la propia PC. Alcanza para probar con el simulador, no para el ESP8266.
   Para el ESP8266, abrir PowerShell **como administrador** en esta carpeta y correr:
   ```powershell
   net stop mosquitto
   & "C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto.conf -v
   ```
   Dejar esa consola abierta. `mosquitto.conf` hace que el broker acepte conexiones desde la red WiFi. La primera vez, cuando Windows pregunte, hay que permitir el acceso en el firewall para redes **privadas**. Para volver al servicio: `net start mosquitto`.
3. **IP de la PC.** Buscarla con `ipconfig` (la "Dirección IPv4" de la placa WiFi) y ponerla en `MQTT_HOST` del `secretos.h` del gateway.
4. **Configuración.** Copiar `config_pc_ejemplo.ini` como `config_pc.ini` y poner la misma `api_key` que tiene `nube/config.php`.
5. **Arrancar el puente** en otra consola:
   ```
   python pc_local.py
   ```
   Se cierra con Ctrl+C; antes de terminar sube lo que quedó pendiente.

## Probar sin las placas

`simulador_gateway.py` se comporta igual que el ESP8266 por MQTT:

- Publica ventanas con datos inventados. El n de cada sensor sale del período vigente, así que se ve el cambio de tasa cuando se aplica un comando.
- Responde los comandos con su confirmación.

Para probar, con Mosquitto y `pc_local.py` corriendo, abrir una tercera consola:
```
python simulador_gateway.py
```
Cuando se cambie un período desde el dashboard (o con `comando.php`), en menos de 30 s tiene que aparecer en la consola de `pc_local.py` el envío del comando y su confirmación.

Antes de conectar las placas reales, cerrar el simulador. Si los datos de prueba llegaron a la nube, se borran desde phpMyAdmin: `TRUNCATE ventanas; TRUNCATE comandos; TRUNCATE estado;`
