"""Explicit successor readers without rewriting a frozen parent bundle.

The compiler is pure. Installing or authorizing a generation is the job of the
native coordinator. A compiled profile or a source receipt alone grants no boot.
"""
from copy import deepcopy
import os
from pathlib import Path
import re

from installer import boot_runtime as boot, mobile_boot_runtime as mobile
from installer import shared_public_runtime as public, gateway_frozen_reference as frozen
from installer import gateway_public_fragments as fragments
from installer.gateway_transition import assess
from installer.model import ErrorCode, canonical_bytes, exact_keys, require
from installer.package_plan import PackagePlan
from installer.maintenance import MaintenanceLease
from installer.transaction import _private_directory

POLICY = 'GATEWAY_PUBLIC_BOOT_SUCCESSOR_V1'
ROLES = ('sql', 'web', 'mobile', 'http', 'https', 'backend', 'renew')
WORKER = 'installer/private/gateway_public_worker.py'


def sha(value): return boot.f._sha(canonical_bytes(value))


def selection(shared, mobile_profile, target_commit, direction, publication_sha256, lease_id, *, predecessor=None, source_binding=None):
    original = mobile.MobileBootRuntime(mobile_profile)
    require(canonical_bytes(shared) == canonical_bytes(mobile_profile['shared']), ErrorCode.INCOMPATIBLE_STATE)
    source = original.gateway.profile
    if predecessor is not None:
        from installer.gateway_service_profile import GatewayServiceProfile
        source = GatewayServiceProfile.from_binding(original.gateway.foundation, source_binding)
    assessed = assess(source, target_commit=target_commit, direction=direction).report()
    require(assessed['configuration_compatible'], ErrorCode.INCOMPATIBLE_STATE)
    value = {'version': 1 if predecessor is None else 2, 'policy': POLICY, 'lease_id': lease_id,
             **({} if predecessor is None else {'predecessor': deepcopy(predecessor), 'source_binding': deepcopy(source_binding)}),
             'shared': deepcopy(shared), 'mobile': deepcopy(mobile_profile),
             'target_binding': assessed['target'], 'direction': direction,
             'publication_sha256': publication_sha256,
             'code': {name: boot.f._sha(raw) for name, raw in mobile.code_files().items()}}
    Generation(value)
    return value


def source_configuration(original):
    require(type(original) is mobile.MobileBootRuntime, ErrorCode.INVALID_DATA)
    original.shared.boot.configuration(); original.shared.configuration(); original.configuration()
    original.shared.completed('verify')
    require(original.shared.ready(), ErrorCode.DEPENDENCY_BLOCKED)
    enabled_sources(original.shared.boot, original)


def enabled_sources(*runtimes):
    for runtime in runtimes:
        owner = runtime._read('enabled.json')
        require(type(owner) is dict and owner == runtime._read('enable.attempt')
                and set(owner) == {'version', 'installation_id', 'spec_sha256', 'profile_sha256'}
                and type(owner['version']) is int and owner['version'] == 1
                and owner['profile_sha256'] == sha(runtime.profile)
                and type(owner['installation_id']) is str
                and re.fullmatch('[a-f0-9-]{32,36}', owner['installation_id']), ErrorCode.SOURCE_DRIFT)
        frozen.digest(owner['spec_sha256'])
        with boot.fs._directory(runtime.link.parent) as fd:
            runtime.exact_link(fd, runtime.link.name, '../' + runtime.target)


