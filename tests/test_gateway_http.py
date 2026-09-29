import json
import unittest
from unittest.mock import patch

import test_httpd
import test_transaction_http
from github_fixture import DUMMY, confirm, make_service
from gateway_fixture import ArtifactResponses, complete_web


class GatewayHTTPTests(unittest.TestCase):
    _connection = test_httpd.HTTPSBootstrapTests._connection
    _unlock = test_httpd.HTTPSBootstrapTests._unlock
    login = test_transaction_http.TransactionHTTPTests.login
    request = test_transaction_http.TransactionHTTPTests.request

    def setUp(self):
        test_httpd.HTTPSBootstrapTests.setUp(self)
        self.service, self.fake = make_service(self.root)
        self.server.state.transaction_service = self.service
        self.cookie = self.csrf = None
        self.responses = ArtifactResponses(); self.fake.override = self.responses
        self.catalogue = patch('installer.gateway_release._RELEASE', self.responses.selected); self.catalogue.start()

    def tearDown(self):
        self.catalogue.stop(); self.service.close(); test_httpd.HTTPSBootstrapTests.tearDown(self)

    def test_session_csrf_and_origin_protect_every_mutation_route(self):
        routes = ['/api/gateway/preparation/' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')]
        for route in routes: self.assertEqual(self.request('POST', route, {})[0], 401)
        self.login()
        for route in routes:
            for headers in ({'X-Hestia-CSRF': ''}, {'Origin': 'https://evil.invalid'}):
                self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
        self.assertFalse(self.service.gateway.root.exists()); self.assertEqual(self.responses.downloads, 0)

    def test_real_https_plan_apply_report_check_and_refresh_without_probe(self):
        parent = complete_web(self.service); original = self.service.engine.journal.path.read_bytes(); self.login()
        status, value, _ = self.request('POST', '/api/gateway/preparation/plan', {
            'web_plan_sha256': parent['plan_sha256'], 'public_origin': 'https://mobile.example', 'dev_enabled': False})
        self.assertEqual(status, 200, value); document = value['gateway']['preparation']
        status, value, _ = self.request('POST', '/api/gateway/preparation/apply', confirm(document))
        self.assertEqual(status, 200, value); self.assertEqual(value['gateway']['preparation']['state'], 'DONE')
        with patch('installer.gateway_plan.verify_package', side_effect=AssertionError('GET probe')), patch('installer.gateway_identity._openssl', side_effect=AssertionError('GET key')):
            for route in ('/api/wizard/state', '/api/installation/report'):
                status, value, _ = self.request('GET', route)
                self.assertEqual(status, 200, value); self.assertEqual(value['gateway']['preparation']['state'], 'DONE')
                for forbidden in (DUMMY, 'PRIVATE KEY', 'ephemeral-fixture'): self.assertNotIn(forbidden, json.dumps(value))
        status, value, _ = self.request('POST', '/api/gateway/preparation/check', confirm(document))
        self.assertEqual(status, 200, value); self.assertEqual(value['gateway']['verification']['state'], 'PREPARATION_VERIFIED')
        self.assertEqual(original, self.service.engine.journal.path.read_bytes()); self.assertEqual(self.responses.downloads, 1)
