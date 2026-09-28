#!/usr/bin/env python3
"""Final public HTTPS qualification on disposable Debian 13 and private ACME.

Acquired application/SQL/package campaigns are fixture setup only. The actual
5D6 boot bundle is retained. No production certificate or private key artifact.
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import http.client
import http.cookiejar
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
import traceback
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import acme_packages_systemd as acquired
import mariadb_wizard_systemd as fixture
from github_fixture import confirm
from test_application_plan import setup_payload
from installer import public_tls_runtime as n
from installer import http_drain, provisioned_backup
from installer.database_step import SqlAuthorityCredentials

EVIDENCE = fixture.EVIDENCE
PHASE = None


def runtime(service): return service.public_tls.engine(service.engine.report())[1]


def setup():
    acquired.setup()
    service = fixture.service()
    try:
        document = service.execute('acme-packages.install.plan', {'acquisition_sha256': service.acme_packages.journals['acquire'].read()['plan_sha256']})['acme_packages']['installation']
        result = service.execute('acme-packages.install.apply', confirm(document))['acme_packages']['installation']
        assert result['state'] == 'DONE', result
        before = acquired.snapshot(service)
        before['parents']['acme'] = hashlib.sha256(service.acme_packages.journals['install'].path.read_bytes()).hexdigest()
        (EVIDENCE / 'before-public.json').write_bytes(quality.encode(before))
        (EVIDENCE / 'public-package-inventory.json').write_bytes(quality.encode(service.acme_packages.packages()._installed()))
    finally: service.close()


def serve():
    original = n.PublicOperation.apply
    def invoke(operation, context):
        try:
            result = original(operation, context)
            if operation.phase == 'certificate': raise OSError('Disposable lost certificate reply')
            return result
        except Exception:
            with (EVIDENCE / 'public-operation-traceback.txt').open('a') as output: traceback.print_exc(file=output)
            raise
    with patch.object(n.PublicOperation, 'apply', invoke): fixture.serve()


def request(path='/login.php', *, tls=True, source='127.0.0.1', headers=None):
    host = 'hestia.example.test'
    if tls:
        context = ssl.create_default_context(); connection = http.client.HTTPSConnection(host, 443, context=context, timeout=15)
        raw = socket.create_connection(('127.0.0.1', 443), timeout=15, source_address=(source, 0))
        try: connection.sock = context.wrap_socket(raw, server_hostname=host)
        except Exception: raw.close(); raise
    else: connection = http.client.HTTPConnection('127.0.0.1', 80, source_address=(source, 0), timeout=10)
    try:
        connection.request('GET', path, headers={'Host': host, 'Connection': 'close', **(headers or {})})
        response = connection.getresponse(); return response.status, response.read(), dict(response.getheaders())
    finally: connection.close()


def login(case):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context()), urllib.request.HTTPCookieProcessor(jar))
    url = 'https://hestia.example.test'
    with opener.open(url + '/login.php', timeout=10) as response: body = response.read().decode()
    token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', body).group(1)
    credentials = setup_payload()['credentials']
    wire = urllib.parse.urlencode({'csrf_token': token, 'identifier': 'admin@example.test', 'password': credentials['admin_password']}).encode()
    with opener.open(url + '/login.php', data=wire, timeout=10) as response:
        case.assertEqual(response.status, 200); case.assertNotIn('/login.php', response.geturl())
    case.assertTrue(any(cookie.secure for cookie in jar))
    with opener.open(url + '/logout.php', timeout=10) as response: case.assertIn('/login.php', response.geturl())


class Browser(unittest.TestCase):
    def test_public_consent_reload_lost_certificate_reply_and_completion(self):
        from playwright.sync_api import sync_playwright, expect
        expect.set_options(timeout=240000)
        info = json.loads(fixture.CONNECTION.read_bytes())
        with sync_playwright() as pw, ExitStack() as cleanup:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage']); cleanup.callback(browser.close)
            context = browser.new_context(ignore_https_errors=True, viewport={'width': 1366, 'height': 900}); cleanup.callback(context.close)
            page = context.new_page(); page.set_default_timeout(240000)
            cleanup.callback(page.screenshot, path=str(EVIDENCE / 'public-tls-wizard.png'), full_page=True)
            base = 'https://127.0.0.1:' + str(info['port'])
            def state(): return context.request.get(base + '/api/wizard/state').json()['public_tls']
            cleanup.callback(lambda: (EVIDENCE / 'browser-public-final.json').write_bytes(quality.encode(state())))
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base); page.locator('#bootstrap-code').fill(info['code']); page.locator('#bootstrap-form button[type=submit]').click()
            page.locator('#public-tls-email').fill('operator@example.test')
            page.locator('#public-tls-networks').fill('172.30.85.10/32, 172.30.85.30/32')
            page.locator('#plan-public-tls').click(); expect(page.locator('#public-tls-state')).to_have_attribute('data-state', 'PLANNED')
            before = state(); self.assertFalse(before['phase5_complete'])
            page.reload(); self.assertEqual(state(), before)
            page.locator('#apply-public-tls').click(); page.keyboard.press('Escape'); self.assertEqual(state(), before)
            page.locator('#apply-public-tls').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#public-tls-state')).to_have_attribute('data-state', 'FAILED')
            self.assertEqual(next(s['name'] for s in state()['installation']['steps'] if s['state'] == 'FAILED'), 'web.public.certificate')
            page.reload(); page.locator('#retry-public-tls').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#public-tls-state')).to_have_attribute('data-state', 'PLANNED')
            page.locator('#resume-public-tls').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#public-tls-state')).to_have_attribute('data-state', 'DONE')
            page.reload(); expect(page.locator('#public-tls-state')).to_have_attribute('data-state', 'DONE')
            self.assertTrue(state()['phase5_complete']); self.assertEqual(errors, [])
            page.locator('#check-public-tls').click()
            expect(page.locator('#public-tls')).to_contain_text('HTTPS disponible')


class Verify(unittest.TestCase):
    def test_public_security_real_login_renewal_reload_and_preserved_parents(self):
        service = fixture.service(); self.addCleanup(service.close); r = runtime(service)
        r.probe(); login(self)
        before = json.loads((EVIDENCE / 'before-public.json').read_bytes()); after = acquired.snapshot(service)
        for key in ('administrator', 'units', 'boot_code', 'boot_profile_sha256'): self.assertEqual(before[key], after[key])
        for key in after['parents']: self.assertEqual(before['parents'][key], after['parents'][key])
        self.assertEqual(before['parents']['acme'], hashlib.sha256(service.acme_packages.journals['install'].path.read_bytes()).hexdigest())
        self.assertEqual(json.loads((EVIDENCE / 'public-package-inventory.json').read_bytes()), service.acme_packages.packages()._installed())
        for unit, value in before['invocations'].items():
            if unit != r.http.unit('apache'): self.assertEqual(value, after['invocations'][unit])
        self.assertEqual(request(tls=False)[0], 308)
        self.assertEqual(request(tls=False, headers={'Host': 'foreign.example.test'})[0], 421)
        self.assertEqual(request(source='127.0.0.3', headers={'X-Forwarded-For': '127.0.0.1', 'X-Real-IP': '127.0.0.1'})[0], 403)
        for path in ('/.git/config', '/includes/config.php', '/sql/schema.sql', '/install.php', '/uploads/ged_documents/private.pdf'):
            self.assertEqual(request(path)[0], 403, path)
        self.assertEqual(request(headers={'Forwarded': 'for=198.51.100.2;proto=http', 'X-Forwarded-Proto': 'http', 'X-Forwarded-For': '198.51.100.2'})[0], 200)
        # Explicit fixture renewal exercises real ACME a second time. Then the
        # exact installed timer service validates and reloads the changed leaf.
        live = r.acme_root / 'live/hestia-web/cert.pem'; old = live.read_bytes()
        pid = r.systemctl('show', 'https')['MainPID']
        n.command([*r.certbot(renew=True), '--force-renewal'], timeout=840)
        self.assertNotEqual(old, live.read_bytes()); r.certificate()
        r.systemctl('start', 'renew'); self.assertEqual(pid, r.systemctl('show', 'https')['MainPID'])
        expected = ssl.PEM_cert_to_DER_cert(live.read_text())
        for _ in range(30):
            with socket.create_connection(('127.0.0.1', 443)) as raw, ssl.create_default_context().wrap_socket(raw, server_hostname=r.hostname) as tls:
                if tls.getpeercert(binary_form=True) == expected: break
            time.sleep(.2)
        else: self.fail('Reload did not publish renewed certificate')
        r.probe()
        scope, drain_profile = http_drain.HttpDrain(r.http, cleaner=r.boot.activation.cleaner)._audit()
        self.assertEqual(json.loads(drain_profile)['public_ingress']['profile_sha256'], r.digest)
        (EVIDENCE / 'before-public-boot.json').write_bytes(quality.encode(acquired.snapshot(service)))
        (EVIDENCE / 'public-journal.json').write_bytes(service.public_tls.journal.path.read_bytes())


class Restart(unittest.TestCase):
    def test_new_pid1_restores_verified_public_tls_and_real_administrator_login(self):
        service = fixture.service(); self.addCleanup(service.close); r = runtime(service)
        before = json.loads((EVIDENCE / 'before-public-boot.json').read_bytes()); after = acquired.snapshot(service)
        self.assertNotEqual(before['pid1_start'], after['pid1_start'])
        for key in ('parents', 'administrator', 'units', 'boot_code', 'boot_profile_sha256'): self.assertEqual(before[key], after[key])
        self.assertEqual(service.public_tls.journal.path.read_bytes(), (EVIDENCE / 'public-journal.json').read_bytes())
        r.probe(); login(self)
        self.assertEqual(r.systemctl('show', 'timer')['ActiveState'], 'active')


class Backup(unittest.TestCase):
    def test_native_backup_keeps_public_backend_bound_and_closed_under_maintenance(self):
        service = fixture.service(); self.addCleanup(service.close); r = runtime(service)
        payload = deepcopy(service.application.read()['configuration'])
        payload.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'},
                       secrets={'database_password': setup_payload()['credentials']['database_password'], 'admin_password': '', 'openai_api_key': ''})
        payload['database']['mode'] = 'existing_local'
        backup_root = r.layout.root / 'final-backup'; backup_root.mkdir(mode=0o700)
        authority = SqlAuthorityCredentials(service.mariadb.runtime().authority_user, fixture.PASSWORD)
        operation = provisioned_backup.ProvisionedBackup(replace(r.layout.runtime(), timeout_seconds=120), fixture.TARGET, r.http, r.boot.activation.cleaner)
        result = operation.create_and_verify(payload, authority, config_root=r.layout.config_root, backup_root=backup_root,
            confirmed=True, allow_global_read_lock=True).report()
        self.assertEqual(result['state'], 'PROVISIONED_BACKUP_RESTORE_VERIFIED', result)
        self.assertTrue(result['provisioned_services_drained'] and result['sql_read_fence_verified'])
        self.assertTrue(result['database_restoration_verified'] and result['registered_data_restoration_verified'])
        self.assertFalse(result['activity_resumed'])
        scope = r.http._scope(r.layout.identity.account()); self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertEqual(request('/.well-known/acme-challenge/absent', tls=False)[0], 404)
        self.assertEqual(request()[0], 502)
        (EVIDENCE / 'public-backup-report.json').write_bytes(quality.encode(result))


class GatedBoot(unittest.TestCase):
    def test_maintenance_boot_keeps_backend_closed_and_http01_available(self):
        service = fixture.service(); self.addCleanup(service.close); r = runtime(service)
        self.assertTrue(r.running('http')); self.assertFalse(r.boot.activation.running('apache'))
        scope = r.http._scope(r.layout.identity.account()); self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertEqual(request('/.well-known/acme-challenge/absent', tls=False)[0], 404)
        before = service.public_tls.journal.path.read_bytes()
        result = service.execute('public-tls.check', confirm(service.public_tls.journal.read()))['public_tls']
        self.assertEqual(result['availability']['state'], 'PUBLIC_TLS_CHECK_FAILED')
        self.assertEqual(before, service.public_tls.journal.path.read_bytes()); self.assertTrue(result['phase5_complete'])
        # Missing enrollment proof remains manual and cannot silently restart.
        proof = r.root / 'enable.json'; saved = proof.read_bytes(); proof.unlink()
        try:
            with self.assertRaises(Exception): r.worker('http')
            engine, _ = service.public_tls.engine(service.engine.report())
            doc = engine.report(); spec = next(s for s in doc['plan']['steps'] if s['name'] == 'web.public.enable')
            record = next(s for s in doc['steps'] if s['name'] == spec['name'])
            self.assertEqual(engine.registry.get(spec).recover(engine._context(doc, spec, record), 'apply').decision, n.RecoveryDecision.MANUAL)
        finally: proof.write_bytes(saved); proof.chmod(0o600)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', choices=('setup', 'serve', 'browser', 'verify', 'restart', 'backup', 'gated-boot'), required=True)
    PHASE = parser.parse_args().phase
    if os.environ.get('HESTIA_PUBLIC_TLS_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Explicit disposable opt-in required')
    if PHASE not in ('browser',) and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('Real disposable systemd required')
    if PHASE == 'setup': setup(); sys.exit(0)
    if PHASE == 'serve': serve(); sys.exit(0)
    before = quality.snapshot(ROOT)
    case = {'browser': Browser, 'verify': Verify, 'restart': Restart, 'backup': Backup, 'gated-boot': GatedBoot}[PHASE]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    stable = before == quality.snapshot(ROOT); passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    report = {'suite': 'public-tls-' + PHASE, 'tests': result.testsRun, 'expected': 1, 'failures': len(result.failures),
        'errors': len(result.errors), 'skips': len(result.skipped), 'status': 'PASS' if passed else 'FAIL', 'source_stable': stable, 'source_files': len(before),
        'acme_authority': 'private disposable Pebble, not production Lets Encrypt'}
    (EVIDENCE / ('public-tls-' + PHASE + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-public-' + PHASE + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
