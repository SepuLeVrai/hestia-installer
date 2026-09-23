from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InstallState(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"


@dataclass(slots=True)
class StepRecord:
    name: str
    state: InstallState = InstallState.PLANNED
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "details": self.details,
        }


# Transaction schema: the legacy StepRecord remains available, but its arbitrary
# `details` mapping is deliberately NOT a persistence format for the engine.
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from installer import __version__
from installer.validation import validate_fqdn

SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_STEPS = 128
MAX_RESOURCES = 128
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SECRET_PATTERN = re.compile(
    r"github_pat_|gh[pousr]_[A-Za-z0-9]|-----BEGIN [A-Z ]*PRIVATE KEY|"
    r"(?:password|passwd|token|authorization|cookie|client_secret)\s*[:=]",
    re.IGNORECASE,
)


class ErrorCode(StrEnum):
    INVALID_DATA = "INVALID_DATA"
    UNSAFE_STATE_PATH = "UNSAFE_STATE_PATH"
    INVALID_STATE = "INVALID_STATE"
    INCOMPATIBLE_STATE = "INCOMPATIBLE_STATE"
    BUSY = "BUSY"
    NOT_PLANNED = "NOT_PLANNED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    PLAN_EXISTS = "PLAN_EXISTS"
    DEPENDENCY_BLOCKED = "DEPENDENCY_BLOCKED"
    TARGETED_RETRY_REQUIRED = "TARGETED_RETRY_REQUIRED"
    OPERATION_FAILED = "OPERATION_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    SECRET_REQUIRED = "SECRET_REQUIRED"
    SECRET_REJECTED = "SECRET_REJECTED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    ROLLBACK_UNSUPPORTED = "ROLLBACK_UNSUPPORTED"
    UNSUPPORTED_MODULE = "UNSUPPORTED_MODULE"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    GITHUB_ACCESS_DENIED = "GITHUB_ACCESS_DENIED"
    GITHUB_RATE_LIMITED = "GITHUB_RATE_LIMITED"
    GITHUB_UNAVAILABLE = "GITHUB_UNAVAILABLE"
    GITHUB_INVALID_RESPONSE = "GITHUB_INVALID_RESPONSE"
    GITHUB_REDIRECT_REJECTED = "GITHUB_REDIRECT_REJECTED"
    ARCHIVE_REJECTED = "ARCHIVE_REJECTED"
    SOURCE_LIMIT = "SOURCE_LIMIT"
    SOURCE_DRIFT = "SOURCE_DRIFT"
    SOURCE_LAYOUT_UNSUPPORTED = "SOURCE_LAYOUT_UNSUPPORTED"


