#!/usr/bin/env python3
"""Real Linux task census. All mutations belong to the disposable fixture."""
import argparse
import json
import os
from pathlib import Path
import pwd
import select
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from installer import process_census as c
from systemd_invocation_systemd import command,until,UNIT_ROOT,fds
sys.path.insert(0,str(ROOT/'scripts'));import quality
WORK=Path('/run/hestia-census-fixture')
NAMES={key:'census-'+key+'.service' for key in ('stable','change','birth','exit','zombie','other')}

HELPER=r'''import ctypes,json,os,sys,threading,time
from pathlib import Path
root=Path(sys.argv[1]);uid,gid,other_uid,other_gid=map(int,sys.argv[2:6]);mode=sys.argv[6]
libc=ctypes.CDLL('libc.so.6',use_errno=True);libc.setfsuid.argtypes=[ctypes.c_uint];libc.setfsuid.restype=ctypes.c_int
children=[]
if mode=='zombie':
 child=os.fork()
 if child==0:os._exit(0)
 children=[child]
else:
 read,write=os.pipe();child=os.fork()
 if child==0:
  os.close(read);os.setgroups([]);os.setgid(other_gid);os.setuid(other_uid);grandchild=os.fork()
  if grandchild==0:
   os.close(write)
   while True:time.sleep(1)
  os.write(write,json.dumps([os.getpid(),grandchild]).encode());os.close(write)
  while True:time.sleep(1)
 os.close(write);children=json.loads(os.read(read,1024));os.close(read)
ready=threading.Event();changed=threading.Event();stop=threading.Event();worker_tid=[]
def worker():
 worker_tid.append(threading.get_native_id());libc.setfsuid(uid)
 # Verify fixture behavior in the thread that actually changed its credentials.
 ids=next(x for x in Path('/proc/thread-self/status').read_text().splitlines() if x.startswith('Uid:')).split()[1:]
 assert ids==['0','0','0',str(uid)],ids
 ready.set()
 while not stop.is_set():
  if (root/(mode+'-change')).exists() and not changed.is_set():libc.setfsuid(0);changed.set()
  time.sleep(.01)
 libc.setfsuid(0)
thread=threading.Thread(target=worker);thread.start();ready.wait()
(root/(mode+'-ready.json')).write_text(json.dumps({'pid':os.getpid(),'tid':worker_tid[0],'children':children}))
born=False
while True:
 if changed.is_set():(root/(mode+'-changed')).touch()
 if (root/(mode+'-exit')).exists() and thread.is_alive():
  stop.set();thread.join();(root/(mode+'-exited')).touch()
 if (root/(mode+'-birth')).exists() and not born:
  born=True;fresh=threading.Thread(target=lambda:threading.Event().wait());fresh.start();(root/(mode+'-born')).touch()
 time.sleep(.01)
'''

class CensusLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SYSTEMD_DISCOVERY_TEST')!='1' or os.geteuid()!=0 or Path('/proc/1/comm').read_text().strip()!='systemd':raise RuntimeError('Explicit disposable root systemd opt-in required')
        command('useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin','hestia-census-test')
        command('useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin','hestia-census-other')
        cls.account=pwd.getpwnam('hestia-census-test');cls.other=pwd.getpwnam('hestia-census-other');WORK.mkdir(mode=0o755)
        (WORK/'helper.py').write_text(HELPER)
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
        for file in WORK.iterdir():file.unlink()
        WORK.rmdir()
    def reader(self):return c.ProcessCensus(self.account.pw_uid,self.account.pw_gid)
    def stable(self):return json.loads((WORK/'stable-ready.json').read_text())
    def test_01_nonleader_fsuid_observed_with_distinct_credentials(self):
        fixture=self.stable();sample=self.reader().collect();rows={x['tid']:x for x in sample.private_manifest()['tasks']}
        self.assertEqual(rows[fixture['pid']]['identity']['uids'],[0]*4)
        self.assertEqual(rows[fixture['tid']]['identity']['uids'],[0,0,0,self.account.pw_uid])
        self.assertEqual(rows[fixture['tid']]['tgid'],fixture['pid'])
        candidates=dict(sample.private_manifest()['candidates']);self.assertIn('UID_MATCH_IN_TASK',candidates[fixture['pid']])
        self.assertTrue(sample.report()['visible_task_enumeration_observed']);self.assertFalse(sample.report()['process_census_authenticated'])
    def test_02_children_and_grandchildren_with_other_uid_remain_candidates(self):
        fixture=self.stable();sample=self.reader().collect();data=sample.private_manifest();rows={x['tid']:x for x in data['tasks']};candidates=dict(data['candidates'])
        for child in fixture['children']:
            self.assertEqual(rows[child]['identity']['uids'],[self.other.pw_uid]*4)
            self.assertIn('DESCENDANT_AT_OBSERVATION',candidates[child])
        other=int(command('systemctl','show','--value','--property=MainPID',NAMES['other']).stdout)
        self.assertIn(other,rows);self.assertNotIn(other,candidates)
        self.assertFalse(sample.report()['automatic_exclusion_allowed']);self.assertFalse(sample.report()['all_descendants_identified'])
    def test_03_fd_cleanup_private_report_and_unrelated_records_retained(self):
        before=fds();sample=self.reader().collect();self.assertEqual(fds(),before)
        self.assertEqual(sample.report()['tasks'],len(sample.private_manifest()['tasks']))
        for key,value in sample.report().items():
            if type(value) is bool and key!='visible_task_enumeration_observed':self.assertFalse(value,key)
        for field in ('tid','tgid','uids','gids','groups','cgroup','parent_pid'):self.assertNotIn(field,sample.report())
        self.assertNotIn('hestia-census',json.dumps(sample.report()))
    def changing(self,key,control,ack):
        fixture=self.start(key);before=fds()
        class Changing(c.ProcessCensus):
            changed=False
            def _pass(self,*args):
                rows=super()._pass(*args)
                if not self.changed:
                    self.changed=True;(WORK/(key+'-'+control)).touch();until(lambda:(WORK/(key+'-'+ack)).exists())
                return rows
        with self.assertRaises(c.CensusError):Changing(self.account.pw_uid,self.account.pw_gid).collect()
        self.assertEqual(fds(),before);return fixture
    def test_04_thread_fsuid_change_between_passes_refused(self):
        fixture=self.changing('change','change','changed')
        raw=Path('/proc/'+str(fixture['pid'])+'/task/'+str(fixture['tid'])+'/status').read_text()
        self.assertEqual(next(x for x in raw.splitlines() if x.startswith('Uid:')).split()[1:],['0']*4)
    def test_05_new_thread_between_passes_refused(self):
        fixture=self.changing('birth','birth','born')
        self.assertEqual(len(list(Path('/proc/'+str(fixture['pid'])+'/task').iterdir())),3)
    def test_06_exited_thread_detected_while_its_leader_is_alive(self):
        fixture=self.start('exit');fd=os.pidfd_open(fixture['tid'],c.PIDFD_THREAD)
        try:
            self.assertTrue(c._live(fd));self.changing('exit','exit','exited');self.assertFalse(c._live(fd))
            self.assertTrue(Path('/proc/'+str(fixture['pid'])+'/status').exists())
        finally:os.close(fd)
    def test_07_zombie_is_explicitly_unresolved_not_dropped(self):
        fixture=self.start('zombie');zombie=fixture['children'][0]
        until(lambda:Path('/proc/'+str(zombie)+'/stat').read_text().rsplit(') ',1)[1].startswith('Z '))
        sample=self.reader().collect();rows={x['tid']:x for x in sample.private_manifest()['tasks']};row=rows[zombie]
        self.assertIsNone(row['identity']);self.assertEqual(row['issue'],'TASK_EXITED_NOT_REAPED')
        self.assertIn('UNRESOLVED_TASK',dict(sample.private_manifest()['candidates'])[zombie])
    def test_08_masked_proc_status_refused_before_enumeration(self):
        target='/proc/'+str(self.stable()['pid'])+'/status';fake=WORK/'fake-status';fake.write_text('masked\n')
        command('mount','--bind',str(fake),target)
        try:
            with self.assertRaises(c.CensusError):self.reader().collect()
        finally:command('umount',target);fake.unlink()
        self.assertIn('Uid:',Path(target).read_text())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    before=quality.snapshot(ROOT);result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CensusLive));stable=before==quality.snapshot(ROOT)
    report={'suite':'Visible task census with thread credentials and descendants','tests':result.testsRun,'expected':8,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==8 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(before),'host_scheduler_inventory_complete':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True);(args.report.parent/'PROCESS-CENSUS-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if report['status']=='PASS' else 1)
