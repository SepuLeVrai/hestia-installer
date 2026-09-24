<?php
declare(strict_types=1);

/** Complete profile supplements SHOW GRANTS with authority-side grant metadata.
 * No raw grant, account name or authentication hash is returned to Python.
 */
function b5_rows(PDO $pdo, string $sql, array $args = [], int $limit = 16): array
{
    $q = $pdo->prepare($sql); $q->execute($args); $rows = [];
    while ($row = $q->fetch(PDO::FETCH_ASSOC)) {
        if (count($rows) >= $limit) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
        $rows[] = $row;
    }
    return $rows;
}
function b5_grants(PDO $pdo, bool $public = false): array
{
    try { $q = $pdo->query($public ? 'SHOW GRANTS FOR PUBLIC' : 'SHOW GRANTS'); }
    catch (PDOException $e) {
        if ($public && ($e->errorInfo[1] ?? null) === 1141) return [];
        throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    }
    $out = [];
    while (($line = $q->fetchColumn()) !== false) {
        if (!is_string($line) || strlen($line) > 4096 || count($out) >= 16) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
        $out[] = $line;
    }
    return $out;
}
function b5_server(PDO $pdo): array
{
    $row = $pdo->query('SELECT VERSION() AS v, @@hostname AS h, @@server_id AS id')->fetch(PDO::FETCH_ASSOC);
    if (!is_array($row) || !preg_match('/^(?:10\.11|11\.4|11\.8)\.[0-9]+[^\r\n]*MariaDB/', (string)$row['v'])) throw new RuntimeException('SERVER_PROFILE_UNSUPPORTED');
    return $row;
}
function b5_peer(string $peer): bool
{
    return in_array($peer, ['localhost', '127.0.0.1', '::1'], true) || filter_var($peer, FILTER_VALIDATE_IP) !== false;
}
function b5_audit(PDO $authority, PDO $pdo, array $db, string $user, string $profile): void
{
    if (b5_server($authority) !== b5_server($pdo)) throw new RuntimeException('TARGET_IDENTITY_MISMATCH');
    $identity = $pdo->query('SELECT CURRENT_USER() AS u, CURRENT_ROLE() AS r, USER() AS p, DATABASE() AS d')->fetch(PDO::FETCH_ASSOC);
    $at = strrpos((string)$identity['u'], '@');
    $host = $at === false ? '' : substr($identity['u'], $at + 1);
    $peerAt = strrpos((string)$identity['p'], '@');
    $peer = $peerAt === false ? '' : substr($identity['p'], $peerAt + 1);
    if ($identity['r'] !== null || $identity['d'] !== $db['name'] || $identity['u'] !== $user . '@' . $host
        || !b5_peer($host) || !b5_peer($peer) || ($host !== $peer && !in_array([$host,$peer], [['localhost','127.0.0.1'],['127.0.0.1','localhost']], true))) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    // One user/Host row only, no anonymous alternative. Reads require the separately
    // supplied authority; the application/migration accounts never receive mysql access.
    $rows = b5_rows($authority, 'SELECT Host FROM mysql.global_priv WHERE User=? OR User=\'\'', [$user]);
    if ($rows !== [['Host' => $host]]) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    foreach (['tables_priv','columns_priv','procs_priv'] as $table) {
        if (b5_rows($authority, 'SELECT Host FROM mysql.' . $table . ' WHERE User=?', [$user])) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    }
    if (b5_rows($authority, 'SELECT Host FROM mysql.proxies_priv WHERE User=? OR Proxied_user=?', [$user,$user])
        || b5_rows($authority, 'SELECT Host FROM mysql.roles_mapping WHERE User=?', [$user])) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    $scope = str_replace('_', '\\_', $db['name']);
    if (b5_rows($authority, 'SELECT Host,Db FROM mysql.db WHERE User=?', [$user]) !== [['Host'=>$host,'Db'=>$scope]]) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    foreach (b5_grants($pdo, true) as $grant) {
        if (!preg_match('/^GRANT USAGE ON \*\.\* TO (?:PUBLIC|`PUBLIC`)$/D', $grant)) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    }
    $account = '(?:`' . preg_quote($user,'/') . '`@`' . preg_quote($host,'/') . '`|\'' . preg_quote($user,'/') . '\'@\'' . preg_quote($host,'/') . '\')';
    $lines = b5_grants($pdo); $usage = 0; $scoped = 0;
    if (count($lines) !== 2) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
    foreach ($lines as $line) {
        if (preg_match('/^GRANT USAGE ON \*\.\* TO ' . $account . '(?: IDENTIFIED BY PASSWORD \'\*[A-F0-9]{40}\'| IDENTIFIED VIA mysql_native_password USING \'\*[A-F0-9]{40}\')' . ($db['mode'] === 'remote' ? ' REQUIRE SSL' : '') . '$/D', $line)) { $usage++; continue; }
        if (!preg_match('/^GRANT ([A-Z, ]+) ON `' . preg_quote($scope,'/') . '`\.\* TO ' . $account . '$/D', $line, $matches)) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
        $privileges = explode(', ', $matches[1]); sort($privileges);
        if ($privileges !== ($profile === 'application' ? ['DELETE','INSERT','SELECT','UPDATE'] : ['ALL PRIVILEGES'])) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
        $scoped++;
    }
    if ($usage !== 1 || $scoped !== 1) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
}
function b5_empty(PDO $authority, string $name): void
{
    foreach (['TABLES'=>'TABLE_SCHEMA','ROUTINES'=>'ROUTINE_SCHEMA','EVENTS'=>'EVENT_SCHEMA'] as $table=>$column) {
        $q = $authority->prepare('SELECT COUNT(*) FROM information_schema.' . $table . ' WHERE ' . $column . '=?');
        $q->execute([$name]);
        if ((int)$q->fetchColumn() !== 0) throw new RuntimeException('DATABASE_NOT_EMPTY');
    }
}
/** Managed creation needs a deliberately supplied administrative authority.
 * Do not discover a missing privilege after creating half of the target. The
 * authority is ephemeral, never the migration or application account.
 */
