"""Journal contract tests with disposable files, no SQL or service mutation.

Controller boundaries are substituted here; actual controllers are exercised by
application_journal_systemd.py in an explicitly disposable systemd environment.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer import application_operations as a
from installer.engine import TransactionEngine
from installer.model import ErrorCode, InstallerError
from installer.operations import OperationContext, OperationRegistry, RecoveryDecision, SecretVault
from installer.transaction import StateJournal
from installer.service import TransactionService
from web_configuration_fixture import request


class Crash(BaseException): pass


class ApplicationJournalTests(unittest.TestCase):
    def setUp(self):
        # The controller filesystem deliberately rejects sticky/writable /tmp.
        # No account, SQL or service is created in this protected file fixture.
        self.temp = tempfile.TemporaryDirectory(prefix='hestia-operator-test-', dir='/var/lib'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = SecretVault()
        self.runtime = a.db.p.PhpRuntime(Path('/usr/bin/php8.4'), Path('/usr/lib/php/20240924'),
            987, 987, self.root / 'run', self.root / 'private')
        self.payload = request(); self.payload['database'].update(mode='existing_local', host='127.0.0.1', tls_ca_file=None)
        self.inputs = a.WebInputs.capture(self.payload, self.vault)
        for name, value in {'migration_user': 'hestia_setup', 'migration_password': 'synthetic-migration-secret',
                            'authority_user': 'hestia_authority', 'authority_password': 'synthetic-authority-secret'}.items():
            self.vault.put('web.' + name, value)
        self.journal = StateJournal(self.root / 'journal/state.json')
        self.trace = []

    def patch(self, *args, **kwargs):
        p = patch.object(*args, **kwargs); value = p.start(); self.addCleanup(p.stop); return value

    def engine(self, *ops, hook=None, vault=None):
        return TransactionEngine(self.journal, OperationRegistry(tuple(ops)),
                                 secrets=self.vault if vault is None else vault, fault_hook=hook)

    def context(self, op, plan, evidence=None):
        return OperationContext(plan['installation_id'], op.spec.as_dict(), evidence or {}, self.vault)

    def crash_after(self, name, phase='apply'):
        def hook(n, p, event):
            self.trace.append((n, p, event))
            if (n, p, event) == (name, phase, 'after'): raise Crash()
        return hook

    def fresh(self, *, final=False):
        controller = (a.f.FinalizationStep(self.runtime, self.root / 'source', repository=a.db.p.WEB_REPOSITORY, commit=a.f.WEB_COMMIT)
                      if final else a.db.DatabaseStep(self.runtime, self.root / 'source', repository=a.db.p.WEB_REPOSITORY, commit=a.db.WEB_COMMIT))
        cls = a.FinalizationOperation if final else a.DatabasePreparationOperation
        op = cls(controller, self.inputs, config_root=self.root / 'config', dependencies=())
        marker = self.root / ('final-result' if final else 'sql-result')
        state = 'WEB_FRESH_FINALIZED' if final else 'DATABASE_CONFIGURATION_READY'
        def preflight(context):
            self.inputs.payload(context)
            if not final: op._credentials(context)
            a.require(not marker.exists())
            return self.inputs.payload(context) if final else (self.inputs.payload(context), *op._credentials(context))
        def mutate(*args, **kwargs):
            self.trace.append(('MUTATE', op.spec.name)); marker.write_text(state)
            return {'state': state}
        def observe(context):
            self.inputs.payload(context, existing=final)
            a.require(marker.read_text() == state)
            return {'state': state, 'application_installed': False}
        self.patch(op, '_preflight', side_effect=preflight)
        self.patch(op, '_observe', side_effect=observe)
        self.patch(controller, 'finalize' if final else 'prepare', side_effect=mutate)
        return op, marker

    def upgrade(self):
        payload = request('upgrade'); payload['database'].update(mode='existing_local', host='127.0.0.1', tls_ca_file=None)
        payload['web']['service_user'] = 'hestia_fixture'
        inputs = a.WebInputs.capture(payload, self.vault)
        spec = a.u.h.RuntimeSpec('a' * 32, Path('/var/lib/hestia-operator-runtime'),
            Path(payload['web']['webroot']), 'hestia_fixture', payload['web']['hostname'], 18080, '8.4',
            maintenance_directory=Path('/var/lib/hestia-operator-config/slot/maintenance'))
        http = a.u.h.HttpRuntime(spec); cleaner = a.u.cleaner.SessionCleaner(http)
        controller = a.u.StorageUpgrade(self.runtime, Path('/var/lib/source'), Path('/var/lib/target'), http, cleaner)
        backups = self.root / 'backups'; backups.mkdir(mode=0o700)
        op = a.StorageUpgradeOperation(controller, inputs, config_root=spec.maintenance_directory.parent.parent, backup_root=backups)
        lease = 'b' * 32; slot = backups / ('upgrade-' + lease)
        self.slot, self.upgrade_op = slot, op
        def prepare(context):
            inputs.payload(context)
            a.require(not op._bound(context) and not list(backups.iterdir()))
        def apply(*args, **kwargs):
            self.trace.append(('MUTATE', 'upgrade-apply'))
            slot.mkdir(mode=0o700)
            (slot / 'attempt.json').write_text('{}')
            return write('STORAGE_UPGRADE_APPLIED_GATED')
        def write(state):
            result = {'state': state, 'lease_id': lease, 'application_installed': False}
            if state == 'STORAGE_UPGRADE_RESUME_AUTHORIZED':
                result.update(selected_commit=a.u.STORAGE_COMMIT, services_started=False, rollback_requires_new_assessment=True)
            (slot / 'result.json').write_text(json.dumps(result))
            return result
        def observe(*args):
            return json.loads((slot / 'result.json').read_text()) if (slot / 'result.json').exists() else {
                'state': 'STORAGE_UPGRADE_RECOVERY_REQUIRED', 'lease_id': lease, 'application_installed': False}
        def recover(*args, direction, **kwargs):
            self.trace.append(('MUTATE', 'upgrade-recover-' + direction))
            a.require(not (slot / 'resume-intent.json').exists())
            if direction == 'rollback': (slot / 'rollback-intent.json').write_text('{}')
            return write('STORAGE_UPGRADE_ROLLED_BACK_GATED' if direction == 'rollback' else 'STORAGE_UPGRADE_APPLIED_GATED')
        def authorize(*args, **kwargs):
            self.trace.append(('MUTATE', 'authorize'))
            (slot / 'resume-intent.json').write_text('{}')
            return write('STORAGE_UPGRADE_RESUME_AUTHORIZED')
        self.patch(op, 'prepare', side_effect=prepare)
        self.patch(controller, 'apply', side_effect=apply)
        self.patch(controller, 'observe', side_effect=observe)
        self.patch(controller, 'recover', side_effect=recover)
        self.patch(controller, 'authorize_resume', side_effect=authorize)
        self.patch(controller.target_http, 'observe', return_value={})
        self.patch(controller.target_collector, 'observe', return_value={})
        def validate_binding(*args): a.require((slot / 'attempt.json').read_text() == '{}')
        self.patch(a.recovery, 'context', side_effect=validate_binding)
        return op, write

    def test_inputs_hold_no_secrets_and_do_not_mutate_the_callers_payload(self):
        value = deepcopy(self.payload); inputs = a.WebInputs.capture(value, self.vault)
        self.assertEqual(value, self.payload)
        for secret in value['secrets'].values():
            if secret: self.assertNotIn(secret, repr(inputs.__dict__))
        copy = inputs.configuration; copy['web']['hostname'] = 'changed.example.test'
        self.assertNotEqual(copy, inputs.configuration)

    def test_public_configuration_digest_changes_with_admin_and_assistant_choices(self):
        value = deepcopy(self.payload); value['administrator']['first_name'] = 'Élise 漢字🙂'
        other = a.WebInputs.capture(value, self.vault)
        self.assertNotEqual(self.inputs.sha256, other.sha256)
        value['assistant']['action'] = 'configure'; value['secrets']['openai_api_key'] = 'synthetic-openai-value-1234567890'
        enabled = a.WebInputs.capture(value, self.vault)
        self.assertIn('web.openai_api_key', enabled.secret_names())

    def test_reconstructed_inputs_require_new_credentials(self):
        op, _ = self.fresh(); engine = self.engine(op, vault=SecretVault()); plan = engine.plan(mode='fresh')
        state = engine.apply(plan['plan_sha256'])
        self.assertEqual(state['last_error_redacted'], 'SECRET_REQUIRED')
        self.assertFalse(self.runtime.state_root.exists())

    def test_plan_is_read_only_and_contains_no_controller_or_credential_values(self):
        op, _ = self.fresh(); plan = self.engine(op).dry_run(mode='fresh')
        self.assertFalse(self.runtime.state_root.exists()); self.assertFalse(self.journal.path.exists())
        raw = json.dumps(plan)
        for secret in self.payload['secrets'].values():
            if secret: self.assertNotIn(secret, raw)
        self.assertIn(self.inputs.sha256, raw)

    def test_sql_and_finalization_complete_without_claiming_application_activation(self):
        database, _ = self.fresh(); final, _ = self.fresh(final=True)
        final.spec = replace(final.spec, dependencies=(database.spec.name,))
        engine = self.engine(database, final); plan = engine.plan(mode='fresh')
        state = engine.apply(plan['plan_sha256'])
        self.assertEqual(state['state'], 'DONE')
        self.assertEqual([x['attempts'] for x in state['steps']], [1, 1])
        self.assertNotIn('application_installed', state)
        self.assertEqual(state, engine.resume(plan['plan_sha256']))
        self.assertEqual(len([x for x in self.trace if x[0] == 'MUTATE']), 2)

    def test_database_lost_response_is_recognized_without_a_second_fresh(self):
        op, _ = self.fresh(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='fresh')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        resumed = self.engine(op).resume(plan['plan_sha256'])
        self.assertEqual(resumed['state'], 'DONE')
        self.assertEqual(self.trace.count(('MUTATE', op.spec.name)), 1)

    def test_finalization_lost_response_only_needs_database_secret_for_observation(self):
        op, _ = self.fresh(final=True); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='fresh')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        vault = SecretVault(); vault.put('web.database_password', self.payload['secrets']['database_password'])
        self.assertEqual(self.engine(op, vault=vault).resume(plan['plan_sha256'])['state'], 'DONE')
        self.assertEqual(self.trace.count(('MUTATE', op.spec.name)), 1)

    def test_missing_private_result_is_manual_and_never_replays_sql(self):
        op, marker = self.fresh(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='fresh')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        marker.unlink()
        self.assertEqual(self.engine(op).resume(plan['plan_sha256'])['state'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(self.trace.count(('MUTATE', op.spec.name)), 1)

    def test_bound_intent_without_native_receipt_is_conservatively_manual(self):
        op, _ = self.fresh(); engine = self.engine(op); plan = engine.plan(mode='fresh'); context = self.context(op, plan)
        op._bind(context)
        self.assertEqual(op.recover(context, 'apply').decision, RecoveryDecision.MANUAL)
        self.assertFalse(self.trace)

    def test_binding_cannot_be_used_with_changed_configuration(self):
        op, _ = self.fresh(); plan = self.engine(op).plan(mode='fresh'); context = self.context(op, plan); op._bind(context)
        value = self.inputs.configuration; value['web']['hostname'] = 'other.example.test'
        op.inputs = a.WebInputs(value)
        with self.assertRaises(InstallerError): op._bound(context)

    def test_binding_refuses_corruption_hardlinks_and_symlinks(self):
        op, _ = self.fresh(); plan = self.engine(op).plan(mode='fresh'); context = self.context(op, plan); op._bind(context)
        file = self.runtime.state_root / op._name(context); raw = file.read_bytes()
        for kind in ('corrupt', 'hardlink', 'symlink', 'permissions'):
            with self.subTest(kind=kind):
                if kind == 'corrupt': file.write_bytes(raw + b' ')
                elif kind == 'hardlink': (self.root / 'linked').hardlink_to(file)
                elif kind == 'symlink': file.unlink(); file.symlink_to(self.root / 'linked')
                else: file.chmod(0o644)
                with self.assertRaises((InstallerError, OSError)): op._bound(context)
                file.unlink(); file.write_bytes(raw); file.chmod(0o600)

    def test_fresh_rollback_is_refused_without_deleting_resources(self):
        op, marker = self.fresh(); engine = self.engine(op); plan = engine.plan(mode='fresh'); engine.apply(plan['plan_sha256'])
        with self.assertRaisesRegex(InstallerError, 'ROLLBACK_UNSUPPORTED'): engine.rollback(op.spec.boundary, plan['plan_sha256'])
        self.assertTrue(marker.exists())

    def test_read_only_report_and_refresh_do_not_call_any_mutating_controller(self):
        op, _ = self.fresh(); engine = self.engine(op); plan = engine.plan(mode='fresh')
        service = TransactionService(engine)
        self.assertEqual(service.report()['installation'], plan)
        self.assertEqual(service.wizard_state()['installation'], plan)
        self.assertFalse(self.trace)

    def test_arbitrary_http_parameters_cannot_select_an_application_controller(self):
        op, _ = self.fresh(); service = TransactionService(self.engine(op))
        with self.assertRaises(InstallerError):
            service.execute('plan', {'modules': ['web'], 'controller': '/tmp/arbitrary'})
        self.assertFalse(self.trace)

    def test_upgrade_recovery_recognition_never_calls_mutating_recover(self):
        op, write = self.upgrade(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='upgrade')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        (self.slot / 'result.json').unlink(); context = self.context(op, plan)
        before = list(self.trace)
        self.assertEqual(op.recover(context, 'apply').decision, RecoveryDecision.RETRY_SAFE)
        self.assertEqual(self.trace, before)

    def test_explicit_resume_checkpoints_apply_before_mutating_upgrade_recovery(self):
        op, _ = self.upgrade(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='upgrade')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        (self.slot / 'result.json').unlink(); self.trace.clear()
        engine = self.engine(op, hook=lambda *event: self.trace.append(event))
        self.assertEqual(engine.resume(plan['plan_sha256'])['state'], 'DONE')
        checkpoint = self.trace.index((op.spec.name, 'apply', 'checkpoint'))
        mutation = self.trace.index(('MUTATE', 'upgrade-recover-forward'))
        self.assertLess(checkpoint, mutation)

    def test_upgrade_lost_response_recovers_receipt_without_reapplying_cutover(self):
        op, _ = self.upgrade(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='upgrade')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        self.assertEqual(self.engine(op, vault=SecretVault()).resume(plan['plan_sha256'])['state'], 'DONE')
        self.assertEqual([x for x in self.trace if x[0] == 'MUTATE'], [('MUTATE', 'upgrade-apply')])

    def test_unknown_or_multiple_backup_slots_are_manual(self):
        op, _ = self.upgrade(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='upgrade')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        (op.backup_root / 'foreign').mkdir()
        self.assertEqual(self.engine(op).resume(plan['plan_sha256'])['state'], 'MANUAL_ACTION_REQUIRED')

    def test_lost_rollback_response_is_recognized_without_second_rollback(self):
        op, _ = self.upgrade(); engine = self.engine(op); plan = engine.plan(mode='upgrade'); engine.apply(plan['plan_sha256'])
        with self.assertRaises(Crash): self.engine(op, hook=self.crash_after(op.spec.name, 'rollback')).rollback(op.spec.boundary, plan['plan_sha256'])
        self.assertEqual(self.engine(op).resume(plan['plan_sha256'])['state'], 'ROLLED_BACK')
        self.assertEqual(self.trace.count(('MUTATE', 'upgrade-recover-rollback')), 1)

    def test_partial_rollback_can_only_continue_in_rollback_direction(self):
        op, _ = self.upgrade(); engine = self.engine(op); plan = engine.plan(mode='upgrade'); engine.apply(plan['plan_sha256'])
        (self.slot / 'rollback-intent.json').write_text('{}')
        context = self.context(op, plan)
        self.assertEqual(op.recover(context, 'apply').decision, RecoveryDecision.MANUAL)
        self.assertEqual(op.recover(context, 'rollback').decision, RecoveryDecision.RETRY_SAFE)

    def test_reopen_intent_overrides_historical_applied_receipt(self):
        op, _ = self.upgrade(); engine = self.engine(op); plan = engine.plan(mode='upgrade'); engine.apply(plan['plan_sha256'])
        (self.slot / 'resume-intent.json').write_text('{}'); context = self.context(op, plan)
        for phase in ('apply', 'commit', 'rollback'):
            self.assertEqual(op.recover(context, phase).decision, RecoveryDecision.MANUAL)
        with self.assertRaises(InstallerError): op._observe(context)

    def test_upgrade_and_authorization_are_distinct_journal_steps_without_service_start(self):
        op, _ = self.upgrade(); reopen = a.StorageResumeOperation(op); engine = self.engine(op, reopen); plan = engine.plan(mode='upgrade')
        result = engine.apply(plan['plan_sha256']); self.assertEqual(result['state'], 'DONE')
        self.assertEqual([x['name'] for x in result['steps']], ['web.storage-upgrade', 'web.storage-resume'])
        with self.assertRaises(InstallerError): engine.rollback(op.spec.boundary, plan['plan_sha256'])
        self.assertEqual(self.trace.count(('MUTATE', 'authorize')), 1)

    def test_authorization_response_loss_recovers_without_second_mutation(self):
        op, _ = self.upgrade(); reopen = a.StorageResumeOperation(op)
        engine = self.engine(op, reopen, hook=self.crash_after(reopen.spec.name)); plan = engine.plan(mode='upgrade')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        self.assertEqual(self.engine(op, reopen, vault=SecretVault()).resume(plan['plan_sha256'])['state'], 'DONE')
        self.assertEqual(self.trace.count(('MUTATE', 'authorize')), 1)

    def test_current_runtime_drift_blocks_commit_even_with_a_private_success_receipt(self):
        op, _ = self.upgrade(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='upgrade')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        op.controller.target_http.observe.side_effect = RuntimeError('runtime drift')
        self.assertNotEqual(self.engine(op).resume(plan['plan_sha256'])['state'], 'DONE')

    def test_storage_profile_refuses_fresh_remote_and_assistant_changes(self):
        op, _ = self.upgrade()
        for mode in ('fresh', 'assistant'):
            value = request() if mode == 'fresh' else request('upgrade')
            if mode == 'assistant': value['assistant']['action'] = 'disabled'
            with self.assertRaises(InstallerError):
                a.StorageUpgradeOperation(op.controller, a.WebInputs.capture(value, self.vault),
                    config_root=op.config_root, backup_root=op.backup_root)

    def test_reports_never_persist_passwords_even_after_lost_response(self):
        op, _ = self.fresh(); engine = self.engine(op, hook=self.crash_after(op.spec.name)); plan = engine.plan(mode='fresh')
        with self.assertRaises(Crash): engine.apply(plan['plan_sha256'])
        raw = self.journal.path.read_text() + ''.join(p.read_text() for p in self.runtime.state_root.iterdir())
        for secret in self.payload['secrets'].values():
            if secret: self.assertNotIn(secret, raw)
        self.assertNotIn('synthetic-migration-secret', raw)

    def test_upgrade_cannot_change_hostname_or_runtime_identity(self):
        op, _ = self.upgrade()
        for key, value in (('hostname', 'other.example.test'), ('service_user', 'other_identity')):
            configuration = op.inputs.configuration; configuration['web'][key] = value
            with self.assertRaises(InstallerError):
                a.StorageUpgradeOperation(op.controller, a.WebInputs(configuration),
                    config_root=op.config_root, backup_root=op.backup_root)


if __name__ == '__main__': unittest.main()
