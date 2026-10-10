"""Successive public compilation and immutable selection ancestry contracts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import gateway_public_generation as g, gateway_public_selection as s
from installer import gateway_public_ancestry as ancestry
from installer import gateway_transition_cycles as cycles
from installer.gateway_transition import LEGACY_COMMIT, FCM_COMMIT
from installer.model import canonical_bytes
from installer.package_plan import PackagePlan
from test_gateway_public_generation import selected


def descendant(previous, *, lease='f' * 32, direction='rollback'):
    reference = {'lease_id': previous.value['lease_id'], 'generation_sha256': previous.digest,
        'publication_sha256': previous.value['publication_sha256'], 'fragment_plan_sha256': 'a' * 64,
        'completion_sha256': 'b' * 64, 'activation_sha256': 'c' * 64,
        'admission_sha256': 'd' * 64, 'consumed_sha256': 'e' * 64}
    return g.Generation(g.selection(previous.value['shared'], previous.value['mobile'],
        LEGACY_COMMIT if direction == 'rollback' else FCM_COMMIT, direction, '9' * 64, lease,
        predecessor=reference, source_binding=previous.value['target_binding']))


class PublicCyclesTests(unittest.TestCase):
    def setUp(self): self.first = g.Generation(selected())

    def test_three_generations_compile_exact_previous_fragments_without_prefix_growth(self):
        with patch('subprocess.run', side_effect=AssertionError('native')):
            second = descendant(self.first); third = descendant(second, lease='8' * 32, direction='upgrade')
            self.assertEqual(second.source_units(), self.first.units())
            self.assertEqual(third.source_units(), second.units())
        self.assertEqual(third.value['mobile'], self.first.value['mobile'])
        self.assertEqual(third.value['shared'], self.first.value['shared'])
        self.assertEqual(third.value['target_binding'], self.first.value['target_binding'])
        for name, value in third.units().items():
            self.assertEqual(value.count(b'Description=Generation '), int(b'Description=' in third.original_units()[name]))
        self.assertEqual(len({x.root for x in (self.first, second, third)}), 3)

    def test_second_generation_source_and_previous_lease_are_closed(self):
        value = descendant(self.first).value
        for mutate in (lambda v: v.update(source_binding=self.first.source_profile.binding()),
                       lambda v: v['predecessor'].update(lease_id=v['lease_id']),
                       lambda v: v['predecessor'].update(command='restart'),
                       lambda v: v['predecessor'].update(generation_sha256='../foreign'),
                       lambda v: v.update(version=True)):
            changed = deepcopy(value); mutate(changed)
            with self.assertRaises(Exception): g.Generation(changed)

    def test_source_units_and_references_bind_previous_target_without_adopting_enrollment(self):
        second = descendant(self.first)
        self.assertEqual(second.references()['source_gateway'], g.sha(self.first.value['target_binding']))
        self.assertEqual(second.original.gateway.profile.binding(), self.first.original.gateway.profile.binding())
        self.assertNotEqual(second.references()['source_gateway'], g.sha(second.original.gateway.profile.binding()))
        self.assertEqual(second.readers()[2].profile, self.first.readers()[2].profile)
        self.assertNotEqual(second.readers()[2].epoch.root, self.first.readers()[2].epoch.root)

    def test_explicit_configuration_observation_cannot_bypass_worker_publication(self):
        generation = descendant(self.first)
        with patch.object(generation, 'configuration', side_effect=RuntimeError('current publication changed')) as check:
            with self.assertRaisesRegex(RuntimeError, 'publication changed'): generation.worker('mobile')
        check.assert_called_once_with()

    def test_sealed_predecessor_requires_exact_activation_and_completion_grammar(self):
        activation = {'generation_sha256': self.first.digest, 'fragment_plan_sha256': 'a' * 64, 'admission_sha256': 'b' * 64}
        completion = {'owner': activation, 'plan_sha256': 'c' * 64, 'receipts_sha256': 'd' * 64}
        records = {'activated.json': activation, 'opening-completed.json': completion}
        with patch.object(self.first, '_read', side_effect=lambda name: records[name]), \
             patch.object(ancestry, 'admission_seal', return_value={'admission_sha256': 'b' * 64, 'consumed_sha256': 'e' * 64}):
            reference = ancestry.binding(self.first, s.binding(self.first, 'a' * 64))
            self.assertEqual(reference['completion_sha256'], g.sha(completion))
            completion['extra'] = True
            with self.assertRaises(Exception): ancestry.binding(self.first, s.binding(self.first, 'a' * 64))


class PublicPointerChainTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-pointer-chain-', dir='/var/lib'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.shared = g.Generation(selected()).original.shared
        self.shared.root = self.root
        first = g.Generation(selected()); second = descendant(first); third = descendant(second, lease='8' * 32, direction='upgrade')
        self.pointers = [s.binding(v, str(i) * 64) for i, v in enumerate((first, second, third), 1)]
        PackagePlan._write(self.shared, s.NAME, self.pointers[0])
        (self.root / s.CHAIN).mkdir(mode=0o700)
        self.edges = []
        for old, new in zip(self.pointers, self.pointers[1:]):
            path = self.root / s.CHAIN / (old['generation_sha256'] + '.json')
            path.write_bytes(canonical_bytes(new)); path.chmod(0o600); self.edges.append(path)

    def test_read_only_pointer_walk_keeps_all_history(self):
        before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in self.root.rglob('*.json')}
        with patch('subprocess.run', side_effect=AssertionError('native')):
            self.assertEqual(s.pointer(self.shared), self.pointers[-1])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in before})

    def test_changed_ancestry_and_repeated_lease_are_refused(self):
        original = self.edges[0].read_bytes()
        for change in ({'previous_generation_sha256': 'a' * 64}, {'lease_id': self.pointers[0]['lease_id']},
                       {'shared_profile_sha256': 'b' * 64}, {'version': 1}):
            self.edges[0].write_bytes(canonical_bytes({**self.pointers[1], **change}))
            with self.assertRaises(Exception): s.pointer(self.shared)
        self.edges[0].write_bytes(original)

    def test_torn_null_linked_and_writable_edges_never_choose_old_workers(self):
        import os
        path = self.edges[0]; raw = path.read_bytes()
        for content in (b'null', b'{', b'{}'):
            path.write_bytes(content)
            with self.assertRaises(Exception): s.pointer(self.shared)
        path.write_bytes(raw); path.chmod(0o644)
        with self.assertRaises(Exception): s.pointer(self.shared)
        path.chmod(0o600); os.link(path, path.with_suffix('.alias'))
        with self.assertRaises(Exception): s.pointer(self.shared)

    def test_orphan_chain_cannot_fall_back_to_original_shared_profile(self):
        (self.root / s.NAME).unlink()
        with self.assertRaises(Exception): s.pointer(self.shared)

    def test_null_cockpit_successor_is_not_an_unplanned_cycle(self):
        controller = SimpleNamespace(root=self.root)
        controller._read = lambda name: PackagePlan._read(controller, name)
        (self.root / 'next.json').write_bytes(b'null'); (self.root / 'next.json').chmod(0o600)
        with self.assertRaises(Exception): cycles.selected(controller)


class HistoricalAdmissionSealTests(unittest.TestCase):
    """Private receipt integrity only; no old receipt grants native authority."""
    def setUp(self):
        from installer import gateway_resume_authority as a
        temp = TemporaryDirectory(prefix='hestia-admission-seal-', dir='/var/lib'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        value = selected()
        publication = {'source_manifest': {'binding': 'source-fixture'}, 'target_manifest': {'binding': 'target-fixture'}}
        value['publication_sha256'] = g.sha(publication)
        self.generation = g.Generation(value); self.generation.root = self.root / 'public'
        self.generation.original.gateway = SimpleNamespace(root=self.root / 'gateway')
        self.slot = self.root / 'gateway/control' / ('resume-' + value['lease_id'])
        reader = SimpleNamespace(root=self.slot)
        self.plan = {'lease_id': value['lease_id'], 'publication': publication}
        owner = {'handoff': a.binding_for(canonical_bytes(self.plan)), 'activation': {'fixture': 'previous-native-owner'}}
        for name, record in (('plan.json', self.plan), ('activation-owner.json', owner), ('consumed.json', owner)):
            PackagePlan._write(reader, name, record)
        self.activation = {'generation_sha256': self.generation.digest,
            'fragment_plan_sha256': 'a' * 64, 'admission_sha256': g.sha(self.plan)}
        PackagePlan._write(self.generation, 'activated.json', self.activation)
        PackagePlan._write(self.generation, 'opening-completed.json', {'owner': self.activation,
            'plan_sha256': 'b' * 64, 'receipts_sha256': 'c' * 64})

    def test_consumed_plan_and_owner_are_pinned_without_native_observation(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*.json')}
        with patch('subprocess.run', side_effect=AssertionError('native')):
            reference = ancestry.binding(self.generation, {'fragment_plan_sha256': 'a' * 64})
        self.assertEqual(reference['admission_sha256'], g.sha(self.plan))
        self.assertEqual(reference['consumed_sha256'], g.boot.f._sha((self.slot / 'consumed.json').read_bytes()))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_missing_consumption_cannot_be_replaced_by_public_opening_seals(self):
        (self.slot / 'consumed.json').unlink()
        with self.assertRaises(Exception): ancestry.binding(self.generation, {'fragment_plan_sha256': 'a' * 64})
        self.assertTrue((self.generation.root / 'opening-completed.json').exists())

    def test_changed_plan_consumption_or_owner_is_refused(self):
        for name in ('plan.json', 'consumed.json', 'activation-owner.json'):
            path = self.slot / name; raw = path.read_bytes()
            path.write_bytes(canonical_bytes({'foreign': 'receipt'}))
            with self.assertRaises(Exception): ancestry.admission_seal(self.generation, self.activation)
            path.write_bytes(raw)

    def test_receipt_links_and_broad_permissions_are_refused(self):
        import os
        path = self.slot / 'consumed.json'; path.chmod(0o644)
        with self.assertRaises(Exception): ancestry.admission_seal(self.generation, self.activation)
        path.chmod(0o600); os.link(path, self.slot / 'alias.json')
        with self.assertRaises(Exception): ancestry.admission_seal(self.generation, self.activation)


if __name__ == '__main__': unittest.main()
