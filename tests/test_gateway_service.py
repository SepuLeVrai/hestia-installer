"""Consent, recovery, probe and maintenance boundaries for the native Gateway."""
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from installer import gateway_service_plan as plan, gateway_service_probe as probe
from installer import gateway_service_drain as drain
from installer.gateway_service_profile import GatewayServiceProfile
from installer.model import ErrorCode, InstallerError, canonical_bytes, require
from installer.operations import OperationContext, RecoveryDecision
from github_fixture import confirm
import test_foundation


class GatewayServiceTests(unittest.TestCase):
    def setUp(self):
        test_foundation.FoundationTests.setUp(self)
        foundation = test_foundation.FoundationTests.plan(self)
        self.assertEqual(test_foundation.FoundationTests.execute(self, 'apply', foundation)['state'], 'DONE')
        self.parents['foundation'] = foundation['plan_sha256']
        self.saved[self.control.journal.path] = self.control.journal.path.read_bytes()
        self.control = self.service.gateway_service
        self.runtime = Mock(root=self.root / 'gateway-service', fragment=self.root / 'gateway.service',
                            unit='hestia-' + 'a' * 32 + '-gateway.service')
        self.runtime.profile.account.directory = self.root / 'gateway-account'
        self.runtime.profile.account.user = 'hst-' + 'b' * 24
        self.runtime.profile.binding.return_value = {'closed': 'fixture-binding'}
        self.runtime.profile.identity = self.service.gateway.profile()['identity']
        self.created = self.staged = self.started = False
        def preflight(): require(not self.staged, ErrorCode.MANUAL_ACTION_REQUIRED)
        def prepare(): require(not self.created, ErrorCode.MANUAL_ACTION_REQUIRED)
        def create(**kwargs): self.created = True
        def observe(): require(self.created, ErrorCode.SOURCE_DRIFT)
        def stage(package): self.staged = True
        def inspect(): require(self.staged, ErrorCode.SOURCE_DRIFT)
        def owned(**kwargs): require(self.staged and self.started, ErrorCode.VALIDATION_FAILED); return True
        def stopped(): require(self.staged and not self.started, ErrorCode.MANUAL_ACTION_REQUIRED)
        def command(argv): self.assertEqual(argv[-1], self.runtime.unit); self.started = True
        self.runtime.preflight.side_effect = preflight; self.runtime.profile.account.prepare.side_effect = prepare
        self.runtime.profile.account.create.side_effect = create; self.runtime.profile.account.observe.side_effect = observe
        self.runtime.inspect.side_effect = inspect; self.runtime.stage.side_effect = stage
        self.runtime.owned.side_effect = owned; self.runtime.stopped.side_effect = stopped
        self.runtime.state_binding.return_value = {'gateway.db': {'device': 1, 'inode': 2}, 'gateway.lock': {'device': 1, 'inode': 3}}
        for item in (patch.object(plan, 'GatewayServiceRuntime', return_value=self.runtime),
                     patch.object(plan.h, '_command', side_effect=command), patch.object(probe, 'check', return_value=probe.RESULT)):
            item.start(); self.addCleanup(item.stop)

    def plan(self): return self.service.execute('gateway-service.plan', {'parents': self.parents})['gateway_service']['installation']
    def execute(self, action, doc, **extra):
        return self.service.execute('gateway-service.' + action, {**confirm(doc), **extra})['gateway_service']['installation']

    def test_separate_consent_and_all_parent_bytes_preserved(self):
        doc = self.plan(); self.assertEqual(self.plan(), doc); self.assertFalse(self.created)
        self.assertEqual(self.execute('apply', doc)['state'], 'DONE')
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)
        self.assertIsNone(self.control.state()['availability'])
        report = self.service.execute('gateway-service.check', confirm(doc))['gateway_service']
        self.assertEqual(report['availability']['state'], 'GATEWAY_MAIN_VERIFIED')
        self.assertFalse(report['public_mobile_available']); self.assertFalse(report['boot_enabled'])

    def test_wrong_parent_arbitrary_target_and_wrong_consent_never_touch_host(self):
        for payload in ({'parents': {**self.parents, 'foundation': '0' * 64}}, {'parents': self.parents, 'port': 9080}):
            with self.assertRaises(InstallerError): self.service.execute('gateway-service.plan', payload)
        self.assertFalse(self.control.root.exists()); doc = self.plan()
        for payload in ({**confirm(doc), 'confirm': False}, confirm(self.parent), {**confirm(doc), 'command': 'restart'}):
            with self.assertRaises(InstallerError): self.service.execute('gateway-service.apply', payload)
        self.runtime.preflight.assert_not_called(); self.assertFalse(self.created)

    def test_occupied_port_or_unit_refused_before_account_creation(self):
        doc = self.plan(); self.runtime.preflight.side_effect = InstallerError(ErrorCode.MANUAL_ACTION_REQUIRED)
        self.assertEqual(self.execute('apply', doc)['state'], 'FAILED')
        self.runtime.profile.account.create.assert_not_called(); self.runtime.stage.assert_not_called()

    def test_damaged_package_refuses_every_native_effect(self):
        doc = self.plan(); (self.service.gateway.root / 'binary/package.zip').write_bytes(b'damaged')
        with self.assertRaises(InstallerError): self.execute('apply', doc)
        self.runtime.preflight.assert_not_called(); self.assertFalse(self.created)

    def test_lost_reply_recovers_same_live_state_without_start_or_probe(self):
        doc = self.plan(); self.assertEqual(self.execute('apply', doc)['state'], 'DONE')
        engine, _ = self.control.engine(self.parent); completed = engine.report()
        for spec, row in zip(completed['plan']['steps'], completed['steps']):
            context = OperationContext(completed['installation_id'], spec, row['evidence'], engine.secrets)
            with patch.object(plan.h, '_command', side_effect=AssertionError('second start')), patch.object(probe, 'check', side_effect=AssertionError('second probe')):
                self.assertEqual(engine.registry.get(spec).recover(context, 'apply').decision, RecoveryDecision.COMMITTED)

    def test_start_intent_with_missing_receipt_or_changed_inode_is_manual(self):
        doc = self.plan(); self.execute('apply', doc); engine, _ = self.control.engine(self.parent)
        completed = engine.report(); spec, row = completed['plan']['steps'][2], completed['steps'][2]
        context = OperationContext(completed['installation_id'], spec, row['evidence'], engine.secrets)
        operation = engine.registry.get(spec)
        self.runtime.state_binding.return_value['gateway.db']['inode'] = 4
        self.assertEqual(operation.recover(context, 'apply').decision, RecoveryDecision.MANUAL)
        (self.control.root / 'started.json').unlink()
        self.assertEqual(operation.recover(context, 'apply').decision, RecoveryDecision.MANUAL)
        self.started = False
        self.assertEqual(operation.recover(context, 'apply').decision, RecoveryDecision.MANUAL)

    def test_check_unavailable_preserves_done_and_never_restarts(self):
        doc = self.plan(); self.execute('apply', doc); original = self.control.journal.path.read_bytes()
        self.started = False
        with patch.object(plan.h, '_command', side_effect=AssertionError('restart')):
            report = self.service.execute('gateway-service.check', confirm(doc))['gateway_service']
        self.assertEqual(report['availability']['state'], 'GATEWAY_MAIN_UNAVAILABLE')
        self.assertEqual(original, self.control.journal.path.read_bytes())

    def test_get_is_metadata_only_even_when_key_is_missing(self):
        doc = self.plan(); self.execute('apply', doc)
        (self.service.gateway.identities.root / 'main.pem').unlink()
        with patch('installer.gateway_identity._openssl', side_effect=AssertionError('GET key')):
            self.runtime.inspect.side_effect = AssertionError('GET systemd')
            self.assertEqual(self.service.wizard_state()['gateway_service']['installation']['state'], 'DONE')
            self.assertEqual(self.service.report()['gateway_service']['installation']['state'], 'DONE')


