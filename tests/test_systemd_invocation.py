"""Adversarial controllers and actual local PIDFD lifecycle, without host bus IO."""
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from installer import systemd_invocation as v
import test_systemd_discovery_transport as base

ID = '1a'*16
PATH = v.d.UNIT_PREFIX+'worker'
HINT = v.InvocationHint(PATH, 4321)

class SystemdInvocationTests(unittest.TestCase):
    def setUp(self):
        self.base = base.SystemdDiscoveryTransportTests(); self.base.setUp()
        self.transport = v.SystemdInvocationTransport(base.model.target(), base.model.storage())
        self.commands = []; self.mapping = [PATH, 'worker.service', list(bytes.fromhex(ID))]

    def reply(self, argv, budget, *, pass_fds=()):
        self.commands.append((argv, pass_fds))
        tail = argv[argv.index('call')+1:]; method = tail[3]
        if method == 'GetUnitByPIDFD': raw = json.dumps({'type': 'osay', 'data': self.mapping}).encode()
        elif method == 'GetUnitByInvocationID': raw = base.encoded('o', v._path(ID))
        elif method == 'Get' and tail[-1] in ('Id', 'InvocationID'):
            signature, value = ('s', 'worker.service') if tail[-1] == 'Id' else ('ay', list(bytes.fromhex(ID)))
            raw = base.encoded('v', {'type': signature, 'data': value})
        else: return self.base.reply(argv, budget)
        budget.calls += 1; budget.bytes += len(raw)
        return raw

    @contextmanager
    def fixture(self, reply=None, opened=None, alive=None):
        with self.base.fixture(reply=reply or self.reply), \
             patch.object(v.os, 'pidfd_open', side_effect=opened, return_value=91) as op, \
             patch.object(v.os, 'get_inheritable', return_value=False), \
             patch.object(v.os, 'close') as close, \
             patch.object(v, '_alive', side_effect=alive):
            yield op, close

    def test_two_passes_same_owned_fd_canonical_path_and_no_authority(self):
        with self.fixture() as (opened, close): result = self.transport.collect((HINT,))
        opened.assert_called_once_with(4321, 0); close.assert_called_once_with(91)
        self.assertEqual(result.report()['bus_calls'], 32)
        self.assertEqual(result.private_manifest()['bindings'][0]['invocation_id'], ID)
        self.assertFalse(result.report()['drain_allowed']); self.assertFalse(result.report()['phase5_complete'])
        for argv, fds in self.commands:
            tail = argv[argv.index('call')+1:]
            self.assertEqual(fds, (91,) if tail[3] == 'GetUnitByPIDFD' else ())
            if tail[3] == 'Get' and tail[-1] in ('Id', 'InvocationID'):
                self.assertEqual(tail[1], v.d.UNIT_PREFIX+'_31'+ID[1:])
            self.assertIn('--auto-start=no', argv)
        self.assertEqual(len(self.commands), 32)

    def test_malformed_hints_rejected_before_host_io(self):
        cases = [(), [], (None,), (HINT, HINT), (v.InvocationHint(PATH, False),),
                 (v.InvocationHint(PATH, 0),), (v.InvocationHint(PATH, 1),),
                 (v.InvocationHint(PATH, 2**31),), (v.InvocationHint('/other', 2),),
                 (HINT, v.InvocationHint(PATH+'2', 4321)), (HINT, v.InvocationHint(PATH, 4322)), (HINT,)*129]
        with patch.object(v.t, '_local', side_effect=AssertionError('host IO')) as host:
            for value in cases:
                with self.subTest(value=value), self.assertRaises(v.t.SystemdTransportError): self.transport.collect(value)
            host.assert_not_called()

    def test_unknown_object_refused_before_pidfd_open(self):
        with self.fixture() as (opened, close), self.assertRaisesRegex(v.t.SystemdTransportError, 'NOT_LISTED'):
            self.transport.collect((v.InvocationHint(PATH+'unknown', 4321),))
        opened.assert_not_called(); close.assert_not_called()

    def test_unsupported_manager_version_refused_without_fallback(self):
        def reply(argv, budget, **kw):
            raw = self.reply(argv, budget, **kw)
            return base.encoded('v', {'type': 's', 'data': '252.39'}) if argv[-1] == 'Version' else raw
        with self.fixture(reply) as (opened, _), self.assertRaisesRegex(v.t.SystemdTransportError, 'PROFILE_UNSUPPORTED'):
            self.transport.collect((HINT,))
        opened.assert_not_called()

    def test_open_failure_closes_all_previously_owned_descriptors(self):
        extra = list(self.base.loaded[0]); extra[0] = 'worker2.service'; extra[6] = PATH+'2'; self.base.loaded.append(extra)
        with self.fixture(opened=[91, OSError('private')]) as (_, close), self.assertRaises(v.t.SystemdTransportError):
            self.transport.collect((HINT, v.InvocationHint(PATH+'2', 4322)))
        close.assert_called_once_with(91)
        self.assertFalse(any('GetUnitByPIDFD' in a for a, _ in self.commands))

    def test_process_exit_at_each_checkpoint_rejects_and_closes(self):
        for checkpoint in range(9):
            alive = [None]*checkpoint + [v.t.SystemdTransportError('INVOCATION_PROCESS_EXITED')]
            with self.subTest(checkpoint=checkpoint), self.fixture(alive=alive) as (_, close), \
                 self.assertRaisesRegex(v.t.SystemdTransportError, 'PROCESS_EXITED'):
                self.transport.collect((HINT,))
            close.assert_called_once_with(91)

    def test_wrong_pid_mapping_and_null_id_stop_before_any_property(self):
        for path, name, identifier in [(PATH+'other', 'worker.service', list(bytes.fromhex(ID))),
                                      (PATH, 'foreign.service', list(bytes.fromhex(ID))),
                                      (PATH, 'worker.service', [0]*16)]:
            self.commands = []; self.mapping = [path, name, identifier]
            with self.fixture(), self.assertRaises(v.t.SystemdTransportError): self.transport.collect((HINT,))
            self.assertEqual(len(self.commands), 13)

    def test_mapping_json_is_strict_and_has_three_results(self):
        cases = [b'', b'{}', b'{"type":"osay","data":[],"x":0}',
                 b'{"type":"osay","type":"osay","data":[]}', b'{"type":"osay","data":[NaN]}',
                 json.dumps({'type': 'osay', 'data': [self.mapping]}).encode()]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(Exception): v._mapping(raw)

    def test_invocation_bytes_and_path_are_closed(self):
        for value in (None, bytes(16), [0]*16, [True]*16, [-1]*16, [256]*16, [1]*15, [1]*17):
            with self.subTest(value=value), self.assertRaises(v.t.SystemdTransportError): v._identifier(value)
        for value in ('0'*32, 'A'*32, '1'*31, '1'*33, 'self', 'worker.service', None):
            with self.subTest(value=value), self.assertRaises(v.t.SystemdTransportError): v._path(value)
        for first in '0123456789abcdef':
            identifier = first+'a'*31
            expected = ('_'+format(ord(first), '02x') if first.isdigit() else first)+'a'*31
            self.assertEqual(v._path(identifier), v.d.UNIT_PREFIX+expected)

    def test_named_or_noncanonical_returned_path_never_reaches_property(self):
        for path in (PATH, v._path(ID)+'/extra', v.d.UNIT_PREFIX+ID, v.d.UNIT_PREFIX+'self', None):
            self.commands = []
            def reply(argv, budget, **kw):
                raw = self.reply(argv, budget, **kw)
                return base.encoded('o', path) if 'GetUnitByInvocationID' in argv else raw
            with self.subTest(path=path), self.fixture(reply), self.assertRaisesRegex(v.t.SystemdTransportError, 'PATH_REJECTED'):
                self.transport.collect((HINT,))
            self.assertEqual(len(self.commands), 14)

    def test_properties_types_values_and_variant_extra_keys_refused(self):
        cases = [('Id', {'type': 's', 'data': 'foreign.service'}), ('Id', {'type': 's', 'data': []}),
                 ('Id', {'type': 's', 'data': 'worker.service', 'extra': 1}),
                 ('InvocationID', {'type': 'as', 'data': []}),
                 ('InvocationID', {'type': 'ay', 'data': [2]*16})]
        for prop, value in cases:
            def reply(argv, budget, **kw):
                raw = self.reply(argv, budget, **kw)
                return base.encoded('v', value) if argv[-1] == prop else raw
            with self.subTest(prop=prop, value=value), self.fixture(reply), self.assertRaises(v.t.SystemdTransportError):
                self.transport.collect((HINT,))

    def test_second_pass_invocation_change_is_refused_even_when_consistent(self):
        calls = 0; changed = 'ab'*16
        def reply(argv, budget, **kw):
            nonlocal calls
            raw = self.reply(argv, budget, **kw)
            if 'GetUnitByPIDFD' in argv: calls += 1
            if calls < 2: return raw
            if 'GetUnitByPIDFD' in argv:
                return json.dumps({'type': 'osay', 'data': [PATH, 'worker.service', list(bytes.fromhex(changed))]}).encode()
            if 'GetUnitByInvocationID' in argv: return base.encoded('o', v._path(changed))
            if argv[-1] == 'InvocationID': return base.encoded('v', {'type': 'ay', 'data': list(bytes.fromhex(changed))})
            return raw
        with self.fixture(reply), self.assertRaisesRegex(v.t.SystemdTransportError, 'INVOCATION_CHANGED'):
            self.transport.collect((HINT,))

    def test_population_change_rejects_and_closes(self):
        seen = 0
        def reply(argv, budget, **kw):
            nonlocal seen
            raw = self.reply(argv, budget, **kw)
            if argv[-1] == 'ListUnits':
                seen += 1
                if seen == 2: return base.encoded(v.t.SIGNATURES['ListUnits'], [])
            return raw
        with self.fixture(reply) as (_, close), self.assertRaises(v.t.SystemdTransportError): self.transport.collect((HINT,))
        close.assert_called_once_with(91)

    def test_call_failure_is_not_retried_and_private_error_not_exposed(self):
        def reply(argv, budget, **kw):
            if 'GetUnitByPIDFD' in argv: raise OSError('private-unit-and-pid')
            return self.reply(argv, budget, **kw)
        with self.fixture(reply) as (_, close):
            with self.assertRaises(v.t.SystemdTransportError) as caught: self.transport.collect((HINT,))
        self.assertNotIn('private', str(caught.exception)); close.assert_called_once_with(91)
        self.assertEqual(len(self.commands), 12)

    def test_closed_commands_and_fd_selection(self):
        for operation in ('LoadUnit', 'GetAll', 'RefUnit', 'GetUnitByPID', 'Names', 'Environment', 'Unit'):
            with self.assertRaises(v.t.SystemdTransportError): v._argv(operation, ':1.0', identifier=ID)
        for fd in (None, True, 0, 2, '91'):
            with self.assertRaises(v.t.SystemdTransportError): v._argv('GetUnitByPIDFD', ':1.0', fd=fd)
        with self.assertRaises(v.t.SystemdTransportError): v._argv('Id', ':1.0', fd=91, identifier=ID)

    def test_budget_extension_is_exact_and_bounded(self):
        for count in (0, 1, 128): self.assertEqual(v.t._Budget(invocation_pairs=count).maximum_calls, 24+8*count)
        for count in (True, -1, 129, '1'):
            with self.assertRaises(v.t.SystemdTransportError): v.t._Budget(invocation_pairs=count)
        budget = v.t._Budget(invocation_pairs=1); budget.calls = 32
        with patch.object(v.t.subprocess, 'Popen') as spawn, self.assertRaises(v.t.SystemdTransportError): v.t._capture([], budget)
        spawn.assert_not_called()

    def test_inheritable_fd_rejected_and_closed(self):
        with self.fixture() as (_, close), patch.object(v.os, 'get_inheritable', return_value=True), \
             self.assertRaisesRegex(v.t.SystemdTransportError, 'FD_REJECTED'): self.transport.collect((HINT,))
        close.assert_called_once_with(91)

    def test_cleanup_attempts_every_fd_even_on_close_failure(self):
        extra = list(self.base.loaded[0]); extra[0] = 'worker2.service'; extra[6] = PATH+'2'; self.base.loaded.append(extra)
        with self.fixture(opened=[91, 92], alive=[None, None, OSError()]) as (_, close):
            close.side_effect = [OSError(), None]
            with self.assertRaisesRegex(v.t.SystemdTransportError, 'FD_CLOSE_FAILED'):
                self.transport.collect((HINT, v.InvocationHint(PATH+'2', 4322)))
        self.assertEqual([c.args for c in close.call_args_list], [(91,), (92,)])

    def test_manifest_bounds_reports_and_repr_keep_private_binding_out(self):
        with self.fixture(): sample = self.transport.collect((HINT,))
        for value in (repr(sample), repr(HINT), repr(self.transport), json.dumps(sample.report())):
            for private in (ID, PATH, 'worker.service', '4321'): self.assertNotIn(private, value)
        changed = sample.private_manifest(); changed['bindings'].clear()
        self.assertEqual(len(sample.private_manifest()['bindings']), 1)
        with self.fixture(), patch.object(v.d, 'MAX_BYTES', 10), self.assertRaises(v.t.SystemdTransportError):
            self.transport.collect((HINT,))

    def test_actual_pidfd_is_cloexec_detects_exit_and_does_not_signal_target(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
        try:
            fd = os.pidfd_open(child.pid, 0)
            try:
                self.assertFalse(os.get_inheritable(fd)); v._alive(fd); self.assertIsNone(child.poll())
                child.terminate(); child.wait(timeout=3)
                with self.assertRaisesRegex(v.t.SystemdTransportError, 'PROCESS_EXITED'): v._alive(fd)
            finally: os.close(fd)
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=3)

    def test_actual_child_inherits_only_selected_fd_and_target_survives(self):
        fd = os.pidfd_open(os.getpid(), 0); other = os.open('/dev/null', os.O_RDONLY)
        try:
            code = 'import os;print(os.readlink("/proc/self/fd/'+str(fd)+'"));print(os.path.exists("/proc/self/fd/'+str(other)+'"))'
            raw = v.t._capture([sys.executable, '-c', code], v.t._Budget(), pass_fds=(fd,))
            self.assertEqual(raw, b'anon_inode:[pidfd]\nFalse\n'); v._alive(fd)
            self.assertFalse(os.get_inheritable(fd))
        finally: os.close(fd); os.close(other)

if __name__ == '__main__': unittest.main()
