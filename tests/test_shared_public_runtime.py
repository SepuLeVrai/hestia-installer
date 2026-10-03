"""Successor contracts, private receipts and ordering with native calls doubled."""
from contextlib import nullcontext
from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from installer import shared_public_runtime as s, shared_public_lifecycle as c
from installer.shared_public_plan import candidate, PARENTS, digest
from installer.gateway_identity import _b64, public_identity
from installer.model import InstallerError, canonical_bytes
from installer.operations import OperationContext, SecretVault, RecoveryDecision
from installer.transaction import StateJournal
from test_public_tls import profile
from test_shared_mobile_tls import gateway


def gateway_binding(web, identity):
    jwk = {'kty': 'EC', 'crv': 'P-256',
        'x': _b64(bytes.fromhex('6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296')),
        'y': _b64(bytes.fromhex('4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5'))}
    foundation = s.FoundationRuntime(s.old.PublicTLS(web).boot.activation, public_identity('main', jwk))
    return s.GatewayServiceProfile(foundation, identity, Path('/var/lib/hestia-fixture-identities')).binding()


def selected(web=None):
    web = profile() if web is None else web
    prepared = candidate(web, gateway(), ['127.0.0.10/32'], dict.fromkeys(PARENTS, 'a' * 64))
    return s.selection(prepared, gateway_binding(web, gateway()))


class SharedProfileTests(unittest.TestCase):
    def setUp(self): self.value = selected(); self.r = s.SharedPublic(self.value)

    def test_compilation_is_pure_and_preserves_original_units_bundles_and_boot(self):
        original = self.r.web.units()
        with patch('subprocess.run', side_effect=AssertionError('native')), patch('socket.socket', side_effect=AssertionError('network')):
            units = self.r.units(); self.r.files(); self.r.apache_dropin()
        self.assertEqual(set(units), set(original)); self.assertTrue(all(units[n] != original[n] for n in units))
        self.assertEqual(self.r.web.units(), original)
        self.assertEqual(self.r.files()['challenge-https.conf'], self.r.web.nginx('https'))
        self.assertNotEqual(self.r.root, self.r.web.root); self.assertNotEqual(self.r.root, self.r.shared.root)

    def test_closed_profile_rejects_parent_commands_source_paths_and_binding_drift(self):
        for mutate in (lambda v: v.update(version=True), lambda v: v.update(hook='anything'),
            lambda v: v['code'].update({'../foreign.py': 'a'*64}),
            lambda v: v['gateway_binding'].update(extra=True),
            lambda v: v['gateway_binding']['release'].update(binary_sha256='0'*64),
            lambda v: v['preparation']['renewal']['web']['renew'].append('--deploy-hook')):
            value = deepcopy(self.value); mutate(value)
            with self.assertRaises(Exception): s.SharedPublic(value)

    def test_new_units_use_only_the_successor_worker_and_keep_dependencies(self):
        units = self.r.units()
        for role in ('http', 'https', 'renew'):
            raw = units[self.r.web.unit(role)]
            self.assertIn(str(self.r.root / 'worker.py').encode(), raw)
            self.assertNotIn(str(self.r.web.root / 'worker.py').encode(), raw)
            self.assertNotIn(b'ExecStartPre=', raw)
        self.assertIn(self.r.boot.target.encode(), units[self.r.web.unit('https')])
        self.assertNotIn(self.r.boot.target.encode(), units[self.r.web.unit('http')])
        self.assertIn(b'TimeoutStartSec=1800', units[self.r.web.unit('renew')])

    def test_apache_change_only_replaces_its_guard_and_retains_every_include(self):
        self.assertEqual(self.r.apache_dropin().replace(str(self.r.root / 'worker.py').encode(), str(self.r.web.root / 'worker.py').encode()), self.r.web.apache_dropin())
        self.assertEqual(self.r.originals()['apache-overlay.conf'], self.r.web.apache_dropin())

    def test_registry_binds_all_resources_original_backups_and_successor_sources(self):
        with TemporaryDirectory() as root:
            engine, _ = s.engine(StateJournal(Path(root) / 'state.json'), self.value)
            doc = engine.plan(mode='upgrade')
            self.assertEqual(len(doc['steps']), 7)
            for spec, record in zip(doc['plan']['steps'], doc['steps']):
                operation = engine.registry.get(spec); engine._receipt(doc, spec, record, operation.receipt())
            resources = doc['plan']['steps'][1]['resources']
            self.assertEqual(sum(r['preexisting'] for r in resources), 5)
            changed = deepcopy(self.value); changed['code']['installer/extra.py'] = 'b'*64
            with self.assertRaises(InstallerError): s.engine(engine.journal, changed)

    def test_mobile_dns_ipv6_is_rejected_without_touching_owned_listeners(self):
        with patch.object(s.socket, 'getaddrinfo', return_value=[(s.socket.AF_INET6, 1, 0, '', ('::1', 443))]), patch.object(s.old, 'command') as effect:
            with self.assertRaises(InstallerError): self.r.network_ready()
            effect.assert_not_called()


class PrivateStateTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.r = s.SharedPublic(selected()); self.r.root = Path(temp.name) / 'private'
        self.operation = s.SharedOperation(self.r, 'handoff', 'shared.public.enroll')
        self.context = OperationContext('a'*32, self.operation.spec.as_dict(), {}, SecretVault())

    def write(self, name, value): self.r._write(name, value)

    def test_real_composed_configuration_exceeds_old_bound_and_roundtrips_exactly(self):
        raw = self.r.files()['ready-https.conf']; self.assertGreater(len(raw), 16384)
        with s._private_directory(self.r.root, create=True) as fd:
            self.r.write_configuration(fd, 'ready-https.conf', raw)
            self.assertEqual(s.f._read(fd, 'ready-https.conf', 0, mode=0o600, limit=s.CONFIG_LIMIT), raw)
            with self.assertRaises(Exception): s.f._read(fd, 'ready-https.conf', 0, mode=0o600)

    def test_configuration_writer_rejects_oversize_and_paths_before_creating_any_file(self):
        with s._private_directory(self.r.root, create=True) as fd:
            for name, raw in (('large.conf', b'x'*(s.CONFIG_LIMIT+1)), ('../foreign.conf', b'x'), ('.', b'x')):
                with self.assertRaises(InstallerError): self.r.write_configuration(fd, name, raw)
            self.assertEqual(os.listdir(fd), [])

    def test_configuration_writer_never_repairs_partial_files_or_follows_links(self):
        with s._private_directory(self.r.root, create=True) as fd:
            self.r.write_configuration(fd, 'partial.conf', b'partial')
            with self.assertRaises(FileExistsError): self.r.write_configuration(fd, 'partial.conf', b'complete')
            self.assertEqual(s.f._read(fd, 'partial.conf', 0, mode=0o600), b'partial')
            os.symlink('partial.conf', 'linked.conf', dir_fd=fd)
            with self.assertRaises(FileExistsError): self.r.write_configuration(fd, 'linked.conf', b'new')

    def test_bundle_preserves_frozen_source_set_and_rejects_worker_code_or_extra_file(self):
        self.r.copy_bundle(); self.r.bundle()
        before = {p: p.read_bytes() for p in self.r.root.rglob('*') if p.is_file()}
        with patch.object(s.boot, 'code_files', side_effect=AssertionError('current source adoption')): self.r.bundle()
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        for path in (self.r.root / 'worker.py', self.r.root / 'code/installer/shared_public_runtime.py'):
            raw = path.read_bytes(); path.write_bytes(raw + b'\n')
            with self.assertRaises(InstallerError): self.r.bundle()
            path.write_bytes(raw)
        path = self.r.root / 'code/installer/unlisted.py'; path.write_bytes(b'pass'); path.chmod(0o600)
        with self.assertRaises(InstallerError): self.r.bundle()

    def test_handoff_requires_exact_durable_intent_and_receipt_before_admission(self):
        with self.assertRaises(InstallerError): self.r.ownership()
        binding = self.r.binding(self.context); self.write('handoff.attempt', binding)
        with self.assertRaises(InstallerError): self.r.ownership()
        self.write('ownership.json', binding); self.r.ownership()
        path = self.r.root / 'ownership.json'; path.write_bytes(canonical_bytes({**binding, 'profile_sha256': 'b'*64}))
        with self.assertRaises(InstallerError): self.r.ownership()

    def test_boolean_or_added_receipt_keys_are_rejected(self):
        binding = self.r.binding(self.context)
        for value in ({**binding, 'version': True}, {**binding, 'extra': 'x'}):
            for name in ('handoff.attempt', 'ownership.json'):
                path = self.r.root / name
                if path.exists(): path.unlink()
                self.write(name, value)
            with self.assertRaises(InstallerError): self.r.ownership()

    def test_partial_publish_blocks_worker_instead_of_falling_back_to_challenge(self):
        self.assertFalse(self.r.ready())
        self.write('publish.attempt', self.r.binding(self.context))
        with self.assertRaisesRegex(InstallerError, 'MANUAL_ACTION_REQUIRED'): self.r.ready()

    def test_ready_requires_certificate_and_staging_dry_run_receipts(self):
        binding = self.r.binding(self.context)
        self.write('publish.attempt', binding); self.write('ready.json', binding)
        with self.assertRaises(InstallerError): self.r.ready()
        for phase in ('certificate', 'dry-run'):
            self.write(phase + '.attempt', binding); self.write(phase + '.json', binding)
        self.assertTrue(self.r.ready()); self.assertEqual(self.r.nginx_path('https').name, 'ready-https.conf')

    def test_incomplete_effect_is_manual_and_never_replayed(self):
        for phase in s.PHASES:
            operation = s.SharedOperation(self.r, phase, None)
            self.write(phase + '.attempt', self.r.binding(self.context))
            with patch.object(operation, 'current', side_effect=InstallerError('INVALID_STATE')), patch.object(operation, 'prepare') as prepare, patch.object(s.old, 'command') as effect:
                self.assertEqual(operation.recover(self.context, 'apply').decision, RecoveryDecision.MANUAL)
                prepare.assert_not_called(); effect.assert_not_called()

    def test_completed_lost_reply_recovers_by_observation_without_a_second_effect(self):
        for phase in s.PHASES:
            operation = s.SharedOperation(self.r, phase, None)
            with patch.object(operation, 'current'), patch.object(s.old, 'command') as effect:
                self.assertEqual(operation.recover(self.context, 'apply').decision, RecoveryDecision.APPLIED)
                effect.assert_not_called()
                self.assertEqual(operation.recover(self.context, 'rollback').decision, RecoveryDecision.MANUAL)

    def test_missing_attempt_can_only_retry_after_fresh_prepare(self):
        with patch.object(self.operation, 'current', side_effect=InstallerError('INVALID_STATE')), patch.object(self.operation, 'prepare') as prepare:
            self.assertEqual(self.operation.recover(self.context, 'apply').decision, RecoveryDecision.RETRY_SAFE)
            prepare.assert_called_once_with(self.context)

    def test_private_state_symlink_hardlink_and_mode_drift_remain_closed(self):
        binding = self.r.binding(self.context); self.write('handoff.attempt', binding); self.write('ownership.json', binding)
        path = self.r.root / 'ownership.json'; other = self.r.root / 'other'; other.write_bytes(path.read_bytes()); other.chmod(0o600)
        path.unlink(); path.symlink_to(other)
        with self.assertRaises(Exception): self.r.ownership()
        path.unlink(); os.link(other, path)
        with self.assertRaises(Exception): self.r.ownership()
        path.unlink(); path.write_bytes(other.read_bytes()); path.chmod(0o644)
        with self.assertRaises(Exception): self.r.ownership()