class GatewayServiceProfileTests(unittest.TestCase):
    def test_distinct_names_never_allow_reused_web_uid_or_gid(self):
        from installer import gateway_service_runtime as native
        runtime = object.__new__(native.GatewayServiceRuntime)
        runtime.profile = Mock(dev=None); runtime.web = Mock()
        runtime.web.spec.service_user = 'web-fixture'
        runtime.profile.account.account.return_value = Mock(pw_uid=901, pw_gid=902)
        with patch.object(native.h, '_identity') as identity:
            for uid, gid in ((901, 904), (903, 902), (901, 902)):
                identity.return_value = Mock(pw_uid=uid, pw_gid=gid)
                with self.subTest(uid=uid, gid=gid), self.assertRaises(InstallerError): runtime.account()
            identity.return_value = Mock(pw_uid=903, pw_gid=904)
            self.assertEqual(runtime.account().pw_uid, 901)
            identity.assert_called_with('web-fixture')

    def profile(self, directory='/var/lib/installer/gateway/identities'):
        from installer.application_plan import FreshProfile
        from installer.application_activation import Activation
        from installer.foundation_runtime import FoundationRuntime
        from installer.gateway_identity import _b64, public_identity
        jwk = {'kty': 'EC', 'crv': 'P-256', 'x': _b64(bytes.fromhex('6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296')),
               'y': _b64(bytes.fromhex('4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5'))}
        fresh = FreshProfile('a' * 32); http = fresh.http({'web': {'hostname': 'web.example', 'webroot': str(fresh.webroot)}})
        foundation = FoundationRuntime(Activation(http, 'b' * 64), public_identity('main', jwk))
        return GatewayServiceProfile(foundation, {'version': 1, 'instance': 'c' * 32,
            'public_origin': 'https://mobile.customer.example', 'dev_enabled': True}, Path(directory))

    def test_closed_main_9083_credential_and_distinct_locked_account(self):
        profile = self.profile(); value = profile.configuration(); unit = profile.unit_bytes().decode()
        self.assertEqual(value['listen'], '127.0.0.1:9083'); self.assertEqual(set(value['contexts']), {'mode', 'main'})
        self.assertEqual(value['contexts']['main']['endpoint'], 'http://127.0.0.1:9082')
        self.assertEqual(value['contexts']['main']['key_file'], '/run/credentials/' + profile.unit + '/main-key')
        self.assertNotEqual(profile.account.user, profile.web.spec.service_user)
        self.assertIn('LoadCredential=main-key:/var/lib/installer/gateway/identities/main.pem\n', unit)
        self.assertIn('Restart=no\n', unit); self.assertIn('ConditionPathExists=!', unit)
        self.assertNotIn('[Install]', unit); self.assertNotIn('--migrate', unit); self.assertNotIn('Environment=', unit)
        self.assertNotIn('PRIVATE KEY', canonical_bytes(profile.binding()).decode())

    def test_systemd_interpolation_and_unsafe_paths_refused_before_render(self):
        for directory in ('relative/path', '/var/lib/bad%H/key', '/var/lib/bad\nExecStart=/bin/sh', '/var/lib/a/../keys', '/var/lib/space here', '/' + 'x' * 241):
            with self.subTest(directory=directory), self.assertRaises(InstallerError): self.profile(directory)


