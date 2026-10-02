"""Two-domain, real NGINX composition in disposable system CI only.

Self-signed fixture certificates and recording upstreams qualify composition,
not public ACME, application authentication or the future systemd handoff.
"""
import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from installer.shared_mobile_tls import SharedMobileTLS, CERT_NAME
from installer.mobile_ingress import ROUTES
from installer.public_tls_profile import CERT_NAME as WEB_CERT_NAME
from installer.application_plan import FreshProfile
from test_public_tls import profile
from test_shared_mobile_tls import gateway
from scripts import quality

WEB = 'hestia.example.test'
MOBILE = 'mobile.hestia.test'
TOKEN = 'A' * 43


class Backend(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def serve(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        value = {'backend': self.server.label, 'path': self.path, 'method': self.command,
            'peer': self.client_address[0], 'headers': dict(self.headers),
            'body_sha256': hashlib.sha256(body).hexdigest()}
        self.server.received.append(value)
        raw = json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(raw)))
        self.send_header('Content-Type', 'application/json'); self.end_headers()
        if self.command != 'HEAD': self.wfile.write(raw)
    do_GET = do_HEAD = do_POST = serve


class SharedMobileTLSLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SHARED_MOBILE_TLS_TEST') != '1' or os.geteuid() != 0 \
                or not Path('/.dockerenv').exists() or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Disposable root systemd CI opt-in required')
        value = profile(); instance = uuid.uuid4().hex
        value['boot']['application'] = {'instance': instance, 'configuration': {'web': FreshProfile(instance).web(WEB)}}
        value['choices']['networks'] = ['127.0.0.11/32']
        cls.candidate = SharedMobileTLS(value, gateway(), ('127.0.0.10/32',))
        subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
            '--shell', '/usr/sbin/nologin', cls.candidate.web.identity.user], check=True, capture_output=True, timeout=10)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['/usr/sbin/userdel', cls.candidate.web.identity.user], check=True, capture_output=True, timeout=10)

    def setUp(self):
        self.c = self.candidate; self.root = self.c.web.layout.root
        self.assertFalse(self.root.exists()); self.root.mkdir(mode=0o755)
        self.processes = {}; self.backends = {}; self.threads = []
        self.addCleanup(self.cleanup)
        self.globals = {str(p): p.read_bytes() for p in (Path('/etc/nginx/nginx.conf'), Path('/etc/apache2/apache2.conf'))}
        self.c.web.root.mkdir(parents=True); self.c.root.mkdir(parents=True)
        self.web_live = self.c.web.acme_root / 'live' / WEB_CERT_NAME
        self.mobile_live = self.c.acme_root / 'live' / CERT_NAME
        self.issue(self.web_live, WEB); self.issue(self.mobile_live, MOBILE)
        for label, public in (('web', self.c.web.public), ('mobile', self.c.public)):
            challenge = public / 'htdocs/.well-known/acme-challenge'; challenge.mkdir(parents=True)
            (challenge / TOKEN).write_text(label + '-challenge')
        for label, port in (('web', 9080), ('mobile', 9083)):
            backend = ThreadingHTTPServer(('127.0.0.1', port), Backend)
            backend.label = label; backend.received = []; self.backends[label] = backend
            thread = threading.Thread(target=backend.serve_forever, daemon=True); thread.start(); self.threads.append(thread)
        self.write_configs(ready=True)
        for role, port in (('http', 80), ('https', 443)):
            self.assert_config(role)
            self.processes[role] = subprocess.Popen(self.argv(role), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.until(lambda: self.listening(port))

    def issue(self, live, hostname):
        live.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
            '-nodes', '-days', '1', '-subj', '/CN=' + hostname, '-addext', 'subjectAltName=DNS:' + hostname,
            '-keyout', str(live / 'privkey.pem'), '-out', str(live / 'fullchain.pem')],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        (live / 'privkey.pem').chmod(0o600)

    def cleanup(self):
        for process in self.processes.values():
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
        for backend in self.backends.values(): backend.shutdown(); backend.server_close()
        for thread in self.threads: thread.join(timeout=2)
        self.assertEqual(self.globals, {n: Path(n).read_bytes() for n in self.globals})
        shutil.rmtree(self.root)

    def until(self, predicate):
        for _ in range(150):
            if predicate(): return
            time.sleep(.02)
        self.fail('NGINX transition did not complete')

    def listening(self, port):
        self.assertTrue(all(p.poll() is None for p in self.processes.values()))
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.1): return True
        except OSError: return False

    def config(self, role): return self.c.root / ('shared-' + role + '.conf')
    def argv(self, role): return ['/usr/sbin/nginx', '-p', str(self.root) + '/', '-c', str(self.config(role))]
    def write_configs(self, *, ready):
        for role in ('http', 'https'): self.config(role).write_bytes(self.c.nginx(role, mobile_ready=ready))
    def assert_config(self, role, valid=True):
        result = subprocess.run(self.argv(role) + ['-t'], capture_output=True, timeout=5)
        self.assertEqual(result.returncode == 0, valid, result.stderr.decode())
    def reload(self, role):
        self.assert_config(role); self.processes[role].send_signal(signal.SIGHUP)

    def request(self, host, path='/health', *, tls=True, sni=None, source=None, method='GET', body=None, headers=()):
        source = source or ('127.0.0.11' if host == WEB else '127.0.0.10')
        rows = [('Host', host), ('Connection', 'close'), *headers]
        if body is not None: rows += [('Content-Length', str(len(body))), ('Content-Type', 'application/json')]
        raw = (method + ' ' + path + ' HTTP/1.1\r\n' + ''.join(k + ': ' + v + '\r\n' for k, v in rows) + '\r\n').encode() + (body or b'')
        with socket.create_connection(('127.0.0.1', 443 if tls else 80), timeout=5, source_address=(source, 0)) as sock:
            if tls:
                actual_sni = sni if sni is not None else host
                if actual_sni == host and host in (WEB, MOBILE):
                    live = self.web_live if host == WEB else self.mobile_live
                    context = ssl.create_default_context(cafile=str(live / 'fullchain.pem'))
                else: context = ssl._create_unverified_context()
                with context.wrap_socket(sock, server_hostname=actual_sni or None) as stream:
                    peer = stream.getpeercert(binary_form=True)
                    stream.sendall(raw); response = http.client.HTTPResponse(stream, method=method); response.begin()
                    return response.status, dict(response.getheaders()), response.read(), peer
            sock.sendall(raw); response = http.client.HTTPResponse(sock, method=method); response.begin()
            return response.status, dict(response.getheaders()), response.read(), None

    def test_shared_listeners_use_distinct_certificates_and_upstreams(self):
        web = self.request(WEB, '/login.php'); mobile = self.request(MOBILE)
        self.assertEqual((web[0], mobile[0]), (200, 200)); self.assertNotEqual(web[3], mobile[3])
        for result, label, peer in ((web, 'web', '127.0.0.2'), (mobile, 'mobile', '127.0.0.3')):
            value = json.loads(result[2]); self.assertEqual((value['backend'], value['peer']), (label, peer))
        self.assertEqual(json.loads(web[2])['headers']['X-Forwarded-Proto'], 'https')
        self.assertEqual(json.loads(mobile[2])['headers']['X-Hestia-Client-IP'], '127.0.0.10')
        self.assertEqual(len(self.processes), 2)

    def test_host_sni_crossovers_unknown_and_absent_sni_reach_neither_backend(self):
        before = {k: len(v.received) for k, v in self.backends.items()}
        for host, sni in ((WEB, MOBILE), (MOBILE, WEB), ('foreign.test', WEB),
                          (WEB, 'foreign.test'), (MOBILE, 'foreign.test'), (WEB, ''), (MOBILE, '')):
            with self.subTest(host=host, sni=sni): self.assertEqual(self.request(host, sni=sni)[0], 421)
        self.assertEqual(before, {k: len(v.received) for k, v in self.backends.items()})

    def test_client_allowlists_remain_independent_and_cannot_be_spoofed(self):
        for host, source in ((WEB, '127.0.0.10'), (MOBILE, '127.0.0.11')):
            before = sum(len(b.received) for b in self.backends.values())
            self.assertEqual(self.request(host, source=source, headers=[('X-Forwarded-For', '127.0.0.11'),
                ('X-Hestia-Client-IP', '127.0.0.10')])[0], 403)
            self.assertEqual(sum(len(b.received) for b in self.backends.values()), before)
        self.assertEqual(self.request(WEB)[0], 200); self.assertEqual(self.request(MOBILE)[0], 200)

    def test_http01_uses_separate_roots_and_mobile_canonical_token_paths(self):
        path = '/.well-known/acme-challenge/' + TOKEN
        for host, label in ((WEB, 'web'), (MOBILE, 'mobile')):
            result = self.request(host, path, tls=False, source='127.0.0.12')
            self.assertEqual((result[0], result[2]), (200, (label + '-challenge').encode()))
        for bad in (path + '?x=1', path + '?', path.replace('AAA', '%41AA', 1),
                    path.replace('/.well-known/', '//.well-known/'), path + '/extra', '/.well-known/acme-challenge/short'):
            self.assertEqual(self.request(MOBILE, bad, tls=False)[0], 404)
        self.assertEqual(self.request(MOBILE, path, tls=False, method='POST')[0], 405)
        self.assertEqual(self.request('foreign.test', path, tls=False)[0], 421)
        self.assertFalse(any(b.received for b in self.backends.values()))

    def test_challenge_then_ready_reload_keeps_web_available_without_mobile_certificate(self):
        self.write_configs(ready=False)
        for role in ('http', 'https'): self.reload(role)
        self.until(lambda: self.request(MOBILE, tls=False)[0] == 503)
        # Connect using the Web trust anchor while Mobile TLS has no vhost.
        self.until(lambda: self.request(MOBILE, sni=WEB)[0] == 421)
        backup = self.mobile_live.with_name('held-mobile'); self.mobile_live.rename(backup)
        try:
            for role in ('http', 'https'): self.assert_config(role)
            self.assertEqual(self.request(WEB, '/login.php')[0], 200)
            self.assertEqual(self.request(MOBILE, '/.well-known/acme-challenge/' + TOKEN, tls=False)[0], 200)
        finally: backup.rename(self.mobile_live)
        self.write_configs(ready=True)
        for role in ('https', 'http'): self.reload(role)
        self.until(lambda: self.request(MOBILE, tls=False)[0] == 308)
        self.until(lambda: self.request(MOBILE)[0] == 200)
        self.assertEqual(self.request(WEB)[0], 200)

    def test_mobile_certificate_rotation_reload_preserves_web_certificate_and_master(self):
        before = self.request(WEB)[3]; old_mobile = self.request(MOBILE)[3]
        pids = {role: p.pid for role, p in self.processes.items()}
        self.issue(self.mobile_live, MOBILE); self.reload('https')
        # During a graceful reload the old worker may serve one more connection;
        # use TLS without trust validation only for the rotation observation.
        self.until(lambda: self.request(MOBILE, sni=WEB)[0] == 421)
        def renewed():
            try: result = self.request(MOBILE)
            except ssl.SSLCertVerificationError: return False
            return result[0] == 200 and result[3] != old_mobile
        self.until(renewed)
        self.assertEqual(self.request(WEB)[3], before)
        self.assertEqual({role: p.pid for role, p in self.processes.items()}, pids)
        self.assertEqual(self.config('https').read_bytes(), self.c.nginx('https', mobile_ready=True))

    def test_invalid_mobile_certificate_configtest_leaves_existing_web_and_mobile_live(self):
        original = self.mobile_live / 'privkey.pem'; held = self.mobile_live / 'held.pem'; original.rename(held)
        try:
            self.assert_config('https', valid=False)
            self.assertEqual(self.request(WEB)[0], 200); self.assertEqual(self.request(MOBILE)[0], 200)
            self.assertTrue(all(p.poll() is None for p in self.processes.values()))
        finally: held.rename(original)

    def test_mobile_backend_outage_does_not_interrupt_web(self):
        self.backends['mobile'].shutdown(); self.backends['mobile'].server_close()
        self.assertEqual(self.request(MOBILE)[0], 502)
        self.assertEqual(self.request(WEB, '/login.php')[0], 200)

    def test_all_mobile_routes_remain_bound_to_gateway_inside_shared_configuration(self):
        for route in ROUTES:
            for method in route.methods:
                body = b'{}' if route.body_limit else None
                headers = [('Origin', 'https://' + MOBILE)] if route.origin_required else []
                result = self.request(MOBILE, route.path, method=method, body=body, headers=headers)
                self.assertEqual(result[0], 200, (route.path, method))
        self.assertEqual(len(self.backends['mobile'].received), sum(len(r.methods) for r in ROUTES))
        self.assertFalse(self.backends['web'].received)

    def test_redirects_are_canonical_and_preserve_paths_without_cross_domain_routing(self):
        for host in (WEB, MOBILE):
            result = self.request(host, '/page?view=1', tls=False)
            self.assertEqual(result[0], 308); self.assertEqual(result[1]['Location'], 'https://' + host + '/page?view=1')
        self.assertFalse(any(b.received for b in self.backends.values()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True); args = parser.parse_args()
    source = quality.snapshot(ROOT); suite = unittest.defaultTestLoader.loadTestsFromTestCase(SharedMobileTLSLive)
    result = unittest.TextTestRunner(verbosity=2).run(suite); stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Shared Web/Mobile real NGINX/TLS', 'tests': result.testsRun, 'expected': 10,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 10 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'domains': 2, 'routes': 63,
        'backends': 'recording fixtures', 'gateway_application_qualified': False,
        'public_acme_delivered': False, 'service_handoff_delivered': False, 'phase6_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'SHARED-MOBILE-TLS-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
