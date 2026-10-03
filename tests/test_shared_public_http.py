"""Actual HTTPS boundary for the public controller; system effects stay in CI."""
import json
import unittest
from unittest.mock import patch

import test_httpd
import test_transaction_http
from github_fixture import make_service
from installer.service import POST_ROUTES


class SharedPublicHTTPTests(unittest.TestCase):
    _connection = test_httpd.HTTPSBootstrapTests._connection
    _unlock = test_httpd.HTTPSBootstrapTests._unlock
    login = test_transaction_http.TransactionHTTPTests.login
    request = test_transaction_http.TransactionHTTPTests.request

    def setUp(self):
        test_httpd.HTTPSBootstrapTests.setUp(self)
        self.addCleanup(test_httpd.HTTPSBootstrapTests.tearDown, self)
        self.service, _ = make_service(self.root)
        self.addCleanup(self.service.close)
        self.server.state.transaction_service = self.service
        self.cookie = self.csrf = None

    def test_all_seven_routes_require_session_origin_and_csrf_before_dispatch(self):
        routes = {k: v for k, v in POST_ROUTES.items() if k.startswith('/api/mobile/public/')}
        self.assertEqual(len(routes), 7)
        with patch.object(self.service.shared_public, 'execute') as execute, \
             patch.object(self.service.shared_public_preparation, 'execute') as prepare:
            for route in routes: self.assertEqual(self.request('POST', route, {})[0], 401)
            self.login()
            for route in routes:
                for headers in ({'X-Hestia-CSRF': ''}, {'Origin': 'https://foreign.invalid'}):
                    self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
            execute.assert_not_called(); prepare.assert_not_called()

    def test_routes_dispatch_only_the_fixed_controller_action(self):
        self.login()
        for prefix, control, actions, field in (
            ('/api/mobile/public/preparation/', self.service.shared_public_preparation, ('plan', 'check'), 'shared_public_preparation'),
            ('/api/mobile/public/', self.service.shared_public, ('plan', 'apply', 'resume', 'retry', 'check'), 'shared_public')):
            with patch.object(control, 'execute', return_value={'historical_only': True}) as invoke:
                for action in actions:
                    status, value, _ = self.request('POST', prefix + action, {'confirm': True})
                    self.assertEqual(status, 200, value); self.assertEqual(value[field], {'historical_only': True})
                    invoke.assert_called_with(action, {'confirm': True})
        for path in ('/api/mobile/public/start', '/api/mobile/public/rollback', '/api/mobile/public/preparation/apply'):
            self.assertEqual(self.request('POST', path, {})[0], 404)

    def test_get_and_report_create_no_plan_and_run_no_observation(self):
        self.login()
        with patch('installer.shared_public_runtime.SharedPublic', side_effect=AssertionError('native reader')), \
             patch('installer.shared_public_plan.reference', side_effect=AssertionError('parent observation')):
            status, value, _ = self.request('GET', '/api/wizard/state')
            self.assertEqual(status, 200, value)
            self.assertIsNone(value['shared_public']['installation'])
            self.assertFalse(value['shared_public']['public_mobile_available'])
            self.assertIsNone(value['shared_public']['verification'])
            status, report, _ = self.request('GET', '/api/installation/report')
            self.assertEqual(status, 200, report); self.assertNotIn('shared_public', report)
        self.assertFalse(self.service.shared_public_preparation.root.exists())

    def test_missing_parent_and_untrusted_parameters_cannot_create_private_state(self):
        self.login()
        for path, payload in (
            ('preparation/plan', {'public_sha256': 'a'*64, 'gateway_sha256': 'b'*64, 'client_networks': ['0.0.0.0/0'], 'path': '/etc'}),
            ('plan', {'preparation_sha256': 'a'*64, 'server': 'https://foreign.invalid'}),
            ('apply', {'confirmation': 'a'*64, 'confirm': 1}),
            ('plan', {'preparation_sha256': 'a'*64})):
            status, value, _ = self.request('POST', '/api/mobile/public/' + path, payload)
            self.assertNotEqual(status, 200, value)
            self.assertNotIn('/etc', json.dumps(value))
        self.assertFalse(self.service.shared_public_preparation.root.exists())

    def test_mutation_lock_excludes_public_execution_and_shutdown_refuses_it(self):
        with self.service._mutation(), patch.object(self.service.shared_public, 'execute') as invoke:
            with self.assertRaisesRegex(Exception, '^BUSY$'): self.service.execute('shared-public.apply', {})
            invoke.assert_not_called()
        self.service.close()
        with self.assertRaisesRegex(Exception, '^SHUTTING_DOWN$'): self.service.execute('shared-public.plan', {})

    def test_mobile_boot_fixed_routes_require_authentication_origin_csrf_and_mutation_lock(self):
        routes = {k:v for k,v in POST_ROUTES.items() if k.startswith('/api/mobile/boot/')}
        self.assertEqual(len(routes), 5)
        with patch.object(self.service.mobile_boot, 'execute', return_value={'historical_only': True}) as invoke:
            for route in routes: self.assertEqual(self.request('POST', route, {})[0], 401)
            self.login()
            for route, action in routes.items():
                for headers in ({'X-Hestia-CSRF':''}, {'Origin':'https://foreign.invalid'}):
                    self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
                status, value, _ = self.request('POST', route, {'confirm':True})
                self.assertEqual(status, 200, value); self.assertTrue(value['mobile_boot']['historical_only'])
                invoke.assert_called_with(action.removeprefix('mobile-boot.'), {'confirm':True})
            invoke.reset_mock()
            with self.service._mutation(), self.assertRaisesRegex(Exception, '^BUSY$'): self.service.execute('mobile-boot.apply', {})
            invoke.assert_not_called()
        for action in ('start', 'restart', 'rollback'):
            self.assertEqual(self.request('POST', '/api/mobile/boot/' + action, {})[0], 404)

    def test_mobile_boot_status_and_report_never_probe_or_create_an_enrollment(self):
        self.login()
        with patch('installer.mobile_boot_runtime.MobileBootRuntime', side_effect=AssertionError('host')):
            status, value, _ = self.request('GET', '/api/wizard/state')
            self.assertEqual(status, 200); self.assertIsNone(value['mobile_boot']['installation'])
            self.assertIsNone(value['mobile_boot']['verification']); self.assertFalse(value['mobile_boot']['phase6_complete'])
            status, value, _ = self.request('GET', '/api/installation/report')
            self.assertEqual(status, 200); self.assertNotIn('mobile_boot', value)
        self.assertFalse(self.service.mobile_boot.root.exists())
