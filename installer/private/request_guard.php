<?php
declare(strict_types=1);
// Installed as root-owned auto_prepend_file in the dedicated PHP service.
// Application code cannot remove the gate or replace its inode/lock.
(static function (): void {
    $root=hex2bin('__MAINTENANCE_ROOT_HEX__');
    $gid=__MAINTENANCE_GID__;
    $deny=static function (): never {
        if(PHP_SAPI!=='cli') {
            http_response_code(503);
            header('Retry-After: 60');header('Cache-Control: no-store');
            header('Content-Type: text/plain; charset=utf-8');
            echo "Maintenance en cours.\n";
        }
        exit(75);
    };
    $valid=static function(mixed $info,int $type,int $mode) use($gid): bool {
        return is_array($info)&&($info['mode']&0170000)===$type&&($info['mode']&07777)===$mode
            &&$info['uid']===0&&$info['gid']===$gid;
    };
    $directory=@lstat($root);
    if(!$valid($directory,0040000,0750))$deny();
    // Presence is enough to refuse, including an incomplete/corrupt attempt.
    $gated=static function() use($root): bool {clearstatcache(true,$root.'/maintenance.attempt');return @lstat($root.'/maintenance.attempt')!==false;};
    if($gated())$deny();
    $named=@lstat($root.'/activity.lock');$lock=@fopen($root.'/activity.lock','rb');
    if(!is_resource($lock))$deny();
    $info=fstat($lock);
    if(!$valid($named,0100000,0640)||!$valid($info,0100000,0640)||$info['size']!==0||$info['nlink']!==1
        ||$info['ino']!==$named['ino']||$info['dev']!==$named['dev']
        ||!flock($lock,LOCK_SH|LOCK_NB)||$gated()) {fclose($lock);$deny();}
    // Keep the shared lock through response/session shutdown, not just bootstrap.
    register_shutdown_function(static function() use($lock): void {
        if(session_status()===PHP_SESSION_ACTIVE)session_write_close();
        // Do not fclose here: later application shutdown callbacks can still
        // write. PHP closes the resource only when request execution fully ends.
    });
    $GLOBALS['hestia_private_request_barrier']=$lock;
})();
