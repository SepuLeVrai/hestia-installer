import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.cli import main
from installer.github_client import REPOSITORIES
from installer.github_sources import GitHubAcquisition
from installer.model import ErrorCode, InstallerError, SourceSpec
from installer.transaction import StateJournal
from github_fixture import DUMMY, SHAS, confirm, make_service, plan_sources, tar_bytes


class SourceEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service, self.fake = make_service(self.root)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def source_path(self, module="web"):
        return self.root / "private" / "sources" / (module + "-" + SHAS[module])

    def test_plan_requires_validated_access_and_makes_no_source_change(self):
        with self.assertRaises(InstallerError) as error:
            self.service.execute("github.plan", {"modules": ["web"], "refs": {}, "mode": "fresh"})
        self.assertEqual(error.exception.code, ErrorCode.SECRET_REQUIRED)
        self.assertFalse((self.root / "private").exists())
        plan = plan_sources(self.service)
        self.assertEqual(plan["state"], "PLANNED")
        self.assertFalse(self.source_path().parent.exists())
        self.assertEqual(self.fake.archive_requests, [])
        self.assertEqual(plan["plan"]["steps"][0]["source"]["commit_sha"], SHAS["web"])
        for payload in ({}, {"confirm": True, "confirmation": "x" * 64}, {"confirm": 1, "confirmation": plan["plan_sha256"]}):
            with self.assertRaises(InstallerError):
                self.service.execute("apply", payload)
        self.assertFalse(self.source_path().exists())

    def test_all_selections_download_only_selected_modules_fresh_and_upgrade(self):
        for mode in ("fresh", "upgrade"):
            for count in (1, 2, 3):
                for modules in itertools.combinations(REPOSITORIES, count):
                    with self.subTest(mode=mode, modules=modules), tempfile.TemporaryDirectory() as temp:
                        service, fake = make_service(temp)
                        try:
                            plan = plan_sources(service, list(modules), mode)
                            done = service.execute("apply", confirm(plan))["installation"]
                            self.assertEqual(done["state"], "DONE", done["last_error_redacted"])
                            self.assertEqual({m for m, _ in fake.archive_requests}, set(modules))
                            self.assertEqual(len(fake.archive_requests), count)
                            self.assertFalse(service.github_status()["github"]["ready"])
                        finally:
                            service.close()

    def test_repeated_plan_refresh_returns_same_digest_without_credentials(self):
        plan = plan_sources(self.service)
        self.service.execute("github.clear", {})
        before = len(self.fake.requests)
        again = self.service.execute("github.plan", {"modules": ["web"], "refs": {}, "mode": "fresh"})["installation"]
        self.assertEqual(again, plan)
        self.assertEqual(before, len(self.fake.requests))
        for payload in ({"modules": ["gateway"], "refs": {}, "mode": "fresh"},
                        {"modules": ["web"], "refs": {"web": "dev"}, "mode": "fresh"},
                        {"modules": ["web"], "refs": {}, "mode": "upgrade"}):
            with self.assertRaises(InstallerError):
                self.service.execute("github.plan", payload)
        self.assertEqual(self.service.engine.report(), plan)

    def test_repeated_apply_and_restart_done_need_no_token_or_download(self):
        plan = plan_sources(self.service)
        done = self.service.execute("apply", confirm(plan))["installation"]
        before = self.source_path().stat().st_ino
        self.assertEqual(self.service.execute("apply", confirm(plan))["installation"], done)
        second, new_fake = make_service(self.root)
        try:
            self.assertEqual(second.execute("resume", confirm(plan))["installation"], done)
            self.assertEqual(new_fake.requests, [])
            self.assertEqual(self.source_path().stat().st_ino, before)
        finally:
            second.close()

    def test_existing_foreign_source_is_preserved(self):
        plan = plan_sources(self.service)
        self.source_path().mkdir(mode=0o700, parents=True)
        foreign = self.source_path() / "do-not-touch"
        foreign.write_bytes(b"foreign")
        result = self.service.execute("apply", confirm(plan))["installation"]
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(foreign.read_bytes(), b"foreign")
        self.assertEqual(self.fake.archive_requests, [])

    def test_symlink_source_or_ancestor_is_never_followed(self):
        plan = plan_sources(self.service)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        os.symlink(outside, self.source_path().parent)
        result = self.service.execute("apply", confirm(plan))["installation"]
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.fake.archive_requests, [])

    def test_partial_error_cleans_payload_and_targeted_retry_resumes(self):
        plan = plan_sources(self.service, ["web", "gateway"])
        # Sorted gateway fails first, leaving web PLANNED.
        self.fake.archive_override = b"invalid"
        failed = self.service.execute("apply", confirm(plan))["installation"]
        self.assertEqual(failed["state"], "FAILED")
        self.assertEqual(list(p.name for p in self.source_path("gateway").iterdir()), ["owner.json"])
        self.assertFalse(self.service.github_status()["github"]["ready"])
        self.fake.archive_override = None
        self.service.execute("github.validate", {"credential": DUMMY})
        self.assertEqual(self.service.execute("retry", confirm(plan, name="github-gateway"))["installation"]["steps"][0]["state"], "DONE")
        done = self.service.execute("resume", confirm(plan))["installation"]
        self.assertEqual(done["state"], "DONE")
        self.assertEqual([m for m, _ in self.fake.archive_requests], ["gateway", "gateway", "web"])

    def test_rollback_one_boundary_preserves_other_sources(self):
        plan = plan_sources(self.service, ["web", "gateway"])
        self.service.execute("apply", confirm(plan))
        gateway = self.source_path("gateway")
        inode = gateway.stat().st_ino
        result = self.service.execute("rollback", confirm(plan, boundary="github-web"))["installation"]
        self.assertFalse(self.source_path().exists())
        self.assertEqual(gateway.stat().st_ino, inode)
        self.assertEqual(result["steps"][0]["state"], "DONE")
        self.assertEqual(result["steps"][1]["state"], "ROLLED_BACK")
        self.service.execute("rollback", confirm(plan, boundary="github-web"))
        self.assertEqual(gateway.stat().st_ino, inode)

    def test_modified_committed_resource_blocks_forward_and_rollback(self):
        plan = plan_sources(self.service)
        self.service.execute("apply", confirm(plan))
        path = self.source_path() / "tree/README.md"
        path.write_bytes(b"foreign modification")
        with self.assertRaises(InstallerError) as error:
            self.service.execute("resume", confirm(plan))
        self.assertEqual(error.exception.code, ErrorCode.SOURCE_DRIFT)
        result = self.service.execute("rollback", confirm(plan, boundary="github-web"))["installation"]
        self.assertEqual(result["state"], "MANUAL_ACTION_REQUIRED")
        self.assertEqual(path.read_bytes(), b"foreign modification")

    def test_missing_done_sources_are_not_silently_recreated(self):
        import shutil
        plan = plan_sources(self.service)
        self.service.execute("apply", confirm(plan))
        shutil.rmtree(self.source_path())
        count = len(self.fake.archive_requests)
        with self.assertRaises(InstallerError):
            self.service.execute("resume", confirm(plan))
        self.assertEqual(len(self.fake.archive_requests), count)
        self.assertFalse(self.source_path().exists())

    def test_forged_registry_source_or_destination_is_rejected(self):
        plan_sources(self.service)
        document = self.service.engine.report()
        from installer.model import plan_digest, initial_document
        for field, value in (("repository", "evil/repo"), ("commit_sha", "d" * 40)):
            plan = json.loads(json.dumps(document["plan"]))
            plan["steps"][0]["source"][field] = value
            with tempfile.TemporaryDirectory() as temp:
                journal = StateJournal(Path(temp) / "private/state.json")
                journal.write(initial_document(plan))
                with self.assertRaises(InstallerError):
                    make_service(temp)

    def test_invalid_payloads_empty_long_and_numeric_inputs(self):
        self.service.execute("github.validate", {"credential": DUMMY})
        valid = {"modules": ["web"], "refs": {}, "mode": "fresh"}
        for key, value in (("modules", []), ("modules", [False]), ("refs", {"web": "x" * 201}),
                           ("mode", False), ("mode", []), ("mode", 2**128), ("refs", {"web": "a\nb"})):
            payload = {**valid, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(InstallerError):
                self.service.execute("github.plan", payload)
        for key in ("command", "path", "credential", "url"):
            with self.assertRaises(InstallerError):
                self.service.execute("github.plan", {**valid, key: "ignored?"})
        self.assertIsNone(self.service.engine.report())

    def test_secret_never_in_plan_state_manifest_or_logs(self):
        capture = io.StringIO()
        with contextlib.redirect_stderr(capture), contextlib.redirect_stdout(capture):
            plan = plan_sources(self.service)
            self.service.execute("apply", confirm(plan))
        self.assertNotIn(DUMMY, capture.getvalue())
        for path in (self.root / "private").rglob("*.json"):
            self.assertNotIn(DUMMY.encode(), path.read_bytes())
        self.assertNotIn(DUMMY, json.dumps(plan))

    def test_echoed_secret_archive_is_cleaned_and_not_committed(self):
        plan = plan_sources(self.service)
        self.fake.archive_override = tar_bytes(entries=[("text", DUMMY.encode(), 0o644)])
        result = self.service.execute("apply", confirm(plan))["installation"]
        self.assertEqual(result["last_error_redacted"], ErrorCode.SECRET_REJECTED.value)
        self.assertEqual([p.name for p in self.source_path().iterdir()], ["owner.json"])
        self.assertNotIn(DUMMY.encode(), self.service.engine.journal.path.read_bytes())

    def test_concurrent_credential_change_and_apply_are_serialized(self):
        plan = plan_sources(self.service)
        started, release = threading.Event(), threading.Event()
        self.fake.block = lambda: (started.set(), release.wait(3))
        results = []
        worker = threading.Thread(target=lambda: results.append(self.service.execute("apply", confirm(plan))))
        worker.start()
        try:
            self.assertTrue(started.wait(2))
            for action, payload in (("github.clear", {}), ("github.validate", {"credential": DUMMY}), ("apply", confirm(plan))):
                with self.assertRaises(InstallerError) as error:
                    self.service.execute(action, payload)
                self.assertEqual(error.exception.code, ErrorCode.BUSY)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["installation"]["state"], "DONE")

    def test_report_and_dry_run_offline_keep_approved_source_plan(self):
        plan = plan_sources(self.service)
        self.service.close()
        for option in ("--report", "--dry-run"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), patch("installer.github_client.GitHubClient._open", side_effect=AssertionError("network forbidden")):
                status = main([option, "--state-dir", str(self.root / "private")])
            self.assertEqual(status, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["installation"] if option == "--report" else result["plan"], plan if option == "--report" else plan["plan"])

    def test_actual_process_death_after_apply_commit_and_rollback(self):
        script = r'''
import os, sys
sys.path.insert(0, "tests")
from github_fixture import make_service, DUMMY, confirm
root, phase = sys.argv[1:]
def fault(name, point, event):
    if name == "github-web" and point == phase and event == "after":
        os._exit(73)
service, fake = make_service(root, fault_hook=fault)
plan = service.engine.report()
if phase != "rollback":
    service.execute("github.validate", {"credential": DUMMY})
service.execute("rollback" if phase == "rollback" else "apply", confirm(plan, **({"boundary": "github-web"} if phase == "rollback" else {})))
raise SystemExit(4)
'''
        for phase in ("apply", "commit", "rollback"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                service, fake = make_service(temp)
                plan = plan_sources(service)
                if phase == "rollback":
                    service.execute("apply", confirm(plan))
                service.close()
                result = subprocess.run([sys.executable, "-c", script, temp, phase], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 73, result.stderr.decode())
                resumed, new_fake = make_service(temp)
                try:
                    done = resumed.execute("resume", confirm(plan))["installation"]
                    self.assertEqual(done["state"], "ROLLED_BACK" if phase == "rollback" else "DONE")
                    self.assertEqual(new_fake.requests, [])
                    if phase != "rollback":
                        self.assertEqual(done["commit_shas"], {"github-web:web": SHAS["web"]})
                finally:
                    resumed.close()

    def test_actual_crash_with_partial_download_requires_new_credential(self):
        script = r'''
import os, sys
sys.path.insert(0, "tests")
from github_fixture import make_service, DUMMY, confirm
service, fake = make_service(sys.argv[1])
service.execute("github.validate", {"credential": DUMMY})
def interrupted(source, token, output):
    output.write(b"partial")
    output.flush()
    os._exit(74)
service.github.access.client.download = interrupted
service.execute("apply", confirm(service.engine.report()))
'''
        plan = plan_sources(self.service)
        self.service.close()
        run = subprocess.run([sys.executable, "-c", script, str(self.root)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 74, run.stderr.decode())
        self.service, fake = make_service(self.root)
        failed = self.service.execute("resume", confirm(plan))["installation"]
        self.assertEqual(failed["last_error_redacted"], "SECRET_REQUIRED")
        self.assertEqual(fake.requests, [])
        self.service.execute("github.validate", {"credential": DUMMY})
        done = self.service.execute("retry", confirm(plan, name="github-web"))["installation"]
        self.assertEqual(done["state"], "DONE")
        self.assertEqual(fake.archive_requests, [("web", SHAS["web"])])

    def test_core_phase2_journal_still_resumes_without_migration(self):
        plan = self.service.execute("plan", {"modules": ["core"]})["installation"]
        self.assertNotIn("source", plan["plan"]["steps"][0])
        second, _ = make_service(self.root)
        try:
            self.assertEqual(second.engine.report(), plan)
            self.assertEqual(second.engine.registry.specs()[0].operation, "preflight.run")
        finally:
            second.close()

    def test_original_phase2_preflight_contract_remains_byte_compatible(self):
        from installer.operations import OperationRegistry, PreflightOperation
        self.service.engine.registry = OperationRegistry((PreflightOperation(legacy=True),))
        plan = self.service.execute("plan", {"modules": ["core"]})["installation"]
        self.assertEqual(plan["plan"]["steps"][0]["adapter_version"], 1)
        self.assertIn("acquisition GitHub", plan["plan"]["steps"][0]["warnings"][0])
        second, _ = make_service(self.root)
        try:
            self.assertEqual(second.engine.registry.specs()[0].as_dict(), plan["plan"]["steps"][0])
            self.assertEqual(second.engine.report(), plan)
        finally:
            second.close()
