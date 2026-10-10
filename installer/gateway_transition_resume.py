"""Explicit preparation and fresh local activation of a published successor.

This first successor admission is restricted to the existing pre-public MAIN
profile. An already enrolled public/boot profile remains a separate handoff.
"""
import os
from contextlib import ExitStack
from pathlib import Path
import re

from installer import gateway_resume_authority as h
from installer import gateway_service_drain as gd
from installer.model import canonical_bytes

a, c, fs, files, require, sha = h.a, h.c, h.fs, h.files, h.require, h.sha


@a.closed
def prepare(runtime, backups, lease_id, *, source_package, target_package, target_commit,
            direction, action, confirmed, cancel=None):
    require(confirmed is True, 'GATEWAY_RESUME_CONSENT_REQUIRED')
    require(type(runtime) is a.GatewayServiceRuntime and not hasattr(runtime, '_active_profile')
        and isinstance(backups, Path) and backups.is_absolute()
        and type(lease_id) is str and re.fullmatch('[a-f0-9]{32}', lease_id)
        and action in ('apply', 'resume', 'check'), 'GATEWAY_RESUME_INPUT_REJECTED')
    require(os.getuid() == os.geteuid() == 0, 'GATEWAY_RESUME_ROOT_REQUIRED')
    account, _, _, _ = runtime.web._inspect_configuration(); scope = runtime.web._scope(account)
    selected = gd.attached(runtime.web, runtime.foundation)
    require(selected is not None and hasattr(selected, '_active_profile'), 'GATEWAY_RESUME_PUBLICATION_REQUIRED')
    require(selected.profile.dev is None and selected.profile.push is None, 'GATEWAY_RESUME_MAIN_PROFILE_REQUIRED')
    # Do not re-audit immutable SQLite after it has legitimately been unsealed.
    root = runtime.root / 'control' / ('resume-' + lease_id)
    try:
        with fs._directory(root) as fd: ready = c._optional(fd, 'ready.json')
    except FileNotFoundError: ready = None
    if ready is not None:
        require(action != 'apply', 'GATEWAY_RESUME_EXPLICIT_RECOVERY_REQUIRED')
        authority = h.Authority.load(selected, backups, lease_id); authority.markers()
        return {'state': 'GATEWAY_SUCCESSOR_ADMISSION_PREPARED', **authority.binding(),
                'historical_only': True, 'services_started': False, 'phase6_complete': False}
    require(action != 'check', 'GATEWAY_RESUME_NOT_READY')
    with scope.recover(lease_id, confirmed=True) as lease, ExitStack() as stack:
        raw = c._optional(lease._directory, c.MARKER)
        require(raw is not None, 'GATEWAY_RESUME_CUTOVER_REQUIRED')
        profile = c.hd.h.f._read(lease._directory, 'http-drain-' + lease_id + '.attempt', scope.web_gid)
        public = None
        if 'public_ingress' in c._json(profile):
            from installer import gateway_public_admission
            public = gateway_public_admission.load(selected.web, lease_id, c._json(profile))
            stack.enter_context(public.scoped(lease=lease))
        else:
            with fs._directory(runtime.web.spec.root.parent) as fd:
                fs._absent(fd, 'boot'); fs._absent(fd, 'public')
        prepared, binaries = c._preparation(runtime, backups, lease_id,
            source_package, target_package, target_commit, direction)
        control = c._Cutover(runtime, lease, backups, prepared, binaries, raw)
        with c.g.recover(runtime, c._barrier(control), confirmed=True) as fence:
            expected = control.verify_backup(fence, cancel); fence.assert_held(); selected.stopped()
            require(c._optional(lease._directory, a.MARKER) == selected._active_profile,
                    'GATEWAY_RESUME_PUBLICATION_CHANGED')
            profile = c.hd.h.f._read(lease._directory, 'http-drain-' + lease_id + '.attempt', scope.web_gid)
            plan = h.Authority.plan(selected, backups, lease_id, profile, public=public)
            marker = canonical_bytes(h.binding_for(plan)); old = c._optional(lease._directory, h.MARKER)
            if action == 'apply':
                require(old is None and not root.exists(), 'GATEWAY_RESUME_EXPLICIT_RECOVERY_REQUIRED')
                c.b._cancel(cancel, c.time.monotonic() + 60)
                files._new(lease._directory, h.MARKER, marker)
            else: require(old == marker, 'GATEWAY_RESUME_MARKER_CHANGED')
            with fs._directory(root.parent) as fd:
                files._private(fd, directory=True)
                try: os.mkdir(root.name, 0o700, dir_fd=fd); os.fsync(fd)
                except FileExistsError: pass
            with fs._directory(root) as fd:
                files._private(fd, directory=True)
                require(set(os.listdir(fd)) <= {'plan.json'}, 'GATEWAY_RESUME_FOREIGN_RECORD')
                old = c.stage._optional(fd, 'plan.json', c.MAX_RECORD * 2)
                if old is None: files._new(fd, 'plan.json', plan)
                else: require(old == plan, 'GATEWAY_RESUME_PLAN_CHANGED')
                fence.assert_held(); c.r._sources(fence.opened, expected, cancel=cancel)
                files._new(fd, 'ready.json', marker)
            authority = h.Authority.load(selected, backups, lease_id); authority.markers()
    return {'state': 'GATEWAY_SUCCESSOR_ADMISSION_PREPARED', **authority.binding(),
            'historical_only': True, 'services_started': False, 'phase6_complete': False}


