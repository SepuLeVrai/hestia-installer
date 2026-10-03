"""Exclusive Gateway resources. No adoption, enable, restart or state reset."""
import fcntl
import os
from pathlib import Path
import re
import select
import socket
import stat
import zipfile

from installer import http_runtime as h, system_drain as drain
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_identity import GatewayIdentityStore
from installer.gateway_release import sha, verify_package
from installer.gateway_service_profile import PORT, GatewayServiceProfile
from installer.fcm_credentials import FcmCredentials, public_binding
from installer.model import ErrorCode, canonical_bytes, require
from installer.transaction import _FILE_FLAGS, _check_file, _private_directory


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
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', PORT))


class GatewayServiceRuntime:
    show = FoundationRuntime.show

    def __init__(self, foundation, identity, key_directory, *, release_commit=None, push=None):
        self.profile = GatewayServiceProfile(foundation, identity, key_directory, release_commit=release_commit, push=push)
        self.foundation, self.web = foundation, foundation.web
        self.root, self.unit = self.profile.root, self.profile.unit
        self.fragment = drain.UNIT_ROOT / self.unit
        self.keys = GatewayIdentityStore(self.profile.key_directory)

    @classmethod
    def from_binding(cls, foundation, binding):
        profile = GatewayServiceProfile.from_binding(foundation, binding)
        return cls(foundation, profile.identity, profile.key_directory,
                   release_commit=profile.selected_release['commit'], push=profile.push)

    def key_binding(self):
        report = self.keys.report()
        require(report is not None and report['profile'] == self.profile.identity
                and report['receipt'] is not None
                and report['receipt']['identities']['main'] == self.profile.main, ErrorCode.SOURCE_DRIFT)
        self.keys.verify()
        if self.profile.push is not None:
            report = FcmCredentials(self.profile.key_directory.parent / 'fcm').verify()
            require(public_binding(report['profile'], report['receipt']) == self.profile.push, ErrorCode.SOURCE_DRIFT)

    def preflight(self):
        self.foundation.owned(); self.key_binding()
        with h.fs._directory(self.root.parent) as fd: h.fs._absent(fd, self.root.name)
        with h.fs._directory(drain.UNIT_ROOT) as fd:
            h.fs._absent(fd, self.unit); h.fs._absent(fd, self.unit + '.d')
        value = self.show()
        require(value['LoadState'] == 'not-found' and value['FragmentPath'] == ''
                and value['MainPID'] == value['ControlPID'] == '0' and value['Job'] == ''
                and value['DropInPaths'] == '' and drain._empty_cgroup(self.unit), ErrorCode.MANUAL_ACTION_REQUIRED)
        free_port()

    def account(self):
        # Identity separation needs the current NSS identity, not another full
        # Foundation/Web audit. inspect()/preflight() validate that dependency;
        # recursively repeating it for every state file exhausts the bounded
        # SQL backup window without adding an independent observation boundary.
        account = self.profile.account.account(); web = h._identity(self.web.spec.service_user)
        require(account.pw_uid != web.pw_uid and account.pw_gid != web.pw_gid,
                ErrorCode.INCOMPATIBLE_STATE)
        return account

    def files(self):
        return {self.profile.config: canonical_bytes(self.profile.configuration()),
                self.fragment: self.profile.unit_bytes()}

    def manifest(self, account):
        return {'binding': self.profile.binding(), 'uid': account.pw_uid, 'gid': account.pw_gid}

    def stage(self, package):
        self.preflight(); account = self.account()
        # Authenticate the complete archive before creating native resources.
        with _private_directory(package.parent, create=False) as directory:
            handle = os.open(package.name, os.O_RDONLY | _FILE_FLAGS, dir_fd=directory)
            try:
                _check_file(handle)
                with os.fdopen(handle, 'rb', closefd=False) as stream:
                    verify_package(stream, self.profile.selected_release)
                    with zipfile.ZipFile(stream) as archive:
                        binary = archive.read('bin/hestia-mobile-gateway')
                require(sha(binary) == self.profile.selected_release['binary_sha256'], ErrorCode.SOURCE_DRIFT)
            finally: os.close(handle)
        for path, uid, gid, mode in ((self.root, 0, account.pw_gid, 0o750),
                (self.root / 'control', 0, 0, 0o700),
                (self.profile.state, account.pw_uid, account.pw_gid, 0o700)):
            with h.fs._directory(path.parent) as parent:
                os.mkdir(path.name, 0o700, dir_fd=parent)
                fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    os.fchown(fd, uid, gid); os.fchmod(fd, mode); os.fsync(fd)
                finally: os.close(fd)
                os.fsync(parent)
        with h.fs._directory(self.root) as fd:
            # The generic configuration writer deliberately rejects >16 KiB.
            out = os.open(self.profile.binary.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
            try:
                with os.fdopen(out, 'wb', closefd=False) as stream:
                    stream.write(binary); stream.flush()
                os.fchown(out, 0, account.pw_gid); os.fchmod(out, 0o750); os.fsync(out)
            finally: os.close(out)
            os.fsync(fd)
        for path, raw in self.files().items():
            with h.fs._directory(path.parent) as fd:
                h.f._write(fd, path.name, raw, 0 if path == self.fragment else account.pw_gid,
                           mode=0o644 if path == self.fragment else 0o640)
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
        with h.fs._directory(self.root) as fd:
            h.f._write(fd, 'staged.json', canonical_bytes(self.manifest(account)), 0, mode=0o600)
        self.inspect(); self.empty_state()

    def state_directory(self):
        # Service-owned state is the only writable subtree. Never traverse it
        # with the root-only configuration helper or change its existing mode.
        account = self.account()
        with h.fs._directory(self.root) as parent:
            fd = os.open('state', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            info = os.fstat(fd); h.fs._no_acl(fd)
            require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                    (account.pw_uid, account.pw_gid, 0o700), ErrorCode.SOURCE_DRIFT)
        except BaseException:
            os.close(fd); raise
        return fd

    def empty_state(self):
        fd = self.state_directory()
        try: require(not os.listdir(fd), ErrorCode.MANUAL_ACTION_REQUIRED)
        finally: os.close(fd)

    def state_binding(self):
        account = self.account(); fd = self.state_directory()
        try:
            result = {}
            for name in ('gateway.db', 'gateway.lock'):
                handle = os.open(name, os.O_RDONLY | os.O_NONBLOCK | _FILE_FLAGS, dir_fd=fd)
                try:
                    info = os.fstat(handle); h.fs._no_acl(handle)
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                            and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                            (account.pw_uid, account.pw_gid, 0o600), ErrorCode.SOURCE_DRIFT)
                    result[name] = {'device': info.st_dev, 'inode': info.st_ino}
                finally: os.close(handle)
            return result
        finally: os.close(fd)

    def inspect(self):
        self.foundation.inspect(); self.key_binding(); account = self.account()
        for path, expected in ((self.root, (0, account.pw_gid, 0o750)), (self.root / 'control', (0, 0, 0o700))):
            with h.fs._directory(path) as fd:
                info = os.fstat(fd)
                require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == expected, ErrorCode.SOURCE_DRIFT)
                if path == self.root:
                    require(set(os.listdir(fd)) == {'control', 'state', 'hestia-mobile-gateway', 'config.json', 'staged.json'}, ErrorCode.SOURCE_DRIFT)
                    require(h.f._read(fd, 'staged.json', 0, mode=0o600) == canonical_bytes(self.manifest(account)), ErrorCode.SOURCE_DRIFT)
                    require(sha(h.f._read(fd, self.profile.binary.name, account.pw_gid, mode=0o750, limit=32*1024*1024))
                            == self.profile.selected_release['binary_sha256'], ErrorCode.SOURCE_DRIFT)
        fd = self.state_directory(); os.close(fd)
        for path, raw in self.files().items():
            with h.fs._directory(path.parent) as fd:
                require(h.f._read(fd, path.name, 0 if path == self.fragment else account.pw_gid,
                    mode=0o644 if path == self.fragment else 0o640) == raw, ErrorCode.SOURCE_DRIFT)
        value = self.show()
        expected = {'Id': self.unit, 'LoadState': 'loaded', 'FragmentPath': str(self.fragment), 'DropInPaths': '',
                    'NeedDaemonReload': 'no', 'Type': 'exec', 'Job': '', 'KillMode': 'control-group',
                    'Delegate': 'no', 'Restart': 'no', 'UnitFileState': 'static', 'ControlPID': '0', 'Result': 'success'}
        require(all(value[k] == v for k, v in expected.items()), ErrorCode.SOURCE_DRIFT)
        return value

    def stopped(self):
        value = self.inspect()
        require(value['ActiveState'] == 'inactive' and value['SubState'] == 'dead'
                and value['MainPID'] == '0' and drain._empty_cgroup(self.unit), ErrorCode.MANUAL_ACTION_REQUIRED)
        free_port()
        from installer.http_drain import identity_census
        account = self.account()
        identity_census(account.pw_uid, account.pw_gid, ())

    def owned(self, *, serving=True):
        require(type(serving) is bool, ErrorCode.INVALID_DATA)
        value = self.inspect(); account = self.account()
        if serving: self.foundation.owned()
        require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
                and re.fullmatch(r'[1-9][0-9]*', value['MainPID']) and int(value['MainPID']) > 1
                and value['ControlGroup'] == '/system.slice/' + self.unit, ErrorCode.VALIDATION_FAILED)
        proc = Path('/proc') / value['MainPID']; pidfd = os.pidfd_open(int(value['MainPID']))
        try:
            start = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            from installer.http_drain import _credentials
            credentials = _credentials((proc / 'status').read_text())
            require(credentials['Uid'] == (account.pw_uid,) * 4 and credentials['Gid'] == (account.pw_gid,) * 4
                    and set(credentials['Groups']) <= {account.pw_gid}
                    and os.path.samefile(proc / 'exe', self.profile.binary)
                    and (proc / 'cmdline').read_bytes().split(b'\0') == [str(self.profile.binary).encode(), b'--config', str(self.profile.config).encode(), b'']
                    and (proc / 'cgroup').read_text().splitlines() == ['0::/system.slice/' + self.unit], ErrorCode.VALIDATION_FAILED)
            sockets = {os.readlink(fd) for fd in (proc / 'fd').iterdir()}
            rows = listeners()
            require(len(rows) == 1 and rows[0][:2] == ('tcp', f'0100007F:{PORT:04X}')
                    and 'socket:[' + rows[0][2] + ']' in sockets, ErrorCode.VALIDATION_FAILED)
            self.state_binding()
            fd = self.state_directory()
            try:
                lock = os.open('gateway.lock', os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
                try:
                    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError: pass
                    else:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        require(False, ErrorCode.VALIDATION_FAILED)
                finally: os.close(lock)
            finally: os.close(fd)
            require(start == (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
                    and self.show()['MainPID'] == value['MainPID'] and not select.select([pidfd], [], [], 0)[0], ErrorCode.VALIDATION_FAILED)
        finally: os.close(pidfd)
        return True
