"""Fresh SQL/archives/runtime admission during the recoverable blocker handoff.

The maintenance lease and the replacement activation blocker remain mandatory.
No old reader is asked to accept a missing marker and no service is controlled.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import os
from pathlib import Path
import stat
import time

from installer import mobile_blocker_state as s

m, e, r = s.m, s.e, s.r
a, b = m.a, m.b
fs, files, f, p = s.fs, s.files, s.f, m.p
require, BlockerError = s.require, s.BlockerError


def _data_rows(state, account):
    with fs._directory(state.runtime.spec.root / 'data') as root:
        mount = r.inf._ext4(root); device = os.fstat(root).st_dev
        deadline = time.monotonic() + r.inf.MAX_SECONDS; records, seen = [], set()
        def visit(fd, relative, depth):
            require(depth <= 64 and len(relative.encode()) <= 2048 and len(records) < r.inf.MAX_ENTRIES
                and time.monotonic() < deadline, 'MOBILE_BLOCKER_FILES_LIMIT')
            row = r.inf._entry(fd, relative, account, device, mount)
            key = row['device'], row['inode']
            require(key not in seen and not row['flags'] & r.inf.IMMUTABLE, 'MOBILE_BLOCKER_FILES_CHANGED')
            seen.add(key); records.append(row)
            if row['kind'] != 'directory': return
            names = r.inf._names(fd)
            require(len(names) <= r.inf.MAX_ENTRIES - len(records), 'MOBILE_BLOCKER_FILES_LIMIT')
            for name in names:
                files._name(name); before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode), 'MOBILE_BLOCKER_FILES_CHANGED')
                child = os.open(name, files.DIRECTORY if stat.S_ISDIR(before.st_mode) else files.REGULAR, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), 'MOBILE_BLOCKER_FILES_CHANGED')
                    visit(child, name if relative == '.' else relative + '/' + name, depth + 1)
                    named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require((named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino), 'MOBILE_BLOCKER_FILES_CHANGED')
                finally: os.close(child)
            require(names == r.inf._names(fd), 'MOBILE_BLOCKER_FILES_CHANGED')
        visit(root, '.', 0)
    return records


class _ParentFiles:
    _read = r.ReopenFilesPlan._read
    profile = r.ReopenFilesPlan.profile
    _raw = r.ReopenFilesPlan._raw
    engine = b._ParentFiles.engine
    _configuration_rows = b._ParentFiles._configuration_rows

    def __init__(self, state, barrier, gateway):
        require(type(state) is s.BlockerState and type(barrier) is r.hd.HttpDrainLease
            and (gateway is None or type(gateway) is r.GatewayServiceRuntime), 'MOBILE_BLOCKER_INPUT_REJECTED')
        require(barrier._lease is state.lease and barrier._drain.runtime is state.runtime
            and (gateway is None or gateway.web is state.runtime), 'MOBILE_BLOCKER_INPUT_REJECTED')
        self.state, self.barrier, self.gateway = state, barrier, gateway
        self.lease, self.backups, self.external = state.lease, state.backups, state.external
        self.root = state.external.source_root
        self.journal = e.StateJournal(self.root / 'transaction/state.json')
        self.data = m._DataProfile(state.runtime, state.static())
        require(barrier._profile == state.originals[s.p.COPIES[3]], 'MOBILE_BLOCKER_PROFILE_CHANGED')

    def attach(self):
        if self.gateway is None: self.gateway = m.gd.attached(self.state.runtime, m.fd.attached(self.state.runtime))
        require(type(self.gateway) is r.GatewayServiceRuntime and self.gateway.web is self.state.runtime,
                'MOBILE_BLOCKER_GATEWAY_REQUIRED')

    def _gateway(self):
        # Observe current native Gateway bytes and its exact archived release.
        # Calling recover() here would require a marker intentionally consumed.
        release = r.gateway; raw = self.state.originals[s.p.COPIES[2]]
        receipt = s._identity(raw); intent_raw = s.canonical_bytes(receipt['intent'])
        fence_raw = s.canonical_bytes(receipt['intent']['fence'])
        release.g._inputs(self.gateway, self.barrier, True)
        with ExitStack() as stack:
            opened, records, _ = release.g._open(self.gateway, stack)
            release._observe(self.gateway, self.barrier, opened, records, fence_raw, released=True)
            _, expected = release._backup(self.backups, self.gateway, self.barrier, fence_raw,
                                          intent=s._identity(intent_raw))
            release._sources(opened, expected)
        return receipt

    def live(self, *, locked):
        account = self.state.static(); self.state.state(); self.barrier.assert_held()
        profile = s.strict_json_loads(self.barrier._profile)
        require('public_ingress' not in profile and 'foundation' in profile and 'gateway_service' in profile
            and self.barrier._drain.cleaner is not None, 'MOBILE_BLOCKER_PROFILE_REJECTED')
        released = self._gateway(); parent = self.profile()
        require(parent['instance'] == self.lease.scope.instance and parent['lease_id'] == self.lease.lease_id
            and parent['backup_root'] == str(self.backups)
            and parent['service_profile_sha256'] == f._sha(self.barrier._profile)
            and parent['gateway_release_sha256'] == f._sha(s.canonical_bytes(released)))
        rows = _data_rows(self.state, account)
        require(rows and rows[0]['path'] == '.' and rows[0]['mode'] == 0o750, 'MOBILE_BLOCKER_FILES_CHANGED')
        rows[0] = {**rows[0], 'mode': 0o700}
        original = self._read('data-access-original.json', 2048)
        observed = {'version': 1, 'instance': self.lease.scope.instance, 'lease_id': self.lease.lease_id,
            'root': str(self.state.runtime.spec.root / 'data'), 'data_fence_sha256': f._sha(original),
            'filesystem': 'ext4', 'entries': rows}
        require(s.canonical_bytes(observed) == self._raw('data'), 'MOBILE_BLOCKER_FILES_CHANGED')
        with fs._directory(self.state.runtime.spec.webroot) as root:
            rows = r.wf._walk(self.barrier, root, closed=False)
            require(s.canonical_bytes(r.wf._value(self.barrier, rows)) == self._raw('web'), 'MOBILE_BLOCKER_FILES_CHANGED')
        self._configuration_rows(locked); self.state.static(); self.state.state()


class _Archives(a._Archives):
    @staticmethod
    def _journal_changes(control, document):
        control.state.static(); control.state.state()
        return a._journal_records(control, document, control.state.originals[s.p.COPIES[2]],
                                  control.state.originals[s.p.COPIES[1]])


def _transition_envelope(archives, control):
    state = control.state.state()
    removed, added = dict(archives.removed), dict(archives.added)
    raw, _ = control.external._external(); removed[e.ef.MARKER] = raw
    for index, name in enumerate(s.OLD):
        require(added[name] == control.state.originals[s.p.COPIES[index + 1]])
        if not state['present'][index]: del added[name]
    if state['activation']: added[s.MARKER] = control.state.activation()
    expected = a._envelope(archives.manifest['files'], removed, added)
    original = control._read('data-access-original.json', 2048)
    key = 'configuration', 'maintenance/' + m.d.da.MARKER
    require(expected.pop(key, None) == m._data_record(original, control.data._account.pw_gid),
            'MOBILE_BLOCKER_ARCHIVED_MARKER_CHANGED')
    return expected


class BlockerWindow:
    def __init__(self, control, fence, schedulers, locked, archives, envelope, slot, report):
        self._control, self._fence, self._schedulers = control, fence, schedulers
        self._locked, self._archives, self._envelope = locked, archives, envelope
        self._slot, self._report, self._pid, self._closed = slot, report, os.getpid(), False

    def __repr__(self): return '<BlockerWindow private live SQL and closed activation>'
    def __reduce__(self): raise TypeError('Blocker windows cannot be serialized')

    def boundary(self):
        require(not self._closed and self._pid == os.getpid(), 'MOBILE_BLOCKER_WINDOW_CLOSED')
        self._fence.assert_held(); self._schedulers.assert_held(); self._locked.assert_held()
        self._control.state.static(); self._control.state.state(); self._fence.assert_held()

    @s.closed
    def assert_held(self):
        self.boundary(); self._control.live(locked=self._locked)
        self._archives.expected = _transition_envelope(self._archives, self._control)
        self._envelope(); self._archives.check(); self.boundary()

    def report(self):
        self.assert_held(); return deepcopy(self._report)


@contextmanager
def acquire(state, barrier, gateway, runtime, source, payload, authority, confirmation, *,
            action, confirmed, allow_global_read_lock, cancel=None):
    window = None
    try:
        require(confirmed is True and allow_global_read_lock is True, 'MOBILE_BLOCKER_CONSENT_REQUIRED')
        require(type(state) is s.BlockerState and type(runtime) is p.PhpRuntime
            and type(authority) is a.c.d.SqlAuthorityCredentials and isinstance(source, Path)
            and type(payload) is dict and payload.get('mode') == 'upgrade'
            and payload.get('assistant') == {'action': 'preserve'}, 'MOBILE_BLOCKER_INPUT_REJECTED')
        require(confirmation == state.confirmation, 'MOBILE_BLOCKER_CONFIRMATION_REQUIRED')
        require(cancel is None or not cancel.is_set(), 'MOBILE_BLOCKER_INTERRUPTED')
        state.static(); state.validate_action(action)
        control = _ParentFiles(state, barrier, gateway); document = control.journal.read(); control.engine()
        lease = control.lease; value = deepcopy(payload); config = f._configuration(value, fresh=False)
        with ExitStack() as stack:
            gid, web, directory, conf, webfd, inc = f._open(runtime, config, lease.scope.directory.parent.parent, stack)
            require(directory == lease.scope.directory.parent and gid == lease.scope.web_gid
                and web == state.runtime.spec.webroot, 'MOBILE_BLOCKER_INSTANCE_MISMATCH')
            current = f.FinalizationStep(runtime, source, repository=p.WEB_REPOSITORY, commit=a.STORAGE_COMMIT)
            database, loader, ca = f._prepared(config, value, directory, conf, gid)
            completed = f._completed(conf, webfd, inc, gid, commit=a.STORAGE_COMMIT)
            require(database['host'] == '127.0.0.1' and database['tls_required'] is False and ca is None
                and f._json_read(conf, 'state.json', gid)['migration_retained'] is False,
                'MOBILE_BLOCKER_PROFILE_REJECTED')
            require(authority._user != database['user'] and authority._password != database['password'],
                    'MOBILE_BLOCKER_ACCOUNT_SEPARATION_REQUIRED')
            def envelope():
                current._sources(web); current._pending_edits(conf)
                require(f._prepared(config, value, directory, conf, gid) == (database, loader, ca)
                    and f._completed(conf, webfd, inc, gid, commit=a.STORAGE_COMMIT) == completed,
                    'MOBILE_BLOCKER_ENVELOPE_CHANGED')
            schedulers = stack.enter_context(a.sa.acquire())
            with control.external._configuration() as locked:
                control.attach(); control.live(locked=locked)
                archives = _Archives(control, runtime, database, document, cancel)
                archives.expected = _transition_envelope(archives, control); envelope(); archives.check()
                parent = stack.enter_context(fs._directory(control.backups)); files._private(parent, directory=True)
                name = 'blocker-admission-' + os.urandom(16).hex()
                os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)
                slot = control.backups / name; slotfd = stack.enter_context(fs._directory(slot))
                binding = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
                    'resume_plan_sha256': confirmation, 'file_plan_sha256': document['plan_sha256'],
                    'web_backup_sha256': control.profile()['web_backup']['manifest_sha256']}
                files._new(slotfd, 'attempt.json', p._json({'state': 'BLOCKER_ADMISSION_STARTED', 'action': action, **binding}))
                with a.c.rf.acquire(runtime, source, database, ca, authority, cancel=cancel) as fence:
                    fence.assert_held()
                    recheck = a.c._recheck(runtime, source, database, ca, authority, slot, archives.sql, cancel)
                    window = BlockerWindow(control, fence, schedulers, locked, archives, envelope, slot, {})
                    window.assert_held()  # Full current native admission before the first durable intent.
                    state._execute(action, window)
                    window.assert_held()  # Full current admission after the exact allowed marker transition.
                    result = {'state': 'BLOCKERS_REPLACED_CURRENT_SQL_FILES_OBSERVED_ACTIVITY_CLOSED', **binding,
                        'observation_id': name, 'sql_recheck': recheck, 'sql_read_fence_max_seconds': 180,
                        'live_sql_read_fence_required': True, 'valid_after_window_close': False,
                        'historical_observation_only': True, 'old_blockers_removed': True,
                        'activation_blocker_kept': True, 'configuration_exclusive_through_window': True,
                        'archives_and_live_files_verified': True, 'sql_logical_digest_verified': True,
                        'maintenance_released': False, 'services_started': False, 'activity_resumed': False,
                        'phase6_complete': False}
                    window._report = result; files._new(slotfd, 'observed.json', p._json(result))
                    try:
                        yield window
                        window.assert_held()
                    finally: window._closed = True
    except BlockerError: raise
    except Exception: raise BlockerError('MOBILE_BLOCKER_ADMISSION_UNAVAILABLE') from None
    finally:
        if window is not None: window._closed = True
