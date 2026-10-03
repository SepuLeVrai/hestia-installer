"""Closed fresh Web composition, persisted choices and ephemeral credentials.

This profile stages the storage release on a prepared Debian 13 host. It does
not start MariaDB, install dependencies, adopt an existing site or open traffic.
Only the fixed server profile supplies filesystem paths and local identities.
"""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import unicodedata

from installer import application_operations as a, database_step as db, finalization as f
from installer import http_runtime as h, session_cleaner as cleaner, web_deployment as deploy
from installer.github_sources import AcquireOperation
from installer.proxy_ingress import ProxyIngress
from installer.model import (ErrorCode, InstallerError, Receipt, ResourceSpec, SourceSpec, StepSpec,
    build_plan, canonical_bytes, exact_keys, initial_document, integer, require, strict_json_loads)
from installer.operations import Operation, OperationContext, OperationRegistry, Recovery, RecoveryDecision, SecretVault
from installer.service_identity import ServiceIdentity, ServiceIdentityOperation
from installer.transaction import _FILE_FLAGS, _check_file, _private_directory
from installer.web_releases import STORAGE_COMMIT
from installer import mobile_web_source as mobile

FILENAME = 'application.json'
LIMIT = 16384
CREDENTIALS = frozenset(('database_password', 'admin_password', 'openai_api_key',
    'migration_user', 'migration_password', 'authority_user', 'authority_password'))


