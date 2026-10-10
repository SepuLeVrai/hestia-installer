"""Recoverable handoff from mobile/Gateway blockers to one activation blocker.

No SQL authority, maintenance release or start exists in this module. The new
blocker is durable before either old blocker is removed. Effects are called only
by the separate native current-admission coordinator.
"""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import stat

from installer import mobile_resume_plan as p
from installer.model import canonical_bytes, strict_json_loads

m, e, r = p.m, p.e, p.m.r
fs, files, f = p.fs, p.files, p.f
MARKER = 'mobile-activation.attempt'
OLD = (e.guard.MARKER, r.gateway.RELEASED)
RECEIPTS = ('mobile-released.json', 'gateway-released.json')
MAX_RECORD = 8192


class BlockerError(RuntimeError): pass


def require(ok, code='MOBILE_BLOCKER_STATE_CHANGED'):
    if not ok: raise BlockerError(code)


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try: return operation(*args, **kwargs)
        except BlockerError: raise
        except Exception: raise BlockerError('MOBILE_BLOCKER_UNAVAILABLE') from None
    return invoke


def _identity(raw):
    value = strict_json_loads(raw)
    require(type(value) is dict and canonical_bytes(value) == raw)
    return value


def _read(root, name, maximum):
    return e._read_path(root, name, maximum)


