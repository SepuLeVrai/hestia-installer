#!/usr/bin/env python3
"""Qualify the two authentic binaries' parsers without opening application state.

Quality-only command. Packages must already be available; no network acquisition,
deployment, systemd, SQL migration or credential check is performed by this audit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from installer.gateway_release import release, verify_package
from installer.gateway_transition import COMMITS, MIGRATIONS
from installer.gateway_service_profile import GatewayServiceProfile
from installer.model import canonical_bytes
import test_gateway_transition as fixture
from dev_fixture import target_fixture


def audit(packages):
    rows = []; authenticated = []
    with TemporaryDirectory(prefix='hestia-gateway-compat-') as temporary:
        root = Path(temporary)
        base = fixture.GatewayTransitionTests(); push = base.push()
        main = base.profile(COMMITS[1]); fcm = base.profile(COMMITS[1], push)
        _, _, identity, keys, foundation = target_fixture(root / 'pair')
        paired = GatewayServiceProfile(foundation, identity, keys.root, release_commit=COMMITS[1])
        paired_fcm = GatewayServiceProfile(foundation, identity, keys.root, release_commit=COMMITS[1], push=push)
        profiles = {'main': main, 'main-fcm': fcm, 'main-dev': paired, 'main-dev-fcm': paired_fcm}
        for pin in COMMITS:
            selected = release(pin)
            with packages[pin].open('rb') as stream:
                verified = verify_package(stream, selected)
                with zipfile.ZipFile(stream) as archive:
                    actual = {}
                    for name in MIGRATIONS:
                        raw = archive.read('internal/state/migrations/' + name)
                        actual[name] = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                    if actual != MIGRATIONS: raise ValueError('GATEWAY_MIGRATIONS_DRIFT')
                    binary = archive.read('bin/hestia-mobile-gateway')
            executable = root / pin; executable.write_bytes(binary); executable.chmod(0o500)
            authenticated.append(verified)
            for option, expected in (('--version', selected['version']), ('--schema-version', '6')):
                result = subprocess.run([str(executable), option], capture_output=True, timeout=15, check=False)
                passed = result.returncode == 0 and result.stdout.decode().strip() == expected
                rows.append({'commit': pin, 'case': option, 'status': 'PASS' if passed else 'FAIL'})
            for name, profile in profiles.items():
                path = root / (name + '.json'); path.write_bytes(canonical_bytes(profile.configuration())); path.chmod(0o600)
                result = subprocess.run([str(executable), '--config', str(path), '--check-config'],
                                        cwd=root, capture_output=True, timeout=15, check=False)
                expected = 1 if pin == COMMITS[0] and profile.push is not None else 0
                passed = result.returncode == expected
                rows.append({'commit': pin, 'case': name, 'returncode': result.returncode,
                             'expected_returncode': expected, 'status': 'PASS' if passed else 'FAIL'})
    return {'suite': 'Gateway exact binary configuration compatibility', 'tests': len(rows),
            'status': 'PASS' if all(r['status'] == 'PASS' for r in rows) else 'FAIL',
            'authenticated_packages': authenticated, 'migration_blobs': MIGRATIONS,
            'checks': rows, 'services_started': False, 'sqlite_opened': False,
            'transition_executed': False, 'phase6_complete': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(dict(zip(COMMITS, (args.legacy, args.current))))
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(report['status'], report['tests'], 'authentic binary checks')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__': raise SystemExit(main())
