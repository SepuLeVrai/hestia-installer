#!/usr/bin/env python3
"""Disposable virgin packages -> product SQL -> fresh Web -> local activation.

Chromium lives in a sidecar. Real SQL/accounts/services run only in the opt-in
systemd target. GitHub transport alone is a fixture for the complete pinned Web.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).resolve().parent)]
import quality
from github_fixture import DUMMY, FakeGitHub, tar_bytes, confirm
from installer import application_plan as app, mariadb_runtime as sql
from installer.bootstrap import prepare_bootstrap, serve_in_thread
from installer.engine import TransactionEngine
from installer.github_client import GitHubAccess, GitHubClient
from installer.github_sources import GitHubAcquisition
from installer.operations import default_registry
from installer.service import TransactionService
from installer.transaction import StateJournal

STATE = Path('/var/lib/hestia-mariadb-wizard/state.json')
CONNECTION = Path('/var/lib/hestia-mariadb-browser.json')
TARGET = Path('/opt/hestia-pinned-web')
EVIDENCE = Path('/evidence')
PASSWORD = 'authority-disposable-fixture-2026'
SOURCE_COMMIT = app.STORAGE_COMMIT
PHASE = None


def service():
    engine = TransactionEngine(StateJournal(STATE), default_registry())
    if TARGET.exists():
        fake = FakeGitHub()
        fake.archive_override = tar_bytes('web', SOURCE_COMMIT, [(p.relative_to(TARGET).as_posix(), p.read_bytes(), p.stat().st_mode & 0o777)
            for p in sorted(TARGET.rglob('*')) if p.is_file()])
        access = GitHubAccess(engine.secrets, GitHubClient(opener=fake))
        github = GitHubAcquisition(engine, access, restore=False)
        if not app.ApplicationPlan(engine, github).restore(): github.restore_registry()
    else: github = GitHubAcquisition(engine)
    return TransactionService(engine, github=github)


def serve():
    # Diagnostic wrapper belongs only to this disposable recipe. Native errors
    # are closed codes; no locals, wire payloads or captured SQL output is logged.
    def diagnostic(original):
        def invoke(instance, *args):
            try: return original(instance, *args)
            except Exception:
                with (EVIDENCE / 'sql-operation-traceback.txt').open('a') as stream: traceback.print_exc(file=stream)
                raise
        return invoke
    for operation in (sql.Initialization, sql.Start, sql.Authority):
        for method in ('prepare', 'apply', 'validate', 'commit'):
            setattr(operation, method, diagnostic(getattr(operation, method)))
    stop = threading.Event(); signal.signal(signal.SIGTERM, lambda *_: stop.set())
    with prepare_bootstrap(web_root=ROOT / 'installer/web', runtime_root=Path('/run/hestia-mariadb-bootstrap'),
            bind_address='127.0.0.1', interactive=False, transaction_service=service()) as prepared:
        thread = serve_in_thread(prepared)
        CONNECTION.write_text(json.dumps({'port': prepared.port, 'code': prepared.bootstrap_code, 'pid': os.getpid()})); CONNECTION.chmod(0o600)
        stop.wait(); prepared.server.shutdown(); thread.join(timeout=5)
    CONNECTION.unlink()


class Browser(unittest.TestCase):
    def test_virgin_mariadb_fresh_and_activation_through_real_browser(self):
        from playwright.sync_api import sync_playwright, expect
        connection = json.loads(CONNECTION.read_bytes())
        with sync_playwright() as pw, ExitStack() as cleanup:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage'])
            cleanup.callback(browser.close)
            context = browser.new_context(ignore_https_errors=True, viewport={'width': 1366, 'height': 900}); cleanup.callback(context.close)
            page = context.new_page(); page.set_default_timeout(120000)
            cleanup.callback(page.screenshot, path=str(EVIDENCE / 'mariadb-fresh-wizard.png'), full_page=True)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            base = 'https://127.0.0.1:' + str(connection['port'])
            def capture_state():
                response = context.request.get(base + '/api/wizard/state')
                if response.ok: (EVIDENCE / 'browser-final-state.json').write_bytes(quality.encode(response.json()))
            cleanup.callback(capture_state)
            def done(selector, timeout=240000):
                expect(page.locator(selector)).to_have_attribute('data-state', re.compile('^(DONE|FAILED|MANUAL_ACTION_REQUIRED)$'), timeout=timeout)
                expect(page.locator(selector)).to_have_attribute('data-state', 'DONE', timeout=1000)
            page.goto(base); page.locator('#bootstrap-code').fill(connection['code']); page.locator('#bootstrap-form button[type=submit]').click()
            expect(page.locator('body')).to_have_attribute('data-wizard-step', re.compile('^[01]$'))
            if page.locator('body').get_attribute('data-wizard-step') == '0': page.locator('#next-button').click()
            page.locator('#plan-mariadb').click(); expect(page.locator('#mariadb-state')).to_have_attribute('data-state', 'PLANNED')
            before = context.request.get(base + '/api/wizard/state').json()['mariadb']['installation']
            page.reload(); expect(page.locator('#mariadb-state')).to_have_attribute('data-state', 'PLANNED')
            self.assertEqual(context.request.get(base + '/api/wizard/state').json()['mariadb']['installation'], before)
            page.locator('#mariadb-authority-password').fill(PASSWORD); page.locator('#save-mariadb-credentials').click()
            expect(page.locator('#wizard-message')).to_contain_text('Mot de passe gardé')
            page.locator('#apply-mariadb').click(); page.keyboard.press('Escape')
            expect(page.locator('#mariadb-state')).to_have_attribute('data-state', 'PLANNED')
            page.locator('#apply-mariadb').click(); page.locator('#operation-dialog button[value=confirm]').click()
            done('#mariadb-state')
            page.reload(); expect(page.locator('#mariadb-state')).to_have_attribute('data-state', 'DONE')
            state = context.request.get(base + '/api/wizard/state').json()
            self.assertEqual(state['mariadb']['missing_credentials'], []); self.assertIsNone(state['installation'])
            page.locator('#github-credential').fill(DUMMY); page.locator('#validate-github').click()
            page.locator('#next-button').click(); page.locator('#run-preflight').click(); page.locator('#next-button').click()
            page.locator('#prepare-web-application').check()
            expect(page.locator('#application-database-mode')).to_be_disabled()
            for name in ('authority_user', 'migration_user'):
                expect(page.locator('#application-' + name)).to_have_value(state['mariadb'][name])
                expect(page.locator('#application-' + name)).to_have_attribute('readonly', '')
            for name, value in {'hostname': 'fresh.hestia.test', 'database-name': 'hestia_virgin', 'database-user': 'hestia_virgin_app',
                    'first-name': 'CI', 'last-name': 'Fresh', 'email': 'virgin@example.test', 'database_password': 'application-fixture-password',
                    'admin_password': 'admin-fixture-password-2026', 'migration_password': 'migration-fixture-password', 'authority_password': PASSWORD}.items():
                page.locator('#application-' + name).fill(value)
            page.locator('#save-web-application').click(); expect(page.locator('#wizard-message')).to_contain_text('Configuration enregistrée')
            page.locator('#next-button').click(); expect(page.locator('#confirm-plan')).to_be_visible()
            before = context.request.get(base + '/api/wizard/state').json()['installation']
            self.assertEqual(len(before['plan']['steps']), 10)
            page.reload(); expect(page.locator('#confirm-plan')).not_to_be_checked()
            page.locator('#confirm-plan').check(); page.locator('#next-button').click()
            done('#execution-state')
            page.locator('#plan-activation').click(); expect(page.locator('#activation-state')).to_have_attribute('data-state', 'PLANNED')
            page.locator('#apply-activation').click(); page.locator('#operation-dialog button[value=confirm]').click()
            done('#activation-state', timeout=120000)
            page.reload(); expect(page.locator('#activation-state')).to_have_attribute('data-state', 'DONE')
            state = context.request.get(base + '/api/wizard/state').json()
            self.assertEqual(errors, []); self.assertNotIn(PASSWORD, json.dumps(state)); self.assertNotIn(PASSWORD, page.content())
            (EVIDENCE / 'mariadb-fresh-state.json').write_bytes(quality.encode(state))


class Verify(unittest.TestCase):
    def test_native_sql_scopes_single_admin_actual_web_login_and_no_boot(self):
        from application_wizard_systemd import ApplicationWizardLive
        facade = service(); self.addCleanup(facade.close); facade.mariadb.assert_ready()
        self.assertEqual(facade.engine.report()['state'], 'DONE'); self.assertEqual(facade.activation.state()['installation']['state'], 'DONE')
        saved = facade.application.read(); profile = app.FreshProfile(saved['instance']); runtime = facade.mariadb.runtime()
        self.assertEqual(runtime.sql('SELECT COUNT(*) FROM hestia_virgin.UserInfo'), '1')
        self.assertEqual(runtime.sql("SELECT COUNT(*) FROM mysql.global_priv WHERE User='" + runtime.migration_user + "'"), '0')
        grants = runtime.sql("SHOW GRANTS FOR 'hestia_virgin_app'@'127.0.0.1'")
        self.assertIn('SELECT, INSERT, UPDATE, DELETE', grants); self.assertNotIn('ALL PRIVILEGES', grants)
        self.assertEqual(sql.run(['/usr/bin/systemctl', 'is-enabled', runtime.unit]).strip(), b'static')
        self.assertFalse(Path('/etc/systemd/system/multi-user.target.wants', runtime.unit).exists())
        self.root = Path('/var/lib/hestia-virgin-login'); self.root.mkdir(mode=0o755)
        self.payload = {'administrator': {'email': 'virgin@example.test'}, 'secrets': {'admin_password': 'admin-fixture-password-2026'}}
        self.nginx = None
        try: ApplicationWizardLive.fixture_login(self, profile.http(saved['configuration']), already_active=True)
        finally:
            if self.nginx is not None: self.nginx.terminate(); self.nginx.wait(timeout=10)
        for file in STATE.parent.rglob('*'):
            if file.is_file(): self.assertNotIn(PASSWORD.encode(), file.read_bytes())
        self.assertNotIn(PASSWORD, json.dumps(facade.report()))
        (EVIDENCE / 'mariadb-native-proof.json').write_bytes(quality.encode({'fresh_admin_count': 1, 'migration_account_removed': True,
            'application_crud_only': True, 'real_https_fixture_login': True, 'default_services_masked': True,
            'boot_enabled': False, 'public_tls_delivered': False, 'mariadb_local_only': True}))


class RecoveryClone(unittest.TestCase):
    def test_readonly_recovery_or_manual_without_reinitialization(self):
        facade = service(); self.addCleanup(facade.close)
        package = facade.packages.state()['installation']
        document = facade.execute('mariadb.plan', {'packages_sha256': package['plan_sha256']})['mariadb']['installation']
        facade.execute('mariadb.credentials', {'confirmation': document['plan_sha256'], 'authority_password': PASSWORD})
        operation = {'lost-init': sql.Initialization, 'lost-start': sql.Start, 'lost-authority': sql.Authority}.get(PHASE)
        if operation:
            original = operation.apply
            def lost_reply(instance, context):
                original(instance, context); raise OSError('Injected reply loss after complete effect')
            injection = patch.object(operation, 'apply', lost_reply)
        elif PHASE == 'partial-authority':
            original = sql.MariaDB.write
            def missing_receipt(instance, name, value):
                if name == 'authority-created.json': raise OSError('Injected final private receipt failure')
                return original(instance, name, value)
            injection = patch.object(sql.MariaDB, 'write', missing_receipt)
        else: injection = ExitStack()
        with injection: result = facade.execute('mariadb.apply', confirm(document))['mariadb']['installation']
        runtime = facade.mariadb.runtime()
        if PHASE == 'drift':
            self.assertEqual(result['state'], 'DONE')
            before = runtime.state()['MainPID']; path = runtime.root / 'server.cnf'; path.write_bytes(path.read_bytes() + b'\n# drift\n')
            other = service(); self.addCleanup(other.close)
            with patch.object(sql, 'run', side_effect=AssertionError('GET host probe')):
                self.assertEqual(other.wizard_state()['mariadb']['installation'], result)
            with self.assertRaises(Exception): other.mariadb.assert_ready()
            self.assertTrue(Path('/proc', before).exists())
        else:
            self.assertEqual(result['state'], 'FAILED')
            other = service(); self.addCleanup(other.close)
            if PHASE in ('lost-init', 'lost-start'):
                other.execute('mariadb.credentials', {'confirmation': document['plan_sha256'], 'authority_password': PASSWORD})
            before = runtime.sql("SELECT COUNT(*) FROM mysql.global_priv WHERE User='" + runtime.authority_user + "'") if PHASE.endswith('authority') else None
            with patch.object(operation or sql.Authority, 'apply', side_effect=AssertionError('No repeated effect')):
                result = other.execute('mariadb.retry', {**confirm(document), 'name': next(s['name'] for s in result['steps'] if s['state'] == 'FAILED')})['mariadb']['installation']
                if PHASE in ('lost-init', 'lost-start'):
                    self.assertEqual(result['state'], 'PLANNED')
                    result = other.execute('mariadb.resume', confirm(document))['mariadb']['installation']
            self.assertEqual(result['state'], 'MANUAL_ACTION_REQUIRED' if PHASE == 'partial-authority' else 'DONE')
            if before is not None: self.assertEqual(runtime.sql("SELECT COUNT(*) FROM mysql.global_priv WHERE User='" + runtime.authority_user + "'"), before)
        (EVIDENCE / ('mariadb-state-' + PHASE + '.json')).write_bytes(quality.encode(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True,
        choices=('acquire', 'install', 'serve', 'stop', 'browser', 'verify', 'lost-init', 'lost-start', 'lost-authority', 'partial-authority', 'drift'))
    PHASE = parser.parse_args().phase
    if os.environ.get('HESTIA_MARIADB_WIZARD_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Disposable CI root opt-in required')
    if PHASE != 'browser' and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('systemd target required')
    if PHASE == 'serve': serve(); sys.exit(0)
    if PHASE == 'stop': os.kill(json.loads(CONNECTION.read_bytes())['pid'], signal.SIGTERM); sys.exit(0)
    if PHASE in ('acquire', 'install'):
        facade = service()
        if PHASE == 'acquire':
            document = facade.execute('packages.acquire.plan', {'nginx': True})['packages']['acquisition']
        else:
            document = facade.execute('packages.install.plan', {'acquisition_sha256': facade.packages.state()['acquisition']['plan_sha256']})['packages']['installation']
        value = facade.execute('packages.' + PHASE + '.apply', confirm(document))['packages']
        assert value['acquisition' if PHASE == 'acquire' else 'installation']['state'] == 'DONE', json.dumps(value)
        (EVIDENCE / ('prerequisite-packages-' + PHASE + '.json')).write_bytes(quality.encode(value))
        facade.close(); sys.exit(0)
    before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(
        Browser if PHASE == 'browser' else Verify if PHASE == 'verify' else RecoveryClone))
    stable = before == quality.snapshot(ROOT)
    passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    report = {'suite': 'mariadb-wizard-' + PHASE, 'tests': result.testsRun, 'expected': 1, 'failures': len(result.failures),
        'errors': len(result.errors), 'skips': len(result.skipped), 'status': 'PASS' if passed else 'FAIL', 'source_stable': stable, 'source_files': len(before)}
    (EVIDENCE / ('mariadb-wizard-' + PHASE + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-' + PHASE + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
