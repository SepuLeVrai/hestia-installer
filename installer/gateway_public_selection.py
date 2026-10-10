"""Read-only selection of a transferred public generation, never admission.

The pointer is published under the original public effect lock only after the
native manager has proved the completed transfer. Readers independently check
the sealed generation and the eight installed fragment identities. An invalid
pointer is fatal; it never selects the historical worker as a fallback.
"""
import os
import re

from installer import gateway_public_generation as g

NAME = 'gateway-successor.json'
POLICY = 'GATEWAY_PUBLIC_SELECTION_V1'


def binding(generation, fragment_plan):
    g.frozen.digest(fragment_plan)
    return {'version': 1, 'policy': POLICY, 'lease_id': generation.value['lease_id'],
            'generation_sha256': generation.digest, 'fragment_plan_sha256': fragment_plan,
            'shared_profile_sha256': g.sha(generation.value['shared'])}


def publish(generation, lease, confirmation):
    """Internal transfer completion, with no service-start authority."""
    manager = g.systemd_manager(generation, lease)
    with manager.locked():
        report = manager.check(confirmation)
        generation.configuration()
        generation.installed_fragments(report['plan_sha256'])
        value = binding(generation, report['plan_sha256'])
        with g.boot.fs._directory(generation.original.shared.root) as fd:
            g.fragments.files._private(fd, directory=True)
            g.fragments._put(fd, NAME, value)
        manager.check(confirmation)
    return value


def selected(shared):
    """Return None only when no selection exists; corruption propagates."""
    with g.boot.fs._directory(shared.root) as fd:
        try: os.stat(NAME, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError: return None
    value = shared._read(NAME)
    g.require(value is not None, g.ErrorCode.SOURCE_DRIFT)
    g.exact_keys(value, {'version', 'policy', 'lease_id', 'generation_sha256',
                         'fragment_plan_sha256', 'shared_profile_sha256'})
    g.require(type(value['version']) is int and value['version'] == 1
        and value['policy'] == POLICY and type(value['lease_id']) is str
        and re.fullmatch('[a-f0-9]{32}', value['lease_id']), g.ErrorCode.SOURCE_DRIFT)
    for key in ('generation_sha256', 'fragment_plan_sha256', 'shared_profile_sha256'):
        g.frozen.digest(value[key])
    reader = object.__new__(g.Generation)
    reader.root = shared.layout.root / ('public-successor-' + value['lease_id'])
    profile = reader._read('profile.json')
    g.require(profile is not None, g.ErrorCode.SOURCE_DRIFT)
    generation = g.Generation(profile)
    g.require(generation.root == reader.root and generation.original.shared.root == shared.root
        and generation.value['shared'] == shared.value
        and value == binding(generation, value['fragment_plan_sha256']), g.ErrorCode.SOURCE_DRIFT)
    generation.configuration()
    generation.installed_fragments(value['fragment_plan_sha256'])
    return generation
