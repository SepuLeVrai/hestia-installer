"""Private stop-only systemd barrier for four explicitly enrolled service units.

This adapter neither installs units nor discovers all host writers. Enrollment
hashes come from the trusted system provisioner, never an HTTP request. A live
receipt covers only these units, not a complete Web inventory or activation.
No start, restart, daemon-reload, mask, kill-by-PID, or implicit activity resume.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import subprocess

from installer import database_config as fs
from installer import finalization as f
from installer import maintenance as m
from installer import php_transport as p

ROLES = ('apache', 'php', 'cli', 'session-cleaner')
UNIT_ROOT = Path('/etc/systemd/system')
CGROUP_ROOT = Path('/sys/fs/cgroup')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'FragmentPath', 'DropInPaths',
              'NeedDaemonReload', 'KillMode', 'SendSIGKILL', 'Delegate', 'Slice',
              'ControlGroup', 'MainPID', 'ControlPID', 'Type', 'Restart', 'RemainAfterExit',
              'RefuseManualStop', 'Job', 'Result')


class SystemDrainError(RuntimeError):
    """Closed diagnostic, without unit names, private paths, or command output."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise SystemDrainError(code)


@dataclass(frozen=True)
class UnitBinding:
    role: str
    fragment_sha256: str = field(repr=False)


def condition_dropin(scope: m.MaintenanceScope) -> bytes:
    """Canonical root-owned drop-in required on each already provisioned unit."""
    path = str(scope.directory)
    require(re.fullmatch(r'/[A-Za-z0-9_/-]+', path) is not None
            and '..' not in scope.directory.parts and '//' not in path,
            'SYSTEM_DRAIN_PROFILE_REJECTED')
    return ('[Unit]\nConditionPathExists=!' + path + '/maintenance.attempt\n').encode()


def _root_file(path: Path) -> bytes:
    with fs._directory(path.parent) as fd:
        return f._read(fd, path.name, 0, mode=0o644, limit=32768)


def _systemctl(action: str, unit: str) -> str:
    require(action in ('show', 'stop') and re.fullmatch(
        r'hestia-[a-f0-9]{32}-(apache|php|cli|session-cleaner)\.service', unit) is not None,
        'SYSTEM_DRAIN_COMMAND_REJECTED')
    executable = Path('/usr/bin/systemctl')
    p._safe_path(executable, directory=False, system=True)
    command = [str(executable), '--no-pager', '--no-ask-password', action]
    if action == 'show':
        command += ['--property=' + ','.join(PROPERTIES)]
    command += ['--', unit]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=40 if action == 'stop' else 5, check=False,
        env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''})
    require(result.returncode == 0 and len(result.stdout) <= 16384, 'SYSTEM_DRAIN_COMMAND_FAILED')
    return result.stdout.decode('utf-8', errors='strict')


def _show(unit: str) -> dict:
    rows = _systemctl('show', unit).splitlines()
    value = {}
    for row in rows:
        key, separator, item = row.partition('=')
        require(separator == '=' and key in PROPERTIES and key not in value,
                'SYSTEM_DRAIN_STATE_REJECTED')
        value[key] = item
    require(set(value) == set(PROPERTIES), 'SYSTEM_DRAIN_STATE_REJECTED')
    return value


def _empty_cgroup(unit: str) -> bool:
    # The kernel's recursive populated bit includes descendants, even setsid()
    # children. A missing, formerly assigned leaf is empty, not an arbitrary path.
    mounts = Path('/proc/self/mountinfo').read_text()
    require(any(' /sys/fs/cgroup ' in row and ' - cgroup2 ' in row
                for row in mounts.splitlines()), 'SYSTEM_DRAIN_CGROUP2_REQUIRED')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(CGROUP_ROOT, flags)
    try:
        for part in ('system.slice', unit):
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                return True
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(info.st_uid == 0 and not info.st_mode & 0o022, 'SYSTEM_DRAIN_CGROUP_REJECTED')
        def read(name):
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            try:
                data = os.read(handle, 4097)
                require(len(data) <= 4096, 'SYSTEM_DRAIN_CGROUP_REJECTED')
                return data.decode('ascii')
            finally:
                os.close(handle)
        events = dict(line.split() for line in read('cgroup.events').splitlines())
        return events.get('populated') == '0' and not read('cgroup.procs').strip()
    finally:
        os.close(fd)


