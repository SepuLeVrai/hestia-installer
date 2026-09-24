<?php
declare(strict_types=1);

// Private CLI resource, copied into a root-owned isolated execution snapshot.
// Never load includes/db.php, config.php, install.php or an Assistant secret file.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
set_error_handler(static function (): never { throw new RuntimeException('BRIDGE_INTERNAL'); });

function bridge_keys(mixed $value, array $keys): void
{
    if (!$value instanceof stdClass) throw new InvalidArgumentException('REQUEST_INVALID');
    $actual = array_keys(get_object_vars($value)); sort($actual); sort($keys);
    if ($actual !== $keys) throw new InvalidArgumentException('REQUEST_INVALID');
}
function bridge_text(mixed $value, int $min, int $max): void
{
    if (!is_string($value) || strlen($value) < $min || strlen($value) > $max
        || preg_match('//u', $value) !== 1 || preg_match('/\p{C}/u', $value)) {
        throw new InvalidArgumentException('REQUEST_INVALID');
    }
}
function bridge_run(#[SensitiveParameter] stdClass $request): array
{
    $base = ['version', 'operation', 'request_id', 'database'];
    $fresh = ($request->operation ?? null) === 'fresh_database';
    if ($fresh) $base = array_merge($base, ['administrator', 'confirmed']);
    bridge_keys($request, $base);
    if ($request->version !== 1 || !in_array($request->operation, ['fresh_database', 'inspect_database'], true)
        || !is_string($request->request_id) || !preg_match('/^[a-f0-9]{32}$/D', $request->request_id)) {
        throw new InvalidArgumentException('REQUEST_INVALID');
    }
    $db = $request->database;
    bridge_keys($db, ['host', 'port', 'name', 'user', 'password']);
    if ($db->host !== '127.0.0.1' || !is_int($db->port) || $db->port < 1 || $db->port > 65535
        || !is_string($db->name) || !preg_match('/^[A-Za-z0-9_]{1,64}$/D', $db->name)
        || in_array(strtolower($db->name), ['mysql', 'sys', 'information_schema', 'performance_schema'], true)
        || !is_string($db->user) || !preg_match('/^[A-Za-z0-9_]{1,32}$/D', $db->user)
        || strtolower($db->user) === 'root') {
        throw new InvalidArgumentException('REQUEST_INVALID');
    }
    bridge_text($db->password, 1, 1024);
    if ($fresh) {
        if ($request->confirmed !== true) throw new InvalidArgumentException('REQUEST_INVALID');
        bridge_keys($request->administrator, ['first_name', 'last_name', 'email', 'password']);
        foreach (get_object_vars($request->administrator) as $value) bridge_text($value, 1, 512);
    }
    if (!class_exists(PDO::class) || !in_array('mysql', PDO::getAvailableDrivers(), true)) {
        return [false, null, 'RUNTIME_UNAVAILABLE'];
    }
    try {
        $pdo = new PDO('mysql:host=127.0.0.1;port=' . $db->port . ';dbname=' . $db->name . ';charset=utf8mb4',
            $db->user, $db->password, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_EMULATE_PREPARES => false, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_TIMEOUT => 5, PDO::ATTR_PERSISTENT => false,
                PDO::MYSQL_ATTR_MULTI_STATEMENTS => false]);
    } catch (Throwable $error) {
        return [false, null, 'CONNECTION_FAILED'];
    }
    if (!$fresh) {
        try {
            if (!str_contains((string)$pdo->query('SELECT VERSION()')->fetchColumn(), 'MariaDB')) {
                return [false, null, 'MARIADB_REQUIRED'];
            }
            $counts = [];
            foreach (['TABLES' => 'TABLE_SCHEMA', 'ROUTINES' => 'ROUTINE_SCHEMA', 'EVENTS' => 'EVENT_SCHEMA'] as $table => $column) {
                $q = $pdo->prepare("SELECT COUNT(*) FROM information_schema.$table WHERE $column = ?");
                $q->execute([$db->name]);
                $counts[strtolower($table)] = (int)$q->fetchColumn();
            }
            return [true, ['scope' => 'DATABASE_OBSERVATION', ...$counts, 'empty' => array_sum($counts) === 0,
                'retry_authorized' => false, 'application_installed' => false], null];
        } catch (Throwable $error) {
            return [false, null, 'INSPECTION_FAILED'];
        }
    }
    try {
        require_once __DIR__ . '/engine/includes/installation/fresh.php';
    } catch (Throwable $error) {
        return [false, null, 'ENGINE_UNAVAILABLE'];
    }
    try {
        // Explicit mapping: no raw 5A payload or SQL application password reaches the engine.
        $a = $request->administrator;
        $result = hestia_install_fresh_database($pdo, ['first_name' => $a->first_name,
            'last_name' => $a->last_name, 'email' => $a->email, 'password' => $a->password], true);
        return [true, $result, null];
    } catch (HestiaFreshInstallException $error) {
        $allowed = ['ADMIN_INPUT_INVALID', 'FRESH_CONFIRMATION_REQUIRED', 'ACTIVE_TRANSACTION_REFUSED',
            'MARIADB_REQUIRED', 'PDO_EXCEPTION_MODE_REQUIRED', 'DATABASE_TARGET_INVALID', 'INSTALL_VERSION_MISMATCH',
            'SCHEMA_INVALID', 'INSTALLATION_BUSY', 'FRESH_DATABASE_NOT_EMPTY', 'FRESH_PREFLIGHT_FAILED',
            'FRESH_INCOMPLETE_MANUAL_ACTION', 'INSTALL_LOCK_RELEASE_FAILED'];
        return [false, null, in_array($error->errorCode, $allowed, true) ? $error->errorCode : 'ENGINE_FAILED'];
    } catch (Throwable $error) {
        return [false, null, 'ENGINE_FAILED'];
    }
}

$id = null; $operation = null;
try {
    $raw = stream_get_contents(STDIN, 16385);
    if (!is_string($raw) || strlen($raw) > 16384) throw new InvalidArgumentException('REQUEST_INVALID');
    $request = json_decode($raw, false, 16, JSON_THROW_ON_ERROR);
    // The private writer uses compact ASCII JSON. Exact round-trip rejects duplicate
    // members, noncanonical numbers and trailing content rather than silently selecting a value.
    if (!$request instanceof stdClass || json_encode($request, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) !== $raw) {
        throw new InvalidArgumentException('REQUEST_INVALID');
    }
    if (isset($request->request_id) && is_string($request->request_id) && preg_match('/^[a-f0-9]{32}$/D', $request->request_id)) $id = $request->request_id;
    if (isset($request->operation) && in_array($request->operation, ['fresh_database', 'inspect_database'], true)) $operation = $request->operation;
    [$ok, $result, $error] = bridge_run($request);
} catch (Throwable $exception) {
    $ok = false; $result = null; $error = 'REQUEST_INVALID';
}
// Only fixed errors or the closed engine result are emitted. No traces or diagnostics.
echo json_encode(['version' => 1, 'operation' => $operation, 'request_id' => $id,
    'ok' => $ok, 'result' => $result, 'error' => $error], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
exit($ok ? 0 : 20);
