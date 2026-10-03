"""Distinct consent for additive Mobile boot; status reads are file-only."""
from installer import mobile_boot_runtime as native
from installer.frozen_shared_public import reference
from installer.model import ErrorCode, exact_keys, require
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal


class MobileBootPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, shared_public):
        self.shared_public, self.parent = shared_public, shared_public.parent
        self.root = shared_public.root / 'mobile-boot'
        self.journal = StateJournal(self.root / 'transaction/state.json')
        self.verification = None

    def state(self):
        return {'installation': self.journal.read(), 'verification': self.verification,
            'historical_only': True, 'phase6_complete': False}

    def binding(self, parent, *, observe=False):
        document, runtime = reference(self.shared_public, parent, observe=observe)
        return native.selection(runtime.value, {'web': parent['plan_sha256'],
            'shared_public': document['plan_sha256'], 'shared_journal': native.digest(document)})

    def engine(self, parent):
        profile = self._read('profile.json')
        require(profile is not None, ErrorCode.NOT_PLANNED)
        require(profile == self.binding(parent), ErrorCode.INCOMPATIBLE_STATE)
        return native.engine(self.journal, profile)

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume', 'retry', 'check'))
        exact_keys(payload, {'shared_public_sha256'} if action == 'plan' else
            {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
        with self.parent.journal.locked(create=False) as locked:
            parent = locked.read()
            if action == 'plan':
                profile = self.binding(parent)
                require(payload['shared_public_sha256'] == profile['parents']['shared_public'], ErrorCode.CONFIRMATION_REQUIRED)
                previous = self._read('profile.json')
                require(previous is None or previous == profile, ErrorCode.PLAN_EXISTS)
                if previous is None:
                    require(self.journal.read() is None, ErrorCode.INVALID_STATE)
                    require(self.binding(parent, observe=True) == profile, ErrorCode.SOURCE_DRIFT)
                    native.MobileBootRuntime(profile).absent()
                    self.parent.secrets.reject_in(profile); self._write('profile.json', profile)
                self.engine(parent)[0].plan(mode='upgrade')
            else:
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine, runtime = self.engine(parent); document = engine.report()
                require(document is not None, ErrorCode.NOT_PLANNED)
                require(payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                self.verification = None
                for spec, record in zip(document['plan']['steps'], document['steps']):
                    if record['state'] == 'DONE':
                        require(engine.registry.get(spec).validate(engine._context(document, spec, record)), ErrorCode.SOURCE_DRIFT)
                if action == 'check':
                    require(document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                    runtime.live()
                    self.verification = {'state': 'MOBILE_BOOT_CONFIGURED', 'checked_at': native.public.old.now()}
                else:
                    scope = runtime.http._scope(runtime.layout.identity.account())
                    with scope.writer():
                        if action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                        else: getattr(engine, action)(payload['confirmation'])
            require(locked.read() == parent, ErrorCode.SOURCE_DRIFT)
            return self.state()
