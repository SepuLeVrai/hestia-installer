"""Pure failure-boundary contracts; service observations are isolated here."""
from copy import deepcopy
import pickle
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch
from installer import mobile_activation_admission as n
from installer.model import canonical_bytes, strict_json_loads

t,v=n.t,n.v

class ActivationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.record=object.__new__(t.ActivationRecord);self.records={};self.starts=[];self.live={}
        r=self.record;r.owner=lambda:canonical_bytes({'exact_owner':True});r.read=self.records.get
        def save(name,raw):
            if name in self.records:self.assertEqual(self.records[name],raw)
            else:self.records[name]=raw
        r.save=save
        def observed(role,*,active):
            if active:
                if role not in self.live:raise v.ActivationError('MOBILE_ACTIVATION_NOT_RUNNING')
                return deepcopy(self.live[role])
            if role in self.live:raise v.ActivationError('MOBILE_ACTIVATION_NOT_STOPPED')
            return {'unit':role,'invocation_id':'','active_enter_monotonic_us':0}
        def start(role):
            self.assertIn(role+'.intent.json',self.records)
            self.assertEqual(list(v.ROLES[:len(self.starts)]),self.starts);self.starts.append(role)
            self.live[role]={'unit':role,'invocation_id':str(len(self.starts))*32,
                'active_enter_monotonic_us':time.monotonic_ns()//1000}
        r.native=SimpleNamespace(unit=lambda role:role,configuration=Mock(),observed=observed,
            start=Mock(side_effect=start),running_after=lambda role:observed(role,active=True))
        self.guard=t.ActivityLock(r,-1,-1);self.guard.assert_held=Mock()
    def run_activation(self,check=False):return self.record.start_services(self.guard,check_only=check)
    def cut(self,target,*,after=True):
        original=self.record.native.start.side_effect
        def start(role):
            if role==target and not after:raise OSError('lost reply before effect')
            original(role)
            if role==target:raise OSError('lost reply after effect')
        self.record.native.start.side_effect=start
        with self.assertRaises(v.ActivationError):self.run_activation()
        self.record.native.start.side_effect=original
    def test_five_ordered_starts_have_durable_intents(self):
        result=self.run_activation();self.assertEqual(self.starts,list(v.ROLES))
        self.assertTrue(result['maintenance_released']);self.assertTrue(result['services_started'])
        self.assertFalse(result['boot_persistence']);self.assertFalse(result['phase6_complete'])
        self.assertFalse(result['automatic_start_retry_allowed'])
    def test_first_lost_start_reply_is_adopted_without_reissue(self):
        self.cut('php');self.run_activation();self.assertEqual(self.starts,list(v.ROLES))
    def test_last_lost_start_reply_is_adopted_without_reissue(self):
        self.cut('timer');self.run_activation();self.assertEqual(self.starts,list(v.ROLES))
    def test_intent_without_running_effect_never_retries(self):
        self.cut('foundation',after=False);before=list(self.starts)
        with self.assertRaises(v.ActivationError):self.run_activation()
        self.assertEqual(self.starts,before);self.assertNotIn('gateway.intent.json',self.records)
    def test_completed_check_is_read_only(self):
        self.run_activation();before=deepcopy(self.records)
        with patch.object(self.record,'save',side_effect=AssertionError('write')):self.run_activation(check=True)
        self.assertEqual(before,self.records);self.assertEqual(self.starts,list(v.ROLES))
    def test_incomplete_check_cannot_finish_remaining_roles(self):
        self.cut('php');before=deepcopy(self.records)
        with self.assertRaisesRegex(v.ActivationError,'INCOMPLETE'):self.run_activation(check=True)
        self.assertEqual(before,self.records);self.assertEqual(self.starts,['php'])
    def test_completed_resume_is_idempotent(self):
        result=self.run_activation();self.assertEqual(self.run_activation(),result)
        self.assertEqual(self.starts,list(v.ROLES))
    def test_changed_invocation_blocks_before_later_start(self):
        self.cut('foundation');self.live['php']['invocation_id']='f'*32
        with self.assertRaisesRegex(v.ActivationError,'INVOCATION_CHANGED'):self.run_activation()
        self.assertEqual(self.starts,['php','apache','foundation'])
    def test_effect_older_than_intent_is_rejected(self):
        self.cut('php');self.live['php']['active_enter_monotonic_us']=1
        with self.assertRaisesRegex(v.ActivationError,'INVOCATION_CHANGED'):self.run_activation()
        self.assertEqual(self.starts,['php'])
    def test_same_invocation_as_before_start_is_rejected(self):
        self.cut('php');value=strict_json_loads(self.records['php.intent.json'])
        value['before']['invocation_id']=self.live['php']['invocation_id'];self.records['php.intent.json']=canonical_bytes(value)
        with self.assertRaisesRegex(v.ActivationError,'INVOCATION_CHANGED'):self.run_activation()
        self.assertEqual(self.starts,['php'])
    def test_future_foreign_or_malformed_intent_precedes_observation_and_start(self):
        self.cut('php');original=self.records['php.intent.json']
        for changes in ({'not_before_monotonic_us':10**30},{'owner':{}},{'role':'gateway'},
                        {'before':{'unit':'php','invocation_id':'','active_enter_monotonic_us':False}}):
            value=strict_json_loads(original);value.update(changes);self.records['php.intent.json']=canonical_bytes(value)
            with patch.object(self.record.native,'observed',side_effect=AssertionError('observation')):
                with self.assertRaises(v.ActivationError):self.run_activation()
        self.assertEqual(self.starts,['php'])
    def test_later_intent_cannot_skip_unfinished_predecessor(self):
        self.records['gateway.intent.json']=self.record._intent('gateway',
            {'unit':'gateway','invocation_id':'','active_enter_monotonic_us':0})
        with self.assertRaisesRegex(v.ActivationError,'ORDER_CHANGED'):self.run_activation()
        self.assertEqual(self.starts,[])
    def test_forged_done_cannot_authorize_unstarted_services(self):
        self.records['done.json']=b'{}'
        with self.assertRaises(v.ActivationError):self.run_activation()
        self.assertEqual(self.starts,[])
    def test_orphan_receipt_rejected_before_effect(self):
        self.records['php.started.json']=b'{}'
        with self.assertRaises(v.ActivationError):self.run_activation()
        self.assertEqual(self.starts,[])
    def test_lock_loss_after_intent_prevents_start(self):
        def held():
            if 'php.intent.json' in self.records:raise v.ActivationError('MOBILE_ACTIVATION_LOCK_CLOSED')
        self.guard.assert_held.side_effect=held
        with self.assertRaisesRegex(v.ActivationError,'LOCK_CLOSED'):self.run_activation()
        self.assertEqual(self.starts,[]);self.assertIn('php.intent.json',self.records)
    def test_lookalike_lock_has_no_authority(self):
        with self.assertRaisesRegex(v.ActivationError,'LOCK_REQUIRED'):self.record.start_services(Mock(spec=t.ActivityLock))
        self.assertFalse(self.records)

class ActivationAdmissionTests(unittest.TestCase):
    def window(self):return n.ActivationWindow(Mock(),Mock(spec=['assert_held']),Mock(spec=['assert_held']),Mock(spec=['assert_held']),Mock(),Mock(),Mock())
    def test_consent_and_types_precede_files_sql_services(self):
        for confirmed,allow in ((False,True),(True,False),(1,True),(True,1)):
            with self.assertRaisesRegex(v.ActivationError,'CONSENT_REQUIRED'):
                n.execute(*([None]*9),action='apply',confirmed=confirmed,allow_global_read_lock=allow)
        with self.assertRaisesRegex(v.ActivationError,'INPUT_REJECTED'):
            n.execute(*([None]*9),action='apply',confirmed=True,allow_global_read_lock=True)
    def test_window_checks_current_archives_sql_configuration_schedulers(self):
        w=self.window()
        with patch.object(n,'transition_envelope',return_value={}):w.assert_held()
        w.control.live.assert_called_once_with(locked=w.locked);w.archives.check.assert_called_once()
        w.envelope.assert_called_once();self.assertEqual(w.fence.assert_held.call_count,2)
        self.assertEqual(w.schedulers.assert_held.call_count,2)
    def test_closed_forked_or_lost_fence_window_refuses_before_live_checks(self):
        for cause in ('closed','forked','fence'):
            w=self.window()
            if cause=='closed':w.closed=True
            if cause=='forked':w.pid+=1
            if cause=='fence':w.fence.assert_held.side_effect=RuntimeError('private sql value')
            with self.assertRaises(v.ActivationError):w.assert_held()
            w.control.live.assert_not_called()
    def test_windows_and_locks_cannot_be_serialized(self):
        for value in (self.window(),t.ActivityLock(None,-1,-1)):
            with self.assertRaises(TypeError):pickle.dumps(value)
    def test_gate_release_requires_its_exact_window(self):
        r=object.__new__(t.ActivationRecord)
        for window in (None,Mock(spec=n.ActivationWindow),self.window()):
            with self.assertRaisesRegex(v.ActivationError,'LIVE_ADMISSION_REQUIRED'):r.release(window)
    def test_plan_creation_requires_live_admission(self):
        with self.assertRaisesRegex(v.ActivationError,'LIVE_ADMISSION_REQUIRED'):t.ActivationRecord.begin(Mock(),Mock())
    def test_closed_forked_activity_lock_refuses_before_record_read(self):
        for forked in (False,True):
            r=Mock();guard=t.ActivityLock(r,-1,-1)
            if forked:guard._pid+=1
            else:guard._closed=True
            with self.assertRaisesRegex(v.ActivationError,'LOCK_CLOSED'):guard.assert_held()
            r.check.assert_not_called()
    def test_original_drain_census_still_refuses_foundation_and_gateway(self):
        for role in ('foundation','gateway'):
            with patch.object(n.r.hd,'_identity_census') as scan:
                with self.assertRaises(n.r.hd.HttpDrainError):
                    n.r.hd.identity_census(19000,19000,('hestia-'+'a'*32+'-'+role+'.service',))
                scan.assert_not_called()
    def test_invocation_reader_refuses_alias_job_or_duplicate_fields(self):
        unit='hestia-'+'a'*32+'-php.service'
        rows={'Id':unit,'LoadState':'loaded','ActiveState':'inactive','SubState':'dead',
            'InvocationID':'','ActiveEnterTimestampMonotonic':'0','Job':''}
        def read(value):return ('\n'.join(k+'='+x for k,x in value.items())+'\n').encode()
        with patch.object(v.h.p,'_safe_path'),patch.object(v.o,'_capture',return_value=read(rows)):
            self.assertEqual(v.invocation(unit),rows)
        for changes in ({'Id':'foreign.service'},{'Job':'123'},{'InvocationID':'x'*32},{'LoadState':'not-found'}):
            with patch.object(v.h.p,'_safe_path'),patch.object(v.o,'_capture',return_value=read({**rows,**changes})):
                with self.assertRaises(v.ActivationError):v.invocation(unit)
        with patch.object(v.h.p,'_safe_path'),patch.object(v.o,'_capture',return_value=read(rows)+b'Job=\n'):
            with self.assertRaises(v.ActivationError):v.invocation(unit)

if __name__=='__main__':unittest.main()
