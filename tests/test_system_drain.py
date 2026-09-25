"""Fault injection for the private stop-only adapter; live systemd is separate."""
import contextlib
import hashlib
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from installer import maintenance as m
from installer import system_drain as s


class SystemDrainTests(unittest.TestCase):
    def setUp(self):
        self.scope = m.MaintenanceScope(Path('/var/lib/hestia-instance/maintenance'), 65534, 'b' * 32)
        self.fragment = b'[Service]\nType=simple\nExecStart=/usr/bin/true\n'
        self.bindings = tuple(s.UnitBinding(role, hashlib.sha256(self.fragment).hexdigest()) for role in s.ROLES)
        self.drain = s.SystemDrain(self.scope, self.bindings)

    def states(self, unit):
        return {'Id': unit, 'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
            'FragmentPath': '/etc/systemd/system/' + unit,
            'DropInPaths': '/etc/systemd/system/' + unit + '.d/50-hestia-maintenance.conf',
            'NeedDaemonReload': 'no', 'KillMode': 'control-group', 'SendSIGKILL': 'yes', 'Delegate': 'no',
            'Slice': 'system.slice', 'ControlGroup': '', 'MainPID': '0', 'ControlPID': '0',
            'Type': 'simple', 'Restart': 'no', 'RemainAfterExit': 'no', 'RefuseManualStop': 'no',
            'Job': '', 'Result': 'success'}

    @contextlib.contextmanager
    def host(self, *, changes=None, empty=True):
        def show(unit):
            value = self.states(unit)
            value.update(changes or {})
            return value
        def read(path):
            return self.drain._dropin if path.name.endswith('.conf') else self.fragment
        with patch.object(s, '_root_file', side_effect=read), patch.object(s, '_show', side_effect=show), \
             patch.object(s, '_empty_cgroup', return_value=empty):
            yield

    @contextlib.contextmanager
    def controller(self):
        journal = {}
        class Lease:
            lease_id = 'c' * 32
            closed = False
            def assert_held(self):
                m.require(not self.closed, 'MAINTENANCE_LEASE_REQUIRED')
            def close(self):
                self.closed = True
        lease = Lease()
        def read(fd, name, gid):
            if name not in journal:
                raise FileNotFoundError
            return journal[name]
        def write(fd, name, data, gid):
            if name in journal:
                raise FileExistsError
            journal[name] = data
        with self.host(), patch.object(m.MaintenanceScope, 'acquire', return_value=lease) as acquire, \
             patch.object(m.MaintenanceScope, 'recover', return_value=lease), \
             patch.object(s.fs, '_directory', side_effect=lambda *a, **k: contextlib.nullcontext(17)), \
             patch.object(s.f, '_read', side_effect=read), patch.object(s.f, '_write', side_effect=write), \
             patch.object(s, '_systemctl', return_value='') as command:
            yield lease, journal, command, acquire

    def test_closed_profile_rejects_missing_duplicate_unknown_and_reordered_roles(self):
        for bindings in ((), self.bindings[:-1], self.bindings[::-1], (self.bindings[0],) * 4,
                         (s.UnitBinding('foreign', 'a' * 64),) + self.bindings[1:], list(self.bindings)):
            with self.subTest(bindings=bindings), self.assertRaisesRegex(s.SystemDrainError, 'PROFILE_REJECTED'):
                s.SystemDrain(self.scope, bindings)

    def test_profile_requires_exact_fragment_hashes_and_owned_instance_names(self):
        for value in ('a' * 63, 'A' * 64, '../etc', None, ''):
            with self.assertRaisesRegex(s.SystemDrainError, 'PROFILE_REJECTED'):
                s.SystemDrain(self.scope, (s.UnitBinding('apache', value),) + self.bindings[1:])
        self.assertEqual(self.drain.unit('php'), 'hestia-' + 'b' * 32 + '-php.service')
        with self.assertRaises(s.SystemDrainError):
            self.drain.unit('mariadb')

    def test_dropin_rejects_directive_injection_specifiers_and_path_traversal(self):
        for path in ('/var/lib/evil\nRestart=yes', '/var/lib/%i', '/var/lib/a/../b', '/var/lib/with space'):
            scope = m.MaintenanceScope(Path(path), 65534, 'b' * 32)
            with self.assertRaisesRegex(s.SystemDrainError, 'PROFILE_REJECTED'):
                s.condition_dropin(scope)
        self.assertEqual(self.drain._dropin,
            b'[Unit]\nConditionPathExists=!/var/lib/hestia-instance/maintenance/maintenance.attempt\n')

    def test_stop_only_command_rejects_foreign_units_and_other_verbs(self):
        with patch.object(s.subprocess, 'run') as run:
            for action, unit in (('start', self.drain.unit('php')), ('stop', 'php8.4-fpm.service'),
                                 ('stop', '--all'), ('kill', self.drain.unit('apache'))):
                with self.assertRaisesRegex(s.SystemDrainError, 'COMMAND_REJECTED'):
                    s._systemctl(action, unit)
            run.assert_not_called()

    def test_command_is_argv_only_bounded_and_strips_bus_and_shell_environment(self):
        with patch.object(s.p, '_safe_path'), patch.object(s.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            s._systemctl('stop', self.drain.unit('php'))
        args, kwargs = run.call_args
        self.assertEqual(args[0][-2:], ['--', self.drain.unit('php')])
        self.assertEqual(kwargs['timeout'], 40)
        self.assertNotIn('shell', kwargs)
        self.assertEqual(set(kwargs['env']), {'PATH', 'LANG', 'SYSTEMD_COLORS', 'SYSTEMD_PAGER'})
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    def test_show_requires_all_properties_without_duplicate_or_unknown_values(self):
        good = '\n'.join(k + '=' + v for k, v in self.states(self.drain.unit('php')).items()) + '\n'
        with patch.object(s, '_systemctl', return_value=good):
            self.assertEqual(s._show(self.drain.unit('php'))['MainPID'], '0')
        for value in (good + 'MainPID=1\n', good + 'Environment=secret\n', 'Id=only\n', 'bad'):
            with patch.object(s, '_systemctl', return_value=value), self.assertRaisesRegex(s.SystemDrainError, 'STATE_REJECTED'):
                s._show(self.drain.unit('php'))

    def test_fragment_or_dropin_drift_prevents_any_service_action(self):
        with self.host(), patch.object(s, '_root_file', return_value=b'changed'), \
             patch.object(s, '_systemctl') as command, self.assertRaisesRegex(s.SystemDrainError, 'UNIT_DRIFT'):
            self.drain.acquire(confirmed=True)
        command.assert_not_called()

    def test_loaded_units_refuse_reload_extra_dropins_delegation_restart_and_weak_kill(self):
        for key, value in {'NeedDaemonReload': 'yes', 'DropInPaths': '/foreign.conf', 'Delegate': 'yes',
                           'KillMode': 'process', 'SendSIGKILL': 'no', 'Restart': 'always',
                           'Slice': 'foreign.slice', 'RefuseManualStop': 'yes', 'Job': '91',
                           'ControlGroup': '/system.slice/foreign.service'}.items():
            with self.subTest(key=key), self.host(changes={key: value}), \
                 self.assertRaisesRegex(s.SystemDrainError, 'UNIT_REJECTED'):
                self.drain._audit(self.bindings[0])

    def test_inactive_pid_zero_is_insufficient_when_recursive_cgroup_is_populated(self):
        with self.host(empty=False), self.assertRaisesRegex(s.SystemDrainError, 'NOT_EMPTY'):
            self.drain._audit(self.bindings[1], stopped=True)

    def test_stopped_proof_refuses_failed_active_queued_or_control_process(self):
        for key, value in {'ActiveState': 'active', 'SubState': 'running', 'Result': 'timeout',
                           'MainPID': '42', 'ControlPID': '43'}.items():
            with self.subTest(key=key), self.host(changes={key: value}), \
                 self.assertRaisesRegex(s.SystemDrainError, 'NOT_EMPTY'):
                self.drain._audit(self.bindings[0], stopped=True)

    def test_confirmation_root_and_pre_cancel_are_checked_before_gate(self):
        with self.controller() as (_, journal, command, acquire):
            with self.assertRaisesRegex(s.SystemDrainError, 'CONSENT_REQUIRED'):
                self.drain.acquire(confirmed=False)
            with patch.object(s.os, 'geteuid', return_value=33), self.assertRaisesRegex(s.SystemDrainError, 'ROOT_REQUIRED'):
                self.drain.acquire(confirmed=True)
            cancel = threading.Event(); cancel.set()
            with self.assertRaisesRegex(s.SystemDrainError, 'INTERRUPTED'):
                self.drain.acquire(confirmed=True, cancel=cancel)
            acquire.assert_not_called(); command.assert_not_called(); self.assertFalse(journal)

    def test_admission_stops_first_and_live_report_stays_narrow_and_private(self):
        with self.controller() as (lease, journal, command, _):
            result = self.drain.acquire(confirmed=True)
            self.assertEqual([c.args for c in command.call_args_list],
                             [('stop', self.drain.unit(role)) for role in s.ROLES])
            self.assertEqual(len(journal), 1)
            report = result.report()
            self.assertTrue(report['cgroup_empty_verified'])
            for key in ('system_wiring_verified', 'storage_inventory_complete', 'complete_web_backup',
                        'apply_allowed', 'rollback_verified', 'application_installed', 'activity_resumed'):
                self.assertIs(report[key], False)
            self.assertNotIn(str(self.scope.directory), str(report) + repr(result) + repr(self.drain))
            self.assertIs(result.maintenance_lease, lease)
            result.close(); self.assertTrue(lease.closed); self.assertEqual(len(journal), 1)

    def test_partial_stop_timeout_closes_lock_but_preserves_durable_attempt(self):
        with self.controller() as (lease, journal, command, _):
            command.side_effect = ['', subprocess.TimeoutExpired(['private'], 40)]
            with self.assertRaisesRegex(s.SystemDrainError, '^SYSTEM_DRAIN_UNAVAILABLE$'):
                self.drain.acquire(confirmed=True)
            self.assertTrue(lease.closed); self.assertEqual(len(journal), 1)
            self.assertEqual(command.call_count, 2)

    def test_cancellation_after_reservation_retains_attempt_without_reopening(self):
        with self.controller() as (lease, journal, command, _):
            cancel = threading.Event()
            command.side_effect = lambda *a: cancel.set()
            with self.assertRaisesRegex(s.SystemDrainError, 'INTERRUPTED'):
                self.drain.acquire(confirmed=True, cancel=cancel)
            self.assertTrue(lease.closed); self.assertEqual(len(journal), 1); self.assertEqual(command.call_count, 1)

    def test_explicit_same_profile_recovery_rechecks_and_idempotently_stops(self):
        with self.controller() as (lease, journal, command, _):
            journal['system-drain-' + lease.lease_id + '.attempt'] = self.drain._profile
            with self.drain.recover(lease.lease_id, confirmed=True) as result:
                self.assertTrue(result.report()['cgroup_empty_verified'])
            self.assertTrue(lease.closed); self.assertEqual(command.call_count, 4)

    def test_changed_profile_cannot_recover_existing_attempt(self):
        with self.controller() as (lease, journal, command, _):
            journal['system-drain-' + lease.lease_id + '.attempt'] = b'changed'
            with self.assertRaisesRegex(s.SystemDrainError, 'RECOVERY_MISMATCH'):
                self.drain.recover(lease.lease_id, confirmed=True)
            command.assert_not_called(); self.assertTrue(lease.closed)

    def test_lease_rechecks_service_and_journal_and_cannot_serialize_or_reuse_closed(self):
        with self.controller() as (lease, journal, _, _):
            result = self.drain.acquire(confirmed=True)
            with self.assertRaises(TypeError):
                pickle.dumps(result)
            with patch.object(self.drain, '_audit_all', side_effect=s.SystemDrainError('SYSTEM_DRAIN_NOT_EMPTY')), \
                 self.assertRaisesRegex(s.SystemDrainError, 'NOT_EMPTY'):
                result.report()
            name = next(iter(journal)); original = journal[name]; journal[name] = b'drift'
            with self.assertRaisesRegex(s.SystemDrainError, 'RECOVERY_MISMATCH'):
                result.report()
            journal[name] = original; result.close()
            with self.assertRaisesRegex(m.MaintenanceError, 'LEASE_REQUIRED'):
                result.report()

    def test_root_file_refuses_hardlink_symlink_permissions_and_foreign_parent(self):
        root = Path(tempfile.mkdtemp(dir='/var/lib', prefix='hestia-system-unit-'))
        self.addCleanup(lambda: shutil.rmtree(root))
        path = root / 'unit.service'; path.write_bytes(self.fragment); path.chmod(0o644)
        self.assertEqual(s._root_file(path), self.fragment)
        path.chmod(0o666)
        with self.assertRaises(Exception): s._root_file(path)
        path.chmod(0o644); os.link(path, root / 'hardlink')
        with self.assertRaises(Exception): s._root_file(path)
        (root / 'hardlink').unlink(); (root / 'link').symlink_to(path)
        with self.assertRaises(Exception): s._root_file(root / 'link')
        root.chmod(0o777)
        with self.assertRaises(Exception): s._root_file(path)


if __name__ == '__main__':
    unittest.main()
