#!/usr/bin/env python3
"""5C2 opt-in cross-repository real snapshot/restoration. Disposable servers only."""
import argparse
import copy
from dataclasses import replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(Path(__file__).resolve().parent)]
from installer import upgrade_backup as b
from installer import backup_runtime as br
from installer import php_transport as p
from installer import finalization as f
import upgrade_preflight_mariadb as previous

WEB=None


class BackupLive(previous.UpgradePreflightLive):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_UPGRADE_BACKUP_TEST')!='1':raise RuntimeError('Explicit backup opt-in required')
        previous.previous.WEB=WEB
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.runtime=replace(self.runtime,timeout_seconds=120)
        self.backups=self.root/'backups';self.backups.mkdir(mode=0o700)
        self.backup=b.UpgradeBackup(self.runtime,WEB,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT)

    def backup_run(self,**kwargs):
        return self.backup.create_and_verify(self.existing(),self.authority,config_root=self.output,
            backup_root=self.backups,confirmed=True,allow_global_read_lock=True,**kwargs).report()

    def definer_user(self):
        return 'hdf_'+hashlib.sha256(self.db.lower().encode()).hexdigest()[:24]

    def tearDown(self):
        try:
            self.sql([f"DROP USER IF EXISTS `{self.definer_user()}`@'localhost'",
                      f"DROP USER IF EXISTS `{self.definer_user()}`@'127.0.0.2'"])
        finally:
            super().tearDown()

    def legacy_orphaned_fixture(self):
        # Explicit older-version fixture. The original 5C2a source reproduces
        # this state independently in the recorded baseline run. Keep the same
        # negative assertions after fixing future provisioning.
        query=f"SELECT TRIGGER_NAME,EVENT_MANIPULATION,EVENT_OBJECT_TABLE,ACTION_TIMING,ACTION_STATEMENT,ACTION_ORDER,SQL_MODE,CHARACTER_SET_CLIENT,COLLATION_CONNECTION,DATABASE_COLLATION FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='{self.db}' ORDER BY TRIGGER_NAME"
        rows=self.sql(query=query)
        self.assertEqual(len(rows),5)
        statements=[f'USE `{self.db}`', 'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci']
        literal=previous.previous.previous.literal
        for row in rows:
            statements += [f"SET SESSION sql_mode={literal(row['SQL_MODE'])}",
                f"CREATE OR REPLACE DEFINER=`{self.migration}`@`127.0.0.1` TRIGGER `{row['TRIGGER_NAME']}` {row['ACTION_TIMING']} {row['EVENT_MANIPULATION']} ON `{row['EVENT_OBJECT_TABLE']}` FOR EACH ROW {row['ACTION_STATEMENT']}"]
        self.sql(statements)
        self.assertEqual(self.sql(query=query),rows)
        self.sql([f"DROP USER `{self.definer_user()}`@'localhost'"])

    def rescue_run(self, **kwargs):
        values=dict(config_root=self.output,backup_root=self.backups,confirmed=True,allow_global_read_lock=True,
            expected_orphaned_definer=self.migration+'@127.0.0.1')
        values.update(kwargs)
        return self.backup.create_rescue_and_verify(self.existing(),self.authority,**values).report()

    def orphaned_ready(self):
        self.managed_ready();self.legacy_orphaned_fixture()

    def test_backup_rescue_restores_data_without_certifying_or_repairing_source(self):
        self.orphaned_ready();before=self.logical_dump();files=self.files();result=self.rescue_run()
        self.assertEqual(result['state'],'RESCUE_RESTORE_VERIFIED',result)
        self.assertTrue(result['rescue_restoration_verified']);self.assertTrue(result['database_restoration_verified'])
        self.assertEqual(result['trigger_smoke_verified'],5)
        for key in ('backup_verified','operational_source_verified','apply_allowed','rollback_verified','web_activation_verified'):
            self.assertFalse(result[key])
        slot=self.backups/result['backup_id']
        self.assertFalse((slot/'verified.json').exists());self.assertTrue((slot/'rescue-verified.json').exists())
        header=json.loads((slot/'database.ndjson').read_bytes().splitlines()[0])
        self.assertEqual(header['version'],2);self.assertEqual(header['purpose'],'ORPHANED_DEFINER_RESCUE')
        self.assertEqual(before,self.logical_dump());self.assertEqual(files,self.files())
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)
        self.assertEqual(self.backup_run()['code'],'BACKUP_DEFINER_MISSING')
        # A rescue cannot pass the ordinary archive verifier by changing only the caller.
        with self.assertRaisesRegex(b.UpgradeBackupError,'BACKUP_ARCHIVE_INVALID'):
            b._restore(self.runtime,WEB,slot,result['backup_id'],result['database_sha256'])

    def test_backup_rescue_requires_exact_known_orphan_and_both_consents(self):
        self.orphaned_ready();before=self.logical_dump()
        result=self.rescue_run(expected_orphaned_definer='other_missing@127.0.0.1')
        self.assertEqual(result['code'],'BACKUP_RESCUE_PROFILE_REJECTED',result)
        for value in ('root@127.0.0.1',self.definer_user()+'@127.0.0.1',self.migration+'@localhost','x;DROP USER root',None):
            with self.assertRaisesRegex(b.UpgradeBackupError,'BACKUP_RESCUE_PROFILE_REJECTED'):
                self.rescue_run(expected_orphaned_definer=value)
        for key in ('confirmed','allow_global_read_lock'):
            for value in (False,1,'true',None):
                with self.assertRaisesRegex(b.UpgradeBackupError,'BACKUP_CONSENT_REQUIRED'):
                    self.rescue_run(**{key:value})
        self.assertEqual(before,self.logical_dump())

    def test_backup_rescue_nonmanaged_source_is_not_adopted(self):
        self.finish();before=self.logical_dump()
        with self.assertRaisesRegex(b.UpgradeBackupError,'BACKUP_RESCUE_PROFILE_REJECTED'):
            self.rescue_run()
        self.assertEqual(before,self.logical_dump());self.assertFalse(list(self.backups.iterdir()))

    def test_backup_rescue_existing_identity_is_not_adopted(self):
        self.orphaned_ready()
        self.sql([f"CREATE USER `{self.migration}`@'127.0.0.2' ACCOUNT LOCK"])
        try:
            result=self.rescue_run();self.assertEqual(result['code'],'BACKUP_RESCUE_PROFILE_REJECTED',result)
            self.assertFalse((self.backups/result['backup_id']/'rescue-verified.json').exists())
        finally:self.sql([f"DROP USER `{self.migration}`@'127.0.0.2'"])

    def test_backup_rescue_unknown_trigger_is_rejected_without_mutation(self):
        self.orphaned_ready()
        self.sql([f'USE `{self.db}`', 'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci',
            f"CREATE OR REPLACE DEFINER=`{self.migration}`@'127.0.0.1' TRIGGER trg_hestia_page_segment_bi BEFORE INSERT ON P_Activite FOR EACH ROW SET NEW.id_segment=NEW.id_segment"])
        before=self.logical_dump();result=self.rescue_run()
        self.assertEqual(result['code'],'BACKUP_TRIGGER_PROFILE_REJECTED',result)
        self.assertEqual(before,self.logical_dump())

    def test_backup_rescue_corruption_never_produces_a_recovery_receipt(self):
        self.orphaned_ready();restore=b._restore
        def corrupt(runtime,source,slot,*args,**kwargs):
            with (slot/'database.ndjson').open('ab') as file:file.write(b'corrupt\n')
            return restore(runtime,source,slot,*args,**kwargs)
        with patch.object(b,'_restore',side_effect=corrupt):result=self.rescue_run()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result)
        self.assertFalse(result['backup_verified'])
        self.assertFalse((self.backups/result['backup_id']/'rescue-verified.json').exists())

    def managed_ready(self):
        self.managed()
        result=self.prepare(authority=self.authority)
        self.assertEqual(result['state'],'DATABASE_CONFIGURATION_READY',result)
        self.assertEqual(self.finalize()['state'],'WEB_FRESH_FINALIZED')

    def test_backup_managed_durable_definer_five_effects_and_actual_restore(self):
        self.managed_ready()
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)
        rows=self.sql(query=f"SELECT DEFINER FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='{self.db}'")
        self.assertEqual(len(rows),5)
        self.assertEqual({r['DEFINER'] for r in rows},{self.definer_user()+'@localhost'})
        # The fresh path already exercised all five via its actual DML identity.
        # The verifier independently restores them and exercises the same effects.
        before=self.logical_dump();files=self.files();result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED',result)
        self.assertEqual(result['trigger_smoke_verified'],5)
        self.assertEqual(before,self.logical_dump());self.assertEqual(files,self.files())
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM `{self.db}`.P_Activite")[0]['n'],0)
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.db WHERE User='{self.definer_user()}'")[0]['n'],0)
        # No ability to read password hashes, write accounts, or alter a schema.
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.columns_priv WHERE User='{self.definer_user()}' AND Table_name='UserInfo' AND Column_name<>'id_user'")[0]['n'],0)

    def test_backup_managed_definer_unlocked_is_rejected_without_repair(self):
        self.managed_ready();self.sql([f"ALTER USER `{self.definer_user()}`@'localhost' ACCOUNT UNLOCK"])
        before=self.logical_dump();result=self.backup_run()
        self.assertEqual(result['code'],'BACKUP_DEFINER_PROFILE_REJECTED',result)
        self.assertEqual(before,self.logical_dump())

    def test_backup_managed_definer_excess_or_missing_rights_are_rejected(self):
        self.managed_ready();account=f"`{self.definer_user()}`@'localhost'"
        changes=[(f'GRANT SELECT (password_hash) ON `{self.db}`.UserInfo TO {account}',
                  f'REVOKE SELECT (password_hash) ON `{self.db}`.UserInfo FROM {account}'),
                 (f'REVOKE SELECT (id_user) ON `{self.db}`.UserInfo FROM {account}',
                  f'GRANT SELECT (id_user) ON `{self.db}`.UserInfo TO {account}')]
        for change,restore in changes:
            self.sql([change]);result=self.backup_run()
            self.assertEqual(result['code'],'BACKUP_DEFINER_PROFILE_REJECTED',result)
            self.sql([restore])
        self.assertEqual(self.backup_run()['state'],'BACKUP_RESTORE_VERIFIED')

    def test_backup_managed_definer_host_collision_refuses_before_database_creation(self):
        self.managed();self.sql([f"CREATE USER `{self.definer_user()}`@'127.0.0.2' ACCOUNT LOCK"])
        result=self.prepare(authority=self.authority)
        self.assertEqual(result['state'],'REFUSED',result)
        self.assertEqual(result['code'],'DEFINER_ACCOUNT_OCCUPIED',result)
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{self.db}'")[0]['n'],0)

    def test_backup_complete_database_envelope_restored_and_source_unchanged(self):
        self.finish();before=self.logical_dump();files=self.files()
        result=self.backup_run();self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED',result)
        self.assertTrue(result['database_restoration_verified']);self.assertTrue(result['private_files_restoration_verified'])
        self.assertFalse(result['apply_allowed']);self.assertFalse(result['web_activation_verified'])
        self.assertEqual(result['trigger_smoke_verified'],5);self.assertGreater(result['foreign_keys_verified'],0)
        self.assertEqual(before,self.logical_dump());self.assertEqual(files,self.files())
        self.assertFalse(list(self.run.iterdir()))
        slot=self.backups/result['backup_id'];self.assertEqual(slot.stat().st_mode&0o777,0o700)
        self.assertFalse((slot/'restored-files').exists())
        manifest=json.loads((slot/'manifest.json').read_bytes());self.assertEqual(manifest['database_sha256'],result['database_sha256'])
        for path in slot.rglob('*'):
            if path.is_file():self.assertEqual(path.stat().st_mode&0o777,0o600)
        text=json.dumps(result)
        for secret in list(self.payload['secrets'].values())+[self.authority._password,self.credentials._password]:
            if secret:self.assertNotIn(secret,text)
        combined=b''.join(x.read_bytes() for x in slot.rglob('*') if x.is_file())
        self.assertNotIn(self.authority._password.encode(),combined);self.assertNotIn(self.credentials._password.encode(),combined)
        # Expected durable application credential is indeed present ONLY inside private file blobs.
        self.assertIn(json.dumps(self.payload['secrets']['database_password']).encode()[1:-1],combined)

    def test_backup_binary_unicode_numeric_null_and_autoincrement_preserved(self):
        self.finish()
        self.sql(["SET SESSION sql_mode='NO_AUTO_VALUE_ON_ZERO'",f'''CREATE TABLE `{self.db}`.backup_edge(id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT, txt LONGTEXT,
            bin LONGBLOB, n DECIMAL(65,30), f FLOAT, d DOUBLE, bits BIT(9), moment TIMESTAMP NULL,
            choice ENUM('','é','none'), flags SET('a','b')) ENGINE=InnoDB AUTO_INCREMENT=1234''',
            f"INSERT INTO `{self.db}`.backup_edge VALUES (0,'',X'0000FF010A',0,0,0,b'100000001',NULL,'','')",
            f"INSERT INTO `{self.db}`.backup_edge VALUES (18446744073709551615,CONCAT('é 漢字🙂',REPEAT('x',30000)),X'00FF00',12345678901234567890123456789012345.123456789012345678901234567890,0.000123456789,1.23456789123456789,b'000000000','2026-09-25 00:00:00','é','a,b')",
            f'CREATE TABLE `{self.db}`.backup_nopk(a TEXT,b INT) ENGINE=InnoDB',
            f"INSERT INTO `{self.db}`.backup_nopk VALUES(NULL,NULL),('',0),('duplicates',1),('duplicates',1),(CONCAT(REPEAT('x',40000),'z'),3),(CONCAT(REPEAT('x',40000),'a'),3)"])
        before=self.logical_dump();result=self.backup_run();self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED',result)
        self.assertEqual(before,self.logical_dump())
        import struct
        archive=self.backups/result['backup_id']/'database.ndjson';table=None;checked=False;zero=False
        for line in archive.read_bytes().splitlines():
            item=json.loads(line)
            if item['type']=='table':table=item['name']
            if table=='backup_edge' and item['type']=='row' and item['values'][0]=='30':zero=True
            if table=='backup_edge' and item['type']=='row' and item['values'][0]==b'18446744073709551615'.hex().upper():
                value=float(bytes.fromhex(item['values'][4]).decode())
                self.assertEqual(struct.pack('f',value),struct.pack('f',0.000123456789));checked=True
        self.assertTrue(checked);self.assertTrue(zero)

    def test_backup_key_enabled_and_disabled_kept_private(self):
        self.payload['assistant']['action']='configure';self.payload['secrets']['openai_api_key']='test-private-key-'+('Z'*100)
        self.finish();self.settings('disabled');files=self.files();result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED',result);self.assertEqual(files,self.files())
        self.assertNotIn(self.payload['secrets']['openai_api_key'],json.dumps(result))

    def test_backup_global_read_lock_consent_required(self):
        self.finish()
        with self.assertRaisesRegex(b.UpgradeBackupError,'BACKUP_CONSENT_REQUIRED'):
            self.backup.create_and_verify(self.existing(),self.authority,config_root=self.output,backup_root=self.backups,
                confirmed=True,allow_global_read_lock=False)
        self.assertFalse(list(self.backups.iterdir()))

    def test_backup_special_objects_fail_closed_and_no_success_receipt(self):
        self.finish();self.sql([f'CREATE VIEW `{self.db}`.extra_v AS SELECT 1 a'])
        before=self.logical_dump();result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result);self.assertEqual(result['code'],'BACKUP_SPECIAL_OBJECTS_UNSUPPORTED',result)
        self.assertFalse((self.backups/result['backup_id']/'verified.json').exists());self.assertEqual(before,self.logical_dump())

    def test_backup_corrupted_archive_never_verified(self):
        self.finish();restore=b._restore
        def corrupt(runtime,source,slot,*args,**kwargs):
            with (slot/'database.ndjson').open('ab') as file:file.write(b'corrupted\n')
            return restore(runtime,source,slot,*args,**kwargs)
        with patch.object(b,'_restore',side_effect=corrupt):result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE');self.assertFalse(result['backup_verified'])

    def test_backup_unknown_mutable_business_file_refused(self):
        self.finish();uploads=self.webroot/'user-data';uploads.mkdir();(uploads/'x.txt').write_text('business data')
        os.chown(uploads,self.web.pw_uid,self.web.pw_gid)
        result=self.backup_run();self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result)
        self.assertFalse(result['backup_verified']);self.assertEqual((uploads/'x.txt').read_text(),'business data')

    def test_backup_failed_restore_retains_private_artifact_no_source_change(self):
        self.finish();before=self.logical_dump()
        with patch.object(b,'_restore',side_effect=b.UpgradeBackupError('BACKUP_VERIFIER_START_FAILED')):result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE');self.assertEqual(before,self.logical_dump())
        slot=self.backups/result['backup_id'];self.assertTrue((slot/'database.ndjson').is_file());self.assertFalse((slot/'verified.json').exists())

    def test_backup_managed_orphaned_definers_are_not_certified_or_repaired(self):
        self.managed()
        self.assertEqual(self.prepare(authority=self.authority)['state'],'DATABASE_CONFIGURATION_READY')
        self.legacy_orphaned_fixture()
        self.assertEqual(self.finalize()['state'],'WEB_FRESH_FINALIZED')
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)
        rows=self.sql(query=f"SELECT DEFINER FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='{self.db}'")
        self.assertEqual(len(rows),5);self.assertTrue(all(r['DEFINER'].startswith(self.migration+'@') for r in rows))
        # This probes an actual canonical trigger on our disposable source. A
        # missing definer is a SOURCE defect, not permission to invent one.
        script="""$v=json_decode(stream_get_contents(STDIN),true);$p=new PDO('mysql:unix_socket='.$v['socket'].';dbname='.$v['db'],'root','',[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
try {$p->beginTransaction();$p->exec("INSERT INTO Ged_Legacy_Stat(source_table) VALUES ('backup_probe')");echo json_encode(['code'=>0]);}
catch(PDOException $e){echo json_encode(['code'=>$e->errorInfo[1]??0]);}finally{if($p->inTransaction())$p->rollBack();}"""
        probe=subprocess.run(['php','-d','display_errors=0','-d','log_errors=0','-r',script],input=json.dumps({'socket':self.socket,'db':self.db}).encode(),capture_output=True,timeout=10)
        self.assertEqual(probe.returncode,0);self.assertEqual(probe.stderr,b'');self.assertEqual(json.loads(probe.stdout)['code'],1449)
        before=self.logical_dump();result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result);self.assertEqual(result['code'],'BACKUP_DEFINER_MISSING',result)
        self.assertEqual(before,self.logical_dump())
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)

    def test_backup_remote_tls_actual_restore_preserves_source(self):
        self.remote()
        literal=previous.previous.previous.literal
        self.tls.sql([f'CREATE USER {self.auth_account} IDENTIFIED BY {literal(self.authority._password)} REQUIRE SSL',
            f'GRANT ALL PRIVILEGES ON *.* TO {self.auth_account} WITH GRANT OPTION'])
        try:
            self.finish();result=self.backup_run();self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED',result)
            self.assertEqual(result['canonical_triggers_verified'],5)
        finally:self.tls.sql([f'DROP USER IF EXISTS {self.auth_account}'])

    def test_backup_repeated_independent_backups_and_live_session_preserved(self):
        self.finish()
        with self.http() as request:
            status,body,_,_=request('/login.php');self.assertEqual(status,200)
            token=re.search(r'name="csrf_token" value="([a-f0-9]+)"',body).group(1)
            status,_,_,_=request('/login.php',{'csrf_token':token,'identifier':self.payload['administrator']['email'],
                'password':self.payload['secrets']['admin_password']});self.assertEqual(status,200)
            before=self.logical_dump();result=self.backup_run();self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED',result)
            self.assertEqual(before,self.logical_dump())
            status,_,_,url=request('/index.php');self.assertEqual(status,200);self.assertNotIn('login.php',url)
            again=self.backup_run();self.assertEqual(again['state'],'BACKUP_RESTORE_VERIFIED',again)
            self.assertNotEqual(result['backup_id'],again['backup_id'])
            status,_,_,url=request('/logout.php');self.assertEqual(status,200);self.assertIn('login.php',url)

    def test_backup_changed_trigger_is_refused(self):
        self.finish();self.sql([f'DROP TRIGGER `{self.db}`.trg_hestia_ged_click_legacy_ai',
            f'CREATE TRIGGER `{self.db}`.trg_hestia_ged_click_legacy_ai AFTER INSERT ON `{self.db}`.Ged_Legacy_Stat FOR EACH ROW SET @changed=1'])
        result=self.backup_run();self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result)
        self.assertEqual(result['code'],'BACKUP_TRIGGER_PROFILE_REJECTED',result)

    def test_backup_insufficient_source_authority_cannot_export(self):
        self.finish();limited=previous.previous.step.SqlAuthorityCredentials(self.migration,self.credentials._password)
        result=self.backup.create_and_verify(self.existing(),limited,config_root=self.output,backup_root=self.backups,
            confirmed=True,allow_global_read_lock=True).report()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result);self.assertEqual(result['code'],'BACKUP_AUTHORITY_REJECTED',result)

    def test_backup_cancellation_before_reservation(self):
        self.finish();cancel=threading.Event();cancel.set()
        with self.assertRaises(b.UpgradeBackupError):self.backup_run(cancel=cancel)
        self.assertFalse(list(self.backups.iterdir()))

    def test_backup_process_death_before_restore_keeps_unverified_archive(self):
        self.finish();before=self.logical_dump();files=self.files()
        def child():
            with patch.object(b,'_restore',side_effect=lambda *a:os._exit(92)):self.backup_run()
        process=multiprocessing.get_context('fork').Process(target=child);process.start();process.join(60)
        if process.is_alive():process.kill();process.join();self.fail('Backup child did not exit')
        self.assertEqual(process.exitcode,92);self.assertEqual(before,self.logical_dump());self.assertEqual(files,self.files())
        slots=list(self.backups.iterdir());self.assertEqual(len(slots),1)
        self.assertTrue((slots[0]/'manifest.json').exists());self.assertFalse((slots[0]/'verified.json').exists())

    def test_backup_sql_writer_blocked_during_real_export_then_unblocked(self):
        self.finish();original=br.capture;checked=[]
        def capture(command,wire,stage,output,*args,**kwargs):
            if json.loads(wire or b'{}').get('operation')=='export':
                # Test-only probe added to the isolated bridge copy, never the
                # product source/pin. Try UPDATE with a distinct connection.
                path=stage/'bridge.php';text=path.read_text();token='$tables=bk_profile($pdo);$triggers=bk_triggers($pdo);'
                injection="""$test=bk_connection($v['target'],$v['authority']);$test->exec('SET SESSION lock_wait_timeout=1');
                $blocked=false;try {$test->exec("UPDATE App_Config SET valeur=valeur WHERE cle='APP_VERSION'");}
                catch(PDOException $e){$blocked=($e->errorInfo[1]??0)===1205;}
                bk_require($blocked,'BACKUP_PROFILE_REJECTED');$test=null;"""
                self.assertIn(token,text);path.write_text(text.replace(token,injection+token));checked.append(True)
            return original(command,wire,stage,output,*args,**kwargs)
        with patch.object(br,'capture',side_effect=capture):result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED',result);self.assertEqual(checked,[True])
        self.sql([f"UPDATE `{self.db}`.App_Config SET valeur=valeur WHERE cle='APP_VERSION'"])

    def test_backup_foreign_key_orphan_is_not_certified(self):
        self.finish()
        self.sql(['SET FOREIGN_KEY_CHECKS=0',f"INSERT INTO `{self.db}`.Ged_Link_Click_Log(id_link,source) VALUES(-2147483000,'TEST')",'SET FOREIGN_KEY_CHECKS=1'])
        before=self.logical_dump();result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result);self.assertEqual(result['code'],'BACKUP_FOREIGN_KEY_MISMATCH',result)
        self.assertEqual(before,self.logical_dump())

    def test_backup_nontransactional_table_and_routine_are_refused(self):
        self.finish()
        for create,drop in [(f'CREATE TABLE `{self.db}`.unsupported (a INT) ENGINE=MyISAM',f'DROP TABLE `{self.db}`.unsupported'),
                            (f'CREATE PROCEDURE `{self.db}`.unsupported() SELECT 1',f'DROP PROCEDURE `{self.db}`.unsupported')]:
            self.sql([create]);before=self.logical_dump();result=self.backup_run()
            self.assertEqual(result['code'],'BACKUP_SPECIAL_OBJECTS_UNSUPPORTED',result)
            self.assertEqual(before,self.logical_dump())
            # A rejected export must release its real global read lock.
            self.sql([f"UPDATE `{self.db}`.App_Config SET valeur=valeur WHERE cle='APP_VERSION'",drop])

    def test_backup_cancel_during_locked_export_releases_sql_lock(self):
        self.finish();before=self.logical_dump();event=threading.Event();capture=br.capture;observed=[]
        def stop(command,wire,stage,output,*args,**kwargs):
            if json.loads(wire or b'{}').get('operation')=='export':
                class Sink:
                    def write(inner,data):
                        count=output.write(data)
                        if b'"type":"header"' in data:observed.append(True);event.set()
                        return count
                return capture(command,wire,stage,Sink(),*args,**kwargs)
            return capture(command,wire,stage,output,*args,**kwargs)
        with patch.object(br,'capture',side_effect=stop):result=self.backup_run(cancel=event)
        self.assertTrue(observed);self.assertEqual(result['code'],'BACKUP_INTERRUPTED',result)
        self.sql([f"UPDATE `{self.db}`.App_Config SET valeur=valeur WHERE cle='APP_VERSION'"])
        self.assertEqual(before,self.logical_dump());self.assertFalse((self.backups/result['backup_id']/'verified.json').exists())

    def test_backup_corrupt_private_file_copy_is_not_certified(self):
        self.finish();verify=b._verify_files
        def corrupt(slot,records):
            path=next((slot/'files').iterdir());path.write_bytes(b'CORRUPTED')
            return verify(slot,records)
        with patch.object(b,'_verify_files',side_effect=corrupt):result=self.backup_run()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE',result);self.assertEqual(result['code'],'BACKUP_FILES_CHANGED',result)


def main():
    global WEB
    parser=argparse.ArgumentParser();parser.add_argument('--web',type=Path,required=True);parser.add_argument('--report',type=Path)
    parser.add_argument('--test');args=parser.parse_args();WEB=args.web.resolve()
    names=[n for n in unittest.defaultTestLoader.getTestCaseNames(BackupLive) if n.startswith('test_backup_') and (args.test is None or args.test in n)]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(BackupLive(n) for n in names))
    report={'suite':'5C2 private backup and isolated restore','tests':result.testsRun,'expected':len(names),
        'errors':len(result.errors),'failures':len(result.failures),'skips':len(result.skipped),'web_commit':f.WEB_COMMIT,
        'status':'PASS' if result.wasSuccessful() and result.testsRun==len(names) and len(names)>0 and not result.skipped else 'FAIL'}
    if args.report:args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
