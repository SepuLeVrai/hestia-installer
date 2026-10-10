"""Durable start ledger contracts; native listeners are tested separately."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import gateway_public_opening as o
from installer.model import InstallerError


class PublicOpeningLedgerTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-public-opening-', dir='/var/lib')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.opening = object.__new__(o.Opening)
        self.opening.root = self.root
        self.unit = 'hestia-' + 'a' * 32 + '-public-http.service'
        self.opening.shared = SimpleNamespace(web=SimpleNamespace(unit=lambda role: self.unit), control=Mock())
        self.before = {'unit': self.unit, 'invocation': ''}
        self.after = {'unit': self.unit, 'invocation': 'b' * 32}
        self.opening.observe = Mock(side_effect=lambda role, running: dict(self.after if running else self.before))
        self.plan = {'binding': 'fixture', 'epoch': 'fixture'}

    def start(self, **kwargs):
        with o.g.boot.fs._directory(self.root) as fd:
            self.opening.start(fd, self.plan, 'http', **kwargs)

    def read(self, name):
        with o.g.boot.fs._directory(self.root) as fd: return o.f._optional(fd, name)

    def write(self, name, value):
        with o.g.boot.fs._directory(self.root) as fd: o.f._put(fd, name, value)

    def lose_reply(self):
        self.opening.shared.control.side_effect = OSError('lost reply after native command')
        with self.assertRaises(OSError): self.start()
        self.assertIsNotNone(self.read('http.intent.json'))
        self.assertIsNone(self.read('http.done.json'))
        self.opening.shared.control.reset_mock(side_effect=True)

    def test_completed_start_check_never_replays_command_or_changes_receipt(self):
        self.start(); raw = (self.root / 'http.done.json').read_bytes()
        self.opening.shared.control.assert_called_once_with('start', 'http')
        self.opening.shared.control.reset_mock()
        self.start(check_only=True); self.start()
        self.opening.shared.control.assert_not_called()
        self.assertEqual((self.root / 'http.done.json').read_bytes(), raw)

    def test_lost_reply_running_invocation_is_observed_without_replay(self):
        self.lose_reply(); self.start()
        self.opening.shared.control.assert_not_called()
        self.assertEqual(self.read('http.done.json')['observed'], self.after)

    def test_pending_start_that_is_still_stopped_never_replays(self):
        self.lose_reply()
        self.opening.observe.side_effect = InstallerError(o.g.ErrorCode.MANUAL_ACTION_REQUIRED)
        with self.assertRaises(InstallerError): self.start()
        self.opening.shared.control.assert_not_called()
        self.assertIsNone(self.read('http.done.json'))

    def test_completed_but_different_invocation_is_not_adopted(self):
        self.start(); self.opening.shared.control.reset_mock()
        self.after['invocation'] = 'c' * 32
        with self.assertRaises(InstallerError): self.start()
        self.opening.shared.control.assert_not_called()

    def test_check_only_pending_does_not_create_intent(self):
        with self.assertRaises(InstallerError): self.start(check_only=True)
        self.assertEqual(list(self.root.iterdir()), [])
        self.opening.shared.control.assert_not_called()

    def test_foreign_intent_is_rejected_before_any_start(self):
        self.write('http.intent.json', {'owner': {}, 'before': self.before})
        with self.assertRaises(InstallerError): self.start()
        self.opening.shared.control.assert_not_called()

    def test_lost_reply_cannot_change_confirmation(self):
        self.lose_reply(); self.plan['binding'] = 'foreign'
        with self.assertRaises(InstallerError): self.start()
        self.opening.shared.control.assert_not_called()



class PublicOpeningOwnershipTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-opening-owner-', dir='/var/lib')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.opening = object.__new__(o.Opening); self.opening.root = self.root
        self.opening.binding = Mock(return_value={'fixture': 'closed'})
        self.epoch = {'boot_id': 'a' * 8 + '-' + 'a' * 27, 'pid1_start': '123'}
        self.enterContext(patch.object(o.g.mobile.MobileBootRuntime, 'epoch_identity', return_value=self.epoch))
        with o.StateJournal(self.root / 'effect-lock.json').locked(create=False):
            with o.g.boot.fs._directory(self.root) as fd:
                self.plan = {'binding': self.opening.binding(), 'slot': o.f._identity(o.os.fstat(fd)),
                             'lock': self.opening.lock_identity(fd), 'epoch': dict(self.epoch)}
                o.f._put(fd, 'plan.json', self.plan)

    def test_resume_requires_original_pid1_epoch(self):
        with self.opening.slot(): pass
        self.epoch['pid1_start'] = '456'
        with self.assertRaises(InstallerError):
            with self.opening.slot(): self.fail('new epoch admitted for start')
        with self.opening.slot(current_epoch=False) as (_, plan): self.assertEqual(plan, self.plan)

    def test_identical_bytes_in_foreign_lock_inode_are_refused(self):
        lock = self.root / '.transaction.lock'; backup = self.root.parent / (self.root.name + '-lock')
        lock.rename(backup); self.addCleanup(backup.unlink)
        lock.write_bytes(b''); lock.chmod(0o600)
        with self.assertRaises(InstallerError):
            with self.opening.slot(current_epoch=False): self.fail('foreign lock adopted')

    def test_read_only_slot_does_not_recreate_missing_lock(self):
        (self.root / '.transaction.lock').unlink()
        with self.assertRaises(OSError):
            with self.opening.slot(): self.fail('missing lock adopted')
        self.assertFalse((self.root / '.transaction.lock').exists())

    def test_unexpected_file_in_opening_slot_refuses_resume(self):
        (self.root / 'foreign.json').write_text('{}')
        with self.assertRaises(InstallerError):
            with self.opening.slot(): self.fail('extra file accepted')

    def test_worker_invalid_opened_owner_never_falls_back_to_intent(self):
        generation = SimpleNamespace(digest='a' * 64, _read=Mock(return_value={'foreign': True}))
        owned = SimpleNamespace(generation=generation, owner={'expected': True}, slot=Mock())
        with patch.object(o, 'Opening', return_value=owned), self.assertRaises(InstallerError):
            o.worker_admitted(generation, 'http', object())
        owned.slot.assert_not_called()

    def test_unopened_generation_cannot_start_sql_mobile_or_renewal(self):
        generation = SimpleNamespace(digest='a' * 64, _read=Mock(return_value=None))
        owned = SimpleNamespace(generation=generation, owner={'expected': True}, slot=Mock())
        with patch.object(o, 'Opening', return_value=owned):
            for role in ('sql', 'web', 'mobile', 'renew'):
                with self.subTest(role=role), self.assertRaises(InstallerError):
                    o.worker_admitted(generation, role, object())
        owned.slot.assert_not_called()


if __name__ == '__main__': unittest.main()
