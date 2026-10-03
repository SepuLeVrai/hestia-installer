"""FCM consent and immutable service bindings. Pure temporary-file tests."""
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from installer.fcm_plan import FcmPlan
from installer.fcm_credentials import public_binding, sha
from installer.gateway_release import FCM_COMMIT, release
from installer.gateway_service_profile import GatewayServiceProfile
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import InstallerError, canonical_bytes
from installer.transaction import StateJournal
import test_fcm_credentials as credentials_fixture
import test_gateway_service as service_fixture


class FcmPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        credentials_fixture.FcmCredentialsTests.setUpClass.__func__(cls)

    def setUp(self):
        credentials_fixture.FcmCredentialsTests.setUp(self)
        self.parent = SimpleNamespace(journal=StateJournal(Path(self.temp.name) / 'parent' / 'state.json'))
        with self.parent.journal.locked(create=True): pass
        self.parent_document = {'state': 'DONE', 'plan_sha256': 'b' * 64}
        reader = patch.object(self.parent.journal, '_read_at', side_effect=lambda fd: self.parent_document)
        reader.start(); self.addCleanup(reader.stop)
        self.gateway = SimpleNamespace(root=Path(self.temp.name), parent=self.parent,
            profile=Mock(), engine=Mock(), _parent=Mock())
        self.gateway.profile.return_value = {'release': release(FCM_COMMIT), 'web_plan_sha256': 'b' * 64}
        self.gateway.engine.return_value.report.return_value = {'state': 'DONE', 'plan_sha256': 'a' * 64}
        self.service = Mock(); self.service.profile.return_value = None
        self.control = FcmPlan(self.gateway, self.service)

    def plan(self):
        return self.control.execute('plan', {k: self.profile[k] for k in ('gateway_plan_sha256', 'project_id')})

    def apply(self):
        value = self.plan(); raw = json.dumps(self.account).encode()
        return self.control.import_file(value['confirmation'], io.BytesIO(raw), len(raw))

    def test_completed_gateway_and_matching_web_are_required_before_planning(self):
        for changes in ({'release': release()}, {'web_plan_sha256': 'c' * 64}):
            original = copy.deepcopy(self.gateway.profile.return_value)
            self.gateway.profile.return_value.update(changes)
            with self.assertRaises(InstallerError): self.plan()
            self.assertFalse(self.root.exists()); self.gateway.profile.return_value = original
        self.gateway.engine.return_value.report.return_value['state'] = 'PLANNED'
        with self.assertRaises(InstallerError): self.plan()
        self.assertFalse(self.root.exists())

    def test_confirmation_and_finalized_service_precede_stream_read(self):
        value = self.plan(); stream = Mock()
        for confirmation, service in (('c' * 64, None), (value['confirmation'], {'sealed': True})):
            self.service.profile.return_value = service
            with self.assertRaises(InstallerError): self.control.import_file(confirmation, stream, 1)
            stream.read.assert_not_called()
        self.assertFalse((self.root / 'server.json').exists())

    def test_global_lock_prevents_import_without_reading_body(self):
        value = self.plan(); stream = Mock()
        with self.parent.journal.locked(), self.assertRaisesRegex(InstallerError, 'BUSY'):
            self.control.import_file(value['confirmation'], stream, 1)
        stream.read.assert_not_called()

    def test_plan_and_import_bind_exact_prepared_gateway(self):
        value = self.plan(); self.gateway.engine.return_value.report.return_value['plan_sha256'] = 'c' * 64
        with self.assertRaises(InstallerError): self.plan()
        stream = Mock()
        with self.assertRaises(InstallerError): self.control.import_file(value['confirmation'], stream, 1)
        stream.read.assert_not_called()

    def test_length_is_bounded_and_truncation_never_writes_private_file(self):
        value = self.plan(); stream = Mock()
        for length in (True, 0, -1, 16385, '1'):
            with self.assertRaises(InstallerError): self.control.import_file(value['confirmation'], stream, length)
        stream.read.assert_not_called()
        with self.assertRaises(InstallerError): self.control.import_file(value['confirmation'], io.BytesIO(b'{}'), 3)
        self.assertFalse((self.root / 'server.json').exists())

    def test_import_check_and_historical_read_have_distinct_effects(self):
        value = self.apply(); self.assertEqual(value['state'], 'IMPORTED')
        with patch.object(self.control.store, 'verify', side_effect=AssertionError('GET verification')):
            self.assertEqual(self.control.state(), value)
        result = self.control.execute('check', {'confirmation': value['confirmation'], 'confirm': True})
        self.assertTrue(result['availability']['credential_valid'])
        self.assertFalse(result['availability']['google_authorization_verified'])
        self.assertNotIn(self.key, json.dumps(result)); self.assertNotIn(self.account['client_email'], json.dumps(result))
        (self.root / 'server.json').unlink()
        with self.assertRaises(InstallerError): self.control.execute('check', {'confirmation': value['confirmation'], 'confirm': True})
        self.assertFalse((self.root / 'server.json').exists())

    def test_check_requires_separate_exact_confirmation(self):
        value = self.apply()
        for payload in ({'confirmation': 'c' * 64, 'confirm': True}, {'confirmation': value['confirmation'], 'confirm': False}):
            with patch.object(self.control.store, 'verify', side_effect=AssertionError('early verification')), self.assertRaises(InstallerError):
                self.control.execute('check', payload)


