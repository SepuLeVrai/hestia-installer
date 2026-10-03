"""Actual HTTPS upload and consent guards. Disposable CI harness only."""
import json
import unittest
from unittest.mock import patch

import fcm_fixture
import test_gateway_http as gateway_fixture


class FcmHTTPTests(unittest.TestCase):
    _connection = gateway_fixture.GatewayHTTPTests._connection
    _unlock = gateway_fixture.GatewayHTTPTests._unlock
    login = gateway_fixture.GatewayHTTPTests.login
    request = gateway_fixture.GatewayHTTPTests.request
    tearDown = gateway_fixture.GatewayHTTPTests.tearDown

    @classmethod
    def setUpClass(cls): cls.raw = fcm_fixture.credential()

    def setUp(self):
        gateway_fixture.GatewayHTTPTests.setUp(self)
        self.responses = fcm_fixture.responses(); self.fake.override = self.responses
        catalogue = patch('installer.gateway_release._FCM_RELEASE', self.responses.selected)
        catalogue.start(); self.addCleanup(catalogue.stop)

    def plan(self):
        gateway = fcm_fixture.prepare(self.service); self.login()
        status, value, _ = self.request('POST', '/api/gateway/fcm/plan', {
            'gateway_plan_sha256': gateway['preparation']['plan_sha256'], 'project_id': 'hestia-test'})
        self.assertEqual(status, 200, value)
        return value['fcm']

    def test_all_fcm_routes_require_session_csrf_and_origin(self):
        routes = ['/api/gateway/fcm/' + action for action in ('plan', 'import', 'check')]
        for route in routes: self.assertEqual(self.request('POST', route, {})[0], 401)
        self.login()
        for route in routes:
            for headers in ({'X-Hestia-CSRF': ''}, {'Origin': 'https://evil.invalid'}):
                self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
        self.assertFalse(self.service.fcm.store.root.exists())

    def test_upload_encoding_size_and_confirmation_reject_before_private_write(self):
        value = self.plan(); headers = {'X-Hestia-Plan': value['confirmation']}
        for changes, expected in (({'Content-Type': 'application/zip'}, 400), ({'Content-Encoding': 'gzip'}, 400),
                ({'Transfer-Encoding': 'chunked'}, 400), ({'Content-Length': '16385'}, 413),
                ({'X-Hestia-Plan': 'b' * 64}, 400)):
            status, result, _ = self.request('POST', '/api/gateway/fcm/import', raw=self.raw, headers={**headers, **changes})
            self.assertEqual(status, expected, result)
            self.assertFalse((self.service.fcm.store.root / 'server.json').exists())

    def test_https_import_repeat_check_and_reports_never_expose_credential(self):
        value = self.plan(); parent = self.service.engine.journal.path.read_bytes()
        for _ in range(2):
            status, result, _ = self.request('POST', '/api/gateway/fcm/import', raw=self.raw,
                headers={'X-Hestia-Plan': value['confirmation']})
            self.assertEqual(status, 200, result); self.assertEqual(result['fcm']['state'], 'IMPORTED')
        with patch('installer.fcm_credentials._openssl', side_effect=AssertionError('GET process')):
            for route in ('/api/wizard/state', '/api/installation/report'):
                status, result, _ = self.request('GET', route)
                self.assertEqual(status, 200, result)
                self.assertNotIn('PRIVATE KEY', json.dumps(result)); self.assertNotIn('sender@', json.dumps(result))
        status, result, _ = self.request('POST', '/api/gateway/fcm/check', {'confirm': True, 'confirmation': value['confirmation']})
        self.assertEqual(status, 200, result); self.assertTrue(result['fcm']['availability']['credential_valid'])
        self.assertFalse(result['fcm']['availability']['google_authorization_verified'])
        self.assertEqual(parent, self.service.engine.journal.path.read_bytes())

    def test_gateway_release_selection_is_closed_and_persisted(self):
        from gateway_fixture import complete_web
        web = complete_web(self.service); self.login()
        payload = {'web_plan_sha256': web['plan_sha256'], 'public_origin': 'https://mobile.customer.example', 'dev_enabled': False}
        for value in (None, True, 'main', 'a' * 40):
            status, _, _ = self.request('POST', '/api/gateway/preparation/plan', {**payload, 'release_commit': value})
            self.assertIn(status, (400, 409)); self.assertIsNone(self.service.gateway.profile())
        from installer.gateway_release import FCM_COMMIT
        status, value, _ = self.request('POST', '/api/gateway/preparation/plan', {**payload, 'release_commit': FCM_COMMIT})
        self.assertEqual(status, 200, value); original = value['gateway']
        status, value, _ = self.request('POST', '/api/gateway/preparation/plan', payload)
        self.assertEqual(status, 200, value); self.assertEqual(value['gateway'], original)


if __name__ == '__main__': unittest.main()
