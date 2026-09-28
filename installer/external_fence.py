"""Durable reservations of two absent legacy paths in a provisioned profile.

Only newly prepared, empty root files are linked and made immutable. Existing
host objects are never adopted. Parent/mount/flag administration is excluded.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat

from installer import inode_fence as inode, maintenance as m
from installer.model import strict_json_loads

fs, f, p = inode.fs, inode.f, inode.p
PATHS = (Path('/etc/hestia/conf_db_ia.php'), Path('/var/lib/hestia-ai'))
PREPARE = 'external-paths.prepare'
MARKER = 'external-paths.attempt'
RELEASE = 'external-paths.release'
MAX_JOURNAL = 8192


class ExternalFenceError(RuntimeError): pass


def require(ok, code='EXTERNAL_FENCE_CHANGED'):
    if not ok: raise ExternalFenceError(code)


def _inputs(lease, confirmed):
    require(confirmed is True, 'EXTERNAL_FENCE_CONSENT_REQUIRED')
    require(type(lease) is m.MaintenanceLease, 'EXTERNAL_FENCE_LEASE_REQUIRED')
    lease.assert_held()
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'EXTERNAL_FENCE_ROOT_REQUIRED')


def _flags(fd, value=None): return inode._flags(fd, value)


def _stat(fd, name):
    try: return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError: return None


def _absent(fd, name):
    require(_stat(fd, name) is None, 'EXTERNAL_FENCE_TARGET_OCCUPIED')


@contextmanager
def _parent(entry):
    with fs._directory(Path(entry['path']).parent) as fd:
        info = os.fstat(fd)
        require(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o755)
        require((info.st_dev, info.st_ino) == (entry['parent_device'], entry['parent_inode']))
        inode._ext4(fd)
        yield fd


def _identity(fd, parent):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0 and info.st_size == 0 and info.st_nlink in (1, 2))
    require(inode._mount_id(fd) == inode._mount_id(parent))
    fs._no_acl(fd)
    flags = _flags(fd); require(not flags & ~(inode.BASE_FLAGS | inode.IMMUTABLE))
    return {'device': info.st_dev, 'inode': info.st_ino, 'flags': flags & ~inode.IMMUTABLE}


def _plan(lease):
    nonce = os.urandom(16).hex(); entries = []
    for index, path in enumerate(PATHS):
        with fs._directory(path.parent) as parent:
            info = os.fstat(parent)
            require(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o755, 'EXTERNAL_FENCE_PROFILE_REJECTED')
            inode._ext4(parent)
            stage = '.hestia-external-' + nonce + '-' + str(index)
            _absent(parent, path.name); _absent(parent, stage)
            entries.append({'path': str(path), 'stage': stage, 'parent_device': info.st_dev, 'parent_inode': info.st_ino})
    return {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id, 'nonce': nonce, 'entries': entries}


def _decode(lease, raw, *, prepared=False):
    require(len(raw) <= MAX_JOURNAL, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
    value = strict_json_loads(raw)
    require(type(value) is dict and set(value) == {'version', 'instance', 'lease_id', 'nonce', 'entries'})
    require(type(value['nonce']) is str and re.fullmatch(r'[a-f0-9]{32}', value['nonce']) is not None)
    require(type(value['entries']) is list and len(value['entries']) == len(PATHS) == 2)
    entries = []
    for index, (entry, path) in enumerate(zip(value['entries'], PATHS)):
        keys = {'path', 'stage', 'parent_device', 'parent_inode'} | (set() if prepared else {'device', 'inode', 'flags'})
        require(type(entry) is dict and set(entry) == keys)
        require(entry['path'] == str(path) and entry['stage'] == '.hestia-external-' + value['nonce'] + '-' + str(index))
        for key in keys - {'path', 'stage'}: require(type(entry[key]) is int and entry[key] >= 0)
        if not prepared: require(not entry['flags'] & ~inode.BASE_FLAGS)
        entries.append(entry)
    expected = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
                'nonce': value['nonce'], 'entries': entries}
    require(raw == p._json(expected), 'EXTERNAL_FENCE_JOURNAL_CHANGED')
    return expected


def _read(lease, name, *, prepared=False):
    raw = f._read(lease._directory, name, 0, mode=0o600, limit=MAX_JOURNAL)
    return raw, _decode(lease, raw, prepared=prepared)


def _prepare(lease, plan, *, recovery):
    entries = []
    for entry in plan['entries']:
        lease.assert_held()
        with _parent(entry) as parent:
            _absent(parent, Path(entry['path']).name)
            try: fd = os.open(entry['stage'], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0, dir_fd=parent)
            except FileExistsError:
                require(recovery, 'EXTERNAL_FENCE_TARGET_OCCUPIED')
                fd = os.open(entry['stage'], inode.files.REGULAR, dir_fd=parent)
            try:
                identity = _identity(fd, parent)
                require(os.fstat(fd).st_nlink == 1 and not _flags(fd) & inode.IMMUTABLE)
                os.fsync(fd); os.fsync(parent)
                entries.append({**entry, **identity})
            finally: os.close(fd)
    raw = p._json({**plan, 'entries': entries})
    require(len(raw) <= MAX_JOURNAL, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
    f._write(lease._directory, MARKER, raw, 0, mode=0o600)
    return raw


@contextmanager
def _stage(entry, parent, *, released=False):
    named = _stat(parent, entry['stage'])
    if named is None:
        require(released and _stat(parent, Path(entry['path']).name) is None)
        yield None; return
    fd = os.open(entry['stage'], inode.files.REGULAR, dir_fd=parent)
    try:
        require(_identity(fd, parent) == {key: entry[key] for key in ('device', 'inode', 'flags')})
        require((named.st_dev, named.st_ino) == (entry['device'], entry['inode']))
        target = _stat(parent, Path(entry['path']).name)
        if target is not None:
            require((target.st_dev, target.st_ino) == (entry['device'], entry['inode']))
        require(os.fstat(fd).st_nlink == (2 if target is not None else 1))
        yield fd
        named = _stat(parent, entry['stage'])
        if named is not None: require((named.st_dev, named.st_ino) == (entry['device'], entry['inode']))
    finally: os.close(fd)


def _seal(lease, value):
    for entry in value['entries']:
        lease.assert_held()
        with _parent(entry) as parent, _stage(entry, parent) as fd:
            name = Path(entry['path']).name
            if _stat(parent, name) is None:
                require(not _flags(fd) & inode.IMMUTABLE)
                os.link(entry['stage'], name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
            require(os.fstat(fd).st_nlink == 2)
            desired = entry['flags'] | inode.IMMUTABLE
            if _flags(fd) != desired: _flags(fd, desired)
            os.fsync(fd); os.fsync(parent)
            require(_flags(fd) == desired)


def assert_reservation(lease, raw):
    """Validate durable state while its maintenance lease lives, even after handle close."""
    _inputs(lease, True)
    fs._absent(lease._directory, PREPARE); fs._absent(lease._directory, RELEASE)
    actual, value = _read(lease, MARKER)
    require(actual == raw, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
    for entry in value['entries']:
        with _parent(entry) as parent, _stage(entry, parent) as fd:
            require(os.fstat(fd).st_nlink == 2 and _flags(fd) == entry['flags'] | inode.IMMUTABLE)


def _release(lease, raw):
    value = _decode(lease, raw)
    for entry in reversed(value['entries']):
        lease.assert_held()
        with _parent(entry) as parent, _stage(entry, parent, released=True) as fd:
            if fd is None: continue
            if _flags(fd) != entry['flags']: _flags(fd, entry['flags'])
            os.fsync(fd); require(_flags(fd) == entry['flags'])
            name = Path(entry['path']).name
            target = _stat(parent, name)
            if target is not None:
                require((target.st_dev, target.st_ino) == (entry['device'], entry['inode']))
                os.unlink(name, dir_fd=parent); os.fsync(parent)
            require(os.fstat(fd).st_nlink == 1)
            named = _stat(parent, entry['stage'])
            require(named is not None and (named.st_dev, named.st_ino) == (entry['device'], entry['inode']))
            os.unlink(entry['stage'], dir_fd=parent); os.fsync(parent)
    for entry in value['entries']:
        with _parent(entry) as parent:
            _absent(parent, Path(entry['path']).name); _absent(parent, entry['stage'])
    if _stat(lease._directory, MARKER) is not None:
        require(_read(lease, MARKER)[0] == raw, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
        os.unlink(MARKER, dir_fd=lease._directory); os.fsync(lease._directory)
    # RELEASE contains the complete binding, so a crash after MARKER removal
    # still has an exact recovery path and continues to block maintenance.
    require(_read(lease, RELEASE)[0] == raw, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
    os.unlink(RELEASE, dir_fd=lease._directory); os.fsync(lease._directory)


class ExternalFence:
    def __init__(self, lease, raw):
        self._lease, self._raw = lease, raw
        self._pid, self._closed = os.getpid(), False

    def __repr__(self): return '<ExternalFence private durable path reservations>'
    def __reduce__(self): raise TypeError('External reservations cannot be serialized')

    def assert_held(self):
        require(not self._closed and self._pid == os.getpid(), 'EXTERNAL_FENCE_LEASE_REQUIRED')
        try: assert_reservation(self._lease, self._raw)
        except ExternalFenceError: raise
        except Exception: raise ExternalFenceError('EXTERNAL_FENCE_UNAVAILABLE') from None

    def report(self):
        self.assert_held()
        return {'state': 'PROVISIONED_LEGACY_PATHS_RESERVED', 'filesystem': 'ext4', 'reserved_paths': 2,
                'legacy_external_paths_reserved': True, 'ordinary_root_legacy_path_writes_fenced': True,
                'fence_sha256': f._sha(self._raw), 'automatic_reopening': False,
                'host_configuration_fenced': False, 'foreign_cli_controlled': False}

    def unseal(self, *, confirmed):
        _inputs(self._lease, confirmed); self.assert_held()
        try:
            f._write(self._lease._directory, RELEASE, self._raw, 0, mode=0o600)
            _release(self._lease, self._raw); self.close()
        except ExternalFenceError: raise
        except Exception: raise ExternalFenceError('EXTERNAL_FENCE_UNAVAILABLE') from None

    def close(self):
        require(self._pid == os.getpid(), 'EXTERNAL_FENCE_LEASE_REQUIRED'); self._closed = True

    def __enter__(self): self.assert_held(); return self
    def __exit__(self, *args): self.close()


def _acquire(lease, *, confirmed, recovery):
    _inputs(lease, confirmed)
    try:
        gate = lease._directory; fs._absent(gate, RELEASE)
        if not recovery:
            fs._absent(gate, PREPARE); fs._absent(gate, MARKER)
            plan = _plan(lease); raw = p._json(plan)
            require(len(raw) <= MAX_JOURNAL, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
            f._write(gate, PREPARE, raw, 0, mode=0o600)
        if _stat(gate, MARKER) is None:
            _, plan = _read(lease, PREPARE, prepared=True)
            _prepare(lease, plan, recovery=recovery)
        raw, value = _read(lease, MARKER)
        if _stat(gate, PREPARE) is not None:
            _, plan = _read(lease, PREPARE, prepared=True)
            expected = {**value, 'entries': [{k: e[k] for k in ('path', 'stage', 'parent_device', 'parent_inode')} for e in value['entries']]}
            require(plan == expected, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
            os.unlink(PREPARE, dir_fd=gate); os.fsync(gate)
        _seal(lease, value)
        result = ExternalFence(lease, raw); result.assert_held(); return result
    except ExternalFenceError: raise
    except Exception: raise ExternalFenceError('EXTERNAL_FENCE_UNAVAILABLE') from None


def acquire(lease, *, confirmed): return _acquire(lease, confirmed=confirmed, recovery=False)
def recover(lease, *, confirmed): return _acquire(lease, confirmed=confirmed, recovery=True)


def recover_unseal(lease, *, confirmed):
    _inputs(lease, confirmed)
    try:
        fs._absent(lease._directory, PREPARE)
        raw, _ = _read(lease, RELEASE)
        if _stat(lease._directory, MARKER) is not None:
            require(_read(lease, MARKER)[0] == raw, 'EXTERNAL_FENCE_JOURNAL_CHANGED')
        _release(lease, raw)
    except ExternalFenceError: raise
    except Exception: raise ExternalFenceError('EXTERNAL_FENCE_UNAVAILABLE') from None
