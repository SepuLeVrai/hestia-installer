"""Frozen-parent and durable-handoff contracts; no system or SQL mutation."""
from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import frozen_public_tls as frozen, shared_public_plan as s
from installer import public_tls_runtime as n, public_tls_plan as p
from installer.engine import TransactionEngine
from installer.model import InstallerError, Receipt, aggregate, canonical_bytes, initial_document, build_plan
from installer.operations import SecretVault, default_registry
from installer.transaction import StateJournal
from test_public_tls import profile
from test_shared_mobile_tls import gateway


def complete(engine):
    document = engine.plan(mode='fresh')
    document.update(state='DONE', approved_plan_sha256=document['plan_sha256'], revision=1)
    for spec, record in zip(document['plan']['steps'], document['steps']):
        operation = engine.registry.get(spec)
        receipt = operation.receipt() if isinstance(operation, n.PublicOperation) else Receipt(
            created_resources=tuple(r['name'] for r in spec['resources']))
        record.update(state='DONE', phase='done', attempts=1, evidence=receipt.as_dict())
    document.update(aggregate(document))
    with engine.journal.locked() as locked: locked.write(document, expected_revision=0)
    return document


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-shared-public-'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.parent = TransactionEngine(StateJournal(self.root / 'main/state.json'), default_registry(), secrets=SecretVault())
        self.parent_doc = complete(self.parent)
        self.web = profile()
        self.boot = SimpleNamespace(_read=lambda name: deepcopy(self.web['boot']))
        self.acme = SimpleNamespace(profile=lambda: deepcopy(self.web['acme']))
        self.public = p.PublicTLSPlan(self.parent, self.boot, self.acme)
        self.public.parents = Mock(return_value=(deepcopy(self.web['parents']), None))
        self.public._write('profile.json', self.web)
        engine, _ = n.engine(self.public.journal, self.web)
        self.public_doc = complete(engine)
        self.gateway_doc = {'state': 'DONE', 'plan_sha256': '8' * 64}
        self.identity = gateway()
        self.identities = {'profile': self.identity, 'receipt': {'public_key_binding': '9' * 64}}
        self.gateway_profile = {'identity': self.identity, 'web_plan_sha256': self.parent_doc['plan_sha256']}
        self.gateway = SimpleNamespace(engine=lambda: SimpleNamespace(report=lambda: deepcopy(self.gateway_doc)),
            profile=lambda: deepcopy(self.gateway_profile), identities=SimpleNamespace(report=lambda: deepcopy(self.identities)))
        self.plan = s.SharedPublicPlan(self.public, self.gateway)
        self.payload = {'public_sha256': self.public_doc['plan_sha256'], 'gateway_sha256': self.gateway_doc['plan_sha256'],
                        'client_networks': ['127.0.0.10/32']}
        self.validation = self.enterContext(patch.object(n.PublicOperation, 'validate', return_value=True))
        self.enterContext(patch.object(n.ServiceIdentityOperation, 'validate', return_value=True))
        self.enabled = self.enterContext(patch.object(n.PublicTLS, 'enabled'))
        self.running = self.enterContext(patch.object(n.PublicTLS, 'running', return_value=True))
        self.enterContext(patch('subprocess.run', side_effect=AssertionError('unexpected native effect')))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('unexpected network')))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode, p.stat().st_ino)
                for p in self.root.rglob('*') if p.is_file()}


