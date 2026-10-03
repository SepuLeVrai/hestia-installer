"""Synthetic FCM account and inert qualified-release fixture for CI only."""
import json
import subprocess

from gateway_fixture import ArtifactResponses, complete_web
from github_fixture import confirm
from installer.gateway_release import FCM_COMMIT, release


def credential():
    key = subprocess.run(['/usr/bin/openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
        capture_output=True, check=True, timeout=30).stdout.decode()
    return json.dumps({'type': 'service_account', 'project_id': 'hestia-test',
        'client_email': 'sender@credential-project.iam.gserviceaccount.com', 'private_key': key,
        'token_uri': 'https://oauth2.googleapis.com/token'}).encode()


def responses(): return ArtifactResponses(release(FCM_COMMIT))


def prepare(service):
    web = complete_web(service)
    value = service.gateway.execute('plan', {'web_plan_sha256': web['plan_sha256'],
        'public_origin': 'https://mobile.customer.example', 'dev_enabled': False, 'release_commit': FCM_COMMIT})
    value = service.gateway.execute('apply', confirm(value['preparation']))
    assert value['preparation']['state'] == 'DONE'
    return value
