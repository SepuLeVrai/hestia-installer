"""Publication crash boundaries and independent current-reader contracts."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import gateway_active_profile as a, gateway_service_drain as drain
from installer import gateway_release as catalog
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import canonical_bytes
import test_gateway_transition_cutover as cut
import test_gateway_transition as profiles


class ActivePublicationTests(unittest.TestCase):
    acquire = cut.TransitionCutoverTests.acquire
    snapshot = cut.TransitionCutoverTests.snapshot
    composed = cut.TransitionCutoverTests.composed
    setUp = cut.TransitionCutoverTests.setUp
    apply = cut.TransitionCutoverTests.apply
    recover = cut.TransitionCutoverTests.recover
    crash = cut.TransitionCutoverTests.crash

    def publish(self, action='apply', **options):
        self.saved.fence.close()
        return a.publish(self.runtime, self.backups, self.barrier._lease.lease_id,
                         action=action, **{**self.kwargs, **options})

    def test_publication_changes_only_successor_records_and_keeps_all_fences(self):
        self.apply()
        paths = [self.marker, *self.cutover.iterdir(), *self.state.rglob('*'), self.runtime.profile.binary]
        before = {p: (p.read_bytes(), p.stat().st_ino) for p in paths if p.is_file()}
        flags = dict(self.flags)
        result = self.publish()
        self.assertTrue(result['active_profile_changed'])
        self.assertEqual(result['intent']['target_manifest']['binding']['release']['commit'], self.kwargs['target_commit'])
        for key in ('activity_resumed', 'services_started', 'sqlite_restored', 'rollback_verified',
                    'boot_requalified', 'restore_to_original_allowed', 'phase6_complete'):
            self.assertFalse(result[key])
        self.assertEqual(flags, self.flags)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino) for p in before})

    def test_every_durable_publication_cut_recovers_without_binary_or_sqlite_replay(self):
        for name in (a.MARKER, a.INTENT, a.ACTIVE):
            with self.subTest(name=name):
                self.apply(); self.crash(name, self.publish)
                with patch.object(a.files, '_write', wraps=a.files._write), \
                        patch.object(a.c.os, 'rename', side_effect=AssertionError('binary replay')), \
                        patch.object(a.c.b, '_worker', side_effect=AssertionError('SQLite replay')):
                    result = self.publish('resume')
                self.assertTrue(result['active_profile_changed'])
                if name != a.ACTIVE: self.doCleanups(); self.setUp()

    def test_completed_check_and_resume_are_read_only(self):
        self.apply(); result = self.publish()
        paths = [self.root / 'maintenance' / a.MARKER,
                 *(self.runtime.root / 'control').glob('active-profile*')]
        before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in paths}
        with patch.object(a.files, '_new', side_effect=AssertionError('rewrite')):
            self.assertEqual(result, self.publish('resume')); self.assertEqual(result, self.publish('check'))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in paths})

    def test_first_publication_intent_blocks_all_old_cutover_actions(self):
        self.apply(); self.crash(a.MARKER, self.publish)
        for action in ('resume', 'check', 'rollback'):
            with self.subTest(action=action), self.assertRaises(a.c.g.GatewayStateError): self.recover(action)
        self.assertFalse((self.cutover / 'rollback.json').exists())

    def test_active_record_blocks_old_cutover_even_if_publication_marker_is_missing(self):
        self.apply(); self.publish(); (self.root / 'maintenance' / a.MARKER).unlink()
        with self.assertRaises(a.c.g.GatewayStateError): self.recover('rollback')
        with self.assertRaises(a.c.g.GatewayStateError): self.publish('resume')

    def test_active_marker_alone_blocks_maintenance_and_original_sqlite_release(self):
        self.apply(); self.publish(); self.marker.unlink()
        with self.assertRaises(a.c.g.GatewayStateError): a.c.r.release(self.saved, confirmed=True)
        with self.assertRaises(a.c.g.GatewayStateError): a.c.r.recover(self.runtime, self.barrier, self.backups, confirmed=True)
        from installer.maintenance import MaintenanceLease, MaintenanceError
        lease = object.__new__(MaintenanceLease); lease._directory = self.gate; lease.assert_held = Mock()
        (self.root / 'maintenance' / a.c.g.MARKER).unlink()
        with self.assertRaisesRegex(MaintenanceError, 'DATA_ACCESS_CLOSED'): lease.resume(confirmed=True)

    def test_consent_cancel_wrong_action_missing_cutover_and_incomplete_target_refuse(self):
        with self.assertRaises(a.c.g.GatewayStateError): self.publish()
        self.doCleanups(); self.setUp()
        self.crash('rename', self.apply)
        with self.assertRaises(a.c.g.GatewayStateError): self.publish()
        self.recover()
        cancel = threading.Event(); cancel.set()
        for options in ({'confirmed': False}, {'confirmed': 1}, {'cancel': cancel}):
            with self.assertRaises(a.c.g.GatewayStateError): self.publish(**options)
        for action in ('resume', 'check', 'start', None):
            with self.assertRaises(a.c.g.GatewayStateError): self.publish(action)
        self.assertFalse((self.root / 'maintenance' / a.MARKER).exists())

    def test_original_binary_restored_cannot_publish_a_target_profile(self):
        self.apply(); self.recover('rollback')
        with self.assertRaisesRegex(a.c.g.GatewayStateError, 'COMPLETED_TARGET_REQUIRED'): self.publish()
        self.assertFalse((self.root / 'maintenance' / a.MARKER).exists())

    def test_saved_backup_or_live_sqlite_drift_refuses_before_publication(self):
        self.apply(); image = self.saved.slot / 'database.sqlite'; raw = image.read_bytes()
        image.write_bytes(b'foreign')
        with self.assertRaises(a.c.g.GatewayStateError): self.publish()
        image.write_bytes(raw); db = self.state / 'gateway.db'; db.write_bytes(b'foreign')
        with self.assertRaises(a.c.g.GatewayStateError): self.publish()
        self.assertFalse((self.root / 'maintenance' / a.MARKER).exists())

    def test_foreign_torn_or_missing_completed_records_never_repaired(self):
        self.apply(); root = self.runtime.root / 'control'
        path = root / a.INTENT; path.write_bytes(b'{'); path.chmod(0o600)
        with self.assertRaises(a.c.g.GatewayStateError): self.publish()
        self.assertEqual(path.read_bytes(), b'{'); path.unlink()
        self.publish(); saved = path.read_bytes(); path.unlink()
        with self.assertRaises(a.c.g.GatewayStateError): self.publish('resume')
        self.assertFalse(path.exists()); path.write_bytes(saved); path.chmod(0o600)
        (root / a.ACTIVE).write_bytes(b'{}')
        with self.assertRaises(a.c.g.GatewayStateError): self.publish('resume')
        self.assertEqual((root / a.ACTIVE).read_bytes(), b'{}')


class ActiveReaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/var/lib'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.payload = b'fixture-target-binary'
        for name, content in (('_RELEASE', b'fixture-source'), ('_FCM_RELEASE', self.payload)):
            item = patch.object(catalog, name, {**getattr(catalog, name), 'binary_sha256': a.sha(content)})
            item.start(); self.addCleanup(item.stop)
        source = profiles.GatewayTransitionTests().profile()
        source.foundation.web.spec = replace(source.web.spec, root=self.root / 'http',
                                             maintenance_directory=self.root / 'maintenance')
        self.foundation = source.foundation
        self.original = GatewayServiceRuntime(self.foundation, source.identity, self.root / 'keys')
        self.runtime = GatewayServiceRuntime(self.foundation, source.identity, self.root / 'keys',
                                             release_commit=catalog.FCM_COMMIT)
        for path in (self.runtime.root, self.runtime.root / 'control', self.runtime.profile.state): path.mkdir(mode=0o700)
        for name in ('gateway.db', 'gateway.lock'):
            path = self.runtime.profile.state / name; path.write_bytes(b'state'); path.chmod(0o600)
        binary = self.runtime.profile.binary; binary.write_bytes(self.payload); binary.chmod(0o750)
        item = patch.object(GatewayServiceRuntime, 'account', return_value=SimpleNamespace(pw_uid=0, pw_gid=0))
        item.start(); self.addCleanup(item.stop)
        self.enrollment = self.original.manifest(self.original.account())
        self.write(self.runtime.root / 'staged.json', self.enrollment)
        target = a.c._identity(binary.stat(), self.payload)
        origin = {'version': 1, 'policy': a.c.POLICY, 'instance': self.runtime.web.spec.instance,
            'lease_id': 'b' * 32, 'source_manifest': self.enrollment,
            'preparation_sha256': '1' * 64, 'fence_sha256': '2' * 64,
            'original_binary': {'device': target['device'], 'inode': target['inode'] + 100,
                               'bytes': 14, 'sha256': a.sha(b'fixture-source')}}
        owner = {'intent_sha256': a.sha(canonical_bytes(origin))}
        arm = {'owner': owner, 'role': 'target', 'previous': origin['original_binary'], 'replacement': target}
        done = {'owner': owner, 'armed_sha256': a.sha(canonical_bytes(arm)), 'role': 'target'}
        self.slot = self.runtime.root / 'control' / ('cutover-' + origin['lease_id']); self.slot.mkdir(mode=0o700)
        self.write(self.slot / 'target.armed.json', arm); self.write(self.slot / 'target.done.json', done)
        self.value = {'version': 1, 'policy': a.POLICY, 'direction': 'upgrade', 'cutover': origin,
            'source_manifest': self.enrollment, 'target_manifest': self.runtime.manifest(self.runtime.account()),
            'target_armed': arm, 'target_done': done, 'state': self.runtime.state_binding()}
        self.publish_value(self.value)

    @staticmethod
    def write(path, value): path.write_bytes(canonical_bytes(value)); path.chmod(0o600)

    def publish_value(self, value):
        raw = canonical_bytes(value); root = self.runtime.root / 'control'
        self.write(root / a.INTENT, value)
        (root / a.ACTIVE).write_bytes(a._receipt(raw)); (root / a.ACTIVE).chmod(0o600)

    def selected(self): return a.selected(self.foundation, self.enrollment)

    def test_current_reader_selects_target_and_keeps_original_enrollment_immutable(self):
        before = (self.runtime.root / 'staged.json').read_bytes()
        current = self.selected()
        self.assertEqual(current.profile.binding(), self.runtime.profile.binding())
        self.assertEqual(current._enrolled_manifest, self.enrollment)
        with patch.object(GatewayServiceRuntime, '_inspect_binary', return_value={'audited': True}) as probe:
            self.assertEqual(current.inspect(), {'audited': True})
            probe.assert_called_once_with(self.runtime.profile.selected_release['binary_sha256'])
            attached = drain.attached(self.runtime.web, self.foundation)
            self.assertEqual(attached.profile.binding(), current.profile.binding())
        self.assertEqual(before, (self.runtime.root / 'staged.json').read_bytes())

    def test_absent_publication_falls_back_only_to_original_enrollment(self):
        for name in (a.INTENT, a.ACTIVE): (self.runtime.root / 'control' / name).unlink()
        self.assertIsNone(self.selected())
        with patch.object(GatewayServiceRuntime, '_inspect_binary') as probe:
            self.original.inspect()
            probe.assert_called_once_with(self.original.profile.selected_release['binary_sha256'])

    def test_incomplete_publication_never_returns_original_or_target(self):
        for name in (a.INTENT, a.ACTIVE):
            path = self.runtime.root / 'control' / name; raw = path.read_bytes(); path.unlink()
            with self.assertRaises(a.c.g.GatewayStateError): self.selected()
            path.write_bytes(raw); path.chmod(0o600)

    def test_cached_reader_rejects_removed_or_replaced_authority_before_native_probe(self):
        current = self.selected(); path = self.runtime.root / 'control' / a.ACTIVE
        path.unlink()
        with patch.object(current, '_inspect_binary', side_effect=AssertionError('probe before authority')):
            with self.assertRaises(a.c.g.GatewayStateError): current.inspect()

    def test_native_probe_failure_and_mid_probe_authority_drift_are_not_masked(self):
        current = self.selected()
        with patch.object(current, '_inspect_binary', side_effect=RuntimeError('native mismatch')):
            with self.assertRaisesRegex(RuntimeError, 'native mismatch'): current.inspect()
        def drift(_): (self.runtime.root / 'control' / a.INTENT).write_bytes(b'{}'); return {}
        with patch.object(current, '_inspect_binary', side_effect=drift):
            with self.assertRaises(a.c.g.GatewayStateError): current.inspect()

    def test_same_bytes_foreign_binary_inode_and_changed_state_inode_refused(self):
        for path in (self.runtime.profile.binary, self.runtime.profile.state / 'gateway.db'):
            raw = path.read_bytes(); path.rename(path.with_name('old'))
            path.write_bytes(raw); path.chmod(0o750 if path == self.runtime.profile.binary else 0o600)
            with self.assertRaises(a.c.g.GatewayStateError): self.selected()
            path.unlink(); path.with_name('old').rename(path)

    def test_altered_profile_identity_lease_parent_uid_and_unknown_fields_refused(self):
        mutations = [lambda v: v.update(extra=True), lambda v: v.update(version=True),
            lambda v: v['source_manifest'].update(uid=True),
            lambda v: v['target_manifest']['binding'].update(key_directory='/var/lib/foreign'),
            lambda v: v['cutover'].update(lease_id='../escape'),
            lambda v: v['target_done'].update(armed_sha256='0' * 64),
            lambda v: v['target_armed']['replacement'].update(inode=True),
            lambda v: v.update(direction='rollback')]
        for mutate in mutations:
            value = deepcopy(self.value); mutate(value); self.publish_value(value)
            with self.subTest(value=value), self.assertRaises(a.c.g.GatewayStateError): self.selected()

    def test_cutover_rollback_drift_or_unexpected_file_refuses_selection(self):
        for name, payload in (('rollback.json', {}), ('source.pending', {}), ('target.done.json', {})):
            path = self.slot / name; old = path.read_bytes() if path.exists() else None
            self.write(path, payload)
            with self.assertRaises(a.c.g.GatewayStateError): self.selected()
            if old is None: path.unlink()
            else: path.write_bytes(old)

    def test_enrollment_rewrite_cannot_make_the_target_its_own_ancestor(self):
        self.write(self.runtime.root / 'staged.json', self.value['target_manifest'])
        with self.assertRaises(a.c.g.GatewayStateError): self.selected()

    def test_publication_links_broad_modes_and_torn_json_are_never_adopted(self):
        path = self.runtime.root / 'control' / a.INTENT; original = path.read_bytes()
        other = self.root / 'foreign'; other.write_bytes(original); other.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'mode', 'torn'):
            path.unlink()
            if kind == 'symlink': path.symlink_to(other)
            elif kind == 'hardlink': os.link(other, path)
            else: path.write_bytes(b'{' if kind == 'torn' else original); path.chmod(0o644 if kind == 'mode' else 0o600)
            with self.subTest(kind=kind), self.assertRaises(a.c.g.GatewayStateError): self.selected()
        self.assertEqual(other.read_bytes(), original)
