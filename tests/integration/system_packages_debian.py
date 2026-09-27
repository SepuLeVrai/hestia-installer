#!/usr/bin/env python3
"""Opt-in disposable official Debian acquisition, then disconnected installation."""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import system_packages as s
from installer import system_bus as b
from installer.operations import RecoveryDecision
from service_identity_systemd import ServiceIdentityLive
from http_runtime_systemd import command
sys.path.insert(0, str(ROOT / 'scripts'))
import quality

INSTANCE = 'a8d741051d9e4d679f53ce292990ab78'
EVIDENCE = Path('/evidence')


def profile():
    return s.SystemPackages(INSTANCE, nginx=s.h.read_os_release()['VERSION_ID'] == '13')


def snapshot_journal(packages):
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in packages.directory.iterdir() if path.is_file()}


class Acquisition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = profile(); cls.before = cls.packages._installed()
        if cls.packages.nginx:
            assert 'dbus' not in cls.before and not Path('/usr/bin/dbus-daemon').exists()
        else:
            b.ensure(confirmed=True)
            (EVIDENCE / 'preexisting-bus-identity.json').write_text(json.dumps(b._probe()))
        # An exact existing block policy is retained on Debian 12. Debian 13
        # exercises creation and removal of the provisioner's own policy.
        if cls.packages.nginx: s.POLICY.unlink()
        cls.hook_marker = Path('/var/lib/hestia-forbidden-apt-hook')
        cls.host_hook = Path('/etc/apt/apt.conf.d/99hestia-fixture')
        cls.host_hook.write_text('DPkg::Pre-Invoke { "touch ' + str(cls.hook_marker) + '"; };\n')
        cls.report = cls.packages.acquire(confirmed=True)
        (EVIDENCE / 'ready-report.json').write_text(json.dumps(cls.report, indent=2) + '\n')
        (EVIDENCE / 'acquired-plan.json').write_bytes((cls.packages.directory / 'ready.json').read_bytes())

    def test_01_signed_acquisition_preserves_installed_state_and_has_no_install_receipt(self):
        self.assertEqual(self.packages._installed(), self.before)
        self.assertEqual(self.report['state'], 'PACKAGE_ARCHIVES_READY')
        self.assertFalse(self.report['packages_installed']); self.assertFalse(self.report['application_installed'])
        self.assertFalse((self.packages.directory / 'install.attempt').exists())
        self.assertFalse(Path('/var/lib/mysql').exists()); self.assertFalse(self.hook_marker.exists())
        self.assertEqual(self.report['web_php_compatible'], self.packages.nginx)

    def test_02_ready_manifest_binds_all_archives_and_official_releases(self):
        value = self.packages._manifest()
        self.assertGreater(len(value['archives']), 10)
        self.assertEqual(len(value['archives']), len(value['proposed']))
        self.assertEqual(len([n for n in value['indices'] if n.endswith('_InRelease')]), 3)
        self.assertEqual(self.report['plan_sha256'], s.f._sha(s.p._json(value)))

    def test_03_archive_corruption_is_refused_without_redownload(self):
        value = self.packages._manifest(); item = next(iter(value['archives'].values()))
        path = self.packages.directory / 'cache/archives' / item['file']; original = path.read_bytes()
        try:
            path.write_bytes(b'corrupt')
            with patch.object(self.packages, '_apt', side_effect=AssertionError('No repair')):
                with self.assertRaises(s.SystemPackagesError): self.packages.observe()
        finally: path.write_bytes(original)
        self.assertEqual(self.packages.observe(), self.report)

    def test_04_configuration_drift_is_refused_and_not_repaired(self):
        path = self.packages.directory / 'apt.conf'; original = path.read_bytes()
        try:
            path.write_bytes(original + b'APT::Get::AllowUnauthenticated "true";\n')
            with self.assertRaises(s.SystemPackagesError): self.packages.observe()
            self.assertEqual(path.read_bytes(), original + b'APT::Get::AllowUnauthenticated "true";\n')
        finally: path.write_bytes(original)

    def test_05_acquisition_lost_reply_recovers_read_only_and_cannot_replay(self):
        before = snapshot_journal(self.packages)
        with patch.object(self.packages, '_apt', side_effect=AssertionError('No APT on observation')):
            self.assertEqual(s.PackageAcquisitionOperation(self.packages).recover(None, 'apply').decision, RecoveryDecision.APPLIED)
            with self.assertRaises(s.SystemPackagesError): self.packages.acquire(confirmed=True)
        self.assertEqual(before, snapshot_journal(self.packages))


