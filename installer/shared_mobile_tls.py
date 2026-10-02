"""Pure composition for the owned Web and Mobile public listeners.

This is a candidate compiler, not an ownership transfer. In particular its
output must never overwrite a Phase 5 frozen bundle or bypass its unit guards.
The future lifecycle must verify the frozen parent, enroll its own immutable
receipt and take responsibility for both renewal paths before using it.
"""
import json

from installer.gateway_identity import profile as gateway_profile
from installer.mobile_ingress import MobileIngress
from installer.model import canonical_bytes, require
from installer.public_tls_profile import Profile, PRODUCTION, STAGING, f

CERT_NAME = 'hestia-mobile'


class SharedMobileTLS:
    def __init__(self, web_profile, gateway, client_networks):
        # Snapshot inputs: a later edit to a cockpit draft cannot change output.
        self._web = canonical_bytes(web_profile)
        self._gateway = canonical_bytes(gateway)
        web = self.web
        identity = gateway_profile(json.loads(self._gateway))
        self.mobile = MobileIngress(identity['public_origin'][8:], client_networks)
        require(self.mobile.hostname != web.hostname)

    @property
    def web(self): return Profile(json.loads(self._web))

    @property
    def public(self): return self.web.public / 'mobile'

    @property
    def root(self): return self.public / 'private'

    @property
    def acme_root(self): return self.root / 'letsencrypt'

    def http_server(self, *, ready):
        require(type(ready) is bool)
        host = self.mobile.hostname
        response = '308 https://' + host + '$request_uri' if ready else '503'
        # HTTP-01 is deliberately public, independently of the Mobile allowlist.
        # Only canonical base64url token paths are served; no query or traversal.
        return f'''server {{
  listen 0.0.0.0:80;
  server_name {host};
  access_log off;
  error_log /dev/null crit;
  client_max_body_size 1;
  if ($http_host != {host}) {{ return 421; }}
  if ($request !~ "^[A-Z]+ /") {{ return 400; }}
  if ($request_method !~ "^(GET|HEAD)$") {{ return 405; }}
  if ($http_transfer_encoding != "") {{ return 400; }}
  if ($http_content_length !~ "^(0)?$") {{ return 400; }}
  location ^~ /.well-known/acme-challenge/ {{
    if ($request_uri !~ "^/\\.well-known/acme-challenge/[A-Za-z0-9_-]{{22,128}}$") {{ return 404; }}
    root {self.public}/htdocs;
    default_type text/plain;
    try_files $uri =404;
  }}
  location / {{ return {response}; }}
}}
'''

    def nginx(self, role, *, mobile_ready):
        require(role in ('http', 'https') and type(mobile_ready) is bool)
        web = self.web
        original = web.nginx(role)
        require(original.endswith(b'}\n'))
        if role == 'http':
            extra = self.http_server(ready=mobile_ready)
        elif not mobile_ready:
            # The certificate is not available yet: no Mobile TLS server at all.
            return original
        else:
            # Preserve every Web directive and add only this reciprocal SNI
            # guard. Otherwise SNI=Mobile/Host=Web would enter the Web vhost.
            anchor = ('  server_name ' + web.hostname + ';\n').encode()
            require(original.count(anchor) == 1)
            guard = ('  if ($ssl_server_name != ' + web.hostname + ') { return 421; }\n').encode()
            original = original.replace(anchor, anchor + guard)
            live = self.acme_root / 'live' / CERT_NAME
            extra = self.mobile.nginx_server(certificate=live / 'fullchain.pem', private_key=live / 'privkey.pem')
        return original[:-2] + extra.encode() + b'}\n'

    def certbot(self, *, renew=False, dry_run=False):
        require(type(renew) is bool and type(dry_run) is bool and (renew or not dry_run))
        argv = ['/usr/bin/certbot', '--config', str(self.root / 'certbot.ini'),
            '--config-dir', str(self.acme_root), '--work-dir', str(self.root / 'certbot-work'),
            '--logs-dir', str(self.root / 'certbot-logs'), '--non-interactive',
            '--no-directory-hooks', '--cert-name', CERT_NAME]
        if renew:
            argv += ['renew', '--no-random-sleep-on-renew']
            if dry_run: argv += ['--dry-run', '--server', STAGING]
        else:
            argv += ['certonly', '--webroot', '-w', str(self.public / 'htdocs'), '-d', self.mobile.hostname,
                '--email', self.web.value['choices']['email'], '--agree-tos', '--key-type', 'ecdsa',
                '--elliptic-curve', 'secp256r1', '--server', PRODUCTION]
        return argv

    def manifest(self):
        """Non-secret deterministic candidate identity, never a live receipt."""
        return {'version': 1, 'kind': 'shared-mobile-tls-candidate',
            'web_profile_sha256': self.web.digest, 'gateway_profile_sha256': f._sha(self._gateway),
            'mobile': self.mobile.contract(), 'mobile_public_root': str(self.public),
            'certificate_name': CERT_NAME,
            'original_web': {role: f._sha(self.web.nginx(role)) for role in ('http', 'https')},
            'configurations': {stage: {role: f._sha(self.nginx(role, mobile_ready=ready))
                for role in ('http', 'https')} for stage, ready in (('challenge', False), ('ready', True))},
            'commands': {stage: self.certbot(**options) for stage, options in
                (('issue', {}), ('dry_run', {'renew': True, 'dry_run': True}), ('renew', {'renew': True}))},
            'deployed': False}
