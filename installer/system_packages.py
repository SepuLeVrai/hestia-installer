"""Private official Debian package acquisition and fresh installation.

Uses an isolated authenticated APT configuration. Never upgrades/removes an
existing package, changes host repositories, or starts the default services.
The sealed archive plan must be explicitly selected before host installation.
"""
import io
import os
from pathlib import Path
import re
import stat
import time

from installer.model import Receipt, ResourceSpec, StepSpec
from installer.operations import Operation, Recovery, RecoveryDecision

from installer import backup_runtime as br
from installer import http_runtime as h

fs, f, p = h.fs, h.f, h.p
KEYRING = Path('/usr/share/keyrings/debian-archive-keyring.gpg')
STATUS = Path('/var/lib/dpkg/status')
POLICY = Path('/usr/sbin/policy-rc.d')
POLICY_BYTES = b'#!/bin/sh\nexit 101\n'
PACKAGE = r'[a-z0-9][a-z0-9+.-]*(?::(?:amd64|arm64|all))?'
VERSION = r'[0-9][A-Za-z0-9.+:~_-]{0,127}'
MAX_PACKAGES = 300
MAX_BYTES = 1024 * 1024 * 1024
MAX_AGE = 24 * 60 * 60


class SystemPackagesError(RuntimeError):
    pass


def require(ok, code='SYSTEM_PACKAGES_REJECTED'):
    if not ok: raise SystemPackagesError(code)


def command(argv, directory, *, timeout=60, limit=16 * 1024 * 1024):
    output = io.BytesIO()
    code, _, _ = br.capture(argv, b'', directory, output, timeout, limit=limit, allow_stderr=True)
    require(code == 0, 'SYSTEM_PACKAGES_COMMAND_FAILED')
    return output.getvalue()


def installed(raw):
    result = {}
    for row in raw.decode().splitlines():
        fields = row.split('\t'); require(len(fields) == 4)
        name, version, architecture, state = fields
        require(re.fullmatch(PACKAGE, name) and re.fullmatch(VERSION, version)
                and architecture in ('amd64', 'arm64', 'all') and name not in result)
        require(state == 'install ok installed', 'SYSTEM_PACKAGES_DPKG_NOT_CLEAN')
        result[name] = {'version': version, 'architecture': architecture}
    require(0 < len(result) <= 20000)
    return result


def simulation(raw, before):
    result = {}
    for line in raw.decode().splitlines():
        if line.startswith(('Remv ', 'Purg ')): raise SystemPackagesError('SYSTEM_PACKAGES_REMOVAL_REFUSED')
        if not line.startswith('Inst '): continue
        match = re.fullmatch(r'Inst (' + PACKAGE + r') \((' + VERSION + r') [^\r\n]+\)', line)
        require(match is not None, 'SYSTEM_PACKAGES_UPGRADE_REFUSED')
        name, version = match.groups()
        require(name not in result and all(key.split(':')[0] != name.split(':')[0] for key in before),
                'SYSTEM_PACKAGES_UPGRADE_REFUSED')
        result[name] = version
    require(0 < len(result) <= MAX_PACKAGES, 'SYSTEM_PACKAGES_EMPTY_OR_LARGE_PLAN')
    return result


def _digest(path, *, limit=128 * 1024 * 1024):
    with fs._directory(path.parent) as fd:
        info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        mode = stat.S_IMODE(info.st_mode)
        require(mode in (0o600, 0o640, 0o644))
        return f._sha(f._read(fd, path.name, info.st_gid, mode=mode, limit=limit))


