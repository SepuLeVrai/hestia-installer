"""Pure private index of system manager declarations, never a host collector.

Keep loaded units, installed files and manager jobs separate. No relevance
classification, execution, enrollment, projection, implicit clock or host IO.
"""
from dataclasses import dataclass, field
import json
import re

from installer import launcher_inventory as l

MAX_UNITS = 4096
MAX_FILES = 4096
MAX_JOBS = 1024
MAX_NAMES = 4096
MAX_BYTES = 4 * 1024 * 1024
MAX_AGE = 60
UNIT_PREFIX = '/org/freedesktop/systemd1/unit/'
JOB_PREFIX = '/org/freedesktop/systemd1/job/'
BASE_BLOCKERS = ('HOST_TRANSPORT_NOT_IMPLEMENTED', 'DECLARATIONS_NOT_AUTHENTICATED',
                 'RELEVANCE_NOT_CLASSIFIED', 'COVERAGE_NOT_CERTIFIED', 'NOT_ATOMIC',
                 'LAUNCHER_CONTROL_NOT_DELIVERED', 'OTHER_PRODUCER_GROUPS_UNVERIFIED')


class SystemdDiscoveryError(RuntimeError):
    """Fixed diagnostics without names, paths, identities or parser details."""


def require(ok, code='DISCOVERY_INPUT_REJECTED'):
    if not ok: raise SystemdDiscoveryError(code)


def _tuple(value, maximum):
    require(type(value) is tuple and len(value) <= maximum, 'DISCOVERY_LIMIT_OR_TYPE_REJECTED')
    return value


def _number(value, maximum=2**63-1, minimum=0):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def _text(value, maximum):
    require(type(value) is str and 0 < len(value) <= maximum
            and len(value.encode('utf-8')) <= maximum and all(32 <= ord(ch) < 127 for ch in value))
    return value


def _token(value):
    _text(value, 64); require(re.fullmatch('[a-z][a-z0-9-]*', value) is not None)
    return value


def _name(value, *, loaded=False):
    _text(value, 255)
    require(re.fullmatch(r'(?:[A-Za-z0-9:_.@-]|\\x[0-9a-f]{2})+\.[a-z][a-z0-9-]*', value) is not None)
    stem, _, suffix = value.rpartition('.')
    require(stem not in ('', '.', '..') and stem.count('@') <= 1 and not stem.startswith('@'))
    require(not loaded or not stem.endswith('@'), 'DISCOVERY_UNINSTANTIATED_LOADED_UNIT')
    return value


def _path(value):
    # Syntax only; no symlink, mount, permissions or byte attestation.
    _text(value, 2048); return l._path(value)


def _unit_object(value):
    _text(value, 2048)
    require(value.startswith(UNIT_PREFIX) and re.fullmatch('[A-Za-z0-9_]+', value[len(UNIT_PREFIX):]) is not None)
    return value


def _json(value):
    raw = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
    for chunk in encoder.iterencode(value):
        raw.extend(chunk.encode('ascii'))
        require(len(raw) <= MAX_BYTES, 'DISCOVERY_DOCUMENT_LIMIT')
    return bytes(raw)


@dataclass(frozen=True)
class DiscoveryProvenance:
    host_id: str = field(repr=False)
    boot_id: str = field(repr=False)
    manager: str = field(repr=False)
    bus_owner: str = field(repr=False)
    pid_namespace: str = field(repr=False)
    init_pid_namespace: str = field(repr=False)
    mount_namespace: str = field(repr=False)
    init_mount_namespace: str = field(repr=False)
    manager_version: str = field(repr=False)
    capabilities: tuple[str, ...] = field(repr=False)
    unit_paths: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class UnitJobReference:
    identifier: int = field(repr=False)  # zero is an explicit no-job tuple
    kind: str = field(repr=False)
    object_path: str = field(repr=False)


@dataclass(frozen=True)
class LoadedUnit:
    primary_name: str = field(repr=False)
    object_path: str = field(repr=False)
    load_state: str = field(repr=False)
    active_state: str = field(repr=False)
    sub_state: str = field(repr=False)
    following: str | None = field(repr=False)  # None unknown, '' explicitly empty
    names: tuple[str, ...] | None = field(repr=False)  # optional Names observation
    job: UnitJobReference = field(repr=False)


@dataclass(frozen=True)
class InstalledUnitFile:
    path: str = field(repr=False)
    enablement: str = field(repr=False)


