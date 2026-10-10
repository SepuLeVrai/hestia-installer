"""Explicit, append-only cockpit cycles. Reading the chain has no host effects."""
from installer import gateway_public_ancestry as ancestry
from installer.gateway_public_generation import Generation
from installer.gateway_public_selection import binding as pointer_binding, pointer
from installer.gateway_publication_chain import MAX_GENERATIONS
from installer.mobile_activation_plan import digest
from installer.model import ErrorCode, exact_keys, require


def previous(controller, value):
    exact_keys(value, {'version', 'execution_sha256', 'predecessor', 'source_binding'})
    require(type(value['version']) is int and value['version'] == 1, ErrorCode.INVALID_STATE)
    ancestry.validate(value['predecessor'])
    profile = controller.profile()
    require(profile is not None and profile['policy'] == 'COCKPIT_GATEWAY_PUBLIC_TRANSITION_V1'
        and digest(profile) == value['execution_sha256'], ErrorCode.INCOMPATIBLE_STATE)
    approved, rows = controller.progress(profile)
    require(approved is not None and all(r['state'] == 'DONE' for r in rows), ErrorCode.DEPENDENCY_BLOCKED)
    generation = Generation(controller._read('public-generation.json'))
    require(generation.value['lease_id'] == profile['lease_id']
        and generation.value['mobile'] == profile['public_source']['mobile']
        and generation.value['target_binding'] == controller.transition.profile()['assessment']['target']
        and value['source_binding'] == generation.value['target_binding']
        and ancestry.binding(generation, {'fragment_plan_sha256': value['predecessor']['fragment_plan_sha256']})
            == value['predecessor'], ErrorCode.INCOMPATIBLE_STATE)
    return generation


def child(controller, value):
    from installer.gateway_transition_plan import GatewayTransitionPlan
    from installer.gateway_transition_execution import GatewayTransitionExecution
    from installer.mobile_backup_plan import MobileBackupPlan
    previous(controller, value)
    # Names come only from validated immutable records, never request paths.
    base = controller.transition.service.gateway.root / 'transition' / 'cycles' / value['execution_sha256']
    transition = GatewayTransitionPlan(controller.transition.service, root=base, cycle=value)
    backup = MobileBackupPlan(controller.backup.activation, root=base / 'backup', cycle=value)
    result = GatewayTransitionExecution(transition, backup, controller.mobile_boot)
    result.source_package = controller.root / 'binary/package.zip'
    return result


def selected(controller):
    seen = set()
    for _ in range(MAX_GENERATIONS):
        value = controller._read('next.json')
        if value is None:
            import os
            from installer.transaction import _private_directory
            try:
                with _private_directory(controller.root, create=False) as fd:
                    os.stat('next.json', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: pass
            else: require(False, ErrorCode.INVALID_STATE)
            return controller
        require(value.get('execution_sha256') not in seen, ErrorCode.INCOMPATIBLE_STATE)
        seen.add(value['execution_sha256'])
        controller = child(controller, value)
    require(False, ErrorCode.SOURCE_LIMIT)


def open_next(controller, payload):
    """One explicit current native check precedes the durable next-cycle link."""
    exact_keys(payload, {'confirmation', 'confirm'})
    require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
    profile = controller.profile()
    require(profile is not None and payload['confirmation'] == digest(profile), ErrorCode.CONFIRMATION_REQUIRED)
    require(profile['policy'] == 'COCKPIT_GATEWAY_PUBLIC_TRANSITION_V1', ErrorCode.UNSUPPORTED_MODULE)
    # execute(check) revalidates the frozen binding, binary package, consumed
    # admission, current publication, native processes and public opening.
    controller.execute('next', payload)
    return child(controller, controller._read('next.json'))


def record_next(controller, profile):
    """Internal call, after check and with the cockpit parent lock still held."""
    generation = Generation(controller._read('public-generation.json'))
    current = pointer(generation.original.shared)
    require(current is not None and current == pointer_binding(generation, current['fragment_plan_sha256']), ErrorCode.SOURCE_DRIFT)
    value = {'version': 1, 'execution_sha256': digest(profile),
        'predecessor': ancestry.binding(generation, current),
        'source_binding': generation.value['target_binding']}
    require(ancestry.history(generation) < MAX_GENERATIONS, ErrorCode.SOURCE_LIMIT)
    previous(controller, value)
    old = controller._read('next.json')
    if old is None: controller._write('next.json', value)
    else: require(old == value, ErrorCode.SOURCE_DRIFT)
