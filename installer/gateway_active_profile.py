"""Publish an explicit successor profile while maintenance and SQLite stay closed.

The staged enrollment and cutover evidence are immutable ancestors. A current
reader needs the complete publication AND a fresh native audit; a historical
service or boot profile cannot adopt this successor. Reopening is a separate
admission, and file-only rollback stops at the first publication intent.
"""
from functools import wraps
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace

from installer import gateway_transition_cutover as c
from installer.gateway_service_profile import GatewayServiceProfile
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.gateway_transition import assess
from installer.model import canonical_bytes

MARKER = 'gateway-active-profile.attempt'
INTENT = 'active-profile.intent.json'
ACTIVE = 'active-profile.json'
POLICY = 'GATEWAY_ACTIVE_PROFILE_ACTIVITY_CLOSED_V1'
fs, files, require, sha = c.fs, c.files, c.require, c.sha


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try: return operation(*args, **kwargs)
        except c.g.GatewayStateError: raise
        except Exception: raise c.g.GatewayStateError('GATEWAY_ACTIVE_PROFILE_UNAVAILABLE') from None
    return invoke


def _receipt(raw):
    return canonical_bytes({'version': 1, 'state': 'GATEWAY_ACTIVE_PROFILE_PUBLISHED_ACTIVITY_CLOSED',
        'intent_sha256': sha(raw), 'intent': c._json(raw), 'active_profile_changed': True,
        'historical_only': True, 'activity_resumed': False, 'services_started': False,
        'sqlite_restored': False, 'rollback_verified': False, 'boot_requalified': False,
        'restore_to_original_allowed': False, 'phase6_complete': False})


def _identity(value, binary_sha256):
    require(type(value) is dict and set(value) == {'device', 'inode', 'bytes', 'sha256'}
        and all(type(value[k]) is int and value[k] > 0 for k in ('device', 'inode', 'bytes'))
        and value['bytes'] <= c.stage.MAX_BINARY and value['sha256'] == binary_sha256,
        'GATEWAY_ACTIVE_BINARY_CHANGED')


def _record(runtime, raw):
    """Validate the closed publication grammar without observing native state."""
    value = c._json(raw); origin = value['cutover']; source = value['source_manifest']
    require(type(source) is dict and set(source) == {'binding', 'uid', 'gid'}
        and all(type(source[k]) is int and source[k] >= 0 for k in ('uid', 'gid')),
        'GATEWAY_ACTIVE_ENROLLMENT_CHANGED')
    profile = GatewayServiceProfile.from_binding(runtime.foundation, source['binding'])
    require(canonical_bytes(profile.binding()) == canonical_bytes(source['binding']),
            'GATEWAY_ACTIVE_ENROLLMENT_CHANGED')
    assessment = assess(profile, target_commit=runtime.profile.selected_release['commit'],
                        direction=value['direction']).report()
    account = SimpleNamespace(pw_uid=source['uid'], pw_gid=source['gid'])
    require(assessment['configuration_compatible'] and assessment['target'] == runtime.profile.binding(),
        'GATEWAY_ACTIVE_PROFILE_CHANGED')
    require(type(origin) is dict and type(origin.get('lease_id')) is str
        and re.fullmatch('[a-f0-9]{32}', origin['lease_id'])
        and all(type(origin.get(k)) is str and re.fullmatch('[a-f0-9]{64}', origin[k])
                for k in ('preparation_sha256', 'fence_sha256')),
        'GATEWAY_ACTIVE_CUTOVER_CHANGED')
    require(canonical_bytes(origin) == canonical_bytes({'version': 1, 'policy': c.POLICY,
        'instance': runtime.web.spec.instance, 'lease_id': origin['lease_id'],
        'source_manifest': source, 'preparation_sha256': origin['preparation_sha256'],
        'fence_sha256': origin['fence_sha256'], 'original_binary': origin['original_binary']}),
        'GATEWAY_ACTIVE_CUTOVER_CHANGED')
    _identity(origin['original_binary'], profile.selected_release['binary_sha256'])
    arm = value['target_armed']; owner = {'intent_sha256': sha(canonical_bytes(origin))}
    _identity(arm['replacement'], runtime.profile.selected_release['binary_sha256'])
    require(canonical_bytes(arm) == canonical_bytes({'owner': owner, 'role': 'target',
        'previous': origin['original_binary'], 'replacement': arm['replacement']})
        and arm['replacement']['device'] == origin['original_binary']['device']
        and arm['replacement']['inode'] != origin['original_binary']['inode'],
        'GATEWAY_ACTIVE_CUTOVER_CHANGED')
    done = {'owner': owner, 'armed_sha256': sha(canonical_bytes(arm)), 'role': 'target'}
    state = value['state']
    require(type(state) is dict and set(state) == {'gateway.db', 'gateway.lock'}
        and all(type(v) is dict and set(v) == {'device', 'inode'}
            and all(type(n) is int and n > 0 for n in v.values()) for v in state.values()),
        'GATEWAY_ACTIVE_STATE_CHANGED')
    expected = {'version': 1, 'policy': POLICY, 'direction': value['direction'],
        'cutover': origin, 'source_manifest': source, 'target_manifest': runtime.manifest(account),
        'target_armed': arm, 'target_done': done, 'state': state}
    require(raw == canonical_bytes(expected), 'GATEWAY_ACTIVE_PROFILE_CHANGED')
    return source


