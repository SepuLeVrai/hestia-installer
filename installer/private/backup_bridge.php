<?php
declare(strict_types=1);
// 5C2 private CLI: binary-safe logical snapshot and socket-only restore proof.
// No include from a deployed Web root, no client command or arbitrary SQL input.
const BK_MAX_LINE = 8388608;
const BK_MAX_BYTES = 134217728;
const BK_MAX_ROWS = 1000000;
function bk_require(bool $condition, string $code = 'BACKUP_PROFILE_REJECTED'): void {
    if (!$condition) throw new RuntimeException($code);
}
function bk_json(mixed $v): string { return json_encode($v, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR); }
function bk_ident(mixed $name): string {
    bk_require(is_string($name) && preg_match('/^[A-Za-z0-9_]{1,64}$/D', $name) === 1);
    return '`'.$name.'`';
}
function bk_connection(array $target, #[SensitiveParameter] array $credential): PDO {
    bk_require(array_keys($credential) === ['password','user'], 'REQUEST_INVALID');
    return hestia_sql_connect($target, $credential['user'], $credential['password'], true);
}
function bk_identity(PDO $pdo): array {
    $r = $pdo->query('SELECT VERSION() AS version, @@hostname AS host, @@port AS port, @@datadir AS dir, @@server_id AS id, CURRENT_USER() AS account, CURRENT_ROLE() AS role, DATABASE() AS db')->fetch();
    bk_require(is_array($r) && preg_match('/^(10\.11|11\.4|11\.8)\.[0-9]+[^\r\n]*MariaDB/', $r['version']) === 1);
    return $r;
}
function bk_authority(PDO $pdo, #[SensitiveParameter] array $credential, bool $tls): void {
    $r = bk_identity($pdo); $user = $credential['user'];
    bk_require($r['role'] === null && str_starts_with($r['account'], $user.'@'), 'BACKUP_AUTHORITY_REJECTED');
    $host = substr($r['account'], strlen($user)+1);
    bk_require($host === 'localhost' || filter_var($host,FILTER_VALIDATE_IP) !== false, 'BACKUP_AUTHORITY_REJECTED');
    $identity = '(?:`'.preg_quote($user,'/').'`@`'.preg_quote($host,'/').'`|\''.preg_quote($user,'/').'\'@\''.preg_quote($host,'/').'\')';
    $grants = hestia_bounded_grants($pdo,false);
    bk_require(count($grants) === 1 && preg_match('/^GRANT ALL PRIVILEGES ON \*\.\* TO '.$identity
        .'(?: IDENTIFIED BY PASSWORD \'\*[A-F0-9]{40}\'| IDENTIFIED VIA mysql_native_password USING \'\*[A-F0-9]{40}\')'
        .($tls ? ' REQUIRE SSL' : '').' WITH GRANT OPTION$/D',$grants[0]) === 1,'BACKUP_AUTHORITY_REJECTED');
    foreach (hestia_bounded_grants($pdo,true) as $grant) {
        bk_require(preg_match('/^GRANT USAGE ON \*\.\* TO (?:PUBLIC|`PUBLIC`)$/D',$grant) === 1,'BACKUP_AUTHORITY_REJECTED');
    }
}
function bk_session(PDO $pdo): void {
    $pdo->exec("SET SESSION time_zone='+00:00'");
    $pdo->exec("SET SESSION sql_mode='NO_AUTO_VALUE_ON_ZERO'");
    $pdo->exec('SET SESSION sql_quote_show_create=1');
    $pdo->exec('SET SESSION max_statement_time=20');
    $pdo->exec('SET SESSION lock_wait_timeout=5');
    $pdo->setAttribute(PDO::MYSQL_ATTR_USE_BUFFERED_QUERY,false);
}
function bk_all(PDO $pdo,string $sql,int $max): array {
    $q=$pdo->query($sql);$rows=[];$bytes=0;
    while(($row=$q->fetch(PDO::FETCH_ASSOC))!==false) {
        $bytes+=strlen(bk_json($row));bk_require(count($rows)<$max && $bytes<BK_MAX_LINE,'BACKUP_LIMIT');$rows[]=$row;
    }
    $q->closeCursor();return $rows;
}
function bk_scalar(PDO $pdo,string $sql): mixed {
    $q=$pdo->query($sql);$v=$q->fetchColumn();$q->closeCursor();return $v;
}
function bk_canonical_triggers(): array {
    $expected=[];
    foreach(hestia_split_sql(hestia_normalize_sql_dump(file_get_contents(__DIR__.'/engine/sql/schema.sql'))) as $statement) {
        if(preg_match('/^CREATE TRIGGER `([A-Za-z0-9_]+)` (BEFORE|AFTER) (INSERT|UPDATE|DELETE) ON `([A-Za-z0-9_]+)`\s+FOR EACH ROW\s+(BEGIN.*END)$/sD',$statement,$m)===1)
            $expected[$m[1]]=['timing'=>$m[2],'event'=>$m[3],'table'=>$m[4],'body'=>$m[5],'statement'=>$statement];
    }
    bk_require(count($expected)===5,'BACKUP_TRIGGER_PROFILE_REJECTED');ksort($expected);return $expected;
}
function bk_triggers(PDO $pdo,bool $auditDefiners=true): array {
    $rows=bk_all($pdo,'SELECT TRIGGER_NAME,EVENT_MANIPULATION,EVENT_OBJECT_TABLE,ACTION_TIMING,ACTION_STATEMENT,ACTION_ORDER,DEFINER,SQL_MODE,CHARACTER_SET_CLIENT,COLLATION_CONNECTION,DATABASE_COLLATION FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() ORDER BY BINARY TRIGGER_NAME LIMIT 6',5);
    $canonical=bk_canonical_triggers();bk_require(count($rows)===count($canonical),'BACKUP_TRIGGER_PROFILE_REJECTED');
    $database=bk_scalar($pdo,'SELECT DATABASE()');
    foreach($rows as $r) {
        $expected=$canonical[$r['TRIGGER_NAME']]??null;
        bk_require(is_array($expected)&&$r['EVENT_MANIPULATION']===$expected['event']&&$r['EVENT_OBJECT_TABLE']===$expected['table']
            &&$r['ACTION_TIMING']===$expected['timing']&&$r['ACTION_STATEMENT']===$expected['body']&&(int)$r['ACTION_ORDER']===1
            &&$r['CHARACTER_SET_CLIENT']==='utf8mb4'&&$r['COLLATION_CONNECTION']==='utf8mb4_unicode_ci'
            &&$r['DATABASE_COLLATION']==='utf8mb4_unicode_ci','BACKUP_TRIGGER_PROFILE_REJECTED');
        bk_require(preg_match('/^([A-Za-z0-9_]{1,32})@(localhost|127\.0\.0\.1|::1)$/D',$r['DEFINER'],$identity)===1
            &&$identity[1]!=='root','BACKUP_DEFINER_PROFILE_REJECTED');
        if($auditDefiners) {
            $q=$pdo->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User=? AND Host=?');$q->execute([$identity[1],$identity[2]]);
            $count=(int)$q->fetchColumn();$q->closeCursor();bk_require($count===1,'BACKUP_DEFINER_MISSING');
            $q=$pdo->query('SHOW GRANTS FOR '.bk_ident($identity[1]).'@`'.$identity[2].'`');$grants=$q->fetchAll(PDO::FETCH_COLUMN);$q->closeCursor();
            $public=hestia_bounded_grants($pdo,true);$allowed=false;
            if (str_starts_with($identity[1], 'hdf_')) {
                bk_require($identity[1] === hdf_user($database) && $identity[2] === 'localhost', 'BACKUP_DEFINER_PROFILE_REJECTED');
                try { hdf_audit($pdo, $database, $identity[1]); $allowed=true; }
                catch (Throwable $error) { throw new RuntimeException('BACKUP_DEFINER_PROFILE_REJECTED'); }
            } else {
                foreach([false,true] as $tls)$allowed=$allowed||hestia_account_policy($grants,$public,$r['DEFINER'],null,$identity[1],$database,'provisioning',$tls);
            }
            bk_require($allowed,'BACKUP_DEFINER_PROFILE_REJECTED');
        }
    }
    return $rows;
}
function bk_orphaned_triggers(PDO $pdo,mixed $expected): array {
    // Explicit recovery evidence only. This never creates an account on source.
    bk_require(is_string($expected)&&preg_match('/^([A-Za-z0-9_]{1,32})@127\.0\.0\.1$/D',$expected,$m)===1
        &&$m[1]!=='root'&&!str_starts_with($m[1],'hdf_'),'BACKUP_RESCUE_PROFILE_REJECTED');
    $rows=bk_triggers($pdo,false);
    foreach($rows as $row)bk_require($row['DEFINER']===$expected,'BACKUP_RESCUE_PROFILE_REJECTED');
    $q=$pdo->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User=?');$q->execute([$m[1]]);
    $count=(int)$q->fetchColumn();$q->closeCursor();
    bk_require($count===0,'BACKUP_RESCUE_PROFILE_REJECTED');
    return $rows;
}
function bk_restore_triggers(PDO $authority,array $triggers): array {
    $canonical=bk_canonical_triggers();bk_require(count($triggers)===5,'BACKUP_TRIGGER_PROFILE_REJECTED');$created=[];
    $authority->exec('USE backup_verify');
    foreach($triggers as $r) {
        bk_require(is_array($r)&&isset($canonical[$r['TRIGGER_NAME']])&&$r['ACTION_STATEMENT']===$canonical[$r['TRIGGER_NAME']]['body']
            &&preg_match('/^([A-Za-z0-9_]{1,32})@(localhost|127\.0\.0\.1|::1)$/D',$r['DEFINER'],$m)===1&&$m[1]!=='root', 'BACKUP_TRIGGER_PROFILE_REJECTED');
        $account=bk_ident($m[1]).'@`'.$m[2].'`';
        if(!isset($created[$account])) {
            $q=$authority->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User=?');$q->execute([$m[1]]);
            $count=(int)$q->fetchColumn();$q->closeCursor();bk_require($count===0,'BACKUP_VERIFIER_TARGET_OCCUPIED');
            // Preserve DEFINER identity and its schema-only privileges, NEVER its login credential.
            if (preg_match('/^hdf_[a-f0-9]{24}$/D', $m[1]) === 1 && $m[2] === 'localhost') {
                try { hdf_create($authority, 'backup_verify', $m[1]); }
                catch (Throwable $error) { throw new RuntimeException('BACKUP_DEFINER_PROFILE_REJECTED'); }
            } else {
                $authority->exec('CREATE USER '.$account.' ACCOUNT LOCK');
                $authority->exec('GRANT ALL PRIVILEGES ON `backup\_verify`.* TO '.$account);
            }
            $created[$account]=true;
        }
        bk_require(is_string($r['SQL_MODE'])&&preg_match('/^[A-Z_,]*$/D',$r['SQL_MODE'])===1
            &&$r['CHARACTER_SET_CLIENT']==='utf8mb4'&&$r['COLLATION_CONNECTION']==='utf8mb4_unicode_ci','BACKUP_TRIGGER_PROFILE_REJECTED');
        $q=$authority->prepare('SET SESSION sql_mode=?');$q->execute([$r['SQL_MODE']]);$q->closeCursor();
        $authority->exec('SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci');
        $authority->exec('CREATE DEFINER='.$account.' '.substr($canonical[$r['TRIGGER_NAME']]['statement'],7));
    }
    bk_require(bk_triggers($authority,false)===$triggers,'BACKUP_TRIGGER_RESTORE_MISMATCH');
    return array_keys($created);
}
function bk_profile(PDO $pdo): array {
    // Global authority has visibility of ALL these objects; DML visibility is NOT used here.
    foreach (["SELECT COUNT(*) FROM mysql.proc WHERE db=DATABASE()",
        "SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA=DATABASE()",
        "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_SCHEMA<>DATABASE()"] as $sql) {
        bk_require((int)bk_scalar($pdo,$sql)===0,'BACKUP_SPECIAL_OBJECTS_UNSUPPORTED');
    }
    $tables=bk_all($pdo,'SELECT TABLE_NAME,TABLE_TYPE,ENGINE,CREATE_OPTIONS FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY BINARY TABLE_NAME LIMIT 513',512);
    bk_require(count($tables)>=6,'BACKUP_PROFILE_REJECTED');$result=[];$columns=0;
    foreach($tables as $t) {
        bk_require($t['TABLE_TYPE']==='BASE TABLE' && $t['ENGINE']==='InnoDB' && $t['CREATE_OPTIONS']==='', 'BACKUP_SPECIAL_OBJECTS_UNSUPPORTED');
        $name=$t['TABLE_NAME'];bk_ident($name);
        $q=$pdo->prepare('SELECT COLUMN_NAME,IS_GENERATED,EXTRA,DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=? ORDER BY ORDINAL_POSITION LIMIT 1025');$q->execute([$name]);$c=$q->fetchAll();$q->closeCursor();
        bk_require(count($c)>0 && count($c)<=1024 && ($columns+=count($c))<=16384,'BACKUP_LIMIT');$names=[];
        foreach($c as $col) {bk_ident($col['COLUMN_NAME']);bk_require(in_array($col['DATA_TYPE'],['tinyint','smallint','mediumint','int','bigint','decimal','float','double','bit','char','varchar','tinytext','text','mediumtext','longtext','binary','varbinary','tinyblob','blob','mediumblob','longblob','date','datetime','timestamp','time','year','enum','set'],true) && $col['IS_GENERATED']==='NEVER' && !str_contains(strtoupper($col['EXTRA']),'INVISIBLE'),'BACKUP_SPECIAL_OBJECTS_UNSUPPORTED');$names[]=$col['COLUMN_NAME'];}
        $q=$pdo->query('SHOW CREATE TABLE '.bk_ident($name));$row=$q->fetch(PDO::FETCH_NUM);$q->closeCursor();
        bk_require(is_array($row) && isset($row[1]) && strlen($row[1])<1048576,'BACKUP_LIMIT');
        $result[]=['name'=>$name,'columns'=>$names,'ddl'=>$row[1]];
    }
    return $result;
}
function bk_table(PDO $pdo,array $t,?callable $emit): array {
    // Native prepared results preserve the binary floating point value. CAST to
    // text on the SQL server can round FLOAT to six digits, which is not a backup.
    $meta=$pdo->prepare("SELECT COLUMN_NAME,DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=? ORDER BY ORDINAL_POSITION");
    $meta->execute([$t['name']]);$types=$meta->fetchAll();$meta->closeCursor();
    bk_require(array_column($types,'COLUMN_NAME')===$t['columns'],'BACKUP_SOURCE_CHANGED');
    $expressions=[];$frames=[];$floats=[];$numericOrder=[];
    foreach($types as $index=>$type) {
        $name=bk_ident($type['COLUMN_NAME']);
        if(in_array($type['DATA_TYPE'],['float','double'],true)) {
            $expressions[]='CAST('.$name.' AS DOUBLE)';$floats[]=$index;$numericOrder[]=$name;
        } else {
            $e='HEX(CAST('.$name.' AS BINARY))';$expressions[]=$e;
            $frames[]="IF($e IS NULL,'N;',CONCAT('S',LENGTH($e),':',$e,';'))";
        }
    }
    // Fixed-size keys avoid max_sort_length truncation of long TEXT/BLOB. Float
    // ties are ordered numerically using native fixed-size sort keys. Different
    // rows with the same framed hash and floats are refused, never elided.
    $sort=$frames===[] ? "REPEAT('0',64)" : 'SHA2(CONCAT('.implode(',',$frames).'),256)';
    $sql='SELECT '.$sort.','.implode(',',$expressions).' FROM '.bk_ident($t['name']).' ORDER BY 1'.($numericOrder===[]?'':','.implode(',',$numericOrder));
    $q=$pdo->prepare($sql);$q->execute();$hash=hash_init('sha256');$count=0;$previousKey=null;$previousLine=null;
    while(($row=$q->fetch(PDO::FETCH_NUM))!==false) {
        $key=array_shift($row);bk_require(is_string($key)&&strlen($key)===64,'BACKUP_PROFILE_REJECTED');
        $floatKeys=[];
        foreach($floats as $index) {
            $value=$row[$index];bk_require($value===null||(is_float($value)&&is_finite($value)),'BACKUP_NUMERIC_UNSUPPORTED');
            $row[$index]=$value===null?null:strtoupper(bin2hex(sprintf('%.17g',$value)));
            $floatKeys[]=$row[$index];
        }
        $key.=bk_json($floatKeys);
        bk_require(++$count<=BK_MAX_ROWS,'BACKUP_LIMIT');$line=bk_json($row)."\n";
        bk_require(strlen($line)<=BK_MAX_LINE,'BACKUP_LIMIT');
        bk_require($key!==$previousKey||$line===$previousLine,'BACKUP_ROW_ORDER_COLLISION');
        $previousKey=$key;$previousLine=$line;hash_update($hash,$line);
        if($emit!==null)$emit(['type'=>'row','values'=>$row]);
    }
    $q->closeCursor();return ['name'=>$t['name'],'rows'=>(string)$count,'sha256'=>hash_final($hash),'ddl_sha256'=>hash('sha256',$t['ddl'])];
}

