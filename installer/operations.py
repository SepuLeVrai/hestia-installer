"""Trusted Python adapters. There is no dynamic import or browser command runner."""
from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
import threading

from installer import __version__

from installer.model import ErrorCode, InstallerError, Receipt, StepSpec, canonical_bytes, identifier, require
from installer.preflight import bootstrap_blockers, run_read_only_preflight


class SecretVault:
    """Process-local credentials; never part of a plan, receipt, or journal.

    clear() drops references. Python does not guarantee secure erasure of copies.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "<SecretVault ephemeral>"

    def __reduce__(self):
        raise TypeError("SecretVault cannot be serialized")

    def put(self, name: str, value: str) -> None:
        identifier(name)
        require(type(value) is str and 0 < len(value) <= 16384)
        with self._lock:
            self._values[name] = value

    def require(self, name: str) -> str:
        identifier(name)
        with self._lock:
            if name not in self._values:
                raise InstallerError(ErrorCode.SECRET_REQUIRED)
            return self._values[name]

    def reject_in(self, payload: Any) -> None:
        # Scan decoded strings as well as JSON: escaping must not hide a known secret.
        def visit(value: Any) -> None:
            if isinstance(value, str):
                require(not any(secret in value for secret in self._values.values()), ErrorCode.SECRET_REJECTED)
            elif isinstance(value, dict):
                for key, item in value.items():
                    visit(key)
                    visit(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    visit(item)
        canonical_bytes(payload)
        with self._lock:
            visit(payload)

    def delete(self, name: str) -> None:
        with self._lock:
            self._values.pop(name, None)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


@dataclass(frozen=True, slots=True)
class OperationContext:
    installation_id: str
    spec: dict
    evidence: dict
    secrets: SecretVault

    def require_secret(self, name: str) -> str:
        require(name in self.spec["requires_secrets"])
        return self.secrets.require(name)


class RecoveryDecision(StrEnum):
    RETRY_SAFE = "RETRY_SAFE"
    APPLIED = "APPLIED"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class Recovery:
    decision: RecoveryDecision
    receipt: Receipt | None = None


class Operation(ABC):
    """A module owns its resources and must prove recovery, never guess it.

    plan/prepare/validate/recover are read-only with respect to managed resources.
    apply/commit/rollback must be bounded, use typed arguments, and make uncertain
    effects discoverable using a durable identity (installation_id + step name).
    """

    def __init__(self, spec: StepSpec) -> None:
        self.spec = spec
        spec.as_dict()

    def plan(self) -> StepSpec:
        return self.spec

    @abstractmethod
    def prepare(self, context: OperationContext) -> None:
        """Read-only drift checks. No backup, account, certificate, or schema write."""

    @abstractmethod
    def apply(self, context: OperationContext) -> Receipt:
        """Apply only the approved footprint; return non-secret evidence."""

    @abstractmethod
    def validate(self, context: OperationContext) -> bool:
        """Verify the actual result, not just that a subprocess exited."""

    @abstractmethod
    def commit(self, context: OperationContext) -> None:
        """Establish the durable commit marker, if the adapter needs one."""

    def rollback(self, context: OperationContext) -> bool:
        raise InstallerError(ErrorCode.ROLLBACK_UNSUPPORTED)

    def recover(self, context: OperationContext, phase: str) -> Recovery:
        # No implicit replay of uncertain system or external effects.
        return Recovery(RecoveryDecision.MANUAL)


class OperationRegistry:
    def __init__(self, operations: tuple[Operation, ...]) -> None:
        require(type(operations) is tuple and bool(operations))
        self._operations: dict[str, Operation] = {}
        for operation in operations:
            require(isinstance(operation, Operation))
            spec = operation.plan().as_dict()
            require(spec["name"] not in self._operations)
            self._operations[spec["name"]] = operation

    def specs(self) -> list[StepSpec]:
        return [operation.plan() for operation in self._operations.values()]

    def get(self, spec: dict) -> Operation:
        operation = self._operations.get(spec["name"])
        require(operation is not None, ErrorCode.INCOMPATIBLE_STATE)
        # A different implementation contract requires a deliberate migration.
        require(operation.plan().as_dict() == spec, ErrorCode.INCOMPATIBLE_STATE)
        return operation

    def validate_document(self, document: dict) -> None:
        require(document["installer_version"] == __version__, ErrorCode.INCOMPATIBLE_STATE)
        for spec in document["plan"]["steps"]:
            self.get(spec)


class PreflightOperation(Operation):
    """Read-only core check. Legacy contract is retained for Phase 2 journals."""

    def __init__(self, *, legacy: bool = False) -> None:
        super().__init__(StepSpec(
            name="preflight", operation="preflight.run", module="core", boundary="preflight",
            action="Vérifier les prérequis locaux sans déployer HESTIA", rollback_supported=True,
            warnings=(("Les adaptateurs Web, Gateway, APK et acquisition GitHub ne sont pas encore livrés.",)
                      if legacy else ("Contrôle core uniquement : aucun composant HESTIA n'est déployé.",)),
            adapter_version=1 if legacy else 2,
        ))

    def prepare(self, context: OperationContext) -> None:
        if bootstrap_blockers(run_read_only_preflight()):
            raise InstallerError(ErrorCode.VALIDATION_FAILED)

    def apply(self, context: OperationContext) -> Receipt:
        return Receipt()

    def validate(self, context: OperationContext) -> bool:
        return not bootstrap_blockers(run_read_only_preflight())

    def commit(self, context: OperationContext) -> None:
        pass

    def rollback(self, context: OperationContext) -> bool:
        return True  # Read-only operation: no system effect to undo.

    def recover(self, context: OperationContext, phase: str) -> Recovery:
        if phase == "rollback":
            return Recovery(RecoveryDecision.ROLLED_BACK)
        return Recovery(RecoveryDecision.RETRY_SAFE)


def default_registry() -> OperationRegistry:
    return OperationRegistry((PreflightOperation(),))
