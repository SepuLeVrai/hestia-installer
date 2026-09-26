"""Bounded property transport controllers and conservative model composition."""
from contextlib import contextmanager
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from installer import systemd_invocation_relations as x
import test_systemd_invocation as source

v, t, r = x.v, x.t, x.r
PATH, ID, HINT = source.PATH, source.ID, source.HINT

class InvocationRelationsTests(unittest.TestCase):
    def setUp(self):
        self.base = source.SystemdInvocationTests(); self.base.setUp()
        self.transport = x.SystemdInvocationRelations(source.base.model.target(), source.base.model.storage())
        self.values = {prop: [] for prop in x.RELATIONS}; self.values['Names'] = ['worker.service', 'worker-alias.service']
        self.values['Requires'] = ['foreign.service', 'not-loaded.service']
        self.property_calls = []
        self.add_unit('foreign.service', PATH+'foreign')
        self.base.base.files.append(['/etc/systemd/system/not-loaded.service', 'disabled'])

    def add_unit(self, name, obj):
        row = list(self.base.base.loaded[0]); row[0] = name; row[6] = obj; self.base.base.loaded.append(row)

    def reply(self, argv, budget, *, pass_fds=()):
        if argv[-1] in x.PROPERTIES and 'Get' in argv:
            self.property_calls.append(argv)
            self.assertEqual(pass_fds, ())
            raw = source.base.encoded('v', {'type': 'as', 'data': self.values[argv[-1]]})
            budget.calls += 1; budget.bytes += len(raw)
            return raw
        return self.base.reply(argv, budget, pass_fds=pass_fds)

    @contextmanager
    def fixture(self, reply=None, **kw):
        with self.base.fixture(reply=reply or self.reply, **kw) as value: yield value

    def collect(self): return self.transport.collect((HINT,))

    def test_fixed_two_passes_and_only_invocation_properties(self):
        with self.fixture() as (_, close): sample = self.collect()
        self.assertEqual(sample.report()['bus_calls'], 50); close.assert_called_once_with(91)
        self.assertEqual([a[-1] for a in self.property_calls], list(x.PROPERTIES)*2)
        for argv in self.property_calls:
            self.assertEqual(argv[argv.index('call')+2], v._path(ID)); self.assertIn('--auto-start=no', argv)
        self.assertEqual(sample.report()['relation_count'], 2)
        self.assertFalse(sample.report()['drain_allowed']); self.assertFalse(sample.report()['host_relevance_verified'])

    def test_names_enrich_only_selected_index_objects(self):
        with self.fixture(): sample = self.collect()
        units = sample.index().private_manifest()['observation']['loaded_units']['rows']
        self.assertEqual(next(u for u in units if u['object_path'] == PATH)['names'], sorted(self.values['Names']))
        self.assertIsNone(next(u for u in units if u['object_path'] != PATH)['names'])
        self.assertEqual(sample.facts().discovery_sha256, sample.index().report()['manifest_sha256'])
        self.assertEqual(sample.facts().observed_at, sample.scan().finished_at)

    def test_targets_resolve_from_loaded_names_never_installed_basename(self):
        with self.fixture(): sample = self.collect()
        targets = {p.target_name: p.target_object for p in sample.facts().units[0].relations}
        self.assertEqual(targets, {'foreign.service': PATH+'foreign', 'not-loaded.service': None})
        files = sample.selection().private_manifest()['installed_unit_files']
        self.assertTrue(all(u['decision'] == 'UNRESOLVED' for u in files))

    def test_all_details_remain_unresolved_without_identity_or_path_seed(self):
        with self.fixture(): sample = self.collect()
        self.assertTrue(all(u['decision'] == 'UNRESOLVED' and not u['enrolled'] for u in sample.selection().private_manifest()['loaded_units']))
        self.assertEqual(sample.report()['known_provisioned_units'], 0)
        detail = sample.facts().units[0]
        self.assertIsNone(detail.identities); self.assertIsNone(detail.paths); self.assertIsNone(detail.bindings)

    def test_pure_composition_with_separate_declared_seed_widens_review_only(self):
        with self.fixture(): sample = self.collect()
        # A synthetic declaration is explicitly separate from collected facts.
        binding = r.ExternalBinding(source.base.model.target().instance, 'operator_catalog', 'c'*64)
        detail = replace(sample.facts().units[0], bindings=(binding,))
        facts = replace(sample.facts(), units=(detail,))
        selection = self.transport._selector.inspect(sample.scan(), facts, now=sample.scan().finished_at)
        self.assertEqual(selection.report()['related_loaded_units'], 2)
        self.assertEqual(selection.report()['known_provisioned_units'], 0)
        self.assertFalse(selection.report()['automatic_exclusion_allowed'])
        self.assertIsNone(sample.facts().units[0].bindings)

    def test_unknown_targets_and_templates_do_not_trigger_recursive_queries(self):
        self.values['Wants'] = ['unknown@.service']
        with self.fixture(): sample = self.collect()
        self.assertEqual(len(self.property_calls), 18); self.assertEqual(sample.report()['bus_calls'], 50)
        self.assertTrue(any(p.target_name == 'unknown@.service' and p.target_object is None for p in sample.facts().units[0].relations))

    def test_property_allowlist_has_no_getall_unit_ordering_or_environment(self):
        for name in ('GetAll', 'Environment', 'ExecStart', 'Id', 'Unit', 'Before', 'After', 'Following', None, ['Names']):
            with self.subTest(name=name), self.assertRaises(t.SystemdTransportError): x._argv(name, ':1.0', ID)
        with self.assertRaises(t.SystemdTransportError): x._argv('Names', 'org.freedesktop.systemd1', ID)
        with self.assertRaises(t.SystemdTransportError): x._argv('Names', ':1.0', 'worker.service')

    def test_names_require_primary_unique_loaded_names_and_same_type(self):
        for names in ([], ['alias.service'], ['worker.service']*2, ['worker.service', 'x.timer'], ['worker.service', 'x@.service'], [False], 'worker.service'):
            self.values['Names'] = names
            with self.subTest(names=names), self.fixture() as (_, close), self.assertRaises(t.SystemdTransportError): self.collect()
            close.assert_called_once_with(91)

    def test_alias_claimed_by_another_loaded_primary_rejected(self):
        self.values['Names'].append('foreign.service')
        with self.fixture(), self.assertRaises(t.SystemdTransportError): self.collect()

    def test_bad_variant_signature_extra_keys_json_and_duplicate_targets(self):
        for raw in (b'{}', b'{"type":"v","data":[NaN]}',
                    b'{"type":"v","type":"v","data":[]}',
                    source.base.encoded('v', {'type': 'as', 'data': [], 'extra': 1}),
                    source.base.encoded('v', {'type': 's', 'data': []}),
                    source.base.encoded('v', {'type': 'as', 'data': ['foreign.service']*2})):
            def reply(argv, budget, **kw):
                normal = self.reply(argv, budget, **kw)
                return raw if argv[-1] == 'Requires' else normal
            with self.subTest(raw=raw), self.fixture(reply), self.assertRaises(t.SystemdTransportError): self.collect()

    def test_names_and_relation_order_is_canonical(self):
        seen = 0
        def reply(argv, budget, **kw):
            nonlocal seen
            if argv[-1] == 'Names':
                seen += 1
                if seen == 2:
                    for prop in x.PROPERTIES: self.values[prop] = list(reversed(self.values[prop]))
            return self.reply(argv, budget, **kw)
        with self.fixture(reply): sample = self.collect()
        self.assertEqual(sample.report()['bus_calls'], 50)

    def test_names_or_relation_set_change_between_passes_refused(self):
        for prop in ('Names', *x.RELATIONS):
            self.setUp(); seen = 0
            def reply(argv, budget, **kw):
                nonlocal seen
                if argv[-1] == prop:
                    seen += 1
                    if seen == 2: self.values[prop].append('new.service')
                return self.reply(argv, budget, **kw)
            with self.subTest(prop=prop), self.fixture(reply) as (_, close), self.assertRaisesRegex(t.SystemdTransportError, 'INVOCATION_CHANGED'):
                self.collect()
            close.assert_called_once_with(91)

    def test_empty_relations_are_observed_without_exhaustiveness_claim(self):
        for prop in x.RELATIONS: self.values[prop] = []
        with self.fixture(): sample = self.collect()
        self.assertEqual(sample.facts().units[0].relations, ())
        self.assertIn('RELATIONS_NOT_EXHAUSTIVE', sample.report()['blockers'])
        self.assertEqual(len(sample.private_manifest()['properties'][0]['relations']), 8)

    def test_name_and_relation_limits_apply_during_the_pass(self):
        for module, constant, maximum, expected in ((x.d, 'MAX_NAMES', 1, 'Names'), (r, 'MAX_RELATIONS', 1, 'Requires')):
            self.property_calls = []
            with self.fixture() as (_, close), patch.object(module, constant, maximum), self.assertRaises(t.SystemdTransportError): self.collect()
            # MAX_NAMES also bounds the prevalidated discovery index; no FD may be opened there.
            if expected == 'Requires':
                self.assertEqual(self.property_calls[-1][-1], expected); close.assert_called_once_with(91)
        raw = source.base.encoded('v', {'type': 'as', 'data': ['worker.service']})
        with self.assertRaises(t.SystemdTransportError): x._names(raw, 0, primary='worker.service')

    def test_relation_limit_is_cumulative_across_properties(self):
        self.values['Triggers'] = ['foreign.service']; self.values['TriggeredBy'] = ['foreign.service']
        with self.fixture(), patch.object(r, 'MAX_RELATIONS', 2), self.assertRaisesRegex(t.SystemdTransportError, 'RELATION_LIMIT'):
            self.collect()
        self.assertEqual(self.property_calls[-1][-1], 'Requires')

    def test_child_or_property_failure_stops_without_retry(self):
        def reply(argv, budget, **kw):
            if argv[-1] == 'Wants': raise OSError('private target')
            return self.reply(argv, budget, **kw)
        with self.fixture(reply) as (_, close), self.assertRaisesRegex(t.SystemdTransportError, '^INVOCATION_TRANSPORT_UNAVAILABLE$'):
            self.collect()
        close.assert_called_once_with(91); self.assertEqual(self.property_calls[-1][-1], 'Requires')

    def test_process_exit_during_properties_is_refused_and_fd_closed(self):
        with self.fixture(alive=[None, None, None, None, t.SystemdTransportError('INVOCATION_PROCESS_EXITED')]) as (_, close), \
             self.assertRaisesRegex(t.SystemdTransportError, 'PROCESS_EXITED'): self.collect()
        self.assertEqual(len(self.property_calls), 9); close.assert_called_once_with(91)

    def test_binding_mismatch_prevents_all_detail_properties(self):
        self.base.mapping[1] = 'foreign.service'
        with self.fixture(), self.assertRaises(t.SystemdTransportError): self.collect()
        self.assertEqual(self.property_calls, [])

    def test_budget_is_closed_and_old_profiles_keep_their_limits(self):
        for count in (1, 128): self.assertEqual(self.transport._budget(count).maximum_calls, 24+26*count)
        self.assertEqual(t._Budget().maximum_calls, 24); self.assertEqual(t._Budget(invocation_pairs=128).maximum_calls, 1048)
        for count, enabled in ((0, True), (129, True), (True, True), (1, 1)):
            with self.assertRaises(t.SystemdTransportError): t._Budget(invocation_pairs=count, invocation_relations=enabled)
        budget = self.transport._budget(1); budget.calls = 50
        with patch.object(t.subprocess, 'Popen') as spawn, self.assertRaises(t.SystemdTransportError): t._capture([], budget)
        spawn.assert_not_called()

    def test_reports_repr_and_envelope_copies_keep_properties_private(self):
        with self.fixture(): sample = self.collect()
        for text in (repr(sample), repr(self.transport), json.dumps(sample.report())):
            for private in ('worker.service', PATH, ID, 'foreign.service', '4321'): self.assertNotIn(private, text)
        data = sample.private_manifest(); data['properties'].clear()
        self.assertEqual(len(sample.private_manifest()['properties']), 1)
        self.assertFalse(sample.report()['effective_identities_observed'])

    def test_property_evidence_is_bound_to_invocation_and_normalized_values(self):
        with self.fixture(): first = self.collect()
        a = first.facts().units[0].relations[0].evidence_sha256
        self.values['Requires'].append('different.service')
        with self.fixture(): second = self.collect()
        b = second.facts().units[0].relations[0].evidence_sha256
        self.assertNotEqual(a, b)
        self.assertNotEqual(first.facts().discovery_sha256, second.facts().discovery_sha256)

    def test_envelope_bound_refuses_before_returning_large_combination(self):
        # elapsed_ms must have the same serialized width in both observations.
        # Otherwise a faster second run can fit the first run's length-minus-one.
        with patch.object(v.time, 'monotonic', return_value=100.0):
            with self.fixture(): sample = self.collect()
            self.property_calls = []
            with self.fixture() as (_, close), patch.object(x.d, 'MAX_BYTES', len(sample._canonical)-1):
                with self.assertRaises(t.SystemdTransportError): self.collect()
        self.assertEqual(len(self.property_calls), 18); close.assert_called_once_with(91)

    def two_units_reply(self, argv, budget, **kw):
        other_id = 'ab'*16
        if 'GetUnitByPIDFD' in argv and argv[-1] == '92':
            budget.calls += 1
            return json.dumps({'type': 'osay', 'data': [PATH+'foreign', 'foreign.service', list(bytes.fromhex(other_id))]}).encode()
        if 'GetUnitByInvocationID' in argv and argv[-16:] == [str(b) for b in bytes.fromhex(other_id)]:
            budget.calls += 1; return source.base.encoded('o', v._path(other_id))
        if v._path(other_id) in argv:
            prop = argv[-1]
            sig, value = ('s', 'foreign.service') if prop == 'Id' else ('ay', list(bytes.fromhex(other_id))) if prop == 'InvocationID' else ('as', ['foreign.service', 'foreign-alias.service']) if prop == 'Names' else ('as', [])
            budget.calls += 1; return source.base.encoded('v', {'type': sig, 'data': value})
        return self.reply(argv, budget, **kw)

    def test_target_alias_resolves_only_after_second_unit_names_observed(self):
        self.values['Requires'] = ['foreign-alias.service']
        with self.fixture(self.two_units_reply, opened=[91, 92]) as (_, close):
            sample = self.transport.collect((HINT, v.InvocationHint(PATH+'foreign', 4322)))
        self.assertEqual(sample.report()['bus_calls'], 76)
        relation = sample.facts().units[0].relations[0]
        self.assertEqual((relation.target_name, relation.target_object), ('foreign-alias.service', PATH+'foreign'))
        self.assertEqual([c.args for c in close.call_args_list], [(91,), (92,)])

    def test_names_limit_accumulates_across_selected_units(self):
        self.values['Names'] = ['worker.service', 'a.service', 'b.service', 'c.service']
        # The original index fits; the two selected units then supply six names.
        with self.fixture(self.two_units_reply, opened=[91, 92]), patch.object(x.d, 'MAX_NAMES', 5):
            with self.assertRaisesRegex(t.SystemdTransportError, 'RELATION_LIMIT'):
                self.transport.collect((HINT, v.InvocationHint(PATH+'foreign', 4322)))

if __name__ == '__main__': unittest.main()
