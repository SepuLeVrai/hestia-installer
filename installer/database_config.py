"""5B2.2a: stage a protected local SQL configuration, NEVER activate it.

The host orchestrator owns all paths. It must expose neither this module nor its
credential-bearing arguments as a public HTTP/plan operation. Existing files
and incomplete attempts are never overwritten. No SQL/account/schema mutation.
"""
from __future__ import annotations

import grp
import hashlib
import os
import pwd
import stat
from contextlib import contextmanager
from pathlib import Path

from installer import php_transport as transport
from installer.sql_accounts import (
    AccountConfigurationError, VERIFIED, audit_local_accounts, local_configuration, require,
)

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
STAGED = {"scope": "CONFIGURATION_STAGED", "application_installed": False,
          "configuration_activated": False, "accounts_verified": True,
          "policy": "local-dml-v1"}


def _no_acl(fd: int) -> None:
    require(not {"system.posix_acl_access", "system.posix_acl_default"}.intersection(os.listxattr(fd)),
            "CONFIGURATION_ACL_REFUSED")


@contextmanager
def _directory(path: Path, *, readable_by: int | None = None):
    """Pinned dirfds, no following links and no writable/sticky ancestor.

    Existing host-prepared directories only. Do not chmod/chown caller trees.
    """
    require(path.is_absolute() and str(path) == os.path.normpath(path)
            and ".." not in path.parts and len(str(path).encode()) <= 2048,
            "CONFIGURATION_PATH_REJECTED")
    fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in ("", *path.parts[1:]):
            if part:
                next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o7022,
                    "CONFIGURATION_PATH_REJECTED")
            _no_acl(fd)
            if readable_by is not None:
                require(bool(info.st_mode & (stat.S_IXGRP if info.st_gid == readable_by else stat.S_IXOTH)),
                        "WEB_CONFIG_NOT_TRAVERSABLE")
        yield fd
    finally:
        os.close(fd)


def _absent(fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise AccountConfigurationError("CONFIGURATION_TARGET_OCCUPIED")


def _web_group(user: str, runtime: transport.PhpRuntime) -> int:
    account = pwd.getpwnam(user)
    group = grp.getgrgid(account.pw_gid)
    require(account.pw_uid > 0 and account.pw_gid > 0 and account.pw_uid != runtime.worker_uid
            and account.pw_gid != runtime.worker_gid and user not in ("root", "nobody"),
            "WEB_IDENTITY_REJECTED")
    require(set(group.gr_mem) <= {user}
            and all(u.pw_uid == account.pw_uid for u in pwd.getpwall() if u.pw_gid == account.pw_gid),
            "WEB_GROUP_SHARED")
    return account.pw_gid


def configuration_slot(config: dict) -> str:
    """One exclusive slot per canonical Web root, independent of credentials."""
    return hashlib.sha256(config["web"]["webroot"].encode("utf-8")).hexdigest()


def _write(fd: int, name: str, data: bytes, gid: int) -> None:
    require(len(data) <= 16384, "CONFIGURATION_SIZE_REJECTED")
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                  0o600, dir_fd=fd)
    try:
        offset = 0
        while offset < len(data):
            count = os.write(out, data[offset:])
            require(count > 0, "CONFIGURATION_WRITE_FAILED")
            offset += count
        os.fchown(out, 0, gid)
        os.fchmod(out, 0o640)
        _no_acl(out)
        os.fsync(out)
    finally:
        os.close(out)


def _loader(path: Path, gid: int) -> bytes:
    template = transport._read_file(Path(__file__).parent / "private" / "database_loader.php")
    require(template.count(b"__DATABASE_PATH_HEX__") == 1 and template.count(b"__SERVICE_GID__") == 1,
            "CONFIGURATION_TEMPLATE_REJECTED")
    return template.replace(b"__DATABASE_PATH_HEX__", str(path).encode("utf-8").hex().encode("ascii")).replace(
        b"__SERVICE_GID__", str(gid).encode("ascii"))


def stage_local_database_configuration(
    runtime: transport.PhpRuntime, payload: dict, credentials: transport.ProvisioningCredentials,
    *, config_root: Path, confirmed: bool, cancel=None,
) -> dict:
    """Audit, then reserve/write/fsync a private configuration directory.

    config_root must be a pre-existing, root-owned, non-served host directory.
    No loader is copied into the Web root, and no install.lock is created.
    The returned status attests staging only, never schema or Web readiness.
    A partial write remains blocked for manual inspection; no cleanup/retry API.
    """
    created = False
    try:
        require(os.getuid() == 0 and os.geteuid() == 0, "ROOT_ORCHESTRATOR_REQUIRED")
        require(confirmed is True, "CONFIGURATION_CONFIRMATION_REQUIRED")
        config = local_configuration(payload, fresh_only=True)
        require(type(config_root) is Path or isinstance(config_root, Path), "CONFIGURATION_PATH_REJECTED")
        webroot = Path(config["web"]["webroot"])
        require(config_root != webroot and webroot not in config_root.parents
                and not (str(config_root) == "/var/www" or str(config_root).startswith("/var/www/")
                         or str(config_root) == "/srv" or str(config_root).startswith("/srv/")),
                "CONFIGURATION_PUBLIC_PATH_REFUSED")
        gid = _web_group(config["web"]["service_user"], runtime)
        slot = configuration_slot(config)
        # Hold all three dirfds through audit and publication. No existing Web
        # PHP file is evaluated (not even by the privileged parent).
        with _directory(config_root, readable_by=gid) as rootfd, _directory(webroot) as webfd, \
                _directory(webroot / "includes") as includesfd:
            _absent(webfd, "install.lock")
            _absent(includesfd, "db.php")
            _absent(rootfd, slot)
            result = audit_local_accounts(runtime, payload, credentials, cancel=cancel)
            require(result == VERIFIED, "CONFIGURATION_ACCOUNT_AUDIT_REFUSED")
            require(cancel is None or not cancel.is_set(), "CONFIGURATION_INTERRUPTED")
            _absent(webfd, "install.lock")
            _absent(includesfd, "db.php")
            # Exclusive reservation is durable before any secret is written.
            # The directory remains 0700 until all files are complete/fsynced.
            try:
                os.mkdir(slot, 0o700, dir_fd=rootfd)
            except FileExistsError:
                raise AccountConfigurationError("CONFIGURATION_TARGET_OCCUPIED") from None
            created = True
            os.fsync(rootfd)
            child = os.open(slot, _DIRECTORY_FLAGS, dir_fd=rootfd)
            try:
                _no_acl(child)
                database = config["database"]
                private = {"version": 1, "host": "127.0.0.1", "name": database["name"], "user": database["user"],
                           "password": payload["secrets"]["database_password"], "charset": "utf8mb4"}
                _write(child, "database.json", transport._json(private), gid)
                _write(child, "db.php", _loader(config_root / slot / "database.json", gid), gid)
                _write(child, "state.json", transport._json(STAGED), gid)
                os.fsync(child)
                os.fchown(child, 0, gid)
                os.fchmod(child, 0o750)
                os.fsync(child)
                os.fsync(rootfd)
            finally:
                os.close(child)
        return dict(STAGED)
    except Exception as error:
        # Do not pretend that a write/fsync failure rolled back a configuration.
        if created:
            raise AccountConfigurationError("CONFIGURATION_MANUAL_ACTION") from None
        if isinstance(error, AccountConfigurationError):
            raise
        raise AccountConfigurationError("CONFIGURATION_LOCAL_PREFLIGHT_FAILED") from None
