"""Scoped historical HTTP binding over a freshly checked public successor.

This scope never substitutes old fragments for native systemd observations.
It only reconciles the drain profile retained in the source-bound archives.
No service command, marker removal or activation owner is produced here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import os

from installer import gateway_public_selection as selection
from installer import gateway_public_systemd as systemd
from installer.transaction import StateJournal

g = selection.g
_CURRENT = ContextVar('gateway_public_admission', default=None)


def overlay_binding(shared):
    profile = shared.web
    return {'path': str(shared.dropin), 'profile_sha256': profile.digest,
        'configuration_sha256': g.boot.f._sha(profile.apache_include()),
        'dropin_sha256': g.boot.f._sha(shared.apache_dropin()),
        'successor_profile_sha256': shared.digest}


def load(http, lease_id, profile):
    """Read the explicit selection, never infer it from installed bytes."""
    g.require(type(http) is g.boot.h.HttpRuntime and type(profile) is dict,
              g.ErrorCode.INVALID_DATA)
    reader = object.__new__(g.public.SharedPublic)
    reader.root = http.spec.root.parent / 'public/shared/private'
    value = reader._read('profile.json')
    g.require(value is not None, g.ErrorCode.DEPENDENCY_BLOCKED)
    shared = g.public.SharedPublic(value)
    g.require(shared.root == reader.root, g.ErrorCode.SOURCE_DRIFT)
    generation = selection.selected(shared)
    g.require(generation is not None, g.ErrorCode.DEPENDENCY_BLOCKED)
    return PublicAdmission(http, lease_id, profile, generation)


class PublicAdmission:
    def __init__(self, http, lease_id, profile, generation):
        g.require(type(http) is g.boot.h.HttpRuntime and type(profile) is dict and type(generation) is g.Generation
            and generation.http.spec == http.spec and generation.value['lease_id'] == lease_id,
            g.ErrorCode.INCOMPATIBLE_STATE)
        self.http, self.generation, self.lease_id = http, generation, lease_id
        self.pid = os.getpid()
        self.closed = None
        self.profile = deepcopy(profile)
        self.pointer = selection.pointer(generation.original.shared)
        g.require(type(self.pointer) is dict, g.ErrorCode.SOURCE_DRIFT)
        source_overlay = overlay_binding(generation.original.shared)
        if 'predecessor' in generation.value:
            from installer.gateway_public_ancestry import load
            previous = load(generation)
            source_overlay = {**overlay_binding(previous.readers()[1]),
                              'gateway_generation_sha256': previous.digest}
        g.require(self.pointer == selection.binding(generation, self.pointer['fragment_plan_sha256'])
            and self.profile.get('public_ingress') == source_overlay,
            g.ErrorCode.SOURCE_DRIFT)
        self.expected_overlay = {**overlay_binding(generation.readers()[1]),
                                 'gateway_generation_sha256': generation.digest}

    def __reduce__(self): raise TypeError('Public admissions cannot be serialized')

    def binding(self): return deepcopy(self.pointer)

    def check(self):
        g.require(self.pid == os.getpid(), g.ErrorCode.INCOMPATIBLE_STATE)
        g.require(selection.pointer(self.generation.original.shared) == self.pointer,
                  g.ErrorCode.SOURCE_DRIFT)
        self.generation.current_publication()
        self.generation.installed_fragments(self.pointer['fragment_plan_sha256'])

    def quiet(self):
        """Fresh loaded/inactive observations, including empty service cgroups."""
        self.check()
        shared = self.generation.readers()[1]
        for role in ('timer', 'renew', 'http', 'https'):
            unit = shared.web.unit(role)
            value = systemd.show(unit)
            systemd.loaded_commands(value, self.generation.units()[unit])
            g.require(value['NeedDaemonReload'] == 'no' and value['DropInPaths'] == ''
                and (value['ActiveState'], value['SubState']) == ('inactive', 'dead'),
                g.ErrorCode.MANUAL_ACTION_REQUIRED)
            if role == 'timer':
                g.require(value['Unit'] == shared.web.unit('renew'), g.ErrorCode.SOURCE_DRIFT)
            else:
                g.require(value['MainPID'] == value['ControlPID'] == '0'
                    and g.boot.h.drain._empty_cgroup(unit), g.ErrorCode.MANUAL_ACTION_REQUIRED)

    def activate(self, window, authority):
        from installer.mobile_activation_admission import ActivationWindow
        from installer import gateway_resume_authority as a
        g.require(type(window) is ActivationWindow and a.current(self.http) is authority
            and authority.public is self and current(self.http) is self
            and self.closed is True
            and window.record.native.http is self.http
            and window.record.lease_id == self.lease_id
            and authority.read('consumed.json') is not None, g.ErrorCode.INCOMPATIBLE_STATE)
        window.assert_held(); self.check()
        owner = {'generation_sha256': self.generation.digest,
                 'fragment_plan_sha256': self.pointer['fragment_plan_sha256'],
                 'admission_sha256': g.boot.f._sha(authority.raw)}
        with g.boot.fs._directory(self.generation.root) as fd:
            g.fragments.files._private(fd, directory=True)
            g.fragments._put(fd, 'activation-epoch.json', {'owner': owner,
                'epoch': g.mobile.MobileBootRuntime.epoch_identity()})
            g.fragments._put(fd, 'activated.json', owner)
        window.assert_held()

    @contextmanager
    def scoped(self, *, lease=None, authority=None, closed=True):
        g.require(_CURRENT.get() is None and (lease is None) != (authority is None),
                  g.ErrorCode.INCOMPATIBLE_STATE)
        g.require(type(closed) is bool and (closed or authority is not None), g.ErrorCode.INVALID_DATA)
        if lease is not None:
            g.require(type(lease) is g.MaintenanceLease and lease.lease_id == self.lease_id
                and lease.scope.directory == self.http.spec.maintenance_directory,
                g.ErrorCode.INCOMPATIBLE_STATE)
            lease.assert_held()
        else:
            from installer import gateway_resume_authority as a
            g.require(type(authority) is a.Authority and a.current(self.http) is authority
                and authority.public is self, g.ErrorCode.INCOMPATIBLE_STATE)
            if not closed:
                g.require(authority.read('consumed.json') is not None
                    and self.generation._read('activated.json') == {
                        'generation_sha256': self.generation.digest,
                        'fragment_plan_sha256': self.pointer['fragment_plan_sha256'],
                        'admission_sha256': g.boot.f._sha(authority.raw)}, g.ErrorCode.DEPENDENCY_BLOCKED)
        with StateJournal(self.generation.original.shared.root / 'effect-lock.json').locked(create=False):
            self.generation.configuration(); self.check()
            if closed: self.quiet()
            epoch = g.mobile.MobileBootRuntime.epoch_identity()
            self.closed = closed
            token = _CURRENT.set(self)
            try:
                yield self
                self.check()
                if closed: self.quiet()
                g.require(g.mobile.MobileBootRuntime.epoch_identity() == epoch, g.ErrorCode.SOURCE_DRIFT)
                if lease is not None: lease.assert_held()
            finally:
                self.closed = None
                _CURRENT.reset(token)


def current(http=None):
    value = _CURRENT.get()
    if value is not None:
        g.require(type(value) is PublicAdmission and value.pid == os.getpid()
            and (http is None or value.http is http), g.ErrorCode.INCOMPATIBLE_STATE)
    return value


def historical_overlay(http, observed):
    value = current(http)
    if value is None: return observed
    value.check()
    g.require(observed == value.expected_overlay, g.ErrorCode.SOURCE_DRIFT)
    return deepcopy(value.profile['public_ingress'])


def require_profile(http, profile, *, closed=True):
    """Public exceptions require this exact live coordinator and source drain."""
    value = current(http)
    g.require(value is not None and profile == value.profile, g.ErrorCode.INCOMPATIBLE_STATE)
    from installer import gateway_resume_authority as a
    authority = a.current(http)
    g.require(authority is not None and authority.public is value, g.ErrorCode.INCOMPATIBLE_STATE)
    g.require(type(closed) is bool and (not closed or value.closed is True), g.ErrorCode.INCOMPATIBLE_STATE)
    value.check()
    return value


def require_closed_paths(http, lease):
    """Keep private absence rules unless this exact live public lease is admitted."""
    value = current(http)
    if value is None:
        with g.boot.fs._directory(http.spec.root.parent) as fd:
            for name in ('boot', 'public'): g.boot.fs._absent(fd, name)
        return
    g.require(type(lease) is g.MaintenanceLease and lease.lease_id == value.lease_id
        and lease.scope.directory == http.spec.maintenance_directory, g.ErrorCode.INCOMPATIBLE_STATE)
    lease.assert_held()
    raw = g.boot.f._read(lease._directory, 'http-drain-' + lease.lease_id + '.attempt', lease.scope.web_gid)
    require_profile(http, g.fragments.strict_json_loads(raw))
    lease.assert_held()
