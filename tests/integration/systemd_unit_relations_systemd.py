#!/usr/bin/env python3
"""Selected object properties on actual systemd; disposable fixture only."""
import argparse
import json
import os
import pwd
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import systemd_unit_relations as u
from systemd_discovery_systemd import command, UNIT_ROOT, SECRET
from installer.storage_inventory import StorageRequirements, PRODUCERS

LIVE = "relations-live.service"
sys.path.insert(0, str(ROOT/'scripts'))
import quality

IDLE = 'relations-idle.service'
TIMER = 'relations-idle.timer'
PATH = 'relations-idle.path'
SOCKET = 'relations-idle.socket'
A = 'relations-cycle-a.target'
B = 'relations-cycle-b.target'
ALIAS = 'relations-alias.service'


class AuditedRelations(u.SystemdUnitRelations):
    def __init__(self, *args): super().__init__(*args); self.properties = []
    def _property(self, owner, obj, primary, prop, budget):
        self.properties.append((obj, prop))
        return super()._property(owner, obj, primary, prop, budget)


class UnitRelationsLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST') != '1' or os.geteuid() != 0 or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl','start','dbus.service')
        command('useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin','hestia-relations-test')
        account = pwd.getpwnam('hestia-relations-test')
        p = u.t.o._provenance(); release = u.d.l.get_release(u.d.l.STORAGE_COMMIT)
        cls.target = u.d.l.LauncherTarget('8'*32,release.commit,release.tree,'/srv/relations-fixture',
            '/var/lib/relations-fixture','/var/lib/relations-fixture/maintenance',account.pw_uid,account.pw_gid,p['host_id'],p['boot_id'])
        cls.storage = StorageRequirements(json.dumps({'version':1,'source_commit':release.commit,'runtime_sha256':release.runtime_sha256,
            'scopes':[{'role':'uploads','path':cls.target.webroot+'/uploads'},{'role':'managed_configuration','path':cls.target.configuration}],
            'producers':[{'group':group,'state':'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers':['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        extra = {
            LIVE: '[Unit]\nDescription='+SECRET+'\n[Service]\nUser=hestia-relations-test\nExecStart=/usr/bin/sleep infinity\n',
            'relations-unloaded.service': '[Service]\nExecStart=/usr/bin/touch /run/discovery-unexpected-start\n[Install]\nWantedBy=multi-user.target\n',
            'relations-unused@.service': '[Service]\nExecStart=/usr/bin/sleep infinity\n',
            IDLE: '[Unit]\nRequires='+A+'\nWants='+B+'\nBindsTo='+A+'\nUpholds='+B+'\nOnSuccess='+A+'\nOnFailure='+B+'\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/touch /run/relations-unexpected-start\n',
            TIMER: '[Timer]\nOnActiveSec=1d\nUnit='+IDLE+'\n',
            PATH: '[Path]\nPathExists=/run/relations-never-created\nUnit='+IDLE+'\n',
            SOCKET: '[Socket]\nListenStream=/run/relations-never-connected.sock\nService='+IDLE+'\n',
            A: '[Unit]\nDefaultDependencies=no\nWants='+B+'\n', B: '[Unit]\nDefaultDependencies=no\nWants='+A+'\n',
        }
        for name, raw in extra.items(): (UNIT_ROOT/name).write_text(raw); (UNIT_ROOT/name).chmod(0o644)
        cls.units = extra
        (UNIT_ROOT/ALIAS).symlink_to(LIVE)
        (UNIT_ROOT/'relations-blocked@.service').symlink_to('/dev/null')
        command('systemctl', 'daemon-reload')
        command('systemctl', 'start', ALIAS, TIMER, PATH, SOCKET, A)

    @classmethod
    def tearDownClass(cls):
        for name in cls.units: command('systemctl','stop',name,check=False)
        for name in (*cls.units,ALIAS,'relations-blocked@.service'): (UNIT_ROOT/name).unlink(missing_ok=True)
        command('systemctl','daemon-reload')

    def reader(self): return AuditedRelations(self.target, self.storage)
    def selection(self, *names):
        rows = u.t.SystemdDiscoveryTransport(self.target, self.storage).collect().index().private_manifest()['observation']['loaded_units']['rows']
        objects = {row['primary_name']: row['object_path'] for row in rows}
        return tuple(objects[name] for name in names)

    def test_01_actual_properties_fixed_calls_and_no_mutation(self):
        reader = self.reader(); selected = self.selection(LIVE)
        pid = command('systemctl','show','--value','--property=MainPID',LIVE).stdout
        sample = reader.collect(selected)
        self.assertEqual(sample.report()['bus_calls'],44)
        self.assertEqual(reader.properties, [(selected[0], name) for name, _, _ in u._spec(LIVE)]*2)
        self.assertEqual(pid,command('systemctl','show','--value','--property=MainPID',LIVE).stdout)
        self.assertNotIn(SECRET.encode(),sample._canonical)
        self.assertFalse(sample.report()['drain_allowed']); self.assertFalse(sample.report()['phase5_complete'])

    def test_02_timer_path_socket_activation_targets_stay_inactive(self):
        selected = self.selection(TIMER, PATH, SOCKET, IDLE)
        sample = self.reader().collect(selected)
        facts = {row.primary_name: row for row in sample.facts().units}
        for name in (TIMER,PATH,SOCKET):
            matches = [row for row in facts[name].relations if row.target_name == IDLE]
            self.assertTrue(matches)
            self.assertTrue(all(row.target_object == selected[3] for row in matches))
            props = {row.property for row in matches}
            self.assertIn('Triggers',props)
            if name != SOCKET: self.assertIn('Unit',props)
            else: self.assertNotIn('Unit',props)
        self.assertTrue({row.target_name for row in facts[IDLE].relations if row.property == 'TriggeredBy'} >= {TIMER,PATH,SOCKET})
        for prop in ('Requires','Wants','BindsTo','Upholds','OnSuccess','OnFailure'):
            self.assertTrue(any(row.property == prop and row.target_name in (A,B) for row in facts[IDLE].relations),prop)
        self.assertFalse(Path('/run/relations-unexpected-start').exists())
        self.assertEqual(command('systemctl','show','--value','--property=ActiveState',IDLE).stdout.strip(),b'inactive')

    def test_03_alias_names_are_bound_to_one_loaded_object(self):
        selected = self.selection(LIVE); sample = self.reader().collect(selected)
        rows = sample.index().private_manifest()['observation']['loaded_units']['rows']
        row = next(row for row in rows if row['object_path'] == selected[0])
        self.assertIn(ALIAS,row['names']); self.assertIn(LIVE,row['names'])
        self.assertEqual(sum(ALIAS in (row['names'] or []) for row in rows),1)

    def test_04_real_cycle_does_not_classify_units_as_writers(self):
        reader = self.reader(); sample = reader.collect(self.selection(A,B))
        facts = {row.primary_name: row for row in sample.facts().units}
        for name, target in ((A,B),(B,A)):
            self.assertTrue(any(row.property == 'Wants' and row.target_name == target for row in facts[name].relations))
        result = reader._relevance.inspect(sample.scan(),sample.facts(),now=sample.scan().finished_at).report()
        self.assertEqual(result['related_loaded_units'],0); self.assertEqual(result['known_provisioned_units'],0)

    def test_05_unselected_units_and_unloaded_templates_remain_unknown(self):
        sample = self.reader().collect(self.selection(LIVE))
        data = sample.index().private_manifest()['observation']
        self.assertTrue(all(row['names'] is None for row in data['loaded_units']['rows'] if row['primary_name'] != LIVE))
        loaded = {row['primary_name'] for row in data['loaded_units']['rows']}
        files = {row['listed_name'] for row in data['installed_unit_files']['rows']}
        for name in ('relations-unloaded.service','relations-unused@.service','relations-blocked@.service'):
            self.assertIn(name,files); self.assertNotIn(name,loaded)
        self.assertFalse(Path('/run/discovery-unexpected-start').exists())
        self.assertIn('UNIT_SELECTION_PARTIAL',sample.report()['blockers'])

    def test_06_absent_object_never_receives_property_or_resolver_call(self):
        reader = self.reader()
        with self.assertRaisesRegex(u.SystemdRelationsError,'NOT_LISTED'):
            reader.collect((u.d.UNIT_PREFIX+'not_2dpresent_2eservice',))
        self.assertEqual(reader.properties,[])

    def test_07_actual_alias_change_between_reads_is_refused(self):
        alias = UNIT_ROOT/'relations-new-alias.service'; selected = self.selection(LIVE)
        class Changed(AuditedRelations):
            rounds = 0
            def _details(self, *args):
                result = super()._details(*args); self.rounds += 1
                if self.rounds == 1:
                    alias.symlink_to(LIVE)
                    command('systemctl','daemon-reload')
                    command('systemctl','start',alias.name)
                return result
        try:
            with self.assertRaisesRegex(u.SystemdRelationsError,'RELATIONS_CHANGED'):
                Changed(self.target,self.storage).collect(selected)
        finally:
            alias.unlink(missing_ok=True); command('systemctl','daemon-reload')

    def test_08_actual_property_failure_is_not_empty_success(self):
        # The bench removes its own otherwise idle transient object after the
        # real first population. The reader must not reload it or retry by name.
        name = 'relations-vanishing.service'
        command('systemd-run','--unit='+name,'/usr/bin/sleep','infinity')
        selected = self.selection(name)
        class Vanished(AuditedRelations):
            def _round(self, budget):
                result = super()._round(budget)
                command('systemctl','stop',name)
                # systemctl stop completes the job, then the manager can GC it.
                from systemd_discovery_systemd import until
                until(lambda: name.encode() not in command('systemctl','list-units','--all','--plain','--no-legend').stdout)
                return result
        reader = Vanished(self.target,self.storage)
        with self.assertRaises(u.SystemdRelationsError): reader.collect(selected)
        self.assertEqual(reader.properties,[(selected[0],'Id')])
        self.assertNotIn(name.encode(),command('systemctl','list-units','--all','--plain','--no-legend').stdout)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report',type=Path,required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UnitRelationsLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite':'Read-only selected unit Names and activation relations','tests':result.testsRun,'expected':8,
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun == 8 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(before),'web_application_qualified':False,
        'business_profile_bridge_system_qualified':False,'host_scheduler_inventory_complete':False,
        'service_activation_delivered':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    (args.report.parent/'UNIT-RELATIONS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report,indent=2)+'\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
