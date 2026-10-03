"""Cockpit consent and durable stage selection; no cached native authority."""
from copy import deepcopy
from dataclasses import replace
import re

from installer.mobile_preparation_runtime import NativePreparation, STAGES
from installer.application_plan import FreshProfile, validate_credentials
from installer.mobile_activation_plan import CREDENTIALS, PARENTS, digest
from installer.github_sources import AcquireOperation
from installer.model import ErrorCode, InstallerError, SourceSpec, exact_keys, require
from installer.package_plan import PackagePlan
from installer.database_step import SqlAuthorityCredentials
from installer.php_transport import WEB_REPOSITORY
from installer.web_releases import STORAGE_COMMIT

POLICY = 'COCKPIT_MAIN_PREPARATION_V1'


class MobilePreparationPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, backup):
        self.backup, self.activation = backup, backup.activation
        self.application, self.parent = backup.application, backup.parent
        self.root = self.parent.journal.path.parent / 'mobile-preparation'
        self.last_error = None

    def binding(self, parent):
        binding = self.activation.binding(parent)
        profile = self.backup.profile(); require(profile is not None, ErrorCode.DEPENDENCY_BLOCKED)
        require(all(profile[key] == value for key, value in binding.items()), ErrorCode.INCOMPATIBLE_STATE)
        approved, lease = self.backup.records(profile)
        receipt = self.backup.receipt(profile, lease)
        require(approved is not None and lease is not None and receipt is not None, ErrorCode.DEPENDENCY_BLOCKED)
        return {**binding, 'policy': POLICY, 'lease_id': lease['lease_id'],
                'backup_profile_sha256': digest(profile), 'backup_receipt_sha256': digest(receipt)}

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'instance', 'parents', 'draft_sha256', 'policy', 'lease_id',
                               'backup_profile_sha256', 'backup_receipt_sha256'})
            require(type(value['version']) is int and value['version'] == 1 and value['policy'] == POLICY, ErrorCode.INVALID_STATE)
            exact_keys(value['parents'], PARENTS)
            for key in ('instance', 'lease_id'):
                require(type(value[key]) is str and re.fullmatch('[a-f0-9]{32}', value[key]), ErrorCode.INVALID_STATE)
            require(all(type(x) is str and re.fullmatch('[a-f0-9]{64}', x) for x in [*value['parents'].values(),
                value['draft_sha256'], value['backup_profile_sha256'], value['backup_receipt_sha256']]), ErrorCode.INVALID_STATE)
        return value

    def progress(self, profile):
        confirmation = digest(profile); approved = self._read('approved.json')
        require(approved is None or approved == {'confirmation': confirmation}, ErrorCode.INVALID_STATE)
        rows = []; previous = confirmation; pending = False
        for stage in STAGES:
            intent = self._read(stage + '.intent.json'); done = self._read(stage + '.done.json')
            owner = {'confirmation': confirmation, 'stage': stage, 'previous_sha256': previous}
            require(not pending or intent is None and done is None, ErrorCode.INVALID_STATE)
            require(intent is None or approved is not None and intent == owner, ErrorCode.INVALID_STATE)
            if done is not None:
                exact_keys(done, {'owner', 'result'})
                result = done['result']; exact_keys(result, {'stage', 'instance', 'lease_id', 'result_sha256', 'services_started', 'activity_resumed'})
                require(intent == owner and done['owner'] == owner and result['stage'] == stage
                        and result['instance'] == profile['instance'] and result['lease_id'] == profile['lease_id']
                        and type(result['result_sha256']) is str and re.fullmatch('[a-f0-9]{64}', result['result_sha256'])
                        and result['services_started'] is False and result['activity_resumed'] is False, ErrorCode.INVALID_STATE)
                previous = digest(done)
            else: pending = True
            rows.append({'stage': stage, 'state': 'DONE' if done else 'INTENT_RECORDED' if intent else 'PENDING'})
        return approved, rows

    def ready(self, profile):
        candidate = self.activation.candidate(profile)
        require(candidate['lease_id'] == profile['lease_id'], ErrorCode.INVALID_STATE)
        return candidate

    def state(self):
        value = {'state': 'NOT_PLANNED', 'profile': None, 'confirmation': None, 'steps': [],
                 'historical_only': True, 'last_error_redacted': self.last_error, 'activation_ready': False,
                 'services_started': False, 'activity_resumed': False, 'phase6_complete': False}
        try:
            profile = self.profile()
            if profile is None: return value
            approved, rows = self.progress(profile); complete = all(row['state'] == 'DONE' for row in rows)
            if complete: self.ready(profile)
            value.update(profile=profile, confirmation=digest(profile), steps=rows, activation_ready=complete,
                         state='DONE' if complete else 'RESUME_REQUIRED' if approved else 'AWAITING_CONFIRMATION')
        except Exception: value.update(state='UNAVAILABLE', steps=[], activation_ready=False, last_error_redacted='INVALID_STATE')
        return value

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume'))
        with self.parent.journal.locked(create=False) as locked:
            binding = self.binding(locked.read())
            if action == 'plan':
                exact_keys(payload, {'parents'})
                require(payload['parents'] == binding['parents'], ErrorCode.CONFIRMATION_REQUIRED)
                profile = self.profile()
                if profile is None: self._write('profile.json', binding)
                else: require(profile == binding, ErrorCode.PLAN_EXISTS)
                return self.state()
            exact_keys(payload, {'confirmation', 'confirm', 'credentials', 'allow_global_read_lock'})
            profile = self.profile(); require(profile is not None, ErrorCode.NOT_PLANNED)
            require(profile == binding, ErrorCode.INCOMPATIBLE_STATE)
            require(payload['confirm'] is True and payload['confirmation'] == digest(profile), ErrorCode.CONFIRMATION_REQUIRED)
            approved, rows = self.progress(profile)
            require(action == 'apply' and approved is None or action == 'resume' and approved is not None, ErrorCode.INCOMPATIBLE_STATE)
            if all(row['state'] == 'DONE' for row in rows): self.ready(profile); return self.state()
            credentials = payload['credentials']
            require(type(credentials) is dict and set(credentials) == CREDENTIALS, ErrorCode.SECRET_REQUIRED)
            validate_credentials(credentials)
            require(payload['allow_global_read_lock'] is True, ErrorCode.CONFIRMATION_REQUIRED)
            draft = self.application.read(); fresh = FreshProfile.from_draft(draft)
            self.last_error = None
            try:
                http = fresh.http(draft['configuration']); account, _, _, _ = http._inspect_configuration(); scope = http._scope(account)
                require(scope.observe() == {'state': 'MAINTENANCE_REQUIRED', 'instance': profile['instance'],
                                            'lease_id': profile['lease_id']}, ErrorCode.MANUAL_ACTION_REQUIRED)
                value = deepcopy(draft['configuration']); value.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'})
                value['database']['mode'] = 'existing_local'
                value['secrets'] = {'database_password': credentials['database_password'], 'admin_password': '', 'openai_api_key': ''}
                authority = SqlAuthorityCredentials(credentials['authority_user'], credentials['authority_password'])
                source = AcquireOperation(self.parent.journal.path.parent, 'web',
                    SourceSpec(WEB_REPOSITORY, fresh.source_commit, fresh.source_commit), None).path / 'tree'
                native = NativePreparation(http, scope, profile['lease_id'], self.backup.backups(profile),
                    replace(fresh.runtime(), timeout_seconds=120), source, value, authority)
                if approved is None: self._write('approved.json', {'confirmation': digest(profile)})
                previous = digest(profile)
                for row in rows:
                    stage = row['stage']
                    if row['state'] == 'DONE': previous = digest(self._read(stage + '.done.json')); continue
                    owner = {'confirmation': digest(profile), 'stage': stage, 'previous_sha256': previous}
                    if row['state'] == 'PENDING': self._write(stage + '.intent.json', owner)
                    result = native.execute(stage)
                    self._write(stage + '.done.json', {'owner': owner, 'result': result})
                    self.progress(profile); previous = digest(self._read(stage + '.done.json'))
                self.ready(profile)
            except Exception:
                self.last_error = ErrorCode.MANUAL_ACTION_REQUIRED.value
                raise InstallerError(ErrorCode.MANUAL_ACTION_REQUIRED) from None
            return self.state()
