"""Non-secret wizard draft and read-only checks. No generic command execution."""
from __future__ import annotations

import os
import secrets
from copy import deepcopy

from installer.github_client import REPOSITORIES
from installer.model import (
    ErrorCode, SourceSpec, canonical_bytes, exact_keys, integer, require, strict_json_loads,
)
from installer.preflight import run_read_only_preflight
from installer.transaction import _FILE_FLAGS, _check_file, _private_directory

LIMIT = 8192
CHECKS = {
    "os": "Debian 12 ou 13", "root": "Droits administrateur",
    "python": "Python 3.11 ou supérieur", "command:python3": "Exécutable Python",
    "command:openssl": "OpenSSL", "command:ip": "iproute2",
}


def preflight_snapshot() -> dict:
    """Never serialize raw environment/command output or exception messages."""
    results = run_read_only_preflight()
    by_name = {item.name: item for item in results}
    checks = [{"name": name, "label": label, "ok": name in by_name and by_name[name].ok is True}
              for name, label in CHECKS.items()]
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def validate_draft(value: dict) -> None:
    exact_keys(value, {"revision", "step", "modules", "refs", "mode"})
    integer(value["revision"], 0, 1000000)
    integer(value["step"], 0, 3)
    require(value["mode"] in ("fresh", "upgrade"))
    modules = value["modules"]
    require(type(modules) is list and len(modules) <= 3)
    require(all(type(m) is str and m in REPOSITORIES for m in modules))
    require(len(set(modules)) == len(modules))
    require(type(value["refs"]) is dict and set(value["refs"]) <= set(modules))
    for module, ref in value["refs"].items():
        SourceSpec(REPOSITORIES[module], ref, "0" * 40).as_dict()


class WizardDraft:
    """Small closed schema, same pinned private directory and lock as the engine."""
    def __init__(self, engine) -> None:
        self.engine = engine

    @staticmethod
    def default() -> dict:
        return {"revision": 0, "step": 0, "modules": ["web"], "refs": {}, "mode": "fresh"}

    def _read_at(self, directory_fd: int) -> dict:
        try:
            fd = os.open("wizard.json", os.O_RDONLY | _FILE_FLAGS, dir_fd=directory_fd)
        except FileNotFoundError:
            return self.default()
        try:
            _check_file(fd, allow_unlinked=True)
            require(0 < os.fstat(fd).st_size <= LIMIT, ErrorCode.INVALID_STATE)
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(LIMIT + 1)
            require(0 < len(data) <= LIMIT, ErrorCode.INVALID_STATE)
            result = strict_json_loads(data)
            validate_draft(result)
            self.engine.secrets.reject_in(result)
            return result
        finally:
            os.close(fd)

    def read(self) -> dict:
        try:
            with _private_directory(self.engine.journal.path.parent, create=False) as fd:
                return self._read_at(fd)
        except FileNotFoundError:
            return self.default()

    def save(self, payload: dict) -> dict:
        validate_draft(payload)
        self.engine.secrets.reject_in(payload)
        with self.engine.journal.locked(create=True) as locked:
            require(locked.read() is None, ErrorCode.PLAN_EXISTS)
            fd = locked.directory_fd
            current = self._read_at(fd)
            require(payload["revision"] == current["revision"], ErrorCode.BUSY)
            result = deepcopy(payload)
            result["revision"] += 1
            validate_draft(result)
            data = canonical_bytes(result) + b"\n"
            require(len(data) <= LIMIT)
            name = ".wizard-" + secrets.token_hex(16) + ".tmp"
            file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
            try:
                os.fchmod(file_fd, 0o600)
                _check_file(file_fd)
                with os.fdopen(file_fd, "wb", closefd=False) as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(file_fd)
                require(self._read_at(fd) == current, ErrorCode.BUSY)
                os.replace(name, "wizard.json", src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                os.close(file_fd)
                try:
                    os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
        return result
