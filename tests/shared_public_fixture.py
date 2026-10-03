"""Bound native-effect fixture for the real public HTTPS/browser facade."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from installer import shared_public_runtime as native
from installer.operations import Recovery, RecoveryDecision
from test_shared_public_plan import complete
from test_shared_public_runtime import selected


def attach(case, service):
    parent = complete(service.engine)
    value = selected(); prepared = value['preparation']
    prepared['parents']['web'] = parent['plan_sha256']
    case.enterContext(patch.object(service.shared_public_preparation, 'binding', return_value=deepcopy(prepared)))
    gateway = SimpleNamespace(profile=SimpleNamespace(identity=prepared['gateway_identity'],
        binding=lambda: deepcopy(value['gateway_binding'])),
        web=SimpleNamespace(spec=SimpleNamespace(instance=prepared['instance'])))
    case.enterContext(patch.object(service.gateway_service, 'engine', return_value=(SimpleNamespace(report=lambda: {'state': 'DONE'}), gateway)))
    case.enterContext(patch.object(service.public_tls, 'state', return_value={'installation': {'state': 'DONE', 'plan_sha256': prepared['parents']['public']}, 'phase5_complete': False}))
    case.enterContext(patch.object(service.gateway, 'state', return_value={'preparation': {'state': 'DONE', 'plan_sha256': prepared['parents']['gateway']}, 'profile': None}))
    case.enterContext(patch.object(service.gateway_service, 'state', return_value={'installation': {'state': 'DONE'}, 'profile': None}))
    case.enterContext(patch.object(native.SharedPublic, 'absent'))
    # writer is a context manager, while all installed workers remain untouched.
    from contextlib import nullcontext
    case.enterContext(patch.object(native.h.HttpRuntime, '_scope', return_value=SimpleNamespace(writer=lambda: nullcontext())))
    case.enterContext(patch('installer.service_identity.ServiceIdentity.account', return_value=None))
    case.enterContext(patch.object(native.SharedOperation, 'prepare'))
    case.enterContext(patch.object(native.SharedOperation, 'validate', return_value=True))
    calls = []
    def apply(operation, context):
        assert service.shared_public.journal.read()['approved_plan_sha256'] is not None
        calls.append(operation.phase)
        return operation.receipt()
    case.enterContext(patch.object(native.SharedOperation, 'apply', apply))
    case.enterContext(patch.object(native.SharedOperation, 'recover', lambda op, ctx, phase: Recovery(RecoveryDecision.APPLIED, op.receipt())))
    return SimpleNamespace(calls=calls, preparation=prepared, value=value)
