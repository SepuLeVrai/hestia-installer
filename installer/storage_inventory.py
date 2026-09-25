"""Read-only requirements map for the pinned Web, never a backup permission.

Host observations are explicit trusted input, not browser input or proof of
service wiring. No deployed PHP configuration is evaluated, no SQL is queried,
and no path is probed, created, chmodded or silently dropped by this module.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
import re

from installer import finalization as f
from installer import php_transport as p
from installer.model import strict_json_loads

ENVIRONMENT = frozenset({'HESTIA_IMPORT_STORAGE', 'HESTIA_GED_LEGACY_ROOTS', 'HESTIA_AI_CONFIG_FILE',
                         'HESTIA_MOBILE_RELEASE_DIR', 'HESTIA_MOBILE_FOUNDATION_CONFIG'})
CONSTANTS = frozenset({'HESTIA_IMPORT_STORAGE'})
APP_CONFIG = frozenset({'security.ged_legacy_roots', 'HESTIA_MOBILE_RELEASE_DIR'})
PHP = frozenset({'session.save_handler', 'session.save_path', 'session.gc_maxlifetime',
                 'effective_sys_temp_dir', 'upload_tmp_dir', 'error_log'})
# Every class remains REQUIRED, including when no cron job was observed. A
# guessed empty scheduler list is not evidence that another producer is absent.
PRODUCERS = {
    'public_php': ('request_guard', 'request_and_shutdown_drain'),
    'internal_mobile_php': ('request_guard', 'internal_listener_drain'),
    'php_upload_staging': ('front_server_admission', 'multipart_and_fpm_drain'),
    'native_session_cleaner': ('shared_maintenance_lock', 'static_php_session_configuration'),
    'cli_admin_and_mobile': ('shared_maintenance_lock', 'privileged_configuration_coordination'),
    'converter_children': ('service_process_group', 'orphan_process_drain'),
    'installer_settings': ('maintenance_coordination', 'private_envelope_recheck'),
    'host_schedulers': ('actual_cron_and_units_inventory', 'all_managed_writers_drain'),
    'other_sql_writers': ('database_writer_inventory', 'maintenance_window_coordination'),
}
SHARED_DIRECTORIES = frozenset({'/', '/tmp', '/var/tmp', '/var/lib/php/sessions', '/var/lib/hestia-ai'})


class StorageInventoryError(RuntimeError):
    """Closed diagnostic, no observation value or underlying path in the text."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise StorageInventoryError(code)


def _text(value) -> str:
    require(type(value) is str and len(value.encode('utf-8')) <= 4096
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'STORAGE_VALUE_REJECTED')
    return value


def _absolute(value: str) -> str:
    _text(value)
    require(value.startswith('/') and not value.startswith('//') and len(value.encode()) <= 2048
            and (value == '/' or all(part not in ('', '.', '..') for part in value.split('/')[1:])),
            'STORAGE_PATH_REJECTED')
    return value


def _mapping(value, names, *, nullable=False):
    require(type(value) is dict and set(value) == names, 'STORAGE_OBSERVATIONS_INCOMPLETE')
    return {key: None if nullable and item is None else _text(item) for key, item in value.items()}


def _verify_source(source: Path, commit: str = f.WEB_COMMIT) -> None:
    require(f._runtime_digest(source) == f.get_release(commit).runtime_sha256, 'SOURCE_PIN_MISMATCH')


