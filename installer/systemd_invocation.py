"""Private PIDFD-to-invocation observations; no named-unit property reads.

PID hints do not authenticate an earlier process census. A result neither
enumerates all writers nor admits any unit to drainage or activation.
"""
from dataclasses import dataclass, field, asdict
import os
import re
import select
import time

from installer import systemd_discovery_transport as t

d = t.d
MAX_BINDINGS = 128
UNIT_INTERFACE = t.MANAGER + '.Unit'
require = t.require


@dataclass(frozen=True)
class InvocationHint:
    object_path: str = field(repr=False)
    pid: int = field(repr=False)


def _hints(values):
    require(type(values) is tuple and 1 <= len(values) <= MAX_BINDINGS, 'INVOCATION_HINTS_REJECTED')
    paths, pids = set(), set()
    for value in values:
        require(type(value) is InvocationHint and type(value.pid) is int
                and 2 <= value.pid <= 2**31-1, 'INVOCATION_HINTS_REJECTED')
        d._unit_object(value.object_path)
        require(value.object_path not in paths and value.pid not in pids, 'INVOCATION_HINTS_REJECTED')
        paths.add(value.object_path); pids.add(value.pid)
    return tuple(sorted(values, key=lambda x: x.object_path))


def _identifier(value):
    require(type(value) is list and len(value) == 16
            and all(type(b) is int and 0 <= b <= 255 for b in value)
            and any(value), 'INVOCATION_ID_REJECTED')
    return bytes(value).hex()


def _path(identifier):
    require(type(identifier) is str and re.fullmatch('[0-9a-f]{32}', identifier) is not None
            and identifier != '0'*32, 'INVOCATION_ID_REJECTED')
    label = ('_'+format(ord(identifier[0]), '02x')+identifier[1:]
             if identifier[0].isdigit() else identifier)
    return d.UNIT_PREFIX + label


def _mapping(raw):
    require(type(raw) is bytes and 0 < len(raw) <= t.MAX_REPLY, 'INVOCATION_REPLY_REJECTED')
    value = d.l.strict_json_loads(raw)
    require(type(value) is dict and set(value) == {'type', 'data'} and value['type'] == 'osay'
            and type(value['data']) is list and len(value['data']) == 3, 'INVOCATION_REPLY_REJECTED')
    path, name, identifier = value['data']
    d._unit_object(path); d._name(name, loaded=True)
    return path, name, _identifier(identifier)


def _variant(raw, signature):
    value = t._reply(raw, 'v')
    require(type(value) is dict and set(value) == {'type', 'data'} and value['type'] == signature,
            'INVOCATION_PROPERTY_REJECTED')
    return value['data']


def _argv(operation, owner, *, fd=None, identifier=None):
    # Reuse the fixed official client flags, but never a named unit destination.
    prefix = t._argv('ListUnits', owner)[:-3]
    if operation == 'GetUnitByPIDFD':
        require(type(fd) is int and fd >= 3 and identifier is None, 'INVOCATION_FD_REJECTED')
        return prefix + [t.MANAGER_PATH, t.INTERFACE, operation, 'h', str(fd)]
    require(fd is None, 'INVOCATION_FD_REJECTED')
    path = _path(identifier)
    if operation == 'GetUnitByInvocationID':
        return prefix + [t.MANAGER_PATH, t.INTERFACE, operation, 'ay', '16',
                         *(str(b) for b in bytes.fromhex(identifier))]
    require(operation in ('Id', 'InvocationID'), 'INVOCATION_OPERATION_NOT_ALLOWED')
    return prefix + [path, t.PROPERTIES, 'Get', 'ss', UNIT_INTERFACE, operation]


