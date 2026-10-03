#!/usr/bin/env python3
"""Two distinct real Web databases behind the exact Gateway, disposable only.

DEV is an existing managed target. Its fixture is provisioned with the qualified
Web engine at a separate fixed port. Product code only registers and connects
it; no fresh-DEV wizard, phone delivery or production identity cloning is claimed.
"""
import argparse
from dataclasses import replace
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests'), str(Path(__file__).parent)]
import quality
import fcm_fixture
import fcm_mobile_application as fcm
from github_fixture import confirm, DUMMY
from test_application_plan import setup_payload
from installer import application_plan as app, application_activation as activation
from installer import foundation_probe, foundation_drain
from installer import sql_read_fence
from installer.dev_target import DevManagedProfile, DevTarget, digest
from installer.gateway_identity import _b64, _public
from installer.gateway_release import FCM_COMMIT
from installer.model import canonical_bytes

mobile = fcm.mobile
fixture = mobile.fixture
EVIDENCE = fixture.EVIDENCE
PRIVATE = Path('/var/lib/hestia-dev-recipe')
SUBJECT = '11111111-1111-4111-8111-111111111111'
DEVICE = '22222222-2222-4222-8222-222222222222'
READ_FENCE = sql_read_fence.acquire


@contextmanager
def prove_sql_peer(*args, **kwargs):
    """Exercise the genuine PHP rejection before the normal paired acquisition."""
    evidence = EVIDENCE / 'dev-sql-peer-proof.json'
    first = kwargs.get('peer_database') is not None and not evidence.exists()
    if first:
        service = fixture.service()
        try:
            sql = service.mariadb.runtime()
            sql.sql('CREATE DATABASE hestia_unmanaged_peer')
            try:
                try:
                    with READ_FENCE(*args, **kwargs):
                        raise AssertionError('Foreign third schema was admitted')
                except sql_read_fence.SqlReadFenceError as error:
                    assert str(error) == 'SQL_FENCE_SERVER_PROFILE_REJECTED'
            finally: sql.sql('DROP DATABASE hestia_unmanaged_peer')
        finally: service.close()
    with READ_FENCE(*args, **kwargs) as held:
        if first:
            evidence.write_bytes(quality.encode({'registered_dev_admitted': True,
                'foreign_third_schema_rejected': True, 'backup_scope': 'MAIN and Gateway only'}))
        yield held


def prepare_target(main):
    PRIVATE.mkdir(mode=0o700)
    main_instance = main.application.read()['instance']
    original = app.FreshProfile.http
    def isolated(profile, configuration):
        runtime = original(profile, configuration)
        return runtime if profile.instance == main_instance else app.h.HttpRuntime(replace(runtime.spec, port=9084))
    # Only the fixture's placement differs. All provisioning, SQL, source
    # deployment, seals, accounts and service activation use the real engines.
    with patch.object(fixture, 'STATE', PRIVATE / 'state/state.json'), patch.object(app.FreshProfile, 'http', isolated), \
         patch.object(activation.Activation, 'probe_port', lambda active: active.runtime.spec.port):
        service = fixture.service()
        try:
            service.execute('github.validate', {'credential': DUMMY})
            payload = setup_payload(); payload['profile'] = 'fresh-mobile-staged-v2'
            payload['configuration']['hostname'] = 'dev.example.test'
            payload['configuration']['database'].update(name='hestia_dev', user='hestia_dev')
            payload['configuration']['administrator']['first_name'] = 'DEV distinct'
            sql = main.mariadb.runtime()
            payload['credentials'].update(authority_user=sql.authority_user, migration_user=sql.migration_user,
                                          authority_password=fixture.PASSWORD)
            saved = service.execute('web.setup', payload)['application']['draft']
            plan = service.execute('wizard.plan', {'modules': ['web'], 'mode': 'fresh', 'refs': {},
                'application_revision': saved['revision']})['installation']
            assert service.execute('apply', confirm(plan))['installation']['state'] == 'DONE'
            active = service.execute('activation.plan', {'preparation_sha256': plan['plan_sha256']})['activation']['installation']
            assert service.execute('activation.apply', confirm(active))['activation']['installation']['state'] == 'DONE'
            layout = app.FreshProfile.from_draft(saved); http = layout.http(saved['configuration'])
            descriptor = {'version': 2, 'http': {k: str(getattr(http.spec, k)) if k in
                ('root', 'webroot', 'maintenance_directory') else getattr(http.spec, k) for k in
                ('instance', 'root', 'webroot', 'service_user', 'hostname', 'port', 'maintenance_directory')},
                'worker': {'user': layout.worker.user, 'run_root': str(layout.root / 'run'),
                           'state_root': str(layout.root / 'attempts')}}
            target = {'version': 1, 'descriptor': descriptor, 'configuration': DevManagedProfile(descriptor).inspect(),
                'preparation_sha256': plan['plan_sha256'], 'main_configuration_sha256': digest(main.application.read()['configuration']),
                'debug_subjects': [SUBJECT]}
            path = PRIVATE / 'target.json'; path.write_bytes(canonical_bytes(target)); path.chmod(0o600)
        finally: service.close()
    registered = main.foundation.dev_target.register(path)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PRIVATE.rglob('*.json')}
    (EVIDENCE / 'dev-target-registration.json').write_bytes(quality.encode({'target': target,
        'confirmation': registered['confirmation'], 'existing_target_only': True, 'preparation_files': before}))
    return registered['confirmation']


