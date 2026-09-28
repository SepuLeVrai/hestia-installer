"""Package composition contract tests; all host effects are mocked, files only."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installer import package_plan as p
from installer.engine import TransactionEngine
from installer.model import InstallerError, canonical_bytes, build_plan, initial_document
from installer.operations import default_registry
from installer.service import TransactionService
from installer.transaction import StateJournal
from github_fixture import confirm


class PackagePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='hestia-package-plan-', dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'operator/state.json'
        self.service = self.restart(); self.plan = self.service.packages
        self.digest = 'a' * 64
        self.ready = {'plan_sha256': self.digest}

    def restart(self):
        service = TransactionService(TransactionEngine(StateJournal(self.path), default_registry()))
        self.addCleanup(service.close); return service

    def acquisition(self, nginx=False):
        with patch.object(p.native.SystemPackages, 'prepare', return_value=({'debian': '13'}, {})):
            return self.service.execute('packages.acquire.plan', {'nginx': nginx})['packages']['acquisition']

    def acquired(self):
        document = self.acquisition()
        with patch.object(p.native.SystemPackages, 'prepare', return_value=({'debian': '13'}, {})), \
             patch.object(p.native.SystemPackages, 'acquire', return_value=self.ready), \
             patch.object(p.native.SystemPackages, 'observe', return_value=self.ready):
            result = self.service.execute('packages.acquire.apply', confirm(document))['packages']['acquisition']
        self.assertEqual(result['state'], 'DONE'); return result

    def installation(self):
        document = self.acquired()
        with patch.object(p.native.SystemPackages, 'observe', return_value=self.ready), \
             patch.object(p.native.SystemPackages, '_manifest', return_value={'archives': {
                 'apache2': {'version': '2.4.65-1', 'architecture': 'amd64', 'bytes': 1024}}}):
            return self.service.execute('packages.install.plan', {'acquisition_sha256': document['plan_sha256']})['packages']['installation']

    def test_empty_get_and_restart_never_create_files_or_probe_host(self):
        with patch.object(p.native.SystemPackages, '_host', side_effect=AssertionError('host')):
            self.assertIsNone(self.restart().wizard_state()['packages']['profile'])
            self.assertIsNone(self.service.report()['installation'])
        self.assertFalse(self.path.parent.exists())

    def test_plan_is_immutable_and_host_read_only_without_acquisition(self):
        with patch.object(p.native.SystemPackages, 'acquire', side_effect=AssertionError('download')):
            document = self.acquisition(nginx=True)
            with patch.object(p.native.SystemPackages, 'prepare', side_effect=AssertionError('host')):
                repeated = self.restart().execute('packages.acquire.plan', {'nginx': True})['packages']['acquisition']
        self.assertEqual(document, repeated); self.assertIsNone(document['approved_plan_sha256'])
        with self.assertRaises(InstallerError): self.service.execute('packages.acquire.plan', {'nginx': False})
        self.assertIsNone(self.service.engine.report())

    def test_closed_profile_refuses_debian12_and_arbitrary_paths(self):
        for payload in ({'nginx': 1}, {'nginx': False, 'directory': '/tmp/unsafe'}):
            with self.assertRaises(InstallerError): self.service.execute('packages.acquire.plan', payload)
        with patch.object(p.native.SystemPackages, 'prepare', return_value=({'debian': '12'}, {})):
            with self.assertRaisesRegex(InstallerError, 'VALIDATION_FAILED'):
                self.service.execute('packages.acquire.plan', {'nginx': False})
        self.assertIsNone(self.plan.profile())

    def test_debian13_is_checked_again_before_apply(self):
        document = self.acquisition()
        with patch.object(p.native.SystemPackages, 'prepare', return_value=({'debian': '12'}, {})), \
             patch.object(p.native.SystemPackages, 'acquire', side_effect=AssertionError('download')):
            value = self.service.execute('packages.acquire.apply', confirm(document))['packages']['acquisition']
        self.assertEqual(value['state'], 'FAILED')

    def test_installation_requires_completed_acquisition_and_its_exact_digest(self):
        document = self.acquisition()
        with self.assertRaises(InstallerError):
            self.service.execute('packages.install.plan', {'acquisition_sha256': document['plan_sha256']})
        self.acquired()
        with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
            self.service.execute('packages.install.plan', {'acquisition_sha256': '0' * 64})
        self.assertIsNone(self.plan.selection())

    def test_versions_and_archive_digest_bound_to_separate_confirmation(self):
        document = self.installation(); selection = self.plan.selection()
        self.assertEqual(selection['archive_plan_sha256'], self.digest)
        self.assertIn(p.native.f._sha(canonical_bytes(selection)), document['plan']['steps'][0]['warnings'][-1])
        self.assertIsNone(document['approved_plan_sha256'])
        with patch.object(p.native.SystemPackages, 'install', side_effect=AssertionError('install')):
            with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
                self.service.execute('packages.install.apply', confirm(self.plan.journals['acquire'].read()))
        self.assertEqual(self.plan._read('selection.json'), selection)

    def test_repeated_install_plan_and_get_do_not_query_packages(self):
        document = self.installation(); before = self.path.parent.joinpath('packages/install/state.json').read_bytes()
        with patch.object(p.native.SystemPackages, 'observe', side_effect=AssertionError('host')), \
             patch.object(p.native.SystemPackages, '_manifest', side_effect=AssertionError('host')):
            service = self.restart()
            state = service.execute('packages.install.plan', {'acquisition_sha256': self.plan.selection()['acquisition_plan_sha256']})['packages']
            self.assertEqual(state['installation'], document)
            self.assertEqual(service.wizard_state()['packages'], state)
        self.assertEqual(before, self.plan.journals['install'].path.read_bytes())

    def test_partial_or_modified_install_plan_is_rejected_before_mutation(self):
        self.installation()
        operation = self.plan.engine('install').registry
        step = operation.specs()[0]
        from dataclasses import replace
        fake = initial_document(build_plan([replace(step, warnings=())], mode='fresh'))
        self.plan.journals['install'].path.write_bytes(canonical_bytes(fake))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.plan.engine('install')

    def test_selection_drift_cannot_change_an_existing_install_plan(self):
        self.installation(); selection = self.plan.selection(); selection['packages'][0]['version'] = '2.4.66-1'
        (self.plan.root / 'selection.json').write_bytes(canonical_bytes(selection))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.plan.engine('install')

    def test_private_profile_links_and_partial_data_are_rejected(self):
        self.acquisition(); path = self.plan.root / 'profile.json'; original = path.read_bytes()
        outside = self.plan.root / 'outside'; outside.write_bytes(original); outside.chmod(0o600)
        path.unlink(); path.symlink_to(outside)
        with self.assertRaises(Exception): self.plan.profile()
        path.unlink(); path.write_bytes(original[:10]); path.chmod(0o600)
        with self.assertRaises(Exception): self.plan.profile()
        self.assertEqual(outside.read_bytes(), original)

    def test_main_plan_and_cross_process_lock_block_package_actions(self):
        document = self.acquisition()
        with self.assertRaisesRegex(InstallerError, 'DEPENDENCY_BLOCKED'):
            self.service.execute('plan', {'modules': ['core']})
        with self.service.engine.journal.locked():
            with self.assertRaisesRegex(InstallerError, 'BUSY'):
                self.restart().execute('packages.acquire.apply', confirm(document))
        self.service.engine.plan()
        with self.assertRaisesRegex(InstallerError, 'PLAN_EXISTS'):
            self.service.execute('packages.acquire.apply', confirm(document))

    def test_success_never_claims_mariadb_or_application_ready(self):
        document = self.installation()
        with patch.object(p.native.SystemPackages, 'observe', return_value=self.ready), \
             patch.object(p.native.SystemPackages, 'install', return_value=self.ready), \
             patch.object(p.native.SystemPackages, 'observe_installed', return_value=self.ready):
            state = self.service.execute('packages.install.apply', confirm(document))['packages']
        self.assertEqual(state['installation']['state'], 'DONE')
        self.assertFalse(state['application_installed']); self.assertFalse(state['mariadb_ready'])
        self.assertIsNone(self.service.engine.report())


if __name__ == '__main__': unittest.main()
