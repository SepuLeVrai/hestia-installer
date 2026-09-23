from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from installer.constants import REQUIRED_BOOTSTRAP_COMMANDS, SUPPORTED_DEBIAN_MAJORS


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    required_for_bootstrap: bool = True


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
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


def _debian_support(os_release: dict[str, str]) -> tuple[bool, str]:
    distro_id = os_release.get("ID", "").strip().lower()
    version_id = os_release.get("VERSION_ID", "").strip().split(".", 1)[0]
    pretty = os_release.get("PRETTY_NAME") or platform.platform()

    if distro_id != "debian":
        return False, f"{pretty} - Debian requis"

    try:
        major = int(version_id)
    except ValueError:
        return False, f"{pretty} - version Debian indéterminée"

    if major not in SUPPORTED_DEBIAN_MAJORS:
        supported = ", ".join(str(item) for item in sorted(SUPPORTED_DEBIAN_MAJORS))
        return False, f"{pretty} - versions supportées: {supported}"
    return True, pretty


def run_read_only_preflight(
    *,
    os_release_path: Path = Path("/etc/os-release"),
    geteuid: Callable[[], int] = os.geteuid,
    which: Callable[[str], str | None] = shutil.which,
) -> list[CheckResult]:
    os_release = read_os_release(os_release_path)
    os_ok, os_detail = _debian_support(os_release)
    python_ok = sys.version_info >= (3, 11)

    results = [
        CheckResult("os", os_ok, os_detail),
        CheckResult(
            "root",
            geteuid() == 0,
            "root" if geteuid() == 0 else "non-root - --check reste non destructif",
            required_for_bootstrap=False,
        ),
        CheckResult(
            "python",
            python_ok,
            platform.python_version() + ("" if python_ok else " - Python >= 3.11 requis"),
        ),
    ]

    for command in REQUIRED_BOOTSTRAP_COMMANDS:
        found = which(command)
        results.append(CheckResult(f"command:{command}", found is not None, found or "absent"))

    return results


def bootstrap_blockers(results: list[CheckResult]) -> list[CheckResult]:
    blockers = [item for item in results if item.required_for_bootstrap and not item.ok]
    root = next((item for item in results if item.name == "root"), None)
    if root is not None and not root.ok:
        blockers.append(CheckResult("root", False, "root requis pour démarrer l'installer"))
    return blockers
