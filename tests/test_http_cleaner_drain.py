"""Explicit collector composition; other producers never enter its closed scope."""
import contextlib
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from installer import http_drain as d, http_runtime as h, session_cleaner as c
import test_http_drain as base


class HttpCleanerDrainTests(unittest.TestCase):
    def setUp(self):
        base.HttpDrainTests.setUp(self)
        self.cleaner = c.SessionCleaner(self.runtime)

    @contextlib.contextmanager
    def controller(self):
        with base.HttpDrainTests.controller(self) as state:
            files = {d.s.UNIT_ROOT / self.cleaner.unit: b'collector', d.s.UNIT_ROOT / self.cleaner.timer: b'timer'}
            with patch.object(self.cleaner, '_inspect_with_runtime', return_value=(
                     self.runtime._inspect_configuration(), (self.account, self.scope, files, b'cleaner-plan'))) as inspect, \
                 patch.object(self.cleaner, '_timer_state') as timer, patch.object(self.cleaner, '_stop_timer') as stop:
                self.drain = d.HttpDrain(self.runtime, cleaner=self.cleaner)
                state.inspect, state.timer, state.timer_stop = inspect, timer, stop
                yield state

    def test_collector_must_be_typed_and_bound_to_identical_runtime_object(self):
        for bad in (object(), {}, c.SessionCleaner(h.HttpRuntime(self.runtime.spec))):
            with self.assertRaisesRegex(d.HttpDrainError, 'COLLECTOR_MISMATCH'):
                d.HttpDrain(self.runtime, cleaner=bad)
        with self.controller() as state:
            self.cleaner.runtime = h.HttpRuntime(self.runtime.spec)
            with self.assertRaisesRegex(d.HttpDrainError, 'COLLECTOR_MISMATCH'): self.drain.acquire(confirmed=True)
            state.acquire.assert_not_called(); state.timer_stop.assert_not_called()

    def test_exact_three_services_are_enrolled_and_timer_stops_before_admission(self):
        with self.controller() as state:
            order = []
            state.timer_stop.side_effect = lambda: order.append('timer')
            state.command.side_effect = lambda action, unit: order.append(unit)
            with self.drain.acquire(confirmed=True) as lease:
                self.assertEqual(order, ['timer', self.runtime.unit('apache'), self.runtime.unit('php'), self.cleaner.unit])
                report = lease.report()
                self.assertEqual(report['state'], 'PROVISIONED_HTTP_AND_CLEANER_DRAINED')
                self.assertEqual((report['services'], report['timers_stopped']), (3, 1))
                self.assertFalse(report['other_producers_controlled']); self.assertFalse(report['storage_inventory_complete'])
                allowed = state.census.call_args_list[0].args[2]
                self.assertEqual(allowed, (self.runtime.unit('apache'), self.runtime.unit('php'), self.cleaner.unit))
                self.assertEqual(state.census.call_args.args[2], ())
                self.assertEqual(state.timer.call_args.kwargs, {'stopped': True})

    def test_collector_configuration_drift_refuses_before_gate_and_any_stop(self):
        with self.controller() as state:
            state.inspect.side_effect = c.SessionCleanerError('SESSION_CLEANER_REJECTED')
            with self.assertRaises(c.SessionCleanerError): self.drain.acquire(confirmed=True)
            state.acquire.assert_not_called(); state.timer_stop.assert_not_called(); state.command.assert_not_called()

    def test_timer_stop_failure_leaves_durable_attempt_without_stopping_http(self):
        with self.controller() as state:
            state.timer_stop.side_effect = c.SessionCleanerError('SESSION_CLEANER_TIMER_STOP_FAILED')
            with self.assertRaisesRegex(c.SessionCleanerError, 'TIMER_STOP_FAILED'): self.drain.acquire(confirmed=True)
            state.command.assert_not_called(); self.assertTrue(state.lease.closed); self.assertEqual(len(state.journal), 1)

    def test_rearmed_timer_after_first_stop_refuses_further_service_actions(self):
        with self.controller() as state:
            def timer(*, stopped=True):
                if stopped and state.command.call_count: raise c.SessionCleanerError('SESSION_CLEANER_REJECTED')
            state.timer.side_effect = timer
            with self.assertRaises(c.SessionCleanerError): self.drain.acquire(confirmed=True)
            self.assertEqual(state.command.call_count, 1); self.assertTrue(state.lease.closed); self.assertTrue(state.journal)

    def test_http_only_attempt_cannot_be_adopted_as_collector_drain(self):
        with self.controller() as state:
            _, old = d.HttpDrain(self.runtime)._audit()
            state.journal['http-drain-' + state.lease.lease_id + '.attempt'] = old
            with self.assertRaisesRegex(d.HttpDrainError, 'RECOVERY_MISMATCH'):
                self.drain.recover(state.lease.lease_id, confirmed=True)
            state.timer_stop.assert_not_called(); state.command.assert_not_called()

    def test_live_receipt_binds_collector_plan_and_rechecks_timer(self):
        with self.controller() as state:
            with self.drain.acquire(confirmed=True) as lease:
                original = state.inspect.return_value
                state.inspect.return_value = (original[0], (*original[1][:3], b'changed-plan'))
                with self.assertRaisesRegex(d.HttpDrainError, 'PROFILE_CHANGED'): lease.report()
                state.inspect.return_value = original
                state.timer.side_effect = c.SessionCleanerError('SESSION_CLEANER_REJECTED')
                with self.assertRaises(c.SessionCleanerError): lease.maintenance_lease


