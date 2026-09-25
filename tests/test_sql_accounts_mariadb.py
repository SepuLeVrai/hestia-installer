"""Opt-in, disposable MariaDB integration, mandatory in both CI core jobs.

Creates its OWN isolated local server and random fixtures. Refuses an occupied
3306 port; never connects to an existing local/remote server. No production DB.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from installer import database_config as config
from installer import php_transport as p
from installer import sql_accounts as accounts
from sql_accounts_fixture import ProtectedConfigurationFixture

# Test-only SQL controller, deliberately absent from the Installer runtime.
CONTROLLER = r'''
$d=json_decode(stream_get_contents(STDIN),true,32,JSON_THROW_ON_ERROR);
try {
 $pdo=new PDO('mysql:unix_socket='.$d['socket'].';charset=utf8mb4','root','',[
   PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_EMULATE_PREPARES=>false]);
 foreach($d['statements'] as $sql) $pdo->exec($sql);
 $value=isset($d['query']) ? $pdo->query($d['query'])->fetchAll(PDO::FETCH_ASSOC) : [];
 echo json_encode($value,JSON_THROW_ON_ERROR);
} catch(Throwable $e) {fwrite(STDERR,'FIXTURE_SQL_FAILED_'.($e instanceof PDOException ? ($e->errorInfo[1]??0) : 0));exit(1);}
'''


def literal(value):
    # Fixtures force the server session's default escaping mode (no SQL-mode user input).
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


class SqlAccountsMariaDBTests(ProtectedConfigurationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_ACCOUNT_DB_TEST') != '1':
            raise RuntimeError('HESTIA_ACCOUNT_DB_TEST=1 required on a disposable host; no skipped SQL tests')
        for tool in ('mariadb-install-db', 'mariadbd', 'php'):
            if not shutil.which(tool): raise RuntimeError('Missing required SQL test tool: ' + tool)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 3306))
        super().setUpClass()
        cls.sqltmp = tempfile.TemporaryDirectory(prefix='hestia-sql-isolated-', dir='/var/lib')
        cls.sqlroot = Path(cls.sqltmp.name)
        cls.socket = str(cls.sqlroot / 'server.sock')
        cls.server = None
        try:
            result = subprocess.run(['mariadb-install-db', '--no-defaults', '--auth-root-authentication-method=normal',
                '--skip-test-db', '--skip-name-resolve', '--datadir=' + str(cls.sqlroot / 'data')],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if result.returncode: raise RuntimeError('FIXTURE_SERVER_INITIALIZATION_FAILED')
            cls.server = subprocess.Popen(['mariadbd', '--no-defaults', '--user=root', '--datadir=' + str(cls.sqlroot / 'data'),
                '--socket=' + cls.socket, '--pid-file=' + str(cls.sqlroot / 'server.pid'), '--bind-address=127.0.0.1',
                '--port=3306', '--skip-name-resolve', '--skip-log-bin', '--innodb-buffer-pool-size=32M', '--max-connections=24',
                '--log-error=' + str(cls.sqlroot / 'server.log')], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
            ready = False
            for _ in range(100):
                if cls.server.poll() is not None: break
                if Path(cls.socket).exists():
                    try:
                        cls.sql(query='SELECT 1 AS ready'); ready = True; break
                    except RuntimeError: pass
                time.sleep(.1)
            if not ready: raise RuntimeError('FIXTURE_SERVER_START_FAILED')
            cls.server_version = cls.sql(query='SELECT VERSION() AS version')[0]['version']
            print('Isolated MariaDB integration server:', cls.server_version)
        except BaseException:
            cls._stop_server(); super().tearDownClass(); raise

    @classmethod
    def _stop_server(cls):
        if cls.server is not None:
            cls.server.terminate()
            try: cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired: cls.server.kill(); cls.server.wait(timeout=5)
        cls.sqltmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        try: cls._stop_server()
        finally: super().tearDownClass()

    @classmethod
    def sql(cls, statements=(), query=None, *, timeout=5):
        value = {'socket': cls.socket, 'statements': list(statements)}
        if query is not None: value['query'] = query
        run = subprocess.run(['php', '-d', 'display_errors=0', '-d', 'log_errors=0', '-r', CONTROLLER],
            input=json.dumps(value).encode(), capture_output=True, timeout=timeout)
        if run.returncode: raise RuntimeError(run.stderr.decode() if run.stderr.startswith(b'FIXTURE_SQL_FAILED_') else 'FIXTURE_CONTROLLER_FAILED')
        return json.loads(run.stdout)

    def setUp(self):
        super().setUp()
        suffix = os.urandom(4).hex()
        self.db = 'hq22_' + suffix
        self.app = 'hqa' + suffix; self.migration = 'hqm' + suffix; self.role = 'hqr' + suffix
        self.schema = '`' + self.db.replace('_', '\\_') + '`.*'
        self.app_account = literal(self.app) + "@'127.0.0.1'"
        self.migration_account = literal(self.migration) + "@'127.0.0.1'"
        self.payload['database'].update(name=self.db, user=self.app)
        self.payload['secrets']['database_password'] = "  Application-'\\\"-é🙂-fixture  "
        self.credentials = p.ProvisioningCredentials(self.migration, 'Migration-private-fixture-' + suffix)
        self.sql([f'CREATE DATABASE `{self.db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci',
                  f'CREATE USER {self.app_account} IDENTIFIED BY {literal(self.payload["secrets"]["database_password"])}',
                  f'CREATE USER {self.migration_account} IDENTIFIED BY {literal(self.credentials._password)}',
                  f'GRANT SELECT, INSERT, UPDATE, DELETE ON {self.schema} TO {self.app_account}',
                  f'GRANT ALL PRIVILEGES ON {self.schema} TO {self.migration_account}'])

    def tearDown(self):
        try:
            # Disposal is not a product request: cross-repository fixtures can
            # have 129 InnoDB tables to unlink/fsync. Run 36124119321 passed the
            # partial-DDL assertions then timed out solely in this cleanup.
            # Keep the default five-second controller and all product deadlines.
            self.sql([f'DROP DATABASE IF EXISTS `{self.db}`', f'DROP USER IF EXISTS {self.app_account}',
                f'DROP USER IF EXISTS {self.migration_account}', f"DROP USER IF EXISTS '{self.app}'@'%'",
                f'DROP ROLE IF EXISTS `{self.role}`'],timeout=30)
            self.assertEqual(self.sql(query=f"SELECT COUNT(*) n FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{self.db}'")[0]['n'],0)
        finally: super().tearDown()

    def audit(self, payload=None):
        return accounts.audit_local_accounts(self.runtime, self.payload if payload is None else payload, self.credentials)

    def test_real_accounts_exact_grants_and_secret_free_result(self):
        result = self.audit()
        self.assertEqual(result, accounts.VERIFIED)
        for secret in (self.payload['secrets']['database_password'], self.credentials._password, self.app, self.migration):
            self.assertNotIn(secret, json.dumps(result))
        self.assertFalse(list(self.run.iterdir()))

    def test_global_schema_excess_missing_and_grant_option_refused(self):
        cases = [f'GRANT SELECT ON *.* TO {self.app_account}',
                 f'GRANT ALTER ON {self.schema} TO {self.app_account}',
                 f'REVOKE DELETE ON {self.schema} FROM {self.app_account}',
                 f'GRANT SELECT ON {self.schema} TO {self.app_account} WITH GRANT OPTION']
        for index, sql in enumerate(cases):
            self.sql([sql])
            self.assertEqual(self.audit()['code'], 'ACCOUNT_POLICY_REJECTED', msg=str(index))
            self.sql([f'REVOKE ALL PRIVILEGES, GRANT OPTION FROM {self.app_account}',
                      f'GRANT SELECT, INSERT, UPDATE, DELETE ON {self.schema} TO {self.app_account}'])

    def test_public_and_inactive_role_privileges_refused(self):
        self.sql([f'GRANT SELECT ON {self.schema} TO PUBLIC'])
        try: self.assertEqual(self.audit()['code'], 'ACCOUNT_POLICY_REJECTED')
        finally: self.sql([f'REVOKE SELECT ON {self.schema} FROM PUBLIC'])
        self.sql([f'CREATE ROLE `{self.role}`', f'GRANT SELECT ON {self.schema} TO `{self.role}`',
                  f'GRANT `{self.role}` TO {self.app_account}'])
        self.assertEqual(self.audit()['code'], 'ACCOUNT_POLICY_REJECTED')

    def test_schema_wildcard_and_account_host_wildcard_refused(self):
        self.sql([f'REVOKE ALL PRIVILEGES, GRANT OPTION FROM {self.app_account}',
                  f'GRANT SELECT, INSERT, UPDATE, DELETE ON `{self.db}`.* TO {self.app_account}'])
        self.assertEqual(self.audit()['code'], 'ACCOUNT_POLICY_REJECTED')
        self.sql([f'DROP USER {self.app_account}',
                  f"CREATE USER '{self.app}'@'%' IDENTIFIED BY {literal(self.payload['secrets']['database_password'])}",
                  f"GRANT SELECT, INSERT, UPDATE, DELETE ON {self.schema} TO '{self.app}'@'%'"])
        self.assertEqual(self.audit()['code'], 'ACCOUNT_POLICY_REJECTED')

    def test_connection_errors_and_underprivileged_migration_account_fail_closed(self):
        value = copy.deepcopy(self.payload); value['secrets']['database_password'] = 'wrong-fixture-password'
        self.assertEqual(self.audit(value)['code'], 'CONNECTION_FAILED')
        self.sql([f'REVOKE ALL PRIVILEGES, GRANT OPTION FROM {self.migration_account}',
                  f'GRANT SELECT, INSERT, UPDATE, DELETE ON {self.schema} TO {self.migration_account}'])
        self.assertEqual(self.audit()['code'], 'ACCOUNT_POLICY_REJECTED')

    def test_empty_fresh_staging_and_real_unprivileged_php_connection(self):
        before = self.sql(query=f"SELECT COUNT(*) AS count FROM information_schema.TABLES WHERE TABLE_SCHEMA='{self.db}'")
        result = config.stage_local_database_configuration(self.runtime, self.payload, self.credentials,
            config_root=self.output, confirmed=True)
        self.assertEqual(result, config.STAGED)
        self.assertEqual(before, self.sql(query=f"SELECT COUNT(*) AS count FROM information_schema.TABLES WHERE TABLE_SCHEMA='{self.db}'"))
        self.assertFalse((self.webroot / 'includes/db.php').exists())
        # Real service UID, generated constants, PDO connection. Only a status
        # is emitted. This is not the full Web HTTP/system qualification.
        script = ('$d=json_decode(stream_get_contents(STDIN),true); require $d["path"]; '
                  '$pdo=new PDO("mysql:host=".DB_HOST.";dbname=".DB_NAME.";charset=".DB_CHARSET,DB_USER,DB_PASS,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);'
                  'echo $pdo->query("SELECT CURRENT_USER()")->fetchColumn()===$d["account"] ? "MATCH" : "WRONG";')
        run = subprocess.run(['setpriv', '--reuid=' + str(self.web.pw_uid), '--regid=' + str(self.web.pw_gid), '--clear-groups',
            'php', '-d', 'display_errors=0', '-d', 'log_errors=0', '-r', script], input=json.dumps({
                'path': str(self.directory / 'db.php'), 'account': self.app + '@127.0.0.1'}).encode(),
            capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 0); self.assertFalse(run.stderr); self.assertEqual(run.stdout, b'MATCH')

    def test_existing_data_and_upgrade_configuration_are_preserved(self):
        text = "  D'Exemple <&> 漢字🙂 " + 'x' * 2000
        self.sql([f'CREATE TABLE `{self.db}`.fixture (id INT PRIMARY KEY, value TEXT)',
                  f'INSERT INTO `{self.db}`.fixture VALUES (1,{literal(text)})'])
        before = self.sql(query=f'SELECT SHA2(value,256) AS hash FROM `{self.db}`.fixture')
        value = copy.deepcopy(self.payload); value.update(mode='upgrade', administrator=None)
        value['secrets']['admin_password'] = ''; value['assistant']['action'] = 'preserve'
        self.assertEqual(self.audit(value), accounts.VERIFIED)
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'UPGRADE_CONFIGURATION_REFUSED'):
            config.stage_local_database_configuration(self.runtime, value, self.credentials, config_root=self.output, confirmed=True)
        self.assertEqual(before, self.sql(query=f'SELECT SHA2(value,256) AS hash FROM `{self.db}`.fixture'))
        self.assertFalse(list(self.output.iterdir()))

    def test_staging_reaudits_instead_of_using_stale_success(self):
        self.assertEqual(self.audit(), accounts.VERIFIED)
        self.sql([f'GRANT ALTER ON {self.schema} TO {self.app_account}'])
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'ACCOUNT_AUDIT_REFUSED'):
            config.stage_local_database_configuration(self.runtime, self.payload, self.credentials, config_root=self.output, confirmed=True)
        self.assertFalse(list(self.output.iterdir()))
