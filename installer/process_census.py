"""Bounded visible task census and conservative identity/descendant candidates.

Read-only, private, non-atomic. Unknown tasks stay unknown; no absence, writer,
systemd binding, persistent receipt or drain authority follows from this census.
"""
from dataclasses import dataclass, field, asdict
import errno
import os
import re
import select
import time

from installer import process_identity as p

v, d, t, require = p.v, p.v.d, p.v.t, p.require
MAX_LEADERS = 512
MAX_TASKS = 1024
MAX_ENTRIES = 32768
PIDFD_THREAD = os.O_EXCL  # Linux UAPI linux/pidfd.h, not a numeric-PID fallback.


class CensusError(RuntimeError):
    """Fixed diagnostics with no task, path or credential contents."""


class _Budget(p.ProcBudget):
    def __init__(self):
        super().__init__(t._Budget())
        self.entries = 0


def _ids(directory, budget, maximum, *, root=False):
    values = []
    # scandir streams entries; never allocate an unbounded listdir result.
    with os.scandir(directory) as entries:
        for entry in entries:
            budget.check(); budget.entries += 1
            require(budget.entries <= MAX_ENTRIES, 'CENSUS_ENTRY_LIMIT')
            if re.fullmatch(r'[1-9][0-9]{0,9}', entry.name) is None:
                require(root and not entry.name.isdecimal(), 'CENSUS_ENTRY_REJECTED')
                continue
            number = int(entry.name)
            require(number <= 2**31-1 and len(values) < maximum and not entry.is_symlink(), 'CENSUS_ENTRY_LIMIT')
            values.append(number)
    require(len(values) == len(set(values)), 'CENSUS_DUPLICATE_TASK')
    return tuple(sorted(values))


def _topology(proc, budget):
    leaders = _ids(proc, budget, MAX_LEADERS, root=True)
    require(1 in leaders and os.getpid() in leaders, 'CENSUS_VISIBILITY_REQUIRED')
    result, seen = [], set()
    for leader in leaders:
        with p._directory(str(leader), proc) as process, p._directory('task', process) as tasks:
            tids = _ids(tasks, budget, MAX_TASKS-len(seen))
        require(leader in tids and not seen.intersection(tids), 'CENSUS_TASK_GROUP_REJECTED')
        seen.update(tids); result.append((leader, tids))
    return tuple(result)


def _context(proc, budget):
    context = p._context(proc, os.getpid(), budget)
    with p._directory(str(os.getpid()), proc) as own:
        raw = p._read(own, 'mountinfo', budget, p.MAX_MOUNTS)
    require(d.l._sha(raw.encode()) == context[1], 'CENSUS_CONTEXT_CHANGED')
    for line in raw.splitlines():
        mount = line.split()[4]
        if mount.startswith('/proc/'):
            require(not mount[6:].split('/')[0].isdecimal(), 'CENSUS_MASKED_TASKS')
    source = t.o._provenance()
    identity = {key: source[key] for key in ('host_id', 'boot_id', 'namespaces')}
    require(identity['namespaces'] == {'pid': context[0][0], 'mnt': context[0][1]}, 'CENSUS_CONTEXT_CHANGED')
    return context, identity


def _fd_pid(proc, fd, tid, budget):
    with p._directory(str(os.getpid()), proc) as own, p._directory('fdinfo', own) as info:
        values = p._fields(p._read(info, str(fd), budget), ('Pid', 'NSpid'))
    nested = p._numbers(values['NSpid'], maximum=2**31-1)
    require(p._numbers(values['Pid'], 1, 2**31-1) == (tid,) and nested and nested[0] == tid
            and all(n > 0 for n in nested), 'CENSUS_PIDFD_MISMATCH')


def _live(fd):
    poll = select.poll(); poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
    events = poll.poll(0)
    require(not any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events), 'CENSUS_PIDFD_REJECTED')
    return not events


def _stat(raw, tid):
    require(raw.startswith(str(tid)+' (') and ') ' in raw, 'CENSUS_STAT_REJECTED')
    fields = raw.rsplit(') ', 1)[1].split()
    require(len(fields) >= 20 and fields[0] in ('R','S','D','T','t','I','W','P'), 'CENSUS_STAT_REJECTED')
    # Boot-time kernel tasks can legitimately have start_ticks=0.
    return p._numbers(fields[19], 1, 2**63-1)[0], p._numbers(fields[1], 1, 2**31-1)[0]


@dataclass(frozen=True)
class TaskIdentity:
    start_ticks: int = field(repr=False)
    parent_pid: int = field(repr=False)
    uids: tuple[int, ...] = field(repr=False)
    gids: tuple[int, ...] = field(repr=False)
    groups: tuple[int, ...] = field(repr=False)
    namespaces: tuple[str, ...] = field(repr=False)
    cgroup: str = field(repr=False)


