"""Private data snapshots under maintenance; not yet a complete Web backup.

The system adapter must register all storage roots and coordinate all writers.
No bytes are executed and no existing destination is overwritten.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from installer import database_config as fs
from installer import php_transport as p
from installer.maintenance import MaintenanceLease
from installer.model import strict_json_loads

MAX_ENTRIES = 100000
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_SECONDS = 300
MIN_FREE_BYTES = 64 * 1024 * 1024
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME
REGULAR = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOATIME
LABEL = re.compile(r'[a-z][a-z0-9_]{0,39}')
FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
          'st_size', 'st_mtime_ns', 'st_ctime_ns')


class FileSnapshotError(RuntimeError):
    """Closed diagnostics without filenames, session IDs or document contents."""


def require(value: bool, code: str) -> None:
    if not value:
        raise FileSnapshotError(code)


def _same(before, after) -> bool:
    return all(getattr(before, key) == getattr(after, key) for key in FIELDS)


def _metadata(info) -> dict:
    return {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
            'mtime_ns': info.st_mtime_ns, 'atime_ns': info.st_atime_ns}


def _name(name: str) -> None:
    require(type(name) is str and name not in ('', '.', '..') and '/' not in name
            and '\\' not in name and not any(ord(c) < 32 or ord(c) == 127 for c in name)
            and len(name.encode('utf-8')) <= 255, 'FILES_NAME_REJECTED')


def _bound(lease: MaintenanceLease, instance: str, lease_id: str, gid: int) -> None:
    require(type(lease) is MaintenanceLease, 'FILES_MAINTENANCE_REQUIRED')
    try:
        lease.assert_held()
    except Exception:
        raise FileSnapshotError('FILES_MAINTENANCE_REQUIRED') from None
    require(lease.scope.instance == instance and lease.lease_id == lease_id
            and lease.scope.web_gid == gid, 'FILES_MAINTENANCE_MISMATCH')


def _check(fd: int, uid: int, gid: int, *, directory: bool, device: int):
    info = os.fstat(fd)
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_dev == device and info.st_uid in (0, uid)
            and info.st_gid in (0, gid) and not info.st_mode & 0o7002
            and (not info.st_mode & 0o020 or info.st_gid == gid)
            and (info.st_uid == 0 or info.st_gid == gid)
            and (directory or (info.st_nlink == 1 and not info.st_mode & 0o111)),
            'FILES_METADATA_REJECTED')
    require(not os.listxattr(fd), 'FILES_XATTR_REJECTED')
    return info


def _private(fd: int, *, directory: bool) -> None:
    info = os.fstat(fd)
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (directory or info.st_nlink == 1) and not os.listxattr(fd), 'FILES_ARCHIVE_REJECTED')


def _write(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        require(count > 0, 'FILES_DISK_FAILED')
        view = view[count:]


def _new(parent: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
    try:
        os.fchmod(fd, 0o600)
        _write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent)


def _read(parent: int, name: str, maximum: int) -> bytes:
    fd = os.open(name, REGULAR, dir_fd=parent)
    try:
        _private(fd, directory=False)
        before = os.fstat(fd)
        require(before.st_size <= maximum, 'FILES_ARCHIVE_REJECTED')
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) == before.st_size and _same(before, os.fstat(fd))
                and _same(before, os.stat(name, dir_fd=parent, follow_symlinks=False)), 'FILES_ARCHIVE_CHANGED')
        return bytes(data)
    finally:
        os.close(fd)


@contextmanager
def _relative_directory(root: int, parts: list[str]):
    """At most two directory descriptors, irrespective of the tree's width."""
    fd = os.dup(root)
    try:
        for part in parts:
            _name(part)
            child = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@dataclass(frozen=True)
