#!/usr/bin/env python3
"""Real invocation-bound relations; fixture mutations are never product actions."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import pwd
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import systemd_invocation_relations as x
from installer.storage_inventory import StorageRequirements, PRODUCERS
from systemd_invocation_systemd import command, until, UNIT_ROOT, pid, hint, absent, fds
sys.path.insert(0, str(ROOT/'scripts'))
import quality

A, B, Q = 'relation-source.service', 'relation-target.service', 'relation-extra.service'
ALIAS = 'relation-source-alias.service'
TIMER, END = 'relation-source.timer', 'relation-end.service'
VANISH, RESTART = 'relation-vanish.service', 'relation-restart.service'
UNLOADED = 'relation-never-loaded.service'
SECRET = 'description-not-for-manifest'

from discovery_diagnostics import DiagnosedCollect


class Audited(DiagnosedCollect, x.SystemdInvocationRelations):
    def __init__(self, *args): super().__init__(*args); self.detail_calls = []; self.returned = []
    def _relation_query(self, prop, binding, owner, budget):
        self.detail_calls.append((prop, binding.invocation_path))
        raw = super()._relation_query(prop, binding, owner, budget)
        self.returned.append(prop)
        return raw

class RelationsLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST') != '1' or os.geteuid() != 0 or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl', 'start', 'dbus.service')
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', 'hestia-relations-test')
        cls.account = pwd.getpwnam('hestia-relations-test')
        p = x.t.o._provenance(); release = x.d.l.get_release(x.d.l.STORAGE_COMMIT)
        cls.target = x.d.l.LauncherTarget('9'*32, release.commit, release.tree, '/srv/relations-fixture',
            '/var/lib/relations-fixture', '/var/lib/relations-fixture/maintenance', cls.account.pw_uid, cls.account.pw_gid,
            p['host_id'], p['boot_id'])
        cls.storage = StorageRequirements(json.dumps({'version': 1, 'source_commit': release.commit,
            'runtime_sha256': release.runtime_sha256,
            'scopes': [{'role': 'uploads', 'path': cls.target.webroot+'/uploads'},
                       {'role': 'managed_configuration', 'path': cls.target.configuration}],
            'producers': [{'group': group, 'state': 'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers': ['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        service = '[Unit]\nDescription='+SECRET+'\n[Service]\nUser=hestia-relations-test\nExecStart=/usr/bin/sleep infinity\n'
        cls.units = {name: service for name in (A, B, Q, VANISH, RESTART, UNLOADED)}
        cls.units[A] += '\n[Unit]\nRequires='+B+'\nWants='+B+'\nBindsTo='+B+'\nUpholds='+B+'\nOnSuccess='+END+'\nOnFailure='+END+'\n'
        cls.units[END] = '[Service]\nType=oneshot\nExecStart=/usr/bin/touch /run/relations-end-started\n'
        cls.units[TIMER] = '[Timer]\nOnActiveSec=1h\nUnit='+A+'\n'
        for name, raw in cls.units.items():
            path = UNIT_ROOT/name; path.write_text(raw); path.chmod(0o644)
        (UNIT_ROOT/ALIAS).symlink_to(A)
        command('systemctl', 'daemon-reload'); command('systemctl', 'start', A, B, Q, TIMER)
        command('systemctl', 'start', ALIAS)

    @classmethod
    def tearDownClass(cls):
        command('systemctl', 'stop', TIMER, check=False)
        for name in cls.units: command('systemctl', 'stop', name, check=False)
        for name in (*cls.units, ALIAS): (UNIT_ROOT/name).unlink(missing_ok=True)
        command('systemctl', 'daemon-reload')

    def reader(self): return Audited(self.target, self.storage)
    def sample(self): return self.reader().collect((hint(A),))

    def test_01_actual_alias_and_eight_properties_on_one_bound_invocation(self):
        h = hint(A); before = fds(); reader = self.reader(); sample = reader.collect((h,))
        self.assertEqual(sample.report()['bus_calls'], 50); self.assertEqual(fds(), before); self.assertEqual(pid(A), h.pid)
        details = sample.private_manifest()['properties'][0]
        self.assertIn(ALIAS, details['names']); self.assertIn(A, details['names'])
        self.assertEqual([p for p, _ in details['relations']], list(x.RELATIONS))
        self.assertEqual([p for p, _ in reader.detail_calls], list(x.PROPERTIES)*2)
        self.assertTrue(all(path == details['binding']['invocation_path'] and path != h.object_path for _, path in reader.detail_calls))
        self.assertNotIn(SECRET.encode(), sample._canonical); self.assertNotIn(A, json.dumps(sample.report()))
        self.assertFalse(Path('/run/relations-end-started').exists())

    def test_02_real_dependency_targets_bind_and_remain_unresolved_without_seed(self):
        sample = self.sample(); facts = sample.facts().units[0].relations
        expected = {'Requires': B, 'Wants': B, 'BindsTo': B, 'Upholds': B, 'OnSuccess': END, 'OnFailure': END, 'TriggeredBy': TIMER}
        rows = sample.index().private_manifest()['observation']['loaded_units']['rows']
        objects = {u['primary_name']: u['object_path'] for u in rows}
        for prop, name in expected.items():
            relation = next(f for f in facts if f.property == prop and f.target_name == name)
            self.assertEqual(relation.target_object, objects.get(name))
        self.assertTrue(all(u['decision'] == 'UNRESOLVED' and not u['enrolled'] for u in sample.selection().private_manifest()['loaded_units']))
        self.assertFalse(sample.report()['drain_allowed']); self.assertFalse(sample.report()['host_relevance_verified'])

    def test_03_timer_without_process_and_unloaded_file_are_never_property_targets(self):
        reader = self.reader(); sample = reader.collect((hint(A),))
        rows = sample.index().private_manifest()['observation']['loaded_units']['rows']
        timer = next(u for u in rows if u['primary_name'] == TIMER)
        self.assertIsNone(timer['names']); self.assertEqual(len(sample.facts().units), 1)
        self.assertTrue(absent(UNLOADED)); self.assertTrue((UNIT_ROOT/UNLOADED).is_file())
        self.assertEqual(len(reader.detail_calls), 18)
        self.assertTrue(all(path != timer['object_path'] for _, path in reader.detail_calls))

    def test_04_disappearance_before_names_refuses_without_reloading_file_unit(self):
        command('systemctl', 'start', VANISH); h = hint(VANISH); before = fds()
        class Vanishing(Audited):
            def _binding(self, *args):
                binding = super()._binding(*args)
                command('systemctl', 'stop', VANISH); until(lambda: absent(VANISH))
                return binding
        reader = Vanishing(self.target, self.storage)
        with self.assertRaisesRegex(x.t.SystemdTransportError, 'DISCOVERY_BUS_UNREADABLE'): reader.collect((h,))
        self.assertEqual([p for p, _ in reader.detail_calls], ['Names']); self.assertEqual(reader.returned, [])
        self.assertTrue(absent(VANISH)); self.assertTrue((UNIT_ROOT/VANISH).is_file()); self.assertEqual(fds(), before)

    def test_05_restart_after_names_invalidates_remaining_properties(self):
        command('systemctl', 'start', RESTART); h = hint(RESTART); before = fds()
        class Restarting(Audited):
            restarted = False
            def _relation_query(self, prop, *args):
                raw = super()._relation_query(prop, *args)
                if prop == 'Names' and not self.restarted:
                    self.restarted = True; command('systemctl', 'restart', RESTART)
                return raw
        reader = Restarting(self.target, self.storage)
        with self.assertRaisesRegex(x.t.SystemdTransportError, 'DISCOVERY_BUS_UNREADABLE'): reader.collect((h,))
        self.assertEqual(reader.returned, ['Names']); self.assertEqual([p for p, _ in reader.detail_calls], ['Names', 'Triggers'])
        self.assertEqual(fds(), before); self.assertNotEqual(pid(RESTART), h.pid)
        current = self.reader().collect((hint(RESTART),)).private_manifest()['properties'][0]
        self.assertNotEqual(current['binding']['invocation_path'], reader.detail_calls[0][1])

    def test_06_dependency_change_between_passes_refused_with_same_invocation(self):
        h = hint(A); before = fds(); original = (UNIT_ROOT/A).read_text()
        class Changing(Audited):
            def __init__(self, *args): super().__init__(*args); self.passes = []
            def _pass(self, *args):
                result = super()._pass(*args); self.passes.append(result)
                if len(self.passes) == 1:
                    (UNIT_ROOT/A).write_text(original+'\n[Unit]\nWants='+Q+'\n')
                    command('systemctl', 'daemon-reload')
                return result
        reader = Changing(self.target, self.storage)
        try:
            with self.assertRaisesRegex(x.t.SystemdTransportError, 'INVOCATION_CHANGED'): reader.collect((h,))
            self.assertEqual(len(reader.passes), 2)
            left, right = reader.passes[0][0], reader.passes[1][0]
            self.assertEqual(left.binding, right.binding); self.assertEqual(left.names, right.names)
            self.assertNotEqual(left.relations, right.relations)
            self.assertNotIn(Q, dict(left.relations)['Wants']); self.assertIn(Q, dict(right.relations)['Wants'])
            self.assertEqual(pid(A), h.pid); self.assertEqual(fds(), before)
        finally:
            (UNIT_ROOT/A).write_text(original); command('systemctl', 'daemon-reload')

    def test_07_two_units_are_bound_and_descriptors_are_closed(self):
        before = fds(); sample = self.reader().collect((hint(A), hint(B)))
        self.assertEqual(sample.report()['bus_calls'], 76); self.assertEqual(fds(), before)
        self.assertEqual(len(sample.facts().units), 2)
        self.assertEqual(len({p['binding']['invocation_id'] for p in sample.private_manifest()['properties']}), 2)

    def test_08_collected_edges_compose_with_separate_declaration_without_admission(self):
        sample = self.sample(); seed = x.r.ExternalBinding(self.target.instance, 'operator_catalog', 'c'*64)
        units = (replace(sample.facts().units[0], bindings=(seed,)),)
        selector = x.r.SystemdRelevance(self.target, self.storage)
        selection = selector.inspect(sample.scan(), replace(sample.facts(), units=units), now=sample.scan().finished_at)
        by_name = {u['primary_name']: u['object_path'] for u in sample.index().private_manifest()['observation']['loaded_units']['rows']}
        related = {u['object_path'] for u in selection.private_manifest()['loaded_units'] if u['decision'] == 'RELATED_UNMANAGED'}
        self.assertIn(by_name[A], related); self.assertIn(by_name[B], related); self.assertIn(by_name[TIMER], related)
        self.assertFalse(selection.report()['drain_allowed']); self.assertEqual(selection.report()['known_provisioned_units'], 0)
        self.assertIsNone(sample.facts().units[0].bindings)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RelationsLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite': 'Invocation-bound Names and eight relations', 'tests': result.testsRun, 'expected': 8,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 8 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(before), 'automatic_pid_census_qualified': False,
        'host_scheduler_inventory_complete': False, 'service_activation_delivered': False, 'phase5_complete': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent/'INVOCATION-RELATIONS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