function bk_foreign_keys(PDO $pdo): int {
    $rows=bk_all($pdo,'SELECT TABLE_NAME,CONSTRAINT_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY BINARY TABLE_NAME,BINARY CONSTRAINT_NAME,ORDINAL_POSITION LIMIT 4097',4096);
    $keys=[];
    foreach($rows as $r)$keys[$r['TABLE_NAME'].'.'.$r['CONSTRAINT_NAME']][]=$r;
    foreach($keys as $columns) {
        $present=[];$equal=[];
        foreach($columns as $r) {
            $present[]='c.'.bk_ident($r['COLUMN_NAME']).' IS NOT NULL';
            $equal[]='p.'.bk_ident($r['REFERENCED_COLUMN_NAME']).'=c.'.bk_ident($r['COLUMN_NAME']);
        }
        $sql='SELECT COUNT(*) FROM '.bk_ident($columns[0]['TABLE_NAME']).' c WHERE '.implode(' AND ',$present)
            .' AND NOT EXISTS(SELECT 1 FROM '.bk_ident($columns[0]['REFERENCED_TABLE_NAME']).' p WHERE '.implode(' AND ',$equal).')';
        bk_require((int)bk_scalar($pdo,$sql)===0,'BACKUP_FOREIGN_KEY_MISMATCH');
    }
    return count($keys);
}

