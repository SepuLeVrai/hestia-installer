"""Private SQLite backup/isolated restore, bound to a durable Gateway fence.

Neither the live SQLite database nor its migrations are opened by this module.
The existing database worker verifies copies without root or network access.
"""
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
from installer import gateway_state_fence as gf, backup_files as files
from installer import foundation_drain, gateway_service_drain, php_transport as p
from installer import coordinated_backup as cb
from installer.model import canonical_bytes, strict_json_loads

fs, f = gf.fs, gf.f
require, GatewayStateError = gf.require, gf.GatewayStateError


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try: return operation(*args, **kwargs)
        except GatewayStateError: raise
        except Exception: raise GatewayStateError('GATEWAY_BACKUP_UNAVAILABLE') from None
    return invoke


def _cancel(cancel, deadline):
    require(cancel is None or not cancel.is_set(), 'GATEWAY_BACKUP_INTERRUPTED')
    require(time.monotonic() < deadline, 'GATEWAY_BACKUP_TIMEOUT')


def _copy(source, parent, name, *, uid=0, gid=0, cancel=None):
    require(name in (*gf.NAMES, 'database.sqlite') or gf.CACHE_NAME.fullmatch(name), 'GATEWAY_BACKUP_NAME_REJECTED')
    info = os.fstat(source); require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        and 0 <= info.st_size <= gf.MAX_BYTES, 'GATEWAY_BACKUP_FILE_REJECTED')
    disk = os.fstatvfs(parent)
    require(disk.f_bavail * disk.f_frsize >= info.st_size + files.MIN_FREE_BYTES,
            'GATEWAY_BACKUP_FREE_SPACE_REQUIRED')
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
    digest = hashlib.sha256(); total = 0; deadline = time.monotonic() + 60
    try:
        os.lseek(source, 0, os.SEEK_SET)
        while True:
            _cancel(cancel, deadline); data = os.read(source, 1024 * 1024)
            if not data: break
            total += len(data); require(total <= gf.MAX_BYTES, 'GATEWAY_BACKUP_SIZE_REJECTED')
            digest.update(data); files._write(target, data)
        require(total == info.st_size and files._same(info, os.fstat(source)), 'GATEWAY_BACKUP_SOURCE_CHANGED')
        os.fchown(target, uid, gid); os.fchmod(target, 0o600); os.fsync(target)
    finally: os.close(target)
    os.fsync(parent)
    return {'bytes': total, 'sha256': digest.hexdigest()}


def _opened(directory, name, uid=0, gid=0):
    fd = os.open(name, files.REGULAR, dir_fd=directory)
    try:
        info = os.fstat(fd); fs._no_acl(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, 0o600),
                'GATEWAY_BACKUP_FILE_REJECTED')
        return fd
    except BaseException: os.close(fd); raise


