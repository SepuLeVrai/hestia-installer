from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InstallState(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"


@dataclass(slots=True)
class StepRecord:
    name: str
    state: InstallState = InstallState.PLANNED
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "details": self.details,
        }
