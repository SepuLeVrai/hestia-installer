"""Private CI ACME authority: real Pebble validation, isolated DNS and TLS proxy.

The target still calls the product's fixed Let's Encrypt URLs. Only this
network's DNS/trust redirect them to a disposable CA; no product test switch.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import struct
import subprocess
import threading
import time

ROOT = Path('/fixture'); ROOT.mkdir(mode=0o700)
CA = '172.30.85.2'; TARGET = '172.30.85.10'
PRODUCTION = 'acme-v02.api.letsencrypt.org'; STAGING = 'acme-staging-v02.api.letsencrypt.org'
subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7', '-subj', '/CN=HESTIA disposable ACME endpoint',
    '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign,digitalSignature',
    '-addext', 'subjectAltName=DNS:' + PRODUCTION + ',DNS:' + STAGING + ',DNS:localhost,IP:127.0.0.1,IP:' + CA,
    '-keyout', str(ROOT / 'endpoint.key'), '-out', str(ROOT / 'endpoint.crt')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
(ROOT / 'endpoint.key').chmod(0o600)
config = {'pebble': {'listenAddress': '0.0.0.0:14000', 'managementListenAddress': '0.0.0.0:15000',
    'certificate': str(ROOT / 'endpoint.crt'), 'privateKey': str(ROOT / 'endpoint.key'), 'httpPort': 80, 'tlsPort': 443,
    'retryAfter': {'authz': 1, 'order': 1}, 'profiles': {'default': {'description': 'Disposable final qualification', 'validityPeriod': 7776000}}}}
(ROOT / 'config.json').write_text(json.dumps(config))


def dns_response(data):
    index = 12; labels = []
    while data[index]:
        length = data[index]; index += 1; labels.append(data[index:index + length].decode()); index += length
    index += 1; kind, cls = struct.unpack('!HH', data[index:index + 4]); end = index + 4
    name = '.'.join(labels).lower(); address = TARGET if name in ('hestia.example.test', 'mobile.example.test') else CA if name in (PRODUCTION, STAGING) else None
    answer = b''
    if kind == 1 and cls == 1 and address:
        answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 10, 4) + socket.inet_aton(address)
    return data[:2] + struct.pack('!HHHHH', 0x8180, 1, 1 if answer else 0, 0, 0) + data[12:end] + answer


def dns():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); server.bind(('0.0.0.0', 53))
    while True:
        data, peer = server.recvfrom(4096)
        try:
            server.sendto(dns_response(data), peer)
        except Exception: pass


class DNSStream(socketserver.StreamRequestHandler):
    # Pebble's custom resolver deliberately uses TCP; libc also uses UDP.
    def handle(self):
        self.connection.settimeout(10)
        while prefix := self.rfile.read(2):
            size, = struct.unpack('!H', prefix); assert 12 <= size <= 4096
            data = self.rfile.read(size); assert len(data) == size
            response = dns_response(data)
            self.wfile.write(struct.pack('!H', len(response)) + response)


class Proxy(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_): pass
    def request(self):
        if self.headers.get('Host') not in (PRODUCTION, STAGING): self.send_error(400); return
        connection = http.client.HTTPSConnection('127.0.0.1', 14000, context=ssl._create_unverified_context(), timeout=30)
        try:
            path = '/dir' if self.path == '/directory' else self.path
            size = int(self.headers.get('Content-Length', '0')); assert 0 <= size <= 262144
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ('connection', 'transfer-encoding')}
            headers['X-Forwarded-Proto'] = 'https'; headers['Connection'] = 'close'
            connection.request(self.command, path, body=self.rfile.read(size) if size else None, headers=headers)
            response = connection.getresponse(); body = response.read(1048576)
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ('transfer-encoding', 'content-length', 'connection'): self.send_header(name, value)
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        finally: connection.close()
    do_GET = do_HEAD = do_POST = request


threading.Thread(target=dns, daemon=True).start()
tcp_dns = socketserver.ThreadingTCPServer(('0.0.0.0', 53), DNSStream)
threading.Thread(target=tcp_dns.serve_forever, daemon=True).start()
pebble = subprocess.Popen(['/usr/local/bin/pebble', '-config', str(ROOT / 'config.json'), '-strict', '-dnsserver', '127.0.0.1:53'],
    env={**os.environ, 'PEBBLE_VA_NOSLEEP': '1', 'PEBBLE_WFE_NONCEREJECT': '0', 'PEBBLE_AUTHZREUSE': '0'})
for _ in range(100):
    try:
        with socket.create_connection(('127.0.0.1', 14000), timeout=1): break
    except OSError: time.sleep(.1)
else: raise RuntimeError('CA not ready')
server = ThreadingHTTPServer(('0.0.0.0', 443), Proxy)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(ROOT / 'endpoint.crt', ROOT / 'endpoint.key')
server.socket = context.wrap_socket(server.socket, server_side=True)
print('DISPOSABLE_ACME_READY', flush=True)
server.serve_forever()
