from __future__ import annotations

import errno
import fcntl
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from installer.model import (
    MAX_DOCUMENT_BYTES, ErrorCode, InstallerError, absolute_path, canonical_bytes,
    require, strict_json_loads, validate_document,
)

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _check_directory(fd: int, *, private: bool) -> None:
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode), ErrorCode.UNSAFE_STATE_PATH)
    require(info.st_uid in {0, os.geteuid()}, ErrorCode.UNSAFE_STATE_PATH)
    if private:
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                ErrorCode.UNSAFE_STATE_PATH)
    else:
        # Sticky /tmp is an acceptable ancestor, not a private state directory.
        writable = info.st_mode & 0o022
        require(not writable or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX),
                ErrorCode.UNSAFE_STATE_PATH)


def _check_file(fd: int, *, allow_unlinked: bool = False) -> None:
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and (info.st_nlink == 1 or (allow_unlinked and info.st_nlink == 0)),
            ErrorCode.UNSAFE_STATE_PATH)


@contextmanager
def _private_directory(path: Path, *, create: bool) -> Iterator[int]:
    """Walk from / with pinned dirfds. Never resolve/follow a symbolic link."""
    absolute_path(str(path))
    fd = os.open("/", _DIR_FLAGS)
    try:
        _check_directory(fd, private=False)
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            created = False
            try:
                next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    created = True
                    os.fsync(fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            if created:
                os.fchmod(fd, 0o700)
            _check_directory(fd, private=index == len(parts) - 1)
        yield fd
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) and not create:
            raise
        raise InstallerError(ErrorCode.UNSAFE_STATE_PATH) from None
    finally:
        os.close(fd)


class StateJournal:
    """Private, bounded, non-secret journal with atomic replacement and CAS.

    A read creates neither a directory nor a lock. Writers hold a persistent
    lock inode for the ENTIRE execution, not just for individual JSON writes.
    Only local filesystems supporting flock/fsync/atomic rename are supported.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        absolute_path(str(self.path))
        require(not self.path.name.startswith("."), ErrorCode.UNSAFE_STATE_PATH)

    @contextmanager
    def locked(self, *, create: bool = False) -> Iterator["_LockedJournal"]:
        with _private_directory(self.path.parent, create=create) as directory_fd:
            lock_fd = os.open(".transaction.lock", os.O_RDWR | os.O_CREAT | _FILE_FLAGS,
                              0o600, dir_fd=directory_fd)
            try:
                _check_file(lock_fd)
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in {errno.EACCES, errno.EAGAIN}:
                        raise InstallerError(ErrorCode.BUSY) from None
                    raise
                yield _LockedJournal(self, directory_fd)
            finally:
                os.close(lock_fd)  # Also releases the lock on every exception/crash.

    def read(self) -> dict | None:
        try:
            with _private_directory(self.path.parent, create=False) as directory_fd:
                return self._read_at(directory_fd)
        except FileNotFoundError:
            return None

    def write(self, payload: dict, *, expected_revision: int | None = None) -> None:
        # Validation precedes even the creation of the journal's private directory.
        validate_document(payload)
        with self.locked(create=True) as locked:
            locked.write(payload, expected_revision=expected_revision)

    def _read_at(self, directory_fd: int) -> dict | None:
        try:
            fd = os.open(self.path.name, os.O_RDONLY | _FILE_FLAGS, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            # Atomic replacement can unlink the old inode AFTER this reader
            # opened it. The pinned old snapshot is still valid; hardlinks >1
            # remain forbidden. Writers and locks never permit unlinked inodes.
            _check_file(fd, allow_unlinked=True)
            require(os.fstat(fd).st_size <= MAX_DOCUMENT_BYTES, ErrorCode.INVALID_STATE)
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(MAX_DOCUMENT_BYTES + 1)
            require(0 < len(data) <= MAX_DOCUMENT_BYTES, ErrorCode.INVALID_STATE)
            document = strict_json_loads(data)
            validate_document(document)
            return document
        except (ValueError, TypeError, KeyError, RecursionError):
            raise InstallerError(ErrorCode.INVALID_STATE) from None
        finally:
            os.close(fd)


class _LockedJournal:
    def __init__(self, journal: StateJournal, directory_fd: int) -> None:
        self._journal = journal
        self._directory_fd = directory_fd

    def read(self) -> dict | None:
        return self._journal._read_at(self._directory_fd)

    def write(self, payload: dict, *, expected_revision: int | None) -> None:
        validate_document(payload)
        current = self.read()
        if expected_revision is None:
            require(current is None and payload["revision"] == 0, ErrorCode.PLAN_EXISTS)
        else:
            require(type(expected_revision) is int and current is not None
                    and current["revision"] == expected_revision, ErrorCode.BUSY)
            require(payload["revision"] == expected_revision + 1
                    and payload["plan_sha256"] == current["plan_sha256"], ErrorCode.INVALID_STATE)
        data = canonical_bytes(payload) + b"\n"
        require(len(data) <= MAX_DOCUMENT_BYTES, ErrorCode.INVALID_STATE)
        name = ".state-" + secrets.token_hex(16) + ".tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                     0o600, dir_fd=self._directory_fd)
        try:
            os.fchmod(fd, 0o600)
            _check_file(fd)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(fd)
            # Validate the old inode again. rename never follows the destination.
            current_again = self.read()
            require((current_again is None and current is None)
                    or (current_again is not None and current_again == current), ErrorCode.BUSY)
            os.replace(name, self._journal.path.name,
                       src_dir_fd=self._directory_fd, dst_dir_fd=self._directory_fd)
            os.fsync(self._directory_fd)
        finally:
            os.close(fd)
            try:
                os.unlink(name, dir_fd=self._directory_fd)
            except FileNotFoundError:
                pass
