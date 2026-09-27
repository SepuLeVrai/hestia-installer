"""Persistent Ext4 protection of a provisioned instance's configuration slot.

The maintenance subtree remains writable for its own journals. Web code,
external configuration paths and administrative removal of flags are not fenced.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import os
import stat
import time

from installer import inode_fence as inode, maintenance as m, provisioned_admission as admission
from installer.model import strict_json_loads

fs, f, p = inode.fs, inode.f, inode.p
MARKER = 'configuration-inodes.attempt'
RELEASE = 'configuration-inodes.release'
MAX_ENTRIES = 128
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_JOURNAL = 128 * 1024


class ConfigurationFenceError(RuntimeError): pass


def require(ok, code='CONFIGURATION_FENCE_PROFILE_REJECTED'):
    if not ok: raise ConfigurationFenceError(code)


def _flags(fd, value=None): return inode._flags(fd, value)


def _inputs(lease, configuration, confirmed):
    require(confirmed is True, 'CONFIGURATION_FENCE_CONSENT_REQUIRED')
    require(type(lease) is m.MaintenanceLease and type(configuration) is admission.ConfigurationLease,
            'CONFIGURATION_FENCE_LEASE_REQUIRED')
    lease.assert_held(); configuration.assert_held()
    require(os.getuid() == os.geteuid() == 0, 'CONFIGURATION_FENCE_ROOT_REQUIRED')


def _entry(fd, name, lease, mount):
    before = os.fstat(fd); directory = name == '.'
    require((stat.S_ISDIR(before.st_mode) if directory else stat.S_ISREG(before.st_mode))
            and before.st_uid == 0 and before.st_gid in (0, lease.scope.web_gid))
    require(inode._mount_id(fd) == mount, 'CONFIGURATION_FENCE_NESTED_MOUNT_REJECTED')
    fs._no_acl(fd)
    if directory:
        require(before.st_gid == lease.scope.web_gid and stat.S_IMODE(before.st_mode) == 0o750)
        size, digest = 0, None
    else:
        require(before.st_nlink == 1 and not before.st_mode & (0o7113 if name == 'assistant.json' else 0o7133)
                and before.st_size <= MAX_FILE_BYTES)
        os.lseek(fd, 0, os.SEEK_SET); raw = bytearray()
        while len(raw) <= MAX_FILE_BYTES:
            block = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - len(raw)))
            if not block: break
            raw.extend(block)
        after = os.fstat(fd)
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
            for field in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')),
            'CONFIGURATION_FENCE_CHANGED')
        size, digest = len(raw), hashlib.sha256(raw).hexdigest()
    flags = _flags(fd)
    require(not flags & ~(inode.BASE_FLAGS | inode.IMMUTABLE))
    return {'path': name, 'device': before.st_dev, 'inode': before.st_ino,
            'uid': before.st_uid, 'gid': before.st_gid, 'mode': stat.S_IMODE(before.st_mode),
            'kind': 'directory' if directory else 'file', 'bytes': size, 'sha256': digest, 'flags': flags}


def _names(root):
    names = []
    with os.scandir(root) as entries:
        for entry in entries:
            require(len(names) < MAX_ENTRIES, 'CONFIGURATION_FENCE_LIMIT')
            inode.files._name(entry.name); names.append(entry.name)
    return sorted(names)


def _walk(lease, configuration, root, *, closed=None):
    lease.assert_held(); configuration.assert_held()
    with fs._directory(lease.scope.directory.parent) as named:
        first, second, locked = os.fstat(named), os.fstat(root), os.fstat(configuration._conf)
        require((first.st_dev, first.st_ino) == (second.st_dev, second.st_ino) == (locked.st_dev, locked.st_ino),
                'CONFIGURATION_FENCE_CHANGED')
    mount = inode._ext4(root); records = [_entry(root, '.', lease, mount)]
    names = _names(root); require('maintenance' in names)
    before = os.stat('maintenance', dir_fd=root, follow_symlinks=False); gate = os.fstat(lease._directory)
    require(stat.S_ISDIR(before.st_mode) and (before.st_dev, before.st_ino) == (gate.st_dev, gate.st_ino))
    total = 0; deadline = time.monotonic() + inode.MAX_SECONDS
    for name in names:
        if name == 'maintenance': continue
        require(time.monotonic() < deadline, 'CONFIGURATION_FENCE_LIMIT')
        fd = os.open(name, inode.files.REGULAR, dir_fd=root)
        try:
            record = _entry(fd, name, lease, mount)
            named = os.stat(name, dir_fd=root, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (record['device'], record['inode']), 'CONFIGURATION_FENCE_CHANGED')
            total += record['bytes']; require(total <= MAX_TOTAL_BYTES, 'CONFIGURATION_FENCE_LIMIT')
            records.append(record)
        finally: os.close(fd)
    require(names == _names(root), 'CONFIGURATION_FENCE_CHANGED')
    if closed is not None:
        require(all(bool(record['flags'] & inode.IMMUTABLE) is closed for record in records), 'CONFIGURATION_FENCE_CHANGED')
    lease.assert_held(); configuration.assert_held()
    return records


def _value(lease, records):
    return {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
            'root': str(lease.scope.directory.parent), 'filesystem': 'ext4',
            'excluded_subtree': 'maintenance', 'entries': records}


def _load(lease, configuration, root):
    raw = f._read(lease._directory, MARKER, 0, mode=0o600, limit=MAX_JOURNAL)
    value = strict_json_loads(raw); records = _walk(lease, configuration, root)
    expected = _value(lease, inode._baseline(records))
    require(type(value) is dict and raw == p._json(value) == p._json(expected), 'CONFIGURATION_FENCE_JOURNAL_CHANGED')
    return raw, expected['entries'], records


@contextmanager
def _opened(root, record):
    fd = os.dup(root) if record['path'] == '.' else os.open(record['path'], inode.files.REGULAR, dir_fd=root)
    try: yield fd
    finally: os.close(fd)


def _set(lease, configuration, root, records, *, closed):
    deadline = time.monotonic() + inode.MAX_SECONDS; mount = inode._ext4(root)
    for record in records if closed else reversed(records):
        require(time.monotonic() < deadline, 'CONFIGURATION_FENCE_LIMIT')
        lease.assert_held(); configuration.assert_held()
        with _opened(root, record) as fd:
            require(inode._baseline([_entry(fd, record['path'], lease, mount)]) == [record], 'CONFIGURATION_FENCE_CHANGED')
            flags = record['flags'] | inode.IMMUTABLE if closed else record['flags']
            _flags(fd, flags); os.fsync(fd)
            require(_flags(fd) == flags, 'CONFIGURATION_FENCE_CHANGED')


def _release(lease, configuration, root, raw, records):
    _set(lease, configuration, root, records, closed=False)
    require(_walk(lease, configuration, root, closed=False) == records, 'CONFIGURATION_FENCE_CHANGED')
    gate = lease._directory
    require(f._read(gate, MARKER, 0, mode=0o600, limit=MAX_JOURNAL) == raw, 'CONFIGURATION_FENCE_JOURNAL_CHANGED')
    os.unlink(RELEASE, dir_fd=gate); os.fsync(gate)
    os.unlink(MARKER, dir_fd=gate); os.fsync(gate)


class ConfigurationFence:
    def __init__(self, lease, configuration, manager, root, raw):
        self._lease, self._configuration, self._manager, self._root, self._raw = lease, configuration, manager, root, raw
        self._pid, self._closed = os.getpid(), False

    def __repr__(self): return '<ConfigurationFence private durable Ext4 protection>'
    def __reduce__(self): raise TypeError('Configuration fences cannot be serialized')

    def assert_held(self):
        try:
            require(not self._closed and self._pid == os.getpid(), 'CONFIGURATION_FENCE_LEASE_REQUIRED')
            fs._absent(self._lease._directory, RELEASE)
            raw, _, records = _load(self._lease, self._configuration, self._root)
            require(raw == self._raw and all(record['flags'] & inode.IMMUTABLE for record in records),
                    'CONFIGURATION_FENCE_CHANGED')
        except ConfigurationFenceError: raise
        except Exception: raise ConfigurationFenceError('CONFIGURATION_FENCE_UNAVAILABLE') from None

    def report(self):
        self.assert_held()
        return {'state': 'PROVISIONED_CONFIGURATION_SLOT_IMMUTABLE', 'filesystem': 'ext4',
                'configuration_slot_inodes_fenced': True, 'ordinary_root_settings_writes_fenced': True,
                'fence_sha256': f._sha(self._raw), 'automatic_reopening': False,
                'maintenance_subtree_fenced': False, 'web_code_fenced': False,
                'external_configuration_fenced': False, 'administrative_flag_removal_controlled': False,
                'foreign_cli_controlled': False}

    def unseal(self, *, confirmed):
        try:
            _inputs(self._lease, self._configuration, confirmed); self.assert_held()
            raw, records, _ = _load(self._lease, self._configuration, self._root)
            f._write(self._lease._directory, RELEASE, p._json({'version': 1, 'fence_sha256': f._sha(raw)}), 0, mode=0o600)
            _release(self._lease, self._configuration, self._root, raw, records); self.close()
        except ConfigurationFenceError: raise
        except Exception: raise ConfigurationFenceError('CONFIGURATION_FENCE_UNAVAILABLE') from None

    def close(self):
        require(self._pid == os.getpid(), 'CONFIGURATION_FENCE_LEASE_REQUIRED')
        if not self._closed: self._closed = True; self._manager.close()

    def __enter__(self): self.assert_held(); return self
    def __exit__(self, *args): self.close()


def _acquire(lease, configuration, *, confirmed, recovery):
    _inputs(lease, configuration, confirmed); manager = ExitStack()
    try:
        root = manager.enter_context(fs._directory(lease.scope.directory.parent))
        gate = lease._directory; fs._absent(gate, RELEASE)
        if recovery:
            raw, records, _ = _load(lease, configuration, root)
        else:
            fs._absent(gate, MARKER)
            records = _walk(lease, configuration, root, closed=False); raw = p._json(_value(lease, records))
            require(len(raw) <= MAX_JOURNAL, 'CONFIGURATION_FENCE_LIMIT')
            inode.files._new(gate, MARKER, raw)
        _set(lease, configuration, root, records, closed=True)
        result = ConfigurationFence(lease, configuration, manager, root, raw); result.assert_held(); return result
    except Exception as error:
        manager.close()
        if isinstance(error, ConfigurationFenceError): raise
        raise ConfigurationFenceError('CONFIGURATION_FENCE_UNAVAILABLE') from None


def acquire(lease, configuration, *, confirmed):
    return _acquire(lease, configuration, confirmed=confirmed, recovery=False)


def recover(lease, configuration, *, confirmed):
    return _acquire(lease, configuration, confirmed=confirmed, recovery=True)


def recover_unseal(lease, configuration, *, confirmed):
    _inputs(lease, configuration, confirmed)
    try:
        with fs._directory(lease.scope.directory.parent) as root:
            raw, records, _ = _load(lease, configuration, root)
            release = f._read(lease._directory, RELEASE, 0, mode=0o600)
            require(release == p._json({'version': 1, 'fence_sha256': f._sha(raw)}), 'CONFIGURATION_FENCE_JOURNAL_CHANGED')
            _release(lease, configuration, root, raw, records)
    except ConfigurationFenceError: raise
    except Exception: raise ConfigurationFenceError('CONFIGURATION_FENCE_UNAVAILABLE') from None
