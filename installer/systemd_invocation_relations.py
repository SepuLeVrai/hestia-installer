"""Bounded Names/dependency observations through PIDFD-bound invocation paths.

No recursive lookup, named-object reads, identity inference or drain authority.
"""
from dataclasses import asdict, dataclass, field, replace

from installer import systemd_invocation as v, systemd_relevance as r

d, t, require = v.d, v.t, v.require
RELATIONS = ('Triggers', 'TriggeredBy', 'Requires', 'Wants', 'BindsTo', 'Upholds', 'OnSuccess', 'OnFailure')
PROPERTIES = ('Names', *RELATIONS)


def _argv(property, owner, identifier):
    require(type(property) is str and property in PROPERTIES, 'INVOCATION_RELATION_PROPERTY_REJECTED')
    # Only the bound invocation path; no arbitrary interface/path accepted.
    return t._argv('ListUnits', owner)[:-3] + [v._path(identifier), t.PROPERTIES, 'Get', 'ss', v.UNIT_INTERFACE, property]


def _names(raw, maximum, *, primary=None):
    values = v._variant(raw, 'as')
    require(type(values) is list and len(values) <= maximum, 'INVOCATION_RELATION_LIMIT')
    names = tuple(d._name(name, loaded=primary is not None) for name in values)
    require(len(names) == len(set(names)), 'INVOCATION_RELATION_DUPLICATE')
    if primary is not None:
        require(primary in names and all(n.rpartition('.')[2] == primary.rpartition('.')[2] for n in names),
                'INVOCATION_NAMES_INCONSISTENT')
    return tuple(sorted(names))


@dataclass(frozen=True)
class InvocationRelations:
    binding: v.InvocationBinding = field(repr=False)
    names: tuple[str, ...] = field(repr=False)
    relations: tuple[tuple[str, tuple[str, ...]], ...] = field(repr=False)


@dataclass(frozen=True)
class RelationSample:
    _scan: d.DiscoveryScan = field(repr=False)
    _index: d.DiscoveryIndex = field(repr=False)
    _facts: r.RelevanceFacts = field(repr=False)
    _selection: r.RelevanceSelection = field(repr=False)
    _canonical: bytes = field(repr=False)

    def scan(self): return self._scan
    def index(self): return self._index
    def facts(self): return self._facts
    def selection(self): return self._selection
    def private_manifest(self): return d.l.strict_json_loads(self._canonical)
    def report(self):
        data = self.private_manifest(); report = self._selection.report()
        report.update(state='SYSTEM_MANAGER_INVOCATION_RELATIONS_OBSERVED', origin='LOCAL_SYSTEM_BUS_PIDFD',
            manifest_sha256=d.l._sha(self._canonical), bus_calls=data['transport']['calls'],
            response_bytes=data['transport']['bytes'], invocation_bindings=len(data['properties']),
            system_manager_lists_observed=True, selected_invocations_observed=True, selected_relations_observed=True,
            process_census_authenticated=False, effective_identities_observed=False)
        return report


class SystemdInvocationRelations(v.SystemdInvocationTransport):
    def __init__(self, target, storage):
        super().__init__(target, storage)
        try: self._selector = r.SystemdRelevance(target, storage)
        except Exception: raise t.SystemdTransportError('INVOCATION_RELATIONS_TARGET_REJECTED') from None

    def __repr__(self): return '<SystemdInvocationRelations private partial observations>'
    def _budget(self, count): return t._Budget(invocation_pairs=count, invocation_relations=True)

    def _relation_query(self, property, binding, owner, budget):
        budget.remaining()
        raw = t._capture(_argv(property, owner, binding.invocation_id), budget)
        budget.remaining()
        return raw

    def _pass(self, hints, units, owned, owner, budget):
        results, name_count, relation_count = [], 0, 0
        for hint, fd in zip(hints, owned):
            binding = self._binding(hint, units[hint.object_path], fd, owner, budget)
            names = _names(self._relation_query('Names', binding, owner, budget),
                           d.MAX_NAMES - name_count, primary=binding.primary_name)
            name_count += len(names)
            relations = []
            for property in RELATIONS:
                values = _names(self._relation_query(property, binding, owner, budget), r.MAX_RELATIONS - relation_count)
                relation_count += len(values); relations.append((property, values))
            v._alive(fd)
            results.append(InvocationRelations(binding, names, tuple(relations)))
        return tuple(results)

    def _sample(self, scan, index, details, context, budget):
        by_object = {x.binding.object_path: x for x in details}
        evidence = [asdict(x) for x in details]
        def enrich(round):
            rows = tuple(replace(u, names=by_object[u.object_path].names) if u.object_path in by_object else u
                         for u in round.loaded_units.rows)
            digest = d.l._sha(d._json({'list_evidence': round.loaded_units.evidence_sha256, 'invocations': evidence}))
            return replace(round, loaded_units=replace(round.loaded_units, rows=rows, evidence_sha256=digest))
        # Original complete lists were already compared before enrichment. Both
        # property passes were compared as canonical sets, including empty ones.
        scan = replace(scan, before=enrich(scan.before), after=enrich(scan.after))
        index = self._discovery.inspect(scan, now=scan.finished_at)
        # Discovery rejects cross-object alias conflicts before any resolution.
        objects = index.private_manifest()['observation']['loaded_units']['rows']
        names = {name: u['object_path'] for u in objects for name in (u['names'] or [u['primary_name']])}
        units = []
        for detail in details:
            relations = []
            for property, targets in detail.relations:
                digest = d.l._sha(d._json({'binding': asdict(detail.binding), 'property': property, 'targets': targets}))
                relations.extend(r.RelationFact(property, name, names.get(name), digest) for name in targets)
            units.append(r.UnitFacts(detail.binding.object_path, detail.binding.primary_name, None, None, tuple(relations), None))
        facts = r.RelevanceFacts(d.l._sha(index._canonical), scan.finished_at, tuple(units))
        selection = self._selector.inspect(scan, facts, now=scan.finished_at)
        data = {'version': 1, 'selection': selection.private_manifest(), 'properties': evidence,
            'transport': {'client': t.BUSCTL, 'policy': 'PIDFD_INVOCATION_RELATIONS_V1', 'context': context,
                          'calls': budget.calls, 'bytes': budget.bytes},
            'limitations': ['PID_HINTS_NOT_AUTHENTICATED', 'UNSELECTED_UNITS_UNKNOWN', 'EFFECTIVE_IDENTITIES_UNKNOWN',
                            'RELATIONS_NOT_EXHAUSTIVE', 'NO_POSITIVE_IDENTITY_OR_PATH_SIGNAL', 'NOT_ATOMIC']}
        return RelationSample(scan, index, facts, selection, d._json(data))
