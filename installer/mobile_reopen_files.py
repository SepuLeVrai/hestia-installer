"""Private three-step release plan; activity, data access and external paths stay closed.

This is not SQL admission or a service-start plan. Original journals are retained
before any flag change. Only this plan's exact durable intent can reconcile the
old RELEASE-removed/MARKER-present cut; qualified low-level readers stay strict.
"""
from contextlib import contextmanager
from pathlib import Path
import os

from installer import backup_files as files, configuration_fence as cf
from installer import data_access as da, external_fence as ef, http_drain as hd
from installer import inode_fence as inf, web_fence as wf
from installer import gateway_state_release as gateway, mobile_reopen_guard as guard
from installer.engine import TransactionEngine
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import (ErrorCode, Receipt, StepSpec, canonical_bytes, exact_keys,
                             require, strict_json_loads)
from installer.operations import Operation, OperationContext, OperationRegistry, Recovery, RecoveryDecision
from installer.transaction import StateJournal, _private_directory

ROLES = ('data', 'configuration', 'web')
MODULES = {'data': inf, 'configuration': cf, 'web': wf}
fs, f = inf.fs, inf.f


def _optional(fd, name, limit):
    try: return files._read(fd, name, limit)
    except FileNotFoundError: return None


class FileRelease(Operation):
    def __init__(self, plan, role, previous):
        self.controller, self.role = plan, role
        self.binding = f._sha(canonical_bytes(plan.profile()))
        super().__init__(StepSpec(name='mobile-reopen-files.' + role,
            operation='mobile-reopen-files.' + role, module='gateway',
            boundary='mobile-reopen-files.' + role, rollback_supported=False,
            action='Retirer la protection immutable : ' + role,
            dependencies=(previous,) if previous else (), warnings=(
                'Profil lié : ' + self.binding,
                'La maintenance, les accès aux données et les réservations externes restent fermés.',
                'Ce plan ne certifie pas le SQL courant et ne démarre aucun service.')))

    def owner(self, context):
        require(context.spec == self.spec.as_dict(), ErrorCode.INCOMPATIBLE_STATE)
        return {'installation_id': context.installation_id, 'profile_sha256': self.binding,
                'spec_sha256': f._sha(canonical_bytes(context.spec))}

    def intent(self, context):
        value = self.controller._read(self.role + '-intent.json', 4096)
        if value is None: return False
        require(value == canonical_bytes(self.owner(context)), ErrorCode.INCOMPATIBLE_STATE)
        return True

    def receipt(self, context):
        return Receipt(hashes_non_secret=(('file-release-owner', f._sha(canonical_bytes(self.owner(context)))),))

    def prepare(self, context):
        self.controller._live()
        require(not self.intent(context), ErrorCode.MANUAL_ACTION_REQUIRED)
        self.controller._observe(self.role, closed=True, markers=True)

    def apply(self, context):
        self.prepare(context)
        self.controller._save(self.role + '-intent.json', canonical_bytes(self.owner(context)))
        self.finish(context)
        return self.receipt(context)

    def finish(self, context):
        self.controller._live()
        require(self.intent(context), ErrorCode.MANUAL_ACTION_REQUIRED)
        self.controller._release(self.role)
        self.controller._save(self.role + '-released.json', canonical_bytes(self.owner(context)))
        require(self.current(context), ErrorCode.VALIDATION_FAILED)

    def current(self, context):
        self.controller._live()
        require(self.intent(context) and self.controller._read(self.role + '-released.json', 4096)
                == canonical_bytes(self.owner(context)), ErrorCode.SOURCE_DRIFT)
        self.controller._observe(self.role, closed=False, markers=False)
        return True

    def validate(self, context):
        return self.current(context) and context.evidence == self.receipt(context).as_dict()

    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)

    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        if not self.intent(context):
            self.prepare(context)
            return Recovery(RecoveryDecision.RETRY_SAFE)
        self.finish(context)
        return Recovery(RecoveryDecision.COMMITTED, self.receipt(context))


