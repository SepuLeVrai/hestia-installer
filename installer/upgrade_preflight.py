"""5C1: read-only assessment of a known sealed Web, NOT an upgrade executor.

No public registration, migration credential, durable journal, backup or mutation.
The digest identifies an observation; it is neither a signature nor permission
for a later apply. Every future mutation must revalidate its own prerequisites.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import hmac
import os
import re
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from installer import finalization as f
from installer import database_config as fs
from installer import php_transport as p
from installer.model import strict_json_loads

# One measured source profile, not a claim that all 3.x or legacy 2.x is supported.
WEB_COMMIT = '46c03060625d4d53c675474b11aaa33007d9aad7'
RUNTIME_SHA256 = 'b2205c6f7b326b0692e57942f282260669e3ec147daf3dc9f18c546782c7eb0f'
COUNTS = frozenset({'users', 'roles', 'rbac_rules', 'rbac_overrides', 'web_sessions', 'settings', 'active_admins'})
SQL_ERRORS = frozenset({'REQUEST_INVALID', 'SQL_TARGET_INVALID', 'SQL_CA_INVALID', 'SQL_CREDENTIAL_INVALID',
    'SQL_DRIVER_REQUIRED', 'SQL_CONNECTION_FAILED', 'SQL_TLS_CONNECTION_FAILED', 'ACCOUNT_POLICY_REJECTED',
    'AUDIT_UNAVAILABLE', 'UPGRADE_SCHEMA_LIMIT', 'UPGRADE_SCHEMA_CHANGED', 'UPGRADE_SOURCE_VERSION_UNSUPPORTED',
    'UPGRADE_REQUIRED_TABLES_MISSING', 'UPGRADE_NO_ACTIVE_ADMIN', 'UPGRADE_INVENTORY_FAILED'})


class UpgradePreflightError(RuntimeError):
    """Closed diagnostic only; never append paths, SQL, input or PDO messages."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise UpgradePreflightError(code)


@dataclass(frozen=True)
class UpgradeAssessment:
    """Immutable canonical bytes; each report() is a detached, non-secret copy."""
    _canonical: bytes = field(repr=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._canonical).hexdigest()

    def report(self) -> dict:
        return {'state': 'UPGRADE_PREFLIGHT_READY', 'code': 'OK', 'plan_sha256': self.sha256,
                'plan': strict_json_loads(self._canonical)}


def _response(code: int, raw: bytes, request: dict) -> dict:
    try:
        _require(type(code) is int and type(raw) is bytes and len(raw) <= p.MAX_OUTPUT, 'PROTOCOL_REJECTED')
        value = strict_json_loads(raw)
        _require(type(value) is dict and set(value) == {'version', 'operation', 'request_id', 'ok', 'result', 'error'}
                 and type(value['version']) is int and value['version'] == 1 and type(value['ok']) is bool
                 and value['operation'] == 'inventory' and value['request_id'] == request['request_id'], 'PROTOCOL_REJECTED')
        if not value['ok']:
            _require(code == 20 and value['result'] is None and type(value['error']) is str
                     and value['error'] in SQL_ERRORS, 'PROTOCOL_REJECTED')
            raise UpgradePreflightError(value['error'])
        _require(code == 0 and value['error'] is None, 'PROTOCOL_REJECTED')
        result = value['result']
        _require(type(result) is dict and set(result) == {'release', 'server_version', 'schema_sha256', 'tables',
            'views', 'non_innodb_tables', 'columns', 'counts', 'assistant_setting', 'read_only'}, 'PROTOCOL_REJECTED')
        _require(result['release'] == p.ENGINE_VERSION and result['read_only'] is True
                 and type(result['assistant_setting']) is bool, 'PROTOCOL_REJECTED')
        _require(type(result['server_version']) is str
                 and re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}', result['server_version']) is not None,
                 'PROTOCOL_REJECTED')
        _require(type(result['schema_sha256']) is str and re.fullmatch(r'[a-f0-9]{64}', result['schema_sha256']) is not None,
                 'PROTOCOL_REJECTED')
        for name, maximum in (('tables', 512), ('views', 512), ('non_innodb_tables', 512), ('columns', 16384)):
            _require(type(result[name]) is int and 0 <= result[name] <= maximum, 'PROTOCOL_REJECTED')
        _require(6 <= result['tables'] and result['tables'] + result['views'] <= 512
                 and result['non_innodb_tables'] <= result['tables'] and result['columns'] >= 6, 'PROTOCOL_REJECTED')
        _require(type(result['counts']) is dict and set(result['counts']) == COUNTS, 'PROTOCOL_REJECTED')
        for count in result['counts'].values():
            _require(type(count) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}', count) is not None
                     and int(count) <= 18446744073709551615, 'PROTOCOL_REJECTED')
        _require(0 < int(result['counts']['active_admins']) <= int(result['counts']['users']), 'PROTOCOL_REJECTED')
        return result
    except UpgradePreflightError:
        raise
    except Exception:
        raise UpgradePreflightError('PROTOCOL_REJECTED') from None


