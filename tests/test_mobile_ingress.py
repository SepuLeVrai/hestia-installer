import hashlib
import json
from pathlib import Path
import re
import unittest

from installer.gateway_release import release
from installer.mobile_ingress import MobileIngress, MobileIngressError, ROUTES, BUSINESS, GATEWAY_COMMIT


class MobileIngressTests(unittest.TestCase):
    def setUp(self):
        self.policy = MobileIngress('mobile.hestia.test', ('192.0.2.0/24',))
        self.fixture = json.loads((Path(__file__).parent/'fixtures/gateway-ingress-contract.json').read_text())

    def render(self, **changes):
        return self.policy.nginx_server(**({'certificate': Path('/etc/hestia/fullchain.pem'),
            'private_key': Path('/etc/hestia/privkey.pem')} | changes))

    def test_contract_matches_pinned_gateway_source_and_release(self):
        self.assertEqual(self.fixture['commit'], GATEWAY_COMMIT)
        self.assertEqual(release()['commit'], GATEWAY_COMMIT)
        for row in self.fixture['sources'].values():
            data = row['content'].encode()
            self.assertEqual(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(), row['blob'])

    def test_every_gateway_route_is_explicit_without_wildcard(self):
        src = {k: v['content'] for k,v in self.fixture['sources'].items()}
        operations = set(re.findall(r'"([a-z-]+)":\s+true', src['internal/backend/operations.go']))
        self.assertEqual(operations, set(BUSINESS)); self.assertEqual(len(operations), 42)
        literals = set(re.findall(r'"(/(?:health|v1/[a-z/-]+|mobile/[a-z/.]+))"',
            '\n'.join(src[n] for n in src if n.startswith('internal/api/'))))
        literals.discard('/v1/business/')
        expected = literals | {'/v1/business/'+name for name in operations}
        self.assertEqual({r.path for r in ROUTES}, expected)
        self.assertEqual(len(ROUTES), 63); self.assertEqual(len(set(r.path for r in ROUTES)), 63)

    def test_methods_and_body_budgets_preserve_gateway_contract(self):
        for r in ROUTES:
            if r.path.startswith('/mobile/bootstrap/'):
                self.assertEqual((r.methods,r.body_limit,r.origin_required),(('POST',),1024,True))
            elif r.path.startswith('/mobile/'):
                self.assertEqual((r.methods,r.body_limit),(('GET','HEAD'),0))
            elif r.path == '/health': self.assertEqual((r.methods,r.body_limit),(('GET',),0))
            elif r.path.startswith('/v1/business/') and not r.path.endswith('/referentials-list'):
                self.assertEqual((r.methods,r.body_limit),(('POST',),1048576))
            else: self.assertEqual((r.methods,r.body_limit),(('POST',),16384))

    def test_rejects_hostname_injections_and_noncanonical_origins(self):
        for host in ('localhost','https://mobile.test','mobile.test:443','MOBILE.test','mobile.test.','a..test',
                     'a_test.test','mobile.test; return 200','mobile.test\ninclude x;',False,None):
            with self.subTest(host=host), self.assertRaises(MobileIngressError): MobileIngress(host, ('0.0.0.0/0',))

    def test_rejects_network_injections_duplicates_host_bits_and_ipv6(self):
        for networks in ([],(),('::/0',),('192.0.2.1/24',),('127.0.0.1',),('127.0.0.1/32;',),
                         ('0.0.0.0/0','127.0.0.1/32'),('192.0.2.0/24','192.0.2.0/24'),
                         ('198.51.100.0/24','192.0.2.0/24'),(False,)):
            with self.subTest(networks=networks),self.assertRaises(MobileIngressError): MobileIngress('mobile.test', networks)

    def test_rejects_certificate_path_injections_and_same_key_path(self):
        for path in ('/etc/a.pem',Path('/tmp/a.pem'),Path('/etc/../a.pem'),Path('/etc/a;bad'),Path('relative')):
            with self.subTest(path=path), self.assertRaises(MobileIngressError): self.render(certificate=path)
        with self.assertRaises(MobileIngressError): self.render(certificate=Path('/etc/hestia/privkey.pem'))

    def test_public_and_allowlist_policies_are_separate_from_web(self):
        self.assertEqual(self.policy.contract()['client_networks'], ['192.0.2.0/24'])
        public = MobileIngress('mobile.hestia.test', ('0.0.0.0/0',))
        self.assertEqual(public.contract()['client_networks'],['0.0.0.0/0'])
        self.assertIn('  allow 192.0.2.0/24;\n  deny all;',self.render())
        self.assertNotIn('127.0.0.2',self.render())

    def test_render_is_deterministic_and_routes_have_fixed_upstream(self):
        self.assertEqual(self.render(), self.render()); self.assertEqual(self.render().count('location = '),63)
        self.assertEqual(self.render().count('proxy_pass http://127.0.0.1:9083;'),63)
        for port in (9080,9081,9082): self.assertNotIn(':'+str(port),self.render())
        self.assertIn('location / { return 404; }',self.render())

    def test_no_generic_forwarded_header_or_authentication_passthrough(self):
        text=self.render();self.assertEqual(text.count('proxy_pass_request_headers off;'),63)
        self.assertNotIn('proxy_add_x_forwarded_for',text)
        self.assertIn('proxy_set_header X-Hestia-Client-IP $remote_addr;',text)
        for name in ('Forwarded','X-Real-IP','Authorization','Cookie'):
            self.assertNotIn('proxy_set_header '+name+' ',text)

    def test_no_retry_cache_response_spooling_or_raw_request_log(self):
        text=self.render()
        for directive in ('proxy_next_upstream off;','proxy_cache off;','proxy_buffering off;',
                          'proxy_max_temp_file_size 0;','access_log off;','error_log /dev/null crit;'):
            self.assertIn(directive,text)
        self.assertNotIn('log_format',text);self.assertNotIn('include ',text)

    def test_download_has_distinct_bounded_timeout_only_on_bootstrap(self):
        self.assertEqual(self.render().count('proxy_read_timeout 90s;'),1)
        self.assertEqual(self.render().count('proxy_read_timeout 35s;'),62)

    def test_contract_returns_independent_serializable_values(self):
        value=self.policy.contract();json.dumps(value);value['routes'].clear();value['client_networks'].clear()
        self.assertEqual(len(self.policy.contract()['routes']),63)
        self.assertEqual(self.policy.client_networks,('192.0.2.0/24',))
