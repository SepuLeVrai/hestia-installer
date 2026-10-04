"""Closed Gateway file transitions. A compatibility report is never authority.

No native observation, subprocess, key read, migration or network request occurs
here. A schema number alone cannot make a target understand the current config.
"""
from dataclasses import dataclass, field

from installer.gateway_release import FCM_COMMIT, release, sha
from installer.gateway_service_profile import GatewayServiceProfile
from installer.model import ErrorCode, canonical_bytes, require, strict_json_loads

LEGACY_COMMIT = 'e2c09f53593bf316906ccc4387f185e73e7f85a8'
COMMITS = (LEGACY_COMMIT, FCM_COMMIT)
MIGRATIONS = {
    '001_foundation.sql': '1a1ac748be1a98dec6e98a7ba16720b19c041d46',
    '002_enrollment.sql': 'd0b6acdca7521d531328e91aed0206fc9852a837',
    '003_passwordless.sql': 'c5ea28525fb60ea357eb987a65bb0ff0cbb313b4',
    '004_contexts.sql': '02520b56c96a40247693112780699293c116a59f',
    '005_project_push.sql': 'f4e6d7ce3b84e5f2e73821c92d219c76b6e180ef',
    '006_dpop.sql': 'f799d585c9715f758409c40c5d048a42ef318638',
}
PRECONDITIONS = (
    'EXACT_SOURCE_AND_TARGET_PACKAGES', 'CURRENT_SOURCE_SERVICE_AND_IDENTITIES',
    'COMPOSED_BACKUP_VERIFIED', 'BOUND_MAINTENANCE_AND_STOPPED_GATEWAY',
    'SQLITE_SCHEMA_AND_INSTALLATION_UUID_VERIFIED', 'DURABLE_EFFECT_INTENT',
    'TARGET_SERVICE_AND_CONTEXTS_VERIFIED', 'BOOT_BINDING_REQUALIFIED',
)


@dataclass(frozen=True)
class TransitionAssessment:
    _raw: bytes = field(repr=False)

    @property
    def sha256(self): return sha(self._raw)

    def report(self): return strict_json_loads(self._raw)


def assess(source, *, target_commit, direction):
    """Only preserve-config transitions between the two measured pins exist.

    Enabling/disabling FCM or adding DEV is a different, separately consented
    transition. In particular this function must not remove FCM to make N-1 fit.
    """
    require(type(source) is GatewayServiceProfile, ErrorCode.INCOMPATIBLE_STATE)
    require(type(target_commit) is str and target_commit in COMMITS, ErrorCode.INCOMPATIBLE_STATE)
    require(type(direction) is str and direction in ('upgrade', 'rollback'), ErrorCode.INVALID_DATA)
    current = source.selected_release
    require(type(current) is dict and current.get('commit') in COMMITS
            and current == release(current['commit']), ErrorCode.INCOMPATIBLE_STATE)
    # Round-trip the whole binding; never accept a caller's altered port, path,
    # identity, FCM receipt, source hash, or release metadata as an observation.
    source = GatewayServiceProfile.from_binding(source.foundation, source.binding())
    target_release = release(target_commit)
    blockers = []
    if current['commit'] == target_commit:
        blockers.append('NO_VERSION_TRANSITION')
    elif (direction == 'upgrade') != (target_commit == FCM_COMMIT):
        blockers.append('TRANSITION_DIRECTION_MISMATCH')
    if source.push is not None and target_commit != FCM_COMMIT:
        blockers.append('TARGET_FCM_PROFILE_UNSUPPORTED')
    target = None
    if not blockers:
        candidate = GatewayServiceProfile(source.foundation, source.identity, source.key_directory,
            release_commit=target_commit, push=source.push)
        require(candidate.configuration() == source.configuration()
                and candidate.unit_bytes() == source.unit_bytes()
                and candidate.account.instance == source.account.instance
                and candidate.state == source.state, ErrorCode.INCOMPATIBLE_STATE)
        target = candidate.binding()
    report = {
        'version': 1, 'policy': 'GATEWAY_PRESERVE_PROFILE_V1', 'direction': direction,
        'source': source.binding(), 'target_release': target_release, 'target': target,
        'configuration_compatible': not blockers, 'blockers': blockers,
        'sqlite': {'schema': 6, 'migration_blobs': MIGRATIONS, 'migrations_to_apply': [],
                   'restore_snapshot': False, 'reset_technical_state': False},
        'preserve': ['web_instance', 'gateway_identity', 'service_account', 'main_key',
                     'dev_target_and_key', 'fcm_credential', 'sqlite_data', 'installation_uuid',
                     'revocations', 'sessions', 'quotas', 'maintenance_gate'],
        'required_before_effect': list(PRECONDITIONS),
        'apply_allowed': False, 'source_host_verified': False, 'target_package_verified': False,
        'backup_verified': False, 'maintenance_held': False, 'rollback_verified': False,
        'restore_to_original_allowed': False, 'phase6_complete': False,
    }
    return TransitionAssessment(canonical_bytes(report))
