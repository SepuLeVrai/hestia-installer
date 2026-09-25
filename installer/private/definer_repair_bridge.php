<?php
declare(strict_types=1);

function hdr_require(bool $ok,string $code='REPAIR_PROFILE_REJECTED'): void {
    if(!$ok)throw new RuntimeException($code);
}
function hdr_snapshot(PDO $pdo,array $triggers,array $alternate=[]): array {
    bk_session($pdo);
    $pdo->exec('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ');
    $pdo->exec('START TRANSACTION READ ONLY, WITH CONSISTENT SNAPSHOT');
    try {
        $tables=bk_profile($pdo);$summary=[];$total=0;
        foreach($tables as $table) {
            $row=bk_table($pdo,$table,null);$summary[]=$row;$total+=(int)$row['rows'];
            hdr_require($total<=BK_MAX_ROWS,'BACKUP_LIMIT');
        }
        hdr_require(bk_profile($pdo)===$tables,'BACKUP_SOURCE_CHANGED');
        $result=['logical_sha256'=>hash('sha256',bk_json([$summary,$triggers])),'tables'=>count($tables),'rows'=>(string)$total];
        if($alternate!==[])$result['alternate_logical_sha256']=hash('sha256',bk_json([$summary,$alternate]));
        return $result;
    } finally {if($pdo->inTransaction())$pdo->rollBack();}
}
function hdr_repair(#[SensitiveParameter] array $v,bool &$mutated): array {
    hdr_require(array_keys($v)===['application','authority','expected_logical_sha256','expected_orphaned_definer','operation','request_id','target','version']
        &&$v['version']===1&&$v['operation']==='repair_definers'
        &&is_string($v['expected_logical_sha256'])&&preg_match('/^[a-f0-9]{64}$/D',$v['expected_logical_sha256'])===1,'REQUEST_INVALID');
    $target=hestia_sql_connection_target($v['target']);
    hdr_require($target['host']==='127.0.0.1'&&$target['port']===3306&&!$target['tls_required']);
    foreach(['application','authority'] as $name) {
        hdr_require(is_array($v[$name])&&array_keys($v[$name])===['password','user'],'REQUEST_INVALID');
    }
    hdr_require($v['application']['user']!==$v['authority']['user']
        &&$v['application']['password']!==$v['authority']['password'],'ACCOUNT_SEPARATION_REQUIRED');
    $pdo=bk_connection($target,$v['authority']);bk_authority($pdo,$v['authority'],false);bk_session($pdo);
    $app=hestia_sql_connect($target,$v['application']['user'],$v['application']['password']);
    $identity=bk_identity($app);
    hdr_require(hestia_account_policy(hestia_bounded_grants($app,false),hestia_bounded_grants($app,true),
        $identity['account'],$identity['role'],$v['application']['user'],$target['name'],'application'),
        'ACCOUNT_POLICY_REJECTED');
    $appId=(int)bk_scalar($app,'SELECT CONNECTION_ID()');
    // The cooperative barrier must have drained every managed PHP/CLI writer.
    $q=$pdo->prepare('SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE USER=? AND ID<>?');
    $q->execute([$v['application']['user'],$appId]);$others=(int)$q->fetchColumn();$q->closeCursor();
    hdr_require($others===0,'REPAIR_WRITERS_ACTIVE');
    $lock='hestia.provision.'.substr(hash('sha256',strtolower($target['name'])),0,46);
    $q=$pdo->prepare('SELECT GET_LOCK(?,0)');$q->execute([$lock]);$locked=(int)$q->fetchColumn()===1;$q->closeCursor();
    hdr_require($locked,'REPAIR_BUSY');
    try {
        $before=bk_orphaned_triggers($pdo,$v['expected_orphaned_definer']);
        $user=hdf_user($target['name']);
        hdr_require(!in_array($user,[$v['application']['user'],$v['authority']['user']],true),'ACCOUNT_SEPARATION_REQUIRED');
        hdf_absent($pdo,$user);
        $snapshot=hdr_snapshot($pdo,$before);
        hdr_require(hash_equals($v['expected_logical_sha256'],$snapshot['logical_sha256']),'REPAIR_SOURCE_CHANGED');
        $old=explode('@',$v['expected_orphaned_definer'])[0];
        $mutated=true;
        hdf_rebind_fresh($pdo,$target['name'],$old);
        $actual=bk_triggers($pdo);$after=$actual;
        foreach($after as &$row)$row['DEFINER']=$v['expected_orphaned_definer'];
        unset($row);
        hdr_require($before===$after,'REPAIR_SOURCE_CHANGED');
        $afterSnapshot=hdr_snapshot($pdo,$after,$actual);$reboundHash=$afterSnapshot['alternate_logical_sha256'];
        unset($afterSnapshot['alternate_logical_sha256']);
        hdr_require($afterSnapshot===$snapshot,'REPAIR_SOURCE_CHANGED');
        $q=$pdo->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User=?');$q->execute([$old]);
        hdr_require((int)$q->fetchColumn()===0,'REPAIR_SOURCE_CHANGED');$q->closeCursor();
        // No source smoke inserts: they could advance AUTO_INCREMENT despite
        // rollback. The caller must obtain a new actual isolated restore proof.
        return ['state'=>'DEFINERS_REBOUND','triggers'=>5,'business_rows_written'=>false,
            'source_snapshot_preserved'=>true,'rebound_logical_sha256'=>$reboundHash,...$snapshot];
    } finally {
        $q=$pdo->prepare('SELECT RELEASE_LOCK(?)');$q->execute([$lock]);$q->closeCursor();
    }
}
$id='';$mutated=false;
try {
    if(PHP_SAPI!=='cli')throw new RuntimeException('REQUEST_INVALID');
    set_error_handler(static function():never {throw new RuntimeException('REPAIR_WORKER_FAILED');});
    require_once __DIR__.'/engine/includes/installation/connection.php';
    require_once __DIR__.'/engine/includes/installation/finalization.php';
    require_once __DIR__.'/sql_accounts_policy.php';
    require_once __DIR__.'/trigger_definer.php';
    define('HESTIA_BACKUP_FUNCTIONS_ONLY',true);
    require_once __DIR__.'/backup_bridge.php';
    $raw=stream_get_contents(STDIN,16385);hdr_require(is_string($raw)&&strlen($raw)<=16384,'REQUEST_INVALID');
    $v=json_decode($raw,true,12,JSON_THROW_ON_ERROR);hdr_require(is_array($v)&&bk_json($v)===$raw,'REQUEST_INVALID');
    hdr_require(isset($v['request_id'])&&is_string($v['request_id'])&&preg_match('/^[a-f0-9]{32}$/D',$v['request_id'])===1,'REQUEST_INVALID');
    $id=$v['request_id'];$result=hdr_repair($v,$mutated);
    echo bk_json(['version'=>1,'request_id'=>$id,'ok'=>true,'uncertain'=>false,'result'=>$result,'error'=>null]);
} catch(Throwable $error) {
    $known=['REQUEST_INVALID','ACCOUNT_SEPARATION_REQUIRED','ACCOUNT_POLICY_REJECTED','AUDIT_UNAVAILABLE',
        'REPAIR_PROFILE_REJECTED','REPAIR_WRITERS_ACTIVE','REPAIR_BUSY','REPAIR_SOURCE_CHANGED','REPAIR_WORKER_FAILED',
        'BACKUP_AUTHORITY_REJECTED','BACKUP_RESCUE_PROFILE_REJECTED','BACKUP_TRIGGER_PROFILE_REJECTED',
        'BACKUP_PROFILE_REJECTED','BACKUP_SOURCE_CHANGED','BACKUP_LIMIT','BACKUP_SPECIAL_OBJECTS_UNSUPPORTED',
        'DEFINER_ACCOUNT_OCCUPIED','DEFINER_PROFILE_REJECTED','DEFINER_TRIGGER_PROFILE_REJECTED','DEFINER_REBIND_FAILED'];
    $code=in_array($error->getMessage(),$known,true)?$error->getMessage():'REPAIR_WORKER_FAILED';
    echo json_encode(['version'=>1,'request_id'=>$id,'ok'=>false,'uncertain'=>$mutated,'result'=>null,'error'=>$code],JSON_THROW_ON_ERROR);
    exit(20);
}
