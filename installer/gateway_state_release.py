"""Explicit Gateway unseal, with a durable refusal of activity until composition.

This primitive never starts a service, opens SQL, restores a source, or removes
maintenance. A completed receipt remains a maintenance blocker. Only a later
composed admission may consume it after validating all Web/SQL barriers.
"""
from contextlib import ExitStack
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import time

from installer import gateway_state_fence as g, gateway_state_backup as b
from installer.model import canonical_bytes, strict_json_loads

RELEASE = 'gateway-state.release'
RELEASED = 'gateway-state.released'
fs, f, files, require = g.fs, g.f, g.files, g.require


def closed(operation):
    @wraps(operation)
    def invoke(*args, **kwargs):
        try: return operation(*args, **kwargs)
        except g.GatewayStateError: raise
        except Exception: raise g.GatewayStateError('GATEWAY_RELEASE_UNAVAILABLE') from None
    return invoke


def _read(fd, name):
    return f._read(fd, name, 0, mode=0o600, limit=g.MAX_JOURNAL * 2)


def _optional(fd, name):
    try: return _read(fd, name)
    except FileNotFoundError: return None


def _json(raw):
    value = strict_json_loads(raw)
    require(type(value) is dict and canonical_bytes(value) == raw, 'GATEWAY_RELEASE_JOURNAL_CHANGED')
    return value


def _hash(fd, *, cancel=None):
    before = os.fstat(fd); digest = hashlib.sha256(); total = 0
    deadline = time.monotonic() + 60; os.lseek(fd, 0, os.SEEK_SET)
    while True:
        b._cancel(cancel, deadline)
        chunk = os.read(fd, 1024 * 1024)
        if not chunk: break
        total += len(chunk); require(total <= g.MAX_BYTES, 'GATEWAY_RELEASE_SIZE_REJECTED')
        digest.update(chunk)
    require(total == before.st_size and files._same(before, os.fstat(fd)), 'GATEWAY_RELEASE_SOURCE_CHANGED')
    return {'bytes': total, 'sha256': digest.hexdigest()}


def _path(root, runtime):
    require(isinstance(root, Path) and root.is_absolute(), 'GATEWAY_RELEASE_PATH_REJECTED')
    for protected in (runtime.root, runtime.profile.key_directory, runtime.web.spec.root,
                      runtime.web.spec.webroot, runtime.web.spec.maintenance_directory):
        require(root != protected and protected not in root.parents and root not in protected.parents,
                'GATEWAY_RELEASE_PATH_REJECTED')
    with fs._directory(root) as fd: files._private(fd, directory=True)


