"""Real journal/lock crash boundaries in disposable Ext4 CI, isolated SQL/services."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from installer import mobile_activation_admission as n
import test_mobile_blocker_files as prior

t,v=n.t,n.v
base=prior.BlockerFilesTests

class ActivationFilesTests(unittest.TestCase):
    fixture_root=base.fixture_root
    mount=base.mount
    controller=base.controller
    write=staticmethod(base.write)
    close_handles=base.close_handles
    loaded=base.loaded
    window=base.window
    execute=base.execute
    def setUp(self):
        base.setUp(self);self.execute()
        self.enterContext(patch.object(v.o,'_provenance',return_value={'fixture_boot':'a'*32}))
        self.native=object.__new__(v.NativeRuntime)
        self.native.http=self.runtime;self.native.scope=self.scope;self.native.account=self.account
        self.native.original_profile=self.barrier._profile;self.native.order=[]
        w=self.activation_window()
        with patch.object(n.ActivationWindow,'assert_held',side_effect=self.lease.assert_held):
            self.record=t.ActivationRecord.begin(w,self.native)
    def activation_window(self):
        w=n.ActivationWindow(SimpleNamespace(state=self.state,lease=self.lease),
            Mock(spec=['assert_held']),Mock(spec=['assert_held']),Mock(spec=['assert_held']),Mock(),Mock(),Mock(),
            getattr(self,'record',None))
        return w
    def unseal(self):
        w=self.activation_window()
        with patch.object(n.ActivationWindow,'assert_held',side_effect=self.lease.assert_held):
            guard=self.record.release(w)
        self.assertTrue(w.closed);return guard
    def blocked_writer(self):
        with self.assertRaises(n.r.hd.m.MaintenanceError):
            with self.scope.writer():self.fail('writer admitted')
    def loaded_record(self):return t.ActivationRecord.load(self.native,self.backups,self.lease.lease_id,self.confirmation)
    def test_plan_is_private_and_parent_copies_unchanged(self):
        self.assertEqual(self.record.root.stat().st_mode&0o777,0o700)
        self.assertEqual((self.record.root/'plan.json').stat().st_mode&0o777,0o600)
        self.assertEqual(self.loaded_record()._raw,self.record._raw)
        for path,raw in self.saved.items():self.assertEqual(path.read_bytes(),raw)
        self.blocked_writer()
    def test_gate_release_retains_actual_exclusive_lock_until_close(self):
        guard=self.unseal();guard.assert_held()
        self.assertEqual(self.scope.observe()['state'],'SERVING');self.blocked_writer()
        with self.assertRaises(n.r.hd.m.MaintenanceError):self.lease.assert_held()
        self.lease.close()
        with self.scope.writer():pass
        with self.assertRaises(Exception):guard.assert_held()
    def test_native_resumed_receipt_precedes_maintenance_unlink(self):
        unlink=os.unlink;events=[]
        def tracked(name,**kwargs):
            if name=='maintenance.attempt':
                receipt=self.scope.directory/('resumed-'+self.lease.lease_id+'.json')
                self.assertEqual(receipt.read_bytes(),t.resumed(self.scope,self.lease.lease_id))
            if name in (t.s.MARKER,'maintenance.attempt'):events.append(name)
            return unlink(name,**kwargs)
        with patch.object(t.os,'unlink',side_effect=tracked):self.unseal()
        self.assertEqual(events,[t.s.MARKER,'maintenance.attempt'])
    def test_unarmed_missing_activation_marker_cannot_be_recreated(self):
        (self.scope.directory/t.s.MARKER).unlink()
        with self.assertRaises(v.ActivationError):self.record.restore_owned_blocker(self.lease)
        self.assertFalse((self.scope.directory/t.s.MARKER).exists());self.blocked_writer()
    def cut_unlink(self,name):
        unlink=os.unlink
        def cut(target,**kwargs):
            unlink(target,**kwargs)
            if target==name:raise OSError('lost unlink reply')
        with patch.object(t.os,'unlink',side_effect=cut),self.assertRaises(OSError):self.unseal()
    def test_cut_before_maintenance_unlink_restores_only_owned_activation(self):
        self.cut_unlink(t.s.MARKER);self.lease.assert_held();self.blocked_writer()
        with self.assertRaises(t.s.BlockerError):self.loaded()
        self.loaded_record().restore_owned_blocker(self.lease)
        self.assertEqual((self.scope.directory/t.s.MARKER).read_bytes(),self.record.marker())
        self.unseal().assert_held()
    def test_cut_after_maintenance_unlink_recovers_serving_lock_without_reclose(self):
        self.cut_unlink('maintenance.attempt');self.lease.close()
        with patch.object(n.r.hd.HttpDrain,'recover',side_effect=AssertionError('drain replay')):
            with self.loaded_record().serving_lock() as guard:
                guard.assert_held();self.blocked_writer()
        self.assertFalse((self.scope.directory/'maintenance.attempt').exists())
        with self.scope.writer():pass
    def test_partial_native_receipt_uses_existing_exact_prefix_recovery(self):
        self.cut_unlink(t.s.MARKER);self.record.restore_owned_blocker(self.lease)
        name='resumed-'+self.lease.lease_id+'.json';raw=t.resumed(self.scope,self.lease.lease_id)
        n.f._write(self.lease._directory,name,raw[:17],self.scope.web_gid)
        self.unseal().assert_held();self.assertEqual((self.scope.directory/name).read_bytes(),raw)
    def test_foreign_native_receipt_cannot_open_activity(self):
        name='resumed-'+self.lease.lease_id+'.json'
        n.f._write(self.lease._directory,name,b'foreign',self.scope.web_gid)
        with self.assertRaises(n.r.hd.m.MaintenanceError):self.unseal()
        self.lease.assert_held();self.blocked_writer()
    def test_reappearing_original_blocker_prevents_gate_release(self):
        self.write(self.scope.directory/t.s.OLD[0],self.state.originals[t.p.COPIES[1]])
        with self.assertRaises(n.r.hd.m.MaintenanceError):self.unseal()
        self.lease.assert_held();self.blocked_writer()
    def test_serving_recovery_rejects_changed_boot(self):
        self.unseal();self.lease.close()
        with patch.object(v.o,'_provenance',return_value={'fixture_boot':'b'*32}):
            with self.assertRaisesRegex(v.ActivationError,'BOOT_CHANGED'):self.loaded_record()
    def test_serving_recovery_requires_exact_resumed_bytes(self):
        self.unseal();self.lease.close()
        name=self.scope.directory/('resumed-'+self.lease.lease_id+'.json')
        name.write_bytes(b'{}')
        with self.assertRaises(v.ActivationError):
            with self.loaded_record().serving_lock():self.fail('accepted')
    def test_unknown_activation_journal_refuses_without_effect(self):
        self.write(self.record.root/'foreign.json',b'{}')
        with self.assertRaises(v.ActivationError):self.loaded_record()
        self.lease.assert_held();self.blocked_writer()
    def test_changed_parent_copy_refuses_without_effect(self):
        (self.resume_root/t.p.COPIES[1]).write_bytes(b'{}')
        with self.assertRaises(v.ActivationError):self.loaded_record()
        self.lease.assert_held();self.blocked_writer()
    def test_serving_recovery_cannot_run_while_first_real_lock_is_held(self):
        self.unseal()
        with self.assertRaisesRegex(v.ActivationError,'BUSY'):
            with self.loaded_record().serving_lock():self.fail('accepted')
    def test_admitted_transition_cannot_restore_any_blocker(self):
        self.unseal()
        with self.assertRaises(Exception):self.record.restore_owned_blocker(self.lease)
        self.assertFalse((self.scope.directory/t.s.MARKER).exists())

if __name__=='__main__':unittest.main()
