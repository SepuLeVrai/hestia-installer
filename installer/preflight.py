from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from installer.constants import REQUIRED_BOOTSTRAP_COMMANDS


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def run_read_only_preflight() -> list[CheckResult]:
    os_release = _read_os_release()
    distro = os_release.get("PRETTY_NAME") or platform.platform()

    results = [
        CheckResult("os", bool(distro), distro),
        CheckResult(
            "root",
            os.geteuid() == 0,
            "root" if os.geteuid() == 0 else "non-root - acceptable pour --check uniquement",
        ),
        CheckResult("python", True, platform.python_version()),
    ]

    for command in REQUIRED_BOOTSTRAP_COMMANDS:
        found = shutil.which(command)
        results.append(CheckResult(f"command:{command}", found is not None, found or "absent"))

    return results
