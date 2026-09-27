"""Private composition contracts; real system/SQL proof is in the integrated recipe."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pickle
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from installer import provisioned_backup as b, sql_read_fence as r
from web_configuration_fixture import local_request


class ProvisionedBackupTests(unittest.TestCase):
    def setUp(self):
        observation=patch.object(b.sa,'_observe',return_value=('unit-fixture',))
        observation.start();self.addCleanup(observation.stop)
        self.http=b.h.HttpRuntime(b.h.RuntimeSpec('a'*32,Path('/var/lib/hestia-managed'),Path('/srv/hestia-managed'),
            'hestia-managed','hestia.test',8123,'8.4',external_uploads=True,
            maintenance_directory=Path('/var/lib/hestia-config/slot/maintenance')))
        self.cleaner=b.sc.SessionCleaner(self.http)
        self.runtime=b.p.PhpRuntime(Path('/usr/bin/php8.4'),Path('/usr/lib/php/20240924'),992,992,
            Path('/var/lib/run'),Path('/var/lib/state'))
        self.operation=b.ProvisionedBackup(self.runtime,Path('/srv/source'),self.http,self.cleaner)
        self.payload=local_request();self.payload.update(mode='upgrade',administrator=None)
        self.payload['assistant']['action']='preserve';self.payload['secrets'].update(admin_password='',openai_api_key='')
        self.payload['web'].update(webroot=str(self.http.spec.webroot),service_user=self.http.spec.service_user)
        self.authority=b.d.SqlAuthorityCredentials('authority','private-sql-fixture')

    def execute(self,**extra):
        args=dict(config_root=Path('/var/lib/hestia-config'),backup_root=Path('/var/lib/backups'),
            confirmed=True,allow_global_read_lock=True);args.update(extra)
        return self.operation.create_and_verify(self.payload,self.authority,**args)

    def test_typed_provisioners_and_same_collector_are_required(self):
        for http,cleaner in [(object(),self.cleaner),(self.http,None),(self.http,b.sc.SessionCleaner(b.h.HttpRuntime(self.http.spec)))]:
            with self.assertRaises(b.ProvisionedBackupError):b.ProvisionedBackup(self.runtime,Path('/srv/source'),http,cleaner)
        old=b.h.HttpRuntime(replace(self.http.spec,external_uploads=False,maintenance_directory=None))
        with self.assertRaisesRegex(b.ProvisionedBackupError,'PROFILE_REQUIRED'):
            b.ProvisionedBackup(self.runtime,Path('/srv/source'),old,b.sc.SessionCleaner(old))

    def test_consent_payload_and_instance_reject_before_any_drain(self):
        with patch.object(b.hd.HttpDrain,'acquire') as acquire:
            for key in ('confirmed','allow_global_read_lock'):
                for bad in (False,1,'yes',None):
                    with self.assertRaisesRegex(b.ProvisionedBackupError,'CONSENT_REQUIRED'):self.execute(**{key:bad})
            with self.assertRaisesRegex(b.ProvisionedBackupError,'INSTANCE_MISMATCH'):
                self.execute(config_root=Path('/var/lib/foreign'))
            self.payload['assistant']={'action':'configure'}
            with self.assertRaisesRegex(b.ProvisionedBackupError,'INPUT_REJECTED'):self.execute()
            acquire.assert_not_called()

    def test_cancel_and_mutated_collector_reject_before_drain(self):
        event=threading.Event();event.set()
        with patch.object(b.hd.HttpDrain,'acquire') as acquire:
            with self.assertRaisesRegex(b.ProvisionedBackupError,'INTERRUPTED'):self.execute(cancel=event)
            self.cleaner.runtime=b.h.HttpRuntime(self.http.spec)
            with self.assertRaisesRegex(b.ProvisionedBackupError,'PROFILE_REQUIRED'):self.execute()
            acquire.assert_not_called()

    def test_exact_roots_live_barrier_passed_and_closed_without_resume(self):
        events=[];maintenance=object();barrier=SimpleNamespace(maintenance_lease=maintenance)
        @contextmanager
        def acquire(*a,**kw):
            events.append('drain')
            try:yield barrier
            finally:events.append('close')
        def compose(*a,**kw):
            self.assertIs(type(kw['scheduler_observation']),b.sa.SchedulerObservation)
            kw['scheduler_observation'].assert_held()
            self.assertIs(kw['service_barrier'],barrier);self.assertIs(kw['maintenance'],maintenance)
            self.assertEqual(kw['inventory'].roots,tuple((n.replace('-','_'),self.http.spec.root/'data'/n)
                for n in ('sessions','tmp','upload-tmp','imports','log','uploads')))
            self.assertEqual((kw['inventory'].web_uid,kw['inventory'].web_gid),(991,991))
            events.append('backup');return 'receipt'
        with patch.object(b.hd.HttpDrain,'acquire',side_effect=acquire), \
             patch.object(self.http,'_inspect_configuration',return_value=(SimpleNamespace(pw_uid=991,pw_gid=991),None,None,None)), \
             patch.object(b.c.CoordinatedBackup,'create_and_verify',side_effect=compose):
            self.assertEqual(self.execute(),'receipt');self.assertEqual(events,['drain','backup','close'])

    def test_failure_still_closes_barrier_and_never_resumes(self):
        closed=[]
        @contextmanager
        def acquire(*a,**kw):
            try:yield SimpleNamespace(maintenance_lease=object())
            finally:closed.append(True)
        with patch.object(b.hd.HttpDrain,'acquire',side_effect=acquire), \
             patch.object(self.http,'_inspect_configuration',side_effect=b.h.HttpRuntimeError('HTTP_RUNTIME_DRIFT')):
            with self.assertRaises(b.h.HttpRuntimeError):self.execute()
        self.assertEqual(closed,[True])

    def test_public_repr_has_no_private_configuration(self):
        for text in ('hestia-managed','/var/lib','private-sql-fixture'):self.assertNotIn(text,repr(self.operation))

    def test_scheduler_rejection_precedes_gate_and_any_backup(self):
        with patch.object(b.sa,'_observe',side_effect=b.sa.SchedulerAdmissionError(b.sa.REJECTED)), \
             patch.object(b.hd.HttpDrain,'acquire') as drain, \
             patch.object(b.c.CoordinatedBackup,'create_and_verify') as backup:
            with self.assertRaisesRegex(b.sa.SchedulerAdmissionError,b.sa.REJECTED):self.execute()
            drain.assert_not_called();backup.assert_not_called()


class SqlFenceChannelTests(unittest.TestCase):
    def test_consumer_error_keeps_identity_and_sql_worker_is_reaped(self):
        from installer.provisioned_admission import AdmissionError
        error=AdmissionError('PROVISIONED_EXTERNAL_STORAGE_REJECTED')
        body='import sys,json\nv=json.loads(sys.stdin.readline());print(json.dumps({"request_id":v["request_id"],"sequence":0,"state":"LOCK_HELD"}),flush=True)\nfor line in sys.stdin:pass'
        database=dict(host='127.0.0.1',port=3306,name='fixture',tls_required=False,tls_ca_file=None,tls_ca_sha256=None)
        with tempfile.TemporaryDirectory() as root,patch.object(r,'_stage'), \
             patch.object(r.p,'_command',return_value=[sys.executable,'-c',body]):
            with self.assertRaises(AdmissionError) as caught:
                with r.acquire(SimpleNamespace(run_root=Path(root)),Path('/unused'),database,None,
                        SimpleNamespace(_user='fixture',_password='private-fixture')) as fence:
                    raise error
            self.assertIs(caught.exception,error)
            self.assertIsNotNone(fence._process.poll())
            self.assertTrue(fence._process.stdin.closed and fence._process.stdout.closed)
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_only_closed_profile_rejections_with_valid_binding_are_reported(self):
        for state in (*sorted(r.PROFILE_REJECTIONS),'private-server-detail'):
            fence=self.channel('import sys,json\nv=json.loads(sys.stdin.readline());print(json.dumps({"request_id":v["request_id"],"sequence":v["sequence"],"state":'+repr(state)+'}),flush=True)')
            with self.assertRaisesRegex(r.SqlReadFenceError,state if state in r.PROFILE_REJECTIONS else 'PROTOCOL'):
                fence.assert_held()
            fence.close()

    def test_large_shared_bridge_uses_source_bundle_and_keeps_secret_limit(self):
        shared=(Path(r.__file__).parent/'private/backup_bridge.php').read_bytes()
        self.assertGreater(len(shared),16384)
        def bundle(source,stage,gid,engine_files,engine_sha256,bridge):
            self.assertEqual(bridge,'backup_bridge.php')
            target=stage/'bridge.php';target.write_bytes(shared);target.chmod(0o640)
        with tempfile.TemporaryDirectory(dir='/var/lib') as tmp:
            stage=Path(tmp)/'stage';stage.mkdir(mode=0o700)
            trusted=Path(tmp)/'trusted';(trusted/'private').mkdir(parents=True,mode=0o750)
            for name in ('sql_read_fence.php','sql_accounts_policy.php'):
                path=trusted/'private'/name
                path.write_bytes((Path(r.__file__).parent/'private'/name).read_bytes());path.chmod(0o640)
            with r.fs._directory(stage) as fd:
                with self.assertRaisesRegex(r.fs.AccountConfigurationError,'SIZE_REJECTED'):
                    r.f._write(fd,'oversized-secret',shared,os.getgid())
            with patch.object(r.p,'_copy_bundle',side_effect=bundle),patch.object(r,'__file__',str(trusted/'sql_read_fence.py')):
                r._stage(SimpleNamespace(worker_gid=os.getgid()),Path('/unused'),stage,None)
            self.assertEqual((stage/'backup_bridge.php').read_bytes(),shared)
            self.assertEqual((stage/'bridge.php').read_bytes(),(Path(r.__file__).parent/'private/sql_read_fence.php').read_bytes())
            self.assertEqual({x.name for x in stage.iterdir()},{'bridge.php','backup_bridge.php','sql_accounts_policy.php'})
            for path in stage.iterdir():
                info=path.stat();self.assertEqual((info.st_uid,info.st_gid,info.st_mode&0o777),(0,os.getgid(),0o640))

    def channel(self,body):
        proc=subprocess.Popen([sys.executable,'-c',body],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,start_new_session=True,bufsize=0)
        os=__import__('os');os.set_blocking(proc.stdin.fileno(),False);os.set_blocking(proc.stdout.fileno(),False)
        fence=r.SqlReadFence(proc,'a'*32,None);self.addCleanup(fence.close);return fence

    def test_real_pipe_handshake_ping_release_and_cleanup(self):
        fence=self.channel('import sys,json\nfor line in sys.stdin:\n v=json.loads(line);op=v["operation"];print(json.dumps({"request_id":v["request_id"],"sequence":v.get("sequence",0),"state":"RELEASED" if op=="release" else "LOCK_HELD"}),flush=True)\n if op=="release":break')
        fence._round({'operation':'acquire','request_id':'a'*32},'LOCK_HELD');fence.assert_held();fence.release()
        self.assertEqual(fence._process.poll(),0)
        with self.assertRaisesRegex(r.SqlReadFenceError,'REQUIRED'):fence.assert_held()

    def test_wrong_id_bool_sequence_extra_fields_and_overflow_are_rejected(self):
        for reply in ('{"request_id":"b","sequence":1,"state":"LOCK_HELD"}',
            '{"request_id":"'+ 'a'*32 +'","sequence":true,"state":"LOCK_HELD"}',
            '{"request_id":"'+ 'a'*32 +'","sequence":1,"state":"LOCK_HELD","extra":1}',
            'x'*1025,'{}\n{}'):
            fence=self.channel('import sys;sys.stdin.readline();print('+repr(reply)+',flush=True)')
            with self.assertRaises(r.SqlReadFenceError):fence.assert_held()
            fence.close()

    def test_worker_loss_timeout_cancel_and_cross_process_lease_fail_closed(self):
        fence=self.channel('import sys;sys.stdin.readline()')
        with self.assertRaisesRegex(r.SqlReadFenceError,'LOST'):fence.assert_held()
        fence.close()
        fence=self.channel('import time;time.sleep(10)');fence._deadline=time.monotonic()-.1
        with self.assertRaisesRegex(r.SqlReadFenceError,'TIMEOUT'):fence.assert_held()
        fence._deadline=time.monotonic()+10;fence._cancel=threading.Event();fence._cancel.set()
        with self.assertRaisesRegex(r.SqlReadFenceError,'INTERRUPTED'):fence.assert_held()
        with patch.object(r.os,'getpid',return_value=-1),self.assertRaisesRegex(r.SqlReadFenceError,'REQUIRED'):fence.assert_held()

    def test_no_pickle_private_repr_and_bounded_requests(self):
        fence=self.channel('import time;time.sleep(10)')
        self.assertNotIn('a'*32,repr(fence))
        with self.assertRaises(TypeError):pickle.dumps(fence)
        with self.assertRaisesRegex(r.SqlReadFenceError,'PROTOCOL'):fence._round({'secret':'x'*16385},'LOCK_HELD')
        fence._sequence=63
        with self.assertRaisesRegex(r.SqlReadFenceError,'LIMIT'):fence.assert_held()
