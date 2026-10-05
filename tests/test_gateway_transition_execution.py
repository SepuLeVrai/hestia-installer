"""Cockpit transaction contracts; native qualification is a separate recipe."""
from copy import deepcopy
import io
import threading
import unittest
from unittest.mock import Mock, patch

from installer import gateway_transition_execution as plan
from installer.gateway_plan import BinaryImport
from installer.model import InstallerError
from installer.service import POST_ROUTES, TRANSITION_PACKAGE_ROUTE
import test_mobile_backup_plan as fixture
import test_gateway_http


class GatewayTransitionExecutionTests(unittest.TestCase):
    def setUp(self):
        fixture.MobileBackupPlanTests.setUp(self)
        self.operation.create_and_verify.side_effect = lambda *a, **k: fixture.MobileBackupPlanTests.completed(self)
        fixture.MobileBackupPlanTests.prepare(self); fixture.MobileBackupPlanTests.request(self)
        self.backup_control = self.control; self.control = self.service.gateway_transition_execution
        self.selection = {'version': 1, 'source_plan_sha256': self.parents['gateway_service'],
            'source_profile_sha256': 'b' * 64, 'assessment': {'configuration_compatible': True, 'source': {}, 'blockers': [],
            'target_release': self.responses.selected, 'direction': 'upgrade'}}
        self.service.gateway_transition._write('profile.json', self.selection)
        self.current = self.enterContext(patch.object(self.service.gateway_transition, 'current', return_value=self.selection))
        self.native = Mock(); self.native.execute.side_effect = lambda name: {'stage': name, 'native': 'fixture'}
        self.native.check.return_value = {'state': 'MOBILE_SERVICES_RUNNING_LOCAL_WEB_AVAILABLE'}
        self.factory = self.enterContext(patch.object(plan, 'NativeTransition', return_value=self.native))

    completed = fixture.MobileBackupPlanTests.completed
    write = staticmethod(fixture.MobileBackupPlanTests.write)

    def prepare(self):
        return self.service.execute('gateway-transition-execution.plan', {'transition_sha256': plan.digest(self.selection)})['gateway_transition_execution']

    def upload(self, raw=None):
        raw = self.responses.package if raw is None else raw
        return self.service.import_transition_package(self.control.state()['confirmation'], io.BytesIO(raw),
            self.responses.selected['package_bytes'])['gateway_transition_execution']

    def request(self, action='apply', **extra):
        payload = {'confirmation': self.control.state()['confirmation'], 'confirm': True}
        if action != 'check': payload.update(credentials=deepcopy(self.credentials), allow_global_read_lock=True)
        return self.service.execute('gateway-transition-execution.' + action, {**payload, **extra})['gateway_transition_execution']

    def ready(self): self.prepare(); self.assertEqual(self.upload()['acquisition']['state'], 'DONE')

    def test_plan_and_get_are_file_only_and_keep_all_parents(self):
        first = self.prepare(); self.assertEqual(self.prepare(), first)
        self.scope.observe.reset_mock()
        with patch('installer.gateway_plan.verify_package', side_effect=AssertionError('GET package audit')):
            self.assertEqual(self.service.wizard_state()['gateway_transition_execution'], first)
            self.assertEqual(self.service.report()['gateway_transition_execution'], first)
        self.factory.assert_not_called(); self.scope.observe.assert_not_called()
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)

    def test_missing_backup_or_incompatible_selection_never_creates_execution(self):
        with patch.object(self.backup_control, 'receipt', return_value=None):
            with self.assertRaises(InstallerError): self.prepare()
        self.selection['assessment']['configuration_compatible'] = False
        with self.assertRaises(InstallerError): self.prepare()
        self.assertFalse(self.control.root.exists())

    def test_fcm_and_dev_profiles_are_refused_before_execution_plan(self):
        for key in ('push', 'dev'):
            selection = deepcopy(self.selection); selection['assessment']['source'][key] = {}
            self.write(self.service.gateway_transition.root / 'profile.json', selection); self.current.return_value = selection
            with self.assertRaises(InstallerError): self.service.execute('gateway-transition-execution.plan', {'transition_sha256': plan.digest(selection)})
        self.assertFalse(self.control.root.exists())

    def test_all_consents_credentials_and_package_precede_native_approval(self):
        self.prepare()
        with self.assertRaises(InstallerError): self.request()
        self.upload()
        for extra in ({'confirm': 1}, {'confirmation': '0' * 64}, {'allow_global_read_lock': False}, {'credentials': {}},
                      {'credentials': {**self.credentials, 'authority_user': 'root'}}, {'target_package': '/foreign'}, {'stage': 'activation'}):
            with self.assertRaises(InstallerError): self.request(**extra)
        self.factory.assert_not_called(); self.assertIsNone(self.control._read('approved.json'))

    def test_public_boot_or_native_drift_refuses_before_durable_approval(self):
        self.ready(); self.native.preflight.side_effect = RuntimeError('preflight refused')
        with self.assertRaisesRegex(InstallerError, '^MANUAL_ACTION_REQUIRED$'): self.request()
        self.assertIsNone(self.control._read('approved.json')); self.native.execute.assert_not_called()

    def test_stream_import_has_independent_durable_consent_and_no_native_effect(self):
        self.prepare(); control = self.control; owner = self
        class Stream(io.BytesIO):
            def read1(self, size):
                value = control.journal.read()
                owner.assertEqual(value['approved_plan_sha256'], value['plan_sha256'])
                owner.assertEqual(value['steps'][0]['state'], 'RUNNING')
                owner.assertIsNone(control._read('approved.json'))
                return super().read1(size)
        self.service.import_transition_package(self.control.state()['confirmation'], Stream(self.responses.package), len(self.responses.package))
        self.assertEqual(self.control.state()['acquisition']['state'], 'DONE'); self.factory.assert_not_called()

    def test_bad_upload_consent_or_length_never_consumes_body(self):
        self.prepare(); before = self.control.journal.path.read_bytes(); stream = Mock()
        for confirmation, length in [('0' * 64, len(self.responses.package)), (self.control.state()['confirmation'], 1)]:
            with self.assertRaises(InstallerError): self.service.import_transition_package(confirmation, stream, length)
        stream.read.assert_not_called(); stream.read1.assert_not_called()
        self.assertEqual(before, self.control.journal.path.read_bytes()); self.assertFalse((self.control.root / 'binary').exists())

    def test_corrupt_upload_can_resume_but_committed_damage_is_never_repaired(self):
        self.prepare(); self.assertEqual(self.upload(b'bad')['acquisition']['state'], 'FAILED')
        self.assertEqual(self.upload()['acquisition']['state'], 'DONE')
        package = self.control.root / 'binary/package.zip'; package.write_bytes(b'damaged')
        with self.assertRaises(InstallerError): self.upload()
        with self.assertRaises(InstallerError): self.request()
        self.assertEqual(package.read_bytes(), b'damaged'); self.factory.assert_not_called()

    def test_lost_import_commit_recovers_without_reading_new_body(self):
        self.prepare()
        with patch.object(BinaryImport, 'commit', side_effect=OSError('lost reply')): self.upload()
        stream = Mock(); stream.read.side_effect = AssertionError('duplicate upload read')
        result = self.service.import_transition_package(self.control.state()['confirmation'], stream, len(self.responses.package))
        self.assertEqual(result['gateway_transition_execution']['acquisition']['state'], 'DONE'); stream.read.assert_not_called()

    def test_five_stages_follow_durable_hash_linked_intents_and_finish_with_check(self):
        self.ready()
        def execute(name):
            self.assertIsNotNone(self.control._read(name + '.intent.json'))
            self.assertIsNone(self.control._read(name + '.done.json'))
            return {'native_stage': name}
        self.native.execute.side_effect = execute
        value = self.request(); self.assertEqual(value['state'], 'DONE')
        self.assertEqual([c.args[0] for c in self.native.execute.call_args_list], list(plan.STAGES))
        self.native.check.assert_called_once(); self.assertFalse(value['phase6_complete'])
        self.assertFalse(value['boot_persistence']); self.assertFalse(value['public_tls_verified'])

    def test_every_effect_response_loss_resumes_only_at_uncommitted_stage(self):
        self.ready()
        for index, name in enumerate(plan.STAGES):
            self.native.execute.side_effect = RuntimeError('lost native response')
            with self.assertRaises(InstallerError): self.request('apply' if index == 0 else 'resume')
            self.assertEqual(self.control.state()['steps'][index]['state'], 'INTENT_RECORDED')
            calls = []
            def execute(stage):
                calls.append(stage)
                if stage != name: raise RuntimeError('next boundary')
                return {'stage': stage}
            self.native.execute.side_effect = execute
            if index == len(plan.STAGES) - 1: self.assertEqual(self.request('resume')['state'], 'DONE')
            else:
                with self.assertRaises(InstallerError): self.request('resume')
            self.assertEqual(calls[0], name)
            self.assertTrue(all(x['state'] == 'DONE' for x in self.control.state()['steps'][:index + 1]))

    def test_complete_lost_response_needs_no_sql_or_native_replay(self):
        self.ready(); self.request(); self.factory.reset_mock()
        self.assertEqual(self.request('resume', credentials={}, allow_global_read_lock=False)['state'], 'DONE')
        self.factory.assert_not_called()

    def test_check_is_explicit_read_only_and_needs_no_credentials(self):
        self.ready(); self.request(); self.native.reset_mock()
        result = self.request('check'); self.assertIsNotNone(result['availability'])
        self.native.check.assert_called_once(); self.native.execute.assert_not_called(); self.native.preflight.assert_not_called()
        with self.assertRaises(InstallerError): self.request('check', credentials={})

    def test_check_failure_clears_previous_availability_without_replay(self):
        self.ready(); self.request(); self.native.check.side_effect = RuntimeError('secret-error')
        with self.assertRaisesRegex(InstallerError, '^MANUAL_ACTION_REQUIRED$'): self.request('check')
        self.assertIsNone(self.control.state()['availability']); self.assertEqual(self.control.state()['state'], 'DONE')
        self.assertNotIn('secret-error', str(self.service.report()))

    def test_foreign_reordered_or_missing_receipts_cannot_skip_stages(self):
        self.ready(); self.request(); path = self.control.root / 'cutover.done.json'; path.unlink()
        self.factory.reset_mock(); self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        with self.assertRaises(InstallerError): self.request('resume')
        self.factory.assert_not_called()

    def test_changed_parent_backup_or_selection_refuses_even_completed_resume(self):
        self.ready(); self.request(); binding = self.control.binding(self.service.engine.report())
        for key in ('draft_sha256', 'backup_profile_sha256', 'backup_receipt_sha256', 'transition_sha256'):
            with patch.object(self.control, 'binding', return_value={**binding, key: 'f' * 64}):
                with self.assertRaises(InstallerError): self.request('resume')
        self.assertEqual(self.native.execute.call_count, 5)

    def test_process_restart_retains_history_but_no_live_availability_or_secrets(self):
        self.ready(); self.request()
        restarted = plan.GatewayTransitionExecution(self.service.gateway_transition, self.backup_control).state()
        self.assertEqual(restarted['state'], 'DONE'); self.assertIsNone(restarted['availability'])
        raw = b''.join(p.read_bytes() for p in self.control.root.rglob('*') if p.is_file())
        for secret in self.credentials.values(): self.assertNotIn(secret.encode(), raw); self.assertNotIn(secret, str(self.service.report()))
        for p in self.control.root.rglob('*'):
            if p.is_file(): self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_read_progress_stays_available_and_concurrent_mutation_refuses(self):
        self.ready(); entered = threading.Event(); release = threading.Event(); errors = []
        def execute(stage): entered.set(); release.wait(5); return {'stage': stage}
        self.native.execute.side_effect = execute
        def run():
            try: self.request()
            except Exception as error: errors.append(type(error).__name__)
        thread = threading.Thread(target=run); thread.start()
        try:
            self.assertTrue(entered.wait(3)); value = self.service.wizard_state()
            self.assertTrue(value['busy']); self.assertEqual(value['gateway_transition_execution']['steps'][0]['state'], 'INTENT_RECORDED')
            with self.assertRaisesRegex(InstallerError, 'BUSY'): self.request('resume')
        finally: release.set(); thread.join(5)
        self.assertEqual(errors, []); self.assertFalse(thread.is_alive())


