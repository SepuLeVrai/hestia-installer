"""Real private files and maintenance locks; no SQL, services or Ext4 changes."""
import os
from pathlib import Path
import pickle
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from installer import mobile_reopen_guard as guard
from installer import gateway_state_release as release
from installer.maintenance import MaintenanceError, MaintenanceScope
from installer.model import canonical_bytes


class ReopenGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hestia-reopen-', dir='/var/lib')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o755)
        self.scope = MaintenanceScope(self.root / 'maintenance', 65534, 'a' * 32)
        self.scope.create(confirmed=True)
        self.lease = self.scope.acquire(confirmed=True)
        self.addCleanup(lambda: self.lease.close())
        self.plan = 'b' * 64
        self.intent = {'version': 1, 'fence': {'version': 1, 'instance': self.scope.instance,
            'lease_id': self.lease.lease_id, 'barrier_sha256': 'c' * 64},
            'snapshot_sha256': 'd' * 64, 'composed_sha256': 'e' * 64, 'web_verified_sha256': 'f' * 64}
        self.source = self.scope.directory / release.RELEASED
        self.marker = self.scope.directory / guard.MARKER
        self.write_source()

    def write_source(self):
        self.source.write_bytes(release._receipt(canonical_bytes(self.intent)))
        self.source.chmod(0o600)

    def begin(self, **kwargs):
        return guard.begin(self.lease, **{'plan_sha256': self.plan, 'confirmed': True, **kwargs})

    def recover(self, **kwargs):
        return guard.recover(self.lease, **{'plan_sha256': self.plan, 'confirmed': True, **kwargs})

    def closed_activity(self):
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        with self.assertRaises(MaintenanceError): self.lease.resume(confirmed=True)
        self.assertFalse((self.scope.directory / ('resumed-' + self.lease.lease_id + '.json')).exists())

    def test_begin_links_exact_plan_lease_release_and_backups_without_admission(self):
        source = self.source.read_bytes(); source_stat = self.source.stat()
        result = self.begin(); report = result.report()
        self.assertEqual(report['state'], 'MOBILE_REOPEN_BLOCKED')
        self.assertEqual(report['plan_sha256'], self.plan)
        self.assertEqual(report['lease_id'], self.lease.lease_id)
        self.assertEqual(report['bindings']['gateway_release_sha256'], release.f._sha(source))
        self.assertEqual(report['bindings']['service_profile_sha256'], 'c' * 64)
        self.assertEqual(report['bindings']['composed_backup_sha256'], 'e' * 64)
        self.assertFalse(report['admission_verified']); self.assertFalse(report['activity_resumed'])
        self.assertFalse(report['services_started'])
        self.assertEqual(self.source.read_bytes(), source)
        self.assertEqual(self.source.stat().st_mtime_ns, source_stat.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.marker.stat().st_mode), 0o600)
        result.assert_held(); self.closed_activity()

    def test_new_blocker_alone_refuses_low_level_resume_after_source_removed(self):
        result = self.begin(); self.source.unlink()
        self.closed_activity()
        with self.assertRaises(guard.ReopenGuardError): result.assert_held()

    def test_unknown_or_truncated_blocker_still_refuses_low_level_resume(self):
        self.source.unlink()
        for content in (b'', b'{', b'{}', b'not-json'):
            with self.subTest(content=content):
                self.marker.write_bytes(content); self.closed_activity()

    def test_blocker_symlink_and_directory_also_refuse_low_level_resume(self):
        self.source.unlink()
        self.marker.symlink_to(self.root / 'missing'); self.closed_activity(); self.marker.unlink()
        self.marker.mkdir(); self.closed_activity()

    def test_confirmation_and_closed_plan_input_precede_any_write(self):
        for consent in (False, None, 1, 'true'):
            with self.subTest(consent=consent), self.assertRaises(guard.ReopenGuardError):
                self.begin(confirmed=consent)
        for value in ('', 'x' * 64, 'b' * 63, 'B' * 64, 'b' * 64 + '\n', 'é' * 64,
                      '../../etc', 'b' * 65536, True, 1, -1, None, {'plan': self.plan}):
            with self.subTest(value=repr(value)[:80]), self.assertRaises(guard.ReopenGuardError):
                self.begin(plan_sha256=value)
        self.assertFalse(self.marker.exists())

    def test_exact_lease_type_root_and_live_lock_are_required(self):
        with self.assertRaises(guard.ReopenGuardError):
            guard.begin(object(), plan_sha256=self.plan, confirmed=True)
        with patch.object(guard.os, 'geteuid', return_value=65534), self.assertRaises(guard.ReopenGuardError): self.begin()
        self.lease.close()
        with self.assertRaises(guard.ReopenGuardError): self.begin()
        self.assertFalse(self.marker.exists())

    def test_missing_or_incomplete_gateway_release_never_creates_intent(self):
        self.source.unlink()
        with self.assertRaises(guard.ReopenGuardError): self.begin()
        self.write_source()
        for name in (release.g.MARKER, release.RELEASE):
            path = self.scope.directory / name; path.write_bytes(b'{}')
            with self.assertRaises(guard.ReopenGuardError): self.begin()
            path.unlink()
        self.assertFalse(self.marker.exists())

    def test_foreign_lease_instance_hash_or_intent_schema_refused(self):
        from copy import deepcopy
        original = deepcopy(self.intent)
        mutations = [('lease_id', '1' * 32), ('instance', '1' * 32), ('barrier_sha256', True)]
        for key, value in mutations:
            self.intent = deepcopy(original); self.intent['fence'][key] = value; self.write_source()
            with self.subTest(key=key), self.assertRaises(guard.ReopenGuardError): self.begin()
        for changes in ({'version': True}, {'version': 2}, {'snapshot_sha256': 42},
                        {'web_verified_sha256': ''}, {'unexpected': 'field'}):
            self.intent = {**original, **changes}; self.write_source()
            with self.subTest(changes=changes), self.assertRaises(guard.ReopenGuardError): self.begin()
        self.assertFalse(self.marker.exists())

    def test_malformed_release_has_no_raw_value_in_closed_error(self):
        for raw in (b'', b'[]', b'{"intent":null}', b'{"x":1,"x":2}', b'fixture-private-value',
                    b'{"intent":{"version":NaN}}', b'X' * (release.g.MAX_JOURNAL * 2 + 1)):
            self.source.write_bytes(raw)
            with self.assertRaises(guard.ReopenGuardError) as error: self.begin()
            self.assertNotIn('fixture-private-value', str(error.exception))
            self.assertFalse(self.marker.exists())

    def test_source_symlink_hardlink_and_wrong_permissions_refused(self):
        raw = self.source.read_bytes(); self.source.unlink()
        target = self.root / 'source'; target.write_bytes(raw); target.chmod(0o600)
        self.source.symlink_to(target)
        with self.assertRaises(guard.ReopenGuardError): self.begin()
        self.source.unlink(); os.link(target, self.source)
        with self.assertRaises(guard.ReopenGuardError): self.begin()
        target.unlink(); self.source.chmod(0o644)
        with self.assertRaises(guard.ReopenGuardError): self.begin()
        self.assertFalse(self.marker.exists())

    def test_begin_is_exclusive_and_recovery_never_rewrites(self):
        result = self.begin(); before = self.marker.stat(); raw = self.marker.read_bytes()
        with self.assertRaises(guard.ReopenGuardError): self.begin()
        with patch.object(guard.files, '_new', side_effect=AssertionError('rewrite')):
            recovered = self.recover()
        self.assertEqual(recovered.report(), result.report())
        self.assertEqual(self.marker.read_bytes(), raw)
        self.assertEqual((self.marker.stat().st_ino, self.marker.stat().st_mtime_ns),
                         (before.st_ino, before.st_mtime_ns))

    def test_recovery_with_foreign_plan_refuses_without_rewriting(self):
        self.begin(); raw = self.marker.read_bytes()
        with self.assertRaises(guard.ReopenGuardError): self.recover(plan_sha256='0' * 64)
        self.assertEqual(raw, self.marker.read_bytes()); self.closed_activity()

    def test_missing_partial_and_changed_intents_are_manual_not_rebuilt(self):
        with self.assertRaises(guard.ReopenGuardError): self.recover()
        self.assertFalse(self.marker.exists())
        self.begin(); raw = self.marker.read_bytes()
        for value in (b'', raw[:40], b'{}', raw + b'\n', b'X' * (guard.MAX_BYTES + 1)):
            self.marker.write_bytes(value)
            with self.assertRaises(guard.ReopenGuardError): self.recover()
            self.assertEqual(value, self.marker.read_bytes()); self.closed_activity()

    def test_changed_source_after_begin_is_never_adopted(self):
        result = self.begin(); raw = self.marker.read_bytes()
        self.intent['composed_sha256'] = '1' * 64; self.write_source()
        for operation in (result.assert_held, self.recover):
            with self.assertRaises(guard.ReopenGuardError): operation()
        self.assertEqual(raw, self.marker.read_bytes()); self.closed_activity()

    def test_guard_file_metadata_and_links_are_checked_on_recovery(self):
        self.begin(); self.marker.chmod(0o644)
        with self.assertRaises(guard.ReopenGuardError): self.recover()
        self.marker.chmod(0o600); os.link(self.marker, self.root / 'alias')
        with self.assertRaises(guard.ReopenGuardError): self.recover()
        (self.root / 'alias').unlink(); self.marker.rename(self.root / 'original')
        self.marker.symlink_to(self.root / 'original')
        with self.assertRaises(guard.ReopenGuardError): self.recover()
        self.closed_activity()

    def test_lost_response_after_durable_write_recovers_without_second_write(self):
        original = guard.files._new
        def lost(*args):
            original(*args); raise RuntimeError('fixture-private-value')
        with patch.object(guard.files, '_new', side_effect=lost), self.assertRaises(guard.ReopenGuardError): self.begin()
        before = self.marker.stat()
        self.recover().assert_held(); self.closed_activity()
        self.assertEqual(before.st_mtime_ns, self.marker.stat().st_mtime_ns)

    def test_partial_write_failure_leaves_a_blocker_and_no_false_recovery(self):
        def partial(fd, name, raw):
            guard.release.f._write(fd, name, raw[:30], 0, mode=0o600)
            raise OSError('fixture disk failure')
        with patch.object(guard.files, '_new', side_effect=partial), self.assertRaises(guard.ReopenGuardError): self.begin()
        with self.assertRaises(guard.ReopenGuardError): self.recover()
        self.closed_activity()

    def test_fresh_process_sigkill_and_exact_lease_recovery(self):
        lease_id = self.lease.lease_id; self.lease.close()
        script = '''
import os, signal, sys
from pathlib import Path
from installer.maintenance import MaintenanceScope
from installer.mobile_reopen_guard import begin
scope=MaintenanceScope(Path(sys.argv[1]),65534,'a'*32)
with scope.recover(sys.argv[2],confirmed=True) as lease:
    begin(lease,plan_sha256='b'*64,confirmed=True)
    os.kill(os.getpid(),signal.SIGKILL)
'''
        process = subprocess.run([sys.executable, '-c', script, str(self.scope.directory), lease_id],
                                 capture_output=True, timeout=15, check=False)
        self.assertEqual(process.returncode, -signal.SIGKILL, process.stderr.decode())
        raw = self.marker.read_bytes(); self.lease = self.scope.recover(lease_id, confirmed=True)
        self.recover().assert_held(); self.assertEqual(raw, self.marker.read_bytes()); self.closed_activity()

    def test_old_object_cannot_cross_process_or_closed_lease(self):
        result = self.begin()
        with patch.object(guard.os, 'getpid', return_value=result._pid + 1), self.assertRaises(guard.ReopenGuardError):
            result.assert_held()
        with self.assertRaises(TypeError): pickle.dumps(result)
        self.lease.close()
        with self.assertRaises(guard.ReopenGuardError): result.assert_held()

    def test_report_is_read_only_detached_and_never_claims_admission(self):
        result = self.begin(); report = result.report(); report['bindings'].clear()
        with patch.object(guard.files, '_read', side_effect=AssertionError('live probe')):
            self.assertEqual(len(result.report()['bindings']), 5)
            self.assertFalse(result.report()['admission_verified'])
        self.assertNotIn(self.scope.instance, repr(result))

    def test_historical_maintenance_without_new_guard_can_still_resume(self):
        self.source.unlink(); self.lease.resume(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'SERVING')
