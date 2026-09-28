<?php
declare(strict_types=1);

final class HestiaMobileFailure extends RuntimeException
{
    public function __construct(public readonly string $errorCode, public readonly int $httpStatus = 400)
    {
        parent::__construct($errorCode);
    }
}

function hm_uuid(): string
{
    $bytes = random_bytes(16);
    $bytes[6] = chr((ord($bytes[6]) & 15) | 64);
    $bytes[8] = chr((ord($bytes[8]) & 63) | 128);
    $hex = bin2hex($bytes);
    return substr($hex, 0, 8) . '-' . substr($hex, 8, 4) . '-' . substr($hex, 12, 4) . '-' . substr($hex, 16, 4) . '-' . substr($hex, 20);
}

function hm_require_uuid(mixed $value): string
{
    if (!is_string($value) || !preg_match('/\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z/', $value)) {
        throw new HestiaMobileFailure('invalid_request');
    }
    return $value;
}

function hm_b64(string $value): string { return rtrim(strtr(base64_encode($value), '+/', '-_'), '='); }

function hm_unb64(mixed $value, int $maxBytes = 16384): string
{
    if (!is_string($value) || strlen($value) > (int)ceil($maxBytes * 4 / 3) || !preg_match('/\A[A-Za-z0-9_-]+\z/', $value)) {
        throw new HestiaMobileFailure('invalid_request');
    }
    $decoded = base64_decode(strtr($value, '-_', '+/'), true);
    if ($decoded === false || strlen($decoded) > $maxBytes || hm_b64($decoded) !== $value) throw new HestiaMobileFailure('invalid_request');
    return $decoded;
}

/** Native parser plus duplicate-key detection, including nested objects. */
function hm_json(string $raw, int $maxBytes = 16384): array
{
    if ($raw === '' || strlen($raw) > $maxBytes) throw new HestiaMobileFailure('invalid_request');
    try { $object = json_decode($raw, false, 16, JSON_THROW_ON_ERROR); }
    catch (JsonException $e) { throw new HestiaMobileFailure('invalid_request'); }
    if (!$object instanceof stdClass) throw new HestiaMobileFailure('invalid_request');
    preg_match_all('/"(?:[^"\\\\]|\\\\.)*"|[{}\[\],:]/s', $raw, $matches);
    $stack = [];
    foreach ($matches[0] as $token) {
        if ($token === '{' || $token === '[') { $stack[] = ['object' => $token === '{', 'key' => true, 'keys' => []]; continue; }
        if ($token === '}' || $token === ']') { array_pop($stack); continue; }
        $index = count($stack) - 1;
        if ($index < 0 || !$stack[$index]['object']) continue;
        if ($token === ',') { $stack[$index]['key'] = true; continue; }
        if ($token === ':') { $stack[$index]['key'] = false; continue; }
        if ($stack[$index]['key'] && $token[0] === '"') {
            $key = json_decode($token, true, 2, JSON_THROW_ON_ERROR);
            if (isset($stack[$index]['keys'][$key])) throw new HestiaMobileFailure('invalid_request');
            $stack[$index]['keys'][$key] = true;
        }
    }
    return json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
}

function hm_keys(array $value, array $required, array $optional = []): void
{
    if (array_diff($required, array_keys($value)) || array_diff(array_keys($value), [...$required, ...$optional])) {
        throw new HestiaMobileFailure('invalid_request');
    }
}

/** RFC 7517 EC public key only; OpenSSL validates the uncompressed curve point. */
function hm_public_key(array $jwk): OpenSSLAsymmetricKey
{
    hm_keys($jwk, ['kty', 'crv', 'x', 'y']);
    if ($jwk['kty'] !== 'EC' || $jwk['crv'] !== 'P-256') throw new HestiaMobileFailure('invalid_request');
    $x = hm_unb64($jwk['x'], 32); $y = hm_unb64($jwk['y'], 32);
    if (strlen($x) !== 32 || strlen($y) !== 32) throw new HestiaMobileFailure('invalid_request');
    $der = hex2bin('3059301306072a8648ce3d020106082a8648ce3d03010703420004') . $x . $y;
    $pem = "-----BEGIN PUBLIC KEY-----\n" . chunk_split(base64_encode($der), 64, "\n") . "-----END PUBLIC KEY-----\n";
    $key = @openssl_pkey_get_public($pem);
    if ($key === false) throw new HestiaMobileFailure('invalid_request');
    $details = openssl_pkey_get_details($key);
    if (($details['type'] ?? null) !== OPENSSL_KEYTYPE_EC || ($details['ec']['curve_name'] ?? '') !== 'prime256v1') throw new HestiaMobileFailure('invalid_request');
    return $key;
}

function hm_thumbprint(array $jwk): string
{
    hm_public_key($jwk);
    return hm_b64(hash('sha256', json_encode(['crv' => 'P-256', 'kty' => 'EC', 'x' => $jwk['x'], 'y' => $jwk['y']], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), true));
}

function hm_jws_parts(string $jws): array
{
    if (strlen($jws) > 16384) throw new HestiaMobileFailure('invalid_request');
    $parts = explode('.', $jws);
    if (count($parts) !== 3) throw new HestiaMobileFailure('invalid_request');
    return [hm_json(hm_unb64($parts[0], 512), 512), hm_json(hm_unb64($parts[1], 8192), 8192), hm_unb64($parts[2], 64), $parts[0] . '.' . $parts[1]];
}

function hm_verify_jws(string $jws, OpenSSLAsymmetricKey $key, string $typ, ?string $kid = null): array
{
    [$header, $claims, $signature, $input] = hm_jws_parts($jws);
    hm_keys($header, $kid === null ? ['alg', 'typ'] : ['alg', 'typ', 'kid']);
    if ($header['alg'] !== 'ES256' || $header['typ'] !== $typ || ($kid !== null && $header['kid'] !== $kid) || strlen($signature) !== 64) {
        throw new HestiaMobileFailure('authentication_failed', 401);
    }
    $integer = static function (string $bytes): string {
        $bytes = ltrim($bytes, "\0");
        if ($bytes === '' || (ord($bytes[0]) & 128)) $bytes = "\0" . $bytes;
        return "\x02" . chr(strlen($bytes)) . $bytes;
    };
    $rs = $integer(substr($signature, 0, 32)) . $integer(substr($signature, 32));
    if (openssl_verify($input, "\x30" . chr(strlen($rs)) . $rs, $key, OPENSSL_ALGO_SHA256) !== 1) {
        throw new HestiaMobileFailure('authentication_failed', 401);
    }
    return $claims;
}
