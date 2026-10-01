"""Pure resume preparation contracts; no native filesystem, services or SQL."""
from copy import deepcopy
from pathlib import Path
import pickle
import unittest
from unittest.mock import Mock, patch

from installer import mobile_resume_plan as r
from installer.model import canonical_bytes


def profile():
    prefix = 'hestia-' + 'a' * 32 + '-'
    return {'policy': 'PROVISIONED_HTTP_AND_CLEANER_STOP_ONLY_V1',
        'units': [{'role': role, 'fragment_sha256': c * 64}
                  for role, c in zip(('apache', 'php', 'session-cleaner'), 'bcd')],
        'foundation': {'policy': 'GATED_FOUNDATION_STOP_ONLY_V1',
            'unit': prefix + 'foundation.service', 'manifest_sha256': 'e' * 64},
        'gateway_service': {'policy': 'GATED_GATEWAY_STOP_BEFORE_FOUNDATION_V1',
            'unit': prefix + 'gateway.service', 'manifest_sha256': 'f' * 64,
            'state_identity': {'device': 1, 'inode': 2}},
        'timer_sha256': '1' * 64, 'cleaner_plan_sha256': '2' * 64}


class ResumePlanPolicyTests(unittest.TestCase):
    def test_bound_order_does_not_include_cleaner_service_start(self):
        rows = r._services(profile(), 'a' * 32)
        self.assertEqual([v['role'] for v in rows], ['php', 'apache', 'foundation', 'gateway', 'timer'])
        self.assertTrue(rows[-1]['unit'].endswith('-session-cleaner.timer'))
        self.assertEqual(rows[-1]['cleaner_fragment_sha256'], 'd' * 64)

    def test_gateway_full_native_binding_including_state_inode_is_hashed(self):
        value = profile(); before = r._services(value, 'a' * 32)
        value['gateway_service']['state_identity']['inode'] += 1
        after = r._services(value, 'a' * 32)
        self.assertNotEqual(before[3], after[3]); self.assertEqual(before[:3], after[:3])

    def test_foreign_instance_and_native_units_are_refused(self):
        for instance in ('A' * 32, 'a' * 31, '../unit', None, 'a' * 32 + '\n'):
            with self.assertRaises(r.ResumePlanError): r._services(profile(), instance)
        for role in ('foundation', 'gateway_service'):
            value = profile(); value[role]['unit'] = 'foreign.service'
            with self.assertRaises(r.ResumePlanError): r._services(value, 'a' * 32)

    def test_public_ingress_and_incorrect_policies_are_refused(self):
        value = profile(); value['public_ingress'] = None
        with self.assertRaises(r.ResumePlanError): r._services(value, 'a' * 32)
        for field in (None, 'foundation', 'gateway_service'):
            value = profile(); target = value if field is None else value[field]; target['policy'] = 'other'
            with self.assertRaises(r.ResumePlanError): r._services(value, 'a' * 32)

    def test_roles_must_be_exact_order_unique_and_closed(self):
        for rows in ([], list(reversed(profile()['units'])), profile()['units'] * 2,
                     [{**profile()['units'][0], 'extra': True}, *profile()['units'][1:]]):
            value = profile(); value['units'] = rows
            with self.assertRaises(r.ResumePlanError): r._services(value, 'a' * 32)

    def test_every_hash_is_validated_before_recording(self):
        for bad in (None, True, 'A' * 64, 'a' * 63, 'a' * 64 + '\n', 'a' * 65536):
            for field in ('timer_sha256', 'cleaner_plan_sha256'):
                value = profile(); value[field] = bad
                with self.assertRaises(r.ResumePlanError): r._services(value, 'a' * 32)
            value = profile(); value['units'][0]['fragment_sha256'] = bad
            with self.assertRaises(r.ResumePlanError): r._services(value, 'a' * 32)
            value = profile(); value['foundation']['manifest_sha256'] = bad
            with self.assertRaises(r.ResumePlanError): r._services(value, 'a' * 32)

    def test_order_is_defensive_and_does_not_modify_profile(self):
        value = profile(); before = deepcopy(value); rows = r._services(value, 'a' * 32)
        rows[0].clear(); self.assertEqual(value, before)

    def test_consent_and_exact_window_type_precede_capture(self):
        with patch.object(r, '_capture') as capture:
            for consent in (False, None, 1, 'true'):
                with self.assertRaisesRegex(r.ResumePlanError, 'CONSENT_REQUIRED'): r.begin(None, confirmed=consent)
            for window in (object(), {}, Mock(spec=r.m.DataAdmissionWindow)):
                with self.assertRaisesRegex(r.ResumePlanError, 'LIVE_ADMISSION_REQUIRED'): r.recover(window, confirmed=True)
            capture.assert_not_called()

    def test_closed_window_cannot_authorize_any_preparation(self):
        window = r.m.DataAdmissionWindow(Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Path('/unused'), {})
        window._closed = True
        with patch.object(r, '_capture') as capture:
            with self.assertRaises(r.ResumePlanError): r.begin(window, confirmed=True)
            capture.assert_not_called()

    def test_confirmation_precedes_live_observation(self):
        raw = canonical_bytes({'start_order': []}); plan = r.ResumePlan(Path('/unused'), raw)
        with patch.object(r, '_inputs') as inputs:
            for op in (plan.prepare, plan.check):
                for consent, confirmation in ((False, plan.plan_sha256), (1, plan.plan_sha256), (True, 'wrong')):
                    with self.assertRaisesRegex(r.ResumePlanError, 'CONFIRMATION_REQUIRED'):
                        op(None, confirmation, confirmed=consent)
            inputs.assert_not_called()

    def test_plan_cannot_cross_process_or_pickle(self):
        raw = canonical_bytes({'start_order': []}); plan = r.ResumePlan(Path('/unused'), raw)
        with self.assertRaises(TypeError): pickle.dumps(plan)
        with patch.object(r.os, 'getpid', return_value=plan._pid + 1), self.assertRaises(r.ResumePlanError):
            plan._match((plan.root, raw, {}, {}))

    def test_mutated_in_memory_plan_or_digest_is_refused(self):
        raw = canonical_bytes({'start_order': []}); plan = r.ResumePlan(Path('/unused'), raw)
        plan.value['start_order'].append('other')
        with self.assertRaises(r.ResumePlanError): plan._match((plan.root, raw, {}, {}))
        plan.value['start_order'].clear(); plan.plan_sha256 = '0' * 64
        with self.assertRaises(r.ResumePlanError): plan._match((plan.root, raw, {}, {}))

    def test_historical_report_grants_no_current_admission_or_activity(self):
        raw = canonical_bytes({'start_order': [{'role': 'php'}]}); plan = r.ResumePlan(Path('/unused'), raw)
        report = plan._report()
        for name in ('current_admission', 'blockers_consumed', 'maintenance_released', 'services_started', 'phase6_complete'):
            self.assertIs(report[name], False)
        self.assertTrue(report['fresh_sql_admission_required_for_next_step'])
        report['start_order'][0].clear(); self.assertEqual(plan.value['start_order'][0], {'role': 'php'})

    def test_errors_do_not_disclose_private_details(self):
        window = r.m.DataAdmissionWindow(Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Path('/unused'), {})
        with patch.object(window, 'assert_held', side_effect=RuntimeError('private SQL credential')):
            with self.assertRaisesRegex(r.ResumePlanError, '^MOBILE_RESUME_PLAN_UNAVAILABLE$'): r.begin(window, confirmed=True)


if __name__ == '__main__': unittest.main()
