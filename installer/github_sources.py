"""Owned source acquisition adapter and reconstruction of approved source plans."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from installer.github_client import GitHubAccess, REPOSITORIES, SECRET_NAME, MAX_ARCHIVE_BYTES
from installer.model import (
    ErrorCode, InstallerError, Receipt, ResourceSpec, SourceSpec, StepSpec,
    canonical_bytes, exact_keys, integer, require, strict_json_loads,
)
from installer.operations import Operation, OperationContext, OperationRegistry, PreflightOperation, Recovery, RecoveryDecision
from installer.source_archive import MAX_CONTENT_BYTES, MAX_FILES, extract_archive, tree_fingerprint
from installer.transaction import _DIR_FLAGS, _FILE_FLAGS, _check_file, _private_directory


def _read_json(fd: int, name: str) -> dict | None:
    try:
        file_fd = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
    except FileNotFoundError:
        return None
    try:
        _check_file(file_fd)
        require(0 < os.fstat(file_fd).st_size <= 16384, ErrorCode.SOURCE_DRIFT)
        with os.fdopen(file_fd, "rb", closefd=False) as handle:
            data = handle.read(16385)
        require(0 < len(data) <= 16384, ErrorCode.SOURCE_DRIFT)
        result = strict_json_loads(data)
        require(type(result) is dict, ErrorCode.SOURCE_DRIFT)
        return result
    finally:
        os.close(file_fd)


def _write_json(fd: int, name: str, data: dict) -> None:
    require(_read_json(fd, name) is None, ErrorCode.SOURCE_DRIFT)
    temporary = name + ".part"
    file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
    try:
        with os.fdopen(file_fd, "wb", closefd=False) as handle:
            handle.write(canonical_bytes(data) + b"\n")
            handle.flush()
            os.fsync(file_fd)
        os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(file_fd)


def _archive_hash(fd: int) -> str:
    file_fd = os.open("archive.tar.gz", os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
    try:
        _check_file(file_fd)
        require(0 < os.fstat(file_fd).st_size <= MAX_ARCHIVE_BYTES, ErrorCode.SOURCE_LIMIT)
        with os.fdopen(file_fd, "rb", closefd=False) as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    finally:
        os.close(file_fd)


class AcquireOperation(Operation):
    def __init__(self, state_dir: Path, module: str, source: SourceSpec, access: GitHubAccess) -> None:
        require(module in REPOSITORIES and source.repository == REPOSITORIES[module], ErrorCode.INCOMPATIBLE_STATE)
        self.path = state_dir / "sources" / (module + "-" + source.commit_sha)
        self.source = source
        self.access = access
        super().__init__(StepSpec(
            name="github-" + module, operation="github.acquire", module=module, boundary="github-" + module,
            action="Acquérir les sources " + module + " au commit approuvé, sans les exécuter",
            resources=(ResourceSpec("sources", "directory", str(self.path), rollback="delete-created"),),
            rollback_supported=True, requires_secrets=(SECRET_NAME,), source=source,
            warnings=("Sources uniquement : aucun déploiement ni compilation. Les liens et sous-modules ne sont pas matérialisés.",),
        ))

    def _owner(self, context: OperationContext) -> dict:
        return {"installation_id": context.installation_id, "module": self.spec.module,
                "source": self.source.as_dict()}

    def _check_owner(self, fd: int, context: OperationContext) -> None:
        require(_read_json(fd, "owner.json") == self._owner(context), ErrorCode.SOURCE_DRIFT)

    def _proof(self, fd: int, context: OperationContext) -> dict | None:
        self._check_owner(fd, context)
        manifest = _read_json(fd, "manifest.json")
        if manifest is None:
            require(_read_json(fd, "committed.json") is None, ErrorCode.SOURCE_DRIFT)
            return None
        exact_keys(manifest, {"owner", "archive_sha256", "tree"})
        require(manifest["owner"] == self._owner(context), ErrorCode.SOURCE_DRIFT)
        exact_keys(manifest["tree"], {"sha256", "files", "bytes", "entries"})
        integer(manifest["tree"]["files"], 1, MAX_FILES)
        integer(manifest["tree"]["entries"], 1, MAX_FILES)
        integer(manifest["tree"]["bytes"], 0, MAX_CONTENT_BYTES)
        require(set(os.listdir(fd)) <= {"owner.json", "archive.tar.gz", "tree", "manifest.json",
                                       "committed.json", "committed.json.part"}, ErrorCode.SOURCE_DRIFT)
        require(_archive_hash(fd) == manifest["archive_sha256"], ErrorCode.SOURCE_DRIFT)
        tree_fd = os.open("tree", _DIR_FLAGS, dir_fd=fd)
        try:
            require(tree_fingerprint(tree_fd) == manifest["tree"], ErrorCode.SOURCE_DRIFT)
        finally:
            os.close(tree_fd)
        committed = _read_json(fd, "committed.json")
        if committed is not None:
            require(committed == manifest, ErrorCode.SOURCE_DRIFT)
        return manifest

    def _receipt(self, manifest: dict) -> Receipt:
        return Receipt(created_resources=("sources",), commit_shas=((self.spec.module, self.source.commit_sha),),
                       hashes_non_secret=(("archive", manifest["archive_sha256"]), ("tree", manifest["tree"]["sha256"])))

    def prepare(self, context: OperationContext) -> None:
        try:
            with _private_directory(self.path, create=False) as fd:
                self._check_owner(fd, context)  # Existing foreign resources are NEVER overwritten.
                self._proof(fd, context)
        except FileNotFoundError:
            pass

    def _clear_partial(self, fd: int, context: OperationContext) -> None:
        self._check_owner(fd, context)
        require(_read_json(fd, "manifest.json") is None and _read_json(fd, "committed.json") is None,
                ErrorCode.SOURCE_DRIFT)
        for name in os.listdir(fd):
            if name == "owner.json":
                continue
            require(name in {"archive.part", "archive.tar.gz", "tree", "manifest.json.part"}, ErrorCode.SOURCE_DRIFT)
            if name == "tree":
                # Inspect the root inode before Python's fd-safe recursive deletion.
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                os.close(child)
                shutil.rmtree(name, dir_fd=fd)
            else:
                file_fd = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
                try:
                    _check_file(file_fd)
                finally:
                    os.close(file_fd)
                os.unlink(name, dir_fd=fd)
        os.fsync(fd)

    def apply(self, context: OperationContext) -> Receipt:
        token = self.access.token()
        require(token == context.require_secret(SECRET_NAME), ErrorCode.SECRET_REQUIRED)
        with _private_directory(self.path.parent, create=True) as parent_fd:
            created = False
            try:
                os.mkdir(self.path.name, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
                created = True
            except FileExistsError:
                pass
            with _private_directory(self.path, create=False) as fd:
                if created:
                    _write_json(fd, "owner.json", self._owner(context))
                else:
                    proof = self._proof(fd, context)
                    if proof is not None:
                        return self._receipt(proof)
                    self._clear_partial(fd, context)
                try:
                    file_fd = os.open("archive.part", os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                                      0o600, dir_fd=fd)
                    try:
                        with os.fdopen(file_fd, "wb", closefd=False) as out:
                            archive_sha = self.access.client.download(self.source, token, out)
                            out.flush()
                            os.fsync(file_fd)
                    finally:
                        os.close(file_fd)
                    os.rename("archive.part", "archive.tar.gz", src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                    require(_archive_hash(fd) == archive_sha, ErrorCode.SOURCE_DRIFT)
                    os.mkdir("tree", 0o700, dir_fd=fd)
                    tree_fd = os.open("tree", _DIR_FLAGS, dir_fd=fd)
                    archive_fd = os.open("archive.tar.gz", os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
                    try:
                        with os.fdopen(archive_fd, "rb", closefd=False) as archive:
                            tree = extract_archive(archive, tree_fd, repository=self.source.repository,
                                                   sha=self.source.commit_sha, forbidden=(token.encode("ascii"),))
                    finally:
                        os.close(archive_fd)
                        os.close(tree_fd)
                    manifest = {"owner": self._owner(context), "archive_sha256": archive_sha, "tree": tree}
                    context.secrets.reject_in(manifest)
                    _write_json(fd, "manifest.json", manifest)
                    return self._receipt(manifest)
                except Exception:
                    # Leave an ownership proof, not a corrupt archive or echoed secret.
                    if _read_json(fd, "manifest.json") is None:
                        self._clear_partial(fd, context)
                    raise

    def validate(self, context: OperationContext) -> bool:
        with _private_directory(self.path, create=False) as fd:
            proof = self._proof(fd, context)
            require(proof is not None, ErrorCode.SOURCE_DRIFT)
            return self._receipt(proof).as_dict() == context.evidence

    def commit(self, context: OperationContext) -> None:
        with _private_directory(self.path, create=False) as fd:
            proof = self._proof(fd, context)
            require(proof is not None, ErrorCode.SOURCE_DRIFT)
            if _read_json(fd, "committed.json") is None:
                # An interrupted atomic marker write may leave its private temp file.
                try:
                    file_fd = os.open("committed.json.part", os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    pass
                else:
                    try:
                        _check_file(file_fd)
                    finally:
                        os.close(file_fd)
                    os.unlink("committed.json.part", dir_fd=fd)
                _write_json(fd, "committed.json", proof)

    def recover(self, context: OperationContext, phase: str) -> Recovery:
        try:
            with _private_directory(self.path, create=False) as fd:
                proof = self._proof(fd, context)
                if phase == "rollback" or proof is None:
                    return Recovery(RecoveryDecision.RETRY_SAFE)
                decision = RecoveryDecision.COMMITTED if _read_json(fd, "committed.json") else RecoveryDecision.APPLIED
                return Recovery(decision, self._receipt(proof))
        except FileNotFoundError:
            return Recovery(RecoveryDecision.ROLLED_BACK if phase == "rollback" else RecoveryDecision.RETRY_SAFE)
        except Exception:
            return Recovery(RecoveryDecision.MANUAL)

    def rollback(self, context: OperationContext) -> bool:
        try:
            with _private_directory(self.path, create=False) as fd:
                self._proof(fd, context)
                inode = os.fstat(fd).st_ino
            with _private_directory(self.path.parent, create=False) as parent_fd:
                require(os.stat(self.path.name, dir_fd=parent_fd, follow_symlinks=False).st_ino == inode,
                        ErrorCode.SOURCE_DRIFT)
                shutil.rmtree(self.path.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            return True
        except FileNotFoundError:
            return True


class GitHubAcquisition:
    """Server-side allowlist, no executable callback or path supplied by the UI."""

    def __init__(self, engine, access: GitHubAccess | None = None) -> None:
        self.engine = engine
        self.access = access if access is not None else GitHubAccess(engine.secrets)
        require(self.access.vault is engine.secrets)
        self.restore_registry()

    def restore_registry(self) -> None:
        document = self.engine.report()
        if document is None:
            return
        specs = document["plan"]["steps"]
        if len(specs) == 1 and specs[0].get("operation") == "preflight.run":
            operation = PreflightOperation(legacy=specs[0]["adapter_version"] == 1)
            require(operation.plan().as_dict() == specs[0], ErrorCode.INCOMPATIBLE_STATE)
            self.engine.registry = OperationRegistry((operation,))
            return
        if not any(s.get("operation") == "github.acquire" for s in specs):
            return
        operations = []
        for spec in document["plan"]["steps"]:
            require(spec.get("operation") == "github.acquire" and "source" in spec, ErrorCode.INCOMPATIBLE_STATE)
            operation = AcquireOperation(self.engine.journal.path.parent, spec["module"], SourceSpec(**spec["source"]), self.access)
            require(operation.plan().as_dict() == spec, ErrorCode.INCOMPATIBLE_STATE)
            operations.append(operation)
        self.engine.registry = OperationRegistry(tuple(operations))
        self.engine.registry.validate_document(document)

    def verify_completed(self) -> None:
        document = self.engine.report()
        if document is None:
            return
        for spec, record in zip(document["plan"]["steps"], document["steps"]):
            if spec["operation"] == "github.acquire" and record["state"] == "DONE":
                operation = self.engine.registry.get(spec)
                context = OperationContext(document["installation_id"], spec, record["evidence"], self.engine.secrets)
                try:
                    require(operation.validate(context), ErrorCode.SOURCE_DRIFT)
                except Exception:
                    raise InstallerError(ErrorCode.SOURCE_DRIFT) from None

    def plan(self, payload: dict) -> dict:
        exact_keys(payload, {"modules", "refs", "mode"})
        require(type(payload["mode"]) is str and payload["mode"] in {"fresh", "upgrade"})
        existing = self.engine.report()
        if existing is not None:
            # Refresh must not resolve moving refs or require a credential again.
            require(type(payload["modules"]) is list and all(type(m) is str for m in payload["modules"]))
            require(sorted(payload["modules"]) == existing["modules"] and payload["mode"] == existing["mode"], ErrorCode.PLAN_EXISTS)
            require(type(payload["refs"]) is dict and set(payload["refs"]) <= set(existing["modules"]))
            require(all(s.get("operation") == "github.acquire" for s in existing["plan"]["steps"]), ErrorCode.PLAN_EXISTS)
            for spec in existing["plan"]["steps"]:
                require(payload["refs"].get(spec["module"], spec["source"]["ref"]) == spec["source"]["ref"], ErrorCode.PLAN_EXISTS)
            self.restore_registry()
            return existing
        selected = self.access.select(payload["modules"], payload["refs"])
        registry = OperationRegistry(tuple(AcquireOperation(self.engine.journal.path.parent, module, source, self.access)
                                           for module, source in selected.items()))
        previous = self.engine.registry
        try:
            self.engine.registry = registry
            return self.engine.plan(mode=payload["mode"])
        except Exception:
            self.engine.registry = previous
            raise
