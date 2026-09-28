"""Public final-plan tests: pure configuration, private files, mocked effects."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import public_tls_runtime as n, public_tls_profile as p, public_tls_plan as plan
from installer.model import InstallerError, Receipt, canonical_bytes
from installer.operations import OperationContext, SecretVault, RecoveryDecision
from installer.transaction import StateJournal
from installer.service import POST_ROUTES


def profile():
    instance = 'a' * 32
    return {'version': 1, 'boot': {'application': {'instance': instance,
        'configuration': {'web': n.boot.app.FreshProfile(instance).web('hestia.example.test')}},
        'sql': {'instance': 'b' * 32, 'packages_sha256': 'c' * 64},
        'parents': {'preparation': 'd' * 64}, 'code': {}},
        'acme': {'base': {'version': 1, 'instance': 'e' * 32, 'nginx': True}, 'base_sha256': 'f' * 64},
        'parents': {'boot': '1' * 64, 'acme': '2' * 64},
        'choices': {'email': 'operator@example.test', 'access': 'allowlist', 'networks': ['192.0.2.0/24']},
        'backend_fragment_sha256': '3' * 64, 'code': {name: n.f._sha(data) for name, data in n.boot.code_files().items()}}


class PublicProfileTests(unittest.TestCase):
    def test_closed_choices_reject_authorities_commands_noncanonical_and_ipv6(self):
        base = profile()['choices']
        for change in ({'server': 'https://other.invalid'}, {'access': 'anything'}, {'email': 'x\ncommand'},
                       {'networks': ['192.0.2.1/24']}, {'networks': ['::/0']}, {'networks': ['0.0.0.0/0']},
                       {'networks': []}, {'networks': ['192.0.2.0/24'] * 2}, {'access': 'public'}):
            with self.subTest(change=change), self.assertRaises(Exception): p.choices({**base, **change})
        self.assertEqual(p.choices({**base, 'access': 'public', 'networks': []})['access'], 'public')

    def test_separate_front_identity_and_original_backend_are_preserved(self):
        r = n.PublicTLS(profile())
        self.assertNotEqual(r.identity.user, r.layout.identity.user)
        self.assertEqual(type(r.http), n.h.HttpRuntime)
        self.assertEqual(r.http.spec.ingress.client_networks, ('127.0.0.1/32',))
        overlay = r.apache_dropin().decode()
        self.assertIn(' -c "Include ', overlay); self.assertIn('/conf/apache.conf', overlay)
        self.assertNotIn('User=', overlay); self.assertNotIn('ExecStop=', overlay)
        self.assertEqual(r.apache_include().count(b'AuthMerging Off'), 5)
        self.assertIn(b'192.0.2.0/24', r.apache_include())

    def test_http_challenge_is_accessible_and_other_requests_redirect_canonically(self):
        r = n.PublicTLS(profile()); http = r.nginx('http').decode(); https = r.nginx('https').decode()
        self.assertIn('location ^~ /.well-known/acme-challenge/', http)
        self.assertNotIn('deny all', http); self.assertIn('return 308 https://hestia.example.test$request_uri', http)
        self.assertIn('allow 192.0.2.0/24;', https); self.assertIn('deny all;', https)
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', https)
        self.assertIn('proxy_bind 127.0.0.2;', https); self.assertNotIn('real_ip', https)
        self.assertIn('ssl_protocols TLSv1.2 TLSv1.3;', https)

    def test_fixed_acme_commands_and_isolated_certbot_configuration(self):
        r = n.PublicTLS(profile()); issue = r.certbot(); dry = r.certbot(renew=True, dry_run=True); renew = r.certbot(renew=True)
        self.assertIn(p.PRODUCTION, issue); self.assertNotIn(p.PRODUCTION, dry); self.assertIn(p.STAGING, dry)
        for argv in (issue, dry, renew):
            self.assertIn('--no-directory-hooks', argv); self.assertIn(str(r.root / 'certbot.ini'), argv)
            self.assertNotIn('--nginx', argv); self.assertNotIn('--apache', argv)
            self.assertNotIn('--force-renewal', argv); self.assertNotIn('--no-verify-ssl', argv)
        self.assertIn('OnCalendar=*-*-* 00,12:00:00', r.units()[r.unit('timer')].decode())
        self.assertIn('Requires=' + r.boot.target, r.units()[r.unit('https')].decode())
        self.assertNotIn(r.boot.target, r.units()[r.unit('http')].decode())

    def test_routes_and_status_are_file_only_and_no_completion_before_done(self):
        with TemporaryDirectory(dir='/var/lib') as temporary:
            parent = SimpleNamespace(journal=StateJournal(Path(temporary) / 'state.json'))
            controller = plan.PublicTLSPlan(parent, None, None)
            with patch.object(n.PublicTLS, 'probe', side_effect=AssertionError('network')):
                value = controller.state()
            self.assertFalse(value['phase5_complete']); self.assertFalse(controller.root.exists())
        self.assertEqual({k for k in POST_ROUTES if '/public-tls/' in k},
                         {'/api/web/public-tls/' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')})

    def test_registry_is_separate_and_binds_every_choice_and_all_source(self):
        with TemporaryDirectory(dir='/var/lib') as temporary:
            journal = StateJournal(Path(temporary) / 'state.json')
            value = profile(); engine, _ = n.engine(journal, value); engine.plan(mode='fresh')
            doc = engine.report(); self.assertEqual(len(doc['steps']), 9)
            self.assertIn('hestia.example.test', str(doc['plan']))
            value = deepcopy(value); value['choices']['email'] = 'second@example.test'
            with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): n.engine(journal, value)

    def test_every_native_receipt_accounts_for_its_complete_approved_footprint(self):
        with TemporaryDirectory(dir='/var/lib') as temporary:
            engine, _ = n.engine(StateJournal(Path(temporary) / 'state.json'), profile())
            document = engine.plan(mode='fresh')
            for spec, record in list(zip(document['plan']['steps'], document['steps']))[1:]:
                operation = engine.registry.get(spec)
                engine._receipt(document, spec, record, operation.receipt())
                self.assertEqual(record['evidence']['created_resources'], ['public_tls'])

    def test_displayed_choices_are_frozen_file_only_and_exclude_the_private_bundle(self):
        with TemporaryDirectory(dir='/var/lib') as temporary:
            parent = SimpleNamespace(journal=StateJournal(Path(temporary) / 'state.json'))
            controller = plan.PublicTLSPlan(parent, None, None); value = profile()
            controller._write('profile.json', value)
            with patch.object(n.PublicTLS, 'configuration', side_effect=AssertionError('host')):
                displayed = controller.state()['configuration']
            self.assertEqual(displayed, {'hostname': 'hestia.example.test', **value['choices']})
            self.assertNotIn('code', displayed); self.assertNotIn('boot', displayed)

    def test_dns_and_foreign_listeners_rejected_without_stopping_them(self):
        r = n.PublicTLS(profile())
        with patch.object(n.socket, 'getaddrinfo', return_value=[(n.socket.AF_INET6, 1, 0, '', ('::1', 443))]), \
             patch.object(n, 'command', side_effect=AssertionError('effect')):
            with self.assertRaises(InstallerError): r.network_ready()
        with patch.object(n.socket, 'getaddrinfo', return_value=[(n.socket.AF_INET, 1, 0, '', ('192.0.2.1', 443))]), \
             patch.object(n.socket, 'socket') as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError('occupied')
            with self.assertRaises(OSError): r.network_ready()

    def test_timer_uses_timer_properties_and_binds_the_exact_renewal_service(self):
        r = n.PublicTLS(profile())
        value = {'Id': r.unit('timer'), 'FragmentPath': str(n.h.drain.UNIT_ROOT / r.unit('timer')),
            'DropInPaths': '', 'NeedDaemonReload': 'no', 'LoadState': 'loaded', 'ActiveState': 'active',
            'SubState': 'waiting', 'Job': '', 'Unit': r.unit('renew')}
        with patch.object(n, 'command', return_value=''.join(k + '=' + v + '\n' for k, v in value.items()).encode()) as command:
            self.assertTrue(r.running('timer'))
            self.assertNotIn('MainPID', ' '.join(command.call_args.args[0]))
        value['Unit'] = 'foreign.service'
        with patch.object(n, 'command', return_value=''.join(k + '=' + v + '\n' for k, v in value.items()).encode()):
            with self.assertRaises(InstallerError): r.running('timer')


class PublicRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        self.r = n.PublicTLS(profile()); self.r.root = Path(self.temp.name) / 'private'
        self.enterContext(patch.object(self.r, 'configuration'))
        self.context = lambda op: OperationContext('a' * 32, op.spec.as_dict(), op.receipt().as_dict(), SecretVault())

    def operation(self, phase): return n.PublicOperation(self.r, phase, 'web.public.stage')

    def test_complete_lost_replies_recover_readonly(self):
        for phase in ('http', 'certificate', 'dry-run', 'https', 'enable', 'verify'):
            with self.subTest(phase=phase):
                op = self.operation(phase); context = self.context(op)
                self.r._write(phase + '.attempt', self.r.binding(context)); self.r._write(phase + '.json', self.r.binding(context))
                with patch.object(self.r, 'running', return_value=True), patch.object(self.r, 'certificate'), \
                     patch.object(self.r, 'enabled'), patch.object(n, 'command', side_effect=AssertionError('effect')):
                    self.assertEqual(op.recover(context, 'apply').decision, RecoveryDecision.APPLIED)

    def test_partial_issuance_switch_and_enable_are_manual_and_never_replayed(self):
        for phase in ('certificate', 'switch', 'enable'):
            op = self.operation(phase); context = self.context(op)
            self.r._write(phase + '.attempt', self.r.binding(context))
            with patch.object(n, 'command', side_effect=AssertionError('effect')):
                self.assertEqual(op.recover(context, 'apply').decision, RecoveryDecision.MANUAL)
                self.assertEqual(op.recover(context, 'rollback').decision, RecoveryDecision.MANUAL)

    def test_partial_boot_enrollment_blocks_every_public_worker_before_effects(self):
        self.r._write('enable.attempt', {'partial': True})
        with patch.object(n, 'command', side_effect=AssertionError('effect')):
            for phase in ('http', 'https', 'backend', 'renew'):
                with self.subTest(phase=phase), self.assertRaises(InstallerError): self.r.worker(phase)

    def test_private_receipt_binding_rejects_other_profile(self):
        op = self.operation('dry-run'); value = self.r.binding(self.context(op)); value['profile_sha256'] = '0' * 64
        self.r._write('dry-run.attempt', value); self.r._write('dry-run.json', value)
        with self.assertRaises(InstallerError): self.r.completed('dry-run')

    def test_renewal_checks_material_and_configuration_before_reload(self):
        with patch.object(self.r, 'enabled'), patch.object(self.r, 'completed'), \
             patch.object(self.r, 'running', return_value=True), patch.object(self.r, 'certificate', side_effect=[None, InstallerError('VALIDATION_FAILED')]), \
             patch.object(self.r, 'systemctl') as systemctl, patch.object(n, 'command') as command:
            with self.assertRaises(InstallerError): self.r.worker('renew')
            systemctl.assert_not_called(); self.assertEqual(command.call_count, 1)

    def test_expiry_can_trigger_renewal_but_never_bypass_the_post_renewal_check(self):
        def certificate(**options):
            if certificate.old:
                self.assertTrue(options.get('allow_expired'))
                certificate.old = False
            else: self.assertFalse(options.get('allow_expired', False))
        certificate.old = True
        with patch.object(self.r, 'enabled'), patch.object(self.r, 'completed'), \
             patch.object(self.r, 'running', return_value=True), patch.object(self.r, 'certificate', side_effect=certificate), \
             patch.object(self.r, 'systemctl') as systemctl, patch.object(n, 'command') as command:
            self.r.worker('renew')
            self.assertEqual(command.call_args_list[0].args[0], self.r.certbot(renew=True))
            systemctl.assert_called_once_with('reload', 'https')
