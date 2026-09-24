"""Private transport regression tests. Disposable root test host required, no real SQL here."""
import copy
import hashlib
import json
import os
import pickle
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from installer import php_transport as p
from test_web_config import request as configuration


def fixture_source(root):
    for name in p.ENGINE_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<?php // transport fixture\n" if name.endswith('.php') else '-- fixture\n')
    (root / 'vendor').mkdir()
    (root / 'vendor' / 'autoload.php').write_text('<?php // test fixture, never a production dependency\n')
    names = sorted(list(p.ENGINE_FILES) + ['vendor/autoload.php'])
    digest = hashlib.sha256()
    for name in names:
        data = (root / name).read_bytes()
        digest.update(p._json([name, len(data), hashlib.sha256(data).hexdigest()]) + b'\n')
    return digest.hexdigest()


def envelope(req, *, error=None):
    if req['operation'] == 'fresh_database':
        result = {'scope': 'DATABASE_READY', 'application_installed': False, 'assistant_enabled': False,
                  'version': p.ENGINE_VERSION, 'schema_statements': 120}
    else:
        result = {'scope': 'DATABASE_OBSERVATION', 'tables': 1, 'routines': 0, 'events': 0,
                  'empty': False, 'retry_authorized': False, 'application_installed': False}
    return {'version': 1, 'operation': req['operation'], 'request_id': req['request_id'],
            'ok': error is None, 'result': result if error is None else None, 'error': error}


class TransportContractTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.credentials = p.ProvisioningCredentials('hestia_setup', 'Private-provisioning-fixture-23!')
        self.request = p._map(self.config, self.credentials, 'fresh_database', True)

    def test_closed_explicit_mapping_keeps_unrelated_secrets_out(self):
        self.config['assistant']['action'] = 'configure'
        self.config['secrets']['openai_api_key'] = 'fixture-openai-' + 'X' * 30
        result = p._map(self.config, self.credentials, 'fresh_database', True)
        self.assertEqual(set(result), {'version', 'operation', 'request_id', 'database', 'administrator', 'confirmed'})
        self.assertEqual(result['database']['host'], '127.0.0.1')
        self.assertEqual(result['database']['password'], self.credentials._password)
        self.assertEqual(result['administrator']['password'], self.config['secrets']['admin_password'])
        for value in (self.config['secrets']['database_password'], self.config['secrets']['openai_api_key'], self.config['web']['webroot']):
            self.assertNotIn(value, p._json(result).decode())
        self.assertLessEqual(len(p._json(result)), p.MAX_INPUT)

    def test_credentials_private_repr_and_not_pickleable(self):
        self.assertNotIn(self.credentials._password, repr(self.credentials))
        with self.assertRaises(TypeError): pickle.dumps(self.credentials)
        for user, password in [('root', 'x'), ('x;dsn', 'x'), ('x', ''), ('x', 'a\0b'), ('x', 'é' * 600), ('x', None)]:
            with self.subTest(user=user), self.assertRaises(p.TransportError): p.ProvisioningCredentials(user, password)

    def test_same_account_refused(self):
        with self.assertRaisesRegex(p.TransportError, 'PROVISIONING_ACCOUNT_REQUIRED'):
            p._map(self.config, p.ProvisioningCredentials('hestia', 'fixture-password'), 'fresh_database', True)

    def test_unchanged_password_and_unicode_mapping(self):
        self.config['administrator']['first_name'] = 'Élise <&>🙂'
        self.config['secrets']['admin_password'] = "  password '&é🙂  "
        result = p._map(self.config, self.credentials, 'fresh_database', True)
        self.assertEqual(result['administrator']['first_name'], 'Élise <&>🙂')
        self.assertEqual(result['administrator']['password'], "  password '&é🙂  ")

    def test_confirmation_exact_boolean(self):
        for value in (False, 0, 1, 'true', None):
            with self.subTest(value=value), self.assertRaises(p.TransportError):
                p._map(self.config, self.credentials, 'fresh_database', value)

    def test_upgrade_unknown_operation_and_remote_refused(self):
        with self.assertRaises(p.TransportError): p._map(configuration('upgrade'), self.credentials, 'fresh_database', True)
        for op in ('shell', 'upgrade', 'install', '', None):
            with self.subTest(op=op), self.assertRaises(p.TransportError): p._map(self.config, self.credentials, op, True)
        self.config['database'].update(mode='remote', host='db.example.test', tls_ca_file='/etc/hestia/ca.pem')
        with self.assertRaisesRegex(p.TransportError, 'TARGET_TRANSPORT_UNSUPPORTED'):
            p._map(self.config, self.credentials, 'fresh_database', True)

    def test_unknown_fields_and_numeric_edge_cases(self):
        for key, value in [('port', True), ('port', 0), ('port', float('nan')), ('name', 'db;host=evil'), ('host', '127.0.0.2')]:
            candidate = copy.deepcopy(self.config); candidate['database'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(p.TransportError):
                p._map(candidate, self.credentials, 'fresh_database', True)
        self.config['unexpected'] = self.credentials._password
        with self.assertRaises(p.TransportError) as caught: p._map(self.config, self.credentials, 'fresh_database', True)
        self.assertNotIn(self.credentials._password, str(caught.exception))

    def test_inspect_has_no_administrator_or_confirmation(self):
        result = p._map(self.config, self.credentials, 'inspect_database', False)
        self.assertEqual(set(result), {'version', 'operation', 'request_id', 'database'})
        self.assertNotIn(self.config['secrets']['admin_password'], p._json(result).decode())

    def test_success_is_database_only(self):
        response = p._response(0, p._json(envelope(self.request)), self.request)
        self.assertEqual(response['state'], 'DATABASE_READY')
        self.assertFalse(response['result']['application_installed'])
        self.assertFalse(response['result']['assistant_enabled'])

    def test_closed_response_and_exit_code(self):
        valid = envelope(self.request)
        for key, value in [('version', True), ('version', 2), ('request_id', '0' * 32), ('operation', 'shell'),
                           ('ok', 1), ('error', 'oops')]:
            candidate = copy.deepcopy(valid); candidate[key] = value
            with self.subTest(key=key), self.assertRaises(p.TransportError): p._response(0, p._json(candidate), self.request)
        for code in (1, 20, -9, 255):
            with self.subTest(code=code), self.assertRaises(p.TransportError): p._response(code, p._json(valid), self.request)
        for key, value in [('scope', 'INSTALLED'), ('application_installed', True), ('assistant_enabled', True),
                           ('version', self.credentials._password), ('schema_statements', True), ('schema_statements', 0),
                           ('schema_statements', 100001), ('schema_statements', 1.5), ('log', 'arbitrary')]:
            candidate = copy.deepcopy(valid); candidate['result'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(p.TransportError): p._response(0, p._json(candidate), self.request)
        valid['stdout'] = 'not allowed'
        with self.assertRaises(p.TransportError): p._response(0, p._json(valid), self.request)

    def test_malformed_json_and_duplicate_members(self):
        for data in (b'', b'null', b'[]', b'{}', b'\xff', b'{"ok":true,"ok":false}', b'{' * 100,
                     p._json(envelope(self.request)) + b'{}', b'{"x":NaN}'):
            with self.subTest(data=data[:30]), self.assertRaises(p.TransportError): p._response(0, data, self.request)

    def test_all_fixed_engine_errors(self):
        for error in p.READ_ONLY_ERRORS | p.UNCERTAIN_ERRORS:
            with self.subTest(error=error):
                result = p._response(20, p._json(envelope(self.request, error=error)), self.request)
                self.assertEqual(result['code'], error)
                self.assertEqual(result['state'], 'MANUAL_ACTION' if error in p.UNCERTAIN_ERRORS else 'REFUSED')
        for error in ('arbitrary PDO secret', '', 1, {}, ['CONNECTION_FAILED']):
            with self.subTest(error=error), self.assertRaises(p.TransportError):
                p._response(20, p._json(envelope(self.request, error=error)), self.request)

    def test_inspection_never_authorizes_retry(self):
        req = p._map(self.config, self.credentials, 'inspect_database', False)
        data = envelope(req)
        self.assertFalse(p._response(0, p._json(data), req)['result']['retry_authorized'])
        for key, value in [('empty', True), ('tables', -1), ('events', True), ('retry_authorized', True), ('application_installed', True)]:
            candidate = copy.deepcopy(data); candidate['result'][key] = value
            with self.subTest(key=key), self.assertRaises(p.TransportError): p._response(0, p._json(candidate), req)


class TransportProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hestia-transport-test-')
        self.root = Path(self.tmp.name)
        self.command = [sys.executable, '-I', '-c']

    def tearDown(self): self.tmp.cleanup()

    def exchange(self, program, wire=b'{}', timeout=2, cancel=None):
        return p._exchange(self.command + [program], wire, self.root, timeout, cancel)

    def test_real_pipes_and_closed_environment(self):
        secret = 'fixture-secret-not-in-argv'
        code, output = self.exchange("import sys,os,json; d=sys.stdin.buffer.read(); print(json.dumps([d.decode(),dict(os.environ),sys.argv]))", secret.encode())
        value, environment, args = json.loads(output)
        self.assertEqual(code, 0); self.assertEqual(value, secret)
        self.assertNotIn(secret, str(args))
        self.assertNotIn('PHPRC', environment); self.assertNotIn('PHP_INI_SCAN_DIR', environment)
        self.assertNotIn('HOME', environment); self.assertNotIn('GITHUB_TOKEN', environment)

    def test_timeout_kills_process_without_unbounded_capture(self):
        before = time.monotonic()
        with self.assertRaisesRegex(p.TransportError, 'TIMEOUT'):
            self.exchange('import time; time.sleep(30)', timeout=0.15)
        self.assertLess(time.monotonic() - before, 3)

    def test_output_and_stderr_flood_are_bounded(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream), self.assertRaisesRegex(p.TransportError, 'OUTPUT_LIMIT'):
                self.exchange(f'import sys; sys.{stream}.write("x"*1000000); sys.{stream}.flush()')

    def test_stderr_secret_is_discarded_even_on_zero_exit(self):
        with self.assertRaisesRegex(p.TransportError, 'UNEXPECTED_STDERR') as caught:
            self.exchange('import sys; sys.stderr.write(sys.stdin.read()); print("{}")', b'sensitive-fixture')
        self.assertNotIn('sensitive-fixture', repr(caught.exception))

    def test_cancellation_and_keyboard_interrupt_cleanup(self):
        cancel = threading.Event(); cancel.set()
        with self.assertRaisesRegex(p.TransportError, 'INTERRUPTED'):
            self.exchange('import time; time.sleep(30)', cancel=cancel)
        with patch.object(p.selectors.DefaultSelector, 'select', side_effect=KeyboardInterrupt):
            with self.assertRaisesRegex(p.TransportError, 'INTERRUPTED'):
                self.exchange('import time; time.sleep(30)')

    def test_nonzero_and_signal_exit_are_observed(self):
        self.assertEqual(self.exchange('import sys; sys.stdin.read(); sys.exit(23)')[0], 23)
        self.assertEqual(self.exchange('import os,signal,sys; sys.stdin.read(); os.kill(os.getpid(),signal.SIGTERM)')[0], -15)

    def test_input_limit_and_spawn_failure(self):
        with self.assertRaisesRegex(p.TransportError, 'INPUT_LIMIT'): self.exchange('pass', b'x' * (p.MAX_INPUT + 1))
        with self.assertRaisesRegex(p.TransportError, 'CHANNEL_FAILED'):
            p._exchange(['/nonexistent-executable'], b'{}', self.root, 1)

    def test_descendant_holding_pipes_is_killed(self):
        # Child and grandchild retain the isolated group; the deadline includes pipe EOF.
        code = 'import os,time; child=os.fork(); time.sleep(30) if child==0 else os._exit(0)'
        with self.assertRaisesRegex(p.TransportError, 'TIMEOUT'): self.exchange(code, timeout=0.15)


class TransportFilesystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0: raise RuntimeError('Disposable root host required; no skipped security tests')
        cls.account = 'hestiapqc' + os.urandom(3).hex()
        subprocess.run(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
                        '--shell', '/usr/sbin/nologin', cls.account], check=True, capture_output=True)
        cls.identity = pwd.getpwnam(cls.account)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['userdel', cls.account], check=True, capture_output=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hestia-php-security-')
        self.root = Path(self.tmp.name); self.root.chmod(0o711)
        self.source = self.root / 'source'; self.source.mkdir()
        self.pin = fixture_source(self.source)
        self.run = self.root / 'run'; self.run.mkdir(mode=0o711)
        self.state = self.root / 'state'
        php = Path(shutil.which('php')).resolve()
        ext = Path(subprocess.check_output([str(php), '-n', '-r', 'echo ini_get("extension_dir");'], text=True))
        self.runtime = p.PhpRuntime(php, ext, self.identity.pw_uid, self.identity.pw_gid, self.run, self.state)
        self.transport = p.PhpTransport(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=p.WEB_COMMIT)
        self.config = configuration()
        self.credentials = p.ProvisioningCredentials('hestia_setup', 'private-test-setup-password')

    def tearDown(self): self.tmp.cleanup()

    def test_pinned_source_bytes_and_no_secret_php_read(self):
        forbidden = self.source / 'includes' / 'db.php'
        forbidden.symlink_to('/nonexistent-NEVER-READ-SECRET')
        dest = self.run / 'stage'; dest.mkdir()
        with patch.object(p, 'ENGINE_SHA256', self.pin): p._bundle(self.source, dest, self.identity.pw_gid)
        self.assertFalse((dest / 'engine' / 'includes' / 'db.php').exists())
        for path in dest.rglob('*'):
            self.assertEqual(path.stat().st_uid, 0)
            self.assertEqual(path.stat().st_gid, self.identity.pw_gid)
            self.assertFalse(path.stat().st_mode & 0o022)
        test = subprocess.run(['/usr/bin/setpriv', '--reuid=' + str(self.identity.pw_uid), '--regid=' + str(self.identity.pw_gid),
                               '--clear-groups', '/usr/bin/test', '-r', str(dest / 'bridge.php')])
        self.assertEqual(test.returncode, 0)
        test = subprocess.run(['/usr/bin/setpriv', '--reuid=' + str(self.identity.pw_uid), '--regid=' + str(self.identity.pw_gid),
                               '--clear-groups', '/usr/bin/test', '-w', str(dest / 'bridge.php')])
        self.assertNotEqual(test.returncode, 0)

    def test_source_hash_tampering_rejected(self):
        dest = self.run / 'stage'; dest.mkdir()
        with self.assertRaisesRegex(p.TransportError, 'SOURCE_PIN_MISMATCH'): p._bundle(self.source, dest, self.identity.pw_gid)
        with self.assertRaises(p.TransportError): p.PhpTransport(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit='0' * 40)

    def test_link_hardlink_writable_owner_fifo_and_oversize_rejected(self):
        path = self.source / p.ENGINE_FILES[0]
        for kind in ('symlink', 'hardlink', 'writable', 'owner', 'fifo', 'large'):
            with self.subTest(kind=kind):
                original = path.read_bytes()
                if kind == 'symlink': path.unlink(); path.symlink_to(self.source / p.ENGINE_FILES[1])
                elif kind == 'hardlink': os.link(path, path.with_suffix('.link'))
                elif kind == 'writable': path.chmod(0o666)
                elif kind == 'owner': os.chown(path, self.identity.pw_uid, self.identity.pw_gid)
                elif kind == 'fifo': path.unlink(); os.mkfifo(path)
                elif kind == 'large': path.write_bytes(b'x' * (p.MAX_FILE + 1))
                with self.assertRaises(p.TransportError): p._read_file(path)
                if kind == 'hardlink': path.with_suffix('.link').unlink()
                path.unlink(); path.write_bytes(original)

    def test_directory_link_and_extra_vendor_file_rejected(self):
        vendor = self.source / 'vendor'; vendor.rename(self.source / 'original-vendor'); vendor.symlink_to(self.source / 'original-vendor')
        dest = self.run / 'stage'; dest.mkdir()
        with self.assertRaises(p.TransportError): p._bundle(self.source, dest, self.identity.pw_gid)
        vendor.unlink(); (self.source / 'original-vendor').rename(vendor)
        (vendor / 'extra.php').write_text('<?php die();')
        with patch.object(p, 'ENGINE_SHA256', self.pin), self.assertRaisesRegex(p.TransportError, 'SOURCE_PIN_MISMATCH'):
            p._bundle(self.source, dest, self.identity.pw_gid)

    def test_runtime_rejects_privileged_web_identity_bad_timeout_and_unsafe_paths(self):
        web = pwd.getpwnam('www-data')
        for runtime in (replace(self.runtime, worker_uid=0), replace(self.runtime, worker_uid=web.pw_uid, worker_gid=web.pw_gid),
                        replace(self.runtime, timeout_seconds=float('nan')), replace(self.runtime, timeout_seconds=True),
                        replace(self.runtime, timeout_seconds=181), replace(self.runtime, php=Path('/tmp/php8.4'))):
            with self.subTest(runtime=runtime), self.assertRaises(p.TransportError): p._runtime(runtime, 'www-data')
        with patch.object(p.os, 'geteuid', return_value=1000), self.assertRaises(p.TransportError): p._runtime(self.runtime, 'www-data')

    def test_command_drops_privileges_and_removes_ini(self):
        command = p._command(self.runtime, self.root)
        for expected in ('--no-new-privs', '--clear-groups', '--bounding-set=-all', '--pdeathsig=KILL', '--core=0', '-n'):
            self.assertIn(expected, command)
        self.assertNotIn(self.credentials._password, ' '.join(command))
        # Run the actual trusted privilege/resource prefix, replacing only PHP in the test.
        prefix = command[:command.index(str(self.runtime.php))]
        script = 'import os,json; print(json.dumps([os.getuid(),os.getgid(),os.getgroups(),open("/proc/self/status").read()]))'
        code, output = p._exchange(prefix + [sys.executable, '-I', '-c', script], b'{}', self.root, 3)
        uid, gid, groups, status = json.loads(output)
        self.assertEqual(code, 0); self.assertEqual(uid, self.identity.pw_uid); self.assertEqual(gid, self.identity.pw_gid)
        self.assertEqual(groups, []); self.assertIn('NoNewPrivs:\t1', status); self.assertIn('CapEff:\t0000000000000000', status)

    def invoke(self, exchange, operation='fresh_database', transport=None):
        transport = transport or self.transport
        with patch.object(p, '_runtime'), patch.object(p, 'ENGINE_SHA256', self.pin), patch.object(p, '_exchange', side_effect=exchange) as call:
            if operation == 'fresh_database': result = transport.fresh(self.config, self.credentials, confirmed=True)
            else: result = transport.inspect(self.config, self.credentials)
            return result, call.call_count

    def test_durable_interlock_precedes_dispatch_and_prevents_new_instance_replay(self):
        def exchange(command, wire, *args):
            records = list(self.state.glob('*.attempt'))
            self.assertEqual(len(records), 1)
            self.assertIn('DISPATCHING', records[0].read_text())
            self.assertEqual(records[0].stat().st_mode & 0o777, 0o600)
            return 0, p._json(envelope(json.loads(wire)))
        outcome, calls = self.invoke(exchange)
        self.assertEqual(calls, 1); self.assertEqual(outcome['state'], 'DATABASE_READY')
        for content in (repr(outcome), next(self.state.glob('*.attempt')).read_text()):
            self.assertNotIn(self.credentials._password, content); self.assertNotIn(self.config['secrets']['admin_password'], content)
        another = p.PhpTransport(self.runtime, self.source, repository=p.WEB_REPOSITORY, commit=p.WEB_COMMIT)
        with self.assertRaisesRegex(p.TransportError, 'FRESH_REPLAY_BLOCKED'): self.invoke(exchange, transport=another)
        self.assertEqual(list(self.run.iterdir()), [])

    def test_ambiguous_result_never_retries_and_observation_does_not_unlock(self):
        outcome, calls = self.invoke(p.TransportError('TIMEOUT'))
        self.assertEqual(calls, 1); self.assertEqual(outcome['state'], 'MANUAL_ACTION')
        def inspect(command, wire, *args): return 0, p._json(envelope(json.loads(wire)))
        observation, _ = self.invoke(inspect, 'inspect_database')
        self.assertFalse(observation['result']['retry_authorized'])
        with self.assertRaisesRegex(p.TransportError, 'FRESH_REPLAY_BLOCKED'): self.invoke(inspect)

    def test_malformed_response_produces_manual_not_false_success(self):
        result, calls = self.invoke(lambda *args: (0, b'{"password":"DO-NOT-ECHO"}'))
        self.assertEqual(result['state'], 'MANUAL_ACTION'); self.assertEqual(result['code'], 'PROTOCOL_REJECTED')
        self.assertNotIn('DO-NOT-ECHO', repr(result)); self.assertEqual(calls, 1)

    def test_existing_interlock_even_corrupt_cannot_be_replayed(self):
        self.invoke(p.TransportError('CHANNEL_FAILED'))
        next(self.state.glob('*.attempt')).write_text('incomplete')
        with self.assertRaisesRegex(p.TransportError, 'FRESH_REPLAY_BLOCKED'): self.invoke(lambda *args: self.fail('replayed'))

    def test_cancel_before_dispatch_has_no_attempt(self):
        cancel = threading.Event(); cancel.set()
        with patch.object(p, '_runtime'), self.assertRaisesRegex(p.TransportError, 'INTERRUPTED_BEFORE_DISPATCH'):
            self.transport.fresh(self.config, self.credentials, confirmed=True, cancel=cancel)
        self.assertFalse(self.state.exists())


class PhpBridgeWireTests(unittest.TestCase):
    def setUp(self):
        self.php = str(Path(shutil.which('php')).resolve())
        self.bridge = Path(p.__file__).parent / 'private' / 'php_bridge.php'
        self.request = p._map(configuration(), p.ProvisioningCredentials('setup', 'test-setup-password'), 'fresh_database', True)

    def run_php(self, wire):
        child = subprocess.run([self.php, '-n', '-d', 'display_errors=0', '-d', 'log_errors=0', '-f', str(self.bridge)],
                               input=wire, capture_output=True, timeout=3, env={})
        self.assertEqual(child.returncode, 20); self.assertEqual(child.stderr, b'')
        self.assertLessEqual(len(child.stdout), p.MAX_OUTPUT)
        return json.loads(child.stdout)

    def test_valid_json_roundtrip_without_driver_is_closed(self):
        result = self.run_php(p._json(self.request))
        # -n deliberately loads no mysql driver in this protocol-only test.
        self.assertEqual(result['error'], 'RUNTIME_UNAVAILABLE')
        self.assertEqual(result['request_id'], self.request['request_id'])

    def test_php_rejects_duplicate_extra_trailing_and_oversize_json(self):
        valid = p._json(self.request)
        for wire in (b'', b'null', b'[]', b'\xff', valid + b'{}', b'{' + valid[1:-1] + b',"version":1}',
                     valid[:-1] + b',"unknown":"PRIVATE-SECRET"}', b'x' * (p.MAX_INPUT + 1)):
            with self.subTest(wire=wire[:30]):
                response = self.run_php(wire)
                self.assertEqual(response['error'], 'REQUEST_INVALID'); self.assertNotIn('PRIVATE-SECRET', repr(response))

    def test_php_rejects_boolean_numbers_and_invalid_closed_fields(self):
        for section, key, value in [(None, 'version', True), (None, 'operation', 'shell'), (None, 'confirmed', 1),
                                    ('database', 'port', True), ('database', 'host', 'remote.invalid'),
                                    ('database', 'name', 'db;evil=1'), ('database', 'user', 'root'),
                                    ('administrator', 'email', ['bad'])]:
            candidate = copy.deepcopy(self.request)
            (candidate[section] if section else candidate)[key] = value
            with self.subTest(section=section, key=key):
                self.assertEqual(self.run_php(p._json(candidate))['error'], 'REQUEST_INVALID')

    def test_php_unicode_and_special_password_are_not_normalized(self):
        self.request['administrator']['first_name'] = 'Élise 🙂 <&>'
        self.request['administrator']['password'] = "  password '&é🙂  "
        response = self.run_php(p._json(self.request))
        self.assertEqual(response['error'], 'RUNTIME_UNAVAILABLE')
        self.assertNotIn(self.request['administrator']['password'], repr(response))