class DataInventory:
    """Trusted-host input from the audited system profile, never an HTTP object.

    Completeness against application configuration is a separate adapter gate.
    Roots have immutable parents; nested mounts and overlapping roots are refused.
    """
    roots: tuple[tuple[str, Path], ...] = field(repr=False)
    web_uid: int
    web_gid: int

    def validate(self, archive: Path, lease: MaintenanceLease) -> None:
        require(os.getuid() == os.geteuid() == 0, 'FILES_ROOT_REQUIRED')
        require(type(self.web_uid) is int and self.web_uid > 0 and type(self.web_gid) is int
                and self.web_gid > 0 and type(self.roots) is tuple and 0 < len(self.roots) <= 32,
                'FILES_INVENTORY_REJECTED')
        require(type(lease) is MaintenanceLease, 'FILES_MAINTENANCE_REQUIRED')
        _bound(lease, lease.scope.instance, lease.lease_id, self.web_gid)
        names, paths = set(), []
        for entry in self.roots:
            require(type(entry) is tuple and len(entry) == 2, 'FILES_INVENTORY_REJECTED')
            label, root = entry
            require(type(label) is str and LABEL.fullmatch(label) is not None and label not in names
                    and isinstance(root, Path) and root.is_absolute() and '..' not in root.parts
                    and str(root) == os.path.normpath(root) and len(os.fsencode(root)) <= 2048
                    and len(root.parts) >= 4, 'FILES_INVENTORY_REJECTED')
            for other in (*paths, archive, lease.scope.directory):
                require(root != other and root not in other.parents and other not in root.parents,
                        'FILES_ROOTS_OVERLAP')
            with fs._directory(root.parent):
                pass
            paths.append(root)
            names.add(label)


class _Scan:
    def __init__(self, inventory: DataInventory, lease: MaintenanceLease, *, destination: int | None = None, cancel=None):
        self.inventory, self.lease, self.destination, self.cancel = inventory, lease, destination, cancel
        self.deadline = time.monotonic() + MAX_SECONDS
        self.records, self.total = [], 0

    def tick(self) -> None:
        require(self.cancel is None or not self.cancel.is_set(), 'FILES_INTERRUPTED')
        require(time.monotonic() < self.deadline, 'FILES_TIMEOUT')
        _bound(self.lease, self.lease.scope.instance, self.lease.lease_id, self.inventory.web_gid)

    def append(self, record: dict) -> None:
        require(len(self.records) < MAX_ENTRIES, 'FILES_LIMIT')
        self.records.append(record)

    def tree(self, fd: int, scope: str, path: str, device: int, depth: int = 0) -> None:
        self.tick()
        require(depth <= 64 and len(path.encode('utf-8')) <= 2048, 'FILES_LIMIT')
        before = _check(fd, self.inventory.web_uid, self.inventory.web_gid, directory=True, device=device)
        names = sorted(os.listdir(fd))
        require(len(names) <= MAX_ENTRIES - len(self.records), 'FILES_LIMIT')
        self.append({'scope': scope, 'path': path, 'kind': 'directory', **_metadata(before)})
        for name in names:
            self.tick()
            _name(name)
            named = os.stat(name, dir_fd=fd, follow_symlinks=False)
            relative = name if path == '.' else path + '/' + name
            require(len(relative.encode('utf-8')) <= 2048, 'FILES_LIMIT')
            require(stat.S_ISDIR(named.st_mode) or stat.S_ISREG(named.st_mode), 'FILES_SPECIAL_ENTRY_REJECTED')
            child = os.open(name, DIRECTORY if stat.S_ISDIR(named.st_mode) else REGULAR, dir_fd=fd)
            try:
                require(_same(named, os.fstat(child)), 'FILES_SOURCE_CHANGED')
                if stat.S_ISDIR(named.st_mode):
                    self.tree(child, scope, relative, device, depth + 1)
                else:
                    self.file(child, scope, relative, device)
                require(_same(named, os.stat(name, dir_fd=fd, follow_symlinks=False)), 'FILES_SOURCE_CHANGED')
            finally:
                os.close(child)
        require(names == sorted(os.listdir(fd)) and _same(before, os.fstat(fd)), 'FILES_SOURCE_CHANGED')

    def file(self, fd: int, scope: str, path: str, device: int) -> None:
        before = _check(fd, self.inventory.web_uid, self.inventory.web_gid, directory=False, device=device)
        require(before.st_size <= MAX_FILE_BYTES and self.total + before.st_size <= MAX_TOTAL_BYTES, 'FILES_LIMIT')
        index = f'{len(self.records):06d}.bin'
        out = None
        digest, count = hashlib.sha256(), 0
        try:
            if self.destination is not None:
                disk = os.fstatvfs(self.destination)
                require(disk.f_bavail * disk.f_frsize >= before.st_size + MIN_FREE_BYTES, 'FILES_FREE_SPACE_REQUIRED')
                out = os.open(index, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                              0o600, dir_fd=self.destination)
                os.fchmod(out, 0o600)
            while True:
                self.tick()
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                count += len(chunk)
                require(count <= before.st_size and count <= MAX_FILE_BYTES, 'FILES_SOURCE_CHANGED')
                digest.update(chunk)
                if out is not None:
                    _write(out, chunk)
            require(count == before.st_size and _same(before, os.fstat(fd)), 'FILES_SOURCE_CHANGED')
            if out is not None:
                os.fsync(out)
        finally:
            if out is not None:
                os.close(out)
        self.total += count
        self.append({'scope': scope, 'path': path, 'kind': 'file', 'blob': index,
                     'bytes': count, 'sha256': digest.hexdigest(), **_metadata(before)})

    def run(self) -> list[dict]:
        for scope, root in sorted(self.inventory.roots):
            with fs._directory(root.parent) as parent:
                named = os.stat(root.name, dir_fd=parent, follow_symlinks=False)
                fd = os.open(root.name, DIRECTORY, dir_fd=parent)
                try:
                    require(_same(named, os.fstat(fd)), 'FILES_SOURCE_CHANGED')
                    self.tree(fd, scope, '.', named.st_dev)
                    require(_same(named, os.stat(root.name, dir_fd=parent, follow_symlinks=False)), 'FILES_SOURCE_CHANGED')
                finally:
                    os.close(fd)
        self.tick()
        return self.records


