"""Exclusive local service identity; no adoption, login, service start or deletion.

Private adapter. Only the distro useradd edits account databases. A durable
attempt precedes that command; incomplete results are never retried blindly.
"""
import grp
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

from installer import http_runtime as h
from installer.model import Receipt, ResourceSpec, StepSpec
from installer.operations import Operation, Recovery, RecoveryDecision

fs, f, p = h.fs, h.f, h.p
ETC = Path('/etc')
USERADD = Path('/usr/sbin/useradd')
NOLOGIN = Path('/usr/sbin/nologin')
DATABASES = {'passwd': 7, 'group': 4, 'shadow': 9, 'gshadow': 4}


class ServiceIdentityError(RuntimeError):
    pass


def require(ok, code='SERVICE_IDENTITY_REJECTED'):
    if not ok: raise ServiceIdentityError(code)


def _file(path, *, private=False, optional=False):
    with fs._directory(path.parent) as fd:
        try: info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            if optional: return None
            raise
        mode = stat.S_IMODE(info.st_mode)
        require(mode in ((0o600, 0o640) if private else (0o600, 0o640, 0o644)))
        return f._read(fd, path.name, info.st_gid, mode=mode, limit=2 * 1024 * 1024)


def _rows(raw, fields, *, unique=True):
    rows = [line.split(':') for line in raw.decode('utf-8').splitlines() if line]
    require(len(rows) <= 20000 and all(len(r) == fields and r[0] and not r[0].startswith(('+', '-')) for r in rows))
    require(not unique or len({r[0] for r in rows}) == len(rows))
    return rows


def _tables():
    return {name: _rows(_file(ETC / name, private=name in ('shadow', 'gshadow')), fields)
            for name, fields in DATABASES.items()}


