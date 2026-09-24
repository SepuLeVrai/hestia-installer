"""Opt-in real MariaDB transport tests. Disposable root host and fixture DB only.

Run with HESTIA_TRANSPORT_DB_TEST=1 and --web-source pointing to the pinned Web
checkout, owned by root. Runtime files are unmodified; faults are injected at
the Python transport boundary only. No secrets in command-line arguments.
"""
from __future__ import annotations
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[1])]
from installer import php_transport as p
def configuration(mode="fresh"):
    return {
        "version": 1, "mode": mode,
        "web": {"hostname": "hestia.example.test", "webroot": "/var/www/hestia", "service_user": "www-data"},
        "database": {"mode": "managed" if mode == "fresh" else "existing_local", "host": "localhost",
                     "port": 3306, "name": "hestia", "user": "hestia", "tls_ca_file": None},
        "administrator": {"first_name": "Bastien", "last_name": "D'Exemple & associés", "email": "admin@example.test"} if mode == "fresh" else None,
        "assistant": {"action": "disabled" if mode == "fresh" else "preserve"},
        "secrets": {"database_password": "DB-fixture-2026!", "admin_password": "Admin-fixture-2026!" if mode == "fresh" else "", "openai_api_key": ""},
    }

# Test controller only. Server fixture credentials use stdin, never argv/logs.
FIXTURE = r'''
set_error_handler(static function(){throw new RuntimeException('FIXTURE_FAILED');});
try {
  $r=json_decode(stream_get_contents(STDIN),true,16,JSON_THROW_ON_ERROR);
  $o=[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC];
  $s=new PDO('mysql:host=127.0.0.1;port='.$r['port'].';charset=utf8mb4',$r['root_user'],$r['root_password'],$o);
  $name=$r['name'];$user=$r['user'];
  if(!preg_match('/^hestia_ci_transport_[a-f0-9]{12}$/D',$name)||!preg_match('/^hcit_[a-f0-9]{12}$/D',$user))throw new RuntimeException();
  $peer=substr(strrchr((string)$s->query('SELECT USER()')->fetchColumn(),'@'),1);
  if($peer!=='localhost'&&!filter_var($peer,FILTER_VALIDATE_IP))throw new RuntimeException();
  $account=$s->quote($user).'@'.$s->quote($peer);$grant=str_replace('_','\\_',$name);
  $result=[];
  if($r['action']==='create'){
    $s->exec("CREATE DATABASE `$name` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");
    $s->exec('CREATE USER '.$account.' IDENTIFIED BY '.$s->quote($r['password']));
    $rights=$r['limited']?'SELECT, CREATE, DROP, ALTER, INDEX, REFERENCES':'ALL PRIVILEGES';
    $s->exec("GRANT $rights ON `$grant`.* TO $account");
    if($r['sentinel']){
      $s->exec("CREATE TABLE `$name`.Sentinel (id INT PRIMARY KEY, data LONGTEXT)");
      $q=$s->prepare("INSERT INTO `$name`.Sentinel VALUES (-2147483648,?)");$q->execute([str_repeat('longé<&>🙂',2000)]);
    }
    $result=['created'=>true];
  }elseif($r['action']==='observe'){
    $s->exec("USE `$name`");
    if($r['sentinel']){
      $result=['sentinel_preserved'=>$s->query('SELECT data FROM Sentinel')->fetchColumn()===str_repeat('longé<&>🙂',2000)];
    }else{
      $row=$s->query('SELECT * FROM UserInfo')->fetch();
      $result=['one_admin'=>(int)$s->query('SELECT COUNT(*) FROM UserInfo')->fetchColumn()===1,
        'names'=>$row['prenom']==='Élise <&>🙂'&&$row['nom']==="D'Angelo",
        'password'=>password_verify($r['admin_password'],$row['password_hash']),
        'roles'=>(int)$s->query("SELECT COUNT(*) FROM Roles WHERE code_role IN ('ADMIN_GENERAL','ADMIN_DIRECTION','ADMIN_EQUIPE','COLLABORATEUR')")->fetchColumn()===4,
        'version'=>$s->query("SELECT valeur FROM App_Config WHERE cle='APP_VERSION'")->fetchColumn()==='3.0.0.0-stable-20260914',
        'assistant_disabled'=>$s->query("SELECT valeur FROM App_Config WHERE cle='assistant.enabled'")->fetchColumn()==='0'];
    }
  }elseif($r['action']==='destroy'){
    $s->exec("DROP DATABASE IF EXISTS `$name`");$s->exec('DROP USER IF EXISTS '.$account);$result=['destroyed'=>true];
  }elseif($r['action']==='version'){$result=['php'=>PHP_VERSION,'mariadb'=>$s->query('SELECT VERSION()')->fetchColumn()];}
  else{throw new RuntimeException();}
  echo json_encode($result,JSON_THROW_ON_ERROR);
}catch(Throwable $e){fwrite(STDERR,'FIXTURE_FAILED');exit(1);}
'''


class MariaDbTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0 or os.environ.get('HESTIA_TRANSPORT_DB_TEST') != '1':
            raise RuntimeError('Explicit disposable root DB test opt-in required')
        cls.source = Path(os.environ['HESTIA_TRANSPORT_WEB_SOURCE'])
        cls.php = Path(shutil.which('php')).resolve()
        cls.extension_dir = Path(subprocess.check_output([str(cls.php), '-n', '-r', 'echo ini_get("extension_dir");'], text=True))
        cls.account = 'hestiait' + os.urandom(3).hex()
        subprocess.run(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
                        '--shell', '/usr/sbin/nologin', cls.account], check=True, capture_output=True)
        cls.identity = pwd.getpwnam(cls.account)
        cls.observations = 0

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['userdel', cls.account], check=True, capture_output=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hestia-real-transport-')
        self.root = Path(self.tmp.name); self.root.chmod(0o711)
        self.run_root = self.root / 'run'; self.run_root.mkdir(mode=0o711)
        self.state = self.root / 'state'
        suffix = os.urandom(6).hex()
        self.fixture = {'name': 'hestia_ci_transport_' + suffix, 'user': 'hcit_' + suffix,
                        'password': 'Setup-fixture-' + os.urandom(16).hex(),
                        'admin_password': "  Exact'&é🙂passphrase29  ", 'limited': False, 'sentinel': False,
                        'port': int(os.environ.get('HESTIA_TEST_DB_PORT', '3306')),
                        'root_user': os.environ.get('HESTIA_TEST_DB_USER', 'root'),
                        'root_password': os.environ.get('HESTIA_TEST_DB_PASSWORD', '')}
        self.config = configuration()
        self.config['database'].update(mode='existing_local', port=self.fixture['port'], name=self.fixture['name'])
        self.config['administrator'].update(first_name='Élise <&>🙂', last_name="D'Angelo")
        self.config['secrets']['admin_password'] = self.fixture['admin_password']
        self.credentials = p.ProvisioningCredentials(self.fixture['user'], self.fixture['password'])
        self.runtime = p.PhpRuntime(self.php, self.extension_dir, self.identity.pw_uid, self.identity.pw_gid,
                                    self.run_root, self.state)
        self.transport = p.PhpTransport(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=p.WEB_COMMIT)

    def sql(self, action):
        payload = {**self.fixture, 'action': action}
        process = subprocess.run([str(self.php), '-d', 'display_errors=0', '-d', 'log_errors=0', '-r', FIXTURE],
                                 input=json.dumps(payload).encode(), capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, 'Fixture controller failed (raw diagnostics intentionally withheld)')
        self.assertEqual(process.stderr, b'')
        return json.loads(process.stdout)

    def tearDown(self):
        self.sql('destroy')
        self.tmp.cleanup()

    def assert_preserved(self):
        self.assertTrue(all(self.sql('observe').values()), 'Actual SQL state/admin/RBAC/version/Assistant assertion failed')

    def test_real_fresh_and_existing_instance_preservation(self):
        self.sql('create')
        result = self.transport.fresh(self.config, self.credentials, confirmed=True)
        self.assertEqual(result['state'], 'DATABASE_READY', repr(result))
        self.assertGreater(result['result']['schema_statements'], 100)
        self.assertFalse(result['result']['application_installed'])
        self.assert_preserved()
        with self.assertRaisesRegex(p.TransportError, 'FRESH_REPLAY_BLOCKED'):
            self.transport.fresh(self.config, self.credentials, confirmed=True)
        # A different state root simulates a separate installer, not a runtime retry.
        other_runtime = p.PhpRuntime(self.php, self.extension_dir, self.identity.pw_uid, self.identity.pw_gid,
                                    self.run_root, self.root / 'separate-state')
        other = p.PhpTransport(other_runtime, self.source, repository=p.WEB_REPOSITORY, commit=p.WEB_COMMIT)
        changed = json.loads(json.dumps(self.config)); changed['secrets']['admin_password'] = 'NeverResetThisAdmin29!'
        refused = other.fresh(changed, self.credentials, confirmed=True)
        self.assertEqual(refused['code'], 'FRESH_DATABASE_NOT_EMPTY')
        self.assert_preserved()

    def test_existing_atypical_data_and_upgrade_not_touched(self):
        self.fixture['sentinel'] = True; self.sql('create')
        result = self.transport.fresh(self.config, self.credentials, confirmed=True)
        self.assertEqual(result['code'], 'FRESH_DATABASE_NOT_EMPTY'); self.assert_preserved()
        with self.assertRaisesRegex(p.TransportError, 'OPERATION_UNSUPPORTED'):
            self.transport.fresh(configuration('upgrade'), self.credentials, confirmed=True)
        self.assert_preserved()

    def test_partial_sql_error_and_read_only_observation(self):
        self.fixture['limited'] = True; self.sql('create')
        result = self.transport.fresh(self.config, self.credentials, confirmed=True)
        self.assertEqual(result['code'], 'FRESH_INCOMPLETE_MANUAL_ACTION', repr(result))
        self.assertEqual(result['state'], 'MANUAL_ACTION')
        observed = self.transport.inspect(self.config, self.credentials)
        self.assertEqual(observed['state'], 'DATABASE_OBSERVATION', repr(observed))
        self.assertGreater(observed['result']['tables'], 0); self.assertFalse(observed['result']['retry_authorized'])
        with self.assertRaisesRegex(p.TransportError, 'FRESH_REPLAY_BLOCKED'):
            self.transport.fresh(self.config, self.credentials, confirmed=True)

    def test_lost_response_after_real_mutation_no_second_fresh(self):
        self.sql('create'); actual = p._exchange; calls = []
        def lose(*args, **kwargs):
            calls.append(1); result = actual(*args, **kwargs)
            self.assertEqual(result[0], 0)
            raise p.TransportError('CHANNEL_FAILED')
        with patch.object(p, '_exchange', side_effect=lose):
            result = self.transport.fresh(self.config, self.credentials, confirmed=True)
        self.assertEqual(len(calls), 1); self.assertEqual(result['state'], 'MANUAL_ACTION')
        self.assert_preserved()
        observed = self.transport.inspect(self.config, self.credentials)
        self.assertFalse(observed['result']['empty']); self.assertFalse(observed['result']['retry_authorized'])
        with self.assertRaisesRegex(p.TransportError, 'FRESH_REPLAY_BLOCKED'):
            self.transport.fresh(self.config, self.credentials, confirmed=True)

    def test_parent_crash_leaves_durable_guard_and_actual_effects(self):
        self.sql('create'); actual = p._exchange
        def crash():
            def lose(*args, **kwargs):
                result = actual(*args, **kwargs)
                os._exit(77 if result[0] == 0 else 78)
            with patch.object(p, '_exchange', side_effect=lose):
                self.transport.fresh(self.config, self.credentials, confirmed=True)
        child = multiprocessing.get_context('fork').Process(target=crash)
        child.start(); child.join(30)
        try:
            self.assertEqual(child.exitcode, 77)
            text = next(self.state.glob('*.attempt')).read_text()
            self.assertIn('DISPATCHING', text); self.assertNotIn('DATABASE_READY', text)
            self.assertNotIn(self.fixture['password'], text)
            self.assert_preserved()
            with self.assertRaisesRegex(p.TransportError, 'FRESH_REPLAY_BLOCKED'):
                self.transport.fresh(self.config, self.credentials, confirmed=True)
            observed = self.transport.inspect(self.config, self.credentials)
            self.assertFalse(observed['result']['empty']); self.assertFalse(observed['result']['retry_authorized'])
        finally:
            if child.is_alive(): child.kill(); child.join(2)

    def test_inspect_empty_is_not_installation_or_retry_authorization(self):
        self.sql('create')
        result = self.transport.inspect(self.config, self.credentials)
        self.assertTrue(result['result']['empty']); self.assertFalse(result['result']['retry_authorized'])
        self.assertFalse(result['result']['application_installed']); self.assertFalse(self.state.exists())
        versions = self.sql('version')
        print(json.dumps({'environment': versions, 'web_commit': p.WEB_COMMIT, 'engine_sha256': p.ENGINE_SHA256}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--web-source', required=True)
    args = parser.parse_args()
    os.environ['HESTIA_TRANSPORT_WEB_SOURCE'] = args.web_source
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MariaDbTransportTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'scope': 'private Python/PHP transport on actual pinned Web engine',
                      'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                      'skipped': len(result.skipped), 'status': 'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL'}))
    sys.exit(0 if result.wasSuccessful() and result.testsRun == 6 and not result.skipped else 1)
