"""Real Web-tree Ext4 flags; service audit alone is isolated from this filesystem suite."""
from dataclasses import replace
import errno
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from installer import web_fence as a
import test_data_access_files as data_fixture
import test_inode_fence_files as inode_fixture


class WebFenceFiles(unittest.TestCase):
    fixture_root=inode_fixture.VOLUME

    def setUp(self):
        self.assertTrue(self.fixture_root.is_dir(),'Disposable Ext4 volume required')
        data_fixture.DataAccessTests.setUp(self)
        self.code=self.root/'code';self.code.mkdir(mode=0o755)
        for name in ('includes','bin','.github','uploads','uploads/empty'):(self.code/name).mkdir(mode=0o755)
        for name,mode,gid in [('index.php',0o644,0),('includes/db.php',0o640,self.account.pw_gid),
                              ('install.lock',0o640,self.account.pw_gid),('bin/tool',0o755,0),('.github/fixture',0o644,0)]:
            path=self.code/name;path.write_bytes(b'original');path.chmod(mode);os.chown(path,0,gid)
        self.web=Path(tempfile.mkdtemp(prefix='hestia-web-fence-',dir='/srv'));self.web.chmod(0o755)
        self.addCleanup(lambda:shutil.rmtree(self.web))
        self.mount(self.code,self.web)
        self.addCleanup(lambda:inode_fixture.fixture_clear(self.web))
        runtime=a.hd.h.HttpRuntime(replace(self.runtime.spec,webroot=self.web))
        self.barrier=a.hd.HttpDrainLease(a.hd.HttpDrain(runtime),self.lease,b'private-profile')
        audit=patch.object(self.barrier,'assert_held',side_effect=self.lease.assert_held)
        audit.start();self.addCleanup(audit.stop)

    def mount(self,source,target,*,tmpfs=False):
        args=['mount','-t','tmpfs','tmpfs',str(target)] if tmpfs else ['mount','--bind',str(source),str(target)]
        subprocess.run(args,check=True,capture_output=True)
        self.addCleanup(lambda:subprocess.run(['umount',str(target)],check=True,capture_output=True))

    def seal(self):
        fence=a.acquire(self.barrier,confirmed=True);self.addCleanup(fence.close);return fence

    def test_code_pointers_executables_and_hidden_metadata_are_all_protected(self):
        fence=self.seal()
        for name in ('index.php','includes/db.php','install.lock','bin/tool','.github/fixture'):
            path=self.web/name
            for operation in (lambda:path.write_bytes(b'bad'),path.unlink,lambda:path.rename(self.web/'replaced'),
                              lambda:path.chmod(0o600),lambda:os.utime(path,None),lambda:os.link(path,self.web/'linked')):
                with self.assertRaises(PermissionError) as caught:operation()
                self.assertEqual(caught.exception.errno,errno.EPERM)
            self.assertEqual(path.read_bytes(),b'original')
        for name in ('new.php','includes/conf_db_ia.php','uploads/empty/new'):
            with self.assertRaises(PermissionError):(self.web/name).write_bytes(b'bad')
        self.assertEqual((self.web/'bin/tool').stat().st_mode&0o777,0o755)
        self.assertTrue(fence.report()['web_activation_pointers_fenced'])

    def test_preopened_descriptor_and_alias_share_write_denial(self):
        alias=self.root/'alias';alias.mkdir();self.mount(self.web,alias)
        with (self.web/'index.php').open('r+b',buffering=0) as stream:
            self.seal()
            for operation in (lambda:stream.write(b'bad'),lambda:os.ftruncate(stream.fileno(),0),
                              lambda:(alias/'includes/db.php').write_bytes(b'bad'),lambda:(alias/'new.php').write_bytes(b'bad')):
                with self.assertRaises(PermissionError):operation()
        self.assertEqual((alias/'index.php').read_bytes(),b'original')

    def test_explicit_unseal_preserves_gate_and_original_executable_modes(self):
        fence=self.seal()
        with self.assertRaisesRegex(a.hd.m.MaintenanceError,'DATA_ACCESS_CLOSED'):self.lease.resume(confirmed=True)
        fence.unseal(confirmed=True)
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        self.assertEqual((self.web/'bin/tool').stat().st_mode&0o777,0o755)
        (self.web/'index.php').write_bytes(b'after-explicit-unseal')
        self.lease.resume(confirmed=True);self.assertEqual(self.scope.observe()['state'],'SERVING')

    def test_partial_closure_keeps_intent_and_exact_recovery_completes(self):
        flags=a._flags;changed=[]
        def fail(fd,value=None):
            if value is not None:
                changed.append(value)
                if len(changed)==4:raise OSError('injected')
            return flags(fd,value)
        with patch.object(a,'_flags',side_effect=fail),self.assertRaises(a.WebFenceError):self.seal()
        self.assertTrue((self.scope.directory/a.MARKER).exists())
        with a.recover(self.barrier,confirmed=True) as protected:protected.assert_held()
        with self.assertRaises(PermissionError):(self.web/'includes/db.php').write_bytes(b'bad')

    def test_fully_closed_recovery_never_reapplies_matching_immutable_flags(self):
        self.seal().close();flags=a._flags;changes=[]
        def reject_redundant_set(fd,value=None):
            if value is not None:
                if value==flags(fd):raise PermissionError(errno.EPERM,'immutable no-op refused')
                changes.append(value)
            return flags(fd,value)
        with patch.object(a,'_flags',side_effect=reject_redundant_set):
            with a.recover(self.barrier,confirmed=True) as protected:
                self.assertEqual(changes,[]);protected.assert_held()
                with self.assertRaises(PermissionError):(self.web/'index.php').write_bytes(b'bad')
                protected.unseal(confirmed=True)
        self.assertTrue(changes);self.assertTrue(all(not value&a.inode.IMMUTABLE for value in changes))
        self.assertFalse((self.scope.directory/a.MARKER).exists())
        (self.web/'index.php').write_bytes(b'resumable')

    def test_partial_release_requires_existing_exact_intent(self):
        fence=self.seal();flags=a._flags;changed=[]
        def fail(fd,value=None):
            if value is not None and not value&a.inode.IMMUTABLE:
                changed.append(value)
                if len(changed)==3:raise OSError('injected')
            return flags(fd,value)
        with patch.object(a,'_flags',side_effect=fail),self.assertRaises(a.WebFenceError):fence.unseal(confirmed=True)
        with self.assertRaises(a.WebFenceError):a.recover(self.barrier,confirmed=True)
        release=self.scope.directory/a.RELEASE;raw=release.read_bytes();release.write_bytes(b'{}')
        with self.assertRaisesRegex(a.WebFenceError,'JOURNAL_CHANGED'):a.recover_unseal(self.barrier,confirmed=True)
        release.write_bytes(raw);a.recover_unseal(self.barrier,confirmed=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists());self.assertFalse(release.exists())

    def test_flag_removal_and_changed_content_are_not_silently_repaired(self):
        fence=self.seal();path=self.web/'index.php';fd=os.open(path,a.inode.files.REGULAR)
        try:a._flags(fd,a._flags(fd)&~a.inode.IMMUTABLE)
        finally:os.close(fd)
        with self.assertRaisesRegex(a.WebFenceError,'CHANGED'):fence.assert_held()
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(a.WebFenceError,'JOURNAL_CHANGED'):a.recover(self.barrier,confirmed=True)
        self.assertEqual(path.read_bytes(),b'changed')

    def test_symlinks_hardlinks_missing_pointer_and_bounds_refuse_before_journal(self):
        link=self.web/'link';link.symlink_to(self.web/'index.php')
        with self.assertRaises(a.WebFenceError):self.seal()
        link.unlink();os.link(self.web/'index.php',link)
        with self.assertRaises(a.WebFenceError):self.seal()
        link.unlink()
        with patch.object(a,'MAX_ENTRIES',1),self.assertRaisesRegex(a.WebFenceError,'LIMIT'):self.seal()
        with patch.object(a,'MAX_FILE_BYTES',1),self.assertRaises(a.WebFenceError):self.seal()
        (self.web/'install.lock').unlink()
        with self.assertRaises(a.WebFenceError):self.seal()
        self.assertFalse((self.scope.directory/a.MARKER).exists())

    def test_nested_bind_and_tmpfs_refuse_without_weaker_fallback(self):
        self.mount(self.web/'bin',self.web/'uploads')
        with self.assertRaisesRegex(a.WebFenceError,'NESTED_MOUNT'):self.seal()
        self.assertFalse((self.scope.directory/a.MARKER).exists())
        self.mount(None,self.web,tmpfs=True)
        with self.assertRaises(a.WebFenceError):self.seal()
        self.assertFalse((self.scope.directory/a.MARKER).exists())

    def test_web_journal_blocks_data_reopening_until_explicit_unseal(self):
        data=data_fixture.DataAccessTests.acquire(self);fence=self.seal()
        with self.assertRaisesRegex(a.inode.da.DataAccessError,'INODES_CLOSED'):data.reopen(confirmed=True)
        fence.unseal(confirmed=True);data.reopen(confirmed=True)
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')


if __name__=='__main__':unittest.main()