@dataclass(frozen=True)
class ManagerJob:
    identifier: int = field(repr=False)
    unit_name: str = field(repr=False)
    kind: str = field(repr=False)
    state: str = field(repr=False)
    object_path: str = field(repr=False)
    unit_object_path: str = field(repr=False)


@dataclass(frozen=True)
class Enumeration:
    state: str = field(repr=False)
    evidence_sha256: str | None = field(repr=False)
    rows: tuple = field(repr=False)


@dataclass(frozen=True)
class DiscoveryRound:
    provenance: DiscoveryProvenance = field(repr=False)
    loaded_units: Enumeration = field(repr=False)
    installed_unit_files: Enumeration = field(repr=False)
    manager_jobs: Enumeration = field(repr=False)


@dataclass(frozen=True)
class DiscoveryScan:
    target: l.LauncherTarget = field(repr=False)
    started_at: int = field(repr=False)
    finished_at: int = field(repr=False)
    elapsed_ms: int = field(repr=False)
    before: DiscoveryRound = field(repr=False)
    after: DiscoveryRound = field(repr=False)


@dataclass(frozen=True)
class DiscoveryIndex:
    _canonical: bytes = field(repr=False)

    def private_manifest(self): return l.strict_json_loads(self._canonical)

    def report(self):
        data = self.private_manifest(); rows = data['observation']
        return {'state': 'SYSTEMD_DISCOVERY_DECLARATIONS_RECORDED', 'origin': 'TRUSTED_ADAPTER_DECLARATIONS',
            'manifest_sha256': l._sha(self._canonical), 'loaded_units': len(rows['loaded_units']['rows']),
            'installed_unit_files': len(rows['installed_unit_files']['rows']),
            'manager_jobs': len(rows['manager_jobs']['rows']), 'distinct_names': rows['distinct_names'],
            'blockers': list(data['blockers']), 'systemd_coverage': 'partial',
            'host_observed_by_this_model': False, 'source_bytes_verified_by_this_model': False,
            'relevance_classified': False, 'projection_delivered': False, 'live_receipt': False,
            'execution_allowed': False, 'drain_allowed': False, 'host_scheduler_inventory_complete': False,
            'storage_inventory_complete': False, 'system_wiring_verified': False,
            'complete_web_backup': False, 'service_activation_delivered': False,
            'application_installed': False, 'writable_business_storage_ready': False,
            'rollback_verified': False, 'phase5_complete': False}


def _job_ref(value):
    require(type(value) is UnitJobReference)
    identifier = _number(value.identifier, 2**32-1)
    if identifier == 0:
        require(type(value.kind) is str and value.kind == '' and type(value.object_path) is str and value.object_path == '/')
    else:
        _token(value.kind); require(value.object_path == JOB_PREFIX + str(identifier) and type(value.object_path) is str)
    return {'identifier': identifier, 'kind': value.kind, 'object_path': value.object_path}


