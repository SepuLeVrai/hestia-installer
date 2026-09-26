"""Bounded, read-only system bus transport for three manager populations.

No unit loading, property sweep, profile enrollment, relevance fact collection
or mutation. Descriptions are discarded before normalized evidence is hashed.
"""
from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import re
import select
import stat
import subprocess
import time

from installer import systemd_discovery as d, systemd_observations as o

BUSCTL = '/usr/bin/busctl'
SOCKET = '/run/dbus/system_bus_socket'
BUS = 'org.freedesktop.DBus'
BUS_PATH = '/org/freedesktop/DBus'
MANAGER = 'org.freedesktop.systemd1'
MANAGER_PATH = '/org/freedesktop/systemd1'
INTERFACE = MANAGER + '.Manager'
PROPERTIES = 'org.freedesktop.DBus.Properties'
SIGNATURES = {'ListUnits': 'a(ssssssouso)', 'ListUnitFiles': 'a(ss)', 'ListJobs': 'a(usssoo)'}
MAX_REPLY = 2 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_CALLS = 24
CALL_SECONDS = 5
COLLECTION_SECONDS = 60


class SystemdTransportError(RuntimeError):
    """Fixed diagnostics; no bus error text or raw response reaches a caller."""


def require(ok, code='DISCOVERY_TRANSPORT_REJECTED'):
    if not ok: raise SystemdTransportError(code)


def _owner(value):
    d._text(value, 255)
    require(re.fullmatch(r':[0-9]+(?:\.[0-9]+)+', value) is not None, 'DISCOVERY_BUS_OWNER_REJECTED')
    return value


def _argv(operation, owner=None):
    prefix = [BUSCTL, '--system', '--address=unix:path='+SOCKET, '--no-pager', '--json=short',
        '--auto-start=no', '--allow-interactive-authorization=no', '--expect-reply=yes', '--timeout=5s', 'call']
    if operation == 'GetId': return prefix + [BUS, BUS_PATH, BUS, operation]
    if operation == 'GetNameOwner': return prefix + [BUS, BUS_PATH, BUS, operation, 's', MANAGER]
    if operation == 'GetBrokerPID': return prefix + [BUS, BUS_PATH, BUS, 'GetConnectionUnixProcessID', 's', BUS]
    _owner(owner)
    if operation in ('GetConnectionUnixProcessID', 'GetConnectionUnixUser'):
        return prefix + [BUS, BUS_PATH, BUS, operation, 's', owner]
    if operation in SIGNATURES: return prefix + [owner, MANAGER_PATH, INTERFACE, operation]
    require(operation in ('Version', 'UnitPath'), 'DISCOVERY_OPERATION_NOT_ALLOWED')
    # Use call Properties.Get, not get-property: auto-start and interactive
    # authorization flags are explicitly applied by busctl's call implementation.
    return prefix + [owner, MANAGER_PATH, PROPERTIES, 'Get', 'ss', INTERFACE, operation]


class _Budget:
    def __init__(self, *, invocation_pairs=0):
        require(type(invocation_pairs) is int and 0 <= invocation_pairs <= 128, 'DISCOVERY_TRANSPORT_LIMIT')
        self.maximum_calls = MAX_CALLS + 8 * invocation_pairs
        self.started = time.monotonic()
        self.deadline = self.started + COLLECTION_SECONDS
        self.bytes = 0
        self.calls = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'DISCOVERY_COLLECTION_TIMEOUT')
        return remaining


def _capture(argv, budget, *, pass_fds=()):
    budget.remaining()
    require(budget.calls < budget.maximum_calls and budget.bytes < MAX_TOTAL, 'DISCOVERY_TRANSPORT_LIMIT')
    require(type(pass_fds) is tuple and len(pass_fds) <= 1
            and all(type(fd) is int and fd >= 3 for fd in pass_fds), 'DISCOVERY_FD_REJECTED')
    budget.calls += 1
    maximum = min(MAX_REPLY, MAX_TOTAL - budget.bytes)
    deadline = min(budget.deadline, time.monotonic() + CALL_SECONDS)
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd='/', close_fds=True, pass_fds=pass_fds,
        env={'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C', 'LC_ALL': 'C', 'SYSTEMD_PAGER': ''})
    raw = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'DISCOVERY_CALL_TIMEOUT')
            ready, _, _ = select.select([child.stdout], [], [], remaining)
            require(ready, 'DISCOVERY_CALL_TIMEOUT')
            block = os.read(child.stdout.fileno(), min(65536, maximum + 1 - len(raw)))
            if not block: break
            raw.extend(block); budget.bytes += len(block)
            require(len(raw) <= maximum, 'DISCOVERY_TRANSPORT_LIMIT')
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'DISCOVERY_CALL_TIMEOUT')
        require(child.wait(timeout=remaining) == 0, 'DISCOVERY_BUS_UNREADABLE')
        budget.remaining()
        return bytes(raw)
    finally:
        # Only our fixed read client is killed; no observed PID/unit is signalled.
        if child.poll() is None: child.kill(); child.wait(timeout=1)
        child.stdout.close()


