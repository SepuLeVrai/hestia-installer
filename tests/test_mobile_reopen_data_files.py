"""Real permissions, flock, process census and SIGKILL in disposable Ext4 CI.

The reused fixture isolates service/Gateway audits. No SQL composition or start
is qualified by this filesystem suite; historical tests remain unmodified.
"""
import os
import pickle
import signal
import subprocess
import unittest
from unittest.mock import patch

from installer import mobile_reopen_data as d
from installer.model import InstallerError
import test_mobile_reopen_external_files as prior


class DataReleaseFilesTests(unittest.TestCase):
    fixture_root = prior.ExternalReleaseFilesTests.fixture_root
    mount = prior.ExternalReleaseFilesTests.mount
    controller = prior.ExternalReleaseFilesTests.controller
    write = staticmethod(prior.ExternalReleaseFilesTests.write)
    close_handles = prior.ExternalReleaseFilesTests.close_handles

    def setUp(self):
        prior.ExternalReleaseFilesTests.setUp(self)
        self.external_plan = prior.ExternalReleaseFilesTests.plan(self)
        self.external_plan.execute('apply', self.external_plan.plan_sha256, confirmed=True)
        def strict_inspect():
            self.assertEqual(self.data.stat().st_mode & 0o777, d.da.expected_mode(self.runtime, self.account))
            return self.account, None, None, None
        self.runtime._inspect_configuration.side_effect = strict_inspect

    def plan(self):
        self.plan_control = d.begin(self.external_plan, self.access, confirmed=True)
        return self.plan_control

    def run_release(self, action='apply', control=None):
        control = control or self.plan_control
        return control.execute(action, control.plan_sha256, confirmed=True)

    def resumed(self):
        return d.recover(self.runtime, self.lease, self.backups, confirmed=True)

    def assert_activity_closed(self):
        self.assertEqual(d.e._parents(self.lease), self.parents_before)
        with self.assertRaises(d.da.m.MaintenanceError): self.lease.resume(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def assert_done(self, result):
        self.assertEqual(result['state'], 'DATA_ACCESS_REOPENED_ACTIVITY_CLOSED')
        self.assertIs(result['data_access_reopened'], True)
        self.assertIs(result['external_paths_released'], True)
        for name in ('activity_resumed', 'services_started', 'admission_verified', 'current_sql_admission'):
            self.assertIs(result[name], False)
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        self.assertFalse((self.scope.directory / d.da.MARKER).exists())
        self.assert_activity_closed()

    def test_planning_is_idempotent_and_keeps_data_closed(self):
        plan = self.plan(); before = (plan.root / 'plan.json').stat().st_mtime_ns
        again = d.begin(self.external_plan, self.access, confirmed=True)
        self.assertEqual(again.plan_sha256, plan.plan_sha256)
        self.assertEqual((plan.root / 'plan.json').stat().st_mtime_ns, before)
        self.assertEqual({p.name for p in plan.root.iterdir()}, {'plan.json'})
        self.access.assert_held(); self.assert_activity_closed()

    def test_consent_and_missing_intent_or_receipt_refuse_before_effect(self):
        plan = self.plan()
        for confirmation, consent in (('0' * 64, True), (plan.plan_sha256, False)):
            with self.assertRaises(InstallerError): plan.execute('apply', confirmation, confirmed=consent)
        for action in ('resume', 'check'):
            with self.assertRaises(InstallerError): self.run_release(action)
        self.assertFalse((plan.root / 'intent.json').exists()); self.access.assert_held()

    def test_success_preserves_parents_and_read_only_check(self):
        plan = self.plan(); self.assert_done(self.run_release())
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in plan.root.iterdir()}
        with patch.object(d.files, '_new', side_effect=AssertionError('unexpected write')), \
                patch.object(d.da, 'recover', side_effect=AssertionError('unexpected closure')):
            self.assert_done(self.run_release('check', self.resumed()))
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in plan.root.iterdir()})
        with self.assertRaises(InstallerError): self.run_release()

    def test_cut_after_intent_resumes_without_losing_original_marker(self):
        plan = self.plan(); save = plan._save
        def cut(name, raw):
            save(name, raw)
            if name == 'intent.json': raise OSError('lost intent response')
        with patch.object(plan, '_save', side_effect=cut), self.assertRaises(OSError): self.run_release()
        self.access.assert_held(); self.assert_done(self.run_release('resume', self.resumed()))

    def interrupt_after_chmod(self):
        self.plan(); chmod = os.fchmod; hit = []
        def cut(fd, mode):
            chmod(fd, mode)
            if mode == 0o750 and not hit:
                hit.append(mode); raise OSError('lost chmod response')
        with patch.object(d.da.os, 'fchmod', side_effect=cut), self.assertRaises(Exception): self.run_release()
        self.assertEqual(hit, [0o750])
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        self.assertTrue((self.scope.directory / d.da.MARKER).exists())
        self.assertFalse((self.plan_control.root / 'released.json').exists())

    def test_partial_chmod_keeps_strict_reader_and_resume_recloses_explicitly(self):
        self.interrupt_after_chmod()
        with self.assertRaisesRegex(d.da.DataAccessError, 'DATA_ACCESS_INCOMPLETE'):
            self.runtime._inspect_configuration()
        recovered = self.resumed()  # Loading is read-only, not implicit repair.
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        modes = []; chmod = os.fchmod
        def observe(fd, mode): modes.append(mode); return chmod(fd, mode)
        with patch.object(d.da.os, 'fchmod', side_effect=observe):
            self.assert_done(self.run_release('resume', recovered))
        self.assertEqual(modes, [0o700, 0o750])

    def interrupt_after_unlink(self):
        self.plan(); unlink = os.unlink; hit = []
        def cut(name, *args, **kwargs):
            unlink(name, *args, **kwargs)
            if name == d.da.MARKER:
                hit.append(name); raise OSError('lost unlink response')
        with patch.object(d.da.os, 'unlink', side_effect=cut), self.assertRaises(Exception): self.run_release()
        self.assertEqual(hit, [d.da.MARKER])

    def test_cut_after_marker_unlink_resumes_without_reclosing_or_repeating(self):
        self.interrupt_after_unlink()
        with patch.object(d.da, 'recover', side_effect=AssertionError('native effect repeated')):
            self.assert_done(self.run_release('resume', self.resumed()))

    def test_receipt_response_loss_is_reconciled_without_native_effect(self):
        plan = self.plan(); save = plan._save
        def cut(name, raw):
            save(name, raw)
            if name == 'released.json': raise OSError('lost receipt response')
        with patch.object(plan, '_save', side_effect=cut), self.assertRaises(OSError): self.run_release()
        before = (plan.root / 'released.json').stat().st_mtime_ns
        with patch.object(d.da, 'recover', side_effect=AssertionError('native effect repeated')):
            self.assert_done(self.run_release('resume', self.resumed()))
        self.assertEqual((plan.root / 'released.json').stat().st_mtime_ns, before)

    def test_low_level_reopen_without_outer_intent_is_never_success(self):
        plan = self.plan(); self.access.reopen(confirmed=True)
        for action in ('apply', 'resume', 'check'):
            with self.assertRaises(Exception): self.run_release(action)
        self.assertEqual({p.name for p in plan.root.iterdir()}, {'plan.json'})
        self.assert_activity_closed()

    def test_partial_mode_without_intent_is_not_repaired(self):
        plan = self.plan(); self.data.chmod(0o750)
        for action in ('apply', 'resume', 'check'):
            with self.assertRaises(Exception): self.run_release(action, self.resumed())
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        self.assertFalse((plan.root / 'intent.json').exists())
        self.data.chmod(0o700)

    def test_missing_marker_with_closed_data_is_not_recreated(self):
        self.plan(); marker = self.scope.directory / d.da.MARKER; marker.unlink()
        with self.assertRaises(Exception): self.run_release()
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o700); self.assertFalse(marker.exists())

    def test_changed_parents_receipts_and_configuration_refuse_without_repair(self):
        plan = self.plan()
        for path in (self.control.root / 'data-access-original.json', self.scope.directory / d.e.guard.MARKER,
                     self.external_plan.root / 'released.json', self.external_plan.root / 'intent.json',
                     self.config / 'assistant.json'):
            raw = path.read_bytes(); path.write_bytes(b'foreign')
            with self.assertRaises(Exception): self.run_release()
            self.assertEqual(path.read_bytes(), b'foreign')
            self.assertFalse((plan.root / 'intent.json').exists()); path.write_bytes(raw)
        self.access.assert_held()

    def test_changed_data_inode_or_mode_is_not_repaired(self):
        self.plan(); original = self.http / 'original-data'; self.data.rename(original)
        self.data.mkdir(mode=0o700); os.chown(self.data, 0, self.account.pw_gid)
        with self.assertRaises(Exception): self.run_release()
        self.assertEqual(list(self.data.iterdir()), [])
        self.data.rmdir(); original.rename(self.data)
        self.data.chmod(0o755)
        with self.assertRaises(Exception): self.run_release()
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o755); self.data.chmod(0o700)

    def test_partial_linked_and_unknown_journals_are_preserved(self):
        plan = self.plan(); path = plan.root / 'plan.json'; raw = path.read_bytes()
        for bad in (b'', b'{}', raw[:100]):
            self.write(path, bad)
            with self.assertRaises(Exception): self.resumed()
            self.assertEqual(path.read_bytes(), bad)
        self.write(path, raw)
        alias = self.root / 'plan-alias'; os.link(path, alias)
        with self.assertRaises(Exception): self.run_release()
        alias.unlink()
        unknown = plan.root / 'unknown.json'; self.write(unknown, b'{}')
        with self.assertRaises(Exception): self.run_release()
        self.assertEqual(unknown.read_bytes(), b'{}'); unknown.unlink()
        for name in ('intent.json', 'released.json'):
            bad = plan.root / name; self.write(bad, b'{}')
            with self.assertRaises(Exception): self.run_release('resume')
            self.assertEqual(bad.read_bytes(), b'{}'); bad.unlink()
        self.access.assert_held()

    def test_exclusive_configuration_lock_is_required(self):
        plan = self.plan()
        with d.fs._directory(self.config) as fd, d.source.cf.admission.acquire(
                fd, self.web, self.account.pw_gid):
            with self.assertRaises(InstallerError): self.run_release()
        self.assertFalse((plan.root / 'intent.json').exists()); self.access.assert_held()
        self.assert_done(self.run_release())

    def test_process_boundary_and_serialization_are_refused(self):
        plan = self.plan()
        with self.assertRaises(TypeError): pickle.dumps(plan)
        with patch.object(d.os, 'getpid', return_value=plan._pid + 1), self.assertRaises(InstallerError):
            self.run_release()
        self.assertFalse((plan.root / 'intent.json').exists()); self.access.assert_held()

    def foreign_process(self):
        proc = subprocess.Popen(['/usr/bin/python3', '-c',
            'import sys;print("ready",flush=True);sys.stdin.readline()'], cwd='/',
            user=19001, group=19001, extra_groups=[], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        def cleanup():
            proc.stdin.close(); proc.wait(timeout=5); proc.stdout.close()
        self.addCleanup(cleanup); self.assertEqual(proc.stdout.readline(), b'ready\n')
        return proc

    def test_foreign_process_refuses_before_reopen_without_signalling(self):
        plan = self.plan(); proc = self.foreign_process()
        with self.assertRaises(Exception): self.run_release()
        self.assertIsNone(proc.poll()); self.access.assert_held()
        self.assertFalse((plan.root / 'released.json').exists())

    def test_partial_chmod_recovery_recloses_and_refuses_foreign_process(self):
        self.interrupt_after_chmod(); proc = self.foreign_process()
        with self.assertRaises(Exception): self.run_release('resume', self.resumed())
        self.assertIsNone(proc.poll()); self.assertEqual(self.data.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.scope.directory / d.da.MARKER).exists())
        self.assertFalse((self.plan_control.root / 'released.json').exists())

    def test_completed_effect_with_foreign_process_never_writes_receipt_or_recloses(self):
        self.interrupt_after_unlink(); proc = self.foreign_process()
        with self.assertRaises(Exception): self.run_release('resume', self.resumed())
        self.assertIsNone(proc.poll()); self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        self.assertFalse((self.scope.directory / d.da.MARKER).exists())
        self.assertFalse((self.plan_control.root / 'released.json').exists())
        self.assert_activity_closed()

    def killed_recovery(self, boundary):
        plan = self.plan(); lease_id = self.lease.lease_id
        self.access.close(); self.lease.close()
        child = os.fork()
        if child == 0:
            try:
                lease = self.scope.recover(lease_id, confirmed=True)
                child_plan = d.recover(self.runtime, lease, self.backups, confirmed=True)
                chmod, unlink = os.fchmod, os.unlink
                def cut_chmod(fd, mode):
                    chmod(fd, mode)
                    if boundary == 'chmod' and mode == 0o750: os.kill(os.getpid(), signal.SIGKILL)
                def cut_unlink(name, *args, **kwargs):
                    unlink(name, *args, **kwargs)
                    if boundary == 'unlink' and name == d.da.MARKER: os.kill(os.getpid(), signal.SIGKILL)
                with patch.object(d.os, 'fchmod', side_effect=cut_chmod), patch.object(d.os, 'unlink', side_effect=cut_unlink):
                    self.run_release(control=child_plan)
            except BaseException: os._exit(90)
            os._exit(91)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), -signal.SIGKILL)
        self.lease = self.scope.recover(lease_id, confirmed=True)
        self.assertTrue((plan.root / 'intent.json').exists())
        self.assertFalse((plan.root / 'released.json').exists())
        self.assert_done(self.run_release('resume', self.resumed()))

    def test_real_sigkill_after_chmod_reacquires_exact_lease(self):
        self.killed_recovery('chmod')

    def test_real_sigkill_after_unlink_reacquires_exact_lease(self):
        self.killed_recovery('unlink')


if __name__ == '__main__': unittest.main()
