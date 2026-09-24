"""Private configuration filesystem and service-identity tests, no SQL server."""
import copy
import json
import sys
import os
from pathlib import Path
import pwd
import shutil
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from installer import database_config as config
from installer import php_transport as p
from installer import sql_accounts as accounts
from test_sql_accounts import local_request


from sql_accounts_fixture import ProtectedConfigurationFixture


class ProtectedDatabaseConfigurationTests(ProtectedConfigurationFixture, unittest.TestCase):
    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_staged_only_private_permissions_and_no_privileged_secret(self, audit):
        self.assertEqual(self.stage(), config.STAGED)
        self.assertFalse((self.webroot / 'includes/db.php').exists())
        self.assertFalse((self.webroot / 'install.lock').exists())
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o750)
        for file in self.directory.iterdir():
            self.assertEqual(file.stat().st_uid, 0); self.assertEqual(file.stat().st_gid, self.web.pw_gid)
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o640); self.assertEqual(file.stat().st_nlink, 1)
            self.assertTrue(self.permission(self.web, '-r', file)); self.assertFalse(self.permission(self.web, '-w', file))
            self.assertFalse(self.permission(self.worker, '-r', file)); self.assertFalse(self.permission(self.other, '-r', file))
            self.assertNotIn(self.credentials._password.encode(), file.read_bytes())
            self.assertNotIn(self.payload['secrets']['admin_password'].encode(), file.read_bytes())
        self.assertFalse(self.permission(self.web, '-w', self.directory))
        self.assertEqual(self.load().stdout, b'MATCH')
        audit.assert_called_once()

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_unicode_php_metacharacters_and_long_password_are_data(self, audit):
        self.payload['secrets']['database_password'] = "  ';die('NO');\\\"<&>é🙂" + 'x' * 900 + '  '
        self.stage(); run = self.load()
        self.assertEqual(run.returncode, 0); self.assertFalse(run.stderr); self.assertEqual(run.stdout, b'MATCH')
        self.assertNotIn(b"die('NO')", (self.directory / 'db.php').read_bytes())

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_existing_web_config_link_and_install_lock_never_evaluated(self, audit):
        for relative in ('includes/db.php', 'install.lock'):
            path = self.webroot / relative
            path.write_text('<?php throw new Exception("NEVER_EXECUTE");')
            with self.assertRaisesRegex(accounts.AccountConfigurationError, 'TARGET_OCCUPIED'): self.stage()
            self.assertEqual(path.read_text(), '<?php throw new Exception("NEVER_EXECUTE");')
            path.unlink()
        path = self.webroot / 'includes/db.php'; path.symlink_to('/not-existing')
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'TARGET_OCCUPIED'): self.stage()
        audit.assert_not_called(); self.assertFalse(list(self.output.iterdir()))

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_upgrade_refuses_before_io_or_sql_and_preserves_files(self, audit):
        original = self.webroot / 'includes/db.php'; original.write_text('existing sentinel')
        value = copy.deepcopy(self.payload); value.update(mode='upgrade', administrator=None)
        value['secrets']['admin_password'] = ''; value['assistant']['action'] = 'preserve'
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'UPGRADE_CONFIGURATION_REFUSED'): self.stage(value)
        self.assertEqual(original.read_text(), 'existing sentinel'); self.assertFalse(list(self.output.iterdir())); audit.assert_not_called()

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_reapplication_different_credentials_and_existing_directory_blocked(self, audit):
        self.stage()
        before = {f.name: f.read_bytes() for f in self.directory.iterdir()}
        self.payload['secrets']['database_password'] = 'another-fixture-password'
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'TARGET_OCCUPIED'): self.stage()
        self.assertEqual(before, {f.name: f.read_bytes() for f in self.directory.iterdir()})
        self.assertEqual(audit.call_count, 1)

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_confirmation_remote_managed_and_port_fail_before_audit(self, audit):
        for value in (False, 1, 'true', None):
            with self.assertRaisesRegex(accounts.AccountConfigurationError, 'CONFIRMATION_REQUIRED'): self.stage(confirmed=value)
        for changes in ({'mode': 'managed'}, {'port': 3307}, {'host': '::1'},
                        {'mode': 'remote', 'host': 'db.example.test', 'tls_ca_file': '/etc/hestia/ca.pem'}):
            candidate = copy.deepcopy(self.payload); candidate['database'].update(changes)
            with self.assertRaises(accounts.AccountConfigurationError): self.stage(candidate)
        audit.assert_not_called(); self.assertFalse(list(self.output.iterdir()))

    def test_failed_timeout_and_forged_partial_audit_create_nothing(self):
        for reply in ({}, {**accounts.VERIFIED, 'policy': 'other'}, {**accounts.VERIFIED, 'state': 'REFUSED'}):
            with patch.object(config, 'audit_local_accounts', return_value=reply), self.assertRaises(accounts.AccountConfigurationError): self.stage()
        with patch.object(config, 'audit_local_accounts', side_effect=accounts.AccountConfigurationError('AUDIT_TIMEOUT')):
            with self.assertRaisesRegex(accounts.AccountConfigurationError, 'AUDIT_TIMEOUT'): self.stage()
        self.assertFalse(list(self.output.iterdir()))

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_writable_linked_wrong_owner_and_nontraversable_paths_refused(self, audit):
        self.output.chmod(0o777)
        with self.assertRaises(accounts.AccountConfigurationError): self.stage()
        self.output.chmod(0o755)
        os.chown(self.output, self.web.pw_uid, self.web.pw_gid)
        with self.assertRaises(accounts.AccountConfigurationError): self.stage()
        os.chown(self.output, 0, 0)
        self.output.chmod(0o700)
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'NOT_TRAVERSABLE'): self.stage()
        self.output.chmod(0o755)
        self.output.rename(self.root / 'real'); self.output.symlink_to(self.root / 'real')
        with self.assertRaises(accounts.AccountConfigurationError): self.stage()
        audit.assert_not_called()

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_acl_and_public_output_paths_refused(self, audit):
        # Actual POSIX ACL (including an extra reader), not chmod-only inference.
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', tag, perm, uid) for tag, perm, uid in
            [(1, 7, 0xffffffff), (2, 4, self.other.pw_uid), (4, 5, 0xffffffff), (16, 5, 0xffffffff), (32, 5, 0xffffffff)])
        os.setxattr(self.output, 'system.posix_acl_default', acl)
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'ACL_REFUSED'): self.stage()
        os.removexattr(self.output, 'system.posix_acl_default')
        self.output = self.webroot / 'includes'
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'PUBLIC_PATH_REFUSED'): self.stage()
        audit.assert_not_called()

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_partial_disk_error_blocks_replay_and_is_not_rollback(self, audit):
        real_write = config._write
        def fail(fd, name, data, gid):
            if name == 'db.php': raise OSError('synthetic disk error with secret-looking data')
            real_write(fd, name, data, gid)
        with patch.object(config, '_write', side_effect=fail), self.assertRaisesRegex(accounts.AccountConfigurationError, '^CONFIGURATION_MANUAL_ACTION$'):
            self.stage()
        self.assertTrue((self.directory / 'database.json').exists())
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertFalse(self.permission(self.web, '-r', self.directory / 'database.json'))
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'TARGET_OCCUPIED'): self.stage()

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_crash_keeps_private_reserved_slot_and_never_restarts(self, audit):
        script = """
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from installer import database_config as c, php_transport as p, sql_accounts as a
value=json.loads(sys.stdin.read())
r=value['runtime']
runtime=p.PhpRuntime(Path(r['php']),Path(r['extension_dir']),r['worker_uid'],r['worker_gid'],Path(r['run_root']),Path(r['state_root']))
real_write=c._write
def die(fd,name,data,gid):
    real_write(fd,name,data,gid)
    os._exit(77)
with patch.object(c,'audit_local_accounts',return_value=a.VERIFIED),patch.object(c,'_write',side_effect=die):
    c.stage_local_database_configuration(runtime,value['payload'],p.ProvisioningCredentials('setup_fixture','setup-sensitive-fixture'),config_root=Path(value['root']),confirmed=True)
"""
        data = {'payload': self.payload, 'root': str(self.output), 'runtime': {
            'php': str(self.runtime.php), 'extension_dir': str(self.runtime.extension_dir),
            'worker_uid': self.worker.pw_uid, 'worker_gid': self.worker.pw_gid,
            'run_root': str(self.run), 'state_root': str(self.runtime.state_root)}}
        process = subprocess.run([sys.executable, '-c', script], input=json.dumps(data).encode(),
                                 capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 77)
        self.assertTrue(self.directory.exists()); self.assertFalse((self.directory / 'state.json').exists())
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'TARGET_OCCUPIED'): self.stage()

    def test_concurrent_stage_has_exactly_one_winner(self):
        barrier = threading.Barrier(2); results = []
        def audit(*args, **kwargs): barrier.wait(timeout=5); return dict(accounts.VERIFIED)
        def stage():
            try: results.append(self.stage()['scope'])
            except accounts.AccountConfigurationError as error: results.append(str(error))
        with patch.object(config, 'audit_local_accounts', side_effect=audit):
            threads = [threading.Thread(target=stage) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(10); self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ['CONFIGURATION_STAGED', 'CONFIGURATION_TARGET_OCCUPIED'])

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_loader_refuses_link_hardlink_bad_permissions_unknown_json(self, audit):
        self.stage(); path = self.directory / 'database.json'; original = path.read_bytes()
        for kind in ('hardlink', 'symlink', 'mode', 'extra', 'root_user', 'duplicate'):
            if kind == 'hardlink': os.link(path, self.directory / 'linked')
            elif kind == 'symlink': path.rename(self.directory / 'real'); path.symlink_to(self.directory / 'real')
            elif kind == 'mode': path.chmod(0o644)
            else:
                value = json.loads(original)
                if kind == 'extra': value['extra'] = 'refused'
                elif kind == 'root_user': value['user'] = 'root'
                path.write_bytes(b'{"version":1,"version":1}' if kind == 'duplicate' else p._json(value))
            run = self.load(); self.assertEqual(run.returncode, 20); self.assertEqual(run.stdout, b'HESTIA_DATABASE_CONFIG_INVALID')
            if kind == 'hardlink': (self.directory / 'linked').unlink()
            if kind == 'symlink': path.unlink(); (self.directory / 'real').rename(path)
            path.write_bytes(original); path.chmod(0o640)

    @patch.object(config, 'audit_local_accounts', return_value=accounts.VERIFIED)
    def test_files_fsynced_before_directory_readable(self, audit):
        events = []; real_chmod = os.fchmod; real_sync = os.fsync
        def chmod(fd, mode): events.append(('mode', mode)); return real_chmod(fd, mode)
        def sync(fd): events.append(('sync', stat.S_ISDIR(os.fstat(fd).st_mode))); return real_sync(fd)
        with patch.object(config.os, 'fchmod', side_effect=chmod), patch.object(config.os, 'fsync', side_effect=sync): self.stage()
        at = events.index(('mode', 0o750))
        self.assertEqual(sum(item == ('sync', False) for item in events[:at]), 3)
        self.assertGreaterEqual(sum(item == ('sync', True) for item in events[at:]), 2)

    def test_audit_uses_private_copy_and_stdin_only(self):
        def exchange(command, wire, stage, timeout, cancel):
            value = json.loads(wire)
            for secret in (self.credentials._password, self.payload['secrets']['database_password']):
                self.assertNotIn(secret, ' '.join(command))
                for path in stage.iterdir(): self.assertNotIn(secret.encode(), path.read_bytes())
            self.assertEqual({p.name for p in stage.iterdir()}, {'bridge.php', 'sql_accounts_policy.php'})
            for path in stage.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
                self.assertEqual(path.stat().st_uid, 0)
            from test_sql_accounts import response
            return 0, p._json(response(value['request_id']))
        with patch.object(p, '_runtime') as runtime, patch.object(p, '_exchange', side_effect=exchange):
            self.assertEqual(accounts.audit_local_accounts(self.runtime, self.payload, self.credentials), accounts.VERIFIED)
        runtime.assert_called_once_with(self.runtime, self.web.pw_name)
        self.assertFalse(list(self.run.iterdir()))

    def test_audit_process_errors_are_fixed_and_private_copies_cleaned(self):
        for code in ('TIMEOUT', 'INTERRUPTED', 'OUTPUT_LIMIT', 'UNEXPECTED_STDERR', 'CHANNEL_FAILED', 'synthetic raw secret'):
            with patch.object(p, '_runtime'), patch.object(p, '_exchange', side_effect=p.TransportError(code)):
                with self.assertRaises(accounts.AccountConfigurationError) as caught:
                    accounts.audit_local_accounts(self.runtime, self.payload, self.credentials)
            self.assertTrue(str(caught.exception).startswith('AUDIT_'))
            self.assertNotIn('synthetic raw secret', str(caught.exception))
            self.assertFalse(list(self.run.iterdir()))

    def test_audit_cancellation_is_before_private_dispatch(self):
        event = threading.Event(); event.set()
        with patch.object(p, '_runtime'), patch.object(p, '_exchange') as exchange:
            with self.assertRaisesRegex(accounts.AccountConfigurationError, 'AUDIT_INTERRUPTED'):
                accounts.audit_local_accounts(self.runtime, self.payload, self.credentials, cancel=event)
        exchange.assert_not_called(); self.assertFalse(list(self.run.iterdir()))

    def test_audit_malformed_secret_response_is_never_echoed(self):
        with patch.object(p, '_runtime'), patch.object(p, '_exchange', return_value=(0, b'secret-raw-output')):
            with self.assertRaisesRegex(accounts.AccountConfigurationError, '^AUDIT_PROTOCOL_REJECTED$'):
                accounts.audit_local_accounts(self.runtime, self.payload, self.credentials)
        self.assertFalse(list(self.run.iterdir()))
