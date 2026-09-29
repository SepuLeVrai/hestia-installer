"""Controller boundaries and genuine P-256 proofs; host adapters are doubled here."""
import base64
import copy
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from installer import foundation_plan as fp, foundation_probe as probe, foundation_runtime as native
from installer import foundation_drain as fd
from installer.gateway_identity import _SPKI, _b64
from installer.model import ErrorCode, InstallerError, Receipt, aggregate, canonical_bytes, require
from installer.operations import OperationContext, RecoveryDecision
from github_fixture import confirm
import test_gateway_acquisition
import test_gateway_http


class FoundationTests(unittest.TestCase):
    def setUp(self):
        test_gateway_acquisition.GatewayPlanTests.setUp(self)
        self.payload['acquisition'] = 'package'
        gateway = self.service.execute('gateway.plan', self.payload)['gateway']['preparation']
        self.service.import_gateway_package(gateway['plan_sha256'], io.BytesIO(self.responses.package), len(self.responses.package))
        engine, active = self.service.activation.engine(self.parent)
        doc = engine.plan(); doc.update(state='DONE', approved_plan_sha256=doc['plan_sha256'], revision=1)
        for spec, row in zip(doc['plan']['steps'], doc['steps']):
            row.update(state='DONE', phase='done', attempts=1, evidence=Receipt().as_dict())
        doc.update(aggregate(doc))
        with engine.journal.locked() as locked: locked.write(doc, expected_revision=0)
        self.control = self.service.foundation
        self.runtime = Mock(root=self.root / 'foundation', fragment=self.root / 'foundation.service',
                            unit='hestia-' + 'a' * 32 + '-foundation.service')
        self.runtime.identity = self.service.gateway.identities.report()['receipt']['identities']['main']
        self.staged = self.started = False
        def absent(): require(not self.staged, ErrorCode.MANUAL_ACTION_REQUIRED)
        def inspect(): require(self.staged, ErrorCode.SOURCE_DRIFT)
        def owned(): require(self.staged and self.started, ErrorCode.VALIDATION_FAILED); return True
        def stage(): self.staged = True
        def start(argv): self.assertEqual(argv[-1], self.runtime.unit); self.started = True
        self.runtime.absent.side_effect = absent; self.runtime.inspect.side_effect = inspect
        self.runtime.owned.side_effect = owned; self.runtime.stage.side_effect = stage
        for item in (patch.object(fp, 'FoundationRuntime', return_value=self.runtime),
                     patch.object(fp.h, '_command', side_effect=start), patch.object(probe, 'check', return_value=probe.RESULT)):
            item.start(); self.addCleanup(item.stop)
        self.parents = {'web': self.parent['plan_sha256'], 'activation': doc['plan_sha256'], 'gateway': gateway['plan_sha256']}
        self.saved = {p: p.read_bytes() for p in [self.service.engine.journal.path, engine.journal.path,
            self.service.gateway.journal.path, *self.service.gateway.identities.root.glob('*.pem')]}

    def plan(self): return self.service.execute('foundation.plan', {'parents': self.parents})['foundation']['installation']
    def execute(self, action, document, **extra):
        return self.service.execute('foundation.' + action, {**confirm(document), **extra})['foundation']['installation']

    def test_separate_plan_preserves_parents_keys_and_explicit_availability(self):
        doc = self.plan(); self.assertEqual(self.plan(), doc); self.runtime.stage.assert_not_called()
        self.assertEqual(self.execute('apply', doc)['state'], 'DONE')
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)
        self.assertIsNone(self.control.state()['availability'])
        state = self.service.execute('foundation.check', confirm(doc))['foundation']
        self.assertEqual(state['availability']['state'], 'FOUNDATION_MAIN_VERIFIED')
        self.assertFalse(state['gateway_service_available']); self.assertFalse(state['boot_enabled'])

    def test_closed_payload_wrong_confirmation_and_parent_mismatch_before_effects(self):
        for payload in ({'parents': {**self.parents, 'web': '0' * 64}}, {'parents': self.parents, 'port': 9080}):
            with self.assertRaises(InstallerError): self.service.execute('foundation.plan', payload)
        self.assertFalse(self.control.root.exists()); doc = self.plan()
        for payload in ({**confirm(doc), 'confirm': False}, confirm(self.parent), {**confirm(doc), 'endpoint': 'http://evil.invalid'}):
            with self.assertRaises(InstallerError): self.service.execute('foundation.apply', payload)
        self.runtime.stage.assert_not_called(); probe.check.assert_not_called()

    def test_get_reads_only_metadata_even_after_private_material_damage(self):
        doc = self.plan(); self.execute('apply', doc)
        (self.service.gateway.identities.root / 'main.pem').unlink()
        with (patch.object(native.FoundationRuntime, 'show', side_effect=AssertionError('GET native')),
              patch('installer.gateway_identity._openssl', side_effect=AssertionError('GET key'))):
            self.assertEqual(self.service.wizard_state()['foundation']['installation']['state'], 'DONE')
            self.assertEqual(self.service.report()['foundation']['installation']['state'], 'DONE')
        calls = probe.check.call_count
        with self.assertRaises(InstallerError): self.service.execute('foundation.check', confirm(doc))
        self.assertEqual(probe.check.call_count, calls)

    def test_foreign_resource_refused_and_damaged_gateway_blocks_native_effects(self):
        doc = self.plan(); self.runtime.absent.side_effect = InstallerError(ErrorCode.MANUAL_ACTION_REQUIRED)
        self.assertEqual(self.execute('apply', doc)['state'], 'FAILED'); self.runtime.stage.assert_not_called()
        (self.service.gateway.root / 'binary/package.zip').write_bytes(b'damaged')
        with self.assertRaises(InstallerError): self.execute('retry', doc, name='foundation.stage')
        self.runtime.stage.assert_not_called()

    def test_lost_start_reply_recovers_owned_process_without_second_start(self):
        doc = self.plan(); original = fp.FoundationOperation.commit
        def crash(operation, context):
            if operation.role == 'start': raise OSError('lost reply')
            return original(operation, context)
        with patch.object(fp.FoundationOperation, 'commit', crash): failed = self.execute('apply', doc)
        self.assertEqual(failed['steps'][1]['phase'], 'commit'); self.assertTrue(self.started)
        with patch.object(fp.h, '_command', side_effect=AssertionError('restart')):
            result = self.execute('retry', doc, name='foundation.start')
            result = self.execute('resume', result)
        self.assertEqual(result['state'], 'DONE')

    def test_intent_without_owned_process_is_manual_without_restart(self):
        doc = self.plan()
        with patch.object(fp.h, '_command', side_effect=OSError('lost before start')):
            failed = self.execute('apply', doc)
        self.assertEqual(failed['steps'][1]['phase'], 'apply')
        with patch.object(fp.h, '_command', side_effect=AssertionError('blind restart')):
            result = self.execute('retry', doc, name='foundation.start')
        self.assertEqual(result['state'], 'MANUAL_ACTION_REQUIRED')

    def test_verified_receipt_recovery_never_replays_probe(self):
        doc = self.plan(); original = fp.FoundationOperation.commit
        def crash(operation, context):
            if operation.role == 'verify': raise OSError('lost reply')
            return original(operation, context)
        with patch.object(fp.FoundationOperation, 'commit', crash): self.execute('apply', doc)
        with patch.object(probe, 'check', side_effect=AssertionError('replayed proof')):
            self.assertEqual(self.execute('retry', doc, name='foundation.verify')['state'], 'DONE')

    def test_current_failure_does_not_rewrite_done_or_restart(self):
        doc = self.plan(); self.execute('apply', doc); before = self.control.journal.path.read_bytes()
        self.started = False
        result = self.service.execute('foundation.check', confirm(doc))['foundation']
        self.assertEqual(result['availability']['state'], 'FOUNDATION_MAIN_UNAVAILABLE')
        self.assertEqual(before, self.control.journal.path.read_bytes()); self.assertFalse(self.started)

    def test_real_signature_exact_claims_and_private_key_unchanged(self):
        store = self.service.gateway.identities; identity = self.runtime.identity
        body, raw, token = probe.assertion(store, identity)
        header, claims, signature = [base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)) for s in token.split('.')]
        self.assertEqual(json.loads(header), {'alg': 'ES256', 'typ': 'hestia-service+jwt', 'kid': identity['kid']})
        claim = json.loads(claims); self.assertEqual(claim['body_sha256'], fp.sha(raw)); self.assertEqual(claim['exp'] - claim['iat'], 30)
        self.assertEqual(claim['aud'], 'hestia-internal-mobile:main'); self.assertEqual(claim['htu_path'], probe.PATH)
        self.assertEqual(claim['request_id'], body['request_id']); self.assertEqual(len(signature), 64)
        def integer(data):
            data = data.lstrip(b'\0') or b'\0'
            if data[0] >= 128: data = b'\0' + data
            return b'\x02' + bytes([len(data)]) + data
        sequence = integer(signature[:32]) + integer(signature[32:]); der = b'\x30' + bytes([len(sequence)]) + sequence
        sig = self.root / 'probe.der'; sig.write_bytes(der)
        jwk = identity['public_jwk']; public = self.root / 'public.der'
        public.write_bytes(_SPKI + base64.urlsafe_b64decode(jwk['x'] + '=') + base64.urlsafe_b64decode(jwk['y'] + '='))
        result = subprocess.run(['/usr/bin/openssl', 'dgst', '-sha256', '-keyform', 'DER', '-verify', str(public), '-signature', str(sig)],
                                input=token.rsplit('.', 1)[0].encode(), capture_output=True)
        self.assertEqual(result.returncode, 0)
        for path, saved in self.saved.items(): self.assertEqual(path.read_bytes(), saved)
        with self.assertRaises(InstallerError): probe.assertion(store, store.report()['receipt']['identities']['dev'])


