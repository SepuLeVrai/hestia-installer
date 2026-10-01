"""Real parent journals, Ext4, locks and interruption in disposable CI only.

SQL and service readers remain isolated like the reused data fixture. These
contracts certify file transitions, not native SQL admission or service starts.
"""
import os
import pickle
import signal
import stat
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import mobile_blocker_admission as n
from installer.model import canonical_bytes
import test_mobile_reopen_data_files as prior

s, p = n.s, n.s.p


class BlockerFilesTests(unittest.TestCase):
    fixture_root = prior.DataReleaseFilesTests.fixture_root
    mount = prior.DataReleaseFilesTests.mount
    controller = prior.DataReleaseFilesTests.controller
    write = staticmethod(prior.DataReleaseFilesTests.write)
    close_handles = prior.DataReleaseFilesTests.close_handles

    def setUp(self):
        prior.DataReleaseFilesTests.setUp(self)
        data = prior.DataReleaseFilesTests.plan(self)
        data.execute('apply', data.plan_sha256, confirmed=True)
        raw = self.barrier._profile
        path = self.scope.directory / ('http-drain-' + self.lease.lease_id + '.attempt')
        path.write_bytes(raw); os.chown(path, 0, self.scope.web_gid); path.chmod(0o640)
        # The old fixture has a minimal service profile. Its native service
        # identity validator is isolated here; pure and native suites cover it.
        self.enterContext(patch.object(p, '_services', return_value=[]))
        view = n.m._ParentFiles(data, self.barrier, self.gateway)
        slot = self.backups / ('data-admission-' + 'f'*32); slot.mkdir(mode=0o700)
        report = {'observation_id': slot.name, 'data_plan_sha256': data.plan_sha256,
            'data_access_reopened': True, 'valid_after_window_close': False}
        self.write(slot / 'observed.json', n.p._json(report))
        window = n.m.DataAdmissionWindow(view, Mock(), Mock(), Mock(), Mock(), Mock(), slot, report)
        with patch.object(n.m.DataAdmissionWindow, 'assert_held', side_effect=self.lease.assert_held):
            resume = p.begin(window, confirmed=True)
        self.confirmation = resume.plan_sha256
        self.resume_root = resume.root
        self.state = self.loaded()
        self.saved = {path: path.read_bytes() for path in self.resume_root.iterdir()}
        self.before_gate = (self.scope.directory / 'maintenance.attempt').read_bytes()

    def loaded(self): return s.BlockerState(self.runtime, self.lease, self.backups, self.confirmation)

    def window(self, state=None):
        state = state or self.state
        control = SimpleNamespace(state=state)
        return n.BlockerWindow(control, Mock(spec=['assert_held']), Mock(spec=['assert_held']),
            Mock(spec=['assert_held']), None, None, self.backups / 'unused', {})

    def execute(self, action='apply', state=None):
        state = state or self.state
        with state.external._configuration() as locked:
            window = self.window(state); window._locked = locked
            return state._execute(action, window)

    def closed_activity(self):
        self.assertEqual((self.scope.directory / 'maintenance.attempt').read_bytes(), self.before_gate)
        with self.assertRaises(n.r.hd.m.MaintenanceError): self.lease.resume(confirmed=True)
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)

    def assert_done(self, result):
        self.assertTrue(result['old_blockers_removed']); self.assertTrue(result['activation_blocker_kept'])
        self.assertFalse(result['services_started']); self.assertFalse(result['maintenance_released'])
        self.assertFalse(result['current_sql_admission'])
        for name in s.OLD: self.assertFalse((self.scope.directory / name).exists())
        marker = self.scope.directory / s.MARKER
        self.assertEqual(marker.read_bytes(), self.state.activation())
        self.assertEqual((marker.stat().st_uid, marker.stat().st_gid, stat.S_IMODE(marker.stat().st_mode)), (0, 0, 0o600))
        self.closed_activity()

    def test_parent_attachment_and_report_are_read_only(self):
        self.assertFalse(self.state.root.exists())
        self.assertEqual(self.loaded().report()['state'], 'BLOCKER_TRANSITION_PENDING')
        self.assertFalse(self.state.root.exists()); self.closed_activity()

    def test_complete_handoff_keeps_activation_blocker_and_exact_parents(self):
        self.assert_done(self.execute())
        self.assertEqual(set(self.state.root.iterdir()), {self.state.root/name for name in ('intent.json', 'done.json', *s.RECEIPTS)})

    def test_completed_check_and_resume_never_rewrite_or_unlink(self):
        self.assert_done(self.execute()); before = {x.name: (x.read_bytes(), x.stat().st_mtime_ns) for x in self.state.root.iterdir()}
        with patch.object(s.files, '_new', side_effect=AssertionError('rewrite')), patch.object(s.os, 'unlink', side_effect=AssertionError('unlink replay')):
            self.assert_done(self.execute('check', self.loaded()))
            self.assert_done(self.execute('resume', self.loaded()))
        self.assertEqual(before, {x.name: (x.read_bytes(), x.stat().st_mtime_ns) for x in self.state.root.iterdir()})

    def test_activation_is_durable_before_either_original_is_removed(self):
        unlink = os.unlink; events = []
        def observed(name, **kwargs):
            if name in s.OLD:
                self.assertEqual((self.scope.directory / s.MARKER).read_bytes(), self.state.activation())
                events.append(name)
            return unlink(name, **kwargs)
        with patch.object(s.os, 'unlink', side_effect=observed): self.assert_done(self.execute())
        self.assertEqual(events, list(s.OLD))

    def cut_write(self, name):
        writer = s.files._new
        def cut(fd, target, raw):
            writer(fd, target, raw)
            if target == name: raise OSError('lost response')
        with patch.object(s.files, '_new', side_effect=cut), self.assertRaises(s.BlockerError): self.execute()
        self.closed_activity(); self.assert_done(self.execute('resume', self.loaded()))

    def test_lost_reply_after_intent_is_recovered(self): self.cut_write('intent.json')
    def test_lost_reply_after_activation_blocker_is_recovered(self): self.cut_write(s.MARKER)
    def test_lost_reply_after_first_receipt_is_recovered(self): self.cut_write(s.RECEIPTS[0])
    def test_lost_reply_after_second_receipt_is_recovered(self): self.cut_write(s.RECEIPTS[1])
    def test_lost_reply_after_completion_receipt_is_recovered(self): self.cut_write('done.json')

    def cut_unlink(self, name):
        unlink = os.unlink
        def cut(target, **kwargs):
            unlink(target, **kwargs)
            if target == name: raise OSError('lost response')
        with patch.object(s.os, 'unlink', side_effect=cut), self.assertRaises(s.BlockerError): self.execute()
        self.closed_activity(); self.assert_done(self.execute('resume', self.loaded()))

    def test_lost_reply_after_mobile_unlink_is_recovered(self): self.cut_unlink(s.OLD[0])
    def test_lost_reply_after_gateway_unlink_is_recovered(self): self.cut_unlink(s.OLD[1])

    def test_missing_old_marker_without_owned_intent_is_rejected(self):
        for name in s.OLD:
            path = self.scope.directory/name; raw = path.read_bytes(); path.unlink()
            with self.assertRaises(s.BlockerError): self.loaded()
            self.write(path, raw)
        self.assertFalse(self.state.root.exists()); self.closed_activity()

    def test_gateway_cannot_disappear_before_mobile_receipt(self):
        self.state._save('intent.json', self.state.owner())
        self.write(self.scope.directory/s.MARKER, self.state.activation())
        for name in s.OLD: (self.scope.directory/name).unlink()
        with self.assertRaisesRegex(s.BlockerError, 'ORDER_CHANGED'): self.loaded()
        self.closed_activity()

    def test_missing_replacement_after_first_unlink_is_not_recreated(self):
        self.assert_done(self.execute()); (self.scope.directory/s.MARKER).unlink()
        with self.assertRaisesRegex(s.BlockerError, 'ACTIVATION_BLOCKER_REQUIRED'): self.loaded()
        self.assertFalse((self.scope.directory/s.MARKER).exists())
        # Restore only fixture data before checking the low-level maintenance gate.
        self.write(self.scope.directory/s.MARKER, self.state.activation()); self.closed_activity()

    def test_reappearing_old_marker_is_foreign_and_never_deleted(self):
        self.assert_done(self.execute()); path = self.scope.directory/s.OLD[0]
        self.write(path, self.state.originals[p.COPIES[1]])
        with self.assertRaises(s.BlockerError): self.loaded()
        self.assertTrue(path.exists()); self.closed_activity()

    def test_partial_or_foreign_replacement_is_not_repaired(self):
        self.state._save('intent.json', self.state.owner()); path = self.scope.directory/s.MARKER
        for raw in (b'', b'{', b'{}', b'foreign'):
            self.write(path, raw)
            with self.assertRaises(s.BlockerError): self.loaded()
            self.assertEqual(path.read_bytes(), raw)
        self.closed_activity()

    def test_links_wrong_modes_and_foreign_transition_records_are_refused(self):
        self.assert_done(self.execute()); path = self.state.root/'done.json'; raw = path.read_bytes()
        path.chmod(0o640)
        with self.assertRaises(s.BlockerError): self.loaded()
        path.chmod(0o600); alias = self.root/'alias'; os.link(path, alias)
        with self.assertRaises(s.BlockerError): self.loaded()
        alias.unlink(); path.rename(alias); path.symlink_to(alias)
        with self.assertRaises(s.BlockerError): self.loaded()
        path.unlink(); alias.rename(path)
        self.write(self.state.root/'unknown.json', b'{}')
        with self.assertRaises(s.BlockerError): self.loaded()
        self.closed_activity()

    def test_changed_parents_copies_receipts_and_observations_are_refused(self):
        for path in (self.resume_root/p.COPIES[1], self.resume_root/'prepared.json',
                     self.external_plan.root/'released.json', self.plan_control.root/'released.json',
                     self.control.root/'profile.json', next(self.resume_root.glob('admission-*.json'))):
            raw = path.read_bytes(); self.write(path, b'{}')
            with self.assertRaises(Exception): self.loaded()
            self.assertEqual(path.read_bytes(), b'{}'); self.write(path, raw)
        self.closed_activity()

    def test_conflicting_configuration_reader_blocks_before_intent(self):
        with n.fs._directory(self.config) as fd, n.r.cf.admission.acquire(fd, self.web, self.account.pw_gid):
            with self.assertRaises(Exception): self.execute()
        self.assertFalse(self.state.root.exists()); self.closed_activity()

    def test_closed_foreign_or_lost_sql_window_cannot_remove_markers(self):
        for mode in ('closed', 'foreign', 'fence'):
            w = self.window()
            if mode == 'closed': w._closed = True
            elif mode == 'foreign': w._control.state = object()
            else: w._fence.assert_held.side_effect = s.BlockerError('SQL_FENCE_TIMEOUT')
            with self.assertRaises(s.BlockerError): self.state._execute('apply', w)
        self.assertFalse(self.state.root.exists()); self.closed_activity()

    def test_lease_fork_and_serialization_boundaries_are_preserved(self):
        with self.assertRaises(TypeError): pickle.dumps(self.state)
        with patch.object(s.os, 'getpid', return_value=self.state._pid+1), self.assertRaises(s.BlockerError): self.state.report()
        self.assertFalse(self.state.root.exists()); self.closed_activity()

    def test_native_files_stay_exact_after_both_old_markers_are_consumed(self):
        self.assert_done(self.execute())
        state = self.loaded(); control = n._ParentFiles(state, self.barrier, self.gateway)
        with state.external._configuration() as locked, patch.object(control, '_gateway', return_value=s._identity(self.released)):
            control.engine(); control.live(locked=locked)
            path = self.data / 'uploads/payload'; mode = stat.S_IMODE(path.stat().st_mode); path.chmod(mode ^ 0o040)
            with self.assertRaises(Exception): control.live(locked=locked)
            path.chmod(mode); control.live(locked=locked)
        with self.assertRaises(Exception): n.m.d.recover(self.runtime, self.lease, self.backups, confirmed=True)._held()
        self.closed_activity()

    def test_replacement_alone_blocks_legacy_resume_even_when_malformed_or_linked(self):
        self.assert_done(self.execute()); path = self.scope.directory/s.MARKER
        for raw in (b'', b'{', b'foreign'):
            self.write(path, raw)
            with self.assertRaises(n.r.hd.m.MaintenanceError): self.lease.resume(confirmed=True)
        path.unlink(); path.symlink_to(self.resume_root/'plan.json')
        with self.assertRaises(n.r.hd.m.MaintenanceError): self.lease.resume(confirmed=True)
        path.unlink(); self.write(path, self.state.activation()); self.closed_activity()

    def killed(self, boundary):
        self.lease.close(); pid = os.fork()
        if pid == 0:
            try:
                with self.scope.recover(self.state.lease.lease_id, confirmed=True) as lease:
                    state = s.BlockerState(self.runtime, lease, self.backups, self.confirmation)
                    unlink = os.unlink
                    def cut(name, **kwargs):
                        unlink(name, **kwargs)
                        if name == boundary: os.kill(os.getpid(), signal.SIGKILL)
                    with patch.object(s.os, 'unlink', side_effect=cut): self.execute(state=state)
            except BaseException: os._exit(98)
            os._exit(97)
        deadline = time.monotonic() + 30
        while True:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found: break
            if time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
                self.fail('Blocker crash fixture exceeded 30 seconds')
            time.sleep(0.05)
        self.assertEqual(found, pid); self.assertTrue(os.WIFSIGNALED(status), status)
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        self.lease = self.scope.recover(self.state.lease.lease_id, confirmed=True)
        self.state = self.loaded(); self.closed_activity(); self.assert_done(self.execute('resume'))

    def test_sigkill_after_mobile_removal_reacquires_exact_lease(self): self.killed(s.OLD[0])
    def test_sigkill_after_gateway_removal_reacquires_exact_lease(self): self.killed(s.OLD[1])


if __name__ == '__main__': unittest.main()
