"""Separate confirmed SQL preparation, bound to the installed package profile."""
from dataclasses import replace
import re
import unicodedata

from installer import mariadb_runtime as native
from installer.engine import TransactionEngine
from installer.model import ErrorCode, exact_keys, require
from installer.operations import OperationRegistry, SecretVault
from installer.package_plan import PackagePlan
from installer.service_identity import ServiceIdentityOperation
from installer.transaction import StateJournal


class MariaDBPlan:
    _read, _write = PackagePlan._read, PackagePlan._write

    def __init__(self, parent, packages):
        self.parent, self.packages = parent, packages
        self.root = parent.journal.path.parent / 'mariadb'
        self.journal = StateJournal(self.root / 'state.json')
        self.secrets = SecretVault()

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'instance', 'packages_sha256'})
            require(type(value['version']) is int and value['version'] == 1
                    and type(value['instance']) is str and re.fullmatch('[a-f0-9]{32}', value['instance'])
                    and type(value['packages_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['packages_sha256']), ErrorCode.INVALID_STATE)
        return value

    def runtime(self):
        profile = self.profile(); require(profile is not None, ErrorCode.NOT_PLANNED)
        return native.MariaDB(profile['instance'], profile['packages_sha256'])

    def engine(self):
        r = self.runtime(); package = self.packages.engine('install').report()
        require(package is not None and package['state'] == 'DONE'
                and package['plan_sha256'] == r.package_sha256
                and self.packages.profile()['instance'] == r.instance, ErrorCode.INCOMPATIBLE_STATE)
        identity = ServiceIdentityOperation(r.identity)
        identity.spec = replace(identity.spec, name='system.mariadb.identity', boundary='system.mariadb.identity',
            action='Créer le compte système dédié à MariaDB',
            warnings=(*identity.spec.warnings, 'Paquets liés : ' + r.package_sha256))
        engine = TransactionEngine(self.journal, OperationRegistry((identity, native.Initialization(r), native.Start(r), native.Authority(r))), secrets=self.secrets)
        document = engine.report()
        if document is not None:
            require([s.as_dict() for s in engine.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(document)
        return engine

    def state(self):
        profile = self.profile(); document = self.journal.read()
        missing = []
        if document and document['steps'][-1]['state'] != 'DONE':
            try: self.secrets.require('sql.authority_password')
            except Exception: missing = ['authority_password']
        return {'profile': profile, 'installation': document, 'missing_credentials': missing,
                'authority_user': self.runtime().authority_user if profile else None,
                'migration_user': self.runtime().migration_user if profile else None,
                'application_installed': False, 'boot_enabled': False}

    def assert_ready(self):
        document = self.engine().report()
        require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        self.packages.packages().observe_installed()
        engine = self.engine()
        for spec, record in zip(document['plan']['steps'], document['steps']):
            require(engine.registry.get(spec).validate(engine._context(document, spec, record)), ErrorCode.VALIDATION_FAILED)

    def bind_credentials(self, values):
        if self.profile() is None: return
        r = self.runtime()
        for key, expected in (('authority_user', r.authority_user), ('migration_user', r.migration_user)):
            if key in values: require(values[key] == expected, ErrorCode.INCOMPATIBLE_STATE)

    def execute(self, action, payload):
        require(action in ('plan', 'credentials', 'apply', 'resume', 'retry'))
        with self.parent.journal.locked(create=True) as locked:
            require(locked.read() is None, ErrorCode.PLAN_EXISTS)
            if action == 'plan':
                exact_keys(payload, {'packages_sha256'})
                package = self.packages.engine('install').report()
                require(package is not None and package['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                require(payload['packages_sha256'] == package['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                if self.profile() is None:
                    require(self.journal.read() is None, ErrorCode.INVALID_STATE)
                    self.packages.packages().observe_installed()
                    value = {'version': 1, 'instance': self.packages.profile()['instance'], 'packages_sha256': package['plan_sha256']}
                    r = native.MariaDB(value['instance'], value['packages_sha256'])
                    r.absent(); r.identity.prepare()
                    self._write('profile.json', value)
                self.engine().plan(mode='fresh')
            elif action == 'credentials':
                exact_keys(payload, {'confirmation', 'authority_password'})
                document = self.engine().report(); require(document is not None, ErrorCode.NOT_PLANNED)
                require(payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                require(document['state'] != 'DONE', ErrorCode.INVALID_STATE)
                password = payload['authority_password']
                require(type(password) is str and 20 <= len(password.encode()) <= 1024
                        and not any(unicodedata.category(c).startswith('C') for c in password), ErrorCode.SECRET_REJECTED)
                temporary = SecretVault(); temporary.put('sql.authority_password', password)
                temporary.reject_in(document); temporary.reject_in(self.state())
                self.secrets.put('sql.authority_password', password)
            else:
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if action == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine = self.engine()
                # Confirmation is checked before live observations, and observations
                # precede every new effect/reconciliation on this server profile.
                document = engine.report(); require(document is not None, ErrorCode.NOT_PLANNED)
                require(payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                authority = document['steps'][-1]
                if authority['state'] != 'DONE' and not (authority['state'] in ('FAILED', 'RUNNING', 'MANUAL_ACTION_REQUIRED')
                        and authority['phase'] in ('apply', 'validate', 'commit')):
                    self.secrets.require('sql.authority_password')
                self.packages.packages().observe_installed()
                if action == 'retry': engine.retry(payload['name'], payload['confirmation'])
                else: getattr(engine, action)(payload['confirmation'])
                if engine.report()['state'] == 'DONE': self.secrets.clear()
            return self.state()
