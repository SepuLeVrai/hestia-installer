"""Explicit fresh activation bound to an immutable completed preparation.

Separate journal: no approved staging step is rewritten. Native ownership and
durable intents precede admission and each start. Recovery only observes; it
never starts, enables, restarts, repairs or recloses a service implicitly.
"""
import http.client
import re
import time

from installer import application_plan as app, http_runtime as h, system_drain as drain
from installer.engine import TransactionEngine
from installer.model import (ErrorCode, Receipt, ResourceSpec, StepSpec, canonical_bytes,
    exact_keys, now, require)
from installer.operations import Operation, OperationRegistry, Recovery, RecoveryDecision
from installer.transaction import StateJournal, _private_directory


class Activation:
    def __init__(self, runtime, parent_sha256):
        require(type(runtime) is h.HttpRuntime and re.fullmatch(r'[a-f0-9]{64}', parent_sha256) is not None)
        self.runtime, self.parent_sha256 = runtime, parent_sha256
        self.cleaner = app.cleaner.SessionCleaner(runtime)
        self.directory = runtime.spec.root.parent / 'attempts'

    def configuration(self):
        account, extension, plan, initial = self.runtime._inspect_configuration()
        scope = self.runtime._scope(account)
        files = self.runtime._files(account, extension)
        for role in ('php', 'apache'):
            drain.audit_unit(scope, drain.UnitBinding(role, h.f._sha(files[drain.UNIT_ROOT / self.runtime.unit(role)])))
        _, _, collector_files, _ = self.cleaner._inspect_configuration()
        drain.audit_unit(scope, drain.UnitBinding('session-cleaner', h.f._sha(collector_files[drain.UNIT_ROOT / self.cleaner.unit])), running_collector=True)
        self.cleaner._timer_state(stopped=False)
        return scope, initial

    def serving(self):
        scope, initial = self.configuration()
        require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
        with scope._open() as (fd, _):
            require(h.f._json_read(fd, 'resumed-' + initial['lease_id'] + '.json', scope.web_gid) == {
                'version': 1, 'instance': scope.instance, 'lease_id': initial['lease_id'], 'state': 'ACTIVITY_RESUMED'},
                ErrorCode.INVALID_STATE)

    def unit(self, role):
        return self.cleaner.timer if role == 'timer' else self.runtime.unit(role)

    def running(self, role):
        if role == 'timer':
            value = self.cleaner._timer_state(stopped=False)
            return value['ActiveState'] == 'active' and value['SubState'] in ('waiting', 'running', 'elapsed')
        value = drain._show(self.unit(role))
        require(value['Type'] == 'simple' and value['Result'] == 'success' and value['ControlPID'] == '0', ErrorCode.INVALID_STATE)
        if value['ActiveState'] == 'inactive' and value['SubState'] == 'dead':
            require(value['MainPID'] == '0' and drain._empty_cgroup(self.unit(role)), ErrorCode.INVALID_STATE)
            return False
        require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
                and re.fullmatch(r'[1-9][0-9]*', value['MainPID']) is not None
                and value['ControlGroup'] == '/system.slice/' + self.unit(role)
                and not drain._empty_cgroup(self.unit(role)), ErrorCode.INVALID_STATE)
        # Kernel membership is checked against the exact provisioned unit.
        from pathlib import Path
        require('0::/system.slice/' + self.unit(role) in (Path('/proc') / value['MainPID'] / 'cgroup').read_text().splitlines(), ErrorCode.INVALID_STATE)
        return True

    def check(self):
        """Explicit bounded local HTTP probe; never invoked by GET/state/recovery."""
        self.serving()
        require(all(self.running(role) for role in ('php', 'apache', 'timer')), ErrorCode.VALIDATION_FAILED)
        spec = self.runtime.spec
        require(spec.port == 9080 and spec.ingress == app.ProxyIngress('127.0.0.2', ('127.0.0.1/32',)), ErrorCode.INVALID_STATE)
        connection = http.client.HTTPConnection('127.0.0.1', 9080, timeout=5, source_address=('127.0.0.2', 0))
        try:
            connection.request('GET', '/login.php', headers={'Host': spec.hostname,
                'X-Forwarded-For': '127.0.0.1', 'X-Forwarded-Proto': 'https', 'Connection': 'close'})
            response = connection.getresponse(); body = response.read(131073)
            require(response.status == 200 and len(body) <= 131072
                    and 'text/html' in (response.getheader('Content-Type') or '')
                    and re.search(rb'name="csrf_token" value="[a-f0-9]+"', body) is not None,
                    ErrorCode.VALIDATION_FAILED)
        finally: connection.close()
        return {'state': 'LOCAL_WEB_AVAILABLE', 'checked_at': now(), 'backend': '127.0.0.1:9080',
                'login_page': True, 'administrator_login_tested': False, 'public_tls_verified': False,
                'boot_persistence_configured': False, 'application_installed': False}


