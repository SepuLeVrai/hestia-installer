"""Configuration journal identity, consent and durable transition contracts."""
from contextlib import contextmanager
from pathlib import Path
import pickle
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import configuration_fence as a


class ConfigurationFenceContracts(unittest.TestCase):
    def setUp(self):
        self.lease=SimpleNamespace(_directory=11,scope=SimpleNamespace(instance='a'*32,directory=Path('/var/lib/slot/maintenance')),lease_id='b'*32,assert_held=lambda:None)
        self.configuration=SimpleNamespace(assert_held=lambda:None)
        self.records=[dict(path='.',device=9,inode=10,uid=0,gid=99,mode=0o750,kind='directory',bytes=0,sha256=None,flags=0x80000)]
        self.raw=a.p._json(a._value(self.lease,self.records))
        @contextmanager
        def directory(*args,**kwargs):yield 12
        self.directory=directory

    def test_consent_and_exact_types_reject_before_opening(self):
        with patch.object(a.fs,'_directory') as opened:
            for value in (False,1,None,'yes'):
                with self.assertRaisesRegex(a.ConfigurationFenceError,'CONSENT_REQUIRED'):
                    a.acquire(self.lease,self.configuration,confirmed=value)
            with self.assertRaisesRegex(a.ConfigurationFenceError,'LEASE_REQUIRED'):
                a.acquire(self.lease,self.configuration,confirmed=True)
            opened.assert_not_called()

    def test_journal_binds_exact_lease_root_entries_and_private_digests(self):
        value=a.strict_json_loads(self.raw)
        variants=[{**value,'version':True},{**value,'lease_id':'c'*32},{**value,'root':'/var/lib/other'},
                  {**value,'excluded_subtree':'other'},{**value,'unknown':1}]
        with patch.object(a,'_walk',return_value=self.records):
            with patch.object(a.f,'_read',return_value=self.raw):self.assertEqual(a._load(self.lease,self.configuration,12)[0],self.raw)
            for value in variants:
                with patch.object(a.f,'_read',return_value=a.p._json(value)),self.assertRaisesRegex(a.ConfigurationFenceError,'JOURNAL_CHANGED'):
                    a._load(self.lease,self.configuration,12)

    def test_journal_is_durable_before_first_mutation_and_failure_keeps_it(self):
        events=[]
        def fail(*args,**kwargs):events.append('flags');raise OSError('private-detail')
        with patch.object(a,'_inputs'),patch.object(a.fs,'_directory',side_effect=self.directory),patch.object(a.fs,'_absent'), \
             patch.object(a,'_walk',return_value=self.records),patch.object(a.inode.files,'_new',side_effect=lambda *args:events.append('journal')), \
             patch.object(a,'_set',side_effect=fail):
            with self.assertRaisesRegex(a.ConfigurationFenceError,'^CONFIGURATION_FENCE_UNAVAILABLE$'):
                a.acquire(self.lease,self.configuration,confirmed=True)
        self.assertEqual(events,['journal','flags'])

    def test_journal_limit_refuses_without_any_persistence_or_flags(self):
        with patch.object(a,'_inputs'),patch.object(a.fs,'_directory',side_effect=self.directory),patch.object(a.fs,'_absent'), \
             patch.object(a,'_walk',return_value=self.records),patch.object(a,'MAX_JOURNAL',1), \
             patch.object(a.inode.files,'_new') as write,patch.object(a,'_set') as mutate:
            with self.assertRaisesRegex(a.ConfigurationFenceError,'LIMIT'):a.acquire(self.lease,self.configuration,confirmed=True)
            write.assert_not_called();mutate.assert_not_called()

    def test_interrupted_unseal_preserves_explicit_intent_and_never_closes_handle(self):
        manager=Mock();fence=a.ConfigurationFence(self.lease,self.configuration,manager,12,self.raw);events=[]
        def fail(*args):events.append('release');raise OSError('private-detail')
        with patch.object(a,'_inputs'),patch.object(fence,'assert_held'),patch.object(a,'_load',return_value=(self.raw,self.records,[])), \
             patch.object(a.f,'_write',side_effect=lambda *args,**kwargs:events.append('intent')),patch.object(a,'_release',side_effect=fail):
            with self.assertRaisesRegex(a.ConfigurationFenceError,'^CONFIGURATION_FENCE_UNAVAILABLE$'):fence.unseal(confirmed=True)
        self.assertEqual(events,['intent','release']);self.assertFalse(fence._closed);manager.close.assert_not_called()

    def test_private_handle_process_binding_and_close_never_unseal(self):
        manager=Mock();fence=a.ConfigurationFence(self.lease,self.configuration,manager,12,self.raw)
        self.assertNotIn('/var/lib',repr(fence))
        with self.assertRaises(TypeError):pickle.dumps(fence)
        with patch.object(a.os,'getpid',return_value=-1),self.assertRaisesRegex(a.ConfigurationFenceError,'LEASE_REQUIRED'):fence.assert_held()
        with patch.object(a,'_set') as mutate:
            fence.close();fence.close();mutate.assert_not_called();manager.close.assert_called_once()
        with self.assertRaisesRegex(a.ConfigurationFenceError,'LEASE_REQUIRED'):fence.assert_held()


if __name__=='__main__':unittest.main()
