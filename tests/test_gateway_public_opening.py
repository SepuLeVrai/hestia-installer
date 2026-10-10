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



class PublicOpeningNewEpochTests(unittest.TestCase):
    setUp = PublicOpeningOwnershipTests.setUp

    def test_same_epoch_check_only_delegates_to_observation_path(self):
        self.opening.apply = Mock(return_value={'state': 'fixture'})
        self.assertEqual(self.opening.check(), {'state': 'fixture'})
        self.opening.apply.assert_called_once_with(confirmed=True, check_only=True)

    def test_new_epoch_never_reuses_first_epoch_start_protocol(self):
        self.epoch['pid1_start'] = '456'
        self.opening.apply = Mock(side_effect=AssertionError('first epoch reused'))
        self.opening.generation = SimpleNamespace(original=SimpleNamespace(shared=SimpleNamespace(root=self.root)))
        self.opening.authority = SimpleNamespace(check=Mock(side_effect=InstallerError(o.g.ErrorCode.SOURCE_DRIFT)))
        with self.assertRaises(InstallerError): self.opening.check()
        self.opening.apply.assert_not_called()
        self.opening.authority.check.assert_called_once()



class PublicLocalGuardTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-local-guard-', dir='/var/lib')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name); self.lease = 'e' * 32
        self.owner = {'generation_sha256': 'a' * 64, 'fragment_plan_sha256': 'b' * 64, 'admission_sha256': 'c' * 64}
        self.activation = {'version': 1, 'instance': 'a' * 32, 'lease_id': self.lease,
                           'activation_plan_sha256': 'd' * 64, 'resume_plan_sha256': 'f' * 64}
        self.raw = o.g.canonical_bytes({'handoff': {}, 'activation': self.activation})
        self.epoch = {'boot_id': 'a' * 36, 'pid1_start': '123'}
        self.enterContext(patch.object(o.g.mobile.MobileBootRuntime, 'epoch_identity', return_value=self.epoch))
        self.values = {'activation-epoch.json': {'owner': self.owner, 'epoch': dict(self.epoch)}}
        self.generation = SimpleNamespace(digest='a' * 64, _read=lambda name: self.values.get(name))
        self.authority = SimpleNamespace(backups=self.root, lease_id=self.lease, activation_owner=lambda: self.raw,
            read=lambda name: self.raw if name == 'consumed.json' else None,
            runtime=SimpleNamespace(web=SimpleNamespace(unit=lambda role: 'hestia-' + 'a' * 32 + '-php.service')))
        owned = SimpleNamespace(generation=self.generation, owner=self.owner)
        self.enterContext(patch.object(o, 'Opening', return_value=owned))
        self.record = self.root / ('mobile-activation-' + self.lease); self.record.mkdir(mode=0o700)
        self.admitted = {'owner': self.activation, 'state': 'ACTIVITY_GATE_RELEASED', 'services_started': False,
                         'current_sql_admission': False, 'automatic_start_retry_allowed': False}
        self.intent = {'owner': self.activation, 'role': 'php', 'not_before_monotonic_us': 1,
            'before': {'unit': self.authority.runtime.web.unit('php'), 'invocation_id': '', 'active_enter_monotonic_us': 0}}
        with o.g.boot.fs._directory(self.record) as fd:
            o.f._put(fd, 'admitted.json', self.admitted); o.f._put(fd, 'php.intent.json', self.intent)

    def test_same_epoch_consumed_php_intent_allows_dependency_guards_only(self):
        for role in ('sql', 'web'): o.worker_admitted(self.generation, role, self.authority)
        for role in ('mobile', 'renew'):
            with self.assertRaises(InstallerError): o.worker_admitted(self.generation, role, self.authority)

    def test_new_pid1_cannot_reuse_local_activation_dependency_permission(self):
        self.epoch['pid1_start'] = '456'
        with self.assertRaises(InstallerError): o.worker_admitted(self.generation, 'web', self.authority)

    def test_missing_or_foreign_php_intent_never_allows_guard(self):
        path = self.record / 'php.intent.json'; path.unlink()
        with self.assertRaises(InstallerError): o.worker_admitted(self.generation, 'sql', self.authority)
        self.intent['owner'] = {'foreign': True}
        with o.g.boot.fs._directory(self.record) as fd: o.f._put(fd, path.name, self.intent)
        with self.assertRaises(InstallerError): o.worker_admitted(self.generation, 'web', self.authority)



class PublicOpeningCompletionTests(unittest.TestCase):
    setUp = PublicOpeningLedgerTests.setUp

    def test_completion_requires_all_three_distinct_role_receipts(self):
        self.opening.owner = {'fixture': 'bound'}
        with o.g.boot.fs._directory(self.root) as fd:
            self.opening.start(fd, self.plan, 'http')
            self.opening.start(fd, self.plan, 'https')
            with self.assertRaises(InstallerError): self.opening.completion(fd, self.plan)
            self.opening.start(fd, self.plan, 'timer')
            sealed = self.opening.completion(fd, self.plan)
            self.assertEqual(sealed['owner'], self.opening.owner)
            self.assertEqual(sealed, self.opening.completion(fd, self.plan))
            path = self.root / 'http.done.json'
            receipt = o.f._optional(fd, path.name); receipt['observed']['invocation'] = 'c' * 32
            path.unlink(); o.f._put(fd, path.name, receipt)
            self.assertNotEqual(sealed, self.opening.completion(fd, self.plan))

    def test_new_epoch_cannot_boot_after_unsealed_public_opening(self):
        from contextlib import contextmanager
        plan = {'epoch': {'boot_id': 'a' * 36, 'pid1_start': '1'}}
        owner = {'fixture': 'bound'}
        values = {'opened.json': owner, 'opening.json': {'plan_sha256': o.f.sha(o.g.canonical_bytes(plan))}}
        generation = SimpleNamespace(digest='a' * 64, _read=lambda name: values.get(name))
        @contextmanager
        def slot(**kwargs): yield None, plan
        owned = SimpleNamespace(generation=generation, owner=owner, slot=slot)
        with patch.object(o, 'Opening', return_value=owned), \
                patch.object(o.g.mobile.MobileBootRuntime, 'epoch_identity', return_value={**plan['epoch'], 'pid1_start': '2'}):
            for role in ('sql', 'web', 'mobile', 'http', 'https', 'renew'):
                with self.subTest(role=role), self.assertRaises(InstallerError):
                    o.worker_admitted(generation, role, object())


if __name__ == '__main__': unittest.main()
