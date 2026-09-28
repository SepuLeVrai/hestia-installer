"""Exclusive MariaDB instance for the qualified virgin Debian 13 package profile.

Uses a new dedicated identity/datadir and a closed unit/configuration. The
distribution instance stays masked. No adoption, repair, restart or SQL rollback.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import time

from installer import system_packages as packages
from installer import http_runtime as h
from installer import systemd_invocation as invocation
from installer.model import ErrorCode, Receipt, ResourceSpec, StepSpec, canonical_bytes, require
from installer.operations import Operation, Recovery, RecoveryDecision
from installer.service_identity import ServiceIdentity
from installer.transaction import _private_directory

fs, f, p = h.fs, h.f, h.p
TOOLS = (Path('/usr/bin/mariadb-install-db'), Path('/usr/sbin/mariadbd'), Path('/usr/bin/mariadb'))


def run(argv, wire=b'', *, directory=Path('/var/lib'), timeout=30, limit=65536):
    output = io.BytesIO()
    code, _, _ = packages.br.capture(argv, wire, directory, output, timeout, limit=limit, allow_stderr=True)
    require(code == 0, ErrorCode.OPERATION_FAILED)
    return output.getvalue()


class MariaDB:
    def __init__(self, instance, package_sha256):
        require(type(instance) is str and re.fullmatch('[a-f0-9]{32}', instance)
                and type(package_sha256) is str and re.fullmatch('[a-f0-9]{64}', package_sha256))
        self.instance, self.package_sha256 = instance, package_sha256
        self.identity = ServiceIdentity(hashlib.sha256(('mariadb:' + instance).encode()).hexdigest()[:32])
        self.root = Path('/var/lib/hestia-mariadb-' + instance)
        self.data = self.root / 'data'
        self.private = self.root / 'private'
        self.runtime_name = 'hestia-sql-' + instance
        self.socket = Path('/run') / self.runtime_name / 'sql.sock'
        self.unit = 'hestia-' + instance + '-mariadb.service'
        self.unit_path = h.drain.UNIT_ROOT / self.unit
        self.authority_user = 'hba_' + instance[:24]
        self.migration_user = 'hbm_' + instance[:24]

    def config(self):
        return f'''[mariadbd]
datadir={self.data}
socket={self.socket}
pid-file=/run/{self.runtime_name}/server.pid
bind-address=127.0.0.1
port=3306
skip-name-resolve
skip-log-bin
general-log=0
slow-query-log=0
local-infile=0
symbolic-links=0
event-scheduler=OFF
secure-file-priv={self.root}/export
innodb-buffer-pool-size=128M
max-connections=64
'''.encode()

    def unit_bytes(self):
        return f'''[Unit]
Description=HESTIA dedicated MariaDB {self.instance}
After=network.target

[Service]
Type=simple
User={self.identity.user}
Group={self.identity.user}
ExecStart=/usr/sbin/mariadbd --defaults-file={self.root}/server.cnf
WorkingDirectory={self.data}
RuntimeDirectory={self.runtime_name}
RuntimeDirectoryMode=0700
UMask=0077
Restart=no
KillMode=control-group
TimeoutStartSec=60
TimeoutStopSec=60
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths={self.data} /run/{self.runtime_name}
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET
LimitCORE=0
TasksMax=256
'''.encode()

    def tools(self):
        require(os.geteuid() == 0 and h.read_os_release().get('ID') == 'debian'
                and h.read_os_release().get('VERSION_ID') == '13', ErrorCode.VALIDATION_FAILED)
        require(Path('/proc/1/comm').read_text().strip() == 'systemd', ErrorCode.VALIDATION_FAILED)
        result = {}
        for path in (*TOOLS, Path('/usr/bin/systemctl'), Path('/usr/bin/busctl'), Path('/usr/bin/setpriv'), Path('/usr/bin/prlimit')):
            p._safe_path(path, directory=False, system=True)
            result[str(path)] = h._system_file_digest(path)
        return result

    def absent(self):
        self.tools()
        with fs._directory(self.root.parent) as fd: fs._absent(fd, self.root.name)
        with fs._directory(self.unit_path.parent) as fd:
            fs._absent(fd, self.unit_path.name); fs._absent(fd, self.unit + '.d')
        h._unit_absent(self.unit)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(('127.0.0.1', 3306))

    def write(self, name, value):
        with _private_directory(self.private, create=False) as fd:
            f._write(fd, name, canonical_bytes(value), 0, mode=0o600)

    def read(self, name):
        with _private_directory(self.private, create=False) as fd:
            return f._json_read(fd, name, 0, mode=0o600)

    def binding(self, context):
        return {'version': 1, 'installation_id': context.installation_id,
                'spec_sha256': f._sha(canonical_bytes(context.spec)), 'packages': self.package_sha256}

    def configuration(self):
        account = self.identity.account(); self.tools()
        with fs._directory(self.root) as fd:
            require(f._read(fd, 'server.cnf', account.pw_gid, mode=0o640) == self.config(), ErrorCode.INVALID_STATE)
            info = os.stat('data', dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == account.pw_uid
                    and info.st_gid == account.pw_gid and stat.S_IMODE(info.st_mode) == 0o700, ErrorCode.INVALID_STATE)
        with fs._directory(self.unit_path.parent) as fd:
            require(f._read(fd, self.unit, 0, mode=0o644) == self.unit_bytes(), ErrorCode.INVALID_STATE)
            fs._absent(fd, self.unit + '.d')
        receipt = self.read('initialized.json')
        require(receipt['tools'] == self.tools(), ErrorCode.INVALID_STATE)
        return account

    def state(self):
        self.configuration()
        expected = {'Id': self.unit, 'LoadState': 'loaded', 'FragmentPath': str(self.unit_path),
            'DropInPaths': '', 'NeedDaemonReload': 'no', 'Type': 'simple', 'Restart': 'no',
            'User': self.identity.user, 'Group': self.identity.user, 'KillMode': 'control-group',
            'NoNewPrivileges': 'yes', 'ProtectSystem': 'strict', 'Environment': '',
            'RootDirectory': '', 'RootImage': '', 'WorkingDirectory': str(self.data), 'CapabilityBoundingSet': '',
            'Transient': 'no', 'DynamicUser': 'no', 'Job': '', 'ControlPID': '0'}
        keys = (*expected, 'ActiveState', 'SubState', 'MainPID', 'ControlGroup', 'ExecStart')
        raw = run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', '--all', 'show', '--property=' + ','.join(keys), '--', self.unit])
        rows = [line.split('=', 1) for line in raw.decode().splitlines()]
        require(len(rows) == len(keys) and all(len(row) == 2 for row in rows), ErrorCode.INVALID_STATE)
        value = dict(rows)
        require(set(value) == set(keys) and all(value[key] == expected[key] for key in expected), ErrorCode.INVALID_STATE)
        command = '/usr/sbin/mariadbd --defaults-file=' + str(self.root / 'server.cnf')
        require(value['ExecStart'].startswith('{ path=/usr/sbin/mariadbd ; argv[]=' + command + ' ; ')
                and value['ExecStart'].count('{') == value['ExecStart'].count('}') == 1, ErrorCode.INVALID_STATE)
        self.empty_arrays()
        return value

    def empty_arrays(self):
        # systemctl's struct-array printer emits no row for empty arrays, even
        # with --all. Ask Properties.Get for explicit typed emptiness instead.
        t = invocation.t
        owner = t._owner(t._reply(run(t._argv('GetNameOwner')), 's'))
        path = '/org/freedesktop/systemd1/unit/' + self.unit.replace('-', '_2d').replace('.', '_2e')
        prefix = t._argv('ListUnits', owner)[:-3]
        for name in ('EnvironmentFiles', 'ExecCondition', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost', 'ExecReload'):
            signature = 'a(sb)' if name == 'EnvironmentFiles' else 'a(sasbttttuii)'
            raw = run(prefix + [path, t.PROPERTIES, 'Get', 'ss', 'org.freedesktop.systemd1.Service', name], timeout=6, limit=16384)
            require(invocation._variant(raw, signature) == [], ErrorCode.INVALID_STATE)
        require(t._reply(run(t._argv('GetNameOwner')), 's') == owner, ErrorCode.INVALID_STATE)

    def running(self):
        value = self.state()
        if value['ActiveState'] == 'inactive' and value['SubState'] == 'dead':
            require(value['MainPID'] == '0' and h.drain._empty_cgroup(self.unit), ErrorCode.INVALID_STATE)
            return False
        require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
                and re.fullmatch('[1-9][0-9]*', value['MainPID'])
                and value['ControlGroup'] == '/system.slice/' + self.unit, ErrorCode.INVALID_STATE)
        process = Path('/proc') / value['MainPID']; account = self.identity.account()
        require(process.stat().st_uid == account.pw_uid
                and os.readlink(process / 'exe') == str(TOOLS[1])
                and process.joinpath('cmdline').read_bytes().split(b'\0') == [str(TOOLS[1]).encode(),
                    ('--defaults-file=' + str(self.root / 'server.cnf')).encode(), b'']
                and '0::/system.slice/' + self.unit in process.joinpath('cgroup').read_text().splitlines(), ErrorCode.INVALID_STATE)
        return True

    def sql(self, statement):
        require(self.running(), ErrorCode.VALIDATION_FAILED)
        argv = [str(TOOLS[2]), '--no-defaults', '--protocol=socket', '--socket=' + str(self.socket),
                '--user=root', '--batch', '--raw', '--skip-column-names', '--connect-timeout=5']
        return run(argv, (statement + '\n').encode(), directory=self.root, timeout=20).decode().strip()

    def probe(self):
        # MariaDB boolean system-variable items can serialize as bare OFF/ON
        # inside JSON_OBJECT. Cast to text and accept only explicit false values.
        value = json.loads(self.sql("SELECT JSON_OBJECT('user',CURRENT_USER(),'data',@@datadir,'bind',@@bind_address,'port',@@port,'binlog',CAST(@@log_bin AS CHAR),'general',CAST(@@general_log AS CHAR),'slow',CAST(@@slow_query_log AS CHAR),'local',CAST(@@local_infile AS CHAR))"))
        for name in ('binlog', 'general', 'slow', 'local'):
            require(value[name] in ('OFF', '0'), ErrorCode.VALIDATION_FAILED)
            value[name] = 0
        require(value == {'user': 'root@localhost', 'data': str(self.data) + '/', 'bind': '127.0.0.1', 'port': 3306,
                          'binlog': 0, 'general': 0, 'slow': 0, 'local': 0}, ErrorCode.VALIDATION_FAILED)


class Initialization(Operation):
    def __init__(self, runtime):
        self.runtime = runtime
        super().__init__(StepSpec(name='system.mariadb.initialize', operation='system.mariadb.initialize', module='web',
            boundary='system.mariadb.initialize', action='Initialiser une instance MariaDB privée neuve',
            dependencies=('system.mariadb.identity',), resources=(ResourceSpec('sql_root', 'directory', str(runtime.root)),
                ResourceSpec('sql_unit', 'file', str(runtime.unit_path))),
            warnings=('Paquets liés : ' + runtime.package_sha256, 'Une initialisation interrompue reste manuelle ; aucun répertoire existant adopté.')))

    def prepare(self, context): self.runtime.absent(); self.runtime.identity.account()

    def receipt(self, context):
        return Receipt(created_resources=('sql_root', 'sql_unit'), hashes_non_secret=(('sql_binding', f._sha(canonical_bytes(self.runtime.binding(context)))),))

    def apply(self, context):
        self.prepare(context); r = self.runtime; account = r.identity.account()
        with fs._directory(r.root.parent) as fd:
            os.mkdir(r.root.name, 0o755, dir_fd=fd); os.chmod(r.root.name, 0o755, dir_fd=fd); os.fsync(fd)
        with fs._directory(r.root) as fd:
            for name in ('private', 'data', 'export'):
                os.mkdir(name, 0o700, dir_fd=fd)
                if name != 'private': os.chown(name, account.pw_uid, account.pw_gid, dir_fd=fd)
            os.fsync(fd)
        r.write('initialize.attempt', r.binding(context))
        runtime = p.PhpRuntime(Path('/usr/bin/php8.4'), Path('/usr/lib/php/20240924'), account.pw_uid, account.pw_gid, r.root, r.private)
        argv = packages.br.system_command(runtime, TOOLS[0], ['--no-defaults', '--auth-root-authentication-method=socket',
            '--auth-root-socket-user=root', '--skip-test-db', '--skip-name-resolve', '--datadir=' + str(r.data),
            '--innodb-use-native-aio=0', '--innodb-buffer-pool-size=32M'])
        run(argv, directory=r.root, timeout=180)
        # The initializer has exited; make its new tree durable before the receipt.
        count = 0
        for directory, dirs, files in os.walk(r.data, followlinks=False):
            for name in (*dirs, *files):
                count += 1; require(count <= 4096, ErrorCode.INVALID_STATE)
                path = Path(directory) / name; info = path.lstat()
                require((stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1))
                        and info.st_uid == account.pw_uid and info.st_gid == account.pw_gid, ErrorCode.INVALID_STATE)
                handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
                try: os.fsync(handle)
                finally: os.close(handle)
        handle = os.open(r.data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try: os.fsync(handle)
        finally: os.close(handle)
        with fs._directory(r.root) as fd: f._write(fd, 'server.cnf', r.config(), account.pw_gid, mode=0o640); os.fsync(fd)
        with fs._directory(r.unit_path.parent) as fd: f._write(fd, r.unit, r.unit_bytes(), 0, mode=0o644); os.fsync(fd)
        run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        r.write('initialized.json', {'binding': r.binding(context), 'tools': r.tools()})
        return self.receipt(context)

    def validate(self, context):
        r = self.runtime; r.configuration()
        return r.read('initialized.json')['binding'] == r.binding(context) and r.read('initialize.attempt') == r.binding(context) and self.receipt(context).as_dict() == context.evidence

    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            from dataclasses import replace
            candidate = replace(context, evidence=self.receipt(context).as_dict())
            require(self.validate(candidate), ErrorCode.INVALID_STATE)
            return Recovery(RecoveryDecision.APPLIED, self.receipt(context))
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class Start(Operation):
    def __init__(self, runtime):
        self.runtime = runtime
        super().__init__(StepSpec(name='system.mariadb.start', operation='system.mariadb.start', module='web',
            boundary='system.mariadb.start', action='Démarrer MariaDB uniquement sur la boucle locale',
            dependencies=('system.mariadb.initialize',), resources=(ResourceSpec('sql_service', 'external', runtime.unit, preexisting=True),),
            warnings=('Paquets liés : ' + runtime.package_sha256, 'Aucune activation au boot ; aucun restart ou démasquage du service Debian.')))

    def receipt(self, context): return Receipt(hashes_non_secret=(('sql_start', f._sha(canonical_bytes(self.runtime.binding(context)))),))
    def prepare(self, context):
        r = self.runtime; require(not r.running(), ErrorCode.MANUAL_ACTION_REQUIRED)
        with _private_directory(r.private, create=False) as fd: fs._absent(fd, 'start.attempt')
    def apply(self, context):
        self.prepare(context); r = self.runtime; r.write('start.attempt', r.binding(context))
        run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', '--job-mode=fail', 'start', '--', r.unit], timeout=70)
        for attempt in range(40):
            try: r.probe(); break
            except Exception:
                if attempt == 39: raise
                time.sleep(.25)
        return self.receipt(context)
    def validate(self, context):
        self.runtime.probe()
        return self.runtime.read('start.attempt') == self.runtime.binding(context) and self.receipt(context).as_dict() == context.evidence
    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            require(self.runtime.read('start.attempt') == self.runtime.binding(context), ErrorCode.INVALID_STATE)
            self.runtime.probe(); return Recovery(RecoveryDecision.APPLIED, self.receipt(context))
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class Authority(Operation):
    def __init__(self, runtime):
        self.runtime = runtime
        super().__init__(StepSpec(name='system.mariadb.authority', operation='system.mariadb.authority', module='web',
            boundary='system.mariadb.authority', action="Créer l'autorité SQL locale du serveur neuf",
            dependencies=('system.mariadb.start',), resources=(ResourceSpec('sql_authority', 'external', 'sql-user.' + runtime.authority_user),),
            requires_secrets=('sql.authority_password',), warnings=('Paquets liés : ' + runtime.package_sha256,
                "Compte d'administration SQL limité à 127.0.0.1 ; conserver son mot de passe pour la préparation Web.")))

    def account(self): return "'" + self.runtime.authority_user + "'@'127.0.0.1'"
    def receipt(self, context):
        return Receipt(created_resources=('sql_authority',), hashes_non_secret=(('sql_authority', f._sha(canonical_bytes(self.runtime.binding(context)))),))
    def prepare(self, context):
        r = self.runtime; r.probe()
        with _private_directory(r.private, create=False) as fd: fs._absent(fd, 'authority.attempt')
        require(r.sql("SELECT COUNT(*) FROM mysql.global_priv WHERE User='" + r.authority_user + "'") == '0', ErrorCode.VALIDATION_FAILED)
        require(r.sql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ('mysql','sys','performance_schema','information_schema')") == '0', ErrorCode.VALIDATION_FAILED)
    def observe(self, context):
        r = self.runtime; r.probe()
        require(r.read('authority.attempt') == r.binding(context) and r.read('authority-created.json') == r.binding(context), ErrorCode.INVALID_STATE)
        require(r.sql("SELECT COUNT(*) FROM mysql.global_priv WHERE User='" + r.authority_user + "'") == '1', ErrorCode.INVALID_STATE)
        grants = r.sql('SHOW GRANTS FOR ' + self.account())
        require(grants.startswith('GRANT ALL PRIVILEGES ON *.* TO `' + r.authority_user + '`@`127.0.0.1`')
                and grants.endswith('WITH GRANT OPTION') and '\n' not in grants, ErrorCode.INVALID_STATE)
    def apply(self, context):
        self.prepare(context); r = self.runtime
        password = context.require_secret('sql.authority_password')
        require(20 <= len(password.encode()) <= 1024, ErrorCode.SECRET_REJECTED)
        digest = '*' + hashlib.sha1(hashlib.sha1(password.encode()).digest()).hexdigest().upper()
        r.write('authority.attempt', r.binding(context))
        r.sql('CREATE USER ' + self.account() + " IDENTIFIED BY PASSWORD '" + digest + "';\nGRANT ALL PRIVILEGES ON *.* TO " + self.account() + ' WITH GRANT OPTION;')
        require(r.sql("SELECT JSON_UNQUOTE(JSON_EXTRACT(Priv,'$.authentication_string')) FROM mysql.global_priv WHERE User='" + r.authority_user + "' AND Host='127.0.0.1'") == digest, ErrorCode.VALIDATION_FAILED)
        r.write('authority-created.json', r.binding(context)); self.observe(context)
        return self.receipt(context)
    def validate(self, context): self.observe(context); return self.receipt(context).as_dict() == context.evidence
    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try: self.observe(context); return Recovery(RecoveryDecision.APPLIED, self.receipt(context))
        except Exception: return Recovery(RecoveryDecision.MANUAL)