class FoundationProbeContract(unittest.TestCase):
    def test_missing_authorization_is_distinct_from_malformed_jws_and_fail_open(self):
        # Pinned Web returns invalid_request/400 for malformed JWS, but an
        # absent credential is authentication_failed/401. Exercise real HTTP.
        import http.client
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        body = {'request_id': 'r', 'mobile_subject_uuid': 's', 'device_id': 'd', 'environment': 'main'}
        raw = canonical_bytes(body); token = 'fixture.signed.assertion'; seen = []; fail_open = False
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                auth = self.headers.get('Authorization'); seen.append(auth)
                status = 401; value = {'error': {'code': 'authentication_failed'}}
                if len(seen) == 1 or (auth is None and fail_open):
                    status = 200; value = {'request_id': body['request_id'], 'data': {**body,
                        'mobile_enabled': True, 'account_active': False, 'device_active': False, 'environment_allowed': False}}
                elif auth is not None and auth != 'Bearer ' + token:
                    status = 400; value = {'error': {'code': 'invalid_request'}}
                encoded = canonical_bytes(value)
                self.send_response(status); self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(encoded)))
                self.end_headers(); self.wfile.write(encoded)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        connection = http.client.HTTPConnection
        def local(host, port, **kwargs):
            self.assertEqual((host, port), ('127.0.0.1', 9082))
            return connection(host, server.server_port, **kwargs)
        try:
            with patch.object(probe, 'assertion', return_value=(body, raw, token)), patch.object(probe.http.client, 'HTTPConnection', side_effect=local):
                self.assertEqual(probe.check(None, None), probe.RESULT)
                self.assertEqual(seen, ['Bearer ' + token, 'Bearer ' + token, None])
                seen.clear(); fail_open = True
                with self.assertRaises(InstallerError): probe.check(None, None)
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()