class FrozenPublicTests(Fixture):
    def test_new_code_set_reads_original_registry_without_adopting_or_writing(self):
        before = self.snapshot()
        with patch.object(n.boot, 'code_files', return_value={'installer/new.py': b'new'}):
            with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.public.engine(self.parent_doc)
            document, runtime = frozen.reference(self.public, self.parent_doc)
        self.assertEqual(document, self.public_doc); self.assertEqual(runtime.value, self.web)
        self.assertEqual(self.snapshot(), before); self.validation.assert_not_called()

    def test_explicit_observation_validates_every_original_step_and_all_public_services(self):
        before = self.snapshot()
        frozen.reference(self.public, self.parent_doc, observe=True)
        self.assertEqual(self.validation.call_count, len(n.PHASES))
        self.enabled.assert_called_once()
        self.assertEqual([c.args[0] for c in self.running.call_args_list], ['http', 'https', 'timer'])
        self.assertEqual(self.snapshot(), before)

    def test_incomplete_parent_is_not_admitted(self):
        other = initial_document(build_plan(n.engine(self.public.journal, self.web)[0].registry.specs()))
        self.public.journal.path.write_bytes(canonical_bytes(other))
        with self.assertRaisesRegex(InstallerError, 'DEPENDENCY_BLOCKED'): frozen.reference(self.public, self.parent_doc)

    def test_parent_profile_drift_is_not_silently_rebased(self):
        for key in ('parents', 'boot', 'acme'):
            with self.subTest(key=key):
                changed = deepcopy(self.web); changed[key] = {}
                (self.public.root / 'profile.json').write_bytes(canonical_bytes(changed))
                with self.assertRaises(Exception): frozen.reference(self.public, self.parent_doc)
        (self.public.root / 'profile.json').write_bytes(canonical_bytes(self.web))

    def test_partial_registry_cannot_be_read_as_completed_public_parent(self):
        engine, _ = n.engine(self.public.journal, self.web)
        document = initial_document(build_plan(engine.registry.specs()[:1]))
        self.public.journal.path.write_bytes(canonical_bytes(document))
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): frozen.reference(self.public, self.parent_doc)

    def test_failed_current_validation_or_stopped_service_refuses_admission(self):
        self.validation.return_value = False
        with self.assertRaisesRegex(InstallerError, 'VALIDATION_FAILED'): frozen.reference(self.public, self.parent_doc, observe=True)
        self.validation.return_value = True; self.running.return_value = False
        with self.assertRaisesRegex(InstallerError, 'VALIDATION_FAILED'): frozen.reference(self.public, self.parent_doc, observe=True)

    def test_drift_during_observation_is_detected_without_repair(self):
        path = self.public.root / 'profile.json'
        changed = deepcopy(self.web); changed['choices']['email'] = 'other@example.test'
        self.enabled.side_effect = lambda: path.write_bytes(canonical_bytes(changed))
        with self.assertRaisesRegex(InstallerError, 'SOURCE_DRIFT'): frozen.reference(self.public, self.parent_doc, observe=True)
        self.assertEqual(path.read_bytes(), canonical_bytes(changed))

    def test_unsafe_or_missing_profile_is_refused(self):
        path = self.public.root / 'profile.json'; raw = path.read_bytes()
        path.chmod(0o644)
        with self.assertRaises(InstallerError): frozen.reference(self.public, self.parent_doc)
        path.unlink()
        with self.assertRaisesRegex(InstallerError, 'NOT_PLANNED'): frozen.reference(self.public, self.parent_doc)
        other = self.root / 'linked'; other.write_bytes(raw); other.chmod(0o600); path.symlink_to(other)
        with self.assertRaises(Exception): frozen.reference(self.public, self.parent_doc)


