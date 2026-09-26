"""Live census lifecycle composition, grouping, refusal and private boundaries."""
from contextlib import contextmanager
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from installer import systemd_census_invocations as z
import test_process_census as census
import test_systemd_invocation as invocation

c, v, t = z.c, z.v, z.t


class CensusInvocationTests(unittest.TestCase):
    def setUp(self):
        self.bus = invocation.SystemdInvocationTests(); self.bus.setUp()
        self.target = invocation.base.model.target()
        self.reader = z.SystemdCensusInvocations(self.target, invocation.base.model.storage())
        self.rows = (census.task(1,parent=0,start=1), census.task(uid=0),
            census.task(tid=3,uid=self.target.web_uid), census.task(4,parent=2,start=30))
        self.topology = ((1,(1,)),(2,(2,3)),(4,(4,)))
        self.context = (census.CONTEXT, invocation.base.local()['identity'] | {})
        self.context = (self.context[0], {k:self.context[1][k] for k in ('host_id','boot_id','namespaces')})

    @contextmanager
    def fixture(self, *, rows=None, passes=None, reply=None, context=None, topologies=None):
        with self.bus.base.fixture(reply=reply or self.bus.reply), patch.object(c.p,'_directory',census.directory), \
             patch.object(c,'_context',return_value=context or self.context), \
             patch.object(c,'_topology',side_effect=topologies,return_value=self.topology), \
             patch.object(c.os,'pidfd_open',side_effect=[91,92,93,94]) as opened, \
             patch.object(c.os,'get_inheritable',return_value=False),patch.object(c.os,'close') as closed, \
             patch.object(c.ProcessCensus,'_pass',side_effect=passes,return_value=rows or self.rows), \
             patch.object(c,'_fd_pid'),patch.object(c,'_live',return_value=True),patch.object(v,'_alive'):
            yield opened,closed

    def test_multiple_leaders_same_unit_same_owned_descriptors_both_passes(self):
        with self.fixture() as (opened,closed): sample=self.reader.collect()
        self.assertEqual([x.args for x in opened.call_args_list],[(n,c.PIDFD_THREAD) for n in (1,2,3,4)])
        self.assertEqual([x.args for x in closed.call_args_list],[(n,) for n in (91,92,93,94)])
        mapped=[fds for argv,fds in self.bus.commands if 'GetUnitByPIDFD' in argv]
        self.assertEqual(mapped,[(92,),(94,),(92,),(94,)])
        self.assertEqual(sample.report()['bus_calls'],40)
        self.assertEqual((sample.report()['bound_candidate_leaders'],sample.report()['candidate_units']),(2,1))
        self.assertEqual(sample.private_manifest()['units'][0]['candidate_leaders'],[2,4])

    def test_thread_credentials_and_descendant_reasons_not_mislabeled_leader_identity(self):
        with self.fixture(): sample=self.reader.collect()
        data=sample.private_manifest();unit=data['units'][0]
        self.assertIn('UID_MATCH_IN_TASK',unit['review_reasons']);self.assertIn('DESCENDANT_AT_OBSERVATION',unit['review_reasons'])
        self.assertEqual(data['census']['tasks'][1]['identity']['uids'],[0]*4)
        self.assertEqual(data['census']['tasks'][2]['identity']['uids'],[self.target.web_uid]*4)
        self.assertFalse(sample.report()['all_task_unit_memberships_observed'])
        self.assertEqual(unit['decision'],'REVIEW_REQUIRED');self.assertFalse(unit['enrolled'])

    def test_unknown_leader_retained_unbound_while_unknown_thread_preserves_known_leader(self):
        rows=(self.rows[0],self.rows[1],census.task(tid=3,issue='TASK_UNREADABLE'),census.task(4,issue='TASK_UNREADABLE'))
        with self.fixture(rows=rows):sample=self.reader.collect()
        self.assertEqual(sample.report()['unresolved_tasks'],2)
        self.assertEqual(sample.report()['bound_candidate_leaders'],1)
        self.assertEqual(sample.private_manifest()['unbound'][0]['tgid'],4)
        self.assertEqual(sample.private_manifest()['unbound'][0]['issue'],'LEADER_IDENTITY_UNAVAILABLE')

    def test_init_candidate_never_sent_to_mapping(self):
        rows=(census.task(1,parent=0,start=1,issue='TASK_UNREADABLE'),*self.rows[1:])
        with self.fixture(rows=rows):sample=self.reader.collect()
        self.assertEqual(sample.private_manifest()['unbound'][0]['issue'],'INIT_LEADER_UNSUPPORTED')
        self.assertNotIn((91,),[fds for argv,fds in self.bus.commands if 'GetUnitByPIDFD' in argv])

    def test_no_candidates_still_keeps_population_and_two_list_rounds(self):
        rows=tuple(replace(row,identity=replace(row.identity,uids=(900,)*4,gids=(900,)*4)) for row in self.rows)
        with self.fixture(rows=rows):sample=self.reader.collect()
        self.assertEqual(sample.report()['candidate_groups'],0);self.assertEqual(sample.report()['bus_calls'],24)
        self.assertEqual(len(sample.private_manifest()['census']['tasks']),4)

    def test_caller_cannot_supply_old_sample_hints_or_descriptors(self):
        with patch.object(t,'_local') as host:
            for value in ((),[],c.CensusSample(b'{}'),91):
                with self.assertRaises(TypeError):self.reader.collect(value)
            host.assert_not_called()

    def test_unknown_mapped_object_or_name_refused_before_properties(self):
        for index,value in ((0,invocation.PATH+'other'),(1,'other.service')):
            self.bus.setUp();self.bus.mapping[index]=value
            with self.fixture() as (_,closed),self.assertRaises(c.CensusError):self.reader.collect()
            self.assertEqual(closed.call_count,4);self.assertEqual(len(self.bus.commands),13)

    def test_same_object_different_invocations_within_pass_refused(self):
        one=v.InvocationBinding(invocation.PATH,'worker.service',2,invocation.ID,v._path(invocation.ID))
        two=replace(one,pid_hint=4,invocation_id='ab'*16,invocation_path=v._path('ab'*16))
        with self.fixture(),patch.object(self.reader,'_bindings',return_value=(one,two)),self.assertRaises(c.CensusError):self.reader.collect()

    def test_invocation_identifier_cannot_bind_two_objects(self):
        one=v.InvocationBinding(invocation.PATH,'worker.service',2,invocation.ID,v._path(invocation.ID))
        two=replace(one,pid_hint=4,object_path=invocation.PATH+'other',primary_name='other.service')
        with self.assertRaises(t.SystemdTransportError):z._groups((one,two),((2,('A',)),(4,('B',))))

    def test_two_mapping_passes_must_match(self):
        original=self.reader._bindings;calls=0
        def changing(*args):
            nonlocal calls
            result=original(*args);calls+=1
            return result if calls==1 else (replace(result[0],invocation_id='ab'*16),*result[1:])
        with self.fixture(),patch.object(self.reader,'_bindings',side_effect=changing),self.assertRaises(c.CensusError):self.reader.collect()

    def test_proc_changes_after_bus_reads_are_rejected(self):
        for field,value in (('uids',(0,)*4),('cgroup','/changed'),('start_ticks',21),('parent_pid',0)):
            changed=(*self.rows[:2],replace(self.rows[2],identity=replace(self.rows[2].identity,**{field:value})),self.rows[3])
            with self.fixture(passes=[self.rows,changed]) as (_,closed),self.assertRaises(c.CensusError):self.reader.collect()
            self.assertEqual(closed.call_count,4)

    def test_population_change_during_bus_collection_refused(self):
        with self.fixture(topologies=[self.topology,self.topology,((1,(1,)),(2,(2,3,5)),(4,(4,)))]) as (_,closed),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(closed.call_count,4)

    def test_bus_and_proc_provenance_must_match(self):
        context=(self.context[0],{**self.context[1],'host_id':'f'*32})
        with self.fixture(context=context),self.assertRaises(c.CensusError):self.reader.collect()

    def test_systemd_list_change_refused(self):
        seen=0
        def reply(argv,budget,**kw):
            nonlocal seen
            raw=self.bus.reply(argv,budget,**kw)
            if argv[-1]=='ListUnits':
                seen+=1
                if seen==2:return invocation.base.encoded(t.SIGNATURES['ListUnits'],[])
            return raw
        with self.fixture(reply=reply),self.assertRaises(c.CensusError):self.reader.collect()

    def test_unsupported_manager_refused_before_any_process_open(self):
        def reply(argv,budget,**kw):
            raw=self.bus.reply(argv,budget,**kw)
            return invocation.base.encoded('v',{'type':'s','data':'252'}) if argv[-1]=='Version' else raw
        with self.fixture(reply=reply) as (opened,closed),self.assertRaises(c.CensusError):self.reader.collect()
        opened.assert_not_called();closed.assert_not_called()

    def test_bound_counts_leaders_not_unique_units_and_refuses_over_limit(self):
        rows=tuple(census.task(pid) for pid in range(2,131));candidates=tuple((row.tgid,('UID_MATCH_IN_TASK',)) for row in rows)
        with self.assertRaises(t.SystemdTransportError):z._eligible(rows,candidates)
        self.assertEqual(len(z._eligible(rows[:-1],candidates[:-1])[0]),128)

    def test_call_failure_has_no_retry_and_closes_all_task_fds(self):
        def reply(argv,budget,**kw):
            if 'GetUnitByPIDFD' in argv:raise OSError('private-task-unit')
            return self.bus.reply(argv,budget,**kw)
        with self.fixture(reply=reply) as (_,closed):
            with self.assertRaises(c.CensusError) as error:self.reader.collect()
        self.assertNotIn('private',str(error.exception));self.assertEqual(closed.call_count,4)
        self.assertEqual(len(self.bus.commands),12)

    def test_single_deadline_and_budget_no_reset_after_first_round(self):
        seen=[];original=self.reader._between
        def between(rows,owned,budget,state):
            seen.append((budget.transport.started,budget.transport.deadline,budget.transport.calls,budget.transport.bytes))
            result=original(rows,owned,budget,state)
            self.assertEqual((budget.transport.started,budget.transport.deadline),seen[0][:2])
            self.assertGreater(budget.transport.bytes,seen[0][3]);return result
        with self.fixture(),patch.object(self.reader,'_between',side_effect=between):self.reader.collect()
        self.assertEqual(seen[0][2],12)

    def test_only_invocation_properties_and_single_owned_fd_transferred(self):
        with self.fixture():self.reader.collect()
        for argv,fds in self.bus.commands:
            if 'GetUnitByPIDFD' in argv:self.assertIn(fds,((92,),(94,)))
            else:self.assertEqual(fds,())
            if argv[-1] in ('Id','InvocationID'):self.assertIn(v._path(invocation.ID),argv)
            self.assertIn('--auto-start=no',argv)
        self.assertFalse(any(op in argv for argv,_ in self.bus.commands for op in ('GetAll','Names','LoadUnit','RefUnit','GetUnitByPID')))

    def test_report_private_independent_copy_and_no_global_authority(self):
        with self.fixture():sample=self.reader.collect()
        for secret in (invocation.ID,'worker.service','/group','pid:[7]'):
            self.assertNotIn(secret,json.dumps(sample.report())+repr(sample)+repr(self.reader))
        positives={'visible_task_enumeration_observed','system_manager_lists_observed','candidate_leader_bindings_observed','systemd_bindings_observed'}
        for key,value in sample.report().items():
            if type(value) is bool:self.assertEqual(value,key in positives,key)
        data=sample.private_manifest();data['bindings'].clear();self.assertEqual(sample.report()['bound_candidate_leaders'],2)

    def test_combined_envelope_limit_refuses_and_closes(self):
        with patch.object(t.time,'monotonic',return_value=100):
            with self.fixture():sample=self.reader.collect()
            with self.fixture() as (_,closed),patch.object(z.d,'MAX_BYTES',len(sample._canonical)-1),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(closed.call_count,4)

    def test_deadline_expiry_during_bus_observation_refuses(self):
        def reply(argv,budget,**kw):
            if 'GetUnitByPIDFD' in argv:budget.deadline=0
            return self.bus.reply(argv,budget,**kw)
        with self.fixture(reply=reply),self.assertRaises(c.CensusError):self.reader.collect()


if __name__=='__main__':unittest.main()
