"""Closed requirements mapping; pinned real Web semantics are checked separately."""
import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from installer import finalization as f
from installer import php_transport as p
from installer import storage_inventory as s


def facts():
    return s.StorageFacts(Path('/srv/hestia'), Path('/etc/hestia/instance'),
        {name: '' for name in s.ENVIRONMENT}, {'HESTIA_IMPORT_STORAGE': None},
        {name: '' for name in s.APP_CONFIG},
        {'session.save_handler': 'files', 'session.save_path': '/var/lib/hestia/instance/sessions',
         'session.gc_maxlifetime': '43200', 'effective_sys_temp_dir': '/var/lib/hestia/instance/tmp',
         'upload_tmp_dir': '', 'error_log': 'syslog'})


class StorageInventoryTests(unittest.TestCase):
    def setUp(self):
        self.pin = patch.object(s, '_verify_source')
        self.verify = self.pin.start()
        self.addCleanup(self.pin.stop)
        self.inventory = s.StorageInventory(Path('/var/lib/exact-web'), repository=p.WEB_REPOSITORY, commit=f.WEB_COMMIT)
        self.facts = facts()

    def resolve(self, value=None):
        return self.inventory.inspect(value or self.facts)

    def scopes(self, value=None):
        return {x['role']: x for x in self.resolve(value).private_manifest()['scopes']}

    def test_required_default_roots_and_fallbacks_are_retained(self):
        roots = self.scopes()
        self.assertEqual(roots['uploads']['path'], '/srv/hestia/uploads')
        self.assertEqual(roots['imports_effective']['path'], '/srv/hestia/var/imports')
        self.assertEqual(roots['upload_temporary']['path'], roots['temporary']['path'])
        self.assertEqual(roots['reference_import_fallback']['covered_by'], ['temporary', 'upload_temporary'])
        self.assertEqual(roots['portability_temporary']['path'], '/var/lib/hestia/instance/tmp/hestia-portability')
        self.assertTrue(roots['pairing_temporary']['path'].startswith('/var/lib/hestia/instance/tmp/hestia-pair-'))
        self.assertIn('ai_system_configuration', roots)
        self.assertIn('ai_legacy_configuration', roots)

    def test_import_constant_takes_precedence_but_shadowed_data_is_retained(self):
        self.facts.environment['HESTIA_IMPORT_STORAGE'] = '/var/lib/from-env/'
        self.facts.constants['HESTIA_IMPORT_STORAGE'] = '/var/lib/from-constant/'
        roots = self.scopes()
        self.assertEqual(roots['imports_effective']['path'], '/var/lib/from-constant')
        self.assertEqual(roots['imports_environment']['path'], '/var/lib/from-env')
        self.assertEqual(roots['imports_default']['path'], '/srv/hestia/var/imports')

    def test_defined_empty_import_constant_does_not_fall_through_to_environment(self):
        self.facts.environment['HESTIA_IMPORT_STORAGE'] = '/var/lib/from-env'
        self.facts.constants['HESTIA_IMPORT_STORAGE'] = ''
        self.assertEqual(self.scopes()['imports_effective']['path'], '/srv/hestia/var/imports')
        self.facts.constants['HESTIA_IMPORT_STORAGE'] = None
        self.assertEqual(self.scopes()['imports_effective']['path'], '/var/lib/from-env')

    def test_database_mobile_path_overrides_environment_without_erasing_it(self):
        self.facts.environment['HESTIA_MOBILE_RELEASE_DIR'] = '/var/lib/releases-env'
        self.facts.app_config['HESTIA_MOBILE_RELEASE_DIR'] = ' /var/lib/releases-db '
        roots = self.scopes()
        self.assertEqual(roots['mobile_releases_effective']['path'], '/var/lib/releases-db')
        self.assertEqual(roots['mobile_releases_environment']['path'], '/var/lib/releases-env')
        self.facts.app_config['HESTIA_MOBILE_RELEASE_DIR'] = ' '
        self.assertEqual(self.scopes()['mobile_releases_effective']['path'], '/var/lib/releases-env')

    def test_explicit_unconfigured_mobile_does_not_guess_a_distribution_directory(self):
        self.assertNotIn('mobile_releases_effective', self.scopes())
        self.facts.environment['HESTIA_MOBILE_FOUNDATION_CONFIG'] = '/etc/hestia/mobile.json'
        self.assertEqual(self.scopes()['mobile_foundation_configuration']['kind'], 'file')
        self.assertEqual(self.resolve().report()['producer_groups'], 9)

    def test_mobile_distribution_cannot_be_inside_or_contain_webroot(self):
        for path in ('/srv/hestia', '/srv/hestia/releases', '/srv', '/'):
            self.facts.app_config['HESTIA_MOBILE_RELEASE_DIR'] = path
            with self.assertRaisesRegex(s.StorageInventoryError, 'STORAGE_MOBILE_PUBLIC_PATH_REJECTED'):
                self.resolve()

    def test_ged_paths_preserve_external_and_legacy_semantic_roles(self):
        self.facts.environment['HESTIA_GED_LEGACY_ROOTS'] = '/mnt/Archives privées, /var/lib/GED/'
        self.facts.app_config['security.ged_legacy_roots'] = 'first, second\\archive'
        roots = self.scopes()
        self.assertEqual(roots['ged_external_0']['path'], '/mnt/Archives privées')
        self.assertEqual(roots['ged_external_1']['path'], '/var/lib/GED')
        self.assertEqual(roots['ged_legacy_1']['path'], '/srv/hestia/uploads/ged_legacy/second/archive')
        self.assertEqual(roots['ged_legacy_1']['covered_by'], ['uploads'])

    def test_invalid_ged_entries_are_refused_instead_of_silently_omitted(self):
        for entry in ('/absolute', '../escape', 'a//b', 'a/./b', 'valid,,other', 'a,b,', 'a:other'):
            self.facts.app_config['security.ged_legacy_roots'] = entry
            with self.assertRaises(s.StorageInventoryError): self.resolve()
        self.facts.app_config['security.ged_legacy_roots'] = ''
        self.facts.environment['HESTIA_GED_LEGACY_ROOTS'] = '/valid,relative'
        with self.assertRaises(s.StorageInventoryError): self.resolve()

    def test_ged_root_count_is_bounded(self):
        self.facts.environment['HESTIA_GED_LEGACY_ROOTS'] = ','.join('/mnt/r' + str(i) for i in range(17))
        with self.assertRaisesRegex(s.StorageInventoryError, 'STORAGE_SCOPE_LIMIT'): self.resolve()

    def test_only_file_sessions_with_supported_flat_layout_are_accepted(self):
        for path in ('/var/lib/sessions', '0;/var/lib/sessions', '0;0600;/var/lib/sessions', '0;600;/var/lib/sessions'):
            self.facts.php['session.save_path'] = path
            self.assertEqual(self.scopes()['sessions']['path'], '/var/lib/sessions')
        for path in ('', '2;/var/lib/sessions', '1;0600;/var/lib/sessions', '0;0999;/var/lib/sessions', 'tcp://127.0.0.1'):
            self.facts.php['session.save_path'] = path
            with self.assertRaises(s.StorageInventoryError): self.resolve()
        self.facts.php['session.save_path'] = '/var/lib/sessions'
        self.facts.php['session.save_handler'] = 'redis'
        with self.assertRaisesRegex(s.StorageInventoryError, 'STORAGE_SESSION_HANDLER_UNSUPPORTED'): self.resolve()

    def test_shared_storage_and_short_session_retention_remain_blockers(self):
        self.facts.php.update({'session.save_path': '/var/lib/php/sessions', 'effective_sys_temp_dir': '/tmp',
                              'session.gc_maxlifetime': '1440'})
        report = self.resolve().report()
        self.assertIn('SHARED_STORAGE:sessions', report['blockers'])
        self.assertIn('SHARED_STORAGE:temporary', report['blockers'])
        self.assertIn('SESSION_RETENTION_MISMATCH', report['blockers'])
        self.assertFalse(report['storage_inventory_complete'])

    def test_separate_upload_tmp_does_not_drop_system_temp_fallback(self):
        self.facts.php['upload_tmp_dir'] = '/var/lib/hestia/instance/uploads-tmp'
        roots = self.scopes()
        self.assertNotEqual(roots['upload_temporary']['path'], roots['temporary']['path'])
        self.assertEqual(roots['upload_temporary']['producers'], ['php_upload_staging'])
        self.assertIn('converter_children', roots['temporary']['producers'])

    def test_ai_unobserved_configuration_cannot_mean_disabled(self):
        self.facts.environment['HESTIA_AI_CONFIG_FILE'] = '/etc/hestia/private-legacy.php'
        report = self.resolve().report()
        self.assertIn('AI_USAGE_DIRECTORY_UNOBSERVED', report['blockers'])
        observed = replace(self.facts, ai_configuration_file='/etc/hestia/private-legacy.php',
                           ai_usage_directory='/var/lib/hestia/instance/ai')
        roots = self.scopes(observed)
        self.assertEqual(roots['ai_usage']['path'], '/var/lib/hestia/instance/ai')
        self.assertNotIn('AI_USAGE_DIRECTORY_UNOBSERVED', self.resolve(observed).report()['blockers'])
        self.assertFalse(self.resolve(observed).report()['system_wiring_verified'])

    def test_missing_unknown_or_null_observations_are_never_invented(self):
        for field in ('environment', 'constants', 'app_config', 'php'):
            broken = copy.deepcopy(self.facts)
            getattr(broken, field).pop(next(iter(getattr(broken, field))))
            with self.assertRaisesRegex(s.StorageInventoryError, 'STORAGE_OBSERVATIONS_INCOMPLETE'): self.resolve(broken)
        broken = copy.deepcopy(self.facts)
        broken.environment['OPENAI_API_KEY'] = 'must-not-accept-secret'
        with self.assertRaisesRegex(s.StorageInventoryError, 'STORAGE_OBSERVATIONS_INCOMPLETE'): self.resolve(broken)
        self.facts.environment['HESTIA_GED_LEGACY_ROOTS'] = None
        with self.assertRaises(s.StorageInventoryError): self.resolve()

    def test_unsafe_noncanonical_paths_and_control_characters_fail_closed(self):
        for path in ('relative', '//host/share', '/path/../escape', '/path//child', '/path/./child', '/bad\nname', '/bad\x00name'):
            self.facts.php['upload_tmp_dir'] = path
            with self.assertRaises(s.StorageInventoryError): self.resolve()
        self.facts.php['upload_tmp_dir'] = '/' + 'é' * 2100
        with self.assertRaises(s.StorageInventoryError): self.resolve()

    def test_private_manifest_and_public_report_are_immutable_and_do_not_leak_paths(self):
        self.facts.environment['HESTIA_MOBILE_RELEASE_DIR'] = '/var/lib/private-Unicode-漢字'
        original = copy.deepcopy(self.facts)
        result = self.resolve()
        self.assertEqual(self.facts, original)
        result.private_manifest()['scopes'].clear()
        self.assertTrue(result.private_manifest()['scopes'])
        report = result.report();report['apply_allowed'] = True
        self.assertFalse(result.report()['apply_allowed'])
        with self.assertRaises(FrozenInstanceError): result._canonical = b'{}'
        self.assertNotIn('/var/lib/private', json.dumps(result.report()))
        self.assertNotIn('/srv/hestia', repr(result))
        for name in ('filesystem_verified', 'storage_inventory_complete', 'system_wiring_verified',
                     'complete_web_backup', 'apply_allowed', 'rollback_verified', 'application_installed'):
            self.assertIs(result.report()[name], False)

    def test_all_producer_classes_remain_unverified_including_prepend_blind_spots(self):
        groups = {x['group']: x for x in self.resolve().private_manifest()['producers']}
        self.assertEqual(set(groups), set(s.PRODUCERS))
        self.assertIn('multipart_and_fpm_drain', groups['php_upload_staging']['requirements'])
        self.assertIn('orphan_process_drain', groups['converter_children']['requirements'])
        self.assertTrue(all(x['state'] == 'REQUIRED_NOT_VERIFIED' for x in groups.values()))

    def test_source_pin_and_source_drift_are_rejected_and_diagnostics_are_closed(self):
        with self.assertRaisesRegex(s.StorageInventoryError, 'SOURCE_PIN_MISMATCH'):
            s.StorageInventory(Path('/unused'), repository=p.WEB_REPOSITORY, commit='main')
        self.verify.side_effect = [None, s.StorageInventoryError('SOURCE_PIN_MISMATCH')]
        with self.assertRaisesRegex(s.StorageInventoryError, 'SOURCE_PIN_MISMATCH'): self.resolve()
        self.verify.side_effect = RuntimeError('private-secret-diagnostic')
        with self.assertRaisesRegex(s.StorageInventoryError, '^STORAGE_INSPECTION_UNAVAILABLE$'): self.resolve()
