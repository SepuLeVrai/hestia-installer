"""Mobile boot consent, sealed assets and no-replay epoch boundaries."""
from contextlib import nullcontext
from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import mobile_boot_runtime as b, mobile_boot_plan as p, frozen_shared_public as frozen
from installer.model import InstallerError, Receipt
from installer.operations import OperationContext, SecretVault, RecoveryDecision
from installer.transaction import StateJournal
from test_shared_public_runtime import selected


def profile():
    shared = selected()
    return b.selection(shared, {'web': shared['preparation']['parents']['web'],
        'shared_public': 'b'*64, 'shared_journal': 'c'*64})


class MobileBootProfileTests(unittest.TestCase):
    def test_unit_only_adds_private_guard_and_orders_after_web_before_https(self):
        runtime = b.MobileBootRuntime(profile())
        with patch('subprocess.run', side_effect=AssertionError('native')):
            units = runtime.units()
        self.assertEqual(set(units), {runtime.target}); self.assertEqual(runtime.dependencies(), {})
        raw = units[runtime.target]
        self.assertIn(('Requires=' + runtime.shared.boot.target).encode(), raw)
        self.assertIn(('Before=' + runtime.shared.web.unit('https')).encode(), raw)
        self.assertIn(b'ConditionPathExists=!', raw); self.assertIn(b'Restart=no', raw)
        self.assertNotIn(b'ExecStartPre=', raw)

    def test_source_bundle_includes_exact_foundation_template_without_changing_old_bundle(self):
        files = b.code_files()
        self.assertIn(b.ASSET, files); self.assertNotIn(b.ASSET, b.boot.code_files())
        self.assertEqual(files[b.ASSET], (b.boot.SOURCE.parent / b.ASSET).read_bytes())

    def test_closed_profile_rejects_extra_assets_paths_and_parent_drift(self):
        for mutate in (lambda v: v.update(version=True), lambda v: v.update(command='start'),
            lambda v: v['code'].pop(b.ASSET), lambda v: v['code'].update({'installer/private/foreign.template':'a'*64}),
            lambda v: v['code'].update({'../foreign.py':'a'*64}), lambda v: v['parents'].update(web='f'*64)):
            value = profile(); mutate(value)
            with self.assertRaises(Exception): b.MobileBootRuntime(value)


class MobileBootEnrollmentTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-mobile-boot-', dir='/var/lib'); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.units = self.root / 'units'; self.units.mkdir(); (self.units / 'multi-user.target.wants').mkdir()
        self.enterContext(patch.object(b.boot.h.drain, 'UNIT_ROOT', self.units))
        self.enterContext(patch.object(b.boot.h, '_unit_absent'))
        self.command = self.enterContext(patch.object(b.boot.h, '_command'))
        self.r = b.MobileBootRuntime(profile()); self.r.root = self.root / 'mobile-boot'
        self.live = self.enterContext(patch.object(self.r, 'live'))
        self.stage = b.MobileBootOperation(self.r, 'stage'); self.enable = b.MobileBootOperation(self.r, 'enable')
        self.context = lambda op: OperationContext('a'*32, op.spec.as_dict(), Receipt().as_dict(), SecretVault())

    def enroll(self):
        self.stage.apply(self.context(self.stage)); self.enable.apply(self.context(self.enable))

    def test_sealed_template_and_code_enrollment_never_starts_or_changes_original_units(self):
        sentinel = self.units / self.r.foundation.unit; sentinel.write_bytes(b'original')
        self.enroll(); self.r.configuration()
        self.assertEqual(sentinel.read_bytes(), b'original')
        self.assertEqual([c.args[0][-1] for c in self.command.call_args_list], ['daemon-reload', 'daemon-reload'])
        self.assertEqual(os.readlink(self.r.link), '../' + self.r.target)
        self.assertEqual((self.r.root / 'code' / b.ASSET).read_bytes(), b.code_files()[b.ASSET])
        self.assertEqual((self.r.root / 'code' / b.ASSET).stat().st_mode & 0o777, 0o600)

    def test_completed_lost_stage_and_enable_replies_are_read_only(self):
        self.enroll(); self.command.reset_mock()
        for operation in (self.stage, self.enable):
            self.assertEqual(operation.recover(self.context(operation), 'apply').decision, RecoveryDecision.APPLIED)
        self.command.assert_not_called()

    def test_template_drift_refuses_recovery_without_repair(self):
        self.enroll(); asset = self.r.root / 'code' / b.ASSET
        asset.write_bytes(asset.read_bytes() + b'\n# changed\n'); self.command.reset_mock()
        self.assertEqual(self.stage.recover(self.context(self.stage), 'apply').decision, RecoveryDecision.MANUAL)
        self.command.assert_not_called(); self.assertTrue(asset.read_bytes().endswith(b'# changed\n'))

    def test_partial_enable_is_manual_even_if_the_link_was_created(self):
        self.stage.apply(self.context(self.stage)); original = self.r._write
        def write(name, value):
            if name == 'enabled.json': raise OSError('lost receipt')
            return original(name, value)
        with patch.object(self.r, '_write', side_effect=write), self.assertRaises(OSError): self.enable.apply(self.context(self.enable))
        self.command.reset_mock()
        self.assertEqual(self.enable.recover(self.context(self.enable), 'apply').decision, RecoveryDecision.MANUAL)
        with self.assertRaises(InstallerError): self.r.boot()
        self.command.assert_not_called()

    def boot_readers(self, states):
        for obj, method in ((self.r.shared, 'configuration'), (self.r.shared, 'completed'),
            (self.r.shared.boot, 'configuration'), (self.r.shared.boot, 'live'),
            (self.r.foundation, 'inspect'), (self.r.gateway, 'inspect'), (self.r.gateway, 'owned')):
            self.enterContext(patch.object(obj, method))
        self.enterContext(patch.object(self.r.shared, 'ready', return_value=True))
        self.enterContext(patch.object(self.r.layout.identity, 'account'))
        scope = SimpleNamespace(writer=lambda: nullcontext(), observe=Mock(side_effect=[{'state':x} for x in states]))
        self.enterContext(patch.object(self.r.http, '_scope', return_value=scope))
        self.r.epoch.root = self.root / 'epoch'
        return self.enterContext(patch.object(self.r, 'start'))

    def test_maintenance_blocks_both_starts_and_new_maintenance_blocks_second_start(self):
        self.enroll()
        start = self.boot_readers(['MAINTENANCE_REQUIRED'])
        with self.assertRaisesRegex(InstallerError, 'MANUAL_ACTION_REQUIRED'): self.r.boot()
        start.assert_not_called()
        scope = SimpleNamespace(writer=lambda: nullcontext(), observe=Mock(side_effect=[{'state':'SERVING'}, {'state':'MAINTENANCE_REQUIRED'}]))
        with patch.object(self.r.http, '_scope', return_value=scope), self.assertRaises(InstallerError): self.r.boot()
        self.assertEqual([c.args[0] for c in start.call_args_list], ['foundation'])


class MobileBootEpochTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.r = b.MobileBootRuntime(profile()); self.r.epoch.root = Path(temp.name) / 'epoch'
        self.epoch = {'boot_id':'a'*36, 'pid1_start':'100'}
        self.enterContext(patch.object(self.r, 'epoch_identity', return_value=self.epoch))
        self.process = self.enterContext(patch.object(self.r, 'process', return_value={'pid':'123','start':'456'}))
        self.stopped = self.enterContext(patch.object(self.r.foundation, 'stopped'))
        self.owned = self.enterContext(patch.object(self.r.foundation, 'owned'))
        self.command = self.enterContext(patch.object(b.boot.h, '_command'))

    def test_same_epoch_completed_start_is_never_replayed(self):
        self.r.start('foundation', self.epoch); before = self.r.epoch._read('foundation.json')
        self.r.start('foundation', self.epoch)
        self.command.assert_called_once_with(['/usr/bin/systemctl','--no-pager','--no-ask-password','start','--',self.r.foundation.unit])
        self.stopped.assert_called_once(); self.assertEqual(before, self.r.epoch._read('foundation.json'))

    def test_lost_successful_start_reply_observes_owned_process_without_second_start(self):
        self.command.side_effect = OSError('lost reply')
        with self.assertRaises(OSError): self.r.start('foundation', self.epoch)
        self.assertIsNone(self.r.epoch._read('foundation.json'))
        self.command.reset_mock(); self.r.start('foundation', self.epoch)
        self.command.assert_not_called(); self.owned.assert_called_once()
        self.assertIsNotNone(self.r.epoch._read('foundation.json'))

    def test_stopped_service_with_prior_intent_requires_manual_action(self):
        self.command.side_effect = OSError('uncertain effect')
        with self.assertRaises(OSError): self.r.start('foundation', self.epoch)
        self.command.reset_mock(); self.owned.side_effect = InstallerError('MANUAL_ACTION_REQUIRED')
        with self.assertRaises(InstallerError): self.r.start('foundation', self.epoch)
        self.command.assert_not_called(); self.assertIsNone(self.r.epoch._read('foundation.json'))

    def test_pid_replacement_or_foreign_epoch_never_replays_completed_start(self):
        self.r.start('foundation', self.epoch); self.command.reset_mock()
        self.process.return_value = {'pid':'123','start':'999'}
        with self.assertRaises(InstallerError): self.r.start('foundation', self.epoch)
        with self.assertRaises(InstallerError): self.r.start('foundation', {**self.epoch,'pid1_start':'999'})
        self.command.assert_not_called()


class DevMobileBootTests(unittest.TestCase):
    setUp = MobileBootEnrollmentTests.setUp
    enroll = MobileBootEnrollmentTests.enroll
    boot_readers = MobileBootEnrollmentTests.boot_readers

    def prepare(self, dev_states):
        self.enroll()
        start = self.boot_readers(['SERVING', 'SERVING'])
        self.r.dev_foundation = Mock()
        scope = SimpleNamespace(writer=lambda: nullcontext(),
            observe=Mock(side_effect=[{'state': value} for value in dev_states]))
        self.r.dev_foundation.target.activation.configuration.return_value = (scope, None)
        return start

    def test_open_dev_starts_owned_web_then_foundation_before_gateway(self):
        start = self.prepare(['SERVING', 'SERVING'])
        self.r.boot()
        self.assertEqual([call.args[0] for call in start.call_args_list],
            ['foundation', 'dev_php', 'dev_apache', 'dev_timer', 'dev_foundation', 'gateway'])
        self.r.dev_foundation.target.activation.check.assert_called_once()

    def test_closed_dev_stays_closed_and_main_still_starts(self):
        start = self.prepare(['MAINTENANCE_REQUIRED'])
        self.r.boot()
        self.assertEqual([call.args[0] for call in start.call_args_list], ['foundation', 'gateway'])
        self.r.dev_foundation.target.activation.check.assert_not_called()

    def test_dev_gate_change_before_writer_refuses_all_dev_starts(self):
        start = self.prepare(['SERVING', 'MAINTENANCE_REQUIRED'])
        with self.assertRaises(InstallerError): self.r.boot()
        self.assertEqual([call.args[0] for call in start.call_args_list], ['foundation'])
        self.r.dev_foundation.target.activation.check.assert_not_called()


