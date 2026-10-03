#!/usr/bin/env python3
"""Explicit v2 fresh, real maintenance cycle and public boot in disposable CI.

The GitHub transport supplies a fully pinned archive. Native SQL, backups,
services, ACME, Web QR creation and Gateway health are never substituted.
Historical recipes keep their original source and frozen boot bundles.
"""
import argparse
from contextlib import ExitStack
import hashlib
import http.cookiejar
import io
import json
import os
from pathlib import Path
import re
import ssl
import sys
import traceback
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import boot_wizard_systemd as boot_fixture
import mariadb_wizard_systemd as fixture
import shared_public_application as shared
from github_fixture import confirm
from test_application_plan import setup_payload
from installer import mobile_web_source as source
from installer import mobile_backup_plan, mobile_preparation_plan, mobile_activation_plan

EVIDENCE = fixture.EVIDENCE
fixture.SOURCE_COMMIT = source.COMMIT


def diagnostic(callback):
    def invoke(*args, **kwargs):
        try: return callback(*args, **kwargs)
        except Exception:
            # Stack frames only; never dump locals, credentials or QR tokens.
            with (EVIDENCE / 'mobile-web-traceback.txt').open('a') as output: traceback.print_exc(file=output)
            raise
    return invoke


def setup(*, gateway_commit=None, credential=None, dev_setup=None):
    boot_fixture.setup(profile='fresh-mobile-staged-v2')
    service = fixture.service()
    try:
        parent = service.engine.report(); draft = service.application.read()
        assert draft['version'] == 2 and service.application.state()['profile'] == 'fresh-mobile-staged-v2'
        planned = service.execute('gateway.plan', {'web_plan_sha256': parent['plan_sha256'],
            'public_origin': shared.ORIGIN, 'dev_enabled': True, 'acquisition': 'package',
            **({'release_commit': gateway_commit} if gateway_commit is not None else {})})['gateway']['preparation']
        raw = Path('/opt/gateway-package.zip').read_bytes()
        gateway = shared.done(service.import_gateway_package(planned['plan_sha256'], io.BytesIO(raw), len(raw))['gateway']['preparation'])
        if credential is not None:
            selected = service.execute('fcm.plan', {'gateway_plan_sha256': gateway['plan_sha256'], 'project_id': 'hestia-test'})['fcm']
            imported = service.import_fcm_credential(selected['confirmation'], io.BytesIO(credential), len(credential))['fcm']
            assert imported['state'] == 'IMPORTED'
            (EVIDENCE / 'fcm-import.json').write_bytes(quality.encode(imported))
        parents = {'web': parent['plan_sha256'], 'activation': service.activation.journal.read()['plan_sha256'], 'gateway': gateway['plan_sha256']}
        dev_confirmation = dev_setup(service) if dev_setup is not None else None
        planned = service.execute('foundation.plan', {'parents': parents,
            **({'dev_confirmation': dev_confirmation} if dev_confirmation is not None else {})})['foundation']['installation']
        foundation = shared.done(service.execute('foundation.apply', confirm(planned))['foundation']['installation'])
        parents['foundation'] = foundation['plan_sha256']
        planned = service.execute('gateway-service.plan', {'parents': parents})['gateway_service']['installation']
        gateway_service = shared.done(service.execute('gateway-service.apply', confirm(planned))['gateway_service']['installation'])
        parents['gateway_service'] = gateway_service['plan_sha256']
        credentials = {'database_password': setup_payload()['credentials']['database_password'],
            'authority_user': service.mariadb.runtime().authority_user, 'authority_password': fixture.PASSWORD}
        proofs = {}
        with ExitStack() as stack:
            for owner, method in ((mobile_backup_plan.native.ProvisionedBackup, 'create_and_verify'),
                    (mobile_preparation_plan.NativePreparation, 'execute'), (mobile_activation_plan.native, 'execute')):
                stack.enter_context(patch.object(owner, method, diagnostic(getattr(owner, method))))
            for action, key in (('mobile-backup', 'mobile_backup'), ('mobile-preparation', 'mobile_preparation'), ('mobile-activation', 'mobile_activation')):
                planned = service.execute(action + '.plan', {'parents': parents})[key]
                result = service.execute(action + '.apply', {'confirm': True, 'confirmation': planned['confirmation'],
                    'credentials': credentials, 'allow_global_read_lock': True})[key]
                assert result['state'] == 'DONE', {k: result[k] for k in ('state', 'last_error_redacted')}
                proofs[key] = result
                (EVIDENCE / 'mobile-web-maintenance.json').write_bytes(quality.encode(proofs))
        checked = service.execute('mobile-activation.check', {'confirm': True,
            'confirmation': proofs['mobile_activation']['confirmation']})['mobile_activation']
        assert checked['availability']['state'] == 'LOCAL_SERVICES_AVAILABLE'
        assert checked['availability']['login_page'] is True
        # New profile uses its own exact bundle; the old frozen recipe remains separate.
        planned = service.execute('boot.plan', {'activation_sha256': service.activation.journal.read()['plan_sha256']})['boot']['installation']
        shared.done(service.execute('boot.apply', confirm(planned))['boot']['installation'])
        planned = service.execute('acme-packages.install.plan', {'acquisition_sha256': service.acme_packages.journals['acquire'].read()['plan_sha256']})['acme_packages']['installation']
        shared.done(service.execute('acme-packages.install.apply', confirm(planned))['acme_packages']['installation'])
        planned = service.execute('public-tls.plan', {'acme_sha256': service.acme_packages.journals['install'].read()['plan_sha256'],
            'choices': {'email': 'operator@example.test', 'access': 'allowlist', 'networks': ['172.30.85.10/32']}})['public_tls']['installation']
        shared.done(service.execute('public-tls.apply', confirm(planned))['public_tls']['installation'])
        paths = [service.engine.journal.path, service.activation.journal.path, service.boot.journal.path,
            service.public_tls.journal.path, service.gateway.journal.path, service.foundation.journal.path,
            service.gateway_service.journal.path, *service.gateway.identities.root.glob('*.pem')]
        if credential is not None: paths.extend(service.fcm.store.root / name for name in ('profile.json', 'receipt.json', 'server.json'))
        saved = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        (EVIDENCE / 'shared-application-parents.json').write_bytes(quality.encode(saved))
    finally: service.close()


