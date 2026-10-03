"""Live, read-only admission after 6B7b3; no release or service-start authority.

Every acquisition re-exports current SQL under the qualified bounded read lock.
Saved observations are historical only. Interrupted attempts are never reused.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import time

from installer import mobile_reopen_files as r, coordinated_backup as c
from installer import upgrade_backup as sql, scheduler_admission as sa
from installer.model import canonical_bytes, strict_json_loads
from installer.operations import OperationContext
from installer.web_releases import STORAGE_COMMIT

files, fs, f, p = c.files, c.fs, c.f, c.p


class AdmissionError(RuntimeError):
    """Fixed non-secret diagnostic; never exposes a source exception."""


def require(ok, code='MOBILE_ADMISSION_CHANGED'):
    if not ok: raise AdmissionError(code)


def _id(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{32}', value), 'MOBILE_ADMISSION_BACKUP_REJECTED')
    return value


def _json(root, name, limit=files.MAX_MANIFEST_BYTES):
    with fs._directory(root) as fd:
        files._private(fd, directory=True)
        raw = files._read(fd, name, limit)
    value = strict_json_loads(raw)
    require(type(value) is dict and p._json(value) == raw, 'MOBILE_ADMISSION_BACKUP_REJECTED')
    return raw, value


def _digest(root, name, maximum, cancel):
    files._name(name)
    with fs._directory(root) as parent:
        files._private(parent, directory=True)
        fd = os.open(name, files.REGULAR, dir_fd=parent)
        try:
            files._private(fd, directory=False)
            before = os.fstat(fd); digest = hashlib.sha256(); count = 0
            require(before.st_size <= maximum, 'MOBILE_ADMISSION_ARCHIVE_LIMIT')
            deadline = time.monotonic() + 60
            while True:
                require(cancel is None or not cancel.is_set(), 'MOBILE_ADMISSION_INTERRUPTED')
                require(time.monotonic() < deadline, 'MOBILE_ADMISSION_ARCHIVE_LIMIT')
                chunk = os.read(fd, 65536)
                if not chunk: break
                count += len(chunk); require(count <= maximum, 'MOBILE_ADMISSION_ARCHIVE_LIMIT')
                digest.update(chunk)
            require(count == before.st_size and files._same(before, os.fstat(fd))
                and files._same(before, os.stat(name, dir_fd=parent, follow_symlinks=False)),
                'MOBILE_ADMISSION_ARCHIVE_CHANGED')
            return {'bytes': count, 'sha256': digest.hexdigest()}
        finally: os.close(fd)


def _blobs(root, records, digits, maximum, cancel):
    names = set()
    for row in records:
        if row['kind'] == 'directory': continue
        require(row['kind'] == 'file' and type(row.get('blob')) is str
            and re.fullmatch('[0-9]{' + str(digits) + '}[.]bin', row['blob'])
            and row['blob'] not in names, 'MOBILE_ADMISSION_BACKUP_REJECTED')
        names.add(row['blob'])
        require(_digest(root, row['blob'], maximum, cancel) ==
            {k: row[k] for k in ('bytes', 'sha256')}, 'MOBILE_ADMISSION_ARCHIVE_CHANGED')
    with fs._directory(root) as fd:
        files._private(fd, directory=True)
        require(set(os.listdir(fd)) == names, 'MOBILE_ADMISSION_ARCHIVE_CHANGED')


def _index(records):
    result = {}
    for row in records:
        key = row['scope'], row['path']
        require(key not in result, 'MOBILE_ADMISSION_ENVELOPE_CHANGED')
        result[key] = {k: v for k, v in row.items() if k != 'blob'}
    return result


def _data_blobs(snapshot, lease, cancel):
    # The qualified legacy reader normalizes exceptions from its consumer.
    # Let its exit-time lease check finish, then propagate our fixed rejection.
    rejected = None
    with snapshot._open(lease) as (_, saved):
        try: _blobs(snapshot._slot / 'blobs', saved['records'], 6, files.MAX_FILE_BYTES, cancel)
        except AdmissionError as error: rejected = error
    if rejected is not None: raise rejected from None


def _private_record(name, raw=None):
    row = {'scope': 'configuration', 'path': 'maintenance/' + name,
           'kind': 'directory' if raw is None else 'file', 'uid': 0, 'gid': 0,
           'mode': 0o700 if raw is None else 0o600}
    if raw is not None: row.update(bytes=len(raw), sha256=f._sha(raw))
    return row


def _envelope(saved, removed, added):
    """Reconcile exact journal transitions; no excluded maintenance subtree."""
    expected = _index(saved)
    for name, raw in removed.items():
        key = 'configuration', 'maintenance/' + name
        require(key in expected and expected.pop(key) == _private_record(name, raw),
                'MOBILE_ADMISSION_PARENT_CHANGED')
    for name, raw in added.items():
        key = 'configuration', 'maintenance/' + name
        require(key not in expected, 'MOBILE_ADMISSION_PARENT_CHANGED')
        expected[key] = _private_record(name, raw)
    return expected


def _journal_changes(control, document):
    released = files._read(control.lease._directory, r.gateway.RELEASED, r.gateway.g.MAX_JOURNAL * 2)
    return _journal_records(control, document, released, control._guard._raw)


def _journal_records(control, document, released, guard_raw):
    """Build exact records from already-validated native or retained originals."""
    receipt = strict_json_loads(released)
    removed = {r.MODULES[role].MARKER: control._raw(role) for role in r.ROLES}
    removed[r.gateway.g.MARKER] = canonical_bytes(receipt['intent']['fence'])
    added = {r.gateway.RELEASED: released, r.guard.MARKER: guard_raw,
        'mobile-reopen-files': None, 'mobile-reopen-files/transaction': None,
        'mobile-reopen-files/transaction/.transaction.lock': b'',
        'mobile-reopen-files/transaction/state.json': canonical_bytes(document) + b'\n',
        'mobile-reopen-files/profile.json': canonical_bytes(control.profile())}
    for role in (*r.ROLES, 'data-access', 'external'):
        raw = control._read(role + '-original.json', files.MAX_MANIFEST_BYTES)
        require(raw is not None and f._sha(raw) == control.profile()['journals'][role])
        added['mobile-reopen-files/' + role + '-original.json'] = raw
    engine = control.engine()
    for spec, row in zip(document['plan']['steps'], document['steps']):
        context = OperationContext(document['installation_id'], spec, row['evidence'], engine.secrets)
        operation = engine.registry.get(spec)
        raw = canonical_bytes(operation.owner(context))
        for suffix in ('-intent.json', '-released.json'):
            added['mobile-reopen-files/' + operation.role + suffix] = raw
    return removed, added


class _Archives:
    _journal_changes = staticmethod(_journal_changes)

    def __init__(self, control, runtime, database, document, cancel):
        self.control, self.runtime, self.cancel = control, runtime, cancel
        self.web = control.profile()['web_backup']
        self.slot = control.backups / _id(self.web['backup_id'])
        raw, self.coordinated = _json(self.slot, 'coordinated.json')
        require(f._sha(raw) == self.web['manifest_sha256'])
        self.raw = raw; value = self.coordinated; lease = control.lease
        commit = control.data._runtime.source_commit
        self.target = f._sha(p._json([database['host'], database['port'], database['name'].lower()]))
        require(value['instance'] == lease.scope.instance and value['lease_id'] == lease.lease_id
            and value['source_commit'] == commit and value['target_sha256'] == self.target
            and value['service_barrier']['profile_sha256'] == f._sha(control.barrier._profile))
        self.sql = value['sql_backup']; self.sql_slot = self.slot / 'sql' / _id(self.sql['backup_id'])
        self.sql_raw, self.manifest = _json(self.sql_slot, 'manifest.json')
        manifest = self.manifest
        require(f._sha(self.sql_raw) == self.sql['manifest_sha256']
            and manifest['source_commit'] == commit
            and manifest['runtime_sha256'] == f.get_release(commit).runtime_sha256
            and manifest['source_webroot'] == str(control.data._runtime.spec.webroot)
            and manifest['source_configuration'] == str(lease.scope.directory.parent)
            and manifest['source_state_root'] == str(runtime.state_root)
            and manifest['fresh_attempt_name'] == 'fresh-' + self.target + '.attempt'
            and manifest['database_sha256'] == self.sql['database_sha256'])
        data = value['data_snapshot']
        self.data = files.FileSnapshot(self.slot / 'data' / _id(data['snapshot_id']),
            data['manifest_sha256'], lease.scope.instance, lease.lease_id, lease.scope.web_gid)
        with self.data._open(lease) as (_, saved):
            account = control.data._account; spec = control.data._runtime.spec
            require(saved['web_uid'] == account.pw_uid and saved['web_gid'] == account.pw_gid
                and saved['roots'] == [{'scope': n.replace('-', '_'), 'root': str(spec.root / 'data' / n)}
                    for n in sorted((*r.hd.h.DATA, 'uploads'), key=lambda n: n.replace('-', '_'))])
        self.removed, self.added = self._journal_changes(control, document)
        self.expected = _envelope(manifest['files'], self.removed, self.added)

    def check(self):
        lease = self.control.lease
        require(_json(self.slot, 'coordinated.json')[0] == self.raw)
        require(_json(self.slot, 'verified.json')[1] == self.web)
        require(_json(self.sql_slot, 'manifest.json')[0] == self.sql_raw)
        require(_json(self.sql_slot, 'verified.json')[1] == self.sql)
        require(_digest(self.sql_slot, 'database.ndjson', c.br.MAX_ARCHIVE, self.cancel) ==
            {'bytes': self.manifest['database_bytes'], 'sha256': self.sql['database_sha256']},
            'MOBILE_ADMISSION_ARCHIVE_CHANGED')
        require(sql._tail(self.sql_slot / 'database.ndjson') == {'type': 'complete',
            'request_id': self.sql['backup_id'], **{k: self.sql[k] for k in ('tables', 'rows', 'logical_sha256')}})
        _blobs(self.sql_slot / 'files', self.manifest['files'], 5, sql.MAX_FILE_BYTES, self.cancel)
        with fs._directory(self.sql_slot) as fd:
            journal = files._read(fd, 'fresh.attempt', sql.MAX_FILE_BYTES)
        require(f._sha(journal) == self.manifest['fresh_attempt_sha256'])
        with fs._directory(self.runtime.state_root) as fd:
            require(f._read(fd, self.manifest['fresh_attempt_name'], 0, mode=0o600) == journal,
                    'MOBILE_ADMISSION_PARENT_CHANGED')
        require(self.data.report(lease) == self.coordinated['data_snapshot'])
        _data_blobs(self.data, lease, self.cancel)
        self.data.verify_sources(lease, cancel=self.cancel)
        actual = sql._scan({'web': self.control.data._runtime.spec.webroot,
                           'configuration': lease.scope.directory.parent}, cancel=self.cancel)
        require(_index(actual) == self.expected, 'MOBILE_ADMISSION_ENVELOPE_CHANGED')


class AdmissionWindow:
    def __init__(self, control, fence, schedulers, archives, envelope, slot, report):
        self._control, self._fence, self._schedulers = control, fence, schedulers
        self._archives, self._envelope, self._slot, self._report = archives, envelope, slot, report
        self._pid, self._closed = os.getpid(), False

    def __repr__(self): return '<AdmissionWindow private live SQL and file observation>'
    def __reduce__(self): raise TypeError('Admission windows cannot be serialized')

    def assert_held(self):
        try:
            require(not self._closed and os.getpid() == self._pid, 'MOBILE_ADMISSION_WINDOW_CLOSED')
            self._fence.assert_held()
            self._control._live(); self._schedulers.assert_held()
            self._envelope(); self._archives.check()
            self._fence.assert_held()
        except AdmissionError: raise
        except Exception: raise AdmissionError('MOBILE_ADMISSION_UNAVAILABLE') from None

    def report(self):
        self.assert_held()
        return deepcopy(self._report)


@contextmanager
def acquire(control, runtime, source, payload, authority, confirmation, *, confirmed, allow_global_read_lock, cancel=None):
    """Fresh bounded observation only; no saved receipt grants a new live window."""
    window = None
    try:
        require(confirmed is True and allow_global_read_lock is True, 'MOBILE_ADMISSION_CONSENT_REQUIRED')
        require(type(control) is r.ReopenFilesPlan and type(runtime) is p.PhpRuntime
            and type(authority) is c.d.SqlAuthorityCredentials and isinstance(source, Path),
            'MOBILE_ADMISSION_INPUT_REJECTED')
        require(type(payload) is dict and payload.get('mode') == 'upgrade'
            and payload.get('assistant') == {'action': 'preserve'}, 'MOBILE_ADMISSION_INPUT_REJECTED')
        require(cancel is None or not cancel.is_set(), 'MOBILE_ADMISSION_INTERRUPTED')
        document = control.execute('check', confirmation, confirmed=True)['transaction']
        require(document['state'] == 'DONE', 'MOBILE_ADMISSION_FILES_REQUIRED')
        value = deepcopy(payload); config = f._configuration(value, fresh=False)
        lease = control.lease; config_root = lease.scope.directory.parent.parent
        with ExitStack() as stack:
            gid, web, directory, conf, webfd, inc = f._open(runtime, config, config_root, stack)
            require(directory == lease.scope.directory.parent and gid == lease.scope.web_gid
                and web == control.data._runtime.spec.webroot, 'MOBILE_ADMISSION_INSTANCE_MISMATCH')
            commit = control.data._runtime.source_commit
            current = f.FinalizationStep(runtime, source, repository=p.WEB_REPOSITORY, commit=commit)
            database, loader, ca = f._prepared(config, value, directory, conf, gid)
            completed = f._completed(conf, webfd, inc, gid, commit=commit)
            require(database['host'] == '127.0.0.1' and database['tls_required'] is False and ca is None
                and f._json_read(conf, 'state.json', gid)['migration_retained'] is False,
                'MOBILE_ADMISSION_PROFILE_REJECTED')
            require(authority._user != database['user'] and authority._password != database['password'],
                    'MOBILE_ADMISSION_ACCOUNT_SEPARATION_REQUIRED')
            def envelope():
                current._sources(web); current._pending_edits(conf)
                require(f._prepared(config, value, directory, conf, gid) == (database, loader, ca)
                    and f._completed(conf, webfd, inc, gid, commit=commit) == completed,
                    'MOBILE_ADMISSION_ENVELOPE_CHANGED')
            archives = _Archives(control, runtime, database, document, cancel)
            envelope(); archives.check()
            schedulers = stack.enter_context(sa.acquire())
            # Own fresh slot outside the evolving configuration tree. A crash or
            # a lost reply leaves historical bytes; no attempt is overwritten.
            parent = stack.enter_context(fs._directory(control.backups))
            files._private(parent, directory=True)
            name = 'admission-' + os.urandom(16).hex()
            os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)
            slot = control.backups / name
            slotfd = stack.enter_context(fs._directory(slot))
            binding = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
                'file_plan_sha256': confirmation, 'file_transaction_sha256': f._sha(canonical_bytes(document)),
                'web_backup_sha256': control.profile()['web_backup']['manifest_sha256'],
                'gateway_release_sha256': control.profile()['gateway_release_sha256']}
            files._new(slotfd, 'attempt.json', p._json({'state': 'ADMISSION_STARTED', **binding}))
            fence = stack.enter_context(c.rf.acquire(runtime, source, database, ca, authority, cancel=cancel, commit=commit))
            fence.assert_held()
            recheck = c._recheck(runtime, source, database, ca, authority, slot, archives.sql, cancel, commit=commit)
            result = {'state': 'CURRENT_SQL_FILES_OBSERVED_ACTIVITY_CLOSED', **binding,
                'observation_id': name, 'sql_recheck': recheck, 'sql_read_fence_max_seconds': 180,
                'live_sql_read_fence_required': True, 'valid_after_window_close': False,
                'historical_observation_only': True, 'archives_and_live_files_verified': True,
                'sql_logical_digest_verified': True, 'activity_resumed': False, 'services_started': False,
                'data_access_reopened': False, 'external_paths_released': False, 'phase6_complete': False}
            window = AdmissionWindow(control, fence, schedulers, archives, envelope, slot, result)
            window.assert_held()
            files._new(slotfd, 'observed.json', p._json(result))
            try:
                yield window
                window.assert_held()
            finally: window._closed = True
    except AdmissionError: raise
    except Exception: raise AdmissionError('MOBILE_ADMISSION_UNAVAILABLE') from None
    finally:
        if window is not None: window._closed = True
