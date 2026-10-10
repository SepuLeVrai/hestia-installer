#!/usr/bin/env python3
"""Three real public cycles on one host, each followed by a new PID 1.

Only fault injection is substituted: a coordinator is killed after its native
publication, fragment transfer or opening, before saving the cockpit reply.
"""
import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import patch

import gateway_public_composed as base
from installer import gateway_publication_chain as chain
from installer import gateway_public_selection as selection
from installer.model import InstallerError

CYCLE = None
TARGETS = (base.FCM_COMMIT, base.LEGACY_COMMIT, base.FCM_COMMIT)


def file_hashes(roots):
    return {str(path): base.digest(path) for root in roots for path in root.rglob('*')
            if path.is_file() and not path.is_symlink() and path.name != '.transaction.lock'}


def preserved(case, records):
    for path, digest in records.items(): case.assertEqual(base.digest(Path(path)), digest, path)


class Transfer(unittest.TestCase):
    def test_same_host_next_cycle_and_lost_native_reply(self):
        service = base.fixture.service(); self.addCleanup(service.close)
        parent = service.engine.report(); original = base.opening.g.mobile.MobileBootRuntime(service.mobile_boot._read('profile.json'))
        initial = CYCLE == 1
        if initial:
            original.live(); _, source = service.gateway_service.engine(parent)
        else:
            source = base.authority.selected_for_admission(original.http)
            old = service.gateway_transition_execution
            self.assertEqual(old.state()['state'], 'DONE')
            old_profile = old.profile()
            self.assertEqual(source.profile.selected_release['commit'], TARGETS[CYCLE - 2])
            answer = service.execute('gateway-transition-execution.next',
                {'confirmation': old.state()['confirmation'], 'confirm': True})
            self.assertEqual(answer['gateway_transition_execution']['state'], 'NOT_PLANNED')
            self.assertEqual(answer['mobile_backup']['state'], 'NOT_PLANNED')
            self.assertEqual(answer['gateway_transition']['source_commit'], TARGETS[CYCLE - 2])
            # Restart the cockpit process's controllers from the durable link.
            expected_root = service.gateway_transition_execution.root
            service.close(); service = base.fixture.service(); self.addCleanup(service.close)
            self.assertEqual(service.gateway_transition_execution.root, expected_root)
            with patch.object(base.cockpit, 'NativeTransition', side_effect=AssertionError('GET native')):
                self.assertEqual(service.wizard_state()['gateway_transition_execution']['state'], 'NOT_PLANNED')
            with self.assertRaises(InstallerError):
                service.execute('gateway-transition-execution.resume',
                    {'confirmation': base.cockpit.digest(old_profile), 'confirm': True, 'credentials': {}, 'allow_global_read_lock': True})
        self.assertIsNone(source.profile.dev); self.assertIsNone(source.profile.push)
        identity = base.identity(source)
        if not initial:
            first = json.loads((base.EVIDENCE / 'public-cycle-1-transfer-proof.json').read_bytes())
            self.assertEqual(identity, first['uuid_sha256'])
        roots = [original.root, original.shared.root, original.shared.boot.root]
        roots.extend(original.layout.root.glob('public-successor-*'))
        roots.extend(original.layout.root.glob('gateway-backup*'))
        retained = file_hashes(roots)
        for p in (*service.gateway.identities.root.glob('*.pem'), service.shared_public.journal.path,
                  service.mobile_boot.journal.path, service.gateway_service.journal.path,
                  source.profile.config, source.fragment): retained[str(p)] = base.digest(p)
        saved = {'cycle': CYCLE, 'target': TARGETS[CYCLE - 1], 'uuid_sha256': identity,
                 'epoch': original.epoch_identity(), 'preserved': retained}
        base.save('public-cycle-' + str(CYCLE) + '-before.json', saved)
        parents = {'web': parent['plan_sha256'], **{name: getattr(service, name).journal.read()['plan_sha256']
                    for name in ('activation', 'gateway', 'foundation', 'gateway_service')}}
        credentials = {'database_password': base.setup_payload()['credentials']['database_password'],
            'authority_user': service.mariadb.runtime().authority_user, 'authority_password': base.fixture.PASSWORD}
        backup = service.execute('mobile-backup.plan', {'parents': parents})['mobile_backup']
        result = service.execute('mobile-backup.apply', {'confirmation': backup['confirmation'], 'confirm': True,
            'credentials': credentials, 'allow_global_read_lock': True})['mobile_backup']
        self.assertEqual(result['state'], 'DONE')
        target = TARGETS[CYCLE - 1]; direction = 'rollback' if CYCLE == 2 else 'upgrade'
        planned = service.execute('gateway-transition.plan', {'source_plan_sha256': parents['gateway_service'],
            'target_commit': target, 'direction': direction})['gateway_transition']
        self.assertEqual(planned['profile']['assessment']['source'], source.profile.binding())
        planned = service.execute('gateway-transition-execution.plan',
            {'transition_sha256': planned['confirmation']})['gateway_transition_execution']
        control = service.gateway_transition_execution
        raw = Path('/opt/gateway-target-package.zip' if target == base.FCM_COMMIT else '/opt/gateway-package.zip').read_bytes()
        service.import_transition_package(planned['confirmation'], io.BytesIO(raw), len(raw))
        request = {'confirmation': planned['confirmation'], 'confirm': True,
                   'credentials': credentials, 'allow_global_read_lock': True}
        boundary = {1: 'publication', 2: 'public-transfer', 3: 'public-open'}[CYCLE]
        original_write = type(control)._write
        def interrupted():
            def write(instance, name, value):
                if instance.root == control.root and name == boundary + '.done.json': os.kill(os.getpid(), signal.SIGKILL)
                return original_write(instance, name, value)
            with patch.object(type(control), '_write', write): service.execute('gateway-transition-execution.apply', request)
        base.kill(interrupted, 'cycle-' + str(CYCLE) + '-' + boundary)
        self.assertEqual(control.state()['state'], 'RESUME_REQUIRED')
        result = service.execute('gateway-transition-execution.resume', request)['gateway_transition_execution']
        self.assertEqual(result['state'], 'DONE')
        self.assertEqual(result['availability']['public']['state'], 'PUBLIC_LISTENERS_RUNNING')
        selected = base.authority.selected_for_admission(original.http)
        self.assertEqual(selected.profile.selected_release['commit'], target)
        self.assertEqual(base.identity(selected), identity)
        self.assertEqual(len(chain.history(selected)), CYCLE)
        generation = selection.selected(original.shared)
        self.assertEqual(generation._read('activated.json')['generation_sha256'], generation.digest)
        self.assertEqual(base.opening.g.sha(generation.value['target_binding']), base.opening.g.sha(selected.profile.binding()))
        preserved(self, retained); base.public_access(self)
        before = file_hashes([control.root])
        with patch.object(base.opening.g.public.SharedPublic, 'control', side_effect=AssertionError('check starts')):
            checked = service.execute('gateway-transition-execution.check', {'confirmation': planned['confirmation'], 'confirm': True})
        self.assertEqual(checked['gateway_transition_execution']['state'], 'DONE')
        self.assertEqual(before, file_hashes([control.root]))
        base.save('public-cycle-' + str(CYCLE) + '-transfer-proof.json', {**saved, 'status': 'PASS',
            'lease_id': control.profile()['lease_id'], 'generation_sha256': generation.digest,
            'backup_root': str(control.backup.backups(control.profile())), 'execution_root': str(control.root),
            'sigkill_boundary': boundary, 'public_web_mobile_available': True, 'completed_check_read_only': True,
            'phase6_complete': False})


