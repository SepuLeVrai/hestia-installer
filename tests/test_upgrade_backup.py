"""5C2a private filesystem/protocol tests; real SQL lives in the opt-in recipe.

SQL transport is stubbed explicitly here, never presented as restore proof.
All historical tests remain defined in their own original modules.
"""
import copy
from dataclasses import FrozenInstanceError
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import subprocess
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from installer import upgrade_backup as b
from installer import backup_runtime as br
from installer import upgrade_preflight as u
from installer import finalization as f
from installer import database_step as db
from installer import database_config as fs
from installer import php_transport as p
from sql_accounts_fixture import ProtectedConfigurationFixture
from test_upgrade_preflight import inventory, probe


class BackupFilesystemTests(ProtectedConfigurationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config=f._configuration(self.payload,fresh=True);self.directory.mkdir(mode=0o700)
        with fs._directory(self.directory) as fd:
            result={'scope':'DATABASE_CONFIGURATION_READY','configuration_activated':False,'application_installed':False,
                'assistant_enabled':False,'tls_verified':False,'application_verified':True,'migration_retained':True,'version':p.ENGINE_VERSION}
            db._configuration_files(fd,self.directory,self.web.pw_gid,config,self.payload,None,result)
        marker=db._marker(self.runtime,db._target(config,None,None),'a'*32)
        os.write(marker,p._json({'state':'DATABASE_CONFIGURATION_READY','code':'OK'})+b'\n');os.fsync(marker);os.close(marker)
        (self.webroot/'includes/installation').mkdir();(self.webroot/'includes/installation/activation.php').write_text('<?php // fixture')
        for obj,name,value in ((f.FinalizationStep,'_sources',None),(f,'_probe',probe()),(u,'_inventory',inventory())):
            m=patch.object(obj,name,return_value=value);m.start();self.addCleanup(m.stop)
        with patch.object(f,'_sql',return_value={'database_verified':True,'assistant_enabled':False}):
            result=f.FinalizationStep(self.runtime,self.webroot,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT).finalize(
                self.payload,config_root=self.output,confirmed=True)
            self.assertEqual(result['state'],'WEB_FRESH_FINALIZED')
        self.request=copy.deepcopy(self.payload);self.request.update(mode='upgrade',administrator=None)
        self.request['assistant']['action']='preserve';self.request['secrets'].update(admin_password='',openai_api_key='')
        self.backups=self.root/'backup';self.backups.mkdir(mode=0o700)
        self.authority=db.SqlAuthorityCredentials('backup_fixture','private-authority-fixture')
        self.backup=b.UpgradeBackup(self.runtime,self.webroot,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT)
        m=patch.object(b,'_worker_stage');m.start();self.addCleanup(m.stop)
        m=patch.object(br,'capture',side_effect=self.capture);self.transport=m.start();self.addCleanup(m.stop)
        self.proof={'tables':129,'rows':'1','logical_sha256':'f'*64,'server_version':'11.8.6-MariaDB',
            'verification_objects_removed':True,'canonical_triggers_verified':5,'trigger_smoke_verified':5,'foreign_keys_verified':100}
        self.original_restore=b._restore
        m=patch.object(b,'_restore',return_value=self.proof);self.restore=m.start();self.addCleanup(m.stop)

    def capture(self,command,wire,stage,output,*args,**kwargs):
        request=json.loads(wire);self.assertEqual(request['operation'],'export')
        self.assertEqual(request['authority']['password'],self.authority._password)
        self.assertNotIn(self.authority._password,str(command));self.assertNotIn(self.authority._password,str(stage))
        raw=p._json({'type':'complete','request_id':request['request_id'],'tables':129,'rows':'1','logical_sha256':'f'*64})+b'\n'
        output.write(raw);return 0,hashlib.sha256(raw).hexdigest(),len(raw)

    def execute(self,**kwargs):
        params=dict(config_root=self.output,backup_root=self.backups,confirmed=True,allow_global_read_lock=True)
        params.update(kwargs)
        return self.backup.create_and_verify(self.request,self.authority,**params)

    def snapshot(self):
        return {str(path):(path.read_bytes(),path.stat().st_mode,path.stat().st_uid,path.stat().st_gid,path.stat().st_mtime_ns)
            for root in (self.webroot,self.output,self.runtime.state_root) for path in root.rglob('*') if path.is_file()}

    def test_private_files_actual_copy_and_source_preserved(self):
        before=self.snapshot();result=self.execute().report()
        self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED');self.assertEqual(before,self.snapshot())
        self.assertEqual(self.transport.call_count,1);self.restore.assert_called_once()
        slot=self.backups/result['backup_id'];self.assertFalse((slot/'restored-files').exists())
        self.assertEqual(json.loads((slot/'verified.json').read_bytes()),result)
        for path in slot.rglob('*'):
            if path.is_file():self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(slot.stat().st_mode),0o700)

    def test_report_immutable_and_never_authorizes_upgrade(self):
        verification=self.execute();report=verification.report();report['apply_allowed']=True
        self.assertFalse(verification.report()['apply_allowed'])
        with self.assertRaises(FrozenInstanceError):verification._canonical=b'changed'
        for key in ('restore_to_original_allowed','rollback_verified','application_installed','web_activation_verified'):
            self.assertIs(verification.report()[key],False)
        self.assertNotIn(self.authority._password,repr(verification))

    def test_no_privileged_secret_or_input_mutation(self):
        original=copy.deepcopy(self.request);report=self.execute().report();self.assertEqual(original,self.request)
        for secret in self.payload['secrets'].values():
            if secret:self.assertNotIn(secret,json.dumps(report))
        slot=self.backups/report['backup_id']
        self.assertNotIn(self.authority._password.encode(),b''.join(x.read_bytes() for x in slot.rglob('*') if x.is_file()))

    def test_both_consents_must_be_exact_true(self):
        for key in ('confirmed','allow_global_read_lock'):
            for value in (False,1,'true',None):
                with self.assertRaisesRegex(b.UpgradeBackupError,'BACKUP_CONSENT_REQUIRED'):self.execute(**{key:value})
        self.transport.assert_not_called();self.assertFalse(list(self.backups.iterdir()))

    def test_account_separation_and_authority_type(self):
        for auth in (None,{},self.credentials):
            with self.assertRaises(b.UpgradeBackupError):
                self.backup.create_and_verify(self.request,auth,config_root=self.output,backup_root=self.backups,confirmed=True,allow_global_read_lock=True)
        for user,password in ((self.request['database']['user'],'other-password-fixture'),('other_user',self.request['secrets']['database_password'])):
            self.authority=db.SqlAuthorityCredentials(user,password)
            with self.assertRaisesRegex(b.UpgradeBackupError,'ACCOUNT_SEPARATION_REQUIRED'):self.execute()
        self.transport.assert_not_called()

    def test_no_generic_source_pin_or_restore_method(self):
        for commit in ('main','0'*40,None):
            with self.assertRaisesRegex(b.UpgradeBackupError,'SOURCE_PIN_MISMATCH'):
                b.UpgradeBackup(self.runtime,self.webroot,repository=p.WEB_REPOSITORY,commit=commit)
        self.assertFalse(hasattr(b.UpgradeBackup,'apply'));self.assertFalse(hasattr(b.UpgradeBackup,'restore_to_source'))

    def test_fresh_or_assistant_changes_are_refused(self):
        for action in ('configure','disabled'):
            self.request['assistant']['action']=action
            with self.assertRaises(b.UpgradeBackupError):self.execute()
        self.request=copy.deepcopy(self.payload)
        with self.assertRaises(b.UpgradeBackupError):self.execute()
        self.transport.assert_not_called()

    def test_public_and_overlapping_backup_paths_refused(self):
        for path in (self.webroot,self.directory,self.run,self.runtime.state_root,self.webroot/'backup',Path('/tmp/backup'),Path('/var/www/backup')):
            with self.assertRaises(b.UpgradeBackupError):self.execute(backup_root=path)
        self.transport.assert_not_called()

    def test_backup_parent_must_be_root_private_no_links(self):
        self.backups.chmod(0o750)
        with self.assertRaises(b.UpgradeBackupError):self.execute()
        self.backups.chmod(0o700);link=self.root/'alias';link.symlink_to(self.backups,target_is_directory=True)
        with self.assertRaises(b.UpgradeBackupError):self.execute(backup_root=link)
        self.transport.assert_not_called()

    def test_pre_cancel_creates_no_slot(self):
        event=threading.Event();event.set()
        with self.assertRaises(b.UpgradeBackupError):self.execute(cancel=event)
        self.assertFalse(list(self.backups.iterdir()));self.transport.assert_not_called()

    def test_settings_lock_blocks_backup_without_mutation(self):
        with (self.directory/'assistant.json').open('rb') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(b.UpgradeBackupError):self.execute()
        self.assertFalse(list(self.backups.iterdir()));self.transport.assert_not_called()

    def test_file_change_during_export_never_certified(self):
        def change(*args,**kwargs):
            result=self.capture(*args,**kwargs);(self.webroot/'new-data.txt').write_text('changed');return result
        self.transport.side_effect=change;result=self.execute().report()
        self.assertEqual(result['code'],'BACKUP_FILES_CHANGED');self.assertFalse(result['backup_verified']);self.restore.assert_not_called()

    def test_file_restore_mismatch_is_closed(self):
        with patch.object(b,'_verify_files',side_effect=b.UpgradeBackupError('BACKUP_FILE_RESTORE_MISMATCH')):result=self.execute().report()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE');self.restore.assert_not_called()
        self.assertFalse((self.backups/result['backup_id']/'verified.json').exists())

    def test_worker_unknown_diagnostic_does_not_leak(self):
        def fail(command,wire,stage,output,*args,**kwargs):
            request=json.loads(wire);raw=p._json({'version':1,'ok':False,'result':None,'error':self.authority._password,'request_id':request['request_id']})+b'\n'
            output.write(raw);return 20,hashlib.sha256(raw).hexdigest(),len(raw)
        self.transport.side_effect=fail;result=self.execute().report()
        self.assertEqual(result['code'],'BACKUP_PROTOCOL_REJECTED');self.assertNotIn(self.authority._password,json.dumps(result))

    def test_restore_error_keeps_private_snapshot_without_verified_receipt(self):
        self.restore.side_effect=b.UpgradeBackupError('BACKUP_VERIFIER_START_FAILED');before=self.snapshot();result=self.execute().report()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE');self.assertEqual(before,self.snapshot())
        slot=self.backups/result['backup_id'];self.assertTrue((slot/'database.ndjson').is_file());self.assertFalse((slot/'verified.json').exists())

    def test_invalid_restore_counts_are_not_success(self):
        self.proof['rows']='2';result=self.execute().report()
        self.assertEqual(result['code'],'BACKUP_RESTORE_MISMATCH');self.assertFalse(result['backup_verified'])

    def test_unexpected_error_is_sanitized(self):
        self.restore.side_effect=RuntimeError(self.authority._password);result=self.execute().report()
        self.assertEqual(result['code'],'BACKUP_OPERATION_UNAVAILABLE');self.assertNotIn(self.authority._password,json.dumps(result))

    def test_disk_failure_retains_private_incomplete_slot(self):
        original=b._new_file
        def write(path,data):
            if path.name=='manifest.json':raise OSError('Synthetic secret-filled disk diagnostic')
            return original(path,data)
        with patch.object(b,'_new_file',side_effect=write):result=self.execute().report()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE');self.assertFalse(result['backup_verified'])
        self.assertFalse((self.backups/result['backup_id']/'verified.json').exists())

    def test_independent_backups_do_not_overwrite_previous_artifacts(self):
        first=self.execute().report();slot=self.backups/first['backup_id'];saved=(slot/'verified.json').read_bytes()
        second=self.execute().report();self.assertNotEqual(first['backup_id'],second['backup_id']);self.assertEqual(saved,(slot/'verified.json').read_bytes())

    def test_unicode_empty_files_and_empty_directories_are_restored(self):
        (self.webroot/'données & vide').mkdir();(self.webroot/'données & vide'/'élève 漢字.txt').write_bytes(b'')
        result=self.execute().report();self.assertEqual(result['state'],'BACKUP_RESTORE_VERIFIED')
        manifest=json.loads((self.backups/result['backup_id']/'manifest.json').read_bytes())
        self.assertTrue(any(x['path']=='données & vide/élève 漢字.txt' and x['bytes']==0 for x in manifest['files'] if x['kind']=='file'))

    def test_mutable_or_linked_file_is_refused_not_omitted(self):
        path=self.webroot/'business.txt';path.write_text('data');path.chmod(0o666)
        result=self.execute().report();self.assertEqual(result['code'],'BACKUP_FILE_PROFILE_UNSUPPORTED');self.transport.assert_not_called()
        path.unlink();path.symlink_to(self.directory/'database.json')
        result=self.execute().report();self.assertFalse(result['backup_verified']);self.transport.assert_not_called()

    def test_new_file_never_overwrites_existing_one(self):
        path=self.backups/'occupied';b._new_file(path,b'original')
        with self.assertRaises(FileExistsError):b._new_file(path,b'replacement')
        self.assertEqual(path.read_bytes(),b'original')

    def test_hash_rejects_hardlinks_and_world_writable_files(self):
        path=self.backups/'hash';b._new_file(path,b'contents');self.assertEqual(b._hash(path),hashlib.sha256(b'contents').hexdigest())
        other=self.backups/'hardlink';os.link(path,other)
        with self.assertRaises(b.UpgradeBackupError):b._hash(path)
        other.unlink();path.chmod(0o666)
        with self.assertRaises(b.UpgradeBackupError):b._hash(path)

    def test_manifest_path_traversal_refused(self):
        slot=self.backups/'slot';slot.mkdir()
        with self.assertRaises(b.UpgradeBackupError):b._verify_files(slot,[{'scope':'web','path':'../../escape','kind':'directory'}])
        self.assertFalse((self.backups/'escape').exists())

    def test_failure_after_receipt_write_revokes_only_own_receipt(self):
        original=b._new_file
        def write(path,data):
            original(path,data)
            if path.name=='verified.json':raise OSError('post-write fixture failure')
        with patch.object(b,'_new_file',side_effect=write):result=self.execute().report()
        slot=self.backups/result['backup_id'];self.assertEqual(result['state'],'BACKUP_INCOMPLETE')
        self.assertFalse((slot/'verified.json').exists());self.assertTrue((slot/'database.ndjson').exists())

    def test_cancel_after_restore_does_not_publish_success(self):
        event=threading.Event()
        def cancel(*args):event.set();return self.proof
        self.restore.side_effect=cancel;result=self.execute(cancel=event).report()
        self.assertEqual(result['code'],'BACKUP_INTERRUPTED');self.assertFalse((self.backups/result['backup_id']/'verified.json').exists())

    def test_restore_protocol_rejects_extra_fields_and_untrusted_errors(self):
        from contextlib import nullcontext
        slot=self.backups/'protocol';slot.mkdir();b._new_file(slot/'database.ndjson',b'fixture')
        request='a'*32;response={'version':1,'request_id':request,'ok':True,'result':self.proof,'error':None}
        # Exercise the actual decoder; only server launch/transport are fixture boundaries.
        with patch.object(br,'verification_server',return_value=nullcontext()),patch.object(p,'_exchange') as exchange:
            # The original function is retained below before its explicit setUp patch.
            for key,value in [('extra','secret'),('version',True),('request_id','b'*32),('ok',1),('error','private')]:
                malformed=copy.deepcopy(response);malformed[key]=value;exchange.return_value=(0,p._json(malformed))
                with self.assertRaises(b.UpgradeBackupError):self.original_restore(self.runtime,self.webroot,slot,request,b._hash(slot/'database.ndjson'))

    def test_backup_file_and_directory_limits_are_not_silent_omissions(self):
        for limit in (1,2):
            with patch.object(b,'MAX_FILES',limit):result=self.execute().report()
            self.assertEqual(result['code'],'BACKUP_FILES_LIMIT');self.assertFalse(result['backup_verified'])
        self.transport.assert_not_called()

    def test_insufficient_space_refused_before_slot(self):
        with patch.object(b.shutil,'disk_usage',return_value=type('Disk',(),{'free':0})()):
            with self.assertRaisesRegex(b.UpgradeBackupError,'BACKUP_FREE_SPACE_REQUIRED'):self.execute()
        self.assertFalse(list(self.backups.iterdir()));self.transport.assert_not_called()

    def test_failure_at_reservation_fsync_is_closed(self):
        original=os.fsync;count=[]
        def sync(fd):
            count.append(fd)
            if len(count)==1:raise OSError('fsync fixture')
            return original(fd)
        with patch.object(b.os,'fsync',side_effect=sync):result=self.execute().report()
        self.assertEqual(result['state'],'BACKUP_INCOMPLETE');self.assertFalse(result['backup_verified'])


class BackupRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hestia-backup-pipes-');self.stage=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def capture(self,code,wire=b'',**kwargs):
        output=io.BytesIO();result=br.capture([sys.executable,'-c',code],wire,self.stage,output,kwargs.pop('timeout',5),**kwargs)
        return result,output.getvalue()

    def test_binary_stdin_and_output_digest(self):
        wire=b'private-test\x00\xff\n';result,out=self.capture('import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())',wire)
        self.assertEqual(out,wire);self.assertEqual(result,(0,hashlib.sha256(wire).hexdigest(),len(wire)))

    def test_environment_is_not_inherited(self):
        with patch.dict(os.environ,{'HESTIA_BACKUP_TEST_SECRET':'not-inherited','OPENAI_API_KEY':'not-inherited'}):
            _,out=self.capture("import os,json;print(json.dumps({k:v for k,v in os.environ.items() if 'SECRET' in k or 'KEY' in k}))")
        self.assertEqual(json.loads(out),{})

    def test_stdout_limit_is_enforced(self):
        with self.assertRaisesRegex(br.BackupRuntimeError,'BACKUP_OUTPUT_LIMIT'):
            self.capture("import sys;sys.stdout.write('x'*4096)",limit=1024)

    def test_stderr_is_rejected_and_not_reported(self):
        with self.assertRaisesRegex(br.BackupRuntimeError,'^BACKUP_UNEXPECTED_STDERR$'):
            self.capture("import sys;sys.stderr.write('synthetic private diagnostic')")

    def test_stderr_limit_even_when_allowed(self):
        with self.assertRaisesRegex(br.BackupRuntimeError,'BACKUP_DIAGNOSTIC_LIMIT'):
            self.capture("import sys;sys.stderr.write('x'*70000)",allow_stderr=True)

    def test_timeout_terminates_process_group(self):
        with self.assertRaisesRegex(br.BackupRuntimeError,'BACKUP_TIMEOUT'):
            self.capture('import time;time.sleep(20)',timeout=.5)

    def test_cancel_interrupts_process_and_closes_channels(self):
        event=threading.Event();event.set()
        with self.assertRaisesRegex(br.BackupRuntimeError,'BACKUP_INTERRUPTED'):
            self.capture('import time;time.sleep(20)',cancel=event)

    def test_oversized_wire_refused_before_spawn(self):
        with patch.object(br.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(br.BackupRuntimeError,'BACKUP_INPUT_LIMIT'):
                self.capture('pass',b'x'*(p.MAX_INPUT+1))
            spawn.assert_not_called()

    def test_short_disk_write_is_failure(self):
        output=unittest.mock.Mock();output.write.return_value=0
        with self.assertRaisesRegex(br.BackupRuntimeError,'BACKUP_DISK_FAILED'):
            br.capture([sys.executable,'-c',"print('data')"],b'',self.stage,output,5)

    def test_dying_leader_does_not_leave_pipe_descendant_running(self):
        code="import os,time;pid=os.fork();\nif pid: os._exit(0)\nopen('descendant','w').write(str(os.getpid()));time.sleep(20)"
        process=subprocess.Popen([sys.executable,'-c',code],cwd=self.stage,stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
        try:
            ready=self.stage/'descendant';deadline=time.monotonic()+5
            while (not ready.exists() or process.poll() is None) and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(ready.exists());self.assertEqual(process.poll(),0)
            pid=int(ready.read_text());br._stop(process);status=Path('/proc')/str(pid)/'status'
            deadline=time.monotonic()+2
            while status.exists() and 'State:\tZ' not in status.read_text() and time.monotonic()<deadline:time.sleep(.01)
            if status.exists():self.assertIn('State:\tZ',status.read_text())
        finally:br._stop(process)

    def test_verification_root_is_exclusive(self):
        (self.stage/'verify').mkdir();runtime=unittest.mock.Mock()
        with self.assertRaises(FileExistsError):
            with br.verification_server(runtime,self.stage):self.fail('Existing verifier accepted')
        self.assertTrue((self.stage/'verify').is_dir())