class InstallerError(RuntimeError):
    """Only a fixed error code can cross a persistence/HTTP/CLI boundary."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = ErrorCode(code)
        super().__init__(self.code.value)


def require(condition: bool, code: ErrorCode = ErrorCode.INVALID_DATA) -> None:
    if not condition:
        raise InstallerError(code)


def identifier(value: Any) -> str:
    require(type(value) is str and _IDENTIFIER.fullmatch(value) is not None)
    require(not _SECRET_PATTERN.search(value), ErrorCode.SECRET_REJECTED)
    return value


def text(value: Any, maximum: int = 512) -> str:
    require(type(value) is str and 0 < len(value) <= maximum)
    require(value.isprintable())
    require(not _SECRET_PATTERN.search(value), ErrorCode.SECRET_REJECTED)
    return value


def absolute_path(value: Any) -> str:
    text(value, 4096)
    require(value.startswith("/") and not value.startswith("//"))
    require(value != "/" and all(part not in {"", ".", ".."} for part in value.split("/")[1:]))
    require(str(PurePosixPath(value)) == value)
    return value


def integer(value: Any, low: int, high: int) -> int:
    require(type(value) is int and low <= value <= high)
    return value


def exact_keys(value: Any, keys: set[str]) -> None:
    require(type(value) is dict and set(value) == keys)


def sequence(value: Any, limit: int = MAX_STEPS) -> list:
    require(type(value) is list and len(value) <= limit)
    return value


def identifiers(value: Any, limit: int = MAX_STEPS) -> list[str]:
    sequence(value, limit)
    result = [identifier(item) for item in value]
    require(len(result) == len(set(result)))
    return result


def timestamp(value: Any) -> str:
    text(value, 32)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InstallerError(ErrorCode.INVALID_DATA) from None
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0)
    return value


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical_bytes(value: Any) -> bytes:
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise InstallerError(ErrorCode.INVALID_DATA) from None
    require(len(result) <= MAX_DOCUMENT_BYTES)
    return result


def strict_json_loads(data: bytes | str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def invalid_number(_value: str) -> None:
        raise InstallerError(ErrorCode.INVALID_DATA)

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_number)
    except (ValueError, UnicodeError, RecursionError):
        raise InstallerError(ErrorCode.INVALID_DATA) from None


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    name: str
    kind: str
    target: str | int
    preexisting: bool = False
    rollback: str = "none"
    backup: str | None = None

    def as_dict(self) -> dict:
        result = {"name": self.name, "kind": self.kind, "target": self.target,
                  "preexisting": self.preexisting, "rollback": self.rollback, "backup": self.backup}
        validate_resource(result)
        return result


def validate_resource(value: Any) -> None:
    exact_keys(value, {"name", "kind", "target", "preexisting", "rollback", "backup"})
    identifier(value["name"])
    require(type(value["preexisting"]) is bool)
    require(type(value["kind"]) is str and value["kind"] in {"file", "directory", "service", "port", "fqdn", "external"})
    require(type(value["rollback"]) is str and value["rollback"] in {"none", "delete-created", "restore-backup", "compensate"})
    if value["kind"] in {"file", "directory"}:
        absolute_path(value["target"])
    elif value["kind"] == "port":
        integer(value["target"], 1, 65535)
    elif value["kind"] == "fqdn":
        text(value["target"], 253)
        try:
            require(validate_fqdn(value["target"]) == value["target"])
        except ValueError:
            raise InstallerError(ErrorCode.INVALID_DATA) from None
    else:
        identifier(value["target"])
    if value["backup"] is not None:
        absolute_path(value["backup"])
        require(value["preexisting"] and value["backup"] != value["target"])
    if value["rollback"] == "restore-backup":
        require(value["backup"] is not None and value["preexisting"])
    if value["rollback"] == "delete-created":
        require(not value["preexisting"])


@dataclass(frozen=True, slots=True)
class SourceSpec:
    repository: str
    ref: str
    commit_sha: str

    def as_dict(self) -> dict:
        text(self.repository, 128)
        require(re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", self.repository) is not None)
        text(self.ref, 200)
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", self.ref) is not None)
        require(".." not in self.ref and not self.ref.endswith(("/", ".", ".lock"))
                and all(part and not part.startswith(".") for part in self.ref.split("/")))
        require(type(self.commit_sha) is str and _COMMIT.fullmatch(self.commit_sha) is not None)
        return {"repository": self.repository, "ref": self.ref, "commit_sha": self.commit_sha}


@dataclass(frozen=True, slots=True)
class StepSpec:
    name: str
    operation: str
    module: str
    boundary: str
    action: str
    dependencies: tuple[str, ...] = ()
    resources: tuple[ResourceSpec, ...] = ()
    rollback_supported: bool = False
    warnings: tuple[str, ...] = ()
    manual_actions: tuple[str, ...] = ()
    requires_secrets: tuple[str, ...] = ()
    adapter_version: int = 1
    source: SourceSpec | None = None

    def as_dict(self) -> dict:
        result = {
            "name": self.name, "operation": self.operation, "module": self.module,
            "boundary": self.boundary, "action": self.action,
            "dependencies": list(self.dependencies), "resources": [r.as_dict() for r in self.resources],
            "rollback_supported": self.rollback_supported, "warnings": list(self.warnings),
            "manual_actions": list(self.manual_actions), "requires_secrets": list(self.requires_secrets),
            "adapter_version": self.adapter_version,
        }
        # Omit absent source: Phase 2 plans and digests remain byte-compatible.
        if self.source is not None:
            result["source"] = self.source.as_dict()
        validate_step_spec(result)
        return result


def validate_step_spec(value: Any) -> None:
    require(type(value) is dict)
    keys = {"name", "operation", "module", "boundary", "action", "dependencies", "resources",
            "rollback_supported", "warnings", "manual_actions", "requires_secrets", "adapter_version"}
    if "source" in value:
        keys.add("source")
        exact_keys(value["source"], {"repository", "ref", "commit_sha"})
        SourceSpec(**value["source"]).as_dict()
    exact_keys(value, keys)
    for key in ("name", "operation", "module", "boundary"):
        identifier(value[key])
    text(value["action"])
    integer(value["adapter_version"], 1, 10000)
    identifiers(value["dependencies"])
    identifiers(value["requires_secrets"], 16)
    require(type(value["rollback_supported"]) is bool)
    for key in ("warnings", "manual_actions"):
        for item in sequence(value[key], 32):
            text(item, 1024)
    resources = sequence(value["resources"], MAX_RESOURCES)
    for item in resources:
        validate_resource(item)
    require(len({item["name"] for item in resources}) == len(resources))
    if value["rollback_supported"]:
        require(all(item["rollback"] != "none" for item in resources))


def build_plan(specs: list[StepSpec], *, mode: str = "check", warnings: tuple[str, ...] = ()) -> dict:
    plan = {
        "installation_id": str(uuid4()), "installer_version": __version__, "created_at": now(),
        "mode": mode, "modules": sorted({step.module for step in specs}),
        "steps": [step.as_dict() for step in specs], "warnings": list(warnings),
    }
    validate_plan(plan)
    return plan


def validate_plan(plan: Any) -> None:
    exact_keys(plan, {"installation_id", "installer_version", "created_at", "mode", "modules", "steps", "warnings"})
    try:
        require(type(plan["installation_id"]) is str and str(UUID(plan["installation_id"])) == plan["installation_id"])
    except (ValueError, AttributeError):
        raise InstallerError(ErrorCode.INVALID_DATA) from None
    text(plan["installer_version"], 64)
    timestamp(plan["created_at"])
    require(type(plan["mode"]) is str and plan["mode"] in {"check", "fresh", "upgrade"})
    modules = identifiers(plan["modules"], 32)
    steps = sequence(plan["steps"])
    require(bool(steps))  # An empty plan must never be reported as a successful installation.
    seen = set()
    targets = set()
    for step in steps:
        validate_step_spec(step)
        require(step["name"] not in seen and set(step["dependencies"]) <= seen)
        seen.add(step["name"])
        for resource in step["resources"]:
            kind = "filesystem" if resource["kind"] in {"file", "directory"} else resource["kind"]
            target = (kind, resource["target"])
            require(target not in targets)  # Shared resources require a single owning adapter.
            targets.add(target)
            if resource["backup"] is not None:
                backup = ("filesystem", resource["backup"])
                require(backup not in targets)
                targets.add(backup)
    require(modules == sorted({step["module"] for step in steps}))
    for warning in sequence(plan["warnings"], 32):
        text(warning, 1024)
    canonical_bytes(plan)


def plan_digest(plan: dict) -> str:
    validate_plan(plan)
    return hashlib.sha256(canonical_bytes(plan)).hexdigest()


@dataclass(frozen=True, slots=True)
class Receipt:
    """Evidence only: resource names and hashes, never command output or secrets."""
    created_resources: tuple[str, ...] = ()
    backups: tuple[str, ...] = ()
    commit_shas: tuple[tuple[str, str], ...] = ()
    hashes_non_secret: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict:
        require(len(dict(self.commit_shas)) == len(self.commit_shas))
        require(len(dict(self.hashes_non_secret)) == len(self.hashes_non_secret))
        return {"created_resources": list(self.created_resources), "backups": list(self.backups),
                "commit_shas": dict(self.commit_shas), "hashes_non_secret": dict(self.hashes_non_secret)}


def validate_receipt(value: Any, spec: dict) -> None:
    exact_keys(value, {"created_resources", "backups", "commit_shas", "hashes_non_secret"})
    created = identifiers(value["created_resources"], MAX_RESOURCES)
    backups = identifiers(value["backups"], MAX_RESOURCES)
    resources = {item["name"]: item for item in spec["resources"]}
    require(set(created) <= {key for key, item in resources.items() if not item["preexisting"]})
    require(set(backups) <= {key for key, item in resources.items() if item["backup"] is not None})
    for key, pattern in (("commit_shas", _COMMIT), ("hashes_non_secret", _SHA256)):
        mapping = value[key]
        require(type(mapping) is dict and len(mapping) <= 64)
        for name, digest in mapping.items():
            identifier(name)
            require(type(digest) is str and pattern.fullmatch(digest) is not None)


def aggregate(document: dict) -> dict:
    result = {"resources_created": [], "resources_preexisting": [], "backups": [],
              "commit_shas": {}, "hashes_non_secret": {}}
    for spec, record in zip(document["plan"]["steps"], document["steps"]):
        prefix = spec["name"] + ":"
        resources = {item["name"]: item for item in spec["resources"]}
        result["resources_preexisting"].extend(prefix + key for key, item in resources.items() if item["preexisting"])
        # Historical evidence is retained even after rollback, with the step state.
        result["resources_created"].extend(prefix + key for key in record["evidence"]["created_resources"])
        result["backups"].extend({"resource": prefix + key, "path": resources[key]["backup"]}
                                 for key in record["evidence"]["backups"])
        for field in ("commit_shas", "hashes_non_secret"):
            result[field].update({prefix + key: value for key, value in record["evidence"][field].items()})
    return result


def initial_document(plan: dict) -> dict:
    validate_plan(plan)
    document = {key: plan[key] for key in ("installation_id", "installer_version", "created_at", "mode", "modules")}
    document.update({
        "schema_version": SCHEMA_VERSION, "updated_at": plan["created_at"], "revision": 0,
        "plan": plan, "plan_sha256": plan_digest(plan), "approved_plan_sha256": None,
        "steps": [{"name": step["name"], "state": "PLANNED", "phase": "planned", "attempts": 0,
                   "evidence": Receipt().as_dict(), "last_error_redacted": None} for step in plan["steps"]],
        "state": "PLANNED", "last_error_redacted": None, "rollback_boundary": None,
    })
    document.update(aggregate(document))
    validate_document(document)
    return document


def validate_document(document: Any) -> None:
    exact_keys(document, {"schema_version", "installation_id", "installer_version", "created_at", "updated_at",
                          "revision", "mode", "modules", "plan", "plan_sha256", "approved_plan_sha256", "steps",
                          "state", "last_error_redacted", "rollback_boundary", "resources_created",
                          "resources_preexisting", "backups", "commit_shas", "hashes_non_secret"})
    require(type(document["schema_version"]) is int and document["schema_version"] == SCHEMA_VERSION,
            ErrorCode.INCOMPATIBLE_STATE)
    validate_plan(document["plan"])
    for key in ("installation_id", "installer_version", "created_at", "mode", "modules"):
        require(document[key] == document["plan"][key])
    integer(document["revision"], 0, 2**63 - 1)
    timestamp(document["updated_at"])
    require(document["plan_sha256"] == plan_digest(document["plan"]))
    require(document["approved_plan_sha256"] is None or document["approved_plan_sha256"] == document["plan_sha256"])
    require(type(document["state"]) is str and document["state"] in {s.value for s in InstallState})
    require(document["last_error_redacted"] is None or (type(document["last_error_redacted"]) is str and document["last_error_redacted"] in {c.value for c in ErrorCode}))
    if document["rollback_boundary"] is not None:
        identifier(document["rollback_boundary"])
        require(document["rollback_boundary"] in {s["boundary"] for s in document["plan"]["steps"]})
    steps = sequence(document["steps"])
    require(len(steps) == len(document["plan"]["steps"]))
    for spec, record in zip(document["plan"]["steps"], steps):
        exact_keys(record, {"name", "state", "phase", "attempts", "evidence", "last_error_redacted"})
        require(record["name"] == spec["name"])
        require(type(record["state"]) is str and record["state"] in {s.value for s in InstallState})
        require(type(record["phase"]) is str and record["phase"] in {"planned", "prepare", "apply", "validate", "commit", "done", "rollback", "rolled_back"})
        integer(record["attempts"], 0, 10000)
        require(record["last_error_redacted"] is None or (type(record["last_error_redacted"]) is str and record["last_error_redacted"] in {c.value for c in ErrorCode}))
        validate_receipt(record["evidence"], spec)
        if record["state"] in {"FAILED", "MANUAL_ACTION_REQUIRED"}:
            require(record["last_error_redacted"] is not None)
        else:
            require(record["last_error_redacted"] is None)
        if record["phase"] in {"validate", "commit", "done"}:
            require(set(record["evidence"]["created_resources"]) ==
                    {r["name"] for r in spec["resources"] if not r["preexisting"]})
            require(set(record["evidence"]["backups"]) >=
                    {r["name"] for r in spec["resources"] if r["rollback"] == "restore-backup"})
        if record["state"] in {"PLANNED", "DONE", "ROLLED_BACK"}:
            require(record["phase"] == {"PLANNED": "planned", "DONE": "done", "ROLLED_BACK": "rolled_back"}[record["state"]])
        else:
            require(record["phase"] in {"prepare", "apply", "validate", "commit", "rollback"})
    if document["rollback_boundary"] is not None:
        require(document["approved_plan_sha256"] == document["plan_sha256"])
    for record in steps:
        if record["phase"] == "rollback":
            require(document["rollback_boundary"] is not None)
    if any(step["state"] != "PLANNED" for step in steps):
        require(document["approved_plan_sha256"] == document["plan_sha256"])
    states_by_name = {record["name"]: record["state"] for record in steps}
    for spec, record in zip(document["plan"]["steps"], steps):
        if record["phase"] in {"apply", "validate", "commit", "done"}:
            require(all(states_by_name[name] == "DONE" for name in spec["dependencies"]))
    if document["state"] == "DONE":
        require(all(step["state"] == "DONE" for step in steps))
    for field, expected in aggregate(document).items():
        require(document[field] == expected)
    canonical_bytes(document)