def _keyring():
    # Trixie's official compatibility link is the only accepted link here.
    # Archives and every other protected file still require a regular inode.
    with fs._directory(KEYRING.parent) as fd:
        info = os.stat(KEYRING.name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            target = KEYRING.with_suffix('.pgp')
            require(info.st_uid == info.st_gid == 0 and info.st_nlink == 1
                    and os.readlink(KEYRING.name, dir_fd=fd) == target.name)
            return {'layout': 'official-pgp-link', 'sha256': _digest(target)}
        return {'layout': 'regular-gpg', 'sha256': _digest(KEYRING)}


def _journal(fd, name, value):
    """Exclusive, durable metadata with a bound separate from SQL secrets."""
    require(name in ('acquire.attempt', 'ready.json', 'install.attempt', 'installed.json'))
    raw = p._json(value); require(len(raw) <= 4 * 1024 * 1024)
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(out, raw[offset:]); require(count > 0); offset += count
        os.fchown(out, 0, 0); os.fchmod(out, 0o600); fs._no_acl(out); os.fsync(out)
    finally: os.close(out)
    os.fsync(fd)


def _dpkg_line(line):
    if '..' in line.split('/'): return False
    return line in ('no-debsig', 'log /var/log/dpkg.log', 'force-unsafe-io') or bool(re.fullmatch(
        r'path-(?:exclude|include)(?:=| +)/usr/share/(?:doc|man|info|locale|gnome/help|linda|lintian/overrides|omf)/[A-Za-z0-9_.*?/-]+', line))


def _dpkg_config():
    """Reject local dpkg hooks and force flags; restore fsync explicitly."""
    paths = [Path('/etc/dpkg/dpkg.cfg')]
    with fs._directory(Path('/etc/dpkg/dpkg.cfg.d')) as fd:
        paths += [Path('/etc/dpkg/dpkg.cfg.d') / name for name in sorted(os.listdir(fd))]
    result = {}
    for path in paths:
        with fs._directory(path.parent) as fd:
            info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_IMODE(info.st_mode) in (0o600, 0o640, 0o644))
            raw = f._read(fd, path.name, info.st_gid, mode=stat.S_IMODE(info.st_mode), limit=65536)
        for line in raw.decode().splitlines():
            line = line.strip()
            if not line or line.startswith('#'): continue
            require(_dpkg_line(line),
                    'SYSTEM_PACKAGES_DPKG_CONFIGURATION_REJECTED')
        result[str(path)] = f._sha(raw)
    return result


