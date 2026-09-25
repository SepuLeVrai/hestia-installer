import dataclasses
from pathlib import Path
from types import SimpleNamespace
import unittest

from installer import http_runtime as h
from installer.proxy_ingress import ProxyIngress, ProxyIngressError


class ProxyIngressTests(unittest.TestCase):
    def setUp(self):
        self.policy = ProxyIngress('127.0.0.2', ('192.0.2.0/24', '2001:db8::/32'))
        self.spec = h.RuntimeSpec('a' * 32, Path('/var/lib/hestia-proxy-test'), Path('/srv/web-test'),
                                  'web-test', 'hestia.test', 8123, '8.4', self.policy)

    def test_trusted_peer_is_one_canonical_dedicated_loopback_address(self):
        for value in ('0.0.0.0', '192.0.2.1', '::1', '127.0.0.1', '127.0.0.0', '127.255.255.255',
                      '127.0.0.2/32', '127.00.0.2', 'localhost', '127.0.0.2\n', None, True):
            with self.subTest(value=value), self.assertRaises(ProxyIngressError):
                ProxyIngress(value, self.policy.client_networks)

    def test_client_networks_are_explicit_canonical_bounded_and_not_proxy_inputs(self):
        for value in ((), [], ('192.0.2.1/24',), ('192.0.2.1',), ('ALL',), ('0/0',),
                      ('2001:DB8::/32',), ('192.0.2.0/24\n',), (None,), ('0.0.0.0/0',) * 2,
                      tuple('10.' + str(x) + '.0.0/16' for x in range(33))):
            with self.subTest(value=value), self.assertRaises(ProxyIngressError):
                ProxyIngress('127.0.0.2', value)
        self.assertEqual(ProxyIngress('127.0.0.2', ('0.0.0.0/0', '::/0')).proxy_address, '127.0.0.2')

    def test_runtime_rejects_untyped_or_mutable_policy(self):
        for value in ({'proxy_address': '127.0.0.2'}, True, '127.0.0.2'):
            with self.assertRaises(h.HttpRuntimeError): dataclasses.replace(self.spec, ingress=value)
        with self.assertRaises(dataclasses.FrozenInstanceError): self.policy.proxy_address = '127.0.0.3'

    def render(self, **changes):
        args = dict(tls_port=8443, certificate=Path('/etc/hestia/tls.crt'), private_key=Path('/etc/hestia/tls.key'))
        return self.policy.nginx_server('hestia.test', 8123, **(args | changes))

    def test_frontend_rejects_interpolation_paths_ports_and_ambiguous_certificates(self):
        cases = [('tls_port', True), ('tls_port', 0), ('tls_port', 65536), ('tls_port', 8123),
                 ('listen_address', 'localhost'), ('listen_address', '127.0.0.1;'),
                 ('certificate', Path('/tmp/cert')), ('certificate', Path('/etc/a/../cert')),
                 ('certificate', Path('/etc/$name')), ('private_key', Path('/etc/hestia/tls.crt')),
                 ('certificate', '/etc/cert'), ('certificate', Path('/etc/a\ninclude'))]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ProxyIngressError): self.render(**{key: value})
        for host in ('a.test\n', 'a.test;', '*.test', 'localhost'):
            with self.assertRaises(ProxyIngressError): self.policy.apache_access(host, 8123)

    def test_frontend_replaces_untrusted_chain_and_uses_actual_backend_port(self):
        value = self.render()
        self.assertIn('proxy_pass http://127.0.0.1:8123;', value)
        self.assertIn('proxy_bind 127.0.0.2;', value)
        self.assertIn('X-Forwarded-For $remote_addr;', value)
        self.assertNotIn('$proxy_add_x_forwarded_for', value)
        self.assertNotIn('real_ip', value)
        self.assertNotIn('include ', value)
        self.assertIn('deny all;', value)

    def test_policy_change_changes_staged_bytes_and_modules_without_mutating_legacy_profile(self):
        account = SimpleNamespace(pw_gid=991)
        extension = Path('/usr/lib/php/20240924')
        runtime = h.HttpRuntime(self.spec)
        before = runtime._files(account, extension)
        changed = h.HttpRuntime(dataclasses.replace(self.spec, ingress=ProxyIngress('127.0.0.3', ('198.51.100.0/24',))))
        self.assertNotEqual(before, changed._files(account, extension))
        for text in ('remoteip', 'authz_host'): self.assertIn(text, runtime._modules())
        legacy = h.HttpRuntime(dataclasses.replace(self.spec, ingress=None))
        self.assertEqual(legacy._modules(), h.MODULES)
        old = legacy._files(account, extension)
        self.assertIn(b'env[HESTIA_TRUSTED_PROXIES] = 127.0.0.1/32', old[self.spec.root / 'conf/fpm.conf'])
        self.assertIn(b'env[HESTIA_TRUSTED_PROXIES] = ""', before[self.spec.root / 'conf/fpm.conf'])

    def test_backend_requires_peer_and_client_separately_and_canonicalizes_php(self):
        access = self.policy.apache_access('hestia.test', 8123)
        self.assertIn("CONN_REMOTE_ADDR} == '127.0.0.2'", access)
        self.assertIn('Require ip 192.0.2.0/24 2001:db8::/32', access)
        self.assertIn('REMOTE_ADDR} != %{CONN_REMOTE_ADDR}', access)
        directives = self.policy.apache_directives()
        self.assertIn('RemoteIPInternalProxy 127.0.0.2/32', directives)
        self.assertIn('ProxyFCGISetEnvIf "true" HTTPS "on"', directives)
        for header in ('Forwarded', 'X-Forwarded-For', 'X-Forwarded-Proto', 'X-Forwarded-Host', 'X-Real-IP'):
            self.assertIn('RequestHeader unset ' + header, directives)

    def test_repr_does_not_publish_network_configuration(self):
        for value in ('127.0.0.2', '192.0.2.0/24', '2001:db8::/32'):
            self.assertNotIn(value, repr(self.policy) + repr(self.spec))


if __name__ == '__main__': unittest.main()