def seed_subjects(service):
    sql = service.mariadb.runtime()
    for database in ('hestia_app', 'hestia_dev'):
        sql.sql(f"UPDATE `{database}`.UserInfo SET mobile_subject_uuid='{SUBJECT}' WHERE email='admin@example.test';"
                f" INSERT INTO `{database}`.Sec_Mobile_Subject(mobile_subject_uuid,id_user,created_at)"
                f" SELECT '{SUBJECT}',id_user,UNIX_TIMESTAMP() FROM `{database}`.UserInfo WHERE email='admin@example.test';")
    key = subprocess.run(['openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256'],
        check=True, capture_output=True, timeout=15).stdout
    (PRIVATE / 'device.pem').write_bytes(key); (PRIVATE / 'device.pem').chmod(0o600)
    jwk = _public(key); encoded = canonical_bytes(jwk).decode(); thumb = _b64(hashlib.sha256(canonical_bytes(jwk)).digest())
    sql.sql(f"INSERT INTO hestia_app.Sec_Mobile_Device(device_id,mobile_subject_uuid,public_key,public_key_thumbprint,"
            f"name,app_version,app_version_code,channel,state,created_at,enrolled_at) VALUES ('{DEVICE}','{SUBJECT}',"
            f"'{encoded}','{thumb}','Synthetic DEV qualification','1.6.3-debug',10603,'debug','active',UNIX_TIMESTAMP(),UNIX_TIMESTAMP());")
    (PRIVATE / 'device.json').write_bytes(canonical_bytes({'thumbprint': thumb})); (PRIVATE / 'device.json').chmod(0o600)


def policy(service, enabled):
    for database in ('hestia_app', 'hestia_dev'):
        service.mariadb.runtime().sql(f"UPDATE `{database}`.App_Config SET valeur='{int(enabled)}' WHERE cle='mobile.foundation.enabled';"
            f"UPDATE `{database}`.App_Config SET valeur='0' WHERE cle IN ('mobile.kill_switch','mobile.minimum_supported_app_version');")


def call(case, path, payload, expected=200):
    connection = http.client.HTTPConnection('127.0.0.1', 9083, timeout=15)
    try:
        connection.request('POST', path, canonical_bytes(payload), {'Content-Type': 'application/json',
            'X-Hestia-Client-IP': '127.0.0.1', 'Connection': 'close'})
        response = connection.getresponse(); reply = json.loads(response.read(65537))
        case.assertEqual(response.status, expected, {'status': response.status, 'error': reply.get('error', {}).get('code')})
        return reply.get('data', reply.get('error'))
    finally: connection.close()


