"""Last fresh-server plan; file-only status, explicit public access consent."""
from installer import public_tls_runtime as native
from installer.frozen_boot import reference
from installer.model import ErrorCode, exact_keys, require
from installer.package_plan import PackagePlan
from installer.public_tls_profile import choices
from installer.transaction import StateJournal


class PublicTLSPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, parent, boot, acme):
        self.parent, self.boot, self.acme = parent, boot, acme
        self.root = parent.journal.path.parent / 'public-tls'
        self.journal = StateJournal(self.root / 'state.json')
        self.availability = None

    def state(self):
        document = self.journal.read()
        profile = self._read('profile.json')
        configuration = None if profile is None else {'hostname': native.Profile(profile).hostname, **profile['choices']}
        done = document is not None and document['state'] == 'DONE'
        return {'installation': document, 'configuration': configuration, 'availability': self.availability,
                'public_tls_configured': done, 'renewal_configured': done, 'phase5_complete': done}

    def parents(self, parent):
        document, runtime = reference(self.boot, parent)
        acme = self.acme.engine('install').report()
        require(acme is not None and acme['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        return {'boot': document['plan_sha256'], 'acme': acme['plan_sha256']}, runtime

    def engine(self, parent):
        parents, _ = self.parents(parent)
        profile = self._read('profile.json'); require(profile is not None, ErrorCode.NOT_PLANNED)
        require(profile['parents'] == parents and profile['boot'] == self.boot._read('profile.json')
                and profile['acme'] == self.acme.profile()
                and profile['code'] == {n: native.f._sha(b) for n, b in native.boot.code_files().items()}, ErrorCode.INCOMPATIBLE_STATE)
        return native.engine(self.journal, profile)

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume', 'retry', 'check'))
        with self.parent.journal.locked(create=True) as locked:
            parent = locked.read(); parents, runtime = self.parents(parent)
            if action == 'plan':
                exact_keys(payload, {'acme_sha256', 'choices'})
                require(payload['acme_sha256'] == parents['acme'], ErrorCode.CONFIRMATION_REQUIRED)
                choices(payload['choices'])
                profile = self._read('profile.json')
                if profile is None:
                    require(self.journal.read() is None, ErrorCode.INVALID_STATE)
                    reference(self.boot, parent, observe=True); self.acme.packages().observe_installed()
                    account, extension, _, _ = runtime.http._inspect_configuration()
                    fragment = runtime.http._files(account, extension)[native.h.drain.UNIT_ROOT / runtime.http.unit('apache')]
                    profile = {'version': 1, 'boot': self.boot._read('profile.json'), 'acme': self.acme.profile(),
                        'parents': parents, 'choices': payload['choices'], 'backend_fragment_sha256': native.f._sha(fragment),
                        'code': {n: native.f._sha(b) for n, b in native.boot.code_files().items()}}
                    self.parent.secrets.reject_in(profile)
                    public = native.PublicTLS(profile); public.absent(); public.network_ready()
                    self._write('profile.json', profile)
                require(profile['choices'] == payload['choices'], ErrorCode.INCOMPATIBLE_STATE)
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
                        self.availability = runtime.probe()
                    except Exception: self.availability = {'state': 'PUBLIC_TLS_CHECK_FAILED', 'checked_at': native.now()}
                elif action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                else: getattr(engine, action)(payload['confirmation'])
            return self.state()