class Installation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = profile(); cls.ready = cls.packages.observe(); cls.value = cls.packages._manifest()
        cls.policy_before = s.POLICY.read_bytes() if s.POLICY.exists() else None

    def test_01_disconnected_network_and_wrong_plan_refused_before_mutation(self):
        self.assertEqual(sorted(p.name for p in Path('/sys/class/net').iterdir()), ['lo'])
        with self.assertRaisesRegex(s.SystemPackagesError, 'PLAN_MISMATCH'):
            self.packages.install(confirmed=True, plan_sha256='0' * 64)
        self.assertFalse((self.packages.directory / 'install.attempt').exists())

    def test_02_custom_start_policy_is_preserved_and_refused(self):
        try:
            s.POLICY.write_bytes(b'#!/bin/sh\nexit 0\n'); s.POLICY.chmod(0o755)
            with self.assertRaisesRegex(s.SystemPackagesError, 'POLICY_OCCUPIED'):
                self.packages.install(confirmed=True, plan_sha256=self.ready['plan_sha256'])
            self.assertFalse((self.packages.directory / 'install.attempt').exists())
            self.assertEqual(s.POLICY.read_bytes(), b'#!/bin/sh\nexit 0\n')
        finally:
            if self.policy_before is None: s.POLICY.unlink()
            else: s.POLICY.write_bytes(self.policy_before)

    def test_03_existing_default_unit_cannot_be_adopted(self):
        path = Path('/etc/systemd/system/apache2.service'); path.symlink_to('/dev/null')
        try:
            command('systemctl', 'daemon-reload')
            with self.assertRaises(s.SystemPackagesError):
                self.packages.install(confirmed=True, plan_sha256=self.ready['plan_sha256'])
            self.assertFalse((self.packages.directory / 'install.attempt').exists())
            self.assertEqual(path.readlink(), Path('/dev/null'))
        finally: path.unlink(); command('systemctl', 'daemon-reload')

    def test_04_real_offline_install_has_exact_versions_and_blocked_default_services(self):
        report = self.packages.install(confirmed=True, plan_sha256=self.ready['plan_sha256'])
        self.assertEqual(report['state'], 'SYSTEM_PACKAGES_INSTALLED')
        self.assertFalse(report['application_installed']); self.assertFalse(report['system_wiring_verified'])
        self.assertTrue(report['default_services_blocked']); self.packages._verify_installed(self.value)
        self.assertTrue(report['system_bus_ready'])
        self.assertTrue(b.observe()['system_bus_ready'])
        if not self.packages.nginx:
            self.assertEqual(json.loads(json.dumps(b._probe())),
                json.loads((EVIDENCE / 'preexisting-bus-identity.json').read_text()))
        for unit in self.packages._units(self.value['host']):
            self.assertEqual(command('systemctl', 'is-enabled', unit, check=False).stdout.strip(), b'masked')
            self.assertNotEqual(command('systemctl', 'start', unit, check=False).returncode, 0)
        self.assertFalse(Path('/var/lib/hestia-forbidden-apt-hook').exists())
        self.assertEqual(s.POLICY.read_bytes() if s.POLICY.exists() else None, self.policy_before)
        (EVIDENCE / 'installed-report.json').write_text(json.dumps(report, indent=2) + '\n')
        (EVIDENCE / 'installed-packages.json').write_text(json.dumps(self.packages._installed(), indent=2) + '\n')

    def test_05_php_extensions_and_native_collector_are_available(self):
        extensions = set(command('php', '-m').stdout.decode().splitlines())
        self.assertTrue({'curl', 'dom', 'gd', 'mbstring', 'mysqli', 'pdo_mysql', 'xml', 'zip'} <= extensions)
        command('apache2', '-v'); command('mariadbd', '--version')
        if self.packages.nginx: command('nginx', '-v')
        self.assertTrue(Path('/usr/lib/php/sessionclean').is_file())
        self.assertFalse(Path('/etc/systemd/system/phpsessionclean.service').exists())
        self.assertNotEqual(command('systemctl', 'is-enabled', 'phpsessionclean.timer', check=False).stdout.strip(), b'masked')

    def test_06_installed_lost_reply_recovers_without_apt_or_changes(self):
        operation = s.PackageInstallationOperation(self.packages, self.ready['plan_sha256'])
        before = snapshot_journal(self.packages)
        with patch.object(self.packages, '_apt', side_effect=AssertionError('No APT on recovery')):
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
            self.assertEqual(operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)
            with self.assertRaises(s.SystemPackagesError):
                self.packages.install(confirmed=True, plan_sha256=self.ready['plan_sha256'])
        self.assertEqual(before, snapshot_journal(self.packages))

    def test_07_installed_dependencies_run_dedicated_identity_http_and_cleaner(self):
        fixture = ServiceIdentityLive(methodName='test_created_identity_runs_real_apache_php_and_collector_with_private_files')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.test_created_identity_runs_real_apache_php_and_collector_with_private_files()
        self.assertEqual(self.packages.observe_installed()['state'], 'SYSTEM_PACKAGES_INSTALLED')

    def test_08_existing_bus_is_preserved_through_repeated_ensure_and_observation(self):
        identity = b._probe()
        self.assertFalse(b.ensure(confirmed=True)['system_bus_started'])
        self.assertTrue(b.observe()['system_bus_ready'])
        self.assertEqual(b._probe(), identity)

    def test_09_masked_bus_is_refused_without_unmask_or_restart(self):
        identity = b._probe(); path = Path('/etc/systemd/system/dbus.service')
        self.assertFalse(path.exists()); path.symlink_to('/dev/null')
        try:
            command('systemctl', 'daemon-reload')
            with self.assertRaises(b.SystemBusError): b.ensure(confirmed=True)
            self.assertEqual(path.readlink(), Path('/dev/null')); self.assertEqual(b._probe(), identity)
        finally: path.unlink(); command('systemctl', 'daemon-reload')

    def test_10_custom_dropin_is_refused_without_overwrite_or_restart(self):
        identity = b._probe(); directory = Path('/etc/systemd/system/dbus.service.d')
        directory.mkdir(); path = directory / '90-fixture.conf'; content = '[Service]\nEnvironment=HESTIA_FIXTURE=1\n'
        path.write_text(content)
        try:
            command('systemctl', 'daemon-reload')
            with self.assertRaises(b.SystemBusError): b.ensure(confirmed=True)
            self.assertEqual(path.read_text(), content); self.assertEqual(b._probe(), identity)
        finally: path.unlink(); directory.rmdir(); command('systemctl', 'daemon-reload')

    def test_11_changed_vendor_bytes_are_refused_and_not_repaired(self):
        identity = b._probe(); path = b.VENDOR / 'dbus.service'; original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n# fixture change\n')
            with self.assertRaises(b.SystemBusError): b.ensure(confirmed=True)
            self.assertEqual(path.read_bytes(), original + b'\n# fixture change\n')
            self.assertEqual(b._probe(), identity)
        finally: path.write_bytes(original); command('systemctl', 'daemon-reload')

    def test_12_stopped_bus_requires_explicit_ensure_and_read_only_recovery_does_not_start(self):
        # Only the disposable fixture stops its broker. The product has no stop
        # command and never temporarily disables an administrator's protection.
        directory = Path('/etc/systemd/system/dbus.service.d'); directory.mkdir()
        path = directory / '90-fixture.conf'; path.write_text('[Unit]\nRefuseManualStop=no\n')
        try:
            command('systemctl', 'daemon-reload'); command('systemctl', 'stop', 'dbus.service', 'dbus.socket')
        finally: path.unlink(); directory.rmdir(); command('systemctl', 'daemon-reload')
        with self.assertRaises(b.SystemBusError): b.observe()
        self.assertEqual(s.PackageInstallationOperation(self.packages, self.ready['plan_sha256']).recover(
            None, 'apply').decision, RecoveryDecision.MANUAL)
        self.assertEqual(b._state('dbus.service')['ActiveState'], 'inactive')
        self.assertTrue(b.ensure(confirmed=True)['system_bus_started'])
        self.assertTrue(self.packages.observe_installed()['system_bus_ready'])


