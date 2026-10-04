"""Real filesystem crash reconciliation; native CI supplies services and Ext4."""
from contextlib import nullcontext
import os
import threading
import unittest
from unittest.mock import Mock, patch

from installer import gateway_transition_cutover as c
from installer.model import canonical_bytes
import test_gateway_transition_stage as fixture


class TransitionCutoverTests(unittest.TestCase):
    acquire = fixture.TransitionStageTests.acquire
    snapshot = fixture.TransitionStageTests.snapshot
    composed = fixture.TransitionStageTests.composed

    def setUp(self):
        fixture.TransitionStageTests.setUp(self)
        self.runtime.profile.binary = self.runtime.root / 'hestia-mobile-gateway'
        self.runtime.profile.binary.write_bytes(b'source-' * 160000); self.runtime.profile.binary.chmod(0o750)
        self.runtime.profile.binding = self.source.binding
        self.runtime.manifest = lambda account: {'binding': self.source.binding(), 'uid': 0, 'gid': 0}
        self.runtime.stopped = Mock()
        (self.runtime.root / 'control').mkdir(mode=0o700)
        self.runtime.web._inspect_configuration = Mock(return_value=(self.runtime.account(), None, None, None))
        self.scope = Mock(); self.scope.recover.side_effect = lambda *a, **k: nullcontext(self.barrier._lease)
        self.runtime.web._scope = Mock(return_value=self.scope)
        self.barrier._lease.scope.web_gid = 0
        item = patch.object(c, '_barrier', return_value=self.barrier); item.start(); self.addCleanup(item.stop)
        self.kwargs = {'source_package': self.root / 'source.zip', 'target_package': self.root / 'target.zip',
                      'target_commit': fixture.FCM_COMMIT, 'direction': 'upgrade', 'confirmed': True}
        self.cutover = self.runtime.root / 'control' / ('cutover-' + self.barrier._lease.lease_id)
        self.marker = self.root / 'maintenance' / c.MARKER
        self.saved = self.composed()
        fixture.TransitionStageTests.prepare(self, self.saved)

    def apply(self, **options): return c.apply(self.saved, **{**self.kwargs, **options})

    def recover(self, action='resume', **options):
        self.saved.fence.close()
        return c.recover(self.runtime, self.backups, self.barrier._lease.lease_id,
                         action=action, **{**self.kwargs, **options})

    def crash(self, boundary, operation):
        original_new, original_write, original_rename = c.files._new, c.files._write, os.rename
        original_chmod = os.fchmod
        def create(fd, name, raw):
            original_new(fd, name, raw)
            if name == boundary: raise RuntimeError('lost write reply')
        def write(fd, raw):
            original_write(fd, raw)
            if boundary == 'partial' and len(raw) == 1024 * 1024: raise RuntimeError('partial')
        def rename(src, dst, **kwargs):
            original_rename(src, dst, **kwargs)
            if boundary == 'rename': raise RuntimeError('lost rename reply')
        def chmod(fd, mode):
            original_chmod(fd, mode)
            if boundary == 'mode' and mode == 0o750: raise RuntimeError('lost mode reply')
        with patch.object(c.files, '_new', create), patch.object(c.files, '_write', write), \
                patch.object(c.os, 'rename', rename), patch.object(c.os, 'fchmod', chmod):
            with self.assertRaises(c.g.GatewayStateError): operation()

    def test_target_and_file_rollback_preserve_all_sqlite_bytes_and_inodes(self):
        before = {p: (p.read_bytes(), p.stat().st_ino) for p in self.state.rglob('*') if p.is_file()}
        frozen = (self.root / 'maintenance' / c.g.MARKER).read_bytes()
        target = self.apply()
        self.assertEqual(target['state'], 'TARGET_BINARY_INSTALLED_ACTIVITY_CLOSED')
        self.assertEqual(self.runtime.profile.binary.read_bytes(), b'target-' * 160000)
        restored = self.recover('rollback')
        self.assertEqual(restored['state'], 'ORIGINAL_BINARY_RESTORED_ACTIVITY_CLOSED')
        self.assertEqual(self.runtime.profile.binary.read_bytes(), b'source-' * 160000)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino) for p in self.state.rglob('*') if p.is_file()})
        self.assertEqual(frozen, (self.root / 'maintenance' / c.g.MARKER).read_bytes())
        self.assertTrue(self.marker.exists()); self.assertTrue(all(v & c.g.inf.IMMUTABLE for v in self.flags.values()))
        for report in (target, restored):
            for key in ('activity_resumed', 'services_started', 'sqlite_restored', 'rollback_verified',
                        'boot_requalified', 'active_profile_changed', 'phase6_complete', 'apply_allowed'):
                self.assertIs(report[key], False)

    def test_target_every_durable_cut_recovers_by_inode_without_sqlite_replay(self):
        boundaries = (c.MARKER, 'partial', 'mode', 'target.armed.json', 'rename', 'target.done.json')
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                self.crash(boundary, self.apply)
                self.worker_call.side_effect = AssertionError('SQLite replay')
                result = self.recover()
                self.assertEqual(result['state'], 'TARGET_BINARY_INSTALLED_ACTIVITY_CLOSED')
                self.assertEqual(self.runtime.profile.binary.read_bytes(), b'target-' * 160000)
                if boundary != boundaries[-1]: self.doCleanups(); self.setUp()

    def test_rollback_every_durable_cut_recovers_without_reinstalling_target(self):
        boundaries = ('rollback.json', 'partial', 'mode', 'source.armed.json', 'rename', 'source.done.json')
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                self.apply(); self.crash(boundary, lambda: self.recover('rollback'))
                result = self.recover()
                self.assertEqual(result['state'], 'ORIGINAL_BINARY_RESTORED_ACTIVITY_CLOSED')
                self.assertEqual(self.runtime.profile.binary.read_bytes(), b'source-' * 160000)
                if boundary != boundaries[-1]: self.doCleanups(); self.setUp()

    def test_completed_resume_and_check_are_read_only_for_both_phases(self):
        self.apply()
        for role in ('target', 'source'):
            if role == 'source': self.recover('rollback')
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
                for p in [self.runtime.profile.binary, self.marker, *self.cutover.iterdir()]}
            with patch.object(c.files, '_new', side_effect=AssertionError('replay')), \
                    patch.object(c.os, 'rename', side_effect=AssertionError('rename replay')):
                self.assertEqual(self.recover(), self.recover('check'))
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in before})

    def test_consent_type_closed_fence_and_cancel_refuse_before_marker(self):
        cancel = threading.Event(); cancel.set()
        for options in ({'confirmed': False}, {'confirmed': 1}, {'cancel': cancel}):
            with self.assertRaises(c.g.GatewayStateError): self.apply(**options)
            self.assertFalse(self.marker.exists())
        self.saved.fence.close()
        with self.assertRaises(c.g.GatewayStateError): self.apply()
        self.assertFalse(self.marker.exists())
        with self.assertRaises(c.g.GatewayStateError): c.apply(object(), **self.kwargs)

    def test_initial_apply_refuses_existing_intent_and_foreign_directory(self):
        self.cutover.mkdir(mode=0o700)
        with self.assertRaises(c.g.GatewayStateError): self.apply()
        self.assertFalse(self.marker.exists()); self.cutover.rmdir(); self.apply()
        with self.assertRaises(c.g.GatewayStateError): self.apply()

    def test_missing_or_changed_preparation_never_authorizes_binary_write(self):
        receipt = self.slot / 'prepared.json'; raw = receipt.read_bytes(); receipt.unlink()
        with self.assertRaises(c.g.GatewayStateError): self.apply()
        receipt.write_bytes(raw); receipt.chmod(0o600); (self.slot / 'target.bin').write_bytes(b'foreign')
        with self.assertRaises(c.g.GatewayStateError): self.apply()
        self.assertFalse(self.marker.exists())

    def test_missing_intent_and_wrong_action_cannot_create_recovery(self):
        for action in ('resume', 'check', 'rollback', 'start', None):
            with self.assertRaises(c.g.GatewayStateError): self.recover(action)
        self.assertFalse(self.marker.exists())

    def test_incomplete_target_must_finish_before_rollback_or_check(self):
        self.crash('rename', self.apply)
        for action in ('check', 'rollback'):
            with self.assertRaises(c.g.GatewayStateError): self.recover(action)
        self.assertFalse((self.cutover / 'rollback.json').exists())
        self.recover(); self.recover('rollback')
        with self.assertRaises(c.g.GatewayStateError): self.recover('rollback')

    def test_partial_foreign_bytes_links_and_broadened_modes_are_not_repaired(self):
        self.crash('partial', self.apply)
        pending = self.cutover / 'target.pending'; raw = pending.read_bytes()
        for kind in ('bytes', 'symlink', 'hardlink', 'mode'):
            pending.unlink()
            other = self.root / 'foreign'; other.write_bytes(raw); other.chmod(0o600)
            if kind == 'symlink': pending.symlink_to(other)
            elif kind == 'hardlink': os.link(other, pending)
            else: pending.write_bytes(b'foreign' if kind == 'bytes' else raw); pending.chmod(0o644 if kind == 'mode' else 0o600)
            with self.subTest(kind=kind), self.assertRaises(c.g.GatewayStateError): self.recover()
            self.assertEqual(other.read_bytes(), raw)

    def test_same_bytes_replaced_inode_cannot_be_adopted_after_lost_rename(self):
        self.crash('rename', self.apply)
        binary = self.runtime.profile.binary; copy = binary.with_name('foreign-binary')
        copy.write_bytes(binary.read_bytes()); copy.chmod(0o750); copy.replace(binary)
        with self.assertRaisesRegex(c.g.GatewayStateError, 'BINARY_CHANGED'): self.recover()
        self.assertFalse((self.cutover / 'target.done.json').exists())

    def test_completed_target_drift_or_deletion_is_not_repaired(self):
        self.apply(); binary = self.runtime.profile.binary
        binary.write_bytes(b'changed')
        with self.assertRaises(c.g.GatewayStateError): self.recover()
        self.assertEqual(binary.read_bytes(), b'changed'); binary.unlink()
        with self.assertRaises(c.g.GatewayStateError): self.recover()
        self.assertFalse(binary.exists())

    def test_unknown_file_or_torn_journal_stays_for_manual_inspection(self):
        self.crash(c.MARKER, self.apply); self.cutover.mkdir(mode=0o700)
        foreign = self.cutover / 'foreign'; foreign.write_bytes(b'keep')
        with self.assertRaises(c.g.GatewayStateError): self.recover()
        self.assertEqual(foreign.read_bytes(), b'keep'); foreign.unlink()
        self.marker.write_bytes(b'{')
        with self.assertRaises(c.g.GatewayStateError): self.recover()
        self.assertEqual(self.marker.read_bytes(), b'{')

    def test_changed_saved_backup_and_live_sqlite_refuse_more_effects(self):
        self.crash(c.MARKER, self.apply)
        image = self.saved.slot / 'database.sqlite'; original = image.read_bytes(); image.write_bytes(b'bad')
        with self.assertRaises(c.g.GatewayStateError): self.recover()
        image.write_bytes(original); db = self.state / 'gateway.db'; old = db.stat()
        db.write_bytes(b'X' * old.st_size); os.utime(db, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaises(c.g.GatewayStateError): self.recover()
        self.assertEqual(self.runtime.profile.binary.read_bytes(), b'source-' * 160000)

    def test_guard_blocks_gateway_unseal_and_maintenance_release_independently(self):
        self.apply()
        before = dict(self.flags)
        with self.assertRaises(c.g.GatewayStateError): c.r.release(self.saved, confirmed=True)
        with self.assertRaises(c.g.GatewayStateError): c.r.recover(self.runtime, self.barrier, self.backups, confirmed=True)
        from installer.maintenance import MaintenanceLease, MaintenanceError
        lease = object.__new__(MaintenanceLease); lease._directory = self.gate; lease.assert_held = Mock()
        # Test the new blocker alone; do not depend on the older SQLite marker.
        (self.root / 'maintenance' / c.g.MARKER).unlink()
        with self.assertRaisesRegex(MaintenanceError, 'DATA_ACCESS_CLOSED'): lease.resume(confirmed=True)
        self.assertEqual(before, self.flags)

    def test_ordinary_service_reader_keeps_its_enrolled_binary_hash(self):
        runtime = self.runtime; runtime._inspect_binary = Mock(return_value={'original': True})
        self.assertEqual(runtime.inspect(), {'original': True})
        runtime._inspect_binary.assert_called_once_with(runtime.profile.selected_release['binary_sha256'])

    def test_wrong_preparation_direction_or_package_refuses_recovery(self):
        self.apply()
        with patch.object(c.stage, '_binary', return_value=b'wrong'):
            with self.assertRaises(c.g.GatewayStateError): self.recover()
        raw = self.marker.read_bytes(); value = c._json(raw); value['preparation_sha256'] = '0' * 64
        self.marker.write_bytes(canonical_bytes(value))
        with self.assertRaises(c.g.GatewayStateError): self.recover()
