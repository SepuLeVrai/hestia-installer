"""File-only SQL facade contracts; host, accounts, SQL and services are mocked."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from installer import mariadb_plan as p
from installer.model import InstallerError, canonical_bytes, build_plan, initial_document
import test_package_plan as fixture
from github_fixture import confirm


class MariaDBPlanTests(unittest.TestCase):
    setUp = fixture.PackagePlanTests.setUp
    restart = fixture.PackagePlanTests.restart
    acquisition = fixture.PackagePlanTests.acquisition
    acquired = fixture.PackagePlanTests.acquired
    installation = fixture.PackagePlanTests.installation

    def installed(self):
        document = self.installation()
        with patch.object(p.native.packages.SystemPackages, 'observe', return_value=self.ready), \
             patch.object(p.native.packages.SystemPackages, 'install', return_value=self.ready), \
             patch.object(p.native.packages.SystemPackages, 'observe_installed', return_value=self.ready):
            return self.service.execute('packages.install.apply', confirm(document))['packages']['installation']

    def sql_plan(self):
        package = self.installed()
        with patch.object(p.native.packages.SystemPackages, 'observe_installed'), \
             patch.object(p.native.MariaDB, 'absent'), patch.object(p.native.ServiceIdentity, 'prepare'):
            return self.service.execute('mariadb.plan', {'packages_sha256': package['plan_sha256']})['mariadb']['installation']

    def test_get_restart_and_report_are_file_only(self):
        with patch.object(p.native, 'run', side_effect=AssertionError('host')):
            self.assertIsNone(self.service.wizard_state()['mariadb']['profile'])
            self.assertNotIn('mariadb', self.restart().report())
        self.assertFalse(self.path.parent.exists())

    def test_sql_requires_installed_packages_and_exact_parent_confirmation(self):
        with self.assertRaises(InstallerError): self.service.execute('mariadb.plan', {'packages_sha256': 'a' * 64})
        package = self.installed()
        with patch.object(p.native.MariaDB, 'absent', side_effect=AssertionError('host')):
            with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
                self.service.execute('mariadb.plan', {'packages_sha256': 'b' * 64})
        self.assertIsNone(self.service.mariadb.profile())
        self.assertEqual(package['state'], 'DONE')

    def test_plan_closed_immutable_restart_no_live_probe(self):
        document = self.sql_plan(); before = self.service.mariadb.journal.path.read_bytes()
        with patch.object(p.native.MariaDB, 'absent', side_effect=AssertionError('host')), \
             patch.object(p.native.packages.SystemPackages, 'observe_installed', side_effect=AssertionError('host')):
            service = self.restart()
            result = service.execute('mariadb.plan', {'packages_sha256': service.mariadb.profile()['packages_sha256']})['mariadb']
            self.assertEqual(result['installation'], document)
            self.assertEqual(service.mariadb.journal.path.read_bytes(), before)
        self.assertEqual(len(document['plan']['steps']), 4)
        self.assertIsNone(document['approved_plan_sha256'])
        self.assertIsNone(self.service.engine.report())

    def test_credentials_are_ephemeral_bound_and_control_characters_rejected(self):
        document = self.sql_plan(); password = 'sql-authority-fixture-password'
        for value in ('short', 'x' * 20 + '\n', 'x' * 1025):
            with self.assertRaisesRegex(InstallerError, 'SECRET_REJECTED'):
                self.service.execute('mariadb.credentials', {'confirmation': document['plan_sha256'], 'authority_password': value})
        with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
            self.service.execute('mariadb.credentials', {'confirmation': 'c' * 64, 'authority_password': password})
        state = self.service.execute('mariadb.credentials', {'confirmation': document['plan_sha256'], 'authority_password': password})['mariadb']
        self.assertEqual(state['missing_credentials'], [])
        self.assertNotIn(password, json.dumps(self.service.wizard_state()))
        for file in self.path.parent.rglob('*'):
            if file.is_file(): self.assertNotIn(password.encode(), file.read_bytes())
        self.assertEqual(self.restart().mariadb.state()['missing_credentials'], ['authority_password'])
        self.service.clear_credentials()
        self.assertEqual(self.service.mariadb.state()['missing_credentials'], ['authority_password'])

    def test_altered_profile_and_partial_registry_cannot_be_resumed(self):
        self.sql_plan(); plan = self.service.mariadb
        steps = plan.engine().registry.specs()
        fake = initial_document(build_plan([replace(steps[0], warnings=())], mode='fresh'))
        plan.journal.path.write_bytes(canonical_bytes(fake))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): plan.engine()

    def test_main_plan_and_shared_lock_block_sql_mutations(self):
        document = self.sql_plan()
        with self.service.engine.journal.locked():
            with self.assertRaisesRegex(InstallerError, 'BUSY'):
                self.restart().execute('mariadb.apply', confirm(document))
        self.service.engine.plan()
        with self.assertRaisesRegex(InstallerError, 'PLAN_EXISTS'):
            self.service.execute('mariadb.apply', confirm(document))

    def test_invalid_confirmation_never_probes_host(self):
        self.sql_plan()
        with patch.object(p.native.packages.SystemPackages, 'observe_installed', side_effect=AssertionError('host')):
            with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
                self.service.execute('mariadb.apply', {'confirm': True, 'confirmation': 'd' * 64})

    def test_new_server_rejects_existing_database_mode_and_changed_sql_names(self):
        self.sql_plan(); plan = self.service.mariadb
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'):
            self.service.execute('web.setup', {'configuration': {'database': {'mode': 'existing_local'}}})
        for name in ('authority_user', 'migration_user'):
            with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): plan.bind_credentials({name: 'foreign'})
        plan.bind_credentials({'authority_user': plan.runtime().authority_user, 'migration_user': plan.runtime().migration_user})
        with self.assertRaisesRegex(InstallerError, 'DEPENDENCY_BLOCKED'): plan.assert_ready()

    def test_private_profile_symlink_and_truncated_bytes_are_rejected(self):
        self.sql_plan(); path = self.service.mariadb.root / 'profile.json'; original = path.read_bytes()
        outside = path.with_name('outside'); outside.write_bytes(original); outside.chmod(0o600)
        path.unlink(); path.symlink_to(outside)
        with self.assertRaises(Exception): self.service.mariadb.profile()
        path.unlink(); path.write_bytes(original[:12]); path.chmod(0o600)
        with self.assertRaises(Exception): self.service.mariadb.profile()
        self.assertEqual(outside.read_bytes(), original)

    def test_lost_sql_reply_recovery_is_observation_only_and_incomplete_is_manual(self):
        from installer.operations import OperationContext, RecoveryDecision, SecretVault
        runtime = p.native.MariaDB('1' * 32, 'a' * 64)
        operation = p.native.Authority(runtime)
        context = OperationContext('test', operation.spec.as_dict(), {}, SecretVault())
        with patch.object(operation, 'observe'), patch.object(operation, 'apply', side_effect=AssertionError('SQL replay')):
            result = operation.recover(context, 'apply')
            self.assertEqual(result.decision, RecoveryDecision.APPLIED)
        with patch.object(operation, 'observe', side_effect=OSError('missing durable proof')):
            self.assertEqual(operation.recover(context, 'apply').decision, RecoveryDecision.MANUAL)
        self.assertEqual(operation.recover(context, 'rollback').decision, RecoveryDecision.MANUAL)

    def test_systemd_struct_arrays_require_typed_empty_values_and_stable_owner(self):
        runtime = p.native.MariaDB('1' * 32, 'a' * 64)
        def reply(argv, **kwargs):
            if 'GetNameOwner' in argv: return canonical_bytes({'type': 's', 'data': [':1.0']})
            self.assertIn('--auto-start=no', argv); self.assertIn('--allow-interactive-authorization=no', argv)
            self.assertIn('/org/freedesktop/systemd1/unit/' + runtime.unit.replace('-', '_2d').replace('.', '_2e'), argv)
            signature = 'a(sb)' if argv[-1] == 'EnvironmentFiles' else 'a(sasbttttuii)'
            return canonical_bytes({'type': 'v', 'data': [{'type': signature, 'data': []}]})
        with patch.object(p.native, 'run', side_effect=reply): runtime.empty_arrays()
        def changed(argv, **kwargs):
            value = json.loads(reply(argv, **kwargs))
            if argv[-1] == 'EnvironmentFiles': value['data'][0]['data'] = [['/foreign', False]]
            return canonical_bytes(value)
        with patch.object(p.native, 'run', side_effect=changed):
            with self.assertRaises(InstallerError): runtime.empty_arrays()

    def test_sql_probe_accepts_explicit_false_text_and_refuses_enabled_flags(self):
        runtime = p.native.MariaDB('1' * 32, 'a' * 64)
        value = {'user': 'root@localhost', 'data': str(runtime.data) + '/', 'bind': '127.0.0.1', 'port': 3306,
                 'binlog': 'OFF', 'general': '0', 'slow': 'OFF', 'local': '0'}
        with patch.object(runtime, 'sql', return_value=json.dumps(value)): runtime.probe()
        for flag in ('binlog', 'general', 'slow', 'local'):
            with patch.object(runtime, 'sql', return_value=json.dumps({**value, flag: 'ON'})):
                with self.assertRaises(InstallerError): runtime.probe()


if __name__ == '__main__': unittest.main()
