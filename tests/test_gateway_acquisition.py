import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer.gateway_download import download
from installer.gateway_release import verify_package, copy_package, sha, members
from installer.gateway_plan import GatewayPlan, BinaryAcquisition, IdentityPreparation
from installer.github_client import GitHubClient
from installer.model import InstallerError, canonical_bytes
from installer.operations import RecoveryDecision
from github_fixture import DUMMY, FakeGitHub, Response, confirm, make_service
from gateway_fixture import ArtifactResponses, complete_web, zip_bytes


class GatewayTransportTests(unittest.TestCase):
    def setUp(self):
        self.responses = ArtifactResponses(); self.fake = FakeGitHub(); self.fake.override = self.responses
        self.client = GitHubClient(opener=self.fake)

    def acquire(self):
        output = io.BytesIO(); download(self.client, self.responses.selected, DUMMY, output); return output

    def test_exact_qualified_run_artifact_and_unsigned_download(self):
        artifact = self.acquire(); package = io.BytesIO()
        copy_package(artifact, package, self.responses.selected)
        self.assertEqual(verify_package(package, self.responses.selected)['sqlite_schema'], 6)
        self.assertEqual(len(self.fake.requests), 4)
        for request in self.fake.requests[:3]: self.assertEqual(request.get_header('Authorization'), 'Bearer ' + DUMMY)
        self.assertIsNone(self.fake.requests[-1].get_header('Authorization'))
        self.assertIsNone(self.fake.requests[-1].get_header('Cookie'))

    def test_expired_foreign_failed_run_or_changed_artifact_never_downloads(self):
        changes = [('run', {'conclusion': 'failure'}), ('run', {'head_sha': 'f' * 40}),
                   ('run', {'path': '.github/workflows/workbench.yml'}), ('run', {'event': 'pull_request'}),
                   ('run', {'head_repository': {'id': 4, 'full_name': self.responses.selected['repository']}}),
                   ('metadata', {'expired': True}), ('metadata', {'id': 1}), ('metadata', {'size_in_bytes': 1}),
                   ('metadata', {'digest': 'sha256:' + '0' * 64}), ('metadata', {'workflow_run': {}})]
        for key, change in changes:
            with self.subTest(change=change):
                original = copy.deepcopy(getattr(self.responses, key)); getattr(self.responses, key).update(change)
                with self.assertRaises(InstallerError): self.acquire()
                setattr(self.responses, key, original)
        self.assertEqual(self.responses.downloads, 0)

    def test_hostile_redirects_are_refused_before_unsigned_request(self):
        for location in ['http://productionresultssa1.blob.core.windows.net/a?sig=x', 'https://evil.invalid/a?sig=x',
                         'https://blob.core.windows.net.evil.invalid/a?sig=x', 'https://127.0.0.1/a?sig=x',
                         'https://u@productionresultssa1.blob.core.windows.net/a?sig=x',
                         'https://productionresultssa1.blob.core.windows.net:443/a?sig=x',
                         'https://productionresultssa1.blob.core.windows.net/a?sig=' + DUMMY,
                         'https://productionresultssa1.blob.core.windows.net/a?sig=x#fragment', 'https://[broken']:
            self.responses.location = location
            with self.subTest(location=location), self.assertRaises(InstallerError): self.acquire()
        self.assertEqual(self.responses.downloads, 0)

    def test_no_second_redirect_error_body_or_signed_url_in_error(self):
        original = self.responses
        for response in (Response(status=302, headers={'Location': 'https://evil.invalid'}), OSError(original.location)):
            def override(request):
                if request.full_url == original.location:
                    if isinstance(response, Exception): raise response
                    return response
                return original(request)
            self.fake.override = override
            with self.assertRaises(InstallerError) as error: self.acquire()
            self.assertNotIn('ephemeral', str(error.exception)); self.assertNotIn(DUMMY, str(error.exception))

    def test_corruption_limits_inner_checksum_and_binary_mode_fail_closed(self):
        selected = self.responses.selected
        for content in (b'', self.responses.package[:-1], self.responses.package + b'x'):
            with self.assertRaises(InstallerError): verify_package(io.BytesIO(content), selected)
        with self.assertRaises(InstallerError): copy_package(io.BytesIO(self.responses.artifact + b'x'), io.BytesIO(), selected)
        # Even when an outer hash matches a catalogue fixture, internal proof is required.
        for mode, sums in [(0o644, None), (0o755, b'0' * 64 + b'  VERSION\n')]:
            files = [('VERSION', (selected['version'] + '\n').encode(), 0o644),
                     ('bin/hestia-mobile-gateway', b'INERT', mode)]
            checksum = sums or ''.join(sha(data) + '  ' + name + '\n' for name, data, _ in files).encode()
            files.append(('SHA256SUMS', checksum, 0o644)); content = zip_bytes(files)
            altered = {**selected, 'package_bytes': len(content), 'package_sha256': sha(content),
                       'binary_sha256': sha(b'INERT'), 'unpacked_bytes': sum(len(f[1]) for f in files)}
            with self.assertRaises(InstallerError): verify_package(io.BytesIO(content), altered)

    def test_duplicate_traversal_symlink_and_file_parent_layouts_refused(self):
        import zipfile
        import warnings
        for names in [['../outside'], ['/outside'], ['a/./b'], ['a\\b'], ['same', 'same'], ['a', 'a/b']]:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore'); content = zip_bytes([(n, b'x', 0o644) for n in names])
            with zipfile.ZipFile(io.BytesIO(content)) as archive, self.assertRaises(InstallerError):
                members(archive, count=10, size=100)
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            entry = zipfile.ZipInfo('link'); entry.external_attr = 0o120777 << 16; archive.writestr(entry, b'outside')
        with zipfile.ZipFile(content) as archive, self.assertRaises(InstallerError): members(archive, count=10, size=100)


class GatewayPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.service, self.fake = make_service(self.root); self.addCleanup(self.service.close)
        self.responses = ArtifactResponses(); self.fake.override = self.responses
        self.catalogue = patch('installer.gateway_release._RELEASE', self.responses.selected)
        self.catalogue.start(); self.addCleanup(self.catalogue.stop)
        self.parent = complete_web(self.service)
        self.original = self.service.engine.journal.path.read_bytes()
        self.payload = {'web_plan_sha256': self.parent['plan_sha256'], 'public_origin': 'https://mobile.customer.example', 'dev_enabled': True}

    def plan(self): return self.service.execute('gateway.plan', self.payload)['gateway']['preparation']
    def apply(self, document): return self.service.execute('gateway.apply', confirm(document))['gateway']['preparation']

    def test_separate_plan_consent_and_complete_receipts_parent_unchanged(self):
        document = self.plan(); self.assertEqual(self.responses.downloads, 0)
        self.assertFalse(self.service.gateway.identities.root.exists())
        for payload in ({**confirm(document), 'confirm': False}, confirm(self.parent), {**confirm(document), 'url': 'https://evil.invalid'}):
            with self.assertRaises(InstallerError): self.service.execute('gateway.apply', payload)
        result = self.apply(document); self.assertEqual(result['state'], 'DONE')
        self.assertEqual(self.responses.downloads, 1)
        self.assertFalse(self.service.github.access.status()['ready'])
        self.assertEqual(self.original, self.service.engine.journal.path.read_bytes())
        state = self.service.gateway.state()
        self.assertFalse(state['deployment_available']); self.assertEqual(set(state['identities']['receipt']['identities']), {'main', 'dev'})
        raw = json.dumps(self.service.report()); self.assertNotIn(DUMMY, raw); self.assertNotIn('PRIVATE KEY', raw); self.assertNotIn('ephemeral-fixture', raw)
        self.assertEqual((self.service.gateway.root / 'binary/package.zip').stat().st_mode & 0o777, 0o600)

    def test_no_get_restart_probe_and_done_is_historical_after_damage(self):
        result = self.apply(self.plan()); path = self.service.gateway.identities.root / 'main.pem'; path.unlink()
        controller = GatewayPlan(self.service.engine)
        with patch('installer.gateway_identity.subprocess.run', side_effect=AssertionError('probe')), patch('installer.gateway_plan.verify_package', side_effect=AssertionError('probe')):
            self.assertEqual(controller.state()['preparation'], result)
            self.assertEqual(self.service.wizard_state()['gateway']['preparation'], result)
            self.assertEqual(self.service.report()['gateway']['preparation'], result)
        with self.assertRaises(InstallerError): self.service.execute('gateway.check', confirm(result))
        self.assertEqual(controller.state()['preparation'], result); self.assertFalse(path.exists())

    def test_interrupted_download_revalidates_secret_targeted_retry_and_resume(self):
        document = self.plan(); self.responses.fail = True
        failed = self.apply(document); self.assertEqual(failed['steps'][0]['state'], 'FAILED')
        self.assertFalse(self.service.gateway.identities.root.exists())
        self.responses.fail = False
        retry = {**confirm(document), 'name': 'gateway.binary'}
        again = self.service.execute('gateway.retry', retry)['gateway']['preparation']
        self.assertEqual(again['last_error_redacted'], 'SECRET_REQUIRED')
        self.service.execute('github.validate', {'credential': DUMMY})
        result = self.service.execute('gateway.retry', retry)['gateway']['preparation']
        self.assertEqual(result['steps'][0]['state'], 'DONE')
        result = self.service.execute('gateway.resume', confirm(result))['gateway']['preparation']
        self.assertEqual(result['state'], 'DONE'); self.assertEqual(self.responses.downloads, 2)
        self.assertEqual(self.original, self.service.engine.journal.path.read_bytes())

    def test_interrupted_identity_keeps_key_and_never_redownloads(self):
        document = self.plan()
        from installer.gateway_identity import GatewayIdentityStore
        original = GatewayIdentityStore._write_key
        def fail(fd, environment, private):
            if environment == 'dev': raise OSError('interrupted')
            return original(fd, environment, private)
        with patch.object(GatewayIdentityStore, '_write_key', side_effect=fail): result = self.apply(document)
        self.assertEqual(result['steps'][1]['state'], 'FAILED')
        key = self.service.gateway.identities.root / 'main.pem'; before = key.read_bytes()
        result = self.service.execute('gateway.retry', {**confirm(document), 'name': 'gateway.identities'})['gateway']['preparation']
        self.assertEqual(result['state'], 'DONE'); self.assertEqual(key.read_bytes(), before); self.assertEqual(self.responses.downloads, 1)

    def test_commit_crash_recovers_durable_receipt_without_secret_or_rotation(self):
        document = self.plan()
        with patch.object(IdentityPreparation, 'commit', side_effect=OSError('power loss')): result = self.apply(document)
        self.assertEqual(result['steps'][1]['phase'], 'commit')
        key = (self.service.gateway.identities.root / 'main.pem').read_bytes()
        result = self.service.execute('gateway.retry', {**confirm(document), 'name': 'gateway.identities'})['gateway']['preparation']
        self.assertEqual(result['state'], 'DONE'); self.assertEqual((self.service.gateway.identities.root / 'main.pem').read_bytes(), key)

    def test_foreign_files_links_and_committed_binary_damage_block_resume(self):
        document = self.plan(); self.apply(document)
        path = self.service.gateway.root / 'binary/package.zip'; original = path.read_bytes()
        for damage in ('content', 'mode', 'link', 'foreign'):
            with self.subTest(damage=damage):
                if damage == 'content': path.write_bytes(b'foreign')
                if damage == 'mode': path.chmod(0o644)
                if damage == 'link': path.unlink(); path.symlink_to(self.root / 'absent')
                if damage == 'foreign': (path.parent / 'unknown').write_bytes(b'foreign')
                with self.assertRaises(Exception): self.service.execute('gateway.check', confirm(document))
                if damage == 'link': path.unlink()
                path.write_bytes(original); path.chmod(0o600)
                if damage == 'foreign': (path.parent / 'unknown').unlink()
        self.assertEqual(self.service.execute('gateway.check', confirm(document))['gateway']['verification']['state'], 'PREPARATION_VERIFIED')

    def test_closed_profile_source_parent_and_plan_registry(self):
        document = self.plan(); self.assertEqual(self.plan(), document)
        for change in ({'public_origin': 'https://other.example'}, {'dev_enabled': False}, {'commit': 'main'}, {'web_plan_sha256': '0' * 64}):
            with self.assertRaises(InstallerError): self.service.execute('gateway.plan', {**self.payload, **change})
        value = self.service.gateway.profile(); value['release']['commit'] = '0' * 40
        (self.service.gateway.root / 'profile.json').write_bytes(canonical_bytes(value))
        with self.assertRaises(InstallerError): self.service.gateway.engine()

    def test_main_only_without_dev_key_and_sealed_key_preserved_on_repeat(self):
        self.payload['dev_enabled'] = False
        result = self.apply(self.plan()); key = self.service.gateway.identities.root / 'main.pem'; original = key.read_bytes()
        self.assertFalse((key.parent / 'dev.pem').exists())
        self.service.execute('gateway.resume', confirm(result)); self.assertEqual(key.read_bytes(), original)
        self.assertEqual(self.responses.downloads, 1)
