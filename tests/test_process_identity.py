"""Closed procfs parsing/IO and selected-leader transport controllers."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from installer import process_identity as p, systemd_process_identity as z
import test_systemd_invocation_relations as source

PID = 4321
NS = ('pid:[7]', 'mnt:[8]', 'user:[9]', 'cgroup:[10]', 'time:[11]')
MOUNTS = '1 0 0:1 / /proc rw - proc proc rw\n'
STATUS = 'Pid:\t4321\nTgid:\t4321\nUid:\t991 991 991 991\nGid:\t991 991 991 991\nGroups:\t999 991 \nThreads:\t1\nNSpid:\t4321\nNStgid:\t4321\n'
def identity(**kw): return replace(p.ProcessIdentity(PID, 555, (991,)*4, (991,)*4, (991,999), 1, NS, '/system.slice/worker.service', 'd'*64), **kw)
def statline(state='S', start='555'): return '4321 (private ) name\n) '+state+' '+' '.join(['0']*18+[start]+['0']*30)+'\n'

class ProcessParsingTests(unittest.TestCase):
    def test_four_credentials_canonical_groups_without_names(self):
        result = p._credentials(STATUS, PID)
        self.assertEqual(result, ((991,)*4, (991,)*4, (991,999), 1))
        self.assertEqual(p._credentials(STATUS.replace('999 991 ', ''),PID)[2], ())

    def test_fields_missing_or_duplicated_refused(self):
        for key in ('Uid','Gid','Pid','Tgid','NSpid','NStgid','Groups','Threads'):
            line = next(x for x in STATUS.splitlines(True) if x.startswith(key+':'))
            for raw in (STATUS+line,STATUS.replace(line,'')):
                with self.subTest(key=key), self.assertRaises(z.t.SystemdTransportError): p._credentials(raw,PID)

    def test_only_leader_in_same_pid_namespace(self):
        for key in ('Pid','Tgid','NSpid','NStgid'):
            for value in ('4322','4321 4'):
                with self.subTest(key=key,value=value), self.assertRaises(z.t.SystemdTransportError):
                    p._credentials(STATUS.replace(key+':\t4321\n',key+':\t'+value+'\n'),PID)

    def test_numeric_ascii_counts_unsigned_limits_and_group_duplicates(self):
        for raw in ('-1','+1','１','1.0',str(2**32),'1 '*65):
            with self.subTest(raw=raw), self.assertRaises(z.t.SystemdTransportError): p._numbers(raw)
        for field,value in [('Uid','1 2 3'),('Gid','1 2 3 4 5'),('Groups','1 1'),('Threads','0'),('Threads','32769')]:
            raw='\n'.join(field+':\t'+value if x.startswith(field+':') else x for x in STATUS.splitlines())+'\n'
            with self.subTest(field=field,value=value), self.assertRaises(z.t.SystemdTransportError): p._credentials(raw,PID)

    def test_stat_delimiters_and_state_do_not_retain_comm(self):
        self.assertEqual(p._start(statline(),PID),555)
        self.assertEqual(p._start(statline('R'),PID),555)
        self.assertEqual(p._start(statline('T'),PID),555)

    def test_stat_pid_truncation_dead_states_and_overflow_refused(self):
        for raw in (statline().replace('4321 (','4322 ('),'4321 (x) S 0\n',*(statline(s) for s in ('Z','X','x','?')),statline(start=str(2**64)),statline(start='-1')):
            with self.subTest(raw=raw), self.assertRaises(z.t.SystemdTransportError): p._start(raw,PID)

    def test_cgroup_one_v2_absolute_membership_only(self):
        self.assertEqual(p._cgroup('0::/\n'),'/')
        self.assertEqual(p._cgroup('0::/system.slice/worker.service/nested\n'),'/system.slice/worker.service/nested')
        for raw in ('0::/a','0::/a\n0::/b\n','1:cpu:/a\n','0::a\n','0::/a/../b\n','0::/a//b\n','0::/a/\n','0::/'+('x'*2049)+'\n','0::/a\tb\n'):
            with self.subTest(raw=raw), self.assertRaises(z.t.SystemdTransportError): p._cgroup(raw)

    def test_mount_profile_accepts_unrelated_proc_submounts(self):
        raw=MOUNTS+'2 1 0:2 / /proc/sys ro - proc proc ro\n'
        self.assertEqual(len(p._mounts(raw,PID,123)),64)

    def test_mount_masks_visibility_and_wrong_filesystem_refused(self):
        for raw in ('',MOUNTS*2,MOUNTS.replace('- proc ','- tmpfs '),MOUNTS.replace(' rw\n',' rw,hidepid=2\n'),MOUNTS.replace(' rw\n',' rw,subset=pid\n'),MOUNTS.replace(' / /proc',' /other /proc')):
            with self.subTest(raw=raw), self.assertRaises(z.t.SystemdTransportError): p._mounts(raw,PID,123)
        for path in ('/proc/4321/status','/proc/4321','/proc/123/ns','/proc/1','/proc/self/fdinfo','/proc/thread-self'):
            with self.subTest(path=path), self.assertRaises(z.t.SystemdTransportError): p._mounts(MOUNTS+'2 1 0:2 / '+path+' rw - tmpfs tmpfs rw\n',PID,123)

    def test_io_reads_to_eof_and_closes_actual_descriptors(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); (root/'data').write_text('x'*9000+'\n')
            budget=p.ProcBudget(z.t._Budget())
            with p._directory(name) as fd: self.assertEqual(len(p._read(fd,'data',budget)),9001)
            self.assertEqual((budget.bytes,budget.reads),(9001,1))

    def test_io_rejects_symlink_directory_fifo_truncation_nul_and_oversize(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); (root/'data').write_text('ok\n');(root/'link').symlink_to('data');(root/'dir').mkdir();os.mkfifo(root/'fifo')
            with p._directory(name) as fd:
                for entry in ('link','dir','fifo'):
                    with self.subTest(entry=entry),self.assertRaises((OSError,z.t.SystemdTransportError)): p._read(fd,entry,p.ProcBudget(z.t._Budget()))
                for raw in (b'no newline',b'\0\n',b'',b'x'*65536+b'\n',b'\xff\n'):
                    (root/'data').write_bytes(raw)
                    with self.subTest(size=len(raw)),self.assertRaises((UnicodeError,z.t.SystemdTransportError)): p._read(fd,'data',p.ProcBudget(z.t._Budget()))

    def test_cumulative_byte_read_and_shared_deadline_limits(self):
        budget=p.ProcBudget(z.t._Budget()); budget.bytes=p.MAX_BYTES+1
        with self.assertRaises(z.t.SystemdTransportError): budget.check()
        budget.bytes=0;budget.reads=p.MAX_READS
        with self.assertRaises(z.t.SystemdTransportError):budget.check()
        budget.reads=0;budget.transport.deadline=0
        with self.assertRaises(z.t.SystemdTransportError):budget.check()

    @contextmanager
    def fake_proc(self, snapshots=None, contexts=None, fdinfo='Pid:\t4321\nNSpid:\t4321\n'):
        @contextmanager
        def directory(*a,**kw):yield 100
        with patch.object(p,'_directory',directory),patch.object(p,'_context',side_effect=contexts,return_value=(NS,'c'*64)),patch.object(p,'_snapshot',side_effect=snapshots,return_value=identity()),patch.object(p,'_read',return_value=fdinfo),patch.object(p.v,'_alive') as alive:
            yield alive

    def test_owned_pidfd_matches_live_numeric_leader_and_rechecked(self):
        b=p.ProcBudget(z.t._Budget())
        with self.fake_proc() as alive:self.assertEqual(p.observe(PID,91,b),identity())
        self.assertEqual(alive.call_count,2);self.assertEqual(b.observations,1)

    def test_pidfd_different_process_or_dead_mapping_refused(self):
        for raw in ('Pid:\t4322\nNSpid:\t4322\n','Pid:\t-1\nNSpid:\t-1\n','Pid:\t4321\n','Pid:\t4321\nNSpid:\t4321 2\n'):
            with self.fake_proc(fdinfo=raw),self.assertRaises(z.t.SystemdTransportError):p.observe(PID,91,p.ProcBudget(z.t._Budget()))

    def test_identity_or_observer_context_change_within_observation_refused(self):
        for changes in ({'start_ticks':556},{'uids':(991,992,991,991)},{'gids':(991,992,991,991)},{'groups':()},{'threads':2},{'cgroup':'/other'},{'namespaces':NS[:1]+('mnt:[20]',)+NS[2:]}):
            with self.subTest(changes=changes),self.fake_proc(snapshots=[identity(),identity(**changes)]),self.assertRaises(z.t.SystemdTransportError):p.observe(PID,91,p.ProcBudget(z.t._Budget()))
        with self.fake_proc(contexts=[(NS,'a'*64),(NS,'b'*64)]),self.assertRaises(z.t.SystemdTransportError):p.observe(PID,91,p.ProcBudget(z.t._Budget()))

    def test_bad_selection_refused_before_io(self):
        with patch.object(p,'_directory') as directory:
            for pid,fd in ((True,91),(1,91),(2**31,91),(PID,False),(PID,2)):
                with self.assertRaises(z.t.SystemdTransportError):p.observe(pid,fd,p.ProcBudget(z.t._Budget()))
            directory.assert_not_called()

    def test_context_requires_root_and_all_five_observer_namespaces(self):
        @contextmanager
        def directory(*a,**kw):yield 100
        with patch.object(p,'_directory',directory),patch.object(p,'_read',return_value=MOUNTS),patch.object(p.os,'geteuid',return_value=0):
            for i in range(5):
                other=list(NS);other[i]=other[i].replace('[','[2')
                with patch.object(p,'_namespaces',side_effect=[NS,tuple(other)]),self.assertRaisesRegex(z.t.SystemdTransportError,'OBSERVER_NAMESPACE'):p._context(100,PID,p.ProcBudget(z.t._Budget()))
            with patch.object(p.os,'geteuid',return_value=1),self.assertRaisesRegex(z.t.SystemdTransportError,'OBSERVER_REJECTED'):p._context(100,PID,p.ProcBudget(z.t._Budget()))

    def test_snapshot_accepts_private_mount_but_rejects_pid_or_user_namespace(self):
        for i in (0,1,2,3,4):
            ns=list(NS);ns[i]=ns[i].replace('[','[2')
            with patch.object(p,'_namespaces',return_value=tuple(ns)),patch.object(p,'_read',side_effect=[statline(),STATUS,'0::/system.slice/worker.service\n',statline()]):
                if i in (0,2):
                    with self.assertRaisesRegex(z.t.SystemdTransportError,'TARGET_NAMESPACE'):p._snapshot(100,PID,(NS,'c'*64),p.ProcBudget(z.t._Budget()))
                else:self.assertEqual(p._snapshot(100,PID,(NS,'c'*64),p.ProcBudget(z.t._Budget())).namespaces,tuple(ns))


class ProcessTransportTests(unittest.TestCase):
    def setUp(self):
        self.base=source.InvocationRelationsTests();self.base.setUp()
        self.transport=z.SystemdProcessIdentity(source.source.base.model.target(),source.source.base.model.storage())
    @contextmanager
    def fixture(self, observations=None, reply=None):
        with self.base.fixture(reply),patch.object(p,'observe',side_effect=observations,return_value=identity()) as observe:yield observe
    def collect(self):return self.transport.collect((source.HINT,))

    def test_effective_signal_and_real_relations_widen_review_only(self):
        with self.fixture() as observe:sample=self.collect()
        self.assertEqual(observe.call_count,8);self.assertEqual(sample.report()['bus_calls'],50)
        self.assertEqual(sample.report()['related_loaded_units'],2);self.assertEqual(sample.report()['known_provisioned_units'],0)
        self.assertFalse(sample.report()['drain_allowed']);self.assertFalse(sample.report()['process_census_authenticated'])
        self.assertTrue(sample.report()['selected_effective_leaders_observed'])
        fact=sample.facts().units[0].identities[0];self.assertEqual((fact.uid,fact.gid,fact.groups),(991,991,(991,999)))
        self.assertEqual(fact.process.pid,PID);self.assertIsNone(sample.facts().units[0].paths)

    def test_real_saved_fs_ids_preserved_but_not_mislabeled_effective(self):
        observed=identity(uids=(991,900,991,991),gids=(991,900,991,991),groups=())
        with self.fixture(observations=lambda *a:observed):sample=self.collect()
        self.assertEqual(sample.report()['related_loaded_units'],0)
        self.assertEqual(sample.facts().units[0].identities[0].uid,900)
        self.assertEqual(sample.private_manifest()['properties'][0]['binding']['identity']['uids'],[991,900,991,991])

    def test_supplementary_group_is_positive_without_uid_match(self):
        observed=identity(uids=(900,)*4,gids=(900,)*4,groups=(991,))
        with self.fixture(observations=lambda *a:observed):sample=self.collect()
        self.assertEqual(sample.report()['related_loaded_units'],2)

    def test_every_identity_checkpoint_rejects_change_and_closes_pidfd(self):
        for i in range(1,8):
            self.setUp(); observations=[identity()]*8;observations[i]=identity(start_ticks=556)
            with self.subTest(i=i),self.fixture(observations),self.assertRaises(z.t.SystemdTransportError):self.collect()

    def test_unreadable_proc_stops_before_any_binding_query(self):
        with self.fixture(observations=PermissionError('private path')),self.assertRaisesRegex(z.t.SystemdTransportError,'^INVOCATION_TRANSPORT_UNAVAILABLE$'):self.collect()
        self.assertFalse(any('GetUnitByPIDFD' in argv for argv,_ in self.base.base.commands))

    def test_identity_digest_changes_with_fs_uid_even_when_effective_same(self):
        with self.fixture():a=self.collect()
        with self.fixture(observations=lambda *a:identity(uids=(991,991,991,900))):b=self.collect()
        self.assertNotEqual(a.facts().units[0].identities[0].evidence_sha256,b.facts().units[0].identities[0].evidence_sha256)
        self.assertNotEqual(a.facts().discovery_sha256,b.facts().discovery_sha256)

    def test_private_reports_copy_and_global_boundaries(self):
        with self.fixture():sample=self.collect()
        for raw in (repr(sample),repr(self.transport),repr(identity()),json.dumps(sample.report())):
            for secret in ('worker.service','4321','991','system.slice','pid:[7]'):self.assertNotIn(secret,raw)
        data=sample.private_manifest();data['properties'].clear();self.assertEqual(len(sample.private_manifest()['properties']),1)
        for key in ('phase5_complete','effective_identities_observed','host_relevance_verified','host_scheduler_inventory_complete'):self.assertFalse(sample.report()[key])
        self.assertIn('LEADER_ONLY_NOT_THREADS_OR_DESCENDANTS',sample.private_manifest()['limitations'])

    def test_shared_bus_budget_and_separate_proc_budget_have_no_extra_calls(self):
        a=self.transport._budget(128);b=self.transport._budget(1)
        self.assertEqual(a.maximum_calls,3352);self.assertIsNot(a.proc,b.proc);self.assertIs(a.proc.transport,a)

if __name__=='__main__':unittest.main()
