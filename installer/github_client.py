"""Read-only GitHub transport. No git process, generic URL, or ambient proxy auth."""
from __future__ import annotations

import hashlib
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import BinaryIO, Callable

from installer.model import ErrorCode, InstallerError, SourceSpec, require, strict_json_loads
from installer.operations import SecretVault

REPOSITORIES = {
    "web": "SepuLeVrai/hestia-nexus-avv",
    "gateway": "SepuLeVrai/hestia-mobile-gateway",
    "apk": "SepuLeVrai/hestia-apk",
}
API = "https://api.github.com"
SECRET_NAME = "github-read"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
CREDENTIAL_TTL = 15 * 60


def credential(value: object) -> str:
    require(type(value) is str and 20 <= len(value) <= 255
            and re.fullmatch(r"[A-Za-z0-9_]+", value) is not None, ErrorCode.SECRET_REJECTED)
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    """HTTPS GET only. A fresh header is scoped to each fixed GitHub destination.

    API archive redirects can contain GitHub's temporary query credential. That
    query is discarded, never requested or persisted. Codeload is authenticated
    with a header at the exact, pinned archive path instead. No other redirect,
    host, alternate port, proxy from the environment, or cookie is accepted.
    """

    def __init__(self, *, opener=None, clock: Callable[[], float] = time.monotonic) -> None:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._opener = opener if opener is not None else urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context))
        self._clock = clock

    def __repr__(self) -> str:
        return "<GitHubClient read-only>"

    @staticmethod
    def _repository(repository: str) -> None:
        require(repository in REPOSITORIES.values())

    @staticmethod
    def _sha(sha: object) -> str:
        require(type(sha) is str and re.fullmatch(r"[0-9a-f]{40}", sha) is not None,
                ErrorCode.GITHUB_INVALID_RESPONSE)
        return sha

    def _open(self, url: str, token: str, *, accept: str = "application/vnd.github+json"):
        credential(token)
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and parsed.netloc in {"api.github.com", "codeload.github.com"}
                and not parsed.query and not parsed.fragment and token not in url,
                ErrorCode.GITHUB_REDIRECT_REJECTED)
        request = urllib.request.Request(url, method="GET", headers={
            "Accept": accept, "User-Agent": "HESTIA-Installer", "X-GitHub-Api-Version": "2026-03-10",
            "Accept-Encoding": "identity",
        })
        request.add_unredirected_header("Authorization", "Bearer " + token)
        try:
            return self._opener.open(request, timeout=15)
        except urllib.error.HTTPError as response:
            if response.code == 302:
                return response  # Caller validates the ONLY supported redirect.
            code = ErrorCode.GITHUB_UNAVAILABLE
            if response.code == 429 or (response.code == 403 and (
                    response.headers.get("X-RateLimit-Remaining") == "0" or response.headers.get("Retry-After"))):
                code = ErrorCode.GITHUB_RATE_LIMITED
            elif response.code in {401, 403, 404}:
                code = ErrorCode.GITHUB_ACCESS_DENIED
            elif 300 <= response.code < 400:
                code = ErrorCode.GITHUB_REDIRECT_REJECTED
            response.close()  # Never read an error body that could echo credentials.
            raise InstallerError(code) from None
        except Exception:
            raise InstallerError(ErrorCode.GITHUB_UNAVAILABLE) from None

    def _copy(self, response, output: BinaryIO, *, limit: int, deadline: float) -> str:
        require(response.status == 200, ErrorCode.GITHUB_INVALID_RESPONSE)
        length = response.headers.get("Content-Length")
        expected = None
        if length is not None:
            require(type(length) is str and re.fullmatch(r"[0-9]{1,12}", length) is not None,
                    ErrorCode.GITHUB_INVALID_RESPONSE)
            expected = int(length)
            require(0 < expected <= limit, ErrorCode.SOURCE_LIMIT)
        require(response.headers.get("Content-Encoding", "identity").lower() == "identity",
                ErrorCode.GITHUB_INVALID_RESPONSE)
        count = 0
        digest = hashlib.sha256()
        try:
            while True:
                require(self._clock() < deadline, ErrorCode.GITHUB_UNAVAILABLE)
                # read1 avoids waiting for a full buffer on a slow-drip response.
                reader = getattr(response, "read1", response.read)
                chunk = reader(min(65536, limit - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                require(count <= limit, ErrorCode.SOURCE_LIMIT)
                output.write(chunk)
                digest.update(chunk)
            require(count > 0 and (expected is None or count == expected), ErrorCode.GITHUB_INVALID_RESPONSE)
        except InstallerError:
            raise
        except Exception:
            raise InstallerError(ErrorCode.GITHUB_UNAVAILABLE) from None
        return digest.hexdigest()

    def _bytes(self, repository: str, suffix: str, token: str, *, accept: str = "application/vnd.github+json",
               limit: int = 1024 * 1024) -> bytes:
        import io
        self._repository(repository)
        out = io.BytesIO()
        with self._open(f"{API}/repos/{repository}{suffix}", token, accept=accept) as response:
            require(response.status != 302, ErrorCode.GITHUB_REDIRECT_REJECTED)
            self._copy(response, out, limit=limit, deadline=self._clock() + 30)
        return out.getvalue()

    def resolve(self, module: str, ref: str, token: str) -> SourceSpec:
        require(type(module) is str and module in REPOSITORIES, ErrorCode.UNSUPPORTED_MODULE)
        SourceSpec(REPOSITORIES[module], ref, "0" * 40).as_dict()
        raw = self._bytes(REPOSITORIES[module], "/commits/" + urllib.parse.quote(ref, safe=""), token,
                          accept="application/vnd.github.sha", limit=4096)
        try:
            sha = raw.decode("ascii").strip()
        except UnicodeError:
            raise InstallerError(ErrorCode.GITHUB_INVALID_RESPONSE) from None
        return SourceSpec(REPOSITORIES[module], ref, self._sha(sha))

    def validate_all(self, token: str) -> dict[str, SourceSpec]:
        credential(token)
        result = {}
        self.validation_checks = {module: "UNCHECKED" for module in REPOSITORIES}
        for module, repository in REPOSITORIES.items():
            try:
                metadata = strict_json_loads(self._bytes(repository, "", token))
                require(type(metadata) is dict and metadata.get("full_name") == repository,
                        ErrorCode.GITHUB_INVALID_RESPONSE)
                # SHA media endpoint requires Contents:read, unlike repo metadata.
                result[module] = self.resolve(module, metadata.get("default_branch"), token)
                self.validation_checks[module] = "ACCESSIBLE"
            except InstallerError as exc:
                self.validation_checks[module] = ("DENIED" if exc.code == ErrorCode.GITHUB_ACCESS_DENIED else "UNAVAILABLE")
                raise
            except Exception:
                self.validation_checks[module] = "UNAVAILABLE"
                raise InstallerError(ErrorCode.GITHUB_INVALID_RESPONSE) from None
        return result

    def download(self, source: SourceSpec, token: str, output: BinaryIO) -> str:
        source.as_dict()
        self._repository(source.repository)
        sha = self._sha(source.commit_sha)
        deadline = self._clock() + 300
        response = self._open(f"{API}/repos/{source.repository}/tarball/{sha}", token)
        with response:
            if response.status == 200:
                return self._copy(response, output, limit=MAX_ARCHIVE_BYTES, deadline=deadline)
            require(response.status == 302, ErrorCode.GITHUB_REDIRECT_REJECTED)
            location = response.headers.get("Location", "")
            require(type(location) is str and len(location) <= 8192 and location.isascii()
                    and all(32 < ord(c) < 127 for c in location) and token not in location,
                    ErrorCode.GITHUB_REDIRECT_REJECTED)
            try:
                parsed = urllib.parse.urlsplit(location)
            except ValueError:
                raise InstallerError(ErrorCode.GITHUB_REDIRECT_REJECTED) from None
            canonical = f"https://codeload.github.com/{source.repository}/legacy.tar.gz/{sha}"
            # Reconstruct from trusted fields. Never follow a signed query string.
            require(parsed.scheme == "https" and parsed.netloc == "codeload.github.com"
                    and parsed.path == urllib.parse.urlsplit(canonical).path and not parsed.fragment,
                    ErrorCode.GITHUB_REDIRECT_REJECTED)
        with self._open(canonical, token, accept="application/octet-stream") as archive:
            require(archive.status != 302, ErrorCode.GITHUB_REDIRECT_REJECTED)
            return self._copy(archive, output, limit=MAX_ARCHIVE_BYTES, deadline=deadline)


class GitHubAccess:
    """Short-lived read credential and validation snapshot, never serialized."""

    def __init__(self, vault: SecretVault, client: GitHubClient | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.vault = vault
        self.client = client if client is not None else GitHubClient()
        self._clock = clock
        self._expires = 0.0
        self._validated: dict[str, SourceSpec] = {}
        self._checks = {module: "UNCHECKED" for module in REPOSITORIES}

    def clear(self) -> None:
        self.vault.delete(SECRET_NAME)
        self._expires = 0.0
        self._validated = {}
        self._checks = {module: "UNCHECKED" for module in REPOSITORIES}

    def token(self) -> str:
        if self._clock() >= self._expires or set(self._validated) != set(REPOSITORIES):
            self.clear()
            raise InstallerError(ErrorCode.SECRET_REQUIRED)
        return self.vault.require(SECRET_NAME)

    def validate(self, value: object) -> dict:
        self.clear()
        token = credential(value)
        try:
            result = self.client.validate_all(token)
            self.vault.put(SECRET_NAME, token)
            self.vault.reject_in({name: spec.as_dict() for name, spec in result.items()})
            self._validated = result
            self._checks = {module: "ACCESSIBLE" for module in REPOSITORIES}
            self._expires = self._clock() + CREDENTIAL_TTL
            return self.status()
        except Exception:
            self.clear()
            candidate = getattr(self.client, "validation_checks", {})
            self._checks = {module: candidate.get(module, "UNCHECKED")
                            if candidate.get(module) in {"ACCESSIBLE", "DENIED", "UNAVAILABLE"}
                            else "UNCHECKED" for module in REPOSITORIES}
            raise

    def status(self) -> dict:
        if self._expires > 0 and self._clock() >= self._expires:
            self.clear()
        checks = [{"module": module, "status": self._checks[module]} for module in REPOSITORIES]
        try:
            self.token()
        except InstallerError:
            # A failed validation has already cleared the secret; retain only
            # fixed per-repository status codes until another validation/clear.
            self._checks = {item["module"]: item["status"] for item in checks}
            return {"ready": False, "repositories": [], "checks": checks}
        return {"ready": True, "checks": checks, "repositories": [
            {"module": name, **spec.as_dict()} for name, spec in self._validated.items()
        ]}

    def select(self, modules: list[str], refs: dict[str, str]) -> dict[str, SourceSpec]:
        require(type(modules) is list and 1 <= len(modules) <= 3)
        require(all(type(m) is str and m in REPOSITORIES for m in modules), ErrorCode.UNSUPPORTED_MODULE)
        require(len(set(modules)) == len(modules))
        require(type(refs) is dict and set(refs) <= set(modules))
        token = self.token()
        result = {}
        for module in sorted(modules):
            current = self._validated[module]
            # Default is the SHA inspected at access validation, not a moving HEAD.
            result[module] = self.client.resolve(module, refs[module], token) if module in refs else current
        self.vault.reject_in({name: source.as_dict() for name, source in result.items()})
        return result
