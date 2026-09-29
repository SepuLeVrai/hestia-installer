"""Private MAIN listener sharing the qualified Web FPM and maintenance guard.

No Web file/unit, private signing key, boot bundle or foreign process is changed.
The separate controller owns only its new fragment and Foundation directory.
"""
import hashlib
import os
from pathlib import Path
import re
import socket
import stat
import subprocess

from installer import http_runtime as h, system_drain as drain
from installer.gateway_identity import public_identity
from installer.model import ErrorCode, canonical_bytes, require

PORT = 9082
MODULES = ('mpm_event', 'authz_core', 'authz_host', 'proxy', 'proxy_fcgi',
           'rewrite', 'env', 'setenvif', 'reqtimeout')
PROPERTIES = ('Id', 'LoadState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload',
              'Type', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'ControlGroup',
              'Result', 'Job', 'KillMode', 'Delegate', 'Restart', 'UnitFileState')


def listeners():
    result = []
    for family in ('tcp', 'tcp6'):
        for row in (Path('/proc/net') / family).read_text().splitlines()[1:]:
            fields = row.split()
            require(len(fields) >= 10, ErrorCode.INVALID_STATE)
            if fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == PORT:
                result.append((family, fields[1], fields[9]))
    return result


def free_port():
    require(not listeners(), ErrorCode.MANUAL_ACTION_REQUIRED)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Match Apache's reuse policy without accepting an existing listener.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', PORT))


