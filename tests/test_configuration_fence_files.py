"""Real Ext4 configuration closure and explicit recovery on a disposable volume."""
from contextlib import ExitStack
import errno
import os
import subprocess
import unittest
from unittest.mock import patch

from installer import configuration_fence as a
import test_data_access_files as data_fixture
import test_inode_fence_files as inode_fixture


class ConfigurationFenceFiles(unittest.TestCase):
    fixture_root=inode_fixture.VOLUME

    def setUp(self):
        self.assertTrue(self.fixture_root.is_dir(),'Disposable Ext4 volume required')
        data_fixture.DataAccessTests.setUp(self)
        self.config=self.scope.directory.parent
        self.addCleanup(lambda:inode_fixture.fixture_clear(self.config))
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.conf=self.stack.enter_context(a.fs._directory(self.config))
        for name,mode in [('assistant.json',0o660),('database.json',0o640),('db.php',0o640),('seal.json',0o640)]:
            a.f._write(self.conf,name,b'private-fixture-only',self.account.pw_gid,mode=mode)
        self.web=self.root/'web';(self.web/'includes').mkdir(parents=True)
        self.configuration=self.stack.enter_context(a.admission.acquire(self.conf,self.web,self.account.pw_gid))

    def seal(self):
        fence=a.acquire(self.lease,self.configuration,confirmed=True);self.addCleanup(fence.close);return fence

    def test_root_writes_replacements_and_metadata_changes_are_denied(self):
        fence=self.seal()
        for name in ('assistant.json','database.json','db.php','seal.json','assistant-edit.lock'):
            path=self.config/name;original=path.read_bytes()
            for operation in (lambda:path.write_bytes(b'bad'),path.unlink,lambda:path.rename(self.config/'replaced'),
                              lambda:path.chmod(0o600),lambda:os.chown(path,0,0),lambda:os.utime(path,None)):
                with self.assertRaises(PermissionError) as caught:operation()
                self.assertEqual(caught.exception.errno,errno.EPERM)
            self.assertEqual(path.read_bytes(),original)
        with self.assertRaises(PermissionError):(self.config/'new-setting').write_bytes(b'bad')
        fence.assert_held();self.assertTrue(fence.report()['ordinary_root_settings_writes_fenced'])

    def test_preopened_descriptor_and_external_bind_alias_cannot_modify_settings(self):
        alias=self.root/'alias';alias.mkdir()
        subprocess.run(['/usr/bin/mount','--bind',str(self.config),str(alias)],check=True,capture_output=True)
        self.addCleanup(lambda:subprocess.run(['/usr/bin/umount',str(alias)],check=True,capture_output=True))
        with (self.config/'assistant.json').open('r+b',buffering=0) as stream:
            self.seal()
            for operation in (lambda:stream.write(b'bad'),lambda:os.ftruncate(stream.fileno(),0),
                              lambda:(alias/'assistant.json').write_bytes(b'bad'),lambda:(alias/'new').write_bytes(b'bad')):
                with self.assertRaises(PermissionError):operation()
        self.assertEqual((alias/'assistant.json').read_bytes(),b'private-fixture-only')

    def test_maintenance_journals_remain_writable_but_resume_requires_unseal(self):
        fence=self.seal();journal=self.scope.directory/'fixture-journal'
        journal.write_bytes(b'private');journal.unlink();fence.assert_held()
        with self.assertRaisesRegex(a.m.MaintenanceError,'DATA_ACCESS_CLOSED'):self.lease.resume(confirmed=True)
        fence.unseal(confirmed=True)
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        self.assertFalse((self.scope.directory/a.MARKER).exists())
        (self.config/'database.json').write_bytes(b'explicitly-unsealed')
        self.lease.resume(confirmed=True);self.assertEqual(self.scope.observe()['state'],'SERVING')

    def test_partial_closure_stays_journalled_and_recovery_completes(self):
        flags=a._flags;changes=[]
        def fail(fd,value=None):
            if value is not None:
                changes.append(value)
                if len(changes)==3:raise OSError('injected')
            return flags(fd,value)
        with patch.object(a,'_flags',side_effect=fail),self.assertRaises(a.ConfigurationFenceError):self.seal()
        self.assertTrue((self.scope.directory/a.MARKER).exists())
        with a.recover(self.lease,self.configuration,confirmed=True) as recovered:recovered.assert_held()
        with self.assertRaises(PermissionError):(self.config/'database.json').write_bytes(b'bad')
        with self.assertRaisesRegex(a.m.MaintenanceError,'DATA_ACCESS_CLOSED'):self.lease.resume(confirmed=True)

    def test_partial_unseal_needs_exact_release_intent_recovery(self):
        fence=self.seal();flags=a._flags;changes=[]
        def fail(fd,value=None):
            if value is not None and not value&a.inode.IMMUTABLE:
                changes.append(value)
                if len(changes)==2:raise OSError('injected')
            return flags(fd,value)
        with patch.object(a,'_flags',side_effect=fail),self.assertRaises(a.ConfigurationFenceError):fence.unseal(confirmed=True)
        with self.assertRaises(a.ConfigurationFenceError):a.recover(self.lease,self.configuration,confirmed=True)
        release=self.scope.directory/a.RELEASE;raw=release.read_bytes();release.write_bytes(b'{}')
        with self.assertRaisesRegex(a.ConfigurationFenceError,'JOURNAL_CHANGED'):a.recover_unseal(self.lease,self.configuration,confirmed=True)
        release.write_bytes(raw);a.recover_unseal(self.lease,self.configuration,confirmed=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists());self.assertFalse(release.exists())

    def test_removed_flag_or_modified_journal_invalidates_without_repair(self):
        fence=self.seal();path=self.config/'database.json';fd=os.open(path,a.inode.files.REGULAR)
        try:
            flags=a._flags(fd);a._flags(fd,flags&~a.inode.IMMUTABLE)
            with self.assertRaisesRegex(a.ConfigurationFenceError,'CHANGED'):fence.assert_held()
            self.assertFalse(a._flags(fd)&a.inode.IMMUTABLE);a._flags(fd,flags)
        finally:os.close(fd)
        journal=self.scope.directory/a.MARKER;journal.write_bytes(b'{}')
        with self.assertRaisesRegex(a.ConfigurationFenceError,'JOURNAL_CHANGED'):fence.assert_held()
        self.assertEqual(journal.read_bytes(),b'{}')

    def test_links_extra_directories_limits_and_foreign_mount_refuse_before_journal(self):
        link=self.config/'linked';link.symlink_to(self.config/'database.json')
        with self.assertRaises(a.ConfigurationFenceError):self.seal()
        link.unlink();os.link(self.config/'database.json',link)
        with self.assertRaises(a.ConfigurationFenceError):self.seal()
        link.unlink();link.mkdir()
        with self.assertRaises(a.ConfigurationFenceError):self.seal()
        link.rmdir()
        with patch.object(a,'MAX_ENTRIES',1),self.assertRaisesRegex(a.ConfigurationFenceError,'LIMIT'):self.seal()
        with patch.object(a,'MAX_FILE_BYTES',1),self.assertRaises(a.ConfigurationFenceError):self.seal()
        path=self.config/'database.json';outside=self.root/'outside';outside.write_bytes(path.read_bytes());outside.chmod(0o640);os.chown(outside,0,self.account.pw_gid)
        subprocess.run(['/usr/bin/mount','--bind',str(outside),str(path)],check=True,capture_output=True)
        try:
            with self.assertRaisesRegex(a.ConfigurationFenceError,'NESTED_MOUNT'):self.seal()
        finally:subprocess.run(['/usr/bin/umount',str(path)],check=True,capture_output=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists())

    def test_configuration_journal_blocks_data_reopening_after_data_unseal(self):
        data=data_fixture.DataAccessTests.acquire(self);fence=self.seal()
        with self.assertRaisesRegex(a.inode.da.DataAccessError,'INODES_CLOSED'):data.reopen(confirmed=True)
        fence.unseal(confirmed=True);data.reopen(confirmed=True)
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')


if __name__=='__main__':unittest.main()