def login(case, environment):
    context = {'device_id': DEVICE, 'environment': environment}
    challenge = call(case, '/v1/auth/challenge', context)
    claims = {'purpose': 'authentication', **context, 'challenge_id': challenge['challenge_id'],
        'challenge': challenge['challenge'], 'public_key_thumbprint': json.loads((PRIVATE / 'device.json').read_bytes())['thumbprint'],
        'iat': int(time.time()), 'app_version': '1.6.3-debug', 'app_version_code': 10603, 'channel': 'debug'}
    signed = (_b64(canonical_bytes({'alg': 'ES256', 'typ': 'hestia-pop+jwt'})) + '.' + _b64(canonical_bytes(claims))).encode()
    der = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(PRIVATE / 'device.pem')], input=signed,
        check=True, capture_output=True, timeout=15).stdout
    proof = signed.decode() + '.' + _b64(foundation_probe.raw_signature(der))
    return call(case, '/v1/auth/complete', {**context, 'challenge_id': challenge['challenge_id'], 'proof': proof})


def context_checks(case, service):
    policy(service, True)
    try:
        main, dev = login(case, 'main'), login(case, 'dev-bastien')
        case.assertNotEqual(main['session_id'], dev['session_id'])
        def access(pair): return {k: pair[k] for k in ('device_id', 'environment', 'access_token')}
        for pair in (main, dev):
            call(case, '/v1/auth/session', access(pair))
            identity = call(case, '/v1/me', access(pair))
            case.assertEqual(identity['environment'], pair['environment'])
            case.assertEqual(identity['mobile_subject_uuid'], SUBJECT)
        case.assertTrue(call(case, '/v1/me', access(dev))['display_name'].startswith('DEV distinct'))
        for pair, wrong in ((main, 'dev-bastien'), (dev, 'main')):
            call(case, '/v1/auth/session', {**access(pair), 'environment': wrong}, 401)
        case.assertEqual(call(case, '/v1/environments', access(main))['environments'], ['main', 'dev-bastien'])
        sql = service.mariadb.runtime()
        case.assertEqual(sql.sql('SELECT COUNT(*) FROM hestia_dev.Sec_Mobile_Device').strip(), '0')
        sql.sql("UPDATE hestia_dev.UserInfo SET actif=0 WHERE email='admin@example.test'")
        try:
            call(case, '/v1/auth/session', access(dev), 403)
            call(case, '/v1/auth/session', access(main))
        finally: sql.sql("UPDATE hestia_dev.UserInfo SET actif=1 WHERE email='admin@example.test'")
        return {'main_authorized': True, 'dev_authorized': True, 'cross_session_rejected': True,
            'dev_account_live_policy': True, 'dev_device_rows': 0}
    finally: policy(service, False)


class Verify(fcm.Verify):
    def test_two_real_contexts_and_crossed_tokens(self):
        service = fixture.service(); self.addCleanup(service.close)
        value = context_checks(self, service)
        _, runtime = service.gateway_service.engine(service.engine.report())
        self.assertEqual(runtime.profile.binding()['version'], 2)
        self.assertEqual(runtime.profile.push['selection']['project_id'], 'hestia-test')
        self.assertEqual(foundation_probe.check_dev(service.gateway.identities, runtime.profile.dev.identity, runtime.profile.main), foundation_probe.DEV_RESULT)
        (EVIDENCE / 'dev-context-proof.json').write_bytes(quality.encode(value))

    def test_dev_maintenance_closes_dev_without_main_fallback(self):
        service = fixture.service(); self.addCleanup(service.close)
        _, runtime = service.gateway_service.engine(service.engine.report()); dev = runtime.profile.dev
        policy(service, True)
        try:
            main, pair = login(self, 'main'), login(self, 'dev-bastien')
            scope, _ = dev.activation.configuration()
            with scope.acquire(confirmed=True, timeout=10) as lease:
                foundation_drain.quiesce(dev, lease)
                call(self, '/v1/auth/session', {k: main[k] for k in ('device_id', 'environment', 'access_token')})
                call(self, '/v1/auth/session', {k: pair[k] for k in ('device_id', 'environment', 'access_token')}, 503)
                self.assertEqual(scope.observe()['state'], 'MAINTENANCE_REQUIRED')
                lease.resume(confirmed=True)
            # Explicit fixture reopen after the stop-only maintenance proof.
            # The product never implicitly restarts this completed service.
            subprocess.run(['systemctl', 'start', dev.unit], check=True, timeout=30)
            deadline = time.monotonic() + 10
            while True:
                try: dev.owned(); break
                except Exception:
                    if time.monotonic() >= deadline: raise
                    time.sleep(.1)
            runtime.owned()
            (EVIDENCE / 'dev-maintenance-proof.json').write_bytes(quality.encode({'main_available': True,
                'dev_unavailable': True, 'fallback': False, 'reopen': 'explicit disposable fixture'}))
        finally: policy(service, False)