def _nss(raw):
    found = {}
    for line in raw.decode('utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line: continue
        key, colon, value = line.partition(':')
        if key in DATABASES:
            require(colon and key not in found and tuple(value.split()) in (('files',), ('files', 'systemd')))
            found[key] = value
    require(all(k in found for k in ('passwd', 'group', 'shadow')))


def _hooks():
    # Debian 13 useradd can execute these root hooks. Never run site scripts.
    with fs._directory(ETC) as fd:
        try: os.stat('shadow-maint', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError: return
    with fs._directory(ETC / 'shadow-maint') as fd:
        for name in ('useradd-pre.d', 'useradd-post.d'):
            try: os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: continue
            with fs._directory(ETC / 'shadow-maint' / name) as child:
                require(not os.listdir(child), 'SERVICE_IDENTITY_HOOKS_REJECTED')


class ServiceIdentity:
    def __init__(self, instance):
        require(type(instance) is str and re.fullmatch(r'[a-f0-9]{32}', instance) is not None)
        self.instance = instance
        self.user = 'hst-' + instance[:24]
        self.directory = Path('/var/lib/hestia-identity-' + instance)

    def __repr__(self): return '<ServiceIdentity private local account provisioner>'

    def _plan(self):
        require(os.geteuid() == 0, 'SERVICE_IDENTITY_ROOT_REQUIRED')
        system = h.read_os_release()
        require(system.get('ID') == 'debian' and system.get('VERSION_ID') in ('12', '13'))
        _hooks()
        configs = {name: _file(ETC / name) for name in ('nsswitch.conf', 'login.defs', 'default/useradd')}
        _nss(configs['nsswitch.conf'])
        with fs._directory(self.directory.parent): pass
        with fs._directory(Path('/')) as fd: fs._absent(fd, 'nonexistent')
        tools = {}
        for path in (USERADD, NOLOGIN):
            p._safe_path(path, directory=False, system=True)
            require(path.stat().st_mode & 0o111 == 0o111)
            tools[str(path)] = h._system_file_digest(path)
        return p._json({'version': 1, 'instance': self.instance, 'user': self.user,
            'debian': system['VERSION_ID'], 'tools': tools,
            'configuration': {name: f._sha(raw) for name, raw in configs.items()},
            'policy': 'local-exclusive-locked-v1'})

    def _subids_absent(self, uid=None):
        for name in ('subuid', 'subgid'):
            raw = _file(ETC / name, optional=True)
            if raw is not None:
                require(all(row[0] not in (self.user, str(uid)) for row in _rows(raw, 3, unique=False)))

    def _absent(self):
        tables = _tables()
        require(all(all(row[0] != self.user for row in rows) for rows in tables.values()),
                'SERVICE_IDENTITY_OCCUPIED')
        for getter in (pwd.getpwnam, grp.getgrnam):
            try: getter(self.user)
            except KeyError: continue
            raise ServiceIdentityError('SERVICE_IDENTITY_OCCUPIED')
        self._subids_absent()

    def prepare(self):
        try:
            self._plan(); self._absent()
            with fs._directory(self.directory.parent) as fd: fs._absent(fd, self.directory.name)
        except ServiceIdentityError: raise
        except Exception: raise ServiceIdentityError('SERVICE_IDENTITY_PRECONDITION_FAILED') from None

    def _argv(self):
        return [str(USERADD), '--system', '--user-group', '--no-create-home', '--no-log-init',
            '--home-dir', '/nonexistent', '--shell', str(NOLOGIN), '--password', '!', '--groups', '',
            '--comment', 'HESTIA-' + self.instance, '--expiredate', '', '--inactive', '-1',
            '--key', 'SYS_UID_MIN=100', '--key', 'SYS_UID_MAX=999',
            '--key', 'SYS_GID_MIN=100', '--key', 'SYS_GID_MAX=999',
            '--key', 'CREATE_MAIL_SPOOL=no', '--', self.user]

    def _create_account(self):
        result = subprocess.run(self._argv(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=30, check=False,
            env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C'})
        require(result.returncode == 0, 'SERVICE_IDENTITY_COMMAND_FAILED')

    def _account(self):
        tables = _tables()
        own = {}
        for name, rows in tables.items():
            matches = [r for r in rows if r[0] == self.user]
            require(len(matches) == 1); own[name] = matches[0]
        pw, group, shadow, gs = (own[k] for k in ('passwd', 'group', 'shadow', 'gshadow'))
        require(pw[2].isdigit() and pw[3].isdigit())
        uid, gid = int(pw[2]), int(pw[3])
        require(100 <= uid <= 999 and 100 <= gid <= 999)
        require(pw == [self.user, 'x', str(uid), str(gid), 'HESTIA-' + self.instance, '/nonexistent', str(NOLOGIN)])
        require(group == [self.user, 'x', str(gid), ''] and gs == [self.user, '!', '', ''])
        require(shadow[1] == '!' and (shadow[2] == '' or shadow[2].isdigit()) and shadow[3:] == [''] * 6)
        require(all(r[0] == self.user for r in tables['passwd'] if r[2] == str(uid) or r[3] == str(gid)))
        require(all(r[0] == self.user for r in tables['group'] if r[2] == str(gid) or self.user in r[3].split(',')))
        require(all(r[0] == self.user for r in tables['gshadow'] if self.user in (r[2] + ',' + r[3]).split(',')))
        account = h._identity(self.user)
        require(tuple(account) == (self.user, 'x', uid, gid, pw[4], pw[5], pw[6]))
        require(tuple(grp.getgrnam(self.user)) == (self.user, 'x', gid, []))
        self._subids_absent(uid)
        require(tables == _tables())
        # Only our locked records are journaled, never other users' credentials.
        return {'uid': uid, 'gid': gid, 'records_sha256': f._sha(p._json(own))}

    def create(self, *, confirmed):
        try:
            require(confirmed is True, 'SERVICE_IDENTITY_CONSENT_REQUIRED'); self.prepare()
            plan = self._plan()
            with fs._directory(self.directory.parent) as parent:
                os.mkdir(self.directory.name, 0o700, dir_fd=parent); os.fsync(parent)
            with fs._directory(self.directory) as fd:
                f._write(fd, 'identity.attempt', plan, 0, mode=0o600)
                require(self._plan() == plan); self._absent()
                self._create_account()
                account = self._account(); require(self._plan() == plan)
                f._write(fd, 'created.json', p._json({'version': 1, 'plan_sha256': f._sha(plan), **account}), 0, mode=0o600)
            return self.observe()
        except ServiceIdentityError: raise
        except Exception: raise ServiceIdentityError('SERVICE_IDENTITY_INCOMPLETE') from None

    def observe(self):
        try:
            plan = self._plan(); account = self._account()
            with fs._directory(self.directory) as fd:
                info = os.fstat(fd)
                require(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
                require(set(os.listdir(fd)) == {'identity.attempt', 'created.json'})
                require(f._read(fd, 'identity.attempt', 0, mode=0o600) == plan)
                require(f._json_read(fd, 'created.json', 0, mode=0o600) ==
                        {'version': 1, 'plan_sha256': f._sha(plan), **account})
            return {'state': 'SERVICE_IDENTITY_CREATED', 'plan_sha256': f._sha(plan),
                'identity_sha256': f._sha(p._json(account)), 'dedicated_identity_created': True,
                'login_enabled': False, 'home_created': False, 'services_started': False,
                'application_installed': False, 'system_wiring_verified': False}
        except ServiceIdentityError: raise
        except Exception: raise ServiceIdentityError('SERVICE_IDENTITY_INCOMPLETE') from None

    def account(self):
        self.observe()
        return h._identity(self.user)


class ServiceIdentityOperation(Operation):
    def __init__(self, identity):
        require(type(identity) is ServiceIdentity); self.identity = identity
        super().__init__(StepSpec(name='web.service-identity', operation='web.service-identity.create', module='web',
            boundary='web.service-identity', action='Créer le compte système dédié et verrouillé',
            resources=(ResourceSpec('identity_journal', 'directory', str(identity.directory)),
                       ResourceSpec('service_user', 'external', 'local-user.' + identity.user),
                       ResourceSpec('service_group', 'external', 'local-group.' + identity.user)),
            rollback_supported=False, warnings=('Une identité partielle exige une récupération manuelle.',)))

    def prepare(self, context): self.identity.prepare()
    def apply(self, context):
        value = self.identity.create(confirmed=True)
        return Receipt(created_resources=tuple(r.name for r in self.spec.resources),
            hashes_non_secret=(('service_identity_plan', value['plan_sha256']), ('service_identity', value['identity_sha256'])))
    def validate(self, context):
        try:
            value = self.identity.observe(); hashes = context.evidence['hashes_non_secret']
            return value['plan_sha256'] == hashes['service_identity_plan'] and value['identity_sha256'] == hashes['service_identity']
        except Exception: return False
    def commit(self, context): require(self.validate(context))
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            value = self.identity.observe()
            return Recovery(RecoveryDecision.APPLIED, Receipt(created_resources=tuple(r.name for r in self.spec.resources),
                hashes_non_secret=(('service_identity_plan', value['plan_sha256']), ('service_identity', value['identity_sha256']))))
        except Exception: return Recovery(RecoveryDecision.MANUAL)
