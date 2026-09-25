"""Exclusive, initially gated Apache/FPM provisioning for a dedicated identity.

Private typed adapter: no package installation, identity adoption, source copy,
service start, database mutation, or public route. Existing resources are never
overwritten. An interrupted footprint requires observation, not blind replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import grp
import hashlib
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess

from installer import database_config as fs
from installer import finalization as f
from installer import maintenance as m
from installer import php_transport as p
from installer import system_drain as drain
from installer.model import Receipt, ResourceSpec, StepSpec
from installer.operations import Operation, Recovery, RecoveryDecision
from installer.preflight import read_os_release
from installer.proxy_ingress import ProxyIngress
from installer.web_releases import STORAGE_COMMIT

EXTENSIONS = ('mysqlnd', 'pdo', 'mysqli', 'pdo_mysql', 'ctype', 'iconv', 'fileinfo',
              'mbstring', 'curl', 'dom', 'simplexml', 'xml', 'xmlreader', 'xmlwriter', 'zip', 'gd', 'tokenizer')
DATA = ('sessions', 'tmp', 'upload-tmp', 'imports', 'log')
MODULES = ('mpm_event', 'authz_core', 'proxy', 'proxy_fcgi', 'reqtimeout', 'headers', 'dir', 'mime')
MAX_SYSTEM_FILE = 32 * 1024 * 1024


class HttpRuntimeError(RuntimeError):
    """Non-secret closed diagnostic."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise HttpRuntimeError(code)


def _path(value: Path, *, maximum=180, dots=False) -> str:
    require(isinstance(value, Path), 'HTTP_RUNTIME_INPUT_REJECTED')
    text = str(value)
    require(2 <= len(text) <= maximum and re.fullmatch(r'/[A-Za-z0-9_./-]+' if dots else r'/[A-Za-z0-9_/-]+', text) is not None
            and '..' not in value.parts and '//' not in text, 'HTTP_RUNTIME_PATH_REJECTED')
    return text


@dataclass(frozen=True)
class RuntimeSpec:
    instance: str
    root: Path = field(repr=False)
    webroot: Path = field(repr=False)
    service_user: str = field(repr=False)
    hostname: str = field(repr=False)
    port: int
    php_family: str
    ingress: ProxyIngress | None = field(default=None, repr=False)
    external_uploads: bool = False
    maintenance_directory: Path | None = field(default=None, repr=False)

    def __post_init__(self):
        require(self.ingress is None or type(self.ingress) is ProxyIngress, 'HTTP_RUNTIME_INPUT_REJECTED')
        require(type(self.external_uploads) is bool and
                (self.maintenance_directory is not None) == self.external_uploads, 'HTTP_RUNTIME_INPUT_REJECTED')
        require(type(self.instance) is str and re.fullmatch(r'[a-f0-9]{32}', self.instance) is not None,
                'HTTP_RUNTIME_INPUT_REJECTED')
        root, web = _path(self.root, maximum=75), _path(self.webroot, dots=True)
        require(root != web and not root.startswith(web + '/') and not web.startswith(root + '/')
                and root.startswith('/var/lib/') and (web.startswith('/srv/') or web.startswith('/var/www/')),
                'HTTP_RUNTIME_PATH_REJECTED')
        require(type(self.service_user) is str and re.fullmatch(r'[a-z][a-z0-9_-]{0,30}', self.service_user)
                and self.service_user not in ('root', 'nobody', 'www-data'), 'HTTP_RUNTIME_IDENTITY_REJECTED')
        require(type(self.hostname) is str and len(self.hostname) <= 253 and '.' in self.hostname
                and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                        for label in self.hostname.split('.')), 'HTTP_RUNTIME_HOST_REJECTED')
        require(type(self.port) is int and 1024 <= self.port <= 65535
                and type(self.php_family) is str and self.php_family in ('8.2', '8.4'), 'HTTP_RUNTIME_INPUT_REJECTED')
        if self.external_uploads:
            gate = _path(self.maintenance_directory)
            require(gate.startswith('/var/lib/') and self.maintenance_directory.name == 'maintenance'
                    and not any(self.maintenance_directory == path or path in self.maintenance_directory.parents
                                or self.maintenance_directory in path.parents for path in (self.root, self.webroot)),
                    'HTTP_RUNTIME_PATH_REJECTED')
            require(self.php_family == '8.4', 'HTTP_RUNTIME_PHP_PROFILE_REJECTED')