function bk_trigger_smoke(PDO $pdo): void {
    // ONLY the socket-only throwaway verifier, after exact row/DDL comparison.
    // Distinct negative IDs and labels avoid relying on an empty business DB.
    $id=-random_int(1000000000,2000000000);$x='BK'.strtoupper(bin2hex(random_bytes(8)));$y=$x.'Y';
    $pdo->beginTransaction();
    $exec=static function(string $sql,array $values=[]) use($pdo):void {$q=$pdo->prepare($sql);$q->execute($values);$q->closeCursor();};
    try {
        foreach([['Ref_Segment','id_segment','code_segment','Ref_Segment_Alias','id_segment_alias'],
                 ['Ref_Complexite','id_complexite','code_complexite','Ref_Complexite_Alias','id_complexite_alias']] as [$table,$pk,$code,$aliases,$aliasPk]) {
            foreach([[$id,$x],[$id+1,$y]] as [$n,$label]) {
                $exec('INSERT INTO '.bk_ident($table).' ('.bk_ident($pk).','.bk_ident($code).',libelle) VALUES(?,?,?)',[$n,$label,$label]);
                $exec('INSERT INTO '.bk_ident($aliases).' ('.bk_ident($aliasPk).','.bk_ident($pk).',alias_source,alias_norm) VALUES(?,?,?,?)',[$n,$n,$label,$label]);
            }
        }
        $exec('INSERT INTO P_Activite(id_activite,titre_projet,code_secteur,complexite_source) VALUES(?,?,?,?)',[$id,$x,$x,$x]);
        bk_require((int)bk_scalar($pdo,'SELECT COUNT(*) FROM P_Activite WHERE id_activite='.$id.' AND id_segment='.$id.' AND id_complexite='.$id)===1,'BACKUP_TRIGGER_SMOKE_FAILED');
        $exec('UPDATE P_Activite SET code_secteur=?,complexite_source=? WHERE id_activite=?',[$y,$y,$id]);
        bk_require((int)bk_scalar($pdo,'SELECT COUNT(*) FROM P_Activite WHERE id_activite='.$id.' AND id_segment='.($id+1).' AND id_complexite='.($id+1))===1,'BACKUP_TRIGGER_SMOKE_FAILED');
        $exec("INSERT INTO P_Activite_Reference(id_reference,id_activite,type) VALUES(?,?,'REFERENCE')",[$id,$id]);
        bk_require(bk_scalar($pdo,'SELECT segment_snapshot FROM P_Activite_Reference WHERE id_reference='.$id)===$y
            &&bk_scalar($pdo,'SELECT complexite_snapshot FROM P_Activite_Reference WHERE id_reference='.$id)===$y,'BACKUP_TRIGGER_SMOKE_FAILED');
        $exec('INSERT INTO P_Activite(id_activite,titre_projet) VALUES(?,?)',[$id+1,$x]);
        $exec('INSERT INTO P_Import_Batch(id_import_batch,type_source,nom_fichier) VALUES(?,?,?)',[$id,$x,$x]);
        $exec("INSERT INTO P_Import_Group(id_import_group,id_import_batch,group_type,group_key,display_label,consolidated_json) VALUES(?,?,'PROJECT',?,?,?)",[$id,$id,$x,$x,bk_json(['code_secteur'=>$x,'complexite'=>$x])]);
        $exec('UPDATE P_Import_Group SET id_activite_cible=? WHERE id_import_group=?',[$id+1,$id]);
        bk_require((int)bk_scalar($pdo,'SELECT COUNT(*) FROM P_Activite WHERE id_activite='.($id+1).' AND id_segment='.$id.' AND id_complexite='.$id)===1,'BACKUP_TRIGGER_SMOKE_FAILED');
        $exec("INSERT INTO Ged_Link(id_link,link_type,label,url) VALUES(?,'EXTERNAL',?,'https://example.invalid')",[$id,$x]);
        $exec("INSERT INTO Ged_Legacy_Stat(id_legacy_stat,source_table,id_link) VALUES(?,'HESTIA_Link_Click',?)",[$id,$id]);
        bk_require((int)bk_scalar($pdo,'SELECT COUNT(*) FROM Ged_Link_Click_Log WHERE source_legacy_stat_id='.$id.' AND id_link='.$id)===1,'BACKUP_TRIGGER_SMOKE_FAILED');
    } finally {if($pdo->inTransaction())$pdo->rollBack();}
}

