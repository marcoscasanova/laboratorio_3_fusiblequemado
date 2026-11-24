<?php
// GET desde el dashboard: periodos vigentes, ultimo contacto del Nivel 2, ultima confirmacion y ultimos comandos.

require __DIR__ . '/_comun.php';

exigir_metodo('GET');
$pdo = db();

// Periodos con los que se tomo la ultima ventana (los lee el ESP8266 del Arduino en cada ventana)
$ultima = $pdo->query('SELECT ts, p_temp, p_hum, p_ldr, p_dist, err_i2c FROM ventanas ORDER BY ts DESC LIMIT 1')->fetch();

$estado = [];
foreach ($pdo->query('SELECT clave, valor, actualizado FROM estado') as $f) {
    $estado[$f['clave']] = ['valor' => $f['valor'], 'ts' => fecha_a_ms($f['actualizado'])];
}

/* Una fila de la tabla comandos en el formato del dashboard. */
function comando(array $f): array
{
    if ((int) $f['reemplazado'] === 1 && $f['confirmado'] === null) {
        $situacion = 'reemplazado';
    } elseif ($f['confirmado'] !== null) {
        $situacion = (int) $f['ok'] === 1 ? 'aplicado' : 'fallo';
    } else {
        $situacion = $f['entregado'] !== null ? 'enviado' : 'en cola';
    }
    return [
        'cmd' => (int) $f['id'],
        'sensor' => (int) $f['sensor'],
        'periodo_ms' => (int) $f['periodo_ms'],
        'situacion' => $situacion,
        'creado' => fecha_a_ms($f['creado']),
        'entregado' => fecha_a_ms($f['entregado']),
        'confirmado' => fecha_a_ms($f['confirmado']),
        'periodos_ack' => $f['periodos_ack'] === null ? null : array_map('intval', explode(',', $f['periodos_ack'])),
    ];
}

$ultimo_ack = $pdo->query('SELECT * FROM comandos WHERE confirmado IS NOT NULL ORDER BY confirmado DESC LIMIT 1')->fetch();
$comandos = array_map('comando', $pdo->query('SELECT * FROM comandos ORDER BY id DESC LIMIT 10')->fetchAll());

responder([
    'ok' => true,
    'ahora' => (int) round(microtime(true) * 1000),
    'ultima_ventana' => $ultima ? fecha_a_ms($ultima['ts']) : null,
    'periodos_vigentes' => $ultima ? array_map(function ($x) { return $x === null ? null : (int) $x; },
        [$ultima['p_temp'], $ultima['p_hum'], $ultima['p_ldr'], $ultima['p_dist']]) : null,
    'errores_i2c' => $ultima && $ultima['err_i2c'] !== null ? (int) $ultima['err_i2c'] : null,
    'ultimo_poll' => $estado['ultimo_poll']['ts'] ?? null,
    'ultima_subida' => $estado['ultima_telemetria']['ts'] ?? null,
    'gateway' => $estado['gateway'] ?? null,
    'ultimo_ack' => $ultimo_ack ? comando($ultimo_ack) : null,
    'periodo_min' => PERIODO_MIN,
    'periodo_max' => PERIODO_MAX,
    'comandos' => $comandos,
]);
