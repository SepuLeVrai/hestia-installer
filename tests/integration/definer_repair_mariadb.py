#!/usr/bin/env python3
"""Explicit repair, real isolated SQL/HTTP and actual interruption boundaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(Path(__file__).resolve().parent)]
from installer import definer_repair as r
from installer import maintenance as m
from installer import php_transport as p
from installer import finalization as f
from installer import upgrade_backup as b
import maintenance_mariadb as previous

WEB=None


class RepairLive(previous.MaintenanceLive):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_DEFINER_REPAIR_TEST')!='1':raise RuntimeError('Explicit repair opt-in required')
        previous.WEB=WEB
        super().setUpClass()

    def ready(self):
        self.orphaned_ready()
        instance=json.loads((self.directory/'seal.json').read_bytes())['instance']
        self.scope=m.MaintenanceScope(self.directory/'maintenance',self.web.pw_gid,instance)
        self.scope.create(confirmed=True);self.guard=self.scope.directory/'request_guard.php'
        self.repair=r.DefinerRepair(self.runtime,WEB,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT)

    def execute(self,lease,**kwargs):
        values=dict(config_root=self.output,backup_root=self.backups,maintenance=lease,
            expected_orphaned_definer=self.migration+'@127.0.0.1',confirmed=True,allow_global_read_lock=True)
        values.update(kwargs)
        return self.repair.repair(self.existing(),self.authority,**values)

    def data_dump(self):
        command=['mariadb-dump','--no-defaults','--socket='+self.socket,'--user=root','--skip-comments',
            '--skip-dump-date','--skip-lock-tables','--skip-add-locks','--compact','--order-by-primary','--hex-blob','--skip-triggers',self.db]
        return hashlib.sha256(subprocess.check_output(command,stderr=subprocess.PIPE,timeout=30)).hexdigest()

    def pending(self):
        return list(self.runtime.state_root.glob('definer-repair-*.attempt'))

    def assert_no_definer(self):
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.definer_user()}'")[0]['n'],0)

    def assert_interlocked(self,lease):
        self.assertEqual(len(self.pending()),1)
        self.assertFalse(list(self.runtime.state_root.glob('definer-repair-*.done')))
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        with self.assertRaisesRegex(r.DefinerRepairError,'REPAIR_PENDING'):self.execute(lease)

    def test_repair_real_preservation_restore_login_and_five_source_trigger_effects(self):
        self.ready();self.settings('configure','repair-private-key-'+('K'*80))
        self.sql([f"UPDATE `{self.db}`.UserInfo SET prenom='Élise 漢字🙂' WHERE id_user=1"])
        with self.http(prepend=self.guard) as request:
            status,body,_,_=request('/login.php');self.assertEqual(status,200)
            token=re.search(r'name="csrf_token" value="([a-f0-9]+)"',body).group(1)
            self.assertEqual(request('/login.php',{'csrf_token':token,'identifier':self.payload['administrator']['email'],
                'password':self.payload['secrets']['admin_password']})[0],200)
            with self.scope.acquire(confirmed=True) as lease:
                before=self.data_dump();files=self.files();result=self.execute(lease)
                self.assertEqual(result['state'],'DEFINER_REPAIR_VERIFIED',result)
                self.assertEqual(before,self.data_dump())
                after=self.files()
                for path,value in files.items():self.assertEqual(after[path],value)
                self.assertEqual(len(set(after)-set(files)),2)
                self.assertTrue(result['source_business_data_preserved']);self.assertFalse(result['source_business_rows_written'])
                self.assertEqual(result['isolated_trigger_smoke_verified'],5);self.assertTrue(result['maintenance_required'])
                self.assertEqual(request('/index.php')[0],503)
                self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)
                rows=self.sql(query=f"SELECT DEFINER FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='{self.db}'")
                self.assertEqual({x['DEFINER'] for x in rows},{self.definer_user()+'@localhost'})
                for secret in [self.authority._password,self.credentials._password,*self.payload['secrets'].values()]:
                    if secret:self.assertNotIn(secret,json.dumps(result))
                lease.resume(confirmed=True)
            status,_,_,url=request('/index.php');self.assertEqual(status,200);self.assertNotIn('login.php',url)
            self.assertIn('login.php',request('/logout.php')[3])
        # Exercise the source through the real DML account, only in this disposable
        # fixture. Production repair itself never advances source AUTO_INCREMENT.
        script="""$v=json_decode(stream_get_contents(STDIN),true);require $v['helper'];