class BlockerState:
    """Private read-only attachment until the admission module applies an effect."""
    def __init__(self, runtime, lease, backups, confirmation):
        require(type(confirmation) is str and e.guard.SHA256.fullmatch(confirmation),
                'MOBILE_BLOCKER_CONFIRMATION_REQUIRED')
        e._inputs(lease, backups, True)
        self.runtime, self.lease, self.backups = runtime, lease, backups
        self.root = backups / ('mobile-blockers-' + lease.lease_id)
        self.resume_root = backups / ('mobile-resume-' + lease.lease_id)
        self._pid = os.getpid(); self.confirmation = confirmation
        raw = _read(self.resume_root, 'plan.json', p.MAX_PLAN)
        require(f._sha(raw) == confirmation, 'MOBILE_BLOCKER_CONFIRMATION_REQUIRED')
        self.resume = p.ResumePlan(self.resume_root, raw)
        self.originals = {name: _read(self.resume_root, name, limit) for name, limit in zip(p.COPIES,
            (2048, e.guard.MAX_BYTES, r.gateway.g.MAX_JOURNAL * 2, 32768))}
        self.external = self.data = None
        self.static(); self.state()

    def __repr__(self): return '<BlockerState private maintenance-closed transition>'
    def __reduce__(self): raise TypeError('Blocker state cannot be serialized')

    @closed
    def static(self):
        require(self._pid == os.getpid(), 'MOBILE_BLOCKER_PROCESS_CHANGED')
        identity = e._inputs(self.lease, self.backups, True)
        account, runtime_profile = m.d._runtime(self.runtime, self.lease)
        raw = _read(self.resume_root, 'plan.json', p.MAX_PLAN)
        require(raw == self.resume._raw and f._sha(raw) == self.confirmation)
        value = _identity(raw)
        require(self.resume.value == value and self.resume.plan_sha256 == self.confirmation)
        definitions = (('maintenance.attempt', self.lease.scope.web_gid, 0o640),
            (OLD[0], 0, 0o600), (OLD[1], 0, 0o600),
            ('http-drain-' + self.lease.lease_id + '.attempt', self.lease.scope.web_gid, 0o640))
        metadata = {}
        for name, (source, gid, mode) in zip(p.COPIES, definitions):
            saved = _read(self.resume_root, name, len(self.originals[name]))
            require(saved == self.originals[name])
            metadata[name] = {'source': source, 'uid': 0, 'gid': gid, 'mode': mode,
                              'bytes': len(saved), 'sha256': f._sha(saved)}
        require(self.originals[p.COPIES[0]] == self.lease._raw)
        require(f._read(self.lease._directory, definitions[3][0], self.lease.scope.web_gid)
                == self.originals[p.COPIES[3]])
        profile = strict_json_loads(self.originals[p.COPIES[3]])
        source_root = self.lease.scope.directory / 'mobile-reopen-files'
        parents = {name: f._sha(_read(source_root, name, limit)) for name, limit in e.SOURCE_FILES.items()}
        parents.update(mobile_guard=f._sha(self.originals[p.COPIES[1]]),
                       gateway_release=f._sha(self.originals[p.COPIES[2]]))
        ext_raw = _read(self.backups / ('external-release-' + self.lease.lease_id), 'plan.json', e.MAX_PLAN)
        data_raw = _read(self.backups / ('data-release-' + self.lease.lease_id), 'plan.json', m.d.MAX_PLAN)
        external = e.ExternalReleasePlan(self.lease, self.backups, ext_raw)
        data = m.d.DataReleasePlan(self.runtime, self.lease, self.backups, data_raw)
        common = {'instance': self.lease.scope.instance, 'lease_id': self.lease.lease_id,
                  'backup_root': str(self.backups), 'backup_identity': identity, 'parents': parents}
        require(all(external.value[k] == v and data.value[k] == v for k, v in common.items()))
        # Compare the canonical persisted representation: ProxyIngress contains
        # tuples in Python and arrays after the exact JSON round trip.
        require(canonical_bytes(data.value['runtime']) == canonical_bytes(runtime_profile)
            and data.value['external_plan_sha256'] == external.plan_sha256)
        require(external._read('intent.json') == external._owner()
            and external._read('released.json') == external._receipt()
            and data._read('intent.json') == data._owner() and data._read('released.json') == data._receipt())
        if self.external is None:
            self.external, self.data = external, data
        else:
            require(self.external._raw == ext_raw and self.data._raw == data_raw)
        document = e.StateJournal(source_root / 'transaction/state.json').read()
        require(document is not None and document['state'] == 'DONE'
            and document['plan_sha256'] == external.value['source_plan_sha256']
            and len(document['steps']) == 3 and all(row['state'] == 'DONE' for row in document['steps']))
        receipt = _identity(self.originals[p.COPIES[2]])
        intent = receipt['intent']; fence = intent['fence']
        require(self.originals[p.COPIES[2]] == r.gateway._receipt(canonical_bytes(intent))
            and set(intent) == {'version', 'fence', 'snapshot_sha256', 'composed_sha256', 'web_verified_sha256'}
            and type(intent['version']) is int and intent['version'] == 1
            and fence['instance'] == self.lease.scope.instance and fence['lease_id'] == self.lease.lease_id)
        bindings = {'gateway_release_sha256': parents['gateway_release'],
            'gateway_snapshot_sha256': intent['snapshot_sha256'], 'composed_backup_sha256': intent['composed_sha256'],
            'web_backup_sha256': intent['web_verified_sha256'], 'service_profile_sha256': fence['barrier_sha256']}
        require(all(type(v) is str and e.guard.SHA256.fullmatch(v) for v in bindings.values()))
        guard = {'version': 1, 'state': 'MOBILE_REOPEN_BLOCKED', 'instance': self.lease.scope.instance,
            'lease_id': self.lease.lease_id, 'plan_sha256': external.value['source_plan_sha256'], 'bindings': bindings,
            'activity_resumed': False, 'admission_verified': False, 'services_started': False}
        require(canonical_bytes(guard) == self.originals[p.COPIES[1]])
        expected = {'version': 1, 'policy': p.POLICY, **common, 'data_plan_sha256': data.plan_sha256,
            'external_plan_sha256': external.plan_sha256, 'file_plan_sha256': external.value['source_plan_sha256'],
            'originals': metadata, 'service_profile_sha256': f._sha(self.originals[p.COPIES[3]]),
            'start_order': p._services(profile, self.lease.scope.instance), 'future_blocker_order': list(OLD),
            'maintenance_last': True, 'automatic_start_retry_allowed': False}
        require(canonical_bytes(expected) == raw)
        with self.resume._slot() as fd: self.resume._records(fd, self.originals, complete=True)
        for module in r.MODULES.values():
            fs._absent(self.lease._directory, module.MARKER); fs._absent(self.lease._directory, module.RELEASE)
        for name in (r.gateway.g.MARKER, r.gateway.RELEASE, m.d.da.MARKER): fs._absent(self.lease._directory, name)
        self.external._absent()
        from installer.gateway_public_admission import require_closed_paths
        require_closed_paths(self.runtime, self.lease)
        original = _read(source_root, 'data-access-original.json', 2048)
        with fs._directory(self.runtime.spec.root / 'data') as fd:
            info = os.fstat(fd); fs._no_acl(fd)
            expected = {'version': 1, 'instance': self.lease.scope.instance, 'lease_id': self.lease.lease_id,
                'root': str(self.runtime.spec.root / 'data'), 'device': info.st_dev, 'inode': info.st_ino,
                'gid': account.pw_gid, 'open_mode': 0o750, 'closed_mode': 0o700}
            require(original == canonical_bytes(expected) and info.st_uid == 0 and info.st_gid == account.pw_gid
                and stat.S_IMODE(info.st_mode) == 0o750)
        self.lease.assert_held()
        return account

    def owner(self):
        return canonical_bytes({'version': 1, 'instance': self.lease.scope.instance, 'lease_id': self.lease.lease_id,
            'resume_plan_sha256': self.confirmation, 'mobile_guard_sha256': f._sha(self.originals[p.COPIES[1]]),
            'gateway_release_sha256': f._sha(self.originals[p.COPIES[2]])})

    def activation(self):
        return canonical_bytes({'owner': _identity(self.owner()), 'state': 'MOBILE_ACTIVATION_BLOCKED',
            'maintenance_released': False, 'services_started': False, 'current_sql_admission': False})

    def receipt(self, index):
        return canonical_bytes({'owner': _identity(self.owner()), 'removed': OLD[index],
                                'activation_blocker_sha256': f._sha(self.activation())})

    def done(self):
        return canonical_bytes({'owner': _identity(self.owner()), 'state': 'OLD_BLOCKERS_REPLACED_ACTIVITY_CLOSED',
            'activation_blocker_sha256': f._sha(self.activation()), 'maintenance_released': False,
            'services_started': False, 'current_sql_admission': False})

    @contextmanager
    def slot(self, *, create=False):
        with fs._directory(self.backups) as parent:
            files._private(parent, directory=True)
            if create:
                try: os.stat(self.root.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError: os.mkdir(self.root.name, 0o700, dir_fd=parent); os.fsync(parent)
            with fs._directory(self.root) as fd:
                files._private(fd, directory=True)
                require(set(os.listdir(fd)) <= {'intent.json', 'done.json', *RECEIPTS}, 'MOBILE_BLOCKER_FOREIGN_RECORD')
                yield fd

    @closed
    def state(self):
        require(self._pid == os.getpid(), 'MOBILE_BLOCKER_PROCESS_CHANGED'); self.lease.assert_held()
        try:
            with self.slot() as fd:
                saved = {name: r._optional(fd, name, MAX_RECORD) for name in ('intent.json', 'done.json', *RECEIPTS)}
        except FileNotFoundError:
            # Only an absent directory is a new transition; a missing record in
            # an existing directory is handled by its explicit state below.
            require(not self.root.exists(), 'MOBILE_BLOCKER_STATE_CHANGED')
            saved = {name: None for name in ('intent.json', 'done.json', *RECEIPTS)}
        marker = r._optional(self.lease._directory, MARKER, MAX_RECORD)
        old = [r._optional(self.lease._directory, name, limit) for name, limit in zip(OLD,
            (e.guard.MAX_BYTES, r.gateway.g.MAX_JOURNAL * 2))]
        require(marker in (None, self.activation()) and saved['intent.json'] in (None, self.owner())
            and saved['done.json'] in (None, self.done()))
        for i, name in enumerate(RECEIPTS):
            require(old[i] in (None, self.originals[p.COPIES[i + 1]]) and saved[name] in (None, self.receipt(i)))
        intent = saved['intent.json'] is not None
        receipts = [saved[name] is not None for name in RECEIPTS]
        require(intent or marker is None and not any(receipts) and saved['done.json'] is None and all(x is not None for x in old),
                'MOBILE_BLOCKER_INTENT_REQUIRED')
        require(not intent or marker is not None or all(x is not None for x in old) and not any(receipts)
            and saved['done.json'] is None, 'MOBILE_ACTIVATION_BLOCKER_REQUIRED')
        require(not receipts[0] or old[0] is None)
        require(old[1] is not None or old[0] is None and receipts[0], 'MOBILE_BLOCKER_ORDER_CHANGED')
        require(not receipts[1] or receipts[0] and old[1] is None)
        require(saved['done.json'] is None or all(receipts) and marker is not None and all(x is None for x in old))
        return {'intent': intent, 'activation': marker is not None, 'present': [x is not None for x in old],
                'receipts': receipts, 'done': saved['done.json'] is not None}

    @closed
    def validate_action(self, action):
        state = self.state()
        require(action in ('apply', 'resume', 'check'), 'MOBILE_BLOCKER_ACTION_REJECTED')
        require((action == 'apply' and not state['intent']) or (action == 'resume' and state['intent'])
            or (action == 'check' and state['done']), 'MOBILE_BLOCKER_ACTION_REJECTED')
        return state

    def _save(self, name, raw):
        with self.slot(create=True) as fd:
            previous = r._optional(fd, name, MAX_RECORD)
            if previous is None: files._new(fd, name, raw)
            else: require(previous == raw)

    @closed
    def _execute(self, action, window):
        from installer.mobile_blocker_admission import BlockerWindow
        require(type(window) is BlockerWindow and window._control.state is self,
                'MOBILE_BLOCKER_LIVE_ADMISSION_REQUIRED')
        boundary = window.boundary
        boundary(); state = self.validate_action(action)
        if action == 'check': return self.report()
        if not state['intent']: self._save('intent.json', self.owner())
        boundary(); state = self.state()
        if not state['activation']: files._new(self.lease._directory, MARKER, self.activation())
        for index, name in enumerate(OLD):
            boundary(); state = self.state()
            if state['present'][index]:
                os.unlink(name, dir_fd=self.lease._directory)
            # fsync is required even if the prior unlink's response was lost.
            os.fsync(self.lease._directory); boundary()
            self._save(RECEIPTS[index], self.receipt(index)); self.state()
        self._save('done.json', self.done()); boundary()
        require(self.state()['done'])
        return self.report()

    @closed
    def report(self):
        state = self.state()
        return {'state': 'OLD_BLOCKERS_REPLACED_ACTIVITY_CLOSED' if state['done'] else 'BLOCKER_TRANSITION_PENDING',
            'resume_plan_sha256': self.confirmation, 'old_blockers_removed': state['done'],
            'activation_blocker_kept': state['activation'], 'maintenance_released': False,
            'services_started': False, 'current_sql_admission': False, 'phase6_complete': False}