class SystemdDiscovery:
    def __init__(self, target, storage):
        try:
            # Reuse the existing strict target/storage contract without changing it.
            contract = l.LauncherInventory(target, storage)
            self._target, self._storage = dict(contract._target), contract._storage
            self._storage_blocked = contract._storage_blocked
        except Exception: raise SystemdDiscoveryError('DISCOVERY_TARGET_OR_STORAGE_REJECTED') from None

    def __repr__(self): return '<SystemdDiscovery private declaration index>'

    def _provenance(self, value):
        require(type(value) is DiscoveryProvenance)
        require(value.host_id == self._target['host_id'] and type(value.host_id) is str
                and value.boot_id == self._target['boot_id'] and type(value.boot_id) is str
                and type(value.manager) is str and value.manager == 'system', 'DISCOVERY_PROVENANCE_REJECTED')
        _text(value.bus_owner, 255)
        require(re.fullmatch(r':[0-9]+(?:\.[0-9]+)+', value.bus_owner) is not None, 'DISCOVERY_PROVENANCE_REJECTED')
        for kind, current, init in (('pid', value.pid_namespace, value.init_pid_namespace),
                                    ('mnt', value.mount_namespace, value.init_mount_namespace)):
            _text(current, 64); _text(init, 64)
            require(current == init and re.fullmatch(kind+r':\[[1-9][0-9]*\]', current) is not None,
                    'DISCOVERY_PROVENANCE_REJECTED')
        version = _text(value.manager_version, 128)
        capabilities = [_text(item, 128) for item in _tuple(value.capabilities, 64)]
        require(all(re.fullmatch('[A-Za-z][A-Za-z0-9_.]*', item) is not None for item in capabilities))
        require(len(set(capabilities)) == len(capabilities)
                and {'ListUnits', 'ListUnitFiles', 'ListJobs'} <= set(capabilities), 'DISCOVERY_CAPABILITIES_REQUIRED')
        paths = [_path(path) for path in _tuple(value.unit_paths, 64)]
        require(paths and len(set(paths)) == len(paths), 'DISCOVERY_SEARCH_PATH_REQUIRED')
        return {'host_id': value.host_id, 'boot_id': value.boot_id, 'manager': value.manager, 'bus_owner': value.bus_owner,
            'pid_namespace': value.pid_namespace, 'mount_namespace': value.mount_namespace,
            'manager_version': version, 'capabilities': sorted(capabilities), 'unit_paths': paths}

    def _round(self, value):
        require(type(value) is DiscoveryRound)
        result = {'provenance': self._provenance(value.provenance)}
        names, owners, objects, file_paths, job_ids = set(), {}, {}, set(), set()
        issues = set()

        def remember(name):
            names.add(name); require(len(names) <= MAX_NAMES, 'DISCOVERY_NAME_LIMIT')

        def population(batch, row_type, maximum):
            require(type(batch) is Enumeration)
            require(type(batch.state) is str and batch.state == 'observed', 'DISCOVERY_ENUMERATION_UNAVAILABLE')
            l._digest(batch.evidence_sha256)
            rows = _tuple(batch.rows, maximum)
            require(all(type(row) is row_type for row in rows))
            return rows

        loaded = []
        for unit in population(value.loaded_units, LoadedUnit, MAX_UNITS):
            primary, obj = _name(unit.primary_name, loaded=True), _unit_object(unit.object_path)
            require(obj not in objects, 'DISCOVERY_DUPLICATE_OBJECT')
            aliases = None if unit.names is None else [_name(name, loaded=True) for name in _tuple(unit.names, MAX_NAMES)]
            if aliases is None: issues.add('ALIASES_UNOBSERVED')
            else:
                require(primary in aliases and len(aliases) == len(set(aliases)), 'DISCOVERY_NAMES_INCONSISTENT')
                require(all(name.rpartition('.')[2] == primary.rpartition('.')[2] for name in aliases), 'DISCOVERY_NAMES_INCONSISTENT')
            for name in aliases if aliases is not None else (primary,):
                remember(name); require(name not in owners, 'DISCOVERY_ALIAS_CONFLICT'); owners[name] = obj
            following = unit.following
            if following is None: issues.add('FOLLOWING_UNOBSERVED')
            elif type(following) is str and following == '': pass
            else: remember(_name(following, loaded=True))
            row = {'primary_name': primary, 'object_path': obj, 'names': sorted(aliases) if aliases is not None else None,
                'following': following, 'load_state': _token(unit.load_state), 'active_state': _token(unit.active_state),
                'sub_state': _token(unit.sub_state), 'job': _job_ref(unit.job)}
            loaded.append(row); objects[obj] = row
            issues.add('EXECUTION_CONTEXT_UNOBSERVED')
        installed = []
        for file in population(value.installed_unit_files, InstalledUnitFile, MAX_FILES):
            path = _path(file.path); name = _name(path.rsplit('/', 1)[1]); remember(name)
            require(path not in file_paths, 'DISCOVERY_DUPLICATE_FILE'); file_paths.add(path)
            # Basename is retained for indexing, never a verified object association.
            installed.append({'path': path, 'listed_name': name, 'enablement': _token(file.enablement),
                              'loaded_object': None, 'definition_verified': False})
            issues.add('FILE_OBJECT_BINDING_UNVERIFIED')
        jobs, job_names, job_objects = [], {}, set()
        for job in population(value.manager_jobs, ManagerJob, MAX_JOBS):
            identifier = _number(job.identifier, 2**32-1, 1)
            reference = _job_ref(UnitJobReference(identifier, job.kind, job.object_path))
            name, obj = _name(job.unit_name, loaded=True), _unit_object(job.unit_object_path); remember(name)
            require(identifier not in job_ids, 'DISCOVERY_DUPLICATE_JOB'); job_ids.add(identifier)
            require((name not in job_names or job_names[name] == obj) and obj not in job_objects,
                    'DISCOVERY_JOB_BINDING_CONFLICT')
            job_names[name] = obj; job_objects.add(obj)
            require(name not in owners or owners[name] == obj, 'DISCOVERY_JOB_BINDING_CONFLICT')
            if obj not in objects: issues.add('JOB_UNIT_DETAIL_UNAVAILABLE')
            else:
                unit = objects[obj]
                require(unit['names'] is None or name in unit['names'], 'DISCOVERY_JOB_BINDING_CONFLICT')
                if name not in owners: issues.add('JOB_NAME_BINDING_UNVERIFIED')
                require(unit['job'] == reference, 'DISCOVERY_JOB_BINDING_CONFLICT')
            jobs.append({**reference, 'unit_name': name, 'unit_object_path': obj, 'state': _token(job.state)})
            issues.add('SYSTEM_MANAGER_JOB_PRESENT')
        for unit in loaded:
            if unit['job']['identifier'] and unit['job']['identifier'] not in job_ids:
                issues.add('LISTED_JOB_DETAIL_UNAVAILABLE')
            if unit['following'] and unit['following'] not in owners:
                issues.add('FOLLOWING_TARGET_UNRESOLVED')
        # Same job id cannot refer to two loaded objects, including a missing ListJobs row.
        referenced = [row['job']['identifier'] for row in loaded if row['job']['identifier']]
        require(len(referenced) == len(set(referenced)), 'DISCOVERY_JOB_BINDING_CONFLICT')
        by_identifier = {row['identifier']: row for row in jobs}
        for unit in loaded:
            job = by_identifier.get(unit['job']['identifier'])
            if job is not None:
                require(job['unit_object_path'] == unit['object_path'] and job['kind'] == unit['job']['kind'],
                        'DISCOVERY_JOB_BINDING_CONFLICT')
        for key, batch, rows, order in (
            ('loaded_units', value.loaded_units, loaded, 'object_path'),
            ('installed_unit_files', value.installed_unit_files, installed, 'path'),
            ('manager_jobs', value.manager_jobs, jobs, 'identifier')):
            result[key] = {'state': 'observed', 'evidence_sha256': batch.evidence_sha256, 'rows': sorted(rows, key=lambda row: row[order])}
        result['distinct_names'] = len(names)
        return result, issues

    def inspect(self, scan, *, now, previous=None):
        try:
            require(type(scan) is DiscoveryScan and l._target(scan.target) == self._target, 'DISCOVERY_TARGET_MISMATCH')
            start, end, current = (_number(value) for value in (scan.started_at, scan.finished_at, now))
            _number(scan.elapsed_ms, MAX_AGE * 1000)
            require(start <= end <= current and current - start <= MAX_AGE, 'DISCOVERY_STALE')
            before, issues = self._round(scan.before); after, after_issues = self._round(scan.after)
            require(before == after and issues == after_issues, 'DISCOVERY_CHANGED_DURING_READ')
            blockers = set(BASE_BLOCKERS) | issues
            if self._storage_blocked: blockers.add('STORAGE_REQUIREMENTS_UNRESOLVED')
            data = {'version': 1, 'target': self._target, 'storage_manifest_sha256': self._storage,
                'coverage': {channel: 'partial' if channel == 'systemd_system' else 'unknown' for channel in l.CHANNELS},
                'observation': before, 'blockers': sorted(blockers)}
            if previous is not None:
                require(type(previous) is DiscoveryIndex and type(previous._canonical) is bytes
                        and len(previous._canonical) <= MAX_BYTES, 'DISCOVERY_PREVIOUS_REJECTED')
                prior = previous.private_manifest(); interval = prior.pop('interval')
                require(set(interval) == {'started_at', 'finished_at', 'elapsed_ms'})
                old_start, old_end = _number(interval['started_at']), _number(interval['finished_at'])
                _number(interval['elapsed_ms'], MAX_AGE * 1000)
                require(old_start <= old_end <= start and old_end - old_start <= MAX_AGE, 'DISCOVERY_REPLAYED')
                require(prior == data, 'DISCOVERY_OBSERVATIONS_CHANGED')
            data['interval'] = {'started_at': start, 'finished_at': end, 'elapsed_ms': scan.elapsed_ms}
            return DiscoveryIndex(_json(data))
        except SystemdDiscoveryError: raise
        except Exception: raise SystemdDiscoveryError('DISCOVERY_INSPECTION_UNAVAILABLE') from None