def _reply(raw, signature):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_REPLY, 'DISCOVERY_REPLY_REJECTED')
    value = d.l.strict_json_loads(raw)
    require(type(value) is dict and set(value) == {'type', 'data'}
            and type(value['type']) is str and value['type'] == signature
            and type(value['data']) is list and len(value['data']) == 1, 'DISCOVERY_REPLY_REJECTED')
    return value['data'][0]


def _population(operation, raw):
    rows = _reply(raw, SIGNATURES[operation])
    maximum, width = {'ListUnits': (d.MAX_UNITS, 10), 'ListUnitFiles': (d.MAX_FILES, 2), 'ListJobs': (d.MAX_JOBS, 6)}[operation]
    require(type(rows) is list and len(rows) <= maximum, 'DISCOVERY_ROW_LIMIT')
    result = []
    for row in rows:
        require(type(row) is list and len(row) == width, 'DISCOVERY_ROW_REJECTED')
        if operation == 'ListUnits':
            require(type(row[1]) is str, 'DISCOVERY_ROW_REJECTED')
            # Never retain or hash the description. No free text is returned.
            result.append(d.LoadedUnit(row[0], row[6], row[2], row[3], row[4], row[5], None,
                d.UnitJobReference(row[7], row[8], row[9])))
        elif operation == 'ListUnitFiles': result.append(d.InstalledUnitFile(*row))
        else: result.append(d.ManagerJob(*row))
    # Canonical digest ignores transport order and discarded descriptions.
    normalized = sorted((asdict(row) for row in result), key=d._json)
    return d.Enumeration('observed', d.l._sha(d._json(normalized)), tuple(result))


def _stamp(info):
    return tuple(getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def _local():
    provenance = o._provenance()  # root, host/boot, same PID and mount namespaces as PID 1
    require(o._identity_file('/proc/1/comm', 32) == 'systemd', 'DISCOVERY_SYSTEM_MANAGER_REQUIRED')
    o.h.p._safe_path(Path(BUSCTL), directory=False, system=True)
    binary = Path(BUSCTL).lstat()
    require(bool(binary.st_mode & 0o111), 'DISCOVERY_CLIENT_REJECTED')
    for name in ('/run', '/run/dbus'):
        info = Path(name).lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'DISCOVERY_BUS_PATH_REJECTED')
    info = Path(SOCKET).lstat()
    # The root-owned socket may accept connections from non-root clients; its
    # protected parent, not its write bit, prevents pathname replacement.
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1, 'DISCOVERY_BUS_PATH_REJECTED')
    # This first supported transport profile requires Debian's already running
    # dbus-daemon in its native cgroup. Never contact a socket-only broker.
    group = Path('/sys/fs/cgroup/system.slice/dbus.service/cgroup.procs')
    o.h.p._safe_path(group, directory=False)
    pid = o._identity_file(str(group), 64)
    require(re.fullmatch('[1-9][0-9]{0,9}', pid) is not None and 1 < int(pid) <= 2**31-1,
            'DISCOVERY_RUNNING_BROKER_REQUIRED')
    require(os.readlink('/proc/'+pid+'/exe') == '/usr/bin/dbus-daemon'
            and os.readlink('/proc/'+pid+'/ns/pid') == provenance['namespaces']['pid'],
            'DISCOVERY_RUNNING_BROKER_REQUIRED')
    return {'identity': provenance, 'socket': _stamp(info), 'client': _stamp(binary), 'broker_pid': int(pid)}


@dataclass(frozen=True)
class DiscoverySample:
    _scan: d.DiscoveryScan = field(repr=False)
    _index: d.DiscoveryIndex = field(repr=False)
    _canonical: bytes = field(repr=False)

    def scan(self): return self._scan
    def index(self): return self._index
    def private_manifest(self): return d.l.strict_json_loads(self._canonical)

    def report(self):
        data = self.private_manifest(); result = self._index.report()
        result.update(state='SYSTEM_MANAGER_LISTS_OBSERVED', origin='LOCAL_SYSTEM_BUS',
            manifest_sha256=d.l._sha(self._canonical), bus_calls=data['transport']['calls'],
            response_bytes=data['transport']['bytes'], system_manager_lists_observed=True)
        return result


