"""Pure conservative review selection from index-bound, private declarations.

No host IO, provisioned-profile admission, exclusion, projection or authority.
Positive signals widen review; they do not establish a writer or drain scope.
"""
from collections import deque
from dataclasses import dataclass, field
import re

from installer import systemd_discovery as d

l = d.l
MAX_DETAILS = 128
MAX_IDENTITIES = 1024
MAX_PATHS = 1024
MAX_RELATIONS = 8192
MAX_PER_UNIT = 64
MAX_CANDIDATES = 128
CANDIDATE_REASONS = ('UID_MATCH_IN_TASK', 'GID_MATCH_IN_TASK', 'SUPPLEMENTARY_GROUP_MATCH_IN_TASK',
    'DESCENDANT_AT_OBSERVATION', 'UNRESOLVED_TASK')
LINK_PROPERTIES = frozenset({'Triggers', 'TriggeredBy', 'Requires', 'Wants',
    'BindsTo', 'Upholds', 'OnSuccess', 'OnFailure', 'Unit'})
ORDER_PROPERTIES = frozenset({'Before', 'After'})
PHASES = ('prepare', 'start', 'reload', 'stop', 'post', 'runtime')
PATH_ROLES = ('executable', 'script', 'cwd', 'configuration', 'destination', 'generated_source')
BLOCKERS = ('RELEVANCE_DECLARATIVE_ONLY', 'PROVISIONED_PROFILE_BRIDGE_NOT_DELIVERED',
    'EXECUTION_CONTEXT_INCOMPLETE', 'RELATIONS_NOT_EXHAUSTIVE', 'SELECTION_IS_NOT_WRITER_PROOF')


class SystemdRelevanceError(RuntimeError):
    """Fixed codes without private facts or nested parser diagnostics."""


def require(ok, code='RELEVANCE_INPUT_REJECTED'):
    if not ok: raise SystemdRelevanceError(code)


def _enum(value, choices):
    require(type(value) is str and value in choices)
    return value


def _path(value):
    d._text(value, 2048)
    return l._path(value, root=True)


def _namespace(value, kind):
    d._text(value, 64)
    require(re.fullmatch(kind+r':\[[1-9][0-9]*\]', value) is not None)
    return value


def _overlap(left, right):
    # Components only. Never resolve a symlink, chroot, variable or namespace.
    a, b = left.strip('/').split('/') if left != '/' else [], right.strip('/').split('/') if right != '/' else []
    return a[:len(b)] == b or b[:len(a)] == a


@dataclass(frozen=True)
class ProcessBinding:
    pid: int = field(repr=False)
    start_ticks: int = field(repr=False)
    pid_namespace: str = field(repr=False)
    cgroup: str = field(repr=False)
    membership_sha256: str = field(repr=False)


@dataclass(frozen=True)
class IdentityFact:
    kind: str = field(repr=False)  # configured / effective
    state: str = field(repr=False)  # observed / unknown / unreadable
    uid: int | None = field(repr=False)
    gid: int | None = field(repr=False)
    groups: tuple[int, ...] | None = field(repr=False)
    dynamic_user: bool | None = field(repr=False)
    process: ProcessBinding | None = field(repr=False)
    evidence_sha256: str | None = field(repr=False)


@dataclass(frozen=True)
class PathFact:
    role: str = field(repr=False)
    phase: str = field(repr=False)
    state: str = field(repr=False)  # textual / resolved / unknown / unreadable
    path: str | None = field(repr=False)
    mount_namespace: str | None = field(repr=False)
    root_directory: str | None = field(repr=False)
    evidence_sha256: str | None = field(repr=False)


@dataclass(frozen=True)
class RelationFact:
    property: str = field(repr=False)
    target_name: str = field(repr=False)
    target_object: str | None = field(repr=False)
    evidence_sha256: str = field(repr=False)


@dataclass(frozen=True)
class ExternalBinding:
    instance: str = field(repr=False)
    origin: str = field(repr=False)
    evidence_sha256: str = field(repr=False)


@dataclass(frozen=True)
class CensusCandidateFact:
    process: ProcessBinding = field(repr=False)  # the bound leader, not its threads
    reasons: tuple[str, ...] = field(repr=False)
    census_sha256: str = field(repr=False)
    binding_sha256: str = field(repr=False)


