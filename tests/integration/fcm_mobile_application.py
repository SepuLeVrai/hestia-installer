#!/usr/bin/env python3
"""Native FCM credential binding through maintenance and new PID 1, CI only.

The account is synthetic; no Google authorization or phone delivery is claimed.
The private Docker network cannot reach Google. The real pinned Gateway binary
executes its credential precheck through the installed systemd LoadCredential.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import fcm_fixture
import mobile_web_application as mobile
from installer.gateway_release import FCM_COMMIT


def verify_credential(case):
    service = mobile.fixture.service(); case.addCleanup(service.close)
    engine, runtime = service.gateway_service.engine(service.engine.report())
    case.assertEqual(engine.report()['state'], 'DONE')
    case.assertEqual(runtime.profile.selected_release['commit'], FCM_COMMIT)
    case.assertEqual(runtime.profile.configuration()['project_push_project_id'], 'hestia-test')
    runtime.key_binding(); runtime.owned()
    report = service.fcm.store.verify()
    case.assertEqual(runtime.profile.push, {'selection': report['profile'], 'receipt': report['receipt']})
    source = service.fcm.store.root / 'server.json'
    case.assertEqual(source.stat().st_mode & 0o777, 0o600)
    case.assertEqual(service.fcm.store.root.stat().st_mode & 0o777, 0o700)
    check = subprocess.run(['systemctl', 'show', '--property=ExecStartPre', '--value', runtime.unit],
        check=True, capture_output=True, text=True, timeout=15).stdout
    case.assertIn('--check-push-credential', check)
    case.assertNotIn('status=1', check); case.assertIn('status=0', check)
    logs = subprocess.run(['journalctl', '--no-pager', '-u', runtime.unit],
        check=True, capture_output=True, timeout=15).stdout
    for forbidden in (b'PRIVATE KEY', b'sender@', b'ya29.'):
        case.assertTrue(forbidden not in logs, 'Credential material appeared in service logs')
    stored = json.loads((mobile.EVIDENCE / 'shared-application-parents.json').read_bytes())
    for name in ('server.json', 'profile.json', 'receipt.json'):
        path = service.fcm.store.root / name
        case.assertEqual(mobile.hashlib.sha256(path.read_bytes()).hexdigest(), stored[str(path)])


class Verify(mobile.Verify):
    def test_fcm_project_credential_and_private_files_survive_maintenance(self): verify_credential(self)


class Restart(mobile.Restart):
    def test_fcm_credential_is_reloaded_after_new_pid1(self): verify_credential(self)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('setup', 'serve', 'browser', 'verify', 'restart'))
    phase = parser.parse_args().phase
    if os.environ.get('HESTIA_SHARED_APPLICATION_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Disposable opt-in required')
    if phase != 'browser' and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('Real PID 1 required')
    if phase == 'setup': mobile.setup(gateway_commit=FCM_COMMIT, credential=fcm_fixture.credential()); sys.exit(0)
    if phase == 'serve': mobile.shared.serve(); sys.exit(0)
    before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(
        {'browser': mobile.shared.Browser, 'verify': Verify, 'restart': Restart}[phase]))
    stable = quality.snapshot(ROOT) == before; expected = 1 if phase == 'browser' else 3
    passed = result.wasSuccessful() and result.testsRun == expected and not result.skipped and stable
    report = {'suite': 'fcm-mobile-application-' + phase, 'status': 'PASS' if passed else 'FAIL', 'tests': result.testsRun,
        'expected': expected, 'errors': len(result.errors), 'failures': len(result.failures), 'skips': len(result.skipped),
        'source_stable': stable, 'source_files': len(before), 'web_source_commit': mobile.source.COMMIT,
        'gateway_source_commit': FCM_COMMIT, 'real_web_gateway': True, 'acme': 'private Pebble',
        'mobile_boot_qualified': passed and phase == 'restart', 'google_authorization_verified': False,
        'phone_delivery_verified': False, 'phase6_complete': False}
    (mobile.EVIDENCE / ('fcm-mobile-application-' + phase + '.json')).write_bytes(quality.encode(report))
    (mobile.EVIDENCE / ('SOURCE-MANIFEST-fcm-mobile-application-' + phase + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
