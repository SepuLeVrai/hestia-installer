"""Version policy, profile preservation and a plan that cannot touch services."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import gateway_transition as policy
from installer.gateway_service_profile import GatewayServiceProfile
from installer.gateway_transition_plan import GatewayTransitionPlan
from installer.gateway_release import release
from installer.model import InstallerError, canonical_bytes
from installer.transaction import StateJournal
from installer.service import POST_ROUTES
import test_gateway_service as service_fixture
import test_gateway_fcm as fcm_fixture
from dev_fixture import target_fixture


class GatewayTransitionTests(unittest.TestCase):
    def profile(self, commit=policy.LEGACY_COMMIT, push=None):
        old = service_fixture.GatewayServiceProfileTests().profile()
        return GatewayServiceProfile(old.foundation, old.identity, old.key_directory,
                                     release_commit=commit, push=push)

    def assess(self, source=None, target=policy.FCM_COMMIT, direction='upgrade'):
        return policy.assess(source or self.profile(), target_commit=target, direction=direction)

    def push(self):
        fixture = fcm_fixture.FcmServiceProfileTests(); fixture.setUp(); return fixture.push

    def test_upgrade_preserves_configuration_unit_account_and_every_identity(self):
        source = self.profile(); value = self.assess(source).report()
        self.assertTrue(value['configuration_compatible']); self.assertEqual(value['blockers'], [])
        target = GatewayServiceProfile.from_binding(source.foundation, value['target'])
        self.assertEqual(source.configuration(), target.configuration())
        self.assertEqual(source.unit_bytes(), target.unit_bytes())
        for key in set(value['source']) - {'release'}:
            self.assertEqual(value['source'][key], value['target'][key], key)
        self.assertNotEqual(value['source']['release']['binary_sha256'], value['target']['release']['binary_sha256'])

    def test_non_fcm_rollback_keeps_database_sessions_quotas_and_uuid(self):
        value = self.assess(self.profile(policy.FCM_COMMIT), policy.LEGACY_COMMIT, 'rollback').report()
        self.assertTrue(value['configuration_compatible']); self.assertEqual(value['sqlite']['schema'], 6)
        self.assertFalse(value['sqlite']['restore_snapshot']); self.assertFalse(value['sqlite']['reset_technical_state'])
        self.assertEqual(value['sqlite']['migrations_to_apply'], [])
        for item in ('sqlite_data', 'installation_uuid', 'revocations', 'sessions', 'quotas'):
            self.assertIn(item, value['preserve'])

    def test_fcm_rollback_is_refused_without_removing_or_relocating_secret(self):
        source = self.profile(policy.FCM_COMMIT, self.push()); original = source.binding()
        value = self.assess(source, policy.LEGACY_COMMIT, 'rollback').report()
        self.assertEqual(value['blockers'], ['TARGET_FCM_PROFILE_UNSUPPORTED'])
        self.assertIsNone(value['target']); self.assertFalse(value['configuration_compatible'])
        self.assertEqual(value['source'], original); self.assertEqual(source.binding(), original)
        self.assertIn('/run/credentials/', source.configuration()['project_push_credential'])

    def test_both_identical_versions_refuse_fake_upgrade_or_rollback(self):
        for pin in policy.COMMITS:
            for direction in ('upgrade', 'rollback'):
                value = self.assess(self.profile(pin), pin, direction).report()
                self.assertIn('NO_VERSION_TRANSITION', value['blockers']); self.assertIsNone(value['target'])

    def test_direction_is_bound_and_cannot_relabel_downgrade_as_upgrade(self):
        for source, target, direction in ((policy.FCM_COMMIT, policy.LEGACY_COMMIT, 'upgrade'),
                                         (policy.LEGACY_COMMIT, policy.FCM_COMMIT, 'rollback')):
            self.assertEqual(self.assess(self.profile(source), target, direction).report()['blockers'],
                             ['TRANSITION_DIRECTION_MISMATCH'])

    def test_branch_tag_version_unknown_commit_and_non_string_are_rejected(self):
        class Text(str): pass
        for value in ('main', 'latest', '0.12.3-installer.rc1', policy.FCM_COMMIT[:7], '0'*40,
                      None, True, {}, [], Text(policy.FCM_COMMIT)):
            with self.subTest(value=value), self.assertRaises(InstallerError): self.assess(target=value)

    def test_unknown_direction_and_host_commands_are_rejected(self):
        for value in ('restore', 'restart', 'reset-technical-state', '', None, True, []):
            with self.subTest(value=value), self.assertRaises(InstallerError): self.assess(direction=value)

    def test_future_release_catalogue_does_not_expand_transition_policy(self):
        source = self.profile(); source.selected_release['commit'] = 'd'*40
        with patch.object(policy, 'release', return_value=source.selected_release), self.assertRaises(InstallerError):
            self.assess(source)

    def test_modified_release_metadata_never_inherits_known_pin_compatibility(self):
        for field, value in (('sqlite_schema', 7), ('binary_sha256', 'd'*64), ('version', 'future')):
            source = self.profile(); source.selected_release[field] = value
            with self.subTest(field=field), self.assertRaises(InstallerError): self.assess(source)

    def test_all_known_pairs_keep_execution_and_live_proof_flags_false(self):
        for source in policy.COMMITS:
            for target in policy.COMMITS:
                for direction in ('upgrade', 'rollback'):
                    value = self.assess(self.profile(source), target, direction).report()
                    for name in ('apply_allowed', 'source_host_verified', 'target_package_verified',
                                 'backup_verified', 'maintenance_held', 'rollback_verified',
                                 'restore_to_original_allowed', 'phase6_complete'):
                        self.assertIs(value[name], False)
                    self.assertIn('BOOT_BINDING_REQUALIFIED', value['required_before_effect'])

    def test_report_is_immutable_and_digest_binds_direction_profile_and_target(self):
        result = self.assess(); original = result.report(); changed = result.report()
        changed['blockers'].append('changed'); changed['sqlite']['migration_blobs'].clear()
        self.assertEqual(result.report(), original)
        self.assertEqual(result.sha256, self.assess().sha256)
        self.assertNotEqual(result.sha256, self.assess(direction='rollback').sha256)
        self.assertNotEqual(result.sha256, self.assess(target=policy.LEGACY_COMMIT).sha256)
        with self.assertRaises(FrozenInstanceError): result._raw = b'{}'

    def test_planning_never_opens_keys_packages_network_sql_or_subprocesses(self):
        source = self.profile()
        with patch('builtins.open', side_effect=AssertionError('file read')), \
             patch('os.open', side_effect=AssertionError('native file')), \
             patch('subprocess.run', side_effect=AssertionError('command')), \
             patch('socket.socket', side_effect=AssertionError('network')):
            self.assertTrue(self.assess(source).report()['configuration_compatible'])

    def test_dev_pair_is_preserved_in_both_directions_without_cloning(self):
        with TemporaryDirectory() as directory:
            _, _, identity, keys, foundation = target_fixture(Path(directory))
            for source, target, direction in ((policy.LEGACY_COMMIT, policy.FCM_COMMIT, 'upgrade'),
                                             (policy.FCM_COMMIT, policy.LEGACY_COMMIT, 'rollback')):
                profile = GatewayServiceProfile(foundation, identity, keys.root, release_commit=source)
                value = self.assess(profile, target, direction).report()
                self.assertTrue(value['configuration_compatible'])
                self.assertEqual(value['source']['dev'], value['target']['dev'])
                self.assertEqual(value['source']['main'], value['target']['main'])
                self.assertNotEqual(value['target']['main']['thumbprint'], value['target']['dev']['identity']['thumbprint'])

    def test_fcm_with_dev_has_the_same_closed_rollback_refusal(self):
        with TemporaryDirectory() as directory:
            _, _, identity, keys, foundation = target_fixture(Path(directory))
            source = GatewayServiceProfile(foundation, identity, keys.root,
                release_commit=policy.FCM_COMMIT, push=self.push())
            value = self.assess(source, policy.LEGACY_COMMIT, 'rollback').report()
            self.assertEqual(value['blockers'], ['TARGET_FCM_PROFILE_UNSUPPORTED'])
            self.assertEqual(value['source']['dev'], source.binding()['dev'])


class GatewayTransitionPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parent = SimpleNamespace(journal=StateJournal(self.root/'parent/state.json'), secrets=Mock())
        with self.parent.journal.locked(create=True): pass
        self.source = service_fixture.GatewayServiceProfileTests().profile()
        self.document = {'state': 'DONE', 'plan_sha256': 'b'*64}
        self.runtime = SimpleNamespace(foundation=self.source.foundation)
        self.service = SimpleNamespace(parent=self.parent, gateway=SimpleNamespace(root=self.root/'gateway'),
            engine=Mock(return_value=(SimpleNamespace(report=lambda: self.document), self.runtime)),
            profile=Mock(return_value={'parents': {'web': 'c'*64}, 'binding': self.source.binding()}))
        self.control = GatewayTransitionPlan(self.service)
        self.payload = {'source_plan_sha256': 'b'*64, 'target_commit': policy.FCM_COMMIT, 'direction': 'upgrade'}

    def plan(self): return self.control.execute('plan', self.payload)

    def test_plan_is_separate_repeatable_and_preserves_parent_bytes(self):
        before = {p:p.read_bytes() for p in (self.root/'parent').rglob('*') if p.is_file()}
        result = self.plan(); self.assertEqual(result, self.plan())
        self.assertEqual(result['state'], 'COMPATIBILITY_ONLY'); self.assertFalse(result['apply_allowed'])
        for path, raw in before.items(): self.assertEqual(path.read_bytes(), raw)
        self.assertEqual((self.control.root/'profile.json').stat().st_mode & 0o777, 0o600)

    def test_wrong_parent_and_unfinished_service_refuse_before_plan_write(self):
        self.payload['source_plan_sha256'] = 'd'*64
        with self.assertRaises(InstallerError): self.plan()
        self.payload['source_plan_sha256'] = 'b'*64; self.document['state'] = 'RUNNING'
        with self.assertRaises(InstallerError): self.plan()
        self.assertFalse(self.control.root.exists())

    def test_apply_resume_retry_restore_and_arbitrary_fields_have_no_effect_entry(self):
        for action in ('apply', 'resume', 'retry', 'rollback', 'restore', 'check'):
            with self.assertRaises(InstallerError): self.control.execute(action, {'confirm': True})
        for name in ('confirm', 'command', 'push', 'source', 'dev', 'restore_to_original_allowed'):
            with self.assertRaises(InstallerError): self.control.execute('plan', {**self.payload, name: True})
        self.service.engine.assert_not_called(); self.assertFalse(self.control.root.exists())
        self.assertEqual([p for p in POST_ROUTES if '/gateway/transition/' in p], ['/api/gateway/transition/plan'])

    def test_saved_plan_refuses_parent_profile_or_selection_drift(self):
        original = self.plan(); raw = (self.control.root/'profile.json').read_bytes()
        self.service.profile.return_value['parents']['web'] = 'd'*64
        with self.assertRaises(InstallerError): self.plan()
        self.service.profile.return_value['parents']['web'] = 'c'*64
        self.payload['direction'] = 'rollback'
        with self.assertRaises(InstallerError): self.plan()
        self.assertEqual((self.control.root/'profile.json').read_bytes(), raw)
        self.assertEqual(self.control.state(), original)

    def test_get_and_restart_read_metadata_without_live_inspection_or_replay(self):
        result = self.plan(); self.service.engine.side_effect = AssertionError('GET service inspection')
        self.service.profile.side_effect = AssertionError('GET profile reconstruction')
        with patch('subprocess.run', side_effect=AssertionError('GET command')):
            self.assertEqual(self.control.state(), result)
            self.assertEqual(GatewayTransitionPlan(self.service).state(), result)

    def test_global_lock_serializes_plan_and_refuses_busy_before_write(self):
        with self.parent.journal.locked(), self.assertRaisesRegex(InstallerError, 'BUSY'): self.plan()
        self.assertFalse(self.control.root.exists())

    def test_symlink_or_unknown_profile_fields_are_refused(self):
        self.plan(); p = self.control.root/'profile.json'; raw = p.read_bytes()
        value = self.control.profile(); value['command'] = 'restart'; p.write_bytes(canonical_bytes(value))
        with self.assertRaises(InstallerError): self.control.state()
        p.unlink(); target = self.root/'external.json'; target.write_bytes(raw); p.symlink_to(target)
        with self.assertRaises(Exception): self.control.state()


class GatewayTransitionHTTPTests(unittest.TestCase):
    setUp = service_fixture.GatewayServiceHTTPTests.setUp
    tearDown = service_fixture.GatewayServiceHTTPTests.tearDown
    _connection = service_fixture.GatewayServiceHTTPTests._connection
    _unlock = service_fixture.GatewayServiceHTTPTests._unlock
    login = service_fixture.GatewayServiceHTTPTests.login
    request = service_fixture.GatewayServiceHTTPTests.request

    def test_plan_requires_session_csrf_origin_and_closed_payload(self):
        route = '/api/gateway/transition/plan'
        self.assertEqual(self.request('POST', route, {})[0], 401)
        self.login()
        for headers in ({'X-Hestia-CSRF': ''}, {'Origin': 'https://evil.invalid'}):
            self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
        self.assertFalse(self.service.gateway_transition.root.exists())
