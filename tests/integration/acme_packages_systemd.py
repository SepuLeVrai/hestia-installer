#!/usr/bin/env python3
"""New additive package scenarios, exclusively in disposable opt-in CI.

The acquired SQL/fresh/activation chain is fixture setup. Its boot code is the
actual frozen 5D6 bundle, not the new candidate's source set.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import mariadb_wizard_systemd as fixture
import boot_wizard_systemd as boot_fixture
from installer import acme_packages as acme, boot_runtime
from installer.frozen_boot import reference
from github_fixture import confirm

EVIDENCE = fixture.EVIDENCE
LEGACY = Path('/opt/hestia-legacy-installer')
PHASE = None


def setup(*, frozen_layout=False):
    boot_fixture.setup()
    service = fixture.service()
    try:
        old = {p.relative_to(LEGACY).as_posix(): p.read_bytes() for p in sorted((LEGACY / 'installer').rglob('*'))
               if p.is_file() and p.suffix in ('.py', '.php', '.json') and '__pycache__' not in p.parts}
        assert 'installer/acme_packages.py' not in old and 'installer/boot_runtime.py' in old
        expected_boot = old['installer/boot_runtime.py']
        if frozen_layout:
            # The later Mobile Web profile added from_draft. This recipe uses
            # the original fresh layout; admit only that exact constructor
            # evolution, keeping the enrolled historical bytes untouched.
            assert service.application.read()['version'] == 1
            old_line = b'self.layout = app.FreshProfile(self.application[\'instance\'])'
            assert expected_boot.count(old_line) == 1
            expected_boot = expected_boot.replace(old_line, b'self.layout = app.FreshProfile.from_draft(self.application)')
        assert expected_boot == (ROOT / 'installer/boot_runtime.py').read_bytes()
        assert old['installer/private/boot_worker.py'] == (ROOT / 'installer/private/boot_worker.py').read_bytes()
        with patch.object(boot_runtime, 'code_files', return_value=old):
            document = service.execute('boot.plan', {'activation_sha256': service.activation.journal.read()['plan_sha256']})['boot']['installation']
            result = service.execute('boot.apply', confirm(document))['boot']['installation']
            assert result['state'] == 'DONE', result
        (EVIDENCE / 'before-acme-install.json').write_bytes(quality.encode(snapshot(service)))
    finally: service.close()


def snapshot(service):
    value = boot_fixture.snapshot(service)
    value['parents']['boot'] = hashlib.sha256(service.boot.journal.path.read_bytes()).hexdigest()
    profile = service.boot._read('profile.json')
    value['boot_code'] = profile['code']
    value['boot_profile_sha256'] = hashlib.sha256((service.boot.root / 'profile.json').read_bytes()).hexdigest()
    return value


def serve():
    original = acme.Installation.apply
    def lost_reply(operation, context):
        original(operation, context)
        raise OSError('Disposable reply loss after completed additional package installation')
    with patch.object(acme.Installation, 'apply', lost_reply): fixture.serve()


class Acquire(unittest.TestCase):
    def test_additional_archives_are_authenticated_without_dpkg_changes(self):
        service = fixture.service(); self.addCleanup(service.close)
        before = service.packages.packages()._installed()
        parent = service.packages.journals['install'].path.read_bytes()
        document = service.execute('acme-packages.acquire.plan', {'packages_sha256': service.packages.journals['install'].read()['plan_sha256']})['acme_packages']['acquisition']
        result = service.execute('acme-packages.acquire.apply', confirm(document))['acme_packages']
        self.assertEqual(result['acquisition']['state'], 'DONE', result)
        self.assertEqual(before, service.packages.packages()._installed())
        self.assertEqual(parent, service.packages.journals['install'].path.read_bytes())
        manifest = service.acme_packages.packages()._manifest()
        self.assertEqual(manifest['before'], before)
        self.assertIn('certbot', manifest['archives'])
        self.assertEqual('nginx' in manifest['archives'], not service.packages.profile()['nginx'])
        (EVIDENCE / 'acme-acquisition.json').write_bytes(quality.encode(result))


class Browser(unittest.TestCase):
    def test_versions_consent_reload_and_lost_install_reply_through_browser(self):
        from playwright.sync_api import sync_playwright, expect
        expect.set_options(timeout=120000)
        connection = json.loads(fixture.CONNECTION.read_bytes())
        with sync_playwright() as pw, ExitStack() as cleanup:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage']); cleanup.callback(browser.close)
            context = browser.new_context(ignore_https_errors=True, viewport={'width': 1366, 'height': 900}); cleanup.callback(context.close)
            page = context.new_page(); page.set_default_timeout(120000)
            cleanup.callback(page.screenshot, path=str(EVIDENCE / 'acme-packages-wizard.png'), full_page=True)
            base = 'https://127.0.0.1:' + str(connection['port'])
            def state(): return context.request.get(base + '/api/wizard/state').json()['acme_packages']
            def capture(): (EVIDENCE / 'browser-acme-final.json').write_bytes(quality.encode(state()))
            cleanup.callback(capture)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base); page.locator('#bootstrap-code').fill(connection['code']); page.locator('#bootstrap-form button[type=submit]').click()
            page.locator('#plan-acme-install').click()
            expect(page.locator('#acme-install-state')).to_have_attribute('data-state', 'PLANNED')
            before = state(); self.assertTrue(before['selection']['packages'])
            for row in before['selection']['packages']:
                expect(page.locator('#acme-packages')).to_contain_text(row['name'] + ' — ' + row['version'])
            page.reload(); expect(page.locator('#acme-install-state')).to_have_attribute('data-state', 'PLANNED')
            self.assertEqual(state(), before)
            page.locator('#apply-acme-install').click(); page.keyboard.press('Escape'); self.assertEqual(state(), before)
            page.locator('#apply-acme-install').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#acme-install-state')).to_have_attribute('data-state', 'FAILED')
            page.reload(); page.locator('#retry-acme-install').click(); page.locator('#operation-dialog button[value=confirm]').click()
            expect(page.locator('#acme-install-state')).to_have_attribute('data-state', 'DONE')
            page.reload(); expect(page.locator('#acme-install-state')).to_have_attribute('data-state', 'DONE')
            self.assertFalse(state()['public_tls_configured']); self.assertFalse(state()['phase5_complete'])
            self.assertEqual(errors, [])


class Verify(unittest.TestCase):
    def test_offline_addition_preserves_live_processes_parents_and_masks(self):
        service = fixture.service(); self.addCleanup(service.close)
        self.assertEqual(snapshot(service), json.loads((EVIDENCE / 'before-acme-install.json').read_bytes()))
        _, runtime = reference(service.boot, service.engine.report(), observe=True)
        runtime.activation.check()
        installed = service.acme_packages.packages().observe_installed()
        self.assertTrue(installed['default_services_blocked'])
        # The original exact dpkg observation remains strict; no old receipt is
        # rewritten to disguise the extra packages as part of the old install.
        with self.assertRaises(acme.n.SystemPackagesError): service.packages.packages().observe_installed()
        with self.assertRaises(Exception): service.boot.engine(service.engine.report())
        (EVIDENCE / 'acme-installed.json').write_bytes(quality.encode(installed))
        (EVIDENCE / 'before-acme-restart.json').write_bytes(quality.encode(snapshot(service)))


class Restart(unittest.TestCase):
    def test_original_boot_bundle_survives_addition_and_missing_receipt_blocks_recovery(self):
        service = fixture.service(); self.addCleanup(service.close)
        before = json.loads((EVIDENCE / 'before-acme-restart.json').read_bytes()); after = snapshot(service)
        self.assertNotEqual(before['pid1_start'], after['pid1_start'])
        self.assertFalse(Path('/run/hestia-boot-volatile-marker').exists())
        for unit, value in before['invocations'].items(): self.assertNotEqual(value, after['invocations'][unit])
        for key in ('parents', 'administrator', 'units', 'boot_code', 'boot_profile_sha256'): self.assertEqual(before[key], after[key])
        _, runtime = reference(service.boot, service.engine.report(), observe=True); runtime.activation.check()
        packages = service.acme_packages.packages(); packages.observe_installed()
        engine = service.acme_packages.engine('install'); document = engine.report()
        op = engine.registry.get(document['plan']['steps'][0])
        context = engine._context(document, document['plan']['steps'][0], document['steps'][0])
        (packages.directory / 'installed.json').unlink()
        with patch.object(packages.__class__, 'install', side_effect=AssertionError('replay')):
            self.assertEqual(op.recover(context, 'apply').decision.value, 'MANUAL')
        self.assertEqual(service.acme_packages.journals['install'].read(), document)
        self.assertEqual(after, snapshot(service))
        (EVIDENCE / 'after-acme-restart.json').write_bytes(quality.encode(after))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('base-acquire', 'acquire', 'setup', 'serve', 'browser', 'verify', 'restart'))
    parser.add_argument('--nginx', choices=('yes', 'no'), default='yes')
    args = parser.parse_args(); PHASE = args.phase
    if os.environ.get('HESTIA_ACME_PACKAGES_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Disposable CI root opt-in required')
    if PHASE != 'browser' and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('systemd target required')
    if PHASE == 'base-acquire':
        service = fixture.service()
        try:
            document = service.execute('packages.acquire.plan', {'nginx': args.nginx == 'yes'})['packages']['acquisition']
            result = service.execute('packages.acquire.apply', confirm(document))['packages']['acquisition']
            assert result['state'] == 'DONE', result
        finally: service.close()
        sys.exit(0)
    if PHASE == 'setup': setup(); sys.exit(0)
    if PHASE == 'serve': serve(); sys.exit(0)
    before = quality.snapshot(ROOT)
    case = {'acquire': Acquire, 'browser': Browser, 'verify': Verify, 'restart': Restart}[PHASE]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    stable = before == quality.snapshot(ROOT); passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    report = {'suite': 'acme-packages-' + PHASE, 'tests': result.testsRun, 'expected': 1, 'failures': len(result.failures),
              'errors': len(result.errors), 'skips': len(result.skipped), 'status': 'PASS' if passed else 'FAIL', 'source_stable': stable, 'source_files': len(before)}
    (EVIDENCE / ('acme-packages-' + PHASE + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-acme-' + PHASE + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