def _inventory(runtime: p.PhpRuntime, source: Path, database: dict, ca: bytes | None, cancel=None) -> dict:
    with tempfile.TemporaryDirectory(prefix='upgrade-inspect-', dir=runtime.run_root) as tmp:
        stage = Path(tmp)
        p._copy_bundle(source, stage, runtime.worker_gid, f.ENGINE_FILES, f.ENGINE_SHA256, 'upgrade_inventory_bridge.php')
        with fs._directory(stage) as fd:
            f._write(fd, 'sql_accounts_policy.php', p._read_file(Path(__file__).parent / 'private/sql_accounts_policy.php'), runtime.worker_gid)
            if ca is not None:
                f._write(fd, 'ca.pem', ca, runtime.worker_gid)
        target = {k: database[k] for k in ('host', 'port', 'name', 'tls_required', 'tls_ca_file', 'tls_ca_sha256')}
        if ca is not None:
            target['tls_ca_file'] = str(stage / 'ca.pem')
        request = {'version': 1, 'operation': 'inventory', 'request_id': os.urandom(16).hex(), 'target': target,
                   'application': {'user': database['user'], 'password': database['password']}}
        code, raw = p._exchange(p._command(runtime, stage), p._json(request), stage, runtime.timeout_seconds, cancel)
        return _response(code, raw, request)


