"""Closed package plans; host package/account databases are never changed here."""
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import system_packages as s
from installer.operations import RecoveryDecision


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hestia-packages-core-', dir='/var/lib'))
        self.addCleanup(lambda: shutil.rmtree(self.root))
        self.packages = s.SystemPackages('a' * 32); self.packages.directory = self.root / 'journal'
        self.host = {'debian': '13', 'suite': 'trixie', 'php': '8.4', 'architecture': 'amd64'}
        self.before = {'base': {'version': '1', 'architecture': 'amd64'}}
        self.value = {'version': 1, 'created_unix': int(time.time()), 'host': self.host, 'before': self.before,
                      'proposed': {'added': '2'}, 'archives': {'added': {'version': '2', 'architecture': 'amd64'}},
                      'requested': ['base', 'added']}

    def test_input_closes_paths_and_optional_proxy_to_a_boolean(self):
        for value in ('', '../a', 'A' * 32, None, 1):
            with self.assertRaises(s.SystemPackagesError): s.SystemPackages(value)
        for value in (1, None, 'true'):
            with self.assertRaises(s.SystemPackagesError): s.SystemPackages('a' * 32, nginx=value)

    def test_both_consents_precede_host_observation(self):
        with patch.object(self.packages, 'prepare') as prepare, patch.object(self.packages, 'observe') as observe:
            for value in (False, 1, 'true', None):
                with self.assertRaisesRegex(s.SystemPackagesError, 'CONSENT_REQUIRED'): self.packages.acquire(confirmed=value)
                with self.assertRaisesRegex(s.SystemPackagesError, 'CONSENT_REQUIRED'):
                    self.packages.install(confirmed=value, plan_sha256='b' * 64)
            prepare.assert_not_called(); observe.assert_not_called()

    def test_dpkg_state_refuses_holds_partial_removed_and_duplicates(self):
        good = b'base\t1:2.3-4+deb13u1\tamd64\tinstall ok installed\n'
        self.assertEqual(s.installed(good)['base']['version'], '1:2.3-4+deb13u1')
        for raw in (good + good, good.replace(b'install ok installed', b'hold ok installed'),
                    good.replace(b'install ok installed', b'deinstall ok config-files'),
                    good.replace(b'install ok installed', b'install ok unpacked'), b'', good.replace(b'amd64', b'i386')):
            with self.assertRaises(s.SystemPackagesError): s.installed(raw)

    def test_simulation_extracts_exact_new_dependency_versions(self):
        raw = b'Reading package lists...\nInst liba:amd64 (1:2.3-4 Debian:13 [amd64])\nConf liba (1:2.3-4 Debian:13 [amd64])\n'
        self.assertEqual(s.simulation(raw, self.before), {'liba:amd64': '1:2.3-4'})
        # Official APT's empty trailing set reports no consequential break.
        raw = b'Inst libheif-plugin-dav1d (1.19.8-1+deb13u1 Debian:13/stable [amd64]) []\n'
        self.assertEqual(s.simulation(raw, self.before), {'libheif-plugin-dav1d': '1.19.8-1+deb13u1'})

    def test_simulation_refuses_upgrade_downgrade_removal_and_adoption(self):
        for raw in (b'Inst base [1] (2 Debian:13 [amd64])', b'Inst base [1] (0 Debian:13 [amd64])',
                    b'Inst added (2 Debian:13 [amd64]) [broken:amd64]', b'Inst base [1] (2 Debian:13 [amd64]) []',
                    b'Remv base [1]', b'Purg base [1]', b'Inst base:amd64 (1 Debian:13 [amd64])',
                    b'Inst added (2 Debian:13 [amd64])\nInst added (2 Debian:13 [amd64])', b'Conf base (1 Debian)'):
            with self.assertRaises(s.SystemPackagesError): s.simulation(raw, self.before)

    def test_plan_size_is_bounded(self):
        raw = '\n'.join('Inst p%d (1 Debian:13 [amd64])' % n for n in range(s.MAX_PACKAGES + 1)).encode()
        with self.assertRaises(s.SystemPackagesError): s.simulation(raw, {})

    def test_configuration_isolated_from_host_hooks_credentials_and_repositories(self):
        config = self.packages._configuration(self.host)
        raw = config['apt.conf'].decode(); sources = config['etc/sources.list'].decode()
        for key in ('parts', 'sourceparts', 'preferencesparts', 'netrcparts', 'trustedparts'):
            self.assertIn('Dir::Etc::' + key + ' "' + str(self.packages.directory / 'etc/parts') + '";', raw)
        self.assertIn('Acquire::https::Proxy "DIRECT";', raw)
        self.assertIn('Acquire::Check-Valid-Until "true";', raw)
        self.assertIn('Acquire::https::Verify-Peer "true";', raw)
        self.assertIn('"--refuse-unsafe-io";', raw)
        self.assertEqual(sources.count('signed-by=/usr/share/keyrings/debian-archive-keyring.gpg'), 3)
        self.assertEqual(sources.count('https://deb.debian.org/'), 3)
        self.assertNotIn('backports', sources); self.assertNotIn('trusted=yes', sources)

    def test_official_php_profile_does_not_claim_bookworm_web_compatibility(self):
        old = {**self.host, 'debian': '12', 'php': '8.2', 'suite': 'bookworm'}
        self.assertIn('php8.2-fpm', self.packages._packages(old))
        self.assertNotIn('php8.4-fpm', self.packages._packages(old))
        self.assertNotIn('nginx', self.packages._packages(self.host))
        self.assertIn('nginx', s.SystemPackages('b' * 32, nginx=True)._packages(self.host))
        self.assertFalse(any('sessionclean' in u for u in self.packages._units(self.host)))

    def test_dpkg_configuration_refuses_hooks_and_runtime_exclusions(self):
        for line in ('no-debsig', 'log /var/log/dpkg.log', 'force-unsafe-io',
                     'path-exclude /usr/share/man/*', 'path-include=/usr/share/doc/*/copyright',
                     'path-exclude /usr/share/gnome/help/*/*', 'path-include /usr/share/gnome/help/*/C/*',
                     'path-exclude /usr/share/linda/*', 'path-exclude /usr/share/lintian/overrides/*',
                     'path-exclude /usr/share/omf/*/*-*.emf', 'path-include /usr/share/omf/*/*-C.emf'):
            self.assertTrue(s._dpkg_line(line))
        for line in ('pre-invoke=evil', 'post-invoke=/bin/true', 'force-all',
                     'path-exclude=/usr/bin/*', 'path-exclude=/usr/share/man/../../../etc/*', 'root=/other', 'log /etc/passwd'):
            self.assertFalse(s._dpkg_line(line))

    def test_command_is_bounded_with_empty_stdin_and_closed_failure(self):
        def capture(argv, data, directory, out, timeout, **kw):
            self.assertEqual(data, b''); self.assertEqual(timeout, 17); self.assertEqual(kw['limit'], 123)
            out.write(b'ok'); return (0, 2, 0)
        with patch.object(s.br, 'capture', side_effect=capture):
            self.assertEqual(s.command(['fixed'], self.root, timeout=17, limit=123), b'ok')
        with patch.object(s.br, 'capture', return_value=(9, 0, 0)):
            with self.assertRaisesRegex(s.SystemPackagesError, '^SYSTEM_PACKAGES_COMMAND_FAILED$'): s.command(['fixed'], self.root)
        with patch.object(s, 'command', return_value=b'') as run:
            self.packages._apt(['update'])
            self.assertEqual(run.call_args.args[0][:4], ['/usr/bin/env', 'APT_CONFIG=' + str(self.packages.directory / 'apt.conf'),
                                                       'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get'])

    def test_large_journal_is_exclusive_bounded_private_and_durable(self):
        value = {'packages': 'x' * 20000}
        with s.fs._directory(self.root) as fd:
            s._journal(fd, 'ready.json', value)
            self.assertEqual(s.f._json_read(fd, 'ready.json', 0, mode=0o600, limit=40000), value)
            with self.assertRaises(FileExistsError): s._journal(fd, 'ready.json', {})
            with self.assertRaises(s.SystemPackagesError): s._journal(fd, '../escape', {})
            with self.assertRaises(s.SystemPackagesError): s._journal(fd, 'installed.json', {'x': 'x' * (4 * 1024 * 1024)})

    def test_package_reader_refuses_links_fifo_writable_file_and_large_file(self):
        path = self.root / 'archive'; path.write_bytes(b'fixture'); path.chmod(0o644)
        self.assertEqual(s._digest(path), s.f._sha(b'fixture'))
        link = self.root / 'link'; link.symlink_to(path)
        with self.assertRaises(Exception): s._digest(link)
        link.unlink(); os.link(path, link)
        with self.assertRaises(Exception): s._digest(path)
        link.unlink(); path.chmod(0o666)
        with self.assertRaises(Exception): s._digest(path)
        path.chmod(0o644)
        with self.assertRaises(Exception): s._digest(path, limit=3)
        path.unlink(); os.mkfifo(path)
        with self.assertRaises(Exception): s._digest(path)

    def test_archives_bind_version_architecture_count_and_digest(self):
        directory = self.packages.directory / 'cache/archives'; directory.mkdir(parents=True)
        path = directory / 'added_2_amd64.deb'; path.write_bytes(b'fixture'); path.chmod(0o644)
        with patch.object(s, 'command', return_value=b'added\t2\tamd64\n'):
            self.assertEqual(self.packages._archives({'added': '2'}, 'amd64')['added']['sha256'], s.f._sha(b'fixture'))
            for proposed, arch in (({'added': '3'}, 'amd64'), ({'added': '2'}, 'arm64'), ({'added': '2', 'missing': '1'}, 'amd64')):
                with self.assertRaises(s.SystemPackagesError): self.packages._archives(proposed, arch)

    def test_only_exact_official_keyring_compatibility_link_is_accepted(self):
        keyring = self.root / 'debian-archive-keyring.gpg'; target = keyring.with_suffix('.pgp')
        target.write_bytes(b'official fixture'); target.chmod(0o644)
        with patch.object(s, 'KEYRING', keyring):
            keyring.write_bytes(target.read_bytes()); keyring.chmod(0o644)
            self.assertEqual(s._keyring()['layout'], 'regular-gpg')
            keyring.unlink(); keyring.symlink_to(target.name)
            self.assertEqual(s._keyring(), {'layout': 'official-pgp-link', 'sha256': s.f._sha(target.read_bytes())})
            keyring.unlink(); keyring.symlink_to('/other/keyring.pgp')
            with self.assertRaises(s.SystemPackagesError): s._keyring()
            keyring.unlink(); keyring.symlink_to(target.name); target.unlink(); target.symlink_to('/other')
            with self.assertRaises(Exception): s._keyring()

    def test_indices_require_signed_release_files_and_bound_total(self):
        directory = self.packages.directory / 'state/lists'; directory.mkdir(parents=True)
        with self.assertRaises(s.SystemPackagesError): self.packages._indices()
        for n in range(3): (directory / (str(n) + '_InRelease')).write_bytes(b'signed-fixture')
        self.assertEqual(len(self.packages._indices()), 3)
        (directory / '0_InRelease').unlink(); (directory / '0_InRelease').symlink_to(directory / '1_InRelease')
        with self.assertRaises(Exception): self.packages._indices()

    def test_interrupted_acquisition_keeps_attempt_and_never_certifies(self):
        with patch.object(self.packages, 'prepare', return_value=(self.host, self.before)), \
             patch.object(self.packages, '_apt', side_effect=RuntimeError('private details')):
            with self.assertRaisesRegex(s.SystemPackagesError, '^SYSTEM_PACKAGES_ACQUISITION_INCOMPLETE$'):
                self.packages.acquire(confirmed=True)
        self.assertTrue((self.packages.directory / 'acquire.attempt').is_file())
        self.assertFalse((self.packages.directory / 'ready.json').exists())
        with patch.object(self.packages, '_apt') as apt:
            self.assertEqual(s.PackageAcquisitionOperation(self.packages).recover(None, 'apply').decision, RecoveryDecision.MANUAL)
            apt.assert_not_called()

    def test_ready_plan_expires_or_refuses_backward_clock_and_host_change(self):
        self.packages.directory.mkdir()
        with patch.object(self.packages, '_manifest', return_value=self.value), \
             patch.object(self.packages, '_installed', return_value=self.before):
            self.assertEqual(self.packages.observe()['state'], 'PACKAGE_ARCHIVES_READY')
            for now in (self.value['created_unix'] - 1, self.value['created_unix'] + s.MAX_AGE + 1):
                with patch.object(s.time, 'time', return_value=now):
                    with self.assertRaisesRegex(s.SystemPackagesError, 'PLAN_EXPIRED'): self.packages.observe()
            with patch.object(self.packages, '_installed', return_value={}):
                with self.assertRaisesRegex(s.SystemPackagesError, 'HOST_CHANGED'): self.packages.observe()

    def test_private_apt_parts_cannot_gain_an_unsealed_hook(self):
        parts = self.packages.directory / 'etc/parts'; parts.mkdir(parents=True)
        (parts / 'hook').write_text('DPkg::Pre-Invoke { "foreign"; };')
        value = {**self.value, 'requested': list(self.packages._packages(self.host))}
        with s.fs._directory(self.packages.directory) as fd:
            s._journal(fd, 'ready.json', value)
            s._journal(fd, 'acquire.attempt', {'host': self.host, 'before': self.before, 'nginx': False})
        with patch.object(self.packages, '_host', return_value=self.host), patch.object(self.packages, '_apt') as apt:
            with self.assertRaisesRegex(s.SystemPackagesError, 'CONFIGURATION_DRIFT'): self.packages._manifest()
            apt.assert_not_called()

    def test_plan_mismatch_precedes_masks_or_package_installation(self):
        with patch.object(self.packages, 'observe', return_value={'plan_sha256': 'b' * 64}), \
             patch.object(self.packages, '_manifest') as manifest, patch.object(self.packages, '_apt') as apt:
            with self.assertRaisesRegex(s.SystemPackagesError, 'PLAN_MISMATCH'):
                self.packages.install(confirmed=True, plan_sha256='c' * 64)
            manifest.assert_not_called(); apt.assert_not_called()

    def test_custom_policy_is_never_adopted_or_overwritten(self):
        policy = self.root / 'policy-rc.d'
        with patch.object(s, 'POLICY', policy):
            self.assertFalse(self.packages._policy()); policy.write_bytes(s.POLICY_BYTES); policy.chmod(0o755)
            self.assertTrue(self.packages._policy()); policy.write_bytes(b'#!/bin/sh\nexit 0\n')
            with self.assertRaisesRegex(s.SystemPackagesError, 'POLICY_OCCUPIED'): self.packages._policy()
            self.assertEqual(policy.read_bytes(), b'#!/bin/sh\nexit 0\n')

    def test_service_and_socket_masks_require_applicable_properties_and_empty_cgroup(self):
        for unit in self.packages._units(self.host): (self.root / unit).symlink_to('/dev/null')
        def observe(argv, *args, **kw):
            raw = b'LoadState=masked\nActiveState=inactive\nJob=\n'
            return raw + (b'MainPID=0\nControlPID=0\n' if argv[-1].endswith('.service') else b'')
        with patch.object(s.h.drain, 'UNIT_ROOT', self.root), patch.object(s, 'command', side_effect=observe), \
             patch.object(s.h.drain, '_empty_cgroup', return_value=True):
            self.packages._masks(self.host)
            with patch.object(s.h.drain, '_empty_cgroup', return_value=False):
                with self.assertRaises(s.SystemPackagesError): self.packages._masks(self.host)

    def test_installed_state_has_exact_additions_and_no_previous_changes(self):
        after = {**self.before, 'added': {'version': '2', 'architecture': 'amd64'}}
        with patch.object(self.packages, '_installed', return_value=after): self.packages._verify_installed(self.value)
        for bad in ({'added': after['added']}, {**after, 'foreign': after['added']},
                    {**after, 'base': {'version': '2', 'architecture': 'amd64'}}):
            with patch.object(self.packages, '_installed', return_value=bad):
                with self.assertRaises(s.SystemPackagesError): self.packages._verify_installed(self.value)

    def test_lost_reply_recovery_observes_without_apt_or_replay(self):
        digest = 'b' * 64; report = {'plan_sha256': digest}
        operation = s.PackageInstallationOperation(self.packages, digest)
        with patch.object(self.packages, 'observe_installed', return_value=report), \
             patch.object(self.packages, 'install') as install, patch.object(self.packages, '_apt') as apt:
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
            context = SimpleNamespace(evidence={'hashes_non_secret': {'system_packages_plan': digest}})
            self.assertTrue(operation.validate(context)); install.assert_not_called(); apt.assert_not_called()
        self.assertEqual(operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)
        with patch.object(self.packages, 'observe_installed', return_value={'plan_sha256': 'c' * 64}):
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)

    def test_partial_installation_never_recovers_as_applied_or_retries(self):
        operation = s.PackageInstallationOperation(self.packages, 'b' * 64)
        with patch.object(self.packages, 'observe_installed', side_effect=s.SystemPackagesError('partial')), \
             patch.object(self.packages, 'install') as install:
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
            self.assertFalse(operation.validate(None)); install.assert_not_called()


if __name__ == '__main__': unittest.main()
