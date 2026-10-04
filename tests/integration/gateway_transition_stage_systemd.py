#!/usr/bin/env python3
"""Real frozen MAIN/SQLite and both exact packages; no active binary cutover."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import gateway_service_systemd as previous
from github_fixture import confirm
from http_runtime_systemd import command
from installer import gateway_transition_stage as stage, http_drain, application_plan
from installer.gateway_transition import LEGACY_COMMIT, FCM_COMMIT


class TransitionStageLive(previous.GatewayLive):
    def exercise_stage(self, source_pin, target_pin, direction):
        self.managed(); parent, active = self.prepared_activation()
        active = self.service.execute('activation.apply', confirm(active))['activation']['installation']
        self.assertEqual(active['state'], 'DONE')
        package = Path('/opt/gateway-package.zip' if source_pin == LEGACY_COMMIT else '/opt/gateway-target-package.zip')
        target = Path('/opt/gateway-target-package.zip' if target_pin == FCM_COMMIT else '/opt/gateway-package.zip')
        gateway = self.service.execute('gateway.plan', {'web_plan_sha256': parent['plan_sha256'],
            'public_origin': 'https://mobile.customer.example', 'dev_enabled': True,
            'release_commit': source_pin, 'acquisition': 'package'})['gateway']['preparation']
        raw = package.read_bytes()
        gateway = self.service.import_gateway_package(gateway['plan_sha256'], io.BytesIO(raw), len(raw))['gateway']['preparation']
        parents = {'web': parent['plan_sha256'], 'activation': active['plan_sha256'], 'gateway': gateway['plan_sha256']}
        foundation = self.service.execute('foundation.plan', {'parents': parents})['foundation']['installation']
        self.assertEqual(self.service.execute('foundation.apply', confirm(foundation))['foundation']['installation']['state'], 'DONE')
        parents['foundation'] = foundation['plan_sha256']
        service = self.service.execute('gateway-service.plan', {'parents': parents})['gateway_service']['installation']
        self.assertEqual(self.service.execute('gateway-service.apply', confirm(service))['gateway_service']['installation']['state'], 'DONE')
        parents['gateway_service'] = service['plan_sha256']
        http = self.http_runtime(); _, runtime = self.service.gateway_service.engine(parent)
        uuid = self.sqlite_identity(runtime)
        self.fixture_login(http, already_active=True)
        self.nginx.terminate(); self.nginx.wait(timeout=10); self.nginx = None
        protected = [self.service.engine.journal.path, self.service.activation.journal.path,
            self.service.gateway.journal.path, self.service.foundation.journal.path,
            self.service.gateway_service.journal.path, runtime.profile.binary, runtime.profile.config,
            runtime.root / 'staged.json', runtime.fragment, *self.service.gateway.identities.root.glob('*.pem')]
        preserved = {path: path.read_bytes() for path in protected}
        backup = self.service.execute('mobile-backup.plan', {'parents': parents})['mobile_backup']
        values = {'database_password': self.payload['secrets']['database_password'],
            'authority_user': self.authority._user, 'authority_password': self.authority._password}
        done = self.service.execute('mobile-backup.apply', {'confirmation': backup['confirmation'], 'confirm': True,
            'credentials': values, 'allow_global_read_lock': True})['mobile_backup']
        self.assertEqual(done['state'], 'DONE')
        control = self.service.mobile_backup; profile = control.profile(); _, lease = control.records(profile)
        lease_id = lease['lease_id']; backups = control.backups(profile)
        packages = self.profile.root / 'transition-packages'; packages.mkdir(mode=0o700)
        for name, source in (('source.zip', package), ('target.zip', target)):
            (packages / name).write_bytes(source.read_bytes()); (packages / name).chmod(0o600)
        kwargs = {'source_package': packages / 'source.zip', 'target_package': packages / 'target.zip',
            'target_commit': target_pin, 'direction': direction, 'confirmed': True}
        def execute(recovery):
            with http_drain.HttpDrain(http, cleaner=application_plan.cleaner.SessionCleaner(http)).recover(
                    lease_id, confirmed=True) as barrier:
                attached = stage.b.gateway_service_drain.attached(http, stage.b.foundation_drain.attached(http))
                with stage.g.recover(attached, barrier, confirmed=True) as fence:
                    snapshot = stage.b.recover_snapshot(fence, self.profile.runtime(), backups, confirmed=True)
                    return stage.prepare(snapshot, recovery=recovery, **kwargs).report()
        # Real process death closes its flock. The durable maintenance and Ext4
        # immutable flags survive every restart of this preparer.
        original_write, original_rename, original_new = stage.files._write, os.rename, stage.files._new
        for boundary in ('partial', 'source.bin', 'target.bin', 'receipt'):
            def interrupted():
                def write(fd, raw):
                    original_write(fd, raw)
                    if boundary == 'partial' and len(raw) == 1024 * 1024: os.kill(os.getpid(), signal.SIGKILL)
                def rename(src, dst, **options):
                    original_rename(src, dst, **options)
                    if dst == boundary: os.kill(os.getpid(), signal.SIGKILL)
                def create(fd, name, raw):
                    original_new(fd, name, raw)
                    if boundary == 'receipt' and name == 'prepared.json': os.kill(os.getpid(), signal.SIGKILL)
                with patch.object(stage.files, '_write', write), patch.object(stage.os, 'rename', rename), patch.object(stage.files, '_new', create):
                    execute(boundary != 'partial')
            self.kill_child(interrupted)
            runtime.stopped(); runtime.foundation.stopped()
            for path, raw in preserved.items(): self.assertEqual(path.read_bytes(), raw)
        slot = backups / ('gateway-transition-' + lease_id)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in slot.iterdir()}
        with patch.object(stage.b, '_worker', side_effect=AssertionError('SQLite worker replay')):
            result = execute(True)
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in slot.iterdir()})
        for name, pin in (('source.bin', source_pin), ('target.bin', target_pin)):
            path = slot / name
            self.assertEqual(stage.sha(path.read_bytes()), previous.release(pin)['binary_sha256'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result['intent']['sqlite']['installation_uuid_sha256'], hashlib.sha256(uuid.encode()).hexdigest())
        self.assertEqual(result['intent']['sqlite']['sqlite_schema'], 6)
        for path, raw in preserved.items(): self.assertEqual(path.read_bytes(), raw)
        account, _, _, _ = http._inspect_configuration(); scope = http._scope(account)
        self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        for service in (runtime, runtime.foundation):
            command('systemctl', 'start', service.unit)
            self.assertEqual(service.show()['MainPID'], '0')
        with self.assertRaises(PermissionError): (runtime.profile.state / 'gateway.db').write_bytes(b'forbidden')
        for key in ('active_profile_changed', 'activity_resumed', 'rollback_verified', 'boot_requalified', 'phase6_complete'):
            self.assertFalse(result[key])
        Path('/evidence/gateway-transition-' + direction + '.json').write_bytes(quality.encode({
            'status': 'PASS', 'source': source_pin, 'target': target_pin, 'direction': direction,
            'exact_package_binaries': True, 'sigkill_boundaries': ['partial', 'source.bin', 'target.bin', 'receipt'],
            'completed_recovery_read_only': True, 'real_ext4_fence_retained': True,
            'unchanged_active_binary_config_unit_keys_parents': True, 'gated_service_start_refused': True,
            'sqlite_schema': 6, 'installation_uuid_preserved': True, 'prepared': result}))

    def test_upgrade_preparation_native_sigkill_and_preservation(self):
        self.exercise_stage(LEGACY_COMMIT, FCM_COMMIT, 'upgrade')

    def test_rollback_preparation_native_sigkill_and_preservation(self):
        self.exercise_stage(FCM_COMMIT, LEGACY_COMMIT, 'rollback')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('HESTIA_GATEWAY_TRANSITION_TEST') != '1': raise RuntimeError('Disposable recipe opt-in required')
    previous.previous.previous.wizard.journal.fresh.WEB = args.web
    previous.previous.previous.wizard.TARGET = args.target
    source = quality.snapshot(ROOT)
    suite = unittest.TestSuite(TransitionStageLive(name) for name in (
        'test_upgrade_preparation_native_sigkill_and_preservation', 'test_rollback_preparation_native_sigkill_and_preservation'))
    result = unittest.TextTestRunner(verbosity=2).run(suite); stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Gateway transition private binary preparation', 'tests': result.testsRun, 'expected': 2,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 2 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'active_profile_changed': False, 'phase6_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'GATEWAY-TRANSITION-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