class FoundationRuntime:
    def __init__(self, activation, identity):
        self.activation = activation
        self.web = activation.runtime
        require(self.web.spec.port == 9080 and self.web.spec.external_uploads
                and self.web.spec.maintenance_directory is not None, ErrorCode.INCOMPATIBLE_STATE)
        require(identity == public_identity('main', identity['public_jwk']), ErrorCode.INCOMPATIBLE_STATE)
        self.identity = identity
        self.root = self.web.spec.root.parent / 'foundation'
        self.unit = 'hestia-' + self.web.spec.instance + '-foundation.service'
        self.fragment = drain.UNIT_ROOT / self.unit

    def show(self):
        h.p._safe_path(Path('/usr/bin/systemctl'), directory=False, system=True)
        result = subprocess.run(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'show',
            '--property=' + ','.join(PROPERTIES), '--', self.unit], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False,
            env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': ''})
        require(result.returncode in (0, 1) and len(result.stdout) <= 16384, ErrorCode.VALIDATION_FAILED)
        value = {}
        for row in result.stdout.decode('ascii').splitlines():
            key, separator, item = row.partition('=')
            require(separator and key in PROPERTIES and key not in value, ErrorCode.INVALID_STATE)
            value[key] = item
        require(set(value) == set(PROPERTIES), ErrorCode.INVALID_STATE)
        require(result.returncode == 0 or value['LoadState'] == 'not-found', ErrorCode.VALIDATION_FAILED)
        return value

    def files(self, gid):
        template = (Path(__file__).parent / 'private/foundation-apache.conf.template').read_bytes()
        require(hashlib.sha1(b'blob ' + str(len(template)).encode() + b'\0' + template).hexdigest()
                == 'cbc20981ebf4030faaac1ed62c5fb58161492919', ErrorCode.SOURCE_DRIFT)
        vhost = template.decode()
        for key, value in {'HESTIA_INTERNAL_MOBILE_PORT': str(PORT), 'HESTIA_WEB_ROOT': str(self.web.spec.webroot),
            'HESTIA_MOBILE_CONFIG': str(self.root / 'main.json'),
            'HESTIA_PHP_FPM_SOCKET': str(self.web.spec.root / 'run/php.sock'), 'APACHE_LOG_DIR': str(self.root / 'log')}.items():
            vhost = vhost.replace('${' + key + '}', value)
        require('${' not in vhost, ErrorCode.INVALID_STATE)
        # Closed URI policy is copied from the pinned Web, never expanded here.
        vhost = vhost.replace('    RewriteEngine On\n', '    RewriteEngine On\n'
            '    RewriteCond %{REQUEST_METHOD} !^POST$ [OR]\n'
            '    RewriteCond %{QUERY_STRING} !^$\n'
            '    RewriteRule ^ - [R=404,L]\n')
        apache = (f'ServerRoot "{self.root}"\nServerName hestia-internal-mobile.local\n'
            'ServerSignature Off\nServerTokens Prod\n'
            f'DefaultRuntimeDir "{self.root}/run"\nPidFile "{self.root}/run/apache.pid"\n'
            + ''.join(f'LoadModule {name}_module /usr/lib/apache2/modules/mod_{name}.so\n' for name in MODULES)
            + f'User {self.web.spec.service_user}\nGroup #{gid}\nErrorLog "{self.root}/log/error.log"\n'
            'LogLevel warn\nRequestReadTimeout header=3-5,MinRate=500 body=3-5,MinRate=500\n'
            '<Directory />\nAllowOverride None\nRequire all denied\n</Directory>\n' + vhost)
        unit = f'''[Unit]
Description=HESTIA private MAIN Foundation
After={self.web.unit('php')}
[Service]
Type=simple
ExecStartPre=/usr/sbin/apache2 -t -f {self.root}/apache.conf
ExecStart=/usr/sbin/apache2 -DFOREGROUND -f {self.root}/apache.conf
UMask=0027
Restart=no
KillMode=control-group
SendSIGKILL=yes
TimeoutStopSec=35s
Delegate=no
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths={self.root}/run {self.root}/log
CapabilityBoundingSet=CAP_SETUID CAP_SETGID
RestrictAddressFamilies=AF_UNIX AF_INET
'''
        config = {'environment': 'main', 'gateway_keys': {self.identity['kid']: self.identity['public_jwk']},
                  'canonical_contexts': True, 'canonical_distribution': True}
        return {self.root / 'apache.conf': apache.encode(), self.root / 'main.json': canonical_bytes(config),
                self.fragment: unit.encode()}

    def host(self):
        self.activation.configuration()
        account, _ = self.web._host()
        for name in MODULES:
            h.p._safe_path(Path('/usr/lib/apache2/modules/mod_' + name + '.so'), directory=False, system=True)
        return account

    def manifest(self, gid):
        dependencies = [Path('/usr/sbin/apache2')] + [Path('/usr/lib/apache2/modules/mod_' + n + '.so') for n in MODULES]
        return {'version': 1, 'web_plan_sha256': self.activation.parent_sha256,
                'files': {str(p): h.f._sha(raw) for p, raw in self.files(gid).items()},
                'dependencies': {str(p): h._system_file_digest(p) for p in dependencies}}

    def absent(self):
        self.host(); self.activation.serving()
        with h.fs._directory(self.root.parent) as fd: h.fs._absent(fd, self.root.name)
        with h.fs._directory(drain.UNIT_ROOT) as fd:
            h.fs._absent(fd, self.unit); h.fs._absent(fd, self.unit + '.d')
        value = self.show()
        require(value['LoadState'] == 'not-found' and value['FragmentPath'] == ''
                and value['MainPID'] == value['ControlPID'] == '0' and value['Job'] == ''
                and value['DropInPaths'] == '' and drain._empty_cgroup(self.unit), ErrorCode.MANUAL_ACTION_REQUIRED)
        free_port()

    def stage(self):
        self.absent(); account = self.host(); gid = account.pw_gid
        for path in (self.root, self.root / 'run', self.root / 'log'):
            with h.fs._directory(path.parent) as parent:
                os.mkdir(path.name, 0o700, dir_fd=parent)
                fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try: os.fchown(fd, 0, gid); os.fchmod(fd, 0o750); os.fsync(fd)
                finally: os.close(fd)
                os.fsync(parent)
        for path, raw in self.files(gid).items():
            with h.fs._directory(path.parent) as fd:
                h.f._write(fd, path.name, raw, 0 if path == self.fragment else gid,
                           mode=0o644 if path == self.fragment else 0o640)
        h._command(['/usr/sbin/apache2', '-t', '-f', str(self.root / 'apache.conf')])
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        with h.fs._directory(self.root) as fd:
            h.f._write(fd, 'staged.json', canonical_bytes(self.manifest(gid)), 0, mode=0o600)
        self.inspect()

    def inspect(self):
        gid = self.host().pw_gid
        for path in (self.root, self.root / 'run', self.root / 'log'):
            with h.fs._directory(path) as fd:
                info = os.fstat(fd); h.fs._no_acl(fd)
                require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, gid, 0o750), ErrorCode.SOURCE_DRIFT)
                if path == self.root:
                    require(set(os.listdir(fd)) == {'run', 'log', 'apache.conf', 'main.json', 'staged.json'}, ErrorCode.SOURCE_DRIFT)
                    require(h.f._read(fd, 'staged.json', 0, mode=0o600) == canonical_bytes(self.manifest(gid)), ErrorCode.SOURCE_DRIFT)
        for path, raw in self.files(gid).items():
            with h.fs._directory(path.parent) as fd:
                require(h.f._read(fd, path.name, 0 if path == self.fragment else gid,
                    mode=0o644 if path == self.fragment else 0o640) == raw, ErrorCode.SOURCE_DRIFT)
        value = self.show()
        expected = {'Id': self.unit, 'LoadState': 'loaded', 'FragmentPath': str(self.fragment), 'DropInPaths': '',
                    'NeedDaemonReload': 'no', 'Type': 'simple', 'Job': '', 'KillMode': 'control-group',
                    'Delegate': 'no', 'Restart': 'no', 'UnitFileState': 'static', 'ControlPID': '0', 'Result': 'success'}
        require(all(value[k] == v for k, v in expected.items()), ErrorCode.SOURCE_DRIFT)
        return value

    def stopped(self):
        value = self.inspect()
        require(value['ActiveState'] == 'inactive' and value['SubState'] == 'dead'
                and value['MainPID'] == '0' and drain._empty_cgroup(self.unit), ErrorCode.MANUAL_ACTION_REQUIRED)
        free_port()

    def owned(self):
        value = self.inspect(); self.activation.serving()
        require(self.activation.running('php'), ErrorCode.VALIDATION_FAILED)
        require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
                and re.fullmatch(r'[1-9][0-9]*', value['MainPID']) and int(value['MainPID']) > 1
                and value['ControlGroup'] == '/system.slice/' + self.unit, ErrorCode.VALIDATION_FAILED)
        proc = Path('/proc') / value['MainPID']
        pidfd = os.pidfd_open(int(value['MainPID']))
        try:
            start = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            require(os.path.samefile(proc / 'exe', '/usr/sbin/apache2')
                    and (proc / 'cmdline').read_bytes().split(b'\0') == [b'/usr/sbin/apache2', b'-DFOREGROUND', b'-f', str(self.root / 'apache.conf').encode(), b'']
                    and (proc / 'cgroup').read_text().splitlines() == ['0::/system.slice/' + self.unit], ErrorCode.VALIDATION_FAILED)
            sockets = {os.readlink(fd) for fd in (proc / 'fd').iterdir()}
            rows = listeners()
            require(len(rows) == 1 and rows[0][:2] == ('tcp', f'0100007F:{PORT:04X}')
                    and 'socket:[' + rows[0][2] + ']' in sockets, ErrorCode.VALIDATION_FAILED)
            require(start == (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
                    and self.show()['MainPID'] == value['MainPID'], ErrorCode.VALIDATION_FAILED)
            import select
            require(not select.select([pidfd], [], [], 0)[0], ErrorCode.VALIDATION_FAILED)
        finally: os.close(pidfd)
        return True