class Restart(fcm.Restart):
    def test_dev_units_and_both_contexts_after_new_pid1(self):
        service = fixture.service(); self.addCleanup(service.close)
        _, runtime = service.gateway_service.engine(service.engine.report())
        target = runtime.profile.dev.target; target.serving(); runtime.profile.dev.owned()
        before = json.loads((EVIDENCE / 'dev-before-boot.json').read_bytes())
        after = snapshot(service)
        self.assertNotEqual(before['pid1'], after['pid1'])
        self.assertEqual(before['files'], after['files'])
        for unit, invocation in before['invocations'].items(): self.assertNotEqual(invocation, after['invocations'][unit])
        context_checks(self, service)
        (EVIDENCE / 'dev-after-boot.json').write_bytes(quality.encode(after))


def snapshot(service):
    _, runtime = service.gateway_service.engine(service.engine.report()); dev = runtime.profile.dev
    paths = [service.foundation.dev_target.root / 'target.json',
        *service.gateway.identities.root.glob('*.pem'), service.fcm.store.root / 'server.json',
        *(path for path in dev.files(dev.host().pw_gid))]
    units = [dev.unit, *(dev.target.activation.unit(r) for r in ('php', 'apache', 'timer'))]
    return {'pid1': Path('/proc/1/stat').read_text().rsplit(')', 1)[1].split()[19],
        'files': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'invocations': {u: subprocess.run(['systemctl', 'show', '--value', '--property=InvocationID', u],
            check=True, capture_output=True, text=True, timeout=10).stdout.strip() for u in units}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--phase', required=True, choices=('setup','serve','browser','verify','restart'))
    phase = parser.parse_args().phase
    if os.environ.get('HESTIA_SHARED_APPLICATION_TEST') != '1' or os.geteuid() != 0: raise RuntimeError('Disposable opt-in required')
    if phase != 'browser' and Path('/proc/1/comm').read_text().strip() != 'systemd': raise RuntimeError('Real PID 1 required')
    if phase == 'setup':
        with patch.object(sql_read_fence, 'acquire', prove_sql_peer):
            mobile.setup(gateway_commit=FCM_COMMIT, credential=fcm_fixture.credential(), dev_setup=prepare_target)
        service = fixture.service()
        try: seed_subjects(service)
        finally: service.close()
        sys.exit(0)
    if phase == 'serve': mobile.shared.serve(); sys.exit(0)
    before = quality.snapshot(ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(
        {'browser': mobile.shared.Browser, 'verify': Verify, 'restart': Restart}[phase]))
    if phase == 'verify' and result.wasSuccessful():
        service = fixture.service()
        try: (EVIDENCE / 'dev-before-boot.json').write_bytes(quality.encode(snapshot(service)))
        finally: service.close()
    stable = quality.snapshot(ROOT) == before; expected = {'browser': 1, 'verify': 5, 'restart': 4}[phase]
    passed = result.wasSuccessful() and result.testsRun == expected and not result.skipped and stable
    report = {'suite': 'dev-mobile-application-' + phase, 'status': 'PASS' if passed else 'FAIL', 'tests': result.testsRun,
        'expected': expected, 'errors': len(result.errors), 'failures': len(result.failures), 'skips': len(result.skipped),
        'source_stable': stable, 'source_files': len(before), 'web_source_commit': mobile.source.COMMIT,
        'gateway_source_commit': FCM_COMMIT, 'real_web_gateway': True, 'two_distinct_databases': True,
        'mobile_boot_qualified': passed and phase == 'restart', 'google_authorization_verified': False,
        'phone_delivery_verified': False, 'phase6_complete': False}
    (EVIDENCE / ('dev-mobile-application-' + phase + '.json')).write_bytes(quality.encode(report))
    (EVIDENCE / ('SOURCE-MANIFEST-dev-mobile-application-' + phase + '.json')).write_bytes(quality.encode(before))
    print(json.dumps(report)); sys.exit(0 if passed else 1)
