"""ideviceinstaller wrapper service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ips_uu.services.backend_runner import BackendRunner
from ips_uu.services.tool_discovery import discover_tools


RUNNER = BackendRunner("ideviceinstaller")


def find_tool() -> dict[str, Any]:
    inventory = discover_tools()
    return next((tool for tool in inventory["tools"] if tool["name"] == "ideviceinstaller"), {})


def build_install_plan(ipa_path: str, udid: str | None = None) -> dict[str, Any]:
    tool = find_tool()
    path = Path(str(tool.get("path") or "tools/ideviceinstaller"))
    command = [str(path)]
    if udid:
        command.extend(["-u", udid])
    command.extend(["-i", ipa_path])
    return RUNNER.build_plan(
        command,
        "Install an IPA through ideviceinstaller on a trusted connected device.",
        ["This changes installed apps on the selected device.", "The device must be trusted and unlocked.", "Check local law and app licensing obligations before use."],
    )


def execute_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return RUNNER.run(list(plan.get("command") or []))


def cancel() -> dict[str, Any]:
    return RUNNER.cancel()
