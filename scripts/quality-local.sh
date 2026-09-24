#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
for tool in python3 node bash openssl ip; do
    command -v "$tool" >/dev/null || { printf 'Required Quality tool missing: %s\n' "$tool" >&2; exit 1; }
done
python3 scripts/quality.py static
python3 scripts/quality.py run core
# Production CLI check runs on the supported OS under its actual required UID.
# A non-Debian/non-root development host must not claim this target check passed.
python3 - <<'PY'
import os
import platform
import subprocess
if os.geteuid() != 0 or platform.freedesktop_os_release().get('ID') != 'debian':
    raise SystemExit('Quality target preflight requires a disposable Debian environment as root.')
subprocess.run(['./install-hestia.sh', '--check'], check=True)
PY
printf '\nHESTIA Installer local target Quality: PASS\n'
