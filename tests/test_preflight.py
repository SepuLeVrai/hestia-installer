from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from installer.preflight import bootstrap_blockers, read_os_release, run_read_only_preflight


class PreflightTests(unittest.TestCase):
    def _os_release(self, text: str) -> Path:
        self.temp = TemporaryDirectory()
        path = Path(self.temp.name) / "os-release"
        path.write_text(text, encoding="utf-8")
        self.addCleanup(self.temp.cleanup)
        return path

    def test_debian_12_is_supported(self) -> None:
        path = self._os_release('ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12"\n')
        results = run_read_only_preflight(
            os_release_path=path,
            geteuid=lambda: 0,
            which=lambda command: f"/usr/bin/{command}",
        )
        self.assertTrue(next(item for item in results if item.name == "os").ok)
        self.assertEqual(bootstrap_blockers(results), [])

    def test_non_root_is_blocker_only_for_bootstrap(self) -> None:
        path = self._os_release('ID=debian\nVERSION_ID="13"\nPRETTY_NAME="Debian GNU/Linux 13"\n')
        results = run_read_only_preflight(
            os_release_path=path,
            geteuid=lambda: 1000,
            which=lambda command: f"/usr/bin/{command}",
        )
        root = next(item for item in results if item.name == "root")
        self.assertFalse(root.required_for_bootstrap)
        self.assertEqual([item.name for item in bootstrap_blockers(results)], ["root"])

    def test_unsupported_os_is_blocker(self) -> None:
        path = self._os_release('ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04"\n')
        results = run_read_only_preflight(
            os_release_path=path,
            geteuid=lambda: 0,
            which=lambda command: f"/usr/bin/{command}",
        )
        self.assertIn("os", [item.name for item in bootstrap_blockers(results)])

    def test_os_release_ignores_comments_and_quotes(self) -> None:
        path = self._os_release('# comment\nID="debian"\nVERSION_ID="12"\n')
        values = read_os_release(path)
        self.assertEqual(values["ID"], "debian")


if __name__ == "__main__":
    unittest.main()
