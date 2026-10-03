"""Live SQL/file admission across journalled external-reservation release.

Private native composition. Every apply/resume/check creates a fresh SQL export;
no historical observation authorizes a new window. Data and activity stay closed.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import os
import time
from pathlib import Path

from installer import mobile_reopen_admission as a, mobile_reopen_external as e
from installer.engine import TransactionEngine
from installer.operations import OperationRegistry
from installer.transaction import StateJournal

r, fs, files, f, p = a.r, a.fs, a.files, a.f, a.p
require, AdmissionError = a.require, a.AdmissionError


class _ParentFiles:
    """Read-only view of the old journal, not a fabricated ReopenFilesPlan lease."""
    _read = r.ReopenFilesPlan._read
    profile = r.ReopenFilesPlan.profile
    _raw = r.ReopenFilesPlan._raw

    def __init__(self, external, barrier, data, gateway):
        require(type(external) is e.ExternalReleasePlan and type(barrier) is r.hd.HttpDrainLease
            and type(data) is r.da.DataAccessFence and type(gateway) is r.GatewayServiceRuntime,
            'MOBILE_EXTERNAL_INPUT_REJECTED')
        self.external, self.barrier, self.data, self.gateway = external, barrier, data, gateway
        self.lease, self.backups, self.root = external.lease, external.backups, external.source_root
        require(barrier._lease is self.lease and data._lease is self.lease
            and data._runtime is barrier._drain.runtime and gateway.web is data._runtime,
            'MOBILE_EXTERNAL_INPUT_REJECTED')
        self.journal = StateJournal(self.root / 'transaction/state.json')
        self._guard = r.guard.recover(self.lease, plan_sha256=external.value['source_plan_sha256'], confirmed=True)

    def engine(self):
        # Rebuild exactly the existing specs solely to validate the old receipt
        # identities. No old apply/resume/live path is called through this view.
        previous = None; operations = []
        for role in r.ROLES:
            operation = r.FileRelease(self, role, previous)
            operations.append(operation); previous = operation.spec.name
        engine = TransactionEngine(self.journal, OperationRegistry(tuple(operations)))
        engine._compatible(engine.report())
        return engine

    def _configuration_rows(self, locked):
        require(type(locked) is e._ConfigurationGuard and locked._plan is self.external,
                'MOBILE_EXTERNAL_CONFIGURATION_REQUIRED')
        locked.assert_held()
        # During a partial native release no old shared ConfigurationLease can
        # honestly be acquired. Observe its full original inode inventory under
        # the real exclusive locks instead, using the qualified entry reader.
        with fs._directory(self.lease.scope.directory.parent) as root:
            mount = r.inf._ext4(root)
            names = r.cf._names(root)
            gate = os.stat('maintenance', dir_fd=root, follow_symlinks=False)
            opened = os.fstat(self.lease._directory)
            require((gate.st_dev, gate.st_ino) == (opened.st_dev, opened.st_ino))
            rows = [r.cf._entry(root, '.', self.lease, mount)]
            total = 0; deadline = time.monotonic() + r.inf.MAX_SECONDS
            for name in names:
                if name == 'maintenance': continue
                require(time.monotonic() < deadline, 'MOBILE_EXTERNAL_FILES_LIMIT')
                fd = os.open(name, files.REGULAR, dir_fd=root)
                try:
                    row = r.cf._entry(fd, name, self.lease, mount)
                    total += row['bytes']; require(total <= r.cf.MAX_TOTAL_BYTES, 'MOBILE_EXTERNAL_FILES_LIMIT')
                    named = os.stat(name, dir_fd=root, follow_symlinks=False)
                    require((named.st_dev, named.st_ino) == (row['device'], row['inode']))
                    rows.append(row)
                finally: os.close(fd)
            require(names == r.cf._names(root))
            require(a.canonical_bytes(r.cf._value(self.lease, rows)) == self._raw('configuration'),
                    'MOBILE_EXTERNAL_FILES_CHANGED')
        locked.assert_held()

    def live(self, *, locked=None, configuration=None):
        self.external._held(); self.barrier.assert_held(); self.data.assert_held(); self._guard.assert_held()
        profile = a.strict_json_loads(self.barrier._profile)
        require('public_ingress' not in profile and 'foundation' in profile and 'gateway_service' in profile
            and self.barrier._drain.cleaner is not None, 'MOBILE_EXTERNAL_PROFILE_REJECTED')
        with fs._directory(self.data._runtime.spec.root.parent) as root:
            for name in ('boot', 'public'): fs._absent(root, name)
        released = r.gateway.recover(self.gateway, self.barrier, self.backups, confirmed=True).report()
        parent = self.profile()
        require(parent['instance'] == self.lease.scope.instance and parent['lease_id'] == self.lease.lease_id
            and parent['backup_root'] == str(self.backups)
            and parent['service_profile_sha256'] == f._sha(self.barrier._profile)
            and parent['gateway_release_sha256'] == f._sha(a.canonical_bytes(released)))
        rows = r.inf._walk(self.data, closed=False)
        require(a.canonical_bytes(r.inf._value(self.data, rows)) == self._raw('data'), 'MOBILE_EXTERNAL_FILES_CHANGED')
        with fs._directory(self.data._runtime.spec.webroot) as root:
            rows = r.wf._walk(self.barrier, root, closed=False)
            require(a.canonical_bytes(r.wf._value(self.barrier, rows)) == self._raw('web'), 'MOBILE_EXTERNAL_FILES_CHANGED')
        require((locked is None) != (configuration is None), 'MOBILE_EXTERNAL_CONFIGURATION_REQUIRED')
        if locked is not None: self._configuration_rows(locked)
        else:
            require(type(configuration) is r.cf.admission.ConfigurationLease and configuration._external is None,
                    'MOBILE_EXTERNAL_CONFIGURATION_REQUIRED')
            configuration.assert_held()
            with fs._directory(self.lease.scope.directory.parent) as root:
                rows = r.cf._walk(self.lease, configuration, root, closed=False)
                require(a.canonical_bytes(r.cf._value(self.lease, rows)) == self._raw('configuration'),
                        'MOBILE_EXTERNAL_FILES_CHANGED')
        self.lease.assert_held()


def _external_state(external, *, completed=False):
    """Allow only the exact native transition backed by the outer intent."""
    external._held()
    raw, value = external._external()
    intent, receipt = external._read('intent.json'), external._read('released.json')
    require(intent in (None, external._owner()) and receipt in (None, external._receipt()),
            'MOBILE_EXTERNAL_JOURNAL_CHANGED')
    require(receipt is None or intent is not None, 'MOBILE_EXTERNAL_JOURNAL_CHANGED')
    marker = r._optional(external.lease._directory, e.ef.MARKER, e.ef.MAX_JOURNAL)
    release = r._optional(external.lease._directory, e.ef.RELEASE, e.ef.MAX_JOURNAL)
    require(marker in (None, raw) and release in (None, raw), 'MOBILE_EXTERNAL_JOURNAL_CHANGED')
    if completed or receipt is not None:
        require(intent is not None and receipt is not None, 'MOBILE_EXTERNAL_RECEIPT_REQUIRED')
        external._absent()
    elif intent is None or (marker is not None and release is None):
        require(marker == raw and release is None, 'MOBILE_EXTERNAL_INTENT_REQUIRED')
        e.ef.assert_reservation(external.lease, raw)
    else:
        # Interrupted removal must still point to the original reserved inodes.
        for entry in value['entries']:
            with e.ef._parent(entry) as parent, e.ef._stage(entry, parent, released=True): pass
        if marker is None and release is None: external._absent()
    return raw, marker, release


def _transition_envelope(archives, external, *, completed=False):
    raw, marker, release = _external_state(external, completed=completed)
    removed, added = dict(archives.removed), dict(archives.added)
    # Validate the archived original even when the live marker is still present.
    removed[e.ef.MARKER] = raw
    if marker is not None: added[e.ef.MARKER] = marker
    if release is not None: added[e.ef.RELEASE] = release
    return a._envelope(archives.manifest['files'], removed, added)


class ExternalAdmissionWindow:
    def __init__(self, control, fence, schedulers, configuration, archives, envelope, slot, report):
        self._control, self._fence, self._schedulers = control, fence, schedulers
        self._configuration, self._archives, self._envelope = configuration, archives, envelope
        self._slot, self._report, self._pid, self._closed = slot, report, os.getpid(), False

    def __repr__(self): return '<ExternalAdmissionWindow private live SQL and released reservations>'
    def __reduce__(self): raise TypeError('External admission windows cannot be serialized')

    def assert_held(self):
        try:
            require(not self._closed and self._pid == os.getpid(), 'MOBILE_EXTERNAL_WINDOW_CLOSED')
            self._fence.assert_held(); self._schedulers.assert_held()
            self._control.live(configuration=self._configuration)
            self._archives.expected = _transition_envelope(self._archives, self._control.external, completed=True)
            self._envelope(); self._archives.check(); self._fence.assert_held()
        except AdmissionError: raise
        except Exception: raise AdmissionError('MOBILE_EXTERNAL_ADMISSION_UNAVAILABLE') from None

    def report(self):
        self.assert_held()
        return deepcopy(self._report)


@contextmanager
def acquire(external, barrier, data, gateway, runtime, source, payload, authority, confirmation, *,
            action, confirmed, allow_global_read_lock, cancel=None):
    """Fresh native apply/resume/check; historical receipts never skip SQL export."""
    window = None
    try:
        require(confirmed is True and allow_global_read_lock is True, 'MOBILE_EXTERNAL_CONSENT_REQUIRED')
        require(type(external) is e.ExternalReleasePlan and type(runtime) is p.PhpRuntime
            and type(authority) is a.c.d.SqlAuthorityCredentials and isinstance(source, Path)
            and type(payload) is dict and payload.get('mode') == 'upgrade'
            and payload.get('assistant') == {'action': 'preserve'}, 'MOBILE_EXTERNAL_INPUT_REJECTED')
        require(action in ('apply', 'resume', 'check') and confirmation == external.plan_sha256,
                'MOBILE_EXTERNAL_CONFIRMATION_REQUIRED')
        require(cancel is None or not cancel.is_set(), 'MOBILE_EXTERNAL_INTERRUPTED')
        control = _ParentFiles(external, barrier, data, gateway)
        document = control.journal.read(); control.engine()
        lease = control.lease; value = deepcopy(payload); config = f._configuration(value, fresh=False)
        with ExitStack() as stack:
            gid, web, directory, conf, webfd, inc = f._open(runtime, config, lease.scope.directory.parent.parent, stack)
            require(directory == lease.scope.directory.parent and gid == lease.scope.web_gid
                and web == data._runtime.spec.webroot, 'MOBILE_EXTERNAL_INSTANCE_MISMATCH')
            commit = data._runtime.source_commit
            current = f.FinalizationStep(runtime, source, repository=p.WEB_REPOSITORY, commit=commit)
            database, loader, ca = f._prepared(config, value, directory, conf, gid)
            completed = f._completed(conf, webfd, inc, gid, commit=commit)
            require(database['host'] == '127.0.0.1' and database['tls_required'] is False and ca is None
                and f._json_read(conf, 'state.json', gid)['migration_retained'] is False,
                'MOBILE_EXTERNAL_PROFILE_REJECTED')
            require(authority._user != database['user'] and authority._password != database['password'],
                    'MOBILE_EXTERNAL_ACCOUNT_SEPARATION_REQUIRED')
            def envelope():
                current._sources(web); current._pending_edits(conf)
                require(f._prepared(config, value, directory, conf, gid) == (database, loader, ca)
                    and f._completed(conf, webfd, inc, gid, commit=commit) == completed,
                    'MOBILE_EXTERNAL_ENVELOPE_CHANGED')
            schedulers = stack.enter_context(a.sa.acquire())
            # The old shared configuration context must already have exited.
            with external._configuration() as locked:
                control.live(locked=locked)
                intent, receipt = external._read('intent.json'), external._read('released.json')
                require((action == 'apply' and intent is None and receipt is None)
                    or (action == 'resume' and intent == external._owner())
                    or (action == 'check' and intent == external._owner() and receipt == external._receipt()),
                    'MOBILE_EXTERNAL_ACTION_REJECTED')
                archives = a._Archives(control, runtime, database, document, cancel)
                archives.expected = _transition_envelope(archives, external)
                envelope(); archives.check()
                parent = stack.enter_context(fs._directory(control.backups)); files._private(parent, directory=True)
                name = 'external-admission-' + os.urandom(16).hex()
                os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)
                slot = control.backups / name; slotfd = stack.enter_context(fs._directory(slot))
                binding = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
                    'external_plan_sha256': confirmation, 'file_plan_sha256': document['plan_sha256'],
                    'file_transaction_sha256': f._sha(a.canonical_bytes(document)),
                    'web_backup_sha256': control.profile()['web_backup']['manifest_sha256']}
                files._new(slotfd, 'attempt.json', p._json({'state': 'EXTERNAL_ADMISSION_STARTED', 'action': action, **binding}))
                fence = stack.enter_context(a.c.rf.acquire(runtime, source, database, ca, authority, cancel=cancel, commit=commit))
                fence.assert_held()
                recheck = a.c._recheck(runtime, source, database, ca, authority, slot, archives.sql, cancel, commit=commit)
                # Reobserve the complete native/files/archive state after export
                # and immediately before authorizing the external-only effect.
                fence.assert_held(); schedulers.assert_held(); control.live(locked=locked)
                archives.expected = _transition_envelope(archives, external)
                envelope(); archives.check(); locked.assert_held(); fence.assert_held()
                external._execute_locked(action, confirmation, confirmed=True, locked=locked)
                fence.assert_held()
                archives.expected = _transition_envelope(archives, external, completed=True)
                control.live(locked=locked); envelope(); archives.check()
            # A new honest shared ConfigurationLease checks actual external
            # absence. SQL remains locked through reacquisition and all checks.
            configuration = stack.enter_context(r.cf.admission.acquire(conf, web, gid))
            result = {'state': 'EXTERNAL_RELEASE_CURRENT_SQL_FILES_OBSERVED_ACTIVITY_CLOSED', **binding,
                'observation_id': name, 'sql_recheck': recheck, 'sql_read_fence_max_seconds': 180,
                'live_sql_read_fence_required': True, 'valid_after_window_close': False,
                'historical_observation_only': True, 'external_paths_released': True,
                'configuration_reacquired_without_reservations': True,
                'archives_and_live_files_verified': True, 'sql_logical_digest_verified': True,
                'data_access_reopened': False, 'services_started': False, 'activity_resumed': False,
                'phase6_complete': False}
            window = ExternalAdmissionWindow(control, fence, schedulers, configuration, archives, envelope, slot, result)
            window.assert_held(); files._new(slotfd, 'observed.json', p._json(result))
            try:
                yield window
                window.assert_held()
            finally: window._closed = True
    except AdmissionError: raise
    except Exception: raise AdmissionError('MOBILE_EXTERNAL_ADMISSION_UNAVAILABLE') from None
    finally:
        if window is not None: window._closed = True
