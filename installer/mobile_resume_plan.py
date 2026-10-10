"""Durable preparation for a future mobile activity-resume coordinator.

Copies the exact blockers under a live SQL/data admission. No blocker removal,
maintenance resume or service command exists here. Persisted evidence is historical;
every preparation/check requires a current, process-bound DataAdmissionWindow.
"""
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import os
import re

from installer import mobile_data_admission as m
from installer.model import canonical_bytes, strict_json_loads

e, fs, files, f = m.e, m.fs, m.files, m.f
MAX_PLAN = 32768
MAX_OBSERVATIONS = 64
COPIES = ('maintenance-original.json', 'mobile-guard-original.json',
          'gateway-released-original.json', 'http-drain-original.json')
OBSERVATION = re.compile(r'admission-[a-f0-9]{32}[.]json\Z')
POLICY = 'MOBILE_RESUME_PREPARATION_ONLY_V1'


class ResumePlanError(RuntimeError): pass


def require(ok, code='MOBILE_RESUME_PLAN_CHANGED'):
    if not ok: raise ResumePlanError(code)


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try: return operation(*args, **kwargs)
        except ResumePlanError: raise
        except Exception: raise ResumePlanError('MOBILE_RESUME_PLAN_UNAVAILABLE') from None
    return invoke


def _inputs(window, confirmed):
    require(confirmed is True, 'MOBILE_RESUME_CONSENT_REQUIRED')
    require(type(window) is m.DataAdmissionWindow, 'MOBILE_RESUME_LIVE_ADMISSION_REQUIRED')
    window.assert_held()


def _digest(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value), 'MOBILE_RESUME_PROFILE_REJECTED')
    return value


def _services(profile, instance):
    """Bound order only, not start intents or a claim of running invocations."""
    require(type(instance) is str and re.fullmatch('[a-f0-9]{32}', instance), 'MOBILE_RESUME_PROFILE_REJECTED')
    require(type(profile) is dict, 'MOBILE_RESUME_PROFILE_REJECTED')
    if 'public_ingress' in profile:
        from installer.gateway_public_admission import current, require_profile
        public = current()
        require(public is not None, 'MOBILE_RESUME_PROFILE_REJECTED')
        require_profile(public.http, profile, closed=False)
    rows = profile.get('units')
    require(type(rows) is list and len(rows) == 3 and all(type(row) is dict
        and set(row) == {'role', 'fragment_sha256'} for row in rows), 'MOBILE_RESUME_PROFILE_REJECTED')
    require([row['role'] for row in rows] == ['apache', 'php', 'session-cleaner'], 'MOBILE_RESUME_PROFILE_REJECTED')
    units = {row['role']: _digest(row['fragment_sha256']) for row in rows}
    prefix = 'hestia-' + instance + '-'; result = []
    for role in ('php', 'apache'):
        result.append({'role': role, 'unit': prefix + role + '.service', 'fragment_sha256': units[role]})
    for role, field, policy in (
        ('foundation', 'foundation', 'GATED_FOUNDATION_STOP_ONLY_V1'),
        ('gateway', 'gateway_service', 'GATED_GATEWAY_STOP_BEFORE_FOUNDATION_V1')):
        row = profile.get(field)
        require(type(row) is dict and row.get('unit') == prefix + role + '.service'
            and row.get('policy') == policy, 'MOBILE_RESUME_PROFILE_REJECTED')
        _digest(row.get('manifest_sha256'))
        result.append({'role': role, 'unit': row['unit'], 'binding_sha256': f._sha(canonical_bytes(row))})
    require(profile.get('policy') == 'PROVISIONED_HTTP_AND_CLEANER_STOP_ONLY_V1',
            'MOBILE_RESUME_PROFILE_REJECTED')
    result.append({'role': 'timer', 'unit': prefix + 'session-cleaner.timer',
        'fragment_sha256': _digest(profile.get('timer_sha256')),
        'cleaner_fragment_sha256': units['session-cleaner'],
        'cleaner_plan_sha256': _digest(profile.get('cleaner_plan_sha256'))})
    return result


