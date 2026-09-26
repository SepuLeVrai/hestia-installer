"""Controller tests; genuine system manager evidence is a separate recipe."""
import contextlib
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import systemd_observations as o
import test_launcher_inventory as model


class SystemdObserverTests(unittest.TestCase):
    def setUp(self):
        t = model.target(); self.target = t
        self.runtime = o.h.HttpRuntime(o.h.RuntimeSpec(t.instance, Path('/var/lib/private-runtime'),
            Path(t.webroot), 'hestia-private', 'hestia.test', 8123, '8.4'))
        self.cleaner = o.c.SessionCleaner(self.runtime)
        self.observer = o.SystemdObserver(self.runtime, cleaner=self.cleaner)
        self.account = SimpleNamespace(pw_name='hestia-private', pw_uid=t.web_uid, pw_gid=t.web_gid)
        self.provenance = {'host_id': t.host_id, 'boot_id': t.boot_id,
                          'namespaces': {'pid': 'pid:[12]', 'mnt': 'mnt:[34]'}, 'manager': 'system'}

    def properties(self, unit):
        timer = unit.endswith('.timer')
        value = {'Id': unit, 'LoadState': 'loaded', 'FragmentPath': str(o.h.drain.UNIT_ROOT / unit),
            'DropInPaths': '' if timer else str(o.h.drain.UNIT_ROOT / (unit+'.d/50-hestia-maintenance.conf')),
            'NeedDaemonReload': 'no', 'ActiveState': 'inactive', 'SubState': 'dead', 'Job': ''}
        if timer: value['Unit'] = self.cleaner.unit
        else: value.update(ControlGroup='', MainPID='0', ControlPID='0', Result='success')
        return value

    @contextlib.contextmanager
    def fixture(self, *, external=False):
        if external:
            self.runtime.spec = replace(self.runtime.spec, external_uploads=True,
                                        maintenance_directory=Path(self.target.maintenance))
        files = {o.h.drain.UNIT_ROOT / self.cleaner.unit: b'collector',
            o.h.drain.UNIT_ROOT / self.cleaner.timer: b'timer',
            o.h.drain.UNIT_ROOT / (self.cleaner.unit+'.d/50-hestia-maintenance.conf'): b'condition'}
        with patch.object(o, '_provenance', return_value=self.provenance) as provenance, \
             patch.object(self.runtime, '_inspect_configuration', return_value=(self.account, Path('/usr/lib/php/20240924'), b'runtime', {})) as runtime, \
             patch.object(self.cleaner, '_inspect_configuration', return_value=(self.account, None, files, b'cleaner')) as cleaner, \
             patch.object(o, '_show', side_effect=self.properties) as show, \
             patch.object(o.h, '_command', side_effect=AssertionError('mutation')), \
             patch.object(o.c.SessionCleaner, '_stop_timer', side_effect=AssertionError('mutation')), \
             patch.object(o.h.m.MaintenanceScope, 'acquire', side_effect=AssertionError('mutation')), \
             patch.object(o.h.m.MaintenanceScope, 'recover', side_effect=AssertionError('mutation')):
            yield SimpleNamespace(show=show, runtime=runtime, cleaner=cleaner, provenance=provenance)

    def test_scope_requires_exact_typed_provisioners_and_same_runtime(self):
        for runtime, cleaner in (({}, None), (self.runtime, object()),
                (self.runtime, o.c.SessionCleaner(o.h.HttpRuntime(self.runtime.spec)))):
            with self.assertRaises(o.SystemdObservationError): o.SystemdObserver(runtime, cleaner=cleaner)
        with self.fixture() as fx:
            self.cleaner.runtime = o.h.HttpRuntime(self.runtime.spec)
            with self.assertRaisesRegex(o.SystemdObservationError, 'COLLECTOR_MISMATCH'): self.observer.collect()
            fx.show.assert_not_called()

    def test_two_or_four_units_no_control_no_certified_inventory_and_private_output(self):
        with self.fixture() as fx:
            for observer, count in ((o.SystemdObserver(self.runtime), 2), (self.observer, 4)):
                sample = observer.collect(); report = sample.report()
                self.assertEqual(report['units'], count); self.assertEqual(report['coverage'], 'partial')
                for value in report.values():
                    if type(value) is bool: self.assertFalse(value)
                text = repr(observer)+repr(sample)+json.dumps(report)
                for private in (self.target.instance, self.target.webroot, self.target.host_id, self.target.boot_id):
                    self.assertNotIn(private, text)
                changed = sample.private_manifest(); changed.clear()
                self.assertEqual(sample.report(), report)
            self.assertEqual(fx.show.call_count, 6)

    def test_legacy_fixture_is_not_bridge_to_pinned_business_web(self):
        with self.fixture(), self.assertRaisesRegex(o.SystemdObservationError, 'TARGET_MISMATCH'):
            self.observer.collect().snapshot(self.target)

    def test_bridge_keeps_unknowns_and_partial_coverage_and_binds_exact_target(self):
        with self.fixture(external=True): sample = self.observer.collect()
        snapshot = sample.snapshot(self.target)
        self.assertEqual([x.state for x in snapshot.coverage], ['partial']+['unknown']*5)
        for row in snapshot.launchers:
            self.assertEqual(row.chain, ()); self.assertEqual(row.chain_mode, 'unknown')
            self.assertIsNone(row.uid); self.assertIsNone(row.gid); self.assertIsNone(row.maintenance)
            self.assertIsNone(row.environment_sha256); self.assertEqual(row.trigger, 'unknown')
        result = o.l.LauncherInventory(self.target, model.storage()).inspect(snapshot, now=int(time.time()))
        self.assertIn('CHANNEL_COVERAGE_UNRESOLVED', result.report()['blockers'])
        for key, bad in (('boot_id', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'), ('host_id', 'c'*32),
                         ('webroot', '/srv/foreign'), ('web_uid', 992), ('instance', 'd'*32)):
            with self.subTest(key=key), self.assertRaisesRegex(o.SystemdObservationError, 'TARGET_MISMATCH'):
                sample.snapshot(replace(self.target, **{key: bad}))

    def test_active_queued_failed_and_armed_are_retained_without_disarming_claim(self):
        with self.fixture(external=True) as fx:
            def show(unit):
                row = self.properties(unit)
                if unit.endswith('.timer'): row.update(ActiveState='active', SubState='waiting')
                elif unit == self.cleaner.unit: row.update(ActiveState='activating', SubState='start', Job='123')
                elif unit == self.runtime.unit('apache'): row.update(ActiveState='failed', SubState='failed', Result='exit-code')
                else: row.update(ActiveState='active', SubState='running', MainPID='900', ControlGroup='/system.slice/'+unit)
                return row
            fx.show.side_effect = show
            rows = {row.key: row for row in self.observer.collect().snapshot(self.target).launchers}
            self.assertEqual(rows[self.cleaner.timer].trigger, 'armed')
            self.assertEqual(rows[self.cleaner.unit].state, 'queued')
            self.assertEqual(rows[self.runtime.unit('apache')].state, 'unknown')
            self.assertEqual(rows[self.runtime.unit('php')].state, 'active')

    def test_configuration_drift_before_read_never_becomes_empty_success(self):
        with self.fixture() as fx:
            fx.runtime.side_effect = RuntimeError('/secret/raw-error')
            with self.assertRaisesRegex(o.SystemdObservationError, '^SYSTEMD_OBSERVATION_UNAVAILABLE$'): self.observer.collect()
            fx.show.assert_not_called()

    def test_configuration_and_provenance_drift_during_read_refuse(self):
        with self.fixture() as fx:
            original = fx.runtime.return_value
            fx.runtime.side_effect = [original, (*original[:2], b'different-plan', {})]
            with self.assertRaisesRegex(o.SystemdObservationError, 'CHANGED_DURING_READ'): self.observer.collect()
        with self.fixture() as fx:
            fx.provenance.side_effect = [self.provenance, {**self.provenance, 'host_id': 'c'*32}]
            with self.assertRaisesRegex(o.SystemdObservationError, 'CHANGED_DURING_READ'): self.observer.collect()

    def test_loaded_fragment_dropin_reload_target_and_not_found_are_rejected(self):
        for change in ({'Id': 'foreign.service'}, {'FragmentPath': '/foreign'}, {'DropInPaths': '/foreign'},
                       {'NeedDaemonReload': 'yes'}, {'LoadState': 'not-found'}):
            with self.subTest(change=change), self.fixture() as fx:
                fx.show.side_effect = lambda unit: {**self.properties(unit), **change}
                with self.assertRaisesRegex(o.SystemdObservationError, 'DEFINITION_CHANGED'): self.observer.collect()
        with self.fixture() as fx:
            fx.show.side_effect = lambda unit: {**self.properties(unit), **({'Unit': 'other.service'} if unit.endswith('.timer') else {})}
            with self.assertRaisesRegex(o.SystemdObservationError, 'DEFINITION_CHANGED'): self.observer.collect()

    def test_malformed_job_state_pid_and_cgroup_are_refused(self):
        for change in ({'Job': '0'}, {'Job': '123 start'}, {'ActiveState': '../active'},
                       {'MainPID': '-1'}, {'ControlGroup': '/a/../b'}, {'Result': 'secret\nvalue'}):
            with self.subTest(change=change), self.fixture() as fx:
                fx.show.side_effect = lambda unit: {**self.properties(unit), **change}
                with self.assertRaises(o.SystemdObservationError): self.observer.collect()

    def test_failed_visibility_and_show_keep_fixed_diagnostics(self):
        with self.fixture() as fx:
            fx.provenance.side_effect = PermissionError('private-path')
            with self.assertRaisesRegex(o.SystemdObservationError, '^SYSTEMD_OBSERVATION_UNAVAILABLE$'): self.observer.collect()
            fx.show.assert_not_called()
        with self.fixture() as fx:
            fx.show.side_effect = OSError('private-service')
            with self.assertRaisesRegex(o.SystemdObservationError, '^SYSTEMD_OBSERVATION_UNAVAILABLE$'): self.observer.collect()

    def test_repeat_comparison_checks_definitions_provenance_state_and_time(self):
        with self.fixture(external=True) as fx:
            first = self.observer.collect(); second = self.observer.collect(previous=first)
            inv = o.l.LauncherInventory(self.target, model.storage())
            old = inv.inspect(first.snapshot(self.target), now=int(time.time()))
            inv.inspect(second.snapshot(self.target), now=int(time.time()), previous=old)
            fx.show.side_effect = lambda unit: {**self.properties(unit), 'Job': '123'}
            with self.assertRaisesRegex(o.SystemdObservationError, 'OBSERVATION_CHANGED'): self.observer.collect(previous=first)
        for previous in ({}, o.SystemdSample(b'x'*(o.l.MAX_BYTES+1))):
            with self.fixture(), self.assertRaisesRegex(o.SystemdObservationError, 'PREVIOUS_REJECTED'):
                self.observer.collect(previous=previous)

    def test_expired_interval_or_clock_reversal_not_admitted(self):
        for stamps in ((100, 99), (100, 161)):
            with self.fixture(), patch.object(o.time, 'time', side_effect=stamps), \
                 self.assertRaisesRegex(o.SystemdObservationError, 'STALE'): self.observer.collect()
        with self.fixture(), patch.object(o.time, 'monotonic', side_effect=[0, 61]), \
             self.assertRaisesRegex(o.SystemdObservationError, 'STALE'): self.observer.collect()

    def test_show_command_is_fixed_read_only_and_parser_closed(self):
        unit = self.runtime.unit('php'); raw = ''.join(k+'='+v+'\n' for k,v in self.properties(unit).items()).encode()
        with patch.object(o.h.p, '_safe_path'), patch.object(o, '_capture', return_value=raw) as capture:
            self.assertEqual(o._show(unit), self.properties(unit)); argv = capture.call_args.args[0]
            self.assertEqual(argv[:5], ['/usr/bin/systemctl','--system','--no-pager','--no-ask-password','show'])
            self.assertEqual(argv[-2:], ['--',unit])
            for bad in (raw+b'Job=\n', raw+b'Other=secret\n', raw.replace(b'Job=\n',b''), b'\xff', b'x'*(o.MAX_OUTPUT+1)):
                capture.return_value = bad
                with self.assertRaises((o.SystemdObservationError, UnicodeError)): o._show(unit)
            for bad in ('apache2.service', '--help', '../unit', self.cleaner.timer+'.evil'):
                with self.assertRaises(o.SystemdObservationError): o._show(bad)

    def test_provenance_rejects_other_pid_or_mount_namespace_and_unreadable_identity(self):
        for kind in ('pid', 'mnt'):
            with patch.object(o.os, 'geteuid', return_value=0), \
                 patch.object(o.os, 'readlink', side_effect=lambda path: kind+':['+('1' if '/self/' in path else '2')+']'), \
                 self.assertRaisesRegex(o.SystemdObservationError, 'VISIBILITY_REQUIRED'): o._provenance()
        with patch.object(o.os, 'geteuid', return_value=1000), \
             self.assertRaisesRegex(o.SystemdObservationError, 'VISIBILITY_REQUIRED'): o._provenance()


class BoundedReaderTests(unittest.TestCase):
    def test_real_pipe_is_capped_during_read_and_child_is_reaped(self):
        original = subprocess.Popen; children = []
        def track(*args, **kwargs):
            children.append(original(*args, **kwargs)); return children[-1]
        with patch.object(o.subprocess, 'Popen', side_effect=track), \
             self.assertRaisesRegex(o.SystemdObservationError, 'OUTPUT_LIMIT'):
            o._capture([sys.executable, '-c', 'import os; os.write(1,b"x"*65536)'])
        self.assertIsNotNone(children[0].returncode); self.assertTrue(children[0].stdout.closed)

    def test_real_timeout_and_nonzero_are_not_empty_success(self):
        with patch.object(o, 'SHOW_SECONDS', 0.15), \
             self.assertRaisesRegex(o.SystemdObservationError, 'TIMEOUT'):
            o._capture([sys.executable, '-c', 'import time; time.sleep(5)'])
        with self.assertRaisesRegex(o.SystemdObservationError, 'UNREADABLE'):
            o._capture([sys.executable, '-c', 'raise SystemExit(1)'])
        self.assertEqual(o._capture([sys.executable, '-c', 'print("ok")']), b'ok\n')


if __name__ == '__main__': unittest.main()
