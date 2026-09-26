"""Read bounded Names and activation relations on already listed unit objects.

Private observations only. No name resolver, loading, recursive collection,
execution context, writer classification, enrollment or control authority.
"""
from dataclasses import asdict, dataclass, field, replace

from installer import systemd_discovery_transport as t, systemd_relevance as r

d = t.d
RELATIONS = ('Triggers', 'TriggeredBy', 'Requires', 'Wants', 'BindsTo', 'Upholds', 'OnSuccess', 'OnFailure')
UNIT_INTERFACE = t.MANAGER + '.Unit'
SPECIAL = {'timer': t.MANAGER+'.Timer', 'path': t.MANAGER+'.Path'}
MAX_CALLS = t.MAX_CALLS + 2 * r.MAX_DETAILS * (2 + len(RELATIONS) + 1)


class SystemdRelationsError(RuntimeError):
    """Closed diagnostics without private unit names or D-Bus error text."""


def require(ok, code='UNIT_RELATIONS_REJECTED'):
    if not ok: raise SystemdRelationsError(code)


def _spec(primary):
    d._name(primary, loaded=True)
    result = [('Id', UNIT_INTERFACE, 's'), ('Names', UNIT_INTERFACE, 'as')]
    result.extend((prop, UNIT_INTERFACE, 'as') for prop in RELATIONS)
    interface = SPECIAL.get(primary.rpartition('.')[2])
    if interface: result.append(('Unit', interface, 's'))
    # Socket has no Unit property in the supported D-Bus API. Its generic
    # Triggers/TriggeredBy are observed, without guessing template instances.
    return tuple(result)


def _argv(owner, obj, primary, prop):
    t._owner(owner); d._unit_object(obj)
    spec = {name: interface for name, interface, _ in _spec(primary)}
    require(type(prop) is str and prop in spec, 'UNIT_PROPERTY_NOT_ALLOWED')
    prefix = t._argv('Version', owner)
    return prefix[:prefix.index('call')+1] + [owner, obj, t.PROPERTIES, 'Get', 'ss', spec[prop], prop]


def _value(raw, signature, *, names=False):
    value = t._reply(raw, 'v')
    require(type(value) is dict and set(value) == {'type', 'data'} and value['type'] == signature,
            'UNIT_PROPERTY_REPLY_REJECTED')
    value = value['data']
    if signature == 's': return d._name(value, loaded=names)
    require(type(value) is list and len(value) <= d.MAX_NAMES, 'UNIT_PROPERTY_LIMIT')
    result = [d._name(name, loaded=names) for name in value]
    require(len(result) == len(set(result)), 'UNIT_PROPERTY_DUPLICATE')
    return sorted(result)


def _enrich(round_, details):
    rows = tuple(replace(row, names=tuple(details[row.object_path]['properties']['Names']))
        if row.object_path in details else row for row in round_.loaded_units.rows)
    digest = d.l._sha(d._json(sorted((asdict(row) for row in rows), key=d._json)))
    return replace(round_, loaded_units=d.Enumeration('observed', digest, rows))


@dataclass(frozen=True)
class RelationsSample:
    _scan: d.DiscoveryScan = field(repr=False)
    _index: d.DiscoveryIndex = field(repr=False)
    _facts: r.RelevanceFacts = field(repr=False)
    _canonical: bytes = field(repr=False)

    def scan(self): return self._scan
    def index(self): return self._index
    def facts(self): return self._facts
    def private_manifest(self): return d.l.strict_json_loads(self._canonical)

    def report(self):
        data = self.private_manifest(); result = self._index.report()
        result.update(state='SYSTEMD_SELECTED_UNIT_RELATIONS_OBSERVED', origin='LOCAL_SYSTEM_BUS',
            manifest_sha256=d.l._sha(self._canonical), system_manager_lists_observed=True,
            selected_unit_relations_observed=True, detailed_units=len(self._facts.units),
            relation_count=sum(len(row.relations) for row in self._facts.units),
            bus_calls=data['transport']['calls'], response_bytes=data['transport']['bytes'],
            blockers=sorted(set(result['blockers']) | {'UNIT_SELECTION_PARTIAL', 'RELATIONS_NOT_EXHAUSTIVE',
                'EXECUTION_CONTEXT_UNOBSERVED', 'PROVISIONED_PROFILE_BRIDGE_NOT_DELIVERED'}))
        return result


