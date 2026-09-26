"""Configured text stays distinct from effective identity and path authority."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from installer import systemd_execution_context as e
from installer.model import InstallerError
import test_systemd_census_relations as parent

c,d,t,v,r=e.c,e.d,e.t,e.v,e.r
encoded=parent.bridge.invocation.base.encoded


def values():
    result={name:'' for name in e.PROPERTIES}
    result.update({name:False for name in e.BOOLS})
    result.update({name:[] for name in e.LISTS|e.BINDS})
    result.update({name:choices[0] for name,choices in e.ENUMS.items()})
    result.update(User='1001',Group='named-group',SupplementaryGroups=['1002','named-group','1002'],
        WorkingDirectory='!~/opaque',RootDirectory='/rootfs',PAMName='fixture-pam',
        ReadWritePaths=['-+/srv/data','/srv/data'],BindPaths=[['/source','/destination',True,16384]])
    return result


def raw(name,value,signature=None):
    if signature is None:signature='b' if name in e.BOOLS else 'as' if name in e.LISTS else 'a(ssbt)' if name in e.BINDS else 's'
    return encoded('v',{'type':signature,'data':value})


class ContextDecodeTests(unittest.TestCase):
    def decode(self,name,value,counts=None,signature=None):return e._decode(name,raw(name,value,signature),counts if counts is not None else [0,0,0])

    def test_closed_property_and_canonical_service_invocation(self):
        identifier=parent.bridge.invocation.ID
        for name in e.PROPERTIES:
            argv=e._argv(name,':1.4',identifier)
            self.assertEqual(argv[-6:],[v._path(identifier),t.PROPERTIES,'Get','ss',e.SERVICE_INTERFACE,name])
            self.assertIn('--auto-start=no',argv)
        for name in ('Environment','ExecStartEx','GetAll','UID','PrivateTmp',True,None):
            with self.assertRaises(t.SystemdTransportError):e._argv(name,':1.4',identifier)
        for value in ('worker.service','/named/path','0'*32):
            with self.assertRaises(t.SystemdTransportError):e._argv('User',':1.4',value)

    def test_all_twenty_signatures_match_frozen_contract(self):
        from pathlib import Path
        contract=json.loads((Path(__file__).parents[1]/'docs/PHASE5_SYSTEMD_EXECUTION_CONTEXT.json').read_text())
        self.assertEqual(tuple(row['name'] for row in contract['properties']),e.PROPERTIES)
        for row in contract['properties']:self.decode(row['name'],values()[row['name']],signature=row['signature'])

    def test_configured_names_numbers_and_empty_are_not_resolved(self):
        for value in ('','0','001','4294967295','a name','élève','name@example'):
            self.assertEqual(self.decode('User',value),value)
            self.assertEqual(self.decode('Group',value),value)

    def test_working_directory_and_prefixes_preserved_without_normalization(self):
        for value in ('','~','!~','!/missing','/a/../b','${HOME}/%i','relative'):
            self.assertEqual(self.decode('WorkingDirectory',value),value)
        self.assertEqual(self.decode('ReadWritePaths',['-+/srv/a','/srv/a','/srv/a']),('-+/srv/a','/srv/a','/srv/a'))

    def test_booleans_strict_and_enums_closed(self):
        for name in e.BOOLS:
            for good in (False,True):self.assertIs(self.decode(name,good),good)
            for bad in (0,1,'true',None,[]):
                with self.assertRaises(t.SystemdTransportError):self.decode(name,bad)
        for name,choices in e.ENUMS.items():
            for choice in choices:self.assertEqual(self.decode(name,choice),choice)
            for bad in ('',True,'future-mode'):
                with self.assertRaises(t.SystemdTransportError):self.decode(name,bad)

    def test_control_surrogate_and_nonstring_text_refused(self):
        for bad in ('a\0b','a\nb','a\tb','\x7f','\x80','\x9f','\ud800',12,False,[],None):
            with self.assertRaises(t.SystemdTransportError):
                self.decode('User',bad)

    def test_utf8_bytes_not_character_count(self):
        self.assertEqual(self.decode('User','é'*128),'é'*128)
        for name,value in (('User','é'*129),('User','x'*257),('RootImage','x'*2049)):
            with self.assertRaises(t.SystemdTransportError):self.decode(name,value)

    def test_empty_arrays_and_repeated_entries_keep_order(self):
        for name in e.LISTS|e.BINDS:self.assertEqual(self.decode(name,[]),())
        self.assertEqual(self.decode('SupplementaryGroups',['z','a','z']),('z','a','z'))
        for name in e.LISTS:
            with self.assertRaises(t.SystemdTransportError):self.decode(name,[''])

    def test_property_list_caps_and_cumulative_entries(self):
        self.assertEqual(len(self.decode('SupplementaryGroups',['g']*128)),128)
        self.assertEqual(len(self.decode('ReadOnlyPaths',['/x']*256)),256)
        for name,n in (('SupplementaryGroups',129),('InaccessiblePaths',257)):
            with self.assertRaises(t.SystemdTransportError):self.decode(name,['x']*n)
        counts=[0,4095,0];self.decode('SupplementaryGroups',['g'],counts)
        with self.assertRaisesRegex(t.SystemdTransportError,'LIST_LIMIT'):self.decode('ReadWritePaths',['/x'],counts)

    def test_bind_four_fields_strict_uint64_and_opaque_flags(self):
        for flags in (0,16384,2**64-1):
            self.assertEqual(self.decode('BindPaths',[['/a','/b',False,flags]]),(('/a','/b',False,flags),))
        for bad in ([['/a','/b',0,0]],[['/a','/b',True,True]],[['/a','/b',True,-1]],
                    [['/a','/b',True,2**64]],[['/a','/b',False]],[['','/b',False,0]],[['/a','',False,0]]):
            with self.assertRaises(t.SystemdTransportError):self.decode('BindPaths',bad)

    def test_bind_lists_keep_duplicates_and_enforce_both_caps(self):
        row=['/a','/b',True,0];self.assertEqual(len(self.decode('BindPaths',[row]*128)),128)
        with self.assertRaises(t.SystemdTransportError):self.decode('BindPaths',[row]*129)
        counts=[0,0,1023];self.decode('BindPaths',[row],counts)
        with self.assertRaisesRegex(t.SystemdTransportError,'BIND_LIMIT'):self.decode('BindReadOnlyPaths',[row],counts)

    def test_cumulative_utf8_counts_scalars_arrays_and_both_bind_paths(self):
        counts=[e.MAX_TEXT-6,0,0]
        self.decode('User','é',counts);self.decode('ReadWritePaths',['/a'],counts)
        self.decode('BindPaths',[['a','b',False,0]],counts)
        self.assertEqual(counts,[e.MAX_TEXT,1,1])
        with self.assertRaisesRegex(t.SystemdTransportError,'TEXT_LIMIT'):self.decode('PAMName','x',counts)

    def test_json_signature_shape_and_truncation_refused(self):
        for data in (raw('User','u','as'),b'{',b'{"type":"v","type":"v","data":[]}',
                     b'{"type":"v","data":[{"type":"b","data":NaN}]}',b'{}'):
            with self.assertRaises((t.SystemdTransportError,InstallerError)):e._decode('User',data,[0,0,0])


class ContextCollectTests(unittest.TestCase):
    def setUp(self):
        self.base=parent.CensusRelationTests();self.base.setUp();self.config=values();self.queries=[]
        self.reader=e.SystemdExecutionContext(self.base.base.target,parent.bridge.invocation.base.model.storage())
    def reply(self,argv,budget,*,pass_fds=()):
        if e.SERVICE_INTERFACE in argv:
            self.assertEqual(pass_fds,());self.assertIn(argv[-1],e.PROPERTIES);self.queries.append(argv)
            data=raw(argv[-1],self.config[argv[-1]]);budget.calls+=1;budget.bytes+=len(data);return data
        return self.base.reply(argv,budget,pass_fds=pass_fds)
    def fixture(self,reply=None,**kw):return self.base.fixture(reply or self.reply,**kw)

    def test_two_leaders_one_context_same_fds_and_exact_budget(self):
        with self.fixture() as (opened,closed):sample=self.reader.collect()
        self.assertEqual(opened.call_count,4);self.assertEqual(closed.call_count,4)
        self.assertEqual([argv[-1] for argv in self.queries],list(e.PROPERTIES)*2)
        self.assertEqual(sample.report()['bus_calls'],98);self.assertEqual(sample.report()['configured_service_contexts'],1)
        self.assertEqual([fds for argv,fds in self.base.base.bus.commands if 'GetUnitByPIDFD' in argv],[(92,),(94,),(92,),(94,)])

    def test_configuration_adds_no_effective_identity_path_or_seed(self):
        with self.fixture():sample=self.reader.collect()
        for unit in sample.facts().units:self.assertIsNone(unit.identities);self.assertIsNone(unit.paths);self.assertIsNone(unit.bindings)
        self.assertEqual(sample.report()['related_loaded_units'],2)
        contexts=sample.private_manifest()['configured_contexts'];self.assertEqual(dict(contexts[0]['properties'])['User'],'1001')

    def test_configuration_and_all_prior_evidence_bound_in_index(self):
        parents=[];original=e.y.SystemdCensusRelations._sample
        def observe(reader,*args):
            sample=original(reader,*args);parents.append(sample);return sample
        with self.fixture(),patch.object(e.y.SystemdCensusRelations,'_sample',observe):sample=self.reader.collect()
        data=sample.private_manifest();old=parents[0]
        self.assertEqual(data['bindings'],old.private_manifest()['bindings']);self.assertEqual(len(data['bindings']),2)
        expected=d.l._sha(d._json({'census_relations_evidence':old.scan().before.loaded_units.evidence_sha256,
                                  'configured_contexts':data['configured_contexts']}))
        self.assertEqual(sample.scan().before.loaded_units.evidence_sha256,expected)
        self.assertNotEqual(sample.index()._canonical,old.index()._canonical)
        self.assertEqual(sample.facts().units,old.facts().units)
        self.assertEqual(sample.facts().discovery_sha256,sample.index().report()['manifest_sha256'])
        with self.assertRaises(r.SystemdRelevanceError):self.reader._transport._selector.inspect(sample.scan(),old.facts(),now=sample.scan().finished_at)

    def test_nonservice_binding_has_no_service_query_and_explicit_unknown(self):
        bus=self.base.base.bus;bus.mapping[1]='worker.scope';bus.base.loaded[0][0]='worker.scope'
        self.base.values['Names']=['worker.scope']
        def reply(argv,budget,**kw):
            if argv[-1]=='Id':
                budget.calls+=1;data=raw('User','worker.scope');budget.bytes+=len(data);return data
            return self.reply(argv,budget,**kw)
        with self.fixture(reply):sample=self.reader.collect()
        self.assertEqual(self.queries,[]);self.assertEqual(sample.report()['bus_calls'],58)
        self.assertIsNone(sample.private_manifest()['configured_contexts'][0]['properties'])
        self.assertEqual(sample.report()['bound_units_without_service_context'],1)

    def test_no_candidates_no_contexts_and_no_property_queries(self):
        rows=tuple(replace(row,identity=replace(row.identity,uids=(900,)*4,gids=(900,)*4)) for row in self.base.base.rows)
        with self.fixture(rows=rows):sample=self.reader.collect()
        self.assertEqual(sample.report()['bus_calls'],24);self.assertEqual(sample.private_manifest()['configured_contexts'],[])
        self.assertEqual(self.queries,[])

    def test_scalar_configuration_drift_refuses_whole_collection(self):
        calls=0
        def reply(argv,budget,**kw):
            nonlocal calls
            if argv[-1]=='User':
                calls+=1
                if calls==2:self.config['User']='changed'
            return self.reply(argv,budget,**kw)
        with self.fixture(reply) as (_,closed),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(calls,2);self.assertEqual(closed.call_count,4)

    def test_list_reordering_is_a_change_not_set_equality(self):
        passes=0;original=self.reader._contexts
        def contexts(*args):
            nonlocal passes
            result=original(*args);passes+=1;self.config['SupplementaryGroups'].reverse();return result
        self.config['SupplementaryGroups']=['a','b']
        with self.fixture(),patch.object(self.reader,'_contexts',side_effect=contexts),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(passes,2)

    def test_missing_property_or_getter_error_never_falls_back_or_retries(self):
        count=0
        def reply(argv,budget,**kw):
            nonlocal count
            if argv[-1]=='PrivateUsersEx':count+=1;raise OSError('private-path-and-unit')
            return self.reply(argv,budget,**kw)
        with self.fixture(reply) as (_,closed):
            with self.assertRaises(c.CensusError) as error:self.reader.collect()
        self.assertEqual(count,1);self.assertEqual(closed.call_count,4);self.assertNotIn('private',str(error.exception))

    def test_procfs_change_after_configuration_still_refused(self):
        rows=self.base.base.rows;changed=(*rows[:2],replace(rows[2],identity=replace(rows[2].identity,cgroup='/changed')),rows[3])
        with self.fixture(passes=[rows,changed]) as (_,closed),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(len(self.queries),40);self.assertEqual(closed.call_count,4)

    def test_text_limits_abort_without_partial_sample(self):
        with self.fixture() as (_,closed),patch.object(e,'MAX_TEXT',2),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(closed.call_count,4);self.assertEqual([a[-1] for a in self.queries],['User'])

    def test_budget_and_deadline_shared_and_only_narrowed(self):
        seen=[];original=self.reader._contexts
        def contexts(*args):
            b=args[-1];seen.append((id(b),b.started,b.deadline,b.maximum_calls));return original(*args)
        with self.fixture(),patch.object(self.reader,'_contexts',side_effect=contexts):sample=self.reader.collect()
        self.assertEqual(seen[0],seen[1]);self.assertEqual(seen[0][3],sample.report()['bus_calls'])

    def test_public_confidentiality_and_no_new_authority(self):
        with self.fixture():sample=self.reader.collect()
        public=json.dumps(sample.report())+repr(sample)+repr(self.reader)
        for secret in ('1001','named-group','/rootfs','fixture-pam','worker.service',parent.bridge.invocation.ID):self.assertNotIn(secret,public)
        allowed={'visible_task_enumeration_observed','system_manager_lists_observed','candidate_leader_bindings_observed',
            'systemd_bindings_observed','selected_relations_observed','census_candidate_signals_observed','selected_service_configuration_observed'}
        for key,value in sample.report().items():
            if type(value) is bool:self.assertEqual(value,key in allowed,key)
        private=sample.private_manifest();private['configured_contexts'].clear()
        self.assertEqual(len(sample.private_manifest()['configured_contexts']),1)

    def test_complete_envelope_limit_and_old_sample_rejection(self):
        with patch.object(t.time,'monotonic',return_value=100):
            with self.fixture():sample=self.reader.collect()
            with self.fixture() as (_,closed),patch.object(d,'MAX_BYTES',len(sample._canonical)-1),self.assertRaises(c.CensusError):self.reader.collect()
        self.assertEqual(closed.call_count,4)
        with self.assertRaises(TypeError):self.reader.collect(sample)


if __name__=='__main__':unittest.main()
