"""Pure current-admission, marker-envelope and failure-boundary contracts."""
from copy import deepcopy
from pathlib import Path
import os
import pickle
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import mobile_blocker_admission as n
s = n.s


class BlockerAdmissionPolicyTests(unittest.TestCase):
    def window(self):
        control = Mock(); control.state = Mock()
        return n.BlockerWindow(control, Mock(spec=['assert_held']), Mock(spec=['assert_held']),
            Mock(spec=['assert_held']), Mock(), Mock(), Path('/unused'),
            {'activation_blocker_kept': True, 'nested': {'historical': True}})

    def test_consent_and_types_precede_any_state_or_sql_read(self):
        for confirmed, allowed in ((False, True), (1, True), (True, False), (True, 1)):
            with self.assertRaisesRegex(s.BlockerError, 'CONSENT_REQUIRED'):
                with n.acquire(None, None, None, None, None, None, None, 'a'*64,
                    action='apply', confirmed=confirmed, allow_global_read_lock=allowed): self.fail('yielded')
        with self.assertRaisesRegex(s.BlockerError, 'INPUT_REJECTED'):
            with n.acquire(None, None, None, None, None, None, None, 'a'*64,
                action='apply', confirmed=True, allow_global_read_lock=True): self.fail('yielded')

    def test_confirmation_rejects_long_special_empty_bool_and_foreign_values_before_io(self):
        with patch.object(s.e, '_inputs') as inputs:
            for value in ('', True, 1, None, 'A'*64, 'a'*63, 'a'*64+'\n', '../root', 'é'*64, 'a'*100000):
                with self.assertRaisesRegex(s.BlockerError, 'CONFIRMATION_REQUIRED'):
                    s.BlockerState(None, None, None, value)
            inputs.assert_not_called()

    def test_full_check_reobserves_files_archives_locks_schedulers_and_sql(self):
        w = self.window()
        with patch.object(n, '_transition_envelope', return_value={'exact': True}): w.assert_held()
        w._control.live.assert_called_once_with(locked=w._locked)
        w._archives.check.assert_called_once(); w._envelope.assert_called_once()
        self.assertEqual(w._fence.assert_held.call_count, 4)
        self.assertEqual(w._locked.assert_held.call_count, 2)
        self.assertEqual(w._schedulers.assert_held.call_count, 2)
        self.assertEqual(w._control.state.static.call_count, 2)
        self.assertEqual(w._archives.expected, {'exact': True})

    def test_closed_or_forked_window_cannot_use_a_saved_report(self):
        w = self.window(); w._closed = True
        with self.assertRaisesRegex(s.BlockerError, 'WINDOW_CLOSED'): w.report()
        w._fence.assert_held.assert_not_called()
        w = self.window()
        with patch.object(n.os, 'getpid', return_value=w._pid+1), self.assertRaisesRegex(s.BlockerError, 'WINDOW_CLOSED'):
            w.report()
        w._fence.assert_held.assert_not_called()

    def test_report_is_defensive_and_not_serializable(self):
        w = self.window()
        with patch.object(n, '_transition_envelope', return_value={}): report = w.report()
        report['nested'].clear(); self.assertTrue(w._report['nested']['historical'])
        with self.assertRaises(TypeError): pickle.dumps(w)

    def test_lost_fence_refuses_before_any_live_effect_and_redacts(self):
        w = self.window(); w._fence.assert_held.side_effect = RuntimeError('private-value')
        with self.assertRaisesRegex(s.BlockerError, '^MOBILE_BLOCKER_UNAVAILABLE$'): w.report()
        w._control.live.assert_not_called(); w._archives.check.assert_not_called()

    def test_archive_settings_or_scheduler_loss_revokes_the_window(self):
        for field, method in (('_archives', 'check'), ('_locked', 'assert_held'), ('_schedulers', 'assert_held')):
            w = self.window(); getattr(getattr(w, field), method).side_effect = s.BlockerError('MOBILE_BLOCKER_CHANGED')
            with patch.object(n, '_transition_envelope', return_value={}), self.assertRaises(s.BlockerError): w.report()

    def envelope(self, present=(True, True), activation=False):
        originals = dict(zip(s.p.COPIES, (b'maintenance', b'mobile', b'gateway', b'http')))
        control = Mock(); control.state.state.return_value = {'present': list(present), 'activation': activation}
        control.state.originals = originals; control.state.activation.return_value = b'activation'
        control.external._external.return_value = (b'external', {}); control._read.return_value = b'data'
        control.data._account.pw_gid = 19001
        archives = SimpleNamespace(removed={}, added={s.OLD[0]: b'mobile', s.OLD[1]: b'gateway'},
            manifest={'files': [n.a._private_record(s.e.ef.MARKER, b'external'), n.m._data_record(b'data', 19001),
                               n.a._private_record('retained', b'unchanged')]})
        self.control, self.archives = control, archives
        return n._transition_envelope(archives, control)

    def test_every_allowed_marker_state_has_an_exact_envelope(self):
        for present, activation in (((True, True), False), ((True, True), True), ((False, True), True), ((False, False), True)):
            expected = self.envelope(present, activation)
            self.assertIn(('configuration', 'maintenance/retained'), expected)
            for flag, name in zip(present, s.OLD):
                self.assertEqual(('configuration', 'maintenance/'+name) in expected, flag)
            self.assertEqual(('configuration', 'maintenance/'+s.MARKER) in expected, activation)
            self.assertEqual(len(expected), 1 + sum(present) + activation)

    def test_original_marker_bytes_cannot_be_substituted_in_archive_envelope(self):
        self.envelope(); self.archives.added[s.OLD[0]] = b'foreign'
        with self.assertRaises(s.BlockerError): n._transition_envelope(self.archives, self.control)

    def test_archived_data_mode_group_and_bytes_remain_exact(self):
        for changes in ({'gid': 0}, {'mode': 0o600}, {'sha256': 'f'*64}):
            self.envelope(); self.archives.manifest['files'][1].update(changes)
            with self.assertRaisesRegex(s.BlockerError, 'ARCHIVED_MARKER_CHANGED'):
                n._transition_envelope(self.archives, self.control)

    def test_unknown_maintenance_is_never_excluded(self):
        expected = self.envelope((False, False), True)
        current = [*expected.values(), n.a._private_record('foreign.json', b'{}')]
        self.assertNotEqual(n.a._index(current), expected)

    def test_effect_requires_exact_window_owned_by_the_same_state(self):
        state = object.__new__(s.BlockerState)
        for window in (None, Mock(spec=n.BlockerWindow), self.window()):
            with self.assertRaisesRegex(s.BlockerError, 'LIVE_ADMISSION_REQUIRED'): state._execute('apply', window)

    def test_legacy_archives_keep_native_marker_reader_as_default(self):
        self.assertIs(n.a._Archives._journal_changes, n.a._journal_changes)
        self.assertFalse(issubclass(n._ParentFiles, n.r.ReopenFilesPlan))
        self.assertFalse(issubclass(n.m._DataProfile, n.m.d.da.DataAccessFence))
        with self.assertRaises(s.BlockerError): n._ParentFiles(None, None, None)


if __name__ == '__main__': unittest.main()