function bk_export(#[SensitiveParameter] array $v,bool $rescue=false): void {
    $locker=null;$pdo=null;$locked=false;$bytes=0;
    $emit=static function(array $line) use (&$bytes): void {
        $raw=bk_json($line)."\n";bk_require(strlen($raw)<=BK_MAX_LINE && ($bytes+=strlen($raw))<=BK_MAX_BYTES,'BACKUP_LIMIT');
        $offset=0;while($offset<strlen($raw)) { $n=fwrite(STDOUT,substr($raw,$offset));bk_require(is_int($n)&&$n>0,'BACKUP_CHANNEL_FAILED');$offset+=$n; }
    };
    try {
        $keys=$rescue?['authority','expected_orphaned_definer','operation','request_id','target','version']:['authority','operation','request_id','target','version'];
        bk_require(array_keys($v)===$keys && $v['version']===1,'REQUEST_INVALID');
        $locker=bk_connection($v['target'],$v['authority']);bk_authority($locker,$v['authority'],$v['target']['tls_required']);
        $identity=bk_identity($locker);bk_require($identity['db']===$v['target']['name']);
        $locker->exec('SET SESSION lock_wait_timeout=5');
        $locker->exec('SET SESSION max_statement_time=10');
        // Explicitly consented server-wide, temporary lock. Separate connection survives the read-only transaction.
        $locker->exec('FLUSH TABLES WITH READ LOCK');$locked=true;
        $pdo=bk_connection($v['target'],$v['authority']);bk_authority($pdo,$v['authority'],$v['target']['tls_required']);
        bk_require(bk_identity($pdo)===$identity,'BACKUP_SOURCE_CHANGED');bk_session($pdo);
        $pdo->exec('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ');
        $pdo->exec('START TRANSACTION READ ONLY, WITH CONSISTENT SNAPSHOT');bk_require($pdo->inTransaction());
        if($rescue){$tables=bk_profile($pdo);$triggers=bk_orphaned_triggers($pdo,$v['expected_orphaned_definer']);}
        else {$tables=bk_profile($pdo);$triggers=bk_triggers($pdo);}
        $db=bk_all($pdo,'SELECT DEFAULT_CHARACTER_SET_NAME AS charset,DEFAULT_COLLATION_NAME AS collation FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE()',1)[0];
        bk_require($db===['charset'=>'utf8mb4','collation'=>'utf8mb4_unicode_ci']);
        bk_require(bk_scalar($pdo,"SELECT valeur FROM App_Config WHERE cle='APP_VERSION'")===HESTIA_INSTALL_VERSION_KEY,'BACKUP_SOURCE_CHANGED');
        $header=['type'=>'header','version'=>$rescue?2:1,'request_id'=>$v['request_id'],'release'=>HESTIA_INSTALL_VERSION_KEY,'server_version'=>$identity['version'],'database'=>$db,'tables'=>count($tables),'triggers'=>$triggers];
        if($rescue)$header['purpose']='ORPHANED_DEFINER_RESCUE';
        $emit($header);
        $summary=[];$total=0;
        foreach($tables as $t) {
            $emit(['type'=>'table','name'=>$t['name'],'columns'=>$t['columns'],'ddl_base64'=>base64_encode($t['ddl'])]);
            $s=bk_table($pdo,$t,$emit);bk_require(($total+=(int)$s['rows'])<=BK_MAX_ROWS,'BACKUP_LIMIT');$summary[]=$s;
            $emit(['type'=>'end_table',...$s]);
        }
        bk_require(bk_profile($pdo)===$tables && ($rescue?bk_orphaned_triggers($pdo,$v['expected_orphaned_definer']):bk_triggers($pdo))===$triggers,'BACKUP_SOURCE_CHANGED');
        bk_require($pdo->rollBack());$locker->exec('UNLOCK TABLES');$locked=false;
        $emit(['type'=>'complete','request_id'=>$v['request_id'],'tables'=>count($tables),'rows'=>(string)$total,'logical_sha256'=>hash('sha256',bk_json([$summary,$triggers]))]);
    } finally {
        if($pdo instanceof PDO && $pdo->inTransaction())$pdo->rollBack();
        if($locked && $locker instanceof PDO)$locker->exec('UNLOCK TABLES');
    }
}
function bk_line($handle): array {
    $raw=fgets($handle,BK_MAX_LINE+2);bk_require(is_string($raw)&&strlen($raw)<=BK_MAX_LINE&&str_ends_with($raw,"\n"),'BACKUP_ARCHIVE_INVALID');
    $v=json_decode($raw,true,20,JSON_THROW_ON_ERROR);bk_require(is_array($v)&&bk_json($v)."\n"===$raw,'BACKUP_ARCHIVE_INVALID');return $v;
}
function bk_socket(#[SensitiveParameter] string $user,#[SensitiveParameter] string $password,?string $database=null): PDO {
    return new PDO('mysql:unix_socket='.__DIR__.'/verify/sql.sock;charset=utf8mb4'.($database===null?'':';dbname='.$database),$user,$password,[
        PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC,PDO::ATTR_EMULATE_PREPARES=>false,
        PDO::MYSQL_ATTR_MULTI_STATEMENTS=>false,PDO::MYSQL_ATTR_LOCAL_INFILE=>false,PDO::ATTR_TIMEOUT=>5]);
}
function bk_verify(#[SensitiveParameter] array $v,bool $rescue=false): array {
    bk_require(array_keys($v)===['archive_sha256','operation','password','request_id','version'] && $v['version']===1
        && preg_match('/^[a-f0-9]{64}$/D',$v['archive_sha256'])===1 && preg_match('/^[a-f0-9]{64}$/D',$v['password'])===1,'REQUEST_INVALID');
    $path=__DIR__.'/database.ndjson';$info=lstat($path);
    bk_require(is_array($info)&&($info['mode']&0170000)===0100000&&$info['uid']===0&&($info['mode']&07022)===0&&$info['nlink']===1
        &&$info['size']>0&&$info['size']<=BK_MAX_BYTES&&hash_file('sha256',$path)===$v['archive_sha256'],'BACKUP_ARCHIVE_INVALID');
    $handle=fopen($path,'rb');$header=bk_line($handle);
    $keys=['type','version','request_id','release','server_version','database','tables','triggers'];
    if($rescue)$keys[]='purpose';
    bk_require(array_keys($header)===$keys&&$header['type']==='header'
        &&$header['version']===($rescue?2:1)&&(!$rescue||$header['purpose']==='ORPHANED_DEFINER_RESCUE')
        &&$header['request_id']===$v['request_id']&&$header['release']===HESTIA_INSTALL_VERSION_KEY
        &&$header['database']===['charset'=>'utf8mb4','collation'=>'utf8mb4_unicode_ci']&&is_int($header['tables'])&&$header['tables']>=6&&$header['tables']<=512,'BACKUP_ARCHIVE_INVALID');
    $authority=bk_socket('root','');$server=bk_identity($authority);
    bk_require($server['version']===$header['server_version'],'BACKUP_VERIFIER_VERSION_MISMATCH');
    bk_require((int)bk_scalar($authority,'SELECT @@skip_networking')===1
        &&realpath($server['dir'])===realpath(__DIR__.'/verify/data'),'BACKUP_VERIFIER_NOT_ISOLATED');
    bk_require((int)bk_scalar($authority,"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='backup_verify'")===0,'BACKUP_VERIFIER_TARGET_OCCUPIED');
    $user='hvb_'.substr($v['request_id'],0,24);$account="`$user`@`localhost`";
    $q=$authority->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User=?');$q->execute([$user]);bk_require((int)$q->fetchColumn()===0,'BACKUP_VERIFIER_TARGET_OCCUPIED');$q->closeCursor();
    $authority->exec('CREATE DATABASE backup_verify CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci');
    $hash='*'.strtoupper(sha1(sha1($v['password'],true)));
    $authority->exec("CREATE USER $account IDENTIFIED BY PASSWORD '$hash'");
    $authority->exec('GRANT SELECT,INSERT,CREATE,REFERENCES ON `backup\_verify`.* TO '.$account);
    $pdo=bk_socket($user,$v['password'],'backup_verify');bk_session($pdo);$pdo->exec('SET SESSION foreign_key_checks=0');
    $summary=[];$names=[];$total=0;
    for($i=0;$i<$header['tables'];$i++) {
        $t=bk_line($handle);bk_require(array_keys($t)===['type','name','columns','ddl_base64']&&$t['type']==='table','BACKUP_ARCHIVE_INVALID');
        $name=$t['name'];bk_ident($name);bk_require(!isset($names[$name])&&is_array($t['columns'])&&count($t['columns'])>0&&count($t['columns'])<=1024,'BACKUP_ARCHIVE_INVALID');$names[$name]=true;
        $ddl=base64_decode($t['ddl_base64'],true);bk_require(is_string($ddl)&&str_starts_with($ddl,'CREATE TABLE '.bk_ident($name).' (')&&strlen($ddl)<1048576,'BACKUP_ARCHIVE_INVALID');
        // Only this captured SHOW CREATE statement, no multi-statements, no global/file/execute privileges.
        $pdo->exec($ddl);
        $insert=$pdo->prepare('INSERT INTO '.bk_ident($name).' ('.implode(',',array_map('bk_ident',$t['columns'])).') VALUES ('.implode(',',array_fill(0,count($t['columns']),'UNHEX(?)')).')');
        $count=0;$h=hash_init('sha256');$pdo->beginTransaction();
        while(true) {
            $row=bk_line($handle);if(($row['type']??null)==='end_table')break;
            bk_require(array_keys($row)===['type','values']&&$row['type']==='row'&&is_array($row['values'])&&array_is_list($row['values'])&&count($row['values'])===count($t['columns']),'BACKUP_ARCHIVE_INVALID');
            foreach($row['values'] as $cell)bk_require($cell===null||(is_string($cell)&&strlen($cell)%2===0&&preg_match('/^[A-F0-9]*$/D',$cell)===1),'BACKUP_ARCHIVE_INVALID');
            bk_require(++$count<=BK_MAX_ROWS&&++$total<=BK_MAX_ROWS,'BACKUP_LIMIT');hash_update($h,bk_json($row['values'])."\n");
            $insert->execute($row['values']);$insert->closeCursor();bk_require((int)bk_scalar($pdo,'SHOW COUNT(*) WARNINGS')===0,'BACKUP_RESTORE_WARNING');
        }
        $expected=['name'=>$name,'rows'=>(string)$count,'sha256'=>hash_final($h),'ddl_sha256'=>hash('sha256',$ddl)];
        bk_require($row===['type'=>'end_table',...$expected],'BACKUP_ARCHIVE_INVALID');$pdo->commit();
        $actual=bk_table($pdo,['name'=>$name,'columns'=>$t['columns'],'ddl'=>$ddl],null);bk_require($actual===$expected,'BACKUP_RESTORE_MISMATCH');
        $q=$pdo->query('SHOW CREATE TABLE '.bk_ident($name));$created=$q->fetch(PDO::FETCH_NUM);$q->closeCursor();bk_require($created[1]===$ddl,'BACKUP_RESTORE_MISMATCH');$summary[]=$expected;
    }
    $triggers=$header['triggers'];bk_require(is_array($triggers),'BACKUP_ARCHIVE_INVALID');
    $logical=hash('sha256',bk_json([$summary,$triggers]));$last=bk_line($handle);
    bk_require($last===['type'=>'complete','request_id'=>$v['request_id'],'tables'=>$header['tables'],'rows'=>(string)$total,'logical_sha256'=>$logical]
        &&fgetc($handle)===false,'BACKUP_ARCHIVE_INVALID');fclose($handle);
    $definers=bk_restore_triggers($authority,$triggers);
    $pdo->exec('SET SESSION foreign_key_checks=1');
    $foreignKeys=bk_foreign_keys($pdo);
    $authority->exec('REVOKE CREATE,REFERENCES ON `backup\_verify`.* FROM '.$account);
    $authority->exec('GRANT UPDATE,DELETE ON `backup\_verify`.* TO '.$account);
    // Database-level privileges are cached on an existing connection. Verify
    // and exercise the new DML-only identity through a fresh connection.
    $insert=null;$pdo=null;$pdo=bk_socket($user,$v['password'],'backup_verify');bk_session($pdo);
    bk_require(hestia_account_policy(hestia_bounded_grants($pdo,false),hestia_bounded_grants($pdo,true),$user.'@localhost',null,$user,'backup_verify','application'),'BACKUP_VERIFIER_NOT_ISOLATED');
    bk_trigger_smoke($pdo);
    bk_require(bk_scalar($pdo,"SELECT valeur FROM App_Config WHERE cle='APP_VERSION'")===HESTIA_INSTALL_VERSION_KEY,'BACKUP_RESTORE_MISMATCH');
    bk_require((int)bk_scalar($pdo,"SELECT COUNT(*) FROM UserInfo u JOIN Roles r ON u.id_role_applicatif=r.id_role_applicatif WHERE u.actif=1 AND r.actif=1 AND r.code_role='ADMIN_GENERAL'")>0,'BACKUP_RESTORE_MISMATCH');
    bk_require((int)bk_scalar($pdo,'SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')===$header['tables'],'BACKUP_RESTORE_MISMATCH');
    $pdo=null;$authority->exec('DROP USER '.$account);$authority->exec('DROP DATABASE backup_verify');
    foreach($definers as $definer)$authority->exec('DROP USER '.$definer);
    bk_require((int)bk_scalar($authority,"SELECT COUNT(*) FROM mysql.global_priv WHERE User NOT IN ('root','mariadb.sys','mysql')")===0,'BACKUP_VERIFIER_CLEANUP_FAILED');
    bk_require((int)bk_scalar($authority,"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='backup_verify'")===0,'BACKUP_VERIFIER_CLEANUP_FAILED');
    $q=$authority->prepare('SELECT COUNT(*) FROM mysql.global_priv WHERE User=?');$q->execute([$user]);bk_require((int)$q->fetchColumn()===0,'BACKUP_VERIFIER_CLEANUP_FAILED');
    return ['tables'=>$header['tables'],'rows'=>(string)$total,'logical_sha256'=>$logical,'server_version'=>$server['version'],'verification_objects_removed'=>true,'canonical_triggers_verified'=>5,'trigger_smoke_verified'=>5,'foreign_keys_verified'=>$foreignKeys];
}
$op='invalid';$id='';
try {
    if(PHP_SAPI!=='cli')throw new RuntimeException('REQUEST_INVALID');
    set_error_handler(static function():never {throw new RuntimeException('BACKUP_WORKER_FAILED');});
    require_once __DIR__.'/engine/includes/installation/connection.php';
    require_once __DIR__.'/engine/includes/installation/finalization.php';
    require_once __DIR__.'/sql_accounts_policy.php';
    require_once __DIR__.'/trigger_definer.php';
    $raw=stream_get_contents(STDIN,16385);bk_require(is_string($raw)&&strlen($raw)<=16384,'REQUEST_INVALID');
    $v=json_decode($raw,true,12,JSON_THROW_ON_ERROR);bk_require(is_array($v)&&bk_json($v)===$raw,'REQUEST_INVALID');
    bk_require(isset($v['request_id'])&&is_string($v['request_id'])&&preg_match('/^[a-f0-9]{32}$/D',$v['request_id'])===1,'REQUEST_INVALID');$id=$v['request_id'];$op=$v['operation']??'invalid';
    if(in_array($op,['export','export_rescue'],true)){bk_export($v,$op==='export_rescue');exit(0);}
    bk_require(in_array($op,['verify','verify_rescue'],true),'REQUEST_INVALID');$result=bk_verify($v,$op==='verify_rescue');
    echo bk_json(['version'=>1,'request_id'=>$id,'ok'=>true,'result'=>$result,'error'=>null]);
} catch(Throwable $error) {
    $known=['REQUEST_INVALID','SQL_TARGET_INVALID','SQL_CA_INVALID','SQL_CREDENTIAL_INVALID','SQL_DRIVER_REQUIRED','SQL_CONNECTION_FAILED','SQL_TLS_CONNECTION_FAILED',
        'BACKUP_PROFILE_REJECTED','BACKUP_AUTHORITY_REJECTED','AUDIT_UNAVAILABLE','BACKUP_SPECIAL_OBJECTS_UNSUPPORTED','BACKUP_LIMIT','BACKUP_CHANNEL_FAILED',
        'BACKUP_SOURCE_CHANGED','BACKUP_ARCHIVE_INVALID','BACKUP_RESCUE_PROFILE_REJECTED','BACKUP_VERIFIER_VERSION_MISMATCH','BACKUP_VERIFIER_NOT_ISOLATED','BACKUP_VERIFIER_TARGET_OCCUPIED',
        'BACKUP_RESTORE_WARNING','BACKUP_RESTORE_MISMATCH','BACKUP_VERIFIER_CLEANUP_FAILED','BACKUP_TRIGGER_PROFILE_REJECTED',
        'BACKUP_DEFINER_PROFILE_REJECTED','BACKUP_DEFINER_MISSING','BACKUP_TRIGGER_RESTORE_MISMATCH','BACKUP_ROW_ORDER_COLLISION','BACKUP_NUMERIC_UNSUPPORTED','BACKUP_FOREIGN_KEY_MISMATCH','BACKUP_TRIGGER_SMOKE_FAILED'];
    $code=in_array($error->getMessage(),$known,true)?$error->getMessage():'BACKUP_WORKER_FAILED';
    echo bk_json(['version'=>1,'request_id'=>$id,'ok'=>false,'result'=>null,'error'=>$code]).(in_array($op,['export','export_rescue'],true)?"\n":'');exit(20);
}
