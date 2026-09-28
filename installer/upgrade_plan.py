"""Closed wizard composition for the acquired, sealed managed upgrade profile.

Only a local administrator can register a descriptor. Browser requests select
its digest; native ownership is checked before registration and before apply.
Reading a journal never probes the host, connects to SQL or repairs a runtime.
"""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import pwd
import re

from installer import application_plan as app, application_activation as activation
from installer import application_operations as a, storage_upgrade as u
from installer.engine import TransactionEngine
from installer.github_sources import AcquireOperation
from installer.model import (ErrorCode, InstallerError, Receipt, ResourceSpec, StepSpec, SourceSpec,
    build_plan, canonical_bytes, exact_keys, initial_document, require, strict_json_loads)
from installer.operations import Operation, OperationContext, OperationRegistry, Recovery, RecoveryDecision, SecretVault
from installer.transaction import _private_directory

FILENAME = 'upgrade-profile.json'
CREDENTIALS = frozenset(('database_password', 'authority_user', 'authority_password'))
HTTP_FIELDS = {'instance', 'root', 'webroot', 'service_user', 'hostname', 'port', 'maintenance_directory'}


class ManagedProfile:
    def __init__(self, descriptor):
        exact_keys(descriptor, {'version', 'http', 'worker'})
        require(type(descriptor['version']) is int and descriptor['version'] == 1)
        http, worker = descriptor['http'], descriptor['worker']
        exact_keys(http, HTTP_FIELDS); exact_keys(worker, {'user', 'run_root', 'state_root'})
        for name in ('root', 'webroot', 'maintenance_directory'):
            require(type(http[name]) is str and str(Path(http[name])) == http[name])
        for name in ('run_root', 'state_root'):
            require(type(worker[name]) is str and str(Path(worker[name])) == worker[name]
                    and worker[name].startswith('/var/lib/'))
            app.h._path(Path(worker[name]))
        require(type(worker['user']) is str and re.fullmatch(r'[a-z][a-z0-9_-]{0,30}', worker['user']) is not None
                and worker['user'] not in ('root', 'nobody', 'www-data', http['service_user']))
        self.descriptor = deepcopy(descriptor)
        self.http = app.h.HttpRuntime(app.h.RuntimeSpec(**{**http,
            **{name: Path(http[name]) for name in ('root', 'webroot', 'maintenance_directory')},
            'php_family': '8.4', 'ingress': app.ProxyIngress('127.0.0.2', ('127.0.0.1/32',))}))
        self.config_root = self.http.spec.maintenance_directory.parent.parent
        self.backups = Path('/var/lib/hst-upgrade-' + self.http.spec.instance) / 'backup'

    def runtime(self, *, planning=False):
        worker = self.descriptor['worker']
        account = None if planning else pwd.getpwnam(worker['user'])
        return app.db.p.PhpRuntime(Path('/usr/bin/php8.4'), Path('/usr/lib/php/20240924'),
            1 if planning else account.pw_uid, 1 if planning else account.pw_gid,
            Path(worker['run_root']), Path(worker['state_root']))

    def inspect(self):
        """Read native seals and managed configuration; never retain a secret."""
        account, _, _, _ = self.http._inspect_configuration()
        app.cleaner.SessionCleaner(self.http)._inspect_configuration()
        app.db.p._runtime(self.runtime(), self.http.spec.service_user)
        with _private_directory(self.runtime().state_root, create=False): pass
        with app.db.fs._directory(self.http.spec.maintenance_directory.parent, readable_by=account.pw_gid) as fd:
            database = app.f._json_read(fd, 'database.json', account.pw_gid)
            state = app.f._json_read(fd, 'state.json', account.pw_gid)
        require(state['migration_retained'] is False and database['host'] == '127.0.0.1'
                and database['tls_required'] is False and database['tls_ca_file'] is None, ErrorCode.INVALID_STATE)
        config = {'version': 1, 'mode': 'upgrade', 'web': {
            'hostname': self.http.spec.hostname, 'webroot': str(self.http.spec.webroot), 'service_user': self.http.spec.service_user},
            'database': {**{k: database[k] for k in ('host', 'port', 'name', 'user')}, 'mode': 'existing_local', 'tls_ca_file': None},
            'administrator': None, 'assistant': {'action': 'preserve', 'desired_enabled': None}}
        return a.WebInputs(config).configuration


def _read(fd):
    try: raw = app.f._read(fd, FILENAME, 0, mode=0o600, limit=app.LIMIT)
    except FileNotFoundError: return None
    value = strict_json_loads(raw)
    require(canonical_bytes(value) == raw, ErrorCode.INVALID_STATE)
    exact_keys(value, {'descriptor', 'configuration'})
    profile = ManagedProfile(value['descriptor']); config = a.WebInputs(value['configuration']).configuration
    require(config['mode'] == 'upgrade' and config['administrator'] is None
            and config['assistant'] == {'action': 'preserve', 'desired_enabled': None}
            and config['database']['mode'] == 'existing_local' and config['database']['host'] == '127.0.0.1'
            and config['database']['tls_ca_file'] is None
            and config['web'] == {k: profile.descriptor['http'][k] for k in ('hostname', 'webroot', 'service_user')}, ErrorCode.INVALID_STATE)
    return value


