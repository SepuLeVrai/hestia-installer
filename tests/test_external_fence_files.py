"""Actual Ext4 reservation, namespace collision and recovery tests."""
import errno
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from installer import external_fence as a
import test_data_access_files as data_fixture
import test_inode_fence_files as inode_fixture


class ExternalFenceFiles(unittest.TestCase):
    fixture_root=inode_fixture.VOLUME

    def setUp(self):
        self.assertTrue(self.fixture_root.is_dir(),'Disposable Ext4 volume required')
        data_fixture.DataAccessTests.setUp(self)
        self.parents=(self.root/'etc-hestia',self.root/'var-lib')
        for parent in self.parents:parent.mkdir(mode=0o755)
        self.paths=(self.parents[0]/'conf_db_ia.php',self.parents[1]/'hestia-ai')
        paths=patch.object(a,'PATHS',self.paths);paths.start();self.addCleanup(paths.stop)
        self.addCleanup(lambda:[inode_fixture.fixture_clear(parent) for parent in self.parents])

    def seal(self):
        fence=a.acquire(self.lease,confirmed=True);self.addCleanup(fence.close);return fence

    def test_root_replacements_writes_metadata_and_links_denied_without_blocking_siblings(self):
        fence=self.seal();self.assertTrue(fence.report()['legacy_external_paths_reserved'])
        for path in self.paths:
            self.assertTrue(path.is_file());self.assertEqual(path.stat().st_mode&0o777,0)
            for action in (lambda:path.write_bytes(b'bad'),path.unlink,lambda:path.rename(path.with_name('renamed')),
                           lambda:path.chmod(0o600),lambda:os.utime(path,None),lambda:os.link(path,path.with_name('alias'))):
                with self.assertRaises(PermissionError):action()
            with self.assertRaises(FileExistsError):path.mkdir()
            with self.assertRaises(NotADirectoryError):(path/'payload').write_bytes(b'bad')
            sibling=path.with_name('unrelated');sibling.write_bytes(b'untouched');sibling.unlink()
        with self.assertRaisesRegex(a.m.MaintenanceError,'DATA_ACCESS_CLOSED'):self.lease.resume(confirmed=True)
        fence.close();a.assert_reservation(self.lease,fence._raw)
        with a.recover(self.lease,confirmed=True) as recovered:recovered.unseal(confirmed=True)
        self.assertTrue(all(not p.exists() and not list(p.parent.iterdir()) for p in self.paths))
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        self.lease.resume(confirmed=True)

    def test_existing_files_directories_links_and_missing_parent_are_never_adopted(self):
        path=self.paths[0];path.write_bytes(b'foreign')
        for shape in ('file','directory','symlink'):
            with self.assertRaises(a.ExternalFenceError):self.seal()
            self.assertFalse((self.scope.directory/a.PREPARE).exists())
            if shape=='file':self.assertEqual(path.read_bytes(),b'foreign');path.unlink();path.mkdir()
            elif shape=='directory':path.rmdir();path.symlink_to('/missing-external-fixture')
            else:path.unlink()
        self.parents[0].rmdir()
        with self.assertRaises(a.ExternalFenceError):self.seal()
        self.parents[0].mkdir(mode=0o755)
        self.assertFalse((self.scope.directory/a.PREPARE).exists())

    def test_prepared_private_stages_recover_before_any_external_publication(self):
        with patch.object(a.f,'_write',wraps=a.f._write) as written:
            original=written._mock_wraps
            def fail(gate,name,*args,**kwargs):
                if name==a.MARKER:raise OSError('injected')
                return original(gate,name,*args,**kwargs)
            written.side_effect=fail
            with self.assertRaises(a.ExternalFenceError):self.seal()
        self.assertTrue((self.scope.directory/a.PREPARE).exists())
        self.assertTrue(all(not p.exists() for p in self.paths))
        self.assertTrue(all(len(list(p.iterdir()))==1 for p in self.parents))
        with a.recover(self.lease,confirmed=True) as recovered:recovered.assert_held()

    def test_partial_publication_keeps_bound_inodes_and_recovers_exactly(self):
        flags=a._flags;writes=[]
        def fail(fd,value=None):
            if value is not None:
                writes.append(value)
                if len(writes)==2:raise OSError('injected')
            return flags(fd,value)
        with patch.object(a,'_flags',side_effect=fail),self.assertRaises(a.ExternalFenceError):self.seal()
        self.assertTrue((self.scope.directory/a.MARKER).exists());self.assertEqual(len(writes),2)
        before=[p.stat().st_ino for p in self.paths]
        with a.recover(self.lease,confirmed=True) as recovered:recovered.assert_held()
        self.assertEqual([p.stat().st_ino for p in self.paths],before)

    def test_target_collision_after_intent_is_preserved_and_never_certified(self):
        seal=a._seal
        def collide(lease,value):self.paths[0].write_bytes(b'foreign');return seal(lease,value)
        with patch.object(a,'_seal',side_effect=collide),self.assertRaises(a.ExternalFenceError):self.seal()
        self.assertEqual(self.paths[0].read_bytes(),b'foreign')
        with self.assertRaises(a.ExternalFenceError):a.recover(self.lease,confirmed=True)
        self.assertEqual(self.paths[0].read_bytes(),b'foreign')

    def test_partial_release_and_missing_main_journal_recover_only_from_full_intent(self):
        fence=self.seal();unlink=a.os.unlink
        def fail(name,*args,**kwargs):
            if name==a.RELEASE:raise OSError('injected after main journal removal')
            return unlink(name,*args,**kwargs)
        with patch.object(a.os,'unlink',side_effect=fail),self.assertRaises(a.ExternalFenceError):fence.unseal(confirmed=True)
        self.assertFalse((self.scope.directory/a.MARKER).exists());self.assertTrue((self.scope.directory/a.RELEASE).exists())
        self.assertTrue(all(not p.exists() for p in self.paths))
        with self.assertRaises(a.ExternalFenceError):a.recover(self.lease,confirmed=True)
        with self.assertRaisesRegex(a.m.MaintenanceError,'DATA_ACCESS_CLOSED'):self.lease.resume(confirmed=True)
        a.recover_unseal(self.lease,confirmed=True);self.lease.resume(confirmed=True)

    def test_release_stopped_between_names_is_resumable_without_recreating_them(self):
        fence=self.seal();unlink=a.os.unlink;removed=[]
        def fail(name,*args,**kwargs):
            if name.startswith('.hestia-external-') and not removed:removed.append(name);raise OSError('injected')
            return unlink(name,*args,**kwargs)
        with patch.object(a.os,'unlink',side_effect=fail),self.assertRaises(a.ExternalFenceError):fence.unseal(confirmed=True)
        self.assertFalse(self.paths[1].exists());self.assertTrue(self.paths[0].exists())
        a.recover_unseal(self.lease,confirmed=True)
        self.assertTrue(all(not list(p.iterdir()) for p in self.parents))

    def test_removed_flag_and_altered_or_extra_linked_stage_refuse_without_repair(self):
        fence=self.seal();fd=os.open(self.paths[0],a.inode.files.REGULAR)
        try:a._flags(fd,a._flags(fd)&~a.inode.IMMUTABLE)
        finally:os.close(fd)
        with self.assertRaises(a.ExternalFenceError):fence.assert_held()
        self.paths[0].write_bytes(b'changed')
        with self.assertRaises(a.ExternalFenceError):a.recover(self.lease,confirmed=True)
        self.assertEqual(self.paths[0].read_bytes(),b'changed')

    def test_bind_alias_denies_root_writes_and_target_mount_replacement_refuses(self):
        alias=self.root/'alias';alias.mkdir();subprocess.run(['mount','--bind',str(self.parents[0]),str(alias)],check=True)
        self.addCleanup(lambda:subprocess.run(['umount',str(alias)],check=True))
        fence=self.seal()
        with self.assertRaises(PermissionError):(alias/self.paths[0].name).write_bytes(b'bad')
        other=self.root/'replacement';other.mkdir();subprocess.run(['mount','--bind',str(other),str(self.parents[0])],check=True)
        try:
            with self.assertRaises(a.ExternalFenceError):fence.assert_held()
        finally:subprocess.run(['umount',str(self.parents[0])],check=True)

    def test_tmpfs_is_refused_before_preparation_with_no_weaker_fallback(self):
        subprocess.run(['mount','-t','tmpfs','tmpfs',str(self.parents[0])],check=True)
        try:
            with self.assertRaises(a.ExternalFenceError):self.seal()
            self.assertFalse((self.scope.directory/a.PREPARE).exists())
        finally:subprocess.run(['umount',str(self.parents[0])],check=True)

    def test_preopened_stage_descriptor_cannot_modify_published_reservation(self):
        seal=a._seal;opened=[]
        def hold(lease,value):
            entry=value['entries'][0]
            opened.append(os.open(Path(entry['path']).parent/entry['stage'],os.O_RDWR|os.O_NOFOLLOW))
            return seal(lease,value)
        try:
            with patch.object(a,'_seal',side_effect=hold):fence=self.seal()
            for action in (lambda:os.write(opened[0],b'bad'),lambda:os.ftruncate(opened[0],1)):
                with self.assertRaises(PermissionError):action()
            fence.assert_held()
        finally:
            for fd in opened:os.close(fd)

    def test_external_journals_block_data_reopening_until_explicit_release(self):
        data=data_fixture.DataAccessTests.acquire(self);fence=self.seal()
        with self.assertRaisesRegex(a.inode.da.DataAccessError,'INODES_CLOSED'):data.reopen(confirmed=True)
        fence.unseal(confirmed=True);data.reopen(confirmed=True);self.lease.resume(confirmed=True)


if __name__=='__main__':unittest.main()