def _worker(worker, runtime, input_fd, names, output_fd, cancel):
    p._runtime(worker, runtime.web.spec.service_user)
    account = runtime.account()
    require(worker.worker_uid != account.pw_uid and worker.worker_gid != account.pw_gid,
            'GATEWAY_BACKUP_WORKER_SEPARATION_REQUIRED')
    for path in ('/usr/bin/python3', '/usr/bin/unshare'):
        p._safe_path(Path(path).resolve(), directory=False, system=True)
    require(shutil.disk_usage(worker.run_root).free >= 2 * 1024 * 1024 * 1024,
            'GATEWAY_BACKUP_FREE_SPACE_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='gateway-verify-', dir=worker.run_root) as temporary:
        stage = Path(temporary)
        with fs._directory(stage) as fd:
            f._write(fd, 'verify.py', p._read_file(Path(__file__).parent / 'private/gateway_sqlite_verify.py'),
                     worker.worker_gid, mode=0o640)
            os.mkdir('data', 0o700, dir_fd=fd)
            with fs._directory(stage / 'data') as data:
                for name in ('gateway.db', 'gateway.db-wal'):
                    if name not in names: continue
                    handle = _opened(input_fd, name)
                    try: _copy(handle, data, name, uid=worker.worker_uid, gid=worker.worker_gid, cancel=cancel)
                    finally: os.close(handle)
                os.fchown(data, worker.worker_uid, worker.worker_gid)
            os.fchown(fd, 0, worker.worker_gid); os.fchmod(fd, 0o750)
        command = ['/usr/bin/unshare', '--net', '--', '/usr/bin/setpriv',
            '--reuid=' + str(worker.worker_uid), '--regid=' + str(worker.worker_gid),
            '--clear-groups', '--no-new-privs', '--inh-caps=-all', '--ambient-caps=-all',
            '--bounding-set=-all', '--pdeathsig=KILL', '/usr/bin/prlimit', '--core=0', '--cpu=50',
            '--as=805306368', '--fsize=1073741824', '--nofile=64', '--nproc=16', '--',
            '/usr/bin/python3', '-I', '-S', str(stage / 'verify.py')]
        code, raw = p._exchange(command, b'{}', stage / 'data', 60, cancel=cancel)
        require(code == 0, 'GATEWAY_BACKUP_SQLITE_REJECTED')
        value = strict_json_loads(raw)
        require(type(value) is dict and set(value) == {'status', 'sqlite_schema', 'installation_uuid_sha256',
            'logical_sha256', 'rows', 'bytes', 'sha256', 'worker_uid', 'worker_gid'}
            and value['status'] == 'PASS' and type(value['sqlite_schema']) is int and value['sqlite_schema'] == 6
            and value['worker_uid'] == worker.worker_uid and value['worker_gid'] == worker.worker_gid,
            'GATEWAY_BACKUP_WORKER_REJECTED')
        for key in ('installation_uuid_sha256', 'logical_sha256', 'sha256'):
            require(type(value[key]) is str and re.fullmatch(r'[a-f0-9]{64}', value[key]), 'GATEWAY_BACKUP_WORKER_REJECTED')
        require(type(value['rows']) is int and 1 <= value['rows'] <= 2000000
                and type(value['bytes']) is int and 0 < value['bytes'] <= gf.MAX_BYTES, 'GATEWAY_BACKUP_WORKER_REJECTED')
        # The worker cannot traverse the backup tree. Reopen its fixed output via
        # root's stage descriptor; never follow a worker-controlled symlink.
        parent = os.open(stage / 'data', files.DIRECTORY)
        try:
            handle = _opened(parent, 'restored.sqlite', worker.worker_uid, worker.worker_gid)
            try: result = _copy(handle, output_fd, 'database.sqlite', cancel=cancel)
            finally: os.close(handle)
        finally: os.close(parent)
        require(result == {k: value[k] for k in ('bytes', 'sha256')}, 'GATEWAY_BACKUP_WORKER_REJECTED')
        return {k: value[k] for k in ('sqlite_schema', 'installation_uuid_sha256', 'logical_sha256', 'rows', 'bytes', 'sha256')}


class GatewayBackup:
    def __init__(self, fence, worker, slot, raw):
        self.fence, self.worker, self.slot, self.raw = fence, worker, slot, raw
        self.manifest = strict_json_loads(raw)

    @closed
    def verify(self, *, cancel=None):
        self.fence.assert_held()
        require(set(self.manifest) == {'version', 'lease_id', 'fence_sha256', 'binding', 'files',
            'sqlite', 'isolated_restore_verified', 'activity_resumed', 'restore_to_original_allowed', 'editor_cache'}
            and self.manifest['version'] == 1 and type(self.manifest['version']) is int
            and self.manifest['lease_id'] == self.fence.barrier._lease.lease_id
            and self.manifest['fence_sha256'] == f._sha(self.fence.raw)
            and self.manifest['binding'] == self.fence.value['binding']
            and self.manifest['isolated_restore_verified'] is True
            and self.manifest['activity_resumed'] is False and self.manifest['restore_to_original_allowed'] is False
            and type(self.manifest['files']) is dict
            and type(self.manifest['editor_cache']) is bool
            and self.manifest['editor_cache'] == (gf.CACHE in self.fence.opened)
            and set(self.manifest['files']) == set(self.fence.opened) - {'.', gf.CACHE}
            and {'gateway.db', 'gateway.lock'} <= set(self.manifest['files'])
            and all(name in gf.NAMES or name.startswith(gf.CACHE + '/')
                and gf.CACHE_NAME.fullmatch(name.split('/', 1)[1]) for name in self.manifest['files']),
            'GATEWAY_BACKUP_CHANGED')
        with fs._directory(self.slot) as fd:
            require(f._read(fd, 'snapshot.json', 0, mode=0o600, limit=gf.MAX_JOURNAL) == self.raw, 'GATEWAY_BACKUP_CHANGED')
            with fs._directory(self.slot / 'source') as source:
                require(set(os.listdir(source)) == {n for n in self.manifest['files'] if '/' not in n}
                    | ({gf.CACHE} if self.manifest['editor_cache'] else set()), 'GATEWAY_BACKUP_CHANGED')
                if self.manifest['editor_cache']:
                    with fs._directory(self.slot / 'source' / gf.CACHE) as cache:
                        require(set(os.listdir(cache)) == {n.split('/', 1)[1] for n in self.manifest['files']
                            if n.startswith(gf.CACHE + '/')}, 'GATEWAY_BACKUP_CHANGED')
            for name, expected in self.manifest['files'].items():
                with fs._directory(self.slot / 'source' / (gf.CACHE if '/' in name else '')) as source:
                    handle = _opened(source, name.rsplit('/', 1)[-1])
                    try:
                        digest = hashlib.sha256(); total = 0; deadline = time.monotonic() + 60
                        while True:
                            _cancel(cancel, deadline)
                            data = os.read(handle, 1024 * 1024)
                            if not data: break
                            digest.update(data); total += len(data)
                            require(total <= gf.MAX_BYTES, 'GATEWAY_BACKUP_SIZE_REJECTED')
                        require({'bytes': total, 'sha256': digest.hexdigest()} == expected, 'GATEWAY_BACKUP_CHANGED')
                    finally: os.close(handle)
            handle = _opened(fd, 'database.sqlite')
            try:
                with os.fdopen(handle, 'rb', closefd=False) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                require(digest == self.manifest['sqlite']['sha256'] and
                        os.fstat(handle).st_size == self.manifest['sqlite']['bytes'], 'GATEWAY_BACKUP_CHANGED')
            finally: os.close(handle)
        self.fence.assert_held()

    @closed
    def compose(self, web):
        require(type(web) is cb.CoordinatedVerification, 'GATEWAY_BACKUP_WEB_INCOMPLETE')
        self.verify(); value = web.report()
        require(value.get('state') == 'PROVISIONED_BACKUP_RESTORE_VERIFIED'
                and value.get('activity_resumed') is False
                and value.get('database_restoration_verified') is True
                and value.get('registered_data_restoration_verified') is True
                and type(value.get('backup_id')) is str and re.fullmatch(r'[a-f0-9]{32}', value['backup_id']),
                'GATEWAY_BACKUP_WEB_INCOMPLETE')
        with fs._directory(self.slot.parent / value['backup_id']) as webfd:
            require(f._read(webfd, 'verified.json', 0, mode=0o600) == canonical_bytes(value)
                    and f._sha(f._read(webfd, 'coordinated.json', 0, mode=0o600)) == value['manifest_sha256'],
                    'GATEWAY_BACKUP_WEB_CHANGED')
        receipt = {**value, 'state': 'MOBILE_BACKUP_RESTORE_VERIFIED',
            'gateway_sqlite_restoration_verified': True, 'gateway_installation_uuid_preserved': True,
            'gateway_source_inode_writes_fenced': True, 'gateway_fence_sha256': f._sha(self.fence.raw),
            'gateway_snapshot_sha256': f._sha(self.raw), 'gateway_snapshot_id': self.slot.name,
            'gateway_sqlite_schema': 6, 'gateway_editor_cache_restoration_verified': True, 'public_mobile_delivered': False, 'boot_delivered': False,
            'restore_to_original_allowed': False, 'activity_resumed': False}
        with fs._directory(self.slot) as fd:
            _receipt(fd, 'composed.json', canonical_bytes({'gateway_snapshot': self.manifest, 'web': value,
                'receipt': receipt}))
            self.fence.assert_held()
            _receipt(fd, 'verified.json', canonical_bytes(receipt))
        return cb.CoordinatedVerification(canonical_bytes(receipt))


@closed
def capture(fence, worker, backup_root, *, confirmed, cancel=None):
    require(confirmed is True, 'GATEWAY_BACKUP_CONSENT_REQUIRED')
    require(type(fence) is gf.GatewayStateFence and type(worker) is p.PhpRuntime,
            'GATEWAY_BACKUP_INPUT_REJECTED')
    fence.assert_held(); _cancel(cancel, time.monotonic() + 60)
    require(isinstance(backup_root, Path) and backup_root.is_absolute(), 'GATEWAY_BACKUP_PATH_REJECTED')
    runtime = fence.runtime
    for protected in (runtime.root, runtime.profile.key_directory, runtime.web.spec.root,
                      runtime.web.spec.webroot, runtime.web.spec.maintenance_directory):
        require(backup_root != protected and protected not in backup_root.parents and backup_root not in protected.parents,
                'GATEWAY_BACKUP_PATH_REJECTED')
    slot = backup_root / ('gateway-' + fence.barrier._lease.lease_id)
    with fs._directory(backup_root) as root:
        files._private(root, directory=True); fs._absent(root, slot.name)
        os.mkdir(slot.name, 0o700, dir_fd=root); os.fsync(root)
    with fs._directory(slot) as fd:
        f._write(fd, 'attempt.json', canonical_bytes({'state': 'GATEWAY_BACKUP_STARTED',
            'fence_sha256': f._sha(fence.raw), 'lease_id': fence.barrier._lease.lease_id}), 0, mode=0o600)
        os.mkdir('source', 0o700, dir_fd=fd); os.fsync(fd)
        with fs._directory(slot / 'source') as source:
            saved = {}
            if gf.CACHE in fence.opened: os.mkdir(gf.CACHE, 0o700, dir_fd=source)
            for name, handle in fence.opened.items():
                if name in ('.', gf.CACHE): continue
                with fs._directory(slot / 'source' / (gf.CACHE if '/' in name else '')) as parent:
                    saved[name] = _copy(handle, parent, name.rsplit('/', 1)[-1], cancel=cancel)
            fence.assert_held()
            normalized = _worker(worker, runtime, source, set(saved), fd, cancel)
        # Restore the stored normalized image into another exclusive private
        # target and compare every table, schema, UUID and migration checksum.
        os.mkdir('restore-proof', 0o700, dir_fd=fd)
        with fs._directory(slot / 'restore-proof') as proof:
            handle = _opened(fd, 'database.sqlite')
            try: _copy(handle, proof, 'gateway.db', cancel=cancel)
            finally: os.close(handle)
            if gf.CACHE in fence.opened:
                os.mkdir(gf.CACHE, 0o700, dir_fd=proof)
                with fs._directory(slot / 'source' / gf.CACHE) as source_cache, \
                     fs._directory(slot / 'restore-proof' / gf.CACHE) as restored_cache:
                    for name, expected in saved.items():
                        if not name.startswith(gf.CACHE + '/'): continue
                        handle = _opened(source_cache, name.split('/', 1)[1])
                        try: restored_file = _copy(handle, restored_cache, name.split('/', 1)[1], cancel=cancel)
                        finally: os.close(handle)
                        require(restored_file == expected, 'GATEWAY_BACKUP_RESTORE_MISMATCH')
            os.mkdir('result', 0o700, dir_fd=proof)
            with fs._directory(slot / 'restore-proof/result') as result:
                restored = _worker(worker, runtime, proof, {'gateway.db'}, result, cancel)
            require(all(restored[k] == normalized[k] for k in
                ('sqlite_schema', 'installation_uuid_sha256', 'logical_sha256', 'rows')),
                'GATEWAY_BACKUP_RESTORE_MISMATCH')
        shutil.rmtree(slot / 'restore-proof'); fence.assert_held()
        raw = canonical_bytes({'version': 1, 'lease_id': fence.barrier._lease.lease_id,
            'fence_sha256': f._sha(fence.raw), 'binding': fence.value['binding'], 'files': saved,
            'sqlite': normalized, 'editor_cache': gf.CACHE in fence.opened, 'isolated_restore_verified': True, 'activity_resumed': False,
            'restore_to_original_allowed': False})
        require(len(raw) <= gf.MAX_JOURNAL, 'GATEWAY_BACKUP_SIZE_REJECTED')
        files._new(fd, 'snapshot.json', raw)
    result = GatewayBackup(fence, worker, slot, raw); result.verify(cancel=cancel); return result


@closed
def prepare(http, barrier, worker, backup_root, stack, *, confirmed, cancel=None, recovering=False):
    root = http.spec.root.parent / 'gateway-service'
    try: root.lstat()
    except FileNotFoundError: return None
    foundation = foundation_drain.attached(http)
    runtime = gateway_service_drain.attached(http, foundation)
    require(runtime is not None, 'GATEWAY_BACKUP_INPUT_REJECTED')
    require(type(recovering) is bool, 'GATEWAY_BACKUP_INPUT_REJECTED')
    gate = barrier._lease._directory
    try: os.stat(gf.MARKER, dir_fd=gate, follow_symlinks=False)
    except FileNotFoundError: method = gf.acquire
    else:
        require(recovering, 'GATEWAY_BACKUP_RECOVERY_REQUIRED'); method = gf.recover
    fence = stack.enter_context(method(runtime, barrier, confirmed=confirmed))
    slot = backup_root / ('gateway-' + barrier._lease.lease_id)
    if recovering and slot.exists():
        return recover_snapshot(fence, worker, backup_root, confirmed=confirmed, cancel=cancel)
    return capture(fence, worker, backup_root, confirmed=confirmed, cancel=cancel)


def _receipt(fd, name, raw):
    require(len(raw) <= gf.MAX_JOURNAL, 'GATEWAY_BACKUP_SIZE_REJECTED')
    try: files._new(fd, name, raw)
    except FileExistsError:
        require(f._read(fd, name, 0, mode=0o600, limit=gf.MAX_JOURNAL) == raw, 'GATEWAY_BACKUP_RECOVERY_MISMATCH')


@closed
def recover_snapshot(fence, worker, backup_root, *, confirmed, cancel=None):
    """Reconcile only an already complete snapshot; never recopy partial state."""
    require(confirmed is True and type(fence) is gf.GatewayStateFence and type(worker) is p.PhpRuntime,
            'GATEWAY_BACKUP_INPUT_REJECTED')
    fence.assert_held()
    slot = backup_root / ('gateway-' + fence.barrier._lease.lease_id)
    with fs._directory(slot) as fd:
        expected = canonical_bytes({'state': 'GATEWAY_BACKUP_STARTED', 'fence_sha256': f._sha(fence.raw),
                                    'lease_id': fence.barrier._lease.lease_id})
        require(f._read(fd, 'attempt.json', 0, mode=0o600) == expected, 'GATEWAY_BACKUP_RECOVERY_MISMATCH')
        raw = f._read(fd, 'snapshot.json', 0, mode=0o600, limit=gf.MAX_JOURNAL)
    result = GatewayBackup(fence, worker, slot, raw); result.verify(cancel=cancel); return result
