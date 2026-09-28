#!/usr/bin/env python3
"""Pinned Web deployment + SQL finalization + actual Apache/FPM/TLS fixture.

Service activation remains fixture-owned. No mutable business storage or
wizard/upgrade qualification is inferred from these actual login/session tests.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import finalization_mariadb as final_tests
import database_step_mariadb as db_tests
from http_runtime_systemd import command, until
from installer import finalization as f
from installer import http_runtime as h
from installer import php_transport as p
from installer import session_cleaner as cleaner
from installer import system_drain as drain
from installer import web_deployment as deploy
from installer.proxy_ingress import ProxyIngress

WEB = None


class DeployedWebLive(final_tests.FinalizationIntegration):
    release_commit = f.WEB_COMMIT
    external_uploads = False
    sealed_maintenance = False
    proxy_user = None
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_DEPLOYED_WEB_TEST') != '1' or os.geteuid() != 0 \
                or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable systemd fixture required')
        if WEB is None or not Path('/usr/sbin/php-fpm8.4').is_file():
            raise RuntimeError('Exact Web and official PHP 8.4 required')
        final_tests.WEB = WEB
        super().setUpClass()

    def setUp(self):
        # Reuse only the isolated SQL fixture; copying code is now PRODUCT work.
        db_tests.DatabaseStepIntegration.setUp(self)
        self.nginx = None; self.http_runtime = None; self.collector = None; self.units = []
        self.http_root = Path('/var/lib/hestia-web-live-' + os.urandom(16).hex())
        self.addCleanup(self.stop_services)
        self.assertEqual(list((self.webroot / 'includes').iterdir()), [])
        (self.webroot / 'includes').rmdir(); self.webroot.rmdir()
        release = f.get_release(self.release_commit)
        self.deployment = deploy.WebDeployment(deploy.DeploymentSpec(WEB, self.webroot, self.root / 'deployment', commit=release.commit))
        self.deployed = self.deployment.create(confirmed=True)
        self.assertEqual(self.deployed['files'], release.files)
        self.assertEqual(self.deployed['source_tree'], release.tree)
        self.final = f.FinalizationStep(self.runtime, WEB, repository=p.WEB_REPOSITORY, commit=release.commit)
        self.global_paths = [Path('/etc/nginx/nginx.conf'), Path('/etc/apache2/apache2.conf'),
            Path('/etc/php/8.4/fpm/php.ini'), Path('/etc/php/8.4/fpm/pool.d/www.conf'), Path('/usr/lib/php/sessionclean')]
        self.global_before = {str(path): path.read_bytes() for path in self.global_paths}

    def tearDown(self):
        try:
            self.stop_services()
            self.assertEqual({str(path): path.read_bytes() for path in self.global_paths}, self.global_before)
        finally: super().tearDown()

    def stop_services(self):
        if self.nginx is not None:
            self.nginx.terminate()
            try: self.nginx.wait(timeout=8)
            except subprocess.TimeoutExpired: self.nginx.kill(); self.nginx.wait(timeout=3)
            self.nginx = None
        for unit in self.units:
            command('systemctl', 'stop', unit, check=False)
            command('systemctl', 'reset-failed', unit, check=False)
            path = drain.UNIT_ROOT / unit
            if path.exists(): path.unlink()
            shutil.rmtree(drain.UNIT_ROOT / (unit + '.d'), ignore_errors=True)
        if self.units: command('systemctl', 'daemon-reload')
        self.units = []
        shutil.rmtree(self.http_root, ignore_errors=True)

    def start(self):
        result = self.finish()
        self.assertFalse(result['result']['application_installed'])
        self.assertEqual(self.observe()['state'], 'WEB_FRESH_FINALIZED')
        # Finalization has added sealed pointers: the old pure-copy receipt must
        # no longer claim this is still the pristine source tree.
        with self.assertRaises(deploy.WebDeploymentError): self.deployment.observe()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); self.tls_port = listener.getsockname()[1]
        instance = json.loads((self.directory / 'seal.json').read_text())['instance']
        self.policy = ProxyIngress('127.0.0.2', ('127.0.0.1/32',))
        self.spec = h.RuntimeSpec(instance, self.http_root, self.webroot, self.web.pw_name,
                                  self.payload['web']['hostname'], port, '8.4', self.policy,
                                  external_uploads=self.external_uploads,
                                  maintenance_directory=self.directory / 'maintenance' if self.external_uploads or self.sealed_maintenance else None)
        self.http_runtime = h.HttpRuntime(self.spec)
        self.scope = self.http_runtime._scope(self.web)
        self.units = [self.http_runtime.unit(role) for role in ('apache', 'php')]
        self.assertEqual(self.http_runtime.create(confirmed=True)['state'], 'HTTP_RUNTIME_STAGED')
        self.collector = cleaner.SessionCleaner(self.http_runtime)
        self.units += [self.collector.timer, self.collector.unit]
        self.assertEqual(self.collector.create(confirmed=True)['state'], 'SESSION_CLEANER_STAGED')
        # Only this opt-in fixture lifts admission and starts the dedicated pair.
        with self.scope.recover(self.scope.observe()['lease_id'], confirmed=True) as lease: lease.resume(confirmed=True)
        command('systemctl', 'start', self.http_runtime.unit('php'))
        command('systemctl', 'start', self.http_runtime.unit('apache'))
        front = self.root / 'frontend'; front.mkdir(mode=0o755)
        cert, key = front / 'fixture.crt', front / 'fixture.key'
        command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=' + self.spec.hostname, '-addext', 'subjectAltName=IP:127.0.0.1,DNS:' + self.spec.hostname,
                '-keyout', str(key), '-out', str(cert)); key.chmod(0o600)
        config = front / 'nginx.conf'
        config.write_text(f'''user {self.proxy_user or self.web.pw_name};
pid {front}/nginx.pid;
error_log {front}/nginx.log warn;
events {{ worker_connections 32; }}
http {{ access_log off; client_body_temp_path {front}/body; proxy_temp_path {front}/proxy;
''' + self.policy.nginx_server(self.spec.hostname, port, tls_port=self.tls_port, certificate=cert,
                              private_key=key, listen_address='127.0.0.1') + '}\n')
        command('nginx', '-t', '-p', str(front) + '/', '-c', str(config))
        self.nginx = subprocess.Popen(['/usr/sbin/nginx', '-p', str(front) + '/', '-c', str(config), '-g', 'daemon off;'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))),
            urllib.request.HTTPCookieProcessor(self.cookies))
        self.url = 'https://127.0.0.1:' + str(self.tls_port)
        def ready():
            try: return self.request('/login.php')[0] == 200
            except (OSError, urllib.error.URLError): return False
        try: until(ready, timeout=12)
        except Exception:
            # Synthetic-only logs; secret values must never be copied out.
            for path in (front / 'nginx.log', self.http_root / 'log/apache.log', self.http_root / 'data/log/php.log'):
                if path.is_file():
                    text = path.read_text()[-4000:]
                    for secret in self.payload['secrets'].values():
                        if secret: text = text.replace(secret, '[REDACTED]')
                    print('Disposable Web service diagnostic:', text)
            raise

    def request(self, path, data=None, *, headers=None):
        wire = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(self.url + path, data=wire, headers={'Host': self.spec.hostname, **(headers or {})})
        try: response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as error: response = error
        with response: return response.status, response.read().decode('utf-8'), dict(response.headers), response.geturl()

    def login(self):
        status, body, _, _ = self.request('/login.php'); self.assertEqual(status, 200)
        token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', body).group(1)
        value = self.request('/login.php', {'csrf_token': token, 'identifier': self.payload['administrator']['email'],
                                           'password': self.payload['secrets']['admin_password']})
        self.assertEqual(value[0], 200); self.assertNotIn('/login.php', value[3])
        return value

    def session(self):
        values = [cookie.value for cookie in self.cookies if cookie.name == 'PHPSESSID']
        self.assertEqual(len(values), 1); self.assertRegex(values[0], r'^[A-Za-z0-9,-]+$')
        return self.http_root / 'data/sessions' / ('sess_' + values[0])

    def age_session(self, seconds):
        # Modify only the real fixture's session through PHP as its service UID.
        value = {'id': self.session().name[5:], 'age': seconds}
        code = "$v=json_decode(stream_get_contents(STDIN),true);session_id($v['id']);session_start();$_SESSION['_hestia_last_activity']=time()-$v['age'];session_write_close();"
        result = subprocess.run(['setpriv', '--reuid=' + str(self.web.pw_uid), '--regid=' + str(self.web.pw_gid),
            '--clear-groups', str(self.runtime.php), '-d', 'display_errors=0', '-d', 'log_errors=0',
            '-d', 'session.save_path=' + str(self.http_root / 'data/sessions'), '-r', code],
            input=json.dumps(value).encode(), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0); self.assertEqual(result.stderr, b'')

    def test_deployed_full_tree_matches_pin_before_sql_finalization(self):
        self.assertEqual(self.deployment.observe(), self.deployed)
        self.assertFalse((self.webroot / 'includes/db.php').exists())
        self.assertFalse((self.webroot / 'install.lock').exists())
        self.assertFalse(self.deployed['application_installed'])

    def test_deployed_admin_login_dashboard_logout_through_real_tls_services(self):
        self.payload['administrator']['first_name'] = 'Élise <&>🙂'
        self.start(); _, body, _, _ = self.login()
        self.assertIn('D&#039;Exemple', body); self.assertNotIn('Élise <&>', body)
        self.assertNotIn(self.payload['secrets']['admin_password'], body)
        self.assertIn('/login.php', self.request('/logout.php')[3])
        self.assertIn('/login.php', self.request('/index.php')[3])

    def test_deployed_csrf_cookie_and_https_security_headers_are_effective(self):
        self.start(); status, body, headers, _ = self.request('/login.php')
        self.assertEqual(status, 200); self.assertIn('max-age=', headers['Strict-Transport-Security'])
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        for cookie in self.cookies:
            if cookie.name == 'PHPSESSID':
                self.assertTrue(cookie.secure); self.assertTrue(cookie.has_nonstandard_attr('HttpOnly'))
                self.assertEqual(cookie.get_nonstandard_attr('SameSite'), 'Lax')
        denied = self.request('/login.php', {'csrf_token': 'wrong', 'identifier': self.payload['administrator']['email'],
                                           'password': self.payload['secrets']['admin_password']})
        self.assertEqual(denied[0], 403)
        self.assertNotIn(self.payload['secrets']['admin_password'], denied[1])
        self.assertIn('/login.php', self.request('/index.php')[3])

    def test_deployed_private_files_and_installer_stay_inaccessible(self):
        self.start()
        for path in ('/install.php', '/includes/db.php', '/includes/bootstrap.php', '/docs/SECURITY.md',
                     '/vendor/autoload.php', '/uploads/ged_documents/.htaccess', '/.gitignore'):
            with self.subTest(path=path): self.assertEqual(self.request(path)[0], 403)

    def test_deployed_actual_user_timeouts_one_four_eight_hours(self):
        self.start()
        for hours in (1, 4, 8):
            with self.subTest(hours=hours):
                self.cookies.clear()
                self.sql([f'UPDATE `{self.db}`.UserInfo SET session_timeout_hours={hours}'])
                self.login(); self.age_session(hours * 3600 - 60)
                self.assertNotIn('/login.php', self.request('/index.php')[3])
                self.age_session(hours * 3600 + 60)
                self.assertIn('/login.php', self.request('/index.php')[3])

    def test_deployed_collector_preserves_valid_eight_hour_session(self):
        self.start(); self.login(); current = self.session()
        self.age_session(7 * 3600); stamp = time.time() - 7 * 3600
        os.utime(current, (stamp, stamp))
        expired = current.parent / ('sess_' + 'a' * 26)
        expired.write_text('fixture|i:1;'); expired.chmod(0o600); os.chown(expired, self.web.pw_uid, self.web.pw_gid)
        old = time.time() - 13 * 3600; os.utime(expired, (old, old))
        command('systemctl', 'start', self.collector.unit)
        self.assertTrue(current.exists()); self.assertFalse(expired.exists())
        self.assertNotIn('/login.php', self.request('/index.php')[3])

    def test_deployed_maintenance_blocks_php_and_preserves_existing_session(self):
        self.start(); self.login(); path = self.session(); before = path.read_bytes(), path.stat().st_mtime_ns
        with self.scope.acquire(confirmed=True) as lease:
            self.assertEqual(self.request('/index.php')[0], 503)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            lease.resume(confirmed=True)
        self.assertNotIn('/login.php', self.request('/index.php')[3])

    def test_deployed_corrupt_activation_seal_returns_closed_failure(self):
        self.start(); path = self.directory / 'seal.json'; original = path.read_bytes()
        try:
            path.write_bytes(original + b' ')
            status, body, _, _ = self.request('/login.php'); self.assertEqual(status, 503)
            for secret in self.payload['secrets'].values():
                if secret: self.assertNotIn(secret, body)
        finally: path.write_bytes(original)
        self.assertEqual(self.request('/login.php')[0], 200)

    def test_deployed_remote_sql_tls_is_used_by_actual_fpm_web(self):
        self.remote(); self.start(); self.login()
        self.assertEqual(self.observe()['state'], 'WEB_FRESH_FINALIZED')
        self.assertTrue(self.payload['database']['tls_ca_file'])

    def test_deployed_web_identity_cannot_rewrite_code_and_data_limit_is_explicit(self):
        self.start()
        for path in (self.webroot / 'index.php', self.webroot / 'includes/db.php', self.webroot / 'uploads'):
            self.assertFalse(self.permission(self.web, '-w', path))
        for name in ('sessions', 'tmp', 'upload-tmp', 'imports', 'log'):
            self.assertTrue(self.permission(self.web, '-w', self.http_root / 'data' / name))
        self.assertFalse(self.deployed['writable_business_storage_ready'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True); args = parser.parse_args(); WEB = args.web
    source = quality.snapshot(ROOT)
    names = sorted(name for name in DeployedWebLive.__dict__ if name.startswith('test_deployed_'))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(DeployedWebLive(name) for name in names))
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Deployed pinned Web with real SQL Apache FPM TLS', 'tests': result.testsRun, 'expected': 10,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 10 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'web_commit': f.WEB_COMMIT, 'web_tree': deploy.WEB_TREE,
        'real_web_login_session_recipe': True, 'functional_session_policy_qualified': result.wasSuccessful(),
        'service_activation_delivered': False, 'writable_business_storage_ready': False,
        'complete_web_backup': False, 'application_installed': False, 'phase5_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'DEPLOYMENT-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
