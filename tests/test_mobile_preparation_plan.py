"""Durable orchestration contracts; real system effects are isolated in CI."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading
import unittest
from pathlib import Path

from installer import mobile_preparation_plan as plan, mobile_preparation_runtime as native
from installer.model import InstallerError
from installer.service import POST_ROUTES
import test_mobile_backup_plan as fixture


class MobilePreparationPlanTests(unittest.TestCase):
    def setUp(self):
        fixture.MobileBackupPlanTests.setUp(self)
        self.operation.create_and_verify.side_effect = lambda *a, **k: fixture.MobileBackupPlanTests.completed(self)
        fixture.MobileBackupPlanTests.prepare(self); fixture.MobileBackupPlanTests.request(self)
        self.backup_control = self.control; self.control = self.service.mobile_preparation
        self.enterContext(patch.object(plan, 'FreshProfile', return_value=self.fresh)).from_draft.return_value = self.fresh
        self.native = Mock(); self.native.execute.side_effect = self.prepared_stage
        self.factory = self.enterContext(patch.object(plan, 'NativePreparation', return_value=self.native))

    completed = fixture.MobileBackupPlanTests.completed
    write = staticmethod(fixture.MobileBackupPlanTests.write)
    def prepared_stage(self, stage):
        return {'stage': stage, 'instance': self.fresh.instance, 'lease_id': 'a' * 32,
                'result_sha256': plan.digest(stage), 'services_started': False, 'activity_resumed': False}
    def prepare(self): return self.service.execute('mobile-preparation.plan', {'parents': self.parents})['mobile_preparation']
    def request(self, action='apply', **extra):
        payload = {'confirmation': self.control.state()['confirmation'], 'confirm': True,
                   'credentials': deepcopy(self.credentials), 'allow_global_read_lock': True, **extra}
        return self.service.execute('mobile-preparation.' + action, payload)['mobile_preparation']

    def test_plan_reads_are_repeatable_without_native_observation(self):
        self.scope.observe.reset_mock(); first = self.prepare()
        self.assertEqual(self.prepare(), first); self.assertEqual(self.service.report()['mobile_preparation'], first)
        self.assertEqual(self.service.wizard_state()['mobile_preparation'], first)
        self.factory.assert_not_called(); self.scope.observe.assert_not_called()
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)

    def test_unverified_backup_refuses_plan(self):
        with patch.object(self.backup_control, 'receipt', return_value=None):
            with self.assertRaisesRegex(InstallerError, 'DEPENDENCY_BLOCKED'): self.prepare()
        self.assertFalse(self.control.root.exists())

    def test_consent_and_all_credentials_precede_any_intent(self):
        self.prepare()
        for extra in ({'confirm': 1}, {'confirmation': '0' * 64}, {'allow_global_read_lock': False},
                      {'credentials': {}}, {'credentials': {**self.credentials, 'authority_user': 'root'}},
                      {'stage': 'blockers'}, {'lease_id': 'f' * 32}):
            with self.assertRaises(InstallerError): self.request(**extra)
        self.assertIsNone(self.control._read('approved.json')); self.factory.assert_not_called()

    def test_six_stages_run_in_order_under_prior_durable_intent(self):
        self.prepare()
        def execute(stage):
            self.assertIsNotNone(self.control._read(stage + '.intent.json'))
            self.assertIsNone(self.control._read(stage + '.done.json'))
            return self.prepared_stage(stage)
        self.native.execute.side_effect = execute
        result = self.request(); self.assertEqual(result['state'], 'DONE'); self.assertTrue(result['activation_ready'])
        self.assertEqual([x.args[0] for x in self.native.execute.call_args_list], list(plan.STAGES))
        self.assertFalse(result['services_started']); self.assertFalse(result['activity_resumed'])
        self.effects.assert_not_called(); self.serving.assert_not_called()

    def test_failure_preserves_completed_stages_and_resumes_only_unfinished(self):
        self.prepare()
        def execute(stage):
            if stage == 'external': raise RuntimeError('private-error')
            return self.prepared_stage(stage)
        self.native.execute.side_effect = execute
        with self.assertRaisesRegex(InstallerError, '^MANUAL_ACTION_REQUIRED$'): self.request()
        self.assertEqual([x['state'] for x in self.control.state()['steps']], ['DONE', 'DONE', 'INTENT_RECORDED', 'PENDING', 'PENDING', 'PENDING'])
        before = {p: p.read_bytes() for p in self.control.root.glob('*.done.json')}
        with self.assertRaises(InstallerError): self.request()
        self.native.execute.reset_mock(); self.native.execute.side_effect = self.prepared_stage
        self.assertEqual(self.request('resume')['state'], 'DONE')
        self.assertEqual([x.args[0] for x in self.native.execute.call_args_list], list(plan.STAGES[2:]))
        for p, raw in before.items(): self.assertEqual(p.read_bytes(), raw)

    def test_lost_stage_response_is_reconciled_by_native_stage_again(self):
        self.prepare(); self.native.execute.side_effect = RuntimeError('response lost after real effect')
        with self.assertRaises(InstallerError): self.request()
        self.assertEqual(self.control.state()['steps'][0]['state'], 'INTENT_RECORDED')
        self.native.execute.side_effect = self.prepared_stage
        self.assertEqual(self.request('resume')['state'], 'DONE')
        self.assertEqual([x.args[0] for x in self.native.execute.call_args_list].count('gateway'), 2)

    def test_complete_response_loss_never_reexecutes_or_needs_sql(self):
        self.prepare(); self.request(); self.native.execute.reset_mock(); self.scope.observe.reset_mock()
        self.assertEqual(self.request('resume', credentials={})['state'], 'DONE')
        self.native.execute.assert_not_called(); self.scope.observe.assert_not_called()

    def test_gate_change_refuses_before_approval(self):
        self.prepare()
        for value in ({'state': 'SERVING'}, {'state': 'MAINTENANCE_REQUIRED', 'instance': self.fresh.instance, 'lease_id': 'f' * 32}):
            self.scope.observe.return_value = value
            with self.assertRaises(InstallerError): self.request()
        self.factory.assert_not_called(); self.assertIsNone(self.control._read('approved.json'))

    def test_parent_or_backup_drift_refuses_even_completed_recovery(self):
        self.prepare(); self.request(); binding = self.control.binding(self.service.engine.report())
        for key in ('draft_sha256', 'backup_profile_sha256', 'backup_receipt_sha256'):
            with patch.object(self.control, 'binding', return_value={**binding, key: 'f' * 64}):
                with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.request('resume')
        self.assertEqual(self.native.execute.call_count, 6)

    def test_foreign_or_reordered_receipts_fail_closed(self):
        self.prepare(); self.request()
        path = self.control.root / 'files.intent.json'; record = self.control._read(path.name)
        self.write(path, {**record, 'previous_sha256': 'f' * 64})
        self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        with self.assertRaises(InstallerError): self.request('resume')
        self.assertEqual(self.native.execute.call_count, 6)

    def test_missing_stage_receipt_cannot_skip_to_later_stage(self):
        self.prepare(); self.request(); (self.control.root / 'files.done.json').unlink()
        self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        with self.assertRaises(InstallerError): self.request('resume')
        self.assertEqual(self.native.execute.call_count, 6)

    def test_ready_requires_exact_activation_candidate(self):
        self.prepare()
        self.candidate.return_value = {'lease_id': 'f' * 32, 'resume_plan_sha256': 'b' * 64}
        with self.assertRaises(InstallerError): self.request()
        self.assertEqual(self.control.state()['state'], 'UNAVAILABLE'); self.assertFalse(self.control.state()['activation_ready'])
        self.effects.assert_not_called()

    def test_restart_keeps_only_history_and_no_secrets(self):
        self.prepare(); self.request(); expected = self.control.state()
        self.assertEqual(plan.MobilePreparationPlan(self.backup_control).state(), expected)
        raw = b''.join(p.read_bytes() for p in self.control.root.iterdir()); public = str(self.service.report())
        for secret in self.credentials.values(): self.assertNotIn(secret.encode(), raw); self.assertNotIn(secret, public)
        for p in self.control.root.iterdir(): self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_raw_native_error_is_redacted(self):
        self.prepare(); self.native.execute.side_effect = RuntimeError('private-database-password')
        with self.assertRaisesRegex(InstallerError, '^MANUAL_ACTION_REQUIRED$'): self.request()
        self.assertNotIn('private-database-password', str(self.service.report()))

    def test_concurrent_parent_mutation_is_refused(self):
        self.prepare()
        with self.service.engine.journal.locked(create=False):
            with self.assertRaisesRegex(InstallerError, 'BUSY'): self.request()
        self.factory.assert_not_called()

    def test_progress_reads_remain_available_during_native_stage(self):
        self.prepare(); entered = threading.Event(); release = threading.Event(); errors = []
        def execute(stage): entered.set(); release.wait(5); return self.prepared_stage(stage)
        def action():
            try: self.request()
            except Exception as error: errors.append(type(error).__name__)
        self.native.execute.side_effect = execute; worker = threading.Thread(target=action); worker.start()
        try:
            self.assertTrue(entered.wait(3)); value = self.service.wizard_state()
            self.assertTrue(value['busy']); self.assertEqual(value['mobile_preparation']['steps'][0]['state'], 'INTENT_RECORDED')
        finally: release.set(); worker.join(5)
        self.assertEqual(errors, []); self.assertFalse(worker.is_alive())

    def test_routes_refuse_arbitrary_retry_and_native_paths(self):
        self.assertEqual({p for p in POST_ROUTES if p.startswith('/api/mobile/preparation/')},
                         {'/api/mobile/preparation/' + x for x in ('plan', 'apply', 'resume')})
        for action in ('retry', 'check', 'activate'):
            with self.assertRaises(InstallerError): self.service.execute('mobile-preparation.' + action, {})


class NativePreparationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.control = object.__new__(native.NativePreparation)
        self.control.scope = SimpleNamespace(instance='a' * 32)
        self.control.lease_id = 'b' * 32

    def test_all_native_operations_finish_before_historical_result(self):
        names = ('_gateway_release', '_file_release', '_external_release', '_data_release', '_resume_plan', '_blocker_handoff')
        for stage, method in zip(native.STAGES, names):
            with self.subTest(stage=stage), patch.object(self.control, method, return_value={
                    'activity_resumed': False, 'services_started': False, 'maintenance_released': False}) as call:
                result = self.control.execute(stage); call.assert_called_once()
                self.assertEqual(result['stage'], stage); self.assertFalse(result['activity_resumed'])

    def test_unexpected_activation_result_never_yields_completion(self):
        with patch.object(self.control, '_gateway_release', return_value={'activity_resumed': True, 'services_started': False}):
            with self.assertRaises(InstallerError): self.control.execute('gateway')

    def test_resume_requires_explicit_maintenance_closed_result(self):
        with patch.object(self.control, '_resume_plan', return_value={'maintenance_released': True, 'services_started': False}):
            with self.assertRaises(InstallerError): self.control.execute('resume')

    def test_constructor_refuses_fabricated_lease_context(self):
        with self.assertRaises(InstallerError): native.NativePreparation(None, None, 'a' * 32, Path('/var/lib/a'), None, Path('/var/lib/b'), {}, None)

    def test_unknown_stage_cannot_be_an_operation(self):
        with self.assertRaises(InstallerError): self.control.execute('restart')
