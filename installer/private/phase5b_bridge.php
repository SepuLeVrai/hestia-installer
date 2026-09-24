<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
set_error_handler(static function (): never { throw new RuntimeException('PRIVATE_BRIDGE_FAILED'); });
define('HESTIA_PRIVATE_CHECK_ROOT', __DIR__);
require_once __DIR__ . '/engine/includes/installation/connection.php';
require_once __DIR__ . '/engine/includes/installation/fresh.php';
require_once __DIR__ . '/engine/includes/installation/finalize.php';
require_once __DIR__ . '/phase5b_policy.php';

function b5_keys(array $data, array $expected): void
{
    $keys = array_keys($data); sort($keys); sort($expected);
    if ($keys !== $expected) throw new RuntimeException('REQUEST_INVALID');
}
function b5_credential(#[SensitiveParameter] array $value, bool $authority = false): void
{
    b5_keys($value,['user','password']);
    if (!is_string($value['user']) || !preg_match('/^[A-Za-z0-9_]{1,32}$/D',$value['user'])
        || (!$authority && strtolower($value['user']) === 'root') || !is_string($value['password'])
        || strlen($value['password']) < 1 || strlen($value['password']) > 1024
        || preg_match('//u',$value['password']) !== 1 || preg_match('/\p{C}/u',$value['password'])) throw new RuntimeException('REQUEST_INVALID');
}
function b5_connect(array $db, #[SensitiveParameter] array $credential, bool $select = true): PDO
{
    return hestia_database_connect($db,$credential['user'],$credential['password'],$select);
}
function b5_run(#[SensitiveParameter] array $r): array
{
    b5_keys($r,['version','request_id','operation','confirmed','database','application','migration','authority','administrator','assistant_enabled']);
    if ($r['version'] !== 2 || !is_string($r['request_id']) || !preg_match('/^[a-f0-9]{32}$/D',$r['request_id'])
        || !in_array($r['operation'],['preflight','fresh','release'],true) || !is_bool($r['assistant_enabled'])
        || $r['confirmed'] !== ($r['operation'] !== 'preflight')) throw new RuntimeException('REQUEST_INVALID');
    $db = hestia_database_endpoint($r['database']);
    b5_credential($r['application']); b5_credential($r['migration']); b5_credential($r['authority'],true);
    $a = $r['application']; $m = $r['migration']; $c = $r['authority'];
    if (count(array_unique([$a['user'],$m['user'],$c['user']])) !== 3
        || count(array_unique([$a['password'],$m['password'],$c['password']])) !== 3) throw new RuntimeException('ACCOUNT_SEPARATION_REQUIRED');
    $admin = hestia_install_validate_administrator($r['administrator']);
    hestia_validate_runtime_dependencies();
    $server = b5_connect($db,$c,false); b5_server($server);
    if (!str_starts_with((string)$server->query('SELECT CURRENT_USER()')->fetchColumn(), $c['user'] . '@')) throw new RuntimeException('ACCOUNT_SEPARATION_REQUIRED');
    $lock = 'hestia.5b.' . substr(hash('sha256', strtolower($db['name'])),0,48);
    $q = $server->prepare('SELECT GET_LOCK(?,0)');$q->execute([$lock]);
    if ((int)$q->fetchColumn() !== 1) throw new RuntimeException('INSTALLATION_BUSY');
    $mutated = false;
    try {
        if ($r['operation'] === 'release') {
            if ($db['mode'] !== 'managed') throw new RuntimeException('REQUEST_INVALID');
            $pdo = b5_connect($db,$m); b5_audit($server,$pdo,$db,$m['user'],'provisioning');
            $app = b5_connect($db,$a); b5_audit($server,$app,$db,$a['user'],'application');
            hestia_install_verify_fresh($app,$admin,$r['assistant_enabled']);
            $host = substr((string)$pdo->query('SELECT CURRENT_USER()')->fetchColumn(),strlen($m['user'])+1);
            $pdo = null; $mutated = true;
            $server->exec('DROP USER ' . $server->quote($m['user']) . '@' . $server->quote($host));
            if (b5_rows($server,'SELECT Host FROM mysql.global_priv WHERE User=?',[$m['user']])) throw new RuntimeException('TEMPORARY_ACCOUNT_REMAINS');
            return ['state'=>'TEMPORARY_ACCOUNT_RELEASED'];
        }
        if ($db['mode'] === 'managed') {
            b5_managed_authority($server);
            b5_free($server,$db,$a,$m);
            // Validate the canonical schema and administrator BEFORE any managed DDL.
            $schema = file_get_contents(__DIR__ . '/engine/sql/schema.sql');
            if (!is_string($schema) || !hestia_split_sql(hestia_normalize_sql_dump($schema))) throw new RuntimeException('SCHEMA_INVALID');
            if ($r['operation'] === 'preflight') return ['state'=>'TARGET_VERIFIED'];
            $peer = substr((string)$server->query('SELECT USER()')->fetchColumn(),strlen($c['user'])+1);
            if (!b5_peer($peer)) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
            $mutated = true;
            $server->exec('CREATE DATABASE `' . $db['name'] . '` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci');
            foreach ([$a,$m] as $credential) {
                // A native password hash avoids mode-dependent SQL quoting of a secret.
                $hash = '*' . strtoupper(sha1(sha1($credential['password'],true)));
                $server->exec('CREATE USER ' . $server->quote($credential['user']) . '@' . $server->quote($peer) . ' IDENTIFIED BY PASSWORD ' . $server->quote($hash));
            }
            $scope = '`' . str_replace('_','\\_',$db['name']) . '`.*';
            $server->exec('GRANT SELECT, INSERT, UPDATE, DELETE ON ' . $scope . ' TO ' . $server->quote($a['user']) . '@' . $server->quote($peer));
            $server->exec('GRANT ALL PRIVILEGES ON ' . $scope . ' TO ' . $server->quote($m['user']) . '@' . $server->quote($peer));
        }
        $migration = b5_connect($db,$m); $application = b5_connect($db,$a);
        b5_audit($server,$migration,$db,$m['user'],'provisioning'); b5_audit($server,$application,$db,$a['user'],'application');
        // The audited schema-scoped migration connection sees ALL objects.
        b5_empty($migration,$db['name']);
        if ($r['operation'] === 'preflight') return ['state'=>'TARGET_VERIFIED'];
        $mutated = true;
        hestia_install_fresh_database($migration,$admin,true);
        hestia_install_verify_fresh($application,$admin,false);
        hestia_install_assistant_state($application,$r['assistant_enabled'],$r['assistant_enabled']);
        hestia_install_verify_fresh($application,$admin,$r['assistant_enabled']);
        // Recheck effective metadata and grants immediately after all SQL effects.
        b5_audit($server,$application,$db,$a['user'],'application');
        return ['state'=>'DATABASE_CONFIGURED'];
    } catch (Throwable $e) {
        if ($mutated) throw new RuntimeException('SQL_MANUAL_ACTION');
        throw $e;
    } finally {
        $q = $server->prepare('SELECT RELEASE_LOCK(?)');$q->execute([$lock]);
        if ((int)$q->fetchColumn() !== 1) throw new RuntimeException('SQL_MANUAL_ACTION');
    }
}
$id = null; $operation = null; $ok = false; $result = null; $error = 'REQUEST_INVALID';
try {
    $raw = stream_get_contents(STDIN,16385);
    if (!is_string($raw) || strlen($raw)>16384) throw new RuntimeException('REQUEST_INVALID');
    $obj = json_decode($raw,false,16,JSON_THROW_ON_ERROR);
    if (!$obj instanceof stdClass || json_encode($obj,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR) !== $raw) throw new RuntimeException('REQUEST_INVALID');
    $r = json_decode($raw,true,16,JSON_THROW_ON_ERROR);
    if (is_string($r['request_id']??null) && preg_match('/^[a-f0-9]{32}$/D',$r['request_id'])) $id=$r['request_id'];
    if (in_array($r['operation']??null,['preflight','fresh','release'],true)) $operation=$r['operation'];
    $result=b5_run($r);$ok=true;$error=null;
} catch (Throwable $e) {
    $allowed=['REQUEST_INVALID','ACCOUNT_SEPARATION_REQUIRED','ACCOUNT_POLICY_REJECTED','ACCOUNT_TARGET_OCCUPIED',
        'DATABASE_TARGET_OCCUPIED','DATABASE_NOT_EMPTY','DATABASE_CONNECTION_FAILED','DATABASE_TLS_REQUIRED',
        'DATABASE_TLS_UNAVAILABLE','SERVER_PROFILE_UNSUPPORTED','TARGET_IDENTITY_MISMATCH','INSTALLATION_BUSY',
        'ADMIN_INPUT_INVALID','SQL_MANUAL_ACTION','SCHEMA_INVALID','AUTHORITY_PRIVILEGES_REQUIRED'];
    $error=in_array($e->getMessage(),$allowed,true)?$e->getMessage():'PRIVATE_BRIDGE_FAILED';
}
echo json_encode(['version'=>2,'request_id'=>$id,'operation'=>$operation,'ok'=>$ok,'result'=>$result,'error'=>$error],JSON_THROW_ON_ERROR);
exit($ok?0:20);