@dataclass(frozen=True)
class StorageFacts:
    """Observed effective configuration, with required explicit empty values.

    None for import constant means not defined; empty means defined but empty.
    None for AI selection/usage means unresolved, never 'feature disabled'.
    PHP effective_sys_temp_dir means actual sys_get_temp_dir(), not an assumed
    /tmp or merely the spelling of sys_temp_dir in one INI file.
    """
    webroot: Path = field(repr=False)
    configuration: Path = field(repr=False)
    environment: dict = field(repr=False)
    constants: dict = field(repr=False)
    app_config: dict = field(repr=False)
    php: dict = field(repr=False)
    ai_configuration_file: str | None = field(default=None, repr=False)
    ai_usage_directory: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class StorageRequirements:
    _canonical: bytes = field(repr=False)

    def private_manifest(self) -> dict:
        """Contains private paths. Never return this from an HTTP/UI endpoint."""
        return strict_json_loads(self._canonical)

    def report(self) -> dict:
        value = self.private_manifest()
        return {'state': 'STORAGE_REQUIREMENTS_RESOLVED', 'source_commit': value['source_commit'],
                'manifest_sha256': hashlib.sha256(self._canonical).hexdigest(),
                'storage_scopes': len(value['scopes']), 'producer_groups': len(value['producers']),
                'blockers': value['blockers'], 'facts_origin': 'TRUSTED_HOST_OBSERVATIONS',
                'filesystem_verified': False, 'storage_inventory_complete': False,
                'system_wiring_verified': False, 'complete_web_backup': False,
                'apply_allowed': False, 'rollback_verified': False, 'application_installed': False}


