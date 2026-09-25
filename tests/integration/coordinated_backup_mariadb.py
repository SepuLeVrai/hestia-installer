#!/usr/bin/env python3
"""Real SQL + registered files + HTTP coordination; disposable fixtures only."""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(Path(__file__).resolve().parent)]
from installer import backup_files as files
from installer import coordinated_backup as c
from installer import finalization as f
from installer import maintenance as m
from installer import php_transport as p
from installer import upgrade_backup as b
import maintenance_mariadb as previous
from database_step_mariadb import literal

WEB = None


class CoordinatedLive(previous.MaintenanceLive):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_COORDINATED_BACKUP_TEST') != '1':
            raise RuntimeError('Explicit coordinated backup opt-in required')
        previous.WEB = WEB
        super().setUpClass()

    def ready(self, remote=False):
        if remote:
            self.remote()
            self.tls.sql([f'CREATE USER {self.auth_account} IDENTIFIED BY {literal(self.authority._password)} REQUIRE SSL',
                          f'GRANT ALL PRIVILEGES ON *.* TO {self.auth_account} WITH GRANT OPTION'])
            self.addCleanup(lambda: self.tls.sql([f'DROP USER IF EXISTS {self.auth_account}']))
            self.finish()
        else:
            self.managed_ready()
        instance = json.loads((self.directory / 'seal.json').read_bytes())['instance']
        self.scope = m.MaintenanceScope(self.directory / 'maintenance', self.web.pw_gid, instance)
        self.scope.create(confirmed=True)
        self.guard = self.scope.directory / 'request_guard.php'
        self.data = self.root / 'registered-data'
        self.sessions = self.root / 'sessions'
        for path in (self.data, self.sessions):
            path.mkdir(mode=0o700)
            os.chown(path, self.web.pw_uid, self.web.pw_gid)
        self.document = self.data / 'contrat privé 漢字.pdf'
        self.document.write_bytes(b'\x00document fixture\xff\n')
        os.chown(self.document, self.web.pw_uid, self.web.pw_gid)
        self.document.chmod(0o600)
        self.inventory = files.DataInventory((('documents', self.data), ('sessions', self.sessions)),
                                              self.web.pw_uid, self.web.pw_gid)
        self.coordinator = c.CoordinatedBackup(self.runtime, WEB, repository=p.WEB_REPOSITORY, commit=f.WEB_COMMIT)

    def execute(self, lease, **kwargs):
        values = dict(config_root=self.output, backup_root=self.backups, inventory=self.inventory,
                      maintenance=lease, confirmed=True, allow_global_read_lock=True)
        values.update(kwargs)
        return self.coordinator.create_and_verify(self.existing(), self.authority, **values).report()

    def assert_incomplete(self, result):
        self.assertEqual(result['state'], 'COORDINATED_BACKUP_INCOMPLETE', result)
        slot = self.backups / result['backup_id']
        self.assertTrue((slot / 'attempt.json').exists())
        self.assertFalse((slot / 'verified.json').exists())
        self.assertFalse(result['apply_allowed'])
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def session_snapshot(self, root):
        result = {}
        for path in root.iterdir():
            fd = os.open(path, files.REGULAR)
            try:
                data = bytearray()
                for block in iter(lambda: os.read(fd, 65536), b''):
                    data.extend(block)
                result[path.name] = (bytes(data), files._metadata(os.fstat(fd)))
            finally:
                os.close(fd)
        return result

    def test_coordinated_real_sql_documents_session_restore_and_login(self):
        self.ready()
        self.settings('configure', 'coordinated-fixture-key-' + 'K' * 60)
        digest = hashlib.sha256(self.document.read_bytes()).hexdigest()
        self.sql([f"UPDATE `{self.db}`.UserInfo SET prenom='Élise 漢字🙂' WHERE id_user=1",
                  f"INSERT INTO `{self.db}`.App_Config(cle,valeur) VALUES('coordinated_document_sha256','{digest}')"])
        with self.http(prepend=self.guard) as request:
            status, body, _, _ = request('/login.php')
            self.assertEqual(status, 200)
            token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', body).group(1)
            self.assertEqual(request('/login.php', {'csrf_token':token,'identifier':self.payload['administrator']['email'],
                                                   'password':self.payload['secrets']['admin_password']})[0], 200)
            with self.scope.acquire(confirmed=True) as lease:
                before = self.logical_dump()
                source_sessions = self.session_snapshot(self.sessions)
                self.assertTrue(source_sessions)
                result = self.execute(lease)
                self.assertEqual(result['state'], 'COORDINATED_BACKUP_RESTORE_VERIFIED', result)
                self.assertEqual(result['trigger_smoke_verified'], 5)
                self.assertEqual(result['registered_roots'], 2)
                self.assertEqual(self.logical_dump(), before)
                self.assertEqual(self.session_snapshot(self.sessions), source_sessions)
                self.assertEqual(request('/index.php')[0], 503)
                slot = self.backups / result['backup_id']
                manifest = json.loads((slot / 'coordinated.json').read_bytes())
                data = manifest['data_snapshot']
                snapshot = files.FileSnapshot(slot / 'data' / data['snapshot_id'], data['manifest_sha256'],
                                               self.scope.instance, lease.lease_id, self.web.pw_gid)
                target = self.backups / 'independent-file-restoration'
                snapshot.restore_new(target, lease)
                self.assertEqual(self.session_snapshot(target / 'sessions'), source_sessions)
                self.assertEqual((target / 'documents' / self.document.name).read_bytes(), self.document.read_bytes())
                # Disposable fixture activation only: actually serve the restored
                # session bytes, not the untouched originals, with the same cookie.
                os.rename(self.sessions, self.root / 'original-sessions-retained')
                os.rename(target / 'sessions', self.sessions)
                for secret in [self.authority._password, *self.payload['secrets'].values()]:
                    if secret:
                        self.assertNotIn(secret, json.dumps(result))
                self.assertFalse(result['complete_web_backup'])
                self.assertFalse(result['system_wiring_verified'])
                lease.resume(confirmed=True)
            status, _, _, url = request('/index.php')
            self.assertEqual(status, 200)
            self.assertNotIn('login.php', url)
            self.assertIn('login.php', request('/logout.php')[3])

    def test_coordinated_consent_and_wrong_instance_fail_before_any_slot(self):
        self.ready()
        before = self.logical_dump()
        with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_MAINTENANCE_REQUIRED'):
            self.execute(None)
        other = m.MaintenanceScope(self.root / 'wrong-instance', self.web.pw_gid, '1'*32)
        other.create(confirmed=True)
        with other.acquire(confirmed=True) as lease:
            with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_INSTANCE_MISMATCH'):
                self.execute(lease)
        with self.scope.acquire(confirmed=True) as lease:
            for key in ('confirmed', 'allow_global_read_lock'):
                with self.assertRaisesRegex(c.CoordinatedBackupError, 'COORDINATED_CONSENT_REQUIRED'):
                    self.execute(lease, **{key: False})
        self.assertEqual(before, self.logical_dump())
        self.assertFalse(list(self.backups.iterdir()))

    def test_coordinated_changed_file_after_real_sql_restore_is_not_certified(self):
        self.ready()
        original = b.UpgradeBackup.create_and_verify
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result.report()['state'], 'BACKUP_RESTORE_VERIFIED')
            self.document.write_bytes(b'changed by uncoordinated writer')
            return result
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=change, autospec=True):
                result = self.execute(lease)
            self.assert_incomplete(result)
            self.assertEqual(result['code'], 'COORDINATED_FILES_CHANGED')

    def test_coordinated_private_settings_drift_is_not_certified(self):
        self.ready()
        original = b.UpgradeBackup.create_and_verify
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result.report()['state'], 'BACKUP_RESTORE_VERIFIED')
            path = self.directory / 'assistant.json'
            value = json.loads(path.read_bytes())
            value['openai_api_key'] = 'changed-fixture-key-' + 'Q' * 60
            path.write_bytes(p._json(value))
            return result
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=changed, autospec=True):
                result = self.execute(lease)
            self.assert_incomplete(result)
            self.assertEqual(result['code'], 'COORDINATED_ENVELOPE_CHANGED')

    def test_coordinated_same_row_count_sql_change_is_not_certified(self):
        self.ready()
        original = c._recheck
        def change(*args, **kwargs):
            self.sql([f"UPDATE `{self.db}`.UserInfo SET prenom='Uncoordinated SQL write' WHERE id_user=1"])
            return original(*args, **kwargs)
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(c, '_recheck', side_effect=change):
                result = self.execute(lease)
            self.assert_incomplete(result)
            self.assertEqual(result['code'], 'COORDINATED_SQL_CHANGED')

    def test_coordinated_corrupt_sql_archive_retains_private_incomplete_bundle(self):
        self.ready()
        original = b._restore
        def corrupt(runtime, source, slot, *args, **kwargs):
            with (slot / 'database.ndjson').open('ab') as stream:
                stream.write(b'truncated or corrupt archive\n')
            return original(runtime, source, slot, *args, **kwargs)
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(b, '_restore', side_effect=corrupt):
                result = self.execute(lease)
            self.assert_incomplete(result)
            self.assertEqual(result['code'], 'COORDINATED_SQL_BACKUP_FAILED')
            self.assertEqual(len(list((self.backups / result['backup_id'] / 'data').rglob('verified.json'))), 1)

    def test_coordinated_corrupt_data_after_real_sql_restore_cannot_publish(self):
        self.ready()
        original = b.UpgradeBackup.create_and_verify
        def corrupt(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result.report()['state'], 'BACKUP_RESTORE_VERIFIED')
            blob = next(self.backups.glob('*/data/*/blobs/*.bin'))
            blob.write_bytes(b'corrupt data blob')
            return result
        with self.scope.acquire(confirmed=True) as lease:
            with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=corrupt, autospec=True):
                result = self.execute(lease)
            self.assert_incomplete(result)
            self.assertEqual(result['code'], 'COORDINATED_OPERATION_UNAVAILABLE')

    def test_coordinated_closed_lease_after_sql_does_not_publish(self):
        self.ready()
        original = b.UpgradeBackup.create_and_verify
        with self.scope.acquire(confirmed=True) as lease:
            def close(*args, **kwargs):
                result = original(*args, **kwargs)
                lease.close()
                return result
            with patch.object(b.UpgradeBackup, 'create_and_verify', side_effect=close, autospec=True):
                result = self.execute(lease)
            self.assert_incomplete(result)
            self.assertEqual(result['code'], 'COORDINATED_MAINTENANCE_REQUIRED')

    def test_coordinated_real_kill_before_common_receipt_never_reopens_web(self):
        self.ready()
        lease = self.scope.acquire(confirmed=True)
        lease_id = lease.lease_id
        lease.close()
        def child():
            active = self.scope.recover(lease_id, confirmed=True)
            original = b._new_file
            def stop(path, data):
                original(path, data)
                if path.name == 'coordinated.json':
                    os.kill(os.getpid(), signal.SIGKILL)
            with patch.object(b, '_new_file', side_effect=stop):
                self.execute(active)
        process = multiprocessing.get_context('fork').Process(target=child)
        process.start()
        process.join(100)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail('Coordinated child did not reach the intended boundary')
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        slots = list(self.backups.iterdir())
        self.assertEqual(len(slots), 1)
        self.assertTrue((slots[0] / 'coordinated.json').exists())
        self.assertFalse((slots[0] / 'verified.json').exists())
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        with self.scope.recover(lease_id, confirmed=True) as active:
            active.assert_held()
            with self.http(prepend=self.guard) as request:
                self.assertEqual(request('/index.php')[0], 503)

    def test_coordinated_remote_tls_preserved_for_both_sql_reads(self):
        self.ready(remote=True)
        # Compare the actual TLS fixture's database, not the separate local
        # server also created by the shared integration harness.
        def source_digest():
            command = ['mariadb-dump', '--no-defaults', '--socket=' + self.tls.socket, '--user=root',
                       '--skip-comments', '--skip-dump-date', '--skip-lock-tables', '--skip-add-locks',
                       '--compact', '--order-by-primary', '--hex-blob', self.db]
            return hashlib.sha256(subprocess.check_output(command, stderr=subprocess.PIPE, timeout=30)).hexdigest()
        before = source_digest()
        with self.scope.acquire(confirmed=True) as lease:
            result = self.execute(lease)
            self.assertEqual(result['state'], 'COORDINATED_BACKUP_RESTORE_VERIFIED', result)
            self.assertEqual(before, source_digest())
            self.assertEqual(result['trigger_smoke_verified'], 5)
            self.assertFalse(result['apply_allowed'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    WEB = args.web.resolve()
    names = [n for n in unittest.defaultTestLoader.getTestCaseNames(CoordinatedLive) if n.startswith('test_coordinated_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(CoordinatedLive(n) for n in names))
    report = {'suite': 'Coordinated SQL and external data under one maintenance lease',
              'tests': result.testsRun, 'expected': len(names), 'failures': len(result.failures),
              'errors': len(result.errors), 'skips': len(result.skipped),
              'status': 'PASS' if result.wasSuccessful() and result.testsRun == len(names) and names and not result.skipped else 'FAIL'}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    raise SystemExit(report['status'] != 'PASS')
