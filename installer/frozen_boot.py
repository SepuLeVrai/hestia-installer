"""Explicit read-only reference to a previously enrolled boot bundle.

Unlike BootPlan.engine, a follow-on controller does not adopt that journal with
the current source set. It validates the original registry and installed copy.
No old profile, receipt, StepSpec or code bundle is rewritten.
"""
from installer import boot_runtime as native
from installer.model import ErrorCode, exact_keys, require


def reference(boot, parent, *, observe=False):
    require(boot.application.owns(parent) and parent['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
    activated, _ = boot.activation.engine(parent)
    activation = activated.report(); sql = boot.mariadb.engine().report()
    require(activation is not None and activation['state'] == 'DONE'
            and sql is not None and sql['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
    profile = boot._read('profile.json'); require(profile is not None, ErrorCode.NOT_PLANNED)
    exact_keys(profile, {'version', 'application', 'sql', 'parents', 'code'})
    require(type(profile['version']) is int and profile['version'] == 1
            and profile['application'] == boot.application.read() and profile['sql'] == boot.mariadb.profile()
            and profile['parents'] == {'preparation': parent['plan_sha256'], 'activation': activation['plan_sha256'],
                                      'sql': sql['plan_sha256']}, ErrorCode.INCOMPATIBLE_STATE)
    engine, runtime = native.engine(boot.journal, profile)
    document = engine.report()
    require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
    if observe:
        for spec, record in zip(document['plan']['steps'], document['steps']):
            require(engine.registry.get(spec).validate(engine._context(document, spec, record)), ErrorCode.VALIDATION_FAILED)
        runtime.live()
    return document, runtime
