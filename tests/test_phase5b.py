"""Closed Phase 5B contract/filesystem tests. Actual SQL/TLS is transverse E2E."""
import copy
import hashlib
import json
import os
import pickle
import stat
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from installer import phase5b as b
from installer import php_transport as p
from sql_accounts_fixture import ProtectedConfigurationFixture
from test_web_config import request


class Phase5BContractTests(unittest.TestCase):
    def test_authority_is_separate_and_not_serializable(self):
        a=b.SqlAuthority('root','only-a-synthetic-fixture-secret')
        self.assertNotIn(a._password,repr(a))
        with self.assertRaises(TypeError):pickle.dumps(a)
        for user,password in [('x;dsn','valid'),('root',''),('root','a\x00b'),('root','é'*600)]:
            with self.subTest(user=user),self.assertRaises(b.Phase5BError):b.SqlAuthority(user,password)

    def test_configuration_accepts_explicit_ports_and_remote_ca(self):
        r=request();r['database'].update(mode='existing_local',port=33306)
        self.assertEqual(b.configuration(r)['database']['port'],33306)
        r['database'].update(mode='remote',host='db.example.test',tls_ca_file='/etc/hestia/ca.pem')
        self.assertEqual(b.configuration(r)['database']['mode'],'remote')

    def test_upgrade_does_not_become_fresh(self):
        with self.assertRaisesRegex(b.Phase5BError,'UPGRADE_REQUIRES'):b.configuration(request('upgrade'))

    def test_closed_payload_numbers_and_dsn_injection(self):
        for key,value in [('port',True),('port',float('nan')),('port',0),('host','db;user=root'),('name','mysql')]:
            r=request();r['database'][key]=value
            with self.subTest(key=key),self.assertRaises(b.Phase5BError):b.configuration(r)
        r=request();r['extra']='secret'
        with self.assertRaises(b.Phase5BError):b.configuration(r)

    def test_paths_cannot_change_open_basedir_list(self):
        r=request();r['web']['webroot']='/srv/instance:/'
        with self.assertRaises(b.Phase5BError):b.configuration(r)

    def test_reject_duplicate_accounts_and_secrets(self):
        r=request();c=b.configuration(r)
        for m,a in [(p.ProvisioningCredentials(c['database']['user'],'setup-pass'),b.SqlAuthority('root','authority-pass')),
                    (p.ProvisioningCredentials('setup','setup-pass'),b.SqlAuthority('root','setup-pass'))]:
            with self.assertRaisesRegex(b.Phase5BError,'SEPARATION'):b._validate_private(c,r,m,a)

    def test_sql_response_is_closed_and_correlated(self):
        req={'request_id':'a'*32,'operation':'fresh'}
        good={'version':2,**req,'ok':True,'result':{'state':'DATABASE_CONFIGURED'},'error':None}
        self.assertEqual(b._sql_response(0,p._json(good),req),good['result'])
        for key,value in [('version',True),('ok',1),('operation','shell'),('request_id','b'*32),('result',{'state':'INSTALLED'}),('error','oops')]:
            bad={**good,key:value}
            with self.subTest(key=key),self.assertRaises(b.Phase5BError):b._sql_response(0,p._json(bad),req)
        for code in (1,-9,20):
            with self.assertRaises(b.Phase5BError):b._sql_response(code,p._json(good),req)

    def test_error_output_never_forwards_diagnostics(self):
        req={'request_id':'b'*32,'operation':'preflight'}
        good={'version':2,**req,'ok':False,'result':None,'error':'DATABASE_NOT_EMPTY'}
        with self.assertRaisesRegex(b.Phase5BError,'DATABASE_NOT_EMPTY'):b._sql_response(20,p._json(good),req)
        good['error']='SQLSTATE containing synthetic secret'
        with self.assertRaisesRegex(b.Phase5BError,'PROTOCOL_REJECTED'):b._sql_response(20,p._json(good),req)
        for raw in [b'',b'[]',b'{"version":2,"version":2}',b'null',b'\xff']:
            with self.assertRaises(b.Phase5BError):b._sql_response(0,raw,req)

    def test_loader_contains_no_secret_or_dynamic_php(self):
        raw=b._loader(Path('/etc/hestia/Épreuve & \'/instance.json'),55)
        self.assertNotIn("Épreuve".encode(),raw)
        self.assertIn(b'hestia_managed_load(hex2bin(',raw)
        self.assertNotIn(b'password',raw)


