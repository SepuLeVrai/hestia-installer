#!/usr/bin/env python3
"""New boot scenarios only, on a disposable real Debian PID 1.

The qualified virgin chain is fixture setup, not a replay of its test campaigns.
A Docker restart is a new systemd boot with an empty /run, not a kernel reboot.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import mariadb_wizard_systemd as fixture
from installer import boot_runtime as boot
from installer.model import canonical_bytes
from github_fixture import DUMMY, confirm
from test_application_plan import setup_payload

EVIDENCE = fixture.EVIDENCE
PHASE = None


def facade(): return fixture.service()


def runtime(service): return service.boot.engine(service.engine.report())[1]


def snapshot(service):
    _, active = service.activation.engine(service.engine.report())
    sql = service.mariadb.runtime()
    units = [sql.unit, *(active.unit(role) for role in ('php', 'apache', 'timer'))]
    def invocation(unit):
        return fixture.sql.run(['/usr/bin/systemctl', 'show', '--value', '--property=InvocationID', '--', unit]).decode().strip()
    return {'pid1_start': Path('/proc/1/stat').read_text().split(') ', 1)[1].split()[19],
        'invocations': {unit: invocation(unit) for unit in units},
        'parents': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in {
            'preparation': service.engine.journal.path, 'activation': service.activation.journal.path, 'sql': service.mariadb.journal.path}.items()},
        'administrator': hashlib.sha256(sql.sql('SELECT id_user,email,password_hash FROM hestia_app.UserInfo').encode()).hexdigest(),
        'units': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [sql.unit_path,
            *(Path('/etc/systemd/system') / unit for unit in units[1:])]}}


def setup():
    service = facade()
    try:
        package = service.packages.state()['installation']
        document = service.execute('mariadb.plan', {'packages_sha256': package['plan_sha256']})['mariadb']['installation']
        service.execute('mariadb.credentials', {'confirmation': document['plan_sha256'], 'authority_password': fixture.PASSWORD})
        assert service.execute('mariadb.apply', confirm(document))['mariadb']['installation']['state'] == 'DONE'
        service.execute('github.validate', {'credential': DUMMY})
        payload = setup_payload(); r = service.mariadb.runtime()
        payload['credentials'].update(authority_user=r.authority_user, migration_user=r.migration_user, authority_password=fixture.PASSWORD)
        saved = service.execute('web.setup', payload)['application']['draft']
        document = service.execute('wizard.plan', {'modules': ['web'], 'mode': 'fresh', 'refs': {}, 'application_revision': saved['revision']})['installation']
        result = service.execute('apply', confirm(document))['installation']; assert result['state'] == 'DONE', result
        active = service.execute('activation.plan', {'preparation_sha256': document['plan_sha256']})['activation']['installation']
        result = service.execute('activation.apply', confirm(active))['activation']['installation']; assert result['state'] == 'DONE', result
        (EVIDENCE / 'before-enrollment.json').write_bytes(quality.encode(snapshot(service)))
        Path('/run/hestia-boot-volatile-marker').write_text('must disappear on the next PID 1 boot')
    finally: service.close()


def serve():
    # Genuine replies lost after complete native effects, one per new step.
    original = boot.BootOperation.apply
    def lost_reply(instance, context):
        result = original(instance, context)
        raise OSError('Disposable reply loss after boot ' + instance.phase)
    with patch.object(boot.BootOperation, 'apply', lost_reply): fixture.serve()


class Browser(unittest.TestCase):
    def test_confirm_reload_and_two_lost_replies_through_browser(self):
        from playwright.sync_api import sync_playwright, expect
        connection = json.loads(fixture.CONNECTION.read_bytes())
        with sync_playwright() as pw, ExitStack() as cleanup:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage']); cleanup.callback(browser.close)
            context = browser.new_context(ignore_https_errors=True, viewport={'width': 1366, 'height': 900}); cleanup.callback(context.close)
            page = context.new_page(); page.set_default_timeout(120000)
            cleanup.callback(page.screenshot, path=str(EVIDENCE / 'boot-wizard.png'), full_page=True)
            base = 'https://127.0.0.1:' + str(connection['port'])
            def capture():
                response = context.request.get(base + '/api/wizard/state')
                if response.ok: (EVIDENCE / 'browser-boot-state.json').write_bytes(quality.encode(response.json()))
            cleanup.callback(capture)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base); page.locator('#bootstrap-code').fill(connection['code']); page.locator('#bootstrap-form button[type=submit]').click()
            expect(page.locator('#plan-boot')).to_be_visible()
            page.locator('#plan-boot').click(); expect(page.locator('#boot-state')).to_have_attribute('data-state', 'PLANNED')
            before = context.request.get(base + '/api/wizard/state').json()['boot']['installation']
            page.reload(); expect(page.locator('#boot-state')).to_have_attribute('data-state', 'PLANNED')
            self.assertEqual(context.request.get(base + '/api/wizard/state').json()['boot']['installation'], before)
            page.locator('#apply-boot').click(); page.keyboard.press('Escape')
            self.assertEqual(context.request.get(base + '/api/wizard/state').json()['boot']['installation'], before)
            page.locator('#apply-boot').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#boot-state')).to_have_attribute('data-state', 'FAILED')
            page.reload(); expect(page.locator('#retry-boot')).to_be_visible()
            page.locator('#retry-boot').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#boot-state')).to_have_attribute('data-state', 'PLANNED')
            page.locator('#resume-boot').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#boot-state')).to_have_attribute('data-state', 'FAILED')
            page.reload(); page.locator('#retry-boot').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#boot-state')).to_have_attribute('data-state', 'DONE')
            page.reload(); expect(page.locator('#boot-state')).to_have_attribute('data-state', 'DONE')
            self.assertEqual(errors, [])


class Enrollment(unittest.TestCase):
    def test_enrollment_preserved_live_processes_and_all_acquired_parents(self):
        service = facade(); self.addCleanup(service.close)
        self.assertEqual(snapshot(service), json.loads((EVIDENCE / 'before-enrollment.json').read_bytes()))
        value = service.execute('boot.check', confirm(service.boot.journal.read()))['boot']
        self.assertTrue(value['availability']['boot_persistence_configured'])
        (EVIDENCE / 'before-boot.json').write_bytes(quality.encode(snapshot(service)))
        (EVIDENCE / 'boot-journal.json').write_bytes(canonical_bytes(service.boot.journal.read()))


class Restart(unittest.TestCase):
    def test_new_pid1_restores_services_and_preserves_admin_with_real_login(self):
        from application_wizard_systemd import ApplicationWizardLive
        service = facade(); self.addCleanup(service.close); r = runtime(service)
        before = json.loads((EVIDENCE / 'before-boot.json').read_bytes())
        self.assertFalse(Path('/run/hestia-boot-volatile-marker').exists())
        after = snapshot(service)
        self.assertNotEqual(before['pid1_start'], after['pid1_start'])
        for unit, invocation in before['invocations'].items(): self.assertNotEqual(invocation, after['invocations'][unit])
        for key in ('parents', 'administrator', 'units'): self.assertEqual(before[key], after[key])
        self.assertEqual(canonical_bytes(service.boot.journal.read()), (EVIDENCE / 'boot-journal.json').read_bytes())
        service.mariadb.assert_ready(); r.live()
        self.assertTrue(service.execute('boot.check', confirm(service.boot.journal.read()))['boot']['availability']['boot_persistence_configured'])
        self.root = Path('/var/lib/hestia-boot-login'); self.root.mkdir(mode=0o755)
        payload = setup_payload(); self.payload = {'administrator': payload['configuration']['administrator'], 'secrets': payload['credentials']}; self.nginx = None
        try: ApplicationWizardLive.fixture_login(self, r.http, already_active=True)
        finally:
            if self.nginx is not None: self.nginx.terminate(); self.nginx.wait(timeout=10)
        (EVIDENCE / 'after-boot.json').write_bytes(quality.encode(after))


class Blocked(unittest.TestCase):
    def test_new_pid1_keeps_web_blocked_without_repair_or_journal_rewrite(self):
        service = facade(); self.addCleanup(service.close); r = runtime(service)
        self.assertEqual(canonical_bytes(service.boot.journal.read()), (EVIDENCE / 'boot-journal.json').read_bytes())
        self.assertNotEqual(Path('/proc/1/stat').read_text().split(') ', 1)[1].split()[19], (EVIDENCE / ('before-' + PHASE + '-pid1.txt')).read_text())
        for role in ('php', 'apache', 'timer'): self.assertFalse(r.activation.running(role))
        if PHASE != 'partial': self.assertTrue(r.sql.running())
        else: self.assertFalse(r.sql.running())
        self.assertFalse(service.execute('boot.check', confirm(service.boot.journal.read()))['boot']['availability']['boot_persistence_configured'])
        if PHASE == 'maintenance':
            scope = r.http._scope(r.http._host()[0]); self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        if PHASE == 'drift': self.assertTrue((r.http.spec.root / 'conf/fpm.conf').read_bytes().endswith(b'\n; fixture boot drift\n'))
        if PHASE == 'partial': self.assertIsNone(r._read('enabled.json'))


def arm(phase):
    service = facade()
    try:
        r = runtime(service)
        if phase == 'maintenance':
            scope = r.http._scope(r.http._host()[0])
            with scope.acquire(confirmed=True): pass
        elif phase == 'drift':
            scope = r.http._scope(r.http._host()[0])
            with scope.recover(scope.observe()['lease_id'], confirmed=True) as lease: lease.resume(confirmed=True)
            path = r.http.spec.root / 'conf/fpm.conf'; (EVIDENCE / 'original-fpm.conf').write_bytes(path.read_bytes())
            path.write_bytes(path.read_bytes() + b'\n; fixture boot drift\n')
        else:
            path = r.http.spec.root / 'conf/fpm.conf'; path.write_bytes((EVIDENCE / 'original-fpm.conf').read_bytes())
            (r.root / 'enabled.json').unlink()
        (EVIDENCE / ('before-' + phase + '-pid1.txt')).write_text(Path('/proc/1/stat').read_text().split(') ', 1)[1].split()[19])
    finally: service.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('setup', 'serve', 'browser', 'enrollment', 'restart', 'arm-maintenance', 'maintenance', 'arm-drift', 'drift', 'arm-partial', 'partial'))
    PHASE = parser.parse_args().phase
    if os.environ.get('HESTIA_BOOT_WIZARD_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Disposable CI root opt-in required')
    if PHASE != 'browser' and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('systemd target required')
    if PHASE == 'setup': setup(); sys.exit(0)
    if PHASE == 'serve': serve(); sys.exit(0)
    if PHASE.startswith('arm-'): arm(PHASE.removeprefix('arm-')); sys.exit(0)
    before = quality.snapshot(ROOT)
    case = {'browser': Browser, 'enrollment': Enrollment, 'restart': Restart}.get(PHASE, Blocked)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    stable = before == quality.snapshot(ROOT); passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    report = {'suite': 'boot-wizard-' + PHASE, 'tests': result.testsRun, 'expected': 1, 'failures': len(result.failures), 'errors': len(result.errors),
              'skips': len(result.skipped), 'status': 'PASS' if passed else 'FAIL', 'source_stable': stable, 'source_files': len(before)}
    (EVIDENCE / ('boot-wizard-' + PHASE + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-' + PHASE + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
