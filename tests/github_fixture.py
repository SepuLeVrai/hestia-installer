"""Offline GitHub fixture: no credential, repository, or network from the environment."""
import io
import json
import tarfile
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path

from installer.engine import TransactionEngine
from installer.github_client import GitHubAccess, GitHubClient, REPOSITORIES
from installer.github_sources import GitHubAcquisition
from installer.operations import default_registry
from installer.service import TransactionService
from installer.transaction import StateJournal

DUMMY = "fixture_" + "z" * 40
SHAS = {"web": "a" * 40, "gateway": "b" * 40, "apk": "c" * 40}


def tar_bytes(module="web", sha=None, entries=None, *, pax_headers=None):
    sha = sha or SHAS[module]
    top = REPOSITORIES[module].replace("/", "-") + "-" + sha[:7]
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", pax_headers=pax_headers or {"comment": sha}) as archive:
        root = tarfile.TarInfo(top)
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        entries = entries if entries is not None else [("README.md", b"HESTIA sources\n", 0o644), ("bin/start.sh", b"echo hestia\n", 0o755)]
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                archive.addfile(entry)
                continue
            name, data, mode = entry
            member = tarfile.TarInfo(top + "/" + name)
            member.size = len(data)
            member.mode = mode
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, data=b"", status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value


class FakeGitHub:
    def __init__(self):
        self.requests = []
        self.archive_requests = []
        self.refs = dict(SHAS)
        self.override = None
        self.archive_override = None
        self.block = None

    def open(self, request, timeout):
        self.requests.append(request)
        if self.override:
            result = self.override(request)
            if result is not None:
                return result
        url = urllib.parse.urlsplit(request.full_url)
        if url.hostname == "api.github.com":
            prefix = "/repos/"
            for module, repo in REPOSITORIES.items():
                path = prefix + repo
                if url.path == path:
                    return Response(json.dumps({"full_name": repo, "default_branch": "main"}).encode())
                if url.path.startswith(path + "/commits/"):
                    ref = urllib.parse.unquote(url.path[len(path + "/commits/"):])
                    sha = ref if len(ref) == 40 else self.refs[module]
                    return Response(sha.encode())
                if url.path.startswith(path + "/tarball/"):
                    sha = url.path.rsplit("/", 1)[1]
                    return Response(status=302, headers={"Location": f"https://codeload.github.com/{repo}/legacy.tar.gz/{sha}?token=fixture-download-capability"})
        if url.hostname == "codeload.github.com":
            for module, repo in REPOSITORIES.items():
                if url.path.startswith("/" + repo + "/legacy.tar.gz/"):
                    self.archive_requests.append((module, url.path.rsplit("/", 1)[1]))
                    if self.block:
                        self.block()
                    return Response(self.archive_override if self.archive_override is not None else tar_bytes(module, self.archive_requests[-1][1]))
        raise AssertionError("Unexpected fixture endpoint")


def make_service(root, *, fake=None, fault_hook=None):
    fake = fake or FakeGitHub()
    engine = TransactionEngine(StateJournal(Path(root) / "private" / "state.json"), default_registry(), fault_hook=fault_hook)
    access = GitHubAccess(engine.secrets, GitHubClient(opener=fake))
    service = TransactionService(engine, github=GitHubAcquisition(engine, access))
    return service, fake


def plan_sources(service, modules=None, mode="fresh"):
    service.execute("github.validate", {"credential": DUMMY})
    return service.execute("github.plan", {"modules": modules or ["web"], "refs": {}, "mode": mode})["installation"]


def confirm(document, **extra):
    return {"confirm": True, "confirmation": document["plan_sha256"], **extra}