@dataclass(frozen=True)
class UnitFacts:
    object_path: str = field(repr=False)
    primary_name: str = field(repr=False)
    identities: tuple[IdentityFact, ...] | None = field(repr=False)
    paths: tuple[PathFact, ...] | None = field(repr=False)
    relations: tuple[RelationFact, ...] | None = field(repr=False)
    bindings: tuple[ExternalBinding, ...] | None = field(repr=False)
    candidates: tuple[CensusCandidateFact, ...] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RelevanceFacts:
    discovery_sha256: str = field(repr=False)
    observed_at: int = field(repr=False)
    units: tuple[UnitFacts, ...] = field(repr=False)
    census_sha256: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RelevanceSelection:
    _canonical: bytes = field(repr=False)

    def private_manifest(self): return l.strict_json_loads(self._canonical)

    def report(self):
        data = self.private_manifest()
        report = d.DiscoveryIndex(d._json(data['discovery'])).report()
        report.update(state='SYSTEMD_DECLARED_RELEVANCE_EVALUATED', manifest_sha256=l._sha(self._canonical),
            detailed_units=len(data['facts']['units']), relation_count=data['relation_count'],
            related_loaded_units=sum(row['decision'] == 'RELATED_UNMANAGED' for row in data['loaded_units']),
            unresolved_loaded_units=sum(row['decision'] == 'UNRESOLVED' for row in data['loaded_units']),
            known_provisioned_units=0, related_jobs=sum(row['decision'] == 'RELATED_UNMANAGED' for row in data['manager_jobs']),
            blockers=list(data['blockers']), host_relevance_verified=False, automatic_exclusion_allowed=False)
        # The inherited relevance_classified flag concerns host certification.
        return report