def audit_unit(scope: m.MaintenanceScope, binding: UnitBinding, *, stopped=False, running_collector=False) -> None:
    """Read-only single-unit observation; never grants a four-producer lease."""
    require(type(scope) is m.MaintenanceScope and type(binding) is UnitBinding and binding.role in ROLES
            and type(binding.fragment_sha256) is str and re.fullmatch(r'[a-f0-9]{64}', binding.fragment_sha256),
            'SYSTEM_DRAIN_PROFILE_REJECTED')
    require(type(running_collector) is bool and (not running_collector or
            (binding.role == 'session-cleaner' and not stopped)), 'SYSTEM_DRAIN_PROFILE_REJECTED')
    unit = 'hestia-' + scope.instance + '-' + binding.role + '.service'
    fragment = UNIT_ROOT / unit
    dropin = UNIT_ROOT / (unit + '.d') / '50-hestia-maintenance.conf'
    require(hashlib.sha256(_root_file(fragment)).hexdigest() == binding.fragment_sha256
            and _root_file(dropin) == condition_dropin(scope), 'SYSTEM_DRAIN_UNIT_DRIFT')
    value = _show(unit)
    from installer.public_tls_profile import overlay_evidence
    public = overlay_evidence(scope, binding.fragment_sha256) if binding.role == 'apache' else None
    dropins = str(dropin) + (' ' + public['path'] if public is not None else '')
    # Only the typed collector composition may wait for a genuine oneshot
    # activation job. The four-role barrier and final stopped proof stay strict.
    pending = (running_collector and value['Type'] == 'oneshot'
        and value['ActiveState'] == 'activating' and value['SubState'] == 'start'
        and re.fullmatch(r'[1-9][0-9]*', value['Job']) is not None)
    required = {'Id': unit, 'LoadState': 'loaded', 'FragmentPath': str(fragment),
        'DropInPaths': dropins, 'NeedDaemonReload': 'no', 'KillMode': 'control-group',
        'SendSIGKILL': 'yes', 'Delegate': 'no', 'Slice': 'system.slice', 'Restart': 'no',
        'RemainAfterExit': 'no', 'RefuseManualStop': 'no', 'Job': value['Job'] if pending else ''}
    require(all(value[k] == v for k, v in required.items())
            and value['Type'] in ('simple', 'exec', 'notify', 'oneshot')
            and value['ControlGroup'] in ('', '/system.slice/' + unit), 'SYSTEM_DRAIN_UNIT_REJECTED')
    if stopped:
        require(value['ActiveState'] == 'inactive' and value['SubState'] == 'dead'
                and value['MainPID'] == value['ControlPID'] == '0' and value['Result'] == 'success'
                and _empty_cgroup(unit), 'SYSTEM_DRAIN_NOT_EMPTY')


