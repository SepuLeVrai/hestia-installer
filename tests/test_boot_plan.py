"""Boot confirmation/recovery contracts. Local effects are files and mocks only."""
from contextlib import ExitStack
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import boot_runtime as b, boot_plan as bp
from installer.model import InstallerError, Receipt, canonical_bytes, build_plan, initial_document, aggregate
from installer.operations import OperationContext, SecretVault
from github_fixture import confirm
import test_application_activation as fixture


class BootPlanTests(unittest.TestCase):
    setUp = fixture.ActivationPlanTests.setUp
    save = fixture.ActivationPlanTests.save
    plan = fixture.ActivationPlanTests.plan
    completed = fixture.ActivationPlanTests.completed
    activation_plan = fixture.ActivationPlanTests.activation_plan
    restart = fixture.ActivationPlanTests.restart

    def boot_plan(self):
        parent, active = self.activation_plan()
        active.update(state='DONE', approved_plan_sha256=active['plan_sha256'], revision=1)
        for record in active['steps']: record.update(state='DONE', phase='done', attempts=1)
        with self.service.activation.journal.locked() as locked: locked.write(active, expected_revision=0)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(bp.native.sql.MariaDB, 'probe'))
        stack.enter_context(patch.object(bp.native.activation.Activation, 'serving'))
        from installer.mariadb_plan import MariaDBPlan
        stack.enter_context(patch.object(MariaDBPlan, 'assert_ready'))
        stack.enter_context(patch.object(MariaDBPlan, 'engine', return_value=SimpleNamespace(report=lambda: {'state': 'DONE', 'plan_sha256': 'c' * 64})))
        stack.enter_context(patch.object(MariaDBPlan, 'profile', return_value={'version': 1, 'instance': 'a' * 32, 'packages_sha256': 'b' * 64}))
        with patch.object(b.BootRuntime, 'absent'):
            doc = self.service.execute('boot.plan', {'activation_sha256': active['plan_sha256']})['boot']['installation']
        return parent, active, doc

    def test_get_report_restart_are_file_only(self):
        with patch.object(b.BootRuntime, 'live', side_effect=AssertionError('host')):
            self.assertEqual(self.restart().wizard_state()['boot'], {'installation': None, 'availability': None})
            self.assertNotIn('boot', self.service.report())
        self.assertFalse(self.service.boot.root.exists())

    def test_requires_completed_activation(self):
        parent = self.completed()
        with self.assertRaises(InstallerError): self.service.execute('boot.plan', {'activation_sha256': parent['plan_sha256']})
        self.assertFalse(self.service.boot.root.exists())

    def test_separate_confirmation_and_closed_plan_preserve_parents(self):
        parent, active, doc = self.boot_plan()
        self.assertEqual(len(doc['plan']['steps']), 2)
        self.assertIsNone(doc['approved_plan_sha256'])
        self.assertEqual(self.service.engine.report(), parent)
        self.assertEqual(self.service.activation.state()['installation'], active)
        with patch.object(b.BootRuntime, 'live', side_effect=AssertionError('host')):
            with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
                self.service.execute('boot.apply', confirm(active))
            with self.assertRaises(InstallerError): self.service.execute('boot.plan', {'activation_sha256': active['plan_sha256'], 'unit': 'nginx.service'})

    def test_restart_and_repeated_plan_are_readonly(self):
        _, active, doc = self.boot_plan()
        with patch.object(b.BootRuntime, 'absent', side_effect=AssertionError('host')):
            other = self.restart()
            self.assertEqual(other.execute('boot.plan', {'activation_sha256': active['plan_sha256']})['boot']['installation'], doc)
        self.assertEqual(other.report()['boot']['installation'], doc)

    def test_partial_registry_and_modified_profile_cannot_resume(self):
        parent, _, _ = self.boot_plan(); engine, _ = self.service.boot.engine(parent)
        path = self.service.boot.journal.path; before = path.read_bytes()
        path.write_bytes(canonical_bytes(initial_document(build_plan(engine.registry.specs()[:1]))))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.service.boot.engine(parent)
        path.write_bytes(before)
        profile = self.service.boot._read('profile.json'); profile['parents']['sql'] = '0' * 64
        (self.service.boot.root / 'profile.json').write_bytes(canonical_bytes(profile))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.service.boot.engine(parent)

    def test_main_lock_serializes_boot(self):
        _, _, doc = self.boot_plan()
        with self.service.engine.journal.locked():
            with self.assertRaisesRegex(InstallerError, 'BUSY'): self.service.execute('boot.apply', confirm(doc))

    def test_failed_current_check_preserves_done(self):
        _, _, doc = self.boot_plan()
        doc.update(state='DONE', approved_plan_sha256=doc['plan_sha256'], revision=1)
        for spec, record in zip(doc['plan']['steps'], doc['steps']):
            record.update(state='DONE', phase='done', attempts=1, evidence=Receipt(created_resources=tuple(r['name'] for r in spec['resources'])).as_dict())
        doc.update(aggregate(doc))
        with self.service.boot.journal.locked() as locked: locked.write(doc, expected_revision=0)
        with patch.object(b.BootRuntime, 'configuration', side_effect=OSError('private')):
            result = self.service.execute('boot.check', confirm(doc))['boot']
        self.assertEqual(result['installation'], doc)
        self.assertEqual(result['availability']['state'], 'BOOT_CHECK_FAILED')
        self.assertIsNone(self.restart().boot.state()['availability'])


class BootRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='hestia-boot-', dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.units = root / 'units'; self.units.mkdir(); (self.units / 'multi-user.target.wants').mkdir()
        self.enterContext(patch.object(b.h.drain, 'UNIT_ROOT', self.units))
        self.enterContext(patch.object(b.h, '_unit_absent'))
        self.command = self.enterContext(patch.object(b.h, '_command'))
        profile = {'version': 1, 'application': {'instance': 'a' * 32, 'configuration': {'web': b.app.FreshProfile('a' * 32).web('hestia.example.test')}},
            'sql': {'instance': 'b' * 32, 'packages_sha256': 'c' * 64},
            'parents': {'preparation': 'd' * 64}, 'code': {n: b.f._sha(v) for n, v in b.code_files().items()}}
        self.runtime = b.BootRuntime(profile); self.runtime.root = root / 'boot'
        self.enterContext(patch.object(self.runtime, 'live'))
        self.stage = b.BootOperation(self.runtime, 'stage'); self.enable = b.BootOperation(self.runtime, 'enable')
        self.context = lambda op: OperationContext('boot-test', op.spec.as_dict(), Receipt().as_dict(), SecretVault())

    def stage_now(self): self.stage.apply(self.context(self.stage))

    def test_only_owned_dependencies_no_start_and_private_bundle(self):
        self.stage_now(); self.enable.apply(self.context(self.enable))
        self.assertEqual([call.args[0][-1] for call in self.command.call_args_list], ['daemon-reload', 'daemon-reload'])
        self.assertEqual(os.readlink(self.runtime.link), '../' + self.runtime.target)
        self.assertEqual((self.runtime.root / 'worker.py').stat().st_mode & 0o777, 0o600)
        self.runtime.configuration()
        self.assertEqual(len(list(self.units.glob('*.requires'))), 4)

    def test_lost_complete_stage_and_enable_reply_recover_without_effect(self):
        self.stage_now(); self.enable.apply(self.context(self.enable)); self.command.reset_mock()
        for op in (self.stage, self.enable):
            self.assertEqual(op.recover(self.context(op), 'apply').decision, b.RecoveryDecision.APPLIED)
        self.command.assert_not_called()

    def test_partial_enable_receipt_stays_manual_even_with_correct_link(self):
        self.stage_now(); original = self.runtime._write
        def write(name, value):
            if name == 'enabled.json': raise OSError('lost persistence')
            return original(name, value)
        with patch.object(self.runtime, '_write', side_effect=write):
            with self.assertRaises(OSError): self.enable.apply(self.context(self.enable))
        self.assertTrue(self.runtime.link.is_symlink()); self.command.reset_mock()
        self.assertEqual(self.enable.recover(self.context(self.enable), 'apply').decision, b.RecoveryDecision.MANUAL)
        self.command.assert_not_called()

    def test_existing_or_redirected_enrollment_is_never_adopted(self):
        self.runtime.link.symlink_to('../other.target')
        with self.assertRaises(Exception): self.stage.prepare(self.context(self.stage))
        self.assertEqual(os.readlink(self.runtime.link), '../other.target')
        self.assertFalse(self.runtime.root.exists())

    def test_unit_code_and_dependency_drift_are_refused(self):
        self.stage_now()
        paths = [self.units / self.runtime.target, self.runtime.root / 'code/installer/boot_runtime.py']
        for path in paths:
            original = path.read_bytes(); path.write_bytes(original + b'\n# drift\n')
            with self.assertRaises(Exception): self.runtime.configuration()
            path.write_bytes(original)
        link = self.units / (self.runtime.sql.unit + '.requires') / self.runtime.guard
        link.unlink(); link.symlink_to('../other.service')
        self.assertEqual(self.stage.recover(self.context(self.stage), 'apply').decision, b.RecoveryDecision.MANUAL)

    def test_boot_guard_never_uses_partial_enable(self):
        self.stage_now()
        with patch.object(self.runtime.sql, 'configuration', side_effect=AssertionError('host')):
            with self.assertRaises(InstallerError): self.runtime.boot('sql')

    def test_boot_web_guard_keeps_maintenance_closed(self):
        self.stage_now(); self.enable.apply(self.context(self.enable))
        scope = SimpleNamespace(observe=lambda: {'state': 'MAINTENANCE'})
        with patch.object(self.runtime.sql, 'configuration'), patch.object(self.runtime.sql, 'probe'), \
             patch.object(self.runtime.http, '_inspect_configuration', return_value=(None, None, None, {})), \
             patch.object(self.runtime.activation.cleaner, '_inspect_configuration'), patch.object(self.runtime.http, '_scope', return_value=scope):
            with self.assertRaisesRegex(InstallerError, 'MANUAL_ACTION_REQUIRED'): self.runtime.boot('web')
        self.assertEqual(self.command.call_count, 2)