def _capture(window):
    control = window._control
    require(type(control) is m._ParentFiles and type(control.plan) is m.d.DataReleasePlan,
            'MOBILE_RESUME_LIVE_ADMISSION_REQUIRED')
    plan, lease = control.plan, control.lease
    identity = e._inputs(lease, control.backups, True)
    m._state(plan, completed=True)
    require(control.barrier._lease is lease and control.plan.lease is lease,
            'MOBILE_RESUME_LIVE_ADMISSION_REQUIRED')
    profile = strict_json_loads(control.barrier._profile)
    order = _services(profile, lease.scope.instance)
    definitions = (
        ('maintenance.attempt', lease.scope.web_gid, 0o640, 2048),
        (e.guard.MARKER, 0, 0o600, e.guard.MAX_BYTES),
        (m.r.gateway.RELEASED, 0, 0o600, m.r.gateway.g.MAX_JOURNAL * 2),
        ('http-drain-' + lease.lease_id + '.attempt', lease.scope.web_gid, 0o640, 32768))
    originals, metadata = {}, {}
    for name, (source, gid, mode, maximum) in zip(COPIES, definitions):
        raw = f._read(lease._directory, source, gid, mode=mode, limit=maximum)
        originals[name] = raw
        metadata[name] = {'source': source, 'uid': 0, 'gid': gid, 'mode': mode,
                          'bytes': len(raw), 'sha256': f._sha(raw)}
    require(originals[COPIES[0]] == lease._raw and originals[COPIES[3]] == control.barrier._profile)
    require(f._sha(originals[COPIES[1]]) == plan.value['parents']['mobile_guard']
        and f._sha(originals[COPIES[2]]) == plan.value['parents']['gateway_release'])
    value = {'version': 1, 'policy': POLICY, 'instance': lease.scope.instance, 'lease_id': lease.lease_id,
        'backup_root': str(control.backups), 'backup_identity': identity,
        'data_plan_sha256': plan.plan_sha256, 'external_plan_sha256': plan.value['external_plan_sha256'],
        'file_plan_sha256': control.external.value['source_plan_sha256'],
        'parents': deepcopy(plan.value['parents']), 'originals': metadata,
        'service_profile_sha256': f._sha(control.barrier._profile), 'start_order': order,
        'future_blocker_order': [e.guard.MARKER, m.r.gateway.RELEASED],
        'maintenance_last': True, 'automatic_start_retry_allowed': False}
    report = deepcopy(window._report); slot = window._slot
    require(re.fullmatch('data-admission-[a-f0-9]{32}', slot.name) is not None
        and slot.parent == control.backups and report.get('observation_id') == slot.name
        and report.get('data_plan_sha256') == plan.plan_sha256
        and report.get('data_access_reopened') is True
        and report.get('valid_after_window_close') is False, 'MOBILE_RESUME_OBSERVATION_REJECTED')
    with fs._directory(slot) as fd:
        files._private(fd, directory=True)
        require(files._read(fd, 'observed.json', MAX_PLAN) == m.p._json(report),
                'MOBILE_RESUME_OBSERVATION_CHANGED')
    # The native window has verified the export and archives. Copy its exact
    # observation, without treating that file as a replacement for the window.
    raw = canonical_bytes(value)
    require(len(raw) <= MAX_PLAN, 'MOBILE_RESUME_PLAN_LIMIT')
    return control.backups / ('mobile-resume-' + lease.lease_id), raw, originals, report