def _command(argv: list[str]) -> None:
    # All callers build literal commands using validated paths and fixed tools.
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=45, check=False,
        env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'PHP_INI_SCAN_DIR': '',
             'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''})
    require(result.returncode == 0, 'HTTP_RUNTIME_COMMAND_FAILED')


def _identity(user: str):
    account = pwd.getpwnam(user); group = grp.getgrgid(account.pw_gid)
    require(account.pw_uid > 0 and account.pw_gid > 0 and account.pw_shell in ('/usr/sbin/nologin', '/bin/false')
            and set(group.gr_mem) <= {user}
            and set(os.getgrouplist(user, account.pw_gid)) == {account.pw_gid}
            and all(x.pw_name == user for x in pwd.getpwall()
                    if x.pw_gid == account.pw_gid or x.pw_uid == account.pw_uid),
            'HTTP_RUNTIME_IDENTITY_REJECTED')
    return account


def _unit_absent(unit: str) -> None:
    result = subprocess.run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'show',
        '--property=LoadState', '--value', '--', unit], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False,
        env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'SYSTEMD_PAGER': ''})
    require(result.returncode in (0, 1) and result.stdout.strip() == b'not-found', 'HTTP_RUNTIME_UNIT_OCCUPIED')


def _code_digest(root: Path, web_gid: int) -> str:
    """The staged source stays immutable, including its historical uploads tree."""
    pending = [root]; digest = hashlib.sha256(); count = total = 0
    while pending:
        path = pending.pop()
        with fs._directory(path, readable_by=web_gid) as fd:
            for name in sorted(os.listdir(fd)):
                count += 1; require(count <= 10000, 'HTTP_RUNTIME_SOURCE_LIMIT')
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(info.st_uid == 0 and not info.st_mode & 0o7022, 'HTTP_RUNTIME_SOURCE_REJECTED')
                label = (path / name).relative_to(root).as_posix()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path / name); value = 'directory'
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'HTTP_RUNTIME_SOURCE_REJECTED')
                    require(bool(info.st_mode & (stat.S_IRGRP if info.st_gid == web_gid else stat.S_IROTH)),
                            'HTTP_RUNTIME_SOURCE_UNREADABLE')
                    raw = f._read(fd, name, info.st_gid, mode=stat.S_IMODE(info.st_mode), limit=8 * 1024 * 1024)
                    total += len(raw); require(total <= 256 * 1024 * 1024, 'HTTP_RUNTIME_SOURCE_LIMIT')
                    value = f._sha(raw)
                digest.update(p._json({'path': label, 'uid': info.st_uid, 'gid': info.st_gid,
                    'mode': stat.S_IMODE(info.st_mode), 'content': value}))
    return digest.hexdigest()


def _system_file_digest(path: Path) -> dict:
    """System binaries have a separate bounded budget from PHP source inputs."""
    p._safe_path(path, directory=False)
    with fs._directory(path.parent) as fd:
        info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        mode = stat.S_IMODE(info.st_mode)
        data = f._read(fd, path.name, info.st_gid, mode=mode, limit=MAX_SYSTEM_FILE)
        return {'sha256': f._sha(data), 'uid': info.st_uid, 'gid': info.st_gid, 'mode': mode}


