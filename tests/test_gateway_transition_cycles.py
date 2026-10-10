"""Cockpit cycle consent and durable routing, with native checks isolated."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

from installer import gateway_transition_cycles as cycles
from installer import gateway_transition_execution as execution
from installer import gateway_public_generation as g
from installer import gateway_public_selection as selection
from installer.gateway_transition import FCM_COMMIT
from installer.model import canonical_bytes
import test_gateway_public_cockpit as fixture


class TransitionCycleTests(unittest.TestCase):
    def setUp(self):
        fixture.PublicCockpitTests.setUp(self)
        self.root = self.control.root
        self.control.last_error = self.control.availability = None
        source = g.mobile.MobileBootRuntime(self.value['public_source']['mobile']).gateway.profile
        self.selection['assessment'] = g.assess(source, target_commit=FCM_COMMIT, direction='upgrade').report()
        self.value['transition_sha256'] = execution.digest(self.selection)
        fixture.PublicCockpitTests.replace_profile(self, self.value)
        fixture.PublicCockpitTests.test_seven_stages_are_hash_linked_including_transfer_and_opening(self)
        self.control.parent = Mock(); self.control.parent.journal.locked.return_value = MagicMock()
        service = SimpleNamespace(gateway=SimpleNamespace(root=self.root / 'gateway'), parent=self.control.parent)
        self.control.transition.service = service
        self.control.backup = SimpleNamespace(activation=SimpleNamespace(parent=self.control.parent, application=Mock()))
        self.control.mobile_boot = Mock()
        self.enterContext(patch.object(self.control, 'binding', return_value=self.value))
        self.enterContext(patch.object(self.control, 'acquired'))
        self.enterContext(patch.object(self.control, 'engine', return_value=Mock(report=lambda: {'state': 'DONE'})))
        self.native = Mock(); self.factory = self.enterContext(patch.object(execution, 'NativeTransition', return_value=self.native))
        value = g.selection(self.value['public_source']['mobile']['shared'], self.value['public_source']['mobile'],
            FCM_COMMIT, 'upgrade', '9' * 64, self.value['lease_id'])
        self.generation = g.Generation(value)
        self.control._write('public-generation.json', value)
        activation = {'generation_sha256': self.generation.digest, 'fragment_plan_sha256': 'a' * 64, 'admission_sha256': 'b' * 64}
        records = {'activated.json': activation,
            'opening-completed.json': {'owner': activation, 'plan_sha256': 'c' * 64, 'receipts_sha256': 'd' * 64}}
        # Only the prior native seals are fixtures. Consent, closed parsing,
        # immutable journal writes, stage hashes and path routing are real.
        self.enterContext(patch.object(g.Generation, '_read', side_effect=lambda name: deepcopy(records[name])))
        self.enterContext(patch.object(cycles, 'pointer', return_value=selection.binding(self.generation, 'a' * 64)))
        self.payload = {'confirmation': execution.digest(self.value), 'confirm': True}

    def test_explicit_check_precedes_new_journal_and_keeps_all_previous_files(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        def check(): self.assertFalse((self.root / 'next.json').exists()); return {'state': 'verified'}
        self.native.check.side_effect = check
        child = cycles.open_next(self.control, self.payload)
        self.assertEqual(child.transition.cycle['source_binding'], self.generation.value['target_binding'])
        self.assertEqual(child.source_package, self.control.root / 'binary/package.zip')
        self.assertNotEqual(child.backup.root, self.control.backup.activation.parent.journal.path)
        self.assertEqual(child.state()['state'], 'NOT_PLANNED')
        self.assertEqual(child.transition.state()['source_commit'], FCM_COMMIT)
        for path, raw in before.items(): self.assertEqual(path.read_bytes(), raw)
        self.native.execute.assert_not_called()

    def test_wrong_consent_or_extra_destination_never_checks_or_writes(self):
        for change in ({'confirm': 1}, {'confirmation': 'f' * 64}, {'backup_root': '/foreign'}):
            with self.assertRaises(Exception): cycles.open_next(self.control, {**self.payload, **change})
        self.factory.assert_not_called(); self.assertFalse((self.root / 'next.json').exists())

    def test_failed_current_native_check_never_opens_new_cycle(self):
        self.native.check.side_effect = RuntimeError('public unavailable')
        with self.assertRaises(Exception): cycles.open_next(self.control, self.payload)
        self.assertFalse((self.root / 'next.json').exists())

    def test_restarted_reader_follows_link_without_repeating_native_check(self):
        child = cycles.open_next(self.control, self.payload); self.factory.reset_mock()
        with patch('subprocess.run', side_effect=AssertionError('reader effect')):
            restored = cycles.selected(self.control)
            self.assertEqual(restored.root, child.root)
            self.assertEqual(restored.backup.root, child.backup.root)
            self.assertEqual(restored.transition.cycle, child.transition.cycle)
        self.factory.assert_not_called()

    def test_completed_link_cannot_change_source_or_previous_confirmation(self):
        cycles.open_next(self.control, self.payload)
        value = self.control._read('next.json')
        for mutate in (lambda v: v.update(execution_sha256='f' * 64),
                       lambda v: v.update(source_binding=self.selection['assessment']['source']),
                       lambda v: v['predecessor'].update(completion_sha256='e' * 64)):
            changed = deepcopy(value); mutate(changed)
            path = self.root / 'next.json'; path.write_bytes(canonical_bytes(changed))
            with self.assertRaises(Exception): cycles.selected(self.control)

    def test_generation_backup_roots_are_distinct_and_determined_by_pinned_source(self):
        child = cycles.open_next(self.control, self.payload)
        first = child.backup.backups({'instance': self.value['instance']})
        second = child.backup.backups({'instance': self.value['instance'], 'source_generation': child.transition.cycle})
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, 'gateway-backup-' + self.generation.digest)


if __name__ == '__main__': unittest.main()
