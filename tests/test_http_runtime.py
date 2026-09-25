"""Closed profile, authority boundaries and immutable input fault tests."""
import dataclasses
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import http_runtime as h
from installer.operations import RecoveryDecision


class HttpRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.spec = h.RuntimeSpec('a' * 32, Path('/var/lib/hestia-http-test'),
            Path('/srv/hestia-web-test'), 'hestia-web-test', 'hestia.test', 8123, '8.4')
        self.runtime = h.HttpRuntime(self.spec)
        self.account = SimpleNamespace(pw_name='hestia-web-test', pw_uid=991, pw_gid=991,
                                       pw_shell='/usr/sbin/nologin')

    def test_paths_reject_injection_overlap_traversal_and_public_runtime(self):
        for field, value in (('root', Path('/srv/runtime')), ('webroot', Path('/tmp/web')),
            ('root', Path('/var/lib/a/../b')), ('root', Path('/var/lib/space here')),
            ('root', Path('/var/lib/x\nRestart=yes')), ('root', Path('/var/lib/%i')),
            ('root', Path('/var/lib/' + 'a' * 70)), ('webroot', Path('/srv/x"')),
            ('root', '/var/lib/string')):
            with self.subTest(field=field, value=value), self.assertRaises(h.HttpRuntimeError):
                dataclasses.replace(self.spec, **{field: value})

    def test_identity_host_port_and_family_have_closed_grammar(self):
        for field, values in {'instance': ('A' * 32, 'a' * 31, None),
            'service_user': ('root', 'nobody', 'www-data', 'x\nuser=root', 'x y'),
            'hostname': ('localhost', '*.test', 'host.test\n', 'a..test', '-a.test', 'a.test/path'),
            'port': (True, 80, 65536, '8123'), 'php_family': ('8.3', '8.4\n', None)}.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(h.HttpRuntimeError):
                    dataclasses.replace(self.spec, **{field: value})

    def test_dedicated_identity_refuses_shared_group_alias_login_and_supplementary_groups(self):
        group = SimpleNamespace(gr_mem=[])
        cases = ({'pw_shell': '/bin/bash'}, {'pw_uid': 0}, {'pw_gid': 0})
        for changes in cases:
            bad = SimpleNamespace(**(vars(self.account) | changes))
            with patch.object(h.pwd, 'getpwnam', return_value=bad), patch.object(h.grp, 'getgrgid', return_value=group), \
                 self.assertRaises(h.HttpRuntimeError): h._identity(self.spec.service_user)
        with patch.object(h.pwd, 'getpwnam', return_value=self.account), \
             patch.object(h.grp, 'getgrgid', return_value=group), \
             patch.object(h.os, 'getgrouplist', return_value=[991]) as groups, \
             patch.object(h.pwd, 'getpwall', return_value=[self.account]) as accounts:
            self.assertIs(h._identity(self.spec.service_user), self.account)
            groups.return_value = [991, 33]
            with self.assertRaises(h.HttpRuntimeError): h._identity(self.spec.service_user)
            groups.return_value = [991]
            accounts.return_value = [self.account, SimpleNamespace(pw_name='other', pw_uid=992, pw_gid=991)]
            with self.assertRaises(h.HttpRuntimeError): h._identity(self.spec.service_user)
            accounts.return_value = [self.account]; group.gr_mem = ['foreign']
            with self.assertRaises(h.HttpRuntimeError): h._identity(self.spec.service_user)

    def test_public_representations_hide_local_paths_and_host(self):
        result = repr(self.spec) + repr(self.runtime)
        for value in (str(self.spec.root), str(self.spec.webroot), self.spec.hostname, self.spec.service_user):
            self.assertNotIn(value, result)

    def test_missing_consent_creates_nothing_and_does_not_probe_host(self):
        with patch.object(self.runtime, 'prepare') as prepare, patch.object(h.os, 'mkdir') as mkdir:
            for value in (False, 1, 'yes', None):
                with self.assertRaisesRegex(h.HttpRuntimeError, 'CONSENT_REQUIRED'):
                    self.runtime.create(confirmed=value)
            prepare.assert_not_called(); mkdir.assert_not_called()

    def test_host_requires_root_and_matching_official_debian_php_family(self):
        with patch.object(h.os, 'geteuid', return_value=991), self.assertRaisesRegex(h.HttpRuntimeError, 'ROOT_REQUIRED'):
            self.runtime._host()
        for system in ({'ID': 'ubuntu', 'VERSION_ID': '24.04'}, {'ID': 'debian', 'VERSION_ID': '14'},
                       {'ID': 'debian', 'VERSION_ID': '12'}):
            with patch.object(h.os, 'geteuid', return_value=0), patch.object(h, 'read_os_release', return_value=system), \
                 patch.object(h, '_identity') as identity, self.assertRaises(h.HttpRuntimeError):
                self.runtime._host()
            identity.assert_not_called()

    def test_commands_are_fixed_argv_bounded_and_ignore_global_php_environment(self):
        with patch.object(h.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.runtime._configtest()
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(run.call_args_list[0].args[0][0:2], ['/usr/sbin/php-fpm8.4', '-t'])
        self.assertEqual(run.call_args_list[1].args[0][0:2], ['/usr/sbin/apache2', '-t'])
        for call in run.call_args_list:
            self.assertNotIn('shell', call.kwargs)
            self.assertEqual(call.kwargs['timeout'], 45)
            self.assertEqual(call.kwargs['env']['PHP_INI_SCAN_DIR'], '')
            self.assertNotIn('PHPRC', call.kwargs['env'])
            self.assertEqual(call.kwargs['stderr'], subprocess.DEVNULL)

    def test_loaded_vendor_transient_and_unknown_unit_state_are_not_adopted(self):
        for code, output in ((0, b'loaded\n'), (1, b'error'), (2, b'not-found\n'), (0, b'not-found\nextra')):
            with patch.object(h.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, output)), \
                 self.assertRaisesRegex(h.HttpRuntimeError, 'UNIT_OCCUPIED'):
                h._unit_absent(self.runtime.unit('php'))
        with patch.object(h.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'not-found\n')):
            h._unit_absent(self.runtime.unit('php'))

    def source(self):
        root = Path(tempfile.mkdtemp(dir='/var/lib', prefix='hestia-http-source-'))
        root.chmod(0o755); self.addCleanup(lambda: shutil.rmtree(root))
        (root / 'index.php').write_text('<?php echo "fixture";')
        (root / 'index.php').chmod(0o644)
        return root

    def test_source_digest_binds_addition_content_and_permissions(self):
        root = self.source(); first = h._code_digest(root, 991)
        (root / 'index.php').write_text('<?php echo "changed";')
        second = h._code_digest(root, 991); self.assertNotEqual(first, second)
        (root / 'index.php').chmod(0o444); third = h._code_digest(root, 991)
        self.assertNotEqual(second, third)
        (root / 'added').write_text('new'); (root / 'added').chmod(0o644)
        self.assertNotEqual(third, h._code_digest(root, 991))

    def test_source_refuses_writable_unreadable_setid_and_linked_files(self):
        root = self.source(); path = root / 'index.php'
        for mode in (0o666, 0o600, 0o4644):
            path.chmod(mode)
            with self.assertRaises(h.HttpRuntimeError): h._code_digest(root, 991)
        path.chmod(0o644); os.link(path, root / 'alias')
        with self.assertRaises(h.HttpRuntimeError): h._code_digest(root, 991)
        (root / 'alias').unlink(); (root / 'alias').symlink_to(path)
        with self.assertRaises(h.HttpRuntimeError): h._code_digest(root, 991)

    def test_source_refuses_symlink_directories_fifos_and_writable_ancestors(self):
        root = self.source(); (root / 'link').symlink_to('/etc', target_is_directory=True)
        with self.assertRaises(h.HttpRuntimeError): h._code_digest(root, 991)
        (root / 'link').unlink(); os.mkfifo(root / 'fifo')
        with self.assertRaises(h.HttpRuntimeError): h._code_digest(root, 991)
        (root / 'fifo').unlink(); root.chmod(0o777)
        with self.assertRaises(Exception): h._code_digest(root, 991)

    def test_precondition_diagnostics_do_not_echo_unexpected_private_errors(self):
        with patch.object(self.runtime, '_host', side_effect=OSError('private-host-secret')), \
             self.assertRaisesRegex(h.HttpRuntimeError, '^HTTP_RUNTIME_PRECONDITION_FAILED$'):
            self.runtime.prepare()

    def test_mutation_failure_keeps_closed_diagnostic_and_never_calls_service_start(self):
        with patch.object(self.runtime, 'prepare', side_effect=OSError('private-token')), \
             patch.object(h, '_command') as command, \
             self.assertRaisesRegex(h.HttpRuntimeError, '^HTTP_RUNTIME_INCOMPLETE$'):
            self.runtime.create(confirmed=True)
        command.assert_not_called()

    def test_observation_and_http_bindings_cannot_succeed_without_live_proof(self):
        with patch.object(self.runtime, '_host', side_effect=OSError('private-token')):
            with self.assertRaisesRegex(h.HttpRuntimeError, '^HTTP_RUNTIME_INCOMPLETE$'): self.runtime.observe()
            with self.assertRaises(h.HttpRuntimeError): self.runtime.http_bindings()

    def test_typed_operation_recovers_only_observed_staging_and_never_replays(self):
        operation = h.HttpRuntimeOperation(self.runtime)
        with patch.object(self.runtime, 'observe', return_value={'plan_sha256': 'd' * 64}) as observe, \
             patch.object(self.runtime, 'create') as create:
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
            self.assertEqual(operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)
            create.assert_not_called(); self.assertEqual(observe.call_count, 1)
        with patch.object(self.runtime, 'observe', side_effect=h.HttpRuntimeError('HTTP_RUNTIME_DRIFT')):
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        self.assertFalse(operation.spec.rollback_supported)

    def test_typed_validation_rechecks_plan_and_refuses_incomplete_or_changed_receipts(self):
        operation = h.HttpRuntimeOperation(self.runtime)
        context = SimpleNamespace(evidence={'hashes_non_secret': {'http_runtime_plan': 'd' * 64}})
        with patch.object(self.runtime, 'observe', return_value={'plan_sha256': 'd' * 64}):
            self.assertTrue(operation.validate(context))
        with patch.object(self.runtime, 'observe', return_value={'plan_sha256': 'e' * 64}):
            self.assertFalse(operation.validate(context))
            with self.assertRaises(h.HttpRuntimeError): operation.commit(context)
        context.evidence = {}; self.assertFalse(operation.validate(context))


if __name__ == '__main__':
    unittest.main()
