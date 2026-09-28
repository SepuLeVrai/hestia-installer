#!/usr/bin/env python3
"""Product starts and explicit availability, only inside disposable Debian CI."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import application_wizard_systemd as wizard
from github_fixture import confirm
from installer import application_activation as activation
from installer import system_drain as drain
from http_runtime_systemd import command, until


class ActivationLive(wizard.ApplicationWizardLive):
    def setUp(self):
        super().setUp()
        # Repeated actual HTTP probes leave server-side TIME_WAIT sockets on
        # the profile's fixed port. Wait for the previous disposable case;
        # keep the product's exclusive bind precondition unchanged.
        def released():
            try:
                with socket.socket() as listener: listener.bind(('127.0.0.1', 9080))
                return True
            except OSError: return False
        until(released, timeout=75)

    def prepared_activation(self):
        parent = self.planned()
        result = self.service.execute('apply', confirm(parent))['installation']
        self.assertEqual(result['state'], 'DONE', [(r['name'], r['state'], r['last_error_redacted']) for r in result['steps']])
        self.staged(self.service)
        document = self.service.execute('activation.plan', {'preparation_sha256': parent['plan_sha256']})['activation']['installation']
        return self.service.engine.report(), document

    def http_runtime(self): return self.profile.http(self.saved['configuration'])

    def finished(self, service, parent):
        self.assertEqual(service.activation.state()['installation']['state'], 'DONE')
        self.assertEqual(service.engine.report(), parent)
        value = service.execute('activation.check', confirm(service.activation.journal.read()))['activation']['availability']
        self.assertEqual(value['state'], 'LOCAL_WEB_AVAILABLE')
        self.assertFalse(value['public_tls_verified']); self.assertFalse(value['application_installed'])
        self.assertEqual(self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.UserInfo')[0]['n'], 1)

    def test_activation_browser_product_start_and_real_login(self):
        from playwright.sync_api import expect
        self.managed(); self.wizard(); parent = self.service.engine.report()
        # Closing the first bootstrap correctly shuts down its service facade.
        self.service = self.build_service()
        with self.browser() as page:
            page.locator('#plan-activation').click()
            expect(page.locator('#activation-state')).to_have_attribute('data-state', 'PLANNED')
            before = self.service.activation.journal.read(); self.staged(self.service)
            page.reload(); expect(page.locator('#apply-activation')).to_be_visible()
            self.assertEqual(self.service.activation.journal.read(), before)
            page.locator('#apply-activation').click(); page.keyboard.press('Escape')
            self.assertEqual(self.service.activation.journal.read(), before)
            page.locator('#apply-activation').click(); page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#activation-state')).to_have_attribute('data-state', 'DONE', timeout=120000)
            page.locator('#check-availability').click()
            expect(page.locator('#activation-availability')).to_contain_text('Page de connexion locale disponible')
            pids = [drain._show(self.http_runtime().unit(role))['MainPID'] for role in ('php', 'apache')]
            page.reload(); expect(page.locator('#activation-state')).to_have_attribute('data-state', 'DONE')
            self.assertEqual(pids, [drain._show(self.http_runtime().unit(role))['MainPID'] for role in ('php', 'apache')])
            page.screenshot(path='/evidence/product-activation.png', full_page=True)
        self.finished(self.build_service(), parent)
        self.fixture_login(self.http_runtime(), already_active=True)

    def lost_reply(self, role):
        parent, document = self.prepared_activation()
        engine, _ = self.service.activation.engine(parent); engine._fault_hook = self.hook('web.activation.' + role)
        self.kill_child(lambda: engine.apply(document['plan_sha256']))
        pid = None if role == 'admission' else drain._show(self.http_runtime().unit(role))['MainPID']
        other = self.build_service(); before = other.activation.journal.path.read_bytes()
        with patch.object(activation.h, '_command', side_effect=AssertionError('GET mutation')):
            other.wizard_state(); other.report()
        self.assertEqual(before, other.activation.journal.path.read_bytes())
        result = other.execute('activation.resume', confirm(document))['activation']['installation']
        self.assertEqual(result['state'], 'DONE', result['last_error_redacted'])
        if pid is not None: self.assertEqual(pid, drain._show(self.http_runtime().unit(role))['MainPID'])
        self.finished(other, parent)

    def test_activation_lost_admission_reply_never_reopens_twice(self): self.lost_reply('admission')
    def test_activation_lost_php_reply_keeps_original_process(self): self.lost_reply('php')
    def test_activation_lost_apache_reply_keeps_original_process(self): self.lost_reply('apache')

    def test_activation_intent_without_start_stays_manual(self):
        parent, document = self.prepared_activation(); engine, _ = self.service.activation.engine(parent)
        original = activation.h._command
        def stopped(argv):
            if 'start' in argv: os.kill(os.getpid(), signal.SIGKILL)
            return original(argv)
        def child():
            with patch.object(activation.h, '_command', side_effect=stopped): engine.apply(document['plan_sha256'])
        self.kill_child(child)
        other = self.build_service()
        result = other.execute('activation.resume', confirm(document))['activation']['installation']
        self.assertEqual(result['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(drain._show(self.http_runtime().unit('php'))['MainPID'], '0')
        self.assertEqual(other.engine.report(), parent)

    def test_activation_unit_drift_before_consent_prevents_admission(self):
        parent, document = self.prepared_activation(); runtime = self.http_runtime()
        path = drain.UNIT_ROOT / runtime.unit('php'); path.write_bytes(path.read_bytes() + b'\n# fixture drift\n')
        result = self.service.execute('activation.apply', confirm(document))['activation']['installation']
        self.assertEqual(result['state'], 'FAILED')
        scope = runtime._scope(wizard.pwd.getpwnam(runtime.spec.service_user))
        self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertEqual(drain._show(runtime.unit('php'))['MainPID'], '0')

    def test_activation_historical_done_and_current_unavailability_are_separate(self):
        parent, document = self.prepared_activation()
        result = self.service.execute('activation.apply', confirm(document))['activation']['installation']
        self.assertEqual(result['state'], 'DONE', result['last_error_redacted']); self.finished(self.service, parent)
        before = self.service.activation.journal.path.read_bytes(); command('systemctl', 'stop', self.http_runtime().unit('php'))
        other = self.build_service()
        with patch.object(activation.Activation, 'check', side_effect=AssertionError('GET probe')):
            self.assertIsNone(other.wizard_state()['activation']['availability'])
        self.assertEqual(other.execute('activation.check', confirm(document))['activation']['availability']['state'], 'LOCAL_WEB_UNAVAILABLE')
        self.assertEqual(before, other.activation.journal.path.read_bytes())
        self.assertEqual(drain._show(self.http_runtime().unit('php'))['MainPID'], '0')

    def test_activation_new_maintenance_after_interruption_is_not_removed(self):
        parent, document = self.prepared_activation(); engine, control = self.service.activation.engine(parent)
        engine._fault_hook = self.hook('web.activation.admission')
        self.kill_child(lambda: engine.apply(document['plan_sha256']))
        scope, _ = control.configuration()
        with scope.acquire(confirmed=True) as lease: new_lease = lease.lease_id
        result = self.build_service().execute('activation.resume', confirm(document))['activation']['installation']
        self.assertEqual(result['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(scope.observe()['lease_id'], new_lease)
        self.assertEqual(drain._show(self.http_runtime().unit('apache'))['MainPID'], '0')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--shard-index', type=int, choices=(0, 1), required=True); args = parser.parse_args()
    if os.environ.get('HESTIA_APPLICATION_ACTIVATION_TEST') != '1': raise RuntimeError('Explicit activation recipe opt-in required')
    wizard.journal.fresh.WEB = args.web; wizard.TARGET = args.target
    source = quality.snapshot(ROOT)
    names = sorted(n for n in ActivationLive.__dict__ if n.startswith('test_activation_'))[args.shard_index::2]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ActivationLive(n) for n in names))
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Product activation and current local availability', 'tests': result.testsRun, 'expected': 4,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 4 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'shard': args.shard_index,
        'service_activation_delivered': True, 'public_tls_delivered': False, 'boot_persistence_delivered': False,
        'phase5_complete': False, 'application_installed': False, 'upgrade_wizard_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'ACTIVATION-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
