import dataclasses
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from installer import web_deployment as w
from installer.operations import RecoveryDecision


class WebDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hestia-deploy-test-', dir='/var/lib')); self.root.chmod(0o755)
        self.addCleanup(lambda: shutil.rmtree(self.root))
        self.web_parent = Path(tempfile.mkdtemp(prefix='hestia-deploy-test-', dir='/srv')); self.web_parent.chmod(0o755)
        self.addCleanup(lambda: shutil.rmtree(self.web_parent))
        self.source = self.root / 'source'; self.source.mkdir(mode=0o755)
        (self.source / 'index.php').write_bytes(b'<?php echo "not executed";'); (self.source / 'index.php').chmod(0o644)
        (self.source / 'bin').mkdir(); (self.source / 'bin/tool.sh').write_bytes(b'#!/bin/sh\nexit 7\n'); (self.source / 'bin/tool.sh').chmod(0o755)
        # Fixture-only full Git tree: production constants are not changed.
        def obj(kind, data): return hashlib.sha1(kind.encode()+b' '+str(len(data)).encode()+b'\0'+data).digest()
        subtree = obj('tree', b'100755 tool.sh\0' + obj('blob', (self.source / 'bin/tool.sh').read_bytes()))
        tree = obj('tree', b'40000 bin\0' + subtree + b'100644 index.php\0' + obj('blob', (self.source / 'index.php').read_bytes())).hex()
        self.pins = patch.multiple(w, WEB_TREE=tree, WEB_FILES=2); self.pins.start(); self.addCleanup(self.pins.stop)
        self.spec = w.DeploymentSpec(self.source, self.web_parent / 'web', self.root / 'journal')
        self.deploy = w.WebDeployment(self.spec)

    def test_complete_copy_is_exact_protected_and_not_an_installed_application(self):
        value = self.deploy.create(confirmed=True)
        self.assertEqual(value['state'], 'WEB_SOURCE_DEPLOYED'); self.assertEqual(value['files'], 2)
        self.assertEqual((self.spec.target / 'bin/tool.sh').read_bytes(), (self.source / 'bin/tool.sh').read_bytes())
        self.assertEqual((self.spec.target / 'bin/tool.sh').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.spec.target / 'index.php').stat().st_mode & 0o777, 0o644)
        for key in ('application_installed','writable_business_storage_ready','service_activation_delivered','system_wiring_verified'):
            self.assertIs(value[key], False)
        self.assertEqual(self.deploy.observe(), value)

    def test_large_source_files_and_complete_manifest_use_deployment_bounds(self):
        raw = b'asset-byte-' * 16000
        (self.source / 'index.php').write_bytes(raw)
        for index in range(200):
            (self.source / ('asset-' + str(index).zfill(4) + '-' + 'a' * 64)).write_bytes(b'fixture')
        def obj(kind, data): return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).digest()
        subtree = obj('tree', b'100755 tool.sh\0' + obj('blob', (self.source / 'bin/tool.sh').read_bytes()))
        entries = [(b'bin/', b'40000 bin\0' + subtree)]
        for path in self.source.iterdir():
            if path.is_file(): entries.append((path.name.encode(), b'100644 ' + path.name.encode() + b'\0' + obj('blob', path.read_bytes())))
        tree = obj('tree', b''.join(value for _, value in sorted(entries))).hex()
        with patch.multiple(w, WEB_TREE=tree, WEB_FILES=202):
            plan = self.deploy._plan(self.deploy.prepare())
            self.assertGreater(len(plan), 16384)
            with patch.object(w, 'MAX_PLAN', 16384), self.assertRaisesRegex(w.WebDeploymentError, 'WEB_DEPLOYMENT_PLAN_LIMIT'):
                self.deploy.create(confirmed=True)
            self.assertFalse(self.spec.journal.exists()); self.assertFalse(self.spec.target.exists())
            result = self.deploy.create(confirmed=True)
            self.assertEqual(result['files'], 202); self.assertEqual(self.deploy.observe(), result)
            self.assertEqual((self.spec.target / 'index.php').read_bytes(), raw)
            self.assertEqual((self.spec.journal / 'deployment.attempt').read_bytes(), plan)

    def test_restrictive_caller_umask_does_not_break_explicit_permissions(self):
        old = os.umask(0o777)
        try: self.deploy.create(confirmed=True)
        finally: os.umask(old)
        self.assertEqual(self.spec.journal.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.spec.target.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.spec.target / 'bin').stat().st_mode & 0o777, 0o755)

    def test_concurrent_controllers_have_exactly_one_exclusive_winner(self):
        context = multiprocessing.get_context('fork'); ready = context.Event(); result = context.Queue()
        def child():
            ready.wait(3)
            try: result.put(self.deploy.create(confirmed=True)['state'])
            except w.WebDeploymentError: result.put('REFUSED')
        workers = [context.Process(target=child) for _ in range(2)]
        for worker in workers: worker.start()
        ready.set()
        for worker in workers:
            worker.join(5)
            if worker.is_alive(): worker.kill(); worker.join(); self.fail('Bounded concurrency check timed out')
            self.assertEqual(worker.exitcode, 0)
        values = [result.get(timeout=2) for _ in workers]; result.close(); result.join_thread()
        self.assertEqual(sorted(values), ['REFUSED', 'WEB_SOURCE_DEPLOYED'])
        self.assertEqual(self.deploy.observe()['state'], 'WEB_SOURCE_DEPLOYED')

    def test_root_consent_and_source_pin_fail_before_reservation(self):
        with patch.object(self.deploy, 'prepare') as prepare:
            for value in (False, None, 1, 'yes'):
                with self.assertRaisesRegex(w.WebDeploymentError, 'CONSENT'): self.deploy.create(confirmed=value)
            prepare.assert_not_called()
        with patch.object(w.os, 'geteuid', return_value=991), self.assertRaises(w.WebDeploymentError): self.deploy.prepare()
        (self.source / 'index.php').write_bytes(b'<?php changed();')
        with self.assertRaisesRegex(w.WebDeploymentError, 'SOURCE_PIN_MISMATCH'): self.deploy.create(confirmed=True)
        self.assertFalse(self.spec.journal.exists()); self.assertFalse(self.spec.target.exists())

    def test_paths_pins_and_overlap_are_rejected(self):
        for change in ({'target': Path('/etc/hestia')}, {'journal': self.spec.target / 'private'},
                       {'source': self.spec.target}, {'source': self.root}, {'target': Path('/srv/../etc/test')},
                       {'commit': 'a'*40}, {'repository': 'other/web'}):
            with self.subTest(change=change), self.assertRaises(Exception): dataclasses.replace(self.spec, **change)

    def test_existing_empty_targets_and_journals_are_not_adopted(self):
        for path in (self.spec.target, self.spec.journal):
            path.mkdir()
            with self.assertRaises(w.WebDeploymentError): self.deploy.create(confirmed=True)
            self.assertEqual(list(path.iterdir()), [])
            path.rmdir()

    def test_source_links_fifo_writable_and_setid_files_are_refused(self):
        file = self.source / 'index.php'
        for mode in (0o666, 0o4644):
            file.chmod(mode)
            with self.assertRaises(w.WebDeploymentError): self.deploy.prepare()
        file.chmod(0o644)
        for kind in ('symlink', 'hardlink', 'fifo'):
            other = self.source / 'other'
            if kind == 'symlink': other.symlink_to('/etc')
            elif kind == 'hardlink': os.link(file, other)
            else: os.mkfifo(other)
            with self.assertRaises(w.WebDeploymentError): self.deploy.prepare()
            other.unlink()

    def test_unlisted_nonexecutable_assets_and_mode_changes_break_full_tree_pin(self):
        file = self.source / 'image.png'; file.write_bytes(b'asset'); file.chmod(0o644)
        with self.assertRaisesRegex(w.WebDeploymentError, 'SOURCE_PIN_MISMATCH'): self.deploy.prepare()
        file.unlink(); (self.source / 'bin/tool.sh').chmod(0o644)
        with self.assertRaisesRegex(w.WebDeploymentError, 'SOURCE_PIN_MISMATCH'): self.deploy.prepare()

    def test_entry_byte_and_time_limits_are_closed(self):
        for name, value in (('MAX_ENTRIES', 1), ('MAX_BYTES', 1), ('MAX_SECONDS', -1)):
            with patch.object(w, name, value), self.assertRaises(w.WebDeploymentError): self.deploy.prepare()

    def test_source_change_during_copy_keeps_partial_journal_and_never_replays(self):
        original = w._write
        def changed(fd, name, data, gid, **kwargs):
            original(fd, name, data, gid, **kwargs)
            if name == 'deployment.attempt': (self.source / 'index.php').write_bytes(b'changed during copy')
        with patch.object(w, '_write', side_effect=changed), self.assertRaises(w.WebDeploymentError): self.deploy.create(confirmed=True)
        self.assertTrue((self.spec.journal / 'deployment.attempt').is_file())
        self.assertFalse((self.spec.journal / 'deployed.json').exists())
        self.assertEqual(w.WebDeploymentOperation(self.deploy).recover(None, 'apply').decision, RecoveryDecision.MANUAL)
        with self.assertRaises(w.WebDeploymentError): self.deploy.create(confirmed=True)

    def test_disk_or_receipt_failure_retains_footprint_without_success(self):
        original = w._write
        for failure in ('index.php', 'deployed.json'):
            def fail(fd, name, data, gid, **kwargs):
                if name == failure: raise OSError('private-path-and-token')
                return original(fd, name, data, gid, **kwargs)
            with patch.object(w, '_write', side_effect=fail), self.assertRaisesRegex(w.WebDeploymentError, '^WEB_DEPLOYMENT_INCOMPLETE$'):
                self.deploy.create(confirmed=True)
            self.assertFalse((self.spec.journal / 'deployed.json').exists())
            self.assertEqual(w.WebDeploymentOperation(self.deploy).recover(None, 'apply').decision, RecoveryDecision.MANUAL)
            shutil.rmtree(self.spec.target); shutil.rmtree(self.spec.journal)

    def test_lost_response_recovers_read_only_and_rollback_remains_manual(self):
        self.deploy.create(confirmed=True); operation = w.WebDeploymentOperation(self.deploy)
        with patch.object(w, '_write', side_effect=AssertionError('no writes')):
            self.assertEqual(operation.recover(None, 'apply').decision, RecoveryDecision.APPLIED)
        self.assertEqual(operation.recover(None, 'rollback').decision, RecoveryDecision.MANUAL)

    def test_target_drift_extra_configuration_or_permission_change_revoke_copy_receipt(self):
        self.deploy.create(confirmed=True); file = self.spec.target / 'index.php'; original = file.read_bytes()
        file.write_bytes(b'drift')
        with self.assertRaises(w.WebDeploymentError): self.deploy.observe()
        file.write_bytes(original); file.chmod(0o600)
        with self.assertRaises(w.WebDeploymentError): self.deploy.observe()
        file.chmod(0o644); (self.spec.target / 'install.lock').write_text('sealed')
        with self.assertRaises(w.WebDeploymentError): self.deploy.observe()

    def test_private_paths_are_not_in_reports_or_repr_and_forged_receipts_fail(self):
        value = self.deploy.create(confirmed=True)
        public = repr(self.spec)+repr(self.deploy)+json.dumps(value)
        for path in (self.source, self.spec.target, self.spec.journal): self.assertNotIn(str(path), public)
        path = self.spec.journal / 'deployed.json'; content = json.loads(path.read_text());content['plan_sha256']='0'*64
        path.write_text(json.dumps(content))
        with self.assertRaises(w.WebDeploymentError): self.deploy.observe()


if __name__ == '__main__': unittest.main()
