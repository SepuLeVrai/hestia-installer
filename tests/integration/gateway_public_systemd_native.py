"""Real disposable PID 1 tests of the transfer subsystem, not full HESTIA boot."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, '/opt/hestia-installer')
from installer import gateway_public_fragments as f, gateway_public_systemd as s
from installer.maintenance import MaintenanceScope


def command(*args):
    subprocess.run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', *args],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=40)


class NativeSystemdTransferTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('HESTIA_PUBLIC_SYSTEMD_TEST'), '1')
        self.assertEqual(Path('/proc/1/comm').read_text().strip(), 'systemd')
        self.instance = uuid.uuid4().hex
        self.temp = tempfile.TemporaryDirectory(prefix='hestia-systemd-', dir='/var/lib')
        self.addCleanup(self.temp.cleanup); self.base = Path(self.temp.name); self.base.chmod(0o755)
        self.control = self.base / 'control'; self.control.mkdir(mode=0o700)
        self.public = self.base / 'public'; self.public.mkdir(mode=0o700)
        self.scope = MaintenanceScope(self.base / 'maintenance', 65534, self.instance)
        self.scope.create(confirmed=True); self.lease = self.scope.acquire(confirmed=True)
        self.addCleanup(lambda: self.lease.close())
        self.lease_id = self.lease.lease_id; self.unit_root = s.boot.h.drain.UNIT_ROOT
        names = f.resources(self.instance); self.apache = names[-1].split('.d/')[0]
        self.all_units = (*names[:-1], self.apache)
        self.addCleanup(self.clean_units)
        self.replacements = {}
        for name in names:
            if name.endswith('.conf'): raw = b'[Service]\nExecStartPre=/usr/bin/true source\nEnvironment=HESTIA_GENERATION=source\n'
            elif name.endswith('.timer'):
                raw = ('[Unit]\nDescription=source\n[Timer]\nOnActiveSec=1d\nUnit=' + names[5] + '\n').encode()
            else: raw = b'[Unit]\nDescription=source\n[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nRestart=no\n'
            self.replacements[name] = (raw, raw.replace(b'source', b'target'))
            path = self.unit_root / name; path.parent.mkdir(exist_ok=True)
            with path.open('xb') as stream: stream.write(raw)
            path.chmod(0o644)
        (self.unit_root / self.apache).write_bytes(b'[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nRestart=no\n')
        (self.unit_root / self.apache).chmod(0o644)
        guard = self.unit_root / (self.apache + '.d/50-hestia-maintenance.conf')
        guard.write_bytes(s.boot.h.drain.condition_dropin(self.scope)); guard.chmod(0o644)
        command('daemon-reload')
        command('start', '--', names[3], names[4], names[6])
        self.refs = dict.fromkeys(f.REFS, 'b' * 64); self.refs['target_gateway'] = 'c' * 64
        self.manager = self.new()
        try: self.confirmation = self.manager.prepare(confirmed=True)['confirmation']
        except Exception:
            if not getattr(type(self), '_diagnosed', False):
                type(self)._diagnosed = True
                for unit in (names[0], names[6], self.apache):
                    # Only synthetic fixture commands; no application secrets.
                    raw = subprocess.check_output(['/usr/bin/systemctl', 'show', '--all',
                        '--property=Id,LoadState,FragmentPath,DropInPaths,NeedDaemonReload,ActiveState,SubState,Job,InvocationID,Description,ControlGroup,MainPID,ControlPID,Result,ExecStart,ExecStartPre,Unit', '--', unit])
                    print('FIXTURE_MANAGER_PROPERTIES', raw.decode(), flush=True)
            raise

    def clean_units(self):
        command('stop', '--', *self.all_units)
        for name in self.all_units:
            path = self.unit_root / name
            if path.exists(): path.unlink()
        directory = self.unit_root / (self.apache + '.d')
        if directory.exists(): shutil.rmtree(directory)
        command('daemon-reload')

    def new(self):
        transfer = f.FragmentTransfer(self.control / ('public-fragments-' + self.lease_id),
            self.unit_root, self.lease, self.refs, self.replacements)
        return s.Manager(transfer, self.public / 'effect-lock.json')

    def apply(self): return self.manager.apply(self.confirmation, confirmed=True)

    def test_real_stops_fragments_reload_and_read_only_check(self):
        before = {r: s.show(self.manager.public[r])['InvocationID'] for r in ('http', 'https')}
        self.assertTrue(all(before.values()))
        report = self.apply(); self.assertEqual(report['state'], 'PUBLIC_FRAGMENTS_LOADED_CLOSED')
        for name, (_, target) in self.replacements.items(): self.assertEqual((self.unit_root / name).read_bytes(), target)
        with patch.object(s.boot.h, '_command', side_effect=AssertionError('effect during check/repeat')):
            self.assertEqual(self.manager.check(self.confirmation), report)
            self.assertEqual(self.apply(), report)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_wrong_consent_and_confirmation_leave_running_listeners(self):
        before = s.show(self.manager.public['http'])
        with self.assertRaises(f.FragmentError): self.manager.apply(self.confirmation, confirmed=False)
        with self.assertRaises(f.FragmentError): self.manager.apply('f' * 64, confirmed=True)
        self.assertEqual(s.show(self.manager.public['http']), before)

    def test_same_byte_foreign_source_inode_refuses_before_stop(self):
        path = self.unit_root / next(iter(self.replacements)); raw = path.read_bytes()
        old = self.base / 'old'; path.rename(old); path.write_bytes(raw); path.chmod(0o644)
        with self.assertRaises(f.FragmentError): self.apply()
        self.assertEqual(s.show(self.manager.public['http'])['ActiveState'], 'active')

    def test_active_renewal_refuses_and_does_not_kill_renewal(self):
        command('start', '--', self.manager.public['renew'])
        before = s.show(self.manager.public['renew'])['InvocationID']
        with self.assertRaises(f.FragmentError): self.apply()
        self.assertEqual(s.show(self.manager.public['renew'])['InvocationID'], before)
        self.assertEqual(s.show(self.manager.public['http'])['ActiveState'], 'active')

    def test_lost_stop_reply_observes_stopped_without_replay(self):
        original = s.boot.h._command
        def interrupted(argv, *args, **kwargs):
            result = original(argv, *args, **kwargs)
            if 'stop' in argv: raise RuntimeError('lost stop response')
            return result
        with patch.object(s.boot.h, '_command', interrupted), self.assertRaises(RuntimeError): self.apply()
        calls = []
        def resumed(argv, *args, **kwargs):
            calls.append(argv); return original(argv, *args, **kwargs)
        with patch.object(s.boot.h, '_command', resumed): self.apply()
        self.assertFalse(any('stop' in argv and self.manager.public['timer'] in argv for argv in calls))

    def test_foreign_maintenance_overlay_refuses_before_stop(self):
        guard = self.unit_root / (self.apache + '.d/50-hestia-maintenance.conf')
        guard.write_bytes(b'[Unit]\nConditionPathExists=/foreign\n')
        with self.assertRaises(f.FragmentError): self.apply()
        self.assertEqual(s.show(self.manager.public['http'])['ActiveState'], 'active')

    def test_restarted_listener_after_lost_stop_reply_is_not_stopped_again(self):
        original = s.boot.h._command
        def interrupted(argv, *args, **kwargs):
            result = original(argv, *args, **kwargs)
            if 'stop' in argv and self.manager.public['https'] in argv:
                raise RuntimeError('lost listener stop response')
            return result
        with patch.object(s.boot.h, '_command', interrupted), self.assertRaises(RuntimeError): self.apply()
        command('start', '--', self.manager.public['https'])
        new_invocation = s.show(self.manager.public['https'])['InvocationID']
        with self.assertRaises(f.FragmentError): self.apply()
        self.assertEqual(s.show(self.manager.public['https'])['InvocationID'], new_invocation)
        self.assertEqual(s.show(self.manager.public['https'])['ActiveState'], 'active')

    def test_partial_fragment_transfer_resumes_with_pending_manager_reload(self):
        original = os.rename
        def interrupted(*args, **kwargs):
            original(*args, **kwargs); raise RuntimeError('lost fragment rename response')
        with patch.object(f.os, 'rename', interrupted), self.assertRaises(RuntimeError): self.apply()
        first = self.unit_root / next(iter(self.replacements)); inode = first.stat().st_ino
        # PID 1 may garbage-collect an inactive unit and load its new bytes on
        # show. NeedDaemonReload=no is therefore not proof that reload ran.
        with f.fs._directory(self.manager.root) as fd:
            self.assertIsNone(f._optional(fd, 'reload.intent.json'))
        calls = []; native = s.boot.h._command
        def observed(argv, *args, **kwargs):
            calls.append(argv); return native(argv, *args, **kwargs)
        with patch.object(s.boot.h, '_command', observed): self.apply()
        self.assertEqual(sum('daemon-reload' in argv for argv in calls), 1)
        self.assertEqual(first.stat().st_ino, inode)
        self.assertEqual(self.manager.check(self.confirmation)['state'], 'PUBLIC_FRAGMENTS_LOADED_CLOSED')

    def test_real_sigkill_after_reload_does_not_repeat_reload(self):
        self.lease.close(); pid = os.fork()
        if pid == 0:
            try:
                self.lease = self.scope.recover(self.lease_id, confirmed=True); self.manager = self.new()
                original = s.boot.h._command
                def killed(argv, *args, **kwargs):
                    result = original(argv, *args, **kwargs)
                    if 'daemon-reload' in argv: os.kill(os.getpid(), signal.SIGKILL)
                    return result
                with patch.object(s.boot.h, '_command', killed): self.apply()
            except BaseException: os._exit(98)
            os._exit(97)
        deadline = time.monotonic() + 60
        while True:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found: break
            if time.monotonic() > deadline:
                os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0); self.fail('child timeout')
            time.sleep(.05)
        self.assertTrue(os.WIFSIGNALED(status)); self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        self.lease = self.scope.recover(self.lease_id, confirmed=True); self.manager = self.new()
        with patch.object(s.boot.h, '_command', side_effect=AssertionError('reload/start replay')): self.apply()


if __name__ == '__main__':
    from scripts.quality import snapshot, encode, digest
    before = snapshot()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeSystemdTransferTests)
    ids = [test.id().replace('__main__.', 'gateway_public_systemd_native.') for test in suite]
    required = json.loads(Path('/opt/hestia-installer/tests/quality-baseline.json').read_text())['required_tests']['public_systemd']
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = (result.wasSuccessful() and set(ids) == set(required) and len(set(ids)) == len(ids)
        and result.testsRun == len(required) and not result.skipped and not result.expectedFailures
        and not result.unexpectedSuccesses and snapshot() == before)
    report = {'status': 'PASS' if passed else 'FAIL', 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
              'source_manifest_sha256': digest(encode(before)), 'source_stable': snapshot() == before,
              'discovered_ids': ids, 'missing_ids': sorted(set(required) - set(ids)),
              'scope': 'systemd transfer subsystem with real fixture services',
              'native_public_boot_qualified': False}
    Path('/evidence/public-systemd.json').write_bytes(encode(report))
    Path('/evidence/SOURCE-MANIFEST.json').write_bytes(encode(before))
    raise SystemExit(0 if passed else 1)
