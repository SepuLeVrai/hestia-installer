"""Real local OpenSSL keys, private temporary files; no host service or SQL."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from installer.gateway_identity import GatewayIdentityStore, _SPKI, _b64, public_identity, public_origin
from installer.model import InstallerError, canonical_bytes


class GatewayIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'identities'
        self.store = GatewayIdentityStore(self.root)
        self.profile = {'version': 1, 'instance': 'a' * 32,
                        'public_origin': 'https://mobile.customer.example', 'dev_enabled': True}

    def test_report_never_creates_or_probes(self):
        with patch('installer.gateway_identity.subprocess.run', side_effect=AssertionError('probe')):
            self.assertIsNone(self.store.report())
        self.assertFalse(self.root.exists())
        receipt = self.store.prepare(self.profile)
        (self.root / 'main.pem').unlink()
        with patch('installer.gateway_identity.subprocess.run', side_effect=AssertionError('probe')):
            self.assertEqual(self.store.report()['receipt'], receipt)
        with self.assertRaises(InstallerError): self.store.verify()
        with self.assertRaises(InstallerError): self.store.prepare(self.profile)
        self.assertFalse((self.root / 'main.pem').exists())

    def test_distinct_keys_real_signatures_public_only_receipt_and_rerun(self):
        receipt = self.store.prepare(self.profile)
        before = {e: (self.root / (e + '.pem')).read_bytes() for e in ('main', 'dev')}
        self.assertNotEqual(before['main'], before['dev'])
        message = b'{"aud":"hestia-mobile-main","environment":"main","jti":"synthetic-probe"}'
        signature = subprocess.run(['/usr/bin/openssl', 'dgst', '-sha256', '-sign', str(self.root / 'main.pem')],
                                   input=message, capture_output=True, check=True).stdout
        signature_file = Path(self.temporary.name) / 'signature'; signature_file.write_bytes(signature)
        for environment in ('main', 'dev'):
            identity = receipt['identities'][environment]
            self.assertEqual(identity, public_identity(environment, identity['public_jwk']))
            import base64
            jwk = identity['public_jwk']
            public = _SPKI + base64.urlsafe_b64decode(jwk['x'] + '=') + base64.urlsafe_b64decode(jwk['y'] + '=')
            pubfile = Path(self.temporary.name) / 'public.der'; pubfile.write_bytes(public)
            result = subprocess.run(['/usr/bin/openssl', 'dgst', '-sha256', '-keyform', 'DER', '-verify', str(pubfile),
                                     '-signature', str(signature_file)], input=message, capture_output=True)
            self.assertEqual(result.returncode == 0, environment == 'main')
            if environment == 'main':
                bad = subprocess.run(['/usr/bin/openssl', 'dgst', '-sha256', '-keyform', 'DER', '-verify', str(pubfile),
                                      '-signature', str(signature_file)], input=message + b'x', capture_output=True)
                self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(self.store.prepare(self.profile), receipt)
        self.assertEqual(before, {e: (self.root / (e + '.pem')).read_bytes() for e in ('main', 'dev')})
        report = canonical_bytes(self.store.report())
        self.assertNotIn(b'PRIVATE KEY', report)
        self.assertNotIn(b'"d"', report)
        for file in self.root.iterdir(): self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_main_only_and_choice_drift(self):
        self.profile['dev_enabled'] = False
        receipt = self.store.prepare(self.profile)
        self.assertEqual(set(receipt['identities']), {'main'})
        self.assertFalse((self.root / 'dev.pem').exists())
        configs = self.store.configurations()
        self.assertNotIn('dev', configs['gateway']['contexts'])
        self.assertEqual(configs['gateway']['public_origin'], self.profile['public_origin'])
        self.assertEqual(configs['gateway']['listen'], '127.0.0.1:9083')
        self.assertEqual(configs['foundation']['main']['gateway_keys'],
                         {receipt['identities']['main']['kid']: receipt['identities']['main']['public_jwk']})
        for field, value in [('public_origin', 'https://other.example'), ('dev_enabled', True), ('instance', 'b' * 32)]:
            changed = dict(self.profile); changed[field] = value
            with self.assertRaises(InstallerError): self.store.prepare(changed)
        self.assertEqual(self.store.verify(), receipt)

    def test_interrupted_creation_keeps_first_key(self):
        original = self.store._write_key
        def interrupted(fd, environment, private):
            if environment == 'dev': raise RuntimeError('interrupted')
            original(fd, environment, private)
        with patch.object(self.store, '_write_key', side_effect=interrupted):
            with self.assertRaises(RuntimeError): self.store.prepare(self.profile)
        first = (self.root / 'main.pem').read_bytes()
        self.assertIsNone(self.store.report()['receipt'])
        self.store.prepare(self.profile)
        self.assertEqual((self.root / 'main.pem').read_bytes(), first)
        self.store.verify()

    def test_partial_or_foreign_or_replaced_material_never_regenerated(self):
        self.root.mkdir(mode=0o700)
        (self.root / 'main.pem').write_bytes(b'partial'); (self.root / 'main.pem').chmod(0o600)
        with self.assertRaises(InstallerError): self.store.prepare(self.profile)
        self.assertFalse((self.root / 'profile.json').exists())
        (self.root / 'main.pem').unlink()
        receipt = self.store.prepare(self.profile)
        main = self.root / 'main.pem'; dev = self.root / 'dev.pem'
        main.write_bytes(dev.read_bytes())
        with self.assertRaises(InstallerError): self.store.prepare(self.profile)
        self.assertEqual(self.store.report()['receipt'], receipt)
        main.write_bytes(dev.read_bytes() + b'trailing')
        with self.assertRaises(InstallerError): self.store.prepare(self.profile)
        main.write_bytes(b'partial')
        with self.assertRaises(InstallerError): self.store.prepare(self.profile)
        self.assertEqual(main.read_bytes(), b'partial')

    def test_permissions_links_and_unknown_files_fail_closed(self):
        self.store.prepare(self.profile)
        key = self.root / 'main.pem'; original = key.read_bytes()
        key.chmod(0o640)
        with self.assertRaises(InstallerError): self.store.verify()
        key.chmod(0o600); key.unlink(); key.symlink_to(self.root / 'dev.pem')
        with self.assertRaises(InstallerError): self.store.verify()
        key.unlink(); key.write_bytes(original); key.chmod(0o600)
        os.link(key, self.root / 'extra.pem')
        with self.assertRaises(InstallerError): self.store.verify()
        (self.root / 'extra.pem').unlink(); (self.root / 'foreign').touch()
        with self.assertRaises(InstallerError): self.store.verify()

    def test_receipt_tamper_and_private_jwk_rejected(self):
        receipt = self.store.prepare(self.profile)
        identity = receipt['identities']['main']; jwk = dict(identity['public_jwk']); jwk['d'] = 'private'
        with self.assertRaises(InstallerError): public_identity('main', jwk)
        jwk = dict(identity['public_jwk']); jwk['x'] = _b64(bytes(32)); jwk['y'] = _b64(bytes(32))
        with self.assertRaises(InstallerError): public_identity('main', jwk)
        changed = copy.deepcopy(receipt); changed['identities']['main']['kid'] = 'foreign'
        (self.root / 'receipt.json').write_bytes(canonical_bytes(changed))
        with self.assertRaises(InstallerError): self.store.report()

    def test_pinned_web_crypto_accepts_key_and_refuses_wrong_key_kid_type_body(self):
        import hashlib
        fixture = Path(__file__).parent / 'fixtures/gateway_web_crypto.php'
        source = fixture.read_bytes()
        self.assertEqual(hashlib.sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest(),
                         '3f7e589d3cb1ae37f0ad942b8811712ef69220e6')
        receipt = self.store.prepare(self.profile)
        payload = {'identities': receipt['identities'], 'private': (self.root / 'main.pem').read_text()}
        result = subprocess.run(['/usr/bin/php', str(fixture.with_name('gateway_identity_interop.php'))],
                                input=canonical_bytes(payload), capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'Pinned Web cryptographic contract failed')
        self.assertEqual(result.stdout, b'GATEWAY_IDENTITY_PINNED_WEB_CRYPTO_PASS\n')
        self.assertEqual(result.stderr, b'')

    def test_strict_origin_and_profile_before_mutation(self):
        for value in ['http://mobile.example', 'https://localhost', 'https://127.0.0.1', 'https://a.test:443',
                      'https://a.test/', 'https://user@a.test', 'https://A.test', 'https://a.test.',
                      'https://a.test?x', 'https://a.test#x', 'https://a..test', 'https://a-.test', 'https://é.test']:
            with self.assertRaises(InstallerError): public_origin(value)
        self.assertEqual(public_origin('https://mobile.example.test'), 'https://mobile.example.test')
        changed = dict(self.profile); changed['dev_enabled'] = 1
        with self.assertRaises(InstallerError): self.store.prepare(changed)
        self.assertFalse(self.root.exists())


if __name__ == '__main__': unittest.main()
