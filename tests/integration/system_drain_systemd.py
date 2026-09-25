#!/usr/bin/env python3
"""Disposable Debian PID-1 systemd, real Apache/FPM and cgroup-v2 recipes.

The PHP endpoints and CLI/cleaner processes are fixtures, not a deployed Web or
the Debian session cleaner. Never run on an existing server.
"""
import argparse
import hashlib
import http.client
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import maintenance as m
from installer import system_drain as s
sys.path.insert(0, str(ROOT / 'scripts'))
import quality


def command(*args, check=True):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=45, check=check)


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Bounded fixture condition not reached')


class SystemDrainLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DRAIN_TEST') != '1' or os.geteuid() != 0:
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        if Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Real systemd PID 1 required')
        command('useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', 'hestia-drain-test')
        cls.web = pwd.getpwnam('hestia-drain-test')
        cls.php = next(p for p in (Path('/usr/sbin/php-fpm8.4'), Path('/usr/sbin/php-fpm8.2')) if p.is_file())
        if not Path('/sys/fs/cgroup/cgroup.controllers').is_file():
            raise RuntimeError('Unified cgroup v2 required')

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hestia-drain-', dir='/var/lib'))
        self.root.chmod(0o755)
        self.units = []
        self.foreign = None
        self.addCleanup(self.cleanup)
        for name in ('run', 'log', 'web', 'data'):
            (self.root / name).mkdir(mode=0o755)
        for name in ('tmp', 'uploads', 'sessions'):
            path = self.root / 'data' / name; path.mkdir(mode=0o700)
            os.chown(path, self.web.pw_uid, self.web.pw_gid)
        os.chown(self.root / 'data', self.web.pw_uid, self.web.pw_gid)
        self.scope = m.MaintenanceScope(self.root / 'maintenance', self.web.pw_gid, os.urandom(16).hex())
        self.scope.create(confirmed=True)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); self.port = listener.getsockname()[1]
        self.write(self.root / 'web' / 'index.php', '<?php session_start(); $_SESSION["fixture"]=17; echo "ready";')
        self.write(self.root / 'web' / 'upload.php', '<?php file_put_contents('
            + self.phpstr(self.root / 'data' / 'upload-executed')
            + ',"ran"); echo isset($_FILES["file"]) ? "uploaded" : "empty";')
        self.write(self.root / 'web' / 'delay.php', '<?php file_put_contents(' + self.phpstr(self.root / 'data' / 'entered')
            + ',"entered"); usleep(900000); echo "done";')
        helper = self.root / 'orphan.py'
        self.write(helper, 'import os,pathlib,time\nos.setsid()\nroot=pathlib.Path(' + repr(str(self.root / 'data'))
            + ')\n(root/"child-pid").write_text(str(os.getpid()))\nos.closerange(0,256)\nwhile True:\n'
            + '    with (root/"orphan-writes").open("ab") as out: out.write(b"x")\n    time.sleep(.01)\n')
        self.write(self.root / 'web' / 'orphan.php', '<?php file_put_contents('
            + self.phpstr(self.root / 'data' / 'worker-pid') + ',(string)getmypid()); '
            + '$p=proc_open(["/usr/bin/python3",' + self.phpstr(helper)
            + '],[0=>["file","/dev/null","r"],1=>["file","/dev/null","w"],2=>["file","/dev/null","w"]],$pipes); sleep(20);')
        self.fpm = self.root / 'fpm.conf'
        self.write(self.fpm, f'''[global]
pid = {self.root}/run/fpm.pid
error_log = {self.root}/log/fpm.log
daemonize = no
[hestia]
user = {self.web.pw_name}
group = {self.web.pw_name}
listen = {self.root}/run/fpm.sock
listen.owner = {self.web.pw_name}
listen.group = {self.web.pw_name}
listen.mode = 0600
pm = static
pm.max_children = 2
clear_env = yes
catch_workers_output = yes
security.limit_extensions = .php
php_admin_value[auto_prepend_file] = {self.scope.directory}/request_guard.php
php_admin_value[session.save_path] = {self.root}/data/sessions
php_admin_value[session.gc_maxlifetime] = 43200
php_admin_value[sys_temp_dir] = {self.root}/data/tmp
php_admin_value[upload_tmp_dir] = {self.root}/data/uploads
php_admin_value[upload_max_filesize] = 16M
php_admin_value[post_max_size] = 18M
php_admin_value[error_log] = {self.root}/data/php-error.log
php_admin_flag[display_errors] = off
''')
        self.apache = self.root / 'apache.conf'
        self.write(self.apache, f'''ServerRoot "{self.root}"
ServerName 127.0.0.1
DefaultRuntimeDir "{self.root}/run"
PidFile "{self.root}/run/apache.pid"
Listen 127.0.0.1:{self.port}
LoadModule mpm_event_module /usr/lib/apache2/modules/mod_mpm_event.so
LoadModule authz_core_module /usr/lib/apache2/modules/mod_authz_core.so
LoadModule proxy_module /usr/lib/apache2/modules/mod_proxy.so
LoadModule proxy_fcgi_module /usr/lib/apache2/modules/mod_proxy_fcgi.so
LoadModule reqtimeout_module /usr/lib/apache2/modules/mod_reqtimeout.so
RequestReadTimeout header=2 body=1-2,MinRate=1024
User {self.web.pw_name}
Group {self.web.pw_name}
ErrorLog "{self.root}/log/apache.log"
LogLevel warn
DocumentRoot "{self.root}/web"
<Directory />
  AllowOverride None
  Require all denied
</Directory>
<Directory "{self.root}/web">
  Options None
  AllowOverride None
  Require all granted
  <FilesMatch "\\.php$">
    SetHandler "proxy:unix:{self.root}/run/fpm.sock|fcgi://localhost/"
  </FilesMatch>
</Directory>
''')
        command(str(self.php), '-t', '-y', str(self.fpm))
        command('/usr/sbin/apache2', '-t', '-f', str(self.apache))
        commands = {'apache': f'/usr/sbin/apache2 -DFOREGROUND -f {self.apache}',
                    'php': f'{self.php} -F -y {self.fpm}',
                    'cli': '/usr/bin/sleep infinity', 'session-cleaner': '/usr/bin/sleep infinity'}
        bindings = []
        for role in s.ROLES:
            unit = 'hestia-' + self.scope.instance + '-' + role + '.service'
            fragment = f'''[Unit]
Description=Disposable HESTIA {role} recipe
[Service]
Type=simple
ExecStart={commands[role]}
Restart=no
KillMode=control-group
SendSIGKILL=yes
TimeoutStopSec=4s
Delegate=no
'''
            self.write(s.UNIT_ROOT / unit, fragment)
            self.units.append(unit)
            directory = s.UNIT_ROOT / (unit + '.d'); directory.mkdir(mode=0o755)
            self.write(directory / '50-hestia-maintenance.conf', s.condition_dropin(self.scope).decode())
            bindings.append(s.UnitBinding(role, hashlib.sha256(fragment.encode()).hexdigest()))
        self.drain = s.SystemDrain(self.scope, tuple(bindings))
        command('systemctl', 'daemon-reload')
        for role in ('php', 'apache', 'cli', 'session-cleaner'):
            command('systemctl', 'start', self.drain.unit(role))
        until(lambda: self.ready())

    def cleanup(self):
        units = self.units + ([self.foreign] if self.foreign else [])
        for unit in units:
            command('systemctl', 'stop', unit, check=False)
            command('systemctl', 'reset-failed', unit, check=False)
            (s.UNIT_ROOT / unit).unlink(missing_ok=True)
            shutil.rmtree(s.UNIT_ROOT / (unit + '.d'), ignore_errors=True)
        command('systemctl', 'daemon-reload', check=False)
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def write(path, content):
        path.write_text(content); path.chmod(0o644)

    @staticmethod
    def phpstr(path):
        return 'hex2bin("' + os.fsencode(path).hex() + '")'

    def request(self, path='/index.php'):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            client.request('GET', path); response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def ready(self):
        try:
            return self.request() == (200, b'ready')
        except (OSError, http.client.HTTPException):
            return False

    def assert_stopped(self, result):
        self.assertTrue(result.report()['cgroup_empty_verified'])
        for role in s.ROLES:
            state = s._show(self.drain.unit(role))
            self.assertEqual(state['ActiveState'], 'inactive')
            self.assertTrue(s._empty_cgroup(self.drain.unit(role)))
        with self.assertRaises(OSError):
            self.request()
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_real_apache_fpm_stop_preserves_session_and_keeps_gate_on_close(self):
        sessions = {p.name: p.read_bytes() for p in (self.root / 'data/sessions').iterdir()}
        self.assertTrue(sessions)
        with self.drain.acquire(confirmed=True) as result:
            self.assert_stopped(result)
            self.assertFalse(result.report()['system_wiring_verified'])
        self.assertEqual(sessions, {p.name: p.read_bytes() for p in (self.root / 'data/sessions').iterdir()})
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def partial_multipart(self):
        stream = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        self.addCleanup(stream.close)
        header = (f'POST /upload.php HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                  'Content-Type: multipart/form-data; boundary=hestia-boundary\r\n'
                  'Content-Length: 10000000\r\n\r\n').encode()
        body = b'--hestia-boundary\r\nContent-Disposition: form-data; name="file"; filename="fixture.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
        stream.sendall(header + body + b'x' * (512 * 1024))
        until(lambda: any((self.root / 'data/uploads').iterdir()))
        return stream

    def test_partial_multipart_before_php_guard_is_drained_and_listener_closed(self):
        self.partial_multipart()
        with self.drain.acquire(confirmed=True) as result:
            self.assert_stopped(result)
            snapshot = {p.name: p.stat().st_size for p in (self.root / 'data/uploads').iterdir()}
            time.sleep(.15)
            self.assertEqual(snapshot, {p.name: p.stat().st_size for p in (self.root / 'data/uploads').iterdir()})
            self.assertFalse((self.root / 'data/upload-executed').exists())

    def test_unbounded_stalled_upload_is_refused_without_certifying_forced_stop(self):
        # Preserve the first campaign's counterexample. No artificial increase
        # of the stop deadline and no acceptance of systemd Result=timeout.
        text = self.apache.read_text().replace('RequestReadTimeout header=2 body=1-2,MinRate=1024',
                                              'RequestReadTimeout header=2 body=0')
        self.write(self.apache, text)
        command('/usr/sbin/apache2', '-t', '-f', str(self.apache))
        command('systemctl', 'restart', self.drain.unit('apache'))
        until(lambda: self.ready()); self.partial_multipart()
        with self.assertRaisesRegex(s.SystemDrainError, 'NOT_EMPTY'):
            self.drain.acquire(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertEqual(s._show(self.drain.unit('apache'))['Result'], 'timeout')
        self.assertFalse((self.root / 'data/upload-executed').exists())

    def test_detached_child_surviving_killed_php_worker_is_removed_by_cgroup_stop(self):
        outcome = []
        def request():
            try: outcome.append(self.request('/orphan.php'))
            except (OSError, http.client.HTTPException): outcome.append(None)
        thread = threading.Thread(target=request, daemon=True); thread.start()
        until(lambda: (self.root / 'data/child-pid').is_file() and (self.root / 'data/worker-pid').is_file())
        pid = int((self.root / 'data/child-pid').read_text())
        worker = int((self.root / 'data/worker-pid').read_text())
        self.assertIn(self.drain.unit('php'), Path(f'/proc/{pid}/cgroup').read_text())
        os.kill(worker, signal.SIGKILL); thread.join(5); self.assertFalse(thread.is_alive())
        heartbeat = self.root / 'data/orphan-writes'
        until(lambda: heartbeat.is_file() and heartbeat.stat().st_size > 1)
        before = heartbeat.stat().st_size
        until(lambda: heartbeat.stat().st_size > before)
        with self.drain.acquire(confirmed=True) as result:
            self.assert_stopped(result)
            size = heartbeat.stat().st_size; time.sleep(.15)
            self.assertEqual(size, heartbeat.stat().st_size)
            self.assertFalse(Path(f'/proc/{pid}').exists())

    def test_inflight_guard_timeout_stays_closed_then_exact_recovery_drains(self):
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(self.request('/delay.php')), daemon=True)
        thread.start(); until(lambda: (self.root / 'data/entered').exists())
        with self.assertRaisesRegex(m.MaintenanceError, 'DRAIN_TIMEOUT'):
            self.drain.acquire(confirmed=True, timeout=.05)
        state = self.scope.observe(); self.assertEqual(state['state'], 'MAINTENANCE_REQUIRED')
        thread.join(5); self.assertEqual(outcome, [(200, b'done')])
        self.assertEqual(self.request()[0], 503)
        with self.drain.recover(state['lease_id'], confirmed=True) as result:
            self.assert_stopped(result)

    def test_controller_sigkill_after_first_real_stop_keeps_gate_and_recovers(self):
        context = multiprocessing.get_context('fork'); ready = context.Event()
        original = s._systemctl
        def child():
            def command_once(action, unit):
                value = original(action, unit)
                if action == 'stop' and unit == self.drain.unit('apache'):
                    ready.set(); time.sleep(20)
                return value
            with patch.object(s, '_systemctl', side_effect=command_once):
                self.drain.acquire(confirmed=True)
        process = context.Process(target=child); process.start()
        try:
            self.assertTrue(ready.wait(8)); process.kill(); process.join(5)
            self.assertEqual(process.exitcode, -signal.SIGKILL)
        finally:
            if process.is_alive(): process.kill(); process.join(5)
        state = self.scope.observe()
        self.assertEqual(s._show(self.drain.unit('apache'))['ActiveState'], 'inactive')
        self.assertEqual(s._show(self.drain.unit('php'))['ActiveState'], 'active')
        self.assertTrue(list(self.scope.directory.glob('system-drain-*.attempt')))
        with self.drain.recover(state['lease_id'], confirmed=True) as result:
            self.assert_stopped(result)

    def test_loaded_condition_prevents_explicit_service_start_while_gate_persists(self):
        with self.drain.acquire(confirmed=True) as result:
            for role in s.ROLES:
                command('systemctl', 'start', self.drain.unit(role))
            self.assert_stopped(result)

    def test_fragment_drift_refuses_before_gate_or_stop_and_live_dropin_drift_revokes(self):
        fragment = s.UNIT_ROOT / self.drain.unit('cli'); before = fragment.read_bytes()
        fragment.write_bytes(before + b'\n# drift\n')
        with self.assertRaisesRegex(s.SystemDrainError, 'UNIT_DRIFT'):
            self.drain.acquire(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'SERVING'); self.assertTrue(self.ready())
        fragment.write_bytes(before); command('systemctl', 'daemon-reload')
        with self.drain.acquire(confirmed=True) as result:
            dropin = s.UNIT_ROOT / (self.drain.unit('php') + '.d/50-hestia-maintenance.conf')
            dropin.write_bytes(dropin.read_bytes() + b'# changed\n')
            with self.assertRaisesRegex(s.SystemDrainError, 'UNIT_DRIFT'):
                result.report()
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_unrelated_active_unit_is_never_stopped_or_reconfigured(self):
        self.foreign = 'hestia-foreign-fixture-' + self.scope.instance + '.service'
        content = '[Service]\nExecStart=/usr/bin/sleep infinity\n'
        self.write(s.UNIT_ROOT / self.foreign, content)
        command('systemctl', 'daemon-reload'); command('systemctl', 'start', self.foreign)
        before = command('systemctl', 'show', '--property=MainPID', '--value', self.foreign).stdout
        with self.drain.acquire(confirmed=True) as result:
            self.assert_stopped(result)
            self.assertEqual(command('systemctl', 'is-active', self.foreign).stdout.strip(), b'active')
            self.assertEqual(before, command('systemctl', 'show', '--property=MainPID', '--value', self.foreign).stdout)
            self.assertEqual((s.UNIT_ROOT / self.foreign).read_text(), content)

    def test_pending_gate_requires_explicit_recovery_and_closed_lease_is_unusable(self):
        result = self.drain.acquire(confirmed=True); result.close()
        with self.assertRaisesRegex(m.MaintenanceError, 'LEASE_REQUIRED'): result.report()
        with self.assertRaisesRegex(m.MaintenanceError, 'MAINTENANCE_PENDING'):
            self.drain.acquire(confirmed=True)
        state = self.scope.observe()
        with self.assertRaisesRegex(m.MaintenanceError, 'RECOVERY_MISMATCH'):
            self.drain.recover('f' * 32, confirmed=True)
        with self.drain.recover(state['lease_id'], confirmed=True) as recovered:
            self.assert_stopped(recovered)

    def test_forced_stop_timeout_is_not_certified_even_when_kernel_cgroup_is_empty(self):
        unit = self.drain.unit('cli'); command('systemctl', 'stop', unit)
        helper = self.root / 'ignore-term.py'
        self.write(helper, 'import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
            + 'open(' + repr(str(self.root / 'ignoring-term')) + ',"w").close()\ntime.sleep(60)\n')
        fragment = (s.UNIT_ROOT / unit).read_text().replace('/usr/bin/sleep infinity', '/usr/bin/python3 ' + str(helper)).replace('TimeoutStopSec=4s', 'TimeoutStopSec=1s')
        self.write(s.UNIT_ROOT / unit, fragment)
        bindings = tuple(s.UnitBinding(b.role, hashlib.sha256(fragment.encode()).hexdigest())
                         if b.role == 'cli' else b for b in self.drain.bindings)
        self.drain = s.SystemDrain(self.scope, bindings)
        command('systemctl', 'daemon-reload'); command('systemctl', 'start', unit)
        until(lambda: (self.root / 'ignoring-term').exists())
        with self.assertRaises(s.SystemDrainError): self.drain.acquire(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        until(lambda: s._empty_cgroup(unit))
        self.assertNotEqual(s._show(unit)['Result'], 'success')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    source = quality.snapshot(ROOT)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SystemDrainLive)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    source_stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Real Debian systemd Apache FPM stop-only barrier', 'tests': result.testsRun,
        'expected': 11, 'failures': len(result.failures), 'errors': len(result.errors),
        'skips': len(result.skipped), 'status': 'PASS' if result.wasSuccessful() and result.testsRun == 11
        and not result.skipped and source_stable else 'FAIL', 'web_application_qualified': False,
        'native_session_cleaner_qualified': False, 'source_stable': source_stable, 'source_files': len(source)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report)); sys.exit(0 if report['status'] == 'PASS' else 1)
