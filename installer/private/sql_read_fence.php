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
    $locker->exec('FLUSH TABLES WITH READ LOCK');$locked=true;
    rf_emit($id,0,'LOCK_HELD');unset($v);
    while(true) {
        $v=rf_line(1024,$deadline);++$sequence;
        bk_require($sequence<=64&&array_keys($v)===['operation','request_id','sequence']
            &&$v['request_id']===$id&&$v['sequence']===$sequence
            &&in_array($v['operation'],['check','release'],true),'FENCE_PROTOCOL');
        bk_require(bk_scalar($locker,'SELECT CONNECTION_ID()')===$connection,'FENCE_LOST');
        if($v['operation']==='release') {
            $locker->exec('UNLOCK TABLES');$locked=false;rf_emit($id,$sequence,'RELEASED');$code=0;break;
        }
        rf_emit($id,$sequence,'LOCK_HELD');
    }
} catch(Throwable $error) {
    // No PDO message, target or credential is returned, even on malformed input.
    try {rf_emit($id,$sequence,'FAILED');} catch(Throwable $ignored) {}
} finally {
    if($locked&&$locker!==null) {try {$locker->exec('UNLOCK TABLES');} catch(Throwable $ignored) {}}
    $locker=null;
}
exit($code);