class DevMobileBootEpochTests(unittest.TestCase):
    setUp = MobileBootEpochTests.setUp

    def test_dev_completed_start_never_replays_and_lost_reply_only_observes(self):
        self.r.dev_foundation = Mock(unit='hestia-dev-foundation.service')
        self.command.side_effect = OSError('lost reply')
        with self.assertRaises(OSError): self.r.start('dev_foundation', self.epoch)
        self.command.reset_mock(); self.command.side_effect = None
        self.r.start('dev_foundation', self.epoch)
        self.r.start('dev_foundation', self.epoch)
        self.command.assert_not_called()
        self.r.dev_foundation.stopped.assert_called_once()
        self.r.dev_foundation.owned.assert_called_once()
        self.assertIsNotNone(self.r.epoch._read('dev_foundation.json'))


class MobileBootPlanTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.value = profile(); self.parent_doc = {'state':'DONE','plan_sha256':self.value['parents']['web']}
        parent = SimpleNamespace(journal=StateJournal(self.root/'parent/state.json'), secrets=SecretVault())
        shared = SimpleNamespace(parent=parent, root=self.root/'shared')
        self.control = p.MobileBootPlan(shared)
        self.enterContext(patch.object(parent.journal, 'locked', return_value=nullcontext(SimpleNamespace(read=lambda:deepcopy(self.parent_doc)))))
        self.binding = self.enterContext(patch.object(self.control, 'binding', return_value=self.value))
        self.absent = self.enterContext(patch.object(b.MobileBootRuntime, 'absent'))

    def plan(self): return self.control.execute('plan', {'shared_public_sha256':'b'*64})

    def test_distinct_confirmation_and_idempotent_plan_preserve_exact_sources(self):
        with self.assertRaises(InstallerError): self.control.execute('plan', {'shared_public_sha256':'f'*64})
        self.assertFalse(self.control.root.exists()); self.absent.assert_not_called()
        result = self.plan(); before = {p:p.read_bytes() for p in self.control.root.rglob('*') if p.is_file()}
        self.absent.reset_mock(); self.assertEqual(self.plan(), result); self.absent.assert_not_called()
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        self.assertEqual(len(result['installation']['steps']), 2); self.assertFalse(result['phase6_complete'])

    def test_status_is_file_only_and_creates_nothing(self):
        self.assertIsNone(self.control.state()['installation']); self.assertFalse(self.control.root.exists())
        self.binding.assert_not_called(); self.absent.assert_not_called()

    def test_code_parent_and_payload_drift_cannot_rebase_existing_plan(self):
        self.plan(); before = self.control._read('profile.json')
        changed = deepcopy(self.value); changed['code']['installer/foreign.py'] = 'a'*64; self.binding.return_value = changed
        with self.assertRaises(InstallerError): self.control.engine(self.parent_doc)
        for action, payload in (('start', {}), ('apply', {'confirmation':'b'*64,'confirm':1}),
            ('plan', {'shared_public_sha256':'b'*64,'unit':'foreign.service'})):
            with self.assertRaises(InstallerError): self.control.execute(action,payload)
        self.assertEqual(self.control._read('profile.json'), before)


class FrozenSharedReaderTests(unittest.TestCase):
    def test_completed_parent_is_read_with_original_code_and_exact_registry(self):
        value = selected(); document = {'state':'DONE','plan':{'steps':[]},'steps':[]}
        control = SimpleNamespace(binding=lambda parent, **options:(value['preparation'], value['gateway_binding'], None),
            profile=lambda:deepcopy(value), journal=SimpleNamespace(read=lambda:document))
        engine = SimpleNamespace(report=lambda:document)
        with patch.object(frozen.native, 'engine', return_value=(engine, object())) as invoke, \
            patch.object(frozen.native, 'code_identity', side_effect=AssertionError('adoption')):
            self.assertEqual(frozen.reference(control, {}, observe=True)[0], document)
            invoke.assert_called_once_with(control.journal, value)
            document['state'] = 'FAILED'
            with self.assertRaises(InstallerError): frozen.reference(control,{})
