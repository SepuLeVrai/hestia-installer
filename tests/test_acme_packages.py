"""Additive dependency contracts; only local files and mocked host effects."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from installer import acme_packages as a
from installer.model import InstallerError, Receipt, aggregate, build_plan, canonical_bytes, initial_document
from installer.operations import RecoveryDecision, OperationContext, SecretVault
from github_fixture import confirm
import test_package_plan as fixture
import test_boot_plan as boot_fixture
from installer.frozen_boot import reference


class AcmePlanTests(unittest.TestCase):
    def setUp(self):
        case = fixture.PackagePlanTests(); case.setUp(); self.addCleanup(case.doCleanups)
        self.fixture, self.service = case, case.service
        document = case.installation()
        with patch.object(a.n.SystemPackages, 'observe', return_value=case.ready), \
             patch.object(a.n.SystemPackages, 'install', return_value=case.ready), \
             patch.object(a.n.SystemPackages, 'observe_installed', return_value=case.ready):
            self.base = self.service.execute('packages.install.apply', confirm(document))['packages']['installation']
        self.plan = self.service.acme_packages
        self.ready = {'plan_sha256': 'd' * 64}

    def planned(self):
        with patch.object(a.AcmePackages, 'prepare', return_value=({'debian': '13'}, {})):
            return self.service.execute('acme-packages.acquire.plan', {'packages_sha256': self.base['plan_sha256']})['acme_packages']['acquisition']

    def acquired(self):
        document = self.planned()
        with patch.object(a.AcmePackages, 'prepare'), patch.object(a.AcmePackages, 'acquire', return_value=self.ready), \
             patch.object(a.AcmePackages, 'observe', return_value=self.ready):
            result = self.service.execute('acme-packages.acquire.apply', confirm(document))['acme_packages']['acquisition']
        self.assertEqual(result['state'], 'DONE'); return result

    def installation(self):
        acquired = self.acquired()
        with patch.object(a, 'reference'), patch.object(a.AcmePackages, 'observe', return_value=self.ready), \
             patch.object(a.AcmePackages, '_manifest', return_value={'archives': {'certbot': {
                 'version': '4.0.0-1', 'architecture': 'all', 'bytes': 1234}}}):
            return self.service.execute('acme-packages.install.plan', {'acquisition_sha256': acquired['plan_sha256']})['acme_packages']['installation']

    def test_readers_are_file_only_and_never_claim_https(self):
        with patch.object(a.AcmePackages, '_host', side_effect=AssertionError('host')):
            value = self.fixture.restart().wizard_state()['acme_packages']
            self.assertIsNone(value['profile'])
            self.assertNotIn('acme_packages', self.service.report())
        self.assertFalse(value['public_tls_configured']); self.assertFalse(value['renewal_configured'])
        self.assertFalse(value['phase5_complete']); self.assertFalse(self.plan.root.exists())

    def test_separate_plan_preserves_completed_parent_and_rejects_wrong_consent(self):
        before = self.service.packages.journals['install'].path.read_bytes()
        document = self.planned()
        self.assertNotEqual(document['plan_sha256'], self.base['plan_sha256'])
        self.assertEqual(self.service.packages.journals['install'].path.read_bytes(), before)
        with patch.object(a.AcmePackages, 'acquire', side_effect=AssertionError('effect')):
            with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
                self.service.execute('acme-packages.acquire.apply', confirm(self.base))
        self.assertIsNone(self.plan.journals['acquire'].read()['approved_plan_sha256'])

    def test_restart_and_repeated_acquisition_do_not_probe_host(self):
        document = self.planned()
        with patch.object(a.AcmePackages, 'prepare', side_effect=AssertionError('host')):
            result = self.fixture.restart().execute('acme-packages.acquire.plan', {'packages_sha256': self.base['plan_sha256']})
        self.assertEqual(document, result['acme_packages']['acquisition'])

    def test_installation_requires_a_verified_completed_boot(self):
        acquired = self.acquired()
        with patch.object(a.AcmePackages, 'install', side_effect=AssertionError('effect')):
            with self.assertRaisesRegex(InstallerError, 'DEPENDENCY_BLOCKED'):
                self.service.execute('acme-packages.install.plan', {'acquisition_sha256': acquired['plan_sha256']})
        self.assertIsNone(self.plan.journals['install'].read())

    def test_install_confirmation_precedes_live_boot_observation(self):
        document = self.installation()
        def guard(*args, **kwargs):
            if kwargs.get('observe'): raise AssertionError('live observation')
        with patch.object(a, 'reference', side_effect=guard):
            with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
                self.service.execute('acme-packages.install.apply', confirm(self.base))
        self.assertIsNone(self.plan.journals['install'].read()['approved_plan_sha256'])
        self.assertNotEqual(document['plan_sha256'], self.base['plan_sha256'])

    def test_exact_versions_are_frozen_before_install(self):
        document = self.installation(); selection = self.plan.selection()
        self.assertEqual(selection['archive_plan_sha256'], 'd' * 64)
        self.assertEqual(selection['packages'][0]['version'], '4.0.0-1')
        with patch.object(a, 'reference'), patch.object(a.AcmePackages, 'observe', side_effect=AssertionError('host')):
            state = self.fixture.restart().execute('acme-packages.install.plan', {'acquisition_sha256': selection['acquisition_plan_sha256']})
        self.assertEqual(document, state['acme_packages']['installation'])

    def test_selection_and_registry_drift_are_rejected(self):
        self.installation(); selection = self.plan.selection()
        selection['packages'][0]['version'] = '99.0'
        (self.plan.root / 'selection.json').write_bytes(canonical_bytes(selection))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.plan.engine('install')
        engine = self.plan.engine('acquire'); step = engine.registry.specs()[0]
        self.plan.journals['acquire'].path.write_bytes(canonical_bytes(initial_document(build_plan([replace(step, warnings=())], mode='fresh'))))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.plan.engine('acquire')

    def test_parent_profile_drift_and_arbitrary_payload_are_rejected(self):
        self.planned()
        with self.assertRaises(InstallerError):
            self.service.execute('acme-packages.acquire.plan', {'packages_sha256': self.base['plan_sha256'], 'server': 'https://evil.invalid'})
        value = self.plan.profile(); value['base']['instance'] = 'f' * 32
        (self.plan.root / 'profile.json').write_bytes(canonical_bytes(value))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.plan.engine('acquire')

    def test_main_lock_serializes_both_package_profiles(self):
        document = self.planned()
        with self.service.engine.journal.locked():
            with self.assertRaisesRegex(InstallerError, 'BUSY'):
                self.fixture.restart().execute('acme-packages.acquire.apply', confirm(document))

    def test_done_is_historical_and_does_not_claim_certificate_or_renewal(self):
        document = self.installation()
        with patch.object(a, 'reference'), patch.object(a.AcmePackages, 'observe', return_value=self.ready), \
             patch.object(a.AcmePackages, 'install', return_value=self.ready), \
             patch.object(a.AcmePackages, 'observe_installed', return_value=self.ready):
            state = self.service.execute('acme-packages.install.apply', confirm(document))['acme_packages']
        self.assertEqual(state['installation']['state'], 'DONE')
        self.assertFalse(state['public_tls_configured']); self.assertFalse(state['renewal_configured'])
        self.assertFalse(state['phase5_complete'])


class NativeAcmeTests(unittest.TestCase):
    def runtime(self, nginx=True):
        return a.AcmePackages({'version': 1, 'instance': 'a' * 32, 'nginx': nginx}, 'b' * 64)

    def test_nginx_is_added_only_when_absent_from_parent_profile(self):
        for included in (True, False):
            r = self.runtime(included)
            self.assertEqual(r._packages({}), ('certbot',) if included else ('certbot', 'nginx'))
            self.assertEqual(r._units({}), ('certbot.service', 'certbot.timer') + (() if included else ('nginx.service',)))
            self.assertNotIn('mariadb.service', r._units({}))
            self.assertNotEqual(r.instance, r.base.instance)

    def test_additive_profile_requires_unchanged_parent_snapshot(self):
        r = self.runtime()
        with patch.object(r.base, 'observe_installed', return_value={'plan_sha256': 'b' * 64}), \
             patch.object(r, '_host', return_value={'base_installed_sha256': '0' * 64}), \
             patch.object(r, '_installed', return_value={'certbot': {'version': '4', 'architecture': 'all'}}):
            with self.assertRaises(a.n.SystemPackagesError): r.prepare()

    def test_old_package_versions_and_exact_additions_are_required(self):
        r = self.runtime(); before = {'apache2': {'version': '2.4', 'architecture': 'amd64'}}
        added = {'certbot': {'version': '4', 'architecture': 'all'}}
        manifest = {'before': before, 'archives': added, 'requested': ['certbot']}
        with patch.object(r, '_installed', return_value={**before, **added}): r._verify_installed(manifest)
        for after in ({**before, **added, 'other': {'version': '1', 'architecture': 'all'}},
                      {**added, 'apache2': {'version': '2.5', 'architecture': 'amd64'}}, before):
            with patch.object(r, '_installed', return_value=after):
                with self.assertRaises(a.n.SystemPackagesError): r._verify_installed(manifest)

    def test_recovery_observes_completed_installation_without_replay(self):
        r = self.runtime(); op = a.Installation(r, 'c' * 64)
        context = OperationContext('acme-test', op.spec.as_dict(), Receipt().as_dict(), SecretVault())
        with patch.object(r, 'observe_installed', return_value={'plan_sha256': 'c' * 64}), \
             patch.object(r, 'install', side_effect=AssertionError('replay')):
            self.assertEqual(op.recover(context, 'apply').decision, RecoveryDecision.APPLIED)
        with patch.object(r, 'observe_installed', side_effect=OSError('partial')):
            self.assertEqual(op.recover(context, 'apply').decision, RecoveryDecision.MANUAL)


class FrozenBootTests(unittest.TestCase):
    def setUp(self):
        self.case = boot_fixture.BootPlanTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)

    def test_new_source_set_does_not_adopt_or_rewrite_old_boot_registry(self):
        parent, _, document = self.case.boot_plan(); boot = self.case.service.boot
        document.update(state='DONE', approved_plan_sha256=document['plan_sha256'], revision=1)
        for spec, row in zip(document['plan']['steps'], document['steps']):
            row.update(state='DONE', phase='done', attempts=1,
                       evidence=Receipt(created_resources=tuple(r['name'] for r in spec['resources'])).as_dict())
        document.update(aggregate(document))
        with boot.journal.locked() as locked: locked.write(document, expected_revision=0)
        before = boot.journal.path.read_bytes()
        with patch.object(boot_fixture.b, 'code_files', return_value={'installer/new.py': b'new'}):
            with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): boot.engine(parent)
            loaded, _ = reference(boot, parent)
            self.assertEqual(loaded, document)
        self.assertEqual(boot.journal.path.read_bytes(), before)
        with patch.object(boot_fixture.b.BootRuntime, 'configuration', side_effect=OSError('drift')):
            with self.assertRaises(OSError): reference(boot, parent, observe=True)

    def test_frozen_reference_rejects_modified_parent_binding(self):
        parent, _, _ = self.case.boot_plan(); boot = self.case.service.boot
        profile = boot._read('profile.json'); profile['parents']['sql'] = '0' * 64
        (boot.root / 'profile.json').write_bytes(canonical_bytes(profile))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): reference(boot, parent)


if __name__ == '__main__': unittest.main()
