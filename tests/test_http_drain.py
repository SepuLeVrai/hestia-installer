"""Private controller fault tests; systemd/procfs qualification is separate."""
import contextlib
from pathlib import Path
import pickle
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from installer import http_drain as d, http_runtime as h, maintenance as m


class HttpDrainTests(unittest.TestCase):
    def setUp(self):
        spec = h.RuntimeSpec('a' * 32, Path('/var/lib/hestia-http-test'), Path('/srv/hestia-web-test'),
                             'hestia-http-test', 'hestia.test', 8123, '8.4')
        self.runtime = h.HttpRuntime(spec); self.drain = d.HttpDrain(self.runtime)
        self.account = SimpleNamespace(pw_uid=991, pw_gid=991)
        self.scope = self.runtime._scope(self.account)
        self.files = {d.s.UNIT_ROOT / self.runtime.unit(role): ('fragment-' + role).encode() for role in d.ROLES}

    @contextlib.contextmanager
    def controller(self):
        journal = {}
        scope = self.scope
        class Lease:
            lease_id = 'b' * 32
            closed = False
            def assert_held(self): m.require(not self.closed, 'MAINTENANCE_LEASE_REQUIRED')
            def close(self): self.closed = True
        lease = Lease(); lease.scope = scope
        def read(fd, name, gid):
            if name not in journal: raise FileNotFoundError
            return journal[name]
        def write(fd, name, raw, gid):
            if name in journal: raise FileExistsError
            journal[name] = raw
        with patch.object(self.runtime, '_inspect_configuration', return_value=(self.account, None, b'plan', {})), \
             patch.object(self.runtime, '_files', return_value=self.files), \
             patch.object(d.s, 'audit_unit') as audit, patch.object(d, 'identity_census') as census, \
             patch.object(m.MaintenanceScope, 'acquire', return_value=lease) as acquire, \
             patch.object(m.MaintenanceScope, 'recover', return_value=lease), \
             patch.object(d.fs, '_directory', side_effect=lambda *a, **k: contextlib.nullcontext(17)), \
             patch.object(d.f, '_read', side_effect=read), patch.object(d.f, '_write', side_effect=write), \
             patch.object(d.s, '_systemctl', return_value='') as command:
            yield SimpleNamespace(lease=lease, journal=journal, command=command, acquire=acquire, audit=audit, census=census)

    def test_input_is_typed_and_private(self):
        for value in (None, {}, 'systemctl stop apache2'):
            with self.assertRaisesRegex(d.HttpDrainError, 'INPUT_REJECTED'): d.HttpDrain(value)
        self.assertNotIn(str(self.runtime.spec.root), repr(self.drain))
        with self.assertRaisesRegex(d.HttpDrainError, 'INPUT_REJECTED'):
            self.drain.recover('../foreign', confirmed=True)

    def test_credentials_cover_all_four_ids_and_supplementary_groups(self):
        status = 'Name:\tfixture\nUid:\t1 2 3 4\nGid:\t5 6 7 8\nGroups:\t9 10\n'
        self.assertEqual(d._credentials(status), {'Uid': (1, 2, 3, 4), 'Gid': (5, 6, 7, 8), 'Groups': (9, 10)})
        for bad in (status + 'Uid: 1 2 3 4\n', status.replace('3 4', '4'), status.replace('9 10', '-1'), ''):
            with self.assertRaises(d.HttpDrainError): d._credentials(bad)

    def test_membership_requires_unified_cgroup_and_exact_subtree_boundary(self):
        unit = self.runtime.unit('php'); root = '0::/system.slice/' + unit
        self.assertTrue(d._membership(root + '\n', (unit,)))
        self.assertTrue(d._membership(root + '/child\n', (unit,)))
        self.assertFalse(d._membership(root + '-foreign\n', (unit,)))
        self.assertFalse(d._membership(root + '\n', ()))
        for bad in (root + '\n1:cpu:/foreign\n', '1:name=systemd:/system.slice/test\n', ''):
            with self.assertRaises(d.HttpDrainError): d._membership(bad, (unit,))

    def test_proc_stat_handles_parentheses_without_exposing_command(self):
        raw = '17 (odd ) name) S ' + ' '.join(['0'] * 18 + ['12345'])
        self.assertEqual(d._start(raw), ('S', '12345'))
        with self.assertRaisesRegex(d.HttpDrainError, 'CENSUS_REJECTED'): d._start('private truncated')

    def test_consent_root_and_cancellation_precede_any_observation_or_gate(self):
        with self.controller() as c:
            with self.assertRaisesRegex(d.HttpDrainError, 'CONSENT_REQUIRED'): self.drain.acquire(confirmed=1)
            with patch.object(d.os, 'geteuid', return_value=991), self.assertRaisesRegex(d.HttpDrainError, 'ROOT_REQUIRED'):
                self.drain.acquire(confirmed=True)
            cancel = threading.Event(); cancel.set()
            with self.assertRaisesRegex(d.HttpDrainError, 'INTERRUPTED'): self.drain.acquire(confirmed=True, cancel=cancel)
            c.acquire.assert_not_called(); c.command.assert_not_called(); c.census.assert_not_called()

    def test_two_real_unit_names_stop_in_order_and_report_never_certifies_other_writers(self):
        with self.controller() as c:
            with self.drain.acquire(confirmed=True) as lease:
                self.assertEqual([call.args for call in c.command.call_args_list],
                                 [('stop', self.runtime.unit(role)) for role in ('apache', 'php')])
                report = lease.report(); self.assertEqual(report['services'], 2)
                for key in ('other_producers_controlled', 'storage_inventory_complete', 'system_wiring_verified',
                            'complete_web_backup', 'service_activation_delivered', 'phase5_complete'):
                    self.assertIs(report[key], False)
                self.assertIs(lease.maintenance_lease, c.lease)
                self.assertEqual(c.census.call_args.args, (991, 991, ()))
            self.assertTrue(c.lease.closed); self.assertEqual(len(c.journal), 1)

    def test_foreign_identity_prevents_gate_and_is_never_signalled(self):
        with self.controller() as c:
            c.census.side_effect = d.HttpDrainError('HTTP_DRAIN_FOREIGN_IDENTITY_PROCESS')
            with self.assertRaisesRegex(d.HttpDrainError, 'FOREIGN_IDENTITY_PROCESS'): self.drain.acquire(confirmed=True)
            c.acquire.assert_not_called(); c.command.assert_not_called(); self.assertFalse(c.journal)

    def test_stop_failure_preserves_attempt_and_closes_without_resume(self):
        with self.controller() as c:
            c.command.side_effect = OSError('private host command failed')
            with self.assertRaisesRegex(d.HttpDrainError, '^HTTP_DRAIN_UNAVAILABLE$'): self.drain.acquire(confirmed=True)
            self.assertTrue(c.lease.closed); self.assertEqual(len(c.journal), 1); self.assertEqual(c.command.call_count, 1)

    def test_failed_admission_observation_prevents_php_stop(self):
        with self.controller() as c:
            def audit(scope, binding, *, stopped=False):
                if stopped and binding.role == 'apache': raise d.s.SystemDrainError('SYSTEM_DRAIN_NOT_EMPTY')
            c.audit.side_effect = audit
            with self.assertRaisesRegex(d.s.SystemDrainError, 'NOT_EMPTY'): self.drain.acquire(confirmed=True)
            self.assertEqual(c.command.call_count, 1); self.assertTrue(c.lease.closed)

    def test_cancellation_after_first_stop_retains_gate_attempt(self):
        with self.controller() as c:
            cancel = threading.Event(); c.command.side_effect = lambda *a: cancel.set()
            with self.assertRaisesRegex(d.HttpDrainError, 'INTERRUPTED'): self.drain.acquire(confirmed=True, cancel=cancel)
            self.assertEqual(c.command.call_count, 1); self.assertTrue(c.journal); self.assertTrue(c.lease.closed)

    def test_recovery_matches_saved_profile_and_rejects_drift_without_stop(self):
        with self.controller() as c:
            _, profile = self.drain._audit()
            name = 'http-drain-' + c.lease.lease_id + '.attempt'; c.journal[name] = profile
            with self.drain.recover(c.lease.lease_id, confirmed=True) as lease: self.assertEqual(lease.report()['services'], 2)
        with self.controller() as c:
            c.journal['http-drain-' + c.lease.lease_id + '.attempt'] = b'old-profile'
            with self.assertRaisesRegex(d.HttpDrainError, 'RECOVERY_MISMATCH'):
                self.drain.recover(c.lease.lease_id, confirmed=True)
            c.command.assert_not_called(); self.assertTrue(c.lease.closed)

    def test_live_receipt_rechecks_processes_profile_journal_and_closed_lease(self):
        with self.controller() as c:
            lease = self.drain.acquire(confirmed=True)
            with self.assertRaises(TypeError): pickle.dumps(lease)
            c.census.side_effect = d.HttpDrainError('HTTP_DRAIN_FOREIGN_IDENTITY_PROCESS')
            with self.assertRaisesRegex(d.HttpDrainError, 'FOREIGN_IDENTITY_PROCESS'): lease.report()
            c.census.side_effect = None
            with patch.object(self.runtime, '_inspect_configuration', return_value=(self.account, None, b'changed', {})), \
                 self.assertRaisesRegex(d.HttpDrainError, 'PROFILE_CHANGED'): lease.report()
            name = next(iter(c.journal)); original = c.journal[name]; c.journal[name] = b'drift'
            with self.assertRaisesRegex(d.HttpDrainError, 'RECOVERY_MISMATCH'): lease.report()
            c.journal[name] = original; lease.close()
            with self.assertRaisesRegex(m.MaintenanceError, 'LEASE_REQUIRED'): lease.report()


if __name__ == '__main__': unittest.main()
