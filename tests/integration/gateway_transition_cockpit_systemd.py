#!/usr/bin/env python3
"""Real cockpit import, native transition, SIGKILL recovery and local check."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import time
from unittest.mock import patch
from playwright.sync_api import expect

import gateway_active_profile_systemd as prior
from github_fixture import confirm
from installer import mobile_activation_admission as activation
from installer import gateway_resume_authority as authority
from installer import gateway_service_drain, foundation_drain
from installer.gateway_transition import LEGACY_COMMIT, FCM_COMMIT


class CockpitTransitionLive(prior.ActiveProfileLive):
    def kill_child(self, action):
        # This coordinator combines six bounded native preparations. Keep the
        # same SIGKILL assertions with a budget for the complete composition.
        pid = os.fork()
        if pid == 0:
            try: action()
            except BaseException: os._exit(98)
            os._exit(97)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found: break
            time.sleep(.1)
        else:
            os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            self.fail('Cockpit native composition timeout')
        self.assertTrue(os.WIFSIGNALED(status), status)
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)

    def exercise_cockpit(self, source_pin, target_pin, direction):
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
        control = self.service.gateway_transition_execution
        with self.browser() as page:
            page.locator('#plan-gateway-transition').click()
            expect(page.locator('#gateway-transition-state')).to_contain_text('Configuration compatible')
            page.locator('#plan-transition-execution').click()
            expect(page.locator('#transition-package')).to_be_visible()
            page.locator('#transition-package').set_input_files(str(target))
            page.locator('#import-transition-package').click(); page.keyboard.press('Escape')
            self.assertFalse((control.root / 'binary').exists())
            page.locator('#transition-package').set_input_files(str(target))
            page.locator('#import-transition-package').click()
            page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#apply-transition-execution')).to_be_visible(timeout=120000)
            for name, value in values.items(): page.locator('#transition-' + name).fill(value)
            page.locator('#transition-sql-consent').check()
            page.locator('#apply-transition-execution').click(); page.keyboard.press('Escape')
            self.assertIsNone(control._read('approved.json'))
            for name in values: expect(page.locator('#transition-' + name)).to_have_value('')
        profile = control.profile(); backups = control.backup.backups(profile); lease_id = profile['lease_id']
        scope = http._scope(http._inspect_configuration()[0])
        request = {'confirmation': control.state()['confirmation'], 'confirm': True,
                   'credentials': values, 'allow_global_read_lock': True}
        boundaries = []; original_write = type(control)._write
        # SIGKILL after the real motor completed, before its cockpit receipt.
        for stage in ('cutover', 'publication', 'admission'):
            def interrupted():
                def write(instance, name, value):
                    if instance.root == control.root and name == stage + '.done.json':
                        os.kill(os.getpid(), signal.SIGKILL)
                    return original_write(instance, name, value)
                with patch.object(type(control), '_write', write):
                    self.service.execute('gateway-transition-execution.' + ('apply' if stage == 'cutover' else 'resume'), request)
            self.kill_child(interrupted); boundaries.append(stage)
            self.assertEqual(control.state()['state'], 'RESUME_REQUIRED')
            self.assertEqual(next(row for row in control.state()['steps'] if row['stage'] == stage)['state'], 'INTENT_RECORDED')
            self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
            for path, raw in preserved.items(): self.assertEqual(path.read_bytes(), raw)
        starts = Path('/evidence/cockpit-starts.json'); windows = Path('/evidence/cockpit-windows.json')
        real_acquire = activation.a.c.rf.acquire; real_start = activation.v.NativeRuntime.start
        released = False; kill_start = True
        def append(path, value):
            rows = json.loads(path.read_text()) if path.exists() else []; rows.append(value)
            path.write_text(json.dumps(rows, indent=2) + '\n')
        @contextmanager
        def fenced(*args, **kwargs):
            nonlocal released
            released = False
            with real_acquire(*args, **kwargs) as held:
                began = held._deadline - 180
                yield held; held.assert_held()
                append(windows, {'seconds': time.monotonic() - began})
            released = True
        def start(native, role):
            self.assertTrue(released)
            self.assertTrue(all(not (scope.directory / name).exists() for name in authority.MARKERS))
            self.assertFalse((scope.directory / 'maintenance.attempt').exists())
            with self.assertRaises(activation.r.hd.m.MaintenanceError):
                with scope.writer(): self.fail('Activity writer entered activation')
            real_start(native, role); append(starts, {'role': role, 'unit': native.unit(role)})
            if kill_start and role == 'php': os.kill(os.getpid(), signal.SIGKILL)
        with patch.object(activation.a.c.rf, 'acquire', fenced), patch.object(activation.v.NativeRuntime, 'start', start):
            self.kill_child(lambda: self.service.execute('gateway-transition-execution.resume', request))
        self.assertEqual(scope.observe()['state'], 'SERVING'); self.assertEqual(json.loads(starts.read_text())[0]['role'], 'php')
        kill_start = False; released = True
        with patch.object(activation.a.c, '_recheck', side_effect=AssertionError('SQL replay after admission')), \
                patch.object(activation.v.NativeRuntime, 'start', start), self.browser() as page:
            for name, value in values.items(): page.locator('#transition-' + name).fill(value)
            page.locator('#transition-sql-consent').check()
            page.locator('#resume-transition-execution').click()
            page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#transition-execution-state')).to_have_attribute('data-state', 'DONE', timeout=120000)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in control.root.rglob('*') if p.is_file()}
            with patch.object(activation.v.NativeRuntime, 'start', side_effect=AssertionError('check starts')):
                page.locator('#check-transition-execution').click()
                expect(page.locator('#gateway-transition-execution')).to_contain_text('Web local disponible', timeout=120000)
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in control.root.rglob('*') if p.is_file()})
            page.reload(); expect(page.locator('#transition-execution-state')).to_have_attribute('data-state', 'DONE')
            page.locator('#gateway-transition-execution').screenshot(path='/evidence/cockpit-transition.png')
        self.assertEqual([row['role'] for row in json.loads(starts.read_text())], list(activation.v.ROLES))
        selected = gateway_service_drain.attached(http, foundation_drain.attached(http))
        selected.owned(); selected.foundation.owned()
        self.assertEqual(selected.profile.selected_release['commit'], target_pin)
        self.assertEqual(self.sqlite_identity(selected), uuid)
        for path, raw in preserved.items(): self.assertEqual(path.read_bytes(), raw)
        with scope.writer(): pass
        result = control.state(); self.assertTrue(result['availability']['local_web']['login_page'])
        raw = b''.join(p.read_bytes() for p in control.root.rglob('*') if p.is_file() and p.suffix == '.json')
        for secret in values.values(): self.assertNotIn(secret.encode(), raw)
        Path('/evidence/cockpit-transition-' + direction + '.json').write_bytes(prior.quality.encode({
            'status': 'PASS', 'direction': direction, 'source': source_pin, 'target': target_pin,
            'sigkill_boundaries': boundaries + ['php-start-before-receipt'], 'cockpit_import_and_resume': True,
            'cancel_preserves_journal_and_clears_credentials': True, 'sql_released_before_real_starts': True,
            'activity_lock_through_five_starts': True, 'completed_check_read_only': True,
            'source_journals_config_keys_preserved': True, 'sqlite_schema': 6,
            'installation_uuid_sha256': hashlib.sha256(uuid.encode()).hexdigest(),
            'local_login_page_available': True, 'phase6_complete': False}))

    def test_upgrade_cockpit_native_sigkill(self):
        self.exercise_cockpit(LEGACY_COMMIT, FCM_COMMIT, 'upgrade')

    def test_downgrade_cockpit_native_sigkill(self):
        self.exercise_cockpit(FCM_COMMIT, LEGACY_COMMIT, 'rollback')
