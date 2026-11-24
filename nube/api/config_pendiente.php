<?php
// GET desde la PC local (polling cada 30 s): comandos sin confirmar.
// Respuesta: {"ok": true, "comandos": [{"cmd": 17, "sensor": 0, "periodo_ms": 5000}, ...]}
// El polling queda registrado como latido del Nivel 2 para el dashboard.

require __DIR__ . '/_comun.php';

exigir_metodo('GET');
exigir_api_key();

if (isset($_GET['gateway']) && is_string($_GET['gateway'])) {
    guardar_estado('gateway', substr($_GET['gateway'], 0, 32)); // la PC informa si el ESP8266 esta online
}
guardar_estado('ultimo_poll', null);

// Comandos de las ultimas 24 h que no fueron confirmados ni reemplazados por uno posterior del mismo sensor
$filas = db()->query('SELECT id, sensor, periodo_ms FROM comandos
    WHERE confirmado IS NULL AND reemplazado = 0 AND creado > UTC_TIMESTAMP() - INTERVAL 1 DAY
    ORDER BY id')->fetchAll();

if ($filas) {
    $ids = implode(',', array_map('intval', array_column($filas, 'id')));
    db()->prepare("UPDATE comandos SET entregado = ? WHERE entregado IS NULL AND id IN ($ids)")->execute([ahora()]);
}

$comandos = array_map(function ($f) {
    return ['cmd' => (int) $f['id'], 'sensor' => (int) $f['sensor'], 'periodo_ms' => (int) $f['periodo_ms']];
}, $filas);

responder(['ok' => true, 'comandos' => $comandos]);
