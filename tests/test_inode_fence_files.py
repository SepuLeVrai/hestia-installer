"""Real Ext4 immutable flags and kernel denial, only in the disposable volume."""
import errno
import mmap
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

from installer import inode_fence as a
import test_data_access_files as data_fixture

VOLUME=Path('/var/lib/hestia-inode-tests')


def fixture_clear(root):
    """Only the disposable fixture may remove flags without product recovery."""
    if not root.exists(): return
    paths=list(root.rglob('*'))+[root]
    for path in paths:
        if path.is_symlink():continue
        fd=os.open(path,a.files.DIRECTORY if path.is_dir() else a.files.REGULAR)
        try:
            flags=a._flags(fd)
            if flags&a.IMMUTABLE:a._flags(fd,flags&~a.IMMUTABLE)
        finally:os.close(fd)


class InodeFenceFiles(unittest.TestCase):
    fixture_root=VOLUME
    acquire_data=data_fixture.DataAccessTests.acquire

    def setUp(self):
        self.assertTrue(VOLUME.is_dir(),'Disposable Ext4 volume required; no skip/fallback')
        data_fixture.DataAccessTests.setUp(self)
        self.addCleanup(lambda:fixture_clear(self.data))

    def seal(self,data=None):
        data=data or self.acquire_data();fence=a.acquire(data,confirmed=True)
        self.addCleanup(fence.close);return data,fence

    def mount(self,source,target,*,tmpfs=False):
        target.mkdir(exist_ok=True)
        args=['/usr/bin/mount','-t','tmpfs','tmpfs',str(target)] if tmpfs else ['/usr/bin/mount','--bind',str(source),str(target)]
        subprocess.run(args,check=True,capture_output=True)
        self.addCleanup(lambda:subprocess.run(['/usr/bin/umount',str(target)],check=True,capture_output=True))

    def test_root_mutations_are_denied_on_all_roots_and_explicit_unseal_keeps_gate(self):
        data,fence=self.seal();report=fence.report()
        self.assertTrue(report['ordinary_root_data_writes_fenced']);self.assertTrue(report['same_inode_alias_writes_fenced'])
        for name in (*a.da.h.DATA,'uploads'):
            root=self.data/name;file=root/'payload'
            operations=[lambda:file.write_bytes(b'bad'),lambda:(root/'new').write_bytes(b'bad'),
                file.unlink,lambda:file.rename(root/'renamed'),lambda:file.chmod(0o644),
                lambda:os.chown(file,0,0),lambda:os.utime(file,None),lambda:os.link(file,root/'hardlink')]
            for operation in operations:
                with self.assertRaises(PermissionError) as caught:operation()
                self.assertEqual(caught.exception.errno,errno.EPERM)
            self.assertEqual(file.read_bytes(),b'original')
        with self.assertRaisesRegex(a.da.DataAccessError,'INODES_CLOSED'):data.reopen(confirmed=True)
        fence.unseal(confirmed=True)
        self.assertEqual(self.data.stat().st_mode&0o777,0o700)
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        (self.data/'uploads/payload').write_bytes(b'root-write-after-explicit-unseal')
        data.reopen(confirmed=True);self.lease.resume(confirmed=True)
        self.assertEqual(self.scope.observe()['state'],'SERVING')

    def test_preopened_root_descriptor_cannot_write_or_truncate_after_seal(self):
        with (self.data/'uploads/payload').open('r+b',buffering=0) as stream:
            self.seal()
            for operation in (lambda:stream.write(b'bad'),lambda:os.ftruncate(stream.fileno(),0)):
                with self.assertRaises(PermissionError):operation()
            self.assertEqual(stream.read(),b'original')

    def test_existing_shared_writable_mapping_is_flushed_then_faults_on_write(self):
        path=self.data/'uploads/payload';path.write_bytes(b'0'*4096)
        body='import mmap,sys,resource\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\nf=open(sys.argv[1],"r+b");m=mmap.mmap(f.fileno(),4096);m[0:1]=b"A";print("ready",flush=True);sys.stdin.readline();m[0:1]=b"B";m.flush()'
        child=subprocess.Popen([sys.executable,'-c',body,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        def clean():
            if child.poll() is None:child.kill();child.wait(timeout=5)
            child.stdin.close();child.stdout.close()
        self.addCleanup(clean);self.assertEqual(child.stdout.readline(),b'ready\n')
        self.seal();self.assertEqual(path.read_bytes()[:1],b'A')
        child.stdin.write(b'go\n');child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),-signal.SIGBUS)
        self.assertEqual(path.read_bytes()[:1],b'A')

    def test_external_bind_alias_shares_protection_for_ordinary_root(self):
        alias=self.root/'alias';self.mount(self.data/'uploads',alias)
        (alias/'payload').write_bytes(b'original')
        data,fence=self.seal()
        self.assertEqual((alias/'payload').stat().st_ino,(self.data/'uploads/payload').stat().st_ino)
        for operation in (lambda:(alias/'payload').write_bytes(b'bad'),lambda:(alias/'new').write_bytes(b'bad'),lambda:(alias/'payload').unlink()):
            with self.assertRaises(PermissionError):operation()
        self.assertEqual((alias/'payload').read_bytes(),b'original');fence.assert_held()

    def test_tmpfs_and_nested_bind_mount_refuse_before_inode_journal(self):
        nested=self.data/'imports';self.mount(self.data/'tmp',nested)
        data=self.acquire_data()
        with self.assertRaisesRegex(a.InodeFenceError,'NESTED_MOUNT'):a.acquire(data,confirmed=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists())

    def test_unsupported_filesystem_has_no_immutable_journal_or_weak_fallback(self):
        self.mount(None,self.data,tmpfs=True)
        self.data.chmod(0o750);os.chown(self.data,0,self.account.pw_gid)
        data=self.acquire_data()
        with self.assertRaisesRegex(a.InodeFenceError,'FILESYSTEM_UNSUPPORTED'):a.acquire(data,confirmed=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists())
        self.assertEqual(self.data.stat().st_mode&0o777,0o700)

    def test_interrupted_closure_persists_and_recovery_completes_only_exact_tree(self):
        data=self.acquire_data();original=a._flags;writes=[]
        def fail(fd,value=None):
            if value is not None:
                writes.append(value)
                if len(writes)==3:raise OSError(errno.EIO,'injected')
            return original(fd,value)
        with patch.object(a,'_flags',side_effect=fail),self.assertRaises(a.InodeFenceError):a.acquire(data,confirmed=True)
        self.assertTrue((self.scope.directory/a.MARKER).is_file())
        self.assertTrue(original(data._data)&a.IMMUTABLE)
        data.close()
        with a.da.recover(self.runtime,self.lease,confirmed=True) as recovered_data:
            with a.recover(recovered_data,confirmed=True) as recovered:recovered.assert_held()
            with self.assertRaisesRegex(a.da.DataAccessError,'INODES_CLOSED'):recovered_data.reopen(confirmed=True)
        fd=os.open(self.data,a.files.DIRECTORY)
        try:self.assertTrue(original(fd)&a.IMMUTABLE)
        finally:os.close(fd)

    def test_interrupted_release_requires_exact_explicit_release_recovery(self):
        data,fence=self.seal();original=a._flags;writes=[]
        def fail(fd,value=None):
            if value is not None and not value&a.IMMUTABLE:
                writes.append(value)
                if len(writes)==2:raise OSError(errno.EIO,'injected')
            return original(fd,value)
        with patch.object(a,'_flags',side_effect=fail),self.assertRaises(a.InodeFenceError):fence.unseal(confirmed=True)
        self.assertTrue((self.scope.directory/a.RELEASE).is_file())
        with self.assertRaises(a.InodeFenceError):a.recover(data,confirmed=True)
        with self.assertRaisesRegex(a.da.DataAccessError,'INODES_CLOSED'):data.reopen(confirmed=True)
        a.recover_unseal(data,confirmed=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists());self.assertFalse((self.scope.directory/a.RELEASE).exists())
        self.assertEqual(self.data.stat().st_mode&0o777,0o700)

    def test_removed_flag_and_journal_drift_revoke_receipt_without_repair(self):
        data,fence=self.seal();file=self.data/'uploads/payload'
        fd=os.open(file,a.files.REGULAR)
        try:
            flags=a._flags(fd);a._flags(fd,flags&~a.IMMUTABLE)
            with self.assertRaisesRegex(a.InodeFenceError,'CHANGED'):fence.assert_held()
            self.assertFalse(a._flags(fd)&a.IMMUTABLE);a._flags(fd,flags)
        finally:os.close(fd)
        marker=self.scope.directory/a.MARKER;marker.write_bytes(b'{}')
        with self.assertRaisesRegex(a.InodeFenceError,'JOURNAL_CHANGED'):fence.assert_held()
        self.assertEqual(marker.read_bytes(),b'{}')

    def test_links_and_entry_limit_refuse_without_publishing_intent(self):
        data=self.acquire_data();file=self.data/'uploads/payload';link=self.root/'hardlink';os.link(file,link)
        with self.assertRaises(a.InodeFenceError):a.acquire(data,confirmed=True)
        link.unlink();link=self.data/'link';link.symlink_to(file)
        with self.assertRaises(a.InodeFenceError):a.acquire(data,confirmed=True)
        link.unlink()
        with patch.object(a,'MAX_ENTRIES',1),self.assertRaisesRegex(a.InodeFenceError,'LIMIT'):a.acquire(data,confirmed=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists())


if __name__=='__main__':unittest.main()
