"""Sandbox-only adapters; never registered by the production installer."""
import hashlib
import json
from pathlib import Path

from installer.model import ErrorCode, InstallerError, Receipt, ResourceSpec, StepSpec
from installer.operations import Operation, OperationContext, Recovery, RecoveryDecision

ORIGINAL = b"existing resource - do not delete\n"
TEST_ERROR = "untrusted exception: password=EXAMPLE-DO-NOT-PERSIST"


class InjectedCrash(BaseException):
    pass


class FileOperation(Operation):
    def __init__(self, root: Path, name="first", *, boundary=None, dependencies=(),
                 preexisting=False, reversible=True, fail=None, needs_secret=False,
                 entered=None, release=None):
        self.root = root
        self.target = root / (name + ".json")
        self.backup = root / (name + ".bak")
        self.calls_path = root / (name + ".calls")  # Test instrumentation, not adapter output.
        self.fail = fail
        self.entered = entered
        self.release = release
        self.recoveries = []
        resource = ResourceSpec(
            "payload", "file", str(self.target), preexisting,
            ("restore-backup" if preexisting else "delete-created") if reversible else "none",
            str(self.backup) if preexisting else None,
        )
        super().__init__(StepSpec(
            name, "fixture.file", "fixture", boundary or name,
            "Installer une ressource contrôlée dans le sandbox des tests",
            dependencies=dependencies, resources=(resource,), rollback_supported=reversible,
            requires_secrets=("github",) if needs_secret else (),
            manual_actions=() if reversible else ("Effet externe non réversible : intervention opérateur.",),
        ))

    def _log(self, phase):
        with self.calls_path.open("a", encoding="utf-8") as handle:
            handle.write(phase + "\n")

    def calls(self, phase):
        if not self.calls_path.exists():
            return 0
        return self.calls_path.read_text().splitlines().count(phase)

    def _failure(self, phase):
        if self.fail == phase:
            raise RuntimeError(TEST_ERROR)

    def _owned(self, context):
        try:
            data = json.loads(self.target.read_text())
        except (FileNotFoundError, ValueError, UnicodeError):
            return None
        if type(data) is dict and data.get("owner") == context.installation_id + ":" + context.spec["name"]:
            return data
        return None

    def _receipt(self):
        return Receipt(
            created_resources=() if self.spec.resources[0].preexisting else ("payload",),
            backups=("payload",) if self.backup.exists() and self.spec.resources[0].preexisting else (),
            commit_shas=(("source", "a" * 40),),
            hashes_non_secret=(("public_content", hashlib.sha256(b"installed").hexdigest()),),
        )

    def prepare(self, context):
        self._log("prepare")
        self._failure("prepare")

    def apply(self, context):
        self._log("apply")
        if self.entered:
            self.entered.set()
        if self.release:
            if not self.release.wait(timeout=10):
                raise RuntimeError("Test barrier timed out")
        self._failure("apply-before")
        if self.spec.requires_secrets:
            context.require_secret("github")
        if self.spec.resources[0].preexisting and not self.backup.exists():
            with self.backup.open("xb") as handle:
                handle.write(self.target.read_bytes())
            self.backup.chmod(0o600)
        self.target.write_text(json.dumps({"owner": context.installation_id + ":" + context.spec["name"],
                                          "phase": "applied", "content": "installed"}))
        self.target.chmod(0o600)
        self._failure("apply-after")
        return self._receipt()

    def validate(self, context):
        self._log("validate")
        self._failure("validate")
        data = self._owned(context)
        return bool(data and data.get("content") == "installed" and data.get("phase") in {"applied", "committed"})

    def commit(self, context):
        self._log("commit")
        self._failure("commit-before")
        data = self._owned(context)
        if data is None:
            raise RuntimeError("Ownership not proven")
        data["phase"] = "committed"
        self.target.write_text(json.dumps(data))
        self._failure("commit-after")

    def rollback(self, context):
        self._log("rollback")
        self._failure("rollback-before")
        if self._owned(context) is None:
            raise RuntimeError("Ownership not proven; refuse deletion")
        if self.spec.resources[0].preexisting:
            self.target.write_bytes(self.backup.read_bytes())
        else:
            self.target.unlink()
        self._failure("rollback-after")
        return True

    def recover(self, context, phase):
        self.recoveries.append(phase)
        data = self._owned(context)
        if phase == "rollback":
            restored = (self.target.exists() and self.target.read_bytes() == ORIGINAL
                        if self.spec.resources[0].preexisting else not self.target.exists())
            if restored:
                return Recovery(RecoveryDecision.ROLLED_BACK)
            return Recovery(RecoveryDecision.RETRY_SAFE if data else RecoveryDecision.MANUAL)
        if data:
            return Recovery(RecoveryDecision.COMMITTED if data["phase"] == "committed" else RecoveryDecision.APPLIED,
                            self._receipt())
        untouched = (self.target.exists() and self.target.read_bytes() == ORIGINAL
                     if self.spec.resources[0].preexisting else not self.target.exists())
        return Recovery(RecoveryDecision.RETRY_SAFE if untouched else RecoveryDecision.MANUAL)


class UnrecoverableOperation(FileOperation):
    def recover(self, context, phase):
        return Operation.recover(self, context, phase)
