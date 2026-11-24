-- Base de datos de la nube (MySQL / MariaDB del servidor de la catedra, base u874918252_dbg06)
-- Ejecutar una vez desde phpMyAdmin (pestaña SQL). Todas las fechas se guardan en UTC.

-- Una fila por ventana de promedios que manda el ESP8266 (cada 10 s), con el n y el periodo de cada sensor
CREATE TABLE IF NOT EXISTS ventanas (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  ts DATETIME(3) NOT NULL COMMENT 'cierre de la ventana, UTC (hora de la PC local al recibir el MQTT)',
  seq TINYINT UNSIGNED NOT NULL COMMENT 'contador de tramas del Arduino',
  temp DOUBLE NULL COMMENT 'DS18B20 [C], NULL si no hubo muestras',
  n_temp SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  p_temp INT UNSIGNED NULL COMMENT 'periodo vigente [ms]',
  hum DOUBLE NULL COMMENT 'DHT11 [%HR]',
  n_hum SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  p_hum INT UNSIGNED NULL,
  ldr DOUBLE NULL COMMENT 'resistencia de la LDR [kOhm]',
  n_ldr SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  p_ldr INT UNSIGNED NULL,
  dist DOUBLE NULL COMMENT 'HC-SR04 compensado en temperatura [cm]',
  n_dist SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  p_dist INT UNSIGNED NULL,
  err_i2c INT UNSIGNED NULL COMMENT 'errores I2C acumulados en el gateway',
  recibido DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'llegada a la nube',
  UNIQUE KEY uk_ts_seq (ts, seq),
  KEY k_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Cola de comandos de configuracion que se cargan desde el dashboard
CREATE TABLE IF NOT EXISTS comandos (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  sensor TINYINT UNSIGNED NOT NULL COMMENT '0 temperatura, 1 humedad, 2 LDR, 3 distancia',
  periodo_ms INT UNSIGNED NOT NULL,
  creado DATETIME(3) NOT NULL COMMENT 'cuando se presiono Aplicar',
  entregado DATETIME(3) NULL COMMENT 'primera vez que la PC local lo bajo',
  confirmado DATETIME(3) NULL COMMENT 'cuando el ESP8266 confirmo (hora de la PC)',
  ok TINYINT(1) NULL COMMENT '1 aplicado, 0 fallo',
  periodos_ack VARCHAR(64) NULL COMMENT 'periodos que leyo el ESP8266 del Arduino al confirmar',
  reemplazado TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'un comando posterior del mismo sensor lo dejo sin efecto',
  KEY k_pendientes (confirmado, reemplazado)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Datos sueltos de estado: ultimo polling de la PC, estado del gateway, etc.
CREATE TABLE IF NOT EXISTS estado (
  clave VARCHAR(32) PRIMARY KEY,
  valor VARCHAR(255) NULL,
  actualizado DATETIME(3) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
