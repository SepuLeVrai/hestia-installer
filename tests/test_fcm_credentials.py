"""Private import with real temporary files and local OpenSSL; no host effects."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installer import fcm_credentials as f
from installer.model import InstallerError, canonical_bytes


class FcmCredentialsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = subprocess.run(['/usr/bin/openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
            capture_output=True, check=True, timeout=30).stdout.decode()

    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'fcm'; self.store = f.FcmCredentials(self.root)
        self.profile = {'version': 1, 'gateway_plan_sha256': 'a' * 64, 'project_id': 'hestia-test'}
        self.account = {'type': 'service_account', 'project_id': 'hestia-test',
            'client_email': 'sender@credential-project.iam.gserviceaccount.com', 'private_key': self.key,
            'token_uri': 'https://oauth2.googleapis.com/token'}

    def raw(self): return json.dumps(self.account).encode()
    def plan(self): return self.store.plan(self.profile)
    def apply(self): return self.store.import_bytes(self.raw(), self.plan()['confirmation'], confirmed=True)

    def test_historical_reads_never_create_read_a_key_or_spawn(self):
        with patch.object(f, '_openssl', side_effect=AssertionError('process')):
            self.assertIsNone(self.store.report()); self.assertFalse(self.root.exists())
        saved = self.apply(); (self.root / 'server.json').unlink()
        with patch.object(self.store, '_private', side_effect=AssertionError('private read')), \
             patch.object(f, '_openssl', side_effect=AssertionError('process')):
            self.assertEqual(self.store.report(), saved)
        with self.assertRaises(InstallerError): self.store.verify()

    def test_exact_project_private_modes_and_repeat_import(self):
        saved = self.apply(); before = (self.root / 'server.json').read_bytes()
        self.assertEqual(saved['state'], 'IMPORTED'); self.assertEqual(saved['receipt']['project_id'], 'hestia-test')
        self.assertEqual(self.store.verify(), saved); self.assertEqual(self.apply(), saved)
        self.assertEqual((self.root / 'server.json').read_bytes(), before)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for path in self.root.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        public = canonical_bytes(saved)
        for secret in (self.key.encode(), self.account['client_email'].encode(), b'PRIVATE KEY'):
            self.assertNotIn(secret, public)
        for field in ('service_configured', 'google_authorization_verified', 'phone_delivery_verified'):
            self.assertFalse(saved[field])

    def test_invalid_selection_has_no_filesystem_effect(self):
        for changes in ({'version': True}, {'version': 2}, {'project_id': '../other'}, {'project_id': 'UPPERCASE'},
                        {'gateway_plan_sha256': 'main'}, {'path': '/tmp/private.json'}):
            with self.subTest(changes=changes), self.assertRaises(InstallerError): self.store.plan({**self.profile, **changes})
            self.assertFalse(self.root.exists())

    def test_confirmation_and_project_mismatch_do_not_create_a_private_file(self):
        plan = self.plan()
        for confirmation, confirmed in (('b' * 64, True), (plan['confirmation'], False)):
            with self.assertRaises(InstallerError): self.store.import_bytes(self.raw(), confirmation, confirmed=confirmed)
        self.account['project_id'] = 'different-project'
        with self.assertRaises(InstallerError): self.apply()
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'.transaction.lock', 'profile.json'})

    def test_ambiguous_oversized_and_wrong_endpoint_credentials_are_rejected(self):
        self.plan(); good = self.raw()
        variants = [b'{}', b'null', b'[]', b'not-json', good + b'{}', b'x' * (f.MAX_BYTES + 1),
            good.replace(b'"project_id": "hestia-test"', b'"project_id":"different-project","project_id":"hestia-test"')]
        for raw in variants:
            with self.subTest(size=len(raw)), self.assertRaises(InstallerError):
                self.store.import_bytes(raw, self.store.report()['confirmation'], confirmed=True)
        for field, value in (('type', 'authorized_user'), ('token_uri', 'https://untrusted.example.test'),
                             ('client_email', 'not-an-account'), ('private_key', self.key + self.key)):
            with self.subTest(field=field), self.assertRaises(InstallerError):
                f.validate(json.dumps({**self.account, field: value}).encode(), 'hestia-test')
        self.assertFalse((self.root / 'server.json').exists())

    def test_non_rsa_and_small_keys_are_refused(self):
        for args in (['EC', '-pkeyopt', 'ec_paramgen_curve:P-256'], ['RSA', '-pkeyopt', 'rsa_keygen_bits:1024']):
            private = subprocess.run(['/usr/bin/openssl', 'genpkey', '-algorithm', *args], capture_output=True, check=True, timeout=30).stdout.decode()
            with self.subTest(algorithm=args[0]), self.assertRaises(InstallerError):
                f.validate(json.dumps({**self.account, 'private_key': private}).encode(), 'hestia-test')

    def test_lost_completion_can_only_reconcile_identical_credential(self):
        self.plan(); original = self.store._write
        def cut(name, value):
            if name == 'receipt.json': raise OSError('synthetic reply loss')
            return original(name, value)
        with patch.object(self.store, '_write', side_effect=cut), self.assertRaises(InstallerError): self.apply()
        self.assertTrue((self.root / 'server.json').exists())
        before = (self.root / 'server.json').read_bytes()
        self.assertEqual(self.apply()['state'], 'IMPORTED'); self.assertEqual(before, (self.root / 'server.json').read_bytes())

    def test_partial_private_file_is_not_repaired(self):
        self.plan()
        _, metadata = f.validate(self.raw(), 'hestia-test')
        self.store._write('intent.json', {'version': 1, 'profile_sha256': self.plan()['confirmation'], **metadata})
        path = self.root / 'server.json'; path.write_bytes(b'{'); path.chmod(0o600)
        with self.assertRaises(InstallerError): self.apply()
        self.assertEqual(path.read_bytes(), b'{'); self.assertFalse((self.root / 'receipt.json').exists())

    def test_completed_credential_is_never_recreated_or_replaced(self):
        self.apply(); path = self.root / 'server.json'; before = path.read_bytes()
        self.account['client_email'] = 'changed@credential-project.iam.gserviceaccount.com'
        with self.assertRaises(InstallerError): self.apply()
        self.assertEqual(path.read_bytes(), before); path.unlink()
        self.account['client_email'] = 'sender@credential-project.iam.gserviceaccount.com'
        with self.assertRaises(InstallerError): self.apply()
        self.assertFalse(path.exists())

    def test_private_file_modes_symlinks_and_hardlinks_are_refused(self):
        self.apply(); path = self.root / 'server.json'
        path.chmod(0o640)
        with self.assertRaises(InstallerError): self.store.verify()
        path.chmod(0o600); link = self.root / 'extra'; os.link(path, link)
        with self.assertRaises(InstallerError): self.store.verify()
        link.unlink(); path.rename(link); path.symlink_to(link)
        with self.assertRaises((InstallerError, OSError)): self.store.verify()

    def test_choice_drift_and_parallel_import_are_blocked(self):
        self.plan()
        with self.assertRaises(InstallerError): self.store.plan({**self.profile, 'gateway_plan_sha256': 'b' * 64})
        with self.store.lock.locked(create=False), self.assertRaisesRegex(InstallerError, 'BUSY'):
            self.apply()


if __name__ == '__main__': unittest.main()
