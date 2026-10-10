"""Pinned public predecessor records; historical evidence is never admission."""
from installer import gateway_public_fragments as f
from installer.model import canonical_bytes

FIELDS = {'lease_id', 'generation_sha256', 'publication_sha256',
          'fragment_plan_sha256', 'completion_sha256', 'activation_sha256'}


def validate(value):
    import re
    f.require(type(value) is dict and set(value) == FIELDS)
    f.require(type(value['lease_id']) is str and re.fullmatch('[a-f0-9]{32}', value['lease_id']))
    for key in FIELDS - {'lease_id'}: f._digest(value[key])
    return value


def binding(generation, pointer):
    """Call only after the coordinator's explicit current Opening.check()."""
    completion = generation._read('opening-completed.json')
    activation = generation._read('activated.json')
    f.require(type(completion) is dict and type(activation) is dict
        and set(completion) == {'owner', 'plan_sha256', 'receipts_sha256'}
        and set(activation) == {'generation_sha256', 'fragment_plan_sha256', 'admission_sha256'}
        and completion.get('owner') == activation
        and activation.get('generation_sha256') == generation.digest
        and activation.get('fragment_plan_sha256') == pointer['fragment_plan_sha256'])
    for name in ('plan_sha256', 'receipts_sha256'): f._digest(completion[name])
    f._digest(activation['admission_sha256'])
    return {'lease_id': generation.value['lease_id'], 'generation_sha256': generation.digest,
        'publication_sha256': generation.value['publication_sha256'],
        'fragment_plan_sha256': pointer['fragment_plan_sha256'],
        'completion_sha256': f.sha(canonical_bytes(completion)),
        'activation_sha256': f.sha(canonical_bytes(activation))}


def load(child):
    """Read the exact earlier bundle and seals, without current native claims."""
    from installer import gateway_public_generation as g
    value = validate(child.value['predecessor'])
    reader = object.__new__(g.Generation)
    reader.root = child.layout.root / ('public-successor-' + value['lease_id'])
    profile = reader._read('profile.json')
    f.require(profile is not None)
    previous = g.Generation(profile)
    f.require(previous.root == reader.root and previous.digest == value['generation_sha256']
        and previous.value['publication_sha256'] == value['publication_sha256']
        and previous.value['shared'] == child.value['shared']
        and previous.value['mobile'] == child.value['mobile']
        and previous.value['target_binding'] == child.value['source_binding']
        and previous.value['lease_id'] != child.value['lease_id'])
    pointer = {'fragment_plan_sha256': value['fragment_plan_sha256']}
    f.require(binding(previous, pointer) == value)
    previous.bundle()
    return previous


def history(child):
    from installer.gateway_publication_chain import MAX_GENERATIONS
    seen = {child.digest}; current = child
    while 'predecessor' in current.value:
        f.require(len(seen) < MAX_GENERATIONS)
        current = load(current)
        f.require(current.digest not in seen)
        seen.add(current.digest)
    return len(seen)
