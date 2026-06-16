#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile \
  ips_uu/gui/app.py \
  ips_uu/restore_research.py \
  ips_uu/services/ios_device_viewer_service.py \
  ips_uu/services/palera1n_service.py \
  ips_uu/services/shsh_blob_service.py \
  ips_uu/services/turdus_merula_service.py

python3 -m pytest -q

python3 - <<'PY'
import ast
from pathlib import Path

ast.parse(Path("iPS-UU.spec").read_text(encoding="utf-8"))
print("release check ok")
PY
