"""Private P-256 preparation for the separate Gateway plan.

This store never edits a Web plan, starts a service or installs an identity.
Reports read public metadata only; explicit prepare/verify checks the private
material. No missing or invalid committed key is ever regenerated.
"""
import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess

from installer.model import ErrorCode, InstallerError, canonical_bytes, exact_keys, require
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal, _private_directory, _FILE_FLAGS, _check_file

_SPKI = bytes.fromhex('3059301306072a8648ce3d020106082a8648ce3d03010703420004')
_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b


def public_origin(value):
    require(type(value) is str and value.startswith('https://'))
    host = value[8:]; labels = host.split('.')
    require(len(host) <= 253 and len(labels) >= 2 and all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
        and re.search('[a-z]', labels[-1]) is not None)
    return value


def profile(value):
    exact_keys(value, {'version', 'instance', 'public_origin', 'dev_enabled'})
    require(type(value['version']) is int and value['version'] == 1
            and type(value['instance']) is str and re.fullmatch('[a-f0-9]{32}', value['instance'])
            and type(value['dev_enabled']) is bool)
    public_origin(value['public_origin'])
    return value


def _b64(value): return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')
def _sha(value): return hashlib.sha256(value).hexdigest()


def public_identity(environment, jwk):
    require(environment in ('main', 'dev'))
    exact_keys(jwk, {'kty', 'crv', 'x', 'y'})
    require(jwk['kty'] == 'EC' and jwk['crv'] == 'P-256')
    coordinates = []
    for name in ('x', 'y'):
        value = jwk[name]
        require(type(value) is str and re.fullmatch('[A-Za-z0-9_-]{43}', value))
        raw = base64.urlsafe_b64decode(value + '=')
        require(len(raw) == 32 and _b64(raw) == value)
        coordinates.append(int.from_bytes(raw, 'big'))
    x, y = coordinates
    require(x < _P and y < _P and (y * y - (x * x * x - 3 * x + _B)) % _P == 0)
    thumbprint = _b64(hashlib.sha256(canonical_bytes(jwk)).digest())
    return {'kid': 'hst-' + environment + '-' + thumbprint, 'public_jwk': jwk, 'thumbprint': thumbprint}


