"""5C1 protocol/filesystem tests; SQL and source execution are fixture boundaries.

The separate opt-in integration recipe exercises unmocked MariaDB/TLS/Web.
"""
import copy
from dataclasses import FrozenInstanceError
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from installer import upgrade_preflight as u
from installer import finalization as f
from installer import database_step as db
from installer import database_config as fs
from installer import php_transport as p
from sql_accounts_fixture import ProtectedConfigurationFixture


def inventory():
    return {'release': p.ENGINE_VERSION, 'server_version': '11.8.6', 'schema_sha256': 'a'*64,
            'tables': 12, 'views': 0, 'non_innodb_tables': 0, 'columns': 70,
            'counts': {k: '1' if k in ('users','roles','active_admins') else '0' for k in u.COUNTS},
            'assistant_setting': False, 'read_only': True}


def probe():
    return {'database_verified': True, 'setting_enabled': False, 'key_configured': False,
            'assistant_enabled': False, 'api_access': 'NOT_TESTED'}


class UpgradeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.request = {'request_id': 'a'*32}
        self.response = {'version': 1, 'operation': 'inventory', 'request_id': 'a'*32,
                         'ok': True, 'result': inventory(), 'error': None}

    def decode(self, code=0, response=None):
        return u._response(code, p._json(self.response if response is None else response), self.request)

    def test_valid_closed_response(self):
        self.assertEqual(self.decode(), inventory())

    def test_boolean_version_integer_flags_refused(self):
        for field in ('version','ok'):
            with self.subTest(field=field):
                value=copy.deepcopy(self.response);value[field]=True if field=='version' else 1
                with self.assertRaises(u.UpgradePreflightError):self.decode(response=value)
        for field in ('read_only','assistant_setting'):
            value=copy.deepcopy(self.response);value['result'][field]=int(value['result'][field])
            with self.assertRaises(u.UpgradePreflightError):self.decode(response=value)

    def test_response_ids_keys_exit_code_and_errors_are_closed(self):
        cases=[{**self.response,'request_id':'b'*32},{**self.response,'operation':'apply'},
               {**self.response,'extra':'secret'},{**self.response,'error':'secret'},
               {**self.response,'result':{**inventory(),'extra':'secret'}}]
        for value in cases:
            with self.assertRaises(u.UpgradePreflightError):self.decode(response=value)
        with self.assertRaises(u.UpgradePreflightError):self.decode(20)
        value={**self.response,'ok':False,'result':None,'error':'UPGRADE_SCHEMA_CHANGED'}
        with self.assertRaisesRegex(u.UpgradePreflightError,'^UPGRADE_SCHEMA_CHANGED$'):self.decode(20,value)
        value['error']='sensitive PDO message'
        with self.assertRaisesRegex(u.UpgradePreflightError,'^PROTOCOL_REJECTED$'):self.decode(20,value)

    def test_malformed_duplicate_truncated_or_oversized_json_refused(self):
        for raw in (b'{',b'{}',b'{"version":1,"version":1}',b'x'*(p.MAX_OUTPUT+1),b'[]',b'null',b'\xff'):
            with self.assertRaises(u.UpgradePreflightError):u._response(0,raw,self.request)

    def test_inventory_numbers_and_schema_digest_bounded(self):
        for field, values in {'tables':[-1,True,513,'12',0], 'views':[513,-1,True],
                'columns':[16385,-1,True,0], 'non_innodb_tables':[13,True,-1],
                'schema_sha256':['z'*64,'a'*63,None], 'server_version':['11.8.6 SECRET',None,'1'*100],
                'release':['2.0.0','',True]}.items():
            for bad in values:
                value=copy.deepcopy(self.response);value['result'][field]=bad
                with self.subTest(field=field,bad=bad),self.assertRaises(u.UpgradePreflightError):self.decode(response=value)

    def test_bigint_counts_are_decimal_strings_not_floats(self):
        self.response['result']['counts']['web_sessions']='18446744073709551615'
        self.assertEqual(self.decode()['counts']['web_sessions'],'18446744073709551615')
        for bad in ('18446744073709551616','-1','01','1.0','1e5',1,1.0,True,None,'1'*100):
            value=copy.deepcopy(self.response);value['result']['counts']['users']=bad
            with self.assertRaises(u.UpgradePreflightError):self.decode(response=value)
        self.response['result']['counts']['active_admins']='0'
        with self.assertRaises(u.UpgradePreflightError):self.decode()

    def test_count_fields_are_exact(self):
        value=copy.deepcopy(self.response);value['result']['counts'].pop('roles')
        with self.assertRaises(u.UpgradePreflightError):self.decode(response=value)
        value['result']['counts']['password_hash']='0'
        with self.assertRaises(u.UpgradePreflightError):self.decode(response=value)

    def test_no_generic_target_pin_or_executor(self):
        for commit in ('main','f'*40,'',None):
            with self.assertRaises(u.UpgradePreflightError):u.UpgradePreflight(None,Path('/var/lib/source'),repository=p.WEB_REPOSITORY,commit=commit)
        with self.assertRaises(u.UpgradePreflightError):u.UpgradePreflight(None,Path('/var/lib/source'),repository='other/repo',commit=u.WEB_COMMIT)
        self.assertFalse(hasattr(u.UpgradePreflight,'apply'))


