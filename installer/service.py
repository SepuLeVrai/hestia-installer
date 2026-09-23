"""Typed HTTPS facade. UI wiring is deliberately deferred to the wizard phase."""
from __future__ import annotations

import threading
from contextlib import contextmanager

from installer.engine import TransactionEngine
from installer.model import ErrorCode, InstallerError, exact_keys, require

POST_ROUTES = {
    "/api/installation/plan": "plan",
    "/api/installation/apply": "apply",
    "/api/installation/resume": "resume",
    "/api/installation/retry": "retry",
    "/api/installation/rollback": "rollback",
}
GET_ROUTES = frozenset({"/api/installation/state", "/api/installation/report"})


class TransactionService:
    def __init__(self, engine: TransactionEngine) -> None:
        self.engine = engine
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

    def report(self) -> dict:
        with self._activity():
            return {"installation": self.engine.report()}

    def execute(self, action: str, payload: dict) -> dict:
        with self._activity():
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
                if action == "apply":
                    document = self.engine.apply(confirmation)
                elif action == "resume":
                    document = self.engine.resume(confirmation)
                elif action == "retry":
                    document = self.engine.retry(payload["name"], confirmation)
                else:
                    document = self.engine.rollback(payload["boundary"], confirmation)
            return {"installation": document}

    def close(self) -> None:
        # Browser disconnection never cancels a mutation. Graceful bootstrap
        # shutdown waits for active, bounded adapters before clearing credentials.
        with self._condition:
            self._closing = True
            self._condition.wait_for(lambda: self._active == 0)
        self.engine.secrets.clear()
