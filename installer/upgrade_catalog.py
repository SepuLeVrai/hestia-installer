"""5C3a: closed build catalogue, never an execution or host-adoption capability.

The two measured builds share a SQL lineage. Their storage change still needs
its own source backup profile and relocation/cutover qualification. Listing a
candidate must not silently turn that missing qualification into permission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json

from installer.web_releases import LEGACY_COMMIT, STORAGE_COMMIT, get_release

REPOSITORY = 'SepuLeVrai/hestia-nexus-avv'
VERSION = '3.0.0.0-stable-20260914'
SCHEMA_BLOB = '17e55373cb3033ad7c2a1779e8e29c47c5e90579'
MIGRATIONS_TREE = 'ebce0d11a07693f00c7f1cb90e08865517410870'
MIGRATION_FILES = 113
VERSION_BLOB = '94e959167a2f890a18bcd9063be777abc8f78d4a'
CORE_BLOB = '1695eb07ddc7c5826687e30fd10ff56937395c38'
FRESH_BLOB = 'ac863b697455d04c6162781ed6743c193db903e3'


class UpgradeCatalogError(RuntimeError):
    """Fixed code only; rejected input is never copied into the diagnostic."""


def _encode(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')


@dataclass(frozen=True)
class CatalogAssessment:
    """Immutable catalogue observation; its digest is not an authorization."""
    _encoded: bytes = field(repr=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._encoded).hexdigest()

    def report(self) -> dict:
        return json.loads(self._encoded)


def _release(commit: str) -> dict:
    # Growing the fresh-release registry must never implicitly grow upgrade
    # support or assign this measured SQL lineage to a future build.
    if type(commit) is not str or commit not in (LEGACY_COMMIT, STORAGE_COMMIT):
        raise UpgradeCatalogError('CATALOG_SOURCE_PIN_MISMATCH')
    try:
        release = get_release(commit)
    except ValueError:
        raise UpgradeCatalogError('CATALOG_SOURCE_PIN_MISMATCH') from None
    return {'commit': release.commit, 'tree': release.tree, 'files': release.files,
            'runtime_sha256': release.runtime_sha256, 'app_version': VERSION,
            'storage_layout': 'EXTERNAL_UPLOADS' if release.external_uploads else 'IN_WEBROOT_UPLOADS'}


def assess_transition(*, repository: str, source_commit: str, target_commit: str) -> CatalogAssessment:
    """Classify exact known pins without opening a file, connecting or mutating.

    Branches, tags, version strings and caller-provided migration lists are not
    selectors. This report says nothing about the identity of a deployed host.
    """
    if type(repository) is not str or repository != REPOSITORY:
        raise UpgradeCatalogError('CATALOG_SOURCE_PIN_MISMATCH')
    source, target = _release(source_commit), _release(target_commit)
    if source_commit == target_commit:
        relation = 'IDENTICAL_RELEASE'
        blockers = ['NO_VERSION_TRANSITION']
        storage_change = 'NONE'
    elif source_commit == LEGACY_COMMIT and target_commit == STORAGE_COMMIT:
        relation = 'STORAGE_LAYOUT_CHANGE_SAME_SQL_LINEAGE'
        storage_change = 'IN_WEBROOT_TO_EXTERNAL_UPLOADS'
        blockers = ['SOURCE_BACKUP_PROFILE_NOT_QUALIFIED', 'DATA_RELOCATION_NOT_IMPLEMENTED',
                    'COHERENT_CUTOVER_NOT_IMPLEMENTED', 'TRANSITION_RECOVERY_NOT_QUALIFIED']
    else:
        relation = 'REVERSE_STORAGE_TRANSITION_UNSUPPORTED'
        storage_change = 'EXTERNAL_TO_IN_WEBROOT_UPLOADS'
        blockers = ['DOWNGRADE_NOT_CATALOGUED']
    return CatalogAssessment(_encode({
        'version': 1, 'scope': 'RELEASE_CATALOG_ONLY', 'repository': REPOSITORY,
        'source': source, 'target': target, 'relation': relation,
        'storage_change': storage_change,
        'sql_lineage': {'schema_blob': SCHEMA_BLOB, 'migrations_tree': MIGRATIONS_TREE,
                        'migration_files': MIGRATION_FILES, 'version_blob': VERSION_BLOB,
                        'core_blob': CORE_BLOB, 'fresh_blob': FRESH_BLOB,
                        'published_sql_delta': False, 'selected_migrations': []},
        'blockers': blockers, 'apply_allowed': False, 'transition_supported': False,
        'source_host_verified': False, 'target_files_verified': False,
        'backup_verified': False, 'maintenance_held': False,
        'migration_verified': False, 'cutover_verified': False, 'rollback_verified': False,
        'phase5c3_complete': False, 'application_installed': False,
        'limitations': ['SOURCE_METADATA_NOT_A_HOST_INSPECTION',
                        'MIGRATION_INVENTORY_NOT_AN_EXECUTION_ORDER',
                        'SAME_APP_VERSION_NOT_SCHEMA_COMPATIBILITY',
                        'REPORT_NOT_AN_APPLY_CAPABILITY'],
    }))
