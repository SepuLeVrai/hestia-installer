#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'ERREUR: Python 3 est requis.\n' >&2
    exit 1
fi

cd "$ROOT_DIR"
exec python3 -I -m installer "$@"
