"""Real Ext4, locks and crash recovery in disposable CI only.

The reused fixture isolates native service/Gateway audits. This suite does not
qualify the live SQL admission transition or mobile service reopening.
"""
import os
import pickle
import signal
import unittest
from unittest.mock import patch

from installer import mobile_reopen_external as e
from installer.model import InstallerError
import test_mobile_reopen_files as prior


class ExternalReleaseFilesTests(unittest.TestCase):
    fixture_root = prior.ReopenFilesTests.fixture_root
    mount = prior.ReopenFilesTests.mount
    controller = prior.ReopenFilesTests.controller
    write = staticmethod(prior.ReopenFilesTests.write)
    close_handles = prior.ReopenFilesTests.close_handles

    def setUp(self):
        prior.ReopenFilesTests.setUp(self)
        self.document = self.control.plan(confirmed=True)
        result = self.control.execute('apply', self.document['plan_sha256'], confirmed=True)
        self.assertEqual(result['transaction']['state'], 'DONE')
        self.parents_before = e._parents(self.lease)

    def plan(self, *, close=True):
        self.plan_control = e.begin(self.control, confirmed=True)
        if close: self.stack.close()
        return self.plan_control

    def run_release(self, action='apply', control=None):
        control = control or self.plan_control
        return control.execute(action, control.plan_sha256, confirmed=True)

    def resumed(self):
        return e.recover(self.lease, self.backups, confirmed=True)

    def assert_closed(self):
        self.access.assert_held()
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o700)
        self.assertEqual(e._parents(self.lease), self.parents_before)
        with self.assertRaises(prior.r.hd.m.MaintenanceError): self.lease.resume(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def assert_done(self, result):
        self.assertTrue(result['external_paths_released'])
        for name in ('data_access_reopened', 'activity_resumed', 'admission_verified',
                     'services_started', 'current_sql_admission'): self.assertIs(result[name], False)
        for path in self.paths:
            self.assertFalse(path.exists()); self.assertEqual(list(path.parent.iterdir()), [])
        for name in (e.ef.PREPARE, e.ef.MARKER, e.ef.RELEASE):
            self.assertFalse((self.scope.directory / name).exists())
        self.assert_closed()

    def test_planning_is_idempotent_read_only_and_retains_reservations(self):
        plan = self.plan(close=False)
        before = (plan.root / 'plan.json').stat().st_mtime_ns
        again = e.begin(self.control, confirmed=True)
        self.assertEqual(again.plan_sha256, plan.plan_sha256)
        self.assertEqual((plan.root / 'plan.json').stat().st_mtime_ns, before)
        self.assertEqual({p.name for p in plan.root.iterdir()}, {'plan.json'})
        self.external.assert_held(); self.assert_closed()

    def test_original_shared_configuration_blocks_apply_and_loaded_recovery(self):
        plan = self.plan(close=False)
        for control in (plan, self.resumed()):
            with self.assertRaises(InstallerError): self.run_release(control=control)
        self.assertFalse((plan.root / 'intent.json').exists())
        self.external.assert_held(); self.assert_closed()
        self.stack.close(); self.assert_done(self.run_release())

    def test_explicit_confirmation_precedes_every_effect(self):
        with self.assertRaises(InstallerError): e.begin(self.control, confirmed=False)
        plan = self.plan()
        for confirmation, consent in (('0' * 64, True), (plan.plan_sha256, False)):
            with self.assertRaises(InstallerError): plan.execute('apply', confirmation, confirmed=consent)
        with self.assertRaises(InstallerError): self.run_release('resume')
        with self.assertRaises(InstallerError): self.run_release('check')
        self.assertFalse((plan.root / 'intent.json').exists())
        self.external.assert_held(); self.assert_closed()

    def test_success_preserves_parents_and_check_is_read_only(self):
        plan = self.plan(); self.assert_done(self.run_release())
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in plan.root.iterdir()}
        with patch.object(e.files, '_new', side_effect=AssertionError('unexpected write')):
            self.assert_done(self.run_release('check', self.resumed()))
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in plan.root.iterdir()})
        with self.assertRaises(InstallerError): self.run_release()

    def test_cut_after_outer_intent_before_native_intent_resumes(self):
        plan = self.plan(); save = plan._save
        def cut(name, raw):
            save(name, raw)
            if name == 'intent.json': raise OSError('lost intent response')
        with patch.object(plan, '_save', side_effect=cut), self.assertRaises(OSError): self.run_release()
        self.external.assert_held(); self.assert_closed()
        self.assert_done(self.run_release('resume', self.resumed()))

    def test_cut_after_real_immutable_removal_resumes(self):
        self.plan(); flags = e.ef._flags; changed = []
        def cut(fd, value=None):
            result = flags(fd, value)
            if value is not None and not value & e.source.inf.IMMUTABLE:
                changed.append(value)
                if len(changed) == 1: raise OSError('lost flag response')
            return result
        with patch.object(e.ef, '_flags', side_effect=cut), self.assertRaises(Exception): self.run_release()
        self.assertEqual(len(changed), 1); self.assert_closed()
        self.assert_done(self.run_release('resume', self.resumed()))

    def cut_unlink(self, boundary):
        self.plan(); unlink = os.unlink; hit = []
        def cut(name, *args, **kwargs):
            unlink(name, *args, **kwargs)
            if name == boundary and not hit:
                hit.append(name); raise OSError('lost unlink response')
        with patch.object(e.os, 'unlink', side_effect=cut), self.assertRaises(Exception): self.run_release()
        self.assertEqual(hit, [boundary]); self.assert_closed()
        self.assert_done(self.run_release('resume', self.resumed()))

    def test_cut_after_public_target_unlink_resumes(self):
        self.cut_unlink(self.paths[-1].name)

    def test_cut_after_stage_unlink_resumes(self):
        value = e.ef._decode(self.lease, self.external._raw)
        self.cut_unlink(value['entries'][-1]['stage'])

    def test_cut_after_native_marker_unlink_resumes(self):
        self.cut_unlink(e.ef.MARKER)

    def test_cut_after_last_native_release_unlink_resumes(self):
        self.cut_unlink(e.ef.RELEASE)

    def test_cut_after_receipt_write_is_reconciled_without_native_release(self):
        plan = self.plan(); save = plan._save
        def cut(name, raw):
            save(name, raw)
            if name == 'released.json': raise OSError('lost receipt response')
        with patch.object(plan, '_save', side_effect=cut), self.assertRaises(OSError): self.run_release()
        before = (plan.root / 'released.json').stat().st_mtime_ns
        with patch.object(e.ef, '_release', side_effect=AssertionError('native effect repeated')):
            self.assert_done(self.run_release('resume', self.resumed()))
        self.assertEqual((plan.root / 'released.json').stat().st_mtime_ns, before)

    def test_foreign_target_after_interruption_is_preserved(self):
        plan = self.plan(); save = plan._save
        def cut(name, raw):
            if name == 'released.json': raise OSError('before receipt')
            save(name, raw)
        with patch.object(plan, '_save', side_effect=cut), self.assertRaises(OSError): self.run_release()
        self.paths[0].write_bytes(b'foreign')
        with self.assertRaises(Exception): self.run_release('resume', self.resumed())
        self.assertEqual(self.paths[0].read_bytes(), b'foreign')
        self.assertFalse((plan.root / 'released.json').exists()); self.assert_closed()

    def test_absent_native_journals_without_outer_intent_are_not_success(self):
        plan = self.plan(); self.external.unseal(confirmed=True)
        for action in ('apply', 'resume', 'check'):
            with self.assertRaises(Exception): self.run_release(action)
        self.assertEqual({p.name for p in plan.root.iterdir()}, {'plan.json'}); self.assert_closed()

    def test_changed_original_guard_and_configuration_refuse_without_repair(self):
        plan = self.plan()
        for path in (self.control.root / 'external-original.json', self.scope.directory / e.guard.MARKER,
                     self.config / 'assistant.json'):
            original = path.read_bytes(); path.write_bytes(b'altered')
            with self.assertRaises(Exception): self.run_release()
            self.assertEqual(path.read_bytes(), b'altered')
            self.assertFalse((plan.root / 'intent.json').exists())
            path.write_bytes(original)
        self.external.assert_held(); self.assert_closed()

    def test_changed_data_access_mode_is_refused_without_closing_it(self):
        plan = self.plan(); self.data.chmod(0o750)
        with self.assertRaises(Exception): self.run_release()
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        self.assertFalse((plan.root / 'intent.json').exists())
        self.data.chmod(0o700); self.assert_closed()

    def test_partial_hardlinked_and_unknown_journals_are_not_repaired(self):
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
        self.external.assert_held(); self.assert_closed()

    def test_process_boundary_and_serialization_are_refused(self):
        plan = self.plan()
        with self.assertRaises(TypeError): pickle.dumps(plan)
        with patch.object(e.os, 'getpid', return_value=plan._pid + 1), self.assertRaises(InstallerError):
            self.run_release()
        self.assertFalse((plan.root / 'intent.json').exists())
        self.external.assert_held(); self.assert_closed()

    def test_real_sigkill_after_last_native_journal_reacquires_exact_lease(self):
        plan = self.plan(); lease_id = self.lease.lease_id
        self.access.close(); self.lease.close()
        child = os.fork()
        if child == 0:
            try:
                lease = self.scope.recover(lease_id, confirmed=True)
                child_plan = e.recover(lease, self.backups, confirmed=True); unlink = os.unlink
                def cut(name, *args, **kwargs):
                    unlink(name, *args, **kwargs)
                    if name == e.ef.RELEASE: os.kill(os.getpid(), signal.SIGKILL)
                with patch.object(e.os, 'unlink', side_effect=cut): self.run_release(control=child_plan)
            except BaseException: os._exit(90)
            os._exit(91)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), -signal.SIGKILL)
        self.lease = self.scope.recover(lease_id, confirmed=True)
        self.access = prior.r.da.recover(self.runtime, self.lease, confirmed=True)
        self.assertFalse((self.scope.directory / e.ef.RELEASE).exists())
        self.assertFalse((self.scope.directory / e.ef.MARKER).exists())
        self.assertTrue((plan.root / 'intent.json').exists())
        self.assertFalse((plan.root / 'released.json').exists())
        self.assert_done(self.run_release('resume', self.resumed()))


if __name__ == '__main__': unittest.main()
