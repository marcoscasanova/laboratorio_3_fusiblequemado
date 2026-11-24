<?php
// POST desde la PC local (cada 10 min): lote de ventanas de promedios.
// Cuerpo: {"gateway": "online", "ventanas": [{"ts": <ms UTC>, "seq": 12, "err": 0, "v": [T, HR, R_LDR, d], "n": [..], "p": [..]}, ...]}
// Reenviar el mismo lote no duplica filas (clave unica ts + seq).

require __DIR__ . '/_comun.php';

exigir_metodo('POST');
exigir_api_key();
$cuerpo = cuerpo_json();
$ventanas = $cuerpo['ventanas'] ?? null;
if (!is_array($ventanas) || count($ventanas) > 5000) {
    fallar('"ventanas" debe ser una lista de hasta 5000 elementos');
}

$sql = db()->prepare('INSERT IGNORE INTO ventanas
    (ts, seq, temp, n_temp, p_temp, hum, n_hum, p_hum, ldr, n_ldr, p_ldr, dist, n_dist, p_dist, err_i2c)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');

/* Toma el elemento i de una lista como numero, o null. */
function num($lista, int $i): ?float
{
    return (is_array($lista) && isset($lista[$i]) && is_numeric($lista[$i])) ? (float) $lista[$i] : null;
}

$insertadas = 0;
$descartadas = 0;
db()->beginTransaction();
foreach ($ventanas as $w) {
    if (!is_array($w) || !isset($w['ts'], $w['seq']) || !is_numeric($w['ts']) || !is_numeric($w['seq'])) {
        $descartadas++;
        continue;
    }
    $fila = [ms_a_fecha((float) $w['ts']), ((int) $w['seq']) & 0xFF];
    for ($i = 0; $i < N_SENS; $i++) {
        $n = (int) (num($w['n'] ?? null, $i) ?? 0);
        $v = num($w['v'] ?? null, $i);
        $p = num($w['p'] ?? null, $i);
        $fila[] = $n > 0 ? $v : null; // sin muestras no hay promedio
        $fila[] = max(0, min(65535, $n));
        $fila[] = $p === null ? null : (int) $p;
    }
    $fila[] = isset($w['err']) && is_numeric($w['err']) ? (int) $w['err'] : null;
    $sql->execute($fila);
    $insertadas += $sql->rowCount();
}
db()->commit();

guardar_estado('ultima_telemetria', (string) count($ventanas));
if (isset($cuerpo['gateway']) && is_string($cuerpo['gateway'])) {
    guardar_estado('gateway', substr($cuerpo['gateway'], 0, 32));
}

responder(['ok' => true, 'recibidas' => count($ventanas), 'insertadas' => $insertadas, 'descartadas' => $descartadas]);
