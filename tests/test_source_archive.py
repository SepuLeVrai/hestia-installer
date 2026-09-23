import gzip
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.github_client import REPOSITORIES
from installer.model import ErrorCode, InstallerError
from installer.source_archive import extract_archive, tree_fingerprint
from github_fixture import DUMMY, SHAS, tar_bytes


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.top = REPOSITORIES["web"].replace("/", "-") + "-" + SHAS["web"][:7]

    def tearDown(self):
        os.close(self.fd)
        self.temp.cleanup()

    def extract(self, data, **kwargs):
        return extract_archive(io.BytesIO(data), self.fd, repository=REPOSITORIES["web"], sha=SHAS["web"], **kwargs)

    def reset(self):
        import shutil
        for entry in self.root.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    def test_valid_archive_exact_bytes_private_modes_and_exec_bit(self):
        proof = self.extract(tar_bytes(entries=[("texte é & 'test.txt", b"UTF-8 accents \xc3\xa9", 0o666), ("bin/go", b"do not run", 0o6777)]))
        self.assertEqual(proof["files"], 2)
        self.assertEqual((self.root / "bin/go").stat().st_mode & 0o7777, 0o700)
        self.assertEqual((self.root / "bin").stat().st_mode & 0o7777, 0o700)
        self.assertEqual((self.root / "texte é & 'test.txt").stat().st_mode & 0o7777, 0o600)
        self.assertEqual(tree_fingerprint(self.fd), proof)

    def test_empty_archive_is_not_success(self):
        with self.assertRaises(InstallerError):
            self.extract(tar_bytes(entries=[]))

    def test_absolute_traversal_windows_and_control_paths_rejected(self):
        for name in ("../outside", "a/../../outside", "/absolute", "a//b", "a/./b", "a\\b", "C:/bad", "a\nb", ".git/config", "A/.GiT/config", "name. "):
            self.reset()
            with self.subTest(path=name), self.assertRaises(InstallerError):
                self.extract(tar_bytes(entries=[(name, b"x", 0o644)]))
        self.assertFalse((self.root.parent / "outside").exists())

    def test_absolute_tar_header_rejected(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            info = tarfile.TarInfo("/outside")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(InstallerError):
            self.extract(output.getvalue())

    def test_symlinks_hardlinks_devices_fifo_and_sparse_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE):
            self.reset()
            info = tarfile.TarInfo(self.top + "/link")
            info.type = kind
            info.linkname = "/etc/passwd"
            with self.subTest(kind=kind), self.assertRaises(InstallerError):
                self.extract(tar_bytes(entries=[info]))

    def test_duplicates_and_case_collisions_do_not_overwrite(self):
        for first, second in (("same", "same"), ("name", "NAME"), ("é", "e\u0301"), ("a/x", "A/y")):
            self.reset()
            with self.subTest(first=first, second=second), self.assertRaises(InstallerError):
                self.extract(tar_bytes(entries=[(first, b"old", 0o644), (second, b"new", 0o644)]))

    def test_file_parent_collision_rejected(self):
        with self.assertRaises(InstallerError):
            self.extract(tar_bytes(entries=[("a", b"file", 0o644), ("a/b", b"child", 0o644)]))

    def test_root_repository_and_commit_must_match(self):
        for data in (tar_bytes("gateway"), tar_bytes(sha="d" * 40), tar_bytes(pax_headers={"comment": "e" * 40})):
            self.reset()
            with self.assertRaises(InstallerError):
                self.extract(data)

    def test_multiple_top_directories_rejected(self):
        info = tarfile.TarInfo("other/x")
        with self.assertRaises(InstallerError):
            self.extract(tar_bytes(entries=[info]))

    def test_long_pax_name_accepted_within_bound(self):
        name = "/".join(["a" * 120] * 5) + "/fichier.txt"
        proof = self.extract(tar_bytes(entries=[(name, b"content", 0o644)]))
        self.assertEqual(proof["files"], 1)
        self.assertEqual((self.root / name).read_bytes(), b"content")

    def test_path_depth_component_and_total_limits(self):
        for name in ("x" * 256, "/".join(["a"] * 34), "/".join(["a" * 250] * 5)):
            self.reset()
            with self.assertRaises(InstallerError):
                self.extract(tar_bytes(entries=[(name, b"x", 0o644)]))

    def test_huge_pax_record_rejected_before_body_allocation(self):
        info = tarfile.TarInfo("pax")
        info.type = tarfile.XHDTYPE
        info.size = 10 * 1024 * 1024
        data = gzip.compress(info.tobuf())
        with self.assertRaises(InstallerError) as error:
            self.extract(data)
        self.assertEqual(error.exception.code, ErrorCode.ARCHIVE_REJECTED)

    def test_unknown_pax_sparse_linkpath_and_size_overrides_rejected(self):
        for field in ("GNU.sparse.map", "linkpath", "size"):
            self.reset()
            with self.assertRaises(InstallerError):
                self.extract(tar_bytes(pax_headers={field: "0"}))

    def test_members_content_and_implicit_directories_counted(self):
        with patch("installer.source_archive.MAX_FILES", 3), self.assertRaises(InstallerError):
            self.extract(tar_bytes(entries=[("a/b/c/d/file", b"x", 0o644)]))
        self.reset()
        with patch("installer.source_archive.MAX_CONTENT_BYTES", 3), self.assertRaises(InstallerError):
            self.extract(tar_bytes(entries=[("a", b"123", 0o644), ("b", b"4", 0o644)]))
        self.reset()
        with patch("installer.source_archive.MAX_FILE_BYTES", 2), self.assertRaises(InstallerError):
            self.extract(tar_bytes(entries=[("a", b"123", 0o644)]))

    def test_compression_bomb_expanded_padding_is_bounded(self):
        data = gzip.compress(b"\0" * 10240)
        with patch("installer.source_archive.MAX_TAR_BYTES", 2048), self.assertRaises(InstallerError):
            self.extract(data)

    def test_crc_truncation_junk_and_hidden_second_archive_rejected(self):
        raw = tar_bytes()
        for data in (raw[:-7], b"not gzip", raw + gzip.compress(b"hidden"), raw + b"junk"):
            self.reset()
            with self.assertRaises(InstallerError):
                self.extract(data)

    def test_known_credential_in_name_or_cross_chunk_content_rejected(self):
        for entries in ([(DUMMY, b"x", 0o644)], [("file", b"a" * (65536 - 10) + DUMMY.encode(), 0o644)]):
            self.reset()
            with self.assertRaises(InstallerError) as error:
                self.extract(tar_bytes(entries=entries), forbidden=(DUMMY.encode(),))
            self.assertEqual(error.exception.code, ErrorCode.SECRET_REJECTED)

    def test_nonempty_destination_refused(self):
        (self.root / "foreign").write_bytes(b"preserve")
        with self.assertRaises(InstallerError):
            self.extract(tar_bytes())
        self.assertEqual((self.root / "foreign").read_bytes(), b"preserve")

    def test_git_submodules_and_lfs_pointers_fail_closed(self):
        for name, content in ((".gitmodules", b"[submodule]"), ("large.bin", b"version https://git-lfs.github.com/spec/v1\noid sha256:abcd\n")):
            self.reset()
            with self.assertRaises(InstallerError) as error:
                self.extract(tar_bytes(entries=[(name, content, 0o644)]))
            self.assertEqual(error.exception.code, ErrorCode.SOURCE_LAYOUT_UNSUPPORTED)

    def test_fingerprint_detects_content_mode_and_added_links(self):
        proof = self.extract(tar_bytes())
        path = self.root / "README.md"
        path.write_bytes(b"changed")
        self.assertNotEqual(tree_fingerprint(self.fd), proof)
        path.chmod(0o644)
        with self.assertRaises(InstallerError):
            tree_fingerprint(self.fd)
        path.chmod(0o600)
        os.symlink("/etc/passwd", self.root / "link")
        with self.assertRaises(InstallerError):
            tree_fingerprint(self.fd)
