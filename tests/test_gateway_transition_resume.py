"""Successor authority isolation, exact envelopes and interrupted consumption."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import gateway_resume_authority as h, gateway_service_drain as gd
from installer import mobile_reopen_admission as admission, mobile_activation_runtime as native
from installer.model import canonical_bytes
import test_gateway_active_profile as fixture


class SuccessorAuthorityTests(unittest.TestCase):
    write = staticmethod(fixture.ActiveReaderTests.write)
    publish_value = fixture.ActiveReaderTests.publish_value
    selected = fixture.ActiveReaderTests.selected

    def setUp(self):
        fixture.ActiveReaderTests.setUp(self)
        self.runtime = self.selected(); self.lease_id = self.value['cutover']['lease_id']
        self.gate = self.runtime.web.spec.maintenance_directory; self.gate.mkdir(mode=0o700)
        self.backups = self.root / 'backups'; self.backups.mkdir(mode=0o700)
        account = SimpleNamespace(pw_uid=0, pw_gid=0, pw_name='fixture')
        probe = patch.object(self.runtime.web, '_inspect_configuration', return_value=(account, None, None, None))
        probe.start(); self.addCleanup(probe.stop)
        self.profile = {'gateway_service': {'unit': self.runtime.unit,
            'manifest_sha256': h.sha(canonical_bytes(self.enrollment)), 'state': self.runtime.state_binding(),
            'policy': 'GATED_GATEWAY_STOP_BEFORE_FOUNDATION_V1'}}
        path = self.gate / ('http-drain-' + self.lease_id + '.attempt')
        self.write(path, self.profile); path.chmod(0o640)
        raw = h.Authority.plan(self.runtime, self.backups, self.lease_id, canonical_bytes(self.profile))
        self.resume = self.runtime.root / 'control' / ('resume-' + self.lease_id); self.resume.mkdir(mode=0o700)
        self.write(self.resume / 'plan.json', h.c._json(raw))
        self.write(self.resume / 'ready.json', h.binding_for(raw))
        self.authority = h.Authority.load(self.runtime, self.backups, self.lease_id)
        for name in h.MARKERS:
            path = self.gate / name; path.write_bytes(self.authority.marker_bytes(name)); path.chmod(0o600)

    def armed(self):
        root = self.backups / ('mobile-activation-' + self.lease_id); root.mkdir(mode=0o700)
        value = {'instance': self.runtime.web.spec.instance, 'lease_id': self.lease_id,
            'backup_root': str(self.backups), 'resume_plan_sha256': '5' * 64,
            'runtime': {'gateway_successor': self.authority.binding()}}
        self.write(root / 'plan.json', value)
        owner = {'version': 1, 'instance': value['instance'], 'lease_id': self.lease_id,
            'activation_plan_sha256': h.sha(canonical_bytes(value)), 'resume_plan_sha256': '5' * 64}
        self.write(root / 'armed.json', owner)
        raw = canonical_bytes({'handoff': self.authority.binding(), 'activation': owner})
        self.authority.save('activation-owner.json', raw)
        return raw

    def test_default_binding_remains_current_and_scope_only_projects_original_evidence(self):
        current = gd.binding(self.runtime)
        self.assertNotEqual(current, self.profile['gateway_service'])
        with self.authority.admitted():
            self.assertEqual(gd.binding(self.runtime), self.profile['gateway_service'])
            self.assertEqual(self.runtime.manifest(self.runtime.account()), self.value['target_manifest'])
        self.assertEqual(gd.binding(self.runtime), current)

    def test_scope_reset_on_failure_and_no_thread_leak(self):
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with self.authority.admitted():
                with ThreadPoolExecutor(max_workers=1) as pool: self.assertIsNone(pool.submit(h.current).result())
                raise RuntimeError('interrupted')
        self.assertIsNone(h.current())

    def test_nested_scope_wrong_http_and_original_runtime_refused(self):
        with self.authority.admitted():
            with self.assertRaises(h.c.g.GatewayStateError):
                with self.authority.admitted(): pass
            with self.assertRaises(h.c.g.GatewayStateError): h.current(object())
            with self.assertRaises(h.c.g.GatewayStateError): gd.binding(self.original)

    def test_process_change_refuses_cached_authority(self):
        with self.authority.admitted(), patch.object(h.os, 'getpid', return_value=os.getpid() + 1):
            with self.assertRaises(h.c.g.GatewayStateError): h.current()

    def test_envelope_adds_exact_three_records_without_excluding_subtrees(self):
        with self.authority.admitted():
            value = admission._envelope([], {}, {})
            self.assertEqual(value, {('configuration', 'maintenance/' + name):
                admission._private_record(name, self.authority.marker_bytes(name)) for name in h.MARKERS})
            with self.assertRaises(h.c.g.GatewayStateError):
                admission._envelope([admission._private_record(h.MARKER, self.authority.marker_bytes(h.MARKER))], {}, {})

    def test_activation_record_binds_both_profiles_and_handoff(self):
        account = SimpleNamespace(pw_uid=0, pw_gid=0, pw_name='fixture')
        self.assertNotIn('gateway_successor', native.runtime_profile(self.runtime.web, account))
        with self.authority.admitted():
            self.assertEqual(native.runtime_profile(self.runtime.web, account)['gateway_successor'], self.authority.binding())

    def test_missing_guard_without_armed_owner_is_not_consumption(self):
        for name in h.MARKERS:
            path = self.gate / name; raw = path.read_bytes(); path.unlink()
            with self.assertRaises(h.c.g.GatewayStateError): self.authority.markers()
            path.write_bytes(raw); path.chmod(0o600)

    def test_changed_publication_or_plan_refuses_cached_scope(self):
        with self.authority.admitted():
            path = self.resume / 'plan.json'; original = path.read_bytes(); path.write_bytes(b'{}')
            with self.assertRaises(h.c.g.GatewayStateError): gd.binding(self.runtime)
            path.write_bytes(original)
            (self.runtime.root / 'control' / h.a.ACTIVE).unlink()
            with self.assertRaises(h.c.g.GatewayStateError): gd.binding(self.runtime)

    def test_foreign_record_and_torn_ready_are_never_repaired(self):
        path = self.resume / 'foreign.json'; self.write(path, {})
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.check()
        path.unlink(); (self.resume / 'ready.json').write_bytes(b'{')
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.check()
        self.assertEqual((self.resume / 'ready.json').read_bytes(), b'{')

    def test_backup_replacement_with_identical_path_is_refused(self):
        self.backups.rename(self.root / 'old-backups'); self.backups.mkdir(mode=0o700)
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.check()

    def test_default_release_retains_all_transition_guards(self):
        fd = os.open(self.gate, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        barrier = SimpleNamespace(_lease=SimpleNamespace(_directory=fd))
        with self.assertRaises(Exception): h.release_admitted(self.runtime, barrier)

    def test_scoped_release_requires_exact_lease_and_historical_drain(self):
        lease = SimpleNamespace(assert_held=Mock(), lease_id=self.lease_id,
            scope=SimpleNamespace(directory=self.gate))
        barrier = SimpleNamespace(_lease=lease, _profile=canonical_bytes(self.profile))
        with self.authority.admitted():
            h.release_admitted(self.runtime, barrier)
            lease.lease_id = 'f' * 32
            with self.assertRaises(h.c.g.GatewayStateError): h.release_admitted(self.runtime, barrier)

    def test_lost_unlink_reply_requires_prefix_and_one_missing_receipt_only(self):
        owner = self.armed(); (self.gate / h.MARKERS[0]).unlink()
        self.assertFalse(self.authority.markers()[h.MARKERS[0]])
        (self.gate / h.MARKERS[1]).unlink()
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.markers()
        self.authority.save(h.MARKERS[0] + '.removed.json', self.authority.removed(h.MARKERS[0], owner))
        self.assertFalse(self.authority.markers()[h.MARKERS[1]])

    def test_out_of_order_unlink_and_receipt_before_unlink_refuse(self):
        owner = self.armed(); path = self.gate / h.MARKERS[1]; raw = path.read_bytes(); path.unlink()
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.markers()
        path.write_bytes(raw); path.chmod(0o600)
        self.authority.save(h.MARKERS[0] + '.removed.json', self.authority.removed(h.MARKERS[0], owner))
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.markers()

    def test_wrong_target_activation_cannot_authorize_consumption(self):
        self.armed(); path = self.backups / ('mobile-activation-' + self.lease_id) / 'plan.json'
        value = h.c._json(path.read_bytes()); value['runtime']['gateway_successor']['target_manifest_sha256'] = '0' * 64
        self.write(path, value)
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.markers()

    def test_completed_consumption_is_read_only_and_allows_no_guard_recreation(self):
        owner = self.armed()
        for name in h.MARKERS:
            (self.gate / name).unlink()
            self.authority.save(name + '.removed.json', self.authority.removed(name, owner))
        self.authority.save('consumed.json', owner)
        with patch.object(h.files, '_new', side_effect=AssertionError('replay')):
            with self.authority.admitted(): self.assertFalse(any(self.authority.markers().values()))
        path = self.gate / h.MARKER; path.write_bytes(self.authority.marker_bytes(h.MARKER)); path.chmod(0o600)
        with self.assertRaises(h.c.g.GatewayStateError): self.authority.markers()

    def test_final_consumption_refuses_fabricated_window_before_any_write(self):
        with self.authority.admitted(), patch.object(h.files, '_new', side_effect=AssertionError('write')):
            with self.assertRaises(h.c.g.GatewayStateError): self.authority.consume(Mock(), Mock())
