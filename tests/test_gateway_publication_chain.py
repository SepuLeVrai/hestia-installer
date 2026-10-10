"""Real private publication files and binary inodes, without native service mocks."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from installer import gateway_publication_chain as chain
from installer.model import canonical_bytes
import test_gateway_active_profile as readers

a = chain.a


class PublicationChainTests(unittest.TestCase):
    setUp = readers.ActiveReaderTests.setUp
    write = staticmethod(readers.ActiveReaderTests.write)
    publish_value = readers.ActiveReaderTests.publish_value
    selected = readers.ActiveReaderTests.selected

    def append(self, previous, lease='c' * 32):
        parent = a.sha(canonical_bytes(previous))
        target = self.original if previous['direction'] == 'upgrade' else self.runtime
        payload = b'fixture-source' if previous['direction'] == 'upgrade' else self.payload
        binary = target.profile.binary
        binary.rename(binary.with_name('old-' + lease))
        binary.write_bytes(payload); binary.chmod(0o750)
        value = deepcopy(previous)
        value.update(version=2, previous_publication_sha256=parent,
            direction='rollback' if previous['direction'] == 'upgrade' else 'upgrade',
            source_manifest=previous['target_manifest'], target_manifest=target.manifest(target.account()))
        origin = {**value['cutover'], 'lease_id': lease, 'source_manifest': value['source_manifest'],
                  'original_binary': previous['target_armed']['replacement']}
        owner = {'intent_sha256': a.sha(canonical_bytes(origin))}
        arm = {'owner': owner, 'role': 'target', 'previous': origin['original_binary'],
               'replacement': a.c._identity(binary.stat(), payload)}
        done = {'owner': owner, 'armed_sha256': a.sha(canonical_bytes(arm)), 'role': 'target'}
        value.update(cutover=origin, target_armed=arm, target_done=done)
        slot = target.root / 'control' / ('cutover-' + lease); slot.mkdir(mode=0o700)
        self.write(slot / 'target.armed.json', arm); self.write(slot / 'target.done.json', done)
        self.save_edge(parent, value)
        return value

    def save_edge(self, parent, value):
        path = chain.slot(self.runtime, parent); path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.write(path / a.INTENT, value)
        receipt = path / a.ACTIVE; receipt.write_bytes(a._receipt(canonical_bytes(value))); receipt.chmod(0o600)

    def test_upgrade_rollback_upgrade_keeps_enrollment_and_every_ancestor(self):
        before = {p: p.read_bytes() for p in self.runtime.root.rglob('*.json')}
        second = self.append(self.value)
        third = self.append(second, 'd' * 32)
        with patch('subprocess.run', side_effect=AssertionError('native effect')):
            self.assertEqual(chain.history(self.runtime), list(map(canonical_bytes, (self.value, second, third))))
            current = self.selected()
        self.assertEqual(current.profile.binding(), third['target_manifest']['binding'])
        self.assertEqual(current._enrolled_manifest, self.enrollment)
        for path, raw in before.items(): self.assertEqual(path.read_bytes(), raw)

    def test_cached_reader_is_invalid_after_next_generation(self):
        cached = self.selected(); self.append(self.value)
        with self.assertRaises(a.c.g.GatewayStateError): a.verify(cached)

    def test_partial_child_is_closed_but_explicit_parent_can_be_recovered(self):
        source = chain.source(self.runtime, a.sha(canonical_bytes(self.value)))
        path = chain.slot(self.runtime, chain.parent(source)); path.mkdir(parents=True, mode=0o700)
        path.parent.chmod(0o700)
        with self.assertRaises(a.c.g.GatewayStateError): self.selected()
        self.assertEqual(chain.parent(source), a.sha(canonical_bytes(self.value)))
        with self.assertRaises(a.c.g.GatewayStateError): chain.require_unpublished(source)

    def test_pinned_source_uses_current_profile_and_original_enrollment(self):
        parent = a.sha(canonical_bytes(self.value))
        source = chain.source(self.runtime, parent)
        chain.require_unpublished(source)
        self.assertFalse(hasattr(source, '_active_profile'))
        self.assertEqual(source._enrolled_manifest, self.enrollment)
        with self.assertRaises(a.c.g.GatewayStateError): chain.source(self.original, parent)

    def test_new_child_cannot_reuse_lease_or_change_sqlite_identity(self):
        second = self.append(self.value); parent = second['previous_publication_sha256']
        for mutation in (lambda v: v['cutover'].update(lease_id='b' * 32),
                         lambda v: v['state']['gateway.db'].update(inode=v['state']['gateway.db']['inode'] + 1)):
            damaged = deepcopy(second); mutation(damaged); self.save_edge(parent, damaged)
            with self.assertRaises(a.c.g.GatewayStateError): chain.history(self.runtime)

    def test_parent_hash_source_manifest_and_binary_lineage_are_bound(self):
        second = self.append(self.value); parent = second['previous_publication_sha256']
        for mutation in (lambda v: v.update(previous_publication_sha256='f' * 64),
                         lambda v: v['source_manifest'].update(uid=12),
                         lambda v: v['cutover']['original_binary'].update(inode=987654)):
            damaged = deepcopy(second); mutation(damaged); self.save_edge(parent, damaged)
            with self.assertRaises(a.c.g.GatewayStateError): chain.history(self.runtime)

    def test_missing_receipt_or_foreign_child_record_never_falls_back(self):
        second = self.append(self.value); root = chain.slot(self.runtime, second['previous_publication_sha256'])
        (root / a.ACTIVE).unlink()
        with self.assertRaises(a.c.g.GatewayStateError): self.selected()
        self.save_edge(second['previous_publication_sha256'], second)
        self.write(root / 'foreign.json', {})
        with self.assertRaises(a.c.g.GatewayStateError): self.selected()

    def test_generation_limit_and_missing_parent_fail_closed(self):
        second = self.append(self.value); self.append(second, 'd' * 32)
        with patch.object(chain, 'MAX_GENERATIONS', 2):
            with self.assertRaises(a.c.g.GatewayStateError): self.selected()
        with self.assertRaises(a.c.g.GatewayStateError): chain.history(self.runtime, through='f' * 64)


if __name__ == '__main__': unittest.main()
