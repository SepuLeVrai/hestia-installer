"""Current SQL/file admission across recoverable data access reopening.

Private, process-bound observation only. Exact journal transitions are required;
maintenance/mobile/Gateway blockers remain. No service-start authority is issued.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time

from installer import mobile_reopen_data as d, mobile_external_admission as b
from installer import foundation_drain as fd, gateway_service_drain as gd

a, r, e = b.a, b.r, b.e
fs, files, f, p = b.fs, b.files, b.f, b.p
require, AdmissionError = b.require, b.AdmissionError


def _state(plan, *, completed=False):
    _, account, marker, mode = plan._held()
    intent, receipt = plan._read('intent.json'), plan._read('released.json')
    require(intent in (None, plan._owner()) and receipt in (None, plan._receipt()),
            'MOBILE_DATA_JOURNAL_CHANGED')
    require(receipt is None or intent is not None, 'MOBILE_DATA_INTENT_REQUIRED')
    require(intent is not None or marker is not None and mode == 0o700, 'MOBILE_DATA_INTENT_REQUIRED')
    if completed or receipt is not None:
        require(intent is not None and receipt is not None and marker is None and mode == 0o750,
                'MOBILE_DATA_RECEIPT_REQUIRED')
    return account, marker, mode, intent, receipt


def _action(plan, action):
    _, marker, mode, intent, receipt = _state(plan, completed=action == 'check')
    require((action == 'apply' and intent is None and receipt is None and marker is not None and mode == 0o700)
        or (action == 'resume' and intent == plan._owner())
        or (action == 'check' and receipt == plan._receipt()), 'MOBILE_DATA_ACTION_REJECTED')
    return marker, mode


def _prepare(plan, action, locked):
    """Explicit resume may reclose a partial chmod; never opens or deletes data."""
    require(type(locked) is e._ConfigurationGuard and locked._plan.lease is plan.lease
        and locked._plan.backups == plan.backups
        and locked._plan.plan_sha256 == plan.value['external_plan_sha256'], 'MOBILE_DATA_CONFIGURATION_REQUIRED')
    locked.assert_held(); marker, mode = _action(plan, action)
    reclosed = marker is not None and mode == 0o750
    if reclosed:
        require(action == 'resume', 'MOBILE_DATA_ACTION_REJECTED')
        # This safe preparation precedes a fresh SQL export. Failure leaves
        # closure in place; only the later admitted effect may reopen it.
        with d.da.recover(plan.runtime, plan.lease, confirmed=True): pass
    _action(plan, action); locked.assert_held()
    return reclosed


def _data_rows(plan, account):
    """Read-only bounded inode census; no fabricated DataAccessFence or old walk."""
    _state(plan)
    with fs._directory(plan.runtime.spec.root / 'data') as root:
        mount = r.inf._ext4(root); device = os.fstat(root).st_dev
        deadline = time.monotonic() + r.inf.MAX_SECONDS; records, seen = [], set()
        def visit(fd, relative, depth):
            require(depth <= 64 and len(relative.encode()) <= 2048 and len(records) < r.inf.MAX_ENTRIES
                and time.monotonic() < deadline, 'MOBILE_DATA_FILES_LIMIT')
            row = r.inf._entry(fd, relative, account, device, mount)
            key = row['device'], row['inode']
            require(key not in seen and not row['flags'] & r.inf.IMMUTABLE, 'MOBILE_DATA_FILES_CHANGED')
            seen.add(key); records.append(row)
            if row['kind'] != 'directory': return
            names = r.inf._names(fd)
            require(len(names) <= r.inf.MAX_ENTRIES - len(records), 'MOBILE_DATA_FILES_LIMIT')
            for name in names:
                files._name(name); before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode), 'MOBILE_DATA_FILES_CHANGED')
                child = os.open(name, files.DIRECTORY if stat.S_ISDIR(before.st_mode) else files.REGULAR, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), 'MOBILE_DATA_FILES_CHANGED')
                    visit(child, name if relative == '.' else relative + '/' + name, depth + 1)
                    named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require((named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino), 'MOBILE_DATA_FILES_CHANGED')
                finally: os.close(child)
            require(names == r.inf._names(fd), 'MOBILE_DATA_FILES_CHANGED')
        visit(root, '.', 0)
    _state(plan)
    return records


@dataclass(frozen=True)
class _DataProfile:
    """Archive metadata only: not a lease, no descriptor or assert_held method."""
    _runtime: object
    _account: object


class _ParentFiles:
    _read = r.ReopenFilesPlan._read
    profile = r.ReopenFilesPlan.profile
    _raw = r.ReopenFilesPlan._raw
    engine = b._ParentFiles.engine
    _configuration_rows = b._ParentFiles._configuration_rows

    def __init__(self, plan, barrier, gateway):
        require(type(plan) is d.DataReleasePlan and type(barrier) is r.hd.HttpDrainLease
            and (gateway is None or type(gateway) is r.GatewayServiceRuntime), 'MOBILE_DATA_INPUT_REJECTED')
        require(barrier._lease is plan.lease and barrier._drain.runtime is plan.runtime
            and (gateway is None or gateway.web is plan.runtime), 'MOBILE_DATA_INPUT_REJECTED')
        self.plan, self.barrier, self.gateway = plan, barrier, gateway
        self.lease, self.backups = plan.lease, plan.backups
        self.external, account, _, _ = plan._held()
        self.data = _DataProfile(plan.runtime, account)
        self.root = self.external.source_root
        self.journal = e.StateJournal(self.root / 'transaction/state.json')
        self._guard = r.guard.recover(self.lease, plan_sha256=self.external.value['source_plan_sha256'], confirmed=True)

    def attach(self):
        # A cold restart may reconstruct the real runtimes only after explicit
        # reclosure has made the unchanged strict HTTP reader applicable again.
        if self.gateway is None: self.gateway = gd.attached(self.plan.runtime, fd.attached(self.plan.runtime))
        require(type(self.gateway) is r.GatewayServiceRuntime and self.gateway.web is self.plan.runtime,
                'MOBILE_DATA_GATEWAY_REQUIRED')

    def live(self, *, locked):
        account, marker, mode, _, _ = _state(self.plan)
        require(marker is None or mode == 0o700, 'MOBILE_DATA_RECLOSE_REQUIRED')
        self.barrier.assert_held(); self._guard.assert_held()
        profile = a.strict_json_loads(self.barrier._profile)
        require('public_ingress' not in profile and 'foundation' in profile and 'gateway_service' in profile
            and self.barrier._drain.cleaner is not None, 'MOBILE_DATA_PROFILE_REJECTED')
        released = r.gateway.recover(self.gateway, self.barrier, self.backups, confirmed=True).report()
        parent = self.profile()
        require(parent['instance'] == self.lease.scope.instance and parent['lease_id'] == self.lease.lease_id
            and parent['backup_root'] == str(self.backups)
            and parent['service_profile_sha256'] == f._sha(self.barrier._profile)
            and parent['gateway_release_sha256'] == f._sha(a.canonical_bytes(released)), 'MOBILE_DATA_PARENT_CHANGED')
        rows = _data_rows(self.plan, account)
        require(rows and rows[0]['path'] == '.' and rows[0]['mode'] == mode, 'MOBILE_DATA_FILES_CHANGED')
        # Only this bound root's 0700 -> 0750 transition differs from the original
        # inode inventory. Every descendant/flag/inode/mode remains exact.
        rows[0] = {**rows[0], 'mode': 0o700}
        original = self._read('data-access-original.json', 2048)
        observed = {'version': 1, 'instance': self.lease.scope.instance, 'lease_id': self.lease.lease_id,
            'root': str(self.plan.runtime.spec.root / 'data'), 'data_fence_sha256': f._sha(original),
            'filesystem': 'ext4', 'entries': rows}
        require(a.canonical_bytes(observed) == self._raw('data'), 'MOBILE_DATA_FILES_CHANGED')
        with fs._directory(self.plan.runtime.spec.webroot) as root:
            rows = r.wf._walk(self.barrier, root, closed=False)
            require(a.canonical_bytes(r.wf._value(self.barrier, rows)) == self._raw('web'), 'MOBILE_DATA_FILES_CHANGED')
        self._configuration_rows(locked)
        _state(self.plan); self.lease.assert_held()


def _data_record(raw, gid):
    return {**a._private_record(d.da.MARKER, raw), 'gid': gid, 'mode': 0o640}


def _transition_envelope(archives, control, *, completed=False):
    account, marker, _, _, _ = _state(control.plan, completed=completed)
    raw, _ = control.external._external()
    removed, added = dict(archives.removed), dict(archives.added)
    removed[e.ef.MARKER] = raw  # The external parent is complete, independently checked by _state.
    expected = a._envelope(archives.manifest['files'], removed, added)
    original = control._read('data-access-original.json', 2048)
    key = 'configuration', 'maintenance/' + d.da.MARKER
    require(expected.get(key) == _data_record(original, account.pw_gid), 'MOBILE_DATA_ARCHIVED_MARKER_CHANGED')
    if marker is None: del expected[key]
    else: require(marker == original, 'MOBILE_DATA_JOURNAL_CHANGED')
    return expected


class DataAdmissionWindow:
    def __init__(self, control, fence, schedulers, locked, archives, envelope, slot, report):
        self._control, self._fence, self._schedulers = control, fence, schedulers
        self._locked, self._archives, self._envelope = locked, archives, envelope
        self._slot, self._report, self._pid, self._closed = slot, report, os.getpid(), False

    def __repr__(self): return '<DataAdmissionWindow private live SQL and reopened data>'
    def __reduce__(self): raise TypeError('Data admission windows cannot be serialized')

    def assert_held(self):
        try:
            require(not self._closed and self._pid == os.getpid(), 'MOBILE_DATA_WINDOW_CLOSED')
            self._fence.assert_held(); self._schedulers.assert_held(); self._locked.assert_held()
            self._control.live(locked=self._locked)
            self._archives.expected = _transition_envelope(self._archives, self._control, completed=True)
            self._envelope(); self._archives.check(); self._locked.assert_held(); self._fence.assert_held()
        except AdmissionError: raise
        except Exception: raise AdmissionError('MOBILE_DATA_ADMISSION_UNAVAILABLE') from None

    def report(self):
        self.assert_held(); return deepcopy(self._report)


@contextmanager
def acquire(plan, barrier, gateway, runtime, source, payload, authority, confirmation, *,
            action, confirmed, allow_global_read_lock, cancel=None):
    """Fresh export before reopening; resume may explicitly reclose a partial chmod."""
    window = None
    try:
        require(confirmed is True and allow_global_read_lock is True, 'MOBILE_DATA_CONSENT_REQUIRED')
        require(type(plan) is d.DataReleasePlan and type(runtime) is p.PhpRuntime
            and type(authority) is a.c.d.SqlAuthorityCredentials and isinstance(source, Path)
            and type(payload) is dict and payload.get('mode') == 'upgrade'
            and payload.get('assistant') == {'action': 'preserve'}, 'MOBILE_DATA_INPUT_REJECTED')
        require(action in ('apply', 'resume', 'check') and confirmation == plan.plan_sha256,
                'MOBILE_DATA_CONFIRMATION_REQUIRED')
        require(cancel is None or not cancel.is_set(), 'MOBILE_DATA_INTERRUPTED')
        control = _ParentFiles(plan, barrier, gateway)
        _action(plan, action); document = control.journal.read(); control.engine()
        lease = control.lease; value = deepcopy(payload); config = f._configuration(value, fresh=False)
        with ExitStack() as stack:
            gid, web, directory, conf, webfd, inc = f._open(runtime, config, lease.scope.directory.parent.parent, stack)
            require(directory == lease.scope.directory.parent and gid == lease.scope.web_gid
                and web == plan.runtime.spec.webroot, 'MOBILE_DATA_INSTANCE_MISMATCH')
            commit = plan.runtime.source_commit
            current = f.FinalizationStep(runtime, source, repository=p.WEB_REPOSITORY, commit=commit)
            database, loader, ca = f._prepared(config, value, directory, conf, gid)
            completed = f._completed(conf, webfd, inc, gid, commit=commit)
            require(database['host'] == '127.0.0.1' and database['tls_required'] is False and ca is None
                and f._json_read(conf, 'state.json', gid)['migration_retained'] is False,
                'MOBILE_DATA_PROFILE_REJECTED')
            require(authority._user != database['user'] and authority._password != database['password'],
                    'MOBILE_DATA_ACCOUNT_SEPARATION_REQUIRED')
            def envelope():
                current._sources(web); current._pending_edits(conf)
                require(f._prepared(config, value, directory, conf, gid) == (database, loader, ca)
                    and f._completed(conf, webfd, inc, gid, commit=commit) == completed,
                    'MOBILE_DATA_ENVELOPE_CHANGED')
            schedulers = stack.enter_context(a.sa.acquire())
            with control.external._configuration() as locked:
                reclosed = _prepare(plan, action, locked)
                control.attach()
                control.live(locked=locked)
                archives = a._Archives(control, runtime, database, document, cancel)
                archives.expected = _transition_envelope(archives, control)
                envelope(); archives.check()
                parent = stack.enter_context(fs._directory(control.backups)); files._private(parent, directory=True)
                name = 'data-admission-' + os.urandom(16).hex()
                os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)
                slot = control.backups / name; slotfd = stack.enter_context(fs._directory(slot))
                binding = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
                    'data_plan_sha256': confirmation, 'external_plan_sha256': plan.value['external_plan_sha256'],
                    'file_plan_sha256': document['plan_sha256'],
                    'file_transaction_sha256': f._sha(a.canonical_bytes(document)),
                    'web_backup_sha256': control.profile()['web_backup']['manifest_sha256']}
                files._new(slotfd, 'attempt.json', p._json({'state': 'DATA_ADMISSION_STARTED', 'action': action,
                    'partial_chmod_explicitly_reclosed': reclosed, **binding}))
                with a.c.rf.acquire(runtime, source, database, ca, authority, cancel=cancel, commit=commit) as fence:
                    fence.assert_held()
                    recheck = a.c._recheck(runtime, source, database, ca, authority, slot, archives.sql, cancel, commit=commit)
                    fence.assert_held(); schedulers.assert_held(); control.live(locked=locked)
                    archives.expected = _transition_envelope(archives, control)
                    envelope(); archives.check(); locked.assert_held(); fence.assert_held()
                    plan._execute_locked(action, confirmation, confirmed=True, locked=locked)
                    fence.assert_held()
                    result = {'state': 'DATA_RELEASE_CURRENT_SQL_FILES_OBSERVED_ACTIVITY_CLOSED', **binding,
                        'observation_id': name, 'sql_recheck': recheck, 'sql_read_fence_max_seconds': 180,
                        'live_sql_read_fence_required': True, 'valid_after_window_close': False,
                        'historical_observation_only': True, 'external_paths_released': True,
                        'data_access_reopened': True, 'configuration_exclusive_through_window': True,
                        'partial_chmod_explicitly_reclosed': reclosed,
                        'archives_and_live_files_verified': True, 'sql_logical_digest_verified': True,
                        'services_started': False, 'activity_resumed': False, 'phase6_complete': False}
                    window = DataAdmissionWindow(control, fence, schedulers, locked, archives, envelope, slot, result)
                    window.assert_held(); files._new(slotfd, 'observed.json', p._json(result))
                    try:
                        yield window
                        window.assert_held()
                    finally: window._closed = True
    except AdmissionError: raise
    except Exception: raise AdmissionError('MOBILE_DATA_ADMISSION_UNAVAILABLE') from None
    finally:
        if window is not None: window._closed = True
