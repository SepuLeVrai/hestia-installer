#!/usr/bin/env python3
"""Focused MAIN recipe: actual systemd, Apache, guarded FPM, SQL and browser.

Only the binary download uses the inert existing catalogue fixture: no Gateway
binary is executed or claimed. Actual package qualification remains phase 6B3.
"""
import argparse
import base64
import fcntl
import http.client
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import application_activation_systemd as previous
from gateway_fixture import ArtifactResponses
from github_fixture import confirm
from installer import foundation_probe as probe, foundation_runtime as native
from installer.gateway_identity import _b64
from installer.model import InstallerError, canonical_bytes
from http_runtime_systemd import command, until


class FoundationLive(previous.ActivationLive):
    def dispose_profile(self):
        if self.profile is not None:
            unit = 'hestia-' + self.profile.instance + '-foundation.service'
            config = self.profile.root / 'foundation/apache.conf'
            if config.exists():
                diagnostic = subprocess.run(['/usr/sbin/apache2', '-t', '-f', str(config)], capture_output=True, timeout=5)
                Path('/evidence/foundation-configtest.txt').write_bytes(diagnostic.stderr[:16384])
            error_log = self.profile.root / 'foundation/log/error.log'
            if error_log.exists(): Path('/evidence/foundation-apache-errors.txt').write_bytes(error_log.read_bytes()[-16384:])
            if hasattr(self, 'service'):
                document = self.service.foundation.journal.read()
                if document is not None:
                    diagnostic = {'state': document['state'], 'error_code': document['last_error_redacted'],
                        'steps': [{k: row[k] for k in ('name', 'state', 'phase', 'last_error_redacted')} for row in document['steps']]}
                    Path('/evidence/foundation-transaction.json').write_bytes(quality.encode(diagnostic))
            command('systemctl', 'stop', unit, check=False)
            command('systemctl', 'reset-failed', unit, check=False)
            (native.drain.UNIT_ROOT / unit).unlink(missing_ok=True)
        super().dispose_profile()

    def request(self, raw=b'{}', token='invalid.invalid.invalid', request_id='00000000-0000-4000-8000-000000000001',
                path=probe.PATH, method='POST', port=9082):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request(method, path, body=raw, headers={'Host': 'hestia-internal-mobile.local',
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token, 'X-Request-ID': request_id})
            response = connection.getresponse(); data = response.read(65537)
            self.assertLessEqual(len(data), 65536)
            return response.status, data, dict(response.getheaders())
        finally: connection.close()

    def altered(self, store, identity, change, environment='main'):
        body, raw, token = probe.assertion(store, identity)
        header, claims, _ = token.split('.')
        claims = json.loads(base64.urlsafe_b64decode(claims + '=' * (-len(claims) % 4))); claims.update(change)
        if environment == 'dev':
            header = _b64(canonical_bytes({'alg': 'ES256', 'typ': 'hestia-service+jwt',
                'kid': store.report()['receipt']['identities']['dev']['kid']}))
        signing = (header + '.' + _b64(canonical_bytes(claims))).encode()
        signature = subprocess.run(['/usr/bin/openssl', 'dgst', '-sha256', '-sign', str(store.root / (environment + '.pem'))],
            input=signing, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=5).stdout
        return body, raw, signing.decode() + '.' + _b64(probe.raw_signature(signature))

    def test_foundation_main_real_connection_recovery_guard_and_web_preservation(self):
        from playwright.sync_api import expect
        self.managed(); parent, activation = self.prepared_activation()
        active = self.service.execute('activation.apply', confirm(activation))['activation']['installation']
        self.assertEqual(active['state'], 'DONE', active['last_error_redacted'])
        responses = ArtifactResponses()
        with patch('installer.gateway_release._RELEASE', responses.selected):
            gateway = self.service.execute('gateway.plan', {'web_plan_sha256': parent['plan_sha256'],
                'public_origin': 'https://mobile.customer.example', 'dev_enabled': True, 'acquisition': 'package'})['gateway']['preparation']
            prepared = self.service.import_gateway_package(gateway['plan_sha256'], io.BytesIO(responses.package), len(responses.package))['gateway']['preparation']
            self.assertEqual(prepared['state'], 'DONE')
            control = self.service.foundation; http = self.http_runtime(); store = self.service.gateway.identities
            protected = [self.service.engine.journal.path, self.service.activation.journal.path, self.service.gateway.journal.path,
                         store.root / 'main.pem', store.root / 'dev.pem', http.spec.root / 'conf/fpm.conf',
                         http.spec.root / 'conf/apache.conf', *[native.drain.UNIT_ROOT / http.unit(r) for r in ('php', 'apache')]]
            preserved = {p: p.read_bytes() for p in protected}
            web_pids = {r: native.drain._show(http.unit(r))['MainPID'] for r in ('php', 'apache')}
            document = self.service.execute('foundation.plan', {'parents': {'web': parent['plan_sha256'],
                'activation': active['plan_sha256'], 'gateway': prepared['plan_sha256']}})['foundation']['installation']
            engine, runtime = control.engine(parent)
            # Real collision: a foreign loopback socket survives the refusal.
            import socket
            with socket.socket() as foreign:
                foreign.bind(('127.0.0.1', 9082)); foreign.listen()
                result = self.service.execute('foundation.apply', confirm(document))['foundation']['installation']
                self.assertEqual(result['state'], 'FAILED'); self.assertFalse(runtime.root.exists())
                self.assertEqual(foreign.getsockname(), ('127.0.0.1', 9082))
            engine._fault_hook = self.hook('foundation.stage')
            self.kill_child(lambda: engine.retry('foundation.stage', document['plan_sha256']))
            engine, runtime = control.engine(parent); runtime.inspect()
            engine._fault_hook = self.hook('foundation.start')
            self.kill_child(lambda: engine.resume(document['plan_sha256']))
            pid = runtime.show()['MainPID']; self.assertTrue(runtime.owned())
            body, raw, token = probe.assertion(store, runtime.identity)
            status, data, headers = self.request(raw, token, body['request_id'])
            try: parsed = json.loads(data)
            except ValueError: parsed = {}
            diagnostic = {'status': status, 'content_type': headers.get('Content-Type'),
                'cache_control': headers.get('Cache-Control'), 'set_cookie_present': 'Set-Cookie' in headers,
                'response_keys': sorted(parsed) if type(parsed) is dict else [],
                'data_keys': sorted(parsed.get('data', {})) if type(parsed) is dict and type(parsed.get('data')) is dict else [],
                'error_code': parsed.get('error', {}).get('code') if type(parsed) is dict and type(parsed.get('error')) is dict else None}
            Path('/evidence/foundation-first-assertion.json').write_bytes(quality.encode(diagnostic))
            self.assertEqual(status, 200, diagnostic)
            with self.browser() as page:
                expect(page.locator('#resume-foundation')).to_be_visible()
                before = control.journal.path.read_bytes()
                page.locator('#resume-foundation').click(); page.keyboard.press('Escape')
                self.assertEqual(before, control.journal.path.read_bytes())
                page.locator('#resume-foundation').click(); page.locator('#operation-dialog button[value="confirm"]').click()
                expect(page.locator('#foundation-state')).to_have_attribute('data-state', re.compile('DONE|FAILED|MANUAL_ACTION_REQUIRED'), timeout=120000)
                completed = control.journal.read()
                self.assertEqual(completed['state'], 'DONE', [(r['name'], r['phase'], r['last_error_redacted']) for r in completed['steps']])
                page.locator('#check-foundation').click()
                expect(page.locator('#foundation-availability')).to_contain_text('Foundation MAIN vérifiée', timeout=120000)
                page.reload(); expect(page.locator('#foundation-state')).to_have_attribute('data-state', 'DONE')
                self.assertEqual(runtime.show()['MainPID'], pid)
                page.screenshot(path='/evidence/foundation-main.png', full_page=True)
            self.service = self.build_service(); control = self.service.foundation
            engine, runtime = control.engine(parent); identity = runtime.identity
            negatives = []
            for change, environment in [({'aud': 'wrong'}, 'main'), ({'htu_path': '/internal/mobile/v1/device/revoke'}, 'main'),
                 ({'exp': 1}, 'main'), ({'environment': 'dev-bastien'}, 'main'), ({}, 'dev')]:
                body, raw, token = self.altered(store, identity, change, environment)
                status = self.request(raw, token, body['request_id'])[0]
                self.assertIn(status, (401, 403)); negatives.append(status)
            body, raw, token = probe.assertion(store, identity)
            self.assertEqual(self.request(raw + b' ', token, body['request_id'])[0], 401)
            for path, method in [('/login.php', 'POST'), (probe.PATH + '?extra=1', 'POST'), (probe.PATH, 'GET'),
                                 ('/internal/mobile/v1/index.php', 'POST'), ('/internal/mobile/v1/business/unknown', 'POST')]:
                self.assertEqual(self.request(path=path, method=method)[0], 404)
            self.assertIn(self.request(path=probe.PATH, port=9080)[0], (403, 404))
            # Hold a real nonce SQL write. The in-flight FPM request must retain
            # the Web activity shared lock until the blocked statement finishes.
            body, raw, token = probe.assertion(store, identity)
            sql = subprocess.Popen(['mariadb', '--no-defaults', '--socket=' + self.socket, '-uroot', '--batch',
                '--skip-column-names', '--unbuffered', self.db], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            answers = []
            def pending(): answers.append(self.request(raw, token, body['request_id'])[0])
            thread = threading.Thread(target=pending, daemon=True)
            scope, _ = runtime.activation.configuration()
            try:
                sql.stdin.write("LOCK TABLES Sec_Mobile_Service_Nonce WRITE; SELECT 'LOCKED';\n"); sql.stdin.flush()
                self.assertEqual(sql.stdout.readline().strip(), 'LOCKED'); thread.start()
                with open(scope.directory / 'activity.lock', 'rb') as lock:
                    def blocked():
                        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError: return True
                        fcntl.flock(lock, fcntl.LOCK_UN); return False
                    until(blocked, timeout=2)
                with self.assertRaises(Exception): scope.acquire(confirmed=True, timeout=.2)
                sql.stdin.write('UNLOCK TABLES;\n'); sql.stdin.flush(); thread.join(timeout=5)
                self.assertEqual(answers, [200])
                before_nonce = self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.Sec_Mobile_Service_Nonce')[0]['n']
                self.assertEqual(self.request()[0], 503)
                self.assertEqual(self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.Sec_Mobile_Service_Nonce')[0]['n'], before_nonce)
                with scope.recover(scope.observe()['lease_id'], confirmed=True) as lease:
                    lease.assert_held(); self.assertEqual(self.request()[0], 503); lease.resume(confirmed=True)
            finally:
                sql.stdin.close(); sql.wait(timeout=5); sql.stdout.close(); thread.join(timeout=5)
            self.assertEqual(probe.check(store, identity), probe.RESULT)
            self.assertTrue(runtime.owned()); self.assertEqual(runtime.show()['MainPID'], pid)
            # Existing Web process/unit and business account remain intact.
            for role, old_pid in web_pids.items(): self.assertEqual(native.drain._show(http.unit(role))['MainPID'], old_pid)
            for path, original in preserved.items(): self.assertEqual(path.read_bytes(), original)
            self.fixture_login(http, already_active=True)
            # The synthetic TLS login proxy borrows the fixture Web identity;
            # dispose that test producer before testing the product's census.
            self.nginx.terminate(); self.nginx.wait(timeout=10); self.nginx = None
            self.assertEqual(self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.UserInfo')[0]['n'], 1)
            self.assertEqual(runtime.show()['UnitFileState'], 'static')
            # The real existing backup must stop Foundation BEFORE its strict
            # UID/GID census, then restore SQL/data into isolated verification.
            from copy import deepcopy
            from dataclasses import replace
            from installer import provisioned_backup, application_plan
            payload = deepcopy(self.saved['configuration'])
            payload.update(mode='upgrade', administrator=None, assistant={'action': 'preserve'},
                secrets={'database_password': self.payload['secrets']['database_password'], 'admin_password': '', 'openai_api_key': ''})
            payload['database']['mode'] = 'existing_local'
            backup_root = self.profile.root / 'foundation-backup'; backup_root.mkdir(mode=0o700)
            operation = provisioned_backup.ProvisionedBackup(replace(self.profile.runtime(), timeout_seconds=120),
                previous.wizard.TARGET, http, application_plan.cleaner.SessionCleaner(http))
            backup = operation.create_and_verify(payload, self.authority, config_root=self.profile.config_root,
                backup_root=backup_root, confirmed=True, allow_global_read_lock=True).report()
            self.assertEqual(backup['state'], 'PROVISIONED_BACKUP_RESTORE_VERIFIED', backup)
            self.assertTrue(backup['database_restoration_verified'] and backup['registered_data_restoration_verified'])
            self.assertFalse(backup['activity_resumed']); self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
            runtime.stopped(); self.assertEqual(runtime.show()['MainPID'], '0')
            # ConditionPathExists refuses even an explicit start while gated.
            command('systemctl', 'start', runtime.unit)
            self.assertEqual(runtime.show()['MainPID'], '0')
            Path('/evidence/foundation-backup.json').write_bytes(quality.encode(backup))
            current = control.journal.path.read_bytes()
            report = self.service.execute('foundation.check', confirm(document))['foundation']
            self.assertEqual(report['availability']['state'], 'FOUNDATION_MAIN_UNAVAILABLE')
            self.assertEqual(control.journal.path.read_bytes(), current)
            self.assertEqual(runtime.show()['MainPID'], '0')
            Path('/evidence/foundation-contract.json').write_text(json.dumps({'status': 'PASS',
                'foreign_9082_refused': True, 'stage_and_start_lost_reply_recovered': True,
                'real_browser_consent_and_refresh': True, 'signed_main_replay_unsigned': True,
                'negative_assertion_statuses': negatives, 'unknown_routes_methods_queries_closed': True,
                'sql_inflight_shared_guard': True, 'maintenance_503_before_sql': True,
                'web_9080_pid_units_and_admin_login_preserved': True, 'main_dev_keys_preserved': True,
                'native_backup_sql_data_restore_verified': True, 'foundation_stopped_before_original_census': True,
                'foundation_start_refused_under_maintenance': True,
                'stopped_done_never_restarted': True, 'gateway_binary_fixture_not_executed': True,
                'gateway_service_delivered': False, 'public_mobile_delivered': False, 'boot_delivered': False}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('HESTIA_FOUNDATION_TEST') != '1': raise RuntimeError('Disposable Foundation recipe opt-in required')
    previous.wizard.journal.fresh.WEB = args.web; previous.wizard.TARGET = args.target
    source = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([FoundationLive(
        'test_foundation_main_real_connection_recovery_guard_and_web_preservation')]))
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Foundation MAIN native connection', 'tests': result.testsRun, 'expected': 1,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'phase6_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'FOUNDATION-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
