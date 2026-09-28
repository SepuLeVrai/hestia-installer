"""Real private plans/files/HTTPS facade; no local users, SQL or services."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installer import application_plan as app
from installer.engine import TransactionEngine
from installer.github_sources import GitHubAcquisition
from installer.model import ErrorCode, InstallerError, initial_document, build_plan
from installer.operations import default_registry
from installer.service import TransactionService
from installer.transaction import StateJournal
from github_fixture import DUMMY, make_service, plan_sources
from test_wizard import good_checks


def setup_payload(mode='managed'):
    return {'revision': 0, 'configuration': {'hostname': 'hestia.example.test',
        'database': {'mode': mode, 'name': 'hestia_app', 'user': 'hestia_user'},
        'administrator': {'first_name': 'Élise <&>漢字🙂', 'last_name': "D'Exemple", 'email': 'admin@example.test'},
        'assistant': {'action': 'disabled'}}, 'credentials': {
        'database_password': 'database-fixture-private-012345', 'admin_password': 'admin-fixture-private-012345',
        'migration_user': 'sql_setup_fixture', 'migration_password': 'migration-fixture-private-012345',
        'authority_user': 'sql_authority_fixture', 'authority_password': 'authority-fixture-private-012345'}}


class ApplicationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='hestia-plan-', dir='/var/lib')
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.service, self.fake = make_service(self.root); self.addCleanup(self.service.close)
        self.app = self.service.application
        self.file = self.service.engine.journal.path.parent / app.FILENAME

    def save(self, payload=None):
        return self.app.save(setup_payload() if payload is None else payload)

    def plan(self):
        value = self.save(); self.service.execute('github.validate', {'credential': DUMMY})
        with patch.object(app.HostPrerequisites, 'check'), patch('installer.wizard.run_read_only_preflight', side_effect=good_checks):
            return self.service.execute('wizard.plan', {'modules': ['web'], 'refs': {}, 'mode': 'fresh',
                                                        'application_revision': value['revision']})['installation']

    def restart(self):
        engine = TransactionEngine(StateJournal(self.service.engine.journal.path), default_registry())
        github = GitHubAcquisition(engine, restore=False)
        application = app.ApplicationPlan(engine, github)
        if not application.restore(): github.restore_registry()
        service = TransactionService(engine, github=github); self.addCleanup(service.close)
        return service

    def test_empty_get_never_creates_a_file_or_instance(self):
        before = list(self.root.iterdir())
        self.assertIsNone(self.service.wizard_state()['application']['draft'])
        self.assertEqual(before, list(self.root.iterdir()))

    def test_saved_choices_survive_restart_with_fixed_paths_and_no_credentials(self):
        saved = self.save(); profile = app.FreshProfile(saved['instance'])
        self.assertEqual(saved['configuration']['web']['webroot'], str(profile.webroot))
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        raw = self.file.read_text()
        for secret in setup_payload()['credentials'].values(): self.assertNotIn(secret, raw)
        other = self.restart(); self.assertEqual(other.application.read(), saved)
        with self.assertRaisesRegex(InstallerError, 'SECRET_REQUIRED'):
            other.engine.secrets.require('web.database_password')
        self.assertFalse(profile.root.exists()); self.assertFalse(profile.webroot.exists())

    def test_revision_conflict_cannot_change_choices_or_vault(self):
        original = self.save(); payload = setup_payload(); payload['credentials']['database_password'] = 'replacement-database-fixture'
        with self.assertRaisesRegex(InstallerError, 'BUSY'): self.save(payload)
        self.assertEqual(original, self.app.read())
        self.assertEqual(self.service.engine.secrets.require('web.database_password'), setup_payload()['credentials']['database_password'])

    def test_save_invalid_paths_modes_fields_and_credentials_does_not_persist(self):
        changes = [({'webroot': '/srv/injected'}, None), ({'service_user': 'root'}, None),
                   ({'command': 'anything'}, None), ({'hostname': '<script>'}, None),
                   ({'database': {'mode': 'remote', 'name': 'foo', 'user': 'bar'}}, None),
                   ({}, {'admin_password': 'short'}), ({}, {'migration_user': 'root'}),
                   ({}, {'authority_password': 'line\nbreak'}), ({}, {'token': 'unknown-fixture'})]
        for choices, credentials in changes:
            value = setup_payload(); value['configuration'].update(choices)
            if credentials: value['credentials'].update(credentials)
            with self.subTest(choices=choices, credentials=list(credentials or {})):
                with self.assertRaises(InstallerError): self.save(value)
                self.assertFalse(self.file.exists())

    def test_known_new_secret_in_non_secret_fields_is_rejected(self):
        value = setup_payload(); value['configuration']['administrator']['first_name'] = value['credentials']['admin_password']
        with self.assertRaisesRegex(InstallerError, 'SECRET_REJECTED'): self.save(value)
        self.assertFalse(self.file.exists())

    def test_new_secret_in_existing_wizard_draft_is_rejected(self):
        draft = self.service.wizard.default(); draft['refs'] = {'web': 'private-secret-collision'}
        self.service.wizard.save(draft)
        value = setup_payload(); value['credentials']['database_password'] = 'private-secret-collision'
        with self.assertRaisesRegex(InstallerError, 'SECRET_REJECTED'): self.save(value)
        self.assertFalse(self.file.exists())

    def test_assistant_blank_key_normalizes_disabled(self):
        value = setup_payload(); value['configuration']['assistant']['action'] = 'configure'
        saved = self.save(value)
        self.assertEqual(saved['configuration']['assistant'], {'action': 'disabled', 'desired_enabled': False})

    def test_assistant_key_is_ephemeral_and_disabling_clears_it(self):
        value = setup_payload(); value['configuration']['assistant']['action'] = 'configure'
        value['credentials']['openai_api_key'] = 'fixture-openai-0123456789012345'
        first = self.save(value)
        self.assertNotIn(value['credentials']['openai_api_key'], self.file.read_text())
        disabled = setup_payload(); disabled['revision'] = first['revision']; self.save(disabled)
        with self.assertRaises(InstallerError): self.service.engine.secrets.require('web.openai_api_key')

    def test_configuration_file_permissions_links_and_duplicates_are_refused(self):
        self.save(); original = self.file.read_bytes(); outside = self.root / 'outside'
        outside.write_bytes(original); outside.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'permissions', 'duplicate', 'oversize'):
            self.file.unlink()
            if kind == 'symlink': self.file.symlink_to(outside)
            elif kind == 'hardlink': os.link(outside, self.file)
            else:
                self.file.write_bytes(b'{"version":1,"version":1}' if kind == 'duplicate' else b' ' * (app.LIMIT + 1) if kind == 'oversize' else original)
                self.file.chmod(0o644 if kind == 'permissions' else 0o600)
            with self.subTest(kind=kind), self.assertRaises((InstallerError, OSError, ValueError)): self.app.read()
            self.assertEqual(outside.read_bytes(), original)

    def test_plan_has_complete_stable_dependency_chain_before_accounts_exist(self):
        document = self.plan(); specs = document['plan']['steps']
        self.assertEqual(len(specs), 10)
        for before, after in zip(specs, specs[1:]): self.assertEqual(after['dependencies'], [before['name']])
        saved = self.app.read(); profile = app.FreshProfile(saved['instance'])
        self.assertIn(profile.instance, json.dumps(specs))
        self.assertEqual(specs[1]['source']['commit_sha'], app.STORAGE_COMMIT)
        self.assertFalse(profile.root.exists()); self.assertFalse(profile.webroot.exists())
        restarted = self.restart()
        self.assertEqual(restarted.engine.report(), document)
        self.assertEqual([s.as_dict() for s in restarted.engine.registry.specs()], specs)
        self.assertIn('database_password', restarted.application.state()['missing_credentials'])
        self.assertFalse(restarted.application.state()['application_installed'])

    def test_repeated_plan_does_not_resolve_github_or_check_mutating_host(self):
        document = self.plan(); self.service.github.access.clear()
        with patch.object(app.HostPrerequisites, 'check', side_effect=AssertionError('unexpected host check')):
            self.assertEqual(self.app.plan(self.app.read()['revision']), document)

    def test_saved_configuration_cannot_change_after_plan(self):
        self.plan(); value = setup_payload(); value['revision'] = self.app.read()['revision']
        with self.assertRaisesRegex(InstallerError, 'PLAN_EXISTS'): self.save(value)

    def test_read_refresh_does_not_apply_resume_or_lookup_created_accounts(self):
        self.plan(); before = self.file.read_bytes(); journal = self.service.engine.journal.path.read_bytes()
        with patch.object(app.pwd, 'getpwnam', side_effect=AssertionError('account lookup on refresh')):
            for _ in range(2): self.restart().wizard_state(); self.service.report()
        self.assertEqual(self.file.read_bytes(), before); self.assertEqual(self.service.engine.journal.path.read_bytes(), journal)

    def test_restore_rejects_changed_configuration_even_with_valid_json(self):
        self.plan(); value = json.loads(self.file.read_bytes())
        value['configuration']['web']['hostname'] = 'changed.example.test'
        self.file.write_bytes(app.canonical_bytes(value))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.restart()

    def test_restore_requires_the_whole_composed_plan_not_a_subset(self):
        self.plan(); registry = self.service.engine.registry
        document = initial_document(build_plan(registry.specs()[:1], mode='fresh'))
        self.service.engine.journal.path.write_bytes(app.canonical_bytes(document) + b'\n')
        with self.assertRaises(InstallerError): self.restart()

    def test_renew_requires_exact_plan_and_never_replays(self):
        document = self.plan(); other = self.restart(); before = other.engine.report()
        payload = {'confirmation': document['plan_sha256'], 'credentials': {'database_password': 'renewed-database-secret-fixture'}}
        state = other.execute('web.credentials', payload)['application']
        self.assertNotIn('database_password', state['missing_credentials'])
        self.assertEqual(other.engine.report(), before)
        self.assertEqual(other.engine.secrets.require('web.database_password'), payload['credentials']['database_password'])
        self.assertNotIn(payload['credentials']['database_password'], json.dumps(other.wizard_state()))

    def test_renew_wrong_confirmation_unknown_secret_and_echoed_secret_are_refused(self):
        document = self.plan(); other = self.restart()
        for confirmation, values in [('0' * 64, {'database_password': 'replacement-private-fixture'}),
                                    (document['plan_sha256'], {'credential': 'replacement-private-fixture'}),
                                    (document['plan_sha256'], {'database_password': 'hestia_app'})]:
            with self.subTest(values=list(values)), self.assertRaises(InstallerError):
                other.execute('web.credentials', {'confirmation': confirmation, 'credentials': values})
        with self.assertRaises(InstallerError): other.engine.secrets.require('web.database_password')

    def test_logout_clears_application_credentials_too(self):
        self.save(); self.service.clear_credentials()
        for name in app.CREDENTIALS:
            with self.assertRaises(InstallerError): self.service.engine.secrets.require('web.' + name)
        self.assertIsNotNone(self.app.read())

    def test_reset_unapproved_preserves_choices_but_keeps_same_instance(self):
        document = self.plan(); saved = self.app.read()
        self.service.execute('wizard.reset-plan', {'confirm': True, 'confirmation': document['plan_sha256']})
        value = setup_payload(); value['revision'] = saved['revision']
        value['configuration']['hostname'] = 'second.example.test'
        self.assertEqual(self.save(value)['instance'], saved['instance'])

    def test_existing_source_plan_is_unchanged_and_not_adopted(self):
        document = plan_sources(self.service)
        self.assertFalse(self.app.owns(document)); self.assertFalse(self.app.restore())
        self.assertEqual(self.restart().engine.report(), document)
        with self.assertRaisesRegex(InstallerError, 'PLAN_EXISTS'): self.save()

    def test_application_profile_rejects_upgrade_extra_modules_and_custom_refs(self):
        saved = self.save(); self.service.execute('github.validate', {'credential': DUMMY})
        for delta in ({'mode': 'upgrade'}, {'modules': ['web', 'apk']}, {'refs': {'web': 'main'}}):
            payload = {'modules': ['web'], 'refs': {}, 'mode': 'fresh', 'application_revision': saved['revision'], **delta}
            with patch('installer.wizard.run_read_only_preflight', side_effect=good_checks), self.assertRaises(InstallerError):
                self.service.execute('wizard.plan', payload)
        self.assertIsNone(self.service.engine.report())

    def test_unprepared_host_cannot_create_an_application_plan(self):
        saved = self.save()
        with patch.object(app.HostPrerequisites, 'check', side_effect=InstallerError(ErrorCode.VALIDATION_FAILED)):
            with self.assertRaisesRegex(InstallerError, 'VALIDATION_FAILED'): self.app.plan(saved['revision'])
        self.assertIsNone(self.service.engine.report())

    def test_deferred_factory_refuses_any_execution_time_plan_change(self):
        document = self.plan(); operation = self.service.engine.registry.get(document['plan']['steps'][6])
        real = operation.factory(planning=True)
        real.spec = app.replace(real.spec, action='Changed after approval')
        operation.factory = lambda **kw: real
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): operation._operation()
