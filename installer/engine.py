"""Transactional execution with explicit confirmation and conservative recovery."""
from __future__ import annotations

import hmac
from copy import deepcopy
from typing import Callable

from installer.model import (
    ErrorCode, InstallerError, Receipt, aggregate, build_plan, identifier,
    initial_document, now, require, validate_document, validate_receipt,
)
from installer.operations import (
    OperationContext, OperationRegistry, Recovery, RecoveryDecision, SecretVault,
)
from installer.transaction import StateJournal

# A test-only constructor hook, never exposed through HTTP, CLI, or environment.
FaultHook = Callable[[str, str, str], None]


class TransactionEngine:
    def __init__(self, journal: StateJournal, registry: OperationRegistry, *,
                 secrets: SecretVault | None = None, fault_hook: FaultHook | None = None) -> None:
        self.journal = journal
        self.registry = registry
        self.secrets = secrets if secrets is not None else SecretVault()
        self._fault_hook = fault_hook

    def dry_run(self, *, mode: str = "check") -> dict:
        plan = build_plan(self.registry.specs(), mode=mode)
        self.secrets.reject_in(plan)
        return plan

    def report(self) -> dict | None:
        document = self.journal.read()
        if document is not None:
            self.secrets.reject_in(document)
        return document

    def _compatible(self, document: dict) -> None:
        validate_document(document)
        self.registry.validate_document(document)
        self.secrets.reject_in(document)

    def plan(self, *, mode: str = "check") -> dict:
        # Repeated planning/refresh returns the immutable existing plan, not a new id.
        existing = self.report()
        if existing is not None:
            self._compatible(existing)
            require(existing["mode"] == mode, ErrorCode.PLAN_EXISTS)
            return existing
        plan = self.dry_run(mode=mode)
        document = initial_document(plan)
        with self.journal.locked(create=True) as locked:
            current = locked.read()
            if current is not None:
                self._compatible(current)
                require(current["mode"] == mode, ErrorCode.PLAN_EXISTS)
                return current
            locked.write(document, expected_revision=None)
        return deepcopy(document)

    def _event(self, record: dict, phase: str, event: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(record["name"], phase, event)

    def _save(self, locked, document: dict) -> None:
        revision = document["revision"]
        document["revision"] += 1
        document["updated_at"] = now()
        document.update(aggregate(document))
        self.secrets.reject_in(document)
        locked.write(document, expected_revision=revision)

    def _load(self, locked, confirmation: str) -> dict:
        document = locked.read()
        require(document is not None, ErrorCode.NOT_PLANNED)
        self._compatible(document)
        require(type(confirmation) is str and len(confirmation) == 64 and confirmation.isascii()
                and hmac.compare_digest(confirmation, document["plan_sha256"]), ErrorCode.CONFIRMATION_REQUIRED)
        return document

    def _approve(self, locked, document: dict) -> None:
        if document["approved_plan_sha256"] is None:
            document["approved_plan_sha256"] = document["plan_sha256"]
            self._save(locked, document)

    def _context(self, document: dict, spec: dict, record: dict) -> OperationContext:
        return OperationContext(document["installation_id"], deepcopy(spec), deepcopy(record["evidence"]), self.secrets)

    def _checkpoint(self, locked, document: dict, record: dict, phase: str) -> None:
        record.update(state="RUNNING", phase=phase, last_error_redacted=None)
        document.update(state="RUNNING", last_error_redacted=None)
        self._save(locked, document)
        self._event(record, phase, "checkpoint")

    def _fail(self, locked, document: dict, record: dict, code: ErrorCode, *, manual: bool = False) -> None:
        state = "MANUAL_ACTION_REQUIRED" if manual else "FAILED"
        record.update(state=state, last_error_redacted=code.value)
        document.update(state=state, last_error_redacted=code.value)
        self._save(locked, document)

    def _receipt(self, document: dict, spec: dict, record: dict, receipt: Receipt) -> None:
        require(type(receipt) is Receipt)
        evidence = receipt.as_dict()
        validate_receipt(evidence, spec)
        self.secrets.reject_in(evidence)
        # Successful apply/recovery must account for the complete approved footprint.
        require(set(evidence["created_resources"]) == {r["name"] for r in spec["resources"] if not r["preexisting"]})
        require(set(evidence["backups"]) >= {r["name"] for r in spec["resources"] if r["rollback"] == "restore-backup"})
        record["evidence"] = evidence

    @staticmethod
    def _dependencies_done(document: dict, spec: dict) -> bool:
        states = {step["name"]: step["state"] for step in document["steps"]}
        return all(states[name] == "DONE" for name in spec["dependencies"])

    def _drive(self, locked, document: dict, spec: dict, record: dict, start: str) -> bool:
        operation = self.registry.get(spec)
        phases = ("prepare", "apply", "validate", "commit")
        require(start in phases)
        if start == "prepare":
            require(record["attempts"] < 10000, ErrorCode.MANUAL_ACTION_REQUIRED)
            record["attempts"] += 1
        for phase in phases[phases.index(start):]:
            # The checkpoint must succeed BEFORE the managed-resource callback.
            self._checkpoint(locked, document, record, phase)
            self._event(record, phase, "before")
            try:
                context = self._context(document, spec, record)
                if phase == "apply":
                    for name in spec["requires_secrets"]:
                        context.require_secret(name)
                    receipt = operation.apply(context)
                    self._receipt(document, spec, record, receipt)
                elif phase == "validate":
                    require(operation.validate(context) is True, ErrorCode.VALIDATION_FAILED)
                else:
                    getattr(operation, phase)(context)  # Phase is a closed, server-owned tuple.
            except Exception as exc:
                code = exc.code if isinstance(exc, InstallerError) else ErrorCode.OPERATION_FAILED
                self._fail(locked, document, record, code)
                return False
            self._event(record, phase, "after")
        record.update(state="DONE", phase="done", last_error_redacted=None)
        self._save(locked, document)
        self._event(record, "done", "checkpoint")
        return True

    def _reconcile(self, locked, document: dict, spec: dict, record: dict) -> str | None:
        phase = record["phase"]
        if phase in {"prepare", "validate"}:
            return phase  # Read-only callbacks, safe to repeat.
        operation = self.registry.get(spec)
        self._event(record, phase, "before_recover")
        try:
            recovery = operation.recover(self._context(document, spec, record), phase)
            require(type(recovery) is Recovery and type(recovery.decision) is RecoveryDecision)
            if recovery.receipt is not None:
                self._receipt(document, spec, record, recovery.receipt)
            decision = recovery.decision
            if decision == RecoveryDecision.RETRY_SAFE:
                return phase
            if decision == RecoveryDecision.APPLIED and phase in {"apply", "commit"}:
                # On an ambiguous apply, the adapter must reconstruct the receipt.
                if phase == "apply":
                    require(recovery.receipt is not None)
                return "validate"  # Revalidate after downtime even if commit was next.
            if decision == RecoveryDecision.COMMITTED and phase in {"apply", "commit"}:
                if phase == "apply":
                    require(recovery.receipt is not None)
                require(operation.validate(self._context(document, spec, record)) is True,
                        ErrorCode.MANUAL_ACTION_REQUIRED)
                record.update(state="DONE", phase="done", last_error_redacted=None)
                self._save(locked, document)
                return None
            if decision == RecoveryDecision.ROLLED_BACK and phase == "rollback":
                record.update(state="ROLLED_BACK", phase="rolled_back", last_error_redacted=None)
                self._save(locked, document)
                return None
        except Exception as exc:
            code = exc.code if isinstance(exc, InstallerError) else ErrorCode.MANUAL_ACTION_REQUIRED
            self._fail(locked, document, record, code, manual=code != ErrorCode.SECRET_REQUIRED)
            return None
        self._fail(locked, document, record, ErrorCode.MANUAL_ACTION_REQUIRED, manual=True)
        return None

    def _finish(self, locked, document: dict) -> None:
        states = {record["state"] for record in document["steps"]}
        error = None
        # A targeted retry must not hide another independent failed boundary.
        for candidate in ("MANUAL_ACTION_REQUIRED", "FAILED", "RUNNING"):
            if candidate in states:
                state = candidate
                error = next(r["last_error_redacted"] for r in document["steps"] if r["state"] == candidate)
                break
        else:
            state = "DONE" if states == {"DONE"} else (
                "ROLLED_BACK" if "ROLLED_BACK" in states and states <= {"PLANNED", "ROLLED_BACK"} else "PLANNED")
        if document["state"] != state or document["last_error_redacted"] != error:
            document.update(state=state, last_error_redacted=error)
            self._save(locked, document)

    def discard_unapproved(self, confirmation: str) -> None:
        """Explicitly abandon only a plan which has never been approved or run."""
        with self.journal.locked() as locked:
            document = self._load(locked, confirmation)
            locked.discard_unapproved(document)

    def apply(self, confirmation: str) -> dict:
        return self._forward(confirmation, resume=False)

    def resume(self, confirmation: str) -> dict:
        return self._forward(confirmation, resume=True)

    def _forward(self, confirmation: str, *, resume: bool) -> dict:
        with self.journal.locked() as locked:
            document = self._load(locked, confirmation)
            if document["rollback_boundary"] is not None:
                require(resume, ErrorCode.TARGETED_RETRY_REQUIRED)
                self._rollback_locked(locked, document, document["rollback_boundary"])
                return deepcopy(document)
            states = {r["state"] for r in document["steps"]}
            require(not states & {"FAILED", "MANUAL_ACTION_REQUIRED", "ROLLED_BACK"}, ErrorCode.TARGETED_RETRY_REQUIRED)
            require(resume or "RUNNING" not in states, ErrorCode.TARGETED_RETRY_REQUIRED)
            self._approve(locked, document)
            for spec, record in zip(document["plan"]["steps"], document["steps"]):
                if record["state"] == "DONE":
                    continue
                require(self._dependencies_done(document, spec), ErrorCode.DEPENDENCY_BLOCKED)
                start = self._reconcile(locked, document, spec, record) if record["state"] == "RUNNING" else "prepare"
                if start is None:
                    if record["state"] == "DONE":
                        continue
                    break
                if not self._drive(locked, document, spec, record, start):
                    break
            self._finish(locked, document)
            return deepcopy(document)

    @staticmethod
    def _block_dependents(document: dict, targets: set[str]) -> None:
        affected = set(targets)
        for spec, record in zip(document["plan"]["steps"], document["steps"]):
            if set(spec["dependencies"]) & affected:
                affected.add(spec["name"])
                if spec["name"] not in targets:
                    require(record["state"] in {"PLANNED", "ROLLED_BACK"}, ErrorCode.DEPENDENCY_BLOCKED)

    def retry(self, name: str, confirmation: str) -> dict:
        identifier(name)
        with self.journal.locked() as locked:
            document = self._load(locked, confirmation)
            require(document["rollback_boundary"] is None, ErrorCode.TARGETED_RETRY_REQUIRED)
            pairs = [(s, r) for s, r in zip(document["plan"]["steps"], document["steps"]) if s["name"] == name]
            require(len(pairs) == 1)
            spec, record = pairs[0]
            require(record["state"] in {"FAILED", "MANUAL_ACTION_REQUIRED", "ROLLED_BACK"}, ErrorCode.TARGETED_RETRY_REQUIRED)
            require(self._dependencies_done(document, spec), ErrorCode.DEPENDENCY_BLOCKED)
            self._block_dependents(document, {name})
            self._approve(locked, document)
            if record["state"] == "ROLLED_BACK":
                record.update(state="PLANNED", phase="planned", evidence=Receipt().as_dict(), last_error_redacted=None)
                start = "prepare"
            else:
                start = self._reconcile(locked, document, spec, record)
            if start is not None:
                self._drive(locked, document, spec, record, start)
            self._finish(locked, document)
            return deepcopy(document)

    def rollback(self, boundary: str, confirmation: str) -> dict:
        identifier(boundary)
        with self.journal.locked() as locked:
            document = self._load(locked, confirmation)
            self._rollback_locked(locked, document, boundary)
            return deepcopy(document)

    def _rollback_locked(self, locked, document: dict, boundary: str) -> None:
        pairs = [(s, r) for s, r in zip(document["plan"]["steps"], document["steps"]) if s["boundary"] == boundary]
        require(bool(pairs))
        require(document["rollback_boundary"] in {None, boundary}, ErrorCode.DEPENDENCY_BLOCKED)
        active = [(s, r) for s, r in pairs if r["state"] not in {"PLANNED", "ROLLED_BACK"}]
        self._block_dependents(document, {s["name"] for s, _ in active})
        for spec, record in active:
            require(record["phase"] == "prepare" or spec["rollback_supported"], ErrorCode.ROLLBACK_UNSUPPORTED)
        if not active:
            if document["rollback_boundary"] is not None:
                document["rollback_boundary"] = None
                self._save(locked, document)
            self._finish(locked, document)
            return
        self._approve(locked, document)
        document["rollback_boundary"] = boundary
        self._save(locked, document)
        for spec, record in reversed(active):
            if record["phase"] == "prepare":
                record.update(state="ROLLED_BACK", phase="rolled_back", last_error_redacted=None)
                self._save(locked, document)
                continue
            if record["phase"] == "rollback":
                start = self._reconcile(locked, document, spec, record)
                if start is None:
                    if record["state"] == "ROLLED_BACK":
                        continue
                    return
            self._checkpoint(locked, document, record, "rollback")
            self._event(record, "rollback", "before")
            try:
                operation = self.registry.get(spec)
                require(operation.rollback(self._context(document, spec, record)) is True, ErrorCode.ROLLBACK_FAILED)
            except Exception:
                self._fail(locked, document, record, ErrorCode.ROLLBACK_FAILED, manual=True)
                return
            self._event(record, "rollback", "after")
            record.update(state="ROLLED_BACK", phase="rolled_back", last_error_redacted=None)
            self._save(locked, document)
        document["rollback_boundary"] = None
        self._save(locked, document)
        self._finish(locked, document)
