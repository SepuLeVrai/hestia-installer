"""Explicit public starts after local activation, with no ambiguous replay.

The public effect lock covers listener changes. The Persistent timer starts
only after that lock is released. A pending start is observed, never repeated.
"""
from contextlib import contextmanager
import os
import re
import time

from installer import gateway_public_admission as p
from installer import gateway_resume_authority as a
from installer import mobile_activation_admission as activation
from installer.transaction import StateJournal

f, g = p.g.fragments, p.g
POLICY = 'GATEWAY_PUBLIC_OPENING_V1'
ROLES = ('http', 'https', 'timer')


def intent_owner(intent, owner):
    g.require(type(intent) is dict and set(intent) == {'owner', 'before'} and intent['owner'] == owner,
              g.ErrorCode.SOURCE_DRIFT)
    before = intent['before']
    g.require(type(before) is dict and set(before) == {'unit', 'invocation'}
        and before['unit'] == owner['unit'] and type(before['invocation']) is str
        and (before['invocation'] == '' or re.fullmatch('[a-f0-9]{32}', before['invocation'])),
        g.ErrorCode.SOURCE_DRIFT)


class Opening:
    def __init__(self, authority):
        g.require(type(authority) is a.Authority and authority.public is not None,
                  g.ErrorCode.INCOMPATIBLE_STATE)
        self.authority, self.public = authority, authority.public
        self.generation = self.public.generation
        self.shared = self.generation.readers()[1]
        self.root = self.generation.root / 'opening'
        self.owner = {'generation_sha256': self.generation.digest,
            'fragment_plan_sha256': self.public.pointer['fragment_plan_sha256'],
            'admission_sha256': a.sha(authority.raw)}

    def binding(self):
        owner = self.authority.activation_owner()
        g.require(owner is not None and self.authority.read('consumed.json') == owner
            and self.generation._read('activated.json') == self.owner, g.ErrorCode.DEPENDENCY_BLOCKED)
        return {'version': 1, 'policy': POLICY, **self.owner,
                'activation_owner_sha256': a.sha(owner)}

    def local(self):
        g.require(a.current(self.authority.runtime.web) is self.authority, g.ErrorCode.INCOMPATIBLE_STATE)
        raw = activation.e._read_path(self.authority.backups /
            ('mobile-resume-' + self.authority.lease_id), 'plan.json', activation.s.p.MAX_PLAN)
        result = activation.continue_serving(self.authority.runtime.web, self.authority.backups,
            self.authority.lease_id, a.sha(raw), action='check', confirmed=True)
        g.require(result['state'] == 'MOBILE_SERVICES_RUNNING_LOCAL_WEB_AVAILABLE', g.ErrorCode.VALIDATION_FAILED)
        return result

    def prepare(self, *, confirmed):
        g.require(confirmed is True, g.ErrorCode.CONFIRMATION_REQUIRED)
        with self.authority.admitted():
            self.local(); binding = self.binding()
            with g.boot.fs._directory(self.root.parent) as fd:
                f.files._private(fd, directory=True)
                try:
                    os.stat(self.root.name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    os.mkdir(self.root.name, 0o700, dir_fd=fd); os.fsync(fd)
                    with StateJournal(self.root / 'effect-lock.json').locked(create=False):
                        with g.boot.fs._directory(self.root) as child:
                            f._put(child, 'plan.json', {'binding': binding,
                                'slot': f._identity(os.fstat(child)), 'lock': self.lock_identity(child),
                                'epoch': g.mobile.MobileBootRuntime.epoch_identity()})
                # An existing empty directory is never silently adopted.
            with self.slot() as (_, plan):
                reference = {'plan_sha256': f.sha(g.canonical_bytes(plan))}
                with g.boot.fs._directory(self.generation.root) as fd:
                    f._put(fd, 'opening.json', reference)
        return reference

    @staticmethod
    def lock_identity(fd):
        handle = os.open('.transaction.lock', f.files.REGULAR, dir_fd=fd)
        try:
            f.files._private(handle, directory=False)
            return f._identity(os.fstat(handle))
        finally: os.close(handle)

    @contextmanager
    def slot(self, *, current_epoch=True):
        g.require(type(current_epoch) is bool, g.ErrorCode.INVALID_DATA)
        with g.boot.fs._directory(self.root) as fd:
            f.files._private(fd, directory=True)
            allowed = {'plan.json', '.transaction.lock', *(role + suffix for role in ROLES
                       for suffix in ('.intent.json', '.done.json'))}
            g.require(set(os.listdir(fd)) <= allowed, g.ErrorCode.SOURCE_DRIFT)
            plan = f._optional(fd, 'plan.json')
            g.require(type(plan) is dict and set(plan) == {'binding', 'slot', 'lock', 'epoch'}
                and plan['binding'] == self.binding() and plan['slot'] == f._identity(os.fstat(fd))
                and plan['lock'] == self.lock_identity(fd), g.ErrorCode.SOURCE_DRIFT)
            epoch = plan['epoch']
            g.require(type(epoch) is dict and set(epoch) == {'boot_id', 'pid1_start'}
                and type(epoch['boot_id']) is str and re.fullmatch('[a-f0-9-]{36}', epoch['boot_id'])
                and type(epoch['pid1_start']) is str and epoch['pid1_start'].isdecimal(), g.ErrorCode.SOURCE_DRIFT)
            if current_epoch:
                g.require(epoch == g.mobile.MobileBootRuntime.epoch_identity(), g.ErrorCode.SOURCE_DRIFT)
            yield fd, plan
            with g.boot.fs._directory(self.root) as named:
                g.require(f._identity(os.fstat(named)) == plan['slot'], g.ErrorCode.SOURCE_DRIFT)

    def observe(self, role, *, running):
        unit = self.shared.web.unit(role)
        before = p.systemd.show(unit)
        p.systemd.loaded_commands(before, self.generation.units()[unit])
        g.require(before['NeedDaemonReload'] == 'no' and before['DropInPaths'] == '', g.ErrorCode.SOURCE_DRIFT)
        if running:
            g.require(before['ActiveState'] == 'active' and before['InvocationID'] not in ('', '0' * 32),
                      g.ErrorCode.MANUAL_ACTION_REQUIRED)
            if role == 'timer':
                g.require(before['Unit'] == self.shared.web.unit('renew')
                    and before['SubState'] in ('waiting', 'running', 'elapsed'), g.ErrorCode.SOURCE_DRIFT)
            else: g.require(self.shared.listener(role), g.ErrorCode.VALIDATION_FAILED)
        else:
            g.require((before['ActiveState'], before['SubState']) == ('inactive', 'dead'),
                      g.ErrorCode.MANUAL_ACTION_REQUIRED)
            if role != 'timer':
                g.require(before['MainPID'] == before['ControlPID'] == '0'
                    and g.boot.h.drain._empty_cgroup(unit), g.ErrorCode.SOURCE_DRIFT)
        g.require(p.systemd.show(unit) == before, g.ErrorCode.SOURCE_DRIFT)
        return {'unit': unit, 'invocation': before['InvocationID']}

    def start(self, fd, plan, role, *, check_only=False):
        g.require(role in ROLES and type(check_only) is bool, g.ErrorCode.INVALID_DATA)
        owner = {'plan_sha256': f.sha(g.canonical_bytes(plan)), 'role': role,
                 'unit': self.shared.web.unit(role)}
        intent, done = (f._optional(fd, role + suffix) for suffix in ('.intent.json', '.done.json'))
        if done is not None:
            intent_owner(intent, owner)
            g.require(done == {'intent': intent, 'observed': self.observe(role, running=True)}, g.ErrorCode.SOURCE_DRIFT)
            return
        g.require(not check_only, g.ErrorCode.DEPENDENCY_BLOCKED)
        if intent is None:
            intent = {'owner': owner, 'before': self.observe(role, running=False)}
            f._put(fd, role + '.intent.json', intent)
            self.shared.control('start', role)
        else:
            intent_owner(intent, owner)
            # Never replay a start after loss of response, even if stopped.
            self.observe(role, running=True)
        deadline = time.monotonic() + 10
        while True:
            try: observed = self.observe(role, running=True); break
            except Exception:
                g.require(time.monotonic() < deadline, g.ErrorCode.VALIDATION_FAILED)
                time.sleep(.1)
        g.require(observed['invocation'] != intent['before']['invocation'], g.ErrorCode.SOURCE_DRIFT)
        f._put(fd, role + '.done.json', {'intent': intent, 'observed': observed})

    def apply(self, *, confirmed, check_only=False):
        g.require(confirmed is True and type(check_only) is bool, g.ErrorCode.CONFIRMATION_REQUIRED)
        with self.slot(): pass  # A missing/replaced lock must not be recreated.
        with StateJournal(self.root / 'effect-lock.json').locked(create=False):
            with self.authority.admitted(public_closed=False):
                local = self.local()
                with self.slot() as (fd, plan):
                    g.require(self.generation._read('opening.json') == {
                        'plan_sha256': f.sha(g.canonical_bytes(plan))}, g.ErrorCode.SOURCE_DRIFT)
                    scope = self.public.http._scope(self.generation.layout.identity.account())
                    with scope.writer():
                        g.require(scope.observe()['state'] == 'SERVING', g.ErrorCode.MANUAL_ACTION_REQUIRED)
                        for role in ('http', 'https'): self.start(fd, plan, role, check_only=check_only)
                        self.shared.web.certificate(minimum_lifetime=0); self.shared.mobile.verify(minimum_lifetime=0)
                        with g.boot.fs._directory(self.generation.root) as root:
                            if check_only:
                                g.require(f._optional(root, 'opened.json') == self.owner, g.ErrorCode.SOURCE_DRIFT)
                            else: f._put(root, 'opened.json', self.owner)
            # Persistent renewal may begin immediately. Do not hold its public
            # effect lock while asking PID 1 to start the timer.
            with scope.writer(), self.slot() as (fd, plan):
                g.require(scope.observe()['state'] == 'SERVING', g.ErrorCode.MANUAL_ACTION_REQUIRED)
                self.public.check()
                self.start(fd, plan, 'timer', check_only=check_only)
        return {'state': 'PUBLIC_LISTENERS_RUNNING', **self.owner,
                'local_web': local['local_web'],
                'services_started': not check_only, 'boot_requalified': False, 'phase6_complete': False}

    def check(self):
        """Read only: an old start receipt cannot authorize a new invocation."""
        with self.slot(current_epoch=False) as (_, plan):
            if plan['epoch'] == g.mobile.MobileBootRuntime.epoch_identity():
                return self.apply(confirmed=True, check_only=True)
        # New boot readers observe the target directly. Historical admission
        # uses one exact HTTP object and is not transferable to these readers.
        with StateJournal(self.generation.original.shared.root / 'effect-lock.json').locked(create=False):
            self.authority.check(); self.public.check(); self.generation.configuration()
            scope = self.public.http._scope(self.generation.layout.identity.account())
            with scope.writer(), self.slot(current_epoch=False) as (fd, plan):
                epoch = g.mobile.MobileBootRuntime.epoch_identity()
                g.require(epoch != plan['epoch'] and scope.observe()['state'] == 'SERVING', g.ErrorCode.SOURCE_DRIFT)
                digest = f.sha(g.canonical_bytes(plan))
                g.require(self.generation._read('opening.json') == {'plan_sha256': digest}
                    and self.generation._read('opened.json') == self.owner, g.ErrorCode.SOURCE_DRIFT)
                for role in ROLES:
                    intent = f._optional(fd, role + '.intent.json')
                    intent_owner(intent, {'plan_sha256': digest, 'role': role, 'unit': self.shared.web.unit(role)})
                    done = f._optional(fd, role + '.done.json')
                    g.require(type(done) is dict and set(done) == {'intent', 'observed'} and done['intent'] == intent
                        and type(done['observed']) is dict and set(done['observed']) == {'unit', 'invocation'}
                        and done['observed']['unit'] == intent['owner']['unit']
                        and type(done['observed']['invocation']) is str
                        and re.fullmatch('[a-f0-9]{32}', done['observed']['invocation']), g.ErrorCode.SOURCE_DRIFT)
                    observed = self.observe(role, running=True)
                    g.require(observed != done['observed'], g.ErrorCode.SOURCE_DRIFT)
                web, _, mobile = self.generation.readers()
                web.live(); mobile.live()
                for role in ('foundation', 'gateway'):
                    runtime = getattr(mobile, role)
                    owner = {'profile_sha256': g.mobile.digest(mobile.profile), 'epoch': epoch,
                             'role': role, 'unit': runtime.unit}
                    g.require(mobile.epoch._read(role + '.attempt') == owner
                        and mobile.epoch._read(role + '.json') == {'owner': owner, 'process': mobile.process(runtime)},
                        g.ErrorCode.SOURCE_DRIFT)
                self.shared.web.certificate(minimum_lifetime=0); self.shared.mobile.verify(minimum_lifetime=0)
                g.require(g.mobile.MobileBootRuntime.epoch_identity() == epoch, g.ErrorCode.SOURCE_DRIFT)
            # A real HTTP request needs the activity lock shared by PHP.
            local_web = web.activation.check()
            self.public.check()
            g.require(g.mobile.MobileBootRuntime.epoch_identity() == epoch, g.ErrorCode.SOURCE_DRIFT)
        return {'state': 'PUBLIC_LISTENERS_RUNNING', **self.owner, 'services_started': False,
                'local_web': local_web, 'boot_requalified': True, 'epoch': epoch, 'phase6_complete': False}


def worker_admitted(generation, role, authority):
    """A first listener may run only under its durable, same-epoch start intent."""
    opening = Opening(authority)
    g.require(opening.generation.digest == generation.digest, g.ErrorCode.SOURCE_DRIFT)
    opened = generation._read('opened.json')
    if opened is not None:
        g.require(opened == opening.owner, g.ErrorCode.SOURCE_DRIFT)
        return
    g.require(role in ('http', 'https'), g.ErrorCode.DEPENDENCY_BLOCKED)
    with opening.slot() as (fd, plan):
        digest = f.sha(g.canonical_bytes(plan))
        g.require(generation._read('opening.json') == {'plan_sha256': digest}, g.ErrorCode.SOURCE_DRIFT)
        intent = f._optional(fd, role + '.intent.json')
        intent_owner(intent, {'plan_sha256': digest, 'role': role, 'unit': opening.shared.web.unit(role)})
        if role == 'https': g.require(f._optional(fd, 'http.done.json') is not None, g.ErrorCode.DEPENDENCY_BLOCKED)
