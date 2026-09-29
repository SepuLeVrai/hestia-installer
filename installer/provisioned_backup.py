"""One private path: provisioned HTTP/cleaner drain, SQL fence and restore proof.

No arbitrary launcher adoption, reopening, original-target restore or exhaustive
host certification. These are explicit later gates, not inferred from this run.
"""
from pathlib import Path
import re
from contextlib import ExitStack
from installer import backup_files as files, coordinated_backup as c, database_step as d
from installer import finalization as f, http_drain as hd, http_runtime as h
from installer import php_transport as p, session_cleaner as sc
from installer import scheduler_admission as sa
from installer import data_access as da
from installer import inode_fence as inf
from installer import gateway_state_backup as gateway
from installer.web_releases import STORAGE_COMMIT


class ProvisionedBackupError(RuntimeError): pass


def require(ok,code):
    if not ok:raise ProvisionedBackupError(code)


class ProvisionedBackup:
    def __init__(self, runtime, source, http_runtime, cleaner):
        require(type(runtime) is p.PhpRuntime and isinstance(source,Path)
            and type(http_runtime) is h.HttpRuntime and type(cleaner) is sc.SessionCleaner
            and cleaner.runtime is http_runtime,'PROVISIONED_BACKUP_INPUT_REJECTED')
        require(http_runtime.spec.external_uploads and http_runtime.spec.php_family=='8.4',
            'PROVISIONED_BACKUP_PROFILE_REQUIRED')
        self._runtime,self._source,self._http,self._cleaner=runtime,source,http_runtime,cleaner

    def __repr__(self):return '<ProvisionedBackup private coordinated maintenance>'

    def create_and_verify(self,payload,authority,*,config_root,backup_root,confirmed,allow_global_read_lock,cancel=None,
                          recover_lease_id=None):
        require(confirmed is True and allow_global_read_lock is True,'PROVISIONED_BACKUP_CONSENT_REQUIRED')
        require(recover_lease_id is None or type(recover_lease_id) is str
            and re.fullmatch(r'[a-f0-9]{32}',recover_lease_id),'PROVISIONED_BACKUP_INPUT_REJECTED')
        require(type(authority) is d.SqlAuthorityCredentials and isinstance(config_root,Path)
            and isinstance(backup_root,Path),'PROVISIONED_BACKUP_INPUT_REJECTED')
        require(type(payload) is dict and payload.get('mode')=='upgrade'
            and payload.get('assistant')=={'action':'preserve'},'PROVISIONED_BACKUP_INPUT_REJECTED')
        config=f._configuration(payload,fresh=False);spec=self._http.spec
        require(Path(config['web']['webroot'])==spec.webroot and config['web']['service_user']==spec.service_user
            and spec.maintenance_directory.parent.parent==config_root,'PROVISIONED_BACKUP_INSTANCE_MISMATCH')
        require(self._cleaner.runtime is self._http,'PROVISIONED_BACKUP_PROFILE_REQUIRED')
        require(cancel is None or not cancel.is_set(),'PROVISIONED_BACKUP_INTERRUPTED')
        drain=hd.HttpDrain(self._http,cleaner=self._cleaner)
        with ExitStack() as stack:
            schedulers=stack.enter_context(sa.acquire())
            barrier=stack.enter_context(drain.acquire(confirmed=True,cancel=cancel) if recover_lease_id is None
                else drain.recover(recover_lease_id,confirmed=True,cancel=cancel))
            gateway_backup=gateway.prepare(self._http,barrier,self._runtime,backup_root,stack,
                confirmed=True,cancel=cancel,recovering=recover_lease_id is not None)
            account,_,_,_=self._http._inspect_configuration()
            data_fence=stack.enter_context(da.acquire(barrier,confirmed=True))
            inode_fence=stack.enter_context(inf.acquire(data_fence,confirmed=True))
            inventory=files.DataInventory(tuple((name.replace('-','_'),spec.root/'data'/name)
                for name in (*h.DATA,'uploads')),account.pw_uid,account.pw_gid)
            coordinator=c.CoordinatedBackup(self._runtime,self._source,repository=p.WEB_REPOSITORY,commit=STORAGE_COMMIT)
            web_backup=coordinator.create_and_verify(payload,authority,config_root=config_root,backup_root=backup_root,
                inventory=inventory,maintenance=barrier.maintenance_lease,confirmed=True,
                allow_global_read_lock=True,cancel=cancel,service_barrier=barrier,
                scheduler_observation=schedulers,data_fence=data_fence,inode_fence=inode_fence)
            return web_backup if gateway_backup is None else gateway_backup.compose(web_backup)
