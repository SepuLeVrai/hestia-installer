<?php
// Test only: private fixture bytes enter stdin, never arguments or output.
declare(strict_types=1);
require __DIR__ . '/gateway_web_crypto.php';
try {
    $input = json_decode(stream_get_contents(STDIN), true, 16, JSON_THROW_ON_ERROR);
    $main = $input['identities']['main']; $dev = $input['identities']['dev'];
    foreach ([$main, $dev] as $identity) {
        if (!hash_equals(hm_thumbprint($identity['public_jwk']), $identity['thumbprint'])) throw new RuntimeException('thumbprint');
    }
    $claims = ['iss'=>'hestia-mobile-gateway','aud'=>'hestia-mobile-main','environment'=>'main',
               'jti'=>hm_uuid(),'iat'=>time(),'exp'=>time()+30];
    $head = hm_b64(json_encode(['alg'=>'ES256','typ'=>'hestia-service+jwt','kid'=>$main['kid']], JSON_THROW_ON_ERROR));
    $body = hm_b64(json_encode($claims, JSON_THROW_ON_ERROR));
    $signed = $head . '.' . $body;
    if (!openssl_sign($signed, $der, $input['private'], OPENSSL_ALGO_SHA256)) throw new RuntimeException('sign');
    $offset = 2; $raw = '';
    for ($i=0; $i<2; $i++) {
        if (ord($der[$offset++]) !== 2) throw new RuntimeException('signature');
        $length = ord($der[$offset++]);
        $integer = ltrim(substr($der, $offset, $length), "\0"); $offset += $length;
        if (strlen($integer)>32) throw new RuntimeException('coordinate');
        $raw .= str_pad($integer, 32, "\0", STR_PAD_LEFT);
    }
    $jws = $signed . '.' . hm_b64($raw);
    $key = hm_public_key($main['public_jwk']);
    if (hm_verify_jws($jws, $key, 'hestia-service+jwt', $main['kid']) !== $claims) throw new RuntimeException('valid signature refused');
    $negative = [[$jws,hm_public_key($dev['public_jwk']),'hestia-service+jwt',$main['kid']],
                 [$jws,$key,'hestia-service+jwt',$dev['kid']],[$jws,$key,'dpop+jwt',$main['kid']],
                 [$head.'.'.hm_b64('{"environment":"dev-bastien"}').'.'.hm_b64($raw),$key,'hestia-service+jwt',$main['kid']]];
    foreach ($negative as [$proof,$public,$type,$kid]) {
        $refused=false;
        try { hm_verify_jws($proof,$public,$type,$kid); } catch (HestiaMobileFailure $e) { $refused=true; }
        if (!$refused) throw new RuntimeException('invalid signature accepted');
    }
    echo "GATEWAY_IDENTITY_PINNED_WEB_CRYPTO_PASS\n";
} catch (Throwable $error) {
    fwrite(STDERR, "GATEWAY_IDENTITY_CRYPTO_FAILED\n");
    exit(1);
}
