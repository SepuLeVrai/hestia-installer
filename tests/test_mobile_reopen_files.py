"""Real Ext4 release and private journals. Native service/Gateway audit is isolated.

These tests certify the file-release subplan, not SQL admission or end-to-end
mobile reopening. Existing native Gateway and SQL campaigns keep their scope.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import mobile_reopen_files as r
from installer.model import InstallerError, canonical_bytes
import test_web_fence_files as fixture
import test_data_access_files as data_fixture
import test_inode_fence_files as inode_fixture


class ReopenFilesTests(unittest.TestCase):
    fixture_root = inode_fixture.VOLUME
    mount = fixture.WebFenceFiles.mount

    def setUp(self):
        fixture.WebFenceFiles.setUp(self)
        self.runtime = self.barrier._drain.runtime
        self.barrier._drain.cleaner = object()  # No systemd effect in this filesystem suite.
        self.barrier._profile = canonical_bytes({'foundation': {}, 'gateway_service': {}})
        self.barrier.assert_held.side_effect = lambda: self.lease.assert_held()
        mocked = patch.object(self.runtime, '_inspect_configuration', return_value=(self.account, None, None, None))
        mocked.start(); self.addCleanup(mocked.stop)
        self.access = data_fixture.DataAccessTests.acquire(self)
        self.addCleanup(lambda: inode_fixture.fixture_clear(self.data))
        self.config = self.scope.directory.parent
        self.addCleanup(lambda: inode_fixture.fixture_clear(self.config))
        self.stack = ExitStack()
        self.conf = self.stack.enter_context(r.fs._directory(self.config))
        r.f._write(self.conf, 'assistant.json', b'fixture-only', self.account.pw_gid, mode=0o660)
        r.f._write(self.conf, 'database.json', b'fixture-only', self.account.pw_gid)
        self.parents = (self.root / 'external-one', self.root / 'external-two')
        for parent in self.parents: parent.mkdir(mode=0o755)
        self.paths = tuple(parent / 'reserved' for parent in self.parents)
        changed = patch.object(r.ef, 'PATHS', self.paths); changed.start(); self.addCleanup(changed.stop)
        self.addCleanup(lambda: [inode_fixture.fixture_clear(parent) for parent in self.parents])
        self.external = r.ef.acquire(self.lease, confirmed=True)
        self.configuration = self.stack.enter_context(r.cf.admission.acquire(
            self.conf, self.web, self.account.pw_gid, external=self.external))
        self.fences = (r.inf.acquire(self.access, confirmed=True),
                       r.cf.acquire(self.lease, self.configuration, confirmed=True),
                       r.wf.acquire(self.barrier, confirmed=True))
        for fence in self.fences: self.addCleanup(fence.close)
        self.originals = {role: (self.scope.directory / module.MARKER).read_bytes()
                          for role, module in r.MODULES.items()}
        web = {'state': 'PROVISIONED_BACKUP_RESTORE_VERIFIED', 'backup_id': 'b' * 32,
               'activity_resumed': False, 'service_profile_sha256': r.f._sha(self.barrier._profile),
               'inode_fence_sha256': r.f._sha(self.originals['data']),
               'configuration_fence_sha256': r.f._sha(self.originals['configuration']),
               'web_fence_sha256': r.f._sha(self.originals['web']),
               'data_access_fence_sha256': r.f._sha(self.access._raw),
               'external_path_reservations_sha256': r.f._sha(self.external._raw)}
        self.backups = self.root / 'backups'; self.backups.mkdir(mode=0o700)
        manifest = canonical_bytes({'fixture': 'not a SQL certificate'})
        web['manifest_sha256'] = r.f._sha(manifest)
        web_slot = self.backups / web['backup_id']; web_slot.mkdir(mode=0o700)
        self.write(web_slot / 'coordinated.json', manifest)
        gateway_slot = self.backups / ('gateway-' + self.lease.lease_id); gateway_slot.mkdir(mode=0o700)
        composed = canonical_bytes({'web': web}); self.write(gateway_slot / 'composed.json', composed)
        intent = {'version': 1, 'fence': {'instance': self.scope.instance, 'lease_id': self.lease.lease_id,
                   'barrier_sha256': r.f._sha(self.barrier._profile)}, 'snapshot_sha256': 'c' * 64,
                   'composed_sha256': r.f._sha(composed), 'web_verified_sha256': r.f._sha(canonical_bytes(web))}
        self.released = r.gateway._receipt(canonical_bytes(intent))
        self.write(self.scope.directory / r.gateway.RELEASED, self.released)
        self.gateway = object.__new__(r.GatewayServiceRuntime); self.gateway.web = self.runtime
        def audited(*args, **kwargs):
            self.assertEqual((self.scope.directory / r.gateway.RELEASED).read_bytes(), self.released)
            self.assertEqual((gateway_slot / 'composed.json').read_bytes(), composed)
            return SimpleNamespace(report=lambda: r.strict_json_loads(self.released))
        audit = patch.object(r.gateway, 'recover', side_effect=audited)
        self.gateway_audit = audit.start(); self.addCleanup(audit.stop)
        self.control = self.controller()
        self.addCleanup(self.close_handles)

    def close_handles(self):
        self.stack.close(); self.access.close(); self.lease.close()

    @staticmethod
    def write(path, raw): path.write_bytes(raw); path.chmod(0o600)

    def controller(self):
        return r.ReopenFilesPlan(self.barrier, self.access, self.configuration,
                                 self.external, self.gateway, self.backups)

    def planned(self):
        self.document = self.control.plan(confirmed=True)
        return self.document

    def execute(self, action='apply', **kwargs):
        return self.control.execute(action, self.document['plan_sha256'], confirmed=True, **kwargs)

    def assert_closed(self):
        self.access.assert_held(); self.external.assert_held()
        self.assertEqual(self.scope.observe()['state'], 'MAINTENANCE_REQUIRED')
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(r.hd.m.MaintenanceError): self.lease.resume(confirmed=True)
        with self.assertRaises(r.da.DataAccessError): self.access.reopen(confirmed=True)
        self.assertEqual((self.scope.directory / r.gateway.RELEASED).read_bytes(), self.released)

    def assert_done(self, result):
        self.assertEqual(result['transaction']['state'], 'DONE', result)
        self.assertEqual(result['state'], 'FILE_PROTECTIONS_RELEASED_ACTIVITY_CLOSED')
        for key in ('activity_resumed', 'admission_verified', 'services_started',
                    'data_access_reopened', 'external_paths_released'): self.assertIs(result[key], False)
        for role, module in r.MODULES.items():
            self.assertFalse((self.scope.directory / module.MARKER).exists())
            self.assertFalse((self.scope.directory / module.RELEASE).exists())
            self.assertEqual((self.control.root / (role + '-original.json')).read_bytes(), self.originals[role])
        self.assert_closed()

    def test_plan_preserves_all_five_original_journals_and_never_unseals(self):
        doc = self.planned(); self.assertEqual(doc['state'], 'PLANNED')
        self.assertEqual(len(doc['steps']), 3)
        for fence in self.fences: fence.assert_held()
        self.assertEqual(self.control.plan(confirmed=True), doc)
        self.assertEqual(len(self.control.profile()['journals']), 5)
        self.assertFalse((self.scope.directory / r.guard.MARKER).exists())
        self.assert_closed()

    def test_release_is_ordered_keeps_bytes_modes_and_activity_closed(self):
        self.planned()
        self.assert_done(self.execute())
        self.assertEqual((self.web / 'index.php').read_bytes(), b'original')
        self.assertEqual((self.web / 'bin/tool').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.config / 'assistant.json').read_bytes(), b'fixture-only')
        self.assertEqual((self.data / 'uploads/payload').read_bytes(), b'original')
        for path in (self.web / 'index.php', self.config / 'assistant.json', self.data / 'uploads/payload'):
            path.write_bytes(path.read_bytes())  # Actual ordinary-root write after flag release.
        self.assert_done(self.execute('check'))

    def test_confirmation_and_boot_public_profiles_refuse_before_plan(self):
        with self.assertRaises(InstallerError): self.control.plan(confirmed=False)
        for name in ('boot', 'public'):
            path = self.runtime.spec.root.parent / name; path.mkdir()
            with self.assertRaises(Exception): self.control.plan(confirmed=True)
            path.rmdir()
        original = self.barrier._profile
        self.barrier._profile = canonical_bytes({'foundation': {}, 'gateway_service': {}, 'public_ingress': {}})
        with self.assertRaises(InstallerError): self.control.plan(confirmed=True)
        self.barrier._profile = original
        self.assertFalse(self.control.root.exists())
        self.assert_closed()

    def test_wrong_confirmation_never_creates_guard_or_changes_flags(self):
        self.planned()
        with self.assertRaises(InstallerError): self.control.execute('apply', '0' * 64, confirmed=True)
        with self.assertRaises(InstallerError): self.control.execute('apply', self.document['plan_sha256'], confirmed=False)
        self.assertFalse((self.scope.directory / r.guard.MARKER).exists())
        for fence in self.fences: fence.assert_held()

    def test_changed_archive_refuses_before_first_effect(self):
        self.planned(); self.write(self.backups / ('b' * 32) / 'coordinated.json', b'{}')
        with self.assertRaises(InstallerError): self.execute()
        for fence in self.fences: fence.assert_held()
        self.assert_closed()

    def test_partial_and_linked_saved_journals_are_never_repaired(self):
        self.planned(); path = self.control.root / 'data-original.json'; raw = path.read_bytes()
        for bad in (b'', b'{}', raw[:40]):
            self.write(path, bad)
            with self.assertRaises(InstallerError): self.execute()
            self.assertEqual(path.read_bytes(), bad)
        self.write(path, raw); os.link(path, self.root / 'alias-journal')
        with self.assertRaises(Exception): self.execute()
        self.assert_closed()

    def test_all_three_interrupted_flag_releases_recover_without_refreezing(self):
        self.planned()
        for role, module in r.MODULES.items():
            original = module._flags; writes = []
            def cut(fd, value=None):
                result = original(fd, value)
                if value is not None and not value & r.inf.IMMUTABLE:
                    writes.append(value)
                    if len(writes) == 1: raise OSError('lost response after real flag removal')
                return result
            with patch.object(module, '_flags', side_effect=cut): result = self.execute('apply' if role == 'data' else 'resume')
            self.assertEqual(result['transaction']['state'], 'FAILED')
            self.assert_closed()
            self.execute('retry', name='mobile-reopen-files.' + role)
        self.assert_done(self.execute('resume'))

    def test_both_native_unlink_cuts_recover_for_each_role(self):
        self.planned()
        for role, module in r.MODULES.items():
            for boundary in (module.RELEASE, module.MARKER):
                unlink = os.unlink
                def cut(name, *args, **kwargs):
                    unlink(name, *args, **kwargs)
                    if name == boundary: raise OSError('lost unlink response')
                with patch.object(r.os, 'unlink', side_effect=cut):
                    result = self.execute('apply' if role == 'data' and boundary == module.RELEASE else
                                          'resume' if boundary == module.RELEASE else 'retry',
                                          **({'name': 'mobile-reopen-files.' + role} if boundary == module.MARKER else {}))
                self.assertIn(result['transaction']['state'], ('FAILED', 'MANUAL_ACTION_REQUIRED'))
                self.assert_closed()
            self.execute('retry', name='mobile-reopen-files.' + role)
        self.assert_done(self.execute('resume'))

    def test_completed_recheck_is_read_only_and_missing_guard_never_recreated(self):
        self.planned(); self.assert_done(self.execute())
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.control.root.rglob('*') if p.is_file()}
        with patch.object(r.files, '_new', side_effect=AssertionError('unexpected write')):
            self.assert_done(self.execute('check'))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                for p in self.control.root.rglob('*') if p.is_file()})
        (self.scope.directory / r.guard.MARKER).unlink()
        with self.assertRaises(InstallerError): self.execute('resume')
        self.assertFalse((self.scope.directory / r.guard.MARKER).exists())
        self.assert_closed()

    def test_done_web_content_drift_revokes_check_without_repair(self):
        self.planned(); self.execute(); (self.web / 'index.php').write_bytes(b'changed')
        with self.assertRaises(InstallerError): self.execute('check')
        self.assertEqual((self.web / 'index.php').read_bytes(), b'changed'); self.assert_closed()

    def test_done_step_is_reobserved_before_next_release(self):
        self.planned(); engine = self.control.engine()
        def cut(name, phase, event):
            if name == 'mobile-reopen-files.configuration' and phase == 'done' and event == 'checkpoint':
                raise KeyboardInterrupt()
        engine._fault_hook = cut
        with patch.object(self.control, 'engine', return_value=engine), self.assertRaises(KeyboardInterrupt): self.execute()
        (self.config / 'database.json').write_bytes(b'changed')
        with self.assertRaises(InstallerError): self.execute('resume')
        self.fences[2].assert_held(); self.assert_closed()

    def test_missing_native_marker_before_effect_never_counts_as_done(self):
        self.planned(); (self.scope.directory / r.inf.MARKER).unlink()
        with self.assertRaises(InstallerError): self.execute()
        self.assertFalse((self.control.root / 'data-released.json').exists())
        self.assert_closed()

    def test_unknown_native_release_journal_cannot_be_overwritten(self):
        self.planned(); path = self.scope.directory / r.inf.RELEASE; self.write(path, b'{}')
        with self.assertRaises(Exception): self.execute()
        self.assertEqual(path.read_bytes(), b'{}'); self.assert_closed()

    def test_foreign_plan_guard_and_lease_refuse_without_rewrite(self):
        self.planned(); r.guard.begin(self.lease, plan_sha256='f' * 64, confirmed=True)
        path = self.scope.directory / r.guard.MARKER; original = path.read_bytes()
        with self.assertRaises(r.guard.ReopenGuardError): self.execute()
        self.assertEqual(path.read_bytes(), original); self.assert_closed()

    def test_journal_may_finish_after_receipt_response_is_lost(self):
        self.planned(); save = self.control._save
        def cut(name, raw):
            save(name, raw)
            if name == 'data-released.json': raise OSError('lost receipt response')
        with patch.object(self.control, '_save', side_effect=cut): result = self.execute()
        self.assertEqual(result['transaction']['state'], 'FAILED')
        before = (self.control.root / 'data-released.json').stat().st_mtime_ns
        self.execute('retry', name='mobile-reopen-files.data')
        self.assertEqual((self.control.root / 'data-released.json').stat().st_mtime_ns, before)
        self.assert_done(self.execute('resume'))

    def reopen_handles(self):
        lease_id = self.lease.lease_id; external_raw = self.external._raw
        self.stack.close(); self.access.close(); self.lease.close()
        self.lease = self.scope.recover(lease_id, confirmed=True)
        self.addCleanup(self.close_handles)
        self.barrier._lease = self.lease
        self.access = r.da.recover(self.runtime, self.lease, confirmed=True); self.addCleanup(self.access.close)
        self.external = r.ef.ExternalFence(self.lease, external_raw)
        self.stack = ExitStack()
        self.conf = self.stack.enter_context(r.fs._directory(self.config))
        self.configuration = self.stack.enter_context(r.cf.admission.acquire(
            self.conf, self.web, self.account.pw_gid, external=self.external))
        self.control = self.controller()

    def test_real_sigkill_after_release_unlink_reacquires_exact_lease(self):
        self.planned(); self.stack.close(); self.access.close(); self.lease.close()
        child = os.fork()
        if child == 0:
            try:
                self.reopen_handles(); unlink = os.unlink
                def cut(name, *args, **kwargs):
                    unlink(name, *args, **kwargs)
                    if name == r.cf.RELEASE: os.kill(os.getpid(), signal.SIGKILL)
                with patch.object(r.os, 'unlink', side_effect=cut): self.execute()
            except BaseException: os._exit(90)
            os._exit(91)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), -signal.SIGKILL)
        self.reopen_handles()
        self.assertTrue((self.scope.directory / r.cf.MARKER).exists())
        self.assertFalse((self.scope.directory / r.cf.RELEASE).exists())
        self.assert_done(self.execute('resume'))


class ReopenJournalScopeTests(unittest.TestCase):
    """Private journal routing only; immutable inode release remains Ext4 CI."""
    def setUp(self):
        from tempfile import TemporaryDirectory
        self.temp = TemporaryDirectory(prefix='hestia-reopen-journals-', dir='/var/lib')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = SimpleNamespace(directory=self.root, instance='a' * 32)
        self.backups = self.root / 'backups'; self.backups.mkdir(mode=0o700)

    def controller(self, lease_id):
        lease = SimpleNamespace(scope=self.scope, lease_id=lease_id)
        http = object()
        barrier = object.__new__(r.hd.HttpDrainLease)
        barrier._lease = lease; barrier._drain = SimpleNamespace(runtime=http)
        data = object.__new__(r.da.DataAccessFence); data._lease = lease; data._runtime = http
        external = object.__new__(r.ef.ExternalFence); external._lease = lease; external._raw = b'fixture'
        configuration = object.__new__(r.cf.admission.ConfigurationLease)
        configuration._external = (lease, external._raw)
        gateway = object.__new__(r.GatewayServiceRuntime); gateway.web = http
        return r.ReopenFilesPlan(barrier, data, configuration, external, gateway, self.backups)

    def profile(self, lease_id):
        return {'version': 1, 'instance': self.scope.instance, 'lease_id': lease_id,
            'service_profile_sha256': 'b' * 64, 'gateway_release_sha256': 'c' * 64,
            'backup_root': str(self.backups), 'web_backup': {},
            'journals': {name: 'd' * 64 for name in (*r.ROLES, 'data-access', 'external')}}

    def legacy(self, raw):
        root = self.root / 'mobile-reopen-files'; root.mkdir(mode=0o700)
        if raw is not None:
            (root / 'profile.json').write_bytes(raw); (root / 'profile.json').chmod(0o600)
        return root

    def test_three_leases_have_distinct_private_journals_and_preserve_previous_bytes(self):
        snapshots = {}
        for lease_id in ('1' * 32, '2' * 32, '3' * 32):
            control = self.controller(lease_id)
            self.assertEqual(control.root.name, 'mobile-reopen-files-' + lease_id)
            self.assertIsNone(control.profile()); self.assertIsNone(control.journal.read())
            control._save('profile.json', canonical_bytes(self.profile(lease_id)))
            planned = control.engine().plan(mode='upgrade')
            self.assertTrue(all(row['state'] == 'PLANNED' for row in planned['steps']))
            self.assertEqual(control.profile()['lease_id'], lease_id)
            for path, raw in snapshots.items(): self.assertEqual(path.read_bytes(), raw)
            snapshots.update({path: path.read_bytes() for path in control.root.rglob('*') if path.is_file()})
            self.assertEqual(self.controller(lease_id).journal.read(), planned)

    def test_original_legacy_lease_keeps_its_exact_path_without_rewriting(self):
        lease_id = '1' * 32; raw = canonical_bytes(self.profile(lease_id)); legacy = self.legacy(raw)
        control = self.controller(lease_id)
        self.assertEqual(control.root, legacy); self.assertEqual(control.profile(), self.profile(lease_id))
        self.assertEqual((legacy / 'profile.json').read_bytes(), raw)
        self.assertFalse((self.root / ('mobile-reopen-files-' + lease_id)).exists())

    def test_foreign_legacy_lease_is_retained_but_never_adopted_by_next_cycle(self):
        raw = canonical_bytes(self.profile('1' * 32)); legacy = self.legacy(raw)
        next_plan = self.controller('2' * 32)
        self.assertNotEqual(next_plan.root, legacy); self.assertIsNone(next_plan.profile())
        self.assertEqual((legacy / 'profile.json').read_bytes(), raw)

    def test_partial_or_ambiguous_legacy_evidence_is_refused_without_new_journal(self):
        legacy = self.legacy(None)
        with self.assertRaises(InstallerError): self.controller('1' * 32)
        profile = legacy / 'profile.json'; profile.write_bytes(canonical_bytes(self.profile('1' * 32))); profile.chmod(0o600)
        scoped = self.root / ('mobile-reopen-files-' + '1' * 32); scoped.mkdir(mode=0o700)
        with self.assertRaises(Exception): self.controller('1' * 32)
        self.assertEqual(list(scoped.iterdir()), [])

    def test_private_profile_permissions_and_foreign_instance_fail_closed(self):
        legacy = self.legacy(canonical_bytes(self.profile('1' * 32)))
        profile = legacy / 'profile.json'; profile.chmod(0o644)
        with self.assertRaises(Exception): self.controller('2' * 32)
        profile.chmod(0o600); value = self.profile('1' * 32); value['instance'] = 'f' * 32
        profile.write_bytes(canonical_bytes(value))
        with self.assertRaises(InstallerError): self.controller('2' * 32)
        self.assertFalse((self.root / ('mobile-reopen-files-' + '2' * 32)).exists())

    def test_current_profile_cannot_import_another_lease_or_backup_root(self):
        control = self.controller('2' * 32)
        for changes in ({'lease_id': '1' * 32}, {'backup_root': str(self.root / 'foreign')}):
            value = {**self.profile('2' * 32), **changes}
            control.root.mkdir(mode=0o700, exist_ok=True)
            path = control.root / 'profile.json'; path.write_bytes(canonical_bytes(value)); path.chmod(0o600)
            with self.subTest(changes=changes), self.assertRaises(InstallerError): control.profile()

    def test_lease_path_grammar_rejects_traversal_before_creating_anything(self):
        before = set(self.root.iterdir())
        for value in ('../foreign', 'A' * 32, '1' * 31, None, True):
            with self.subTest(value=value), self.assertRaises(InstallerError): self.controller(value)
        self.assertEqual(set(self.root.iterdir()), before)


if __name__ == '__main__': unittest.main()
