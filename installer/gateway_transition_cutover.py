"""Exact binary replacement and file rollback while all activity stays closed.

The original service profile, journals and boot bundles remain immutable. Only
this coordinator can observe the intermediate binary under the original live
maintenance/SQLite fence. Ordinary service readers still reject the new binary;
no active-profile, admission or boot handoff is implied by these receipts.
"""
from contextlib import ExitStack, contextmanager
from functools import wraps
import os
from pathlib import Path
import re
import stat
import time

from installer import gateway_transition_stage as stage
from installer import http_drain as hd, session_cleaner
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import canonical_bytes, strict_json_loads

g, r, b, fs, files = stage.g, stage.r, stage.b, stage.fs, stage.files
require, sha = g.require, stage.sha
MARKER = 'gateway-cutover.attempt'
POLICY = 'GATEWAY_BINARY_CUTOVER_ACTIVITY_CLOSED_V1'
MAX_RECORD = 128 * 1024


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try: return operation(*args, **kwargs)
        except g.GatewayStateError: raise
        except Exception: raise g.GatewayStateError('GATEWAY_CUTOVER_UNAVAILABLE') from None
    return invoke


def _optional(fd, name): return stage._optional(fd, name, MAX_RECORD)


def _json(raw):
    value = strict_json_loads(raw)
    require(type(value) is dict and canonical_bytes(value) == raw, 'GATEWAY_CUTOVER_JOURNAL_CHANGED')
    return value


def _identity(info, raw):
    return {'device': info.st_dev, 'inode': info.st_ino, 'bytes': len(raw), 'sha256': sha(raw)}


def _preparation(runtime, backups, lease_id, source_package, target_package, target_commit, direction):
    """Read a completed 3B1 preparation; never create or repair its evidence."""
    r._path(backups, runtime)
    assessment = stage.assess(runtime.profile, target_commit=target_commit, direction=direction).report()
    require(assessment['configuration_compatible'], 'GATEWAY_CUTOVER_PROFILE_REFUSED')
    binaries = {'source': stage._binary(source_package, runtime.profile.selected_release),
                'target': stage._binary(target_package, assessment['target_release'])}
    name = 'gateway-transition-' + lease_id
    with fs._directory(backups) as parent:
        intent_raw = files._read(parent, name + '.json', MAX_RECORD)
    intent = _json(intent_raw)
    require(set(intent) == {'version', 'policy', 'assessment', 'backup', 'sqlite', 'binaries'}
        and type(intent['version']) is int and intent['version'] == 1
        and intent['policy'] == 'GATEWAY_TRANSITION_PRIVATE_BINARIES_V1'
        and intent['assessment'] == assessment
        and intent['binaries'] == {k + '.bin': {'bytes': len(v), 'sha256': sha(v)} for k, v in binaries.items()},
        'GATEWAY_CUTOVER_PREPARATION_CHANGED')
    expected = canonical_bytes({'version': 1, 'state': 'GATEWAY_TRANSITION_BINARIES_PREPARED',
        'intent_sha256': sha(intent_raw), 'intent': intent, 'historical_only': True,
        'active_profile_changed': False, 'activity_resumed': False, 'services_started': False,
        'apply_allowed': False, 'rollback_verified': False, 'boot_requalified': False,
        'restore_to_original_allowed': False, 'phase6_complete': False})
    with fs._directory(backups / name) as fd:
        files._private(fd, directory=True)
        require(set(os.listdir(fd)) == {'source.bin', 'target.bin', 'prepared.json'}, 'GATEWAY_CUTOVER_PREPARATION_CHANGED')
        require(files._read(fd, 'prepared.json', MAX_RECORD) == expected, 'GATEWAY_CUTOVER_PREPARATION_CHANGED')
        for role, raw in binaries.items():
            require(files._read(fd, role + '.bin', stage.MAX_BINARY) == raw, 'GATEWAY_CUTOVER_PREPARATION_CHANGED')
    return _json(expected), binaries


