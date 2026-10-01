#!/usr/bin/env python3
"""Exact Gateway binary + real Foundation/Web/SQL/systemd, disposable CI only."""
import argparse
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import foundation_systemd as previous
from github_fixture import confirm
from http_runtime_systemd import command
from installer import gateway_service_runtime as native
from installer.gateway_release import release, sha, verify_package

PACKAGE = Path('/opt/gateway-package.zip')


class GatewayLive(previous.FoundationLive):
    def dispose_profile(self):
        if self.profile is not None:
            unit = 'hestia-' + self.profile.instance + '-gateway.service'
            if hasattr(self, 'service'):
                doc = self.service.gateway_service.journal.read()
                if doc is not None:
                    Path('/evidence/gateway-service-transaction.json').write_bytes(quality.encode({
                        'state': doc['state'], 'error_code': doc['last_error_redacted'],
                        'steps': [{k: row[k] for k in ('name', 'state', 'phase', 'last_error_redacted')} for row in doc['steps']]}))
            command('systemctl', 'stop', unit, check=False)
            command('systemctl', 'reset-failed', unit, check=False)
            (native.drain.UNIT_ROOT / unit).unlink(missing_ok=True)
        super().dispose_profile()

    def sqlite_identity(self, runtime):
        with closing(sqlite3.connect(runtime.profile.state.as_uri() + '/gateway.db?mode=ro', uri=True)) as database:
            database.execute('PRAGMA query_only=ON')
            self.assertEqual(database.execute('PRAGMA quick_check').fetchall(), [('ok',)])
            self.assertEqual(database.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall(),
                             [(i,) for i in range(1, 7)])
            values = database.execute('SELECT installation_uuid FROM gateway_metadata WHERE singleton=1').fetchall()
            self.assertEqual(len(values), 1)
            import uuid
            self.assertEqual(uuid.UUID(values[0][0]).version, 4)
            return values[0][0]

    def test_gateway_real_credentials_recovery_main_and_coordinated_backup(self):
        from playwright.sync_api import expect
        self.managed(); parent, activation = self.prepared_activation()
        active = self.service.execute('activation.apply', confirm(activation))['activation']['installation']
        self.assertEqual(active['state'], 'DONE', active['last_error_redacted'])
        raw = PACKAGE.read_bytes(); self.assertEqual((len(raw), sha(raw)), (release()['package_bytes'], release()['package_sha256']))
        verify_package(io.BytesIO(raw), release())
        gateway = self.service.execute('gateway.plan', {'web_plan_sha256': parent['plan_sha256'],
            'public_origin': 'https://mobile.customer.example', 'dev_enabled': True, 'acquisition': 'package'})['gateway']['preparation']
        prepared = self.service.import_gateway_package(gateway['plan_sha256'], io.BytesIO(raw), len(raw))['gateway']['preparation']
        self.assertEqual(prepared['state'], 'DONE')
        parents = {'web': parent['plan_sha256'], 'activation': active['plan_sha256'], 'gateway': prepared['plan_sha256']}
        foundation = self.service.execute('foundation.plan', {'parents': parents})['foundation']['installation']
        self.assertEqual(self.service.execute('foundation.apply', confirm(foundation))['foundation']['installation']['state'], 'DONE')
        parents['foundation'] = foundation['plan_sha256']
        http = self.http_runtime(); store = self.service.gateway.identities
        protected = [self.service.engine.journal.path, self.service.activation.journal.path, self.service.gateway.journal.path,
            self.service.foundation.journal.path, store.root / 'main.pem', store.root / 'dev.pem',
            http.spec.root / 'conf/fpm.conf', http.spec.root / 'conf/apache.conf',
            *[native.drain.UNIT_ROOT / http.unit(role) for role in ('php', 'apache')]]
        preserved = {p: p.read_bytes() for p in protected}
        web_pids = {role: native.drain._show(http.unit(role))['MainPID'] for role in ('php', 'apache')}
        control = self.service.gateway_service
        document = self.service.execute('gateway-service.plan', {'parents': parents})['gateway_service']['installation']
        engine, runtime = control.engine(parent)
        with socket.socket() as foreign:
            foreign.bind(('127.0.0.1', 9083)); foreign.listen()
            failed = self.service.execute('gateway-service.apply', confirm(document))['gateway_service']['installation']
            self.assertEqual(failed['state'], 'FAILED'); self.assertFalse(runtime.root.exists())
            self.assertFalse(runtime.profile.account.directory.exists())
            self.assertEqual(foreign.getsockname(), ('127.0.0.1', 9083))
        engine.retry('gateway-service.identity', document['plan_sha256'])
        # Retry applies only the named step. Resume is a separate explicit action.
        engine, runtime = control.engine(parent)
        engine._fault_hook = self.hook('gateway-service.stage')
        self.kill_child(lambda: engine.resume(document['plan_sha256']))
        engine, runtime = control.engine(parent); runtime.inspect(); runtime.empty_state()
        engine._fault_hook = self.hook('gateway-service.start')
        self.kill_child(lambda: engine.resume(document['plan_sha256']))
        engine, runtime = control.engine(parent); self.assertTrue(runtime.owned())
        pid = runtime.show()['MainPID']; sqlite_uuid = self.sqlite_identity(runtime)
        account = runtime.account(); web_account = runtime.foundation.host()
        self.assertNotEqual(account.pw_uid, web_account.pw_uid); self.assertNotEqual(account.pw_gid, web_account.pw_gid)
        # A service may use its systemd credential, never read the source PEM.
        inaccessible = command('runuser', '-u', account.pw_name, '--', 'test', '-r', str(store.root / 'main.pem'), check=False)
        self.assertNotEqual(inaccessible.returncode, 0)
        nonce_before = {row['jti'] for row in self.sql(query=f'SELECT jti FROM `{self.db}`.Sec_Mobile_Service_Nonce')}
        with self.browser() as page:
            expect(page.locator('#resume-gatewayService')).to_be_visible()
            before = control.journal.path.read_bytes()
            page.locator('#resume-gatewayService').click(); page.keyboard.press('Escape')
            self.assertEqual(control.journal.path.read_bytes(), before)
            page.locator('#resume-gatewayService').click(); page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#gatewayService-state')).to_have_attribute('data-state', re.compile('DONE|FAILED|MANUAL_ACTION_REQUIRED'), timeout=120000)
            completed = control.journal.read()
            self.assertEqual(completed['state'], 'DONE', [(r['name'], r['last_error_redacted']) for r in completed['steps']])
            page.locator('#check-gatewayService').click()
            expect(page.locator('#gatewayService-availability')).to_contain_text('Gateway MAIN vérifiée', timeout=120000)
            page.reload(); expect(page.locator('#gatewayService-state')).to_have_attribute('data-state', 'DONE')
            page.locator('#gatewayService-main').screenshot(path='/evidence/gateway-service.png')
        self.assertEqual(runtime.show()['MainPID'], pid); self.assertEqual(self.sqlite_identity(runtime), sqlite_uuid)
        nonce_after = {row['jti'] for row in self.sql(query=f'SELECT jti FROM `{self.db}`.Sec_Mobile_Service_Nonce')}
        # Each signed request also purges expired nonces; compare new identities,
        # not the total population, which can shrink during native setup.
        self.assertGreaterEqual(len(nonce_after - nonce_before), 2)
        self.assertEqual(int(self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.Sec_Mobile_Enrollment')[0]['n']), 0)
        for path, original in preserved.items(): self.assertEqual(path.read_bytes(), original)
        for role, old_pid in web_pids.items(): self.assertEqual(native.drain._show(http.unit(role))['MainPID'], old_pid)
        self.service = self.build_service(); control = self.service.gateway_service; _, runtime = control.engine(parent)
        self.assertTrue(runtime.owned()); self.assertEqual(runtime.show()['MainPID'], pid)
        self.assertEqual(runtime.show()['UnitFileState'], 'static')
        self.fixture_login(http, already_active=True)
        self.nginx.terminate(); self.nginx.wait(timeout=10); self.nginx = None
        from installer import provisioned_backup, application_plan
        payload = deepcopy(self.saved['configuration'])
        payload.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'},
            secrets={'database_password': self.payload['secrets']['database_password'], 'admin_password': '', 'openai_api_key': ''})
        payload['database']['mode'] = 'existing_local'
        backup_root = self.profile.root / 'gateway-backup'; backup_root.mkdir(mode=0o700)
        operation = provisioned_backup.ProvisionedBackup(replace(self.profile.runtime(), timeout_seconds=120),
            previous.previous.wizard.TARGET, http, application_plan.cleaner.SessionCleaner(http))
        from installer import gateway_state_fence as state_fence, gateway_state_backup as state_backup
        from unittest.mock import patch
        import signal
        cache = runtime.profile.state / state_fence.CACHE
        logo = cache / ('a' * 64 + '.logo')
        import base64
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')
        logo_bytes = sha(png).encode() + b'\n' + png
        logo.write_bytes(logo_bytes); logo.chmod(0o600); os.chown(logo, account.pw_uid, account.pw_gid)
        old_fd = os.open(runtime.profile.state / 'gateway.db', os.O_RDWR)
        self.addCleanup(os.close, old_fd)
        original_flags = state_fence.inf._flags
        def cut_after_first_flag(fd, value=None):
            result = original_flags(fd, value)
            if value is not None: os.kill(os.getpid(), signal.SIGKILL)
            return result
        def interrupted_backup():
            with patch.object(state_fence.inf, '_flags', side_effect=cut_after_first_flag):
                operation.create_and_verify(payload, self.authority, config_root=self.profile.config_root,
                    backup_root=backup_root, confirmed=True, allow_global_read_lock=True)
        self.kill_child(interrupted_backup)
        scope, _ = runtime.foundation.activation.configuration()
        lease_id = scope.observe()['lease_id']
        self.assertTrue((scope.directory / state_fence.MARKER).exists())
        self.assertFalse(any(backup_root.iterdir()))
        backup = operation.create_and_verify(payload, self.authority, config_root=self.profile.config_root,
            backup_root=backup_root, confirmed=True, allow_global_read_lock=True,
            recover_lease_id=lease_id).report()
        Path('/evidence/gateway-backup.json').write_bytes(quality.encode(backup))
        self.assertEqual(backup['state'], 'MOBILE_BACKUP_RESTORE_VERIFIED', backup)
        self.assertTrue(backup['database_restoration_verified'] and backup['registered_data_restoration_verified'])
        self.assertFalse(backup['activity_resumed'])
        runtime.stopped(); runtime.foundation.stopped()
        self.assertTrue(backup['gateway_sqlite_restoration_verified'])
        snapshot_slot = backup_root / backup['gateway_snapshot_id']
        snapshot = json.loads((snapshot_slot / 'snapshot.json').read_bytes())
        self.assertEqual(snapshot['sqlite']['installation_uuid_sha256'], sha(sqlite_uuid.encode()))
        self.assertTrue(snapshot['isolated_restore_verified'])
        self.assertTrue(backup['gateway_editor_cache_restoration_verified'])
        self.assertTrue(snapshot['editor_cache'])
        self.assertEqual(snapshot['files'][state_fence.CACHE + '/' + logo.name],
                         {'bytes': len(logo_bytes), 'sha256': sha(logo_bytes)})
        self.assertEqual((snapshot_slot / 'source' / state_fence.CACHE / logo.name).read_bytes(), logo_bytes)
        self.assertEqual(logo.read_bytes(), logo_bytes)
        with self.assertRaises(PermissionError): logo.write_bytes(b'forbidden')
        # Immutable inodes protect writes even through a descriptor opened while
        # Gateway was active. A bind alias cannot evade that protection either.
        with self.assertRaises(PermissionError): os.write(old_fd, b'forbidden')
        alias = self.profile.root / 'gateway-alias'; alias.mkdir()
        command('mount', '--bind', str(runtime.profile.state), str(alias))
        try:
            for path in (runtime.profile.state / 'gateway.db', alias / 'gateway.db', alias / state_fence.CACHE / logo.name):
                with self.assertRaises(PermissionError): path.write_bytes(b'forbidden')
                with self.assertRaises(PermissionError): path.unlink()
            with self.assertRaises(PermissionError): (alias / 'unknown').write_bytes(b'forbidden')
            with self.assertRaises(PermissionError): (alias / state_fence.CACHE / ('b' * 64 + '.logo')).write_bytes(b'forbidden')
        finally: command('umount', str(alias))
        from installer import http_drain
        before_snapshot = (snapshot_slot / 'snapshot.json').read_bytes()
        with http_drain.HttpDrain(http, cleaner=application_plan.cleaner.SessionCleaner(http)).recover(
                lease_id, confirmed=True) as barrier:
            recovered_runtime = state_backup.gateway_service_drain.attached(http,
                state_backup.foundation_drain.attached(http))
            with state_fence.recover(recovered_runtime, barrier, confirmed=True) as recovered:
                with patch.object(state_backup, '_worker', side_effect=AssertionError('snapshot replay')):
                    saved = state_backup.recover_snapshot(recovered, self.profile.runtime(), backup_root, confirmed=True)
                    self.assertEqual(saved.raw, before_snapshot)
                # The low-level reopen boundary also refuses the new durable marker.
                with self.assertRaisesRegex(Exception, 'MAINTENANCE_DATA_ACCESS_CLOSED'):
                    barrier._lease.resume(confirmed=True)
        Path('/evidence/gateway-sqlite-snapshot.json').write_bytes(quality.encode(snapshot))
        scope, _ = runtime.foundation.activation.configuration()
        self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        for target in (runtime, runtime.foundation):
            command('systemctl', 'start', target.unit)
            self.assertEqual(target.show()['MainPID'], '0')
        before = control.journal.path.read_bytes()
        report = self.service.execute('gateway-service.check', confirm(document))['gateway_service']
        self.assertEqual(report['availability']['state'], 'GATEWAY_MAIN_UNAVAILABLE')
        self.assertEqual(control.journal.path.read_bytes(), before)
        for path, original in preserved.items(): self.assertEqual(path.read_bytes(), original)
        # 6B7a: explicit unseal remains subordinate to composed admission.
        from installer import gateway_state_release as state_release
        def release_interrupted():
            with http_drain.HttpDrain(http, cleaner=application_plan.cleaner.SessionCleaner(http)).recover(
                    lease_id, confirmed=True) as barrier:
                attached = state_backup.gateway_service_drain.attached(http, state_backup.foundation_drain.attached(http))
                with state_fence.recover(attached, barrier, confirmed=True) as fence:
                    saved = state_backup.recover_snapshot(fence, self.profile.runtime(), backup_root, confirmed=True)
                    with patch.object(state_fence.inf, '_flags', side_effect=cut_after_first_flag):
                        state_release.release(saved, confirmed=True)
        self.kill_child(release_interrupted)
        self.assertTrue((scope.directory / state_release.RELEASE).exists())
        self.assertTrue((scope.directory / state_fence.MARKER).exists())
        original_unlink = os.unlink
        def cut_after_attempt_removal(path, *args, **kwargs):
            original_unlink(path, *args, **kwargs)
            if path == state_fence.MARKER: os.kill(os.getpid(), signal.SIGKILL)
        def recovery_interrupted():
            with http_drain.HttpDrain(http, cleaner=application_plan.cleaner.SessionCleaner(http)).recover(
                    lease_id, confirmed=True) as barrier:
                attached = state_backup.gateway_service_drain.attached(http, state_backup.foundation_drain.attached(http))
                with patch.object(state_release.os, 'unlink', side_effect=cut_after_attempt_removal):
                    state_release.recover(attached, barrier, backup_root, confirmed=True)
        self.kill_child(recovery_interrupted)
        self.assertTrue((scope.directory / state_release.RELEASED).exists())
        self.assertTrue((scope.directory / state_release.RELEASE).exists())
        self.assertFalse((scope.directory / state_fence.MARKER).exists())
        with http_drain.HttpDrain(http, cleaner=application_plan.cleaner.SessionCleaner(http)).recover(
                lease_id, confirmed=True) as barrier:
            attached = state_backup.gateway_service_drain.attached(http, state_backup.foundation_drain.attached(http))
            with patch.object(state_backup, '_worker', side_effect=AssertionError('worker replay during release')):
                released = state_release.recover(attached, barrier, backup_root, confirmed=True).report()
                receipt = (scope.directory / state_release.RELEASED).read_bytes()
                repeated = state_release.recover(attached, barrier, backup_root, confirmed=True).report()
            self.assertEqual(released, repeated)
            self.assertEqual((scope.directory / state_release.RELEASED).read_bytes(), receipt)
            with self.assertRaises(state_fence.GatewayStateError): state_fence.recover(attached, barrier, confirmed=True)
            with self.assertRaisesRegex(Exception, 'MAINTENANCE_DATA_ACCESS_CLOSED'): barrier._lease.resume(confirmed=True)
        self.assertFalse((scope.directory / state_release.RELEASE).exists())
        self.assertFalse(released['activity_resumed']); self.assertFalse(released['services_started'])
        self.assertFalse(released['restore_to_original_allowed'])
        self.assertEqual(logo.read_bytes(), logo_bytes)
        for path in (runtime.profile.state, runtime.profile.state / 'gateway.db', cache, logo):
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try: self.assertFalse(state_fence.inf._flags(fd) & state_fence.inf.IMMUTABLE)
            finally: os.close(fd)
        runtime.stopped(); runtime.foundation.stopped()
        for target in (runtime, runtime.foundation):
            command('systemctl', 'start', target.unit)
            self.assertEqual(target.show()['MainPID'], '0')
        self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        for path, original in preserved.items(): self.assertEqual(path.read_bytes(), original)
        Path('/evidence/gateway-release.json').write_bytes(quality.encode(released))
        # 6B7b3: execute the qualified file subplan through real native readers.
        from mobile_reopen_files_systemd import exercise
        file_release = exercise(self, http, runtime, scope, lease_id, backup_root, preserved)
        # 6B7b4: no guard consumption; observation only under a live SQL fence.
        from mobile_reopen_admission_systemd import exercise as admission_exercise
        admission = admission_exercise(self, http, runtime, scope, lease_id, backup_root,
            operation._runtime, operation._source, payload, self.authority, preserved)
        # 6B7b5b: hold fresh SQL admission across recoverable external release.
        from mobile_external_admission_systemd import exercise as external_exercise
        external_admission = external_exercise(self, http, runtime, scope, lease_id, backup_root,
            operation._runtime, operation._source, payload, self.authority, preserved)
        # 6B7b6b candidate: fresh SQL across recoverable data-path reopening.
        from mobile_data_admission_systemd import exercise as data_exercise
        data_admission = data_exercise(self, http, runtime, scope, lease_id, backup_root,
            operation._runtime, operation._source, payload, self.authority, preserved)
        # 6B7b7b: durable preparation and explicit recovery under native admission.
        from mobile_resume_plan_systemd import exercise as resume_exercise
        resume_plan = resume_exercise(self, http, runtime, scope, lease_id, backup_root,
            operation._runtime, operation._source, payload, self.authority, preserved)
        # 6B7b8: recoverable old-blocker handoff under fresh native SQL admission.
        from mobile_blocker_admission_systemd import exercise as blocker_exercise
        blocker_admission = blocker_exercise(self, http, runtime, scope, lease_id, backup_root,
            operation._runtime, operation._source, payload, self.authority, preserved, resume_plan['plan_sha256'])
        # SQLite mode=ro may still create/remove WAL sidecars. Keep this native
        # identity oracle after all exact Gateway file-fence observations.
        self.assertEqual(self.sqlite_identity(runtime), sqlite_uuid)
        Path('/evidence/gateway-contract.json').write_bytes(quality.encode({'status': 'PASS',
            'gateway_commit': release()['commit'], 'package_sha256': release()['package_sha256'],
            'binary_sha256': release()['binary_sha256'], 'foreign_9083_refused_before_account': True,
            'dedicated_identity_and_private_systemd_credential': True, 'stage_and_start_lost_reply_recovered': True,
            'real_browser_consent_and_refresh': True, 'signed_gateway_main_roundtrip': True,
            'origin_and_forwarding_negatives': True, 'sqlite_schema': 6, 'sqlite_uuid_preserved': True,
            'web_pids_units_login_and_parent_journals_preserved': True, 'main_dev_keys_preserved': True,
            'web_backup_sql_and_data_restore_verified': True, 'gateway_and_foundation_stopped': True,
            'gated_explicit_starts_refused': True, 'stopped_done_never_restarted': True,
            'gateway_sqlite_backup_qualified': True, 'gateway_editor_cache_backup_restore_and_writes_fenced': True,
            'gateway_fence_sigkill_recovery': True, 'gateway_release_sigkill_recovery': True,
            'gateway_release_receipt_precedes_old_marker_removal': True, 'gateway_release_keeps_activity_closed': True,
            'gateway_release_completed_recovery_without_worker_or_restart': True,
            'mobile_file_release_native': file_release,
            'mobile_admission_native': admission,
            'mobile_external_admission_native': external_admission,
            'mobile_data_admission_native': data_admission,
            'mobile_resume_plan_native': resume_plan,
            'mobile_blocker_admission_native': blocker_admission,
            'gateway_old_fd_and_bind_alias_writes_denied': True, 'gateway_snapshot_recovery_without_replay': True, 'public_mobile_delivered': False, 'boot_delivered': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('HESTIA_GATEWAY_SERVICE_TEST') != '1': raise RuntimeError('Disposable Gateway recipe opt-in required')
    previous.previous.wizard.journal.fresh.WEB = args.web; previous.previous.wizard.TARGET = args.target
    source = quality.snapshot(ROOT)
    import test_gateway_state, test_gateway_state_release
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromModule(test_gateway_state),
        unittest.defaultTestLoader.loadTestsFromModule(test_gateway_state_release),
        GatewayLive('test_gateway_real_credentials_recovery_main_and_coordinated_backup')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Gateway MAIN native service, backup and guarded file release', 'tests': result.testsRun, 'expected': 36,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 36 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'phase6_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'GATEWAY-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
