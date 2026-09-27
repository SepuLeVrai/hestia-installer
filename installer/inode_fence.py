"""Durable Ext4 inode barrier for provisioned data, including ordinary root I/O.

Explicit removal of immutable flags, raw devices and kernel/mount administration
remain trusted administrative actions, not operations this barrier can contain.
"""
from array import array
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
import struct
import time

from installer import data_access as da, backup_files as files
from installer.model import strict_json_loads

fs, f, p = da.fs, da.f, da.p
MARKER = 'inode-fence.attempt'
RELEASE = 'inode-fence.release'
IMMUTABLE = 0x10
BASE_FLAGS = 0x80000 | 0x1000  # Extents and directory index, never modified here.
MAX_ENTRIES = 8192
MAX_JOURNAL = 4 * 1024 * 1024
MAX_SECONDS = 60


class InodeFenceError(RuntimeError): pass


def require(ok, code='INODE_FENCE_PROFILE_REJECTED'):
    if not ok: raise InodeFenceError(code)


def _flags(fd, value=None):
    system = os.uname(); version = re.match(r'^(\d+)\.(\d+)', system.release)
    require(system.sysname == 'Linux' and version is not None
            and tuple(map(int, version.groups())) >= (6, 1)
            and struct.calcsize('l') == 8 and system.machine in ('x86_64', 'aarch64'),
            'INODE_FENCE_PLATFORM_UNSUPPORTED')
    data = array('I', [0 if value is None else value])
    fcntl.ioctl(fd, 0x80086601 if value is None else 0x40086602, data, True)
    return data[0]


def _bounded(path, limit):
    with open(path, 'rb', buffering=0) as stream: raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'INODE_FENCE_LIMIT')
    return raw.decode('ascii')


def _mount_id(fd):
    rows = [line.split() for line in _bounded('/proc/self/fdinfo/' + str(fd), 4096).splitlines()
            if line.startswith('mnt_id:')]
    require(len(rows) == 1 and len(rows[0]) == 2 and rows[0][1].isdigit())
    return int(rows[0][1])


def _ext4(fd):
    mount = _mount_id(fd)
    matches = []
    for line in _bounded('/proc/self/mountinfo', 2 * 1024 * 1024).splitlines():
        fields = line.split(); require(len(fields) >= 10 and fields[0].isdigit())
        if int(fields[0]) == mount:
            split = fields.index('-')
            require(split >= 6 and len(fields) == split + 4)
            matches.append(fields)
    require(len(matches) == 1, 'INODE_FENCE_FILESYSTEM_UNSUPPORTED')
    row = matches[0]; split = row.index('-'); info = os.fstat(fd)
    require(row[split + 1] == 'ext4' and 'rw' in row[5].split(',')
            and 'rw' in row[split + 3].split(',')
            and row[2] == f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}',
            'INODE_FENCE_FILESYSTEM_UNSUPPORTED')
    return mount


def _entry(fd, path, account, device, mount):
    info = os.fstat(fd); directory = stat.S_ISDIR(info.st_mode)
    files._check(fd, account.pw_uid, account.pw_gid, directory=directory, device=device)
    require(_mount_id(fd) == mount, 'INODE_FENCE_NESTED_MOUNT_REJECTED')
    value = _flags(fd)
    require(not value & ~(BASE_FLAGS | IMMUTABLE))
    return {'path': path, 'device': info.st_dev, 'inode': info.st_ino,
            'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid,
            'kind': 'directory' if directory else 'file', 'flags': value}


