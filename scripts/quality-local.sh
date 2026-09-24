#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
for tool in python3 node bash openssl ip php useradd userdel setpriv prlimit mariadb-install-db mariadbd; do
    command -v "$tool" >/dev/null || { printf 'Required Quality tool missing: %s\n' "$tool" >&2; exit 1; }
done
[[ "${HESTIA_ACCOUNT_DB_TEST:-}" == 1 ]] || { printf 'Use HESTIA_ACCOUNT_DB_TEST=1 on a disposable SQL test host.\n' >&2; exit 1; }
find installer/private -type f -name '*.php' -print0 | sort -z | xargs -0 -n1 php -l
python3 scripts/quality.py static
python3 scripts/quality.py run core
printf '\nHESTIA Installer local target Quality: PASS\n'
