<?php
declare(strict_types=1);

// Private CLI-only contract. No Web bootstrap, config, Assistant, or UI import.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
set_error_handler(static function (): never { throw new RuntimeException('DATABASE_STEP_FAILED'); });
require_once __DIR__ . '/engine/includes/installation/connection.php';
require_once __DIR__ . '/engine/includes/installation/fresh.php';
require_once __DIR__ . '/sql_accounts_policy.php';
require_once __DIR__ . '/trigger_definer.php';

function dbstep_keys(mixed $value, array $keys): void
{
    if (!$value instanceof stdClass) throw new RuntimeException('REQUEST_INVALID');
    $actual = array_keys(get_object_vars($value)); sort($actual); sort($keys);
    if ($actual !== $keys) throw new RuntimeException('REQUEST_INVALID');
}
function dbstep_credential(#[SensitiveParameter] mixed $value, bool $authority = false): void
{
    dbstep_keys($value, ['user', 'password']);
    if (!is_string($value->user) || !preg_match('/^[A-Za-z0-9_]{1,32}$/D', $value->user)
        || (!$authority && strtolower($value->user) === 'root') || !is_string($value->password)
        || strlen($value->password) < 1 || strlen($value->password) > 1024
        || preg_match('//u', $value->password) !== 1 || preg_match('/\p{C}/u', $value->password)) {
        throw new RuntimeException('REQUEST_INVALID');
    }
}
function dbstep_identity(PDO $pdo, ?string $database): array
{
    $r = $pdo->query('SELECT VERSION() AS version, DATABASE() AS db, CURRENT_USER() AS account, CURRENT_ROLE() AS role, @@server_id AS id, @@hostname AS host')->fetch();
    if (!is_array($r) || !preg_match('/^(?:10\.11|11\.4|11\.8)\.[0-9]+[^\r\n]*MariaDB/', (string)$r['version'])
        || $r['db'] !== $database) throw new RuntimeException('SERVER_PROFILE_UNSUPPORTED');
    return $r;
}
function dbstep_audit(PDO $pdo, array $target, string $user, string $profile, array &$server): void
{
    $r = dbstep_identity($pdo, $target['name']);
    $fingerprint = [$r['version'], $r['id'], $r['host']];
    if ($server !== [] && $server !== $fingerprint) throw new RuntimeException('TARGET_IDENTITY_MISMATCH');
    $server = $fingerprint;
    if (!hestia_account_policy(hestia_bounded_grants($pdo, false), hestia_bounded_grants($pdo, true),
        (string)$r['account'], $r['role'], $user, $target['name'], $profile, $target['tls_required'])) {
        throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    }
}
function dbstep_account(string $user): string { return '`' . $user . '`@`127.0.0.1`'; }
function dbstep_schema(string $database): string { return '`' . str_replace('_', '\\_', $database) . '`.*'; }

function dbstep_run(#[SensitiveParameter] stdClass $request, bool &$mutated): array
{
    dbstep_keys($request, ['version', 'operation', 'request_id', 'target', 'mode', 'application', 'migration', 'authority', 'administrator', 'confirmed']);
    if ($request->version !== 1 || !in_array($request->operation, ['audit_accounts', 'prepare_database'], true)
        || !is_string($request->request_id) || !preg_match('/^[a-f0-9]{32}$/D', $request->request_id)
        || !in_array($request->mode, ['managed', 'existing_local', 'remote'], true)) throw new RuntimeException('REQUEST_INVALID');
    dbstep_keys($request->target, ['host', 'name', 'port', 'tls_required', 'tls_ca_file', 'tls_ca_sha256']);
    $target = hestia_sql_connection_target((array)$request->target);
    if (($request->mode === 'remote') !== $target['tls_required']
        || ($request->mode === 'managed' && ($target['host'] !== '127.0.0.1' || $target['port'] !== 3306))) throw new RuntimeException('REQUEST_INVALID');
    if ($target['tls_required'] && $target['tls_ca_file'] !== __DIR__ . '/ca.pem') throw new RuntimeException('REQUEST_INVALID');
    dbstep_credential($request->application); dbstep_credential($request->migration);
    $fresh = $request->operation === 'prepare_database';
    if ($fresh) {
        if ($request->confirmed !== true) throw new RuntimeException('REQUEST_INVALID');
        dbstep_keys($request->administrator, ['first_name', 'last_name', 'email', 'password']);
        hestia_install_validate_administrator((array)$request->administrator);
        hestia_validate_runtime_dependencies();
    } elseif ($request->confirmed !== false || $request->administrator !== null || $request->authority !== null) {
        throw new RuntimeException('REQUEST_INVALID');
    }
    $managed = $fresh && $request->mode === 'managed';
    if ($managed) dbstep_credential($request->authority, true);
    elseif ($request->authority !== null) throw new RuntimeException('REQUEST_INVALID');
    $credentials = [$request->application, $request->migration];
    if ($managed) $credentials[] = $request->authority;
    for ($i = 0; $i < count($credentials); $i++) {
        for ($j = $i + 1; $j < count($credentials); $j++) {
            if ($credentials[$i]->user === $credentials[$j]->user || $credentials[$i]->password === $credentials[$j]->password) {
                throw new RuntimeException('ACCOUNT_SEPARATION_REQUIRED');
            }
        }
    }
    $authority = null; $locked = false; $server = [];
    try {
        if ($managed) {
            $authority = hestia_sql_connect([...$target, 'name' => null], $request->authority->user, $request->authority->password, true);
            $r = dbstep_identity($authority, null);
            if (!in_array($r['account'], [$request->authority->user . '@localhost', $request->authority->user . '@127.0.0.1'], true)
                || $r['role'] !== null) throw new RuntimeException('AUTHORITY_IDENTITY_REJECTED');
            // This managed profile requires an explicit local account-management authority.
            // Only the authority (never the migration or runtime account) may have global ALL.
            $authorityReady = false;
            $identityPattern = '(?:`' . preg_quote($request->authority->user, '/') . '`@`(?:localhost|127\\.0\\.0\\.1)`|\''
                . preg_quote($request->authority->user, '/') . '\'@\'(?:localhost|127\\.0\\.0\\.1)\')';
            foreach (hestia_bounded_grants($authority, false) as $grant) {
                if (preg_match('/^GRANT ALL PRIVILEGES ON \\*\\.\\* TO ' . $identityPattern
                    . '(?: [^\\r\\n]*)? WITH GRANT OPTION$/D', $grant)) $authorityReady = true;
            }
            if (!$authorityReady) throw new RuntimeException('AUTHORITY_PRIVILEGES_REQUIRED');
            $server = [$r['version'], $r['id'], $r['host']];
            $lock = 'hestia.provision.' . substr(hash('sha256', strtolower($target['name'])), 0, 46);
            $q = $authority->prepare('SELECT GET_LOCK(?, 0)'); $q->execute([$lock]);
            if ((int)$q->fetchColumn() !== 1) throw new RuntimeException('INSTALLATION_BUSY');
            $locked = true;
            // Require visibility of ALL host variants, not just the authenticating account.
            $q = $authority->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User IN (?, ?)');
            $q->execute([$request->application->user, $request->migration->user]);
            if ((int)$q->fetchColumn() !== 0) throw new RuntimeException('SQL_ACCOUNT_OCCUPIED');
            $definerUser = hdf_user($target['name']);
            if (in_array($definerUser, array_map(static fn($c) => $c->user, $credentials), true)) {
                throw new RuntimeException('ACCOUNT_SEPARATION_REQUIRED');
            }
            hdf_absent($authority, $definerUser);
            $q = $authority->prepare('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = ?');
            $q->execute([$target['name']]);
            if ((int)$q->fetchColumn() !== 0) throw new RuntimeException('SQL_DATABASE_OCCUPIED');
            // No IF NOT EXISTS, no ALTER/DROP of existing resources. DDL is not atomic.
            $mutated = true;
            $authority->exec('CREATE DATABASE `' . $target['name'] . '` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci');
            foreach (['application', 'migration'] as $kind) {
                $c = $request->{$kind}; $account = dbstep_account($c->user);
                $hash = '*' . strtoupper(sha1(sha1($c->password, true)));
                $authority->exec("CREATE USER $account IDENTIFIED BY PASSWORD '$hash'");
                $privileges = $kind === 'application' ? 'SELECT, INSERT, UPDATE, DELETE' : 'ALL PRIVILEGES';
                $authority->exec('GRANT ' . $privileges . ' ON ' . dbstep_schema($target['name']) . ' TO ' . $account);
            }
        }
        $migration = hestia_sql_connect($target, $request->migration->user, $request->migration->password);
        $application = hestia_sql_connect($target, $request->application->user, $request->application->password);
        dbstep_audit($migration, $target, $request->migration->user, 'provisioning', $server);
        dbstep_audit($application, $target, $request->application->user, 'application', $server);
        if (!$fresh) return ['scope' => 'ACCOUNTS_VERIFIED', 'tls_verified' => $target['tls_required'], 'application_installed' => false];
        try {
            $result = hestia_install_fresh_database($migration, (array)$request->administrator, true);
        } catch (HestiaFreshInstallException $error) {
            if (in_array($error->errorCode, ['FRESH_INCOMPLETE_MANUAL_ACTION', 'INSTALL_LOCK_RELEASE_FAILED'], true)) $mutated = true;
            throw $error;
        }
        $mutated = true;
        if ($managed) hdf_rebind_fresh($authority, $target['name'], $request->migration->user);
        // Observe actual fresh invariants through the restricted application connection.
        $q = $application->prepare("SELECT u.password_hash, r.code_role FROM UserInfo u JOIN Roles r ON r.id_role_applicatif=u.id_role_applicatif WHERE u.email=? AND u.actif=1");
        $q->execute([strtolower($request->administrator->email)]); $admin = $q->fetch();
        $version = $application->query("SELECT valeur FROM App_Config WHERE cle='APP_VERSION'")->fetchColumn();
        $assistant = $application->query("SELECT valeur FROM App_Config WHERE cle='assistant.enabled'")->fetchColumn();
        if (!is_array($admin) || $admin['code_role'] !== 'ADMIN_GENERAL' || !password_verify($request->administrator->password, $admin['password_hash'])
            || (int)$application->query('SELECT COUNT(*) FROM UserInfo')->fetchColumn() !== 1
            || $version !== HESTIA_INSTALL_VERSION_KEY || $assistant !== '0') throw new RuntimeException('DATABASE_VERIFICATION_FAILED');
        dbstep_audit($application, $target, $request->application->user, 'application', $server);
        $migration = null;
        if ($managed) {
            // Only the temporary account this invocation created may be dropped.
            $authority->exec('DROP USER ' . dbstep_account($request->migration->user));
            $q = $authority->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User = ?');
            $q->execute([$request->migration->user]);
            if ((int)$q->fetchColumn() !== 0) throw new RuntimeException('TEMPORARY_ACCOUNT_CLEANUP_FAILED');
            // Exercise all five effects only AFTER the migration account is gone.
            // The private fresh database is not active yet; probes are rolled back.
            hdf_smoke($application);
            hdf_audit($authority, $target['name'], $definerUser);
        }
        return ['scope' => 'DATABASE_READY', 'version' => $result['version'], 'schema_statements' => $result['schema_statements'],
            'tls_verified' => $target['tls_required'], 'application_verified' => true, 'migration_retained' => !$managed,
            'application_installed' => false, 'assistant_enabled' => false];
    } finally {
        if ($locked) {
            try {
                $q = $authority->prepare('SELECT RELEASE_LOCK(?)'); $q->execute([$lock]);
                if ((int)$q->fetchColumn() !== 1) throw new RuntimeException('INSTALL_LOCK_RELEASE_FAILED');
            } catch (Throwable $error) { throw new RuntimeException('INSTALL_LOCK_RELEASE_FAILED'); }
        }
    }
}

$id = null; $operation = null; $mutated = false; $result = null; $error = null;
try {
    $raw = stream_get_contents(STDIN, 16385);
    if (!is_string($raw) || strlen($raw) > 16384) throw new RuntimeException('REQUEST_INVALID');
    $request = json_decode($raw, false, 16, JSON_THROW_ON_ERROR);
    if (!$request instanceof stdClass || json_encode($request, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) !== $raw) throw new RuntimeException('REQUEST_INVALID');
    if (is_string($request->request_id ?? null) && preg_match('/^[a-f0-9]{32}$/D', $request->request_id)) $id = $request->request_id;
    if (in_array($request->operation ?? null, ['audit_accounts', 'prepare_database'], true)) $operation = $request->operation;
    $result = dbstep_run($request, $mutated);
} catch (Throwable $exception) {
    $allowed = ['REQUEST_INVALID', 'SQL_TARGET_INVALID', 'SQL_CA_INVALID', 'SQL_CREDENTIAL_INVALID', 'SQL_DRIVER_REQUIRED',
        'SQL_CONNECTION_FAILED', 'SQL_TLS_CONNECTION_FAILED', 'ACCOUNT_SEPARATION_REQUIRED', 'SERVER_PROFILE_UNSUPPORTED',
        'TARGET_IDENTITY_MISMATCH', 'ACCOUNT_POLICY_REJECTED', 'AUTHORITY_IDENTITY_REJECTED', 'AUTHORITY_PRIVILEGES_REQUIRED', 'SQL_ACCOUNT_OCCUPIED', 'SQL_DATABASE_OCCUPIED',
        'INSTALLATION_BUSY', 'AUDIT_UNAVAILABLE', 'DATABASE_VERIFICATION_FAILED', 'TEMPORARY_ACCOUNT_CLEANUP_FAILED',
        'ADMIN_INPUT_INVALID', 'FRESH_CONFIRMATION_REQUIRED', 'ACTIVE_TRANSACTION_REFUSED', 'MARIADB_REQUIRED',
        'PDO_EXCEPTION_MODE_REQUIRED', 'DATABASE_TARGET_INVALID', 'INSTALL_VERSION_MISMATCH', 'SCHEMA_INVALID',
        'FRESH_DATABASE_NOT_EMPTY', 'FRESH_PREFLIGHT_FAILED', 'FRESH_INCOMPLETE_MANUAL_ACTION', 'INSTALL_LOCK_RELEASE_FAILED',
        'DEFINER_ACCOUNT_OCCUPIED', 'DEFINER_PROFILE_REJECTED', 'DEFINER_TRIGGER_PROFILE_REJECTED', 'DEFINER_REBIND_FAILED', 'DEFINER_SMOKE_FAILED'];
    $code = $exception instanceof HestiaFreshInstallException ? $exception->errorCode : $exception->getMessage();
    $error = in_array($code, $allowed, true) ? $code : 'DATABASE_STEP_FAILED';
}
echo json_encode(['version' => 1, 'operation' => $operation, 'request_id' => $id, 'ok' => $error === null,
    'result' => $result, 'error' => $error, 'uncertain' => $error !== null && $mutated], JSON_THROW_ON_ERROR);
exit($error === null ? 0 : 20);