@dataclass(frozen=True)
class FileSnapshot:
    _slot: Path = field(repr=False)
    _manifest_sha256: str = field(repr=False)
    _instance: str = field(repr=False)
    _lease_id: str = field(repr=False)
    _gid: int = field(repr=False)

    @contextmanager
    def _open(self, lease: MaintenanceLease):
        _bound(lease, self._instance, self._lease_id, self._gid)
        try:
            with fs._directory(self._slot) as fd:
                _private(fd, directory=True)
                raw = _read(fd, 'manifest.json', MAX_MANIFEST_BYTES)
                require(hmac.compare_digest(hashlib.sha256(raw).hexdigest(), self._manifest_sha256), 'FILES_ARCHIVE_CHANGED')
                value = strict_json_loads(raw)
                require(p._json(value) == raw and value['version'] == 1
                        and value['instance'] == self._instance and value['lease_id'] == self._lease_id,
                        'FILES_ARCHIVE_REJECTED')
                yield fd, value
                _bound(lease, self._instance, self._lease_id, self._gid)
        except FileSnapshotError:
            raise
        except Exception:
            raise FileSnapshotError('FILES_ARCHIVE_REJECTED') from None

    def verify_sources(self, lease: MaintenanceLease, *, cancel=None) -> None:
        with self._open(lease) as (_, value):
            inventory = DataInventory(tuple((x['scope'], Path(x['root'])) for x in value['roots']),
                                      value['web_uid'], value['web_gid'])
            inventory.validate(self._slot.parent, lease)
            require(_Scan(inventory, lease, cancel=cancel).run() == value['records'], 'FILES_SOURCE_CHANGED')

    def restore_new(self, destination: Path, lease: MaintenanceLease, *, cancel=None) -> dict:
        """Restore to a NEW private tree; no existing/source destination accepted.

        Its parent remains root:root 0700. The service cannot access the restored
        Web-owned bytes; absolute pointers and uploaded code remain inert data.
        """
        try:
            with self._open(lease) as (slot, value):
                require(isinstance(destination, Path) and destination.is_absolute(), 'FILES_TARGET_REJECTED')
                for root in [Path(x['root']) for x in value['roots']] + [self._slot, lease.scope.directory]:
                    require(destination != root and destination not in root.parents and root not in destination.parents,
                            'FILES_TARGET_REJECTED')
                require(cancel is None or not cancel.is_set(), 'FILES_INTERRUPTED')
                with fs._directory(destination.parent) as parent:
                    _private(parent, directory=True)
                    fs._absent(parent, destination.name)
                    os.mkdir(destination.name, 0o700, dir_fd=parent)
                    os.fsync(parent)
                rootfd = os.open(destination, DIRECTORY)
                blobs, directories = None, []
                deadline = time.monotonic() + MAX_SECONDS
                try:
                    blobs = os.open('blobs', DIRECTORY, dir_fd=slot)
                    _private(rootfd, directory=True)
                    _private(blobs, directory=True)
                    for record in value['records']:
                        _bound(lease, self._instance, self._lease_id, self._gid)
                        require(cancel is None or not cancel.is_set(), 'FILES_INTERRUPTED')
                        require(time.monotonic() < deadline, 'FILES_TIMEOUT')
                        scope, path = record['scope'], record['path']
                        parts = path.split('/')
                        require(LABEL.fullmatch(scope) is not None, 'FILES_ARCHIVE_REJECTED')
                        if path != '.':
                            for name in parts:
                                _name(name)
                        parents = [] if path == '.' else [scope, *parts[:-1]]
                        name = scope if path == '.' else parts[-1]
                        if record['kind'] == 'directory':
                            with _relative_directory(rootfd, parents) as parentfd:
                                os.mkdir(name, 0o700, dir_fd=parentfd)
                                os.fsync(parentfd)
                            directories.append(([scope] if path == '.' else [scope, *parts], record))
                        else:
                            self._restore_file(blobs, rootfd, parents, name, record, deadline, cancel)
                    # Apply ownership/modes and directory dates only after creating descendants.
                    for parts, record in reversed(directories):
                        require(cancel is None or not cancel.is_set(), 'FILES_INTERRUPTED')
                        require(time.monotonic() < deadline, 'FILES_TIMEOUT')
                        with _relative_directory(rootfd, parts) as child:
                            _restore_metadata(child, record)
                    os.fsync(rootfd)
                finally:
                    if blobs is not None:
                        os.close(blobs)
                    os.close(rootfd)
                return self._summary(value, 'DATA_FILES_RESTORE_VERIFIED')
        except FileSnapshotError:
            raise
        except Exception:
            raise FileSnapshotError('FILES_RESTORE_FAILED') from None

    def _restore_file(self, blobs, rootfd, parents, name, record, deadline, cancel):
        require(record['kind'] == 'file' and re.fullmatch(r'[0-9]{6}\.bin', record['blob'])
                and re.fullmatch(r'[a-f0-9]{64}', record['sha256']), 'FILES_ARCHIVE_REJECTED')
        disk = os.fstatvfs(rootfd)
        require(disk.f_bavail * disk.f_frsize >= record['bytes'] + MIN_FREE_BYTES, 'FILES_FREE_SPACE_REQUIRED')
        source = os.open(record['blob'], REGULAR, dir_fd=blobs)
        out = None
        try:
            _private(source, directory=False)
            before = os.fstat(source)
            require(before.st_size == record['bytes'] <= MAX_FILE_BYTES, 'FILES_ARCHIVE_CHANGED')
            with _relative_directory(rootfd, parents) as parentfd:
                out = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                              0o600, dir_fd=parentfd)
                os.fsync(parentfd)
            digest, count = hashlib.sha256(), 0
            while True:
                require(cancel is None or not cancel.is_set(), 'FILES_INTERRUPTED')
                require(time.monotonic() < deadline, 'FILES_TIMEOUT')
                block = os.read(source, 65536)
                if not block:
                    break
                count += len(block)
                require(count <= record['bytes'], 'FILES_ARCHIVE_CHANGED')
                digest.update(block)
                _write(out, block)
            require(count == record['bytes'] and digest.hexdigest() == record['sha256']
                    and _same(before, os.fstat(source))
                    and _same(before, os.stat(record['blob'], dir_fd=blobs, follow_symlinks=False)),
                    'FILES_ARCHIVE_CHANGED')
            # Read the destination inode independently before accepting its bytes.
            os.lseek(out, 0, os.SEEK_SET)
            check = hashlib.sha256()
            for block in iter(lambda: os.read(out, 65536), b''):
                require(cancel is None or not cancel.is_set(), 'FILES_INTERRUPTED')
                require(time.monotonic() < deadline, 'FILES_TIMEOUT')
                check.update(block)
            require(check.hexdigest() == record['sha256'], 'FILES_RESTORE_MISMATCH')
            _restore_metadata(out, record)
        finally:
            os.close(source)
            if out is not None:
                os.close(out)

    def _summary(self, value: dict, state: str) -> dict:
        return {'state': state, 'snapshot_id': self._slot.name,
                'manifest_sha256': self._manifest_sha256, 'files': sum(r['kind'] == 'file' for r in value['records']),
                'bytes': sum(r.get('bytes', 0) for r in value['records']), 'registered_roots': len(value['roots']),
                'database_verified': False, 'storage_inventory_complete': False,
                'restore_to_original_allowed': False, 'application_installed': False}

    def report(self, lease: MaintenanceLease) -> dict:
        with self._open(lease) as (fd, value):
            result = self._summary(value, 'DATA_FILES_SNAPSHOT_VERIFIED')
            require(_read(fd, 'verified.json', 4096) == p._json(result), 'FILES_VERIFICATION_REQUIRED')
            return result


