#!/usr/bin/env python3
"""Actual browser + product composition on a disposable prepared host only.

GitHub transport serves an exact pinned archive. SQL, local accounts, deployment,
finalization, Apache/FPM staging, HTTPS and the operator journal are production.
The optional final login starts services in the fixture, never as a product claim.
"""
import argparse
from contextlib import contextmanager
import http.cookiejar
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import application_journal_systemd as journal
from github_fixture import DUMMY, FakeGitHub, tar_bytes, confirm
from installer import application_plan as app, system_drain as drain
from installer.bootstrap import prepare_bootstrap, serve_in_thread
from installer.engine import TransactionEngine
from installer.github_client import GitHubAccess, GitHubClient
from installer.github_sources import GitHubAcquisition
from installer.operations import default_registry
from installer.service import TransactionService
from installer.transaction import StateJournal
from http_runtime_systemd import command, until

TARGET = None


class ApplicationWizardLive(journal.JournalMixin, journal.fresh.FinalizationIntegration):
    def setUp(self):
        super().setUp()
        self.fake = FakeGitHub()
        entries = [(p.relative_to(TARGET).as_posix(), p.read_bytes(), p.stat().st_mode & 0o777)
                   for p in sorted(TARGET.rglob('*')) if p.is_file()]
        self.fake.archive_override = tar_bytes('web', app.STORAGE_COMMIT, entries)
        self.profile = None; self.services = []; self.nginx = None
        self.addCleanup(self.dispose_profile)
        self.service = self.build_service()

    def build_service(self, hook=None):
        engine = TransactionEngine(self.operator_journal, default_registry(), fault_hook=hook)
        access = GitHubAccess(engine.secrets, GitHubClient(opener=self.fake))
        github = GitHubAcquisition(engine, access, restore=False)
        if not app.ApplicationPlan(engine, github).restore(): github.restore_registry()
        service = TransactionService(engine, github=github)
        self.services.append(service); return service

    def dispose_profile(self):
        for service in self.services: service.close()
        if self.nginx is not None:
            self.nginx.terminate(); self.nginx.wait(timeout=10)
        if self.profile is None: return
        http = self.profile.http(self.saved['configuration']); collector = app.cleaner.SessionCleaner(http)
        for unit in (collector.timer, collector.unit, http.unit('apache'), http.unit('php')):
            command('systemctl', 'stop', unit, check=False); command('systemctl', 'reset-failed', unit, check=False)
            (drain.UNIT_ROOT / unit).unlink(missing_ok=True)
            shutil.rmtree(drain.UNIT_ROOT / (unit + '.d'), ignore_errors=True)
        command('systemctl', 'daemon-reload')
        for identity in (self.profile.identity, self.profile.worker):
            command('userdel', identity.user, check=False); command('groupdel', identity.user, check=False)
            shutil.rmtree(identity.directory, ignore_errors=True)
        for path in (self.profile.root, self.profile.config_root, self.profile.webroot): shutil.rmtree(path, ignore_errors=True)
        # SQL fixture disposal owns its DB/account cleanup, not the product.
        import hashlib
        name = 'hdf_' + hashlib.sha256(self.db.lower().encode()).hexdigest()[:24]
        self.sql([f"DROP USER IF EXISTS `{name}`@'localhost'"])

    def choices(self):
        return {'revision': 0, 'configuration': {'hostname': self.payload['web']['hostname'],
            'database': {k: self.payload['database'][k] for k in ('mode', 'name', 'user')},
            'administrator': self.payload['administrator'], 'assistant': self.payload['assistant']},
            'credentials': {**{k: v for k, v in self.payload['secrets'].items() if v},
                'migration_user': self.credentials._user, 'migration_password': self.credentials._password,
                **({'authority_user': self.authority._user, 'authority_password': self.authority._password}
                   if self.payload['database']['mode'] == 'managed' else {})}}

    def planned(self, hook=None):
        self.saved = self.service.application.save(self.choices()); self.profile = app.FreshProfile(self.saved['instance'])
        self.service.execute('github.validate', {'credential': DUMMY})
        document = self.service.execute('wizard.plan', {'modules': ['web'], 'refs': {}, 'mode': 'fresh',
            'application_revision': self.saved['revision']})['installation']
        self.service.engine._fault_hook = hook
        return document

    def staged(self, service):
        document = service.engine.report(); self.assertEqual(document['state'], 'DONE', document['last_error_redacted'])
        config = self.saved['configuration']; http = self.profile.http(config)
        self.assertEqual(http.observe()['state'], 'HTTP_RUNTIME_STAGED')
        self.assertEqual(app.cleaner.SessionCleaner(http).observe()['state'], 'SESSION_CLEANER_STAGED')
        slot = self.profile.config_root / app.db.fs.configuration_slot(config)
        self.assertEqual(json.loads((slot / 'seal.json').read_bytes())['instance'], self.saved['instance'])
        self.assertEqual(self.sql(query=f'SELECT COUNT(*) n FROM `{self.db}`.UserInfo')[0]['n'], 1)
        self.assertFalse(service.application.state()['application_installed'])
        for secret in self.choices()['credentials'].values():
            self.assertNotIn(secret, json.dumps(service.wizard_state()))
        return http

    @contextmanager
    def browser(self):
        from playwright.sync_api import sync_playwright
        with prepare_bootstrap(web_root=ROOT / 'installer/web', runtime_root=self.root / 'bootstrap',
                bind_address='127.0.0.1', interactive=False, transaction_service=self.service) as prepared:
            thread = serve_in_thread(prepared)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                context = browser.new_context(ignore_https_errors=True, viewport={'width': 1366, 'height': 900})
                page = context.new_page(); page.set_default_timeout(120000)
                errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    page.goto('https://127.0.0.1:' + str(prepared.port)); page.locator('#bootstrap-code').fill(prepared.bootstrap_code)
                    page.locator('#bootstrap-form button[type=submit]').click()
                    yield page
                    self.assertEqual(errors, [])
                finally:
                    context.close(); browser.close(); prepared.server.shutdown(); thread.join(timeout=5)

    def wizard(self):
        from playwright.sync_api import expect
        choices = self.choices(); fields = choices['configuration']
        with self.browser() as page:
            page.locator('#next-button').click()
            page.locator('#github-credential').fill(DUMMY); page.locator('#validate-github').click()
            page.locator('#next-button').click(); page.locator('#run-preflight').click()
            page.locator('#next-button').click(); page.locator('#prepare-web-application').check()
            page.locator('#application-database-mode').select_option(fields['database']['mode'])
            for name, value in {'hostname': fields['hostname'], 'database-name': fields['database']['name'],
                'database-user': fields['database']['user'], 'first-name': fields['administrator']['first_name'],
                'last-name': fields['administrator']['last_name'], 'email': fields['administrator']['email'],
                **choices['credentials']}.items(): page.locator('#application-' + name).fill(value)
            page.locator('#save-web-application').click()
            expect(page.locator('#wizard-message')).to_contain_text('Configuration enregistrée')
            self.saved = self.service.application.read(); self.profile = app.FreshProfile(self.saved['instance'])
            self.assertFalse(self.profile.root.exists())
            page.locator('#next-button').click(); expect(page.locator('#confirm-plan')).to_be_visible()
            before = self.service.engine.report(); self.assertEqual(len(before['plan']['steps']), 10)
            self.assertFalse(self.profile.root.exists()); self.assertEqual(self.fake.archive_requests, [])
            page.reload(); expect(page.locator('#confirm-plan')).not_to_be_checked()
            self.assertEqual(self.service.engine.report(), before)
            page.locator('#confirm-plan').check(); page.locator('#next-button').click()
            expect(page.locator('#execution-state')).to_have_attribute('data-state', 'DONE', timeout=180000)
            expect(page.locator('#execution-state')).to_contain_text('Web préparé sous maintenance')
            self.staged(self.service)
            page.reload(); expect(page.locator('#execution-state')).to_have_attribute('data-state', 'DONE')
            self.assertEqual(len(self.fake.archive_requests), 1)
            for secret in choices['credentials'].values(): self.assertNotIn(secret, page.content())
            page.screenshot(path='/evidence/' + self._testMethodName + '.png', full_page=True)

    def fixture_login(self, runtime):
        """A real login proof; these service starts explicitly remain fixture work."""
        spec = runtime.spec; scope = runtime._scope(pwd.getpwnam(spec.service_user))
        with scope.recover(scope.observe()['lease_id'], confirmed=True) as lease: lease.resume(confirmed=True)
        for role in ('php', 'apache'): command('systemctl', 'start', runtime.unit(role))
        front = self.root / 'frontend'; front.mkdir(mode=0o755)
        cert, key = front / 'fixture.crt', front / 'fixture.key'
        command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=' + spec.hostname,
            '-addext', 'subjectAltName=IP:127.0.0.1,DNS:' + spec.hostname, '-keyout', str(key), '-out', str(cert)); key.chmod(0o600)
        with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        config = front / 'nginx.conf'
        config.write_text(f'user {spec.service_user};\npid {front}/pid;\nerror_log {front}/error.log warn;\nevents {{ worker_connections 32; }}\nhttp {{ access_log off; client_body_temp_path {front}/body; proxy_temp_path {front}/proxy;\n'
            + spec.ingress.nginx_server(spec.hostname, spec.port, tls_port=port, certificate=cert, private_key=key, listen_address='127.0.0.1') + '}\n')
        command('nginx', '-t', '-p', str(front) + '/', '-c', str(config))
        self.nginx = subprocess.Popen(['/usr/sbin/nginx', '-p', str(front) + '/', '-c', str(config), '-g', 'daemon off;'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def request(path, data=None):
            request = urllib.request.Request('https://127.0.0.1:' + str(port) + path,
                data=urllib.parse.urlencode(data).encode() if data else None, headers={'Host': spec.hostname})
            try: response = opener.open(request, timeout=10)
            except urllib.error.HTTPError as error: response = error
            with response: return response.status, response.read().decode(), response.geturl()
        def ready():
            try: return request('/login.php')[0] == 200
            except OSError: return False
        until(ready, timeout=12)
        body = request('/login.php')[1]; token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', body).group(1)
        result = request('/login.php', {'csrf_token': token, 'identifier': self.payload['administrator']['email'], 'password': self.payload['secrets']['admin_password']})
        self.assertEqual(result[0], 200); self.assertNotIn('/login.php', result[2])
        self.assertIn('/login.php', request('/logout.php')[2])

    def test_composition_browser_managed_fresh_and_real_fixture_login(self):
        self.managed(); self.wizard(); self.fixture_login(self.profile.http(self.saved['configuration']))

    def test_composition_browser_existing_local_fresh_stays_gated(self):
        self.wizard(); self.staged(self.build_service())

    def test_composition_final_reply_lost_reopens_same_plan_with_only_database_secret(self):
        document = self.planned(self.hook('web.finalization'))
        self.kill_child(lambda: self.service.engine.apply(document['plan_sha256']))
        before = self.sql(query=f'SELECT id_user,email,password_hash FROM `{self.db}`.UserInfo')
        other = self.build_service()
        self.assertEqual(other.wizard_state()['installation']['state'], 'RUNNING')
        other.execute('web.credentials', {'confirmation': document['plan_sha256'], 'credentials': {
            'database_password': self.payload['secrets']['database_password']}})
        self.assertEqual(other.execute('resume', confirm(document))['installation']['state'], 'DONE')
        self.staged(other)
        self.assertEqual(before, self.sql(query=f'SELECT id_user,email,password_hash FROM `{self.db}`.UserInfo'))

    def test_composition_worker_reply_lost_is_recovered_without_a_second_account(self):
        document = self.planned(self.hook('web.worker-identity'))
        self.kill_child(lambda: self.service.engine.apply(document['plan_sha256']))
        uid = pwd.getpwnam(self.profile.worker.user).pw_uid
        other = self.build_service(); other.execute('web.credentials', {'confirmation': document['plan_sha256'], 'credentials': self.choices()['credentials']})
        self.assertEqual(other.execute('resume', confirm(document))['installation']['state'], 'DONE')
        self.assertEqual(pwd.getpwnam(self.profile.worker.user).pw_uid, uid); self.staged(other)

    def test_composition_partial_directories_remain_manual_without_recreation(self):
        document = self.planned(); write = app.f._write
        def interrupted(fd, name, *args, **kwargs):
            if name == 'profile.json': os.kill(os.getpid(), signal.SIGKILL)
            return write(fd, name, *args, **kwargs)
        def child():
            with patch.object(app.f, '_write', side_effect=interrupted): self.service.engine.apply(document['plan_sha256'])
        self.kill_child(child)
        inode = self.profile.root.stat().st_ino; other = self.build_service()
        self.assertEqual(other.execute('resume', confirm(document))['installation']['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(self.profile.root.stat().st_ino, inode); self.assertFalse(self.profile.webroot.exists())

    def test_composition_secrets_missing_before_sql_then_targeted_retry(self):
        def hook(name, phase, event):
            if (name, phase, event) == ('web.source-deployment', 'done', 'checkpoint'): os.kill(os.getpid(), signal.SIGKILL)
        document = self.planned(hook)
        self.kill_child(lambda: self.service.engine.apply(document['plan_sha256']))
        other = self.build_service(); result = other.execute('resume', confirm(document))['installation']
        self.assertEqual(result['last_error_redacted'], 'SECRET_REQUIRED')
        before = [row for row in result['steps'] if row['state'] == 'DONE']
        other.execute('web.credentials', {'confirmation': document['plan_sha256'], 'credentials': self.choices()['credentials']})
        retried = other.execute('retry', confirm(document, name='web.database'))['installation']
        self.assertEqual(retried['state'], 'PLANNED')
        self.assertEqual(next(row for row in retried['steps'] if row['name'] == 'web.database')['state'], 'DONE')
        self.assertEqual(other.execute('resume', confirm(document))['installation']['state'], 'DONE')
        self.assertEqual(before, other.engine.report()['steps'][:len(before)]); self.staged(other)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--shard-index', type=int, choices=(0, 1), required=True); args = parser.parse_args()
    if os.environ.get('HESTIA_APPLICATION_WIZARD_TEST') != '1': raise RuntimeError('Explicit wizard recipe opt-in required')
    journal.fresh.WEB = args.web; TARGET = args.target
    source = quality.snapshot(ROOT)
    names = sorted(name for name in ApplicationWizardLive.__dict__ if name.startswith('test_composition_'))[args.shard_index::2]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ApplicationWizardLive(name) for name in names))
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Native application wizard and immutable composition', 'tests': result.testsRun, 'expected': 3,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 3 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'shard': args.shard_index,
        'phase5_complete': False, 'application_installed': False, 'wizard_fresh_staging_wired': True,
        'service_activation_delivered': False, 'upgrade_wizard_delivered': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'WIZARD-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
