"""Quiesce an enrolled Foundation before the unchanged strict Web census.

No new identity exception. Gate first, verify the exact owned listener, record
intent, stop only that unit, then require its cgroup empty for the entire backup.
No implicit reopen or restart, including after a backup failure.
"""
import re

from installer import http_runtime as h
from installer.application_activation import Activation
from installer.foundation_runtime import FoundationRuntime
from installer.gateway_identity import public_identity
from installer.model import ErrorCode, canonical_bytes, exact_keys, require, strict_json_loads
from installer.package_plan import PackagePlan


def attached(http):
    root = http.spec.root.parent / 'foundation'
    # Legacy profiles retain their exact policy and need no new host probe.
    try: root.lstat()
    except FileNotFoundError: return None
    account, _, _, _ = http._inspect_configuration()
    with h.fs._directory(root) as fd:
        config = strict_json_loads(h.f._read(fd, 'main.json', account.pw_gid))
        manifest = strict_json_loads(h.f._read(fd, 'staged.json', 0, mode=0o600))
    exact_keys(config, {'environment', 'gateway_keys', 'canonical_contexts', 'canonical_distribution'})
    require(config['environment'] == 'main' and config['canonical_contexts'] is True
            and config['canonical_distribution'] is True and type(config['gateway_keys']) is dict
            and len(config['gateway_keys']) == 1, ErrorCode.SOURCE_DRIFT)
    kid, jwk = next(iter(config['gateway_keys'].items())); identity = public_identity('main', jwk)
    require(kid == identity['kid'], ErrorCode.SOURCE_DRIFT)
    runtime = FoundationRuntime(Activation(http, manifest['web_plan_sha256']), identity)
    runtime.inspect()
    return runtime


class Records:
    _read, _write = PackagePlan._read, PackagePlan._write
    def __init__(self, runtime): self.root = runtime.root / 'control'


def binding(runtime):
    gid = runtime.host().pw_gid
    return {'unit': runtime.unit, 'manifest_sha256': h.f._sha(canonical_bytes(runtime.manifest(gid))),
            'policy': 'GATED_FOUNDATION_STOP_ONLY_V1'}


def quiesce(runtime, lease):
    lease.assert_held()
    require(lease.scope.instance == runtime.web.spec.instance
            and lease.scope.directory == runtime.web.spec.maintenance_directory, ErrorCode.INCOMPATIBLE_STATE)
    records = Records(runtime); name = 'quiesce-' + lease.lease_id + '.json'
    expected = {'binding': binding(runtime), 'lease_id': lease.lease_id}
    value = runtime.inspect(); active = value['ActiveState'] == 'active'
    if active: runtime.owned(serving=False)
    else: runtime.stopped()
    intent = records._read(name)
    if intent is None:
        intent = {**expected, 'pid': value['MainPID']}
        records._write(name, intent)
    exact_keys(intent, {'binding', 'lease_id', 'pid'})
    require(all(intent[k] == v for k, v in expected.items())
            and type(intent['pid']) is str and re.fullmatch(r'0|[1-9][0-9]*', intent['pid']), ErrorCode.SOURCE_DRIFT)
    if active:
        # An explicit retry may finish a lost stop only for the same owned PID.
        require(intent['pid'] == value['MainPID'], ErrorCode.MANUAL_ACTION_REQUIRED)
        lease.assert_held(); runtime.owned(serving=False)
        h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'stop', '--', runtime.unit])
    runtime.stopped(); lease.assert_held()
    quiet = 'quiet-' + lease.lease_id + '.json'
    current = records._read(quiet)
    if current is None: records._write(quiet, expected)
    else: require(current == expected, ErrorCode.SOURCE_DRIFT)


def quiet_binding(http):
    runtime = attached(http)
    if runtime is None: return None
    runtime.stopped()
    return binding(runtime)
