"""Typed HTTPS facade for the wizard and transaction engine."""
from __future__ import annotations

import threading
from contextlib import contextmanager

from installer.engine import TransactionEngine
from installer.wizard import WizardDraft, preflight_snapshot
from installer.web_config import validate_web_configuration
from installer.operations import default_registry
from installer.model import ErrorCode, InstallerError, exact_keys, require
from installer.application_plan import ApplicationPlan
from installer.application_activation import ActivationPlan
from installer.upgrade_plan import UpgradePlan, UpgradeActivationPlan
from installer.package_plan import PackagePlan
from installer.mariadb_plan import MariaDBPlan
from installer.boot_plan import BootPlan
from installer.acme_packages import AcmePackagePlan
from installer.public_tls_plan import PublicTLSPlan
from installer.gateway_plan import GatewayPlan
from installer.foundation_plan import FoundationPlan
from installer.gateway_service_plan import GatewayServicePlan
from installer.gateway_transition_plan import GatewayTransitionPlan
from installer.gateway_transition_execution import GatewayTransitionExecution
from installer.mobile_activation_plan import MobileActivationPlan
from installer.mobile_backup_plan import MobileBackupPlan
from installer.mobile_preparation_plan import MobilePreparationPlan
from installer.shared_public_plan import SharedPublicPlan
from installer.shared_public_lifecycle import SharedPublicLifecycle
from installer.mobile_boot_plan import MobileBootPlan
from installer.fcm_plan import FcmPlan

