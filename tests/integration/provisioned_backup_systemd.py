#!/usr/bin/env python3
"""Actual provisioned drain + continuous SQL fence + restored business data.

Fault injection wraps real operations; no SQL, process census or unit mock.
Only the fixture reopens services and swaps restored data, never the product.
"""
import argparse
from contextlib import contextmanager
from dataclasses import replace
import json
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'scripts'),str(Path(__file__).resolve().parent)]
import quality
import business_storage_systemd as previous
from installer import provisioned_backup as b, http_drain as hd, sql_read_fence as rf
from installer.web_releases import STORAGE_COMMIT, get_release
from http_runtime_systemd import command, until


class ProvisionedBackupLive(previous.BusinessStorageLive):
    # A proxy is a separate producer, never borrow the application identity.
    proxy_user='www-data'

    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_PROVISIONED_BACKUP_TEST')!='1':
            raise RuntimeError('Explicit provisioned backup opt-in required')
        super().setUpClass()

    def setup_backup(self):
        self.ready();self.backups=self.root/'integrated-backups';self.backups.mkdir(mode=0o700)
        self.operation=b.ProvisionedBackup(replace(self.runtime,timeout_seconds=120),previous.previous.WEB,
            self.http_runtime,self.collector)

    def execute(self,**extra):
        args=dict(config_root=self.output,backup_root=self.backups,confirmed=True,allow_global_read_lock=True)
        args.update(extra)
        return self.operation.create_and_verify(self.existing(),self.authority,**args).report()

    def closed(self):
        self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        for unit in (self.http_runtime.unit('apache'),self.http_runtime.unit('php'),self.collector.unit):
            self.assertTrue(previous.drain._empty_cgroup(unit))
            command('systemctl','start',unit)
            self.assertEqual(previous.drain._show(unit)['ActiveState'],'inactive')
        self.collector._timer_state(stopped=True)

    def incomplete(self,result):
        self.assertEqual(result['state'],'COORDINATED_BACKUP_INCOMPLETE',result)
        slot=self.backups/result['backup_id'];self.assertTrue((slot/'attempt.json').is_file())
        self.assertFalse((slot/'verified.json').exists());self.closed()
        self.assertFalse(result['activity_resumed']);self.assertTrue(result['manual_inspection_required'])

    def test_provisioned_real_writes_sql_fence_restore_and_manual_reopening(self):
        self.setup_backup();relative,photo=self.photo();doc,document,doc_data=self.document()
        imported,import_data=self.import_file();current=self.session()
        photo_bytes=photo.read_bytes();session_bytes=current.read_bytes();self.immutable()
        legacy=self.uploads/'ged_legacy'/'archive'/'old.txt';legacy.parent.mkdir(parents=True,mode=0o750)
        legacy.write_bytes(b'Legacy GED inside the admitted root');legacy.chmod(0o640)
        for path in (legacy.parent.parent,legacy.parent,legacy):os.chown(path,self.web.pw_uid,self.web.pw_gid)
        self.sql([f"INSERT INTO `{self.db}`.App_Config(cle,valeur) VALUES ('security.ged_legacy_roots','archive') ON DUPLICATE KEY UPDATE valeur='archive'"])
        secret=(self.directory/'assistant.json').read_bytes()
        original=previous.files.capture_and_verify;checks=[]
        def capture(*args,**kwargs):
            self.assertEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
            # A distinct root SQL session must be prevented from writing too.
            with self.assertRaisesRegex(RuntimeError,r'FIXTURE_SQL_FAILED_(1205|1969)'):
                self.sql(['SET SESSION lock_wait_timeout=1','SET SESSION max_statement_time=2',
                    f"UPDATE `{self.db}`.App_Config SET valeur=valeur WHERE cle='APP_VERSION'"],timeout=4)
            checks.append(True)
            # Actual Installer API cannot edit, journal or probe under the outer lock.
            before=set(self.directory.iterdir())
            with self.assertRaisesRegex(previous.f.FinalizationError,'ASSISTANT_PREFLIGHT_FAILED'):self.settings('disabled')
            self.assertEqual(set(self.directory.iterdir()),before)
            self.assertEqual((self.directory/'assistant.json').read_bytes(),secret)
            with (self.directory/'assistant.json').open('rb') as settings:
                with self.assertRaises(BlockingIOError):fcntl.flock(settings,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return original(*args,**kwargs)
        with patch.object(previous.files,'capture_and_verify',side_effect=capture):result=self.execute()
        self.assertEqual(checks,[True]);self.assertEqual(result['state'],'PROVISIONED_BACKUP_RESTORE_VERIFIED',result)
        self.assertTrue(result['sql_read_fence_verified']);self.assertTrue(result['provisioned_services_drained'])
        self.assertTrue(result['installer_settings_fenced']);self.assertTrue(result['configuration_storage_admitted'])
        self.assertTrue(result['classic_scheduler_absence_observed'])
        self.assertIs(result['host_scheduler_inventory_complete'],False)
        self.assertIs(result['foreign_cli_controlled'],False)
        self.assertEqual(result['registered_roots'],6);self.assertEqual(result['trigger_smoke_verified'],5)
        for key in ('complete_web_backup','storage_inventory_complete','system_wiring_verified','phase5_complete',
                    'activity_resumed','apply_allowed','rollback_verified'):
            self.assertIs(result[key],False)
        # Release is real, not just a receipt field. Same write now completes.
        self.sql([f"UPDATE `{self.db}`.App_Config SET valeur=valeur WHERE cle='APP_VERSION'"])
        for name in ('assistant-edit.lock','assistant.json'):
            with (self.directory/name).open('rb') as settings:fcntl.flock(settings,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.closed();slot=self.backups/result['backup_id']
        self.assertEqual(json.loads((slot/'verified.json').read_bytes()),result)
        manifest=json.loads((slot/'coordinated.json').read_bytes());data=manifest['data_snapshot']
        self.assertEqual(manifest['service_barrier']['profile_sha256'],result['service_profile_sha256'])
        before=set(self.backups.iterdir())
        with self.assertRaises(Exception):self.execute()
        self.assertEqual(set(self.backups.iterdir()),before)
        # Explicit fixture-only restore under the recovered exact attempt.
        with hd.HttpDrain(self.http_runtime,cleaner=self.collector).recover(manifest['lease_id'],confirmed=True) as barrier:
            lease=barrier.maintenance_lease
            snapshot=previous.files.FileSnapshot(slot/'data'/data['snapshot_id'],data['manifest_sha256'],
                self.scope.instance,lease.lease_id,self.web.pw_gid)
            restored=self.backups/'restored';snapshot.restore_new(restored,lease)
            for name in (*b.h.DATA,'uploads'):
                destination=self.http_root/'data'/name
                os.rename(destination,self.root/('retained-'+name))
                os.rename(restored/name.replace('-','_'),destination)
            self.assertEqual(photo.read_bytes(),photo_bytes);self.assertEqual(document.read_bytes(),doc_data)
            self.assertEqual(imported.read_bytes(),import_data);self.assertEqual(current.read_bytes(),session_bytes)
            self.assertEqual(legacy.read_bytes(),b'Legacy GED inside the admitted root')
            barrier.assert_held();lease.resume(confirmed=True)
        self.restart_fixture_services()
        self.assertEqual(self.binary('/'+relative)[:2],(200,photo_bytes))
        self.assertEqual(self.binary('/index.php?page=ged_download&id='+str(doc['id_document']))[:2],(200,doc_data))
        self.immutable()

    def test_provisioned_lost_sql_worker_cannot_certify_and_releases_lock(self):
        self.setup_backup();self.photo();acquire=rf.acquire;capture=previous.files.capture_and_verify;active=[]
        @contextmanager
        def tracked(*args,**kwargs):
            with acquire(*args,**kwargs) as fence:
                active.append(fence);yield fence
        def kill_after_copy(*args,**kwargs):
            saved=capture(*args,**kwargs)
            os.kill(active[0]._process.pid,signal.SIGKILL);active[0]._process.wait(timeout=3)
            return saved
        with patch.object(rf,'acquire',side_effect=tracked), \
             patch.object(previous.files,'capture_and_verify',side_effect=kill_after_copy):result=self.execute()
        self.incomplete(result)
        self.sql([f"UPDATE `{self.db}`.App_Config SET valeur=valeur WHERE cle='APP_VERSION'"])

    def test_provisioned_changed_service_barrier_after_copy_cannot_certify(self):
        self.setup_backup();capture=previous.files.capture_and_verify;changed=[]
        def drift(*args,**kwargs):
            result=capture(*args,**kwargs)
            path=next(self.scope.directory.glob('http-drain-*.attempt'));changed.append((path,path.read_bytes()))
            path.write_bytes(b'{}');return result
        try:
            with patch.object(previous.files,'capture_and_verify',side_effect=drift):result=self.execute()
            self.incomplete(result)
        finally:
            for path,data in changed:path.write_bytes(data)

    def test_provisioned_foreign_identity_process_refused_before_gate(self):
        self.setup_backup()
        proc=subprocess.Popen(['setpriv','--reuid='+str(self.web.pw_uid),'--regid='+str(self.web.pw_gid),
            '--clear-groups','sleep','60'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            until(lambda:Path('/proc/'+str(proc.pid)+'/status').exists() and
                ('Uid:\t'+str(self.web.pw_uid)+'\t') in Path('/proc/'+str(proc.pid)+'/status').read_text(),timeout=3)
            with self.assertRaisesRegex(hd.HttpDrainError,'FOREIGN_IDENTITY'):self.execute()
            self.assertIsNone(proc.poll());self.assertEqual(list(self.backups.iterdir()),[])
            self.assertNotEqual(self.scope.observe()['state'],'MAINTENANCE_REQUIRED')
        finally:proc.terminate();proc.wait(timeout=3)

    def test_provisioned_extra_data_root_refused_without_silent_omission(self):
        self.setup_backup();(self.http_root/'data'/'unknown-writer').mkdir(mode=0o700)
        with self.assertRaisesRegex(b.c.CoordinatedBackupError,'PROVISIONED_ROOTS_REQUIRED'):self.execute()
        self.assertEqual(list(self.backups.iterdir()),[]);self.closed()

    def test_provisioned_cancellation_after_copy_leaves_incomplete_attempt(self):
        self.setup_backup();event=threading.Event();capture=previous.files.capture_and_verify
        def cancelled(*args,**kwargs):
            result=capture(*args,**kwargs);event.set();return result
        with patch.object(previous.files,'capture_and_verify',side_effect=cancelled):result=self.execute(cancel=event)
        self.incomplete(result)

    def test_provisioned_wrong_sql_authority_refused_before_backup_reservation(self):
        self.setup_backup();self.authority=b.d.SqlAuthorityCredentials(self.authority._user,'wrong-fixture-only')
        with self.assertRaises(b.c.CoordinatedBackupError):self.execute()
        self.assertEqual(list(self.backups.iterdir()),[]);self.closed()

    def admission_refused(self,code):
        with self.assertRaisesRegex(b.c.CoordinatedBackupError,code):self.execute()
        self.assertEqual(list(self.backups.iterdir()),[]);self.closed()

    def test_provisioned_foreign_sql_schema_refused_without_modification(self):
        self.setup_backup();name=self.db+'_foreign';self.sql([f'CREATE DATABASE `{name}`'])
        try:
            self.admission_refused('SQL_FENCE_SERVER_PROFILE_REJECTED')
            self.assertEqual(self.sql(query=f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{name}'"),[{'SCHEMA_NAME':name}])
        finally:self.sql([f'DROP DATABASE `{name}`'])

    def test_provisioned_external_mobile_setting_refused(self):
        self.setup_backup()
        self.sql([f"INSERT INTO `{self.db}`.App_Config(cle,valeur) VALUES ('HESTIA_MOBILE_RELEASE_DIR','/srv/unsupported-fixture') ON DUPLICATE KEY UPDATE valeur='/srv/unsupported-fixture'"])
        self.admission_refused('SQL_FENCE_STORAGE_PROFILE_REJECTED')

    def test_provisioned_traversing_ged_setting_refused(self):
        self.setup_backup()
        self.sql([f"INSERT INTO `{self.db}`.App_Config(cle,valeur) VALUES ('security.ged_legacy_roots','../outside') ON DUPLICATE KEY UPDATE valeur='../outside'"])
        self.admission_refused('SQL_FENCE_STORAGE_PROFILE_REJECTED')

    def test_provisioned_legacy_ai_usage_refused_without_read_or_removal(self):
        self.setup_backup();path=Path('/var/lib/hestia-ai');self.assertFalse(path.exists());path.mkdir(mode=0o700)
        try:
            self.admission_refused('PROVISIONED_EXTERNAL_STORAGE_REJECTED');self.assertTrue(path.is_dir())
        finally:path.rmdir()

    def test_provisioned_settings_writer_refused_before_reservation(self):
        self.setup_backup()
        with (self.directory/'assistant.json').open('rb') as settings:
            fcntl.flock(settings,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.admission_refused('PROVISIONED_SETTINGS_BUSY')

    def test_provisioned_legacy_ai_appearing_after_copy_invalidates_receipt(self):
        self.setup_backup();path=Path('/var/lib/hestia-ai');self.assertFalse(path.exists())
        capture=previous.files.capture_and_verify
        def drift(*args,**kwargs):
            result=capture(*args,**kwargs);path.mkdir(mode=0o700);return result
        try:
            with patch.object(previous.files,'capture_and_verify',side_effect=drift):result=self.execute()
            self.incomplete(result);self.assertEqual(result['code'],'PROVISIONED_EXTERNAL_STORAGE_REJECTED')
        finally:
            if path.exists():path.rmdir()

    def scheduler_refused_before_gate(self):
        before=self.scope.observe()
        with self.assertRaisesRegex(b.sa.SchedulerAdmissionError,b.sa.REJECTED):self.execute()
        self.assertEqual(self.scope.observe(),before)
        self.assertEqual(list(self.backups.iterdir()),[])

    def test_provisioned_inactive_installed_classic_timer_refused(self):
        self.setup_backup();path=Path('/etc/systemd/system/cron-hestia-fixture.timer')
        self.assertFalse(path.exists())
        data=b'[Timer]\nOnCalendar=yearly\nUnit=cron-hestia-fixture.service\n'
        path.write_bytes(data);path.chmod(0o644)
        try:
            # No start and no show/load call: the installed population alone
            # must reject a disabled timer which has never had an invocation.
            command('systemctl','daemon-reload')
            self.scheduler_refused_before_gate();self.assertEqual(path.read_bytes(),data)
        finally:path.unlink();command('systemctl','daemon-reload')

    def test_provisioned_loaded_transient_classic_service_refused(self):
        self.setup_backup();unit='cron-hestia-fixture.service'
        command('systemd-run','--unit='+unit,'--property=RemainAfterExit=yes','/usr/bin/true')
        try:
            state=lambda:command('systemctl','show','--property=ActiveState','--value',unit).stdout.strip()
            until(lambda:state()==b'active',timeout=5)
            self.scheduler_refused_before_gate()
            self.assertEqual(state(),b'active')
        finally:command('systemctl','stop',unit)

    def test_provisioned_classic_spool_refused_without_read_or_removal(self):
        self.setup_backup();path=Path('/var/spool/cron');self.assertFalse(path.exists())
        path.mkdir(mode=0o700);queue=path/'crontabs';queue.mkdir(mode=0o700)
        job=queue/self.web.pw_name;data=b'PRIVATE_QUEUED_JOB\xff\n';job.write_bytes(data)
        try:
            self.scheduler_refused_before_gate();self.assertEqual(job.read_bytes(),data)
        finally:job.unlink();queue.rmdir();path.rmdir()

    def test_provisioned_classic_config_appearing_after_copy_invalidates_receipt(self):
        self.setup_backup();path=Path('/etc/crontab');self.assertFalse(path.exists())
        capture=previous.files.capture_and_verify;data=b'PRIVATE_QUEUED_JOB\xff\n'
        def drift(*args,**kwargs):
            saved=capture(*args,**kwargs);path.write_bytes(data);return saved
        try:
            with patch.object(previous.files,'capture_and_verify',side_effect=drift):result=self.execute()
            self.incomplete(result);self.assertEqual(result['code'],b.sa.REJECTED)
            self.assertEqual(path.read_bytes(),data)
            self.sql([f"UPDATE `{self.db}`.App_Config SET valeur=valeur WHERE cle='APP_VERSION'"])
        finally:
            if path.exists():path.unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--web',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True);args=parser.parse_args();previous.previous.WEB=args.web
    source=quality.snapshot(ROOT)
    names=sorted(n for n in ProvisionedBackupLive.__dict__ if n.startswith('test_provisioned_'))
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ProvisionedBackupLive(n) for n in names))
    stable=source==quality.snapshot(ROOT);release=get_release(STORAGE_COMMIT)
    report={'suite':'Provisioned services configuration and classic scheduler admission','tests':result.testsRun,'expected':17,
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun==17 and not result.skipped and stable else 'FAIL',
        'source_stable':stable,'source_files':len(source),'web_commit':release.commit,'web_tree':release.tree,
        'database_profile':'fresh_managed','proxy_identity_separate':True,'service_activation_delivered':False,
        'classic_scheduler_admission_tested':True,'host_scheduler_inventory_complete':False,'foreign_cli_controlled':False,
        'storage_inventory_complete':False,'complete_web_backup':False,'application_installed':False,'phase5_complete':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    (args.report.parent/'PROVISIONED-BACKUP-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
    sys.exit(0 if report['status']=='PASS' else 1)
