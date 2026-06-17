"""idevicerestore wrapper service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ips_uu.services.backend_runner import BackendRunner, practical_risks
from ips_uu.services.tool_discovery import discover_tools


RUNNER = BackendRunner("idevicerestore")


def find_tool() -> dict[str, Any]:
    inventory = discover_tools()
    return next((tool for tool in inventory["tools"] if tool["name"] == "idevicerestore"), {})


def build_restore_plan(ipsw_path: str, erase: bool = True, extra_args: list[str] | None = None) -> dict[str, Any]:
    tool = find_tool()
    path = Path(str(tool.get("path") or "tools/idevicerestore"))
    command = [str(path)]
    if erase:
        command.append("--erase")
    if extra_args:
        command.extend(extra_args)
    command.append(ipsw_path)
    return RUNNER.build_plan(command, "iOS firmware restore/update through idevicerestore", practical_risks())


def execute_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return RUNNER.run(list(plan.get("command") or []))


def cancel() -> dict[str, Any]:
    return RUNNER.cancel()
