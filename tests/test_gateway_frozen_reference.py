"""Publication-pinned public/boot readers: pure planning, strict live checks."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import gateway_frozen_reference as f, shared_public_runtime as public
from installer import mobile_boot_runtime as boot, frozen_shared_public as frozen
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import canonical_bytes
import test_gateway_active_profile as fixture
import test_shared_public_runtime as shared
import test_mobile_boot as mobile


class FrozenReferenceTests(unittest.TestCase):
    setUp = fixture.ActiveReaderTests.setUp
    write = staticmethod(fixture.ActiveReaderTests.write)
    publish_value = fixture.ActiveReaderTests.publish_value

    def digest(self): return f.a.sha(canonical_bytes(self.value))

    def test_reference_selects_exact_target_without_nss_process_network_or_state_probe(self):
        before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                  for p in self.root.rglob('*') if p.is_file()}
        with patch.object(GatewayServiceRuntime, 'account', side_effect=AssertionError('NSS')), \
             patch.object(GatewayServiceRuntime, 'state_binding', side_effect=AssertionError('live state')), \
             patch.object(GatewayServiceRuntime, 'inspect', side_effect=AssertionError('native')), \
             patch('subprocess.run', side_effect=AssertionError('effect')):
            self.assertEqual(f.reference(self.original), (self.runtime.profile.binding(), self.digest()))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in before})

    def test_original_selection_requires_both_publication_files_absent(self):
        root = self.runtime.root / 'control'
        for missing in (f.a.INTENT, f.a.ACTIVE):
            path = root / missing; raw = path.read_bytes(); path.unlink()
            with self.assertRaises(f.a.c.g.GatewayStateError): f.reference(self.original)
            path.write_bytes(raw); path.chmod(0o600)
        for name in (f.a.INTENT, f.a.ACTIVE): (root / name).unlink()
        self.assertEqual(f.reference(self.original), (self.original.profile.binding(), None))

    def test_reference_rejects_modified_ancestor_completion_and_state_grammar(self):
        for mutate in (lambda v: v.update(extra=True), lambda v: v.update(version=True),
                lambda v: v['target_manifest'].update(uid=True),
                lambda v: v['state']['gateway.db'].update(inode=True),
                lambda v: v['state'].update(foreign={}),
                lambda v: v['target_done'].update(armed_sha256='0'*64)):
            value = deepcopy(self.value); mutate(value); self.publish_value(value)
            with self.subTest(value=value), self.assertRaises(f.a.c.g.GatewayStateError): f.reference(self.original)

    def test_reference_refuses_foreign_enrollment_and_cutover_without_repair(self):
        path = self.slot / 'target.done.json'; path.write_bytes(b'{}')
        with self.assertRaises(f.a.c.g.GatewayStateError): f.reference(self.original)
        self.assertEqual(path.read_bytes(), b'{}')
        self.write(path, self.value['target_done'])
        with self.assertRaises(f.a.c.g.GatewayStateError): f.reference(self.runtime)
        self.write(self.runtime.root / 'staged.json', self.value['target_manifest'])
        with self.assertRaises(f.a.c.g.GatewayStateError): f.reference(self.original)

    def test_reference_refuses_links_permissions_and_torn_records(self):
        path = self.runtime.root / 'control' / f.a.ACTIVE; raw = path.read_bytes()
        other = self.root / 'other'; other.write_bytes(raw); other.chmod(0o600)
        for mode in ('symlink', 'hardlink', 'permissions', 'torn'):
            path.unlink()
            if mode == 'symlink': path.symlink_to(other)
            elif mode == 'hardlink': os.link(other, path)
            else: path.write_bytes(b'{' if mode == 'torn' else raw); path.chmod(0o644 if mode == 'permissions' else 0o600)
            with self.subTest(mode=mode), self.assertRaises(f.a.c.g.GatewayStateError): f.reference(self.original)
        self.assertEqual(other.read_bytes(), raw)

    def test_attach_pins_target_and_audits_real_binary_without_rewriting_profile(self):
        before = canonical_bytes(self.runtime.profile.binding())
        with patch.object(self.runtime, '_inspect_binary', return_value={'native': True}) as audit:
            f.attach(self.runtime, self.digest())
            self.assertEqual(self.runtime.inspect(), {'native': True})
        self.assertEqual(audit.call_count, 2)
        self.assertTrue(all(call.args == (self.runtime.profile.selected_release['binary_sha256'],)
                            for call in audit.call_args_list))
        self.assertEqual(before, canonical_bytes(self.runtime.profile.binding()))
        self.assertEqual(self.runtime._enrolled_manifest, self.enrollment)

    def test_wrong_generation_or_original_profile_refuses_before_native_audit(self):
        with patch.object(GatewayServiceRuntime, '_inspect_binary', side_effect=AssertionError('audit too early')):
            for runtime, digest in ((self.runtime, '0'*64), (self.original, self.digest())):
                with self.assertRaises(f.a.c.g.GatewayStateError): f.attach(runtime, digest)
        self.assertFalse(hasattr(self.runtime, '_active_profile'))
        self.assertFalse(hasattr(self.original, '_active_profile'))

    def test_absent_publication_cannot_fall_back_to_original_at_boot(self):
        for name in (f.a.INTENT, f.a.ACTIVE): (self.runtime.root / 'control' / name).unlink()
        with patch.object(GatewayServiceRuntime, '_inspect_binary', side_effect=AssertionError('fallback')):
            for runtime in (self.original, self.runtime):
                with self.assertRaises(f.a.c.g.GatewayStateError): f.attach(runtime, self.digest())

    def test_cached_frozen_reader_rechecks_authority_before_and_after_native_probe(self):
        with patch.object(self.runtime, '_inspect_binary'):
            f.attach(self.runtime, self.digest())
        def changed(_):
            (self.runtime.root / 'control' / f.a.ACTIVE).unlink(); return {}
        with patch.object(self.runtime, '_inspect_binary', side_effect=changed):
            with self.assertRaises(f.a.c.g.GatewayStateError): self.runtime.inspect()
        with patch.object(self.runtime, '_inspect_binary', side_effect=AssertionError('stale')):
            with self.assertRaises(f.a.c.g.GatewayStateError): self.runtime.inspect()

    def test_historical_reference_does_not_substitute_for_current_inode_audit(self):
        path = self.runtime.profile.binary; raw = path.read_bytes(); path.rename(path.with_name('old'))
        path.write_bytes(raw); path.chmod(0o750)
        self.assertEqual(f.reference(self.original)[1], self.digest())
        with self.assertRaises(f.a.c.g.GatewayStateError): f.attach(self.runtime, self.digest())

    def test_native_rejection_is_not_masked_and_source_reader_is_unchanged(self):
        with patch.object(self.runtime, '_inspect_binary', side_effect=RuntimeError('native mismatch')):
            with self.assertRaises(f.a.c.g.GatewayStateError): f.attach(self.runtime, self.digest())
        self.assertFalse(hasattr(self.original, '_active_profile'))
        with patch.object(self.original, '_inspect_binary') as audit:
            self.original.inspect()
        audit.assert_called_once_with(self.original.profile.selected_release['binary_sha256'])

    def test_public_reference_cannot_omit_or_change_generation(self):
        with patch.object(self.runtime, '_inspect_binary'): f.attach(self.runtime, self.digest())
        f.matches(self.original, None); f.matches(self.runtime, self.digest())
        for runtime, digest in ((self.runtime, None), (self.runtime, '0'*64), (self.original, self.digest())):
            with self.assertRaises(f.a.c.g.GatewayStateError): f.matches(runtime, digest)


class FrozenConsumerTests(unittest.TestCase):
    def profile(self):
        value = mobile.profile()
        value['shared'] = public.selection(value['shared']['preparation'], value['shared']['gateway_binding'], 'a'*64)
        return value

    def test_version_two_public_and_mobile_boot_compilers_are_pure_and_pin_publication(self):
        value = self.profile()
        with patch.object(f, 'reference', side_effect=AssertionError('host')), \
             patch.object(f, 'attach', side_effect=AssertionError('host')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('host')):
            runtime = boot.MobileBootRuntime(value)
            runtime.units(); runtime.shared.units()
        self.assertEqual(runtime.profile['shared']['gateway_publication_sha256'], 'a'*64)
        self.assertFalse(hasattr(runtime.gateway, '_active_profile'))

    def test_version_and_digest_grammar_cannot_downgrade_or_add_a_reference(self):
        for mutate in (lambda v: v.update(version=True), lambda v: v.update(version=1),
                lambda v: v.pop('gateway_publication_sha256'), lambda v: v.update(gateway_publication_sha256=True),
                lambda v: v.update(gateway_publication_sha256='A'*64), lambda v: v.update(extra='x')):
            value = self.profile()['shared']; mutate(value)
            with self.assertRaises(Exception): public.SharedPublic(value)

    def test_mobile_boot_attaches_only_its_frozen_profile_and_propagates_refusal(self):
        runtime = boot.MobileBootRuntime(self.profile()); binding = canonical_bytes(runtime.gateway.profile.binding())
        with patch.object(f, 'attach', side_effect=f.a.c.g.GatewayStateError('REFUSED')) as attach:
            with self.assertRaises(f.a.c.g.GatewayStateError): runtime.attach_gateway()
        attach.assert_called_once_with(runtime.gateway, 'a'*64)
        self.assertEqual(binding, canonical_bytes(runtime.gateway.profile.binding()))
        with patch.object(f, 'attach', side_effect=AssertionError('legacy adoption')):
            boot.MobileBootRuntime(mobile.profile()).attach_gateway()

    def test_public_native_reader_checks_generation_before_accepting_owned_service(self):
        runtime = public.SharedPublic(self.profile()['shared'])
        gateway = SimpleNamespace(profile=SimpleNamespace(binding=lambda: runtime.value['gateway_binding']), owned=Mock())
        with patch.object(public.foundation_drain, 'attached'), patch.object(public.gateway_service_drain, 'attached', return_value=gateway):
            with self.assertRaises(f.a.c.g.GatewayStateError): runtime.gateway()
        gateway.owned.assert_not_called()

    def test_frozen_shared_parent_refuses_changed_generation_despite_same_target(self):
        value = self.profile()['shared']
        control = SimpleNamespace(binding=lambda parent, **options:(value['preparation'], value['gateway_binding'], 'b'*64),
            profile=lambda: value)
        with patch.object(public, 'engine', side_effect=AssertionError('stale engine')):
            with self.assertRaises(Exception): frozen.reference(control, {})


class SuccessorLifecycleTests(unittest.TestCase):
    setUp = shared.LifecycleTests.setUp
    plan = shared.LifecycleTests.plan

    def test_new_public_plan_freezes_reference_and_cannot_follow_later_publication(self):
        self.reference.side_effect = None
        self.reference.return_value = (self.value['gateway_binding'], 'a'*64)
        self.plan(); self.assertEqual(self.control.profile()['version'], 2)
        self.assertEqual(self.control.profile()['gateway_publication_sha256'], 'a'*64)
        before = {p: p.read_bytes() for p in self.control.root.rglob('*') if p.is_file()}
        self.reference.return_value = (self.value['gateway_binding'], 'b'*64)
        with self.assertRaises(Exception): self.control.engine(self.parent_doc)
        with self.assertRaises(Exception): self.plan()
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_existing_original_public_plan_cannot_adopt_a_publication(self):
        self.plan(); before = self.control.profile()
        self.reference.side_effect = None
        self.reference.return_value = (self.value['gateway_binding'], 'a'*64)
        with self.assertRaises(Exception): self.control.engine(self.parent_doc)
        with self.assertRaises(Exception): self.plan()
        self.assertEqual(before, self.control.profile())
