"""Recoverable binary preparation under a live composed-backup fence.

Only private, non-executable copies are written. This does not switch the active
profile, release any maintenance blocker, restore SQLite, or authorize boot.
"""
from functools import wraps
import os
from pathlib import Path
import time
import zipfile

from installer import gateway_state_backup as b, gateway_state_release as r
from installer.gateway_release import sha, verify_package
from installer.gateway_transition import assess
from installer.model import canonical_bytes, strict_json_loads
from installer.transaction import _private_directory, _FILE_FLAGS, _check_file

g, fs, files, require = b.gf, b.fs, b.files, b.require
MAX_BINARY = 64 * 1024 * 1024


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try: return operation(*args, **kwargs)
        except g.GatewayStateError: raise
        except Exception: raise g.GatewayStateError('GATEWAY_TRANSITION_STAGE_UNAVAILABLE') from None
    return invoke


def _binary(package, selected):
    require(isinstance(package, Path) and package.is_absolute(), 'GATEWAY_TRANSITION_PACKAGE_REQUIRED')
    with _private_directory(package.parent, create=False) as directory:
        handle = os.open(package.name, os.O_RDONLY | _FILE_FLAGS, dir_fd=directory)
        try:
            _check_file(handle)
            with os.fdopen(handle, 'rb', closefd=False) as stream:
                verify_package(stream, selected)
                with zipfile.ZipFile(stream) as archive:
                    value = archive.read('bin/hestia-mobile-gateway')
            require(0 < len(value) <= MAX_BINARY and sha(value) == selected['binary_sha256'],
                    'GATEWAY_TRANSITION_PACKAGE_CHANGED')
            return value
        finally: os.close(handle)


def _optional(directory, name, maximum):
    try: return files._read(directory, name, maximum)
    except FileNotFoundError: return None


def _copy(directory, name, raw, fence, cancel):
    """Complete only an exact prefix of the authenticated bytes after a crash."""
    pending = name + '.part'
    existing = _optional(directory, name, MAX_BINARY)
    partial = _optional(directory, pending, MAX_BINARY)
    if existing is not None:
        require(existing == raw and partial is None, 'GATEWAY_TRANSITION_STAGE_CHANGED')
        return
    require(partial is None or raw.startswith(partial), 'GATEWAY_TRANSITION_STAGE_CHANGED')
    fence.assert_held()
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    handle = os.open(pending, flags | (os.O_CREAT | os.O_EXCL if partial is None else 0),
                     0o600, dir_fd=directory)
    try:
        files._private(handle, directory=False)
        if partial is not None:
            require(os.fstat(handle).st_size == len(partial), 'GATEWAY_TRANSITION_STAGE_CHANGED')
            os.lseek(handle, len(partial), os.SEEK_SET)
        deadline = time.monotonic() + 60
        for offset in range(len(partial or b''), len(raw), 1024 * 1024):
            b._cancel(cancel, deadline)
            files._write(handle, raw[offset:offset + 1024 * 1024])
        os.fsync(handle)
    finally: os.close(handle)
    require(files._read(directory, pending, MAX_BINARY) == raw, 'GATEWAY_TRANSITION_STAGE_CHANGED')
    fence.assert_held(); fs._absent(directory, name)
    os.rename(pending, name, src_dir_fd=directory, dst_dir_fd=directory); os.fsync(directory)


class GatewayTransitionStage:
    def __init__(self, raw): self._raw = raw
    def report(self): return strict_json_loads(self._raw)


