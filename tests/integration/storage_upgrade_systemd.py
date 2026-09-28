#!/usr/bin/env python3
"""Actual pinned storage cutover with MariaDB, FPM, Apache, TLS and Ext4.

Faults interrupt real operations; fixture-owned activation follows product gate
authorization. This suite never adopts an unmanaged legacy installation.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import deployed_web_systemd as previous
import business_storage_systemd as business
import test_inode_fence_files as fixture
from installer import storage_upgrade as u, external_fence as ef
from installer import finalization as f, system_drain as drain
from installer.web_releases import LEGACY_COMMIT, STORAGE_COMMIT
from http_runtime_systemd import command

TARGET = None
EVIDENCE = []


class StorageUpgradeLive(previous.DeployedWebLive):
    proxy_user = 'www-data'
    sealed_maintenance = True
    csrf = business.BusinessStorageLive.csrf
    binary = business.BusinessStorageLive.binary
    upload = business.BusinessStorageLive.upload
    photo = business.BusinessStorageLive.photo
    restart_fixture_services = business.BusinessStorageLive.restart_fixture_services

    @classmethod
    def setUpClass(cls):
        if os.environ.get('HESTIA_STORAGE_UPGRADE_TEST') != '1' or os.environ.get('HESTIA_INODE_FENCE_TEST') != '1':
            raise RuntimeError('Explicit disposable upgrade fixture required')
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.http_root = fixture.VOLUME / os.urandom(16).hex()
        self.output = fixture.VOLUME / os.urandom(16).hex(); self.output.mkdir(mode=0o755)
        self.directory = self.output / self.directory.name
        self.backups = fixture.VOLUME / os.urandom(16).hex(); self.backups.mkdir(mode=0o700)

    def stop_services(self):
        if hasattr(self, 'scope'):
            owned = set()
            for name in (ef.PREPARE, ef.MARKER, ef.RELEASE):
                journal = self.scope.directory / name
                if not journal.is_file(): continue
                value = json.loads(journal.read_bytes()); self.assertEqual(value['instance'], self.scope.instance)
                for entry in value['entries']:
                    self.assertIn(Path(entry['path']), ef.PATHS)
                    owned.update((Path(entry['path']), Path(entry['path']).parent / entry['stage']))
            for path in owned:
                if path.exists(): fixture.fixture_clear(path); path.unlink()
        for name in ('webroot', 'output'):
            path = getattr(self, name, None)
            if path is not None and path.exists(): fixture.fixture_clear(path)
        if hasattr(self, 'http_root'): fixture.fixture_clear(self.http_root / 'data')
        super().stop_services()
        for path in (getattr(self, 'output', None), getattr(self, 'backups', None)):
            if path is not None: shutil.rmtree(path, ignore_errors=True)
        if hasattr(self, 'operation'):
            for path in (self.operation.next_web, self.operation.previous):
                if path.exists(): fixture.fixture_clear(path); shutil.rmtree(path)

    def tearDown(self):
        try:
            self.stop_services()
            name = 'hdf_' + hashlib.sha256(self.db.lower().encode()).hexdigest()[:24]
            self.sql([f"DROP USER IF EXISTS `{name}`@'localhost'"])
        finally: super().tearDown()

    def finish(self):
        self.managed()
        self.assertEqual(self.prepare(authority=self.authority)['state'], 'DATABASE_CONFIGURATION_READY')
        result = self.finalize(); self.assertEqual(result['state'], 'WEB_FRESH_FINALIZED', result)
        self.legacy_relative = 'uploads/profiles/legacy.png'
        path = self.webroot / self.legacy_relative
        path.parent.mkdir(mode=0o755, exist_ok=True); path.write_bytes(business.png()); path.chmod(0o644)
        private = self.webroot / 'uploads/ged_documents/archive-Été.txt'
        private.write_bytes('Document conservé 漢字🙂\n'.encode()); private.chmod(0o644)
        for item in (path, private): os.utime(item, ns=(1600000000123456789, 1600000000987654321))
        self.sql([f"UPDATE `{self.db}`.UserInfo SET photo_profil='{self.legacy_relative}',session_timeout_hours=8 WHERE id_user=1"])
        return result

    def ready(self):
        self.start(); self.login()
        self.operation = u.StorageUpgrade(replace(self.runtime, timeout_seconds=120), previous.WEB,
                                         TARGET, self.http_runtime, self.collector)

    def execute(self):
        return self.operation.apply(self.existing(), self.authority, config_root=self.output,
            backup_root=self.backups, confirmed=True, allow_global_read_lock=True)

    def closed(self):
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        for unit in (self.http_runtime.unit('apache'), self.http_runtime.unit('php'), self.collector.unit):
            self.assertTrue(drain._empty_cgroup(unit))
            command('systemctl', 'start', unit)
            self.assertEqual(drain._show(unit)['ActiveState'], 'inactive')

    def incomplete(self, result):
        self.assertEqual(result['state'], 'STORAGE_UPGRADE_INCOMPLETE', result)
        self.closed()
        self.assertEqual(list(self.backups.glob('*/applied.json')), [])
        with self.assertRaises(Exception):
            self.operation.authorize_resume(self.backups, self.scope.observe()['lease_id'], confirmed=True)

    def test_upgrade_actual_login_data_and_new_upload(self):
        self.ready()
        self.assertEqual(self.binary('/' + self.legacy_relative)[:2], (200, business.png()))
        session = self.session(); before_session = session.read_bytes()
        user_before = self.sql(query=f'SELECT * FROM `{self.db}`.UserInfo')
        settings_before = (self.directory / 'assistant.json').read_bytes()
        result = self.execute(); self.assertEqual(result['state'], 'STORAGE_UPGRADE_APPLIED_GATED', result)
        self.assertEqual(self.sql(query=f'SELECT * FROM `{self.db}`.UserInfo'), user_before)
        self.assertEqual(session.read_bytes(), before_session)
        self.assertEqual((self.directory / 'assistant.json').read_bytes(), settings_before)
        self.assertEqual(result['sql_migrations_executed'], 0); self.closed()
        old = self.operation.previous / 'web' / self.legacy_relative
        target = self.http_root / 'data' / self.legacy_relative
        self.assertEqual(target.stat().st_mtime_ns, old.stat().st_mtime_ns)
        self.assertEqual(target.stat().st_uid, self.web.pw_uid)
        result2 = self.operation.authorize_resume(self.backups, result['lease_id'], confirmed=True)
        self.assertEqual(result2['state'], 'STORAGE_UPGRADE_RESUME_AUTHORIZED')
        self.assertEqual(target.read_bytes(), old.read_bytes())
        self.http_runtime = self.operation.target_http; self.collector = self.operation.target_collector
        self.spec = self.http_runtime.spec; self.uploads = self.http_root / 'data/uploads'
        self.restart_fixture_services()
        self.assertEqual(self.binary('/' + self.legacy_relative)[:2], (200, business.png()))
        self.photo(); self.assertFalse(target.exists()); self.assertTrue(old.exists())
        self.assertIn('/login.php', self.request('/logout.php')[3]); self.login()
        self.assertEqual(f._runtime_digest(self.webroot), f.get_release(STORAGE_COMMIT).runtime_sha256)
        EVIDENCE.append({**result, 'real_tls_login_after_upgrade': True, 'existing_session_preserved': True,
            'real_photo_replacement_after_upgrade': True, 'administrator_and_settings_preserved': True})

    def test_upgrade_failure_before_cutover_keeps_source_and_gate(self):
        self.ready(); original = u._event
        def event(slot, number, state):
            original(slot, number, state)
            if state == 'TARGET_PREPARED': raise u.StorageUpgradeError('INJECTED_BEFORE_CUTOVER')
        with patch.object(u, '_event', side_effect=event): result = self.execute()
        self.incomplete(result); self.assertEqual(result['code'], 'INJECTED_BEFORE_CUTOVER')
        self.assertEqual(f._runtime_digest(self.webroot), f.get_release(LEGACY_COMMIT).runtime_sha256)
        with self.assertRaises(u.StorageUpgradeError): self.execute()

    def test_upgrade_failure_after_source_move_stays_gated(self):
        self.ready(); original = u._move
        def move(source, target):
            original(source, target)
            if source == self.webroot: raise u.StorageUpgradeError('INJECTED_AFTER_SOURCE_MOVE')
        with patch.object(u, '_move', side_effect=move): result = self.execute()
        self.incomplete(result); self.assertEqual(result['code'], 'INJECTED_AFTER_SOURCE_MOVE')
        self.assertTrue((self.operation.previous / 'web' / self.legacy_relative).is_file())

    def test_upgrade_wrong_target_rejected_before_drain(self):
        self.ready()
        self.operation.target_source = previous.WEB
        with self.assertRaises(u.StorageUpgradeError): self.execute()
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertNotIn('/login.php', self.request('/index.php')[3])

    def test_upgrade_failed_sql_backup_never_moves_web(self):
        self.ready()
        with patch.object(u.backup.UpgradeBackup, 'create_and_verify', side_effect=u.StorageUpgradeError('INJECTED_BACKUP_FAILURE')):
            result = self.execute()
        self.incomplete(result); self.assertEqual(result['code'], 'INJECTED_BACKUP_FAILURE')
        self.assertFalse(self.operation.previous.exists())
        self.assertEqual(f._runtime_digest(self.webroot), f.get_release(LEGACY_COMMIT).runtime_sha256)

    def test_upgrade_changed_relocated_data_cannot_resume(self):
        self.ready(); result = self.execute()
        self.assertEqual(result['state'], 'STORAGE_UPGRADE_APPLIED_GATED', result)
        target = self.http_root / 'data' / self.legacy_relative
        target.write_bytes(b'corrupted fixture')
        with self.assertRaisesRegex(u.StorageUpgradeError, 'STORAGE_UPGRADE_FILES_CHANGED'):
            self.operation.authorize_resume(self.backups, result['lease_id'], confirmed=True)
        self.closed()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--shard-index', type=int, choices=(0, 1), default=0)
    args = parser.parse_args(); previous.WEB = args.web; TARGET = args.target
    source = quality.snapshot(ROOT)
    names = sorted(n for n in StorageUpgradeLive.__dict__ if n.startswith('test_upgrade_'))
    assert len(names) == 6
    names = names[args.shard_index::2]
    class ImmediateResult(unittest.TextTestResult):
        def addError(self, test, error):
            super().addError(test, error); self.stream.write(self.errors[-1][1]); self.stream.flush()
        def addFailure(self, test, error):
            super().addFailure(test, error); self.stream.write(self.failures[-1][1]); self.stream.flush()
    result = unittest.TextTestRunner(verbosity=2, resultclass=ImmediateResult).run(unittest.TestSuite(StorageUpgradeLive(n) for n in names))
    stable = quality.snapshot(ROOT) == source
    report = {'suite': 'Pinned actual storage upgrade', 'tests': result.testsRun, 'expected': 3, 'test_ids': names,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'source_stable': stable, 'source_files': len(source), 'source_commit': LEGACY_COMMIT, 'target_commit': STORAGE_COMMIT,
        'source_profile': 'SEALED_MANAGED_ROOT_OWNED_WEB', 'rollback_verified': False, 'phase5_complete': False,
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 3 and not result.skipped and stable else 'FAIL'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'STORAGE-UPGRADE-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    (args.report.parent / 'storage-upgrade-success.json').write_bytes(quality.encode(EVIDENCE))
    args.report.write_bytes(quality.encode(report)); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
