"""Pure transition-policy and live-window revocation contracts."""
import os
from pathlib import Path
import pickle
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import mobile_external_admission as b


class ExternalAdmissionPolicyTests(unittest.TestCase):
    def envelope(self, marker=b'original', release=None):
        self.external = Mock()
        self.archives = SimpleNamespace(removed={}, added={}, manifest={'files': [
            b.a._private_record(b.e.ef.MARKER, b'original'),
            b.a._private_record('parent.json', b'parent')]})
        with patch.object(b, '_external_state', return_value=(b'original', marker, release)):
            return b._transition_envelope(self.archives, self.external)

    def test_reserved_phase_preserves_the_exact_archived_marker(self):
        expected = self.envelope()
        self.assertEqual(expected, b.a._index(self.archives.manifest['files']))

    def test_partial_phase_adds_only_exact_native_release(self):
        expected = self.envelope(release=b'original')
        self.assertEqual(expected['configuration', 'maintenance/' + b.e.ef.RELEASE],
                         b.a._private_record(b.e.ef.RELEASE, b'original'))
        self.assertIn(('configuration', 'maintenance/' + b.e.ef.MARKER), expected)

    def test_complete_phase_removes_only_the_archived_external_marker(self):
        expected = self.envelope(marker=None)
        self.assertEqual(expected, b.a._index([b.a._private_record('parent.json', b'parent')]))

    def test_foreign_maintenance_files_still_invalidate_the_whole_envelope(self):
        expected = self.envelope(marker=None)
        actual = [*expected.values(), b.a._private_record('foreign.json', b'foreign')]
        self.assertNotEqual(b.a._index(actual), expected)

    def test_missing_or_changed_archived_marker_is_rejected(self):
        self.envelope()
        for saved in ([], [b.a._private_record(b.e.ef.MARKER, b'foreign')]):
            self.archives.manifest['files'] = saved
            with patch.object(b, '_external_state', return_value=(b'original', None, None)):
                with self.assertRaisesRegex(b.AdmissionError, 'MOBILE_ADMISSION_PARENT_CHANGED'):
                    b._transition_envelope(self.archives, self.external)

    def test_completed_window_demands_actual_completed_transition(self):
        self.envelope()
        with patch.object(b, '_external_state', side_effect=b.AdmissionError('MOBILE_EXTERNAL_RECEIPT_REQUIRED')) as state:
            with self.assertRaises(b.AdmissionError): b._transition_envelope(self.archives, self.external, completed=True)
        state.assert_called_once_with(self.external, completed=True)

    def window(self):
        return b.ExternalAdmissionWindow(Mock(), Mock(spec=['assert_held']), Mock(spec=['assert_held']),
            Mock(), Mock(), Mock(), Path('/unused'),
            {'external_paths_released': True, 'nested': {'historical_observation_only': True}})

    def test_window_rechecks_native_configuration_archives_sql_and_schedulers(self):
        window = self.window()
        with patch.object(b, '_transition_envelope', return_value={'exact': 'transition'}): window.assert_held()
        window._control.live.assert_called_once_with(configuration=window._configuration)
        window._archives.check.assert_called_once(); window._envelope.assert_called_once()
        window._schedulers.assert_held.assert_called_once()
        self.assertEqual(window._fence.assert_held.call_count, 2)
        self.assertEqual(window._archives.expected, {'exact': 'transition'})

    def test_live_report_is_defensive(self):
        window = self.window()
        with patch.object(b, '_transition_envelope', return_value={}): report = window.report()
        report['nested'].clear()
        self.assertTrue(window._report['nested']['historical_observation_only'])

    def test_closed_window_cannot_reuse_a_historical_observation(self):
        window = self.window(); window._closed = True
        with self.assertRaisesRegex(b.AdmissionError, 'MOBILE_EXTERNAL_WINDOW_CLOSED'): window.report()
        window._fence.assert_held.assert_not_called()

    def test_fork_and_serialization_are_refused(self):
        window = self.window()
        with patch.object(b.os, 'getpid', return_value=os.getpid() + 1), self.assertRaises(b.AdmissionError): window.report()
        with self.assertRaises(TypeError): pickle.dumps(window)

    def test_lost_sql_lock_prevents_native_validation_and_hides_exception_text(self):
        window = self.window(); window._fence.assert_held.side_effect = RuntimeError('private credential')
        with self.assertRaisesRegex(b.AdmissionError, '^MOBILE_EXTERNAL_ADMISSION_UNAVAILABLE$'): window.report()
        window._control.live.assert_not_called(); window._archives.check.assert_not_called()

    def test_changed_archive_revokes_even_an_already_observed_window(self):
        window = self.window(); window._archives.check.side_effect = b.AdmissionError('MOBILE_ADMISSION_ARCHIVE_CHANGED')
        with patch.object(b, '_transition_envelope', return_value={}):
            with self.assertRaisesRegex(b.AdmissionError, 'MOBILE_ADMISSION_ARCHIVE_CHANGED'): window.report()

    def test_consent_and_input_types_precede_any_native_read(self):
        for consent, allowed in ((False, True), (1, True), (True, False)):
            with self.assertRaisesRegex(b.AdmissionError, 'MOBILE_EXTERNAL_CONSENT_REQUIRED'):
                with b.acquire(None, None, None, None, None, None, None, None, 'a'*64,
                    action='apply', confirmed=consent, allow_global_read_lock=allowed): self.fail('yielded')
        with self.assertRaisesRegex(b.AdmissionError, 'MOBILE_EXTERNAL_INPUT_REJECTED'):
            with b.acquire(None, None, None, None, None, None, None, None, 'a'*64,
                action='apply', confirmed=True, allow_global_read_lock=True): self.fail('yielded')

    def test_parent_view_is_not_a_fabricated_old_lease(self):
        self.assertFalse(issubclass(b._ParentFiles, b.r.ReopenFilesPlan))
        with self.assertRaisesRegex(b.AdmissionError, 'MOBILE_EXTERNAL_INPUT_REJECTED'):
            b._ParentFiles(None, None, None, None)


if __name__ == '__main__': unittest.main()