def _backup(root, runtime, barrier, fence_raw, *, intent=None, cancel=None):
    """Reobserve the exact composed backup; read bytes only, no SQLite worker."""
    _path(root, runtime)
    slot = root / ('gateway-' + barrier._lease.lease_id)
    with fs._directory(slot) as fd:
        files._private(fd, directory=True)
        snapshot_raw = _read(fd, 'snapshot.json'); snapshot = _json(snapshot_raw)
        composed_raw = _read(fd, 'composed.json'); composed = _json(composed_raw)
        receipt_raw = _read(fd, 'verified.json'); receipt = _json(receipt_raw)
        require(_read(fd, 'attempt.json') == canonical_bytes({'state': 'GATEWAY_BACKUP_STARTED',
            'fence_sha256': f._sha(fence_raw), 'lease_id': barrier._lease.lease_id}), 'GATEWAY_RELEASE_BACKUP_CHANGED')
        require(set(composed) == {'gateway_snapshot', 'web', 'receipt'} and composed['receipt'] == receipt
            and composed['gateway_snapshot'] == snapshot, 'GATEWAY_RELEASE_BACKUP_CHANGED')
        require(receipt.get('state') == 'MOBILE_BACKUP_RESTORE_VERIFIED'
            and receipt.get('activity_resumed') is False and receipt.get('restore_to_original_allowed') is False
            and receipt.get('gateway_snapshot_sha256') == f._sha(snapshot_raw)
            and receipt.get('gateway_fence_sha256') == f._sha(fence_raw)
            and receipt.get('gateway_snapshot_id') == slot.name
            and receipt.get('gateway_sqlite_restoration_verified') is True
            and receipt.get('database_restoration_verified') is True
            and receipt.get('registered_data_restoration_verified') is True
            and receipt.get('boot_delivered') is False and receipt.get('public_mobile_delivered') is False,
            'GATEWAY_RELEASE_COMPOSED_BACKUP_REQUIRED')
        web = composed['web']
        require(type(web) is dict and type(web.get('backup_id')) is str
            and re.fullmatch(r'[a-f0-9]{32}', web['backup_id'])
            and web.get('state') == 'PROVISIONED_BACKUP_RESTORE_VERIFIED'
            and web.get('activity_resumed') is False, 'GATEWAY_RELEASE_COMPOSED_BACKUP_REQUIRED')
        with fs._directory(root / web['backup_id']) as webfd:
            files._private(webfd, directory=True)
            coordinated_raw = _read(webfd, 'coordinated.json'); coordinated = _json(coordinated_raw)
            require(coordinated.get('instance') == runtime.web.spec.instance
                and coordinated.get('lease_id') == barrier._lease.lease_id
                and web.get('service_profile_sha256') == f._sha(barrier._profile)
                and _read(webfd, 'verified.json') == canonical_bytes(web)
                and f._sha(coordinated_raw) == web['manifest_sha256'],
                'GATEWAY_RELEASE_BACKUP_CHANGED')
        binding = {'version': 1, 'fence': _json(fence_raw), 'snapshot_sha256': f._sha(snapshot_raw),
                   'composed_sha256': f._sha(composed_raw), 'web_verified_sha256': f._sha(canonical_bytes(web))}
        if intent is not None:
            require(canonical_bytes(intent) == canonical_bytes(binding), 'GATEWAY_RELEASE_JOURNAL_CHANGED')
        require(snapshot.get('fence_sha256') == f._sha(fence_raw)
            and snapshot.get('lease_id') == barrier._lease.lease_id
            and snapshot.get('binding') == binding['fence']['binding']
            and snapshot.get('isolated_restore_verified') is True
            and snapshot.get('activity_resumed') is False and snapshot.get('restore_to_original_allowed') is False,
            'GATEWAY_RELEASE_BACKUP_CHANGED')
        expected = snapshot['files']
        require(type(expected) is dict and {'gateway.db', 'gateway.lock'} <= set(expected)
            and all(n in g.NAMES or n.startswith(g.CACHE + '/')
                and g.CACHE_NAME.fullmatch(n.split('/', 1)[1]) for n in expected), 'GATEWAY_RELEASE_BACKUP_CHANGED')
        with fs._directory(slot / 'source') as source:
            files._private(source, directory=True)
            require(set(os.listdir(source)) == {n for n in expected if '/' not in n}
                | ({g.CACHE} if snapshot['editor_cache'] else set()), 'GATEWAY_RELEASE_BACKUP_CHANGED')
        if snapshot['editor_cache']:
            with fs._directory(slot / 'source' / g.CACHE) as cache:
                files._private(cache, directory=True)
                require(set(os.listdir(cache)) == {n.split('/', 1)[1] for n in expected if '/' in n},
                        'GATEWAY_RELEASE_BACKUP_CHANGED')
        for name, record in expected.items():
            with fs._directory(slot / 'source' / (g.CACHE if '/' in name else '')) as source:
                handle = b._opened(source, name.rsplit('/', 1)[-1])
                try: require(_hash(handle, cancel=cancel) == record, 'GATEWAY_RELEASE_BACKUP_CHANGED')
                finally: os.close(handle)
        handle = b._opened(fd, 'database.sqlite')
        try:
            require(_hash(handle, cancel=cancel) == {k: snapshot['sqlite'][k] for k in ('bytes', 'sha256')},
                    'GATEWAY_RELEASE_BACKUP_CHANGED')
        finally: os.close(handle)
    return binding, expected