class GatewayServiceProbeTests(unittest.TestCase):
    def test_real_http_signed_result_and_closed_origin_forwarding_contract(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import http.client
        import threading
        import uuid
        seen = []; fail_open = False
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def send(self, status, value, identifier):
                raw = canonical_bytes(value); self.send_response(status)
                for key, val in {'Content-Type': 'application/json', 'Cache-Control': 'no-store',
                                 'X-Request-ID': identifier, 'Content-Length': str(len(raw))}.items(): self.send_header(key, val)
                self.end_headers(); self.wfile.write(raw)
            def do_GET(self): self.send(200, {'status': 'ok'}, str(uuid.uuid4()))
            def do_POST(self):
                identifier = str(uuid.uuid4()); body = self.rfile.read(int(self.headers['Content-Length'])); seen.append(body)
                status = 200; reply = {'data': {'state': 'invalid', 'expires_at': 0}, 'request_id': identifier}
                if not fail_open and (self.headers['Origin'] == 'null' or self.headers.get('Forwarded')):
                    status = 400 if self.headers['Origin'] == 'null' else 404
                    reply = {'error': {'code': 'invalid_request' if status == 400 else 'not_found'}, 'request_id': identifier}
                self.send(status, reply, identifier)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        connection = http.client.HTTPConnection
        def local(host, port, **kwargs):
            self.assertEqual((host, port), ('127.0.0.1', 9083)); return connection(host, server.server_port, **kwargs)
        try:
            with patch.object(probe.http.client, 'HTTPConnection', side_effect=local):
                self.assertEqual(probe.check('https://mobile.customer.example'), probe.RESULT)
                self.assertEqual(len(seen), 3); self.assertEqual(len(set(seen)), 1)
                fail_open = True
                with self.assertRaises(InstallerError): probe.check('https://mobile.customer.example')
        finally: server.shutdown(); thread.join(timeout=5); server.server_close()

    def test_health_alone_redirect_auth_error_or_ready_never_proves_connection(self):
        identifier = '00000000-0000-4000-8000-000000000001'
        for status, data in ((302, {}), (503, {'error': {'code': 'backend_unavailable'}}),
                (200, {'data': {'state': 'ready', 'expires_at': 1}, 'request_id': identifier}),
                (200, {'data': {'state': 'invalid', 'expires_at': False}, 'request_id': identifier})):
            with self.subTest(status=status, data=data), patch.object(probe, 'request', side_effect=[
                (200, {'status': 'ok'}, identifier), (status, data, identifier)]), self.assertRaises(InstallerError):
                probe.check('https://mobile.customer.example')


class GatewayServiceDrainTests(unittest.TestCase):
    def test_absent_gateway_has_no_host_probe_and_orphan_gateway_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            http = Mock(); http.spec.root = Path(directory) / 'http'
            with patch.object(drain, 'GatewayServiceRuntime', side_effect=AssertionError('unexpected host')):
                self.assertIsNone(drain.attached(http, None))
                (Path(directory) / 'gateway-service').mkdir()
                with self.assertRaises(InstallerError): drain.attached(http, None)

    def test_stop_lost_reply_recovery_is_same_pid_only_and_gate_first(self):
        runtime = Mock(unit='hestia-' + 'a' * 32 + '-gateway.service')
        runtime.web.spec.instance = 'a' * 32; runtime.web.spec.maintenance_directory = Path('/var/lib/maintenance')
        lease = Mock(lease_id='b' * 32); lease.scope = Mock(instance=runtime.web.spec.instance, directory=runtime.web.spec.maintenance_directory)
        values = {}; records = Mock(); records._read.side_effect = lambda name: copy.deepcopy(values.get(name))
        records._write.side_effect = lambda name, value: values.setdefault(name, copy.deepcopy(value))
        state = {'ActiveState': 'active', 'MainPID': '123'}; runtime.inspect.side_effect = lambda: dict(state)
        events = []; lease.assert_held = Mock(side_effect=lambda: events.append('gate'))
        runtime.owned.side_effect = lambda **kwargs: events.append('owned')
        def stop(argv):
            events.append('stop'); self.assertEqual(argv[-1], runtime.unit)
            state.update(ActiveState='inactive', MainPID='0'); raise RuntimeError('lost stop reply')
        with patch.object(drain.foundation_drain, 'Records', return_value=records), patch.object(drain, 'binding', return_value={'owned': True}), patch.object(drain.h, '_command', side_effect=stop) as command:
            with self.assertRaises(RuntimeError): drain.quiesce(runtime, lease)
            self.assertEqual(events[:3], ['gate', 'owned', 'gate'])
            drain.quiesce(runtime, lease); self.assertEqual(command.call_count, 1)
            state.update(ActiveState='active', MainPID='124')
            with self.assertRaises(InstallerError): drain.quiesce(runtime, lease)
            self.assertEqual(command.call_count, 1)


class GatewayServiceHTTPTests(unittest.TestCase):
    setUp = test_foundation.FoundationHTTPTests.setUp
    tearDown = test_foundation.FoundationHTTPTests.tearDown
    _connection = test_foundation.FoundationHTTPTests._connection
    _unlock = test_foundation.FoundationHTTPTests._unlock
    login = test_foundation.FoundationHTTPTests.login
    request = test_foundation.FoundationHTTPTests.request

    def test_every_route_requires_session_csrf_and_origin(self):
        routes = ['/api/gateway/service/' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')]
        for route in routes: self.assertEqual(self.request('POST', route, {})[0], 401)
        self.login()
        for route in routes:
            for headers in ({'X-Hestia-CSRF': ''}, {'Origin': 'https://evil.invalid'}):
                self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
        self.assertFalse(self.service.gateway_service.root.exists())
