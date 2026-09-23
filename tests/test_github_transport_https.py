"""Exercise the real urllib/TLS stack against a local GitHub-shaped HTTPS server."""
import http.server
import io
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from installer.github_client import GitHubClient, REPOSITORIES
from installer.model import InstallerError, SourceSpec
from github_fixture import DUMMY, SHAS, FakeGitHub


class OutboundHTTPSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        cert, key = root / "test-cert.pem", root / "test-key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=api.github.com", "-addext", "subjectAltName=DNS:api.github.com,DNS:codeload.github.com",
                        "-keyout", str(key), "-out", str(cert)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        self.trusted = ssl.create_default_context(cafile=str(cert))
        self.fake = FakeGitHub()
        self.received = []
        fixture, received = self.fake, self.received

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.headers["Host"], self.path, self.headers.get("Authorization"), self.headers.get("Cookie")))
                request = urllib.request.Request("https://" + self.headers["Host"] + self.path)
                result = fixture.open(request, 3)
                data = result.read()
                self.send_response(result.status)
                for name, value in result.headers.items():
                    self.send_header(name, value)
                self.send_header("Set-Cookie", "untrusted-upstream-cookie=value")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = server_context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        original = socket.create_connection

        def route(address, *args, **kwargs):
            self.assertIn(address, {("api.github.com", 443), ("codeload.github.com", 443)})
            return original(("127.0.0.1", self.server.server_port), *args, **kwargs)

        self.routing = patch("socket.create_connection", side_effect=route)
        self.routing.start()

    def tearDown(self):
        self.routing.stop()
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.temp.cleanup()

    def client(self):
        with patch("installer.github_client.ssl.create_default_context", return_value=self.trusted):
            return GitHubClient()

    def test_real_tls_metadata_sha_redirect_and_streaming_download(self):
        client = self.client()
        validated = client.validate_all(DUMMY)
        self.assertEqual(set(validated), {"web", "gateway", "apk"})
        data = io.BytesIO()
        client.download(validated["web"], DUMMY, data)
        self.assertTrue(data.getvalue().startswith(b"\x1f\x8b"))
        self.assertEqual(len(self.received), 8)
        self.assertEqual(self.received[-1][0], "codeload.github.com")
        for host, path, authorization, cookie in self.received:
            self.assertNotIn("?", path)
            self.assertNotIn(DUMMY, path)
            self.assertEqual(authorization, "Bearer " + DUMMY)
            self.assertIsNone(cookie)

    def test_untrusted_server_certificate_is_rejected_before_http(self):
        with self.assertRaises(InstallerError):
            GitHubClient().validate_all(DUMMY)
        self.assertEqual(self.received, [])

    def test_real_redirect_to_other_host_never_makes_second_request(self):
        from github_fixture import Response
        self.fake.override = lambda request: Response(status=302, headers={"Location": "https://evil.invalid/archive"})
        source = SourceSpec(REPOSITORIES["web"], "main", SHAS["web"])
        with self.assertRaises(InstallerError):
            self.client().download(source, DUMMY, io.BytesIO())
        self.assertEqual(len(self.received), 1)
