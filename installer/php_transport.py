"""5B2.1 private, local-only transport. Not registered with the HTTP/plan facade."""
from __future__ import annotations

import grp
import hashlib
import json
import math
import os
import pwd
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from installer.model import strict_json_loads
from installer.transaction import _private_directory
from installer.web_config import validate_web_configuration

WEB_REPOSITORY = "SepuLeVrai/hestia-nexus-avv"
WEB_COMMIT = "dcb856bc5ef5f35006d2398289b49f5386dcc5f5"
ENGINE_SHA256 = "646653d2785a1ed1d933bf56ff40ee0cc2f3ae35666f6f553ff23312a0079ca8"
ENGINE_VERSION = "3.0.0.0-stable-20260914"
ENGINE_FILES = ("includes/installation/core.php", "includes/installation/fresh.php",
                "includes/version.php", "sql/schema.sql")
MAX_INPUT = 16384
MAX_OUTPUT = 4096
MAX_FILE = 8 * 1024 * 1024
MAX_BUNDLE = 64 * 1024 * 1024
READ_ONLY_ERRORS = frozenset({
    "REQUEST_INVALID", "CONNECTION_FAILED", "RUNTIME_UNAVAILABLE", "ENGINE_UNAVAILABLE",
    "ADMIN_INPUT_INVALID", "FRESH_CONFIRMATION_REQUIRED", "ACTIVE_TRANSACTION_REFUSED",
    "MARIADB_REQUIRED", "PDO_EXCEPTION_MODE_REQUIRED", "DATABASE_TARGET_INVALID",
    "INSTALL_VERSION_MISMATCH", "SCHEMA_INVALID", "INSTALLATION_BUSY",
    "FRESH_DATABASE_NOT_EMPTY", "FRESH_PREFLIGHT_FAILED", "INSPECTION_FAILED",
})
UNCERTAIN_ERRORS = frozenset({"FRESH_INCOMPLETE_MANUAL_ACTION", "INSTALL_LOCK_RELEASE_FAILED", "ENGINE_FAILED"})


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


class TransportError(RuntimeError):
    """Fixed internal code only. No original exception or user text in the message."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise TransportError(code)


class ProvisioningCredentials:
    """Separate from the application's SQL account; no repr/pickle disclosure."""
    __slots__ = ("_user", "_password")

    def __init__(self, user: str, password: str) -> None:
        _require(type(user) is str and re.fullmatch(r"[A-Za-z0-9_]{1,32}", user) is not None
                 and user.lower() != "root", "CREDENTIALS_REJECTED")
        _require(type(password) is str and 1 <= len(password) <= 1024
                 and not any(unicodedata.category(c).startswith("C") for c in password)
                 and len(password.encode("utf-8")) <= 1024, "CREDENTIALS_REJECTED")
        self._user, self._password = user, password

    def __repr__(self) -> str:
        return "<ProvisioningCredentials private>"

    def __reduce__(self):
        raise TypeError("Provisioning credentials cannot be serialized")


@dataclass(frozen=True)
class PhpRuntime:
    """Trusted host configuration, never from a browser or a 5A field.

    uid/gid must describe a dedicated, pre-existing non-login worker account.
    PHP and its extension directory must be canonical system paths.
    """
    php: Path
    extension_dir: Path
    worker_uid: int
    worker_gid: int
    run_root: Path
    state_root: Path
    timeout_seconds: float = 60.0


