import io
import json
import unittest
from unittest.mock import patch

from installer.gateway_plan import BinaryImport, IdentityPreparation
from installer.model import InstallerError
from github_fixture import confirm
from gateway_fixture import complete_web
import test_gateway_acquisition
import test_gateway_http


class GatewayImportTests(unittest.TestCase):
    setUp = test_gateway_acquisition.GatewayPlanTests.setUp
    plan = test_gateway_acquisition.GatewayPlanTests.plan

    def plan_import(self):
        self.payload['acquisition'] = 'package'
        document = self.plan()
        self.service.github.access.clear()
        return document

    def upload(self, document, content=None):
        content = self.responses.package if content is None else content
        return self.service.import_gateway_package(document['plan_sha256'], io.BytesIO(content),
            self.responses.selected['package_bytes'])['gateway']['preparation']

    def test_main_only_import_after_artifact_expiry_without_github_or_parent_change(self):
        self.responses.selected['expires_at'] = '2000-01-01T00:00:00Z'
        self.payload['dev_enabled'] = False
        document = self.plan_import(); before_requests = len(self.fake.requests)
        self.assertEqual(document['plan']['steps'][0]['requires_secrets'], [])
        result = self.upload(document); self.assertEqual(result['state'], 'DONE')
        self.assertEqual(len(self.fake.requests), before_requests); self.assertEqual(self.responses.downloads, 0)
        root = self.service.gateway.identities.root
        self.assertTrue((root / 'main.pem').is_file()); self.assertFalse((root / 'dev.pem').exists())
        self.assertEqual(self.original, self.service.engine.journal.path.read_bytes())
        self.assertEqual((root.parent / 'binary/package.zip').read_bytes(), self.responses.package)
        self.assertEqual((root.parent / 'binary/package.zip').stat().st_mode & 0o777, 0o600)

    def test_bad_confirmation_length_and_json_import_never_read_or_create_resources(self):
        document = self.plan_import()
        class Unreadable:
            def read(self, size): raise AssertionError('unapproved body read')
        before = self.service.gateway.journal.path.read_bytes()
        for digest, length in [('0' * 64, len(self.responses.package)), (document['plan_sha256'], 1)]:
            with self.assertRaises(InstallerError): self.service.import_gateway_package(digest, Unreadable(), length)
        with self.assertRaises(InstallerError): self.service.execute('gateway.import', confirm(document))
        self.assertEqual(before, self.service.gateway.journal.path.read_bytes())
        self.assertFalse((self.service.gateway.root / 'binary').exists())

    def test_corrupt_and_truncated_uploads_block_keys_then_explicit_reimport_succeeds(self):
        document = self.plan_import()
        for content in (b'x' + self.responses.package[1:], self.responses.package[:17]):
            result = self.upload(document, content)
            self.assertEqual(result['steps'][0]['state'], 'FAILED')
            self.assertFalse(self.service.gateway.identities.root.exists())
            self.assertFalse((self.service.gateway.root / 'binary/receipt.json').exists())
        result = self.upload(document); self.assertEqual(result['state'], 'DONE')
        self.assertFalse((self.service.gateway.root / 'binary/package.part').exists())

    def test_first_body_read_observes_durable_approval_and_apply_checkpoint(self):
        document = self.plan_import(); service = self.service; owner = self
        class CheckpointStream(io.BytesIO):
            def read1(self, size):
                persisted = service.gateway.journal.read()
                owner.assertEqual(persisted['approved_plan_sha256'], document['plan_sha256'])
                owner.assertEqual(persisted['steps'][0]['state'], 'RUNNING')
                owner.assertEqual(persisted['steps'][0]['phase'], 'apply')
                return super().read1(size)
        result = service.import_gateway_package(document['plan_sha256'], CheckpointStream(self.responses.package),
            len(self.responses.package))
        self.assertEqual(result['gateway']['preparation']['state'], 'DONE')

    def test_commit_crash_recovers_receipt_without_upload_or_network(self):
        document = self.plan_import()
        with patch.object(BinaryImport, 'commit', side_effect=OSError('interrupted')):
            result = self.upload(document)
        self.assertEqual(result['steps'][0]['phase'], 'commit')
        package = self.service.gateway.root / 'binary/package.zip'; before = package.stat().st_mtime_ns
        result = self.service.execute('gateway.retry', {**confirm(document), 'name': 'gateway.binary'})['gateway']['preparation']
        self.assertEqual(result['steps'][0]['state'], 'DONE')
        result = self.service.execute('gateway.resume', confirm(document))['gateway']['preparation']
        self.assertEqual(result['state'], 'DONE'); self.assertEqual(package.stat().st_mtime_ns, before)
        self.assertEqual(self.responses.downloads, 0)

    def test_identity_crash_preserves_main_dev_keys_and_refuses_duplicate_upload(self):
        document = self.plan_import()
        with patch.object(IdentityPreparation, 'commit', side_effect=OSError('interrupted')): result = self.upload(document)
        root = self.service.gateway.identities.root
        keys = {name: (root / (name + '.pem')).read_bytes() for name in ('main', 'dev')}
        with self.assertRaises(InstallerError): self.upload(document)
        result = self.service.execute('gateway.retry', {**confirm(document), 'name': 'gateway.identities'})['gateway']['preparation']
        self.assertEqual(result['state'], 'DONE')
        self.assertEqual(keys, {name: (root / (name + '.pem')).read_bytes() for name in keys})
        with self.assertRaises(InstallerError): self.upload(document)
        with patch('installer.gateway_plan.verify_package', side_effect=AssertionError('GET probe')):
            report = self.service.report()
        self.assertNotIn('PRIVATE KEY', json.dumps(report)); self.assertEqual(report['gateway']['preparation'], result)

    def test_foreign_partial_and_damaged_committed_package_are_never_replaced(self):
        document = self.plan_import(); self.upload(document, self.responses.package[:17])
        root = self.service.gateway.root / 'binary'; foreign = root / 'foreign'; foreign.write_bytes(b'untouched')
        result = self.upload(document); self.assertEqual(result['steps'][0]['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(foreign.read_bytes(), b'untouched'); foreign.unlink()
        self.assertEqual(self.upload(document)['state'], 'DONE')
        (root / 'package.zip').write_bytes(b'damaged')
        before = self.service.gateway.journal.path.read_bytes()
        with self.assertRaises(InstallerError): self.service.execute('gateway.resume', confirm(document))
        self.assertEqual(before, self.service.gateway.journal.path.read_bytes())
        self.assertEqual((root / 'package.zip').read_bytes(), b'damaged')

    def test_old_github_plan_profile_and_journal_cannot_be_migrated_by_import(self):
        document = self.plan(); profile = (self.service.gateway.root / 'profile.json').read_bytes()
        journal = self.service.gateway.journal.path.read_bytes()
        self.assertNotIn('acquisition', self.service.gateway.profile())
        with self.assertRaises(InstallerError): self.upload(document)
        with self.assertRaises(InstallerError): self.service.execute('gateway.plan', {**self.payload, 'acquisition': 'package'})
        self.assertEqual(profile, (self.service.gateway.root / 'profile.json').read_bytes())
        self.assertEqual(journal, self.service.gateway.journal.path.read_bytes())
        result = self.service.execute('gateway.apply', confirm(document))['gateway']['preparation']
        self.assertEqual(result['state'], 'DONE'); self.assertEqual(self.responses.downloads, 1)


class GatewayImportHTTPTests(unittest.TestCase):
    setUp = test_gateway_http.GatewayHTTPTests.setUp
    tearDown = test_gateway_http.GatewayHTTPTests.tearDown
    _connection = test_gateway_http.GatewayHTTPTests._connection
    _unlock = test_gateway_http.GatewayHTTPTests._unlock
    login = test_gateway_http.GatewayHTTPTests.login
    request = test_gateway_http.GatewayHTTPTests.request
    route = '/api/gateway/preparation/import'

    def plan_import(self):
        parent = complete_web(self.service); self.login()
        status, value, _ = self.request('POST', '/api/gateway/preparation/plan', {
            'web_plan_sha256': parent['plan_sha256'], 'public_origin': 'https://mobile.example',
            'dev_enabled': False, 'acquisition': 'package'})
        self.assertEqual(status, 200, value)
        return {'Content-Type': 'application/zip', 'X-Hestia-Plan': value['gateway']['preparation']['plan_sha256']}

    def test_binary_route_requires_session_csrf_origin_and_exact_headers(self):
        self.assertEqual(self.request('POST', self.route, raw=b'bad')[0], 401)
        headers = self.plan_import(); before = self.service.gateway.journal.path.read_bytes()
        changes = [({'X-Hestia-CSRF': ''}, 403), ({'Origin': 'https://evil.invalid'}, 403),
                   ({'Content-Type': 'application/json'}, 400), ({'X-Hestia-Plan': 'invalid'}, 400),
                   ({'Content-Encoding': 'gzip'}, 400), ({'Transfer-Encoding': 'chunked'}, 400),
                   ({'Content-Length': '0'}, 413), ({'Content-Length': '134217729'}, 413),
                   ({'X-Hestia-Plan': '0' * 64}, 400)]
        for change, expected in changes:
            with self.subTest(change=change):
                status, _, response = self.request('POST', self.route, headers={**headers, **change}, raw=self.responses.package)
                self.assertEqual(status, expected); self.assertEqual(response['Connection'], 'close')
        self.assertEqual(before, self.service.gateway.journal.path.read_bytes())
        self.assertFalse(self.service.gateway.identities.root.exists())

    def test_https_upload_done_refresh_and_duplicate_headers_refused(self):
        headers = self.plan_import()
        for name in ('Content-Length', 'X-Hestia-Plan', 'Content-Encoding'):
            connection = self._connection(); connection.putrequest('POST', self.route)
            values = {**headers, 'Cookie': self.cookie, 'X-Hestia-CSRF': self.csrf,
                      'Origin': f'https://127.0.0.1:{self.port}', 'Content-Length': str(len(self.responses.package)), 'Content-Encoding': 'identity'}
            for key, value in values.items(): connection.putheader(key, value)
            connection.putheader(name, values[name]); connection.endheaders()
            response = connection.getresponse(); self.assertEqual(response.status, 400); response.read(); connection.close()
        status, result, response = self.request('POST', self.route, headers=headers, raw=self.responses.package)
        self.assertEqual(status, 200, result); self.assertEqual(result['gateway']['preparation']['state'], 'DONE')
        self.assertEqual(response['Connection'], 'close')
        status, report, _ = self.request('GET', '/api/installation/report')
        self.assertEqual(status, 200); self.assertEqual(report['gateway']['preparation'], result['gateway']['preparation'])
        status, _, _ = self.request('POST', self.route, headers=headers, raw=self.responses.package)
        self.assertEqual(status, 409); self.assertEqual(self.responses.downloads, 0)