class NativeOrderingTests(unittest.TestCase):
    def setUp(self):
        self.r = s.SharedPublic(selected()); self.events = []
        self.enterContext(patch.object(s.old, 'command', side_effect=lambda argv, **kw: self.events.append(('command', argv))))
        self.enterContext(patch.object(self.r, 'control', side_effect=lambda verb, role: self.events.append((verb, role))))
        self.enterContext(patch.object(self.r, 'stopped', side_effect=lambda role: self.events.append(('stopped', role))))
        self.enterContext(patch.object(self.r, 'enrolled'))
        self.enterContext(patch.object(self.r, 'configuration'))
        self.enterContext(patch.object(self.r.web, 'configuration'))
        self.enterContext(patch.object(self.r.web, 'running', return_value=True))
        self.enterContext(patch.object(self.r, 'listener', return_value=True))
        self.enterContext(patch.object(self.r, '_write', side_effect=lambda name, value: self.events.append(('write', name))))
        operation = s.SharedOperation(self.r, 'handoff', None)
        self.context = OperationContext('a'*32, operation.spec.as_dict(), {}, SecretVault())

    def test_handoff_stops_only_its_three_roles_and_records_ownership_before_starts(self):
        with patch.object(self.r, 'replace_owned', side_effect=lambda path, before, after: self.events.append(('replace', path.name))):
            self.r.handoff(self.context)
        self.assertEqual(self.events[:6], [('stop','timer'), ('stopped','timer'), ('stopped','renew'), ('stop','https'), ('stopped','https'), ('stop','http')])
        self.assertEqual(sum(row[0]=='replace' for row in self.events), 5)
        self.assertLess(self.events.index(('write','ownership.json')), self.events.index(('start','http')))
        self.assertNotIn(('start','timer'), self.events); self.assertNotIn(('stop','apache'), self.events)

    def test_busy_renewal_refuses_before_listener_stop_or_any_replacement(self):
        self.r.stopped.side_effect = lambda role: (_ for _ in ()).throw(InstallerError('BUSY')) if role=='renew' else None
        with patch.object(self.r, 'replace_owned') as replace, self.assertRaises(InstallerError): self.r.handoff(self.context)
        replace.assert_not_called(); self.assertEqual(self.events, [('stop','timer')])

    def test_partial_replacement_never_records_ownership_or_starts(self):
        with patch.object(self.r, 'replace_owned', side_effect=OSError('cut')), self.assertRaises(OSError): self.r.handoff(self.context)
        self.assertFalse(any(row[0] in ('start','write') for row in self.events))

    def test_publish_checks_certificates_and_gateway_before_ready_and_starts(self):
        with patch.object(self.r.web, 'certificate'), patch.object(self.r.mobile, 'verify'), patch.object(self.r, 'gateway'):
            self.r.publish(self.context)
        self.assertLess(self.events.index(('write','ready.json')), self.events.index(('start','http')))

    def test_mobile_certificate_drift_after_stop_never_publishes_or_restarts(self):
        with patch.object(self.r.web, 'certificate'), patch.object(self.r.mobile, 'verify', side_effect=InstallerError('INVALID_STATE')), self.assertRaises(InstallerError): self.r.publish(self.context)
        self.assertFalse(any(row[0] in ('start','write') for row in self.events))


class SharedWorkerTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.r = s.SharedPublic(selected()); self.r.root = Path(temp.name)
        self.enterContext(patch.object(self.r, 'configuration'))
        self.enterContext(patch.object(self.r, 'ready', return_value=True))
        self.enterContext(patch.object(self.r, 'completed'))
        self.enterContext(patch.object(self.r, '_read', return_value={'approved': True}))
        self.web = self.enterContext(patch.object(self.r.web, 'certificate'))
        self.mobile = self.enterContext(patch.object(self.r.mobile, 'verify'))
        self.enterContext(patch.object(self.r.web, 'running', return_value=True))
        self.enterContext(patch.object(self.r, 'listener', return_value=True))
        self.reload = self.enterContext(patch.object(self.r.web, 'systemctl'))
        self.command = self.enterContext(patch.object(s.old, 'command'))

    def test_renewal_runs_both_fixed_certbot_commands_then_one_hup(self):
        self.r.worker('renew')
        self.assertEqual([call.args[0] for call in self.command.call_args_list[:2]], [self.r.web.certbot(renew=True), self.r.shared.certbot(renew=True)])
        self.reload.assert_called_once_with('reload','https')
        self.assertEqual(self.web.call_args_list[0].kwargs, {'minimum_lifetime':0, 'allow_expired':True})
        self.assertEqual(self.mobile.call_args_list[0].kwargs, {'minimum_lifetime':0, 'allow_expired':True})
        self.assertEqual(self.web.call_args_list[-1].kwargs, {}); self.assertEqual(self.mobile.call_args_list[-1].kwargs, {})

    def test_web_renewal_failure_still_attempts_mobile_but_never_reloads(self):
        self.command.side_effect = [InstallerError('VALIDATION_FAILED'), b'']
        with self.assertRaises(InstallerError): self.r.worker('renew')
        self.assertEqual(self.command.call_count, 2); self.reload.assert_not_called()

    def test_mobile_renewal_failure_never_reloads_or_starts_either_listener(self):
        self.command.side_effect = [b'', InstallerError('VALIDATION_FAILED')]
        with self.assertRaises(InstallerError): self.r.worker('renew')
        self.reload.assert_not_called()

    def test_expired_certificate_cannot_bypass_post_renewal_validation(self):
        self.mobile.side_effect = [None, InstallerError('INVALID_STATE')]
        with self.assertRaises(InstallerError): self.r.worker('renew')
        self.assertEqual(self.command.call_count, 2); self.reload.assert_not_called()

    def test_stopped_https_is_not_implicitly_started_after_successful_renewal(self):
        self.r.listener.side_effect = [True, False]
        self.r.worker('renew'); self.reload.assert_not_called()

    def test_effect_lock_excludes_concurrent_renewal_before_certbot(self):
        with StateJournal(self.r.root / 'effect-lock.json').locked():
            with self.assertRaisesRegex(InstallerError,'BUSY'): self.r.worker('renew')
        self.command.assert_not_called(); self.reload.assert_not_called()

    def test_http_challenge_start_needs_no_certificate_and_https_uses_only_web(self):
        self.r.ready.return_value = False
        self.assertEqual(self.r.worker('http')[-1], str(self.r.root / 'challenge-http.conf'))
        self.web.assert_not_called(); self.mobile.assert_not_called()
        self.r.worker('https'); self.web.assert_called_once_with(minimum_lifetime=0); self.mobile.assert_not_called()

    def test_ready_https_start_validates_both_and_returns_exact_nginx_vector(self):
        argv = self.r.worker('https')
        self.assertEqual(argv, ['/usr/sbin/nginx','-c',str(self.r.root / 'ready-https.conf')])
        self.web.assert_called_once_with(minimum_lifetime=0); self.mobile.assert_called_once_with(minimum_lifetime=0)
        self.reload.assert_not_called()

    def test_partial_configuration_or_unknown_role_refuses_before_commands(self):
        for role in ('anything','renew'):
            self.r.configuration.side_effect = InstallerError('INVALID_STATE')
            with self.assertRaises(InstallerError): self.r.worker(role)
        self.command.assert_not_called()

    def test_backend_guard_checks_maintenance_without_sql_or_any_effect(self):
        with patch.object(self.r.http,'_inspect_configuration'), patch.object(self.r.layout.identity,'account'), patch.object(self.r.http,'_scope') as scope:
            scope.return_value.observe.return_value = {'state':'MAINTENANCE_REQUIRED'}
            with self.assertRaises(InstallerError): self.r.worker('backend')
        self.command.assert_not_called(); self.reload.assert_not_called()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.value = selected(); prepared = self.value['preparation']
        self.parent = SimpleNamespace(journal=StateJournal(self.root / 'main/state.json'), secrets=SecretVault())
        self.preparation = SimpleNamespace(root=self.root/'preparation', parent=self.parent, gateway=object(),
            profile=lambda: deepcopy(prepared), binding=Mock(return_value=deepcopy(prepared)))
        runtime = SimpleNamespace(profile=SimpleNamespace(identity=gateway(), binding=lambda: deepcopy(self.value['gateway_binding'])),
            web=SimpleNamespace(spec=SimpleNamespace(instance=prepared['instance'])))
        self.service = SimpleNamespace(gateway=self.preparation.gateway, engine=lambda parent:(SimpleNamespace(report=lambda:{'state':'DONE'}),runtime))
        self.control = c.SharedPublicLifecycle(self.preparation,self.service)
        self.parent_doc = {'state':'DONE'}
        self.enterContext(patch.object(self.parent.journal,'locked',return_value=nullcontext(SimpleNamespace(read=lambda:deepcopy(self.parent_doc)))))
        self.absent = self.enterContext(patch.object(s.SharedPublic,'absent'))
        self.enterContext(patch('subprocess.run',side_effect=AssertionError('native effect')))

    def plan(self): return self.control.execute('plan',{'preparation_sha256':digest(self.value['preparation'])})

    def test_plan_requires_distinct_confirmation_and_new_immutable_source_selection(self):
        with self.assertRaisesRegex(InstallerError,'CONFIRMATION_REQUIRED'): self.control.execute('plan',{'preparation_sha256':'0'*64})
        self.assertFalse(self.control.root.exists()); self.absent.assert_not_called()
        value = self.plan(); self.assertEqual(value['installation']['state'],'PLANNED')
        self.assertFalse(value['public_mobile_available']); self.assertFalse(value['phase6_complete'])
        self.assertEqual(self.control.profile(),self.value)

    def test_repeated_plan_and_status_preserve_every_byte_without_native_observation(self):
        state=self.plan(); before={p:p.read_bytes() for p in self.control.root.rglob('*') if p.is_file()}
        self.absent.reset_mock(); self.assertEqual(self.plan(),state); self.assertEqual(self.control.state(),state)
        self.assertEqual(before,{p:p.read_bytes() for p in before}); self.absent.assert_not_called()

    def test_actions_paths_hooks_and_missing_explicit_consent_are_refused(self):
        for action,payload in (('start',{}),('plan',{'preparation_sha256':'a'*64,'path':'/etc'}),('apply',{'confirmation':'a'*64,'confirm':1})):
            with self.assertRaises(InstallerError): self.control.execute(action,payload)
        self.absent.assert_not_called()

    def test_source_change_after_planning_cannot_drive_the_old_registry(self):
        self.plan()
        with patch.object(s,'code_identity',return_value={'installer/changed.py':'f'*64}), self.assertRaises(InstallerError): self.control.engine(self.parent_doc)

    def test_parent_preparation_drift_cannot_rebase_or_rewrite_a_plan(self):
        self.plan(); before=self.control._read('profile.json')
        self.preparation.binding.return_value['parents']['gateway']='f'*64
        with self.assertRaises(InstallerError): self.plan()
        self.assertEqual(before,self.control._read('profile.json'))

    def test_status_without_plan_creates_nothing_and_claims_no_health(self):
        state=self.control.state(); self.assertIsNone(state['installation']); self.assertFalse(self.control.root.exists())
        self.absent.assert_not_called(); self.preparation.binding.assert_not_called()

    def test_foreign_gateway_controller_or_unfinished_parent_cannot_plan(self):
        self.service.gateway=object()
        with self.assertRaises(InstallerError): self.plan()
        self.assertFalse(self.control.root.exists())
        self.service.gateway=self.preparation.gateway; self.parent_doc['state']='PLANNED'
        with self.assertRaises(InstallerError): self.plan()

    def test_known_secrets_rejected_before_new_profile_write(self):
        self.parent.secrets.put('secret','operator@example.test')
        with self.assertRaisesRegex(InstallerError,'SECRET_REJECTED'): self.plan()
        self.assertFalse(self.control.root.exists())


