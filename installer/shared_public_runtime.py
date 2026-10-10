"""Explicit successor of the frozen public listener, with closed recovery.

Parent bundles and configurations are evidence, never editable runtime input.
Only the four named public fragments and the known Apache overlay are replaced.
An incomplete native effect is manual; a completed lost reply is read-only.
"""
import os
from pathlib import Path
import re
import socket
import stat
import time

from installer import public_tls_runtime as old
from installer import foundation_drain, gateway_service_drain, gateway_service_probe
from installer import gateway_frozen_reference
from installer.engine import TransactionEngine
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_service_profile import GatewayServiceProfile
from installer.model import ErrorCode, InstallerError, Receipt, ResourceSpec, StepSpec, canonical_bytes, exact_keys, require
from installer.operations import Operation, OperationRegistry, Recovery, RecoveryDecision
from installer.shared_mobile_tls import SharedMobileTLS, CERT_NAME
from installer.shared_public_plan import candidate, digest
from installer.transaction import StateJournal, _private_directory, _FILE_FLAGS, _check_file

f, fs, h, boot = old.f, old.fs, old.h, old.boot
PHASES = ('enroll', 'handoff', 'certificate', 'dry-run', 'publish', 'renewal', 'verify')
CONFIG_LIMIT = 262144


def code_identity(): return {name: f._sha(raw) for name, raw in boot.code_files().items()}


def selection(preparation, gateway_binding, publication_sha256=None):
    value = {'version': 1, 'preparation': preparation, 'gateway_binding': gateway_binding, 'code': code_identity()}
    if publication_sha256 is not None:
        value.update(version=2, gateway_publication_sha256=gateway_frozen_reference.digest(publication_sha256))
    return value


class MobileCertificate:
    _read, _write = old.Profile._read, old.Profile._write

    def __init__(self, runtime):
        self.runtime, self.root = runtime, runtime.shared.root

    def configuration(self):
        with fs._directory(self.runtime.shared.acme_root / 'renewal') as fd:
            require(set(os.listdir(fd)) == {CERT_NAME + '.conf'}, ErrorCode.INVALID_STATE)
            raw = f._read(fd, CERT_NAME + '.conf', 0, mode=0o644)
        require(not re.search(rb'(?im)^\s*(?:pre_hook|post_hook|renew_hook|deploy_hook|manual_auth_hook|manual_cleanup_hook)\s*=', raw)
            and ('server = ' + old.PRODUCTION).encode() in raw, ErrorCode.INVALID_STATE)
        return {'version': 1, 'configuration_sha256': f._sha(raw), 'profile_sha256': self.runtime.digest}

    def verify(self, *, allow_expired=False, minimum_lifetime=604800):
        require(type(allow_expired) is bool and type(minimum_lifetime) is int and minimum_lifetime in (0, 604800))
        require(self._read('renewal.json') == self.configuration(), ErrorCode.INVALID_STATE)
        acme = self.runtime.shared.acme_root
        live, archive = acme / 'live' / CERT_NAME, acme / 'archive' / CERT_NAME
        versions = set()
        with fs._directory(live) as fd:
            for name in ('cert', 'chain', 'fullchain', 'privkey'):
                target = os.readlink(name + '.pem', dir_fd=fd)
                match = re.fullmatch(r'\.\./\.\./archive/' + CERT_NAME + '/' + name + r'([1-9][0-9]*)\.pem', target)
                require(match is not None, ErrorCode.INVALID_STATE); versions.add(match[1])
                info = os.stat(name + '.pem', dir_fd=fd, follow_symlinks=False)
                require(info.st_uid == info.st_gid == 0, ErrorCode.INVALID_STATE)
        require(len(versions) == 1, ErrorCode.INVALID_STATE); version = next(iter(versions))
        with fs._directory(archive) as fd:
            material = {name: f._read(fd, name + version + '.pem', 0, mode=0o600 if name == 'privkey' else 0o644)
                        for name in ('cert', 'chain', 'fullchain', 'privkey')}
            require(material['fullchain'] == material['cert'] + material['chain'], ErrorCode.INVALID_STATE)
        old.command(['/usr/bin/openssl', 'verify', *(['-no_check_time'] if allow_expired else []),
            '-purpose', 'sslserver', '-verify_hostname', self.runtime.shared.mobile.hostname,
            '-untrusted', str(live / 'chain.pem'), str(live / 'cert.pem')])
        if not allow_expired:
            old.command(['/usr/bin/openssl', 'x509', '-in', str(live / 'cert.pem'), '-noout', '-checkend', str(minimum_lifetime)])
        public = old.command(['/usr/bin/openssl', 'x509', '-in', str(live / 'cert.pem'), '-noout', '-pubkey'])
        require(public == old.command(['/usr/bin/openssl', 'pkey', '-in', str(live / 'privkey.pem'), '-pubout']), ErrorCode.INVALID_STATE)


