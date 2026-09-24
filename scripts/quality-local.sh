#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
for tool in python3 node bash openssl ip; do
    command -v "$tool" >/dev/null || { printf 'Required Quality tool missing: %s\n' "$tool" >&2; exit 1; }
done
python3 scripts/quality.py static
python3 scripts/quality.py run core
printf '\nHESTIA Installer local target Quality: PASS\n'
