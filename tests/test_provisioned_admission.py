"""Real advisory locks and protected absence checks, without SQL substitutes."""
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

from installer import provisioned_admission as a


class ConfigurationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/var/lib')))
        self.web=self.root/'web';(self.web/'includes').mkdir(parents=True)
        self.conf=self.stack.enter_context(a.fs._directory(self.root))
        a.f._write(self.conf,'assistant.json',b'{"fixture":true}',0,mode=0o660)

    def acquire(self): return a.acquire(self.conf,self.web,0)

    def test_both_settings_writers_excluded_and_nested_readers_allowed_until_release(self):
        with self.acquire() as lease:
            with self.acquire() as nested: nested.assert_held()
            for name in ('assistant-edit.lock','assistant.json'):
                with (self.root/name).open('rb') as stream:
                    with self.assertRaises(BlockingIOError):fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            lease.assert_held()
        for name in ('assistant-edit.lock','assistant.json'):
            with (self.root/name).open('rb') as stream:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with self.assertRaises(a.AdmissionError):lease.assert_held()

    def test_existing_writer_on_either_lock_refuses_admission(self):
        a.f._write(self.conf,'assistant-edit.lock',b'',0,mode=0o600)
        for name in ('assistant-edit.lock','assistant.json'):
            with (self.root/name).open('rb') as stream:
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(a.AdmissionError,'SETTINGS_BUSY'):
                    with self.acquire():self.fail('writer admitted')
        with self.acquire():pass

    def test_same_bytes_replaced_inode_cannot_certify(self):
        with self.assertRaisesRegex(a.AdmissionError,'CONFIGURATION_CHANGED'):
            with self.acquire() as lease:
                path=self.root/'assistant.json';data=path.read_bytes();path.unlink()
                a.f._write(self.conf,'assistant.json',data,0,mode=0o660)
                lease.assert_held()

    def test_unfinished_settings_attempt_is_not_adopted(self):
        name='assistant-'+'a'*32+'.attempt'
        a.f._write(self.conf,name,a.f.p._json({'version':1,'request_id':'a'*32}),0,mode=0o600)
        with self.assertRaises(FileNotFoundError):
            with self.acquire():self.fail('unfinished edit admitted')
        self.assertTrue((self.root/name).exists())

    def test_absence_rejects_existing_leaf_links_and_unsafe_parent(self):
        absent=self.root/'missing'/'config.php';a._absent(absent)
        (self.root/'missing').symlink_to(self.web,target_is_directory=True)
        with self.assertRaises(OSError):a._absent(absent)
        leaf=self.root/'legacy.php';leaf.symlink_to('/nonexistent-fixture')
        with self.assertRaisesRegex(a.AdmissionError,'EXTERNAL_STORAGE'):a._absent(leaf)
        unsafe=self.root/'unsafe';unsafe.mkdir(mode=0o777);unsafe.chmod(0o777)
        with self.assertRaises(a.fs.AccountConfigurationError):a._absent(unsafe/'absent')

    def test_legacy_configuration_never_executed_and_appearance_invalidates_window(self):
        path=self.web/'includes/conf_db_ia.php';marker=self.root/'executed'
        with self.assertRaisesRegex(a.AdmissionError,'EXTERNAL_STORAGE'):
            with self.acquire():path.write_text('<?php file_put_contents('+repr(str(marker))+',"bad");')
        self.assertFalse(marker.exists())
        with self.assertRaisesRegex(a.AdmissionError,'EXTERNAL_STORAGE'):
            with self.acquire():self.fail('legacy config admitted')

    def test_lease_is_private_nonserializable_and_process_bound(self):
        with self.acquire() as lease:
            self.assertNotIn(str(self.root),repr(lease))
            with self.assertRaises(TypeError):pickle.dumps(lease)
            with patch.object(a.os,'getpid',return_value=-1),self.assertRaises(a.AdmissionError):lease.assert_held()
