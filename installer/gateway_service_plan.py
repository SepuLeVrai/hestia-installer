"""Explicit native Gateway plan, separate from every qualified parent journal."""
import time

from installer import gateway_service_probe as probe
from installer.engine import TransactionEngine
from installer.gateway_release import sha
from installer.gateway_service_runtime import GatewayServiceRuntime, h
from installer.model import ErrorCode, Receipt, ResourceSpec, StepSpec, canonical_bytes, exact_keys, now, require
from installer.operations import Operation, OperationContext, OperationRegistry, Recovery, RecoveryDecision
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal


class GatewayServiceOperation(Operation):
    def __init__(self, controller, runtime, role, previous=None):
        self.controller, self.runtime, self.role = controller, runtime, role
        self.binding = sha(canonical_bytes(controller.profile()))
        account = runtime.profile.account
        resources = {'identity': (ResourceSpec('gateway-identity-journal', 'directory', str(account.directory)),
                     ResourceSpec('gateway-service-user', 'external', 'local-user.' + account.user),
                     ResourceSpec('gateway-service-group', 'external', 'local-group.' + account.user)),
                     'stage': (ResourceSpec('gateway-service-root', 'directory', str(runtime.root)),
                               ResourceSpec('gateway-service-unit', 'file', str(runtime.fragment)))}
        actions = {'identity': 'Créer le compte système privé de Gateway après contrôle des collisions',
                   'stage': 'Préparer le binaire qualifié et le credential MAIN privé',
                   'start': 'Démarrer Gateway sur 9083 et initialiser son état privé',
                   'verify': 'Vérifier le raccordement signé de Gateway à Foundation MAIN'}
        super().__init__(StepSpec(name='gateway-service.' + role, operation='gateway-service.' + role, module='gateway',
            boundary='gateway-service.' + role, action=actions[role], dependencies=(previous,) if previous else (),
            resources=resources.get(role, ()), warnings=('Profil lié : ' + self.binding,
                'MAIN uniquement ; les clés DEV préparées sont conservées pour un raccordement ultérieur.',
                'La sonde écrit uniquement des quotas, nonces et événements techniques. Aucun enrôlement.',
                'Pas de démarrage automatique ni de réouverture après maintenance dans ce plan.')))

    def owner(self, context):
        require(context.spec == self.spec.as_dict(), ErrorCode.INCOMPATIBLE_STATE)
        return {'installation_id': context.installation_id, 'profile_sha256': self.binding,
                'spec_sha256': sha(canonical_bytes(context.spec))}

    def intent(self, context, create=False):
        name = self.role + '-intent.json'; value = self.controller._read(name)
        if value is None:
            if not create: return False
            self.controller._write(name, self.owner(context)); value = self.owner(context)
        require(value == self.owner(context), ErrorCode.INCOMPATIBLE_STATE)
        return True

    def receipt(self, context):
        return Receipt(created_resources=tuple(r.name for r in self.spec.resources),
                       hashes_non_secret=(('gateway-service-binding', sha(canonical_bytes(self.owner(context)))),))

    def prepare(self, context):
        self.runtime.foundation.owned()
        if self.role == 'verify': self.runtime.owned()
        else:
            require(not self.intent(context), ErrorCode.MANUAL_ACTION_REQUIRED)
            if self.role == 'identity':
                self.runtime.preflight(); self.runtime.profile.account.prepare()
            elif self.role == 'stage': self.runtime.preflight(); self.runtime.account()
            else: self.runtime.stopped(); self.runtime.empty_state()

    def apply(self, context):
        self.prepare(context); self.intent(context, create=True)
        if self.role == 'identity': self.runtime.profile.account.create(confirmed=True)
        elif self.role == 'stage': self.runtime.stage(self.controller.gateway.root / 'binary/package.zip')
        elif self.role == 'start':
            h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'start', '--', self.runtime.unit])
            for attempt in range(20):
                try: self.runtime.owned(); break
                except Exception:
                    if attempt == 19: raise
                    time.sleep(.2)
            self.controller._write('started.json', {'owner': self.owner(context), 'state': self.runtime.state_binding()})
        else:
            result = probe.check(self.runtime.profile.identity['public_origin']); self.runtime.owned()
            self.controller._write('verified.json', {'owner': self.owner(context), 'result': result, 'checked_at': now()})
        return self.receipt(context)

    def current(self, context):
        require(self.intent(context), ErrorCode.INVALID_STATE)
        if self.role == 'identity': self.runtime.profile.account.observe()
        elif self.role == 'stage': self.runtime.inspect()
        elif self.role == 'start':
            self.runtime.owned()
            require(self.controller._read('started.json') == {'owner': self.owner(context), 'state': self.runtime.state_binding()},
                    ErrorCode.SOURCE_DRIFT)
        else:
            value = self.controller._read('verified.json')
            require(type(value) is dict and set(value) == {'owner', 'result', 'checked_at'}
                    and value['owner'] == self.owner(context) and value['result'] == probe.RESULT, ErrorCode.INVALID_STATE)
        return True

    def validate(self, context): return self.current(context) and context.evidence == self.receipt(context).as_dict()
    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)

    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            if self.current(context): return Recovery(RecoveryDecision.COMMITTED, self.receipt(context))
        except Exception: pass
        try:
            if self.role == 'verify':
                require(self.controller._read('verified.json') is None, ErrorCode.MANUAL_ACTION_REQUIRED)
            self.prepare(context)
            return Recovery(RecoveryDecision.RETRY_SAFE)
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class GatewayServicePlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, foundation):
        self.foundation, self.gateway = foundation, foundation.gateway
        self.parent = foundation.parent
        self.root = self.gateway.root / 'service'
        self.journal = StateJournal(self.root / 'transaction/state.json')
        self.availability = None

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'parents', 'binding'})
            exact_keys(value['parents'], {'web', 'activation', 'gateway', 'foundation'})
            require(all(type(v) is str and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
                        for v in value['parents'].values()), ErrorCode.INVALID_STATE)
            require(type(value['binding']) is dict, ErrorCode.INVALID_STATE)
        return value

    def state(self):
        return {'profile': self.profile(), 'installation': self.journal.read(), 'availability': self.availability,
                'public_mobile_available': False, 'boot_enabled': False}

    def parents(self, parent):
        _, foundation = self.foundation.engine(parent)
        document = self.foundation.journal.read()
        require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        runtime = GatewayServiceRuntime(foundation, self.gateway.profile()['identity'], self.gateway.identities.root)
        return {'parents': {**self.foundation.profile()['parents'], 'foundation': document['plan_sha256']},
                'binding': runtime.profile.binding()}, runtime

    def engine(self, parent):
        value, runtime = self.parents(parent)
        require(self.profile() == value, ErrorCode.INCOMPATIBLE_STATE)
        operations = []; previous = None
        for role in ('identity', 'stage', 'start', 'verify'):
            operation = GatewayServiceOperation(self, runtime, role, previous)
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
            parent = locked.read(); value, _ = self.parents(parent)
            if action == 'plan':
                exact_keys(payload, {'parents'})
                require(payload['parents'] == value['parents'], ErrorCode.CONFIRMATION_REQUIRED)
                existing = self.profile()
                if existing is None:
                    require(self.journal.read() is None, ErrorCode.INVALID_STATE); self._write('profile.json', value)
                else: require(existing == value, ErrorCode.PLAN_EXISTS)
                self.engine(parent)[0].plan(mode='fresh')
            else:
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine, runtime = self.engine(parent); document = engine.report()
                require(document is not None and payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                self.availability = None
                gateway = self.gateway.engine(); prepared = gateway.report()
                for spec, record in zip(prepared['plan']['steps'], prepared['steps']):
                    context = OperationContext(prepared['installation_id'], spec, record['evidence'], gateway.secrets)
                    require(gateway.registry.get(spec).validate(context), ErrorCode.SOURCE_DRIFT)
                if action == 'check':
                    require(document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                    try:
                        runtime.owned()
                        require(self._read('started.json')['state'] == runtime.state_binding(), ErrorCode.SOURCE_DRIFT)
                        result = probe.check(runtime.profile.identity['public_origin']); runtime.owned()
                        self.availability = {**result, 'checked_at': now()}
                    except Exception: self.availability = {'state': 'GATEWAY_MAIN_UNAVAILABLE', 'checked_at': now()}
                else:
                    for spec, record in zip(document['plan']['steps'], document['steps']):
                        if record['state'] == 'DONE':
                            context = OperationContext(document['installation_id'], spec, record['evidence'], engine.secrets)
                            require(engine.registry.get(spec).validate(context), ErrorCode.SOURCE_DRIFT)
                    if action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                    else: getattr(engine, action)(payload['confirmation'])
            return self.state()
