"""5B2.2 private fresh assembly. No public operation, Assistant or activation.

OS/PHP/MariaDB services and service identities are host prerequisites (5D).
Only managed mode creates SQL resources; remote/existing_local never do so.
"""
from __future__ import annotations

import hashlib
import os
import re
import ssl
import stat
import tempfile
from pathlib import Path

from installer import database_config as fs
from installer import php_transport as p
from installer.model import strict_json_loads
from installer.transaction import _private_directory
from installer.web_config import validate_web_configuration

# Fixed to the separately qualified Web commit before publication of this module.
WEB_COMMIT = "8cafa4427fcfc8dd57decdb0a09701f1093c37aa"
ENGINE_FILES = (*p.ENGINE_FILES, "includes/installation/connection.php")
ENGINE_SHA256 = "3dc8ec604cedce3d30bfe9f517d3a0cf78ed60bde3ea42c6c03a893ec826d860"
ERRORS = frozenset({"REQUEST_INVALID", "SQL_TARGET_INVALID", "SQL_CA_INVALID", "SQL_CREDENTIAL_INVALID", "SQL_DRIVER_REQUIRED",
    "SQL_CONNECTION_FAILED", "SQL_TLS_CONNECTION_FAILED", "ACCOUNT_SEPARATION_REQUIRED", "SERVER_PROFILE_UNSUPPORTED",
    "TARGET_IDENTITY_MISMATCH", "ACCOUNT_POLICY_REJECTED", "AUTHORITY_IDENTITY_REJECTED", "AUTHORITY_PRIVILEGES_REQUIRED", "SQL_ACCOUNT_OCCUPIED", "SQL_DATABASE_OCCUPIED",
    "INSTALLATION_BUSY", "AUDIT_UNAVAILABLE", "DATABASE_VERIFICATION_FAILED", "TEMPORARY_ACCOUNT_CLEANUP_FAILED",
    "ADMIN_INPUT_INVALID", "FRESH_CONFIRMATION_REQUIRED", "ACTIVE_TRANSACTION_REFUSED", "MARIADB_REQUIRED",
    "PDO_EXCEPTION_MODE_REQUIRED", "DATABASE_TARGET_INVALID", "INSTALL_VERSION_MISMATCH", "SCHEMA_INVALID",
    "FRESH_DATABASE_NOT_EMPTY", "FRESH_PREFLIGHT_FAILED", "FRESH_INCOMPLETE_MANUAL_ACTION", "INSTALL_LOCK_RELEASE_FAILED",
    "DATABASE_STEP_FAILED", "DEFINER_ACCOUNT_OCCUPIED", "DEFINER_PROFILE_REJECTED", "DEFINER_TRIGGER_PROFILE_REJECTED",
    "DEFINER_REBIND_FAILED", "DEFINER_SMOKE_FAILED"})