class CollectorTimerTests(unittest.TestCase):
    def setUp(self):
        self.cleaner = c.SessionCleaner(h.HttpRuntime(h.RuntimeSpec('a'*32, Path('/var/lib/hestia-cleaner'),
            Path('/srv/hestia-web'), 'hestia-web', 'hestia.test', 8123, '8.4')))

    def state(self, **changes):
        values = {'Id': self.cleaner.timer, 'LoadState': 'loaded', 'FragmentPath': str(c.s.UNIT_ROOT/self.cleaner.timer),
            'DropInPaths': '', 'NeedDaemonReload': 'no', 'ActiveState': 'inactive', 'SubState': 'dead',
            'Job': '', 'Unit': self.cleaner.unit}
        values.update(changes)
        return subprocess.CompletedProcess([],0, ''.join(k+'='+v+'\n' for k,v in values.items()).encode())

    def test_active_timer_is_only_accepted_for_explicit_lifecycle_inspection(self):
        with patch.object(c.subprocess,'run',return_value=self.state(ActiveState='active',SubState='waiting')):
            with self.assertRaises(c.SessionCleanerError): self.cleaner._timer_state()
            self.assertEqual(self.cleaner._timer_state(stopped=False)['ActiveState'],'active')
        for changes in ({'Unit':'foreign.service'}, {'Job':'123'}, {'NeedDaemonReload':'yes'},
                        {'DropInPaths':'/foreign.conf'}, {'ActiveState':'failed'}):
            with self.subTest(changes=changes), patch.object(c.subprocess,'run',return_value=self.state(**changes)), \
                 self.assertRaises(c.SessionCleanerError): self.cleaner._timer_state(stopped=False)

    def test_timer_stop_is_fixed_bounded_and_validated_before_and_after(self):
        with patch.object(self.cleaner,'_timer_state') as observe, \
             patch.object(c.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
            self.cleaner._stop_timer()
            self.assertEqual([call.kwargs for call in observe.call_args_list], [{'stopped':False},{}])
            argv = run.call_args.args[0]
            self.assertEqual(argv, ['/usr/bin/systemctl','--no-pager','--no-ask-password','stop','--',self.cleaner.timer])
            self.assertEqual(run.call_args.kwargs['timeout'],5); self.assertNotIn('shell',run.call_args.kwargs)
            run.return_value = subprocess.CompletedProcess([],1)
            with self.assertRaisesRegex(c.SessionCleanerError,'TIMER_STOP_FAILED'): self.cleaner._stop_timer()


if __name__=='__main__': unittest.main()
