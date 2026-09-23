import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_httpd
from installer.bootstrap import prepare_bootstrap, serve_in_thread
from installer.engine import TransactionEngine
from installer.model import ErrorCode, InstallerError
from installer.network import verify_port_closed
from installer.operations import OperationRegistry
from installer.service import TransactionService
from installer.transaction import StateJournal
from transaction_fixture import FileOperation


class TransactionHTTPTests(unittest.TestCase):
    # Reuse the live HTTPS harness, not its test methods.
    _connection = test_httpd.HTTPSBootstrapTests._connection
    _unlock = test_httpd.HTTPSBootstrapTests._unlock

    def setUp(self):
        test_httpd.HTTPSBootstrapTests.setUp(self)
        self.operation = FileOperation(self.root)
        self.journal = StateJournal(self.root / "private" / "state.json")
        self.engine = TransactionEngine(self.journal, OperationRegistry((self.operation,)))
        self.service = TransactionService(self.engine)
        self.server.state.transaction_service = self.service
        self.cookie = None
        self.csrf = None

    def tearDown(self):
        self.service.close()
        test_httpd.HTTPSBootstrapTests.tearDown(self)

    def login(self):
        status, _, cookie, _ = self._unlock()
        self.assertEqual(status, 200)
        self.cookie = cookie.split(";", 1)[0]
        status, payload, _ = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.csrf = payload["csrf_token"]

    def request(self, method, path, payload=None, *, headers=None, raw=None):
        values = {"Content-Type": "application/json", "Origin": f"https://127.0.0.1:{self.port}"}
        if self.cookie:
            values["Cookie"] = self.cookie
        if self.csrf:
            values["X-Hestia-CSRF"] = self.csrf
        values.update(headers or {})
        body = raw if raw is not None else (json.dumps(payload) if payload is not None else None)
        connection = self._connection()
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        data = response.read()
        status, result_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, json.loads(data) if data else None, result_headers

    def plan(self):
        status, payload, _ = self.request("POST", "/api/installation/plan", {"modules": ["core"]})
        self.assertEqual(status, 200)
        return payload["installation"]

    @staticmethod
    def confirmation(document):
        return {"confirm": True, "confirmation": document["plan_sha256"]}

    def test_api_requires_authentication_and_creates_no_state(self):
        for method, path, payload in (("GET", "/api/installation/state", None),
                                      ("POST", "/api/installation/plan", {"modules": ["core"]})):
            self.assertEqual(self.request(method, path, payload)[0], 401)
        self.assertFalse(self.journal.path.parent.exists())

    def test_plan_csrf_origin_and_explicit_confirmation(self):
        self.login()
        for headers in ({"X-Hestia-CSRF": ""}, {"Origin": "https://evil.invalid"}):
            self.assertEqual(self.request("POST", "/api/installation/plan", {"modules": ["core"]}, headers=headers)[0], 403)
        self.assertFalse(self.journal.path.parent.exists())
        plan = self.plan()
        for body in ({"confirm": False, "confirmation": plan["plan_sha256"]},
                     {"confirm": 1, "confirmation": plan["plan_sha256"]},
                     {"confirm": True, "confirmation": "b" * 64},
                     {"confirm": True, "confirmation": None}, {}):
            self.assertEqual(self.request("POST", "/api/installation/apply", body)[0], 400)
        self.assertEqual(self.operation.calls("apply"), 0)
        self.assertEqual(self.engine.report(), plan)

    def test_refresh_report_and_repeated_apply_never_replay_done(self):
        self.login()
        status, payload, _ = self.request("GET", "/api/installation/state")
        self.assertEqual((status, payload), (200, {"installation": None}))
        plan = self.plan()
        status, done, _ = self.request("POST", "/api/installation/apply", self.confirmation(plan))
        self.assertEqual((status, done["installation"]["state"]), (200, "DONE"))
        frozen = self.journal.path.read_bytes()
        for _ in range(3):
            status, report, headers = self.request("GET", "/api/installation/report")
            self.assertEqual((status, report), (200, done))
            self.assertIn("no-store", headers["Cache-Control"])
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertEqual(self.request("POST", "/api/installation/apply", self.confirmation(plan))[0], 200)
        self.assertEqual(self.journal.path.read_bytes(), frozen)
        self.assertEqual((self.operation.calls("apply"), self.operation.calls("commit")), (1, 1))

    def test_unknown_operations_parameters_and_secrets_cannot_reach_adapter(self):
        self.login()
        for path in ("/run-command", "/api/run-command", "/api/installation/run-command", "/api/installation/fixture.file"):
            self.assertEqual(self.request("POST", path, {"command": "touch forbidden"})[0], 404)
        for payload in ({"modules": ["web"]}, {"modules": []}, {"modules": ["core"], "token": "fake"},
                        {"modules": ["core"], "path": "/etc/forbidden"}, {"modules": 0}):
            self.assertEqual(self.request("POST", "/api/installation/plan", payload)[0], 400)
        self.assertFalse(self.journal.path.parent.exists())

    def test_strict_json_empty_long_numeric_and_unicode_inputs(self):
        self.login()
        for raw in ('{"modules":["core"],"modules":["web"]}', '{"modules":NaN}', '[]', 'null',
                    '{"modules":1e100000}', '{"modules":-1}', '{"modules":"é<script>"}',
                    '{"modules":"' + 'x' * 17000 + '"}', '[' * 1000 + ']' * 1000, ''):
            status, _, _ = self.request("POST", "/api/installation/plan", raw=raw)
            self.assertIn(status, (400, 413))
        self.assertFalse(self.journal.path.parent.exists())

    def test_duplicate_headers_and_host_mismatch_are_rejected(self):
        self.login()
        for header, first, second in (("Host", f"127.0.0.1:{self.port}", "evil.invalid"),
                                      ("Content-Length", "2", "100"), ("Origin", f"https://127.0.0.1:{self.port}", "https://evil.invalid")):
            conn = self._connection()
            conn.putrequest("POST", "/api/installation/plan", skip_host=header == "Host")
            conn.putheader(header, first)
            conn.putheader(header, second)
            conn.putheader("Cookie", self.cookie)
            conn.putheader("X-Hestia-CSRF", self.csrf)
            conn.endheaders(b"{}")
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 400)
            conn.close()
        self.assertFalse(self.journal.path.parent.exists())

    def test_concurrent_command_is_busy_while_report_remains_available(self):
        self.login()
        self.operation.entered, self.operation.release = threading.Event(), threading.Event()
        plan = self.plan()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.request("POST", "/api/installation/apply", self.confirmation(plan))))
        thread.start()
        try:
            self.assertTrue(self.operation.entered.wait(3))
            self.assertEqual(self.request("GET", "/api/installation/state")[1]["installation"]["state"], "RUNNING")
            status, payload, _ = self.request("POST", "/api/installation/apply", self.confirmation(plan))
            self.assertEqual((status, payload["error"]), (409, "BUSY"))
        finally:
            self.operation.release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][1]["installation"]["state"], "DONE")
        self.assertEqual(self.operation.calls("apply"), 1)

    def test_browser_disconnect_does_not_cancel_or_replay_mutation(self):
        self.login()
        self.operation.entered, self.operation.release = threading.Event(), threading.Event()
        plan = self.plan()
        conn = self._connection()
        conn.request("POST", "/api/installation/apply", body=json.dumps(self.confirmation(plan)), headers={
            "Content-Type": "application/json", "Cookie": self.cookie, "X-Hestia-CSRF": self.csrf,
            "Origin": f"https://127.0.0.1:{self.port}"})
        self.assertTrue(self.operation.entered.wait(3))
        conn.close()  # The server owns execution; the browser does not.
        self.operation.release.set()
        for _ in range(100):
            report = self.engine.report()
            if report["state"] == "DONE":
                break
            time.sleep(0.02)
        self.assertEqual(report["state"], "DONE")
        self.assertEqual(self.request("POST", "/api/installation/resume", self.confirmation(plan))[1]["installation"]["state"], "DONE")
        self.assertEqual(self.operation.calls("apply"), 1)

    def test_retry_and_rollback_are_scoped_and_confirmed(self):
        self.login()
        self.operation.fail = "apply-after"
        plan = self.plan()
        request = self.confirmation(plan)
        failed = self.request("POST", "/api/installation/apply", request)[1]["installation"]
        self.assertEqual(failed["state"], "FAILED")
        self.assertEqual(self.request("POST", "/api/installation/resume", request)[0], 409)
        self.operation.fail = None
        self.assertEqual(self.request("POST", "/api/installation/retry", request | {"name": "first"})[1]["installation"]["state"], "DONE")
        self.assertEqual(self.operation.calls("apply"), 1)
        self.assertEqual(self.request("POST", "/api/installation/rollback", request | {"boundary": "first"})[1]["installation"]["state"], "ROLLED_BACK")
        self.assertFalse(self.operation.target.exists())

    def test_errors_and_unmatched_paths_do_not_leak_credentials(self):
        self.login()
        capture = io.StringIO()
        with contextlib.redirect_stderr(capture):
            self.request("POST", "/api/installation/plan", {"modules": ["core"], "password": "BODY-SECRET"})
            self.request("GET", "/PRIVATE-PATH-SECRET?token=QUERY-SECRET")
            self.request("GET", "/api/installation/state?token=QUERY-SECRET")
        self.assertNotIn("BODY-SECRET", capture.getvalue())
        self.assertNotIn("PRIVATE-PATH-SECRET", capture.getvalue())
        self.assertNotIn("QUERY-SECRET", capture.getvalue())
        self.assertFalse(self.journal.path.parent.exists())


