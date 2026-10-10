"""Pure successor compilation and closed reader contracts, not native boot proof."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer import gateway_public_generation as g
from installer.gateway_transition import FCM_COMMIT
from installer.model import InstallerError, canonical_bytes
from installer.private import gateway_public_worker as worker
from test_mobile_boot import profile


def selected():
    original = profile()
    return g.selection(original['shared'], original, FCM_COMMIT, 'upgrade', 'd' * 64, 'e' * 32)


class PublicGenerationTests(unittest.TestCase):
    def setUp(self):
        self.value = selected(); self.generation = g.Generation(self.value)

    def test_compilation_has_no_native_or_network_effect_and_preserves_parents(self):
        before = canonical_bytes(self.value)
        with patch('subprocess.run', side_effect=AssertionError('native')), \
                patch('socket.socket', side_effect=AssertionError('network')):
            self.generation.units(); self.generation.replacements(); self.generation.references(); self.generation.readers()
        self.assertEqual(canonical_bytes(self.value), before)

    def test_eight_resources_pin_new_worker_and_generation_without_changing_schedule(self):
        before, after = self.generation.source_units(), self.generation.units()
        self.assertEqual(tuple(after), g.fragments.resources(self.generation.layout.instance))
        self.assertTrue(all(before[n] != after[n] for n in before))
        for name, raw in after.items():
            if name.endswith('.timer'):
                normalize = lambda b: b'\n'.join(line for line in b.splitlines() if not line.startswith(b'Description='))
                self.assertEqual(normalize(raw), normalize(before[name]))
            else:
                self.assertIn(str(self.generation.root / 'worker.py').encode(), raw)
        self.assertNotIn(b'__GATEWAY_PUBLIC_GENERATION_SHA256__', self.generation.runner())
        self.assertIn(self.generation.digest.encode(), self.generation.runner())

    def test_backend_include_and_unit_dependencies_remain_exact(self):
        before, after = self.generation.source_units(), self.generation.units()
        for name in before:
            rows = lambda raw: [line for line in raw.splitlines()
                               if line.startswith((b'Requires=', b'After=', b'Before=', b'ConditionPathExists=', b'ExecStart=/usr/sbin/apache2'))]
            self.assertEqual(rows(before[name]), rows(after[name]))

    def test_all_readers_keep_historical_bundle_paths_and_use_successor_fragments(self):
        web, shared, mobile = self.generation.readers(); original = self.generation.original
        self.assertEqual(web.root, original.shared.boot.root)
        self.assertEqual(shared.root, original.shared.root)
        self.assertEqual(mobile.root, original.root)
        self.assertEqual(web.profile, original.shared.boot.profile)
        self.assertEqual(shared.value, original.shared.value)
        self.assertEqual(mobile.profile, original.profile)
        self.assertEqual(web.runner(), original.shared.boot.runner())
        self.assertEqual(shared.runner(), original.shared.runner())
        self.assertEqual(mobile.runner(), original.runner())
        for reader in (web, shared, mobile):
            for name, raw in reader.units().items():
                if name in self.generation.units(): self.assertEqual(raw, self.generation.units()[name])

    def test_certificate_paths_routes_policies_and_renewal_commands_are_preserved(self):
        shared = self.generation.readers()[1]; original = self.generation.original.shared
        self.assertEqual(shared.files(), original.files())
        self.assertEqual(shared.web.acme_root, original.web.acme_root)
        self.assertEqual(shared.shared.acme_root, original.shared.acme_root)
        self.assertEqual(shared.web.certbot(renew=True), original.web.certbot(renew=True))
        self.assertEqual(shared.shared.certbot(renew=True), original.shared.certbot(renew=True))
        self.assertEqual(shared.web.apache_include(), original.web.apache_include())

    def test_mobile_epoch_is_separate_and_source_epoch_is_unchanged(self):
        source = self.generation.original.epoch.root
        successor = self.generation.readers()[2].epoch.root
        self.assertNotEqual(source, successor)
        self.assertTrue(str(successor).endswith(self.value['lease_id']))
        self.assertEqual(self.generation.original.epoch.root, source)

    def test_closed_generation_refuses_unbound_source_target_lease_and_code(self):
        mutations = (lambda v: v.update(version=True), lambda v: v.update(policy='foreign'),
                     lambda v: v.update(command='start'), lambda v: v.update(lease_id='../foreign'),
                     lambda v: v.update(publication_sha256='f'),
                     lambda v: v['code'].update({'../evil.py': 'f' * 64}),
                     lambda v: v['code'].pop(g.WORKER),
                     lambda v: v['code'].pop(g.mobile.ASSET),
                     lambda v: v['target_binding']['release'].update(binary_sha256='f' * 64),
                     lambda v: v['mobile']['parents'].update(shared_public='f' * 64))
        for mutate in mutations:
            value = deepcopy(self.value); mutate(value)
            # The Mobile journal parent is independent evidence. Its change is
            # still a different frozen generation, not a compiler grammar error.
            if value['mobile']['parents'] != self.value['mobile']['parents']:
                self.assertNotEqual(g.Generation(value).digest, self.generation.digest)
            else:
                with self.assertRaises(Exception): g.Generation(value)

    def test_generation_references_are_exact_and_input_mutation_is_detached(self):
        refs = self.generation.references()
        self.assertEqual(set(refs), g.fragments.REFS)
        self.assertEqual(refs['publication'], self.value['publication_sha256'])
        self.assertNotEqual(refs['source_gateway'], refs['target_gateway'])
        self.value['publication_sha256'] = 'a' * 64
        self.assertEqual(self.generation.references(), refs)

    def test_first_generation_does_not_adopt_an_already_published_shared_profile(self):
        value = deepcopy(self.value)
        for profile in (value['shared'], value['mobile']['shared']):
            profile.update(version=2, gateway_publication_sha256='a' * 64)
        with self.assertRaises(Exception): g.Generation(value)

    def test_invalid_worker_role_refuses_before_bundle_or_service_read(self):
        for role in ('', 'restart', '../web', None, True):
            with patch.object(self.generation, 'configuration', side_effect=AssertionError('read too early')):
                with self.assertRaises(InstallerError):
                    self.generation.worker(role)


class PublicGenerationBundleTests(unittest.TestCase):
    """Real private files only; these checks do not simulate native admission."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hestia-generation-', dir='/var/lib')
        self.addCleanup(temporary.cleanup)
        self.generation = g.Generation(selected())
        self.generation.root = Path(temporary.name)
        payloads = {'profile.json': canonical_bytes(self.generation.value),
                    'worker.py': self.generation.runner(),
                    **{'code/' + name: raw for name, raw in g.mobile.code_files().items()}}
        for name, raw in payloads.items():
            path = self.generation.root / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(raw); path.chmod(0o600)

    def test_exact_sealed_bundle_is_read_only_and_does_not_grant_activation(self):
        before = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in self.generation.root.rglob('*')}
        with patch('subprocess.run', side_effect=AssertionError('native')):
            self.generation.bundle()
            self.assertIsNone(self.generation._read('activated.json'))
        self.assertEqual(before, {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in before})

    def test_corrupted_worker_and_extra_code_are_refused(self):
        path = self.generation.root / 'worker.py'; raw = path.read_bytes()
        path.write_bytes(raw + b'\n')
        with self.assertRaises(InstallerError): self.generation.bundle()
        path.write_bytes(raw)
        (self.generation.root / 'code/foreign.py').write_bytes(b'')
        with self.assertRaises(InstallerError): self.generation.bundle()

    def test_worker_reader_rejects_hardlink_symlink_and_writable_profile(self):
        path = self.generation.root / 'profile.json'
        self.assertEqual(worker.read(path), canonical_bytes(self.generation.value))
        other = path.with_name('other.json'); os.link(path, other)
        with self.assertRaises(ValueError): worker.read(path)
        other.unlink(); path.chmod(0o640)
        with self.assertRaises(ValueError): worker.read(path)
        path.chmod(0o600); path.rename(other); path.symlink_to(other)
        with self.assertRaises(OSError): worker.read(path)

    def test_worker_reader_rejects_writable_parent_and_oversize_file(self):
        path = self.generation.root / 'profile.json'
        self.generation.root.chmod(0o777)
        with self.assertRaises(ValueError): worker.read(path)
        self.generation.root.chmod(0o700); path.write_bytes(b'x' * 1048577)
        with self.assertRaises(ValueError): worker.read(path)


if __name__ == '__main__': unittest.main()
