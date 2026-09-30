"""Private cooperative maintenance for a dedicated, guarded Web deployment.

The system adapter must pin auto_prepend_file for every PHP request and use
writer() for every other managed producer, including the session cleaner.
This is not evidence that an arbitrary existing server has those protections.
No automatic resume on exception or controller death: the durable gate stays.
"""
from __future__ import annotations

import fcntl
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from installer import database_config as fs
from installer import finalization as f
from installer import php_transport as p


class MaintenanceError(RuntimeError):
    """A closed, non-secret diagnostic."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise MaintenanceError(code)


class MaintenanceScope:
    def __init__(self, directory: Path, web_gid: int, instance: str):
        require(isinstance(directory,Path) and directory.is_absolute()
            and type(web_gid) is int and web_gid>0
            and type(instance) is str and re.fullmatch(r'[a-f0-9]{32}',instance) is not None,
            'MAINTENANCE_INPUT_REJECTED')
        self.directory,self.web_gid,self.instance=directory,web_gid,instance

    def _guard(self) -> bytes:
        template=p._read_file(Path(__file__).parent/'private/request_guard.php')
        require(template.count(b'__MAINTENANCE_ROOT_HEX__')==1
            and template.count(b'__MAINTENANCE_GID__')==1,'MAINTENANCE_TEMPLATE_REJECTED')
        return template.replace(b'__MAINTENANCE_ROOT_HEX__',os.fsencode(self.directory).hex().encode()).replace(
            b'__MAINTENANCE_GID__',str(self.web_gid).encode())

    def _profile(self) -> bytes:
        return p._json({'version':1,'instance':self.instance,'web_gid':self.web_gid,
            'guard_sha256':f._sha(self._guard()),'policy':'COOPERATIVE_ALL_WRITERS_V1'})

    def create(self, *, confirmed: bool) -> None:
        """Create exclusively in a root-owned parent; never alter an existing scope."""
        require(confirmed is True,'MAINTENANCE_CONSENT_REQUIRED')
        require(os.geteuid()==0,'MAINTENANCE_ROOT_REQUIRED')
        with fs._directory(self.directory.parent,readable_by=self.web_gid) as parent:
            fs._absent(parent,self.directory.name)
            os.mkdir(self.directory.name,0o700,dir_fd=parent)
            with fs._directory(self.directory) as fd:
                f._write(fd,'activity.lock',b'',self.web_gid)
                f._write(fd,'request_guard.php',self._guard(),self.web_gid)
                f._write(fd,'profile.json',self._profile(),self.web_gid)
                os.fchown(fd,0,self.web_gid);os.fchmod(fd,0o750);os.fsync(fd)
            os.fsync(parent)

    @contextmanager
    def _open(self):
        with fs._directory(self.directory,readable_by=self.web_gid) as directory:
            info=os.fstat(directory)
            require(info.st_gid==self.web_gid and stat.S_IMODE(info.st_mode)==0o750,
                'MAINTENANCE_PROFILE_REJECTED')
            require(f._read(directory,'profile.json',self.web_gid)==self._profile()
                and f._read(directory,'request_guard.php',self.web_gid)==self._guard(),
                'MAINTENANCE_PROFILE_REJECTED')
            f._read(directory,'activity.lock',self.web_gid)
            lock=os.open('activity.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
            try:
                info=os.fstat(lock);named=os.stat('activity.lock',dir_fd=directory,follow_symlinks=False)
                require((info.st_ino,info.st_dev)==(named.st_ino,named.st_dev) and info.st_size==0,
                    'MAINTENANCE_PROFILE_REJECTED')
                yield directory,lock
            finally:os.close(lock)

    def _flag(self, directory: int) -> bytes | None:
        try:
            raw=f._read(directory,'maintenance.attempt',self.web_gid,limit=2048)
        except FileNotFoundError:return None
        from installer.model import strict_json_loads
        value=strict_json_loads(raw)
        require(type(value) is dict and set(value)=={'version','instance','lease_id','state'}
            and value['version']==1 and type(value['version']) is int and value['instance']==self.instance
            and type(value['lease_id']) is str and re.fullmatch(r'[a-f0-9]{32}',value['lease_id']) is not None
            and value['state']=='MAINTENANCE_REQUIRED' and p._json(value)==raw,'MAINTENANCE_PROFILE_REJECTED')
        return raw

    def acquire(self, *, confirmed: bool, timeout: float = 30, cancel=None) -> MaintenanceLease:
        """Publish the durable refusal before waiting for existing requests to drain."""
        return self._acquire(confirmed=confirmed,timeout=timeout,cancel=cancel,recover_id=None)

    def recover(self, lease_id: str, *, confirmed: bool, timeout: float = 30) -> MaintenanceLease:
        """Reacquire only the exact interrupted gate. Does not retry any SQL or resume."""
        require(type(lease_id) is str and re.fullmatch(r'[a-f0-9]{32}',lease_id) is not None,
            'MAINTENANCE_INPUT_REJECTED')
        return self._acquire(confirmed=confirmed,timeout=timeout,cancel=None,recover_id=lease_id)

    def _acquire(self, *, confirmed: bool, timeout: float, cancel, recover_id: str | None) -> MaintenanceLease:
        require(confirmed is True,'MAINTENANCE_CONSENT_REQUIRED')
        require(os.geteuid()==0,'MAINTENANCE_ROOT_REQUIRED')
        require(type(timeout) in (float,int) and 0<timeout<=60,'MAINTENANCE_INPUT_REJECTED')
        manager=self._open();directory,lock=manager.__enter__()
        try:
            existing=self._flag(directory)
            require(cancel is None or not cancel.is_set(),'MAINTENANCE_INTERRUPTED')
            if recover_id is None:
                require(existing is None,'MAINTENANCE_PENDING')
                lease_id=os.urandom(16).hex()
                raw=p._json({'version':1,'instance':self.instance,'lease_id':lease_id,'state':'MAINTENANCE_REQUIRED'})
                f._write(directory,'maintenance.attempt',raw,self.web_gid)
            else:
                lease_id=recover_id
                raw=p._json({'version':1,'instance':self.instance,'lease_id':lease_id,'state':'MAINTENANCE_REQUIRED'})
                require(existing==raw,'MAINTENANCE_RECOVERY_MISMATCH')
            deadline=time.monotonic()+timeout
            while True:
                require(cancel is None or not cancel.is_set(),'MAINTENANCE_INTERRUPTED')
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    require(time.monotonic()<deadline,'MAINTENANCE_DRAIN_TIMEOUT')
                    time.sleep(.025)
            require(self._flag(directory)==raw,'MAINTENANCE_PROFILE_REJECTED')
            return MaintenanceLease(self,manager,directory,lock,raw,lease_id)
        except BaseException:
            # Keep maintenance.attempt even after a drain failure. No implicit reopen.
            manager.__exit__(None,None,None)
            raise

    @contextmanager
    def writer(self):
        """Shared barrier for trusted non-HTTP writers, using the same root-owned lock."""
        with self._open() as (directory,lock):
            require(self._flag(directory) is None,'MAINTENANCE_ACTIVE')
            try:fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:raise MaintenanceError('MAINTENANCE_ACTIVE') from None
            require(self._flag(directory) is None,'MAINTENANCE_ACTIVE')
            yield

    def observe(self) -> dict:
        from installer.model import strict_json_loads
        with self._open() as (directory,lock):
            raw=self._flag(directory)
            if raw is None:return {'state':'SERVING','instance':self.instance}
            return {'state':'MAINTENANCE_REQUIRED','instance':self.instance,'lease_id':strict_json_loads(raw)['lease_id']}


class MaintenanceLease:
    def __init__(self, scope, manager, directory, lock, raw, lease_id):
        self.scope,self._manager,self._directory,self._lock=scope,manager,directory,lock
        self._raw,self.lease_id,self._pid,self._closed=raw,lease_id,os.getpid(),False

    def __repr__(self):return '<MaintenanceLease private live barrier>'
    def __reduce__(self):raise TypeError('Maintenance lease cannot be serialized')

    def assert_held(self) -> None:
        require(not self._closed and os.getpid()==self._pid,'MAINTENANCE_LEASE_REQUIRED')
        require(f._read(self._directory,'activity.lock',self.scope.web_gid)==b''
            and f._read(self._directory,'profile.json',self.scope.web_gid)==self.scope._profile()
            and f._read(self._directory,'request_guard.php',self.scope.web_gid)==self.scope._guard(),
            'MAINTENANCE_PROFILE_REJECTED')
        info=os.fstat(self._lock)
        named=os.stat('activity.lock',dir_fd=self._directory,follow_symlinks=False)
        require((info.st_ino,info.st_dev)==(named.st_ino,named.st_dev)
            and self.scope._flag(self._directory)==self._raw,'MAINTENANCE_PROFILE_REJECTED')

    def resume(self, *, confirmed: bool) -> None:
        """Explicit activity boundary. Rollback callers must refuse after this receipt."""
        require(confirmed is True,'MAINTENANCE_CONSENT_REQUIRED');self.assert_held()
        for marker in ('mobile-reopen.attempt','gateway-state.attempt','gateway-state.release','gateway-state.released','upgrade.attempt','data-access.attempt','inode-fence.attempt','inode-fence.release',
                       'configuration-inodes.attempt','configuration-inodes.release','web-inodes.attempt','web-inodes.release',
                       'external-paths.prepare','external-paths.attempt','external-paths.release'):
            try: os.stat(marker,dir_fd=self._directory,follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise MaintenanceError('MAINTENANCE_DATA_ACCESS_CLOSED')
        name='resumed-'+self.lease_id+'.json'
        receipt=p._json({'version':1,'instance':self.scope.instance,'lease_id':self.lease_id,'state':'ACTIVITY_RESUMED'})
        try: f._write(self._directory,name,receipt,self.scope.web_gid)
        except FileExistsError:
            partial=f._read(self._directory,name,self.scope.web_gid)
            require(receipt.startswith(partial),'MAINTENANCE_RECOVERY_MISMATCH')
            if partial!=receipt:
                os.unlink(name,dir_fd=self._directory);os.fsync(self._directory)
                f._write(self._directory,name,receipt,self.scope.web_gid)
        os.unlink('maintenance.attempt',dir_fd=self._directory);os.fsync(self._directory)
        self.close()

    def close(self) -> None:
        if not self._closed:
            require(os.getpid()==self._pid,'MAINTENANCE_LEASE_REQUIRED')
            self._closed=True;self._manager.__exit__(None,None,None)

    def __enter__(self):self.assert_held();return self
    def __exit__(self,*args):self.close()