def _walk(data, *, closed=None):
    """At most 65 descriptors, bounded paths/count/time; no symlink traversal."""
    data.assert_held(); root = data._data; mount = _ext4(root)
    device = os.fstat(root).st_dev; deadline = time.monotonic() + MAX_SECONDS
    records, seen = [], set()
    def visit(fd, relative, depth):
        require(depth <= 64 and len(relative.encode()) <= 2048 and len(records) < MAX_ENTRIES
                and time.monotonic() < deadline, 'INODE_FENCE_LIMIT')
        record = _entry(fd, relative, data._account, device, mount)
        key = (record['device'], record['inode'])
        require(key not in seen, 'INODE_FENCE_ALIAS_INSIDE_TREE_REJECTED'); seen.add(key)
        if closed is not None:
            require(bool(record['flags'] & IMMUTABLE) is closed, 'INODE_FENCE_CHANGED')
        records.append(record)
        if record['kind'] != 'directory': return
        names = _names(fd)
        require(len(names) <= MAX_ENTRIES - len(records), 'INODE_FENCE_LIMIT')
        for name in names:
            files._name(name)
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode))
            child = os.open(name, files.DIRECTORY if stat.S_ISDIR(before.st_mode) else files.REGULAR, dir_fd=fd)
            try:
                opened = os.fstat(child)
                require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), 'INODE_FENCE_CHANGED')
                visit(child, name if relative == '.' else relative + '/' + name, depth + 1)
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require((after.st_dev, after.st_ino) == (opened.st_dev, opened.st_ino), 'INODE_FENCE_CHANGED')
            finally: os.close(child)
        require(names == _names(fd), 'INODE_FENCE_CHANGED')
    visit(root, '.', 0)
    data.assert_held()
    return records


def _names(fd):
    result = []
    with os.scandir(fd) as entries:
        for entry in entries:
            require(len(result) < MAX_ENTRIES, 'INODE_FENCE_LIMIT')
            result.append(entry.name)
    return sorted(result)


def _baseline(records):
    return [{**entry, 'flags': entry['flags'] & ~IMMUTABLE} for entry in records]


def _inputs(data, confirmed):
    require(confirmed is True, 'INODE_FENCE_CONSENT_REQUIRED')
    require(type(data) is da.DataAccessFence, 'INODE_FENCE_LEASE_REQUIRED')
    data.assert_held()
    require(os.getuid() == os.geteuid() == 0, 'INODE_FENCE_ROOT_REQUIRED')


def _value(data, records):
    return {'version': 1, 'instance': data._lease.scope.instance, 'lease_id': data._lease.lease_id,
            'root': str(data._runtime.spec.root / 'data'), 'data_fence_sha256': f._sha(data._raw),
            'filesystem': 'ext4', 'entries': records}


def _load(data):
    raw = f._read(data._lease._directory, MARKER, 0, mode=0o600, limit=MAX_JOURNAL)
    value = strict_json_loads(raw)
    records = _walk(data)
    expected = _value(data, _baseline(records))
    # Comparing canonical bytes also refuses bool-as-integer and unknown fields.
    require(type(value) is dict and raw == p._json(value) == p._json(expected), 'INODE_FENCE_JOURNAL_CHANGED')
    return raw, expected['entries'], records


@contextmanager
def _opened(data, record):
    parts = [] if record['path'] == '.' else record['path'].split('/')
    fd = os.dup(data._data)
    try:
        for index, name in enumerate(parts):
            files._name(name)
            directory = index < len(parts) - 1 or record['kind'] == 'directory'
            child = os.open(name, files.DIRECTORY if directory else files.REGULAR, dir_fd=fd)
            os.close(fd); fd = child
        observed = _entry(fd, record['path'], data._account, record['device'], _mount_id(data._data))
        require(_baseline([observed]) == [record], 'INODE_FENCE_CHANGED')
        yield fd
    finally: os.close(fd)


def _set(data, records, *, closed):
    deadline = time.monotonic() + MAX_SECONDS
    for record in records if closed else reversed(records):
        require(time.monotonic() < deadline, 'INODE_FENCE_LIMIT')
        data.assert_held()
        with _opened(data, record) as fd:
            value = record['flags'] | IMMUTABLE if closed else record['flags']
            _flags(fd, value); os.fsync(fd)
            require(_flags(fd) == value, 'INODE_FENCE_CHANGED')


