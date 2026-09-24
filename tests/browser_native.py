"""Native Chromium -> production HTTPS routes. GitHub alone remains a fixture.

No fetch bridge, HTML rewriting, response-cookie injection, CSP override or fake
Blob download. Self-signed TLS acceptance is confined to this disposable browser
context. Production HTTPS/outbound certificate verification is not changed.
"""
import json
import os
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect
import browser_wizard as legacy
import test_httpd
from github_fixture import DUMMY, make_service
from test_wizard import good_checks


class NativeBrowserTests(legacy.BrowserWizardTests):
    def setUp(self):
        test_httpd.HTTPSBootstrapTests.setUp(self)
        self.addCleanup(test_httpd.HTTPSBootstrapTests.tearDown, self)
        self.service, self.fake = make_service(self.root)
        self.addCleanup(self.service.close)
        self.server.state.transaction_service = self.service
        self.server.state.web_root = legacy.WEB
        self.preflight_patch = patch('installer.wizard.run_read_only_preflight', side_effect=good_checks)
        self.preflight_patch.start()
        self.addCleanup(self.preflight_patch.stop)
        self.browser_context = self.browser.new_context(ignore_https_errors=True,
            viewport={'width': 1366, 'height': 768}, reduced_motion='reduce', accept_downloads=True)
        self.addCleanup(self.browser_context.close)
        self.js_errors = []
        self.paths = []
        self.url = f'https://127.0.0.1:{self.port}'
        self.browser_context.on('request', lambda request: self.paths.append(request.url))
        self.page = self.browser_context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.js_errors.append(str(error)))
        response = self.page.goto(self.url + '/', wait_until='load')
        self.assertEqual(response.status, 200)
        expect(self.page).to_have_url(self.url + '/bootstrap')
        self.page.locator('#bootstrap-code').fill(self.code)
        self.page.locator('#bootstrap-form button[type=submit]').click()
        expect(self.page).to_have_url(self.url + '/')
        expect(self.page.locator('#next-button')).to_be_enabled()
        self.step(0)
        cookies = self.browser_context.cookies(self.url)
        self.assertEqual(len(cookies), 1)
        self.cookie = cookies[0]['name'] + '=' + cookies[0]['value']

    def tearDown(self):
        self.quiesce_page()
        self.assertEqual(self.js_errors, [])

    def refresh(self):
        # Real navigation, preserving the browser cookie and no saved JS state.
        size = self.page.viewport_size if self.page else {'width': 1366, 'height': 768}
        if self.page:
            self.quiesce_page()
            self.page.close()
        self.page = self.browser_context.new_page()
        self.page.set_viewport_size(size)
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.js_errors.append(str(error)))
        self.page.goto(self.url + '/', wait_until='load')

    def quiesce_page(self):
        if self.page and not self.page.is_closed() and not self.service._mutation_lock.locked():
            # Let completed form responses settle before closing; never wait for
            # an intentionally blocked apply in the disconnection scenario.
            self.page.wait_for_timeout(50)

    def test_fresh_web_full_ui_report_and_refresh_no_replay(self):
        self.plan(); self.apply()
        expect(self.page.locator('#wizard-form')).to_contain_text("HESTIA n'est pas encore déployé")
        with self.page.expect_download() as event:
            self.page.locator('#download-report').click()
        download = event.value
        self.assertEqual(download.suggested_filename, 'HESTIA-ACQUISITION-REPORT.json')
        self.assertIsNone(download.failure())
        downloaded = json.loads(Path(download.path()).read_text())
        self.assertEqual(downloaded['installation']['state'], 'DONE')
        self.assertNotIn(DUMMY, json.dumps(downloaded))
        self.refresh(); self.step(5)
        self.assertEqual(len(self.fake.archive_requests), 1)
        self.assertEqual(self.page.evaluate('localStorage.length + sessionStorage.length'), 0)
        self.assertTrue(all(path.startswith(self.url + '/') for path in self.paths))
        self.assertFalse(any(DUMMY in path for path in self.paths))

    def test_initial_network_failure_has_explicit_reconnect(self):
        def abort(route):
            route.abort('connectionfailed')
        pattern = self.url + '/api/session'
        self.browser_context.route(pattern, abort, times=1)
        self.refresh()
        expect(self.page.locator('#retry-connection')).to_be_visible()
        expect(self.page.locator('#next-button')).to_be_disabled()
        self.page.locator('#retry-connection').click()
        expect(self.page.locator('#next-button')).to_be_enabled()
        self.step(0)

    def test_raw_error_text_is_never_injected_or_displayed(self):
        # Targeted hostile response fixture, not a replacement for native fetch.
        self.browser_context.route(self.url + '/api/github/validate', lambda route:
            route.fulfill(status=500, content_type='application/json',
                body=json.dumps({'error': '<img src=x onerror=alert(1)>PRIVATE-FIXTURE'})), times=1)
        self.page.locator('#next-button').click(); self.step(1)
        self.page.locator('#github-credential').fill(DUMMY)
        self.page.locator('#validate-github').click()
        expect(self.page.locator('#wizard-message')).to_contain_text('Action indisponible')
        self.assertNotIn('PRIVATE-FIXTURE', self.page.locator('body').inner_text())
        self.assertNotIn(DUMMY, self.page.content())
        expect(self.page.locator('#github-credential')).to_have_value('')

    def test_expired_session_is_rejected_by_https_without_replay(self):
        self.plan()
        self.server.state.session_store.clear()
        self.refresh()
        expect(self.page).to_have_url(self.url + '/bootstrap')
        self.assertEqual(self.fake.archive_requests, [])
        self.assertEqual(self.http('/api/wizard/state')['status'], 401)

    def test_cookie_headers_and_original_assets_are_effective(self):
        cookies = self.browser_context.cookies(self.url)
        self.assertTrue(cookies[0]['httpOnly'])
        self.assertTrue(cookies[0]['secure'])
        self.assertEqual(cookies[0]['sameSite'], 'Strict')
        self.assertEqual(self.page.evaluate('document.cookie'), '')
        self.page.wait_for_function("document.querySelector('.hero-image').complete")
        self.assertEqual(self.page.locator('.hero-image').evaluate('(image) => image.naturalWidth'), 1060)
        self.assertEqual(self.page.locator('.hero-image').evaluate('(image) => image.naturalHeight'), 1510)
        response = self.browser_context.request.get(self.url + '/')
        self.assertEqual(response.headers['cache-control'], 'no-store, max-age=0')
        self.assertIn("script-src 'self'", response.headers['content-security-policy'])
        self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
        self.assertEqual(self.page.evaluate('typeof window.__httpsBridge'), 'undefined')
        self.assertEqual(self.page.evaluate("Function.prototype.toString.call(window.fetch).includes('[native code]')"), True)
        directory = os.environ.get('HESTIA_QC_SCREENSHOTS')
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(Path(directory) / 'native-welcome-desktop.png'))

    def test_native_missing_csrf_cannot_mutate_or_logout(self):
        self.plan()
        result = self.page.evaluate("""async () => {
          const response = await fetch('/api/logout', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
          return response.status;
        }""")
        self.assertEqual(result, 403)
        self.assertEqual(self.service.engine.report()['state'], 'PLANNED')
        self.refresh(); self.step(4)
        self.assertEqual(self.fake.archive_requests, [])

    def test_native_second_tab_keeps_session_but_not_secret_input(self):
        self.page.locator('#next-button').click(); self.step(1)
        self.page.locator('#github-credential').fill(DUMMY)
        other = self.browser_context.new_page()
        try:
            other.goto(self.url + '/', wait_until='load')
            expect(other.locator('#github-credential')).to_have_value('')
            self.assertNotIn(DUMMY, other.content())
            self.assertEqual(other.evaluate('localStorage.length + sessionStorage.length'), 0)
            self.assertEqual(self.fake.archive_requests, [])
        finally:
            other.close()

    def test_native_bootstrap_code_cannot_unlock_a_second_context(self):
        context = self.browser.new_context(ignore_https_errors=True)
        try:
            page = context.new_page()
            page.goto(self.url + '/bootstrap')
            page.locator('#bootstrap-code').fill(self.code)
            page.locator('#bootstrap-form button[type=submit]').click()
            expect(page.locator('#bootstrap-message')).to_contain_text('Code incorrect ou expiré')
            expect(page.locator('#bootstrap-code')).to_have_value('')
            self.assertEqual(context.cookies(), [])
        finally:
            context.close()

    def test_native_unknown_api_remains_closed(self):
        response = self.browser_context.request.post(self.url + '/api/run-command', data={'command': 'fixture-only'})
        self.assertIn(response.status, (403, 404))
        self.assertEqual(self.fake.archive_requests, [])