def _restore_metadata(fd: int, record: dict) -> None:
    os.fchown(fd, record['uid'], record['gid'])
    os.fchmod(fd, record['mode'])
    os.utime(fd, ns=(record['atime_ns'], record['mtime_ns']))
    os.fsync(fd)
    require(_metadata(os.fstat(fd)) == {k: record[k] for k in ('uid', 'gid', 'mode', 'mtime_ns', 'atime_ns')},
            'FILES_RESTORE_MISMATCH')


def capture_and_verify(inventory: DataInventory, archive_root: Path, lease: MaintenanceLease,
                       *, confirmed: bool, cancel=None) -> FileSnapshot:
    """Capture and actually restore once; keep interrupted slots without success.

    This operation doesn't publish a coherent SQL/files backup or grant an
    upgrade/rollback permission. Those require the later orchestration contract.
    """
    try:
        require(confirmed is True, 'FILES_CONSENT_REQUIRED')
        require(type(inventory) is DataInventory and isinstance(archive_root, Path), 'FILES_INVENTORY_REJECTED')
        inventory.validate(archive_root, lease)
        require(cancel is None or not cancel.is_set(), 'FILES_INTERRUPTED')
        with fs._directory(archive_root) as parent:
            _private(parent, directory=True)
            run_id = os.urandom(16).hex()
            os.mkdir(run_id, 0o700, dir_fd=parent)
            os.fsync(parent)
            slot = archive_root / run_id
            with fs._directory(slot) as fd:
                _new(fd, 'attempt.json', p._json({'version': 1, 'instance': lease.scope.instance,
                     'lease_id': lease.lease_id, 'state': 'FILES_STARTED'}))
                os.mkdir('blobs', 0o700, dir_fd=fd)
                blobs = os.open('blobs', DIRECTORY, dir_fd=fd)
                try:
                    records = _Scan(inventory, lease, destination=blobs, cancel=cancel).run()
                    os.fsync(blobs)
                finally:
                    os.close(blobs)
                value = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
                         'web_uid': inventory.web_uid, 'web_gid': inventory.web_gid,
                         'roots': [{'scope': k, 'root': str(v)} for k, v in sorted(inventory.roots)], 'records': records}
                raw = p._json(value)
                require(len(raw) <= MAX_MANIFEST_BYTES, 'FILES_LIMIT')
                _new(fd, 'manifest.json', raw)
                snapshot = FileSnapshot(slot, hashlib.sha256(raw).hexdigest(), lease.scope.instance, lease.lease_id, inventory.web_gid)
                snapshot.verify_sources(lease, cancel=cancel)
                isolated = archive_root / ('verify-' + run_id)
                snapshot.restore_new(isolated, lease, cancel=cancel)
                shutil.rmtree(isolated)
                snapshot.verify_sources(lease, cancel=cancel)
                _new(fd, 'verified.json', p._json(snapshot._summary(value, 'DATA_FILES_SNAPSHOT_VERIFIED')))
                return snapshot
    except FileSnapshotError:
        raise
    except Exception:
        raise FileSnapshotError('FILES_OPERATION_FAILED') from None
