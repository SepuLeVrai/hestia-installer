#!/usr/bin/env python3
"""Actual configured Service context in a disposable, headless Debian manager."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from installer import systemd_execution_context as e
from installer.storage_inventory import StorageRequirements,PRODUCERS
from systemd_invocation_systemd import command,until,UNIT_ROOT,fds,absent,pid,loaded
from process_census_systemd import HELPER
from discovery_diagnostics import DiagnosedCollect
sys.path.insert(0,str(ROOT/'scripts'));import quality
WORK=Path('/run/hestia-context-fixture');ACCOUNT='hestia-context';OTHER='hestia-context-other'
ROOTFS=Path('/var/lib/hestia-context-root-fixture')
NAMES={key:'context-'+key+'.service' for key in ('multi','change','vanish','restart','named','numeric','home','binds',
    'dynamic','dynamic-static','declared','root','unloaded')}
SCOPE='context-external.scope';TIMER='context-unused.timer'


class Observed(DiagnosedCollect,e.SystemdExecutionContext): pass


class Audited(e.ExecutionContextTransport):
    def __init__(self,*args):super().__init__(*args);self.queries=[];self.returned=[]
    def _context_query(self,prop,binding,*args):
        self.queries.append((prop,binding.primary_name,binding.invocation_path))
        raw=super()._context_query(prop,binding,*args);self.returned.append((prop,binding.primary_name));return raw


class ExecutionContextLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST')!='1' or os.geteuid()!=0 or Path('/proc/1/comm').read_text().strip()!='systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl','start','dbus.service')
        WORK.mkdir(mode=0o755)
        for name in (ACCOUNT,OTHER):
            command('useradd','--system','--user-group','--no-create-home','--home-dir',str(WORK/'home'),'--shell','/usr/sbin/nologin',name)
        cls.account=pwd.getpwnam(ACCOUNT);cls.other=pwd.getpwnam(OTHER)
        for name in ('home','source','destination','readonly','hidden'):(WORK/name).mkdir(mode=0o755)
        ROOTFS.mkdir(mode=0o755)
        mounts={'work_noexec':bool(os.statvfs(WORK).f_flag & os.ST_NOEXEC),
            'rootfs_noexec':bool(os.statvfs(ROOTFS).f_flag & os.ST_NOEXEC)}
        Path('/evidence/fixture-execution-mounts.json').write_text(json.dumps(mounts)+'\n')
        if mounts!={'work_noexec':True,'rootfs_noexec':False}:raise RuntimeError('Unexpected fixture execution mounts')
        (WORK/'source/data').write_text('fixture-binding-content');(WORK/'root.img').write_bytes(b'not-mounted-fixture-marker')
        (WORK/'helper.py').write_text(HELPER)
        p=e.t.o._provenance();release=e.d.l.get_release(e.d.l.STORAGE_COMMIT)
        cls.target=e.d.l.LauncherTarget('9'*32,release.commit,release.tree,'/srv/context-fixture',
            '/var/lib/context-fixture','/var/lib/context-fixture/maintenance',cls.account.pw_uid,cls.account.pw_gid,p['host_id'],p['boot_id'])
        cls.storage=StorageRequirements(json.dumps({'version':1,'source_commit':release.commit,'runtime_sha256':release.runtime_sha256,
            'scopes':[{'role':'uploads','path':cls.target.webroot+'/uploads'},{'role':'managed_configuration','path':cls.target.configuration}],
            'producers':[{'group':group,'state':'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers':['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        cls.units={}
        for key,name in NAMES.items():
            if key in ('multi','change','vanish','restart'):
                argv='/usr/bin/python3 '+str(WORK/'helper.py')+' '+str(WORK)+' '+' '.join(str(n) for n in
                    (cls.account.pw_uid,cls.account.pw_gid,cls.other.pw_uid,cls.other.pw_gid))+' '+key
                unit='[Service]\nExecStart='+argv+'\nKillMode=control-group\nTimeoutStopSec=5\n'
            else:unit='[Service]\nUser='+ACCOUNT+'\nGroup='+ACCOUNT+'\nExecStart=/usr/bin/sleep infinity\n'
            if key=='named':unit+='SupplementaryGroups='+OTHER+'\nWorkingDirectory=-'+str(WORK/'absent')+'\n'
            if key=='numeric':unit='[Service]\nUser='+str(cls.account.pw_uid)+'\nGroup='+str(cls.account.pw_gid)+'\nSupplementaryGroups='+str(cls.other.pw_gid)+'\nExecStart=/usr/bin/sleep infinity\n'
            if key=='home':unit+='WorkingDirectory=~\n'
            if key=='binds':unit+='PrivateTmp=disconnected\nPrivateMounts=yes\nBindPaths='+str(WORK/'source')+':'+str(WORK/'destination')+'\nBindReadOnlyPaths='+str(WORK/'source')+':'+str(WORK/'readonly')+'\nReadWritePaths=-'+str(WORK/'absent-rw')+'\nReadOnlyPaths='+str(WORK/'source')+'\nInaccessiblePaths='+str(WORK/'hidden')+'\n'
            if key=='dynamic':unit='[Service]\nUser=hestia-context-dynamic\nDynamicUser=yes\nSupplementaryGroups='+ACCOUNT+'\nExecStart=/usr/bin/sleep infinity\n'
            if key=='dynamic-static':unit+='DynamicUser=yes\n'
            if key=='root':unit+='RootDirectory='+str(ROOTFS)+'\nRootDirectoryStartOnly=yes\n'
            unit=unit.replace('[Service]\n','[Service]\nType=exec\n',1)
            cls.units[key]=unit;(UNIT_ROOT/name).write_text(unit)
        # Fixture-only minimal rootfs from the already installed official sleep
        # and its loader/libraries. Never an Installer feature or host deployment.
        dependencies=command('ldd','/usr/bin/sleep').stdout.decode()
        paths={'/usr/bin/sleep',*re.findall(r'(?:=>\s+|^\s*)(/[^\s()]+)',dependencies,re.M)}
        if not 2<=len(paths)<=8:raise RuntimeError('Unexpected fixture loader layout')
        for value in paths:
            source=Path(value)
            if not source.is_file() or not value.startswith(('/usr/','/lib/','/lib64/')):raise RuntimeError('Unexpected fixture library')
            dest=ROOTFS/value.lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest);dest.chmod(0o755)
        (UNIT_ROOT/TIMER).write_text('[Timer]\nOnActiveSec=1h\nUnit='+NAMES['unloaded']+'\n')
        command('systemctl','daemon-reload')
        cls.start_helper('multi')
        for key in ('named','numeric','home','binds','dynamic','dynamic-static','declared','root'):command('systemctl','start',NAMES[key])
        cls.scope=subprocess.Popen(['systemd-run','--quiet','--scope','--unit='+SCOPE,'/usr/bin/setpriv',
            '--reuid='+str(cls.account.pw_uid),'--regid='+str(cls.account.pw_gid),'--clear-groups','/usr/bin/sleep','infinity'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        until(lambda:any(row.primary_name==SCOPE and row.active_state=='active' for row in loaded()))
        group=command('systemctl','show','--value','--property=ControlGroup',SCOPE).stdout.decode().strip()
        def scoped_sleep_ready():
            for process in (Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs').read_text().split():
                try:
                    status=Path('/proc/'+process+'/status').read_text()
                    ids=next(line for line in status.splitlines() if line.startswith('Uid:')).split()[1:]
                    if Path('/proc/'+process+'/comm').read_text().strip()=='sleep' and ids==[str(cls.account.pw_uid)]*4:return True
                except FileNotFoundError:pass
            return False
        until(scoped_sleep_ready)
    @classmethod
    def start_helper(cls,key):
        command('systemctl','start',NAMES[key]);current=pid(NAMES[key])
        def ready():
            try:return json.loads((WORK/(key+'-ready.json')).read_text())['pid']==current
            except (OSError,ValueError):return False
        until(ready);return json.loads((WORK/(key+'-ready.json')).read_text())
    @classmethod
    def tearDownClass(cls):
        command('systemctl','stop',SCOPE,check=False);cls.scope.wait(timeout=10)
        for name in NAMES.values():command('systemctl','stop',name,check=False);(UNIT_ROOT/name).unlink(missing_ok=True)
        (UNIT_ROOT/TIMER).unlink(missing_ok=True);command('systemctl','daemon-reload');shutil.rmtree(WORK);shutil.rmtree(ROOTFS)
    def reader(self,transport=Audited):
        result=Observed(self.target,self.storage);result._transport=transport(self.target,self.storage);return result
    def context(self,sample,key):
        row=next(row for row in sample.private_manifest()['configured_contexts'] if row['binding']['primary_name']==NAMES.get(key,key))
        return None if row['properties'] is None else dict(row['properties'])

    def test_01_names_numeric_ids_and_supplementary_groups_remain_text(self):
        sample=self.reader().collect();named=self.context(sample,'named');numeric=self.context(sample,'numeric')
        self.assertEqual((named['User'],named['Group'],named['SupplementaryGroups']),(ACCOUNT,ACCOUNT,[OTHER]))
        self.assertEqual((numeric['User'],numeric['Group'],numeric['SupplementaryGroups']),
                         (str(self.account.pw_uid),str(self.account.pw_gid),[str(self.other.pw_gid)]))
        self.assertEqual(self.context(sample,'multi')['User'],'')
        for facts in sample.facts().units:self.assertIsNone(facts.identities);self.assertIsNone(facts.paths)

    def test_02_scope_and_processless_units_never_receive_service_properties(self):
        reader=self.reader();sample=reader.collect()
        self.assertIsNone(self.context(sample,SCOPE));self.assertTrue(absent(NAMES['unloaded']));self.assertTrue(absent(TIMER))
        self.assertFalse(any(name in (SCOPE,TIMER,NAMES['unloaded']) for _,name,_ in reader._transport.queries))
        self.assertGreaterEqual(sample.report()['bound_units_without_service_context'],1)

    def test_03_dynamic_and_static_dynamic_configuration_not_effective_projection(self):
        sample=self.reader().collect()
        for key in ('dynamic','dynamic-static'):self.assertIs(self.context(sample,key)['DynamicUser'],True)
        self.assertEqual(self.context(sample,'dynamic')['User'],'hestia-context-dynamic')
        self.assertEqual(self.context(sample,'dynamic-static')['User'],ACCOUNT)
        identity=next(row['identity'] for row in sample.private_manifest()['census']['tasks'] if row['tid']==pid(NAMES['dynamic']))
        self.assertNotEqual(identity['uids'][1],self.account.pw_uid);self.assertIn(self.account.pw_gid,identity['groups'])
        self.assertFalse(sample.report()['configured_identity_projection_delivered'])

    def test_04_real_bind_mounts_namespace_and_paths_remain_unresolved(self):
        sample=self.reader().collect();values=self.context(sample,'binds');process=pid(NAMES['binds'])
        self.assertEqual(values['PrivateTmpEx'],'disconnected');self.assertIs(values['PrivateMounts'],True)
        self.assertEqual(values['BindPaths'],[[str(WORK/'source'),str(WORK/'destination'),False,16384]])
        self.assertEqual(values['BindReadOnlyPaths'],[[str(WORK/'source'),str(WORK/'readonly'),False,16384]])
        self.assertEqual(values['ReadWritePaths'],['-'+str(WORK/'absent-rw')]);self.assertEqual(values['ReadOnlyPaths'],[str(WORK/'source')])
        self.assertEqual(values['InaccessiblePaths'],[str(WORK/'hidden')])
        self.assertNotEqual(os.readlink('/proc/'+str(process)+'/ns/mnt'),os.readlink('/proc/self/ns/mnt'))
        self.assertEqual(Path('/proc/'+str(process)+'/root'+str(WORK/'destination/data')).read_text(),'fixture-binding-content')
        self.assertFalse(sample.report()['effective_context_verified'])

    def test_05_working_directory_home_missing_and_empty_getter_markers(self):
        sample=self.reader().collect()
        self.assertEqual(self.context(sample,'named')['WorkingDirectory'],'!'+str(WORK/'absent'))
        self.assertEqual(self.context(sample,'home')['WorkingDirectory'],'~')
        self.assertEqual(self.context(sample,'multi')['WorkingDirectory'],'')

    def test_06_reloaded_private_user_image_and_pam_are_not_runtime_claims(self):
        name=NAMES['declared'];before=pid(name);namespace=os.readlink('/proc/'+str(before)+'/ns/user')
        try:
            (UNIT_ROOT/name).write_text(self.units['declared']+'PrivateUsers=identity\nRootImage='+str(WORK/'root.img')+'\nPAMName=hestia-fixture-not-activated\n')
            command('systemctl','daemon-reload');sample=self.reader().collect();values=self.context(sample,'declared')
            self.assertEqual(values['PrivateUsersEx'],'identity');self.assertEqual(values['RootImage'],str(WORK/'root.img'))
            self.assertEqual(values['PAMName'],'hestia-fixture-not-activated');self.assertEqual(pid(name),before)
            self.assertEqual(os.readlink('/proc/'+str(before)+'/ns/user'),namespace)
            self.assertFalse(sample.report()['effective_context_verified'])
        finally:(UNIT_ROOT/name).write_text(self.units['declared']);command('systemctl','daemon-reload')

    def test_07_configuration_drift_at_same_invocation_refused(self):
        observed=[];original=self.units['named']
        class Changing(Observed):
            def _contexts(self,*args):
                values=super()._contexts(*args);observed.append(values)
                if len(observed)==1:
                    (UNIT_ROOT/NAMES['named']).write_text(original+'PAMName=hestia-fixture-never-run\n');command('systemctl','daemon-reload')
                return values
        before=fds()
        try:
            with self.assertRaises(e.c.CensusError):Changing(self.target,self.storage).collect()
            self.assertEqual(len(observed),2)
            a,b=(next(row for row in batch if row.binding.primary_name==NAMES['named']) for batch in observed)
            self.assertEqual(a.binding,b.binding);self.assertNotEqual(a.properties,b.properties);self.assertEqual(fds(),before)
        finally:(UNIT_ROOT/NAMES['named']).write_text(original);command('systemctl','daemon-reload')

    def mutation(self,key,action):
        fixture=self.start_helper(key);changed=[];before=fds()
        class Mutating(Audited):
            def _context_query(self,prop,binding,*args):
                result=super()._context_query(prop,binding,*args)
                if prop=='User' and binding.primary_name==NAMES[key] and not changed:changed.append(True);action(fixture)
                return result
        reader=self.reader(Mutating)
        with self.assertRaises(e.c.CensusError):reader.collect()
        self.assertEqual(changed,[True]);self.assertEqual(fds(),before);return fixture,reader

    def test_08_thread_fsuid_change_during_context_refused(self):
        def change(_):
            (WORK/'change-change').touch();until(lambda:(WORK/'change-changed').exists())
        fixture,_=self.mutation('change',change)
        status=Path('/proc/'+str(fixture['pid'])+'/task/'+str(fixture['tid'])+'/status').read_text()
        self.assertEqual(next(line for line in status.splitlines() if line.startswith('Uid:')).split()[1:],['0']*4)

    def test_09_disappearance_after_user_never_loads_named_object(self):
        def stop(_):command('systemctl','stop',NAMES['vanish']);until(lambda:absent(NAMES['vanish']))
        _,reader=self.mutation('vanish',stop)
        self.assertTrue(absent(NAMES['vanish']));self.assertTrue((UNIT_ROOT/NAMES['vanish']).is_file())
        self.assertEqual([p for p,name,_ in reader._transport.queries if name==NAMES['vanish']],['User','Group'])
        self.assertEqual([p for p,name in reader._transport.returned if name==NAMES['vanish']],['User'])

    def test_10_restart_does_not_adopt_new_invocation(self):
        fixture,reader=self.mutation('restart',lambda _:command('systemctl','restart',NAMES['restart']))
        self.assertNotEqual(pid(NAMES['restart']),fixture['pid'])
        self.assertEqual([p for p,name in reader._transport.returned if name==NAMES['restart']],['User'])
        self.start_helper('restart')

    def test_11_grouping_closed_fds_private_report_exact_budget_and_digests(self):
        before=fds();reader=self.reader();sample=reader.collect();self.assertEqual(fds(),before)
        data=sample.private_manifest();report=sample.report();multi=next(row for row in data['units'] if row['primary_name']==NAMES['multi'])
        self.assertEqual(len(multi['candidate_leaders']),3)
        self.assertEqual([p for p,name,_ in reader._transport.queries if name==NAMES['multi']],list(e.PROPERTIES)*2)
        self.assertEqual(report['bus_calls'],24+8*len(data['bindings'])+18*len(data['units'])+40*report['configured_service_contexts'])
        self.assertEqual(sample.facts().discovery_sha256,sample.index().report()['manifest_sha256'])
        self.assertEqual(sample.facts().census_sha256,e.d.l._sha(e.d._json(data['census'])))
        allowed={'visible_task_enumeration_observed','system_manager_lists_observed','candidate_leader_bindings_observed',
            'systemd_bindings_observed','selected_relations_observed','census_candidate_signals_observed','selected_service_configuration_observed'}
        for key,value in report.items():
            if type(value) is bool:self.assertEqual(value,key in allowed,key)
        for secret in ('hestia-context','/run/','context-named.service'):self.assertNotIn(secret,json.dumps(report)+repr(sample))
        self.assertEqual(report['known_provisioned_units'],0)

    def test_12_actual_disposable_rootfs_is_observed_as_configured_only(self):
        sample=self.reader().collect();values=self.context(sample,'root');process=pid(NAMES['root'])
        self.assertEqual(os.readlink('/proc/'+str(process)+'/root'),str(ROOTFS))
        self.assertEqual(values['RootDirectory'],str(ROOTFS));self.assertIs(values['RootDirectoryStartOnly'],True)
        self.assertEqual(values['RootImage'],'');self.assertFalse(sample.report()['effective_context_verified'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    before=quality.snapshot(ROOT);result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ExecutionContextLive));stable=before==quality.snapshot(ROOT)
    report={'suite':'Live configured service execution context','tests':result.testsRun,'expected':12,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==12 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(before),'effective_context_verified':False,'host_scheduler_inventory_complete':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True);(args.report.parent/'EXECUTION-CONTEXT-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if report['status']=='PASS' else 1)
