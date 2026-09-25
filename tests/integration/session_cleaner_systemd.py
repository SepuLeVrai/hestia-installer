#!/usr/bin/env python3
"""Dedicated UID collector, real PHP/flock/systemd/timers and native coexistence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import session_cleaner as c
from installer import system_drain as s
from installer.operations import RecoveryDecision
from http_runtime_systemd import HttpRuntimeLive, command, until
sys.path.insert(0, str(ROOT / 'scripts'))
import quality


class SessionCleanerLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SESSION_CLEANER_TEST') != '1' or os.geteuid() != 0 \
                or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell',
                '/usr/sbin/nologin', 'hestia-cleaner-test')
        cls.account = pwd.getpwnam('hestia-cleaner-test')
        cls.family = '8.4' if Path('/usr/sbin/php-fpm8.4').is_file() else '8.2'

    def setUp(self):
        self.fx = HttpRuntimeLive('test_exclusive_stage_is_gated_with_exact_permissions_and_no_global_mutation')
        self.fx.account, self.fx.family = self.account, self.family
        self.fx.setUp(); self.addCleanup(self.fx.doCleanups)
        self.cleaner = c.SessionCleaner(self.fx.runtime)
        self.fx.units.insert(0, self.cleaner.timer); self.fx.units.append(self.cleaner.unit)
        self.python = '/usr/bin/python' + {'8.2': '3.11', '8.4': '3.13'}[self.family]
        self.fx.write('hold.php', '<?php session_start(); file_put_contents(' + repr(str(self.fx.root / 'data/tmp/entered'))
                      + ',"entered"); $end=microtime(true)+5; while(!file_exists('
                      + repr(str(self.fx.root / 'data/tmp/release-php'))
                      + ') && microtime(true)<$end) usleep(10000); echo "done";')
        self.fx.create()

    def stage(self): return self.cleaner.create(confirmed=True)

    def file(self, name='sess_expired', age=50000):
        path = self.fx.root / 'data/sessions' / name
        path.write_bytes(b'private fixture session'); os.chown(path, self.account.pw_uid, self.account.pw_gid)
        path.chmod(0o600); stamp = time.time_ns() - age * 1000000000; os.utime(path, ns=(stamp, stamp))
        return path

    def resume_fixture(self):
        state = self.fx.scope.observe()
        with self.fx.scope.recover(state['lease_id'], confirmed=True) as lease: lease.resume(confirmed=True)

    def execute(self, *, check=True): return command('systemctl', 'start', self.cleaner.unit, check=check)

    def run_worker(self):
        result = command('runuser', '-u', self.account.pw_name, '--', self.python, '-I', '-B',
                         str(self.cleaner.directory / 'worker.py'))
        return json.loads(result.stdout)

    def test_stage_keeps_timer_inactive_native_untouched_and_service_condition_closed(self):
        native = Path('/usr/lib/php/sessionclean').read_bytes()
        timer_before = command('systemctl', 'is-enabled', 'phpsessionclean.timer', check=False).stdout
        report = self.stage(); self.assertEqual(report['state'], 'SESSION_CLEANER_STAGED')
        self.assertEqual(report['lifetime_seconds'], 43200); self.assertFalse(report['schedule_active'])
        old = self.file(); self.execute(); self.assertTrue(old.exists())
        self.assertEqual(s._show(self.cleaner.unit)['ActiveState'], 'inactive')
        self.assertEqual(command('systemctl', 'is-active', self.cleaner.timer, check=False).stdout.strip(), b'inactive')
        self.assertEqual(Path('/usr/lib/php/sessionclean').read_bytes(), native)
        self.assertEqual(command('systemctl', 'is-enabled', 'phpsessionclean.timer', check=False).stdout, timer_before)
        self.assertEqual(self.cleaner.observe(), report)
        for key in ('native_cleaner_modified', 'system_wiring_verified', 'application_installed', 'complete_web_backup'):
            self.assertFalse(report[key])

    def test_real_collector_expires_old_session_preserves_1h_4h_8h_and_native_coexists(self):
        self.stage(); self.resume_fixture(); old = self.file()
        kept = [self.file('sess_' + str(age), age) for age in (3600, 14400, 28800)]
        before = {p: (p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.read_bytes()) for p in kept}
        self.execute(); self.assertFalse(old.exists())
        command('/usr/lib/php/sessionclean')
        self.assertEqual(before, {p: (p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.read_bytes()) for p in kept})
        self.assertEqual({str(p): p.read_bytes() for p in self.fx.global_paths}, self.fx.global_before)

    def test_actual_php_request_blocks_collection_without_extending_session_dates(self):
        self.stage(); self.fx.activate_fixture(); old = self.file(); stamp = old.stat().st_mtime_ns
        replies = []; request = threading.Thread(target=lambda: replies.append(self.fx.request('/hold.php')))
        request.start(); self.addCleanup(lambda: request.join(5))
        until(lambda: (self.fx.root / 'data/tmp/entered').exists())
        try:
            self.execute(); self.assertTrue(old.exists()); self.assertEqual(old.stat().st_mtime_ns, stamp)
        finally: (self.fx.root / 'data/tmp/release-php').touch()
        request.join(5); self.assertFalse(request.is_alive()); self.assertEqual(replies[0][0], 200)
        self.execute(); self.assertFalse(old.exists())

    def test_worker_and_systemd_both_refuse_under_durable_maintenance(self):
        self.stage(); self.resume_fixture(); old = self.file()
        with self.fx.scope.acquire(confirmed=True):
            self.execute(); self.assertTrue(old.exists())
            self.assertEqual(self.run_worker()['state'], 'SESSION_CLEANER_MAINTENANCE')
        self.assertEqual(self.run_worker()['removed'], 0); self.assertTrue(old.exists())

    def test_real_session_file_lock_survives_collection_then_expires_after_release(self):
        import fcntl
        self.stage(); self.resume_fixture(); old = self.file(); handle = os.open(old, os.O_RDONLY)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.execute(); self.assertTrue(old.exists())
        finally: os.close(handle)
        self.execute(); self.assertFalse(old.exists())

    def test_hostile_session_link_fails_before_deleting_valid_expired_sessions(self):
        self.stage(); self.resume_fixture(); old = self.file()
        (old.parent / 'sess_link').symlink_to('/etc/passwd')
        self.assertNotEqual(self.execute(check=False).returncode, 0)
        self.assertTrue(old.exists())
        log = command('journalctl', '-u', self.cleaner.unit, '--no-pager').stdout
        self.assertIn(b'SESSION_CLEANER_REJECTED', log); self.assertNotIn(b'private fixture session', log)

    def test_existing_timer_refuses_without_reservation_or_adoption(self):
        path = s.UNIT_ROOT / self.cleaner.timer
        content = '[Timer]\nOnBootSec=1h\n'; path.write_text(content); path.chmod(0o644)
        with self.assertRaises(c.SessionCleanerError): self.stage()
        self.assertFalse(self.cleaner.directory.exists()); self.assertEqual(path.read_text(), content)

    def test_partial_worker_write_retains_gate_attempt_and_manual_recovery(self):
        original = c.f._write
        def fail(fd, name, *args, **kwargs):
            if name == 'worker.py': raise OSError('synthetic-disk-full')
            return original(fd, name, *args, **kwargs)
        with patch.object(c.f, '_write', side_effect=fail), self.assertRaises(c.SessionCleanerError): self.stage()
        self.assertTrue((self.cleaner.directory / 'cleaner.attempt').exists())
        self.assertFalse((self.cleaner.directory / 'staged.json').exists())
        self.assertEqual(self.fx.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertEqual(c.SessionCleanerOperation(self.cleaner).recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        with self.assertRaises(c.SessionCleanerError): self.stage()

    def test_lost_response_is_recovered_read_only_and_worker_timer_drift_is_refused(self):
        self.stage(); operation = c.SessionCleanerOperation(self.cleaner)
        with patch.object(c.h, '_command', side_effect=AssertionError('observation must not mutate')):
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
        for path in (self.cleaner.directory / 'worker.py', s.UNIT_ROOT / self.cleaner.timer):
            original = path.read_bytes(); path.write_bytes(original + b'\n# drift\n')
            with self.assertRaises(c.SessionCleanerError): self.cleaner.observe()
            path.write_bytes(original)
        command('systemctl', 'daemon-reload'); self.assertEqual(self.cleaner.observe()['state'], 'SESSION_CLEANER_STAGED')

    def test_three_real_units_and_one_cli_fixture_drain_with_timer_still_armed(self):
        self.stage(); bindings = list(self.fx.runtime.http_bindings()); cleaner_binding = self.cleaner.binding()
        unit = 'hestia-' + self.fx.instance + '-cli.service'; self.fx.units.append(unit)
        fragment = b'[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nRestart=no\nKillMode=control-group\nSendSIGKILL=yes\nDelegate=no\n'
        path = s.UNIT_ROOT / unit; path.write_bytes(fragment); path.chmod(0o644)
        directory = s.UNIT_ROOT / (unit + '.d'); directory.mkdir(mode=0o755)
        path = directory / '50-hestia-maintenance.conf'; path.write_bytes(s.condition_dropin(self.fx.scope)); path.chmod(0o644)
        bindings += [s.UnitBinding('cli', hashlib.sha256(fragment).hexdigest()), cleaner_binding]
        command('systemctl', 'daemon-reload'); self.fx.activate_fixture()
        command('systemctl', 'start', unit); command('systemctl', 'start', self.cleaner.timer)
        self.assertEqual(command('systemctl', 'is-active', self.cleaner.timer).stdout.strip(), b'active')
        until(lambda: s._show(self.cleaner.unit)['ActiveState'] == 'inactive')
        old = self.file(); before = old.read_bytes(), old.stat().st_mtime_ns
        with s.SystemDrain(self.fx.scope, tuple(bindings)).acquire(confirmed=True) as lease:
            self.execute(); self.assertTrue(lease.report()['cgroup_empty_verified'])
            self.assertEqual((old.read_bytes(), old.stat().st_mtime_ns), before)
            self.assertFalse(lease.report()['system_wiring_verified'])

    def test_actual_timer_dispatch_with_accelerated_fixture_preserves_maintenance(self):
        self.stage(); old = self.file()
        path = s.UNIT_ROOT / self.cleaner.timer
        # Only the synthetic timing fixture changes: production service/worker/gate
        # are exact. The actual generated 5min/30min schedule is audited separately.
        text = path.read_text().replace('OnBootSec=5min', 'OnActiveSec=100ms').replace('AccuracySec=1min', 'AccuracySec=1ms')
        path.write_text(text); command('systemctl', 'daemon-reload')
        with self.assertRaises(c.SessionCleanerError): self.cleaner.observe()
        command('systemctl', 'start', self.cleaner.timer)
        until(lambda: command('systemctl', 'show', '--property=LastTriggerUSec', '--value', self.cleaner.timer).stdout.strip() != b'')
        self.assertTrue(old.exists()); command('systemctl', 'stop', self.cleaner.timer)
        self.resume_fixture(); command('systemctl', 'start', self.cleaner.timer)
        until(lambda: not old.exists())

    def test_maintenance_waits_for_collector_lock_and_published_gate_stops_collection(self):
        self.stage(); self.resume_fixture(); old = self.file()
        entered = self.fx.root / 'data/tmp/collector-entered'; release = self.fx.root / 'data/tmp/release'
        program = ('import runpy,json,time,pathlib\nns=runpy.run_path(' + repr(str(self.cleaner.directory / 'worker.py')) + ')\n'
            + 'clean=ns["clean"]; original=clean.__globals__["collect"]\ndef paused(*args):\n'
            + ' pathlib.Path(' + repr(str(entered)) + ').write_text("entered")\n'
            + ' while not pathlib.Path(' + repr(str(release)) + ').exists(): time.sleep(.01)\n'
            + ' return original(*args)\nclean.__globals__["collect"]=paused\n'
            + 'print(json.dumps(clean(json.loads(bytes.fromhex(ns["PROFILE_HEX"])))),flush=True)\n')
        process = subprocess.Popen(['runuser', '-u', self.account.pw_name, '--', self.python, '-I', '-B', '-c', program],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        until(entered.exists); leases = []; errors = []
        def acquire():
            try: leases.append(self.fx.scope.acquire(confirmed=True, timeout=5))
            except Exception as error: errors.append(error)
        waiter = threading.Thread(target=acquire); waiter.start()
        try:
            until(lambda: (self.fx.scope.directory / 'maintenance.attempt').exists())
            self.assertFalse(leases); release.write_text('release')
            output, stderr = process.communicate(timeout=5); waiter.join(5)
            self.assertEqual(process.returncode, 0, stderr.decode()); self.assertFalse(errors)
            self.assertEqual(json.loads(output)['state'], 'SESSION_CLEANER_MAINTENANCE')
            self.assertEqual(len(leases), 1); self.assertTrue(old.exists())
        finally:
            release.touch(); waiter.join(6)
            for lease in leases: lease.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SessionCleanerLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite': 'Dedicated session collection and unchanged native cleaner', 'tests': result.testsRun,
        'expected': 12, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 12 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(before), 'web_application_qualified': False,
        'functional_session_policy_qualified': False, 'native_cleaner_unchanged': True,
        'service_activation_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'CLEANER-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
