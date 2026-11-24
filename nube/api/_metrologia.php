<?php
// Incertidumbre de cada variable y magnitudes derivadas (avance, seccion 4).
// Cada ventana trae el promedio del Arduino; el error de hoja de datos es sistematico, asi que no baja al promediar.

const T_AIRE_DEFECTO = 20.0; // temperatura supuesta para el HC-SR04 si la ventana no trae DS18B20 [C]
const INC_T_DEFECTO = 5.0; // incertidumbre de esa temperatura supuesta [C]
const MAGNUS_A = 17.62; // constantes de Magnus (Alduchov y Eskridge, 1996)
const MAGNUS_B = 243.12; // [C]

/* DS18B20: +-0,5 C entre -10 y 85 C (+-2 C fuera), en cuadratura con la mitad de la resolucion de 12 bits. */
function inc_temp(float $t): float
{
    $hoja = ($t >= -10 && $t <= 85) ? 0.5 : 2.0;
    return hypot($hoja, 0.0625 / 2);
}

/* DHT11: +-5 %HR y resolucion de 1 %HR. */
function inc_hum(float $hr): float
{
    return hypot(5.0, 1.0 / 2);
}

/* LDR: propaga dN = 0,5 cuentas y la incertidumbre de Rf en R = Rf (1024/N - 1). Entrada y salida en kOhm. */
function inc_ldr(float $r): float
{
    $n = 1024 * RF_KOHM / (RF_KOHM + $r); // cuentas del ADC que corresponden a esa resistencia
    $por_n = RF_KOHM * 1024 / ($n * $n) * 0.5;
    $por_rf = (1024 / $n - 1) * RF_INC_KOHM;
    return hypot($por_n, $por_rf);
}

/* Iluminancia segun la calibracion E = 10 (R/R10)^(-1/gamma), con su incertidumbre por la de R. Null si no se calibro. */
function lux_ldr(float $r, float $inc_r): ?array
{
    if (LDR_R10_KOHM <= 0 || $r <= 0) {
        return null;
    }
    $e = 10 * pow($r / LDR_R10_KOHM, -1 / LDR_GAMMA);
    return [$e, $e / LDR_GAMMA * $inc_r / $r];
}

/* HC-SR04 compensado: d = v t / 2 con v = 331,3 + 0,606 T. Entrada y salida en cm. */
function inc_dist(float $d_cm, float $t, float $inc_t): float
{
    $v = 331.3 + 0.606 * $t; // [m/s]
    $tiempo = 2 * ($d_cm / 100) / $v; // tiempo de vuelo [s]
    $por_t = $tiempo / 2 * 0.606 * $inc_t; // [m]
    $por_tiempo = $v / 2 * 2e-6; // mitad de la resolucion de 4 us de pulseIn [m]
    $hoja = 0.003; // +-3 mm de la hoja de datos [m]
    return sqrt($por_t ** 2 + $por_tiempo ** 2 + $hoja ** 2) * 100;
}

/* Punto de rocio (Magnus) y su incertidumbre por propagacion en cuadratura. */
function punto_rocio(float $t, float $hr, float $inc_t, float $inc_hr): ?array
{
    if ($hr <= 0) {
        return null;
    }
    $a = MAGNUS_A;
    $b = MAGNUS_B;
    $g = log($hr / 100) + $a * $t / ($b + $t);
    $td = $b * $g / ($a - $g);
    $dtd_dg = $a * $b / (($a - $g) ** 2);
    $dtd_dt = $dtd_dg * $a * $b / (($b + $t) ** 2);
    $dtd_dhr = $dtd_dg / $hr;
    return [$td, hypot($dtd_dt * $inc_t, $dtd_dhr * $inc_hr)];
}

/* Agrega a una ventana los valores con su incertidumbre y las magnitudes derivadas. */
function con_incertidumbre(array $f): array
{
    $r = ['ts' => (int) $f['ts_ms']];
    $t = $f['temp'] === null ? null : (float) $f['temp'];
    $hr = $f['hum'] === null ? null : (float) $f['hum'];
    $inc_t = $t === null ? null : inc_temp($t);
    $inc_hr = $hr === null ? null : inc_hum($hr);
    $r['temp'] = $t === null ? null : [round($t, 3), round($inc_t, 3)];
    $r['hum'] = $hr === null ? null : [round($hr, 2), round($inc_hr, 2)];

    if ($f['ldr'] === null) {
        $r['ldr'] = null;
        $r['lux'] = null;
    } else {
        $ldr = (float) $f['ldr'];
        $inc_ldr = inc_ldr($ldr);
        $r['ldr'] = [round($ldr, 3), round($inc_ldr, 3)];
        $lux = lux_ldr($ldr, $inc_ldr);
        $r['lux'] = $lux === null ? null : [round($lux[0], 2), round($lux[1], 2)];
    }

    if ($f['dist'] === null) {
        $r['dist'] = null;
    } else {
        $d = (float) $f['dist'];
        $r['dist'] = [round($d, 2), round(inc_dist($d, $t ?? T_AIRE_DEFECTO, $inc_t ?? INC_T_DEFECTO), 2)];
    }

    $rocio = ($t === null || $hr === null) ? null : punto_rocio($t, $hr, $inc_t, $inc_hr);
    $r['rocio'] = $rocio === null ? null : [round($rocio[0], 2), round($rocio[1], 2)];

    $r['n'] = [(int) $f['n_temp'], (int) $f['n_hum'], (int) $f['n_ldr'], (int) $f['n_dist']];
    $r['p'] = [$f['p_temp'], $f['p_hum'], $f['p_ldr'], $f['p_dist']];
    $r['p'] = array_map(function ($x) { return $x === null ? null : (int) $x; }, $r['p']);
    return $r;
}
