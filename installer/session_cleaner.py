"""Private exclusive staging of a dedicated session collector and its timer.

Staging requires the exact initially-gated HTTP runtime. Never starts/enables a timer
nor alters Debian's native phpsessionclean, cron, PHP or Apache configuration.
"""
import os
from pathlib import Path
import subprocess

from installer import http_runtime as h
from installer import system_drain as s
from installer.model import Receipt, ResourceSpec, StepSpec
from installer.operations import Operation, Recovery, RecoveryDecision

fs, f, p = h.fs, h.f, h.p
TIMER_PROPERTIES = ('Id', 'LoadState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload',
                    'ActiveState', 'SubState', 'Job', 'Unit')


class SessionCleanerError(RuntimeError):
    pass


def require(ok, code='SESSION_CLEANER_REJECTED'):
    if not ok: raise SessionCleanerError(code)


class SessionCleaner:
    def __init__(self, runtime):
        require(type(runtime) is h.HttpRuntime)
        self.runtime = runtime
        self.directory = runtime.spec.root / 'cleaner'
        self.unit = 'hestia-' + runtime.spec.instance + '-session-cleaner.service'
        self.timer = self.unit.replace('.service', '.timer')

    def __repr__(self): return '<SessionCleaner private initially gated scheduler>'

    def _inputs(self):
        result = self.runtime.observe()
        account, _ = self.runtime._host(); scope = self.runtime._scope(account)
        return self._profile_inputs(account, scope, result['plan_sha256'])

    def _profile_inputs(self, account, scope, runtime_plan_sha256):
        python = Path('/usr/bin/python' + {'8.2': '3.11', '8.4': '3.13'}[self.runtime.spec.php_family])
        dependencies = {str(path): h._system_file_digest(path) for path in (
            python, Path('/usr/lib/php/sessionclean'), Path('/etc/cron.d/php'),
            Path('/etc/php') / self.runtime.spec.php_family / 'fpm/php.ini')}
        profile = {'root': str(self.runtime.spec.root), 'uid': account.pw_uid, 'gid': account.pw_gid,
                   'profile_sha256': f._sha(scope._profile()), 'guard_sha256': f._sha(scope._guard())}
        if self.runtime.spec.maintenance_directory is not None:
            profile['maintenance'] = str(scope.directory)
        template = p._read_file(Path(__file__).parent / 'private/session_cleaner_worker.py')
        require(template.count(b'__SESSION_CLEANER_PROFILE_HEX__') == 1)
        worker = template.replace(b'__SESSION_CLEANER_PROFILE_HEX__', p._json(profile).hex().encode())
        fragment = f'''[Unit]
Description=HESTIA dedicated session collection
[Service]
Type=oneshot
User={account.pw_name}
Group={account.pw_gid}
ExecStart={python} -I -B {self.directory}/worker.py
UMask=0077
Restart=no
RemainAfterExit=no
KillMode=control-group
SendSIGKILL=yes
TimeoutStartSec=10s
TimeoutStopSec=5s
Delegate=no
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ReadWritePaths={self.runtime.spec.root}/data/sessions
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX
'''.encode()
        timer = f'''[Unit]
Description=HESTIA dedicated session collection schedule
[Timer]
OnBootSec=5min
OnUnitInactiveSec=30min
AccuracySec=1min
Unit={self.unit}
[Install]
WantedBy=timers.target
'''.encode()
        files = {self.directory / 'worker.py': worker, s.UNIT_ROOT / self.unit: fragment,
                 s.UNIT_ROOT / self.timer: timer,
                 s.UNIT_ROOT / (self.unit + '.d/50-hestia-maintenance.conf'): s.condition_dropin(scope)}
        plan = p._json({'version': 1, 'runtime_plan_sha256': runtime_plan_sha256,
            'dependencies': dependencies, 'lifetime': 43200,
            'files': {str(path): f._sha(data) for path, data in files.items()}})
        return account, scope, files, plan

    def prepare(self):
        try:
            require(os.geteuid() == 0)
            self._inputs()
            with fs._directory(self.directory.parent) as fd: fs._absent(fd, self.directory.name)
            with fs._directory(s.UNIT_ROOT) as fd:
                for name in (self.unit, self.timer, self.unit + '.d', self.timer + '.d'): fs._absent(fd, name)
            h._unit_absent(self.unit); h._unit_absent(self.timer)
        except SessionCleanerError: raise
        except Exception: raise SessionCleanerError('SESSION_CLEANER_PRECONDITION_FAILED') from None

    def create(self, *, confirmed):
        try:
            require(confirmed is True, 'SESSION_CLEANER_CONSENT_REQUIRED'); self.prepare()
            account, scope, files, plan = self._inputs()
            with scope.recover(scope.observe()['lease_id'], confirmed=True) as lease:
                self.runtime.observe(); lease.assert_held()
                with fs._directory(self.directory.parent) as parent:
                    os.mkdir(self.directory.name, 0o700, dir_fd=parent); os.fsync(parent)
                with fs._directory(self.directory) as fd:
                    f._write(fd, 'cleaner.attempt', plan, 0)
                    os.fchown(fd, 0, account.pw_gid); os.fchmod(fd, 0o750); os.fsync(fd)
                with fs._directory(s.UNIT_ROOT) as fd:
                    os.mkdir(self.unit + '.d', 0o755, dir_fd=fd); os.fsync(fd)
                for path, data in files.items():
                    with fs._directory(path.parent) as fd:
                        f._write(fd, path.name, data, account.pw_gid if path.parent == self.directory else 0,
                                 mode=0o640 if path.parent == self.directory else 0o644)
                lease.assert_held(); self.runtime.observe()
                h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
                with fs._directory(self.directory) as fd:
                    f._write(fd, 'staged.json', p._json({'version': 1, 'state': 'SESSION_CLEANER_STAGED',
                        'plan_sha256': f._sha(plan), 'lease_id': lease.lease_id}), 0)
            return self.observe()
        except SessionCleanerError: raise
        except Exception: raise SessionCleanerError('SESSION_CLEANER_INCOMPLETE') from None

    def _timer_state(self, *, stopped=True):
        require(type(stopped) is bool)
        result = subprocess.run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'show',
            '--property=' + ','.join(TIMER_PROPERTIES), '--', self.timer], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False,
            env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'SYSTEMD_PAGER': ''})
        require(result.returncode == 0 and len(result.stdout) <= 8192)
        value = {}
        for row in result.stdout.decode().splitlines():
            key, sep, text = row.partition('=')
            require(sep == '=' and key in TIMER_PROPERTIES and key not in value); value[key] = text
        expected = {'Id': self.timer, 'LoadState': 'loaded', 'FragmentPath': str(s.UNIT_ROOT / self.timer),
            'DropInPaths': '', 'NeedDaemonReload': 'no', 'Job': '', 'Unit': self.unit}
        require(set(value) == set(TIMER_PROPERTIES) and all(value[k] == v for k, v in expected.items()))
        state = (value['ActiveState'], value['SubState'])
        require(state == ('inactive', 'dead') if stopped else state in
                (('inactive', 'dead'), ('active', 'waiting'), ('active', 'running'), ('active', 'elapsed')))
        return value

    def _stop_timer(self):
        # This private lifecycle operation never enables, starts or disables it.
        self._timer_state(stopped=False)
        result = subprocess.run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password',
            'stop', '--', self.timer], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False,
            env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'SYSTEMD_PAGER': ''})
        require(result.returncode == 0, 'SESSION_CLEANER_TIMER_STOP_FAILED')
        self._timer_state()

    def _verify_configuration(self, account, files, plan, lease_id):
        with fs._directory(self.directory) as fd:
            info = os.fstat(fd)
            require(info.st_gid == account.pw_gid and info.st_mode & 0o7777 == 0o750)
            require(f._read(fd, 'cleaner.attempt', 0) == plan)
            require(f._json_read(fd, 'staged.json', 0) == {'version': 1, 'state': 'SESSION_CLEANER_STAGED',
                'plan_sha256': f._sha(plan), 'lease_id': lease_id})
        for path, data in files.items():
            with fs._directory(path.parent) as fd:
                require(f._read(fd, path.name, account.pw_gid if path.parent == self.directory else 0,
                    mode=0o640 if path.parent == self.directory else 0o644) == data)

    def _inspect_with_runtime(self):
        """Fresh combined file audit; no reusable observation or native authority.

        The collector plan binds this exact HTTP plan. Return both observations
        to composition callers so they do not scan the same source twice before
        auditing units. Every call reads all files again.
        """
        runtime = self.runtime._inspect_configuration()
        account, _, runtime_plan, initial = runtime
        scope = self.runtime._scope(account)
        account, scope, files, plan = self._profile_inputs(account, scope, f._sha(runtime_plan))
        self._verify_configuration(account, files, plan, initial['lease_id'])
        return runtime, (account, scope, files, plan)

    def _inspect_configuration(self):
        """Private immutable proof, never a staging or running observation."""
        return self._inspect_with_runtime()[1]

    def observe(self):
        try:
            account, scope, files, plan = self._inputs()
            self._verify_configuration(account, files, plan, scope.observe()['lease_id'])
            s.audit_unit(scope, s.UnitBinding('session-cleaner', f._sha(files[s.UNIT_ROOT / self.unit])), stopped=True)
            self._timer_state()
            return {'state': 'SESSION_CLEANER_STAGED', 'plan_sha256': f._sha(plan), 'lifetime_seconds': 43200,
                'dedicated_cleaner_staged': True, 'schedule_active': False, 'native_cleaner_modified': False,
                'system_wiring_verified': False, 'application_installed': False, 'complete_web_backup': False}
        except SessionCleanerError: raise
        except Exception: raise SessionCleanerError('SESSION_CLEANER_INCOMPLETE') from None

    def binding(self):
        self.observe(); _, _, files, _ = self._inputs()
        return s.UnitBinding('session-cleaner', f._sha(files[s.UNIT_ROOT / self.unit]))


