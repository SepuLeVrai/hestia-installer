"""Bounded local Gateway -> signed MAIN probe; no enrollment or APK transfer."""
import http.client
import secrets
import uuid

from installer.gateway_identity import _b64, public_origin
from installer.model import ErrorCode, canonical_bytes, require, strict_json_loads

RESULT = {'state': 'GATEWAY_MAIN_VERIFIED', 'endpoint': '127.0.0.1:9083',
          'signed_main_roundtrip': True, 'origin_rejected': True, 'forwarding_rejected': True,
          'public_mobile_available': False, 'boot_enabled': False}
PATH = '/mobile/bootstrap/check'


def request(method, path, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', 9083, timeout=8)
    try:
        connection.request(method, path, body=body, headers={'Connection': 'close', **(headers or {})})
        response = connection.getresponse(); raw = response.read(65537)
        require(len(raw) <= 65536 and response.getheader('Cache-Control') == 'no-store'
                and response.getheader('Set-Cookie') is None
                and response.getheader('Content-Type', '').startswith('application/json'), ErrorCode.VALIDATION_FAILED)
        identifier = response.getheader('X-Request-ID')
        require(type(identifier) is str and str(uuid.UUID(identifier)) == identifier
                and uuid.UUID(identifier).version == 4, ErrorCode.VALIDATION_FAILED)
        return response.status, strict_json_loads(raw), identifier
    finally: connection.close()


def check(origin):
    public_origin(origin)
    status, health, _ = request('GET', '/health')
    require(status == 200 and health == {'status': 'ok'}, ErrorCode.VALIDATION_FAILED)
    raw = canonical_bytes({'enrollment_token': _b64(secrets.token_bytes(32))})
    headers = {'Content-Type': 'application/json', 'Origin': origin, 'X-Hestia-Client-IP': '127.0.0.1'}
    status, reply, identifier = request('POST', PATH, raw, headers)
    # Both are successful signed Web answers: disabled policy yields unavailable;
    # enabled policy cannot recognize the fresh random nonexistent enrollment.
    require(status == 200 and type(reply) is dict and set(reply) == {'data', 'request_id'}
            and reply['request_id'] == identifier and type(reply['data']) is dict
            and set(reply['data']) == {'state', 'expires_at'}
            and reply['data']['state'] in ('invalid', 'unavailable')
            and type(reply['data']['expires_at']) is int and reply['data']['expires_at'] == 0,
            ErrorCode.VALIDATION_FAILED)
    for extra, expected_status, expected_code in (({'Origin': 'null'}, 400, 'invalid_request'),
            ({'Forwarded': 'for=127.0.0.1'}, 404, 'not_found')):
        status, reply, identifier = request('POST', PATH, raw, {**headers, **extra})
        require(status == expected_status and type(reply) is dict
                and reply.get('request_id') == identifier and type(reply.get('error')) is dict
                and reply['error'].get('code') == expected_code, ErrorCode.VALIDATION_FAILED)
    return dict(RESULT)