def _records(runtime, raw):
    value = c._json(raw); origin = value['cutover']
    with fs._directory(runtime.root) as fd:
        require(files._read(fd, 'staged.json', c.MAX_RECORD) == canonical_bytes(value['source_manifest']),
                'GATEWAY_ACTIVE_ENROLLMENT_CHANGED')
    with fs._directory(runtime.root / 'control' / ('cutover-' + origin['lease_id'])) as fd:
        files._private(fd, directory=True)
        require(set(os.listdir(fd)) == {'target.armed.json', 'target.done.json'}
            and files._read(fd, 'target.armed.json', c.MAX_RECORD) == canonical_bytes(value['target_armed'])
            and files._read(fd, 'target.done.json', c.MAX_RECORD) == canonical_bytes(value['target_done']),
            'GATEWAY_ACTIVE_CUTOVER_CHANGED')


def _validate(runtime, raw):
    """Reobserve native identities in addition to the immutable record chain."""
    source = _record(runtime, raw); value = c._json(raw)
    account = runtime.account()
    require((source['uid'], source['gid']) == (account.pw_uid, account.pw_gid),
            'GATEWAY_ACTIVE_ENROLLMENT_CHANGED')
    require(value['state'] == runtime.state_binding(), 'GATEWAY_ACTIVE_STATE_CHANGED')
    _records(runtime, raw)
    with fs._directory(runtime.root) as fd:
        info = os.stat(runtime.profile.binary.name, dir_fd=fd, follow_symlinks=False)
        require({k: v for k, v in value['target_armed']['replacement'].items() if k != 'sha256'} ==
            {'device': info.st_dev, 'inode': info.st_ino, 'bytes': info.st_size},
            'GATEWAY_ACTIVE_BINARY_CHANGED')
    return source


@closed
def verify(runtime):
    """Reobserve selection on every inspect; cached objects cannot outlive drift."""
    raw = runtime._active_profile
    with fs._directory(runtime.root / 'control') as fd:
        files._private(fd, directory=True)
        require(files._read(fd, INTENT, c.MAX_RECORD) == raw
            and files._read(fd, ACTIVE, c.MAX_RECORD * 2) == _receipt(raw),
            'GATEWAY_ACTIVE_PUBLICATION_CHANGED')
    require(_validate(runtime, raw) == runtime._enrolled_manifest, 'GATEWAY_ACTIVE_ENROLLMENT_CHANGED')


@closed
def selected(foundation, enrollment):
    """Return an explicitly published runtime, or None for original enrollment."""
    original = GatewayServiceRuntime.from_binding(foundation, enrollment['binding'])
    with fs._directory(original.root / 'control') as fd:
        files._private(fd, directory=True)
        raw = c._optional(fd, INTENT)
        try: completed = files._read(fd, ACTIVE, c.MAX_RECORD * 2)
        except FileNotFoundError: completed = None
    if raw is None and completed is None: return None
    require(raw is not None and completed == _receipt(raw), 'GATEWAY_ACTIVE_PUBLICATION_INCOMPLETE')
    value = c._json(raw)
    require(canonical_bytes(value['source_manifest']) == canonical_bytes(enrollment),
            'GATEWAY_ACTIVE_ENROLLMENT_CHANGED')
    runtime = GatewayServiceRuntime.from_binding(foundation, value['target_manifest']['binding'])
    runtime._active_profile, runtime._enrolled_manifest = raw, enrollment
    verify(runtime)
    return runtime