@closed
def prepare(snapshot, *, source_package, target_package, target_commit, direction,
            confirmed, recovery=False, cancel=None):
    require(confirmed is True, 'GATEWAY_TRANSITION_CONSENT_REQUIRED')
    require(type(recovery) is bool and type(snapshot) is b.GatewayBackup
            and type(snapshot.fence) is g.GatewayStateFence, 'GATEWAY_TRANSITION_SNAPSHOT_REQUIRED')
    fence = snapshot.fence; runtime = fence.runtime
    g._inputs(runtime, fence.barrier, confirmed); fence.assert_held()
    assessment = assess(runtime.profile, target_commit=target_commit, direction=direction).report()
    require(assessment['configuration_compatible'], 'GATEWAY_TRANSITION_PROFILE_REFUSED')
    snapshot.verify(cancel=cancel)
    backup, expected = r._backup(snapshot.slot.parent, runtime, fence.barrier, fence.raw, cancel=cancel)
    r._sources(fence.opened, expected, cancel=cancel)
    binaries = {'source.bin': _binary(source_package, runtime.profile.selected_release),
                'target.bin': _binary(target_package, assessment['target_release'])}
    fence.assert_held(); b._cancel(cancel, time.monotonic() + 60)
    value = {'version': 1, 'policy': 'GATEWAY_TRANSITION_PRIVATE_BINARIES_V1',
        'assessment': assessment, 'backup': backup,
        'sqlite': snapshot.manifest['sqlite'],
        'binaries': {name: {'bytes': len(raw), 'sha256': sha(raw)} for name, raw in binaries.items()}}
    raw = canonical_bytes(value)
    require(len(raw) <= g.MAX_JOURNAL * 2, 'GATEWAY_TRANSITION_STAGE_TOO_LARGE')
    name = 'gateway-transition-' + fence.barrier._lease.lease_id
    receipt = canonical_bytes({'version': 1, 'state': 'GATEWAY_TRANSITION_BINARIES_PREPARED',
        'intent_sha256': sha(raw), 'intent': value, 'historical_only': True,
        'active_profile_changed': False, 'activity_resumed': False, 'services_started': False,
        'apply_allowed': False, 'rollback_verified': False, 'boot_requalified': False,
        'restore_to_original_allowed': False, 'phase6_complete': False})
    with fs._directory(snapshot.slot.parent) as parent:
        files._private(parent, directory=True)
        saved = _optional(parent, name + '.json', g.MAX_JOURNAL * 2)
        if recovery:
            require(saved == raw, 'GATEWAY_TRANSITION_INTENT_REQUIRED')
        else:
            require(saved is None, 'GATEWAY_TRANSITION_EXPLICIT_RECOVERY_REQUIRED')
            fs._absent(parent, name)
            disk = os.fstatvfs(parent)
            require(disk.f_bavail * disk.f_frsize >= sum(map(len, binaries.values())) + files.MIN_FREE_BYTES,
                    'GATEWAY_TRANSITION_FREE_SPACE_REQUIRED')
            files._new(parent, name + '.json', raw)
        # Intent precedes even directory creation. An empty directory after a
        # killed mkdir is therefore recoverable without adopting foreign files.
        try: directory = os.open(name, files.DIRECTORY, dir_fd=parent)
        except FileNotFoundError:
            os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)
            directory = os.open(name, files.DIRECTORY, dir_fd=parent)
        try:
            files._private(directory, directory=True)
            require(set(os.listdir(directory)) <= {'source.bin', 'target.bin', 'source.bin.part',
                    'target.bin.part', 'prepared.json'}, 'GATEWAY_TRANSITION_STAGE_CHANGED')
            completed = _optional(directory, 'prepared.json', g.MAX_JOURNAL * 3)
            require(completed is None or completed == receipt, 'GATEWAY_TRANSITION_STAGE_CHANGED')
            if completed is not None:
                require(set(os.listdir(directory)) == {'source.bin', 'target.bin', 'prepared.json'},
                        'GATEWAY_TRANSITION_STAGE_CHANGED')
                for entry, content in binaries.items():
                    require(files._read(directory, entry, MAX_BINARY) == content,
                            'GATEWAY_TRANSITION_STAGE_CHANGED')
            else:
                for entry, content in binaries.items(): _copy(directory, entry, content, fence, cancel)
                fence.assert_held(); r._sources(fence.opened, expected, cancel=cancel)
                files._new(directory, 'prepared.json', receipt)
            fence.assert_held()
            return GatewayTransitionStage(receipt)
        finally: os.close(directory)
