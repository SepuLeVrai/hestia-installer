"""Real private journals and explicit DEV consent; native calls are fixtures."""
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

from installer import foundation_plan as fp, foundation_probe as probe
from installer.dev_target import DevTarget, digest
from installer.model import ErrorCode, InstallerError, canonical_bytes, require
from dev_fixture import target_fixture
from github_fixture import confirm
import test_foundation


class DevFoundationPlanTests(unittest.TestCase):
    def setUp(self):
        test_foundation.FoundationTests.setUp(self)
        _, target, _, _, _ = target_fixture(self.root / 'dev-fixture')
        target.value['main_configuration_sha256'] = digest(self.service.application.read()['configuration'])
        self.target = target
        self.enterContext(patch.object(DevTarget, 'serving', return_value=target))
        self.enterContext(patch.object(DevTarget, 'inspect', return_value=target))
        path = self.root / 'dev.json'; path.write_bytes(canonical_bytes(target.value)); path.chmod(0o600)
        self.selection = self.control.dev_target.register(path)
        self.dev = Mock(root=self.root / 'dev-foundation', fragment=self.root / 'dev-foundation.service',
                        unit='hestia-' + 'd' * 32 + '-foundation.service')
        self.dev.identity = self.service.gateway.identities.report()['receipt']['identities']['dev']
        self.runtime.dev = self.dev
        self.dev_staged = self.dev_started = False
        def absent(): require(not self.dev_staged, ErrorCode.MANUAL_ACTION_REQUIRED)
        def inspect(): require(self.dev_staged, ErrorCode.SOURCE_DRIFT)
        def owned(): require(self.dev_staged and self.dev_started, ErrorCode.VALIDATION_FAILED)
        def stage(): self.dev_staged = True
        def start(argv):
            if argv[-1] == self.runtime.unit: self.started = True
            elif argv[-1] == self.dev.unit: self.dev_started = True
            else: self.fail('unexpected native unit')
        self.dev.absent.side_effect = absent; self.dev.inspect.side_effect = inspect
        self.dev.owned.side_effect = owned; self.dev.stage.side_effect = stage
        self.enterContext(patch.object(fp.h, '_command', side_effect=start))
        self.enterContext(patch.object(probe, 'check_dev', return_value=probe.DEV_RESULT))

    def plan(self):
        return self.service.execute('foundation.plan', {'parents': self.parents,
            'dev_confirmation': self.selection['confirmation']})['foundation']['installation']

    def execute(self, action, document, **extra):
        return self.service.execute('foundation.' + action, {**confirm(document), **extra})['foundation']['installation']

    def test_dev_requires_explicit_selection_and_has_six_ordered_steps(self):
        document = self.plan()
        self.assertEqual(self.control.profile()['version'], 2)
        self.assertEqual([r['name'] for r in document['steps']], ['foundation.' + name for name in
            ('stage', 'start', 'verify', 'dev-stage', 'dev-start', 'dev-verify')])
        self.runtime.stage.assert_not_called(); self.dev.stage.assert_not_called()
        self.assertEqual(self.plan(), document)
        self.assertEqual(self.execute('apply', document)['state'], 'DONE')
        probe.check_dev.assert_called_once()
        for path, raw in self.saved.items(): self.assertEqual(path.read_bytes(), raw)

    def test_registered_target_does_not_implicitly_enable_dev(self):
        document = self.service.execute('foundation.plan', {'parents': self.parents})['foundation']['installation']
        self.assertEqual(len(document['steps']), 3); self.assertNotIn('dev', self.control.profile())
        with self.assertRaises(InstallerError): self.plan()
        self.dev.stage.assert_not_called()

    def test_wrong_selection_and_confirmation_never_touch_target(self):
        with self.assertRaises(InstallerError):
            self.service.execute('foundation.plan', {'parents': self.parents, 'dev_confirmation': '0' * 64})
        self.assertIsNone(self.control.profile()); self.dev.stage.assert_not_called()
        document = self.plan()
        with self.assertRaises(InstallerError): self.service.execute('foundation.apply', {**confirm(document), 'confirm': False})
        self.dev.stage.assert_not_called()

    def test_lost_dev_start_reply_recovers_without_restarting_either_context(self):
        document = self.plan(); original = fp.FoundationOperation.commit
        def lost(operation, context):
            if operation.environment == 'dev' and operation.role == 'start': raise OSError('lost reply')
            return original(operation, context)
        with patch.object(fp.FoundationOperation, 'commit', lost): result = self.execute('apply', document)
        self.assertEqual(result['state'], 'FAILED'); self.assertTrue(self.dev_started)
        with patch.object(fp.h, '_command', side_effect=AssertionError('unexpected replay')):
            result = self.execute('retry', document, name='foundation.dev-start')
            self.assertEqual(self.execute('resume', result)['state'], 'DONE')

    def test_uncertain_dev_start_is_manual_and_main_remains_untouched(self):
        document = self.plan()
        def start(argv):
            if argv[-1] == self.runtime.unit: self.started = True
            else: raise OSError('unknown start result')
        with patch.object(fp.h, '_command', side_effect=start): self.execute('apply', document)
        with patch.object(fp.h, '_command', side_effect=AssertionError('unexpected replay')):
            self.assertEqual(self.execute('retry', document, name='foundation.dev-start')['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertTrue(self.started); self.assertFalse(self.dev_started)

    def test_get_and_check_never_rewrite_committed_plan(self):
        document = self.plan(); self.assertEqual(self.execute('apply', document)['state'], 'DONE')
        before = self.control.journal.path.read_bytes()
        with patch.object(probe, 'check_dev', side_effect=AssertionError('GET probe')):
            self.assertEqual(self.service.wizard_state()['foundation']['installation']['state'], 'DONE')
        self.dev_started = False
        result = self.service.execute('foundation.check', confirm(document))['foundation']
        self.assertEqual(result['availability']['state'], 'FOUNDATION_MAIN_UNAVAILABLE')
        self.assertEqual(self.control.journal.path.read_bytes(), before)

    def test_registration_cannot_replace_a_frozen_target(self):
        self.plan(); path = self.root / 'dev.json'
        with self.assertRaises(InstallerError): self.control.dev_target.register(path)
        self.assertEqual(self.control.dev_target.state(), self.selection)


if __name__ == '__main__': unittest.main()
