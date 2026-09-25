"""Standalone dedicated-UID session collector; template installed by root.

Never imports application code, reads session contents, touches retained dates,
or removes anything while guarded PHP/CLI writers or maintenance hold the gate.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from contextlib import contextmanager

PROFILE_HEX = '__SESSION_CLEANER_PROFILE_HEX__'
LIFETIME = 43200
MAX_ENTRIES = 10000
MAX_SECONDS = 2
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class CleanerError(RuntimeError):
    pass


def require(value):
    if not value: raise CleanerError('SESSION_CLEANER_REJECTED')


def no_acl(fd):
    require(not {'system.posix_acl_access', 'system.posix_acl_default'}.intersection(os.listxattr(fd)))


@contextmanager
def directory(path):
    require(path.is_absolute() and str(path) == os.path.normpath(path) and '..' not in path.parts)
    fd = os.open('/', FLAGS | os.O_DIRECTORY)
    try:
        for name in ('', *path.parts[1:]):
            if name:
                child = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=fd); os.close(fd); fd = child
            info = os.fstat(fd)
            require(info.st_uid == 0 and stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o7022)
            no_acl(fd)
        yield fd
    finally: os.close(fd)


def root_file(fd, name, gid, *, limit=16384):
    handle = os.open(name, FLAGS, dir_fd=fd)
    try:
        before = os.fstat(handle)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_gid == gid
                and stat.S_IMODE(before.st_mode) == 0o640 and before.st_nlink == 1 and before.st_size <= limit)
        no_acl(handle); data = os.read(handle, limit + 1); after = os.fstat(handle)
        require(len(data) == before.st_size and identity(before) == identity(after))
        return data
    finally: os.close(handle)


def identity(info):
    return tuple(getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
                                                'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def gated(fd):
    try: os.stat('maintenance.attempt', dir_fd=fd, follow_symlinks=False); return True
    except FileNotFoundError: return False


def session(info, uid, gid):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)


def collect(fd, uid, gid, gate):
    """Caller owns exclusive admission. Two passes validate before any unlink."""
    deadline = time.monotonic() + MAX_SECONDS
    cutoff = time.time_ns() - LIFETIME * 1000000000
    entries = []
    with os.scandir(fd) as scanner:
        for entry in scanner:
            require(len(entries) < MAX_ENTRIES and time.monotonic() <= deadline)
            require(re.fullmatch(r'sess_[A-Za-z0-9,-]{1,256}', entry.name) is not None)
            info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False); session(info, uid, gid)
            handle = os.open(entry.name, FLAGS, dir_fd=fd)
            try:
                no_acl(handle); require(identity(os.fstat(handle)) == identity(info))
            finally: os.close(handle)
            entries.append((entry.name, info))
    result = {'state': 'SESSION_CLEANER_DONE', 'scanned': len(entries), 'removed': 0, 'locked': 0}
    try:
        for name, before in entries:
            require(time.monotonic() <= deadline)
            if gated(gate): result['state'] = 'SESSION_CLEANER_MAINTENANCE'; break
            if before.st_mtime_ns >= cutoff: continue
            handle = os.open(name, FLAGS, dir_fd=fd)
            try:
                try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: result['locked'] += 1; continue
                no_acl(handle); require(identity(os.fstat(handle)) == identity(before))
                require(identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) == identity(before))
                if gated(gate): result['state'] = 'SESSION_CLEANER_MAINTENANCE'; break
                os.unlink(name, dir_fd=fd); result['removed'] += 1
            finally: os.close(handle)
    finally:
        if result['removed']: os.fsync(fd)
    return result


def clean(profile):
    keys = {'root', 'uid', 'gid', 'profile_sha256', 'guard_sha256'}
    require(type(profile) is dict and set(profile) in (keys, keys | {'maintenance'}))
    require(type(profile['root']) is str and re.fullmatch(r'/var/lib/[A-Za-z0-9_/-]+', profile['root'])
            and len(profile['root']) <= 75 and '..' not in Path(profile['root']).parts)
    uid, gid = profile['uid'], profile['gid']
    require(type(uid) is int and type(gid) is int and uid > 0 and gid > 0
            and os.geteuid() == uid and os.getegid() == gid and set(os.getgroups()) <= {gid})
    require(all(type(profile[key]) is str and re.fullmatch(r'[a-f0-9]{64}', profile[key])
                for key in ('profile_sha256', 'guard_sha256')))
    root = Path(profile['root'])
    gate_path = profile.get('maintenance', str(root / 'maintenance'))
    require(type(gate_path) is str and re.fullmatch(r'/var/lib/[A-Za-z0-9_/-]+/maintenance', gate_path)
            and len(gate_path) <= 180 and '..' not in Path(gate_path).parts)
    with directory(Path(gate_path)) as gate:
        info = os.fstat(gate); require(info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o750)
        for name, key in (('profile.json', 'profile_sha256'), ('request_guard.php', 'guard_sha256')):
            require(hashlib.sha256(root_file(gate, name, gid)).hexdigest() == profile[key])
        if gated(gate): return {'state': 'SESSION_CLEANER_MAINTENANCE', 'scanned': 0, 'removed': 0, 'locked': 0}
        require(root_file(gate, 'activity.lock', gid) == b'')
        lock = os.open('activity.lock', FLAGS, dir_fd=gate)
        try:
            require(identity(os.fstat(lock)) == identity(os.stat('activity.lock', dir_fd=gate, follow_symlinks=False)))
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return {'state': 'SESSION_CLEANER_BUSY', 'scanned': 0, 'removed': 0, 'locked': 0}
            if gated(gate): return {'state': 'SESSION_CLEANER_MAINTENANCE', 'scanned': 0, 'removed': 0, 'locked': 0}
            with directory(root / 'data') as parent:
                fd = os.open('sessions', FLAGS | os.O_DIRECTORY, dir_fd=parent)
                try:
                    info = os.fstat(fd); no_acl(fd)
                    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, 0o700))
                    return collect(fd, uid, gid, gate)
                finally: os.close(fd)
        finally: os.close(lock)


def main():
    try:
        result = clean(json.loads(bytes.fromhex(PROFILE_HEX)))
    except Exception:
        print('{"state":"SESSION_CLEANER_REJECTED"}'); return 1
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
