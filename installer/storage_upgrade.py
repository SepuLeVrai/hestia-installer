"""Pinned 46c0306 -> 2a27c7a storage migration under managed maintenance.

The admitted source is a sealed, root-owned Web with a dedicated managed runtime
and SQL server. No arbitrary legacy adoption or SQL migration replay. Services
remain stopped; the separate explicit authorization only releases their gate.
"""
from contextlib import ExitStack
from dataclasses import replace
import os
import re
from pathlib import Path
import stat

from installer import backup_files as files, coordinated_backup as coordinated
from installer import configuration_fence as cf, data_access as da, database_step as db
from installer import external_fence as ef, finalization as f, http_drain as hd
from installer import http_runtime as h, inode_fence as inode, maintenance as m
from installer import php_transport as p, provisioned_admission as admission
from installer import scheduler_admission as sched, session_cleaner as cleaner
from installer import sql_read_fence as sqlf, upgrade_backup as backup
from installer import upgrade_preflight as preflight, web_deployment as deploy, web_fence as wf
from installer.model import strict_json_loads
from installer.web_releases import LEGACY_COMMIT, STORAGE_COMMIT, get_release

fs = f.fs
MARKER = 'upgrade.attempt'


class StorageUpgradeError(RuntimeError):
    """Fixed non-secret diagnostics. An incomplete operation remains gated."""


def require(ok, code='STORAGE_UPGRADE_PROFILE_REJECTED'):
    if not ok:
        raise StorageUpgradeError(code)


def _mkdir(path, mode=0o700):
    with fs._directory(path.parent) as fd:
        fs._absent(fd, path.name)
        os.mkdir(path.name, mode, dir_fd=fd)
        os.chmod(path.name, mode, dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)


def _save(path, value):
    with fs._directory(path.parent) as fd:
        files._new(fd, path.name, p._json(value))


def _read(path, maximum=4 * 1024 * 1024):
    with fs._directory(path.parent) as fd:
        return strict_json_loads(files._read(fd, path.name, maximum))


def _event(slot, number, state):
    _save(slot / ('%02d-%s.json' % (number, state)), {'version': 1, 'state': state})


def _replace(path, expected, data, gid, mode):
    """Replace only the exact owned file, with a durable new inode first."""
    with fs._directory(path.parent) as fd:
        require(f._read(fd, path.name, gid, mode=mode, limit=p.MAX_FILE) == expected,
                'STORAGE_UPGRADE_TARGET_CHANGED')
        temporary = '.upgrade-' + f._sha(data)[:32]
        try: f._write(fd, temporary, data, gid, mode=mode)
        except FileExistsError:
            partial = f._read(fd, temporary, gid, mode=mode, limit=p.MAX_FILE)
            require(data.startswith(partial), 'STORAGE_UPGRADE_TARGET_CHANGED')
            if partial != data:
                os.unlink(temporary, dir_fd=fd); os.fsync(fd)
                f._write(fd, temporary, data, gid, mode=mode)
        require(f._read(fd, path.name, gid, mode=mode, limit=p.MAX_FILE) == expected,
                'STORAGE_UPGRADE_TARGET_CHANGED')
        os.replace(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)


def _move(source, target):
    with fs._directory(source.parent) as before, fs._directory(target.parent) as after:
        fs._absent(after, target.name)
        named = os.stat(source.name, dir_fd=before, follow_symlinks=False)
        require(stat.S_ISDIR(named.st_mode) and named.st_dev == os.fstat(after).st_dev,
                'STORAGE_UPGRADE_SAME_FILESYSTEM_REQUIRED')
        os.rename(source.name, target.name, src_dir_fd=before, dst_dir_fd=after)
        os.fsync(before)
        os.fsync(after)