def _observe(runtime, barrier, opened, records, fence_raw, *, released=False):
    root = opened['.']; account = runtime.account(); mount = g.inf._mount_id(root)
    current = runtime.state_directory()
    try: require(os.path.samestat(os.fstat(root), os.fstat(current)), 'GATEWAY_RELEASE_SOURCE_CHANGED')
    finally: os.close(current)
    require(set(os.listdir(root)) == {n for n in opened if n != '.' and '/' not in n},
            'GATEWAY_RELEASE_SOURCE_CHANGED')
    if g.CACHE in opened:
        require(set(os.listdir(opened[g.CACHE])) == {n.split('/', 1)[1] for n in opened if '/' in n},
                'GATEWAY_RELEASE_SOURCE_CHANGED')
    for row in records:
        name = row['name']
        require(g._record(opened[name], name, account, mount) == row, 'GATEWAY_RELEASE_SOURCE_CHANGED')
        if name != '.':
            parent = opened[g.CACHE] if '/' in name else root
            named = os.stat(name.rsplit('/', 1)[-1], dir_fd=parent, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (row['device'], row['inode']), 'GATEWAY_RELEASE_SOURCE_CHANGED')
    expected = {'version': 1, 'lease_id': barrier._lease.lease_id, 'instance': runtime.web.spec.instance,
        'binding': g.gd.binding(runtime), 'barrier_sha256': f._sha(barrier._profile), 'entries': records}
    require(canonical_bytes(expected) == fence_raw, 'GATEWAY_RELEASE_SOURCE_CHANGED')
    for row in records:
        flags = g.inf._flags(opened[row['name']])
        require(flags == row['flags'] if released else flags in (row['flags'], row['flags'] | g.inf.IMMUTABLE),
                'GATEWAY_RELEASE_FLAGS_CHANGED')


def _sources(opened, expected, *, cancel=None):
    require(set(opened) - {'.', g.CACHE} == set(expected), 'GATEWAY_RELEASE_SOURCE_CHANGED')
    for name, value in expected.items():
        require(_hash(opened[name], cancel=cancel) == value, 'GATEWAY_RELEASE_SOURCE_CHANGED')


def _receipt(raw):
    return canonical_bytes({'version': 1, 'state': 'GATEWAY_STATE_RELEASED_ACTIVITY_CLOSED',
        'intent': _json(raw), 'intent_sha256': f._sha(raw), 'activity_resumed': False,
        'restore_to_original_allowed': False, 'services_started': False})


class GatewayReleaseVerification:
    def __init__(self, raw): self._raw = raw
    def report(self): return strict_json_loads(self._raw)