def _safe_path(path: Path, *, directory: bool, system: bool = False) -> None:
    """Reject links and untrusted owners along the entire absolute path."""
    _require(path.is_absolute() and str(path) == os.path.normpath(path), "UNSAFE_PATH")
    if system:
        _require(str(path).startswith("/usr/"), "UNSAFE_RUNTIME")
    current = Path("/")
    for index, part in enumerate(path.parts[1:]):
        current /= part
        info = current.lstat()
        last = index == len(path.parts) - 2
        is_dir = not last or directory
        _require(info.st_uid == 0 and not stat.S_ISLNK(info.st_mode)
                 and (stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode)), "UNSAFE_PATH")
        # Only a sticky, root-owned ancestor such as /tmp may be writable.
        sticky_ancestor = not last and is_dir and bool(info.st_mode & stat.S_ISVTX)
        _require(not info.st_mode & 0o022 or sticky_ancestor, "UNSAFE_PATH")
        if last and not directory:
            _require(info.st_nlink == 1 and not info.st_mode & 0o6000, "UNSAFE_PATH")


def _read_file(path: Path) -> bytes:
    _safe_path(path, directory=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                 and not before.st_mode & 0o022 and before.st_size <= MAX_FILE, "SOURCE_REJECTED")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_FILE + 1)
        after = os.fstat(fd)
        _require(len(data) <= MAX_FILE and len(data) == before.st_size
                 and (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                 == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "SOURCE_REJECTED")
        return data
    finally:
        os.close(fd)


def _bundle(source: Path, destination: Path, gid: int) -> None:
    """Copy only the pinned executable closure; never read includes/db.php or IA secrets."""
    deadline = time.monotonic() + 30
    _safe_path(source, directory=True)
    names = list(ENGINE_FILES)
    vendor = source / "vendor"
    _safe_path(vendor, directory=True)
    pending = [vendor]
    entries = 0
    while pending:
        directory = pending.pop()
        _safe_path(directory, directory=True)
        for path in directory.iterdir():
            entries += 1
            _require(entries <= 10000 and len(path.relative_to(source).parts) <= 32
                     and time.monotonic() < deadline, "SOURCE_LIMIT")
            _require(not path.is_symlink(), "SOURCE_REJECTED")
            if path.is_dir():
                pending.append(path)
            else:
                names.append(path.relative_to(source).as_posix())
    digest = hashlib.sha256()
    total = 0
    for name in sorted(names):
        _require(time.monotonic() < deadline and all(c.isprintable() for c in name), "SOURCE_LIMIT")
        data = _read_file(source / name)
        total += len(data)
        _require(total <= MAX_BUNDLE, "SOURCE_LIMIT")
        digest.update(_json([name, len(data), hashlib.sha256(data).hexdigest()]) + b"\n")
        target = destination / "engine" / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        with target.open("xb") as output:
            output.write(data)
        os.chown(target, 0, gid)
        os.chmod(target, 0o640)
    _require(digest.hexdigest() == ENGINE_SHA256, "SOURCE_PIN_MISMATCH")
    # The bridge belongs to the installed, root-owned Installer, not to the Web tree.
    bridge = _read_file(Path(__file__).parent / "private" / "php_bridge.php")
    (destination / "bridge.php").write_bytes(bridge)
    for root, dirs, files in os.walk(destination):
        os.chown(root, 0, gid)
        os.chmod(root, 0o750)
        for name in files:
            os.chown(Path(root) / name, 0, gid)
            os.chmod(Path(root) / name, 0o640)


def _runtime(runtime: PhpRuntime, web_user: str) -> None:
    _require(os.getuid() == 0 and os.geteuid() == 0, "ROOT_ORCHESTRATOR_REQUIRED")
    _require(type(runtime.worker_uid) is int and type(runtime.worker_gid) is int
             and runtime.worker_uid > 0 and runtime.worker_gid > 0, "WORKER_IDENTITY_REJECTED")
    account = pwd.getpwuid(runtime.worker_uid)
    web = pwd.getpwnam(web_user)
    group = grp.getgrgid(runtime.worker_gid)
    _require(set(group.gr_mem) <= {account.pw_name}
             and all(u.pw_uid == account.pw_uid for u in pwd.getpwall() if u.pw_gid == runtime.worker_gid),
             "WORKER_IDENTITY_REJECTED")
    _require(account.pw_gid == runtime.worker_gid and account.pw_uid != web.pw_uid
             and runtime.worker_gid != web.pw_gid and account.pw_name not in ("nobody", "www-data")
             and account.pw_shell in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false")
             and account.pw_dir in ("/nonexistent", "/var/empty"), "WORKER_IDENTITY_REJECTED")
    _require(type(runtime.timeout_seconds) in (int, float) and math.isfinite(runtime.timeout_seconds)
             and 0.05 <= runtime.timeout_seconds <= 180, "TIMEOUT_REJECTED")
    for path in (runtime.php, Path("/usr/bin/setpriv"), Path("/usr/bin/prlimit")):
        _safe_path(path, directory=False, system=True)
        _require(os.access(path, os.X_OK), "UNSAFE_RUNTIME")
    _safe_path(runtime.extension_dir, directory=True, system=True)
    _require(re.fullmatch(r"php[0-9]+\.[0-9]+", runtime.php.name) is not None
             and re.fullmatch(r"[0-9]{8}", runtime.extension_dir.name) is not None, "UNSAFE_RUNTIME")
    for name in ("mysqlnd", "pdo", "pdo_mysql"):
        _safe_path(runtime.extension_dir / (name + ".so"), directory=False, system=True)
    _safe_path(runtime.run_root, directory=True)
    _require(stat.S_IMODE(runtime.run_root.stat().st_mode) == 0o711, "UNSAFE_RUN_ROOT")


def _command(runtime: PhpRuntime, stage: Path) -> list[str]:
    command = ["/usr/bin/setpriv", "--reuid=" + str(runtime.worker_uid),
               "--regid=" + str(runtime.worker_gid), "--clear-groups", "--no-new-privs",
               "--inh-caps=-all", "--ambient-caps=-all", "--bounding-set=-all", "--pdeathsig=KILL",
               "/usr/bin/prlimit", "--core=0", "--cpu=120", "--as=536870912", "--fsize=0",
               "--nofile=64", "--nproc=16", "--", str(runtime.php), "-n"]
    for name in ("mysqlnd", "pdo", "pdo_mysql"):
        command += ["-d", "extension=" + str(runtime.extension_dir / (name + ".so"))]
    settings = {
        "display_errors": "0", "display_startup_errors": "0", "log_errors": "0",
        "zend.exception_ignore_args": "1", "html_errors": "0", "memory_limit": "128M",
        "allow_url_fopen": "0", "allow_url_include": "0", "enable_dl": "0",
        "auto_prepend_file": "", "auto_append_file": "", "user_ini.filename": "",
        "opcache.enable_cli": "0", "open_basedir": str(stage),
        "disable_functions": "exec,system,shell_exec,passthru,proc_open,popen,pcntl_exec,pcntl_fork,dl,mail",
    }
    for key, value in settings.items():
        command += ["-d", key + "=" + value]
    return command + ["-f", str(stage / "bridge.php")]


def _exchange(command: list[str], wire: bytes, stage: Path, timeout: float, cancel=None) -> tuple[int, bytes]:
    """Concurrent bounded pipes, wall deadline, no shell, no raw stderr retention."""
    _require(len(wire) <= MAX_INPUT, "INPUT_LIMIT")
    process = None
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, close_fds=True, shell=False,
                                   start_new_session=True, cwd=stage, umask=0o077,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC"})
        with selectors.DefaultSelector() as selector:
            for stream, event, label in ((process.stdin, selectors.EVENT_WRITE, "in"),
                                         (process.stdout, selectors.EVENT_READ, "out"),
                                         (process.stderr, selectors.EVENT_READ, "err")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event, label)
            offset = 0
            output = bytearray()
            diagnostics = 0
            while selector.get_map():
                if cancel is not None and cancel.is_set():
                    raise TransportError("INTERRUPTED")
                _require(time.monotonic() < deadline, "TIMEOUT")
                for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    stream = key.fileobj
                    if key.data == "in":
                        offset += os.write(stream.fileno(), wire[offset:offset + 4096])
                        if offset == len(wire):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        block = os.read(stream.fileno(), 4096)
                        if not block:
                            selector.unregister(stream)
                            stream.close()
                        elif key.data == "out":
                            _require(len(output) + len(block) <= MAX_OUTPUT, "OUTPUT_LIMIT")
                            output.extend(block)
                        else:
                            diagnostics += len(block)
                            _require(diagnostics <= MAX_OUTPUT, "OUTPUT_LIMIT")
            while True:
                if cancel is not None and cancel.is_set():
                    raise TransportError("INTERRUPTED")
                _require(time.monotonic() < deadline, "TIMEOUT")
                try:
                    code = process.wait(timeout=min(0.05, max(0, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    pass
            _require(diagnostics == 0, "UNEXPECTED_STDERR")
            return code, bytes(output)
    except KeyboardInterrupt:
        raise TransportError("INTERRUPTED") from None
    except (OSError, ValueError):
        raise TransportError("CHANNEL_FAILED") from None
    finally:
        if process is not None:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass  # Durable interlock still prohibits any replay.
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def _map(payload: dict, credentials: ProvisioningCredentials, operation: str, confirmed: bool) -> dict:
    try:
        preview = validate_web_configuration(payload)["configuration"]
        _require(operation in ("fresh_database", "inspect_database") and preview["mode"] == "fresh",
                 "OPERATION_UNSUPPORTED")
        database = preview["database"]
        _require(database["mode"] in ("managed", "existing_local")
                 and database["host"] in ("127.0.0.1", "localhost") and database["tls_ca_file"] is None,
                 "TARGET_TRANSPORT_UNSUPPORTED")
        _require(type(credentials) is ProvisioningCredentials and credentials._user != database["user"],
                 "PROVISIONING_ACCOUNT_REQUIRED")
        request = {"version": 1, "operation": operation, "request_id": os.urandom(16).hex(),
                   "database": {"host": "127.0.0.1", "port": database["port"], "name": database["name"],
                                "user": credentials._user, "password": credentials._password}}
        if operation == "fresh_database":
            _require(confirmed is True, "CONFIRMATION_REQUIRED")
            request.update({"confirmed": True,
                            "administrator": {**preview["administrator"], "password": payload["secrets"]["admin_password"]}})
        _require(len(_json(request)) <= MAX_INPUT, "INPUT_LIMIT")
        return request
    except TransportError:
        raise
    except Exception:
        raise TransportError("INPUT_REJECTED") from None


def _response(code: int, data: bytes, request: dict) -> dict:
    try:
        response = strict_json_loads(data)
        _require(type(response) is dict and set(response) == {"version", "operation", "request_id", "ok", "result", "error"}, "PROTOCOL_REJECTED")
        _require(type(response["version"]) is int and response["version"] == 1
                 and response["request_id"] == request["request_id"]
                 and response["operation"] == request["operation"] and type(response["ok"]) is bool, "PROTOCOL_REJECTED")
        result = response["result"]
        if response["ok"]:
            _require(code == 0 and response["error"] is None and type(result) is dict, "PROTOCOL_REJECTED")
            if request["operation"] == "fresh_database":
                _require(set(result) == {"scope", "application_installed", "assistant_enabled", "version", "schema_statements"}
                         and result["scope"] == "DATABASE_READY" and result["application_installed"] is False
                         and result["assistant_enabled"] is False and result["version"] == ENGINE_VERSION
                         and type(result["schema_statements"]) is int and 1 <= result["schema_statements"] <= 100000,
                         "PROTOCOL_REJECTED")
            else:
                _require(set(result) == {"scope", "tables", "routines", "events", "empty", "retry_authorized", "application_installed"}
                         and result["scope"] == "DATABASE_OBSERVATION" and result["retry_authorized"] is False
                         and result["application_installed"] is False and type(result["empty"]) is bool,
                         "PROTOCOL_REJECTED")
                _require(all(type(result[k]) is int and 0 <= result[k] <= 100000 for k in ("tables", "routines", "events"))
                         and result["empty"] == (result["tables"] + result["routines"] + result["events"] == 0), "PROTOCOL_REJECTED")
            return {"state": result["scope"], "code": "OK", "result": result}
        error = response["error"]
        _require(code == 20 and result is None and type(error) is str
                 and error in READ_ONLY_ERRORS | UNCERTAIN_ERRORS, "PROTOCOL_REJECTED")
        return {"state": "MANUAL_ACTION" if error in UNCERTAIN_ERRORS else "REFUSED", "code": error, "result": None}
    except TransportError:
        raise
    except Exception:
        raise TransportError("PROTOCOL_REJECTED") from None


class PhpTransport:
    """Explicit private calls only; no retry/reset/rollback API and no HTTP binding."""
    def __init__(self, runtime: PhpRuntime, source: Path, *, repository: str, commit: str) -> None:
        _require(repository == WEB_REPOSITORY and commit == WEB_COMMIT, "SOURCE_PIN_MISMATCH")
        self.runtime, self.source = runtime, Path(source)

    def fresh(self, payload: dict, credentials: ProvisioningCredentials, *, confirmed: bool, cancel=None) -> dict:
        return self._call(payload, credentials, "fresh_database", confirmed, cancel)

    def inspect(self, payload: dict, credentials: ProvisioningCredentials, *, cancel=None) -> dict:
        return self._call(payload, credentials, "inspect_database", False, cancel)

    def _call(self, payload: dict, credentials: ProvisioningCredentials, operation: str, confirmed: bool, cancel) -> dict:
        request = _map(payload, credentials, operation, confirmed)
        marker = None
        try:
            _runtime(self.runtime, payload["web"]["service_user"])
            if cancel is not None and cancel.is_set():
                raise TransportError("INTERRUPTED_BEFORE_DISPATCH")
            with tempfile.TemporaryDirectory(prefix="php-", dir=self.runtime.run_root) as temporary:
                stage = Path(temporary)
                _bundle(self.source, stage, self.runtime.worker_gid)
                if operation == "fresh_database":
                    # Database identity, not request ID: changing caller/account/source cannot silently replay.
                    target = request["database"]
                    key = hashlib.sha256(_json([target["host"], target["port"], target["name"].lower()])).hexdigest()
                    with _private_directory(self.runtime.state_root, create=True) as directory:
                        try:
                            marker = os.open("fresh-" + key + ".attempt", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                        except FileExistsError:
                            raise TransportError("FRESH_REPLAY_BLOCKED") from None
                        os.write(marker, _json({"version": 1, "request_id": request["request_id"], "state": "DISPATCHING"}) + b"\n")
                        os.fsync(marker)
                        os.fsync(directory)
                try:
                    code, data = _exchange(_command(self.runtime, stage), _json(request), stage,
                                           self.runtime.timeout_seconds, cancel)
                    outcome = _response(code, data, request)
                except TransportError as error:
                    outcome = {"state": "MANUAL_ACTION" if marker is not None else "OBSERVATION_FAILED",
                               "code": str(error) if str(error) in {"INTERRUPTED", "TIMEOUT", "OUTPUT_LIMIT",
                                   "UNEXPECTED_STDERR", "CHANNEL_FAILED", "PROTOCOL_REJECTED"} else "TRANSPORT_FAILED",
                               "result": None}
                # No output or credential is copied to the durable interlock.
                if marker is not None:
                    os.write(marker, _json({"state": outcome["state"], "code": outcome["code"]}) + b"\n")
                    os.fsync(marker)
                return {"version": 1, "operation": operation, "request_id": request["request_id"], **outcome}
        except TransportError:
            raise
        except Exception:
            raise TransportError("MANUAL_ACTION_REQUIRED" if marker is not None else "LOCAL_PREFLIGHT_FAILED") from None
        finally:
            if marker is not None:
                os.close(marker)
