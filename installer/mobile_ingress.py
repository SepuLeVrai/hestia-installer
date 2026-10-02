"""Pure, closed NGINX Mobile boundary for the pinned Installer Gateway.

No installation, certificate issuance, service mutation or Web overlay. The
future lifecycle must bind this server block to its own immutable profile.
Client identity comes only from the direct socket peer, never from forwarding
headers. Loopback is a host trust boundary, not process authentication.
"""
from dataclasses import dataclass
import ipaddress

from installer.proxy_ingress import _host_port, _certificate_path

GATEWAY_COMMIT = 'e2c09f53593bf316906ccc4387f185e73e7f85a8'
BUSINESS = (
    'profile-get', 'profile-update', 'dashboard-get', 'activities-list', 'activity-get',
    'cockpit-get', 'workload-get', 'workload-update', 'pipeline-get', 'pipeline-update',
    'express-clients', 'express-create', 'todos-list', 'todo-update', 'roadmaps-list',
    'roadmap-get', 'roadmap-create', 'roadmap-update', 'roadmap-clients', 'roadmap-axis-create',
    'roadmap-axis-update', 'photos-list', 'photo-delete', 'photo-get', 'photo-upload',
    'profile-photo-get', 'profile-photo-upload', 'tickets-list', 'ticket-update', 'ticket-create',
    'references-list', 'reference-get', 'news-list', 'notifications-list', 'notification-read',
    'admin-users-list', 'admin-user-update', 'organisation-list', 'referentials-list',
    'referential-update', 'templates-list', 'template-update')
POST = ('/v1/enrollment/start', '/v1/enrollment/complete', '/v1/auth/challenge',
    '/v1/auth/complete', '/v1/auth/refresh', '/v1/auth/session', '/v1/auth/logout',
    '/v1/environments', '/v1/me', '/v1/project-push/register', '/v1/project-push/unregister',
    '/v1/project-push/resolve', '/v1/app-update', '/v1/app-update/download')
BOOTSTRAP = ('/mobile/bootstrap', '/mobile/bootstrap.js', '/mobile/bootstrap.css', '/mobile/bootstrap.svg')


@dataclass(frozen=True)
class Route:
    path: str
    methods: tuple[str, ...]
    body_limit: int
    origin_required: bool = False


ROUTES = tuple(sorted((
    Route('/health', ('GET',), 0),
    *(Route(path, ('POST',), 16384) for path in POST),
    *(Route('/v1/business/' + op, ('POST',), 16384 if op == 'referentials-list' else 1048576) for op in BUSINESS),
    *(Route(path, ('GET', 'HEAD'), 0) for path in BOOTSTRAP),
    *(Route('/mobile/bootstrap/' + op, ('POST',), 1024, True) for op in ('check', 'download'))),
    key=lambda route: route.path))


class MobileIngressError(ValueError): pass


def require(ok):
    if not ok: raise MobileIngressError('MOBILE_INGRESS_INPUT_REJECTED')


