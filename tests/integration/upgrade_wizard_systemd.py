#!/usr/bin/env python3
"""Six new wizard upgrade scenarios, only inside disposable Debian/Ext4 CI."""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import sys
import unittest
import urllib.parse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import quality
import application_journal_systemd as journal
import application_wizard_systemd as wizard
from github_fixture import DUMMY, FakeGitHub, Response, tar_bytes, confirm
from installer import upgrade_plan as up, system_drain as drain
from installer.engine import TransactionEngine
from installer.github_client import GitHubAccess, GitHubClient
from installer.github_sources import GitHubAcquisition
from installer.operations import default_registry
from installer.service import TransactionService

ARCHIVES = {}


class UpgradeWizardLive(journal.JournalMixin, journal.previous.RecoveryLive):
    browser = wizard.ApplicationWizardLive.browser

    def setUp(self):
        super().setUp(); self.fake = FakeGitHub(); self.services = []
        def archive(request):
            url = urllib.parse.urlsplit(request.full_url)
            if url.hostname == 'codeload.github.com':
                commit = url.path.rsplit('/', 1)[1]; self.fake.archive_requests.append(('web', commit))
                return Response(ARCHIVES[commit])
        self.fake.override = archive
        self.addCleanup(lambda: [service.close() for service in self.services])

    def build_service(self, hook=None):
        engine = TransactionEngine(self.operator_journal, default_registry(), fault_hook=hook)
        access = GitHubAccess(engine.secrets, GitHubClient(opener=self.fake))
        service = TransactionService(engine, github=GitHubAcquisition(engine, access, restore=False))
        self.services.append(service); return service

    def registered(self):
        self.ready(); self.service = self.build_service(); spec = self.http_runtime.spec
        descriptor = {'version': 1, 'http': {k: str(getattr(spec, k)) if isinstance(getattr(spec, k), Path) else getattr(spec, k) for k in up.HTTP_FIELDS},
            'worker': {'user': pwd.getpwuid(self.runtime.worker_uid).pw_name, 'run_root': str(self.runtime.run_root), 'state_root': str(self.runtime.state_root)}}
        private = self.root / 'private-descriptor'; private.mkdir(mode=0o700)
        path = private / 'managed-profile.json'; path.write_bytes(up.canonical_bytes(descriptor)); path.chmod(0o600)
        self.saved = self.service.upgrade.register(path); self.descriptor_path = path
        self.backups.rmdir(); self.backups = up.ManagedProfile(descriptor).backups
        self.addCleanup(lambda: shutil.rmtree(self.backups.parent, ignore_errors=True))
        self.before_sql = self.sql(query=f'SELECT id_user,email,password_hash,photo_profil FROM `{self.db}`.UserInfo')
        return self.saved

    def credentials(self, service, confirmation):
        return service.execute('web.upgrade.credentials', {'confirmation': confirmation, 'credentials': {
            'database_password': self.payload['secrets']['database_password'],
            'authority_user': self.authority._user, 'authority_password': self.authority._password}})

    def planned(self, hook=None):
        self.registered(); self.credentials(self.service, self.saved['profile_sha256'])
        self.service.execute('github.validate', {'credential': DUMMY})
        document = self.service.execute('wizard.plan', {'modules': ['web'], 'refs': {}, 'mode': 'upgrade',
            'upgrade_profile_sha256': self.saved['profile_sha256']})['installation']
        self.service.engine._fault_hook = hook
        return document

    def done(self, service, result):
        self.assertEqual(result['state'], 'DONE', [(s['name'], s['state'], s['last_error_redacted']) for s in result['steps']])
        self.assertEqual(self.before_sql, self.sql(query=f'SELECT id_user,email,password_hash,photo_profil FROM `{self.db}`.UserInfo'))
        self.operation.target_http.observe(); self.operation.target_collector.observe()
        for secret in (self.payload['secrets']['database_password'], self.authority._password):
            self.assertNotIn(secret, json.dumps(service.wizard_state()))

    def activated(self, service, parent):
        self.assertEqual(service.activation.journal.read()['state'], 'DONE')
        self.assertEqual(service.engine.report(), parent)
        result = service.execute('activation.check', confirm(service.activation.journal.read()))['activation']['availability']
        self.assertEqual(result['state'], 'LOCAL_WEB_AVAILABLE')
        self.assertEqual(result['backend'], '127.0.0.1:' + str(self.spec.port))
        self.http_runtime = self.operation.target_http; self.collector = self.operation.target_collector
        self.spec = self.http_runtime.spec; self.uploads = self.http_root / 'data/uploads'
        # No fixture start: these are the processes launched by product activation.
        pids = [drain._show(self.http_runtime.unit(role))['MainPID'] for role in ('php', 'apache')]
        self.login(); self.assertEqual(self.binary('/' + self.legacy_relative)[:2], (200, journal.previous.base.business.png()))
        self.assertEqual(pids, [drain._show(self.http_runtime.unit(role))['MainPID'] for role in ('php', 'apache')])

    def test_upgrade_browser_migration_and_separate_product_activation(self):
        from playwright.sync_api import expect
        self.registered()
        with self.browser() as page:
            page.locator('#next-button').click(); page.locator('#github-credential').fill(DUMMY); page.locator('#validate-github').click()
            page.locator('#next-button').click(); page.locator('#run-preflight').click(); page.locator('#next-button').click()
            page.locator('#upgrade-web-application').check()
            for name, value in {'database_password': self.payload['secrets']['database_password'], 'authority_user': self.authority._user,
                                'authority_password': self.authority._password}.items(): page.locator('#upgrade-' + name).fill(value)
            page.locator('#save-upgrade-credentials').click(); expect(page.locator('#wizard-message')).to_contain_text('Identifiants conservés')
            page.locator('#next-button').click(); expect(page.locator('#confirm-plan')).to_be_visible()
            document = self.service.engine.report(); self.assertEqual(len(document['plan']['steps']), 4)
            self.assertFalse(self.backups.exists()); self.assertEqual(self.fake.archive_requests, [])
            page.reload(); expect(page.locator('#confirm-plan')).not_to_be_checked(); self.assertEqual(document, self.service.engine.report())
            page.locator('#confirm-plan').check(); page.locator('#next-button').click()
            expect(page.locator('#execution-state')).to_have_attribute('data-state', 'DONE', timeout=180000)
            parent = self.service.engine.report(); self.done(self.service, parent)
            page.locator('#plan-activation').click(); expect(page.locator('#activation-state')).to_have_attribute('data-state', 'PLANNED')
            before = self.service.activation.journal.read(); page.reload(); expect(page.locator('#apply-activation')).to_be_visible()
            page.locator('#apply-activation').click(); page.keyboard.press('Escape'); self.assertEqual(self.service.activation.journal.read(), before)
            page.locator('#apply-activation').click(); page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#activation-state')).to_have_attribute('data-state', 'DONE', timeout=180000)
            page.locator('#check-availability').click(); expect(page.locator('#activation-availability')).to_contain_text('Page de connexion locale disponible')
            expect(page.locator('[id="rollback-web.storage-upgrade"]')).to_have_count(0)
            page.screenshot(path='/evidence/upgrade-wizard.png', full_page=True)
        self.activated(self.build_service(), parent)

    def test_upgrade_reply_lost_restores_without_credentials_or_replaying_sql(self):
        parent = self.planned(self.hook('web.storage-upgrade'))
        self.kill_child(lambda: self.service.execute('apply', confirm(parent)))
        other = self.build_service(); before = self.operator_journal.path.read_bytes()
        with patch.object(up.ManagedProfile, 'inspect', side_effect=AssertionError('GET host')): other.wizard_state(); other.report()
        self.assertEqual(before, self.operator_journal.path.read_bytes())
        result = other.execute('resume', confirm(parent))['installation']; self.done(other, result)
        self.assertEqual([step['attempts'] for step in result['steps'][:2]], [1, 1])

    def test_upgrade_cutover_sigkill_refresh_and_explicit_secret_renewal(self):
        parent = self.planned(); move = up.u._move
        def interrupted(source, target):
            move(source, target)
            if source == self.webroot: os.kill(os.getpid(), signal.SIGKILL)
        def child():
            with patch.object(up.u, '_move', side_effect=interrupted): self.service.execute('apply', confirm(parent))
        self.kill_child(child); self.assertFalse(self.webroot.exists())
        other = self.build_service(); before = self.operator_journal.path.read_bytes()
        self.assertEqual(len(other.wizard_state()['upgrade']['missing_credentials']), 3)
        self.assertEqual(other.report()['installation']['state'], 'RUNNING'); self.assertEqual(before, self.operator_journal.path.read_bytes())
        self.credentials(other, parent['plan_sha256'])
        result = other.execute('resume', confirm(parent))['installation']; self.done(other, result)

    def test_upgrade_browser_rollback_preserves_sql_and_stays_gated(self):
        from playwright.sync_api import expect
        parent = self.planned(); result = self.service.execute('apply', confirm(parent))['installation']; self.done(self.service, result)
        with self.browser() as page:
            page.locator('summary').filter(has_text='Ressaisir les identifiants applicatifs').click()
            for name, value in {'database_password': self.payload['secrets']['database_password'], 'authority_user': self.authority._user,
                                'authority_password': self.authority._password}.items(): page.locator('#renew-' + name).fill(value)
            page.locator('#renew-web-credentials').click(); expect(page.locator('#wizard-message')).to_contain_text('Identifiants mis à jour')
            page.locator('[id="rollback-web.storage-upgrade"]').click(); page.locator('#operation-dialog button[value="confirm"]').click()
            expect(page.locator('#execution-state')).to_have_attribute('data-state', 'ROLLED_BACK', timeout=180000)
        self.assertEqual(self.before_sql, self.sql(query=f'SELECT id_user,email,password_hash,photo_profil FROM `{self.db}`.UserInfo'))
        self.http_runtime.observe(); self.collector.observe()
        self.assertEqual((self.webroot / self.legacy_relative).read_bytes(), journal.previous.base.business.png())

    def test_upgrade_authorization_reply_lost_keeps_parent_binding_and_refuses_rollback(self):
        parent = self.planned(); result = self.service.execute('apply', confirm(parent))['installation']; self.done(self.service, result)
        document = self.service.execute('activation.plan', {'preparation_sha256': parent['plan_sha256']})['activation']['installation']
        engine, _ = self.service.activation.engine(result); engine._fault_hook = self.hook('web.storage-resume')
        self.kill_child(lambda: engine.apply(document['plan_sha256']))
        other = self.build_service(); actual = other.execute('activation.resume', confirm(document))['activation']['installation']
        self.assertEqual(actual['state'], 'DONE', actual['last_error_redacted'])
        with self.assertRaises(up.InstallerError): other.execute('rollback', confirm(parent, boundary='web.storage-upgrade'))
        self.activated(other, result)

    def test_upgrade_registration_refuses_a_drifted_native_runtime(self):
        self.registered(); before = self.service.upgrade.read()
        file = self.http_root / 'staged.json'; raw = file.read_bytes()
        try:
            file.write_bytes(raw + b' ')
            with self.assertRaises(Exception): self.service.upgrade.register(self.descriptor_path)
            self.assertEqual(before, self.service.upgrade.read()); self.assertIsNone(self.service.engine.report())
            self.assertFalse(self.backups.exists())
        finally: file.write_bytes(raw)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--shard-index', type=int, choices=(0, 1), required=True); args = parser.parse_args()
    if os.environ.get('HESTIA_UPGRADE_WIZARD_TEST') != '1': raise RuntimeError('Explicit disposable upgrade wizard opt-in required')
    journal.fresh.WEB = args.web; journal.previous.base.previous.WEB = args.web; journal.previous.base.TARGET = args.target
    for commit, root in ((up.u.LEGACY_COMMIT, args.web), (up.u.STORAGE_COMMIT, args.target)):
        ARCHIVES[commit] = tar_bytes('web', commit, [(p.relative_to(root).as_posix(), p.read_bytes(), p.stat().st_mode & 0o777)
            for p in sorted(root.rglob('*')) if p.is_file()])
    source = quality.snapshot(ROOT)
    names = sorted(name for name in UpgradeWizardLive.__dict__ if name.startswith('test_upgrade_'))[args.shard_index::2]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(UpgradeWizardLive(name) for name in names))
    stable = source == quality.snapshot(ROOT)
    report = {'suite': 'Managed upgrade through real wizard and product activation', 'tests': result.testsRun, 'expected': 3,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped), 'shard': args.shard_index,
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 3 and not result.skipped and stable else 'FAIL',
        'source_stable': stable, 'source_files': len(source), 'upgrade_wizard_delivered': True,
        'service_activation_delivered': True, 'phase5_complete': False, 'application_installed': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'UPGRADE-WIZARD-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    args.report.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
