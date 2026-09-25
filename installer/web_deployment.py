"""Exclusive deployment of the complete pinned Web tree, before finalization.

No PHP execution, database access, writable data conversion or service start.
Partial copies remain reserved for manual recovery; no existing tree is adopted.
"""
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
import time

from installer import http_runtime as h
from installer.model import Receipt, ResourceSpec, StepSpec
from installer.operations import Operation, Recovery, RecoveryDecision

WEB_TREE = 'aaac278270e0fd1169396945916dfe997ae078bf'
WEB_FILES = 1840
MAX_ENTRIES = 10000
MAX_BYTES = 256 * 1024 * 1024
MAX_SECONDS = 45


class WebDeploymentError(RuntimeError):
    """Non-secret closed diagnostic."""


def require(ok, code='WEB_DEPLOYMENT_REJECTED'):
    if not ok: raise WebDeploymentError(code)


@dataclass(frozen=True)
class DeploymentSpec:
    source: Path = field(repr=False)
    target: Path = field(repr=False)
    journal: Path = field(repr=False)
    repository: str = h.p.WEB_REPOSITORY
    commit: str = h.f.WEB_COMMIT

    def __post_init__(self):
        paths = [h._path(x, dots=True) for x in (self.source, self.target, self.journal)]
        require(self.repository == h.p.WEB_REPOSITORY and self.commit == h.f.WEB_COMMIT, 'SOURCE_PIN_MISMATCH')
        require(paths[1].startswith(('/srv/', '/var/www/')) and paths[2].startswith('/var/lib/'))
        for index, path in enumerate((self.source, self.target, self.journal)):
            require(len(path.parts) >= 3)
            for other in (self.source, self.target, self.journal)[index + 1:]:
                require(path != other and path not in other.parents and other not in path.parents)


def _object(kind, data):
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).digest()


def _scan(root, *, deployed=False):
    """Recompute Git objects from bytes; never invoke git or execute the source."""
    files, directories = {}, []
    count = total = 0; deadline = time.monotonic() + MAX_SECONDS

    def visit(path, depth):
        nonlocal count, total
        require(depth <= 64 and time.monotonic() <= deadline, 'WEB_DEPLOYMENT_SOURCE_LIMIT')
        entries = []
        with h.fs._directory(path) as fd:
            info = os.fstat(fd)
            if deployed: require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o755))
            for name in sorted(os.listdir(fd)):
                require(name not in ('.', '..', '.git') and len(os.fsencode(name)) <= 255
                        and '\\' not in name and not any(ord(c) < 32 or ord(c) == 127 for c in name))
                count += 1; require(count <= MAX_ENTRIES and time.monotonic() <= deadline, 'WEB_DEPLOYMENT_SOURCE_LIMIT')
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                relative = (path / name).relative_to(root).as_posix()
                require(info.st_uid == 0 and not info.st_mode & 0o7022, 'WEB_DEPLOYMENT_SOURCE_UNSAFE')
                if stat.S_ISDIR(info.st_mode):
                    directories.append(relative)
                    digest = visit(path / name, depth + 1)
                    mode, sortname = b'40000', os.fsencode(name) + b'/'
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'WEB_DEPLOYMENT_SOURCE_UNSAFE')
                    actual_mode = stat.S_IMODE(info.st_mode)
                    data = h.f._read(fd, name, info.st_gid, mode=actual_mode, limit=h.p.MAX_FILE)
                    total += len(data); require(total <= MAX_BYTES, 'WEB_DEPLOYMENT_SOURCE_LIMIT')
                    executable = bool(info.st_mode & 0o111)
                    permission = 0o755 if executable else 0o644
                    if deployed: require(info.st_gid == 0 and actual_mode == permission)
                    files[relative] = {'sha256': h.f._sha(data), 'bytes': len(data), 'mode': permission}
                    digest = _object('blob', data)
                    mode, sortname = (b'100755' if executable else b'100644'), os.fsencode(name)
                entries.append((sortname, mode + b' ' + os.fsencode(name) + b'\0' + digest))
        return _object('tree', b''.join(value for _, value in sorted(entries)))

    tree = visit(root, 0).hex()
    require(tree == WEB_TREE and len(files) == WEB_FILES, 'SOURCE_PIN_MISMATCH')
    return {'tree': tree, 'files': files, 'directories': sorted(directories), 'bytes': total}