class SystemPackages:
    def __init__(self, instance, *, nginx=False):
        require(type(instance) is str and re.fullmatch(r'[a-f0-9]{32}', instance) and type(nginx) is bool)
        self.instance, self.nginx = instance, nginx
        self.directory = Path('/var/lib/hestia-packages-' + instance)

    def __repr__(self): return '<SystemPackages private official fresh dependency provisioner>'

    def _host(self):
        require(os.geteuid() == 0)
        osinfo = h.read_os_release(); major = osinfo.get('VERSION_ID')
        require(osinfo.get('ID') == 'debian' and major in ('12', '13'))
        tools = {}
        for path in ('/usr/bin/apt-get', '/usr/bin/dpkg-query', '/usr/bin/dpkg-deb', '/usr/bin/dpkg', '/usr/bin/env', '/usr/bin/systemctl'):
            p._safe_path(Path(path), directory=False, system=True)
            tools[path] = h._system_file_digest(Path(path))
        with fs._directory(self.directory.parent): pass
        architecture = command(['/usr/bin/dpkg', '--print-architecture'], self.directory.parent).decode().strip()
        require(architecture in ('amd64', 'arm64'))
        require(not command(['/usr/bin/dpkg', '--print-foreign-architectures'], self.directory.parent).strip())
        require(not command(['/usr/bin/dpkg', '--audit'], self.directory.parent).strip())
        return {'debian': major, 'suite': {'12': 'bookworm', '13': 'trixie'}[major],
                'php': {'12': '8.2', '13': '8.4'}[major], 'architecture': architecture,
                'tools': tools, 'keyring': _keyring(), 'dpkg_configuration': _dpkg_config()}

    def _installed(self):
        return installed(command(['/usr/bin/dpkg-query', '--show',
            '--showformat=${binary:Package}\t${Version}\t${Architecture}\t${Status}\n'], self.directory.parent))

    def _packages(self, host):
        family = 'php' + host['php']
        return ('apache2', 'mariadb-server', family + '-cli', family + '-fpm', family + '-mysql',
            family + '-mbstring', family + '-curl', family + '-xml', family + '-zip', family + '-gd',
            'passwd', 'util-linux', 'ca-certificates') + (('nginx',) if self.nginx else ())

    def _units(self, host):
        return ('apache2.service', 'apache-htcacheclean.service', 'php' + host['php'] + '-fpm.service',
            'mariadb.service', 'mysql.service', 'mysqld.service', 'mariadb.socket') + (('nginx.service',) if self.nginx else ())

    def _configuration(self, host):
        root = self.directory
        options = {'Dir::Etc': str(root / 'etc'), 'Dir::Etc::main': str(root / 'etc/empty.conf'),
            'Dir::Etc::parts': str(root / 'etc/parts'), 'Dir::Etc::sourcelist': str(root / 'etc/sources.list'),
            'Dir::Etc::sourceparts': str(root / 'etc/parts'), 'Dir::Etc::preferences': str(root / 'etc/empty.conf'),
            'Dir::Etc::preferencesparts': str(root / 'etc/parts'), 'Dir::Etc::netrc': str(root / 'etc/empty.conf'),
            'Dir::Etc::netrcparts': str(root / 'etc/parts'), 'Dir::Etc::trusted': str(KEYRING),
            'Dir::Etc::trustedparts': str(root / 'etc/parts'), 'Dir::State': str(root / 'state'),
            'Dir::State::status': str(STATUS), 'Dir::State::lists': str(root / 'state/lists'),
            'Dir::State::extended_states': str(root / 'state/extended_states'),
            'Dir::Cache': str(root / 'cache'), 'Dir::Cache::archives': str(root / 'cache/archives'),
            'Dir::Cache::pkgcache': '', 'Dir::Cache::srcpkgcache': '', 'Dir::Log': str(root / 'log'),
            'APT::Architecture': host['architecture'], 'APT::Install-Recommends': 'false',
            'APT::Install-Suggests': 'false', 'APT::Get::AllowUnauthenticated': 'false',
            'Acquire::AllowInsecureRepositories': 'false', 'Acquire::AllowWeakRepositories': 'false',
            'Acquire::AllowDowngradeToInsecureRepositories': 'false', 'Acquire::Check-Date': 'true',
            'Acquire::Check-Valid-Until': 'true', 'Acquire::https::Verify-Peer': 'true',
            'Acquire::https::Verify-Host': 'true', 'Acquire::https::AllowRedirect': 'false',
            'Acquire::https::Proxy': 'DIRECT', 'Acquire::http::Proxy': 'DIRECT',
            'Acquire::https::Timeout': '30', 'Acquire::Retries': '0', 'Acquire::Languages': 'none',
            'APT::Update::Error-Mode': 'any', 'DPkg::Lock::Timeout': '0', 'Dpkg::Use-Pty': 'false'}
        config = ''.join(key + ' "' + value + '";\n' for key, value in options.items())
        config += 'DPkg::Options { "--force-confdef"; "--force-confold"; "--refuse-unsafe-io"; };\n'
        suite, arch = host['suite'], host['architecture']
        sources = ''.join('deb [arch=' + arch + ' signed-by=' + str(KEYRING) + '] https://deb.debian.org/'
            + archive + ' ' + release + ' main\n' for archive, release in
            (('debian', suite), ('debian', suite + '-updates'), ('debian-security', suite + '-security')))
        return {'apt.conf': config.encode(), 'etc/sources.list': sources.encode(), 'etc/empty.conf': b''}

    def _apt(self, args, *, timeout=300):
        try:
            return command(['/usr/bin/env', 'APT_CONFIG=' + str(self.directory / 'apt.conf'),
                'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get', *args], self.directory, timeout=timeout)
        except Exception:
            stage = 'UPDATE' if args == ['update'] else 'SIMULATION' if '--simulate' in args else 'DOWNLOAD' if '--download-only' in args else 'INSTALL'
            raise SystemPackagesError('SYSTEM_PACKAGES_APT_' + stage + '_FAILED') from None


    def prepare(self):
        try:
            host = self._host(); before = self._installed()
            require(all(not re.fullmatch(r'(?:apache2|nginx|mariadb-server.*|mysql-server.*|php.*-fpm)', key.split(':')[0])
                        for key in before), 'SYSTEM_PACKAGES_EXISTING_SERVICE_REFUSED')
            with fs._directory(self.directory.parent) as fd: fs._absent(fd, self.directory.name)
            with fs._directory(Path('/var/lib')) as fd: fs._absent(fd, 'mysql')
            return host, before
        except SystemPackagesError: raise
        except Exception: raise SystemPackagesError('SYSTEM_PACKAGES_PRECONDITION_FAILED') from None

    def _archives(self, proposed, architecture):
        directory = self.directory / 'cache/archives'; found = {}; total = 0
        with fs._directory(directory) as fd:
            names = sorted(os.listdir(fd))
        for name in names:
            if name in ('partial', 'lock'): continue
            require(re.fullmatch(r'[A-Za-z0-9_+.%:~_-]+\.deb', name))
            path = directory / name; size = path.lstat().st_size
            total += size; require(total <= MAX_BYTES)
            digest = _digest(path)
            fields = command(['/usr/bin/dpkg-deb', '--show', '--showformat=${Package}\t${Version}\t${Architecture}\n', str(path)], self.directory).decode().strip().split('\t')
            require(len(fields) == 3); package, version, archive_arch = fields
            require(re.fullmatch(PACKAGE, package) and ':' not in package and re.fullmatch(VERSION, version))
            require(archive_arch in (architecture, 'all') and package not in found
                    and proposed.get(package, proposed.get(package + ':' + archive_arch)) == version)
            found[package] = {'file': name, 'version': version, 'architecture': archive_arch, 'bytes': size, 'sha256': digest}
        require(len(found) == len(proposed))
        return found

    def _indices(self):
        directory = self.directory / 'state/lists'
        with fs._directory(directory) as fd: names = sorted(os.listdir(fd))
        names = [name for name in names if name not in ('partial', 'auxfiles', 'lock')]
        require(3 <= len(names) <= 64 and len([name for name in names if name.endswith('_InRelease')]) == 3)
        require(sum((directory / name).lstat().st_size for name in names) <= 256 * 1024 * 1024)
        return {name: _digest(directory / name) for name in names}

    def acquire(self, *, confirmed):
        try:
            require(confirmed is True, 'SYSTEM_PACKAGES_CONSENT_REQUIRED'); host, before = self.prepare()
            with fs._directory(self.directory.parent) as fd:
                os.mkdir(self.directory.name, 0o755, dir_fd=fd); os.fsync(fd)
            with fs._directory(self.directory) as fd:
                _journal(fd, 'acquire.attempt', {'host': host, 'before': before, 'nginx': self.nginx})
            for name in ('etc', 'etc/parts', 'state', 'state/lists', 'cache', 'cache/archives', 'log'):
                path = self.directory / name
                with fs._directory(path.parent) as fd: os.mkdir(path.name, 0o755, dir_fd=fd); os.fsync(fd)
            for name, raw in self._configuration(host).items():
                path = self.directory / name
                with fs._directory(path.parent) as fd: f._write(fd, path.name, raw, 0, mode=0o644)
            self._apt(['update'])
            proposed = simulation(self._apt(['--simulate', '--no-remove', '--no-install-recommends', 'install', *self._packages(host)]), before)
            exact = [name + '=' + version for name, version in sorted(proposed.items())]
            self._apt(['--yes', '--download-only', '--no-remove', '--no-install-recommends', 'install', *exact])
            require(self._installed() == before and self._host() == host, 'SYSTEM_PACKAGES_HOST_CHANGED')
            manifest = {'version': 1, 'created_unix': int(time.time()), 'host': host, 'before': before, 'requested': list(self._packages(host)),
                'proposed': proposed, 'archives': self._archives(proposed, host['architecture']), 'indices': self._indices()}
            with fs._directory(self.directory) as fd: _journal(fd, 'ready.json', manifest)
            return self.observe()
        except SystemPackagesError: raise
        except Exception: raise SystemPackagesError('SYSTEM_PACKAGES_ACQUISITION_INCOMPLETE') from None

    def _manifest(self):
        with fs._directory(self.directory) as fd:
            value = f._json_read(fd, 'ready.json', 0, mode=0o600, limit=4 * 1024 * 1024)
            attempt = f._json_read(fd, 'acquire.attempt', 0, mode=0o600, limit=4 * 1024 * 1024)
        host = self._host()
        require(value['version'] == 1 and value['host'] == host and value['requested'] == list(self._packages(host)))
        require(attempt == {'host': host, 'before': value['before'], 'nginx': self.nginx})
        with fs._directory(self.directory / 'etc/parts') as fd:
            require(not os.listdir(fd), 'SYSTEM_PACKAGES_CONFIGURATION_DRIFT')
        for name, expected in self._configuration(host).items():
            path = self.directory / name
            with fs._directory(path.parent) as fd: require(f._read(fd, path.name, 0, mode=0o644) == expected)
        require(self._archives(value['proposed'], host['architecture']) == value['archives'] and self._indices() == value['indices'])
        return value

    def observe(self):
        try:
            value = self._manifest()
            require(type(value['created_unix']) is int and 0 <= time.time() - value['created_unix'] <= MAX_AGE,
                    'SYSTEM_PACKAGES_PLAN_EXPIRED')
            require(self._installed() == value['before'], 'SYSTEM_PACKAGES_HOST_CHANGED')
            with fs._directory(self.directory) as fd: fs._absent(fd, 'install.attempt')
            return {'state': 'PACKAGE_ARCHIVES_READY', 'plan_sha256': f._sha(p._json(value)),
                'packages': len(value['archives']), 'web_php_compatible': value['host']['debian'] == '13',
                'packages_installed': False, 'application_installed': False}
        except SystemPackagesError: raise
        except Exception: raise SystemPackagesError('SYSTEM_PACKAGES_ACQUISITION_INCOMPLETE') from None

    def _policy(self):
        with fs._directory(POLICY.parent) as fd:
            try: os.stat(POLICY.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: return False
            require(f._read(fd, POLICY.name, 0, mode=0o755) == POLICY_BYTES, 'SYSTEM_PACKAGES_POLICY_OCCUPIED')
        return True

    def _masks(self, host, *, absent=False):
        with fs._directory(h.drain.UNIT_ROOT) as fd:
            for unit in self._units(host):
                if absent:
                    fs._absent(fd, unit); h._unit_absent(unit)
                else:
                    info = os.stat(unit, dir_fd=fd, follow_symlinks=False)
                    require(stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                            and os.readlink(unit, dir_fd=fd) == '/dev/null')
                    expected = {'LoadState': 'masked', 'ActiveState': 'inactive', 'Job': ''}
                    if unit.endswith('.service'): expected.update(MainPID='0', ControlPID='0')
                    raw = command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'show',
                        '--property=' + ','.join(expected), '--', unit], self.directory, limit=16384)
                    rows = [line.split('=', 1) for line in raw.decode().splitlines()]
                    require(len(rows) == len(expected) and dict(rows) == expected and h.drain._empty_cgroup(unit))


    def install(self, *, confirmed, plan_sha256):
        try:
            require(confirmed is True, 'SYSTEM_PACKAGES_CONSENT_REQUIRED')
            report = self.observe(); require(plan_sha256 == report['plan_sha256'], 'SYSTEM_PACKAGES_PLAN_MISMATCH')
            value = self._manifest(); host = value['host']
            require(Path('/proc/1/comm').read_text().strip() == 'systemd')
            self._masks(host, absent=True); preexisting_policy = self._policy()
            with fs._directory(Path('/var/lib')) as fd: fs._absent(fd, 'mysql')
            exact = [name + '=' + version for name, version in sorted(value['proposed'].items())]
            require(simulation(self._apt(['--simulate', '--no-remove', '--no-install-recommends', 'install', *exact]), value['before']) == value['proposed'])
            require(self._installed() == value['before'])
            with fs._directory(self.directory) as fd:
                _journal(fd, 'install.attempt', {'plan_sha256': plan_sha256, 'preexisting_policy': preexisting_policy})
            if not preexisting_policy:
                with fs._directory(POLICY.parent) as fd: f._write(fd, POLICY.name, POLICY_BYTES, 0, mode=0o755)
            with fs._directory(h.drain.UNIT_ROOT) as fd:
                for unit in self._units(host): os.symlink('/dev/null', unit, dir_fd=fd); os.fsync(fd)
            h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
            self._masks(host); self._policy()
            self._apt(['--yes', '--no-download', '--no-remove', '--no-install-recommends', 'install', *exact], timeout=600)
            self._verify_installed(value); self._masks(host)
            with fs._directory(self.directory) as fd:
                _journal(fd, 'installed.json', {'version': 1, 'plan_sha256': plan_sha256,
                    'installed_sha256': f._sha(p._json(self._installed()))})
            if not preexisting_policy:
                self._policy()
                with fs._directory(POLICY.parent) as fd: os.unlink(POLICY.name, dir_fd=fd); os.fsync(fd)
            return self.observe_installed()
        except SystemPackagesError: raise
        except Exception: raise SystemPackagesError('SYSTEM_PACKAGES_INSTALL_INCOMPLETE') from None

    def _verify_installed(self, value):
        after = self._installed(); before = value['before']
        require(all(after.get(key) == record for key, record in before.items()), 'SYSTEM_PACKAGES_HOST_CHANGED')
        added = {key.split(':')[0]: record for key, record in after.items() if key not in before}
        require(added == {key: {'version': record['version'], 'architecture': record['architecture']}
                         for key, record in value['archives'].items()})
        require(all(any(key.split(':')[0] == name for key in after) for name in value['requested']))

    def observe_installed(self):
        try:
            value = self._manifest(); self._verify_installed(value); self._masks(value['host'])
            with fs._directory(self.directory) as fd:
                attempt = f._json_read(fd, 'install.attempt', 0, mode=0o600)
                receipt = f._json_read(fd, 'installed.json', 0, mode=0o600)
            digest = f._sha(p._json(value))
            require(attempt['plan_sha256'] == digest and type(attempt['preexisting_policy']) is bool)
            require(self._policy() == attempt['preexisting_policy'])
            require(receipt == {'version': 1, 'plan_sha256': digest, 'installed_sha256': f._sha(p._json(self._installed()))})
            return {'state': 'SYSTEM_PACKAGES_INSTALLED', 'plan_sha256': digest, 'packages': len(value['archives']),
                'packages_installed': True, 'default_services_blocked': True, 'web_php_compatible': value['host']['debian'] == '13',
                'application_installed': False, 'system_wiring_verified': False}
        except SystemPackagesError: raise
        except Exception: raise SystemPackagesError('SYSTEM_PACKAGES_INSTALL_INCOMPLETE') from None


