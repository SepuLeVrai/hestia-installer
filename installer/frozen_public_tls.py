"""Read a completed public parent without adopting it with the current code set.

The old registry, receipts and installed bundle remain authoritative. This
reader grants no right to replace their units, configurations or renewal worker.
"""
from installer import public_tls_runtime as native
from installer.model import ErrorCode, require


def reference(public, parent, *, observe=False):
    require(type(observe) is bool)
    parents, _ = public.parents(parent)
    profile = public._read('profile.json')
    require(profile is not None, ErrorCode.NOT_PLANNED)
    require(profile['parents'] == parents
            and profile['boot'] == public.boot._read('profile.json')
            and profile['acme'] == public.acme.profile(), ErrorCode.INCOMPATIBLE_STATE)
    # Deliberately do not call PublicTLSPlan.engine: that controller owns only
    # its original source set. The native registry still has to match exactly.
    engine, runtime = native.engine(public.journal, profile)
    document = engine.report()
    require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
    if observe:
        for spec, record in zip(document['plan']['steps'], document['steps']):
            require(engine.registry.get(spec).validate(engine._context(document, spec, record)),
                    ErrorCode.VALIDATION_FAILED)
        # Validate every original enable link and both listener identities.
        runtime.enabled()
        require(runtime.running('http') and runtime.running('https') and runtime.running('timer'),
                ErrorCode.VALIDATION_FAILED)
        require(public.journal.read() == document and public._read('profile.json') == profile,
                ErrorCode.SOURCE_DRIFT)
    return document, runtime
