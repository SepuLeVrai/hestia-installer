"""Prepare and recover the durable resume plan using native SQL/data admission."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import signal
import stat
import time
import traceback
from unittest.mock import patch

from installer import mobile_resume_plan as r
from mobile_reopen_files_systemd import killed_at_boundary, SessionCleaner

m = r.m


def exercise(test, http, runtime, scope, lease_id, backups, worker, source, payload, authority, preserved):
    stage = 'resume-plan'; timings = []
    root = backups / ('mobile-resume-' + lease_id)
    with scope.recover(lease_id, confirmed=True) as lease:
        data = m.d.recover(http, lease, backups, confirmed=True)
        confirmation = data.plan_sha256; parents = m.e._parents(lease)

    def timing(window, event):
        timings.append({'observation_id': window._slot.name, 'event': event,
            'elapsed_sql_seconds': round(time.monotonic() - (window._fence._deadline - 180), 6)})
        Path('/evidence/mobile-resume-plan-timings.json').write_text(json.dumps(timings, indent=2) + '\n')

    def failure(error):
        chain = []; current = error
        while current is not None and len(chain) < 6:
            text = str(current)
            chain.append({'type': type(current).__name__,
                'code': text if re.fullmatch('[A-Z][A-Z0-9_]{1,80}', text) else 'REDACTED',
                'frames': [{'file': Path(x.filename).name, 'line': x.lineno, 'function': x.name}
                           for x in traceback.extract_tb(current.__traceback__)]})
            current = current.__context__
        Path('/evidence/mobile-resume-plan-error.json').write_text(json.dumps({'stage': stage, 'chain': chain}, indent=2) + '\n')

    @contextmanager
    def admitted():
        try:
            with ExitStack() as stack:
                lease = stack.enter_context(scope.recover(lease_id, confirmed=True))
                data = m.d.recover(http, lease, backups, confirmed=True)
                raw = m.f._read(lease._directory, 'http-drain-' + lease_id + '.attempt', scope.web_gid)
                barrier = m.r.hd.HttpDrainLease(m.r.hd.HttpDrain(http, cleaner=SessionCleaner(http)), lease, raw)
                with m.acquire(data, barrier, None, worker, source, payload, authority, confirmation,
                    action='check', confirmed=True, allow_global_read_lock=True) as window:
                    timing(window, 'entered')
                    yield window
                    timing(window, 'consumer-complete')
                timing(window, 'normally-released')
        except BaseException as error:
            failure(error); raise

    def activity_closed():
        test.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        test.assertEqual(stat.S_IMODE((http.spec.root / 'data').stat().st_mode), 0o750)
        runtime.stopped(); runtime.foundation.stopped()
        for path, raw in preserved.items(): test.assertEqual(path.read_bytes(), raw)
        with scope.recover(lease_id, confirmed=True) as lease:
            test.assertEqual(m.e._parents(lease), parents)
            with test.assertRaises(m.r.hd.m.MaintenanceError): lease.resume(confirmed=True)

    def snapshot():
        return {p.name: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode), p.stat().st_uid, p.stat().st_gid)
                for p in root.iterdir()}

    before = set(backups.glob('data-admission-*')); original_new = r.files._new
    stage = 'sigkill-after-first-private-copy'
    def interrupted():
        def cut(fd, name, raw):
            original_new(fd, name, raw)
            if name == r.COPIES[0]: os.kill(os.getpid(), signal.SIGKILL)
        with patch.object(r.files, '_new', side_effect=cut):
            with admitted() as window: r.begin(window, confirmed=True)
        test.fail('SIGKILL boundary missed')
    killed_at_boundary(test, interrupted)
    slots = set(backups.glob('data-admission-*')) - before; test.assertEqual(len(slots), 1)
    interrupted_slot = slots.pop()
    interrupted_bytes = {p.name: p.read_bytes() for p in interrupted_slot.iterdir()}
    test.assertTrue((interrupted_slot / 'sql-recheck.ndjson').is_file())
    test.assertTrue((interrupted_slot / 'observed.json').is_file())
    test.assertEqual(set(snapshot()), {'plan.json', r.COPIES[0]})
    partial = snapshot(); activity_closed()

    # A complete historical data observation cannot authorize a new operation
    # if current SQL changed. No missing resume-plan copy may be filled here.
    stage = 'sql-drift-before-resume-preparation'
    test.sql([f'CREATE TABLE `{test.db}`.Hestia_Resume_Plan_Drift (id INT PRIMARY KEY)'])
    try:
        with test.assertRaises(m.AdmissionError):
            with admitted(): test.fail('Changed SQL admitted before resume preparation')
    finally: test.sql([f'DROP TABLE `{test.db}`.Hestia_Resume_Plan_Drift'])
    test.assertEqual(snapshot(), partial); activity_closed()

    completed_slots, exports = set(), set()
    def fresh(window):
        test.assertNotEqual(window._slot, interrupted_slot)
        test.assertNotIn(window._slot, completed_slots); completed_slots.add(window._slot)
        export = window._report['sql_recheck']['sha256']
        test.assertNotIn(export, exports); exports.add(export)
        test.assertNotEqual(export, m.a.sql._hash(interrupted_slot / 'sql-recheck.ndjson'))

    # Each operation is a separate bounded admission. The plan object contains
    # no live window, so read-only recovery never consumes the next SQL budget.
    stage = 'recover-partial-read-only'
    with admitted() as window:
        fresh(window); plan = r.recover(window, confirmed=True)
        test.assertEqual(snapshot(), partial)
    with test.assertRaises(r.ResumePlanError): plan.prepare(window, plan.plan_sha256, confirmed=True)
    test.assertEqual(snapshot(), partial); activity_closed()

    stage = 'prepare-under-fresh-admission'
    with admitted() as window:
        fresh(window); prepared = plan.prepare(window, plan.plan_sha256, confirmed=True)
        for name in r.COPIES:
            metadata = plan.value['originals'][name]
            original = scope.directory / metadata['source']; info = original.stat()
            test.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)),
                             (metadata['uid'], metadata['gid'], metadata['mode']))
            test.assertEqual((root / name).read_bytes(), original.read_bytes())
            copy = (root / name).stat()
            test.assertEqual((copy.st_uid, copy.st_gid, stat.S_IMODE(copy.st_mode)), (0, 0, 0o600))
        test.assertEqual([row['role'] for row in prepared['start_order']],
                         ['php', 'apache', 'foundation', 'gateway', 'timer'])
        saved = snapshot()
    test.assertEqual(snapshot(), saved); activity_closed()

    stage = 'check-complete-under-fresh-admission'
    with admitted() as window:
        fresh(window); test.assertEqual(plan.check(window, plan.plan_sha256, confirmed=True), prepared)
        test.assertEqual(snapshot(), saved)
    with test.assertRaises(r.ResumePlanError): plan.check(window, plan.plan_sha256, confirmed=True)
    test.assertEqual(snapshot(), saved); activity_closed()
    test.assertEqual({p.name: p.read_bytes() for p in interrupted_slot.iterdir()}, interrupted_bytes)
    test.assertEqual(len(completed_slots), 3); test.assertEqual(len(exports), 3)
    test.assertEqual(len(timings), 9)
    test.assertTrue(all(0 <= row['elapsed_sql_seconds'] < 180 for row in timings))
    proof = {'status': 'PASS', 'actual_current_sql_export': True, 'actual_bounded_sql_read_fence': True,
        'native_archives_live_files_and_gateway_readers': True, 'sigkill_after_first_private_copy': True,
        'partial_recovery_read_only': True, 'fresh_admission_for_explicit_prepare': True,
        'sql_drift_refused_before_preparation': True, 'original_journals_bytes_modes_owners_preserved': True,
        'private_copies_verified': True, 'bound_service_order_verified': True,
        'completed_check_read_only': True, 'closed_window_rejected': True,
        'interrupted_attempt_preserved': True, 'maintenance_and_mobile_gateway_blockers_kept': True,
        'completed_fresh_windows': 3, 'sql_read_fence_max_seconds': 180, 'window_timings': timings,
        'plan_sha256': plan.plan_sha256, 'prepared': prepared, 'data_access_reopened': True,
        'external_paths_released': True, 'blockers_consumed': False, 'services_started': False,
        'activity_resumed': False, 'phase6_complete': False}
    Path('/evidence/mobile-resume-plan-native.json').write_text(json.dumps(proof, indent=2) + '\n')
    return proof
