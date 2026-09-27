"""Durable pathname-access fence for the provisioned application's data roots.

This denies future unprivileged traversal through the canonical parent. It is
not a mount-namespace/alias inventory or a defence against privileged writers.
No context-manager exit, failure or controller death reopens application data.
"""
from contextlib import ExitStack
import os
import re
import stat

from installer import http_runtime as h, maintenance as m, http_drain as hd
from installer.model import strict_json_loads

fs, f, p = h.fs, h.f, h.p
MARKER = 'data-access.attempt'


class DataAccessError(RuntimeError): pass


def require(ok, code='DATA_ACCESS_PROFILE_REJECTED'):
    if not ok: raise DataAccessError(code)


def _inputs(runtime, lease):
    require(type(runtime) is h.HttpRuntime and runtime.spec.external_uploads
        and type(lease) is m.MaintenanceLease, 'DATA_ACCESS_LEASE_REQUIRED')
    lease.assert_held(); spec = runtime.spec
    require(spec.instance == lease.scope.instance and spec.maintenance_directory == lease.scope.directory)
    account = h._identity(spec.service_user)
    require(account.pw_gid == lease.scope.web_gid)
    return account


def _record(runtime, account, gate, data, *, lease_id=None):
    raw = f._read(gate, MARKER, account.pw_gid, limit=2048)
    value = strict_json_loads(raw)
    info = os.fstat(data)
    expected = {'version': 1, 'instance': runtime.spec.instance,
        'lease_id': value.get('lease_id') if type(value) is dict else None,
        'root': str(runtime.spec.root / 'data'), 'device': info.st_dev,
        'inode': info.st_ino, 'gid': account.pw_gid, 'open_mode': 0o750, 'closed_mode': 0o700}
    require(type(value) is dict and value == expected and p._json(value) == raw
        and all(type(value[k]) is int for k in ('version', 'device', 'inode', 'gid', 'open_mode', 'closed_mode')))
    require(type(value['lease_id']) is str and re.fullmatch('[a-f0-9]{32}', value['lease_id'])
        and (lease_id is None or value['lease_id'] == lease_id))
    require(info.st_uid == 0 and info.st_gid == account.pw_gid and stat.S_ISDIR(info.st_mode))
    fs._no_acl(data)
    scope = runtime._scope(account)
    flag = scope._flag(gate)
    require(flag is not None and strict_json_loads(flag)['lease_id'] == value['lease_id'])
    return raw


def expected_mode(runtime, account):
    """Read-only runtime audit: an exact durable closed state is legitimate."""
    scope = runtime._scope(account)
    with scope._open() as (gate, _):
        try: os.stat(MARKER, dir_fd=gate, follow_symlinks=False)
        except FileNotFoundError: return 0o750
        with fs._directory(runtime.spec.root / 'data') as data:
            _record(runtime, account, gate, data)
            require(stat.S_IMODE(os.fstat(data).st_mode) == 0o700, 'DATA_ACCESS_INCOMPLETE')
    return 0o700


