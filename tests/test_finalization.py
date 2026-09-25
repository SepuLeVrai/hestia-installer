"""5B2.3 filesystem/protocol unit tests; SQL/HTTP live recipe is separate.

Mocks delimit only SQL/source/Web execution; all inode/permission/journal and
activation publication behavior in these tests is real on disposable paths.
"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import unittest
from unittest.mock import patch

from installer import finalization as f
from installer import database_step as step
from installer import database_config as fs
from installer import php_transport as p
from sql_accounts_fixture import ProtectedConfigurationFixture


def probe_result(enabled=False, key=False):
    return {'database_verified': True, 'setting_enabled': enabled, 'key_configured': key,
            'assistant_enabled': enabled and key, 'api_access': 'NOT_TESTED'}


class FinalizationTests(ProtectedConfigurationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.config = f._configuration(self.payload, fresh=True)
        self.directory.mkdir(mode=0o700)
        with fs._directory(self.directory) as fd:
            result = {'scope':'DATABASE_CONFIGURATION_READY', 'configuration_activated':False, 'application_installed':False,
                      'assistant_enabled':False, 'tls_verified':False,'application_verified':True,'migration_retained':True,'version':p.ENGINE_VERSION}
            step._configuration_files(fd,self.directory,self.web.pw_gid,self.config,self.payload,None,result)
        target=step._target(self.config,None,None)
        marker=step._marker(self.runtime,target,'a'*32)
        os.write(marker,p._json({'state':'DATABASE_CONFIGURATION_READY','code':'OK'})+b'\n');os.fsync(marker);os.close(marker)
        (self.webroot/'includes/installation').mkdir()
        (self.webroot/'includes/installation/activation.php').write_text('<?php // immutable unit fixture\n')
        self.enabled=False
        def sql(*args,desired,mutate,**kwargs):
            if mutate:self.enabled=desired
            return {'database_verified':True,'assistant_enabled':self.enabled}
        def probe(*args,action='preserve',key='',**kwargs):
            data=json.loads((self.directory/'assistant.json').read_text())
            if action=='configure': data['openai_api_key']=key;self.enabled=True
            elif action=='disabled':self.enabled=False
            if action!='preserve': (self.directory/'assistant.json').write_bytes(p._json(data))
            return probe_result(self.enabled,bool(data['openai_api_key']))
        for name,kw in (('_sql',{'side_effect':sql}),('_probe',{'side_effect':probe})):
            mock=patch.object(f,name,**kw);mock.start();self.addCleanup(mock.stop)
        mock=patch.object(f.FinalizationStep,'_sources');mock.start();self.addCleanup(mock.stop)
        self.client=f.FinalizationStep(self.runtime,self.webroot,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT)

    def finalize(self,**kwargs):
        return self.client.finalize(self.payload,config_root=self.output,confirmed=kwargs.pop('confirmed',True),**kwargs)

    def existing(self,action='preserve',key=''):
        v=copy.deepcopy(self.payload);v.update(mode='upgrade',administrator=None)
        v['secrets'].update(admin_password='',openai_api_key=key);v['assistant']['action']=action
        return v

    def configure(self,action='preserve',key=''):
        return self.client.configure_assistant(self.existing(action,key),config_root=self.output,confirmed=True)

    def test_prepared_boolean_fields_reject_integer_substitution(self):
        path=self.directory/'state.json';raw=path.read_bytes();original=json.loads(raw)
        for field in ('configuration_activated','application_installed','assistant_enabled','tls_verified','application_verified','migration_retained'):
            with self.subTest(field=field):
                value=copy.deepcopy(original);value[field]=int(value[field]);path.write_bytes(p._json(value))
                with self.assertRaisesRegex(f.FinalizationError,'DATABASE_NOT_PREPARED'):self.finalize()
                self.assertFalse((self.directory/'finalization.attempt').exists())
        path.write_bytes(raw)

    def test_sql_receipt_rejects_boolean_version(self):
        path=next(self.runtime.state_root.glob('fresh-*.attempt'))
        rows=path.read_bytes().splitlines();value=json.loads(rows[0]);value['version']=True
        path.write_bytes(p._json(value)+b'\n'+rows[1]+b'\n')
        with self.assertRaisesRegex(f.FinalizationError,'DATABASE_RECEIPT_REQUIRED'):self.finalize()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_final_receipt_rejects_boolean_version(self):
        self.finalize();path=self.directory/'finalized.json';value=json.loads(path.read_bytes());value['version']=True
        path.write_bytes(p._json(value))
        self.assertEqual(self.client.observe(self.existing(),config_root=self.output)['state'],'MANUAL_ACTION')
        with self.assertRaises(f.FinalizationError):self.configure('disabled')

    def test_existing_setting_lock_refuses_concurrent_update(self):
        self.finalize();path=self.directory/'assistant-edit.lock';path.write_bytes(b'');path.chmod(0o600)
        before=(self.directory/'assistant.json').read_bytes()
        with path.open('rb') as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(f.FinalizationError,'ASSISTANT_PREFLIGHT_FAILED'):self.configure('configure','L'*40)
        self.assertEqual(before,(self.directory/'assistant.json').read_bytes())
        self.assertFalse(list(self.directory.glob('assistant-*.attempt')))

    def test_no_key_finalizes_without_claiming_system_installation(self):
        value=self.finalize();self.assertEqual(value['state'],'WEB_FRESH_FINALIZED')
        self.assertFalse(value['result']['application_installed']);self.assertTrue(value['result']['system_qualification_required'])
        self.assertFalse(value['result']['assistant_enabled']);self.assertEqual(value['result']['api_access'],'NOT_TESTED')
        self.assertTrue((self.webroot/'install.lock').is_file())

    def test_configured_key_is_data_only_and_not_in_receipts(self):
        self.payload['assistant']['action']='configure';self.payload['secrets']['openai_api_key']='fixture-'+('A'*100)
        result=self.finalize();self.assertTrue(result['result']['assistant_enabled'])
        for name in ('finalization.attempt','finalized.json','seal.json','state.json','db.php'):
            self.assertNotIn(self.payload['secrets']['openai_api_key'],(self.directory/name).read_text())
        self.assertNotIn(self.payload['secrets']['openai_api_key'],json.dumps(result))

    def test_durable_secret_permissions_and_identity_separation(self):
        self.finalize()
        for name,mode in (('assistant.json',0o660),('database.json',0o640),('db.php',0o640),('seal.json',0o640),('finalization.attempt',0o600)):
            info=(self.directory/name).stat();self.assertEqual(stat.S_IMODE(info.st_mode),mode);self.assertEqual(info.st_uid,0)
        self.assertTrue(self.permission(self.web,'-w',self.directory/'assistant.json'))
        self.assertFalse(self.permission(self.web,'-w',self.directory/'db.php'))
        self.assertFalse(self.permission(self.other,'-r',self.directory/'assistant.json'))
        self.assertFalse(self.permission(self.worker,'-r',self.directory/'database.json'))

    def test_second_finalize_is_not_a_replay(self):
        self.finalize();before=(self.directory/'finalized.json').read_bytes()
        with self.assertRaises(f.FinalizationError):self.finalize()
        self.assertEqual(before,(self.directory/'finalized.json').read_bytes())

    def test_unconfirmed_cannot_mutate(self):
        with self.assertRaisesRegex(f.FinalizationError,'CONFIRMATION_REQUIRED'):self.finalize(confirmed=False)
        self.assertFalse((self.directory/'finalization.attempt').exists())

    def test_upgrade_cannot_finalize_or_recreate_admin(self):
        with self.assertRaisesRegex(f.FinalizationError,'FINALIZATION_MODE_REFUSED'):
            self.client.finalize(self.existing(),config_root=self.output,confirmed=True)
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_preexisting_database_pointer_is_not_overwritten(self):
        target=self.webroot/'includes/db.php';target.write_text('KEEP')
        with self.assertRaises(f.FinalizationError):self.finalize()
        self.assertEqual(target.read_text(),'KEEP')

    def test_preexisting_lock_is_not_overwritten(self):
        target=self.webroot/'install.lock';target.write_text('KEEP')
        with self.assertRaises(f.FinalizationError):self.finalize()
        self.assertEqual(target.read_text(),'KEEP')

    def test_preexisting_assistant_file_is_not_evaluated(self):
        (self.directory/'assistant.json').write_text('<?php throw new Exception();')
        with self.assertRaises(f.FinalizationError):self.finalize()
        self.assertFalse((self.directory/'finalization.attempt').exists())

    def test_missing_sql_receipt_refuses_activation(self):
        for file in self.runtime.state_root.iterdir():file.unlink()
        with self.assertRaises(f.FinalizationError):self.finalize()
        self.assertFalse((self.directory/'finalization.attempt').exists())

    def test_stage_only_receipt_is_not_database_ready(self):
        (self.directory/'state.json').write_bytes(p._json(fs.STAGED))
        with self.assertRaisesRegex(f.FinalizationError,'DATABASE_NOT_PREPARED'):self.finalize()

    def test_database_target_or_password_mismatch_refused(self):
        self.payload['secrets']['database_password']='another-password-fixture'
        with self.assertRaisesRegex(f.FinalizationError,'CONFIGURATION_TARGET_MISMATCH'):self.finalize()

    def test_tampered_loader_refused(self):
        (self.directory/'db.php').write_text('<?php // wrong\n')
        with self.assertRaisesRegex(f.FinalizationError,'CONFIGURATION_LOADER_MISMATCH'):self.finalize()

    def test_symlink_and_hardlink_secret_refused(self):
        target=self.directory/'database.json';data=target.read_bytes();target.unlink()
        other=self.root/'secret';other.write_bytes(data);other.chmod(0o640);os.chown(other,0,self.web.pw_gid)
        target.symlink_to(other)
        with self.assertRaises(f.FinalizationError):self.finalize()
        target.unlink();os.link(other,target)
        with self.assertRaisesRegex(f.FinalizationError,'CONFIGURATION_INTEGRITY_FAILED'):self.finalize()

    def test_wrong_permissions_refuse_without_repairing_caller_files(self):
        target=self.directory/'database.json';target.chmod(0o666)
        with self.assertRaisesRegex(f.FinalizationError,'CONFIGURATION_INTEGRITY_FAILED'):self.finalize()
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o666)

    def test_cancelled_before_dispatch_does_not_publish(self):
        event=threading.Event();event.set()
        with self.assertRaisesRegex(f.FinalizationError,'INTERRUPTED'):self.finalize(cancel=event)
        self.assertFalse((self.directory/'finalization.attempt').exists())

    def test_database_check_failure_creates_no_seal(self):
        with patch.object(f,'_sql',side_effect=f.FinalizationError('FINALIZATION_DATABASE_INVALID')):
            with self.assertRaises(f.FinalizationError):self.finalize()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_failure_during_setting_keeps_durable_interlock(self):
        with patch.object(f,'_sql',side_effect=[{'database_verified':True,'assistant_enabled':False},f.FinalizationError('FINALIZATION_SETTING_FAILED')]):
            self.assertEqual(self.finalize()['state'],'MANUAL_ACTION')
        self.assertTrue((self.directory/'finalization.attempt').exists());self.assertFalse((self.directory/'seal.json').exists())
        with self.assertRaises(f.FinalizationError):self.finalize()

    def test_disk_failure_before_seal_never_claims_success(self):
        original=f._write
        def fail(fd,name,*args,**kwargs):
            if name=='install.lock':raise OSError('synthetic disk failure')
            return original(fd,name,*args,**kwargs)
        with patch.object(f,'_write',side_effect=fail):self.assertEqual(self.finalize()['state'],'MANUAL_ACTION')
        self.assertTrue((self.webroot/'includes/db.php').exists());self.assertFalse((self.directory/'seal.json').exists())

    def test_final_probe_failure_revokes_only_seal(self):
        with patch.object(f,'_probe',side_effect=[probe_result(),f.FinalizationError('FINALIZATION_RUNTIME_FAILED')]):
            self.assertEqual(self.finalize()['state'],'MANUAL_ACTION')
        self.assertFalse((self.directory/'seal.json').exists());self.assertTrue((self.webroot/'install.lock').exists())
        self.assertTrue((self.directory/'database.json').exists())

    def test_missing_final_receipt_is_manual_even_with_a_lock(self):
        self.finalize();(self.directory/'finalized.json').unlink()
        self.assertEqual(self.client.observe(self.existing(),config_root=self.output)['state'],'MANUAL_ACTION')

    def test_tampered_lock_is_not_a_successful_observation(self):
        self.finalize();(self.webroot/'install.lock').write_text('wrong')
        self.assertEqual(self.client.observe(self.existing(),config_root=self.output)['state'],'MANUAL_ACTION')

    def test_successful_observation_does_not_write(self):
        self.finalize();before={p.name:p.read_bytes() for p in self.directory.iterdir()}
        self.assertEqual(self.client.observe(self.existing(),config_root=self.output)['state'],'WEB_FRESH_FINALIZED')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.directory.iterdir()})

    def test_keep_empty_replace_disable_and_keep_disabled(self):
        self.finalize();key='fixture-'+('B'*50)
        self.assertTrue(self.configure('configure',key)['result']['assistant_enabled'])
        for action in ('preserve','configure'):
            self.assertTrue(self.configure(action,'')['result']['assistant_enabled'])
        self.assertFalse(self.configure('disabled')['result']['assistant_enabled'])
        self.assertEqual(json.loads((self.directory/'assistant.json').read_text())['openai_api_key'],key)
        self.assertFalse(self.configure()['result']['assistant_enabled'])

    def test_pending_edit_blocks_another_write_and_observation(self):
        self.finalize();rid='b'*32
        with fs._directory(self.directory) as fd:f._write(fd,'assistant-'+rid+'.attempt',p._json({'version':1,'request_id':rid}),0,mode=0o600)
        with self.assertRaises(f.FinalizationError):self.configure('configure','fixture-'+('C'*30))
        self.assertEqual(self.client.observe(self.existing(),config_root=self.output)['state'],'MANUAL_ACTION')

    def test_failed_settings_response_keeps_pending_evidence(self):
        self.finalize()
        with patch.object(f,'_probe',side_effect=[probe_result(),f.FinalizationError('FINALIZATION_RUNTIME_FAILED')]):
            self.assertEqual(self.configure('configure','fixture-'+('D'*30))['state'],'MANUAL_ACTION')
        self.assertTrue(list(self.directory.glob('assistant-*.attempt')))
        self.assertFalse(list(self.directory.glob('assistant-*.done')))

    def test_legacy_instance_is_not_adopted(self):
        with self.assertRaises(f.FinalizationError):self.configure('disabled')
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_source_pin_rejects_other_repositories_or_commits(self):
        for repository,commit in (('other/repository',f.WEB_COMMIT),(p.WEB_REPOSITORY,'f'*40),(p.WEB_REPOSITORY,'main')):
            with self.assertRaisesRegex(f.FinalizationError,'SOURCE_PIN_MISMATCH'):
                f.FinalizationStep(self.runtime,self.webroot,repository=repository,commit=commit)


class FinalizationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.request={'operation':'verify','request_id':'a'*32}
        self.value={'version':1,**self.request,'ok':True,'result':{'database_verified':True,'assistant_enabled':False},'error':None}

    def test_valid_closed_response(self):
        self.assertEqual(f._response(0,p._json(self.value),self.request,probe=False),self.value['result'])

    def test_wrong_ids_flags_scope_and_extra_secrets_are_refused(self):
        for path,value in ((('version',),True),(('request_id',),'b'*32),(('ok',),1),
                           (('result','database_verified'),1),(('result','password'),'not-for-output')):
            data=copy.deepcopy(self.value);parent=data
            for k in path[:-1]:parent=parent[k]
            parent[path[-1]]=value
            with self.assertRaisesRegex(f.FinalizationError,'PROTOCOL_REJECTED'):f._response(0,p._json(data),self.request,probe=False)

    def test_malformed_and_oversized_responses(self):
        for raw in (b'{}',b'[]',b'not json',b'x'*(p.MAX_OUTPUT+1)):
            with self.assertRaises(f.FinalizationError):f._response(0,raw,self.request,probe=False)

    def test_runtime_flags_must_be_consistent(self):
        data={**self.value,'result':probe_result(False,True)}
        self.assertEqual(f._response(0,p._json(data),self.request,probe=True),data['result'])
        data['result']['assistant_enabled']=True
        with self.assertRaises(f.FinalizationError):f._response(0,p._json(data),self.request,probe=True)

    def test_unknown_error_never_leaks_remote_text(self):
        data={**self.value,'ok':False,'result':None,'error':'raw-secret-fixture'}
        with self.assertRaisesRegex(f.FinalizationError,'PROTOCOL_REJECTED') as exc:f._response(20,p._json(data),self.request,probe=False)
        self.assertNotIn('raw-secret',str(exc.exception))
