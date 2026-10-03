"""DEV registration, immutable renderings, distinct keys and closed probes."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import base64
import json
import unittest
from unittest.mock import Mock, patch

from installer import foundation_probe as probe, foundation_drain as drain
from installer.dev_target import DevTarget, DevRegistration, DevManagedProfile, digest
from installer.gateway_service_profile import GatewayServiceProfile
from installer.gateway_release import FCM_COMMIT
from installer.model import InstallerError, canonical_bytes
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_identity import public_identity
from installer.fcm_credentials import FcmCredentials, public_binding
from dev_fixture import target_fixture
import fcm_fixture


class DevTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.main, self.target, self.identity, self.keys, self.foundation = target_fixture(self.root)

    def profile(self, push=None):
        return GatewayServiceProfile(self.foundation, self.identity, self.keys.root, release_commit=FCM_COMMIT, push=push)

    def test_pure_registration_descriptor_has_fixed_port_source_and_distinct_sql(self):
        with patch('subprocess.run', side_effect=AssertionError('native effect')):
            target = DevTarget(self.target.value).separate(self.foundation.web, self.main['configuration'])
        self.assertEqual(target.http.spec.port, 9084)
        self.assertEqual(target.http.source_commit, self.foundation.web.source_commit)
        self.assertNotEqual(target.http.spec.instance, self.foundation.web.spec.instance)
        self.assertNotEqual(target.http.spec.service_user, self.foundation.web.spec.service_user)

    def test_same_database_user_or_main_binding_is_refused(self):
        for key, value in (('name', self.main['configuration']['database']['name']),
                           ('user', self.main['configuration']['database']['user'])):
            changed = deepcopy(self.target.value); changed['configuration']['database'][key] = value
            with self.assertRaises(InstallerError): DevTarget(changed).separate(self.foundation.web, self.main['configuration'])
        changed = deepcopy(self.target.value); changed['main_configuration_sha256'] = 'f' * 64
        with self.assertRaises(InstallerError): DevTarget(changed).separate(self.foundation.web, self.main['configuration'])

    def test_same_instance_identity_or_nested_paths_are_refused(self):
        for key, value in (('instance', self.foundation.web.spec.instance), ('service_user', self.foundation.web.spec.service_user),
                           ('root', str(self.foundation.web.spec.root)), ('webroot', str(self.foundation.web.spec.webroot))):
            changed = deepcopy(self.target.value); changed['descriptor']['http'][key] = value
            if key in ('service_user', 'webroot'): changed['configuration']['web'][key] = value
            with self.assertRaises(Exception): DevTarget(changed).separate(self.foundation.web, self.main['configuration'])

    def test_closed_descriptor_rejects_unknown_version_port_paths_and_subjects(self):
        mutations = [lambda v: v.update(version=True), lambda v: v.update(command='start'),
            lambda v: v['descriptor'].update(version=1), lambda v: v['descriptor']['http'].update(port=9082),
            lambda v: v['descriptor']['http'].update(root='/var/lib/../foreign'),
            lambda v: v.update(debug_subjects=[]), lambda v: v.update(debug_subjects=['not-a-uuid']),
            lambda v: v.update(debug_subjects=v['debug_subjects'] * 2),
            lambda v: v.update(preparation_sha256='branch-name')]
        for mutate in mutations:
            value = deepcopy(self.target.value); mutate(value)
            with self.assertRaises(Exception): DevTarget(value)

    def test_main_and_dev_renderings_have_distinct_credentials_and_environments(self):
        main = self.foundation.files(991); dev = self.foundation.dev.files(992)
        self.assertIn(b'Listen 127.0.0.1:9082', main[self.foundation.root / 'apache.conf'])
        self.assertIn(b'Listen 127.0.0.1:9081', dev[self.foundation.dev.root / 'apache.conf'])
        config = json.loads(dev[self.foundation.dev.root / 'main.json'])
        self.assertEqual(config['environment'], 'dev-bastien'); self.assertNotIn('canonical_distribution', config)
        self.assertEqual(set(config['gateway_keys']), {self.foundation.dev.identity['kid']})
        self.assertNotIn(self.foundation.identity['kid'], config['gateway_keys'])
        self.assertEqual(json.loads(main[self.foundation.root / 'main.json'])['canonical_debug_subjects'], self.target.value['debug_subjects'])
        for files in (main, dev): self.assertFalse(any(b'PRIVATE KEY' in raw for raw in files.values()))

    def test_reused_key_and_disabled_dev_identity_cannot_create_pair(self):
        with self.assertRaises(InstallerError):
            FoundationRuntime(self.foundation.activation, self.foundation.identity, public_origin=self.identity['public_origin'],
                dev={'target': self.target.value, 'identity': public_identity('dev', self.foundation.identity['public_jwk'])})
        with self.assertRaises(InstallerError): GatewayServiceProfile(self.foundation, {**self.identity, 'dev_enabled': False}, self.keys.root)

    def test_gateway_v2_binding_roundtrip_preserves_fcm_and_both_private_keys(self):
        store = FcmCredentials(self.keys.root.parent / 'fcm')
        selection = {'version': 1, 'gateway_plan_sha256': 'd' * 64, 'project_id': 'hestia-test'}
        # Use the same public FCM binding shape as the independently qualified import.
        receipt = {'version': 1, 'profile_sha256': digest(selection), 'project_id': 'hestia-test',
                   'credential_sha256': 'e' * 64, 'public_key_sha256': 'f' * 64}
        profile = self.profile(public_binding(selection, receipt))
        binding = profile.binding(); self.assertEqual(binding['version'], 2)
        restored = GatewayServiceProfile.from_binding(self.foundation, binding)
        self.assertEqual(restored.binding(), binding); self.assertEqual(restored.unit_bytes(), profile.unit_bytes())
        config = restored.configuration(); self.assertEqual(config['contexts']['dev']['endpoint'], 'http://127.0.0.1:9081')
        self.assertNotEqual(config['contexts']['dev']['kid'], config['contexts']['main']['kid'])
        unit = restored.unit_bytes()
        for key in ('main-key:', 'dev-key:', 'project-push:'): self.assertIn(('LoadCredential=' + key).encode(), unit)
        self.assertIn(b'--check-push-credential', unit); self.assertEqual(config['contexts']['mode'], 'public-contexts-distribution')

    def test_tampered_paired_binding_cannot_be_reconstructed(self):
        binding = self.profile().binding()
        for mutate in (lambda b: b.update(version=1), lambda b: b['dev']['target']['debug_subjects'].append('22222222-2222-4222-8222-222222222222'),
                       lambda b: b.update(configuration_sha256='f' * 64), lambda b: b['dev']['identity'].update(kid='forged')):
            changed = deepcopy(binding); mutate(changed)
            with self.assertRaises(Exception): GatewayServiceProfile.from_binding(self.foundation, changed)

    def test_real_dev_assertion_uses_dev_key_and_dev_audience_without_mutating_keys(self):
        before = {p.name: p.read_bytes() for p in self.keys.root.glob('*.pem')}
        body, _, token = probe.assertion(self.keys, self.foundation.dev.identity, environment='dev')
        header, claims, _ = [base64.urlsafe_b64decode(v + '=' * (-len(v) % 4)) for v in token.split('.')]
        self.assertEqual(json.loads(header)['kid'], self.foundation.dev.identity['kid'])
        self.assertEqual(json.loads(claims)['aud'], 'hestia-internal-mobile:dev-bastien')
        self.assertEqual(body['environment'], 'dev-bastien')
        with self.assertRaises(InstallerError): probe.assertion(self.keys, self.foundation.identity, environment='dev')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.keys.root.glob('*.pem')})

    def test_cross_probe_requires_both_directions_and_replay_rejection(self):
        calls = []
        def request(raw, identifier, token, *, port):
            calls.append(port)
            if len(calls) == 1:
                body = json.loads(raw)
                return 200, {'request_id': identifier, 'data': {**body, 'mobile_enabled': False,
                    'account_active': False, 'device_active': False, 'environment_allowed': False}}
            return 401, {'error': {'code': 'authentication_failed'}}
        with patch.object(probe, 'request', side_effect=request):
            self.assertEqual(probe.check_dev(self.keys, self.foundation.dev.identity, self.foundation.identity), probe.DEV_RESULT)
        self.assertEqual(calls, [9081, 9081, 9081, 9081, 9082])

    def test_registration_state_is_metadata_only_and_detects_tampered_descriptor(self):
        owner = Mock(); owner.gateway.root = self.root / 'gateway'
        registration = DevRegistration(owner)
        with patch('subprocess.run', side_effect=AssertionError('GET effect')):
            self.assertEqual(registration.state(), {'target': None, 'confirmation': None})
            self.assertFalse(registration.root.exists())
            registration._write('target.json', self.target.value)
            self.assertEqual(registration.state()['confirmation'], digest(self.target.value))
        path = registration.root / 'target.json'; path.chmod(0o644)
        with self.assertRaises(Exception): registration.state()

    def test_native_configuration_drift_is_not_adopted(self):
        with patch.object(DevManagedProfile, 'inspect', return_value={}), patch.object(self.target.activation, 'configuration') as native:
            with self.assertRaises(InstallerError): self.target.inspect()
            native.assert_not_called()


if __name__ == '__main__': unittest.main()
