"""Wizard contracts: closed draft, safe reset and HTTPS gates."""
import contextlib
import io
import json
import os
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import test_github_http
from github_fixture import DUMMY, SHAS, Response, confirm, make_service, plan_sources
from installer.model import ErrorCode, InstallerError
from installer.preflight import CheckResult
from installer.wizard import CHECKS, WizardDraft, preflight_snapshot


def good_checks():
    return [CheckResult(name, True, "never serialize environment values") for name in CHECKS]


class WizardDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service, self.fake = make_service(self.root)
        self.addCleanup(self.service.close)
        self.draft = self.service.wizard

    def test_read_empty_is_non_mutating(self):
        self.assertEqual(self.service.wizard_state()["draft"], WizardDraft.default())
        self.assertFalse((self.root / "private").exists())

    def test_save_survives_service_restart_and_has_strict_permissions(self):
        value = {**WizardDraft.default(), "step": 3, "mode": "upgrade", "modules": ["web", "apk"], "refs": {"web": "dev-Bastien"}}
        saved = self.service.execute("wizard.draft", value)["draft"]
        self.assertEqual(saved["revision"], 1)
        state_dir = self.root / "private"
        self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual((state_dir / "wizard.json").stat().st_mode & 0o777, 0o600)
        other, _ = make_service(self.root)
        self.addCleanup(other.close)
        self.assertEqual(other.wizard.read(), saved)
        self.assertFalse((state_dir / "state.json").exists())

    def test_empty_modules_can_be_saved_but_cannot_be_planned(self):
        self.draft.save({**WizardDraft.default(), "modules": []})
        self.service.execute("github.validate", {"credential": DUMMY})
        with patch("installer.wizard.run_read_only_preflight", side_effect=good_checks):
            with self.assertRaises(InstallerError):
                self.service.execute("wizard.plan", {"modules": [], "mode": "fresh", "refs": {}})
        self.assertIsNone(self.service.engine.report())

    def test_invalid_empty_long_special_numeric_and_secret_fields_create_nothing(self):
        base = WizardDraft.default()
        values = [{}, [], None, {**base, "password": "rejected"}, {**base, "step": True},
                  {**base, "step": -1}, {**base, "step": 4}, {**base, "revision": 10**80},
                  {**base, "modules": ["web", "web"]}, {**base, "mode": "invalid"},
                  {**base, "modules": ["web", 4]}, {**base, "refs": {"web": "a" * 300}},
                  {**base, "refs": {"web": "../main"}}, {**base, "refs": {"web": "<script>"}},
                  {**base, "refs": {"web": "token=forbidden"}}, {**base, "refs": {"apk": "main"}}]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(InstallerError): self.draft.save(value)
        self.assertFalse((self.root / "private").exists())

    def test_known_secret_never_enters_draft(self):
        self.service.execute("github.validate", {"credential": DUMMY})
        with self.assertRaises(InstallerError):
            self.draft.save({**WizardDraft.default(), "refs": {"web": DUMMY}})
        self.assertFalse((self.root / "private").exists())

    def test_stale_tab_cannot_overwrite_recent_choices(self):
        old = WizardDraft.default()
        saved = self.draft.save({**old, "mode": "upgrade"})
        with self.assertRaises(InstallerError) as cm: self.draft.save(old)
        self.assertEqual(cm.exception.code, ErrorCode.BUSY)
        self.assertEqual(self.draft.read(), saved)

    def test_concurrent_writers_one_wins(self):
        def attempt():
            try: return self.draft.save(WizardDraft.default())["revision"]
            except InstallerError: return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, [1, "blocked"])

    def test_symlink_hardlink_and_permissions_refused(self):
        self.draft.save(WizardDraft.default())
        dest = self.root / "private" / "wizard.json"
        original = dest.read_bytes()
        outside = self.root / "outside.json"; outside.write_bytes(original); outside.chmod(0o600)
        for kind in ("symlink", "hardlink", "permissions"):
            dest.unlink(missing_ok=True)
            if kind == "symlink": dest.symlink_to(outside)
            elif kind == "hardlink": os.link(outside, dest)
            else: dest.write_bytes(original); dest.chmod(0o644)
            with self.subTest(kind=kind):
                with self.assertRaises((InstallerError, OSError)): self.draft.read()
                with self.assertRaises((InstallerError, OSError)): self.draft.save(WizardDraft.default())
                self.assertEqual(outside.read_bytes(), original)

    def test_corrupt_duplicate_and_oversize_journal_refused(self):
        self.draft.save(WizardDraft.default())
        path = self.root / "private" / "wizard.json"
        for raw in (b"", b"[]", b'{"revision":1,"revision":2}', b"x" * 8193):
            path.write_bytes(raw)
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(InstallerError): self.draft.read()

    def test_failed_atomic_write_keeps_old_draft(self):
        saved = self.draft.save(WizardDraft.default())
        with patch("installer.wizard.os.replace", side_effect=OSError("fixture failure")):
            with self.assertRaises((InstallerError, OSError)): self.draft.save({**saved, "mode": "upgrade"})
        self.assertEqual(self.draft.read(), saved)
        self.assertFalse(list((self.root / "private").glob(".wizard-*")))

    def test_frozen_plan_blocks_draft_changes(self):
        plan_sources(self.service)
        with self.assertRaises(InstallerError) as cm: self.draft.save(WizardDraft.default())
        self.assertEqual(cm.exception.code, ErrorCode.PLAN_EXISTS)

    def test_reset_plan_requires_confirmation_and_never_downloads(self):
        plan = plan_sources(self.service)
        for payload in ({}, {**confirm(plan), "confirm": 1}, {**confirm(plan), "confirmation": "0" * 64}):
            with self.assertRaises(InstallerError): self.service.execute("wizard.reset-plan", payload)
        self.assertEqual(self.service.engine.report(), plan)
        self.assertIsNone(self.service.execute("wizard.reset-plan", confirm(plan))["installation"])
        self.assertEqual(self.fake.archive_requests, [])
        self.assertTrue((self.root / "private" / ".transaction.lock").exists())

    def test_new_plan_after_reset_rejects_old_approval(self):
        old = plan_sources(self.service)
        self.service.execute("wizard.reset-plan", confirm(old))
        new = self.service.execute("github.plan", {"modules": ["apk"], "refs": {}, "mode": "upgrade"})["installation"]
        self.assertNotEqual(old["plan_sha256"], new["plan_sha256"])
        with self.assertRaises(InstallerError): self.service.execute("apply", confirm(old))
        self.assertEqual(self.fake.archive_requests, [])
        self.assertEqual(self.service.execute("apply", confirm(new))["installation"]["state"], "DONE")

    def test_reset_done_failed_and_rolled_back_always_refused(self):
        plan = plan_sources(self.service)
        self.service.execute("apply", confirm(plan))
        for state in ("DONE", "ROLLED_BACK"):
            if state == "ROLLED_BACK": self.service.execute("rollback", confirm(plan, boundary="github-web"))
            old = self.service.engine.report()
            with self.assertRaises(InstallerError): self.service.execute("wizard.reset-plan", confirm(plan))
            self.assertEqual(self.service.engine.report(), old)

    def test_preflight_has_no_raw_environment_output_and_missing_check_blocks(self):
        values = good_checks()
        values[0] = CheckResult("os", True, DUMMY)
        with patch("installer.wizard.run_read_only_preflight", return_value=values):
            result = self.service.execute("preflight.run", {})["preflight"]
        self.assertTrue(result["ok"])
        self.assertNotIn(DUMMY, json.dumps(result))
        with patch("installer.wizard.run_read_only_preflight", return_value=[]):
            self.assertFalse(preflight_snapshot()["ok"])
        self.assertFalse((self.root / "private").exists())

    def test_wizard_plan_rechecks_machine_and_preserves_journal_on_failure(self):
        self.service.execute("github.validate", {"credential": DUMMY})
        with patch("installer.wizard.run_read_only_preflight", return_value=[]):
            with self.assertRaises(InstallerError):
                self.service.execute("wizard.plan", {"modules": ["web"], "refs": {}, "mode": "fresh"})
        self.assertIsNone(self.service.engine.report())
        with patch("installer.wizard.run_read_only_preflight", side_effect=good_checks):
            plan = self.service.execute("wizard.plan", {"modules": ["web"], "refs": {}, "mode": "fresh"})["installation"]
        self.assertEqual(plan["state"], "PLANNED")
        self.assertEqual(self.fake.archive_requests, [])

    def test_per_repo_validation_failure_is_safe_and_not_falsely_green(self):
        def deny(request):
            if "hestia-mobile-gateway" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 403, "fixture", {}, None)
        self.fake.override = deny
        with self.assertRaises(InstallerError): self.service.execute("github.validate", {"credential": DUMMY})
        result = self.service.github_status()["github"]
        self.assertFalse(result["ready"])
        self.assertEqual([x["status"] for x in result["checks"]], ["ACCESSIBLE", "DENIED", "UNCHECKED"])
        self.assertNotIn(DUMMY, json.dumps(result))
        self.assertEqual(self.service.github_status(), {"github": result})

    def test_expired_github_status_clears_old_green_checks(self):
        self.service.execute("github.validate", {"credential": DUMMY})
        self.service.github.access._expires = 1
        result = self.service.github_status()["github"]
        self.assertFalse(result["ready"])
        self.assertTrue(all(c["status"] == "UNCHECKED" for c in result["checks"]))

    def test_busy_status_remains_readable_during_execution(self):
        self.service._mutation_lock.acquire()
        try: self.assertTrue(self.service.wizard_state()["busy"])
        finally: self.service._mutation_lock.release()
        self.assertFalse(self.service.wizard_state()["busy"])


