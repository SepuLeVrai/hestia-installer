#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'ERREUR: Python 3 est requis.\n' >&2
    exit 1
fi

exec python3 -I -c '
import pathlib
import sys
root = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(root))
from installer.cli import main
raise SystemExit(main(sys.argv[1:]))
' "$ROOT_DIR" "$@"
