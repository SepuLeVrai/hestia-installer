"""Exact Gateway publication references for newly enrolled public/boot bundles.

Planning reads private evidence only. A frozen consumer never follows a newer
selection implicitly; native admission revalidates the pinned publication.
"""
import re

from installer import gateway_active_profile as a
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import canonical_bytes


@a.closed
def reference(original):
    """Return a historical selection, never a claim of current availability."""
    a.require(type(original) is GatewayServiceRuntime, 'GATEWAY_FROZEN_RUNTIME_REQUIRED')
    with a.fs._directory(original.root) as fd:
        enrollment = a.c._json(a.files._read(fd, 'staged.json', a.c.MAX_RECORD))
    a.require(set(enrollment) == {'binding', 'uid', 'gid'}
        and all(type(enrollment[k]) is int and enrollment[k] >= 0 for k in ('uid', 'gid'))
        and canonical_bytes(enrollment['binding']) == canonical_bytes(original.profile.binding()),
              'GATEWAY_FROZEN_ENROLLMENT_CHANGED')
    with a.fs._directory(original.root / 'control') as fd:
        a.files._private(fd, directory=True)
        raw = a.c._optional(fd, a.INTENT)
        completed = a.c.stage._optional(fd, a.ACTIVE, a.c.MAX_RECORD * 2)
    if raw is None and completed is None:
        return original.profile.binding(), None
    a.require(raw is not None and completed == a._receipt(raw), 'GATEWAY_ACTIVE_PUBLICATION_INCOMPLETE')
    value = a.c._json(raw)
    runtime = GatewayServiceRuntime.from_binding(original.foundation, value['target_manifest']['binding'])
    a.require(canonical_bytes(a._record(runtime, raw)) == canonical_bytes(enrollment),
              'GATEWAY_FROZEN_ENROLLMENT_CHANGED')
    a._records(runtime, raw)
    return runtime.profile.binding(), a.sha(raw)


def digest(value):
    a.require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None,
              'GATEWAY_FROZEN_PUBLICATION_REQUIRED')
    return value


@a.closed
def attach(runtime, publication_sha256):
    """Authorize only the already-frozen target; never replace its profile."""
    a.require(type(runtime) is GatewayServiceRuntime, 'GATEWAY_FROZEN_RUNTIME_REQUIRED')
    digest(publication_sha256)
    with a.fs._directory(runtime.root) as fd:
        enrollment = a.c._json(a.files._read(fd, 'staged.json', a.c.MAX_RECORD))
    current = a.selected(runtime.foundation, enrollment)
    a.require(current is not None and a.sha(current._active_profile) == publication_sha256
        and canonical_bytes(current.profile.binding()) == canonical_bytes(runtime.profile.binding()),
        'GATEWAY_FROZEN_PUBLICATION_CHANGED')
    # Only evidence is attached. inspect() audits the actual target binary,
    # config, process and identities, with publication checks on both sides.
    runtime._active_profile = current._active_profile
    runtime._enrolled_manifest = current._enrolled_manifest
    runtime.inspect()


@a.closed
def matches(runtime, publication_sha256):
    """Check a current native reader against the frozen profile's generation."""
    raw = getattr(runtime, '_active_profile', None)
    if publication_sha256 is None:
        a.require(raw is None, 'GATEWAY_FROZEN_PUBLICATION_REQUIRED')
    else:
        digest(publication_sha256)
        a.require(raw is not None and a.sha(raw) == publication_sha256,
                  'GATEWAY_FROZEN_PUBLICATION_CHANGED')