class GatewayTransitionExecutionHTTPTests(unittest.TestCase):
    setUp = test_gateway_http.GatewayHTTPTests.setUp
    tearDown = test_gateway_http.GatewayHTTPTests.tearDown
    _connection = test_gateway_http.GatewayHTTPTests._connection
    _unlock = test_gateway_http.GatewayHTTPTests._unlock
    login = test_gateway_http.GatewayHTTPTests.login
    request = test_gateway_http.GatewayHTTPTests.request

    def test_every_route_requires_session_csrf_and_same_origin(self):
        routes = [p for p in POST_ROUTES if p.startswith('/api/gateway/transition/execution/')]
        self.assertEqual(len(routes), 4); routes.append(TRANSITION_PACKAGE_ROUTE)
        for route in routes: self.assertEqual(self.request('POST', route, {})[0], 401)
        self.login()
        for route in routes:
            for headers in ({'X-Hestia-CSRF': ''}, {'Origin': 'https://evil.invalid'}):
                self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
        self.assertFalse(self.service.gateway_transition_execution.root.exists())

    def test_zip_route_streams_only_to_transition_import_and_rejects_bad_headers(self):
        self.login(); headers = {'Content-Type': 'application/zip', 'X-Hestia-Plan': 'a' * 64}
        with patch.object(self.service, 'import_transition_package', return_value={'gateway_transition_execution': {'fixture': True}}) as call:
            for change, expected in (({'Content-Type': 'application/json'}, 400), ({'Content-Encoding': 'gzip'}, 400),
                                     ({'X-Hestia-Plan': 'bad'}, 400), ({'Content-Length': '0'}, 413)):
                self.assertEqual(self.request('POST', TRANSITION_PACKAGE_ROUTE, headers={**headers, **change}, raw=b'zip')[0], expected)
            call.assert_not_called()
            status, value, response = self.request('POST', TRANSITION_PACKAGE_ROUTE, headers=headers, raw=b'zip')
            self.assertEqual(status, 200, value); self.assertEqual(response['Connection'], 'close')
            self.assertEqual(call.call_args.args[0], 'a' * 64); self.assertEqual(call.call_args.args[2], 3)


