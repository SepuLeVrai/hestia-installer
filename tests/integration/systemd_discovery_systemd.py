#!/usr/bin/env python3
"""Real bus lists in a disposable systemd container; no Web/SQL pin attestation."""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import systemd_discovery_transport as t
from installer.storage_inventory import StorageRequirements, PRODUCERS
sys.path.insert(0, str(ROOT/'scripts'))
import quality

UNIT_ROOT = Path('/etc/systemd/system')
LIVE = 'outside-profile-live.service'
SECRET = 'fixture-private-description-not-for-envelope'


def command(*argv, check=True):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, timeout=15)


def until(predicate):
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.05)
    raise AssertionError('Disposable fixture did not reach expected state')


class AuditedTransport(t.SystemdDiscoveryTransport):
    def __init__(self, *args): super().__init__(*args); self.operations = []
    def _query(self, operation, budget, owner=None):
        self.operations.append(operation)
        return super()._query(operation, budget, owner)


class SystemdDiscoveryLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST') != '1' or os.geteuid() != 0 or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl', 'start', 'dbus.service')
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', 'hestia-discovery-test')
        cls.account = pwd.getpwnam('hestia-discovery-test')
        p = t.o._provenance(); release = t.d.l.get_release(t.d.l.STORAGE_COMMIT)
        cls.target = t.d.l.LauncherTarget('9'*32, release.commit, release.tree, '/srv/discovery-fixture',
            '/var/lib/discovery-fixture', '/var/lib/discovery-fixture/maintenance', cls.account.pw_uid, cls.account.pw_gid,
            p['host_id'], p['boot_id'])
        # Explicit fixture declarations, not a mocked collector or a claim that
        # the pinned Web is deployed. The transport never attests these paths.
        cls.storage = StorageRequirements(json.dumps({'version': 1, 'source_commit': release.commit,
            'runtime_sha256': release.runtime_sha256,
            'scopes': [{'role': 'uploads', 'path': cls.target.webroot+'/uploads'},
                       {'role': 'managed_configuration', 'path': cls.target.configuration}],
            'producers': [{'group': group, 'state': 'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers': ['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        cls.units = {
            LIVE: '[Unit]\nDescription='+SECRET+'\n[Service]\nUser=hestia-discovery-test\nExecStart=/usr/bin/sleep infinity\n',
            'outside-profile-unloaded.service': '[Service]\nExecStart=/usr/bin/touch /run/discovery-unexpected-start\n[Install]\nWantedBy=multi-user.target\n',
            'outside-profile-unused@.service': '[Service]\nExecStart=/usr/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n',
            'outside-profile-flip.service': '[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/true\n',
            'outside-profile-previous.service': '[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/true\n',
            'outside-profile-job.service': '[Service]\nType=notify\nTimeoutStartSec=infinity\nExecStart=/usr/bin/sleep infinity\n',
        }
        for name, raw in cls.units.items():
            path = UNIT_ROOT/name; path.write_text(raw); path.chmod(0o644)
        (UNIT_ROOT/'outside-profile-alias.service').symlink_to(LIVE)
        (UNIT_ROOT/'outside-profile-blocked@.service').symlink_to('/dev/null')
        command('systemctl', 'daemon-reload'); command('systemctl', 'start', LIVE)

    @classmethod
    def tearDownClass(cls):
        for name in (*cls.units, 'outside-profile-transient.service'):
            command('systemctl', 'stop', name, check=False)
        for name in (*cls.units, 'outside-profile-alias.service', 'outside-profile-blocked@.service'):
            (UNIT_ROOT/name).unlink(missing_ok=True)
        command('systemctl', 'daemon-reload')

    def transport(self): return AuditedTransport(self.target, self.storage)
    def sample(self): return self.transport().collect()
    def observations(self, sample): return sample.index().private_manifest()['observation']

    def test_01_actual_lists_provenance_and_fixed_read_only_call_set(self):
        transport = self.transport(); sample = transport.collect(); report = sample.report()
        self.assertEqual(report['bus_calls'], 24); self.assertTrue(report['system_manager_lists_observed'])
        expected = ['GetId', 'GetBrokerPID', 'GetNameOwner', 'GetConnectionUnixProcessID', 'GetConnectionUnixUser',
                    'Version', 'UnitPath', 'ListUnits', 'ListUnitFiles', 'ListJobs', 'GetNameOwner', 'GetId']*2
        self.assertEqual(transport.operations, expected)
        data = self.observations(sample)
        self.assertEqual(data['provenance']['host_id'], Path('/etc/machine-id').read_text().strip())
        self.assertTrue(data['provenance']['unit_paths']); self.assertGreater(report['response_bytes'], 0)
        self.assertFalse(report['phase5_complete']); self.assertFalse(report['drain_allowed'])
        self.assertNotIn(SECRET.encode(), sample._canonical)

    def test_02_foreign_live_service_retains_pid_and_never_enters_drain_scope(self):
        before = command('systemctl', 'show', '--value', '--property=MainPID', LIVE).stdout
        sample = self.sample(); rows = self.observations(sample)['loaded_units']['rows']
        row = next(row for row in rows if row['primary_name'] == LIVE)
        self.assertEqual(row['active_state'], 'active'); self.assertIsNone(row['names'])
        self.assertEqual(before, command('systemctl', 'show', '--value', '--property=MainPID', LIVE).stdout)
        self.assertFalse(sample.report()['execution_allowed'])

    def test_03_disabled_unloaded_masked_templates_are_listed_without_loading(self):
        sample = self.sample(); data = self.observations(sample)
        loaded = {row['primary_name'] for row in data['loaded_units']['rows']}
        files = {row['listed_name']: row for row in data['installed_unit_files']['rows']}
        for name in ('outside-profile-unloaded.service', 'outside-profile-unused@.service', 'outside-profile-blocked@.service'):
            self.assertIn(name, files); self.assertNotIn(name, loaded); self.assertIsNone(files[name]['loaded_object'])
        self.assertEqual(files['outside-profile-unloaded.service']['enablement'], 'disabled')
        self.assertEqual(files['outside-profile-blocked@.service']['enablement'], 'masked')
        self.assertFalse(Path('/run/discovery-unexpected-start').exists())

    def test_04_alias_file_is_not_adopted_as_second_loaded_object(self):
        data = self.observations(self.sample())
        self.assertEqual(sum(row['primary_name'] == LIVE for row in data['loaded_units']['rows']), 1)
        aliases = [row for row in data['installed_unit_files']['rows'] if row['listed_name'] == 'outside-profile-alias.service']
        self.assertEqual(len(aliases), 1); self.assertIsNone(aliases[0]['loaded_object'])

    def test_05_transient_service_is_kept_without_invented_fragment(self):
        command('systemd-run', '--unit=outside-profile-transient.service', '--property=Description='+SECRET, '/usr/bin/sleep', 'infinity')
        data = self.observations(self.sample())
        row = next(row for row in data['loaded_units']['rows'] if row['primary_name'] == 'outside-profile-transient.service')
        self.assertEqual(row['active_state'], 'active'); self.assertNotIn('fragment', row)

    def test_06_real_pending_job_survives_collection_and_matches_its_object(self):
        name = 'outside-profile-job.service'
        command('systemctl', 'start', '--no-block', name)
        until(lambda: command('systemctl', 'show', '--value', '--property=ActiveState', name).stdout.strip() == b'activating')
        # Keep this stable notify job until class cleanup, rather than induce GC races between other tests.
        data = self.observations(self.sample())
        unit = next(row for row in data['loaded_units']['rows'] if row['primary_name'] == name)
        job = next(row for row in data['manager_jobs']['rows'] if row['unit_name'] == name)
        self.assertGreater(job['identifier'], 0); self.assertEqual(unit['job']['identifier'], job['identifier'])
        self.assertEqual(unit['object_path'], job['unit_object_path'])
        self.assertEqual(command('systemctl', 'show', '--value', '--property=ActiveState', name).stdout.strip(), b'activating')

    def test_07_change_between_rounds_is_rejected_using_real_manager_reads(self):
        class ChangingTransport(AuditedTransport):
            def _round(self, budget):
                result = super()._round(budget)
                if len(self.operations) == 12: command('systemctl', 'start', 'outside-profile-flip.service')
                return result
        with self.assertRaises(t.SystemdTransportError): ChangingTransport(self.target, self.storage).collect()

    def test_08_new_state_invalidates_previous_real_sample(self):
        previous = self.sample()
        command('systemctl', 'start', 'outside-profile-previous.service')
        with self.assertRaises(t.SystemdTransportError): self.transport().collect(previous=previous)
        self.assertTrue(self.sample().report()['system_manager_lists_observed'])

    def test_09_nonroot_and_writable_client_are_refused_before_bus_use(self):
        code = 'import sys;sys.path.insert(0,'+repr(str(ROOT))+');from installer.systemd_discovery_transport import _local;_local()'
        result = command('runuser', '-u', self.account.pw_name, '--', 'python3', '-I', '-B', '-c', code, check=False)
        self.assertNotEqual(result.returncode, 0)
        client = Path(t.BUSCTL); mode = client.stat().st_mode & 0o777
        try:
            client.chmod(0o777); transport = self.transport()
            with self.assertRaises(t.SystemdTransportError): transport.collect()
            self.assertEqual(transport.operations, [])
        finally: client.chmod(mode)

    def test_10_absent_broker_is_refused_without_socket_activation(self):
        command('systemctl', 'stop', 'dbus.service')
        try:
            self.assertTrue(Path(t.SOCKET).is_socket())
            transport = self.transport()
            with self.assertRaises(t.SystemdTransportError): transport.collect()
            self.assertEqual(transport.operations, [])
            self.assertEqual(command('systemctl', 'show', '--value', '--property=ActiveState', 'dbus.service').stdout.strip(), b'inactive')
        finally: command('systemctl', 'start', 'dbus.service')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SystemdDiscoveryLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite': 'Read-only system manager discovery lists', 'tests': result.testsRun, 'expected': 10,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 10 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(before), 'web_application_qualified': False,
        'business_profile_bridge_system_qualified': False, 'host_scheduler_inventory_complete': False,
        'service_activation_delivered': False, 'phase5_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent/'DISCOVERY-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
