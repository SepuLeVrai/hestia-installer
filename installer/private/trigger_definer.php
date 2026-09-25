<?php
declare(strict_types=1);

// Closed, shared policy for the five triggers in the pinned Web schema.
// Loading this file never connects, creates an identity or repairs a database.
function hdf_require(bool $ok, string $code = 'DEFINER_PROFILE_REJECTED'): void
{
    if (!$ok) throw new RuntimeException($code);
}

function hdf_ident(string $name): string
{
    hdf_require(preg_match('/^[A-Za-z0-9_]{1,64}$/D', $name) === 1);
    return '`' . $name . '`';
}

function hdf_user(string $database): string
{
    hdf_ident($database);
    return 'hdf_' . substr(hash('sha256', strtolower($database)), 0, 24);
}

function hdf_account(string $user): string
{
    hdf_require(preg_match('/^hdf_[a-f0-9]{24}$/D', $user) === 1);
    return hdf_ident($user) . '@`localhost`';
}

function hdf_rights(): array
{
    // Empty column list means a table privilege. No schema/global/DML wildcard.
    return [
        'P_Activite' => [
            'TRIGGER' => [],
            'SELECT' => ['id_activite', 'id_segment', 'id_complexite', 'code_secteur', 'complexite_source'],
            'UPDATE' => ['id_segment', 'id_complexite', 'code_secteur', 'complexite_source'],
        ],
        'P_Activite_Reference' => [
            'TRIGGER' => [],
            'SELECT' => ['id_activite', 'id_segment', 'id_complexite', 'segment_snapshot', 'complexite_snapshot'],
            'UPDATE' => ['id_segment', 'id_complexite', 'segment_snapshot', 'complexite_snapshot'],
        ],
        'P_Import_Group' => ['TRIGGER' => [], 'SELECT' => ['id_activite_cible', 'consolidated_json']],
        'Ged_Legacy_Stat' => ['TRIGGER' => [], 'SELECT' => ['source_table', 'id_link', 'id_legacy_stat', 'happened_at', 'imported_at', 'raw_json']],
        'Ref_Segment_Alias' => ['SELECT' => ['id_segment', 'actif', 'source_type', 'alias_norm']],
        'Ref_Complexite_Alias' => ['SELECT' => ['id_complexite', 'actif', 'source_type', 'alias_norm']],
        'Ref_Segment' => ['SELECT' => ['id_segment', 'libelle']],
        'Ref_Complexite' => ['SELECT' => ['id_complexite', 'libelle']],
        'Ged_Link' => ['SELECT' => ['id_link', 'link_type', 'label', 'vendor_label']],
        'UserInfo' => ['SELECT' => ['id_user']],
        'Ged_Link_Click_Log' => ['INSERT' => ['id_link', 'id_user', 'link_type_snapshot', 'label_snapshot', 'vendor_snapshot', 'source', 'source_legacy_stat_id', 'clicked_at']],
    ];
}

function hdf_scalar(PDO $pdo, string $sql, array $values = []): mixed
{
    $q = $pdo->prepare($sql);
    $q->execute($values);
    $value = $q->fetchColumn();
    $q->closeCursor();
    return $value;
}

function hdf_absent(PDO $pdo, string $user): void
{
    hdf_account($user);
    // Refuse all host variants; never adopt/alter an existing identity.
    hdf_require((int)hdf_scalar($pdo, 'SELECT COUNT(*) FROM mysql.global_priv WHERE User=?', [$user]) === 0,
        'DEFINER_ACCOUNT_OCCUPIED');
}

