"""Closed external storage profile and independent release receipt boundaries."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from installer import finalization as f, http_runtime as h, storage_inventory as s
from installer import web_releases as r
from installer import php_transport as p
from test_storage_inventory import facts


class BusinessStorageTests(unittest.TestCase):
    def setUp(self):
        self.spec = h.RuntimeSpec('a'*32, Path('/var/lib/hestia-data-test'), Path('/srv/hestia-test'),
            'hestia-test', 'hestia.test', 8123, '8.4', external_uploads=True,
            maintenance_directory=Path('/var/lib/hestia-config/slot/maintenance'))
        self.runtime = h.HttpRuntime(self.spec)
        self.account = SimpleNamespace(pw_uid=991, pw_gid=991)

    def test_profile_requires_explicit_shared_gate_outside_code_and_data(self):
        for changes in ({'external_uploads': 1}, {'maintenance_directory': None},
            {'external_uploads': False}, {'php_family': '8.2'},
            {'maintenance_directory': Path('/etc/slot/maintenance')},
            {'maintenance_directory': self.spec.root / 'maintenance'},
            {'maintenance_directory': self.spec.webroot / 'maintenance'},
            {'maintenance_directory': Path('/var/lib/slot/other')},
            {'maintenance_directory': Path('/var/lib/slot/../maintenance')}):
            with self.subTest(changes=changes), self.assertRaises(h.HttpRuntimeError): replace(self.spec, **changes)

    def test_legacy_and_external_writes_have_distinct_exact_resources(self):
        operation = h.HttpRuntimeOperation(self.runtime)
        self.assertEqual([x.target for x in operation.spec.resources if x.name == 'maintenance'],
                         [str(self.spec.maintenance_directory)])
        dirs = self.runtime._directories(self.account)
        self.assertEqual(dirs[self.spec.root / 'data/uploads'], (991, 991, 0o700))
        self.assertNotIn(self.spec.webroot / 'uploads', dirs)
        legacy = h.HttpRuntime(replace(self.spec, external_uploads=False, maintenance_directory=None))
        self.assertNotIn('maintenance', [x.name for x in h.HttpRuntimeOperation(legacy).spec.resources])
        self.assertNotIn(self.spec.root / 'data/uploads', legacy._directories(self.account))

    def test_source_receipt_of_other_release_is_never_accepted(self):
        receipt = {'version': 1, 'state': 'WEB_FRESH_FINALIZED', 'source_commit': r.LEGACY_COMMIT,
            'runtime_sha256': r.get_release(r.LEGACY_COMMIT).runtime_sha256,
            'seal_sha256': f._sha(b'x'), 'lock_sha256': f._sha(b'x'), 'pointer_sha256': f._sha(b'x')}
        with patch.object(f, '_json_read', return_value=receipt), patch.object(f, '_read', return_value=b'x') as read:
            self.assertEqual(f._completed(1, 2, 3, 991), receipt)
            read.reset_mock()
            with self.assertRaisesRegex(f.FinalizationError, 'RECEIPT_REQUIRED'):
                f._completed(1, 2, 3, 991, commit=r.STORAGE_COMMIT)
            read.assert_not_called()
        for commit in ('main', r.STORAGE_COMMIT[:8], 'f'*40, None, True):
            with self.assertRaises(ValueError): r.get_release(commit)

    def test_inventory_requires_observed_external_setting_and_keeps_historical_tree(self):
        value = facts()
        inventory = s.StorageInventory(Path('/var/lib/source'), repository=p.WEB_REPOSITORY, commit=r.STORAGE_COMMIT)
        with patch.object(s, '_verify_source'):
            with self.assertRaises(s.StorageInventoryError): inventory.inspect(value)
            value.environment['HESTIA_UPLOAD_STORAGE'] = '/var/lib/hestia-data-test/data/uploads'
            value.app_config['security.ged_legacy_roots'] = 'archive'
            result = inventory.inspect(value)
            scopes = {item['role']: item for item in result.private_manifest()['scopes']}
            self.assertEqual(scopes['uploads']['path'], '/srv/hestia/uploads')
            self.assertEqual(scopes['uploads_effective']['path'], value.environment['HESTIA_UPLOAD_STORAGE'])
            self.assertEqual(scopes['ged_legacy_0']['path'], value.environment['HESTIA_UPLOAD_STORAGE'] + '/ged_legacy/archive')
            self.assertFalse(result.report()['storage_inventory_complete'])
            self.assertEqual(result.report()['producer_groups'], 9)

    def test_inventory_rejects_code_overlap_and_relative_external_root(self):
        value = facts()
        inventory = s.StorageInventory(Path('/var/lib/source'), repository=p.WEB_REPOSITORY, commit=r.STORAGE_COMMIT)
        with patch.object(s, '_verify_source'):
            for root in ('relative', '/srv', '/srv/hestia', '/srv/hestia/uploads', '/'):
                value.environment['HESTIA_UPLOAD_STORAGE'] = root
                with self.subTest(root=root), self.assertRaises(s.StorageInventoryError): inventory.inspect(value)


if __name__ == '__main__': unittest.main()
