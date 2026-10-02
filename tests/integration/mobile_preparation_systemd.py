"""Fresh native cockpit backup -> preparation -> activation, disposable CI only."""
import io
import json
import os
from pathlib import Path
import re
import signal
import time
import traceback
from unittest.mock import patch

from playwright.sync_api import expect
from github_fixture import confirm
from installer import mobile_preparation_runtime as native
from installer.model import InstallerError
from mobile_reopen_files_systemd import killed_at_boundary


def exercise(test):
    stage = 'setup'; started = time.monotonic()
    def failure(error):
        chain = []; current = error
        while current is not None and len(chain) < 6:
            code = str(current)
            chain.append({'type': type(current).__name__, 'code': code if re.fullmatch('[A-Z][A-Z0-9_]{1,80}', code) else 'REDACTED',
                'frames': [{'file': Path(x.filename).name, 'line': x.lineno, 'function': x.name} for x in traceback.extract_tb(current.__traceback__)]})
            current = current.__context__
        Path('/evidence/mobile-preparation-error.json').write_text(json.dumps({'stage': stage, 'chain': chain}, indent=2) + '\n')
    def credentials(page, prefix, values):
        for name, value in values.items(): page.locator('#' + prefix + '-' + name).fill(value)
        page.locator('#' + prefix + '-sql-consent').check()
    try:
        test.managed(); parent, active = test.prepared_activation()
        active = test.service.execute('activation.apply', confirm(active))['activation']['installation']; test.assertEqual(active['state'], 'DONE')
        gateway = test.service.execute('gateway.plan', {'web_plan_sha256': parent['plan_sha256'],
            'public_origin': 'https://mobile.customer.example', 'dev_enabled': True, 'acquisition': 'package'})['gateway']['preparation']
        raw = Path('/opt/gateway-package.zip').read_bytes()
        gateway = test.service.import_gateway_package(gateway['plan_sha256'], io.BytesIO(raw), len(raw))['gateway']['preparation']
        parents = {'web': parent['plan_sha256'], 'activation': active['plan_sha256'], 'gateway': gateway['plan_sha256']}
        foundation = test.service.execute('foundation.plan', {'parents': parents})['foundation']['installation']
        foundation = test.service.execute('foundation.apply', confirm(foundation))['foundation']['installation']; test.assertEqual(foundation['state'], 'DONE')
        parents['foundation'] = foundation['plan_sha256']
        main = test.service.execute('gateway-service.plan', {'parents': parents})['gateway_service']['installation']
        main = test.service.execute('gateway-service.apply', confirm(main))['gateway_service']['installation']; test.assertEqual(main['state'], 'DONE')
        parents['gateway_service'] = main['plan_sha256']
        http = test.http_runtime(); _, runtime = test.service.gateway_service.engine(parent)
        test.fixture_login(http, already_active=True)
        test.nginx.terminate(); test.nginx.wait(timeout=10); test.nginx = None
        values = {'database_password': test.payload['secrets']['database_password'],
                  'authority_user': test.authority._user, 'authority_password': test.authority._password}
        protected = [test.service.engine.journal.path, test.service.activation.journal.path, test.service.gateway.journal.path,
            test.service.foundation.journal.path, test.service.gateway_service.journal.path,
            *test.service.gateway.identities.root.glob('*.pem')]
        preserved = {p: p.read_bytes() for p in protected}
        stage = 'backup-and-preparation-consent'
        with test.browser() as page:
            page.locator('#plan-mobile-backup').click(); credentials(page, 'backup', values)
            page.locator('#apply-mobile-backup').click(); page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#mobile-backup-state')).to_have_attribute('data-state', 'DONE', timeout=600000)
            page.locator('#plan-mobile-preparation').click()
            expect(page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'AWAITING_CONFIRMATION')
            credentials(page, 'preparation', values); page.locator('#apply-mobile-preparation').click(); page.keyboard.press('Escape')
            test.assertIsNone(test.service.mobile_preparation._read('approved.json'))
            for name in values: expect(page.locator('#preparation-' + name)).to_have_value('')
        test.service = test.build_service(); control = test.service.mobile_preparation
        profile = control.profile(); lease_id = profile['lease_id']
        test.assertEqual(profile['parents'], parents)
        account, _, _, _ = http._inspect_configuration(); scope = http._scope(account)
        gate = (scope.directory / 'maintenance.attempt').read_bytes()
        request = {'confirmation': control.state()['confirmation'], 'confirm': True, 'credentials': values, 'allow_global_read_lock': True}
        original = native.NativePreparation.execute
        stage = 'sigkill-after-file-release-before-cockpit-receipt'
        def interrupted():
            def cut(instance, name):
                result = original(instance, name)
                if name == 'files': os.kill(os.getpid(), signal.SIGKILL)
                return result
            try:
                with patch.object(native.NativePreparation, 'execute', cut): test.service.execute('mobile-preparation.apply', request)
            except BaseException as error: failure(error); raise
        killed_at_boundary(test, interrupted)
        test.assertIsNotNone(control._read('gateway.done.json')); test.assertIsNotNone(control._read('files.intent.json'))
        test.assertIsNone(control._read('files.done.json')); test.assertIsNone(control._read('external.intent.json'))
        test.assertEqual(scope.observe()['lease_id'], lease_id)
        test.assertEqual((scope.directory / 'maintenance.attempt').read_bytes(), gate)
        runtime.stopped(); runtime.foundation.stopped()
        gateway_done = (control.root / 'gateway.done.json').read_bytes()
        stage = 'sql-drift-before-external-release'
        test.sql([f'CREATE TABLE `{test.db}`.Hestia_Cockpit_Preparation_Drift (id INT PRIMARY KEY)'])
        try:
            with patch.object(native.external.a.c, '_recheck', wraps=native.external.a.c._recheck) as recheck:
                with test.assertRaisesRegex(InstallerError, '^MANUAL_ACTION_REQUIRED$'): test.service.execute('mobile-preparation.resume', request)
                recheck.assert_called_once()
        finally: test.sql([f'DROP TABLE `{test.db}`.Hestia_Cockpit_Preparation_Drift'])
        test.assertIsNotNone(control._read('files.done.json')); test.assertIsNone(control._read('external.done.json'))
        test.assertEqual((control.root / 'gateway.done.json').read_bytes(), gateway_done)
        test.assertFalse((control.backup.backups(profile) / ('external-release-' + lease_id) / 'intent.json').exists())
        stage = 'https-preparation-resume-and-separate-activation'
        with test.browser() as page:
            expect(page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'RESUME_REQUIRED')
            credentials(page, 'preparation', values); page.locator('#resume-mobile-preparation').click()
            page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'DONE', timeout=900000)
            test.assertTrue(control.state()['activation_ready']); test.assertIsNone(test.service.mobile_activation.profile())
            test.assertEqual((scope.directory / 'maintenance.attempt').read_bytes(), gate)
            runtime.stopped(); runtime.foundation.stopped()
            page.reload(); expect(page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'DONE')
            page.locator('#mobile-preparation').screenshot(path='/evidence/mobile-preparation-cockpit.png')
            page.locator('#plan-mobile-activation').click(); credentials(page, 'mobile', values)
            page.locator('#apply-mobile-activation').click(); page.keyboard.press('Escape')
            test.assertIsNone(test.service.mobile_activation._read('approved.json')); test.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
            credentials(page, 'mobile', values); page.locator('#apply-mobile-activation').click()
            page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#mobile-activation-state')).to_have_attribute('data-state', 'DONE', timeout=300000)
            page.locator('#check-mobile-activation').click()
            expect(page.locator('#mobile-activation-availability')).to_contain_text('Services locaux et page de connexion vérifiés', timeout=120000)
        test.service = test.build_service(); control = test.service.mobile_preparation
        test.assertEqual(scope.observe()['state'], 'SERVING'); test.assertEqual(test.service.mobile_activation.state()['state'], 'DONE')
        with patch.object(native.NativePreparation, 'execute', side_effect=AssertionError('native replay')):
            test.assertEqual(test.service.execute('mobile-preparation.resume', {**request, 'credentials': {}})['mobile_preparation']['state'], 'DONE')
            test.service.wizard_state(); report = test.service.report()
        for secret in values.values():
            test.assertNotIn(secret, str(report))
            for path in control.root.iterdir(): test.assertNotIn(secret.encode(), path.read_bytes())
        for path, raw in preserved.items(): test.assertEqual(path.read_bytes(), raw)
        proof = {'status': 'PASS', 'fresh_instance': True, 'actual_https_backup': True, 'preparation_cancel_without_effect': True,
            'sigkill_after_real_file_release': True, 'same_owned_lease_recovered': True, 'completed_stages_preserved': True,
            'current_sql_drift_refused_before_external_effect': True, 'actual_sql_recheck_reached': True,
            'https_preparation_resume': True, 'all_six_stages_complete': True, 'maintenance_kept_until_separate_activation': True,
            'separate_activation_cancel_without_effect': True, 'actual_https_activation_and_local_login': True,
            'completed_preparation_not_replayed': True, 'parent_journals_and_keys_preserved': True, 'secrets_not_persisted': True,
            'bootstrap_lifecycle_reopened': True, 'public_tls_verified': False, 'boot_persistence': False, 'phase6_complete': False,
            'seconds': round(time.monotonic() - started, 3)}
        Path('/evidence/mobile-preparation-native.json').write_text(json.dumps(proof, indent=2) + '\n')
    except BaseException as error: failure(error); raise
