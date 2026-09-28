"""File-only boot status and distinct confirmation after fresh local activation."""
from installer import boot_runtime as native
from installer.model import ErrorCode, canonical_bytes, exact_keys, require
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal


class BootPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, application, activation, mariadb):
        self.application, self.activation, self.mariadb = application, activation, mariadb
        self.parent = application.engine
        self.root = self.parent.journal.path.parent / 'boot'
        self.journal = StateJournal(self.root / 'state.json')
        self.availability = None

    def state(self):
        return {'installation': self.journal.read(), 'availability': self.availability}

    def engine(self, parent):
        require(self.application.owns(parent) and parent['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        self.application.restore()
        activated, _ = self.activation.engine(parent)
        activation = activated.report()
        sql = self.mariadb.engine().report()
        require(activation is not None and activation['state'] == 'DONE' and sql is not None and sql['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        profile = self._read('profile.json')
        require(profile is not None, ErrorCode.NOT_PLANNED)
        exact_keys(profile, {'version', 'application', 'sql', 'parents', 'code'})
        require(type(profile['version']) is int and profile['version'] == 1
                and profile['application'] == self.application.read() and profile['sql'] == self.mariadb.profile()
                and profile['parents'] == {'preparation': parent['plan_sha256'], 'activation': activation['plan_sha256'], 'sql': sql['plan_sha256']}
                and profile['code'] == {n: native.f._sha(b) for n, b in native.code_files().items()}, ErrorCode.INCOMPATIBLE_STATE)
        return native.engine(self.journal, profile)

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume', 'retry', 'check'))
        with self.parent.journal.locked(create=True) as locked:
            parent = locked.read()
            require(self.application.owns(parent) and parent['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
            activated, current = self.activation.engine(parent)
            active = activated.report()
            require(active is not None and active['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
            if action == 'plan':
                exact_keys(payload, {'activation_sha256'})
                require(payload['activation_sha256'] == active['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                if self._read('profile.json') is None:
                    require(self.journal.read() is None, ErrorCode.INVALID_STATE)
                    self.mariadb.assert_ready(); current.serving()
                    profile = {'version': 1, 'application': self.application.read(), 'sql': self.mariadb.profile(),
                        'parents': {'preparation': parent['plan_sha256'], 'activation': active['plan_sha256'],
                                    'sql': self.mariadb.engine().report()['plan_sha256']},
                        'code': {n: native.f._sha(b) for n, b in native.code_files().items()}}
                    self.parent.secrets.reject_in(profile)
                    native.BootRuntime(profile).absent()
                    self._write('profile.json', profile)
                engine, _ = self.engine(parent); engine.plan(mode='fresh')
            else:
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine, runtime = self.engine(parent); document = engine.report()
                require(document is not None, ErrorCode.NOT_PLANNED)
                require(payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                self.availability = None
                if action == 'check':
                    require(document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                    try:
                        for spec, record in zip(document['plan']['steps'], document['steps']):
                            require(engine.registry.get(spec).validate(engine._context(document, spec, record)), ErrorCode.VALIDATION_FAILED)
                        runtime.live(); value = runtime.activation.check()
                        self.availability = {**value, 'boot_persistence_configured': True}
                    except Exception:
                        self.availability = {'state': 'BOOT_CHECK_FAILED', 'checked_at': native.activation.now(), 'boot_persistence_configured': False}
                elif action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                else: getattr(engine, action)(payload['confirmation'])
            return self.state()
