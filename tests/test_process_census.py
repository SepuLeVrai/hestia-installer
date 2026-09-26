"""Bounded enumeration, per-task identity, conservative graph and lifecycle."""
from contextlib import contextmanager
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from installer import process_census as c
import test_process_identity as base

NS=base.NS;CONTEXT=(NS,'c'*64);PROVENANCE={'host_id':'a'*32,'boot_id':'b'*36,'namespaces':{'pid':NS[0],'mnt':NS[1]}}

def task(tgid=2,tid=None,parent=1,start=20,uid=900,gid=900,groups=(),issue=None):
    return c.TaskRecord(tgid,tgid if tid is None else tid,None if issue else c.TaskIdentity(start,parent,(uid,)*4,(gid,)*4,groups,NS,'/group'),issue)
def statline(tid=2,parent=1,start=20):return str(tid)+' (private ) comm\n) S '+str(parent)+' '+' '.join(['0']*17+[str(start)]+['0']*30)+'\n'
def status(tgid=2,tid=2,count=2):return f'Pid:\t{tid}\nTgid:\t{tgid}\nPPid:\t1\nNSpid:\t{tid}\nNStgid:\t{tgid}\nUid:\t900 900 900 991\nGid:\t900 900 900 900\nGroups:\t993 992 \nThreads:\t{count}\n'
@contextmanager
def directory(*a,**kw):yield 100

