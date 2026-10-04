#!/usr/bin/env python3
"""Native active-profile publication; original enrollment and fences are preserved."""
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


from installer import gateway_transition_cutover as cutover, gateway_active_profile as publication


class ActiveProfileLive(previous.GatewayLive):
    def exercise_publication(self, source_pin, target_pin, direction):
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
            self.service.gateway_service.journal.path, runtime.profile.config,
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
        # Keep the original typed runtime bound to this exact HTTP object. Its
        # ordinary inspector will deliberately refuse the target binary.
        runtime = stage.b.gateway_service_drain.attached(http, stage.b.foundation_drain.attached(http))
        with http_drain.HttpDrain(http, cleaner=application_plan.cleaner.SessionCleaner(http)).recover(
                lease_id, confirmed=True) as barrier:
            with stage.g.recover(runtime, barrier, confirmed=True) as fence:
                snapshot = stage.b.recover_snapshot(fence, self.profile.runtime(), backups, confirmed=True)
                stage.prepare(snapshot, **kwargs)
        def initial():
            with http_drain.HttpDrain(http, cleaner=application_plan.cleaner.SessionCleaner(http)).recover(
                    lease_id, confirmed=True) as barrier:
                with stage.g.recover(runtime, barrier, confirmed=True) as fence:
                    snapshot = stage.b.recover_snapshot(fence, self.profile.runtime(), backups, confirmed=True)
                    return cutover.apply(snapshot, **kwargs)
        def resume(action='resume'):
            return cutover.recover(runtime, backups, lease_id, action=action, **kwargs)
        def state_bytes():
            return {path.relative_to(runtime.profile.state).as_posix():
                (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
                for path in runtime.profile.state.rglob('*') if path.is_file()}
        state_before = state_bytes(); initial()
        def publish(action='resume'):
            return publication.publish(runtime, backups, lease_id, action=action, **kwargs)
        boundaries = []; original_new = publication.files._new
        for name in (publication.MARKER, publication.INTENT, publication.ACTIVE):
            def interrupted():
                def create(fd, filename, raw):
                    original_new(fd, filename, raw)
                    if filename == name: os.kill(os.getpid(), signal.SIGKILL)
                with patch.object(publication.files, '_new', create):
                    publish('apply' if name == publication.MARKER else 'resume')
            self.kill_child(interrupted); boundaries.append(name)
            self.assertEqual(state_bytes(), state_before)
            for path, raw in preserved.items(): self.assertEqual(path.read_bytes(), raw)
            with self.assertRaises(Exception): resume('rollback')
            self.assertFalse((runtime.root / 'control' / ('cutover-' + lease_id) / 'rollback.json').exists())
        with patch.object(stage.b, '_worker', side_effect=AssertionError('SQLite replay')):
            result = publish()
        selected = stage.b.gateway_service_drain.attached(http, stage.b.foundation_drain.attached(http))
        selected.stopped()
        self.assertEqual(selected.profile.selected_release['commit'], target_pin)
        self.assertEqual(selected.manifest(selected.account()), result['intent']['target_manifest'])
        self.assertEqual(selected._enrolled_manifest, runtime.manifest(runtime.account()))
        self.assertEqual(selected.state_binding(), runtime.state_binding())
        with self.assertRaises(Exception): runtime.inspect()
        self.assertEqual(stage.sha(selected.profile.binary.read_bytes()), previous.release(target_pin)['binary_sha256'])
        paths = [runtime.root / 'control' / publication.INTENT, runtime.root / 'control' / publication.ACTIVE,
                 http.spec.maintenance_directory / publication.MARKER, runtime.profile.binary]
        before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in paths}
        self.assertEqual(result, publish('check')); self.assertEqual(result, publish('resume'))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in paths})
        account, _, _, _ = http._inspect_configuration(); scope = http._scope(account)
        self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        for service in (selected, selected.foundation):
            command('systemctl', 'start', service.unit)
            self.assertEqual(service.show()['MainPID'], '0')
        with scope.recover(lease_id, confirmed=True) as held:
            with self.assertRaises(Exception): held.resume(confirmed=True)
        with self.assertRaises(PermissionError): (runtime.profile.state / 'gateway.db').write_bytes(b'forbidden')
        self.assertEqual(state_bytes(), state_before)
        for path, raw in preserved.items(): self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(result['active_profile_changed'])
        for key in ('activity_resumed', 'services_started', 'sqlite_restored', 'rollback_verified', 'boot_requalified', 'phase6_complete'):
            self.assertFalse(result[key])
        Path('/evidence/gateway-active-profile-' + direction + '.json').write_bytes(quality.encode({
            'status': 'PASS', 'source': source_pin, 'target': target_pin, 'direction': direction,
            'sigkill_boundaries': boundaries, 'real_ext4_fence_retained': True,
            'current_native_reader_selects_target': True, 'historical_reader_refuses_target': True,
            'old_file_rollback_blocked_from_first_intent': True, 'completed_recovery_read_only': True,
            'configuration_unit_keys_parents_preserved': True, 'gated_service_start_refused': True,
            'sqlite_bytes_and_inodes_preserved': True,
            'installation_uuid_sha256': hashlib.sha256(uuid.encode()).hexdigest(), 'sqlite_schema': 6,
            'result': result}))
        return {'http': http, 'runtime': runtime, 'scope': scope, 'backups': backups,
                'lease_id': lease_id, 'packages': kwargs, 'uuid': uuid, 'preserved': preserved}

    def test_upgrade_active_profile_native_sigkill(self):
        self.exercise_publication(LEGACY_COMMIT, FCM_COMMIT, 'upgrade')

    def test_downgrade_active_profile_native_sigkill(self):
        self.exercise_publication(FCM_COMMIT, LEGACY_COMMIT, 'rollback')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('HESTIA_GATEWAY_ACTIVE_PROFILE_TEST') != '1': raise RuntimeError('Disposable recipe opt-in required')
    previous.previous.previous.wizard.journal.fresh.WEB = args.web
    previous.previous.previous.wizard.TARGET = args.target
    source = quality.snapshot(ROOT)
    suite = unittest.TestSuite(ActiveProfileLive(name) for name in (
        'test_upgrade_active_profile_native_sigkill', 'test_downgrade_active_profile_native_sigkill'))
    result = unittest.TextTestRunner(verbosity=2).run(suite); stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Gateway active-profile publication under maintenance', 'tests': result.testsRun, 'expected': 2,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 2 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'active_profile_changed': True, 'phase6_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'GATEWAY-ACTIVE-PROFILE-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