class ActivationOperation(Operation):
    def __init__(self, activation, role, previous=None):
        self.activation, self.role = activation, role
        name = 'web.activation.' + role
        target = (str(activation.runtime.spec.maintenance_directory) if role == 'admission'
                  else str(drain.UNIT_ROOT / activation.unit(role)))
        actions = {'admission': 'Autoriser explicitement la sortie de maintenance',
                   'php': 'Démarrer le service PHP-FPM dédié', 'apache': 'Démarrer le service Apache dédié',
                   'timer': 'Démarrer le nettoyage dédié des sessions'}
        super().__init__(StepSpec(name=name, operation=name, module='web', boundary=name, action=actions[role],
            dependencies=(previous,) if previous else (), resources=(ResourceSpec('managed', 'directory' if role == 'admission' else 'file', target, preexisting=True),),
            rollback_supported=False, warnings=('Préparation liée : ' + activation.parent_sha256,
                'Admission ouverte : aucun retour arrière SQL automatique. Démarrage valable pour cette session système ; frontal TLS public non configuré.')))

    def binding(self, context):
        require(context.spec == self.spec.as_dict(), ErrorCode.INCOMPATIBLE_STATE)
        return {'version': 1, 'installation_id': context.installation_id, 'spec_sha256': app.a.digest(context.spec),
                'parent_sha256': self.activation.parent_sha256}

    def intent(self, context, *, create=False):
        expected = canonical_bytes(self.binding(context)); name = 'activation-' + self.role + '.json'
        with _private_directory(self.activation.directory, create=False) as fd:
            try: raw = h.f._read(fd, name, 0, mode=0o600)
            except FileNotFoundError:
                if not create: return False
                h.f._write(fd, name, expected, 0, mode=0o600); raw = expected
            require(raw == expected, ErrorCode.INVALID_STATE)
        return True

    def receipt(self, context):
        return Receipt(hashes_non_secret=(('activation_binding', app.a.digest(self.binding(context))),))

    def prepare(self, context):
        self.activation.configuration()
        if self.role == 'admission':
            self.activation.runtime.observe(); self.activation.cleaner.observe()
        else:
            self.activation.serving()
            require(not self.activation.running(self.role), ErrorCode.MANUAL_ACTION_REQUIRED)
        require(not self.intent(context), ErrorCode.MANUAL_ACTION_REQUIRED)

    def apply(self, context):
        self.prepare(context)
        self.intent(context, create=True)
        if self.role == 'admission':
            scope, initial = self.activation.configuration()
            with scope.recover(initial['lease_id'], confirmed=True) as lease:
                lease.resume(confirmed=True)
        else:
            h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'start', '--', self.activation.unit(self.role)])
        require(self.current(context), ErrorCode.VALIDATION_FAILED)
        return self.receipt(context)

    def current(self, context):
        require(self.intent(context), ErrorCode.INVALID_STATE)
        self.activation.serving()
        return self.role == 'admission' or self.activation.running(self.role)

    def validate(self, context):
        return self.current(context) and context.evidence == self.receipt(context).as_dict()

    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)

    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            if self.current(context): return Recovery(RecoveryDecision.APPLIED, self.receipt(context))
        except Exception: pass
        # No intent means the callback had not begun; otherwise a stopped or
        # ambiguous footprint needs an operator diagnosis, never a blind restart.
        try:
            self.prepare(context)
            return Recovery(RecoveryDecision.RETRY_SAFE)
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class AvailabilityOperation(Operation):
    def __init__(self, activation):
        self.activation = activation
        super().__init__(StepSpec(name='web.activation.availability', operation='web.activation.availability', module='web',
            boundary='web.activation.availability', action='Vérifier la page de connexion du backend local',
            dependencies=('web.activation.timer',), rollback_supported=False,
            warnings=('Préparation liée : ' + activation.parent_sha256, 'Vérification locale à cet instant ; aucun test de connexion administrateur ni de frontal TLS public.')))

    def prepare(self, context): self.activation.serving()

    def apply(self, context):
        # Start jobs complete before the daemons necessarily bind their sockets.
        for attempt in range(10):
            try:
                self.activation.check()
                return Receipt(hashes_non_secret=(('parent_plan', self.activation.parent_sha256),))
            except Exception:
                if attempt == 9: raise
                time.sleep(.2)

    def validate(self, context):
        return context.evidence == Receipt(hashes_non_secret=(('parent_plan', self.activation.parent_sha256),)).as_dict()
    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)
    def recover(self, context, phase):
        return Recovery(RecoveryDecision.RETRY_SAFE if phase == 'apply' else RecoveryDecision.MANUAL)


