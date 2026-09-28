#!/usr/bin/env python3
"""Real SIGKILL boundaries and idempotent recovery of the pinned cutover."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage_upgrade_systemd as base
from installer import storage_upgrade_recovery as r

u, f, quality = base.u, base.f, base.quality
EVIDENCE = []


class RecoveryLive(base.StorageUpgradeLive):
    def kill(self):
        os.kill(os.getpid(), signal.SIGKILL)

    def child(self, action, *, killed=True):
        pid = os.fork()
        if pid == 0:
            try:
                action()
                os._exit(0)
            except BaseException:
                traceback.print_exc(); sys.stderr.flush(); os._exit(97)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found: break
            time.sleep(.1)
        else:
            os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            self.fail('Disposable child exceeded bounded recovery timeout')
        if killed:
            self.assertTrue(os.WIFSIGNALED(status), status)
            self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        else: self.assertEqual(status, 0)
        # A new controller gets no live lease or transaction object from child.
        old = self.operation
        self.operation = u.StorageUpgrade(old.runtime, old.source, old.target_source, old.http, old.collector)

    def lease(self):
        attempts = list(self.backups.glob('upgrade-*/attempt.json'))
        self.assertEqual(len(attempts), 1)
        return json.loads(attempts[0].read_bytes())['lease_id']

    def slot(self):
        return self.backups / ('upgrade-' + self.lease())

    def recover(self, direction='forward'):
        return self.operation.recover(self.existing(), self.authority, config_root=self.output,
            backup_root=self.backups, lease_id=self.lease(), direction=direction,
            confirmed=True, allow_global_read_lock=True)

    def resume(self):
        return self.operation.authorize_resume(self.backups, self.lease(), confirmed=True)

    def crash_event(self, state):
        original = u._event
        def event(slot, number, actual):
            original(slot, number, actual)
            if state == actual: self.kill()
        def action():
            with patch.object(u, '_event', side_effect=event): self.execute()
        self.child(action)

    def crash_move(self, destination):
        original = u._move
        def move(source, target):
            original(source, target)
            if target == destination: self.kill()
        def action():
            with patch.object(u, '_move', side_effect=move): self.execute()
        self.child(action)

    def serving(self, rollback=False):
        outcome = self.resume()
        self.assertEqual(outcome, self.resume())
        selected = u.LEGACY_COMMIT if rollback else u.STORAGE_COMMIT
        self.assertEqual(outcome['selected_commit'], selected)
        if not rollback:
            self.http_runtime = self.operation.target_http; self.collector = self.operation.target_collector
            self.spec = self.http_runtime.spec; self.uploads = self.http_root / 'data/uploads'
        self.restart_fixture_services()
        self.assertEqual(self.binary('/' + self.legacy_relative)[:2], (200, base.business.png()))
        self.assertEqual(f._runtime_digest(self.webroot), f.get_release(selected).runtime_sha256)
        EVIDENCE.append({'test': self._testMethodName, 'selected_commit': selected,
            'real_tls_session_and_legacy_photo': True, 'authorization_idempotent': True})

    def test_recovery_prepared_sigkill_forward(self):
        self.ready(); self.crash_event('TARGET_PREPARED')
        value = self.recover(); self.assertEqual(value['state'], 'STORAGE_UPGRADE_APPLIED_GATED')
        self.assertEqual(self.operation.observe(self.backups, self.lease()), value)
        self.closed(); self.serving()

    def test_recovery_source_move_sigkill_rollback(self):
        self.ready(); self.crash_move(self.operation.previous / 'web')
        value = self.recover('rollback'); self.assertTrue(value['rollback_verified'])
        self.assertFalse(value['sql_restored']); self.assertFalse(value['data_deleted'])
        self.assertEqual(self.recover('rollback'), value); self.serving(rollback=True)

    def test_recovery_target_move_sigkill_forward_twice(self):
        self.ready(); self.crash_move(self.webroot)
        value = self.recover(); self.assertEqual(value, self.recover())
        self.serving()

    def test_recovery_interrupted_rollback_converges(self):
        self.ready(); self.crash_move(self.http_root / 'data/uploads')
        original = u._move
        def move(source, target):
            original(source, target)
            if target == self.operation.previous / 'target-web': self.kill()
        def action():
            with patch.object(u, '_move', side_effect=move): self.recover('rollback')
        self.child(action)
        with self.assertRaisesRegex(u.StorageUpgradeError, 'STORAGE_RECOVERY_ROLLBACK_STARTED'): self.recover()
        value = self.recover('rollback'); self.assertEqual(value, self.recover('rollback'))
        self.assertTrue((self.operation.previous / 'target-web').is_dir())
        self.assertTrue((self.slot() / 'unused-uploads').is_dir()); self.serving(rollback=True)

    def test_recovery_mixed_configuration_sigkill(self):
        self.ready(); original = u._replace
        def replace(path, *args):
            original(path, *args)
            if path.name == 'finalized.json': self.kill()
        def action():
            with patch.object(u, '_replace', side_effect=replace): self.execute()
        self.child(action); self.recover(); self.serving()

    def test_recovery_verified_sigkill_lost_apply_response(self):
        self.ready(); self.crash_event('TARGET_VERIFIED')
        self.assertEqual(self.operation.observe(self.backups, self.lease())['state'], 'STORAGE_UPGRADE_RECOVERY_REQUIRED')
        value = self.recover(); self.assertEqual(value, self.recover()); self.serving()

    def test_recovery_lost_resume_response_preserves_new_activity(self):
        self.ready(); self.assertEqual(self.execute()['state'], 'STORAGE_UPGRADE_APPLIED_GATED')
        original = r.ensure
        def ensure(path, value):
            if path.name == 'resume-complete.json': self.kill()
            return original(path, value)
        def action():
            with patch.object(r, 'ensure', side_effect=ensure): self.resume()
        self.child(action)
        self.http_runtime = self.operation.target_http; self.collector = self.operation.target_collector
        self.spec = self.http_runtime.spec; self.uploads = self.http_root / 'data/uploads'
        self.restart_fixture_services(); _, photo = self.photo(); before = photo.read_bytes()
        rows = self.sql(query=f'SELECT * FROM `{self.db}`.UserInfo')
        receipt = self.resume(); self.assertEqual(receipt, self.resume())
        with self.assertRaisesRegex(u.StorageUpgradeError, 'STORAGE_RECOVERY_ACTIVITY_AUTHORIZED'): self.recover('rollback')
        self.assertEqual(photo.read_bytes(), before)
        self.assertEqual(self.sql(query=f'SELECT * FROM `{self.db}`.UserInfo'), rows)
        EVIDENCE.append({'test': self._testMethodName, 'new_real_photo_and_sql_preserved': True, 'rollback_refused': True})

    def test_recovery_interrupted_data_reopen(self):
        self.ready(); self.assertEqual(self.execute()['state'], 'STORAGE_UPGRADE_APPLIED_GATED')
        original = u.da.DataAccessFence.reopen
        def reopen(fence, **kwargs):
            original(fence, **kwargs); self.kill()
        def action():
            with patch.object(u.da.DataAccessFence, 'reopen', reopen): self.resume()
        self.child(action)
        with self.assertRaisesRegex(u.StorageUpgradeError, 'STORAGE_RECOVERY_ACTIVITY_AUTHORIZED'): self.recover('rollback')
        self.serving()

    def test_recovery_partial_resumed_receipt(self):
        self.ready(); self.assertEqual(self.execute()['state'], 'STORAGE_UPGRADE_APPLIED_GATED')
        original = f._write
        def write(fd, name, data, gid, **kwargs):
            if name.startswith('resumed-'):
                original(fd, name, data[:len(data)//2], gid, **kwargs); self.kill()
            return original(fd, name, data, gid, **kwargs)
        def action():
            with patch.object(f, '_write', side_effect=write): self.resume()
        self.child(action); self.serving()

    def test_recovery_partial_config_temporary(self):
        self.ready(); original = f._write
        def write(fd, name, data, gid, **kwargs):
            if name.startswith('.upgrade-'):
                original(fd, name, data[:len(data)//2], gid, **kwargs); self.kill()
            return original(fd, name, data, gid, **kwargs)
        def action():
            with patch.object(f, '_write', side_effect=write): self.execute()
        self.child(action); self.recover(); self.serving()

    def test_recovery_concurrent_controller_refused(self):
        self.ready(); self.crash_event('TARGET_PREPARED')
        def action():
            with self.assertRaisesRegex(u.StorageUpgradeError, 'STORAGE_RECOVERY_BUSY'): self.recover()
        with r.operation_lock(self.slot()): self.child(action, killed=False)
        self.assertFalse((self.slot() / 'applied.json').exists())
        self.recover(); self.serving()

    def test_recovery_new_sql_write_refuses_both_directions(self):
        self.ready(); self.crash_move(self.operation.previous / 'web')
        self.sql([f'UPDATE `{self.db}`.UserInfo SET session_timeout_hours=7 WHERE id_user=1'])
        for direction in ('forward', 'rollback'):
            with self.assertRaisesRegex(Exception, 'COORDINATED_SQL_CHANGED'): self.recover(direction)
        self.assertFalse(self.webroot.exists()); self.assertTrue(self.operation.next_web.exists())
        self.assertEqual(str(self.sql(query=f'SELECT session_timeout_hours FROM `{self.db}`.UserInfo WHERE id_user=1')[0]['session_timeout_hours']), '7')
        self.closed()

    def test_recovery_unknown_configuration_refused(self):
        self.ready(); self.crash_move(self.operation.previous / 'web')
        path = self.http_root / 'provision.attempt'; path.write_bytes(b'unknown configuration')
        for direction in ('forward', 'rollback'):
            with self.assertRaisesRegex(u.StorageUpgradeError, 'STORAGE_RECOVERY_CONFIGURATION_CHANGED'): self.recover(direction)
        self.assertEqual(path.read_bytes(), b'unknown configuration'); self.closed()

    def test_recovery_backup_failure_aborts_unchanged_source(self):
        self.ready()
        with patch.object(u.backup.UpgradeBackup, 'create_and_verify', side_effect=u.StorageUpgradeError('INJECTED_BACKUP_FAILURE')):
            self.assertEqual(self.execute()['state'], 'STORAGE_UPGRADE_INCOMPLETE')
        self.assertEqual(self.recover('rollback')['state'], 'STORAGE_UPGRADE_ROLLED_BACK_GATED')
        self.serving(rollback=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--web', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True); parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--shard-index', type=int, choices=range(4), default=0)
    args = parser.parse_args(); base.previous.WEB = args.web; base.TARGET = args.target
    source = quality.snapshot(base.ROOT)
    names = sorted(n for n in dir(RecoveryLive) if n.startswith(('test_upgrade_', 'test_recovery_')))
    assert len(names) == 20, names
    names = names[args.shard_index::4]
    class ImmediateResult(unittest.TextTestResult):
        def addError(self, test, error):
            super().addError(test, error); self.stream.write(self.errors[-1][1]); self.stream.flush()
        def addFailure(self, test, error):
            super().addFailure(test, error); self.stream.write(self.failures[-1][1]); self.stream.flush()
    result = unittest.TextTestRunner(verbosity=2, resultclass=ImmediateResult).run(unittest.TestSuite(RecoveryLive(n) for n in names))
    stable = quality.snapshot(base.ROOT) == source
    report = {'suite': 'Pinned storage recovery and rollback', 'tests': result.testsRun, 'expected': 5, 'test_ids': names,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'source_stable': stable, 'source_files': len(source), 'source_commit': u.LEGACY_COMMIT, 'target_commit': u.STORAGE_COMMIT,
        'source_profile': 'SEALED_MANAGED_ROOT_OWNED_WEB', 'phase5_complete': False,
        'status': 'PASS' if result.wasSuccessful() and result.testsRun == 5 and not result.skipped and stable else 'FAIL'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    (args.report.parent / 'STORAGE-UPGRADE-SOURCE-MANIFEST.json').write_bytes(quality.encode(source))
    (args.report.parent / 'storage-upgrade-success.json').write_bytes(quality.encode(base.EVIDENCE + EVIDENCE))
    args.report.write_bytes(quality.encode(report)); print(json.dumps(report))
    sys.exit(0 if report['status'] == 'PASS' else 1)
