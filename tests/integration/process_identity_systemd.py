#!/usr/bin/env python3
"""Real selected leaders, credentials and races on disposable Debian 13."""
import argparse
import json
import os
from pathlib import Path
import pwd
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from installer import process_identity as p, systemd_process_identity as z
from installer.storage_inventory import StorageRequirements, PRODUCERS
from systemd_invocation_systemd import command,until,UNIT_ROOT,pid,hint,fds
sys.path.insert(0,str(ROOT/'scripts'))
import quality
A='identity-worker.service';B='identity-group.service';PRIVATE='identity-private.service'
CHANGE='identity-change.service';MOVE='identity-move.service';EXIT='identity-exit.service'
WORK=Path('/run/hestia-identity-fixture')

class IdentityLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST')!='1' or os.geteuid()!=0 or Path('/proc/1/comm').read_text().strip()!='systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('systemctl','start','dbus.service')
        command('useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin','hestia-process-test')
        cls.account=pwd.getpwnam('hestia-process-test');WORK.mkdir(mode=0o755)
        context=z.t.o._provenance();release=z.d.l.get_release(z.d.l.STORAGE_COMMIT)
        cls.target=z.d.l.LauncherTarget('9'*32,release.commit,release.tree,'/srv/process-fixture','/var/lib/process-fixture',
            '/var/lib/process-fixture/maintenance',cls.account.pw_uid,cls.account.pw_gid,context['host_id'],context['boot_id'])
        cls.storage=StorageRequirements(json.dumps({'version':1,'source_commit':release.commit,'runtime_sha256':release.runtime_sha256,
            'scopes':[{'role':'uploads','path':cls.target.webroot+'/uploads'},{'role':'managed_configuration','path':cls.target.configuration}],
            'producers':[{'group':group,'state':'REQUIRED_NOT_VERIFIED'} for group in PRODUCERS],
            'blockers':['FIXTURE_DECLARED_STORAGE_NOT_DEPLOYED']}).encode())
        service='[Service]\nUser=hestia-process-test\nExecStart=/usr/bin/sleep infinity\n'
        cls.units={name:service for name in (A,PRIVATE,MOVE,EXIT)}
        cls.units[PRIVATE]+='PrivateTmp=yes\n'
        cls.units[B]='[Service]\nSupplementaryGroups=hestia-process-test\nExecStart=/usr/bin/sleep infinity\n'
        helper=WORK/'change.py'
        helper.write_text('import os,time\nfrom pathlib import Path\np=Path('+repr(str(WORK))+')\nf=os.open(p/"ack",os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)\n(p/"ready").touch()\nwhile not (p/"trigger").exists():time.sleep(.01)\nos.seteuid('+str(cls.account.pw_uid)+')\nos.write(f,b"changed");os.close(f)\nwhile True:time.sleep(1)\n')
        cls.units[CHANGE]='[Service]\nExecStart=/usr/bin/python3 '+str(helper)+'\n'
        for name,raw in cls.units.items():(UNIT_ROOT/name).write_text(raw)
        command('systemctl','daemon-reload');command('systemctl','start',A,B,PRIVATE)
    @classmethod
    def tearDownClass(cls):
        for name in cls.units:
            command('systemctl','stop',name,check=False);(UNIT_ROOT/name).unlink(missing_ok=True)
        command('systemctl','daemon-reload')
        for item in WORK.iterdir():item.unlink()
        WORK.rmdir()
    def reader(self):return z.SystemdProcessIdentity(self.target,self.storage)
    def test_01_actual_credentials_and_membership_bound_without_authority(self):
        h=hint(A);before=fds();sample=self.reader().collect((h,));self.assertEqual(fds(),before)
        detail=sample.private_manifest()['properties'][0]['binding'];identity=detail['identity']
        self.assertEqual(identity['pid'],h.pid);self.assertEqual(identity['uids'],[self.account.pw_uid]*4)
        self.assertEqual(identity['gids'],[self.account.pw_gid]*4)
        self.assertEqual(identity['cgroup'],Path('/proc/'+str(h.pid)+'/cgroup').read_text()[3:-1])
        self.assertGreater(identity['start_ticks'],0);self.assertEqual(sample.report()['bus_calls'],50)
        self.assertEqual(sample.report()['process_observations'],7)
        self.assertTrue(sample.report()['selected_effective_leaders_observed'])
        self.assertEqual(sample.report()['known_provisioned_units'],0);self.assertFalse(sample.report()['drain_allowed'])
        fact=sample.facts().units[0].identities[0];self.assertEqual(fact.uid,self.account.pw_uid)
        selected=next(u for u in sample.selection().private_manifest()['loaded_units'] if u['object_path']==h.object_path)
        self.assertEqual(selected['decision'],'RELATED_UNMANAGED');self.assertFalse(selected['enrolled'])
        for key in ('process_census_authenticated','effective_identities_observed','phase5_complete'):self.assertFalse(sample.report()[key])
        for secret in (A,detail['invocation_id']):self.assertNotIn(secret,json.dumps(sample.report()))
        self.assertTrue({'pid','uids','gids','groups','cgroup'}.isdisjoint(sample.report()))
    def test_02_supplementary_group_matches_without_effective_uid(self):
        sample=self.reader().collect((hint(B),));fact=sample.facts().units[0].identities[0]
        self.assertEqual((fact.uid,fact.gid),(0,0));self.assertIn(self.account.pw_gid,fact.groups)
        selected=next(u for u in sample.selection().private_manifest()['loaded_units'] if u['object_path']==hint(B).object_path)
        self.assertEqual(selected['decision'],'RELATED_UNMANAGED');self.assertIn('ROOT_IDENTITY_NOT_EXCLUDED',selected['issues'])
    def test_03_private_mount_namespace_is_recorded_without_path_resolution(self):
        sample=self.reader().collect((hint(PRIVATE),));identity=sample.private_manifest()['properties'][0]['binding']['identity']
        self.assertNotEqual(identity['namespaces'][1],os.readlink('/proc/self/ns/mnt'))
        self.assertEqual(identity['namespaces'][0],os.readlink('/proc/self/ns/pid'))
        self.assertIsNone(sample.facts().units[0].paths)
    def test_04_real_wrong_owned_pidfd_rejected_and_caller_retains_fd(self):
        before=fds();fd=os.pidfd_open(pid(A))
        try:
            with self.assertRaisesRegex(z.t.SystemdTransportError,'PIDFD_MISMATCH'):p.observe(pid(B),fd,p.ProcBudget(z.t._Budget()))
            os.fstat(fd)
        finally:os.close(fd)
        self.assertEqual(fds(),before)
    def test_05_effective_uid_changes_with_same_pid_during_properties(self):
        command('systemctl','start',CHANGE);until(lambda:(WORK/'ready').exists());h=hint(CHANGE);before=fds()
        class Changing(z.SystemdProcessIdentity):
            changed=False
            def _relation_query(self,prop,*args):
                raw=super()._relation_query(prop,*args)
                if prop=='Names' and not self.changed:
                    self.changed=True;(WORK/'trigger').touch();until(lambda:(WORK/'ack').read_text()=='changed')
                return raw
        with self.assertRaisesRegex(z.t.SystemdTransportError,'PROCESS_IDENTITY_CHANGED'):Changing(self.target,self.storage).collect((h,))
        self.assertEqual(pid(CHANGE),h.pid);self.assertEqual(fds(),before)
        uids=p._credentials(Path('/proc/'+str(h.pid)+'/status').read_text(),h.pid)[0]
        self.assertEqual((uids[0],uids[1],uids[2]),(0,self.account.pw_uid,0))
    def test_06_same_pid_cgroup_change_is_refused(self):
        command('systemctl','start',MOVE);h=hint(MOVE);before=fds()
        original=Path('/proc/'+str(h.pid)+'/cgroup').read_text()[3:-1]
        nested=Path('/sys/fs/cgroup'+original)/'fixture-child';nested.mkdir()
        class Moving(z.SystemdProcessIdentity):
            moved=False
            def _relation_query(self,prop,*args):
                raw=super()._relation_query(prop,*args)
                if prop=='Names' and not self.moved:
                    self.moved=True;(nested/'cgroup.procs').write_text(str(h.pid))
                return raw
        with self.assertRaisesRegex(z.t.SystemdTransportError,'PROCESS_IDENTITY_CHANGED'):Moving(self.target,self.storage).collect((h,))
        self.assertEqual(pid(MOVE),h.pid);self.assertEqual(fds(),before)
        self.assertEqual(Path('/proc/'+str(h.pid)+'/cgroup').read_text()[3:-1],original+'/fixture-child')
    def test_07_process_exit_after_mapping_refuses_and_closes_all_fds(self):
        command('systemctl','start',EXIT);h=hint(EXIT);before=fds()
        class Exiting(z.SystemdProcessIdentity):
            def _invocation_query(self,operation,*args,**kwargs):
                raw=super()._invocation_query(operation,*args,**kwargs)
                if operation=='GetUnitByPIDFD':command('systemctl','stop',EXIT)
                return raw
        with self.assertRaisesRegex(z.t.SystemdTransportError,'PROCESS_EXITED'):Exiting(self.target,self.storage).collect((h,))
        self.assertEqual(fds(),before)
    def test_08_foreign_observer_mount_namespace_refused_in_real_child(self):
        script='import os,sys;sys.path.insert(0,'+repr(str(ROOT))+');from installer import process_identity as p,systemd_discovery_transport as t\nfd=os.pidfd_open('+str(pid(A))+')\ntry:\n try:p.observe('+str(pid(A))+',fd,p.ProcBudget(t._Budget()))\n except t.SystemdTransportError as e:\n  assert str(e)=="PROCESS_OBSERVER_NAMESPACE_REJECTED",str(e)\n else:raise AssertionError("foreign observer accepted")\nfinally:os.close(fd)\n'
        command('unshare','--mount','--propagation','private','/usr/bin/python3','-c',script)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    before=quality.snapshot(ROOT);result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IdentityLive))
    stable=before==quality.snapshot(ROOT)
    report={'suite':'Selected leader identity and invocation','tests':result.testsRun,'expected':8,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==8 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(before),'automatic_pid_census_qualified':False,'host_scheduler_inventory_complete':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    (args.report.parent/'PROCESS-IDENTITY-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if report['status']=='PASS' else 1)
