"""Ext4/inode/configuration observations in disposable CI; SQL/native audits isolated."""
import os
import unittest
from unittest.mock import patch

from installer import mobile_data_admission as m
from installer.model import InstallerError
import test_mobile_reopen_data_files as prior


class DataAdmissionFilesTests(unittest.TestCase):
    fixture_root = prior.DataReleaseFilesTests.fixture_root
    mount = prior.DataReleaseFilesTests.mount
    controller = prior.DataReleaseFilesTests.controller
    write = staticmethod(prior.DataReleaseFilesTests.write)
    close_handles = prior.DataReleaseFilesTests.close_handles
    setUp = prior.DataReleaseFilesTests.setUp
    plan = prior.DataReleaseFilesTests.plan
    run_release = prior.DataReleaseFilesTests.run_release
    assert_activity_closed = prior.DataReleaseFilesTests.assert_activity_closed
    interrupt_after_chmod = prior.DataReleaseFilesTests.interrupt_after_chmod
    foreign_process = prior.DataReleaseFilesTests.foreign_process

    def view(self):
        return m._ParentFiles(self.plan_control, self.barrier, self.gateway)

    def test_native_inventories_remain_exact_through_locked_reopen(self):
        plan = self.plan(); view = self.view(); before = m.e._parents(self.lease)
        view.engine(); removed, added = m.a._journal_changes(view, view.journal.read())
        self.assertEqual(added[self.control.root.name + '/transaction/state.json'],
                         (self.control.root / 'transaction/state.json').read_bytes())
        with view.external._configuration() as locked:
            view.live(locked=locked)
            result = plan._execute_locked('apply', plan.plan_sha256, confirmed=True, locked=locked)
            self.assertTrue(result['data_access_reopened'])
            with patch.object(m.r.inf, '_walk', side_effect=AssertionError('closed lease reader reused')):
                view.live(locked=locked)
            with self.assertRaises(m.d.da.DataAccessError): self.access.assert_held()
        self.assertEqual(m.e._parents(self.lease), before); self.assert_activity_closed()
        self.assertNotIsInstance(view.data, m.d.da.DataAccessFence)

    def test_explicit_partial_preparation_recloses_before_live_observation(self):
        self.interrupt_after_chmod(); plan = self.plan_control; view = self.view()
        with view.external._configuration() as locked:
            with self.assertRaisesRegex(m.AdmissionError, 'MOBILE_DATA_RECLOSE_REQUIRED'): view.live(locked=locked)
            self.assertTrue(m._prepare(plan, 'resume', locked))
            self.assertEqual(self.data.stat().st_mode & 0o777, 0o700)
            view.live(locked=locked)
            plan._execute_locked('resume', plan.plan_sha256, confirmed=True, locked=locked)
            view.live(locked=locked)
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750); self.assert_activity_closed()

    def test_partial_without_intent_cannot_be_implicitly_reclosed(self):
        plan = self.plan(); view = self.view(); self.data.chmod(0o750)
        with view.external._configuration() as locked:
            for action in ('apply', 'resume', 'check'):
                with self.assertRaises(m.AdmissionError): m._prepare(plan, action, locked)
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        self.assertFalse((plan.root / 'intent.json').exists()); self.data.chmod(0o700)

    def test_native_open_without_intent_cannot_be_adopted(self):
        self.plan(); view = self.view(); self.access.reopen(confirmed=True)
        with view.external._configuration() as locked, self.assertRaises(m.AdmissionError): view.live(locked=locked)
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750); self.assert_activity_closed()

    def test_closed_or_foreign_guard_cannot_authorize_data_effect(self):
        plan = self.plan(); view = self.view()
        with view.external._configuration() as locked: locked.assert_held()
        for guard in (locked, object()):
            with self.assertRaises(InstallerError):
                plan._execute_locked('apply', plan.plan_sha256, confirmed=True, locked=guard)
        self.access.assert_held(); self.assertFalse((plan.root / 'intent.json').exists())

    def test_only_root_mode_transition_is_allowed_not_descendant_drift(self):
        self.plan(); self.run_release(); view = self.view(); path = self.data / 'uploads'
        original = path.stat().st_mode & 0o777; path.chmod(0o750)
        try:
            with view.external._configuration() as locked, self.assertRaises(m.AdmissionError): view.live(locked=locked)
            self.assertEqual(path.stat().st_mode & 0o777, 0o750)
        finally: path.chmod(original)
        with view.external._configuration() as locked: view.live(locked=locked)
        self.assert_activity_closed()

    def test_links_in_open_tree_are_refused_without_cleanup(self):
        self.plan(); self.run_release(); view = self.view(); alias = self.data / 'uploads/alias'
        for link in ('symlink', 'hardlink'):
            if link == 'symlink': alias.symlink_to(self.data / 'uploads/payload')
            else: os.link(self.data / 'uploads/payload', alias)
            try:
                with view.external._configuration() as locked, self.assertRaises(Exception): view.live(locked=locked)
                self.assertTrue(alias.is_symlink() or alias.exists())
            finally: alias.unlink()
        self.assert_activity_closed()

    def test_nested_mount_in_open_tree_is_refused(self):
        self.plan(); self.run_release(); view = self.view()
        self.mount(self.data / 'uploads', self.data / 'imports')
        with view.external._configuration() as locked, self.assertRaises(Exception): view.live(locked=locked)
        self.assert_activity_closed()

    def test_non_settings_configuration_drift_after_open_is_refused(self):
        self.plan(); self.run_release(); view = self.view(); path = self.config / 'database.json'
        raw = path.read_bytes(); path.write_bytes(b'changed')
        try:
            with view.external._configuration() as locked, self.assertRaises(m.AdmissionError): view.live(locked=locked)
            self.assertEqual(path.read_bytes(), b'changed')
        finally: path.write_bytes(raw)
        self.assert_activity_closed()

    def test_partial_preparation_recloses_and_refuses_foreign_process(self):
        self.interrupt_after_chmod(); plan = self.plan_control; view = self.view(); proc = self.foreign_process()
        with view.external._configuration() as locked, self.assertRaises(m.d.da.DataAccessError):
            m._prepare(plan, 'resume', locked)
        self.assertIsNone(proc.poll()); self.assertEqual(self.data.stat().st_mode & 0o777, 0o700)
        self.assertFalse((plan.root / 'released.json').exists()); self.assert_activity_closed()


if __name__ == '__main__': unittest.main()