class Restart(unittest.TestCase):
    def test_each_generation_boots_and_renews_after_new_pid1(self):
        service = base.fixture.service(); self.addCleanup(service.close)
        before = json.loads((base.EVIDENCE / ('public-cycle-' + str(CYCLE) + '-before.json')).read_bytes())
        epoch = base.opening.g.mobile.MobileBootRuntime.epoch_identity()
        self.assertNotEqual(epoch, before['epoch']); self.assertEqual(epoch['boot_id'], before['epoch']['boot_id'])
        control = service.gateway_transition_execution
        checked = service.execute('gateway-transition-execution.check', {'confirmation': control.state()['confirmation'], 'confirm': True})
        self.assertTrue(checked['gateway_transition_execution']['availability']['public']['boot_requalified'])
        original = base.opening.g.mobile.MobileBootRuntime(control.profile()['public_source']['mobile'])
        selected = base.authority.selected_for_admission(original.http)
        self.assertEqual(base.identity(selected), before['uuid_sha256'])
        self.assertEqual(selected.profile.selected_release['commit'], TARGETS[CYCLE - 1])
        preserved(self, before['preserved']); base.public_access(self)
        admitted = base.authority.Authority.load(selected, control.backup.backups(control.profile()), control.profile()['lease_id'])
        generation = admitted.public.generation; mobile = generation.readers()[2]; mobile.attach_gateway()
        processes = {role: mobile.process(getattr(mobile, role)) for role in ('foundation', 'gateway')}
        base.opening.g.public.old.command(['/usr/bin/python3.13', '-I', '-B', str(generation.root / 'worker.py'), 'mobile'])
        self.assertEqual(processes, {role: mobile.process(getattr(mobile, role)) for role in processes})
        public = generation.readers()[1]; pid = public.web.systemctl('show', 'https')['MainPID']
        leaves = [public.web.acme_root / 'live/hestia-web/cert.pem', public.shared.acme_root / 'live/hestia-mobile/cert.pem']
        leaf_before = {str(path): path.read_bytes() for path in leaves}
        for argv in (public.web.certbot(renew=True), public.shared.certbot(renew=True)):
            base.opening.g.public.old.command([*argv, '--force-renewal'], timeout=840)
        base.opening.g.public.old.command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password',
            'start', '--', public.web.unit('renew')], timeout=1800)
        self.assertEqual(public.web.systemctl('show', 'https')['MainPID'], pid)
        for path in leaves: self.assertNotEqual(path.read_bytes(), leaf_before[str(path)])
        public.web.certificate(); public.mobile.verify(); base.public_access(self)
        base.save('public-cycle-' + str(CYCLE) + '-restart-proof.json', {'status': 'PASS', 'cycle': CYCLE,
            'new_pid1_same_kernel': True, 'epoch': epoch, 'target': TARGETS[CYCLE - 1],
            'no_mobile_start_replay': True, 'two_actual_acme_renewals_with_successor_worker': True,
            'https_master_preserved': True, 'phase6_complete': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('transfer', 'restart'))
    parser.add_argument('--cycle', required=True, type=int, choices=(1, 2, 3)); args = parser.parse_args(); CYCLE = args.cycle
    if os.environ.get('HESTIA_PUBLIC_COMPOSED_TEST') != '1' or os.geteuid() != 0:
        raise RuntimeError('Disposable opt-in required')
    if Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('Real PID 1 required')
    before = base.quality.snapshot(base.ROOT); acquire = base.activation.a.c.rf.acquire
    label = 'public-cycle-' + str(CYCLE) + '-' + args.phase
    @contextmanager
    def timed(*args, **kwargs):
        with acquire(*args, **kwargs) as held:
            began = held._deadline - 180
            yield held; held.assert_held()
            path = base.EVIDENCE / (label + '-sql-windows.json')
            windows = json.loads(path.read_bytes()) if path.exists() else []
            windows.append({'seconds': time.monotonic() - began}); base.save(path.name, windows)
    with patch.object(base.activation.a.c.rf, 'acquire', timed):
        result = unittest.TextTestRunner(verbosity=2, resultclass=base.EvidenceResult).run(unittest.defaultTestLoader.loadTestsFromTestCase(Transfer if args.phase == 'transfer' else Restart))
    stable = before == base.quality.snapshot(base.ROOT)
    passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    base.save(label + '.json', {'status': 'PASS' if passed else 'FAIL', 'tests': result.testsRun,
        'expected': 1, 'source_stable': stable, 'source_files': len(before), 'phase6_complete': False})
    base.save('SOURCE-MANIFEST-' + label + '.json', before)
    sys.exit(0 if passed else 1)