class _Cutover:
    def __init__(self, runtime, lease, backups, prepared, binaries, raw):
        self.runtime, self.lease, self.backups = runtime, lease, backups
        self.prepared, self.binaries, self.raw = prepared, binaries, raw
        self.value = _json(raw); self.owner = {'intent_sha256': sha(raw)}
        self.slot = runtime.root / 'control' / ('cutover-' + lease.lease_id)
        expected = {'version': 1, 'policy': POLICY, 'instance': runtime.web.spec.instance,
            'lease_id': lease.lease_id, 'source_manifest': runtime.manifest(runtime.account()),
            'preparation_sha256': sha(canonical_bytes(prepared)),
            'fence_sha256': sha(canonical_bytes(prepared['intent']['backup']['fence'])),
            'original_binary': self.value.get('original_binary')}
        require(self.value == expected, 'GATEWAY_CUTOVER_JOURNAL_CHANGED')
        self._validate_identity(expected['original_binary'], 'source')

    def _validate_identity(self, value, role):
        require(type(value) is dict and set(value) == {'device', 'inode', 'bytes', 'sha256'}
            and all(type(value[k]) is int and value[k] > 0 for k in ('device', 'inode', 'bytes'))
            and value['bytes'] == len(self.binaries[role]) and value['sha256'] == sha(self.binaries[role]),
            'GATEWAY_CUTOVER_BINARY_CHANGED')

    @contextmanager
    def directory(self, *, create=False):
        with fs._directory(self.slot.parent) as parent:
            files._private(parent, directory=True)
            try: fd = os.open(self.slot.name, files.DIRECTORY, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    yield None; return
                os.mkdir(self.slot.name, 0o700, dir_fd=parent); os.fsync(parent)
                fd = os.open(self.slot.name, files.DIRECTORY, dir_fd=parent)
            try:
                files._private(fd, directory=True)
                require(set(os.listdir(fd)) <= {'rollback.json', *(
                    role + suffix for role in ('source', 'target') for suffix in ('.pending', '.armed.json', '.done.json'))},
                    'GATEWAY_CUTOVER_FOREIGN_FILE')
                yield fd
            finally: os.close(fd)

    def pending(self, fd, role):
        if fd is None: return None
        try: handle = os.open(role + '.pending', os.O_RDONLY | stage._FILE_FLAGS, dir_fd=fd)
        except FileNotFoundError: return None
        try:
            info = os.fstat(handle); fs._no_acl(handle)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
                and (info.st_gid, stat.S_IMODE(info.st_mode)) in ((0, 0o600),
                    (self.runtime.account().pw_gid, 0o600), (self.runtime.account().pw_gid, 0o750))
                and 0 <= info.st_size <= len(self.binaries[role]), 'GATEWAY_CUTOVER_PENDING_CHANGED')
            with os.fdopen(handle, 'rb', closefd=False) as stream: raw = stream.read(stage.MAX_BINARY + 1)
            require(files._same(info, os.fstat(handle)) and len(raw) == info.st_size
                and self.binaries[role].startswith(raw)
                and (stat.S_IMODE(info.st_mode) == 0o600 or raw == self.binaries[role]),
                'GATEWAY_CUTOVER_PENDING_CHANGED')
            return info, raw
        finally: os.close(handle)

    def active(self):
        account = self.runtime.account()
        with fs._directory(self.runtime.root) as fd:
            name = self.runtime.profile.binary.name
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            raw = g.f._read(fd, name, account.pw_gid, mode=0o750, limit=stage.MAX_BINARY)
            require(files._same(before, os.stat(name, dir_fd=fd, follow_symlinks=False)), 'GATEWAY_CUTOVER_BINARY_CHANGED')
            return _identity(before, raw)

    def observe(self):
        self.lease.assert_held()
        require(files._read(self.lease._directory, MARKER, MAX_RECORD) == self.raw, 'GATEWAY_CUTOVER_JOURNAL_CHANGED')
        active = self.active()
        with self.directory() as fd:
            read = lambda name: None if fd is None else _optional(fd, name)
            armed, done = {}, {}
            predecessor = self.value['original_binary']
            rollback = read('rollback.json')
            require(rollback is None or rollback == canonical_bytes(self.owner), 'GATEWAY_CUTOVER_JOURNAL_CHANGED')
            for role in ('target', 'source'):
                raw = read(role + '.armed.json'); armed[role] = None if raw is None else _json(raw)
                done[role] = read(role + '.done.json')
                if raw is not None:
                    record = armed[role]
                    require(set(record) == {'owner', 'role', 'previous', 'replacement'}
                        and record['owner'] == self.owner and record['role'] == role
                        and record['previous'] == predecessor, 'GATEWAY_CUTOVER_JOURNAL_CHANGED')
                    self._validate_identity(record['replacement'], role)
                    require(record['replacement']['inode'] != predecessor['inode']
                        and record['replacement']['device'] == predecessor['device'], 'GATEWAY_CUTOVER_BINARY_CHANGED')
                    require(done[role] is None or done[role] == canonical_bytes({
                        'owner': self.owner, 'armed_sha256': sha(raw), 'role': role}), 'GATEWAY_CUTOVER_JOURNAL_CHANGED')
                    predecessor = record['replacement']
                else: require(done[role] is None, 'GATEWAY_CUTOVER_JOURNAL_CHANGED')
            require(rollback is None or done['target'] is not None, 'GATEWAY_CUTOVER_ORDER_CHANGED')
            require(rollback is not None or armed['source'] is None and done['source'] is None
                and self.pending(fd, 'source') is None, 'GATEWAY_CUTOVER_ORDER_CHANGED')
            role = 'source' if rollback is not None else 'target'
            if role == 'source': require(self.pending(fd, 'target') is None, 'GATEWAY_CUTOVER_ORDER_CHANGED')
            previous = self.value['original_binary'] if role == 'target' else armed['target']['replacement']
            pending = self.pending(fd, role); arm = armed[role]
            if arm is None: require(active == previous, 'GATEWAY_CUTOVER_BINARY_CHANGED')
            elif active == arm['replacement']:
                require(pending is None, 'GATEWAY_CUTOVER_PENDING_CHANGED')
            else:
                require(done[role] is None and active == previous and pending is not None
                    and _identity(*pending) == arm['replacement']
                    and (pending[0].st_gid, stat.S_IMODE(pending[0].st_mode)) ==
                        (self.runtime.account().pw_gid, 0o750), 'GATEWAY_CUTOVER_BINARY_CHANGED')
            if done[role] is not None: require(active == arm['replacement'], 'GATEWAY_CUTOVER_BINARY_CHANGED')
        self.lease.assert_held()
        return role, armed[role], done[role], active

    def native_stopped(self, foundation):
        require(foundation is not None and b.foundation_drain.binding(foundation) ==
            b.foundation_drain.binding(self.runtime.foundation), 'GATEWAY_CUTOVER_PROFILE_REFUSED')
        _, _, _, active = self.observe()
        self.runtime._stopped_state(self.runtime._inspect_binary(active['sha256']))
        return g.gd.binding(self.runtime)

    def verify_backup(self, fence, cancel):
        require(sha(fence.raw) == self.value['fence_sha256'], 'GATEWAY_CUTOVER_FENCE_CHANGED')
        binding, expected = r._backup(self.backups, self.runtime, fence.barrier, fence.raw, cancel=cancel)
        require(binding == self.prepared['intent']['backup'], 'GATEWAY_CUTOVER_BACKUP_CHANGED')
        with fs._directory(self.backups / ('gateway-' + self.lease.lease_id)) as fd:
            snapshot = _json(files._read(fd, 'snapshot.json', MAX_RECORD))
            require(snapshot['sqlite'] == self.prepared['intent']['sqlite']
                and snapshot['sqlite']['sqlite_schema'] == 6, 'GATEWAY_CUTOVER_BACKUP_CHANGED')
        r._sources(fence.opened, expected, cancel=cancel)
        return expected

    def copy(self, fd, role, fence, cancel):
        observed = self.pending(fd, role); raw = self.binaries[role]
        flags = os.O_WRONLY | stage._FILE_FLAGS
        handle = os.open(role + '.pending', flags | (os.O_CREAT | os.O_EXCL if observed is None else 0), 0o600, dir_fd=fd)
        try:
            before = os.fstat(handle)
            if observed is not None:
                require(files._same(before, observed[0]), 'GATEWAY_CUTOVER_PENDING_CHANGED')
            else: files._private(handle, directory=False)
            os.lseek(handle, before.st_size, os.SEEK_SET)
            deadline = time.monotonic() + 60
            for offset in range(before.st_size, len(raw), 1024 * 1024):
                b._cancel(cancel, deadline); files._write(handle, raw[offset:offset + 1024 * 1024])
            os.fsync(handle)
            os.fchown(handle, 0, self.runtime.account().pw_gid); os.fchmod(handle, 0o750); os.fsync(handle)
        finally: os.close(handle)
        os.fsync(fd); fence.assert_held()
        observed = self.pending(fd, role)
        require(observed is not None and observed[1] == raw, 'GATEWAY_CUTOVER_PENDING_CHANGED')
        return _identity(*observed)

    def finish(self, fence, action, cancel):
        # Once successor publication begins, only its coordinator may recover.
        # An old file rollback must never invalidate a selected target profile.
        fs._absent(self.lease._directory, 'gateway-active-profile.attempt')
        with fs._directory(self.runtime.root / 'control') as fd:
            fs._absent(fd, 'active-profile.intent.json'); fs._absent(fd, 'active-profile.json')
        expected = self.verify_backup(fence, cancel); fence.assert_held()
        role, arm, done, active = self.observe()
        if action == 'rollback':
            require(role == 'target' and done is not None, 'GATEWAY_CUTOVER_TARGET_REQUIRED')
            with self.directory() as fd: files._new(fd, 'rollback.json', canonical_bytes(self.owner))
            role, arm, done, active = self.observe()
        if action == 'check': require(done is not None, 'GATEWAY_CUTOVER_INCOMPLETE')
        elif done is None:
            with self.directory(create=True) as fd:
                if arm is None:
                    replacement = self.copy(fd, role, fence, cancel)
                    arm = {'owner': self.owner, 'role': role, 'previous': active, 'replacement': replacement}
                    files._new(fd, role + '.armed.json', canonical_bytes(arm))
                fence.assert_held(); _, _, _, active = self.observe()
                if active != arm['replacement']:
                    b._cancel(cancel, time.monotonic() + 60)
                    with fs._directory(self.runtime.root) as parent:
                        os.rename(role + '.pending', self.runtime.profile.binary.name, src_dir_fd=fd, dst_dir_fd=parent)
                        os.fsync(parent); os.fsync(fd)
                fence.assert_held(); r._sources(fence.opened, expected, cancel=cancel)
                files._new(fd, role + '.done.json', canonical_bytes({
                    'owner': self.owner, 'armed_sha256': sha(canonical_bytes(arm)), 'role': role}))
        fence.assert_held(); role, _, done, active = self.observe(); require(done is not None)
        r._sources(fence.opened, expected, cancel=cancel)
        return {'version': 1, 'state': 'ORIGINAL_BINARY_RESTORED_ACTIVITY_CLOSED' if role == 'source'
            else 'TARGET_BINARY_INSTALLED_ACTIVITY_CLOSED', **self.owner, 'historical_only': True,
            'binary': active, 'binary_commit': self.prepared['intent']['assessment'][
                'source' if role == 'source' else 'target']['release']['commit'],
            'active_binary_changed': role == 'target', 'original_binary_restored': role == 'source',
            'active_profile_changed': False, 'activity_resumed': False, 'services_started': False,
            'sqlite_restored': False, 'rollback_verified': False, 'boot_requalified': False,
            'apply_allowed': False, 'restore_to_original_allowed': False, 'phase6_complete': False}


