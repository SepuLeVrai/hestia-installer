"""Private bounded subprocesses and an ephemeral socket-only MariaDB verifier.

Never accepts a restore host, database name, shell command or existing datadir.
System binaries are prerequisites supplied by the trusted host (not downloaded).
"""
from __future__ import annotations

import hashlib
import os
import selectors
import signal
import socket
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from installer import php_transport as p
from installer import database_config as fs

MAX_ARCHIVE = 128 * 1024 * 1024


class BackupRuntimeError(RuntimeError):
    """Only a fixed diagnostic is exposed."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise BackupRuntimeError(code)


def _stop(process: subprocess.Popen) -> None:
    # Kill the session even when its original leader exited: a descendant may
    # still own a pipe, SQL lock or initialization subprocess.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def capture(command: list[str], wire: bytes, stage: Path, output: BinaryIO, timeout: float,
            *, limit: int = MAX_ARCHIVE, allow_stderr: bool = False, cancel=None) -> tuple[int, str, int]:
    """Secret stdin, concurrent pipes, bounded output written to private storage.

    Unlike the JSON control channel, SQL data intentionally goes only to a
    protected archive, never to the caller's diagnostics or an in-memory log.
    """
    require(type(wire) is bytes and len(wire) <= p.MAX_INPUT, 'BACKUP_INPUT_LIMIT')
    deadline = time.monotonic() + timeout
    process = None
    total = errors = offset = 0
    digest = hashlib.sha256()
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=stage, close_fds=True, start_new_session=True, shell=False, umask=0o077,
            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC', 'MALLOC_ARENA_MAX': '2'})
        with selectors.DefaultSelector() as selector:
            for stream, event, label in ((process.stdin, selectors.EVENT_WRITE, 'in'),
                    (process.stdout, selectors.EVENT_READ, 'out'), (process.stderr, selectors.EVENT_READ, 'err')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event, label)
            while selector.get_map():
                require(cancel is None or not cancel.is_set(), 'BACKUP_INTERRUPTED')
                require(time.monotonic() < deadline, 'BACKUP_TIMEOUT')
                for key, _ in selector.select(.05):
                    stream = key.fileobj
                    if key.data == 'in':
                        if offset < len(wire):
                            offset += os.write(stream.fileno(), wire[offset:offset+4096])
                        if offset == len(wire):
                            selector.unregister(stream)
                            stream.close()
                        continue
                    block = os.read(stream.fileno(), 65536)
                    if not block:
                        selector.unregister(stream)
                        stream.close()
                    elif key.data == 'out':
                        total += len(block)
                        require(total <= limit, 'BACKUP_OUTPUT_LIMIT')
                        digest.update(block)
                        written = output.write(block)
                        require(written == len(block), 'BACKUP_DISK_FAILED')
                    else:
                        errors += len(block)
                        require(errors <= 65536, 'BACKUP_DIAGNOSTIC_LIMIT')
            code = process.wait(timeout=max(.01, deadline-time.monotonic()))
            require(errors == 0 or allow_stderr, 'BACKUP_UNEXPECTED_STDERR')
            return code, digest.hexdigest(), total
    except BackupRuntimeError:
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise BackupRuntimeError('BACKUP_CHANNEL_FAILED') from None
    finally:
        if process is not None:
            _stop(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def system_command(runtime: p.PhpRuntime, executable: Path, args: list[str]) -> list[str]:
    # Called exclusively with fixed system executable paths and generated paths.
    p._safe_path(executable, directory=False, system=True)
    require(os.access(executable, os.X_OK), 'BACKUP_SYSTEM_TOOL_REQUIRED')
    return ['/usr/bin/setpriv', '--reuid='+str(runtime.worker_uid), '--regid='+str(runtime.worker_gid),
        '--clear-groups', '--no-new-privs', '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all',
        '--pdeathsig=KILL', '/usr/bin/prlimit', '--core=0', '--cpu=180', '--as=1073741824',
        '--fsize=536870912', '--nofile=1024', '--nproc=128', '--', str(executable), *args]


@contextmanager
def verification_server(runtime: p.PhpRuntime, stage: Path, *, cancel=None):
    """Create a NEW server under an exclusive private directory, with TCP off.

    This is not a system service or a source/production MariaDB. On any failure
    only this newly created process is stopped; no system database is removed.
    A hard controller kill can leave its private scratch datadir for inspection.
    """
    import io
    root = stage / 'verify'
    require(len(os.fsencode(root / 'sql.sock')) < 104, 'BACKUP_SOCKET_PATH_LIMIT')
    # Never chmod or reuse an existing verification directory, even if empty.
    os.mkdir(root, 0o700)
    os.chown(root, runtime.worker_uid, runtime.worker_gid)
    init = system_command(runtime, Path('/usr/bin/mariadb-install-db'), ['--no-defaults',
        '--auth-root-authentication-method=normal', '--skip-test-db', '--skip-name-resolve', '--datadir='+str(root/'data'), '--innodb-use-native-aio=0',
        '--innodb-read-io-threads=1', '--innodb-write-io-threads=1',
        '--innodb-buffer-pool-size=32M', '--aria-pagecache-buffer-size=8M', '--key-buffer-size=8M'])
    sink = io.BytesIO()
    code, _, _ = capture(init, b'', stage, sink, min(90, runtime.timeout_seconds),
                          limit=65536, allow_stderr=True, cancel=cancel)
    require(code == 0, 'BACKUP_VERIFIER_INITIALIZATION_FAILED')
    safe = root / 'export'
    os.mkdir(safe, 0o700)
    os.chown(safe, runtime.worker_uid, runtime.worker_gid)
    command = system_command(runtime, Path('/usr/sbin/mariadbd'), ['--no-defaults', '--datadir='+str(root/'data'),
        '--socket='+str(root/'sql.sock'), '--pid-file='+str(root/'server.pid'), '--skip-networking', '--skip-log-bin',
        '--skip-name-resolve', '--general-log=0', '--slow-query-log=0', '--event-scheduler=OFF',
        '--secure-file-priv='+str(safe), '--innodb-buffer-pool-size=32M', '--max-connections=8',
        '--innodb-use-native-aio=0', '--innodb-read-io-threads=1', '--innodb-write-io-threads=1',
        '--aria-pagecache-buffer-size=8M', '--key-buffer-size=8M', '--thread-cache-size=0',
        '--log-error='+str(root/'server.log')])
    process = None
    try:
        process = subprocess.Popen(command, cwd=stage, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True, shell=False, umask=0o077,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin', 'HOME':'/nonexistent', 'LC_ALL':'C', 'TZ':'UTC', 'MALLOC_ARENA_MAX':'2'})
        deadline = time.monotonic() + min(20, runtime.timeout_seconds)
        while True:
            require(cancel is None or not cancel.is_set(), 'BACKUP_INTERRUPTED')
            require(process.poll() is None and time.monotonic() < deadline, 'BACKUP_VERIFIER_START_FAILED')
            try:
                info = (root/'sql.sock').lstat()
                require(stat.S_ISSOCK(info.st_mode) and info.st_uid == runtime.worker_uid, 'BACKUP_VERIFIER_NOT_ISOLATED')
                # A socket inode alone can predate accept()/server readiness.
                # Read a genuine MySQL greeting; no source or login credential
                # is involved in this private Unix socket readiness check.
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(.5)
                    probe.connect(str(root/'sql.sock'))
                    greeting=probe.recv(5)
                if len(greeting)==5 and greeting[4]==10:
                    break
                time.sleep(.05)
            except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
                time.sleep(.05)
        yield root
    finally:
        if process is not None:
            _stop(process)