def _upload_metadata(restored, records, account):
    """The dedicated service gains ownership; bytes and both dates stay exact."""
    rows = [row for row in records if row['scope'] == 'uploads']
    with fs._directory(restored) as root:
        for row in reversed(rows):
            parts = [] if row['path'] == '.' else row['path'].split('/')
            if row['kind'] == 'directory':
                with files._relative_directory(root, ['uploads', *parts]) as fd:
                    files._restore_metadata(fd, {**row, 'uid': account.pw_uid, 'gid': account.pw_gid, 'mode': 0o700})
            else:
                with files._relative_directory(root, ['uploads', *parts[:-1]]) as parent:
                    fd = os.open(parts[-1], files.REGULAR, dir_fd=parent)
                    try:
                        files._restore_metadata(fd, {**row, 'uid': account.pw_uid, 'gid': account.pw_gid, 'mode': 0o600})
                    finally:
                        os.close(fd)
    return [{**row, 'uid': account.pw_uid, 'gid': account.pw_gid,
             'mode': 0o700 if row['kind'] == 'directory' else 0o600} for row in rows]


class StorageUpgrade:
    def __init__(self, runtime, source, target_source, http_runtime, collector):
        require(type(runtime) is p.PhpRuntime and isinstance(source, Path) and isinstance(target_source, Path)
                and type(http_runtime) is h.HttpRuntime and type(collector) is cleaner.SessionCleaner
                and collector.runtime is http_runtime)
        spec = http_runtime.spec
        require(not spec.external_uploads and spec.maintenance_directory is not None and spec.php_family == '8.4')
        self.runtime, self.source, self.target_source = runtime, source, target_source
        self.http, self.collector = http_runtime, collector
        self.target_http = h.HttpRuntime(replace(spec, external_uploads=True))
        self.target_collector = cleaner.SessionCleaner(self.target_http)
        self.next_web = spec.webroot.with_name(spec.webroot.name + '-upgrade-next')
        self.previous = spec.webroot.with_name(spec.webroot.name + '-upgrade-previous')

    def __repr__(self):
        return '<StorageUpgrade pinned private migration>'

    def _paths(self, backup_root):
        require(isinstance(backup_root, Path))
        protected = (self.source, self.target_source, self.http.spec.webroot, self.http.spec.root,
                     self.runtime.run_root, self.runtime.state_root, self.http.spec.maintenance_directory.parent.parent)
        require(str(backup_root).startswith('/var/lib/'))
        for path in protected:
            require(backup_root != path and backup_root not in path.parents and path not in backup_root.parents)
        with fs._directory(backup_root) as fd:
            files._private(fd, directory=True)
            require(os.fstat(fd).st_dev == (self.http.spec.root / 'data').stat().st_dev,
                    'STORAGE_UPGRADE_SAME_FILESYSTEM_REQUIRED')
        for path in (self.next_web, self.previous, self.http.spec.root / 'data/uploads'):
            with fs._directory(path.parent) as fd:
                fs._absent(fd, path.name)

    def apply(self, payload, authority, *, config_root, backup_root, confirmed, allow_global_read_lock, cancel=None):
        from installer import storage_upgrade_recovery as recovery
        require(confirmed is True and allow_global_read_lock is True, 'STORAGE_UPGRADE_CONSENT_REQUIRED')
        require(type(authority) is db.SqlAuthorityCredentials and isinstance(config_root, Path))
        slot = None
        stage = 'PREFLIGHT'
        operation = ExitStack()
        try:
            self._paths(backup_root)
            config = f._configuration(payload, fresh=False)
            require(Path(config['web']['webroot']) == self.http.spec.webroot
                    and config_root == self.http.spec.maintenance_directory.parent.parent)
            assessment = preflight.UpgradePreflight(self.runtime, self.source,
                repository=p.WEB_REPOSITORY, commit=LEGACY_COMMIT).inspect(payload, config_root=config_root, cancel=cancel)
            deploy._scan(self.target_source, commit=STORAGE_COMMIT)
            require(cancel is None or not cancel.is_set(), 'STORAGE_UPGRADE_INTERRUPTED')
            with ExitStack() as stack:
                schedulers = stack.enter_context(sched.acquire())
                barrier = stack.enter_context(hd.HttpDrain(self.http, cleaner=self.collector).acquire(confirmed=True, cancel=cancel))
                lease = barrier.maintenance_lease
                account, extension, old_plan, old_staged = self.http._inspect_configuration()
                _, _, old_cleaner_files, old_cleaner_plan = self.collector._inspect_configuration()
                old_files = {**self.http._files(account, extension), **old_cleaner_files}
                gid, web, directory, conf, webfd, inc = f._open(self.runtime, config, config_root, stack)
                require(directory / 'maintenance' == lease.scope.directory and gid == account.pw_gid)
                database, loader, ca = f._prepared(config, payload, directory, conf, gid)
                old_receipt = f._completed(conf, webfd, inc, gid, commit=LEGACY_COMMIT)
                require(f._json_read(conf, 'state.json', gid)['migration_retained'] is False
                        and database['host'] == '127.0.0.1' and not database['tls_required'] and ca is None,
                        'STORAGE_UPGRADE_MANAGED_DATABASE_REQUIRED')
                require(authority._user != database['user'] and authority._password != database['password'])
                slot = backup_root / ('upgrade-' + lease.lease_id)
                _mkdir(slot)
                operation.enter_context(recovery.operation_lock(slot))
                binding = {'version': 1, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
                           'source_commit': LEGACY_COMMIT, 'target_commit': STORAGE_COMMIT,
                           'preflight_sha256': assessment.sha256, 'webroot': str(web),
                           'runtime': str(self.http.spec.root), 'slot': str(slot)}
                _save(slot / 'attempt.json', binding)
                files._new(lease._directory, MARKER, p._json(binding))
                _save(slot / 'runtime-before.json', {str(path): data.hex() for path, data in old_files.items()})
                _save(slot / 'receipts-before.json', {'runtime_plan': old_plan.hex(), 'runtime_staged': old_staged,
                    'cleaner_plan': old_cleaner_plan.hex(), 'finalized': old_receipt})
                data = stack.enter_context(da.acquire(barrier, confirmed=True))
                data_inodes = stack.enter_context(inode.acquire(data, confirmed=True))
                configuration = stack.enter_context(admission.acquire(conf, web, gid))
                config_inodes = stack.enter_context(cf.acquire(lease, configuration, confirmed=True))
                web_inodes = stack.enter_context(wf.acquire(barrier, confirmed=True))
                external = stack.enter_context(ef.acquire(lease, confirmed=True))
                configuration._bind_external(external)
                sql_lock = stack.enter_context(sqlf.acquire(self.runtime, self.source, database, ca, authority, cancel=cancel))
                def held():
                    require(cancel is None or not cancel.is_set(), 'STORAGE_UPGRADE_INTERRUPTED')
                    lease.assert_held(); sql_lock.assert_held(); schedulers.assert_held(); external.assert_held()
                held()
                stage = 'BACKUP'
                _mkdir(slot / 'data'); _mkdir(slot / 'sql')
                roots = tuple((name.replace('-', '_'), self.http.spec.root / 'data' / name) for name in h.DATA) + (('uploads', web / 'uploads'),)
                inventory = files.DataInventory(roots, account.pw_uid, gid)
                snapshot = files.capture_and_verify(inventory, slot / 'data', lease, confirmed=True, cancel=cancel)
                sql = backup.UpgradeBackup(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=LEGACY_COMMIT)
                verified = sql.create_and_verify(payload, authority, config_root=config_root,
                    backup_root=slot / 'sql', confirmed=True, allow_global_read_lock=True, cancel=cancel).report()
                require(verified.get('state') == 'BACKUP_RESTORE_VERIFIED', 'STORAGE_UPGRADE_BACKUP_FAILED')
                _save(slot / 'backup.json', {'sql': verified, 'files': snapshot.report(lease)})
                held(); snapshot.verify_sources(lease, cancel=cancel)
                _event(slot, 1, 'BACKUP_VERIFIED')
                stage = 'TARGET_PREPARATION'
                deploy.WebDeployment(deploy.DeploymentSpec(self.target_source, self.next_web,
                    slot / 'deployment', commit=STORAGE_COMMIT)).create(confirmed=True)
                # Pointers are generated for the final canonical path, never activated in staging.
                seal = f._read(conf, 'seal.json', gid)
                lock = f._read(webfd, 'install.lock', gid)
                pointer = f._read(inc, 'db.php', gid)
                require(f._documents(web, directory, gid, database, loader, lease.scope.instance) == (seal, lock, pointer))
                with fs._directory(self.next_web) as fd:
                    f._write(fd, 'install.lock', lock, gid)
                with fs._directory(self.next_web / 'includes') as fd:
                    f._write(fd, 'db.php', pointer, gid)
                require(f._runtime_digest(self.next_web) == get_release(STORAGE_COMMIT).runtime_sha256)
                snapshot.restore_new(slot / 'relocated', lease, cancel=cancel)
                with snapshot._open(lease) as (_, captured):
                    expected_uploads = _upload_metadata(slot / 'relocated', captured['records'], account)
                _save(slot / 'uploads-after.json', expected_uploads)
                prepared = recovery.prepare(self, slot, lease, account, extension, old_files, old_plan, old_staged,
                    old_cleaner_plan, old_receipt, expected_uploads, snapshot._manifest_sha256, verified)
                _event(slot, 2, 'TARGET_PREPARED')
                held(); barrier.assert_held(); snapshot.verify_sources(lease, cancel=cancel)
                web_inodes.assert_held(); data_inodes.assert_held(); config_inodes.assert_held()
                # Durable intent and both restored backups precede any source-envelope mutation.
                _event(slot, 3, 'CUTOVER_STARTED')
                stage = 'CUTOVER'
                web_inodes.unseal(confirmed=True)
                config_inodes.unseal(confirmed=True)
                data_inodes.unseal(confirmed=True)
                # The full barrier was verified immediately before unsealing.
                # Re-reading legacy business files through code audits now would
                # advance atime after the immutable flag changed their ctime.
                held(); snapshot.verify_sources(lease, cancel=cancel)
                recovery.switch(self, slot, lease, prepared, 'forward')
                stage = 'TARGET_VERIFICATION'
                coordinated._recheck(self.runtime, self.source, database, ca, authority, slot, verified, cancel)
                probe = f._probe(self.runtime, config, directory, gid, active=True, cancel=cancel)
                require(probe['database_verified'] is True)
                held()
                result = recovery.applied(prepared, expected_uploads)
                _event(slot, 4, 'TARGET_VERIFIED')
            # Admission checks and SQL lock release must also succeed. External
            # reservations stay durable until explicit target authorization.
            _save(slot / 'applied.json', result)
            return result
        except Exception as error:
            message = str(error)
            code = message if re.fullmatch(r'[A-Z][A-Z0-9_]{2,100}', message) else 'STORAGE_UPGRADE_INCOMPLETE'
            if slot is not None:
                return {'state': 'STORAGE_UPGRADE_INCOMPLETE', 'code': code, 'failure_type': type(error).__name__, 'stage': stage, 'services_started': False,
                        'activity_resumed': False, 'manual_action_required': True, 'application_installed': False}
            raise StorageUpgradeError(code) from None
        finally:
            operation.close()

    def authorize_resume(self, backup_root, lease_id, *, confirmed):
        from installer.storage_upgrade_recovery import authorize_resume
        return authorize_resume(self, backup_root, lease_id, confirmed=confirmed)

    def recover(self, payload, authority, *, config_root, backup_root, lease_id, direction,
                confirmed, allow_global_read_lock, cancel=None):
        from installer.storage_upgrade_recovery import recover
        return recover(self, payload, authority, config_root=config_root, backup_root=backup_root,
                       lease_id=lease_id, direction=direction, confirmed=confirmed,
                       allow_global_read_lock=allow_global_read_lock, cancel=cancel)

    def observe(self, backup_root, lease_id):
        from installer.storage_upgrade_recovery import observe
        return observe(self, backup_root, lease_id)
