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
CHAIN = 'gateway-generations'



def binding(generation, fragment_plan):
    g.frozen.digest(fragment_plan)
    previous = generation.value.get('predecessor')
    return {'version': 1 if previous is None else 2, 'policy': POLICY,
            **({} if previous is None else {'previous_generation_sha256': previous['generation_sha256']}),
            'lease_id': generation.value['lease_id'],
            'generation_sha256': generation.digest, 'fragment_plan_sha256': fragment_plan,
            'shared_profile_sha256': g.sha(generation.value['shared'])}


def _validate(value):
    g.require(type(value) is dict, g.ErrorCode.SOURCE_DRIFT)
    g.exact_keys(value, {'version', 'policy', 'lease_id', 'generation_sha256',
                         'fragment_plan_sha256', 'shared_profile_sha256'} |
                ({'previous_generation_sha256'} if value.get('version') == 2 else set()))
    g.require(type(value['version']) is int and value['version'] in (1, 2)
        and value['policy'] == POLICY and type(value['lease_id']) is str
        and re.fullmatch('[a-f0-9]{32}', value['lease_id']), g.ErrorCode.SOURCE_DRIFT)
    for key in set(value) - {'version', 'policy', 'lease_id'}: g.frozen.digest(value[key])
    return value


def pointer(shared):
    """Follow immutable metadata only; this is not a current native admission."""
    value = shared._read(NAME)
    if value is None:
        with g.boot.fs._directory(shared.root) as fd:
            g.boot.fs._absent(fd, NAME); g.boot.fs._absent(fd, CHAIN)
        return None
    _validate(value)
    g.require(value['version'] == 1, g.ErrorCode.SOURCE_DRIFT)
    seen = set(); leases = set()
    while True:
        from installer.gateway_publication_chain import MAX_GENERATIONS
        g.require(len(seen) < MAX_GENERATIONS and value['generation_sha256'] not in seen
            and value['lease_id'] not in leases, g.ErrorCode.SOURCE_DRIFT)
        seen.add(value['generation_sha256']); leases.add(value['lease_id'])
        root = shared.root / CHAIN
        try: root.lstat()
        except FileNotFoundError: return value
        with g.boot.fs._directory(root) as fd:
            g.fragments.files._private(fd, directory=True)
            next_value = g.fragments._optional(fd, value['generation_sha256'] + '.json')
        if next_value is None: return value
        _validate(next_value)
        g.require(next_value['version'] == 2
            and next_value['previous_generation_sha256'] == value['generation_sha256']
            and next_value['shared_profile_sha256'] == value['shared_profile_sha256'], g.ErrorCode.SOURCE_DRIFT)
        value = next_value


def publish(generation, lease, confirmation):
    """Internal transfer completion, with no service-start authority."""
    manager = g.systemd_manager(generation, lease)
    with manager.locked():
        report = manager.check(confirmation)
        generation.configuration()
        generation.installed_fragments(report['plan_sha256'])
        value = binding(generation, report['plan_sha256'])
        shared = generation.original.shared
        if value['version'] == 1:
            with g.boot.fs._directory(shared.root) as fd:
                g.fragments.files._private(fd, directory=True)
                g.fragments._put(fd, NAME, value)
        else:
            previous = generation.value['predecessor']; current = pointer(shared)
            g.require(current == value or current is not None
                and current['generation_sha256'] == previous['generation_sha256']
                and current['lease_id'] == previous['lease_id']
                and current['fragment_plan_sha256'] == previous['fragment_plan_sha256'], g.ErrorCode.SOURCE_DRIFT)
            from installer.transaction import _private_directory
            with _private_directory(shared.root / CHAIN, create=True) as fd:
                g.fragments._put(fd, previous['generation_sha256'] + '.json', value)
            g.require(pointer(shared) == value, g.ErrorCode.SOURCE_DRIFT)
        manager.check(confirmation)
    return value


def selected(shared, *, configuration_only=False):
    """The HTTP envelope may inspect an old overlay during fenced cutover.

    It still checks the exact installed fragments. All admissions and workers
    additionally require the current Gateway publication (the default path).
    """
    g.require(type(configuration_only) is bool, g.ErrorCode.INVALID_DATA)
    value = pointer(shared)
    if value is None: return None
    reader = object.__new__(g.Generation)
    reader.root = shared.layout.root / ('public-successor-' + value['lease_id'])
    profile = reader._read('profile.json')
    g.require(profile is not None, g.ErrorCode.SOURCE_DRIFT)
    generation = g.Generation(profile)
    g.require(generation.root == reader.root and generation.original.shared.root == shared.root
        and generation.value['shared'] == shared.value
        and value == binding(generation, value['fragment_plan_sha256']), g.ErrorCode.SOURCE_DRIFT)
    if configuration_only: generation.configuration(publication=False)
    else: generation.configuration()
    generation.installed_fragments(value['fragment_plan_sha256'])
    return generation