function hdf_audit(PDO $pdo, string $database, string $user): void
{
    hdf_ident($database); hdf_account($user);
    $q = $pdo->prepare("SELECT Host, JSON_EXTRACT(Priv, '$.account_locked') AS locked,
        JSON_VALUE(Priv, '$.access') AS access, JSON_VALUE(Priv, '$.plugin') AS plugin,
        JSON_VALUE(Priv, '$.authentication_string') AS authentication,
        JSON_EXTRACT(Priv, '$.auth_or') AS alternative, JSON_VALUE(Priv, '$.is_role') AS role,
        JSON_VALUE(Priv, '$.default_role') AS default_role FROM mysql.global_priv WHERE User=? LIMIT 2");
    $q->execute([$user]); $rows = $q->fetchAll(PDO::FETCH_ASSOC); $q->closeCursor();
    hdf_require(count($rows) === 1);
    $r = $rows[0];
    hdf_require($r['Host'] === 'localhost' && $r['locked'] === 'true' && (string)$r['access'] === '0'
        && $r['plugin'] === 'mysql_native_password' && $r['authentication'] === '' && $r['alternative'] === null
        && in_array($r['role'], [null, '0', 0], true) && in_array($r['default_role'], [null, ''], true));
    foreach (['SELECT COUNT(*) FROM mysql.db WHERE User=?',
              'SELECT COUNT(*) FROM mysql.procs_priv WHERE User=?',
              'SELECT COUNT(*) FROM mysql.roles_mapping WHERE User=? OR Role=?',
              'SELECT COUNT(*) FROM mysql.proxies_priv WHERE User=? OR Proxied_user=?'] as $sql) {
        hdf_require((int)hdf_scalar($pdo, $sql, array_fill(0, substr_count($sql, '?'), $user)) === 0);
    }
    foreach (hestia_bounded_grants($pdo, true) as $grant) {
        hdf_require(preg_match('/^GRANT USAGE ON \*\.\* TO (?:PUBLIC|`PUBLIC`)$/D', $grant) === 1);
    }
    $expected = ['TABLE_PRIVILEGES' => [], 'COLUMN_PRIVILEGES' => []];
    foreach (hdf_rights() as $table => $rights) {
        foreach ($rights as $privilege => $columns) {
            if ($columns === []) $expected['TABLE_PRIVILEGES'][] = [$database, $table, $privilege, 'NO'];
            foreach ($columns as $column) $expected['COLUMN_PRIVILEGES'][] = [$database, $table, $column, $privilege, 'NO'];
        }
    }
    foreach ($expected as $view => $wanted) {
        $fields = $view === 'TABLE_PRIVILEGES' ? 'TABLE_SCHEMA,TABLE_NAME,PRIVILEGE_TYPE,IS_GRANTABLE'
            : 'TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,PRIVILEGE_TYPE,IS_GRANTABLE';
        $q = $pdo->prepare('SELECT ' . $fields . ' FROM information_schema.' . $view . ' WHERE GRANTEE=? LIMIT 129');
        $q->execute(["'" . $user . "'@'localhost'"]);
        $actual = $q->fetchAll(PDO::FETCH_NUM); $q->closeCursor();
        sort($actual); sort($wanted);
        hdf_require($actual === $wanted);
    }
}

function hdf_create(PDO $pdo, string $database, string $user): void
{
    hdf_ident($database); $account = hdf_account($user); hdf_absent($pdo, $user);
    $pdo->exec('CREATE USER ' . $account . ' ACCOUNT LOCK');
    foreach (hdf_rights() as $table => $rights) {
        $clauses = [];
        foreach ($rights as $privilege => $columns) {
            $clauses[] = $privilege . ($columns === [] ? '' : ' (' . implode(',', array_map('hdf_ident', $columns)) . ')');
        }
        $pdo->exec('GRANT ' . implode(',', $clauses) . ' ON ' . hdf_ident($database) . '.' . hdf_ident($table) . ' TO ' . $account);
    }
    hdf_audit($pdo, $database, $user);
}

function hdf_canonical(): array
{
    $triggers = [];
    foreach (hestia_split_sql(hestia_normalize_sql_dump(file_get_contents(__DIR__ . '/engine/sql/schema.sql'))) as $statement) {
        if (preg_match('/^CREATE TRIGGER `([A-Za-z0-9_]+)` (BEFORE|AFTER) (INSERT|UPDATE|DELETE) ON `([A-Za-z0-9_]+)`\s+FOR EACH ROW\s+(BEGIN.*END)$/sD', $statement, $m) === 1) {
            $triggers[$m[1]] = ['timing' => $m[2], 'event' => $m[3], 'table' => $m[4], 'body' => $m[5], 'statement' => $statement];
        }
    }
    hdf_require(count($triggers) === 5, 'DEFINER_TRIGGER_PROFILE_REJECTED');
    ksort($triggers); return $triggers;
}

function hdf_triggers(PDO $pdo, string $database, string $definer): array
{
    $q = $pdo->prepare('SELECT TRIGGER_NAME,EVENT_MANIPULATION,EVENT_OBJECT_TABLE,ACTION_TIMING,ACTION_STATEMENT,ACTION_ORDER,DEFINER,SQL_MODE,CHARACTER_SET_CLIENT,COLLATION_CONNECTION,DATABASE_COLLATION FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=? ORDER BY BINARY TRIGGER_NAME LIMIT 6');
    $q->execute([$database]); $rows = $q->fetchAll(PDO::FETCH_ASSOC); $q->closeCursor();
    $expected = hdf_canonical();
    hdf_require(count($rows) === 5, 'DEFINER_TRIGGER_PROFILE_REJECTED');
    foreach ($rows as $row) {
        $e = $expected[$row['TRIGGER_NAME']] ?? null;
        hdf_require(is_array($e) && $row['DEFINER'] === $definer && $row['EVENT_MANIPULATION'] === $e['event']
            && $row['EVENT_OBJECT_TABLE'] === $e['table'] && $row['ACTION_TIMING'] === $e['timing']
            && $row['ACTION_STATEMENT'] === $e['body'] && (int)$row['ACTION_ORDER'] === 1
            && preg_match('/^[A-Z_,]*$/D', $row['SQL_MODE']) === 1 && $row['CHARACTER_SET_CLIENT'] === 'utf8mb4'
            && $row['COLLATION_CONNECTION'] === 'utf8mb4_unicode_ci' && $row['DATABASE_COLLATION'] === 'utf8mb4_unicode_ci',
            'DEFINER_TRIGGER_PROFILE_REJECTED');
    }
    return $rows;
}

function hdf_rebind_fresh(PDO $pdo, string $database, string $migration): void
{
    $user = hdf_user($database);
    $before = hdf_triggers($pdo, $database, $migration . '@127.0.0.1');
    hdf_create($pdo, $database, $user);
    $pdo->exec('USE ' . hdf_ident($database));
    $canonical = hdf_canonical();
    foreach ($before as $row) {
        $q = $pdo->prepare('SET SESSION sql_mode=?'); $q->execute([$row['SQL_MODE']]); $q->closeCursor();
        $pdo->exec('SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci');
        $pdo->exec('CREATE OR REPLACE DEFINER=' . hdf_account($user) . ' ' . substr($canonical[$row['TRIGGER_NAME']]['statement'], 7));
    }
    $after = hdf_triggers($pdo, $database, $user . '@localhost');
    foreach ($before as &$row) $row['DEFINER'] = $user . '@localhost';
    unset($row);
    hdf_require($after === $before, 'DEFINER_REBIND_FAILED');
    hdf_audit($pdo, $database, $user);
}

function hdf_smoke(PDO $pdo): void {
    // Caller supplies a DML-only connection to a fresh database or isolated verifier.
    // Distinct negative IDs and labels avoid relying on an empty business DB.
    $id=-random_int(1000000000,2000000000);$x='BK'.strtoupper(bin2hex(random_bytes(8)));$y=$x.'Y';
    $pdo->beginTransaction();
    $exec=static function(string $sql,array $values=[]) use($pdo):void {$q=$pdo->prepare($sql);$q->execute($values);$q->closeCursor();};
    try {
        foreach([['Ref_Segment','id_segment','code_segment','Ref_Segment_Alias','id_segment_alias'],
                 ['Ref_Complexite','id_complexite','code_complexite','Ref_Complexite_Alias','id_complexite_alias']] as [$table,$pk,$code,$aliases,$aliasPk]) {
            foreach([[$id,$x],[$id+1,$y]] as [$n,$label]) {
                $exec('INSERT INTO '.hdf_ident($table).' ('.hdf_ident($pk).','.hdf_ident($code).',libelle) VALUES(?,?,?)',[$n,$label,$label]);
                $exec('INSERT INTO '.hdf_ident($aliases).' ('.hdf_ident($aliasPk).','.hdf_ident($pk).',alias_source,alias_norm) VALUES(?,?,?,?)',[$n,$n,$label,$label]);
            }
        }
        $exec('INSERT INTO P_Activite(id_activite,titre_projet,code_secteur,complexite_source) VALUES(?,?,?,?)',[$id,$x,$x,$x]);
        hdf_require((int)hdf_scalar($pdo,'SELECT COUNT(*) FROM P_Activite WHERE id_activite='.$id.' AND id_segment='.$id.' AND id_complexite='.$id)===1,'DEFINER_SMOKE_FAILED');
        $exec('UPDATE P_Activite SET code_secteur=?,complexite_source=? WHERE id_activite=?',[$y,$y,$id]);
        hdf_require((int)hdf_scalar($pdo,'SELECT COUNT(*) FROM P_Activite WHERE id_activite='.$id.' AND id_segment='.($id+1).' AND id_complexite='.($id+1))===1,'DEFINER_SMOKE_FAILED');
        $exec("INSERT INTO P_Activite_Reference(id_reference,id_activite,type) VALUES(?,?,'REFERENCE')",[$id,$id]);
        hdf_require(hdf_scalar($pdo,'SELECT segment_snapshot FROM P_Activite_Reference WHERE id_reference='.$id)===$y
            &&hdf_scalar($pdo,'SELECT complexite_snapshot FROM P_Activite_Reference WHERE id_reference='.$id)===$y,'DEFINER_SMOKE_FAILED');
        $exec('INSERT INTO P_Activite(id_activite,titre_projet) VALUES(?,?)',[$id+1,$x]);
        $exec('INSERT INTO P_Import_Batch(id_import_batch,type_source,nom_fichier) VALUES(?,?,?)',[$id,$x,$x]);
        $exec("INSERT INTO P_Import_Group(id_import_group,id_import_batch,group_type,group_key,display_label,consolidated_json) VALUES(?,?,'PROJECT',?,?,?)",[$id,$id,$x,$x,json_encode(['code_secteur'=>$x,'complexite'=>$x], JSON_THROW_ON_ERROR)]);
        $exec('UPDATE P_Import_Group SET id_activite_cible=? WHERE id_import_group=?',[$id+1,$id]);
        hdf_require((int)hdf_scalar($pdo,'SELECT COUNT(*) FROM P_Activite WHERE id_activite='.($id+1).' AND id_segment='.$id.' AND id_complexite='.$id)===1,'DEFINER_SMOKE_FAILED');
        $exec("INSERT INTO Ged_Link(id_link,link_type,label,url) VALUES(?,'EXTERNAL',?,'https://example.invalid')",[$id,$x]);
        $exec("INSERT INTO Ged_Legacy_Stat(id_legacy_stat,source_table,id_link) VALUES(?,'HESTIA_Link_Click',?)",[$id,$id]);
        hdf_require((int)hdf_scalar($pdo,'SELECT COUNT(*) FROM Ged_Link_Click_Log WHERE source_legacy_stat_id='.$id.' AND id_link='.$id)===1,'DEFINER_SMOKE_FAILED');
    } finally {if($pdo->inTransaction())$pdo->rollBack();}
}
