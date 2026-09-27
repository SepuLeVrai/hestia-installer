"""Closed contracts, journal ordering and filesystem refusal, without ioctl mutation."""
from contextlib import contextmanager
import os
from pathlib import Path
import pickle
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import inode_fence as a


class InodeFenceContracts(unittest.TestCase):
    def setUp(self):
        self.data=SimpleNamespace(_raw=b'private-data-fence',_data=12,
            _runtime=SimpleNamespace(spec=SimpleNamespace(root=Path('/var/lib/private'))),
            _lease=SimpleNamespace(_directory=11,scope=SimpleNamespace(instance='a'*32),lease_id='b'*32),
            assert_held=lambda:None)
        self.records=[dict(path='.',device=9,inode=10,mode=0o700,uid=0,gid=99,kind='directory',flags=0x80000),
            dict(path='file',device=9,inode=11,mode=0o600,uid=99,gid=99,kind='file',flags=0x80000)]
        self.raw=a.p._json(a._value(self.data,self.records))

    def test_consent_and_exact_type_reject_before_any_probe(self):
        with patch.object(a,'_walk') as walk,patch.object(a.files,'_new') as write:
            for consent in (False,1,None,'yes'):
                with self.assertRaisesRegex(a.InodeFenceError,'CONSENT_REQUIRED'):a.acquire(self.data,confirmed=consent)
            with self.assertRaisesRegex(a.InodeFenceError,'LEASE_REQUIRED'):a.acquire(self.data,confirmed=True)
            walk.assert_not_called();write.assert_not_called()

    def test_mount_requires_one_writable_ext4_and_exact_device(self):
        row='42 1 8:7 / /var/lib rw,relatime - ext4 /dev/loop0 rw\n'
        with patch.object(a,'_mount_id',return_value=42),patch.object(a.os,'fstat',return_value=SimpleNamespace(st_dev=os.makedev(8,7))):
            with patch.object(a,'_bounded',return_value=row):self.assertEqual(a._ext4(12),42)
            for bad in (row.replace('ext4','overlay'),row.replace('rw','ro'),row.replace('8:7','8:8'),row+row,row.replace('42','43')):
                with patch.object(a,'_bounded',return_value=bad),self.assertRaises(a.InodeFenceError):a._ext4(12)

    def test_canonical_journal_rejects_foreign_lease_extra_fields_bool_and_flags(self):
        value=a.strict_json_loads(self.raw)
        variants=[{**value,'lease_id':'c'*32},{**value,'version':True},{**value,'extra':1}]
        changed=a.strict_json_loads(self.raw);changed['entries'][0]['flags']|=a.IMMUTABLE;variants.append(changed)
        with patch.object(a,'_walk',return_value=[{**e,'flags':e['flags']|a.IMMUTABLE} for e in self.records]):
            with patch.object(a.f,'_read',return_value=self.raw):self.assertEqual(a._load(self.data)[0],self.raw)
            for changed in variants:
                with patch.object(a.f,'_read',return_value=a.p._json(changed)),self.assertRaisesRegex(a.InodeFenceError,'JOURNAL_CHANGED'):a._load(self.data)

    def test_durable_journal_precedes_first_ioctl_and_failure_never_unseals(self):
        events=[]
        def seal(*args,**kwargs):events.append(('seal',kwargs));raise OSError('private-path')
        with patch.object(a,'_inputs'),patch.object(a.fs,'_absent'),patch.object(a,'_walk',return_value=self.records), \
             patch.object(a.files,'_new',side_effect=lambda *args:events.append(('journal',args[2]))),patch.object(a,'_set',side_effect=seal):
            with self.assertRaisesRegex(a.InodeFenceError,'^INODE_FENCE_UNAVAILABLE$'):a.acquire(self.data,confirmed=True)
        self.assertEqual(events,[('journal',self.raw),('seal',{'closed':True})])

    def test_oversize_journal_refuses_before_any_persistence(self):
        with patch.object(a,'_inputs'),patch.object(a.fs,'_absent'),patch.object(a,'_walk',return_value=self.records), \
             patch.object(a,'MAX_JOURNAL',1),patch.object(a.files,'_new') as write,patch.object(a,'_set') as seal:
            with self.assertRaisesRegex(a.InodeFenceError,'LIMIT'):a.acquire(self.data,confirmed=True)
            write.assert_not_called();seal.assert_not_called()

    def test_closure_is_parent_first_release_is_children_first_and_syncs_each(self):
        events=[];values={}
        @contextmanager
        def opened(data,record):yield record['inode']
        def flags(fd,value=None):
            if value is not None:values[fd]=value;events.append(('set',fd,value))
            return values[fd]
        with patch.object(a,'_opened',side_effect=opened),patch.object(a,'_flags',side_effect=flags), \
             patch.object(a.os,'fsync',side_effect=lambda fd:events.append(('sync',fd))):
            a._set(self.data,self.records,closed=True);a._set(self.data,self.records,closed=False)
        self.assertEqual([e[1] for e in events if e[0]=='set'],[10,11,11,10])
        self.assertEqual([e[1] for e in events if e[0]=='sync'],[10,11,11,10])
        self.assertEqual(values,{10:0x80000,11:0x80000})

    def test_explicit_release_intent_precedes_flags_and_interruption_keeps_journal(self):
        fence=a.InodeFence(self.data,self.raw);events=[]
        def release(*args):events.append('release');raise OSError('injected')
        with patch.object(a,'_inputs'),patch.object(fence,'assert_held'),patch.object(a,'_load',return_value=(self.raw,self.records,[])), \
             patch.object(a.f,'_write',side_effect=lambda *args,**kw:events.append('intent')),patch.object(a,'_release',side_effect=release):
            with self.assertRaisesRegex(a.InodeFenceError,'^INODE_FENCE_UNAVAILABLE$'):fence.unseal(confirmed=True)
        self.assertEqual(events,['intent','release']);self.assertFalse(fence._closed)

    def test_recovery_of_release_requires_exact_intent_and_does_not_close_again(self):
        expected=a.p._json({'version':1,'fence_sha256':a.f._sha(self.raw)})
        with patch.object(a,'_inputs'),patch.object(a,'_load',return_value=(self.raw,self.records,[])),patch.object(a,'_release') as release:
            for raw in (b'{}',a.p._json({'version':True,'fence_sha256':a.f._sha(self.raw)})):
                with patch.object(a.f,'_read',return_value=raw),self.assertRaises(a.InodeFenceError):a.recover_unseal(self.data,confirmed=True)
            release.assert_not_called()
            with patch.object(a.f,'_read',return_value=expected):a.recover_unseal(self.data,confirmed=True)
            release.assert_called_once_with(self.data,self.raw,self.records)

    def test_release_removes_only_its_two_journals_after_verified_flags(self):
        events=[]
        with patch.object(a,'_set',side_effect=lambda *args,**kw:events.append('clear')), \
             patch.object(a,'_walk',return_value=self.records),patch.object(a.f,'_read',return_value=self.raw), \
             patch.object(a.os,'unlink',side_effect=lambda name,**kw:events.append(name)),patch.object(a.os,'fsync'):
            a._release(self.data,self.raw,self.records)
        self.assertEqual(events,['clear',a.RELEASE,a.MARKER])

    def test_handle_is_private_nonserializable_process_bound_and_close_has_no_io(self):
        fence=a.InodeFence(self.data,self.raw)
        self.assertNotIn('private',repr(fence).replace('private durable',''))
        with self.assertRaises(TypeError):pickle.dumps(fence)
        with patch.object(a.os,'getpid',return_value=-1),self.assertRaisesRegex(a.InodeFenceError,'LEASE_REQUIRED'):fence.assert_held()
        with patch.object(a,'_set') as mutate:
            fence.close();mutate.assert_not_called()
        with self.assertRaisesRegex(a.InodeFenceError,'LEASE_REQUIRED'):fence.assert_held()


if __name__=='__main__':unittest.main()
