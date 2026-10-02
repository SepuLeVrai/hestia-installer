"""Pure contracts for shared listeners; no account, process or file effects."""
import json
import unittest
from unittest.mock import patch

from installer import shared_mobile_tls as s
from installer.model import canonical_bytes
from test_public_tls import profile


def gateway():
    return {'version': 1, 'instance': 'f' * 32, 'public_origin': 'https://mobile.hestia.test', 'dev_enabled': False}


class SharedMobileTLSContracts(unittest.TestCase):
    def candidate(self): return s.SharedMobileTLS(profile(), gateway(), ('127.0.0.10/32',))

    def test_collision_and_noncanonical_gateway_origins_fail_closed(self):
        web = profile()
        for origin in ('https://hestia.example.test', 'https://MOBILE.hestia.test',
                       'https://mobile.hestia.test:443', 'http://mobile.hestia.test',
                       'https://mobile.hestia.test/path', 'https://127.0.0.1'):
            with self.subTest(origin=origin), self.assertRaises(Exception):
                s.SharedMobileTLS(web, {**gateway(), 'public_origin': origin}, ('127.0.0.10/32',))

    def test_input_drafts_and_returned_profiles_cannot_mutate_candidate(self):
        web, identity = profile(), gateway()
        candidate = s.SharedMobileTLS(web, identity, ('127.0.0.10/32',)); before = candidate.manifest()
        web['choices']['email'] = 'later@example.test'; identity['public_origin'] = 'https://later.example.test'
        candidate.web.value['choices']['networks'].append('198.51.100.0/24')
        self.assertEqual(candidate.manifest(), before)

    def test_challenge_stage_keeps_web_tls_identical_and_mobile_closed(self):
        c = self.candidate()
        self.assertEqual(c.nginx('https', mobile_ready=False), c.web.nginx('https'))
        http = c.nginx('http', mobile_ready=False)
        self.assertTrue(http.startswith(c.web.nginx('http')[:-2]))
        self.assertIn(b'location / { return 503; }', http)
        self.assertNotIn(b'127.0.0.1:9083', http)

    def test_ready_preserves_all_web_directives_except_added_sni_guard(self):
        c = self.candidate(); live = c.acme_root / 'live' / s.CERT_NAME
        extra = c.mobile.nginx_server(certificate=live/'fullchain.pem', private_key=live/'privkey.pem').encode()
        guard = b'  if ($ssl_server_name != hestia.example.test) { return 421; }\n'
        self.assertEqual(c.nginx('https', mobile_ready=True).replace(guard, b'').replace(extra, b''), c.web.nginx('https'))
        self.assertEqual(c.web.apache_include(), s.Profile(profile()).apache_include())

    def test_domains_certificates_challenge_roots_and_upstreams_are_distinct(self):
        c = self.candidate(); http = c.nginx('http', mobile_ready=True); tls = c.nginx('https', mobile_ready=True)
        for name in ('hestia-web', 'hestia-mobile'): self.assertIn(('/live/' + name + '/fullchain.pem').encode(), tls)
        self.assertIn(('root ' + str(c.web.public / 'htdocs') + ';').encode(), http)
        self.assertIn(('root ' + str(c.public / 'htdocs') + ';').encode(), http)
        self.assertIn(b'proxy_bind 127.0.0.2;', tls); self.assertIn(b'proxy_bind 127.0.0.3;', tls)
        self.assertIn(b'allow 192.0.2.0/24;', tls); self.assertIn(b'allow 127.0.0.10/32;', tls)

    def test_mobile_http01_remains_public_and_canonical_with_closed_methods(self):
        text = self.candidate().http_server(ready=True)
        self.assertNotIn('allow ', text); self.assertNotIn('proxy_pass', text)
        self.assertIn('[A-Za-z0-9_-]{22,128}$', text); self.assertIn('$request_uri !~', text)
        self.assertIn('^(GET|HEAD)$', text); self.assertIn('return 308 https://mobile.hestia.test$request_uri;', text)

    def test_closed_role_state_and_networks_reject_ambiguous_values(self):
        c = self.candidate()
        for role, ready in (('other', True), ('https', 1), ('http', 'yes')):
            with self.assertRaises(Exception): c.nginx(role, mobile_ready=ready)
        for networks in (['127.0.0.10/32'], ('::/0',), ('127.0.0.10/8',), ()):
            with self.assertRaises(Exception): s.SharedMobileTLS(profile(), gateway(), networks)

    def test_certbot_arguments_are_isolated_fixed_and_without_plugins_or_hooks(self):
        c = self.candidate()
        for args in (c.certbot(), c.certbot(renew=True), c.certbot(renew=True, dry_run=True)):
            self.assertIn(str(c.acme_root), args); self.assertIn('hestia-mobile', args)
            self.assertNotIn(str(c.web.acme_root), args); self.assertIn('--no-directory-hooks', args)
            for forbidden in ('--nginx', '--apache', '--force-renewal', '--deploy-hook'): self.assertNotIn(forbidden, args)
        self.assertIn(s.PRODUCTION, c.certbot()); self.assertIn(s.STAGING, c.certbot(renew=True, dry_run=True))
        self.assertIn('--no-random-sleep-on-renew', c.certbot(renew=True))
        with self.assertRaises(Exception): c.certbot(dry_run=True)

    def test_manifest_binds_parents_networks_stage_bytes_and_gateway_pin(self):
        c = self.candidate(); manifest = c.manifest()
        self.assertEqual(json.loads(canonical_bytes(manifest)), manifest); self.assertFalse(manifest['deployed'])
        self.assertEqual(manifest['gateway_profile_sha256'], s.f._sha(canonical_bytes(gateway())))
        for stage, ready in (('challenge', False), ('ready', True)):
            for role in ('http', 'https'):
                self.assertEqual(manifest['configurations'][stage][role], s.f._sha(c.nginx(role, mobile_ready=ready)))
        other = s.SharedMobileTLS(profile(), gateway(), ('0.0.0.0/0',))
        self.assertNotEqual(other.manifest()['configurations']['ready']['https'], manifest['configurations']['ready']['https'])

    def test_compilation_and_commands_never_probe_or_mutate_the_host(self):
        # Build the source-hash fixture first, then forbid all effects during compilation.
        web = profile()
        with patch('subprocess.run', side_effect=AssertionError('process')), \
             patch('builtins.open', side_effect=AssertionError('file')), \
             patch('pathlib.Path.open', side_effect=AssertionError('path')), \
             patch('socket.socket', side_effect=AssertionError('network')):
            candidate = s.SharedMobileTLS(web, gateway(), ('127.0.0.10/32',))
            self.assertFalse(candidate.manifest()['deployed'])
