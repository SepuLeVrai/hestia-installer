"""Native continuation: actual archives, logical SQL recheck and held window."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import traceback
import re
import time
from unittest.mock import patch

from installer import mobile_reopen_admission as a
from mobile_reopen_files_systemd import opened, killed_at_boundary


def exercise(test, http, runtime, scope, lease_id, backups, worker, source, payload, authority, preserved):
    stage = 'initial-acquisition'; timings = []
    def timing(window, event):
        timings.append({'observation_id': window._slot.name, 'event': event,
            'elapsed_sql_seconds': round(time.monotonic() - (window._fence._deadline - 180), 6)})
        Path('/evidence/mobile-reopen-admission-timings.json').write_text(json.dumps(timings, indent=2) + '\n')
    def failure(error, name):
        # Native-only evidence: fixed stage, codes and frame locations; no
        # exception message, local variables, file contents or credentials.
        chain = []; current = error
        while current is not None and len(chain) < 6:
            value = str(current)
            chain.append({'type': type(current).__name__,
                'code': value if re.fullmatch('[A-Z][A-Z0-9_]{1,80}', value) else 'REDACTED',
                'diagnostic_codes': sorted(set(re.findall(
                    r'\b(?:MOBILE_ADMISSION|FILES|SQL_FENCE)_[A-Z_]{1,70}\b', value))),
                'frames': [{'file': Path(x.filename).name, 'line': x.lineno, 'function': x.name}
                           for x in traceback.extract_tb(current.__traceback__)]})
            current = current.__context__
        Path('/evidence/' + name).write_text(json.dumps({'stage': stage, 'chain': chain}, indent=2) + '\n')

    @contextmanager
    def admitted():
        try:
            with opened(http, scope, lease_id, backups) as control:
                document = control.journal.read()
                with a.acquire(control, worker, source, payload, authority, document['plan_sha256'],
                               confirmed=True, allow_global_read_lock=True) as window:
                    timing(window, 'entered')
                    yield window
                    timing(window, 'consumer-complete')
                timing(window, 'normally-released')
        except BaseException as error:
            failure(error, 'mobile-admission-error.json')
            raise

    def observations(): return set(backups.glob('admission-*'))

    def closed():
        test.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        test.assertEqual((http.spec.root / 'data').stat().st_mode & 0o777, 0o700)
        for marker in (a.r.guard.MARKER, a.r.gateway.RELEASED, a.r.da.MARKER, a.r.ef.MARKER):
            test.assertTrue((scope.directory / marker).is_file())
        runtime.stopped(); runtime.foundation.stopped()
        for path, raw in preserved.items(): test.assertEqual(path.read_bytes(), raw)

    # Kill only AFTER the real export succeeded, BEFORE an observation is stored.
    before = observations()
    recheck = a.c._recheck
    def interrupted():
        def cut(*args, **kwargs):
            recheck(*args, **kwargs)
            os.kill(os.getpid(), signal.SIGKILL)
        try:
            with patch.object(a.c, '_recheck', side_effect=cut):
                with admitted(): test.fail('SIGKILL boundary missed')
        except BaseException as error:
            failure(error, 'mobile-admission-child-error.json')
            raise
    killed_at_boundary(test, interrupted)
    interrupted_slots = observations() - before
    test.assertEqual(len(interrupted_slots), 1)
    interrupted_slot = interrupted_slots.pop()
    test.assertTrue((interrupted_slot / 'sql-recheck.ndjson').is_file())
    test.assertFalse((interrupted_slot / 'observed.json').exists())
    interrupted_bytes = {p.name: p.read_bytes() for p in interrupted_slot.iterdir()}
    closed()
    # Independent negative cases each use a fresh native SQL fence. Never renew
    # a deadline or reuse a persisted observation as active authorization.
    slots, exports = set(), set()
    for scenario in ('report', 'data', 'blob', 'maintenance'):
        stage = 'fresh-acquisition-' + scenario
        with admitted() as window:
            test.assertNotEqual(window._slot, interrupted_slot)
            test.assertNotIn(window._slot, slots); slots.add(window._slot)
            export = window._report['sql_recheck']['sha256']
            test.assertNotIn(export, exports); exports.add(export)
            if scenario == 'report':
                stage = 'fresh-report'; result = window.report()
            elif scenario in ('data', 'blob'):
                state = window._archives.data
                with state._open(window._control.lease) as (_, manifest):
                    row = next(row for row in manifest['records'] if row['kind'] == 'file')
                    roots = {r['scope']: Path(r['root']) for r in manifest['roots']}
                    path = roots[row['scope']] / row['path']
                if scenario == 'data':
                    info = path.stat(); fd = os.open(path, a.files.REGULAR)
                    try:
                        with os.fdopen(fd, 'rb', closefd=False) as stream: raw = stream.read()
                    finally: os.close(fd)
                    try:
                        stage = 'current-data-drift'; path.write_bytes(raw + b'changed current data')
                        with test.assertRaises(a.AdmissionError): window.assert_held()
                    finally:
                        path.write_bytes(raw); os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                else:
                    blob = state._slot / 'blobs' / row['blob']; saved_blob = blob.read_bytes()
                    try:
                        stage = 'saved-blob-drift'; blob.write_bytes(saved_blob + b'changed saved data')
                        with test.assertRaisesRegex(a.AdmissionError, 'MOBILE_ADMISSION_ARCHIVE_CHANGED'):
                            window.assert_held()
                    finally: blob.write_bytes(saved_blob)
            else:
                foreign = scope.directory / 'foreign-admission.json'
                foreign.write_bytes(b'{}'); foreign.chmod(0o600)
                try:
                    stage = 'unknown-maintenance-journal'
                    with test.assertRaisesRegex(a.AdmissionError, 'MOBILE_ADMISSION_ENVELOPE_CHANGED'):
                        window.assert_held()
                finally: foreign.unlink()
            saved_observation = (window._slot / 'observed.json').read_bytes()
            stage = 'normal-window-exit-' + scenario
        with test.assertRaisesRegex(a.AdmissionError, 'MOBILE_ADMISSION_WINDOW_CLOSED'): window.report()
        test.assertEqual((window._slot / 'observed.json').read_bytes(), saved_observation)
        test.assertEqual({p.name: p.read_bytes() for p in interrupted_slot.iterdir()}, interrupted_bytes)
        closed()
    test.assertEqual(len(slots), 4); test.assertEqual(len(exports), 4); test.assertEqual(len(timings), 12)
    test.assertTrue(all(0 <= row['elapsed_sql_seconds'] < 180 for row in timings))
    # Actual current SQL drift after window close: the old observation is not an
    # admission cache. A fresh export must reject the additional native table.
    test.sql([f'CREATE TABLE `{test.db}`.Hestia_Admission_Drift (id INT PRIMARY KEY)'])
    before = observations()
    try:
        stage = 'current-sql-drift'
        with test.assertRaises(a.AdmissionError):
            with admitted(): test.fail('Changed SQL admitted')
    finally: test.sql([f'DROP TABLE `{test.db}`.Hestia_Admission_Drift'])
    new = observations() - before
    test.assertEqual(len(new), 1)
    test.assertFalse((new.pop() / 'observed.json').exists())
    closed()
    proof = {'status': 'PASS', 'native_archives_and_live_files': True,
        'actual_current_sql_export': True, 'actual_bounded_sql_read_fence': True,
        'sigkill_after_sql_export_before_observation': True, 'interrupted_attempt_preserved': True,
        'fresh_attempt_and_fresh_export_required': True, 'current_data_drift_rejected': True,
        'saved_data_blob_drift_rejected': True,
        'unknown_maintenance_journal_rejected': True, 'current_sql_drift_rejected': True,
        'closed_window_rejected': True, 'parent_journals_and_keys_preserved': True,
        'maintenance_gate_kept': True, 'historical_observation_only': True,
        'completed_fresh_windows': 4, 'sql_read_fence_max_seconds': 180, 'window_timings': timings,
        'observation': result, 'data_access_reopened': False, 'external_paths_released': False,
        'services_started': False, 'activity_resumed': False, 'phase6_complete': False}
    Path('/evidence/mobile-reopen-admission-native.json').write_text(json.dumps(proof, indent=2) + '\n')
    return proof
