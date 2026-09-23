import copy
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.model import (
    MAX_DOCUMENT_BYTES, ErrorCode, InstallerError, StepSpec, build_plan,
    canonical_bytes, initial_document,
)
from installer.transaction import StateJournal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "private"
        self.journal = StateJournal(self.directory / "state.json")
        self.document = initial_document(build_plan([
            StepSpec("check", "preflight.run", "core", "core", "Contrôle non secret")
        ]))

    def write(self):
        self.journal.write(self.document)

    def next_document(self):
        result = copy.deepcopy(self.document)
        result["revision"] += 1
        return result

    def test_absent_report_creates_no_directory_or_lock(self):
        self.assertIsNone(self.journal.read())
        self.assertFalse(self.directory.exists())

    def test_permissions_and_persistent_lock_inode(self):
        self.write()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.journal.path.stat().st_mode), 0o600)
        lock = self.directory / ".transaction.lock"
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        inode = lock.stat().st_ino
        self.journal.write(self.next_document(), expected_revision=0)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse(list(self.directory.glob(".state-*.tmp")))

    def test_read_existing_directory_does_not_create_a_lock(self):
        self.directory.mkdir(mode=0o700)
        self.assertIsNone(self.journal.read())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_directory_with_broad_permissions_is_rejected_not_chmodded(self):
        for mode in (0o755, 0o777, 0o750):
            with self.subTest(mode=mode):
                self.directory.mkdir(exist_ok=True)
                self.directory.chmod(mode)
                with self.assertRaises(InstallerError):
                    self.write()
                self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), mode)
                self.assertFalse(self.journal.path.exists())

    def test_wrong_file_permissions_fail_closed(self):
        self.write()
        self.journal.path.chmod(0o644)
        with self.assertRaises(InstallerError):
            self.journal.read()
        with self.assertRaises(InstallerError):
            self.journal.write(self.next_document(), expected_revision=0)
        self.assertEqual(stat.S_IMODE(self.journal.path.stat().st_mode), 0o644)

    def test_non_owner_file_is_rejected(self):
        self.write()
        original = os.fstat
        def foreign(fd):
            info = original(fd)
            if stat.S_ISREG(info.st_mode):
                fields = list(info)
                fields[4] = os.geteuid() + 1000
                return os.stat_result(fields)
            return info
        with patch("installer.transaction.os.fstat", side_effect=foreign):
            with self.assertRaises(InstallerError):
                self.journal.read()

    def test_symlink_directory_and_ancestor_are_refused(self):
        real = self.root / "real"
        real.mkdir(mode=0o700)
        self.directory.symlink_to(real, target_is_directory=True)
        for path in (self.directory / "state.json", self.directory / "child" / "state.json"):
            with self.subTest(path=path):
                journal = StateJournal(path)
                with self.assertRaises(InstallerError):
                    journal.write(self.document)
                with self.assertRaises(InstallerError):
                    journal.read()
        self.assertEqual(list(real.iterdir()), [])

    def test_symlink_final_file_and_dangling_link_do_not_modify_victim(self):
        self.directory.mkdir(mode=0o700)
        victim = self.root / "victim"
        victim.write_bytes(b"untouched")
        victim.chmod(0o600)
        for target in (victim, self.root / "absent"):
            self.journal.path.symlink_to(target)
            with self.assertRaises(InstallerError):
                self.write()
            with self.assertRaises(InstallerError):
                self.journal.read()
            self.assertTrue(self.journal.path.is_symlink())
            self.journal.path.unlink()
        self.assertEqual(victim.read_bytes(), b"untouched")
        self.assertFalse((self.root / "absent").exists())

    def test_symlink_lock_is_refused(self):
        self.directory.mkdir(mode=0o700)
        victim = self.root / "lock-victim"
        victim.write_bytes(b"untouched")
        (self.directory / ".transaction.lock").symlink_to(victim)
        with self.assertRaises(InstallerError):
            self.write()
        self.assertEqual(victim.read_bytes(), b"untouched")

    def test_hardlinked_state_and_lock_are_refused(self):
        for filename in ("state.json", ".transaction.lock"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                private = Path(tmp) / "private"
                private.mkdir(mode=0o700)
                victim = Path(tmp) / "victim"
                victim.write_bytes(canonical_bytes(self.document))
                victim.chmod(0o600)
                os.link(victim, private / filename)
                before = victim.read_bytes()
                with self.assertRaises(InstallerError):
                    StateJournal(private / "state.json").write(self.document)
                self.assertEqual(before, victim.read_bytes())

    def test_fifo_is_refused_without_blocking(self):
        self.directory.mkdir(mode=0o700)
        os.mkfifo(self.journal.path, 0o600)
        with self.assertRaises(InstallerError):
            self.journal.read()

    def test_group_writable_non_sticky_ancestor_is_rejected(self):
        self.root.chmod(0o770)
        with self.assertRaises(InstallerError):
            self.write()
        self.assertFalse(self.directory.exists())

    def test_atypical_safe_path_and_traversal_inputs(self):
        journal = StateJournal(self.root / "état de l'équipe (phase 2)" / "state.json")
        journal.write(self.document)
        self.assertEqual(journal.read(), self.document)
        for name in ("relative/state.json", "/tmp/../state.json", "/tmp/secret\n/state.json", "/tmp/.state"):
            with self.subTest(name=name), self.assertRaises(InstallerError):
                StateJournal(Path(name))

    def test_cas_refuses_stale_revision_and_replacement_plan(self):
        self.write()
        new = self.next_document()
        self.journal.write(new, expected_revision=0)
        with self.assertRaises(InstallerError):
            self.journal.write(new, expected_revision=0)
        with self.assertRaises(InstallerError):
            self.journal.write(self.document)
        other = initial_document(build_plan([
            StepSpec("check", "preflight.run", "core", "core", "Autre plan")
        ]))
        other["revision"] = 2
        with self.assertRaises(InstallerError):
            self.journal.write(other, expected_revision=1)
        self.assertEqual(self.journal.read(), new)

    def test_lock_contention_is_nonblocking_and_released_on_exception(self):
        self.write()
        with self.journal.locked():
            with self.assertRaises(InstallerError) as caught:
                with self.journal.locked():
                    self.fail("lock acquired twice")
            self.assertEqual(caught.exception.code, ErrorCode.BUSY)
            self.assertEqual(self.journal.read(), self.document)
        with self.assertRaises(RuntimeError):
            with self.journal.locked():
                raise RuntimeError("simulated")
        with self.journal.locked():
            pass

    def test_failed_rename_preserves_old_document_and_cleans_temporary(self):
        self.write()
        with patch("installer.transaction.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(InstallerError):
                self.journal.write(self.next_document(), expected_revision=0)
        self.assertEqual(self.journal.read(), self.document)
        self.assertFalse(list(self.directory.glob(".state-*.tmp")))

    def test_error_after_rename_leaves_complete_new_document(self):
        self.write()
        original = os.replace
        def replace_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("simulated crash after rename")
        with patch("installer.transaction.os.replace", side_effect=replace_then_fail):
            with self.assertRaises(InstallerError):
                self.journal.write(self.next_document(), expected_revision=0)
        self.assertEqual(self.journal.read(), self.next_document())
        self.assertFalse(list(self.directory.glob(".state-*.tmp")))

    def test_data_and_directory_are_fsynced_in_order(self):
        self.write()
        events = []
        original_sync, original_replace = os.fsync, os.replace
        def sync(fd):
            events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "data")
            return original_sync(fd)
        def replace(*args, **kwargs):
            events.append("rename")
            return original_replace(*args, **kwargs)
        with patch("installer.transaction.os.fsync", side_effect=sync), patch("installer.transaction.os.replace", side_effect=replace):
            self.journal.write(self.next_document(), expected_revision=0)
        self.assertEqual(events, ["data", "rename", "directory"])

    def test_corrupt_truncated_oversized_duplicate_and_nan_states_fail_closed(self):
        self.write()
        good = self.journal.path.read_bytes()
        for bad in (b"", good[:50], b"{}", b"[]", b"null", b"\xff", b'{"x":1,"x":2}',
                    b'{"x":NaN}', b"[" * 1100 + b"]" * 1100, b" " * (MAX_DOCUMENT_BYTES + 1)):
            with self.subTest(prefix=repr(bad[:30])):
                self.journal.path.write_bytes(bad)
                with self.assertRaises(InstallerError):
                    self.journal.read()
                self.assertEqual(self.journal.path.read_bytes(), bad)

    def test_invalid_secret_or_versioned_document_is_rejected_before_mkdir(self):
        for changes in ({"password": "example"}, {"schema_version": 100}, {"revision": True},
                        {"revision": -1}, {"revision": 2**100}, {"last_error_redacted": "raw exception"},
                        {"last_error_redacted": {}}, {"state": "UNKNOWN"}):
            bad = copy.deepcopy(self.document)
            bad.update(changes)
            with self.subTest(changes=changes), self.assertRaises(InstallerError):
                self.journal.write(bad)
            self.assertFalse(self.directory.exists())

    def test_readers_never_see_partially_written_json(self):
        self.write()
        errors = []
        stop = threading.Event()
        def reader():
            while not stop.is_set():
                try:
                    result = self.journal.read()
                    self.assertIsInstance(result["revision"], int)
                except BaseException as exc:
                    errors.append(exc)
                    return
        thread = threading.Thread(target=reader)
        thread.start()
        try:
            current = self.document
            for _ in range(20):
                new = copy.deepcopy(current)
                new["revision"] += 1
                self.journal.write(new, expected_revision=current["revision"])
                current = new
        finally:
            stop.set()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.journal.read()["revision"], 20)
