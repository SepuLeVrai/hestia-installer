"""Scoped successor admission over immutable, source-bound backup evidence.

The historical drain binding is usable only inside an explicit coordinator.
The selected runtime and every native binary inspection remain target-bound.
The three transition blockers survive until the existing final SQL window has
armed a target-bound activation record. No historical receipt starts a unit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os

from installer import gateway_active_profile as a
from installer.model import canonical_bytes

c, fs, files, require, sha = a.c, a.fs, a.files, a.require, a.sha
MARKER = 'gateway-resume.attempt'
POLICY = 'GATEWAY_SUCCESSOR_FRESH_ADMISSION_V1'
MARKERS = (c.MARKER, a.MARKER, MARKER)
STAGES = ('gateway', 'files', 'external', 'data', 'resume', 'blockers')
_CURRENT = ContextVar('gateway_successor_admission', default=None)


def _identity(fd):
    info = os.fstat(fd)
    return {'device': info.st_dev, 'inode': info.st_ino}


def binding_for(raw):
    value = c._json(raw); publication = value['publication']
    return {'policy': POLICY, 'lease_id': value['lease_id'], 'plan_sha256': sha(raw),
        'publication_sha256': sha(canonical_bytes(publication)),
        'source_manifest_sha256': sha(canonical_bytes(publication['source_manifest'])),
        'target_manifest_sha256': sha(canonical_bytes(publication['target_manifest']))}


def selected_for_admission(http):
    """Reconstruct the selected profile before interrupted data chmod recovery.

    This reads enrollment/publication only. The qualified stage performs its
    real native audit after reclosure, under the original maintenance lease.
    """
    from installer.application_activation import Activation
    from installer.foundation_runtime import FoundationRuntime
    require(type(http) is c.hd.h.HttpRuntime, 'GATEWAY_RESUME_RUNTIME_CHANGED')
    with fs._directory(http.spec.root.parent / 'foundation') as fd:
        original = c._json(files._read(fd, 'staged.json', c.MAX_RECORD))
    with fs._directory(http.spec.root.parent / 'gateway-service') as fd:
        enrollment = c._json(files._read(fd, 'staged.json', c.MAX_RECORD))
    binding = enrollment['binding']
    foundation = FoundationRuntime.for_gateway(Activation(http, original['web_plan_sha256']),
        binding['main'], binding['gateway_identity'])
    runtime = a.selected(foundation, enrollment)
    require(runtime is not None and runtime.profile.dev is None and runtime.profile.push is None,
            'GATEWAY_RESUME_MAIN_PROFILE_REQUIRED')
    return runtime


def current(http=None):
    value = _CURRENT.get()
    if value is not None:
        require(type(value) is Authority and value.pid == os.getpid(), 'GATEWAY_RESUME_PROCESS_CHANGED')
        if http is not None:
            require(http is value.runtime.web, 'GATEWAY_RESUME_RUNTIME_CHANGED')
    return value


def historical_binding(runtime):
    value = current(runtime.web)
    if value is None: return None
    value.check_runtime(runtime)
    return value.source_binding()


def activation_binding(http):
    value = current(http)
    if value is None: return None
    value.check()
    return value.binding()


def release_admitted(runtime, barrier):
    value = current(runtime.web)
    if value is None:
        for name in MARKERS: fs._absent(barrier._lease._directory, name)
        return
    value.check_runtime(runtime); barrier._lease.assert_held()
    require(barrier._lease.lease_id == value.lease_id
        and barrier._lease.scope.directory == runtime.web.spec.maintenance_directory
        and barrier._profile == canonical_bytes(value.value['http_profile']), 'GATEWAY_RESUME_GATE_CHANGED')
    require(all(value.markers().values()), 'GATEWAY_RESUME_GUARDS_REQUIRED')


def augment_envelope(expected, record):
    value = current()
    if value is None: return expected
    for name, present in value.markers().items():
        key = 'configuration', 'maintenance/' + name
        require(key not in expected, 'GATEWAY_RESUME_ENVELOPE_CHANGED')
        if present: expected[key] = record(name, value.marker_bytes(name))
    return expected


def consume(window, record):
    value = current(record.native.http)
    if value is not None: value.consume(window, record)


class Authority:
    def __init__(self, runtime, backups, lease_id, raw):
        require(type(runtime) is a.GatewayServiceRuntime and hasattr(runtime, '_active_profile'),
                'GATEWAY_RESUME_TARGET_REQUIRED')
        self.runtime, self.backups, self.lease_id = runtime, backups, lease_id
        self.root = runtime.root / 'control' / ('resume-' + lease_id)
        self.raw, self.value, self.pid = raw, c._json(raw), os.getpid()
        self.check()

    def __reduce__(self): raise TypeError('Successor admissions cannot be serialized')

    @classmethod
    def load(cls, runtime, backups, lease_id):
        with fs._directory(runtime.root / 'control' / ('resume-' + lease_id)) as fd:
            files._private(fd, directory=True)
            raw = files._read(fd, 'plan.json', c.MAX_RECORD * 2)
        return cls(runtime, backups, lease_id, raw)

    @staticmethod
    def plan(runtime, backups, lease_id, profile):
        a.verify(runtime)
        with fs._directory(backups) as fd:
            files._private(fd, directory=True); identity = _identity(fd)
        return canonical_bytes({'version': 1, 'policy': POLICY,
            'instance': runtime.web.spec.instance, 'lease_id': lease_id,
            'backup_root': str(backups), 'backup_identity': identity,
            'publication': c._json(runtime._active_profile), 'http_profile': c._json(profile)})

    def binding(self):
        return binding_for(self.raw)

    def marker_bytes(self, name):
        if name == c.MARKER: return canonical_bytes(self.value['publication']['cutover'])
        if name == a.MARKER: return self.runtime._active_profile
        require(name == MARKER, 'GATEWAY_RESUME_MARKER_REJECTED')
        return canonical_bytes(self.binding())

    def source_binding(self):
        return {'unit': self.runtime.unit,
            'manifest_sha256': sha(canonical_bytes(self.value['publication']['source_manifest'])),
            'state': self.runtime.state_binding(), 'policy': 'GATED_GATEWAY_STOP_BEFORE_FOUNDATION_V1'}

    @contextmanager
    def slot(self):
        require(self.pid == os.getpid(), 'GATEWAY_RESUME_PROCESS_CHANGED')
        with fs._directory(self.root) as fd:
            files._private(fd, directory=True)
            names = {'plan.json', 'ready.json', 'activation-owner.json', 'consumed.json',
                *(stage + '.done.json' for stage in STAGES), *(name + '.removed.json' for name in MARKERS)}
            require(set(os.listdir(fd)) <= names, 'GATEWAY_RESUME_FOREIGN_RECORD')
            require(files._read(fd, 'plan.json', c.MAX_RECORD * 2) == self.raw,
                    'GATEWAY_RESUME_PLAN_CHANGED')
            yield fd

    def read(self, name):
        with self.slot() as fd: return c._optional(fd, name)

    def save(self, name, raw):
        with self.slot() as fd:
            old = c._optional(fd, name)
            if old is None: files._new(fd, name, raw)
            else: require(old == raw, 'GATEWAY_RESUME_RECORD_CHANGED')

    def check(self):
        require(self.pid == os.getpid(), 'GATEWAY_RESUME_PROCESS_CHANGED')
        a.verify(self.runtime)
        account = c.hd.h._identity(self.runtime.web.spec.service_user)
        with fs._directory(self.runtime.web.spec.maintenance_directory) as fd:
            profile = c.hd.h.f._read(fd, 'http-drain-' + self.lease_id + '.attempt',
                                   account.pw_gid)
        require(self.raw == self.plan(self.runtime, self.backups, self.lease_id, profile)
            and self.value['publication']['cutover']['lease_id'] == self.lease_id
            and self.value['http_profile']['gateway_service'] == self.source_binding(),
            'GATEWAY_RESUME_PARENT_CHANGED')
        require(self.read('ready.json') == canonical_bytes(self.binding()), 'GATEWAY_RESUME_NOT_READY')

    def check_runtime(self, runtime):
        self.check()
        require(type(runtime) is a.GatewayServiceRuntime and runtime.web is self.runtime.web
            and getattr(runtime, '_active_profile', None) == self.runtime._active_profile
            and runtime.manifest(runtime.account()) == self.value['publication']['target_manifest']
            and runtime.state_binding() == self.value['publication']['state'], 'GATEWAY_RESUME_TARGET_CHANGED')
        a.verify(runtime)

    def activation_owner(self):
        raw = self.read('activation-owner.json')
        if raw is None: return None
        owner = c._json(raw)
        require(set(owner) == {'handoff', 'activation'} and owner['handoff'] == self.binding(),
                'GATEWAY_RESUME_ACTIVATION_CHANGED')
        with fs._directory(self.backups / ('mobile-activation-' + self.lease_id)) as fd:
            files._private(fd, directory=True)
            plan = files._read(fd, 'plan.json', c.MAX_RECORD)
            armed = files._read(fd, 'armed.json', c.MAX_RECORD)
        value = c._json(plan); activation = owner['activation']
        require(activation == {'version': 1, 'instance': self.runtime.web.spec.instance,
            'lease_id': self.lease_id, 'activation_plan_sha256': sha(plan),
            'resume_plan_sha256': value['resume_plan_sha256']}
            and armed == canonical_bytes(activation)
            and value['runtime']['gateway_successor'] == self.binding()
            and value['instance'] == activation['instance'] and value['lease_id'] == self.lease_id
            and value['backup_root'] == str(self.backups), 'GATEWAY_RESUME_ACTIVATION_CHANGED')
        return raw

    def removed(self, name, owner):
        return canonical_bytes({'owner_sha256': sha(owner), 'marker': name,
                                'marker_sha256': sha(self.marker_bytes(name))})

    def markers(self):
        self.check(); owner = self.activation_owner(); present = {}; pending = False
        with fs._directory(self.runtime.web.spec.maintenance_directory) as fd:
            for name in MARKERS:
                raw = c._optional(fd, name); receipt = self.read(name + '.removed.json')
                if raw is not None:
                    require(raw == self.marker_bytes(name) and receipt is None,
                            'GATEWAY_RESUME_MARKER_CHANGED')
                    pending = True
                else:
                    require(owner is not None and not pending, 'GATEWAY_RESUME_GUARD_MISSING')
                    require(receipt is None or receipt == self.removed(name, owner),
                            'GATEWAY_RESUME_RECEIPT_CHANGED')
                    # Only one unlink may precede its durable receipt.
                    if receipt is None: pending = True
                present[name] = raw is not None
        done = self.read('consumed.json')
        if done is not None:
            require(owner is not None and not any(present.values())
                and all(self.read(name + '.removed.json') == self.removed(name, owner) for name in MARKERS)
                and done == owner, 'GATEWAY_RESUME_CONSUMPTION_CHANGED')
        return present

    @contextmanager
    def admitted(self):
        require(_CURRENT.get() is None, 'GATEWAY_RESUME_NESTED_SCOPE')
        self.check(); self.markers()
        token = _CURRENT.set(self)
        try: yield self
        finally: _CURRENT.reset(token)

    def consume(self, window, record):
        from installer.mobile_activation_admission import ActivationWindow
        require(type(window) is ActivationWindow and window.record is record
            and record.native.http is self.runtime.web and record.lease_id == self.lease_id
            and record.backups == self.backups and record.value['runtime']['gateway_successor'] == self.binding(),
            'GATEWAY_RESUME_LIVE_ADMISSION_REQUIRED')
        window.assert_held()
        require(record.read('armed.json') == record.owner(), 'GATEWAY_RESUME_ARMED_REQUIRED')
        owner = canonical_bytes({'handoff': self.binding(), 'activation': c._json(record.owner())})
        self.save('activation-owner.json', owner)
        for name in MARKERS:
            window.assert_held(); present = self.markers()
            if present[name]:
                os.unlink(name, dir_fd=window.control.lease._directory)
                os.fsync(window.control.lease._directory)
            self.save(name + '.removed.json', self.removed(name, owner))
        window.assert_held(); self.save('consumed.json', owner); self.markers()
