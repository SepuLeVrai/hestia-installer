"""Private live census-to-invocation bridge; review evidence, never authority.

Task PIDFDs remain owned across D-Bus observations. Only candidate leaders are
mapped; credentials of other tasks do not prove those tasks' unit membership.
"""
from dataclasses import asdict, dataclass, field
import re
import time

from installer import process_census as c, systemd_invocation as v

d, t, require = v.d, v.t, v.require


def _eligible(rows, candidates):
    leaders = {row.tgid: row for row in rows if row.tid == row.tgid}
    selected, unresolved = [], []
    for pid, reasons in candidates:
        row = leaders[pid]
        if pid == 1 or row.identity is None:
            unresolved.append({'tgid': pid, 'reasons': reasons,
                'issue': 'INIT_LEADER_UNSUPPORTED' if pid == 1 else 'LEADER_IDENTITY_UNAVAILABLE'})
        else: selected.append(pid)
    require(len(selected) <= v.MAX_BINDINGS, 'CENSUS_INVOCATION_LIMIT')
    return tuple(selected), tuple(unresolved)


def _groups(bindings, candidates):
    reasons = dict(candidates); objects, invocations = {}, {}
    for binding in bindings:
        identity = (binding.primary_name, binding.invocation_id, binding.invocation_path)
        if binding.object_path in objects:
            require(objects[binding.object_path][0] == identity, 'CENSUS_INVOCATION_CONFLICT')
        else: objects[binding.object_path] = (identity, [])
        require(invocations.get(binding.invocation_id, binding.object_path) == binding.object_path,
                'CENSUS_INVOCATION_CONFLICT')
        invocations[binding.invocation_id] = binding.object_path
        objects[binding.object_path][1].append(binding.pid_hint)
    return tuple({'object_path': path, 'primary_name': identity[0], 'invocation_id': identity[1],
        'invocation_path': identity[2], 'candidate_leaders': sorted(pids),
        'review_reasons': sorted({reason for pid in pids for reason in reasons[pid]}),
        'decision': 'REVIEW_REQUIRED', 'enrolled': False}
        for path, (identity, pids) in sorted(objects.items()))


@dataclass(frozen=True)
class CensusInvocationSample:
    _index: d.DiscoveryIndex = field(repr=False)
    _canonical: bytes = field(repr=False)

    def index(self): return self._index
    def private_manifest(self): return d.l.strict_json_loads(self._canonical)
    def report(self):
        data = self.private_manifest()
        result = c.CensusSample(d._json(data['census'])).report()
        result.update(state='CENSUS_CANDIDATE_INVOCATIONS_OBSERVED', origin='LOCAL_PROCFS_AND_SYSTEM_BUS_PIDFD',
            manifest_sha256=d.l._sha(self._canonical), bus_calls=data['transport']['calls'],
            response_bytes=data['transport']['bytes'], bound_candidate_leaders=len(data['bindings']),
            candidate_units=len(data['units']), unbound_candidate_groups=len(data['unbound']),
            system_manager_lists_observed=True, candidate_leader_bindings_observed=True,
            systemd_bindings_observed=True, all_task_unit_memberships_observed=False,
            known_provisioned_units=0)
        return result


class SystemdCensusInvocations(c.ProcessCensus):
    def __init__(self, target, storage):
        try:
            self._transport = v.SystemdInvocationTransport(target, storage)
            super().__init__(target.web_uid, target.web_gid)
        except Exception: raise c.CensusError('CENSUS_INVOCATION_TARGET_REJECTED') from None

    def __repr__(self): return '<SystemdCensusInvocations private review observations, no authority>'

    def _begin(self, budget, started):
        before, context = self._transport._round(budget.transport)
        self._transport._discovery.inspect(d.DiscoveryScan(self._transport._target, started, started, 0,
            before, before), now=started)
        require(re.match(r'^257(?:[.\- ]|$)', before.provenance.manager_version) is not None,
                'CENSUS_INVOCATION_MANAGER_UNSUPPORTED')
        return before, context

    def _binding(self, pid, fd, units, owner, budget):
        v._alive(fd)
        path, name, identifier = v._mapping(self._transport._invocation_query('GetUnitByPIDFD', budget, owner, fd=fd))
        v._alive(fd)
        require(units.get(path) == name, 'CENSUS_INVOCATION_NOT_LISTED')
        return self._transport._confirm(pid, path, name, identifier, fd, owner, budget)

    def _bindings(self, selected, owned, units, owner, budget):
        return tuple(self._binding(pid, owned[pid], units, owner, budget) for pid in selected)

    def _between(self, rows, owned, budget, state):
        before, context = state
        candidates = c._candidates(rows, self._uid, self._gid)
        selected, unresolved = _eligible(rows, candidates)
        # Same deadline and byte counters as the initial bus round and procfs.
        # Even many leaders in one unit consume the per-leader call allowance.
        budget.transport.maximum_calls = t.MAX_CALLS + 8*len(selected)
        units = {u.object_path: u.primary_name for u in before.loaded_units.rows}
        owner = before.provenance.bus_owner
        first = self._bindings(selected, owned, units, owner, budget.transport)
        groups = _groups(first, candidates)
        second = self._bindings(selected, owned, units, owner, budget.transport)
        require(first == second, 'CENSUS_INVOCATION_CHANGED')
        after, after_context = self._transport._round(budget.transport)
        require(context == after_context, 'CENSUS_INVOCATION_CONTEXT_CHANGED')
        require(budget.transport.calls == budget.transport.maximum_calls, 'CENSUS_INVOCATION_CALLS_INCOMPLETE')
        return first, groups, unresolved, after

    def _sample(self, data, budget, state, observations):
        before, context = state
        bindings, groups, unresolved, after = observations
        provenance = before.provenance
        require(data['provenance'] == {'host_id': provenance.host_id, 'boot_id': provenance.boot_id,
            'namespaces': {'pid': provenance.pid_namespace, 'mnt': provenance.mount_namespace}},
            'CENSUS_INVOCATION_PROVENANCE_MISMATCH')
        scan = d.DiscoveryScan(self._transport._target, data['started_at'], data['finished_at'],
            int((time.monotonic()-budget.transport.started)*1000), before, after)
        index = self._transport._discovery.inspect(scan, now=data['finished_at'])
        data['limitations'].remove('NO_SYSTEMD_BINDING')
        data['limitations'].append('ONLY_CANDIDATE_LEADERS_BOUND_NOT_OTHER_TASKS')
        envelope = {'version': 1, 'census': data, 'discovery': index.private_manifest(),
            'bindings': [asdict(binding) for binding in bindings], 'units': groups, 'unbound': unresolved,
            'transport': {'client': t.BUSCTL, 'policy': 'CENSUS_OWNED_PIDFD_INVOCATION_V1',
                'context': context, 'calls': budget.transport.calls, 'bytes': budget.transport.bytes},
            'limitations': ['NON_ATOMIC_ABA_NOT_EXCLUDED', 'CLOSED_FDS_NOT_A_LIVE_RECEIPT',
                'THREAD_UNIT_MEMBERSHIP_NOT_INFERRED', 'CANDIDATE_SIGNALS_NOT_WRITER_PROOF',
                'UNBOUND_AND_NONMATCHES_NOT_EXCLUDED', 'NO_CONFIGURED_IDENTITY_OR_EXECUTION_CHAIN',
                'NO_RELATIONS_OR_PROCESSLESS_UNIT_BINDINGS', 'NO_PURE_MODEL_ENROLLMENT', 'NO_DRAIN_AUTHORITY']}
        return CensusInvocationSample(index, d._json(envelope))
