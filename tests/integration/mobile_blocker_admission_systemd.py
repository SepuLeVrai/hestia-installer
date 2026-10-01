"""Native recoverable old-blocker handoff; maintenance/activation remain closed."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import stat
import time
import traceback
from unittest.mock import patch

from installer import mobile_blocker_admission as n
from mobile_reopen_files_systemd import killed_at_boundary, SessionCleaner

s = n.s


def exercise(test, http, runtime, scope, lease_id, backups, worker, source, payload, authority, preserved, confirmation):
    stage = 'blocker-handoff'; timings = []
    root = backups / ('mobile-blockers-' + lease_id)
    resume = backups / ('mobile-resume-' + lease_id)
    saved = {p: (p.read_bytes(), p.stat().st_uid, p.stat().st_gid, stat.S_IMODE(p.stat().st_mode)) for p in resume.iterdir()}
    gate = (scope.directory / 'maintenance.attempt').read_bytes()

    def timing(window, event):
        timings.append({'observation_id': window._slot.name, 'event': event,
            'elapsed_sql_seconds': round(time.monotonic() - (window._fence._deadline - 180), 6)})
        Path('/evidence/mobile-blocker-timings.json').write_text(json.dumps(timings, indent=2) + '\n')

    def failure(error):
        chain = []; current = error
        while current is not None and len(chain) < 6:
            code = str(current)
            chain.append({'type': type(current).__name__,
                'code': code if re.fullmatch('[A-Z][A-Z0-9_]{1,80}', code) else 'REDACTED',
                'frames': [{'file': Path(x.filename).name, 'line': x.lineno, 'function': x.name}
                           for x in traceback.extract_tb(current.__traceback__)]})
            current = current.__context__
        Path('/evidence/mobile-blocker-error.json').write_text(json.dumps({'stage': stage, 'chain': chain}, indent=2) + '\n')

    @contextmanager
    def admitted(action):
        try:
            with scope.recover(lease_id, confirmed=True) as lease:
                state = s.BlockerState(http, lease, backups, confirmation)
                raw = n.f._read(lease._directory, 'http-drain-' + lease_id + '.attempt', scope.web_gid)
                barrier = n.r.hd.HttpDrainLease(n.r.hd.HttpDrain(http, cleaner=SessionCleaner(http)), lease, raw)
                with n.acquire(state, barrier, None, worker, source, payload, authority, confirmation,
                    action=action, confirmed=True, allow_global_read_lock=True) as window:
                    timing(window, 'entered'); yield window; timing(window, 'consumer-complete')
                timing(window, 'normally-released')
        except BaseException as error:
            failure(error); raise

    def activity_closed():
        test.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        test.assertEqual((scope.directory / 'maintenance.attempt').read_bytes(), gate)
        test.assertEqual(stat.S_IMODE((http.spec.root / 'data').stat().st_mode), 0o750)
        runtime.stopped(); runtime.foundation.stopped()
        for path, raw in preserved.items(): test.assertEqual(path.read_bytes(), raw)
        for path, expected in saved.items():
            info = path.stat()
            test.assertEqual((path.read_bytes(), info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), expected)
        with scope.recover(lease_id, confirmed=True) as lease:
            state = s.BlockerState(http, lease, backups, confirmation)
            with test.assertRaises(n.r.hd.m.MaintenanceError): lease.resume(confirmed=True)
            return state.state()

    def snapshot():
        return {p.name: p.read_bytes() for p in root.iterdir()} if root.exists() else {}

    stage = 'sql-drift-before-first-intent'
    test.sql([f'CREATE TABLE `{test.db}`.Hestia_Blocker_Drift (id INT PRIMARY KEY)'])
    try:
        with test.assertRaises(s.BlockerError):
            with admitted('apply'): test.fail('Changed SQL admitted before blocker handoff')
    finally: test.sql([f'DROP TABLE `{test.db}`.Hestia_Blocker_Drift'])
    test.assertFalse(root.exists()); test.assertEqual(activity_closed()['present'], [True, True])

    interrupted = []; exports = set(); unlink = os.unlink
    for index, marker in enumerate(s.OLD):
        stage = 'sigkill-after-' + marker
        before = set(backups.glob('blocker-admission-*'))
        def cut_action():
            def cut(name, **kwargs):
                unlink(name, **kwargs)
                if name == marker: os.kill(os.getpid(), signal.SIGKILL)
            with patch.object(s.os, 'unlink', side_effect=cut):
                with admitted('apply' if index == 0 else 'resume'): pass
            test.fail('SIGKILL boundary missed')
        killed_at_boundary(test, cut_action)
        slots = set(backups.glob('blocker-admission-*')) - before; test.assertEqual(len(slots), 1)
        slot = slots.pop(); content = {p.name: p.read_bytes() for p in slot.iterdir()}
        test.assertIn('sql-recheck.ndjson', content); test.assertNotIn('observed.json', content)
        digest = n.a.sql._hash(slot / 'sql-recheck.ndjson'); test.assertNotIn(digest, exports); exports.add(digest)
        interrupted.append((slot, content)); status = activity_closed()
        test.assertTrue(status['activation']); test.assertFalse(status['present'][index]); test.assertFalse(status['done'])

    partial = snapshot(); stage = 'sql-drift-after-both-unlinks'
    test.sql([f'CREATE TABLE `{test.db}`.Hestia_Blocker_Drift (id INT PRIMARY KEY)'])
    try:
        with test.assertRaises(s.BlockerError):
            with admitted('resume'): test.fail('Changed SQL admitted during blocker recovery')
    finally: test.sql([f'DROP TABLE `{test.db}`.Hestia_Blocker_Drift'])
    test.assertEqual(snapshot(), partial); activity_closed()

    complete = None
    for action in ('resume', 'check'):
        stage = action + '-under-fresh-admission'
        with admitted(action) as window:
            digest = window._report['sql_recheck']['sha256']; test.assertNotIn(digest, exports); exports.add(digest)
            test.assertTrue(window._report['old_blockers_removed']); test.assertTrue(window._report['activation_blocker_kept'])
            test.assertFalse(window._report['maintenance_released']); test.assertFalse(window._report['services_started'])
            if complete is not None: test.assertEqual(snapshot(), complete)
            complete = snapshot()
        with test.assertRaises(s.BlockerError): window.assert_held()
        test.assertTrue(activity_closed()['done'])
    for slot, content in interrupted: test.assertEqual({p.name: p.read_bytes() for p in slot.iterdir()}, content)
    test.assertEqual(len(exports), 4); test.assertEqual(len(timings), 6)
    test.assertTrue(all(0 <= row['elapsed_sql_seconds'] < 180 for row in timings))
    proof = {'status': 'PASS', 'plan_sha256': confirmation, 'sigkill_after_each_original_unlink': True,
        'sql_drift_before_intent_and_after_unlinks_refused': True, 'originals_bytes_modes_owners_preserved': True,
        'native_gateway_archives_data_web_configuration_verified': True, 'fresh_exports_for_each_recovery': True,
        'replacement_durable_before_old_blockers_removed': True, 'legacy_maintenance_release_refused': True,
        'completed_check_read_only': True, 'closed_window_rejected': True, 'interrupted_attempts_preserved': True,
        'completed_fresh_windows': 2, 'interrupted_fresh_windows': 2, 'sql_read_fence_max_seconds': 180,
        'window_timings': timings, 'old_blockers_removed': True, 'activation_blocker_kept': True,
        'maintenance_released': False, 'services_started': False, 'activity_resumed': False, 'phase6_complete': False}
    Path('/evidence/mobile-blocker-native.json').write_text(json.dumps(proof, indent=2) + '\n')
    return proof