class SessionCleanerOperation(Operation):
    def __init__(self, cleaner):
        require(type(cleaner) is SessionCleaner); self.cleaner = cleaner
        super().__init__(StepSpec(name='web.session-cleaner', operation='web.session-cleaner.stage', module='web',
            boundary='web.session-cleaner', action='Préparer le nettoyage dédié des sessions sous maintenance',
            resources=(ResourceSpec('cleaner', 'directory', str(cleaner.directory)),
                       ResourceSpec('cleaner_unit', 'file', str(s.UNIT_ROOT / cleaner.unit)),
                       ResourceSpec('cleaner_timer', 'file', str(s.UNIT_ROOT / cleaner.timer)),
                       ResourceSpec('cleaner_dropin', 'directory', str(s.UNIT_ROOT / (cleaner.unit + '.d')))),
            rollback_supported=False, warnings=('Planification préparée ; activation explicite encore requise.',)))

    def prepare(self, context): self.cleaner.prepare()
    def apply(self, context):
        value = self.cleaner.create(confirmed=True)
        return Receipt(created_resources=tuple(r.name for r in self.spec.resources),
                       hashes_non_secret=(('session_cleaner_plan', value['plan_sha256']),))
    def validate(self, context):
        try: return self.cleaner.observe()['plan_sha256'] == context.evidence['hashes_non_secret']['session_cleaner_plan']
        except Exception: return False
    def commit(self, context): require(self.validate(context))
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            value = self.cleaner.observe()
            return Recovery(RecoveryDecision.APPLIED, Receipt(created_resources=tuple(r.name for r in self.spec.resources),
                hashes_non_secret=(('session_cleaner_plan', value['plan_sha256']),)))
        except Exception: return Recovery(RecoveryDecision.MANUAL)
