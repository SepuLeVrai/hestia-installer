"""Private read-only collection of two/four provisioned system manager units.

Never enumerates all launchers, executes business code or controls a unit.
Selected loaded properties and verified files are not an execution attestation.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time

from installer import http_runtime as h, session_cleaner as c, launcher_inventory as l

MAX_OUTPUT = 16384
SHOW_SECONDS = 5
COLLECTION_SECONDS = 60
COMMON = ('Id', 'LoadState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload',
          'ActiveState', 'SubState', 'Job')
SERVICE = ('ControlGroup', 'MainPID', 'ControlPID', 'Result')
UNIT_RE = r'hestia-[a-f0-9]{32}-(?:(?:apache|php|session-cleaner)\.service|session-cleaner\.timer)'


class SystemdObservationError(RuntimeError):
    """Closed diagnostics only; inaccessible never means absent."""


def require(ok, code='SYSTEMD_OBSERVATION_REJECTED'):
    if not ok: raise SystemdObservationError(code)


def _capture(argv):
    """Bound stdout while reading; terminate only our read-only systemctl child."""
    deadline = time.monotonic() + SHOW_SECONDS
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd='/', close_fds=True,
        env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'SYSTEMD_PAGER': ''})
    raw = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'SYSTEMD_OBSERVATION_TIMEOUT')
            ready, _, _ = select.select([child.stdout], [], [], remaining)
            require(ready, 'SYSTEMD_OBSERVATION_TIMEOUT')
            block = os.read(child.stdout.fileno(), min(4096, MAX_OUTPUT + 1 - len(raw)))
            if not block: break
            raw.extend(block)
            require(len(raw) <= MAX_OUTPUT, 'SYSTEMD_OBSERVATION_OUTPUT_LIMIT')
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'SYSTEMD_OBSERVATION_TIMEOUT')
        require(child.wait(timeout=remaining) == 0, 'SYSTEMD_OBSERVATION_UNREADABLE')
        return bytes(raw)
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=1)
        child.stdout.close()


def _show(unit):
    require(type(unit) is str and re.fullmatch(UNIT_RE, unit) is not None)
    h.p._safe_path(Path('/usr/bin/systemctl'), directory=False, system=True)
    properties = COMMON + (('Unit',) if unit.endswith('.timer') else SERVICE)
    raw = _capture(['/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password',
                    'show', '--property=' + ','.join(properties), '--', unit])
    require(len(raw) <= MAX_OUTPUT, 'SYSTEMD_OBSERVATION_OUTPUT_LIMIT')
    values = {}
    for row in raw.decode('utf-8').splitlines():
        key, sep, value = row.partition('=')
        require(sep == '=' and key in properties and key not in values)
        require(len(value) <= 2048 and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))
        values[key] = value
    require(set(values) == set(properties))
    return values


def _identity_file(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                and not before.st_mode & 0o022, 'SYSTEMD_OBSERVATION_VISIBILITY_REQUIRED')
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        attributes = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) <= maximum and all(getattr(before, key) == getattr(after, key) for key in attributes),
                'SYSTEMD_OBSERVATION_VISIBILITY_REQUIRED')
        return raw.decode('ascii').strip()
    finally: os.close(fd)


def _provenance():
    require(os.geteuid() == 0, 'SYSTEMD_OBSERVATION_VISIBILITY_REQUIRED')
    namespaces = {}
    for name in ('pid', 'mnt'):
        current, init = (os.readlink('/proc/' + who + '/ns/' + name) for who in ('self', '1'))
        require(current == init and re.fullmatch(name + r':\[[0-9]+\]', init) is not None,
                'SYSTEMD_OBSERVATION_VISIBILITY_REQUIRED')
        namespaces[name] = init
    with h.fs._directory(Path('/etc')):
        host = _identity_file('/etc/machine-id', 64)
    boot = _identity_file('/proc/sys/kernel/random/boot_id', 64)
    require(re.fullmatch('[a-f0-9]{32}', host) is not None and host != '0' * 32
            and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', boot) is not None,
            'SYSTEMD_OBSERVATION_VISIBILITY_REQUIRED')
    return {'host_id': host, 'boot_id': boot, 'namespaces': namespaces, 'manager': 'system'}


@dataclass(frozen=True)
class SystemdSample:
    _canonical: bytes = field(repr=False)

    def private_manifest(self): return json.loads(self._canonical)

    def report(self):
        data = self.private_manifest()
        return {'state': 'PROVISIONED_SYSTEMD_UNITS_OBSERVED', 'origin': 'LOCAL_SYSTEM_MANAGER',
            'units': len(data['content']['units']), 'coverage': 'partial',
            'manifest_sha256': l._sha(self._canonical), 'live_receipt': False,
            'execution_allowed': False, 'drain_allowed': False, 'host_scheduler_inventory_complete': False,
            'storage_inventory_complete': False, 'system_wiring_verified': False,
            'complete_web_backup': False, 'service_activation_delivered': False,
            'application_installed': False, 'writable_business_storage_ready': False, 'phase5_complete': False}

    def snapshot(self, target):
        """Conservative bridge; legacy isolated fixtures cannot assert the Web pin."""
        try:
            data = self.private_manifest(); content = data['content']
            require(content['profile']['target'] is not None and l._target(target) == content['profile']['target'],
                    'SYSTEMD_OBSERVATION_TARGET_MISMATCH')
            rows = []
            for row in content['units']:
                value = row['properties']; timer = row['name'].endswith('.timer')
                state = ('queued' if value['Job'] else 'idle' if value['ActiveState'] == 'inactive'
                         else 'active' if value['ActiveState'] in ('active', 'activating', 'deactivating', 'reloading')
                         else 'unknown')
                # A service may have other triggers. Inactive never implies disarmed.
                trigger = 'armed' if timer and value['ActiveState'] == 'active' else 'unknown'
                rows.append(l.LauncherObservation(key=row['name'], channel='systemd_system',
                    definitions=tuple(l.FileObservation(**item) for item in row['definitions']),
                    chain=(), chain_mode='unknown', argv_sha256=None, environment_sha256=None,
                    uid=None, gid=None, groups=None, cwd=None, storage_roles=None, sql_identities_sha256=None,
                    state=state, trigger=trigger, cgroup=value.get('ControlGroup') or None, maintenance=None))
            coverage = tuple(l.CoverageObservation(channel, 'partial' if channel == 'systemd_system' else 'unknown',
                l._sha(l._json(content)) if channel == 'systemd_system' else None) for channel in l.CHANNELS)
            return l.LauncherSnapshot(target, data['started_at'], coverage, tuple(rows))
        except SystemdObservationError: raise
        except Exception: raise SystemdObservationError('SYSTEMD_OBSERVATION_TARGET_MISMATCH') from None


class SystemdObserver:
    def __init__(self, runtime, *, cleaner=None):
        require(type(runtime) is h.HttpRuntime)
        require(cleaner is None or (type(cleaner) is c.SessionCleaner and cleaner.runtime is runtime),
                'SYSTEMD_OBSERVATION_COLLECTOR_MISMATCH')
        self.runtime, self.cleaner = runtime, cleaner

    def __repr__(self): return '<SystemdObserver private read-only provisioned scope>'

    def _profile(self, provenance):
        require(self.cleaner is None or (type(self.cleaner) is c.SessionCleaner and self.cleaner.runtime is self.runtime),
                'SYSTEMD_OBSERVATION_COLLECTOR_MISMATCH')
        account, extension, plan, _ = self.runtime._inspect_configuration()
        files = self.runtime._files(account, extension)
        units = [self.runtime.unit(role) for role in ('apache', 'php')]
        cleaner_plan = None
        if self.cleaner is not None:
            _, _, extra, cleaner_plan = self.cleaner._inspect_configuration()
            files.update(extra); units += [self.cleaner.unit, self.cleaner.timer]
        definitions = {}
        for unit in units:
            paths = [h.drain.UNIT_ROOT / unit]
            if unit.endswith('.service'): paths.append(h.drain.UNIT_ROOT / (unit + '.d/50-hestia-maintenance.conf'))
            definitions[unit] = [{'path': str(path), 'sha256': l._sha(files[path])} for path in paths]
        spec = self.runtime.spec; target = None
        if spec.external_uploads:
            target = l._target(l.LauncherTarget(spec.instance, self.runtime.source_commit, l.get_release(self.runtime.source_commit).tree,
                str(spec.webroot), str(spec.maintenance_directory.parent), str(spec.maintenance_directory),
                account.pw_uid, account.pw_gid, provenance['host_id'], provenance['boot_id']))
        return {'instance': spec.instance, 'root': str(spec.root), 'webroot': str(spec.webroot),
            'maintenance': str(self.runtime._scope(account).directory), 'uid': account.pw_uid, 'gid': account.pw_gid,
            'runtime_plan_sha256': l._sha(plan), 'cleaner_plan_sha256': l._sha(cleaner_plan) if cleaner_plan else None,
            'target': target, 'definitions': definitions}

    def collect(self, *, previous=None):
        try:
            started, clock = int(time.time()), time.monotonic()
            provenance = _provenance(); profile = self._profile(provenance)
            rows = []
            for unit, definitions in profile['definitions'].items():
                value = _show(unit)
                expected = {'Id': unit, 'LoadState': 'loaded', 'FragmentPath': definitions[0]['path'],
                    'DropInPaths': definitions[1]['path'] if len(definitions) == 2 else '', 'NeedDaemonReload': 'no'}
                require(all(value[k] == v for k, v in expected.items()), 'SYSTEMD_OBSERVATION_DEFINITION_CHANGED')
                for name in ('ActiveState', 'SubState'):
                    require(re.fullmatch('[a-z][a-z0-9-]{0,63}', value[name]) is not None)
                require(value['Job'] == '' or re.fullmatch('[1-9][0-9]{0,19}', value['Job']) is not None)
                if unit.endswith('.timer'):
                    require(value['Unit'] == unit.replace('.timer', '.service'), 'SYSTEMD_OBSERVATION_DEFINITION_CHANGED')
                else:
                    for name in ('MainPID', 'ControlPID'): require(re.fullmatch('[0-9]{1,10}', value[name]) is not None)
                    if value['ControlGroup']: l._path(value['ControlGroup'], root=True)
                    require(re.fullmatch('[a-z][a-z0-9-]{0,63}', value['Result']) is not None)
                rows.append({'name': unit, 'definitions': definitions, 'properties': value})
            require(profile == self._profile(provenance) and provenance == _provenance(),
                    'SYSTEMD_OBSERVATION_CHANGED_DURING_READ')
            finished, elapsed = int(time.time()), time.monotonic() - clock
            require(0 <= finished - started <= COLLECTION_SECONDS and 0 <= elapsed <= COLLECTION_SECONDS,
                    'SYSTEMD_OBSERVATION_STALE')
            content = {'profile': profile, 'provenance': provenance, 'units': rows}
            if previous is not None:
                require(type(previous) is SystemdSample and type(previous._canonical) is bytes
                        and len(previous._canonical) <= l.MAX_BYTES, 'SYSTEMD_OBSERVATION_PREVIOUS_REJECTED')
                prior = previous.private_manifest()
                require(prior['finished_at'] <= started and prior['content'] == content,
                        'SYSTEMD_OBSERVATION_CHANGED')
            return SystemdSample(l._json({'version': 1, 'started_at': started, 'finished_at': finished, 'content': content}))
        except SystemdObservationError: raise
        except Exception: raise SystemdObservationError('SYSTEMD_OBSERVATION_UNAVAILABLE') from None