class NativeTransitionTests(unittest.TestCase):
    def test_constructor_preserves_exact_http_identity_without_full_native_audit(self):
        from types import SimpleNamespace
        from installer import gateway_transition_native as native
        from installer.gateway_service_runtime import GatewayServiceRuntime
        from installer.gateway_transition import assess, FCM_COMMIT
        from test_gateway_transition import GatewayTransitionTests
        source = GatewayTransitionTests().profile()
        runtime = GatewayServiceRuntime.from_binding(source.foundation, source.binding())
        fresh = native.FreshProfile(runtime.web.spec.instance)
        draft = {'configuration': {'database': {'mode': 'existing_local'}}}
        controller = Mock()
        controller.backup.application.read.return_value = draft
        controller.transition.service.engine.return_value = (None, runtime)
        controller.transition.profile.return_value = {'assessment': assess(source, target_commit=FCM_COMMIT, direction='upgrade').report()}
        controller.root = runtime.root.parent / 'execution'
        controller.parent.journal.path = runtime.root.parent / 'installer/state.json'
        controller.transition.service.gateway.root = runtime.root.parent / 'acquisition'
        profile = {'instance': runtime.web.spec.instance, 'lease_id': 'c' * 32}
        with patch.object(native.FreshProfile, 'from_draft', return_value=fresh), \
             patch.object(fresh, 'http', return_value=runtime.web), \
             patch.object(fresh, 'runtime', return_value=fresh.runtime(planning=True)), \
             patch.object(native.cutover.hd.h, '_identity', return_value=SimpleNamespace(pw_uid=901, pw_gid=902)), \
             patch.object(runtime.web, '_scope', return_value=Mock()), \
             patch.object(runtime.web, '_inspect_configuration', side_effect=AssertionError('audit before data reclosure')):
            actual = native.NativeTransition(controller, {}, profile, None)
        self.assertIs(actual.runtime.web, actual.http)
        self.assertIs(actual.runtime.foundation.web, actual.http)
        self.assertEqual(actual.runtime.profile.binding(), source.binding())
        self.assertIsNone(actual.credentials); self.assertIsNone(actual.payload)
        self.assertEqual(actual.kwargs['target_package'], controller.root / 'binary/package.zip')

    def test_activation_uses_successor_admission_and_never_replays_cutover(self):
        from installer import gateway_transition_native as native
        control = object.__new__(native.NativeTransition)
        for key in ('http', 'scope', 'lease_id', 'backups', 'worker', 'source', 'payload', 'credentials'):
            setattr(control, key, Mock())
        admitted = Mock(raw=b'canonical-authority')
        with patch.object(native.authority, 'selected_for_admission', return_value=Mock()), \
             patch.object(native.authority.Authority, 'load', return_value=admitted), \
             patch.object(native.successor, 'execute', return_value={'native': 'completed'}) as call, \
             patch.object(native.cutover, 'recover', side_effect=AssertionError('old binary replay')):
            self.assertEqual(control.execute('activation'), {'native': 'completed'})
        self.assertEqual(call.call_args.kwargs['confirmation'], native.authority.sha(admitted.raw))
        self.assertEqual(call.call_args.kwargs['action'], 'resume')
        self.assertIs(call.call_args.args[0], control.http)

    def test_unknown_native_stage_cannot_be_a_command(self):
        from installer.gateway_transition_native import NativeTransition
        control = object.__new__(NativeTransition)
        for name in ('restart', 'restore', 'rollback', '/bin/sh'):
            with self.assertRaises(InstallerError): control.execute(name)
