"""Native 6B7b3 continuation of the exact Gateway fixture, disposable CI only.

No mocked ownership, service, archive or filesystem readers. Injection performs
the actual syscall first and only then kills the child process. This qualifies
the file-release subplan; it is not SQL admission or application reopening.
"""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import signal
import traceback
from unittest.mock import patch

from installer import mobile_reopen_files as r
from installer import gateway_service_drain as gd, foundation_drain as fd
from installer.session_cleaner import SessionCleaner


@contextmanager
def opened(http, scope, lease_id, backups):
    """Reacquire stopped objects by observation, with no stop/start command."""
    with ExitStack() as stack:
        lease = stack.enter_context(scope.recover(lease_id, confirmed=True))
        data = stack.enter_context(r.da.recover(http, lease, confirmed=True))
        raw = r.f._read(lease._directory, 'http-drain-' + lease_id + '.attempt', scope.web_gid)
        barrier = r.hd.HttpDrainLease(r.hd.HttpDrain(http, cleaner=SessionCleaner(http)), lease, raw)
        barrier.assert_held()
        external = stack.enter_context(r.ef.ExternalFence(lease,
            r.files._read(lease._directory, r.ef.MARKER, r.ef.MAX_JOURNAL)))
        conf = stack.enter_context(r.fs._directory(scope.directory.parent))
        configuration = stack.enter_context(r.cf.admission.acquire(
            conf, http.spec.webroot, scope.web_gid, external=external))
        gateway = gd.attached(http, fd.attached(http))
        yield r.ReopenFilesPlan(barrier, data, configuration, external, gateway, backups)


