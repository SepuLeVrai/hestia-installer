#!/usr/bin/env python3
"""Offline verification of the catalogue against both complete Git tree records.

No checkout content or SQL is executed. These records prove source metadata,
not deployed files, a SQL schema, a backup or a supported transition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer import upgrade_catalog as c
from installer.model import InstallerError, strict_json_loads
from installer.web_releases import LEGACY_COMMIT, STORAGE_COMMIT, get_release

MAX_RECORD = 2 * 1024 * 1024


def require(condition: bool) -> None:
    if not condition:
        raise ValueError('CATALOG_EVIDENCE_REJECTED')


def verify_tree(value: object, commit: str) -> dict[str, tuple[str, str, str]]:
    release = get_release(commit)
    require(type(value) is dict and value.get('truncated') is False
            and value.get('sha') == release.tree and type(value.get('tree')) is list
            and 0 < len(value['tree']) <= 10000)
    entries = {}
    for item in value['tree']:
        require(type(item) is dict)
        name = item.get('path')
        require(type(name) is str and 0 < len(name) <= 4096 and name not in entries)
        path = PurePosixPath(name)
        require(not path.is_absolute() and path.as_posix() == name
                and not set(path.parts).intersection({'..', '.', '.git'})
                and '\\' not in name and not any(ord(ch) < 32 or ord(ch) == 127 for ch in name))
        mode, kind, digest = item.get('mode'), item.get('type'), item.get('sha')
        require(type(mode) is str and type(kind) is str
                and (mode, kind) in {('100644', 'blob'), ('100755', 'blob'), ('040000', 'tree')}
                and type(digest) is str and re.fullmatch('[0-9a-f]{40}', digest) is not None)
        entries[name] = mode, kind, digest
    children = {'': []}
    for name, (_, kind, _) in entries.items():
        if kind == 'tree':
            children[name] = []
    for name in entries:
        parent = name.rpartition('/')[0]
        require(parent in children)
        children[parent].append(name)
    # Rebuild every directory, including the root, using Git's byte ordering.
    for directory, names in children.items():
        records = []
        for name in names:
            mode, kind, digest = entries[name]
            basename = name.rpartition('/')[2].encode('utf-8')
            records.append((basename + (b'/' if kind == 'tree' else b''),
                            mode.lstrip('0').encode('ascii') + b' ' + basename + b'\0' + bytes.fromhex(digest)))
        body = b''.join(record for _, record in sorted(records))
        actual = hashlib.sha1(b'tree ' + str(len(body)).encode('ascii') + b'\0' + body).hexdigest()
        require(actual == (release.tree if not directory else entries[directory][2]))
    require(sum(kind == 'blob' for _, kind, _ in entries.values()) == release.files)
    expected = {'sql/schema.sql': ('100644', 'blob', c.SCHEMA_BLOB),
                'sql/migrations': ('040000', 'tree', c.MIGRATIONS_TREE),
                'includes/version.php': ('100644', 'blob', c.VERSION_BLOB),
                'includes/installation/core.php': ('100644', 'blob', c.CORE_BLOB),
                'includes/installation/fresh.php': ('100644', 'blob', c.FRESH_BLOB)}
    require(all(entries.get(name) == record for name, record in expected.items()))
    require(sum(name.startswith('sql/migrations/') and name.endswith('.sql')
                and entry[1] == 'blob' for name, entry in entries.items()) == c.MIGRATION_FILES)
    return entries


def audit(source: object, target: object) -> dict:
    before = verify_tree(source, LEGACY_COMMIT)
    after = verify_tree(target, STORAGE_COMMIT)
    changes = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old == new or (old or new)[1] == 'tree':
            continue
        changes.append({'path': name, 'before': old, 'after': new})
    require(not any(x['path'].startswith('sql/') for x in changes))
    report = c.assess_transition(repository=c.REPOSITORY, source_commit=LEGACY_COMMIT,
                                target_commit=STORAGE_COMMIT)
    return {'status': 'PASS', 'scope': 'PINNED_SOURCE_METADATA',
            'source_tree': source['sha'], 'target_tree': target['sha'],
            'catalog_sha256': report.sha256, 'catalog': report.report(),
            'changed_files': changes, 'changed_file_count': len(changes)}


def read_record(path: Path) -> object:
    with path.open('rb') as stream:
        raw = stream.read(MAX_RECORD + 1)
    require(len(raw) <= MAX_RECORD)
    try:
        return strict_json_loads(raw)
    except (InstallerError, ValueError, RecursionError):
        raise ValueError('CATALOG_EVIDENCE_REJECTED') from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-tree', type=Path, required=True)
    parser.add_argument('--target-tree', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = audit(read_record(args.source_tree), read_record(args.target_tree))
    except (ValueError, TypeError, KeyError, OSError, UnicodeError):
        print(json.dumps({'status': 'FAIL', 'code': 'CATALOG_EVIDENCE_REJECTED'}))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
