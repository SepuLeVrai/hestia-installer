"""Native SQL/configuration/external transition; no mocked admission readers."""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import re
import signal
import traceback
from unittest.mock import patch

from installer import mobile_external_admission as b
from mobile_reopen_files_systemd import opened, killed_at_boundary, gd, fd, SessionCleaner


def exercise(test, http, runtime, scope, lease_id, backups, worker, source, payload, authority, preserved):
    stage = 'external-plan'
    with opened(http, scope, lease_id, backups) as control:
        plan = b.e.begin(control, confirmed=True)
        confirmation = plan.plan_sha256
        parents = b.e._parents(control.lease)
    plan_root = backups / ('external-release-' + lease_id)

    def failure(error):
        chain = []; current = error
        while current is not None and len(chain) < 6:
            text = str(current)
            chain.append({'type': type(current).__name__,
                'code': text if re.fullmatch('[A-Z][A-Z0-9_]{1,80}', text) else 'REDACTED',
                'frames': [{'file': Path(row.filename).name, 'line': row.lineno, 'function': row.name}
                           for row in traceback.extract_tb(current.__traceback__)]})
            current = current.__context__
        Path('/evidence/mobile-external-admission-error.json').write_text(json.dumps({'stage': stage, 'chain': chain}, indent=2)+'\n')

    @contextmanager
    def admitted(action):
        try:
            with ExitStack() as stack:
                lease = stack.enter_context(scope.recover(lease_id, confirmed=True))
                data = stack.enter_context(b.r.da.recover(http, lease, confirmed=True))
                raw = b.r.f._read(lease._directory, 'http-drain-' + lease_id + '.attempt', scope.web_gid)
                barrier = b.r.hd.HttpDrainLease(b.r.hd.HttpDrain(http, cleaner=SessionCleaner(http)), lease, raw)
                barrier.assert_held()
                gateway = gd.attached(http, fd.attached(http))
                external = b.e.recover(lease, backups, confirmed=True)
                test.assertEqual(external.plan_sha256, confirmation)
                with b.acquire(external, barrier, data, gateway, worker, source, payload, authority, confirmation,
                    action=action, confirmed=True, allow_global_read_lock=True) as window:
                    yield window
        except BaseException as error:
            failure(error)
            raise

    def observations(): return set(backups.glob('external-admission-*'))

    def closed():
        test.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        test.assertEqual((http.spec.root / 'data').stat().st_mode & 0o777, 0o700)
        runtime.stopped(); runtime.foundation.stopped()
        for path, raw in preserved.items(): test.assertEqual(path.read_bytes(), raw)
        with scope.recover(lease_id, confirmed=True) as lease:
            test.assertEqual(b.e._parents(lease), parents)
            with test.assertRaises(b.r.hd.m.MaintenanceError): lease.resume(confirmed=True)

    # Current SQL mismatch must refuse BEFORE the external intent or any removal.
    stage = 'sql-drift-before-effect'; before = observations()
    test.sql([f'CREATE TABLE `{test.db}`.Hestia_External_Admission_Drift (id INT PRIMARY KEY)'])
    try:
        with test.assertRaises(b.AdmissionError):
            with admitted('apply'): test.fail('Changed SQL admitted')
    finally: test.sql([f'DROP TABLE `{test.db}`.Hestia_External_Admission_Drift'])
    rejected = observations() - before; test.assertEqual(len(rejected), 1)
    rejected_slot = rejected.pop()
    test.assertTrue((rejected_slot / 'sql-recheck.ndjson').is_file())
    test.assertFalse((rejected_slot / 'observed.json').exists())
    test.assertFalse((plan_root / 'intent.json').exists())
    test.assertTrue((scope.directory / b.e.ef.MARKER).is_file())
    for path in b.e.ef.PATHS: test.assertTrue(path.is_file())
    closed()

    # Execute real unlink first; leave no low-level recovery journal at all.
    stage = 'sigkill-after-last-external-release'; before = observations(); unlink = os.unlink
    def interrupted():
        def cut(name, *args, **kwargs):
            unlink(name, *args, **kwargs)
            if name == b.e.ef.RELEASE: os.kill(os.getpid(), signal.SIGKILL)
        with patch.object(b.e.os, 'unlink', side_effect=cut):
            with admitted('apply'): test.fail('SIGKILL boundary missed')
    killed_at_boundary(test, interrupted)
    interrupted_slots = observations() - before; test.assertEqual(len(interrupted_slots), 1)
    interrupted_slot = interrupted_slots.pop()
    test.assertTrue((interrupted_slot / 'sql-recheck.ndjson').is_file())
    test.assertFalse((interrupted_slot / 'observed.json').exists())
    interrupted_bytes = {path.name: path.read_bytes() for path in interrupted_slot.iterdir()}
    test.assertTrue((plan_root / 'intent.json').is_file())
    test.assertFalse((plan_root / 'released.json').exists())
    for name in (b.e.ef.MARKER, b.e.ef.RELEASE): test.assertFalse((scope.directory / name).exists())
    for path in b.e.ef.PATHS: test.assertFalse(path.exists())
    closed()

    stage = 'fresh-admission-after-sigkill'
    with admitted('resume') as window:
        result = window.report()
        test.assertNotEqual(window._slot, interrupted_slot)
        test.assertNotEqual(result['sql_recheck']['sha256'], b.a.sql._hash(interrupted_slot / 'sql-recheck.ndjson'))
        test.assertIsNone(window._configuration._external)
        test.assertTrue(window._configuration._files)
        test.assertTrue((plan_root / 'released.json').is_file())
        # Unknown maintenance remains visible after the one allowed removal.
        foreign = scope.directory / 'foreign-external-admission.json'
        foreign.write_bytes(b'{}'); foreign.chmod(0o600)
        try:
            stage = 'foreign-maintenance-after-release'
            with test.assertRaisesRegex(b.AdmissionError, 'MOBILE_ADMISSION_ENVELOPE_CHANGED'): window.assert_held()
        finally: foreign.unlink()
        stage = 'current-data-after-release'
        with window._archives.data._open(window._control.lease) as (_, manifest):
            row = next(row for row in manifest['records'] if row['kind'] == 'file')
            roots = {r['scope']: Path(r['root']) for r in manifest['roots']}
            path = roots[row['scope']] / row['path']
        info = path.stat(); descriptor = os.open(path, b.files.REGULAR)
        try:
            with os.fdopen(descriptor, 'rb', closefd=False) as stream: raw = stream.read()
        finally: os.close(descriptor)
        try:
            path.write_bytes(raw + b'changed after external release')
            with test.assertRaises(b.AdmissionError): window.assert_held()
        finally:
            path.write_bytes(raw); os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        stage = 'normal-released-window-exit'
        saved = (window._slot / 'observed.json').read_bytes()
    with test.assertRaisesRegex(b.AdmissionError, 'MOBILE_EXTERNAL_WINDOW_CLOSED'): window.report()
    test.assertEqual((window._slot / 'observed.json').read_bytes(), saved)
    test.assertEqual({path.name: path.read_bytes() for path in interrupted_slot.iterdir()}, interrupted_bytes)
    closed()
    proof = {'status': 'PASS', 'native_service_and_gateway_readers': True,
        'native_archives_and_live_files': True, 'actual_current_sql_export': True,
        'actual_bounded_sql_read_fence': True, 'sql_drift_refused_before_external_intent': True,
        'sigkill_after_last_external_release_unlink': True, 'outer_intent_recovers_native_journals_absence': True,
        'fresh_sql_export_after_sigkill': True, 'configuration_exclusive_during_effect': True,
        'configuration_reacquired_without_reservations': True, 'interrupted_attempt_preserved': True,
        'unknown_maintenance_journal_rejected': True, 'current_data_drift_rejected': True,
        'closed_window_rejected': True, 'parent_journals_and_keys_preserved': True,
        'maintenance_gate_kept': True, 'observation': result, 'external_paths_released': True,
        'data_access_reopened': False, 'services_started': False, 'activity_resumed': False, 'phase6_complete': False}
    Path('/evidence/mobile-external-admission-native.json').write_text(json.dumps(proof, indent=2)+'\n')
    return proof