def _openssl(arguments, data=None):
    # Fixed executable/argument vectors. Credentials are stdin bytes only and
    # neither stdout nor stderr crosses a log/report/error boundary.
    try:
        result = subprocess.run(['/usr/bin/openssl', *arguments], input=data,
                                capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        raise InstallerError(ErrorCode.OPERATION_FAILED) from None
    require(result.returncode == 0 and len(result.stdout) <= 4096, ErrorCode.VALIDATION_FAILED)
    return result.stdout


def _public(private):
    require(type(private) is bytes and 0 < len(private) <= 4096, ErrorCode.INVALID_STATE)
    require(_openssl(['pkey', '-outform', 'PEM'], private) == private, ErrorCode.INVALID_STATE)
    _openssl(['pkey', '-check', '-noout'], private)
    der = _openssl(['pkey', '-pubout', '-outform', 'DER'], private)
    require(len(der) == len(_SPKI) + 64 and der.startswith(_SPKI), ErrorCode.VALIDATION_FAILED)
    return {'kty': 'EC', 'crv': 'P-256', 'x': _b64(der[-64:-32]), 'y': _b64(der[-32:])}


class GatewayIdentityStore:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, root):
        self.root = Path(root)
        self.lock = StateJournal(self.root / 'identity-state.json')

    @staticmethod
    def _environments(value): return ('main', 'dev') if value['dev_enabled'] else ('main',)

    def _receipt(self, value):
        receipt = self._read('receipt.json')
        if receipt is None: return None
        exact_keys(receipt, {'version', 'profile_sha256', 'identities'})
        require(type(receipt['version']) is int and receipt['version'] == 1
                and receipt['profile_sha256'] == _sha(canonical_bytes(value)), ErrorCode.INCOMPATIBLE_STATE)
        exact_keys(receipt['identities'], set(self._environments(value)))
        thumbprints = []
        for environment, identity in receipt['identities'].items():
            exact_keys(identity, {'kid', 'public_jwk', 'thumbprint'})
            require(identity == public_identity(environment, identity['public_jwk']), ErrorCode.INVALID_STATE)
            thumbprints.append(identity['thumbprint'])
        require(len(set(thumbprints)) == len(thumbprints), ErrorCode.INCOMPATIBLE_STATE)
        return receipt

    def report(self):
        # Deliberately no private-key read, process, network, mutation or probe.
        value = self._read('profile.json')
        if value is None:
            require(self._read('receipt.json') is None, ErrorCode.INVALID_STATE)
            return None
        profile(value)
        return {'profile': value, 'receipt': self._receipt(value)}

    def configurations(self):
        # Templates only. A later deployment plan must verify/install the keys
        # and configure the corresponding systemd credentials and Web listeners.
        report = self.report()
        require(report is not None and report['receipt'] is not None, ErrorCode.NOT_PLANNED)
        contexts = {'mode': 'public-contexts-distribution'}
        foundation = {}
        for environment, identity in report['receipt']['identities'].items():
            contexts[environment] = {'endpoint': 'http://127.0.0.1:' + ('9082' if environment == 'main' else '9081'),
                                     'kid': identity['kid'],
                                     'key_file': '/run/credentials/hestia-mobile-gateway.service/' + environment + '-key'}
            foundation[environment] = {'environment': 'main' if environment == 'main' else 'dev-bastien',
                                       'gateway_keys': {identity['kid']: identity['public_jwk']}, 'canonical_contexts': True}
            if environment == 'main': foundation[environment]['canonical_distribution'] = True
        return {'gateway': {'schema_version': 1, 'listen': '127.0.0.1:9083',
                            'public_origin': report['profile']['public_origin'],
                            'state_dir': '/var/lib/hestia-mobile-gateway', 'contexts': contexts},
                'foundation': foundation}

    @staticmethod
    def _key(fd, environment):
        try: handle = os.open(environment + '.pem', os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
        except FileNotFoundError: return None
        try:
            _check_file(handle)
            require(0 < os.fstat(handle).st_size <= 4096, ErrorCode.INVALID_STATE)
            return os.read(handle, 4097)
        finally: os.close(handle)

    @staticmethod
    def _write_key(fd, environment, private):
        handle = os.open(environment + '.pem', os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                         0o600, dir_fd=fd)
        try:
            os.fchmod(handle, 0o600); _check_file(handle)
            with os.fdopen(handle, 'wb', closefd=False) as stream:
                stream.write(private); stream.flush(); os.fsync(handle)
            os.fsync(fd)
        finally: os.close(handle)

    def _verify(self, fd, value, receipt):
        allowed = {'.transaction.lock', 'profile.json', 'receipt.json'} | {
            e + '.pem' for e in self._environments(value)}
        require(set(os.listdir(fd)) <= allowed, ErrorCode.INCOMPATIBLE_STATE)
        identities = {}
        for environment in self._environments(value):
            private = self._key(fd, environment)
            require(private is not None, ErrorCode.MANUAL_ACTION_REQUIRED)
            identities[environment] = public_identity(environment, _public(private))
        require(identities == receipt['identities'], ErrorCode.INCOMPATIBLE_STATE)
        return receipt

    def verify(self):
        with self.lock.locked(create=False) as locked:
            report = self.report()
            require(report is not None and report['receipt'] is not None, ErrorCode.NOT_PLANNED)
            return self._verify(locked.directory_fd, report['profile'], report['receipt'])

    def prepare(self, value):
        # Validate before even creating a private directory or lock.
        value = dict(profile(value))
        with self.lock.locked(create=True) as locked:
            fd = locked.directory_fd
            report = self.report()
            if report is None:
                require(set(os.listdir(fd)) == {'.transaction.lock'}, ErrorCode.INCOMPATIBLE_STATE)
                self._write('profile.json', value)
            else:
                require(report['profile'] == value, ErrorCode.INCOMPATIBLE_STATE)
                if report['receipt'] is not None:
                    return self._verify(fd, value, report['receipt'])
            allowed = {'.transaction.lock', 'profile.json'} | {e + '.pem' for e in self._environments(value)}
            require(set(os.listdir(fd)) <= allowed, ErrorCode.INCOMPATIBLE_STATE)
            identities = {}
            for environment in self._environments(value):
                private = self._key(fd, environment)
                if private is None:
                    private = _openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256'])
                    public_identity(environment, _public(private))
                    self._write_key(fd, environment, private)
                identities[environment] = public_identity(environment, _public(private))
            require(len({i['thumbprint'] for i in identities.values()}) == len(identities), ErrorCode.INCOMPATIBLE_STATE)
            receipt = {'version': 1, 'profile_sha256': _sha(canonical_bytes(value)), 'identities': identities}
            self._write('receipt.json', receipt)
            return self._verify(fd, value, receipt)
