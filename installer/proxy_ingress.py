"""Closed IPv4 loopback TLS ingress interface; no frontend installation.

Apache consumes the forwarding header once, before its client authorization.
The Web receives canonical REMOTE_ADDR/HTTPS and no forwarding headers.
The host and the single local proxy are trusted; IP binding is not process auth.
"""
from dataclasses import dataclass, field
import ipaddress
from pathlib import Path
import re


class ProxyIngressError(ValueError):
    """Closed, non-secret input diagnostic."""


def require(ok):
    if not ok: raise ProxyIngressError('PROXY_INGRESS_INPUT_REJECTED')


def _host_port(hostname, port):
    require(type(hostname) is str and len(hostname) <= 253 and '.' in hostname
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in hostname.split('.')))
    require(type(port) is int and 1024 <= port <= 65535)


def _certificate_path(path):
    require(isinstance(path, Path))
    value = str(path)
    require(len(value) <= 220 and re.fullmatch(r'/(?:etc|var/lib)/[A-Za-z0-9_./-]+', value)
            and '..' not in path.parts)
    return value


@dataclass(frozen=True)
class ProxyIngress:
    proxy_address: str = field(repr=False)
    client_networks: tuple[str, ...] = field(repr=False)

    def __post_init__(self):
        try:
            require(type(self.proxy_address) is str)
            address = ipaddress.IPv4Address(self.proxy_address)
            require(address.is_loopback and str(address) == self.proxy_address
                    and self.proxy_address not in ('127.0.0.0', '127.0.0.1', '127.255.255.255'))
            require(type(self.client_networks) is tuple and 1 <= len(self.client_networks) <= 32)
            require(all(type(x) is str for x in self.client_networks))
            require(len(set(self.client_networks)) == len(self.client_networks))
            for value in self.client_networks:
                network = ipaddress.ip_network(value, strict=True)
                require(str(network) == value)
        except Exception:
            raise ProxyIngressError('PROXY_INGRESS_INPUT_REJECTED') from None

    def apache_access(self, hostname, port):
        _host_port(hostname, port)
        # Never infer proxy trust from the client allowlist. A missing/invalid
        # forwarding address leaves REMOTE_ADDR equal to the peer and is denied.
        return f'''  <RequireAll>
    Require expr "%{{HTTP_HOST}} == '{hostname}'"
    Require expr "%{{CONN_REMOTE_ADDR}} == '{self.proxy_address}'"
    Require expr "%{{REMOTE_ADDR}} != %{{CONN_REMOTE_ADDR}}"
    Require expr "req('X-Forwarded-Proto') == 'https'"
    Require ip {' '.join(self.client_networks)}
  </RequireAll>
'''

    def apache_directives(self):
        # Keep the authorization inputs available to DirectoryIndex internal
        # redirects. Remove them only at the FastCGI boundary, not from Apache's
        # request headers. PHP must never interpret a second forwarding chain.
        return f'''RemoteIPHeader X-Forwarded-For
RemoteIPInternalProxy {self.proxy_address}/32
ProxyFCGISetEnvIf "true" !HTTP_FORWARDED
ProxyFCGISetEnvIf "true" !HTTP_X_FORWARDED_FOR
ProxyFCGISetEnvIf "true" !HTTP_X_FORWARDED_PROTO
ProxyFCGISetEnvIf "true" !HTTP_X_FORWARDED_HOST
ProxyFCGISetEnvIf "true" !HTTP_X_FORWARDED_PORT
ProxyFCGISetEnvIf "true" !HTTP_X_REAL_IP
ProxyFCGISetEnvIf "true" HTTPS "on"
'''

    def nginx_server(self, hostname, backend_port, *, tls_port, certificate, private_key, listen_address='0.0.0.0'):
        """Pure server block for a dedicated frontend, to validate before use.

        The caller owns certificates, service lifecycle, configtest and rollback.
        No inherited real_ip/header rewrite/includes are part of this contract.
        """
        _host_port(hostname, backend_port)
        require(type(tls_port) is int and 1 <= tls_port <= 65535 and tls_port != backend_port)
        try:
            require(type(listen_address) is str and str(ipaddress.IPv4Address(listen_address)) == listen_address)
        except Exception:
            raise ProxyIngressError('PROXY_INGRESS_INPUT_REJECTED') from None
        cert, key = _certificate_path(certificate), _certificate_path(private_key)
        require(cert != key)
        return f'''server {{
  listen {listen_address}:{tls_port} ssl;
  server_name {hostname};
  ssl_certificate {cert};
  ssl_certificate_key {key};
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_session_tickets off;
  if ($host != {hostname}) {{ return 421; }}
  client_max_body_size 66m;
''' + ''.join('  allow ' + x + ';\n' for x in self.client_networks) + f'''  deny all;
  location / {{
    proxy_bind {self.proxy_address};
    proxy_pass http://127.0.0.1:{backend_port};
    proxy_http_version 1.1;
    proxy_set_header Host {hostname};
    proxy_set_header Connection "";
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Forwarded "";
    proxy_set_header X-Forwarded-Host "";
    proxy_set_header X-Forwarded-Port "";
    proxy_set_header X-Real-IP "";
    proxy_connect_timeout 5s;
    proxy_read_timeout 35s;
    proxy_send_timeout 35s;
  }}
}}
'''
