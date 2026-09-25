<?php
declare(strict_types=1);
// 5C1 private fixed-query inspection. Non-root worker, no Web bootstrap or DDL.
$operation = 'invalid'; $requestId = ''; $pdo = null;
$reply = static function (bool $ok, ?array $result, ?string $error) use (&$operation, &$requestId): never {
    echo json_encode(['version'=>1, 'operation'=>$operation, 'request_id'=>$requestId,
        'ok'=>$ok, 'result'=>$result, 'error'=>$error], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    exit($ok ? 0 : 20);
};
try {
    if (PHP_SAPI !== 'cli') throw new RuntimeException('REQUEST_INVALID');
    $raw = stream_get_contents(STDIN, 16385);
    if (!is_string($raw) || strlen($raw) > 16384) throw new RuntimeException('REQUEST_INVALID');
    $v = json_decode($raw, true, 12, JSON_THROW_ON_ERROR);
    if (!is_array($v) || array_keys($v) !== ['application','operation','request_id','target','version']
        || json_encode($v, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) !== $raw || $v['version'] !== 1
        || $v['operation'] !== 'inventory' || !is_string($v['request_id'])
        || !preg_match('/^[a-f0-9]{32}$/D', $v['request_id']) || !is_array($v['application'])
        || array_keys($v['application']) !== ['password','user']) throw new RuntimeException('REQUEST_INVALID');
    $operation = $v['operation']; $requestId = $v['request_id'];
    require_once __DIR__.'/sql_accounts_policy.php';
    require_once __DIR__.'/engine/includes/installation/connection.php';
    require_once __DIR__.'/engine/includes/installation/finalization.php';
    $pdo = hestia_sql_connect($v['target'], $v['application']['user'], $v['application']['password']);
    $identity = $pdo->query('SELECT VERSION() AS version, DATABASE() AS db, CURRENT_USER() AS account, CURRENT_ROLE() AS role')->fetch(PDO::FETCH_ASSOC);
    if (!str_contains($identity['version'], 'MariaDB') || $identity['db'] !== $v['target']['name']
        || !hestia_account_policy(hestia_bounded_grants($pdo, false), hestia_bounded_grants($pdo, true),
            $identity['account'], $identity['role'], $v['application']['user'], $v['target']['name'],
            'application', $v['target']['tls_required'])) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    if (!preg_match('/^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/', $identity['version'], $server)) throw new RuntimeException();
    // Only session settings are changed. No global setting, application write or named lock.
    $pdo->exec('SET SESSION max_statement_time=5');
    $pdo->exec('SET SESSION lock_wait_timeout=5');
    $pdo->exec('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ');
    $pdo->exec('START TRANSACTION READ ONLY, WITH CONSISTENT SNAPSHOT');
    if (!$pdo->inTransaction()) throw new RuntimeException();
    $metadata = static function () use ($pdo): array {
        $rows = static function (string $sql, int $maximum) use ($pdo): array {
            $q = $pdo->query($sql); $out = []; $bytes = 0;
            while (($row = $q->fetch(PDO::FETCH_ASSOC)) !== false) {
                $bytes += strlen(json_encode($row, JSON_THROW_ON_ERROR));
                if (count($out) >= $maximum || $bytes > 8388608) throw new RuntimeException('UPGRADE_SCHEMA_LIMIT');
                $out[] = $row;
            }
            return $out;
        };
        // No comments/default values, row values, routines, views' bodies or password hashes are returned.
        $tables = $rows('SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY BINARY TABLE_NAME LIMIT 513', 512);
        $columns = $rows('SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE, IS_NULLABLE, COLLATION_NAME, EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY BINARY TABLE_NAME, ORDINAL_POSITION LIMIT 16385', 16384);
        $indexes = $rows('SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART, INDEX_TYPE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() ORDER BY BINARY TABLE_NAME, BINARY INDEX_NAME, SEQ_IN_INDEX LIMIT 16385', 16384);
        return ['tables'=>$tables, 'columns'=>$columns, 'indexes'=>$indexes];
    };
    $before = $metadata(); $tables = []; $baseCount = 0; $views = 0; $nonInnodb = 0;
    foreach ($before['tables'] as $table) {
        $tables[$table['TABLE_NAME']] = $table;
        if ($table['TABLE_TYPE'] === 'BASE TABLE') {
            $baseCount++; if ($table['ENGINE'] !== 'InnoDB') $nonInnodb++;
        } elseif ($table['TABLE_TYPE'] === 'VIEW') { $views++; }
        else { throw new RuntimeException('UPGRADE_REQUIRED_TABLES_MISSING'); }
    }
    $required = ['users'=>'UserInfo', 'roles'=>'Roles', 'rbac_rules'=>'Role_Rbac_Action',
        'rbac_overrides'=>'User_Rbac_Override', 'web_sessions'=>'Sec_User_Session', 'settings'=>'App_Config'];
    $counts = [];
    foreach ($required as $label=>$table) {
        if (!isset($tables[$table]) || $tables[$table]['TABLE_TYPE'] !== 'BASE TABLE'
            || $tables[$table]['ENGINE'] !== 'InnoDB') throw new RuntimeException('UPGRADE_REQUIRED_TABLES_MISSING');
        // Identifier comes exclusively from the fixed list above, never from the request/metadata.
        $counts[$label] = (string)$pdo->query('SELECT COUNT(*) FROM `'.$table.'`')->fetchColumn();
    }
    $release = $pdo->query("SELECT valeur FROM App_Config WHERE cle='APP_VERSION'")->fetchColumn();
    if ($release !== HESTIA_INSTALL_VERSION_KEY) throw new RuntimeException('UPGRADE_SOURCE_VERSION_UNSUPPORTED');
    $counts['active_admins'] = (string)$pdo->query("SELECT COUNT(*) FROM UserInfo u JOIN Roles r ON u.id_role_applicatif=r.id_role_applicatif WHERE u.actif=1 AND r.actif=1 AND r.code_role='ADMIN_GENERAL'")->fetchColumn();
    if ($counts['active_admins'] === '0') throw new RuntimeException('UPGRADE_NO_ACTIVE_ADMIN');
    $setting = $pdo->query("SELECT valeur FROM App_Config WHERE cle='assistant.enabled'")->fetchColumn();
    if (!in_array($setting, ['0','1'], true)) throw new RuntimeException();
    $after = $metadata();
    if ($before !== $after) throw new RuntimeException('UPGRADE_SCHEMA_CHANGED');
    if (!$pdo->rollBack()) throw new RuntimeException();
    $reply(true, ['release'=>$release, 'server_version'=>$server[1],
        'schema_sha256'=>hash('sha256', json_encode($before, JSON_THROW_ON_ERROR)),
        'tables'=>$baseCount, 'views'=>$views, 'non_innodb_tables'=>$nonInnodb,
        'columns'=>count($before['columns']), 'counts'=>$counts, 'assistant_setting'=>$setting==='1', 'read_only'=>true], null);
} catch (Throwable $error) {
    if ($pdo instanceof PDO && $pdo->inTransaction()) { try { $pdo->rollBack(); } catch (Throwable $ignored) {} }
    $allowed = ['REQUEST_INVALID','SQL_TARGET_INVALID','SQL_CA_INVALID','SQL_CREDENTIAL_INVALID','SQL_DRIVER_REQUIRED',
        'SQL_CONNECTION_FAILED','SQL_TLS_CONNECTION_FAILED','ACCOUNT_POLICY_REJECTED','AUDIT_UNAVAILABLE','UPGRADE_SCHEMA_LIMIT',
        'UPGRADE_SCHEMA_CHANGED','UPGRADE_SOURCE_VERSION_UNSUPPORTED','UPGRADE_REQUIRED_TABLES_MISSING','UPGRADE_NO_ACTIVE_ADMIN'];
    $code = $error->getMessage();
    $reply(false, null, in_array($code, $allowed, true) ? $code : 'UPGRADE_INVENTORY_FAILED');
}
