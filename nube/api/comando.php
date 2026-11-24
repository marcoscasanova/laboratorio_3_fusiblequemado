<?php
// POST desde el dashboard al presionar "Aplicar": encola un periodo nuevo para un sensor.
// Cuerpo: {"sensor": 0, "periodo_ms": 5000, "clave": "<PANEL_CLAVE>"}

require __DIR__ . '/_comun.php';

exigir_metodo('POST');
$c = cuerpo_json();

if (PANEL_CLAVE !== '' && !hash_equals(PANEL_CLAVE, (string) ($c['clave'] ?? ''))) {
    fallar('clave del panel incorrecta', 401);
}
$sensor = $c['sensor'] ?? null;
$periodo = $c['periodo_ms'] ?? null;
if (!is_int($sensor) || $sensor < 0 || $sensor >= N_SENS) {
    fallar('"sensor" debe ser 0, 1, 2 o 3');
}
if (!is_int($periodo) || $periodo < PERIODO_MIN[$sensor] || $periodo > PERIODO_MAX) {
    fallar('"periodo_ms" para ese sensor debe estar entre ' . PERIODO_MIN[$sensor] . ' y ' . PERIODO_MAX);
}

$pdo = db();
$pdo->beginTransaction();
// Un comando nuevo deja sin efecto los pendientes del mismo sensor
$pdo->prepare('UPDATE comandos SET reemplazado = 1 WHERE sensor = ? AND confirmado IS NULL AND reemplazado = 0')
    ->execute([$sensor]);
$pdo->prepare('INSERT INTO comandos (sensor, periodo_ms, creado) VALUES (?, ?, ?)')
    ->execute([$sensor, $periodo, ahora()]);
$id = (int) $pdo->lastInsertId();
$pdo->commit();

responder(['ok' => true, 'cmd' => $id]);