class ReopenFilesPlan:
    """Trusted native caller only. No HTTP route, arbitrary paths or start callback."""
    def __init__(self, barrier, data, configuration, external, gateway_runtime, backup_root):
        require(type(barrier) is hd.HttpDrainLease and type(data) is da.DataAccessFence
                and type(configuration) is cf.admission.ConfigurationLease
                and type(external) is ef.ExternalFence
                and type(gateway_runtime) is GatewayServiceRuntime, ErrorCode.INVALID_DATA)
        self.barrier, self.data, self.configuration, self.external = barrier, data, configuration, external
        self.lease = barrier._lease
        require(data._lease is self.lease and external._lease is self.lease
                and data._runtime is barrier._drain.runtime and gateway_runtime.web is data._runtime
                and configuration._external == (self.lease, external._raw), ErrorCode.INCOMPATIBLE_STATE)
        require(isinstance(backup_root, Path) and backup_root.is_absolute(), ErrorCode.INVALID_DATA)
        self.gateway, self.backups = gateway_runtime, backup_root
        self.root = self.lease.scope.directory / 'mobile-reopen-files'
        self.journal = StateJournal(self.root / 'transaction/state.json')
        self._guard = None

    def __repr__(self): return '<ReopenFilesPlan private activity-closed release>'
    def __reduce__(self): raise TypeError('Reopen plans cannot be serialized')

    def _read(self, name, limit):
        try:
            with _private_directory(self.root, create=False) as fd:
                files._private(fd, directory=True)
                return _optional(fd, name, limit)
        except FileNotFoundError: return None

    def _save(self, name, raw):
        with _private_directory(self.root, create=True) as fd:
            files._private(fd, directory=True)
            old = _optional(fd, name, max(len(raw), 1))
            if old is None: files._new(fd, name, raw)
            else: require(old == raw, ErrorCode.INCOMPATIBLE_STATE)

    def profile(self):
        raw = self._read('profile.json', 16384)
        if raw is None: return None
        value = strict_json_loads(raw)
        exact_keys(value, {'version', 'instance', 'lease_id', 'service_profile_sha256',
                           'gateway_release_sha256', 'backup_root', 'web_backup', 'journals'})
        require(raw == canonical_bytes(value) and type(value['version']) is int and value['version'] == 1,
                ErrorCode.INVALID_STATE)
        exact_keys(value['journals'], {*ROLES, 'data-access', 'external'})
        require(all(type(v) is str and guard.SHA256.fullmatch(v) for v in value['journals'].values()),
                ErrorCode.INVALID_STATE)
        return value

    def _held(self):
        require(os.getuid() == os.geteuid() == 0, ErrorCode.INVALID_DATA)
        self.lease.assert_held(); self.barrier.assert_held(); self.data.assert_held()
        self.configuration.assert_held(); self.external.assert_held()
        profile = strict_json_loads(self.barrier._profile)
        require('foundation' in profile and 'gateway_service' in profile
                and self.barrier._drain.cleaner is not None, ErrorCode.INCOMPATIBLE_STATE)
        if 'public_ingress' in profile:
            from installer.gateway_public_admission import require_profile
            require_profile(self.data._runtime, profile)
        else:
            with fs._directory(self.data._runtime.spec.root.parent) as fd:
                for name in ('boot', 'public'): fs._absent(fd, name)

    def _backup(self):
        # Completed 6B7a recovery rechecks native ownership, stopped services,
        # Gateway live bytes, saved images and the exact composed Web receipt.
        released = gateway.recover(self.gateway, self.barrier, self.backups, confirmed=True).report()
        slot = self.backups / ('gateway-' + self.lease.lease_id)
        with fs._directory(slot) as fd:
            composed = strict_json_loads(files._read(fd, 'composed.json', gateway.g.MAX_JOURNAL * 2))
        web = composed['web']
        with fs._directory(self.backups / web['backup_id']) as fd:
            raw = files._read(fd, 'coordinated.json', gateway.g.MAX_JOURNAL * 2)
        require(f._sha(raw) == web['manifest_sha256'], ErrorCode.SOURCE_DRIFT)
        return released, web

    def _raw(self, role):
        module = MODULES[role]
        raw = self._read(role + '-original.json', module.MAX_JOURNAL)
        require(raw is not None and f._sha(raw) == self.profile()['journals'][role], ErrorCode.SOURCE_DRIFT)
        return raw

    @contextmanager
    def _native(self, role):
        if role == 'data':
            yield (lambda closed: inf._walk(self.data, closed=closed),
                   lambda rows: inf._value(self.data, rows),
                   lambda rows: inf._set(self.data, rows, closed=False))
        elif role == 'configuration':
            with fs._directory(self.lease.scope.directory.parent) as root:
                yield (lambda closed: cf._walk(self.lease, self.configuration, root, closed=closed),
                       lambda rows: cf._value(self.lease, rows),
                       lambda rows: cf._set(self.lease, self.configuration, root, rows, closed=False))
        else:
            require(role == 'web', ErrorCode.INVALID_DATA)
            with fs._directory(self.data._runtime.spec.webroot) as root:
                yield (lambda closed: wf._walk(self.barrier, root, closed=closed),
                       lambda rows: wf._value(self.barrier, rows),
                       lambda rows: wf._set(self.barrier, root, rows, closed=False))

    def _observe(self, role, *, closed, markers):
        raw = self._raw(role); module = MODULES[role]; gate = self.lease._directory
        with self._native(role) as (walk, value, _):
            require(canonical_bytes(value(inf._baseline(walk(closed)))) == raw, ErrorCode.SOURCE_DRIFT)
        marker = _optional(gate, module.MARKER, module.MAX_JOURNAL)
        require(marker == raw if markers else marker is None, ErrorCode.SOURCE_DRIFT)
        fs._absent(gate, module.RELEASE)

    def _live(self):
        self._held(); require(self._guard is not None, ErrorCode.CONFIRMATION_REQUIRED)
        self._guard.assert_held()
        profile = self.profile(); released, web = self._backup()
        require(profile == self._profile(released, web, profile['journals']), ErrorCode.SOURCE_DRIFT)
        for role, raw in (('data-access', self.data._raw), ('external', self.external._raw)):
            require(self._read(role + '-original.json', ef.MAX_JOURNAL) == raw
                    and profile['journals'][role] == f._sha(raw), ErrorCode.SOURCE_DRIFT)
        for role in ROLES: self._raw(role)
        document = self.journal.read()
        for role, record in zip(ROLES, document['steps']):
            if record['state'] == 'DONE': self._observe(role, closed=False, markers=False)
            elif record['state'] == 'PLANNED': self._observe(role, closed=True, markers=True)

    def _profile(self, released, web, journals):
        return {'version': 1, 'instance': self.lease.scope.instance, 'lease_id': self.lease.lease_id,
                'service_profile_sha256': f._sha(self.barrier._profile),
                'gateway_release_sha256': f._sha(canonical_bytes(released)),
                'backup_root': str(self.backups), 'web_backup': web, 'journals': journals}

    def plan(self, *, confirmed):
        require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
        self._held()
        if self.profile() is not None:
            # Repeated planning is read-only; progress is inspected by execute.
            engine = self.engine(); require(engine.report() is not None, ErrorCode.MANUAL_ACTION_REQUIRED)
            return engine.report()
        require(self.journal.read() is None, ErrorCode.INCOMPATIBLE_STATE)
        released, web = self._backup(); journals = {}
        for role in ROLES:
            module = MODULES[role]; fs._absent(self.lease._directory, module.RELEASE)
            raw = files._read(self.lease._directory, module.MARKER, module.MAX_JOURNAL)
            with self._native(role) as (walk, value, _):
                require(raw == canonical_bytes(value(inf._baseline(walk(True)))), ErrorCode.SOURCE_DRIFT)
            key = {'data': 'inode_fence_sha256', 'configuration': 'configuration_fence_sha256',
                   'web': 'web_fence_sha256'}[role]
            require(web.get(key) == f._sha(raw), ErrorCode.SOURCE_DRIFT)
            self._save(role + '-original.json', raw); journals[role] = f._sha(raw)
        for role, raw, key in (('data-access', self.data._raw, 'data_access_fence_sha256'),
                              ('external', self.external._raw, 'external_path_reservations_sha256')):
            require(web.get(key) == f._sha(raw), ErrorCode.SOURCE_DRIFT)
            self._save(role + '-original.json', raw); journals[role] = f._sha(raw)
        self._save('profile.json', canonical_bytes(self._profile(released, web, journals)))
        return self.engine().plan(mode='upgrade')

    def engine(self):
        require(self.profile() is not None, ErrorCode.NOT_PLANNED)
        operations = []; previous = None
        for role in ROLES:
            operation = FileRelease(self, role, previous)
            operations.append(operation); previous = operation.spec.name
        engine = TransactionEngine(self.journal, OperationRegistry(tuple(operations)))
        if engine.report() is not None: engine._compatible(engine.report())
        return engine

    def _release(self, role):
        module = MODULES[role]; gate = self.lease._directory; raw = self._raw(role)
        release_raw = canonical_bytes({'version': 1, 'fence_sha256': f._sha(raw)})
        marker = _optional(gate, module.MARKER, module.MAX_JOURNAL)
        pending = _optional(gate, module.RELEASE, 4096)
        with self._native(role) as (walk, value, unseal):
            observed = walk(None); rows = inf._baseline(observed)
            require(canonical_bytes(value(rows)) == raw, ErrorCode.SOURCE_DRIFT)
            all_open = all(not row['flags'] & inf.IMMUTABLE for row in observed)
            if marker is None:
                # Exact external intent plus exact native state, never absence alone.
                require(pending is None and all_open, ErrorCode.MANUAL_ACTION_REQUIRED)
                return
            require(marker == raw and pending in (None, release_raw), ErrorCode.SOURCE_DRIFT)
            if pending is None and not all_open:
                require(all(row['flags'] & inf.IMMUTABLE for row in observed), ErrorCode.MANUAL_ACTION_REQUIRED)
                files._new(gate, module.RELEASE, release_raw)
                pending = release_raw
            # The all-open case also covers the old RELEASE-unlinked cut.
            unseal(rows)
            require(walk(False) == rows, ErrorCode.SOURCE_DRIFT)
        require(files._read(gate, module.MARKER, module.MAX_JOURNAL) == raw, ErrorCode.SOURCE_DRIFT)
        if pending is not None:
            require(files._read(gate, module.RELEASE, 4096) == release_raw, ErrorCode.SOURCE_DRIFT)
            os.unlink(module.RELEASE, dir_fd=gate); os.fsync(gate)
        os.unlink(module.MARKER, dir_fd=gate); os.fsync(gate)

    def execute(self, action, confirmation, *, confirmed, name=None):
        require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
        require(action in ('apply', 'resume', 'retry', 'check')
                and (name is not None) == (action == 'retry'), ErrorCode.INVALID_DATA)
        self._held(); engine = self.engine(); document = engine.report()
        require(document is not None and confirmation == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
        existing = _optional(self.lease._directory, guard.MARKER, guard.MAX_BYTES)
        if existing is None:
            require(action == 'apply' and all(row['state'] == 'PLANNED' for row in document['steps'])
                    and all(self._read(role + '-intent.json', 4096) is None for role in ROLES),
                    ErrorCode.MANUAL_ACTION_REQUIRED)
            self._guard = guard.begin(self.lease, plan_sha256=confirmation, confirmed=True)
        else: self._guard = guard.recover(self.lease, plan_sha256=confirmation, confirmed=True)
        self._live()
        # TransactionEngine skips DONE steps. Reobserve them before ANY next step.
        for spec, record in zip(document['plan']['steps'], document['steps']):
            if record['state'] == 'DONE':
                context = OperationContext(document['installation_id'], spec, record['evidence'], engine.secrets)
                require(engine.registry.get(spec).validate(context), ErrorCode.SOURCE_DRIFT)
        if action == 'check': require(document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        elif action == 'retry': document = engine.retry(name, confirmation)
        else: document = getattr(engine, action)(confirmation)
        return {'state': 'FILE_PROTECTIONS_RELEASED_ACTIVITY_CLOSED' if document['state'] == 'DONE'
                else 'FILE_PROTECTIONS_RELEASE_INCOMPLETE', 'transaction': document,
                'activity_resumed': False, 'admission_verified': False, 'services_started': False,
                'data_access_reopened': False, 'external_paths_released': False}