class HttpRuntime:
    def __init__(self, spec: RuntimeSpec):
        require(type(spec) is RuntimeSpec, 'HTTP_RUNTIME_INPUT_REJECTED')
        self.spec = spec

    def __repr__(self):
        return '<HttpRuntime private initially gated provisioner>'

    def unit(self, role):
        require(role in ('apache', 'php'), 'HTTP_RUNTIME_INPUT_REJECTED')
        return 'hestia-' + self.spec.instance + '-' + role + '.service'

    def _modules(self):
        return MODULES + (('remoteip', 'authz_host') if self.spec.ingress is not None else ()) + (('alias',) if self.spec.external_uploads else ())

    def _host(self):
        require(os.geteuid() == 0, 'HTTP_RUNTIME_ROOT_REQUIRED')
        system = read_os_release()
        require(system.get('ID') == 'debian' and system.get('VERSION_ID') in ('12', '13'), 'HTTP_RUNTIME_OS_REJECTED')
        expected = {'12': '8.2', '13': '8.4'}[system['VERSION_ID']]
        require(self.spec.php_family == expected, 'HTTP_RUNTIME_PHP_PROFILE_REJECTED')
        account = _identity(self.spec.service_user)
        with fs._directory(self.spec.root.parent, readable_by=account.pw_gid): pass
        with fs._directory(self.spec.webroot, readable_by=account.pw_gid): pass
        with fs._directory(drain.UNIT_ROOT): pass
        for path in (Path('/usr/bin/systemctl'), Path('/usr/sbin/apache2'),
                     Path('/usr/sbin/php-fpm' + expected)):
            p._safe_path(path, directory=False, system=True)
        extension = Path('/usr/lib/php') / {'8.2': '20220829', '8.4': '20240924'}[expected]
        for name in EXTENSIONS:
            p._safe_path(extension / (name + '.so'), directory=False, system=True)
        for name in self._modules():
            p._safe_path(Path('/usr/lib/apache2/modules/mod_' + name + '.so'), directory=False, system=True)
        p._safe_path(Path('/etc/mime.types'), directory=False)
        require(Path('/proc/1/comm').read_text().strip() == 'systemd'
                and Path('/sys/fs/cgroup/cgroup.controllers').is_file(), 'HTTP_RUNTIME_SYSTEMD_REQUIRED')
        if self.spec.external_uploads:
            self._verify_sealed_slot(account)
        return account, extension

    def _verify_sealed_slot(self, account):
        release = f.get_release(STORAGE_COMMIT)
        require(f._runtime_digest(self.spec.webroot) == release.runtime_sha256, 'SOURCE_PIN_MISMATCH')
        directory, gid = self.spec.maintenance_directory.parent, account.pw_gid
        require(directory.name == fs.configuration_slot({'web': {'webroot': str(self.spec.webroot)}}),
                'HTTP_RUNTIME_INSTANCE_MISMATCH')
        # Reconstruct the activation pointer as well as checking its receipt:
        # copying a valid slot elsewhere must not move HTTP maintenance away
        # from the configuration actually used by the Web.
        with fs._directory(directory, readable_by=gid) as conf:
            with fs._directory(self.spec.webroot) as webfd, fs._directory(self.spec.webroot / 'includes') as inc:
                f._completed(conf, webfd, inc, gid, commit=release.commit)
                seal = f._json_read(conf, 'seal.json', gid)
                require(seal['instance'] == self.spec.instance and seal['webroot'] == str(self.spec.webroot),
                        'HTTP_RUNTIME_INSTANCE_MISMATCH')
                database = f._json_read(conf, 'database.json', gid)
                expected = f._documents(self.spec.webroot, directory, gid, database,
                                        f._read(conf, 'db.php', gid), self.spec.instance)
                actual = (f._read(conf, 'seal.json', gid), f._read(webfd, 'install.lock', gid), f._read(inc, 'db.php', gid))
                require(expected == actual, 'HTTP_RUNTIME_CONFIGURATION_BINDING_REQUIRED')

    def _dependency_hashes(self, extension):
        paths = [Path('/usr/bin/systemctl'), Path('/usr/sbin/apache2'),
                 Path('/usr/sbin/php-fpm' + self.spec.php_family), Path('/etc/mime.types')]
        paths += [extension / (name + '.so') for name in EXTENSIONS]
        paths += [Path('/usr/lib/apache2/modules/mod_' + name + '.so') for name in self._modules()]
        return {str(path): _system_file_digest(path) for path in paths}

    def _scope(self, account):
        return m.MaintenanceScope(self.spec.maintenance_directory or self.spec.root / 'maintenance', account.pw_gid, self.spec.instance)

    def _files(self, account, extension):
        spec = self.spec; root, web = str(spec.root), str(spec.webroot)
        gate = str(self._scope(account).directory)
        upload_environment = f'env[HESTIA_UPLOAD_STORAGE] = {root}/data/uploads\n' if spec.external_uploads else ''
        # FPM rejects an empty env[...] value. clear_env=yes and no declaration
        # leave the Web proxy list absent after Apache has canonicalized it.
        trusted_proxies = '' if spec.ingress is not None else 'env[HESTIA_TRUSTED_PROXIES] = 127.0.0.1/32\n'
        access = (spec.ingress.apache_access(spec.hostname, spec.port) if spec.ingress is not None
                  else f'  Require expr "%{{HTTP_HOST}} == \'{spec.hostname}\' || %{{HTTP_HOST}} == \'{spec.hostname}:{spec.port}\'"\n')
        ini = ('[PHP]\nexpose_php=Off\ndisplay_errors=Off\nlog_errors=On\n'
            + 'date.timezone=UTC\nmemory_limit=256M\nmax_execution_time=20\n'
            + 'upload_max_filesize=64M\npost_max_size=66M\nuser_ini.filename=\n'
            + 'extension_dir=' + str(extension) + '\n'
            + ''.join('extension=' + name + '.so\n' for name in EXTENSIONS))
        fpm = f'''[global]
pid = {root}/run/php.pid
error_log = {root}/log/fpm.log
daemonize = no
[hestia]
user = {spec.service_user}
group = {account.pw_gid}
listen = {root}/run/php.sock
listen.owner = {spec.service_user}
listen.group = {account.pw_gid}
listen.mode = 0600
pm = ondemand
pm.max_children = 8
pm.process_idle_timeout = 10s
clear_env = yes
catch_workers_output = yes
security.limit_extensions = .php
request_terminate_timeout = 25s
request_terminate_timeout_track_finished = yes
env[PATH] = /usr/bin:/bin
env[TMPDIR] = {root}/data/tmp
env[TMP] = {root}/data/tmp
env[TEMP] = {root}/data/tmp
env[HOME] = {root}/data/tmp
env[HESTIA_IMPORT_STORAGE] = {root}/data/imports
{trusted_proxies}{upload_environment}php_admin_value[auto_prepend_file] = {gate}/request_guard.php
php_admin_value[session.save_handler] = files
php_admin_value[session.save_path] = {root}/data/sessions
php_admin_value[session.gc_maxlifetime] = 43200
php_admin_value[session.gc_probability] = 0
php_admin_flag[session.use_strict_mode] = on
php_admin_value[sys_temp_dir] = {root}/data/tmp
php_admin_value[upload_tmp_dir] = {root}/data/upload-tmp
php_admin_value[error_log] = {root}/data/log/php.log
'''
        apache = f'''ServerRoot "{root}"
ServerName {spec.hostname}
ServerSignature Off
ServerTokens Prod
DefaultRuntimeDir "{root}/run"
PidFile "{root}/run/apache.pid"
Listen 127.0.0.1:{spec.port}
''' + ''.join(f'LoadModule {name}_module /usr/lib/apache2/modules/mod_{name}.so\n' for name in self._modules()) + f'''User {spec.service_user}
Group #{account.pw_gid}
ErrorLog "{root}/log/apache.log"
LogLevel warn
Timeout 30
ProxyTimeout 30
RequestReadTimeout header=5-10,MinRate=500 body=5-20,MinRate=500
DocumentRoot "{web}"
TypesConfig /etc/mime.types
DirectoryIndex index.php
<Directory />
  AllowOverride None
  Require all denied
</Directory>
<Directory "{web}">
  Options None
  AllowOverride None
  CGIPassAuth On
{access}  <FilesMatch "\\.php$">
    SetHandler "proxy:unix:{root}/run/php.sock|fcgi://localhost/"
  </FilesMatch>
</Directory>
<LocationMatch "(?i)^/(?:[.]|docs(?:/|$)|sql(?:/|$)|pages(?:/|$)|includes(?:/|$)|var(?:/|$)|logs(?:/|$)|tools(?:/|$)|tests(?:/|$)|scripts(?:/|$)|config(?:/|$)|internal(?:/|$)|vendor(?:/|$)|uploads/(?:dar|tmp|ged_documents)(?:/|$))">
  Require all denied
</LocationMatch>
<LocationMatch "(?:^|/)[.]">
  Require all denied
</LocationMatch>
<FilesMatch "(?i)(^install\\.php$|^README\\.md$|^AGENTS\\.md$|^CLAUDE\\.md$|^composer\\.(json|lock)$|^[.]|[.](sql|bak|old|backup|zip|tar|tgz|gz|log|sh)$)">
  Require all denied
</FilesMatch>
<Directory "{web}/uploads">
  <FilesMatch "(?i)[.](php[0-9]*|phtml|phar)$">
    Require all denied
  </FilesMatch>
</Directory>
ProxyFCGISetEnvIf "req_novary('Authorization') =~ m#^(.+)$#" HTTP_AUTHORIZATION "$1"
Header always set X-Frame-Options "SAMEORIGIN"
Header always set X-Content-Type-Options "nosniff"
Header always set Referrer-Policy "strict-origin-when-cross-origin"
Header always set Permissions-Policy "geolocation=(), microphone=(), camera=()"
'''
        if spec.ingress is not None:
            apache += spec.ingress.apache_directives()
        if spec.external_uploads:
            # No generic upload publication. Only existing public image families
            # are mapped; every other data family remains inaccessible directly.
            for family in ('profiles', 'constructeurs', 'distributeurs', 'references'):
                directory = root + '/data/uploads/' + family
                apache += f'''Alias "/uploads/{family}/" "{directory}/"
<Directory "{directory}">
  Options None
  AllowOverride None
  SetHandler none
  Require all denied
  <FilesMatch "(?i)^[^.][^/]*[.](png|jpe?g|webp|gif)$">
    AuthMerging Off
{access}  </FilesMatch>
</Directory>
'''
        files = {spec.root / 'conf/php.ini': ini.encode(), spec.root / 'conf/fpm.conf': fpm.encode(),
                 spec.root / 'conf/apache.conf': apache.encode()}
        starts = {'apache': f'/usr/sbin/apache2 -DFOREGROUND -f {root}/conf/apache.conf',
                  'php': f'/usr/sbin/php-fpm{spec.php_family} -F -c {root}/conf/php.ini -y {root}/conf/fpm.conf'}
        for role, start in starts.items():
            unit = self.unit(role)
            files[drain.UNIT_ROOT / unit] = f'''[Unit]
Description=HESTIA dedicated {role} runtime
[Service]
Type=simple
ExecStart={start}
Environment=PHP_INI_SCAN_DIR=
UMask=0077
Restart=no
KillMode=control-group
SendSIGKILL=yes
TimeoutStopSec=35s
Delegate=no
'''.encode()
            files[drain.UNIT_ROOT / (unit + '.d/50-hestia-maintenance.conf')] = drain.condition_dropin(self._scope(account))
        return files

    def _plan(self, account, extension):
        spec = self.spec
        return p._json({'version': 1, 'instance': spec.instance, 'root': str(spec.root), 'webroot': str(spec.webroot),
            'user': spec.service_user, 'uid': account.pw_uid, 'gid': account.pw_gid,
            'hostname': spec.hostname, 'port': spec.port, 'php_family': spec.php_family,
            'immutable_code_sha256': _code_digest(spec.webroot, account.pw_gid), 'dependencies': self._dependency_hashes(extension),
            'files': {str(path): f._sha(data) for path, data in self._files(account, extension).items()}})

    def prepare(self) -> None:
        try:
            account, extension = self._host()
            with fs._directory(self.spec.root.parent) as fd: fs._absent(fd, self.spec.root.name)
            with fs._directory(drain.UNIT_ROOT) as fd:
                for role in ('apache', 'php'):
                    fs._absent(fd, self.unit(role)); fs._absent(fd, self.unit(role) + '.d')
                    # Refuse a loaded/transient/vendor unit with the same identity.
                    _unit_absent(self.unit(role))
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', self.spec.port))
            if self.spec.external_uploads:
                with fs._directory(self.spec.maintenance_directory.parent) as fd:
                    fs._absent(fd, self.spec.maintenance_directory.name)
            self._plan(account, extension)
        except HttpRuntimeError: raise
        except Exception: raise HttpRuntimeError('HTTP_RUNTIME_PRECONDITION_FAILED') from None

    def _directories(self, account):
        root = self.spec.root
        return {root: (0, account.pw_gid, 0o750), root / 'conf': (0, 0, 0o700),
            root / 'run': (0, account.pw_gid, 0o750), root / 'log': (0, 0, 0o700),
            root / 'data': (0, account.pw_gid, 0o750),
            **{root / 'data' / name: (account.pw_uid, account.pw_gid, 0o700)
               for name in DATA + (('uploads',) if self.spec.external_uploads else ())}}

    def _configtest(self):
        spec = self.spec
        _command(['/usr/sbin/php-fpm' + spec.php_family, '-t', '-c', str(spec.root / 'conf/php.ini'),
                  '-y', str(spec.root / 'conf/fpm.conf')])
        _command(['/usr/sbin/apache2', '-t', '-f', str(spec.root / 'conf/apache.conf')])

    def create(self, *, confirmed: bool) -> dict:
        try:
            require(confirmed is True, 'HTTP_RUNTIME_CONSENT_REQUIRED'); self.prepare()
            account, extension = self._host(); plan = self._plan(account, extension)
            with fs._directory(self.spec.root.parent) as parent:
                os.mkdir(self.spec.root.name, 0o700, dir_fd=parent); os.fsync(parent)
            with fs._directory(self.spec.root) as fd:
                f._write(fd, 'provision.attempt', plan, 0)
                os.fchown(fd, 0, account.pw_gid); os.fchmod(fd, 0o750); os.fsync(fd)
            for path, (uid, gid, mode) in self._directories(account).items():
                if path == self.spec.root: continue
                with fs._directory(path.parent) as parent:
                    os.mkdir(path.name, 0o700, dir_fd=parent)
                    child = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        os.fchown(child, uid, gid); os.fchmod(child, mode); os.fsync(child)
                    finally: os.close(child)
                    os.fsync(parent)
            scope = self._scope(account); scope.create(confirmed=True)
            with scope.acquire(confirmed=True) as lease:
                for role in ('apache', 'php'):
                    with fs._directory(drain.UNIT_ROOT) as parent:
                        os.mkdir(self.unit(role) + '.d', 0o755, dir_fd=parent); os.fsync(parent)
                for path, data in self._files(account, extension).items():
                    with fs._directory(path.parent) as parent:
                        f._write(parent, path.name, data, 0, mode=0o644 if path.is_relative_to(drain.UNIT_ROOT) else 0o640)
                self._configtest(); lease.assert_held()
                _command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
                with fs._directory(self.spec.root) as fd:
                    f._write(fd, 'staged.json', p._json({'version': 1, 'plan_sha256': f._sha(plan),
                        'lease_id': lease.lease_id, 'state': 'HTTP_RUNTIME_STAGED'}), 0)
            return self.observe()
        except HttpRuntimeError: raise
        except Exception: raise HttpRuntimeError('HTTP_RUNTIME_INCOMPLETE') from None

    def observe(self) -> dict:
        try:
            account, extension = self._host(); plan = self._plan(account, extension)
            with fs._directory(self.spec.root) as fd:
                require(f._read(fd, 'provision.attempt', 0) == plan, 'HTTP_RUNTIME_DRIFT')
                saved = f._json_read(fd, 'staged.json', 0)
                require(set(saved) == {'version', 'plan_sha256', 'lease_id', 'state'} and saved['version'] == 1
                        and saved['plan_sha256'] == f._sha(plan) and saved['state'] == 'HTTP_RUNTIME_STAGED',
                        'HTTP_RUNTIME_DRIFT')
            for path, expected in self._directories(account).items():
                with fs._directory(path.parent) as parent:
                    handle = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        info = os.fstat(handle); fs._no_acl(handle)
                        require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == expected, 'HTTP_RUNTIME_DRIFT')
                    finally: os.close(handle)
            for path, data in self._files(account, extension).items():
                with fs._directory(path.parent) as parent:
                    require(f._read(parent, path.name, 0,
                        mode=0o644 if path.is_relative_to(drain.UNIT_ROOT) else 0o640) == data, 'HTTP_RUNTIME_DRIFT')
            scope = self._scope(account)
            state = scope.observe()
            require(state.get('lease_id') == saved['lease_id'], 'HTTP_RUNTIME_GATE_REQUIRED')
            for role in ('apache', 'php'):
                value = drain._show(self.unit(role))
                require(value['LoadState'] == 'loaded' and value['FragmentPath'] == str(drain.UNIT_ROOT / self.unit(role))
                        and value['DropInPaths'] == str(drain.UNIT_ROOT / (self.unit(role) + '.d/50-hestia-maintenance.conf'))
                        and value['NeedDaemonReload'] == 'no' and value['ActiveState'] == 'inactive'
                        and value['SubState'] == 'dead' and value['Job'] == ''
                        and value['Id'] == self.unit(role) and value['Result'] == 'success'
                        and value['KillMode'] == 'control-group' and value['SendSIGKILL'] == 'yes'
                        and value['Delegate'] == 'no' and value['Slice'] == 'system.slice'
                        and value['Type'] == 'simple' and value['Restart'] == 'no'
                        and value['RemainAfterExit'] == value['RefuseManualStop'] == 'no'
                        and value['ControlGroup'] in ('', '/system.slice/' + self.unit(role))
                        and value['MainPID'] == value['ControlPID'] == '0' and drain._empty_cgroup(self.unit(role)),
                        'HTTP_RUNTIME_SERVICE_STATE_REJECTED')
            return {'state': 'HTTP_RUNTIME_STAGED', 'plan_sha256': f._sha(plan),
                'services_staged': 2, 'private_data_directories': len(DATA) + int(self.spec.external_uploads), 'services_started': False,
                'web_php_compatible': self.spec.php_family == '8.4', 'native_session_cleaner_wired': False,
                'system_wiring_verified': False, 'application_installed': False, 'complete_web_backup': False}
        except HttpRuntimeError: raise
        except Exception: raise HttpRuntimeError('HTTP_RUNTIME_INCOMPLETE') from None

    def http_bindings(self) -> tuple[drain.UnitBinding, ...]:
        self.observe(); account, extension = self._host(); files = self._files(account, extension)
        return tuple(drain.UnitBinding(role, f._sha(files[drain.UNIT_ROOT / self.unit(role)])) for role in ('apache', 'php'))


