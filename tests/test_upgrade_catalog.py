"""Closed transition policy and source-metadata audit; no SQL or host mutation."""
import copy
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import upgrade_catalog as c
from installer.web_releases import LEGACY_COMMIT, STORAGE_COMMIT

spec = importlib.util.spec_from_file_location('catalog_audit', Path(__file__).resolve().parents[1] / 'scripts/audit_upgrade_catalog.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class UpgradeCatalogTests(unittest.TestCase):
    def assess(self, source=LEGACY_COMMIT, target=STORAGE_COMMIT):
        return c.assess_transition(repository=c.REPOSITORY, source_commit=source, target_commit=target)

    def test_forward_build_change_requires_relocation_and_source_backup(self):
        report = self.assess().report()
        self.assertEqual(report['relation'], 'STORAGE_LAYOUT_CHANGE_SAME_SQL_LINEAGE')
        self.assertEqual(report['storage_change'], 'IN_WEBROOT_TO_EXTERNAL_UPLOADS')
        self.assertIn('SOURCE_BACKUP_PROFILE_NOT_QUALIFIED', report['blockers'])
        self.assertIn('DATA_RELOCATION_NOT_IMPLEMENTED', report['blockers'])
        self.assertIn('COHERENT_CUTOVER_NOT_IMPLEMENTED', report['blockers'])
        self.assertIn('TRANSITION_RECOVERY_NOT_QUALIFIED', report['blockers'])

    def test_identical_builds_are_not_a_version_transition(self):
        for pin in (LEGACY_COMMIT, STORAGE_COMMIT):
            result = self.assess(pin, pin).report()
            self.assertEqual(result['relation'], 'IDENTICAL_RELEASE')
            self.assertEqual(result['blockers'], ['NO_VERSION_TRANSITION'])

    def test_reverse_edge_never_implies_a_supported_downgrade(self):
        result = self.assess(STORAGE_COMMIT, LEGACY_COMMIT).report()
        self.assertEqual(result['relation'], 'REVERSE_STORAGE_TRANSITION_UNSUPPORTED')
        self.assertEqual(result['blockers'], ['DOWNGRADE_NOT_CATALOGUED'])

    def test_every_known_pair_retains_all_execution_and_host_proof_flags_false(self):
        flags = ('apply_allowed', 'transition_supported', 'source_host_verified', 'target_files_verified',
                 'backup_verified', 'maintenance_held', 'migration_verified', 'cutover_verified',
                 'rollback_verified', 'phase5c3_complete', 'application_installed')
        for source in (LEGACY_COMMIT, STORAGE_COMMIT):
            for target in (LEGACY_COMMIT, STORAGE_COMMIT):
                result = self.assess(source, target).report()
                self.assertEqual(result['scope'], 'RELEASE_CATALOG_ONLY')
                for flag in flags:
                    self.assertIs(result[flag], False, flag)

    def test_unknown_branches_tags_versions_and_types_never_select_a_pin(self):
        class Text(str):
            pass
        for bad in ('main', 'dev-Bastien', 'v3.0.0.0', c.VERSION, LEGACY_COMMIT[:7], LEGACY_COMMIT.upper(),
                    '0' * 40, '', None, True, 1, [], {}, Text(LEGACY_COMMIT)):
            for source, target in ((bad, STORAGE_COMMIT), (LEGACY_COMMIT, bad)):
                with self.subTest(bad=bad), self.assertRaisesRegex(c.UpgradeCatalogError, '^CATALOG_SOURCE_PIN_MISMATCH$'):
                    self.assess(source, target)

    def test_repository_identity_is_exact_and_error_does_not_echo_input(self):
        for repository in ('other/repo', 'sepuLeVrai/hestia-nexus-avv', c.REPOSITORY + '/secret', None, [], True):
            with self.assertRaisesRegex(c.UpgradeCatalogError, '^CATALOG_SOURCE_PIN_MISMATCH$'):
                c.assess_transition(repository=repository, source_commit=LEGACY_COMMIT, target_commit=STORAGE_COMMIT)

    def test_future_fresh_release_does_not_implicitly_join_the_upgrade_catalogue(self):
        with patch.object(c, 'get_release') as registry:
            with self.assertRaisesRegex(c.UpgradeCatalogError, '^CATALOG_SOURCE_PIN_MISMATCH$'):
                self.assess('a' * 40, STORAGE_COMMIT)
            registry.assert_not_called()

    def test_same_version_does_not_erase_distinct_runtime_or_storage_identities(self):
        result = self.assess().report()
        self.assertEqual(result['source']['app_version'], result['target']['app_version'])
        for key in ('commit', 'tree', 'runtime_sha256', 'storage_layout'):
            self.assertNotEqual(result['source'][key], result['target'][key])

    def test_published_inventory_is_not_replayed_or_ordered_for_execution(self):
        sql = self.assess().report()['sql_lineage']
        self.assertEqual(sql['migration_files'], 113)
        self.assertEqual(sql['selected_migrations'], [])
        self.assertIs(sql['published_sql_delta'], False)

    def test_caller_cannot_supply_migrations_confirmation_or_apply_permission(self):
        for field, value in (('migrations', ['schema.sql']), ('confirmed', True), ('apply_allowed', True)):
            with self.assertRaises(TypeError):
                c.assess_transition(repository=c.REPOSITORY, source_commit=LEGACY_COMMIT,
                                    target_commit=STORAGE_COMMIT, **{field: value})
        self.assertFalse(hasattr(c.CatalogAssessment, 'apply'))

    def test_nested_report_edits_cannot_change_the_observation_or_its_digest(self):
        assessment = self.assess()
        original, digest = assessment.report(), assessment.sha256
        report = assessment.report()
        report['source']['commit'] = 'forged'
        report['sql_lineage']['selected_migrations'].append('schema.sql')
        report['blockers'].clear()
        report['apply_allowed'] = True
        self.assertEqual(assessment.report(), original)
        self.assertEqual(assessment.sha256, digest)
        with self.assertRaises(FrozenInstanceError):
            assessment._encoded = b'{}'

    def test_digest_binds_direction_and_is_repeatable(self):
        assessment = self.assess()
        self.assertEqual(assessment.sha256, self.assess().sha256)
        self.assertNotEqual(assessment.sha256, self.assess(STORAGE_COMMIT, LEGACY_COMMIT).sha256)
        self.assertEqual(assessment.sha256, hashlib.sha256(c._encode(assessment.report())).hexdigest())


class CatalogSourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Native git constructs the fixture hashes independently of our auditor.
        self.git('init', '-q')
        paths = ('sql/schema.sql', 'sql/migrations/one.sql', 'includes/version.php',
                 'includes/installation/core.php', 'includes/installation/fresh.php', 'script.sh')
        for name in paths:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture ' + name + '\n')
        (self.root / 'script.sh').chmod(0o755)
        self.git('add', '.')
        tree = self.git('write-tree').decode().strip()
        entries = []
        for line in self.git('ls-tree', '-r', '-t', '-z', tree).split(b'\0'):
            if line:
                meta, name = line.decode().split('\t')
                mode, kind, digest = meta.split()
                entries.append({'path': name, 'mode': mode, 'type': kind, 'sha': digest})
        self.record = {'sha': tree, 'tree': entries, 'truncated': False}
        pins = {entry['path']: entry['sha'] for entry in entries}
        values = {'SCHEMA_BLOB': pins['sql/schema.sql'], 'MIGRATIONS_TREE': pins['sql/migrations'],
                  'VERSION_BLOB': pins['includes/version.php'], 'CORE_BLOB': pins['includes/installation/core.php'],
                  'FRESH_BLOB': pins['includes/installation/fresh.php'], 'MIGRATION_FILES': 1}
        for name, value in values.items():
            context = patch.object(c, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(audit, 'get_release', return_value=SimpleNamespace(tree=tree, files=len(paths)))
        context.start()
        self.addCleanup(context.stop)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True).stdout

    def verify(self, record=None):
        return audit.verify_tree(self.record if record is None else record, LEGACY_COMMIT)

    def rejected(self, record):
        with self.assertRaisesRegex(ValueError, '^CATALOG_EVIDENCE_REJECTED$'):
            audit.verify_tree(record, LEGACY_COMMIT)

    def test_full_native_git_tree_and_executable_mode_are_accepted(self):
        result = self.verify()
        self.assertEqual(result['script.sh'][0], '100755')
        self.assertEqual(len(result), len(self.record['tree']))

    def test_input_order_does_not_change_git_identity(self):
        other = copy.deepcopy(self.record)
        other['tree'].reverse()
        self.assertEqual(self.verify(other), self.verify())

    def test_truncated_missing_and_non_boolean_completeness_refused(self):
        for value in (True, 0, None, 'false'):
            record = copy.deepcopy(self.record)
            record['truncated'] = value
            self.rejected(record)

    def test_claimed_root_without_matching_content_is_refused(self):
        record = copy.deepcopy(self.record)
        record['tree'][-1]['sha'] = 'a' * 40
        self.rejected(record)
        record = copy.deepcopy(self.record)
        record['sha'] = 'a' * 40
        self.rejected(record)

    def test_missing_file_directory_or_entire_subtree_is_refused(self):
        for prefix in ('script.sh', 'includes', 'includes/'):
            record = copy.deepcopy(self.record)
            record['tree'] = [x for x in record['tree'] if not x['path'].startswith(prefix)]
            self.rejected(record)

    def test_duplicate_entry_is_refused(self):
        record = copy.deepcopy(self.record)
        record['tree'].append(copy.deepcopy(record['tree'][0]))
        self.rejected(record)

    def test_path_traversal_aliases_and_control_characters_are_refused(self):
        for name in ('../file', '/file', './file', 'a//b', 'a/./b', 'a/../b', 'a\\b', 'x\0y', 'x\ny', '.git/config', ''):
            record = copy.deepcopy(self.record)
            record['tree'][-1]['path'] = name
            self.rejected(record)

    def test_symlinks_submodules_and_unknown_modes_are_refused(self):
        for mode, kind in (('120000', 'blob'), ('160000', 'commit'), ('100664', 'blob'), ('040000', 'blob')):
            record = copy.deepcopy(self.record)
            record['tree'][-1].update(mode=mode, type=kind)
            self.rejected(record)

    def test_blob_masquerading_as_parent_is_refused(self):
        record = copy.deepcopy(self.record)
        record['tree'].append({'path': 'script.sh/child', 'mode': '100644', 'type': 'blob', 'sha': 'a' * 40})
        self.rejected(record)

    def test_mode_drift_without_content_change_is_refused(self):
        record = copy.deepcopy(self.record)
        next(x for x in record['tree'] if x['path'] == 'script.sh')['mode'] = '100644'
        self.rejected(record)

    def test_catalogue_pin_count_and_sql_lineage_mismatches_are_refused(self):
        for field, value in (('SCHEMA_BLOB', '0' * 40), ('MIGRATIONS_TREE', '0' * 40),
                             ('VERSION_BLOB', '0' * 40), ('CORE_BLOB', '0' * 40),
                             ('FRESH_BLOB', '0' * 40), ('MIGRATION_FILES', 2)):
            with patch.object(c, field, value):
                self.rejected(self.record)
        with patch.object(audit, 'get_release', return_value=SimpleNamespace(tree=self.record['sha'], files=7)):
            self.rejected(self.record)

    def test_malformed_records_and_unbounded_entry_lists_are_refused(self):
        for bad in ([], None, {'sha': self.record['sha'], 'truncated': False, 'tree': []}):
            self.rejected(bad)
        record = copy.deepcopy(self.record)
        record['tree'] = [record['tree'][0]] * 10001
        self.rejected(record)
        for field, value in (('path', True), ('mode', []), ('sha', 'f' * 41), ('type', {})):
            record = copy.deepcopy(self.record)
            record['tree'][0][field] = value
            self.rejected(record)

    def test_duplicate_json_keys_and_oversized_records_are_refused(self):
        path = self.root / 'input.json'
        for raw in (b'{"sha":"first","sha":"second"}', b' ' * (audit.MAX_RECORD + 1)):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                audit.read_record(path)

    def test_cli_bad_evidence_returns_fixed_error_without_echoing_content(self):
        path = self.root / 'secret-evidence.json'
        for raw in ('{"private_value":"sensitive fixture"}', '{"sha":"first","sha":"second"}',
                    '[' * 1500 + ']' * 1500):
            path.write_text(raw)
            result = subprocess.run(['python3', str(Path(audit.__file__)), '--source-tree', str(path),
                                     '--target-tree', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'status': 'FAIL', 'code': 'CATALOG_EVIDENCE_REJECTED'})
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
