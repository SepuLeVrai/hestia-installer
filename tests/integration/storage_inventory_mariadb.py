#!/usr/bin/env python3
"""Pinned real PHP/SQL path semantics and maintenance boundary evidence."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(Path(__file__).resolve().parent)]
from installer import finalization as f
from installer import php_transport as p
from installer import storage_inventory as s
import coordinated_backup_mariadb as previous
from database_step_mariadb import literal

WEB = None


class StorageLive(previous.CoordinatedLive):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_STORAGE_INVENTORY_TEST') != '1':
            raise RuntimeError('Explicit storage inventory opt-in required')
        previous.WEB = WEB
        super().setUpClass()

    def setup_storage(self):
        self.ready()
        self.temporary = self.make_directory('dedicated-tmp')
        self.env = {key: '' for key in s.ENVIRONMENT}
        self.app = {key: '' for key in s.APP_CONFIG}
        self.constants = {'HESTIA_IMPORT_STORAGE': None}
        self.inventory = s.StorageInventory(WEB, repository=p.WEB_REPOSITORY, commit=f.WEB_COMMIT)

    def make_directory(self, name):
        path = self.root / name
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(path, self.web.pw_uid, self.web.pw_gid)
        return path

    def script(self, body, name='storage-probe.php'):
        path = self.root / name
        path.write_text('<?php\ndeclare(strict_types=1);\n' + body)
        path.chmod(0o644)
        return path

    def command(self, path, guard=False):
        command = ['setpriv', '--reuid=' + str(self.web.pw_uid), '--regid=' + str(self.web.pw_gid),
                   '--clear-groups', '--no-new-privs', str(self.runtime.php),
                   '-d', 'display_errors=0', '-d', 'log_errors=0', '-d', 'zend.exception_ignore_args=1',
                   '-d', 'session.save_handler=files', '-d', 'session.save_path=' + str(self.sessions),
                   '-d', 'session.gc_maxlifetime=43200', '-d', 'sys_temp_dir=' + str(self.temporary),
                   '-d', 'upload_tmp_dir=', '-d', 'error_log=syslog']
        if guard:
            command += ['-d', 'auto_prepend_file=' + str(self.guard)]
        return command + [str(path)]

    def environment(self):
        return {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TZ': 'UTC', **self.env}

    def probe(self, body):
        prefix = 'define("APP_ROOT",hex2bin("' + os.fsencode(self.webroot).hex() + '"));\n'
        if self.constants['HESTIA_IMPORT_STORAGE'] is not None:
            prefix += 'define("HESTIA_IMPORT_STORAGE",hex2bin("' + self.constants['HESTIA_IMPORT_STORAGE'].encode().hex() + '"));\n'
        path = self.script(prefix + body)
        result = subprocess.run(self.command(path), env=self.environment(), cwd=self.webroot,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, 'Real PHP storage probe failed')
        self.assertEqual(result.stderr, b'')
        return json.loads(result.stdout)

    def observations(self, *, ai_file=None, ai_usage=None):
        php = self.probe('echo json_encode(["session.save_handler"=>ini_get("session.save_handler"),'
            '"session.save_path"=>session_save_path(),"session.gc_maxlifetime"=>ini_get("session.gc_maxlifetime"),'
            '"effective_sys_temp_dir"=>sys_get_temp_dir(),"upload_tmp_dir"=>ini_get("upload_tmp_dir"),'
            '"error_log"=>ini_get("error_log")],JSON_THROW_ON_ERROR);')
        return s.StorageFacts(self.webroot, self.directory, dict(self.env), dict(self.constants), dict(self.app),
                              php, ai_file, ai_usage)

    def mapped(self, facts=None):
        return {x['role']: x for x in self.inventory.inspect(facts or self.observations()).private_manifest()['scopes']}

    def test_storage_real_php_import_constant_and_environment_precedence(self):
        self.setup_storage()
        env = self.make_directory('imports-env')
        constant = self.make_directory('imports-constant')
        self.env['HESTIA_IMPORT_STORAGE'] = str(env)
        body = 'require APP_ROOT."/includes/import_engine.php";echo json_encode(pa_import_private_root());'
        self.assertEqual(self.probe(body), self.mapped()['imports_effective']['path'])
        self.constants['HESTIA_IMPORT_STORAGE'] = str(constant)
        self.assertEqual(self.probe(body), self.mapped()['imports_effective']['path'])
        self.constants['HESTIA_IMPORT_STORAGE'] = ''
        self.assertEqual(self.probe(body), self.mapped()['imports_effective']['path'])
        self.assertEqual(self.mapped()['imports_environment']['path'], str(env))

    def test_storage_reference_import_real_fallback_is_included(self):
        self.setup_storage()
        # Existing root-owned private directory is unavailable to the Web UID.
        denied = self.root / 'denied-imports';denied.mkdir(mode=0o700)
        self.env['HESTIA_IMPORT_STORAGE'] = str(denied)
        actual = self.probe('require APP_ROOT."/includes/reference_import_engine.php";'
                            'echo json_encode(hestia_reference_import_private_root());')
        roots = self.mapped()
        self.assertEqual(actual, roots['reference_import_fallback']['path'])
        self.assertIn('temporary', roots['reference_import_fallback']['covered_by'])
        self.assertTrue(Path(actual).is_dir())

    def test_storage_mobile_setting_comes_from_actual_database_before_environment(self):
        self.setup_storage()
        db_root = self.make_directory('mobile-database')
        env_root = self.make_directory('mobile-environment')
        self.env['HESTIA_MOBILE_RELEASE_DIR'] = str(env_root)
        self.sql([f"INSERT INTO `{self.db}`.App_Config(cle,valeur) VALUES('HESTIA_MOBILE_RELEASE_DIR',{literal(str(db_root))}) ON DUPLICATE KEY UPDATE valeur=VALUES(valeur)"])
        observed = self.sql(query=f"SELECT valeur FROM `{self.db}`.App_Config WHERE cle='HESTIA_MOBILE_RELEASE_DIR'")[0]['valeur']
        self.app['HESTIA_MOBILE_RELEASE_DIR'] = observed
        before = self.logical_dump()
        actual = self.probe('require APP_ROOT."/includes/functions.php";require APP_ROOT."/includes/db.php";'
                            'require APP_ROOT."/includes/mobile_updates.php";echo json_encode(hestia_mobile_update_configured_path());')
        roots = self.mapped()
        self.assertEqual(actual, roots['mobile_releases_effective']['path'])
        self.assertEqual(roots['mobile_releases_environment']['path'], str(env_root))
        self.assertEqual(before, self.logical_dump())

    def test_storage_ged_real_helpers_match_existing_external_and_legacy_roots(self):
        self.setup_storage()
        external = self.make_directory('legacy-external')
        legacy = self.webroot / 'uploads/ged_legacy/retained'
        legacy.mkdir(parents=True, mode=0o755)
        self.env['HESTIA_GED_LEGACY_ROOTS'] = str(external)
        self.app['security.ged_legacy_roots'] = 'retained'
        self.sql([f"INSERT INTO `{self.db}`.App_Config(cle,valeur) VALUES('security.ged_legacy_roots','retained') ON DUPLICATE KEY UPDATE valeur=VALUES(valeur)"])
        before = self.logical_dump()
        actual = self.probe('require APP_ROOT."/includes/functions.php";require APP_ROOT."/includes/db.php";'
                            'require APP_ROOT."/includes/ged/helpers.php";echo json_encode(hestia_ged_allowed_document_roots());')
        roots = self.mapped()
        self.assertIn(roots['ged_external_0']['path'], actual)
        self.assertIn(roots['ged_legacy_0']['path'], actual)
        self.assertIn(str(self.webroot / 'uploads/ged_documents'), actual)
        self.assertEqual(before, self.logical_dump())

    def test_storage_ai_effective_configuration_is_observed_without_exporting_secret(self):
        self.setup_storage()
        usage = self.make_directory('ai-usage')
        secret = 'fixture-private-ai-key-' + 'A' * 48
        conf = self.script('return ["usage_dir"=>hex2bin("' + str(usage).encode().hex()
                           + '"),"openai_api_key"=>"' + secret + '"];', 'private-ai.php')
        self.env['HESTIA_AI_CONFIG_FILE'] = str(conf)
        actual = self.probe('require APP_ROOT."/includes/ai/config.php";$c=hestia_ai_config();'
                            'echo json_encode(["path"=>hestia_ai_config_path(),"usage"=>$c["usage_dir"]]);')
        requirements = self.inventory.inspect(self.observations(ai_file=actual['path'], ai_usage=actual['usage']))
        roots = {x['role']: x for x in requirements.private_manifest()['scopes']}
        self.assertEqual(roots['ai_effective_configuration']['path'], str(conf))
        self.assertEqual(roots['ai_usage']['path'], str(usage))
        self.assertNotIn(secret, json.dumps(requirements.report()))
        self.assertNotIn(secret, json.dumps(requirements.private_manifest()))
        self.assertFalse(requirements.report()['storage_inventory_complete'])

    def test_storage_real_cli_writer_is_blocked_by_existing_prepend_guard(self):
        self.setup_storage()
        target = self.data / 'cli-write'
        path = self.script('file_put_contents(hex2bin("' + str(target).encode().hex() + '"),"written");')
        command = self.command(path, guard=True)
        with self.scope.acquire(confirmed=True) as lease:
            result = subprocess.run(command, env=self.environment(), capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 75)
            self.assertFalse(target.exists())
            lease.resume(confirmed=True)
        result = subprocess.run(command, env=self.environment(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(target.read_bytes(), b'written')

    def test_storage_multipart_staging_precedes_guard_even_for_a_refused_request(self):
        self.setup_storage()
        marker = self.data / 'staging-before-guard'
        prepend = self.script('if(isset($_FILES["fixture"]))file_put_contents(hex2bin("' + str(marker).encode().hex()
            + '"),is_uploaded_file($_FILES["fixture"]["tmp_name"])?"staged":"absent");'
            'require hex2bin("' + str(self.guard).encode().hex() + '");', 'multipart-observer.php')
        with self.http(prepend=prepend) as request:
            url = urlsplit(request('/login.php')[3])
            with self.scope.acquire(confirmed=True):
                body = b'--fixture-boundary\r\nContent-Disposition: form-data; name="fixture"; filename="sample.bin"\r\nContent-Type: application/octet-stream\r\n\r\nprivate fixture bytes\r\n--fixture-boundary--\r\n'
                req = urllib.request.Request(url.scheme + '://' + url.netloc + '/index.php', data=body,
                    headers={'Content-Type': 'multipart/form-data; boundary=fixture-boundary'})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    response = opener.open(req, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    self.assertEqual(response.status, 503)
                self.assertEqual(marker.read_bytes(), b'staged')
                groups = self.inventory.inspect(self.observations()).private_manifest()['producers']
                self.assertIn('php_upload_staging', [x['group'] for x in groups])

    def test_storage_orphan_converter_requires_service_process_drain(self):
        self.setup_storage()
        child_ready, release, written = (self.data / n for n in ('child-ready', 'release-child', 'child-written'))
        child = self.root / 'converter-fixture.py'
        child.write_text('import os,time\nfrom pathlib import Path\nos.closerange(3,1024)\n'
            'Path(' + repr(str(child_ready)) + ').write_text("ready")\n'
            'deadline=time.monotonic()+12\n'
            'while not Path(' + repr(str(release)) + ').exists() and time.monotonic()<deadline: time.sleep(.02)\n'
            'if Path(' + repr(str(release)) + ').exists(): Path(' + repr(str(written)) + ').write_text("late write")\n')
        child.chmod(0o644)
        php = self.script('$p=proc_open(["/usr/bin/python3",hex2bin("' + str(child).encode().hex()
            + '")],[0=>["file","/dev/null","r"],1=>["file","/dev/null","w"],2=>["file","/dev/null","w"]],$pipes);'
            'if(!is_resource($p))exit(2);while(true)usleep(100000);', 'converter-parent.php')
        process = subprocess.Popen(self.command(php, guard=True), env=self.environment(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 8
            while not child_ready.exists() and time.monotonic() < deadline:
                self.assertIsNone(process.poll());time.sleep(.02)
            self.assertTrue(child_ready.exists())
            os.kill(process.pid, signal.SIGKILL);process.wait(timeout=5)
            with self.scope.acquire(confirmed=True, timeout=2):
                release.write_text('continue')
                deadline = time.monotonic() + 3
                while not written.exists() and time.monotonic() < deadline: time.sleep(.02)
                self.assertEqual(written.read_text(), 'late write')
                report = self.inventory.inspect(self.observations()).report()
                self.assertFalse(report['system_wiring_verified'])
        finally:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    WEB = args.web.resolve()
    names = [n for n in unittest.defaultTestLoader.getTestCaseNames(StorageLive) if n.startswith('test_storage_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(StorageLive(n) for n in names))
    report = {'suite': 'Pinned Web storage semantics and producer boundary evidence', 'tests': result.testsRun,
              'expected': len(names), 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
              'status': 'PASS' if names and result.wasSuccessful() and result.testsRun == len(names) and not result.skipped else 'FAIL'}
    if args.report: args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report));raise SystemExit(report['status'] != 'PASS')
