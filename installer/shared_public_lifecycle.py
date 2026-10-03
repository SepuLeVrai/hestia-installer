"""Internal lifecycle with consent distinct from the historical preparation.

No cockpit route is exposed. Read-only status never runs a native observation.
The main transaction lock serializes this controller with existing workflows.
"""
from installer import shared_public_runtime as native
from installer.model import ErrorCode, canonical_bytes, exact_keys, require
from installer.package_plan import PackagePlan
from installer.shared_public_plan import digest
from installer.transaction import StateJournal


class SharedPublicLifecycle:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, preparation, gateway_service):
        self.preparation, self.gateway_service = preparation, gateway_service
        self.parent = preparation.parent
        self.root = preparation.root / 'lifecycle'
        self.journal = StateJournal(self.root / 'transaction/state.json')

    def profile(self):
        value = self._read('profile.json')
        if value is not None: native.SharedPublic(value)
        return value

    def state(self):
        value, document = self.profile(), self.journal.read()
        return {'installation': document, 'profile_sha256': None if value is None else digest(value),
            'historical_only': True, 'current_admission': False, 'public_mobile_available': False,
            'boot_mobile_enabled': False, 'phase6_complete': False}

    def binding(self, parent):
        require(parent is not None and parent['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        prepared = self.preparation.profile(); require(prepared is not None, ErrorCode.NOT_PLANNED)
        require(canonical_bytes(self.preparation.binding(parent, prepared['client_networks'], observe=False)) ==
            canonical_bytes(prepared), ErrorCode.SOURCE_DRIFT)
        engine, runtime = self.gateway_service.engine(parent)
        document = engine.report()
        require(document is not None and document['state'] == 'DONE'
            and self.gateway_service.gateway is self.preparation.gateway, ErrorCode.DEPENDENCY_BLOCKED)
        require(runtime.profile.identity == prepared['gateway_identity'] and runtime.web.spec.instance == prepared['instance'],
            ErrorCode.INCOMPATIBLE_STATE)
        return prepared, runtime.profile.binding()

    def engine(self, parent):
        prepared, gateway = self.binding(parent)
        value = self.profile(); require(value is not None, ErrorCode.NOT_PLANNED)
        require(value == native.selection(prepared, gateway), ErrorCode.INCOMPATIBLE_STATE)
        return native.engine(self.journal, value)

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume', 'retry', 'check'))
        exact_keys(payload, {'preparation_sha256'} if action == 'plan' else
            {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
        with self.parent.journal.locked(create=False) as locked:
            parent = locked.read()
            if action == 'plan':
                prepared, gateway = self.binding(parent)
                require(payload['preparation_sha256'] == digest(prepared), ErrorCode.CONFIRMATION_REQUIRED)
                value = native.selection(prepared, gateway)
                previous = self.profile()
                require(previous is None or previous == value, ErrorCode.PLAN_EXISTS)
                if previous is None:
                    require(self.journal.read() is None, ErrorCode.INVALID_STATE)
                    require(self.preparation.binding(parent, prepared['client_networks'], observe=True) == prepared, ErrorCode.SOURCE_DRIFT)
                    runtime = native.SharedPublic(value); runtime.absent()
                    self.parent.secrets.reject_in(value); self._write('profile.json', value)
                self.engine(parent)[0].plan(mode='upgrade')
            else:
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine, runtime = self.engine(parent); document = engine.report()
                require(document is not None, ErrorCode.NOT_PLANNED)
                require(payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                # Validate completed steps before the engine skips them. A
                # stopped/changed successor can never be silently restarted.
                for spec, record in zip(document['plan']['steps'], document['steps']):
                    if record['state'] == 'DONE':
                        require(engine.registry.get(spec).validate(engine._context(document, spec, record)), ErrorCode.SOURCE_DRIFT)
                if action == 'check': require(document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                else:
                    scope = runtime.http._scope(runtime.layout.identity.account())
                    with scope.writer():
                        if action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                        else: getattr(engine, action)(payload['confirmation'])
            require(locked.read() == parent, ErrorCode.SOURCE_DRIFT)
            return self.state()
