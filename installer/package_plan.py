"""Two explicitly approved package journals for a fresh Debian 13 host.

GET reads private metadata only. Native acquisition/installation keep their
qualified contracts; neither phase initializes SQL or starts application units.
"""
from dataclasses import replace
import os
import re
import secrets

from installer import system_packages as native
from installer.engine import TransactionEngine
from installer.model import (ErrorCode, canonical_bytes, exact_keys, require, strict_json_loads)
from installer.operations import OperationRegistry
from installer.transaction import StateJournal, _private_directory, _FILE_FLAGS, _check_file


class FreshAcquisition(native.PackageAcquisitionOperation):
    def prepare(self, context):
        host, _ = self.packages.prepare()
        require(host['debian'] == '13', ErrorCode.VALIDATION_FAILED)


class PackagePlan:
    def __init__(self, parent):
        self.parent = parent
        self.root = parent.journal.path.parent / 'packages'
        self.journals = {phase: StateJournal(self.root / phase / 'state.json')
                         for phase in ('acquire', 'install')}

    def _read(self, name):
        try:
            with _private_directory(self.root, create=False) as fd:
                handle = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
                try:
                    _check_file(handle)
                    require(0 < os.fstat(handle).st_size <= 262144, ErrorCode.INVALID_STATE)
                    raw = os.read(handle, 262145); value = strict_json_loads(raw)
                    require(canonical_bytes(value) == raw, ErrorCode.INVALID_STATE)
                    return value
                finally: os.close(handle)
        except FileNotFoundError: return None

    def _write(self, name, value):
        raw = canonical_bytes(value); require(len(raw) <= 262144)
        with _private_directory(self.root, create=True) as fd:
            # Exclusive immutable selection; an interrupted file is never replaced.
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=fd)
            try:
                os.fchmod(handle, 0o600); _check_file(handle)
                with os.fdopen(handle, 'wb', closefd=False) as stream:
                    stream.write(raw); stream.flush(); os.fsync(handle)
                os.fsync(fd)
            finally: os.close(handle)

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'instance', 'nginx'})
            require(type(value['version']) is int and value['version'] == 1
                    and type(value['nginx']) is bool and type(value['instance']) is str
                    and re.fullmatch('[a-f0-9]{32}', value['instance']), ErrorCode.INVALID_STATE)
        return value

    def packages(self):
        profile = self.profile(); require(profile is not None, ErrorCode.NOT_PLANNED)
        return native.SystemPackages(profile['instance'], nginx=profile['nginx'])

    def selection(self):
        value = self._read('selection.json')
        if value is not None:
            exact_keys(value, {'archive_plan_sha256', 'acquisition_plan_sha256', 'packages'})
            for key in ('archive_plan_sha256', 'acquisition_plan_sha256'):
                require(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key]), ErrorCode.INVALID_STATE)
            require(type(value['packages']) is list and 0 < len(value['packages']) <= native.MAX_PACKAGES, ErrorCode.INVALID_STATE)
            names = []
            for row in value['packages']:
                exact_keys(row, {'name', 'version', 'architecture', 'bytes'})
                require(type(row['name']) is str and re.fullmatch(native.PACKAGE, row['name'])
                        and type(row['version']) is str and re.fullmatch(native.VERSION, row['version'])
                        and row['architecture'] in ('amd64', 'arm64', 'all')
                        and type(row['bytes']) is int and 0 < row['bytes'] <= 128 * 1024 * 1024, ErrorCode.INVALID_STATE)
                names.append(row['name'])
            require(names == sorted(set(names)), ErrorCode.INVALID_STATE)
        return value

    def state(self):
        # Historical receipts are not a live dpkg/systemd health claim.
        return {'profile': self.profile(), 'selection': self.selection(),
                'acquisition': self.journals['acquire'].read(), 'installation': self.journals['install'].read(),
                'application_installed': False, 'mariadb_ready': False}

    def engine(self, phase):
        require(phase in self.journals)
        packages = self.packages()
        if phase == 'acquire':
            operation = FreshAcquisition(packages)
            warning = 'Profil Debian 13 vierge ; nginx : ' + ('oui' if packages.nginx else 'non')
        else:
            selection = self.selection(); require(selection is not None, ErrorCode.NOT_PLANNED)
            acquisition = self.engine('acquire').report()
            require(acquisition is not None and acquisition['state'] == 'DONE'
                    and acquisition['plan_sha256'] == selection['acquisition_plan_sha256']
                    and acquisition['steps'][0]['evidence']['hashes_non_secret']['system_packages_plan'] == selection['archive_plan_sha256'],
                    ErrorCode.INCOMPATIBLE_STATE)
            operation = native.PackageInstallationOperation(packages, selection['archive_plan_sha256'])
            warning = 'Sélection immuable : ' + native.f._sha(canonical_bytes(selection))
        operation.spec = replace(operation.spec, warnings=(*operation.spec.warnings, warning))
        engine = TransactionEngine(self.journals[phase], OperationRegistry((operation,)))
        document = engine.report()
        if document is not None:
            require([s.as_dict() for s in engine.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(document)
        return engine

    def execute(self, action, payload):
        phase, verb = action.split('.')
        require(phase in self.journals and verb in ('plan', 'apply', 'resume', 'retry'))
        # The same lock as all main CLI/HTTP plans, even before a main plan exists.
        with self.parent.journal.locked(create=True) as locked:
            require(locked.read() is None, ErrorCode.PLAN_EXISTS)
            if verb == 'plan' and phase == 'acquire':
                exact_keys(payload, {'nginx'}); require(type(payload['nginx']) is bool)
                profile = self.profile()
                if profile is None:
                    require(self.journals['acquire'].read() is None and self.journals['install'].read() is None
                            and self.selection() is None, ErrorCode.INVALID_STATE)
                    profile = {'version': 1, 'instance': secrets.token_hex(16), 'nginx': payload['nginx']}
                    packages = native.SystemPackages(profile['instance'], nginx=profile['nginx'])
                    host, _ = packages.prepare()
                    require(host['debian'] == '13', ErrorCode.VALIDATION_FAILED)
                    self._write('profile.json', profile)
                require(profile['nginx'] == payload['nginx'], ErrorCode.PLAN_EXISTS)
                self.engine(phase).plan(mode='fresh')
            elif verb == 'plan':
                exact_keys(payload, {'acquisition_sha256'})
                acquisition = self.engine('acquire').report()
                require(acquisition is not None and acquisition['state'] == 'DONE', ErrorCode.NOT_PLANNED)
                require(payload['acquisition_sha256'] == acquisition['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                if self.journals['install'].read() is None:
                    packages = self.packages(); report = packages.observe(); manifest = packages._manifest()
                    require(report['plan_sha256'] == acquisition['steps'][0]['evidence']['hashes_non_secret']['system_packages_plan'],
                            ErrorCode.INCOMPATIBLE_STATE)
                    value = {'archive_plan_sha256': report['plan_sha256'], 'acquisition_plan_sha256': acquisition['plan_sha256'],
                             'packages': [{'name': name, **{key: record[key] for key in ('version', 'architecture', 'bytes')}}
                                          for name, record in sorted(manifest['archives'].items())]}
                    selection = self.selection()
                    if selection is None: self._write('selection.json', value)
                    else: require(selection == value, ErrorCode.INCOMPATIBLE_STATE)
                self.engine(phase).plan(mode='fresh')
            else:
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if verb == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine = self.engine(phase)
                if verb == 'retry': engine.retry(payload['name'], payload['confirmation'])
                else: getattr(engine, verb)(payload['confirmation'])
            return self.state()
