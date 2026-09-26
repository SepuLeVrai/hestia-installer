#!/usr/bin/env python3
"""Real read-only collection in disposable systemd; no full Web/SQL claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import systemd_observations as o
from session_cleaner_systemd import SessionCleanerLive
from http_runtime_systemd import command, until
sys.path.insert(0, str(ROOT / 'scripts'))
import quality


class SystemdObservationsLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_OBSERVATIONS_TEST') != '1' or os.geteuid() != 0 \
                or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell',
                '/usr/sbin/nologin', 'hestia-observe-test')
        cls.account = pwd.getpwnam('hestia-observe-test')
        cls.family = '8.4' if Path('/usr/sbin/php-fpm8.4').is_file() else '8.2'

    def setUp(self):
        self.fx = SessionCleanerLive('test_stage_keeps_timer_inactive_native_untouched_and_service_condition_closed')
        self.fx.account, self.fx.family = self.account, self.family
        self.fx.setUp(); self.addCleanup(self.fx.doCleanups); self.fx.stage()
        self.runtime, self.cleaner = self.fx.fx.runtime, self.fx.cleaner
        self.observer = o.SystemdObserver(self.runtime, cleaner=self.cleaner)

    def start_fixture(self):
        self.fx.fx.activate_fixture()
        command('systemctl', 'start', self.cleaner.timer)
        until(lambda: o.h.drain._show(self.cleaner.unit)['ActiveState'] == 'inactive')

    def test_real_inactive_scope_files_host_and_gate_are_unchanged(self):
        gate = self.fx.fx.scope.observe()
        sample = self.observer.collect(); content = sample.private_manifest()['content']
        self.assertEqual(sample.report()['units'], 4)
        self.assertEqual(o.SystemdObserver(self.runtime).collect().report()['units'], 2)
        self.assertEqual(content['provenance']['host_id'], Path('/etc/machine-id').read_text().strip())
        self.assertEqual(content['provenance']['boot_id'], Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        for row in content['units']:
            self.assertEqual(row['properties']['ActiveState'], 'inactive')
            for item in row['definitions']:
                self.assertEqual(item['sha256'], hashlib.sha256(Path(item['path']).read_bytes()).hexdigest())
        self.assertEqual(gate, self.fx.fx.scope.observe())
        self.assertEqual(sample.private_manifest()['content'], self.observer.collect(previous=sample).private_manifest()['content'])
        for key, value in sample.report().items():
            if type(value) is bool: self.assertFalse(value, key)
        self.assertEqual({str(p): p.read_bytes() for p in self.fx.fx.global_paths}, self.fx.fx.global_before)

    def test_running_http_and_armed_timer_are_observed_without_stopping(self):
        self.start_fixture(); sample = self.observer.collect()
        rows = {row['name']: row['properties'] for row in sample.private_manifest()['content']['units']}
        for role in ('apache', 'php'):
            unit = self.runtime.unit(role)
            self.assertEqual(rows[unit]['ActiveState'], 'active')
            self.assertNotEqual(rows[unit]['MainPID'], '0')
            self.assertEqual(rows[unit]['ControlGroup'], '/system.slice/'+unit)
            self.assertEqual(command('systemctl', 'show', '--value', '--property=MainPID', unit).stdout.decode().strip(), rows[unit]['MainPID'])
        self.assertEqual(rows[self.cleaner.timer]['ActiveState'], 'active')
        self.assertEqual(self.fx.fx.request('/')[0], 200)
        self.assertEqual(self.fx.fx.scope.observe()['state'], 'SERVING')

    def test_foreign_service_remains_outside_partial_scope_and_is_not_stopped(self):
        unit = 'hestia-foreign-' + self.fx.fx.instance + '.service'; self.fx.fx.units.append(unit)
        path = o.h.drain.UNIT_ROOT / unit
        path.write_text('[Service]\nExecStart=/usr/bin/sleep infinity\n'); path.chmod(0o644)
        command('systemctl', 'daemon-reload'); command('systemctl', 'start', unit)
        sample = self.observer.collect()
        self.assertNotIn(unit, [row['name'] for row in sample.private_manifest()['content']['units']])
        self.assertEqual(sample.report()['coverage'], 'partial')
        self.assertFalse(sample.report()['host_scheduler_inventory_complete'])
        self.assertEqual(command('systemctl', 'is-active', unit).stdout.strip(), b'active')

    def test_real_fragment_bytes_and_permissions_drift_are_closed_refusals(self):
        path = o.h.drain.UNIT_ROOT / self.runtime.unit('php'); before = path.read_bytes()
        try:
            path.write_bytes(before+b'\n# changed after provisioning\n')
            with self.assertRaisesRegex(o.SystemdObservationError, 'UNAVAILABLE'): self.observer.collect()
            path.write_bytes(before); path.chmod(0o666)
            with self.assertRaisesRegex(o.SystemdObservationError, 'UNAVAILABLE'): self.observer.collect()
        finally: path.write_bytes(before); path.chmod(0o644)
        self.assertEqual(self.fx.fx.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_loaded_extra_dropin_is_refused_without_adoption(self):
        path = o.h.drain.UNIT_ROOT / (self.runtime.unit('php')+'.d/60-foreign.conf')
        raw = b'[Service]\nEnvironment=FOREIGN_CONTEXT=1\n'; path.write_bytes(raw); path.chmod(0o644)
        try:
            command('systemctl', 'daemon-reload')
            with self.assertRaisesRegex(o.SystemdObservationError, 'DEFINITION_CHANGED'): self.observer.collect()
            self.assertEqual(path.read_bytes(), raw)
        finally: path.unlink(); command('systemctl', 'daemon-reload')

    def test_new_live_state_invalidates_previous_sample(self):
        previous = self.observer.collect(); self.start_fixture()
        with self.assertRaisesRegex(o.SystemdObservationError, 'OBSERVATION_CHANGED'):
            self.observer.collect(previous=previous)
        self.assertEqual(self.observer.collect().report()['units'], 4)
        self.assertEqual(self.fx.fx.request('/')[0], 200)

    def test_real_nonroot_visibility_refuses_before_inspection(self):
        code = 'import sys; sys.path.insert(0, ' + repr(str(ROOT)) + '); from installer.systemd_observations import _provenance; _provenance()'
        result = command('runuser', '-u', self.account.pw_name, '--', 'python3', '-I', '-B', '-c', code, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SYSTEMD_OBSERVATION_VISIBILITY_REQUIRED', result.stderr)

    def test_legacy_fixture_does_not_claim_pinned_business_web(self):
        sample = self.observer.collect()
        self.assertIsNone(sample.private_manifest()['content']['profile']['target'])
        with self.assertRaisesRegex(o.SystemdObservationError, 'TARGET_MISMATCH'): sample.snapshot(None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SystemdObservationsLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite': 'Read-only provisioned system manager observations', 'tests': result.testsRun,
        'expected': 8, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 8 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(before), 'web_application_qualified': False,
        'business_profile_bridge_system_qualified': False, 'host_scheduler_inventory_complete': False,
        'service_activation_delivered': False, 'phase5_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'OBSERVATIONS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
