"""Exclusive configuration seam and native file observations; disposable Ext4 CI."""
import os
import pickle
import unittest
from unittest.mock import patch

from installer import mobile_external_admission as b
from installer.model import InstallerError
import test_mobile_reopen_external_files as fixture


class ExternalAdmissionFilesTests(unittest.TestCase):
    fixture_root = fixture.ExternalReleaseFilesTests.fixture_root
    mount = fixture.ExternalReleaseFilesTests.mount
    controller = fixture.ExternalReleaseFilesTests.controller
    write = staticmethod(fixture.ExternalReleaseFilesTests.write)
    close_handles = fixture.ExternalReleaseFilesTests.close_handles
    setUp = fixture.ExternalReleaseFilesTests.setUp
    plan = fixture.ExternalReleaseFilesTests.plan
    assert_closed = fixture.ExternalReleaseFilesTests.assert_closed

    def view(self):
        return b._ParentFiles(self.plan_control, self.barrier, self.access, self.gateway)

    def test_locked_composition_preserves_old_journal_and_reacquires_real_configuration(self):
        plan = self.plan(); view = self.view()
        document = view.journal.read(); view.engine()
        removed, added = b.a._journal_changes(view, document)
        self.assertEqual(added[self.control.root.name + '/transaction/state.json'],
                         (self.control.root / 'transaction/state.json').read_bytes())
        with plan._configuration() as locked:
            view.live(locked=locked)
            plan._execute_locked('apply', plan.plan_sha256, confirmed=True, locked=locked)
            view.live(locked=locked)
            b._external_state(plan, completed=True)
        with b.fs._directory(self.config) as conf:
            with b.r.cf.admission.acquire(conf, self.web, self.account.pw_gid) as configuration:
                self.assertIsNone(configuration._external)
                view.live(configuration=configuration)
        self.assert_closed()

    def test_closed_and_foreign_exclusive_guards_cannot_authorize_effect(self):
        plan = self.plan()
        with plan._configuration() as locked: locked.assert_held()
        with self.assertRaises(InstallerError):
            plan._execute_locked('apply', plan.plan_sha256, confirmed=True, locked=locked)
        with self.assertRaises(InstallerError):
            plan._execute_locked('apply', plan.plan_sha256, confirmed=True, locked=object())
        self.assertFalse((plan.root / 'intent.json').exists()); self.external.assert_held()

    def test_exclusive_guard_is_process_bound_and_not_serializable(self):
        plan = self.plan()
        with plan._configuration() as locked:
            with self.assertRaises(TypeError): pickle.dumps(locked)
            with patch.object(b.e.os, 'getpid', return_value=os.getpid() + 1), self.assertRaises(InstallerError):
                locked.assert_held()
        self.external.assert_held(); self.assert_closed()

    def test_non_settings_configuration_drift_is_rejected_before_effect(self):
        plan = self.plan(); view = self.view(); path = self.config / 'database.json'
        raw = path.read_bytes(); path.write_bytes(b'foreign')
        try:
            with plan._configuration() as locked, self.assertRaises(b.AdmissionError): view.live(locked=locked)
            self.assertFalse((plan.root / 'intent.json').exists())
        finally: path.write_bytes(raw)
        self.external.assert_held(); self.assert_closed()

    def test_partial_native_journal_is_observed_without_repair(self):
        plan = self.plan(); view = self.view(); unlink = os.unlink
        def cut(name, *args, **kwargs):
            unlink(name, *args, **kwargs)
            if name == b.e.ef.MARKER: raise OSError('lost marker removal response')
        with patch.object(b.e.os, 'unlink', side_effect=cut), self.assertRaises(Exception):
            plan.execute('apply', plan.plan_sha256, confirmed=True)
        before = {p: p.read_bytes() for p in plan.root.iterdir()}
        raw, marker, release = b._external_state(plan)
        self.assertIsNone(marker); self.assertEqual(raw, release)
        with plan._configuration() as locked: view.live(locked=locked)
        self.assertEqual(before, {p: p.read_bytes() for p in plan.root.iterdir()})
        with self.assertRaises(b.AdmissionError): b._external_state(plan, completed=True)
        self.assert_closed()

    def test_missing_outer_intent_never_admits_missing_native_reservations(self):
        plan = self.plan(); self.external.unseal(confirmed=True)
        with self.assertRaisesRegex(b.AdmissionError, 'MOBILE_EXTERNAL_INTENT_REQUIRED'): b._external_state(plan)
        self.assertFalse((plan.root / 'intent.json').exists()); self.assert_closed()


if __name__ == '__main__': unittest.main()
