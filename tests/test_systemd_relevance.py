"""Synthetic facts only: no host discovery, profile admission or drain proof."""
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
import json
import unittest
from unittest.mock import patch

from installer import systemd_relevance as r
import test_systemd_discovery as source
import test_launcher_inventory as model

d = r.d
NOW = model.NOW
HASH = 'd'*64


def identity(**changes):
    return replace(r.IdentityFact('configured', 'observed', 991, 992, (), False, None, HASH), **changes)


def process(**changes):
    return replace(r.ProcessBinding(123, 700, 'pid:[7]', '/system.slice/worker.service', HASH), **changes)


def path(**changes):
    return replace(r.PathFact('configuration', 'start', 'resolved', '/var/lib/private-slot/config.json', 'mnt:[8]', '/', HASH), **changes)


def detail(unit=None, **changes):
    u = source.unit() if unit is None else unit
    return replace(r.UnitFacts(u.object_path, u.primary_name, None, None, None, None), **changes)


def relation(name='worker.service', obj='worker_2eservice', prop='Requires', **changes):
    return replace(r.RelationFact(prop, name, d.UNIT_PREFIX+obj, HASH), **changes)


class SystemdRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.storage = model.storage()
        self.selector = r.SystemdRelevance(model.target(), self.storage)
        self.discovery = d.SystemdDiscovery(model.target(), self.storage)

    def scan(self, units=None, files=None, jobs=None):
        changes = {}
        for key, rows in (('loaded_units', units), ('installed_unit_files', files), ('manager_jobs', jobs)):
            if rows is not None: changes[key] = source.enumeration(rows)
        return source.scan(source.round_value(**changes))

    def facts(self, units=(), scan=None, **changes):
        s = self.scan() if scan is None else scan
        digest = self.discovery.inspect(s, now=NOW).report()['manifest_sha256']
        return replace(r.RelevanceFacts(digest, NOW, units), **changes)

    def inspect(self, *units, scan=None, facts=None, now=NOW):
        s = self.scan() if scan is None else scan
        return self.selector.inspect(s, self.facts(units, s) if facts is None else facts, now=now)

    def row(self, result, obj='worker_2eservice'):
        return next(row for row in result.private_manifest()['loaded_units'] if row['object_path'] == d.UNIT_PREFIX+obj)

    def test_no_details_preserves_three_populations_and_all_global_blocks(self):
        result = self.inspect(); data = result.private_manifest(); report = result.report()
        self.assertEqual(self.row(result)['decision'], 'UNRESOLVED')
        self.assertEqual(data['installed_unit_files'][0]['decision'], 'UNRESOLVED')
        self.assertEqual(data['discovery']['coverage']['systemd_system'], 'partial')
        self.assertEqual(report['known_provisioned_units'], 0)
        self.assertTrue(set(r.BLOCKERS) <= set(report['blockers']))
        for key, value in report.items():
            if type(value) is bool: self.assertFalse(value, key)

    def test_empty_lists_are_not_host_absence_or_global_coverage(self):
        result = self.inspect(scan=self.scan((), (), ()))
        self.assertEqual(result.report()['loaded_units'], 0)
        self.assertFalse(result.report()['host_scheduler_inventory_complete'])

    def test_name_or_provisioned_spelling_never_grants_known_profile(self):
        u = source.unit('hestia-'+model.target().instance+'-apache.service', 'managed')
        result = self.inspect(scan=self.scan((u,)))
        self.assertEqual(self.row(result, 'managed')['decision'], 'UNRESOLVED')
        result = self.inspect(detail(u, identities=(identity(),)), scan=self.scan((u,)))
        self.assertEqual(self.row(result, 'managed')['decision'], 'RELATED_UNMANAGED')
        self.assertFalse(self.row(result, 'managed')['enrolled'])

    def test_unprefixed_proxy_uid_gid_and_supplementary_group_signals(self):
        u = source.unit('nginx.service', 'proxy')
        for fact in (identity(), identity(uid=900, gid=991), identity(uid=900, groups=(991, 999))):
            with self.subTest(fact=repr(fact)):
                row = self.row(self.inspect(detail(u, identities=(fact,)), scan=self.scan((u,))), 'proxy')
                self.assertEqual(row['decision'], 'RELATED_UNMANAGED')
                self.assertEqual(row['reasons'], ['CONFIGURED_IDENTITY_MATCH'])
                self.assertFalse(row['enrolled'])

    def test_root_other_unknown_dynamic_and_empty_identity_are_never_exclusions(self):
        for fact in (identity(uid=0), identity(uid=900), identity(uid=None, gid=None, groups=None),
                     identity(uid=900, dynamic_user=True), identity(uid=900, dynamic_user=None),
                     identity(state='unknown', uid=None, gid=None, groups=None, dynamic_user=None, evidence_sha256=None)):
            row = self.row(self.inspect(detail(identities=(fact,))))
            self.assertEqual(row['decision'], 'UNRESOLVED')
            self.assertIn('NO_POSITIVE_SIGNAL_NOT_EXCLUSION', row['issues'])

    def test_configured_and_effective_identity_are_distinct_and_both_preserved(self):
        effective = identity(kind='effective', uid=900, gid=991, dynamic_user=None, process=process())
        result = self.inspect(detail(identities=(identity(uid=0), effective)))
        self.assertEqual(self.row(result)['reasons'], ['EFFECTIVE_IDENTITY_MATCH'])
        self.assertEqual(len(result.private_manifest()['facts']['units'][0]['identities']), 2)
        self.assertIn('PROCESS_MEMBERSHIP_DECLARED_ONLY', self.row(result)['issues'])

    def test_effective_identity_requires_process_membership_and_same_pid_namespace(self):
        for p in (None, {}, process(pid=0), process(pid=True), process(start_ticks=0), process(pid_namespace='pid:[99]'), process(membership_sha256=None)):
            with self.subTest(p=repr(p)), self.assertRaises(r.SystemdRelevanceError):
                self.inspect(detail(identities=(identity(kind='effective', dynamic_user=None, process=p),)))
        with self.assertRaises(r.SystemdRelevanceError): self.inspect(detail(identities=(identity(process=process()),)))

    def test_identity_bool_duplicates_unknown_payload_and_missing_digest_fail_closed(self):
        for fact in (identity(uid=True), identity(gid=-1), identity(groups=[991]), identity(groups=(991, 991)),
                     identity(dynamic_user=1), identity(evidence_sha256=None), identity(state='unknown'), identity(state='unreadable')):
            with self.subTest(fact=repr(fact)), self.assertRaises(r.SystemdRelevanceError): self.inspect(detail(identities=(fact,)))
        for facts in ((identity(), identity(uid=0)),
                      (identity(kind='effective', dynamic_user=None, process=process()), identity(kind='effective', dynamic_user=None, process=process(start_ticks=701)))):
            with self.assertRaisesRegex(r.SystemdRelevanceError, 'DUPLICATE_FACT'): self.inspect(detail(identities=facts))

    def test_same_process_cannot_be_bound_to_two_loaded_objects(self):
        other = source.unit('other.service', 'other'); scan = self.scan((source.unit(), other))
        effective = identity(kind='effective', dynamic_user=None, process=process())
        for second in (effective, replace(effective, process=process(start_ticks=999))):
            with self.assertRaisesRegex(r.SystemdRelevanceError, 'PROCESS_BINDING'):
                self.inspect(detail(identities=(effective,)), detail(other, identities=(second,)), scan=scan)

    def test_root_configuration_path_and_registered_business_roots_select_review(self):
        for value in (model.target().webroot, model.target().webroot+'/scripts/task.php',
                      model.target().configuration+'/config.json', '/var/lib/private-data/uploads/ged/document.pdf',
                      '/var/lib/private-data/sessions/abc'):
            row = self.row(self.inspect(detail(identities=(identity(uid=0),), paths=(path(path=value),))))
            self.assertIn('DECLARED_HOST_PATH_MATCH', row['reasons'])
            self.assertIn('ROOT_IDENTITY_NOT_EXCLUDED', row['issues'])

    def test_path_components_prevent_substring_and_sibling_matches(self):
        for value in ('/srv/private-web-old/task.php', '/srv/not-private-web', '/var/lib/private-slot2', '/elsewhere/private-web'):
            self.assertEqual(self.row(self.inspect(detail(paths=(path(path=value),))))['decision'], 'UNRESOLVED')

    def test_ancestor_and_root_paths_are_broad_review_hints_only(self):
        for value in ('/srv', '/'):
            row = self.row(self.inspect(detail(paths=(path(path=value, role='destination'),))))
            self.assertIn('DECLARED_HOST_PATH_MATCH', row['reasons'])
            self.assertIn('PATH_IDENTITY_NOT_ATTESTED', row['issues'])
        self.assertIn('BROAD_PATH_HINT', row['issues'])

    def test_textual_and_foreign_namespace_or_chroot_paths_are_distinct_hints(self):
        for fact, reason in ((path(state='textual', mount_namespace=None, root_directory=None), 'TEXTUAL_PATH_HINT'),
                             (path(mount_namespace='mnt:[99]'), 'CONTEXTUAL_PATH_HINT'),
                             (path(root_directory='/container'), 'CONTEXTUAL_PATH_HINT')):
            row = self.row(self.inspect(detail(paths=(fact,))))
            self.assertEqual(row['reasons'], [reason]); self.assertIn('PATH_CONTEXT_UNVERIFIED', row['issues'])
        self.assertEqual(self.row(self.inspect(detail(paths=(path(path='/config', root_directory='/container'),))))['decision'], 'UNRESOLVED')

    def test_unknown_unreadable_and_noncanonical_paths_never_become_empty_proof(self):
        for state in ('unknown', 'unreadable'):
            row = self.row(self.inspect(detail(paths=(path(state=state, path=None, mount_namespace=None, root_directory=None, evidence_sha256=None),))))
            self.assertIn('PATH_UNAVAILABLE', row['issues']); self.assertEqual(row['decision'], 'UNRESOLVED')
        for fact in (path(path='/srv/../private-web'), path(path='relative'), path(path='/srv//x'), path(path='/x\nsecret'),
                     path(evidence_sha256=None), path(state='unknown'), path(state='textual'), path(role='argv'), path(phase='free')):
            with self.subTest(fact=repr(fact)), self.assertRaises(r.SystemdRelevanceError): self.inspect(detail(paths=(fact,)))

    def test_all_execution_phases_and_generated_sources_are_retained_without_running(self):
        values = tuple(path(role='generated_source', phase=phase) for phase in r.PHASES)
        data = self.inspect(detail(paths=values)).private_manifest()
        self.assertEqual({v['phase'] for v in data['facts']['units'][0]['paths']}, set(r.PHASES))

    def test_explicit_external_binding_is_a_review_signal_not_identity_proof(self):
        for origin in ('operator_task', 'operator_catalog', 'sql_client'):
            binding = r.ExternalBinding(model.target().instance, origin, HASH)
            row = self.row(self.inspect(detail(bindings=(binding,))))
            self.assertEqual(row['reasons'], ['DECLARED_EXTERNAL_BINDING'])
            self.assertIn('EXTERNAL_BINDING_DECLARED_ONLY', row['issues'])
        for binding in (r.ExternalBinding('f'*32, 'operator_task', HASH), r.ExternalBinding(model.target().instance, 'comment', HASH),
                        r.ExternalBinding(model.target().instance, 'operator_task', None)):
            with self.assertRaises(r.SystemdRelevanceError): self.inspect(detail(bindings=(binding,)))

    def test_timer_path_socket_targets_propagate_review_both_directions(self):
        for kind in ('timer', 'path', 'socket'):
            other = source.unit('launch.'+kind, 'launch')
            scan = self.scan((source.unit(), other))
            a = detail(identities=(identity(),)); b = detail(other, relations=(relation(prop='Unit'),))
            self.assertEqual(self.row(self.inspect(a, b, scan=scan), 'launch')['reasons'], ['RELATION_TO_RELATED_CANDIDATE'])
            result = self.inspect(detail(), replace(b, identities=(identity(),)), scan=scan)
            self.assertEqual(self.row(result)['reasons'], ['RELATION_TO_RELATED_CANDIDATE'])

    def test_named_dependency_and_reaction_properties_widen_review(self):
        other = source.unit('other.service', 'other'); scan = self.scan((source.unit(), other))
        for prop in r.LINK_PROPERTIES-{'Unit'}:
            result = self.inspect(detail(identities=(identity(),)), detail(other, relations=(relation(prop=prop),)), scan=scan)
            self.assertEqual(self.row(result, 'other')['decision'], 'RELATED_UNMANAGED')

    def test_ordering_following_and_unsupported_properties_do_not_propagate(self):
        a = source.unit(); b = source.unit('other.service', 'other', following='worker.service'); scan = self.scan((a, b))
        for prop in ('Before', 'After', 'Following', 'NewProperty'):
            result = self.inspect(detail(a, identities=(identity(),)), detail(b, relations=(relation(prop=prop),)), scan=scan)
            self.assertEqual(self.row(result, 'other')['decision'], 'UNRESOLVED')
            expected = 'ORDERING_IS_NOT_ACTIVATION' if prop in ('Before', 'After') else 'RELATION_KIND_UNSUPPORTED'
            self.assertIn(expected, self.row(result, 'other')['issues'])

    def test_cycles_without_seed_stay_unresolved_and_seeded_cycles_terminate(self):
        a = source.unit(); b = source.unit('b.service', 'b'); c = source.unit('c.service', 'c')
        scan = self.scan((a, b, c))
        facts = (detail(a, relations=(relation('b.service', 'b'),)), detail(b, relations=(relation('c.service', 'c'),)), detail(c, relations=(relation(),)))
        result = self.inspect(*facts, scan=scan)
        self.assertEqual(result.report()['unresolved_loaded_units'], 3)
        result = self.inspect(replace(facts[0], identities=(identity(),)), *facts[1:], scan=scan)
        self.assertEqual(result.report()['related_loaded_units'], 3)
        self.assertEqual(result.report()['relation_count'], 3)

    def test_missing_template_target_is_preserved_without_instantiation_or_basename_binding(self):
        sock = source.unit('web.socket', 'sock'); scan = self.scan((sock,), (d.InstalledUnitFile('/etc/systemd/system/web@.service', 'masked'),))
        fact = r.RelationFact('Unit', 'web@.service', None, HASH)
        result = self.inspect(detail(sock, identities=(identity(),), relations=(fact,)), scan=scan)
        self.assertIn('TEMPLATE_INSTANCES_UNKNOWN', self.row(result, 'sock')['issues'])
        self.assertEqual(len(result.private_manifest()['loaded_units']), 1)
        self.assertEqual(result.private_manifest()['installed_unit_files'][0]['decision'], 'UNRESOLVED')

    def test_relations_require_exact_observed_name_object_binding(self):
        for fact in (relation(obj='foreign'), relation(name='foreign.service'), relation(prop='Unit'), relation(evidence_sha256=None), relation(prop='Bad\nProperty')):
            with self.assertRaises(r.SystemdRelevanceError): self.inspect(detail(relations=(fact,)))
        # Unresolved targets are retained, even if an adapter declined to bind a known name.
        result = self.inspect(detail(relations=(relation(target_object=None),)))
        self.assertIn('RELATION_TARGET_UNRESOLVED', self.row(result)['issues'])

    def test_alias_endpoint_does_not_duplicate_objects_or_attach_same_named_file(self):
        worker = source.unit(names=('worker.service', 'alias.service')); timer = source.unit('schedule.timer', 'timer')
        result = self.inspect(detail(worker, identities=(identity(),)), detail(timer, relations=(relation(name='alias.service', prop='Unit'),)), scan=self.scan((worker, timer)))
        self.assertEqual(result.report()['related_loaded_units'], 2)
        self.assertEqual(len(result.private_manifest()['loaded_units']), 2)
        self.assertEqual(result.private_manifest()['installed_unit_files'][0]['decision'], 'UNRESOLVED')

    def test_job_inherits_review_only_with_exact_declared_loaded_binding(self):
        job = source.job(); worker = source.unit(job=d.UnitJobReference(3, 'start', job.object_path))
        result = self.inspect(detail(worker, identities=(identity(),)), scan=self.scan((worker,), jobs=(job,)))
        self.assertEqual(result.report()['related_jobs'], 1)
        result = self.inspect(scan=self.scan((), jobs=(job,)))
        self.assertEqual(result.private_manifest()['manager_jobs'][0]['decision'], 'UNRESOLVED')
        worker = replace(worker, names=None); job = replace(job, unit_name='unverified-alias.service')
        result = self.inspect(detail(worker, identities=(identity(),)), scan=self.scan((worker,), jobs=(job,)))
        self.assertEqual(result.report()['related_loaded_units'], 1); self.assertEqual(result.report()['related_jobs'], 0)

    def test_files_masked_disabled_templates_and_same_basename_stay_unresolved(self):
        files = tuple(d.InstalledUnitFile('/'+root+'/worker.service', state) for root, state in (('etc', 'disabled'), ('run', 'masked')))
        result = self.inspect(detail(identities=(identity(),)), scan=self.scan(files=files))
        self.assertEqual([row['decision'] for row in result.private_manifest()['installed_unit_files']], ['UNRESOLVED']*2)

    def test_partial_unreadable_fields_do_not_erase_positive_identity(self):
        missing = path(state='unreadable', path=None, mount_namespace=None, root_directory=None, evidence_sha256=None)
        row = self.row(self.inspect(detail(identities=(identity(),), paths=(missing,))))
        self.assertEqual(row['decision'], 'RELATED_UNMANAGED'); self.assertIn('PATH_UNAVAILABLE', row['issues'])

    def test_absent_and_empty_details_are_distinct_and_neither_certifies_completeness(self):
        unknown = self.inspect(detail()).private_manifest()['facts']['units'][0]
        empty_result = self.inspect(detail(identities=(), paths=(), relations=(), bindings=()))
        empty = empty_result.private_manifest()['facts']['units'][0]
        self.assertIsNone(unknown['paths']); self.assertEqual(empty['paths'], [])
        self.assertIn('EXECUTION_CONTEXT_INCOMPLETE', self.row(empty_result)['issues'])

    def test_exact_scan_digest_binds_all_populations_provenance_and_storage(self):
        facts = self.facts((detail(identities=(identity(),)),))
        for scan in (self.scan(files=()), self.scan((source.unit(active_state='active'),)),
                     source.scan(source.round_value(provenance=replace(source.provenance(), bus_owner=':1.5')))):
            with self.assertRaisesRegex(r.SystemdRelevanceError, 'INDEX_BINDING'): self.inspect(scan=scan, facts=facts)
        with self.assertRaises(r.SystemdRelevanceError): self.inspect(facts=replace(facts, discovery_sha256='0'*64))
        data = self.storage.private_manifest(); data['blockers'].append('ANOTHER_BLOCKER')
        changed = type(self.storage)(json.dumps(data).encode())
        with self.assertRaisesRegex(r.SystemdRelevanceError, 'INDEX_BINDING'):
            r.SystemdRelevance(model.target(), changed).inspect(self.scan(), facts, now=NOW)

    def test_unit_primary_object_duplicates_and_foreign_facts_fail_closed(self):
        for unit in (replace(detail(), primary_name='alias.service'), replace(detail(), object_path=d.UNIT_PREFIX+'foreign')):
            with self.assertRaisesRegex(r.SystemdRelevanceError, 'UNIT_BINDING'): self.inspect(unit)
        with self.assertRaisesRegex(r.SystemdRelevanceError, 'DUPLICATE_UNIT'): self.inspect(detail(), detail())

    def test_facts_must_be_inside_fresh_scan_and_clocks_are_explicit(self):
        for at in (NOW-2, NOW+1, True):
            with self.assertRaises(r.SystemdRelevanceError): self.inspect(facts=self.facts(observed_at=at))
        for now in (NOW-1, NOW+60, True):
            with self.assertRaises(r.SystemdRelevanceError): self.inspect(now=now)
        self.inspect(facts=self.facts(observed_at=NOW-1))

    def test_scan_drift_and_unreadable_enumeration_are_rejected_before_selection(self):
        scan = self.scan(); facts = self.facts()
        changed = replace(scan, after=replace(scan.after, loaded_units=source.enumeration((source.unit(active_state='active'),))))
        bad = replace(scan, before=replace(scan.before, manager_jobs=d.Enumeration('unreadable', None, ())))
        for value in (changed, bad):
            with self.assertRaises(r.SystemdRelevanceError): self.inspect(scan=value, facts=facts)

    def test_strict_types_and_raw_free_fields_are_not_an_input_channel(self):
        for facts in (None, {}, replace(self.facts(), units=[]), replace(self.facts(), units=({},))):
            with self.assertRaises(r.SystemdRelevanceError): self.selector.inspect(self.scan(), facts, now=NOW)
        for unit in (replace(detail(), identities=[]), replace(detail(), paths=({},)), replace(detail(), bindings=({},))):
            with self.assertRaises(r.SystemdRelevanceError): self.inspect(unit)
        with self.assertRaises(TypeError): r.UnitFacts(**{**detail().__dict__, 'argv': 'SECRET'})
        with self.assertRaises(r.SystemdRelevanceError): r.SystemdRelevance({}, self.storage)

    def test_duplicate_paths_relations_bindings_are_not_silently_deduplicated(self):
        binding = r.ExternalBinding(model.target().instance, 'operator_task', HASH)
        for unit in (detail(paths=(path(), path())), detail(relations=(relation(), relation())), detail(bindings=(binding, binding))):
            with self.assertRaisesRegex(r.SystemdRelevanceError, 'DUPLICATE_FACT'): self.inspect(unit)

    def test_order_of_facts_units_and_edges_does_not_change_selection_digest(self):
        b = source.unit('other.service', 'other'); scan = self.scan((source.unit(), b))
        a = detail(paths=(path(), path(role='script')), identities=(identity(), identity(kind='effective', dynamic_user=None, process=process())))
        other = detail(b, relations=(relation(), relation(prop='After')))
        left = self.inspect(a, other, scan=scan)
        right = self.inspect(replace(other, relations=tuple(reversed(other.relations))), replace(a, paths=tuple(reversed(a.paths)), identities=tuple(reversed(a.identities))), scan=scan)
        self.assertEqual(left._canonical, right._canonical)

    def test_4096_loaded_units_are_preserved_without_128_row_projection(self):
        units = tuple(source.unit('u'+str(i)+'.service', 'u'+str(i)) for i in range(4096))
        result = self.inspect(detail(units[0], identities=(identity(),)), scan=self.scan(units, ()))
        self.assertEqual(len(result.private_manifest()['loaded_units']), 4096)
        self.assertEqual(result.report()['unresolved_loaded_units'], 4095)

    def test_detail_and_per_unit_limits_refuse_before_truncation(self):
        units = tuple(source.unit('u'+str(i)+'.service', 'u'+str(i)) for i in range(129)); scan = self.scan(units, ())
        with self.assertRaises(r.SystemdRelevanceError): self.inspect(*(detail(u) for u in units), scan=scan)
        for unit in (detail(identities=(identity(),)*65), detail(paths=(path(),)*65), detail(bindings=(r.ExternalBinding(model.target().instance, 'operator_task', HASH),)*17)):
            with self.assertRaises(r.SystemdRelevanceError): self.inspect(unit)

    def test_relation_limit_and_global_path_budget_are_effective(self):
        with self.assertRaises(r.SystemdRelevanceError): self.inspect(detail(relations=(relation(),)*8193))
        units = tuple(source.unit('u'+str(i)+'.service', 'u'+str(i)) for i in range(17)); scan = self.scan(units, ())
        details = tuple(detail(u, paths=tuple(path(path='/unrelated/'+str(i)) for i in range(64))) for u in units)
        with self.assertRaisesRegex(r.SystemdRelevanceError, 'FACT_LIMIT'): self.inspect(*details, scan=scan)

    def test_8192_real_edges_are_bounded_and_cycles_do_not_need_recursion(self):
        units = tuple(source.unit('u'+str(i)+'.service', 'u'+str(i)) for i in range(128)); scan = self.scan(units, ())
        details = tuple(detail(u, identities=(identity(),) if n == 0 else None,
            relations=tuple(relation('u'+str(i)+'.service', 'u'+str(i)) for i in range(64))) for n, u in enumerate(units))
        result = self.inspect(*details, scan=scan)
        self.assertEqual(result.report()['relation_count'], 8192)
        self.assertEqual(result.report()['related_loaded_units'], 128)

    def test_global_identity_and_relation_limits_apply_across_units(self):
        units = tuple(source.unit('u'+str(i)+'.service', 'u'+str(i)) for i in range(128)); scan = self.scan(units, ())
        identities = tuple(detail(u, identities=tuple(identity(kind='effective', dynamic_user=None,
            process=process(pid=n*64+i+1)) for i in range(64))) for n, u in enumerate(units[:17]))
        with self.assertRaisesRegex(r.SystemdRelevanceError, 'FACT_LIMIT'): self.inspect(*identities, scan=scan)
        relations = tuple(detail(u, relations=tuple(relation('u'+str(i)+'.service', 'u'+str(i))
            for i in range(65 if n == 127 else 64))) for n, u in enumerate(units))
        with self.assertRaisesRegex(r.SystemdRelevanceError, 'FACT_LIMIT'): self.inspect(*relations, scan=scan)

    def test_actual_four_mib_output_limit_refuses_instead_of_shortening(self):
        units = tuple(source.unit('u'+str(i)+'.service', 'u'+str(i)) for i in range(16))
        long = '/'+('x'*1700)+'/'
        files = tuple(d.InstalledUnitFile(long+'f'+str(i)+'.service', 'disabled') for i in range(1200))
        scan = self.scan(units, files)
        details = tuple(detail(u, paths=tuple(path(path='/'+('y'*1900)+'/'+str(i)) for i in range(64))) for u in units)
        with self.assertRaisesRegex(r.SystemdRelevanceError, 'DOCUMENT_LIMIT'): self.inspect(*details, scan=scan)

    def test_private_copy_repr_and_errors_do_not_expose_identities_paths_or_names(self):
        fact = detail(identities=(identity(),), paths=(path(),)); result = self.inspect(fact)
        original = result._canonical; data = result.private_manifest(); data['loaded_units'].clear()
        self.assertEqual(result._canonical, original)
        for obj in (result, self.selector, fact, identity(), path(), process(), self.facts()):
            for private in ('worker.service', '/var/lib/', '991', model.target().instance): self.assertNotIn(private, repr(obj))
        report = json.dumps(result.report())
        for private in ('worker.service', '/var/lib/', model.target().host_id): self.assertNotIn(private, report)
        with self.assertRaises(FrozenInstanceError): result._canonical = b'{}'
        try: self.inspect(detail(paths=(path(path='SECRET\nVALUE'),)))
        except r.SystemdRelevanceError as exc: self.assertNotIn('SECRET', str(exc))

    def test_validation_does_not_use_files_processes_network_or_implicit_clock(self):
        scan = self.scan(); facts = self.facts((detail(identities=(identity(),), paths=(path(),)),), scan)
        denied = ('builtins.open', 'os.open', 'os.stat', 'os.lstat', 'os.scandir', 'os.listdir', 'os.readlink',
                  'pathlib.Path.read_bytes', 'pathlib.Path.read_text', 'subprocess.Popen', 'subprocess.run',
                  'socket.socket', 'socket.create_connection', 'time.time', 'time.monotonic')
        with ExitStack() as guards:
            for name in denied: guards.enter_context(patch(name, side_effect=AssertionError('IO forbidden')))
            result = r.SystemdRelevance(model.target(), self.storage).inspect(scan, facts, now=NOW)
            self.assertEqual(result.report()['related_loaded_units'], 1)


if __name__ == '__main__': unittest.main()
