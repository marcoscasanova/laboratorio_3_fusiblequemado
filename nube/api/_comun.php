<?php
// Funciones comunes de la API: conexion a la base, respuestas JSON y autenticacion.

require __DIR__ . '/../config.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

const N_SENS = 4; // 0 temperatura, 1 humedad, 2 LDR, 3 distancia
const COLUMNAS = ['temp', 'hum', 'ldr', 'dist']; // columna de cada sensor en la tabla ventanas
const PERIODO_MIN = [750, 2000, 10, 60]; // minimos del firmware del Arduino [ms]
const PERIODO_MAX = 3600000; // maximo del firmware del Arduino [ms]

// Ante cualquier error inesperado responde 500 en JSON sin mostrar detalles internos
set_exception_handler(function (Throwable $e) {
    error_log('API FusibleQuemado: ' . $e->getMessage());
    responder(['ok' => false, 'error' => 'error interno'], 500);
});

/* Conexion PDO unica, con la sesion en UTC. */
function db(): PDO
{
    static $pdo = null;
    if ($pdo === null) {
        $pdo = new PDO('mysql:host=' . DB_HOST . ';dbname=' . DB_NOMBRE . ';charset=utf8mb4', DB_USUARIO, DB_CLAVE, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);
        $pdo->exec("SET time_zone = '+00:00'");
    }
    return $pdo;
}

/* Envia la respuesta JSON y termina. */
function responder(array $datos, int $codigo = 200): void
{
    http_response_code($codigo);
    echo json_encode($datos, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

/* Responde un error de la peticion. */
function fallar(string $mensaje, int $codigo = 400): void
{
    responder(['ok' => false, 'error' => $mensaje], $codigo);
}

/* Solo acepta el metodo HTTP indicado. */
function exigir_metodo(string $metodo): void
{
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== $metodo) {
        header('Allow: ' . $metodo);
        fallar('metodo no permitido, usar ' . $metodo, 405);
    }
}

/* Lee el cuerpo JSON de la peticion. */
function cuerpo_json(): array
{
    $datos = json_decode(file_get_contents('php://input') ?: '', true);
    if (!is_array($datos)) {
        fallar('el cuerpo debe ser JSON');
    }
    return $datos;
}

/* Exige la clave de la PC local en el encabezado X-Api-Key (o en ?api_key= si el hosting filtra encabezados). */
function exigir_api_key(): void
{
    $clave = $_SERVER['HTTP_X_API_KEY'] ?? ($_GET['api_key'] ?? '');
    if (!is_string($clave) || !hash_equals(API_KEY, $clave)) {
        fallar('clave de API invalida', 401);
    }
}

/* Milisegundos epoch -> texto DATETIME(3) en UTC. */
function ms_a_fecha(float $ms): string
{
    $entero = (int) round($ms);
    return gmdate('Y-m-d H:i:s', intdiv($entero, 1000)) . sprintf('.%03d', $entero % 1000);
}

/* Texto DATETIME(3) en UTC -> milisegundos epoch (o null). */
function fecha_a_ms(?string $fecha): ?int
{
    if ($fecha === null) {
        return null;
    }
    $partes = explode('.', $fecha);
    $base = strtotime($partes[0] . ' UTC');
    return $base * 1000 + (isset($partes[1]) ? (int) str_pad(substr($partes[1], 0, 3), 3, '0') : 0);
}

/* Hora actual en UTC con milisegundos, para guardar. */
function ahora(): string
{
    return ms_a_fecha(microtime(true) * 1000);
}

/* Guarda un dato en la tabla estado. */
function guardar_estado(string $clave, ?string $valor): void
{
    db()->prepare('INSERT INTO estado (clave, valor, actualizado) VALUES (?, ?, ?)
                   ON DUPLICATE KEY UPDATE valor = VALUES(valor), actualizado = VALUES(actualizado)')
        ->execute([$clave, $valor, ahora()]);
}