def verify_web_mobile(case):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), urllib.request.HTTPCookieProcessor(jar))
    origin = 'https://hestia.example.test'
    with opener.open(origin + '/login.php', timeout=15) as response: body = response.read().decode()
    token = re.search(r'name="csrf_token" value="([a-f0-9]+)"', body).group(1)
    password = setup_payload()['credentials']['admin_password']
    payload = urllib.parse.urlencode({'csrf_token': token, 'identifier': 'admin@example.test', 'password': password}).encode()
    with opener.open(origin + '/login.php', data=payload, timeout=15) as response:
        case.assertNotIn('/login.php', response.geturl()); body = response.read().decode()
    token = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', body).group(1)
    def request(action, **fields):
        data = urllib.parse.urlencode({'csrf_token': token, 'action': action, **fields}).encode()
        req = urllib.request.Request(origin + '/ajax/mobile_devices.php', data=data, headers={'Origin': origin})
        with opener.open(req, timeout=15) as response:
            case.assertEqual(response.status, 200); result = json.loads(response.read())
        case.assertIs(result['ok'], True)
        return result['data']
    case.assertIs(request('status')['gateway_online'], True)
    request('policy', enabled='1', kill_switch='0', minimum_supported_app_version='0')
    try:
        grant = request('create_enrollment', password=password, public_origin='https://untrusted.example.test')
        case.assertEqual(grant['qr']['gateway_url'], shared.ORIGIN)
        case.assertEqual(grant['bootstrap_url'], shared.ORIGIN + '/mobile/bootstrap#v1.' + grant['qr']['enrollment_token'])
        case.assertNotIn(grant['qr']['enrollment_token'], grant['bootstrap_url'].split('#', 1)[0])
    finally:
        request('cancel_enrollment')
        request('policy', enabled='0', kill_switch='0', minimum_supported_app_version='0')
    with opener.open(origin + '/logout.php', timeout=15) as response: case.assertIn('/login.php', response.geturl())


class Verify(shared.Verify):
    def test_v2_private_source_binding_and_authenticated_qr_health(self):
        service = fixture.service(); self.addCleanup(service.close)
        draft = service.application.read(); self.assertEqual(draft['version'], 2)
        _, runtime = service.foundation.engine(service.engine.report())
        self.assertEqual(runtime.web.source_commit, source.COMMIT)
        config = json.loads((runtime.root / 'main.json').read_bytes())
        self.assertEqual(config['public_origin'], shared.ORIGIN); self.assertEqual(config['gateway_port'], 9083)
        proofs = json.loads((EVIDENCE / 'mobile-web-maintenance.json').read_bytes())
        self.assertEqual(set(proofs), {'mobile_backup', 'mobile_preparation', 'mobile_activation'})
        for value in proofs.values(): self.assertEqual(value['state'], 'DONE')
        verify_web_mobile(self)


class Restart(shared.Restart):
    def test_v2_authenticated_qr_and_gateway_health_after_new_pid1(self):
        verify_web_mobile(self)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('setup', 'serve', 'browser', 'verify', 'restart'))
    phase = parser.parse_args().phase
    if os.environ.get('HESTIA_SHARED_APPLICATION_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Disposable opt-in required')
    if phase != 'browser' and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('Real PID 1 required')
    if phase == 'setup': setup(); sys.exit(0)
    if phase == 'serve': shared.serve(); sys.exit(0)
    before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(
        {'browser': shared.Browser, 'verify': Verify, 'restart': Restart}[phase]))
    stable = quality.snapshot(ROOT) == before; expected = 1 if phase == 'browser' else 2
    passed = result.wasSuccessful() and result.testsRun == expected and not result.skipped and stable
    report = {'suite': 'mobile-web-application-' + phase, 'status': 'PASS' if passed else 'FAIL', 'tests': result.testsRun, 'expected': expected,
        'errors': len(result.errors), 'failures': len(result.failures), 'skips': len(result.skipped), 'source_stable': stable, 'source_files': len(before),
        'web_source_commit': source.COMMIT, 'real_web_gateway': True, 'acme': 'private Pebble',
        'mobile_boot_qualified': passed and phase == 'restart', 'phase6_complete': False}
    (EVIDENCE / ('mobile-web-application-' + phase + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-mobile-web-application-' + phase + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
