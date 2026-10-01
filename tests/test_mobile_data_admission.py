"""Pure journal transitions and revocation; no SQL/system/native filesystem access."""
import os
from pathlib import Path
import pickle
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import mobile_data_admission as m


class DataAdmissionPolicyTests(unittest.TestCase):
    def plan(self, *, marker=b'data', mode=0o700, intent=None, receipt=None):
        plan = Mock()
        plan._held.return_value = (None, SimpleNamespace(pw_gid=19001), marker, mode)
        plan._owner.return_value = b'intent'; plan._receipt.return_value = b'receipt'
        plan._read.side_effect = lambda name: {'intent.json': intent, 'released.json': receipt}[name]
        return plan

    def test_open_or_partial_without_own_intent_is_not_admitted(self):
        for marker in (None, b'data'):
            with self.subTest(marker=marker), self.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_INTENT_REQUIRED'):
                m._state(self.plan(marker=marker, mode=0o750))

    def test_exact_partial_is_read_only_and_requires_resume(self):
        plan = self.plan(mode=0o750, intent=b'intent')
        self.assertEqual(m._action(plan, 'resume'), (b'data', 0o750))
        for action in ('apply', 'check'):
            with self.assertRaises(m.AdmissionError): m._action(plan, action)
        plan._save.assert_not_called()

    def test_receipt_requires_both_intent_and_completed_native_state(self):
        for plan in (self.plan(receipt=b'receipt'), self.plan(intent=b'intent', receipt=b'receipt')):
            with self.assertRaises(m.AdmissionError): m._state(plan)
        plan = self.plan(marker=None, mode=0o750, intent=b'intent', receipt=b'receipt')
        self.assertEqual(m._action(plan, 'check'), (None, 0o750))
        with self.assertRaises(m.AdmissionError): m._action(plan, 'apply')

    def test_foreign_intent_and_receipt_are_refused(self):
        for args in ({'intent': b'foreign'}, {'intent': b'intent', 'receipt': b'foreign'}):
            with self.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_JOURNAL_CHANGED'): m._state(self.plan(**args))

    def envelope(self, marker=b'data'):
        control = Mock(); control._read.return_value = b'data'
        control.external._external.return_value = (b'external', {})
        archives = SimpleNamespace(removed={}, added={}, manifest={'files': [
            m.a._private_record(m.e.ef.MARKER, b'external'), m._data_record(b'data', 19001),
            m.a._private_record('retained.json', b'parent')]})
        state = (SimpleNamespace(pw_gid=19001), marker, 0o700 if marker else 0o750, b'intent', None)
        self.archives, self.control, self.state = archives, control, state
        with patch.object(m, '_state', return_value=state): return m._transition_envelope(archives, control)

    def test_closed_transition_preserves_original_data_marker_group_and_mode(self):
        expected = self.envelope()
        row = expected['configuration', 'maintenance/' + m.d.da.MARKER]
        self.assertEqual((row['uid'], row['gid'], row['mode']), (0, 19001, 0o640))
        self.assertEqual(len(expected), 2)

    def test_open_transition_removes_only_both_exact_archived_native_markers(self):
        self.assertEqual(self.envelope(None), m.a._index([m.a._private_record('retained.json', b'parent')]))

    def test_archived_marker_wrong_group_mode_bytes_or_absence_is_refused(self):
        self.envelope()
        external = m.a._private_record(m.e.ef.MARKER, b'external')
        correct = m._data_record(b'data', 19001)
        for rows in ([], [{**correct, 'gid': 0}], [{**correct, 'mode': 0o600}], [m._data_record(b'foreign', 19001)]):
            self.archives.manifest['files'] = [external, *rows]
            with patch.object(m, '_state', return_value=self.state), self.assertRaisesRegex(
                    m.AdmissionError, 'MOBILE_DATA_ARCHIVED_MARKER_CHANGED'):
                m._transition_envelope(self.archives, self.control)

    def test_foreign_maintenance_remains_visible_after_data_marker_removal(self):
        expected = self.envelope(None)
        self.assertNotEqual(m.a._index([*expected.values(), m.a._private_record('foreign.json', b'{}')]), expected)

    def test_completed_envelope_demands_current_receipt_and_open_state(self):
        self.envelope(None)
        with patch.object(m, '_state', side_effect=m.AdmissionError('MOBILE_DATA_RECEIPT_REQUIRED')) as state:
            with self.assertRaises(m.AdmissionError): m._transition_envelope(self.archives, self.control, completed=True)
        state.assert_called_once_with(self.control.plan, completed=True)

    def window(self):
        return m.DataAdmissionWindow(Mock(), Mock(spec=['assert_held']), Mock(spec=['assert_held']),
            Mock(spec=['assert_held']), Mock(), Mock(), Path('/unused'),
            {'data_access_reopened': True, 'nested': {'historical_observation_only': True}})

    def test_window_rechecks_native_files_archives_schedulers_locks_and_sql(self):
        window = self.window()
        with patch.object(m, '_transition_envelope', return_value={'exact': 'data transition'}): window.assert_held()
        window._control.live.assert_called_once_with(locked=window._locked)
        window._archives.check.assert_called_once(); window._envelope.assert_called_once()
        self.assertEqual(window._locked.assert_held.call_count, 2)
        self.assertEqual(window._fence.assert_held.call_count, 2)
        self.assertEqual(window._archives.expected, {'exact': 'data transition'})

    def test_closed_window_cannot_reuse_saved_observation(self):
        window = self.window(); window._closed = True
        with self.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_WINDOW_CLOSED'): window.report()
        window._fence.assert_held.assert_not_called()

    def test_report_is_defensive_and_objects_cannot_cross_processes(self):
        window = self.window()
        with patch.object(m, '_transition_envelope', return_value={}): result = window.report()
        result['nested'].clear(); self.assertTrue(window._report['nested']['historical_observation_only'])
        with self.assertRaises(TypeError): pickle.dumps(window)
        with patch.object(m.os, 'getpid', return_value=os.getpid()+1), self.assertRaises(m.AdmissionError): window.report()

    def test_lost_sql_lock_refuses_before_native_reads_and_redacts_error(self):
        window = self.window(); window._fence.assert_held.side_effect = RuntimeError('private credential')
        with self.assertRaisesRegex(m.AdmissionError, '^MOBILE_DATA_ADMISSION_UNAVAILABLE$'): window.report()
        window._control.live.assert_not_called(); window._archives.check.assert_not_called()

    def test_archive_or_configuration_loss_revokes_even_recorded_window(self):
        for name in ('_archives', '_locked'):
            window = self.window()
            getattr(getattr(window, name), 'check' if name == '_archives' else 'assert_held').side_effect = m.AdmissionError('MOBILE_DATA_CHANGED')
            with patch.object(m, '_transition_envelope', return_value={}), self.assertRaises(m.AdmissionError): window.report()

    def test_consent_and_types_precede_any_native_read(self):
        for consent, allowed in ((False, True), (1, True), (True, False)):
            with self.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_CONSENT_REQUIRED'):
                with m.acquire(None, None, None, None, None, None, None, 'a'*64,
                    action='apply', confirmed=consent, allow_global_read_lock=allowed): self.fail('yielded')
        with self.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_INPUT_REJECTED'):
            with m.acquire(None, None, None, None, None, None, None, 'a'*64,
                action='apply', confirmed=True, allow_global_read_lock=True): self.fail('yielded')

    def test_archive_profile_is_not_a_fabricated_native_lease(self):
        self.assertFalse(issubclass(m._ParentFiles, m.r.ReopenFilesPlan))
        self.assertFalse(issubclass(m._DataProfile, m.d.da.DataAccessFence))
        self.assertFalse(hasattr(m._DataProfile(object(), object()), 'assert_held'))
        with self.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_INPUT_REJECTED'): m._ParentFiles(None, None, None)


if __name__ == '__main__': unittest.main()
