import contextlib
import io
import json
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import test_httpd
import test_transaction_http
from installer.bootstrap import prepare_bootstrap, serve_in_thread
from installer.github_client import GitHubClient
from installer.model import ErrorCode
from github_fixture import DUMMY, SHAS, confirm, make_service


class GitHubHTTPTests(unittest.TestCase):
    _connection = test_httpd.HTTPSBootstrapTests._connection
    _unlock = test_httpd.HTTPSBootstrapTests._unlock
    login = test_transaction_http.TransactionHTTPTests.login
    request = test_transaction_http.TransactionHTTPTests.request

    def setUp(self):
        test_httpd.HTTPSBootstrapTests.setUp(self)
        self.service, self.fake = make_service(self.root)
        self.server.state.transaction_service = self.service
        self.cookie = self.csrf = None

    def tearDown(self):
        self.service.close()
        test_httpd.HTTPSBootstrapTests.tearDown(self)

    def validate(self):
        status, payload, headers = self.request("POST", "/api/github/validate", {"credential": DUMMY})
        self.assertEqual(status, 200, payload)
        return payload

    def plan(self):
        status, payload, _ = self.request("POST", "/api/github/plan", {"modules": ["web"], "refs": {}, "mode": "fresh"})
        self.assertEqual(status, 200, payload)
        return payload["installation"]

    def test_github_routes_require_session(self):
        for method, route, body in (("GET", "/api/github/status", None),
                                    ("POST", "/api/github/validate", {"credential": DUMMY}),
                                    ("POST", "/api/github/plan", {"modules": ["web"], "refs": {}, "mode": "fresh"}),
                                    ("POST", "/api/github/clear", {})):
            self.assertEqual(self.request(method, route, body)[0], 401)
        self.assertEqual(self.fake.requests, [])
        self.assertFalse((self.root / "private").exists())

    def test_csrf_and_origin_are_checked_before_credential_use(self):
        self.login()
        for headers in ({"X-Hestia-CSRF": ""}, {"Origin": "https://evil.invalid"}):
            self.assertEqual(self.request("POST", "/api/github/validate", {"credential": DUMMY}, headers=headers)[0], 403)
        self.assertEqual(self.fake.requests, [])

    def test_validation_three_repositories_no_persistence_and_no_download(self):
        self.login()
        payload = self.validate()
        self.assertEqual(len(payload["github"]["repositories"]), 3)
        self.assertEqual(self.fake.archive_requests, [])
        self.assertFalse((self.root / "private").exists())
        status, payload, headers = self.request("GET", "/api/github/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["github"]["ready"])
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(DUMMY, json.dumps(payload))

    def test_full_cycle_confirmation_state_and_clear(self):
        self.login()
        self.validate()
        plan = self.plan()
        self.assertEqual(self.fake.archive_requests, [])
        status, _, _ = self.request("POST", "/api/installation/apply", {**confirm(plan), "confirm": False})
        self.assertEqual(status, 400)
        self.assertEqual(self.fake.archive_requests, [])
        status, payload, _ = self.request("POST", "/api/installation/apply", confirm(plan))
        self.assertEqual(status, 200)
        self.assertEqual(payload["installation"]["state"], "DONE")
        self.assertEqual(self.fake.archive_requests, [("web", SHAS["web"])])
        status, payload, _ = self.request("GET", "/api/github/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["github"]["ready"])
        for _ in range(2):
            self.assertEqual(self.request("GET", "/api/installation/state")[1]["installation"]["state"], "DONE")
            self.assertEqual(self.request("POST", "/api/installation/resume", confirm(plan))[0], 200)
        self.assertEqual(len(self.fake.archive_requests), 1)

    def test_logout_forgets_github_credential(self):
        self.login()
        self.validate()
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 204)
        self.assertFalse(self.service.github.access.status()["ready"])
        self.assertEqual(self.request("GET", "/api/github/status")[0], 401)

    def test_explicit_clear_is_csrf_protected_and_effective(self):
        self.login()
        self.validate()
        self.assertEqual(self.request("POST", "/api/github/clear", {}, headers={"X-Hestia-CSRF": ""})[0], 403)
        self.assertTrue(self.service.github.access.status()["ready"])
        self.assertEqual(self.request("POST", "/api/github/clear", {})[0], 200)
        self.assertFalse(self.service.github.access.status()["ready"])

    def test_no_credential_in_response_logs_or_rejected_url(self):
        self.login()
        capture = io.StringIO()
        with contextlib.redirect_stderr(capture):
            payload = self.validate()
            self.assertEqual(self.request("GET", "/api/github/status?credential=" + DUMMY)[0], 400)
            self.assertEqual(self.request("POST", "/api/github/" + DUMMY, {})[0], 404)
        self.assertNotIn(DUMMY, capture.getvalue() + json.dumps(payload))
        self.assertNotIn("fixture-download-capability", capture.getvalue())

    def test_unknown_credential_fields_duplicate_json_and_oversize_are_rejected(self):
        self.login()
        for body in ({}, {"credential": DUMMY, "url": "https://evil.invalid"}, {"username": "user", "password": DUMMY}, {"credential": "x" * 256}):
            self.assertEqual(self.request("POST", "/api/github/validate", body)[0], 400)
        raw = '{"credential":"x","credential":"y"}'
        self.assertEqual(self.request("POST", "/api/github/validate", raw=raw)[0], 400)
        self.assertEqual(self.request("POST", "/api/github/validate", {"credential": "x" * 20000})[0], 413)
        self.assertEqual(self.fake.requests, [])

    def test_production_resume_reopens_https_without_persisting_browser_secret(self):
        self.login()
        self.validate()
        plan = self.plan()
        self.request("POST", "/api/installation/apply", confirm(plan))
        second, fake = make_service(self.root)
        with patch("installer.bootstrap.discover_ipv4_candidates", return_value=[]):
            prepared = prepare_bootstrap(web_root=self.web, runtime_root=self.root / "second-runtime",
                                         bind_address="127.0.0.1", interactive=False, transaction_service=second)
        thread = serve_in_thread(prepared)
        import http.client
        try:
            conn = http.client.HTTPSConnection("127.0.0.1", prepared.port, context=self.context, timeout=3)
            conn.request("GET", "/api/github/status", headers={"Cookie": self.cookie})
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 401)
            conn.close()
            # New authentication is mandatory; the journal still proves DONE.
            self.assertEqual(second.engine.report()["state"], "DONE")
            self.assertFalse(second.github_status()["github"]["ready"])
            self.assertEqual(second.execute("resume", confirm(plan))["installation"]["state"], "DONE")
            self.assertEqual(fake.requests, [])
        finally:
            prepared.server.shutdown()
            thread.join(3)
            prepared.close()
            second.close()
        self.assertTrue((self.root / "private" / "state.json").is_file())

    def test_disconnected_cockpit_does_not_cancel_acquisition(self):
        self.login()
        self.validate()
        plan = self.plan()
        started, release = threading.Event(), threading.Event()
        self.fake.block = lambda: (started.set(), release.wait(3))
        conn = self._connection()
        conn.request("POST", "/api/installation/apply", json.dumps(confirm(plan)), headers={
            "Content-Type": "application/json", "Cookie": self.cookie, "X-Hestia-CSRF": self.csrf,
            "Origin": f"https://127.0.0.1:{self.port}"})
        self.assertTrue(started.wait(2))
        conn.close()
        release.set()
        # close() waits for the bounded operation independently of the browser.
        self.service.close()
        self.assertEqual(self.service.engine.report()["state"], "DONE")
        self.assertEqual(len(self.fake.archive_requests), 1)
