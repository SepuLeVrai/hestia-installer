"""Separate, resumable preparation journal. No native deployment is claimed."""
import os
import secrets
import time

from installer.application_plan import ApplicationPlan
from installer.engine import TransactionEngine
from installer.gateway_download import download
from installer.gateway_identity import GatewayIdentityStore, profile as identity_profile
from installer.gateway_release import release, sha, verify_package, copy_package
from installer.github_client import SECRET_NAME
from installer.github_sources import _read_json, _write_json
from installer.model import (ErrorCode, Receipt, ResourceSpec, SourceSpec, StepSpec,
                             canonical_bytes, exact_keys, now, require)
from installer.operations import Operation, OperationContext, OperationRegistry, Recovery, RecoveryDecision
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal, _private_directory, _FILE_FLAGS, _check_file
from installer.web_releases import STORAGE_COMMIT


class BinaryAcquisition(Operation):
    def __init__(self, root, selected, access, binding):
        self.root, self.selected, self.access, self.binding = root, selected, access, binding
        super().__init__(StepSpec(
            name='gateway.binary', operation='gateway.binary.acquire', module='gateway', boundary='gateway-binary',
            action='Acquérir et vérifier le paquet binaire Gateway qualifié',
            resources=(ResourceSpec('gateway-package', 'directory', str(root)),),
            source=SourceSpec(selected['repository'], selected['commit'], selected['commit']),
            requires_secrets=(SECRET_NAME,), warnings=(
                'Profil immuable : ' + binding, 'Paquet SHA-256 : ' + selected['package_sha256'],
                'GitHub Actions : lecture requise. Artefact temporaire, expiration : ' + selected['expires_at'],
                'Aucun binaire ni script du paquet exécuté pendant cette préparation.',)))

    def _owner(self, context):
        return {'installation_id': context.installation_id, 'profile_sha256': self.binding, 'release': self.selected}

    def _proof(self, fd, context):
        require(_read_json(fd, 'owner.json') == self._owner(context), ErrorCode.INCOMPATIBLE_STATE)
        allowed = {'owner.json', 'artifact.part', 'package.part', 'package.zip', 'receipt.json', 'receipt.json.part'}
        require(set(os.listdir(fd)) <= allowed, ErrorCode.INCOMPATIBLE_STATE)
        for name in os.listdir(fd):
            handle = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
            try: _check_file(handle)
            finally: os.close(handle)
        receipt = _read_json(fd, 'receipt.json')
        if receipt is None: return None
        handle = os.open('package.zip', os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
        with os.fdopen(handle, 'rb') as stream:
            _check_file(stream.fileno()); verified = verify_package(stream, self.selected)
        require(receipt == {'owner': self._owner(context), 'verified': verified}, ErrorCode.SOURCE_DRIFT)
        return receipt

    def _receipt(self, proof):
        return Receipt(created_resources=('gateway-package',), commit_shas=(('gateway', self.selected['commit']),),
                       hashes_non_secret=(('gateway-package', self.selected['package_sha256']),
                                          ('gateway-binary', self.selected['binary_sha256']),
                                          ('gateway-proof', sha(canonical_bytes(proof)))))

    def prepare(self, context):
        try:
            with _private_directory(self.root, create=False) as fd: self._proof(fd, context)
        except FileNotFoundError:
            # A missing resource is normal only before acquisition starts.
            require(not self.root.exists(), ErrorCode.SOURCE_DRIFT)

    def apply(self, context):
        with _private_directory(self.root.parent, create=True) as parent_fd:
            created = False
            try:
                os.mkdir(self.root.name, 0o700, dir_fd=parent_fd); os.fsync(parent_fd); created = True
            except FileExistsError: pass
        with _private_directory(self.root, create=False) as fd:
            if created: _write_json(fd, 'owner.json', self._owner(context))
            proof = self._proof(fd, context)
            if proof is not None: return self._receipt(proof)
            # Only a bound, uncommitted download can be discarded and retried.
            for name in set(os.listdir(fd)) - {'owner.json'}: os.unlink(name, dir_fd=fd)
            os.fsync(fd)
            require(self.access is not None, ErrorCode.SECRET_REQUIRED)
            token = self.access.token()
            require(token == context.require_secret(SECRET_NAME), ErrorCode.SECRET_REQUIRED)
            handle = os.open('artifact.part', os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
            with os.fdopen(handle, 'w+b') as artifact:
                download(self.access.client, self.selected, token, artifact)
                artifact.flush(); os.fsync(artifact.fileno())
                target = os.open('package.part', os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
                with os.fdopen(target, 'w+b') as package:
                    copy_package(artifact, package, self.selected)
                    package.flush(); os.fsync(package.fileno())
                    verified = verify_package(package, self.selected)
            os.rename('package.part', 'package.zip', src_dir_fd=fd, dst_dir_fd=fd)
            os.unlink('artifact.part', dir_fd=fd); os.fsync(fd)
            proof = {'owner': self._owner(context), 'verified': verified}
            context.secrets.reject_in(proof); _write_json(fd, 'receipt.json', proof)
            return self._receipt(proof)

    def validate(self, context):
        with _private_directory(self.root, create=False) as fd:
            proof = self._proof(fd, context)
            require(proof is not None, ErrorCode.SOURCE_DRIFT)
            return self._receipt(proof).as_dict() == context.evidence

    def commit(self, context): require(self.validate(context), ErrorCode.SOURCE_DRIFT)

    def recover(self, context, phase):
        try:
            with _private_directory(self.root, create=False) as fd: proof = self._proof(fd, context)
            if proof is not None: return Recovery(RecoveryDecision.COMMITTED, self._receipt(proof))
            return Recovery(RecoveryDecision.RETRY_SAFE)
        except FileNotFoundError:
            return Recovery(RecoveryDecision.MANUAL if self.root.exists() else RecoveryDecision.RETRY_SAFE)
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class IdentityPreparation(Operation):
    def __init__(self, store, value, binding):
        self.store, self.value = store, value
        super().__init__(StepSpec(
            name='gateway.identities', operation='gateway.identities.prepare', module='gateway', boundary='gateway-identities',
            dependencies=('gateway.binary',), action='Préparer les identités privées Gateway et les JWK Foundation',
            resources=(ResourceSpec('gateway-identities', 'directory', str(store.root)),),
            warnings=('Profil immuable : ' + binding, 'Identités conservées à la reprise ; aucun service démarré.')))

    def _receipt(self, receipt):
        return Receipt(created_resources=('gateway-identities',),
                       hashes_non_secret=(('gateway-public-identities', sha(canonical_bytes(receipt))),))

    def prepare(self, context):
        report = self.store.report()
        if report is not None:
            require(report['profile'] == self.value, ErrorCode.INCOMPATIBLE_STATE)
            if report['receipt'] is not None: self.store.verify()

    def apply(self, context): return self._receipt(self.store.prepare(self.value))
    def validate(self, context): return self._receipt(self.store.verify()).as_dict() == context.evidence
    def commit(self, context): require(self.validate(context), ErrorCode.SOURCE_DRIFT)

    def recover(self, context, phase):
        try:
            self.prepare(context); report = self.store.report()
            if report is not None and report['receipt'] is not None:
                return Recovery(RecoveryDecision.COMMITTED, self._receipt(self.store.verify()))
            return Recovery(RecoveryDecision.RETRY_SAFE)
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class BinaryImport(BinaryAcquisition):
    """The approved engine drives the upload, including its pre-effect journal.

    A request stream is process-local input, never a path or persisted callback.
    The old acquisition adapter and its StepSpec remain unchanged.
    """
    def __init__(self, root, selected, binding, stream=None, length=None):
        super().__init__(root, selected, None, binding)
        self.stream, self.length = stream, length
        self.spec = StepSpec(
            name='gateway.binary', operation='gateway.binary.import', module='gateway', boundary='gateway-binary',
            action='Importer et vérifier le paquet binaire Gateway qualifié', resources=self.spec.resources,
            source=self.spec.source, warnings=(
                'Profil immuable : ' + binding, 'Paquet SHA-256 : ' + selected['package_sha256'],
                'ZIP binaire qualifié uniquement ; aucun accès GitHub requis pour cet import.',
                'Aucun binaire ni script du paquet exécuté pendant cette préparation.'))

    def apply(self, context):
        # Reconcile the durable proof before asking for a new request body.
        try:
            with _private_directory(self.root, create=False) as fd: proof = self._proof(fd, context)
        except FileNotFoundError:
            require(not self.root.exists(), ErrorCode.SOURCE_DRIFT); proof = None
        if proof is not None: return self._receipt(proof)
        require(self.stream is not None and type(self.length) is int
                and self.length == self.selected['package_bytes'], ErrorCode.DEPENDENCY_BLOCKED)
        with _private_directory(self.root.parent, create=True) as parent_fd:
            created = False
            try:
                os.mkdir(self.root.name, 0o700, dir_fd=parent_fd); os.fsync(parent_fd); created = True
            except FileExistsError: pass
        with _private_directory(self.root, create=False) as fd:
            if created: _write_json(fd, 'owner.json', self._owner(context))
            require(self._proof(fd, context) is None, ErrorCode.INCOMPATIBLE_STATE)
            for name in set(os.listdir(fd)) - {'owner.json'}: os.unlink(name, dir_fd=fd)
            os.fsync(fd)
            handle = os.open('package.part', os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
            with os.fdopen(handle, 'w+b') as package:
                remaining = self.length; deadline = time.monotonic() + 120
                reader = getattr(self.stream, 'read1', self.stream.read)
                while remaining:
                    require(time.monotonic() < deadline, ErrorCode.SOURCE_LIMIT)
                    chunk = reader(min(65536, remaining))
                    require(time.monotonic() < deadline, ErrorCode.SOURCE_LIMIT)
                    require(type(chunk) is bytes and 0 < len(chunk) <= remaining, ErrorCode.SOURCE_DRIFT)
                    package.write(chunk); remaining -= len(chunk)
                package.flush(); os.fsync(package.fileno())
                verified = verify_package(package, self.selected)
            os.rename('package.part', 'package.zip', src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)
            proof = {'owner': self._owner(context), 'verified': verified}
            context.secrets.reject_in(proof); _write_json(fd, 'receipt.json', proof)
            return self._receipt(proof)


class GatewayPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, parent, access=None):
        self.parent, self.access = parent, access
        self.root = parent.journal.path.parent / 'gateway'
        self.journal = StateJournal(self.root / 'preparation/state.json')
        self.identities = GatewayIdentityStore(self.root / 'identities')

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'identity', 'release', 'web_plan_sha256'} | ({'acquisition'} if 'acquisition' in value else set()))
            require(value.get('acquisition', 'github') in ('github', 'package'), ErrorCode.INVALID_STATE)
            identity_profile(value['identity'])
            require(value['release'] == release(), ErrorCode.INCOMPATIBLE_STATE)
            require(type(value['web_plan_sha256']) is str and len(value['web_plan_sha256']) == 64
                    and all(c in '0123456789abcdef' for c in value['web_plan_sha256']), ErrorCode.INVALID_STATE)
            self.parent.secrets.reject_in(value)
        return value

    def state(self):
        # Public metadata only. DONE describes preparation, never availability.
        return {'profile': self.profile(), 'release': release(), 'preparation': self.journal.read(),
                'identities': self.identities.report(), 'deployment_available': False}

    @staticmethod
    def _parent(document):
        require(ApplicationPlan.owns(document) and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        require(any(s.get('source', {}).get('repository') == 'SepuLeVrai/hestia-nexus-avv'
                    and s['source']['commit_sha'] == STORAGE_COMMIT for s in document['plan']['steps']),
                ErrorCode.INCOMPATIBLE_STATE)
        return document['plan_sha256']

    def engine(self, *, stream=None, length=None):
        value = self.profile(); require(value is not None, ErrorCode.NOT_PLANNED)
        binding = sha(canonical_bytes(value))
        binary = (BinaryImport(self.root / 'binary', value['release'], binding, stream, length)
                  if value.get('acquisition') == 'package' else
                  BinaryAcquisition(self.root / 'binary', value['release'], self.access, binding))
        operations = (binary,
                      IdentityPreparation(self.identities, value['identity'], binding))
        engine = TransactionEngine(self.journal, OperationRegistry(operations), secrets=self.parent.secrets)
        document = engine.report()
        if document is not None:
            require([s.as_dict() for s in engine.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(document)
        return engine

    def execute(self, action, payload, *, stream=None, length=None):
        require(action in ('plan', 'apply', 'resume', 'retry', 'check', 'import'))
        with self.parent.journal.locked(create=False) as locked:
            parent_sha = self._parent(locked.read())
            if action == 'plan':
                exact_keys(payload, {'web_plan_sha256', 'public_origin', 'dev_enabled'} |
                           ({'acquisition'} if 'acquisition' in payload else set()))
                require(payload.get('acquisition', 'github') in ('github', 'package'))
                require(payload['web_plan_sha256'] == parent_sha, ErrorCode.CONFIRMATION_REQUIRED)
                identity = identity_profile({'version': 1, 'instance': secrets.token_hex(16),
                                             'public_origin': payload['public_origin'], 'dev_enabled': payload['dev_enabled']})
                value = self.profile()
                if value is None:
                    require(self.journal.read() is None and self.identities.report() is None, ErrorCode.INVALID_STATE)
                    value = {'identity': identity, 'release': release(), 'web_plan_sha256': parent_sha}
                    if 'acquisition' in payload: value['acquisition'] = payload['acquisition']
                    self.parent.secrets.reject_in(value)
                    self._write('profile.json', value)
                require(value.get('acquisition', 'github') == payload.get('acquisition', 'github')
                        and value['web_plan_sha256'] == parent_sha and all(value['identity'][k] == payload[k]
                        for k in ('public_origin', 'dev_enabled')), ErrorCode.PLAN_EXISTS)
                self.engine().plan(mode='fresh')
            else:
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                value = self.profile(); require(value is not None, ErrorCode.NOT_PLANNED)
                require(value['web_plan_sha256'] == parent_sha, ErrorCode.INCOMPATIBLE_STATE)
                if action == 'import':
                    require(value.get('acquisition') == 'package' and stream is not None, ErrorCode.INCOMPATIBLE_STATE)
                    require(type(length) is int and length == value['release']['package_bytes'], ErrorCode.SOURCE_LIMIT)
                engine = self.engine(stream=stream, length=length); document = engine.report()
                require(document is not None and payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                # Explicit mutations verify completed resources without changing
                # the old journal on damage. GET does not perform these checks.
                for spec, record in zip(document['plan']['steps'], document['steps']):
                    if record['state'] == 'DONE':
                        context = OperationContext(document['installation_id'], spec, record['evidence'], engine.secrets)
                        require(engine.registry.get(spec).validate(context), ErrorCode.SOURCE_DRIFT)
                if action == 'check':
                    require(document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                    return {**self.state(), 'verification': {'state': 'PREPARATION_VERIFIED', 'checked_at': now()}}
                try:
                    if action == 'import':
                        record = document['steps'][0]
                        require(record['state'] != 'DONE', ErrorCode.INCOMPATIBLE_STATE)
                        require(document['steps'][1]['state'] == 'PLANNED', ErrorCode.INCOMPATIBLE_STATE)
                        if record['state'] in ('FAILED', 'MANUAL_ACTION_REQUIRED'):
                            retried = engine.retry('gateway.binary', payload['confirmation'])
                            if retried['steps'][0]['state'] == 'DONE': engine.resume(payload['confirmation'])
                        elif record['state'] == 'RUNNING': engine.resume(payload['confirmation'])
                        else: engine.apply(payload['confirmation'])
                    elif action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                    else: getattr(engine, action)(payload['confirmation'])
                finally:
                    if self.access is not None: self.access.clear()
            return self.state()
