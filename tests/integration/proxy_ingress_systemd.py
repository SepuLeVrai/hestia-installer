#!/usr/bin/env python3
"""Real offline TLS edge -> Apache -> FPM, optionally pinned Web helpers.

Only the fixture issues a certificate and starts services. No ACME, application
activation, real database or shared frontend configuration is involved.
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
import ssl
import subprocess
import sys
import tempfile
import unittest

from http_runtime_systemd import HttpRuntimeLive, command, until, h, s, quality, ROOT
from installer.proxy_ingress import ProxyIngress

WEB = None
SECURITY_SHA256 = '2abfe1185b81318c1e14b3a329eee78d0d0b69b30c7fdfb7cd4e703269690347'


class ProxyIngressLive(HttpRuntimeLive):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_PROXY_INGRESS_TEST') != '1' or os.geteuid() != 0 \
                or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell',
                '/usr/sbin/nologin', 'hestia-proxy-test')
        cls.account = pwd.getpwnam('hestia-proxy-test')
        cls.family = '8.4' if Path('/usr/sbin/php-fpm8.4').is_file() else '8.2'
        if WEB is not None:
            assert cls.family == '8.4'
            assert hashlib.sha256((WEB / 'includes/security.php').read_bytes()).hexdigest() == SECURITY_SHA256

    def setUp(self):
        super().setUp()
        self.policy = ProxyIngress('127.0.0.2', ('127.0.0.10/32', '2001:db8::/32'))
        self.spec = dataclasses.replace(self.spec, ingress=self.policy)
        self.runtime = h.HttpRuntime(self.spec)
        self.scope = self.runtime._scope(self.account)
        self.front = Path(tempfile.mkdtemp(prefix='hestia-proxy-fixture-', dir='/var/lib'))
        self.front.chmod(0o755)
        self.addCleanup(lambda: shutil.rmtree(self.front, ignore_errors=True))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); self.tls_port = listener.getsockname()[1]
        self.nginx = None
        self.addCleanup(self.stop_front)
        helpers = ''
        if WEB is not None:
            self.write('includes/security.php', (WEB / 'includes/security.php').read_text())
            helpers = "require __DIR__.'/includes/security.php'; security_headers();"
        self.write('index.php', '''<?php
''' + helpers + '''
$https = function_exists('security_request_is_https') ? security_request_is_https() : (($_SERVER['HTTPS']??'') === 'on');
$client = function_exists('client_ip') ? client_ip() : ($_SERVER['REMOTE_ADDR']??'');
session_set_cookie_params(['secure'=>$https,'httponly'=>true,'samesite'=>'Lax']);
session_start(); $_SESSION['proxy_fixture'] = 1;
echo json_encode(['client'=>$client,'https'=>$https,'remote'=>$_SERVER['REMOTE_ADDR']??'',
'authorization'=>$_SERVER['HTTP_AUTHORIZATION']??'', 'host'=>$_SERVER['HTTP_HOST']??'',
'forwarded'=>array_filter($_SERVER,fn($key)=>str_starts_with($key,'HTTP_X_FORWARDED_') || $key==='HTTP_FORWARDED' || $key==='HTTP_X_REAL_IP',ARRAY_FILTER_USE_KEY),
'proxy_ranges'=>getenv('HESTIA_TRUSTED_PROXIES'), 'ttl'=>ini_get('session.gc_maxlifetime')]);
''')
        self.global_paths.append(Path('/etc/nginx/nginx.conf'))
        self.global_before = {str(p): p.read_bytes() for p in self.global_paths}

    def stop_front(self):
        if self.nginx is not None:
            self.nginx.terminate()
            try: self.nginx.wait(timeout=8)
            except subprocess.TimeoutExpired: self.nginx.kill(); self.nginx.wait(timeout=3)

    def start(self):
        self.create()
        state = self.scope.observe()
        with self.scope.recover(state['lease_id'], confirmed=True) as lease: lease.resume(confirmed=True)
        command('systemctl', 'start', self.runtime.unit('php'))
        command('systemctl', 'start', self.runtime.unit('apache'))
        cert, key = self.front / 'fixture.crt', self.front / 'fixture.key'
        command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=runtime.hestia.test', '-addext', 'subjectAltName=DNS:runtime.hestia.test,IP:127.0.0.1',
                '-keyout', str(key), '-out', str(cert))
        key.chmod(0o600)
        value = f'''user {self.account.pw_name};
pid {self.front}/nginx.pid;
error_log {self.front}/nginx.log warn;
events {{ worker_connections 32; }}
http {{
  access_log off;
  client_body_temp_path {self.front}/body;
  proxy_temp_path {self.front}/proxy;
''' + self.policy.nginx_server(self.spec.hostname, self.port, tls_port=self.tls_port,
        certificate=cert, private_key=key, listen_address='127.0.0.1') + '}\n'
        config = self.front / 'nginx.conf'; config.write_text(value)
        result = command('nginx', '-t', '-p', str(self.front) + '/', '-c', str(config), check=False)
        if result.returncode: print('Fixture nginx configtest:', result.stderr.decode())
        self.assertEqual(result.returncode, 0)
        self.nginx = subprocess.Popen(['/usr/sbin/nginx', '-p', str(self.front) + '/', '-c', str(config),
                                       '-g', 'daemon off;'], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.context = ssl.create_default_context(cafile=str(cert))
        def ready():
            try: return self.edge()[0] == 200
            except OSError: return False
        try: until(ready)
        except Exception:
            for path in (self.front / 'nginx.log', self.root / 'log/apache.log', self.root / 'data/log/php.log'):
                if path.is_file(): print('Synthetic ingress log:', path.read_text()[-4000:])
            raise

    def edge(self, path='/', *, client='127.0.0.10', host=None, headers=None, context=None):
        conn = http.client.HTTPSConnection('127.0.0.1', self.tls_port, timeout=5,
                                           source_address=(client, 0), context=context or self.context)
        try:
            conn.request('GET', path, headers={'Host': host or self.spec.hostname, **(headers or {})})
            response = conn.getresponse(); return response.status, dict(response.getheaders()), response.read()
        finally: conn.close()

    def backend(self, *, peer='127.0.0.2', headers=None, path='/'):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5, source_address=(peer, 0))
        try:
            conn.request('GET', path, headers={'Host': self.spec.hostname, **(headers or {})})
            response = conn.getresponse(); return response.status, dict(response.getheaders()), response.read()
        finally: conn.close()

    def test_proxy_https_preserves_client_secure_session_and_bearer(self):
        self.start()
        status, headers, raw = self.edge(headers={'Authorization': 'Bearer synthetic-fixture'})
        value = json.loads(raw)
        self.assertEqual(status, 200); self.assertEqual(value['client'], '127.0.0.10')
        self.assertEqual(value['remote'], value['client']); self.assertTrue(value['https'])
        self.assertEqual(value['authorization'], 'Bearer synthetic-fixture')
        self.assertEqual(value['host'], self.spec.hostname)
        self.assertFalse(value['forwarded']); self.assertFalse(value['proxy_ranges'])
        self.assertEqual(value['ttl'], '43200')
        for flag in ('secure', 'httponly', 'samesite=lax'): self.assertIn(flag, headers['Set-Cookie'].lower())
        if WEB is not None: self.assertIn('max-age=', headers['Strict-Transport-Security'])

    def test_proxy_edge_replaces_all_client_supplied_forwarding_headers(self):
        self.start()
        status, _, raw = self.edge(headers={'X-Forwarded-For': '198.51.100.99, 127.0.0.10',
            'X-Forwarded-Proto': 'http', 'Forwarded': 'for=198.51.100.99;proto=http;host=evil.test',
            'X-Forwarded-Host': 'evil.test', 'X-Forwarded-Port': '80', 'X-Real-IP': '198.51.100.99'})
        value = json.loads(raw); self.assertEqual(status, 200)
        self.assertEqual(value['client'], '127.0.0.10'); self.assertTrue(value['https'])
        self.assertFalse(value['forwarded']); self.assertEqual(value['host'], self.spec.hostname)

    def test_proxy_disallowed_client_cannot_spoof_allowlist_at_edge(self):
        self.start()
        for headers in ({}, {'X-Forwarded-For': '127.0.0.10', 'Forwarded': 'for=127.0.0.10;proto=https'}):
            self.assertEqual(self.edge(client='127.0.0.11', headers=headers)[0], 403)

    def test_proxy_backend_also_applies_client_allowlist(self):
        self.start()
        for client in ('127.0.0.11', '198.51.100.25'):
            self.assertEqual(self.backend(headers={'X-Forwarded-For': client, 'X-Forwarded-Proto': 'https'})[0], 403)

    def test_proxy_undeclared_peer_cannot_supply_forwarding_headers(self):
        self.start()
        for peer in ('127.0.0.1', '127.0.0.10', '127.0.0.3'):
            self.assertEqual(self.backend(peer=peer, headers={'X-Forwarded-For': '127.0.0.10',
                                                             'X-Forwarded-Proto': 'https'})[0], 403)

    def test_proxy_missing_malformed_or_incomplete_envelope_is_closed(self):
        self.start()
        cases = ({}, {'X-Forwarded-For': '127.0.0.10'}, {'X-Forwarded-Proto': 'https'},
                 {'X-Forwarded-For': 'invalid', 'X-Forwarded-Proto': 'https'},
                 {'X-Forwarded-For': '127.0.0.2', 'X-Forwarded-Proto': 'https'},
                 {'X-Forwarded-For': '127.0.0.10', 'X-Forwarded-Proto': 'http'},
                 {'X-Forwarded-For': '127.0.0.10', 'X-Forwarded-Proto': 'http,https'},
                 {'X-Forwarded-For': '127.0.0.10, 198.51.100.4', 'X-Forwarded-Proto': 'https'})
        for headers in cases:
            with self.subTest(headers=headers): self.assertIn(self.backend(headers=headers)[0], (400, 403))

    def test_proxy_tls12_and_tls13_and_certificate_verification(self):
        self.start()
        for version in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
            context = ssl.create_default_context(cafile=str(self.front / 'fixture.crt'))
            context.minimum_version = context.maximum_version = version
            self.assertEqual(self.edge(context=context)[0], 200)
        with self.assertRaises(ssl.SSLCertVerificationError): self.edge(context=ssl.create_default_context())

    def test_proxy_wrong_host_is_denied_at_edge_and_backend(self):
        self.start()
        self.assertEqual(self.edge(host='evil.test')[0], 421)
        self.assertEqual(self.backend(headers={'Host': 'evil.test', 'X-Forwarded-For': '127.0.0.10',
                                                'X-Forwarded-Proto': 'https'})[0], 403)

    def test_proxy_private_paths_stay_denied_and_static_assets_work(self):
        self.start()
        for path in ('/install.php', '/includes/security.php', '/.env', '/dump.sql', '/uploads/run.php'):
            self.assertEqual(self.edge(path)[0], 403)
        self.assertEqual(self.edge('/style.css')[0], 200)

    def test_proxy_maintenance_remains_closed_through_tls(self):
        self.start()
        with self.scope.acquire(confirmed=True): self.assertEqual(self.edge()[0], 503)
        self.assertEqual(self.edge()[0], 503)

    def test_proxy_plan_drift_revokes_staging_without_global_changes(self):
        report = self.create(); self.assertFalse(report['application_installed'])
        changed = h.HttpRuntime(dataclasses.replace(self.spec, ingress=ProxyIngress('127.0.0.3', self.policy.client_networks)))
        with self.assertRaises(h.HttpRuntimeError): changed.observe()
        self.assertEqual(self.runtime.observe(), report)
        self.assertEqual({str(p): p.read_bytes() for p in self.global_paths}, self.global_before)

    def test_proxy_backend_listens_only_on_ipv4_loopback(self):
        self.start()
        addresses = [line.split()[1] for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                     if line.split()[3] == '0A' and int(line.split()[1].split(':')[1], 16) == self.port]
        self.assertEqual(addresses, ['0100007F:' + format(self.port, '04X')])

    def test_proxy_plain_http_cannot_reach_application_on_tls_port(self):
        self.start()
        conn = http.client.HTTPConnection('127.0.0.1', self.tls_port, timeout=5)
        try:
            conn.request('GET', '/', headers={'Host': self.spec.hostname})
            response = conn.getresponse(); self.assertEqual(response.status, 400)
            self.assertNotIn(b'"client"', response.read())
        finally: conn.close()

    def test_proxy_ipv6_client_identity_is_authorized_without_second_header_parse(self):
        self.start()
        status, _, raw = self.backend(headers={'X-Forwarded-For': '2001:db8::7', 'X-Forwarded-Proto': 'https',
            'Forwarded': 'for=198.51.100.25;proto=http'})
        self.assertEqual(status, 200); value = json.loads(raw)
        self.assertEqual(value['client'], '2001:db8::7'); self.assertTrue(value['https'])
        self.assertFalse(value['forwarded'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--web', type=Path); args = parser.parse_args(); WEB = args.web
    source = quality.snapshot(ROOT)
    # Explicit inventory: inherited runtime tests already have their own suite.
    names = sorted(x for x in ProxyIngressLive.__dict__ if x.startswith('test_proxy_'))
    suite = unittest.TestSuite(ProxyIngressLive(name) for name in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Real TLS proxy ingress', 'tests': result.testsRun, 'expected': 14,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 14 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'web_helpers_exercised': WEB is not None,
        'web_security_sha256': SECURITY_SHA256 if WEB is not None else None,
        'web_application_qualified': False, 'service_activation_delivered': False, 'acme_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'PROXY-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report)); sys.exit(0 if report['status'] == 'PASS' else 1)