class BackupWorkspace(a._BoundOperation):
    def __init__(self, profile, inputs, profile_sha256):
        self.path = profile.backups.parent
        self.backups = profile.backups
        super().__init__(StepSpec(name='web.upgrade-workspace', operation='web.upgrade-workspace.create', module='web',
            boundary='web.upgrade-workspace', action='Réserver la sauvegarde privée de cette migration',
            dependencies=('web.upgrade.target',), resources=(ResourceSpec('backup_root', 'directory', str(self.path)),),
            warnings=('Profil géré SHA-256 : ' + profile_sha256, 'Une création incomplète exige un examen manuel.')),
            inputs, profile.runtime(planning=True).state_root)

    def prepare(self, context):
        require(not self._bound(context), ErrorCode.MANUAL_ACTION_REQUIRED)
        with app.db.fs._directory(self.path.parent) as fd: app.db.fs._absent(fd, self.path.name)

    def apply(self, context):
        self.prepare(context); u._mkdir(self.path); u._mkdir(self.backups); self._bind(context)
        return self._receipt(context, self._observe(context))

    def _observe(self, context):
        require(self._bound(context), ErrorCode.INVALID_STATE)
        with _private_directory(self.path, create=False): pass
        with _private_directory(self.backups, create=False): pass
        return {'state': 'UPGRADE_WORKSPACE_READY'}

    def recover(self, context, phase):
        if phase == 'rollback': return Recovery(RecoveryDecision.MANUAL)
        try: return Recovery(RecoveryDecision.APPLIED, self._receipt(context, self._observe(context)))
        except Exception: return Recovery(RecoveryDecision.MANUAL)


def composition(engine, github, value):
    profile = ManagedProfile(value['descriptor']); inputs = a.WebInputs(value['configuration'])
    sources = []
    for role, commit in (('source', u.LEGACY_COMMIT), ('target', u.STORAGE_COMMIT)):
        operation = AcquireOperation(engine.journal.path.parent, 'web', SourceSpec(app.db.p.WEB_REPOSITORY, commit, commit), github.access)
        operation.spec = replace(operation.spec, name='web.upgrade.' + role, boundary='web.upgrade.' + role,
            dependencies=(sources[-1].spec.name,) if sources else ())
        sources.append(operation)
    workspace = BackupWorkspace(profile, inputs, a.digest(value))
    def upgrade(*, planning=False):
        controller = u.StorageUpgrade(profile.runtime(planning=planning), sources[0].path / 'tree', sources[1].path / 'tree',
            profile.http, app.cleaner.SessionCleaner(profile.http))
        return a.StorageUpgradeOperation(controller, inputs, config_root=profile.config_root, backup_root=profile.backups,
            dependencies=(workspace.spec.name,))
    return OperationRegistry((*sources, workspace, app.BoundFactory(upgrade)))


