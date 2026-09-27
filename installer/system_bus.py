"""Explicit Debian system-bus prerequisite, separate from read-only admission.

Only starts the unmodified vendor dbus.service if it is inactive. Never stops,
restarts, unmasks, reloads or repairs an existing broker or its configuration.
"""
import hashlib
import os
from pathlib import Path
import re

from installer import systemd_discovery_transport as t

fs, f, p = t.o.h.fs, t.o.h.f, t.o.h.p
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'FragmentPath',
              'DropInPaths', 'NeedDaemonReload', 'Job', 'MainPID', 'ControlPID')
VENDOR = Path('/usr/lib/systemd/system')
INFO = Path('/var/lib/dpkg/info')


class SystemBusError(RuntimeError): pass


def require(ok, code='SYSTEM_BUS_PROFILE_REJECTED'):
    if not ok: raise SystemBusError(code)


def _read(path, *, mode=0o644, limit=1024 * 1024):
    with fs._directory(path.parent) as fd:
        return f._read(fd, path.name, 0, mode=mode, limit=limit)


def _vendor_file(package, path, *, mode=0o644):
    # dpkg's local checksums detect drift, not package authenticity. The latter
    # comes from the authenticated APT plan (or the trusted existing Debian OS).
    rows = _read(INFO / (package + '.md5sums')).decode('ascii').splitlines()
    names = {path.as_posix().lstrip('/')}
    if path.as_posix().startswith('/usr/lib/'):
        names.add(path.as_posix()[5:])  # Bookworm's merged-/usr record: lib/...
    matches = []
    for row in rows:
        fields = row.split('  ', 1)
        if len(fields) == 2 and fields[1] in names: matches.append(fields[0])
    require(len(matches) == 1 and re.fullmatch('[a-f0-9]{32}', matches[0]))
    raw = _read(path, mode=mode, limit=32 * 1024 * 1024)
    require(hashlib.md5(raw, usedforsecurity=False).hexdigest() == matches[0])
    return f._sha(raw)


def _state(unit):
    require(unit in ('dbus.service', 'dbus.socket'))
    properties = PROPERTIES if unit.endswith('.service') else PROPERTIES[:-2]
    raw = t._capture(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'show',
        '--property=' + ','.join(properties), '--', unit], t._Budget())
    require(len(raw) <= 16384)
    rows = [line.split('=', 1) for line in raw.decode().splitlines()]
    require(len(rows) == len(properties) and all(len(row) == 2 for row in rows))
    value = dict(rows)
    require(set(value) == set(properties) and value['Id'] == unit
        and value['LoadState'] == 'loaded' and value['DropInPaths'] == ''
        and value['NeedDaemonReload'] == 'no' and value['Job'] == '')
    require(value['FragmentPath'] in (str(VENDOR / unit), '/lib/systemd/system/' + unit)
        and Path(value['FragmentPath']).resolve(strict=True) == VENDOR / unit)
    permitted = (('active', 'running'), ('inactive', 'dead')) if unit.endswith('.service') else (
        ('active', 'listening'), ('active', 'running'), ('inactive', 'dead'))
    require((value['ActiveState'], value['SubState']) in permitted)
    if unit.endswith('.service'):
        require(value['ControlPID'] == '0' and (value['MainPID'] == '0' if value['ActiveState'] == 'inactive'
            else re.fullmatch('[1-9][0-9]{0,9}', value['MainPID']) is not None))
    return value


def _profile():
    require(os.geteuid() == 0, 'SYSTEM_BUS_ROOT_REQUIRED')
    system = t.o.h.read_os_release()
    require(system.get('ID') == 'debian' and system.get('VERSION_ID') in ('12', '13'))
    t.o._provenance()
    require(t.o._identity_file('/proc/1/comm', 32) == 'systemd')
    for tool in ('/usr/bin/systemctl', t.BUSCTL):
        p._safe_path(Path(tool), directory=False, system=True)
    result = {
        'service': _vendor_file('dbus', VENDOR / 'dbus.service'),
        'socket': _vendor_file('dbus-system-bus-common', VENDOR / 'dbus.socket'),
        'daemon': _vendor_file('dbus-daemon', Path('/usr/bin/dbus-daemon'), mode=0o755),
    }
    for unit in ('dbus.service', 'dbus.socket'):
        _state(unit)
    return result


def _probe():
    budget = t._Budget(); local = t._local()
    def query(operation, owner=None):
        return t._capture(t._argv(operation, owner), budget)
    bus_id = t._reply(query('GetId'), 's')
    require(type(bus_id) is str and re.fullmatch('[a-f0-9]{32}', bus_id))
    broker = t._reply(query('GetBrokerPID'), 'u')
    require(type(broker) is int and broker == local['broker_pid'])
    owner = t._owner(t._reply(query('GetNameOwner'), 's'))
    pid = t._reply(query('GetConnectionUnixProcessID', owner), 'u')
    uid = t._reply(query('GetConnectionUnixUser', owner), 'u')
    require(type(pid) is int and pid == 1 and type(uid) is int and uid == 0)
    require(t._reply(query('GetNameOwner'), 's') == owner and t._reply(query('GetId'), 's') == bus_id)
    require(t._local() == local)
    return local, bus_id, owner


def _start():
    # No shell, unit input, automatic retry or interactive authorization.
    t._capture(['/usr/bin/systemctl', '--no-pager', '--no-ask-password',
        '--job-mode=fail', 'start', '--', 'dbus.service'], t._Budget())


def _ready(profile, *, started, identity=None):
    require(_profile() == profile, 'SYSTEM_BUS_CHANGED')
    require(_state('dbus.service')['ActiveState'] == 'active', 'SYSTEM_BUS_NOT_READY')
    current = _probe()
    require(identity is None or current == identity, 'SYSTEM_BUS_CHANGED')
    return {'state': 'SYSTEM_BUS_READY', 'system_bus_ready': True,
        'system_bus_started': started, 'profile_sha256': f._sha(p._json(profile)),
        'application_installed': False, 'system_wiring_verified': False}


def observe():
    """Read-only existing/upgrade check; never repairs or starts a stopped bus."""
    try: return _ready(_profile(), started=False)
    except SystemBusError: raise
    except Exception: raise SystemBusError('SYSTEM_BUS_UNAVAILABLE') from None


def ensure(*, confirmed):
    """Provisioning step; an already active broker retains its exact identity."""
    require(confirmed is True, 'SYSTEM_BUS_CONSENT_REQUIRED')
    try:
        profile = _profile()
        running = _state('dbus.service')['ActiveState'] == 'active'
        identity = _probe() if running else None
        if not running: _start()
        return _ready(profile, started=not running, identity=identity)
    except SystemBusError: raise
    except Exception: raise SystemBusError('SYSTEM_BUS_UNAVAILABLE') from None