class SystemdRelevance:
    def __init__(self, target, storage):
        try:
            self._discovery = d.SystemdDiscovery(target, storage)
            self._target = dict(self._discovery._target)
            self._roots = tuple(sorted({self._target['webroot'], self._target['configuration'],
                *(_path(row['path']) for row in storage.private_manifest()['scopes'])}))
        except Exception: raise SystemdRelevanceError('RELEVANCE_TARGET_OR_STORAGE_REJECTED') from None

    def __repr__(self): return '<SystemdRelevance private declaration selection>'

    def _identity(self, fact, provenance, reasons, issues):
        require(type(fact) is IdentityFact)
        kind = _enum(fact.kind, ('configured', 'effective'))
        state = _enum(fact.state, ('observed', 'unknown', 'unreadable'))
        process = None
        if state != 'observed':
            require(all(v is None for v in (fact.uid, fact.gid, fact.groups, fact.dynamic_user, fact.process, fact.evidence_sha256)))
            issues.add('IDENTITY_UNAVAILABLE')
        else:
            l._digest(fact.evidence_sha256)
            for value in (fact.uid, fact.gid):
                if value is not None: l._number(value)
            if fact.groups is not None:
                groups = [l._number(value) for value in d._tuple(fact.groups, MAX_PER_UNIT)]
                require(len(groups) == len(set(groups)), 'RELEVANCE_DUPLICATE_FACT')
            require(fact.dynamic_user is None or type(fact.dynamic_user) is bool)
            if kind == 'configured':
                require(fact.process is None)
            else:
                p = fact.process; require(type(p) is ProcessBinding and fact.dynamic_user is None)
                process = {'pid': d._number(p.pid, 2**31-1, 1), 'start_ticks': d._number(p.start_ticks, minimum=1),
                    'pid_namespace': _namespace(p.pid_namespace, 'pid'), 'cgroup': _path(p.cgroup),
                    'membership_sha256': l._digest(p.membership_sha256)}
                require(process['pid_namespace'] == provenance['pid_namespace'], 'RELEVANCE_PROCESS_BINDING_REJECTED')
                issues.add('PROCESS_MEMBERSHIP_DECLARED_ONLY')
            if (fact.uid == self._target['web_uid'] or fact.gid == self._target['web_gid']
                    or self._target['web_gid'] in (fact.groups or ())):
                reasons.add('CONFIGURED_IDENTITY_MATCH' if kind == 'configured' else 'EFFECTIVE_IDENTITY_MATCH')
            if fact.uid is None or fact.gid is None or fact.groups is None: issues.add('IDENTITY_PARTIAL')
            if fact.uid == 0: issues.add('ROOT_IDENTITY_NOT_EXCLUDED')
            if kind == 'configured' and fact.dynamic_user is not False: issues.add('DYNAMIC_IDENTITY_UNRESOLVED')
        return {'kind': kind, 'state': state, 'uid': fact.uid, 'gid': fact.gid,
            'groups': sorted(fact.groups) if fact.groups is not None else None, 'dynamic_user': fact.dynamic_user,
            'process': process, 'evidence_sha256': fact.evidence_sha256}

    def _path_fact(self, fact, provenance, reasons, issues):
        require(type(fact) is PathFact)
        role, phase = _enum(fact.role, PATH_ROLES), _enum(fact.phase, PHASES)
        state = _enum(fact.state, ('textual', 'resolved', 'unknown', 'unreadable'))
        if state in ('unknown', 'unreadable'):
            require(all(v is None for v in (fact.path, fact.mount_namespace, fact.root_directory, fact.evidence_sha256)))
            issues.add('PATH_UNAVAILABLE')
        else:
            _path(fact.path); l._digest(fact.evidence_sha256)
            if state == 'textual': require(fact.mount_namespace is None and fact.root_directory is None)
            else:
                _namespace(fact.mount_namespace, 'mnt'); _path(fact.root_directory)
            local = state == 'resolved' and fact.mount_namespace == provenance['mount_namespace'] and fact.root_directory == '/'
            if not local: issues.add('PATH_CONTEXT_UNVERIFIED')
            issues.add('PATH_IDENTITY_NOT_ATTESTED')
            if any(_overlap(fact.path, root) for root in self._roots):
                reasons.add('DECLARED_HOST_PATH_MATCH' if local else 'TEXTUAL_PATH_HINT' if state == 'textual' else 'CONTEXTUAL_PATH_HINT')
                if fact.path == '/': issues.add('BROAD_PATH_HINT')
        return {'role': role, 'phase': phase, 'state': state, 'path': fact.path,
            'mount_namespace': fact.mount_namespace, 'root_directory': fact.root_directory, 'evidence_sha256': fact.evidence_sha256}

    def _candidate(self, fact, census, provenance, reasons, issues):
        require(type(fact) is CensusCandidateFact and census is not None, 'RELEVANCE_CENSUS_BINDING_REJECTED')
        require(l._digest(fact.census_sha256) == census, 'RELEVANCE_CENSUS_BINDING_REJECTED')
        signals = tuple(_enum(value, CANDIDATE_REASONS) for value in d._tuple(fact.reasons, len(CANDIDATE_REASONS)))
        require(signals and len(signals) == len(set(signals)), 'RELEVANCE_CANDIDATE_REASONS_REJECTED')
        p = fact.process; require(type(p) is ProcessBinding)
        process = {'pid': d._number(p.pid, 2**31-1, 2), 'start_ticks': d._number(p.start_ticks),
            'pid_namespace': _namespace(p.pid_namespace, 'pid'), 'cgroup': _path(p.cgroup),
            'membership_sha256': l._digest(p.membership_sha256)}
        require(process['pid_namespace'] == provenance['pid_namespace'], 'RELEVANCE_PROCESS_BINDING_REJECTED')
        issues.update(('CENSUS_SIGNALS_DECLARED_ONLY', 'CENSUS_NON_ATOMIC', 'THREAD_UNIT_MEMBERSHIP_UNVERIFIED'))
        for signal in signals:
            if signal == 'UNRESOLVED_TASK': issues.add('CENSUS_TASK_UNRESOLVED')
            else: reasons.add('CENSUS_'+signal)
        return {'process': process, 'reasons': sorted(signals), 'census_sha256': census,
            'binding_sha256': l._digest(fact.binding_sha256)}

    def inspect(self, scan, facts, *, now):
        try:
            # Revalidate the typed scan, rather than trust arbitrary serialized index bytes.
            index = self._discovery.inspect(scan, now=now); source = index.private_manifest()
            require(type(facts) is RelevanceFacts)
            census = None if facts.census_sha256 is None else l._digest(facts.census_sha256)
            require(l._digest(facts.discovery_sha256) == l._sha(index._canonical), 'RELEVANCE_INDEX_BINDING_REJECTED')
            observed = d._number(facts.observed_at)
            require(scan.started_at <= observed <= scan.finished_at, 'RELEVANCE_FACTS_OUTSIDE_SCAN')
            loaded = source['observation']['loaded_units']['rows']; provenance = source['observation']['provenance']
            objects = {row['object_path']: row for row in loaded}
            names = {name: row['object_path'] for row in loaded for name in (row['names'] or [row['primary_name']])}
            reasons = {obj: set() for obj in objects}
            issues = {obj: {'EXECUTION_CONTEXT_INCOMPLETE', 'RELATIONS_NOT_EXHAUSTIVE'} for obj in objects}
            details, seen, edges, process_owners = [], set(), [], {}
            process_contexts = {}
            totals = {'identities': 0, 'paths': 0, 'relations': 0, 'candidates': 0}
            for unit in d._tuple(facts.units, MAX_DETAILS):
                require(type(unit) is UnitFacts)
                obj, primary = d._unit_object(unit.object_path), d._name(unit.primary_name, loaded=True)
                require(obj in objects and objects[obj]['primary_name'] == primary, 'RELEVANCE_UNIT_BINDING_REJECTED')
                require(obj not in seen, 'RELEVANCE_DUPLICATE_UNIT'); seen.add(obj)
                detail = {'object_path': obj, 'primary_name': primary}
                for key, maximum in (('identities', MAX_PER_UNIT), ('paths', MAX_PER_UNIT), ('relations', MAX_RELATIONS), ('bindings', 16), ('candidates', MAX_CANDIDATES)):
                    values = getattr(unit, key)
                    if values is None:
                        detail[key] = None; issues[obj].add(key.upper() + '_UNOBSERVED'); continue
                    values = d._tuple(values, maximum)
                    if key in totals:
                        totals[key] += len(values)
                        require(totals[key] <= {'identities': MAX_IDENTITIES, 'paths': MAX_PATHS, 'relations': MAX_RELATIONS, 'candidates': MAX_CANDIDATES}[key],
                                'RELEVANCE_FACT_LIMIT')
                    rows, keys = [], set()
                    for value in values:
                        if key == 'identities':
                            row = self._identity(value, provenance, reasons[obj], issues[obj])
                            identity_key = row['process']['pid'] if row['process'] else row['kind']
                        elif key == 'candidates':
                            row = self._candidate(value, census, provenance, reasons[obj], issues[obj])
                            identity_key = row['process']['pid']
                        elif key == 'paths':
                            row = self._path_fact(value, provenance, reasons[obj], issues[obj])
                            identity_key = (row['role'], row['phase'], row['path'])
                        elif key == 'bindings':
                            require(type(value) is ExternalBinding and type(value.instance) is str and value.instance == self._target['instance'],
                                    'RELEVANCE_EXTERNAL_BINDING_REJECTED')
                            origin = _enum(value.origin, ('operator_task', 'operator_catalog', 'sql_client'))
                            row = {'instance': value.instance, 'origin': origin, 'evidence_sha256': l._digest(value.evidence_sha256)}
                            identity_key = origin; reasons[obj].add('DECLARED_EXTERNAL_BINDING'); issues[obj].add('EXTERNAL_BINDING_DECLARED_ONLY')
                        else:
                            require(type(value) is RelationFact)
                            prop = d._text(value.property, 64)
                            require(re.fullmatch('[A-Za-z][A-Za-z0-9]*', prop) is not None)
                            name = d._name(value.target_name); target = value.target_object
                            if target is not None:
                                d._unit_object(target)
                                require(target in objects and names.get(name) == target, 'RELEVANCE_RELATION_BINDING_REJECTED')
                            else:
                                issues[obj].add('RELATION_TARGET_UNRESOLVED')
                                if name.rpartition('.')[0].endswith('@'): issues[obj].add('TEMPLATE_INSTANCES_UNKNOWN')
                            if prop == 'Unit': require(primary.rpartition('.')[2] in ('timer', 'path', 'socket'))
                            if prop in ORDER_PROPERTIES: issues[obj].add('ORDERING_IS_NOT_ACTIVATION')
                            elif prop not in LINK_PROPERTIES: issues[obj].add('RELATION_KIND_UNSUPPORTED')
                            if prop in LINK_PROPERTIES and target is not None: edges.append((obj, target))
                            row = {'property': prop, 'target_name': name, 'target_object': target, 'evidence_sha256': l._digest(value.evidence_sha256)}
                            identity_key = (prop, name)
                        require(identity_key not in keys, 'RELEVANCE_DUPLICATE_FACT')
                        if key in ('identities', 'candidates') and row.get('process'):
                            process = row['process']; pid = process['pid']
                            context = tuple(process[k] for k in ('start_ticks', 'pid_namespace', 'cgroup'))
                            require(process_owners.get(pid, obj) == obj and process_contexts.get(pid, context) == context,
                                    'RELEVANCE_PROCESS_BINDING_REJECTED')
                            process_owners[pid], process_contexts[pid] = obj, context
                        keys.add(identity_key); rows.append(row)
                    detail[key] = sorted(rows, key=d._json)
                details.append(detail)
            # Undirected review expansion, not systemd execution semantics. Each
            # object is queued once; every admitted edge is considered at most twice.
            adjacent = {obj: set() for obj in objects}
            for left, right in edges: adjacent[left].add(right); adjacent[right].add(left)
            selected = {obj for obj in objects if reasons[obj]}
            queue = deque(sorted(selected))
            while queue:
                obj = queue.popleft()
                for other in sorted(adjacent[obj]):
                    if other not in selected:
                        selected.add(other); reasons[other].add('RELATION_TO_RELATED_CANDIDATE'); queue.append(other)
            decisions = []
            for obj in sorted(objects):
                if obj not in seen: issues[obj].add('UNIT_DETAILS_UNOBSERVED')
                if obj not in selected: issues[obj].add('NO_POSITIVE_SIGNAL_NOT_EXCLUSION')
                decisions.append({'object_path': obj, 'decision': 'RELATED_UNMANAGED' if obj in selected else 'UNRESOLVED',
                    'reasons': sorted(reasons[obj]), 'issues': sorted(issues[obj]), 'enrolled': False})
            jobs = []
            for job in source['observation']['manager_jobs']['rows']:
                bound = names.get(job['unit_name']) == job['unit_object_path'] and job['unit_object_path'] in objects
                related = bound and job['unit_object_path'] in selected
                jobs.append({'identifier': job['identifier'], 'decision': 'RELATED_UNMANAGED' if related else 'UNRESOLVED',
                    'reason': 'RELATED_LOADED_OBJECT' if related else 'JOB_NOT_EXCLUDED', 'unit_binding_verified_by_declarations': bound})
            result = {'version': 1, 'discovery_sha256': facts.discovery_sha256, 'discovery': source,
                'facts': {'observed_at': observed, 'census_sha256': census, 'units': sorted(details, key=lambda row: row['object_path'])},
                'loaded_units': decisions, 'manager_jobs': jobs,
                'installed_unit_files': [{'path': row['path'], 'decision': 'UNRESOLVED', 'reason': 'FILE_OBJECT_BINDING_UNVERIFIED'}
                    for row in source['observation']['installed_unit_files']['rows']],
                'relation_count': totals['relations'], 'blockers': sorted(set(source['blockers']) | set(BLOCKERS))}
            return RelevanceSelection(d._json(result))
        except SystemdRelevanceError: raise
        except d.SystemdDiscoveryError as exc:
            code = 'RELEVANCE_DOCUMENT_LIMIT' if str(exc) == 'DISCOVERY_DOCUMENT_LIMIT' else 'RELEVANCE_DISCOVERY_OR_INPUT_REJECTED'
            raise SystemdRelevanceError(code) from None
        except Exception: raise SystemdRelevanceError('RELEVANCE_INPUT_REJECTED') from None
