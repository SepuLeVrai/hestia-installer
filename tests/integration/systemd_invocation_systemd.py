#!/usr/bin/env python3
"""Real PIDFD D-Bus binding in a disposable Debian 13 system manager."""
import argparse
import json
import os
from pathlib import Path
import pwd
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import systemd_invocation as v
from systemd_discovery_systemd import command, until, UNIT_ROOT
from installer.storage_inventory import StorageRequirements, PRODUCERS
sys.path.insert(0, str(ROOT/'scripts'))
import quality

OTHER = 'invocation-other.service'
LIVE = 'invocation-live.service'
SECRET = 'fixture-private-description-not-for-envelope'
VANISH = 'invocation-vanish.service'
RESTART = 'invocation-restart.service'
EXIT = 'invocation-exit.service'


def pid(name): return int(command('systemctl', 'show', '--value', '--property=MainPID', name).stdout)
def loaded():
    raw = command(
        v.t.BUSCTL, '--system', '--address=unix:path='+v.t.SOCKET, '--json=short', '--auto-start=no',
        '--allow-interactive-authorization=no', 'call', v.t.MANAGER, v.t.MANAGER_PATH, v.t.INTERFACE, 'ListUnits').stdout
    return v.t._population('ListUnits', raw).rows

def hint(name):
    row = next(x for x in loaded() if x.primary_name == name)
    return v.InvocationHint(row.object_path, pid(name))

def absent(name): return all(x.primary_name != name for x in loaded())
def fds(): return set(os.listdir('/proc/self/fd'))


class Audited(v.SystemdInvocationTransport):
    def __init__(self, *args): super().__init__(*args); self.operations = []; self.properties = []; self.returned = []
    def _invocation_query(self, operation, budget, owner, *, fd=None, identifier=None):
        self.operations.append(operation)
        if operation in ('Id', 'InvocationID'): self.properties.append(v._path(identifier))
        raw = super()._invocation_query(operation, budget, owner, fd=fd, identifier=identifier)
        self.returned.append(operation)
        return raw


class InvocationLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST') != '1' or os.geteuid() != 0 or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl', 'start', 'dbus.service')
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', 'hestia-invocation-test')
        cls.account = pwd.getpwnam('hestia-invocation-test')
        p = v.t.o._provenance(); release = v.d.l.get_release(v.d.l.STORAGE_COMMIT)
        cls.target = v.d.l.LauncherTarget('9'*32, release.commit, release.tree, '/srv/invocation-fixture',
            '/var/lib/invocation-fixture', '/var/lib/invocation-fixture/maintenance', cls.account.pw_uid, cls.account.pw_gid,
            p['host_id'], p['boot_id'])
        cls.storage = StorageRequirements(json.dumps({'version': 1, 'source_commit': release.commit,
            'runtime_sha256': release.runtime_sha256,
            'scopes': [{'role': 'uploads', 'path': cls.target.webroot+'/uploads'},
                       {'role': 'managed_configuration', 'path': cls.target.configuration}],
            'producers': [{'group': group, 'state': 'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers': ['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        cls.units = {}
        for name in (LIVE, OTHER, VANISH, RESTART, EXIT):
            cls.units[name] = '[Unit]\nDescription='+SECRET+'\n[Service]\nUser=hestia-invocation-test\nExecStart=/usr/bin/sleep infinity\n'
            path = UNIT_ROOT/name; path.write_text(cls.units[name]); path.chmod(0o644)
        command('systemctl', 'daemon-reload'); command('systemctl', 'start', LIVE, OTHER)

    @classmethod
    def tearDownClass(cls):
        for name in cls.units:
            command('systemctl', 'stop', name, check=False)
            (UNIT_ROOT/name).unlink(missing_ok=True)
        command('systemctl', 'daemon-reload')
    def transport(self): return Audited(self.target, self.storage)

    def test_01_real_format_pidfd_and_properties_bind_without_changing_service(self):
        h = hint(LIVE); before = fds(); reader = self.transport(); sample = reader.collect((h,))
        self.assertEqual(fds(), before); self.assertEqual(pid(LIVE), h.pid)
        self.assertEqual(sample.report()['bus_calls'], 32)
        self.assertEqual(reader.operations, ['GetUnitByPIDFD', 'GetUnitByInvocationID', 'Id', 'InvocationID']*2)
        binding = sample.private_manifest()['bindings'][0]
        self.assertEqual(binding['primary_name'], LIVE); self.assertEqual(binding['object_path'], h.object_path)
        self.assertEqual(reader.properties, [v._path(binding['invocation_id'])]*4)
        self.assertNotEqual(binding['invocation_path'], h.object_path)
        self.assertNotIn(SECRET.encode(), sample._canonical)
        for key in ('drain_allowed', 'phase5_complete', 'process_census_authenticated', 'effective_identities_observed'):
            self.assertFalse(sample.report()[key])
        self.assertIn('UNSELECTED_UNITS_UNKNOWN', sample.private_manifest()['limitations'])
        self.assertNotIn(LIVE, json.dumps(sample.report()))

    def test_02_two_units_keep_distinct_bindings_and_close_all_fds(self):
        hints = (hint(LIVE), hint(OTHER)); before = fds()
        sample = self.transport().collect(hints)
        self.assertEqual(fds(), before); self.assertEqual(sample.report()['bus_calls'], 40)
        bindings = sample.private_manifest()['bindings']
        self.assertEqual(len({b['invocation_id'] for b in bindings}), 2)
        self.assertEqual({b['pid_hint'] for b in bindings}, {h.pid for h in hints})

    def test_03_wrong_live_pid_rejected_without_properties_or_service_change(self):
        h = hint(LIVE); other = pid(OTHER); reader = self.transport(); before = fds()
        with self.assertRaisesRegex(v.t.SystemdTransportError, 'BINDING_MISMATCH'):
            reader.collect((v.InvocationHint(h.object_path, other),))
        self.assertEqual(reader.operations, ['GetUnitByPIDFD']); self.assertEqual(fds(), before)
        self.assertEqual(pid(LIVE), h.pid); self.assertEqual(pid(OTHER), other)

    def test_04_process_exit_after_mapping_refuses_before_invocation_lookup(self):
        command('systemctl', 'start', EXIT); h = hint(EXIT); before = fds()
        class Exiting(Audited):
            def _invocation_query(self, operation, *args, **kwargs):
                raw = super()._invocation_query(operation, *args, **kwargs)
                if operation == 'GetUnitByPIDFD': command('systemctl', 'stop', EXIT)
                return raw
        reader = Exiting(self.target, self.storage)
        with self.assertRaisesRegex(v.t.SystemdTransportError, 'PROCESS_EXITED'): reader.collect((h,))
        self.assertEqual(reader.operations, ['GetUnitByPIDFD']); self.assertEqual(fds(), before)
        until(lambda: absent(EXIT))

    def test_05_disappeared_file_backed_unit_is_not_loaded_by_property_read(self):
        command('systemctl', 'start', VANISH); h = hint(VANISH); before = fds()
        class Vanishing(Audited):
            stopped = False
            def _invocation_query(self, operation, *args, **kwargs):
                raw = super()._invocation_query(operation, *args, **kwargs)
                if operation == 'GetUnitByInvocationID' and not self.stopped:
                    self.stopped = True
                    command('systemctl', 'stop', VANISH)
                    until(lambda: absent(VANISH))
                return raw
        reader = Vanishing(self.target, self.storage)
        with self.assertRaisesRegex(v.t.SystemdTransportError, 'DISCOVERY_BUS_UNREADABLE'): reader.collect((h,))
        self.assertTrue(reader.stopped); self.assertEqual(reader.operations, ['GetUnitByPIDFD', 'GetUnitByInvocationID', 'Id'])
        self.assertEqual(reader.returned, ['GetUnitByPIDFD', 'GetUnitByInvocationID'])
        self.assertEqual(len(reader.properties), 1); self.assertNotEqual(reader.properties[0], h.object_path)
        self.assertTrue(absent(VANISH)); self.assertTrue((UNIT_ROOT/VANISH).is_file()); self.assertEqual(fds(), before)

    def test_06_restarted_invocation_cannot_satisfy_old_property_destination(self):
        command('systemctl', 'start', RESTART); h = hint(RESTART); before = fds()
        class Restarting(Audited):
            restarted = False
            def _invocation_query(self, operation, *args, **kwargs):
                raw = super()._invocation_query(operation, *args, **kwargs)
                if operation == 'GetUnitByInvocationID' and not self.restarted:
                    self.restarted = True; command('systemctl', 'restart', RESTART)
                return raw
        reader = Restarting(self.target, self.storage)
        with self.assertRaisesRegex(v.t.SystemdTransportError, 'DISCOVERY_BUS_UNREADABLE'): reader.collect((h,))
        self.assertEqual(reader.returned, ['GetUnitByPIDFD', 'GetUnitByInvocationID']); self.assertEqual(fds(), before)
        current = self.transport().collect((hint(RESTART),)).private_manifest()['bindings'][0]
        self.assertNotEqual(current['invocation_path'], reader.properties[0]); self.assertNotEqual(current['pid_hint'], h.pid)

    def test_07_unknown_selection_rejects_without_pidfd_query(self):
        h = hint(LIVE); reader = self.transport(); before = fds()
        with self.assertRaisesRegex(v.t.SystemdTransportError, 'NOT_LISTED'):
            reader.collect((v.InvocationHint(v.d.UNIT_PREFIX+'not_listed', h.pid),))
        self.assertEqual(reader.operations, []); self.assertEqual(fds(), before)

    def test_08_nonroot_is_refused_before_system_bus_use(self):
        code = 'import sys;sys.path.insert(0,'+repr(str(ROOT))+');from installer.systemd_discovery_transport import _local;_local()'
        result = command('runuser', '-u', self.account.pw_name, '--', 'python3', '-I', '-B', '-c', code, check=False)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(InvocationLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite': 'Read-only PIDFD invocation binding', 'tests': result.testsRun, 'expected': 8,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 8 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(before), 'automatic_pid_census_qualified': False,
        'host_scheduler_inventory_complete': False, 'service_activation_delivered': False, 'phase5_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent/'INVOCATION-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
