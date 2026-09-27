"""Lifecycle contracts; real UID permission tests run in the Debian file suite."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import stat
import unittest
from unittest.mock import Mock, patch

from installer import data_access as a


class DataAccessContractTests(unittest.TestCase):
    def test_consent_and_typed_live_barrier_precede_mutation(self):
        with patch.object(a,'_acquire') as start:
            for value in (False,1,'yes',None):
                with self.assertRaisesRegex(a.DataAccessError,'CONSENT_REQUIRED'):a.acquire(None,confirmed=value)
            for value in (None,{},Mock()):
                with self.assertRaisesRegex(a.DataAccessError,'LEASE_REQUIRED'):a.acquire(value,confirmed=True)
            start.assert_not_called()

    @contextmanager
    def operation(self,*,census_error=None):
        events=[];account=SimpleNamespace(pw_uid=991,pw_gid=991)
        runtime=SimpleNamespace(spec=SimpleNamespace(root=Path('/var/lib/fenced'),instance='a'*32),
            _inspect_configuration=lambda:events.append('inspect'))
        lease=SimpleNamespace(_directory=88,lease_id='b'*32)
        @contextmanager
        def directory(*args,**kwargs):
            try:yield 77
            finally:events.append('close-fd')
        def census(*args):
            events.append('census')
            if census_error:raise census_error
        with patch.object(a,'_inputs',return_value=account),patch.object(a.fs,'_directory',side_effect=directory), \
             patch.object(a.os,'fstat',return_value=SimpleNamespace(st_uid=0,st_gid=991,st_mode=stat.S_IFDIR|0o750,st_dev=2,st_ino=3)), \
             patch.object(a.fs,'_absent'),patch.object(a.f,'_write',side_effect=lambda *x:events.append('intent')), \
             patch.object(a.os,'fchmod',side_effect=lambda fd,mode:events.append(('mode',mode))), \
             patch.object(a.os,'fsync'),patch.object(a.hd,'identity_census',side_effect=census), \
             patch.object(a.DataAccessFence,'assert_held',side_effect=lambda:events.append('assert')), \
             patch.object(a.os,'unlink') as unlink:
            yield runtime,lease,events,unlink

    def test_durable_intent_precedes_closure_and_census_close_never_reopens(self):
        with self.operation() as (runtime,lease,events,unlink):
            with a._acquire(runtime,lease,confirmed=True,recover=False):pass
            self.assertLess(events.index('intent'),events.index(('mode',0o700)))
            self.assertLess(events.index(('mode',0o700)),events.index('census'))
            self.assertEqual([e for e in events if isinstance(e,tuple)],[('mode',0o700)])
            self.assertEqual(events[-1],'close-fd');unlink.assert_not_called()

    def test_failed_second_census_leaves_closed_intent_without_deletion(self):
        error=a.hd.HttpDrainError('HTTP_DRAIN_FOREIGN_IDENTITY_PROCESS')
        with self.operation(census_error=error) as (runtime,lease,events,unlink):
            with self.assertRaises(a.hd.HttpDrainError) as caught:
                a._acquire(runtime,lease,confirmed=True,recover=False)
            self.assertIs(caught.exception,error)
            self.assertEqual([e for e in events if isinstance(e,tuple)],[('mode',0o700)])
            self.assertIn('intent',events);self.assertEqual(events[-1],'close-fd');unlink.assert_not_called()

    def test_record_rejects_foreign_lease_inode_extra_fields_and_bool_numbers(self):
        runtime=SimpleNamespace(spec=SimpleNamespace(instance='a'*32,root=Path('/var/lib/fenced')))
        account=SimpleNamespace(pw_gid=991)
        value={'version':1,'instance':'a'*32,'lease_id':'b'*32,'root':'/var/lib/fenced/data',
            'device':2,'inode':3,'gid':991,'open_mode':0o750,'closed_mode':0o700}
        runtime._scope=lambda account:SimpleNamespace(_flag=lambda fd:a.p._json({'lease_id':'b'*32}))
        info=SimpleNamespace(st_uid=0,st_gid=991,st_mode=stat.S_IFDIR|0o700,st_dev=2,st_ino=3)
        with patch.object(a.os,'fstat',return_value=info),patch.object(a.fs,'_no_acl'):
            with patch.object(a.f,'_read',return_value=a.p._json(value)):
                self.assertEqual(a._record(runtime,account,1,2,lease_id='b'*32),a.p._json(value))
                with self.assertRaises(a.DataAccessError):a._record(runtime,account,1,2,lease_id='c'*32)
            for change in ({'version':True},{'inode':4},{'device':3},{'private':'secret'},
                {'lease_id':'c'*32},{'root':'/var/lib/foreign/data'},{'closed_mode':0o750}):
                with patch.object(a.f,'_read',return_value=a.p._json({**value,**change})):
                    with self.assertRaises(a.DataAccessError):a._record(runtime,account,1,2)

    def test_explicit_reopen_keeps_main_maintenance_and_rejects_weak_consent(self):
        with self.operation() as (runtime,lease,events,unlink):
            fence=a._acquire(runtime,lease,confirmed=True,recover=False)
            for value in (False,1,'yes',None):
                with self.assertRaisesRegex(a.DataAccessError,'CONSENT_REQUIRED'):fence.reopen(confirmed=value)
            self.assertEqual([e for e in events if isinstance(e,tuple)],[('mode',0o700)])
            fence.reopen(confirmed=True)
            self.assertEqual([e for e in events if isinstance(e,tuple)],[('mode',0o700),('mode',0o750)])
            unlink.assert_called_once_with(a.MARKER,dir_fd=88)


if __name__=='__main__':unittest.main()
