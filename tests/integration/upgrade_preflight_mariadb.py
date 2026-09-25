#!/usr/bin/env python3
"""5C1 actual SQL/TLS/HTTP preflight recipe. Disposable own servers ONLY."""
import argparse
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(Path(__file__).resolve().parent)]
from installer import upgrade_preflight as u
from installer import finalization as f
from installer import php_transport as p
import finalization_mariadb as previous


class UpgradePreflightLive(previous.FinalizationIntegration):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_UPGRADE_PREFLIGHT_TEST')!='1':raise RuntimeError('Explicit upgrade preflight opt-in required')
        super().setUpClass()

    def reader(self):
        return u.UpgradePreflight(self.runtime,previous.WEB,repository=p.WEB_REPOSITORY,commit=u.WEB_COMMIT)

    def inspect(self):
        return self.reader().inspect(self.existing(),config_root=self.output).report()

    def files(self):
        return {str(path):(path.read_bytes(),path.stat().st_mode,path.stat().st_uid,path.stat().st_gid,path.stat().st_mtime_ns)
            for root in (self.directory,self.runtime.state_root) for path in root.rglob('*') if path.is_file()}

    def logical_dump(self):
        # No password: only this test-created server's private local root socket.
        command=['mariadb-dump','--no-defaults','--socket='+self.socket,'--user=root','--skip-comments',
            '--skip-dump-date','--skip-lock-tables','--skip-add-locks','--compact','--order-by-primary','--hex-blob',self.db]
        raw=subprocess.check_output(command,stderr=subprocess.PIPE,timeout=30)
        return hashlib.sha256(raw).hexdigest()

    def test_upgrade_same_release_noop_report_and_total_preservation(self):
        self.finish();before=self.logical_dump();files=self.files()
        a=self.inspect();b=self.inspect();self.assertEqual(a,b)
        self.assertEqual(a['state'],'UPGRADE_PREFLIGHT_READY');self.assertFalse(a['plan']['apply_allowed'])
        self.assertEqual(a['plan']['inventory']['counts']['users'],'1')
        self.assertEqual(a['plan']['inventory']['counts']['web_sessions'],'0')
        self.assertEqual(before,self.logical_dump());self.assertEqual(files,self.files())
        self.assertFalse((self.directory/'assistant-edit.lock').exists());self.assertFalse(list(self.run.iterdir()))

    def test_upgrade_long_unicode_values_and_max_bigint_are_not_read_or_changed(self):
        self.finish();text="  D'Exemple <&> 漢字🙂 "+'x'*12000
        self.sql([f'CREATE TABLE `{self.db}`.preservation(id BIGINT UNSIGNED PRIMARY KEY,value TEXT) ENGINE=InnoDB',
            f'INSERT INTO `{self.db}`.preservation VALUES(18446744073709551615,{previous.previous.literal(text)})'])
        before=self.logical_dump();result=self.inspect();self.assertEqual(before,self.logical_dump())
        self.assertNotIn(text,json.dumps(result));self.assertNotIn('18446744073709551615',json.dumps(result))

    def test_upgrade_key_users_rbac_sessions_and_parameters_preserved(self):
        self.payload['assistant']['action']='configure';self.payload['secrets']['openai_api_key']='secret-key-'+('A'*100)
        self.finish();before=self.logical_dump();files=self.files();result=self.inspect()
        self.assertTrue(result['plan']['assistant']['key_configured'])
        self.assertTrue(result['plan']['assistant']['setting_enabled'])
        for secret in self.payload['secrets'].values():
            if secret:self.assertNotIn(secret,json.dumps(result))
        self.assertEqual(before,self.logical_dump());self.assertEqual(files,self.files())

    def test_upgrade_sql_managed_profile_after_temporary_account_removal(self):
        self.managed();self.assertEqual(self.prepare(authority=self.authority)['state'],'DATABASE_CONFIGURATION_READY')
        self.assertEqual(self.finalize()['state'],'WEB_FRESH_FINALIZED');self.assertEqual(self.inspect()['state'],'UPGRADE_PREFLIGHT_READY')
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)

    def test_upgrade_remote_tls_inspection_and_ca_drift_refused(self):
        self.remote();self.finish();self.assertTrue(self.inspect()['plan']['database']['tls_required'])
        (self.directory/'ca.pem').write_text('invalid CA fixture')
        with self.assertRaises(u.UpgradePreflightError):self.inspect()

    def test_upgrade_legacy_or_incomplete_target_not_adopted(self):
        self.prepare();files=self.files()
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.assertEqual(files,self.files());self.assertFalse((self.webroot/'install.lock').exists())

    def test_upgrade_wrong_version_not_assumed_migratable(self):
        self.finish();self.sql([f"UPDATE `{self.db}`.App_Config SET valeur='2.0.5.0' WHERE cle='APP_VERSION'"])
        before=self.logical_dump()
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.assertEqual(before,self.logical_dump())

    def test_upgrade_missing_rbac_table_not_hidden_by_version_marker(self):
        self.finish();self.sql([f'DROP TABLE `{self.db}`.User_Rbac_Override'])
        with self.assertRaisesRegex(u.UpgradePreflightError,'UPGRADE_REQUIRED_TABLES_MISSING'):self.inspect()

    def test_upgrade_no_active_administrator_no_reset(self):
        self.finish();self.sql([f'UPDATE `{self.db}`.UserInfo SET actif=0']);before=self.logical_dump()
        with self.assertRaisesRegex(u.UpgradePreflightError,'UPGRADE_NO_ACTIVE_ADMIN'):self.inspect()
        self.assertEqual(before,self.logical_dump())

    def test_upgrade_privilege_drift_refused(self):
        self.finish();self.sql([f'GRANT ALTER ON {self.schema} TO {self.app_account}']);before=self.files()
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.assertEqual(before,self.files())

    def test_upgrade_added_executable_not_run(self):
        self.finish();(self.webroot/'unexpected.php').write_text('<?php throw new Exception("must not execute");')
        with self.assertRaises(u.UpgradePreflightError):self.inspect()

    def test_upgrade_views_and_nontransactional_extra_tables_not_certified(self):
        self.finish();self.sql([f'CREATE TABLE `{self.db}`.extra_fixture(id INT PRIMARY KEY) ENGINE=MyISAM',
            f'CREATE VIEW `{self.db}`.fixture_view AS SELECT 1 AS id'])
        result=self.inspect()['plan'];self.assertEqual(result['inventory']['views'],1)
        self.assertEqual(result['inventory']['non_innodb_tables'],1);self.assertFalse(result['backup_verified'])
        self.assertFalse(result['apply_allowed']);self.assertIn('APPLICATION_ACCOUNT_VISIBILITY_ONLY',result['limitations'])

    def test_upgrade_readonly_sql_session_rejects_real_mutation(self):
        self.finish();exchange=p._exchange
        def verify(command,request,stage,timeout,cancel):
            bridge=stage/'bridge.php'
            if b"$operation = 'invalid';" in bridge.read_bytes() and b'START TRANSACTION READ ONLY' in bridge.read_bytes():
                raw=bridge.read_bytes();token=b'    $before = $metadata();'
                injected=b'''    $blocked=false;
    try {$pdo->exec("UPDATE App_Config SET valeur='INVALID' WHERE cle='APP_VERSION'");}
    catch(PDOException $e) {$blocked=($e->errorInfo[1]??null)===1792;}
    if(!$blocked) throw new RuntimeException('READ_ONLY_NOT_ENFORCED');
'''
                self.assertIn(token,raw);bridge.write_bytes(raw.replace(token,injected+token,1));self.enforcement_checked=True
            return exchange(command,request,stage,timeout,cancel)
        self.enforcement_checked=False;before=self.logical_dump()
        with patch.object(p,'_exchange',side_effect=verify):self.inspect()
        self.assertTrue(self.enforcement_checked);self.assertEqual(before,self.logical_dump())

    def test_upgrade_killed_inspector_does_not_change_target_or_leave_replay_marker(self):
        self.finish();files=self.files();before=self.logical_dump()
        def child():
            with patch.object(u,'_inventory',side_effect=lambda *a:os._exit(91)):self.inspect()
        process=multiprocessing.get_context('fork').Process(target=child);process.start();process.join(30)
        if process.is_alive():process.kill();process.join();self.fail('Inspector did not stop')
        self.assertEqual(process.exitcode,91);self.assertEqual(files,self.files());self.assertEqual(before,self.logical_dump())
        self.assertEqual(self.inspect()['state'],'UPGRADE_PREFLIGHT_READY')

    def test_upgrade_inspection_does_not_break_admin_login_dashboard_logout(self):
        self.finish();self.inspect()
        with self.http() as request:
            status,body,_,_=request('/login.php');self.assertEqual(status,200)
            token=re.search(r'name="csrf_token" value="([a-f0-9]+)"',body).group(1)
            status,body,_,url=request('/login.php',{'csrf_token':token,'identifier':self.payload['administrator']['email'],
                'password':self.payload['secrets']['admin_password']})
            self.assertEqual(status,200);self.assertNotIn('/login.php',url)
            before=self.logical_dump();files=self.files()
            report=self.inspect();self.assertEqual(report['state'],'UPGRADE_PREFLIGHT_READY')
            self.assertEqual(before,self.logical_dump());self.assertEqual(files,self.files())
            status,_,_,url=request('/index.php');self.assertEqual(status,200);self.assertNotIn('/login.php',url)
            status,_,_,url=request('/logout.php');self.assertEqual(status,200);self.assertIn('/login.php',url)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--web',type=Path,required=True);parser.add_argument('--report',type=Path)
    args=parser.parse_args();previous.WEB=args.web.resolve()
    names=[n for n in unittest.defaultTestLoader.getTestCaseNames(UpgradePreflightLive) if n.startswith('test_upgrade_')]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(UpgradePreflightLive(n) for n in names))
    report={'suite':'5C1 read-only upgrade preflight SQL/TLS/HTTP','tests':result.testsRun,'expected':len(names),
        'errors':len(result.errors),'failures':len(result.failures),'skips':len(result.skipped),'web_commit':u.WEB_COMMIT,
        'status':'PASS' if result.wasSuccessful() and result.testsRun==len(names) and not result.skipped else 'FAIL'}
    if args.report:args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
