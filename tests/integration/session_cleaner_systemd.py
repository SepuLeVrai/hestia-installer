#!/usr/bin/env python3
"""Dedicated UID collector, real PHP/flock/systemd/timers and native coexistence."""
import argparse
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import session_cleaner as c
from installer import http_drain as hd
from installer import http_runtime as h
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

    def coordinated(self): return hd.HttpDrain(self.fx.runtime, cleaner=self.cleaner)

    def armed(self):
        command('systemctl', 'start', self.cleaner.timer)
        until(lambda: s._show(self.cleaner.unit)['ActiveState'] == 'inactive')
        self.assertEqual(self.cleaner._timer_state(stopped=False)['ActiveState'], 'active')

    def assert_coordinated(self, lease):
        report = lease.report()
        self.assertEqual(report['state'], 'PROVISIONED_HTTP_AND_CLEANER_DRAINED')
        self.assertEqual((report['services'], report['timers_stopped']), (3, 1))
        self.assertFalse(report['other_producers_controlled']); self.assertFalse(report['storage_inventory_complete'])
        self.assertEqual(self.cleaner._timer_state()['ActiveState'], 'inactive')
        for unit in (self.fx.runtime.unit('apache'), self.fx.runtime.unit('php'), self.cleaner.unit):
            self.assertTrue(s._empty_cgroup(unit)); self.assertEqual(s._show(unit)['Result'], 'success')
            command('systemctl', 'start', unit)
            self.assertEqual(s._show(unit)['ActiveState'], 'inactive')
        with self.assertRaises(c.SessionCleanerError): self.cleaner.observe()
        with self.assertRaises(h.HttpRuntimeError): self.fx.runtime.observe()

    def test_coordinated_drain_stops_original_timer_and_three_services_preserving_sessions(self):
        self.stage(); self.fx.activate_fixture(); self.armed()
        kept = [self.file('sess_' + str(age), age) for age in (3600, 14400, 28800, 50000)]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in kept}
        with self.coordinated().acquire(confirmed=True) as lease:
            self.assert_coordinated(lease); self.execute()
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in kept})
            self.assertEqual({str(p): p.read_bytes() for p in self.fx.global_paths}, self.fx.global_before)
        self.assertEqual(self.fx.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_coordinated_drain_waits_for_real_collector_holding_lock_and_publishes_gate(self):
        self.stage(); self.resume_fixture()
        # Use the exact installed worker/unit. Pause its real process only after
        # it owns admission; no replacement worker, profile or timer schedule.
        for index in range(9000): self.file('sess_bulk' + str(index), 100)
        old = self.file(); worker = subprocess.Popen(['systemctl', 'start', self.cleaner.unit],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lock = os.open(self.fx.scope.directory / 'activity.lock', os.O_RDONLY)
        self.addCleanup(os.close, lock)
        pid = None; waiter = None; leases = []; errors = []
        def locked_worker():
            nonlocal pid
            current = s._show(self.cleaner.unit)['MainPID']
            if current == '0': return False
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN); return False
            except BlockingIOError:
                pid = int(current); return True
        try:
            until(locked_worker); os.kill(pid, signal.SIGSTOP)
            with self.assertRaises(BlockingIOError): fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(s._show(self.cleaner.unit)['ActiveState'], 'activating')
            def acquire():
                try: leases.append(self.coordinated().acquire(confirmed=True, timeout=5))
                except Exception as error: errors.append(error)
            waiter = threading.Thread(target=acquire); waiter.start()
            until(lambda: (self.fx.scope.directory / 'maintenance.attempt').exists())
            self.assertFalse(leases)
            os.kill(pid, signal.SIGCONT)
            stdout, stderr = worker.communicate(timeout=6); waiter.join(6)
            self.assertEqual(worker.returncode, 0, stderr.decode())
            self.assertFalse(waiter.is_alive()); self.assertFalse(errors, repr(errors))
            self.assertEqual(len(leases), 1); self.assertTrue(old.exists())
            self.assert_coordinated(leases[0])
        finally:
            if pid is not None:
                try: os.kill(pid, signal.SIGCONT)
                except ProcessLookupError: pass
            if waiter is not None: waiter.join(7)
            for lease in leases: lease.close()
            if worker.poll() is None: worker.terminate(); worker.wait(timeout=5)

    def test_coordinated_live_receipt_refuses_rearmed_timer_without_resuming_services(self):
        self.stage(); self.fx.activate_fixture(); self.armed()
        with self.coordinated().acquire(confirmed=True) as lease:
            self.assert_coordinated(lease)
            command('systemctl', 'start', self.cleaner.timer)
            with self.assertRaises(c.SessionCleanerError): lease.report()
            self.assertEqual(self.fx.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
            self.assertEqual(s._show(self.fx.runtime.unit('php'))['ActiveState'], 'inactive')

    def test_coordinated_collector_drift_is_refused_before_gate_and_timer_stop(self):
        self.stage(); self.fx.activate_fixture(); self.armed()
        path = self.cleaner.directory / 'worker.py'; original = path.read_bytes()
        path.write_bytes(original + b'\n# drift\n')
        with self.assertRaises(c.SessionCleanerError): self.coordinated().acquire(confirmed=True)
        self.assertEqual(self.fx.scope.observe()['state'], 'SERVING'); self.assertTrue(self.fx.ready())
        self.assertEqual(self.cleaner._timer_state(stopped=False)['ActiveState'], 'active')
        self.assertFalse(list(self.fx.scope.directory.glob('http-drain-*.attempt')))
        path.write_bytes(original)

    def test_coordinated_controller_sigkill_after_timer_stop_keeps_gate_and_recovers(self):
        self.stage(); self.fx.activate_fixture(); self.armed()
        context = multiprocessing.get_context('fork'); ready = context.Event(); original = self.cleaner._stop_timer
        def child():
            def pause(): original(); ready.set(); time.sleep(20)
            with patch.object(self.cleaner, '_stop_timer', side_effect=pause): self.coordinated().acquire(confirmed=True)
        process = context.Process(target=child); process.start()
        try:
            self.assertTrue(ready.wait(12)); process.kill(); process.join(5)
            self.assertEqual(process.exitcode, -signal.SIGKILL)
        finally:
            if process.is_alive(): process.kill(); process.join(5)
        state = self.fx.scope.observe(); self.assertEqual(state['state'], 'MAINTENANCE_REQUIRED')
        self.assertEqual(self.cleaner._timer_state()['ActiveState'], 'inactive')
        self.assertEqual(s._show(self.fx.runtime.unit('apache'))['ActiveState'], 'active')
        self.assertTrue(list(self.fx.scope.directory.glob('http-drain-*.attempt')))
        with self.coordinated().recover(state['lease_id'], confirmed=True) as lease: self.assert_coordinated(lease)

    def test_coordinated_allowlist_does_not_adopt_other_processes_of_same_identity(self):
        self.stage(); self.fx.activate_fixture(); self.armed()
        unit = 'hestia-foreign-' + self.fx.instance + '.service'; self.fx.units.append(unit)
        path = s.UNIT_ROOT / unit
        fragment = f'[Service]\nUser={self.account.pw_name}\nExecStart=/usr/bin/sleep infinity\n'
        path.write_text(fragment); path.chmod(0o644)
        command('systemctl', 'daemon-reload'); command('systemctl', 'start', unit)
        pid = command('systemctl', 'show', '--value', '--property=MainPID', unit).stdout.strip()
        until(lambda: hd._credentials(Path('/proc/' + pid.decode() + '/status').read_text())['Uid'][1] == self.account.pw_uid)
        with self.assertRaisesRegex(hd.HttpDrainError, 'FOREIGN_IDENTITY_PROCESS'): self.coordinated().acquire(confirmed=True)
        self.assertEqual(command('systemctl', 'is-active', unit).stdout.strip(), b'active')
        self.assertEqual(path.read_text(), fragment); self.assertEqual(self.fx.scope.observe()['state'], 'SERVING')
        self.assertEqual(self.cleaner._timer_state(stopped=False)['ActiveState'], 'active')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SessionCleanerLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite': 'Dedicated session collection and unchanged native cleaner', 'tests': result.testsRun,
        'expected': 18, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 18 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(before), 'web_application_qualified': False,
        'functional_session_policy_qualified': False, 'native_cleaner_unchanged': True,
        'service_activation_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'CLEANER-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
