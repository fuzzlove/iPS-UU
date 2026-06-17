"""Standalone iOS device detection debug command."""

from __future__ import annotations

import json
import os
import platform
import shutil

from ips_uu.services.device_service import detect_target, resolve_tool


def main() -> int:
    payload = detect_target("auto")
    tools = {
        name: resolve_tool(name) or shutil.which(name)
        for name in ("idevice_id", "ideviceinfo", "irecovery", "pymobiledevice3", "system_profiler")
    }
    output = {
        "os_version": platform.platform(),
        "path": os.environ.get("PATH", ""),
        "available_tools": tools,
        "usb_device_tree_excerpt": ((payload.get("raw") or {}).get("usb") or {}).get("apple_entries") or [],
        "normal_mode_detection": ((payload.get("raw") or {}).get("normal") or {}),
        "recovery_dfu_detection": ((payload.get("raw") or {}).get("recovery_dfu") or {}),
        "final_normalized_identity": {
            key: payload.get(key)
            for key in (
                "product_type",
                "product_version",
                "build_version",
                "device_name",
                "ecid",
                "cpid",
                "bdid",
                "model_identifier",
                "current_mode",
                "detection_method",
                "marketing_name",
                "chip_family",
                "board_config",
            )
        },
        "recommended_next_action": ((payload.get("diagnostics") or {}).get("recommended_fix") or {}),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
