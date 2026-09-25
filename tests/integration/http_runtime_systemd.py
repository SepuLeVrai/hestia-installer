#!/usr/bin/env python3
"""Actual generated Apache/FPM staging in a disposable Debian PID-1 container.

Only this fixture explicitly lifts maintenance and starts synthetic endpoints.
The product adapter never starts services or claims deployed Web readiness.
"""
import argparse
import dataclasses
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import http_runtime as h
from installer import system_drain as s
from installer.operations import RecoveryDecision
sys.path.insert(0, str(ROOT / 'scripts'))
import quality


def command(*args, check=True):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=45, check=check)


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.03)
    raise AssertionError('Bounded synthetic runtime condition not reached')


class HttpRuntimeLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_HTTP_RUNTIME_TEST') != '1' or os.geteuid() != 0 \
                or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell',
                '/usr/sbin/nologin', 'hestia-runtime-test')
        cls.account = pwd.getpwnam('hestia-runtime-test')
        cls.family = '8.4' if Path('/usr/sbin/php-fpm8.4').is_file() else '8.2'
        extension = Path('/usr/lib/php') / {'8.2': '20220829', '8.4': '20240924'}[cls.family]
        print('Official PHP extension sizes:', json.dumps({name: (extension / (name + '.so')).stat().st_size
                                                          for name in h.EXTENSIONS}, sort_keys=True))

    def setUp(self):
        self.instance = os.urandom(16).hex()
        self.root = Path('/var/lib/hestia-http-' + self.instance)
        self.web = Path(tempfile.mkdtemp(prefix='hestia-http-web-', dir='/srv')); self.web.chmod(0o755)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); self.port = listener.getsockname()[1]
        self.spec = h.RuntimeSpec(self.instance, self.root, self.web, self.account.pw_name,
                                 'runtime.hestia.test', self.port, self.family)
        self.runtime = h.HttpRuntime(self.spec); self.scope = self.runtime._scope(self.account)
        self.units = [self.runtime.unit(role) for role in ('apache', 'php')]
        self.addCleanup(self.cleanup)
        self.write('index.php', '''<?php
session_start(); $_SESSION['fixture'] = 17;
echo json_encode(['ready'=>true, 'session'=>session_save_path(), 'ttl'=>ini_get('session.gc_maxlifetime'),
'strict'=>ini_get('session.use_strict_mode'), 'tmp'=>sys_get_temp_dir(), 'upload'=>ini_get('upload_tmp_dir'),
'imports'=>getenv('HESTIA_IMPORT_STORAGE'), 'proxy'=>getenv('HESTIA_TRUSTED_PROXIES'),
'authorization'=>$_SERVER['HTTP_AUTHORIZATION']??'', 'cookie'=>session_id(),
'override'=>ini_set('session.save_path','/tmp'), 'guard_override'=>ini_set('auto_prepend_file',''),
'extensions'=>array_map('extension_loaded',['mysqli','pdo_mysql','mbstring','curl','dom','zip','gd'])]);
''')
        self.write('upload.php', '''<?php echo json_encode(['error'=>$_FILES['file']['error']??99,
'parent'=>dirname($_FILES['file']['tmp_name']??''),
'body'=>isset($_FILES['file'])?file_get_contents($_FILES['file']['tmp_name']):'']);''')
        self.write('style.css', 'body { color: black; }')
        for name in ('install.php', 'docs/probe.txt', 'config/probe.txt', 'internal/probe.txt',
                     'vendor/probe.txt', '.env', 'nested/.secret', 'dump.sql', 'uploads/run.php',
                     'uploads/run.phtml', 'uploads/dar/probe.txt', 'uploads/tmp/probe.txt'):
            self.write(name, '<?php echo "forbidden-fixture";')
        self.write('.htaccess', 'THIS_INVALID_DIRECTIVE_MUST_NOT_BE_PARSED\n')
        self.global_paths = [Path('/etc/apache2/apache2.conf'), Path('/etc/php') / self.family / 'fpm/php.ini',
            Path('/etc/php') / self.family / 'fpm/pool.d/www.conf', Path('/usr/lib/php/sessionclean')]
        self.global_before = {str(p): p.read_bytes() for p in self.global_paths}

    def write(self, name, content):
        path = self.web / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content); path.chmod(0o644)

    def cleanup(self):
        for unit in self.units:
            command('systemctl', 'stop', unit, check=False)
            command('systemctl', 'reset-failed', unit, check=False)
            path = s.UNIT_ROOT / unit
            if path.exists(): path.unlink()
            shutil.rmtree(s.UNIT_ROOT / (unit + '.d'), ignore_errors=True)
        command('systemctl', 'daemon-reload')
        shutil.rmtree(self.root, ignore_errors=True); shutil.rmtree(self.web, ignore_errors=True)

    def create(self):
        try: return self.runtime.create(confirmed=True)
        except Exception:
            # Synthetic configurations only, no credentials or real deployment.
            if (self.root / 'conf/fpm.conf').exists():
                result = command('/usr/sbin/php-fpm' + self.family, '-t', '-c', str(self.root / 'conf/php.ini'),
                                 '-y', str(self.root / 'conf/fpm.conf'), check=False)
                print('Fixture FPM configtest:', result.stderr.decode(errors='replace'))
            if (self.root / 'conf/apache.conf').exists():
                result = command('/usr/sbin/apache2', '-t', '-f', str(self.root / 'conf/apache.conf'), check=False)
                print('Fixture Apache configtest:', result.stderr.decode(errors='replace'))
            raise

    def request(self, path='/', *, host=None, headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request('POST' if body is not None else 'GET', path, body=body,
                         headers={'Host': host or self.spec.hostname, **(headers or {})})
            response = conn.getresponse(); return response.status, dict(response.getheaders()), response.read()
        finally: conn.close()

    def ready(self):
        try: return self.request()[0] == 200
        except OSError: return False

    def activate_fixture(self):
        state = self.scope.observe()
        with self.scope.recover(state['lease_id'], confirmed=True) as lease: lease.resume(confirmed=True)
        command('systemctl', 'start', self.runtime.unit('php'))
        command('systemctl', 'start', self.runtime.unit('apache'))
        try: until(self.ready)
        except Exception:
            for path in (self.root / 'log/apache.log', self.root / 'log/fpm.log', self.root / 'data/log/php.log'):
                if path.is_file(): print('Synthetic service log:', path.read_text()[-3000:])
            raise

    def test_exclusive_stage_is_gated_with_exact_permissions_and_no_global_mutation(self):
        report = self.create()
        self.assertEqual(report['state'], 'HTTP_RUNTIME_STAGED')
        self.assertEqual(report['services_staged'], 2)
        self.assertEqual(report['web_php_compatible'], self.family == '8.4')
        for key in ('services_started', 'native_session_cleaner_wired', 'system_wiring_verified',
                    'application_installed', 'complete_web_backup'):
            self.assertIs(report[key], False)
        for path, expected in self.runtime._directories(self.account).items():
            info = path.stat(); self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), expected)
        for unit in self.units:
            command('systemctl', 'start', unit)
            self.assertEqual(s._show(unit)['ActiveState'], 'inactive'); self.assertTrue(s._empty_cgroup(unit))
        self.assertEqual(self.runtime.observe(), report)
        self.assertEqual({str(p): p.read_bytes() for p in self.global_paths}, self.global_before)
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_actual_php_effective_paths_session_lifetime_extensions_and_admin_locks(self):
        self.create(); self.activate_fixture()
        status, headers, raw = self.request(headers={'Authorization': 'Bearer synthetic-fixture'})
        self.assertEqual(status, 200); body = json.loads(raw)
        for field, directory in (('session', 'sessions'), ('tmp', 'tmp'), ('upload', 'upload-tmp'), ('imports', 'imports')):
            self.assertEqual(body[field], str(self.root / 'data' / directory))
        self.assertEqual(body['ttl'], '43200'); self.assertEqual(body['strict'], '1')
        self.assertFalse(body['override']); self.assertFalse(body['guard_override'])
        self.assertEqual(body['authorization'], 'Bearer synthetic-fixture')
        self.assertEqual(body['proxy'], '127.0.0.1/32'); self.assertTrue(all(body['extensions']))
        self.assertNotIn('X-Powered-By', headers)
        session = self.root / 'data/sessions' / ('sess_' + body['cookie'])
        self.assertTrue(session.is_file()); self.assertEqual(session.stat().st_uid, self.account.pw_uid)
        self.assertEqual(stat.S_IMODE(session.stat().st_mode), 0o600)
        with self.assertRaises(h.HttpRuntimeError): self.runtime.observe()

    def test_apache_host_private_routes_upload_code_and_ignored_htaccess(self):
        self.create(); self.activate_fixture()
        self.assertEqual(self.request(host='attacker.test')[0], 403)
        for path in ('/install.php', '/docs/probe.txt', '/config/probe.txt', '/internal/probe.txt',
                     '/vendor/probe.txt', '/.env', '/nested/.secret', '/dump.sql', '/uploads/run.php',
                     '/uploads/run.phtml', '/uploads/dar/probe.txt', '/uploads/tmp/probe.txt'):
            with self.subTest(path=path): self.assertEqual(self.request(path)[0], 403)
        status, headers, raw = self.request('/style.css')
        self.assertEqual(status, 200); self.assertEqual(headers['Content-Type'], 'text/css')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff'); self.assertIn(b'color', raw)

    def test_multipart_uses_private_upload_directory_and_cleans_temporary_file(self):
        self.create(); self.activate_fixture()
        boundary = 'hestia-fixture-boundary'
        body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="fixture.txt"\r\n'
                + 'Content-Type: text/plain\r\n\r\nfixture-body\r\n--' + boundary + '--\r\n').encode()
        status, _, raw = self.request('/upload.php', body=body,
            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        self.assertEqual(status, 200); result = json.loads(raw)
        self.assertEqual(result, {'error': 0, 'parent': str(self.root / 'data/upload-tmp'), 'body': 'fixture-body'})
        self.assertEqual(list((self.root / 'data/upload-tmp').iterdir()), [])

    def test_foreign_unit_collision_fails_before_root_reservation(self):
        path = s.UNIT_ROOT / self.runtime.unit('apache'); content = '[Service]\nExecStart=/usr/bin/true\n'
        path.write_text(content); path.chmod(0o644)
        with self.assertRaises(h.HttpRuntimeError): self.runtime.create(confirmed=True)
        self.assertFalse(self.root.exists()); self.assertEqual(path.read_text(), content)

    def test_occupied_port_fails_before_creating_any_owned_resource(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.port)); listener.listen()
            with self.assertRaises(h.HttpRuntimeError): self.runtime.create(confirmed=True)
        self.assertFalse(self.root.exists())
        self.assertFalse(any((s.UNIT_ROOT / unit).exists() for unit in self.units))

    def test_existing_root_is_never_adopted_even_when_empty(self):
        self.root.mkdir(mode=0o700)
        with self.assertRaises(h.HttpRuntimeError): self.runtime.create(confirmed=True)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_partial_write_keeps_attempt_and_gate_and_forbids_blind_retry(self):
        original = h.f._write
        def fail(fd, name, *args, **kwargs):
            if name == 'fpm.conf': raise OSError('synthetic-disk-full')
            return original(fd, name, *args, **kwargs)
        with patch.object(h.f, '_write', side_effect=fail), self.assertRaisesRegex(h.HttpRuntimeError, 'INCOMPLETE'):
            self.runtime.create(confirmed=True)
        before = (self.root / 'provision.attempt').read_bytes()
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertFalse((self.root / 'staged.json').exists())
        operation = h.HttpRuntimeOperation(self.runtime)
        self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        with self.assertRaises(h.HttpRuntimeError): self.runtime.create(confirmed=True)
        self.assertEqual((self.root / 'provision.attempt').read_bytes(), before)

    def test_lost_apply_response_recovers_from_live_proof_without_writing_or_reloading(self):
        self.create(); operation = h.HttpRuntimeOperation(self.runtime)
        before = {p: p.read_bytes() for p in (self.root / 'provision.attempt', self.root / 'staged.json')}
        with patch.object(h, '_command', side_effect=AssertionError('observation must not mutate')):
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)

    def test_config_code_directory_and_dependency_drift_revoke_staging_proof(self):
        self.create()
        for path in (self.root / 'conf/php.ini', self.web / 'index.php', s.UNIT_ROOT / self.runtime.unit('php')):
            with self.subTest(path=path.name):
                before = path.read_bytes(); path.write_bytes(before + b'\n# drift\n')
                with self.assertRaises(h.HttpRuntimeError): self.runtime.observe()
                path.write_bytes(before)
        data = self.root / 'data/tmp'; data.chmod(0o755)
        with self.assertRaises(h.HttpRuntimeError): self.runtime.observe()
        data.chmod(0o700)
        with patch.object(self.runtime, '_dependency_hashes', return_value={'changed': 'a' * 64}), \
             self.assertRaises(h.HttpRuntimeError): self.runtime.observe()
        command('systemctl', 'daemon-reload')
        self.assertEqual(self.runtime.observe()['state'], 'HTTP_RUNTIME_STAGED')

    def test_running_php_guard_closes_admission_without_claiming_service_drain(self):
        self.create(); self.activate_fixture()
        with self.scope.acquire(confirmed=True):
            self.assertEqual(self.request()[0], 503)
            self.assertEqual(s._show(self.runtime.unit('apache'))['ActiveState'], 'active')
            with self.assertRaises(h.HttpRuntimeError): self.runtime.observe()
        self.assertEqual(self.request()[0], 503)

    def test_generated_http_units_compose_with_existing_four_role_drain(self):
        self.create(); bindings = list(self.runtime.http_bindings())
        for role in ('cli', 'session-cleaner'):
            unit = 'hestia-' + self.instance + '-' + role + '.service'; self.units.append(unit)
            fragment = b'[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nRestart=no\nKillMode=control-group\nSendSIGKILL=yes\nDelegate=no\n'
            path = s.UNIT_ROOT / unit; path.write_bytes(fragment); path.chmod(0o644)
            directory = s.UNIT_ROOT / (unit + '.d'); directory.mkdir(mode=0o755)
            path = directory / '50-hestia-maintenance.conf'; path.write_bytes(s.condition_dropin(self.scope)); path.chmod(0o644)
            bindings.append(s.UnitBinding(role, hashlib.sha256(fragment).hexdigest()))
        command('systemctl', 'daemon-reload'); self.activate_fixture()
        for unit in self.units[2:]: command('systemctl', 'start', unit)
        _, _, raw = self.request(); session = self.root / 'data/sessions' / ('sess_' + json.loads(raw)['cookie'])
        before = session.read_bytes(), session.stat().st_mtime_ns
        with s.SystemDrain(self.scope, tuple(bindings)).acquire(confirmed=True) as lease:
            report = lease.report(); self.assertTrue(report['cgroup_empty_verified'])
            self.assertFalse(report['system_wiring_verified'])
            self.assertEqual((session.read_bytes(), session.stat().st_mtime_ns), before)
            for unit in self.units:
                command('systemctl', 'start', unit); self.assertEqual(s._show(unit)['ActiveState'], 'inactive')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); source = quality.snapshot(ROOT)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HttpRuntimeLive)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Generated dedicated Apache FPM runtime staging', 'tests': result.testsRun,
        'expected': 12, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 12 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'web_application_qualified': False,
        'native_session_cleaner_qualified': False, 'service_activation_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'HTTP-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report)); sys.exit(0 if report['status'] == 'PASS' else 1)
