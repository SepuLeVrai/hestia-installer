"""Real permission barriers and recovery, with systemd profile audit isolated."""
import errno
import os
from pathlib import Path
import pickle
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import data_access as a


class DataAccessTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='hestia-fence-',dir=getattr(self,'fixture_root','/var/lib')));self.root.chmod(0o755)
        self.addCleanup(lambda:shutil.rmtree(self.root))
        self.account=SimpleNamespace(pw_uid=19001,pw_gid=19001,pw_name='hestia-fence-test')
        self.http=self.root/'http';self.http.mkdir(mode=0o750);os.chown(self.http,0,19001)
        self.data=self.http/'data';self.data.mkdir(mode=0o750);os.chown(self.data,0,19001)
        for name in (*a.h.DATA,'uploads'):
            path=self.data/name;path.mkdir(mode=0o700);os.chown(path,19001,19001)
            file=path/'payload';file.write_bytes(b'original');file.chmod(0o600);os.chown(file,19001,19001)
        config=self.root/'config';config.mkdir(mode=0o750);os.chown(config,0,19001)
        self.runtime=a.h.HttpRuntime(a.h.RuntimeSpec('a'*32,self.http,Path('/srv/fence-fixture'),
            self.account.pw_name,'hestia.test',8123,'8.4',external_uploads=True,
            maintenance_directory=config/'maintenance'))
        self.scope=self.runtime._scope(self.account);self.scope.create(confirmed=True)
        identity=patch.object(a.h,'_identity',return_value=self.account);identity.start();self.addCleanup(identity.stop)
        def inspect():
            self.assertEqual(stat.S_IMODE(self.data.stat().st_mode),a.expected_mode(self.runtime,self.account))
            return self.account,None,None,None
        inspect_patch=patch.object(self.runtime,'_inspect_configuration',side_effect=inspect)
        inspect_patch.start();self.addCleanup(inspect_patch.stop)
        self.lease=self.scope.acquire(confirmed=True);self.addCleanup(self.lease.close)

    def acquire(self,**kwargs):
        value=a._acquire(self.runtime,self.lease,confirmed=True,recover=False,**kwargs)
        self.addCleanup(value.close);return value

    def child(self,body):
        return subprocess.run(['/usr/bin/python3','-c',body,str(self.data)],cwd='/',
            user=19001,group=19001,extra_groups=[],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=False)

    def test_future_unprivileged_reads_writes_creation_and_removal_are_denied(self):
        self.assertEqual(self.child('import pathlib,sys;(pathlib.Path(sys.argv[1])/"uploads/payload").write_bytes(b"original")').returncode,0)
        fence=self.acquire();self.assertTrue(fence.report()['canonical_data_paths_fenced'])
        body='''import pathlib,sys,errno
root=pathlib.Path(sys.argv[1]);count=0
for name in ('sessions','tmp','upload-tmp','imports','log','uploads'):
 for operation in ('read','write','create','remove'):
  try:
   p=root/name/'payload'
   if operation=='read':p.read_bytes()
   elif operation=='write':p.write_bytes(b'changed')
   elif operation=='create':(root/name/'new').write_bytes(b'new')
   else:p.unlink()
  except PermissionError as error:
   assert error.errno==errno.EACCES;count+=1
  else:raise AssertionError('Unexpected data access')
assert count==24
'''
        child=self.child(body);self.assertEqual(child.returncode,0,child.stderr.decode())
        for name in (*a.h.DATA,'uploads'):self.assertEqual((self.data/name/'payload').read_bytes(),b'original')

    def test_existing_descriptor_holder_is_refused_after_closure_without_signalling(self):
        proc=subprocess.Popen(['/usr/bin/python3','-c',
            'import sys;f=open(sys.argv[1],"r+b");print("ready",flush=True);sys.stdin.readline()',
            str(self.data/'uploads/payload')],cwd='/',user=19001,group=19001,extra_groups=[],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        def cleanup():
            proc.stdin.close();proc.wait(timeout=5);proc.stdout.close()
        self.addCleanup(cleanup);self.assertEqual(proc.stdout.readline(),b'ready\n')
        with self.assertRaisesRegex(a.hd.HttpDrainError,'FOREIGN_IDENTITY_PROCESS'):self.acquire()
        self.assertIsNone(proc.poll());self.assertEqual(stat.S_IMODE(self.data.stat().st_mode),0o700)
        self.assertTrue((self.scope.directory/a.MARKER).is_file())

    def test_close_and_recovery_do_not_reopen_and_resume_requires_explicit_reopen(self):
        fence=self.acquire();fence.close();lease_id=self.lease.lease_id;self.lease.close()
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode),0o700)
        with self.scope.recover(lease_id,confirmed=True) as lease:
            with a.recover(self.runtime,lease,confirmed=True) as recovered:
                with self.assertRaisesRegex(a.m.MaintenanceError,'DATA_ACCESS_CLOSED'):lease.resume(confirmed=True)
                recovered.reopen(confirmed=True)
                self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
                lease.resume(confirmed=True)
        self.assertEqual(self.scope.observe()['state'],'SERVING')
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode),0o750)
        self.assertEqual(self.child('import pathlib,sys;(pathlib.Path(sys.argv[1])/"uploads/new").write_bytes(b"ok")').returncode,0)

    def test_interrupted_closure_retains_intent_and_recovery_only_finishes_closing(self):
        original=os.fchmod
        def fail(fd,mode):
            if mode==0o700:raise OSError('injected closure failure')
            return original(fd,mode)
        with patch.object(a.os,'fchmod',side_effect=fail):
            with self.assertRaises(OSError):self.acquire()
        self.assertTrue((self.scope.directory/a.MARKER).exists())
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode),0o750)
        with self.assertRaises(a.DataAccessError):a.expected_mode(self.runtime,self.account)
        with self.assertRaisesRegex(a.m.MaintenanceError,'DATA_ACCESS_CLOSED'):self.lease.resume(confirmed=True)
        with a.recover(self.runtime,self.lease,confirmed=True) as recovered:recovered.assert_held()
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode),0o700)

    def test_marker_inode_or_permissions_drift_revokes_fence_and_never_repairs(self):
        fence=self.acquire();self.data.chmod(0o750)
        with self.assertRaises(a.DataAccessError):fence.assert_held()
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode),0o750);self.data.chmod(0o700)
        original=self.http/'retained';self.data.rename(original);self.data.mkdir(mode=0o700);os.chown(self.data,0,19001)
        with self.assertRaises(a.DataAccessError):fence.assert_held()
        self.data.rmdir();original.rename(self.data)
        marker=self.scope.directory/a.MARKER;marker.write_bytes(b'{}')
        with self.assertRaises(a.DataAccessError):fence.assert_held()
        self.assertEqual(marker.read_bytes(),b'{}')

    def test_wrong_lease_replay_and_unmarked_closed_directory_are_rejected(self):
        with self.assertRaisesRegex(a.DataAccessError,'UNAVAILABLE'):a.recover(self.runtime,self.lease,confirmed=True)
        self.data.chmod(0o700)
        with self.assertRaises(AssertionError):self.acquire()
        self.assertFalse((self.scope.directory/a.MARKER).exists());self.data.chmod(0o750)
        fence=self.acquire()
        with self.assertRaises(Exception):self.acquire()
        raw=fence._raw;fence._raw=b'{}'
        with self.assertRaises(a.DataAccessError):fence.assert_held()
        fence._raw=raw

    def test_consent_foreign_process_pickle_and_public_report_are_closed(self):
        for value in (False,1,'yes',None):
            with self.assertRaisesRegex(a.DataAccessError,'CONSENT_REQUIRED'):
                a._acquire(self.runtime,self.lease,confirmed=value,recover=False)
        self.assertFalse((self.scope.directory/a.MARKER).exists());fence=self.acquire()
        for value in (False,1,'yes',None):
            with self.assertRaisesRegex(a.DataAccessError,'CONSENT_REQUIRED'):fence.reopen(confirmed=value)
        with self.assertRaises(TypeError):pickle.dumps(fence)
        with patch.object(a.os,'getpid',return_value=-1):
            with self.assertRaisesRegex(a.DataAccessError,'LEASE_REQUIRED'):fence.assert_held()
        self.assertNotIn(str(self.root),repr(fence)+str(fence.report()))
        self.assertFalse(fence.report()['foreign_cli_controlled'])


if __name__=='__main__':unittest.main()