class UpgradePlan:
    def __init__(self, engine, github): self.engine, self.github = engine, github

    def read(self):
        try:
            with _private_directory(self.engine.journal.path.parent, create=False) as fd: value = _read(fd)
        except FileNotFoundError: return None
        self.engine.secrets.reject_in(value)
        return value

    @staticmethod
    def owns(document):
        return document is not None and any(s['operation'] == 'web.upgrade-workspace.create' for s in document['plan']['steps'])

    def register(self, descriptor_path):
        require(isinstance(descriptor_path, Path) and descriptor_path.is_absolute())
        with _private_directory(descriptor_path.parent, create=False) as fd:
            raw = app.f._read(fd, descriptor_path.name, 0, mode=0o600, limit=app.LIMIT)
        descriptor = strict_json_loads(raw)
        profile = ManagedProfile(descriptor)
        with self.engine.journal.locked(create=True) as locked:
            require(locked.read() is None, ErrorCode.PLAN_EXISTS)
            value = {'descriptor': descriptor, 'configuration': profile.inspect()}
            self.engine.secrets.reject_in(value)
            current = _read(locked.directory_fd)
            require(current is None or current == value, ErrorCode.INCOMPATIBLE_STATE)
            if current is None: app.f._write(locked.directory_fd, FILENAME, canonical_bytes(value), 0, mode=0o600)
        return self.state()

    def restore(self):
        document = self.engine.report()
        if not self.owns(document): return False
        value = self.read(); require(value is not None and self.github is not None, ErrorCode.INCOMPATIBLE_STATE)
        registry = composition(self.engine, self.github, value)
        require([s.as_dict() for s in registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
        registry.validate_document(document); self.engine.registry = registry
        return True

    def state(self):
        value, document = self.read(), self.engine.report()
        missing = []
        if value is not None and (document is None or self.owns(document) and document['state'] != 'DONE'):
            for name in sorted(CREDENTIALS):
                try: self.engine.secrets.require('web.' + name)
                except InstallerError: missing.append(name)
        return {'profile': value, 'profile_sha256': a.digest(value) if value else None,
                'source_commit': u.LEGACY_COMMIT, 'target_commit': u.STORAGE_COMMIT, 'missing_credentials': missing}

    def renew(self, payload):
        exact_keys(payload, {'confirmation', 'credentials'})
        values = payload['credentials']; app.validate_credentials(values)
        require(set(values) <= CREDENTIALS, ErrorCode.SECRET_REJECTED)
        with self.engine.journal.locked(create=False) as locked:
            value = _read(locked.directory_fd); document = locked.read()
            require(value is not None and (document is None or self.owns(document)), ErrorCode.NOT_PLANNED)
            require(payload['confirmation'] == (document['plan_sha256'] if document else a.digest(value)), ErrorCode.CONFIRMATION_REQUIRED)
            if document: self.restore()
            temporary = SecretVault()
            for name, secret in values.items(): temporary.put('web.' + name, secret)
            from installer.wizard import WizardDraft
            for public in (value, document, WizardDraft(self.engine)._read_at(locked.directory_fd)): temporary.reject_in(public)
            for name, secret in values.items(): self.engine.secrets.put('web.' + name, secret)
        return self.state()

    def plan(self, profile_sha256):
        value = self.read(); require(value is not None and a.digest(value) == profile_sha256, ErrorCode.CONFIRMATION_REQUIRED)
        require(self.github is not None, ErrorCode.UNSUPPORTED_MODULE)
        current = self.engine.report()
        if current:
            require(self.owns(current), ErrorCode.PLAN_EXISTS); self.restore(); return current
        require(ManagedProfile(value['descriptor']).inspect() == value['configuration'], ErrorCode.INVALID_STATE)
        for commit in (u.LEGACY_COMMIT, u.STORAGE_COMMIT):
            selected = self.github.access.select(['web'], {'web': commit})
            require(selected['web'].commit_sha == commit, ErrorCode.SOURCE_DRIFT)
        with self.engine.journal.locked(create=True) as locked:
            require(locked.read() is None, ErrorCode.PLAN_EXISTS)
            require(_read(locked.directory_fd) == value, ErrorCode.INCOMPATIBLE_STATE)
            registry = composition(self.engine, self.github, value)
            document = initial_document(build_plan(registry.specs(), mode='upgrade'))
            self.engine.secrets.reject_in(document); locked.write(document, expected_revision=None)
            self.engine.registry = registry
        return deepcopy(document)


class ParentResume(a.StorageResumeOperation):
    def __init__(self, upgrade, parent):
        super().__init__(upgrade); self.parent = parent
        self.spec = replace(self.spec, dependencies=(), warnings=('Préparation liée : ' + parent['plan_sha256'], *self.spec.warnings))

    def _upgrade_context(self, context):
        return OperationContext(self.parent['installation_id'], self.upgrade.spec.as_dict(), {}, context.secrets)


class UpgradeActivation(activation.Activation):
    def __init__(self, upgrade, parent):
        super().__init__(upgrade.controller.target_http, parent['plan_sha256'])
        self.directory = upgrade.state_root
        self.resume = ParentResume(upgrade, parent)

    def serving(self):
        scope, _ = self.configuration()
        require(scope.observe()['state'] == 'SERVING', ErrorCode.MANUAL_ACTION_REQUIRED)
        context = OperationContext('activation-observation', self.resume.spec.as_dict(), {}, SecretVault())
        self.resume._observe(context)

    def probe_port(self):
        require(self.runtime.spec.ingress == app.ProxyIngress('127.0.0.2', ('127.0.0.1/32',)), ErrorCode.INVALID_STATE)
        return self.runtime.spec.port


class UpgradeActivationPlan(activation.ActivationPlan):
    mode = 'upgrade'
    def engine(self, parent):
        require(self.application.owns(parent) and parent['state'] == 'DONE', ErrorCode.NOT_PLANNED)
        self.application.restore()
        operation = self.application.engine.registry.get(parent['plan']['steps'][-1])._operation()
        current = UpgradeActivation(operation, parent)
        operations = [current.resume]; previous = current.resume.spec.name
        for role in ('php', 'apache', 'timer'):
            step = activation.ActivationOperation(current, role, previous); operations.append(step); previous = step.spec.name
        engine = TransactionEngine(self.journal, OperationRegistry((*operations, activation.AvailabilityOperation(current))))
        document = engine.report()
        if document is not None:
            require([s.as_dict() for s in engine.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(document)
        return engine, current
