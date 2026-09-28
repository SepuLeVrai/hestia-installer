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

POST_ROUTES = {
    **{'/api/web/activation/' + action: 'activation.' + action for action in ('plan', 'apply', 'resume', 'retry', 'check')},
    "/api/web/setup": "web.setup",
    "/api/web/credentials": "web.credentials",
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


class TransactionService:
    def __init__(self, engine: TransactionEngine, *, github=None) -> None:
        self.engine = engine
        self.github = github
        self.wizard = WizardDraft(engine)
        self.application = ApplicationPlan(engine, github)
        self.application.restore()
        self.activation = ActivationPlan(self.application)
        self._preflight = None
        self._mutation_lock = threading.Lock()
        self._condition = threading.Condition()
        self._active = 0
        self._closing = False

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
                    "application": self.application.state(), "activation": self.activation.state()}

    def github_status(self) -> dict:
        with self._activity(), self._mutation():
            require(self.github is not None, ErrorCode.UNSUPPORTED_MODULE)
            return {"github": self.github.access.status()}

    def clear_credentials(self) -> None:
        with self._activity(), self._mutation():
            if self.github is not None:
                self.github.access.clear()
            self.application.clear()

    def report(self) -> dict:
        with self._activity():
            result = {"installation": self.engine.report()}
            activation = self.activation.state()
            if activation['installation'] is not None: result['activation'] = activation
            return result

    def execute(self, action: str, payload: dict) -> dict:
        with self._activity(), self._mutation():
            if action.startswith('activation.'):
                return {"activation": self.activation.execute(action.removeprefix('activation.'), payload)}
            if action == "web.setup":
                self.application.save(payload)
                return {"application": self.application.state()}
            if action == "web.credentials":
                return {"application": self.application.renew(payload)}
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
                exact_keys(payload, {"modules", "refs", "mode"} | ({"application_revision"} if application else set()))
                self._preflight = preflight_snapshot()
                require(self._preflight["ok"], ErrorCode.VALIDATION_FAILED)
                if application:
                    require(payload["modules"] == ["web"] and payload["mode"] == "fresh" and payload["refs"] == {})
                    return {"installation": self.application.plan(payload["application_revision"])}
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
                self.application.restore()
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

    def close(self) -> None:
        # Browser disconnection never cancels a mutation. Graceful bootstrap
        # shutdown waits for active, bounded adapters before clearing credentials.
        with self._condition:
            self._closing = True
            self._condition.wait_for(lambda: self._active == 0)
        if self.github is not None:
            self.github.access.clear()
        self.engine.secrets.clear()