@dataclass(frozen=True)
class TaskRecord:
    tgid: int = field(repr=False)
    tid: int = field(repr=False)
    identity: TaskIdentity | None = field(repr=False)
    issue: str | None = field(repr=False)


def _task(task, tgid, tid, count, context, budget):
    start, parent = _stat(p._read(task, 'stat', budget), tid)
    f = p._fields(p._read(task, 'status', budget), ('Pid','Tgid','PPid','NSpid','NStgid','Uid','Gid','Groups','Threads'))
    for key, value in (('Pid',tid),('Tgid',tgid),('PPid',parent),('Threads',count)):
        require(p._numbers(f[key],1,2**31-1) == (value,), 'CENSUS_TASK_GROUP_CHANGED')
    nested_pid, nested_tgid = p._numbers(f['NSpid']), p._numbers(f['NStgid'])
    require(nested_pid and nested_pid[0] == tid and nested_tgid and nested_tgid[0] == tgid,
            'CENSUS_TASK_GROUP_CHANGED')
    if len(nested_pid) != 1 or len(nested_tgid) != 1:
        return TaskRecord(tgid, tid, None, 'TASK_PID_NAMESPACE_UNSUPPORTED')
    uids, gids, groups = p._numbers(f['Uid'],4), p._numbers(f['Gid'],4), p._numbers(f['Groups'])
    require(len(groups) == len(set(groups)), 'CENSUS_GROUPS_REJECTED')
    try: namespaces = p._namespaces(task, budget)
    except FileNotFoundError:
        # Some live kernel tasks have no namespace proxy. FD identity and
        # liveness are still checked; they are never silently excluded.
        return TaskRecord(tgid, tid, None, 'TASK_NAMESPACES_UNAVAILABLE')
    if namespaces[0] != context[0][0] or namespaces[2] != context[0][2]:
        return TaskRecord(tgid, tid, None, 'TASK_NAMESPACE_UNSUPPORTED')
    cgroup = p._cgroup(p._read(task,'cgroup',budget))
    require(_stat(p._read(task,'stat',budget),tid) == (start,parent), 'CENSUS_TASK_CHANGED')
    return TaskRecord(tgid,tid,TaskIdentity(start,parent,uids,gids,tuple(sorted(groups)),namespaces,cgroup),None)


def _inspect(proc, task, tgid, tid, count, fd, context, budget):
    _fd_pid(proc,fd,tid,budget)
    live = _live(fd)
    if not live: result = TaskRecord(tgid,tid,None,'TASK_EXITED_NOT_REAPED')
    else:
        try: result = _task(task,tgid,tid,count,context,budget)
        except PermissionError: result = TaskRecord(tgid,tid,None,'TASK_UNREADABLE')
    _fd_pid(proc,fd,tid,budget)
    require(_live(fd) == live, 'CENSUS_TASK_CHANGED')
    return result


def _candidates(rows, uid, gid):
    leaders = {x.tgid for x in rows}
    parent, starts, reasons = {}, {}, {pid:set() for pid in leaders}
    for row in rows:
        identity = row.identity
        if identity is None:
            reasons[row.tgid].add('UNRESOLVED_TASK'); continue
        if uid in identity.uids: reasons[row.tgid].add('UID_MATCH_IN_TASK')
        if gid in identity.gids: reasons[row.tgid].add('GID_MATCH_IN_TASK')
        if gid in identity.groups: reasons[row.tgid].add('SUPPLEMENTARY_GROUP_MATCH_IN_TASK')
        if row.tid == row.tgid:
            parent[row.tgid], starts[row.tgid] = identity.parent_pid, identity.start_ticks
    for child, ancestor in parent.items():
        require(ancestor == 0 or ancestor in leaders, 'CENSUS_PARENT_NOT_LISTED')
        require(ancestor not in starts or starts[ancestor] <= starts[child], 'CENSUS_PARENT_IDENTITY_AMBIGUOUS')
        visited, current = set(), child
        while current in parent and current:
            require(current not in visited, 'CENSUS_PARENT_CYCLE')
            visited.add(current); current = parent[current]
    # Only positive identity signals seed descendant review. An unreadable
    # process is unresolved, not a fabricated positive seed.
    seeds = {pid for pid,why in reasons.items() if why-{'UNRESOLVED_TASK'}}
    for child in leaders:
        current = parent.get(child,0)
        while current:
            if current in seeds:
                reasons[child].add('DESCENDANT_AT_OBSERVATION'); break
            current = parent.get(current,0)
    return tuple((pid,tuple(sorted(why))) for pid,why in sorted(reasons.items()) if why)


