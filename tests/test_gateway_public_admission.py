"""Scoped binding contracts only; native admission is qualified separately."""
from copy import deepcopy
import os
import unittest
from unittest.mock import patch

from installer import gateway_public_admission as p
from installer import gateway_resume_authority as a
from installer.model import InstallerError
from test_gateway_public_generation import selected


class PublicAdmissionScopeTests(unittest.TestCase):
    def setUp(self):
        self.generation = p.g.Generation(selected())
        self.pointer = p.selection.binding(self.generation, 'a' * 64)
        self.enterContext(patch.object(self.generation.original.shared, '_read', return_value=self.pointer))
        self.profile = {'public_ingress': p.overlay_binding(self.generation.original.shared)}
        self.context = p.PublicAdmission(self.generation.http, self.generation.value['lease_id'],
                                         self.profile, self.generation)
        self.check = self.enterContext(patch.object(self.context, 'check'))

    def enter_scope(self):
        token = p._CURRENT.set(self.context)
        self.addCleanup(p._CURRENT.reset, token)

    def test_no_scope_preserves_observed_overlay_and_refuses_public_exception(self):
        observed = self.context.expected_overlay
        self.assertIs(p.historical_overlay(self.generation.http, observed), observed)
        with self.assertRaises(InstallerError): p.require_profile(self.generation.http, self.profile)
        self.check.assert_not_called()

    def test_historical_mapping_requires_exact_new_overlay_and_keeps_input_unchanged(self):
        self.enter_scope(); observed = deepcopy(self.context.expected_overlay)
        before = deepcopy(observed)
        self.assertEqual(p.historical_overlay(self.generation.http, observed), self.profile['public_ingress'])
        self.assertEqual(observed, before)
        self.check.assert_called_once_with()
        for key in observed:
            damaged = {**observed, key: 'foreign'}
            with self.assertRaises(InstallerError): p.historical_overlay(self.generation.http, damaged)

    def test_equal_http_spec_does_not_replace_process_bound_runtime_identity(self):
        self.enter_scope()
        other = p.g.Generation(self.generation.value).http
        self.assertEqual(other.spec, self.generation.http.spec)
        with self.assertRaises(InstallerError): p.historical_overlay(other, self.context.expected_overlay)

    def test_source_overlay_cannot_be_used_as_actual_successor_evidence(self):
        self.enter_scope()
        with self.assertRaises(InstallerError):
            p.historical_overlay(self.generation.http, self.profile['public_ingress'])

    def test_preparation_scope_alone_does_not_release_mobile_guards(self):
        self.enter_scope()
        with self.assertRaises(InstallerError): p.require_profile(self.generation.http, self.profile)
        with self.assertRaises(InstallerError): self.context.activate(object(), object())

    def test_wrong_lease_or_authority_refuses_before_lock_or_native_observation(self):
        with patch.object(p, 'StateJournal', side_effect=AssertionError('lock too early')):
            for kwargs in ({}, {'lease': object()}, {'authority': object()},
                           {'lease': object(), 'authority': object()}):
                with self.assertRaises(InstallerError), self.context.scoped(**kwargs): pass

    def test_process_drift_and_nested_context_are_rejected(self):
        self.enter_scope(); self.context.pid = os.getpid() + 1
        with self.assertRaises(InstallerError): p.current(self.generation.http)
        self.context.pid = os.getpid()
        with self.assertRaises(InstallerError), self.context.scoped(lease=object()): pass

    def test_parent_profile_and_lease_cannot_adopt_selected_generation(self):
        for profile, lease in (({'public_ingress': {}}, self.context.lease_id),
                               (self.profile, 'b' * 32)):
            with self.assertRaises(InstallerError):
                p.PublicAdmission(self.generation.http, lease, profile, self.generation)
        self.profile['public_ingress']['dropin_sha256'] = 'foreign'
        self.assertNotEqual(self.context.profile, self.profile)


if __name__ == '__main__': unittest.main()
