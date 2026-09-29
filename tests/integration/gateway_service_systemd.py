#!/usr/bin/env python3
"""Exact Gateway binary + real Foundation/Web/SQL/systemd, disposable CI only."""
import argparse
from copy import deepcopy
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
        with sqlite3.connect(runtime.profile.state.as_uri() + '/gateway.db?mode=ro', uri=True) as database:
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
        backup = operation.create_and_verify(payload, self.authority, config_root=self.profile.config_root,
            backup_root=backup_root, confirmed=True, allow_global_read_lock=True).report()
        Path('/evidence/gateway-backup.json').write_bytes(quality.encode(backup))
        self.assertEqual(backup['state'], 'PROVISIONED_BACKUP_RESTORE_VERIFIED', backup)
        self.assertTrue(backup['database_restoration_verified'] and backup['registered_data_restoration_verified'])
        self.assertFalse(backup['activity_resumed'])
        runtime.stopped(); runtime.foundation.stopped()
        self.assertEqual(self.sqlite_identity(runtime), sqlite_uuid)
        scope, _ = runtime.foundation.activation.configuration()
        self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        for unit in (runtime.unit, runtime.foundation.unit):
            command('systemctl', 'start', unit)
            self.assertEqual(native.drain._show(unit)['MainPID'], '0')
        before = control.journal.path.read_bytes()
        report = self.service.execute('gateway-service.check', confirm(document))['gateway_service']
        self.assertEqual(report['availability']['state'], 'GATEWAY_MAIN_UNAVAILABLE')
        self.assertEqual(control.journal.path.read_bytes(), before)
        for path, original in preserved.items(): self.assertEqual(path.read_bytes(), original)
        Path('/evidence/gateway-contract.json').write_bytes(quality.encode({'status': 'PASS',
            'gateway_commit': release()['commit'], 'package_sha256': release()['package_sha256'],
            'binary_sha256': release()['binary_sha256'], 'foreign_9083_refused_before_account': True,
            'dedicated_identity_and_private_systemd_credential': True, 'stage_and_start_lost_reply_recovered': True,
            'real_browser_consent_and_refresh': True, 'signed_gateway_main_roundtrip': True,
            'origin_and_forwarding_negatives': True, 'sqlite_schema': 6, 'sqlite_uuid_preserved': True,
            'web_pids_units_login_and_parent_journals_preserved': True, 'main_dev_keys_preserved': True,
            'web_backup_sql_and_data_restore_verified': True, 'gateway_and_foundation_stopped': True,
            'gated_explicit_starts_refused': True, 'stopped_done_never_restarted': True,
            'gateway_sqlite_backup_qualified': False, 'public_mobile_delivered': False, 'boot_delivered': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('HESTIA_GATEWAY_SERVICE_TEST') != '1': raise RuntimeError('Disposable Gateway recipe opt-in required')
    previous.previous.wizard.journal.fresh.WEB = args.web; previous.previous.wizard.TARGET = args.target
    source = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([GatewayLive(
        'test_gateway_real_credentials_recovery_main_and_coordinated_backup')]))
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Gateway MAIN native service and backup', 'tests': result.testsRun, 'expected': 1,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'phase6_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'GATEWAY-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
