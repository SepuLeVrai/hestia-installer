"""Closed public cockpit profile and durable seven-stage ledger contracts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import gateway_transition_execution as p
from installer import gateway_public_generation as g
from installer.model import InstallerError
from test_mobile_boot import profile


class PublicCockpitTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-public-cockpit-', dir='/var/lib')
        self.addCleanup(temp.cleanup)
        self.control = object.__new__(p.GatewayTransitionExecution)
        self.control.root = Path(temp.name)
        mobile = profile(); original = g.mobile.MobileBootRuntime(mobile)
        self.selection = {'assessment': {'source': original.gateway.profile.binding()}}
        self.control.transition = SimpleNamespace(profile=lambda: deepcopy(self.selection))
        self.value = {'version': 1, 'instance': original.layout.instance, 'lease_id': 'e' * 32,
            'policy': p.PUBLIC_POLICY, 'parents': {key: 'a' * 64 for key in p.PARENTS},
            'draft_sha256': 'b' * 64, 'backup_profile_sha256': 'c' * 64, 'backup_receipt_sha256': 'd' * 64,
            'transition_sha256': p.digest(self.selection), 'public_source': {'mobile': mobile,
                'successor_code': {n: g.boot.f._sha(raw) for n, raw in g.mobile.code_files().items()},
                'mobile_journal_sha256': 'a' * 64, 'shared_journal_sha256': 'b' * 64}}
        self.control._write('profile.json', self.value)

    def replace_profile(self, value):
        (self.control.root / 'profile.json').unlink(); self.control._write('profile.json', value)

    def test_profile_read_is_file_only_and_preserves_frozen_source_code(self):
        with patch('subprocess.run', side_effect=AssertionError('native read')):
            self.assertEqual(self.control.profile(), self.value)
        self.assertEqual(self.control.profile()['public_source']['mobile']['code'], self.value['public_source']['mobile']['code'])

    def test_public_policy_cannot_omit_source_or_add_arbitrary_destination(self):
        for change in ('missing', 'destination'):
            value = deepcopy(self.value)
            if change == 'missing': del value['public_source']
            else: value['public_source']['destination'] = '/foreign'
            self.replace_profile(value)
            with self.assertRaises(InstallerError): self.control.profile()

    def test_source_identity_and_journal_hashes_are_closed(self):
        for change in ('identity', 'journal'):
            value = deepcopy(self.value)
            if change == 'identity': value['instance'] = 'f' * 32
            else: value['public_source']['mobile_journal_sha256'] = 'foreign'
            self.replace_profile(value)
            with self.assertRaises(InstallerError): self.control.profile()

    def test_seven_stages_are_hash_linked_including_transfer_and_opening(self):
        self.assertEqual(p.PUBLIC_STAGES, ('binaries', 'cutover', 'publication', 'public-transfer', 'admission', 'activation', 'public-open'))
        confirmation = p.digest(self.value); previous = confirmation
        self.control._write('approved.json', {'confirmation': confirmation})
        for index, name in enumerate(p.PUBLIC_STAGES):
            owner = {'confirmation': confirmation, 'stage': name, 'previous_sha256': previous}
            self.control._write(name + '.intent.json', owner)
            rows = self.control.progress(self.value)[1]
            self.assertEqual(rows[index]['state'], 'INTENT_RECORDED')
            done = {'owner': owner, 'result_sha256': 'c' * 64}
            self.control._write(name + '.done.json', done); previous = p.digest(done)
        self.assertTrue(all(row['state'] == 'DONE' for row in self.control.progress(self.value)[1]))

    def test_public_opening_cannot_jump_over_admission_or_activation(self):
        confirmation = p.digest(self.value)
        self.control._write('approved.json', {'confirmation': confirmation})
        self.control._write('public-open.intent.json', {'confirmation': confirmation,
            'stage': 'public-open', 'previous_sha256': confirmation})
        with self.assertRaises(InstallerError): self.control.progress(self.value)


if __name__ == '__main__': unittest.main()
