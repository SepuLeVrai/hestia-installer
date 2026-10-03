"""Additive Foundation MAIN transaction; old Web/Gateway journals are immutable."""
import time

from installer import foundation_probe as probe
from installer.engine import TransactionEngine
from installer.foundation_runtime import FoundationRuntime, h
from installer.gateway_identity import public_identity
from installer.gateway_release import sha
from installer.model import ErrorCode, Receipt, ResourceSpec, StepSpec, canonical_bytes, exact_keys, now, require
from installer.operations import Operation, OperationContext, OperationRegistry, Recovery, RecoveryDecision
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal


class FoundationOperation(Operation):
    def __init__(self, plan, runtime, role, previous=None, *, environment="main"):
        self.controller, self.runtime, self.role = plan, runtime, role
        self.environment = environment
        self.prefix = "" if environment == "main" else "dev-"
        self.result = probe.RESULT if environment == "main" else probe.DEV_RESULT
        self.binding = sha(canonical_bytes(plan.profile()))
        actions = {'stage': 'Préparer le listener privé Foundation MAIN sur 9082',
                   'start': 'Démarrer le listener Foundation MAIN avec le PHP Web existant',
                   'verify': 'Vérifier une assertion MAIN signée, son rejet au rejeu et le refus sans signature'}
        if environment == 'dev':
            actions = {k: v.replace('MAIN', 'DEV').replace('9082', '9081') for k, v in actions.items()}
        super().__init__(StepSpec(name='foundation.' + self.prefix + role, operation='foundation.' + self.prefix + role, module='gateway',
            boundary='foundation.' + self.prefix + role, action=actions[role], dependencies=(previous,) if previous else (),
            resources=((ResourceSpec(self.prefix + 'foundation-root', 'directory', str(runtime.root)),
                        ResourceSpec(self.prefix + 'foundation-unit', 'file', str(runtime.fragment))) if role == 'stage' else ()),
            warnings=('Profil lié : ' + self.binding,
                ('MAIN uniquement. Gateway, accès Mobile public et démarrage automatique restent à raccorder.' if environment == 'main' and 'dev' not in plan.profile() else 'MAIN et DEV restent séparés ; aucun appareil, compte ou session n’est copié.'),
                'Le contrôle signé écrit uniquement des nonces et événements techniques dans le Web.')))

    def owner(self, context):
        require(context.spec == self.spec.as_dict(), ErrorCode.INCOMPATIBLE_STATE)
        return {'installation_id': context.installation_id, 'profile_sha256': self.binding,
                'spec_sha256': sha(canonical_bytes(context.spec))}

    def intent(self, context, create=False):
        name = self.prefix + self.role + '-intent.json'; value = self.controller._read(name)
        if value is None:
            if not create: return False
            self.controller._write(name, self.owner(context)); value = self.owner(context)
        require(value == self.owner(context), ErrorCode.INCOMPATIBLE_STATE)
        return True

    def receipt(self, context):
        return Receipt(created_resources=(self.prefix + 'foundation-root', self.prefix + 'foundation-unit') if self.role == 'stage' else (),
                       hashes_non_secret=(('foundation-binding', sha(canonical_bytes(self.owner(context)))),))

    def prepare(self, context):
        self.runtime.activation.serving()
        if self.role == 'verify': self.runtime.owned()
        else:
            require(not self.intent(context), ErrorCode.MANUAL_ACTION_REQUIRED)
            if self.role == 'stage': self.runtime.absent()
            else: self.runtime.stopped()

    def apply(self, context):
        self.prepare(context); self.intent(context, create=True)
        if self.role == 'stage': self.runtime.stage()
        elif self.role == 'start':
            # Intent is durable before the only start. No enable/restart/adoption.
            h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'start', '--', self.runtime.unit])
            for attempt in range(15):
                try: self.runtime.owned(); break
                except Exception:
                    if attempt == 14: raise
                    time.sleep(.2)
        else:
            result = (probe.check(self.controller.gateway.identities, self.runtime.identity) if self.environment == 'main' else
                      probe.check_dev(self.controller.gateway.identities, self.runtime.identity, self.controller.profile()['identity']))
            self.runtime.owned()
            self.controller._write(self.prefix + 'verified.json', {'owner': self.owner(context), 'result': result, 'checked_at': now()})
        return self.receipt(context)

    def current(self, context):
        require(self.intent(context), ErrorCode.INVALID_STATE)
        if self.role == 'stage': self.runtime.inspect()
        elif self.role == 'start': self.runtime.owned()
        else:
            value = self.controller._read(self.prefix + 'verified.json')
            require(type(value) is dict and set(value) == {'owner', 'result', 'checked_at'}
                    and value['owner'] == self.owner(context) and value['result'] == self.result,
                    ErrorCode.INVALID_STATE)
        return True

    def validate(self, context):
        return self.current(context) and context.evidence == self.receipt(context).as_dict()

    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)

    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            if self.current(context): return Recovery(RecoveryDecision.COMMITTED, self.receipt(context))
        except Exception: pass
        try:
            if self.role == 'verify':
                require(self.controller._read(self.prefix + 'verified.json') is None, ErrorCode.MANUAL_ACTION_REQUIRED)
                # A new explicit attempt generates fresh UUIDs; old tokens are
                # never replayed as successful probes. Recovery itself is read-only.
            self.prepare(context)
            return Recovery(RecoveryDecision.RETRY_SAFE)
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class FoundationPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, application, activation, gateway):
        self.application, self.activation, self.gateway = application, activation, gateway
        self.parent = application.engine
        self.root = gateway.root / 'foundation'
        self.journal = StateJournal(self.root / 'transaction/state.json')
        self.availability = None
        from installer.dev_target import DevRegistration
        self.dev_target = DevRegistration(self)

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'parents', 'draft_sha256', 'identity'} | ({'public_origin'} if 'public_origin' in value else set()) | ({'dev', 'version'} if 'dev' in value else set()))
            if 'dev' in value:
                require(type(value['version']) is int and value['version'] == 2, ErrorCode.INVALID_STATE)
                from installer.dev_target import DevTarget
                exact_keys(value['dev'], {'target', 'identity'})
                DevTarget(value['dev']['target'])
                require(value['dev']['identity'] == public_identity('dev', value['dev']['identity']['public_jwk'])
                        and value['dev']['identity']['thumbprint'] != value['identity']['thumbprint'], ErrorCode.INVALID_STATE)
            if 'public_origin' in value:
                from installer.gateway_identity import public_origin
                public_origin(value['public_origin'])
            exact_keys(value['parents'], {'web', 'activation', 'gateway'})
            require(all(type(v) is str and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
                        for v in [*value['parents'].values(), value['draft_sha256']]), ErrorCode.INVALID_STATE)
            require(value['identity'] == public_identity('main', value['identity']['public_jwk']), ErrorCode.INVALID_STATE)
        return value

    def state(self):
        return {'profile': self.profile(), 'installation': self.journal.read(), 'availability': self.availability,
                'gateway_service_available': False, 'public_mobile_available': False, 'boot_enabled': False,
                'dev_target': self.dev_target.state()}

    def parents(self, parent, dev_confirmation=None):
        web_sha = self.gateway._parent(parent)
        _, activation = self.activation.engine(parent)
        active = self.activation.journal.read(); prepared = self.gateway.engine().report()
        require(active is not None and active['state'] == 'DONE' and prepared is not None
                and prepared['state'] == 'DONE' and self.gateway.profile()['web_plan_sha256'] == web_sha,
                ErrorCode.DEPENDENCY_BLOCKED)
        report = self.gateway.identities.report()
        require(report is not None and report['receipt'] is not None, ErrorCode.DEPENDENCY_BLOCKED)
        value = {'parents': {'web': web_sha, 'activation': active['plan_sha256'], 'gateway': prepared['plan_sha256']},
                 'draft_sha256': sha(canonical_bytes(self.application.read())),
                 'identity': report['receipt']['identities']['main']}
        if self.application.read()['version'] == 2:
            value['public_origin'] = report['profile']['public_origin']
        existing = self.profile()
        if dev_confirmation is not None or existing is not None and 'dev' in existing:
            from installer.dev_target import DevTarget, digest
            target = self.dev_target.read()
            require(target is not None and report['profile']['dev_enabled'] is True, ErrorCode.DEPENDENCY_BLOCKED)
            require(dev_confirmation is None or dev_confirmation == digest(target), ErrorCode.CONFIRMATION_REQUIRED)
            DevTarget(target).separate(activation.runtime, self.application.read()['configuration'])
            value['version'] = 2
            value['dev'] = {'target': target, 'identity': report['receipt']['identities']['dev']}
        return value, activation

    def engine(self, parent):
        value, activation = self.parents(parent)
        require(self.profile() == value, ErrorCode.INCOMPATIBLE_STATE)
        runtime = FoundationRuntime(activation, value['identity'], public_origin=value.get('public_origin'),
                                    **({'dev': value['dev']} if 'dev' in value else {}))
        operations = []; previous = None
        for role in ('stage', 'start', 'verify'):
            operation = FoundationOperation(self, runtime, role, previous)
            operations.append(operation); previous = operation.spec.name
        if 'dev' in value:
            for role in ('stage', 'start', 'verify'):
                operation = FoundationOperation(self, runtime.dev, role, previous, environment='dev')
                operations.append(operation); previous = operation.spec.name
        engine = TransactionEngine(self.journal, OperationRegistry(tuple(operations)), secrets=self.parent.secrets)
        document = engine.report()
        if document is not None:
            require([s.as_dict() for s in engine.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(document)
        return engine, runtime

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume', 'retry', 'check'))
        with self.parent.journal.locked(create=False) as locked:
            parent = locked.read(); value, _ = self.parents(parent, payload.get('dev_confirmation') if action == 'plan' else None)
            if action == 'plan':
                exact_keys(payload, {'parents'} | ({'dev_confirmation'} if 'dev_confirmation' in payload else set()))
                require('dev_confirmation' not in payload or type(payload['dev_confirmation']) is str, ErrorCode.INVALID_DATA)
                require(payload['parents'] == value['parents'], ErrorCode.CONFIRMATION_REQUIRED)
                existing = self.profile()
                if existing is None:
                    require(self.journal.read() is None, ErrorCode.INVALID_STATE)
                    self._write('profile.json', value)
                else: require(existing == value, ErrorCode.PLAN_EXISTS)
                self.engine(parent)[0].plan(mode='fresh')
            else:
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine, runtime = self.engine(parent); document = engine.report()
                require(document is not None and payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                self.availability = None
                if 'dev' in value: runtime.dev.target.inspect()
                gateway = self.gateway.engine(); prepared = gateway.report()
                for spec, record in zip(prepared['plan']['steps'], prepared['steps']):
                    context = OperationContext(prepared['installation_id'], spec, record['evidence'], gateway.secrets)
                    require(gateway.registry.get(spec).validate(context), ErrorCode.SOURCE_DRIFT)
                if action == 'check':
                    require(document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                    try:
                        runtime.owned()
                        result = probe.check(self.gateway.identities, runtime.identity)
                        if 'dev' in value:
                            runtime.dev.owned()
                            result = {**result, 'state': 'FOUNDATION_MAIN_DEV_VERIFIED',
                                      'dev': probe.check_dev(self.gateway.identities, runtime.dev.identity, runtime.identity)}
                            runtime.dev.owned()
                        runtime.owned(); self.availability = {**result, 'checked_at': now()}
                    except Exception:
                        self.availability = {'state': 'FOUNDATION_MAIN_UNAVAILABLE', 'checked_at': now()}
                else:
                    for spec, record in zip(document['plan']['steps'], document['steps']):
                        if record['state'] == 'DONE':
                            context = OperationContext(document['installation_id'], spec, record['evidence'], engine.secrets)
                            require(engine.registry.get(spec).validate(context), ErrorCode.SOURCE_DRIFT)
                    if action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                    else: getattr(engine, action)(payload['confirmation'])
            return self.state()
