"""Identity policy and durable interruption tests without changing host accounts."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import service_identity as i
from installer.operations import RecoveryDecision


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hestia-identity-core-', dir='/var/lib'))
        self.addCleanup(lambda: shutil.rmtree(self.root))
        self.identity = i.ServiceIdentity('a' * 32); self.identity.directory = self.root / 'journal'
        self.operation = i.ServiceIdentityOperation(self.identity)
        self.plan = i.p._json({'version': 1, 'instance': self.identity.instance})
        self.account = {'uid': 321, 'gid': 322, 'records_sha256': 'b' * 64}

    def fixture(self):
        user = self.identity.user
        return {'passwd': [[user, 'x', '321', '322', 'HESTIA-' + 'a' * 32, '/nonexistent', '/usr/sbin/nologin']],
            'group': [[user, 'x', '322', '']], 'shadow': [[user, '!', '20000', '', '', '', '', '', '']],
            'gshadow': [[user, '!', '', '']]}

    def inspect(self, tables):
        account = (self.identity.user, 'x', 321, 322, 'HESTIA-' + 'a' * 32, '/nonexistent', '/usr/sbin/nologin')
        with patch.object(i, '_tables', return_value=tables), patch.object(i.h, '_identity', return_value=account), \
             patch.object(i.grp, 'getgrnam', return_value=(self.identity.user, 'x', 322, [])), \
             patch.object(self.identity, '_subids_absent'):
            return self.identity._account()

    def staged(self, *, command=None, write=None):
        with patch.object(self.identity, '_plan', return_value=self.plan), \
             patch.object(self.identity, '_absent'), patch.object(self.identity, '_account', return_value=self.account), \
             patch.object(self.identity, '_create_account', side_effect=command):
            if write:
                with patch.object(i.f, '_write', side_effect=write): return self.identity.create(confirmed=True)
            return self.identity.create(confirmed=True)

    def test_instance_is_closed_and_derives_bounded_identity(self):
        for value in ('', 'A' * 32, '../unsafe', 'a' * 31, 'a' * 33, None, 1):
            with self.assertRaises(i.ServiceIdentityError): i.ServiceIdentity(value)
        self.assertEqual(self.identity.user, 'hst-' + 'a' * 24)
        self.assertNotIn(str(self.root), repr(self.identity))

    def test_explicit_consent_precedes_even_host_observation(self):
        with patch.object(self.identity, 'prepare') as prepare:
            for value in (False, None, 1, 'true'):
                with self.assertRaisesRegex(i.ServiceIdentityError, 'CONSENT_REQUIRED'):
                    self.identity.create(confirmed=value)
            prepare.assert_not_called()

    def test_command_has_literal_lock_and_no_shell_stdin_or_inherited_environment(self):
        with patch.object(i.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.identity._create_account()
        args, kw = run.call_args
        self.assertEqual(args[0][-2:], ['--', self.identity.user])
        for option in ('--system', '--user-group', '--no-create-home', '--no-log-init'): self.assertIn(option, args[0])
        self.assertEqual(args[0][args[0].index('--password') + 1], '!')
        self.assertEqual(kw['env'], {'PATH': '/usr/sbin:/usr/bin', 'LANG': 'C'})
        self.assertEqual(kw['stdin'], subprocess.DEVNULL); self.assertNotIn('shell', kw)
        self.assertEqual(kw['timeout'], 30)

    def test_failed_command_keeps_attempt_and_private_error(self):
        with self.assertRaisesRegex(i.ServiceIdentityError, '^SERVICE_IDENTITY_INCOMPLETE$'):
            self.staged(command=RuntimeError('private host details'))
        self.assertEqual((self.identity.directory / 'identity.attempt').read_bytes(), self.plan)
        self.assertFalse((self.identity.directory / 'created.json').exists())
        with patch.object(i.subprocess, 'run', return_value=SimpleNamespace(returncode=9)):
            with self.assertRaisesRegex(i.ServiceIdentityError, 'COMMAND_FAILED'): self.identity._create_account()

    def test_nss_is_local_only_and_rejects_missing_duplicate_or_remote_sources(self):
        i._nss(b'passwd: files systemd\ngroup: files\nshadow: files\ngshadow: files\n')
        for raw in (b'passwd: files\n', b'passwd: files ldap\ngroup: files\nshadow: files',
                    b'passwd: files\npasswd: files\ngroup: files\nshadow: files',
                    b'passwd: compat\ngroup: files\nshadow: files'):
            with self.assertRaises(i.ServiceIdentityError): i._nss(raw)

    def test_database_parser_refuses_duplicate_names_and_malformed_records(self):
        for raw in (b'u:x:1\nu:x:2', b'+:x:1', b'u:x', b':x:1'):
            with self.assertRaises(i.ServiceIdentityError): i._rows(raw, 3)
        self.assertEqual(len(i._rows(b'other:1:2\nother:3:4', 3, unique=False)), 2)

    def test_protected_reader_refuses_symlink_hardlink_and_fifo(self):
        target = self.root / 'source'; target.write_bytes(b'fixture'); target.chmod(0o600)
        link = self.root / 'link'; link.symlink_to(target)
        with self.assertRaises(Exception): i._file(link)
        link.unlink(); os.link(target, link)
        with self.assertRaises(Exception): i._file(target)
        link.unlink(); target.unlink(); os.mkfifo(target, 0o600)
        with self.assertRaises(Exception): i._file(target)

    def test_private_database_permissions_acl_and_size_are_enforced(self):
        path = self.root / 'shadow'; path.write_bytes(b'fixture'); path.chmod(0o644)
        with self.assertRaises(i.ServiceIdentityError): i._file(path, private=True)
        path.chmod(0o600); self.assertEqual(i._file(path, private=True), b'fixture')
        with patch.object(i.fs, '_no_acl', side_effect=RuntimeError('ACL')):
            with self.assertRaises(Exception): i._file(path, private=True)
        path.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        with self.assertRaises(Exception): i._file(path, private=True)

    def test_useradd_hooks_must_be_absent_or_empty_and_never_follow_links(self):
        with patch.object(i, 'ETC', self.root):
            i._hooks(); hooks = self.root / 'shadow-maint/useradd-pre.d'; hooks.mkdir(parents=True)
            i._hooks(); (hooks / 'local-script').write_text('not executed')
            with self.assertRaisesRegex(i.ServiceIdentityError, 'HOOKS_REJECTED'): i._hooks()
            shutil.rmtree(hooks); hooks.symlink_to(self.root)
            with self.assertRaises(Exception): i._hooks()

    def test_local_and_nss_collisions_are_never_adopted(self):
        with patch.object(i, '_tables', return_value=self.fixture()):
            with self.assertRaisesRegex(i.ServiceIdentityError, 'OCCUPIED'): self.identity._absent()
        with patch.object(i, '_tables', return_value={}), patch.object(i.pwd, 'getpwnam', return_value=object()):
            with self.assertRaisesRegex(i.ServiceIdentityError, 'OCCUPIED'): self.identity._absent()

    def test_exact_locked_identity_is_bound_to_numeric_ids(self):
        value = self.inspect(self.fixture())
        self.assertEqual((value['uid'], value['gid']), (321, 322))
        self.assertEqual(len(value['records_sha256']), 64)

    def test_uid_shell_home_gecos_and_alias_drift_are_refused(self):
        for index, value in ((1, '!'), (2, '0'), (3, '0'), (4, 'other'), (5, '/root'), (6, '/bin/bash')):
            tables = self.fixture(); tables['passwd'][0][index] = value
            with self.assertRaises(i.ServiceIdentityError): self.inspect(tables)
        tables = self.fixture(); alias = list(tables['passwd'][0]); alias[0] = 'alias'; tables['passwd'].append(alias)
        with self.assertRaises(i.ServiceIdentityError): self.inspect(tables)

    def test_password_aging_membership_and_group_admin_drift_are_refused(self):
        for key, index, value in (('shadow', 1, ''), ('shadow', 4, '30'), ('group', 3, 'other'), ('gshadow', 2, 'other')):
            tables = self.fixture(); tables[key][0][index] = value
            with self.assertRaises(i.ServiceIdentityError): self.inspect(tables)
        tables = self.fixture(); tables['group'].append(['sudo', 'x', '27', self.identity.user])
        with self.assertRaises(i.ServiceIdentityError): self.inspect(tables)

    def test_subordinate_ranges_for_name_or_numeric_identity_are_refused(self):
        with patch.object(i, 'ETC', self.root):
            self.identity._subids_absent(321)
            path = self.root / 'subuid'
            for value in (self.identity.user, '321'):
                path.write_text(value + ':100000:65536\n'); path.chmod(0o644)
                with self.assertRaises(i.ServiceIdentityError): self.identity._subids_absent(321)

    def test_failure_after_account_creation_before_receipt_is_manual_without_replay(self):
        write = i.f._write
        def fail(fd, name, *args, **kwargs):
            if name == 'created.json': raise OSError('injected receipt failure')
            return write(fd, name, *args, **kwargs)
        with self.assertRaises(i.ServiceIdentityError): self.staged(write=fail)
        with patch.object(self.identity, '_plan', return_value=self.plan), \
             patch.object(self.identity, '_account', return_value=self.account), \
             patch.object(self.identity, '_create_account') as command:
            self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
            command.assert_not_called()

    def test_lost_reply_recovery_is_read_only_and_receipt_binds_identity_and_plan(self):
        result = self.staged()
        context = SimpleNamespace(evidence={'hashes_non_secret': {'service_identity_plan': result['plan_sha256'],
                                                                 'service_identity': result['identity_sha256']}})
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.identity.directory.iterdir()}
        with patch.object(self.identity, '_plan', return_value=self.plan), \
             patch.object(self.identity, '_account', return_value=self.account), \
             patch.object(self.identity, '_create_account') as command:
            self.assertEqual(self.operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
            self.assertTrue(self.operation.validate(context))
            context.evidence['hashes_non_secret']['service_identity'] = 'c' * 64
            self.assertFalse(self.operation.validate(context)); command.assert_not_called()
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.identity.directory.iterdir()})
        self.assertEqual(self.operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)

    def test_existing_journal_and_drift_cannot_be_overwritten(self):
        self.staged()
        with self.assertRaises(i.ServiceIdentityError): self.staged()
        (self.identity.directory / 'extra').write_text('foreign')
        with patch.object(self.identity, '_plan', return_value=self.plan), patch.object(self.identity, '_account', return_value=self.account):
            with self.assertRaises(i.ServiceIdentityError): self.identity.observe()

    def test_plan_change_after_reservation_stops_before_command(self):
        with patch.object(self.identity, '_plan', side_effect=[self.plan, self.plan, b'changed']), \
             patch.object(self.identity, '_absent'), patch.object(self.identity, '_create_account') as command:
            with self.assertRaises(i.ServiceIdentityError): self.identity.create(confirmed=True)
            command.assert_not_called()
        self.assertTrue((self.identity.directory / 'identity.attempt').is_file())


if __name__ == '__main__': unittest.main()
