#!/usr/bin/env python3
"""Opt-in cross-repository SQL/TLS test. Own servers only; never a deployed DB.

Requires both opt-ins, root, PHP 8.3+, MariaDB binaries, OpenSSL, the exact Web
source pin and a free local 3306. The other server uses a random loopback port.
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
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from installer import database_step as step
from installer import database_config as fs
from installer import php_transport as p
from test_sql_accounts_mariadb import SqlAccountsMariaDBTests, CONTROLLER, literal

WEB = None


def run(*command):
    value = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    if value.returncode:
        raise RuntimeError('TLS_FIXTURE_COMMAND_FAILED: ' + command[0])


class TlsServer:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hestia-tls-fixture-', dir='/var/lib')
        self.root = Path(self.temp.name); self.root.chmod(0o755)
        self.socket = str(self.root / 'sql.sock'); self.process = None
        self.hostname = 'hq' + os.urandom(5).hex() + '.example.test'
        self.host_line = '\n127.0.0.1 ' + self.hostname + '\n'
        self.ca = self.root / 'ca.pem'
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(self.root / 'ca.key'),
            '-out', str(self.ca), '-days', '2', '-subj', '/CN=HESTIA disposable test CA',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        for kind in ('good', 'wrong', 'expired'):
            name = self.hostname if kind != 'wrong' else 'wrong.example.test'
            run('openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(self.root / (kind + '.key')),
                '-out', str(self.root / (kind + '.csr')), '-subj', '/CN=' + name)
            ext = self.root / (kind + '.ext')
            ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:' + name + '\n')
            if kind == 'expired':
                (self.root / 'index').write_text(''); (self.root / 'serial').write_text('1000\n')
                (self.root / 'certs').mkdir()
                ca_config = self.root / 'ca.conf'
                ca_config.write_text('[ca]\ndefault_ca=issuer\n[issuer]\ndatabase=' + str(self.root / 'index')
                    + '\nnew_certs_dir=' + str(self.root / 'certs') + '\nserial=' + str(self.root / 'serial')
                    + '\nprivate_key=' + str(self.root / 'ca.key') + '\ncertificate=' + str(self.ca)
                    + '\ndefault_md=sha256\npolicy=policy\nx509_extensions=server\nunique_subject=no\n'
                    + '[policy]\ncommonName=supplied\n[server]\n' + ext.read_text())
                run('openssl', 'ca', '-batch', '-notext', '-config', str(ca_config), '-in', str(self.root / 'expired.csr'),
                    '-out', str(self.root / 'expired.pem'), '-startdate', '20200101000000Z', '-enddate', '20200102000000Z')
            else:
                run('openssl', 'x509', '-req', '-in', str(self.root / (kind + '.csr')), '-CA', str(self.ca),
                    '-CAkey', str(self.root / 'ca.key'), '-CAcreateserial', '-out', str(self.root / (kind + '.pem')),
                    '-days', '2', '-extfile', str(ext))
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); self.port = probe.getsockname()[1]
        with open('/etc/hosts', 'a') as f: f.write(self.host_line)
        run('mariadb-install-db', '--no-defaults', '--auth-root-authentication-method=normal', '--skip-test-db',
            '--skip-name-resolve', '--datadir=' + str(self.root / 'data'))
        self.start('good')

    def start(self, kind, address="127.0.0.1"):
        if self.process is not None:
            self.process.terminate(); self.process.wait(timeout=15)
        command = ['mariadbd', '--no-defaults', '--user=root', '--datadir=' + str(self.root / 'data'),
                   '--socket=' + self.socket, '--pid-file=' + str(self.root / 'pid'), '--bind-address=' + address,
                   '--port=' + str(self.port), '--skip-name-resolve', '--skip-log-bin', '--innodb-buffer-pool-size=32M',
                   '--log-error=' + str(self.root / 'server.log')]
        if kind is not None:
            command += ['--ssl-ca=' + str(self.ca), '--ssl-cert=' + str(self.root / (kind + '.pem')),
                        '--ssl-key=' + str(self.root / (kind + '.key'))]
        else: command += ['--skip-ssl']
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if self.process.poll() is not None: break
            if Path(self.socket).exists():
                try:
                    self.sql(query='SELECT 1 AS ready'); return
                except RuntimeError: pass
            time.sleep(.1)
        raise RuntimeError('TLS_FIXTURE_SERVER_START_FAILED')

    def sql(self, statements=(), query=None):
        data = {'socket': self.socket, 'statements': list(statements)}
        if query is not None: data['query'] = query
        r = subprocess.run(['php', '-d', 'display_errors=0', '-d', 'log_errors=0', '-r', CONTROLLER],
            input=json.dumps(data).encode(), capture_output=True, timeout=5)
        if r.returncode: raise RuntimeError('TLS_FIXTURE_SQL_FAILED')
        return json.loads(r.stdout)

    def close(self):
        if self.process is not None:
            self.process.terminate(); self.process.wait(timeout=15)
        # Remove exactly the fixture line, leaving other resolver entries unchanged.
        text = Path('/etc/hosts').read_text()
        with open('/etc/hosts', 'w') as f: f.write(text.replace(self.host_line, ''))
        self.temp.cleanup()


class DatabaseStepIntegration(SqlAccountsMariaDBTests):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_DATABASE_STEP_TEST') != '1': raise RuntimeError('Explicit cross-repository SQL opt-in required')
        if WEB is None: raise RuntimeError('Exact Web source required')
        super().setUpClass()
        try: cls.tls = TlsServer()
        except BaseException: super().tearDownClass(); raise

    @classmethod
    def tearDownClass(cls):
        try: cls.tls.close()
        finally: super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.runtime = p.PhpRuntime(self.runtime.php, self.runtime.extension_dir, self.worker.pw_uid, self.worker.pw_gid,
                                    self.run, self.root / 'attempts', timeout_seconds=30)
        self.client = step.DatabaseStep(self.runtime, WEB, repository=p.WEB_REPOSITORY, commit=step.WEB_COMMIT)
        self.auth_user = 'hqx' + os.urandom(5).hex()
        self.authority = step.SqlAuthorityCredentials(self.auth_user, 'Authority-fixture-only-' + os.urandom(8).hex())
        self.auth_account = literal(self.auth_user) + "@'127.0.0.1'"
        self.sql([f'CREATE USER {self.auth_account} IDENTIFIED BY {literal(self.authority._password)}',
                  f'GRANT ALL PRIVILEGES ON *.* TO {self.auth_account} WITH GRANT OPTION'])

    def tearDown(self):
        try:
            self.sql([f'DROP USER IF EXISTS {self.auth_account}'])
            self.tls.sql([f'DROP DATABASE IF EXISTS `{self.db}`', f'DROP USER IF EXISTS {self.app_account}',
                          f'DROP USER IF EXISTS {self.migration_account}'])
            self.tls.start('good')
        finally: super().tearDown()

    def prepare(self, payload=None, authority=None):
        return self.client.prepare(self.payload if payload is None else payload, self.credentials,
                                   config_root=self.output, confirmed=True, authority=authority)

    def connect_staged(self, expect_error=False):
        data = {'loader': str(self.directory / 'db.php'), 'functions': str(WEB / 'includes/functions.php')}
        script = r'''$v=json_decode(stream_get_contents(STDIN),true);
try {require $v["loader"]; require $v["functions"]; $p=get_pdo();
$q=$p->prepare('SELECT valeur FROM App_Config WHERE cle=?'); $q->execute(['APP_VERSION']);
echo json_encode(['version'=>$q->fetchColumn(),'same'=>get_pdo()===$p]);
} catch(Throwable $e){echo json_encode(['error'=>$e->getMessage()]);exit(20);}'''
        result = subprocess.run(['setpriv', '--reuid=' + str(self.web.pw_uid), '--regid=' + str(self.web.pw_gid), '--clear-groups',
            'php', '-d', 'display_errors=0', '-d', 'log_errors=0', '-r', script], input=json.dumps(data).encode(), capture_output=True, timeout=10)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(result.returncode, 20 if expect_error else 0, result.stdout.decode())
        return json.loads(result.stdout)

    def managed(self):
        self.sql([f'DROP DATABASE `{self.db}`', f'DROP USER {self.app_account}', f'DROP USER {self.migration_account}'])
        self.payload['database']['mode'] = 'managed'

    def remote(self):
        self.tls.sql([f'CREATE DATABASE `{self.db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci',
            f'CREATE USER {self.app_account} IDENTIFIED BY {literal(self.payload["secrets"]["database_password"])} REQUIRE SSL',
            f'CREATE USER {self.migration_account} IDENTIFIED BY {literal(self.credentials._password)} REQUIRE SSL',
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON {self.schema} TO {self.app_account}',
            f'GRANT ALL PRIVILEGES ON {self.schema} TO {self.migration_account}'])
        self.payload['database'].update(mode='remote', host=self.tls.hostname, port=self.tls.port, tls_ca_file=str(self.tls.ca))

    def test_step_existing_fresh_and_staged_get_pdo(self):
        self.payload['administrator']['first_name'] = 'Élise <&>🙂'
        self.payload['secrets']['admin_password'] = "  exact-password-Éé🙂'  "
        result = self.prepare()
        self.assertEqual(result['state'], 'DATABASE_CONFIGURATION_READY', result)
        self.assertFalse(result['result']['configuration_activated'])
        self.assertTrue(result['result']['migration_retained'])
        self.assertEqual(self.connect_staged(), {'version': p.ENGINE_VERSION, 'same': True})
        self.assertFalse((self.webroot / 'includes/db.php').exists())
        self.assertFalse((self.webroot / 'install.lock').exists())
        for path in self.root.rglob('*'):
            if path.is_file():
                data = path.read_bytes()
                for secret in (self.authority._password, self.credentials._password, self.payload['secrets']['admin_password']):
                    self.assertNotIn(secret.encode(), data)
        for name in ('database.json', 'db.php', 'state.json'):
            self.assertTrue(self.permission(self.web, '-r', self.directory / name))
            self.assertFalse(self.permission(self.worker, '-r', self.directory / name))
            self.assertFalse(self.permission(self.other, '-r', self.directory / name))
            self.assertFalse(self.permission(self.web, '-w', self.directory / name))

    def test_step_managed_creation_and_temporary_account_removal(self):
        self.managed()
        result = self.prepare(authority=self.authority)
        self.assertEqual(result['state'], 'DATABASE_CONFIGURATION_READY', result)
        self.assertFalse(result['result']['migration_retained'])
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) AS n FROM mysql.global_priv WHERE User='{self.migration}'")[0]['n'], 0)
        self.assertEqual(self.connect_staged()['version'], p.ENGINE_VERSION)
        # A second call does not re-create anything or overwrite the staged files.
        before = {f.name: f.read_bytes() for f in self.directory.iterdir()}
        with self.assertRaises(step.DatabaseStepError): self.prepare(authority=self.authority)
        self.assertEqual(before, {f.name: f.read_bytes() for f in self.directory.iterdir()})

    def test_step_managed_database_and_host_variant_collision(self):
        self.payload['database']['mode'] = 'managed'
        result = self.prepare(authority=self.authority)
        self.assertEqual(result['state'], 'REFUSED')
        self.assertEqual(result['code'], 'SQL_ACCOUNT_OCCUPIED')
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA='{self.db}'")[0]['n'], 0)

    def test_step_managed_empty_existing_database_is_never_reused(self):
        self.sql([f'DROP USER {self.app_account}', f'DROP USER {self.migration_account}'])
        self.payload['database']['mode'] = 'managed'
        result = self.prepare(authority=self.authority)
        self.assertEqual(result['code'], 'SQL_DATABASE_OCCUPIED', result)
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) AS n FROM mysql.global_priv WHERE User IN ('{self.app}','{self.migration}')")[0]['n'], 0)

    def test_step_managed_other_host_variant_is_never_overwritten(self):
        self.managed()
        variant = literal(self.app) + "@'192.0.2.44'"
        self.sql([f'CREATE USER {variant} IDENTIFIED BY '+literal('other-host-fixture-password')])
        try:
            before = self.sql(query=f"SELECT Priv FROM mysql.global_priv WHERE User='{self.app}'")
            result = self.prepare(authority=self.authority)
            self.assertEqual(result['code'], 'SQL_ACCOUNT_OCCUPIED', result)
            self.assertEqual(self.sql(query=f"SELECT Priv FROM mysql.global_priv WHERE User='{self.app}'"), before)
            self.assertEqual(self.sql(query=f"SELECT COUNT(*) AS n FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{self.db}'")[0]['n'], 0)
        finally: self.sql([f'DROP USER {variant}'])

    def test_step_sql_success_then_configuration_io_failure_is_not_success(self):
        with patch.object(fs, '_write', side_effect=OSError('synthetic disk failure')):
            result = self.prepare()
        self.assertEqual(result['state'], 'MANUAL_ACTION')
        self.assertEqual(self.sql(query=f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'], 1)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.webroot/'includes/db.php').exists())
        with self.assertRaises(step.DatabaseStepError): self.prepare()

    def test_step_actual_partial_ddl_is_uncertain_and_never_replayed(self):
        # Fault-injection fixture only: a trusted COPY with its own test pin.
        # The published engine and its production pin are never altered.
        source = self.root/'fault-source'; shutil.copytree(WEB, source, ignore=shutil.ignore_patterns('.git'))
        schema = source/'sql/schema.sql'
        schema.write_bytes(schema.read_bytes()+b'\nCREATE TABLE HestiaFaultFixture (id INT);\nINSERT INTO HestiaMissingFixture VALUES (1);\n')
        names = sorted([*step.ENGINE_FILES, *[str(f.relative_to(source)) for f in (source/'vendor').rglob('*') if f.is_file()]])
        digest = hashlib.sha256()
        for name in names:
            data = (source/name).read_bytes()
            digest.update(p._json([name,len(data),hashlib.sha256(data).hexdigest()])+b'\n')
        with patch.object(step,'ENGINE_SHA256',digest.hexdigest()):
            client = step.DatabaseStep(self.runtime, source, repository=p.WEB_REPOSITORY, commit=step.WEB_COMMIT)
            result = client.prepare(self.payload,self.credentials,config_root=self.output,confirmed=True)
        self.assertEqual(result['state'],'MANUAL_ACTION',result)
        self.assertEqual(result['code'],'FRESH_INCOMPLETE_MANUAL_ACTION',result)
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA='{self.db}' AND TABLE_NAME='HestiaFaultFixture'")[0]['n'],1)
        self.assertFalse((self.directory/'database.json').exists())
        with self.assertRaises(step.DatabaseStepError):self.prepare()

    def test_step_local_ipv6_nondefault_port_with_effective_ipv6_accounts(self):
        self.remote()
        extra = []
        for user, password, grants in [(self.app,self.payload['secrets']['database_password'],'SELECT, INSERT, UPDATE, DELETE'),
                                       (self.migration,self.credentials._password,'ALL PRIVILEGES')]:
            account = literal(user)+"@'::1'";extra.append(account)
            self.tls.sql([f'CREATE USER {account} IDENTIFIED BY {literal(password)}',f'GRANT {grants} ON {self.schema} TO {account}'])
        try:
            self.tls.start(None, '::1')
            self.payload['database'].update(mode='existing_local',host='::1',tls_ca_file=None)
            result=self.prepare()
            self.assertEqual(result['state'],'DATABASE_CONFIGURATION_READY',result)
            self.assertFalse(result['result']['tls_verified'])
            self.assertEqual(self.connect_staged()['version'],p.ENGINE_VERSION)
        finally:self.tls.sql([f'DROP USER {account}' for account in extra])

    def test_step_insufficient_authority_is_rejected_before_ddl(self):
        self.managed()
        self.sql([f'REVOKE ALL PRIVILEGES, GRANT OPTION FROM {self.auth_account}',
                  f'GRANT CREATE, SELECT ON *.* TO {self.auth_account}'])
        result = self.prepare(authority=self.authority)
        self.assertEqual(result['code'], 'AUTHORITY_PRIVILEGES_REQUIRED', result)
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) AS n FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{self.db}'")[0]['n'], 0)

    def test_step_existing_data_and_upgrade_preserved(self):
        text = 'Élise🙂<&>\\' * 1000
        self.sql([f'CREATE TABLE `{self.db}`.sentinel (n BIGINT, value LONGTEXT)',
                  f'INSERT INTO `{self.db}`.sentinel VALUES (-9223372036854775807, {literal(text)})'])
        original = self.sql(query=f'SELECT * FROM `{self.db}`.sentinel')
        upgrade = copy.deepcopy(self.payload); upgrade.update(mode='upgrade', administrator=None)
        upgrade['secrets']['admin_password'] = ''
        self.assertEqual(self.client.audit(upgrade, self.credentials)['state'], 'ACCOUNTS_VERIFIED')
        with self.assertRaisesRegex(step.DatabaseStepError, 'UPGRADE_PREPARE_REFUSED'): self.prepare(upgrade)
        result = self.prepare()
        self.assertEqual(result['code'], 'FRESH_DATABASE_NOT_EMPTY', result)
        self.assertEqual(self.sql(query=f'SELECT * FROM `{self.db}`.sentinel'), original)
        self.assertFalse((self.directory / 'database.json').exists())

    def test_step_authentication_and_excess_privileges(self):
        bad = copy.deepcopy(self.payload); bad['secrets']['database_password'] = 'incorrect-synthetic-password'
        self.assertEqual(self.client.audit(bad, self.credentials)['code'], 'SQL_CONNECTION_FAILED')
        self.sql([f'GRANT ALTER ON {self.schema} TO {self.app_account}'])
        self.assertEqual(self.client.audit(self.payload, self.credentials)['code'], 'ACCOUNT_POLICY_REJECTED')
        result = self.prepare(); self.assertEqual(result['code'], 'ACCOUNT_POLICY_REJECTED')
        self.assertEqual(self.sql(query=f"SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA='{self.db}'")[0]['n'], 0)

    def test_step_reply_lost_after_actual_sql_blocks_replay(self):
        exchange = p._exchange
        def lost(*args, **kwargs):
            code, raw = exchange(*args, **kwargs)
            self.assertEqual(code, 0, raw.decode())
            raise p.TransportError('TIMEOUT')
        with patch.object(p, '_exchange', side_effect=lost): result = self.prepare()
        self.assertEqual(result['state'], 'MANUAL_ACTION')
        self.assertEqual(self.sql(query=f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'], 1)
        self.assertFalse((self.directory / 'database.json').exists())
        with self.assertRaises(step.DatabaseStepError): self.prepare()

    def test_step_parent_crash_after_sql_preserves_interlocks(self):
        exchange = p._exchange
        def child():
            def crash(*args, **kwargs):
                result = exchange(*args, **kwargs)
                if result[0] != 0: os._exit(78)
                os._exit(77)
            with patch.object(p, '_exchange', side_effect=crash): self.prepare()
        proc = multiprocessing.get_context('fork').Process(target=child); proc.start(); proc.join(45)
        if proc.is_alive(): proc.kill(); proc.join()
        self.assertEqual(proc.exitcode, 77)
        self.assertEqual(self.sql(query=f'SELECT COUNT(*) AS n FROM `{self.db}`.UserInfo')[0]['n'], 1)
        with self.assertRaises(step.DatabaseStepError): self.prepare()
        self.assertTrue(list((self.root / 'attempts').glob('fresh-*.attempt')))

    def test_step_tls_positive_nondefault_port_and_real_web_connection(self):
        self.remote()
        self.assertEqual(self.client.audit(self.payload, self.credentials)['state'], 'ACCOUNTS_VERIFIED')
        result = self.prepare()
        self.assertEqual(result['state'], 'DATABASE_CONFIGURATION_READY', result)
        self.assertTrue(result['result']['tls_verified'])
        self.assertEqual(self.connect_staged()['version'], p.ENGINE_VERSION)

    def test_step_wrong_hostname_expired_and_clear_tls_servers_rejected(self):
        self.remote()
        self.assertEqual(self.client.audit(self.payload, self.credentials)['state'], 'ACCOUNTS_VERIFIED')
        for kind in ('wrong', 'expired', None):
            with self.subTest(kind=kind):
                self.tls.start(kind)
                result = self.client.audit(self.payload, self.credentials)
                self.assertEqual(result['code'], 'SQL_TLS_CONNECTION_FAILED', result)
                self.assertEqual(self.tls.sql(query=f"SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA='{self.db}'")[0]['n'], 0)

    def test_step_untrusted_ca_and_ca_symlink_rejected(self):
        self.remote()
        other = self.root / 'other-ca.pem'
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(self.root / 'other.key'),
            '-out', str(other), '-days', '1', '-subj', '/CN=Other disposable CA')
        self.payload['database']['tls_ca_file'] = str(other)
        self.assertEqual(self.client.audit(self.payload, self.credentials)['code'], 'SQL_TLS_CONNECTION_FAILED')
        link = self.root / 'linked-ca.pem'; link.symlink_to(other)
        self.payload['database']['tls_ca_file'] = str(link)
        with self.assertRaises(step.DatabaseStepError): self.prepare()
        self.assertFalse(self.directory.exists())

    def test_step_remote_accounts_without_require_ssl_refused(self):
        self.remote()
        self.tls.sql([f'ALTER USER {self.app_account} REQUIRE NONE'])
        self.assertEqual(self.client.audit(self.payload, self.credentials)['code'], 'ACCOUNT_POLICY_REJECTED')

    def test_step_staged_ca_tamper_never_downgrades_web_connection(self):
        self.remote(); result = self.prepare()
        self.assertEqual(result['state'], 'DATABASE_CONFIGURATION_READY', result)
        self.assertEqual(self.connect_staged()['version'], p.ENGINE_VERSION)
        ca = self.directory / 'ca.pem'; ca.write_bytes(ca.read_bytes() + b'\n')
        self.assertEqual(self.connect_staged(expect_error=True)['error'], 'SQL_CA_INVALID')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web-source', type=Path, required=True)
    args = parser.parse_args(); WEB = args.web_source
    # Only this campaign's named scenarios. The inherited eight 5B2.2a tests
    # remain required in the permanent core suite, not silently counted twice.
    names = sorted(n for n in DatabaseStepIntegration.__dict__ if n.startswith('test_step_'))
    suite = unittest.TestSuite(DatabaseStepIntegration(n) for n in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'campaign': '5B2.2 real Web SQL/TLS', 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and not result.skipped and result.testsRun == len(names) else 'FAIL'}))
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped and result.testsRun == len(names) else 1)
