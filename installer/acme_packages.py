"""Additive, separately approved Certbot dependencies after the fresh package plan.

The original provisioner remains immutable: this profile proves an exact
extension of its installed snapshot, never an upgrade, adoption or repair.
"""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import re

from installer import system_packages as n
from installer.engine import TransactionEngine
from installer.model import ErrorCode, ResourceSpec, StepSpec, canonical_bytes, exact_keys, require
from installer.operations import Operation, OperationRegistry
from installer.package_plan import PackagePlan
from installer.transaction import StateJournal
from installer.frozen_boot import reference


class AcmePackages(n.SystemPackages):
    def __init__(self, base_profile, base_sha256):
        exact_keys(base_profile, {'version', 'instance', 'nginx'})
        require(base_profile['version'] == 1 and type(base_profile['version']) is int
                and type(base_profile['nginx']) is bool and type(base_profile['instance']) is str
                and re.fullmatch('[a-f0-9]{32}', base_profile['instance'])
                and type(base_sha256) is str and re.fullmatch('[a-f0-9]{64}', base_sha256))
        self.base = n.SystemPackages(base_profile['instance'], nginx=base_profile['nginx'])
        self.base_sha256 = base_sha256
        instance = hashlib.sha256(canonical_bytes({'base': base_profile, 'sha256': base_sha256,
                                                 'purpose': 'web-acme-v1'})).hexdigest()[:32]
        super().__init__(instance, nginx=not base_profile['nginx'])

    def _packages(self, host):
        return ('certbot',) + (('nginx',) if self.nginx else ())

    def _units(self, host):
        return ('certbot.service', 'certbot.timer') + (('nginx.service',) if self.nginx else ())

    def _host(self):
        value = super()._host()
        n.require(value['debian'] == '13', 'ACME_PACKAGES_DEBIAN13_REQUIRED')
        # Host evidence binds this additive plan to the exact immutable parent.
        manifest = self.base._manifest()
        n.require(n.f._sha(n.p._json(manifest)) == self.base_sha256, 'ACME_PACKAGES_PARENT_CHANGED')
        with n.fs._directory(self.base.directory) as fd:
            receipt = n.f._json_read(fd, 'installed.json', 0, mode=0o600)
            intent = n.f._json_read(fd, 'install.attempt', 0, mode=0o600)
        n.require(receipt['plan_sha256'] == intent['plan_sha256'] == self.base_sha256
                  and type(intent['preexisting_policy']) is bool)
        self.base._masks(manifest['host'])
        n.require(self.base._policy() == intent['preexisting_policy'])
        return {**value, 'base_instance': self.base.instance, 'base_plan_sha256': self.base_sha256,
                'base_installed_sha256': receipt['installed_sha256']}

    def prepare(self):
        # This check is intentionally only valid BEFORE the extension exists.
        report = self.base.observe_installed()
        n.require(report['plan_sha256'] == self.base_sha256)
        host, before = self._host(), self._installed()
        n.require(n.f._sha(n.p._json(before)) == host['base_installed_sha256'])
        n.require(not any(key.split(':')[0] in self._packages(host) for key in before),
                  'ACME_PACKAGES_EXISTING_SERVICE_REFUSED')
        with n.fs._directory(self.directory.parent) as fd: n.fs._absent(fd, self.directory.name)
        return host, before

    def _manifest(self):
        value = super()._manifest()
        n.require(n.f._sha(n.p._json(value['before'])) == value['host']['base_installed_sha256'],
                  'ACME_PACKAGES_PARENT_CHANGED')
        return value

    def install(self, *, confirmed, plan_sha256):
        """No original SQL/service effects; only authenticated additional archives."""
        try:
            n.require(confirmed is True, 'SYSTEM_PACKAGES_CONSENT_REQUIRED')
            report = self.observe()
            n.require(plan_sha256 == report['plan_sha256'], 'SYSTEM_PACKAGES_PLAN_MISMATCH')
            value = self._manifest(); host = value['host']
            n.require(Path('/proc/1/comm').read_text().strip() == 'systemd')
            self._masks(host, absent=True); preexisting_policy = self._policy()
            exact = [name + '=' + version for name, version in sorted(value['proposed'].items())]
            n.require(n.simulation(self._apt(['--simulate', '--no-remove', '--no-install-recommends', 'install', *exact]), value['before']) == value['proposed'])
            n.require(self._installed() == value['before'])
            with n.fs._directory(self.directory) as fd:
                n._journal(fd, 'install.attempt', {'plan_sha256': plan_sha256, 'preexisting_policy': preexisting_policy})
            if not preexisting_policy:
                with n.fs._directory(n.POLICY.parent) as fd: n.f._write(fd, n.POLICY.name, n.POLICY_BYTES, 0, mode=0o755)
            with n.fs._directory(n.h.drain.UNIT_ROOT) as fd:
                for unit in self._units(host): os.symlink('/dev/null', unit, dir_fd=fd); os.fsync(fd)
            n.h._command(['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'daemon-reload'])
            self._masks(host); self._policy()
            self._apt(['--yes', '--no-download', '--no-remove', '--no-install-recommends', 'install', *exact], timeout=600)
            self._verify_installed(value); self._masks(host); n.system_bus.observe()
            with n.fs._directory(self.directory) as fd:
                n._journal(fd, 'installed.json', {'version': 1, 'plan_sha256': plan_sha256,
                    'installed_sha256': n.f._sha(n.p._json(self._installed()))})
            if not preexisting_policy:
                self._policy()
                with n.fs._directory(n.POLICY.parent) as fd: os.unlink(n.POLICY.name, dir_fd=fd); os.fsync(fd)
            return self.observe_installed()
        except n.SystemPackagesError: raise
        except Exception: raise n.SystemPackagesError('ACME_PACKAGES_INSTALL_INCOMPLETE') from None


class Acquisition(n.PackageAcquisitionOperation):
    def __init__(self, packages):
        require(type(packages) is AcmePackages); self.packages = packages
        Operation.__init__(self, StepSpec(name='web.acme.packages-acquire', operation='web.acme.packages.acquire', module='web',
            boundary='web.acme.packages-acquire', action='Télécharger Certbot et les dépendances du frontal',
            resources=(ResourceSpec('acme_packages', 'directory', str(packages.directory)),), rollback_supported=False,
            warnings=('Ajout lié au reçu des paquets existants : ' + packages.base_sha256,
                      'Aucune mise à jour des paquets déjà installés. Une acquisition partielle reste manuelle.')))


class Installation(n.PackageInstallationOperation):
    def __init__(self, packages, plan_sha256):
        require(type(packages) is AcmePackages and type(plan_sha256) is str and re.fullmatch('[a-f0-9]{64}', plan_sha256))
        self.packages, self.plan_sha256 = packages, plan_sha256
        Operation.__init__(self, StepSpec(name='web.acme.packages-install', operation='web.acme.packages.install', module='web',
            boundary='web.acme.packages-install', action='Installer les dépendances du frontal sans démarrer leurs services',
            resources=(ResourceSpec('acme_installation', 'external', 'debian-acme.' + packages.instance),), rollback_supported=False,
            warnings=('Archives immuables : ' + plan_sha256,
                      'Le certificat, le frontal HTTPS et le renouvellement ne sont pas encore configurés.')))


class AcmePackagePlan(PackagePlan):
    def __init__(self, parent, base, boot):
        self.parent, self.base, self.boot = parent, base, boot
        self.root = parent.journal.path.parent / 'acme-packages'
        self.journals = {phase: StateJournal(self.root / phase / 'state.json') for phase in ('acquire', 'install')}

    def profile(self):
        value = self._read('profile.json')
        if value is not None:
            exact_keys(value, {'version', 'base', 'base_sha256', 'base_installation_sha256'})
            require(type(value['version']) is int and value['version'] == 1)
            AcmePackages(value['base'], value['base_sha256'])
            require(type(value['base_installation_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['base_installation_sha256']))
        return value

    def packages(self):
        value = self.profile(); require(value is not None, ErrorCode.NOT_PLANNED)
        return AcmePackages(value['base'], value['base_sha256'])

    def state(self):
        return {'profile': self.profile(), 'selection': self.selection(),
                'acquisition': self.journals['acquire'].read(), 'installation': self.journals['install'].read(),
                'public_tls_configured': False, 'renewal_configured': False, 'phase5_complete': False}

    def parent_document(self):
        document = self.base.engine('install').report()
        require(document is not None and document['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
        profile = self.profile()
        if profile is not None:
            require(profile['base'] == self.base.profile()
                    and profile['base_installation_sha256'] == document['plan_sha256']
                    and profile['base_sha256'] == document['steps'][0]['evidence']['hashes_non_secret']['system_packages_plan'],
                    ErrorCode.INCOMPATIBLE_STATE)
        return document

    def engine(self, phase):
        require(phase in self.journals); self.parent_document(); packages = self.packages()
        if phase == 'acquire': operation = Acquisition(packages)
        else:
            selection = self.selection(); require(selection is not None, ErrorCode.NOT_PLANNED)
            acquired = self.engine('acquire').report()
            require(acquired is not None and acquired['state'] == 'DONE'
                    and acquired['plan_sha256'] == selection['acquisition_plan_sha256']
                    and acquired['steps'][0]['evidence']['hashes_non_secret']['system_packages_plan'] == selection['archive_plan_sha256'],
                    ErrorCode.INCOMPATIBLE_STATE)
            operation = Installation(packages, selection['archive_plan_sha256'])
            operation.spec = replace(operation.spec, warnings=(*operation.spec.warnings, 'Sélection : ' + n.f._sha(canonical_bytes(selection))))
        engine = TransactionEngine(self.journals[phase], OperationRegistry((operation,)))
        document = engine.report()
        if document is not None:
            require([s.as_dict() for s in engine.registry.specs()] == document['plan']['steps'], ErrorCode.INCOMPATIBLE_STATE)
            engine.registry.validate_document(document)
        return engine

    def execute(self, action, payload):
        require(type(action) is str and action.count('.') == 1)
        phase, verb = action.split('.')
        require(phase in self.journals and verb in ('plan', 'apply', 'resume', 'retry'))
        with self.parent.journal.locked(create=True) as locked:
            base = self.parent_document()
            # Acquisition downloads only; dpkg mutation follows the completed
            # fresh/SQL/activation/boot journals, never an intermediate fresh.
            if phase == 'install':
                parent = locked.read()
                reference(self.boot, parent)
            if phase == 'acquire' and verb == 'plan':
                exact_keys(payload, {'packages_sha256'})
                require(payload['packages_sha256'] == base['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                if self.profile() is None:
                    require(self.journals['acquire'].read() is None and self.journals['install'].read() is None
                            and self.selection() is None, ErrorCode.INVALID_STATE)
                    value = {'version': 1, 'base': self.base.profile(), 'base_installation_sha256': base['plan_sha256'],
                             'base_sha256': base['steps'][0]['evidence']['hashes_non_secret']['system_packages_plan']}
                    AcmePackages(value['base'], value['base_sha256']).prepare()
                    self._write('profile.json', value)
                self.engine(phase).plan(mode='fresh')
            elif verb == 'plan':
                exact_keys(payload, {'acquisition_sha256'})
                acquired = self.engine('acquire').report()
                require(acquired is not None and acquired['state'] == 'DONE', ErrorCode.DEPENDENCY_BLOCKED)
                require(payload['acquisition_sha256'] == acquired['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                if self.journals['install'].read() is None:
                    reference(self.boot, parent, observe=True)
                    packages = self.packages(); report = packages.observe(); manifest = packages._manifest()
                    require(report['plan_sha256'] == acquired['steps'][0]['evidence']['hashes_non_secret']['system_packages_plan'], ErrorCode.INCOMPATIBLE_STATE)
                    value = {'archive_plan_sha256': report['plan_sha256'], 'acquisition_plan_sha256': acquired['plan_sha256'],
                             'packages': [{'name': name, **{k: row[k] for k in ('version', 'architecture', 'bytes')}}
                                          for name, row in sorted(manifest['archives'].items())]}
                    if self.selection() is None: self._write('selection.json', value)
                    else: require(self.selection() == value, ErrorCode.INCOMPATIBLE_STATE)
                self.engine(phase).plan(mode='fresh')
            else:
                exact_keys(payload, {'confirmation', 'confirm'} | ({'name'} if verb == 'retry' else set()))
                require(payload['confirm'] is True, ErrorCode.CONFIRMATION_REQUIRED)
                engine = self.engine(phase)
                document = engine.report(); require(document is not None, ErrorCode.NOT_PLANNED)
                require(payload['confirmation'] == document['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                if phase == 'install': reference(self.boot, parent, observe=True)
                if verb == 'retry': engine.retry(payload['name'], payload['confirmation'])
                else: getattr(engine, verb)(payload['confirmation'])
            return self.state()
