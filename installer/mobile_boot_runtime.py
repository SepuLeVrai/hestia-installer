"""Additive Mobile boot enrollment, preserving all existing unit fragments.

One private PID-1 service starts only the selected, already-provisioned units.
Its volatile epoch journal prevents retries from replaying a completed start.
"""
import os
from pathlib import Path
import re
import time

from installer import boot_runtime as boot, shared_public_runtime as public
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.engine import TransactionEngine
from installer.model import ErrorCode, ResourceSpec, StepSpec, canonical_bytes, exact_keys, require
from installer.operations import OperationRegistry
from installer.package_plan import PackagePlan
from installer.transaction import _private_directory, _FILE_FLAGS, _check_file

ASSET = 'installer/private/foundation-apache.conf.template'


def code_files():
    return {**boot.code_files(), ASSET: (boot.SOURCE.parent / ASSET).read_bytes()}


def digest(value): return boot.f._sha(canonical_bytes(value))


def selection(shared, parents):
    return {'version': 1, 'shared': shared, 'parents': parents,
        'code': {name: boot.f._sha(raw) for name, raw in code_files().items()}}


class Epoch:
    _read, _write = PackagePlan._read, PackagePlan._write
    def __init__(self, instance): self.root = Path('/run') / ('hestia-' + instance + '-mobile-boot')