def _release(data, raw, records):
    _set(data, records, closed=False)
    require(_walk(data, closed=False) == records, 'INODE_FENCE_CHANGED')
    gate = data._lease._directory
    require(f._read(gate, MARKER, 0, mode=0o600, limit=MAX_JOURNAL) == raw, 'INODE_FENCE_JOURNAL_CHANGED')
    os.unlink(RELEASE, dir_fd=gate); os.fsync(gate)
    os.unlink(MARKER, dir_fd=gate); os.fsync(gate)


class InodeFence:
    def __init__(self, data, raw):
        self._data, self._raw = data, raw
        self._pid, self._closed = os.getpid(), False

    def __repr__(self): return '<InodeFence private durable Ext4 barrier>'
    def __reduce__(self): raise TypeError('Inode fences cannot be serialized')

    def assert_held(self):
        try:
            require(not self._closed and self._pid == os.getpid(), 'INODE_FENCE_LEASE_REQUIRED')
            fs._absent(self._data._lease._directory, RELEASE)
            raw, _, observed = _load(self._data)
            require(raw == self._raw and all(e['flags'] & IMMUTABLE for e in observed), 'INODE_FENCE_CHANGED')
        except InodeFenceError: raise
        except Exception: raise InodeFenceError('INODE_FENCE_UNAVAILABLE') from None

    def report(self):
        self.assert_held()
        return {'state': 'PROVISIONED_DATA_INODES_IMMUTABLE', 'filesystem': 'ext4',
                'data_inode_writes_fenced': True, 'same_inode_alias_writes_fenced': True,
                'ordinary_root_data_writes_fenced': True, 'fence_sha256': f._sha(self._raw),
                'automatic_reopening': False, 'administrative_flag_removal_controlled': False,
                'foreign_cli_controlled': False, 'storage_aliases_inventory_complete': False}

    def unseal(self, *, confirmed):
        try: self._unseal(confirmed=confirmed)
        except InodeFenceError: raise
        except Exception: raise InodeFenceError('INODE_FENCE_UNAVAILABLE') from None

    def _unseal(self, *, confirmed):
        _inputs(self._data, confirmed); self.assert_held()
        raw, records, _ = _load(self._data); gate = self._data._lease._directory
        f._write(gate, RELEASE, p._json({'version': 1, 'fence_sha256': f._sha(raw)}), 0, mode=0o600)
        _release(self._data, raw, records); self.close()

    def close(self):
        require(self._pid == os.getpid(), 'INODE_FENCE_LEASE_REQUIRED'); self._closed = True

    def __enter__(self): self.assert_held(); return self
    def __exit__(self, *args): self.close()


def acquire(data, *, confirmed):
    _inputs(data, confirmed)
    try:
        gate = data._lease._directory; fs._absent(gate, MARKER); fs._absent(gate, RELEASE)
        records = _walk(data, closed=False)
        raw = p._json(_value(data, records))
        require(len(raw) <= MAX_JOURNAL, 'INODE_FENCE_LIMIT')
        # A separate bounded data journal, never the small secret writer.
        files._new(gate, MARKER, raw)
        _set(data, records, closed=True)
        result = InodeFence(data, raw); result.assert_held(); return result
    except InodeFenceError: raise
    except Exception: raise InodeFenceError('INODE_FENCE_UNAVAILABLE') from None


def recover(data, *, confirmed):
    _inputs(data, confirmed)
    try:
        fs._absent(data._lease._directory, RELEASE)
        raw, records, _ = _load(data)
        _set(data, records, closed=True)
        result = InodeFence(data, raw); result.assert_held(); return result
    except InodeFenceError: raise
    except Exception: raise InodeFenceError('INODE_FENCE_UNAVAILABLE') from None


def recover_unseal(data, *, confirmed):
    """Only finish an already journalled explicit release, never infer consent."""
    _inputs(data, confirmed)
    try:
        raw, records, _ = _load(data)
        release = f._read(data._lease._directory, RELEASE, 0, mode=0o600)
        require(release == p._json({'version': 1, 'fence_sha256': f._sha(raw)}), 'INODE_FENCE_JOURNAL_CHANGED')
        _release(data, raw, records)
    except InodeFenceError: raise
    except Exception: raise InodeFenceError('INODE_FENCE_UNAVAILABLE') from None
