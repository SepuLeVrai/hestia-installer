<?php
declare(strict_types=1);
// Private bounded channel. Credentials arrive only over stdin; no arbitrary SQL.
define('HESTIA_BACKUP_FUNCTIONS_ONLY', true);
require_once __DIR__.'/backup_bridge.php';
require_once __DIR__.'/engine/includes/installation/connection.php';
require_once __DIR__.'/sql_accounts_policy.php';
function rf_line(int $maximum, float $deadline): array {
    $raw='';
    while (microtime(true)<$deadline) {
        $part=fgets(STDIN,$maximum+2-strlen($raw));
        if(is_string($part)) {
            $raw.=$part;bk_require(strlen($raw)<=$maximum+1,'FENCE_PROTOCOL');
            if(str_ends_with($raw,"\n")) {
                $body=substr($raw,0,-1);$v=json_decode($body,true,12,JSON_THROW_ON_ERROR);
                bk_require(is_array($v)&&bk_json($v)===$body,'FENCE_PROTOCOL');return $v;
            }
        }
        bk_require(!feof(STDIN),'FENCE_CLOSED');
        $read=[STDIN];$write=null;$except=null;
        bk_require(stream_select($read,$write,$except,0,100000)!==false,'FENCE_CHANNEL');
    }
    throw new RuntimeException('FENCE_TIMEOUT');
}
function rf_emit(string $id,int $sequence,string $state): void {
    $raw=bk_json(['request_id'=>$id,'sequence'=>$sequence,'state'=>$state])."\n";
    bk_require(fwrite(STDOUT,$raw)===strlen($raw)&&fflush(STDOUT),'FENCE_CHANNEL');
}
function rf_profile(PDO $db,string $name): void {
    $query=$db->prepare("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ('information_schema','performance_schema','mysql','sys') AND BINARY SCHEMA_NAME <> BINARY ? LIMIT 1");
    $query->execute([$name]);
    bk_require($query->fetch(PDO::FETCH_ASSOC)===false,'SQL_FENCE_SERVER_PROFILE_REJECTED');
    $rows=$db->query("SELECT cle, OCTET_LENGTH(valeur) AS bytes, LEFT(valeur,4097) AS value FROM App_Config WHERE cle IN ('security.ged_legacy_roots','HESTIA_MOBILE_RELEASE_DIR') LIMIT 3")->fetchAll(PDO::FETCH_ASSOC);
    bk_require(count($rows)<=2,'SQL_FENCE_STORAGE_PROFILE_REJECTED');$seen=[];
    foreach($rows as $row) {
        $key=$row['cle'];$value=$row['value'];
        bk_require(in_array($key,['security.ged_legacy_roots','HESTIA_MOBILE_RELEASE_DIR'],true)
            &&!isset($seen[$key])&&is_string($value)&&$row['bytes']!==null
            &&(int)$row['bytes']<=4096&&strlen($value)===(int)$row['bytes'], 'SQL_FENCE_STORAGE_PROFILE_REJECTED');
        $seen[$key]=true;
        if($key==='HESTIA_MOBILE_RELEASE_DIR') {
            bk_require($value==='','SQL_FENCE_STORAGE_PROFILE_REJECTED');continue;
        }
        bk_require(preg_match('/[\x00-\x1f\x7f]/',$value)===0,'SQL_FENCE_STORAGE_PROFILE_REJECTED');
        if(trim($value)==='') continue;
        $roots=explode(',',$value);bk_require(count($roots)<=16,'SQL_FENCE_STORAGE_PROFILE_REJECTED');
        foreach($roots as $root) {
            $root=str_replace('\\','/',trim($root));$parts=explode('/',$root);
            bk_require(preg_match('~^[A-Za-z0-9._/-]+$~D',$root)===1&&count($parts)<=64
                &&!array_intersect($parts,['','.','..']),'SQL_FENCE_STORAGE_PROFILE_REJECTED');
        }
    }
}
$locker=null;$locked=false;$id='';$sequence=0;$code=20;
try {
    bk_require(PHP_SAPI==='cli','FENCE_PROTOCOL');
    set_error_handler(static function():never {throw new RuntimeException('FENCE_FAILED');});
    stream_set_blocking(STDIN,false);
    $v=rf_line(16384,microtime(true)+12);
    bk_require(array_keys($v)===['authority','operation','request_id','target','version']
        &&$v['version']===1&&$v['operation']==='acquire'&&is_string($v['request_id'])
        &&preg_match('/^[a-f0-9]{32}$/D',$v['request_id'])===1,'FENCE_PROTOCOL');
    $id=$v['request_id'];$deadline=microtime(true)+180;
    $locker=bk_connection($v['target'],$v['authority']);
    bk_authority($locker,$v['authority'],$v['target']['tls_required']);
    bk_require(bk_identity($locker)['db']===$v['target']['name'],'FENCE_TARGET');
    $locker->exec('SET SESSION lock_wait_timeout=5');
    $locker->exec('SET SESSION max_statement_time=10');
    $connection=bk_scalar($locker,'SELECT CONNECTION_ID()');
    $name=$v['target']['name'];rf_profile($locker,$name);
    $locker->exec('FLUSH TABLES WITH READ LOCK');$locked=true;
    rf_profile($locker,$name);
    rf_emit($id,0,'LOCK_HELD');unset($v);
    while(true) {
        $v=rf_line(1024,$deadline);++$sequence;
        bk_require($sequence<=64&&array_keys($v)===['operation','request_id','sequence']
            &&$v['request_id']===$id&&$v['sequence']===$sequence
            &&in_array($v['operation'],['check','release'],true),'FENCE_PROTOCOL');
        bk_require(bk_scalar($locker,'SELECT CONNECTION_ID()')===$connection,'FENCE_LOST');
        rf_profile($locker,$name);
        if($v['operation']==='release') {
            $locker->exec('UNLOCK TABLES');$locked=false;rf_emit($id,$sequence,'RELEASED');$code=0;break;
        }
        rf_emit($id,$sequence,'LOCK_HELD');
    }
} catch(Throwable $error) {
    // No PDO message, target or credential is returned, even on malformed input.
    $state=in_array($error->getMessage(),['SQL_FENCE_SERVER_PROFILE_REJECTED','SQL_FENCE_STORAGE_PROFILE_REJECTED'],true)
        ?$error->getMessage():'FAILED';
    try {rf_emit($id,$sequence,$state);} catch(Throwable $ignored) {}
} finally {
    if($locked&&$locker!==null) {try {$locker->exec('UNLOCK TABLES');} catch(Throwable $ignored) {}}
    $locker=null;
}
exit($code);
