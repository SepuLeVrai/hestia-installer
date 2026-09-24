#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
case "${1:-}" in
    '') ./scripts/quality-local.sh ;;
    --browser-only) ;;
    --bridge-only) ;;
    *) printf 'Usage: %s [--browser-only|--bridge-only]\n' "$0" >&2; exit 2 ;;
esac
for tool in python3 node chromium openssl; do
    command -v "$tool" >/dev/null || { printf 'Required browser Quality tool missing: %s\n' "$tool" >&2; exit 1; }
done
python3 -c 'import playwright.sync_api'
test -s installer/web/assets/hestia-hero.webp
python3 scripts/quality.py run bridge
if [[ "${1:-}" == --bridge-only ]]; then
    printf '\nPartial verification: DOM bridge only. Native browser Quality not executed.\n'
else
    python3 scripts/quality.py run native
    printf '\nHESTIA Installer browser Quality (bridge + native HTTPS): PASS\n'
fi
