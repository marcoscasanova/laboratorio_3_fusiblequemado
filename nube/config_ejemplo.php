<?php
// Copiar como config.php (no se sube al repositorio) y completar.

// Base de datos (phpMyAdmin: https://auth-db581.hstgr.io/)
const DB_HOST = 'localhost'; // desde el propio hosting la base suele verse como localhost; si no conecta, probar 'auth-db581.hstgr.io'
const DB_NOMBRE = 'u874918252_dbg06';
const DB_USUARIO = 'u874918252_dbg06';
const DB_CLAVE = 'la_misma_del_ftp';

// Clave que manda la PC local en el encabezado X-Api-Key (inventar una larga, por ejemplo con: python -c "import secrets; print(secrets.token_hex(16))")
const API_KEY = 'cambiar_por_una_clave_larga';

// Clave para mandar comandos desde el dashboard. Vacia = cualquiera que entre a la pagina puede cambiar los periodos.
const PANEL_CLAVE = 'cambiar';

// Divisor de la LDR: valor de Rf medido con el multimetro y su incertidumbre (exactitud del multimetro)
const RF_KOHM = 10.0;
const RF_INC_KOHM = 0.5; // 5 % mientras no se mida; con el multimetro baja a ~0,1 kOhm

// Calibracion de la LDR contra el luxometro: E = 10 * (R / R10)^(-1 / GAMMA). Dejar R10 en 0 hasta calibrar (no se calcula lux).
const LDR_R10_KOHM = 0.0;
const LDR_GAMMA = 0.7;
