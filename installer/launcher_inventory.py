"""Validate private launcher declarations; never collect or control a host.

Inputs come from a future trusted adapter, not HTTP. A consistent declaration
is not evidence of absence, a live receipt, a drain lease or an execution list.
No files, clocks, processes, services, network or SQL are accessed here.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re

from installer.model import strict_json_loads
from installer.storage_inventory import StorageRequirements, PRODUCERS
from installer.web_releases import STORAGE_COMMIT, get_release

CHANNELS = ('systemd_system', 'systemd_user', 'cron_system', 'cron_users', 'queued_jobs', 'external_launchers')
COVERAGE_STATES = ('unknown', 'unreadable', 'partial', 'observed')
MAX_LAUNCHERS = 128
MAX_CHAIN = 8
MAX_DEFINITIONS = 16
MAX_BYTES = 256 * 1024
MAX_AGE_SECONDS = 60
BASE_BLOCKERS = ('HOST_COLLECTION_NOT_IMPLEMENTED', 'COVERAGE_NOT_CERTIFIED',
                 'LAUNCHER_CONTROL_NOT_DELIVERED', 'OTHER_PRODUCER_GROUPS_UNVERIFIED')


class LauncherInventoryError(RuntimeError):
    """Fixed diagnostic only, never the private observation or parser exception."""


def require(ok, code='LAUNCHER_INPUT_REJECTED'):
    if not ok: raise LauncherInventoryError(code)


def _text(value, limit=2048):
    require(type(value) is str and 0 < len(value) <= limit and len(value.encode('utf-8')) <= limit
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    return value


def _path(value, *, root=False):
    _text(value)
    if root and value == '/': return value
    require(value.startswith('/') and value != '/' and all(p not in ('', '.', '..') for p in value.split('/')[1:]))
    return value


def _digest(value, *, optional=False):
    if optional and value is None: return None
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None)
    return value


def _number(value, *, positive=False):
    require(type(value) is int and (1 if positive else 0) <= value <= 2147483647)
    return value


def _tuple(value, maximum):
    require(type(value) is tuple and len(value) <= maximum, 'LAUNCHER_LIMIT_OR_TYPE_REJECTED')
    return value


def _enum(value, choices):
    require(type(value) is str and value in choices)
    return value


def _json(value):
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    require(len(raw) <= MAX_BYTES, 'LAUNCHER_DOCUMENT_LIMIT')
    return raw


def _sha(raw): return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class LauncherTarget:
    instance: str = field(repr=False)
    source_commit: str = field(repr=False)
    source_tree: str = field(repr=False)
    webroot: str = field(repr=False)
    configuration: str = field(repr=False)
    maintenance: str = field(repr=False)
    web_uid: int = field(repr=False)
    web_gid: int = field(repr=False)
    host_id: str = field(repr=False)
    boot_id: str = field(repr=False)


@dataclass(frozen=True)
class FileObservation:
    path: str = field(repr=False)
    sha256: str | None = field(repr=False)


@dataclass(frozen=True)
class CoverageObservation:
    channel: str = field(repr=False)
    state: str = field(repr=False)
    evidence_sha256: str | None = field(repr=False)


@dataclass(frozen=True)
class LauncherObservation:
    key: str = field(repr=False)
    channel: str = field(repr=False)
    definitions: tuple[FileObservation, ...] = field(repr=False)
    chain: tuple[FileObservation, ...] = field(repr=False)
    chain_mode: str = field(repr=False)  # fixed / dynamic / unknown
    argv_sha256: str | None = field(repr=False)
    environment_sha256: str | None = field(repr=False)
    uid: int | None = field(repr=False)
    gid: int | None = field(repr=False)
    groups: tuple[int, ...] | None = field(repr=False)
    cwd: str | None = field(repr=False)
    storage_roles: tuple[str, ...] | None = field(repr=False)
    sql_identities_sha256: str | None = field(repr=False)
    state: str = field(repr=False)  # unknown / idle / active / queued
    trigger: str = field(repr=False)  # unknown / disarmed / armed
    cgroup: str | None = field(repr=False)
    maintenance: str | None = field(repr=False)


@dataclass(frozen=True)
class LauncherSnapshot:
    target: LauncherTarget = field(repr=False)
    observed_at: int = field(repr=False)  # explicit UTC epoch seconds from adapter
    coverage: tuple[CoverageObservation, ...] = field(repr=False)
    launchers: tuple[LauncherObservation, ...] = field(repr=False)


@dataclass(frozen=True)
class LauncherRequirements:
    _canonical: bytes = field(repr=False)

    def private_manifest(self):
        return strict_json_loads(self._canonical)

    def report(self):
        data = self.private_manifest()
        return {'state': 'LAUNCHER_DECLARATIONS_RECORDED', 'origin': 'TRUSTED_ADAPTER_DECLARATIONS',
            'manifest_sha256': _sha(self._canonical), 'input_channels': len(data['coverage']),
            'input_launchers': len(data['launchers']), 'blockers': list(data['blockers']),
            'host_observed_by_this_model': False, 'source_bytes_verified_by_this_model': False,
            'live_receipt': False, 'execution_allowed': False, 'drain_allowed': False,
            'host_scheduler_inventory_complete': False, 'storage_inventory_complete': False,
            'system_wiring_verified': False, 'complete_web_backup': False,
            'service_activation_delivered': False, 'application_installed': False,
            'writable_business_storage_ready': False, 'apply_allowed': False,
            'rollback_verified': False, 'phase5_complete': False}


def _target(value):
    require(type(value) is LauncherTarget)
    require(type(value.instance) is str and re.fullmatch('[a-f0-9]{32}', value.instance) is not None)
    require(value.source_commit == STORAGE_COMMIT and type(value.source_commit) is str, 'LAUNCHER_SOURCE_MISMATCH')
    release = get_release(value.source_commit)
    require(type(value.source_tree) is str and value.source_tree == release.tree, 'LAUNCHER_SOURCE_MISMATCH')
    web, conf, gate = (_path(v) for v in (value.webroot, value.configuration, value.maintenance))
    require(web != conf and not web.startswith(conf + '/') and not conf.startswith(web + '/'))
    require(conf.startswith('/var/lib/') and gate == conf + '/maintenance', 'LAUNCHER_TARGET_MISMATCH')
    require(type(value.host_id) is str and re.fullmatch('[a-f0-9]{32}', value.host_id) is not None)
    require(type(value.boot_id) is str and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value.boot_id) is not None)
    return {'instance': value.instance, 'source_commit': value.source_commit, 'source_tree': value.source_tree,
            'webroot': web, 'configuration': conf, 'maintenance': gate,
            'web_uid': _number(value.web_uid, positive=True), 'web_gid': _number(value.web_gid, positive=True),
            'host_id': value.host_id, 'boot_id': value.boot_id}


def _files(values, maximum):
    result = []
    for value in _tuple(values, maximum):
        require(type(value) is FileObservation)
        result.append({'path': _path(value.path), 'sha256': _digest(value.sha256, optional=True)})
    require(len({x['path'] for x in result}) == len(result), 'LAUNCHER_DUPLICATE_REFERENCE')
    return result


class LauncherInventory:
    def __init__(self, target, storage):
        try:
            self._target = _target(target)
            require(type(storage) is StorageRequirements, 'LAUNCHER_STORAGE_MISMATCH')
            require(type(storage._canonical) is bytes and len(storage._canonical) <= MAX_BYTES, 'LAUNCHER_STORAGE_MISMATCH')
            data = storage.private_manifest()
            require(type(data) is dict and set(data) == {'version', 'source_commit', 'runtime_sha256', 'scopes', 'producers', 'blockers'},
                    'LAUNCHER_STORAGE_MISMATCH')
            require(type(data['version']) is int and data['version'] == 1 and data['source_commit'] == target.source_commit
                    and data['runtime_sha256'] == get_release(target.source_commit).runtime_sha256, 'LAUNCHER_STORAGE_MISMATCH')
            require(type(data['scopes']) is list and 0 < len(data['scopes']) <= 64, 'LAUNCHER_STORAGE_MISMATCH')
            scopes = {}
            for row in data['scopes']:
                role = _text(row['role'], 64)
                require(role not in scopes, 'LAUNCHER_STORAGE_MISMATCH')
                scopes[role] = _path(row['path'])
            require(scopes.get('uploads') == target.webroot + '/uploads'
                    and scopes.get('managed_configuration') == target.configuration, 'LAUNCHER_STORAGE_MISMATCH')
            require(type(data['producers']) is list and len(data['producers']) == len(PRODUCERS)
                    and {r['group'] for r in data['producers']} == set(PRODUCERS)
                    and all(r['state'] == 'REQUIRED_NOT_VERIFIED' for r in data['producers']), 'LAUNCHER_STORAGE_MISMATCH')
            self._storage = _sha(storage._canonical)
            self._roles = frozenset(scopes)
            require(type(data['blockers']) is list and len(data['blockers']) <= 256, 'LAUNCHER_STORAGE_MISMATCH')
            for blocker in data['blockers']: _text(blocker, 128)
            self._storage_blocked = bool(data['blockers'])
        except LauncherInventoryError: raise
        except Exception: raise LauncherInventoryError('LAUNCHER_STORAGE_MISMATCH') from None

    def __repr__(self): return '<LauncherInventory private declaration validator>'

    def _launcher(self, value):
        require(type(value) is LauncherObservation)
        key = _text(value.key, 64)
        require(re.fullmatch('[A-Za-z0-9_.-]+', key) is not None)
        channel = _enum(value.channel, CHANNELS)
        definitions = sorted(_files(value.definitions, MAX_DEFINITIONS), key=lambda x: x['path'])
        chain = _files(value.chain, MAX_CHAIN)  # execution order is significant
        mode = _enum(value.chain_mode, ('fixed', 'dynamic', 'unknown'))
        argv, env, sql = (_digest(v, optional=True) for v in
                          (value.argv_sha256, value.environment_sha256, value.sql_identities_sha256))
        uid, gid = (None if v is None else _number(v) for v in (value.uid, value.gid))
        groups = None
        if value.groups is not None:
            groups = sorted(_number(v) for v in _tuple(value.groups, 64))
            require(len(set(groups)) == len(groups), 'LAUNCHER_DUPLICATE_REFERENCE')
        cwd = None if value.cwd is None else _path(value.cwd, root=True)
        cgroup = None if value.cgroup is None else _path(value.cgroup, root=True)
        gate = None if value.maintenance is None else _path(value.maintenance)
        roles = None
        if value.storage_roles is not None:
            roles = sorted(_text(v, 64) for v in _tuple(value.storage_roles, 64))
            require(len(set(roles)) == len(roles), 'LAUNCHER_DUPLICATE_REFERENCE')
            require(set(roles) <= self._roles, 'LAUNCHER_UNKNOWN_STORAGE_ROLE')
        state = _enum(value.state, ('unknown', 'idle', 'active', 'queued'))
        trigger = _enum(value.trigger, ('unknown', 'disarmed', 'armed'))
        issues = {'LAUNCHER_EXECUTION_NOT_ENROLLED'}
        if not definitions or not chain or any(x['sha256'] is None for x in definitions + chain): issues.add('DEFINITION_OR_CHAIN_UNRESOLVED')
        if mode != 'fixed': issues.add('DYNAMIC_OR_UNKNOWN_CHAIN')
        if argv is None or env is None or sql is None or cwd is None or roles is None: issues.add('EXECUTION_CONTEXT_UNRESOLVED')
        if uid is None or gid is None or groups is None: issues.add('IDENTITY_UNRESOLVED')
        if (uid is not None and uid != self._target['web_uid']) or (gid is not None and gid != self._target['web_gid']): issues.add('OTHER_IDENTITY_REQUIRES_COORDINATION')
        if groups and any(g != self._target['web_gid'] for g in groups): issues.add('SUPPLEMENTARY_GROUPS_REQUIRE_REVIEW')
        if state == 'unknown' or trigger == 'unknown' or cgroup is None: issues.add('LIVE_STATE_UNRESOLVED')
        if state in ('active', 'queued'): issues.add('ACTIVE_OR_QUEUED_LAUNCHER')
        if trigger == 'armed': issues.add('ARMED_TRIGGER')
        if gate != self._target['maintenance']: issues.add('COMMON_GATE_UNRESOLVED_OR_DIFFERENT')
        return {'key': key, 'channel': channel, 'definitions': definitions, 'chain': chain, 'chain_mode': mode,
                'argv_sha256': argv, 'environment_sha256': env, 'sql_identities_sha256': sql,
                'uid': uid, 'gid': gid, 'groups': groups, 'cwd': cwd, 'storage_roles': roles,
                'state': state, 'trigger': trigger, 'cgroup': cgroup, 'maintenance': gate, 'issues': sorted(issues)}

    def inspect(self, snapshot, *, now, previous=None):
        try:
            require(type(snapshot) is LauncherSnapshot)
            require(_target(snapshot.target) == self._target, 'LAUNCHER_TARGET_MISMATCH')
            require(type(now) is int and type(snapshot.observed_at) is int
                    and 0 <= snapshot.observed_at <= now <= 2**63 - 1
                    and now - snapshot.observed_at <= MAX_AGE_SECONDS, 'LAUNCHER_OBSERVATION_STALE')
            coverage = []
            for value in _tuple(snapshot.coverage, len(CHANNELS)):
                require(type(value) is CoverageObservation)
                channel = _enum(value.channel, CHANNELS); state = _enum(value.state, COVERAGE_STATES)
                digest = _digest(value.evidence_sha256, optional=True)
                require(state != 'observed' or digest is not None, 'LAUNCHER_COVERAGE_INCONSISTENT')
                coverage.append({'channel': channel, 'state': state, 'evidence_sha256': digest})
            require(len(coverage) == len(CHANNELS) and {c['channel'] for c in coverage} == set(CHANNELS), 'LAUNCHER_COVERAGE_INCOMPLETE')
            launchers = [self._launcher(v) for v in _tuple(snapshot.launchers, MAX_LAUNCHERS)]
            require(len({v['key'] for v in launchers}) == len(launchers), 'LAUNCHER_DUPLICATE_REFERENCE')
            blockers = set(BASE_BLOCKERS)
            if self._storage_blocked: blockers.add('STORAGE_REQUIREMENTS_UNRESOLVED')
            if any(v['state'] != 'observed' for v in coverage): blockers.add('CHANNEL_COVERAGE_UNRESOLVED')
            for row in launchers: blockers.update(row['issues'])
            data = {'version': 1, 'target': self._target, 'storage_manifest_sha256': self._storage,
                    'coverage': sorted(coverage, key=lambda x: x['channel']),
                    'launchers': sorted(launchers, key=lambda x: x['key']), 'blockers': sorted(blockers)}
            comparison = _sha(_json(data))
            if previous is not None:
                require(type(previous) is LauncherRequirements and type(previous._canonical) is bytes
                        and len(previous._canonical) <= MAX_BYTES, 'LAUNCHER_PREVIOUS_REJECTED')
                prior = previous.private_manifest()
                require(type(prior['observed_at']) is int and prior['observed_at'] <= snapshot.observed_at,
                        'LAUNCHER_OBSERVATION_REPLAYED')
                prior = dict(prior); prior.pop('observed_at')
                require(_sha(_json(prior)) == comparison, 'LAUNCHER_OBSERVATIONS_CHANGED')
            data['observed_at'] = snapshot.observed_at
            return LauncherRequirements(_json(data))
        except LauncherInventoryError: raise
        except Exception: raise LauncherInventoryError('LAUNCHER_INSPECTION_UNAVAILABLE') from None
