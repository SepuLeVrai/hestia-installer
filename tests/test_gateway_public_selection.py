"""Selection contracts with real private pointer files, not native boot proof."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import gateway_public_selection as s
from installer.model import InstallerError, canonical_bytes
from installer.gateway_state_fence import GatewayStateError
from test_gateway_public_generation import selected


class PublicSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-public-selection-', dir='/var/lib')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.generation = s.g.Generation(selected())
        self.shared = self.generation.original.shared
        self.shared.root = self.root / 'shared'
        self.shared.root.mkdir(mode=0o700)
        self.shared.layout = SimpleNamespace(root=self.root)
        self.generation.root = self.root / ('public-successor-' + self.generation.value['lease_id'])
        self.generation.root.mkdir(mode=0o700)
        self.write(self.generation.root / 'profile.json', self.generation.value)
        self.value = s.binding(self.generation, 'a' * 64)
        self.path = self.shared.root / s.NAME
        self.configuration = self.enterContext(patch.object(self.generation, 'configuration'))
        self.fragments = self.enterContext(patch.object(self.generation, 'installed_fragments'))
        # Only the constructor's fixed host path is relocated. Pointer parsing,
        # private-file reading and all selection comparisons are real.
        constructor = s.g.Generation
        def relocated(value):
            candidate = constructor(value)
            self.assertEqual(candidate.value, self.generation.value)
            return self.generation
        class Relocated(constructor):
            def __new__(cls, value): return relocated(value)
        self.enterContext(patch.object(s.g, 'Generation', Relocated))
        self.enterContext(patch('subprocess.run', side_effect=AssertionError('native effect')))

    @staticmethod
    def write(path, value):
        path.write_bytes(canonical_bytes(value)); path.chmod(0o600)

    def test_absent_pointer_is_the_only_legacy_selection(self):
        self.assertIsNone(s.selected(self.shared))
        self.configuration.assert_not_called(); self.fragments.assert_not_called()

    def test_valid_selection_checks_configuration_and_fragment_identity_without_activation(self):
        self.write(self.path, self.value)
        before = (self.path.stat().st_ino, self.path.stat().st_mtime_ns)
        self.assertIs(s.selected(self.shared), self.generation)
        self.configuration.assert_called_once_with()
        self.fragments.assert_called_once_with('a' * 64)
        self.assertEqual(before, (self.path.stat().st_ino, self.path.stat().st_mtime_ns))
        self.assertFalse((self.generation.root / 'activated.json').exists())

    def test_corrupt_empty_and_dangling_pointer_never_fall_back(self):
        for raw in (b'', b'{', b'null', b'{}'):
            self.path.write_bytes(raw); self.path.chmod(0o600)
            with self.assertRaises(Exception): s.selected(self.shared)
        self.path.unlink(); self.path.symlink_to(self.root / 'missing')
        with self.assertRaises(Exception): s.selected(self.shared)

    def test_closed_schema_and_all_parent_hashes_are_enforced(self):
        for key, value in (('version', True), ('policy', 'foreign'), ('lease_id', '../escape'),
                           ('generation_sha256', 'b' * 64), ('shared_profile_sha256', 'b' * 64),
                           ('fragment_plan_sha256', 'bad'), ('command', 'start')):
            selected_value = deepcopy(self.value); selected_value[key] = value
            self.write(self.path, selected_value)
            with self.assertRaises((InstallerError, GatewayStateError)): s.selected(self.shared)

    def test_missing_generation_and_failed_fragment_proof_never_fall_back(self):
        self.write(self.path, self.value)
        path = self.generation.root / 'profile.json'; raw = path.read_bytes(); path.unlink()
        with self.assertRaises((InstallerError, GatewayStateError)): s.selected(self.shared)
        path.write_bytes(raw); path.chmod(0o600)
        self.fragments.side_effect = InstallerError(s.g.ErrorCode.SOURCE_DRIFT)
        with self.assertRaises((InstallerError, GatewayStateError)): s.selected(self.shared)

    def test_writable_and_hardlinked_pointer_is_rejected(self):
        import os
        self.write(self.path, self.value); self.path.chmod(0o666)
        with self.assertRaises((InstallerError, GatewayStateError)): s.selected(self.shared)

        self.path.chmod(0o600); os.link(self.path, self.path.with_suffix('.alias'))
        with self.assertRaises((InstallerError, GatewayStateError)): s.selected(self.shared)


class PublicOverlaySelectionTests(unittest.TestCase):
    def setUp(self):
        self.generation = s.g.Generation(selected())
        self.shared = self.generation.original.shared
        self.profile = self.shared.web
        self.scope = SimpleNamespace(directory=self.shared.http.spec.maintenance_directory)
        self.enterContext(patch.object(s.g.public.SharedPublic, '_read',
            side_effect=lambda _, name: {} if name == 'handoff.attempt' else self.shared.value,
            autospec=True))
        self.old_configuration = self.enterContext(patch.object(s.g.public.SharedPublic, 'configuration'))
        self.enterContext(patch('subprocess.run', side_effect=AssertionError('native effect')))

    def overlay(self):
        return s.g.public.overlay(self.profile, self.scope, self.profile.value['backend_fragment_sha256'])

    def test_unselected_shared_overlay_keeps_historical_contract(self):
        with patch.object(s, 'selected', return_value=None):
            evidence = self.overlay()
        self.old_configuration.assert_called_once_with()
        self.assertNotIn('gateway_generation_sha256', evidence)
        self.assertEqual(evidence['dropin_sha256'], s.g.boot.f._sha(self.shared.apache_dropin()))

    def test_selected_overlay_reports_actual_successor_bytes_not_old_drain_hash(self):
        with patch.object(s, 'selected', return_value=self.generation):
            evidence = self.overlay()
        self.old_configuration.assert_not_called()
        self.assertEqual(evidence['gateway_generation_sha256'], self.generation.digest)
        self.assertEqual(evidence['dropin_sha256'], s.g.boot.f._sha(self.generation.readers()[1].apache_dropin()))
        self.assertNotEqual(evidence['dropin_sha256'], s.g.boot.f._sha(self.shared.apache_dropin()))

    def test_damaged_selection_never_falls_back_to_old_configuration(self):
        with patch.object(s, 'selected', side_effect=InstallerError(s.g.ErrorCode.SOURCE_DRIFT)):
            with self.assertRaises(InstallerError): self.overlay()
        self.old_configuration.assert_not_called()


if __name__ == '__main__': unittest.main()
