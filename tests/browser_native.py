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
    def transition_fixture(self, fcm=False):
        from test_gateway_transition import GatewayTransitionPlanTests, GatewayTransitionTests
        from installer.gateway_transition import FCM_COMMIT
        self.plan()
        self.page.locator('#confirm-plan').check(); self.page.locator('#next-button').click()
        expect(self.page.locator('#execution-state')).to_have_attribute('data-state', 'DONE')
        self.quiesce_page()
        fixture = GatewayTransitionPlanTests('test_plan_is_separate_repeatable_and_preserves_parent_bytes')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        if fcm:
            source = GatewayTransitionTests().profile(FCM_COMMIT, GatewayTransitionTests().push())
            fixture.source = source; fixture.runtime.foundation = source.foundation
            fixture.service.profile.return_value['binding'] = source.binding()
        self.service.gateway_transition = fixture.control
        view = patch.object(self.service.gateway_service, 'state', return_value={
            'installation': fixture.document, 'profile': fixture.service.profile(), 'availability': None})
        view.start(); self.addCleanup(view.stop)
        self.refresh(); self.step(5)
        return fixture

    def test_gateway_transition_plan_is_explicit_and_refresh_has_no_effect(self):
        fixture = self.transition_fixture()
        self.assertFalse(fixture.control.root.exists())
        self.refresh(); self.step(5)
        self.assertFalse(fixture.control.root.exists())
        self.page.locator('#plan-gateway-transition').click()
        expect(self.page.locator('#gateway-transition-state')).to_contain_text('Configuration compatible')
        before = (fixture.control.root/'profile.json').read_bytes()
        calls = fixture.service.engine.call_count
        self.refresh(); self.step(5)
        expect(self.page.locator('#gateway-transition-state')).to_contain_text('Configuration compatible')
        self.assertEqual(fixture.service.engine.call_count, calls)
        self.assertEqual((fixture.control.root/'profile.json').read_bytes(), before)
        self.assertEqual(len([p for p in self.paths if '/api/gateway/transition/' in p]), 1)
        self.assertFalse(fixture.control.state()['apply_allowed'])
        self.assertEqual(self.page.evaluate('localStorage.length + sessionStorage.length'), 0)

    def test_gateway_transition_fcm_rollback_has_explanation_and_no_apply(self):
        fixture = self.transition_fixture(fcm=True)
        self.page.locator('#plan-gateway-transition').click()
        expect(self.page.locator('#gateway-transition-state')).to_contain_text('bloquée')
        expect(self.page.locator('#gateway-transition')).to_contain_text('ne prend pas en charge le profil Firebase actuel')
        expect(self.page.locator('#gateway-transition button')).to_have_count(0)
        self.assertEqual(fixture.control.profile()['assessment']['blockers'], ['TARGET_FCM_PROFILE_UNSUPPORTED'])
        self.assertNotIn('PRIVATE KEY', self.page.content())

    def test_dev_target_explicit_choice_cancel_apply_and_refresh_without_replay(self):
        from test_dev_plan import DevFoundationPlanTests
        self.quiesce_page()
        fixture = DevFoundationPlanTests('test_dev_requires_explicit_selection_and_has_six_ordered_steps')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.service = fixture.service
        self.server.state.transaction_service = self.service
        self.refresh(); self.step(5)
        expect(self.page.locator('#foundation-dev')).not_to_be_checked()
        self.page.locator('#foundation-dev').check()
        self.refresh(); self.step(5)
        expect(self.page.locator('#foundation-dev')).not_to_be_checked()
        self.page.locator('#foundation-dev').check()
        self.page.locator('#plan-foundation').click()
        expect(self.page.locator('#foundation-state')).to_have_attribute('data-state', 'PLANNED')
        self.assertEqual(fixture.control.profile()['version'], 2)
        fixture.dev.stage.assert_not_called()
        self.page.locator('#apply-foundation').click(); self.dialog('cancel')
        fixture.dev.stage.assert_not_called()
        self.page.locator('#apply-foundation').click(); self.dialog()
        expect(self.page.locator('#foundation-state')).to_have_attribute('data-state', 'DONE')
        fixture.dev.stage.assert_called_once()
        before = fixture.control.journal.path.read_bytes()
        self.refresh(); self.step(5)
        expect(self.page.locator('#foundation-state')).to_have_attribute('data-state', 'DONE')
        self.assertEqual(fixture.control.journal.path.read_bytes(), before)
        fixture.dev.stage.assert_called_once()
        self.assertNotIn('PRIVATE KEY', self.page.content())
        self.assertEqual(self.page.evaluate('localStorage.length + sessionStorage.length'), 0)

    def fcm_fixture(self):
        import fcm_fixture
        responses = fcm_fixture.responses(); self.fake.override = responses
        catalogue = patch('installer.gateway_release._FCM_RELEASE', responses.selected)
        catalogue.start(); self.addCleanup(catalogue.stop)
        self.quiesce_page(); fcm_fixture.prepare(self.service)
        self.refresh(); self.step(5)
        self.page.locator('#fcm-project').fill('hestia-test')
        self.page.locator('#plan-fcm').click()
        expect(self.page.locator('#fcm-state')).to_have_attribute('data-state', 'AWAITING_IMPORT')
        return {'name': 'synthetic-account.json', 'mimeType': 'application/json', 'buffer': fcm_fixture.credential()}

    def test_fcm_private_upload_cancel_then_import_and_refresh_without_replay(self):
        upload = self.fcm_fixture(); root = self.service.fcm.store.root
        self.page.locator('#fcm-credential').set_input_files(upload)
        self.page.locator('#import-fcm').click(); self.dialog('cancel')
        self.assertFalse((root / 'server.json').exists())
        expect(self.page.locator('#fcm-credential')).to_have_value('')
        self.assertFalse(any(path.endswith('/api/gateway/fcm/import') for path in self.paths))
        self.page.locator('#fcm-credential').set_input_files(upload)
        self.page.locator('#import-fcm').click(); self.dialog()
        expect(self.page.locator('#fcm-state')).to_have_attribute('data-state', 'IMPORTED')
        self.assertEqual((root / 'server.json').stat().st_mode & 0o777, 0o600)
        before = (root / 'server.json').read_bytes()
        count = sum(path.endswith('/api/gateway/fcm/import') for path in self.paths)
        self.refresh(); self.step(5)
        expect(self.page.locator('#fcm-state')).to_have_attribute('data-state', 'IMPORTED')
        self.assertEqual(sum(path.endswith('/api/gateway/fcm/import') for path in self.paths), count)
        self.page.locator('#check-fcm').click()
        expect(self.page.locator('#fcm-verification')).to_contain_text('Credential contrôlé le')
        self.assertEqual((root / 'server.json').read_bytes(), before)
        for text in ('PRIVATE KEY', 'sender@'):
            self.assertNotIn(text, self.page.content()); self.assertNotIn(text, self.http('/api/installation/report')['body'])
        self.assertEqual(self.page.evaluate('localStorage.length + sessionStorage.length'), 0)

    def test_fcm_file_selection_survives_poll_and_wrong_project_has_no_private_effect(self):
        upload = self.fcm_fixture()
        value = json.loads(upload['buffer']); value['project_id'] = 'different-project'
        self.page.locator('#fcm-credential').set_input_files({**upload, 'buffer': json.dumps(value).encode()})
        self.page.wait_for_timeout(1750)
        self.assertEqual(self.page.locator('#fcm-credential').evaluate('(node) => node.files.length'), 1)
        self.page.locator('#import-fcm').click()
        with self.page.expect_response(lambda response: response.url.endswith('/api/gateway/fcm/import')) as rejected:
            self.dialog()
        self.assertEqual(rejected.value.status, 409)
        expect(self.page.locator('#wizard-message')).to_contain_text('Les contrôles ne sont pas tous validés')
        expect(self.page.locator('#wizard-form')).to_have_attribute('aria-busy', 'false')
        expect(self.page.locator('#fcm-state')).to_have_attribute('data-state', 'AWAITING_IMPORT')
        self.assertFalse((self.service.fcm.store.root / 'server.json').exists())
        imports = sum(path.endswith('/api/gateway/fcm/import') for path in self.paths)
        for size in (0, 16385):
            self.page.locator('#fcm-credential').set_input_files({**upload, 'buffer': b'x' * size})
            self.page.locator('#import-fcm').click()
            expect(self.page.locator('#operation-dialog')).not_to_be_visible()
            expect(self.page.locator('#wizard-message')).to_contain_text('16 Kio')
            self.assertEqual(sum(path.endswith('/api/gateway/fcm/import') for path in self.paths), imports)

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
        self.page.wait_for_function("() => document.querySelector('.hero-image').complete")
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

    def test_responsive_plan_and_reduced_motion(self):
        # Native Chromium applies dynamic viewport units asynchronously. Await the
        # requested CSS viewport, not an arbitrary sleep or relaxed pixel bound.
        self.plan(('web', 'gateway', 'apk'))
        sizes = [(1920,1080),(1440,900),(1366,768),(1280,720),(1024,768),
                 (840,600),(768,1024),(390,844),(360,640),(320,568)]
        for cycle in range(3):
            for width, height in sizes:
                with self.subTest(cycle=cycle, width=width, height=height):
                    self.page.set_viewport_size({'width':width,'height':height})
                    self.page.wait_for_function("""({width,height}) =>
                        innerWidth === width && innerHeight === height &&
                        Math.round(parseFloat(getComputedStyle(document.body).minHeight)) === height
                    """, arg={'width':width,'height':height})
                    self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    result = self.page.evaluate("""() => ({
                      width:document.documentElement.scrollWidth, viewport:innerWidth,
                      footerBottom:document.querySelector('.wizard-actions').getBoundingClientRect().bottom,
                      rootBottom:document.querySelector('.installer-window').getBoundingClientRect().bottom,
                      buttonRight:document.querySelector('#next-button').getBoundingClientRect().right,
                      transition:getComputedStyle(document.querySelector('#next-button')).transitionDuration
                    })""")
                    self.assertLessEqual(result['width'],width,result)
                    self.assertLessEqual(result['footerBottom'],result['rootBottom']+1,result)
                    self.assertLessEqual(result['buttonRight'],width,result)
                    if width > 820: self.assertLessEqual(result['footerBottom'],height,result)
                    self.assertIn('1e-05s',result['transition'])
        self.page.set_viewport_size({'width':1366,'height':768})
        self.page.wait_for_function('() => innerHeight === 768 && parseFloat(getComputedStyle(document.body).minHeight) === 768')
        if os.environ.get('HESTIA_QC_SCREENSHOTS'):
            directory=Path(os.environ['HESTIA_QC_SCREENSHOTS']); directory.mkdir(parents=True,exist_ok=True)
            self.page.screenshot(path=str(directory/'native-plan-desktop.png'))

    def test_draft_refresh_empty_selection_and_advanced_ref(self):
        self.modules()
        self.page.locator("#module-web").uncheck()
        expect(self.page.locator("#next-button")).to_be_disabled()
        self.page.locator("#module-apk").check()
        self.page.locator("#installation-mode").select_option("upgrade")
        self.page.get_by_text("Références GitHub avancées", exact=True).click()
        self.page.locator("#ref-apk").fill("feature/" + "a" * 150)
        self.page.locator("#ref-apk").press("Tab")
        self.page.wait_for_function("async () => (await (await fetch('/api/wizard/state')).json()).draft.refs.apk?.length === 158")
        # Last saved draft is visible after refresh, without browser storage.
        self.page.wait_for_function("() => document.getElementById('wizard-form').getAttribute('aria-busy') === 'false'")
        self.refresh(); self.step(3)
        expect(self.page.locator("#module-apk")).to_be_checked()
        expect(self.page.locator("#module-web")).not_to_be_checked()
        expect(self.page.locator("#installation-mode")).to_have_value("upgrade")


    def test_gateway_preparation_real_https_confirmation_refresh_and_private_keys(self):
        from gateway_fixture import ArtifactResponses, complete_web
        responses = ArtifactResponses(); self.fake.override = responses
        catalogue = patch('installer.gateway_release._RELEASE', responses.selected); catalogue.start(); self.addCleanup(catalogue.stop)
        complete_web(self.service)
        before = self.service.engine.journal.path.read_bytes()
        self.refresh(); self.step(5)
        self.page.locator('#gateway-origin').fill('https://mobile.customer.example')
        self.page.locator('#gateway-dev').check()
        self.page.locator('#plan-gateway').click()
        expect(self.page.locator('#gateway-state')).to_have_attribute('data-state', 'PLANNED')
        self.assertEqual(responses.downloads, 0)
        self.page.locator('#apply-gateway').click()
        expect(self.page.locator('#operation-dialog')).to_be_visible()
        self.page.keyboard.press('Escape')
        self.assertEqual(responses.downloads, 0)
        self.page.locator('#apply-gateway').click()
        self.page.locator('#operation-dialog button[value="confirm"]').click()
        expect(self.page.locator('#gateway-state')).to_have_attribute('data-state', 'DONE')
        expect(self.page.locator('#gateway-preparation')).to_contain_text('restent à installer')
        root = self.service.gateway.identities.root
        keys = {name: (root / (name + '.pem')).read_bytes() for name in ('main', 'dev')}
        self.assertNotEqual(keys['main'], keys['dev'])
        self.refresh(); self.step(5)
        expect(self.page.locator('#gateway-state')).to_have_attribute('data-state', 'DONE')
        self.page.locator('#check-gateway').click()
        expect(self.page.locator('#wizard-message')).to_contain_text('Paquet et identités vérifiés')
        self.assertEqual(responses.downloads, 1)
        self.assertEqual(before, self.service.engine.journal.path.read_bytes())
        self.assertEqual(keys, {name: (root / (name + '.pem')).read_bytes() for name in keys})
        self.assertNotIn('PRIVATE KEY', self.page.content()); self.assertNotIn(DUMMY, self.page.content())
        self.assertEqual(self.page.evaluate('localStorage.length + sessionStorage.length'), 0)
        directory = os.environ.get('HESTIA_QC_SCREENSHOTS')
        if directory: self.page.locator('#gateway-preparation').screenshot(path=str(Path(directory) / 'native-gateway-preparation.png'))

    def test_gateway_package_import_confirmation_cancel_retry_and_refresh(self):
        from gateway_fixture import ArtifactResponses, complete_web
        responses = ArtifactResponses(); self.fake.override = responses
        catalogue = patch('installer.gateway_release._RELEASE', responses.selected); catalogue.start(); self.addCleanup(catalogue.stop)
        complete_web(self.service); self.service.github.access.clear()
        before = self.service.engine.journal.path.read_bytes()
        self.refresh(); self.step(5)
        self.page.locator('#gateway-origin').fill('https://mobile.customer.example')
        self.page.locator('#gateway-acquisition').select_option('package')
        self.page.locator('#plan-gateway').click()
        expect(self.page.locator('#gateway-state')).to_have_attribute('data-state', 'PLANNED')
        expect(self.page.locator('#gateway-credential')).to_have_count(0)
        upload = {'name': 'qualified-fixture.zip', 'mimeType': 'application/zip', 'buffer': responses.package}
        self.page.locator('#gateway-package').set_input_files({**upload, 'buffer': b'bad'})
        self.page.locator('#import-gateway').click()
        expect(self.page.locator('#wizard-message')).to_contain_text('taille du ZIP')
        self.page.locator('#gateway-package').set_input_files(upload)
        self.page.locator('#import-gateway').click()
        expect(self.page.locator('#operation-dialog')).to_be_visible()
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#gateway-package')).to_have_value('')
        self.assertFalse((self.service.gateway.root / 'binary').exists())
        self.page.locator('#gateway-package').set_input_files({**upload, 'buffer': b'x' + responses.package[1:]})
        self.page.locator('#import-gateway').click()
        self.page.locator('#operation-dialog button[value="confirm"]').click()
        expect(self.page.locator('#gateway-state')).to_have_attribute('data-state', 'FAILED')
        self.assertFalse(self.service.gateway.identities.root.exists())
        self.page.locator('#gateway-package').set_input_files(upload)
        self.page.locator('#import-gateway').click()
        self.page.locator('#operation-dialog button[value="confirm"]').click()
        expect(self.page.locator('#gateway-state')).to_have_attribute('data-state', 'DONE')
        root = self.service.gateway.identities.root; key = (root / 'main.pem').read_bytes()
        self.assertFalse((root / 'dev.pem').exists())
        count = len([p for p in self.paths if p.endswith('/api/gateway/preparation/import')])
        self.assertEqual(count, 2)
        self.refresh(); self.step(5)
        expect(self.page.locator('#gateway-state')).to_have_attribute('data-state', 'DONE')
        self.page.locator('#check-gateway').click()
        expect(self.page.locator('#wizard-message')).to_contain_text('Paquet et identités vérifiés')
        self.assertEqual(count, len([p for p in self.paths if p.endswith('/api/gateway/preparation/import')]))
        self.assertEqual((root / 'main.pem').read_bytes(), key)
        self.assertEqual(before, self.service.engine.journal.path.read_bytes()); self.assertEqual(responses.downloads, 0)
        self.assertNotIn('PRIVATE KEY', self.page.content()); self.assertNotIn('qualified-fixture.zip', self.page.content())
        self.assertEqual(self.page.evaluate('localStorage.length + sessionStorage.length'), 0)
        directory = os.environ.get('HESTIA_QC_SCREENSHOTS')
        if directory: self.page.locator('#gateway-preparation').screenshot(path=str(Path(directory) / 'native-gateway-package-import.png'))
