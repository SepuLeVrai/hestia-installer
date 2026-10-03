"""Read a completed shared front without adopting or replacing its code set."""
from installer import shared_public_runtime as native
from installer.model import ErrorCode, canonical_bytes, require


def reference(control, parent, *, observe=False):
    require(type(observe) is bool)
    prepared, gateway = control.binding(parent)
    profile = control.profile()
    require(profile is not None, ErrorCode.NOT_PLANNED)
    require(canonical_bytes(profile['preparation']) == canonical_bytes(prepared)
        and canonical_bytes(profile['gateway_binding']) == canonical_bytes(gateway), ErrorCode.SOURCE_DRIFT)
    engine, runtime = native.engine(control.journal, profile)
    document = engine.report()
    require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
    if observe:
        for spec, record in zip(document['plan']['steps'], document['steps']):
            require(engine.registry.get(spec).validate(engine._context(document, spec, record)), ErrorCode.VALIDATION_FAILED)
        require(control.journal.read() == document and control.profile() == profile, ErrorCode.SOURCE_DRIFT)
    return document, runtime
