"""Append-only Gateway publication ancestry, separate from native authority.

The original enrollment and first publication are never replaced. Each completed
publication can name exactly one successor in its own private slot. A partial
edge is fatal to ordinary readers. Only the fenced publisher may finish it.
"""
import os
import re

from installer import gateway_active_profile as a

MAX_GENERATIONS = 64
DIRECTORY = 'publications'


def digest(value):
    a.require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value),
              'GATEWAY_GENERATION_REFERENCE_CHANGED')
    return value


def slot(runtime, parent):
    return runtime.root / 'control' / DIRECTORY / digest(parent)


def enrollment(runtime):
    with a.fs._directory(runtime.root) as fd:
        value = a.c._json(a.files._read(fd, 'staged.json', a.c.MAX_RECORD))
    a.require(set(value) == {'binding', 'uid', 'gid'} and
        all(type(value[k]) is int and value[k] >= 0 for k in ('uid', 'gid')),
        'GATEWAY_ACTIVE_ENROLLMENT_CHANGED')
    return value


def edge(runtime, parent):
    """Read a complete edge; missing directory alone means no successor."""
    path = slot(runtime, parent)
    try: path.lstat()
    except FileNotFoundError: return None
    with a.fs._directory(path) as fd:
        a.files._private(fd, directory=True)
        a.require(set(os.listdir(fd)) == {a.INTENT, a.ACTIVE},
                  'GATEWAY_ACTIVE_PUBLICATION_INCOMPLETE')
        raw = a.files._read(fd, a.INTENT, a.c.MAX_RECORD)
        a.require(a.files._read(fd, a.ACTIVE, a.c.MAX_RECORD * 2) == a._receipt(raw),
                  'GATEWAY_ACTIVE_PUBLICATION_INCOMPLETE')
    return raw


def history(runtime, *, through=None):
    """Validate immutable ancestry without inspecting current binary/processes.

    A fenced recovery may stop at its explicitly pinned parent. It does not
    adopt an incomplete child or confer permission to roll that child back.
    """
    if through is not None: digest(through)
    original = enrollment(runtime)
    with a.fs._directory(runtime.root / 'control') as fd:
        a.files._private(fd, directory=True)
        raw = a.c._optional(fd, a.INTENT)
        done = a.c.stage._optional(fd, a.ACTIVE, a.c.MAX_RECORD * 2)
        if raw is None and done is None:
            a.fs._absent(fd, DIRECTORY)
            a.require(through is None, 'GATEWAY_GENERATION_PARENT_MISSING')
            return []
        a.require(raw is not None and done == a._receipt(raw),
                  'GATEWAY_ACTIVE_PUBLICATION_INCOMPLETE')
    result, leases, source, parent, state = [], set(), original, None, None
    while raw is not None:
        a.require(len(result) < MAX_GENERATIONS, 'GATEWAY_GENERATION_LIMIT')
        value = a.c._json(raw)
        a.require(value.get('version') == (1 if parent is None else 2),
                  'GATEWAY_GENERATION_PARENT_CHANGED')
        if parent is not None:
            a.require(value.get('previous_publication_sha256') == parent,
                      'GATEWAY_GENERATION_PARENT_CHANGED')
        current = a.GatewayServiceRuntime.from_binding(runtime.foundation,
            value['target_manifest']['binding'])
        a.require(a._record(current, raw) == source, 'GATEWAY_GENERATION_SOURCE_CHANGED')
        if result:
            a.require(value['cutover']['original_binary'] == a.c._json(result[-1])['target_armed']['replacement'],
                      'GATEWAY_GENERATION_BINARY_CHANGED')
        a._cutover_records(current, raw)
        lease = value['cutover']['lease_id']
        a.require(lease not in leases and (state is None or state == value['state']),
                  'GATEWAY_GENERATION_STATE_CHANGED')
        leases.add(lease); state = value['state']; result.append(raw)
        parent, source = a.sha(raw), value['target_manifest']
        if through == parent: return result
        raw = edge(runtime, parent)
    a.require(through is None, 'GATEWAY_GENERATION_PARENT_MISSING')
    return result


def source(runtime, parent):
    """Pin a source for a new fenced cutover; never select/start it implicitly."""
    values = history(runtime, through=digest(parent))
    raw = values[-1]
    a.require(a.c._json(raw)['target_manifest']['binding'] == runtime.profile.binding(),
              'GATEWAY_GENERATION_SOURCE_CHANGED')
    runtime._transition_parent = raw
    runtime._enrolled_manifest = enrollment(runtime)
    return runtime


def parent(runtime):
    raw = getattr(runtime, '_transition_parent', None)
    if raw is None: return None
    a.require(type(raw) is bytes and history(runtime, through=a.sha(raw))[-1] == raw
        and a.c._json(raw)['target_manifest']['binding'] == runtime.profile.binding()
        and getattr(runtime, '_enrolled_manifest', None) == enrollment(runtime),
        'GATEWAY_GENERATION_SOURCE_CHANGED')
    return a.sha(raw)


def publication_directory(runtime):
    previous = parent(runtime)
    return runtime.root / 'control' if previous is None else slot(runtime, previous)


def require_unpublished(runtime):
    """Block every old cutover action as soon as its own publication begins."""
    previous = parent(runtime)
    if previous is None:
        with a.fs._directory(runtime.root / 'control') as fd:
            a.fs._absent(fd, a.INTENT); a.fs._absent(fd, a.ACTIVE)
        return
    a.require(a.sha(history(runtime)[-1]) == previous, 'GATEWAY_GENERATION_NO_LONGER_CURRENT')
    path = slot(runtime, previous)
    try: path.parent.lstat()
    except FileNotFoundError: return
    with a.fs._directory(path.parent) as fd: a.fs._absent(fd, path.name)