class Generation:
    _read = PackagePlan._read

    def __init__(self, value):
        exact_keys(value, {'version', 'policy', 'lease_id', 'shared', 'mobile',
                           'target_binding', 'direction', 'publication_sha256', 'code'} |
                   ({'predecessor', 'source_binding'} if value.get('version') == 2 else set()))
        require(type(value['version']) is int and value['version'] in (1, 2) and value['policy'] == POLICY)
        require(type(value['lease_id']) is str and re.fullmatch('[a-f0-9]{32}', value['lease_id']))
        frozen.digest(value['publication_sha256'])
        self.value = deepcopy(value)
        self.original = mobile.MobileBootRuntime(self.value['mobile'])
        require(self.original.profile['shared'] == self.value['shared'], ErrorCode.INCOMPATIBLE_STATE)
        # First public generation only. Later cycles require their own parent
        # chain, not an implicit adoption of an already-published profile.
        require(self.value['shared']['version'] == 1
                and self.original.gateway.profile.dev is None and self.original.gateway.profile.push is None,
                ErrorCode.UNSUPPORTED_MODULE)
        self.source_profile = self.original.gateway.profile
        if value['version'] == 2:
            from installer.gateway_public_ancestry import validate
            from installer.gateway_service_profile import GatewayServiceProfile
            validate(value['predecessor'])
            require(value['predecessor']['lease_id'] != value['lease_id'], ErrorCode.INCOMPATIBLE_STATE)
            self.source_profile = GatewayServiceProfile.from_binding(self.original.gateway.foundation, value['source_binding'])
            require(self.source_profile.dev is None and self.source_profile.push is None, ErrorCode.UNSUPPORTED_MODULE)
        assessed = assess(self.source_profile,
            target_commit=self.value['target_binding']['release']['commit'], direction=self.value['direction']).report()
        require(assessed['configuration_compatible'] and assessed['target'] == self.value['target_binding'],
                ErrorCode.INCOMPATIBLE_STATE)
        # Reuse the exact closed code grammar; the Apache template is the one
        # explicit non-Python/PHP/JSON asset admitted by Mobile boot.
        require(type(value['code']) is dict and mobile.ASSET in value['code'] and WORKER in value['code'])
        frozen.digest(value['code'][mobile.ASSET])
        public.old.Profile({**self.original.shared.web.value,
                            'code': {n: d for n, d in value['code'].items() if n != mobile.ASSET}})
        self.layout, self.http = self.original.layout, self.original.http
        self.root = self.layout.root / ('public-successor-' + value['lease_id'])
        self.digest = sha(self.value)

    def runner(self):
        template = (boot.SOURCE.parent / WORKER).read_bytes()
        require(template.count(b'__GATEWAY_PUBLIC_GENERATION_SHA256__') == 1, ErrorCode.INVALID_STATE)
        return template.replace(b'__GATEWAY_PUBLIC_GENERATION_SHA256__', self.digest.encode())

    def original_units(self):
        original = self.original; shared = original.shared; web = shared.boot
        available = {**web.units(), **original.units(), **shared.units(),
                     str(shared.dropin.relative_to(boot.h.drain.UNIT_ROOT)): shared.apache_dropin()}
        return {name: available[name] for name in fragments.resources(self.layout.instance)}

    def source_units(self):
        if 'predecessor' not in self.value: return self.original_units()
        previous = self.value['predecessor']
        return self._units(self.layout.root / ('public-successor-' + previous['lease_id']),
                           previous['generation_sha256'])

    def units(self): return self._units(self.root, self.digest)

    def _units(self, root, digest):
        original = self.original; shared = original.shared; web = shared.boot
        sources = self.original_units(); result = {}
        replacements = {
            web.guard: (web.root, 'sql', 'sql'), web.ready: (web.root, 'web', 'web'),
            original.target: (original.root, '', 'mobile'),
            **{shared.web.unit(role): (shared.root, role, role) for role in ('http', 'https', 'renew')},
            str(shared.dropin.relative_to(boot.h.drain.UNIT_ROOT)): (shared.root, 'backend', 'backend')}
        for name, raw in sources.items():
            if name in replacements:
                original_root, old_role, new_role = replacements[name]
                before = (str(original_root / 'worker.py') + (' ' + old_role if old_role else '') + '\n').encode()
                after = (str(root / 'worker.py') + ' ' + new_role + '\n').encode()
                require(raw.count(before) == 1, ErrorCode.INVALID_STATE)
                raw = raw.replace(before, after)
            # The timer has no worker. Bind even that fragment to this exact
            # generation, while keeping its schedule and target unchanged.
            if b'Description=' in raw:
                raw = raw.replace(b'Description=', ('Description=Generation ' + digest + ' ').encode())
            require(raw != sources[name], ErrorCode.INVALID_STATE)
            result[name] = raw
        return result

    def references(self):
        return {'source_gateway': sha(self.source_profile.binding()),
                'target_gateway': sha(self.value['target_binding']),
                'publication': self.value['publication_sha256'],
                'shared_public': sha(self.value['shared']),
                'web_boot': sha(self.original.shared.boot.profile),
                'mobile_boot': sha(self.value['mobile']), 'successor_code': sha(self.value['code'])}

    def replacements(self):
        before, after = self.source_units(), self.units()
        return {name: (before[name], after[name]) for name in before}

    def source_configuration(self):
        if 'predecessor' not in self.value:
            source_configuration(self.original)
            return
        from installer.gateway_public_ancestry import load, history
        history(self); previous = load(self)
        # The next Gateway publication may already exist. Audit the exact old
        # public fragments without pretending that the old Gateway is current.
        previous.configuration(publication=False)
        enabled_sources(previous.readers()[0], previous.readers()[2])

    def stage(self, lease, *, confirmed):
        require(confirmed is True and type(lease) is MaintenanceLease, ErrorCode.CONFIRMATION_REQUIRED)
        require(lease.lease_id == self.value['lease_id'] and lease.scope.instance == self.layout.instance
                and lease.scope.directory == self.http.spec.maintenance_directory, ErrorCode.INCOMPATIBLE_STATE)
        lease.assert_held(); self.source_configuration(); self.current_publication()
        # The caller must first stop and fence these exact public workers.
        source_shared = self.original.shared
        if 'predecessor' in self.value:
            from installer.gateway_public_ancestry import load
            source_shared = load(self).readers()[1]
        for role in ('timer', 'renew', 'http', 'https'): source_shared.stopped(role)
        contents = mobile.code_files()
        require({n: boot.f._sha(raw) for n, raw in contents.items()} == self.value['code'], ErrorCode.SOURCE_DRIFT)
        payloads = {'profile.json': canonical_bytes(self.value), 'worker.py': self.runner(),
                    **{'code/' + name: raw for name, raw in contents.items()}}
        for name, raw in payloads.items():
            require(len(raw) <= 1048576, ErrorCode.INVALID_DATA); lease.assert_held()
            path = self.root / name
            with _private_directory(path.parent, create=True) as fd:
                try: old = boot.f._read(fd, path.name, 0, mode=0o600, limit=1048576)
                except FileNotFoundError: old = None
                if old is None: fragments.files._new(fd, path.name, raw)
                else: require(old == raw, ErrorCode.SOURCE_DRIFT)
        self.bundle(); lease.assert_held()
        with boot.fs._directory(self.root) as fd:
            fragments.files._private(fd, directory=True)
            fragments._put(fd, 'staged.json', {'generation_sha256': self.digest})
        return {'state': 'PUBLIC_SUCCESSOR_BUNDLE_STAGED', 'generation_sha256': self.digest,
                'services_started': False, 'activity_resumed': False, 'boot_requalified': False}

    def bundle(self):
        require(self._read('profile.json') == self.value, ErrorCode.SOURCE_DRIFT)
        expected = {'worker.py': boot.f._sha(self.runner()),
                    **{'code/' + name: digest for name, digest in self.value['code'].items()}}
        paths = list((self.root / 'code').rglob('*'))
        require(not any(p.is_symlink() for p in paths)
                and {p.relative_to(self.root).as_posix() for p in paths if not p.is_dir()}
                    == set(expected) - {'worker.py'}, ErrorCode.SOURCE_DRIFT)
        for name, digest in expected.items():
            path = self.root / name
            with boot.fs._directory(path.parent) as fd:
                require(boot.f._sha(boot.f._read(fd, path.name, 0, mode=0o600, limit=1048576)) == digest,
                        ErrorCode.SOURCE_DRIFT)

    def current_publication(self):
        binding, publication = frozen.reference(self.original.gateway)
        require(binding == self.value['target_binding'] and publication == self.value['publication_sha256'],
                ErrorCode.SOURCE_DRIFT)

    def selected(self, http=None):
        """File-backed selection; safe before SQL and queued systemd jobs."""
        from installer import gateway_resume_authority as admission
        runtime = admission.selected_for_admission(self.http if http is None else http)
        require(runtime.profile.binding() == self.value['target_binding']
                and boot.f._sha(runtime._active_profile) == self.value['publication_sha256'], ErrorCode.SOURCE_DRIFT)
        return runtime

    def target(self, http=None):
        runtime = self.selected(http)
        frozen.attach(runtime, self.value['publication_sha256'])
        return runtime

    def readers(self):
        return SuccessorBoot(self), SuccessorPublic(self), SuccessorMobile(self)

    def configuration(self, *, publication=True):
        require(type(publication) is bool, ErrorCode.INVALID_DATA)
        self.bundle()
        if 'predecessor' in self.value:
            from installer.gateway_public_ancestry import history
            history(self)
        if publication: self.current_publication()
        web, shared, boot_mobile = self.readers()
        web.configuration(); shared.configuration(); boot_mobile.configuration()
        shared.completed('verify'); require(shared.ready(), ErrorCode.DEPENDENCY_BLOCKED)

    def installed_fragments(self, confirmation):
        root = self.original.gateway.root / 'control' / ('public-fragments-' + self.value['lease_id'])
        return fragments.CompletedFragments(root, boot.h.drain.UNIT_ROOT, self.layout.instance,
            self.value['lease_id'], self.http.spec.maintenance_directory,
            self.references(), self.replacements()).check(confirmation)

    def worker(self, role):
        require(role in ROLES, ErrorCode.INVALID_DATA)
        self.configuration()
        # Installation and worker authority remain distinct. Only the complete
        # coordinator can issue this owner; workers never create/repair it.
        owner = self._read('activated.json')
        require(type(owner) is dict and set(owner) == {'generation_sha256', 'fragment_plan_sha256', 'admission_sha256'}
                and owner['generation_sha256'] == self.digest, ErrorCode.DEPENDENCY_BLOCKED)
        frozen.digest(owner['fragment_plan_sha256']); frozen.digest(owner['admission_sha256'])
        self.installed_fragments(owner['fragment_plan_sha256'])
        from installer import gateway_resume_authority as admission
        runtime = self.selected(); pub = admission.c._json(runtime._active_profile)
        require(pub['cutover']['lease_id'] == self.value['lease_id'], ErrorCode.SOURCE_DRIFT)
        # Do not perform an idle-job native audit ahead of SQL/Web boot.
        # The publication, installed fragment inodes and sealed bundles are
        # checked here; Mobile performs its native audit after Web is ready.
        root = runtime.root / 'control' / ('resume-' + self.value['lease_id'])
        with boot.fs._directory(root) as fd:
            raw = admission.files._read(fd, 'plan.json', admission.c.MAX_RECORD * 2)
            require(boot.f._sha(raw) == owner['admission_sha256'], ErrorCode.SOURCE_DRIFT)
        plan = admission.c._json(raw)
        authority = admission.Authority.load(runtime, Path(plan['backup_root']), self.value['lease_id'])
        authority.markers()
        require(authority.read('consumed.json') is not None, ErrorCode.DEPENDENCY_BLOCKED)
        # Local activation authorizes the Apache guard only. Public listeners,
        # renewal and a later boot also require the public opening boundary.
        # Its producer is deliberately separate from SQL admission.
        if role != 'backend':
            from installer.gateway_public_opening import worker_admitted
            worker_admitted(self, role, authority)
        if role in ('http', 'https', 'renew'):
            scope = self.http._scope(self.layout.identity.account())
            require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
        web, shared, boot_mobile = self.readers()
        if role in ('sql', 'web'): web.boot(role); return None
        if role == 'mobile': boot_mobile.boot(); return None
        return shared.worker(role)


