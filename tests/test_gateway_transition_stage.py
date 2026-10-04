"""Stage crash/drift contracts; native recipe supplies real service/fence/packages."""
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from installer import gateway_transition_stage as stage
from installer import gateway_state_release as release
from installer.gateway_transition import FCM_COMMIT
from installer.model import canonical_bytes
import test_gateway_state_release as fixture
import test_gateway_transition as profiles


class TransitionStageTests(unittest.TestCase):
    acquire = fixture.ReleaseTests.acquire
    snapshot = fixture.ReleaseTests.snapshot
    composed = fixture.ReleaseTests.composed

    def setUp(self):
        fixture.ReleaseTests.setUp(self)
        self.source = profiles.GatewayTransitionTests().profile()
        self.runtime.profile.selected_release = self.source.selected_release
        assessment = profiles.GatewayTransitionTests().assess(self.source)
        for item in (patch.object(stage, 'assess', return_value=assessment),
                     patch.object(stage, '_binary', side_effect=lambda package, selected:
                        (b'source-' if selected['commit'] == self.source.selected_release['commit'] else b'target-') * 160000)):
            item.start(); self.addCleanup(item.stop)
        self.name = 'gateway-transition-' + self.barrier._lease.lease_id
        self.slot = self.backups / self.name
        self.intent = self.backups / (self.name + '.json')

    def prepare(self, saved, **options):
        return stage.prepare(saved, source_package=self.root / 'source.zip', target_package=self.root / 'target.zip',
            target_commit=FCM_COMMIT, direction='upgrade', confirmed=True, **options)

    def test_private_binaries_preserve_state_keys_and_maintenance(self):
        saved = self.composed()
        before = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        marker = (self.root / 'maintenance' / stage.g.MARKER).read_bytes()
        report = self.prepare(saved).report()
        self.assertEqual(report['state'], 'GATEWAY_TRANSITION_BINARIES_PREPARED')
        self.assertEqual(set(p.name for p in self.slot.iterdir()), {'source.bin', 'target.bin', 'prepared.json'})
        self.assertEqual(self.slot.stat().st_mode & 0o777, 0o700)
        for path in self.slot.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for key in ('active_profile_changed', 'apply_allowed', 'activity_resumed', 'services_started',
                    'rollback_verified', 'restore_to_original_allowed', 'boot_requalified', 'phase6_complete'):
            self.assertFalse(report[key])
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()})
        self.assertEqual((self.root / 'maintenance' / stage.g.MARKER).read_bytes(), marker)
        saved.fence.assert_held()

    def test_every_durable_write_boundary_recovers_without_worker_or_unseal(self):
        for boundary in ('intent', 'partial', 'source.bin', 'target.bin', 'receipt'):
            with self.subTest(boundary=boundary):
                saved = self.composed(); original_new = stage.files._new
                original_write = stage.files._write; original_rename = os.rename
                def create(fd, name, raw):
                    original_new(fd, name, raw)
                    if (boundary == 'intent' and name == self.name + '.json') or (boundary == 'receipt' and name == 'prepared.json'):
                        raise RuntimeError('lost reply')
                def write(fd, raw):
                    original_write(fd, raw)
                    if boundary == 'partial' and len(raw) == 1024 * 1024: raise RuntimeError('partial copy')
                def rename(src, dst, **kwargs):
                    original_rename(src, dst, **kwargs)
                    if dst == boundary: raise RuntimeError('lost rename reply')
                with patch.object(stage.files, '_new', side_effect=create), patch.object(stage.files, '_write', side_effect=write), patch.object(stage.os, 'rename', side_effect=rename):
                    with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
                raw = self.intent.read_bytes()
                self.worker_call.side_effect = AssertionError('worker replay')
                with patch.object(stage.g.inf, '_flags', side_effect=lambda fd, value=None:
                        self.flags_call(fd) if value is None else (_ for _ in ()).throw(AssertionError('unseal'))):
                    report = self.prepare(saved, recovery=True).report()
                self.assertEqual(raw, self.intent.read_bytes())
                self.assertEqual(report['intent_sha256'], stage.sha(raw)); saved.fence.assert_held()
                if boundary != 'receipt': self.doCleanups(); self.setUp()

    def test_completed_recovery_does_not_rewrite_any_artifact(self):
        saved = self.composed(); first = self.prepare(saved).report()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.slot.iterdir()}
        with patch.object(stage.files, '_new', side_effect=AssertionError('replay')):
            self.assertEqual(self.prepare(saved, recovery=True).report(), first)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.slot.iterdir()})
        self.barrier.assert_held.side_effect = AssertionError('GET probe')
        self.assertTrue(stage.GatewayTransitionStage(canonical_bytes(first)).report()['historical_only'])

    def test_initial_apply_never_adopts_prior_intent(self):
        saved = self.composed(); self.prepare(saved)
        with self.assertRaisesRegex(stage.g.GatewayStateError, 'EXPLICIT_RECOVERY'): self.prepare(saved)

    def test_recovery_never_creates_missing_intent(self):
        saved = self.composed()
        with self.assertRaisesRegex(stage.g.GatewayStateError, 'INTENT_REQUIRED'): self.prepare(saved, recovery=True)
        self.assertFalse(self.intent.exists()); self.assertFalse(self.slot.exists())

    def test_foreign_directory_is_not_adopted(self):
        saved = self.composed(); self.slot.mkdir(mode=0o700)
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
        self.assertFalse(self.intent.exists())

    def test_unknown_file_or_changed_receipt_is_not_deleted(self):
        saved = self.composed(); self.prepare(saved)
        path = self.slot / 'foreign'; path.write_bytes(b'keep'); path.chmod(0o600)
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved, recovery=True)
        self.assertEqual(path.read_bytes(), b'keep'); path.unlink()
        (self.slot / 'prepared.json').write_bytes(b'{}')
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved, recovery=True)

    def test_changed_binary_and_missing_completed_binary_are_never_repaired(self):
        saved = self.composed(); self.prepare(saved); binary = self.slot / 'target.bin'
        binary.write_bytes(b'changed')
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved, recovery=True)
        self.assertEqual(binary.read_bytes(), b'changed'); binary.unlink()
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved, recovery=True)
        self.assertFalse(binary.exists())

    def test_symlink_hardlink_and_executable_are_refused(self):
        saved = self.composed(); self.prepare(saved); binary = self.slot / 'target.bin'; original = binary.read_bytes()
        other = self.root / 'other'; other.write_bytes(original); other.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'executable'):
            binary.unlink()
            if kind == 'symlink': binary.symlink_to(other)
            elif kind == 'hardlink': os.link(other, binary)
            else: binary.write_bytes(original); binary.chmod(0o700)
            with self.subTest(kind=kind), self.assertRaises(stage.g.GatewayStateError): self.prepare(saved, recovery=True)
        self.assertEqual(other.read_bytes(), original)

    def test_partial_bytes_must_be_exact_prefix(self):
        saved = self.composed(); original = stage._copy
        with patch.object(stage, '_copy', side_effect=RuntimeError('crash')), self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
        path = self.slot / 'source.bin.part'; path.write_bytes(b'foreign'); path.chmod(0o600)
        with self.assertRaisesRegex(stage.g.GatewayStateError, 'STAGE_CHANGED'): self.prepare(saved, recovery=True)
        self.assertEqual(path.read_bytes(), b'foreign')

    def test_consent_wrong_type_and_incompatible_profile_refuse_before_intent(self):
        saved = self.composed()
        for confirmed, snapshot in ((False, saved), (1, saved), (True, object())):
            with self.assertRaises(stage.g.GatewayStateError):
                stage.prepare(snapshot, source_package=Path('/source'), target_package=Path('/target'),
                    target_commit=FCM_COMMIT, direction='upgrade', confirmed=confirmed)
        with patch.object(stage, 'assess', return_value=type('Report', (), {'report': lambda self: {'configuration_compatible': False}})()):
            with self.assertRaisesRegex(stage.g.GatewayStateError, 'PROFILE_REFUSED'): self.prepare(saved)
        self.assertFalse(self.intent.exists())

    def test_missing_composed_backup_or_changed_sqlite_refuses_before_intent(self):
        saved = self.snapshot()
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
        self.assertFalse(self.intent.exists())

    def test_altered_composed_snapshot_and_live_source_refuse(self):
        saved = self.composed(); image = saved.slot / 'database.sqlite'; raw = image.read_bytes(); image.write_bytes(b'changed')
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
        image.write_bytes(raw); db = self.state / 'gateway.db'; before = db.stat(); db.write_bytes(b'X' * before.st_size)
        os.utime(db, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
        self.assertFalse(self.intent.exists())

    def test_package_rejection_and_cancel_precede_effects(self):
        saved = self.composed()
        with patch.object(stage, '_binary', side_effect=RuntimeError('wrong package')), self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved, cancel=cancel)
        self.assertFalse(self.intent.exists())

    def test_changed_intent_is_not_rebased_to_new_input(self):
        saved = self.composed(); self.prepare(saved); self.intent.write_bytes(b'{}')
        with self.assertRaisesRegex(stage.g.GatewayStateError, 'INTENT_REQUIRED'): self.prepare(saved, recovery=True)
        self.assertEqual(self.intent.read_bytes(), b'{}')

    def test_released_or_closed_fence_cannot_authorize_preparation(self):
        saved = self.composed(); saved.fence.close()
        with self.assertRaises(stage.g.GatewayStateError): self.prepare(saved)
        self.assertFalse(self.intent.exists())
