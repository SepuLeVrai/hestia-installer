#!/usr/bin/env python3
"""Actual census, invocation relations and conservative review in disposable Debian."""
import argparse
import json
import os
from pathlib import Path
import pwd
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from installer import systemd_census_relations as y
from installer.storage_inventory import StorageRequirements,PRODUCERS
from systemd_invocation_systemd import command,until,UNIT_ROOT,fds,absent,pid
from process_census_systemd import HELPER
sys.path.insert(0,str(ROOT/'scripts'));import quality
WORK=Path('/run/hestia-census-relations-fixture')
NAMES={key:'census-relations-'+key+'.service' for key in ('stable','zombie','change','vanish','restart','foreign','quiet','unloaded')}
TIMER='census-relations.timer';ALIAS='census-relations-alias.service'


class Audited(y.x.SystemdInvocationRelations):
    def __init__(self,*args):super().__init__(*args);self.properties=[];self.returned=[]
    def _relation_query(self,prop,binding,*args):
        self.properties.append((prop,binding.primary_name,binding.invocation_path))
        raw=super()._relation_query(prop,binding,*args);self.returned.append((prop,binding.primary_name))
        return raw


class CensusRelationsLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST')!='1' or os.geteuid()!=0 or Path('/proc/1/comm').read_text().strip()!='systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl','start','dbus.service')
        for name in ('hestia-census-relations','hestia-census-relations-other'):
            command('useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin',name)
        cls.account=pwd.getpwnam('hestia-census-relations');cls.other=pwd.getpwnam('hestia-census-relations-other')
        provenance=y.t.o._provenance();release=y.d.l.get_release(y.d.l.STORAGE_COMMIT)
        cls.target=y.d.l.LauncherTarget('9'*32,release.commit,release.tree,'/srv/census-relations-fixture',
            '/var/lib/census-relations-fixture','/var/lib/census-relations-fixture/maintenance',
            cls.account.pw_uid,cls.account.pw_gid,provenance['host_id'],provenance['boot_id'])
        cls.storage=StorageRequirements(json.dumps({'version':1,'source_commit':release.commit,'runtime_sha256':release.runtime_sha256,
            'scopes':[{'role':'uploads','path':cls.target.webroot+'/uploads'},{'role':'managed_configuration','path':cls.target.configuration}],
            'producers':[{'group':group,'state':'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers':['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        WORK.mkdir(mode=0o755);(WORK/'helper.py').write_text(HELPER)
        for key,name in NAMES.items():
            if key in ('foreign','quiet','unloaded'):
                raw='[Service]\nUser=hestia-census-relations-other\nExecStart=/usr/bin/sleep infinity\n'
            else:
                argv='/usr/bin/python3 '+str(WORK/'helper.py')+' '+str(WORK)+' '+' '.join(str(x) for x in (cls.account.pw_uid,cls.account.pw_gid,cls.other.pw_uid,cls.other.pw_gid))+' '+key
                raw='[Service]\nExecStart='+argv+'\nKillMode=control-group\nTimeoutStopSec=5\n'
            if key=='stable':raw+='\n[Unit]\nRequires='+NAMES['foreign']+'\nWants='+NAMES['foreign']+'\nBindsTo='+NAMES['foreign']+'\nUpholds='+NAMES['foreign']+'\n'
            (UNIT_ROOT/name).write_text(raw)
        (UNIT_ROOT/TIMER).write_text('[Timer]\nOnActiveSec=1h\nUnit='+NAMES['stable']+'\n')
        (UNIT_ROOT/ALIAS).symlink_to(NAMES['stable'])
        command('systemctl','daemon-reload');cls.start('stable');command('systemctl','start',NAMES['quiet'],TIMER,ALIAS)
    @classmethod
    def start(cls,key):
        command('systemctl','start',NAMES[key]);until(lambda:(WORK/(key+'-ready.json')).exists())
        return json.loads((WORK/(key+'-ready.json')).read_text())
    @classmethod
    def tearDownClass(cls):
        command('systemctl','stop',TIMER,check=False)
        for name in NAMES.values():command('systemctl','stop',name,check=False);(UNIT_ROOT/name).unlink(missing_ok=True)
        (UNIT_ROOT/TIMER).unlink();(UNIT_ROOT/ALIAS).unlink();command('systemctl','daemon-reload')
        for path in WORK.iterdir():path.unlink()
        WORK.rmdir()
    def reader(self,transport=Audited):
        reader=y.SystemdCensusRelations(self.target,self.storage);reader._transport=transport(self.target,self.storage);return reader
    def stable(self):return json.loads((WORK/'stable-ready.json').read_text())

    def test_01_three_leaders_one_invocation_two_property_sets(self):
        reader=self.reader();sample=reader.collect();fixture=self.stable();data=sample.private_manifest()
        detail=next(p for p in data['properties'] if p['binding']['primary_name']==NAMES['stable'])
        self.assertIn(ALIAS,detail['names']);self.assertEqual(len(data['bindings']),3)
        self.assertEqual(len(data['properties']),1);self.assertEqual(sample.report()['bus_calls'],66)
        self.assertEqual([p for p,_,_ in reader._transport.properties],list(y.x.PROPERTIES)*2)
        self.assertTrue(all(path==detail['binding']['invocation_path'] for _,_,path in reader._transport.properties))
        self.assertEqual({f.process.pid for f in sample.facts().units[0].candidates},{fixture['pid'],*fixture['children']})

    def test_02_real_thread_and_descendants_expand_dependencies_and_timer(self):
        sample=self.reader().collect();data=sample.private_manifest();rows={r['primary_name']:r for r in sample.index().private_manifest()['observation']['loaded_units']['rows']}
        selected={r['object_path']:r for r in sample.selection().private_manifest()['loaded_units']}
        for name in (NAMES['stable'],NAMES['foreign'],TIMER):self.assertEqual(selected[rows[name]['object_path']]['decision'],'RELATED_UNMANAGED')
        self.assertIsNone(rows[TIMER]['names']);self.assertTrue(absent(NAMES['unloaded']))
        self.assertIsNone(sample.facts().units[0].identities);self.assertIsNone(sample.facts().units[0].bindings)
        self.assertIn('CENSUS_UID_MATCH_IN_TASK',selected[rows[NAMES['stable']]['object_path']]['reasons'])
        self.assertNotIn('EFFECTIVE_IDENTITY_MATCH',selected[rows[NAMES['stable']]['object_path']]['reasons'])
        self.assertEqual(selected[rows[NAMES['quiet']]['object_path']]['decision'],'UNRESOLVED')
        self.assertTrue(all(r['decision']=='UNRESOLVED' for r in data['selection']['installed_unit_files']))

    def test_03_zombie_remains_unbound_without_fabricated_fact(self):
        fixture=self.start('zombie');child=fixture['children'][0]
        until(lambda:Path('/proc/'+str(child)+'/stat').read_text().rsplit(') ',1)[1].startswith('Z '))
        sample=self.reader().collect();data=sample.private_manifest()
        self.assertIn(child,{r['tgid'] for r in data['unbound']})
        self.assertNotIn(child,{f.process.pid for u in sample.facts().units for f in u.candidates})
        row=next(r for r in data['census']['tasks'] if r['tid']==child)
        self.assertIsNone(row['identity']);self.assertEqual(row['issue'],'TASK_EXITED_NOT_REAPED')

    def test_04_changed_relations_same_invocation_refused(self):
        original=(UNIT_ROOT/NAMES['stable']).read_text();passes=[]
        class Changing(y.SystemdCensusRelations):
            def _properties(self,*args):
                details=super()._properties(*args);passes.append(details)
                if len(passes)==1:
                    (UNIT_ROOT/NAMES['stable']).write_text(original+'\n[Unit]\nWants='+NAMES['quiet']+'\n')
                    command('systemctl','daemon-reload')
                return details
        before=fds()
        try:
            with self.assertRaises(y.c.CensusError):Changing(self.target,self.storage).collect()
            self.assertEqual(len(passes),2)
            left,right=(next(d for d in batch if d.binding.primary_name==NAMES['stable']) for batch in passes)
            self.assertEqual(left.binding,right.binding);self.assertEqual(left.names,right.names)
            self.assertNotIn(NAMES['quiet'],dict(left.relations)['Wants']);self.assertIn(NAMES['quiet'],dict(right.relations)['Wants'])
            self.assertEqual(fds(),before)
        finally:(UNIT_ROOT/NAMES['stable']).write_text(original);command('systemctl','daemon-reload')

    def mutation(self,key,action):
        fixture=self.start(key);changed=[];before=fds()
        class Mutating(Audited):
            def _relation_query(self,prop,binding,*args):
                raw=super()._relation_query(prop,binding,*args)
                if prop=='Names' and binding.primary_name==NAMES[key] and not changed:
                    changed.append(True);action(fixture)
                return raw
        reader=self.reader(Mutating)
        with self.assertRaises(y.c.CensusError):reader.collect()
        self.assertEqual(changed,[True]);self.assertEqual(fds(),before)
        return fixture,reader

    def test_05_thread_fsuid_change_during_properties_refused(self):
        def action(fixture):
            (WORK/'change-change').touch();until(lambda:(WORK/'change-changed').exists())
        fixture,_=self.mutation('change',action)
        raw=Path('/proc/'+str(fixture['pid'])+'/task/'+str(fixture['tid'])+'/status').read_text()
        self.assertEqual(next(x for x in raw.splitlines() if x.startswith('Uid:')).split()[1:],['0']*4)

    def test_06_disappearance_during_properties_has_no_named_reload(self):
        def action(fixture):command('systemctl','stop',NAMES['vanish']);until(lambda:absent(NAMES['vanish']))
        _,reader=self.mutation('vanish',action)
        self.assertTrue(absent(NAMES['vanish']));self.assertTrue((UNIT_ROOT/NAMES['vanish']).is_file())
        calls=[p for p,name,_ in reader._transport.properties if name==NAMES['vanish']]
        self.assertEqual(calls,['Names','Triggers'])
        self.assertEqual([p for p,name in reader._transport.returned if name==NAMES['vanish']],['Names'])

    def test_07_restart_keeps_old_property_path_unusable(self):
        def action(fixture):command('systemctl','restart',NAMES['restart'])
        fixture,reader=self.mutation('restart',action);new=pid(NAMES['restart'])
        self.assertNotEqual(new,fixture['pid']);self.assertGreater(new,1)
        self.assertEqual([p for p,name in reader._transport.returned if name==NAMES['restart']],['Names'])
        def ready():
            try:return json.loads((WORK/'restart-ready.json').read_text())['pid']==new
            except (OSError,ValueError):return False
        until(ready)

    def test_08_closed_fds_private_reports_and_all_authority_flags_false(self):
        before=fds();reader=self.reader();sample=reader.collect();self.assertEqual(fds(),before)
        report=sample.report();data=sample.private_manifest()
        self.assertEqual(report['bus_calls'],24+8*len(data['bindings'])+18*len(data['properties']))
        self.assertEqual(sample.facts().census_sha256,y.d.l._sha(y.d._json(data['census'])))
        true={'visible_task_enumeration_observed','system_manager_lists_observed','candidate_leader_bindings_observed',
            'systemd_bindings_observed','selected_relations_observed','census_candidate_signals_observed'}
        for key,value in report.items():
            if type(value) is bool:self.assertEqual(value,key in true,key)
        self.assertEqual(report['known_provisioned_units'],0);self.assertFalse(report['drain_allowed'])
        self.assertTrue(all(not row['enrolled'] for row in sample.selection().private_manifest()['loaded_units']))
        self.assertNotIn('census-relations',json.dumps(report));self.assertNotIn('pid_hint',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    before=quality.snapshot(ROOT);result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CensusRelationsLive));stable=before==quality.snapshot(ROOT)
    report={'suite':'Live census invocation relations and review','tests':result.testsRun,'expected':8,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==8 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(before),'process_census_authenticated':False,'host_scheduler_inventory_complete':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True);(args.report.parent/'CENSUS-RELATIONS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if report['status']=='PASS' else 1)
