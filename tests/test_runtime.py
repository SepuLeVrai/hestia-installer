from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from installer.bootstrap import prepare_bootstrap
from installer.network import IPv4Candidate, verify_port_closed
from installer.runtime import cleanup_staging, create_private_staging


class RuntimeTests(unittest.TestCase):
    def _web_root(self, root: Path) -> Path:
        web = root / "web"
        (web / "assets").mkdir(parents=True)
        (web / "bootstrap.html").write_text("bootstrap", encoding="utf-8")
        (web / "index.html").write_text("index", encoding="utf-8")
        return web

    def test_private_staging_permissions_and_cleanup(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            staging = create_private_staging(root)
            self.assertEqual(staging.stat().st_mode & 0o777, 0o700)
            cleanup_staging(staging, root)
            self.assertFalse(staging.exists())

    def test_cleanup_refuses_path_outside_runtime(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            root.mkdir()
            outside = Path(temp) / "outside"
            outside.mkdir()
            with self.assertRaises(RuntimeError):
                cleanup_staging(outside, root)

    @patch("installer.bootstrap.discover_ipv4_candidates")
    def test_prepare_close_and_restart_leave_no_listener_or_staging(self, discover) -> None:
        discover.return_value = [IPv4Candidate("127.0.0.1", "lo", True)]
        with TemporaryDirectory() as temp:
            base = Path(temp)
            web = self._web_root(base)
            runtime = base / "runtime"

            for _ in range(2):
                prepared = prepare_bootstrap(
                    web_root=web,
                    runtime_root=runtime,
                    bind_address="127.0.0.1",
                    interactive=False,
                )
                port = prepared.port
                staging = prepared.staging_dir
                thread = threading.Thread(target=prepared.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                thread.start()
                prepared.server.shutdown()
                thread.join(timeout=2)
                prepared.close()
                self.assertFalse(staging.exists())
                self.assertTrue(verify_port_closed("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