POST_ROUTES = {
    **{'/api/gateway/transition/execution/' + action: 'gateway-transition-execution.' + action
       for action in ('plan', 'apply', 'resume', 'check')},
    '/api/gateway/transition/plan': 'gateway-transition.plan',
    **{'/api/gateway/fcm/' + action: 'fcm.' + action for action in ('plan', 'check')},
    **{'/api/mobile/boot/' + action: 'mobile-boot.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    **{'/api/mobile/public/preparation/' + action: 'shared-public-preparation.' + action for action in ('plan', 'check')},
    **{'/api/mobile/public/' + action: 'shared-public.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    **{'/api/mobile/preparation/' + action: 'mobile-preparation.' + action for action in ('plan', 'apply', 'resume')},
    **{'/api/mobile/backup/' + action: 'mobile-backup.' + action for action in ('plan', 'apply', 'resume')},
    **{'/api/mobile/activation/' + action: 'mobile-activation.' + action for action in ('plan', 'apply', 'resume', 'check')},
    **{'/api/gateway/service/' + action: 'gateway-service.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    **{'/api/gateway/foundation/' + action: 'foundation.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    **{'/api/gateway/preparation/' + action: 'gateway.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    **{'/api/web/public-tls/' + action: 'public-tls.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    **{'/api/system/acme-packages/' + phase + '/' + action: 'acme-packages.' + phase + '.' + action
       for phase in ('acquire', 'install') for action in ('plan', 'apply', 'resume', 'retry')},
    **{'/api/system/boot/' + action: 'boot.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    **{'/api/system/mariadb/' + action: 'mariadb.' + action for action in ('plan', 'credentials', 'apply', 'resume', 'retry')},
    **{'/api/system/packages/' + phase + '/' + action: 'packages.' + phase + '.' + action
       for phase in ('acquire', 'install') for action in ('plan', 'apply', 'resume', 'retry')},
    **{'/api/web/activation/' + action: 'activation.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    "/api/web/setup": "web.setup",
    "/api/web/credentials": "web.credentials",
    "/api/web/upgrade/credentials": "web.upgrade.credentials",
    "/api/web/config/validate": "web.config.validate",
    "/api/wizard/draft": "wizard.draft",
    "/api/wizard/plan": "wizard.plan",
    "/api/wizard/reset-plan": "wizard.reset-plan",
    "/api/preflight/run": "preflight.run",
    "/api/installation/plan": "plan",
    "/api/installation/apply": "apply",
    "/api/installation/resume": "resume",
    "/api/installation/retry": "retry",
    "/api/installation/rollback": "rollback",
    "/api/github/validate": "github.validate",
    "/api/github/plan": "github.plan",
    "/api/github/clear": "github.clear",
}
GET_ROUTES = frozenset({"/api/wizard/state", "/api/installation/state", "/api/installation/report", "/api/github/status"})
TRANSITION_PACKAGE_ROUTE = '/api/gateway/transition/execution/import'
GATEWAY_PACKAGE_ROUTE = '/api/gateway/preparation/import'
FCM_IMPORT_ROUTE = '/api/gateway/fcm/import'


class TransactionService:
    def __init__(self, engine: TransactionEngine, *, github=None) -> None:
        self.engine = engine
        self.github = github
        self.wizard = WizardDraft(engine)
        self.application = ApplicationPlan(engine, github)
        self.upgrade = UpgradePlan(engine, github)
        self.packages = PackagePlan(engine)
        self.mariadb = MariaDBPlan(engine, self.packages)
        if not self.application.restore(): self.upgrade.restore()
        self._fresh_activation = ActivationPlan(self.application)
        self._upgrade_activation = UpgradeActivationPlan(self.upgrade)
        self.boot = BootPlan(self.application, self._fresh_activation, self.mariadb)
        self.acme_packages = AcmePackagePlan(engine, self.packages, self.boot)
        self.public_tls = PublicTLSPlan(engine, self.boot, self.acme_packages)
        self.gateway = GatewayPlan(engine, github.access if github is not None else None)
        self.foundation = FoundationPlan(self.application, self._fresh_activation, self.gateway)
        self.gateway_service = GatewayServicePlan(self.foundation)
        self.gateway_transition = GatewayTransitionPlan(self.gateway_service)
        self.fcm = FcmPlan(self.gateway, self.gateway_service)
        self.mobile_activation = MobileActivationPlan(self.application, self.gateway_service)
        self.mobile_backup = MobileBackupPlan(self.mobile_activation)
        self.gateway_transition_execution = GatewayTransitionExecution(self.gateway_transition, self.mobile_backup)
        self.mobile_preparation = MobilePreparationPlan(self.mobile_backup)
        self.shared_public_preparation = SharedPublicPlan(self.public_tls, self.gateway)
        self.shared_public = SharedPublicLifecycle(self.shared_public_preparation, self.gateway_service)
        self.mobile_boot = MobileBootPlan(self.shared_public)
        self._preflight = None
        self._mutation_lock = threading.Lock()
        self._condition = threading.Condition()
        self._active = 0
        self._closing = False

    @property
    def activation(self):
        return self._upgrade_activation if self.upgrade.owns(self.engine.report()) else self._fresh_activation

    @contextmanager
    def _activity(self):
        with self._condition:
            require(not self._closing, ErrorCode.SHUTTING_DOWN)
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def _mutation(self):
        require(self._mutation_lock.acquire(blocking=False), ErrorCode.BUSY)
        try:
            yield
        finally:
            self._mutation_lock.release()

    def wizard_state(self) -> dict:
        with self._activity():
            # Readers never wait for a long acquisition. A stale RUNNING snapshot
            # does not authorize a replay; mutations retain the engine's lock.
            return {"installation": self.engine.report(), "draft": self.wizard.read(),
                    "busy": self._mutation_lock.locked(), "preflight": self._preflight,
                    "application": self.application.state(), "activation": self.activation.state(), "upgrade": self.upgrade.state(),
                    "packages": self.packages.state(), "mariadb": self.mariadb.state(), "boot": self.boot.state(),
                    "acme_packages": self.acme_packages.state(), "public_tls": self.public_tls.state(),
                    "gateway": self.gateway.state(), "foundation": self.foundation.state(), "fcm": self.fcm.state(),
                    "gateway_service": self.gateway_service.state(), "mobile_activation": self.mobile_activation.state(),
                    "gateway_transition": self.gateway_transition.state(),
                    "gateway_transition_execution": self.gateway_transition_execution.state(),
                    "mobile_backup": self.mobile_backup.state(), "mobile_preparation": self.mobile_preparation.state(),
                    "shared_public_preparation": self.shared_public_preparation.state(), "shared_public": self.shared_public.state(), "mobile_boot": self.mobile_boot.state()}

    def github_status(self) -> dict:
        with self._activity(), self._mutation():
            require(self.github is not None, ErrorCode.UNSUPPORTED_MODULE)
            return {"github": self.github.access.status()}

    def clear_credentials(self) -> None:
        with self._activity(), self._mutation():
            if self.github is not None:
                self.github.access.clear()
            self.application.clear()
            self.mariadb.secrets.clear()

    def report(self) -> dict:
        with self._activity():
            result = {"installation": self.engine.report()}
            activation = self.activation.state()
            if activation['installation'] is not None: result['activation'] = activation
            packages = self.packages.state()
            if packages['profile'] is not None: result['packages'] = packages
            mariadb = self.mariadb.state()
            if mariadb['profile'] is not None: result['mariadb'] = mariadb
            boot = self.boot.state()
            if boot['installation'] is not None: result['boot'] = boot
            acme = self.acme_packages.state()
            if acme['profile'] is not None: result['acme_packages'] = acme
            public = self.public_tls.state()
            if public['installation'] is not None: result['public_tls'] = public
            gateway = self.gateway.state()
            if gateway['profile'] is not None: result['gateway'] = gateway
            fcm = self.fcm.state()
            if fcm['profile'] is not None: result['fcm'] = fcm
            foundation = self.foundation.state()
            if foundation['profile'] is not None: result['foundation'] = foundation
            gateway_service = self.gateway_service.state()
            if gateway_service['profile'] is not None: result['gateway_service'] = gateway_service
            transition = self.gateway_transition.state()
            if transition['profile'] is not None: result['gateway_transition'] = transition
            execution = self.gateway_transition_execution.state()
            if execution['state'] != 'NOT_PLANNED': result['gateway_transition_execution'] = execution
            mobile = self.mobile_activation.state()
            if mobile['state'] != 'NOT_PLANNED': result['mobile_activation'] = mobile
            backup = self.mobile_backup.state()
            if backup['state'] != 'NOT_PLANNED': result['mobile_backup'] = backup
            preparation = self.mobile_preparation.state()
            if preparation['state'] != 'NOT_PLANNED': result['mobile_preparation'] = preparation
            public_preparation = self.shared_public_preparation.state()
            if public_preparation['plan'] is not None: result['shared_public_preparation'] = public_preparation
            shared_public = self.shared_public.state()
            if shared_public['installation'] is not None: result['shared_public'] = shared_public
            mobile_boot = self.mobile_boot.state()
            if mobile_boot['installation'] is not None: result['mobile_boot'] = mobile_boot
            return result

    def execute(self, action: str, payload: dict) -> dict:
        with self._activity(), self._mutation():
            if action.startswith('gateway-transition-execution.'):
                return {"gateway_transition_execution": self.gateway_transition_execution.execute(
                    action.removeprefix('gateway-transition-execution.'), payload)}
            if action.startswith('gateway-transition.'):
                return {"gateway_transition": self.gateway_transition.execute(action.removeprefix('gateway-transition.'), payload)}
            if action.startswith('mobile-boot.'):
                return {"mobile_boot": self.mobile_boot.execute(action.removeprefix('mobile-boot.'), payload)}
            if action.startswith('fcm.'):
                return {"fcm": self.fcm.execute(action.removeprefix('fcm.'), payload)}
            if action.startswith('shared-public-preparation.'):
                return {"shared_public_preparation": self.shared_public_preparation.execute(action.removeprefix('shared-public-preparation.'), payload)}
            if action.startswith('shared-public.'):
                return {"shared_public": self.shared_public.execute(action.removeprefix('shared-public.'), payload)}
            if action.startswith('mobile-preparation.'):
                return {"mobile_preparation": self.mobile_preparation.execute(action.removeprefix('mobile-preparation.'), payload)}
            if action.startswith('mobile-backup.'):
                return {"mobile_backup": self.mobile_backup.execute(action.removeprefix('mobile-backup.'), payload)}
            if action.startswith('mobile-activation.'):
                return {"mobile_activation": self.mobile_activation.execute(action.removeprefix('mobile-activation.'), payload)}
            if action.startswith('gateway-service.'):
                return {"gateway_service": self.gateway_service.execute(action.removeprefix('gateway-service.'), payload)}
            if action.startswith('foundation.'):
                return {"foundation": self.foundation.execute(action.removeprefix('foundation.'), payload)}
            if action.startswith('gateway.'):
                require(action != 'gateway.import', ErrorCode.INVALID_DATA)
                return {"gateway": self.gateway.execute(action.removeprefix('gateway.'), payload)}
            if action.startswith('public-tls.'):
                return {"public_tls": self.public_tls.execute(action.removeprefix('public-tls.'), payload)}
            if action.startswith('acme-packages.'):
                return {"acme_packages": self.acme_packages.execute(action.removeprefix('acme-packages.'), payload)}
            if action.startswith('boot.'):
                return {"boot": self.boot.execute(action.removeprefix('boot.'), payload)}
            if action.startswith('mariadb.'):
                return {"mariadb": self.mariadb.execute(action.removeprefix('mariadb.'), payload)}
            if action.startswith('packages.'):
                return {"packages": self.packages.execute(action.removeprefix('packages.'), payload)}
            if action in ('wizard.plan', 'github.plan', 'plan') and self.packages.profile() is not None:
                package_state = self.packages.state()
                require(package_state['installation'] is not None and package_state['installation']['state'] == 'DONE',
                        ErrorCode.DEPENDENCY_BLOCKED)
            if action.startswith('activation.'):
                return {"activation": self.activation.execute(action.removeprefix('activation.'), payload)}
            if action == "web.setup":
                if self.packages.profile() is not None:
                    require(payload.get('configuration', {}).get('database', {}).get('mode') == 'managed', ErrorCode.INCOMPATIBLE_STATE)
                    self.mariadb.bind_credentials(payload.get('credentials', {}))
                    self.mariadb.assert_ready()
                self.application.save(payload)
                return {"application": self.application.state()}
            if action == "web.credentials":
                self.mariadb.bind_credentials(payload.get('credentials', {}))
                return {"application": self.application.renew(payload)}
            if action == "web.upgrade.credentials":
                return {"upgrade": self.upgrade.renew(payload)}
            if action == "web.config.validate":
                preview = validate_web_configuration(payload)
                self.engine.secrets.reject_in(preview)
                return {"web_configuration": preview}
            if action == "wizard.draft":
                return {"draft": self.wizard.save(payload)}
            if action == "preflight.run":
                exact_keys(payload, set())
                self._preflight = preflight_snapshot()
                return {"preflight": self._preflight}
            if action == "wizard.plan":
                require(self.github is not None, ErrorCode.UNSUPPORTED_MODULE)
                application = "application_revision" in payload
                upgrade = "upgrade_profile_sha256" in payload
                require(not (application and upgrade))
                exact_keys(payload, {"modules", "refs", "mode"} | ({"application_revision"} if application else set())
                           | ({"upgrade_profile_sha256"} if upgrade else set()))
                self._preflight = preflight_snapshot()
                require(self._preflight["ok"], ErrorCode.VALIDATION_FAILED)
                if application:
                    require(payload["modules"] == ["web"] and payload["mode"] == "fresh" and payload["refs"] == {})
                    if self.packages.profile() is not None: self.mariadb.assert_ready()
                    return {"installation": self.application.plan(payload["application_revision"])}
                if upgrade:
                    require(payload["modules"] == ["web"] and payload["mode"] == "upgrade" and payload["refs"] == {})
                    return {"installation": self.upgrade.plan(payload["upgrade_profile_sha256"])}
                return {"installation": self.github.plan(payload)}
            if action == "wizard.reset-plan":
                exact_keys(payload, {"confirm", "confirmation"})
                require(payload["confirm"] is True, ErrorCode.CONFIRMATION_REQUIRED)
                self.engine.discard_unapproved(payload["confirmation"])
                self.engine.registry = default_registry()
                return {"installation": None, "draft": self.wizard.read()}
            if action in {"github.validate", "github.plan", "github.clear"}:
                require(self.github is not None, ErrorCode.UNSUPPORTED_MODULE)
                if action == "github.validate":
                    exact_keys(payload, {"credential"})
                    return {"github": self.github.access.validate(payload["credential"])}
                if action == "github.clear":
                    exact_keys(payload, set())
                    self.github.access.clear()
                    return {"github": self.github.access.status()}
                return {"installation": self.github.plan(payload)}
            if action == "plan":
                # Production Phase 2 is core-check only. No pretend deployment.
                exact_keys(payload, {"modules"})
                require(payload["modules"] == ["core"], ErrorCode.UNSUPPORTED_MODULE)
                document = self.engine.plan()
            else:
                keys = {"confirmation", "confirm"}
                if action == "retry":
                    keys.add("name")
                elif action == "rollback":
                    keys.add("boundary")
                else:
                    require(action in {"apply", "resume"})
                exact_keys(payload, keys)
                require(payload["confirm"] is True, ErrorCode.CONFIRMATION_REQUIRED)
                confirmation = payload["confirmation"]
                if not self.application.restore(): self.upgrade.restore()
                if action in {'apply', 'resume', 'retry'} and self.application.owns(self.engine.report()) and self.packages.profile() is not None:
                    require(confirmation == self.engine.report()['plan_sha256'], ErrorCode.CONFIRMATION_REQUIRED)
                    self.mariadb.assert_ready()
                if action == 'rollback' and self.upgrade.owns(self.engine.report()):
                    activation = self.activation.journal.read()
                    require(activation is None or activation['approved_plan_sha256'] is None, ErrorCode.MANUAL_ACTION_REQUIRED)
                if self.github is not None and action in {"apply", "resume", "retry"}:
                    self.github.verify_completed()
                if action == "apply":
                    document = self.engine.apply(confirmation)
                elif action == "resume":
                    document = self.engine.resume(confirmation)
                elif action == "retry":
                    document = self.engine.retry(payload["name"], confirmation)
                else:
                    document = self.engine.rollback(payload["boundary"], confirmation)
            if self.github is not None and action in {"apply", "resume", "retry", "rollback"}:
                # Keep credentials only while a selection can still require downloads.
                if document["state"] in {"DONE", "FAILED", "MANUAL_ACTION_REQUIRED", "ROLLED_BACK"}:
                    self.github.access.clear()
            if action in {"apply", "resume", "retry", "rollback"} and document["state"] == "DONE":
                self.application.clear()
            return {"installation": document}

    def import_transition_package(self, confirmation, stream, length):
        with self._activity(), self._mutation():
            return {"gateway_transition_execution": self.gateway_transition_execution.import_package(confirmation, stream, length)}

    def import_gateway_package(self, confirmation, stream, length):
        with self._activity(), self._mutation():
            return {'gateway': self.gateway.execute('import', {'confirmation': confirmation, 'confirm': True},
                                                     stream=stream, length=length)}

    def import_fcm_credential(self, confirmation, stream, length):
        with self._activity(), self._mutation():
            return {'fcm': self.fcm.import_file(confirmation, stream, length)}

    def close(self) -> None:
        # Browser disconnection never cancels a mutation. Graceful bootstrap
        # shutdown waits for active, bounded adapters before clearing credentials.
        with self._condition:
            self._closing = True
            self._condition.wait_for(lambda: self._active == 0)
        if self.github is not None:
            self.github.access.clear()
        self.engine.secrets.clear()
        self.mariadb.secrets.clear()
