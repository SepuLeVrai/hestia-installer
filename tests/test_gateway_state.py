"""Gateway snapshot contracts; real services/Ext4/SQL worker are covered in CI."""
from contextlib import ExitStack
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import gateway_state_fence as g, gateway_state_backup as b
from installer.private import gateway_sqlite_verify as verifier
from installer.gateway_service_runtime import GatewayServiceRuntime
from installer.model import canonical_bytes


class FenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.state = self.root / 'service/state'
        self.state.mkdir(parents=True, mode=0o700)
        (self.state / 'gateway.db').write_bytes(b'fixture-sqlite-bytes')
        (self.state / 'gateway.lock').write_bytes(b'')
        for path in self.state.iterdir(): path.chmod(0o600)
        self.cache = self.state / g.CACHE; self.cache.mkdir(mode=0o700)
        gate = self.root / 'maintenance'; gate.mkdir(mode=0o700)
        self.gate = os.open(gate, os.O_RDONLY | os.O_DIRECTORY); self.addCleanup(os.close, self.gate)
        self.runtime = object.__new__(GatewayServiceRuntime)
        self.runtime.root = self.state.parent
        self.runtime.profile = SimpleNamespace(state=self.state, key_directory=self.root / 'keys')
        self.runtime.web = SimpleNamespace(spec=SimpleNamespace(instance='a' * 32,
            root=self.root / 'web', webroot=self.root / 'html', maintenance_directory=gate))
        self.runtime.account = Mock(return_value=SimpleNamespace(pw_uid=0, pw_gid=0))
        self.runtime.state_directory = lambda: os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.barrier = Mock(); self.barrier.assert_held = Mock(); self.barrier._profile = b'fixture-profile'
        self.barrier._lease.assert_held = Mock(); self.barrier._lease._directory = self.gate; self.barrier._lease.lease_id = 'b' * 32
        self.flags = {}
        def flags(fd, value=None):
            st = os.fstat(fd); key = (st.st_dev, st.st_ino)
            if value is not None: self.flags[key] = value
            return self.flags.get(key, 0)
        self.flags_call = flags
        for item in (patch.object(g, '_inputs'), patch.object(g.gd, 'binding', return_value={'fixture': True}),
                     patch.object(g.inf, '_ext4', return_value=42), patch.object(g.inf, '_mount_id', return_value=42),
                     patch.object(g.inf, '_flags', side_effect=flags)):
            item.start(); self.addCleanup(item.stop)

    def acquire(self):
        fence = g.acquire(self.runtime, self.barrier, confirmed=True)
        self.addCleanup(fence.close); return fence

    def test_freeze_holds_real_lock_and_close_preserves_durable_barrier(self):
        self.cache.rmdir()  # Legacy state without an initialized optional cache.
        fence = self.acquire(); fence.assert_held()
        with (self.state / 'gateway.lock').open('rb') as foreign:
            with self.assertRaises(BlockingIOError): fcntl.flock(foreign, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fence.close()
        self.assertTrue((self.root / 'maintenance' / g.MARKER).is_file())
        self.assertTrue(all(flags & g.inf.IMMUTABLE for flags in self.flags.values()))
        with self.assertRaises(g.GatewayStateError): fence.assert_held()

    def test_partial_flag_change_recovers_exact_journal_without_unsealing(self):
        changes = []
        def cut(fd, value=None):
            result = self.flags_call(fd, value)
            if value is not None:
                changes.append(value)
                if len(changes) == 1: raise RuntimeError('lost reply')
            return result
        with patch.object(g.inf, '_flags', side_effect=cut), self.assertRaises(g.GatewayStateError): self.acquire()
        raw = (self.root / 'maintenance' / g.MARKER).read_bytes()
        with g.recover(self.runtime, self.barrier, confirmed=True) as fence: fence.assert_held()
        self.assertEqual((self.root / 'maintenance' / g.MARKER).read_bytes(), raw)
        self.assertTrue(all(flags & g.inf.IMMUTABLE for flags in self.flags.values()))

    def test_changed_lease_or_replaced_inode_cannot_recover(self):
        fence = self.acquire(); fence.close()
        self.barrier._lease.lease_id = 'c' * 32
        with self.assertRaisesRegex(g.GatewayStateError, 'RECOVERY_MISMATCH'):
            g.recover(self.runtime, self.barrier, confirmed=True)
        self.barrier._lease.lease_id = 'b' * 32
        replacement = self.state / 'replacement'; replacement.write_bytes(b'fixture-sqlite-bytes'); replacement.chmod(0o600)
        replacement.replace(self.state / 'gateway.db')
        with self.assertRaises(g.GatewayStateError): g.recover(self.runtime, self.barrier, confirmed=True)

    def test_unknown_file_symlink_and_hardlink_refused(self):
        unknown = self.state / 'unregistered'; unknown.write_bytes(b'x')
        with self.assertRaises(g.GatewayStateError): self.acquire()
        unknown.unlink(); db = self.state / 'gateway.db'; other = self.root / 'other'
        db.rename(other); db.symlink_to(other)
        with self.assertRaises(g.GatewayStateError): self.acquire()
        db.unlink(); os.link(other, db)
        with self.assertRaises(g.GatewayStateError): self.acquire()
        self.assertFalse((self.root / 'maintenance' / g.MARKER).exists())

    def test_held_foreign_lock_refuses_before_marker(self):
        with (self.state / 'gateway.lock').open('rb') as foreign:
            fcntl.flock(foreign, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(g.GatewayStateError, 'BUSY'): self.acquire()
        self.assertFalse((self.root / 'maintenance' / g.MARKER).exists())

    def test_unowned_immutable_state_is_never_adopted(self):
        fd = self.runtime.state_directory()
        try: self.flags_call(fd, g.inf.IMMUTABLE)
        finally: os.close(fd)
        with self.assertRaisesRegex(g.GatewayStateError, 'FOREIGN_FENCE'): self.acquire()
        self.assertFalse((self.root / 'maintenance' / g.MARKER).exists())

    def test_removed_flag_and_changed_file_fail_reobservation(self):
        fence = self.acquire(); handle = fence.opened['gateway.db']
        self.flags_call(handle, 0)
        with self.assertRaises(g.GatewayStateError): fence.assert_held()
        self.flags_call(handle, g.inf.IMMUTABLE)
        (self.state / 'gateway.db').write_bytes(b'changed')
        with self.assertRaises(g.GatewayStateError): fence.assert_held()

    def test_editor_cache_directory_and_named_inode_are_frozen_and_reobserved(self):
        logo = self.cache / ('a' * 64 + '.logo'); logo.write_bytes(b'a' * 66); logo.chmod(0o600)
        fence = self.acquire(); fence.assert_held()
        self.assertEqual(set(fence.opened), {'.', 'gateway.db', 'gateway.lock', g.CACHE, g.CACHE + '/' + logo.name})
        self.assertTrue(self.flags_call(fence.opened[g.CACHE]) & g.inf.IMMUTABLE)
        self.assertTrue(self.flags_call(fence.opened[g.CACHE + '/' + logo.name]) & g.inf.IMMUTABLE)
        logo.write_bytes(b'b' * 66)
        with self.assertRaises(g.GatewayStateError): fence.assert_held()

    def test_cache_unknown_symlink_hardlink_and_oversize_refuse_before_marker(self):
        logo = self.cache / ('a' * 64 + '.logo'); other = self.root / 'foreign-logo'
        other.write_bytes(b'a' * 66); other.chmod(0o600)
        for kind in ('unknown', 'symlink', 'hardlink', 'oversize'):
            target = self.cache / 'logo-incomplete.tmp' if kind == 'unknown' else logo
            with self.subTest(kind=kind):
                if kind == 'symlink': target.symlink_to(other)
                elif kind == 'hardlink': os.link(other, target)
                else: target.write_bytes(b'a' * (65602 if kind == 'oversize' else 66)); target.chmod(0o600)
                try:
                    with self.assertRaises(g.GatewayStateError): self.acquire()
                    self.assertFalse((self.root / 'maintenance' / g.MARKER).exists())
                finally: target.unlink()


class SnapshotTests(unittest.TestCase):
    acquire = FenceTests.acquire
    def setUp(self):
        FenceTests.setUp(self); self.backups = self.root / 'backups'; self.backups.mkdir(mode=0o700)
        self.worker = b.p.PhpRuntime(Path('/usr/bin/php8.4'), Path('/usr/lib/php/20240924'),
            902, 902, self.root / 'run', self.root / 'attempts')
        def worker(worker, runtime, input_fd, names, output_fd, cancel):
            handle = b._opened(input_fd, 'gateway.db')
            try: result = b._copy(handle, output_fd, 'database.sqlite', cancel=cancel)
            finally: os.close(handle)
            return {**result, 'sqlite_schema': 6, 'installation_uuid_sha256': 'a' * 64,
                    'logical_sha256': 'b' * 64, 'rows': 7}
        item = patch.object(b, '_worker', side_effect=worker); self.worker_call = item.start(); self.addCleanup(item.stop)

    def snapshot(self): return b.capture(self.acquire(), self.worker, self.backups, confirmed=True)

    def test_completed_snapshot_recovery_preserves_bytes_and_does_not_replay_worker(self):
        saved = self.snapshot(); before = {p: p.read_bytes() for p in saved.slot.rglob('*') if p.is_file()}
        self.worker_call.side_effect = AssertionError('replayed SQLite worker')
        recovered = b.recover_snapshot(saved.fence, self.worker, self.backups, confirmed=True)
        self.assertEqual(saved.raw, recovered.raw)
        self.assertEqual(before, {p: p.read_bytes() for p in saved.slot.rglob('*') if p.is_file()})
        self.assertFalse(saved.manifest['restore_to_original_allowed'])

    def test_damaged_saved_database_and_source_image_are_rejected(self):
        saved = self.snapshot(); target = saved.slot / 'database.sqlite'; original = target.read_bytes()
        target.write_bytes(b'damaged')
        with self.assertRaises(g.GatewayStateError): saved.verify()
        target.write_bytes(original); (saved.slot / 'source/gateway.db').write_bytes(b'damaged')
        with self.assertRaises(g.GatewayStateError): saved.verify()

    def test_incomplete_snapshot_is_not_overwritten_on_explicit_recovery(self):
        fence = self.acquire(); self.worker_call.side_effect = g.GatewayStateError('GATEWAY_BACKUP_SQLITE_REJECTED')
        with self.assertRaises(g.GatewayStateError): b.capture(fence, self.worker, self.backups, confirmed=True)
        slot = next(self.backups.iterdir()); before = (slot / 'attempt.json').read_bytes()
        with self.assertRaisesRegex(g.GatewayStateError, 'GATEWAY_BACKUP_UNAVAILABLE'):
            b.recover_snapshot(fence, self.worker, self.backups, confirmed=True)
        self.assertEqual((slot / 'attempt.json').read_bytes(), before)
        self.assertFalse((slot / 'snapshot.json').exists())

    def test_web_failure_cannot_issue_a_composed_success_receipt(self):
        saved = self.snapshot()
        report = b.cb.CoordinatedVerification(canonical_bytes({'state': 'COORDINATED_BACKUP_INCOMPLETE', 'activity_resumed': False}))
        with self.assertRaisesRegex(g.GatewayStateError, 'WEB_INCOMPLETE'): saved.compose(report)
        self.assertFalse((saved.slot / 'verified.json').exists())

    def test_live_state_or_parent_cannot_be_a_backup_destination(self):
        fence = self.acquire()
        for path in (self.state, self.runtime.root, self.root, self.runtime.web.spec.root):
            with self.subTest(path=path), self.assertRaisesRegex(g.GatewayStateError, 'PATH_REJECTED'):
                b.capture(fence, self.worker, path, confirmed=True)
        self.worker_call.assert_not_called()

    def test_wrong_confirmation_or_cancel_never_copies(self):
        import threading
        fence = self.acquire()
        with self.assertRaisesRegex(g.GatewayStateError, 'CONSENT_REQUIRED'):
            b.capture(fence, self.worker, self.backups, confirmed=False)
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaisesRegex(g.GatewayStateError, 'INTERRUPTED'):
            b.capture(fence, self.worker, self.backups, confirmed=True, cancel=cancelled)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_cache_restore_and_large_manifest_recovery_reject_changed_saved_logo(self):
        for number in range(100):
            logo = self.cache / (format(number, '064x') + '.logo')
            logo.write_bytes(b'a' * 66); logo.chmod(0o600)
        saved = self.snapshot(); self.assertGreater(len(saved.raw), 16384)
        self.assertTrue(saved.manifest['editor_cache'])
        self.worker_call.side_effect = AssertionError('replayed worker')
        recovered = b.recover_snapshot(saved.fence, self.worker, self.backups, confirmed=True)
        self.assertEqual(recovered.raw, saved.raw)
        target = saved.slot / 'source' / g.CACHE / ('0' * 64 + '.logo')
        self.assertEqual(target.read_bytes(), b'a' * 66)
        target.write_bytes(b'changed')
        with self.assertRaisesRegex(g.GatewayStateError, 'BACKUP_CHANGED'): saved.verify()

    def test_copy_refuses_low_space_before_creating_destination(self):
        source = os.open(self.state / 'gateway.db', os.O_RDONLY)
        parent = os.open(self.backups, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with patch.object(b.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)):
                with self.assertRaisesRegex(g.GatewayStateError, 'FREE_SPACE_REQUIRED'):
                    b._copy(source, parent, 'database.sqlite')
            self.assertEqual(list(self.backups.iterdir()), [])
        finally: os.close(source); os.close(parent)


class SQLiteVerifierTests(unittest.TestCase):
    """SQLite fixture operations run in disposable CI, never against a host DB."""
    def database(self, path):
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)')
        con.executemany('INSERT INTO schema_migrations VALUES (?,?)', enumerate(verifier.MIGRATIONS, 1))
        con.execute('CREATE TABLE gateway_metadata(singleton INTEGER PRIMARY KEY, installation_uuid TEXT, created_at INTEGER)')
        con.execute('INSERT INTO gateway_metadata VALUES (1,?,1)', ('12345678-1234-4234-8234-123456789abc',))
        con.execute('CREATE TABLE payload(a TEXT,b BLOB,c INTEGER,d REAL)')
        con.executemany('INSERT INTO payload VALUES (?,?,?,?)', [('Écho', b'\x00\xff', 2, 1.25), ('alpha', b'', -4, None)])
        con.commit(); return con

    def test_normalized_backup_preserves_every_value_and_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); source = self.database(path / 'source'); target = sqlite3.connect(path / 'copy')
            try:
                before = verifier.fingerprint(source); source.backup(target)
                self.assertEqual(before, verifier.fingerprint(target))
                target.execute("UPDATE payload SET a='altered' WHERE c=2"); target.commit()
                self.assertNotEqual(before['logical_sha256'], verifier.fingerprint(target)['logical_sha256'])
                self.assertEqual(before['installation_uuid_sha256'], verifier.fingerprint(target)['installation_uuid_sha256'])
            finally: source.close(); target.close()

    def test_wal_only_committed_rows_survive_isolated_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); source = self.database(path / 'gateway.db')
            try:
                source.execute('PRAGMA journal_mode=WAL'); source.execute('PRAGMA wal_autocheckpoint=0')
                source.execute("INSERT INTO payload VALUES ('wal-only', X'12', 3, 2.5)"); source.commit()
                expected = verifier.fingerprint(source); stage = path / 'stage'; stage.mkdir()
                import shutil
                for name in ('gateway.db', 'gateway.db-wal'): shutil.copyfile(path / name, stage / name)
                copied = verifier.connect(stage / 'gateway.db', time.monotonic() + 10)
                try: self.assertEqual(verifier.fingerprint(copied), expected)
                finally: copied.close()
            finally: source.close()

    def test_changed_migration_checksum_and_missing_version_are_refused(self):
        con = self.database(':memory:')
        try:
            con.execute("UPDATE schema_migrations SET checksum='forged' WHERE version=6")
            with self.assertRaises(ValueError): verifier.fingerprint(con)
            con.execute('DELETE FROM schema_migrations WHERE version=6')
            with self.assertRaises(ValueError): verifier.fingerprint(con)
        finally: con.close()

    def test_wrong_uuid_and_damaged_database_cannot_validate(self):
        con = self.database(':memory:')
        try:
            con.execute("UPDATE gateway_metadata SET installation_uuid='invalid'")
            with self.assertRaises(ValueError): verifier.fingerprint(con)
        finally: con.close()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad'; path.write_bytes(b'not-sqlite')
            bad = verifier.connect(path, time.monotonic() + 10)
            try:
                with self.assertRaises(sqlite3.DatabaseError): verifier.fingerprint(bad)
            finally: bad.close()
