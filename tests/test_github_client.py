import contextlib
import io
import json
import ssl
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from installer.github_client import GitHubAccess, GitHubClient, NoRedirect, REPOSITORIES, SECRET_NAME
from installer.model import ErrorCode, InstallerError, SourceSpec
from installer.operations import SecretVault
from github_fixture import DUMMY, SHAS, FakeGitHub, Response


class GitHubClientTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub()
        self.client = GitHubClient(opener=self.fake)
        self.vault = SecretVault()
        self.access = GitHubAccess(self.vault, self.client)
        self.source = SourceSpec(REPOSITORIES["web"], "main", SHAS["web"])

    def test_validates_three_metadata_and_contents_without_archives(self):
        status = self.access.validate(DUMMY)
        self.assertTrue(status["ready"])
        self.assertEqual(len(status["repositories"]), 3)
        self.assertEqual(len(self.fake.requests), 6)
        self.assertEqual(self.fake.archive_requests, [])
        for request in self.fake.requests:
            self.assertEqual(request.get_method(), "GET")
            self.assertNotIn(DUMMY, request.full_url)
            self.assertEqual(request.get_header("Authorization"), "Bearer " + DUMMY)
            self.assertIn("Authorization", request.unredirected_hdrs)
        self.assertNotIn(DUMMY, json.dumps(status))

    def test_invalid_credentials_rejected_without_network(self):
        for value in (None, False, 42, "", " ", "x" * 256, "x" * 19, "a\r\nb" * 20, "é" * 40, "user:password", [DUMMY]):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(InstallerError):
                self.access.validate(value)
        self.assertEqual(self.fake.requests, [])

    def test_metadata_is_not_sufficient_for_contents_permission(self):
        def denied(request):
            if "/commits/" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 403, "private " + DUMMY, Message(), io.BytesIO(DUMMY.encode()))
        self.fake.override = denied
        with self.assertRaises(InstallerError) as error:
            self.access.validate(DUMMY)
        self.assertEqual(str(error.exception), ErrorCode.GITHUB_ACCESS_DENIED.value)
        self.assertFalse(self.access.status()["ready"])
        with self.assertRaises(InstallerError):
            self.vault.require(SECRET_NAME)

    def test_http_errors_are_fixed_codes_and_do_not_echo_bodies(self):
        for status, code, headers in (
            (401, ErrorCode.GITHUB_ACCESS_DENIED, {}), (403, ErrorCode.GITHUB_ACCESS_DENIED, {}),
            (404, ErrorCode.GITHUB_ACCESS_DENIED, {}), (429, ErrorCode.GITHUB_RATE_LIMITED, {}),
            (403, ErrorCode.GITHUB_RATE_LIMITED, {"X-RateLimit-Remaining": "0"}),
            (403, ErrorCode.GITHUB_RATE_LIMITED, {"Retry-After": "60"}),
            (500, ErrorCode.GITHUB_UNAVAILABLE, {}), (301, ErrorCode.GITHUB_REDIRECT_REJECTED, {}),
        ):
            def failed(request):
                h = Message()
                for k, v in headers.items():
                    h[k] = v
                raise urllib.error.HTTPError(request.full_url, status, DUMMY, h, io.BytesIO(DUMMY.encode()))
            self.fake.override = failed
            capture = io.StringIO()
            with self.subTest(status=status, headers=headers), contextlib.redirect_stderr(capture), self.assertRaises(InstallerError) as error:
                self.access.validate(DUMMY)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn(DUMMY, str(error.exception) + capture.getvalue())

    def test_tls_and_transport_failures_are_not_exposed(self):
        for exception in (ssl.SSLCertVerificationError(DUMMY), TimeoutError(DUMMY), OSError(DUMMY)):
            self.fake.override = lambda req, e=exception: (_ for _ in ()).throw(e)
            with self.assertRaises(InstallerError) as error:
                self.access.validate(DUMMY)
            self.assertEqual(str(error.exception), ErrorCode.GITHUB_UNAVAILABLE.value)

    def test_metadata_identity_empty_invalid_and_duplicate_json_are_rejected(self):
        for body in (b"[]", b"{}", b"", b'{"full_name":"other/repo","default_branch":"main"}',
                     b'{"full_name":1,"full_name":2}', b'{"full_name":NaN}'):
            self.fake.override = lambda req, b=body: Response(b)
            with self.subTest(body=body), self.assertRaises(InstallerError):
                self.access.validate(DUMMY)

    def test_invalid_sha_or_ref_is_rejected(self):
        for sha in ("", "a" * 39, "A" * 40, "a" * 41, "x" * 40, '{"sha":"' + "a" * 40 + '"}'):
            self.fake.refs["web"] = sha
            with self.subTest(sha=sha), self.assertRaises(InstallerError):
                self.access.validate(DUMMY)
        for ref in (None, True, 4, "", "../main", "main?x=1", "main\n", "refs//main", "https://example.invalid", "x" * 201):
            with self.subTest(ref=ref), self.assertRaises(InstallerError):
                self.client.resolve("web", ref, DUMMY)

    def test_scoped_pinned_archive_and_signed_query_never_sent(self):
        out = io.BytesIO()
        digest = self.client.download(self.source, DUMMY, out)
        self.assertEqual(len(digest), 64)
        self.assertGreater(len(out.getvalue()), 0)
        self.assertEqual(self.fake.archive_requests, [("web", SHAS["web"])])
        for request in self.fake.requests:
            self.assertNotIn("?", request.full_url)
            self.assertNotIn("fixture-download-capability", request.full_url)
            self.assertNotIn(DUMMY, request.full_url)
        self.assertTrue(self.fake.requests[-1].full_url.startswith("https://codeload.github.com/"))
        self.assertEqual(self.fake.requests[-1].get_header("Authorization"), "Bearer " + DUMMY)

    def test_untrusted_redirects_are_never_requested(self):
        path = "/" + REPOSITORIES["web"] + "/legacy.tar.gz/" + SHAS["web"]
        for location in ("http://codeload.github.com" + path, "https://evil.invalid" + path,
                         "https://codeload.github.com.evil.invalid" + path, "https://codeload.github.com:443" + path,
                         "https://user@codeload.github.com" + path, "https://codeload.github.com" + path + "#fragment",
                         "https://codeload.github.com/other/repo", "https://codeload.github.com" + path + "/../else",
                         "https://codeload.github.com" + path + "?token=" + DUMMY,
                         "\nhttps://codeload.github.com" + path):
            self.fake.requests.clear()
            self.fake.override = lambda req, loc=location: Response(status=302, headers={"Location": loc})
            with self.subTest(location=location), self.assertRaises(InstallerError):
                self.client.download(self.source, DUMMY, io.BytesIO())
            self.assertEqual(len(self.fake.requests), 1)

    def test_malformed_redirect_uses_fixed_error_code(self):
        self.fake.override = lambda req: Response(status=302, headers={"Location": "https://[invalid"})
        with self.assertRaises(InstallerError) as error:
            self.client.download(self.source, DUMMY, io.BytesIO())
        self.assertEqual(error.exception.code, ErrorCode.GITHUB_REDIRECT_REJECTED)
        self.assertEqual(len(self.fake.requests), 1)

    def test_no_redirect_handler_never_forwards_authorization(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))
        self.fake.override = lambda req: Response(status=302, headers={"Location": "https://evil.invalid"})
        with self.assertRaises(InstallerError):
            self.access.validate(DUMMY)

    def test_codeload_second_redirect_is_rejected(self):
        self.fake.override = lambda req: Response(status=302) if "codeload" in req.full_url else None
        with self.assertRaises(InstallerError):
            self.client.download(self.source, DUMMY, io.BytesIO())
        self.assertEqual(len(self.fake.requests), 2)

    def test_content_lengths_limits_and_encoding(self):
        for headers in ({"Content-Length": "-1"}, {"Content-Length": "999999999999"},
                        {"Content-Length": "true"}, {"Content-Length": "4"},
                        {"Content-Length": "0"}, {"Content-Length": "1, 1"}, {"Content-Encoding": "gzip"}):
            with self.subTest(headers=headers), self.assertRaises(InstallerError):
                self.client._copy(Response(b"abc", headers=headers), io.BytesIO(), limit=10, deadline=1e20)
        with self.assertRaises(InstallerError):
            self.client._copy(Response(b"a" * 11), io.BytesIO(), limit=10, deadline=1e20)
        with self.assertRaises(InstallerError):
            self.client._copy(Response(b"ok"), io.BytesIO(), limit=10, deadline=0)

    def test_chunked_transfer_is_counted_without_content_length(self):
        out = io.BytesIO()
        self.client._copy(Response(b"abc"), out, limit=3, deadline=1e20)
        self.assertEqual(out.getvalue(), b"abc")

    def test_failed_revalidation_and_expiration_forget_credential(self):
        clock = [0.0]
        self.access = GitHubAccess(self.vault, self.client, clock=lambda: clock[0])
        self.access.validate(DUMMY)
        clock[0] = 901
        self.assertFalse(self.access.status()["ready"])
        with self.assertRaises(InstallerError):
            self.vault.require(SECRET_NAME)
        self.access.validate(DUMMY)
        self.fake.override = lambda req: Response(b"broken")
        with self.assertRaises(InstallerError):
            self.access.validate(DUMMY)
        self.assertFalse(self.access.status()["ready"])

    def test_module_selection_ref_resolution_and_default_snapshot(self):
        self.access.validate(DUMMY)
        self.fake.refs["web"] = "d" * 40
        pinned = self.access.select(["web"], {})
        self.assertEqual(pinned["web"].commit_sha, SHAS["web"])
        selected = self.access.select(["web"], {"web": "dev-Bastien"})
        self.assertEqual(selected["web"].commit_sha, "d" * 40)
        self.assertEqual(self.fake.archive_requests, [])

    def test_invalid_modules_or_paths_are_not_network_inputs(self):
        self.access.validate(DUMMY)
        count = len(self.fake.requests)
        for modules, refs in (([], {}), (["web", "web"], {}), (["unknown"], {}), ([True], {}),
                              ("web", {}), (["web"], {"gateway": "main"}), (["web"], [])):
            with self.subTest(modules=modules), self.assertRaises(InstallerError):
                self.access.select(modules, refs)
        self.assertEqual(len(self.fake.requests), count)

    def test_default_transport_ignores_ambient_proxy_and_verifies_tls(self):
        import urllib.request
        with patch("installer.github_client.urllib.request.build_opener") as build, patch("installer.github_client.ssl.create_default_context", wraps=ssl.create_default_context) as tls:
            GitHubClient()
        self.assertTrue(tls.called)
        proxy, redirects, https = build.call_args.args
        self.assertIsInstance(proxy, urllib.request.ProxyHandler)
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirects, NoRedirect)
        self.assertIsInstance(https, urllib.request.HTTPSHandler)
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)
