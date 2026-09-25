<?php
declare(strict_types=1);
// Executed only by the privileged host controller after code and path checks,
// as the configured Web uid/gid, with empty environment and bounded pipes.
$operation='invalid';$requestId='';
$reply=static function(bool $ok,?array $result,?string $error) use (&$operation,&$requestId):never {
    echo json_encode(['version'=>1,'operation'=>$operation,'request_id'=>$requestId,
        'ok'=>$ok,'result'=>$result,'error'=>$error],JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
    exit($ok?0:20);
};
try {
    if(PHP_SAPI!=='cli')throw new RuntimeException();
    $raw=stream_get_contents(STDIN,16385);
    if(!is_string($raw)||strlen($raw)>16384)throw new RuntimeException();
    $v=json_decode($raw,true,12,JSON_THROW_ON_ERROR);
    if(!is_array($v)||array_keys($v)!==['action','directory','gid','key','operation','request_id','root','version']
        ||json_encode($v,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)!==$raw
        ||$v['version']!==1||!is_int($v['gid'])||$v['gid']<1
        ||!is_string($v['request_id'])||!preg_match('/^[a-f0-9]{32}$/D',$v['request_id'])
        ||!in_array($v['operation'],['prepared','active','settings'],true)
        ||!in_array($v['action'],['preserve','configure','disabled'],true)
        ||!is_string($v['key']))throw new RuntimeException();
    $requestId=$v['request_id'];$operation=$v['operation'];
    define('APP_ROOT',$v['root']);
    if($operation==='prepared') {
        require $v['directory'].'/db.php';
        define('HESTIA_ASSISTANT_MANAGED_FILE',$v['directory'].'/assistant.json');
        define('HESTIA_ASSISTANT_MANAGED_GID',$v['gid']);
    } else {
        require $v['root'].'/includes/db.php';
    }
    require_once $v['root'].'/includes/functions.php';
    require_once $v['root'].'/includes/installation/finalization.php';
    require_once $v['root'].'/includes/assistant/config.php';
    require_once __DIR__.'/sql_accounts_policy.php';
    $pdo=get_pdo();
    $identity=$pdo->query('SELECT VERSION() AS version, DATABASE() AS db, CURRENT_USER() AS account, CURRENT_ROLE() AS role')->fetch(PDO::FETCH_ASSOC);
    if(!str_contains($identity['version'],'MariaDB')||$identity['db']!==DB_NAME
        ||!hestia_account_policy(hestia_bounded_grants($pdo,false),hestia_bounded_grants($pdo,true),
            $identity['account'],$identity['role'],DB_USER,DB_NAME,'application',DB_TLS_REQUIRED))throw new RuntimeException();
    if($pdo->query("SELECT valeur FROM App_Config WHERE cle='APP_VERSION'")->fetchColumn()!==HESTIA_INSTALL_VERSION_KEY)throw new RuntimeException();
    $setting=$pdo->query("SELECT valeur FROM App_Config WHERE cle='assistant.enabled'")->fetchColumn();
    if(!in_array($setting,['0','1'],true))throw new RuntimeException();
    if($operation==='settings') {
        if($v['action']==='configure') {
            if($v['key']===''||!hestia_assistant_api_key_is_valid($v['key']))throw new RuntimeException();
            // Disable first: interrupted replacement cannot leave an enabled
            // service pointing at an incomplete/unverified key store.
            hestia_installation_set_assistant($pdo,false);
            hestia_assistant_managed_save($v['key'],false);
            if(!hash_equals($v['key'],hestia_assistant_managed_key()))throw new RuntimeException();
            hestia_installation_set_assistant($pdo,true);
        } elseif($v['action']==='disabled') {
            hestia_installation_set_assistant($pdo,false); // Preserve durable key.
        }
    }
    $setting=$pdo->query("SELECT valeur FROM App_Config WHERE cle='assistant.enabled'")->fetchColumn();
    $configured=hestia_assistant_managed_key()!=='';
    $reply(true,['database_verified'=>true,'setting_enabled'=>$setting==='1',
        'key_configured'=>$configured,'assistant_enabled'=>$setting==='1'&&$configured,'api_access'=>'NOT_TESTED'],null);
} catch(Throwable $error) {
    $reply(false,null,'FINALIZATION_RUNTIME_FAILED');
}
