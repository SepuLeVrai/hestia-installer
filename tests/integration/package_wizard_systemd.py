#!/usr/bin/env python3
"""Opt-in fresh Debian target, Chromium sidecar and offline failure clones.

The target never receives Chromium/test libraries. Only the official package
controller installs Apache, FPM, MariaDB and nginx. No user host is permitted.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import quality
from installer import package_plan as p
from installer.bootstrap import prepare_bootstrap, serve_in_thread
from installer.engine import TransactionEngine
from installer.github_sources import GitHubAcquisition
from installer.operations import default_registry
from installer.service import TransactionService
from installer.transaction import StateJournal

STATE = Path('/var/lib/hestia-package-wizard/state.json')
CONNECTION = Path('/var/lib/hestia-package-browser.json')
EVIDENCE = Path('/evidence')
PHASE = None


def service():
    engine = TransactionEngine(StateJournal(STATE), default_registry())
    return TransactionService(engine, github=GitHubAcquisition(engine))


def confirm(document): return {'confirmation': document['plan_sha256'], 'confirm': True}


def serve():
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    with prepare_bootstrap(web_root=ROOT / 'installer/web', runtime_root=Path('/run/hestia-package-bootstrap'),
            bind_address='127.0.0.1', interactive=False, transaction_service=service()) as prepared:
        thread = serve_in_thread(prepared)
        CONNECTION.write_text(json.dumps({'port': prepared.port, 'code': prepared.bootstrap_code, 'pid': os.getpid()}))
        CONNECTION.chmod(0o600)
        stop.wait()
        prepared.server.shutdown(); thread.join(timeout=5)
    CONNECTION.unlink()


class Browser(unittest.TestCase):
    def test_real_browser_two_confirmations_refresh_and_official_packages(self):
        from playwright.sync_api import sync_playwright, expect
        connection = json.loads(CONNECTION.read_bytes())
        with sync_playwright() as pw, ExitStack() as cleanup:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage'])
            cleanup.callback(browser.close)
            context = browser.new_context(ignore_https_errors=True, viewport={'width': 1366, 'height': 900})
            cleanup.callback(context.close)
            page = context.new_page(); page.set_default_timeout(120000)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('https://127.0.0.1:' + str(connection['port']))
            page.locator('#bootstrap-code').fill(connection['code']); page.locator('#bootstrap-form button[type=submit]').click()
            # At restored GitHub step, Next correctly remains disabled without
            # a token; Cancel is enabled as soon as initialization completes.
            expect(page.locator('#cancel-button')).to_be_enabled()
            if page.locator('body').get_attribute('data-wizard-step') == '0': page.locator('#next-button').click()
            expect(page.locator('#package-preparation')).to_be_visible()
            if PHASE == 'acquire':
                page.locator('#package-preparation > summary').click(); page.locator('#packages-nginx').check()
                page.locator('#plan-packages-acquire').click()
                expect(page.locator('#packages-acquire-state')).to_have_attribute('data-state', 'PLANNED')
                page.reload(); expect(page.locator('#packages-acquire-state')).to_have_attribute('data-state', 'PLANNED')
                page.locator('#apply-packages-acquire').click(); page.keyboard.press('Escape')
                expect(page.locator('#packages-acquire-state')).to_have_attribute('data-state', 'PLANNED')
                page.locator('#apply-packages-acquire').click()
                page.locator('#operation-dialog button[value=confirm]').click()
                expect(page.locator('#packages-acquire-state')).to_have_attribute('data-state', 'DONE', timeout=600000)
                expect(page.locator('#packages-install-state')).to_have_count(0)
            else:
                expect(page.locator('#packages-acquire-state')).to_have_attribute('data-state', 'DONE')
                page.locator('#plan-packages-install').click()
                expect(page.locator('#packages-install-state')).to_have_attribute('data-state', 'PLANNED')
                page.locator('#package-versions > summary').click()
                expect(page.locator('#package-versions')).to_contain_text('mariadb-server')
                expect(page.locator('#package-versions')).to_contain_text('php8.4-fpm')
                expect(page.locator('#package-versions')).to_contain_text('nginx')
                page.reload(); expect(page.locator('#packages-install-state')).to_have_attribute('data-state', 'PLANNED')
                page.locator('#apply-packages-install').click(); page.keyboard.press('Escape')
                expect(page.locator('#packages-install-state')).to_have_attribute('data-state', 'PLANNED')
                page.locator('#apply-packages-install').click(); page.locator('#operation-dialog button[value=confirm]').click()
                expect(page.locator('#packages-install-state')).to_have_attribute('data-state', 'DONE', timeout=600000)
            page.reload()
            expect(page.locator('#packages-' + PHASE + '-state')).to_have_attribute('data-state', 'DONE')
            response = context.request.get('https://127.0.0.1:' + str(connection['port']) + '/api/wizard/state')
            self.assertTrue(response.ok); state = response.json()
            self.assertIsNone(state['installation']); self.assertFalse(state['packages']['application_installed'])
            self.assertFalse(state['packages']['mariadb_ready']); self.assertEqual(errors, [])
            (EVIDENCE / ('wizard-state-' + PHASE + '.json')).write_text(json.dumps(state, indent=2))
            page.screenshot(path=str(EVIDENCE / ('wizard-' + PHASE + '.png')), full_page=True)


class FailureClone(unittest.TestCase):
    def test_offline_install_recovery_is_receipt_driven_and_never_replays_apt(self):
        facade = service(); self.addCleanup(facade.close)
        acquisition = facade.packages.state()['acquisition']; self.assertEqual(acquisition['state'], 'DONE')
        document = facade.execute('packages.install.plan', {'acquisition_sha256': acquisition['plan_sha256']})['packages']['installation']
        packages = facade.packages.packages(); manifest = packages._manifest()
        if PHASE == 'lost-reply':
            original = p.native.SystemPackages.install
            def lost_reply(operation, **kwargs):
                original(operation, **kwargs)
                raise OSError('injected lost reply after durable success')
            injection = patch.object(p.native.SystemPackages, 'install', lost_reply)
        else:
            original = p.native._journal
            def missing_receipt(fd, name, value):
                if name == 'installed.json': raise OSError('injected private receipt write failure')
                return original(fd, name, value)
            injection = patch.object(p.native, '_journal', missing_receipt)
        with injection:
            result = facade.execute('packages.install.apply', confirm(document))['packages']['installation']
        self.assertEqual(result['state'], 'FAILED')
        packages._verify_installed(manifest); packages._masks(manifest['host'])
        restarted = service(); self.addCleanup(restarted.close)
        with patch.object(p.native.SystemPackages, '_apt', side_effect=AssertionError('No APT during recovery')):
            result = restarted.execute('packages.install.retry', {**confirm(document), 'name': 'system.packages-install'})['packages']
        self.assertEqual(result['installation']['state'], 'DONE' if PHASE == 'lost-reply' else 'MANUAL_ACTION_REQUIRED')
        self.assertFalse(result['mariadb_ready']); self.assertFalse(result['application_installed'])
        self.assertEqual(result['acquisition'], acquisition)
        (EVIDENCE / ('wizard-state-' + PHASE + '.json')).write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True,
        choices=('serve', 'stop', 'verify-installed', 'acquire', 'install', 'lost-reply', 'interrupted'))
    PHASE = parser.parse_args().phase
    if os.environ.get('HESTIA_PACKAGE_WIZARD_TEST') != '1' or os.geteuid() != 0:
        raise RuntimeError('Disposable CI root opt-in required')
    if PHASE in ('serve', 'stop', 'verify-installed', 'lost-reply', 'interrupted'):
        if Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('systemd target required')
    if PHASE == 'serve': serve(); sys.exit(0)
    if PHASE == 'stop':
        os.kill(json.loads(CONNECTION.read_bytes())['pid'], signal.SIGTERM); sys.exit(0)
    if PHASE == 'verify-installed':
        facade = service()
        report = facade.packages.packages().observe_installed()
        assert report['default_services_blocked'] and not report['application_installed']
        (EVIDENCE / 'native-installed.json').write_text(json.dumps(report, indent=2)); facade.close(); sys.exit(0)
    before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(
        Browser if PHASE in ('acquire', 'install') else FailureClone))
    stable = before == quality.snapshot(ROOT)
    passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    report = {'suite': 'package-wizard-' + PHASE, 'tests': result.testsRun, 'expected': 1,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if passed else 'FAIL', 'source_stable': stable, 'source_files': len(before)}
    (EVIDENCE / ('package-wizard-' + PHASE + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-' + PHASE + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
