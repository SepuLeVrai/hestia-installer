"""Bound journal, explicit consent and private external reservation handles."""
from pathlib import Path
import pickle
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import external_fence as a


class ExternalFenceContracts(unittest.TestCase):
    def setUp(self):
        self.lease=SimpleNamespace(scope=SimpleNamespace(instance='a'*32),lease_id='b'*32,_directory=9)
        self.plan={'version':1,'instance':'a'*32,'lease_id':'b'*32,'nonce':'c'*32,'entries':[
            {'path':str(path),'stage':'.hestia-external-'+'c'*32+'-'+str(i),'parent_device':7,'parent_inode':10+i}
            for i,path in enumerate(a.PATHS)]}
        self.value={**self.plan,'entries':[{**e,'device':7,'inode':20+i,'flags':0x80000} for i,e in enumerate(self.plan['entries'])]}

    def test_strict_consent_and_real_lease_required_before_paths_or_journals(self):
        with patch.object(a.fs,'_directory') as opened,patch.object(a.f,'_write') as written:
            for consent in (False,1,None,'yes'):
                with self.assertRaisesRegex(a.ExternalFenceError,'CONSENT_REQUIRED'):a.acquire(self.lease,confirmed=consent)
            with self.assertRaisesRegex(a.ExternalFenceError,'LEASE_REQUIRED'):a.acquire(self.lease,confirmed=True)
            opened.assert_not_called();written.assert_not_called()

    def test_canonical_journal_binds_lease_paths_parent_inodes_and_exact_keys(self):
        raw=a.p._json(self.value);self.assertEqual(a._decode(self.lease,raw),self.value)
        variants=[{**self.value,'version':True},{**self.value,'lease_id':'d'*32},{**self.value,'unknown':1},
                  {**self.value,'nonce':'bad'},{**self.value,'entries':self.value['entries'][:1]}]
        for key,value in [('path','/etc/foreign'),('stage','other'),('inode',True),('flags',a.inode.IMMUTABLE)]:
            variants.append({**self.value,'entries':[{**self.value['entries'][0],key:value},self.value['entries'][1]]})
        for value in variants:
            with self.assertRaises(a.ExternalFenceError):a._decode(self.lease,a.p._json(value))
        with self.assertRaises(a.ExternalFenceError):a._decode(self.lease,raw+b' ')

    def test_preparation_has_no_unknown_inode_adoption_fields(self):
        self.assertEqual(a._decode(self.lease,a.p._json(self.plan),prepared=True),self.plan)
        with self.assertRaises(a.ExternalFenceError):a._decode(self.lease,a.p._json(self.value),prepared=True)
        with patch.object(a,'MAX_JOURNAL',1),self.assertRaises(a.ExternalFenceError):a._decode(self.lease,a.p._json(self.plan),prepared=True)

    def test_private_process_bound_close_does_not_modify_reservations(self):
        fence=a.ExternalFence(self.lease,a.p._json(self.value))
        self.assertNotIn('/etc',repr(fence))
        with self.assertRaises(TypeError):pickle.dumps(fence)
        with patch.object(a.os,'getpid',return_value=-1),self.assertRaisesRegex(a.ExternalFenceError,'LEASE_REQUIRED'):fence.close()
        with patch.object(a,'_release') as release:
            fence.close();fence.close();release.assert_not_called()
        with self.assertRaisesRegex(a.ExternalFenceError,'LEASE_REQUIRED'):fence.assert_held()

    def test_release_intent_contains_full_recoverable_binding_before_mutation(self):
        raw=a.p._json(self.value);fence=a.ExternalFence(self.lease,raw);events=[]
        def write(gate,name,data,gid,**kwargs):
            self.assertEqual((gate,name,data,gid,kwargs),(9,a.RELEASE,raw,0,{'mode':0o600}));events.append('intent')
        def fail(*args):events.append('release');raise OSError('private-detail')
        with patch.object(a,'_inputs'),patch.object(fence,'assert_held'),patch.object(a.f,'_write',side_effect=write),patch.object(a,'_release',side_effect=fail):
            with self.assertRaisesRegex(a.ExternalFenceError,'^EXTERNAL_FENCE_UNAVAILABLE$'):fence.unseal(confirmed=True)
        self.assertEqual(events,['intent','release']);self.assertFalse(fence._closed)


if __name__=='__main__':unittest.main()
