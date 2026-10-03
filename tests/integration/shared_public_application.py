#!/usr/bin/env python3
"""Real Web/Gateway/Certbot composition, exclusively in disposable Debian CI.

Public parent code is frozen separately. No ACME, SQL, Gateway, Apache or
systemd observation is mocked. Only a completed certificate reply is lost.
"""
import argparse
from contextlib import ExitStack
import hashlib
import http.client
import io
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import mariadb_wizard_systemd as fixture
import public_tls_systemd as public
from github_fixture import confirm
from installer import shared_public_runtime as native
from installer.gateway_service_probe import PATH

EVIDENCE = Path('/evidence')
ORIGIN = 'https://mobile.example.test'


def done(result):
    assert result['state'] == 'DONE', {k: result[k] for k in ('state', 'last_error_redacted')}
    return result


def setup():
    public.setup()
    service = fixture.service()
    try:
        parent = service.engine.report()
        frozen = Path('/opt/hestia-public-parent')
        code = {p.relative_to(frozen).as_posix(): p.read_bytes() for p in (frozen / 'installer').rglob('*')
            if p.is_file() and p.suffix in ('.py', '.php', '.json') and '__pycache__' not in p.parts}
        assert code and code['installer/private/shared_public_worker.py']
        with patch.object(native.boot, 'code_files', return_value=code):
            planned = service.execute('public-tls.plan', {'acme_sha256': service.acme_packages.journals['install'].read()['plan_sha256'],
                'choices': {'email': 'operator@example.test', 'access': 'allowlist', 'networks': ['172.30.85.10/32']}})['public_tls']['installation']
            done(service.execute('public-tls.apply', confirm(planned))['public_tls']['installation'])
        planned = service.execute('gateway.plan', {'web_plan_sha256': parent['plan_sha256'], 'public_origin': ORIGIN,
            'dev_enabled': True, 'acquisition': 'package'})['gateway']['preparation']
        raw = Path('/opt/gateway-package.zip').read_bytes()
        gateway = done(service.import_gateway_package(planned['plan_sha256'], io.BytesIO(raw), len(raw))['gateway']['preparation'])
        parents = {'web': parent['plan_sha256'], 'activation': service.activation.journal.read()['plan_sha256'], 'gateway': gateway['plan_sha256']}
        planned = service.execute('foundation.plan', {'parents': parents})['foundation']['installation']
        foundation = done(service.execute('foundation.apply', confirm(planned))['foundation']['installation'])
        planned = service.execute('gateway-service.plan', {'parents': {**parents, 'foundation': foundation['plan_sha256']}})['gateway_service']['installation']
        done(service.execute('gateway-service.apply', confirm(planned))['gateway_service']['installation'])
        paths = [service.engine.journal.path, service.activation.journal.path, service.boot.journal.path,
            service.public_tls.journal.path, service.gateway.journal.path, service.foundation.journal.path,
            service.gateway_service.journal.path, *service.gateway.identities.root.glob('*.pem')]
        saved = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        (EVIDENCE / 'shared-application-parents.json').write_bytes(quality.encode(saved))
    finally: service.close()


def serve():
    original = native.SharedOperation.apply
    def lost_reply(operation, context):
        result = original(operation, context)
        if operation.phase == 'certificate': raise OSError('Disposable completed certificate reply lost')
        return result
    def diagnostic(callback):
        def invoke(*args, **kwargs):
            try: return callback(*args, **kwargs)
            except Exception:
                with (EVIDENCE / 'shared-application-traceback.txt').open('a') as output: traceback.print_exc(file=output)
                raise
        return invoke
    with ExitStack() as stack:
        stack.enter_context(patch.object(native.SharedOperation, 'apply', diagnostic(lost_reply)))
        for name in ('prepare', 'current'):
            stack.enter_context(patch.object(native.SharedOperation, name, diagnostic(getattr(native.SharedOperation, name))))
        fixture.serve()