class WizardHTTPTests(unittest.TestCase):
    setUp = test_github_http.GitHubHTTPTests.setUp
    tearDown = test_github_http.GitHubHTTPTests.tearDown
    _connection = test_github_http.GitHubHTTPTests._connection
    _unlock = test_github_http.GitHubHTTPTests._unlock
    login = test_github_http.GitHubHTTPTests.login
    request = test_github_http.GitHubHTTPTests.request

    def test_new_routes_require_authentication(self):
        for method, route, body in (("GET", "/api/wizard/state", None),
                                    ("POST", "/api/wizard/draft", WizardDraft.default()),
                                    ("POST", "/api/preflight/run", {}),
                                    ("POST", "/api/wizard/plan", {}),
                                    ("POST", "/api/wizard/reset-plan", {})):
            self.assertEqual(self.request(method, route, body)[0], 401)
        self.assertIsNone(self.service.engine.report())

    def test_new_mutations_require_csrf_and_origin(self):
        self.login()
        for route in ("/api/preflight/run", "/api/wizard/draft", "/api/wizard/plan", "/api/wizard/reset-plan"):
            for headers in ({"X-Hestia-CSRF": ""}, {"Origin": "https://evil.invalid"}):
                self.assertEqual(self.request("POST", route, {}, headers=headers)[0], 403)

    def test_draft_roundtrip_refresh_and_rejected_secret_has_no_leak(self):
        self.login()
        draft = WizardDraft.default()
        status, result, headers = self.request("POST", "/api/wizard/draft", draft)
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(self.request("GET", "/api/wizard/state")[1]["draft"], result["draft"])
        capture = io.StringIO()
        with contextlib.redirect_stderr(capture):
            status, result, _ = self.request("POST", "/api/wizard/draft", {**draft, "credential": DUMMY})
        self.assertEqual(status, 400)
        self.assertNotIn(DUMMY, json.dumps(result) + capture.getvalue())
        self.assertIsNone(self.service.engine.report())

    def test_unknown_preflight_arguments_refused(self):
        self.login()
        for value in ({"command": "id"}, {"path": "/etc"}, {"root": False}):
            self.assertEqual(self.request("POST", "/api/preflight/run", value)[0], 400)


if __name__ == "__main__":
    unittest.main()
