"""Stop only the enrolled Gateway under the Web gate, before Foundation."""
import re

from installer import foundation_drain, http_runtime as h
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import ErrorCode, canonical_bytes, exact_keys, require, strict_json_loads


def attached(http, foundation):
    root = http.spec.root.parent / 'gateway-service'
    try: root.lstat()
    except FileNotFoundError: return None
    require(foundation is not None, ErrorCode.SOURCE_DRIFT)
    with h.fs._directory(root) as fd:
        manifest = strict_json_loads(h.f._read(fd, 'staged.json', 0, mode=0o600))
    exact_keys(manifest, {'binding', 'uid', 'gid'})
    value = manifest['binding']
    require(type(value) is dict, ErrorCode.SOURCE_DRIFT)
    from installer.gateway_active_profile import selected
    runtime = selected(foundation, manifest)
    if runtime is None:
        runtime = GatewayServiceRuntime.from_binding(foundation, value)
        require(value == runtime.profile.binding(), ErrorCode.SOURCE_DRIFT)
    runtime.inspect()
    return runtime


def binding(runtime):
    from installer.gateway_resume_authority import historical_binding
    historical = historical_binding(runtime)
    if historical is not None: return historical
    return {'unit': runtime.unit, 'manifest_sha256': h.f._sha(canonical_bytes(runtime.manifest(runtime.account()))),
            'state': runtime.state_binding(), 'policy': 'GATED_GATEWAY_STOP_BEFORE_FOUNDATION_V1'}


def quiesce(runtime, lease):
    lease.assert_held()
    require(lease.scope.instance == runtime.web.spec.instance
            and lease.scope.directory == runtime.web.spec.maintenance_directory, ErrorCode.INCOMPATIBLE_STATE)
    records = foundation_drain.Records(runtime); name = 'quiesce-' + lease.lease_id + '.json'
    expected = {'binding': binding(runtime), 'lease_id': lease.lease_id}
    value = runtime.inspect(); active = value['ActiveState'] == 'active'
    if active: runtime.owned(serving=False)
    else: runtime.stopped()
    intent = records._read(name)
    if intent is None:
        intent = {**expected, 'pid': value['MainPID']}; records._write(name, intent)
    exact_keys(intent, {'binding', 'lease_id', 'pid'})
    require(all(intent[k] == v for k, v in expected.items()) and type(intent['pid']) is str
            and re.fullmatch(r'0|[1-9][0-9]*', intent['pid']), ErrorCode.SOURCE_DRIFT)
    if active:
        require(intent['pid'] == value['MainPID'], ErrorCode.MANUAL_ACTION_REQUIRED)
        lease.assert_held(); runtime.owned(serving=False)
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'stop', '--', runtime.unit])
    runtime.stopped(); lease.assert_held()
    require(binding(runtime) == expected['binding'], ErrorCode.SOURCE_DRIFT)
    quiet = 'quiet-' + lease.lease_id + '.json'; current = records._read(quiet)
    if current is None: records._write(quiet, expected)
    else: require(current == expected, ErrorCode.SOURCE_DRIFT)


def quiet_binding(http, foundation):
    runtime = attached(http, foundation)
    if runtime is None: return None
    runtime.stopped()
    return binding(runtime)
