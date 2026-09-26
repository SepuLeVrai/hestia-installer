"""Private bounded procfs observations of selected, live thread-group leaders.

Not a census, an atomic snapshot, or proof about threads/descendants. Numeric
identities are relative to the verified observer context, not guessed from names.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
import os
import re
import stat

from installer import systemd_invocation as v

require = v.require
MAX_FILE = 65536
MAX_MOUNTS = 1048576
MAX_BYTES = 32 * 1048576
MAX_READS = 32768
NAMESPACES = ('pid', 'mnt', 'user', 'cgroup', 'time')


class ProcBudget:
    def __init__(self, transport):
        self.transport = transport
        self.bytes = self.reads = self.observations = 0

    def check(self):
        self.transport.remaining()
        require(self.bytes <= MAX_BYTES and self.reads < MAX_READS, 'PROCESS_IO_LIMIT')


@contextmanager
def _directory(name, parent=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try: yield fd
    finally: os.close(fd)


def _read(parent, name, budget, maximum=MAX_FILE):
    budget.check(); budget.reads += 1
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'PROCESS_FILE_REJECTED')
        raw = bytearray()
        while True:
            budget.transport.remaining()
            piece = os.read(fd, min(4096, maximum + 1 - len(raw)))
            budget.bytes += len(piece); raw.extend(piece)
            require(len(raw) <= maximum and budget.bytes <= MAX_BYTES, 'PROCESS_IO_LIMIT')
            if not piece: break
        require(raw and raw.endswith(b'\n') and b'\0' not in raw, 'PROCESS_FILE_REJECTED')
        return bytes(raw).decode('utf-8', 'strict')
    finally: os.close(fd)


def _numbers(raw, count=None, maximum=2**32-1):
    require(type(raw) is str and re.fullmatch(r'[ \t0-9]*', raw) is not None, 'PROCESS_NUMBERS_REJECTED')
    values = raw.split()
    require(len(values) <= 64 and (count is None or len(values) == count), 'PROCESS_NUMBERS_REJECTED')
    require(all(re.fullmatch(r'[0-9]{1,20}', x) and int(x) <= maximum for x in values), 'PROCESS_NUMBERS_REJECTED')
    return tuple(int(x) for x in values)


def _fields(raw, selected):
    result = {}
    for line in raw.splitlines():
        name, sep, value = line.partition(':')
        if name in selected:
            require(sep and name not in result, 'PROCESS_FIELDS_REJECTED')
            result[name] = value
    require(set(result) == set(selected), 'PROCESS_FIELDS_REJECTED')
    return result


def _credentials(raw, pid):
    f = _fields(raw, ('Pid', 'Tgid', 'Uid', 'Gid', 'Groups', 'Threads', 'NSpid', 'NStgid'))
    for key in ('Pid', 'Tgid', 'NSpid', 'NStgid'):
        require(_numbers(f[key], 1, 2**31-1) == (pid,), 'PROCESS_LEADER_REJECTED')
    threads = _numbers(f['Threads'], 1, 32768)[0]
    require(threads >= 1, 'PROCESS_LEADER_REJECTED')
    groups = _numbers(f['Groups'])
    require(len(groups) == len(set(groups)), 'PROCESS_GROUPS_REJECTED')
    return _numbers(f['Uid'], 4), _numbers(f['Gid'], 4), tuple(sorted(groups)), threads


def _start(raw, pid):
    require(raw.startswith(str(pid)+' (') and ') ' in raw, 'PROCESS_STAT_REJECTED')
    tail = raw.rsplit(') ', 1)[1].split()
    require(len(tail) >= 20 and tail[0] in ('R', 'S', 'D', 'T', 't', 'I', 'W', 'P'), 'PROCESS_STAT_REJECTED')
    start = _numbers(tail[19], 1, 2**63-1)[0]
    require(start > 0, 'PROCESS_STAT_REJECTED')
    return start


def _cgroup(raw):
    require(raw.count('\n') == 1 and raw.startswith('0::/'), 'PROCESS_CGROUP_REJECTED')
    path = raw[3:-1]
    # A path in the observer's cgroup namespace. Never infer a unit from it.
    require(len(path.encode()) <= 2048 and not any(ord(c) < 32 or ord(c) == 127 for c in path), 'PROCESS_CGROUP_REJECTED')
    require(path == '/' or all(p and p not in ('.', '..') for p in path[1:].split('/')), 'PROCESS_CGROUP_REJECTED')
    return path


def _namespaces(parent, budget):
    values = []
    with _directory('ns', parent) as ns:
        for kind in NAMESPACES:
            budget.check(); budget.reads += 1
            value = os.readlink(kind, dir_fd=ns)
            require(len(value) <= 64 and re.fullmatch(kind+r':\[[1-9][0-9]*\]', value), 'PROCESS_NAMESPACE_REJECTED')
            values.append(value)
    return tuple(values)


def _mounts(raw, pid, observer):
    lines = raw.splitlines()
    require(0 < len(lines) <= 8192, 'PROCESS_MOUNT_REJECTED')
    roots = []
    protected = {str(pid), str(observer), '1', 'self', 'thread-self'}
    for line in lines:
        parts = line.split(); require(' - ' in line and len(parts) >= 10, 'PROCESS_MOUNT_REJECTED')
        split = parts.index('-')
        require(split >= 6 and len(parts) == split+4, 'PROCESS_MOUNT_REJECTED')
        mount = parts[4]
        if mount == '/proc':
            roots.append(parts)
            require(parts[3] == '/' and parts[split+1] == 'proc', 'PROCESS_MOUNT_REJECTED')
            options = (parts[5]+','+parts[split+3]).split(',')
            require(not any((o.startswith('hidepid=') and o != 'hidepid=0') or o.startswith('subset=') for o in options), 'PROCESS_MOUNT_REJECTED')
        elif mount.startswith('/proc/'):
            require(mount[6:].split('/')[0] not in protected, 'PROCESS_MOUNT_REJECTED')
    require(len(roots) == 1, 'PROCESS_MOUNT_REJECTED')
    return v.d.l._sha(raw.encode())


def _context(proc, pid, budget):
    require(os.geteuid() == 0, 'PROCESS_OBSERVER_REJECTED')
    observer = os.getpid()
    with _directory(str(observer), proc) as own, _directory('1', proc) as init:
        ours, initial = _namespaces(own, budget), _namespaces(init, budget)
        require(ours == initial, 'PROCESS_OBSERVER_NAMESPACE_REJECTED')
        mounts = _mounts(_read(own, 'mountinfo', budget, MAX_MOUNTS), pid, observer)
    return ours, mounts


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int = field(repr=False)
    start_ticks: int = field(repr=False)
    uids: tuple[int, ...] = field(repr=False)
    gids: tuple[int, ...] = field(repr=False)
    groups: tuple[int, ...] = field(repr=False)
    threads: int = field(repr=False)
    namespaces: tuple[str, ...] = field(repr=False)
    cgroup: str = field(repr=False)
    context_sha256: str = field(repr=False)

    def digest(self): return v.d.l._sha(v.d._json(asdict(self)))


def _snapshot(process, pid, context, budget):
    start = _start(_read(process, 'stat', budget), pid)
    uids, gids, groups, threads = _credentials(_read(process, 'status', budget), pid)
    namespaces = _namespaces(process, budget)
    require(namespaces[0] == context[0][0] and namespaces[2] == context[0][2], 'PROCESS_TARGET_NAMESPACE_REJECTED')
    cgroup = _cgroup(_read(process, 'cgroup', budget))
    require(_start(_read(process, 'stat', budget), pid) == start, 'PROCESS_IDENTITY_CHANGED')
    return ProcessIdentity(pid, start, uids, gids, groups, threads, namespaces, cgroup,
                           v.d.l._sha(v.d._json(context)))


def observe(pid, fd, budget):
    """Only called with the collector's own live PIDFD; caller retains ownership."""
    require(type(pid) is int and 2 <= pid <= 2**31-1 and type(fd) is int and fd >= 3,
            'PROCESS_SELECTION_REJECTED')
    budget.check(); v._alive(fd)
    with _directory('/proc') as proc:
        context = _context(proc, pid, budget)
        with _directory(str(os.getpid()), proc) as own, _directory('fdinfo', own) as info:
            values = _fields(_read(info, str(fd), budget), ('Pid', 'NSpid'))
            require(all(_numbers(values[k], 1, 2**31-1) == (pid,) for k in values), 'PROCESS_PIDFD_MISMATCH')
        with _directory(str(pid), proc) as process:
            first = _snapshot(process, pid, context, budget)
            second = _snapshot(process, pid, context, budget)
        require(first == second and context == _context(proc, pid, budget), 'PROCESS_IDENTITY_CHANGED')
    v._alive(fd); budget.transport.remaining(); budget.observations += 1
    return first
