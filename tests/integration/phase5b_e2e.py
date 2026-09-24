#!/usr/bin/env python3
"""Mandatory transverse 5B acceptance on disposable MariaDB and exact Web sources.

This script starts ONLY its own servers/datadirs. No existing database endpoint
is used. No skip or expected-failure counts as success. Requires a root sandbox.
"""
import argparse
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/'tests')]
from installer import phase5b as b
from installer import php_transport as p
from sql_accounts_fixture import ProtectedConfigurationFixture
from test_sql_accounts_mariadb import CONTROLLER, literal

WEB = None


def openssl(*args):
    subprocess.run(['openssl',*map(str,args)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=20)


class Phase5BRealTests(ProtectedConfigurationFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_PHASE5B_DB_TEST')!='1':raise RuntimeError('Explicit disposable-host opt-in required')
        for tool in ('php','mariadb-install-db','mariadbd','openssl'):
            if not shutil.which(tool):raise RuntimeError('Missing '+tool)
        super().setUpClass()
        cls.environment=tempfile.TemporaryDirectory(prefix='hestia-b5-sql-',dir='/var/lib')
        cls.envroot=Path(cls.environment.name);cls.envroot.chmod(0o755)
        cls.servers=[];cls.hosts_before=Path('/etc/hosts').read_bytes()
        try:
            with open('/etc/hosts','ab') as out:out.write(b'\n127.0.0.1 db.phase5b.test wrong.phase5b.test\n')
            cls.ca=cls.envroot/'ca.pem';cls.ca_key=cls.envroot/'ca.key'
            openssl('req','-x509','-newkey','rsa:2048','-nodes','-keyout',cls.ca_key,'-out',cls.ca,'-subj','/CN=HESTIA Test CA','-days','1')
            cls.other_ca=cls.envroot/'other-ca.pem'
            openssl('req','-x509','-newkey','rsa:2048','-nodes','-keyout',cls.envroot/'other.key','-out',cls.other_ca,'-subj','/CN=Other CA','-days','1')
            for kind in ('valid','expired','clear','ipv6'):
                data=cls.envroot/kind;data.mkdir(mode=0o755)
                address = '::1' if kind=='ipv6' else '127.0.0.1'
                with socket.socket(socket.AF_INET6 if kind=='ipv6' else socket.AF_INET) as probe:
                    probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    probe.bind((address,3306 if kind=='valid' else 0));probe.listen(1);port=probe.getsockname()[1]
                if kind not in ('clear','ipv6'):
                    openssl('req','-new','-newkey','rsa:2048','-nodes','-keyout',data/'key.pem','-out',data/'req.pem','-subj','/CN=db.phase5b.test')
                    (data/'extensions').write_text('subjectAltName=DNS:db.phase5b.test\nextendedKeyUsage=serverAuth\n')
                    if kind == 'expired':
                        (data/'index').write_text('');(data/'serial').write_text('01\n')
                        (data/'ca.conf').write_text('[ca]\ndefault_ca=local\n[local]\ndatabase='+str(data/'index')+
                            '\nserial='+str(data/'serial')+'\nnew_certs_dir='+str(data)+'\ncertificate='+str(cls.ca)+
                            '\nprivate_key='+str(cls.ca_key)+'\ndefault_md=sha256\npolicy=names\nx509_extensions=server\n'+
                            '[names]\ncommonName=supplied\n[server]\nsubjectAltName=DNS:db.phase5b.test\nextendedKeyUsage=serverAuth\n')
                        openssl('ca','-batch','-notext','-config',data/'ca.conf','-in',data/'req.pem','-out',data/'cert.pem',
                                '-startdate','20000101000000Z','-enddate','20010101000000Z')
                    else:
                        openssl('x509','-req','-in',data/'req.pem','-CA',cls.ca,'-CAkey',cls.ca_key,'-CAcreateserial',
                                '-out',data/'cert.pem','-days','1','-extfile',data/'extensions')
                subprocess.run(['mariadb-install-db','--no-defaults','--auth-root-authentication-method=normal','--skip-test-db',
                                '--skip-name-resolve','--datadir='+str(data/'data')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
                cmd=['mariadbd','--no-defaults','--user=root','--datadir='+str(data/'data'),'--socket='+str(data/'server.sock'),
                    '--pid-file='+str(data/'server.pid'),'--bind-address='+address,'--port='+str(port),'--skip-name-resolve','--skip-log-bin',
                    '--innodb-buffer-pool-size=32M','--max-connections=24','--log-error='+str(data/'server.log')]
                cmd += ['--skip-ssl'] if kind in ('clear','ipv6') else ['--ssl-ca='+str(cls.ca),'--ssl-cert='+str(data/'cert.pem'),'--ssl-key='+str(data/'key.pem')]
                process=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
                record={'kind':kind,'process':process,'socket':str(data/'server.sock'),'port':port}
                cls.servers.append(record)
                for _ in range(150):
                    if process.poll() is not None:raise RuntimeError('FIXTURE_SERVER_DIED')
                    if Path(record['socket']).exists():
                        try:cls.control(record,query='SELECT 1');break
                        except RuntimeError:pass
                    time.sleep(.1)
                else:raise RuntimeError('FIXTURE_SERVER_UNAVAILABLE')
                cls.control(record,[f"CREATE USER 'b5authority'@'{address}' IDENTIFIED BY 'fixture-authority-314159'",
                    f"GRANT ALL PRIVILEGES ON *.* TO 'b5authority'@'{address}' WITH GRANT OPTION"])
            cls.primary=cls.servers[0]
            print('Real 5B PHP:',subprocess.check_output(['php','-r','echo PHP_VERSION;'],text=True))
            print('Real 5B MariaDB:',cls.control(cls.primary,query='SELECT VERSION() AS version')[0]['version'])
        except BaseException:
            cls.cleanup_servers();super().tearDownClass();raise

    @classmethod
    def cleanup_servers(cls):
        for entry in cls.servers:
            process=entry['process'];process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        Path('/etc/hosts').write_bytes(cls.hosts_before)
        cls.environment.cleanup()

    @classmethod
    def tearDownClass(cls):
        try:cls.cleanup_servers()
        finally:super().tearDownClass()

    @staticmethod
    def control(server,statements=(),query=None):
        value={'socket':server['socket'],'statements':list(statements)}
        if query is not None:value['query']=query
        run=subprocess.run(['php','-d','display_errors=0','-d','log_errors=0','-r',CONTROLLER],
            input=json.dumps(value).encode(),capture_output=True,timeout=10)
        if run.returncode:raise RuntimeError(run.stderr.decode() if run.stderr.startswith(b'FIXTURE_SQL_FAILED_') else 'FIXTURE_CONTROL_FAILED')
        return json.loads(run.stdout)

    def setUp(self):
        super().setUp()
        for entry in WEB.iterdir():
            if entry.name in b._EXCLUDED:continue
            if entry.is_dir():shutil.copytree(entry,self.webroot/entry.name,dirs_exist_ok=True)
            else:shutil.copy2(entry,self.webroot/entry.name)
        for current,dirs,files in os.walk(self.webroot):
            os.chmod(current,0o755)
        suffix=os.urandom(5).hex()
        self.db='b5_'+suffix;self.app='b5a'+suffix;self.migration='b5m'+suffix
        self.payload['database'].update(mode='managed',host='127.0.0.1',port=3306,name=self.db,user=self.app)
        self.payload['administrator'].update(first_name='Élise <&>🙂',last_name="D'Angelo",email='test.admin@example.test')
        self.payload['secrets']['admin_password']="  AdmiN &\"é🙂 password  "
        self.payload['secrets']['database_password']="  Application '&\"é🙂 password  "
        self.credentials=p.ProvisioningCredentials(self.migration,'fixture-migration-271828')
        self.authority=b.SqlAuthority('b5authority','fixture-authority-314159')
        self.schema='`'+self.db.replace('_','\\_')+'`.*'
        self.app_account=literal(self.app)+"@'127.0.0.1'";self.migration_account=literal(self.migration)+"@'127.0.0.1'"
        self.directory=self.output/b.fs.configuration_slot(b.configuration(self.payload))

    def tearDown(self):
        try:
            for server in self.servers:
                self.control(server,[f'DROP DATABASE IF EXISTS `{self.db}`',f'DROP USER IF EXISTS {self.app_account}',
                    f'DROP USER IF EXISTS {self.migration_account}',f"DROP USER IF EXISTS '{self.app}'@'%'"])
        finally:super().tearDown()

    def existing(self,*,remote=False,server=None):
        server=server or self.primary
        self.payload['database'].update(mode='remote' if remote else 'existing_local',
            host='db.phase5b.test' if remote else '127.0.0.1',port=server['port'],tls_ca_file=str(self.ca) if remote else None)
        self.control(server,[f'CREATE DATABASE `{self.db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci',
            f'CREATE USER {self.app_account} IDENTIFIED BY {literal(self.payload["secrets"]["database_password"])}'+(' REQUIRE SSL' if remote else ''),
            f'CREATE USER {self.migration_account} IDENTIFIED BY {literal(self.credentials._password)}'+(' REQUIRE SSL' if remote else ''),
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON {self.schema} TO {self.app_account}',
            f'GRANT ALL PRIVILEGES ON {self.schema} TO {self.migration_account}'])

    def complete(self):
        return b.complete_fresh(self.runtime,self.payload,self.credentials,self.authority,config_root=self.output,confirmed=True)

    def query(self,sql):return self.control(self.primary,query=sql)

    def upgrade_payload(self,action,key=''):
        r=copy.deepcopy(self.payload);r.update(mode='upgrade',administrator=None);r['database']['mode']='existing_local'
        r['secrets']['admin_password']='';r['assistant']['action']=action;r['secrets']['openai_api_key']=key
        return r

    def test_managed_real_fresh_scoped_accounts_config_and_seal(self):
        result=self.complete()
        self.assertEqual(result['scope'],'WEB_CONFIGURED');self.assertFalse(result['application_installed']);self.assertTrue(result['temporary_account_released'])
        self.assertEqual(self.query(f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'],1)
        self.assertEqual(self.query(f"SELECT COUNT(*) AS n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],0)
        self.assertTrue((self.webroot/'install.lock').exists())
        for secret in (self.credentials._password,self.authority._password,self.payload['secrets']['admin_password']):
            for path in self.directory.iterdir():self.assertNotIn(secret.encode(),path.read_bytes())
        self.assertTrue(self.permission(self.web,'-r',self.directory/'instance.json'));self.assertFalse(self.permission(self.web,'-w',self.directory/'instance.json'))
        self.assertFalse(self.permission(self.worker,'-r',self.directory/'instance.json'))

    def test_existing_local_composes_engine_without_replacing_accounts(self):
        self.existing()
        before=self.query(f"SELECT authentication_string FROM mysql.user WHERE User='{self.migration}'")
        self.assertFalse(self.complete()['temporary_account_released'])
        self.assertEqual(before,self.query(f"SELECT authentication_string FROM mysql.user WHERE User='{self.migration}'"))

    def test_assistant_configure_preserve_empty_replace_disable(self):
        self.payload['assistant']['action']='configure';self.payload['secrets']['openai_api_key']='fixture-initial-openai-key-123456'
        self.assertTrue(self.complete()['assistant_enabled'])
        def change(action,key=''):
            return b.update_assistant(self.runtime,self.upgrade_payload(action,key),config_root=self.output,confirmed=True)
        before=(self.directory/'instance.json').read_bytes()
        self.assertTrue(change('preserve')['assistant_enabled']);self.assertEqual(before,(self.directory/'instance.json').read_bytes())
        self.assertEqual(change('configure')['state'],'ASSISTANT_PRESERVED');self.assertEqual(before,(self.directory/'instance.json').read_bytes())
        self.assertTrue(change('configure','fixture-replacement-openai-key-654321')['key_configured'])
        result=change('disabled');self.assertFalse(result['assistant_enabled']);self.assertFalse(result['key_configured']);self.assertFalse(result['api_access_tested'])

    def test_remote_tls_real_fresh_with_certificate_verification(self):
        self.existing(remote=True)
        result=self.complete();self.assertEqual(result['scope'],'WEB_CONFIGURED')
        data=json.loads((self.directory/'instance.json').read_text())
        self.assertEqual(Path(data['database']['tls_ca_file']).read_bytes(),self.ca.read_bytes())
        self.assertFalse(result['http_verified'])

    def test_wrong_ca_and_wrong_certificate_name_refused_before_mutation(self):
        self.existing(remote=True)
        for host,ca in [('db.phase5b.test',self.other_ca),('wrong.phase5b.test',self.ca)]:
            self.payload['database'].update(host=host,tls_ca_file=str(ca))
            with self.subTest(host=host),self.assertRaisesRegex(b.Phase5BError,'DATABASE_CONNECTION_FAILED'):self.complete()
            self.assertFalse(self.directory.exists());self.assertFalse((self.webroot/'install.lock').exists())

    def test_expired_certificate_refused_before_mutation(self):
        self.existing(remote=True,server=self.servers[1])
        with self.assertRaisesRegex(b.Phase5BError,'DATABASE_CONNECTION_FAILED'):self.complete()
        self.assertFalse(self.directory.exists())

    def test_plain_server_refused_without_authentication_downgrade(self):
        server=self.servers[2]
        # Replay the real non-TLS server greeting, capture any authentication packet.
        with socket.create_connection(('127.0.0.1',server['port']),timeout=5) as source:
            greeting=source.recv(4096)
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(10)
        port=listener.getsockname()[1];captured=[]
        def fake():
            try:
                conn,_=listener.accept()
                with conn:
                    conn.settimeout(5);conn.sendall(greeting)
                    try:captured.append(conn.recv(4096))
                    except ConnectionResetError:captured.append(b'')
            finally:listener.close()
        thread=threading.Thread(target=fake);thread.start()
        self.payload['database'].update(mode='remote',host='db.phase5b.test',port=port,tls_ca_file=str(self.ca))
        try:
            with self.assertRaisesRegex(b.Phase5BError,'DATABASE_CONNECTION_FAILED'):self.complete()
        finally:thread.join(12)
        self.assertEqual(captured,[b''],'A remote authentication packet must never be sent without TLS')
        self.assertFalse(self.directory.exists())

    def test_managed_refuses_existing_database_and_foreign_account(self):
        self.control(self.primary,[f'CREATE DATABASE `{self.db}`'])
        with self.assertRaisesRegex(b.Phase5BError,'DATABASE_TARGET_OCCUPIED'):self.complete()
        self.control(self.primary,[f'DROP DATABASE `{self.db}`',f"CREATE USER '{self.app}'@'%' IDENTIFIED BY 'other-account-fixture-password'"])
        with self.assertRaisesRegex(b.Phase5BError,'ACCOUNT_TARGET_OCCUPIED'):self.complete()
        self.assertFalse(self.directory.exists())

    def test_shadow_host_global_public_and_excess_privileges_refused(self):
        self.existing()
        cases=[(f"CREATE USER '{self.app}'@'%' IDENTIFIED BY 'fixture-hidden-host-password'",f"DROP USER '{self.app}'@'%'"),
               (f'GRANT SELECT ON {self.schema} TO PUBLIC',f'REVOKE SELECT ON {self.schema} FROM PUBLIC'),
               (f'GRANT ALTER ON {self.schema} TO {self.app_account}',f'REVOKE ALTER ON {self.schema} FROM {self.app_account}')]
        for bad,undo in cases:
            self.control(self.primary,[bad])
            try:
                with self.assertRaisesRegex(b.Phase5BError,'ACCOUNT_POLICY_REJECTED'):self.complete()
                self.assertFalse(self.directory.exists())
            finally:self.control(self.primary,[undo])

    def test_long_existing_data_and_upgrade_never_reset(self):
        self.existing();text="  D'Angelo <&>🙂漢字"+'x'*12000
        self.control(self.primary,[f'CREATE TABLE `{self.db}`.Sentinel (id INT,value LONGTEXT)',f'INSERT INTO `{self.db}`.Sentinel VALUES(-2147483648,{literal(text)})'])
        with self.assertRaisesRegex(b.Phase5BError,'DATABASE_NOT_EMPTY'):self.complete()
        self.assertEqual(self.query(f'SELECT value FROM `{self.db}`.Sentinel')[0]['value'],text)
        r=self.upgrade_payload('preserve')
        with self.assertRaisesRegex(b.Phase5BError,'UPGRADE_REQUIRES'):
            b.complete_fresh(self.runtime,r,self.credentials,self.authority,config_root=self.output,confirmed=True)
        self.assertEqual(self.query(f'SELECT value FROM `{self.db}`.Sentinel')[0]['value'],text)

    def test_lost_response_after_real_ddl_never_replays(self):
        original=b._sql
        def lost(*args,**kwargs):
            result=original(*args,**kwargs)
            if args[6]=='fresh':raise p.TransportError('CHANNEL_FAILED')
            return result
        with patch.object(b,'_sql',side_effect=lost),self.assertRaisesRegex(b.Phase5BError,'MANUAL_ACTION'):self.complete()
        self.assertEqual(self.query(f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'],1)
        before=self.query(f'SELECT password_hash FROM `{self.db}`.UserInfo')
        with self.assertRaises(b.Phase5BError):self.complete()
        self.assertEqual(before,self.query(f'SELECT password_hash FROM `{self.db}`.UserInfo'))
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_parent_crash_after_real_mutation_is_durable_and_blocking(self):
        original=b._sql
        def child():
            def crashed(*args,**kwargs):
                result=original(*args,**kwargs)
                if args[6]=='fresh':os._exit(77)
                return result
            with patch.object(b,'_sql',side_effect=crashed):self.complete()
        process=multiprocessing.get_context('fork').Process(target=child);process.start();process.join(120)
        if process.is_alive():process.kill();process.join(5)
        self.assertEqual(process.exitcode,77)
        self.assertTrue(list(self.runtime.state_root.iterdir()))
        self.assertEqual(self.query(f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'],1)
        with self.assertRaises(b.Phase5BError):self.complete()
        self.assertFalse((self.webroot/'install.lock').exists())

    def test_real_partial_ddl_fault_remains_manual_no_fake_rollback(self):
        # Deliberate test-only corruption of a COPIED schema and its fixture pin.
        # No production pin injection option is exposed by the API.
        manifest=b.verify_source(self.webroot)
        schema=self.webroot/'sql/schema.sql';schema.write_bytes(schema.read_bytes()+b'\nINVALID_SQL_FIXTURE_COMMAND;\n')
        manifest['sql/schema.sql']['sha256']=hashlib.sha256(schema.read_bytes()).hexdigest()
        with patch.object(b,'WEB_RUNTIME_SHA256',hashlib.sha256(p._json(manifest)).hexdigest()):
            with self.assertRaisesRegex(b.Phase5BError,'MANUAL_ACTION'):self.complete()
        rows=self.query(f"SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA='{self.db}'")
        self.assertGreater(rows[0]['n'],0);self.assertFalse((self.webroot/'install.lock').exists())

    def test_managed_insufficient_authority_is_rejected_before_any_ddl(self):
        name='reader'+os.urandom(4).hex();account=literal(name)+"@'127.0.0.1'"
        self.control(self.primary,[f"CREATE USER {account} IDENTIFIED BY 'fixture-reader-secret'",f"GRANT SELECT ON mysql.* TO {account}"])
        try:
            self.authority=b.SqlAuthority(name,'fixture-reader-secret')
            with self.assertRaisesRegex(b.Phase5BError,'AUTHORITY_PRIVILEGES_REQUIRED'):self.complete()
            self.assertFalse(self.directory.exists())
            self.assertEqual(self.query(f"SELECT COUNT(*) AS n FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{self.db}'")[0]['n'],0)
        finally:self.control(self.primary,[f'DROP USER {account}'])

    def test_existing_metadata_reader_uses_migration_visibility(self):
        self.existing();self.control(self.primary,[f'CREATE TABLE `{self.db}`.Sentinel (id INT)'])
        name='reader'+os.urandom(4).hex();account=literal(name)+"@'127.0.0.1'"
        self.control(self.primary,[f"CREATE USER {account} IDENTIFIED BY 'fixture-reader-secret'",f"GRANT SELECT ON mysql.* TO {account}"])
        try:
            self.authority=b.SqlAuthority(name,'fixture-reader-secret')
            with self.assertRaisesRegex(b.Phase5BError,'DATABASE_NOT_EMPTY'):self.complete()
            self.assertFalse(self.directory.exists())
        finally:self.control(self.primary,[f'DROP USER {account}'])

    def test_existing_local_custom_port_is_used_by_generated_runtime(self):
        server=next(x for x in self.servers if x['kind']=='clear')
        self.existing(server=server)
        self.assertTrue(self.complete()['runtime_verified'])
        self.assertEqual(self.control(server,query=f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'],1)
        self.assertEqual(json.loads((self.directory/'instance.json').read_text())['database']['port'],server['port'])

    def test_existing_local_ipv6_loopback_uses_exact_host(self):
        server=next(x for x in self.servers if x['kind']=='ipv6')
        self.app_account=literal(self.app)+"@'::1'";self.migration_account=literal(self.migration)+"@'::1'"
        self.existing(server=server);self.payload['database']['host']='::1'
        self.assertTrue(self.complete()['runtime_verified'])
        self.assertEqual(self.control(server,query=f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'],1)

    def test_release_failure_never_seals_and_keeps_recovery_state(self):
        original=b._sql
        def reject(*args,**kwargs):
            if args[6]=='release':raise p.TransportError('CHANNEL_FAILED')
            return original(*args,**kwargs)
        with patch.object(b,'_sql',side_effect=reject),self.assertRaisesRegex(b.Phase5BError,'MANUAL_ACTION'):self.complete()
        self.assertFalse((self.webroot/'install.lock').exists())
        self.assertEqual(self.query(f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'],1)
        self.assertEqual(self.query(f"SELECT COUNT(*) AS n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'],1)
        with self.assertRaises(b.Phase5BError):self.complete()

    def test_concurrent_real_calls_have_one_winner(self):
        results=[];errors=[];barrier=threading.Barrier(2)
        def run():
            barrier.wait()
            try:results.append(self.complete())
            except b.Phase5BError as error:errors.append(str(error))
        jobs=[threading.Thread(target=run) for _ in range(2)]
        for job in jobs:job.start()
        for job in jobs:job.join(120)
        self.assertFalse(any(x.is_alive() for x in jobs));self.assertEqual(len(results),1);self.assertEqual(len(errors),1)
        self.assertTrue(results[0]['runtime_verified'])
        self.assertEqual(self.query(f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'],1)

    def test_remote_accounts_without_require_ssl_are_refused(self):
        self.existing(remote=True)
        self.control(self.primary,[f'ALTER USER {self.app_account} REQUIRE NONE'])
        with self.assertRaisesRegex(b.Phase5BError,'ACCOUNT_POLICY_REJECTED'):self.complete()
        self.assertFalse(self.directory.exists())

    def test_generated_runtime_loads_without_private_check_boundary(self):
        self.existing(remote=True);self.complete()
        script=("$v=json_decode(stream_get_contents(STDIN),true);define('APP_ROOT',$v['root']);"
            "require APP_ROOT.'/includes/db.php';require APP_ROOT.'/includes/functions.php';"
            "require APP_ROOT.'/includes/assistant/config.php';$p=get_pdo();"
            "echo json_encode(['db'=>$p->query('SELECT DATABASE()')->fetchColumn()===$v['db'],"
            "'key'=>hestia_assistant_api_key()==='', 'boundary'=>defined('HESTIA_PRIVATE_CHECK_ROOT')]);")
        result=subprocess.run(['setpriv','--reuid='+str(self.web.pw_uid),'--regid='+str(self.web.pw_gid),'--clear-groups',
            'php','-d','display_errors=0','-d','log_errors=0','-r',script],input=json.dumps({'root':str(self.webroot),'db':self.db}).encode(),
            capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stderr,b'')
        self.assertEqual(json.loads(result.stdout),{'db':True,'key':True,'boundary':False})

    def test_stored_configuration_is_not_executable_php_and_secrets_do_not_escape(self):
        self.existing();self.complete()
        data=json.loads((self.directory/'instance.json').read_text())
        self.assertEqual(data['database']['password'],self.payload['secrets']['database_password'])
        for entry in self.runtime.state_root.iterdir():
            text=entry.read_text()
            for value in self.payload['secrets'].values():
                if value:self.assertNotIn(value,text)
        self.assertNotIn(self.payload['secrets']['database_password'],(self.webroot/'includes/db.php').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--web-source',type=Path,required=True);args=parser.parse_args()
    WEB=args.web_source
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Phase5BRealTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'suite':'phase5b_e2e','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'skipped':len(result.skipped),'status':'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL'}
    print(json.dumps(report))
    raise SystemExit(0 if report['status']=='PASS' else 1)
