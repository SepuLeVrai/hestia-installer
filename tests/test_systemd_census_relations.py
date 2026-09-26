"""Typed census signals and bounded relation composition; no host mock proof."""
from contextlib import contextmanager
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from installer import systemd_census_relations as y
import test_systemd_census_invocations as bridge
import test_systemd_relevance as pure

c,d,t,v,r,x=y.c,y.d,y.t,y.v,y.r,y.x
HASH='e'*64


class CandidateFactTests(unittest.TestCase):
    def setUp(self):
        self.base=pure.SystemdRelevanceTests();self.base.setUp()
        self.fact=r.CensusCandidateFact(pure.process(),('UID_MATCH_IN_TASK',),HASH,'f'*64)
    def inspect(self,fact=None,*,census=HASH,**kw):
        units=(pure.detail(candidates=(self.fact if fact is None else fact,)),)
        facts=self.base.facts(units,census_sha256=census,**kw)
        return self.base.inspect(facts=facts)

    def test_all_positive_signals_are_distinct_from_effective_identity(self):
        for reason in r.CANDIDATE_REASONS[:-1]:
            sample=self.inspect(replace(self.fact,reasons=(reason,)));row=self.base.row(sample)
            self.assertEqual(row['reasons'],['CENSUS_'+reason]);self.assertEqual(row['decision'],'RELATED_UNMANAGED')
            detail=sample.private_manifest()['facts']['units'][0]
            self.assertIsNone(detail['identities']);self.assertIsNone(detail['bindings'])

    def test_unknown_only_is_not_a_positive_seed(self):
        row=self.base.row(self.inspect(replace(self.fact,reasons=('UNRESOLVED_TASK',))))
        self.assertEqual(row['decision'],'UNRESOLVED');self.assertEqual(row['reasons'],[])
        self.assertIn('CENSUS_TASK_UNRESOLVED',row['issues'])

    def test_unknown_does_not_erase_positive_signal(self):
        row=self.base.row(self.inspect(replace(self.fact,reasons=('UNRESOLVED_TASK','DESCENDANT_AT_OBSERVATION'))))
        self.assertEqual(row['decision'],'RELATED_UNMANAGED');self.assertIn('CENSUS_TASK_UNRESOLVED',row['issues'])

    def test_census_digest_required_and_exact_without_authenticity_claim(self):
        for digest in (None,'d'*64,'bad'):
            with self.assertRaises(r.SystemdRelevanceError):self.inspect(census=digest)
        for key,value in self.inspect().report().items():
            if type(value) is bool:self.assertFalse(value,key)

    def test_reason_set_closed_nonempty_unique_and_typed(self):
        for reasons in ((),[],('UNRESOLVED_TASK',)*2,('EFFECTIVE_IDENTITY_MATCH',),('operator_task',),(True,),tuple(r.CANDIDATE_REASONS)+('EXTRA',)):
            with self.subTest(reasons=reasons),self.assertRaises(r.SystemdRelevanceError):self.inspect(replace(self.fact,reasons=reasons))

    def test_bound_leader_context_limits_and_digests(self):
        for changes in ({'pid':1},{'pid':True},{'pid':2**31},{'start_ticks':-1},{'pid_namespace':'pid:[9]'},
                        {'cgroup':'/bad/../path'},{'membership_sha256':'bad'}):
            with self.assertRaises(r.SystemdRelevanceError):self.inspect(replace(self.fact,process=replace(self.fact.process,**changes)))
        with self.assertRaises(r.SystemdRelevanceError):self.inspect(replace(self.fact,binding_sha256='bad'))
        self.assertEqual(self.base.row(self.inspect(replace(self.fact,process=replace(self.fact.process,start_ticks=0))))['decision'],'RELATED_UNMANAGED')

    def test_128_candidates_allowed_per_unit_129_or_duplicates_refused(self):
        def facts(count):return tuple(replace(self.fact,process=replace(self.fact.process,pid=n+2)) for n in range(count))
        sample=self.base.inspect(facts=self.base.facts((pure.detail(candidates=facts(128)),),census_sha256=HASH))
        self.assertEqual(len(sample.private_manifest()['facts']['units'][0]['candidates']),128)
        for values in (facts(129),(self.fact,self.fact)):
            with self.assertRaises(r.SystemdRelevanceError):self.base.inspect(facts=self.base.facts((pure.detail(candidates=values),),census_sha256=HASH))

    def test_process_not_claimed_by_two_objects_or_inconsistent_identity(self):
        other=pure.source.unit('other.service','other');scan=self.base.scan((pure.source.unit(),other))
        facts=self.base.facts((pure.detail(candidates=(self.fact,)),pure.detail(other,candidates=(self.fact,))),scan,census_sha256=HASH)
        with self.assertRaisesRegex(r.SystemdRelevanceError,'PROCESS_BINDING'):self.base.inspect(scan=scan,facts=facts)
        ident=pure.identity(kind='effective',dynamic_user=None,process=replace(self.fact.process,start_ticks=999))
        facts=self.base.facts((pure.detail(identities=(ident,),candidates=(self.fact,)),),census_sha256=HASH)
        with self.assertRaisesRegex(r.SystemdRelevanceError,'PROCESS_BINDING'):self.base.inspect(facts=facts)

    def test_candidate_count_is_cumulative_across_units(self):
        other=pure.source.unit('other.service','other');scan=self.base.scan((pure.source.unit(),other))
        left=tuple(replace(self.fact,process=replace(self.fact.process,pid=n+2)) for n in range(65))
        right=tuple(replace(self.fact,process=replace(self.fact.process,pid=n+200)) for n in range(64))
        facts=self.base.facts((pure.detail(candidates=left),pure.detail(other,candidates=right)),scan,census_sha256=HASH)
        with self.assertRaisesRegex(r.SystemdRelevanceError,'FACT_LIMIT'):self.base.inspect(scan=scan,facts=facts)

    def test_absent_empty_and_private_candidate_payloads(self):
        sample=self.inspect();data=sample.private_manifest()
        for raw in (repr(self.fact),json.dumps(sample.report())):
            self.assertNotIn('/system.slice/worker.service',raw)
        data['facts']['units'][0]['candidates'].clear();self.assertEqual(len(sample.private_manifest()['facts']['units'][0]['candidates']),1)
        none=self.base.inspect(pure.detail()).private_manifest()['facts']['units'][0]['candidates']
        empty=self.base.inspect(pure.detail(candidates=())).private_manifest()['facts']['units'][0]['candidates']
        self.assertIsNone(none);self.assertEqual(empty,[])


