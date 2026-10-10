"""Maintenance-closed public stops, owned fragment transfer and manager reload.

No start or reopening command exists here. Recovery may repeat a stop of the
same invocation or an idempotent daemon-reload, never adopt another invocation.
The completed record is not an admission to serve or a boot qualification.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shlex

from installer import gateway_public_fragments as f
from installer import systemd_observations as observations
from installer import boot_runtime as boot
from installer.mobile_boot_runtime import MobileBootRuntime
from installer.transaction import StateJournal

POLICY = 'GATEWAY_PUBLIC_SYSTEMD_TRANSFER_V1'
STOP = ('timer', 'https', 'http')
require = f.require


def show(unit):
    require(type(unit) is str and re.fullmatch(
        r'hestia-[a-f0-9]{32}-(?:(?:boot-sql|boot-web|mobile-boot|apache|public-http|public-https|public-renew)\.service|public-renew\.timer)', unit))
    boot.h.p._safe_path(Path('/usr/bin/systemctl'), directory=False, system=True)
    props = observations.COMMON + ('InvocationID', 'Description') + (('Unit',) if unit.endswith('.timer')
        else observations.SERVICE + ('ExecStart', 'ExecStartPre'))
    raw = observations._capture(['/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password',
        'show', '--all', '--property=' + ','.join(props), '--', unit])
    rows = raw.decode('ascii').splitlines(); values = {}
    for row in rows:
        key, sep, value = row.partition('=')
        require(sep and key in props and key not in values and len(value) <= 2048
                and not any(ord(c) < 32 or ord(c) == 127 for c in value))
        values[key] = value
    # systemctl's composite Exec printer emits no row for an empty array,
    # including with --all (observed on the qualified Debian 13 manager).
    # Only those two arrays have this representation; all scalar fields remain
    # mandatory. loaded() still rejects an empty array when a command is bound.
    missing = set(props) - set(values)
    require(missing <= {'ExecStart', 'ExecStartPre'})
    for field in missing: values[field] = ''
    require(set(values) == set(props) and values['Id'] == unit and values['LoadState'] == 'loaded'
        and values['Job'] == '' and values['NeedDaemonReload'] in ('yes', 'no')
        and values['FragmentPath'] == str(boot.h.drain.UNIT_ROOT / unit)
        and (values['InvocationID'] == '' or re.fullmatch('[a-f0-9]{32}', values['InvocationID'])))
    return values


class Manager:
    """Internal primitive: fixed units, real lease and public effect lock."""
    def __init__(self, transfer, effect_lock):
        require(type(transfer) is f.FragmentTransfer and transfer.unit_root == boot.h.drain.UNIT_ROOT)
        require(isinstance(effect_lock, Path) and effect_lock.is_absolute() and effect_lock.name == 'effect-lock.json')
        self.transfer, self.lease, self.effect_lock = transfer, transfer.lease, effect_lock
        self.root = transfer.root.parent / ('public-systemd-' + self.lease.lease_id)
        self.units = tuple(transfer.replacements)
        self.public = {role: self.units[index] for role, index in (('http', 3), ('https', 4), ('renew', 5), ('timer', 6))}
        self.apache = self.units[-1].split('.d/')[0]

    def binding(self):
        return {'version': 1, 'policy': POLICY, 'fragments': self.transfer.binding,
                'effect_lock': str(self.effect_lock)}

    def held(self):
        self.transfer._held()

    @contextmanager
    def locked(self):
        self.held()
        with StateJournal(self.effect_lock).locked(create=False):
            with f.fs._directory(self.effect_lock.parent) as fd:
                identity = f._identity(os.fstat(fd))
            yield
            self.held()
            with f.fs._directory(self.effect_lock.parent) as fd:
                require(f._identity(os.fstat(fd)) == identity)

    def observe(self, unit, *, reload_pending=False):
        value = show(unit)
        expected = ''
        if unit == self.apache:
            guard = self.transfer.unit_root / (self.apache + '.d/50-hestia-maintenance.conf')
            with f.fs._directory(guard.parent) as fd:
                require(set(os.listdir(fd)) <= {'50-hestia-maintenance.conf', '60-hestia-public.conf',
                    self.transfer._temporary('60-hestia-public.conf')})
                require(f._file(fd, guard.name)[1] == boot.h.drain.condition_dropin(self.lease.scope))
            expected = str(guard) + ' ' + str(self.transfer.unit_root / self.units[-1])
        require(value['DropInPaths'] == expected)
        require(reload_pending or value['NeedDaemonReload'] == 'no')
        if unit == self.public['timer']: require(value['Unit'] == self.public['renew'])
        return value

    def loaded(self, unit, raw):
        """Compare the selected executable argv against PID 1's loaded view."""
        value = self.observe(unit)
        lines = raw.decode('ascii').splitlines()
        descriptions = [line.partition('=')[2] for line in lines if line.startswith('Description=')]
        if descriptions: require(value['Description'] == descriptions[-1])
        for field in ('ExecStart', 'ExecStartPre'):
            directives = [line.partition('=')[2] for line in lines if line.startswith(field + '=')]
            if not directives:
                if unit != self.apache and unit.endswith('.service'):
                    require(value[field] == '', 'GATEWAY_PUBLIC_UNEXPECTED_LOADED_COMMAND')
                continue  # An Apache overlay may inherit the base command.
            expected = []
            for directive in directives:
                if not directive: expected.clear()
                else: expected.append(' '.join(shlex.split(directive)))
            actual = re.findall(r'\{ path=[^;{}]+ ; argv\[\]=([^;{}]+) ;', value[field])
            require(actual == expected, 'GATEWAY_PUBLIC_LOADED_COMMAND_CHANGED')

    def stopped(self, unit, *, reload_pending=False):
        value = self.observe(unit, reload_pending=reload_pending)
        require((value['ActiveState'], value['SubState']) == ('inactive', 'dead'))
        if unit.endswith('.service'):
            require(value['MainPID'] == value['ControlPID'] == '0' and boot.h.drain._empty_cgroup(unit))
        return value

    def quiet(self, *, reload_pending=False):
        self.held()
        for unit in (*self.public.values(), self.apache): self.stopped(unit, reload_pending=reload_pending)

    def guard_identity(self):
        path = self.transfer.unit_root / (self.apache + '.d/50-hestia-maintenance.conf')
        with f.fs._directory(path.parent) as fd:
            identity, raw = f._file(fd, path.name)
            require(raw == boot.h.drain.condition_dropin(self.lease.scope))
            return identity

    def lock_identity(self):
        with f.fs._directory(self.effect_lock.parent) as fd:
            f.files._private(fd, directory=True)
            handle = os.open('.transaction.lock', f.files.REGULAR, dir_fd=fd)
            try:
                f.files._private(handle, directory=False)
                return {'directory': f._identity(os.fstat(fd)), 'file': f._identity(os.fstat(handle))}
            finally: os.close(handle)

    def prepare(self, *, confirmed):
        require(confirmed is True, 'GATEWAY_PUBLIC_CONSENT_REQUIRED')
        with self.locked():
            self.stopped(self.apache)
            # No fragment mutation, stop, reload, or bundle write during plan.
            sources = {}
            for name, (source, _) in self.transfer.replacements.items():
                with self.transfer._parent(name) as (fd, leaf):
                    identity, raw = f._file(fd, leaf); require(raw == source)
                    sources[name] = {'file': identity, 'directory': f._identity(os.fstat(fd))}
            for name, (source, _) in self.transfer.replacements.items():
                self.loaded(self.apache if name == self.units[-1] else name, source)
            with f.fs._directory(self.root.parent) as parent:
                f.files._private(parent, directory=True); f.fs._absent(parent, self.root.name)
                os.mkdir(self.root.name, 0o700, dir_fd=parent); os.fsync(parent)
            with f.fs._directory(self.root) as fd:
                f.files._private(fd, directory=True)
                plan = {'binding': self.binding(), 'slot': f._identity(os.fstat(fd)),
                        'epoch': MobileBootRuntime.epoch_identity(), 'sources': sources,
                        'maintenance_guard': self.guard_identity(), 'lock': self.lock_identity()}
                f._put(fd, 'plan.json', plan)
            return {'confirmation': f.sha(boot.canonical_bytes(plan)), 'state': 'AWAITING_CONFIRMATION',
                    'services_started': False, 'activity_resumed': False}

    @contextmanager
    def slot(self, confirmation):
        f._digest(confirmation); self.held()
        with f.fs._directory(self.root) as fd:
            f.files._private(fd, directory=True)
            allowed = {'plan.json', 'fragment-plan.json', 'reload.intent.json', 'reload.done.json',
                       *(role + suffix for role in STOP for suffix in ('.intent.json', '.done.json'))}
            require(set(os.listdir(fd)) <= allowed)
            plan = f._optional(fd, 'plan.json')
            require(type(plan) is dict and set(plan) == {'binding', 'slot', 'epoch', 'sources', 'maintenance_guard', 'lock'}
                and plan['binding'] == self.binding() and plan['slot'] == f._identity(os.fstat(fd))
                and plan['epoch'] == MobileBootRuntime.epoch_identity()
                and plan['maintenance_guard'] == self.guard_identity() and plan['lock'] == self.lock_identity()
                and f.sha(boot.canonical_bytes(plan)) == confirmation)
            yield fd, plan
            self.held()
            with f.fs._directory(self.root) as current:
                require(f._identity(os.fstat(current)) == plan['slot'])

    def stop(self, fd, role, confirmation):
        unit = self.public[role]
        intent = f._optional(fd, role + '.intent.json'); done = f._optional(fd, role + '.done.json')
        value = self.observe(unit)
        if intent is None:
            require(done is None)
            intent = {'confirmation': confirmation, 'unit': unit, 'invocation': value['InvocationID']}
            f._put(fd, role + '.intent.json', intent)
        else:
            require(set(intent) == {'confirmation', 'unit', 'invocation'}
                and intent['confirmation'] == confirmation and intent['unit'] == unit)
        if done is not None:
            require(done == intent); self.stopped(unit); return
        if (value['ActiveState'], value['SubState']) != ('inactive', 'dead'):
            require(value['InvocationID'] == intent['invocation'] and re.fullmatch('[a-f0-9]{32}', value['InvocationID'])
                    and value['InvocationID'] != '0' * 32, 'GATEWAY_PUBLIC_INVOCATION_CHANGED')
            self.held()
            boot.h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'stop', '--', unit])
        self.stopped(unit); self.held(); f._put(fd, role + '.done.json', intent)

    def source_files(self, plan):
        require(type(plan['sources']) is dict and set(plan['sources']) == set(self.units))
        for name, (before, _) in self.transfer.replacements.items():
            with self.transfer._parent(name) as (fd, leaf):
                identity, raw = f._file(fd, leaf)
                require(raw == before and plan['sources'][name] ==
                    {'file': identity, 'directory': f._identity(os.fstat(fd))})

    def stop_public(self, confirmation, *, confirmed):
        require(confirmed is True, 'GATEWAY_PUBLIC_CONSENT_REQUIRED')
        with self.locked(), self.slot(confirmation) as (fd, plan):
            require(f._optional(fd, 'fragment-plan.json') is None)
            self.source_files(plan); self.stopped(self.apache)
            for role in STOP:
                self.stop(fd, role, confirmation)
                if role == 'timer': self.stopped(self.public['renew'])
            self.quiet()

    def stop_records(self, fd, confirmation):
        for role in STOP:
            intent = f._optional(fd, role + '.intent.json')
            require(intent is not None and f._optional(fd, role + '.done.json') == intent
                and set(intent) == {'confirmation', 'unit', 'invocation'}
                and intent['confirmation'] == confirmation and intent['unit'] == self.public[role])

    def apply(self, confirmation, *, confirmed):
        require(confirmed is True, 'GATEWAY_PUBLIC_CONSENT_REQUIRED')
        with self.locked(), self.slot(confirmation) as (fd, plan):
            fragment = f._optional(fd, 'fragment-plan.json')
            if fragment is None:
                self.source_files(plan); self.stopped(self.apache)
                for role in STOP:
                    self.stop(fd, role, confirmation)
                    if role == 'timer': self.stopped(self.public['renew'])
                self.quiet()
                if self.transfer.root.exists(): result = self.transfer.check()
                else: result = self.transfer.prepare(confirmed=True)
                require(result['state'] == 'INCOMPLETE' and all(r['state'] == 'PENDING' for r in result['fragments']))
                fragment = {'confirmation': confirmation, 'plan_sha256': result['plan_sha256']}
                f._put(fd, 'fragment-plan.json', fragment)
            require(set(fragment) == {'confirmation', 'plan_sha256'} and fragment['confirmation'] == confirmation)
            self.stop_records(fd, confirmation)
            while True:
                self.quiet(reload_pending=True)
                result = self.transfer.check()
                require(result['plan_sha256'] == fragment['plan_sha256'])
                if result['state'] == 'FRAGMENTS_REPLACED': break
                self.transfer.replace_next(confirmation=fragment['plan_sha256'], confirmed=True)
            owner = {**fragment, 'epoch': plan['epoch']}
            first_reload = f._optional(fd, 'reload.intent.json') is None
            f._put(fd, 'reload.intent.json', owner)
            observed = [self.observe(unit, reload_pending=True) for unit in (*self.units[:-1], self.apache)]
            pending = any(value['NeedDaemonReload'] == 'yes' for value in observed)
            done = f._optional(fd, 'reload.done.json')
            require(done is None or done == owner and not pending)
            if pending or first_reload:
                self.quiet(reload_pending=True); self.held()
                boot.h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
            for name, (_, target) in self.transfer.replacements.items():
                self.loaded(self.apache if name == self.units[-1] else name, target)
            self.quiet(); self.transfer.check(); self.held()
            f._put(fd, 'reload.done.json', owner)
        return self.check(confirmation)

    def check(self, confirmation):
        with self.slot(confirmation) as (fd, plan):
            self.stop_records(fd, confirmation)
            fragment = f._optional(fd, 'fragment-plan.json')
            require(type(fragment) is dict and set(fragment) == {'confirmation', 'plan_sha256'}
                and fragment['confirmation'] == confirmation)
            owner = {**fragment, 'epoch': plan['epoch']}
            require(f._optional(fd, 'reload.intent.json') == f._optional(fd, 'reload.done.json') == owner)
            report = self.transfer.check()
            require(report['state'] == 'FRAGMENTS_REPLACED' and report['plan_sha256'] == fragment['plan_sha256'])
            self.quiet()
            for name, (_, target) in self.transfer.replacements.items():
                self.loaded(self.apache if name == self.units[-1] else name, target)
        return {'state': 'PUBLIC_FRAGMENTS_LOADED_CLOSED', **fragment,
                'services_started': False, 'activity_resumed': False, 'boot_requalified': False}
