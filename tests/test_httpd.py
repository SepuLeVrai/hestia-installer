import contextlib
import http.client
import io
import json
import shutil
import ssl
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from installer.httpd import BootstrapRequestHandler, BootstrapWebState, ThreadedHTTPSServer, build_ssl_context
from installer.network import reserve_random_port, verify_port_closed
from installer.security import BootstrapToken, SessionStore
from installer.tls import generate_ephemeral_certificate


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL requis pour le test HTTPS")
class HTTPSBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        (self.web / "assets").mkdir(parents=True)
        (self.web / "bootstrap.html").write_text("<!doctype html><title>Bootstrap</title><p>BOOTSTRAP_GATE</p>", encoding="utf-8")
        (self.web / "index.html").write_text("<!doctype html><title>HESTIA</title><p>AUTHENTICATED_HOME</p>", encoding="utf-8")
        (self.web / "assets" / "test.css").write_text("body{}", encoding="utf-8")
        self.material = generate_ephemeral_certificate(self.root, "127.0.0.1")
        self.code, token = BootstrapToken.generate()
        reservation = reserve_random_port("127.0.0.1")
        self.port = reservation.port
        state = BootstrapWebState(
            web_root=self.web,
            bootstrap_token=token,
            session_store=SessionStore(),
            host="127.0.0.1",
            port=self.port,
        )
        self.server = ThreadedHTTPSServer(
            reservation,
            BootstrapRequestHandler,
            ssl_context=build_ssl_context(self.material.certificate, self.material.private_key),
            state=state,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.context = ssl._create_unverified_context()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.temp.cleanup()
        self.assertTrue(verify_port_closed("127.0.0.1", self.port))

    def _connection(self) -> http.client.HTTPSConnection:
        return http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=3)

    def _unlock(self, code: str | None = None):
        conn = self._connection()
        body = json.dumps({"code": code or self.code})
        conn.request(
            "POST",
            "/api/bootstrap/unlock",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": f"https://127.0.0.1:{self.port}",
            },
        )
        response = conn.getresponse()
        payload = response.read()
        cookie = response.getheader("Set-Cookie")
        status = response.status
        headers = dict(response.getheaders())
        conn.close()
        return status, payload, cookie, headers

    def test_unauthenticated_root_redirects_to_gate_with_security_headers(self) -> None:
        conn = self._connection()
        conn.request("GET", "/")
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/bootstrap")
        self.assertEqual(response.getheader("Cache-Control"), "no-store, max-age=0")
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        conn.close()

    def test_unlock_sets_secure_cookie_and_allows_session(self) -> None:
        status, payload, cookie, _headers = self._unlock()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"authenticated": True})
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        cookie_pair = cookie.split(";", 1)[0]

        conn = self._connection()
        conn.request("GET", "/api/session", headers={"Cookie": cookie_pair})
        response = conn.getresponse()
        session_payload = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertTrue(session_payload["authenticated"])
        self.assertTrue(session_payload["csrf_token"])
        conn.close()

    def test_bootstrap_code_cannot_be_reused(self) -> None:
        self.assertEqual(self._unlock()[0], 200)
        self.assertEqual(self._unlock()[0], 401)

    def test_wrong_origin_is_rejected(self) -> None:
        conn = self._connection()
        body = json.dumps({"code": self.code})
        conn.request(
            "POST",
            "/api/bootstrap/unlock",
            body=body,
            headers={"Content-Type": "application/json", "Origin": "https://evil.invalid"},
        )
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 403)
        conn.close()

    def test_host_header_and_query_strings_are_rejected(self) -> None:
        conn = self._connection()
        conn.putrequest("GET", "/bootstrap?code=secret", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{self.port}")
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        conn.close()

        conn = self._connection()
        conn.putrequest("GET", "/bootstrap", skip_host=True)
        conn.putheader("Host", "evil.invalid")
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        conn.close()

    def test_static_path_traversal_cannot_bypass_authentication(self) -> None:
        conn = self._connection()
        conn.request("GET", "/assets/../index.html")
        response = conn.getresponse()
        body = response.read()
        self.assertEqual(response.status, 404)
        self.assertNotIn(b"AUTHENTICATED_HOME", body)
        conn.close()

    def test_get_body_and_oversized_json_are_rejected(self) -> None:
        conn = self._connection()
        conn.request("GET", "/bootstrap", body=b"unexpected", headers={"Content-Length": "10"})
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        conn.close()

        conn = self._connection()
        conn.request(
            "POST",
            "/api/bootstrap/unlock",
            body=b"x",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "999999",
                "Origin": f"https://127.0.0.1:{self.port}",
            },
        )
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 413)
        conn.close()

    def test_secret_in_rejected_query_is_not_logged(self) -> None:
        capture = io.StringIO()
        with contextlib.redirect_stderr(capture):
            conn = self._connection()
            conn.request("GET", "/bootstrap?code=SUPERSECRET")
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 400)
            conn.close()
        self.assertNotIn("SUPERSECRET", capture.getvalue())

    def test_logout_requires_csrf(self) -> None:
        status, _payload, cookie, _headers = self._unlock()
        self.assertEqual(status, 200)
        cookie_pair = cookie.split(";", 1)[0]
        conn = self._connection()
        conn.request("GET", "/api/session", headers={"Cookie": cookie_pair})
        response = conn.getresponse()
        csrf = json.loads(response.read())["csrf_token"]
        conn.close()

        conn = self._connection()
        conn.request("POST", "/api/logout", body=b"{}", headers={"Cookie": cookie_pair, "Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 403)
        conn.close()

        conn = self._connection()
        conn.request(
            "POST",
            "/api/logout",
            body=b"{}",
            headers={
                "Cookie": cookie_pair,
                "Content-Type": "application/json",
                "Origin": f"https://127.0.0.1:{self.port}",
                "X-Hestia-CSRF": csrf,
            },
        )
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 204)
        conn.close()


if __name__ == "__main__":
    unittest.main()