class UpgradeFilesystemTests(ProtectedConfigurationFixture, unittest.TestCase):
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
        m=patch.object(f.FinalizationStep,'_sources');m.start();self.addCleanup(m.stop)
        m=patch.object(f,'_probe',return_value=probe());m.start();self.addCleanup(m.stop)
        with patch.object(f,'_sql',return_value={'database_verified':True,'assistant_enabled':False}):
            result=f.FinalizationStep(self.runtime,self.webroot,repository=p.WEB_REPOSITORY,commit=f.WEB_COMMIT).finalize(
                self.payload,config_root=self.output,confirmed=True)
            self.assertEqual(result['state'],'WEB_FRESH_FINALIZED')
        self.request=copy.deepcopy(self.payload);self.request.update(mode='upgrade',administrator=None)
        self.request['assistant']['action']='preserve';self.request['secrets'].update(admin_password='',openai_api_key='')
        self.reader=u.UpgradePreflight(self.runtime,self.webroot,repository=p.WEB_REPOSITORY,commit=u.WEB_COMMIT)
        m=patch.object(u,'_inventory',return_value=inventory());self.sql=m.start();self.addCleanup(m.stop)

    def inspect(self,request=None,**kwargs):
        return self.reader.inspect(self.request if request is None else request,config_root=self.output,**kwargs)

    def snapshot(self):
        return {str(p):(p.read_bytes(),p.stat().st_mode,p.stat().st_uid,p.stat().st_gid,p.stat().st_mtime_ns)
                for root in (self.webroot,self.output,self.runtime.state_root) for p in root.rglob('*') if p.is_file()}

    def test_readonly_repeated_inspection_has_stable_immutable_report(self):
        before=self.snapshot();original=copy.deepcopy(self.request);first=self.inspect();second=self.inspect()
        self.assertEqual(first,second);self.assertEqual(before,self.snapshot());self.assertEqual(original,self.request)
        self.assertFalse((self.directory/'assistant-edit.lock').exists())
        report=first.report();report['plan']['apply_allowed']=True
        self.assertFalse(first.report()['plan']['apply_allowed'])
        with self.assertRaises(FrozenInstanceError):first._canonical=b'changed'
        self.assertEqual(first.sha256,hashlib.sha256(p._json(first.report()['plan'])).hexdigest())

    def test_report_never_claims_backup_or_upgrade_and_has_no_secrets(self):
        report=self.inspect().report();value=report['plan']
        for field in ('apply_allowed','backup_verified','rollback_verified','preservation_verified','application_installed'):
            self.assertIs(value[field],False)
        self.assertEqual(value['migration_catalog'],'NOT_DELIVERED')
        self.assertEqual(value['target_relation'],'IDENTICAL_RELEASE')
        for secret in self.payload['secrets'].values():
            if secret:self.assertNotIn(secret,json.dumps(report))

    def test_settings_fresh_and_extra_privileged_credentials_refused(self):
        before=self.snapshot()
        for action in ('configure','disabled'):
            value=copy.deepcopy(self.request);value['assistant']['action']=action
            with self.assertRaises(u.UpgradePreflightError):self.inspect(value)
        for field in ('authority','migration','confirmed'):
            value=copy.deepcopy(self.request);value[field]='private'
            with self.assertRaises(u.UpgradePreflightError):self.inspect(value)
        with self.assertRaises(u.UpgradePreflightError):self.inspect(self.payload)
        self.assertEqual(before,self.snapshot());self.sql.assert_not_called()

    def test_empty_or_atypical_inputs_refused_before_io(self):
        for value in (None,{},[],True,{'mode':'upgrade','assistant':{'action':'preserve'}}):
            with self.assertRaises(u.UpgradePreflightError):self.reader.inspect(value,config_root=self.output)
        for field,bad in (('port',True),('port',65536),('name',"database'; DROP TABLE x")):
            value=copy.deepcopy(self.request);value['database'][field]=bad
            with self.assertRaises(u.UpgradePreflightError):self.inspect(value)
        self.sql.assert_not_called()

    def test_admin_and_key_fields_cannot_mutate_existing_instance(self):
        for key in ('admin_password','openai_api_key'):
            value=copy.deepcopy(self.request);value['secrets'][key]='S'*30
            with self.assertRaises(u.UpgradePreflightError):self.inspect(value)
        self.sql.assert_not_called()

    def test_wrong_database_password_refused_before_probe(self):
        value=copy.deepcopy(self.request);value['secrets']['database_password']='wrong-fixture-password'
        with self.assertRaises(u.UpgradePreflightError):self.inspect(value)
        self.sql.assert_not_called()

    def test_missing_receipt_never_adopted_or_recreated(self):
        (self.directory/'finalized.json').unlink();before=self.snapshot()
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.assertEqual(before,self.snapshot());self.sql.assert_not_called()

    def test_altered_seal_refused(self):
        (self.directory/'seal.json').write_text('{}');before=self.snapshot()
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.assertEqual(before,self.snapshot());self.sql.assert_not_called()

    def test_symlink_hardlink_and_permissions_refused(self):
        path=self.directory/'assistant.json';raw=path.read_bytes()
        path.chmod(0o666)
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        path.chmod(0o660);other=self.directory/'other.json';os.link(path,other)
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        other.unlink();path.unlink();other.write_bytes(raw);os.chown(other,0,self.web.pw_gid);other.chmod(0o660);path.symlink_to(other)
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.sql.assert_not_called()

    def test_invalid_assistant_data_has_no_fallback(self):
        path=self.directory/'assistant.json'
        for raw in (b'{',p._json({'version':True,'openai_api_key':''}),p._json({'version':1,'openai_api_key':'bad'})):
            path.write_bytes(raw)
            with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.sql.assert_not_called()

    def test_existing_settings_lock_contention_refused_without_mutation(self):
        path=self.directory/'assistant-edit.lock';path.write_bytes(b'');path.chmod(0o600)
        before=self.snapshot()
        with path.open('rb') as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.assertEqual(before,self.snapshot());self.sql.assert_not_called()

    def test_web_key_writer_lock_contention_refused(self):
        with (self.directory/'assistant.json').open('rb') as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.sql.assert_not_called()

    def test_unresolved_assistant_attempt_refused(self):
        with fs._directory(self.directory) as fd:f._write(fd,'assistant-'+('c'*32)+'.attempt',p._json({'version':1,'request_id':'c'*32}),0,mode=0o600)
        before=self.snapshot()
        with self.assertRaises(u.UpgradePreflightError):self.inspect()
        self.assertEqual(before,self.snapshot());self.sql.assert_not_called()

    def test_changes_during_inventory_are_not_silently_accepted(self):
        def change(*args):
            (self.directory/'seal.json').write_text('{}');return inventory()
        self.sql.side_effect=change
        with self.assertRaises(u.UpgradePreflightError):self.inspect()

    def test_new_complete_settings_journal_invalidates_observation(self):
        def change(*args):
            with fs._directory(self.directory) as fd:
                for extension in ('.attempt','.done'):
                    f._write(fd,'assistant-'+('d'*32)+extension,p._json({'version':1,'request_id':'d'*32}),0,mode=0o600)
            return inventory()
        self.sql.side_effect=change
        with self.assertRaisesRegex(u.UpgradePreflightError,'UPGRADE_TARGET_CHANGED'):self.inspect()

    def test_cancel_before_or_after_inventory_creates_no_attempt(self):
        event=threading.Event();event.set();before=self.snapshot()
        with self.assertRaisesRegex(u.UpgradePreflightError,'INTERRUPTED'):self.inspect(cancel=event)
        self.sql.assert_not_called();event.clear()
        def stop(*args):event.set();return inventory()
        self.sql.side_effect=stop
        with self.assertRaisesRegex(u.UpgradePreflightError,'INTERRUPTED'):self.inspect(cancel=event)
        self.assertEqual(before,self.snapshot())

    def test_underlying_exception_never_discloses_diagnostics(self):
        self.sql.side_effect=RuntimeError(self.payload['secrets']['database_password'])
        with self.assertRaisesRegex(u.UpgradePreflightError,'^UPGRADE_INSPECTION_UNAVAILABLE$') as caught:self.inspect()
        self.assertTrue(caught.exception.__suppress_context__)
