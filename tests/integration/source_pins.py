#!/usr/bin/env python3
"""Read-only source provenance check, independent of fixture-generated pins.

Requires a complete, unmodified Web Git tree. No PHP, SQL or deployed config is
executed. A mismatch is a failure, never a request to regenerate trusted pins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import database_step as d
from installer import finalization as f
from installer import php_transport as p
from installer import upgrade_preflight as u

WEB_COMMIT = '46c03060625d4d53c675474b11aaa33007d9aad7'
WEB_TREE = 'aaac278270e0fd1169396945916dfe997ae078bf'
STORAGE_COMMIT = '2a27c7a1f9fe0a00289eb53278f75d5f230900b7'
STORAGE_TREE = '783be5abdcd5e13addefe96d743eee3a97b7a6de'
STORAGE_RUNTIME = '42c99a13f41b50a5263c69d14557dd088b5787b40ffdc51873b8d61ea1bc8edb'


def object_hash(kind: str, content: bytes) -> bytes:
    return hashlib.sha1(kind.encode() + b' ' + str(len(content)).encode() + b'\0' + content).digest()


def snapshot(root: Path) -> tuple[str, dict[str, bytes]]:
    files = {}

    def tree(directory: Path) -> bytes:
        entries = []
        for path in directory.iterdir():
            if directory == root and path.name == '.git':
                continue
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                mode, digest, sortname = b'40000', tree(path), path.name.encode() + b'/'
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                relative = path.relative_to(root).as_posix()
                if relative in ('includes/db.php', 'install.lock') or info.st_size > p.MAX_FILE:
                    raise ValueError('Source checkout required, no deployed configuration')
                data = path.read_bytes()
                files[relative] = data
                mode = b'100755' if info.st_mode & 0o111 else b'100644'
                digest, sortname = object_hash('blob', data), path.name.encode()
            else:
                raise ValueError('Source links and special files refused')
            entries.append((sortname, mode + b' ' + path.name.encode() + b'\0' + digest))
        return object_hash('tree', b''.join(entry for _, entry in sorted(entries)))

    return tree(root).hex(), files


def digest(files: dict[str, bytes], names: list[str]) -> str:
    result = hashlib.sha256()
    for name in sorted(names):
        data = files[name]
        result.update(p._json([name, len(data), hashlib.sha256(data).hexdigest()]) + b'\n')
    return result.hexdigest()


def inspect(root: Path, commit: str = WEB_COMMIT) -> dict:
    if commit not in (WEB_COMMIT, STORAGE_COMMIT):
        raise ValueError('Unknown source commit')
    expected_tree = STORAGE_TREE if commit == STORAGE_COMMIT else WEB_TREE
    tree, files = snapshot(root)
    checks = [{'name': 'git_tree', 'expected': expected_tree, 'actual': tree, 'ok': tree == expected_tree}]
    vendor = [name for name in files if name.startswith('vendor/')]
    for module in (p, d, f):
        actual = digest(files, [*module.ENGINE_FILES, *vendor])
        checks.append({'name': module.__name__ + '.ENGINE_SHA256', 'expected': module.ENGINE_SHA256,
                       'actual': actual, 'ok': actual == module.ENGINE_SHA256})
    names = [name for name in files if not set(Path(name).parts).intersection(f._SKIPPED)
             and (name.startswith('vendor/') or Path(name).suffix.lower() in f._SUFFIXES
                  or Path(name).name in ('.htaccess', '.user.ini', 'composer.json', 'composer.lock'))]
    actual = digest(files, names)
    for module in (f, u):
        checks.append({'name': module.__name__ + '.RUNTIME_SHA256', 'expected': STORAGE_RUNTIME if commit == STORAGE_COMMIT else module.RUNTIME_SHA256,
                       'actual': actual, 'ok': actual == (STORAGE_RUNTIME if commit == STORAGE_COMMIT else module.RUNTIME_SHA256)})
    after_tree, after_files = snapshot(root)
    stable = tree == after_tree and files == after_files
    return {'suite': 'Exact Web source pins', 'web_commit': commit, 'git_tree': tree,
            'files': len(files), 'source_stable': stable, 'checks': checks,
            'status': 'PASS' if stable and all(check['ok'] for check in checks) else 'FAIL'}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--web', required=True, type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--commit', default=WEB_COMMIT)
    args = parser.parse_args()
    report = inspect(args.web.resolve(), args.commit)
    text = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(text)
    print(text, end='')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
