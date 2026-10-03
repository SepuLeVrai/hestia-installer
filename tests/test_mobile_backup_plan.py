"""Consent, lease ownership and lost-response recovery; no native host effects."""
from copy import deepcopy
from unittest.mock import Mock, MagicMock, patch
import unittest

from installer import mobile_backup_plan as plan
from installer.mobile_activation_plan import digest
from installer.model import InstallerError
from installer.service import POST_ROUTES
import test_mobile_activation_plan as fixture


class MobileBackupPlanTests(unittest.TestCase):
    def setUp(self):
        fixture.MobileActivationPlanTests.setUp(self)
        self.control = self.service.mobile_backup
        self.enterContext(patch.object(plan, 'FreshProfile', return_value=self.fresh)).from_draft.return_value = self.fresh
        self.scope = Mock()
        self.scope.observe.return_value = {'state': 'SERVING', 'instance': self.fresh.instance}
        self.held = MagicMock(lease_id='a' * 32, assert_held=Mock())
        self.held.__enter__.return_value = self.held
        def acquire(**kwargs):
            self.scope.observe.return_value = {'state': 'MAINTENANCE_REQUIRED', 'instance': self.fresh.instance, 'lease_id': 'a' * 32}
            return self.held
        self.scope.acquire.side_effect = acquire
        http = Mock()
        http._inspect_configuration.return_value = (None, None, None, None)
        http._scope.return_value = self.scope
        self.enterContext(patch.object(self.fresh, 'http', return_value=http))
        self.enterContext(patch.object(plan, 'SessionCleaner', return_value=Mock()))
        self.operation = Mock()
        self.operation.create_and_verify.side_effect = self.completed
        self.factory = self.enterContext(patch.object(plan.native, 'ProvisionedBackup', return_value=self.operation))

    write = staticmethod(fixture.MobileActivationPlanTests.write)

    def completed(self, *args, **kwargs):
        lease = self.control._read('lease.json'); self.native_root = self.backups / ('gateway-' + lease['lease_id'])
        snapshot = {'lease_id': lease['lease_id']}; manifest = {'fixture': 'isolated-native-effect'}
        web = {'state': 'PROVISIONED_BACKUP_RESTORE_VERIFIED', 'backup_id': 'b' * 32, 'manifest_sha256': digest(manifest)}
        report = {**web, 'state': 'MOBILE_BACKUP_RESTORE_VERIFIED', 'source_commit': plan.STORAGE_COMMIT,
                  'gateway_snapshot_id': self.native_root.name, 'gateway_snapshot_sha256': digest(snapshot)}
        report.update({key: True for key in ('database_restoration_verified', 'registered_data_restoration_verified',
            'gateway_sqlite_restoration_verified', 'gateway_editor_cache_restoration_verified',
            'gateway_installation_uuid_preserved', 'maintenance_required')})
        report.update({key: False for key in ('activity_resumed', 'restore_to_original_allowed', 'public_mobile_delivered', 'boot_delivered')})
        for name, value in {'snapshot.json': snapshot, 'verified.json': report,
                            'composed.json': {'gateway_snapshot': snapshot, 'web': web, 'receipt': report}}.items():
            self.write(self.native_root / name, value)
        for name, value in {'verified.json': web, 'coordinated.json': manifest}.items():
            self.write(self.backups / web['backup_id'] / name, value)
        return Mock(report=Mock(return_value=report))

    def prepare(self): return self.service.execute('mobile-backup.plan', {'parents': self.parents})['mobile_backup']
    def request(self, action='apply', **extra):
        value = {'confirmation': self.control.state()['confirmation'], 'confirm': True,
                 'credentials': deepcopy(self.credentials), 'allow_global_read_lock': True, **extra}
        return self.service.execute('mobile-backup.' + action, value)['mobile_backup']

    def test_plan_and_reads_preserve_parents_and_never_observe_host(self):
        first = self.prepare(); self.assertEqual(self.prepare(), first)
        self.assertEqual(self.service.wizard_state()['mobile_backup'], first)
        self.assertEqual(self.service.report()['mobile_backup'], first)
        self.scope.observe.assert_not_called(); self.factory.assert_not_called()
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)

    def test_all_consents_and_closed_credentials_precede_approval(self):
        self.prepare()
        for extra in ({'confirm': 1}, {'confirmation': 'c' * 64}, {'allow_global_read_lock': 1},
                      {'credentials': {}}, {'credentials': {**self.credentials, 'authority_user': 'root'}},
                      {'backup_root': '/var/lib/foreign'}):
            with self.assertRaises(InstallerError): self.request(**extra)
        self.assertIsNone(self.control._read('approved.json')); self.scope.acquire.assert_not_called()

    def test_real_gate_identifier_is_saved_before_native_backup(self):
        self.prepare()
        def effect(*args, **kwargs):
            self.assertEqual(self.control._read('approved.json'), {'confirmation': self.control.state()['confirmation']})
            self.assertEqual(kwargs['recover_lease_id'], self.held.lease_id)
            self.assertEqual(kwargs['backup_root'], self.backups)
            self.held.__exit__.assert_called_once()
            self.assertEqual(args[0]['assistant'], {'action': 'preserve'})
            self.assertEqual(args[0]['secrets']['database_password'], self.credentials['database_password'])
            return self.completed()
        self.operation.create_and_verify.side_effect = effect
        self.assertEqual(self.request()['state'], 'DONE')

    def test_foreign_gate_and_nonempty_backup_root_refuse_before_approval(self):
        self.prepare(); self.scope.observe.return_value = {'state': 'MAINTENANCE_REQUIRED'}
        with self.assertRaises(InstallerError): self.request()
        self.scope.observe.return_value = {'state': 'SERVING'}
        (self.backups / 'foreign').mkdir()
        with self.assertRaises(InstallerError): self.request()
        self.assertIsNone(self.control._read('approved.json')); self.scope.acquire.assert_not_called()

    def test_interrupted_backup_requires_explicit_resume_of_same_lease(self):
        self.prepare(); self.operation.create_and_verify.side_effect = RuntimeError('private-secret')
        with self.assertRaisesRegex(InstallerError, '^MANUAL_ACTION_REQUIRED$'): self.request()
        self.assertEqual(self.control.state()['state'], 'RESUME_REQUIRED')
        with self.assertRaises(InstallerError): self.request()
        self.operation.create_and_verify.side_effect = self.completed
        self.assertEqual(self.request('resume')['state'], 'DONE'); self.scope.acquire.assert_called_once()

    def test_changed_or_reopened_gate_never_restarts_a_backup(self):
        self.prepare(); self.operation.create_and_verify.side_effect = RuntimeError('interrupted')
        with self.assertRaises(InstallerError): self.request()
        for observed in ({'state': 'SERVING'}, {'state': 'MAINTENANCE_REQUIRED', 'instance': self.fresh.instance, 'lease_id': 'f' * 32}):
            self.scope.observe.return_value = observed
            with self.assertRaises(InstallerError): self.request('resume')
        self.assertEqual(self.operation.create_and_verify.call_count, 1)

    def test_interruption_before_lease_receipt_does_not_adopt_closed_gate(self):
        self.prepare(); self.control._write('approved.json', {'confirmation': self.control.state()['confirmation']})
        self.scope.observe.return_value = {'state': 'MAINTENANCE_REQUIRED', 'instance': self.fresh.instance, 'lease_id': 'a' * 32}
        with self.assertRaises(InstallerError): self.request('resume')
        self.scope.acquire.assert_not_called(); self.factory.assert_not_called()

    def test_interruption_before_gate_can_resume_explicitly(self):
        self.prepare(); self.control._write('approved.json', {'confirmation': self.control.state()['confirmation']})
        self.assertEqual(self.request('resume')['state'], 'DONE')

    def test_lost_response_after_complete_receipt_is_done_without_replay(self):
        self.prepare()
        def lost(*args, **kwargs): self.completed(); raise RuntimeError('lost HTTPS response')
        self.operation.create_and_verify.side_effect = lost
        with self.assertRaises(InstallerError): self.request()
        self.assertEqual(self.control.state()['state'], 'DONE')
        before = self.scope.observe.call_count
        self.assertEqual(self.request('resume', credentials={})['state'], 'DONE')
        self.assertEqual(self.operation.create_and_verify.call_count, 1)
        self.assertEqual(self.scope.observe.call_count, before)

    def test_restart_and_poll_only_read_historical_receipts(self):
        self.prepare(); self.request()
        self.scope.observe.reset_mock(); self.factory.reset_mock()
        rebuilt = plan.MobileBackupPlan(self.service.mobile_activation)
        self.assertEqual(rebuilt.state()['state'], 'DONE'); self.assertTrue(rebuilt.state()['historical_only'])
        self.assertFalse(rebuilt.state()['phase6_complete']); self.assertFalse(rebuilt.state()['activity_resumed'])
        self.assertEqual(self.service.wizard_state()['mobile_backup'], rebuilt.state())
        self.scope.observe.assert_not_called(); self.factory.assert_not_called()

    def test_damaged_linked_or_foreign_receipts_fail_closed(self):
        self.prepare(); self.request(); path = self.native_root / 'snapshot.json'
        self.write(path, {'lease_id': 'f' * 32}); self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        path.unlink(); path.symlink_to(self.control.root / 'profile.json')
        self.assertEqual(self.control.state()['state'], 'UNAVAILABLE')
        with self.assertRaises(Exception): self.request('resume')
        self.assertEqual(self.operation.create_and_verify.call_count, 1)

    def test_parent_drift_refuses_recovery(self):
        self.prepare(); binding = self.control.activation.binding(self.service.engine.report()); binding['draft_sha256'] = 'f' * 64
        with patch.object(self.control.activation, 'binding', return_value=binding):
            with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.request()
        self.factory.assert_not_called()

    def test_secrets_and_raw_errors_never_enter_journals_or_report(self):
        self.prepare(); self.operation.create_and_verify.side_effect = RuntimeError('private-error')
        with self.assertRaises(InstallerError): self.request()
        public = str(self.service.report()); raw = b''.join(p.read_bytes() for p in self.control.root.iterdir())
        for secret in (*self.credentials.values(), 'private-error'):
            self.assertNotIn(secret, public); self.assertNotIn(secret.encode(), raw)
        for path in self.control.root.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_second_service_cannot_mutate_under_parent_lock(self):
        self.prepare()
        with self.service.engine.journal.locked(create=False):
            with self.assertRaisesRegex(InstallerError, 'BUSY'): self.request()
        self.factory.assert_not_called()

    def test_routes_have_no_retry_or_implicit_reopen(self):
        self.assertEqual({path for path in POST_ROUTES if path.startswith('/api/mobile/backup/')},
                         {'/api/mobile/backup/' + action for action in ('plan', 'apply', 'resume')})
        for action in ('retry', 'check', 'reopen'):
            with self.assertRaises(InstallerError): self.service.execute('mobile-backup.' + action, {})
