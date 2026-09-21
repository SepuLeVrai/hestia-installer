#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m compileall -q installer tests
python3 -m unittest discover -s tests -v

printf '\nHESTIA Installer local quality: PASS\n'