$pdo=new PDO('mysql:host=127.0.0.1;dbname='.$v['database'].';charset=utf8mb4',$v['user'],$v['password'],[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
hdf_smoke($pdo);echo 'FIVE_EFFECTS_VERIFIED';"""
        wire={'helper':str(ROOT/'installer/private/trigger_definer.php'),'database':self.db,'user':self.app,
            'password':self.payload['secrets']['database_password']}
        out=subprocess.run(['setpriv','--reuid='+str(self.web.pw_uid),'--regid='+str(self.web.pw_gid),'--clear-groups',
            str(self.runtime.php),'-d','display_errors=0','-d','log_errors=0','-r',script],input=json.dumps(wire).encode(),capture_output=True,timeout=15)
        self.assertEqual(out.returncode,0);self.assertEqual(out.stderr,b'');self.assertEqual(out.stdout,b'FIVE_EFFECTS_VERIFIED')
        self.assertEqual(self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.P_Activite')[0]['n'],0)

    def test_repair_requires_live_instance_bound_maintenance_and_both_consents(self):
        self.ready();before=self.logical_dump()
        with self.assertRaisesRegex(r.DefinerRepairError,'REPAIR_MAINTENANCE_REQUIRED'):self.execute(None)
        other=m.MaintenanceScope(self.root/'other-maintenance',self.web.pw_gid,'8'*32);other.create(confirmed=True)
        with other.acquire(confirmed=True) as lease:
            with self.assertRaisesRegex(r.DefinerRepairError,'REPAIR_MAINTENANCE_BINDING_REQUIRED'):self.execute(lease)
        with self.scope.acquire(confirmed=True) as lease:
            for key in ('confirmed','allow_global_read_lock'):
                for value in (False,1,'true',None):
                    with self.assertRaisesRegex(r.DefinerRepairError,'REPAIR_CONSENT_REQUIRED'):self.execute(lease,**{key:value})
        self.assertEqual(before,self.logical_dump());self.assertFalse(list(self.backups.iterdir()));self.assertFalse(self.pending())

    def test_repair_failed_rescue_never_dispatches_sql_mutation(self):
        self.ready();before=self.logical_dump()
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(b,'_restore',side_effect=b.UpgradeBackupError('BACKUP_ARCHIVE_INVALID')):
                with self.assertRaisesRegex(r.DefinerRepairError,'REPAIR_RESCUE_REQUIRED'):self.execute(lease)
        self.assertEqual(before,self.logical_dump());self.assert_no_definer();self.assertFalse(self.pending())

    def test_repair_drift_after_rescue_is_rejected_before_ddl(self):
        self.ready();original=r._stage
        def drift(*args):
            original(*args)
            self.sql([f"INSERT INTO `{self.db}`.App_Config(cle,valeur) VALUES('repair_fixture_drift','concurrent')"])
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(r,'_stage',side_effect=drift):result=self.execute(lease)
            self.assertEqual(result['state'],'REPAIR_MANUAL_ACTION',result);self.assertEqual(result['code'],'REPAIR_SOURCE_CHANGED')
            self.assert_no_definer();self.assert_interlocked(lease)

    def test_repair_existing_durable_identity_is_not_adopted_or_changed(self):
        self.ready();self.sql([f"CREATE USER `{self.definer_user()}`@'localhost' ACCOUNT LOCK"])
        before=self.logical_dump()
        with self.scope.acquire(confirmed=True) as lease:
            result=self.execute(lease);self.assertEqual(result['code'],'DEFINER_ACCOUNT_OCCUPIED',result)
            self.assertEqual(before,self.logical_dump());self.assert_interlocked(lease)

    def test_repair_actual_partial_rebind_keeps_evidence_and_never_replays(self):
        self.ready();original=r._stage;before=self.data_dump()
        def fault(runtime,source,stage):
            original(runtime,source,stage);path=stage/'trigger_definer.php';text=path.read_text()
            token="        $pdo->exec('CREATE OR REPLACE DEFINER=' . hdf_account($user) . ' ' . substr($canonical[$row['TRIGGER_NAME']]['statement'], 7));"
            self.assertIn(token,text)
            path.write_text(text.replace(token,token+"\n        throw new RuntimeException('DEFINER_REBIND_FAILED');"))
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(r,'_stage',side_effect=fault):result=self.execute(lease)
            self.assertEqual(result['state'],'REPAIR_MANUAL_ACTION',result);self.assertEqual(result['code'],'DEFINER_REBIND_FAILED')
            rows=self.sql(query=f"SELECT DEFINER FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='{self.db}'")
            self.assertEqual(sum(x['DEFINER']==self.definer_user()+'@localhost' for x in rows),1)
            self.assertEqual(sum(x['DEFINER']==self.migration+'@127.0.0.1' for x in rows),4)
            self.assertEqual(before,self.data_dump())
            self.assert_interlocked(lease)

    def test_repair_response_lost_after_actual_ddl_does_not_replay(self):
        self.ready();exchange=p._exchange;before=self.data_dump()
        def lose(command,wire,*args,**kwargs):
            result=exchange(command,wire,*args,**kwargs)
            if json.loads(wire).get('operation')=='repair_definers':
                self.assertEqual(result[0],0);raise p.TransportError('CHANNEL_FAILED')
            return result
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(p,'_exchange',side_effect=lose):result=self.execute(lease)
            self.assertEqual(result['state'],'REPAIR_MANUAL_ACTION',result)
            rows=self.sql(query=f"SELECT DEFINER FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='{self.db}'")
            self.assertEqual({x['DEFINER'] for x in rows},{self.definer_user()+'@localhost'})
            self.assertEqual(before,self.data_dump())
            self.assert_interlocked(lease)

    def test_repair_failed_post_restore_keeps_maintenance_and_no_success_receipt(self):
        self.ready();before=self.data_dump()
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(b.UpgradeBackup,'create_and_verify',return_value=b.BackupVerification(p._json({'state':'BACKUP_INCOMPLETE'}))):
                result=self.execute(lease)
            self.assertEqual(result['code'],'REPAIR_POST_RESTORE_REQUIRED',result);self.assert_interlocked(lease)
            self.assertEqual(before,self.data_dump())

    def test_repair_live_sql_writer_outside_barrier_is_refused(self):
        self.ready();before=self.logical_dump()
        script="""$v=json_decode(stream_get_contents(STDIN),true);
$pdo=new PDO('mysql:host=127.0.0.1;dbname='.$v['database'],$v['user'],$v['password']);
echo "READY\\n";flush();sleep(120);"""
        process=subprocess.Popen(['setpriv','--reuid='+str(self.web.pw_uid),'--regid='+str(self.web.pw_gid),'--clear-groups',
            str(self.runtime.php),'-d','display_errors=0','-d','log_errors=0','-r',script],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        try:
            process.stdin.write(json.dumps({'database':self.db,'user':self.app,'password':self.payload['secrets']['database_password']}).encode())
            process.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout,selectors.EVENT_READ)
                self.assertTrue(selector.select(5));self.assertEqual(process.stdout.readline(),b'READY\n')
            with self.scope.acquire(confirmed=True) as lease:
                result=self.execute(lease);self.assertEqual(result['code'],'REPAIR_WRITERS_ACTIVE',result)
                self.assertEqual(before,self.logical_dump());self.assert_no_definer();self.assert_interlocked(lease)
        finally:
            process.terminate();process.wait(timeout=5);process.stdout.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--web',type=Path,required=True);parser.add_argument('--report',type=Path)
    args=parser.parse_args();WEB=args.web.resolve()
    names=[n for n in unittest.defaultTestLoader.getTestCaseNames(RepairLive) if n.startswith('test_repair_')]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(RepairLive(n) for n in names))
    report={'suite':'Explicit orphaned definer repair under guarded maintenance','tests':result.testsRun,'expected':len(names),
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==len(names) and names and not result.skipped else 'FAIL'}
    if args.report:args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));raise SystemExit(report['status']!='PASS')
