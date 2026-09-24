#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
command -v node >/dev/null
command -v chromium >/dev/null
python3 -c 'import playwright.sync_api'
./scripts/quality-local.sh
python3 tests/browser_wizard.py
printf '\nHESTIA Installer wizard quality: PASS\n'
