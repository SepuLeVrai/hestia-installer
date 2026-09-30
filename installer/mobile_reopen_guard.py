"""Durable refusal of activity while a future mobile reopen plan is composed.

This is a private journal primitive, not an admission or a service controller.
It binds a caller's plan digest to the existing maintenance lease and completed
Gateway release. It cannot consume either blocker. A future coordinator must
validate the plan, stopped services, archives, live files and SQL separately.
"""
from functools import wraps
import os
import re

from installer import backup_files as files, gateway_state_release as release
from installer.maintenance import MaintenanceLease
from installer.model import canonical_bytes, strict_json_loads

MARKER = 'mobile-reopen.attempt'
MAX_BYTES = 8192
SHA256 = re.compile(r'[a-f0-9]{64}\Z')


class ReopenGuardError(RuntimeError):
    """Closed non-secret failure; the maintenance gate remains closed."""


def require(value, code):
    if not value:
        raise ReopenGuardError(code)


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ReopenGuardError:
            raise
        except Exception:
            raise ReopenGuardError('MOBILE_REOPEN_GUARD_UNAVAILABLE') from None
    return invoke


def _inputs(lease, plan_sha256, confirmed):
    require(confirmed is True, 'MOBILE_REOPEN_CONSENT_REQUIRED')
    require(type(lease) is MaintenanceLease, 'MOBILE_REOPEN_LEASE_REQUIRED')
    require(type(plan_sha256) is str and SHA256.fullmatch(plan_sha256) is not None,
            'MOBILE_REOPEN_PLAN_REJECTED')
    require(os.geteuid() == 0, 'MOBILE_REOPEN_ROOT_REQUIRED')
    lease.assert_held()


def _binding(lease, plan_sha256):
    gate = lease._directory
    # Only an already completed 6B7a release can be bound. This primitive does
    # not finish a release or treat a missing source marker as a success.
    for marker in (release.g.MARKER, release.RELEASE):
        release.fs._absent(gate, marker)
    raw = files._read(gate, release.RELEASED, release.g.MAX_JOURNAL * 2)
    value = strict_json_loads(raw)
    require(type(value) is dict and type(value.get('intent')) is dict
            and raw == release._receipt(canonical_bytes(value['intent'])),
            'MOBILE_REOPEN_RELEASE_REJECTED')
    intent = value['intent']
    require(set(intent) == {'version', 'fence', 'snapshot_sha256', 'composed_sha256', 'web_verified_sha256'}
            and type(intent['version']) is int and intent['version'] == 1,
            'MOBILE_REOPEN_RELEASE_REJECTED')
    fence = intent['fence']
    require(type(fence) is dict and fence.get('instance') == lease.scope.instance
            and fence.get('lease_id') == lease.lease_id, 'MOBILE_REOPEN_RELEASE_REJECTED')
    digests = {'gateway_release_sha256': release.f._sha(raw),
               'gateway_snapshot_sha256': intent['snapshot_sha256'],
               'composed_backup_sha256': intent['composed_sha256'],
               'web_backup_sha256': intent['web_verified_sha256'],
               'service_profile_sha256': fence.get('barrier_sha256')}
    require(all(type(v) is str and SHA256.fullmatch(v) is not None for v in digests.values()),
            'MOBILE_REOPEN_RELEASE_REJECTED')
    return canonical_bytes({'version': 1, 'state': 'MOBILE_REOPEN_BLOCKED',
        'instance': lease.scope.instance, 'lease_id': lease.lease_id,
        'plan_sha256': plan_sha256, 'bindings': digests,
        'activity_resumed': False, 'admission_verified': False, 'services_started': False})


class ReopenGuard:
    def __init__(self, lease, plan_sha256, raw):
        self._lease, self._plan, self._raw = lease, plan_sha256, raw
        self._pid = os.getpid()

    def __repr__(self):
        return '<ReopenGuard private activity refusal>'

    def __reduce__(self):
        raise TypeError('Reopen guards cannot be serialized')

    def report(self):
        """Historical non-secret evidence; no filesystem, SQL or service probe."""
        return strict_json_loads(self._raw)

    @closed
    def assert_held(self):
        require(os.getpid() == self._pid, 'MOBILE_REOPEN_LEASE_REQUIRED')
        self._lease.assert_held()
        require(files._read(self._lease._directory, MARKER, MAX_BYTES) == self._raw,
                'MOBILE_REOPEN_INTENT_CHANGED')
        require(_binding(self._lease, self._plan) == self._raw, 'MOBILE_REOPEN_SOURCE_CHANGED')
        self._lease.assert_held()


@closed
def begin(lease, *, plan_sha256, confirmed):
    """Create once under the existing exclusive lease; never overwrite a guard."""
    _inputs(lease, plan_sha256, confirmed)
    release.fs._absent(lease._directory, MARKER)
    raw = _binding(lease, plan_sha256)
    lease.assert_held()
    files._new(lease._directory, MARKER, raw)
    guard = ReopenGuard(lease, plan_sha256, raw)
    guard.assert_held()
    return guard


@closed
def recover(lease, *, plan_sha256, confirmed):
    """Reconcile only the complete exact intent; absent/partial stays manual.

    Never rewrite a marker, consume a release, unseal data, or repeat a start.
    The caller explicitly reacquires the same MaintenanceLease after a crash.
    """
    _inputs(lease, plan_sha256, confirmed)
    raw = files._read(lease._directory, MARKER, MAX_BYTES)
    require(raw == _binding(lease, plan_sha256), 'MOBILE_REOPEN_INTENT_CHANGED')
    guard = ReopenGuard(lease, plan_sha256, raw)
    guard.assert_held()
    return guard