class ResumePlan:
    def __init__(self, root, raw):
        self.root, self._raw, self._pid = root, raw, os.getpid()
        self.value = strict_json_loads(raw); self.plan_sha256 = f._sha(raw)

    def __repr__(self): return '<ResumePlan private preparation; no activation authority>'
    def __reduce__(self): raise TypeError('Resume plans cannot be serialized')

    @contextmanager
    def _slot(self):
        require(self._pid == os.getpid(), 'MOBILE_RESUME_PROCESS_CHANGED')
        with fs._directory(self.root) as fd:
            files._private(fd, directory=True)
            require(files._read(fd, 'plan.json', MAX_PLAN) == self._raw)
            names = set(os.listdir(fd))
            observations = names - {'plan.json', 'prepared.json', *COPIES}
            require(len(observations) <= MAX_OBSERVATIONS and all(OBSERVATION.fullmatch(n) for n in observations),
                    'MOBILE_RESUME_FOREIGN_RECORD')
            yield fd

    def _prepared(self):
        return canonical_bytes({'version': 1, 'plan_sha256': self.plan_sha256,
            'state': 'RESUME_PLAN_PREPARED_HISTORICAL', 'current_admission': False,
            'blockers_consumed': False, 'maintenance_released': False, 'services_started': False})

    def _observation(self, report):
        return canonical_bytes({'version': 1, 'plan_sha256': self.plan_sha256,
                                'historical_observation_only': True, 'data_admission': report})

    def _match(self, capture):
        root, raw, _, _ = capture
        require(self._pid == os.getpid(), 'MOBILE_RESUME_PROCESS_CHANGED')
        require(root == self.root and raw == self._raw, 'MOBILE_RESUME_PLAN_CHANGED')
        require(canonical_bytes(self.value) == self._raw and self.plan_sha256 == f._sha(self._raw),
                'MOBILE_RESUME_PLAN_CHANGED')

    def _records(self, fd, originals, *, complete):
        names = set(os.listdir(fd))
        prepared = m.r._optional(fd, 'prepared.json', MAX_PLAN)
        if prepared is not None:
            require(prepared == self._prepared())
            require(any(OBSERVATION.fullmatch(n) for n in names), 'MOBILE_RESUME_PREPARATION_INCOMPLETE')
        for name in COPIES:
            if name in names: require(files._read(fd, name, len(originals[name])) == originals[name])
            else: require(not complete and prepared is None, 'MOBILE_RESUME_PREPARATION_INCOMPLETE')
        if complete: require(prepared is not None, 'MOBILE_RESUME_PREPARATION_INCOMPLETE')
        for name in names:
            if not OBSERVATION.fullmatch(name): continue
            raw = files._read(fd, name, MAX_PLAN); value = strict_json_loads(raw)
            require(type(value) is dict and set(value) == {'version', 'plan_sha256', 'historical_observation_only', 'data_admission'}
                and raw == self._observation(value['data_admission']), 'MOBILE_RESUME_OBSERVATION_CHANGED')
            observation = value['data_admission']
            require(type(observation) is dict and observation.get('observation_id') == 'data-' + name[:-5]
                and observation.get('data_plan_sha256') == self.value['data_plan_sha256']
                and observation.get('data_access_reopened') is True
                and observation.get('valid_after_window_close') is False, 'MOBILE_RESUME_OBSERVATION_CHANGED')
            with fs._directory(self.root.parent / observation['observation_id']) as source:
                files._private(source, directory=True)
                require(files._read(source, 'observed.json', MAX_PLAN) == m.p._json(observation),
                        'MOBILE_RESUME_OBSERVATION_CHANGED')

    def _complete(self, window, capture):
        self._match(capture); _, _, originals, report = capture
        name = 'admission-' + report['observation_id'].removeprefix('data-admission-') + '.json'
        with self._slot() as fd:
            self._records(fd, originals, complete=False)
            for item in COPIES:
                if m.r._optional(fd, item, len(originals[item])) is None: files._new(fd, item, originals[item])
            old = m.r._optional(fd, name, MAX_PLAN); raw = self._observation(report)
            if old is None:
                require(sum(bool(OBSERVATION.fullmatch(n)) for n in os.listdir(fd)) < MAX_OBSERVATIONS,
                        'MOBILE_RESUME_OBSERVATIONS_LIMIT')
                files._new(fd, name, raw)
            else: require(old == raw, 'MOBILE_RESUME_OBSERVATION_CHANGED')
            if m.r._optional(fd, 'prepared.json', MAX_PLAN) is None: files._new(fd, 'prepared.json', self._prepared())
            self._records(fd, originals, complete=True)
        window.assert_held(); self._match(_capture(window))
        return self._report()

    def _report(self):
        return {**strict_json_loads(self._prepared()), 'phase6_complete': False,
                'fresh_sql_admission_required_for_next_step': True, 'start_order': deepcopy(self.value['start_order'])}

    @closed
    def prepare(self, window, confirmation, *, confirmed):
        require(confirmed is True and confirmation == self.plan_sha256, 'MOBILE_RESUME_CONFIRMATION_REQUIRED')
        _inputs(window, confirmed)
        return self._complete(window, _capture(window))

    @closed
    def check(self, window, confirmation, *, confirmed):
        require(confirmed is True and confirmation == self.plan_sha256, 'MOBILE_RESUME_CONFIRMATION_REQUIRED')
        _inputs(window, confirmed); capture = _capture(window); self._match(capture)
        with self._slot() as fd: self._records(fd, capture[2], complete=True)
        window.assert_held(); self._match(_capture(window))
        return self._report()


@closed
def begin(window, *, confirmed):
    _inputs(window, confirmed); capture = _capture(window); root, raw, _, _ = capture
    with fs._directory(root.parent) as parent:
        files._private(parent, directory=True)
        try: os.stat(root.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            os.mkdir(root.name, 0o700, dir_fd=parent); os.fsync(parent)
            with fs._directory(root) as fd:
                files._private(fd, directory=True); files._new(fd, 'plan.json', raw)
    plan = ResumePlan(root, raw); plan._complete(window, capture)
    return plan


@closed
def recover(window, *, confirmed):
    """Read-only; missing copies remain incomplete until an explicit prepare."""
    _inputs(window, confirmed); capture = _capture(window); root, raw, originals, _ = capture
    plan = ResumePlan(root, raw)
    with plan._slot() as fd: plan._records(fd, originals, complete=False)
    window.assert_held(); plan._match(_capture(window))
    return plan