class CensusParsingTests(unittest.TestCase):
    def test_streamed_entries_numeric_canonical_and_repeatable(self):
        with tempfile.TemporaryDirectory() as name:
            for item in ('12','2','self'):(Path(name)/item).mkdir()
            with c.p._directory(name) as fd:
                b=c._Budget();self.assertEqual(c._ids(fd,b,5,root=True),(2,12));self.assertEqual(c._ids(fd,b,5,root=True),(2,12))
            self.assertEqual(b.entries,6)

    def test_numeric_alias_unicode_overflow_links_and_limits_refused(self):
        for entry in ('01','１２',str(2**31)):
            with tempfile.TemporaryDirectory() as name:
                (Path(name)/entry).mkdir()
                with c.p._directory(name) as fd,self.assertRaises(c.t.SystemdTransportError):c._ids(fd,c._Budget(),5,root=True)
        with tempfile.TemporaryDirectory() as name:
            (Path(name)/'2').symlink_to('/proc')
            with c.p._directory(name) as fd,self.assertRaises(c.t.SystemdTransportError):c._ids(fd,c._Budget(),5)
        with tempfile.TemporaryDirectory() as name:
            (Path(name)/'2').mkdir()
            with c.p._directory(name) as fd,self.assertRaises(c.t.SystemdTransportError):c._ids(fd,c._Budget(),0)
            b=c._Budget();b.entries=c.MAX_ENTRIES
            with c.p._directory(name) as fd,self.assertRaises(c.t.SystemdTransportError):c._ids(fd,b,5)

    def test_task_directory_does_not_accept_nonnumeric_entries(self):
        with tempfile.TemporaryDirectory() as name:
            (Path(name)/'self').mkdir()
            with c.p._directory(name) as fd,self.assertRaises(c.t.SystemdTransportError):c._ids(fd,c._Budget(),5)

    def test_topology_includes_init_observer_and_unique_complete_groups(self):
        with patch.object(c.os,'getpid',return_value=2),patch.object(c.p,'_directory',directory):
            with patch.object(c,'_ids',side_effect=[(1,2),(1,),(2,3)]):self.assertEqual(c._topology(100,c._Budget()),((1,(1,)),(2,(2,3))))
            for values in ([(2,)],[(1,2),(4,)],[(1,2),(1,3),(2,3)]):
                with patch.object(c,'_ids',side_effect=values),self.assertRaises(c.t.SystemdTransportError):c._topology(100,c._Budget())

    def test_global_mount_masks_reject_any_numeric_process_subtree(self):
        mount=base.MOUNTS+'2 1 0:2 / /proc/98765/status rw - tmpfs tmpfs rw\n'
        ctx=(NS,c.d.l._sha(mount.encode()))
        with patch.object(c.p,'_context',return_value=ctx),patch.object(c.p,'_directory',directory),patch.object(c.p,'_read',return_value=mount),self.assertRaisesRegex(c.t.SystemdTransportError,'MASKED_TASKS'):c._context(100,c._Budget())

    def test_context_binds_mount_bytes_and_provenance(self):
        ctx=(NS,c.d.l._sha(base.MOUNTS.encode()))
        with patch.object(c.p,'_context',return_value=ctx),patch.object(c.p,'_directory',directory),patch.object(c.p,'_read',return_value=base.MOUNTS),patch.object(c.t.o,'_provenance',return_value=PROVENANCE):self.assertEqual(c._context(100,c._Budget()),(ctx,PROVENANCE))
        with patch.object(c.p,'_context',return_value=CONTEXT),patch.object(c.p,'_directory',directory),patch.object(c.p,'_read',return_value=base.MOUNTS),self.assertRaisesRegex(c.t.SystemdTransportError,'CONTEXT_CHANGED'):c._context(100,c._Budget())

    def test_task_fdinfo_accepts_nested_visible_id_but_not_another_task(self):
        with patch.object(c.p,'_directory',directory):
            for raw in ('Pid:\t3\nNSpid:\t3\n','Pid:\t3\nNSpid:\t3 1\n'):
                with patch.object(c.p,'_read',return_value=raw):c._fd_pid(100,91,3,c._Budget())
            for raw in ('Pid:\t4\nNSpid:\t4\n','Pid:\t-1\nNSpid:\t-1\n','Pid:\t3\nNSpid:\t4\n'):
                with patch.object(c.p,'_read',return_value=raw),self.assertRaises(c.t.SystemdTransportError):c._fd_pid(100,91,3,c._Budget())

    def test_thread_credentials_come_from_own_status_not_leader(self):
        with patch.object(c.p,'_read',side_effect=[statline(3),status(tid=3),'0::/group\n',statline(3)]),patch.object(c.p,'_namespaces',return_value=NS):
            row=c._task(100,2,3,2,CONTEXT,c._Budget())
        self.assertEqual((row.tgid,row.tid),(2,3));self.assertEqual(row.identity.uids,(900,900,900,991));self.assertEqual(row.identity.groups,(992,993))

    def test_parent_thread_count_membership_and_start_changes_refused(self):
        cases=[(status(tgid=4),statline(3)),(status(tid=3,count=3),statline(3)),(status(tid=3).replace('PPid:\t1','PPid:\t2'),statline(3)),(status(tid=3),statline(3,start=21))]
        for raw,after in cases:
            with patch.object(c.p,'_read',side_effect=[statline(3),raw,'0::/group\n',after]),patch.object(c.p,'_namespaces',return_value=NS),self.assertRaises(c.t.SystemdTransportError):c._task(100,2,3,2,CONTEXT,c._Budget())

    def test_boot_tick_zero_is_valid_but_dead_stat_not_identity(self):
        self.assertEqual(c._stat(statline(start=0),2),(0,1))
        with self.assertRaises(c.t.SystemdTransportError):c._stat(statline().replace(') S ',') Z '),2)

    def test_nested_namespace_and_missing_kernel_namespaces_remain_unknown(self):
        with patch.object(c.p,'_read',side_effect=[statline(3),status(tid=3).replace('NSpid:\t3','NSpid:\t3 2')]):
            self.assertEqual(c._task(100,2,3,2,CONTEXT,c._Budget()).issue,'TASK_PID_NAMESPACE_UNSUPPORTED')
        with patch.object(c.p,'_read',side_effect=[statline(3),status(tid=3)]),patch.object(c.p,'_namespaces',side_effect=FileNotFoundError):
            self.assertEqual(c._task(100,2,3,2,CONTEXT,c._Budget()).issue,'TASK_NAMESPACES_UNAVAILABLE')

    def test_unreadable_and_unreaped_are_retained_not_excluded(self):
        with patch.object(c,'_fd_pid'),patch.object(c,'_live',return_value=True),patch.object(c,'_task',side_effect=PermissionError('private')):
            self.assertEqual(c._inspect(100,100,2,3,2,91,CONTEXT,c._Budget()).issue,'TASK_UNREADABLE')
        with patch.object(c,'_fd_pid'),patch.object(c,'_live',return_value=False),patch.object(c,'_task') as read:
            self.assertEqual(c._inspect(100,100,2,3,2,91,CONTEXT,c._Budget()).issue,'TASK_EXITED_NOT_REAPED');read.assert_not_called()

    def test_task_exit_during_read_refused_even_with_leader_still_live(self):
        with patch.object(c,'_fd_pid'),patch.object(c,'_live',side_effect=[True,False]),patch.object(c,'_task',return_value=task(tid=3)),self.assertRaisesRegex(c.t.SystemdTransportError,'TASK_CHANGED'):c._inspect(100,100,2,3,2,91,CONTEXT,c._Budget())

