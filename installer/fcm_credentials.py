"""Private, immutable FCM import with exact project binding and bounded recovery.

Preparation is separate from service activation. Reports never read credentials
or contact Google. A changed/missing completed file is not repaired implicitly.
"""
import hashlib
import os
from pathlib import Path
import re

from installer.gateway_identity import _openssl
from installer.model import ErrorCode, InstallerError, canonical_bytes, exact_keys, require, strict_json_loads
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal, _FILE_FLAGS, _check_file

MAX_BYTES = 16384
PROJECT = r'[a-z][a-z0-9-]{4,61}[a-z0-9]'
REQUIRED = {'type', 'project_id', 'client_email', 'private_key', 'token_uri'}
OPTIONAL = {'private_key_id', 'client_id', 'auth_uri', 'auth_provider_x509_cert_url', 'client_x509_cert_url', 'universe_domain'}


def sha(raw): return hashlib.sha256(raw).hexdigest()


def selection(value):
    exact_keys(value, {'version', 'gateway_plan_sha256', 'project_id'})
    require(type(value['version']) is int and value['version'] == 1
            and type(value['gateway_plan_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['gateway_plan_sha256'])
            and type(value['project_id']) is str and re.fullmatch(PROJECT, value['project_id']))
    return value


def public_binding(profile, receipt):
    selection(profile)
    exact_keys(receipt, {'version', 'profile_sha256', 'project_id', 'credential_sha256', 'public_key_sha256'})
    require(type(receipt['version']) is int and receipt['version'] == 1
            and receipt['profile_sha256'] == sha(canonical_bytes(profile)) and receipt['project_id'] == profile['project_id']
            and all(type(receipt[k]) is str and re.fullmatch('[a-f0-9]{64}', receipt[k])
                for k in ('credential_sha256', 'public_key_sha256')), ErrorCode.INVALID_STATE)
    return {'selection': profile, 'receipt': receipt}


def _integer(raw, offset):
    require(offset < len(raw) and raw[offset] == 2, ErrorCode.VALIDATION_FAILED)
    offset += 1; size = raw[offset]; offset += 1
    if size & 0x80:
        count = size & 0x7f
        require(0 < count <= 2 and offset + count <= len(raw), ErrorCode.VALIDATION_FAILED)
        size = int.from_bytes(raw[offset:offset + count], 'big'); offset += count
    require(size > 0 and offset + size <= len(raw), ErrorCode.VALIDATION_FAILED)
    value = raw[offset:offset + size]
    require(value[0] < 128 and (len(value) == 1 or value[0] != 0 or value[1] >= 128), ErrorCode.VALIDATION_FAILED)
    return int.from_bytes(value, 'big'), offset + size


def validate(raw, project):
    """Validate in memory with local OpenSSL, never a network or arbitrary path."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, ErrorCode.SOURCE_LIMIT)
    require(type(project) is str and re.fullmatch(PROJECT, project), ErrorCode.INVALID_DATA)
    try: value = strict_json_loads(raw)
    except (ValueError, TypeError, RecursionError): raise InstallerError(ErrorCode.INVALID_DATA) from None
    require(type(value) is dict and REQUIRED <= set(value) <= REQUIRED | OPTIONAL, ErrorCode.INVALID_DATA)
    require(all(type(item) is str and len(item) <= 8192 for item in value.values()), ErrorCode.INVALID_DATA)
    require(value['type'] == 'service_account' and value['project_id'] == project
            and value['token_uri'] == 'https://oauth2.googleapis.com/token'
            and len(value['client_email']) <= 254
            and re.fullmatch(r'[a-zA-Z0-9-]+@[a-z][a-z0-9-]+[.]iam[.]gserviceaccount[.]com', value['client_email']),
            ErrorCode.VALIDATION_FAILED)
    private = value['private_key'].encode()
    require(private.startswith(b'-----BEGIN PRIVATE KEY-----\n')
            and _openssl(['pkey', '-outform', 'PEM'], private) == private, ErrorCode.VALIDATION_FAILED)
    _openssl(['rsa', '-check', '-noout'], private)
    public = _openssl(['rsa', '-RSAPublicKey_out', '-outform', 'DER'], private)
    require(len(public) >= 5 and public[:2] == b'\x30\x82'
            and int.from_bytes(public[2:4], 'big') == len(public) - 4, ErrorCode.VALIDATION_FAILED)
    modulus, position = _integer(public, 4); exponent, position = _integer(public, position)
    require(position == len(public) and 2048 <= modulus.bit_length() <= 4096 and exponent >= 3 and exponent % 2 == 1,
            ErrorCode.VALIDATION_FAILED)
    canonical = canonical_bytes(value)
    require(len(canonical) <= MAX_BYTES, ErrorCode.SOURCE_LIMIT)
    return canonical, {'project_id': project, 'credential_sha256': sha(canonical), 'public_key_sha256': sha(public)}


class FcmCredentials:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, root):
        self.root = Path(root)
        self.lock = StateJournal(self.root / 'import-state.json')

    def report(self):
        profile = self._read('profile.json')
        if profile is None:
            require(self._read('receipt.json') is None, ErrorCode.INVALID_STATE)
            return None
        selection(profile); receipt = self._read('receipt.json')
        if receipt is not None:
            public_binding(profile, receipt)
        return {'profile': profile, 'confirmation': sha(canonical_bytes(profile)), 'receipt': receipt,
                'state': 'IMPORTED' if receipt else 'AWAITING_IMPORT', 'historical_only': True,
                'service_configured': False, 'google_authorization_verified': False, 'phone_delivery_verified': False}

    def plan(self, value):
        value = dict(selection(value))
        with self.lock.locked(create=True) as locked:
            report = self.report()
            if report is None:
                require(set(os.listdir(locked.directory_fd)) == {'.transaction.lock'}, ErrorCode.MANUAL_ACTION_REQUIRED)
                self._write('profile.json', value)
            else: require(report['profile'] == value, ErrorCode.PLAN_EXISTS)
            return self.report()

    @staticmethod
    def _private(fd):
        try: handle = os.open('server.json', os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
        except FileNotFoundError: return None
        try:
            _check_file(handle)
            require(0 < os.fstat(handle).st_size <= MAX_BYTES, ErrorCode.INVALID_STATE)
            return os.read(handle, MAX_BYTES + 1)
        finally: os.close(handle)

    def _verified(self, fd, report):
        require(set(os.listdir(fd)) == {'.transaction.lock', 'profile.json', 'intent.json', 'server.json', 'receipt.json'},
                ErrorCode.INVALID_STATE)
        raw = self._private(fd); require(raw is not None, ErrorCode.MANUAL_ACTION_REQUIRED)
        canonical, metadata = validate(raw, report['profile']['project_id'])
        expected = {'version': 1, 'profile_sha256': report['confirmation'], **metadata}
        require(raw == canonical and self._read('intent.json') == expected and report['receipt'] == expected,
                ErrorCode.SOURCE_DRIFT)
        return report

    def verify(self):
        with self.lock.locked(create=False) as locked:
            report = self.report()
            require(report is not None and report['receipt'] is not None, ErrorCode.NOT_PLANNED)
            return self._verified(locked.directory_fd, report)

    def import_bytes(self, raw, confirmation, *, confirmed):
        require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
        with self.lock.locked(create=False) as locked:
            report = self.report(); require(report is not None, ErrorCode.NOT_PLANNED)
            require(type(confirmation) is str and confirmation == report['confirmation'], ErrorCode.CONFIRMATION_REQUIRED)
            canonical, metadata = validate(raw, report['profile']['project_id'])
            expected = {'version': 1, 'profile_sha256': confirmation, **metadata}
            if report['receipt'] is not None:
                require(report['receipt'] == expected, ErrorCode.SOURCE_DRIFT)
                return self._verified(locked.directory_fd, report)
            intent = self._read('intent.json')
            require(set(os.listdir(locked.directory_fd)) <= {'.transaction.lock', 'profile.json', 'intent.json', 'server.json'},
                    ErrorCode.MANUAL_ACTION_REQUIRED)
            private = self._private(locked.directory_fd)
            if intent is None:
                require(private is None, ErrorCode.MANUAL_ACTION_REQUIRED)
                self._write('intent.json', expected)
            else: require(intent == expected, ErrorCode.MANUAL_ACTION_REQUIRED)
            if private is None:
                handle = os.open('server.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=locked.directory_fd)
                try:
                    os.fchmod(handle, 0o600); _check_file(handle)
                    with os.fdopen(handle, 'wb', closefd=False) as stream:
                        stream.write(canonical); stream.flush(); os.fsync(handle)
                    os.fsync(locked.directory_fd)
                finally: os.close(handle)
            else: require(private == canonical, ErrorCode.MANUAL_ACTION_REQUIRED)
            self._write('receipt.json', expected)
            return self._verified(locked.directory_fd, self.report())
