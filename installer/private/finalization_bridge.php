<?php
declare(strict_types=1);
// Private, non-root, bounded stdin protocol. No Web bootstrap or secret PHP.
$operation = 'invalid'; $requestId = '';
$reply = static function (bool $ok, ?array $result, ?string $error) use (&$operation, &$requestId): never {
    echo json_encode(['version'=>1,'operation'=>$operation,'request_id'=>$requestId,
        'ok'=>$ok,'result'=>$result,'error'=>$error], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    exit($ok ? 0 : 20);
};
try {
    if (PHP_SAPI !== 'cli') throw new RuntimeException('REQUEST_INVALID');
    $raw = stream_get_contents(STDIN, 16385);
    if (!is_string($raw) || strlen($raw)>16384) throw new RuntimeException('REQUEST_INVALID');
    $request = json_decode($raw, true, 12, JSON_THROW_ON_ERROR);
    if (!is_array($request) || array_keys($request)!==['administrator','application','desired_enabled','operation','request_id','target','version']
        || json_encode($request, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR)!==$raw
        || $request['version']!==1 || !is_string($request['request_id'])
        || !preg_match('/^[a-f0-9]{32}$/D', $request['request_id'])
        || !in_array($request['operation'], ['verify','set_assistant'], true)
        || !is_bool($request['desired_enabled']) || !is_array($request['application'])
        || array_keys($request['application'])!==['password','user']) throw new RuntimeException('REQUEST_INVALID');
    $operation=$request['operation']; $requestId=$request['request_id'];
    require_once __DIR__.'/sql_accounts_policy.php';
    require_once __DIR__.'/engine/includes/installation/connection.php';
    require_once __DIR__.'/engine/includes/installation/finalization.php';
    $pdo=hestia_sql_connect($request['target'], $request['application']['user'], $request['application']['password']);
    $identity=$pdo->query('SELECT VERSION() AS version, DATABASE() AS db, CURRENT_USER() AS account, CURRENT_ROLE() AS role')->fetch(PDO::FETCH_ASSOC);
    if (!str_contains($identity['version'], 'MariaDB') || $identity['db']!==$request['target']['name']
        || !hestia_account_policy(hestia_bounded_grants($pdo,false),hestia_bounded_grants($pdo,true),
            $identity['account'],$identity['role'],$request['application']['user'],$request['target']['name'],
            'application',$request['target']['tls_required'])) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    $enabled=hestia_installation_verify_ready($pdo, $request['administrator'], true);
    if ($operation==='set_assistant') {
        hestia_installation_set_assistant($pdo, $request['desired_enabled']);
        $enabled=hestia_installation_verify_ready($pdo, $request['administrator'], true);
        if ($enabled!==$request['desired_enabled']) throw new RuntimeException('FINALIZATION_SETTING_FAILED');
    }
    $reply(true, ['database_verified'=>true, 'assistant_enabled'=>$enabled], null);
} catch (Throwable $error) {
    $code=$error->getMessage();
    $allowed=['FINALIZATION_DATABASE_INVALID','FINALIZATION_SETTING_FAILED','REQUEST_INVALID',
        'SQL_TARGET_INVALID','SQL_CA_INVALID','SQL_CREDENTIAL_INVALID','SQL_DRIVER_REQUIRED',
        'SQL_CONNECTION_FAILED','SQL_TLS_CONNECTION_FAILED','ACCOUNT_POLICY_REJECTED','AUDIT_UNAVAILABLE'];
    $reply(false, null, in_array($code,$allowed,true) ? $code : 'FINALIZATION_CHECK_FAILED');
}
