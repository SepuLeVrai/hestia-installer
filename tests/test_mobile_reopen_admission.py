"""Admission policy negatives; actual SQL/systemd/files remain native CI gates."""
from copy import deepcopy
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import pickle
import unittest
from unittest.mock import Mock, patch

from installer import mobile_reopen_admission as a


class AdmissionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.parent = {'scope': 'configuration', 'path': 'maintenance/profile.json',
            'kind': 'file', 'uid': 0, 'gid': 123, 'mode': 0o640, 'bytes': 6,
            'sha256': a.f._sha(b'parent'), 'blob': '00001.bin'}
        self.removed = {'web-inodes.attempt': b'original inode evidence'}
        self.added = {'mobile-reopen-files': None, 'mobile-reopen-files/profile.json': b'new exact profile'}
        self.saved = [self.parent, {**a._private_record('web-inodes.attempt', self.removed['web-inodes.attempt']),
                                  'blob': '00002.bin'}]
        self.expected = a._envelope(self.saved, self.removed, self.added)

    def compare(self, records):
        a.require(a._index(records) == self.expected, 'MOBILE_ADMISSION_ENVELOPE_CHANGED')

    def test_only_exact_release_journals_may_replace_archived_markers(self):
        self.compare([self.parent, *[a._private_record(k, v) for k, v in self.added.items()]])
        self.assertIn(('configuration', 'maintenance/profile.json'), self.expected)

    def test_unknown_maintenance_file_is_not_silently_excluded(self):
        with self.assertRaises(a.AdmissionError):
            self.compare([*self.expected.values(), a._private_record('foreign.json', b'foreign')])

    def test_missing_parent_journal_is_rejected(self):
        with self.assertRaises(a.AdmissionError):
            self.compare([v for k, v in self.expected.items() if k[1] != self.parent['path']])

    def test_parent_content_or_permissions_cannot_drift(self):
        for field, value in (('sha256', '0' * 64), ('mode', 0o666), ('gid', 0), ('bytes', 7)):
            changed = deepcopy(list(self.expected.values()))
            changed[0][field] = value
            with self.subTest(field=field), self.assertRaises(a.AdmissionError): self.compare(changed)

    def test_removed_marker_must_match_original_bytes_and_mode(self):
        with self.assertRaises(a.AdmissionError): a._envelope(self.saved, {'web-inodes.attempt': b'wrong'}, self.added)
        changed = deepcopy(self.saved); changed[-1]['mode'] = 0o640
        with self.assertRaises(a.AdmissionError): a._envelope(changed, self.removed, self.added)

    def test_missing_removed_marker_cannot_be_inferred_as_success(self):
        with self.assertRaises(a.AdmissionError): a._envelope([self.parent], self.removed, self.added)

    def test_new_journal_cannot_overwrite_an_archived_path(self):
        with self.assertRaises(a.AdmissionError): a._envelope(self.saved, self.removed, {'profile.json': b'new'})

    def test_duplicate_archive_path_is_rejected(self):
        with self.assertRaises(a.AdmissionError): a._index([self.parent, self.parent])

    def test_blob_number_changes_do_not_weaken_content_comparison(self):
        changed = {**self.parent, 'blob': '54321.bin'}
        self.assertEqual(a._index([changed]), a._index([self.parent]))
        changed['sha256'] = '0' * 64
        self.assertNotEqual(a._index([changed]), a._index([self.parent]))

    def test_archive_ids_refuse_traversal_and_foreign_shapes(self):
        self.assertEqual(a._id('a' * 32), 'a' * 32)
        for value in ('../escape', '/absolute', 'A' * 32, 'a' * 31, True, None):
            with self.subTest(value=value), self.assertRaises(a.AdmissionError): a._id(value)

    @contextmanager
    def archive_reader(self):
        # Use the actual legacy context/error boundary with in-memory I/O.
        # Native hashing, ownership and lease qualification stay in native CI.
        raw = a.p._json({'version': 1, 'instance': 'main', 'lease_id': 'a' * 32, 'records': []})
        snapshot = a.files.FileSnapshot(Path('/unused'), a.f._sha(raw), 'main', 'a' * 32, 1000)
        with patch.object(a.files, '_bound') as bound, \
                patch.object(a.fs, '_directory', return_value=nullcontext(17)), \
                patch.object(a.files, '_private'), patch.object(a.files, '_read', return_value=raw):
            yield snapshot, bound

    def test_data_blob_rejection_survives_legacy_archive_context_exit(self):
        rejected = a.AdmissionError('MOBILE_ADMISSION_ARCHIVE_CHANGED')
        with self.archive_reader() as (snapshot, bound), patch.object(a, '_blobs', side_effect=rejected):
            with self.assertRaisesRegex(a.AdmissionError, '^MOBILE_ADMISSION_ARCHIVE_CHANGED$') as result:
                a._data_blobs(snapshot, object(), None)
            self.assertIs(result.exception, rejected)
            self.assertEqual(bound.call_count, 2)

    def test_data_blob_rejection_does_not_bypass_archive_exit_lease_check(self):
        with self.archive_reader() as (snapshot, bound), patch.object(a, '_blobs',
                side_effect=a.AdmissionError('MOBILE_ADMISSION_ARCHIVE_CHANGED')):
            bound.side_effect = [None, a.files.FileSnapshotError('FILES_MAINTENANCE_REQUIRED')]
            with self.assertRaisesRegex(a.files.FileSnapshotError, '^FILES_MAINTENANCE_REQUIRED$'):
                a._data_blobs(snapshot, object(), None)
            self.assertEqual(bound.call_count, 2)

    def window(self):
        return a.AdmissionWindow(Mock(), Mock(spec=['assert_held']), Mock(spec=['assert_held']), Mock(), Mock(), Path('/unused'),
            {'activity_resumed': False, 'nested': {'historical_observation_only': True}})

    def test_live_report_is_defensive_and_reobserves_all_guards(self):
        window = self.window(); value = window.report(); value['nested'].clear()
        self.assertTrue(window._report['nested']['historical_observation_only'])
        window._control._live.assert_called_once(); window._archives.check.assert_called_once()
        window._envelope.assert_called_once(); window._schedulers.assert_held.assert_called_once()
        self.assertEqual(window._fence.assert_held.call_count, 2)

    def test_closed_window_does_not_reuse_its_historical_report(self):
        window = self.window(); window._closed = True
        with self.assertRaisesRegex(a.AdmissionError, 'MOBILE_ADMISSION_WINDOW_CLOSED'): window.report()
        window._fence.assert_held.assert_not_called()

    def test_window_is_process_bound_and_not_serializable(self):
        window = self.window()
        with patch.object(a.os, 'getpid', return_value=os.getpid() + 1):
            with self.assertRaises(a.AdmissionError): window.assert_held()
        with self.assertRaises(TypeError): pickle.dumps(window)

    def test_lost_sql_lock_never_reaches_file_observation_or_success(self):
        window = self.window(); window._fence.assert_held.side_effect = RuntimeError('credential must not escape')
        with self.assertRaisesRegex(a.AdmissionError, '^MOBILE_ADMISSION_UNAVAILABLE$'):
            window.report()
        window._control._live.assert_not_called()

    def test_file_drift_after_export_revokes_window(self):
        window = self.window(); window._archives.check.side_effect = a.AdmissionError('MOBILE_ADMISSION_ARCHIVE_CHANGED')
        with self.assertRaisesRegex(a.AdmissionError, 'MOBILE_ADMISSION_ARCHIVE_CHANGED'): window.report()

    def test_consent_and_types_refuse_before_any_live_effect(self):
        for confirmed, allowed in ((False, True), (True, False), (1, True)):
            with self.assertRaisesRegex(a.AdmissionError, 'MOBILE_ADMISSION_CONSENT_REQUIRED'):
                with a.acquire(None, None, None, None, None, 'a' * 64,
                               confirmed=confirmed, allow_global_read_lock=allowed): self.fail('yielded')
        with self.assertRaisesRegex(a.AdmissionError, 'MOBILE_ADMISSION_INPUT_REJECTED'):
            with a.acquire(None, None, None, None, None, 'a' * 64,
                           confirmed=True, allow_global_read_lock=True): self.fail('yielded')


if __name__ == '__main__': unittest.main()
