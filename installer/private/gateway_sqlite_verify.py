"""Unprivileged, network-isolated verifier. Only fixed names inside its stage."""
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import struct
import sys
import time
import uuid

MIGRATIONS = (
    '86983620ac6019f553337a243ff8cba5b43760c1be2f4edd23393d4165491cae',
    '15e876d007f792fde2f2698ddfb20965f876fb15ce402b72f5152e5f4db7fc3d',
    '8837431d787d8db5122263c40762c5ff0514ec63726cb33a37c45a998b264e08',
    '68c6be97570a9ee7ab3df8539a02f3daf4b0d5b908ebbad485d2fbc670322dac',
    'cba690272256040f246deac916bb99ec3bf47a8839ffa1deeec92e8bf90452bf',
    'b4bc665a6d6092021d860e170be85e53f187fe929025c7ed8a99dcac22901b94',
)
LIMIT = 512 * 1024 * 1024


def require(ok):
    if not ok: raise ValueError('GATEWAY_SQLITE_REJECTED')


def connect(path, deadline):
    con = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1)
    con.enable_load_extension(False)
    for category, maximum in ((sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024),
            (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024), (sqlite3.SQLITE_LIMIT_COLUMN, 64),
            (sqlite3.SQLITE_LIMIT_ATTACHED, 0), (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)):
        con.setlimit(category, maximum)
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    con.execute('PRAGMA trusted_schema=OFF'); con.execute('PRAGMA query_only=ON')
    return con


def quoted(value): return '"' + value.replace('"', '""') + '"'


def fingerprint(con):
    require(con.execute('PRAGMA quick_check').fetchall() == [('ok',)])
    require(con.execute('PRAGMA foreign_key_check').fetchone() is None)
    require(con.execute('SELECT version, checksum FROM schema_migrations ORDER BY version').fetchall()
            == list(enumerate(MIGRATIONS, 1)))
    values = con.execute('SELECT singleton, installation_uuid, created_at FROM gateway_metadata').fetchall()
    require(len(values) == 1 and values[0][0] == 1 and type(values[0][2]) is int and values[0][2] > 0)
    identity = values[0][1]; parsed = uuid.UUID(identity)
    require(parsed.version == 4 and str(parsed) == identity)
    digest = hashlib.sha256(); rows = 0
    def update(value):
        if value is None: tag, raw = b'n', b''
        elif type(value) is bytes: tag, raw = b'b', value
        elif type(value) is str: tag, raw = b's', value.encode('utf-8')
        elif type(value) is int: tag, raw = b'i', str(value).encode('ascii')
        elif type(value) is float:
            require(math.isfinite(value)); tag, raw = b'f', struct.pack('>d', value)
        else: raise ValueError('GATEWAY_SQLITE_REJECTED')
        digest.update(tag + str(len(raw)).encode('ascii') + b':' + raw)
    schema = con.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
    require(0 < len(schema) <= 128)
    for entry in schema:
        digest.update(b'schema'); [update(v) for v in entry]
        if entry[0] != 'table': continue
        require(not (entry[3] or '').upper().startswith('CREATE VIRTUAL TABLE'))
        name = quoted(entry[1])
        columns = con.execute('PRAGMA table_xinfo(' + name + ')').fetchall()
        require(0 < len(columns) <= 64 and all(row[6] == 0 for row in columns))
        order = ','.join(quoted(row[1]) + ' COLLATE BINARY' for row in columns)
        for row in con.execute('SELECT * FROM ' + name + ' ORDER BY ' + order):
            rows += 1; require(rows <= 2000000)
            digest.update(b'row'); [update(v) for v in row]
    return {'sqlite_schema': 6, 'installation_uuid_sha256': hashlib.sha256(identity.encode()).hexdigest(),
            'logical_sha256': digest.hexdigest(), 'rows': rows}


def main():
    require(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0)
    require(not os.getgroups())
    os.umask(0o077); signal.alarm(55); deadline = time.monotonic() + 50
    require(sys.stdin.buffer.read(3) == b'{}')
    source = Path.cwd() / 'gateway.db'; destination = Path.cwd() / 'restored.sqlite'
    require(source.is_file() and not destination.exists() and 0 < source.stat().st_size <= LIMIT)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600); os.close(fd)
    con = connect(source, deadline)
    try:
        before = fingerprint(con)
        out = sqlite3.connect(destination, timeout=1)
        try:
            out.execute('PRAGMA trusted_schema=OFF')
            def progress(status, remaining, total): require(time.monotonic() <= deadline and total * 65536 <= LIMIT * 16)
            con.backup(out, pages=128, progress=progress, sleep=0.01)
            out.execute('PRAGMA journal_mode=DELETE')
        finally: out.close()
        check = connect(destination, deadline)
        try: require(fingerprint(check) == before)
        finally: check.close()
    finally: con.close()
    require(0 < destination.stat().st_size <= LIMIT)
    with destination.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest(); os.fsync(stream.fileno())
    print(json.dumps({'status': 'PASS', **before, 'bytes': destination.stat().st_size, 'sha256': digest,
                     'worker_uid': os.getuid(), 'worker_gid': os.getgid()}, sort_keys=True))


if __name__ == '__main__':
    try: main()
    except Exception:
        print('{"status":"FAIL","code":"GATEWAY_SQLITE_REJECTED"}'); sys.exit(1)
