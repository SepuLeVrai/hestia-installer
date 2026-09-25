"""Filesystem orchestration tests with explicitly stubbed SQL; live proof is separate."""
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from installer import backup_files as files
from installer import coordinated_backup as c
from installer import finalization as f
from installer import maintenance as m
from installer import php_transport as p
from installer import upgrade_backup as b
from test_upgrade_backup import BackupFilesystemTests


class CoordinatedFilesystemTests(BackupFilesystemTests):
    def setUp(self):
        super().setUp()
        instance = json.loads((self.directory / 'seal.json').read_bytes())['instance']
        self.scope = m.MaintenanceScope(self.directory / 'maintenance', self.web.pw_gid, instance)
        self.scope.create(confirmed=True)
        self.lease = self.scope.acquire(confirmed=True)
        self.addCleanup(self.lease.close)
        self.data = self.root / 'external-data'
        self.data.mkdir(mode=0o700)
        os.chown(self.data, self.web.pw_uid, self.web.pw_gid)
        self.document = self.data / 'private document.pdf'
        self.document.write_bytes(b'private-data\x00\xff')
        self.document.chmod(0o600)
        os.chown(self.document, self.web.pw_uid, self.web.pw_gid)
        self.inventory = files.DataInventory((('registered', self.data),), self.web.pw_uid, self.web.pw_gid)
        self.coordinator = c.CoordinatedBackup(self.runtime, self.webroot, repository=p.WEB_REPOSITORY, commit=f.WEB_COMMIT)

    def coordinated(self, **kwargs):
        values = dict(config_root=self.output, backup_root=self.backups, inventory=self.inventory,
                      maintenance=self.lease, confirmed=True, allow_global_read_lock=True)
        values.update(kwargs)
        return self.coordinator.create_and_verify(self.request, self.authority, **values).report()

    def incomplete(self, result):
        self.assertEqual(result['state'], 'COORDINATED_BACKUP_INCOMPLETE', result)
        slot = self.backups / result['backup_id']
        self.assertTrue((slot / 'attempt.json').is_file())
        self.assertFalse((slot / 'verified.json').exists())
        self.assertFalse(result['apply_allowed'])
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_coordinated_actual_files_with_stubbed_sql_have_bound_common_receipt(self):
        result = self.coordinated()
        self.assertEqual(result['state'], 'COORDINATED_BACKUP_RESTORE_VERIFIED', result)
        slot = self.backups / result['backup_id']
        raw = (slot / 'coordinated.json').read_bytes()
        manifest = json.loads(raw)
        self.assertEqual(f._sha(raw), result['manifest_sha256'])
        self.assertEqual(manifest['instance'], self.scope.instance)
        self.assertEqual(manifest['lease_id'], self.lease.lease_id)
        self.assertEqual(json.loads((slot / 'verified.json').read_bytes()), result)
        self.assertEqual(self.transport.call_count, 2)
        self.assertEqual(result['data_files'], 1)
        for name in ('complete_web_backup', 'storage_inventory_complete', 'system_wiring_verified',
                     'restore_to_original_allowed', 'apply_allowed', 'rollback_verified', 'application_installed'):
            self.assertIs(result[name], False)
        for value in (self.authority._password, str(self.data), self.document.name, 'private-data'):
            self.assertNotIn(value, json.dumps(result))

    def test_coordinated_missing_consents_instance_or_identity_rejected_before_slot(self):
        for key in ('confirmed', 'allow_global_read_lock'):
            for value in (False, 1, 'true', None):
                with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_CONSENT_REQUIRED'):
                    self.coordinated(**{key: value})
        other = m.MaintenanceScope(self.root / 'other-maintenance', self.web.pw_gid, '9' * 32)
        other.create(confirmed=True)
        with other.acquire(confirmed=True) as lease:
            with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_INSTANCE_MISMATCH'):
                self.coordinated(maintenance=lease)
        wrong = files.DataInventory(self.inventory.roots, self.web.pw_uid + 10, self.web.pw_gid)
        with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_IDENTITY_MISMATCH'):
            self.coordinated(inventory=wrong)
        self.assertEqual(list(self.backups.iterdir()), [])
        self.transport.assert_not_called()

    def test_coordinated_overlapping_and_internal_mutable_roots_refused(self):
        for root in (self.webroot, self.directory, self.runtime.state_root, self.runtime.run_root):
            inventory = files.DataInventory((('registered', root),), self.web.pw_uid, self.web.pw_gid)
            with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_EXTERNAL_ROOTS_REQUIRED'):
                self.coordinated(inventory=inventory)
        self.assertEqual(list(self.backups.iterdir()), [])
        self.transport.assert_not_called()

    def test_coordinated_file_drift_after_sql_component_cannot_be_certified(self):
        original = b.UpgradeBackup.create_and_verify
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.document.write_bytes(b'concurrent change')
            return result
        with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=changed, autospec=True):
            result = self.coordinated()
        self.incomplete(result)
        self.assertEqual(result['code'], 'COORDINATED_FILES_CHANGED')

    def test_coordinated_private_envelope_drift_after_sql_is_not_certified(self):
        original = b.UpgradeBackup.create_and_verify
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            path = self.directory / 'assistant.json'
            value = json.loads(path.read_bytes())
            value['openai_api_key'] = 'changed-private-key'
            path.write_bytes(p._json(value))
            return result
        with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=changed, autospec=True):
            result = self.coordinated()
        self.incomplete(result)
        self.assertEqual(result['code'], 'COORDINATED_ENVELOPE_CHANGED')

    def test_coordinated_sql_restore_failure_keeps_only_component_proofs(self):
        self.restore.side_effect = b.UpgradeBackupError('BACKUP_RESTORE_MISMATCH')
        result = self.coordinated()
        self.incomplete(result)
        self.assertEqual(result['code'], 'COORDINATED_SQL_BACKUP_FAILED')
        self.assertEqual(len(list((self.backups / result['backup_id'] / 'data').rglob('verified.json'))), 1)

    def test_coordinated_recheck_compares_digest_not_counts_only(self):
        original = self.capture
        # fdopen.name is a descriptor, so identify the recheck via its actual private path.
        def routed(command, wire, stage, output, *args, **kwargs):
            if os.readlink('/proc/self/fd/' + str(output.fileno())).endswith('/sql-recheck.ndjson'):
                request = json.loads(wire)
                raw = p._json({'type':'complete','request_id':request['request_id'],'tables':129,
                              'rows':'1','logical_sha256':'e'*64}) + b'\n'
                output.write(raw)
                return 0, f._sha(raw), len(raw)
            return original(command, wire, stage, output, *args, **kwargs)
        self.transport.side_effect = routed
        result = self.coordinated()
        self.incomplete(result)
        self.assertEqual(result['code'], 'COORDINATED_SQL_CHANGED')

    def test_coordinated_interruption_and_unexpected_diagnostics_do_not_publish(self):
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_INTERRUPTED'):
            self.coordinated(cancel=event)
        self.assertEqual(list(self.backups.iterdir()), [])
        with patch.object(c, '_recheck', side_effect=RuntimeError(self.authority._password)):
            result = self.coordinated()
        self.incomplete(result)
        self.assertEqual(result['code'], 'COORDINATED_OPERATION_UNAVAILABLE')
        self.assertNotIn(self.authority._password, json.dumps(result))

    def test_coordinated_lost_lease_never_publishes_common_success(self):
        original = b.UpgradeBackup.create_and_verify
        def close(*args, **kwargs):
            result = original(*args, **kwargs)
            self.lease.close()
            return result
        with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=close, autospec=True):
            result = self.coordinated()
        self.incomplete(result)
        self.assertEqual(result['code'], 'COORDINATED_MAINTENANCE_REQUIRED')

    def test_coordinated_corrupt_saved_data_fails_after_good_sql_restore(self):
        original = b.UpgradeBackup.create_and_verify
        def corrupt(*args, **kwargs):
            result = original(*args, **kwargs)
            for blob in self.backups.glob('*/data/*/blobs/*.bin'):
                blob.write_bytes(b'damaged saved data')
            return result
        with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=corrupt, autospec=True):
            result = self.coordinated()
        self.incomplete(result)
        self.assertEqual(result['code'], 'COORDINATED_OPERATION_UNAVAILABLE')


def load_tests(loader, tests, pattern):
    # Reuse fixture mechanics, not duplicate its historical cases. They remain
    # mandatory and are executed by test_upgrade_backup in the same core suite.
    names = [n for n in loader.getTestCaseNames(CoordinatedFilesystemTests) if n.startswith('test_coordinated_')]
    return unittest.TestSuite(CoordinatedFilesystemTests(n) for n in names)