class Phase5BFilesystemTests(ProtectedConfigurationFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        # One root-owned synthetic source, never substituted in production.
        (self.webroot/'index.php').write_text('<?php // immutable test source')
        self.manifest={'index.php':{'sha256':hashlib.sha256((self.webroot/'index.php').read_bytes()).hexdigest(),'mode':'0644'}}
        self.authority=b.SqlAuthority('root','authority-private-fixture-only')
        self.mocks=[patch.object(b,'WEB_RUNTIME_SHA256',hashlib.sha256(p._json(self.manifest)).hexdigest()),patch.object(p,'_runtime'),
                    patch.object(b,'_sql',return_value={'state':'DATABASE_CONFIGURED'}),
                    patch.object(b,'_runtime_check',return_value={'state':'RUNTIME_VERIFIED','assistant_enabled':False,'key_configured':False,'api_access_tested':False})]
        self.patched=[m.start() for m in self.mocks]

    def tearDown(self):
        for m in reversed(self.mocks):m.stop()
        super().tearDown()

    def complete(self,payload=None,confirmed=True):
        return b.complete_fresh(self.runtime,self.payload if payload is None else payload,self.credentials,self.authority,
                               config_root=self.output,confirmed=confirmed)

    def test_root_and_public_configuration_roots_are_refused(self):
        for root in (Path('/'),Path('/srv'),Path('/var/www'),self.webroot,self.webroot/'secrets'):
            with self.subTest(root=str(root)),self.assertRaisesRegex(b.Phase5BError,'PUBLIC_PATH_REFUSED'):
                b.complete_fresh(self.runtime,self.payload,self.credentials,self.authority,config_root=root,confirmed=True)
        self.patched[2].assert_not_called()

    def test_complete_seals_after_verified_runtime_and_no_secret_in_result(self):
        result=self.complete()
        self.assertEqual(result['scope'],'WEB_CONFIGURED');self.assertFalse(result['application_installed']);self.assertFalse(result['http_verified'])
        self.assertTrue((self.webroot/'install.lock').is_file());self.assertTrue((self.webroot/'includes/db.php').is_file())
        for secret in (self.credentials._password,self.authority._password,self.payload['secrets']['admin_password']):
            self.assertNotIn(secret,json.dumps(result))
            for f in self.directory.iterdir():self.assertNotIn(secret,f.read_text())
        data=json.loads((self.directory/'instance.json').read_text())
        self.assertEqual(data['database']['password'],self.payload['secrets']['database_password'])
        self.assertEqual(data['assistant_key'],'')

    def test_confirmation_refuses_before_any_sql_or_file(self):
        for value in (False,1,'true',None):
            with self.subTest(value=value),self.assertRaises(b.Phase5BError):self.complete(confirmed=value)
        self.patched[2].assert_not_called();self.assertFalse(list(self.output.iterdir()))

    def test_occupied_config_and_symlink_are_not_executed(self):
        for relative in ('includes/db.php','install.lock'):
            path=self.webroot/relative;path.symlink_to('/does-not-exist')
            with self.assertRaises(b.Phase5BError):self.complete()
            path.unlink()
        self.patched[2].assert_not_called()

    def test_unpinned_or_extra_code_refused(self):
        (self.webroot/'index.php').write_text('<?php // changed')
        with self.assertRaises(b.Phase5BError):self.complete()
        self.patched[2].assert_not_called()

    def test_public_config_destination_refused(self):
        with self.assertRaisesRegex(b.Phase5BError,'PUBLIC_PATH'):
            b.complete_fresh(self.runtime,self.payload,self.credentials,self.authority,config_root=self.webroot/'conf',confirmed=True)

    def test_sql_partial_failure_leaves_durable_interlock_and_no_lock(self):
        def sql(*args,**kwargs):
            if args[6]=='fresh':raise b.Phase5BError('SQL_MANUAL_ACTION')
            return {'state':'TARGET_VERIFIED'}
        self.patched[2].side_effect=sql
        with self.assertRaisesRegex(b.Phase5BError,'MANUAL_ACTION'):self.complete()
        self.assertTrue(list(self.runtime.state_root.iterdir()));self.assertFalse((self.webroot/'install.lock').exists())
        self.assertFalse((self.webroot/'includes/db.php').exists())
        with self.assertRaises(b.Phase5BError):self.complete()

    def test_verification_failure_never_seals(self):
        self.patched[3].side_effect=b.Phase5BError('RUNTIME_VERIFICATION_FAILED')
        with self.assertRaisesRegex(b.Phase5BError,'MANUAL_ACTION'):self.complete()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_service_can_read_but_not_modify_credentials_other_identities_cannot(self):
        self.complete()
        for path in [self.directory,self.directory/'instance.json',self.webroot/'includes/db.php']:
            self.assertFalse(self.permission(self.web,'-w',path))
        self.assertTrue(self.permission(self.web,'-r',self.directory/'instance.json'))
        for user in (self.worker,self.other):self.assertFalse(self.permission(user,'-r',self.directory/'instance.json'))

    def test_managed_migration_release_precedes_seal(self):
        self.payload['database']['mode']='managed'
        order=[]
        def sql(*args,**kwargs):
            order.append(args[6]);self.assertFalse((self.webroot/'install.lock').exists());return {}
        self.patched[2].side_effect=sql
        result=self.complete();self.assertEqual(order,['preflight','fresh','release']);self.assertTrue(result['temporary_account_released'])

    def test_fs_failure_preserves_blocking_partial_state(self):
        with patch.object(fs_module(),'_write',side_effect=OSError('fixture')):
            with self.assertRaisesRegex(b.Phase5BError,'MANUAL_ACTION'):self.complete()
        self.assertFalse((self.webroot/'install.lock').exists());self.assertTrue(self.directory.exists())

    def test_cancellation_before_dispatch_has_no_effect(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaisesRegex(b.Phase5BError,'INTERRUPTED'):
            b.complete_fresh(self.runtime,self.payload,self.credentials,self.authority,config_root=self.output,confirmed=True,cancel=cancel)
        self.patched[2].assert_not_called();self.assertFalse(list(self.output.iterdir()))

    def test_upgrade_preserves_existing_content(self):
        r=copy.deepcopy(self.payload);r.update(mode='upgrade',administrator=None);r['secrets']['admin_password']='';r['assistant']['action']='preserve'
        with self.assertRaisesRegex(b.Phase5BError,'UPGRADE_REQUIRES'):self.complete(r)
        self.patched[2].assert_not_called()

    def test_assistant_empty_replace_preserves_key_and_observed_state(self):
        self.complete();r=copy.deepcopy(self.payload);r.update(mode='upgrade',administrator=None);r['secrets']['admin_password']='';r['assistant']['action']='configure'
        before=(self.directory/'instance.json').read_bytes()
        self.patched[3].return_value={'state':'RUNTIME_VERIFIED','assistant_enabled':True,'key_configured':True,'api_access_tested':False}
        result=b.update_assistant(self.runtime,r,config_root=self.output,confirmed=True)
        self.assertEqual(result['state'],'ASSISTANT_PRESERVED');self.assertTrue(result['assistant_enabled'])
        self.assertEqual(before,(self.directory/'instance.json').read_bytes())

    def test_assistant_mutation_never_executes_php_secret(self):
        self.complete();r=copy.deepcopy(self.payload);r.update(mode='upgrade',administrator=None);r['secrets']['admin_password']='';r['assistant']['action']='configure'
        r['secrets']['openai_api_key']='fixture-syntax-only-openai-key'
        self.patched[3].return_value={'state':'RUNTIME_VERIFIED','assistant_enabled':True,'key_configured':True,'api_access_tested':False}
        result=b.update_assistant(self.runtime,r,config_root=self.output,confirmed=True)
        self.assertEqual(result['state'],'ASSISTANT_CONFIGURED')
        self.assertEqual(json.loads((self.directory/'instance.json').read_text())['assistant_key'],r['secrets']['openai_api_key'])
        self.assertNotIn(r['secrets']['openai_api_key'],(self.webroot/'includes/db.php').read_text())

    def test_assistant_interruption_blocks_future_updates(self):
        self.complete();r=copy.deepcopy(self.payload);r.update(mode='upgrade',administrator=None);r['secrets']['admin_password']='';r['assistant']['action']='disabled'
        original=self.patched[3].return_value
        self.patched[3].side_effect=[original,b.Phase5BError('TIMEOUT')]
        with self.assertRaisesRegex(b.Phase5BError,'ASSISTANT_MANUAL'):b.update_assistant(self.runtime,r,config_root=self.output,confirmed=True)
        self.patched[3].side_effect=None
        with self.assertRaisesRegex(b.Phase5BError,'ASSISTANT_MANUAL'):b.update_assistant(self.runtime,r,config_root=self.output,confirmed=True)


def fs_module():
    return b.fs
