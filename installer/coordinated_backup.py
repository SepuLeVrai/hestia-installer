"""Compose SQL/envelope and registered external data proofs under one lease.

Not an exhaustive storage-discovery service, upgrade permission or source restore.
All writers must obey the maintenance contract; actual service wiring is a 5D gate.
"""
from __future__ import annotations

import copy
import os
import pwd
import shutil
import stat
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from installer import backup_files as files
from installer import backup_runtime as br
from installer import database_config as fs
from installer import database_step as d
from installer import finalization as f
from installer import maintenance as m
from installer import php_transport as p
from installer import upgrade_backup as sql
from installer.model import strict_json_loads


class CoordinatedBackupError(RuntimeError):
    """Closed non-secret error, never the underlying SQL/path/credential text."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise CoordinatedBackupError(code)


@dataclass(frozen=True)
class CoordinatedVerification:
    _canonical: bytes = field(repr=False)

    def report(self) -> dict:
        return strict_json_loads(self._canonical)


def _held(lease: m.MaintenanceLease) -> None:
    require(type(lease) is m.MaintenanceLease, 'COORDINATED_MAINTENANCE_REQUIRED')
    try:
        lease.assert_held()
    except Exception:
        raise CoordinatedBackupError('COORDINATED_MAINTENANCE_REQUIRED') from None


def _recheck(runtime, source, database, ca, authority, slot, expected, cancel):
    """Second real read-only export under the same bounded global-read-lock policy.

    It uses the existing canonical exporter, not a weaker row-count comparison.
    Recheck bytes stay private; they are not a new restoration certificate.
    """
    with tempfile.TemporaryDirectory(prefix='bc-', dir=runtime.run_root) as tmp:
        stage = Path(tmp)
        sql._worker_stage(runtime, source, stage, ca)
        target = {k: database[k] for k in ('host', 'port', 'name', 'tls_required', 'tls_ca_file', 'tls_ca_sha256')}
        if ca is not None:
            target['tls_ca_file'] = str(stage / 'ca.pem')
        request_id = os.urandom(16).hex()
        request = {'version': 1, 'operation': 'export', 'request_id': request_id, 'target': target,
                   'authority': {'user': authority._user, 'password': authority._password}}
        path = slot / 'sql-recheck.ndjson'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'wb') as output:
            code, digest, count = br.capture(p._command(runtime, stage), p._json(request), stage,
                                           output, runtime.timeout_seconds, cancel=cancel)
            output.flush()
            os.fsync(output.fileno())
        require(code == 0, 'COORDINATED_SQL_RECHECK_FAILED')
        tail = sql._tail(path)
        require(tail == {'type': 'complete', 'request_id': request_id,
                'tables': expected['tables'], 'rows': expected['rows'],
                'logical_sha256': expected['logical_sha256']}, 'COORDINATED_SQL_CHANGED')
        require(sql._hash(path) == digest and path.stat().st_size == count, 'COORDINATED_ARCHIVE_CHANGED')
        return {'sha256': digest, 'bytes': count}


def _files_unchanged(snapshot, lease, cancel):
    try:
        snapshot.verify_sources(lease, cancel=cancel)
    except Exception:
        raise CoordinatedBackupError('COORDINATED_FILES_CHANGED') from None


class CoordinatedBackup:
    def __init__(self, runtime: p.PhpRuntime, source: Path, *, repository: str, commit: str):
        require(repository == p.WEB_REPOSITORY and commit == f.WEB_COMMIT, 'SOURCE_PIN_MISMATCH')
        self.runtime, self.source = runtime, Path(source)

    def create_and_verify(self, payload: dict, authority: d.SqlAuthorityCredentials, *, config_root: Path,
                          backup_root: Path, inventory: files.DataInventory, maintenance: m.MaintenanceLease,
                          confirmed: bool, allow_global_read_lock: bool, cancel=None) -> CoordinatedVerification:
        started = False
        try:
            require(confirmed is True and allow_global_read_lock is True, 'COORDINATED_CONSENT_REQUIRED')
            _held(maintenance)
            require(type(authority) is d.SqlAuthorityCredentials and type(inventory) is files.DataInventory,
                    'COORDINATED_INPUT_REJECTED')
            require(type(payload) is dict and payload.get('mode') == 'upgrade'
                    and payload.get('assistant') == {'action': 'preserve'}, 'COORDINATED_INPUT_REJECTED')
            value = copy.deepcopy(payload)
            config = f._configuration(value, fresh=False)
            require(isinstance(backup_root, Path), 'COORDINATED_PATH_REJECTED')
            with ExitStack() as stack:
                gid, web, directory, conf, webfd, inc = f._open(self.runtime, config, config_root, stack)
                current = f.FinalizationStep(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=f.WEB_COMMIT)
                current._sources(web)
                database, loader, ca = f._prepared(config, value, directory, conf, gid)
                completed = f._completed(conf, webfd, inc, gid)
                seal = f._json_read(conf, 'seal.json', gid)
                require(maintenance.scope.instance == seal['instance'] and maintenance.scope.web_gid == gid
                        and maintenance.scope.directory == directory / 'maintenance', 'COORDINATED_INSTANCE_MISMATCH')
                require(inventory.web_uid == pwd.getpwnam(config['web']['service_user']).pw_uid
                        and inventory.web_gid == gid, 'COORDINATED_IDENTITY_MISMATCH')
                require(authority._user != database['user'] and authority._password != database['password'],
                        'COORDINATED_ACCOUNT_SEPARATION_REQUIRED')
                protected = (web, config_root, self.runtime.state_root, self.runtime.run_root, self.source)
                for path in protected:
                    require(backup_root != path and backup_root not in path.parents and path not in backup_root.parents,
                            'COORDINATED_PATH_REJECTED')
                    for _, root in inventory.roots:
                        require(root != path and root not in path.parents and path not in root.parents,
                                'COORDINATED_EXTERNAL_ROOTS_REQUIRED')
                require(not any(backup_root == Path(x) or Path(x) in backup_root.parents
                                for x in ('/srv', '/var/www', '/tmp')), 'COORDINATED_PATH_REJECTED')
                inventory.validate(backup_root, maintenance)
                rootfd = stack.enter_context(fs._directory(backup_root))
                info = os.fstat(rootfd)
                require(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700, 'COORDINATED_PATH_REJECTED')
                require(cancel is None or not cancel.is_set(), 'COORDINATED_INTERRUPTED')
                backup_id = os.urandom(16).hex()
                slot = backup_root / backup_id
                os.mkdir(backup_id, 0o700, dir_fd=rootfd)
                started = True
                os.fsync(rootfd)
                binding = {'version': 1, 'backup_id': backup_id, 'instance': seal['instance'],
                           'lease_id': maintenance.lease_id, 'source_commit': f.WEB_COMMIT,
                           'target_sha256': f._sha(p._json([database['host'], database['port'], database['name'].lower()]))}
                sql._new_file(slot / 'attempt.json', p._json({'state': 'COORDINATED_STARTED', **binding}))
                for name in ('data', 'sql'):
                    (slot / name).mkdir(mode=0o700)
                snapshot = files.capture_and_verify(inventory, slot / 'data', maintenance, confirmed=True, cancel=cancel)
                _held(maintenance)
                backup = sql.UpgradeBackup(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=f.WEB_COMMIT)
                restored = backup.create_and_verify(value, authority, config_root=config_root, backup_root=slot / 'sql',
                    confirmed=True, allow_global_read_lock=True, cancel=cancel).report()
                require(restored.get('state') == 'BACKUP_RESTORE_VERIFIED' and restored.get('backup_verified') is True,
                        'COORDINATED_SQL_BACKUP_FAILED')
                _held(maintenance)
                _files_unchanged(snapshot, maintenance, cancel)
                # Re-restore saved data after SQL validation, detecting damage to its blobs.
                snapshot.restore_new(slot / 'data-proof', maintenance, cancel=cancel)
                shutil.rmtree(slot / 'data-proof')
                recheck = _recheck(self.runtime, self.source, database, ca, authority, slot, restored, cancel)
                _held(maintenance)
                _files_unchanged(snapshot, maintenance, cancel)
                current._sources(web)
                require(f._completed(conf, webfd, inc, gid) == completed
                        and f._prepared(config, value, directory, conf, gid) == (database, loader, ca),
                        'COORDINATED_ENVELOPE_CHANGED')
                sql_slot = slot / 'sql' / restored['backup_id']
                raw, _ = sql._file(sql_slot / 'manifest.json')
                require(f._sha(raw) == restored['manifest_sha256']
                        and sql._hash(sql_slot / 'database.ndjson') == restored['database_sha256'],
                        'COORDINATED_ARCHIVE_CHANGED')
                saved = strict_json_loads(raw)
                current._pending_edits(conf)
                require(sql._scan({'web': web, 'configuration': directory}, cancel=cancel) == saved['files'],
                        'COORDINATED_ENVELOPE_CHANGED')
                journal, _ = sql._file(sql_slot / 'fresh.attempt')
                require(f._sha(journal) == saved['fresh_attempt_sha256'], 'COORDINATED_ARCHIVE_CHANGED')
                with fs._directory(self.runtime.state_root) as statefd:
                    require(f._read(statefd, saved['fresh_attempt_name'], 0, mode=0o600) == journal,
                            'COORDINATED_ENVELOPE_CHANGED')
                sql._verify_files(sql_slot, saved['files'])
                sql_receipt, _ = sql._file(sql_slot / 'verified.json')
                require(sql_receipt == p._json(restored), 'COORDINATED_ARCHIVE_CHANGED')
                data = snapshot.report(maintenance)
                manifest = {**binding, 'scope': 'SQL_IMMUTABLE_WEB_ENVELOPE_REGISTERED_EXTERNAL_DATA',
                            'sql_backup': restored, 'data_snapshot': data, 'sql_recheck': recheck,
                            'storage_inventory_complete': False, 'system_wiring_verified': False}
                sql._new_file(slot / 'coordinated.json', p._json(manifest))
                _held(maintenance)
                require(cancel is None or not cancel.is_set(), 'COORDINATED_INTERRUPTED')
                result = {'state': 'COORDINATED_BACKUP_RESTORE_VERIFIED', 'code': 'OK', 'backup_id': backup_id,
                    'manifest_sha256': f._sha(p._json(manifest)), 'source_commit': f.WEB_COMMIT,
                    'database_restoration_verified': True, 'registered_data_restoration_verified': True,
                    'registered_scope_coherence_verified': True, 'trigger_smoke_verified': restored['trigger_smoke_verified'],
                    'registered_roots': data['registered_roots'], 'data_files': data['files'], 'data_bytes': data['bytes'],
                    'storage_inventory_complete': False, 'system_wiring_verified': False, 'complete_web_backup': False,
                    'maintenance_required': True, 'activity_resumed': False, 'apply_allowed': False,
                    'restore_to_original_allowed': False, 'rollback_verified': False, 'application_installed': False}
                with fs._directory(slot) as fd:
                    os.fsync(fd)
                sql._new_file(slot / 'verified.json', p._json(result))
                with fs._directory(slot) as fd:
                    os.fsync(fd)
                os.fsync(rootfd)
                return CoordinatedVerification(p._json(result))
        except Exception as error:
            code = str(error) if isinstance(error, CoordinatedBackupError) else 'COORDINATED_OPERATION_UNAVAILABLE'
            if started:
                try:
                    (slot / 'verified.json').unlink(missing_ok=True)
                    with fs._directory(slot) as fd:
                        os.fsync(fd)
                except OSError:
                    pass
                return CoordinatedVerification(p._json({'state': 'COORDINATED_BACKUP_INCOMPLETE', 'code': code,
                    'backup_id': backup_id, 'maintenance_required': True, 'activity_resumed': False,
                    'complete_web_backup': False, 'apply_allowed': False, 'rollback_verified': False,
                    'manual_inspection_required': True}))
            raise CoordinatedBackupError(code) from None
