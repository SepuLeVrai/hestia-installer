"""Bounded tar.gz reader. No extract/extractall, links, devices, or inherited modes."""
from __future__ import annotations

import gzip
import hashlib
import os
import stat
import tarfile
import time
import unicodedata
from contextlib import contextmanager
from typing import BinaryIO

from installer.model import ErrorCode, InstallerError, canonical_bytes, require
from installer.transaction import _DIR_FLAGS, _FILE_FLAGS, _check_directory

MAX_FILES = 50000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_CONTENT_BYTES = 512 * 1024 * 1024
MAX_TAR_BYTES = MAX_CONTENT_BYTES + 64 * 1024 * 1024
MAX_PATH_BYTES = 1024
MAX_DEPTH = 32


def safe_parts(path: str) -> tuple[str, ...]:
    require(type(path) is str and bool(path) and path.isprintable()
            and len(path.encode("utf-8")) <= MAX_PATH_BYTES
            and "\\" not in path and ":" not in path, ErrorCode.ARCHIVE_REJECTED)
    parts = tuple(path.split("/"))
    require(len(parts) <= MAX_DEPTH and all(
        part and part not in {".", ".."} and part.casefold() != ".git"
        and not part.endswith((" ", ".")) and len(part.encode("utf-8")) <= 255 for part in parts
    ), ErrorCode.ARCHIVE_REJECTED)
    return parts