function b5_managed_authority(PDO $authority): void
{
    $identity = $authority->query('SELECT CURRENT_USER() AS u, CURRENT_ROLE() AS r')->fetch(PDO::FETCH_ASSOC);
    if ($identity['r'] !== null || !preg_match('/^([A-Za-z0-9_]{1,32})@(.+)$/D', (string)$identity['u'], $m)
        || !b5_peer($m[2])) throw new RuntimeException('AUTHORITY_PRIVILEGES_REQUIRED');
    $account = '(?:`' . preg_quote($m[1],'/') . '`@`' . preg_quote($m[2],'/') . '`|\'' . preg_quote($m[1],'/') . '\'@\'' . preg_quote($m[2],'/') . '\')';
    $found = false;
    foreach (b5_grants($authority) as $grant) {
        if (preg_match('/^GRANT ALL PRIVILEGES ON \*\.\* TO ' . $account . '(?: IDENTIFIED BY PASSWORD \'\*[A-F0-9]{40}\'| IDENTIFIED VIA mysql_native_password USING \'\*[A-F0-9]{40}\')(?: REQUIRE SSL)? WITH GRANT OPTION$/D', $grant)) $found = true;
    }
    if (!$found) throw new RuntimeException('AUTHORITY_PRIVILEGES_REQUIRED');
}
function b5_free(PDO $authority, array $db, array $application, array $migration): void
{
    $q = $authority->prepare('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=?'); $q->execute([$db['name']]);
    if ((int)$q->fetchColumn() !== 0) throw new RuntimeException('DATABASE_TARGET_OCCUPIED');
    foreach (['global_priv','db','tables_priv','columns_priv','procs_priv','roles_mapping'] as $table) {
        if (b5_rows($authority, 'SELECT Host FROM mysql.' . $table . ' WHERE User IN (?, ?, \'\')', [$application['user'],$migration['user']])) throw new RuntimeException('ACCOUNT_TARGET_OCCUPIED');
    }
    if (b5_rows($authority, 'SELECT Host FROM mysql.proxies_priv WHERE User IN (?,?) OR Proxied_user IN (?,?)', [$application['user'],$migration['user'],$application['user'],$migration['user']])) throw new RuntimeException('ACCOUNT_TARGET_OCCUPIED');
    foreach (b5_grants($authority, true) as $grant) if (!preg_match('/^GRANT USAGE ON \*\.\* TO (?:PUBLIC|`PUBLIC`)$/D', $grant)) throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
}
