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
from installer import scheduler_admission as sa
from installer import data_access as da
from installer import inode_fence as inf
from installer import configuration_fence as cf
from installer import web_fence as wf

from installer import backup_files as files
from installer import backup_runtime as br
from installer import database_config as fs
from installer import database_step as d
from installer import finalization as f
from installer import maintenance as m
from installer import http_drain as hd, sql_read_fence as rf
from installer import provisioned_admission as admission
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
        require(repository == p.WEB_REPOSITORY, 'SOURCE_PIN_MISMATCH')
        try: self.release = f.get_release(commit)
        except ValueError: raise CoordinatedBackupError('SOURCE_PIN_MISMATCH') from None
        self.runtime, self.source = runtime, Path(source)

    def create_and_verify(self, payload: dict, authority: d.SqlAuthorityCredentials, *, config_root: Path,
                          backup_root: Path, inventory: files.DataInventory, maintenance: m.MaintenanceLease,
                          confirmed: bool, allow_global_read_lock: bool, cancel=None,
                          service_barrier: hd.HttpDrainLease | None = None,
                          scheduler_observation: sa.SchedulerObservation | None = None,
                          data_fence: da.DataAccessFence | None = None,
                          inode_fence: inf.InodeFence | None = None) -> CoordinatedVerification:
        started = False
        fence = None
        configuration = None
        configuration_fence = None
        web_fence = None
        def held():
            _held(maintenance)
            if service_barrier is not None:
                require(type(service_barrier) is hd.HttpDrainLease and service_barrier._lease is maintenance,
                        'COORDINATED_SERVICE_BARRIER_REQUIRED')
                service_barrier.assert_held()
                require(type(scheduler_observation) is sa.SchedulerObservation,
                        'COORDINATED_SCHEDULER_OBSERVATION_REQUIRED')
                scheduler_observation.assert_held()
                require(type(data_fence) is da.DataAccessFence and data_fence._lease is maintenance
                    and data_fence._runtime is service_barrier._drain.runtime, 'COORDINATED_DATA_FENCE_REQUIRED')
                data_fence.assert_held()
                require(type(inode_fence) is inf.InodeFence and inode_fence._data is data_fence,
                        'COORDINATED_INODE_FENCE_REQUIRED')
                inode_fence.assert_held()
            else:
                require(scheduler_observation is None, 'COORDINATED_SCHEDULER_OBSERVATION_REQUIRED')
                require(data_fence is None, 'COORDINATED_DATA_FENCE_REQUIRED')
                require(inode_fence is None, 'COORDINATED_INODE_FENCE_REQUIRED')
            if fence is not None: fence.assert_held()
            if configuration is not None: configuration.assert_held()
            if configuration_fence is not None: configuration_fence.assert_held()
            if web_fence is not None: web_fence.assert_held()
        try:
            require(confirmed is True and allow_global_read_lock is True, 'COORDINATED_CONSENT_REQUIRED')
            held()
            require(type(authority) is d.SqlAuthorityCredentials and type(inventory) is files.DataInventory,
                    'COORDINATED_INPUT_REJECTED')
            require(type(payload) is dict and payload.get('mode') == 'upgrade'
                    and payload.get('assistant') == {'action': 'preserve'}, 'COORDINATED_INPUT_REJECTED')
            value = copy.deepcopy(payload)
            config = f._configuration(value, fresh=False)
            require(isinstance(backup_root, Path), 'COORDINATED_PATH_REJECTED')
            with ExitStack() as stack:
                gid, web, directory, conf, webfd, inc = f._open(self.runtime, config, config_root, stack)
                current = f.FinalizationStep(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=self.release.commit)
                current._sources(web)
                database, loader, ca = f._prepared(config, value, directory, conf, gid)
                completed = f._completed(conf, webfd, inc, gid, commit=self.release.commit)
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
                barrier_profile = None
                if service_barrier is not None:
                    runtime = service_barrier._drain.runtime
                    spec = runtime.spec
                    require(service_barrier._drain.cleaner is not None and spec.external_uploads
                            and spec.webroot == web and spec.maintenance_directory == directory / 'maintenance'
                            and spec.instance == seal['instance'], 'COORDINATED_SERVICE_BARRIER_REQUIRED')
                    prepared = f._json_read(conf, 'state.json', gid)
                    require(prepared.get('migration_retained') is False and database['host'] == '127.0.0.1'
                            and database['tls_required'] is False and ca is None,
                            'COORDINATED_FRESH_MANAGED_PROFILE_REQUIRED')
                    expected = tuple((name.replace('-', '_'), spec.root / 'data' / name)
                                     for name in (*hd.h.DATA, 'uploads'))
                    require(inventory.roots == expected, 'COORDINATED_PROVISIONED_ROOTS_REQUIRED')
                    with fs._directory(spec.root / 'data') as datafd:
                        require(set(os.listdir(datafd)) == set((*hd.h.DATA, 'uploads')),
                                'COORDINATED_PROVISIONED_ROOTS_REQUIRED')
                    barrier_profile = f._sha(service_barrier._profile)
                inventory.validate(backup_root, maintenance)
                rootfd = stack.enter_context(fs._directory(backup_root))
                info = os.fstat(rootfd)
                require(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700, 'COORDINATED_PATH_REJECTED')
                require(cancel is None or not cancel.is_set(), 'COORDINATED_INTERRUPTED')
                if service_barrier is not None:
                    configuration = stack.enter_context(admission.acquire(conf, web, gid))
                    configuration_fence = stack.enter_context(cf.acquire(maintenance, configuration, confirmed=True))
                    web_fence = stack.enter_context(wf.acquire(service_barrier, confirmed=True))
                    fence = stack.enter_context(rf.acquire(self.runtime, self.source, database, ca, authority, cancel=cancel))
                    held()
                backup_id = os.urandom(16).hex()
                slot = backup_root / backup_id
                os.mkdir(backup_id, 0o700, dir_fd=rootfd)
                started = True
                os.fsync(rootfd)
                binding = {'version': 1, 'backup_id': backup_id, 'instance': seal['instance'],
                           'lease_id': maintenance.lease_id, 'source_commit': self.release.commit,
                           'target_sha256': f._sha(p._json([database['host'], database['port'], database['name'].lower()]))}
                sql._new_file(slot / 'attempt.json', p._json({'state': 'COORDINATED_STARTED', **binding}))
                for name in ('data', 'sql'):
                    (slot / name).mkdir(mode=0o700)
                snapshot = files.capture_and_verify(inventory, slot / 'data', maintenance, confirmed=True, cancel=cancel)
                held()
                backup = sql.UpgradeBackup(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=self.release.commit)
                restored = backup.create_and_verify(value, authority, config_root=config_root, backup_root=slot / 'sql',
                    confirmed=True, allow_global_read_lock=True, cancel=cancel).report()
                require(restored.get('state') == 'BACKUP_RESTORE_VERIFIED' and restored.get('backup_verified') is True,
                        'COORDINATED_SQL_BACKUP_FAILED')
                held()
                _files_unchanged(snapshot, maintenance, cancel)
                # Re-restore saved data after SQL validation, detecting damage to its blobs.
                snapshot.restore_new(slot / 'data-proof', maintenance, cancel=cancel)
                shutil.rmtree(slot / 'data-proof')
                recheck = _recheck(self.runtime, self.source, database, ca, authority, slot, restored, cancel)
                held()
                _files_unchanged(snapshot, maintenance, cancel)
                current._sources(web)
                require(f._completed(conf, webfd, inc, gid, commit=self.release.commit) == completed
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
                if barrier_profile is not None:
                    manifest['service_barrier'] = {'profile_sha256': barrier_profile,
                        'policy': 'PROVISIONED_HTTP_CLEANER_SQL_CONFIGURATION_SCHEDULERS_DATA_WEB_INODES_V7'}
                    manifest['web_fence'] = web_fence.report()
                    manifest['configuration_fence'] = configuration_fence.report()
                    manifest['data_access_fence'] = data_fence.report()
                    manifest['inode_fence'] = inode_fence.report()
                    manifest['scheduler_admission'] = {'policy': 'CLASSIC_SCHEDULER_ABSENCE_V1',
                        'reobserved_during_backup': True, 'observation_is_point_in_time': True,
                        'host_scheduler_inventory_complete': False, 'foreign_cli_controlled': False}
                sql._new_file(slot / 'coordinated.json', p._json(manifest))
                held()
                require(cancel is None or not cancel.is_set(), 'COORDINATED_INTERRUPTED')
                result = {'state': 'COORDINATED_BACKUP_RESTORE_VERIFIED', 'code': 'OK', 'backup_id': backup_id,
                    'manifest_sha256': f._sha(p._json(manifest)), 'source_commit': self.release.commit,
                    'database_restoration_verified': True, 'registered_data_restoration_verified': True,
                    'registered_scope_coherence_verified': True, 'trigger_smoke_verified': restored['trigger_smoke_verified'],
                    'registered_roots': data['registered_roots'], 'data_files': data['files'], 'data_bytes': data['bytes'],
                    'storage_inventory_complete': False, 'system_wiring_verified': False, 'complete_web_backup': False,
                    'maintenance_required': True, 'activity_resumed': False, 'apply_allowed': False,
                    'restore_to_original_allowed': False, 'rollback_verified': False, 'application_installed': False}
                if barrier_profile is not None:
                    result.update(state='PROVISIONED_BACKUP_RESTORE_VERIFIED',
                        provisioned_services_drained=True, sql_read_fence_verified=True,
                        installer_settings_fenced=True, configuration_storage_admitted=True,
                        configuration_slot_inodes_fenced=True, ordinary_root_settings_writes_fenced=True,
                        configuration_fence_sha256=configuration_fence.report()['fence_sha256'],
                        web_code_fenced=True, web_activation_pointers_fenced=True, ordinary_root_web_writes_fenced=True,
                        web_fence_sha256=web_fence.report()['fence_sha256'],
                        canonical_data_paths_fenced=True, data_access_fence_sha256=data_fence.report()['fence_sha256'],
                        data_inode_writes_fenced=True, same_inode_alias_writes_fenced=True,
                        ordinary_root_data_writes_fenced=True, inode_fence_sha256=inode_fence.report()['fence_sha256'],
                        classic_scheduler_absence_observed=True, host_scheduler_inventory_complete=False,
                        foreign_cli_controlled=False,
                        service_profile_sha256=barrier_profile, phase5_complete=False)
                held()
                with fs._directory(slot) as fd:
                    os.fsync(fd)
                sql._new_file(slot / 'verified.json', p._json(result))
                with fs._directory(slot) as fd:
                    os.fsync(fd)
                os.fsync(rootfd)
                return CoordinatedVerification(p._json(result))
        except Exception as error:
            code = str(error) if isinstance(error, CoordinatedBackupError) else 'COORDINATED_OPERATION_UNAVAILABLE'
            if isinstance(error, sa.SchedulerAdmissionError) and str(error) in (sa.REJECTED, sa.UNAVAILABLE):
                code = str(error)
            if isinstance(error, (rf.SqlReadFenceError, admission.AdmissionError)) and str(error) in (
                    *rf.PROFILE_REJECTIONS, 'PROVISIONED_SETTINGS_BUSY', 'PROVISIONED_CONFIGURATION_CHANGED',
                    'PROVISIONED_EXTERNAL_STORAGE_REJECTED'):
                code = str(error)
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
