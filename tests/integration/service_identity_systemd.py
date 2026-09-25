#!/usr/bin/env python3
"""Real local accounts only inside the opted-in disposable systemd container."""
import argparse
import dataclasses
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import service_identity as i
from installer import session_cleaner as c
from installer.operations import RecoveryDecision
from http_runtime_systemd import HttpRuntimeLive, command
sys.path.insert(0, str(ROOT / 'scripts'))
import quality


class ServiceIdentityLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_SERVICE_IDENTITY_TEST') != '1' or os.geteuid() != 0 \
                or Path('/proc/1/comm').read_text().strip() != 'systemd':
            raise RuntimeError('Explicit disposable root systemd opt-in required')

    def setUp(self):
        self.identity = i.ServiceIdentity(os.urandom(16).hex())
        self.operation = i.ServiceIdentityOperation(self.identity)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # Fixture-only deletion of the random identity created by this case.
        command('userdel', self.identity.user, check=False)
        command('groupdel', self.identity.user, check=False)
        shutil.rmtree(self.identity.directory, ignore_errors=True)

    def test_prepare_is_read_only_without_account_or_journal(self):
        before = i._tables(); self.identity.prepare()
        self.assertTrue(before == i._tables())
        self.assertFalse(self.identity.directory.exists())
        with self.assertRaises(KeyError): pwd.getpwnam(self.identity.user)

    def test_real_creation_locked_exclusive_no_home_mail_or_other_account_changes(self):
        before = i._tables(); report = self.identity.create(confirmed=True)
        account = self.identity.account(); after = i._tables()
        self.assertEqual(report['state'], 'SERVICE_IDENTITY_CREATED')
        self.assertTrue(before == {k: [r for r in rows if r[0] != self.identity.user] for k, rows in after.items()})
        self.assertEqual(account.pw_dir, '/nonexistent'); self.assertEqual(account.pw_shell, '/usr/sbin/nologin')
        self.assertEqual(os.getgrouplist(account.pw_name, account.pw_gid), [account.pw_gid])
        self.assertFalse(Path('/nonexistent').exists()); self.assertFalse((Path('/var/mail') / account.pw_name).exists())
        self.assertNotEqual(command('runuser', '-u', account.pw_name, '--', '/usr/sbin/nologin', check=False).returncode, 0)
        for path in self.identity.directory.iterdir(): self.assertEqual(path.stat().st_mode & 0o7777, 0o600)
        self.assertNotIn(account.pw_name, json.dumps(report)); self.assertNotIn('records', json.dumps(report))
        self.assertFalse(report['application_installed']); self.assertFalse(report['services_started'])

    def test_existing_user_is_refused_before_any_journal_or_mutation(self):
        command('useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', self.identity.user)
        before = i._tables()
        with self.assertRaises(i.ServiceIdentityError): self.identity.create(confirmed=True)
        self.assertTrue(before == i._tables()); self.assertFalse(self.identity.directory.exists())

    def test_existing_group_without_user_is_refused_before_reservation(self):
        command('groupadd', '--system', self.identity.user); before = i._tables()
        with self.assertRaises(i.ServiceIdentityError): self.identity.create(confirmed=True)
        self.assertTrue(before == i._tables()); self.assertFalse(self.identity.directory.exists())

    def test_failed_external_command_retains_attempt_and_never_blindly_retries(self):
        with patch.object(self.identity, '_create_account', side_effect=i.ServiceIdentityError('SERVICE_IDENTITY_COMMAND_FAILED')):
            with self.assertRaises(i.ServiceIdentityError): self.identity.create(confirmed=True)
        self.assertTrue((self.identity.directory / 'identity.attempt').is_file())
        with self.assertRaises(i.ServiceIdentityError): self.identity.create(confirmed=True)
        self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        with self.assertRaises(KeyError): pwd.getpwnam(self.identity.user)

    def test_receipt_failure_keeps_real_account_but_recovery_is_manual(self):
        write = i.f._write
        def fail(fd, name, *args, **kwargs):
            if name == 'created.json': raise OSError('fixture write failure')
            return write(fd, name, *args, **kwargs)
        with patch.object(i.f, '_write', side_effect=fail), self.assertRaises(i.ServiceIdentityError):
            self.identity.create(confirmed=True)
        self.assertGreater(pwd.getpwnam(self.identity.user).pw_uid, 0)
        self.assertFalse((self.identity.directory / 'created.json').exists())
        self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        with self.assertRaises(i.ServiceIdentityError): self.identity.create(confirmed=True)

    def test_process_exit_after_useradd_does_not_certify_or_delete_partial_identity(self):
        def child():
            create = self.identity._create_account
            def leave(): create(); os._exit(73)
            self.identity._create_account = leave
            self.identity.create(confirmed=True)
        process = multiprocessing.get_context('fork').Process(target=child)
        process.start(); process.join(10)
        if process.is_alive(): process.kill(); process.join(); self.fail('Account fixture deadline exceeded')
        self.assertEqual(process.exitcode, 73); account = pwd.getpwnam(self.identity.user)
        self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        with self.assertRaises(i.ServiceIdentityError): self.identity.create(confirmed=True)
        self.assertEqual(pwd.getpwnam(self.identity.user), account)

    def test_completed_lost_response_recovers_read_only_and_drifted_receipt_refuses(self):
        self.identity.create(confirmed=True)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.identity.directory.iterdir()}
        with patch.object(i.subprocess, 'run', side_effect=AssertionError('Observation cannot execute a command')):
            self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
            self.assertEqual(self.operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.identity.directory.iterdir()})
        (self.identity.directory / 'created.json').write_text('{}')
        self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)

    def test_live_shell_and_supplementary_group_drift_are_refused_without_repair(self):
        self.identity.create(confirmed=True)
        command('usermod', '--shell', '/bin/sh', self.identity.user)
        with self.assertRaises(i.ServiceIdentityError): self.identity.observe()
        self.assertEqual(pwd.getpwnam(self.identity.user).pw_shell, '/bin/sh')
        command('usermod', '--shell', '/usr/sbin/nologin', '--groups', 'adm', self.identity.user)
        with self.assertRaises(i.ServiceIdentityError): self.identity.observe()
        self.assertGreater(len(os.getgrouplist(self.identity.user, pwd.getpwnam(self.identity.user).pw_gid)), 1)

    def test_two_real_creators_have_one_exclusive_winner(self):
        context = multiprocessing.get_context('fork'); start = context.Event(); results = context.Queue()
        def child():
            start.wait(3)
            try: self.identity.create(confirmed=True); results.put('created')
            except i.ServiceIdentityError: results.put('refused')
        processes = [context.Process(target=child) for _ in range(2)]
        for process in processes: process.start()
        start.set()
        for process in processes:
            process.join(10)
            if process.is_alive(): process.kill(); process.join(); self.fail('Concurrent fixture deadline exceeded')
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(results.get(timeout=2) for _ in processes), ['created', 'refused'])
        self.assertEqual(self.identity.observe()['state'], 'SERVICE_IDENTITY_CREATED')

    def test_site_hook_is_refused_before_it_can_execute(self):
        base = Path('/etc/shadow-maint'); hooks = base / 'useradd-pre.d'
        created = [p for p in (base, hooks) if not p.exists()]
        hooks.mkdir(parents=True, exist_ok=True)
        marker = Path('/var/lib/hestia-hook-' + self.identity.instance)
        hook = hooks / ('hestia-fixture-' + self.identity.instance)
        try:
            hook.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n'); hook.chmod(0o755)
            with self.assertRaisesRegex(i.ServiceIdentityError, 'HOOKS_REJECTED'): self.identity.create(confirmed=True)
            self.assertFalse(marker.exists()); self.assertFalse(self.identity.directory.exists())
        finally:
            hook.unlink(); marker.unlink(missing_ok=True)
            for path in reversed(created): path.rmdir()

    def test_created_identity_runs_real_apache_php_and_collector_with_private_files(self):
        self.identity.create(confirmed=True)
        fx = HttpRuntimeLive(methodName='runTest'); fx.account = self.identity.account()
        fx.family = '8.4' if Path('/usr/sbin/php-fpm8.4').is_file() else '8.2'
        fx.setUp(); self.addCleanup(fx.doCleanups)
        fx.spec = dataclasses.replace(fx.spec, instance=self.identity.instance)
        fx.runtime = i.h.HttpRuntime(fx.spec); fx.scope = fx.runtime._scope(fx.account)
        fx.units = [fx.runtime.unit(role) for role in ('apache', 'php')]
        fx.create(); cleaner = c.SessionCleaner(fx.runtime)
        fx.units.extend((cleaner.unit, cleaner.timer)); cleaner.create(confirmed=True)
        self.assertEqual(self.identity.account().pw_uid, fx.account.pw_uid)
        fx.activate_fixture()
        status, _, raw = fx.request(); self.assertEqual(status, 200); body = json.loads(raw)
        session = fx.root / 'data/sessions' / ('sess_' + body['cookie'])
        self.assertEqual((session.stat().st_uid, session.stat().st_gid), (fx.account.pw_uid, fx.account.pw_gid))
        expired = fx.root / 'data/sessions/sess_old'; expired.write_text('synthetic expired fixture')
        expired.chmod(0o600); os.chown(expired, fx.account.pw_uid, fx.account.pw_gid); os.utime(expired, (1, 1))
        command('systemctl', 'start', cleaner.unit)
        self.assertFalse(expired.exists()); self.assertTrue(session.exists())
        self.assertEqual(self.identity.observe()['state'], 'SERVICE_IDENTITY_CREATED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(); before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ServiceIdentityLive))
    stable = before == quality.snapshot(ROOT)
    report = {'suite': 'Exclusive service identity with real runtime consumption', 'tests': result.testsRun,
        'expected': 12, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 12 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(before),
        'identity_creation_qualified': result.wasSuccessful() and result.testsRun == 12 and not result.skipped and stable,
        'web_application_qualified': False, 'service_activation_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'IDENTITY-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