class SystemdUnitRelations(t.SystemdDiscoveryTransport):
    def __init__(self, target, storage):
        try:
            super().__init__(target, storage)
            self._relevance = r.SystemdRelevance(target, storage)
        except Exception: raise SystemdRelationsError('UNIT_RELATIONS_TARGET_REJECTED') from None

    def __repr__(self): return '<SystemdUnitRelations private read-only selected objects>'

    def _property(self, owner, obj, primary, prop, budget):
        budget.remaining()
        raw = t._capture(_argv(owner, obj, primary, prop), budget)
        budget.remaining()
        return raw

    def _details(self, round_, selected, budget):
        rows = {row.object_path: row for row in round_.loaded_units.rows}
        require(set(selected) <= set(rows), 'UNIT_SELECTION_NOT_LISTED')
        details, names, relations = {}, 0, 0
        for obj in selected:
            row = rows[obj]; properties = {}
            for prop, _, signature in _spec(row.primary_name):
                properties[prop] = _value(self._property(round_.provenance.bus_owner, obj,
                    row.primary_name, prop, budget), signature, names=prop in ('Id', 'Names'))
                if prop == 'Names':
                    names += len(properties[prop]); require(names <= d.MAX_NAMES, 'UNIT_NAMES_LIMIT')
                elif prop not in ('Id', 'Names'):
                    relations += len(properties[prop]) if signature == 'as' else 1
                    require(relations <= r.MAX_RELATIONS, 'UNIT_RELATIONS_LIMIT')
            require(properties['Id'] == row.primary_name and row.primary_name in properties['Names'],
                    'UNIT_OBJECT_IDENTITY_CHANGED')
            details[obj] = {'primary_name': row.primary_name, 'properties': properties}
        d._json(details)  # Bound the complete detail envelope, including empty observations.
        budget.remaining()
        return details

    def collect(self, selected):
        try:
            # Caller selection is private and explicit, never interpreted as a
            # name or recursive instruction. Unselected objects stay unknown.
            d._tuple(selected, r.MAX_DETAILS); require(bool(selected), 'UNIT_SELECTION_EMPTY')
            selected = tuple(sorted(d._unit_object(obj) for obj in selected))
            require(len(set(selected)) == len(selected), 'UNIT_SELECTION_DUPLICATE')
            budget = t._Budget(); budget.call_limit = MAX_CALLS; started = int(t.time.time())
            before, context = self._round(budget)
            self._discovery._round(before)  # Validate all populations before any detail query.
            objects = {row.object_path: row for row in before.loaded_units.rows}
            require(set(selected) <= set(objects), 'UNIT_SELECTION_NOT_LISTED')
            expected = t.MAX_CALLS + 2 * sum(len(_spec(objects[obj].primary_name)) for obj in selected)
            budget.call_limit = expected
            first = self._details(before, selected, budget)
            second = self._details(before, selected, budget)
            require(first == second, 'UNIT_RELATIONS_CHANGED')
            after, after_context = self._round(budget)
            require(context == after_context, 'UNIT_RELATIONS_PROVENANCE_CHANGED')
            finished = int(t.time.time()); elapsed = int((t.time.monotonic()-budget.started)*1000)
            plain_scan = d.DiscoveryScan(self._target, started, finished, elapsed, before, after)
            self._discovery.inspect(plain_scan, now=finished)  # List drift is independently refused.
            scan = replace(plain_scan, before=_enrich(before, first), after=_enrich(after, second))
            index = self._discovery.inspect(scan, now=finished)
            known = {name: row['object_path'] for row in index.private_manifest()['observation']['loaded_units']['rows']
                for name in (row['names'] or [row['primary_name']])}
            units = []
            for obj, detail in first.items():
                facts = []
                for prop, value in detail['properties'].items():
                    if prop in ('Id', 'Names'): continue
                    digest = d.l._sha(d._json({'object_path': obj, 'property': prop, 'value': value}))
                    for name in value if type(value) is list else (value,):
                        facts.append(r.RelationFact(prop, name, known.get(name), digest))
                units.append(r.UnitFacts(obj, detail['primary_name'], None, None, tuple(facts), None))
            facts = r.RelevanceFacts(d.l._sha(index._canonical), finished, tuple(units))
            # Validate the pure consumer contract without adding any declared
            # positive seed or confusing relations with writer evidence.
            self._relevance.inspect(scan, facts, now=finished)
            require(budget.calls == expected, 'UNIT_RELATIONS_CALL_SET_INCOMPLETE')
            data = {'version': 1, 'discovery': index.private_manifest(), 'details': first, 'facts': asdict(facts),
                'transport': {'client': t.BUSCTL, 'policy': 'FIXED_SELECTED_UNIT_RELATIONS_V1',
                    'context': context, 'calls': budget.calls, 'bytes': budget.bytes}}
            result = RelationsSample(scan, index, facts, d._json(data)); budget.remaining()
            return result
        except SystemdRelationsError: raise
        except Exception: raise SystemdRelationsError('UNIT_RELATIONS_UNAVAILABLE') from None