def _finish(runtime, barrier, opened, records, mount, raw, expected, *, cancel=None):
    gate = barrier._lease._directory; value = _json(raw); fence_raw = canonical_bytes(value['fence'])
    completed = _optional(gate, RELEASED)
    if completed is not None:
        require(completed == _receipt(raw), 'GATEWAY_RELEASE_JOURNAL_CHANGED')
    _observe(runtime, barrier, opened, records, fence_raw, released=completed is not None)
    marker = _optional(gate, g.MARKER)
    require(marker == fence_raw or marker is None and completed is not None, 'GATEWAY_RELEASE_JOURNAL_CHANGED')
    _sources(opened, expected, cancel=cancel)
    if completed is None:
        require(_read(gate, RELEASE) == raw, 'GATEWAY_RELEASE_JOURNAL_CHANGED')
        # Reverse traversal leaves the state directory protected until last.
        deadline = time.monotonic() + 60
        for row in reversed(records):
            b._cancel(cancel, deadline)
            handle = opened[row['name']]
            require(g._record(handle, row['name'], runtime.account(), mount) == row,
                    'GATEWAY_RELEASE_SOURCE_CHANGED')
            g.inf._flags(handle, row['flags']); os.fsync(handle)
        _observe(runtime, barrier, opened, records, fence_raw, released=True)
        _sources(opened, expected, cancel=cancel)
        barrier.assert_held()
        files._new(gate, RELEASED, _receipt(raw))
    # The completed blocker is durable BEFORE either earlier blocker disappears.
    if marker is not None:
        require(_read(gate, g.MARKER) == fence_raw, 'GATEWAY_RELEASE_JOURNAL_CHANGED')
        os.unlink(g.MARKER, dir_fd=gate); os.fsync(gate)
    pending = _optional(gate, RELEASE)
    if pending is not None:
        require(pending == raw, 'GATEWAY_RELEASE_JOURNAL_CHANGED')
        os.unlink(RELEASE, dir_fd=gate); os.fsync(gate)
    barrier.assert_held()
    return GatewayReleaseVerification(_receipt(raw))


@closed
def release(snapshot, *, confirmed, cancel=None):
    """Release only a verified composed snapshot; keep its maintenance blocker."""
    require(confirmed is True, 'GATEWAY_RELEASE_CONSENT_REQUIRED')
    require(type(snapshot) is b.GatewayBackup and type(snapshot.fence) is g.GatewayStateFence,
            'GATEWAY_RELEASE_SNAPSHOT_REQUIRED')
    fence = snapshot.fence
    fs._absent(fence.barrier._lease._directory, 'gateway-cutover.attempt')
    fs._absent(fence.barrier._lease._directory, 'gateway-active-profile.attempt')
    g._inputs(fence.runtime, fence.barrier, confirmed); snapshot.verify(cancel=cancel)
    gate = fence.barrier._lease._directory
    fs._absent(gate, RELEASE); fs._absent(gate, RELEASED)
    binding, expected = _backup(snapshot.slot.parent, fence.runtime, fence.barrier, fence.raw, cancel=cancel)
    _sources(fence.opened, expected, cancel=cancel); fence.assert_held()
    raw = canonical_bytes(binding)
    require(len(raw) <= g.MAX_JOURNAL * 2, 'GATEWAY_RELEASE_SIZE_REJECTED')
    files._new(gate, RELEASE, raw)
    try:
        return _finish(fence.runtime, fence.barrier, fence.opened, fence.value['entries'],
                       fence.mount, raw, expected, cancel=cancel)
    finally: fence.close()  # Releases flock; never resumes HTTP or clears the completed blocker.


@closed
def recover(runtime, barrier, backup_root, *, confirmed, cancel=None):
    """Explicit reconciliation only; absence of an intent is never completion."""
    fs._absent(barrier._lease._directory, 'gateway-cutover.attempt')
    fs._absent(barrier._lease._directory, 'gateway-active-profile.attempt')
    g._inputs(runtime, barrier, confirmed)
    gate = barrier._lease._directory
    raw = _optional(gate, RELEASE); completed = _optional(gate, RELEASED)
    require(raw is not None or completed is not None, 'GATEWAY_RELEASE_INTENT_REQUIRED')
    if raw is None:
        receipt = _json(completed); raw = canonical_bytes(receipt['intent'])
        require(completed == _receipt(raw), 'GATEWAY_RELEASE_JOURNAL_CHANGED')
    intent = _json(raw); fence_raw = canonical_bytes(intent['fence'])
    with ExitStack() as stack:
        opened, records, mount = g._open(runtime, stack)
        _observe(runtime, barrier, opened, records, fence_raw, released=completed is not None)
        _, expected = _backup(backup_root, runtime, barrier, fence_raw, intent=intent, cancel=cancel)
        return _finish(runtime, barrier, opened, records, mount, raw, expected, cancel=cancel)
