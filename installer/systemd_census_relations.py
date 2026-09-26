"""Live census candidate signals and invocation-only relations, for review.

No caller-supplied hints or old receipt, identity inference, recursion or drain.
"""
from dataclasses import asdict, dataclass, field, replace

from installer import systemd_census_invocations as z, systemd_invocation_relations as x

c, d, t, v, r, require = z.c, x.d, x.t, x.v, x.r, x.require


@dataclass(frozen=True)
class CensusRelationSample(z.CensusInvocationSample):
    _scan: d.DiscoveryScan = field(repr=False)
    _facts: r.RelevanceFacts = field(repr=False)
    _selection: r.RelevanceSelection = field(repr=False)

    def scan(self): return self._scan
    def facts(self): return self._facts
    def selection(self): return self._selection
    def report(self):
        report = {**self._selection.report(), **super().report()}
        report.update(state='CENSUS_INVOCATION_RELATIONS_REVIEWED', selected_relations_observed=True,
            census_candidate_signals_observed=True, effective_identities_observed=False)
        return report


class SystemdCensusRelations(z.SystemdCensusInvocations):
    def __init__(self, target, storage):
        super().__init__(target, storage)
        self._transport = x.SystemdInvocationRelations(target, storage)

    def __repr__(self): return '<SystemdCensusRelations private partial review, no authority>'

    def _properties(self, bindings, owned, owner, budget):
        # Group conflicts are checked before choosing one stable representative.
        representatives = {}
        for binding in bindings: representatives.setdefault(binding.object_path, binding)
        counts = [0, 0]
        return tuple(self._transport._detail(binding, owned[binding.pid_hint], owner, budget, counts)
            for _, binding in sorted(representatives.items()))

    def _between(self, rows, owned, budget, state):
        before, context = state
        candidates = c._candidates(rows, self._uid, self._gid)
        selected, unresolved = z._eligible(rows, candidates)
        # N is known now, M <= N becomes known after the first mappings. The
        # allowance only narrows; the shared clock and byte counters never reset.
        budget.transport.maximum_calls = t.MAX_CALLS + 26*len(selected)
        units = {u.object_path: u.primary_name for u in before.loaded_units.rows}
        owner = before.provenance.bus_owner
        first = self._bindings(selected, owned, units, owner, budget.transport)
        groups = z._groups(first, candidates)
        budget.transport.maximum_calls = t.MAX_CALLS + 8*len(selected) + 18*len(groups)
        details = self._properties(first, owned, owner, budget.transport)
        second = self._bindings(selected, owned, units, owner, budget.transport)
        require(first == second, 'CENSUS_INVOCATION_CHANGED')
        require(details == self._properties(second, owned, owner, budget.transport), 'CENSUS_RELATIONS_CHANGED')
        after, after_context = self._transport._round(budget.transport)
        require(context == after_context, 'CENSUS_INVOCATION_CONTEXT_CHANGED')
        require(budget.transport.calls == budget.transport.maximum_calls, 'CENSUS_INVOCATION_CALLS_INCOMPLETE')
        return first, groups, unresolved, after, details

    def _sample(self, data, budget, state, observations):
        # Parent verifies procfs/bus provenance and both complete raw list rounds.
        parent = super()._sample(data, budget, state, observations[:4])
        envelope = parent.private_manifest(); interval = envelope['discovery']['interval']
        scan = d.DiscoveryScan(self._transport._target, interval['started_at'], interval['finished_at'],
            interval['elapsed_ms'], state[0], observations[3])
        relation_sample = self._transport._sample(scan, parent.index(), observations[4], state[1], budget.transport)
        census_hash = d.l._sha(d._json(envelope['census']))
        bindings = envelope['bindings']
        def bind(round):
            digest = d.l._sha(d._json({'relations_evidence': round.loaded_units.evidence_sha256,
                'census_sha256': census_hash, 'all_leader_bindings': bindings}))
            return replace(round, loaded_units=replace(round.loaded_units, evidence_sha256=digest))
        prior = relation_sample.scan()
        scan = replace(prior, before=bind(prior.before), after=bind(prior.after))
        index = self._transport._discovery.inspect(scan, now=scan.finished_at)
        rows = {row['tid']: row for row in envelope['census']['tasks'] if row['tid'] == row['tgid']}
        reasons = dict(envelope['census']['candidates'])
        facts_by_unit = {}
        for binding in bindings:
            pid = binding['pid_hint']; row = rows[pid]; identity = row['identity']
            process = r.ProcessBinding(pid, identity['start_ticks'], identity['namespaces'][0], identity['cgroup'],
                d.l._sha(d._json(row)))
            fact = r.CensusCandidateFact(process, tuple(reasons[pid]), census_hash,
                d.l._sha(d._json({'binding': binding, 'leader': row})))
            facts_by_unit.setdefault(binding['object_path'], []).append(fact)
        facts = replace(relation_sample.facts(), discovery_sha256=d.l._sha(index._canonical), census_sha256=census_hash,
            units=tuple(replace(unit, candidates=tuple(facts_by_unit[unit.object_path])) for unit in relation_sample.facts().units))
        selection = self._transport._selector.inspect(scan, facts, now=scan.finished_at)
        envelope['discovery'] = index.private_manifest()
        envelope['selection'] = selection.private_manifest()
        envelope['properties'] = relation_sample.private_manifest()['properties']
        envelope['transport']['policy'] = 'CENSUS_INVOCATION_RELATIONS_REVIEW_V1'
        envelope['limitations'].remove('NO_RELATIONS_OR_PROCESSLESS_UNIT_BINDINGS')
        envelope['limitations'].extend(('RELATIONS_NOT_EXHAUSTIVE', 'NO_PROCESSLESS_UNIT_PROPERTY_READS',
            'CANDIDATE_FACTS_NOT_EFFECTIVE_IDENTITIES', 'PURE_MODEL_STILL_DECLARATIVE'))
        return CensusRelationSample(index, d._json(envelope), scan, facts, selection)