def registry(activation):
    operations = []; previous = None
    for role in ('admission', 'php', 'apache', 'timer'):
        operation = ActivationOperation(activation, role, previous); operations.append(operation); previous = operation.spec.name
    return OperationRegistry((*operations, AvailabilityOperation(activation)))


class ActivationPlan:
    def __init__(self, application):
        self.application = application
        self.journal = StateJournal(application.engine.journal.path.parent / 'activation' / 'state.json')
        self.availability = None

    def state(self):
        # No services, network, SQL or runtime configuration inspections on GET.
        return {'installation': self.journal.read(), 'availability': self.availability}

    def engine(self, parent):
        require(self.application.owns(parent) and parent['state'] == 'DONE', ErrorCode.NOT_PLANNED)
        self.application.restore()
        draft = self.application.read()
        activation = Activation(app.FreshProfile(draft['instance']).http(draft['configuration']), parent['plan_sha256'])
        engine = TransactionEngine(self.journal, registry(activation))
        current = engine.report()
        if current is not None:
            require([s.as_dict() for s in engine.registry.specs()] == current['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(current)
        return engine, activation

    def execute(self, action, payload):
        # The preparation lock also serializes independent CLI/controllers.
        with self.application.engine.journal.locked(create=False) as locked:
            parent = locked.read(); engine, activation = self.engine(parent)
            if action == 'plan':
                exact_keys(payload, {'preparation_sha256'})
                require(payload['preparation_sha256'] == parent['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                if engine.report() is None: activation.runtime.observe(); activation.cleaner.observe()
                engine.plan(mode='fresh')
            elif action == 'check':
                exact_keys(payload, {'confirmation', 'confirm'})
                require(payload['confirm'] is True and engine.report() is not None
                        and payload['confirmation'] == engine.report()['plan_sha256'] and engine.report()['state'] == 'DONE', ErrorCode.CONFIRMATION_REQUIRED)
                self.availability = None
                try: self.availability = activation.check()
                except Exception:
                    self.availability = {'state': 'LOCAL_WEB_UNAVAILABLE', 'checked_at': now(), 'application_installed': False}
            else:
                require(action in ('apply', 'resume', 'retry'))
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                self.availability = None
                if action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                else: getattr(engine, action)(payload['confirmation'])
            return self.state()