class InterruptedInstallation(unittest.TestCase):
    def test_real_install_with_failed_receipt_stays_manual_and_never_replays(self):
        packages = profile(); ready = packages.observe(); value = packages._manifest()
        original = s._journal
        def interrupted(fd, name, data):
            if name == 'installed.json': raise OSError('injected durable receipt failure')
            return original(fd, name, data)
        with patch.object(s, '_journal', side_effect=interrupted):
            with self.assertRaisesRegex(s.SystemPackagesError, 'INSTALL_INCOMPLETE'):
                packages.install(confirmed=True, plan_sha256=ready['plan_sha256'])
        packages._verify_installed(value); packages._masks(value['host'])
        self.assertTrue((packages.directory / 'install.attempt').exists())
        self.assertFalse((packages.directory / 'installed.json').exists()); self.assertTrue(packages._policy())
        before = snapshot_journal(packages); operation = s.PackageInstallationOperation(packages, ready['plan_sha256'])
        with patch.object(packages, '_apt', side_effect=AssertionError('No repair/retry')):
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
            with self.assertRaises(s.SystemPackagesError): packages.install(confirmed=True, plan_sha256=ready['plan_sha256'])
        self.assertEqual(before, snapshot_journal(packages))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', choices=('acquire', 'install', 'interrupted'), required=True)
    args = parser.parse_args()
    if os.environ.get('HESTIA_SYSTEM_PACKAGES_TEST') != '1' or os.geteuid() != 0 or Path('/proc/1/comm').read_text().strip() != 'systemd':
        raise RuntimeError('Explicit disposable root systemd opt-in required')
    before = quality.snapshot(ROOT)
    cls, expected = {'acquire': (Acquisition, 5), 'install': (Installation, 12), 'interrupted': (InterruptedInstallation, 1)}[args.phase]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    stable = before == quality.snapshot(ROOT)
    passed = result.wasSuccessful() and result.testsRun == expected and not result.skipped and stable
    report = {'suite': 'Official Debian packages: ' + args.phase, 'tests': result.testsRun, 'expected': expected,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'status': 'PASS' if passed else 'FAIL', 'source_stable': stable, 'source_files': len(before),
        'architecture': command('dpkg', '--print-architecture').stdout.decode().strip(), 'phase': args.phase,
        'web_application_qualified': False, 'service_activation_delivered': False}
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / ('packages-' + args.phase + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    (EVIDENCE / 'PACKAGES-SOURCE-MANIFEST.json').write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
