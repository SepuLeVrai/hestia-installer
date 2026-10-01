"""Native SQL/data continuation. Kept pending until the parent recipe qualifies."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import signal
import time
import traceback
from unittest.mock import patch

from installer import mobile_data_admission as m
from mobile_reopen_files_systemd import killed_at_boundary, SessionCleaner


def exercise(test, http, runtime, scope, lease_id, backups, worker, source, payload, authority, preserved):
    stage = 'data-plan'; timings = []
    with scope.recover(lease_id, confirmed=True) as lease:
        with m.d.da.recover(http, lease, confirmed=True) as access:
            external = m.e.recover(lease, backups, confirmed=True)
            plan = m.d.begin(external, access, confirmed=True)
            confirmation = plan.plan_sha256; parents = m.e._parents(lease)
    plan_root = backups / ('data-release-' + lease_id)
    def timing(window, event):
        timings.append({'observation_id': window._slot.name, 'event': event,
            'elapsed_sql_seconds': round(time.monotonic() - (window._fence._deadline - 180), 6)})
        Path('/evidence/mobile-data-admission-timings.json').write_text(json.dumps(timings, indent=2)+'\n')
    def failure(error):
        chain = []; current = error
        while current is not None and len(chain) < 6:
            text = str(current)
            chain.append({'type': type(current).__name__,
                'code': text if re.fullmatch('[A-Z][A-Z0-9_]{1,80}', text) else 'REDACTED',
                'frames': [{'file': Path(x.filename).name, 'line': x.lineno, 'function': x.name}
                           for x in traceback.extract_tb(current.__traceback__)]})
            current = current.__context__
        Path('/evidence/mobile-data-admission-error.json').write_text(json.dumps({'stage': stage, 'chain': chain}, indent=2)+'\n')

    @contextmanager
    def admitted(action):
        try:
            with ExitStack() as stack:
                lease = stack.enter_context(scope.recover(lease_id, confirmed=True))
                data_plan = m.d.recover(http, lease, backups, confirmed=True)
                raw = m.f._read(lease._directory, 'http-drain-' + lease_id + '.attempt', scope.web_gid)
                barrier = m.r.hd.HttpDrainLease(m.r.hd.HttpDrain(http, cleaner=SessionCleaner(http)), lease, raw)
                # No DataAccessFence is invented after its marker disappears.
                # None asks the coordinator to attach fresh native runtimes after
                # explicit partial recovery, before the first live/SQL admission.
                with m.acquire(data_plan, barrier, None, worker, source, payload, authority, confirmation,
                    action=action, confirmed=True, allow_global_read_lock=True) as window:
                    timing(window, action + '-entered')
                    yield window
                    timing(window, action + '-consumer-complete')
                timing(window, action + '-normally-released')
        except BaseException as error:
            failure(error); raise

    def observations(): return set(backups.glob('data-admission-*'))
    def activity_closed(mode):
        test.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        test.assertEqual((http.spec.root / 'data').stat().st_mode & 0o777, mode)
        runtime.stopped(); runtime.foundation.stopped()
        for path, raw in preserved.items(): test.assertEqual(path.read_bytes(), raw)
        with scope.recover(lease_id, confirmed=True) as lease:
            test.assertEqual(m.e._parents(lease), parents)
            with test.assertRaises(m.r.hd.m.MaintenanceError): lease.resume(confirmed=True)

    stage = 'sql-drift-before-data-intent'; before = observations()
    test.sql([f'CREATE TABLE `{test.db}`.Hestia_Data_Admission_Drift (id INT PRIMARY KEY)'])
    try:
        with test.assertRaises(m.AdmissionError):
            with admitted('apply'): test.fail('Changed SQL admitted before data reopen')
    finally: test.sql([f'DROP TABLE `{test.db}`.Hestia_Data_Admission_Drift'])
    rejected = observations() - before; test.assertEqual(len(rejected), 1); rejected_slot = rejected.pop()
    test.assertTrue((rejected_slot / 'sql-recheck.ndjson').is_file())
    test.assertFalse((rejected_slot / 'observed.json').exists())
    test.assertFalse((plan_root / 'intent.json').exists()); activity_closed(0o700)

    stage = 'sigkill-after-data-chmod'; before = observations(); chmod = os.fchmod
    data_identity = (http.spec.root / 'data').stat()
    def interrupted():
        def cut(fd, mode):
            info = os.fstat(fd); chmod(fd, mode)
            if mode == 0o750 and (info.st_dev, info.st_ino) == (data_identity.st_dev, data_identity.st_ino):
                os.kill(os.getpid(), signal.SIGKILL)
        with patch.object(m.os, 'fchmod', side_effect=cut):
            with admitted('apply'): test.fail('SIGKILL boundary missed')
    killed_at_boundary(test, interrupted)
    partial = observations() - before; test.assertEqual(len(partial), 1); partial_slot = partial.pop()
    partial_bytes = {p.name: p.read_bytes() for p in partial_slot.iterdir()}
    test.assertTrue((partial_slot / 'sql-recheck.ndjson').is_file())
    test.assertFalse((partial_slot / 'observed.json').exists())
    test.assertTrue((plan_root / 'intent.json').exists()); test.assertFalse((plan_root / 'released.json').exists())
    test.assertTrue((scope.directory / m.d.da.MARKER).is_file())
    test.assertEqual((http.spec.root / 'data').stat().st_mode & 0o777, 0o750)
    with test.assertRaisesRegex(m.d.da.DataAccessError, 'DATA_ACCESS_INCOMPLETE'):
        m.d.da.expected_mode(http, m.d.da.h._identity(http.spec.service_user))
    # Do not call stopped() yet: its unchanged reader correctly rejects this
    # partial state. Resume must explicitly reclose it before that native audit.

    stage = 'fresh-sql-after-explicit-reclosure'
    with admitted('resume') as window:
        result = window.report()
        test.assertTrue(result['partial_chmod_explicitly_reclosed'])
        test.assertTrue(result['data_access_reopened'])
        test.assertNotEqual(window._slot, partial_slot)
        test.assertNotEqual(result['sql_recheck']['sha256'], m.a.sql._hash(partial_slot / 'sql-recheck.ndjson'))
        test.assertFalse((scope.directory / m.d.da.MARKER).exists())
        saved = (window._slot / 'observed.json').read_bytes()
        stage = 'normal-resumed-data-window-exit'
    with test.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_WINDOW_CLOSED'): window.report()
    test.assertEqual((window._slot / 'observed.json').read_bytes(), saved)
    test.assertEqual({p.name: p.read_bytes() for p in partial_slot.iterdir()}, partial_bytes)
    activity_closed(0o750)
    slots = {window._slot}; exports = {result['sql_recheck']['sha256']}
    # Independent negative scenarios receive independent fresh SQL budgets.
    for negative in ('maintenance', 'data'):
        stage = 'fresh-check-' + negative
        with admitted('check') as window:
            test.assertNotIn(window._slot, slots); slots.add(window._slot)
            test.assertNotIn(window._report['sql_recheck']['sha256'], exports)
            exports.add(window._report['sql_recheck']['sha256'])
            if negative == 'maintenance':
                path = scope.directory / 'foreign-data-admission.json'; path.write_bytes(b'{}'); path.chmod(0o600)
                try:
                    stage = 'unknown-maintenance-after-data-open'
                    with test.assertRaisesRegex(m.AdmissionError, 'MOBILE_ADMISSION_ENVELOPE_CHANGED'): window.assert_held()
                finally: path.unlink()
            else:
                with window._archives.data._open(window._control.lease) as (_, manifest):
                    row = next(row for row in manifest['records'] if row['kind'] == 'file')
                    roots = {r['scope']: Path(r['root']) for r in manifest['roots']}; path = roots[row['scope']] / row['path']
                info = path.stat(); fd = os.open(path, m.files.REGULAR)
                try:
                    with os.fdopen(fd, 'rb', closefd=False) as stream: raw = stream.read()
                finally: os.close(fd)
                try:
                    stage = 'current-data-drift-after-open'; path.write_bytes(raw + b'changed after data access reopen')
                    with test.assertRaises(m.AdmissionError): window.assert_held()
                finally: path.write_bytes(raw); os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            saved = (window._slot / 'observed.json').read_bytes(); stage = 'normal-check-exit-' + negative
        with test.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_WINDOW_CLOSED'): window.report()
        test.assertEqual((window._slot / 'observed.json').read_bytes(), saved); activity_closed(0o750)
    test.assertEqual(len(slots), 3); test.assertEqual(len(exports), 3); test.assertEqual(len(timings), 9)
    test.assertTrue(all(0 <= r['elapsed_sql_seconds'] < 180 for r in timings))
    proof = {'status': 'PASS', 'native_service_and_gateway_readers': True,
        'actual_current_sql_export': True, 'actual_bounded_sql_read_fence': True,
        'native_archives_and_live_files_after_data_open': True, 'sql_drift_refused_before_data_intent': True,
        'sigkill_after_real_data_chmod': True, 'strict_partial_state_reader_preserved': True,
        'partial_state_explicitly_reclosed_before_native_attachment': True, 'fresh_sql_export_before_reopening': True,
        'interrupted_attempt_preserved': True, 'unknown_maintenance_journal_rejected': True,
        'current_data_drift_after_reopening_rejected': True, 'closed_window_rejected': True,
        'parent_journals_and_keys_preserved': True, 'maintenance_and_mobile_gateway_blockers_kept': True,
        'completed_fresh_windows': 3, 'sql_read_fence_max_seconds': 180, 'window_timings': timings,
        'observation': result, 'data_access_reopened': True, 'external_paths_released': True,
        'services_started': False, 'activity_resumed': False, 'phase6_complete': False}
    Path('/evidence/mobile-data-admission-native.json').write_text(json.dumps(proof, indent=2)+'\n')
    return proof
