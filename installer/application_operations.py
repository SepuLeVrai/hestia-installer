"""Trusted Phase 5D journal adapters, never browser-supplied controller paths.

The existing controllers retain their own safety interlocks. Recovery here only
recognizes their evidence. Mutating recovery runs in apply/rollback, after the
transaction engine has persisted its checkpoint and checked consent.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re

from installer import database_step as db, finalization as f, storage_upgrade as u
from installer import storage_upgrade_recovery as recovery
from installer.model import (ErrorCode, InstallerError, Receipt, ResourceSpec,
                             SourceSpec, StepSpec, canonical_bytes, require, strict_json_loads)
from installer.operations import Operation, OperationContext, Recovery, RecoveryDecision, SecretVault
from installer.transaction import _private_directory, _FILE_FLAGS, _check_file
from installer.web_config import validate_web_configuration


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class WebInputs:
    """Immutable non-secret configuration; values are fetched from the vault.

    capture is a trusted composition helper, not an HTTP endpoint. Reconstructing
    from configuration does not invent credentials after an orchestrator restart.
    """
    def __init__(self, configuration):
        probe = deepcopy(configuration)
        desired = probe['assistant'].pop('desired_enabled')
        probe['secrets'] = {'database_password': 'fixture-validation-database',
                            'admin_password': 'fixture-validation-admin' if probe['mode'] == 'fresh' else '',
                            'openai_api_key': 'fixture-validation-openai-key' if desired is True else ''}
        require(validate_web_configuration(probe)['configuration'] == configuration)
        self._encoded = canonical_bytes(configuration)

    def __repr__(self): return '<WebInputs non-secret immutable configuration>'

    @classmethod
    def capture(cls, payload, vault):
        require(type(vault) is SecretVault)
        configuration = validate_web_configuration(payload)['configuration']
        # Validate every value before replacing any of the previous credentials.
        result = cls(configuration)
        vault.reject_in(configuration)
        for name, value in payload['secrets'].items():
            if value: vault.put('web.' + name, value)
            else: vault.delete('web.' + name)
        return result

    @property
    def configuration(self): return strict_json_loads(self._encoded)

    @property
    def sha256(self): return hashlib.sha256(self._encoded).hexdigest()

    def payload(self, context, *, existing=False):
        value = self.configuration
        desired = value['assistant'].pop('desired_enabled')
        if existing:
            value.update(mode='upgrade', administrator=None)
            if value['database']['mode'] == 'managed': value['database']['mode'] = 'existing_local'
            value['assistant']['action'] = 'preserve'
        value['secrets'] = {
            'database_password': context.require_secret('web.database_password'),
            'admin_password': context.require_secret('web.admin_password') if value['mode'] == 'fresh' else '',
            'openai_api_key': context.require_secret('web.openai_api_key') if desired is True and not existing else '',
        }
        return value

    def secret_names(self):
        value = self.configuration
        return ('web.database_password',) + (('web.admin_password',) if value['mode'] == 'fresh' else ()) + (
            ('web.openai_api_key',) if value['assistant']['desired_enabled'] is True else ())


class _BoundOperation(Operation):
    """Private dispatch ownership separate from the public transaction receipt."""
    def __init__(self, spec, inputs, state_root):
        require(type(inputs) is WebInputs and isinstance(state_root, Path))
        self.inputs, self.state_root = inputs, state_root
        super().__init__(spec)

    def _binding(self, context):
        # Do not put credentials, or hashes of credentials, in either journal.
        require(context.spec == self.spec.as_dict(), ErrorCode.INCOMPATIBLE_STATE)
        value = {'version': 1, 'installation_id': context.installation_id,
                 'step': self.spec.name, 'spec_sha256': digest(context.spec),
                 'configuration_sha256': self.inputs.sha256}
        context.secrets.reject_in(value)
        return value

    def _name(self, context):
        return 'operator-' + digest([context.installation_id, self.spec.name]) + '.json'

    def _bound(self, context):
        expected = canonical_bytes(self._binding(context))
        try:
            with _private_directory(self.state_root, create=False) as root:
                fd = os.open(self._name(context), os.O_RDONLY | _FILE_FLAGS, dir_fd=root)
                try:
                    _check_file(fd)
                    require(os.fstat(fd).st_size == len(expected), ErrorCode.INVALID_STATE)
                    require(os.read(fd, len(expected) + 1) == expected, ErrorCode.INVALID_STATE)
                finally: os.close(fd)
        except FileNotFoundError: return False
        return True

    def _bind(self, context):
        if self._bound(context): return
        raw = canonical_bytes(self._binding(context))
        with _private_directory(self.state_root, create=True) as root:
            fd = os.open(self._name(context), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600, dir_fd=root)
            try:
                os.fchmod(fd, 0o600); _check_file(fd)
                require(os.write(fd, raw) == len(raw), ErrorCode.OPERATION_FAILED)
                os.fsync(fd); os.fsync(root)
            finally: os.close(fd)

    def _receipt(self, context, value):
        require(self._bound(context), ErrorCode.INVALID_STATE)
        return Receipt(created_resources=tuple(r.name for r in self.spec.resources if not r.preexisting),
                       hashes_non_secret=(('configuration', self.inputs.sha256),
                                          ('operator_binding', digest(self._binding(context))),
                                          ('controller_result', digest(value))))

    def commit(self, context):
        require(self.validate(context), ErrorCode.VALIDATION_FAILED)

    def validate(self, context):
        return self._receipt(context, self._observe(context)).as_dict() == context.evidence


class DatabasePreparationOperation(_BoundOperation):
    """Fresh SQL is irreversible here; no DROP/restore is inferred from failure."""
    def __init__(self, controller, inputs, *, config_root, dependencies=()):
        require(type(controller) is db.DatabaseStep and type(inputs) is WebInputs)
        config = inputs.configuration
        require(config['mode'] == 'fresh')
        self.controller, self.config_root = controller, config_root
        self.slot = config_root / db.fs.configuration_slot(config)
        names = inputs.secret_names() + ('web.migration_user', 'web.migration_password')
        if config['database']['mode'] == 'managed': names += ('web.authority_user', 'web.authority_password')
        spec = StepSpec(name='web.database', operation='web.database.prepare', module='web', boundary='web.database',
            action='Préparer la base Web et son administrateur initial', dependencies=dependencies,
            resources=(ResourceSpec('database_configuration', 'directory', str(self.slot)),
                       ResourceSpec('sql_application', 'external', 'sql-application',
                                    preexisting=config['database']['mode'] != 'managed')),
            requires_secrets=names,
            warnings=('Configuration SHA-256 : ' + inputs.sha256,
                      'Les écritures SQL ne sont pas annulées automatiquement après une interruption.'),
            manual_actions=('Conserver les preuves privées et examiner tout fresh incomplet.',))
        super().__init__(spec, inputs, controller.runtime.state_root)

    def _credentials(self, context):
        migration = db.p.ProvisioningCredentials(context.require_secret('web.migration_user'),
                                                context.require_secret('web.migration_password'))
        authority = None
        if self.inputs.configuration['database']['mode'] == 'managed':
            authority = db.SqlAuthorityCredentials(context.require_secret('web.authority_user'),
                                                    context.require_secret('web.authority_password'))
        return migration, authority

    def _preflight(self, context):
        payload = self.inputs.payload(context)
        migration, authority = self._credentials(context)
        config = db._configuration(payload, migration, authority, fresh=True, confirmed=True)
        db.p._runtime(self.controller.runtime, config['web']['service_user'])
        gid = db.fs._web_group(config['web']['service_user'], self.controller.runtime)
        web = Path(config['web']['webroot'])
        with db.fs._directory(self.config_root, readable_by=gid) as root, db.fs._directory(web) as webfd, \
                db.fs._directory(web / 'includes') as inc:
            db.fs._absent(root, self.slot.name)
            db.fs._absent(webfd, 'install.lock'); db.fs._absent(inc, 'db.php')
        target = db._target(config, None, None)
        key = f._sha(db.p._json([target['host'], target['port'], target['name'].lower()]))
        try:
            with _private_directory(self.state_root, create=False) as fd:
                db.fs._absent(fd, 'fresh-' + key + '.attempt')
        except FileNotFoundError: pass
        return payload, migration, authority

    def prepare(self, context): self._preflight(context)

    def apply(self, context):
        payload, migration, authority = self._preflight(context)
        self._bind(context)
        value = self.controller.prepare(payload, migration, authority=authority,
                                        config_root=self.config_root, confirmed=True)
        require(value['state'] == 'DATABASE_CONFIGURATION_READY', ErrorCode.MANUAL_ACTION_REQUIRED)
        return self._receipt(context, self._observe(context))

    def _observe(self, context):
        payload = self.inputs.payload(context)
        config = f._configuration(payload, fresh=True)
        with ExitStack() as stack:
            gid, _, directory, conf, _, _ = f._open(self.controller.runtime, config, self.config_root, stack)
            database, _, ca = f._prepared(config, payload, directory, conf, gid)
            f._database_receipt(self.controller.runtime, database)
            result = f._sql(self.controller.runtime, self.controller.source, database, payload, config, ca,
                            desired=False, mutate=False)
            require(result == {'database_verified': True, 'assistant_enabled': False}, ErrorCode.VALIDATION_FAILED)
        return {'state': 'DATABASE_CONFIGURATION_READY', 'application_installed': False}

    def recover(self, context, phase):
        if phase not in ('apply', 'commit'): return Recovery(RecoveryDecision.MANUAL)
        try:
            if self._bound(context):
                return Recovery(RecoveryDecision.APPLIED, self._receipt(context, self._observe(context)))
            self._preflight(context)
            return Recovery(RecoveryDecision.RETRY_SAFE)
        except InstallerError as error:
            if error.code == ErrorCode.SECRET_REQUIRED: raise
        except Exception: pass
        return Recovery(RecoveryDecision.MANUAL)


class FinalizationOperation(_BoundOperation):
    def __init__(self, controller, inputs, *, config_root, dependencies=('web.database',)):
        require(type(controller) is f.FinalizationStep and type(inputs) is WebInputs)
        config = inputs.configuration
        require(config['mode'] == 'fresh')
        self.controller, self.config_root = controller, config_root
        slot = config_root / db.fs.configuration_slot(config)
        web = Path(config['web']['webroot'])
        spec = StepSpec(name='web.finalization', operation='web.finalization.finalize', module='web',
            boundary='web.finalization', action='Activer la configuration Web, l’Assistant et le sceau',
            dependencies=dependencies,
            resources=(ResourceSpec('web_pointer', 'file', str(web / 'includes/db.php')),
                       ResourceSpec('web_lock', 'file', str(web / 'install.lock')),
                       ResourceSpec('web_seal', 'file', str(slot / 'seal.json')),
                       ResourceSpec('assistant_settings', 'file', str(slot / 'assistant.json'))),
            requires_secrets=inputs.secret_names(),
            source=SourceSpec(db.p.WEB_REPOSITORY, controller.release.commit, controller.release.commit),
            warnings=('Configuration SHA-256 : ' + inputs.sha256,
                      'Le sceau ne prouve pas le démarrage des services ni l’accès Web final.') + (
                          ('Instance planifiée : ' + controller.instance,) if controller.instance is not None else ()),
            manual_actions=('Une activation sans reçu complet exige un examen manuel.',))
        super().__init__(spec, inputs, controller.runtime.state_root)

    def _preflight(self, context):
        payload = self.inputs.payload(context)
        config = f._configuration(payload, fresh=True)
        with ExitStack() as stack:
            gid, web, directory, conf, webfd, inc = f._open(self.controller.runtime, config, self.config_root, stack)
            self.controller._sources(web)
            for fd, name in ((inc, 'db.php'), (webfd, 'install.lock'), (conf, 'assistant.json'),
                             (conf, 'seal.json'), (conf, 'finalized.json'), (conf, 'finalization.attempt')):
                db.fs._absent(fd, name)
            database, _, _ = f._prepared(config, payload, directory, conf, gid)
            f._database_receipt(self.controller.runtime, database)
        return payload

    def prepare(self, context): self._preflight(context)

    def apply(self, context):
        payload = self._preflight(context)
        self._bind(context)
        value = self.controller.finalize(payload, config_root=self.config_root, confirmed=True)
        require(value['state'] == 'WEB_FRESH_FINALIZED', ErrorCode.MANUAL_ACTION_REQUIRED)
        return self._receipt(context, self._observe(context))

    def _observe(self, context):
        result = self.controller.observe(self.inputs.payload(context, existing=True), config_root=self.config_root)
        require(result['state'] == 'WEB_FRESH_FINALIZED', ErrorCode.MANUAL_ACTION_REQUIRED)
        # The SQL setting/key presence must match the approved choice, even if
        # observation succeeds after an external Assistant settings change.
        desired = self.inputs.configuration['assistant']['desired_enabled']
        require(result['result']['setting_enabled'] is desired
                and result['result']['key_configured'] is desired, ErrorCode.VALIDATION_FAILED)
        return result

    def recover(self, context, phase):
        if phase not in ('apply', 'commit'): return Recovery(RecoveryDecision.MANUAL)
        try:
            if self._bound(context):
                return Recovery(RecoveryDecision.APPLIED, self._receipt(context, self._observe(context)))
            self._preflight(context)
            return Recovery(RecoveryDecision.RETRY_SAFE)
        except InstallerError as error:
            if error.code == ErrorCode.SECRET_REQUIRED: raise
        except Exception: pass
        return Recovery(RecoveryDecision.MANUAL)


class StorageUpgradeOperation(_BoundOperation):
    """One dedicated, initially empty backup root per transaction operation.

    The sole lease is recognized from a controller-validated binding, never from
    an HTTP parameter. Ambiguous/missing attempts remain manual, not a new apply.
    """
    def __init__(self, controller, inputs, *, config_root, backup_root, dependencies=()):
        require(type(controller) is u.StorageUpgrade and type(inputs) is WebInputs)
        config = inputs.configuration
        require(config['mode'] == 'upgrade' and config['assistant']['action'] == 'preserve')
        require(config['database']['mode'] == 'existing_local')
        require(config['web'] == {'webroot': str(controller.http.spec.webroot),
                'hostname': controller.http.spec.hostname, 'service_user': controller.http.spec.service_user})
        self.controller, self.config_root, self.backup_root = controller, config_root, backup_root
        spec = StepSpec(name='web.storage-upgrade', operation='web.storage-upgrade.apply', module='web',
            boundary='web.storage-upgrade', action='Migrer les stockages Web sous maintenance avec sauvegarde vérifiée',
            dependencies=dependencies, rollback_supported=True,
            resources=(ResourceSpec('managed_web', 'directory', str(controller.http.spec.webroot), True, 'compensate'),
                       ResourceSpec('managed_runtime', 'directory', str(controller.http.spec.root), True, 'compensate'),
                       ResourceSpec('managed_configuration', 'directory', str(controller.http.spec.maintenance_directory.parent), True, 'compensate'),
                       ResourceSpec('upgrade_backups', 'directory', str(backup_root), True, 'compensate')),
            requires_secrets=('web.database_password', 'web.authority_user', 'web.authority_password'),
            source=SourceSpec(db.p.WEB_REPOSITORY, u.STORAGE_COMMIT, u.STORAGE_COMMIT),
            warnings=('Configuration SHA-256 : ' + inputs.sha256,
                      'Debian 13/PHP 8.4/Ext4, source scellée managed uniquement.',
                      'Verrou SQL global de lecture autorisé ; services arrêtés jusqu’à réouverture explicite.'),
            manual_actions=('Rollback avant réouverture seulement, sans restauration SQL ni suppression de données.',))
        super().__init__(spec, inputs, controller.runtime.state_root)

    def _lease(self, context):
        require(self._bound(context), ErrorCode.INVALID_STATE)
        with db.fs._directory(self.backup_root) as fd:
            u.files._private(fd, directory=True)
            names = os.listdir(fd)
        require(len(names) == 1 and re.fullmatch(r'upgrade-[a-f0-9]{32}', names[0]) is not None,
                ErrorCode.MANUAL_ACTION_REQUIRED)
        lease_id = names[0][8:]
        recovery.context(self.controller, self.backup_root, lease_id)
        return lease_id

    def _authority(self, context):
        return db.SqlAuthorityCredentials(context.require_secret('web.authority_user'),
                                          context.require_secret('web.authority_password'))

    def prepare(self, context):
        config = self.inputs.configuration
        require(Path(config['web']['webroot']) == self.controller.http.spec.webroot
                and self.config_root == self.controller.http.spec.maintenance_directory.parent.parent)
        require(not self._bound(context), ErrorCode.MANUAL_ACTION_REQUIRED)
        self.controller._paths(self.backup_root)
        with db.fs._directory(self.backup_root) as fd:
            require(not os.listdir(fd), ErrorCode.MANUAL_ACTION_REQUIRED)
        u.preflight.UpgradePreflight(self.controller.runtime, self.controller.source,
            repository=db.p.WEB_REPOSITORY, commit=u.LEGACY_COMMIT).inspect(
                self.inputs.payload(context), config_root=self.config_root)
        u.deploy._scan(self.controller.target_source, commit=u.STORAGE_COMMIT)

    def apply(self, context):
        authority = self._authority(context)
        payload = self.inputs.payload(context)
        if self._bound(context):
            # This mutation is reached only after the engine's apply checkpoint.
            lease_id = self._lease(context)
            result = self.controller.recover(payload, authority, config_root=self.config_root,
                backup_root=self.backup_root, lease_id=lease_id, direction='forward',
                confirmed=True, allow_global_read_lock=True)
        else:
            self.prepare(context)
            self._bind(context)
            result = self.controller.apply(payload, authority, config_root=self.config_root,
                backup_root=self.backup_root, confirmed=True, allow_global_read_lock=True)
        require(result['state'] == 'STORAGE_UPGRADE_APPLIED_GATED', ErrorCode.MANUAL_ACTION_REQUIRED)
        return self._receipt(context, self._observe(context))

    def _observe(self, context):
        lease_id = self._lease(context)
        result = self.controller.observe(self.backup_root, lease_id)
        require(result['state'] == 'STORAGE_UPGRADE_APPLIED_GATED', ErrorCode.MANUAL_ACTION_REQUIRED)
        slot = self.backup_root / ('upgrade-' + lease_id)
        require(not recovery.present(slot / 'resume-intent.json')
                and not recovery.present(slot / 'rollback-intent.json'), ErrorCode.MANUAL_ACTION_REQUIRED)
        # The private result is historical, so also audit the current code,
        # runtime configuration, maintenance lease, stopped services and timer.
        self.controller.target_http.observe()
        self.controller.target_collector.observe()
        return result

    def rollback(self, context):
        result = self.controller.recover(self.inputs.payload(context), self._authority(context),
            config_root=self.config_root, backup_root=self.backup_root, lease_id=self._lease(context),
            direction='rollback', confirmed=True, allow_global_read_lock=True)
        return result['state'] == 'STORAGE_UPGRADE_ROLLED_BACK_GATED'

    def recover(self, context, phase):
        if phase not in ('apply', 'commit', 'rollback'): return Recovery(RecoveryDecision.MANUAL)
        try:
            if not self._bound(context):
                if phase != 'apply': return Recovery(RecoveryDecision.MANUAL)
                self.prepare(context)
                return Recovery(RecoveryDecision.RETRY_SAFE)
            lease_id = self._lease(context)
            result = self.controller.observe(self.backup_root, lease_id)
            slot = self.backup_root / ('upgrade-' + lease_id)
            # An applied receipt is historical. A later intent overrides it.
            if recovery.present(slot / 'resume-intent.json') or result['state'] == 'STORAGE_UPGRADE_RESUME_AUTHORIZED':
                return Recovery(RecoveryDecision.MANUAL)
            if phase == 'rollback':
                if result['state'] == 'STORAGE_UPGRADE_ROLLED_BACK_GATED':
                    return Recovery(RecoveryDecision.ROLLED_BACK)
                return Recovery(RecoveryDecision.RETRY_SAFE)
            if recovery.present(slot / 'rollback-intent.json'):
                return Recovery(RecoveryDecision.MANUAL)
            if result['state'] == 'STORAGE_UPGRADE_APPLIED_GATED':
                return Recovery(RecoveryDecision.APPLIED, self._receipt(context, result))
            if phase == 'apply' and result['state'] == 'STORAGE_UPGRADE_RECOVERY_REQUIRED':
                return Recovery(RecoveryDecision.RETRY_SAFE)
        except InstallerError as error:
            if error.code == ErrorCode.SECRET_REQUIRED: raise
        except Exception: pass
        return Recovery(RecoveryDecision.MANUAL)


class StorageResumeOperation(_BoundOperation):
    """Authorize activity after cutover. Starting services is a later boundary.

    This operation has no rollback. Its durable private intent forbids rollback
    of the storage transition even when the outer journal loses the response.
    """
    def __init__(self, upgrade):
        require(type(upgrade) is StorageUpgradeOperation)
        self.upgrade = upgrade
        spec = StepSpec(name='web.storage-resume', operation='web.storage-upgrade.authorize', module='web',
            boundary='web.storage-resume', action='Autoriser la réouverture du Web migré après contrôles',
            dependencies=(upgrade.spec.name,),
            warnings=('Configuration SHA-256 : ' + upgrade.inputs.sha256,
                      'Cette autorisation termine le rollback précédent et ne démarre aucun service.'),
            manual_actions=('Après autorisation, toute restauration exige une nouvelle évaluation.',))
        super().__init__(spec, upgrade.inputs, upgrade.state_root)

    def _upgrade_context(self, context):
        return OperationContext(context.installation_id, self.upgrade.spec.as_dict(), {}, context.secrets)

    def _lease(self, context):
        return self.upgrade._lease(self._upgrade_context(context))

    def prepare(self, context):
        self.upgrade._observe(self._upgrade_context(context))

    def apply(self, context):
        self._bind(context)
        result = self.upgrade.controller.authorize_resume(self.upgrade.backup_root, self._lease(context), confirmed=True)
        require(result['state'] == 'STORAGE_UPGRADE_RESUME_AUTHORIZED', ErrorCode.MANUAL_ACTION_REQUIRED)
        return self._receipt(context, self._observe(context))

    def _observe(self, context):
        lease_id = self._lease(context)
        result = self.upgrade.controller.observe(self.upgrade.backup_root, lease_id)
        require(result == {'state': 'STORAGE_UPGRADE_RESUME_AUTHORIZED', 'lease_id': lease_id,
            'selected_commit': u.STORAGE_COMMIT, 'services_started': False,
            'rollback_requires_new_assessment': True, 'application_installed': False}, ErrorCode.MANUAL_ACTION_REQUIRED)
        return result

    def recover(self, context, phase):
        if phase not in ('apply', 'commit'): return Recovery(RecoveryDecision.MANUAL)
        try:
            if not self._bound(context):
                self.prepare(context)
                return Recovery(RecoveryDecision.RETRY_SAFE)
            result = self.upgrade.controller.observe(self.upgrade.backup_root, self._lease(context))
            if result['state'] == 'STORAGE_UPGRADE_RESUME_AUTHORIZED':
                return Recovery(RecoveryDecision.APPLIED, self._receipt(context, self._observe(context)))
            # The native authorization is explicitly idempotent, including a
            # partially written reopen. Never call it in this read-only method.
            if result['state'] == 'STORAGE_UPGRADE_APPLIED_GATED':
                return Recovery(RecoveryDecision.RETRY_SAFE)
        except Exception: pass
        return Recovery(RecoveryDecision.MANUAL)
