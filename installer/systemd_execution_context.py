"""Private configured Service context through census-owned invocation bindings.

No NSS, command collection, path resolution, identity projection or authority.
"""
from dataclasses import asdict, dataclass, field, replace

from installer import systemd_census_relations as y

c, d, t, v, r, require = y.c, y.d, y.t, y.v, y.r, y.require
SERVICE_INTERFACE = 'org.freedesktop.systemd1.Service'
PROPERTIES = ('User', 'Group', 'SupplementaryGroups', 'DynamicUser', 'PAMName',
    'WorkingDirectory', 'RootDirectory', 'RootImage', 'RootDirectoryStartOnly',
    'PrivateUsersEx', 'PrivateTmpEx', 'PrivateMounts', 'PrivateDevices', 'ProtectHome',
    'ProtectSystem', 'ReadWritePaths', 'ReadOnlyPaths', 'InaccessiblePaths', 'BindPaths', 'BindReadOnlyPaths')
BOOLS = frozenset(('DynamicUser', 'RootDirectoryStartOnly', 'PrivateMounts', 'PrivateDevices'))
LISTS = frozenset(('SupplementaryGroups', 'ReadWritePaths', 'ReadOnlyPaths', 'InaccessiblePaths'))
BINDS = frozenset(('BindPaths', 'BindReadOnlyPaths'))
ENUMS = {'PrivateUsersEx': ('no', 'self', 'identity'), 'PrivateTmpEx': ('no', 'connected', 'disconnected'),
    'ProtectHome': ('no', 'yes', 'read-only', 'tmpfs'), 'ProtectSystem': ('no', 'yes', 'full', 'strict')}
MAX_TEXT = 256 * 1024
MAX_ENTRIES = 4096
MAX_BINDS = 1024


def _argv(property, owner, identifier):
    require(type(property) is str and property in PROPERTIES, 'EXECUTION_PROPERTY_NOT_ALLOWED')
    return t._argv('ListUnits', owner)[:-3] + [v._path(identifier), t.PROPERTIES, 'Get', 'ss', SERVICE_INTERFACE, property]


def _text(value, maximum, counts, *, empty=True):
    require(type(value) is str and len(value) <= maximum and (empty or value), 'EXECUTION_TEXT_REJECTED')
    require(all(ord(ch) >= 32 and not 127 <= ord(ch) <= 159 and not 0xd800 <= ord(ch) <= 0xdfff for ch in value),
            'EXECUTION_TEXT_REJECTED')
    size = len(value.encode('utf-8'))
    require(size <= maximum, 'EXECUTION_TEXT_LIMIT')
    counts[0] += size
    require(counts[0] <= MAX_TEXT, 'EXECUTION_TEXT_LIMIT')
    return value


def _decode(property, raw, counts):
    require(type(property) is str and property in PROPERTIES, 'EXECUTION_PROPERTY_NOT_ALLOWED')
    signature = 'b' if property in BOOLS else 'as' if property in LISTS else 'a(ssbt)' if property in BINDS else 's'
    value = v._variant(raw, signature)
    if property in BOOLS:
        require(type(value) is bool, 'EXECUTION_BOOLEAN_REJECTED')
        return value
    if property in LISTS:
        group = property == 'SupplementaryGroups'
        require(type(value) is list and len(value) <= (128 if group else 256), 'EXECUTION_LIST_LIMIT')
        counts[1] += len(value)
        require(counts[1] <= MAX_ENTRIES, 'EXECUTION_LIST_LIMIT')
        return tuple(_text(item, 256 if group else 2048, counts, empty=False) for item in value)
    if property in BINDS:
        require(type(value) is list and len(value) <= 128, 'EXECUTION_BIND_LIMIT')
        counts[2] += len(value)
        require(counts[2] <= MAX_BINDS, 'EXECUTION_BIND_LIMIT')
        result = []
        for row in value:
            require(type(row) is list and len(row) == 4, 'EXECUTION_BIND_REJECTED')
            require(type(row[2]) is bool and type(row[3]) is int and 0 <= row[3] <= 2**64-1, 'EXECUTION_BIND_REJECTED')
            result.append((_text(row[0], 2048, counts, empty=False), _text(row[1], 2048, counts, empty=False), row[2], row[3]))
        return tuple(result)
    result = _text(value, 256 if property in ('User', 'Group', 'PAMName') or property in ENUMS else 2048, counts)
    if property in ENUMS: require(result in ENUMS[property], 'EXECUTION_ENUM_REJECTED')
    return result


@dataclass(frozen=True)
class ConfiguredContext:
    binding: v.InvocationBinding = field(repr=False)
    properties: tuple | None = field(repr=False)  # None: non-Service, not an empty observed context


