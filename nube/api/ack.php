<?php
// POST desde la PC local apenas el ESP8266 confirma un comando.
// Cuerpo: {"cmd": 17, "ok": true, "periodos": [5000, 2000, 250, 500], "ts": <ms UTC en que llego el ack a la PC>}

require __DIR__ . '/_comun.php';

exigir_metodo('POST');
exigir_api_key();
$a = cuerpo_json();

if (!isset($a['cmd']) || !is_numeric($a['cmd']) || !isset($a['ok']) || !is_bool($a['ok'])) {
    fallar('faltan "cmd" (numero) u "ok" (true/false)');
}
$periodos = null;
if (isset($a['periodos']) && is_array($a['periodos']) && count($a['periodos']) === N_SENS) {
    $periodos = implode(',', array_map('intval', $a['periodos']));
}
$ts = isset($a['ts']) && is_numeric($a['ts']) ? ms_a_fecha((float) $a['ts']) : ahora();

$sql = db()->prepare('UPDATE comandos SET confirmado = ?, ok = ?, periodos_ack = ? WHERE id = ?');
$sql->execute([$ts, $a['ok'] ? 1 : 0, $periodos, (int) $a['cmd']]);
if ($sql->rowCount() === 0 && !db()->query('SELECT 1 FROM comandos WHERE id = ' . (int) $a['cmd'])->fetch()) {
    fallar('no existe el comando ' . (int) $a['cmd'], 404);
}

if ($periodos !== null) {
    guardar_estado('periodos_ack', $periodos);
}
responder(['ok' => true]);
