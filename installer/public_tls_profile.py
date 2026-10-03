"""Closed public ingress profile, separate from the acquired backend and boot.

The only admitted Apache overlay is derived here and bound to its immutable
private receipt. A foreign, partial or changed overlay is never adopted.
"""
import ipaddress
import os
from pathlib import Path
import re

from installer import boot_runtime as boot
from installer.model import ErrorCode, canonical_bytes, exact_keys, require
from installer.package_plan import PackagePlan
from installer.proxy_ingress import ProxyIngress
from installer.service_identity import ServiceIdentity
from installer.transaction import _private_directory, _FILE_FLAGS, _check_file

f, fs, h = boot.f, boot.fs, boot.h
PRODUCTION = 'https://acme-v02.api.letsencrypt.org/directory'
STAGING = 'https://acme-staging-v02.api.letsencrypt.org/directory'
CERT_NAME = 'hestia-web'


def choices(value):
    exact_keys(value, {'email', 'access', 'networks'})
    require(type(value['email']) is str and len(value['email']) <= 254
            and re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', value['email']))
    require(value['access'] in ('public', 'allowlist') and type(value['networks']) is list)
    networks = value['networks']
    require((not networks and value['access'] == 'public') or
            (value['access'] == 'allowlist' and 1 <= len(networks) <= 30))
    try:
        require(all(type(x) is str and isinstance(ipaddress.ip_network(x, strict=True), ipaddress.IPv4Network)
                    and str(ipaddress.ip_network(x)) == x and ipaddress.ip_network(x).prefixlen > 0 for x in networks))
        require(networks == sorted(set(networks)))
    except Exception:
        require(False)
    return value


class Profile:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, value):
        exact_keys(value, {'version', 'boot', 'acme', 'parents', 'choices', 'backend_fragment_sha256', 'code'})
        require(type(value['version']) is int and value['version'] == 1)
        choices(value['choices']); exact_keys(value['parents'], {'boot', 'acme'})
        require(all(type(x) is str and re.fullmatch('[a-f0-9]{64}', x)
                    for x in (*value['parents'].values(), value['backend_fragment_sha256'])))
        require(type(value['code']) is dict and value['code'] and all(
            type(n) is str and re.fullmatch(r'installer/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_.-]+\.(py|php|json)', n)
            and type(d) is str and re.fullmatch('[a-f0-9]{64}', d) for n, d in value['code'].items()))
        self.value = value
        self.boot = boot.BootRuntime(value['boot'])
        self.layout, self.http = self.boot.layout, self.boot.http
        self.hostname = self.http.spec.hostname
        self.public = self.layout.root / 'public'
        self.root = self.public / 'private'
        self.prefix = 'hestia-' + self.layout.instance + '-public'
        self.identity = ServiceIdentity(f._sha(canonical_bytes(['public-ingress', self.layout.instance]))[:32])
        networks = ('0.0.0.0/0',) if value['choices']['access'] == 'public' else tuple(sorted(set(value['choices']['networks'] + ['127.0.0.1/32'])))
        self.ingress = ProxyIngress('127.0.0.2', networks)
        self.dropin = h.drain.UNIT_ROOT / (self.http.unit('apache') + '.d/60-hestia-public.conf')
        self.acme_root = self.root / 'letsencrypt'

    @property
    def digest(self): return f._sha(canonical_bytes(self.value))

    def apache_include(self):
        access = '  AuthMerging Off\n' + self.ingress.apache_access(self.hostname, self.http.spec.port)
        text = f'<Directory "{self.http.spec.webroot}">\n{access}</Directory>\n'
        for family in ('profiles', 'constructeurs', 'distributeurs', 'references'):
            text += (f'<Directory "{self.http.spec.root}/data/uploads/{family}">\n'
                     '<FilesMatch "(?i)^[^.][^/]*[.](png|jpe?g|webp|gif)$">\n' + access + '</FilesMatch>\n</Directory>\n')
        return text.encode()

    def apache_dropin(self):
        return (f'[Service]\nExecStart=\nExecStart=/usr/sbin/apache2 -DFOREGROUND -f {self.http.spec.root}/conf/apache.conf '
                f'-c "Include {self.root}/apache-public.conf"\n'
                f'ExecStartPre=/usr/bin/python3.13 -I -B {self.root}/worker.py backend\n').encode()

    def nginx(self, role):
        require(role in ('http', 'https'))
        if role == 'http':
            server = f'''server {{
  listen 0.0.0.0:80;
  server_name {self.hostname};
  if ($host != {self.hostname}) {{ return 421; }}
  location ^~ /.well-known/acme-challenge/ {{
    root {self.public}/htdocs;
    default_type text/plain;
    try_files $uri =404;
  }}
  location / {{ return 308 https://{self.hostname}$request_uri; }}
}}
'''
        else:
            live = self.acme_root / 'live' / CERT_NAME
            server = self.ingress.nginx_server(self.hostname, self.http.spec.port, tls_port=443,
                certificate=live / 'fullchain.pem', private_key=live / 'privkey.pem')
        return (f'user {self.identity.user};\nworker_processes 1;\ndaemon off;\n'
                f'pid {self.root}/{role}.pid;\nerror_log {self.root}/{role}.log warn;\n'
                'events { worker_connections 512; }\nhttp {\naccess_log off;\nserver_tokens off;\n'
                f'client_body_temp_path {self.public}/body;\nproxy_temp_path {self.public}/proxy;\n'
                f'fastcgi_temp_path {self.public}/fastcgi;\nuwsgi_temp_path {self.public}/uwsgi;\nscgi_temp_path {self.public}/scgi;\n' + server + '}\n').encode()

    def runner(self):
        return (boot.SOURCE / 'private/public_tls_worker.py').read_bytes().replace(b'__PUBLIC_PROFILE_SHA256__', self.digest.encode())

    def copy_bundle(self):
        files = boot.code_files()
        require({n: f._sha(b) for n, b in files.items()} == self.value['code'], ErrorCode.INCOMPATIBLE_STATE)
        self._write('profile.json', self.value)
        for name, data in {'worker.py': self.runner(), **{'code/' + n: b for n, b in files.items()}}.items():
            path = self.root / name
            require(len(data) <= 1048576)
            with _private_directory(path.parent, create=True) as fd:
                handle = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
                try:
                    os.fchmod(handle, 0o600); _check_file(handle)
                    with os.fdopen(handle, 'wb', closefd=False) as stream:
                        stream.write(data); stream.flush(); os.fsync(handle)
                    os.fsync(fd)
                finally: os.close(handle)

    def bundle(self):
        require(self._read('profile.json') == self.value, ErrorCode.INVALID_STATE)
        expected = {'worker.py': f._sha(self.runner()), **{'code/' + n: d for n, d in self.value['code'].items()}}
        entries = list((self.root / 'code').rglob('*'))
        require(not any(p.is_symlink() for p in entries) and
                {p.relative_to(self.root).as_posix() for p in entries if not p.is_dir()} == set(expected) - {'worker.py'}, ErrorCode.INVALID_STATE)
        for name, digest in expected.items():
            with _private_directory((self.root / name).parent, create=False) as fd:
                require(f._sha(f._read(fd, Path(name).name, 0, mode=0o600, limit=1048576)) == digest, ErrorCode.INVALID_STATE)

    def completed(self, phase):
        value = self._read(phase + '.json')
        require(value is not None and value == self._read(phase + '.attempt'), ErrorCode.INVALID_STATE)
        exact_keys(value, {'version', 'installation_id', 'spec_sha256', 'profile_sha256'})
        require(value['version'] == 1 and value['profile_sha256'] == self.digest
                and type(value['installation_id']) is str and re.fullmatch('[a-f0-9-]{32,36}', value['installation_id'])
                and type(value['spec_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['spec_sha256']), ErrorCode.INVALID_STATE)
        return value


def overlay_evidence(scope, fragment_sha256):
    """Read-only admission of exactly the enrolled public Apache overlay."""
    reader = object.__new__(Profile)
    reader.root = boot.app.FreshProfile(scope.instance).root / 'public/private'
    value = reader._read('profile.json')
    if value is None: return None
    profile = Profile(value)
    require(profile.root == reader.root and profile.http.spec.maintenance_directory == scope.directory, ErrorCode.INVALID_STATE)
    if profile._read('switch.attempt') is None:
        with fs._directory(profile.dropin.parent) as fd: fs._absent(fd, profile.dropin.name)
        return None
    profile.completed('switch'); profile.bundle()
    require(value['backend_fragment_sha256'] == fragment_sha256, ErrorCode.INVALID_STATE)
    with _private_directory(profile.root, create=False) as fd:
        require(f._read(fd, 'apache-public.conf', 0, mode=0o600) == profile.apache_include(), ErrorCode.INVALID_STATE)
    from installer.shared_public_runtime import overlay
    successor = overlay(profile, scope, fragment_sha256)
    if successor is not None: return successor
    with fs._directory(profile.dropin.parent) as fd:
        require(f._read(fd, profile.dropin.name, 0, mode=0o644) == profile.apache_dropin(), ErrorCode.INVALID_STATE)
    return {'path': str(profile.dropin), 'profile_sha256': profile.digest,
            'configuration_sha256': f._sha(profile.apache_include()), 'dropin_sha256': f._sha(profile.apache_dropin())}