def _candidate(control):
    role, arm, done, _ = control.observe()
    require(role == 'target' and done is not None, 'GATEWAY_ACTIVE_COMPLETED_TARGET_REQUIRED')
    source = control.runtime.manifest(control.runtime.account())
    assessment = control.prepared['intent']['assessment']
    return canonical_bytes({'version': 1, 'policy': POLICY, 'direction': assessment['direction'],
        'cutover': control.value, 'source_manifest': source,
        'target_manifest': {**source, 'binding': assessment['target']},
        'target_armed': arm, 'target_done': c._json(done), 'state': control.runtime.state_binding()})


def _finish(control, fence, action, cancel):
    expected = control.verify_backup(fence, cancel); fence.assert_held()
    raw = _candidate(control); require(len(raw) <= c.MAX_RECORD, 'GATEWAY_ACTIVE_LIMIT')
    lease = control.lease
    with fs._directory(control.runtime.root / 'control') as fd:
        files._private(fd, directory=True)
        marker, intent, completed = (c._optional(lease._directory, MARKER), c._optional(fd, INTENT),
                                    c.stage._optional(fd, ACTIVE, c.MAX_RECORD * 2))
        if action == 'apply':
            require(marker is None and intent is None and completed is None,
                    'GATEWAY_ACTIVE_EXPLICIT_RECOVERY_REQUIRED')
            c.b._cancel(cancel, time.monotonic() + 60)
            files._new(lease._directory, MARKER, raw); marker = raw
        require(marker == raw, 'GATEWAY_ACTIVE_INTENT_REQUIRED')
        require(intent is None or intent == raw, 'GATEWAY_ACTIVE_PUBLICATION_CHANGED')
        require(completed is None or intent == raw and completed == _receipt(raw),
                'GATEWAY_ACTIVE_PUBLICATION_CHANGED')
        if action == 'check':
            require(intent == raw and completed == _receipt(raw), 'GATEWAY_ACTIVE_PUBLICATION_INCOMPLETE')
        else:
            if intent is None: files._new(fd, INTENT, raw)
            fence.assert_held(); c.r._sources(fence.opened, expected, cancel=cancel)
            require(_candidate(control) == raw, 'GATEWAY_ACTIVE_CUTOVER_CHANGED')
            if completed is None: files._new(fd, ACTIVE, _receipt(raw))
    fence.assert_held(); c.r._sources(fence.opened, expected, cancel=cancel)
    return c._json(_receipt(raw))


@closed
def publish(runtime, backups, lease_id, *, source_package, target_package, target_commit,
            direction, action, confirmed, cancel=None):
    require(confirmed is True, 'GATEWAY_ACTIVE_CONSENT_REQUIRED')
    require(type(runtime) is GatewayServiceRuntime and not hasattr(runtime, '_active_profile')
        and isinstance(backups, Path) and backups.is_absolute()
        and type(lease_id) is str and re.fullmatch('[a-f0-9]{32}', lease_id)
        and action in ('apply', 'resume', 'check'), 'GATEWAY_ACTIVE_INPUT_REJECTED')
    require(os.getuid() == os.geteuid() == 0, 'GATEWAY_ACTIVE_ROOT_REQUIRED')
    account, _, _, _ = runtime.web._inspect_configuration(); scope = runtime.web._scope(account)
    with scope.recover(lease_id, confirmed=True) as lease:
        raw = c._optional(lease._directory, c.MARKER)
        require(raw is not None, 'GATEWAY_ACTIVE_CUTOVER_REQUIRED')
        prepared, binaries = c._preparation(runtime, backups, lease_id,
            source_package, target_package, target_commit, direction)
        control = c._Cutover(runtime, lease, backups, prepared, binaries, raw)
        with c.g.recover(runtime, c._barrier(control), confirmed=True) as fence:
            return _finish(control, fence, action, cancel)