class ListenerObservationTests(unittest.TestCase):
    def setUp(self):
        self.r=s.SharedPublic(selected())
        self.value={'MainPID':'123'}
        self.show=self.enterContext(patch.object(self.r.web,'systemctl',return_value=self.value))
        self.enterContext(patch.object(self.r.web,'running',return_value=True))
        self.enterContext(patch.object(self.r,'ready',return_value=True))
        self.exe=self.enterContext(patch.object(s.os.path,'samefile',return_value=True))
        self.argv=self.enterContext(patch.object(Path,'read_bytes',return_value=('nginx: master process /usr/sbin/nginx -c '+str(self.r.nginx_path('http'))).encode()+b'\0'))
        self.tcp=self.enterContext(patch.object(Path,'read_text',return_value='header\n0: 00000000:0050 remote 0A a b c d e 42\n'))
        self.enterContext(patch.object(Path,'iterdir',return_value=iter([Path('/proc/123/fd/3')])))
        self.link=self.enterContext(patch.object(s.os,'readlink',return_value='socket:[42]'))

    def test_active_python_guard_is_not_an_owned_nginx_listener(self):
        self.exe.return_value=False; self.assertFalse(self.r.listener('http')); self.argv.assert_not_called()

    def test_exact_master_arguments_cgroup_observation_and_listening_fd_are_required(self):
        self.assertTrue(self.r.listener('http')); self.exe.assert_called_once_with(Path('/proc/123/exe'),'/usr/sbin/nginx')
        self.assertEqual(self.show.call_count,2)

    def test_wrong_stage_configuration_is_rejected_despite_active_nginx(self):
        self.argv.return_value=b'nginx: master process /usr/sbin/nginx -c /foreign.conf\0'
        with self.assertRaisesRegex(InstallerError,'SOURCE_DRIFT'): self.r.listener('http')

    def test_foreign_listening_inode_cannot_complete_a_start(self):
        self.link.return_value='socket:[99]'; self.assertFalse(self.r.listener('http'))

    def test_manager_pid_drift_during_observation_is_refused(self):
        self.show.side_effect=[self.value,{'MainPID':'124'}]
        with self.assertRaisesRegex(InstallerError,'SOURCE_DRIFT'): self.r.listener('http')

    def test_start_waits_boundedly_for_nginx_without_repeating_the_start_command(self):
        with patch.object(self.r,'listener',side_effect=[False,False,True]),patch.object(self.r,'control') as control,patch.object(s.time,'sleep') as sleep:
            self.r.start_listener('http'); control.assert_called_once_with('start','http'); self.assertEqual(sleep.call_count,2)
        with patch.object(self.r,'listener',return_value=False),patch.object(self.r,'control') as control,patch.object(s.time,'sleep'),patch.object(s.time,'monotonic',side_effect=[0,11]):
            with self.assertRaisesRegex(InstallerError,'VALIDATION_FAILED'): self.r.start_listener('http')
            control.assert_called_once_with('start','http')
