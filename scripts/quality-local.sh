#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m compileall -q installer tests
python3 -m unittest discover -s tests -v

if command -v node >/dev/null 2>&1; then
    for file in installer/web/assets/*.js; do
        node --check "$file"
    done
fi

if grep -RInE '(shell=True|https?://[^[:space:]]*TOKEN|Authorization:[[:space:]]*(Bearer|token)[[:space:]]+[A-Za-z0-9])' installer tests --exclude='*.pyc'; then
    printf '\nHESTIA Installer security scan: FAIL\n' >&2
    exit 1
fi

if grep -RInE 'https?://' installer/web --include='*.html' --include='*.css' --include='*.js'; then
    printf '\nHESTIA Installer remote runtime asset scan: FAIL\n' >&2
    exit 1
fi

if [ -r /etc/os-release ] && grep -Eq '^ID=debian$|^ID="debian"$' /etc/os-release; then
    ./install-hestia.sh --check >/dev/null
fi

printf '\nHESTIA Installer local quality: PASS\n'
