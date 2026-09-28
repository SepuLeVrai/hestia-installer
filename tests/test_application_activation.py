"""File-only activation contracts; host/process effects are mocked here."""
from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installer import application_activation as activation, application_plan as app
from installer.model import InstallerError, Receipt, aggregate, build_plan, initial_document
from installer.operations import OperationContext, SecretVault
from github_fixture import confirm
import test_application_plan as plans


class ActivationPlanTests(unittest.TestCase):
    setUp = plans.ApplicationPlanTests.setUp
    save = plans.ApplicationPlanTests.save
    plan = plans.ApplicationPlanTests.plan
    restart = plans.ApplicationPlanTests.restart

    def completed(self):
        document = self.plan()
        document.update(state='DONE', approved_plan_sha256=document['plan_sha256'], revision=1)
        for spec, record in zip(document['plan']['steps'], document['steps']):
            record.update(state='DONE', phase='done', attempts=1, evidence=Receipt(created_resources=tuple(
                r['name'] for r in spec['resources'] if not r['preexisting'])).as_dict())
        document.update(aggregate(document))
        with self.service.engine.journal.locked() as locked: locked.write(document, expected_revision=0)
        return document

    def activation_plan(self):
        parent = self.completed()
        with patch.object(app.h.HttpRuntime, 'observe'), patch.object(app.cleaner.SessionCleaner, 'observe'):
            result = self.service.execute('activation.plan', {'preparation_sha256': parent['plan_sha256']})['activation']['installation']
        return parent, result

    def test_get_and_restart_do_not_create_activation_or_probe_host(self):
        self.plan(); before = self.service.engine.journal.path.read_bytes()
        with patch.object(activation.Activation, 'configuration', side_effect=AssertionError('host')), patch.object(activation.Activation, 'check', side_effect=AssertionError('HTTP')):
            self.assertEqual(self.restart().wizard_state()['activation'], {'installation': None, 'availability': None})
        self.assertFalse(self.service.activation.journal.path.parent.exists())
        self.assertEqual(before, self.service.engine.journal.path.read_bytes())

    def test_unfinished_preparation_and_wrong_parent_cannot_plan(self):
        parent = self.plan()
        with self.assertRaises(InstallerError): self.service.execute('activation.plan', {'preparation_sha256': parent['plan_sha256']})
        self.assertFalse(self.service.activation.journal.path.exists())

    def test_completed_preparation_stays_byte_identical_and_activation_has_separate_consent(self):
        parent, document = self.activation_plan()
        self.assertEqual(self.service.engine.report(), parent)
        self.assertEqual(len(document['plan']['steps']), 5)
        self.assertIsNone(document['approved_plan_sha256'])
        self.assertTrue(all(parent['plan_sha256'] in s['warnings'][0] for s in document['plan']['steps']))
        self.assertEqual(self.restart().activation.state()['installation'], document)
        with self.assertRaises(InstallerError): self.service.execute('activation.apply', {'confirmation': parent['plan_sha256'], 'confirm': True})

    def test_repeated_plan_is_read_only_and_arbitrary_targets_are_refused(self):
        parent, document = self.activation_plan()
        with patch.object(app.h.HttpRuntime, 'observe', side_effect=AssertionError('new observation')):
            self.assertEqual(self.service.execute('activation.plan', {'preparation_sha256': parent['plan_sha256']})['activation']['installation'], document)
        for payload in ({'preparation_sha256': '0' * 64}, {'preparation_sha256': parent['plan_sha256'], 'unit': 'apache2.service'}):
            with self.assertRaises(InstallerError): self.service.execute('activation.plan', payload)

    def test_truncated_even_valid_activation_plan_is_rejected(self):
        parent, _ = self.activation_plan()
        engine, _ = self.service.activation.engine(parent)
        replacement = initial_document(build_plan(engine.registry.specs()[:1], mode='fresh'))
        self.service.activation.journal.path.write_bytes(activation.canonical_bytes(replacement))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.service.activation.engine(parent)

    def test_explicit_availability_failure_does_not_change_historical_done(self):
        parent, document = self.activation_plan()
        document.update(state='DONE', approved_plan_sha256=document['plan_sha256'], revision=1)
        for record in document['steps']: record.update(state='DONE', phase='done', attempts=1)
        with self.service.activation.journal.locked() as locked: locked.write(document, expected_revision=0)
        with patch.object(activation.Activation, 'check', side_effect=OSError('private diagnostic')):
            value = self.service.execute('activation.check', confirm(document))['activation']
        self.assertEqual(value['availability']['state'], 'LOCAL_WEB_UNAVAILABLE')
        self.assertEqual(value['installation'], document)
        self.assertIsNone(self.restart().activation.state()['availability'])


class ActivationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='hestia-activation-', dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        profile = app.FreshProfile('a' * 32)
        config = {'web': profile.web('hestia.example.test')}
        self.activation = activation.Activation(profile.http(config), 'b' * 64)
        self.activation.directory = Path(self.temp.name)
        self.operation = activation.ActivationOperation(self.activation, 'php')
        self.context = OperationContext('test-installation', self.operation.spec.as_dict(), Receipt().as_dict(), SecretVault())

    def test_intent_is_private_and_bound_to_exact_installation_and_spec(self):
        self.assertFalse(self.operation.intent(self.context)); self.operation.intent(self.context, create=True)
        path = self.activation.directory / 'activation-php.json'; self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        other = OperationContext('other-installation', self.context.spec, self.context.evidence, self.context.secrets)
        with self.assertRaises(InstallerError): self.operation.intent(other)
        path.chmod(0o644)
        with self.assertRaises(Exception): self.operation.intent(self.context)

    def test_intent_links_and_partial_documents_cannot_authorize_recovery(self):
        self.operation.intent(self.context, create=True); path = self.activation.directory / 'activation-php.json'
        raw = path.read_bytes(); target = path.with_name('outside'); target.write_bytes(raw); target.chmod(0o600)
        for mode in ('symlink', 'hardlink', 'partial'):
            path.unlink()
            if mode == 'symlink': path.symlink_to(target)
            elif mode == 'hardlink': os.link(target, path)
            else: path.write_bytes(raw[:20]); path.chmod(0o600)
            with self.subTest(mode=mode), self.assertRaises(Exception): self.operation.intent(self.context)
        self.assertEqual(target.read_bytes(), raw)

    def test_lost_start_reply_is_observed_without_starting_again(self):
        self.operation.intent(self.context, create=True)
        with patch.object(self.activation, 'serving'), patch.object(self.activation, 'running', return_value=True), patch.object(activation.h, '_command', side_effect=AssertionError('start')):
            result = self.operation.recover(self.context, 'apply')
        self.assertEqual(result.decision, activation.RecoveryDecision.APPLIED)

    def test_intent_with_stopped_service_is_manual_not_blind_replay(self):
        self.operation.intent(self.context, create=True)
        with patch.object(self.activation, 'configuration'), patch.object(self.activation, 'serving'), patch.object(self.activation, 'running', return_value=False), patch.object(activation.h, '_command', side_effect=AssertionError('start')):
            self.assertEqual(self.operation.recover(self.context, 'apply').decision, activation.RecoveryDecision.MANUAL)

    def test_no_intent_and_stopped_service_is_retry_safe_without_effect(self):
        with patch.object(self.activation, 'configuration'), patch.object(self.activation, 'serving'), patch.object(self.activation, 'running', return_value=False), patch.object(activation.h, '_command', side_effect=AssertionError('start')):
            self.assertEqual(self.operation.recover(self.context, 'apply').decision, activation.RecoveryDecision.RETRY_SAFE)

    def test_start_is_checkpointed_by_private_intent_before_command(self):
        states = iter((False, True))
        def command(argv):
            self.assertTrue(self.operation.intent(self.context)); self.assertEqual(argv[-2:], ['--', self.activation.unit('php')])
        with patch.object(self.activation, 'configuration'), patch.object(self.activation, 'serving'), patch.object(self.activation, 'running', side_effect=lambda role: next(states)), patch.object(activation.h, '_command', side_effect=command):
            self.assertEqual(self.operation.apply(self.context), self.operation.receipt(self.context))

    def test_http_probe_uses_fixed_loopback_and_does_not_follow_redirect(self):
        with patch.object(self.activation, 'serving'), patch.object(self.activation, 'running', return_value=True), patch.object(activation.http.client, 'HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302; response.read.return_value = b'name="csrf_token" value="aa"'; response.getheader.return_value = 'text/html'
            with self.assertRaises(InstallerError): self.activation.check()
            connection.assert_called_once_with('127.0.0.1', 9080, timeout=5, source_address=('127.0.0.2', 0))
            connection.return_value.close.assert_called_once()
