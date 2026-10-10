"""Private recoverable external-reservation release, with activity still closed.

Filesystem primitive only: no current SQL admission, data reopen or service start.
The durable outer intent covers the cut after the last native RELEASE disappears.
Existing file-plan StepSpecs, strict readers and native release stay unchanged.
"""
from contextlib import ExitStack, contextmanager
import fcntl
import os
from pathlib import Path
import stat

from installer import mobile_reopen_files as source
from installer.maintenance import MaintenanceLease
from installer.model import (MAX_DOCUMENT_BYTES, ErrorCode, canonical_bytes,
                             exact_keys, require, strict_json_loads)
from installer.transaction import StateJournal

fs, files, f, ef, da, guard = source.fs, source.files, source.f, source.ef, source.da, source.guard
MAX_PLAN = 16384
SOURCE_FILES = {'profile.json': 16384, 'transaction/state.json': MAX_DOCUMENT_BYTES,
                **{role + '-original.json': module.MAX_JOURNAL for role, module in source.MODULES.items()},
                **{role + suffix: 4096 for role in source.ROLES for suffix in ('-intent.json', '-released.json')},
                'data-access-original.json': 2048, 'external-original.json': ef.MAX_JOURNAL}


def _read_path(root, name, limit):
    path = root / name
    with fs._directory(path.parent) as fd:
        files._private(fd, directory=True)
        return files._read(fd, path.name, limit)


def _inputs(lease, backups, confirmed):
    require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
    require(type(lease) is MaintenanceLease and os.getuid() == os.geteuid() == 0,
            ErrorCode.INVALID_DATA)
    require(isinstance(backups, Path) and backups.is_absolute(), ErrorCode.INVALID_DATA)
    lease.assert_held()
    with fs._directory(backups) as fd:
        files._private(fd, directory=True)
        info = os.fstat(fd)
        return {'device': info.st_dev, 'inode': info.st_ino}


def _parents(lease):
    root = source.journal_root(lease)
    return {**{name: f._sha(_read_path(root, name, limit)) for name, limit in SOURCE_FILES.items()},
            'mobile_guard': f._sha(files._read(lease._directory, guard.MARKER, guard.MAX_BYTES)),
            'gateway_release': f._sha(files._read(lease._directory, source.gateway.RELEASED,
                                                 source.gateway.g.MAX_JOURNAL * 2))}


class _ConfigurationGuard:
    """Process-local proof that this plan still owns both exclusive locks."""
    def __init__(self, plan, check):
        self._plan, self._check, self._pid, self._closed = plan, check, os.getpid(), False

    def __reduce__(self): raise TypeError('Configuration guards cannot be serialized')

    def assert_held(self):
        require(not self._closed and self._pid == os.getpid(), ErrorCode.INVALID_STATE)
        self._plan.lease.assert_held(); self._check()


