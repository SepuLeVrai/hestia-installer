"""Owned boot dependencies for the qualified fresh Debian 13 server profile.

The acquired units and StepSpecs are unchanged. Only new units, exact dependency
links and a private immutable copy of the native readers are installed. No start,
restart, SQL mutation or admission change occurs while enrolling boot.
"""
import os
from pathlib import Path
import stat
import time

from installer import application_plan as app, application_activation as activation
from installer import mariadb_runtime as sql
from installer.engine import TransactionEngine
from installer.model import ErrorCode, Receipt, ResourceSpec, StepSpec, canonical_bytes, require
from installer.operations import Operation, OperationRegistry, Recovery, RecoveryDecision
from installer.package_plan import PackagePlan
from installer.transaction import _private_directory, _FILE_FLAGS, _check_file

h, f, fs = sql.h, sql.f, sql.fs
SOURCE = Path(__file__).parent


def code_files():
    # These readers have file-backed PHP/templates; a zipimport would change
    # their semantics. Exclude frontend assets and interpreter caches.
    return {p.relative_to(SOURCE.parent).as_posix(): p.read_bytes() for p in sorted(SOURCE.rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.php', '.json') and '__pycache__' not in p.parts}


class BootRuntime:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, profile):
        self.profile = profile
        self.application = profile['application']
        self.layout = app.FreshProfile.from_draft(self.application)
        self.http = self.layout.http(self.application['configuration'])
        self.activation = activation.Activation(self.http, profile['parents']['preparation'])
        self.sql = sql.MariaDB(profile['sql']['instance'], profile['sql']['packages_sha256'])
        self.root = self.layout.root / 'boot'
        self.prefix = 'hestia-' + self.layout.instance + '-boot'
        self.guard = self.prefix + '-sql.service'
        self.ready = self.prefix + '-web.service'
        self.target = self.prefix + '.target'
        self.web_units = tuple(self.activation.unit(role) for role in ('php', 'apache', 'timer'))
        self.link = h.drain.UNIT_ROOT / 'multi-user.target.wants' / self.target

    def runner(self):
        template = (SOURCE / 'private/boot_worker.py').read_bytes()
        return template.replace(b'__BOOT_PROFILE_SHA256__', f._sha(canonical_bytes(self.profile)).encode())

    def units(self):
        def guard(name, ordering, phase):
            return (f'[Unit]\nDescription=HESTIA verified boot {phase}\n{ordering}\n'
                    f'[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                    f'ExecStart=/usr/bin/python3.13 -I -B {self.root}/worker.py {phase}\n'
                    'UMask=0077\nRestart=no\nTimeoutStartSec=90\n'
                    'NoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\n').encode()
        return {self.guard: guard(self.guard, 'Before=' + self.sql.unit, 'sql'),
                self.ready: guard(self.ready, 'Requires=' + self.sql.unit + ' dbus.service\nAfter=' + self.sql.unit
                    + ' dbus.service\nBefore=' + ' '.join(self.web_units), 'web'),
                self.target: ('[Unit]\nDescription=HESTIA dedicated server boot\nRequires=' + ' '.join((self.sql.unit, *self.web_units))
                    + '\nAfter=' + ' '.join((self.sql.unit, *self.web_units)) + '\n').encode()}

    def dependencies(self):
        return {self.sql.unit + '.requires': self.guard,
                **{unit + '.requires': self.ready for unit in self.web_units}}

    def live(self):
        self.activation.serving(); self.sql.probe()
        require(all(self.activation.running(role) for role in ('php', 'apache', 'timer')), ErrorCode.VALIDATION_FAILED)

    def absent(self):
        self.live()
        with fs._directory(self.root.parent) as fd: fs._absent(fd, self.root.name)
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for name in (*self.units(), *self.dependencies()): fs._absent(fd, name)
            for name in self.units(): fs._absent(fd, name + '.d'); h._unit_absent(name)
        with fs._directory(self.link.parent) as fd: fs._absent(fd, self.link.name)

    def binding(self, context):
        return {'version': 1, 'installation_id': context.installation_id,
                'spec_sha256': f._sha(canonical_bytes(context.spec)), 'profile_sha256': f._sha(canonical_bytes(self.profile))}

    def create(self, context):
        self.absent()
        self._write('stage.attempt', self.binding(context))
        self._write('profile.json', self.profile)
        files = code_files()
        require({n: f._sha(b) for n, b in files.items()} == self.profile['code'], ErrorCode.INCOMPATIBLE_STATE)
        for name, data in {'worker.py': self.runner(), **{'code/' + n: b for n, b in files.items()}}.items():
            path = self.root / name
            require(len(data) <= 1048576, ErrorCode.INVALID_DATA)
            with _private_directory(path.parent, create=True) as fd:
                handle = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
                try:
                    os.fchmod(handle, 0o600); _check_file(handle)
                    with os.fdopen(handle, 'wb', closefd=False) as stream:
                        stream.write(data); stream.flush(); os.fsync(handle)
                    os.fsync(fd)
                finally: os.close(handle)
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for name, data in self.units().items(): f._write(fd, name, data, 0, mode=0o644)
            for name, target in self.dependencies().items():
                os.mkdir(name, 0o755, dir_fd=fd); os.fsync(fd)
                with fs._directory(h.drain.UNIT_ROOT / name) as child:
                    os.symlink('../' + target, target, dir_fd=child); os.fsync(child)
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        self._write('staged.json', self.binding(context))
        self.staged(context)

    @staticmethod
    def exact_link(parent, name, target):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require(stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
                and os.readlink(name, dir_fd=parent) == target, ErrorCode.INVALID_STATE)

    def configuration(self):
        require(self._read('profile.json') == self.profile, ErrorCode.INVALID_STATE)
        expected = {'worker.py': f._sha(self.runner()), **{'code/' + n: digest for n, digest in self.profile['code'].items()}}
        entries = list((self.root / 'code').rglob('*'))
        require(not any(p.is_symlink() for p in entries), ErrorCode.INVALID_STATE)
        actual = {p.relative_to(self.root).as_posix() for p in entries if not p.is_dir()}
        require(actual == set(expected) - {'worker.py'}, ErrorCode.INVALID_STATE)
        for name, digest in expected.items():
            path = self.root / name
            with _private_directory(path.parent, create=False) as fd:
                require(f._sha(f._read(fd, path.name, 0, mode=0o600, limit=1048576)) == digest, ErrorCode.INVALID_STATE)
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for name, data in self.units().items():
                require(f._read(fd, name, 0, mode=0o644) == data, ErrorCode.INVALID_STATE); fs._absent(fd, name + '.d')
            for name, target in self.dependencies().items():
                with fs._directory(h.drain.UNIT_ROOT / name) as child:
                    require(set(os.listdir(child)) == {target}, ErrorCode.INVALID_STATE)
                    self.exact_link(child, target, '../' + target)

    def staged(self, context):
        self.configuration()
        require(self._read('stage.attempt') == self._read('staged.json') == self.binding(context), ErrorCode.INVALID_STATE)

    def enable(self, context):
        self.configuration(); self.live()
        with fs._directory(self.link.parent) as fd: fs._absent(fd, self.link.name)
        self._write('enable.attempt', self.binding(context))
        with fs._directory(self.link.parent) as fd:
            os.symlink('../' + self.target, self.target, dir_fd=fd); os.fsync(fd)
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        self._write('enabled.json', self.binding(context))
        self.enabled(context)

    def enabled(self, context):
        self.configuration()
        require(self._read('enable.attempt') == self._read('enabled.json') == self.binding(context), ErrorCode.INVALID_STATE)
        with fs._directory(self.link.parent) as fd: self.exact_link(fd, self.target, '../' + self.target)

    def boot(self, phase):
        """Invoked only by the enrolled PID 1 units, without operator secrets.

        Offline native readers run while future systemd jobs are pending. The
        acquired idle-job observations are not relaxed to admit that state.
        """
        require(phase in ('sql', 'web'), ErrorCode.INVALID_DATA)
        self.configuration()
        # The final enable receipt must exist even if the link became visible
        # just before a power failure. No partial enrollment may boot the app.
        enabled = self._read('enabled.json')
        require(enabled is not None and enabled == self._read('enable.attempt')
                and enabled['profile_sha256'] == f._sha(canonical_bytes(self.profile)), ErrorCode.INVALID_STATE)
        self.sql.configuration()
        if phase == 'sql': return
        for attempt in range(40):
            try: self.sql.probe(); break
            except Exception:
                if attempt == 39: raise
                time.sleep(.25)
        account, _, _, initial = self.http._inspect_configuration()
        self.activation.cleaner._inspect_configuration()
        scope = self.http._scope(account)
        require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
        with scope._open() as (fd, _):
            require(f._json_read(fd, 'resumed-' + initial['lease_id'] + '.json', scope.web_gid) == {
                'version': 1, 'instance': scope.instance, 'lease_id': initial['lease_id'], 'state': 'ACTIVITY_RESUMED'}, ErrorCode.INVALID_STATE)


class BootOperation(Operation):
    def __init__(self, runtime, phase):
        self.runtime, self.phase = runtime, phase
        super().__init__(StepSpec(name='system.boot.' + phase, operation='system.boot.' + phase, module='web',
            boundary='system.boot.' + phase,
            action='Préparer les contrôles de démarrage' if phase == 'stage' else 'Activer le démarrage automatique du serveur dédié',
            dependencies=() if phase == 'stage' else ('system.boot.stage',), rollback_supported=False,
            resources=(ResourceSpec('boot_' + phase, 'directory' if phase == 'stage' else 'file', str(runtime.root if phase == 'stage' else runtime.link)),),
            warnings=('Profil lié : ' + f._sha(canonical_bytes(runtime.profile)), 'Aucun redémarrage immédiat. La maintenance reste fermée au boot.')))

    def receipt(self):
        return Receipt(created_resources=('boot_' + self.phase,), hashes_non_secret=(('boot_profile', f._sha(canonical_bytes(self.runtime.profile))),))

    def prepare(self, context):
        if self.phase == 'stage': self.runtime.absent()
        else:
            self.runtime.configuration(); self.runtime.live()
            with fs._directory(self.runtime.link.parent) as fd: fs._absent(fd, self.runtime.link.name)

    def apply(self, context):
        if self.phase == 'stage': self.runtime.create(context)
        else: self.runtime.enable(context)
        return self.receipt()

    def current(self, context):
        getattr(self.runtime, 'staged' if self.phase == 'stage' else 'enabled')(context)

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
    runtime = BootRuntime(profile)
    result = TransactionEngine(journal, OperationRegistry(tuple(BootOperation(runtime, phase) for phase in ('stage', 'enable'))))
    document = result.report()
    if document is not None:
        require([s.as_dict() for s in result.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
        result.registry.validate_document(document)
    return result, runtime