class CensusGraphTests(unittest.TestCase):
    def test_only_matching_thread_seeds_group_and_other_identity_descendants(self):
        rows=(task(1,parent=0,start=1),task(),task(tid=3,uid=991),task(4,parent=2,start=30),task(5,parent=4,start=40),task(6,parent=1))
        candidates=dict(c._candidates(rows,991,992))
        self.assertEqual(set(candidates),{2,4,5});self.assertIn('UID_MATCH_IN_TASK',candidates[2]);self.assertEqual(candidates[5],('DESCENDANT_AT_OBSERVATION',))

    def test_all_four_uid_gid_and_supplementary_groups_are_positive_signals(self):
        row=task()
        for field in ('uids','gids'):
            for i in range(4):
                values=[900]*4;values[i]=991 if field=='uids' else 992
                changed=replace(row,identity=replace(row.identity,**{field:tuple(values)}))
                self.assertIn(2,dict(c._candidates((task(1,parent=0,start=1),changed),991,992)))
        self.assertIn(2,dict(c._candidates((task(1,parent=0,start=1),task(groups=(992,))),991,992)))

    def test_unknown_never_becomes_positive_ancestor_and_nonmatches_retained(self):
        rows=(task(1,parent=0,start=1),task(issue='TASK_UNREADABLE'),task(4,parent=2,start=30))
        self.assertEqual(c._candidates(rows,991,992),((2,('UNRESOLVED_TASK',)),))
        self.assertEqual(len(rows),3)

    def test_reparented_child_is_not_assigned_historical_ancestor(self):
        rows=(task(1,parent=0,start=1),task(uid=991),task(4,parent=1,start=30))
        self.assertEqual(set(dict(c._candidates(rows,991,992))),{2})

    def test_missing_parent_cycle_and_younger_parent_refused(self):
        for rows in ((task(parent=999),),(task(1,parent=2,start=20),task(parent=1)),(task(1,parent=0,start=30),task(start=20))):
            with self.assertRaises(c.t.SystemdTransportError):c._candidates(rows,991,992)

