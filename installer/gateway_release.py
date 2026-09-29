"""Closed binary catalogue. Moving refs and source archives are never releases."""
from copy import deepcopy
import hashlib
from pathlib import PurePosixPath
import re
import stat
import zipfile

from installer.model import ErrorCode, InstallerError, require

_RELEASE = {
    'repository': 'SepuLeVrai/hestia-mobile-gateway', 'repository_id': 1369648122,
    'commit': 'e2c09f53593bf316906ccc4387f185e73e7f85a8',
    'version': '0.12.2-installer.rc1', 'sqlite_schema': 6, 'architecture': 'linux-amd64',
    'run_id': 36499757403, 'artifact_id': 11005276084,
    'artifact_name': 'gateway-project-push-linux-amd64', 'artifact_bytes': 11774392,
    'artifact_sha256': 'bf1a2ede352a7706135e38db9b8cfc221d63d94552871d6cabfc521231c5941e',
    'expires_at': '2026-10-12T23:57:18Z',
    'member': 'build/release/HESTIA-Gateway-0.12.2-installer.rc1-linux-amd64.zip',
    'package_bytes': 10832222,
    'package_sha256': 'f3138b5bd4fc5c85e4dcf9e4480d8f521f72c02db34219cb9053c38a1eae8a0e',
    'binary_sha256': 'bf6d3020f4b068046ab35eaef8adaef18888e27caa360c7ebf4152f6a64b5684',
    'files': 267, 'unpacked_bytes': 19484759,
}


def release(): return deepcopy(_RELEASE)
def sha(data): return hashlib.sha256(data).hexdigest()


def digest(stream, limit):
    stream.seek(0); count = 0; result = hashlib.sha256()
    while True:
        chunk = stream.read(min(65536, limit - count + 1))
        if not chunk: break
        count += len(chunk); require(count <= limit, ErrorCode.SOURCE_LIMIT); result.update(chunk)
    return count, result.hexdigest()


def members(archive, *, count, size):
    entries = archive.infolist()
    require(0 < len(entries) <= count and sum(e.file_size for e in entries) <= size, ErrorCode.SOURCE_LIMIT)
    names = set()
    for entry in entries:
        name = entry.filename; path = PurePosixPath(name)
        require(name and str(path) == name and not path.is_absolute() and '..' not in path.parts
                and not any(ord(c) < 32 or ord(c) > 126 for c in name) and '\\' not in name
                and not entry.is_dir() and not entry.flag_bits & 1
                and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                and not (entry.external_attr >> 16) & 0o7000 and name not in names,
                ErrorCode.SOURCE_LAYOUT_UNSUPPORTED)
        names.add(name)
    require(not any('/'.join(n.split('/')[:i]) in names for n in names
                    for i in range(1, len(n.split('/')))), ErrorCode.SOURCE_LAYOUT_UNSUPPORTED)
    return entries


def verify_package(stream, selected):
    """Bounded reads only; even the verified binary is never executed here."""
    require(digest(stream, selected['package_bytes']) ==
            (selected['package_bytes'], selected['package_sha256']), ErrorCode.SOURCE_DRIFT)
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = members(archive, count=selected['files'], size=selected['unpacked_bytes'])
            require(len(entries) == selected['files'] and sum(e.file_size for e in entries) == selected['unpacked_bytes'],
                    ErrorCode.SOURCE_DRIFT)
            require(archive.getinfo('SHA256SUMS').file_size <= 262144, ErrorCode.SOURCE_LIMIT)
            checksums = {}
            for line in archive.read('SHA256SUMS').decode('ascii').splitlines():
                match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
                require(match is not None and match[2] not in checksums, ErrorCode.SOURCE_DRIFT)
                checksums[match[2]] = match[1]
            require(set(checksums) == {e.filename for e in entries} - {'SHA256SUMS'}, ErrorCode.SOURCE_DRIFT)
            for entry in entries:
                if entry.filename == 'SHA256SUMS': continue
                with archive.open(entry) as source:
                    require(digest(source, entry.file_size) == (entry.file_size, checksums[entry.filename]),
                            ErrorCode.SOURCE_DRIFT)
            require(archive.read('VERSION') == (selected['version'] + '\n').encode('ascii')
                    and checksums['bin/hestia-mobile-gateway'] == selected['binary_sha256']
                    and archive.getinfo('bin/hestia-mobile-gateway').external_attr >> 16 & 0o777 == 0o755,
                    ErrorCode.SOURCE_DRIFT)
    except InstallerError: raise
    except Exception: raise InstallerError(ErrorCode.SOURCE_LAYOUT_UNSUPPORTED) from None
    return {key: selected[key] for key in ('commit', 'version', 'sqlite_schema', 'architecture',
                                          'package_sha256', 'binary_sha256')}


def copy_package(artifact, output, selected):
    require(digest(artifact, selected['artifact_bytes']) ==
            (selected['artifact_bytes'], selected['artifact_sha256']), ErrorCode.SOURCE_DRIFT)
    try:
        with zipfile.ZipFile(artifact) as archive:
            members(archive, count=1024, size=128 * 1024 * 1024)
            entry = archive.getinfo(selected['member'])
            require(entry.file_size == selected['package_bytes'], ErrorCode.SOURCE_DRIFT)
            with archive.open(entry) as source:
                while chunk := source.read(65536): output.write(chunk)
    except InstallerError: raise
    except Exception: raise InstallerError(ErrorCode.SOURCE_LAYOUT_UNSUPPORTED) from None
