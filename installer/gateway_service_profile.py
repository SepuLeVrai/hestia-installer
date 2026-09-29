"""Closed, additive native Gateway contract; rendering has no host effects.

The qualified binary owns SQLite migrations. Its historical install scripts
are deliberately not executors for this transaction (global paths and restart).
"""
from pathlib import Path
import re

from installer.gateway_identity import profile as identity_profile, public_identity
from installer.gateway_release import release, sha
from installer.model import ErrorCode, canonical_bytes, require
from installer.service_identity import ServiceIdentity

PORT = 9083


class GatewayServiceProfile:
    def __init__(self, foundation, identity, key_directory):
        identity_profile(identity)
        self.foundation = foundation
        self.web = foundation.web
        self.identity = dict(identity)
        self.main = public_identity('main', foundation.identity['public_jwk'])
        require(foundation.identity == self.main, ErrorCode.INCOMPATIBLE_STATE)
        self.root = self.web.spec.root.parent / 'gateway-service'
        self.unit = 'hestia-' + self.web.spec.instance + '-gateway.service'
        self.key_directory = Path(key_directory)
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

    def configuration(self):
        # Prepared DEV keys are preserved, but MAIN is the only native backend
        # qualified here. No DEV endpoint or fallback is fabricated.
        return {'schema_version': 1, 'listen': '127.0.0.1:9083',
                'public_origin': self.identity['public_origin'], 'state_dir': str(self.state),
                'contexts': {'mode': 'public-contexts-distribution', 'main': {
                    'endpoint': 'http://127.0.0.1:9082', 'kid': self.main['kid'],
                    'key_file': '/run/credentials/' + self.unit + '/main-key'}}}

    def unit_bytes(self):
        return f'''[Unit]
Description=HESTIA private Mobile Gateway MAIN
After={self.foundation.unit}
ConditionPathExists=!{self.web.spec.maintenance_directory}/maintenance.attempt
[Service]
Type=exec
User={self.account.user}
Group={self.account.user}
ExecStartPre={self.binary} --config {self.config} --check-config
ExecStart={self.binary} --config {self.config}
WorkingDirectory={self.state}
LoadCredential=main-key:{self.key_directory}/main.pem
UMask=0077
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
        selected = release()
        return {'version': 1, 'web_instance': self.web.spec.instance,
                'gateway_identity': self.identity, 'main': self.main,
                'key_directory': str(self.key_directory), 'service_instance': self.account.instance,
                'release': {k: selected[k] for k in ('commit', 'version', 'sqlite_schema', 'binary_sha256', 'package_sha256')},
                'configuration_sha256': sha(canonical_bytes(self.configuration())),
                'unit_sha256': sha(self.unit_bytes())}
