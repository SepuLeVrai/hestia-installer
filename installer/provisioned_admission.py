"""Private bounded admission for the provisioned backup configuration window.

This does not admit arbitrary host launchers or prevent administrative root writes.
"""
from contextlib import ExitStack, contextmanager
import fcntl
import os
from pathlib import Path

from installer import database_config as fs, finalization as f


class AdmissionError(RuntimeError): pass


def require(ok, code='PROVISIONED_CONFIGURATION_CHANGED'):
    if not ok: raise AdmissionError(code)


def _absent(path):
    # Walk protected parents before accepting absence; never follow a symlink,
    # read legacy credentials or execute a PHP configuration file.
    parent=Path('/')
    for part in path.parts[1:]:
        with fs._directory(parent) as fd:
            try: os.stat(part,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError: return
        parent/=part
    raise AdmissionError('PROVISIONED_EXTERNAL_STORAGE_REJECTED')


class ConfigurationLease:
    def __init__(self, conf, web):
        self._conf=conf;self._web=web;self._files=[];self._pid=os.getpid();self._closed=False
        self._external=None

    def __repr__(self): return '<ConfigurationLease private settings admission>'
    def __reduce__(self): raise TypeError('Configuration leases cannot be serialized')

    def _bind_external(self, fence):
        from installer import external_fence as ef
        require(type(fence) is ef.ExternalFence and self._external is None)
        fence.assert_held()
        with fs._directory(fence._lease.scope.directory.parent) as fd:
            expected,actual=os.fstat(fd),os.fstat(self._conf)
            require((expected.st_dev,expected.st_ino)==(actual.st_dev,actual.st_ino))
        self._external=(fence._lease,fence._raw)
        self.assert_held()

    def assert_held(self):
        require(not self._closed and self._pid==os.getpid())
        for fd,name,gid,mode,limit,identity,data in self._files:
            opened=os.fstat(fd);named=os.stat(name,dir_fd=self._conf,follow_symlinks=False)
            require((opened.st_dev,opened.st_ino)==identity==(named.st_dev,named.st_ino))
            require(f._read(self._conf,name,gid,mode=mode,limit=limit)==data)
        f.FinalizationStep._pending_edits(self._conf)
        if self._external is None:
            for path in (Path('/etc/hestia/conf_db_ia.php'),Path('/var/lib/hestia-ai')):_absent(path)
        else:
            from installer import external_fence as ef
            ef.assert_reservation(*self._external)
        _absent(self._web/'includes/conf_db_ia.php')


@contextmanager
def acquire(conf, web, gid, *, external=None):
    """Shared locks coexist with nested backup readers, exclude settings writers."""
    lease=ConfigurationLease(conf,web)
    try:
        with ExitStack() as stack:
            try: f._write(conf,'assistant-edit.lock',b'',0,mode=0o600)
            except FileExistsError: pass
            for name,group,mode,limit in (('assistant-edit.lock',0,0o600,0),('assistant.json',gid,0o660,2048)):
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=conf)
                stack.callback(os.close,fd)
                try: fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
                except BlockingIOError: raise AdmissionError('PROVISIONED_SETTINGS_BUSY') from None
                info=os.fstat(fd)
                data=f._read(conf,name,group,mode=mode,limit=limit)
                lease._files.append((fd,name,group,mode,limit,(info.st_dev,info.st_ino),data))
            if external is not None: lease._bind_external(external)
            else: lease.assert_held()
            yield lease
            lease.assert_held()
    finally:
        lease._closed=True