@contextmanager
def directory_at(root_fd: int, parts: tuple[str, ...], *, create: bool, claim=None):
    fd = os.dup(root_fd)
    try:
        _check_directory(fd, private=True)
        for part in parts:
            if create:
                try:
                    # Check before creating so implicit parent directories are
                    # included in the same inode budget as explicit members.
                    try:
                        existing = os.open(part, _DIR_FLAGS, dir_fd=fd)
                    except FileNotFoundError:
                        if claim is not None:
                            claim()
                        os.mkdir(part, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    else:
                        os.close(existing)
                except FileExistsError:
                    pass
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            _check_directory(fd, private=True)
        yield fd
    finally:
        os.close(fd)


class _Reader:
    def __init__(self, stream: BinaryIO, *, deadline: float):
        self.stream = stream
        self.total = 0
        self.deadline = deadline

    def read(self, size: int) -> bytes:
        require(0 <= size <= 65536, ErrorCode.SOURCE_LIMIT)
        require(time.monotonic() < self.deadline, ErrorCode.SOURCE_LIMIT)
        data = self.stream.read(size)
        self.total += len(data)
        require(self.total <= MAX_TAR_BYTES, ErrorCode.SOURCE_LIMIT)
        return data

    def exact(self, size: int) -> bytes:
        data = self.read(size)
        require(len(data) == size, ErrorCode.ARCHIVE_REJECTED)
        return data

    def padding(self, size: int) -> None:
        if size % 512:
            self.exact(512 - size % 512)


def _pax(data: bytes) -> dict[str, str]:
    result = {}
    index = 0
    while index < len(data):
        space = data.find(b" ", index, index + 8)
        require(space > index and data[index:space].isdigit(), ErrorCode.ARCHIVE_REJECTED)
        length = int(data[index:space])
        require(length > space - index + 3 and index + length <= len(data), ErrorCode.ARCHIVE_REJECTED)
        record = data[space + 1:index + length]
        require(record.endswith(b"\n") and b"=" in record, ErrorCode.ARCHIVE_REJECTED)
        key, value = record[:-1].decode("utf-8").split("=", 1)
        require(key in {"path", "comment", "mtime", "atime", "ctime"} and key not in result,
                ErrorCode.ARCHIVE_REJECTED)
        result[key] = value
        index += length
    return result


def extract_archive(archive: BinaryIO, root_fd: int, *, repository: str, sha: str,
                    forbidden: tuple[bytes, ...] = ()) -> dict:
    """The caller owns an EMPTY private root. Failure leaves only owned partial data.

    Public TarInfo.frombuf checks headers; PAX/long-name records are parsed with a
    16 KiB bound BEFORE allocation. gzip output is counted, including metadata and
    padding, so a compressed metadata bomb cannot bypass member/content limits.
    """
    require(not os.listdir(root_fd), ErrorCode.ARCHIVE_REJECTED)
    expected_prefix = repository.replace("/", "-") + "-"
    seen = set()
    top = None
    pending = None
    entries = files = content_bytes = allocated = 0

    def claim():
        nonlocal allocated
        allocated += 1
        require(allocated <= MAX_FILES, ErrorCode.SOURCE_LIMIT)

    try:
        with gzip.GzipFile(fileobj=archive, mode="rb") as compressed:
            reader = _Reader(compressed, deadline=time.monotonic() + 300)
            while True:
                header = reader.exact(512)
                if header == b"\0" * 512:
                    require(reader.exact(512) == b"\0" * 512 and pending is None, ErrorCode.ARCHIVE_REJECTED)
                    # Verify gzip CRC/truncation and reject concatenated hidden tar data.
                    while chunk := reader.read(65536):
                        require(not chunk.strip(b"\0"), ErrorCode.ARCHIVE_REJECTED)
                    break
                info = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                entries += 1
                require(entries <= MAX_FILES and 0 <= info.size <= MAX_FILE_BYTES, ErrorCode.SOURCE_LIMIT)
                if info.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME}:
                    require(0 < info.size <= 16384 and pending is None, ErrorCode.ARCHIVE_REJECTED)
                    data = reader.exact(info.size)
                    reader.padding(info.size)
                    if info.type == tarfile.GNUTYPE_LONGNAME:
                        pending = data.rstrip(b"\0").decode("utf-8")
                    else:
                        metadata = _pax(data)
                        if "comment" in metadata:
                            require(metadata["comment"] == sha, ErrorCode.ARCHIVE_REJECTED)
                        if "path" in metadata:
                            require(info.type != tarfile.XGLTYPE, ErrorCode.ARCHIVE_REJECTED)
                            pending = metadata["path"]
                    continue
                require(info.type in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}, ErrorCode.ARCHIVE_REJECTED)
                name = pending if pending is not None else info.name
                pending = None
                if info.isdir() and name.endswith("/"):
                    name = name[:-1]
                parts = safe_parts(name)
                require(not any(secret in name.encode("utf-8") for secret in forbidden), ErrorCode.SECRET_REJECTED)
                if top is None:
                    top = parts[0]
                    short = top[len(expected_prefix):] if top.startswith(expected_prefix) else ""
                    require(7 <= len(short) <= 40 and sha.startswith(short), ErrorCode.ARCHIVE_REJECTED)
                require(parts[0] == top, ErrorCode.ARCHIVE_REJECTED)
                key = unicodedata.normalize("NFC", name).casefold()
                require(key not in seen, ErrorCode.ARCHIVE_REJECTED)
                seen.add(key)
                relative = parts[1:]
                if info.isdir():
                    require(info.size == 0, ErrorCode.ARCHIVE_REJECTED)
                    with directory_at(root_fd, relative, create=True, claim=claim):
                        pass
                    continue
                require(bool(relative), ErrorCode.ARCHIVE_REJECTED)
                require(relative[-1].casefold() != ".gitmodules", ErrorCode.SOURCE_LAYOUT_UNSUPPORTED)
                content_bytes += info.size
                require(content_bytes <= MAX_CONTENT_BYTES, ErrorCode.SOURCE_LIMIT)
                files += 1
                with directory_at(root_fd, relative[:-1], create=True, claim=claim) as parent_fd:
                    claim()
                    fd = os.open(relative[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                                 0o600, dir_fd=parent_fd)
                    try:
                        remaining = info.size
                        tail = b""
                        overlap = max((len(secret) for secret in forbidden), default=1) - 1
                        with os.fdopen(fd, "wb", closefd=False) as handle:
                            while remaining:
                                chunk = reader.exact(min(65536, remaining))
                                if remaining == info.size:
                                    require(not chunk.startswith(b"version https://git-lfs.github.com/spec/v1\n"),
                                            ErrorCode.SOURCE_LAYOUT_UNSUPPORTED)
                                block = tail + chunk
                                require(not any(secret in block for secret in forbidden), ErrorCode.SECRET_REJECTED)
                                tail = block[-overlap:] if overlap else b""
                                handle.write(chunk)
                                remaining -= len(chunk)
                            handle.flush()
                        os.fchmod(fd, 0o700 if info.mode & 0o111 else 0o600)
                        os.fsync(fd)
                        os.fsync(parent_fd)
                    finally:
                        os.close(fd)
                reader.padding(info.size)
            require(files > 0, ErrorCode.ARCHIVE_REJECTED)
        return tree_fingerprint(root_fd)
    except InstallerError:
        raise
    except Exception:
        raise InstallerError(ErrorCode.ARCHIVE_REJECTED) from None


def tree_fingerprint(root_fd: int) -> dict:
    """Read-only proof of actual paths, contents, and executable bits, with bounds."""
    digest = hashlib.sha256()
    count = files = total = 0
    deadline = time.monotonic() + 300
    normalized = set()

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def visit(fd: int, prefix: tuple[str, ...]) -> None:
        nonlocal count, files, total
        _check_directory(fd, private=True)
        for name in sorted(os.listdir(fd)):
            require(time.monotonic() < deadline, ErrorCode.SOURCE_LIMIT)
            parts = prefix + (name,)
            path = "/".join(parts)
            safe_parts(path)
            key = unicodedata.normalize("NFC", path).casefold()
            require(key not in normalized, ErrorCode.SOURCE_DRIFT)
            normalized.add(key)
            count += 1
            require(count <= MAX_FILES, ErrorCode.SOURCE_LIMIT)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(info.st_uid == os.geteuid(), ErrorCode.SOURCE_DRIFT)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                try:
                    digest.update(canonical_bytes([path, "directory"]) + b"\n")
                    visit(child, parts)
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and stat.S_IMODE(info.st_mode) in {0o600, 0o700}
                        and info.st_size <= MAX_FILE_BYTES, ErrorCode.SOURCE_DRIFT)
                file_fd = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
                try:
                    require(identity(os.fstat(file_fd)) == identity(info), ErrorCode.SOURCE_DRIFT)
                    file_hash = hashlib.sha256()
                    with os.fdopen(file_fd, "rb", closefd=False) as handle:
                        size = 0
                        while block := handle.read(65536):
                            size += len(block)
                            require(size <= MAX_FILE_BYTES and time.monotonic() < deadline, ErrorCode.SOURCE_LIMIT)
                            file_hash.update(block)
                    require(size == info.st_size and identity(os.fstat(file_fd)) == identity(info), ErrorCode.SOURCE_DRIFT)
                finally:
                    os.close(file_fd)
                total += size
                files += 1
                require(total <= MAX_CONTENT_BYTES, ErrorCode.SOURCE_LIMIT)
                digest.update(canonical_bytes([path, "file", bool(info.st_mode & 0o111), file_hash.hexdigest()]) + b"\n")
        os.fsync(fd)

    try:
        visit(root_fd, ())
        require(files > 0, ErrorCode.SOURCE_DRIFT)
        return {"sha256": digest.hexdigest(), "files": files, "bytes": total, "entries": count}
    except InstallerError:
        raise
    except Exception:
        raise InstallerError(ErrorCode.SOURCE_DRIFT) from None
