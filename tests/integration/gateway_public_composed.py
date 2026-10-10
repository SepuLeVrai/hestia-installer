#!/usr/bin/env python3
"""First public generation transfer on independently provisioned Debian hosts.

Real SQL, TLS, systemd, native fences and Gateway processes. Fault injection
kills the coordinator after native effects, never substitutes observations.
"""
import argparse
from contextlib import contextmanager, closing
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import time
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import shared_public_application as shared
import mariadb_wizard_systemd as fixture
from github_fixture import confirm
from test_application_plan import setup_payload
from installer import gateway_transition_execution as cockpit, gateway_resume_authority as authority
from installer import gateway_public_opening as opening, mobile_activation_admission as activation
from installer.gateway_transition import LEGACY_COMMIT, FCM_COMMIT

EVIDENCE = Path('/evidence')
DIRECTION = os.environ.get('HESTIA_TRANSITION_CASE')
SOURCE, TARGET = (LEGACY_COMMIT, FCM_COMMIT) if DIRECTION == 'upgrade' else (FCM_COMMIT, LEGACY_COMMIT)


def save(name, value): (EVIDENCE / name).write_bytes(quality.encode(value))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(runtime):
    with closing(sqlite3.connect(runtime.profile.state.as_uri() + '/gateway.db?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]
        assert db.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall() == [(i,) for i in range(1, 7)]
        rows = db.execute('SELECT installation_uuid FROM gateway_metadata WHERE singleton=1').fetchall()
        assert len(rows) == 1
        return hashlib.sha256(rows[0][0].encode()).hexdigest()


def kill(action):
    pid = os.fork()
    if pid == 0:
        try: action()
        except BaseException as error:
            chain = []; current = error
            while current is not None and len(chain) < 12:
                code = str(current)
                chain.append({'type': type(current).__name__, 'code': code if re.fullmatch('[A-Z][A-Z0-9_]{1,100}', code) else 'REDACTED',
                    'frames': [{'file': Path(row.filename).name, 'line': row.lineno, 'function': row.name}
                               for row in traceback.extract_tb(current.__traceback__)]})
                current = current.__context__
            save('public-child-failure.json', chain); os._exit(98)
        os._exit(97)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        found, status = os.waitpid(pid, os.WNOHANG)
        if found:
            assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, status
            return
        time.sleep(.1)
    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
    raise AssertionError('Native public composition timeout')


class Transfer(unittest.TestCase):
    def test_public_transition_and_lost_native_replies(self):
        service = fixture.service(); self.addCleanup(service.close)
        parent = service.engine.report()
        _, source = service.gateway_service.engine(parent)
        self.assertIsNone(source.profile.dev); self.assertIsNone(source.profile.push)
        self.assertEqual(source.profile.selected_release['commit'], SOURCE)
        old_boot = opening.g.mobile.MobileBootRuntime(service.mobile_boot._read('profile.json'))
        old_boot.live()
        roots = (old_boot.root, old_boot.shared.root, old_boot.shared.boot.root)
        preserved = {str(p): digest(p) for root in roots for p in root.rglob('*')
                     if p.is_file() and not p.is_symlink() and p.name != '.transaction.lock'}
        for p in (*service.gateway.identities.root.glob('*.pem'), service.shared_public.journal.path,
                  service.mobile_boot.journal.path, service.gateway_service.journal.path,
                  source.profile.config, source.fragment): preserved[str(p)] = digest(p)
        for profile, name in ((old_boot.shared.web, 'hestia-web'), (old_boot.shared.shared, 'hestia-mobile')):
            for p in (profile.acme_root / 'live' / name).iterdir():
                if p.is_file(): preserved[str(p)] = digest(p)
        saved = {'preserved': preserved, 'uuid_sha256': identity(source), 'epoch': old_boot.epoch_identity(),
                 'source': SOURCE, 'target': TARGET, 'direction': DIRECTION}
        save('public-before-transfer.json', saved)
        parents = {'web': parent['plan_sha256'], **{name: getattr(service, name).journal.read()['plan_sha256']
                    for name in ('activation', 'gateway', 'foundation', 'gateway_service')}}
        values = {'database_password': setup_payload()['credentials']['database_password'],
                  'authority_user': service.mariadb.runtime().authority_user, 'authority_password': fixture.PASSWORD}
        backup = service.execute('mobile-backup.plan', {'parents': parents})['mobile_backup']
        result = service.execute('mobile-backup.apply', {'confirmation': backup['confirmation'], 'confirm': True,
             'credentials': values, 'allow_global_read_lock': True})['mobile_backup']
        self.assertEqual(result['state'], 'DONE', result)
        planned = service.execute('gateway-transition.plan', {'source_plan_sha256': parents['gateway_service'],
            'target_commit': TARGET, 'direction': DIRECTION})['gateway_transition']
        control = service.gateway_transition_execution
        planned = service.execute('gateway-transition-execution.plan', {'transition_sha256': planned['confirmation']})['gateway_transition_execution']
        self.assertEqual(planned['profile']['policy'], cockpit.PUBLIC_POLICY)
        self.assertEqual([r['stage'] for r in planned['steps']], list(cockpit.PUBLIC_STAGES))
        raw = Path('/opt/gateway-target-package.zip' if TARGET == FCM_COMMIT else '/opt/gateway-package.zip').read_bytes()
        control.import_package(planned['confirmation'], io.BytesIO(raw), len(raw))
        request = {'confirmation': planned['confirmation'], 'confirm': True, 'credentials': values, 'allow_global_read_lock': True}
        original_write = type(control)._write
        boundaries = []
        for stage in ('public-transfer', 'admission'):
            def interrupted():
                def write(instance, name, value):
                    if instance.root == control.root and name == stage + '.done.json': os.kill(os.getpid(), signal.SIGKILL)
                    return original_write(instance, name, value)
                with patch.object(type(control), '_write', write):
                    service.execute('gateway-transition-execution.' + ('apply' if stage == 'public-transfer' else 'resume'), request)
            kill(interrupted); boundaries.append(stage)
            self.assertEqual(control.state()['state'], 'RESUME_REQUIRED')
        original_put = opening.f._put
        for role in ('http', 'https', 'timer'):
            def interrupted():
                def put(fd, name, value):
                    if name == role + '.done.json' and isinstance(value, dict) and 'observed' in value:
                        os.kill(os.getpid(), signal.SIGKILL)
                    return original_put(fd, name, value)
                with patch.object(opening.f, '_put', put): service.execute('gateway-transition-execution.resume', request)
            kill(interrupted); boundaries.append(role + '-start-before-receipt')
        result = service.execute('gateway-transition-execution.resume', request)['gateway_transition_execution']
        self.assertEqual(result['state'], 'DONE', result)
        for path, sha in preserved.items(): self.assertEqual(digest(Path(path)), sha, path)
        self.assertEqual(result['availability']['public']['state'], 'PUBLIC_LISTENERS_RUNNING')
        self.assertFalse(result['availability']['public']['boot_requalified'])
        selected = authority.selected_for_admission(old_boot.http)
        self.assertEqual(identity(selected), saved['uuid_sha256'])
        self.assertEqual(selected.profile.selected_release['commit'], TARGET)
        shared.public.login(self); self.assertEqual(shared.mobile_request()[0], 200)
        before = {str(p): (digest(p), p.stat().st_mtime_ns) for p in control.root.rglob('*') if p.is_file()}
        with patch.object(opening.g.public.SharedPublic.control, side_effect=AssertionError('check starts')):
            checked = service.execute('gateway-transition-execution.check', {'confirmation': planned['confirmation'], 'confirm': True})['gateway_transition_execution']
        self.assertEqual(checked['state'], 'DONE')
        self.assertEqual(before, {str(p): (digest(p), p.stat().st_mtime_ns) for p in control.root.rglob('*') if p.is_file()})
        save('public-transfer-proof.json', {'status': 'PASS', **saved, 'sigkill_boundaries': boundaries,
            'public_web_mobile_available': True, 'completed_check_read_only': True, 'phase6_complete': False})


class Restart(unittest.TestCase):
    def test_new_pid1_boots_successors_and_preserves_source(self):
        service = fixture.service(); self.addCleanup(service.close)
        before = json.loads((EVIDENCE / 'public-before-transfer.json').read_bytes())
        current = opening.g.mobile.MobileBootRuntime.epoch_identity()
        self.assertNotEqual(current, before['epoch'])
        self.assertEqual(current['boot_id'], before['epoch']['boot_id'])
        control = service.gateway_transition_execution
        checked = service.execute('gateway-transition-execution.check', {'confirmation': control.state()['confirmation'], 'confirm': True})['gateway_transition_execution']
        self.assertEqual(checked['state'], 'DONE')
        self.assertTrue(checked['availability']['public']['boot_requalified'])
        original = opening.g.mobile.MobileBootRuntime(control.profile()['public_source']['mobile'])
        selected = authority.selected_for_admission(original.http)
        self.assertEqual(identity(selected), before['uuid_sha256'])
        self.assertEqual(selected.profile.selected_release['commit'], TARGET)
        for path, sha in before['preserved'].items(): self.assertEqual(digest(Path(path)), sha, path)
        shared.public.login(self); self.assertEqual(shared.mobile_request()[0], 200)
        admitted = authority.Authority.load(selected, control.backup.backups(control.profile()), control.profile()['lease_id'])
        generation = admitted.public.generation; mobile = generation.readers()[2]; mobile.attach_gateway()
        processes = {role: mobile.process(getattr(mobile, role)) for role in ('foundation', 'gateway')}
        opening.g.public.old.command(['/usr/bin/python3.13', '-I', '-B', str(generation.root / 'worker.py'), 'mobile'])
        self.assertEqual(processes, {role: mobile.process(getattr(mobile, role)) for role in processes})
        save('public-restart-proof.json', {'status': 'PASS', 'new_pid1_same_kernel': True,
             'epoch': current, 'target': TARGET, 'no_mobile_start_replay': True, 'phase6_complete': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('setup', 'transfer', 'restart'))
    phase = parser.parse_args().phase
    if os.environ.get('HESTIA_PUBLIC_COMPOSED_TEST') != '1' or DIRECTION not in ('upgrade', 'rollback') or os.geteuid() != 0:
        raise RuntimeError('Disposable opt-in and direction required')
    if Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('Real PID 1 required')
    if phase == 'setup': shared.setup(dev_enabled=False, release_commit=SOURCE); sys.exit(0)
    before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Transfer if phase == 'transfer' else Restart))
    stable = before == quality.snapshot(ROOT)
    passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped and stable
    save('public-composed-' + phase + '.json', {'status': 'PASS' if passed else 'FAIL', 'tests': result.testsRun,
         'expected': 1, 'source_stable': stable, 'source_files': len(before), 'phase6_complete': False})
    save('SOURCE-MANIFEST-public-composed-' + phase + '.json', before)
    sys.exit(0 if passed else 1)