class SharedPublicPlanTests(Fixture):
    def test_empty_report_creates_nothing_and_runs_no_native_observation(self):
        before = self.snapshot(); state = self.plan.state()
        self.assertEqual(state['state'], 'NOT_PLANNED'); self.assertFalse(self.plan.root.exists())
        self.assertEqual(self.snapshot(), before); self.validation.assert_not_called()

    def test_plan_binds_both_renewals_all_units_links_and_exact_composition(self):
        state = self.plan.execute('plan', self.payload); value = state['plan']
        self.assertEqual(state['state'], 'PREPARED_HISTORICAL')
        self.assertEqual(set(value['responsibilities']), {'http', 'https', 'renew', 'timer'})
        self.assertEqual(len(value['enable_links']), 3)
        for owner in ('web', 'mobile'):
            self.assertIn('hestia-' + owner, value['renewal'][owner]['renew'])
            self.assertIn('--no-directory-hooks', value['renewal'][owner]['renew'])
        self.assertEqual(value['composition']['gateway_profile_sha256'], s.digest(self.identity))
        self.assertIn('renewal-handoff', value['stages'])
        for flag in ('current_admission', 'execution_authorized', 'ownership_transferred', 'public_tls_verified', 'phase6_complete'):
            self.assertIs(state[flag], False)

    def test_restart_and_lost_response_reuse_same_plan_without_rewriting(self):
        first = self.plan.execute('plan', self.payload); before = self.snapshot()
        restarted = s.SharedPublicPlan(self.public, self.gateway)
        self.assertEqual(restarted.state(), first)
        self.assertEqual(restarted.execute('plan', self.payload), first)
        self.assertEqual(restarted.execute('check', {'plan_sha256': first['plan_sha256']}), first)
        self.assertEqual(self.snapshot(), before)

    def test_status_remains_historical_and_never_observes_live_services(self):
        expected = self.plan.execute('plan', self.payload); self.validation.reset_mock()
        self.running.side_effect = AssertionError('report is historical')
        self.assertEqual(self.plan.state(), expected); self.validation.assert_not_called()

    def test_parent_files_are_preserved_byte_for_byte_including_modes_and_inodes(self):
        before = self.snapshot(); self.plan.execute('plan', self.payload); after = self.snapshot()
        self.assertEqual({k: after[k] for k in before}, before)
        self.assertEqual(set(after) - set(before), {'main/shared-public/profile.json'})
        self.assertEqual((self.plan.root / 'profile.json').stat().st_mode & 0o777, 0o600)

    def test_stale_confirmation_is_refused_before_live_observation_and_creation(self):
        for key in ('public_sha256', 'gateway_sha256'):
            with self.subTest(key=key), self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'):
                self.plan.execute('plan', {**self.payload, key: '0' * 64})
        self.validation.assert_not_called(); self.assertFalse(self.plan.root.exists())

    def test_closed_actions_and_payloads_exclude_effects_paths_and_commands(self):
        for action in ('apply', 'resume', 'retry', 'start', 'renew'):
            with self.assertRaises(InstallerError): self.plan.execute(action, self.payload)
        for key in ('path', 'command', 'unit', 'confirm', 'server', 'credentials'):
            with self.assertRaises(InstallerError): self.plan.execute('plan', {**self.payload, key: 'forbidden'})
        self.assertFalse(self.plan.root.exists())

    def test_invalid_networks_origins_and_domain_collision_fail_before_write(self):
        for networks in ([], ['::/0'], ['127.0.0.1/8'], ['127.0.0.10/32'] * 2, 'public', [42]):
            with self.subTest(networks=networks), self.assertRaises(Exception):
                self.plan.execute('plan', {**self.payload, 'client_networks': networks})
        for origin in ('https://hestia.example.test', 'https://Mobile.example.test', 'https://mobile.example.test/path', 'https://' + 'a' * 300):
            self.identity['public_origin'] = origin
            with self.subTest(origin=origin), self.assertRaises(Exception): self.plan.execute('plan', self.payload)
        self.assertFalse(self.plan.root.exists())

    def test_changed_selection_never_overwrites_prepared_plan(self):
        self.plan.execute('plan', self.payload); before = self.snapshot()
        with self.assertRaisesRegex(InstallerError, 'PLAN_EXISTS'):
            self.plan.execute('plan', {**self.payload, 'client_networks': ['0.0.0.0/0']})
        self.assertEqual(self.snapshot(), before)

    def test_changed_parent_journal_or_identity_receipt_blocks_check(self):
        state = self.plan.execute('plan', self.payload); before = self.snapshot()
        self.gateway_doc['revision'] = 2
        with self.assertRaisesRegex(InstallerError, 'SOURCE_DRIFT'):
            self.plan.execute('check', {'plan_sha256': state['plan_sha256']})
        self.gateway_doc.pop('revision'); self.identities['receipt']['public_key_binding'] = '0' * 64
        with self.assertRaisesRegex(InstallerError, 'SOURCE_DRIFT'):
            self.plan.execute('check', {'plan_sha256': state['plan_sha256']})
        self.assertEqual(self.snapshot(), before)

    def test_missing_or_unrelated_gateway_parent_is_refused(self):
        self.gateway_doc['state'] = 'PLANNED'
        with self.assertRaisesRegex(InstallerError, 'DEPENDENCY_BLOCKED'): self.plan.execute('plan', self.payload)
        self.gateway_doc['state'] = 'DONE'; self.gateway_profile['web_plan_sha256'] = '0' * 64
        with self.assertRaisesRegex(InstallerError, 'INCOMPATIBLE_STATE'): self.plan.execute('plan', self.payload)
        self.assertFalse(self.plan.root.exists())

    def test_missing_identity_receipt_is_refused_before_plan(self):
        self.identities['receipt'] = None
        with self.assertRaisesRegex(InstallerError, 'DEPENDENCY_BLOCKED'): self.plan.execute('plan', self.payload)
        self.assertFalse(self.plan.root.exists())

    def test_plan_drift_between_metadata_and_live_admission_is_refused(self):
        self.enabled.side_effect = lambda: self.gateway_doc.update(revision=2)
        with self.assertRaisesRegex(InstallerError, 'SOURCE_DRIFT'): self.plan.execute('plan', self.payload)
        self.assertFalse(self.plan.root.exists())

    def test_main_lock_serializes_plan_without_creating_another_lock(self):
        with self.parent.journal.locked():
            with self.assertRaisesRegex(InstallerError, 'BUSY'): self.plan.execute('plan', self.payload)
        self.assertFalse(self.plan.root.exists())

    def test_known_secret_cannot_enter_persisted_contact_or_report(self):
        self.parent.secrets.put('fixture-secret', 'operator@example.test')
        with self.assertRaisesRegex(InstallerError, 'SECRET_REJECTED'): self.plan.execute('plan', self.payload)
        self.assertFalse(self.plan.root.exists())

    def test_tampered_responsibility_renewal_and_boolean_aliases_fail_closed(self):
        state = self.plan.execute('plan', self.payload); path = self.plan.root / 'profile.json'
        changes = [lambda v: v['responsibilities'].pop('timer'),
                   lambda v: v['renewal']['web']['renew'].append('--deploy-hook'),
                   lambda v: v.update(ownership_transferred=True), lambda v: v.update(version=True),
                   lambda v: v.update(execution_authorized=0), lambda v: v['stages'].reverse()]
        for change in changes:
            value = deepcopy(state['plan']); change(value); path.write_bytes(canonical_bytes(value))
            with self.assertRaises(InstallerError): self.plan.state()
        path.write_bytes(canonical_bytes(state['plan']))

    def test_partial_durable_write_is_preserved_and_never_recreated(self):
        self.plan.root.mkdir(mode=0o700); path = self.plan.root / 'profile.json'
        path.write_bytes(b'{"version":'); path.chmod(0o600); before = self.snapshot()
        with self.assertRaises(Exception): self.plan.execute('plan', self.payload)
        self.assertEqual(self.snapshot(), before)

    def test_symlink_hardlink_and_wrong_mode_state_are_refused(self):
        self.plan.execute('plan', self.payload); path = self.plan.root / 'profile.json'; raw = path.read_bytes()
        other = self.root / 'other'; other.write_bytes(raw); other.chmod(0o600)
        path.unlink(); path.symlink_to(other)
        with self.assertRaises(Exception): self.plan.state()
        path.unlink(); os.link(other, path)
        with self.assertRaises(InstallerError): self.plan.state()
        path.unlink(); path.write_bytes(raw); path.chmod(0o644)
        with self.assertRaises(InstallerError): self.plan.state()

    def test_saved_plan_does_not_change_when_request_object_is_later_edited(self):
        state = self.plan.execute('plan', self.payload)
        self.payload['client_networks'].append('198.51.100.0/24')
        self.assertEqual(self.plan.state(), state)

    def test_check_requires_the_exact_prepared_plan_without_recording_availability(self):
        with self.assertRaisesRegex(InstallerError, 'NOT_PLANNED'): self.plan.execute('check', {'plan_sha256': '0' * 64})
        state = self.plan.execute('plan', self.payload); before = self.snapshot()
        self.validation.reset_mock()
        with self.assertRaisesRegex(InstallerError, 'CONFIRMATION_REQUIRED'): self.plan.execute('check', {'plan_sha256': '0' * 64})
        self.validation.assert_not_called(); self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.plan.execute('check', {'plan_sha256': state['plan_sha256']})['current_admission'])


class FrozenPublicBundleTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='hestia-frozen-bundle-'); self.addCleanup(temp.cleanup)
        self.runtime = n.PublicTLS(profile()); self.runtime.root = Path(temp.name) / 'private'
        self.runtime.copy_bundle()

    def test_installed_bundle_is_verified_against_its_frozen_set_after_source_extension(self):
        before = {p: p.read_bytes() for p in self.runtime.root.rglob('*') if p.is_file()}
        with patch.object(n.boot, 'code_files', side_effect=AssertionError('must not adopt current source')):
            self.runtime.bundle()
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_changed_missing_or_additional_installed_code_is_refused(self):
        path = self.runtime.root / 'code/installer/public_tls_runtime.py'; raw = path.read_bytes()
        path.write_bytes(raw + b'\n# drift\n')
        with self.assertRaises(InstallerError): self.runtime.bundle()
        path.unlink()
        with self.assertRaises(InstallerError): self.runtime.bundle()
        path.write_bytes(raw); path.chmod(0o600)
        unexpected = path.parent / 'foreign.py'; unexpected.write_bytes(b'# foreign\n'); unexpected.chmod(0o600)
        with self.assertRaises(InstallerError): self.runtime.bundle()