class _CutoverDrain(hd.HttpDrain):
    def __init__(self, control):
        super().__init__(control.runtime.web, cleaner=session_cleaner.SessionCleaner(control.runtime.web))
        self.control = control

    def _gateway_binding(self, foundation): return self.control.native_stopped(foundation)


def _barrier(control):
    raw = g.f._read(control.lease._directory, 'http-drain-' + control.lease.lease_id + '.attempt',
                    control.lease.scope.web_gid, limit=32768)
    return hd.HttpDrainLease(_CutoverDrain(control), control.lease, raw)


@closed
def apply(snapshot, *, source_package, target_package, target_commit, direction, confirmed, cancel=None):
    require(confirmed is True, 'GATEWAY_CUTOVER_CONSENT_REQUIRED')
    require(type(snapshot) is b.GatewayBackup and type(snapshot.fence) is g.GatewayStateFence,
            'GATEWAY_CUTOVER_SNAPSHOT_REQUIRED')
    fence = snapshot.fence; runtime = fence.runtime; lease = fence.barrier._lease
    g._inputs(runtime, fence.barrier, confirmed); snapshot.verify(cancel=cancel)
    fs._absent(lease._directory, MARKER)
    prepared, binaries = _preparation(runtime, snapshot.slot.parent, lease.lease_id,
        source_package, target_package, target_commit, direction)
    runtime.stopped(); account = runtime.account()
    with fs._directory(runtime.root) as fd:
        before = os.stat(runtime.profile.binary.name, dir_fd=fd, follow_symlinks=False)
        original = g.f._read(fd, runtime.profile.binary.name, account.pw_gid, mode=0o750, limit=stage.MAX_BINARY)
        require(original == binaries['source'] and files._same(before, os.stat(
            runtime.profile.binary.name, dir_fd=fd, follow_symlinks=False)), 'GATEWAY_CUTOVER_BINARY_CHANGED')
    raw = canonical_bytes({'version': 1, 'policy': POLICY, 'instance': runtime.web.spec.instance,
        'lease_id': lease.lease_id, 'source_manifest': runtime.manifest(account),
        'preparation_sha256': sha(canonical_bytes(prepared)), 'fence_sha256': sha(fence.raw),
        'original_binary': _identity(before, original)})
    require(len(raw) <= MAX_RECORD, 'GATEWAY_CUTOVER_LIMIT')
    control = _Cutover(runtime, lease, snapshot.slot.parent, prepared, binaries, raw)
    with fs._directory(control.slot.parent) as parent:
        files._private(parent, directory=True); fs._absent(parent, control.slot.name)
        disk = os.fstatvfs(parent)
        require(disk.f_bavail * disk.f_frsize >= max(map(len, binaries.values())) * 2 + files.MIN_FREE_BYTES,
                'GATEWAY_CUTOVER_FREE_SPACE_REQUIRED')
    control.verify_backup(fence, cancel); fence.assert_held(); b._cancel(cancel, time.monotonic() + 60)
    files._new(lease._directory, MARKER, raw)
    # Borrow the already-open, exclusively locked inodes. Only this local view
    # audits the intermediate binary; closing it cannot release the caller's FDs.
    with g.GatewayStateFence(runtime, _barrier(control), ExitStack(), fence.opened, fence.raw, fence.mount) as native:
        return control.finish(native, 'resume', cancel)


