<?php
declare(strict_types=1);

// CLI-only, no Web bootstrap/config file, no SQL mutation, no diagnostic output.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
set_error_handler(static function (): never { throw new RuntimeException('AUDIT_UNAVAILABLE'); });
require_once __DIR__ . '/sql_accounts_policy.php';

function local_accounts_keys(mixed $value, array $expected): void
{
    if (!$value instanceof stdClass) throw new RuntimeException('REQUEST_INVALID');
    $keys = array_keys(get_object_vars($value)); sort($keys); sort($expected);
    if ($keys !== $expected) throw new RuntimeException('REQUEST_INVALID');
}
function local_accounts_credential(#[SensitiveParameter] mixed $value): void
{
    local_accounts_keys($value, ['user', 'password']);
    if (!is_string($value->user) || !preg_match('/^[A-Za-z0-9_]{1,32}$/D', $value->user)
        || strtolower($value->user) === 'root' || !is_string($value->password)
        || strlen($value->password) < 1 || strlen($value->password) > 1024
        || preg_match('//u', $value->password) !== 1 || preg_match('/\p{C}/u', $value->password)) {
        throw new RuntimeException('REQUEST_INVALID');
    }
}
function local_accounts_run(#[SensitiveParameter] stdClass $request): void
{
    local_accounts_keys($request, ['version', 'operation', 'request_id', 'database', 'application', 'provisioning']);
    if ($request->version !== 1 || $request->operation !== 'audit_local_accounts'
        || !is_string($request->request_id) || !preg_match('/^[a-f0-9]{32}$/D', $request->request_id)) {
        throw new RuntimeException('REQUEST_INVALID');
    }
    $db = $request->database;
    local_accounts_keys($db, ['host', 'port', 'name']);
    if ($db->host !== '127.0.0.1' || $db->port !== 3306 || !is_string($db->name)
        || !preg_match('/^[A-Za-z0-9_]{1,64}$/D', $db->name)
        || in_array(strtolower($db->name), ['mysql', 'sys', 'information_schema', 'performance_schema'], true)) {
        throw new RuntimeException('REQUEST_INVALID');
    }
    local_accounts_credential($request->application); local_accounts_credential($request->provisioning);
    if ($request->application->user === $request->provisioning->user
        || $request->application->password === $request->provisioning->password) throw new RuntimeException('ACCOUNT_SEPARATION_REQUIRED');
    $server = null;
    foreach (['provisioning', 'application'] as $profile) {
        $credential = $request->{$profile};
        try {
            $pdo = new PDO('mysql:host=127.0.0.1;port=3306;dbname=' . $db->name . ';charset=utf8mb4',
                $credential->user, $credential->password, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                    PDO::ATTR_EMULATE_PREPARES => false, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                    PDO::ATTR_TIMEOUT => 5, PDO::ATTR_PERSISTENT => false, PDO::MYSQL_ATTR_MULTI_STATEMENTS => false]);
        } catch (Throwable $error) { throw new RuntimeException('CONNECTION_FAILED'); }
        $identity = $pdo->query('SELECT VERSION() AS version, DATABASE() AS db, CURRENT_USER() AS account, CURRENT_ROLE() AS role, @@server_id AS server_id, @@hostname AS host')->fetch();
        // New server series must be requalified (e.g. hidden DENY semantics).
        if (!is_array($identity) || !preg_match('/^(?:10\.11|11\.4|11\.8)\.[0-9]+[^\r\n]*MariaDB/', (string)$identity['version'])
            || $identity['db'] !== $db->name) throw new RuntimeException('SERVER_PROFILE_UNSUPPORTED');
        $fingerprint = [$identity['version'], $identity['server_id'], $identity['host']];
        if ($server !== null && $fingerprint !== $server) throw new RuntimeException('TARGET_IDENTITY_MISMATCH');
        $server = $fingerprint;
        if (!hestia_account_policy(hestia_bounded_grants($pdo, false), hestia_bounded_grants($pdo, true),
            (string)$identity['account'], $identity['role'], $credential->user, $db->name, $profile)) {
            throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
        }
        $pdo = null;
    }
}

$id = null; $ok = false; $error = 'REQUEST_INVALID';
try {
    $raw = stream_get_contents(STDIN, 16385);
    if (!is_string($raw) || strlen($raw) > 16384) throw new RuntimeException('REQUEST_INVALID');
    $request = json_decode($raw, false, 16, JSON_THROW_ON_ERROR);
    if (!$request instanceof stdClass || json_encode($request, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) !== $raw) {
        throw new RuntimeException('REQUEST_INVALID');
    }
    if (is_string($request->request_id ?? null) && preg_match('/^[a-f0-9]{32}$/D', $request->request_id)) $id = $request->request_id;
    local_accounts_run($request); $ok = true; $error = null;
} catch (Throwable $exception) {
    $allowed = ['REQUEST_INVALID', 'ACCOUNT_SEPARATION_REQUIRED', 'CONNECTION_FAILED', 'SERVER_PROFILE_UNSUPPORTED',
        'TARGET_IDENTITY_MISMATCH', 'ACCOUNT_POLICY_REJECTED', 'AUDIT_UNAVAILABLE'];
    $error = in_array($exception->getMessage(), $allowed, true) ? $exception->getMessage() : 'AUDIT_UNAVAILABLE';
}
echo json_encode(['version' => 1, 'request_id' => $id, 'operation' => 'audit_local_accounts', 'ok' => $ok,
    'error' => $error, 'policy' => $ok ? 'local-dml-v1' : null, 'application_installed' => false], JSON_THROW_ON_ERROR);
exit($ok ? 0 : 20);
