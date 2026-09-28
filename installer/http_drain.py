"""Stop provisioned HTTP, optionally its exact collector and timer.

The collector is enrolled by its verified typed provisioner, not an allowlist
exception. A procfs census remains a point-in-time check, not an all-host
scheduler fence, SQL barrier or defence against a privileged administrator.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import time

from installer import database_config as fs
from installer import finalization as f
from installer import http_runtime as h
from installer import maintenance as m
from installer import php_transport as p
from installer import system_drain as s
from installer import session_cleaner as c
from installer.model import strict_json_loads

ROLES = ('apache', 'php')
MAX_THREADS = 32768
MAX_PROC_BYTES = 65536
CENSUS_SECONDS = 5


class HttpDrainError(RuntimeError):
    """Closed diagnostic without paths, process identifiers or command lines."""


def require(ok, code):
    if not ok: raise HttpDrainError(code)


def _read(fd, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    try:
        raw = os.read(handle, MAX_PROC_BYTES + 1)
        require(len(raw) <= MAX_PROC_BYTES, 'HTTP_DRAIN_CENSUS_LIMIT')
        return raw.decode('utf-8', errors='surrogateescape')
    finally: os.close(handle)


def _credentials(status):
    values = {}
    for line in status.splitlines():
        key, separator, value = line.partition(':')
        if key not in ('Uid', 'Gid', 'Groups'): continue
        require(separator and key not in values and all(x.isdecimal() for x in value.split()),
                'HTTP_DRAIN_CENSUS_REJECTED')
        values[key] = tuple(int(x) for x in value.split())
    require(set(values) == {'Uid', 'Gid', 'Groups'} and len(values['Uid']) == len(values['Gid']) == 4,
            'HTTP_DRAIN_CENSUS_REJECTED')
    return values


def _membership(raw, units):
    rows = raw.splitlines()
    require(len(rows) == 1 and rows[0].startswith('0::/'), 'HTTP_DRAIN_CENSUS_REJECTED')
    path = rows[0][3:]
    return any(path == '/system.slice/' + unit or path.startswith('/system.slice/' + unit + '/') for unit in units)


def _start(raw):
    # comm may contain spaces and parentheses. Never disclose it or read cmdline.
    tail = raw[raw.rfind(')') + 2:].split()
    require(len(tail) >= 20 and tail[19].isdecimal(), 'HTTP_DRAIN_CENSUS_REJECTED')
    return tail[0], tail[19]


def identity_census(uid, gid, allowed_units):
    """Read every visible thread, including real/saved/fs IDs and shared groups."""
    require(type(uid) is int and type(gid) is int and uid > 0 and gid > 0
            and type(allowed_units) is tuple and all(type(unit) is str and re.fullmatch(
                r'hestia-[a-f0-9]{32}-(apache|php|session-cleaner)\.service', unit) for unit in allowed_units),
            'HTTP_DRAIN_CENSUS_REJECTED')
    require(os.readlink('/proc/self/ns/pid') == os.readlink('/proc/1/ns/pid'),
            'HTTP_DRAIN_CENSUS_VISIBILITY_REQUIRED')
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    proc = [row for row in mounts if len(row.split()) > 6 and row.split()[4] == '/proc' and ' - proc ' in row]
    require(len(proc) == 1 and 'hidepid=' not in proc[0] and 'subset=' not in proc[0],
            'HTTP_DRAIN_CENSUS_VISIBILITY_REQUIRED')
    deadline, count = time.monotonic() + CENSUS_SECONDS, 0
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root = os.open('/proc', flags)
    try:
        processes = [name for name in os.listdir(root) if name.isdecimal()]
        require(len(processes) <= MAX_THREADS, 'HTTP_DRAIN_CENSUS_LIMIT')
        for name in processes:
            process = tasks = None
            try:
                process = os.open(name, flags, dir_fd=root)
                tasks = os.open('task', flags, dir_fd=process)
                threads = os.listdir(tasks)
                for thread in threads:
                    require(thread.isdecimal(), 'HTTP_DRAIN_CENSUS_REJECTED')
                    count += 1
                    require(count <= MAX_THREADS and time.monotonic() < deadline, 'HTTP_DRAIN_CENSUS_LIMIT')
                    handle = None
                    try:
                        handle = os.open(thread, flags, dir_fd=tasks)
                        before = _start(_read(handle, 'stat'))
                        credentials = _credentials(_read(handle, 'status'))
                        belongs = uid in credentials['Uid'] or gid in credentials['Gid'] or gid in credentials['Groups']
                        member = _membership(_read(handle, 'cgroup'), allowed_units) if belongs else True
                        after = _start(_read(handle, 'stat'))
                        require(before[1] == after[1], 'HTTP_DRAIN_CENSUS_CHANGED')
                        if after[0] not in ('Z', 'X', 'x'):
                            require(member, 'HTTP_DRAIN_FOREIGN_IDENTITY_PROCESS')
                    except (FileNotFoundError, ProcessLookupError):
                        pass  # Kernel thread disappeared; no signal or PID reuse action.
                    finally:
                        if handle is not None: os.close(handle)
            except (FileNotFoundError, ProcessLookupError):
                pass
            finally:
                if tasks is not None: os.close(tasks)
                if process is not None: os.close(process)
    finally: os.close(root)
    require(time.monotonic() < deadline, 'HTTP_DRAIN_CENSUS_LIMIT')


class HttpDrain:
    def __init__(self, runtime, *, cleaner=None):
        require(type(runtime) is h.HttpRuntime, 'HTTP_DRAIN_INPUT_REJECTED')
        require(cleaner is None or (type(cleaner) is c.SessionCleaner and cleaner.runtime is runtime),
                'HTTP_DRAIN_COLLECTOR_MISMATCH')
        self.runtime, self.cleaner = runtime, cleaner
        self.roles = ROLES + (('session-cleaner',) if cleaner is not None else ())

    def __repr__(self): return '<HttpDrain private provisioned service barrier>'

    def _unit(self, role):
        require(role in self.roles, 'HTTP_DRAIN_INPUT_REJECTED')
        return self.cleaner.unit if role == 'session-cleaner' else self.runtime.unit(role)

    def _audit(self, *, stopped=False, expected=None, timer_stopped=False):
        account, extension, plan, _ = self.runtime._inspect_configuration()
        scope = self.runtime._scope(account)
        generated = self.runtime._files(account, extension)
        bindings = tuple(s.UnitBinding(role, f._sha(generated[s.UNIT_ROOT / self.runtime.unit(role)])) for role in ROLES)
        extra = {}
        if self.cleaner is not None:
            require(type(self.cleaner) is c.SessionCleaner and self.cleaner.runtime is self.runtime,
                    'HTTP_DRAIN_COLLECTOR_MISMATCH')
            owner, gate, files, cleaner_plan = self.cleaner._inspect_configuration()
            require((owner.pw_uid, owner.pw_gid, gate.instance, gate.directory) ==
                    (account.pw_uid, account.pw_gid, scope.instance, scope.directory), 'HTTP_DRAIN_COLLECTOR_MISMATCH')
            bindings += (s.UnitBinding('session-cleaner', f._sha(files[s.UNIT_ROOT / self.cleaner.unit])),)
            self.cleaner._timer_state(stopped=stopped or timer_stopped)
            extra = {'policy': 'PROVISIONED_HTTP_AND_CLEANER_STOP_ONLY_V1',
                     'cleaner_plan_sha256': f._sha(cleaner_plan),
                     'timer_sha256': f._sha(files[s.UNIT_ROOT / self.cleaner.timer])}
        from installer.public_tls_profile import overlay_evidence
        public = overlay_evidence(scope, bindings[0].fragment_sha256)
        if public is not None: extra['public_ingress'] = public
        profile = p._json({'version': 1, 'instance': scope.instance, 'maintenance': str(scope.directory),
            'policy': 'PROVISIONED_HTTP_STOP_ONLY_V1', 'runtime_plan_sha256': f._sha(plan),
            'uid': account.pw_uid, 'gid': account.pw_gid,
            'units': [{'role': b.role, 'fragment_sha256': b.fragment_sha256} for b in bindings], **extra})
        require(expected is None or profile == expected, 'HTTP_DRAIN_PROFILE_CHANGED')
        for binding in bindings:
            if binding.role == 'session-cleaner' and not stopped:
                s.audit_unit(scope, binding, running_collector=True)
            else: s.audit_unit(scope, binding, stopped=stopped)
        identity_census(account.pw_uid, account.pw_gid,
                        () if stopped else tuple(self._unit(role) for role in self.roles))
        return scope, profile

    def acquire(self, *, confirmed, timeout=30, cancel=None):
        return self._acquire(confirmed, timeout, cancel, None)

    def recover(self, lease_id, *, confirmed, timeout=30, cancel=None):
        require(type(lease_id) is str and re.fullmatch(r'[a-f0-9]{32}', lease_id), 'HTTP_DRAIN_INPUT_REJECTED')
        return self._acquire(confirmed, timeout, cancel, lease_id)

    def _acquire(self, confirmed, timeout, cancel, recover_id):
        lease = None
        try:
            require(confirmed is True, 'HTTP_DRAIN_CONSENT_REQUIRED')
            require(os.geteuid() == 0, 'HTTP_DRAIN_ROOT_REQUIRED')
            require(cancel is None or not cancel.is_set(), 'HTTP_DRAIN_INTERRUPTED')
            scope, profile = self._audit()
            lease = (scope.acquire(confirmed=True, timeout=timeout, cancel=cancel) if recover_id is None
                     else scope.recover(recover_id, confirmed=True, timeout=timeout))
            with fs._directory(scope.directory, readable_by=scope.web_gid) as fd:
                name = 'http-drain-' + lease.lease_id + '.attempt'
                try: old = f._read(fd, name, scope.web_gid)
                except FileNotFoundError: f._write(fd, name, profile, scope.web_gid)
                else: require(old == profile, 'HTTP_DRAIN_RECOVERY_MISMATCH')
            if self.cleaner is not None:
                lease.assert_held()
                require(cancel is None or not cancel.is_set(), 'HTTP_DRAIN_INTERRUPTED')
                self._audit(expected=profile)
                self.cleaner._stop_timer()
            for role in self.roles:
                lease.assert_held()
                require(cancel is None or not cancel.is_set(), 'HTTP_DRAIN_INTERRUPTED')
                self._audit(expected=profile, timer_stopped=self.cleaner is not None)
                s._systemctl('stop', self._unit(role))
                binding = next(row for row in strict_json_loads(profile)['units'] if row['role'] == role)
                s.audit_unit(scope, s.UnitBinding(role, binding['fragment_sha256']), stopped=True)
            self._audit(stopped=True, expected=profile)
            require(cancel is None or not cancel.is_set(), 'HTTP_DRAIN_INTERRUPTED')
            lease.assert_held()
            return HttpDrainLease(self, lease, profile)
        except BaseException as error:
            if lease is not None: lease.close()
            if isinstance(error, (HttpDrainError, h.HttpRuntimeError, c.SessionCleanerError, m.MaintenanceError, s.SystemDrainError,
                                  KeyboardInterrupt, SystemExit)): raise
            raise HttpDrainError('HTTP_DRAIN_UNAVAILABLE') from None


class HttpDrainLease:
    def __init__(self, drain, lease, profile):
        self._drain, self._lease, self._profile = drain, lease, profile

    def __repr__(self): return '<HttpDrainLease private live barrier>'
    def __reduce__(self): raise TypeError('HTTP drain leases cannot be serialized')

    def assert_held(self):
        try:
            self._lease.assert_held()
            scope = self._lease.scope
            with fs._directory(scope.directory, readable_by=scope.web_gid) as fd:
                require(f._read(fd, 'http-drain-' + self._lease.lease_id + '.attempt', scope.web_gid) == self._profile,
                        'HTTP_DRAIN_RECOVERY_MISMATCH')
            self._drain._audit(stopped=True, expected=self._profile)
            self._lease.assert_held()
        except (HttpDrainError, h.HttpRuntimeError, c.SessionCleanerError, m.MaintenanceError, s.SystemDrainError): raise
        except Exception: raise HttpDrainError('HTTP_DRAIN_UNAVAILABLE') from None

    @property
    def maintenance_lease(self):
        self.assert_held(); return self._lease

    def report(self):
        self.assert_held()
        combined = self._drain.cleaner is not None
        return {'state': 'PROVISIONED_HTTP_AND_CLEANER_DRAINED' if combined else 'PROVISIONED_HTTP_SERVICES_DRAINED',
            'services': len(self._drain.roles), 'timers_stopped': int(combined),
            'profile_sha256': f._sha(self._profile), 'cgroup_empty_verified': True,
            'identity_processes_absent_at_observation': True, 'census_is_point_in_time': True,
            'automatic_restart_blocked_by_gate': True, 'other_producers_controlled': False,
            'storage_inventory_complete': False, 'system_wiring_verified': False,
            'complete_web_backup': False, 'application_installed': False, 'activity_resumed': False,
            'writable_business_storage_ready': False, 'apply_allowed': False, 'rollback_verified': False,
            'service_activation_delivered': False, 'phase5_complete': False}

    def close(self): self._lease.close()
    def __enter__(self): self.assert_held(); return self
    def __exit__(self, *args): self.close()
