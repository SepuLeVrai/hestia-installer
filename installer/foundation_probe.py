"""Explicit MAIN assertion probe. No arbitrary endpoint, operation or subject.

Only nonce/rejection audit records are produced by the existing Web contract.
No enrollment, user, device, business or schema mutation is requested.
"""
import http.client
import os
import subprocess
import time
import uuid

from installer.gateway_identity import _b64, _public
from installer.model import ErrorCode, canonical_bytes, require, strict_json_loads
from installer.transaction import _private_directory, _FILE_FLAGS, _check_file

PATH = '/internal/mobile/v1/access/check'
RESULT = {'state': 'FOUNDATION_MAIN_VERIFIED', 'endpoint': '127.0.0.1:9082',
          'signed_assertion': True, 'replay_rejected': True, 'unsigned_rejected': True,
          'gateway_service_available': False, 'public_mobile_available': False, 'boot_enabled': False}


def raw_signature(der):
    require(type(der) is bytes and 8 <= len(der) <= 72 and der[0] == 48 and der[1] == len(der) - 2,
            ErrorCode.VALIDATION_FAILED)
    result = b''; offset = 2
    order = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
    for _ in range(2):
        require(offset + 2 <= len(der) and der[offset] == 2, ErrorCode.VALIDATION_FAILED)
        length = der[offset + 1]; offset += 2
        number = der[offset:offset + length]; offset += length
        require(1 <= length <= 33 and len(number) == length and number[0] < 128
                and (length == 1 or number[0] != 0 or number[1] >= 128), ErrorCode.VALIDATION_FAILED)
        value = int.from_bytes(number, 'big')
        require(0 < value < order, ErrorCode.VALIDATION_FAILED)
        result += value.to_bytes(32, 'big')
    require(offset == len(der), ErrorCode.VALIDATION_FAILED)
    return result


def assertion(store, identity, *, environment="main"):
    require(environment in ("main", "dev"), ErrorCode.INVALID_DATA)
    target = "main" if environment == "main" else "dev-bastien"
    request_id, subject, device = (str(uuid.uuid4()) for _ in range(3))
    body = {'request_id': request_id, 'mobile_subject_uuid': subject, 'device_id': device, 'environment': target}
    raw = canonical_bytes(body); now = int(time.time())
    from hashlib import sha256
    claims = {**body, 'iss': 'hestia-mobile-gateway', 'aud': 'hestia-internal-mobile:' + target,
              'iat': now, 'exp': now + 30, 'jti': str(uuid.uuid4()), 'operation': 'access/check',
              'htm': 'POST', 'htu_path': PATH, 'body_sha256': sha256(raw).hexdigest()}
    signing = (_b64(canonical_bytes({'alg': 'ES256', 'typ': 'hestia-service+jwt', 'kid': identity['kid']}))
               + '.' + _b64(canonical_bytes(claims))).encode()
    with _private_directory(store.root, create=False) as directory:
        handle = os.open(environment + '.pem', os.O_RDONLY | _FILE_FLAGS, dir_fd=directory)
        try:
            _check_file(handle); key = os.read(handle, 4097)
            require(_public(key) == identity['public_jwk'], ErrorCode.SOURCE_DRIFT)
            os.lseek(handle, 0, os.SEEK_SET)
            result = subprocess.run(['/usr/bin/openssl', 'dgst', '-sha256', '-sign', '/proc/self/fd/' + str(handle)],
                input=signing, pass_fds=(handle,), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=15, check=False, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
            require(result.returncode == 0, ErrorCode.VALIDATION_FAILED)
        finally: os.close(handle)
    return body, raw, signing.decode() + '.' + _b64(raw_signature(result.stdout))


def request(body, request_id, token, *, port=9082):
    require(type(port) is int and port in (9081, 9082), ErrorCode.INVALID_DATA)
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        headers = {'Host': 'hestia-internal-mobile.local', 'Content-Type': 'application/json',
                   'X-Request-ID': request_id, 'Connection': 'close'}
        if token is not None: headers['Authorization'] = 'Bearer ' + token
        connection.request('POST', PATH, body=body, headers=headers)
        response = connection.getresponse(); raw = response.read(65537)
        require(len(raw) <= 65536 and response.getheader('Cache-Control') == 'no-store'
                and response.getheader('Set-Cookie') is None and response.getheader('Content-Type', '').startswith('application/json'),
                ErrorCode.VALIDATION_FAILED)
        return response.status, strict_json_loads(raw)
    finally: connection.close()


def check(store, identity):
    body, raw, token = assertion(store, identity)
    status, reply = request(raw, body['request_id'], token)
    require(status == 200 and type(reply) is dict and set(reply) == {'data', 'request_id'}
            and reply['request_id'] == body['request_id'] and type(reply['data']) is dict, ErrorCode.VALIDATION_FAILED)
    data = reply['data']
    require(set(data) == set(body) | {'mobile_enabled', 'account_active', 'device_active', 'environment_allowed'}
            and all(data[k] == value for k, value in body.items()) and type(data['mobile_enabled']) is bool
            and all(data[k] is False for k in ('account_active', 'device_active', 'environment_allowed')),
            ErrorCode.VALIDATION_FAILED)
    # Replay must be rejected by Web's durable SQL nonce, not only by a client.
    status, reply = request(raw, body['request_id'], token)
    require(status == 401 and type(reply) is dict and type(reply.get('error')) is dict
            and reply['error'].get('code') == 'authentication_failed', ErrorCode.VALIDATION_FAILED)
    # An unsigned caller on the same loopback still has no authority.
    status, reply = request(raw, body['request_id'], None)
    require(status == 401 and type(reply) is dict and type(reply.get('error')) is dict
            and reply['error'].get('code') == 'authentication_failed', ErrorCode.VALIDATION_FAILED)
    return dict(RESULT)


def check_dev(store, identity, main_identity):
    body, raw, token = assertion(store, identity, environment='dev')
    status, reply = request(raw, body['request_id'], token, port=9081)
    require(status == 200 and type(reply) is dict and reply.get('request_id') == body['request_id']
            and type(reply.get('data')) is dict, ErrorCode.VALIDATION_FAILED)
    data = reply['data']
    require(set(data) == set(body) | {'mobile_enabled', 'account_active', 'device_active', 'environment_allowed'}
            and all(data[k] == v for k, v in body.items()) and type(data['mobile_enabled']) is bool
            and all(data[k] is False for k in ('account_active', 'device_active', 'environment_allowed')),
            ErrorCode.VALIDATION_FAILED)
    for supplied in (token, None):
        status, reply = request(raw, body['request_id'], supplied, port=9081)
        require(status == 401 and reply.get('error', {}).get('code') == 'authentication_failed', ErrorCode.VALIDATION_FAILED)
    # Both directions must reject the other environment's real private key.
    for source, env, port in ((main_identity, 'main', 9081), (identity, 'dev', 9082)):
        body, raw, token = assertion(store, source, environment=env)
        status, reply = request(raw, body['request_id'], token, port=port)
        require(status in (401, 403) and type(reply.get('error')) is dict, ErrorCode.VALIDATION_FAILED)
    return dict(DEV_RESULT)


DEV_RESULT = {'state': 'FOUNDATION_DEV_VERIFIED', 'endpoint': '127.0.0.1:9081',
              'signed_assertion': True, 'replay_rejected': True, 'unsigned_rejected': True,
              'cross_context_rejected': True, 'gateway_service_available': False,
              'public_mobile_available': False, 'boot_enabled': False}
