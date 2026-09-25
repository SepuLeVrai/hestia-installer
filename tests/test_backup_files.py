"""Actual filesystem copies/restores; SQL coherence is not claimed by these tests."""
import errno
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from installer import backup_files as b
from installer.maintenance import MaintenanceScope


class DataSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.geteuid(), 0, 'Disposable root host required')
        self.root = Path(tempfile.mkdtemp(prefix='hestia-files-', dir='/var/lib'))
        self.root.chmod(0o755)
        self.addCleanup(lambda: shutil.rmtree(self.root))
        self.uid = self.gid = 65534
        self.ged, self.sessions, self.archive = [self.root / x for x in ('ged', 'sessions', 'archive')]
        for root in (self.ged, self.sessions):
            root.mkdir(mode=0o700)
            os.chown(root, self.uid, self.gid)
        self.archive.mkdir(mode=0o700)
        self.scope = MaintenanceScope(self.root / 'maintenance', self.gid, 'a' * 32)
        self.scope.create(confirmed=True)
        self.lease = self.scope.acquire(confirmed=True)
        self.addCleanup(lambda: self.lease.close())
        self.inventory = b.DataInventory((('ged', self.ged), ('sessions', self.sessions)), self.uid, self.gid)
        self.document = self.ged / 'élève 漢字 & contrat.pdf'
        self.session = self.sessions / 'sess_private-session-identifier'
        self.put(self.document, b'\x00private document\xff\n')
        self.put(self.session, b'user_id|i:17;token|s:12:"private-data";')

    def put(self, path, data):
        path.write_bytes(data)
        os.chown(path, self.uid, self.gid)
        path.chmod(0o600)
        os.utime(path, ns=(1700000000123456789, 1700000000987654321))

    def capture(self, **kwargs):
        return b.capture_and_verify(self.inventory, self.archive, self.lease, confirmed=True, **kwargs)

    def assert_no_receipt(self):
        self.assertFalse(list(self.archive.rglob('verified.json')))
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')

    def test_roundtrip_unicode_empty_large_data_and_all_metadata(self):
        (self.ged / 'vide').mkdir(mode=0o750)
        self.put(self.ged / '.git', b'business data despite its name')
        self.put(self.ged / 'empty', b'')
        self.put(self.ged / 'template.pptx', b'X' * (9 * 1024 * 1024))
        before = {p.relative_to(self.root).as_posix(): b._metadata(p.stat())
                  for r in (self.ged, self.sessions) for p in (r, *r.rglob('*'))}
        snapshot = self.capture()
        for name, metadata in before.items():
            self.assertEqual(b._metadata((self.root / name).stat()), metadata)
        target = self.archive / 'operator-restoration'
        report = snapshot.restore_new(target, self.lease)
        self.assertEqual(report['state'], 'DATA_FILES_RESTORE_VERIFIED')
        for name, metadata in before.items():
            path = target / name
            self.assertEqual(b._metadata(path.stat()), metadata)
            if path.is_file():
                self.assertEqual(path.read_bytes(), (self.root / name).read_bytes())
        self.assertFalse(report['storage_inventory_complete'])
        self.assertFalse(report['database_verified'])
        self.assertFalse(report['restore_to_original_allowed'])
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_session_dates_not_extended_and_private_names_not_reported(self):
        before = b._metadata(self.session.stat())
        snapshot = self.capture()
        self.assertEqual(b._metadata(self.session.stat()), before)
        target = self.archive / 'session-restore'
        snapshot.restore_new(target, self.lease)
        self.assertEqual(b._metadata((target / 'sessions' / self.session.name).stat()), before)
        report = json.dumps(snapshot.report(self.lease))
        for secret in (self.session.name, self.document.name, 'private-data', str(self.ged)):
            self.assertNotIn(secret, report)
        self.assertNotIn(str(self.ged), repr(snapshot))
        self.assertNotIn(str(self.ged), repr(self.inventory))

    def test_confirmation_and_live_lease_required_before_reservation(self):
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_CONSENT_REQUIRED'):
            b.capture_and_verify(self.inventory, self.archive, self.lease, confirmed=False)
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_MAINTENANCE_REQUIRED'):
            b.capture_and_verify(self.inventory, self.archive, None, confirmed=True)
        self.lease.close()
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_MAINTENANCE_REQUIRED'):
            self.capture()
        self.assertEqual(list(self.archive.iterdir()), [])

    def test_resumed_activity_and_new_lease_cannot_restore_old_snapshot(self):
        snapshot = self.capture()
        self.lease.resume(confirmed=True)
        self.lease = self.scope.acquire(confirmed=True)
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_MAINTENANCE_MISMATCH'):
            snapshot.restore_new(self.archive / 'stale', self.lease)
        self.assertFalse((self.archive / 'stale').exists())

    def test_same_interrupted_lease_can_reinspect_saved_snapshot(self):
        snapshot = self.capture()
        lease_id = self.lease.lease_id
        self.lease.close()
        self.lease = self.scope.recover(lease_id, confirmed=True)
        snapshot.verify_sources(self.lease)
        self.assertEqual(snapshot.report(self.lease)['state'], 'DATA_FILES_SNAPSHOT_VERIFIED')

    def test_overlapping_roots_and_untrusted_parent_are_refused(self):
        for roots in ((('first', self.ged), ('second', self.ged)),
                      (('first', self.root),), (('first', self.archive),),
                      (('first', self.scope.directory),), (('bad-label', self.ged),)):
            with self.subTest(roots=roots):
                with self.assertRaises(b.FileSnapshotError):
                    b.capture_and_verify(b.DataInventory(roots, self.uid, self.gid), self.archive, self.lease, confirmed=True)
        self.root.chmod(0o777)
        with self.assertRaises(b.FileSnapshotError):
            self.capture()
        self.root.chmod(0o755)
        self.assertEqual(list(self.archive.iterdir()), [])

    def test_symlink_hardlink_fifo_and_linked_directory_are_refused(self):
        path = self.ged / 'unsafe'
        for kind in ('symlink', 'hardlink', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if kind == 'hardlink':
                    os.link(self.document, path)
                elif kind == 'fifo':
                    os.mkfifo(path)
                else:
                    path.symlink_to(self.sessions if kind == 'directory' else self.session)
                with self.assertRaises(b.FileSnapshotError):
                    self.capture()
                path.unlink()
                self.assert_no_receipt()

    def test_unsafe_modes_foreign_owner_and_executable_data_are_refused(self):
        for mode in (0o666, 0o4750, 0o755):
            with self.subTest(mode=mode):
                self.document.chmod(mode)
                with self.assertRaises(b.FileSnapshotError):
                    self.capture()
                self.document.chmod(0o600)
        os.chown(self.document, 1, 1)
        with self.assertRaises(b.FileSnapshotError):
            self.capture()
        self.assert_no_receipt()

    def test_xattrs_and_real_access_acl_are_not_silently_lost(self):
        os.setxattr(self.document, 'user.fixture', b'non-secret')
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_XATTR_REJECTED'):
            self.capture()
        os.removexattr(self.document, 'user.fixture')
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', tag, perms, who)
            for tag, perms, who in [(1, 6, 0xffffffff), (2, 4, 1), (4, 0, 0xffffffff),
                                     (16, 4, 0xffffffff), (32, 0, 0xffffffff)])
        os.setxattr(self.document, 'system.posix_acl_access', acl)
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_XATTR_REJECTED'):
            self.capture()
        self.assert_no_receipt()

    def test_insufficient_space_keeps_incomplete_slot_and_source(self):
        before = self.document.read_bytes()
        with patch.object(b, 'MIN_FREE_BYTES', 2 ** 63):
            with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_FREE_SPACE_REQUIRED'):
                self.capture()
        self.assertEqual(self.document.read_bytes(), before)
        self.assertTrue(list(self.archive.rglob('attempt.json')))
        self.assert_no_receipt()

    def test_short_writes_are_completed(self):
        original = os.write
        with patch.object(b.os, 'write', side_effect=lambda fd, data: original(fd, data[:7])):
            snapshot = self.capture()
        self.assertEqual(snapshot.report(self.lease)['files'], 2)

    def test_disk_full_during_blob_copy_is_private_and_not_success(self):
        original = b._write
        def write(fd, data):
            if os.readlink('/proc/self/fd/' + str(fd)).endswith('.bin'):
                raise OSError(errno.ENOSPC, 'private fixture error detail')
            return original(fd, data)
        with patch.object(b, '_write', side_effect=write):
            with self.assertRaisesRegex(b.FileSnapshotError, '^FILES_OPERATION_FAILED$'):
                self.capture()
        self.assert_no_receipt()

    def test_change_after_copy_is_not_certified(self):
        original = b._Scan.run
        calls = 0
        def run(scan):
            nonlocal calls
            result = original(scan)
            calls += 1
            if calls == 1:
                self.document.write_bytes(b'changed behind the barrier')
            return result
        with patch.object(b._Scan, 'run', run):
            with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_SOURCE_CHANGED'):
                self.capture()
        self.assert_no_receipt()

    def test_file_replacement_during_read_is_not_certified(self):
        original = os.read
        inode = self.document.stat().st_ino
        replaced = False
        def read(fd, size):
            nonlocal replaced
            data = original(fd, size)
            if not replaced and os.fstat(fd).st_ino == inode:
                replaced = True
                other = self.ged / 'replacement'
                self.put(other, b'replacement')
                os.replace(other, self.document)
            return data
        with patch.object(b.os, 'read', side_effect=read):
            with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_SOURCE_CHANGED'):
                self.capture()
        self.assert_no_receipt()

    def test_manifest_change_and_missing_receipt_are_not_success(self):
        snapshot = self.capture()
        receipt = snapshot._slot / 'verified.json'
        saved = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaises(b.FileSnapshotError):
            snapshot.report(self.lease)
        receipt.write_bytes(saved)
        receipt.chmod(0o600)
        (snapshot._slot / 'manifest.json').write_bytes(b'{}')
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_ARCHIVE_CHANGED'):
            snapshot.restore_new(self.archive / 'corrupt', self.lease)
        self.assertFalse((self.archive / 'corrupt').exists())

    def test_corrupt_truncated_or_hardlinked_blob_cannot_restore(self):
        for kind in ('corrupt', 'truncate', 'hardlink'):
            with self.subTest(kind=kind):
                snapshot = self.capture()
                blob = next((snapshot._slot / 'blobs').iterdir())
                if kind == 'hardlink':
                    os.link(blob, snapshot._slot / 'second-link')
                else:
                    data = blob.read_bytes()
                    blob.write_bytes((b'X' * len(data)) if kind == 'corrupt' else data[:-1])
                with self.assertRaises(b.FileSnapshotError):
                    snapshot.restore_new(self.archive / ('restore-' + kind), self.lease)

    def test_restore_refuses_existing_empty_directory_source_and_public_parent(self):
        snapshot = self.capture()
        occupied = self.archive / 'occupied'
        occupied.mkdir()
        inode = occupied.stat().st_ino
        for target in (occupied, self.ged, self.ged / 'inside', self.root / 'public'):
            with self.subTest(target=target):
                with self.assertRaises(b.FileSnapshotError):
                    snapshot.restore_new(target, self.lease)
        self.assertEqual(occupied.stat().st_ino, inode)
        self.assertEqual(list(occupied.iterdir()), [])
        self.assertFalse((self.root / 'public').exists())

    def test_cancellation_before_capture_or_restore_does_not_create_target(self):
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_INTERRUPTED'):
            self.capture(cancel=event)
        self.assertEqual(list(self.archive.iterdir()), [])
        snapshot = self.capture()
        with self.assertRaisesRegex(b.FileSnapshotError, 'FILES_INTERRUPTED'):
            snapshot.restore_new(self.archive / 'cancelled', self.lease, cancel=event)
        self.assertFalse((self.archive / 'cancelled').exists())

    def test_entry_size_total_and_deadline_limits_are_enforced(self):
        for name, value in (('MAX_ENTRIES', 1), ('MAX_FILE_BYTES', 1),
                            ('MAX_TOTAL_BYTES', 1), ('MAX_SECONDS', 0)):
            with self.subTest(name=name), patch.object(b, name, value):
                with self.assertRaises(b.FileSnapshotError):
                    self.capture()
        self.assert_no_receipt()

    def test_wide_tree_restores_with_bounded_descriptors(self):
        for index in range(150):
            (self.ged / str(index)).mkdir(mode=0o700)
        old = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (96, old[1]))
            snapshot = self.capture()
            snapshot.restore_new(self.archive / 'wide', self.lease)
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, old)
        self.assertEqual(len(list((self.archive / 'wide' / 'ged').iterdir())), 151)

    def test_real_controller_death_retains_attempt_and_maintenance(self):
        lease_id = self.lease.lease_id
        self.lease.close()
        script = '''
import os,signal,sys
from pathlib import Path
from installer import backup_files as b
from installer.maintenance import MaintenanceScope
root=Path(sys.argv[1]);scope=MaintenanceScope(root/'maintenance',65534,'a'*32)
lease=scope.recover(sys.argv[2],confirmed=True)
original=b._new
def write(fd,name,data):
 original(fd,name,data)
 if name=='manifest.json':os.kill(os.getpid(),signal.SIGKILL)
b._new=write
b.capture_and_verify(b.DataInventory((('ged',root/'ged'),('sessions',root/'sessions')),65534,65534),root/'archive',lease,confirmed=True)
'''
        process = subprocess.run([sys.executable, '-c', script, str(self.root), lease_id],
                                 capture_output=True, timeout=30, check=False)
        self.assertEqual(process.returncode, -signal.SIGKILL, process.stderr.decode())
        self.assertTrue(list(self.archive.rglob('manifest.json')))
        self.assertTrue(list(self.archive.rglob('attempt.json')))
        self.assert_no_receipt()
        self.lease = self.scope.recover(lease_id, confirmed=True)
        self.lease.assert_held()

    def test_source_contents_never_executed_and_service_cannot_read_archives(self):
        marker = self.root / 'must-not-exist'
        self.put(self.ged / 'uploaded.php', ('<?php file_put_contents(' + repr(str(marker)) + ',"bad");').encode())
        snapshot = self.capture()
        target = self.archive / 'private-copy'
        snapshot.restore_new(target, self.lease)
        result = subprocess.run(['/usr/bin/setpriv', '--reuid=65534', '--regid=65534', '--clear-groups',
                                 '/usr/bin/test', '-r', str(target / 'sessions' / self.session.name)], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
