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


class CombinedConfigurationTests(unittest.TestCase):
    """Real source/receipt drift; host identity and service observations doubled."""
    def setUp(self):
        from installer import application_activation as activation
        self.root = Path(tempfile.mkdtemp(dir='/var/lib', prefix='hestia-combined-'))
        self.web = Path(tempfile.mkdtemp(dir='/srv', prefix='hestia-combined-'))
        for root in (self.root, self.web):
            root.chmod(0o755); self.addCleanup(shutil.rmtree, root)
        self.code = self.web / 'index.php'; self.code.write_bytes(b'<?php // fixture'); self.code.chmod(0o644)
        self.units = self.root / 'units'; self.units.mkdir()
        template = Path(c.__file__).parent / 'private/session_cleaner_worker.py'
        self.put(self.root / 'private/session_cleaner_worker.py', template.read_bytes())
        self.runtime = h.HttpRuntime(h.RuntimeSpec('a' * 32, self.root / 'http', self.web,
            'hestia-fixture', 'hestia.test', 8123, '8.4'))
        self.runtime.spec.root.mkdir()
        self.account = SimpleNamespace(pw_uid=0, pw_gid=0, pw_name='hestia-fixture')
        self.scope = SimpleNamespace(instance='a' * 32, directory=self.root / 'gate',
            _profile=lambda: b'fixture-profile', _guard=lambda: b'fixture-guard')
        self.activation = activation.Activation(self.runtime, 'b' * 64)
        self.cleaner = self.activation.cleaner
        self.cleaner.directory.mkdir(mode=0o750)
        self.files = {self.units / self.runtime.unit(role): ('fixture-' + role).encode()
                      for role in ('php', 'apache')}
        patches = (
            patch.object(c, '__file__', str(self.root / 'session_cleaner.py')),
            patch.object(h.drain, 'UNIT_ROOT', self.units),
            patch.object(self.runtime, '_host', return_value=(self.account, None)),
            patch.object(self.runtime, '_scope', return_value=self.scope),
            patch.object(self.runtime, '_files', return_value=self.files),
            patch.object(self.runtime, '_directories', return_value={}),
            patch.object(self.runtime, '_dependency_hashes', return_value={}),
            patch.object(h, '_system_file_digest', return_value={'sha256': 'd' * 64}),
        )
        for mock in patches: mock.start(); self.addCleanup(mock.stop)
        plan = self.runtime._plan(self.account, None)
        self.initial = {'version': 1, 'state': 'HTTP_RUNTIME_STAGED',
            'plan_sha256': h.f._sha(plan), 'lease_id': 'c' * 32}
        self.put(self.runtime.spec.root / 'provision.attempt', plan)
        self.put(self.runtime.spec.root / 'staged.json', h.p._json(self.initial))
        for path, raw in self.files.items(): self.put(path, raw, 0o644)
        _, _, files, plan = self.cleaner._profile_inputs(self.account, self.scope, h.f._sha(plan))
        self.put(self.cleaner.directory / 'cleaner.attempt', plan)
        self.put(self.cleaner.directory / 'staged.json', h.p._json({'version': 1,
            'state': 'SESSION_CLEANER_STAGED', 'plan_sha256': h.f._sha(plan), 'lease_id': 'c' * 32}))
        for path, raw in files.items():
            self.put(path, raw, 0o640 if path.parent == self.cleaner.directory else 0o644)

    def put(self, path, raw, mode=0o640):
        path.parent.mkdir(exist_ok=True); path.write_bytes(raw); path.chmod(mode)

    def activate(self):
        with patch.object(h.drain, 'audit_unit') as audit, patch.object(self.cleaner, '_timer_state') as timer:
            result = self.activation.configuration()
            self.assertEqual([call.args[1].role for call in audit.call_args_list], ['php', 'apache', 'session-cleaner'])
            self.assertTrue(audit.call_args.kwargs['running_collector'])
            timer.assert_called_once_with(stopped=False)
            return result

    def test_activation_scans_source_once_per_fresh_call_and_retains_all_unit_audits(self):
        with patch.object(h, '_code_digest', wraps=h._code_digest) as digest:
            self.assertEqual(self.activate(), (self.scope, self.initial))
            self.assertEqual(digest.call_count, 1)
            self.assertEqual(self.activate(), (self.scope, self.initial))
            self.assertEqual(digest.call_count, 2)

    def test_source_mutation_after_success_refuses_the_next_activation(self):
        self.activate(); self.code.write_bytes(b'<?php // altered')
        with patch.object(h.drain, 'audit_unit') as audit, self.assertRaisesRegex(h.HttpRuntimeError, 'DRIFT'):
            self.activation.configuration()
        audit.assert_not_called()

    def test_collector_worker_mutation_after_success_refuses_the_next_activation(self):
        self.activate(); (self.cleaner.directory / 'worker.py').write_bytes(b'altered')
        with patch.object(h.drain, 'audit_unit') as audit, self.assertRaises(c.SessionCleanerError):
            self.activation.configuration()
        audit.assert_not_called()

    def test_standalone_collector_still_reads_http_and_refuses_source_drift(self):
        with patch.object(h, '_code_digest', wraps=h._code_digest) as digest:
            result = self.cleaner._inspect_configuration()
            self.assertEqual(result[:2], (self.account, self.scope)); self.assertEqual(digest.call_count, 1)
            self.code.chmod(0o666)
            with self.assertRaises(h.HttpRuntimeError): self.cleaner._inspect_configuration()

    def test_combined_activation_refuses_a_collector_bound_to_another_runtime(self):
        from installer.model import InstallerError
        self.cleaner.runtime = h.HttpRuntime(self.runtime.spec)
        with patch.object(h.HttpRuntime, '_inspect_configuration') as inspect, self.assertRaises(InstallerError):
            self.activation.configuration()
        inspect.assert_not_called()

    def test_native_unit_and_timer_rejections_still_propagate(self):
        for target, name in ((h.drain, 'audit_unit'), (self.cleaner, '_timer_state')):
            with self.subTest(name=name), patch.object(h.drain, 'audit_unit'), \
                 patch.object(self.cleaner, '_timer_state'), \
                 patch.object(target, name, side_effect=c.SessionCleanerError('NATIVE_REJECTED')), \
                 self.assertRaisesRegex(c.SessionCleanerError, 'NATIVE_REJECTED'):
                self.activation.configuration()

    def test_drain_scans_source_once_and_rejects_drift_on_its_next_audit(self):
        from installer import http_drain as d, foundation_drain, public_tls_profile
        drain = d.HttpDrain(self.runtime, cleaner=self.cleaner)
        with patch.object(foundation_drain, 'attached', return_value=None), \
             patch.object(drain, '_gateway_binding', return_value=None), \
             patch.object(public_tls_profile, 'overlay_evidence', return_value=None), \
             patch.object(h.drain, 'audit_unit') as audit, patch.object(d, 'identity_census'), \
             patch.object(self.cleaner, '_timer_state') as timer, \
             patch.object(h, '_code_digest', wraps=h._code_digest) as digest:
            scope, profile = drain._audit(stopped=True)
            self.assertIs(scope, self.scope); self.assertEqual(digest.call_count, 1)
            self.assertEqual([call.args[1].role for call in audit.call_args_list], ['apache', 'php', 'session-cleaner'])
            self.assertTrue(all(call.kwargs['stopped'] for call in audit.call_args_list))
            timer.assert_called_once_with(stopped=True)
            self.assertEqual(drain._audit(stopped=True), (scope, profile)); self.assertEqual(digest.call_count, 2)
            self.code.write_bytes(b'changed')
            with self.assertRaises(h.HttpRuntimeError): drain._audit(stopped=True)


if __name__ == '__main__': unittest.main()
