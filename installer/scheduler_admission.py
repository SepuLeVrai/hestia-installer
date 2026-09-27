"""Read-only refusal of classic scheduler footprints in the provisioned profile.

Absence is re-observed, never a lock against administrative host changes or a
claim that arbitrary CLI/native systemd producers have been controlled.
"""
from contextlib import contextmanager
import os
from pathlib import Path

from installer import provisioned_admission as configuration
from installer import systemd_discovery_transport as t

ABSENT_PATHS = tuple(Path(p) for p in (
    '/usr/sbin/cron', '/usr/sbin/crond', '/usr/sbin/anacron', '/usr/sbin/atd',
    '/usr/sbin/fcron', '/usr/sbin/bcron', '/usr/sbin/bcron-sched',
    '/usr/lib/systemd/system-generators/systemd-crontab-generator',
    '/etc/crontab', '/etc/anacrontab', '/var/spool/cron', '/var/spool/at',
))
STEMS = frozenset(('cron', 'crond', 'cronie', 'anacron', 'atd', 'fcron', 'bcron'))
REJECTED = 'PROVISIONED_CLASSIC_SCHEDULER_REJECTED'
UNAVAILABLE = 'PROVISIONED_SCHEDULER_OBSERVATION_UNAVAILABLE'


class SchedulerAdmissionError(RuntimeError): pass


def require(ok, code=UNAVAILABLE):
    if not ok: raise SchedulerAdmissionError(code)


def _scheduler_name(name):
    t.d._name(name)
    stem = name.rsplit('.', 1)[0].split('@', 1)[0]
    return stem in STEMS or any(stem.startswith(s + '-') for s in STEMS)


def _paths():
    # Inspect only protected directory entries. Never read a crontab, queue,
    # executable, or command line, and never interpret its contents as code.
    for path in ABSENT_PATHS:
        try: configuration._absent(path)
        except configuration.AdmissionError: raise SchedulerAdmissionError(REJECTED) from None


def _observe():
    """Reuse the qualified no-autostart bus client; no GetUnit/LoadUnit/show."""
    _paths()
    budget = t._Budget()
    local = t._local()
    def query(operation, owner=None):
        return t._capture(t._argv(operation, owner), budget)
    bus_id = t._reply(query('GetId'), 's')
    require(type(bus_id) is str and len(bus_id) == 32 and all(c in '0123456789abcdef' for c in bus_id))
    broker_pid = t._reply(query('GetBrokerPID'), 'u')
    require(type(broker_pid) is int and broker_pid == local['broker_pid'])
    owner = t._owner(t._reply(query('GetNameOwner'), 's'))
    manager_pid = t._reply(query('GetConnectionUnixProcessID', owner), 'u')
    manager_uid = t._reply(query('GetConnectionUnixUser', owner), 'u')
    require(type(manager_pid) is int and manager_pid == 1)
    require(type(manager_uid) is int and manager_uid == 0)
    # Two populations are needed: an installed inactive service can have no
    # live invocation, and a loaded/transient service can have no file left.
    for operation in ('ListUnits', 'ListUnitFiles'):
        rows = t._population(operation, query(operation, owner)).rows
        for row in rows:
            if operation == 'ListUnitFiles': t.d._path(row.path)
            name = row.primary_name if operation == 'ListUnits' else Path(row.path).name
            require(not _scheduler_name(name), REJECTED)
    require(t._reply(query('GetNameOwner'), 's') == owner)
    require(t._reply(query('GetId'), 's') == bus_id)
    require(t._local() == local)
    _paths()
    budget.remaining()
    return local, bus_id, owner


class SchedulerObservation:
    def __init__(self):
        self._pid = os.getpid()
        self._closed = False
        try: self._identity = _observe()
        except SchedulerAdmissionError: raise
        except Exception: raise SchedulerAdmissionError(UNAVAILABLE) from None

    def __repr__(self): return '<SchedulerObservation private classic profile admission>'
    def __reduce__(self): raise TypeError('Scheduler observations cannot be serialized')

    def assert_held(self):
        require(not self._closed and self._pid == os.getpid())
        try: require(_observe() == self._identity)
        except SchedulerAdmissionError: raise
        except Exception: raise SchedulerAdmissionError(UNAVAILABLE) from None

    def close(self): self._closed = True


@contextmanager
def acquire():
    observation = SchedulerObservation()
    try: yield observation
    finally: observation.close()
