#!/usr/bin/env bash
set -euo pipefail

PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/private/tmp/ips-uu-pycache}" python3 -m py_compile \
  ips_uu/gui/app.py \
  ips_uu/restore_research.py \
  ips_uu/services/ios_device_viewer_service.py \
  ips_uu/services/device_service.py \
  ips_uu/services/backend_runner.py \
  ips_uu/services/tool_discovery.py \
  ips_uu/services/ideviceinstaller_service.py \
  ips_uu/services/idevicerestore_service.py \
  ips_uu/services/mock_tss_service.py \
  ips_uu/services/purple_restore_service.py \
  ips_uu/services/restore_options_service.py \
  ips_uu/services/shsh_blob_service.py
PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/private/tmp/ips-uu-pycache}" python3 -m py_compile \
  app/__init__.py \
  app/device_debug.py

QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" python3 -m pytest -q

python3 scripts/audit_tool_architectures.py

python3 - <<'PY'
import ast
from pathlib import Path

ast.parse(Path("iPS-UU.spec").read_text(encoding="utf-8"))
print("release check ok")
PY

python3 - <<'PY'
from pathlib import Path

required_for_full_bundle = [
    "idevicerestore",
    "ideviceinstaller",
    "idevice_id",
    "ideviceinfo",
    "idevicepair",
    "idevicediagnostics",
    "ideviceenterrecovery",
    "irecovery",
    "idevicescreenshot",
]
missing = []
for name in required_for_full_bundle:
    direct = Path("tools") / name
    nested = Path("tools/libimobiledevice") / name
    if not direct.exists() and not nested.exists():
        missing.append(name)
if missing:
    print("bundle tool warning: missing from tools/: " + ", ".join(missing))
else:
    print("bundle tool check ok")
PY
