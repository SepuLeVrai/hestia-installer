<?php
declare(strict_types=1);

// Private, read-only account policy. Loading this library has no side effect.
// Raw grants may contain authentication hashes: never return or log them.
function hestia_account_policy(
    #[SensitiveParameter] array $grants,
    #[SensitiveParameter] array $publicGrants,
    string $account,
    mixed $role,
    string $user,
    string $database,
    string $profile
): bool {
    if (!preg_match('/^[A-Za-z0-9_]{1,32}$/D', $user) || strtolower($user) === 'root'
        || !preg_match('/^[A-Za-z0-9_]{1,64}$/D', $database)
        || !in_array($profile, ['application', 'provisioning'], true) || $role !== null
        || !in_array($account, [$user . '@localhost', $user . '@127.0.0.1'], true)
        || count($grants) !== 2 || count($publicGrants) > 1) return false;
    foreach ($publicGrants as $grant) {
        if (!is_string($grant) || !preg_match('/^GRANT USAGE ON \*\.\* TO (?:PUBLIC|`PUBLIC`)$/D', $grant)) return false;
    }
    // Exact schema grant, with escaped SQL wildcard underscore. A grant on
    // `hestia_prod`.* without escaping also covers hestiaXprod and is refused.
    $schema = '`' . str_replace('_', '\\_', $database) . '`.*';
    $host = substr($account, strlen($user) + 1);
    $quotedAccount = '(?:`' . preg_quote($user, '/') . '`@`' . preg_quote($host, '/')
        . '`|\'' . preg_quote($user, '/') . '\'@\'' . preg_quote($host, '/') . '\')';
    $usage = 0; $scoped = 0;
    foreach ($grants as $grant) {
        if (!is_string($grant) || strlen($grant) > 4096) return false;
        // Only native-password authentication is accepted by this first local
        // profile; plugins, PROXY, roles and unknown clauses fail closed.
        if (preg_match('/^GRANT USAGE ON \*\.\* TO ' . $quotedAccount
            . '(?: IDENTIFIED BY PASSWORD \'\*[A-F0-9]{40}\'| IDENTIFIED VIA mysql_native_password USING \'\*[A-F0-9]{40}\')$/D', $grant)) {
            $usage++; continue;
        }
        if (!preg_match('/^GRANT ([A-Z, ]+) ON ' . preg_quote($schema, '/') . ' TO ' . $quotedAccount . '$/D', $grant, $matches)) return false;
        $privileges = explode(', ', $matches[1]); sort($privileges);
        $expected = $profile === 'application' ? ['DELETE', 'INSERT', 'SELECT', 'UPDATE'] : ['ALL PRIVILEGES'];
        if ($privileges !== $expected) return false;
        $scoped++;
    }
    return $usage === 1 && $scoped === 1;
}

/** @return list<string> */
function hestia_bounded_grants(PDO $pdo, bool $public): array
{
    try {
        $statement = $pdo->query($public ? 'SHOW GRANTS FOR PUBLIC' : 'SHOW GRANTS');
    } catch (PDOException $exception) {
        // No PUBLIC grants is distinct from permission failure / unknown query.
        if ($public && ($exception->errorInfo[1] ?? null) === 1141) return [];
        throw new RuntimeException('AUDIT_UNAVAILABLE');
    }
    $rows = [];
    while (($value = $statement->fetchColumn()) !== false) {
        if (!is_string($value) || strlen($value) > 4096 || count($rows) >= 16) {
            throw new RuntimeException('ACCOUNT_POLICY_REJECTED');
        }
        $rows[] = $value;
    }
    return $rows;
}