class DatabaseStepError(RuntimeError):
    """Fixed code only, never a raw connection/process/credential diagnostic."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise DatabaseStepError(code)


class SqlAuthorityCredentials(p.ProvisioningCredentials):
    """SQL account-management authority, NOT an OS root identity or runtime account."""
    __slots__ = ()

    def __init__(self, user: str, password: str):
        super().__init__("authority" if user == "root" else user, password)
        self._user = user

    def __repr__(self):
        return "<SqlAuthorityCredentials private>"


def _configuration(payload: dict, migration: p.ProvisioningCredentials, authority: SqlAuthorityCredentials | None,
                   *, fresh: bool, confirmed: bool) -> dict:
    try:
        config = validate_web_configuration(payload)["configuration"]
        require(not fresh or config["mode"] == "fresh", "UPGRADE_PREPARE_REFUSED")
        require(not fresh or confirmed is True, "CONFIRMATION_REQUIRED")
        require(type(migration) is p.ProvisioningCredentials, "MIGRATION_ACCOUNT_REQUIRED")
        db = config["database"]
        if db["mode"] != "remote":
            require(db["host"] in ("localhost", "127.0.0.1", "::1"), "LOCAL_TARGET_UNSUPPORTED")
            if db["host"] == "localhost": db["host"] = "127.0.0.1"
        require(db["mode"] != "managed" or (fresh and db["host"] == "127.0.0.1"), "MANAGED_TARGET_UNSUPPORTED")
        app = p.ProvisioningCredentials(db["user"], payload["secrets"]["database_password"])
        all_credentials = [app, migration]
        if fresh and db["mode"] == "managed":
            require(type(authority) is SqlAuthorityCredentials, "SQL_AUTHORITY_REQUIRED")
            all_credentials.append(authority)
        else:
            require(authority is None, "SQL_AUTHORITY_UNEXPECTED")
        require(len({c._user for c in all_credentials}) == len(all_credentials)
                and len({c._password for c in all_credentials}) == len(all_credentials), "ACCOUNT_SEPARATION_REQUIRED")
        return config
    except DatabaseStepError:
        raise
    except Exception:
        raise DatabaseStepError("INPUT_REJECTED") from None


def _ca(config: dict) -> bytes | None:
    if config["database"]["mode"] != "remote":
        return None
    path = Path(config["database"]["tls_ca_file"])
    # Root-owned no-link/ACL-protected directories, then pin the opened CA inode.
    with fs._directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and not info.st_mode & 0o7022 and 1 <= info.st_size <= 16384, "CA_REJECTED")
            fs._no_acl(fd)
            data = bytearray()
            while len(data) <= 16384:
                block = os.read(fd, min(4096, 16385 - len(data)))
                if not block: break
                data.extend(block)
            after = os.fstat(fd)
            require(len(data) == info.st_size and (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "CA_REJECTED")
        finally: os.close(fd)
    # Parse as certificates only. This is not a network or identity verification.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=bytes(data).decode("ascii"))
    return bytes(data)


def _target(config: dict, ca_path: Path | None, ca: bytes | None) -> dict:
    db = config["database"]
    return {"host": db["host"], "port": db["port"], "name": db["name"], "tls_required": ca is not None,
            "tls_ca_file": str(ca_path) if ca_path is not None else None,
            "tls_ca_sha256": hashlib.sha256(ca).hexdigest() if ca is not None else None}


def _request(config: dict, payload: dict, migration: p.ProvisioningCredentials,
             authority: SqlAuthorityCredentials | None, target: dict, fresh: bool) -> dict:
    def credential(c): return {"user": c._user, "password": c._password} if c is not None else None
    request = {"version": 1, "operation": "prepare_database" if fresh else "audit_accounts", "request_id": os.urandom(16).hex(),
               "mode": config["database"]["mode"], "target": target,
               "application": {"user": config["database"]["user"], "password": payload["secrets"]["database_password"]},
               "migration": credential(migration), "authority": credential(authority),
               "administrator": {**config["administrator"], "password": payload["secrets"]["admin_password"]} if fresh else None,
               "confirmed": fresh}
    require(len(p._json(request)) <= p.MAX_INPUT, "INPUT_LIMIT")
    return request


def _response(code: int, raw: bytes, request: dict) -> dict:
    try:
        require(type(code) is int and type(raw) is bytes and len(raw) <= p.MAX_OUTPUT, "PROTOCOL_REJECTED")
        value = strict_json_loads(raw)
        require(type(value) is dict and set(value) == {"version", "operation", "request_id", "ok", "result", "error", "uncertain"}
                and type(value["version"]) is int and value["version"] == 1
                and value["operation"] == request["operation"] and value["request_id"] == request["request_id"]
                and type(value["ok"]) is bool and type(value["uncertain"]) is bool, "PROTOCOL_REJECTED")
        if not value["ok"]:
            require(code == 20 and value["result"] is None and type(value["error"]) is str and value["error"] in ERRORS
                    and (request["operation"] != "audit_accounts" or not value["uncertain"]), "PROTOCOL_REJECTED")
            return {"state": "MANUAL_ACTION" if value["uncertain"] else "REFUSED", "code": value["error"], "result": None}
        require(code == 0 and value["error"] is None and not value["uncertain"], "PROTOCOL_REJECTED")
        result = value["result"]
        keys = {"scope", "tls_verified", "application_installed"}
        fresh = request["operation"] == "prepare_database"
        if fresh: keys |= {"version", "schema_statements", "application_verified", "migration_retained", "assistant_enabled"}
        require(type(result) is dict and set(result) == keys and result["application_installed"] is False
                and result["scope"] == ("DATABASE_READY" if fresh else "ACCOUNTS_VERIFIED")
                and result["tls_verified"] is request["target"]["tls_required"], "PROTOCOL_REJECTED")
        if fresh:
            require(result["version"] == p.ENGINE_VERSION and type(result["schema_statements"]) is int
                    and 1 <= result["schema_statements"] <= 100000 and result["application_verified"] is True
                    and result["assistant_enabled"] is False and result["migration_retained"] is (request["mode"] != "managed"), "PROTOCOL_REJECTED")
        return {"state": result["scope"], "code": "OK", "result": result}
    except DatabaseStepError:
        raise
    except Exception:
        raise DatabaseStepError("PROTOCOL_REJECTED") from None


def _snapshot(runtime: p.PhpRuntime, source: Path, stage: Path, ca: bytes | None):
    p._copy_bundle(source, stage, runtime.worker_gid, ENGINE_FILES, ENGINE_SHA256, "database_step_bridge.php")
    helpers = [(name, p._read_file(Path(__file__).parent / "private" / name))
               for name in ("sql_accounts_policy.php", "trigger_definer.php")]
    for name, content in (*helpers, ("ca.pem", ca)):
        if content is None: continue
        file = stage / name
        with file.open("xb") as stream: stream.write(content)
        os.chown(file, 0, runtime.worker_gid); file.chmod(0o640)


def _marker(runtime: p.PhpRuntime, target: dict, request_id: str) -> int:
    key = hashlib.sha256(p._json([target["host"], target["port"], target["name"].lower()])).hexdigest()
    with _private_directory(runtime.state_root, create=True) as parent:
        try:
            fd = os.open("fresh-" + key + ".attempt", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
        except FileExistsError: raise DatabaseStepError("FRESH_REPLAY_BLOCKED") from None
        try:
            data = p._json({"version": 1, "request_id": request_id, "state": "DISPATCHING"}) + b"\n"
            require(os.write(fd, data) == len(data), "MARKER_WRITE_FAILED")
            os.fsync(fd); os.fsync(parent)
            return fd
        except BaseException: os.close(fd); raise


def _configuration_files(child: int, directory: Path, gid: int, config: dict, payload: dict, ca: bytes | None, result: dict):
    private = {"version": 2, **_target(config, directory / "ca.pem" if ca is not None else None, ca),
               "user": config["database"]["user"], "password": payload["secrets"]["database_password"], "charset": "utf8mb4"}
    template = p._read_file(Path(__file__).parent / "private" / "database_loader_v2.php")
    require(template.count(b"__DATABASE_PATH_HEX__") == 1 and template.count(b"__SERVICE_GID__") == 1, "TEMPLATE_REJECTED")
    loader = template.replace(b"__DATABASE_PATH_HEX__", str(directory / "database.json").encode().hex().encode()).replace(b"__SERVICE_GID__", str(gid).encode())
    fs._write(child, "database.json", p._json(private), gid)
    if ca is not None: fs._write(child, "ca.pem", ca, gid)
    fs._write(child, "db.php", loader, gid)
    fs._write(child, "state.json", p._json(result), gid)
    os.fsync(child); os.fchown(child, 0, gid); os.fchmod(child, 0o750); os.fsync(child)


class DatabaseStep:
    """A private orchestrator API. No registered HTTP/plan action or shell facade."""
    def __init__(self, runtime: p.PhpRuntime, source: Path, *, repository: str, commit: str):
        require(repository == p.WEB_REPOSITORY and commit == WEB_COMMIT
                and re.fullmatch(r"[a-f0-9]{40}", commit) is not None, "SOURCE_PIN_MISMATCH")
        self.runtime, self.source = runtime, Path(source)

    def audit(self, payload: dict, migration: p.ProvisioningCredentials, *, cancel=None) -> dict:
        config = _configuration(payload, migration, None, fresh=False, confirmed=False)
        try:
            p._runtime(self.runtime, config["web"]["service_user"])
            ca = _ca(config)
            require(cancel is None or not cancel.is_set(), "INTERRUPTED")
            with tempfile.TemporaryDirectory(prefix="database-audit-", dir=self.runtime.run_root) as tmp:
                stage = Path(tmp); _snapshot(self.runtime, self.source, stage, ca)
                request = _request(config, payload, migration, None, _target(config, stage / "ca.pem" if ca is not None else None, ca), False)
                code, raw = p._exchange(p._command(self.runtime, stage), p._json(request), stage, self.runtime.timeout_seconds, cancel)
                return _response(code, raw, request)
        except DatabaseStepError: raise
        except Exception: raise DatabaseStepError("AUDIT_FAILED") from None

    def prepare(self, payload: dict, migration: p.ProvisioningCredentials, *, config_root: Path,
                confirmed: bool, authority: SqlAuthorityCredentials | None = None, cancel=None) -> dict:
        """Fresh SQL preparation + protected staging. Never activates/seals the Web.

        Existing resources and prior attempts are never overwritten. After any
        reservation/dispatch uncertainty, preserve the interlock and inspect.
        SQL authority/migration credentials are not stored in the staged files.
        """
        config = _configuration(payload, migration, authority, fresh=True, confirmed=confirmed)
        marker = None; reserved = False
        try:
            p._runtime(self.runtime, config["web"]["service_user"])
            require(isinstance(config_root, Path), "CONFIGURATION_PATH_REJECTED")
            webroot = Path(config["web"]["webroot"])
            require(config_root != webroot and webroot not in config_root.parents
                    and not (str(config_root) == "/var/www" or str(config_root).startswith("/var/www/")
                             or str(config_root) == "/srv" or str(config_root).startswith("/srv/")), "CONFIGURATION_PUBLIC_PATH_REFUSED")
            gid = fs._web_group(config["web"]["service_user"], self.runtime)
            ca = _ca(config)
            require(cancel is None or not cancel.is_set(), "INTERRUPTED")
            slot = fs.configuration_slot(config)
            with fs._directory(config_root, readable_by=gid) as rootfd, fs._directory(webroot) as webfd, \
                    fs._directory(webroot / "includes") as includesfd, \
                    tempfile.TemporaryDirectory(prefix="database-prepare-", dir=self.runtime.run_root) as tmp:
                fs._absent(webfd, "install.lock"); fs._absent(includesfd, "db.php"); fs._absent(rootfd, slot)
                stage = Path(tmp); _snapshot(self.runtime, self.source, stage, ca)
                request = _request(config, payload, migration, authority, _target(config, stage / "ca.pem" if ca is not None else None, ca), True)
                require(cancel is None or not cancel.is_set(), "INTERRUPTED")
                # Reserve the Web slot and database identity durably before the first SQL mutation.
                os.mkdir(slot, 0o700, dir_fd=rootfd); reserved = True; os.fsync(rootfd)
                marker = _marker(self.runtime, request["target"], request["request_id"])
                code, raw = p._exchange(p._command(self.runtime, stage), p._json(request), stage, self.runtime.timeout_seconds, cancel)
                outcome = _response(code, raw, request)
                if outcome["state"] != "DATABASE_READY": return outcome
                require(cancel is None or not cancel.is_set(), "INTERRUPTED")
                fs._absent(webfd, "install.lock"); fs._absent(includesfd, "db.php")
                result = {"scope": "DATABASE_CONFIGURATION_READY", "configuration_activated": False, "application_installed": False,
                          "assistant_enabled": False, "tls_verified": ca is not None, "application_verified": True,
                          "migration_retained": config["database"]["mode"] != "managed", "version": p.ENGINE_VERSION}
                child = os.open(slot, fs._DIRECTORY_FLAGS, dir_fd=rootfd)
                try:
                    fs._no_acl(child)
                    _configuration_files(child, config_root / slot, gid, config, payload, ca, result)
                    os.fsync(rootfd)
                finally: os.close(child)
                data = p._json({"state": "DATABASE_CONFIGURATION_READY", "code": "OK"}) + b"\n"
                require(os.write(marker, data) == len(data), "MARKER_WRITE_FAILED"); os.fsync(marker)
                return {"state": result["scope"], "code": "OK", "result": result}
        except BaseException as error:
            # No unsafe replay, rollback claim, raw PHP output or exception chaining.
            if isinstance(error, (SystemExit, GeneratorExit)): raise
            if marker is not None or reserved:
                return {"state": "MANUAL_ACTION", "code": "DATABASE_PREPARE_INCOMPLETE", "result": None}
            if isinstance(error, DatabaseStepError): raise
            raise DatabaseStepError("LOCAL_PREFLIGHT_FAILED") from None
        finally:
            if marker is not None: os.close(marker)