class SuccessorBoot(boot.BootRuntime):
    def __init__(self, generation):
        self.generation = generation
        super().__init__(generation.original.shared.boot.profile)

    def units(self):
        result = super().units(); target = self.generation.units()
        return {name: target.get(name, raw) for name, raw in result.items()}


class SuccessorPublic(public.SharedPublic):
    def __init__(self, generation):
        self.generation = generation
        super().__init__(generation.value['shared'])
        self.boot = SuccessorBoot(generation)

    def units(self):
        target = self.generation.units()
        return {name: target[name] for name in super().units()}

    def apache_dropin(self):
        return self.generation.units()[str(self.dropin.relative_to(boot.h.drain.UNIT_ROOT))]

    def gateway(self):
        runtime = self.generation.target(self.http); runtime.owned(); return runtime


class SuccessorMobile(mobile.MobileBootRuntime):
    def __init__(self, generation):
        self.generation = generation
        super().__init__(generation.value['mobile'])
        self.shared = SuccessorPublic(generation)
        self.epoch.root = Path('/run') / ('hestia-' + self.layout.instance + '-mobile-boot-' + generation.value['lease_id'])

    def units(self): return {self.target: self.generation.units()[self.target]}

    def attach_gateway(self):
        self.gateway = self.generation.target(self.http)
        self.foundation = self.gateway.foundation