class CensusControllerTests(unittest.TestCase):
    def setUp(self):
        self.topology=((1,(1,)),(2,(2,3)))
        self.rows=(task(1,parent=0,start=1),task(),task(tid=3,uid=991))
        self.reader=c.ProcessCensus(991,992)
    @contextmanager
    def fixture(self,topologies=None,passes=None,opened=None):
        with patch.object(c.p,'_directory',directory),patch.object(c,'_context',return_value=(CONTEXT,PROVENANCE)),patch.object(c,'_topology',side_effect=topologies,return_value=self.topology),patch.object(c.os,'pidfd_open',side_effect=opened or [91,92,93]) as op,patch.object(c.os,'get_inheritable',return_value=False),patch.object(c.os,'close') as close,patch.object(c.ProcessCensus,'_pass',side_effect=passes,return_value=self.rows),patch.object(c,'_fd_pid'),patch.object(c,'_live',return_value=True):yield op,close

    def test_two_passes_three_enumerations_and_all_task_pidfds_closed(self):
        with self.fixture() as (op,close):sample=self.reader.collect()
        self.assertEqual([x.args for x in op.call_args_list],[(1,c.PIDFD_THREAD),(2,c.PIDFD_THREAD),(3,c.PIDFD_THREAD)])
        self.assertEqual([x.args for x in close.call_args_list],[(91,),(92,),(93,)])
        self.assertEqual(sample.report()['tasks'],3);self.assertEqual(sample.report()['candidate_groups'],1)

    def test_population_change_between_or_after_passes_is_fatal(self):
        for index in (1,2):
            values=[self.topology]*3;values[index]=((1,(1,)),(2,(2,3,4)))
            with self.fixture(topologies=values) as (_,close),self.assertRaises(c.CensusError):self.reader.collect()
            self.assertEqual(close.call_count,3)

    def test_credential_parent_or_namespace_change_between_passes_refused(self):
        for changes in ({'uids':(900,)*4},{'parent_pid':0},{'namespaces':NS[:1]+('mnt:[99]',)+NS[2:]}):
            rows=(*self.rows[:2],replace(self.rows[2],identity=replace(self.rows[2].identity,**changes)))
            with self.fixture(passes=[self.rows,rows]),self.assertRaises(c.CensusError):self.reader.collect()

    def test_partially_opened_fd_set_is_closed_on_resource_failure(self):
        with self.fixture(opened=[91,OSError(errno.EMFILE,'private')]) as (_,close),self.assertRaisesRegex(c.CensusError,'^CENSUS_UNAVAILABLE$'):self.reader.collect()
        close.assert_called_once_with(91)

    def test_thread_flag_unsupported_has_no_process_fd_fallback(self):
        with self.fixture(opened=[OSError(errno.EINVAL,'private')]) as (op,close),self.assertRaisesRegex(c.CensusError,'THREAD_UNSUPPORTED'):self.reader.collect()
        self.assertEqual(op.call_count,1);close.assert_not_called()

    def test_every_close_attempted_even_when_one_close_fails(self):
        with self.fixture() as (_,close):
            close.side_effect=[OSError(),None,None]
            with self.assertRaisesRegex(c.CensusError,'FD_CLOSE_FAILED'):self.reader.collect()
            self.assertEqual(close.call_count,3)

    def test_input_closed_before_host_io(self):
        for uid,gid in ((True,1),(0,1),(-1,1),(1,2**32),(1,'2')):
            with self.assertRaises(c.CensusError):c.ProcessCensus(uid,gid)

    def test_unknown_rows_survive_serialization_and_all_authority_flags_stay_false(self):
        rows=(self.rows[0],self.rows[1],task(tid=3,issue='TASK_UNREADABLE'))
        with self.fixture(passes=[rows,rows]):sample=self.reader.collect()
        self.assertEqual(sample.report()['unresolved_tasks'],1)
        self.assertEqual(sample.private_manifest()['tasks'][2]['issue'],'TASK_UNREADABLE')
        for key,value in sample.report().items():
            if type(value) is bool and key!='visible_task_enumeration_observed':self.assertFalse(value,key)

    def test_private_copy_repr_and_manifest_bound(self):
        with self.fixture():sample=self.reader.collect()
        for raw in (repr(sample),repr(self.reader),json.dumps(sample.report())):
            for secret in ('/group','pid:[7]'):self.assertNotIn(secret,raw)
        self.assertTrue({'target','uid','gid','groups','tid','tgid','parent_pid'}.isdisjoint(sample.report()))
        self.assertTrue(all(type(value) in (int,bool) for key,value in sample.report().items()
                            if key not in ('state','origin','manifest_sha256')))
        data=sample.private_manifest();data['tasks'].clear();self.assertEqual(sample.report()['tasks'],3)
        with self.fixture(),patch.object(c.d,'MAX_BYTES',len(sample._canonical)-1),self.assertRaises(c.CensusError):self.reader.collect()

if __name__=='__main__':unittest.main()