def _read(fd):
    try: handle = os.open(FILENAME, os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
    except FileNotFoundError: return None
    try:
        _check_file(handle)
        require(0 < os.fstat(handle).st_size <= LIMIT, ErrorCode.INVALID_STATE)
        raw = os.read(handle, LIMIT + 1)
        value = strict_json_loads(raw)
        require(canonical_bytes(value) == raw, ErrorCode.INVALID_STATE)
        exact_keys(value, {'version', 'revision', 'instance', 'configuration'})
        integer(value['version'], 1, 2); integer(value['revision'], 1, 1000000)
        profile = FreshProfile.from_draft(value)
        inputs = a.WebInputs(value['configuration'])
        config = inputs.configuration
        require(config['mode'] == 'fresh' and config['web'] == profile.web(config['web']['hostname'])
                and config['database']['mode'] in ('managed', 'existing_local')
                and config['database']['host'] == '127.0.0.1' and config['database']['port'] == 3306
                and config['database']['tls_ca_file'] is None, ErrorCode.INVALID_STATE)
        return value
    finally: os.close(handle)


def _write(fd, value):
    raw = canonical_bytes(value); require(len(raw) <= LIMIT)
    name = '.application-' + secrets.token_hex(16) + '.tmp'
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
    try:
        os.fchmod(handle, 0o600); _check_file(handle)
        with os.fdopen(handle, 'wb', closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(handle)
        os.replace(name, FILENAME, src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)
    finally:
        os.close(handle)
        try: os.unlink(name, dir_fd=fd)
        except FileNotFoundError: pass


def validate_credentials(values):
    require(type(values) is dict and set(values) <= CREDENTIALS)
    for name, value in values.items():
        require(type(value) is str and 0 < len(value.encode('utf-8')) <= 1024
                and not any(unicodedata.category(c).startswith('C') for c in value), ErrorCode.SECRET_REJECTED)
        if name.endswith('_user'):
            require(re.fullmatch(r'[A-Za-z0-9_]{1,32}', value) is not None and value.lower() != 'root', ErrorCode.SECRET_REJECTED)
        elif name == 'admin_password':
            require(12 <= len(value) <= 72 and len(value.encode('utf-8')) <= 72 and bool(value.strip()), ErrorCode.SECRET_REJECTED)
        elif name == 'openai_api_key':
            require(re.fullmatch(r'[A-Za-z0-9_.-]{20,500}', value) is not None
                    and value.upper() not in ('COLLER_LA_CLE_OPENAI_ICI', 'YOUR_OPENAI_API_KEY_HERE'), ErrorCode.SECRET_REJECTED)


class FreshProfile:
    """Versioned server-owned layout, independent of mutable host account IDs."""
    def __init__(self, instance, version=1):
        require(type(instance) is str and re.fullmatch(r'[a-f0-9]{32}', instance) is not None)
        integer(version, 1, 2)
        self.version = version
        self.source_commit = mobile.COMMIT if version == 2 else STORAGE_COMMIT
        self.instance = instance
        self.identity = ServiceIdentity(instance)
        self.worker = ServiceIdentity(a.digest(['php-worker', instance])[:32])
        self.root = Path('/var/lib/hst-' + instance)
        self.config_root = Path('/var/lib/hst-config-' + instance)
        self.webroot = Path('/srv/hst-' + instance)
        self.port = 9080

    @classmethod
    def from_draft(cls, draft):
        return cls(draft['instance'], draft['version'])

    def web(self, hostname):
        return {'hostname': hostname, 'webroot': str(self.webroot), 'service_user': self.identity.user}

    def runtime(self, *, planning=False):
        # Planning never looks up an account that its own plan has yet to create.
        account = None if planning else pwd.getpwnam(self.worker.user)
        return db.p.PhpRuntime(Path('/usr/bin/php8.4'), Path('/usr/lib/php/20240924'),
            1 if planning else account.pw_uid, 1 if planning else account.pw_gid,
            self.root / 'run', self.root / 'attempts')

    def http(self, configuration):
        return h.HttpRuntime(h.RuntimeSpec(self.instance, self.root / 'http', self.webroot,
            self.identity.user, configuration['web']['hostname'], self.port, '8.4',
            ProxyIngress('127.0.0.2', ('127.0.0.1/32',)), external_uploads=True,
            maintenance_directory=self.config_root / db.fs.configuration_slot(configuration) / 'maintenance',
            source_commit=self.source_commit if self.version == 2 else None))


class HostPrerequisites(Operation):
    def __init__(self):
        super().__init__(StepSpec(name='web.host-profile', operation='web.host-profile.check', module='web',
            boundary='web.host-profile', action='Vérifier le profil Debian 13 et ses dépendances déjà installées',
            warnings=('MariaDB doit déjà être accessible sur 127.0.0.1:3306.',
                      'Ce parcours prépare les services sous maintenance ; il ne les démarre pas.')))

    def check(self):
        system = h.read_os_release()
        require(system.get('ID') == 'debian' and system.get('VERSION_ID') == '13'
                and os.geteuid() == 0, ErrorCode.VALIDATION_FAILED)
        require(Path('/proc/1/comm').read_text().strip() == 'systemd'
                and Path('/sys/fs/cgroup/cgroup.controllers').is_file(), ErrorCode.VALIDATION_FAILED)
        paths = ['/usr/bin/php8.4', '/usr/sbin/php-fpm8.4', '/usr/sbin/apache2', '/usr/bin/systemctl',
                 '/usr/bin/setpriv', '/usr/bin/prlimit', '/usr/sbin/useradd', '/usr/sbin/nologin']
        paths += ['/usr/lib/php/20240924/' + name + '.so' for name in h.EXTENSIONS]
        paths += ['/usr/lib/apache2/modules/mod_' + name + '.so' for name in (*h.MODULES, 'remoteip', 'authz_host', 'alias')]
        for name in paths: db.p._safe_path(Path(name), directory=False, system=True)
        for path in (Path('/srv'), Path('/var/lib'), Path('/etc')):
            with db.fs._directory(path): pass

    def prepare(self, context): self.check()
    def apply(self, context): self.check(); return Receipt()
    def validate(self, context): self.check(); return context.evidence == Receipt().as_dict()
    def commit(self, context): self.check()
    def recover(self, context, phase): return Recovery(RecoveryDecision.RETRY_SAFE)


class ProfileDirectories(Operation):
    def __init__(self, profile):
        self.profile = profile
        self.directories = ((profile.root, 0o755), (profile.root / 'run', 0o711),
                            (profile.root / 'attempts', 0o700), (profile.config_root, 0o755))
        super().__init__(StepSpec(name='web.profile-directories', operation='web.profile-directories.create', module='web',
            boundary='web.profile-directories', action='Créer les répertoires privés du profil Web',
            dependencies=('web.worker-identity',), resources=(ResourceSpec('profile_root', 'directory', str(profile.root)),
                ResourceSpec('configuration_root', 'directory', str(profile.config_root))),
            warnings=('Un répertoire partiellement créé reste à examiner manuellement.',)))

    def prepare(self, context):
        for path in (self.profile.root, self.profile.config_root):
            with db.fs._directory(path.parent) as fd: db.fs._absent(fd, path.name)

    def _binding(self, context):
        return {'installation_id': context.installation_id, 'spec_sha256': a.digest(context.spec)}

    def apply(self, context):
        self.prepare(context)
        for path, mode in self.directories:
            with db.fs._directory(path.parent) as fd:
                os.mkdir(path.name, mode, dir_fd=fd)
                os.chmod(path.name, mode, dir_fd=fd, follow_symlinks=False); os.fsync(fd)
        with _private_directory(self.profile.root / 'attempts', create=False) as fd:
            f._write(fd, 'profile.json', canonical_bytes(self._binding(context)), 0, mode=0o600)
        return self._observe(context)

    def _observe(self, context):
        for path, mode in self.directories:
            with db.fs._directory(path) as fd:
                info = os.fstat(fd)
                require(stat.S_IMODE(info.st_mode) == mode and info.st_gid == 0, ErrorCode.INVALID_STATE)
        with _private_directory(self.profile.root / 'attempts', create=False) as fd:
            require(f._read(fd, 'profile.json', 0, mode=0o600) == canonical_bytes(self._binding(context)), ErrorCode.INVALID_STATE)
        return Receipt(created_resources=('profile_root', 'configuration_root'),
                       hashes_non_secret=(('profile', a.digest(self._binding(context))),))

    def validate(self, context): return self._observe(context).as_dict() == context.evidence
    def commit(self, context): require(self.validate(context), ErrorCode.VALIDATION_FAILED)
    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try: return Recovery(RecoveryDecision.APPLIED, self._observe(context))
        except Exception: return Recovery(RecoveryDecision.MANUAL)


class BoundFactory(Operation):
    """Resolve created account IDs at execution, without changing a planned byte."""
    def __init__(self, factory):
        self.factory = factory
        super().__init__(factory(planning=True).plan())

    def _operation(self):
        operation = self.factory(planning=False)
        require(operation.plan().as_dict() == self.spec.as_dict(), ErrorCode.INCOMPATIBLE_STATE)
        return operation

    def prepare(self, context): return self._operation().prepare(context)
    def apply(self, context): return self._operation().apply(context)
    def validate(self, context): return self._operation().validate(context)
    def commit(self, context): return self._operation().commit(context)
    def recover(self, context, phase): return self._operation().recover(context, phase)
    def rollback(self, context): return self._operation().rollback(context)


def composition(engine, github, draft):
    profile = FreshProfile.from_draft(draft); inputs = a.WebInputs(draft['configuration'])
    source = SourceSpec(db.p.WEB_REPOSITORY, profile.source_commit, profile.source_commit)
    acquire = AcquireOperation(engine.journal.path.parent, 'web', source, github.access)
    source_root = acquire.path / 'tree'
    acquire.spec = replace(acquire.spec, dependencies=('web.host-profile',))
    identity = ServiceIdentityOperation(profile.identity)
    identity.spec = replace(identity.spec, dependencies=(acquire.spec.name,))
    worker = ServiceIdentityOperation(profile.worker)
    worker.spec = replace(worker.spec, name='web.worker-identity', boundary='web.worker-identity',
                          action='Créer le worker PHP séparé du compte Web', dependencies=(identity.spec.name,))
    directories = ProfileDirectories(profile)
    deployment = deploy.WebDeploymentOperation(deploy.WebDeployment(deploy.DeploymentSpec(source_root,
        profile.webroot, profile.root / 'deployment', commit=profile.source_commit)))
    deployment.spec = replace(deployment.spec, dependencies=(directories.spec.name,), source=source)
    def database(*, planning):
        return a.DatabasePreparationOperation(db.DatabaseStep(profile.runtime(planning=planning), source_root,
            repository=db.p.WEB_REPOSITORY, commit=mobile.COMMIT if profile.version == 2 else db.WEB_COMMIT), inputs, config_root=profile.config_root,
            dependencies=(deployment.spec.name,))
    def finalization(*, planning):
        return a.FinalizationOperation(f.FinalizationStep(profile.runtime(planning=planning), source_root,
            repository=db.p.WEB_REPOSITORY, commit=profile.source_commit, instance=profile.instance), inputs,
            config_root=profile.config_root)
    http = profile.http(inputs.configuration)
    stage = h.HttpRuntimeOperation(http)
    stage.spec = replace(stage.spec, dependencies=('web.finalization',))
    collect = cleaner.SessionCleanerOperation(cleaner.SessionCleaner(http))
    collect.spec = replace(collect.spec, dependencies=(stage.spec.name,))
    return OperationRegistry((HostPrerequisites(), acquire, identity, worker, directories, deployment,
                              BoundFactory(database), BoundFactory(finalization), stage, collect))


class ApplicationPlan:
    def __init__(self, engine, github):
        self.engine, self.github = engine, github

    def read(self):
        try:
            with _private_directory(self.engine.journal.path.parent, create=False) as fd: result = _read(fd)
        except FileNotFoundError: return None
        self.engine.secrets.reject_in(result)
        return result

    @staticmethod
    def owns(document):
        return document is not None and any(s['operation'] == 'web.host-profile.check' for s in document['plan']['steps'])

    def restore(self):
        document = self.engine.report()
        if not self.owns(document): return False
        require(self.github is not None, ErrorCode.INCOMPATIBLE_STATE)
        draft = self.read(); require(draft is not None, ErrorCode.INCOMPATIBLE_STATE)
        registry = composition(self.engine, self.github, draft)
        require([s.as_dict() for s in registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
        registry.validate_document(document)
        self.engine.registry = registry
        return True

    def state(self):
        draft = self.read(); document = self.engine.report()
        names = set()
        if self.owns(document):
            for spec, record in zip(document['plan']['steps'], document['steps']):
                if record['state'] != 'DONE': names.update(n for n in spec['requires_secrets'] if n.startswith('web.'))
        missing = []
        for name in sorted(names):
            try: self.engine.secrets.require(name)
            except InstallerError: missing.append(name.removeprefix('web.'))
        return {'draft': draft, 'missing_credentials': missing,
                'profile': 'fresh-mobile-staged-v2' if draft and draft['version'] == 2 else 'fresh-storage-staged-v1',
                'application_installed': False, 'services_started': False}

    def save(self, payload):
        exact_keys(payload, {'revision', 'configuration', 'credentials'} | ({'profile'} if 'profile' in payload else set()))
        require(payload.get('profile', 'fresh-storage-staged-v1') in ('fresh-storage-staged-v1', 'fresh-mobile-staged-v2'))
        integer(payload['revision'], 0, 999999)
        choices = payload['configuration']
        exact_keys(choices, {'hostname', 'database', 'administrator', 'assistant'})
        exact_keys(choices['database'], {'mode', 'name', 'user'})
        require(choices['database']['mode'] in ('managed', 'existing_local'))
        credentials = payload['credentials']; validate_credentials(credentials)
        required = {'database_password', 'admin_password', 'migration_user', 'migration_password'}
        if choices['database']['mode'] == 'managed': required |= {'authority_user', 'authority_password'}
        require(required <= set(credentials), ErrorCode.SECRET_REQUIRED)
        with self.engine.journal.locked(create=True) as locked:
            require(locked.read() is None, ErrorCode.PLAN_EXISTS)
            current = _read(locked.directory_fd)
            require(payload['revision'] == (current['revision'] if current else 0), ErrorCode.BUSY)
            instance = current['instance'] if current else secrets.token_hex(16)
            version = (2 if payload['profile'] == 'fresh-mobile-staged-v2' else 1) if 'profile' in payload else (current['version'] if current else 1)
            profile = FreshProfile(instance, version)
            value = {'version': 1, 'mode': 'fresh', 'web': profile.web(choices['hostname']),
                'database': {**choices['database'], 'host': '127.0.0.1', 'port': 3306, 'tls_ca_file': None},
                'administrator': choices['administrator'], 'assistant': choices['assistant'],
                'secrets': {k: credentials.get(k, '') for k in ('database_password', 'admin_password', 'openai_api_key')}}
            temporary = SecretVault()
            for name, secret in credentials.items(): temporary.put('web.' + name, secret)
            inputs = a.WebInputs.capture(value, temporary)
            result = {'version': version, 'revision': payload['revision'] + 1, 'instance': instance, 'configuration': inputs.configuration}
            temporary.reject_in(result); self.engine.secrets.reject_in(result)
            # Validate against every persisted public field before accepting new secrets.
            from installer.wizard import WizardDraft
            temporary.reject_in(WizardDraft(self.engine)._read_at(locked.directory_fd))
            _write(locked.directory_fd, result)
            self.clear()
            for name, secret in credentials.items(): self.engine.secrets.put('web.' + name, secret)
            return deepcopy(result)

    def clear(self):
        for name in CREDENTIALS: self.engine.secrets.delete('web.' + name)

    def renew(self, payload):
        exact_keys(payload, {'confirmation', 'credentials'})
        values = payload['credentials']; validate_credentials(values)
        with self.engine.journal.locked(create=False) as locked:
            document = locked.read(); require(self.owns(document), ErrorCode.NOT_PLANNED)
            require(payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
            draft = _read(locked.directory_fd); require(draft is not None, ErrorCode.INVALID_STATE)
            registry = composition(self.engine, self.github, draft); registry.validate_document(document)
            require([s.as_dict() for s in registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            allowed = {n.removeprefix('web.') for s in document['plan']['steps'] for n in s['requires_secrets'] if n.startswith('web.')}
            require(set(values) <= allowed, ErrorCode.SECRET_REJECTED)
            temporary = SecretVault()
            for name, secret in values.items(): temporary.put('web.' + name, secret)
            from installer.wizard import WizardDraft
            for public in (document, draft, WizardDraft(self.engine)._read_at(locked.directory_fd)): temporary.reject_in(public)
            for name, secret in values.items(): self.engine.secrets.put('web.' + name, secret)
        return self.state()

    def plan(self, revision):
        integer(revision, 1, 1000000)
        require(self.github is not None, ErrorCode.UNSUPPORTED_MODULE)
        existing = self.engine.report()
        if existing is not None:
            require(self.owns(existing) and self.read()['revision'] == revision, ErrorCode.PLAN_EXISTS)
            self.restore(); return existing
        HostPrerequisites().check()
        selected_draft = self.read(); require(selected_draft is not None, ErrorCode.NOT_PLANNED)
        commit = FreshProfile.from_draft(selected_draft).source_commit
        selected = self.github.access.select(['web'], {'web': commit})
        require(selected['web'].commit_sha == commit, ErrorCode.SOURCE_DRIFT)
        with self.engine.journal.locked(create=True) as locked:
            require(locked.read() is None, ErrorCode.PLAN_EXISTS)
            draft = _read(locked.directory_fd)
            require(draft is not None and draft['revision'] == revision and draft == selected_draft, ErrorCode.BUSY)
            registry = composition(self.engine, self.github, draft)
            document = initial_document(build_plan(registry.specs(), mode='fresh'))
            self.engine.secrets.reject_in(document)
            locked.write(document, expected_revision=None)
            self.engine.registry = registry
            return deepcopy(document)
