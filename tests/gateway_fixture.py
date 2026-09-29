"""Small inert ZIP and GitHub responses. Never a real deployable binary."""
import io
import json
import stat
import zipfile
from unittest.mock import patch

from installer.gateway_release import release, sha
from installer.model import Receipt, aggregate
from github_fixture import DUMMY, Response


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in files:
            entry = zipfile.ZipInfo(name); entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(entry, data)
    return output.getvalue()


def packages():
    selected = release()
    files = [('VERSION', (selected['version'] + '\n').encode(), 0o644),
             ('bin/hestia-mobile-gateway', b'INERT-TEST-FIXTURE-NO-EXECUTION\n', 0o755)]
    sums = ''.join(sha(data) + '  ' + name + '\n' for name, data, mode in files).encode()
    files.append(('SHA256SUMS', sums, 0o644))
    package = zip_bytes(files)
    artifact = zip_bytes([(selected['member'], package, 0o644)])
    selected.update(package_sha256=sha(package), package_bytes=len(package),
                    binary_sha256=sha(files[1][1]), files=len(files), unpacked_bytes=sum(len(f[1]) for f in files),
                    artifact_bytes=len(artifact), artifact_sha256=sha(artifact))
    return selected, package, artifact


class ArtifactResponses:
    def __init__(self):
        self.selected, self.package, self.artifact = packages(); s = self.selected
        self.run = {'id': s['run_id'], 'head_sha': s['commit'], 'status': 'completed', 'conclusion': 'success',
                    'event': 'push', 'path': '.github/workflows/quality.yml',
                    'head_repository': {'id': s['repository_id'], 'full_name': s['repository']}}
        self.metadata = {'id': s['artifact_id'], 'name': s['artifact_name'], 'size_in_bytes': s['artifact_bytes'],
                         'expired': False, 'digest': 'sha256:' + s['artifact_sha256'],
                         'workflow_run': {'id': s['run_id'], 'head_sha': s['commit'],
                             'repository_id': s['repository_id'], 'head_repository_id': s['repository_id']}}
        self.location = 'https://productionresultssa1.blob.core.windows.net/actions/fixture.zip?sig=ephemeral-fixture'
        self.downloads = 0; self.fail = False

    def __call__(self, request):
        url = request.full_url
        if '/actions/runs/' in url: return Response(json.dumps(self.run).encode())
        if '/actions/artifacts/' in url:
            if url.endswith('/zip'): return Response(status=302, headers={'Location': self.location})
            return Response(json.dumps(self.metadata).encode())
        if url == self.location:
            self.downloads += 1
            if self.fail: raise OSError('PRIVATE RESPONSE ' + DUMMY)
            return Response(self.artifact)
        return None


def complete_web(service):
    from test_application_plan import setup_payload
    value = service.application.save(setup_payload())
    service.execute('github.validate', {'credential': DUMMY})
    with patch('installer.application_plan.HostPrerequisites.check'):
        document = service.application.plan(value['revision'])
    document.update(state='DONE', approved_plan_sha256=document['plan_sha256'], revision=1)
    for spec, record in zip(document['plan']['steps'], document['steps']):
        record.update(state='DONE', phase='done', attempts=1, evidence=Receipt(created_resources=tuple(
            r['name'] for r in spec['resources'] if not r['preexisting'])).as_dict())
    document.update(aggregate(document))
    with service.engine.journal.locked() as locked: locked.write(document, expected_revision=0)
    return document