class Browser(unittest.TestCase):
    def test_real_cockpit_shared_certificates_lost_reply_and_explicit_check(self):
        from playwright.sync_api import sync_playwright, expect
        expect.set_options(timeout=240000)
        info = json.loads(fixture.CONNECTION.read_bytes())
        with sync_playwright() as pw, ExitStack() as cleanup:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage']); cleanup.callback(browser.close)
            context = browser.new_context(ignore_https_errors=True, viewport={'width': 1366, 'height': 900}); cleanup.callback(context.close)
            page = context.new_page(); page.set_default_timeout(240000)
            base = 'https://127.0.0.1:' + str(info['port'])
            state = lambda: context.request.get(base + '/api/wizard/state').json()['shared_public']
            cleanup.callback(lambda: (EVIDENCE / 'shared-cockpit-state.json').write_bytes(quality.encode(state())))
            cleanup.callback(page.screenshot, path=str(EVIDENCE / 'shared-public-cockpit.png'), full_page=True)
            page.goto(base); page.locator('#bootstrap-code').fill(info['code']); page.locator('#bootstrap-form button[type=submit]').click()
            page.locator('#shared-public-networks').fill('127.0.0.1/32, 172.30.85.10/32')
            page.locator('#prepare-shared-public').click(); page.locator('#plan-shared-public').click()
            expect(page.locator('#shared-public-state')).to_have_attribute('data-state', 'PLANNED')
            before = state(); page.locator('#apply-shared-public').click(); page.keyboard.press('Escape'); self.assertEqual(state(), before)
            page.locator('#apply-shared-public').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#shared-public-state')).to_have_attribute('data-state', 'FAILED')
            document = state()['installation']; failed = [r for r in document['steps'] if r['state'] == 'FAILED']
            self.assertEqual([r['name'] for r in failed], ['shared.public.certificate'])
            page.reload(); page.locator('#retry-shared-public').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#shared-public-state')).to_have_attribute('data-state', 'PLANNED')
            page.locator('#resume-shared-public').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#shared-public-state')).to_have_attribute('data-state', 'DONE')
            page.reload(); self.assertIsNone(state()['verification'])
            page.locator('#check-shared-public').click()
            expect(page.locator('#shared-public-verification')).to_contain_text('Frontal et services liés contrôlés')
            self.assertFalse(state()['phase6_complete'])
            page.locator('#plan-mobile-boot').click()
            expect(page.locator('#mobile-boot-state')).to_have_attribute('data-state', 'PLANNED')
            boot_state = lambda: context.request.get(base + '/api/wizard/state').json()['mobile_boot']
            before_boot = boot_state()
            page.locator('#apply-mobile-boot').click(); page.keyboard.press('Escape')
            self.assertEqual(boot_state(), before_boot)
            page.locator('#apply-mobile-boot').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#mobile-boot-state')).to_have_attribute('data-state', 'DONE')
            page.reload(); page.locator('#check-mobile-boot').click()
            expect(page.locator('#mobile-boot-verification')).to_contain_text('Configuration contrôlée')


def mobile_request(path='/health', method='GET', body=None, headers=None):
    host = ORIGIN[8:]; context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(host, 443, context=context, timeout=15)
    raw = socket.create_connection(('127.0.0.1', 443), timeout=15)
    try:
        connection.sock = context.wrap_socket(raw, server_hostname=host)
        connection.request(method, path, body=body, headers={'Host': host, 'Connection': 'close', **(headers or {})})
        response = connection.getresponse(); return response.status, response.read(), dict(response.getheaders())
    finally: connection.close(); raw.close()


class Verify(unittest.TestCase):
    def test_real_web_login_gateway_roundtrip_and_two_actual_acme_renewals(self):
        service = fixture.service(); self.addCleanup(service.close)
        control = service.shared_public; document = control.journal.read(); self.assertEqual(document['state'], 'DONE')
        _, runtime = control.engine(service.engine.report())
        public.login(self)
        status, body, _ = mobile_request(); self.assertEqual((status, json.loads(body)), (200, {'status': 'ok'}))
        token = native.gateway_service_probe._b64(os.urandom(32))
        status, body, _ = mobile_request(PATH, 'POST', json.dumps({'enrollment_token': token}).encode(), {'Content-Type': 'application/json', 'Origin': ORIGIN})
        self.assertEqual(status, 200); self.assertIn(json.loads(body)['data']['state'], ('invalid', 'unavailable'))
        self.assertEqual(mobile_request('/unknown')[0], 404)
        self.assertIn(mobile_request('/login.php')[0], (403, 404))
        saved = json.loads((EVIDENCE / 'shared-application-parents.json').read_bytes())
        for path, digest in saved.items(): self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
        pid = runtime.web.systemctl('show', 'https')['MainPID']
        web_leaf = runtime.web.acme_root / 'live/hestia-web/cert.pem'
        mobile_leaf = runtime.shared.acme_root / 'live/hestia-mobile/cert.pem'
        leaves = {str(p): p.read_bytes() for p in (web_leaf, mobile_leaf)}
        for argv in (runtime.web.certbot(renew=True), runtime.shared.certbot(renew=True)):
            native.old.command([*argv, '--force-renewal'], timeout=840)
        runtime.control('start', 'renew')
        self.assertEqual(runtime.web.systemctl('show', 'https')['MainPID'], pid)
        for path, before in leaves.items(): self.assertNotEqual(Path(path).read_bytes(), before)
        runtime.web.certificate(); runtime.mobile.verify(); public.login(self)
        self.assertEqual(mobile_request()[0], 200)
        snapshot = {'pid1_start': Path('/proc/1/stat').read_text().split(') ', 1)[1].split()[19], 'parents': saved,
            'shared_journal_sha256': hashlib.sha256(control.journal.path.read_bytes()).hexdigest(),
            'mobile_boot_journal_sha256': hashlib.sha256(service.mobile_boot.journal.path.read_bytes()).hexdigest()}
        _, boot = service.mobile_boot.engine(service.engine.report()); boot.live()
        snapshot['mobile_units'] = {r.unit: r.show()['MainPID'] for r in (boot.foundation, boot.gateway)}
        (EVIDENCE / 'shared-before-boot.json').write_bytes(quality.encode(snapshot))