def exercise(test, http, runtime, scope, lease_id, backups, preserved):
    from http_runtime_systemd import command
    gate = scope.directory
    original_gateway = (gate / r.gateway.RELEASED).read_bytes()
    original_data = (gate / r.da.MARKER).read_bytes()
    original_external = (gate / r.ef.MARKER).read_bytes()
    originals = {role: (gate / module.MARKER).read_bytes() for role, module in r.MODULES.items()}
    cleaner = SessionCleaner(http)
    units = (http.unit('php'), http.unit('apache'), cleaner.unit, cleaner.timer,
             runtime.foundation.unit, runtime.unit)

    def inactive():
        for unit in units:
            value = command('systemctl', 'show', '--property=ActiveState', '--value', unit).stdout.decode().strip()
            test.assertIn(value, ('inactive', 'failed'), (unit, value))
        runtime.stopped(); runtime.foundation.stopped()

    def closed():
        test.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        test.assertEqual((http.spec.root / 'data').stat().st_mode & 0o777, 0o700)
        test.assertEqual((gate / r.gateway.RELEASED).read_bytes(), original_gateway)
        test.assertEqual((gate / r.da.MARKER).read_bytes(), original_data)
        test.assertEqual((gate / r.ef.MARKER).read_bytes(), original_external)
        inactive()
        for path, raw in preserved.items(): test.assertEqual(path.read_bytes(), raw)

    closed()
    with opened(http, scope, lease_id, backups) as control:
        document = control.plan(confirmed=True)
        test.assertEqual(document['state'], 'PLANNED')
        test.assertEqual(control.plan(confirmed=True), document)
        for role, module in r.MODULES.items():
            test.assertEqual((gate / module.MARKER).read_bytes(), originals[role])
        test.assertFalse((gate / r.guard.MARKER).exists())
    confirmation = document['plan_sha256']

    def child(role, action, boundary):
        try:
            with opened(http, scope, lease_id, backups) as control:
                if boundary == 'flag':
                    flags = r.inf._flags
                    def cut(handle, value=None):
                        result = flags(handle, value)
                        if value is not None: os.kill(os.getpid(), signal.SIGKILL)
                        return result
                    injection = patch.object(r.inf, '_flags', side_effect=cut)
                else:
                    unlink = os.unlink
                    def cut(name, *args, **kwargs):
                        unlink(name, *args, **kwargs)
                        if name == boundary: os.kill(os.getpid(), signal.SIGKILL)
                    injection = patch.object(r.os, 'unlink', side_effect=cut)
                with injection: control.execute(action, confirmation, confirmed=True)
        except BaseException as error:
            # Keep only locations and fixed codes. Never dump credentials/locals.
            import re
            value = str(error)
            report = {'role': role, 'type': type(error).__name__,
                'code': value if re.fullmatch('[A-Z][A-Z0-9_]{1,80}', value) else 'REDACTED',
                'frames': [{'file': Path(x.filename).name, 'line': x.lineno, 'function': x.name}
                           for x in traceback.extract_tb(error.__traceback__)]}
            Path('/evidence/mobile-reopen-files-child-error.json').write_text(json.dumps(report, indent=2) + '\n')
            raise

    cuts = (('data', 'apply', 'flag'), ('configuration', 'resume', r.cf.RELEASE),
            ('web', 'resume', r.wf.MARKER))
    for role, action, boundary in cuts:
        test.kill_child(lambda: child(role, action, boundary))
        closed()
        with opened(http, scope, lease_id, backups) as control:
            state = control.journal.read()
            current = next(row for row in state['steps'] if row['name'] == 'mobile-reopen-files.' + role)
            test.assertEqual((current['state'], current['phase']), ('RUNNING', 'apply'))
            test.assertEqual(state['plan_sha256'], confirmation)
            for name, raw in originals.items():
                test.assertEqual((control.root / (name + '-original.json')).read_bytes(), raw)
            with test.assertRaises(r.hd.m.MaintenanceError): control.lease.resume(confirmed=True)
            with test.assertRaises(r.da.DataAccessError): control.data.reopen(confirmed=True)
        test.assertTrue((gate / r.guard.MARKER).is_file())
    with opened(http, scope, lease_id, backups) as control:
        result = control.execute('resume', confirmation, confirmed=True)
        test.assertEqual(result['transaction']['state'], 'DONE', result)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in control.root.rglob('*') if p.is_file()}
        repeated = control.execute('check', confirmation, confirmed=True)
        test.assertEqual(repeated, result)
        test.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                for p in control.root.rglob('*') if p.is_file()})
        for key in ('activity_resumed', 'admission_verified', 'services_started',
                    'data_access_reopened', 'external_paths_released'): test.assertIs(result[key], False)
        for module in r.MODULES.values():
            test.assertFalse((gate / module.MARKER).exists())
            test.assertFalse((gate / module.RELEASE).exists())
        control.external.assert_held(); control.data.assert_held()
        with test.assertRaises(r.hd.m.MaintenanceError): control.lease.resume(confirmed=True)
        # A saved DONE is not a live certificate: the native Web reader must
        # reject drift before another effect. Restore only our fixture bytes.
        path = http.spec.webroot / 'index.php'; raw = path.read_bytes()
        try:
            path.write_bytes(raw + b'\n/* disposable source-drift test */\n')
            with test.assertRaises(Exception): control.execute('check', confirmation, confirmed=True)
        finally: path.write_bytes(raw)
        test.assertEqual(control.execute('check', confirmation, confirmed=True), result)
    closed()
    # Native unit conditions still refuse activation with both durable blockers.
    for unit in (http.unit('php'), runtime.foundation.unit, runtime.unit, http.unit('apache'), cleaner.unit):
        command('systemctl', 'start', unit)
    closed()
    evidence = {'status': 'PASS', 'state': result['state'], 'plan_sha256': confirmation,
        'original_journals': 5, 'native_service_and_gateway_readers': True,
        'real_ext4_flags': True, 'sigkill_after_data_flag': True,
        'sigkill_after_configuration_release_unlink': True, 'sigkill_after_web_marker_unlink': True,
        'exact_lease_reacquired_without_drain_replay': True, 'done_drift_rejected': True,
        'check_is_read_only': True, 'parent_journals_and_keys_preserved': True,
        'maintenance_gate_kept': True, 'native_starts_still_gated': True,
        'data_access_reopened': False, 'external_paths_released': False,
        'current_sql_admission_delivered': False, 'services_started': False,
        'activity_resumed': False, 'phase6_complete': False}
    Path('/evidence/mobile-reopen-files-native.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence
