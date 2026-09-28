#!/usr/bin/env python3
"""Real fresh/upgrade controllers through the operator journal, disposable only.

This qualifies the transaction boundary, not the public wizard or product-owned
service activation. Existing 5C campaigns are not rerun by this focused suite.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_upgrade_recovery_systemd as previous
import finalization_mariadb as fresh
from installer import application_operations as a
from installer.engine import TransactionEngine
from installer.operations import OperationRegistry, SecretVault
from installer.transaction import StateJournal
from installer.service import TransactionService

quality = previous.quality


class JournalMixin:
    def setUp(self):
        super().setUp()
        self.operator_journal = StateJournal(self.root / 'operator/state.json')

    def kill_child(self, action):
        pid = os.fork()
        if pid == 0:
            try: action()
            except BaseException: os._exit(98)
            os._exit(97)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found: break
            time.sleep(.1)
        else:
            os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            self.fail('Journal child timeout')
        self.assertTrue(os.WIFSIGNALED(status), status)
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)

    def hook(self, name, phase='apply'):
        def event(actual_name, actual_phase, event):
            if (actual_name, actual_phase, event) == (name, phase, 'after'):
                os.kill(os.getpid(), signal.SIGKILL)
        return event

    def vault(self, payload):
        vault = SecretVault(); inputs = a.WebInputs.capture(payload, vault)
        for prefix, credential in (('migration', self.credentials), ('authority', self.authority)):
            vault.put('web.' + prefix + '_user', credential._user)
            vault.put('web.' + prefix + '_password', credential._password)
        return inputs, vault

    def engine(self, operations, vault, hook=None):
        return TransactionEngine(self.operator_journal, OperationRegistry(tuple(operations)), secrets=vault, fault_hook=hook)

    def no_secret(self, engine):
        text = json.dumps(engine.report())
        for secret in (*self.payload['secrets'].values(), self.credentials._password, self.authority._password):
            if secret: self.assertNotIn(secret, text)


class FreshJournalLive(JournalMixin, fresh.FinalizationIntegration):
    def build(self, hook=None):
        inputs, vault = self.vault(self.payload)
        db = a.DatabasePreparationOperation(self.client, inputs, config_root=self.output)
        final = a.FinalizationOperation(self.final, inputs, config_root=self.output)
        return self.engine((db, final), vault, hook)

    def test_journal_fresh_existing_local_completes_and_refuses_rollback(self):
        self.payload['administrator']['first_name'] = 'Élise <&>漢字🙂'
        engine = self.build(); plan = engine.plan(mode='fresh')
        self.assertEqual(engine.apply(plan['plan_sha256'])['state'], 'DONE')
        self.assertEqual(self.observe()['state'], 'WEB_FRESH_FINALIZED')
        # Finalization is an active dependent: that guard intentionally runs
        # before testing whether the SQL boundary itself is reversible.
        with self.assertRaisesRegex(a.InstallerError, 'DEPENDENCY_BLOCKED'):
            engine.rollback('web.database', plan['plan_sha256'])
        with self.assertRaisesRegex(a.InstallerError, 'ROLLBACK_UNSUPPORTED'):
            engine.rollback('web.finalization', plan['plan_sha256'])
        self.assertEqual(self.observe()['state'], 'WEB_FRESH_FINALIZED')
        self.no_secret(engine)

    def test_journal_fresh_managed_and_assistant_are_real(self):
        self.managed(); self.payload['assistant']['action'] = 'configure'
        self.payload['secrets']['openai_api_key'] = 'fixture-journal-openai-0123456789'
        engine = self.build(); plan = engine.plan(mode='fresh')
        self.assertEqual(engine.apply(plan['plan_sha256'])['state'], 'DONE')
        result = self.observe(); self.assertTrue(result['result']['assistant_enabled'])
        self.assertEqual(result['result']['api_access'], 'NOT_TESTED'); self.no_secret(engine)

    def test_journal_database_reply_lost_does_not_recreate_admin(self):
        engine = self.build(self.hook('web.database')); plan = engine.plan(mode='fresh')
        self.kill_child(lambda: engine.apply(plan['plan_sha256']))
        before = self.sql(query=f'SELECT id_user,email,password_hash FROM `{self.db}`.UserInfo')
        restarted = self.build()
        self.assertEqual(restarted.resume(plan['plan_sha256'])['state'], 'DONE')
        self.assertEqual(before, self.sql(query=f'SELECT id_user,email,password_hash FROM `{self.db}`.UserInfo'))
        self.no_secret(restarted)

    def test_journal_finalization_reply_lost_refresh_and_new_credentials(self):
        engine = self.build(self.hook('web.finalization')); plan = engine.plan(mode='fresh')
        self.kill_child(lambda: engine.apply(plan['plan_sha256']))
        restarted = self.build(); restarted.secrets.clear()
        service = TransactionService(restarted)
        before = self.operator_journal.read()
        self.assertEqual(service.wizard_state()['installation'], before)
        self.assertEqual(service.report()['installation'], before)
        state = restarted.resume(plan['plan_sha256'])
        self.assertEqual(state['last_error_redacted'], 'SECRET_REQUIRED')
        restarted.secrets.put('web.database_password', self.payload['secrets']['database_password'])
        self.assertEqual(restarted.retry('web.finalization', plan['plan_sha256'])['state'], 'DONE')
        self.no_secret(restarted)

    def test_journal_incomplete_sql_stays_manual_and_preserves_the_database(self):
        engine = self.build(); plan = engine.plan(mode='fresh')
        def interrupted(*args, **kwargs): os.kill(os.getpid(), signal.SIGKILL)
        def child():
            with patch.object(a.db, '_configuration_files', side_effect=interrupted): engine.apply(plan['plan_sha256'])
        self.kill_child(child)
        before = self.sql(query=f'SELECT COUNT(*) AS count FROM `{self.db}`.UserInfo')
        result = self.build().resume(plan['plan_sha256'])
        self.assertEqual(result['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(before, self.sql(query=f'SELECT COUNT(*) AS count FROM `{self.db}`.UserInfo'))
        self.assertFalse((self.webroot / 'includes/db.php').exists())

    def test_journal_seal_without_receipt_is_never_completed_by_replay(self):
        engine = self.build(); plan = engine.plan(mode='fresh'); write = a.f._write
        def interrupted(fd, name, *args, **kwargs):
            if name == 'finalized.json': os.kill(os.getpid(), signal.SIGKILL)
            return write(fd, name, *args, **kwargs)
        def child():
            with patch.object(a.f, '_write', side_effect=interrupted): engine.apply(plan['plan_sha256'])
        self.kill_child(child)
        seal = (self.directory / 'seal.json').read_bytes()
        self.assertEqual(self.build().resume(plan['plan_sha256'])['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual((self.directory / 'seal.json').read_bytes(), seal)
        self.assertFalse((self.directory / 'finalized.json').exists())


class UpgradeJournalLive(JournalMixin, previous.RecoveryLive):
    def build(self, *, reopen=False, hook=None):
        inputs, vault = self.vault(self.existing())
        # Reconstruct controllers, never reuse an in-memory lease from a child.
        old = self.operation
        self.operation = a.u.StorageUpgrade(old.runtime, old.source, old.target_source, old.http, old.collector)
        op = a.StorageUpgradeOperation(self.operation, inputs, config_root=self.output, backup_root=self.backups)
        ops = (op, a.StorageResumeOperation(op)) if reopen else (op,)
        return self.engine(ops, vault, hook)

    def test_journal_upgrade_reopen_then_real_fixture_login(self):
        self.ready(); engine = self.build(reopen=True); plan = engine.plan(mode='upgrade')
        self.assertEqual(engine.apply(plan['plan_sha256'])['state'], 'DONE')
        self.serving(); self.login(); self.no_secret(engine)
        with self.assertRaises(a.InstallerError): engine.rollback('web.storage-upgrade', plan['plan_sha256'])

    def test_journal_upgrade_reply_lost_is_recognized_without_authority(self):
        self.ready(); engine = self.build(hook=self.hook('web.storage-upgrade')); plan = engine.plan(mode='upgrade')
        self.kill_child(lambda: engine.apply(plan['plan_sha256']))
        restarted = self.build(); restarted.secrets.clear()
        self.assertEqual(restarted.resume(plan['plan_sha256'])['state'], 'DONE')
        self.closed(); self.no_secret(restarted)

    def test_journal_interrupted_cutover_resumes_after_read_only_refresh(self):
        self.ready(); engine = self.build(); plan = engine.plan(mode='upgrade'); move = a.u._move
        def interrupted(source, target):
            move(source, target)
            if source == self.webroot: os.kill(os.getpid(), signal.SIGKILL)
        def child():
            with patch.object(a.u, '_move', side_effect=interrupted): engine.apply(plan['plan_sha256'])
        self.kill_child(child)
        restarted = self.build(); service = TransactionService(restarted)
        self.assertEqual(service.report()['installation']['state'], 'RUNNING')
        self.assertEqual(service.wizard_state()['installation']['state'], 'RUNNING')
        self.assertFalse(self.webroot.exists())
        self.assertEqual(restarted.resume(plan['plan_sha256'])['state'], 'DONE')
        self.serving(); self.login()

    def test_journal_rollback_reply_lost_does_not_restore_or_delete_sql(self):
        self.ready(); engine = self.build(); plan = engine.plan(mode='upgrade')
        self.assertEqual(engine.apply(plan['plan_sha256'])['state'], 'DONE')
        before = self.sql(query=f'SELECT id_user,photo_profil FROM `{self.db}`.UserInfo')
        interrupted = self.build(hook=self.hook('web.storage-upgrade', 'rollback'))
        self.kill_child(lambda: interrupted.rollback('web.storage-upgrade', plan['plan_sha256']))
        self.assertEqual(self.build().resume(plan['plan_sha256'])['state'], 'ROLLED_BACK')
        self.assertEqual(before, self.sql(query=f'SELECT id_user,photo_profil FROM `{self.db}`.UserInfo'))
        self.serving(rollback=True); self.login()

    def test_journal_authorization_response_lost_remains_irreversible(self):
        self.ready(); engine = self.build(reopen=True, hook=self.hook('web.storage-resume')); plan = engine.plan(mode='upgrade')
        self.kill_child(lambda: engine.apply(plan['plan_sha256']))
        restarted = self.build(reopen=True); restarted.secrets.clear()
        self.assertEqual(restarted.resume(plan['plan_sha256'])['state'], 'DONE')
        with self.assertRaises(a.InstallerError): restarted.rollback('web.storage-upgrade', plan['plan_sha256'])
        self.serving(); self.login()

    def test_journal_runtime_drift_after_lost_reply_never_becomes_done(self):
        self.ready(); engine = self.build(hook=self.hook('web.storage-upgrade')); plan = engine.plan(mode='upgrade')
        self.kill_child(lambda: engine.apply(plan['plan_sha256']))
        file = self.http_root / 'staged.json'; original = file.read_bytes()
        try:
            file.write_bytes(original + b' ')
            self.assertNotEqual(self.build().resume(plan['plan_sha256'])['state'], 'DONE')
        finally: file.write_bytes(original)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--shard-index', type=int, choices=(0, 1, 2), required=True); args = parser.parse_args()
    if os.environ.get('HESTIA_APPLICATION_JOURNAL_TEST') != '1': raise RuntimeError('Explicit journal recipe opt-in required')
    fresh.WEB = args.web; previous.base.previous.WEB = args.web; previous.base.TARGET = args.target
    source = quality.snapshot(previous.base.ROOT)
    cls = FreshJournalLive if args.shard_index == 0 else UpgradeJournalLive
    names = sorted(name for name in cls.__dict__ if name.startswith('test_journal_'))
    if args.shard_index: names = names[args.shard_index - 1::2]
    expected = 6 if args.shard_index == 0 else 3
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(cls(name) for name in names))
    stable = source == quality.snapshot(previous.base.ROOT)
    report = {'suite': 'Application transaction journal with actual controllers', 'tests': result.testsRun,
        'expected': expected, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == expected and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'shard': args.shard_index,
        'phase5_complete': False, 'application_installed': False, 'wizard_application_wired': False,
        'service_activation_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'JOURNAL-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
