"""Explicit HTTP-01, public ingress and renewal on the owned Debian 13 server.

Fixed ACME authorities, commands and resources. Ambiguous effects are observed
or left manual; no automatic issuance replay or maintenance reopening.
"""
from dataclasses import replace
import http.client
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess

from installer import boot_runtime as boot
from installer.acme_packages import AcmePackages
from installer.engine import TransactionEngine
from installer.model import ErrorCode, Receipt, ResourceSpec, StepSpec, canonical_bytes, now, require
from installer.operations import Operation, OperationRegistry, Recovery, RecoveryDecision
from installer.public_tls_profile import Profile, PRODUCTION, STAGING, CERT_NAME, overlay_evidence
from installer.service_identity import ServiceIdentityOperation
from installer.transaction import _private_directory

f, fs, h = boot.f, boot.fs, boot.h
PHASES = ('stage', 'http', 'certificate', 'dry-run', 'switch', 'https', 'enable', 'verify')


def command(argv, *, timeout=60):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=timeout, check=False, umask=0o022,
        env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''})
    require(result.returncode == 0 and len(result.stdout) <= 262144, ErrorCode.VALIDATION_FAILED)
    return result.stdout


class PublicTLS(Profile):
    def unit(self, role):
        require(role in ('http', 'https', 'renew', 'timer'))
        return self.prefix + ('-renew.timer' if role == 'timer' else '-' + role + '.service')

    def units(self):
        result = {}
        for role in ('http', 'https'):
            ordering = ('Requires=' + self.boot.target + ' ' + self.http.unit('apache') + '\nAfter=' + self.boot.target
                        + ' ' + self.http.unit('apache') + '\n') if role == 'https' else ''
            result[self.unit(role)] = (f'[Unit]\nDescription=HESTIA public {role}\n{ordering}'
                '[Service]\nType=simple\nUMask=0077\nRestart=no\nKillMode=control-group\n'
                'SendSIGKILL=yes\nTimeoutStopSec=35\nDelegate=no\n'
                f'ExecStartPre=/usr/bin/python3.13 -I -B {self.root}/worker.py {role}\n'
                f'ExecStart=/usr/sbin/nginx -c {self.root}/nginx-{role}.conf\n'
                'NoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\n').encode()
        result[self.unit('renew')] = (f'[Unit]\nDescription=HESTIA certificate renewal\nRequires={self.unit("http")}\nAfter={self.unit("http")}\n'
            '[Service]\nType=oneshot\nUMask=0077\nRestart=no\nTimeoutStartSec=900\n'
            f'ExecStart=/usr/bin/python3.13 -I -B {self.root}/worker.py renew\n'
            'NoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\n').encode()
        result[self.unit('timer')] = (f'[Unit]\nDescription=HESTIA certificate renewal schedule\n'
            f'[Timer]\nOnCalendar=*-*-* 00,12:00:00\nRandomizedDelaySec=3600\nPersistent=yes\nUnit={self.unit("renew")}\n').encode()
        return result

    def files(self):
        return {'apache-public.conf': self.apache_include(), 'nginx-http.conf': self.nginx('http'),
                'nginx-https.conf': self.nginx('https'), 'certbot.ini': b''}

    def links(self):
        return {h.drain.UNIT_ROOT / ('timers.target.wants' if role == 'timer' else 'multi-user.target.wants') / self.unit(role):
                '../' + self.unit(role) for role in ('http', 'https', 'timer')}

    def absent(self):
        self.boot.configuration(); self.boot.live()
        acme = self.value['acme']
        AcmePackages(acme['base'], acme['base_sha256']).observe_installed()
        with fs._directory(self.public.parent) as fd: fs._absent(fd, self.public.name)
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for name in self.units(): fs._absent(fd, name); fs._absent(fd, name + '.d')
        with fs._directory(self.dropin.parent) as fd: fs._absent(fd, self.dropin.name)
        for path in self.links():
            with fs._directory(path.parent) as fd: fs._absent(fd, path.name)

    def network_ready(self):
        # This first public profile is IPv4 only. Reject an advertised AAAA
        # rather than silently allowing the CA/client to select an unserved IP.
        rows = socket.getaddrinfo(self.hostname, 443, type=socket.SOCK_STREAM)
        require(rows and all(row[0] == socket.AF_INET for row in rows), ErrorCode.VALIDATION_FAILED)
        for port in (80, 443):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(('0.0.0.0', port))

    def binding(self, context):
        return {'version': 1, 'installation_id': context.installation_id,
                'spec_sha256': f._sha(canonical_bytes(context.spec)), 'profile_sha256': self.digest}

    def stage(self, context):
        self.absent(); self.network_ready(); account = self.identity.account()
        with fs._directory(self.public.parent) as fd:
            os.mkdir(self.public.name, 0o755, dir_fd=fd); os.chmod(self.public.name, 0o755, dir_fd=fd); os.fsync(fd)
        self._write('stage.attempt', self.binding(context)); self.copy_bundle()
        with fs._directory(self.public) as fd:
            for name in ('body', 'proxy', 'fastcgi', 'uwsgi', 'scgi', 'htdocs'):
                os.mkdir(name, 0o700 if name != 'htdocs' else 0o755, dir_fd=fd)
                os.chmod(name, 0o700 if name != 'htdocs' else 0o755, dir_fd=fd)
                if name != 'htdocs': os.chown(name, account.pw_uid, account.pw_gid, dir_fd=fd)
            os.fsync(fd)
        for parent, name in ((self.public / 'htdocs', '.well-known'), (self.public / 'htdocs/.well-known', 'acme-challenge')):
            with fs._directory(parent) as fd:
                os.mkdir(name, 0o755, dir_fd=fd); os.chmod(name, 0o755, dir_fd=fd); os.fsync(fd)
        with _private_directory(self.root, create=False) as fd:
            for name, data in self.files().items(): f._write(fd, name, data, 0, mode=0o600)
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for name, data in self.units().items(): f._write(fd, name, data, 0, mode=0o644)
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        self._write('stage.json', self.binding(context))

    def configuration(self):
        self.completed('stage'); self.bundle(); account = self.identity.account()
        with _private_directory(self.root, create=False) as fd:
            for name, data in self.files().items():
                require(f._read(fd, name, 0, mode=0o600) == data, ErrorCode.INVALID_STATE)
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for name, data in self.units().items():
                require(f._read(fd, name, 0, mode=0o644) == data, ErrorCode.INVALID_STATE); fs._absent(fd, name + '.d')
        for path in (self.public, self.public / 'htdocs', self.public / 'htdocs/.well-known', self.public / 'htdocs/.well-known/acme-challenge'):
            with fs._directory(path) as fd:
                info = os.fstat(fd)
                require(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o755, ErrorCode.INVALID_STATE)
        with fs._directory(self.public) as fd:
            for name in ('body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                        (account.pw_uid, account.pw_gid, 0o700), ErrorCode.INVALID_STATE)

    def systemctl(self, verb, role):
        require(verb in ('start', 'show', 'reload'))
        unit = self.unit(role)
        if verb == 'reload':
            require(role == 'https'); args = ['kill', '--kill-whom=main', '--signal=HUP', '--', unit]
        elif verb == 'show':
            props = ('Id', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'LoadState', 'ActiveState', 'SubState', 'Job')
            props += ('Unit',) if role == 'timer' else ('MainPID', 'ControlPID', 'ControlGroup', 'Result')
            args = ['show', '--property=' + ','.join(props), '--', unit]
        else: args = ['start', '--', unit]
        raw = command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', *args], timeout=900)
        if verb != 'show': return None
        rows = raw.decode().splitlines(); value = dict(row.split('=', 1) for row in rows)
        require(len(rows) == len(value) and set(value) == set(props), ErrorCode.INVALID_STATE)
        require(all(value[k] == v for k, v in {'Id': unit, 'FragmentPath': str(h.drain.UNIT_ROOT / unit),
                'DropInPaths': '', 'NeedDaemonReload': 'no', 'LoadState': 'loaded', 'Job': ''}.items()), ErrorCode.INVALID_STATE)
        if role == 'timer': require(value['Unit'] == self.unit('renew'), ErrorCode.INVALID_STATE)
        return value

    def running(self, role):
        value = self.systemctl('show', role)
        if role == 'timer':
            return value['ActiveState'] == 'active' and value['SubState'] in ('waiting', 'running', 'elapsed')
        if value['ActiveState'] == 'inactive' and value['SubState'] == 'dead':
            require(value['MainPID'] == value['ControlPID'] == '0', ErrorCode.INVALID_STATE); return False
        require(value['ActiveState'] == 'active' and value['SubState'] == 'running' and value['Result'] == 'success'
                and value['ControlPID'] == '0' and re.fullmatch('[1-9][0-9]*', value['MainPID'])
                and value['ControlGroup'] == '/system.slice/' + self.unit(role), ErrorCode.INVALID_STATE)
        require('0::/system.slice/' + self.unit(role) in (Path('/proc') / value['MainPID'] / 'cgroup').read_text().splitlines(), ErrorCode.INVALID_STATE)
        return True

    def certbot(self, *, renew=False, dry_run=False):
        args = ['/usr/bin/certbot', '--config', str(self.root / 'certbot.ini'), '--config-dir', str(self.acme_root),
                '--work-dir', str(self.root / 'certbot-work'), '--logs-dir', str(self.root / 'certbot-logs'),
                '--non-interactive', '--no-directory-hooks', '--cert-name', CERT_NAME]
        if renew:
            args += ['renew']
            if dry_run: args += ['--dry-run', '--server', STAGING]
        else:
            args += ['certonly', '--webroot', '-w', str(self.public / 'htdocs'), '-d', self.hostname,
                     '--email', self.value['choices']['email'], '--agree-tos', '--key-type', 'ecdsa',
                     '--elliptic-curve', 'secp256r1', '--server', PRODUCTION]
        return args

    def renewal_configuration(self):
        with fs._directory(self.acme_root / 'renewal') as fd:
            require(set(os.listdir(fd)) == {CERT_NAME + '.conf'}, ErrorCode.INVALID_STATE)
            raw = f._read(fd, CERT_NAME + '.conf', 0, mode=0o644)
        require(not re.search(rb'(?im)^\s*(?:pre_hook|post_hook|renew_hook|deploy_hook|manual_auth_hook|manual_cleanup_hook)\s*=', raw)
                and ('server = ' + PRODUCTION).encode() in raw, ErrorCode.INVALID_STATE)
        return {'version': 1, 'configuration_sha256': f._sha(raw), 'profile_sha256': self.digest}

    def certificate(self, *, minimum_lifetime=604800, allow_expired=False):
        require(type(minimum_lifetime) is int and minimum_lifetime in (0, 604800) and type(allow_expired) is bool)
        require(self._read('renewal.json') == self.renewal_configuration(), ErrorCode.INVALID_STATE)
        live = self.acme_root / 'live' / CERT_NAME; archive = self.acme_root / 'archive' / CERT_NAME
        versions = set()
        with fs._directory(live) as fd:
            for name in ('cert', 'chain', 'fullchain', 'privkey'):
                target = os.readlink(name + '.pem', dir_fd=fd)
                match = re.fullmatch(r'\.\./\.\./archive/' + CERT_NAME + '/' + name + r'([1-9][0-9]*)\.pem', target)
                require(match is not None, ErrorCode.INVALID_STATE); versions.add(match[1])
                info = os.stat(name + '.pem', dir_fd=fd, follow_symlinks=False)
                require(info.st_uid == info.st_gid == 0, ErrorCode.INVALID_STATE)
        require(len(versions) == 1, ErrorCode.INVALID_STATE)
        version = next(iter(versions))
        with fs._directory(archive) as fd:
            material = {}
            for name in ('cert', 'chain', 'fullchain', 'privkey'):
                material[name] = f._read(fd, name + version + '.pem', 0, mode=0o600 if name == 'privkey' else 0o644)
            require(material['fullchain'] == material['cert'] + material['chain'], ErrorCode.INVALID_STATE)
        command(['/usr/bin/openssl', 'verify', *(['-no_check_time'] if allow_expired else []),
                 '-purpose', 'sslserver', '-verify_hostname', self.hostname,
                 '-untrusted', str(live / 'chain.pem'), str(live / 'cert.pem')])
        if not allow_expired:
            command(['/usr/bin/openssl', 'x509', '-in', str(live / 'cert.pem'), '-noout', '-checkend', str(minimum_lifetime)])
        public = command(['/usr/bin/openssl', 'x509', '-in', str(live / 'cert.pem'), '-noout', '-pubkey'])
        require(public == command(['/usr/bin/openssl', 'pkey', '-in', str(live / 'privkey.pem'), '-pubout']), ErrorCode.INVALID_STATE)

    def switch(self, context):
        account, extension, _, _ = self.http._inspect_configuration()
        scope = self.http._scope(account)
        self.certificate()
        fragment = self.http._files(account, extension)[h.drain.UNIT_ROOT / self.http.unit('apache')]
        require(f._sha(fragment) == self.value['backend_fragment_sha256'], ErrorCode.INVALID_STATE)
        # The intent is already durable. An exception leaves maintenance closed.
        with scope.acquire(confirmed=True) as lease:
            self._write('switch-lease.json', {'lease_id': lease.lease_id, 'profile_sha256': self.digest})
            h.drain._systemctl('stop', self.http.unit('apache'))
            with fs._directory(self.dropin.parent) as fd:
                f._write(fd, self.dropin.name, self.apache_dropin(), 0, mode=0o644)
            command(['/usr/sbin/apache2', '-t', '-f', str(self.http.spec.root / 'conf/apache.conf'),
                     '-c', 'Include ' + str(self.root / 'apache-public.conf')])
            h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
            self._write('switch.json', self.binding(context))
            lease.resume(confirmed=True)
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'start', '--', self.http.unit('apache')])
        self.boot.activation.check()

    def enabled(self):
        self.completed('enable')
        for path, target in self.links().items():
            with fs._directory(path.parent) as fd: self.boot.exact_link(fd, path.name, target)

    def probe(self):
        self.configuration(); self.certificate(minimum_lifetime=0); self.boot.live()
        require(self.running('http') and self.running('https') and self.running('timer'), ErrorCode.VALIDATION_FAILED)
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(self.hostname, 443, timeout=10, context=context)
        try:
            raw = socket.create_connection(('127.0.0.1', 443), timeout=10)
            try: connection.sock = context.wrap_socket(raw, server_hostname=self.hostname)
            except Exception: raw.close(); raise
            connection.request('GET', '/login.php', headers={'Host': self.hostname, 'Connection': 'close'})
            response = connection.getresponse(); body = response.read(131073)
            require(response.status == 200 and len(body) <= 131072
                    and re.search(rb'name="csrf_token" value="[a-f0-9]+"', body), ErrorCode.VALIDATION_FAILED)
        finally: connection.close()
        return {'state': 'PUBLIC_TLS_AVAILABLE', 'checked_at': now(), 'url': 'https://' + self.hostname,
                'certificate_verified': True, 'renewal_configured': True, 'administrator_login_tested': False}

    def worker(self, phase):
        self.configuration()
        # Do not admit a link made visible by an incomplete boot enrollment.
        if self._read('enable.attempt') is not None: self.enabled()
        if phase == 'http':
            require(self._read('http.attempt') is not None, ErrorCode.INVALID_STATE)
            command(['/usr/sbin/nginx', '-t', '-c', str(self.root / 'nginx-http.conf')]); return
        if phase == 'backend':
            self.http._inspect_configuration()
            account = self.layout.identity.account(); scope = self.http._scope(account)
            require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
            require(overlay_evidence(scope, self.value['backend_fragment_sha256']) is not None, ErrorCode.INVALID_STATE); return
        require(phase in ('https', 'renew'))
        # Expiry must not prevent the renewal that repairs it after downtime.
        # Serving still requires a currently valid leaf; enrollment requires 7d.
        self.certificate(minimum_lifetime=0, allow_expired=phase == 'renew'); self.completed('dry-run')
        if phase == 'https':
            self.completed('switch')
            require(self._read('https.attempt') is not None, ErrorCode.INVALID_STATE)
            command(['/usr/sbin/nginx', '-t', '-c', str(self.root / 'nginx-https.conf')]); return
        self.enabled(); require(self.running('http'), ErrorCode.INVALID_STATE)
        # Certbot serializes its own immutable dedicated config/work paths.
        command(self.certbot(renew=True), timeout=840)
        self.certificate(); self.configuration()
        command(['/usr/sbin/nginx', '-t', '-c', str(self.root / 'nginx-https.conf')])
        if self.running('https'): self.systemctl('reload', 'https')


