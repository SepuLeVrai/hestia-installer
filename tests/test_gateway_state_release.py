"""File-only release faults; real immutable flags and services run in native CI."""
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from installer import gateway_state_release as r, gateway_state_fence as g, gateway_state_backup as b
from installer.model import canonical_bytes
import test_gateway_state as fixture


class ReleaseTests(unittest.TestCase):
    setUp = fixture.SnapshotTests.setUp
    acquire = fixture.FenceTests.acquire
    snapshot = fixture.SnapshotTests.snapshot

    def composed(self):
        saved = self.snapshot(); web_id = 'd' * 32; root = self.backups / web_id; root.mkdir(mode=0o700)
        manifest = canonical_bytes({'version': 1, 'instance': self.runtime.web.spec.instance,
            'lease_id': self.barrier._lease.lease_id})
        web = {'state': 'PROVISIONED_BACKUP_RESTORE_VERIFIED', 'backup_id': web_id,
            'activity_resumed': False, 'database_restoration_verified': True,
            'registered_data_restoration_verified': True, 'manifest_sha256': b.f._sha(manifest),
            'service_profile_sha256': b.f._sha(self.barrier._profile)}
        for name, raw in (('coordinated.json', manifest), ('verified.json', canonical_bytes(web))):
            (root / name).write_bytes(raw); (root / name).chmod(0o600)
        saved.compose(b.cb.CoordinatedVerification(canonical_bytes(web)))
        return saved

    def recover(self): return r.recover(self.runtime, self.barrier, self.backups, confirmed=True)
    def marker(self, name): return self.root / 'maintenance' / name

    def test_release_preserves_sources_and_leaves_durable_activity_refusal(self):
        saved = self.composed(); before = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        result = r.release(saved, confirmed=True).report()
        self.assertEqual(result['state'], 'GATEWAY_STATE_RELEASED_ACTIVITY_CLOSED')
        self.assertFalse(result['activity_resumed']); self.assertFalse(result['services_started'])
        self.assertFalse(result['restore_to_original_allowed'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()})
        self.assertFalse(any(f & g.inf.IMMUTABLE for f in self.flags.values()))
        self.assertTrue(self.marker(r.RELEASED).exists())
        self.assertFalse(self.marker(g.MARKER).exists()); self.assertFalse(self.marker(r.RELEASE).exists())
        with self.assertRaises(g.GatewayStateError): g.recover(self.runtime, self.barrier, confirmed=True)

    def test_every_flag_cut_recovers_exact_intent_and_never_replays_sqlite_worker(self):
        # One fresh fixture per cut, including the root-directory-last write.
        for cutoff in range(1, 5):
            with self.subTest(cutoff=cutoff):
                saved = self.composed(); changes = []
                def cut(fd, value=None):
                    observed = self.flags_call(fd, value)
                    if value is not None:
                        changes.append(value)
                        if len(changes) == cutoff: raise RuntimeError('lost reply')
                    return observed
                with patch.object(g.inf, '_flags', side_effect=cut), self.assertRaises(g.GatewayStateError):
                    r.release(saved, confirmed=True)
                raw = self.marker(r.RELEASE).read_bytes()
                self.assertTrue(self.marker(g.MARKER).exists())
                with patch.object(b, '_worker', side_effect=AssertionError('worker replay')):
                    result = self.recover().report()
                self.assertEqual(result['intent_sha256'], b.f._sha(raw))
                self.assertFalse(any(f & g.inf.IMMUTABLE for f in self.flags.values()))
                if cutoff < 4:
                    self.doCleanups(); self.setUp()

    def test_reply_lost_after_receipt_or_either_unlink_recovers(self):
        for boundary in ('receipt', g.MARKER, r.RELEASE):
            with self.subTest(boundary=boundary):
                saved = self.composed(); original_new = r.files._new; original_unlink = os.unlink
                def write(fd, name, raw):
                    original_new(fd, name, raw)
                    if boundary == 'receipt' and name == r.RELEASED: raise RuntimeError('lost receipt reply')
                def unlink(path, *args, **kwargs):
                    original_unlink(path, *args, **kwargs)
                    if path == boundary: raise RuntimeError('lost unlink reply')
                with patch.object(r.files, '_new', side_effect=write), patch.object(r.os, 'unlink', side_effect=unlink):
                    with self.assertRaises(g.GatewayStateError): r.release(saved, confirmed=True)
                receipt = self.marker(r.RELEASED).read_bytes()
                self.assertEqual(canonical_bytes(self.recover().report()), receipt)
                self.assertFalse(self.marker(g.MARKER).exists()); self.assertFalse(self.marker(r.RELEASE).exists())
                if boundary != r.RELEASE:
                    self.doCleanups(); self.setUp()

    def test_completed_recovery_is_read_only_and_report_has_no_live_probes(self):
        saved = self.composed(); result = r.release(saved, confirmed=True)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.marker(r.RELEASED).parent.iterdir()}
        self.worker_call.side_effect = AssertionError('worker replay')
        with patch.object(r.files, '_new', side_effect=AssertionError('journal rewrite')):
            recovered = self.recover()
        self.assertEqual(recovered.report(), result.report())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.marker(r.RELEASED).parent.iterdir()})
        self.barrier.assert_held.side_effect = AssertionError('report probe')
        self.assertFalse(recovered.report()['activity_resumed'])

    def test_confirmation_missing_composition_and_cancel_refuse_before_release_intent(self):
        saved = self.snapshot()
        with self.assertRaises(g.GatewayStateError): r.release(saved, confirmed=False)
        with self.assertRaises(g.GatewayStateError): r.release(saved, confirmed=True)
        self.assertFalse(self.marker(r.RELEASE).exists()); saved.fence.assert_held()
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(g.GatewayStateError): r.release(saved, confirmed=True, cancel=cancel)
        self.assertFalse(self.marker(r.RELEASE).exists()); saved.fence.assert_held()

    def test_altered_saved_image_and_live_bytes_cannot_release(self):
        saved = self.composed(); target = saved.slot / 'database.sqlite'; original = target.read_bytes()
        target.write_bytes(b'corrupt')
        with self.assertRaises(g.GatewayStateError): r.release(saved, confirmed=True)
        target.write_bytes(original)
        path = self.state / 'gateway.db'; info = path.stat(); path.write_bytes(b'X' * info.st_size)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(g.GatewayStateError, 'SOURCE_CHANGED'): r.release(saved, confirmed=True)
        self.assertFalse(self.marker(r.RELEASE).exists())
        self.assertTrue(all(f & g.inf.IMMUTABLE for f in self.flags.values()))

    def test_altered_web_receipt_refuses_before_intent(self):
        saved = self.composed(); path = self.backups / ('d' * 32) / 'verified.json'
        path.write_bytes(b'{}')
        with self.assertRaises(g.GatewayStateError): r.release(saved, confirmed=True)
        self.assertFalse(self.marker(r.RELEASE).exists()); saved.fence.assert_held()

    def pending(self):
        saved = self.composed()
        def cut(fd, value=None):
            result = self.flags_call(fd, value)
            if value is not None: raise RuntimeError('lost reply')
            return result
        with patch.object(g.inf, '_flags', side_effect=cut), self.assertRaises(g.GatewayStateError):
            r.release(saved, confirmed=True)
        return saved

    def test_partial_release_refuses_refreeze_and_changed_lease(self):
        self.pending(); raw = self.marker(r.RELEASE).read_bytes()
        for method in (g.acquire, g.recover):
            with self.assertRaises(g.GatewayStateError): method(self.runtime, self.barrier, confirmed=True)
        self.barrier._lease.lease_id = 'e' * 32
        with self.assertRaises(g.GatewayStateError): self.recover()
        self.assertEqual(raw, self.marker(r.RELEASE).read_bytes())

    def test_replaced_inode_changed_live_bytes_and_unknown_flags_refuse_recovery(self):
        for kind in ('inode', 'bytes', 'flags'):
            with self.subTest(kind=kind):
                self.pending(); path = self.state / 'gateway.db'; info = path.stat()
                if kind == 'inode':
                    path.rename(self.state / 'old'); path.write_bytes(b'fixture-sqlite-bytes'); path.chmod(0o600)
                    (self.state / 'old').unlink()
                elif kind == 'bytes':
                    path.write_bytes(b'X' * info.st_size); os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                else: self.flags[(info.st_dev, info.st_ino)] = 0x40000000
                before = dict(self.flags)
                with self.assertRaises(g.GatewayStateError): self.recover()
                self.assertEqual(before, self.flags); self.assertTrue(self.marker(r.RELEASE).exists())
                if kind != 'flags': self.doCleanups(); self.setUp()

    def test_missing_attempt_before_completion_never_counts_as_success(self):
        self.pending(); self.marker(g.MARKER).unlink()
        with self.assertRaises(g.GatewayStateError): self.recover()
        self.assertFalse(self.marker(r.RELEASED).exists())

    def test_missing_intent_and_forged_completed_receipt_are_rejected(self):
        with self.assertRaisesRegex(g.GatewayStateError, 'INTENT_REQUIRED'): self.recover()
        saved = self.composed(); r.release(saved, confirmed=True)
        self.marker(r.RELEASED).write_bytes(b'{"activity_resumed":true}')
        with self.assertRaises(g.GatewayStateError): self.recover()

    def test_corrupt_backup_after_partial_release_stops_without_more_flag_changes(self):
        saved = self.pending(); (saved.slot / 'source/gateway.db').write_bytes(b'corrupt')
        before = dict(self.flags)
        with self.assertRaises(g.GatewayStateError): self.recover()
        self.assertEqual(before, self.flags); self.assertTrue(self.marker(r.RELEASE).exists())

    def test_foreign_live_lock_blocks_recovery_without_modifying_journal(self):
        import fcntl
        self.pending(); before = self.marker(r.RELEASE).read_bytes()
        with (self.state / 'gateway.lock').open('rb') as foreign:
            fcntl.flock(foreign, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(g.GatewayStateError, 'BUSY'): self.recover()
        self.assertEqual(before, self.marker(r.RELEASE).read_bytes())

    def test_pending_and_completed_markers_independently_block_low_level_resume(self):
        from installer.maintenance import MaintenanceLease, MaintenanceError
        from unittest.mock import Mock
        lease = object.__new__(MaintenanceLease); lease._directory = self.gate; lease.assert_held = Mock()
        for name in (r.RELEASE, r.RELEASED):
            with self.subTest(name=name):
                self.marker(name).write_bytes(b'closed')
                try:
                    with self.assertRaisesRegex(MaintenanceError, 'DATA_ACCESS_CLOSED'): lease.resume(confirmed=True)
                finally: self.marker(name).unlink()
        self.assertEqual(list(self.marker(r.RELEASED).parent.iterdir()), [])
