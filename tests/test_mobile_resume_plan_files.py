"""Real journals/locks in disposable CI; SQL, runtime and data admission isolated.

The fixture supplies typed parent records only at the already-tested admission
boundary. MaintenanceLease, blocker readers, original copies and writes are real.
This suite does not qualify native SQL composition or any service activation.
"""
from copy import deepcopy
import os
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import mobile_resume_plan as r
from installer.maintenance import MaintenanceError
from installer.model import canonical_bytes
import test_mobile_reopen_guard as prior
from test_mobile_resume_plan_policy import profile


class ResumePlanFilesTests(unittest.TestCase):
    write_source = prior.ReopenGuardTests.write_source

    def setUp(self):
        prior.ReopenGuardTests.setUp(self)
        self.guard = prior.guard.begin(self.lease, plan_sha256=self.plan, confirmed=True)
        self.backups = self.root / 'backups'; self.backups.mkdir(mode=0o700)
        self.native_profile = canonical_bytes(profile())
        self.http = self.scope.directory / ('http-drain-' + self.lease.lease_id + '.attempt')
        self.http.write_bytes(self.native_profile); os.chown(self.http, 0, self.scope.web_gid); self.http.chmod(0o640)
        # Typed test parent records, never fabricated native leases or production readers.
        self.data = object.__new__(r.m.d.DataReleasePlan)
        self.data.lease = self.lease; self.data.plan_sha256 = '3' * 64
        self.data.value = {'external_plan_sha256': '4' * 64, 'parents': {
            'mobile_guard': r.f._sha(self.marker.read_bytes()), 'gateway_release': r.f._sha(self.source.read_bytes())}}
        self.control = object.__new__(r.m._ParentFiles)
        self.control.plan = self.data; self.control.lease = self.lease; self.control.backups = self.backups
        self.control.barrier = SimpleNamespace(_lease=self.lease, _profile=self.native_profile)
        self.control.external = SimpleNamespace(value={'source_plan_sha256': self.plan})
        self.state = self.enterContext(patch.object(r.m, '_state'))
        self.live_check = self.enterContext(patch.object(r.m.DataAdmissionWindow, 'assert_held',
            autospec=True, side_effect=self.live))
        self.window = self.new_window('5')
        self.target = self.backups / ('mobile-resume-' + self.lease.lease_id)
        self.originals = (self.scope.directory / 'maintenance.attempt', self.marker, self.source, self.http)
        self.before = self.original_state()

    def live(self, window):
        if window._closed: raise r.m.AdmissionError('MOBILE_DATA_WINDOW_CLOSED')
        self.lease.assert_held(); self.guard.assert_held()

    def new_window(self, token):
        slot = self.backups / ('data-admission-' + token * 32); slot.mkdir(mode=0o700)
        report = {'observation_id': slot.name, 'data_plan_sha256': self.data.plan_sha256,
            'data_access_reopened': True, 'valid_after_window_close': False, 'historical_observation_only': True}
        path = slot / 'observed.json'; path.write_bytes(r.m.p._json(report)); path.chmod(0o600)
        return r.m.DataAdmissionWindow(self.control, Mock(), Mock(), Mock(), Mock(), Mock(), slot, report)

    def original_state(self):
        return [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_mode,
                 p.stat().st_uid, p.stat().st_gid) for p in self.originals]

    def begin(self): return r.begin(self.window, confirmed=True)
    def recover(self): return r.recover(self.window, confirmed=True)
    def check(self, plan): return plan.check(self.window, plan.plan_sha256, confirmed=True)
    def prepare(self, plan): return plan.prepare(self.window, plan.plan_sha256, confirmed=True)

    def closed_activity(self):
        self.assertEqual(self.original_state(), self.before)
        with self.assertRaises(MaintenanceError): self.lease.resume(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def files_state(self):
        return {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in self.target.iterdir()}

    def partial(self, stop_after):
        original = r.files._new
        def interrupted(fd, name, raw):
            original(fd, name, raw)
            if name == stop_after: raise OSError('lost response')
        with patch.object(r.files, '_new', side_effect=interrupted), self.assertRaises(r.ResumePlanError): self.begin()

    def test_exact_originals_copied_private_with_native_metadata_preserved(self):
        plan = self.begin(); report = self.check(plan)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        for name, source in zip(r.COPIES, self.originals):
            copy = self.target / name; self.assertEqual(copy.read_bytes(), source.read_bytes())
            self.assertEqual((copy.stat().st_uid, copy.stat().st_gid, copy.stat().st_mode & 0o777), (0, 0, 0o600))
            meta = plan.value['originals'][name]
            self.assertEqual((meta['gid'], meta['mode']), (source.stat().st_gid, source.stat().st_mode & 0o777))
        self.assertEqual(plan.value['future_blocker_order'], [prior.guard.MARKER, prior.release.RELEASED])
        self.assertFalse(report['services_started']); self.state.assert_called_with(self.data, completed=True)
        self.closed_activity()

    def test_repeat_begin_check_and_recover_do_not_rewrite(self):
        plan = self.begin(); before = self.files_state()
        with patch.object(r.files, '_new', side_effect=AssertionError('rewrite')):
            self.check(plan); self.prepare(plan); self.begin(); self.recover()
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def test_new_live_window_adds_only_historical_observation(self):
        plan = self.begin(); before = self.files_state(); self.window._closed = True
        self.window = self.new_window('6'); recovered = self.recover()
        self.assertEqual(before, self.files_state())
        self.prepare(recovered); after = self.files_state()
        self.assertEqual({k: after[k] for k in before}, before)
        self.assertEqual(len(after), len(before) + 1); self.assertEqual(plan.plan_sha256, recovered.plan_sha256)
        self.closed_activity()

    def test_closed_window_refuses_even_with_completed_historical_receipt(self):
        plan = self.begin(); before = self.files_state(); self.window._closed = True
        for operation in (self.begin, self.recover, lambda: self.check(plan), lambda: self.prepare(plan)):
            with self.assertRaises(r.ResumePlanError): operation()
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def test_missing_slot_recovery_is_read_only(self):
        with self.assertRaises(r.ResumePlanError): self.recover()
        self.assertFalse(self.target.exists()); self.closed_activity()

    def test_partial_preparation_recovery_needs_explicit_prepare(self):
        self.partial(r.COPIES[0]); before = self.files_state(); recovered = self.recover()
        self.assertEqual(before, self.files_state())
        with self.assertRaisesRegex(r.ResumePlanError, 'INCOMPLETE'): self.check(recovered)
        self.prepare(recovered); after = self.files_state()
        self.assertEqual({k: after[k] for k in before}, before); self.check(recovered); self.closed_activity()

    def test_lost_response_after_prepared_receipt_recovers_without_write(self):
        self.partial('prepared.json'); before = self.files_state()
        with patch.object(r.files, '_new', side_effect=AssertionError('rewrite')): self.check(self.recover())
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def test_truncated_copy_is_not_repaired_or_adopted(self):
        self.partial('plan.json'); path = self.target / r.COPIES[0]
        path.write_bytes(b'{'); path.chmod(0o600)
        before = self.files_state()
        for operation in (self.begin, self.recover):
            with self.assertRaises(r.ResumePlanError): operation()
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def test_empty_directory_and_partial_plan_are_manual(self):
        self.target.mkdir(mode=0o700)
        with self.assertRaises(r.ResumePlanError): self.begin()
        path = self.target / 'plan.json'; path.write_bytes(b'{'); path.chmod(0o600)
        for operation in (self.begin, self.recover):
            with self.assertRaises(r.ResumePlanError): operation()
        self.assertEqual(path.read_bytes(), b'{'); self.closed_activity()

    def test_prepared_receipt_with_missing_copy_is_not_repaired(self):
        self.begin(); (self.target / r.COPIES[0]).unlink(); before = self.files_state()
        for operation in (self.begin, self.recover):
            with self.assertRaisesRegex(r.ResumePlanError, 'INCOMPLETE'): operation()
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def test_prepared_receipt_without_observation_is_incomplete(self):
        self.begin()
        for path in self.target.glob('admission-*.json'): path.unlink()
        with self.assertRaisesRegex(r.ResumePlanError, 'INCOMPLETE'): self.recover()
        self.closed_activity()

    def test_foreign_record_is_refused_without_cleanup(self):
        self.begin(); path = self.target / 'unexpected'; path.write_bytes(b'foreign'); path.chmod(0o600)
        with self.assertRaisesRegex(r.ResumePlanError, 'FOREIGN_RECORD'): self.recover()
        self.assertEqual(path.read_bytes(), b'foreign'); self.closed_activity()

    def test_symlink_hardlink_and_wrong_mode_copies_are_refused(self):
        self.begin(); path = self.target / r.COPIES[0]; raw = path.read_bytes()
        path.chmod(0o640)
        with self.assertRaises(r.ResumePlanError): self.recover()
        path.chmod(0o600); other = self.root / 'alias'; os.link(path, other)
        with self.assertRaises(r.ResumePlanError): self.recover()
        other.unlink(); path.rename(other); path.symlink_to(other)
        with self.assertRaises(r.ResumePlanError): self.recover()
        self.assertEqual(other.read_bytes(), raw); self.closed_activity()

    def test_backup_directory_permissions_or_links_are_refused(self):
        self.begin(); self.target.chmod(0o750)
        with self.assertRaises(r.ResumePlanError): self.recover()
        self.target.chmod(0o700); other = self.backups / 'renamed'; self.target.rename(other); self.target.symlink_to(other)
        with self.assertRaises(r.ResumePlanError): self.recover()
        self.closed_activity()

    def test_current_observation_bytes_and_metadata_are_native_exact(self):
        path = self.window._slot / 'observed.json'; raw = path.read_bytes()
        path.write_bytes(raw + b'\n')
        with self.assertRaises(r.ResumePlanError): self.begin()
        path.write_bytes(raw); path.chmod(0o640)
        with self.assertRaises(r.ResumePlanError): self.begin()
        self.assertFalse(self.target.exists()); self.closed_activity()

    def test_historical_observation_still_matches_original_native_source(self):
        self.begin(); old = self.window._slot / 'observed.json'
        self.window = self.new_window('6'); old.write_bytes(b'{}')
        with self.assertRaisesRegex(r.ResumePlanError, 'OBSERVATION_CHANGED'): self.recover()
        self.closed_activity()

    def test_canonical_but_forged_historical_observation_is_refused(self):
        plan = self.begin(); path = next(self.target.glob('admission-*.json'))
        report = deepcopy(self.window._report); report['extra'] = 'not native'
        path.write_bytes(plan._observation(report))
        with self.assertRaisesRegex(r.ResumePlanError, 'OBSERVATION_CHANGED'): self.recover()
        self.closed_activity()

    def test_changed_native_http_profile_is_not_recaptured(self):
        self.begin(); self.http.write_bytes(b'{}')
        with self.assertRaises(r.ResumePlanError): self.recover()
        self.http.write_bytes(self.native_profile)
        with self.assertRaises(MaintenanceError): self.lease.resume(confirmed=True)

    def test_changed_parent_digest_refuses_existing_plan(self):
        self.begin(); before = self.files_state(); self.data.value['external_plan_sha256'] = '7' * 64
        for operation in (self.begin, self.recover):
            with self.assertRaises(r.ResumePlanError): operation()
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def test_failed_current_data_admission_precedes_creation(self):
        self.state.side_effect = r.m.AdmissionError('MOBILE_DATA_RECEIPT_REQUIRED')
        with self.assertRaises(r.ResumePlanError): self.begin()
        self.assertFalse(self.target.exists()); self.closed_activity()

    def test_late_revocation_can_leave_only_a_historical_preparation(self):
        self.live_check.side_effect = [None, r.m.AdmissionError('SQL_FENCE_TIMEOUT')]
        with self.assertRaises(r.ResumePlanError): self.begin()
        receipt = r.strict_json_loads((self.target / 'prepared.json').read_bytes())
        self.assertFalse(receipt['current_admission']); self.assertFalse(receipt['services_started'])
        self.live_check.side_effect = self.live; self.window._closed = True
        with self.assertRaises(r.ResumePlanError): self.recover()
        self.closed_activity()

    def test_observation_count_is_bounded_without_rewriting_prior_records(self):
        self.begin(); self.window = self.new_window('6'); before = self.files_state()
        with patch.object(r, 'MAX_OBSERVATIONS', 1), self.assertRaisesRegex(r.ResumePlanError, 'OBSERVATIONS_LIMIT'):
            self.begin()
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def test_wrong_confirmation_never_writes_after_partial_preparation(self):
        self.partial('plan.json'); recovered = self.recover(); before = self.files_state()
        with self.assertRaises(r.ResumePlanError): recovered.prepare(self.window, 'wrong', confirmed=True)
        self.assertEqual(before, self.files_state()); self.closed_activity()

    def killed_writer(self, stop_after):
        lease_id = self.lease.lease_id; self.lease.close()
        def reattach():
            self.lease = self.scope.recover(lease_id, confirmed=True)
            self.data.lease = self.lease; self.control.lease = self.lease
            self.control.barrier._lease = self.lease
            self.guard = prior.guard.recover(self.lease, plan_sha256=self.plan, confirmed=True)
        child = os.fork()
        if child == 0:
            signal.alarm(15)
            try:
                reattach(); self.window = self.new_window('7'); original = r.files._new
                def killed(fd, name, raw):
                    original(fd, name, raw)
                    if name == stop_after: os.kill(os.getpid(), signal.SIGKILL)
                with patch.object(r.files, '_new', side_effect=killed): self.begin()
            finally: os._exit(3)
        _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFSIGNALED(status)); self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        reattach(); self.window._closed = True; self.window = self.new_window('6')
        before = self.files_state(); recovered = self.recover()
        self.assertEqual(before, self.files_state())
        if stop_after == 'prepared.json': self.check(recovered)
        else:
            with self.assertRaises(r.ResumePlanError): self.check(recovered)
        self.prepare(recovered); self.check(recovered)
        self.assertEqual({k: self.files_state()[k] for k in before}, before); self.closed_activity()

    def test_sigkill_after_first_copy_resumes_under_recovered_real_lease(self):
        self.killed_writer(r.COPIES[0])

    def test_sigkill_after_prepared_receipt_does_not_rewrite_history(self):
        self.killed_writer('prepared.json')

    def test_closed_real_maintenance_lease_revokes_preparation(self):
        self.begin(); before = self.files_state(); self.lease.close()
        for operation in (self.begin, self.recover):
            with self.assertRaises(r.ResumePlanError): operation()
        self.assertEqual(before, self.files_state())


if __name__ == '__main__': unittest.main()
