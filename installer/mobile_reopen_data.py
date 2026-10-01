"""Private recoverable data-path release; maintenance and mobile stay closed.

This filesystem primitive grants neither current SQL admission nor service
activation. An interrupted native reopen is reconciled only under our exact
outer intent. Native strict readers and their historical contracts are intact.
"""
from contextlib import contextmanager
from dataclasses import asdict
import os
from pathlib import Path
import stat

from installer import mobile_reopen_external as e
from installer.model import ErrorCode, canonical_bytes, exact_keys, require, strict_json_loads

fs, files, f, da, source = e.fs, e.files, e.f, e.da, e.source
MAX_PLAN = 16384


def _runtime(runtime, lease):
    account = da._inputs(runtime, lease)
    profile = asdict(runtime.spec)
    for name in ('root', 'webroot', 'maintenance_directory'):
        profile[name] = str(profile[name]) if profile[name] is not None else None
    return account, {'spec': profile, 'uid': account.pw_uid, 'gid': account.pw_gid,
                     'user': account.pw_name}


class DataReleasePlan:
    def __init__(self, runtime, lease, backups, raw):
        self.runtime, self.lease, self.backups = runtime, lease, backups
        self._raw, self._pid = raw, os.getpid()
        self.root = backups / ('data-release-' + lease.lease_id)
        self.value = strict_json_loads(raw)
        exact_keys(self.value, {'version', 'instance', 'lease_id', 'backup_root', 'backup_identity',
                                'external_plan_sha256', 'parents', 'runtime'})
        require(type(self.value['version']) is int and self.value['version'] == 1
                and canonical_bytes(self.value) == raw, ErrorCode.INVALID_STATE)
        exact_keys(self.value['parents'], {*e.SOURCE_FILES, 'mobile_guard', 'gateway_release'})
        require(all(type(v) is str and e.guard.SHA256.fullmatch(v) for v in self.value['parents'].values())
                and type(self.value['external_plan_sha256']) is str
                and e.guard.SHA256.fullmatch(self.value['external_plan_sha256']), ErrorCode.INVALID_STATE)
        exact_keys(self.value['backup_identity'], {'device', 'inode'})
        require(all(type(v) is int and v >= 0 for v in self.value['backup_identity'].values()),
                ErrorCode.INVALID_STATE)
        self.plan_sha256 = f._sha(raw)

    def __repr__(self): return '<DataReleasePlan private activity-closed release>'
    def __reduce__(self): raise TypeError('Data release plans cannot be serialized')

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
                                'external_plan_sha256': self.value['external_plan_sha256']})

    def _receipt(self):
        return canonical_bytes({'owner': strict_json_loads(self._owner()),
                                'state': 'DATA_ACCESS_REOPENED_ACTIVITY_CLOSED',
                                'external_paths_released': True, 'data_access_reopened': True,
                                'activity_resumed': False, 'services_started': False,
                                'admission_verified': False, 'current_sql_admission': False})

    def _held(self):
        require(self._pid == os.getpid(), ErrorCode.INVALID_STATE)
        identity = e._inputs(self.lease, self.backups, True)
        account, profile = _runtime(self.runtime, self.lease)
        require(self.value['instance'] == self.lease.scope.instance
                and self.value['lease_id'] == self.lease.lease_id
                and self.value['backup_root'] == str(self.backups)
                and self.value['backup_identity'] == identity
                and canonical_bytes(self.value['runtime']) == canonical_bytes(profile), ErrorCode.SOURCE_DRIFT)
        require(e._parents(self.lease) == self.value['parents'], ErrorCode.SOURCE_DRIFT)
        raw = e._read_path(self.backups / ('external-release-' + self.lease.lease_id), 'plan.json', e.MAX_PLAN)
        require(f._sha(raw) == self.value['external_plan_sha256'], ErrorCode.SOURCE_DRIFT)
        external = e.ExternalReleasePlan(self.lease, self.backups, raw)
        require(all(external.value[k] == self.value[k] for k in
                    ('instance', 'lease_id', 'backup_root', 'backup_identity', 'parents')), ErrorCode.SOURCE_DRIFT)
        require(external._read('intent.json') == external._owner()
                and external._read('released.json') == external._receipt(), ErrorCode.SOURCE_DRIFT)
        # Historical parents are inspected directly: their live 0700 reader is
        # deliberately not invoked after this primitive changes data access.
        document = e.StateJournal(external.source_root / 'transaction/state.json').read()
        require(document is not None and document['state'] == 'DONE'
                and document['plan_sha256'] == external.value['source_plan_sha256']
                and len(document['steps']) == 3 and all(s['state'] == 'DONE' for s in document['steps']),
                ErrorCode.INCOMPATIBLE_STATE)
        e.guard.recover(self.lease, plan_sha256=external.value['source_plan_sha256'], confirmed=True).assert_held()
        for module in source.MODULES.values():
            fs._absent(self.lease._directory, module.MARKER); fs._absent(self.lease._directory, module.RELEASE)
        external._absent()
        with fs._directory(self.runtime.spec.root.parent) as fd:
            for name in ('boot', 'public'): fs._absent(fd, name)
        original = e._read_path(external.source_root, 'data-access-original.json', 2048)
        with fs._directory(self.runtime.spec.root / 'data') as fd:
            info = os.fstat(fd); fs._no_acl(fd)
            expected = {'version': 1, 'instance': self.lease.scope.instance, 'lease_id': self.lease.lease_id,
                        'root': str(self.runtime.spec.root / 'data'), 'device': info.st_dev, 'inode': info.st_ino,
                        'gid': account.pw_gid, 'open_mode': 0o750, 'closed_mode': 0o700}
            mode = stat.S_IMODE(info.st_mode)
            require(original == canonical_bytes(expected) and info.st_uid == 0 and info.st_gid == account.pw_gid
                    and mode in (0o700, 0o750), ErrorCode.SOURCE_DRIFT)
        try: marker = f._read(self.lease._directory, da.MARKER, account.pw_gid, limit=2048)
        except FileNotFoundError: marker = None
        require(marker in (None, original) and (marker is not None or mode == 0o750), ErrorCode.SOURCE_DRIFT)
        self.lease.assert_held()
        return external, account, marker, mode

    def _open(self):
        _, account, marker, mode = self._held()
        require(marker is None and mode == 0o750, ErrorCode.MANUAL_ACTION_REQUIRED)
        self.runtime._inspect_configuration()
        da.hd.identity_census(account.pw_uid, account.pw_gid, ())
        _, _, marker, mode = self._held()
        require(marker is None and mode == 0o750, ErrorCode.SOURCE_DRIFT)

    def execute(self, action, confirmation, *, confirmed):
        require(confirmed is True and confirmation == self.plan_sha256, ErrorCode.CONFIRMATION_REQUIRED)
        require(action in ('apply', 'resume', 'check'), ErrorCode.INVALID_DATA)
        external, _, _, _ = self._held()
        with external._configuration() as locked:
            _, _, marker, mode = self._held()
            intent, receipt = self._read('intent.json'), self._read('released.json')
            require(intent in (None, self._owner()) and receipt in (None, self._receipt()), ErrorCode.SOURCE_DRIFT)
            require(receipt is None or intent is not None, ErrorCode.INCOMPATIBLE_STATE)
            if action == 'check':
                require(intent is not None and receipt is not None, ErrorCode.MANUAL_ACTION_REQUIRED)
                self._open()
            else:
                if action == 'apply':
                    require(intent is None and receipt is None and marker is not None and mode == 0o700,
                            ErrorCode.MANUAL_ACTION_REQUIRED)
                    self._save('intent.json', self._owner())
                else: require(intent is not None, ErrorCode.MANUAL_ACTION_REQUIRED)
                require(self._read('intent.json') == self._owner(), ErrorCode.SOURCE_DRIFT)
                locked.assert_held()
                _, _, marker, _ = self._held()
                if receipt is not None:
                    self._open()  # A receipt never authorizes another native effect.
                elif marker is not None:
                    # Qualified explicit recovery recloses an interrupted 0750
                    # + marker state before runtime audit and process census.
                    # A census failure leaves closure in place; no signals sent.
                    fence = da.recover(self.runtime, self.lease, confirmed=True)
                    try:
                        self._held(); locked.assert_held(); fence.reopen(confirmed=True)
                    finally: fence.close()
                self._open(); locked.assert_held()
                if receipt is None:
                    # Reconcile unlink whose response was lost before native
                    # fsync: make the completed effect durable before our receipt.
                    with fs._directory(self.runtime.spec.root / 'data') as fd: os.fsync(fd)
                    os.fsync(self.lease._directory)
                    self._open(); locked.assert_held()
                self._save('released.json', self._receipt())
            self._open(); locked.assert_held()
            return {**strict_json_loads(self._receipt()), 'plan_sha256': self.plan_sha256}


