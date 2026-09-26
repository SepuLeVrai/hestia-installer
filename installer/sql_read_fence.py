"""Live server-wide SQL read lock for the bounded provisioned backup window."""
from contextlib import contextmanager
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

from installer import database_config as fs, finalization as f, php_transport as p
from installer.model import strict_json_loads


class SqlReadFenceError(RuntimeError): pass

PROFILE_REJECTIONS=frozenset(('SQL_FENCE_SERVER_PROFILE_REJECTED','SQL_FENCE_STORAGE_PROFILE_REJECTED'))


def require(ok, code='SQL_FENCE_UNAVAILABLE'):
    if not ok: raise SqlReadFenceError(code)


class SqlReadFence:
    def __init__(self, process, request_id, cancel):
        self._process=process;self._id=request_id;self._cancel=cancel
        self._sequence=0;self._deadline=time.monotonic()+180;self._pid=os.getpid();self._closed=False

    def __repr__(self): return '<SqlReadFence private live server lock>'
    def __reduce__(self): raise TypeError('SQL read fences cannot be serialized')

    def _round(self, request, state):
        require(not self._closed and self._pid==os.getpid(),'SQL_FENCE_REQUIRED')
        wire=p._json(request)+b'\n';require(len(wire)<=16385,'SQL_FENCE_PROTOCOL')
        proc=self._process;output=bytearray();offset=0
        deadline=min(self._deadline,time.monotonic()+12)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdin,selectors.EVENT_WRITE,'in')
            selector.register(proc.stdout,selectors.EVENT_READ,'out')
            while True:
                require(self._cancel is None or not self._cancel.is_set(),'SQL_FENCE_INTERRUPTED')
                require(time.monotonic()<deadline,'SQL_FENCE_TIMEOUT')
                for key,_ in selector.select(min(.05,max(0,deadline-time.monotonic()))):
                    if key.data=='in':
                        try: offset+=os.write(proc.stdin.fileno(),wire[offset:offset+4096])
                        except BlockingIOError: continue
                        if offset==len(wire): selector.unregister(proc.stdin)
                    else:
                        try: block=os.read(proc.stdout.fileno(),1025)
                        except BlockingIOError: continue
                        require(block,'SQL_FENCE_LOST');output.extend(block)
                        require(len(output)<=1024,'SQL_FENCE_PROTOCOL')
                if b'\n' in output:
                    require(offset==len(wire) and output.endswith(b'\n') and output.count(b'\n')==1,'SQL_FENCE_PROTOCOL')
                    try: value=strict_json_loads(bytes(output[:-1]))
                    except Exception: raise SqlReadFenceError('SQL_FENCE_PROTOCOL') from None
                    require(type(value) is dict and set(value)=={'request_id','sequence','state'}
                        and type(value['sequence']) is int and value['sequence']==self._sequence
                        and value['request_id']==self._id and type(value['state']) is str,'SQL_FENCE_PROTOCOL')
                    if value['state'] in PROFILE_REJECTIONS: raise SqlReadFenceError(value['state'])
                    require(value['state']==state,'SQL_FENCE_PROTOCOL')
                    return

    def assert_held(self):
        self._sequence+=1
        require(self._sequence<64,'SQL_FENCE_LIMIT')
        try:self._round({'operation':'check','request_id':self._id,'sequence':self._sequence},'LOCK_HELD')
        except SqlReadFenceError:raise
        except Exception:raise SqlReadFenceError('SQL_FENCE_UNAVAILABLE') from None

    def release(self):
        self._sequence+=1
        self._round({'operation':'release','request_id':self._id,'sequence':self._sequence},'RELEASED')
        require(self._process.wait(timeout=3)==0,'SQL_FENCE_RELEASE_FAILED');self._closed=True

    def close(self):
        require(self._pid==os.getpid(),'SQL_FENCE_REQUIRED')
        self._closed=True;proc=self._process
        if proc.poll() is None:
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        proc.wait(timeout=3)
        for stream in (proc.stdin,proc.stdout):stream.close()


def _stage(runtime, source, stage, ca):
    # The shared backup source exceeds the 16 KiB secret writer limit. Reuse
    # its existing source-bundle path, then add only the small channel bridge.
    p._copy_bundle(source,stage,runtime.worker_gid,f.ENGINE_FILES,f.ENGINE_SHA256,'backup_bridge.php')
    with fs._directory(stage) as fd:
        fs._absent(fd,'backup_bridge.php')
        os.rename('bridge.php','backup_bridge.php',src_dir_fd=fd,dst_dir_fd=fd)
        for name,source_name in (('bridge.php','sql_read_fence.php'),('sql_accounts_policy.php','sql_accounts_policy.php')):
            f._write(fd,name,p._read_file(Path(__file__).parent/'private'/source_name),runtime.worker_gid)
        if ca is not None:f._write(fd,'ca.pem',ca,runtime.worker_gid)


@contextmanager
def acquire(runtime, source, database, ca, authority, *, cancel=None):
    """Private input already validated by finalization; no client-controlled SQL."""
    fence=None
    with tempfile.TemporaryDirectory(prefix='rf-',dir=runtime.run_root) as tmp:
        stage=Path(tmp);_stage(runtime,source,stage,ca)
        target={k:database[k] for k in ('host','port','name','tls_required','tls_ca_file','tls_ca_sha256')}
        if ca is not None:target['tls_ca_file']=str(stage/'ca.pem')
        try:
            proc=subprocess.Popen(p._command(runtime,stage),cwd=stage,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,bufsize=0,start_new_session=True,close_fds=True,
                env={'PATH':'/usr/sbin:/usr/bin','LANG':'C','PHP_INI_SCAN_DIR':''})
            fence=SqlReadFence(proc,os.urandom(16).hex(),cancel)
            os.set_blocking(proc.stdin.fileno(),False);os.set_blocking(proc.stdout.fileno(),False)
            fence._round({'version':1,'operation':'acquire','request_id':fence._id,'target':target,
                'authority':{'user':authority._user,'password':authority._password}},'LOCK_HELD')
            yield fence
            fence.assert_held();fence.release()
        except SqlReadFenceError:raise
        except Exception:raise SqlReadFenceError('SQL_FENCE_UNAVAILABLE') from None
        finally:
            if fence is not None:fence.close()
