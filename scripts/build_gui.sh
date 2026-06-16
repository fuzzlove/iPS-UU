#!/usr/bin/env bash
set -euo pipefail

python3 -m pip install -e '.[gui]'
python3 -m PyInstaller iPS-UU.spec --clean --noconfirm