class ExternalReleasePlan:
    """Native caller owns the maintenance lease; instances cannot cross a fork."""
    def __init__(self, lease, backups, raw):
        self.lease, self.backups, self._raw, self._pid = lease, backups, raw, os.getpid()
        self.root = backups / ('external-release-' + lease.lease_id)
        self.source_root = source.journal_root(lease)
        self.value = strict_json_loads(raw)
        exact_keys(self.value, {'version', 'instance', 'lease_id', 'backup_root', 'backup_identity',
                                'source_plan_sha256', 'parents', 'configuration'})
        require(raw == canonical_bytes(self.value) and type(self.value['version']) is int
                and self.value['version'] == 1, ErrorCode.INVALID_STATE)
        exact_keys(self.value['parents'], {*SOURCE_FILES, 'mobile_guard', 'gateway_release'})
        require(all(type(v) is str and guard.SHA256.fullmatch(v) for v in self.value['parents'].values()),
                ErrorCode.INVALID_STATE)
        require(type(self.value['source_plan_sha256']) is str
                and guard.SHA256.fullmatch(self.value['source_plan_sha256']), ErrorCode.INVALID_STATE)
        exact_keys(self.value['backup_identity'], {'device', 'inode'})
        require(all(type(v) is int and v >= 0 for v in self.value['backup_identity'].values()),
                ErrorCode.INVALID_STATE)
        locks = self.value['configuration']
        require(type(locks) is list and len(locks) == 2, ErrorCode.INVALID_STATE)
        for row, name, gid, mode, limit in zip(locks, ('assistant-edit.lock', 'assistant.json'),
                                             (0, lease.scope.web_gid), (0o600, 0o660), (0, 2048)):
            exact_keys(row, {'name', 'gid', 'mode', 'limit', 'device', 'inode', 'sha256'})
            require(row['name'] == name and row['gid'] == gid and row['mode'] == mode and row['limit'] == limit
                    and all(type(row[k]) is int for k in ('gid', 'mode', 'limit', 'device', 'inode'))
                    and type(row['sha256']) is str and guard.SHA256.fullmatch(row['sha256']),
                    ErrorCode.INVALID_STATE)
        self.plan_sha256 = f._sha(raw)

    def __repr__(self): return '<ExternalReleasePlan private activity-closed release>'
    def __reduce__(self): raise TypeError('External release plans cannot be serialized')

    @contextmanager
    def _slot(self):
        with fs._directory(self.root) as fd:
            files._private(fd, directory=True)
            names = set(os.listdir(fd))
            require('plan.json' in names and names <= {'plan.json', 'intent.json', 'released.json'},
                    ErrorCode.INCOMPATIBLE_STATE)
            require(files._read(fd, 'plan.json', MAX_PLAN) == self._raw, ErrorCode.SOURCE_DRIFT)
            yield fd

    def _read(self, name):
        with self._slot() as fd: return source._optional(fd, name, MAX_PLAN)

    def _save(self, name, raw):
        with self._slot() as fd:
            prior = source._optional(fd, name, MAX_PLAN)
            if prior is None: files._new(fd, name, raw)
            else: require(prior == raw, ErrorCode.INCOMPATIBLE_STATE)

    def _owner(self):
        return canonical_bytes({'version': 1, 'instance': self.lease.scope.instance,
                                'lease_id': self.lease.lease_id, 'plan_sha256': self.plan_sha256,
                                'source_plan_sha256': self.value['source_plan_sha256']})

    def _receipt(self):
        return canonical_bytes({'owner': strict_json_loads(self._owner()),
                                'state': 'EXTERNAL_RESERVATIONS_RELEASED_ACTIVITY_CLOSED',
                                'external_paths_released': True, 'data_access_reopened': False,
                                'activity_resumed': False, 'services_started': False,
                                'admission_verified': False, 'current_sql_admission': False})

    def _held(self):
        require(self._pid == os.getpid(), ErrorCode.INVALID_STATE)
        identity = _inputs(self.lease, self.backups, True)
        require(self.value['instance'] == self.lease.scope.instance
                and self.value['lease_id'] == self.lease.lease_id
                and self.value['backup_root'] == str(self.backups)
                and self.value['backup_identity'] == identity, ErrorCode.SOURCE_DRIFT)
        require(_parents(self.lease) == self.value['parents'], ErrorCode.SOURCE_DRIFT)
        document = StateJournal(self.source_root / 'transaction/state.json').read()
        require(document is not None and document['state'] == 'DONE'
                and document['plan_sha256'] == self.value['source_plan_sha256']
                and len(document['steps']) == 3 and all(s['state'] == 'DONE' for s in document['steps']),
                ErrorCode.INCOMPATIBLE_STATE)
        guard.recover(self.lease, plan_sha256=self.value['source_plan_sha256'], confirmed=True).assert_held()
        for module in source.MODULES.values():
            fs._absent(self.lease._directory, module.MARKER)
            fs._absent(self.lease._directory, module.RELEASE)
        fs._absent(self.lease._directory, ef.PREPARE)
        raw = _read_path(self.source_root, 'data-access-original.json', 2048)
        require(f._read(self.lease._directory, da.MARKER, self.lease.scope.web_gid, limit=2048) == raw, ErrorCode.SOURCE_DRIFT)
        data = strict_json_loads(raw)
        exact_keys(data, {'version', 'instance', 'lease_id', 'root', 'device', 'inode', 'gid',
                          'open_mode', 'closed_mode'})
        require(data['instance'] == self.lease.scope.instance and data['lease_id'] == self.lease.lease_id
                and data['gid'] == self.lease.scope.web_gid and data['version'] == 1
                and data['open_mode'] == 0o750 and data['closed_mode'] == 0o700
                and all(type(data[k]) is int for k in ('version', 'device', 'inode', 'gid', 'open_mode', 'closed_mode')),
                ErrorCode.SOURCE_DRIFT)
        with fs._directory(Path(data['root'])) as fd:
            info = os.fstat(fd); fs._no_acl(fd)
            require((info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                    == (data['device'], data['inode'], 0, data['gid'], 0o700), ErrorCode.SOURCE_DRIFT)
        self.lease.assert_held()

    @contextmanager
    def _configuration_files(self):
        # Exclusive locks cannot coexist with the old shared ConfigurationLease,
        # even if recover() is called in the same process. Never forge/close it.
        with fs._directory(self.lease.scope.directory.parent) as conf, ExitStack() as stack:
            opened = []
            for row in self.value['configuration']:
                fd = os.open(row['name'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=conf)
                stack.callback(os.close, fd)
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    require(False, ErrorCode.MANUAL_ACTION_REQUIRED)
                opened.append((fd, row))
            def check():
                source.cf.admission.f.FinalizationStep._pending_edits(conf)
                for fd, row in opened:
                    info = os.fstat(fd); named = os.stat(row['name'], dir_fd=conf, follow_symlinks=False)
                    require((info.st_dev, info.st_ino) == (named.st_dev, named.st_ino)
                            == (row['device'], row['inode']), ErrorCode.SOURCE_DRIFT)
                    require(f._sha(f._read(conf, row['name'], row['gid'], mode=row['mode'], limit=row['limit']))
                            == row['sha256'], ErrorCode.SOURCE_DRIFT)
            check()
            yield check
            check()

    @contextmanager
    def _configuration(self):
        with self._configuration_files() as check:
            guard = _ConfigurationGuard(self, check)
            guard.assert_held()
            try:
                yield guard
                guard.assert_held()
            finally: guard._closed = True

    def _external(self):
        raw = _read_path(self.source_root, 'external-original.json', ef.MAX_JOURNAL)
        value = ef._decode(self.lease, raw)
        return raw, value

    def _absent(self):
        _, value = self._external()
        for entry in value['entries']:
            with ef._parent(entry) as parent:
                fs._absent(parent, Path(entry['path']).name)
                fs._absent(parent, entry['stage'])
        for name in (ef.PREPARE, ef.MARKER, ef.RELEASE): fs._absent(self.lease._directory, name)

    def _finish(self):
        raw, value = self._external()
        marker = source._optional(self.lease._directory, ef.MARKER, ef.MAX_JOURNAL)
        release = source._optional(self.lease._directory, ef.RELEASE, ef.MAX_JOURNAL)
        require(marker in (None, raw) and release in (None, raw), ErrorCode.SOURCE_DRIFT)
        # Inspect every reservation before touching one; foreign paths are never removed.
        for entry in value['entries']:
            with ef._parent(entry) as parent, ef._stage(entry, parent, released=True): pass
        if release is not None:
            ef.recover_unseal(self.lease, confirmed=True)
        elif marker is not None:
            ef.assert_reservation(self.lease, raw)
            ef.ExternalFence(self.lease, raw).unseal(confirmed=True)
        # Only an exact outer intent permits reconciling the lost final response.
        self._absent()

    def execute(self, action, confirmation, *, confirmed):
        require(confirmed is True and confirmation == self.plan_sha256, ErrorCode.CONFIRMATION_REQUIRED)
        require(action in ('apply', 'resume', 'check'), ErrorCode.INVALID_DATA)
        self._held()
        with self._configuration() as locked:
            return self._execute_locked(action, confirmation, confirmed=confirmed, locked=locked)

    def _execute_locked(self, action, confirmation, *, confirmed, locked):
        # Private composition seam: never reacquire a flock already held by the
        # live SQL coordinator, and never accept a closed/foreign/forked guard.
        require(confirmed is True and confirmation == self.plan_sha256, ErrorCode.CONFIRMATION_REQUIRED)
        require(action in ('apply', 'resume', 'check'), ErrorCode.INVALID_DATA)
        require(type(locked) is _ConfigurationGuard and locked._plan is self, ErrorCode.INVALID_STATE)
        locked.assert_held(); self._held()
        intent, receipt = self._read('intent.json'), self._read('released.json')
        require(intent in (None, self._owner()) and receipt in (None, self._receipt()), ErrorCode.SOURCE_DRIFT)
        require(receipt is None or intent is not None, ErrorCode.INCOMPATIBLE_STATE)
        if action == 'check':
            require(intent is not None and receipt is not None, ErrorCode.MANUAL_ACTION_REQUIRED)
            self._absent()
        else:
            if action == 'apply':
                require(intent is None and receipt is None, ErrorCode.MANUAL_ACTION_REQUIRED)
                raw, _ = self._external(); ef.assert_reservation(self.lease, raw)
                self._save('intent.json', self._owner())
            else: require(intent is not None, ErrorCode.MANUAL_ACTION_REQUIRED)
            require(self._read('intent.json') == self._owner(), ErrorCode.SOURCE_DRIFT)
            locked.assert_held(); self._held(); self._finish(); self._held(); locked.assert_held()
            self._save('released.json', self._receipt())
        self._held()
        locked.assert_held()
        return {**strict_json_loads(self._receipt()), 'plan_sha256': self.plan_sha256}


def begin(control, *, confirmed):
    """Plan under the old live configuration context; close that context to apply."""
    require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
    require(type(control) is source.ReopenFilesPlan, ErrorCode.INVALID_DATA)
    document = control.journal.read()
    require(document is not None and document['state'] == 'DONE', ErrorCode.INCOMPATIBLE_STATE)
    control.execute('check', document['plan_sha256'], confirmed=True)
    identity = _inputs(control.lease, control.backups, True)
    locks = [{'name': name, 'gid': gid, 'mode': mode, 'limit': limit,
              'device': identity[0], 'inode': identity[1], 'sha256': f._sha(data)}
             for _, name, gid, mode, limit, identity, data in control.configuration._files]
    raw = canonical_bytes({'version': 1, 'instance': control.lease.scope.instance,
        'lease_id': control.lease.lease_id, 'backup_root': str(control.backups), 'backup_identity': identity,
        'source_plan_sha256': document['plan_sha256'], 'parents': _parents(control.lease), 'configuration': locks})
    result = ExternalReleasePlan(control.lease, control.backups, raw)
    # A partial plan is preserved for inspection, never adopted or overwritten.
    try:
        with fs._directory(result.root) as fd:
            files._private(fd, directory=True)
            require(set(os.listdir(fd)) == {'plan.json'}
                    and files._read(fd, 'plan.json', MAX_PLAN) == raw, ErrorCode.INCOMPATIBLE_STATE)
    except FileNotFoundError:
        with fs._directory(control.backups) as fd:
            os.mkdir(result.root.name, 0o700, dir_fd=fd); os.fsync(fd)
        with fs._directory(result.root) as fd:
            files._private(fd, directory=True); files._new(fd, 'plan.json', raw)
    result._held()
    return result


def recover(lease, backup_root, *, confirmed):
    """Load exact existing plan, never synthesize a missing intent or receipt."""
    _inputs(lease, backup_root, confirmed)
    root = backup_root / ('external-release-' + lease.lease_id)
    raw = _read_path(root, 'plan.json', MAX_PLAN)
    result = ExternalReleasePlan(lease, backup_root, raw)
    with result._slot(): pass
    result._held()
    return result
