"""Explicit cockpit entry into the qualified MAIN backup, with bounded recovery.

The saved lease ID identifies a real native gate; it is never a serialized lease.
An interruption before that ID is saved requires manual inspection. Readers only
project original private receipts and never observe or change live services.
"""
from copy import deepcopy
from dataclasses import replace
import os
import re

from installer import provisioned_backup as native
from installer.application_plan import FreshProfile, validate_credentials
from installer.github_sources import AcquireOperation
from installer.mobile_activation_plan import CREDENTIALS, PARENTS, digest, read_private
from installer.model import ErrorCode, InstallerError, SourceSpec, exact_keys, require
from installer.package_plan import PackagePlan
from installer.session_cleaner import SessionCleaner
from installer.transaction import _private_directory
from installer.web_releases import STORAGE_COMMIT

POLICY = 'COCKPIT_MAIN_BACKUP_V1'


class MobileBackupPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, activation, *, root=None, cycle=None):
        self.activation = activation
        self.application, self.parent = activation.application, activation.parent
        self.root = self.parent.journal.path.parent / 'mobile-backup' if root is None else root
        self.cycle = cycle
        self.last_error = None

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'instance', 'parents', 'draft_sha256', 'policy'} |
                       ({'source_generation'} if self.cycle is not None else set()))
            if self.cycle is not None:
                require(value['source_generation'] == self.cycle, ErrorCode.INCOMPATIBLE_STATE)
            require(type(value['version']) is int and value['version'] == 1 and value['policy'] == POLICY,
                    ErrorCode.INVALID_STATE)
            require(type(value['instance']) is str and re.fullmatch('[a-f0-9]{32}', value['instance']), ErrorCode.INVALID_STATE)
            exact_keys(value['parents'], PARENTS)
            require(all(type(x) is str and re.fullmatch('[a-f0-9]{64}', x)
                        for x in [value['draft_sha256'], *value['parents'].values()]), ErrorCode.INVALID_STATE)
        return value

    @staticmethod
    def backups(profile):
        name = 'gateway-backup'
        if 'source_generation' in profile:
            from installer.gateway_public_ancestry import validate
            source = validate(profile['source_generation']['predecessor'])
            name += '-' + source['generation_sha256']
        return FreshProfile(profile['instance']).root / name

    def records(self, profile):
        approved, lease = self._read('approved.json'), self._read('lease.json')
        require(approved is None or approved == {'confirmation': digest(profile)}, ErrorCode.INVALID_STATE)
        if lease is not None:
            exact_keys(lease, {'confirmation', 'lease_id'})
            require(approved is not None and lease['confirmation'] == digest(profile)
                    and type(lease['lease_id']) is str and re.fullmatch('[a-f0-9]{32}', lease['lease_id']), ErrorCode.INVALID_STATE)
        return approved, lease

    def receipt(self, profile, lease):
        if lease is None: return None
        backups = self.backups(profile); slot = backups / ('gateway-' + lease['lease_id'])
        receipt = read_private(slot, 'verified.json')
        if receipt is None: return None
        require(type(receipt) is dict and receipt.get('state') == 'MOBILE_BACKUP_RESTORE_VERIFIED'
                and receipt.get('source_commit') == FreshProfile.from_draft(self.application.read()).source_commit
                and type(receipt.get('backup_id')) is str and re.fullmatch('[a-f0-9]{32}', receipt['backup_id'])
                and receipt.get('gateway_snapshot_id') == slot.name, ErrorCode.INVALID_STATE)
        for key in ('database_restoration_verified', 'registered_data_restoration_verified',
                    'gateway_sqlite_restoration_verified', 'gateway_editor_cache_restoration_verified',
                    'gateway_installation_uuid_preserved', 'maintenance_required'):
            require(receipt.get(key) is True, ErrorCode.INVALID_STATE)
        for key in ('activity_resumed', 'restore_to_original_allowed', 'public_mobile_delivered', 'boot_delivered'):
            require(receipt.get(key) is False, ErrorCode.INVALID_STATE)
        snapshot = read_private(slot, 'snapshot.json'); composed = read_private(slot, 'composed.json')
        web = read_private(backups / receipt['backup_id'], 'verified.json')
        manifest = read_private(backups / receipt['backup_id'], 'coordinated.json')
        require(type(snapshot) is dict and snapshot.get('lease_id') == lease['lease_id']
                and digest(snapshot) == receipt.get('gateway_snapshot_sha256')
                and type(web) is dict and web.get('state') == 'PROVISIONED_BACKUP_RESTORE_VERIFIED'
                and web.get('backup_id') == receipt['backup_id']
                and type(manifest) is dict and digest(manifest) == receipt.get('manifest_sha256')
                and web.get('manifest_sha256') == receipt['manifest_sha256']
                and composed == {'gateway_snapshot': snapshot, 'web': web, 'receipt': receipt}, ErrorCode.INVALID_STATE)
        return receipt

    def state(self):
        result = {'state': 'NOT_PLANNED', 'profile': None, 'confirmation': None, 'backup': None,
                  'historical_only': True, 'last_error_redacted': self.last_error,
                  'activity_resumed': False, 'phase6_complete': False}
        try:
            profile = self.profile()
            if profile is None: return result
            result.update(profile=profile, confirmation=digest(profile))
            approved, lease = self.records(profile); receipt = self.receipt(profile, lease)
            # A native complete receipt also covers loss of the HTTPS response.
            result.update(state='DONE' if receipt else 'RESUME_REQUIRED' if approved else 'AWAITING_CONFIRMATION',
                          backup=None if receipt is None else {key: receipt[key] for key in (
                              'database_restoration_verified', 'registered_data_restoration_verified',
                              'gateway_sqlite_restoration_verified', 'gateway_editor_cache_restoration_verified')})
        except Exception:
            result.update(state='UNAVAILABLE', backup=None, last_error_redacted='INVALID_STATE')
        return result

    def execute(self, action, payload):
        require(action in ('plan', 'apply', 'resume'))
        with self.parent.journal.locked(create=False) as locked:
            binding = {**self.activation.binding(locked.read()), 'policy': POLICY}
            if self.cycle is not None: binding['source_generation'] = self.cycle
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
            approved, lease = self.records(profile)
            require((action == 'apply' and approved is None) or (action == 'resume' and approved is not None),
                    ErrorCode.INCOMPATIBLE_STATE)
            # Never repeat a completed backup, even after a lost response.
            if self.receipt(profile, lease) is not None: return self.state()
            credentials = payload['credentials']
            require(type(credentials) is dict and set(credentials) == CREDENTIALS, ErrorCode.SECRET_REQUIRED)
            validate_credentials(credentials)
            require(payload['allow_global_read_lock'] is True, ErrorCode.CONFIRMATION_REQUIRED)
            draft = self.application.read(); fresh = FreshProfile.from_draft(draft)
            http = fresh.http(draft['configuration']); backups = self.backups(profile)
            self.last_error = None
            try:
                account, _, _, _ = http._inspect_configuration(); scope = http._scope(account)
                observed = scope.observe()
                if lease is None:
                    # A gate created before our lease receipt is not adopted by inference.
                    require(observed['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
                    if self.cycle is not None:
                        from installer.gateway_public_selection import selected
                        from installer.gateway_public_generation import Generation
                        from installer.gateway_public_opening import Opening
                        from installer.gateway_resume_authority import Authority
                        reference = self.cycle['predecessor']
                        previous = Generation(read_private(fresh.root / ('public-successor-' + reference['lease_id']), 'profile.json'))
                        generation = selected(previous.original.shared)
                        require(generation.digest == reference['generation_sha256'], ErrorCode.SOURCE_DRIFT)
                        runtime = generation.selected(http)
                        # Recheck the current consumed admission before a new gate.
                        raw = read_private(runtime.root / 'control' / ('resume-' + reference['lease_id']), 'plan.json')
                        from pathlib import Path
                        Opening(Authority.load(runtime, Path(raw['backup_root']), reference['lease_id'])).check()
                    with _private_directory(backups, create=True) as fd:
                        require(not os.listdir(fd), ErrorCode.MANUAL_ACTION_REQUIRED)
                    if approved is None: self._write('approved.json', {'confirmation': digest(profile)})
                    with scope.acquire(confirmed=True) as held:
                        held.assert_held()
                        lease = {'confirmation': digest(profile), 'lease_id': held.lease_id}
                        self._write('lease.json', lease)
                    # Closing the descriptor deliberately keeps the durable maintenance gate.
                else:
                    require(observed == {'state': 'MAINTENANCE_REQUIRED', 'instance': profile['instance'],
                                         'lease_id': lease['lease_id']}, ErrorCode.MANUAL_ACTION_REQUIRED)
                value = deepcopy(draft['configuration'])
                value.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'})
                value['database']['mode'] = 'existing_local'
                value['secrets'] = {'database_password': credentials['database_password'], 'admin_password': '', 'openai_api_key': ''}
                authority = native.d.SqlAuthorityCredentials(credentials['authority_user'], credentials['authority_password'])
                runtime = replace(fresh.runtime(), timeout_seconds=120)
                source = AcquireOperation(self.parent.journal.path.parent, 'web',
                    SourceSpec(native.p.WEB_REPOSITORY, fresh.source_commit, fresh.source_commit), None).path / 'tree'
                operation = native.ProvisionedBackup(runtime, source, http, SessionCleaner(http))
                report = operation.create_and_verify(value, authority, config_root=fresh.config_root, backup_root=backups,
                    confirmed=True, allow_global_read_lock=True, recover_lease_id=lease['lease_id']).report()
                require(self.receipt(profile, lease) == report, ErrorCode.INVALID_STATE)
            except Exception:
                self.last_error = ErrorCode.MANUAL_ACTION_REQUIRED.value
                raise InstallerError(ErrorCode.MANUAL_ACTION_REQUIRED) from None
            return self.state()