class DataAccessFence:
    def __init__(self, runtime, lease, account, manager, data, raw):
        self._runtime, self._lease, self._account = runtime, lease, account
        self._manager, self._data, self._raw = manager, data, raw
        self._pid, self._closed = os.getpid(), False

    def __repr__(self): return '<DataAccessFence private durable pathname barrier>'
    def __reduce__(self): raise TypeError('Data access fences cannot be serialized')

    def assert_held(self):
        try: self._assert_held()
        except DataAccessError: raise
        except Exception: raise DataAccessError('DATA_ACCESS_UNAVAILABLE') from None

    def _assert_held(self):
        require(not self._closed and self._pid == os.getpid(), 'DATA_ACCESS_LEASE_REQUIRED')
        self._lease.assert_held()
        with fs._directory(self._runtime.spec.root / 'data') as named:
            current, pinned = os.fstat(named), os.fstat(self._data)
            require((current.st_dev, current.st_ino) == (pinned.st_dev, pinned.st_ino), 'DATA_ACCESS_CHANGED')
            require(_record(self._runtime, self._account, self._lease._directory, named,
                lease_id=self._lease.lease_id) == self._raw and stat.S_IMODE(current.st_mode) == 0o700,
                'DATA_ACCESS_CHANGED')

    def report(self):
        self.assert_held()
        return {'state': 'PROVISIONED_DATA_PATHS_CLOSED', 'canonical_data_paths_fenced': True,
            'fence_sha256': f._sha(self._raw), 'automatic_reopening': False,
            'foreign_cli_controlled': False, 'privileged_writers_controlled': False,
            'storage_aliases_inventory_complete': False}

    def reopen(self, *, confirmed):
        require(confirmed is True, 'DATA_ACCESS_CONSENT_REQUIRED'); self.assert_held()
        self._runtime._inspect_configuration()
        hd.identity_census(self._account.pw_uid, self._account.pw_gid, ())
        os.fchmod(self._data, 0o750); os.fsync(self._data)
        os.unlink(MARKER, dir_fd=self._lease._directory); os.fsync(self._lease._directory)
        self.close()
        # The separate maintenance attempt and stopped services remain intact.

    def close(self):
        if not self._closed:
            require(self._pid == os.getpid(), 'DATA_ACCESS_LEASE_REQUIRED')
            self._closed = True; self._manager.close()

    def __enter__(self): self.assert_held(); return self
    def __exit__(self, *args): self.close()


def _acquire(runtime, lease, *, confirmed, recover):
    require(confirmed is True, 'DATA_ACCESS_CONSENT_REQUIRED')
    account = _inputs(runtime, lease)
    manager = ExitStack()
    try:
        data = manager.enter_context(fs._directory(runtime.spec.root / 'data'))
        gate = lease._directory
        info = os.fstat(data)
        require(info.st_uid == 0 and info.st_gid == account.pw_gid)
        if recover:
            raw = _record(runtime, account, gate, data, lease_id=lease.lease_id)
            require(stat.S_IMODE(info.st_mode) in (0o750, 0o700))
        else:
            runtime._inspect_configuration()
            fs._absent(gate, MARKER)
            require(stat.S_IMODE(info.st_mode) == 0o750)
            raw = p._json({'version': 1, 'instance': runtime.spec.instance, 'lease_id': lease.lease_id,
                'root': str(runtime.spec.root / 'data'), 'device': info.st_dev, 'inode': info.st_ino,
                'gid': account.pw_gid, 'open_mode': 0o750, 'closed_mode': 0o700})
            f._write(gate, MARKER, raw, account.pw_gid)
        # Durable intent precedes closure. Close before the second census so a
        # process which opened a directory in the intervening window is refused.
        os.fchmod(data, 0o700); os.fsync(data)
        hd.identity_census(account.pw_uid, account.pw_gid, ())
        result = DataAccessFence(runtime, lease, account, manager, data, raw)
        result.assert_held(); runtime._inspect_configuration()
        return result
    except BaseException:
        manager.close()  # Never restore permissions or remove durable intent.
        raise


def acquire(barrier, *, confirmed):
    require(confirmed is True, 'DATA_ACCESS_CONSENT_REQUIRED')
    require(type(barrier) is hd.HttpDrainLease, 'DATA_ACCESS_LEASE_REQUIRED')
    try:
        barrier.assert_held()
        return _acquire(barrier._drain.runtime, barrier.maintenance_lease, confirmed=True, recover=False)
    except DataAccessError: raise
    except Exception: raise DataAccessError('DATA_ACCESS_UNAVAILABLE') from None


def recover(runtime, lease, *, confirmed):
    """Complete only the exact interrupted closure; never reopen or resume."""
    try: return _acquire(runtime, lease, confirmed=confirmed, recover=True)
    except DataAccessError: raise
    except Exception: raise DataAccessError('DATA_ACCESS_UNAVAILABLE') from None