class Restart(unittest.TestCase):
    def test_new_pid1_preserves_shared_public_guard_and_real_web_login(self):
        service = fixture.service(); self.addCleanup(service.close)
        before = json.loads((EVIDENCE / 'shared-before-boot.json').read_bytes())
        self.assertNotEqual(Path('/proc/1/stat').read_text().split(') ', 1)[1].split()[19], before['pid1_start'])
        for path, digest in before['parents'].items(): self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
        self.assertEqual(hashlib.sha256(service.shared_public.journal.path.read_bytes()).hexdigest(), before['shared_journal_sha256'])
        runtime = native.SharedPublic(service.shared_public.profile())
        runtime.configuration(); self.assertTrue(runtime.listener('http')); self.assertTrue(runtime.listener('https'))
        public.login(self)
        self.assertTrue(runtime.web.running('timer'))
        self.assertEqual(hashlib.sha256(service.mobile_boot.journal.path.read_bytes()).hexdigest(), before['mobile_boot_journal_sha256'])
        _, boot = service.mobile_boot.engine(service.engine.report()); boot.live()
        epoch = boot.epoch_identity()
        for role in ('foundation', 'gateway'):
            completed = boot.epoch._read(role + '.json')
            self.assertEqual(completed['owner']['epoch'], epoch)
            self.assertEqual(completed['process'], boot.process(getattr(boot, role)))
        self.assertEqual(mobile_request()[0], 200)
        token = native.gateway_service_probe._b64(os.urandom(32))
        status, body, _ = mobile_request(PATH, 'POST', json.dumps({'enrollment_token':token}).encode(), {'Content-Type':'application/json','Origin':ORIGIN})
        self.assertEqual(status, 200); self.assertIn(json.loads(body)['data']['state'], ('invalid','unavailable'))
        # Reinvoke the installed guard, proving that its epoch receipts observe
        # the same processes without a second systemctl start.
        processes = {role: boot.process(getattr(boot, role)) for role in ('foundation','gateway')}
        native.old.command(['/usr/bin/python3.13','-I','-B',str(boot.root/'worker.py')])
        self.assertEqual(processes, {role: boot.process(getattr(boot, role)) for role in processes})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('setup', 'serve', 'browser', 'verify', 'restart'))
    phase = parser.parse_args().phase
    if os.environ.get('HESTIA_SHARED_APPLICATION_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Disposable opt-in required')
    if phase != 'browser' and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('Real PID 1 required')
    if phase == 'setup': setup(); sys.exit(0)
    if phase == 'serve': serve(); sys.exit(0)
    before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase({'browser': Browser, 'verify': Verify, 'restart': Restart}[phase]))
    stable = quality.snapshot(ROOT) == before
    passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    report = {'suite': 'shared-application-' + phase, 'status': 'PASS' if passed else 'FAIL', 'tests': result.testsRun, 'expected': 1,
        'errors': len(result.errors), 'failures': len(result.failures), 'skips': len(result.skipped), 'source_stable': stable, 'source_files': len(before),
        'real_web_gateway': True, 'acme': 'private Pebble', 'mobile_boot_qualified': passed and phase == 'restart', 'phase6_complete': False}
    (EVIDENCE / ('shared-application-' + phase + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-shared-application-' + phase + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