class SharedPublic(old.Profile):
    def __init__(self, value):
        require(type(value) is dict and type(value.get('version')) is int and value['version'] in (1, 2))
        exact_keys(value, {'version', 'preparation', 'gateway_binding', 'code'} |
            ({'gateway_publication_sha256'} if value['version'] == 2 else set()))
        if value['version'] == 2: gateway_frozen_reference.digest(value['gateway_publication_sha256'])
        selected = value['preparation']
        expected = candidate(selected['web_profile'], selected['gateway_identity'], selected['client_networks'], selected['parents'])
        require(canonical_bytes(selected) == canonical_bytes(expected), ErrorCode.INVALID_STATE)
        # Reuse the closed source-name/hash grammar without changing the parent.
        old.Profile({**selected['web_profile'], 'code': value['code']})
        require(type(value['gateway_binding']) is dict and value['gateway_binding'].get('gateway_identity') == selected['gateway_identity']
                and value['gateway_binding'].get('web_instance') == selected['instance'], ErrorCode.INCOMPATIBLE_STATE)
        self.value = value
        self.web = old.PublicTLS(selected['web_profile'])
        self.shared = SharedMobileTLS(self.web.value, selected['gateway_identity'], tuple(selected['client_networks']))
        self.layout, self.http, self.boot = self.web.layout, self.web.http, self.web.boot
        binding = value['gateway_binding']
        foundation = FoundationRuntime.for_gateway(self.boot.activation, binding['main'], binding['gateway_identity'])
        expected_gateway = GatewayServiceProfile.from_binding(foundation, binding).binding()
        require(canonical_bytes(binding) == canonical_bytes(expected_gateway), ErrorCode.INCOMPATIBLE_STATE)
        self.public = self.web.public / 'shared'
        self.root = self.public / 'private'
        self.mobile = MobileCertificate(self)
        self.identity, self.dropin = self.web.identity, self.web.dropin

    def runner(self):
        return (boot.SOURCE / 'private/shared_public_worker.py').read_bytes().replace(b'__SHARED_PUBLIC_SHA256__', self.digest.encode())

    def apache_dropin(self):
        old_worker = str(self.web.root / 'worker.py').encode()
        raw = self.web.apache_dropin(); require(raw.count(old_worker) == 1)
        return raw.replace(old_worker, str(self.root / 'worker.py').encode())

    def units(self):
        units = self.web.units()
        for role in ('http', 'https'):
            raw = units[self.web.unit(role)]
            raw = raw.replace(f'ExecStartPre=/usr/bin/python3.13 -I -B {self.web.root}/worker.py {role}\n'.encode(), b'')
            raw = raw.replace(f'ExecStart=/usr/sbin/nginx -c {self.web.root}/nginx-{role}.conf\n'.encode(),
                f'ExecStart=/usr/bin/python3.13 -I -B {self.root}/worker.py {role}\n'.encode())
            units[self.web.unit(role)] = raw
        units[self.web.unit('renew')] = units[self.web.unit('renew')].replace(str(self.web.root / 'worker.py').encode(), str(self.root / 'worker.py').encode()).replace(b'TimeoutStartSec=900', b'TimeoutStartSec=1800')
        return {name: raw.replace(b'Description=HESTIA ', b'Description=HESTIA shared ') for name, raw in units.items()}

    def originals(self):
        return {**self.web.units(), 'apache-overlay.conf': self.web.apache_dropin()}

    def files(self):
        return {**{f'{stage}-{role}.conf': self.shared.nginx(role, mobile_ready=ready)
            for stage, ready in (('challenge', False), ('ready', True)) for role in ('http', 'https')},
            **{'original-' + name: raw for name, raw in self.originals().items()}}

    def gateway(self):
        foundation = foundation_drain.attached(self.http)
        runtime = gateway_service_drain.attached(self.http, foundation)
        require(runtime is not None and canonical_bytes(runtime.profile.binding()) == canonical_bytes(self.value['gateway_binding']),
                ErrorCode.SOURCE_DRIFT)
        gateway_frozen_reference.matches(runtime, self.value.get('gateway_publication_sha256'))
        runtime.owned(); return runtime

    def network_ready(self):
        rows = socket.getaddrinfo(self.shared.mobile.hostname, 443, type=socket.SOCK_STREAM)
        require(rows and all(row[0] == socket.AF_INET for row in rows), ErrorCode.VALIDATION_FAILED)

    def binding(self, context): return self.web.binding(context) | {'profile_sha256': self.digest}

    @staticmethod
    def write_configuration(fd, name, raw):
        # The composed 63-route configuration exceeds the historical 16 KiB
        # data helper. Keep that helper unchanged; this private writer is bounded
        # separately and can only create, never replace or repair, a file.
        require(type(raw) is bytes and len(raw) <= CONFIG_LIMIT, ErrorCode.INVALID_DATA)
        require(type(name) is str and re.fullmatch('[A-Za-z0-9_.-]+', name) and name not in ('.', '..'))
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
        try:
            os.fchmod(handle, 0o600); _check_file(handle); fs._no_acl(handle)
            require(os.fstat(handle).st_gid == 0, ErrorCode.UNSAFE_STATE_PATH)
            with os.fdopen(handle, 'wb', closefd=False) as stream:
                stream.write(raw); stream.flush(); os.fsync(handle)
            os.fsync(fd)
        finally: os.close(handle)

    def absent(self):
        self.web.configuration(); self.web.enabled(); self.boot.configuration(); self.boot.live(); self.gateway(); self.network_ready()
        require(all(self.web.running(role) for role in ('http', 'https', 'timer')), ErrorCode.VALIDATION_FAILED)
        require(not self.web.running('renew'), ErrorCode.BUSY)
        with fs._directory(self.web.public) as fd:
            fs._absent(fd, self.public.name); fs._absent(fd, self.shared.public.name)

    def enroll(self, context):
        self.absent()
        for path in (self.public, self.shared.public):
            with fs._directory(path.parent) as fd:
                os.mkdir(path.name, 0o755, dir_fd=fd); os.chmod(path.name, 0o755, dir_fd=fd); os.fsync(fd)
        self._write('enroll.attempt', self.binding(context)); self.copy_bundle()
        for path in (self.shared.public / 'htdocs', self.shared.public / 'htdocs/.well-known', self.shared.public / 'htdocs/.well-known/acme-challenge'):
            with fs._directory(path.parent) as fd:
                os.mkdir(path.name, 0o755, dir_fd=fd); os.chmod(path.name, 0o755, dir_fd=fd); os.fsync(fd)
        with _private_directory(self.shared.root, create=True) as fd: f._write(fd, 'certbot.ini', b'', 0, mode=0o600)
        with _private_directory(self.root, create=False) as fd:
            for name, raw in self.files().items(): self.write_configuration(fd, name, raw)
        self._write('enroll.json', self.binding(context))

    def enrolled(self):
        self.completed('enroll'); self.bundle(); self.web.bundle(); self.web.enabled()
        account = self.identity.account()
        with _private_directory(self.root, create=False) as fd:
            for name, raw in self.files().items(): require(f._read(fd, name, 0, mode=0o600, limit=CONFIG_LIMIT) == raw, ErrorCode.SOURCE_DRIFT)
        with _private_directory(self.web.root, create=False) as fd:
            for name, raw in self.web.files().items(): require(f._read(fd, name, 0, mode=0o600) == raw, ErrorCode.SOURCE_DRIFT)
        with _private_directory(self.shared.root, create=False) as fd:
            require(f._read(fd, 'certbot.ini', 0, mode=0o600) == b'', ErrorCode.SOURCE_DRIFT)
        for public in (self.web.public, self.shared.public):
            for path in (public, public / 'htdocs', public / 'htdocs/.well-known', public / 'htdocs/.well-known/acme-challenge'):
                with fs._directory(path) as fd:
                    info = os.fstat(fd)
                    require(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o755, ErrorCode.INVALID_STATE)
        with fs._directory(self.web.public) as fd:
            for name in ('body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                    (account.pw_uid, account.pw_gid, 0o700), ErrorCode.INVALID_STATE)

    def ownership(self):
        intent = self._read('handoff.attempt')
        require(intent is not None and self._read('ownership.json') == intent, ErrorCode.MANUAL_ACTION_REQUIRED)
        # Same closed receipt schema as completed(), without claiming starts.
        require(intent['profile_sha256'] == self.digest, ErrorCode.INVALID_STATE)
        exact_keys(intent, {'version', 'installation_id', 'spec_sha256', 'profile_sha256'})
        require(type(intent['version']) is int and intent['version'] == 1
            and type(intent['installation_id']) is str and re.fullmatch('[a-f0-9-]{32,36}', intent['installation_id'])
            and type(intent['spec_sha256']) is str and re.fullmatch('[a-f0-9]{64}', intent['spec_sha256']), ErrorCode.INVALID_STATE)

    def configuration(self):
        self.enrolled(); self.ownership()
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for name, raw in self.units().items():
                require(f._read(fd, name, 0, mode=0o644) == raw, ErrorCode.SOURCE_DRIFT); fs._absent(fd, name + '.d')
        with fs._directory(self.dropin.parent) as fd:
            require(f._read(fd, self.dropin.name, 0, mode=0o644) == self.apache_dropin(), ErrorCode.SOURCE_DRIFT)

    def ready(self):
        value = self._read('ready.json')
        if value is None:
            require(self._read('publish.attempt') is None, ErrorCode.MANUAL_ACTION_REQUIRED)
            return False
        require(value == self._read('publish.attempt') and value['profile_sha256'] == self.digest, ErrorCode.INVALID_STATE)
        self.completed('certificate'); self.completed('dry-run'); return True

    def nginx_path(self, role):
        require(role in ('http', 'https'))
        return self.root / (('ready' if self.ready() else 'challenge') + '-' + role + '.conf')

    @staticmethod
    def replace_owned(path, before, after):
        # The fixed temporary name is never removed/reused after interruption.
        with fs._directory(path.parent) as fd:
            require(f._read(fd, path.name, 0, mode=0o644) == before, ErrorCode.SOURCE_DRIFT)
            name = '.' + path.name + '.shared-new'
            f._write(fd, name, after, 0, mode=0o644)
            require(f._read(fd, path.name, 0, mode=0o644) == before, ErrorCode.SOURCE_DRIFT)
            os.replace(name, path.name, src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)

    def control(self, verb, role):
        require(verb in ('start', 'stop') and role in ('http', 'https', 'timer'))
        old.command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', verb, '--', self.web.unit(role)], timeout=120)

    def listener(self, role, *, failed_is_stopped=False):
        require(role in ('http', 'https'))
        value = self.web.systemctl('show', role)
        if not self.web.running(role, failed_is_stopped=failed_is_stopped): return False
        proc = Path('/proc') / value['MainPID']
        # Type=simple can still report the Python guard as active. A completed
        # start must own the actual NGINX master and its expected listening FD.
        if not os.path.samefile(proc / 'exe', '/usr/sbin/nginx'): return False
        argv = ['/usr/sbin/nginx', '-c', str(self.nginx_path(role))]
        command_line = (proc / 'cmdline').read_bytes().rstrip(b'\0')
        require(command_line in (b'\0'.join(x.encode() for x in argv),
            ('nginx: master process ' + ' '.join(argv)).encode()), ErrorCode.SOURCE_DRIFT)
        try: sockets = {os.readlink(path) for path in (proc / 'fd').iterdir()}
        except FileNotFoundError: return False
        endpoint = '00000000:' + ('0050' if role == 'http' else '01BB')
        listeners = [row.split() for row in Path('/proc/net/tcp').read_text().splitlines()[1:]]
        found = any(len(row) >= 10 and row[1] == endpoint and row[3] == '0A'
            and 'socket:[' + row[9] + ']' in sockets for row in listeners)
        require(self.web.systemctl('show', role) == value, ErrorCode.SOURCE_DRIFT)
        return found

    def start_listener(self, role):
        self.control('start', role); deadline = time.monotonic() + 10
        while True:
            try:
                if self.listener(role): return
            except InstallerError as error:
                # exec/proctitle can change while /proc is being sampled.
                # Re-observe, never repeat the start or relax listener checks.
                if error.code != ErrorCode.SOURCE_DRIFT or time.monotonic() >= deadline:
                    raise
            require(time.monotonic() < deadline, ErrorCode.VALIDATION_FAILED)
            time.sleep(.05)

    def stopped(self, role):
        if role == 'timer':
            value = self.web.systemctl('show', role)
            require((value['ActiveState'], value['SubState']) == ('inactive', 'dead'), ErrorCode.MANUAL_ACTION_REQUIRED)
        else: require(not self.web.running(role), ErrorCode.MANUAL_ACTION_REQUIRED)

    def handoff(self, context):
        self.control('stop', 'timer'); self.stopped('timer'); self.stopped('renew')
        for role in ('https', 'http'): self.control('stop', role); self.stopped(role)
        self.enrolled(); self.web.configuration()
        for name, raw in self.units().items(): self.replace_owned(h.drain.UNIT_ROOT / name, self.web.units()[name], raw)
        self.replace_owned(self.dropin, self.web.apache_dropin(), self.apache_dropin())
        old.command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        self._write('ownership.json', self.binding(context)); self.configuration()
        for role in ('http', 'https'): self.start_listener(role)

    def publish(self, context):
        for role in ('https', 'http'): self.control('stop', role); self.stopped(role)
        self.configuration(); self.web.certificate(); self.mobile.verify(); self.gateway()
        self._write('ready.json', self.binding(context))
        for role in ('http', 'https'): self.start_listener(role)

    def worker(self, phase):
        require(phase in ('http', 'https', 'backend', 'renew'))
        self.configuration()
        if phase == 'backend':
            self.http._inspect_configuration()
            scope = self.http._scope(self.layout.identity.account())
            require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
            return
        ready = self.ready()
        if phase in ('http', 'https'):
            if phase == 'https':
                self.web.certificate(minimum_lifetime=0)
                if ready: self.mobile.verify(minimum_lifetime=0)
            path = self.nginx_path(phase)
            old.command(['/usr/sbin/nginx', '-t', '-c', str(path)])
            return ['/usr/sbin/nginx', '-c', str(path)]
        require(ready, ErrorCode.DEPENDENCY_BLOCKED)
        self.completed('publish')
        require(self._read('renewal.attempt') is not None, ErrorCode.INVALID_STATE)
        # The same lock excludes any controller effect and concurrent workers.
        with StateJournal(self.root / 'effect-lock.json').locked(create=False):
            self.configuration(); require(self.listener('http'), ErrorCode.VALIDATION_FAILED)
            self.web.certificate(minimum_lifetime=0, allow_expired=True); self.mobile.verify(minimum_lifetime=0, allow_expired=True)
            failed = False
            for argv in (self.web.certbot(renew=True), self.shared.certbot(renew=True)):
                try: old.command(argv, timeout=840)
                except Exception: failed = True
            require(not failed, ErrorCode.VALIDATION_FAILED)
            self.web.certificate(); self.mobile.verify(); self.configuration()
            old.command(['/usr/sbin/nginx', '-t', '-c', str(self.nginx_path('https'))])
            if self.listener('https', failed_is_stopped=True): self.web.systemctl('reload', 'https')


class SharedOperation(Operation):
    def __init__(self, runtime, phase, previous):
        self.runtime, self.phase = runtime, phase
        resources = [ResourceSpec('shared_' + phase, 'directory' if phase == 'enroll' else 'file',
            str(runtime.root if phase == 'enroll' else runtime.root / (phase + '.json')))]
        if phase == 'enroll': resources.append(ResourceSpec('mobile_certificate_root', 'directory', str(runtime.shared.public)))
        if phase == 'handoff':
            resources += [ResourceSpec('unit_' + role, 'file', str(h.drain.UNIT_ROOT / runtime.web.unit(role)), preexisting=True,
                backup=str(runtime.root / ('original-' + runtime.web.unit(role)))) for role in ('http', 'https', 'renew', 'timer')]
            resources.append(ResourceSpec('apache_overlay', 'file', str(runtime.dropin), preexisting=True,
                backup=str(runtime.root / 'original-apache-overlay.conf')))
        actions = {'enroll': 'Enrôler les sources du frontal commun et conserver les originaux',
            'handoff': 'Transférer les quatre unités et le garde Apache, puis servir Web et HTTP-01 Mobile',
            'certificate': 'Obtenir le certificat Mobile par HTTP-01', 'dry-run': 'Tester le renouvellement Mobile',
            'publish': 'Ouvrir HTTPS Mobile avec le certificat distinct', 'renewal': 'Activer le renouvellement des deux certificats',
            'verify': 'Vérifier le frontal commun et la Gateway MAIN liée'}
        super().__init__(StepSpec(name='shared.public.' + phase, operation='shared.public.' + phase, module='gateway',
            boundary='shared.public.' + phase, action=actions[phase], dependencies=() if previous is None else (previous,),
            resources=tuple(resources), warnings=('Profil lié : ' + runtime.digest,
                'Interruption brève des listeners publics à chaque transfert. Aucun redémarrage Apache ni changement SQL.',
                'Le timer reste arrêté entre transfert et activation finale. Tout effet incomplet reste manuel.',
                'Boot Gateway/Mobile non enrôlé. Aucun APK ni clôture de phase 6.')))

    def receipt(self):
        return Receipt(created_resources=tuple(r.name for r in self.spec.resources if not r.preexisting),
            backups=tuple(r.name for r in self.spec.resources if r.backup is not None),
            hashes_non_secret=(('shared_public_profile', self.runtime.digest),))

    def prepare(self, context):
        r = self.runtime
        require(r._read(self.phase + '.attempt') is None, ErrorCode.MANUAL_ACTION_REQUIRED)
        if self.phase == 'enroll': r.absent(); return
        if self.phase == 'handoff':
            r.enrolled(); r.web.configuration(); r.web.enabled(); r.boot.configuration(); r.boot.live(); r.gateway(); r.network_ready()
            require(all(r.web.running(role) for role in ('http', 'https', 'timer')), ErrorCode.VALIDATION_FAILED)
            r.stopped('renew'); return
        r.configuration(); r.completed('handoff')
        require(r.listener('http') and r.listener('https'), ErrorCode.VALIDATION_FAILED)
        if self.phase != 'verify': r.stopped('timer'); r.stopped('renew')
        if self.phase == 'certificate':
            with _private_directory(r.shared.root, create=False) as fd: fs._absent(fd, 'letsencrypt')
        else: r.mobile.verify(); r.web.certificate()
        if self.phase in ('publish', 'verify'): r.gateway()
        if self.phase == 'publish': r.completed('dry-run')
        if self.phase in ('renewal', 'verify'): r.completed('publish'); require(r.ready(), ErrorCode.INVALID_STATE)

    def apply(self, context):
        r = self.runtime; self.prepare(context)
        if self.phase == 'enroll': r.enroll(context)
        else:
            with StateJournal(r.root / 'effect-lock.json').locked(create=False):
                self.prepare(context)
                r._write(self.phase + '.attempt', r.binding(context))
                if self.phase in ('handoff', 'publish'): getattr(r, self.phase)(context)
                elif self.phase == 'certificate':
                    old.command(r.shared.certbot(), timeout=840)
                    r.mobile._write('renewal.json', r.mobile.configuration()); r.mobile.verify()
                elif self.phase == 'dry-run': old.command(r.shared.certbot(renew=True, dry_run=True), timeout=840); r.mobile.verify()
                elif self.phase == 'verify': gateway_service_probe.check(r.value['preparation']['gateway_identity']['public_origin'])
                # Timer starts after the effect lock is released: Persistent=
                # can immediately invoke renewal. All its prerequisites exist.
                if self.phase != 'renewal': r._write(self.phase + '.json', r.binding(context))
            if self.phase == 'renewal':
                r.control('start', 'timer'); require(r.web.running('timer'), ErrorCode.VALIDATION_FAILED)
                r._write('renewal.json', r.binding(context))
        self.current(context); return self.receipt()

    def current(self, context):
        r = self.runtime; r.enrolled()
        require(r.completed(self.phase) == r.binding(context), ErrorCode.INVALID_STATE)
        if self.phase == 'enroll': return
        r.configuration()
        if self.phase in ('handoff', 'publish', 'renewal', 'verify'):
            require(r.listener('http') and r.listener('https'), ErrorCode.VALIDATION_FAILED)
        if self.phase in ('certificate', 'dry-run', 'publish', 'renewal', 'verify'): r.mobile.verify(); r.web.certificate()
        if self.phase in ('publish', 'renewal', 'verify'): require(r.ready(), ErrorCode.INVALID_STATE)
        if self.phase in ('renewal', 'verify'): require(r.web.running('timer'), ErrorCode.VALIDATION_FAILED)
        if self.phase == 'verify': r.gateway()

    def validate(self, context): self.current(context); return context.evidence == self.receipt().as_dict()
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
    runtime = SharedPublic(profile); operations = []
    for phase in PHASES: operations.append(SharedOperation(runtime, phase, operations[-1].spec.name if operations else None))
    result = TransactionEngine(journal, OperationRegistry(tuple(operations)))
    document = result.report()
    if document is not None:
        require([s.as_dict() for s in result.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
        result.registry.validate_document(document)
    return result, runtime


def overlay(profile, scope, fragment_sha256):
    """No fallback once a successor handoff intent exists, including damage."""
    reader = object.__new__(SharedPublic); reader.root = profile.public / 'shared/private'
    if reader._read('handoff.attempt') is None: return None
    value = reader._read('profile.json'); require(value is not None, ErrorCode.INVALID_STATE)
    runtime = SharedPublic(value)
    require(runtime.root == reader.root and runtime.web.value == profile.value
        and runtime.http.spec.maintenance_directory == scope.directory
        and profile.value['backend_fragment_sha256'] == fragment_sha256, ErrorCode.SOURCE_DRIFT)
    from installer.gateway_public_selection import selected
    generation = selected(runtime)
    if generation is not None:
        runtime = generation.readers()[1]
    else:
        runtime.configuration()
    evidence = {'path': str(runtime.dropin), 'profile_sha256': profile.digest,
        'configuration_sha256': f._sha(profile.apache_include()), 'dropin_sha256': f._sha(runtime.apache_dropin()),
        'successor_profile_sha256': runtime.digest}
    if generation is not None: evidence['gateway_generation_sha256'] = generation.digest
    return evidence
