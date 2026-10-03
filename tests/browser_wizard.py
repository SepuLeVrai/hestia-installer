"""Offline Chromium DOM + HTTPS bridge + offline GitHub transport fixtures.

Run explicitly: python3 tests/browser_wizard.py. Playwright/Chromium are quality
only dependencies, never bootstrap dependencies. Missing tools fail this command.
Browser navigation/fetch/download are not a native end-to-end test here: managed
Chromium forbids URL navigation. DOM uses unchanged production JS; a test fetch
bridge exercises the actual HTTPS endpoints with a separately authenticated cookie.
The unchanged illustration may be absent in a partial source checkout: a neutral
same-size fixture is then used for geometry only, not for artwork approval.
"""
import io
import asyncio
import base64
import http.client
import re
import json
import os
import shutil
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
import test_httpd
from github_fixture import DUMMY, confirm, make_service
from test_wizard import good_checks

WEB = Path(__file__).resolve().parents[1] / "installer" / "web"


class BrowserWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=shutil.which("chromium"),
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        test_httpd.HTTPSBootstrapTests.setUp(self)
        self.service, self.fake = make_service(self.root)
        self.server.state.transaction_service = self.service
        self.server.state.web_root = WEB
        self.preflight_patch = patch("installer.wizard.run_read_only_preflight", side_effect=good_checks)
        self.preflight_patch.start()
        self.browser_context = self.browser.new_context(ignore_https_errors=True,
            viewport={"width": 1366, "height": 768}, reduced_motion="reduce", accept_downloads=True)
        self.js_errors = []
        self.paths = []
        self.url = f"https://127.0.0.1:{self.port}"
        status, _, cookie, _ = test_httpd.HTTPSBootstrapTests._unlock(self)
        self.assertEqual(status, 200)
        self.cookie = cookie.split(";", 1)[0]
        async def bridge(_source, path, options):
            return await asyncio.to_thread(self.http, path, options)
        self.browser_context.expose_binding("__httpsBridge", bridge)
        self.page = None
        self.refresh()
        expect(self.page.locator("#next-button")).to_be_enabled()
        expect(self.page.locator("body")).to_have_attribute("data-wizard-step", "0")

    _connection = test_httpd.HTTPSBootstrapTests._connection

    def http(self, path, options=None):
        options = options or {}
        self.paths.append(self.url + path)
        headers = {"Cookie": self.cookie, "Origin": self.url}
        headers.update(options.get("headers", {}))
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=15)
        try:
            body = options.get("body")
            # Match native fetch's UTF-8 JSON transport, including operator names.
            if isinstance(body, str): body = body.encode('utf-8')
            conn.request(options.get("method", "GET"), path, body=body, headers=headers)
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            return {"status": response.status, "body": body, "headers": dict(response.getheaders())}
        finally:
            conn.close()

    def refresh(self):
        size = self.page.viewport_size if self.page else {"width": 1366, "height": 768}
        if self.page:
            self.quiesce_page()
            self.page.close()
        self.page = self.browser_context.new_page()
        self.page.set_viewport_size(size)
        self.page.set_default_timeout(8000)
        self.page.on("pageerror", lambda error: self.js_errors.append(str(error)))
        # Retrieve the authenticated HTML through the real TLS handler, then render
        # its DOM locally. No browser URL navigation or policy override is used.
        html = self.http("/")["body"]
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S)
        html = re.sub(r'<link[^>]*>', '', html)
        if not (WEB / "assets" / "hestia-hero.webp").exists():
            from PIL import Image
            pixels = io.BytesIO(); Image.new("RGB", (1060, 1510), (220, 211, 200)).save(pixels, "WEBP")
            image = pixels.getvalue()
        else:
            image = (WEB / "assets" / "hestia-hero.webp").read_bytes()
        data_url = "data:image/webp;base64," + base64.b64encode(image).decode()
        html = html.replace('src="assets/hestia-hero.webp"', 'src="' + data_url + '"')
        self.page.set_content(html)
        self.page.add_style_tag(content=(WEB / "assets" / "installer.css").read_text().replace('url("hestia-hero.webp")', 'url("' + data_url + '")'))
        self.page.add_style_tag(content=(WEB / "assets" / "wizard.css").read_text())
        self.page.evaluate("""() => {
          window.__storageAttempts = 0;
          window.__requests = 0; window.__intervals = [];
          const interval = window.setInterval.bind(window);
          window.setInterval = (...args) => {const id = interval(...args); window.__intervals.push(id); return id;};
          for (const key of ['localStorage', 'sessionStorage', 'indexedDB']) {
            Object.defineProperty(window, key, {get() {window.__storageAttempts++; throw new Error('Forbidden persistence in UI test');}});
          }
          window.fetch = async (path, options = {}) => {
            window.__requests++;
            try {
              const reply = await window.__httpsBridge(path, options);
              return new Response(reply.status === 204 ? null : reply.body, {status:reply.status, headers:reply.headers});
            } finally {window.__requests--;}

          };
          URL.createObjectURL = (blob) => {blob.text().then(text => {window.__downloadText = text;}); return 'blob:offline-test';};
          URL.revokeObjectURL = () => {};
          HTMLAnchorElement.prototype.click = function() {window.__downloadName = this.download;};
        }""")
        if getattr(self, "fail_start_once", False):
            self.page.evaluate("() => {const fetch = window.fetch; let first = true; window.fetch = (...args) => {if (first) {first = false; return Promise.reject(new Error('fixture'));} return fetch(...args);};}")
        self.page.add_script_tag(content=(WEB / "assets" / "installer.js").read_text())

    def quiesce_page(self):
        if self.page and not self.page.is_closed():
            self.page.evaluate("() => window.__intervals?.forEach(clearInterval)")
            # A disconnected client may leave an active apply running. The dedicated
            # disconnect test deliberately closes it without waiting for that effect.
            if not self.service._mutation_lock.locked():
                self.page.wait_for_function("(window.__requests || 0) === 0")
                self.page.wait_for_timeout(30)

    def tearDown(self):
        self.quiesce_page()
        self.browser_context.close()
        self.preflight_patch.stop()
        self.service.close()
        test_httpd.HTTPSBootstrapTests.tearDown(self)
        self.assertEqual(self.js_errors, [])

    def step(self, number):
        expect(self.page.locator("body")).to_have_attribute("data-wizard-step", str(number))
        expect(self.page.locator("#wizard-form")).to_have_attribute("aria-busy", "false")

    def github(self):
        self.page.locator("#next-button").click(); self.step(1)
        self.validate()

    def validate(self):
        self.page.locator("#github-credential").fill(DUMMY)
        self.page.locator("#validate-github").click()
        expect(self.page.locator("#next-button")).to_be_enabled()
        expect(self.page.locator("#github-credential")).to_have_value("")

    def modules(self):
        self.github(); self.page.locator("#next-button").click(); self.step(2)
        self.page.locator("#run-preflight").click(); expect(self.page.locator("#next-button")).to_be_enabled()
        self.page.locator("#next-button").click(); self.step(3)

    def web_application_choices(self, after_submit=None):
        from test_application_plan import setup_payload
        value = setup_payload(); choices = value['configuration']
        self.page.locator('#prepare-web-application').check()
        for name, text in {'hostname': choices['hostname'], 'database-name': choices['database']['name'],
            'database-user': choices['database']['user'], 'first-name': choices['administrator']['first_name'],
            'last-name': choices['administrator']['last_name'], 'email': choices['administrator']['email'],
            **value['credentials']}.items(): self.page.locator('#application-' + name).fill(text)
        self.page.locator('#save-web-application').click()
        if after_submit is not None: after_submit()
        expect(self.page.locator('#wizard-message')).to_contain_text('Configuration enregistrée')
        expect(self.page.locator('#next-button')).to_be_enabled()
        return value

    def test_application_save_waits_for_its_pending_module_draft(self):
        self.modules(); entered = threading.Event(); release = threading.Event()
        original = self.service.wizard.save
        def held(value):
            entered.set()
            if not release.wait(8): raise RuntimeError('Draft fixture release timeout')
            return original(value)
        def submitted():
            self.assertTrue(entered.wait(2))
            self.page.wait_for_timeout(150)
            self.assertFalse(any(path.endswith('/api/web/setup') for path in self.paths))
            release.set()
        try:
            with patch.object(self.service.wizard, 'save', side_effect=held):
                self.web_application_choices(after_submit=submitted)
        finally: release.set()
        self.assertIsNotNone(self.service.application.read())

    def test_application_choices_plan_and_refresh_keep_identity_without_secrets(self):
        self.modules(); value = self.web_application_choices()
        for name in value['credentials']: expect(self.page.locator('#application-' + name)).to_have_value('')
        with patch('installer.application_plan.HostPrerequisites.check'):
            self.page.locator('#next-button').click(); self.step(4)
        document = self.service.engine.report()
        self.assertEqual(len(document['plan']['steps']), 10)
        expect(self.page.locator('#application-plan-choices')).to_contain_text(value['configuration']['administrator']['first_name'])
        expect(self.page.locator('#application-plan-choices')).to_contain_text(value['configuration']['database']['name'])
        expect(self.page.locator('#next-button')).to_be_disabled()
        expect(self.page.locator('#confirm-plan').locator('..')).to_contain_text('création des comptes')
        self.assertEqual(self.fake.archive_requests, [])
        self.refresh(); self.step(4)
        self.assertEqual(self.service.engine.report(), document)
        for secret in value['credentials'].values(): self.assertNotIn(secret, self.page.content())
        self.assertFalse(self.service.application.state()['application_installed'])

    def test_application_edit_requires_save_and_source_selection_stays_explicit(self):
        self.modules(); self.web_application_choices()
        expect(self.page.locator('#module-gateway')).to_be_disabled()
        self.page.locator('#application-hostname').fill('changed.example.test')
        expect(self.page.locator('#next-button')).to_be_disabled()
        self.page.locator('#prepare-web-application').uncheck()
        expect(self.page.locator('#module-gateway')).to_be_enabled()
        self.page.locator('#next-button').click(); self.step(4)
        self.assertEqual(len(self.service.engine.report()['plan']['steps']), 1)
        expect(self.page.locator('#confirm-plan').locator('..')).to_contain_text("uniquement l'acquisition")

    def test_application_credentials_renewal_does_not_approve_or_replay(self):
        self.modules(); self.web_application_choices()
        with patch('installer.application_plan.HostPrerequisites.check'):
            self.page.locator('#next-button').click(); self.step(4)
        before = self.service.engine.report(); self.service.application.clear()
        self.refresh(); self.step(4)
        self.page.locator('.progress-dot[data-step="5"]').click(); self.step(5)
        self.page.get_by_text('Ressaisir les identifiants applicatifs', exact=True).click()
        secret = 'browser-renewed-private-fixture'
        self.page.locator('#renew-database_password').fill(secret)
        self.page.locator('#renew-web-credentials').click()
        expect(self.page.locator('#wizard-message')).to_contain_text('Identifiants mis à jour')
        self.assertEqual(self.service.engine.report(), before)
        self.assertEqual(self.service.engine.secrets.require('web.database_password'), secret)
        self.assertNotIn(secret, self.page.content()); self.assertEqual(self.fake.archive_requests, [])

    def plan(self, modules=("web",), mode="fresh"):
        self.modules()
        for module in ("web", "gateway", "apk"):
            self.page.locator("#module-" + module).set_checked(module in modules)
        self.page.locator("#installation-mode").select_option(mode)
        self.page.locator("#next-button").click(); self.step(4)
        expect(self.page.locator("#next-button")).to_be_disabled()
        self.assertEqual(self.fake.archive_requests, [])

    def apply(self):
        self.page.locator("#confirm-plan").check()
        self.page.locator("#next-button").click()
        expect(self.page.locator("#execution-state")).to_have_attribute("data-state", "DONE")
        self.step(5)

    def dialog(self, value="confirm"):
        expect(self.page.locator("#operation-dialog")).to_be_visible()
        self.page.locator(f'#operation-dialog button[value="{value}"]').click()

    def test_fresh_web_full_ui_report_and_refresh_no_replay(self):
        self.plan(); self.apply()
        self.assertEqual([m for m, _ in self.fake.archive_requests], ["web"])
        expect(self.page.locator("#wizard-form")).to_contain_text("HESTIA n'est pas encore déployé")
        self.page.locator("#download-report").click()
        self.page.wait_for_function("window.__downloadText !== undefined")
        downloaded = json.loads(self.page.evaluate("window.__downloadText"))
        self.assertEqual(self.page.evaluate("window.__downloadName"), "HESTIA-ACQUISITION-REPORT.json")
        self.assertEqual(downloaded["installation"]["state"], "DONE")
        self.assertNotIn(DUMMY, json.dumps(downloaded))
        self.refresh(); self.step(5)
        self.assertEqual(len(self.fake.archive_requests), 1)
        self.assertEqual(self.page.evaluate("window.__storageAttempts"), 0)
        self.assertTrue(all(path.startswith(self.url) for path in self.paths))
        self.assertFalse(any(DUMMY in path for path in self.paths))

    def test_upgrade_web_gateway_selection(self):
        self.plan(("web", "gateway"), "upgrade"); self.apply()
        self.assertCountEqual([m for m, _ in self.fake.archive_requests], ["web", "gateway"])
        self.assertEqual(self.service.engine.report()["mode"], "upgrade")

    def test_full_selection_and_no_early_archive(self):
        self.plan(("web", "gateway", "apk")); self.apply()
        self.assertCountEqual([m for m, _ in self.fake.archive_requests], ["web", "gateway", "apk"])

    def test_partial_github_denial_blocks_next_and_shows_each_status(self):
        def deny(request):
            if "hestia-mobile-gateway" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 403, "fixture", {}, None)
        self.fake.override = deny
        self.page.locator("#next-button").click(); self.step(1)
        expect(self.page.locator("#next-button")).to_be_disabled()
        self.page.locator("#github-credential").fill(DUMMY)
        self.page.locator("#validate-github").click()
        expect(self.page.locator("#wizard-message")).to_contain_text("Accès refusé")
        expect(self.page.locator("#github-repositories")).to_contain_text("Refusé")
        expect(self.page.locator("#next-button")).to_be_disabled()
        expect(self.page.locator("#github-credential")).to_have_value("")
        self.assertIsNone(self.service.engine.report())
        self.assertEqual(self.fake.archive_requests, [])

    def test_preflight_failure_blocks_next_and_server_plan(self):
        self.github(); self.page.locator("#next-button").click(); self.step(2)
        self.preflight_patch.stop()
        self.preflight_patch = patch("installer.wizard.run_read_only_preflight", return_value=[])
        self.preflight_patch.start()
        self.page.locator("#run-preflight").click()
        expect(self.page.locator("#preflight-results")).to_contain_text("Manquant")
        expect(self.page.locator("#next-button")).to_be_disabled()
        self.assertEqual(self.fake.archive_requests, [])

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
        self.page.wait_for_function("document.getElementById('wizard-form').getAttribute('aria-busy') === 'false'")
        self.refresh(); self.step(3)
        expect(self.page.locator("#module-apk")).to_be_checked()
        expect(self.page.locator("#module-web")).not_to_be_checked()
        expect(self.page.locator("#installation-mode")).to_have_value("upgrade")

    def test_reset_unapproved_plan_then_new_selection(self):
        self.plan()
        self.page.locator("#previous-button").click(); self.dialog("cancel")
        self.step(4); self.assertIsNotNone(self.service.engine.report())
        self.page.locator("#previous-button").click(); self.dialog()
        self.step(3); self.assertIsNone(self.service.engine.report())
        self.page.locator("#module-apk").check()
        self.page.locator("#next-button").click(); self.step(4); self.apply()
        self.assertCountEqual([m for m, _ in self.fake.archive_requests], ["web", "apk"])

    def test_refresh_while_server_busy_never_replays(self):
        started, release = threading.Event(), threading.Event()
        self.fake.block = lambda: (started.set(), release.wait(10))
        self.plan()
        session = json.loads(self.http("/api/session")["body"])
        payload = confirm(self.service.engine.report())
        replies = []
        worker = threading.Thread(target=lambda: replies.append(self.http("/api/installation/apply", {
            "method": "POST", "headers": {"Content-Type": "application/json", "X-Hestia-CSRF": session["csrf_token"]},
            "body": json.dumps(payload),
        })))
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            self.refresh()
            expect(self.page.locator("body")).to_have_attribute("data-wizard-step", "5")
            expect(self.page.locator("#resume-installation")).to_be_disabled()
            self.assertEqual(len(self.fake.archive_requests), 1)
        finally:
            release.set()
            worker.join(timeout=15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(replies[0]["status"], 200)
        expect(self.page.locator("#execution-state")).to_have_attribute("data-state", "DONE")
        self.assertEqual(len(self.fake.archive_requests), 1)

    def test_failure_credential_renewal_retry_and_rollback_confirmation(self):
        self.fake.archive_override = b"invalid archive"
        self.plan(); self.page.locator("#confirm-plan").check(); self.page.locator("#next-button").click()
        expect(self.page.locator("#execution-state")).to_have_attribute("data-state", "FAILED")
        self.step(5)
        self.fake.archive_override = None
        self.page.locator("#renew-credential").click(); self.step(1); self.validate()
        self.page.locator("#next-button").click(); self.step(5)
        self.page.locator("#retry-github-web").click(); self.dialog()
        expect(self.page.locator("#execution-state")).to_have_attribute("data-state", "DONE")
        self.step(5)
        self.page.locator("#rollback-github-web").click(); self.dialog("cancel")
        self.assertEqual(self.service.engine.report()["state"], "DONE")
        self.page.locator("#rollback-github-web").click(); self.dialog()
        expect(self.page.locator("#execution-state")).to_have_attribute("data-state", "ROLLED_BACK")

    def shared_public_fixture(self):
        from shared_public_fixture import attach
        fixture = attach(self, self.service)
        self.refresh(); self.step(5)
        return fixture

    def prepare_shared_public(self):
        self.page.locator('#shared-public-networks').fill('127.0.0.10/32')
        self.page.locator('#prepare-shared-public').click()
        self.page.locator('#plan-shared-public').click()
        expect(self.page.locator('#shared-public-state')).to_have_attribute('data-state', 'PLANNED')

    def test_shared_public_separate_consent_cancel_completion_and_historical_refresh(self):
        fixture = self.shared_public_fixture(); self.prepare_shared_public()
        self.assertEqual(fixture.calls, [])
        self.page.locator('#apply-shared-public').click(); self.dialog('cancel')
        self.assertIsNone(self.service.shared_public.journal.read()['approved_plan_sha256'])
        self.assertEqual(fixture.calls, [])
        self.page.locator('#apply-shared-public').click(); self.dialog()
        expect(self.page.locator('#shared-public-state')).to_have_attribute('data-state', 'DONE')
        self.assertEqual(fixture.calls, ['enroll', 'handoff', 'certificate', 'dry-run', 'publish', 'renewal', 'verify'])
        self.refresh(); self.step(5)
        expect(self.page.locator('#shared-public-state')).to_have_attribute('data-state', 'DONE')
        expect(self.page.locator('#shared-public-verification')).to_contain_text('Aucun contrôle actuel')
        self.assertEqual(len(fixture.calls), 7)
        self.page.locator('#check-shared-public').click()
        expect(self.page.locator('#shared-public-verification')).to_contain_text('Frontal et services liés contrôlés')
        self.assertEqual(len(fixture.calls), 7)

    def test_shared_public_focused_networks_survive_poll_and_no_automatic_execution(self):
        fixture = self.shared_public_fixture()
        self.page.locator('#shared-public-networks').fill('192.0.2.0/24')
        self.page.wait_for_timeout(1750)
        expect(self.page.locator('#shared-public-networks')).to_have_value('192.0.2.0/24')
        self.assertEqual(fixture.calls, []); self.assertIsNone(self.service.shared_public.profile())

    def preparation_fixture(self):
        import test_mobile_preparation_plan as fixtures
        fixture = fixtures.MobilePreparationPlanTests('test_plan_reads_are_repeatable_without_native_observation')
        self.addCleanup(fixture.doCleanups); fixture.setUp()
        self.quiesce_page(); self.service.close(); self.service = fixture.service
        self.server.state.transaction_service = self.service
        self.refresh(); self.step(5); self.page.locator('#plan-mobile-preparation').click()
        expect(self.page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'AWAITING_CONFIRMATION')
        return fixture

    def preparation_credentials(self, fixture):
        for name, value in fixture.credentials.items(): self.page.locator('#preparation-' + name).fill(value)
        self.page.locator('#preparation-sql-consent').check()

    def test_mobile_preparation_cancel_then_complete_without_activation(self):
        fixture = self.preparation_fixture(); self.preparation_credentials(fixture)
        self.page.locator('#apply-mobile-preparation').click(); self.dialog('cancel')
        fixture.native.execute.assert_not_called(); self.assertIsNone(fixture.control._read('approved.json'))
        for name in fixture.credentials: expect(self.page.locator('#preparation-' + name)).to_have_value('')
        self.preparation_credentials(fixture); self.page.locator('#apply-mobile-preparation').click(); self.dialog()
        expect(self.page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'DONE')
        self.assertEqual(fixture.native.execute.call_count, 6); fixture.effects.assert_not_called()
        self.refresh(); self.step(5)
        expect(self.page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'DONE')
        expect(self.page.locator('#plan-mobile-activation')).to_be_visible()
        self.assertEqual(fixture.native.execute.call_count, 6)
        for secret in fixture.credentials.values(): self.assertNotIn(secret, self.http('/api/installation/report')['body'])

    def test_mobile_preparation_resume_keeps_completed_stages(self):
        fixture = self.preparation_fixture()
        def interrupted(stage):
            if stage == 'external': raise RuntimeError('interrupted')
            return fixture.prepared_stage(stage)
        fixture.native.execute.side_effect = interrupted
        self.preparation_credentials(fixture); self.page.locator('#apply-mobile-preparation').click(); self.dialog()
        expect(self.page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'RESUME_REQUIRED')
        self.refresh(); self.step(5); self.assertEqual(fixture.native.execute.call_count, 3)
        fixture.native.execute.side_effect = fixture.prepared_stage
        self.preparation_credentials(fixture); self.page.locator('#resume-mobile-preparation').click(); self.dialog()
        expect(self.page.locator('#mobile-preparation-state')).to_have_attribute('data-state', 'DONE')
        self.assertEqual([x.args[0] for x in fixture.native.execute.call_args_list].count('gateway'), 1)
        fixture.effects.assert_not_called()

    def test_mobile_preparation_sql_consent_and_focused_field_survive_poll(self):
        fixture = self.preparation_fixture()
        self.page.locator('#preparation-database_password').fill(fixture.credentials['database_password'])
        self.page.wait_for_timeout(1750)
        expect(self.page.locator('#preparation-database_password')).to_have_value(fixture.credentials['database_password'])
        self.page.locator('#apply-mobile-preparation').click()
        expect(self.page.locator('#operation-dialog')).not_to_be_visible(); fixture.native.execute.assert_not_called()

    def backup_fixture(self):
        import test_mobile_backup_plan as fixtures
        fixture = fixtures.MobileBackupPlanTests('test_plan_and_reads_preserve_parents_and_never_observe_host')
        self.addCleanup(fixture.doCleanups); fixture.setUp()
        self.quiesce_page(); self.service.close(); self.service = fixture.service
        self.server.state.transaction_service = self.service
        self.refresh(); self.step(5)
        self.page.locator('#plan-mobile-backup').click()
        expect(self.page.locator('#mobile-backup-state')).to_have_attribute('data-state', 'AWAITING_CONFIRMATION')
        return fixture

    def backup_credentials(self, fixture):
        for name, value in fixture.credentials.items(): self.page.locator('#backup-' + name).fill(value)
        self.page.locator('#backup-sql-consent').check()

    def test_mobile_backup_confirmation_cancel_and_historical_completion(self):
        fixture = self.backup_fixture(); self.backup_credentials(fixture)
        self.page.locator('#apply-mobile-backup').click(); self.dialog('cancel')
        fixture.factory.assert_not_called(); self.assertIsNone(fixture.control._read('approved.json'))
        for name in fixture.credentials: expect(self.page.locator('#backup-' + name)).to_have_value('')
        self.backup_credentials(fixture); self.page.locator('#apply-mobile-backup').click(); self.dialog()
        expect(self.page.locator('#mobile-backup-state')).to_have_attribute('data-state', 'DONE')
        self.refresh(); self.step(5)
        expect(self.page.locator('#mobile-backup-state')).to_have_attribute('data-state', 'DONE')
        self.assertEqual(fixture.operation.create_and_verify.call_count, 1)
        for secret in fixture.credentials.values(): self.assertNotIn(secret, self.http('/api/installation/report')['body'])

    def test_mobile_backup_sql_consent_poll_and_explicit_resume(self):
        fixture = self.backup_fixture()
        self.page.locator('#backup-database_password').fill(fixture.credentials['database_password'])
        self.page.wait_for_timeout(1750)
        expect(self.page.locator('#backup-database_password')).to_have_value(fixture.credentials['database_password'])
        self.page.locator('#apply-mobile-backup').click()
        expect(self.page.locator('#operation-dialog')).not_to_be_visible(); fixture.factory.assert_not_called()
        fixture.operation.create_and_verify.side_effect = RuntimeError('interrupted backup')
        self.backup_credentials(fixture); self.page.locator('#apply-mobile-backup').click(); self.dialog()
        expect(self.page.locator('#mobile-backup-state')).to_have_attribute('data-state', 'RESUME_REQUIRED')
        self.refresh(); self.step(5); self.assertEqual(fixture.operation.create_and_verify.call_count, 1)
        fixture.operation.create_and_verify.side_effect = fixture.completed
        self.backup_credentials(fixture); self.page.locator('#resume-mobile-backup').click(); self.dialog()
        expect(self.page.locator('#mobile-backup-state')).to_have_attribute('data-state', 'DONE')
        fixture.scope.acquire.assert_called_once()

    def mobile_fixture(self):
        import test_mobile_activation_plan as fixtures
        fixture = fixtures.MobileActivationPlanTests('test_plan_is_separate_repeatable_and_read_only_for_all_parents')
        self.addCleanup(fixture.doCleanups); fixture.setUp()
        self.quiesce_page(); self.service.close(); self.service = fixture.service
        self.server.state.transaction_service = self.service
        self.refresh(); self.step(5)
        self.page.locator('#plan-mobile-activation').click()
        expect(self.page.locator('#mobile-activation-state')).to_have_attribute('data-state', 'AWAITING_CONFIRMATION')
        return fixture

    def mobile_credentials(self, fixture):
        for name, value in fixture.credentials.items(): self.page.locator('#mobile-' + name).fill(value)
        self.page.locator('#mobile-sql-consent').check()

    def test_mobile_activation_confirmation_cancel_secrets_and_check(self):
        fixture = self.mobile_fixture(); self.mobile_credentials(fixture)
        self.page.locator('#apply-mobile-activation').click(); self.dialog('cancel')
        fixture.effects.assert_not_called(); self.assertIsNone(fixture.control._read('approved.json'))
        for name in fixture.credentials: expect(self.page.locator('#mobile-' + name)).to_have_value('')
        self.mobile_credentials(fixture); self.page.locator('#apply-mobile-activation').click(); self.dialog()
        expect(self.page.locator('#mobile-activation-state')).to_have_attribute('data-state', 'DONE')
        self.page.locator('#check-mobile-activation').click()
        expect(self.page.locator('#mobile-activation-availability')).to_contain_text('Services locaux et page de connexion vérifiés')
        report = self.http('/api/installation/report')['body']
        for value in fixture.credentials.values(): self.assertNotIn(value, report)
        self.refresh(); self.step(5)
        expect(self.page.locator('#mobile-activation-state')).to_have_attribute('data-state', 'DONE')
        self.assertEqual(fixture.effects.call_count, 1); self.assertEqual(fixture.serving.call_count, 1)

    def test_mobile_activation_refresh_and_credentialless_explicit_resume(self):
        fixture = self.mobile_fixture()
        def interrupted(*args, **kwargs):
            fixture.completed()
            for path in fixture.native_root.iterdir():
                if path.name not in ('plan.json', 'admitted.json', 'php.intent.json'): path.unlink()
            raise RuntimeError('lost start response')
        fixture.effects.side_effect = interrupted
        self.mobile_credentials(fixture); self.page.locator('#apply-mobile-activation').click(); self.dialog()
        expect(self.page.locator('#mobile-activation-state')).to_have_attribute('data-state', 'RESUME_REQUIRED')
        self.refresh(); self.step(5)
        expect(self.page.locator('#resume-mobile-activation')).to_be_visible()
        self.assertEqual(fixture.effects.call_count, 1); fixture.serving.assert_not_called()
        self.page.locator('#resume-mobile-activation').click(); self.dialog('cancel')
        fixture.serving.assert_not_called()
        self.page.locator('#resume-mobile-activation').click(); self.dialog()
        expect(self.page.locator('#mobile-activation-state')).to_have_attribute('data-state', 'DONE')
        self.assertEqual(fixture.serving.call_args.kwargs['action'], 'resume')
        self.assertEqual(fixture.effects.call_count, 1)

    def test_mobile_activation_sql_consent_and_poll_do_not_discard_focused_input(self):
        fixture = self.mobile_fixture()
        self.page.locator('#mobile-database_password').fill(fixture.credentials['database_password'])
        self.page.wait_for_timeout(1750)
        expect(self.page.locator('#mobile-database_password')).to_have_value(fixture.credentials['database_password'])
        self.page.locator('#apply-mobile-activation').click()
        expect(self.page.locator('#wizard-message')).to_contain_text('Renseignez les trois identifiants')
        expect(self.page.locator('#operation-dialog')).not_to_be_visible(); fixture.effects.assert_not_called()
        self.assertIsNone(fixture.control._read('approved.json'))

    def test_keyboard_navigation_and_dialog_escape(self):
        self.page.locator("#next-button").focus(); self.page.keyboard.press("Enter"); self.step(1)
        self.page.keyboard.press("Tab")
        expect(self.page.locator("#github-credential")).to_be_focused()
        self.page.locator("#github-credential").fill(DUMMY); self.page.keyboard.press("Enter")
        expect(self.page.locator("#next-button")).to_be_enabled()
        self.page.locator("#cancel-button").click()
        expect(self.page.locator("#cancel-dialog")).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(self.page.locator("#cancel-dialog")).not_to_be_visible()
        expect(self.page.locator("#cancel-button")).to_be_focused()

    def test_responsive_plan_and_reduced_motion(self):
        self.plan(("web", "gateway", "apk"))
        sizes = ((1920,1080),(1440,900),(1366,768),(1280,720),(1024,768),(840,600),(768,1024),(390,844),(360,640),(320,568))
        # Like the native suite, wait for CSS dynamic viewport units to catch up
        # with the requested viewport. Two animation frames alone can still see
        # old body min-height. Never wait for the tested footer bounds to pass.
        for cycle in range(3):
            for width, height in sizes:
                with self.subTest(cycle=cycle, width=width, height=height):
                    self.page.set_viewport_size({"width": width, "height": height})
                    self.page.wait_for_function("""({width,height}) =>
                        innerWidth === width && innerHeight === height &&
                        Math.round(parseFloat(getComputedStyle(document.body).minHeight)) === height
                    """, arg={"width": width, "height": height})
                    self.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    result = self.page.evaluate("""() => {
                      const footer = document.querySelector('.wizard-actions').getBoundingClientRect();
                      const root = document.querySelector('.installer-window').getBoundingClientRect();
                      const button = document.getElementById('next-button').getBoundingClientRect();
                      const inner = document.getElementById('wizard-content');
                      return {width: document.documentElement.scrollWidth, viewport: innerWidth,
                        footerBottom: footer.bottom, rootBottom: root.bottom, buttonRight: button.right,
                        panelOverflow: inner.scrollWidth > inner.clientWidth + 1,
                        transition: getComputedStyle(inner).transitionDuration};
                    }""")
                    self.assertLessEqual(result["width"], width + 1, result)
                    self.assertFalse(result["panelOverflow"], result)
                    self.assertLessEqual(result["buttonRight"], width, result)
                    self.assertLessEqual(result["footerBottom"], result["rootBottom"] + 1, result)
                    if width > 820: self.assertLessEqual(result["footerBottom"], height, result)
                    self.assertIn("1e-05s", result["transition"])
        self.page.set_viewport_size({"width":1366,"height":768})
        self.page.wait_for_function("() => innerHeight === 768 && parseFloat(getComputedStyle(document.body).minHeight) === 768")
        screenshot_dir = os.environ.get("HESTIA_QC_SCREENSHOTS")
        if screenshot_dir:
            Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(Path(screenshot_dir) / "wizard-plan-desktop.png"))

    def test_initial_network_failure_has_explicit_reconnect(self):
        self.fail_start_once = True
        self.refresh()
        expect(self.page.locator("#retry-connection")).to_be_visible()
        expect(self.page.locator("#next-button")).to_be_disabled()
        self.page.locator("#retry-connection").click()
        expect(self.page.locator("#next-button")).to_be_enabled()
        self.step(0)

    def test_stale_tab_choices_are_not_overwritten_and_can_reload(self):
        self.modules()
        saved = self.service.wizard.read()
        self.service.wizard.save({**saved, "mode": "upgrade"})
        self.page.locator("#module-apk").check()
        expect(self.page.locator("#wizard-message")).to_contain_text("autre onglet")
        expect(self.page.locator("#next-button")).to_be_disabled()
        self.page.locator("#reload-state").click(); self.step(3)
        expect(self.page.locator("#installation-mode")).to_have_value("upgrade")
        expect(self.page.locator("#module-apk")).not_to_be_checked()

    def test_raw_error_text_is_never_injected_or_displayed(self):
        self.page.locator("#next-button").click(); self.step(1)
        self.page.evaluate("""() => {const original = window.fetch;
          window.fetch = (path, opts) => path === '/api/github/validate'
            ? Promise.resolve(new Response(JSON.stringify({error:'<img src=x onerror=alert(1)>PRIVATE-FIXTURE'}), {status:500}))
            : original(path, opts);
        }""")
        self.page.locator("#github-credential").fill(DUMMY)
        self.page.locator("#validate-github").click()
        expect(self.page.locator("#wizard-message")).to_contain_text("Action indisponible")
        self.assertNotIn("PRIVATE-FIXTURE", self.page.locator("body").inner_text())
        self.assertNotIn(DUMMY, self.page.content())
        expect(self.page.locator("#github-credential")).to_have_value("")

    def test_forms_remain_accessible_across_viewports(self):
        self.page.locator("#next-button").click(); self.step(1)
        for cycle in range(3):
            for width, height in ((1366,768),(1024,768),(768,1024),(390,844),(320,568)):
                with self.subTest(cycle=cycle, width=width, height=height):
                    self.page.set_viewport_size({"width":width,"height":height})
                    # Synchronize the requested INPUT, as in the plan/native
                    # recipes. Do not wait for the overflow assertion to pass.
                    self.page.wait_for_function("""({width,height}) =>
                        innerWidth === width && innerHeight === height &&
                        Math.round(parseFloat(getComputedStyle(document.body).minHeight)) === height
                    """, arg={"width": width, "height": height})
                    self.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width + 1)
                    self.page.locator("#github-credential").fill("z" * 255)
                    expect(self.page.locator("#github-credential")).to_have_value("z" * 255)
                    self.page.locator("#validate-github").scroll_into_view_if_needed()
                    expect(self.page.locator("#validate-github")).to_be_visible()
        self.assertEqual(self.fake.archive_requests, [])

    def test_expired_session_is_rejected_by_https_without_replay(self):
        self.plan()
        self.quiesce_page(); self.page.close(); self.page = None
        self.server.state.session_store.clear()
        self.assertEqual(self.http("/api/wizard/state")["status"], 401)
        self.assertEqual(self.fake.archive_requests, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
