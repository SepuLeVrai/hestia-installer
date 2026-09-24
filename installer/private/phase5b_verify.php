<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
set_error_handler(static function (): never { throw new RuntimeException('RUNTIME_VERIFICATION_FAILED'); });
$id=null; $ok=false; $error='RUNTIME_VERIFICATION_FAILED'; $result=null;
try {
    $raw=stream_get_contents(STDIN,16385);
    if (!is_string($raw) || strlen($raw)>16384) throw new RuntimeException('INVALID');
    $o=json_decode($raw,false,12,JSON_THROW_ON_ERROR);
    if (!$o instanceof stdClass || json_encode($o,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)!==$raw) throw new RuntimeException('INVALID');
    $r=json_decode($raw,true,12,JSON_THROW_ON_ERROR);$keys=array_keys($r);sort($keys);
    if ($keys!==['administrator','assistant_enabled','configroot','operation','request_id','version','webroot'] || $r['version']!==2
        || !in_array($r['operation'],['verify','assistant_observe','assistant_set'],true)
        || !is_bool($r['assistant_enabled']) || !is_string($r['request_id']) || !preg_match('/^[a-f0-9]{32}$/D',$r['request_id'])
        || !is_string($r['webroot']) || $r['webroot']!==realpath($r['webroot'])) throw new RuntimeException('INVALID');
    $id=$r['request_id'];
    if (!is_string($r['configroot']) || $r['configroot']!==realpath($r['configroot'])) throw new RuntimeException('INVALID');
    define('HESTIA_PRIVATE_CHECK_ROOT',$r['configroot']);
    define('APP_ROOT',$r['webroot']);
    require APP_ROOT . '/includes/db.php';
    require_once APP_ROOT . '/includes/functions.php';
    require_once APP_ROOT . '/includes/installation/finalize.php';
    if (!defined('HESTIA_MANAGED_ASSISTANT_KEY') || !defined('HESTIA_DATABASE_ENDPOINT')) throw new RuntimeException('INVALID');
    $pdo=get_pdo();
    if ($r['operation']==='assistant_set') hestia_install_assistant_state($pdo,$r['assistant_enabled'],HESTIA_MANAGED_ASSISTANT_KEY!=='');
    $actual=$pdo->query("SELECT valeur FROM App_Config WHERE cle='assistant.enabled'")->fetchColumn();
    if (!in_array($actual,['0','1'],true)) throw new RuntimeException('INVALID');
    if ($r['operation']==='verify') {
        if (!is_array($r['administrator'])) throw new RuntimeException('INVALID');
        hestia_install_verify_fresh($pdo,$r['administrator'],$r['assistant_enabled']);
    } elseif ($r['administrator']!==null) throw new RuntimeException('INVALID');
    if ($pdo->query("SELECT valeur FROM App_Config WHERE cle='APP_VERSION'")->fetchColumn()!==HESTIA_INSTALL_VERSION_KEY) throw new RuntimeException('INVALID');
    $status=hestia_assistant_runtime_status();
    if ($status['enabled']!==($actual==='1') || ($actual==='1' && !$status['api_key_configured'])) throw new RuntimeException('INVALID');
    $result=['state'=>'RUNTIME_VERIFIED','assistant_enabled'=>$status['enabled'],
        'key_configured'=>$status['api_key_configured'],'api_access_tested'=>false];
    $ok=true;$error=null;
} catch (Throwable $e) { }
echo json_encode(['version'=>2,'request_id'=>$id,'operation'=>'runtime','ok'=>$ok,'result'=>$result,'error'=>$error],JSON_THROW_ON_ERROR);
exit($ok?0:20);
