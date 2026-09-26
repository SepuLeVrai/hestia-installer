"""Controller and real pipe tests; actual D-Bus is a separate disposable recipe."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from installer import systemd_discovery_transport as t
import test_launcher_inventory as model
import test_systemd_discovery as source

NOW = model.NOW
SECRET = 'description-private-not-retained'


def encoded(signature, value): return json.dumps({'type': signature, 'data': [value]}).encode()


def rows(): return [['worker.service', SECRET, 'loaded', 'active', 'running', '', t.d.UNIT_PREFIX+'worker', 0, '', '/']]


def local():
    target = model.target()
    return {'identity': {'host_id': target.host_id, 'boot_id': target.boot_id, 'manager': 'system',
        'namespaces': {'pid': 'pid:[7]', 'mnt': 'mnt:[8]'}}, 'socket': (1, 2), 'client': (3, 4), 'broker_pid': 321}


class SystemdDiscoveryTransportTests(unittest.TestCase):
    def setUp(self):
        self.transport = t.SystemdDiscoveryTransport(model.target(), model.storage())
        self.loaded = rows(); self.files = [['/etc/systemd/system/unloaded@.service', 'disabled']]; self.jobs = []
        self.commands = []

    def reply(self, argv, budget):
        self.commands.append(argv)
        tail = argv[argv.index('call')+1:]; method = tail[3]
        if method == 'GetId': raw = encoded('s', 'e'*32)
        elif method == 'GetNameOwner': raw = encoded('s', ':1.0')
        elif method == 'GetConnectionUnixProcessID': raw = encoded('u', 321 if tail[-1] == t.BUS else 1)
        elif method == 'GetConnectionUnixUser': raw = encoded('u', 0)
        elif method == 'Get':
            sig, data = ('s', '257') if tail[-1] == 'Version' else ('as', ['/etc/systemd/system', '/usr/lib/systemd/system'])
            raw = encoded('v', {'type': sig, 'data': data})
        else: raw = encoded(t.SIGNATURES[method], {'ListUnits': self.loaded, 'ListUnitFiles': self.files, 'ListJobs': self.jobs}[method])
        budget.calls += 1; budget.bytes += len(raw)
        return raw

    @contextmanager
    def fixture(self, *, reply=None, locals=None):
        with patch.object(t, '_local', side_effect=locals, return_value=local()) as identity, \
             patch.object(t, '_capture', side_effect=reply or self.reply) as capture, \
             patch.object(t.time, 'time', return_value=NOW):
            yield identity, capture

    def test_two_rounds_three_populations_fixed_calls_and_no_authority(self):
        with self.fixture(): result = self.transport.collect()
        report = result.report()
        self.assertEqual(report['bus_calls'], 24); self.assertTrue(report['system_manager_lists_observed'])
        self.assertEqual((report['loaded_units'], report['installed_unit_files'], report['manager_jobs']), (1, 1, 0))
        self.assertEqual(result.scan().target, model.target())
        for key, value in report.items():
            if type(value) is bool and key != 'system_manager_lists_observed': self.assertFalse(value, key)
        self.assertEqual(len(self.commands), 24)
        for argv in self.commands:
            self.assertEqual(argv[0], '/usr/bin/busctl')
            self.assertIn('--auto-start=no', argv); self.assertIn('--allow-interactive-authorization=no', argv)
            self.assertIn('--address=unix:path=/run/dbus/system_bus_socket', argv)
            self.assertEqual(argv[argv.index('call')+1] in (t.BUS, ':1.0'), True)

    def test_descriptions_are_discarded_before_hashing_and_never_persisted(self):
        with self.fixture(): first = self.transport.collect()
        self.loaded[0][1] = 'different SECRET\ntext'
        with self.fixture(): second = self.transport.collect()
        self.assertEqual(first.index().private_manifest()['observation'], second.index().private_manifest()['observation'])
        self.assertNotIn(SECRET.encode(), first._canonical)
        self.assertIsNone(first.index().private_manifest()['observation']['loaded_units']['rows'][0]['names'])

    def test_population_order_is_canonical_for_evidence(self):
        a = rows()[0]; b = [*a]; b[0] = 'other.service'; b[6] += '2'
        one = t._population('ListUnits', encoded(t.SIGNATURES['ListUnits'], [a, b]))
        two = t._population('ListUnits', encoded(t.SIGNATURES['ListUnits'], [b, a]))
        self.assertEqual(one.evidence_sha256, two.evidence_sha256)

    def test_closed_operations_refuse_mutations_free_destinations_and_property_sweeps(self):
        for op in ('LoadUnit', 'StartUnit', 'GetAll', 'Environment', 'Set', 'systemctl', 'GetUnit'):
            with self.assertRaises(t.SystemdTransportError): t._argv(op, ':1.0')
        for owner in ('org.freedesktop.systemd1', '--address=evil', ':bad', '', None):
            with self.assertRaises(Exception): t._argv('ListUnits', owner)
        self.assertEqual(t._argv('Version', ':1.0')[-5:], [t.PROPERTIES, 'Get', 'ss', t.INTERFACE, 'Version'])

    def test_manager_pid_uid_bool_and_broker_identity_are_closed(self):
        for method, value in (('GetConnectionUnixUser', 1), ('GetConnectionUnixUser', False),
                              ('GetConnectionUnixProcessID', 999), ('GetConnectionUnixProcessID', True)):
            def reply(argv, budget):
                raw = self.reply(argv, budget)
                tail = argv[argv.index('call')+1:]
                return encoded('u', value) if tail[3] == method else raw
            with self.subTest(method=method, value=value), self.fixture(reply=reply), self.assertRaises(t.SystemdTransportError):
                self.transport.collect()

    def test_bus_id_owner_or_local_identity_change_rejects_without_retry(self):
        for method, value in (('GetId', 'f'*32), ('GetNameOwner', ':1.99')):
            seen = 0
            def reply(argv, budget):
                nonlocal seen
                raw = self.reply(argv, budget)
                if argv[argv.index('call')+4] == method:
                    seen += 1
                    if seen > 1: return encoded('s', value)
                return raw
            with self.fixture(reply=reply), self.assertRaisesRegex(t.SystemdTransportError, 'MANAGER_CHANGED'): self.transport.collect()
            self.assertEqual(seen, 2)
        altered = local(); altered['broker_pid'] = 999
        with self.fixture(locals=[local(), altered]), self.assertRaises(t.SystemdTransportError): self.transport.collect()

    def test_foreign_host_or_namespace_and_incomplete_properties_cannot_make_a_scan(self):
        altered = local(); altered['identity']['host_id'] = 'c'*32
        with self.fixture(locals=[altered]*4), self.assertRaises(t.SystemdTransportError): self.transport.collect()
        for data in (None, 'not-an-array', [None], ['/etc/../systemd']):
            def reply(argv, budget):
                raw = self.reply(argv, budget)
                return encoded('v', {'type': 'as', 'data': data}) if argv[-1] == 'UnitPath' else raw
            with self.fixture(reply=reply), self.assertRaises(t.SystemdTransportError): self.transport.collect()

    def test_unreadable_population_never_becomes_an_empty_success(self):
        for method in t.SIGNATURES:
            def reply(argv, budget):
                if argv[-1] == method: raise t.SystemdTransportError('DISCOVERY_BUS_UNREADABLE')
                return self.reply(argv, budget)
            with self.fixture(reply=reply), self.assertRaisesRegex(t.SystemdTransportError, 'UNREADABLE'): self.transport.collect()

    def test_population_changes_between_realistic_rounds_are_refused(self):
        count = 0
        def reply(argv, budget):
            nonlocal count
            if argv[-1] == 'ListUnits':
                count += 1
                if count == 2: self.loaded[0][3] = 'inactive'
            return self.reply(argv, budget)
        with self.fixture(reply=reply), self.assertRaises(t.SystemdTransportError): self.transport.collect()
        self.assertEqual(count, 2)

    def test_positive_job_tuple_and_unloaded_masked_template_are_retained(self):
        self.loaded[0][7:] = [9, 'start', t.d.JOB_PREFIX+'9']
        self.jobs = [[9, 'worker.service', 'start', 'running', t.d.JOB_PREFIX+'9', t.d.UNIT_PREFIX+'worker']]
        self.files[0][1] = 'masked'
        with self.fixture(): result = self.transport.collect()
        data = result.index().private_manifest()['observation']
        self.assertEqual(data['manager_jobs']['rows'][0]['identifier'], 9)
        self.assertIsNone(data['installed_unit_files']['rows'][0]['loaded_object'])

    def test_strict_json_signatures_dimensions_and_raw_types(self):
        for raw in (b'', b'[]', b'{"type":"s","type":"s","data":["x"]}', b'{"type":"s","data":[NaN]}',
                    b'{"type":"s","data":["x"],"extra":1}', encoded('u', 1), b'{"type":"s","data":"x"}',
                    b'{"type":"s","data":["x","y"]}'):
            with self.subTest(raw=raw), self.assertRaises(Exception): t._reply(raw, 's')
        for data in ({}, [rows()[0][:-1]], [[None]*10], 'private-text'):
            self.loaded = data
            with self.fixture(), self.assertRaises(t.SystemdTransportError): self.transport.collect()

    def test_reply_and_row_limits_apply_before_iteration(self):
        for op, maximum, value in (('ListUnits', 4096, rows()[0]), ('ListUnitFiles', 4096, self.files[0]), ('ListJobs', 1024, [1]*6)):
            with self.assertRaisesRegex(t.SystemdTransportError, 'ROW_LIMIT'):
                t._population(op, encoded(t.SIGNATURES[op], [value]*(maximum+1)))
        with self.assertRaisesRegex(t.SystemdTransportError, 'REPLY_REJECTED'): t._reply(b' '*(t.MAX_REPLY+1), 's')

    def test_previous_requires_same_context_and_full_index_and_no_time_reversal(self):
        with self.fixture(): previous = self.transport.collect(); self.transport.collect(previous=previous)
        altered = local(); altered['client'] = (8, 9)
        with self.fixture(locals=[altered]*4), self.assertRaisesRegex(t.SystemdTransportError, 'PREVIOUS_CONTEXT_CHANGED'):
            self.transport.collect(previous=previous)
        self.files.append(['/etc/systemd/system/new.service', 'static'])
        with self.fixture(), self.assertRaises(t.SystemdTransportError): self.transport.collect(previous=previous)
        with self.fixture(), self.assertRaises(t.SystemdTransportError): self.transport.collect(previous={})

    def test_private_reports_repr_and_errors_never_include_bus_stderr_or_descriptions(self):
        with self.fixture(): result = self.transport.collect()
        for obj in (result, self.transport): self.assertNotIn(SECRET, repr(obj))
        self.assertNotIn('worker.service', json.dumps(result.report()))
        data = result.private_manifest(); data['discovery'].clear(); self.assertTrue(result.private_manifest()['discovery'])
        with self.fixture(reply=lambda *args: (_ for _ in ()).throw(ValueError(SECRET))):
            try: self.transport.collect()
            except t.SystemdTransportError as exc: self.assertNotIn(SECRET, str(exc))

    def test_clock_reversal_and_expired_budget_are_refused(self):
        with self.fixture(), patch.object(t.time, 'time', side_effect=[NOW, NOW-1]), self.assertRaises(t.SystemdTransportError):
            self.transport.collect()
        budget = t._Budget(); budget.deadline = time.monotonic()-1
        with self.assertRaisesRegex(t.SystemdTransportError, 'TIMEOUT'): budget.remaining()

    def test_target_rejected_before_any_host_io(self):
        with patch.object(t, '_local', side_effect=AssertionError('host IO')):
            for target, storage in (({}, model.storage()), (model.target(), {})):
                with self.assertRaisesRegex(t.SystemdTransportError, 'TARGET_REJECTED'): t.SystemdDiscoveryTransport(target, storage)

    def test_local_preflight_failure_prevents_any_bus_connection(self):
        with self.fixture(locals=OSError('broker absent')) as (_, capture), self.assertRaises(t.SystemdTransportError):
            self.transport.collect()
        capture.assert_not_called()

    def test_real_pipe_output_bound_and_total_budget_kill_only_own_child(self):
        for reply_limit, used in ((100, 0), (t.MAX_REPLY, t.MAX_TOTAL-100)):
            budget = t._Budget(); budget.bytes = used
            with patch.object(t, 'MAX_REPLY', reply_limit), self.assertRaisesRegex(t.SystemdTransportError, 'TRANSPORT_LIMIT'):
                t._capture([sys.executable, '-c', 'import sys,time; sys.stdout.write("x"*101);sys.stdout.flush();time.sleep(5)'], budget)
            self.assertEqual(budget.bytes, used+101)

    def test_real_pipe_timeout_failure_and_empty_stream_are_bounded(self):
        with patch.object(t, 'CALL_SECONDS', .05), self.assertRaisesRegex(t.SystemdTransportError, 'CALL_TIMEOUT'):
            t._capture([sys.executable, '-c', 'import time;time.sleep(5)'], t._Budget())
        with self.assertRaisesRegex(t.SystemdTransportError, 'BUS_UNREADABLE'):
            t._capture([sys.executable, '-c', 'import sys;sys.stderr.write("SECRET");sys.exit(3)'], t._Budget())
        self.assertEqual(t._capture([sys.executable, '-c', 'pass'], t._Budget()), b'')

    def test_call_count_exhaustion_stops_before_spawning(self):
        budget = t._Budget(); budget.calls = t.MAX_CALLS
        with patch.object(t.subprocess, 'Popen') as spawn, self.assertRaises(t.SystemdTransportError): t._capture([], budget)
        spawn.assert_not_called()

    def test_child_has_fixed_environment_no_shell_stdin_stderr_and_cleanup(self):
        with patch.dict(os.environ, {'DBUS_SYSTEM_BUS_ADDRESS': 'unixexec:secret', 'LD_PRELOAD': 'secret'}), \
             patch.object(t.subprocess, 'Popen', wraps=subprocess.Popen) as spawn:
            raw = t._capture([sys.executable, '-c', 'import os;print(os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"));print(os.environ.get("LD_PRELOAD"))'], t._Budget())
        self.assertEqual(raw, b'None\nNone\n')
        kwargs = spawn.call_args.kwargs
        self.assertNotIn('shell', kwargs); self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL); self.assertTrue(kwargs['close_fds'])


if __name__ == '__main__': unittest.main()
