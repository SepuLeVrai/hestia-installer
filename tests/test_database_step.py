"""Closed 5B2.2 contracts and real protected filesystem/process identities.

SQL is simulated in these unit tests only. The independent opt-in integration
campaign executes the actual pinned Web engine and MariaDB/TLS connections.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import pickle
import threading
import unittest
from unittest.mock import patch

from installer import database_step as step
from installer import php_transport as p
from sql_accounts_fixture import ProtectedConfigurationFixture
from test_sql_accounts import local_request
from test_php_transport import fixture_source


def reply(request, *, error=None, uncertain=False):
    fresh = request['operation'] == 'prepare_database'
    result = {'scope': 'DATABASE_READY' if fresh else 'ACCOUNTS_VERIFIED',
              'tls_verified': request['target']['tls_required'], 'application_installed': False}
    if fresh:
        result.update(version=p.ENGINE_VERSION, schema_statements=100, application_verified=True,
                      migration_retained=request['mode'] != 'managed', assistant_enabled=False)
    return {'version': 1, 'operation': request['operation'], 'request_id': request['request_id'],
            'ok': error is None, 'error': error, 'uncertain': uncertain, 'result': None if error else result}


class DatabaseStepContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = local_request()
        self.migration = p.ProvisioningCredentials('setup_fixture', 'setup-sensitive-fixture')

    def config(self, payload=None, authority=None, fresh=True, confirmed=True):
        return step._configuration(self.payload if payload is None else payload, self.migration, authority,
                                   fresh=fresh, confirmed=confirmed)

    def request(self, fresh=True):
        config = self.config(fresh=fresh)
        return step._request(config, self.payload, self.migration, None, step._target(config, None, None), fresh)

    def test_explicit_target_mapping_without_assistant_or_paths(self):
        self.payload['assistant']['action'] = 'configure'
        self.payload['secrets']['openai_api_key'] = 'fixture-openai-' + 'Z' * 24
        request = self.request()
        self.assertEqual(set(request), {'version','operation','request_id','mode','target','application','migration','authority','administrator','confirmed'})
        for value in (self.payload['secrets']['openai_api_key'], self.payload['web']['webroot']):
            self.assertNotIn(value, p._json(request).decode())
        self.assertEqual(request['migration']['password'], self.migration._password)
        self.assertLessEqual(len(p._json(request)), p.MAX_INPUT)

    def test_no_mutation_of_caller_payload(self):
        before = copy.deepcopy(self.payload); self.config(); self.assertEqual(self.payload, before)

    def test_upgrade_is_only_a_read_only_audit(self):
        self.payload.update(mode='upgrade', administrator=None); self.payload['secrets']['admin_password'] = ''
        self.assertEqual(self.config(fresh=False)['mode'], 'upgrade')
        with self.assertRaisesRegex(step.DatabaseStepError, 'UPGRADE_PREPARE_REFUSED'): self.config()
        req = self.request(fresh=False); self.assertIsNone(req['administrator']); self.assertIsNone(req['authority'])
        self.assertFalse(req['confirmed'])

    def test_managed_requires_separate_authority(self):
        self.payload['database']['mode'] = 'managed'
        with self.assertRaisesRegex(step.DatabaseStepError, 'SQL_AUTHORITY_REQUIRED'): self.config()
        authority = step.SqlAuthorityCredentials('root', 'authority-private-fixture')
        self.assertEqual(self.config(authority=authority)['database']['mode'], 'managed')
        self.assertNotIn(authority._password, repr(authority))
        with self.assertRaises(TypeError): pickle.dumps(authority)

    def test_authority_not_accepted_for_existing_or_audit(self):
        authority = step.SqlAuthorityCredentials('root', 'authority-private-fixture')
        with self.assertRaisesRegex(step.DatabaseStepError, 'SQL_AUTHORITY_UNEXPECTED'): self.config(authority=authority)
        with self.assertRaises(step.DatabaseStepError): self.config(authority=authority, fresh=False)

    def test_separation_checks_both_user_and_password(self):
        for migration in (p.ProvisioningCredentials(self.payload['database']['user'], 'different-fixture-password'),
                          p.ProvisioningCredentials('another', self.payload['secrets']['database_password'])):
            with patch.object(self, 'migration', migration), self.assertRaisesRegex(step.DatabaseStepError, 'ACCOUNT_SEPARATION_REQUIRED'): self.config()

    def test_unknown_fields_types_ports_and_confirmation(self):
        for value in (False, None, 0, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(step.DatabaseStepError): self.config(confirmed=value)
        for port in (False, 0, -1, 65536, float('nan'), '3306'):
            v=copy.deepcopy(self.payload);v['database']['port']=port
            with self.subTest(port=port), self.assertRaises(step.DatabaseStepError):self.config(v)
        self.payload['arbitrary']='secret'
        with self.assertRaises(step.DatabaseStepError):self.config()

    def test_local_port_and_ipv6_are_explicit(self):
        for host in ('localhost','127.0.0.1','::1'):
            v=copy.deepcopy(self.payload);v['database'].update(host=host,port=3307)
            self.assertEqual(self.config(v)['database']['port'],3307)
        self.payload['database']['host']='127.0.0.2'
        with self.assertRaises(step.DatabaseStepError):self.config()

    def test_remote_requires_ca_and_preserves_hostname(self):
        self.payload['database'].update(mode='remote',host='SQL.Example.Test',port=3318,tls_ca_file='/etc/hestia/sql-ca.pem')
        self.assertEqual(self.config()['database']['host'],'sql.example.test')
        self.payload['database']['tls_ca_file']=None
        with self.assertRaises(step.DatabaseStepError):self.config()

    def test_response_is_closed_and_never_claims_installation(self):
        request=self.request();good=reply(request)
        self.assertEqual(step._response(0,p._json(good),request)['state'],'DATABASE_READY')
        for key,value in [('version',True),('operation','install'),('request_id','wrong'),('ok',1),('uncertain',True),('error','OK'),('extra','bad')]:
            bad=copy.deepcopy(good);bad[key]=value
            with self.subTest(key=key),self.assertRaises(step.DatabaseStepError):step._response(0,p._json(bad),request)
        for key,value in [('scope','INSTALLED'),('schema_statements',True),('schema_statements',0),('schema_statements',100001),
                          ('tls_verified',1),('application_installed',True),('migration_retained',False),('version','secret')]:
            bad=copy.deepcopy(good);bad['result'][key]=value
            with self.subTest(key=key),self.assertRaises(step.DatabaseStepError):step._response(0,p._json(bad),request)

    def test_malformed_duplicate_overflow_and_exit_mismatch(self):
        req=self.request()
        for raw in (b'',b'{}',b'[]',b'null',b'\xff',b'{"ok":true,"ok":false}',b'a'*4097):
            with self.subTest(raw=raw[:20]),self.assertRaises(step.DatabaseStepError):step._response(0,raw,req)
        for code in (20,1,-9,True):
            with self.subTest(code=code),self.assertRaises(step.DatabaseStepError):step._response(code,p._json(reply(req)),req)

    def test_errors_are_fixed_and_uncertain_audits_refused(self):
        req=self.request()
        for code in step.ERRORS:
            result=step._response(20,p._json(reply(req,error=code,uncertain=True)),req)
            self.assertEqual(result,{'state':'MANUAL_ACTION','code':code,'result':None})
        req=self.request(False)
        with self.assertRaises(step.DatabaseStepError):step._response(20,p._json(reply(req,error='REQUEST_INVALID',uncertain=True)),req)
        with self.assertRaises(step.DatabaseStepError):step._response(20,p._json(reply(req,error='raw-PDO-secret')),req)


class DatabaseStepFileTests(ProtectedConfigurationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.source=self.root/'source';self.source.mkdir()
        fixture_source(self.source)
        (self.source/'includes/installation/connection.php').write_text('<?php // explicit unit-test source fixture\n')
        digest=hashlib.sha256()
        for name in sorted(list(step.ENGINE_FILES)+['vendor/autoload.php']):
            b=(self.source/name).read_bytes();digest.update(p._json([name,len(b),hashlib.sha256(b).hexdigest()])+b'\n')
        self.pin=patch.object(step,'ENGINE_SHA256',digest.hexdigest());self.pin.start()
        self.client=step.DatabaseStep(self.runtime,self.source,repository=p.WEB_REPOSITORY,commit=step.WEB_COMMIT)
        self.calls=0
        def exchange(command,wire,stage,timeout,cancel=None):
            self.calls+=1
            self.assertNotIn(self.credentials._password,' '.join(command))
            request=json.loads(wire)
            return 0,p._json(reply(request))
        self.exchange=patch.object(p,'_exchange',side_effect=exchange);self.exchange.start()

    def tearDown(self):
        self.exchange.stop();self.pin.stop();super().tearDown()

    def prepare(self, payload=None, cancel=None):
        return self.client.prepare(self.payload if payload is None else payload,self.credentials,
                                   config_root=self.output,confirmed=True,cancel=cancel)

    def test_real_files_permissions_constants_and_secret_separation(self):
        r=self.prepare();self.assertEqual(r['state'],'DATABASE_CONFIGURATION_READY')
        self.assertFalse(r['result']['configuration_activated']);self.assertFalse(r['result']['application_installed'])
        self.assertEqual(self.load().returncode,0)
        self.assertEqual(self.load().stdout,b'MATCH')
        for file in self.directory.iterdir():
            self.assertNotIn(self.credentials._password.encode(),file.read_bytes())
            self.assertTrue(self.permission(self.web,'-r',file));self.assertFalse(self.permission(self.web,'-w',file))
            self.assertFalse(self.permission(self.worker,'-r',file));self.assertFalse(self.permission(self.other,'-r',file))
        self.assertFalse((self.webroot/'includes/db.php').exists());self.assertFalse((self.webroot/'install.lock').exists())

    def test_occupied_configuration_and_existing_loader_not_executed(self):
        marker=self.root/'should-not-exist'
        (self.webroot/'includes/db.php').write_text('<?php file_put_contents('+json.dumps(str(marker))+',"bad");')
        with self.assertRaises(step.DatabaseStepError):self.prepare()
        self.assertFalse(marker.exists());self.assertEqual(self.calls,0)

    def test_reapplication_never_overwrites_files(self):
        self.prepare();before={f.name:f.read_bytes() for f in self.directory.iterdir()}
        with self.assertRaises(step.DatabaseStepError):self.prepare()
        self.assertEqual(before,{f.name:f.read_bytes() for f in self.directory.iterdir()});self.assertEqual(self.calls,1)

    def test_source_drift_refused_before_dispatch_or_reservation(self):
        (self.source/'includes/installation/connection.php').write_text('<?php // altered\n')
        with self.assertRaises(step.DatabaseStepError):self.prepare()
        self.assertEqual(self.calls,0);self.assertFalse(self.directory.exists())

    def test_same_database_old_marker_is_shared(self):
        config=step._configuration(self.payload,self.credentials,None,fresh=True,confirmed=True)
        fd=step._marker(self.runtime,step._target(config,None,None),'1'*32);os.close(fd)
        result=self.prepare();self.assertEqual(result['state'],'MANUAL_ACTION');self.assertEqual(self.calls,0)

    def test_pre_cancel_has_no_side_effect(self):
        event=threading.Event();event.set()
        with self.assertRaises(step.DatabaseStepError):self.prepare(cancel=event)
        self.assertEqual(self.calls,0);self.assertFalse(self.directory.exists())

    def test_response_loss_keeps_interlock_and_no_config(self):
        with patch.object(p,'_exchange',side_effect=p.TransportError('TIMEOUT')):
            self.assertEqual(self.prepare()['state'],'MANUAL_ACTION')
        self.assertTrue(list((self.root/'attempts').iterdir()))
        self.assertFalse((self.directory/'database.json').exists())
        with self.assertRaises(step.DatabaseStepError):self.prepare()

    def test_partial_write_stays_private_and_no_activation(self):
        write=__import__('installer.database_config',fromlist=['_write'])._write
        def broken(fd,name,data,gid):
            write(fd,name,data,gid)
            raise OSError('synthetic fixture write failure')
        with patch('installer.database_config._write',side_effect=broken):
            self.assertEqual(self.prepare()['state'],'MANUAL_ACTION')
        self.assertEqual(self.directory.stat().st_mode & 0o777,0o700)
        self.assertFalse(self.permission(self.web,'-r',self.directory/'database.json'))
        self.assertFalse((self.webroot/'includes/db.php').exists())

    def test_unexpected_exception_never_echoes_secret(self):
        with patch.object(p,'_exchange',side_effect=RuntimeError(self.credentials._password)):
            result=self.prepare()
        self.assertNotIn(self.credentials._password,str(result));self.assertEqual(result['code'],'DATABASE_PREPARE_INCOMPLETE')

    def test_ca_input_control_and_public_directory_refused(self):
        self.payload['database'].update(mode='remote',host='db.example.test',tls_ca_file=str(self.root/'ca.pem'))
        (self.root/'ca.pem').write_text('not a certificate')
        with self.assertRaises(step.DatabaseStepError):self.prepare()
        self.assertFalse(self.directory.exists());self.assertEqual(self.calls,0)
        self.payload=local_request();self.payload['web'].update(webroot=str(self.webroot),service_user=self.web.pw_name)
        with self.assertRaisesRegex(step.DatabaseStepError,'CONFIGURATION_PUBLIC_PATH_REFUSED'):
            self.client.prepare(self.payload,self.credentials,config_root=self.webroot,confirmed=True)
