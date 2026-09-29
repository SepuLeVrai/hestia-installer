"""Durable Gateway SQLite inode barrier; no restart, reset or implicit release."""
from contextlib import ExitStack
import fcntl
import os
import re
import stat
from installer import http_drain as hd, inode_fence as inf
from installer import gateway_service_drain as gd, backup_files as files
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import canonical_bytes, strict_json_loads

fs, f = hd.fs, hd.f
MARKER = 'gateway-state.attempt'
NAMES = {'gateway.db', 'gateway.lock', 'gateway.db-wal', 'gateway.db-shm'}
CACHE = 'editor-logos-v1'
CACHE_NAME = re.compile(r'[a-f0-9]{64}\.logo')
MAX_JOURNAL = 1024 * 1024
MAX_BYTES = 512 * 1024 * 1024


class GatewayStateError(RuntimeError): pass


def require(ok, code='GATEWAY_STATE_CHANGED'):
    if not ok: raise GatewayStateError(code)


def _inputs(runtime, barrier, confirmed):
    require(confirmed is True, 'GATEWAY_STATE_CONSENT_REQUIRED')
    require(type(runtime) is GatewayServiceRuntime and type(barrier) is hd.HttpDrainLease,
            'GATEWAY_STATE_BARRIER_REQUIRED')
    require(os.getuid() == os.geteuid() == 0, 'GATEWAY_STATE_ROOT_REQUIRED')
    require(runtime.web is barrier._drain.runtime, 'GATEWAY_STATE_INSTANCE_MISMATCH')
    barrier.assert_held()
    profile = strict_json_loads(barrier._profile)
    require(profile.get('gateway_service') == gd.binding(runtime), 'GATEWAY_STATE_INSTANCE_MISMATCH')


def _record(fd, name, account, mount):
    info = os.fstat(fd); directory = name in ('.', CACHE)
    fs._no_acl(fd)
    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
            (account.pw_uid, account.pw_gid, 0o700 if directory else 0o600))
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    require(inf._mount_id(fd) == mount, 'GATEWAY_STATE_NESTED_MOUNT_REJECTED')
    flags = inf._flags(fd)
    require(not flags & ~(inf.BASE_FLAGS | inf.IMMUTABLE), 'GATEWAY_STATE_FLAGS_REJECTED')
    if not directory:
        require(0 <= info.st_size <= MAX_BYTES, 'GATEWAY_STATE_SIZE_REJECTED')
        if name == 'gateway.lock': require(info.st_size == 0)
        if name.startswith(CACHE + '/'):
            require(66 <= info.st_size <= 65536 + 65, 'GATEWAY_CACHE_SIZE_REJECTED')
    return {'name': name, 'device': info.st_dev, 'inode': info.st_ino,
            'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
            'size': 0 if directory else info.st_size,
            'mtime_ns': info.st_mtime_ns, 'flags': flags & ~inf.IMMUTABLE}


