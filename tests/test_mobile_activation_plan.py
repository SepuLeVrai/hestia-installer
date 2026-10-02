"""Cockpit/API file contracts; native effects are mocked, never run on this host."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from installer import mobile_activation_plan as plan
from installer.model import InstallerError, canonical_bytes
from installer.service import POST_ROUTES, TransactionService
import test_gateway_service
from test_application_plan import setup_payload


class MobileActivationPlanTests(unittest.TestCase):
    def setUp(self):
        test_gateway_service.GatewayServiceTests.setUp(self)
        document = test_gateway_service.GatewayServiceTests.plan(self)
        self.assertEqual(test_gateway_service.GatewayServiceTests.execute(self, 'apply', document)['state'], 'DONE')
        self.parents['gateway_service'] = document['plan_sha256']
        self.saved[self.control.journal.path] = self.control.journal.path.read_bytes()
        self.control = self.service.mobile_activation
        self.fresh = plan.FreshProfile(self.service.application.read()['instance'])
        self.fresh.root = self.root / 'managed'; self.fresh.root.mkdir(mode=0o700)
        self.backups = self.fresh.root / 'gateway-backup'; self.backups.mkdir(mode=0o700)
        self.enterContext(patch.object(plan, 'FreshProfile', return_value=self.fresh))
        self.candidate = self.enterContext(patch.object(plan.MobileActivationPlan, 'candidate',
            return_value={'lease_id': 'a' * 32, 'resume_plan_sha256': 'b' * 64}))
        self.enterContext(patch.object(self.fresh, 'runtime', return_value=self.fresh.runtime(planning=True)))
        self.enterContext(patch.object(plan.native.v.h.HttpRuntime, '_inspect_configuration', return_value=(None, None, None, None)))
        self.enterContext(patch.object(plan.native.v.h.HttpRuntime, '_scope', return_value=None))
        self.effects = self.enterContext(patch.object(plan.native, 'execute', side_effect=self.completed))
        self.serving = self.enterContext(patch.object(plan.native, 'continue_serving', side_effect=self.completed))
        self.credentials = {k: v for k, v in setup_payload()['credentials'].items() if k in plan.CREDENTIALS}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(canonical_bytes(value)); path.chmod(0o600)

    def completed(self, *args, **kwargs):
        profile = self.control.profile(); self.native_root = self.backups / ('mobile-activation-' + profile['lease_id'])
        value = {'instance': profile['instance'], 'lease_id': profile['lease_id'],
                 'resume_plan_sha256': profile['resume_plan_sha256'], 'policy': plan.native.t.POLICY}
        self.write(self.native_root / 'plan.json', value)
        owner = {'version': 1, 'instance': profile['instance'], 'lease_id': profile['lease_id'],
                 'resume_plan_sha256': profile['resume_plan_sha256'], 'activation_plan_sha256': plan.digest(value)}
        self.write(self.native_root / 'admitted.json', {'owner': owner, 'state': 'ACTIVITY_GATE_RELEASED',
            'services_started': False, 'current_sql_admission': False, 'automatic_start_retry_allowed': False})
        for role in plan.ROLES:
            intent = {'owner': owner, 'role': role}
            self.write(self.native_root / (role + '.intent.json'), intent)
            self.write(self.native_root / (role + '.started.json'), {'owner': owner, 'role': role, 'intent_sha256': plan.digest(intent)})
        self.write(self.native_root / 'done.json', {'owner': owner, 'state': 'MOBILE_SERVICES_RUNNING', 'services_started': True})
        return {'local_web': {'login_page': True}}

    def prepare(self): return self.service.execute('mobile-activation.plan', {'parents': self.parents})['mobile_activation']
    def request(self, action='apply', **extra):
        payload = {'confirmation': self.control.state()['confirmation'], 'confirm': True}
        if action != 'check': payload.update(credentials=deepcopy(self.credentials), allow_global_read_lock=True)
        payload.update(extra)
        return self.service.execute('mobile-activation.' + action, payload)['mobile_activation']

    def test_plan_is_separate_repeatable_and_read_only_for_all_parents(self):
        first = self.prepare(); self.assertEqual(self.prepare(), first)
        self.effects.assert_not_called(); self.serving.assert_not_called()
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)

    def test_confirmation_unknown_keys_and_no_sql_consent_precede_effects(self):
        self.prepare()
        for extra in ({'confirmation': '0' * 64}, {'confirm': 1}, {'unit': 'apache2.service'}, {'allow_global_read_lock': False}):
            with self.assertRaises(InstallerError): self.request(**extra)
        self.effects.assert_not_called(); self.assertIsNone(self.control._read('approved.json'))

    def test_empty_partial_and_invalid_credentials_never_create_approval(self):
        self.prepare()
        for credentials in ({}, {'database_password': 'x'}, {**self.credentials, 'authority_user': 'root'},
                            {**self.credentials, 'authority_password': 'bad\nsecret'}):
            with self.assertRaises(InstallerError): self.request(credentials=credentials)
        self.assertIsNone(self.control._read('approved.json')); self.effects.assert_not_called()

    def test_approval_precedes_native_effect_and_paths_are_server_owned(self):
        self.prepare()
        def effect(*args, **kwargs):
            self.assertEqual(self.control._read('approved.json'), {'confirmation': self.control.state()['confirmation']})
            self.assertEqual(args[3], self.backups)
            self.assertEqual(args[6]['assistant'], {'action': 'preserve'})
            self.assertEqual(args[6]['secrets']['database_password'], self.credentials['database_password'])
            self.assertEqual(kwargs['action'], 'apply')
            return self.completed()
        self.effects.side_effect = effect
        self.assertEqual(self.request()['state'], 'DONE')
        self.assertFalse(self.control.state()['phase6_complete'])

    def test_progress_is_historical_and_get_has_no_native_observation(self):
        self.prepare(); self.request(); before = self.control.state()
        self.effects.reset_mock(); self.serving.reset_mock()
        with patch.object(plan.native.v, 'NativeRuntime', side_effect=AssertionError('native GET')):
            self.assertEqual(self.service.wizard_state()['mobile_activation'], before)
            self.assertEqual(self.service.report()['mobile_activation'], before)
        self.assertTrue(before['historical_only']); self.effects.assert_not_called(); self.serving.assert_not_called()

    def test_restart_drops_availability_and_never_replays(self):
        self.prepare(); self.request()
        control = plan.MobileActivationPlan(self.service.application, self.service.gateway_service)
        self.assertEqual(control.state()['state'], 'DONE'); self.assertIsNone(control.state()['availability'])
        self.assertEqual(self.effects.call_count, 1)

    def test_failure_before_native_plan_requires_explicit_resume_and_fresh_admission(self):
        self.prepare(); self.effects.side_effect = RuntimeError('private-sql-password')
        with self.assertRaisesRegex(InstallerError, '^MANUAL_ACTION_REQUIRED$'): self.request()
        self.assertEqual(self.control.state()['state'], 'RESUME_REQUIRED')
        with self.assertRaises(InstallerError): self.request()
        self.effects.side_effect = self.completed; self.request('resume')
        self.assertEqual(self.effects.call_args.kwargs['action'], 'apply')

    def test_existing_native_plan_uses_resume_never_repeated_apply(self):
        self.prepare(); self.request(); self.request('resume')
        self.assertEqual(self.effects.call_args.kwargs['action'], 'resume')

    def test_admitted_resume_and_check_need_no_sql_credentials(self):
        self.prepare(); self.request(); self.effects.reset_mock()
        self.request('resume', credentials={}, allow_global_read_lock=False); self.request('check')
        self.assertEqual([x.kwargs['action'] for x in self.serving.call_args_list], ['resume', 'check'])
        self.effects.assert_not_called()

    def test_closed_credentialless_resume_returns_only_secret_required(self):
        self.prepare(); self.request(); self.serving.side_effect = plan.native.ActivationError('MOBILE_ACTIVATION_MAINTENANCE_REQUIRED')
        with self.assertRaisesRegex(InstallerError, '^SECRET_REQUIRED$'):
            self.request('resume', credentials={}, allow_global_read_lock=False)

    def test_check_failure_preserves_done_and_reports_unavailable(self):
        self.prepare(); self.request(); self.serving.side_effect = RuntimeError('private-value')
        before = {p: p.read_bytes() for p in self.native_root.iterdir()}
        state = self.request('check')
        self.assertEqual(state['state'], 'DONE'); self.assertEqual(state['availability']['state'], 'LOCAL_SERVICES_UNAVAILABLE')
        self.assertEqual(before, {p: p.read_bytes() for p in self.native_root.iterdir()})

    def test_changed_parent_refuses_native_recovery(self):
        self.prepare(); self.request()
        original = self.control.binding(self.service.engine.report()); original['draft_sha256'] = 'c' * 64
        with patch.object(self.control, 'binding', return_value=original):
            with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.request('resume')
        self.assertEqual(self.effects.call_count, 1)

    def test_private_state_and_report_do_not_store_credentials_or_raw_error(self):
        self.prepare(); self.request()
        public = str(self.service.report())
        raw = b''.join(p.read_bytes() for p in self.control.root.rglob('*.json'))
        for secret in self.credentials.values():
            self.assertNotIn(secret, public); self.assertNotIn(secret.encode(), raw)
        for path in self.control.root.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupted_or_linked_history_fails_closed_without_native_call(self):
        self.prepare(); self.request()
        path = self.native_root / 'php.started.json'; path.write_bytes(b'{')
        self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        path.unlink(); path.symlink_to(self.control.root / 'profile.json')
        self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        self.assertEqual(self.effects.call_count, 1)

    def test_foreign_receipt_and_out_of_order_progress_are_rejected(self):
        self.prepare(); self.request(); path = self.native_root / 'php.started.json'
        self.write(path, {'owner': {}, 'role': 'php', 'intent_sha256': '0' * 64})
        self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        path.unlink(); self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')

    def test_all_posts_are_closed_and_no_targeted_retry_route_exists(self):
        self.assertEqual({path for path in POST_ROUTES if path.startswith('/api/mobile/')},
                         {'/api/mobile/activation/' + x for x in ('plan', 'apply', 'resume', 'check')})
        with self.assertRaises(InstallerError): self.service.execute('mobile-activation.retry', {})

    def test_another_service_cannot_mutate_while_parent_lock_held(self):
        self.prepare()
        with self.service.engine.journal.locked(create=False):
            with self.assertRaisesRegex(InstallerError, 'BUSY'): self.request()
        self.effects.assert_not_called()

    def test_get_progress_remains_available_during_long_mutation(self):
        self.prepare(); entered = threading.Event(); release = threading.Event(); failures = []
        def effect(*args, **kwargs):
            entered.set(); release.wait(5); return self.completed()
        def run():
            try: self.request()
            except Exception as error: failures.append(type(error).__name__)
        self.effects.side_effect = effect; thread = threading.Thread(target=run); thread.start()
        try:
            self.assertTrue(entered.wait(3)); state = self.service.wizard_state()
            self.assertTrue(state['busy']); self.assertEqual(state['mobile_activation']['state'], 'RESUME_REQUIRED')
        finally: release.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertEqual(failures, [])

    def test_missing_or_ambiguous_candidates_refuse_without_writing_profile(self):
        binding = self.control.binding(self.service.engine.report())
        # Call the original reader, saved independently of the facade patch.
        for name in ('mobile-resume-' + 'c' * 32, 'mobile-resume-' + 'd' * 32):
            (self.backups / name).mkdir(mode=0o700)
        with self.assertRaises(InstallerError): ORIGINAL_CANDIDATE(self.control, binding)
        self.assertFalse(self.control.root.exists())


ORIGINAL_CANDIDATE = plan.MobileActivationPlan.candidate


class ServingEntryTests(unittest.TestCase):
    def serving_fixture(self, state):
        fresh = plan.FreshProfile('a' * 32); http = fresh.http({'web': fresh.web('hestia.example.test')})
        raw = b'{}'; runtime = Mock(); runtime.scope.observe.return_value = {'state': state}
        runtime.activation.check.return_value = {'login_page': True}
        self.enterContext(patch.object(plan.native.e, '_read_path', return_value=raw))
        self.enterContext(patch.object(plan.native.v, 'NativeRuntime', return_value=runtime))
        self.enterContext(patch.object(plan.native.a.c, '_recheck', side_effect=AssertionError('SQL export')))
        return http, Path('/var/lib/fixture-backups'), 'a' * 32, plan.native.f._sha(raw)

    def test_closed_maintenance_cannot_enter_credentialless_path(self):
        args = self.serving_fixture('MAINTENANCE_REQUIRED')
        with patch.object(plan.native.t.ActivationRecord, 'load', side_effect=AssertionError('record')):
            with self.assertRaisesRegex(plan.native.ActivationError, 'MAINTENANCE_REQUIRED'):
                plan.native.continue_serving(*args, action='resume', confirmed=True)

    def test_completed_credentialless_check_uses_native_lock_and_read_only_mode(self):
        args = self.serving_fixture('SERVING')
        from unittest.mock import MagicMock
        record = MagicMock(); record.start_services.return_value = {}
        with patch.object(plan.native.t.ActivationRecord, 'load', return_value=record):
            result = plan.native.continue_serving(*args, action='check', confirmed=True)
        record.serving_lock.assert_called_once_with()
        record.start_services.assert_called_once_with(record.serving_lock.return_value.__enter__.return_value, check_only=True)
        self.assertTrue(result['local_web']['login_page']); self.assertFalse(result['phase6_complete'])

    def test_consent_and_fixed_types_precede_native_construction(self):
        with patch.object(plan.native.v, 'NativeRuntime', side_effect=AssertionError('native')):
            for confirmed in (False, 1):
                with self.assertRaises(plan.native.ActivationError):
                    plan.native.continue_serving(None, None, None, None, action='resume', confirmed=confirmed)
            with self.assertRaises(plan.native.ActivationError):
                plan.native.continue_serving(None, Path('/var/lib/unused'), 'a' * 32, 'b' * 64, action='apply', confirmed=True)
