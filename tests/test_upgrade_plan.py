"""File-only upgrade selection and journal contracts; no SQL/accounts/services."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installer import upgrade_plan as up, application_plan as app
from installer.engine import TransactionEngine
from installer.github_sources import GitHubAcquisition
from installer.model import InstallerError, canonical_bytes, Receipt, aggregate, build_plan, initial_document
from installer.operations import default_registry, OperationContext, SecretVault
from installer.service import TransactionService
from installer.transaction import StateJournal
from github_fixture import make_service, DUMMY, confirm
from test_wizard import good_checks


def profile():
    fresh = app.FreshProfile('a' * 32)
    return {'descriptor': {'version': 1, 'http': {'instance': fresh.instance, 'root': str(fresh.root / 'http'),
        'webroot': str(fresh.webroot), 'service_user': fresh.identity.user, 'hostname': 'hestia.example.test',
        'port': 19080, 'maintenance_directory': str(fresh.config_root / app.db.fs.configuration_slot({'web': {'webroot': str(fresh.webroot)}}) / 'maintenance')},
        'worker': {'user': fresh.worker.user, 'run_root': str(fresh.root / 'run'), 'state_root': str(fresh.root / 'attempts')}},
        'configuration': {'version': 1, 'mode': 'upgrade', 'web': fresh.web('hestia.example.test'),
            'database': {'mode': 'existing_local', 'host': '127.0.0.1', 'port': 3306, 'name': 'hestia_app', 'user': 'hestia_user', 'tls_ca_file': None},
            'administrator': None, 'assistant': {'action': 'preserve', 'desired_enabled': None}}}


class UpgradePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='hestia-upgrade-plan-', dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.service, self.fake = make_service(self.root); self.addCleanup(self.service.close)
        self.value = profile(); self.descriptor = self.root / 'descriptor.json'
        self.descriptor.write_bytes(canonical_bytes(self.value['descriptor'])); self.descriptor.chmod(0o600)

    def register(self):
        with patch.object(up.ManagedProfile, 'inspect', return_value=self.value['configuration']):
            return self.service.upgrade.register(self.descriptor)

    def plan(self):
        saved = self.register(); self.service.execute('github.validate', {'credential': DUMMY})
        with patch.object(up.ManagedProfile, 'inspect', return_value=self.value['configuration']), patch('installer.wizard.run_read_only_preflight', side_effect=good_checks):
            return self.service.execute('wizard.plan', {'modules': ['web'], 'refs': {}, 'mode': 'upgrade',
                'upgrade_profile_sha256': saved['profile_sha256']})['installation']

    def restart(self):
        engine = TransactionEngine(self.service.engine.journal, default_registry())
        other = TransactionService(engine, github=GitHubAcquisition(engine, restore=False)); self.addCleanup(other.close)
        return other

    def completed(self):
        document = self.plan(); document.update(state='DONE', approved_plan_sha256=document['plan_sha256'], revision=1)
        for spec, record in zip(document['plan']['steps'], document['steps']):
            record.update(state='DONE', phase='done', attempts=1, evidence=Receipt(created_resources=tuple(r['name'] for r in spec['resources'] if not r['preexisting'])).as_dict())
        document.update(aggregate(document))
        with self.service.engine.journal.locked() as locked: locked.write(document, expected_revision=0)
        return document

    def test_empty_get_never_registers_or_probes(self):
        with patch.object(up.ManagedProfile, 'inspect', side_effect=AssertionError('host')):
            self.assertIsNone(self.service.wizard_state()['upgrade']['profile'])
        self.assertFalse(self.service.engine.journal.path.parent.exists())

    def test_registration_is_private_and_immutable(self):
        value = self.register(); path = self.service.engine.journal.path.parent / up.FILENAME
        self.assertEqual(path.stat().st_mode & 0o777, 0o600); self.assertEqual(value, self.register())
        self.value['descriptor']['http']['port'] += 1; self.descriptor.write_bytes(canonical_bytes(self.value['descriptor']))
        with self.assertRaises(InstallerError): self.register()
        self.assertEqual(self.service.upgrade.state(), value)

    def test_registration_rejects_non_private_and_unsealed_descriptors(self):
        self.descriptor.chmod(0o644)
        with self.assertRaises(Exception): self.register()
        self.descriptor.chmod(0o600)
        with patch.object(up.ManagedProfile, 'inspect', side_effect=RuntimeError('unsealed')):
            with self.assertRaises(RuntimeError): self.service.upgrade.register(self.descriptor)
        self.assertIsNone(self.service.upgrade.read())

    def test_descriptor_has_no_browser_paths_or_credentials(self):
        for change in ({'password': 'private'}, {'source_commit': '0' * 40}, {'backup_root': '/tmp/private'}):
            value = deepcopy(self.value['descriptor']); value.update(change)
            with self.assertRaises(InstallerError): up.ManagedProfile(value)

    def test_plan_has_two_pinned_sources_and_existing_native_rollback(self):
        document = self.plan(); specs = document['plan']['steps']
        self.assertEqual(len(specs), 4); self.assertEqual(document['mode'], 'upgrade')
        self.assertEqual([s['source']['commit_sha'] for s in specs[:2]], [up.u.LEGACY_COMMIT, up.u.STORAGE_COMMIT])
        self.assertTrue(specs[-1]['rollback_supported']); self.assertEqual(self.fake.archive_requests, [])
        self.assertIsNone(document['approved_plan_sha256'])

    def test_restart_and_repeated_plan_are_read_only_during_cutover(self):
        document = self.plan(); before = self.service.engine.journal.path.read_bytes()
        with patch.object(up.ManagedProfile, 'inspect', side_effect=AssertionError('host')), patch.object(up.pwd, 'getpwnam', side_effect=AssertionError('account')):
            other = self.restart(); self.assertEqual(other.wizard_state()['installation'], document)
            self.assertEqual(other.upgrade.plan(up.a.digest(self.value)), document)
        self.assertEqual(self.service.engine.journal.path.read_bytes(), before)

    def test_truncated_valid_plan_cannot_be_restored(self):
        self.plan(); specs = self.service.engine.registry.specs()[-2:]
        short = initial_document(build_plan([replace(specs[0], dependencies=()), specs[1]], mode='upgrade'))
        self.service.engine.journal.path.write_bytes(canonical_bytes(short))
        with self.assertRaises(InstallerError): self.restart()

    def test_credentials_are_ephemeral_and_bound_to_profile_then_plan(self):
        state = self.register(); values = {'database_password': 'private-database-fixture', 'authority_user': 'fixture_authority', 'authority_password': 'private-authority-fixture'}
        result = self.service.execute('web.upgrade.credentials', {'confirmation': state['profile_sha256'], 'credentials': values})['upgrade']
        self.assertEqual(result['missing_credentials'], [])
        for value in values.values(): self.assertNotIn(value, canonical_bytes(result).decode())
        self.assertEqual(len(self.restart().upgrade.state()['missing_credentials']), 3)
        document = self.plan()
        with self.assertRaises(InstallerError): self.service.upgrade.renew({'confirmation': state['profile_sha256'], 'credentials': values})
        self.service.upgrade.renew({'confirmation': document['plan_sha256'], 'credentials': values})

    def test_credentials_reject_admin_reset_unknowns_and_public_collisions(self):
        state = self.register()
        for values in ({'admin_password': 'private-admin-fixture'}, {'authority_user': 'root'}, {'database_password': 'hestia.example.test'}):
            with self.assertRaises(InstallerError): self.service.upgrade.renew({'confirmation': state['profile_sha256'], 'credentials': values})

    def test_plan_refuses_wrong_digest_and_browser_overrides(self):
        self.register()
        with self.assertRaises(InstallerError): self.service.upgrade.plan('0' * 64)
        with self.assertRaises(InstallerError): self.service.execute('wizard.plan', {'modules': ['web'], 'refs': {}, 'mode': 'upgrade',
            'upgrade_profile_sha256': up.a.digest(self.value), 'backup_root': '/var/lib/other'})

    def test_factory_forwards_rollback_to_exact_native_operation(self):
        document = self.plan(); factory = self.service.engine.registry.get(document['plan']['steps'][-1])
        context = OperationContext(document['installation_id'], factory.spec.as_dict(), {}, SecretVault())
        with patch.object(up.ManagedProfile, 'runtime', return_value=up.ManagedProfile(self.value['descriptor']).runtime(planning=True)), patch.object(up.a.StorageUpgradeOperation, 'rollback', return_value=True) as native:
            self.assertTrue(factory.rollback(context)); native.assert_called_once_with(context)

    def test_separate_activation_preserves_parent_context_and_registered_port(self):
        parent = self.completed(); before = self.service.engine.journal.path.read_bytes()
        with patch.object(up.ManagedProfile, 'runtime', return_value=up.ManagedProfile(self.value['descriptor']).runtime(planning=True)), patch.object(app.h.HttpRuntime, 'observe'), patch.object(app.cleaner.SessionCleaner, 'observe'):
            document = self.service.execute('activation.plan', {'preparation_sha256': parent['plan_sha256']})['activation']['installation']
            engine, active = self.service.activation.engine(parent)
        self.assertEqual(document['mode'], 'upgrade'); self.assertEqual(len(document['plan']['steps']), 5)
        context = OperationContext(document['installation_id'], active.resume.spec.as_dict(), {}, SecretVault())
        self.assertNotEqual(parent['installation_id'], document['installation_id'])
        self.assertEqual(active.resume._upgrade_context(context).installation_id, parent['installation_id'])
        self.assertEqual(active.probe_port(), 19080); self.assertEqual(active.directory, Path(self.value['descriptor']['worker']['state_root']))
        self.assertEqual(before, self.service.engine.journal.path.read_bytes())

    def test_approved_activation_blocks_parent_rollback_before_native_dispatch(self):
        parent = self.completed()
        with patch.object(up.ManagedProfile, 'runtime', return_value=up.ManagedProfile(self.value['descriptor']).runtime(planning=True)), patch.object(app.h.HttpRuntime, 'observe'), patch.object(app.cleaner.SessionCleaner, 'observe'):
            document = self.service.execute('activation.plan', {'preparation_sha256': parent['plan_sha256']})['activation']['installation']
        document.update(approved_plan_sha256=document['plan_sha256'], revision=1)
        with self.service.activation.journal.locked() as locked: locked.write(document, expected_revision=0)
        with patch.object(self.service.engine, 'rollback', side_effect=AssertionError('native dispatch')):
            with self.assertRaisesRegex(InstallerError, 'MANUAL_ACTION_REQUIRED'): self.service.execute('rollback', confirm(parent, boundary='web.storage-upgrade'))


if __name__ == '__main__': unittest.main()