class HttpRuntimeOperation(Operation):
    """Register explicitly in a trusted plan; no mutation of the default registry."""
    def __init__(self, runtime: HttpRuntime):
        require(type(runtime) is HttpRuntime, 'HTTP_RUNTIME_INPUT_REJECTED')
        self.runtime = runtime
        resources = (ResourceSpec('runtime', 'directory', str(runtime.spec.root)),
            *((ResourceSpec('maintenance', 'directory', str(runtime.spec.maintenance_directory)),)
              if runtime.spec.external_uploads else ()),
            *(ResourceSpec(role + '_unit', 'file', str(drain.UNIT_ROOT / runtime.unit(role))) for role in ('apache', 'php')),
            *(ResourceSpec(role + '_dropin', 'directory', str(drain.UNIT_ROOT / (runtime.unit(role) + '.d'))) for role in ('apache', 'php')))
        super().__init__(StepSpec(name='web.http-runtime', operation='web.http-runtime.stage', module='web',
            boundary='web.http-runtime', action='Préparer Apache et PHP-FPM sous maintenance, sans démarrer le Web',
            resources=resources, rollback_supported=False,
            warnings=('Services préparés seulement ; activation Web et nettoyage des sessions restent requis.',)))

    def prepare(self, context): self.runtime.prepare()
    def apply(self, context):
        result = self.runtime.create(confirmed=True)
        return Receipt(created_resources=tuple(r.name for r in self.spec.resources),
                       hashes_non_secret=(('http_runtime_plan', result['plan_sha256']),))
    def validate(self, context):
        try: return self.runtime.observe()['plan_sha256'] == context.evidence['hashes_non_secret']['http_runtime_plan']
        except Exception: return False
    def commit(self, context):
        require(self.validate(context), 'HTTP_RUNTIME_DRIFT')
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            result = self.runtime.observe()
            return Recovery(RecoveryDecision.APPLIED, Receipt(created_resources=tuple(r.name for r in self.spec.resources),
                hashes_non_secret=(('http_runtime_plan', result['plan_sha256']),)))
        except Exception: return Recovery(RecoveryDecision.MANUAL)