def _alive(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
    require(not poll.poll(0), 'INVOCATION_PROCESS_EXITED')


@dataclass(frozen=True)
class InvocationBinding:
    object_path: str = field(repr=False)
    primary_name: str = field(repr=False)
    pid_hint: int = field(repr=False)
    invocation_id: str = field(repr=False)
    invocation_path: str = field(repr=False)


@dataclass(frozen=True)
class InvocationSample:
    _index: d.DiscoveryIndex = field(repr=False)
    _canonical: bytes = field(repr=False)

    def index(self): return self._index
    def private_manifest(self): return d.l.strict_json_loads(self._canonical)
    def report(self):
        data = self.private_manifest(); result = self._index.report()
        result.update(state='SYSTEM_MANAGER_INVOCATIONS_OBSERVED', origin='LOCAL_SYSTEM_BUS_PIDFD',
            manifest_sha256=d.l._sha(self._canonical), bus_calls=data['transport']['calls'],
            response_bytes=data['transport']['bytes'], invocation_bindings=len(data['bindings']),
            system_manager_lists_observed=True, selected_invocations_observed=True,
            process_census_authenticated=False, effective_identities_observed=False)
        return result


class SystemdInvocationTransport(t.SystemdDiscoveryTransport):
    def __repr__(self): return '<SystemdInvocationTransport private read-only invocation bindings>'

    def _invocation_query(self, operation, budget, owner, *, fd=None, identifier=None):
        argv = _argv(operation, owner, fd=fd, identifier=identifier)
        budget.remaining()
        raw = t._capture(argv, budget, pass_fds=(fd,) if fd is not None else ())
        budget.remaining()
        return raw

    def _binding(self, hint, name, fd, owner, budget):
        _alive(fd)
        path, primary, identifier = _mapping(self._invocation_query('GetUnitByPIDFD', budget, owner, fd=fd))
        _alive(fd)
        require((path, primary) == (hint.object_path, name), 'INVOCATION_BINDING_MISMATCH')
        invocation_path = t._reply(self._invocation_query('GetUnitByInvocationID', budget, owner,
                                                       identifier=identifier), 'o')
        require(type(invocation_path) is str and invocation_path == _path(identifier), 'INVOCATION_PATH_REJECTED')
        unit_id = _variant(self._invocation_query('Id', budget, owner, identifier=identifier), 's')
        invocation_id = _identifier(_variant(self._invocation_query('InvocationID', budget, owner,
                                                                   identifier=identifier), 'ay'))
        _alive(fd)
        require(type(unit_id) is str and unit_id == name and invocation_id == identifier,
                'INVOCATION_PROPERTY_MISMATCH')
        return InvocationBinding(path, primary, hint.pid, identifier, invocation_path)

    def collect(self, hints):
        owned = []
        try:
            hints = _hints(hints)  # reject malformed input before host IO
            budget = t._Budget(invocation_pairs=len(hints)); started = int(time.time())
            before, context = self._round(budget)
            # Validate the first population before it can authorize a selection.
            self._discovery.inspect(d.DiscoveryScan(self._target, started, started, 0, before, before), now=started)
            require(re.match(r'^257(?:[.\- ]|$)', before.provenance.manager_version) is not None,
                    'INVOCATION_MANAGER_PROFILE_UNSUPPORTED')
            units = {u.object_path: u.primary_name for u in before.loaded_units.rows}
            require(all(h.object_path in units for h in hints), 'INVOCATION_SELECTION_NOT_LISTED')
            for hint in hints:
                budget.remaining()
                fd = os.pidfd_open(hint.pid, 0); owned.append(fd)
                require(fd >= 3 and not os.get_inheritable(fd), 'INVOCATION_FD_REJECTED')
                _alive(fd)
            owner = before.provenance.bus_owner
            first = tuple(self._binding(h, units[h.object_path], fd, owner, budget) for h, fd in zip(hints, owned))
            second = tuple(self._binding(h, units[h.object_path], fd, owner, budget) for h, fd in zip(hints, owned))
            require(first == second, 'INVOCATION_CHANGED')
            after, after_context = self._round(budget)
            require(context == after_context, 'DISCOVERY_LOCAL_PROVENANCE_CHANGED')
            for fd in owned: _alive(fd)
            budget.remaining(); finished = int(time.time())
            elapsed = int((time.monotonic() - budget.started)*1000)
            index = self._discovery.inspect(d.DiscoveryScan(self._target, started, finished, elapsed, before, after), now=finished)
            require(budget.calls == 24+8*len(hints), 'DISCOVERY_CALL_SET_INCOMPLETE')
            data = {'version': 1, 'discovery': index.private_manifest(),
                'bindings': [asdict(b) for b in first],
                'transport': {'client': t.BUSCTL, 'policy': 'PIDFD_INVOCATION_ID_V1', 'context': context,
                              'calls': budget.calls, 'bytes': budget.bytes},
                'limitations': ['PID_HINTS_NOT_AUTHENTICATED', 'UNSELECTED_UNITS_UNKNOWN',
                                'EFFECTIVE_IDENTITIES_UNKNOWN', 'RELATIONS_UNKNOWN', 'NOT_ATOMIC']}
            result = InvocationSample(index, d._json(data))
            for fd in owned: _alive(fd)
            budget.remaining()
            return result
        except t.SystemdTransportError: raise
        except Exception: raise t.SystemdTransportError('INVOCATION_TRANSPORT_UNAVAILABLE') from None
        finally:
            # Attempt every close even when another close fails. No operation
            # targets the observed process itself.
            close_failed = False
            for fd in owned:
                try: os.close(fd)
                except OSError: close_failed = True
            if close_failed: raise t.SystemdTransportError('INVOCATION_FD_CLOSE_FAILED') from None
