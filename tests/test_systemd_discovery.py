"""Pure declaration tests. No systemd transport or relevance recipe is run."""
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
import json
import unittest
from unittest.mock import patch

from installer import systemd_discovery as d
import test_launcher_inventory as model

NOW = model.NOW
HASH = 'c'*64


def provenance():
    t = model.target()
    return d.DiscoveryProvenance(t.host_id,t.boot_id,'system',':1.0','pid:[7]','pid:[7]',
        'mnt:[8]','mnt:[8]','257',('ListUnits','ListUnitFiles','ListJobs'),('/etc/systemd/system','/usr/lib/systemd/system'))


def unit(name='worker.service', obj='worker_2eservice', **changes):
    return replace(d.LoadedUnit(name,d.UNIT_PREFIX+obj,'loaded','inactive','dead','',
        (name,),d.UnitJobReference(0,'','/')),**changes)


def enumeration(rows=()): return d.Enumeration('observed', HASH, rows)


def round_value(**changes):
    return replace(d.DiscoveryRound(provenance(),enumeration((unit(),)),
        enumeration((d.InstalledUnitFile('/etc/systemd/system/worker.service','disabled'),)),enumeration()),**changes)


def scan(value=None, **changes):
    row = round_value() if value is None else value
    return replace(d.DiscoveryScan(model.target(),NOW-1,NOW,900,row,row),**changes)


def job(identifier=3, name='worker.service', obj='worker_2eservice', **changes):
    return replace(d.ManagerJob(identifier,name,'start','waiting',d.JOB_PREFIX+str(identifier),d.UNIT_PREFIX+obj),**changes)


class SystemdDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.storage=model.storage(); self.discovery=d.SystemdDiscovery(model.target(),self.storage)

    def inspect(self, value=None, **kwargs):
        return self.discovery.inspect(scan() if value is None else value,now=NOW,**kwargs)

    def rows(self, *, loaded=None, files=None, jobs=None):
        changes={}
        for key,value in (('loaded_units',loaded),('installed_unit_files',files),('manager_jobs',jobs)):
            if value is not None: changes[key]=enumeration(value)
        return scan(round_value(**changes))

    def test_three_populations_stay_separate_and_never_grant_inventory_or_actions(self):
        result=self.inspect(); data=result.private_manifest(); report=result.report()
        self.assertEqual((report['loaded_units'],report['installed_unit_files'],report['manager_jobs']),(1,1,0))
        self.assertEqual(data['coverage'],{key:'partial' if key=='systemd_system' else 'unknown' for key in d.l.CHANNELS})
        self.assertIsNone(data['observation']['installed_unit_files']['rows'][0]['loaded_object'])
        self.assertFalse(data['observation']['installed_unit_files']['rows'][0]['definition_verified'])
        self.assertTrue(set(d.BASE_BLOCKERS)<=set(report['blockers']))
        for key,value in report.items():
            if type(value) is bool: self.assertFalse(value,key)

    def test_empty_observed_populations_still_leave_all_global_claims_open(self):
        result=self.inspect(self.rows(loaded=(),files=(),jobs=()))
        self.assertEqual(result.report()['distinct_names'],0)
        self.assertEqual(result.report()['systemd_coverage'],'partial')
        self.assertFalse(result.report()['host_scheduler_inventory_complete'])

    def test_incomplete_or_unproven_enumeration_is_never_empty_success(self):
        for key in ('loaded_units','installed_unit_files','manager_jobs'):
            for state in ('unknown','unreadable','partial','complete',None):
                with self.subTest(key=key,state=state),self.assertRaisesRegex(d.SystemdDiscoveryError,'ENUMERATION_UNAVAILABLE'):
                    self.inspect(scan(round_value(**{key:d.Enumeration(state,None,())})))
            with self.assertRaises(d.SystemdDiscoveryError): self.inspect(scan(round_value(**{key:d.Enumeration('observed',None,())})))

    def test_target_and_storage_are_exact_and_existing_pin_contract_is_reused(self):
        for bad in (None,{},replace(model.target(),web_uid=True),replace(model.target(),source_commit='a'*40)):
            with self.assertRaisesRegex(d.SystemdDiscoveryError,'TARGET_OR_STORAGE_REJECTED'): d.SystemdDiscovery(bad,self.storage)
        with self.assertRaises(d.SystemdDiscoveryError): d.SystemdDiscovery(model.target(),{})
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'TARGET_MISMATCH'):
            self.inspect(scan(target=replace(model.target(),webroot='/srv/other')))

    def test_provenance_rejects_foreign_host_boot_manager_owner_and_namespaces(self):
        values={'host_id':'d'*32,'boot_id':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee','manager':'user',
            'bus_owner':'org.freedesktop.systemd1','pid_namespace':'pid:[9]','mount_namespace':'mnt:[9]',
            'init_pid_namespace':'pid:[9]','init_mount_namespace':'mnt:[9]'}
        for key,value in values.items():
            with self.subTest(key=key),self.assertRaisesRegex(d.SystemdDiscoveryError,'PROVENANCE_REJECTED'):
                self.inspect(scan(round_value(provenance=replace(provenance(),**{key:value}))))

    def test_version_capabilities_and_ordered_search_path_are_required(self):
        for change in ({'manager_version':''},{'capabilities':('ListUnits','ListJobs')},
                       {'capabilities':('ListUnits','ListJobs','ListUnitFiles','ListJobs')},
                       {'unit_paths':()},{'unit_paths':('/etc/systemd/system','/etc/systemd/system')},
                       {'unit_paths':('/etc/../systemd',)}):
            with self.subTest(change=change),self.assertRaises(d.SystemdDiscoveryError):
                self.inspect(scan(round_value(provenance=replace(provenance(),**change))))

    def test_declaration_order_is_normalized_except_search_path_precedence(self):
        a=unit(names=('alias.service','worker.service')); b=unit('second.service','second')
        left=round_value(loaded_units=enumeration((a,b)))
        right=replace(left,loaded_units=enumeration((b,replace(a,names=tuple(reversed(a.names))))),
                      provenance=replace(provenance(),capabilities=tuple(reversed(provenance().capabilities))))
        self.inspect(scan(left,after=right))
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'CHANGED_DURING_READ'):
            self.inspect(scan(left,after=replace(right,provenance=replace(provenance(),unit_paths=tuple(reversed(provenance().unit_paths))))))

    def test_aliases_do_not_create_multiple_loaded_objects_and_unknown_is_not_empty(self):
        row=unit(names=('worker.service','alias.service'))
        result=self.inspect(self.rows(loaded=(row,)))
        self.assertEqual(result.report()['loaded_units'],1); self.assertEqual(result.report()['distinct_names'],2)
        unknown=self.inspect(self.rows(loaded=(replace(row,names=None,following=None),)))
        data=unknown.private_manifest()['observation']['loaded_units']['rows'][0]
        self.assertIsNone(data['names']); self.assertIsNone(data['following'])
        self.assertIn('ALIASES_UNOBSERVED',unknown.report()['blockers'])
        for names in ((),('alias.service',),('worker.service','worker.service'),('worker.service','other.timer')):
            with self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(loaded=(replace(row,names=names),)))

    def test_duplicate_objects_primary_names_and_alias_ownership_are_refused(self):
        for rows in ((unit(),unit()),(unit(),unit('other.service','worker_2eservice')),
                     (unit(),unit('worker.service','different')),
                     (unit(names=('worker.service','alias.service')),unit('alias.service','other'))):
            with self.subTest(rows=repr(rows)),self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(loaded=rows))

    def test_following_relation_keeps_unknown_targets_and_cycles_without_merging(self):
        a=unit(following='other.service'); b=unit('other.service','other',following='worker.service')
        result=self.inspect(self.rows(loaded=(a,b)))
        self.assertEqual(result.report()['loaded_units'],2)
        self.assertNotIn('FOLLOWING_TARGET_UNRESOLVED',result.report()['blockers'])
        self.assertIn('FOLLOWING_TARGET_UNRESOLVED',self.inspect(self.rows(loaded=(a,))).report()['blockers'])

    def test_systemd_names_are_private_bounded_and_never_hestia_filtered(self):
        for name in ('other.service','-.mount','instance@tenant.service',r'backup\x2dnight.timer','x'*240+'.service','new.future'):
            with self.subTest(name=name): self.inspect(self.rows(loaded=(unit(name,'item'),)))
        for name in ('../x.service','x.service\n','@tenant.service','x@.service','bad*.service',r'bad\oops.service','x'*248+'.service'):
            with self.subTest(name=name),self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(loaded=(unit(name,'item'),)))

    def test_templates_disabled_masked_and_unknown_file_states_are_preserved(self):
        files=tuple(d.InstalledUnitFile('/etc/systemd/system/a'+str(i)+'@.service',state)
                    for i,state in enumerate(('disabled','masked','static','indirect','future-state')))
        result=self.inspect(self.rows(loaded=(),files=files))
        self.assertEqual(result.report()['installed_unit_files'],5)
        self.assertEqual({r['enablement'] for r in result.private_manifest()['observation']['installed_unit_files']['rows']},
                         {'disabled','masked','static','indirect','future-state'})

    def test_file_paths_are_not_merged_by_basename_and_duplicates_are_refused(self):
        a=d.InstalledUnitFile('/etc/systemd/system/a.service','enabled')
        b=replace(a,path='/run/systemd/generator/a.service')
        result=self.inspect(self.rows(loaded=(),files=(a,b)))
        self.assertEqual(result.report()['installed_unit_files'],2); self.assertEqual(result.report()['distinct_names'],1)
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'DUPLICATE_FILE'): self.inspect(self.rows(files=(a,a)))
        for path in ('relative/a.service','/a/../b.service','/a//b.service','/a.service\n'):
            with self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(files=(replace(a,path=path),)))

    def test_transient_or_other_unit_types_need_no_fabricated_fragment(self):
        for name in ('run-task.service','transient.scope','devices.target','future.future'):
            result=self.inspect(self.rows(loaded=(unit(name,'transient',names=None),),files=()))
            row=result.private_manifest()['observation']['loaded_units']['rows'][0]
            self.assertNotIn('fragment',row); self.assertNotIn('uid',row)
            self.assertFalse(result.report()['relevance_classified'])

    def test_unknown_loaded_states_are_retained_without_execution_claim(self):
        row=unit(load_state='future-load',active_state='future-active',sub_state='future-sub')
        result=self.inspect(self.rows(loaded=(row,)))
        self.assertEqual(result.private_manifest()['observation']['loaded_units']['rows'][0]['active_state'],'future-active')
        self.assertFalse(result.report()['execution_allowed'])

    def test_job_with_loaded_unit_and_alias_is_bound_without_duplicate_unit(self):
        queued=job(name='alias.service'); ref=d.UnitJobReference(3,'start',d.JOB_PREFIX+'3')
        result=self.inspect(self.rows(loaded=(unit(names=('worker.service','alias.service'),job=ref),),jobs=(queued,)))
        self.assertEqual(result.report()['loaded_units'],1); self.assertEqual(result.report()['manager_jobs'],1)
        self.assertIn('SYSTEM_MANAGER_JOB_PRESENT',result.report()['blockers'])

    def test_job_without_loaded_detail_and_listed_job_without_job_row_remain_visible(self):
        result=self.inspect(self.rows(loaded=(),files=(),jobs=(job(),)))
        self.assertIn('JOB_UNIT_DETAIL_UNAVAILABLE',result.report()['blockers'])
        self.assertEqual(result.report()['manager_jobs'],1)
        result=self.inspect(self.rows(loaded=(unit(job=d.UnitJobReference(3,'start',d.JOB_PREFIX+'3')),)))
        self.assertIn('LISTED_JOB_DETAIL_UNAVAILABLE',result.report()['blockers'])

    def test_unknown_alias_for_job_is_retained_as_unverified(self):
        row=unit(names=None,job=d.UnitJobReference(3,'start',d.JOB_PREFIX+'3'))
        result=self.inspect(self.rows(loaded=(row,),jobs=(job(name='unobserved-alias.service'),)))
        self.assertIn('JOB_NAME_BINDING_UNVERIFIED',result.report()['blockers'])

    def test_job_id_type_object_and_name_contradictions_are_rejected(self):
        row=unit(job=d.UnitJobReference(3,'start',d.JOB_PREFIX+'3'))
        for queued in (job(obj='foreign'),job(kind='stop'),job(name='foreign.service'),job(identifier=4),
                       job(object_path=d.JOB_PREFIX+'4'),job(unit_object_path='/foreign'),job(identifier=True)):
            with self.subTest(queued=repr(queued)),self.assertRaises(d.SystemdDiscoveryError):
                self.inspect(self.rows(loaded=(row,),jobs=(queued,)))
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'DUPLICATE_JOB'): self.inspect(self.rows(loaded=(),jobs=(job(),job())))
        for other in (job(identifier=4,obj='other'),job(identifier=4,name='alias.service')):
            with self.assertRaisesRegex(d.SystemdDiscoveryError,'JOB_BINDING_CONFLICT'):
                self.inspect(self.rows(loaded=(),jobs=(job(),other)))
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'JOB_BINDING_CONFLICT'):
            self.inspect(self.rows(loaded=(row,unit('other.service','other',job=row.job))))

    def test_zero_job_tuple_and_uint32_limits_are_exact(self):
        for reference in (d.UnitJobReference(False,'','/'),d.UnitJobReference(0,'start','/'),
                          d.UnitJobReference(0,'',d.JOB_PREFIX+'0'),d.UnitJobReference(2**32,'start',d.JOB_PREFIX+str(2**32))):
            with self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(loaded=(unit(job=reference),)))
        self.inspect(self.rows(loaded=(),jobs=(job(identifier=2**32-1),)))

    def test_exact_dto_and_tuple_types_reject_free_dicts_lists_and_subclasses(self):
        class OtherUnit(d.LoadedUnit): pass
        row=unit()
        for bad in ({'primary_name':'worker.service'},OtherUnit(**vars(row))):
            with self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(loaded=(bad,)))
        for bad in ([],[row]):
            with self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(loaded=bad))
        with self.assertRaises(d.SystemdDiscoveryError): self.inspect({})
        with self.assertRaises(d.SystemdDiscoveryError): self.inspect(scan(before=None))
        with self.assertRaises(d.SystemdDiscoveryError): self.inspect(self.rows(jobs=(row,)))

    def test_bad_identifiers_controls_and_properties_never_echo_private_input(self):
        for changes in ({'object_path':'/private-secret'}, {'active_state':'secret\ncommand'},
                        {'following':'secret;command.service'}, {'names':('worker.service','secret\n.service')},
                        {'load_state':True}, {'job':{}}):
            with self.subTest(changes=changes):
                try: self.inspect(self.rows(loaded=(replace(unit(),**changes),)))
                except d.SystemdDiscoveryError as error:
                    self.assertRegex(str(error),r'^DISCOVERY_[A-Z_]+$'); self.assertNotIn('secret',str(error))
                else: self.fail('Malformed declaration was accepted')

    def test_before_after_changes_in_unit_file_job_and_raw_evidence_are_refused(self):
        first=round_value()
        changes=(replace(first,loaded_units=enumeration((unit(active_state='active',sub_state='running'),))),
                 replace(first,installed_unit_files=enumeration((d.InstalledUnitFile('/etc/systemd/system/worker.service','enabled'),))),
                 replace(first,manager_jobs=enumeration((job(name='other.service',obj='other'),))),
                 replace(first,loaded_units=replace(first.loaded_units,evidence_sha256='d'*64)))
        for after in changes:
            with self.subTest(after=repr(after)),self.assertRaisesRegex(d.SystemdDiscoveryError,'CHANGED_DURING_READ'):
                self.inspect(scan(first,after=after))

    def test_before_after_alias_and_bus_owner_changes_are_refused(self):
        first=round_value(loaded_units=enumeration((unit(names=('worker.service','alias.service')),)))
        for after in (replace(first,provenance=replace(provenance(),bus_owner=':1.1')),
                      replace(first,loaded_units=enumeration((unit(names=('worker.service','other.service')),)))):
            with self.assertRaisesRegex(d.SystemdDiscoveryError,'CHANGED_DURING_READ'): self.inspect(scan(first,after=after))

    def test_explicit_clocks_and_age_are_bounded_without_reading_clock(self):
        self.inspect(scan(started_at=NOW-60,finished_at=NOW,elapsed_ms=60000))
        for changes in ({'started_at':NOW-61},{'finished_at':NOW+1},{'finished_at':NOW-2},
                        {'elapsed_ms':60001},{'elapsed_ms':-1},{'elapsed_ms':True},{'started_at':True},
                        {'started_at':-1},{'finished_at':2**63}):
            with self.subTest(changes=changes),self.assertRaises(d.SystemdDiscoveryError): self.inspect(scan(**changes))
        for now in (True,NOW-1,2**63):
            with self.assertRaises(d.SystemdDiscoveryError): self.discovery.inspect(scan(),now=now)

    def test_previous_comparison_accepts_new_interval_but_not_changed_observations(self):
        previous=self.inspect()
        next_scan=scan(started_at=NOW,finished_at=NOW+1)
        current=self.discovery.inspect(next_scan,now=NOW+1,previous=previous)
        self.assertEqual(current.private_manifest()['observation'],previous.private_manifest()['observation'])
        changed=self.rows(loaded=(unit(active_state='active'),))
        changed=replace(changed,started_at=NOW,finished_at=NOW+1)
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'OBSERVATIONS_CHANGED'):
            self.discovery.inspect(changed,now=NOW+1,previous=previous)

    def test_previous_comparison_refuses_backdated_overlapping_interval(self):
        previous=self.inspect()
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'REPLAYED'): self.inspect(previous=previous)
        old=previous.private_manifest(); old['interval']['finished_at']=NOW+5
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'REPLAYED'):
            self.discovery.inspect(scan(started_at=NOW,finished_at=NOW),now=NOW,previous=d.DiscoveryIndex(d._json(old)))

    def test_previous_is_bounded_typed_and_cannot_hide_extra_fields(self):
        for previous in ({},d.DiscoveryIndex('private'),d.DiscoveryIndex(b'x'*(d.MAX_BYTES+1)),
                         d.DiscoveryIndex(b'{}'),d.DiscoveryIndex(b'{"x":1,"x":2}')):
            with self.assertRaises(d.SystemdDiscoveryError): self.inspect(previous=previous)
        data=self.inspect().private_manifest(); data['extra']='private'
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'OBSERVATIONS_CHANGED'):
            self.discovery.inspect(scan(started_at=NOW,finished_at=NOW),now=NOW,previous=d.DiscoveryIndex(d._json(data)))

    def test_previous_binds_entire_storage_manifest(self):
        previous=self.inspect(); raw=self.storage.private_manifest(); raw['blockers'].append('ADDITIONAL_REQUIREMENT')
        storage=type(self.storage)(json.dumps(raw).encode())
        other=d.SystemdDiscovery(model.target(),storage)
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'OBSERVATIONS_CHANGED'):
            other.inspect(scan(started_at=NOW,finished_at=NOW),now=NOW,previous=previous)

    def test_all_population_limits_are_enforced_before_iteration(self):
        for key,rows in (('loaded',(unit(),)*(d.MAX_UNITS+1)),
                         ('files',(d.InstalledUnitFile('/a.service','static'),)*(d.MAX_FILES+1)),
                         ('jobs',(job(),)*(d.MAX_JOBS+1))):
            with self.subTest(key=key),self.assertRaisesRegex(d.SystemdDiscoveryError,'LIMIT_OR_TYPE_REJECTED'):
                self.inspect(self.rows(**{key:rows}))

    def test_4096_loaded_objects_are_kept_without_128_row_projection(self):
        rows=tuple(unit('u'+str(i)+'.service','u'+str(i),names=None) for i in range(4096))
        result=self.inspect(self.rows(loaded=rows,files=()))
        self.assertEqual(result.report()['loaded_units'],4096)
        self.assertEqual(result.report()['distinct_names'],4096)
        self.assertFalse(result.report()['projection_delivered'])

    def test_distinct_name_union_includes_alias_file_job_and_following(self):
        aliases=tuple(['worker.service']+['a'+str(i)+'.service' for i in range(4095)])
        value=self.rows(loaded=(unit(names=aliases),),files=())
        self.assertEqual(self.inspect(value).report()['distinct_names'],4096)
        variants=(replace(value.before,installed_unit_files=enumeration((d.InstalledUnitFile('/new.service','static'),))),
                  replace(value.before,manager_jobs=enumeration((job(name='new.service',obj='new'),))),
                  replace(value.before,loaded_units=enumeration((unit(names=aliases,following='new.service'),))))
        for row in variants:
            with self.assertRaisesRegex(d.SystemdDiscoveryError,'NAME_LIMIT'): self.inspect(scan(row))

    def test_actual_four_mib_envelope_limit_does_not_truncate(self):
        rows=tuple(unit('u'+str(i)+'.service','a'*1100+str(i),names=None) for i in range(4096))
        with self.assertRaisesRegex(d.SystemdDiscoveryError,'DOCUMENT_LIMIT'): self.inspect(self.rows(loaded=rows,files=()))

    def test_repr_report_and_frozen_manifest_keep_private_data_private(self):
        value=scan(); result=self.inspect(value)
        output=repr(self.discovery)+repr(value)+repr(value.before)+repr(value.before.provenance)+repr(unit())+repr(result)+json.dumps(result.report())
        for private in (model.target().webroot,model.target().host_id,model.target().boot_id,
                        'worker.service','/etc/systemd/system',':1.0','pid:[7]'):
            self.assertNotIn(private,output)
        altered=result.private_manifest(); altered['observation'].clear()
        self.assertEqual(result.report()['loaded_units'],1)
        with self.assertRaises(FrozenInstanceError): result._canonical=b'{}'
        with self.assertRaises(FrozenInstanceError): value.started_at=0

    def test_validator_has_no_files_processes_network_or_implicit_clock(self):
        value=scan(); storage=self.storage; target=model.target()
        guards=('builtins.open','os.open','os.listdir','os.stat','subprocess.run','subprocess.Popen',
                'socket.socket','socket.create_connection','time.time','time.monotonic')
        with ExitStack() as stack:
            for name in guards: stack.enter_context(patch(name,side_effect=AssertionError('Unexpected IO')))
            result=d.SystemdDiscovery(target,storage).inspect(value,now=NOW)
            self.assertEqual(result.report()['loaded_units'],1)


if __name__ == '__main__': unittest.main()
