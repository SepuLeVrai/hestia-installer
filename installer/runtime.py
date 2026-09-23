from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from installer.constants import DEFAULT_RUNTIME_ROOT


def create_private_staging(root: Path = DEFAULT_RUNTIME_ROOT) -> Path:
    root = Path(root)
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise RuntimeError(f"Runtime root non sûr: {root}")
    else:
        root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    staging = Path(tempfile.mkdtemp(prefix="session-", dir=root))
    os.chmod(staging, 0o700)
    return staging


def cleanup_staging(staging: Path, root: Path = DEFAULT_RUNTIME_ROOT) -> None:
    root = Path(root).resolve()
    staging = Path(staging)
    if staging.is_symlink():
        raise RuntimeError("Refus de supprimer un staging symbolique")
    resolved = staging.resolve()
    if resolved.parent != root or not resolved.name.startswith("session-"):
        raise RuntimeError("Refus de nettoyer un chemin hors du runtime HESTIA")
    if resolved.exists():
        shutil.rmtree(resolved)
    try:
        root.rmdir()
    except OSError:
        pass