@closed
def recover(runtime, backups, lease_id, *, source_package, target_package, target_commit,
            direction, action, confirmed, cancel=None):
    require(confirmed is True, 'GATEWAY_CUTOVER_CONSENT_REQUIRED')
    require(type(runtime) is GatewayServiceRuntime and isinstance(backups, Path) and backups.is_absolute()
        and type(lease_id) is str and re.fullmatch('[a-f0-9]{32}', lease_id)
        and action in ('resume', 'check', 'rollback'), 'GATEWAY_CUTOVER_INPUT_REJECTED')
    require(os.getuid() == os.geteuid() == 0, 'GATEWAY_CUTOVER_ROOT_REQUIRED')
    account, _, _, _ = runtime.web._inspect_configuration(); scope = runtime.web._scope(account)
    with scope.recover(lease_id, confirmed=True) as lease:
        raw = _optional(lease._directory, MARKER)
        require(raw is not None, 'GATEWAY_CUTOVER_INTENT_REQUIRED')
        prepared, binaries = _preparation(runtime, backups, lease_id,
            source_package, target_package, target_commit, direction)
        control = _Cutover(runtime, lease, backups, prepared, binaries, raw)
        with g.recover(runtime, _barrier(control), confirmed=True) as fence:
            return control.finish(fence, action, cancel)
