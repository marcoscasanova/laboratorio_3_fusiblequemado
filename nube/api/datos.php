<?php
// GET desde el dashboard: ventanas entre dos fechas, cada valor como [valor, incertidumbre].
// Parametros: desde, hasta (ms epoch UTC; por defecto las ultimas 24 h), max (puntos maximos, por defecto 2000).
// Si el rango tiene mas ventanas que max, se agrupan en intervalos iguales con promedio ponderado por n;
// en ese caso n es el promedio de muestras por ventana dentro del grupo.

require __DIR__ . '/_comun.php';
require __DIR__ . '/_metrologia.php';

exigir_metodo('GET');

$hasta = isset($_GET['hasta']) && is_numeric($_GET['hasta']) ? (float) $_GET['hasta'] : microtime(true) * 1000;
$desde = isset($_GET['desde']) && is_numeric($_GET['desde']) ? (float) $_GET['desde'] : $hasta - 86400000;
$max = isset($_GET['max']) && is_numeric($_GET['max']) ? max(50, min(10000, (int) $_GET['max'])) : 2000;
if ($desde >= $hasta) {
    fallar('"desde" debe ser anterior a "hasta"');
}
$rango = [ms_a_fecha($desde), ms_a_fecha($hasta)];

$cuenta = db()->prepare('SELECT COUNT(*) FROM ventanas WHERE ts BETWEEN ? AND ?');
$cuenta->execute($rango);
$total = (int) $cuenta->fetchColumn();

$agrupado = 0; // segundos por grupo, 0 = ventanas sin agrupar
if ($total > $max) {
    $agrupado = (int) ceil(($hasta - $desde) / 1000 / $max);
    $cols = '';
    foreach (COLUMNAS as $c) {
        $cols .= ", SUM($c * n_$c) / NULLIF(SUM(n_$c), 0) AS $c, ROUND(AVG(n_$c)) AS n_$c, MAX(p_$c) AS p_$c";
    }
    $sql = db()->prepare("SELECT ROUND(AVG(UNIX_TIMESTAMP(ts)) * 1000) AS ts_ms $cols
        FROM ventanas WHERE ts BETWEEN ? AND ?
        GROUP BY FLOOR(UNIX_TIMESTAMP(ts) / $agrupado) ORDER BY ts_ms");
} else {
    $sql = db()->prepare('SELECT ROUND(UNIX_TIMESTAMP(ts) * 1000) AS ts_ms, temp, n_temp, p_temp, hum, n_hum, p_hum,
        ldr, n_ldr, p_ldr, dist, n_dist, p_dist FROM ventanas WHERE ts BETWEEN ? AND ? ORDER BY ts');
}
$sql->execute($rango);

$filas = [];
while ($f = $sql->fetch()) {
    $filas[] = con_incertidumbre($f);
}

responder([
    'ok' => true,
    'desde' => (int) $desde,
    'hasta' => (int) $hasta,
    'ventanas' => $total,
    'agrupado_s' => $agrupado,
    'calibrada_ldr' => LDR_R10_KOHM > 0,
    'filas' => $filas,
]);