class PackageAcquisitionOperation(Operation):
    def __init__(self, packages):
        require(type(packages) is SystemPackages); self.packages = packages
        super().__init__(StepSpec(name='system.packages-acquire', operation='system.packages.acquire', module='web',
            boundary='system.packages-acquire', action='Télécharger les paquets Debian authentifiés',
            resources=(ResourceSpec('package_journal', 'directory', str(packages.directory)),),
            rollback_supported=False, warnings=('Un téléchargement incomplet exige une inspection manuelle.',)))

    def prepare(self, context): self.packages.prepare()
    def _observe(self): return self.packages.observe()
    def _receipt(self, report):
        return Receipt(created_resources=tuple(r.name for r in self.spec.resources),
                       hashes_non_secret=(('system_packages_plan', report['plan_sha256']),))
    def apply(self, context): return self._receipt(self.packages.acquire(confirmed=True))
    def validate(self, context):
        try: return self._observe()['plan_sha256'] == context.evidence['hashes_non_secret']['system_packages_plan']
        except Exception: return False
    def commit(self, context): require(self.validate(context))
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try: return Recovery(RecoveryDecision.APPLIED, self._receipt(self._observe()))
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class PackageInstallationOperation(PackageAcquisitionOperation):
    def __init__(self, packages, plan_sha256):
        require(type(packages) is SystemPackages and type(plan_sha256) is str and re.fullmatch(r'[a-f0-9]{64}', plan_sha256))
        self.packages, self.plan_sha256 = packages, plan_sha256
        Operation.__init__(self, StepSpec(name='system.packages-install', operation='system.packages.install', module='web',
            boundary='system.packages-install', action='Installer le plan Debian figé sans démarrer les services',
            resources=(ResourceSpec('package_installation', 'external', 'debian-packages.' + packages.instance),),
            rollback_supported=False, warnings=('Aucune réparation ni désinstallation automatique après interruption.',)))

    def prepare(self, context): require(self.packages.observe()['plan_sha256'] == self.plan_sha256)
    def _observe(self):
        report = self.packages.observe_installed(); require(report['plan_sha256'] == self.plan_sha256); return report
    def apply(self, context):
        return self._receipt(self.packages.install(confirmed=True, plan_sha256=self.plan_sha256))
