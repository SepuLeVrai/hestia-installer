#!/usr/bin/env python3
"""Census-owned thread PIDFDs and real systemd mappings, disposable fixture only."""
import argparse
import json
import os
from pathlib import Path
import pwd
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from installer import systemd_census_invocations as z
from installer.storage_inventory import StorageRequirements,PRODUCERS
from systemd_invocation_systemd import command,until,UNIT_ROOT,fds
from process_census_systemd import HELPER
sys.path.insert(0,str(ROOT/'scripts'));import quality
WORK=Path('/run/hestia-census-invocation-fixture')
NAMES={key:'census-invocation-'+key+'.service' for key in ('stable','zombie','change','move','birth','exit','restart','other')}


class CensusInvocationLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST')!='1' or os.geteuid()!=0 or Path('/proc/1/comm').read_text().strip()!='systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl','start','dbus.service')
        for name in ('hestia-census-binding','hestia-census-binding-other'):
            command('useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin',name)
        cls.account=pwd.getpwnam('hestia-census-binding');cls.other=pwd.getpwnam('hestia-census-binding-other')
        context=z.t.o._provenance();release=z.d.l.get_release(z.d.l.STORAGE_COMMIT)
        cls.target=z.d.l.LauncherTarget('9'*32,release.commit,release.tree,'/srv/census-binding-fixture',
            '/var/lib/census-binding-fixture','/var/lib/census-binding-fixture/maintenance',
            cls.account.pw_uid,cls.account.pw_gid,context['host_id'],context['boot_id'])
        cls.storage=StorageRequirements(json.dumps({'version':1,'source_commit':release.commit,'runtime_sha256':release.runtime_sha256,
            'scopes':[{'role':'uploads','path':cls.target.webroot+'/uploads'},{'role':'managed_configuration','path':cls.target.configuration}],
            'producers':[{'group':group,'state':'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers':['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        WORK.mkdir(mode=0o755);(WORK/'helper.py').write_text(HELPER)
        for key,name in NAMES.items():
            argv='/usr/bin/sleep infinity' if key=='other' else '/usr/bin/python3 '+str(WORK/'helper.py')+' '+str(WORK)+' '+' '.join(str(x) for x in (cls.account.pw_uid,cls.account.pw_gid,cls.other.pw_uid,cls.other.pw_gid))+' '+key
            (UNIT_ROOT/name).write_text('[Service]\nExecStart='+argv+'\nKillMode=control-group\nTimeoutStopSec=5\n')
        command('systemctl','daemon-reload');cls.start('stable');command('systemctl','start',NAMES['other'])
    @classmethod
    def start(cls,key):
        command('systemctl','start',NAMES[key]);until(lambda:(WORK/(key+'-ready.json')).exists())
        return json.loads((WORK/(key+'-ready.json')).read_text())
    @classmethod
    def tearDownClass(cls):
        for name in NAMES.values():command('systemctl','stop',name,check=False);(UNIT_ROOT/name).unlink(missing_ok=True)
        command('systemctl','daemon-reload')
        for path in WORK.iterdir():path.unlink()
        WORK.rmdir()
    def reader(self):return z.SystemdCensusInvocations(self.target,self.storage)
    def stable(self):return json.loads((WORK/'stable-ready.json').read_text())

    def test_01_thread_signal_and_three_leaders_share_one_actual_invocation(self):
        fixture=self.stable();sample=self.reader().collect();data=sample.private_manifest()
        unit=next(u for u in data['units'] if u['primary_name']==NAMES['stable'])
        self.assertEqual(unit['candidate_leaders'],sorted([fixture['pid'],*fixture['children']]))
        bindings=[b for b in data['bindings'] if b['primary_name']==NAMES['stable']]
        self.assertEqual(len(bindings),3);self.assertEqual(len({b['invocation_id'] for b in bindings}),1)
        rows={r['tid']:r for r in data['census']['tasks']}
        self.assertEqual(rows[fixture['pid']]['identity']['uids'],[0]*4)
        self.assertEqual(rows[fixture['tid']]['identity']['uids'],[0,0,0,self.account.pw_uid])
        self.assertIn('UID_MATCH_IN_TASK',unit['review_reasons']);self.assertIn('DESCENDANT_AT_OBSERVATION',unit['review_reasons'])
        self.assertFalse(sample.report()['all_task_unit_memberships_observed'])
        self.assertEqual(sample.report()['bus_calls'],24+8*len(data['bindings']))

    def test_02_zombie_unbound_and_nonmatching_unit_still_retained(self):
        fixture=self.start('zombie');child=fixture['children'][0]
        until(lambda:Path('/proc/'+str(child)+'/stat').read_text().rsplit(') ',1)[1].startswith('Z '))
        sample=self.reader().collect();data=sample.private_manifest()
        unknown=next(r for r in data['unbound'] if r['tgid']==child)
        self.assertEqual(unknown['issue'],'LEADER_IDENTITY_UNAVAILABLE')
        self.assertIn('UNRESOLVED_TASK',unknown['reasons'])
        row=next(r for r in data['census']['tasks'] if r['tid']==child)
        self.assertIsNone(row['identity']);self.assertEqual(row['issue'],'TASK_EXITED_NOT_REAPED')
        other=int(command('systemctl','show','--value','--property=MainPID',NAMES['other']).stdout)
        self.assertIn(other,{r['tid'] for r in data['census']['tasks']})
        self.assertNotIn(other,dict(data['census']['candidates']))
        self.assertFalse(sample.report()['automatic_exclusion_allowed'])

    def test_03_same_live_descriptors_owned_through_queries_then_all_closed(self):
        seen={};owned_set={};before=fds()
        class Audited(z.SystemdCensusInvocations):
            def _between(self,rows,owned,*args):
                owned_set.update(owned);return super()._between(rows,owned,*args)
            def _binding(self,pid,fd,*args):
                self_pid=Path('/proc/self/fdinfo/'+str(fd)).read_text()
                self_outer.assertEqual(int(next(x for x in self_pid.splitlines() if x.startswith('Pid:')).split()[1]),pid)
                self_outer.assertEqual(fd,owned_set[pid]);seen.setdefault(pid,[]).append(fd)
                return super()._binding(pid,fd,*args)
        self_outer=self;sample=Audited(self.target,self.storage).collect();self.assertEqual(fds(),before)
        self.assertTrue(seen);self.assertTrue(all(len(x)==2 and x[0]==x[1] for x in seen.values()))
        for fd in owned_set.values():
            with self.assertRaises(OSError):os.fstat(fd)
        positives={'visible_task_enumeration_observed','system_manager_lists_observed','candidate_leader_bindings_observed','systemd_bindings_observed'}
        for key,value in sample.report().items():
            if type(value) is bool:self.assertEqual(value,key in positives,key)
        self.assertNotIn('census-invocation',json.dumps(sample.report()));self.assertNotIn('tgid',sample.report())

    def mutation(self,key,action):
        fixture=self.start(key);before=fds()
        class Changing(z.SystemdCensusInvocations):
            changed=False
            def _binding(self,pid,*args):
                result=super()._binding(pid,*args)
                if pid==fixture['pid'] and not self.changed:
                    self.changed=True;action(fixture)
                return result
        reader=Changing(self.target,self.storage)
        with self.assertRaises(z.c.CensusError):reader.collect()
        self.assertTrue(reader.changed, 'Fixture must reach the actual mutation before rejection')
        self.assertEqual(fds(),before);return fixture

    def test_04_thread_fsuid_changes_during_bus_reads_refused(self):
        def action(fixture):
            (WORK/'change-change').touch();until(lambda:(WORK/'change-changed').exists())
        fixture=self.mutation('change',action)
        raw=Path('/proc/'+str(fixture['pid'])+'/task/'+str(fixture['tid'])+'/status').read_text()
        self.assertEqual(next(x for x in raw.splitlines() if x.startswith('Uid:')).split()[1:],['0']*4)

    def test_05_same_unit_different_cgroup_path_refused(self):
        def action(fixture):
            original=Path('/proc/'+str(fixture['pid'])+'/cgroup').read_text()[3:-1]
            nested=Path('/sys/fs/cgroup'+original)/'fixture-child';nested.mkdir()
            (nested/'cgroup.procs').write_text(str(fixture['pid']))
        fixture=self.mutation('move',action)
        self.assertTrue(Path('/proc/'+str(fixture['pid'])+'/cgroup').read_text().endswith('/fixture-child\n'))

    def test_06_thread_born_during_mapping_refused(self):
        def action(fixture):
            (WORK/'birth-birth').touch();until(lambda:(WORK/'birth-born').exists())
        fixture=self.mutation('birth',action)
        self.assertEqual(len(list(Path('/proc/'+str(fixture['pid'])+'/task').iterdir())),3)

    def test_07_thread_exit_with_live_leader_during_mapping_refused(self):
        def action(fixture):
            (WORK/'exit-exit').touch();until(lambda:(WORK/'exit-exited').exists())
        fixture=self.mutation('exit',action)
        self.assertTrue(Path('/proc/'+str(fixture['pid'])).exists())
        self.assertFalse(Path('/proc/'+str(fixture['pid'])+'/task/'+str(fixture['tid'])).exists())

    def test_08_restarted_unit_does_not_adopt_replacement_process(self):
        def action(fixture):command('systemctl','restart',NAMES['restart'])
        fixture=self.mutation('restart',action)
        new=int(command('systemctl','show','--value','--property=MainPID',NAMES['restart']).stdout)
        self.assertGreater(new,1);self.assertNotEqual(new,fixture['pid'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    before=quality.snapshot(ROOT);result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CensusInvocationLive));stable=before==quality.snapshot(ROOT)
    report={'suite':'Census candidates to live systemd invocations','tests':result.testsRun,'expected':8,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==8 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(before),'process_census_authenticated':False,'host_scheduler_inventory_complete':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True);(args.report.parent/'CENSUS-INVOCATIONS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if report['status']=='PASS' else 1)
