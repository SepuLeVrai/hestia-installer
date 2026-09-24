"""Regression tests for the checks themselves. No environment changes or network."""
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import quality as q


class QualityGateTests(unittest.TestCase):
    def test_documentation_change_classification_is_fail_closed(self):
        self.assertTrue(q.is_docs_only(['docs/QUALITY.md', 'README.md']))
        for names in ([], ['docs/tool.py'], ['installer/web/assets/logo.webp'], ['.github/workflows/quality.yml'],
                      ['tests/test_security.py'], ['docs/../installer/file.md'], ['unknown.md'], ['README.md', 'installer/cli.py'],
                      ['docs/long-' + 'é' * 100 + '.py'], ['docs/bad\nname.md']):
            with self.subTest(names=names):
                self.assertFalse(q.is_docs_only(names))

    def test_missing_diff_metadata_enables_full_quality(self):
        with patch.dict(q.os.environ, {'QUALITY_BASE': '', 'GITHUB_EVENT_NAME': 'push'}):
            self.assertTrue(q.scope())
        with patch.dict(q.os.environ, {'QUALITY_BASE': '0' * 40}):
            self.assertTrue(q.scope())

    def test_dispatch_always_runs_full_quality(self):
        with patch.dict(q.os.environ, {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'QUALITY_BASE': 'a' * 40}):
            self.assertTrue(q.scope())
        base = 'a' * 40; repo = 'fixture/installer'
        run = {'head_sha':base,'status':'completed','conclusion':'success','path':'.github/workflows/quality.yml',
               'event':'push','head_repository':{'full_name':repo}}
        self.assertTrue(q.approved_previous_run({'workflow_runs':[run]}, base, repo))
        for change in ({'status':'in_progress'}, {'conclusion':'failure'}, {'conclusion':'cancelled'},
                       {'head_sha':'b'*40}, {'path':'other.yml'}, {'head_repository':{'full_name':'other/repo'}}):
            self.assertFalse(q.approved_previous_run({'workflow_runs':[dict(run,**change)]},base,repo))
        self.assertFalse(q.approved_previous_run({},base,repo))
        with patch.dict(q.os.environ, {'QUALITY_HISTORY_TOKEN':'', 'GITHUB_REPOSITORY':repo}):
            self.assertFalse(q.previous_quality_passed(base))

    def test_only_successful_required_jobs_pass_the_gate(self):
        q.gate('true', 'success', 'success', 'success')
        q.gate('false', 'success', 'skipped', 'skipped')
        for values in [('true','success','failure','success'), ('true','success','success','skipped'),
                       ('true','success','cancelled','success'), ('false','failure','skipped','skipped'),
                       ('','success','skipped','skipped'), ('false','success','success','skipped')]:
            with self.subTest(values=values), self.assertRaises(q.QualityError):
                q.gate(*values)

    def _result(self, code):
        namespace = {'unittest': unittest}
        exec('class Sample(unittest.TestCase):\n    def test_sample(self):\n        ' + code, namespace)
        return unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromTestCase(namespace['Sample']))

    def test_passing_test_is_required(self):
        self.assertTrue(q.verdict(self._result('self.assertTrue(True)'), [], True))
        self.assertFalse(q.verdict(self._result('self.fail("fixture")'), [], True))
        self.assertFalse(q.verdict(unittest.TestResult(), [], True))
        self.assertFalse(q.verdict(self._result('self.assertTrue(True)'), [], True, preflight=False))

    def test_skip_cannot_produce_green_quality(self):
        self.assertFalse(q.verdict(self._result('self.skipTest("fixture")'), [], True))

    def test_missing_inventory_and_source_drift_are_failures(self):
        result = self._result('self.assertTrue(True)')
        self.assertFalse(q.verdict(result, ['missing.test'], True))
        self.assertFalse(q.verdict(result, [], False))

    def test_expected_failures_are_not_accepted(self):
        result = self._result('self.assertTrue(True)')
        result.expectedFailures.append((None, 'fixture'))
        self.assertFalse(q.verdict(result, [], True))
        xml = q.ET.Element('testsuite')
        case = q.ET.SubElement(xml, 'testcase', name='strict_gate')
        q.ET.SubElement(case, 'failure').text = 'fixture'
        q.finish_junit(xml)
        self.assertEqual(xml.get('tests'), '1')
        self.assertEqual(xml.get('failures'), '1')

    def test_runtime_ast_guards_detect_shell_spacing_and_dynamic_calls(self):
        for source in ('subprocess.run(["x"], shell = True)', 'subprocess.run(["x"], shell=flag)',
                       'eval(value)', 'exec(value)', 'os.system(value)', 'ssl._create_unverified_context()'):
            with self.subTest(source=source):
                self.assertTrue(q.python_risks(source))
        self.assertEqual(q.python_risks('subprocess.run(["x"], shell=False, check=True)'), [])

    def test_unsafe_archive_paths_are_rejected(self):
        for name in ('', '/etc/passwd', '../escape', 'a/../b', 'a\\b', 'a\x00b', 'a//b'):
            self.assertFalse(q.safe_name(name))
        self.assertTrue(q.safe_name('docs/Caractères spéciaux.md'))

    def test_source_manifest_detects_content_and_executable_mode_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'launch.sh'
            source.write_text('#!/bin/sh\nexit 0\n'); source.chmod(0o755)
            before = q.snapshot(root); q.verify_snapshot(before, root)
            source.chmod(0o644)
            with self.assertRaises(q.QualityError): q.verify_snapshot(before, root)
            source.chmod(0o755); source.write_text('#!/bin/sh\nexit 1\n')
            with self.assertRaises(q.QualityError): q.verify_snapshot(before, root)

    def test_source_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'real').write_text('fixture'); (root/'link').symlink_to('real')
            with self.assertRaises(q.QualityError): q.snapshot(root)

    def test_package_preserves_exact_bytes_and_unix_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'source'; root.mkdir()
            path = root/'launch.sh'; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
            (root/'document.md').write_text('# Titre é & < >\n'); (root/'document.md').chmod(0o644)
            manifest=q.snapshot(root)
            one=Path(directory)/'one.zip'; two=Path(directory)/'two.zip'
            q.write_zip(one,manifest,root); q.write_zip(two,manifest,root)
            self.assertEqual(one.read_bytes(),two.read_bytes())
            with zipfile.ZipFile(one) as archive:
                self.assertEqual(stat.S_IMODE(archive.getinfo('launch.sh').external_attr >> 16),0o755)
                self.assertEqual(archive.read('document.md'),(root/'document.md').read_bytes())

    def test_package_refuses_last_minute_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'; root.mkdir(); path=root/'a'; path.write_text('before')
            manifest=q.snapshot(root); path.write_text('after')
            with self.assertRaises(q.QualityError): q.write_zip(Path(directory)/'bad.zip',manifest,root)

    def test_incomplete_reports_cannot_authorize_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(q.QualityError): q.package(Path(directory), Path(directory)/'out')

    def test_script_mode_regression_is_visible_in_snapshot(self):
        mode=q.snapshot()['scripts/quality-wizard.sh']['mode']
        self.assertEqual(mode,'0755')