class MobileBootRuntime(boot.BootRuntime):
    def __init__(self, profile):
        exact_keys(profile, {'version', 'shared', 'parents', 'code'})
        require(type(profile['version']) is int and profile['version'] == 1)
        exact_keys(profile['parents'], {'web', 'shared_public', 'shared_journal'})
        require(all(type(x) is str and re.fullmatch('[a-f0-9]{64}', x) for x in profile['parents'].values()))
        self.profile = profile
        self.shared = public.SharedPublic(profile['shared'])
        require(type(profile['code']) is dict and type(profile['code'].get(ASSET)) is str
            and re.fullmatch('[a-f0-9]{64}', profile['code'][ASSET]), ErrorCode.INVALID_DATA)
        public.old.Profile({**self.shared.web.value, 'code': {n: d for n, d in profile['code'].items() if n != ASSET}})
        require(profile['parents']['web'] == self.shared.value['preparation']['parents']['web'], ErrorCode.INCOMPATIBLE_STATE)
        self.layout, self.http = self.shared.layout, self.shared.http
        binding = profile['shared']['gateway_binding']
        self.foundation = FoundationRuntime.for_gateway(self.shared.boot.activation, binding['main'], binding['gateway_identity'])
        self.gateway = GatewayServiceRuntime.from_binding(self.foundation, binding)
        self.foundation = self.gateway.foundation
        self.dev_foundation = self.gateway.profile.dev
        if self.dev_foundation is not None:
            from installer.dev_target import DevBootUnit
            for role in ('php', 'apache', 'timer'):
                setattr(self, 'dev_' + role, DevBootUnit(self.dev_foundation.target, role))
        require(canonical_bytes(self.gateway.profile.binding()) == canonical_bytes(binding), ErrorCode.INCOMPATIBLE_STATE)
        self.root = self.layout.root / 'mobile-boot'
        self.target = 'hestia-' + self.layout.instance + '-mobile-boot.service'
        self.link = boot.h.drain.UNIT_ROOT / 'multi-user.target.wants' / self.target
        self.epoch = Epoch(self.layout.instance)

    def runner(self):
        return (boot.SOURCE / 'private/mobile_boot_worker.py').read_bytes().replace(b'__MOBILE_BOOT_SHA256__', digest(self.profile).encode())

    def units(self):
        return {self.target: (f'[Unit]\nDescription=HESTIA verified Mobile boot\n'
            f'Requires={self.shared.boot.target}\nAfter={self.shared.boot.target}\n'
            f'Before={self.shared.web.unit("https")}\n'
            f'ConditionPathExists=!{self.http.spec.maintenance_directory}/maintenance.attempt\n'
            '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
            f'ExecStart=/usr/bin/python3.13 -I -B {self.root}/worker.py\n'
            'UMask=0077\nRestart=no\nTimeoutStartSec=180\nTimeoutStopSec=15\n'
            'NoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\n').encode()}

    def dependencies(self): return {}

    def create(self, context):
        self.absent()
        self._write('stage.attempt', self.binding(context))
        self._write('profile.json', self.profile)
        files = code_files()
        require({n: boot.f._sha(b) for n, b in files.items()} == self.profile['code'], ErrorCode.INCOMPATIBLE_STATE)
        for name, data in {'worker.py': self.runner(), **{'code/' + n: b for n, b in files.items()}}.items():
            require(len(data) <= 1048576, ErrorCode.INVALID_DATA)
            path = self.root / name
            with _private_directory(path.parent, create=True) as fd:
                handle = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
                try:
                    os.fchmod(handle, 0o600); _check_file(handle)
                    with os.fdopen(handle, 'wb', closefd=False) as stream:
                        stream.write(data); stream.flush(); os.fsync(handle)
                    os.fsync(fd)
                finally: os.close(handle)
        with boot.fs._directory(boot.h.drain.UNIT_ROOT) as fd:
            for name, data in self.units().items(): boot.f._write(fd, name, data, 0, mode=0o644)
        boot.h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        self._write('staged.json', self.binding(context))
        self.staged(context)

    def live(self):
        self.shared.configuration(); self.shared.completed('verify')
        require(self.shared.ready(), ErrorCode.DEPENDENCY_BLOCKED)
        self.shared.boot.configuration(); self.shared.boot.live()
        self.gateway.owned()
        if self.dev_foundation is not None:
            self.dev_foundation.target.serving(); self.dev_foundation.owned()

    @staticmethod
    def epoch_identity():
        identifier = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        start = Path('/proc/1/stat').read_text().rsplit(')', 1)[1].split()[19]
        require(re.fullmatch('[a-f0-9-]{36}', identifier) and start.isdecimal(), ErrorCode.INVALID_STATE)
        return {'boot_id': identifier, 'pid1_start': start}

    @staticmethod
    def process(runtime):
        from installer.dev_target import DevBootUnit
        if isinstance(runtime, DevBootUnit): return runtime.process()
        runtime.owned()
        pid = runtime.show()['MainPID']
        start = (Path('/proc') / pid / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        require(start.isdecimal(), ErrorCode.INVALID_STATE)
        runtime.owned(); require(runtime.show()['MainPID'] == pid, ErrorCode.SOURCE_DRIFT)
        return {'pid': pid, 'start': start}

    def start(self, role, epoch):
        require(role in ('foundation', 'gateway', 'dev_foundation', 'dev_php', 'dev_apache', 'dev_timer'))
        runtime = getattr(self, role)
        owner = {'profile_sha256': digest(self.profile), 'epoch': epoch, 'role': role, 'unit': runtime.unit}
        attempt = self.epoch._read(role + '.attempt')
        completed = self.epoch._read(role + '.json')
        if completed is not None:
            require(attempt == owner and completed == {'owner': owner, 'process': self.process(runtime)}, ErrorCode.MANUAL_ACTION_REQUIRED)
            return
        if attempt is None:
            runtime.stopped()
            self.epoch._write(role + '.attempt', owner)
            require(self.epoch_identity() == epoch, ErrorCode.SOURCE_DRIFT)
            boot.h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'start', '--', runtime.unit])
        else:
            require(attempt == owner, ErrorCode.INVALID_STATE)
            # A stopped service with an earlier intent must not be replayed.
            runtime.owned()
        deadline = time.monotonic() + 10
        while True:
            try: observed = self.process(runtime); break
            except Exception:
                if time.monotonic() >= deadline: raise
                time.sleep(.1)
        require(self.epoch_identity() == epoch, ErrorCode.SOURCE_DRIFT)
        self.epoch._write(role + '.json', {'owner': owner, 'process': observed})

    def boot(self):
        self.configuration()
        enabled = self._read('enabled.json')
        require(enabled is not None and enabled == self._read('enable.attempt')
            and enabled['profile_sha256'] == digest(self.profile), ErrorCode.INVALID_STATE)
        with boot.fs._directory(self.link.parent) as fd: self.exact_link(fd, self.target, '../' + self.target)
        self.shared.configuration(); self.shared.completed('verify')
        require(self.shared.ready(), ErrorCode.DEPENDENCY_BLOCKED)
        self.shared.boot.configuration(); self.shared.boot.live()
        scope = self.http._scope(self.layout.identity.account())
        # Maintenance cannot be acquired across the two ordered starts.
        with scope.writer():
            require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
            self.foundation.inspect(); self.gateway.inspect()
            epoch = self.epoch_identity()
            from installer.transaction import StateJournal
            with StateJournal(self.epoch.root / 'lock.json').locked(create=True):
                self.start('foundation', epoch)
                if self.dev_foundation is not None:
                    target = self.dev_foundation.target
                    dev_scope, _ = target.activation.configuration()
                    if dev_scope.observe()['state'] == 'SERVING':
                        with dev_scope.writer():
                            require(dev_scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
                            for role in ('dev_php', 'dev_apache', 'dev_timer', 'dev_foundation'):
                                self.start(role, epoch)
                            target.activation.check()
                    # A closed DEV gate is never opened at boot. MAIN remains
                    # usable; the absent DEV listener cannot fall back to MAIN.
                require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
                self.start('gateway', epoch)
                self.gateway.owned()


class MobileBootOperation(boot.BootOperation):
    def __init__(self, runtime, phase):
        super().__init__(runtime, phase)
        self.spec = StepSpec(name='mobile.boot.' + phase, operation='mobile.boot.' + phase, module='gateway',
            boundary='mobile.boot.' + phase,
            action='Préparer le démarrage Mobile' if phase == 'stage' else 'Activer le démarrage automatique Mobile',
            dependencies=() if phase == 'stage' else ('mobile.boot.stage',), rollback_supported=False,
            resources=(ResourceSpec('boot_' + phase, 'directory' if phase == 'stage' else 'file', str(runtime.root if phase == 'stage' else runtime.link)),),
            warnings=('Profil lié : ' + digest(runtime.profile), 'Aucun redémarrage immédiat. La maintenance reste fermée au boot.'))


def engine(journal, profile):
    runtime = MobileBootRuntime(profile)
    result = TransactionEngine(journal, OperationRegistry(tuple(MobileBootOperation(runtime, phase) for phase in ('stage', 'enable'))))
    document = result.report()
    if document is not None:
        require([x.as_dict() for x in result.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
        result.registry.validate_document(document)
    return result, runtime