class SystemdDiscoveryTransport:
    def __init__(self, target, storage):
        try:
            self._discovery = d.SystemdDiscovery(target, storage)
            self._target = target
        except Exception: raise SystemdTransportError('DISCOVERY_TRANSPORT_TARGET_REJECTED') from None

    def __repr__(self): return '<SystemdDiscoveryTransport private read-only lists>'

    def _query(self, operation, budget, owner=None):
        budget.remaining()
        value = _capture(_argv(operation, owner), budget)
        budget.remaining()
        return value

    def _round(self, budget):
        budget.remaining(); local = _local(); budget.remaining()
        bus_id = _reply(self._query('GetId', budget), 's')
        require(type(bus_id) is str and re.fullmatch('[a-f0-9]{32}', bus_id) is not None, 'DISCOVERY_BUS_ID_REJECTED')
        broker_pid = _reply(self._query('GetBrokerPID', budget), 'u')
        require(type(broker_pid) is int and broker_pid == local['broker_pid'], 'DISCOVERY_BROKER_CHANGED')
        owner = _owner(_reply(self._query('GetNameOwner', budget), 's'))
        pid = _reply(self._query('GetConnectionUnixProcessID', budget, owner), 'u')
        uid = _reply(self._query('GetConnectionUnixUser', budget, owner), 'u')
        require(type(pid) is int and pid == 1 and type(uid) is int and uid == 0, 'DISCOVERY_MANAGER_CREDENTIALS_REJECTED')
        properties = {}
        for key, signature in (('Version', 's'), ('UnitPath', 'as')):
            value = _reply(self._query(key, budget, owner), 'v')
            require(type(value) is dict and set(value) == {'type', 'data'} and value['type'] == signature,
                    'DISCOVERY_PROPERTY_REJECTED')
            properties[key] = value['data']
        require(type(properties['UnitPath']) is list and len(properties['UnitPath']) <= 64, 'DISCOVERY_PROPERTY_REJECTED')
        p = local['identity']; namespaces = p['namespaces']
        provenance = d.DiscoveryProvenance(p['host_id'], p['boot_id'], 'system', owner,
            namespaces['pid'], namespaces['pid'], namespaces['mnt'], namespaces['mnt'],
            properties['Version'], tuple(SIGNATURES), tuple(properties['UnitPath']))
        populations = [_population(key, self._query(key, budget, owner)) for key in SIGNATURES]
        require(_reply(self._query('GetNameOwner', budget), 's') == owner
                and _reply(self._query('GetId', budget), 's') == bus_id, 'DISCOVERY_MANAGER_CHANGED')
        budget.remaining(); require(_local() == local, 'DISCOVERY_LOCAL_PROVENANCE_CHANGED'); budget.remaining()
        return d.DiscoveryRound(provenance, *populations), {'bus_id': bus_id, 'local': local}

    def collect(self, *, previous=None):
        try:
            budget = _Budget(); started = int(time.time())
            before, context = self._round(budget)
            after, after_context = self._round(budget)
            require(context == after_context, 'DISCOVERY_LOCAL_PROVENANCE_CHANGED')
            budget.remaining(); finished = int(time.time())
            elapsed = int((time.monotonic() - budget.started) * 1000)
            scan = d.DiscoveryScan(self._target, started, finished, elapsed, before, after)
            prior = None
            if previous is not None:
                require(type(previous) is DiscoverySample and type(previous._canonical) is bytes
                        and len(previous._canonical) <= d.MAX_BYTES, 'DISCOVERY_SAMPLE_REJECTED')
                old = previous.private_manifest()
                require(old['transport']['context'] == d.l.strict_json_loads(d._json(context)), 'DISCOVERY_PREVIOUS_CONTEXT_CHANGED')
                require(type(previous._index) is d.DiscoveryIndex and old['discovery'] == previous._index.private_manifest(),
                        'DISCOVERY_SAMPLE_REJECTED')
                prior = previous._index
            index = self._discovery.inspect(scan, now=finished, previous=prior)
            require(budget.calls == MAX_CALLS, 'DISCOVERY_CALL_SET_INCOMPLETE')
            data = {'version': 1, 'discovery': index.private_manifest(), 'transport': {'client': BUSCTL,
                'policy': 'FIXED_READ_ONLY_CALLS_V1', 'context': context, 'calls': budget.calls, 'bytes': budget.bytes}}
            result = DiscoverySample(scan, index, d._json(data)); budget.remaining()
            return result
        except SystemdTransportError: raise
        except Exception: raise SystemdTransportError('DISCOVERY_TRANSPORT_UNAVAILABLE') from None