@dataclass(frozen=True)
class MobileIngress:
    hostname: str
    client_networks: tuple[str, ...]

    def __post_init__(self):
        try:
            _host_port(self.hostname, 9083)
            networks = self.client_networks
            require(type(networks) is tuple and 1 <= len(networks) <= 30)
            require(all(type(n) is str for n in networks) and tuple(sorted(set(networks))) == networks)
            for value in networks:
                parsed = ipaddress.ip_network(value, strict=True)
                require(type(parsed) is ipaddress.IPv4Network and str(parsed) == value)
            require('0.0.0.0/0' not in networks or networks == ('0.0.0.0/0',))
        except Exception:
            raise MobileIngressError('MOBILE_INGRESS_INPUT_REJECTED') from None

    def contract(self):
        return {'version': 1, 'gateway_commit': GATEWAY_COMMIT,
            'public_origin': 'https://' + self.hostname, 'client_networks': list(self.client_networks),
            'listen': '0.0.0.0:443', 'upstream': '127.0.0.1:9083', 'proxy_address': '127.0.0.3',
            'routes': [{'path': r.path, 'methods': list(r.methods), 'body_limit': r.body_limit,
                        'origin_required': r.origin_required} for r in ROUTES]}

    def nginx_server(self, *, certificate, private_key):
        try:
            cert, key = _certificate_path(certificate), _certificate_path(private_key)
            require(cert != key)
        except Exception:
            raise MobileIngressError('MOBILE_INGRESS_INPUT_REJECTED') from None
        server = f'''server {{
  listen 0.0.0.0:443 ssl;
  server_name {self.hostname};
  ssl_certificate {cert};
  ssl_certificate_key {key};
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_session_tickets off;
  server_tokens off;
  access_log off;
  error_log /dev/null crit;
  client_header_timeout 5s;
  client_body_timeout 10s;
  client_body_buffer_size 1m;
  client_body_in_file_only off;
  keepalive_timeout 15s;
  if ($http_host != {self.hostname}) {{ return 421; }}
  if ($ssl_server_name != {self.hostname}) {{ return 421; }}
  if ($request !~ "^[A-Z]+ /") {{ return 400; }}
  if ($http_transfer_encoding != "") {{ return 400; }}
  if ($http_content_encoding != "") {{ return 400; }}
  if ($http_upgrade != "") {{ return 400; }}
  if ($http_authorization != "") {{ return 400; }}
  if ($http_cookie != "") {{ return 400; }}
  add_header Cache-Control "no-store" always;
  add_header Referrer-Policy "no-referrer" always;
  add_header X-Content-Type-Options "nosniff" always;
''' + ''.join('  allow ' + n + ';\n' for n in self.client_networks) + '''  deny all;
  location / { return 404; }
'''
        for route in ROUTES:
            server += f'''  location = {route.path} {{
    if ($request_uri != "{route.path}") {{ return 404; }}
    if ($request_method !~ "^({'|'.join(route.methods)})$") {{ return 405; }}
'''
            if route.body_limit:
                server += f'''    client_max_body_size {route.body_limit};
    if ($http_content_length !~ "^[1-9][0-9]*$") {{ return 400; }}
    if ($http_content_type !~* "^application/json[ \\t]*(;[ \\t]*charset[ \\t]*=[ \\t]*(utf-8|\\\"utf-8\\\")[ \\t]*)?$") {{ return 400; }}
'''
            else:
                server += '''    client_max_body_size 1;
    if ($http_content_length !~ "^(0)?$") { return 400; }
    if ($http_content_type != "") { return 400; }
'''
            if route.origin_required:
                server += f'    if ($http_origin != "https://{self.hostname}") {{ return 400; }}\n'
            server += f'''    proxy_pass http://127.0.0.1:9083;
    proxy_bind 127.0.0.3;
    proxy_http_version 1.1;
    proxy_pass_request_headers off;
    proxy_set_header Host {self.hostname};
    proxy_set_header Connection "";
    proxy_set_header Content-Type $http_content_type;
    proxy_set_header Content-Length $http_content_length;
    proxy_set_header DPoP $http_dpop;
    proxy_set_header Origin $http_origin;
    proxy_set_header X-Hestia-Client-IP $remote_addr;
    proxy_request_buffering on;
    proxy_buffering off;
    proxy_max_temp_file_size 0;
    proxy_cache off;
    proxy_next_upstream off;
    proxy_intercept_errors off;
    proxy_redirect off;
    proxy_ignore_headers X-Accel-Redirect X-Accel-Expires Expires Cache-Control Set-Cookie Vary X-Accel-Limit-Rate X-Accel-Buffering X-Accel-Charset;
    proxy_hide_header Set-Cookie;
    proxy_hide_header Cache-Control;
    proxy_hide_header Referrer-Policy;
    proxy_hide_header X-Content-Type-Options;
    proxy_connect_timeout 2s;
    proxy_read_timeout {90 if route.path == '/mobile/bootstrap/download' else 35}s;
    proxy_send_timeout 10s;
    send_timeout 90s;
  }}
'''
        return server + '}\n'