class WebDeployment:
    def __init__(self, spec):
        require(type(spec) is DeploymentSpec)
        self.spec = spec

    def __repr__(self): return '<WebDeployment exclusive pinned source copy>'

    def _plan(self, source):
        return h.p._json({'version': 1, 'source': str(self.spec.source), 'target': str(self.spec.target),
            'journal': str(self.spec.journal), 'repository': self.spec.repository, 'commit': self.spec.commit,
            'snapshot': source})

    def prepare(self):
        try:
            require(os.getuid() == os.geteuid() == 0, 'WEB_DEPLOYMENT_ROOT_REQUIRED')
            for path in (self.spec.target, self.spec.journal):
                with h.fs._directory(path.parent) as fd: h.fs._absent(fd, path.name)
            return _scan(self.spec.source)
        except WebDeploymentError: raise
        except Exception: raise WebDeploymentError('WEB_DEPLOYMENT_PRECONDITION_FAILED') from None

    def create(self, *, confirmed):
        try:
            require(confirmed is True, 'WEB_DEPLOYMENT_CONSENT_REQUIRED')
            source = self.prepare(); plan = self._plan(source)
            with h.fs._directory(self.spec.journal.parent) as fd:
                os.mkdir(self.spec.journal.name, 0o700, dir_fd=fd)
                os.chmod(self.spec.journal.name, 0o700, dir_fd=fd, follow_symlinks=False); os.fsync(fd)
            with h.fs._directory(self.spec.journal) as journal:
                os.fchmod(journal, 0o700); os.fsync(journal)
                h.f._write(journal, 'deployment.attempt', plan, 0)
                with h.fs._directory(self.spec.target.parent) as fd:
                    os.mkdir(self.spec.target.name, 0o755, dir_fd=fd)
                    os.chmod(self.spec.target.name, 0o755, dir_fd=fd, follow_symlinks=False); os.fsync(fd)
                # Set explicit permissions despite a caller's restrictive umask.
                with h.fs._directory(self.spec.target) as fd: os.fchmod(fd, 0o755); os.fsync(fd)
                for name in sorted(source['directories'], key=lambda x: (len(Path(x).parts), x)):
                    path = self.spec.target / name
                    with h.fs._directory(path.parent) as fd:
                        os.mkdir(path.name, 0o755, dir_fd=fd)
                        os.chmod(path.name, 0o755, dir_fd=fd, follow_symlinks=False); os.fsync(fd)
                    with h.fs._directory(path) as fd: os.fchmod(fd, 0o755); os.fsync(fd)
                deadline = time.monotonic() + MAX_SECONDS
                for name, meta in sorted(source['files'].items()):
                    require(time.monotonic() <= deadline, 'WEB_DEPLOYMENT_COPY_LIMIT')
                    original = self.spec.source / name
                    with h.fs._directory(original.parent) as fd:
                        info = os.stat(original.name, dir_fd=fd, follow_symlinks=False)
                        raw = h.f._read(fd, original.name, info.st_gid, mode=stat.S_IMODE(info.st_mode), limit=h.p.MAX_FILE)
                    require(h.f._sha(raw) == meta['sha256'] and len(raw) == meta['bytes'], 'WEB_DEPLOYMENT_SOURCE_CHANGED')
                    target = self.spec.target / name
                    with h.fs._directory(target.parent) as fd: h.f._write(fd, target.name, raw, 0, mode=meta['mode'])
                require(_scan(self.spec.source) == source and _scan(self.spec.target, deployed=True) == source,
                        'WEB_DEPLOYMENT_SOURCE_CHANGED')
                h.f._write(journal, 'deployed.json', h.p._json({'version': 1, 'plan_sha256': h.f._sha(plan),
                    'state': 'WEB_SOURCE_DEPLOYED'}), 0)
            return self.observe()
        except WebDeploymentError: raise
        except Exception: raise WebDeploymentError('WEB_DEPLOYMENT_INCOMPLETE') from None

    def observe(self):
        try:
            require(os.getuid() == os.geteuid() == 0, 'WEB_DEPLOYMENT_ROOT_REQUIRED')
            source = _scan(self.spec.source); plan = self._plan(source)
            with h.fs._directory(self.spec.journal) as fd:
                info = os.fstat(fd)
                require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o700))
                require(h.f._read(fd, 'deployment.attempt', 0) == plan, 'WEB_DEPLOYMENT_DRIFT')
                require(h.f._json_read(fd, 'deployed.json', 0) == {'version': 1, 'plan_sha256': h.f._sha(plan),
                        'state': 'WEB_SOURCE_DEPLOYED'}, 'WEB_DEPLOYMENT_DRIFT')
            require(_scan(self.spec.target, deployed=True) == source, 'WEB_DEPLOYMENT_DRIFT')
            return {'state': 'WEB_SOURCE_DEPLOYED', 'source_commit': self.spec.commit, 'source_tree': WEB_TREE,
                'plan_sha256': h.f._sha(plan), 'files': WEB_FILES, 'code_deployed': True,
                'application_installed': False, 'writable_business_storage_ready': False,
                'service_activation_delivered': False, 'system_wiring_verified': False}
        except WebDeploymentError: raise
        except Exception: raise WebDeploymentError('WEB_DEPLOYMENT_INCOMPLETE') from None


class WebDeploymentOperation(Operation):
    def __init__(self, deployment):
        require(type(deployment) is WebDeployment); self.deployment = deployment
        super().__init__(StepSpec(name='web.source-deployment', operation='web.source-deployment.create', module='web',
            boundary='web.source-deployment', action='Déployer le code Web épinglé en lecture seule',
            resources=(ResourceSpec('webroot', 'directory', str(deployment.spec.target)),
                       ResourceSpec('deployment_journal', 'directory', str(deployment.spec.journal))),
            rollback_supported=False, warnings=('Code copié seulement ; finalisation, données et activation restent requises.',)))

    def prepare(self, context): self.deployment.prepare()
    def apply(self, context):
        value = self.deployment.create(confirmed=True)
        return Receipt(created_resources=('webroot', 'deployment_journal'),
                       hashes_non_secret=(('web_deployment_plan', value['plan_sha256']),))
    def validate(self, context):
        try: return self.deployment.observe()['plan_sha256'] == context.evidence['hashes_non_secret']['web_deployment_plan']
        except Exception: return False
    def commit(self, context): require(self.validate(context), 'WEB_DEPLOYMENT_DRIFT')
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try:
            value = self.deployment.observe()
            return Recovery(RecoveryDecision.APPLIED, Receipt(created_resources=('webroot', 'deployment_journal'),
                hashes_non_secret=(('web_deployment_plan', value['plan_sha256']),)))
        except Exception: return Recovery(RecoveryDecision.MANUAL)
