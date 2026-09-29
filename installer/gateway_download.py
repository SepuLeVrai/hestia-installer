"""Pinned Actions artifact transport, distinct from authenticated source tarballs."""
import re
import urllib.error
import urllib.parse
import urllib.request

from installer.github_client import API
from installer.model import ErrorCode, InstallerError, require, strict_json_loads


def download(client, selected, token, output):
    repository = selected['repository']
    run = strict_json_loads(client._bytes(repository, '/actions/runs/' + str(selected['run_id']), token))
    artifact = strict_json_loads(client._bytes(repository, '/actions/artifacts/' + str(selected['artifact_id']), token))
    require(type(run) is dict and type(artifact) is dict, ErrorCode.GITHUB_INVALID_RESPONSE)
    require(run.get('id') == selected['run_id'] and run.get('head_sha') == selected['commit']
            and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and run.get('event') == 'push' and run.get('path') == '.github/workflows/quality.yml'
            and type(run.get('head_repository')) is dict
            and run['head_repository'].get('id') == selected['repository_id']
            and run['head_repository'].get('full_name') == repository, ErrorCode.SOURCE_DRIFT)
    require(artifact.get('id') == selected['artifact_id'] and artifact.get('name') == selected['artifact_name']
            and artifact.get('size_in_bytes') == selected['artifact_bytes'] and artifact.get('expired') is False
            and artifact.get('digest') == 'sha256:' + selected['artifact_sha256']
            and type(artifact.get('workflow_run')) is dict, ErrorCode.SOURCE_DRIFT)
    workflow = artifact['workflow_run']
    require(all(workflow.get(k) == v for k, v in {
        'id': selected['run_id'], 'head_sha': selected['commit'],
        'repository_id': selected['repository_id'], 'head_repository_id': selected['repository_id'],
    }.items()), ErrorCode.SOURCE_DRIFT)
    url = API + '/repos/' + repository + '/actions/artifacts/' + str(selected['artifact_id']) + '/zip'
    with client._open(url, token, accept='application/octet-stream') as response:
        require(response.status == 302, ErrorCode.GITHUB_REDIRECT_REJECTED)
        location = response.headers.get('Location', '')
        require(type(location) is str and 0 < len(location) <= 8192 and location.isascii()
                and all(32 < ord(c) < 127 for c in location) and token not in location and '\\' not in location,
                ErrorCode.GITHUB_REDIRECT_REJECTED)
        try: parsed = urllib.parse.urlsplit(location)
        except ValueError: raise InstallerError(ErrorCode.GITHUB_REDIRECT_REJECTED) from None
        # GitHub Actions serves artifact blobs via signed, short-lived Azure
        # URLs. Never forward Authorization/cookies/referer, follow another hop,
        # persist this capability, or accept a URL supplied by a browser payload.
        require(parsed.scheme == 'https' and re.fullmatch(r'[a-z0-9]+\.blob\.core\.windows\.net', parsed.netloc)
                and parsed.path.startswith('/') and not parsed.fragment and parsed.query,
                ErrorCode.GITHUB_REDIRECT_REJECTED)
    request = urllib.request.Request(location, method='GET', headers={
        'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity', 'User-Agent': 'HESTIA-Installer'})
    try:
        with client._opener.open(request, timeout=15) as response:
            require(response.status == 200, ErrorCode.GITHUB_REDIRECT_REJECTED)
            result = client._copy(response, output, limit=selected['artifact_bytes'], deadline=client._clock() + 300)
    except InstallerError: raise
    except urllib.error.HTTPError as error:
        error.close()
        raise InstallerError(ErrorCode.GITHUB_UNAVAILABLE) from None
    except Exception: raise InstallerError(ErrorCode.GITHUB_UNAVAILABLE) from None
    require(result == selected['artifact_sha256'], ErrorCode.SOURCE_DRIFT)
