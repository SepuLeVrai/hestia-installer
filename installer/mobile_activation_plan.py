"""Cockpit consent and historical progress for the qualified mobile transition.

The one supported source is the fresh MAIN profile's gateway-backup directory.
No browser path, unit, lease object, command or replacement journal is accepted.
GET only reads private files. Native authority is reconstructed on explicit POST.
"""
from copy import deepcopy
from dataclasses import replace
import os
import re

from installer import mobile_activation_admission as native
from installer.application_plan import FreshProfile, validate_credentials
from installer.github_sources import AcquireOperation
from installer.model import (ErrorCode, InstallerError, SourceSpec, canonical_bytes,
                             exact_keys, now, require, strict_json_loads)
from installer.package_plan import PackagePlan
from installer.transaction import _private_directory, _FILE_FLAGS, _check_file
from installer.web_releases import STORAGE_COMMIT

ROLES = native.v.ROLES
CREDENTIALS = {'database_password', 'authority_user', 'authority_password'}
PARENTS = {'web', 'activation', 'gateway', 'foundation', 'gateway_service'}


def digest(value): return native.f._sha(canonical_bytes(value))


def read_private(root, name):
    try:
        with _private_directory(root, create=False) as fd:
            handle = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
            try:
                _check_file(handle)
                require(0 < os.fstat(handle).st_size <= 65536, ErrorCode.INVALID_STATE)
                raw = os.read(handle, 65537); value = strict_json_loads(raw)
                require(canonical_bytes(value) == raw, ErrorCode.INVALID_STATE)
                return value
            finally: os.close(handle)
    except FileNotFoundError: return None


class MobileActivationPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, application, gateway_service=None):
        self.gateway_service = gateway_service
        self.application = application
        self.parent = self.application.engine
        self.root = self.parent.journal.path.parent / 'mobile-activation'
        self.availability = None
        self.last_error = None

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'instance', 'parents', 'draft_sha256', 'lease_id', 'resume_plan_sha256'})
            require(type(value['version']) is int and value['version'] == 1, ErrorCode.INVALID_STATE)
            exact_keys(value['parents'], PARENTS)
            for item in ('instance', 'lease_id'):
                require(type(value[item]) is str and re.fullmatch('[a-f0-9]{32}', value[item]), ErrorCode.INVALID_STATE)
            for item in [*value['parents'].values(), value['draft_sha256'], value['resume_plan_sha256']]:
                require(type(item) is str and re.fullmatch('[a-f0-9]{64}', item), ErrorCode.INVALID_STATE)
        return value

    def binding(self, parent):
        require(self.gateway_service is not None, ErrorCode.UNSUPPORTED_MODULE)
        require(parent is not None and parent['state'] == 'DONE' and self.application.owns(parent),
                ErrorCode.DEPENDENCY_BLOCKED)
        engine, _ = self.gateway_service.engine(parent)
        completed = engine.report()
        require(completed is not None and completed['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        draft = self.application.read()
        return {'version': 1, 'instance': draft['instance'], 'draft_sha256': digest(draft),
                'parents': {**self.gateway_service.profile()['parents'], 'gateway_service': completed['plan_sha256']}}

    @staticmethod
    def backups(value): return FreshProfile(value['instance']).root / 'gateway-backup'

    def candidate(self, value):
        backups = self.backups(value)
        try:
            with _private_directory(backups, create=False) as fd:
                names = [name for name in os.listdir(fd) if re.fullmatch('mobile-resume-[a-f0-9]{32}', name)]
        except FileNotFoundError: raise InstallerError(ErrorCode.DEPENDENCY_BLOCKED) from None
        # Ambiguity is manual; never choose the newest path or an arbitrary lease.
        require(len(names) == 1, ErrorCode.DEPENDENCY_BLOCKED)
        root = backups / names[0]; plan = read_private(root, 'plan.json')
        require(type(plan) is dict and plan.get('instance') == value['instance']
                and plan.get('lease_id') == names[0][14:] and plan.get('backup_root') == str(backups)
                and plan.get('policy') == native.s.p.POLICY and plan.get('maintenance_last') is True
                and plan.get('automatic_start_retry_allowed') is False, ErrorCode.INCOMPATIBLE_STATE)
        raw = canonical_bytes(plan); saved = native.s.p.ResumePlan(root, raw)
        originals = {name: native.e._read_path(root, name, 65536) for name in native.s.p.COPIES}
        with saved._slot() as fd: saved._records(fd, originals, complete=True)
        require([row['role'] for row in plan['start_order']] == list(ROLES), ErrorCode.INCOMPATIBLE_STATE)
        blockers = read_private(backups / ('mobile-blockers-' + plan['lease_id']), 'done.json')
        require(type(blockers) is dict and blockers.get('state') == 'OLD_BLOCKERS_REPLACED_ACTIVITY_CLOSED'
                and blockers.get('owner', {}).get('resume_plan_sha256') == digest(plan), ErrorCode.DEPENDENCY_BLOCKED)
        return {'lease_id': plan['lease_id'], 'resume_plan_sha256': digest(plan)}

    def snapshot(self, profile):
        """Projection, never an admission/health certificate or replay authority."""
        root = self.backups(profile) / ('mobile-activation-' + profile['lease_id'])
        plan = read_private(root, 'plan.json')
        steps = [{'role': role, 'state': 'PENDING'} for role in ROLES]
        if plan is None: return steps, False, False
        require(plan.get('instance') == profile['instance'] and plan.get('lease_id') == profile['lease_id']
                and plan.get('resume_plan_sha256') == profile['resume_plan_sha256']
                and plan.get('policy') == native.t.POLICY, ErrorCode.INVALID_STATE)
        owner = {'version': 1, 'instance': profile['instance'], 'lease_id': profile['lease_id'],
                 'activation_plan_sha256': digest(plan), 'resume_plan_sha256': profile['resume_plan_sha256']}
        admitted = read_private(root, 'admitted.json')
        if admitted is not None:
            require(admitted == {'owner': owner, 'state': 'ACTIVITY_GATE_RELEASED', 'services_started': False,
                'current_sql_admission': False, 'automatic_start_retry_allowed': False}, ErrorCode.INVALID_STATE)
        pending = False
        for row in steps:
            intent = read_private(root, row['role'] + '.intent.json')
            receipt = read_private(root, row['role'] + '.started.json')
            require(not pending or intent is None and receipt is None, ErrorCode.INVALID_STATE)
            if intent is not None:
                require(admitted is not None and intent.get('owner') == owner and intent.get('role') == row['role'],
                        ErrorCode.INVALID_STATE)
                row['state'] = 'INTENT_RECORDED'
            if receipt is not None:
                require(intent is not None and receipt.get('owner') == owner and receipt.get('role') == row['role']
                        and receipt.get('intent_sha256') == digest(intent), ErrorCode.INVALID_STATE)
                row['state'] = 'START_RECORDED'
            else: pending = True
        done = read_private(root, 'done.json')
        if done is not None:
            require(not pending and done.get('owner') == owner and done.get('state') == 'MOBILE_SERVICES_RUNNING'
                    and done.get('services_started') is True, ErrorCode.INVALID_STATE)
        return steps, admitted is not None, done is not None

    def state(self):
        result = {'profile': None, 'confirmation': None, 'state': 'NOT_PLANNED', 'steps': [],
                  'admission_recorded': False, 'historical_only': True, 'availability': self.availability,
                  'last_error_redacted': self.last_error, 'public_tls_verified': False,
                  'boot_persistence': False, 'phase6_complete': False}
        try:
            profile = self.profile()
            if profile is None: return result
            result.update(profile=profile, confirmation=digest(profile))
            attempt = self._read('approved.json')
            require(attempt is None or attempt == {'confirmation': digest(profile)}, ErrorCode.INVALID_STATE)
            steps, admitted, done = self.snapshot(profile)
            require(attempt is not None or not admitted and not done, ErrorCode.INVALID_STATE)
            result.update(steps=steps, admission_recorded=admitted,
                          state='DONE' if done else 'RESUME_REQUIRED' if attempt else 'AWAITING_CONFIRMATION')
        except Exception:
            result.update(state='UNAVAILABLE', steps=[], availability=None, last_error_redacted='INVALID_STATE')
        return result

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume', 'check'))
        with self.parent.journal.locked(create=False) as locked:
            binding = self.binding(locked.read())
            if action == 'plan':
                exact_keys(payload, {'parents'})
                require(payload['parents'] == binding['parents'], ErrorCode.CONFIRMATION_REQUIRED)
                profile = self.profile()
                if profile is None:
                    profile = {**binding, **self.candidate(binding)}
                    self._write('profile.json', profile)
                else: require(all(profile[key] == value for key, value in binding.items()), ErrorCode.PLAN_EXISTS)
                return self.state()
            exact_keys(payload, {'confirmation', 'confirm'} | ({'credentials', 'allow_global_read_lock'} if action != 'check' else set()))
            profile = self.profile()
            require(profile is not None, ErrorCode.NOT_PLANNED)
            require(payload['confirm'] is True and payload['confirmation'] == digest(profile), ErrorCode.CONFIRMATION_REQUIRED)
            require(all(profile[key] == value for key, value in binding.items()), ErrorCode.INCOMPATIBLE_STATE)
            attempt = self._read('approved.json')
            require(attempt is None or attempt == {'confirmation': digest(profile)}, ErrorCode.INVALID_STATE)
            require((action == 'apply' and attempt is None) or (action in ('resume', 'check') and attempt is not None),
                    ErrorCode.INCOMPATIBLE_STATE)
            credentials = {} if action == 'check' else payload['credentials']
            if action != 'check':
                require(type(credentials) is dict and set(credentials) in (set(), CREDENTIALS))
                require(type(payload['allow_global_read_lock']) is bool)
                validate_credentials(credentials)
                if action == 'apply': require(set(credentials) == CREDENTIALS, ErrorCode.SECRET_REQUIRED)
                if credentials: require(payload['allow_global_read_lock'] is True, ErrorCode.CONFIRMATION_REQUIRED)
            draft = self.application.read(); fresh = FreshProfile.from_draft(draft)
            http = fresh.http(draft['configuration']); backups = self.backups(profile)
            self.availability = None; self.last_error = None
            try:
                if action == 'check' or not credentials:
                    result = native.continue_serving(http, backups, profile['lease_id'], profile['resume_plan_sha256'],
                                                    action=action, confirmed=True)
                else:
                    value = deepcopy(draft['configuration'])
                    value.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'})
                    value['database']['mode'] = 'existing_local'
                    value['secrets'] = {'database_password': credentials['database_password'], 'admin_password': '', 'openai_api_key': ''}
                    authority = native.a.c.d.SqlAuthorityCredentials(credentials['authority_user'], credentials['authority_password'])
                    runtime = replace(fresh.runtime(), timeout_seconds=120)
                    source = AcquireOperation(self.parent.journal.path.parent, 'web',
                        SourceSpec(native.p.WEB_REPOSITORY, fresh.source_commit, fresh.source_commit), None).path / 'tree'
                    account, _, _, _ = http._inspect_configuration(); scope = http._scope(account)
                    if attempt is None: self._write('approved.json', {'confirmation': digest(profile)})
                    record = read_private(backups / ('mobile-activation-' + profile['lease_id']), 'plan.json')
                    result = native.execute(http, scope, profile['lease_id'], backups, runtime, source, value, authority,
                        profile['resume_plan_sha256'], action='resume' if record is not None else 'apply',
                        confirmed=True, allow_global_read_lock=True)
                self.availability = {'state': 'LOCAL_SERVICES_AVAILABLE', 'checked_at': now(),
                                     'login_page': result['local_web']['login_page'] is True}
            except Exception as error:
                code = (ErrorCode.SECRET_REQUIRED if str(error) == 'MOBILE_ACTIVATION_MAINTENANCE_REQUIRED'
                        else ErrorCode.BUSY if str(error) == 'MOBILE_ACTIVATION_BUSY' else ErrorCode.MANUAL_ACTION_REQUIRED)
                self.last_error = code.value
                if action == 'check': self.availability = {'state': 'LOCAL_SERVICES_UNAVAILABLE', 'checked_at': now()}
                else: raise InstallerError(code) from None
            return self.state()