class ExecutionContextTransport(y.x.SystemdInvocationRelations):
    def _context_query(self, property, binding, owner, budget):
        budget.remaining()
        raw = t._capture(_argv(property, owner, binding.invocation_id), budget)
        budget.remaining()
        return raw

    def _context_detail(self, binding, fd, owner, budget, counts):
        properties = None
        if binding.primary_name.endswith('.service'):
            properties = tuple((name, _decode(name, self._context_query(name, binding, owner, budget), counts))
                               for name in PROPERTIES)
        v._alive(fd)
        return ConfiguredContext(binding, properties)


@dataclass(frozen=True)
class ExecutionContextSample(y.CensusRelationSample):
    def report(self):
        report = super().report(); data = self.private_manifest()
        report.update(state='CENSUS_SERVICE_CONFIGURATION_OBSERVED',
            configured_service_contexts=sum(row['properties'] is not None for row in data['configured_contexts']),
            bound_units_without_service_context=sum(row['properties'] is None for row in data['configured_contexts']),
            selected_service_configuration_observed=True, configured_identity_projection_delivered=False,
            execution_commands_observed=False, effective_context_verified=False)
        return report


class SystemdExecutionContext(y.SystemdCensusRelations):
    def __init__(self, target, storage):
        super().__init__(target, storage)
        self._transport = ExecutionContextTransport(target, storage)

    def __repr__(self): return '<SystemdExecutionContext private configuration observations, no authority>'

    def _contexts(self, bindings, owned, owner, budget):
        representatives = {}
        for binding in bindings: representatives.setdefault(binding.object_path, binding)
        counts = [0, 0, 0]
        return tuple(self._transport._context_detail(binding, owned[binding.pid_hint], owner, budget, counts)
                     for _, binding in sorted(representatives.items()))

    def _between(self, rows, owned, budget, state):
        before, context = state
        candidates = c._candidates(rows, self._uid, self._gid)
        selected, unresolved = y.z._eligible(rows, candidates)
        budget.transport.maximum_calls = t.MAX_CALLS + 66*len(selected)
        units = {u.object_path: u.primary_name for u in before.loaded_units.rows}
        owner = before.provenance.bus_owner
        first = self._bindings(selected, owned, units, owner, budget.transport)
        groups = y.z._groups(first, candidates)
        services = sum(group['primary_name'].endswith('.service') for group in groups)
        budget.transport.maximum_calls = t.MAX_CALLS + 8*len(selected) + 18*len(groups) + 40*services
        details = self._properties(first, owned, owner, budget.transport)
        contexts = self._contexts(first, owned, owner, budget.transport)
        second = self._bindings(selected, owned, units, owner, budget.transport)
        require(first == second, 'CENSUS_INVOCATION_CHANGED')
        require(details == self._properties(second, owned, owner, budget.transport), 'CENSUS_RELATIONS_CHANGED')
        require(contexts == self._contexts(second, owned, owner, budget.transport), 'EXECUTION_CONFIGURATION_CHANGED')
        after, after_context = self._transport._round(budget.transport)
        require(context == after_context, 'CENSUS_INVOCATION_CONTEXT_CHANGED')
        require(budget.transport.calls == budget.transport.maximum_calls, 'CENSUS_INVOCATION_CALLS_INCOMPLETE')
        return first, groups, unresolved, after, details, contexts

    def _sample(self, data, budget, state, observations):
        parent = super()._sample(data, budget, state, observations[:5])
        envelope = parent.private_manifest()
        contexts = [asdict(context) for context in observations[5]]
        def bind(round):
            digest = d.l._sha(d._json({'census_relations_evidence': round.loaded_units.evidence_sha256,
                                      'configured_contexts': contexts}))
            return replace(round, loaded_units=replace(round.loaded_units, evidence_sha256=digest))
        prior = parent.scan()
        scan = replace(prior, before=bind(prior.before), after=bind(prior.after))
        index = self._transport._discovery.inspect(scan, now=scan.finished_at)
        # Preserve every census/relation fact. No configured text becomes a
        # numeric identity, resolved path or additional relevance signal.
        facts = replace(parent.facts(), discovery_sha256=d.l._sha(index._canonical))
        selection = self._transport._selector.inspect(scan, facts, now=scan.finished_at)
        envelope.update(discovery=index.private_manifest(), selection=selection.private_manifest(), configured_contexts=contexts)
        envelope['transport']['policy'] = 'CENSUS_SERVICE_CONFIGURATION_V1'
        envelope['limitations'].remove('NO_CONFIGURED_IDENTITY_OR_EXECUTION_CHAIN')
        envelope['limitations'].extend(('CONFIGURED_TEXT_NOT_EFFECTIVE_CREDENTIALS', 'EXECUTION_COMMANDS_UNOBSERVED',
            'NO_NSS_OR_PATH_RESOLUTION', 'NON_SERVICE_AND_PROCESSLESS_CONTEXTS_UNKNOWN',
            'OTHER_CONFIGURATION_AND_EFFECTIVE_NAMESPACES_UNKNOWN', 'NO_CONFIGURED_FACT_PROJECTION'))
        return ExecutionContextSample(index, d._json(envelope), scan, facts, selection)