@dataclass(frozen=True)
class CensusSample:
    _canonical: bytes = field(repr=False)
    def private_manifest(self): return d.l.strict_json_loads(self._canonical)
    def report(self):
        data = self.private_manifest(); tasks = data['tasks']
        return {'state':'VISIBLE_TASK_CENSUS_OBSERVED','origin':'LOCAL_PROCFS','manifest_sha256':d.l._sha(self._canonical),
            'leaders':len(data['topology']),'tasks':len(tasks),'observed_identities':sum(x['identity'] is not None for x in tasks),
            'unresolved_tasks':sum(x['identity'] is None for x in tasks),'candidate_groups':len(data['candidates']),
            'directory_entries':data['io']['entries'],'proc_bytes':data['io']['bytes'],'proc_reads':data['io']['reads'],
            'visible_task_enumeration_observed':True,'process_census_authenticated':False,
            'all_descendants_identified':False,'systemd_bindings_observed':False,'live_receipt':False,
            'automatic_exclusion_allowed':False,'drain_allowed':False,'host_scheduler_inventory_complete':False,
            'storage_inventory_complete':False,'phase5_complete':False}


class ProcessCensus:
    def __init__(self, uid, gid):
        if not all(type(x) is int and 0 < x < 2**32 for x in (uid,gid)):
            raise CensusError('CENSUS_TARGET_REJECTED')
        self._uid, self._gid = uid,gid
    def __repr__(self): return '<ProcessCensus private visible tasks, no authority>'

    def _pass(self, proc, topology, owned, context, budget):
        rows = []
        for tgid,tids in topology:
            with p._directory(str(tgid),proc) as process, p._directory('task',process) as tasks:
                for tid in tids:
                    with p._directory(str(tid),tasks) as task:
                        rows.append(_inspect(proc,task,tgid,tid,len(tids),owned[tid],context,budget))
        return tuple(rows)

    def collect(self):
        owned = {}
        try:
            budget = _Budget(); started = int(time.time())
            with p._directory('/proc') as proc:
                context, provenance = _context(proc,budget)
                topology = _topology(proc,budget)
                for _,tids in topology:
                    for tid in tids:
                        budget.check()
                        try: fd = os.pidfd_open(tid,PIDFD_THREAD)
                        except OSError as error:
                            if error.errno in (errno.EINVAL,errno.ENOSYS):
                                raise CensusError('CENSUS_PIDFD_THREAD_UNSUPPORTED') from None
                            raise
                        owned[tid] = fd
                        require(fd >= 3 and not os.get_inheritable(fd), 'CENSUS_PIDFD_REJECTED')
                first = self._pass(proc,topology,owned,context,budget)
                require(_topology(proc,budget) == topology, 'CENSUS_POPULATION_CHANGED')
                second = self._pass(proc,topology,owned,context,budget)
                require(first == second, 'CENSUS_TASK_CHANGED')
                require(_topology(proc,budget) == topology, 'CENSUS_POPULATION_CHANGED')
                require(_context(proc,budget) == (context,provenance), 'CENSUS_CONTEXT_CHANGED')
                candidates = _candidates(first,self._uid,self._gid)
                for row in first:
                    _fd_pid(proc,owned[row.tid],row.tid,budget)
                    require(_live(owned[row.tid]) == (row.issue != 'TASK_EXITED_NOT_REAPED'), 'CENSUS_TASK_CHANGED')
                finished = int(time.time())
                require(0 < started <= finished <= started+60, 'CENSUS_CLOCK_CHANGED')
                data = {'version':1,'target':{'uid':self._uid,'gid':self._gid},'provenance':provenance,
                    'context':context,'started_at':started,'finished_at':finished,
                    'topology':topology,'tasks':[asdict(row) for row in first],'candidates':candidates,
                    'io':{'entries':budget.entries,'reads':budget.reads,'bytes':budget.bytes},
                    'limitations':['NON_ATOMIC_ABA_NOT_EXCLUDED','OBSERVER_NAMESPACE_ONLY','UNKNOWN_TASKS_NOT_EXCLUDED',
                        'REPARENTED_ANCESTRY_UNKNOWN','NO_SYSTEMD_BINDING','CLOSED_FDS_NOT_A_LIVE_RECEIPT',
                        'NO_NEGATIVE_IDENTITY_EXCLUSION','PROCFS_TRUSTS_PRIVILEGED_OBSERVER','NO_WRITER_OR_DRAIN_AUTHORITY']}
                result = CensusSample(d._json(data))
                for row in first:
                    require(_live(owned[row.tid]) == (row.issue != 'TASK_EXITED_NOT_REAPED'), 'CENSUS_TASK_CHANGED')
                budget.transport.remaining()
                return result
        except CensusError: raise
        except (FileNotFoundError,ProcessLookupError): raise CensusError('CENSUS_POPULATION_CHANGED') from None
        except OSError: raise CensusError('CENSUS_UNAVAILABLE') from None
        except Exception: raise CensusError('CENSUS_REJECTED') from None
        finally:
            failed = False
            for fd in owned.values():
                try: os.close(fd)
                except OSError: failed = True
            if failed: raise CensusError('CENSUS_FD_CLOSE_FAILED') from None
