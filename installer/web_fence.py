"""Persistent Ext4 protection of the complete deployed Web tree and pointers.

No exclusions or symlink traversal. External host configuration and explicit
administrative flag/mount/kernel changes remain outside this inode guarantee.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import os
import stat
import time

from installer import inode_fence as inode, http_drain as hd
from installer.model import strict_json_loads

fs, f, p = inode.fs, inode.f, inode.p
MARKER = 'web-inodes.attempt'
RELEASE = 'web-inodes.release'
MAX_ENTRIES = 10000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_JOURNAL = 4 * 1024 * 1024


class WebFenceError(RuntimeError): pass


def require(ok, code='WEB_FENCE_PROFILE_REJECTED'):
    if not ok: raise WebFenceError(code)


def _flags(fd, value=None): return inode._flags(fd, value)


def _inputs(barrier, confirmed):
    require(confirmed is True, 'WEB_FENCE_CONSENT_REQUIRED')
    require(type(barrier) is hd.HttpDrainLease, 'WEB_FENCE_LEASE_REQUIRED')
    barrier.assert_held()
    spec = barrier._drain.runtime.spec
    require(spec.external_uploads and spec.instance == barrier._lease.scope.instance
            and spec.maintenance_directory == barrier._lease.scope.directory, 'WEB_FENCE_LEASE_REQUIRED')
    require(os.getuid() == os.geteuid() == 0, 'WEB_FENCE_ROOT_REQUIRED')


def _entry(fd, name, barrier, mount):
    before = os.fstat(fd); directory = stat.S_ISDIR(before.st_mode)
    require((directory or stat.S_ISREG(before.st_mode)) and before.st_uid == 0)
    require(inode._mount_id(fd) == mount, 'WEB_FENCE_NESTED_MOUNT_REJECTED')
    fs._no_acl(fd)
    if directory:
        require(before.st_gid == 0 and stat.S_IMODE(before.st_mode) == 0o755)
        size, digest = 0, None
    else:
        require(before.st_nlink == 1 and before.st_size <= MAX_FILE_BYTES)
        expected = (barrier._lease.scope.web_gid, (0o640,)) if name in ('includes/db.php','install.lock') else (0, (0o644,0o755))
        require(before.st_gid == expected[0] and stat.S_IMODE(before.st_mode) in expected[1])
        os.lseek(fd, 0, os.SEEK_SET); raw = bytearray()
        while len(raw) <= MAX_FILE_BYTES:
            block = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - len(raw)))
            if not block: break
            raw.extend(block)
        after = os.fstat(fd)
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
            for field in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')),
            'WEB_FENCE_CHANGED')
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
            require(len(names) < MAX_ENTRIES, 'WEB_FENCE_LIMIT')
            inode.files._name(entry.name); names.append(entry.name)
    return sorted(names)


def _walk(barrier, root, *, closed=None):
    barrier._lease.assert_held()
    with fs._directory(barrier._drain.runtime.spec.webroot) as named:
        first, second = os.fstat(named), os.fstat(root)
        require((first.st_dev, first.st_ino) == (second.st_dev, second.st_ino), 'WEB_FENCE_CHANGED')
    mount = inode._ext4(root); records = []; seen = set()
    total = 0; deadline = time.monotonic() + inode.MAX_SECONDS
    def visit(fd, relative, depth):
        nonlocal total
        require(depth <= 64 and len(relative.encode()) <= 2048 and len(records) < MAX_ENTRIES
                and time.monotonic() < deadline, 'WEB_FENCE_LIMIT')
        record = _entry(fd, relative, barrier, mount)
        key = record['device'], record['inode']; require(key not in seen, 'WEB_FENCE_ALIAS_REJECTED'); seen.add(key)
        total += record['bytes']; require(total <= MAX_TOTAL_BYTES, 'WEB_FENCE_LIMIT'); records.append(record)
        if record['kind'] != 'directory': return
        names = _names(fd)
        for name in names:
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode))
            child = os.open(name, inode.files.DIRECTORY if stat.S_ISDIR(before.st_mode) else inode.files.REGULAR, dir_fd=fd)
            try:
                opened = os.fstat(child)
                require((before.st_dev,before.st_ino) == (opened.st_dev,opened.st_ino), 'WEB_FENCE_CHANGED')
                visit(child, name if relative == '.' else relative + '/' + name, depth + 1)
                named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require((named.st_dev,named.st_ino) == (opened.st_dev,opened.st_ino), 'WEB_FENCE_CHANGED')
            finally: os.close(child)
        require(names == _names(fd), 'WEB_FENCE_CHANGED')
    visit(root, '.', 0)
    indexed = {record['path']:record for record in records}
    require(all(name in indexed and indexed[name]['kind'] == 'file' for name in ('includes/db.php','install.lock')))
    if closed is not None:
        require(all(bool(record['flags'] & inode.IMMUTABLE) is closed for record in records), 'WEB_FENCE_CHANGED')
    barrier._lease.assert_held()
    return records


def _value(barrier, records):
    return {'version': 1, 'instance': barrier._lease.scope.instance, 'lease_id': barrier._lease.lease_id,
            'root': str(barrier._drain.runtime.spec.webroot), 'filesystem': 'ext4',
            'service_profile_sha256': f._sha(barrier._profile), 'entries': records}


def _load(barrier, root):
    raw = f._read(barrier._lease._directory, MARKER, 0, mode=0o600, limit=MAX_JOURNAL)
    value = strict_json_loads(raw); records = _walk(barrier, root)
    expected = _value(barrier, inode._baseline(records))
    require(type(value) is dict and raw == p._json(value) == p._json(expected), 'WEB_FENCE_JOURNAL_CHANGED')
    return raw, expected['entries'], records


@contextmanager
def _opened(root, record):
    parts = [] if record['path'] == '.' else record['path'].split('/')
    fd = os.dup(root)
    try:
        for index, name in enumerate(parts):
            inode.files._name(name)
            directory = index < len(parts)-1 or record['kind'] == 'directory'
            child = os.open(name, inode.files.DIRECTORY if directory else inode.files.REGULAR, dir_fd=fd)
            os.close(fd); fd = child
        yield fd
    finally: os.close(fd)


def _set(barrier, root, records, *, closed):
    deadline = time.monotonic() + inode.MAX_SECONDS; mount = inode._ext4(root)
    for record in records if closed else reversed(records):
        require(time.monotonic() < deadline, 'WEB_FENCE_LIMIT')
        barrier._lease.assert_held()
        with _opened(root, record) as fd:
            observed = _entry(fd, record['path'], barrier, mount)
            require(inode._baseline([observed]) == [record], 'WEB_FENCE_CHANGED')
            flags = record['flags'] | inode.IMMUTABLE if closed else record['flags']
            # Recovery must not reapply flags to an already immutable inode.
            # Still synchronize a matching state left by an interrupted setter.
            if observed['flags'] != flags: _flags(fd, flags)
            os.fsync(fd)
            require(_flags(fd) == flags, 'WEB_FENCE_CHANGED')


def _release(barrier, root, raw, records):
    _set(barrier, root, records, closed=False)
    require(_walk(barrier, root, closed=False) == records, 'WEB_FENCE_CHANGED')
    gate = barrier._lease._directory
    require(f._read(gate, MARKER, 0, mode=0o600, limit=MAX_JOURNAL) == raw, 'WEB_FENCE_JOURNAL_CHANGED')
    os.unlink(RELEASE, dir_fd=gate); os.fsync(gate)
    os.unlink(MARKER, dir_fd=gate); os.fsync(gate)


class WebFence:
    def __init__(self, barrier, manager, root, raw):
        self._barrier, self._manager, self._root, self._raw = barrier, manager, root, raw
        self._pid, self._closed = os.getpid(), False

    def __repr__(self): return '<WebFence private durable Ext4 protection>'
    def __reduce__(self): raise TypeError('Web fences cannot be serialized')

    def assert_held(self):
        try:
            require(not self._closed and self._pid == os.getpid(), 'WEB_FENCE_LEASE_REQUIRED')
            self._barrier.assert_held()
            fs._absent(self._barrier._lease._directory, RELEASE)
            raw, _, records = _load(self._barrier, self._root)
            require(raw == self._raw and all(record['flags'] & inode.IMMUTABLE for record in records),
                    'WEB_FENCE_CHANGED')
        except WebFenceError: raise
        except Exception: raise WebFenceError('WEB_FENCE_UNAVAILABLE') from None

    def report(self):
        self.assert_held()
        return {'state': 'PROVISIONED_WEB_INODES_IMMUTABLE', 'filesystem': 'ext4',
                'web_code_fenced': True, 'web_activation_pointers_fenced': True,
                'ordinary_root_web_writes_fenced': True,
                'fence_sha256': f._sha(self._raw), 'automatic_reopening': False,
                'external_configuration_fenced': False, 'administrative_flag_removal_controlled': False,
                'foreign_cli_controlled': False}

    def unseal(self, *, confirmed):
        try:
            _inputs(self._barrier, confirmed); self.assert_held()
            raw, records, _ = _load(self._barrier, self._root)
            f._write(self._barrier._lease._directory, RELEASE, p._json({'version': 1, 'fence_sha256': f._sha(raw)}), 0, mode=0o600)
            _release(self._barrier, self._root, raw, records); self.close()
        except WebFenceError: raise
        except Exception: raise WebFenceError('WEB_FENCE_UNAVAILABLE') from None

    def close(self):
        require(self._pid == os.getpid(), 'WEB_FENCE_LEASE_REQUIRED')
        if not self._closed: self._closed = True; self._manager.close()

    def __enter__(self): self.assert_held(); return self
    def __exit__(self, *args): self.close()


def _acquire(barrier, *, confirmed, recovery):
    _inputs(barrier, confirmed); manager = ExitStack()
    try:
        root = manager.enter_context(fs._directory(barrier._drain.runtime.spec.webroot))
        gate = barrier._lease._directory; fs._absent(gate, RELEASE)
        if recovery:
            raw, records, _ = _load(barrier, root)
        else:
            fs._absent(gate, MARKER)
            records = _walk(barrier, root, closed=False); raw = p._json(_value(barrier, records))
            require(len(raw) <= MAX_JOURNAL, 'WEB_FENCE_LIMIT')
            inode.files._new(gate, MARKER, raw)
        _set(barrier, root, records, closed=True)
        result = WebFence(barrier, manager, root, raw); result.assert_held(); return result
    except Exception as error:
        manager.close()
        if isinstance(error, WebFenceError): raise
        raise WebFenceError('WEB_FENCE_UNAVAILABLE') from None


def acquire(barrier, *, confirmed):
    return _acquire(barrier, confirmed=confirmed, recovery=False)


def recover(barrier, *, confirmed):
    return _acquire(barrier, confirmed=confirmed, recovery=True)


def recover_unseal(barrier, *, confirmed):
    _inputs(barrier, confirmed)
    try:
        with fs._directory(barrier._drain.runtime.spec.webroot) as root:
            raw, records, _ = _load(barrier, root)
            release = f._read(barrier._lease._directory, RELEASE, 0, mode=0o600)
            require(release == p._json({'version': 1, 'fence_sha256': f._sha(raw)}), 'WEB_FENCE_JOURNAL_CHANGED')
            _release(barrier, root, raw, records)
    except WebFenceError: raise
    except Exception: raise WebFenceError('WEB_FENCE_UNAVAILABLE') from None
