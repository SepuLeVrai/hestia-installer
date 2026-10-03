"""Explicit local registration of a distinct, already managed DEV Web.

Registration only reads native seals and writes public metadata. The browser
selects its digest; it cannot supply a path, import a database or adopt a site.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import re
import uuid

from installer import application_plan as app
from installer.application_activation import Activation
from installer.model import ErrorCode, canonical_bytes, exact_keys, require, strict_json_loads
from installer.package_plan import PackagePlan
from installer.transaction import _private_directory
from installer.upgrade_plan import ManagedProfile


def digest(value): return app.a.digest(value)


class DevActivation(Activation):
    def probe_port(self):
        require(self.runtime.spec.port == 9084 and self.runtime.spec.ingress ==
                app.ProxyIngress('127.0.0.2', ('127.0.0.1/32',)), ErrorCode.INVALID_STATE)
        return 9084


class DevManagedProfile(ManagedProfile):
    def __init__(self, descriptor):
        require(type(descriptor) is dict and descriptor.get('version') == 2, ErrorCode.INCOMPATIBLE_STATE)
        super().__init__({**descriptor, 'version': 1})
        self.descriptor = deepcopy(descriptor)
        self.http = app.h.HttpRuntime(replace(self.http.spec, external_uploads=True, source_commit=app.mobile.COMMIT))


class DevTarget:
    def __init__(self, value):
        exact_keys(value, {'version', 'descriptor', 'configuration', 'preparation_sha256',
                           'main_configuration_sha256', 'debug_subjects'})
        require(type(value['version']) is int and value['version'] == 1)
        for key in ('preparation_sha256', 'main_configuration_sha256'):
            require(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key]))
        subjects = value['debug_subjects']
        require(type(subjects) is list and 1 <= len(subjects) <= 16 and subjects == sorted(set(subjects)))
        for subject in subjects:
            require(type(subject) is str and str(uuid.UUID(subject)) == subject and uuid.UUID(subject).version == 4)
        require(value['descriptor'].get('version') == 2, ErrorCode.INCOMPATIBLE_STATE)
        self.managed = DevManagedProfile(value['descriptor'])
        self.http = self.managed.http
        layout = app.FreshProfile(self.http.spec.instance, 2)
        require(self.http.spec.root == layout.root / 'http' and self.http.spec.webroot == layout.webroot
                and self.http.spec.service_user == layout.identity.user
                and value['descriptor']['worker'] == {'user': layout.worker.user,
                    'run_root': str(layout.root / 'run'), 'state_root': str(layout.root / 'attempts')},
                ErrorCode.INCOMPATIBLE_STATE)
        require(self.http.spec.port == 9084, ErrorCode.INCOMPATIBLE_STATE)
        inputs = app.a.WebInputs(value['configuration']).configuration
        require(inputs['mode'] == 'upgrade' and inputs['administrator'] is None
                and inputs['database']['mode'] == 'existing_local'
                and inputs['database']['host'] == '127.0.0.1' and inputs['database']['port'] == 3306
                and inputs['web'] == {k: value['descriptor']['http'][k]
                                     for k in ('hostname', 'webroot', 'service_user')}, ErrorCode.INVALID_STATE)
        self.value = deepcopy(value)
        self.activation = DevActivation(self.http, value['preparation_sha256'])

    def separate(self, main, configuration=None):
        a, b = main.spec, self.http.spec
        require(a.instance != b.instance and a.service_user != b.service_user
                and a.hostname != b.hostname and a.port != b.port, ErrorCode.INCOMPATIBLE_STATE)
        for left in (a.root.parent, a.webroot, a.maintenance_directory.parent.parent):
            for right in (b.root.parent, b.webroot, b.maintenance_directory.parent.parent):
                require(left != right and left not in right.parents and right not in left.parents,
                        ErrorCode.INCOMPATIBLE_STATE)
        if configuration is not None:
            require(digest(configuration) == self.value['main_configuration_sha256'], ErrorCode.SOURCE_DRIFT)
            dev = self.value['configuration']['database']; source = configuration['database']
            require(source['name'].lower() != dev['name'].lower() and source['user'] != dev['user'],
                    ErrorCode.INCOMPATIBLE_STATE)
        return self

    def inspect(self):
        require(self.managed.inspect() == self.value['configuration'], ErrorCode.SOURCE_DRIFT)
        self.activation.configuration()
        return self

    def serving(self):
        self.inspect(); self.activation.serving()
        require(all(self.activation.running(role) for role in ('php', 'apache', 'timer')),
                ErrorCode.DEPENDENCY_BLOCKED)
        return self


class DevBootUnit:
    """One existing DEV unit, started once per PID-1 epoch by Mobile boot."""
    def __init__(self, target, role):
        require(role in ('php', 'apache', 'timer'))
        self.target, self.role = target, role
        self.unit = target.activation.unit(role)

    def stopped(self):
        from installer import system_drain as drain
        self.target.inspect()
        active = self.target.activation
        if self.role == 'timer':
            active.cleaner._timer_state(stopped=True)
        else:
            scope, _ = active.configuration()
            account, extension, _, _ = self.target.http._inspect_configuration()
            raw = self.target.http._files(account, extension)[drain.UNIT_ROOT / self.unit]
            drain.audit_unit(scope, drain.UnitBinding(self.role, app.f._sha(raw)), stopped=True)

    def owned(self):
        self.target.inspect(); self.target.activation.serving()
        require(self.target.activation.running(self.role), ErrorCode.VALIDATION_FAILED)

    def process(self):
        from installer.mobile_activation_runtime import invocation
        before = invocation(self.unit)
        self.owned()
        require(before == invocation(self.unit) and before['InvocationID'] not in ('', '0' * 32), ErrorCode.SOURCE_DRIFT)
        return {'invocation_id': before['InvocationID'],
                'active_enter_monotonic_us': before['ActiveEnterTimestampMonotonic']}


class DevRegistration:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, foundation):
        self.foundation = foundation
        self.root = foundation.gateway.root / 'dev-target'

    def read(self):
        value = self._read('target.json')
        if value is not None: DevTarget(value)
        return value

    def state(self):
        value = self.read()
        return {'target': value, 'confirmation': digest(value) if value is not None else None}

    def register(self, path):
        require(isinstance(path, Path) and path.is_absolute())
        with _private_directory(path.parent, create=False) as fd:
            value = strict_json_loads(app.f._read(fd, path.name, 0, mode=0o600, limit=16384))
        target = DevTarget(value)
        f = self.foundation
        with f.parent.journal.locked(create=False) as locked:
            parent = locked.read(); _, active = f.activation.engine(parent)
            require(f.profile() is None and f.journal.read() is None
                    and not (f.gateway.root / 'service').exists(), ErrorCode.PLAN_EXISTS)
            target.separate(active.runtime, f.application.read()['configuration']).serving()
            require(f.gateway.profile()['identity']['dev_enabled'] is True, ErrorCode.DEPENDENCY_BLOCKED)
            # The keys are already owned by the Gateway preparation. Never create
            # or re-label an identity when registering a target.
            f.gateway.identities.verify()
            f.parent.secrets.reject_in(value)
            existing = self.read()
            require(existing is None or existing == value, ErrorCode.PLAN_EXISTS)
            if existing is None: self._write('target.json', value)
        return self.state()