def _shared_file(conf: int, name: str, gid: int, stack: ExitStack, *, mode: int, limit: int) -> bytes:
    """Existing file only, no lock-file creation and no filesystem mutation."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=conf)
    stack.callback(os.close, fd)
    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    data = f._read(conf, name, gid, mode=mode, limit=limit)
    opened, named = os.fstat(fd), os.stat(name, dir_fd=conf, follow_symlinks=False)
    _require((opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino), 'UPGRADE_TARGET_CHANGED')
    return data


class UpgradePreflight:
    """Private trusted-host API for 5B2.3 instances only. No apply method."""
    def __init__(self, runtime: p.PhpRuntime, source: Path, *, repository: str, commit: str):
        _require(repository == p.WEB_REPOSITORY and commit == WEB_COMMIT
                 and f.WEB_COMMIT == WEB_COMMIT and f.RUNTIME_SHA256 == RUNTIME_SHA256, 'SOURCE_PIN_MISMATCH')
        self.runtime, self.source = runtime, Path(source)

    def inspect(self, payload: dict, *, config_root: Path, cancel=None) -> UpgradeAssessment:
        try:
            # Do not silently normalize a settings/fresh request into an upgrade.
            _require(type(payload) is dict and payload.get('mode') == 'upgrade'
                     and payload.get('assistant') == {'action': 'preserve'}, 'UPGRADE_INPUT_REJECTED')
            value = copy.deepcopy(payload)
            config = f._configuration(value, fresh=False)
        except UpgradePreflightError:
            raise
        except Exception:
            raise UpgradePreflightError('UPGRADE_INPUT_REJECTED') from None
        try:
            with ExitStack() as stack:
                _require(cancel is None or not cancel.is_set(), 'INTERRUPTED')
                gid, web, directory, conf, webfd, inc = f._open(self.runtime, config, config_root, stack)
                source = f.FinalizationStep(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=WEB_COMMIT)
                source._sources(web)
                database, loader, ca = f._prepared(config, value, directory, conf, gid)
                receipt = f._completed(conf, webfd, inc, gid)
                # Serialize with a settings controller when its stable lock exists.
                try:
                    _require(_shared_file(conf, 'assistant-edit.lock', 0, stack, mode=0o600, limit=0) == b'',
                             'UPGRADE_TARGET_CHANGED')
                except FileNotFoundError:
                    pass
                source._pending_edits(conf)
                journal_names = sorted(os.listdir(conf))
                secret = _shared_file(conf, 'assistant.json', gid, stack, mode=0o660, limit=2048)
                assistant = strict_json_loads(secret)
                _require(type(assistant) is dict and set(assistant) == {'version', 'openai_api_key'}
                         and type(assistant['version']) is int and assistant['version'] == 1
                         and type(assistant['openai_api_key']) is str and p._json(assistant) == secret,
                         'UPGRADE_ASSISTANT_INVALID')
                key = assistant['openai_api_key']
                _require(key == '' or (re.fullmatch(r'[A-Za-z0-9_.-]{20,500}', key) is not None
                         and key.upper() not in ('COLLER_LA_CLE_OPENAI_ICI', 'YOUR_OPENAI_API_KEY_HERE')),
                         'UPGRADE_ASSISTANT_INVALID')
                before = f._probe(self.runtime, config, directory, gid, active=True, cancel=cancel)
                _require(before['key_configured'] is bool(key), 'UPGRADE_ASSISTANT_INVALID')
                inventory = _inventory(self.runtime, self.source, database, ca, cancel)
                after = f._probe(self.runtime, config, directory, gid, active=True, cancel=cancel)
                _require(before == after and inventory['assistant_setting'] is after['setting_enabled'], 'UPGRADE_TARGET_CHANGED')
                source._sources(web)
                _require(f._prepared(config, value, directory, conf, gid) == (database, loader, ca)
                         and f._completed(conf, webfd, inc, gid) == receipt
                         and hmac.compare_digest(secret, f._read(conf, 'assistant.json', gid, mode=0o660, limit=2048))
                         and sorted(os.listdir(conf)) == journal_names, 'UPGRADE_TARGET_CHANGED')
                source._pending_edits(conf)
                _require(cancel is None or not cancel.is_set(), 'INTERRUPTED')
                plan = {'version': 1, 'operation': 'upgrade_assessment', 'apply_allowed': False,
                    'source_profile': 'SEALED_5B23', 'source_commit': WEB_COMMIT, 'target_commit': WEB_COMMIT,
                    'runtime_sha256': RUNTIME_SHA256, 'target_relation': 'IDENTICAL_RELEASE',
                    'web': config['web'], 'database': {k: database[k] for k in ('host', 'port', 'name', 'user', 'tls_required')},
                    'inventory': inventory, 'assistant': {'setting_enabled': after['setting_enabled'],
                        'key_configured': after['key_configured'], 'api_access': 'NOT_TESTED'},
                    'migration_catalog': 'NOT_DELIVERED', 'backup_verified': False, 'rollback_verified': False,
                    'preservation_verified': False, 'application_installed': False, 'system_qualification_required': True,
                    'required_next_steps': ['PRIVATE_BACKUP_AND_RESTORE_VERIFICATION', 'CONTROLLED_UPGRADE', 'RESUME_AND_ROLLBACK'],
                    'limitations': ['APPLICATION_ACCOUNT_VISIBILITY_ONLY', 'POINT_IN_TIME_NOT_MAINTENANCE_LOCK',
                        'SCHEMA_FINGERPRINT_NOT_FULL_DDL', 'NO_LEGACY_ADOPTION', 'PLAN_NOT_AN_APPLY_CAPABILITY']}
                return UpgradeAssessment(p._json(plan))
        except UpgradePreflightError:
            raise
        except Exception:
            raise UpgradePreflightError('UPGRADE_INSPECTION_UNAVAILABLE') from None