class SystemDrain:
    def __init__(self, scope: m.MaintenanceScope, bindings: tuple[UnitBinding, ...]):
        require(type(scope) is m.MaintenanceScope and type(bindings) is tuple
                and len(bindings) == len(ROLES) and all(type(b) is UnitBinding for b in bindings),
                'SYSTEM_DRAIN_PROFILE_REJECTED')
        require(tuple(b.role for b in bindings) == ROLES and all(type(b.fragment_sha256) is str
                and re.fullmatch(r'[a-f0-9]{64}', b.fragment_sha256) for b in bindings),
                'SYSTEM_DRAIN_PROFILE_REJECTED')
        self.scope, self.bindings = scope, bindings
        self._dropin = condition_dropin(scope)
        from installer.public_tls_profile import overlay_evidence
        self._public = overlay_evidence(scope, bindings[0].fragment_sha256)
        self._profile = p._json({'version': 1, 'instance': scope.instance,
            'maintenance': str(scope.directory), 'policy': 'STOP_ONLY_SYSTEMD_CGROUP2_V1',
            'dropin_sha256': f._sha(self._dropin),
            'units': [{'role': b.role, 'fragment_sha256': b.fragment_sha256} for b in bindings],
            **({'public_ingress': self._public} if self._public is not None else {})})

    def __repr__(self):
        return '<SystemDrain private enrolled services>'

    def unit(self, role: str) -> str:
        require(role in ROLES, 'SYSTEM_DRAIN_PROFILE_REJECTED')
        return 'hestia-' + self.scope.instance + '-' + role + '.service'

    def _audit(self, binding: UnitBinding, *, stopped=False) -> None:
        audit_unit(self.scope, binding, stopped=stopped)

    def _audit_all(self, *, stopped=False) -> None:
        from installer.public_tls_profile import overlay_evidence
        require(overlay_evidence(self.scope, self.bindings[0].fragment_sha256) == self._public,
                'SYSTEM_DRAIN_PROFILE_REJECTED')
        for binding in self.bindings:
            self._audit(binding, stopped=stopped)

    def acquire(self, *, confirmed: bool, timeout: float = 30, cancel=None) -> SystemDrainLease:
        return self._acquire(confirmed=confirmed, timeout=timeout, cancel=cancel, recover_id=None)

    def recover(self, lease_id: str, *, confirmed: bool, timeout: float = 30, cancel=None) -> SystemDrainLease:
        return self._acquire(confirmed=confirmed, timeout=timeout, cancel=cancel, recover_id=lease_id)

    def _acquire(self, *, confirmed, timeout, cancel, recover_id):
        lease = None
        try:
            require(confirmed is True, 'SYSTEM_DRAIN_CONSENT_REQUIRED')
            require(os.geteuid() == 0, 'SYSTEM_DRAIN_ROOT_REQUIRED')
            require(cancel is None or not cancel.is_set(), 'SYSTEM_DRAIN_INTERRUPTED')
            self._audit_all()
            lease = (self.scope.acquire(confirmed=True, timeout=timeout, cancel=cancel) if recover_id is None
                     else self.scope.recover(recover_id, confirmed=True, timeout=timeout))
            attempt = 'system-drain-' + lease.lease_id + '.attempt'
            with fs._directory(self.scope.directory, readable_by=self.scope.web_gid) as fd:
                try:
                    old = f._read(fd, attempt, self.scope.web_gid)
                except FileNotFoundError:
                    f._write(fd, attempt, self._profile, self.scope.web_gid)
                else:
                    require(old == self._profile, 'SYSTEM_DRAIN_RECOVERY_MISMATCH')
            # Admission service first; PHP and its descendants next. Partial
            # failure never reopens admission or removes the durable attempt.
            for binding in self.bindings:
                lease.assert_held()
                require(cancel is None or not cancel.is_set(), 'SYSTEM_DRAIN_INTERRUPTED')
                self._audit(binding)
                _systemctl('stop', self.unit(binding.role))
                self._audit(binding, stopped=True)
            lease.assert_held()
            self._audit_all(stopped=True)
            require(cancel is None or not cancel.is_set(), 'SYSTEM_DRAIN_INTERRUPTED')
            return SystemDrainLease(self, lease)
        except BaseException as error:
            if lease is not None:
                lease.close()
            if isinstance(error, (SystemDrainError, m.MaintenanceError, KeyboardInterrupt, SystemExit)):
                raise
            raise SystemDrainError('SYSTEM_DRAIN_UNAVAILABLE') from None


class SystemDrainLease:
    def __init__(self, drain: SystemDrain, lease: m.MaintenanceLease):
        self._drain, self._lease = drain, lease

    def __repr__(self):
        return '<SystemDrainLease private live cgroup barrier>'

    def __reduce__(self):
        raise TypeError('System drain leases cannot be serialized')

    @property
    def maintenance_lease(self) -> m.MaintenanceLease:
        self.assert_held()
        return self._lease

    def assert_held(self) -> None:
        try:
            self._lease.assert_held()
            with fs._directory(self._drain.scope.directory, readable_by=self._drain.scope.web_gid) as fd:
                require(f._read(fd, 'system-drain-' + self._lease.lease_id + '.attempt',
                    self._drain.scope.web_gid) == self._drain._profile, 'SYSTEM_DRAIN_RECOVERY_MISMATCH')
            self._drain._audit_all(stopped=True)
            self._lease.assert_held()
        except (SystemDrainError, m.MaintenanceError):
            raise
        except Exception:
            raise SystemDrainError('SYSTEM_DRAIN_UNAVAILABLE') from None

    def report(self) -> dict:
        self.assert_held()
        return {'state': 'ENROLLED_SYSTEM_SERVICES_DRAINED', 'services': len(ROLES),
            'cgroup_empty_verified': True, 'profile_sha256': f._sha(self._drain._profile),
            'automatic_restart_blocked_by_gate': True, 'activity_resumed': False,
            'storage_inventory_complete': False, 'system_wiring_verified': False,
            'complete_web_backup': False, 'apply_allowed': False,
            'rollback_verified': False, 'application_installed': False}

    def close(self) -> None:
        self._lease.close()

    def __enter__(self):
        self.assert_held()
        return self

    def __exit__(self, *args):
        self.close()