class CensusRelationTests(unittest.TestCase):
    def setUp(self):
        self.base=bridge.CensusInvocationTests();self.base.setUp()
        self.reader=y.SystemdCensusRelations(self.base.target,bridge.invocation.base.model.storage())
        self.properties=[];self.values={p:[] for p in x.RELATIONS}
        self.values['Names']=['worker.service','worker-alias.service'];self.values['Requires']=['foreign.service','unloaded.service']
        row=list(self.base.bus.base.loaded[0]);row[0]='foreign.service';row[6]+='foreign';self.base.bus.base.loaded.append(row)
        self.base.bus.base.files.append(['/etc/systemd/system/unloaded.service','disabled'])
    def reply(self,argv,budget,*,pass_fds=()):
        if argv[-1] in x.PROPERTIES and 'Get' in argv:
            self.properties.append(argv);self.assertEqual(pass_fds,())
            raw=bridge.invocation.base.encoded('v',{'type':'as','data':self.values[argv[-1]]})
            budget.calls+=1;budget.bytes+=len(raw);return raw
        return self.base.bus.reply(argv,budget,pass_fds=pass_fds)
    @contextmanager
    def fixture(self,reply=None,**kw):
        with self.base.fixture(reply=reply or self.reply,**kw) as result:yield result

    def test_multiple_leaders_one_unit_use_one_property_set_per_pass(self):
        with self.fixture() as (opened,closed):sample=self.reader.collect()
        self.assertEqual(opened.call_count,4);self.assertEqual(closed.call_count,4)
        self.assertEqual(sample.report()['bus_calls'],58);self.assertEqual(len(sample.facts().units),1)
        self.assertEqual(len(sample.facts().units[0].candidates),2)
        self.assertEqual([a[-1] for a in self.properties],list(x.PROPERTIES)*2)
        self.assertEqual([fds for a,fds in self.base.bus.commands if 'GetUnitByPIDFD' in a],[(92,),(94,),(92,),(94,)])

    def test_thread_and_descendant_signals_expand_loaded_relations_only(self):
        with self.fixture():sample=self.reader.collect()
        self.assertEqual(sample.report()['related_loaded_units'],2)
        unit=sample.facts().units[0];self.assertIsNone(unit.identities);self.assertIsNone(unit.bindings)
        self.assertEqual({reason for fact in unit.candidates for reason in fact.reasons},{'UID_MATCH_IN_TASK','DESCENDANT_AT_OBSERVATION'})
        self.assertTrue(any(f.target_name=='unloaded.service' and f.target_object is None for f in unit.relations))
        self.assertTrue(all(f['decision']=='UNRESOLVED' for f in sample.selection().private_manifest()['installed_unit_files']))

    def test_unknown_only_stays_unresolved_without_seeding_dependencies(self):
        rows=(self.base.rows[0],self.base.rows[1],bridge.census.task(tid=3,issue='TASK_UNREADABLE'),self.base.rows[3])
        with self.fixture(rows=rows):sample=self.reader.collect()
        self.assertEqual(sample.report()['related_loaded_units'],0)
        self.assertEqual(sample.report()['unresolved_tasks'],1)
        self.assertEqual(sample.facts().units[0].candidates[0].reasons,('UNRESOLVED_TASK',))

    def test_unbound_unknown_and_nonmatching_records_survive(self):
        rows=(*self.base.rows[:3],bridge.census.task(4,issue='TASK_UNREADABLE'))
        with self.fixture(rows=rows):sample=self.reader.collect()
        self.assertEqual(sample.report()['unbound_candidate_groups'],1)
        self.assertEqual(len(sample.private_manifest()['census']['tasks']),4)
        self.assertEqual(sample.private_manifest()['unbound'][0]['tgid'],4)

    def test_no_candidates_keeps_lists_without_property_calls(self):
        rows=tuple(replace(row,identity=replace(row.identity,uids=(900,)*4,gids=(900,)*4)) for row in self.base.rows)
        with self.fixture(rows=rows):sample=self.reader.collect()
        self.assertEqual(sample.report()['bus_calls'],24);self.assertEqual(self.properties,[])
        self.assertEqual(sample.report()['related_loaded_units'],0);self.assertEqual(sample.facts().units,())

    def test_census_and_every_binding_digest_are_part_of_enriched_index(self):
        with self.fixture():sample=self.reader.collect()
        data=sample.private_manifest();digest=d.l._sha(d._json(data['census']))
        self.assertEqual(sample.facts().census_sha256,digest)
        self.assertEqual(sample.facts().discovery_sha256,sample.index().report()['manifest_sha256'])
        self.assertTrue(all(f.census_sha256==digest for f in sample.facts().units[0].candidates))
        wrong=replace(sample.facts(),census_sha256='f'*64)
        with self.assertRaises(r.SystemdRelevanceError):self.reader._transport._selector.inspect(sample.scan(),wrong,now=sample.scan().finished_at)

    def test_alias_conflicts_and_changed_properties_refused(self):
        with self.fixture(),patch.dict(self.values,Names=['worker.service','foreign.service']),self.assertRaises(c.CensusError):self.reader.collect()
        seen=0
        def reply(argv,budget,**kw):
            nonlocal seen
            if argv[-1]=='Names':
                seen+=1
                if seen==2:self.values['Wants']=['new.service']
            return self.reply(argv,budget,**kw)
        with self.fixture(reply),self.assertRaises(c.CensusError):self.reader.collect()

    def test_relation_budget_refuses_before_partial_receipt_and_closes_fds(self):
        with self.fixture() as (_,closed),patch.object(r,'MAX_RELATIONS',1),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(closed.call_count,4)

    def test_proc_change_after_properties_refuses(self):
        changed=(*self.base.rows[:2],replace(self.base.rows[2],identity=replace(self.base.rows[2].identity,uids=(0,)*4)),self.base.rows[3])
        with self.fixture(passes=[self.base.rows,changed]) as (_,closed),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(len(self.properties),18);self.assertEqual(closed.call_count,4)

    def test_property_failure_no_retry_no_partial_receipt(self):
        def reply(argv,budget,**kw):
            if argv[-1]=='Names':raise OSError('secret-path-unit')
            return self.reply(argv,budget,**kw)
        with self.fixture(reply) as (_,closed):
            with self.assertRaises(c.CensusError) as error:self.reader.collect()
        self.assertNotIn('secret',str(error.exception));self.assertEqual(closed.call_count,4)

    def test_properties_have_only_bound_invocation_destinations_and_no_recursive_calls(self):
        with self.fixture():self.reader.collect()
        self.assertEqual(len(self.properties),18)
        self.assertTrue(all(v._path(bridge.invocation.ID)==a[a.index('call')+2] for a in self.properties))
        self.assertTrue(all('--auto-start=no' in a for a in self.properties))
        self.assertTrue(all(a[-1] in x.PROPERTIES for a in self.properties))

    def test_shared_budget_exact_n_and_m_formula(self):
        budgets=[];original=self.reader._properties
        def observe(*args):
            b=args[-1];budgets.append((id(b),b.started,b.deadline,b.maximum_calls));return original(*args)
        with self.fixture(),patch.object(self.reader,'_properties',side_effect=observe):sample=self.reader.collect()
        self.assertEqual(len(budgets),2);self.assertEqual(budgets[0],budgets[1]);self.assertEqual(budgets[0][3],58)
        self.assertEqual(sample.report()['bus_calls'],58)

    def test_public_report_and_repr_expose_no_private_signals_or_authority(self):
        with self.fixture():sample=self.reader.collect()
        for secret in ('worker.service','/group',bridge.invocation.ID,'pid:[7]'):
            self.assertNotIn(secret,json.dumps(sample.report())+repr(sample)+repr(self.reader))
        true={'visible_task_enumeration_observed','system_manager_lists_observed','candidate_leader_bindings_observed',
            'systemd_bindings_observed','selected_relations_observed','census_candidate_signals_observed'}
        for key,value in sample.report().items():
            if type(value) is bool:self.assertEqual(value,key in true,key)
        self.assertEqual(sample.report()['known_provisioned_units'],0)

    def test_combined_envelope_limit_and_no_old_sample_input(self):
        with patch.object(t.time,'monotonic',return_value=100):
            with self.fixture():sample=self.reader.collect()
            with self.fixture() as (_,closed),patch.object(d,'MAX_BYTES',len(sample._canonical)-1),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(closed.call_count,4)
        with self.assertRaises(TypeError):self.reader.collect(sample)


if __name__=='__main__':unittest.main()
