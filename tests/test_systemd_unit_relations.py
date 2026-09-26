"""Local contract tests; actual property reads are qualified separately."""
from contextlib import contextmanager
from dataclasses import replace
import json
import time
import unittest
from unittest.mock import patch

from installer import systemd_unit_relations as u
import test_systemd_discovery_transport as transport
import test_launcher_inventory as model

t = u.t
d = u.d
OBJ = d.UNIT_PREFIX+'worker'
OTHER = d.UNIT_PREFIX+'other'


class UnitRelationsTests(unittest.TestCase):
    def setUp(self):
        self.source = transport.SystemdDiscoveryTransportTests(); self.source.setUp()
        self.reader = u.SystemdUnitRelations(model.target(), model.storage())
        self.props = {OBJ: {'Id': 'worker.service', 'Names': ['worker.service', 'alias.service'],
            **{key: [] for key in u.RELATIONS}}}
        self.property_calls = []

    def add(self, name='other.service', obj=OTHER):
        self.source.loaded.append([name, 'PRIVATE', 'loaded', 'active', 'running', '', obj, 0, '', '/'])
        self.props[obj] = {'Id': name, 'Names': [name], **{key: [] for key in u.RELATIONS}}
        if name.endswith(('.timer', '.path')): self.props[obj]['Unit'] = 'alias.service'

    def reply(self, argv, budget):
        tail = argv[argv.index('call')+1:]
        if tail[1].startswith(d.UNIT_PREFIX):
            self.property_calls.append(argv); prop = tail[-1]
            raw = transport.encoded('v', {'type': 's' if prop in ('Id', 'Unit') else 'as', 'data': self.props[tail[1]][prop]})
            budget.calls += 1; budget.bytes += len(raw); return raw
        return self.source.reply(argv, budget)

    @contextmanager
    def fixture(self, reply=None):
        with self.source.fixture(reply=reply or self.reply): yield

    def test_exact_fixed_calls_and_index_bound_private_facts(self):
        with self.fixture(): sample = self.reader.collect((OBJ,))
        self.assertEqual(sample.report()['bus_calls'], 44)
        self.assertEqual(len(self.property_calls), 20)
        self.assertEqual(sample.facts().discovery_sha256, d.l._sha(sample.index()._canonical))
        self.assertEqual(sample.facts().units[0].relations, ())
        row = sample.index().private_manifest()['observation']['loaded_units']['rows'][0]
        self.assertEqual(row['names'], ['alias.service', 'worker.service'])
        for argv in self.property_calls:
            self.assertIn('--auto-start=no', argv); self.assertIn('--allow-interactive-authorization=no', argv)
            self.assertEqual(argv[argv.index('call')+1:][:5], [':1.0', OBJ, t.PROPERTIES, 'Get', 'ss'])

    def test_timer_and_path_unit_property_but_socket_only_generic_triggers(self):
        for suffix in ('timer', 'path', 'socket'):
            with self.subTest(suffix=suffix):
                self.setUp(); self.add('other.'+suffix); self.props[OTHER]['Triggers'] = ['alias.service']
                with self.fixture(): sample = self.reader.collect((OBJ, OTHER))
                facts = next(row for row in sample.facts().units if row.object_path == OTHER)
                self.assertEqual({row.property for row in facts.relations}, {'Triggers', 'Unit'} if suffix != 'socket' else {'Triggers'})
                self.assertTrue(all(row.target_object == OBJ for row in facts.relations))
                self.assertEqual(sample.report()['bus_calls'], 66 if suffix != 'socket' else 64)
                if suffix == 'socket':
                    with self.assertRaises(u.SystemdRelationsError): u._argv(':1.0', OTHER, 'other.socket', 'Unit')

    def test_relations_are_resolved_by_observed_primary_or_alias_only(self):
        self.add(); self.props[OBJ]['Wants'] = ['other.service', 'absent.service', 'template@.service']
        with self.fixture(): sample = self.reader.collect((OBJ,))
        relations = {row.target_name: row.target_object for row in sample.facts().units[0].relations}
        self.assertEqual(relations, {'other.service': OTHER, 'absent.service': None, 'template@.service': None})
        self.assertFalse(any(call[call.index('call')+2] == OTHER for call in self.property_calls))
        unselected = next(row for row in sample.index().private_manifest()['observation']['loaded_units']['rows'] if row['object_path'] == OTHER)
        self.assertIsNone(unselected['names'])

    def test_relation_cycle_does_not_create_positive_relevance_or_authority(self):
        self.add(); self.props[OBJ]['Wants'] = ['other.service']; self.props[OTHER]['Wants'] = ['worker.service']
        with self.fixture(): sample = self.reader.collect((OBJ, OTHER))
        result = self.reader._relevance.inspect(sample.scan(), sample.facts(), now=model.NOW).report()
        self.assertEqual(result['related_loaded_units'], 0); self.assertEqual(result['unresolved_loaded_units'], 2)
        for key, value in sample.report().items():
            if type(value) is bool and key not in ('system_manager_lists_observed', 'selected_unit_relations_observed'):
                self.assertFalse(value, key)
        for unit in sample.facts().units:
            self.assertIsNone(unit.identities); self.assertIsNone(unit.paths); self.assertIsNone(unit.bindings)

    def test_empty_relations_are_observations_not_missing_or_complete_inventory(self):
        with self.fixture(): sample = self.reader.collect((OBJ,))
        self.assertEqual(set(sample.private_manifest()['details'][OBJ]['properties']), {'Id', 'Names', *u.RELATIONS})
        self.assertIn('RELATIONS_NOT_EXHAUSTIVE', sample.report()['blockers'])
        self.assertIn('UNIT_SELECTION_PARTIAL', sample.report()['blockers'])
        self.assertFalse(sample.report()['host_scheduler_inventory_complete'])

    def test_bad_selection_is_refused_before_host_io(self):
        for selection in ((), [], (OBJ, OBJ), ('worker.service',), ('/org/freedesktop/systemd1',), tuple(d.UNIT_PREFIX+str(n) for n in range(129))):
            with patch.object(t, '_local') as local, self.assertRaises(u.SystemdRelationsError): self.reader.collect(selection)
            local.assert_not_called()

    def test_object_absent_from_first_list_is_not_queried(self):
        with self.fixture(), self.assertRaisesRegex(u.SystemdRelationsError, 'NOT_LISTED'): self.reader.collect((OTHER,))
        self.assertEqual(self.property_calls, []); self.assertEqual(len(self.source.commands), 12)

    def test_malformed_population_is_refused_before_detail_io(self):
        self.source.loaded[0][0] = '../private'
        with self.fixture(), self.assertRaises(u.SystemdRelationsError): self.reader.collect((OBJ,))
        self.assertEqual(self.property_calls, [])

    def test_property_whitelist_blocks_getall_ordering_execution_and_resolution(self):
        for prop in ('GetAll', 'GetUnit', 'LoadUnit', 'ExecStart', 'Environment', 'Before', 'After', 'Following', 'Unit'):
            with self.assertRaises(u.SystemdRelationsError): u._argv(':1.0', OBJ, 'worker.service', prop)
        for owner in ('org.freedesktop.systemd1', '--evil', None):
            with self.assertRaises(Exception): u._argv(owner, OBJ, 'worker.service', 'Names')

    def test_strict_variant_types_and_duplicate_keys(self):
        for raw in (transport.encoded('v', {'type':'s','data': ['worker.service']}),
                    transport.encoded('v', {'type':'as','data': 'worker.service'}),
                    transport.encoded('v', {'type':'as','data': [False]}),
                    transport.encoded('v', {'type':'as','data': [], 'extra': 1}),
                    b'{"type":"v","data":[{"type":"as","type":"as","data":[]}]}'):
            with self.assertRaises(Exception): u._value(raw, 'as')

    def test_duplicate_names_templates_and_suffix_conflicts_refuse(self):
        for value in (['worker.service']*2, ['alias.service'], ['worker.service','unused@.service'], ['worker.service','other.timer']):
            self.props[OBJ]['Names'] = value
            with self.fixture(), self.assertRaises(u.SystemdRelationsError): self.reader.collect((OBJ,))

    def test_alias_owned_by_two_objects_is_refused(self):
        self.add(); self.props[OTHER]['Names'].append('alias.service')
        with self.fixture(), self.assertRaises(u.SystemdRelationsError): self.reader.collect((OBJ, OTHER))

    def test_primary_id_mismatch_is_refused(self):
        self.props[OBJ]['Id'] = 'other.service'
        with self.fixture(), self.assertRaisesRegex(u.SystemdRelationsError, 'IDENTITY_CHANGED'): self.reader.collect((OBJ,))

    def test_duplicate_relation_target_is_not_silently_deduplicated(self):
        self.props[OBJ]['Wants'] = ['absent.service']*2
        with self.fixture(), self.assertRaisesRegex(u.SystemdRelationsError, 'DUPLICATE'): self.reader.collect((OBJ,))

    def test_names_and_relations_changed_between_reads_refuse(self):
        for prop, value in (('Names',['worker.service']), ('Wants',['absent.service'])):
            self.setUp(); seen = 0
            def reply(argv, budget):
                nonlocal seen
                if argv[-1] == prop:
                    seen += 1
                    if seen == 2: self.props[OBJ][prop] = value
                return self.reply(argv, budget)
            with self.fixture(reply), self.assertRaisesRegex(u.SystemdRelationsError, 'RELATIONS_CHANGED'):
                self.reader.collect((OBJ,))

    def test_property_array_order_is_canonical(self):
        self.props[OBJ]['Wants'] = ['z.service','a.service']
        def reply(argv, budget):
            if argv[-1] in ('Names','Wants'): self.props[OBJ][argv[-1]].reverse()
            return self.reply(argv, budget)
        with self.fixture(reply): sample = self.reader.collect((OBJ,))
        self.assertEqual([r.target_name for r in sample.facts().units[0].relations], ['a.service','z.service'])

    def test_list_change_after_details_invalidates_whole_observation(self):
        count = 0
        def reply(argv, budget):
            nonlocal count
            if argv[-1] == 'ListUnits':
                count += 1
                if count == 2: self.source.loaded[0][3] = 'inactive'
            return self.reply(argv, budget)
        with self.fixture(reply), self.assertRaises(u.SystemdRelationsError): self.reader.collect((OBJ,))
        self.assertEqual(count, 2)

    def test_context_change_after_details_invalidates_observation(self):
        old = transport.local(); changed = transport.local(); changed['broker_pid'] = 999
        with self.source.fixture(reply=self.reply, locals=[old,old,changed,changed]), self.assertRaises(u.SystemdRelationsError):
            self.reader.collect((OBJ,))

    def test_unreadable_property_never_becomes_empty_success(self):
        def reply(argv,budget):
            if argv[-1] == 'Wants': raise OSError('PRIVATE')
            return self.reply(argv,budget)
        with self.fixture(reply), self.assertRaisesRegex(u.SystemdRelationsError, '^UNIT_RELATIONS_UNAVAILABLE$'):
            self.reader.collect((OBJ,))

    def test_property_and_cumulative_names_bounds(self):
        raw = transport.encoded('v', {'type':'as','data':['x.service']*4097})
        with self.assertRaisesRegex(u.SystemdRelationsError, 'LIMIT'): u._value(raw,'as')
        self.add()
        self.props[OBJ]['Names'] = ['worker.service']+['a'+str(n)+'.service' for n in range(2048)]
        self.props[OTHER]['Names'] = ['other.service']+['b'+str(n)+'.service' for n in range(2048)]
        with self.fixture(), self.assertRaisesRegex(u.SystemdRelationsError, 'NAMES_LIMIT'): self.reader.collect((OBJ,OTHER))

    def test_global_relation_limit_before_following_any_target(self):
        for key in ('Wants','Requires','Triggers'): self.props[OBJ][key] = ['x'+str(n)+'.service' for n in range(3000)]
        with self.fixture(), self.assertRaisesRegex(u.SystemdRelationsError, 'RELATIONS_LIMIT'): self.reader.collect((OBJ,))
        self.assertEqual(len(self.source.commands), 12)

    def test_shared_deadline_and_total_budget_not_reset_by_details(self):
        budgets = []
        def reply(argv,budget):
            budgets.append(budget)
            raw = self.reply(argv,budget)
            if argv[-1] == 'Wants': budget.deadline = time.monotonic()-1
            return raw
        with self.fixture(reply), self.assertRaises(u.SystemdRelationsError): self.reader.collect((OBJ,))
        self.assertEqual(len({id(b) for b in budgets}),1)
        self.assertEqual(budgets[0].call_limit,44)
        self.assertEqual(t._Budget().call_limit,24)
        budget = t._Budget(); budget.call_limit = 44; budget.calls = 44
        with patch.object(t.subprocess,'Popen') as spawn, self.assertRaises(t.SystemdTransportError): t._capture([],budget)
        spawn.assert_not_called()

    def test_final_envelope_limit_includes_facts_and_details(self):
        original = d._json
        def limited(value):
            if type(value) is dict and 'details' in value and 'facts' in value:
                with patch.object(d,'MAX_BYTES',100): return original(value)
            return original(value)
        with self.fixture(), patch.object(d,'_json',side_effect=limited), self.assertRaises(u.SystemdRelationsError):
            self.reader.collect((OBJ,))

    def test_private_reports_and_copies_and_fixed_errors(self):
        with self.fixture(): sample = self.reader.collect((OBJ,))
        self.assertNotIn('worker.service',json.dumps(sample.report()))
        self.assertNotIn('worker.service',repr(sample)); self.assertNotIn('worker.service',repr(sample.facts()))
        value = sample.private_manifest(); value['details'].clear(); self.assertTrue(sample.private_manifest()['details'])
        self.assertNotIn(transport.SECRET.encode(),sample._canonical)

    def test_bad_target_and_clock_reversal_refuse(self):
        with patch.object(t,'_local') as local, self.assertRaises(u.SystemdRelationsError): u.SystemdUnitRelations({},model.storage())
        local.assert_not_called()
        with self.fixture(), patch.object(t.time,'time',side_effect=[model.NOW,model.NOW-1]), self.assertRaises(u.SystemdRelationsError):
            self.reader.collect((OBJ,))


if __name__ == '__main__': unittest.main()