class TransactionLifecycleTests(unittest.TestCase):
    def test_graceful_shutdown_waits_for_active_operation_and_rejects_new_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entered, release = threading.Event(), threading.Event()
            op = FileOperation(root, entered=entered, release=release)
            engine = TransactionEngine(StateJournal(root / "private" / "state.json"), OperationRegistry((op,)))
            plan = engine.plan()
            service = TransactionService(engine)
            results = []
            worker = threading.Thread(target=lambda: results.append(service.execute("apply", {"confirm": True, "confirmation": plan["plan_sha256"]})))
            worker.start()
            self.assertTrue(entered.wait(3))
            stopped = threading.Event()
            closer = threading.Thread(target=lambda: (service.close(), stopped.set()))
            closer.start()
            for _ in range(100):
                with service._condition:
                    if service._closing:
                        break
                time.sleep(0.005)
            try:
                self.assertFalse(stopped.is_set())
                with self.assertRaises(InstallerError) as caught:
                    service.report()
                self.assertEqual(caught.exception.code, ErrorCode.SHUTTING_DOWN)
            finally:
                release.set()
                worker.join(5)
                closer.join(5)
            self.assertTrue(stopped.is_set())
            self.assertEqual(results[0]["installation"]["state"], "DONE")

    @patch("installer.bootstrap.discover_ipv4_candidates", return_value=[])
    def test_restart_reauthenticates_without_destroying_persistent_state(self, discover):
        import http.client
        import ssl
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text("index")
            (web / "bootstrap.html").write_text("gate")
            journal = StateJournal(root / "private" / "state.json")
            op = FileOperation(root)
            engine = TransactionEngine(journal, OperationRegistry((op,)))
            plan = engine.plan()
            engine.apply(plan["plan_sha256"])
            frozen = journal.path.read_bytes()
            old_cookie = None
            for _ in range(2):
                service = TransactionService(TransactionEngine(journal, OperationRegistry((FileOperation(root),))))
                with prepare_bootstrap(web_root=web, runtime_root=root / "runtime", bind_address="127.0.0.1",
                                       interactive=False, transaction_service=service) as prepared:
                    thread = serve_in_thread(prepared)
                    port = prepared.port
                    def request(method, path, payload=None, cookie=None):
                        conn = http.client.HTTPSConnection("127.0.0.1", port, context=ssl._create_unverified_context(), timeout=3)
                        headers = {"Content-Type": "application/json"}
                        if cookie:
                            headers["Cookie"] = cookie
                        conn.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
                        response = conn.getresponse()
                        status, body, new_cookie = response.status, response.read(), response.getheader("Set-Cookie")
                        conn.close()
                        return status, body, new_cookie
                    try:
                        if old_cookie:
                            self.assertEqual(request("GET", "/api/installation/state", cookie=old_cookie)[0], 401)
                        status, _, cookie = request("POST", "/api/bootstrap/unlock", {"code": prepared.bootstrap_code})
                        self.assertEqual(status, 200)
                        old_cookie = cookie.split(";", 1)[0]
                        status, body, _ = request("GET", "/api/installation/state", cookie=old_cookie)
                        self.assertEqual((status, json.loads(body)["installation"]["state"]), (200, "DONE"))
                    finally:
                        prepared.server.shutdown()
                        thread.join(3)
                self.assertTrue(verify_port_closed("127.0.0.1", port))
                self.assertFalse((root / "runtime").exists())
                self.assertEqual(journal.path.read_bytes(), frozen)
                self.assertEqual(op.calls("apply"), 1)
