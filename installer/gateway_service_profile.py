"""Closed, additive native Gateway contract; rendering has no host effects.

The qualified binary owns SQLite migrations. Its historical install scripts
are deliberately not executors for this transaction (global paths and restart).
"""
from pathlib import Path
from copy import deepcopy
import re

from installer.gateway_identity import profile as identity_profile, public_identity
from installer.gateway_release import release, sha, FCM_COMMIT
from installer.model import ErrorCode, canonical_bytes, exact_keys, require
from installer.service_identity import ServiceIdentity
from installer import mobile_web_source as mobile
from installer import fcm_credentials as fcm

PORT = 9083


class GatewayServiceProfile:
    def __init__(self, foundation, identity, key_directory, *, release_commit=None, push=None):
        identity_profile(identity)
        self.foundation = foundation
        self.dev = foundation.dev
        self.web = foundation.web
        self.identity = dict(identity)
        self.main = public_identity('main', foundation.identity['public_jwk'])
        require(foundation.identity == self.main, ErrorCode.INCOMPATIBLE_STATE)
        if self.dev is not None:
            require(identity['dev_enabled'] is True, ErrorCode.INCOMPATIBLE_STATE)
            self.dev.target.separate(self.web)
            require(self.dev.identity == public_identity('dev', self.dev.identity['public_jwk'])
                    and self.dev.identity['thumbprint'] != self.main['thumbprint'], ErrorCode.INCOMPATIBLE_STATE)
        if self.web.spec.source_commit == mobile.COMMIT:
            require(foundation.public_origin == identity['public_origin'], ErrorCode.INCOMPATIBLE_STATE)
        self.root = self.web.spec.root.parent / 'gateway-service'
        self.unit = 'hestia-' + self.web.spec.instance + '-gateway.service'
        self.key_directory = Path(key_directory)
        self.selected_release = release(release_commit)
        self.push = deepcopy(push)
        if push is not None:
            require(self.selected_release['commit'] == FCM_COMMIT, ErrorCode.INCOMPATIBLE_STATE)
            exact_keys(push, {'selection', 'receipt'})
            require(push == fcm.public_binding(push['selection'], push['receipt']), ErrorCode.INVALID_STATE)
        # systemd specifiers, whitespace and quoting may never enter directives.
        for path in (self.root, self.key_directory, self.web.spec.maintenance_directory):
            require(type(path) is type(Path()) and path.is_absolute()
                    and str(path) == str(Path(str(path))) and '..' not in path.parts
                    and re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)) is not None
                    and len(str(path)) <= 240, ErrorCode.INVALID_DATA)
        instance = sha(canonical_bytes({'role': 'gateway-service-v1', 'web': self.web.spec.instance,
                                       'gateway': identity['instance']}))[:32]
        self.account = ServiceIdentity(instance)
        require(self.account.user != self.web.spec.service_user, ErrorCode.INCOMPATIBLE_STATE)
        self.binary = self.root / 'hestia-mobile-gateway'
        self.config = self.root / 'config.json'
        self.state = self.root / 'state'

    @classmethod
    def from_binding(cls, foundation, binding):
        require(type(binding) is dict and type(binding.get('release')) is dict, ErrorCode.INVALID_STATE)
        if binding.get('version') == 2:
            if foundation.dev is not None:
                require(binding.get('dev') == {'target': foundation.dev.target.value, 'identity': foundation.dev.identity}, ErrorCode.SOURCE_DRIFT)
            from installer.foundation_runtime import FoundationRuntime
            foundation = FoundationRuntime(foundation.activation, binding['main'],
                public_origin=foundation.public_origin, dev=binding['dev'])
        result = cls(foundation, binding['gateway_identity'], binding['key_directory'],
            release_commit=binding['release']['commit'], push=binding.get('push'))
        require(result.binding() == binding, ErrorCode.SOURCE_DRIFT)
        return result

    def configuration(self):
        # The optional DEV backend is bound to a separately verified Web.
        # A prepared key alone never creates a backend or a fallback.
        value = {'schema_version': 1, 'listen': '127.0.0.1:9083',
                'public_origin': self.identity['public_origin'], 'state_dir': str(self.state),
                'contexts': {'mode': 'public-contexts-distribution', 'main': {
                    'endpoint': 'http://127.0.0.1:9082', 'kid': self.main['kid'],
                    'key_file': '/run/credentials/' + self.unit + '/main-key'}}}
        if self.dev is not None:
            value['contexts']['dev'] = {'endpoint': 'http://127.0.0.1:9081', 'kid': self.dev.identity['kid'],
                'key_file': '/run/credentials/' + self.unit + '/dev-key'}
        if self.push is not None:
            value.update(project_push_credential='/run/credentials/' + self.unit + '/project-push',
                         project_push_project_id=self.push['selection']['project_id'])
        return value

    def unit_bytes(self):
        dev_credential = '' if self.dev is None else f'LoadCredential=dev-key:{self.key_directory}/dev.pem\n'
        after = self.foundation.unit + ('' if self.dev is None else ' ' + self.dev.unit)
        description = 'MAIN' if self.dev is None else 'MAIN and DEV'
        credential = '' if self.push is None else f'LoadCredential=project-push:{self.key_directory.parent}/fcm/server.json\n'
        push_check = '' if self.push is None else f'ExecStartPre={self.binary} --config {self.config} --check-push-credential\n'
        return f'''[Unit]
Description=HESTIA private Mobile Gateway {description}
After={after}
ConditionPathExists=!{self.web.spec.maintenance_directory}/maintenance.attempt
[Service]
Type=exec
User={self.account.user}
Group={self.account.user}
ExecStartPre={self.binary} --config {self.config} --check-config
{push_check}ExecStart={self.binary} --config {self.config}
WorkingDirectory={self.state}
LoadCredential=main-key:{self.key_directory}/main.pem
{dev_credential}{credential}UMask=0077
Restart=no
KillMode=control-group
SendSIGKILL=yes
Delegate=no
TimeoutStartSec=20
TimeoutStopSec=15
KillSignal=SIGTERM
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_UNIX
RestrictNamespaces=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
ReadWritePaths={self.state}
InaccessiblePaths=-/var/www -/var/lib/mysql -/var/lib/mariadb
LimitCORE=0
LimitNOFILE=4096
TasksMax=128
MemoryMax=256M
CPUQuota=100%
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hestia-mobile-gateway
'''.encode()

    def binding(self):
        selected = self.selected_release
        value = {'version': 1, 'web_instance': self.web.spec.instance,
                'gateway_identity': self.identity, 'main': self.main,
                'key_directory': str(self.key_directory), 'service_instance': self.account.instance,
                'release': {k: selected[k] for k in ('commit', 'version', 'sqlite_schema', 'binary_sha256', 'package_sha256')},
                'configuration_sha256': sha(canonical_bytes(self.configuration())),
                'unit_sha256': sha(self.unit_bytes())}
        if self.dev is not None:
            value['version'] = 2
            value['dev'] = {'target': deepcopy(self.dev.target.value), 'identity': deepcopy(self.dev.identity)}
        if self.push is not None: value['push'] = deepcopy(self.push)
        return value