class FcmServiceProfileTests(unittest.TestCase):
    def setUp(self):
        self.legacy = service_fixture.GatewayServiceProfileTests().profile()
        selection = {'version': 1, 'project_id': 'hestia-test', 'gateway_plan_sha256': 'a' * 64}
        receipt = {'version': 1, 'profile_sha256': sha(canonical_bytes(selection)), 'project_id': 'hestia-test',
                   'credential_sha256': 'b' * 64, 'public_key_sha256': 'c' * 64}
        self.push = public_binding(selection, receipt)

    def profile(self, **kwargs):
        return GatewayServiceProfile(self.legacy.foundation, self.legacy.identity, self.legacy.key_directory, **kwargs)

    def test_legacy_profile_and_reconstruction_keep_exact_bytes(self):
        restored = GatewayServiceProfile.from_binding(self.legacy.foundation, self.legacy.binding())
        self.assertEqual(restored.unit_bytes(), self.legacy.unit_bytes())
        self.assertEqual(restored.configuration(), self.legacy.configuration())
        self.assertNotIn('push', restored.binding()); self.assertNotIn('project-push', restored.unit_bytes().decode())

    def test_project_and_private_path_are_bound_to_qualified_service(self):
        profile = self.profile(release_commit=FCM_COMMIT, push=self.push)
        self.assertEqual(profile.configuration()['project_push_project_id'], 'hestia-test')
        self.assertEqual(profile.configuration()['project_push_credential'], '/run/credentials/' + profile.unit + '/project-push')
        self.assertIn(b'LoadCredential=project-push:/var/lib/installer/gateway/fcm/server.json\n', profile.unit_bytes())
        restored = GatewayServiceRuntime.from_binding(profile.foundation, profile.binding())
        self.assertEqual(restored.profile.binding(), profile.binding())
        self.assertEqual(restored.profile.selected_release, release(FCM_COMMIT))
        self.push['selection']['project_id'] = 'changed-project'
        self.assertEqual(profile.configuration()['project_push_project_id'], 'hestia-test')

    def test_unqualified_release_legacy_push_and_forged_receipt_are_refused(self):
        for kwargs in ({'push': self.push}, {'release_commit': 'f' * 40}):
            with self.assertRaises(InstallerError): self.profile(**kwargs)
        self.push['receipt']['credential_sha256'] = 'unverified'
        with self.assertRaises(InstallerError): self.profile(release_commit=FCM_COMMIT, push=self.push)

    def test_readers_refuse_changed_unit_binary_and_extra_binding(self):
        profile = self.profile(release_commit=FCM_COMMIT, push=self.push)
        for key, value in (('unit_sha256', 'f' * 64), ('unexpected', True), ('release', self.legacy.binding()['release'])):
            binding = profile.binding(); binding[key] = value
            with self.subTest(key=key), self.assertRaises(InstallerError):
                GatewayServiceRuntime.from_binding(profile.foundation, binding)


if __name__ == '__main__': unittest.main()