def begin(external, access, *, confirmed):
    require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
    require(type(external) is e.ExternalReleasePlan and type(access) is da.DataAccessFence,
            ErrorCode.INVALID_DATA)
    require(access._lease is external.lease, ErrorCode.INVALID_DATA)
    access.assert_held()
    external.execute('check', external.plan_sha256, confirmed=True)
    identity = e._inputs(external.lease, external.backups, True)
    account, profile = _runtime(access._runtime, external.lease)
    access._runtime._inspect_configuration(); da.hd.identity_census(account.pw_uid, account.pw_gid, ())
    raw = canonical_bytes({'version': 1, 'instance': external.lease.scope.instance,
        'lease_id': external.lease.lease_id, 'backup_root': str(external.backups), 'backup_identity': identity,
        'external_plan_sha256': external.plan_sha256, 'parents': e._parents(external.lease), 'runtime': profile})
    result = DataReleasePlan(access._runtime, external.lease, external.backups, raw)
    result._held()
    try:
        with fs._directory(result.root) as fd:
            files._private(fd, directory=True)
            require(set(os.listdir(fd)) == {'plan.json'} and files._read(fd, 'plan.json', MAX_PLAN) == raw,
                    ErrorCode.INCOMPATIBLE_STATE)
    except FileNotFoundError:
        with fs._directory(external.backups) as fd:
            os.mkdir(result.root.name, 0o700, dir_fd=fd); os.fsync(fd)
        with fs._directory(result.root) as fd:
            files._private(fd, directory=True); files._new(fd, 'plan.json', raw)
    result._held()
    return result


def recover(runtime, lease, backup_root, *, confirmed):
    """Read-only load, including exact interrupted states; execute resumes them."""
    e._inputs(lease, backup_root, confirmed)
    _runtime(runtime, lease)
    raw = e._read_path(backup_root / ('data-release-' + lease.lease_id), 'plan.json', MAX_PLAN)
    result = DataReleasePlan(runtime, lease, backup_root, raw)
    with result._slot(): pass
    result._held()
    return result