def systemd_manager(generation, lease):
    """Closed adapter: all destinations come from the selected generation."""
    from installer.gateway_public_systemd import Manager
    require(type(generation) is Generation and type(lease) is MaintenanceLease, ErrorCode.INVALID_DATA)
    require(lease.lease_id == generation.value['lease_id']
        and lease.scope.instance == generation.layout.instance
        and lease.scope.directory == generation.http.spec.maintenance_directory, ErrorCode.INCOMPATIBLE_STATE)
    lease.assert_held()
    transfer = fragments.FragmentTransfer(generation.original.gateway.root / 'control' /
        ('public-fragments-' + lease.lease_id), boot.h.drain.UNIT_ROOT, lease,
        generation.references(), generation.replacements())
    return Manager(transfer, generation.original.shared.root / 'effect-lock.json')


def prepare_systemd(generation, lease, *, confirmed):
    require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
    manager = systemd_manager(generation, lease)
    generation.current_publication(); generation.source_configuration()
    generation.selected().stopped()
    return manager.prepare(confirmed=True)


def transfer_systemd(generation, lease, confirmation, *, confirmed):
    require(confirmed is True, ErrorCode.CONFIRMATION_REQUIRED)
    manager = systemd_manager(generation, lease)
    generation.current_publication()
    if generation._read('staged.json') is None:
        manager.stop_public(confirmation, confirmed=True)
        with manager.locked(), manager.slot(confirmation):
            generation.stage(lease, confirmed=True)
    else:
        require(generation._read('staged.json') == {'generation_sha256': generation.digest}, ErrorCode.SOURCE_DRIFT)
        generation.bundle()
    result = manager.apply(confirmation, confirmed=True)
    generation.configuration(); generation.installed_fragments(result['plan_sha256'])
    from installer.gateway_public_selection import publish
    publish(generation, lease, confirmation)
    return result