def _open(runtime, stack):
    account = runtime.account(); root = runtime.state_directory(); stack.callback(os.close, root)
    mount = inf._ext4(root); names = set(os.listdir(root))
    require({'gateway.db', 'gateway.lock'} <= names <= NAMES | {CACHE}, 'GATEWAY_STATE_FILES_REJECTED')
    opened = {'.': root}
    for name in sorted(names):
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(name, flags | (os.O_DIRECTORY if name == CACHE else 0), dir_fd=root)
        stack.callback(os.close, fd); opened[name] = fd
        if name == CACHE:
            children = sorted(os.listdir(fd))
            require(len(children) <= 1024 and all(CACHE_NAME.fullmatch(n) for n in children),
                    'GATEWAY_CACHE_FILES_REJECTED')
            for child in children:
                item = os.open(child, flags, dir_fd=fd); stack.callback(os.close, item)
                opened[CACHE + '/' + child] = item
    records = [_record(fd, name, account, mount) for name, fd in opened.items()]
    require(sum(r['size'] for r in records if r['name'].startswith(CACHE + '/')) <= 16 * 1024 * 1024,
            'GATEWAY_CACHE_SIZE_REJECTED')
    require(sum(row['size'] for row in records) <= MAX_BYTES, 'GATEWAY_STATE_SIZE_REJECTED')
    require(len({(r['device'], r['inode']) for r in records}) == len(records), 'GATEWAY_STATE_ALIAS_REJECTED')
    try: fcntl.flock(opened['gateway.lock'], fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise GatewayStateError('GATEWAY_STATE_BUSY') from None
    return opened, records, mount


class GatewayStateFence:
    def __init__(self, runtime, barrier, stack, opened, raw, mount):
        self.runtime, self.barrier, self._stack = runtime, barrier, stack
        self.opened, self.raw, self.mount = opened, raw, mount
        self.value = strict_json_loads(raw)
        self._pid, self._closed = os.getpid(), False

    def __repr__(self): return '<GatewayStateFence private durable SQLite barrier>'
    def __reduce__(self): raise TypeError('Gateway state fences cannot be serialized')

    def assert_held(self):
        require(not self._closed and self._pid == os.getpid(), 'GATEWAY_STATE_BARRIER_REQUIRED')
        self.barrier.assert_held()
        fs._absent(self.barrier._lease._directory, 'gateway-state.release')
        fs._absent(self.barrier._lease._directory, 'gateway-state.released')
        require(f._read(self.barrier._lease._directory, MARKER, 0, mode=0o600, limit=MAX_JOURNAL) == self.raw)
        account = self.runtime.account(); root = self.opened['.']
        require(set(os.listdir(root)) == {n for n in self.opened if n != '.' and '/' not in n})
        if CACHE in self.opened:
            require(set(os.listdir(self.opened[CACHE])) ==
                    {n.split('/', 1)[1] for n in self.opened if n.startswith(CACHE + '/')})
        current = self.runtime.state_directory()
        try: require(os.path.samestat(os.fstat(root), os.fstat(current)))
        finally: os.close(current)
        for row in self.value['entries']:
            fd = self.opened[row['name']]
            require(_record(fd, row['name'], account, self.mount) == row)
            require(inf._flags(fd) == row['flags'] | inf.IMMUTABLE)
            if row['name'] != '.':
                parent = self.opened[CACHE] if row['name'].startswith(CACHE + '/') else root
                named = os.stat(row['name'].rsplit('/', 1)[-1], dir_fd=parent, follow_symlinks=False)
                require((named.st_dev, named.st_ino) == (row['device'], row['inode']))
        fcntl.flock(self.opened['gateway.lock'], fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.barrier._lease.assert_held()

    def close(self):
        require(self._pid == os.getpid(), 'GATEWAY_STATE_BARRIER_REQUIRED')
        if not self._closed:
            self._closed = True; self._stack.close()
        # Immutable flags and the durable marker deliberately survive close/death.

    def __enter__(self): self.assert_held(); return self
    def __exit__(self, *args): self.close()


def _acquire(runtime, barrier, confirmed, recovery):
    stack = ExitStack()
    try:
        _inputs(runtime, barrier, confirmed)
        gate = barrier._lease._directory
        fs._absent(gate, 'gateway-state.release')
        fs._absent(gate, 'gateway-state.released')
        if not recovery: fs._absent(gate, MARKER)
        opened, records, mount = _open(runtime, stack)
        value = {'version': 1, 'lease_id': barrier._lease.lease_id,
                 'instance': runtime.web.spec.instance, 'binding': gd.binding(runtime),
                 'barrier_sha256': f._sha(barrier._profile), 'entries': records}
        raw = canonical_bytes(value)
        if recovery:
            require(f._read(gate, MARKER, 0, mode=0o600, limit=MAX_JOURNAL) == raw, 'GATEWAY_STATE_RECOVERY_MISMATCH')
        else:
            require(all(not inf._flags(fd) & inf.IMMUTABLE for fd in opened.values()),
                    'GATEWAY_STATE_FOREIGN_FENCE')
            require(len(raw) <= MAX_JOURNAL, 'GATEWAY_STATE_SIZE_REJECTED')
            files._new(gate, MARKER, raw)
        # Freeze the directory first, then existing inodes (including WAL/SHM).
        # Intent precedes every flag change; no SQL library ever opens live state.
        for row in records:
            fd = opened[row['name']]
            require(_record(fd, row['name'], runtime.account(), mount) == row)
            inf._flags(fd, row['flags'] | inf.IMMUTABLE); os.fsync(fd)
        result = GatewayStateFence(runtime, barrier, stack, opened, raw, mount)
        result.assert_held(); return result
    except BaseException as error:
        stack.close()
        if isinstance(error, (GatewayStateError, KeyboardInterrupt, SystemExit)): raise
        raise GatewayStateError('GATEWAY_STATE_UNAVAILABLE') from None


def acquire(runtime, barrier, *, confirmed): return _acquire(runtime, barrier, confirmed, False)
def recover(runtime, barrier, *, confirmed): return _acquire(runtime, barrier, confirmed, True)