class PublicOperation(Operation):
    def __init__(self, runtime, phase, previous):
        self.runtime, self.phase = runtime, phase
        actions = {'stage': 'Préparer le frontal public et ses contrôles', 'http': 'Ouvrir le challenge HTTP-01 sur le port 80',
            'certificate': 'Obtenir le certificat Let’s Encrypt', 'dry-run': 'Vérifier le renouvellement sur l’autorité de test',
            'switch': 'Basculer Apache sous maintenance puis reprendre le service', 'https': 'Ouvrir le frontal HTTPS sur le port 443',
            'enable': 'Activer le frontal au démarrage et le renouvellement automatique', 'verify': 'Vérifier HTTPS et la page de connexion'}
        super().__init__(StepSpec(name='web.public.' + phase, operation='web.public.' + phase, module='web',
            boundary='web.public.' + phase, action=actions[phase], dependencies=(previous,), rollback_supported=False,
            resources=(ResourceSpec('public_tls', 'directory' if phase == 'stage' else 'file',
                str(runtime.root if phase == 'stage' else runtime.root / (phase + '.json'))),),
            warnings=('Profil public : ' + runtime.digest, 'Domaine : ' + runtime.hostname,
                'Contact ACME : ' + runtime.value['choices']['email'],
                'Clients IPv4 : ' + ', '.join(runtime.ingress.client_networks),
                'Conditions Let’s Encrypt : https://letsencrypt.org/repository/',
                'Tout effet partiel reste manuel. La bascule confirme la reprise après maintenance.')))

    def receipt(self):
        return Receipt(created_resources=('public_tls',), hashes_non_secret=(('public_tls_profile', self.runtime.digest),))

    def prepare(self, context):
        r = self.runtime
        require(r._read(self.phase + '.attempt') is None, ErrorCode.MANUAL_ACTION_REQUIRED)
        if self.phase == 'stage': r.absent(); r.network_ready()
        else:
            r.configuration()
            if self.phase in ('http', 'https'): require(not r.running(self.phase), ErrorCode.MANUAL_ACTION_REQUIRED)
            if self.phase in ('certificate', 'dry-run'): require(r.running('http'), ErrorCode.VALIDATION_FAILED)
            if self.phase == 'certificate':
                with _private_directory(r.root, create=False) as fd: fs._absent(fd, 'letsencrypt')
            if self.phase in ('dry-run', 'switch', 'https', 'enable', 'verify'): r.certificate()
            if self.phase == 'switch': r.boot.live()
            if self.phase == 'enable':
                require(r.running('http') and r.running('https'), ErrorCode.VALIDATION_FAILED)
                for path in r.links():
                    with fs._directory(path.parent) as fd: fs._absent(fd, path.name)

    def apply(self, context):
        r = self.runtime; self.prepare(context)
        if self.phase == 'stage': r.stage(context)
        else:
            r._write(self.phase + '.attempt', r.binding(context))
            if self.phase in ('http', 'https'): r.systemctl('start', self.phase)
            elif self.phase == 'certificate':
                command(r.certbot(), timeout=840)
                r._write('renewal.json', r.renewal_configuration()); r.certificate()
            elif self.phase == 'dry-run': command(r.certbot(renew=True, dry_run=True), timeout=840); r.certificate()
            elif self.phase == 'switch': r.switch(context)
            elif self.phase == 'enable':
                for path, target in r.links().items():
                    with fs._directory(path.parent) as fd: os.symlink(target, path.name, dir_fd=fd); os.fsync(fd)
                h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
                r._write('enable.json', r.binding(context)); r.systemctl('start', 'timer')
            elif self.phase == 'verify': r.probe()
            if self.phase not in ('switch', 'enable'): r._write(self.phase + '.json', r.binding(context))
        self.current(context); return self.receipt()

    def current(self, context):
        r = self.runtime; r.configuration()
        require(r.completed(self.phase) == r.binding(context), ErrorCode.INVALID_STATE)
        if self.phase in ('http', 'https'): require(r.running(self.phase), ErrorCode.VALIDATION_FAILED)
        if self.phase in ('certificate', 'dry-run', 'verify'): r.certificate()
        if self.phase == 'switch':
            account = r.layout.identity.account()
            require(overlay_evidence(r.http._scope(account), r.value['backend_fragment_sha256']) is not None, ErrorCode.INVALID_STATE)
            r.boot.live()
        if self.phase in ('enable', 'verify'): r.enabled(); require(r.running('timer'), ErrorCode.VALIDATION_FAILED)

    def validate(self, context):
        self.current(context); return context.evidence == self.receipt().as_dict()

    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)

    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try: self.current(context); return Recovery(RecoveryDecision.APPLIED, self.receipt())
        except Exception: pass
        try:
            if self.runtime._read(self.phase + '.attempt') is not None: return Recovery(RecoveryDecision.MANUAL)
            self.prepare(context); return Recovery(RecoveryDecision.RETRY_SAFE)
        except Exception: return Recovery(RecoveryDecision.MANUAL)


def engine(journal, profile):
    runtime = PublicTLS(profile)
    identity = ServiceIdentityOperation(runtime.identity)
    identity.spec = replace(identity.spec, name='web.public.identity', operation='web.public.identity', boundary='web.public.identity',
                            action='Créer le compte isolé du frontal', warnings=(*identity.spec.warnings, 'Profil public : ' + runtime.digest))
    operations = [identity]
    for phase in PHASES: operations.append(PublicOperation(runtime, phase, operations[-1].spec.name))
    result = TransactionEngine(journal, OperationRegistry(tuple(operations)))
    document = result.report()
    if document is not None:
        require([s.as_dict() for s in result.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
        result.registry.validate_document(document)
    return result, runtime