class FoundationNativeBoundaries(unittest.TestCase):
    def test_systemd_absence_exit_one_cannot_authorize_failed_loaded_unit(self):
        runtime = object.__new__(native.FoundationRuntime); runtime.unit = 'hestia-' + 'a' * 32 + '-foundation.service'
        value = {k: '' for k in native.PROPERTIES}; value['LoadState'] = 'not-found'
        def result(): return Mock(returncode=1, stdout=''.join(k + '=' + v + '\n' for k, v in value.items()).encode())
        with patch.object(native.h.p, '_safe_path'), patch.object(native.subprocess, 'run', side_effect=lambda *a, **kw: result()):
            self.assertEqual(runtime.show()['LoadState'], 'not-found')
            value['LoadState'] = 'loaded'
            with self.assertRaises(InstallerError): runtime.show()

    def test_noncanonical_zero_negative_oversized_and_trailing_der_rejected(self):
        good = bytes.fromhex('3006020101020102'); self.assertEqual(len(probe.raw_signature(good)), 64)
        for bad in (b'', good + b'x', bytes.fromhex('3006020100020102'), bytes.fromhex('3006020181020102'),
                    bytes.fromhex('300702020001020102'), bytes.fromhex('308106020101020102'), b'0' * 73):
            with self.subTest(bad=bad), self.assertRaises(InstallerError): probe.raw_signature(bad)

    def test_closed_template_and_shared_guarded_socket_without_private_keys(self):
        from installer.application_plan import FreshProfile
        from installer.application_activation import Activation
        from installer.gateway_identity import public_identity
        # Valid public NIST P-256 generator, no fixture private key.
        jwk = {'kty': 'EC', 'crv': 'P-256', 'x': _b64(bytes.fromhex('6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296')),
               'y': _b64(bytes.fromhex('4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5'))}
        profile = FreshProfile('a' * 32)
        http = profile.http({'web': {'hostname': 'web.example', 'webroot': str(profile.webroot)}})
        runtime = native.FoundationRuntime(Activation(http, 'b' * 64), public_identity('main', jwk))
        files = runtime.files(2000); apache = files[runtime.root / 'apache.conf']; unit = files[runtime.fragment]
        self.assertIn(b'Listen 127.0.0.1:9082', apache); self.assertNotIn(b'Listen 127.0.0.1:9080', apache)
        self.assertIn(str(http.spec.root / 'run/php.sock').encode(), apache)
        self.assertNotIn(b'PRIVATE KEY', b''.join(files.values())); self.assertNotIn(b'[Install]', unit)
        self.assertNotIn(b'Requires=', unit); self.assertIn(b'ProtectSystem=strict', unit)
        self.assertIn(b'RewriteCond %{QUERY_STRING} !^$', apache); self.assertNotIn(b'${', apache)
        self.assertEqual(json.loads(files[runtime.root / 'main.json'])['environment'], 'main')

    def test_foreign_ipv4_ipv6_listener_refused_before_bind(self):
        for row in [('tcp', '0100007F:237A', '4'), ('tcp', '00000000:237A', '4'), ('tcp6', '0' * 32 + ':237A', '4')]:
            with patch.object(native, 'listeners', return_value=[row]), patch.object(native.socket, 'socket') as socket:
                with self.assertRaises(InstallerError): native.free_port()
                socket.assert_not_called()


class FoundationDrainTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory(dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'control').mkdir(mode=0o700)
        self.runtime = Mock(root=self.root, unit='hestia-' + 'a' * 32 + '-foundation.service')
        self.runtime.web.spec.instance = 'a' * 32
        self.runtime.web.spec.maintenance_directory = self.root / 'maintenance'
        self.lease = Mock(lease_id='b' * 32, assert_held=Mock())
        self.lease.scope.instance = self.runtime.web.spec.instance
        self.lease.scope.directory = self.runtime.web.spec.maintenance_directory
        self.value = {'ActiveState': 'active', 'MainPID': '123'}
        self.runtime.inspect.side_effect = lambda: dict(self.value)
        self.runtime.host.return_value.pw_gid = 1234
        self.runtime.manifest.return_value = {'fixture': True}
        self.records = fd.Records(self.runtime)
        def command(argv):
            self.assertEqual(argv[-3:], ['stop', '--', self.runtime.unit])
            self.assertIsNotNone(self.records._read('quiesce-' + self.lease.lease_id + '.json'))
            self.lease.assert_held.assert_called()
            self.value.update(ActiveState='inactive', MainPID='0')
        self.command = patch.object(fd.h, '_command', side_effect=command); self.command.start(); self.addCleanup(self.command.stop)

    def test_owned_stop_intent_precedes_effect_and_no_implicit_restart(self):
        fd.quiesce(self.runtime, self.lease)
        self.runtime.owned.assert_called_with(serving=False)
        self.assertIsNotNone(self.records._read('quiet-' + self.lease.lease_id + '.json'))
        with patch.object(fd.h, '_command', side_effect=AssertionError('repeated effect')):
            fd.quiesce(self.runtime, self.lease)

    def test_lost_stop_reply_reconciles_inactive_service(self):
        original = fd.Records._write
        def fail(store, name, value):
            if name.startswith('quiet-'): raise OSError('lost reply')
            return original(store, name, value)
        with patch.object(fd.Records, '_write', fail), self.assertRaises(OSError): fd.quiesce(self.runtime, self.lease)
        with patch.object(fd.h, '_command', side_effect=AssertionError('repeated stop')): fd.quiesce(self.runtime, self.lease)
        self.assertEqual(self.value['MainPID'], '0')

    def test_changed_pid_after_lost_stop_is_not_adopted(self):
        with patch.object(fd.h, '_command', side_effect=OSError('before stop')), self.assertRaises(OSError): fd.quiesce(self.runtime, self.lease)
        self.value['MainPID'] = '456'
        with patch.object(fd.h, '_command', side_effect=AssertionError('foreign stop')), self.assertRaises(InstallerError): fd.quiesce(self.runtime, self.lease)

    def test_wrong_lease_or_unit_damage_never_stops(self):
        self.lease.scope.instance = 'c' * 32
        with self.assertRaises(InstallerError): fd.quiesce(self.runtime, self.lease)
        self.lease.scope.instance = 'a' * 32
        self.runtime.inspect.side_effect = InstallerError(ErrorCode.SOURCE_DRIFT)
        with patch.object(fd.h, '_command', side_effect=AssertionError('drift stop')), self.assertRaises(InstallerError): fd.quiesce(self.runtime, self.lease)
        self.assertFalse(list((self.root / 'control').iterdir()))

    def test_legacy_absence_has_no_new_probe_or_filesystem_mutation(self):
        http = Mock(); http.spec.root = self.root / 'legacy/http'
        self.assertIsNone(fd.attached(http)); http._inspect_configuration.assert_not_called()
        self.assertFalse((self.root / 'legacy').exists())


class FoundationHTTPTests(unittest.TestCase):
    setUp = test_gateway_http.GatewayHTTPTests.setUp
    tearDown = test_gateway_http.GatewayHTTPTests.tearDown
    _connection = test_gateway_http.GatewayHTTPTests._connection
    _unlock = test_gateway_http.GatewayHTTPTests._unlock
    login = test_gateway_http.GatewayHTTPTests.login
    request = test_gateway_http.GatewayHTTPTests.request

    def test_every_route_requires_session_csrf_and_origin(self):
        routes = ['/api/gateway/foundation/' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')]
        for route in routes: self.assertEqual(self.request('POST', route, {})[0], 401)
        self.login()
        for route in routes:
            for headers in ({'X-Hestia-CSRF': ''}, {'Origin': 'https://evil.invalid'}):
                self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
        self.assertFalse(self.service.foundation.root.exists())