@a.closed
def execute(http, scope, lease_id, backups, worker, source, payload, credentials, *,
            confirmation, action, confirmed, allow_global_read_lock, cancel=None):
    from installer import mobile_preparation_runtime as p, mobile_activation_admission as n
    require(confirmed is True and allow_global_read_lock is True, 'GATEWAY_RESUME_CONSENT_REQUIRED')
    require(action in ('apply', 'resume', 'check') and type(confirmation) is str,
            'GATEWAY_RESUME_ACTION_REJECTED')
    preparation = p.NativePreparation(http, scope, lease_id, backups, worker, source, payload, credentials)
    runtime = h.selected_for_admission(http); authority = h.Authority.load(runtime, backups, lease_id)
    require(confirmation == sha(authority.raw), 'GATEWAY_RESUME_CONFIRMATION_REQUIRED')
    require(cancel is None or not cancel.is_set(), 'GATEWAY_RESUME_INTERRUPTED')
    with authority.admitted():
        pending = False
        for stage in h.STAGES:
            name = stage + '.done.json'; raw = authority.read(name)
            if raw is not None:
                value = c._json(raw)
                require(not pending and set(value) == {'owner', 'stage', 'result_sha256'}
                    and value['owner'] == authority.binding() and value['stage'] == stage
                    and type(value['result_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['result_sha256']),
                    'GATEWAY_RESUME_STAGE_CHANGED')
                continue
            pending = True
            require(action != 'check', 'GATEWAY_RESUME_INCOMPLETE')
            require(cancel is None or not cancel.is_set(), 'GATEWAY_RESUME_INTERRUPTED')
            result = preparation.execute(stage)
            authority.save(name, canonical_bytes({'owner': authority.binding(), 'stage': stage,
                                                  'result_sha256': sha(canonical_bytes(result))}))
        resume_raw = n.e._read_path(backups / ('mobile-resume-' + lease_id), 'plan.json', n.s.p.MAX_PLAN)
        started = backups / ('mobile-activation-' + lease_id) / 'armed.json'
        selected_action = 'check' if action == 'check' else 'resume' if started.exists() else 'apply'
        result = n.execute(http, scope, lease_id, backups, worker, source, payload, credentials,
            sha(resume_raw), action=selected_action, confirmed=True, allow_global_read_lock=True, cancel=cancel)
        authority.check(); require(authority.read('consumed.json') is not None, 'GATEWAY_RESUME_INCOMPLETE')
    result.update(gateway_successor=authority.binding(), target_commit=runtime.profile.selected_release['commit'])
    return result
