"""Explicit cockpit coordinator; receipts select stages, never native authority."""
import re

from installer.engine import TransactionEngine
from installer.gateway_plan import BinaryImport
from installer.gateway_transition_native import NativeTransition
from installer.mobile_preparation_plan import MobilePreparationPlan
from installer.mobile_activation_plan import CREDENTIALS, PARENTS, digest
from installer.application_plan import validate_credentials
from installer.model import ErrorCode, InstallerError, exact_keys, require
from installer.operations import OperationContext, OperationRegistry
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal

POLICY = 'COCKPIT_GATEWAY_PRIVATE_TRANSITION_V1'
STAGES = ('binaries', 'cutover', 'publication', 'admission', 'activation')


class GatewayTransitionExecution:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, transition, backup):
        self.transition, self.backup, self.parent = transition, backup, backup.parent
        self.root = transition.root / 'execution'
        self.journal = StateJournal(self.root / 'acquisition/state.json')
        self.last_error = self.availability = None

    def binding(self, parent):
        value = self.transition.profile()
        require(value is not None, ErrorCode.DEPENDENCY_BLOCKED)
        assessment = value['assessment']
        current = self.transition.current(parent, assessment['target_release']['commit'], assessment['direction'])
        require(value == current and assessment['configuration_compatible'] is True, ErrorCode.INCOMPATIBLE_STATE)
        require('dev' not in assessment['source'] and 'push' not in assessment['source'], ErrorCode.UNSUPPORTED_MODULE)
        base = MobilePreparationPlan(self.backup).binding(parent)
        require(base['parents']['gateway_service'] == value['source_plan_sha256'], ErrorCode.INCOMPATIBLE_STATE)
        return {**base, 'policy': POLICY, 'transition_sha256': digest(value)}

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'instance', 'parents', 'draft_sha256', 'policy', 'lease_id',
                               'backup_profile_sha256', 'backup_receipt_sha256', 'transition_sha256'})
            require(type(value['version']) is int and value['version'] == 1 and value['policy'] == POLICY, ErrorCode.INVALID_STATE)
            exact_keys(value['parents'], PARENTS)
            for key in ('instance', 'lease_id'):
                require(type(value[key]) is str and re.fullmatch('[a-f0-9]{32}', value[key]), ErrorCode.INVALID_STATE)
            require(all(type(x) is str and re.fullmatch('[a-f0-9]{64}', x) for x in [*value['parents'].values(),
                value['draft_sha256'], value['backup_profile_sha256'], value['backup_receipt_sha256'], value['transition_sha256']]), ErrorCode.INVALID_STATE)
            require(digest(self.transition.profile()) == value['transition_sha256'], ErrorCode.INCOMPATIBLE_STATE)
        return value

    def engine(self, profile, *, stream=None, length=None):
        selected = self.transition.profile()['assessment']['target_release']
        operation = BinaryImport(self.root / 'binary', selected, digest(profile), stream, length)
        engine = TransactionEngine(self.journal, OperationRegistry((operation,)), secrets=self.parent.secrets)
        document = engine.report()
        if document is not None:
            require(document['plan']['steps'] == [operation.spec.as_dict()], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(document)
        return engine

    def acquired(self, profile):
        engine = self.engine(profile); document = engine.report()
        require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        spec, record = document['plan']['steps'][0], document['steps'][0]
        require(engine.registry.get(spec).validate(OperationContext(document['installation_id'], spec,
            record['evidence'], engine.secrets)), ErrorCode.SOURCE_DRIFT)

    def progress(self, profile):
        confirmation = digest(profile); approved = self._read('approved.json')
        require(approved is None or approved == {'confirmation': confirmation}, ErrorCode.INVALID_STATE)
        previous = confirmation; pending = False; rows = []
        for stage in STAGES:
            intent = self._read(stage + '.intent.json'); done = self._read(stage + '.done.json')
            owner = {'confirmation': confirmation, 'stage': stage, 'previous_sha256': previous}
            require(not pending or intent is None and done is None, ErrorCode.INVALID_STATE)
            require(intent is None or approved is not None and intent == owner, ErrorCode.INVALID_STATE)
            if done is not None:
                exact_keys(done, {'owner', 'result_sha256'})
                require(intent == owner and done['owner'] == owner and type(done['result_sha256']) is str
                    and re.fullmatch('[a-f0-9]{64}', done['result_sha256']), ErrorCode.INVALID_STATE)
                previous = digest(done)
            else: pending = True
            rows.append({'stage': stage, 'state': 'DONE' if done else 'INTENT_RECORDED' if intent else 'PENDING'})
        return approved, rows

    def state(self):
        value = {'state': 'NOT_PLANNED', 'profile': None, 'confirmation': None, 'steps': [], 'acquisition': None,
                 'historical_only': True, 'availability': self.availability, 'last_error_redacted': self.last_error,
                 'public_tls_verified': False, 'boot_persistence': False, 'phase6_complete': False}
        try:
            profile = self.profile()
            if profile is None: return value
            approved, rows = self.progress(profile)
            value.update(profile=profile, confirmation=digest(profile), steps=rows, acquisition=self.engine(profile).report(),
                state='DONE' if all(row['state'] == 'DONE' for row in rows) else
                    'RESUME_REQUIRED' if approved else 'AWAITING_CONFIRMATION')
        except Exception: value.update(state='UNAVAILABLE', steps=[], availability=None, last_error_redacted='INVALID_STATE')
        return value

    def import_package(self, confirmation, stream, length):
        with self.parent.journal.locked(create=False) as locked:
            profile = self.profile(); require(profile is not None, ErrorCode.NOT_PLANNED)
            require(confirmation == digest(profile), ErrorCode.CONFIRMATION_REQUIRED)
            require(profile == self.binding(locked.read()), ErrorCode.INCOMPATIBLE_STATE)
            selected = self.transition.profile()['assessment']['target_release']
            require(stream is not None and type(length) is int and length == selected['package_bytes'], ErrorCode.SOURCE_LIMIT)
            engine = self.engine(profile, stream=stream, length=length); document = engine.report()
            require(document is not None, ErrorCode.NOT_PLANNED)
            if document['state'] == 'DONE': self.acquired(profile); return self.state()
            require(self.progress(profile)[0] is None, ErrorCode.INCOMPATIBLE_STATE)
            confirmation = document['plan_sha256']; state = document['steps'][0]['state']
            if state in ('FAILED', 'MANUAL_ACTION_REQUIRED'): engine.retry('gateway.binary', confirmation)
            elif state == 'RUNNING': engine.resume(confirmation)
            else: engine.apply(confirmation)
            return self.state()

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume', 'check'))
        with self.parent.journal.locked(create=False) as locked:
            parent = locked.read(); binding = self.binding(parent)
            if action == 'plan':
                exact_keys(payload, {'transition_sha256'})
                require(payload['transition_sha256'] == binding['transition_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                profile = self.profile()
                if profile is None: self._write('profile.json', binding); profile = binding
                else: require(profile == binding, ErrorCode.PLAN_EXISTS)
                self.engine(profile).plan(mode='fresh')
                return self.state()
            exact_keys(payload, {'confirmation', 'confirm'} | (set() if action == 'check' else {'credentials', 'allow_global_read_lock'}))
            profile = self.profile(); require(profile is not None, ErrorCode.NOT_PLANNED)
            require(profile == binding, ErrorCode.INCOMPATIBLE_STATE)
            require(payload['confirm'] is True and payload['confirmation'] == digest(profile), ErrorCode.CONFIRMATION_REQUIRED)
            approved, rows = self.progress(profile); complete = all(row['state'] == 'DONE' for row in rows)
            require(action == 'apply' and approved is None or action == 'resume' and approved is not None
                    or action == 'check' and complete, ErrorCode.INCOMPATIBLE_STATE)
            # A lost final HTTP response cannot initiate a second activation.
            if action == 'resume' and complete: return self.state()
            credentials = None if action == 'check' else payload['credentials']
            if action != 'check':
                require(type(credentials) is dict and set(credentials) == CREDENTIALS, ErrorCode.SECRET_REQUIRED)
                validate_credentials(credentials)
                require(payload['allow_global_read_lock'] is True, ErrorCode.CONFIRMATION_REQUIRED)
            self.last_error = self.availability = None
            self.acquired(profile)
            try:
                native = NativeTransition(self, parent, profile, credentials)
                if action == 'check': self.availability = native.check(); return self.state()
                if approved is None:
                    native.preflight()
                    self._write('approved.json', {'confirmation': digest(profile)})
                previous = digest(profile)
                for row in rows:
                    stage = row['stage']
                    if row['state'] == 'DONE': previous = digest(self._read(stage + '.done.json')); continue
                    owner = {'confirmation': digest(profile), 'stage': stage, 'previous_sha256': previous}
                    if row['state'] == 'PENDING': self._write(stage + '.intent.json', owner)
                    result = native.execute(stage)
                    self._write(stage + '.done.json', {'owner': owner, 'result_sha256': digest(result)})
                    self.progress(profile); previous = digest(self._read(stage + '.done.json'))
                self.availability = native.check()
            except Exception:
                self.last_error = ErrorCode.MANUAL_ACTION_REQUIRED.value
                raise InstallerError(ErrorCode.MANUAL_ACTION_REQUIRED) from None
            return self.state()
