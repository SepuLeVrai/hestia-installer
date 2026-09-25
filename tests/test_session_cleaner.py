"""Real file locks and hostile session entries, without running a host scheduler."""
import fcntl
import io
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import http_runtime as h
from installer import session_cleaner as c
from installer.operations import RecoveryDecision
from installer.private import session_cleaner_worker as w


class SessionCollectorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir='/var/lib', prefix='hestia-cleaner-core-'))
        self.addCleanup(lambda: shutil.rmtree(self.root))
        self.data = self.root / 'sessions'; self.data.mkdir(mode=0o700)
        self.gate = self.root / 'gate'; self.gate.mkdir(mode=0o700)
        self.fd = os.open(self.data, os.O_RDONLY | os.O_DIRECTORY)
        self.gfd = os.open(self.gate, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd); self.addCleanup(os.close, self.gfd)
        self.now = w.time.time_ns()
        clock = patch.object(w.time, 'time_ns', return_value=self.now); clock.start(); self.addCleanup(clock.stop)

    def file(self, name='sess_old', age=43201):
        path = self.data / name; path.write_bytes(b'private session fixture'); path.chmod(0o600)
        stamp = self.now - age * 1000000000; os.utime(path, ns=(stamp, stamp))
        return path

    def collect(self): return w.collect(self.fd, os.geteuid(), os.getegid(), self.gfd)

    def test_only_expired_files_removed_and_exact_boundary_future_and_dates_preserved(self):
        old = self.file(); boundary = self.file('sess_boundary', 43200)
        young = self.file('sess_young', 3600); future = self.file('sess_future', -60)
        before = {p: w.identity(p.stat()) for p in (boundary, young, future)}
        result = self.collect()
        self.assertEqual(result['removed'], 1); self.assertFalse(old.exists())
        self.assertEqual(before, {p: w.identity(p.stat()) for p in before})

    def test_session_content_is_never_read_or_evaluated(self):
        path = self.file(); path.write_bytes(b'<?php dangerous(); ?>'); path.chmod(0o600)
        stamp = self.now - 50000 * 1000000000; os.utime(path, ns=(stamp, stamp))
        with patch.object(w.os, 'read', side_effect=AssertionError('session bytes must not be read')):
            self.assertEqual(self.collect()['removed'], 1)

    def test_actual_exclusive_file_lock_preserves_active_expired_session(self):
        path = self.file(); fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.collect(); self.assertEqual(result['locked'], 1)
            self.assertEqual(result['removed'], 0); self.assertTrue(path.exists())
        finally: os.close(fd)
        self.assertEqual(self.collect()['removed'], 1)

    def test_durable_gate_even_malformed_or_symlink_prevents_deletion(self):
        path = self.file(); marker = self.gate / 'maintenance.attempt'
        marker.write_bytes(b'incomplete')
        self.assertEqual(self.collect()['state'], 'SESSION_CLEANER_MAINTENANCE'); self.assertTrue(path.exists())
        marker.unlink(); marker.symlink_to('/nonexistent')
        self.assertEqual(self.collect()['removed'], 0); self.assertTrue(path.exists())

    def test_unrecognized_filename_aborts_prescan_without_deleting_other_expired_files(self):
        path = self.file(); self.file('business-data')
        with self.assertRaises(w.CleanerError): self.collect()
        self.assertTrue(path.exists())

    def test_symlink_hardlink_and_directory_are_refused_before_unlink(self):
        path = self.file(); link = self.data / 'sess_bad'
        link.symlink_to(path)
        with self.assertRaises(w.CleanerError): self.collect()
        link.unlink(); os.link(path, link)
        with self.assertRaises(w.CleanerError): self.collect()
        link.unlink(); link.mkdir()
        with self.assertRaises(w.CleanerError): self.collect()
        self.assertTrue(path.exists())

    def test_fifo_is_refused_without_blocking(self):
        path = self.file(); os.mkfifo(self.data / 'sess_pipe')
        with self.assertRaises(w.CleanerError): self.collect()
        self.assertTrue(path.exists())

    def test_foreign_owners_public_permissions_and_acl_are_not_silently_accepted(self):
        path = self.file()
        with self.assertRaises(w.CleanerError): w.collect(self.fd, 99999, 99999, self.gfd)
        path.chmod(0o644)
        with self.assertRaises(w.CleanerError): self.collect()
        path.chmod(0o600)
        with patch.object(w, 'no_acl', side_effect=w.CleanerError('SESSION_CLEANER_REJECTED')):
            with self.assertRaises(w.CleanerError): self.collect()
        self.assertTrue(path.exists())

    def test_entry_budget_refuses_before_any_deletion(self):
        path = self.file(); self.file('sess_second')
        with patch.object(w, 'MAX_ENTRIES', 1), self.assertRaises(w.CleanerError): self.collect()
        self.assertTrue(path.exists())

    def test_deadline_is_bounded_without_waiting_or_weakening_it(self):
        path = self.file()
        with patch.object(w.time, 'monotonic', side_effect=[0, 0, 3]), self.assertRaises(w.CleanerError): self.collect()
        self.assertTrue(path.exists())

    def test_changed_inode_or_timestamp_after_prescan_cannot_be_deleted(self):
        path = self.file(); original = w.os.open; calls = 0
        def opened(name, *args, **kwargs):
            nonlocal calls
            if name == path.name:
                calls += 1
                if calls == 2: os.utime(path, ns=(self.now, self.now))
            return original(name, *args, **kwargs)
        with patch.object(w.os, 'open', side_effect=opened), self.assertRaises(w.CleanerError): self.collect()
        self.assertTrue(path.exists())

    def test_gate_published_between_candidates_stops_further_deletions(self):
        self.file(); self.file('sess_second')
        with patch.object(w, 'gated', side_effect=[False, False, True]):
            result = self.collect()
        self.assertEqual(result['removed'], 1); self.assertEqual(result['state'], 'SESSION_CLEANER_MAINTENANCE')
        self.assertEqual(len(list(self.data.iterdir())), 1)

    def test_malformed_template_or_root_execution_returns_only_closed_diagnostic(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(w.main(), 1)
        self.assertEqual(output.getvalue(), '{"state":"SESSION_CLEANER_REJECTED"}\n')
        with self.assertRaises(w.CleanerError): w.clean({'root': '/etc'})


class SessionCleanerOperationTests(unittest.TestCase):
    def setUp(self):
        runtime = h.HttpRuntime(h.RuntimeSpec('c' * 32, Path('/var/lib/hestia-cleaner'),
            Path('/srv/hestia-web'), 'hestia-web', 'hestia.test', 8123, '8.4'))
        self.cleaner = c.SessionCleaner(runtime); self.operation = c.SessionCleanerOperation(self.cleaner)

    def test_consent_is_required_before_host_observation_or_filesystem_mutation(self):
        with patch.object(self.cleaner, 'prepare') as prepare:
            for value in (False, None, 'yes', 1):
                with self.assertRaisesRegex(c.SessionCleanerError, 'CONSENT_REQUIRED'):
                    self.cleaner.create(confirmed=value)
            prepare.assert_not_called()

    def test_recovery_observes_only_and_partial_or_rollback_is_manual(self):
        with patch.object(self.cleaner, 'observe', return_value={'plan_sha256': 'e' * 64}), \
             patch.object(self.cleaner, 'create') as create:
            self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
            self.assertEqual(self.operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)
            create.assert_not_called()
        with patch.object(self.cleaner, 'observe', side_effect=OSError('private-error')):
            self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        self.assertFalse(self.operation.spec.rollback_supported)

    def test_validation_binds_receipt_to_live_plan_and_hides_private_paths(self):
        context = SimpleNamespace(evidence={'hashes_non_secret': {'session_cleaner_plan': 'e' * 64}})
        with patch.object(self.cleaner, 'observe', return_value={'plan_sha256': 'e' * 64}):
            self.assertTrue(self.operation.validate(context))
        with patch.object(self.cleaner, 'observe', return_value={'plan_sha256': 'f' * 64}):
            self.assertFalse(self.operation.validate(context))
            with self.assertRaises(c.SessionCleanerError): self.operation.commit(context)
        self.assertNotIn('/var/lib', repr(self.cleaner))


if __name__ == '__main__': unittest.main()