class StorageInventory:
    def __init__(self, source: Path, *, repository: str, commit: str):
        require(repository == p.WEB_REPOSITORY, 'SOURCE_PIN_MISMATCH')
        try: self.release = f.get_release(commit)
        except ValueError: raise StorageInventoryError('SOURCE_PIN_MISMATCH') from None
        require(isinstance(source, Path), 'STORAGE_INPUT_REJECTED')
        self.source = source

    def inspect(self, facts: StorageFacts) -> StorageRequirements:
        try:
            require(type(facts) is StorageFacts, 'STORAGE_INPUT_REJECTED')
            _verify_source(self.source, self.release.commit)
            value = copy.deepcopy(facts)
            require(isinstance(value.webroot, Path) and isinstance(value.configuration, Path), 'STORAGE_INPUT_REJECTED')
            web, conf = _absolute(str(value.webroot)), _absolute(str(value.configuration))
            require(web != conf and not web.startswith(conf + '/') and not conf.startswith(web + '/')
                    and web != '/' and conf != '/', 'STORAGE_PATH_REJECTED')
            env = _mapping(value.environment, ENVIRONMENT | ({'HESTIA_UPLOAD_STORAGE'} if self.release.external_uploads else set()))
            constants = _mapping(value.constants, CONSTANTS, nullable=True)
            config = _mapping(value.app_config, APP_CONFIG)
            php = _mapping(value.php, PHP)
            scopes = []
            blockers = {'FILESYSTEM_OBSERVATION_REQUIRED', 'SERVICE_WIRING_REQUIRED',
                        'EXTERNAL_PRODUCERS_AUDIT_REQUIRED', 'IMMUTABLE_WEB_DATA_PROFILE_REQUIRED'}

            def add(role, path, kind, origin, producers):
                require(len(scopes) < 64, 'STORAGE_SCOPE_LIMIT')
                path = _absolute(path)
                scopes.append({'role': role, 'path': path, 'kind': kind, 'origin': origin,
                               'producers': list(producers), 'presence': 'UNVERIFIED'})
                if kind == 'directory' and path in SHARED_DIRECTORIES:
                    blockers.add('SHARED_STORAGE:' + role)

            add('uploads', web + '/uploads', 'directory', 'WEB_FIXED',
                ('public_php', 'internal_mobile_php'))
            uploads = web + '/uploads'
            if self.release.external_uploads and env['HESTIA_UPLOAD_STORAGE']:
                uploads = _absolute(env['HESTIA_UPLOAD_STORAGE'])
                require(uploads not in ('/', '/tmp', '/var/tmp', '/var/lib') and uploads != web
                        and not uploads.startswith(web + '/') and not web.startswith(uploads + '/'),
                        'STORAGE_UPLOAD_PATH_REJECTED')
                add('uploads_effective', uploads, 'directory', 'DEPLOYMENT_ENVIRONMENT',
                    ('public_php', 'internal_mobile_php'))
            add('imports_default', web + '/var/imports', 'directory', 'WEB_DEFAULT_OR_RETAINED_DATA',
                ('public_php',))
            configured = constants['HESTIA_IMPORT_STORAGE']
            if configured is None:
                configured = env['HESTIA_IMPORT_STORAGE']
            imports = configured.rstrip('/\\') if configured else web + '/var/imports'
            add('imports_effective', imports, 'directory', 'PHP_CONSTANT_THEN_ENVIRONMENT_THEN_WEB', ('public_php',))
            # A shadowed configured directory can still hold historical data.
            if env['HESTIA_IMPORT_STORAGE']:
                add('imports_environment', env['HESTIA_IMPORT_STORAGE'].rstrip('/\\'), 'directory',
                    'CONFIGURED_CANDIDATE', ('public_php',))
            for field, source, external in (('HESTIA_GED_LEGACY_ROOTS', env, True),
                                             ('security.ged_legacy_roots', config, False)):
                raw = source[field].strip(' ')
                if not raw:
                    continue
                roots = raw.split(',')
                require(len(roots) <= 16, 'STORAGE_SCOPE_LIMIT')
                for index, root in enumerate(roots):
                    root = root.strip(' ')
                    if external:
                        add('ged_external_' + str(index), root.rstrip('/'), 'directory', 'ENVIRONMENT',
                            ('public_php', 'host_schedulers'))
                    else:
                        root = root.replace('\\', '/')
                        require(re.fullmatch(r'[A-Za-z0-9._/-]+', root) is not None
                                and all(part not in ('', '.', '..') for part in root.split('/')),
                                'STORAGE_GED_PATH_REJECTED')
                        add('ged_legacy_' + str(index), uploads + '/ged_legacy/' + root,
                            'directory', 'APP_CONFIG_RELATIVE', ('public_php',))

            require(php['session.save_handler'] == 'files', 'STORAGE_SESSION_HANDLER_UNSUPPORTED')
            session = php['session.save_path']
            if ';' in session:
                match = re.fullmatch(r'0;(?:0?[0-7]{3};)?(/.+)', session)
                require(match is not None, 'STORAGE_SESSION_LAYOUT_UNSUPPORTED')
                session = match.group(1)
            add('sessions', session, 'directory', 'EFFECTIVE_PHP_SESSION_SAVE_PATH',
                ('public_php', 'native_session_cleaner'))
            if php['session.gc_maxlifetime'] != '43200':
                blockers.add('SESSION_RETENTION_MISMATCH')
            temporary = _absolute(php['effective_sys_temp_dir'].rstrip('/'))
            add('temporary', temporary, 'directory', 'EFFECTIVE_SYS_GET_TEMP_DIR',
                ('public_php', 'internal_mobile_php', 'converter_children', 'host_schedulers'))
            upload_tmp = php['upload_tmp_dir'].rstrip('/') if php['upload_tmp_dir'] else temporary
            add('upload_temporary', upload_tmp, 'directory', 'PHP_UPLOAD_TMP_OR_SYSTEM_FALLBACK', ('php_upload_staging',))
            # PHP may fall back to the system temp directory when upload_tmp_dir
            # is unavailable. Both remain listed, even when currently distinct.
            add('reference_import_fallback', temporary + '/hestia-reference-import-' + hashlib.sha256(web.encode()).hexdigest()[:16],
                'directory', 'WEB_FALLBACK', ('public_php',))
            add('portability_temporary', temporary + '/hestia-portability', 'directory', 'WEB_FIXED_SUFFIX', ('public_php',))
            add('pairing_temporary', temporary + '/hestia-pair-' + hashlib.sha256(web.encode()).hexdigest()[:20],
                'directory', 'WEB_FIXED_SUFFIX', ('public_php',))
            if php['error_log'] and php['error_log'] != 'syslog':
                add('php_error_log', php['error_log'], 'file', 'PHP_ERROR_LOG', ('public_php', 'cli_admin_and_mobile'))
            else:
                blockers.add('SYSTEM_LOG_ROUTING_UNVERIFIED')

            mobile = config['HESTIA_MOBILE_RELEASE_DIR'].strip(' ') or env['HESTIA_MOBILE_RELEASE_DIR'].strip(' ')
            if mobile:
                mobile = _absolute(mobile)
                require(mobile != web and not mobile.startswith(web + '/') and not web.startswith(mobile.rstrip('/') + '/'),
                        'STORAGE_MOBILE_PUBLIC_PATH_REJECTED')
                add('mobile_releases_effective', mobile, 'directory', 'APP_CONFIG_THEN_ENVIRONMENT',
                    ('public_php', 'cli_admin_and_mobile'))
            if env['HESTIA_MOBILE_RELEASE_DIR'].strip(' '):
                add('mobile_releases_environment', env['HESTIA_MOBILE_RELEASE_DIR'].strip(' '), 'directory',
                    'CONFIGURED_CANDIDATE', ('cli_admin_and_mobile',))
            if env['HESTIA_MOBILE_FOUNDATION_CONFIG'].strip(' '):
                add('mobile_foundation_configuration', env['HESTIA_MOBILE_FOUNDATION_CONFIG'].strip(' '), 'file',
                    'ENVIRONMENT', ('cli_admin_and_mobile',))
            add('managed_configuration', conf, 'envelope', 'SEALED_INSTANCE_CONFIGURATION',
                ('public_php', 'installer_settings'))
            for role, path in (('ai_system_configuration', '/etc/hestia/conf_db_ia.php'),
                               ('ai_legacy_configuration', web + '/includes/conf_db_ia.php')):
                add(role, path, 'file', 'WEB_CONFIGURATION_CANDIDATE', ('cli_admin_and_mobile',))
            if env['HESTIA_AI_CONFIG_FILE'].strip(' '):
                add('ai_environment_configuration', env['HESTIA_AI_CONFIG_FILE'].strip(' '), 'file',
                    'ENVIRONMENT', ('cli_admin_and_mobile',))
            if value.ai_configuration_file is None:
                blockers.add('AI_CONFIGURATION_SELECTION_UNOBSERVED')
            else:
                add('ai_effective_configuration', value.ai_configuration_file, 'file', 'OBSERVED_PHP_CONFIGURATION',
                    ('cli_admin_and_mobile',))
            if value.ai_usage_directory is None:
                blockers.add('AI_USAGE_DIRECTORY_UNOBSERVED')
            else:
                add('ai_usage', value.ai_usage_directory, 'directory', 'OBSERVED_PHP_CONFIGURATION',
                    ('public_php', 'cli_admin_and_mobile'))
            # Retain every semantic role. This relation is informational; it
            # never turns overlapping or aliased paths into a backup inventory.
            for scope in scopes:
                candidates = [other for other in scopes if other['role'] != scope['role']
                              and other['kind'] in ('directory', 'envelope')
                              and scope['path'].startswith(other['path'].rstrip('/') + '/')]
                scope['covered_by'] = sorted(other['role'] for other in candidates)
            result = {'version': 1, 'source_commit': self.release.commit, 'runtime_sha256': self.release.runtime_sha256,
                      'scopes': sorted(scopes, key=lambda x: x['role']),
                      'producers': [{'group': group, 'requirements': list(items), 'state': 'REQUIRED_NOT_VERIFIED'}
                                    for group, items in sorted(PRODUCERS.items())],
                      'blockers': sorted(blockers)}
            _verify_source(self.source, self.release.commit)
            return StorageRequirements(p._json(result))
        except StorageInventoryError:
            raise
        except Exception:
            raise StorageInventoryError('STORAGE_INSPECTION_UNAVAILABLE') from None
