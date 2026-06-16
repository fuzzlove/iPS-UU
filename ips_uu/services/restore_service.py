"""Safe restore research service."""

from __future__ import annotations

import argparse
import subprocess
from typing import Any

from ips_uu.restore_research import RestoreResearchError, build_plan, enforce_execution_guardrails, inventory


def backend_inventory() -> dict[str, Any]:
    return inventory()


def dry_run_plan(
    ipsw: str,
    device: str = "auto",
    product_type: str | None = None,
    device_class: str | None = None,
    variant: str | None = None,
    current_build: str | None = None,
    action: str = "restore",
    backend: str = "auto",
) -> dict[str, Any]:
    args = argparse.Namespace(
        ipsw=ipsw,
        device=device,
        device_selector="ecid",
        product_type=product_type,
        device_class=device_class,
        variant=variant,
        current_build=current_build,
        action=action,
        backend=backend,
        dry_run=True,
        execute=False,
        erase_device=False,
        i_understand_this_may_wipe_data=False,
    )
    try:
        return build_plan(args)
    except RestoreResearchError:
        raise


def execute_restore(
    ipsw: str,
    device: str = "auto",
    product_type: str | None = None,
    device_class: str | None = None,
    variant: str | None = None,
    current_build: str | None = None,
    action: str = "restore",
    backend: str = "auto",
) -> dict[str, Any]:
    args = argparse.Namespace(
        ipsw=ipsw,
        device=device,
        device_selector="ecid",
        product_type=product_type,
        device_class=device_class,
        variant=variant,
        current_build=current_build,
        action=action,
        backend=backend,
        dry_run=False,
        execute=True,
        erase_device=True,
        i_understand_this_may_wipe_data=True,
    )
    plan = build_plan(args)
    enforce_execution_guardrails(args, plan)
    command = plan["candidate_restore_backend"]["command"]
    if not command:
        raise RestoreResearchError("no restore backend command is available")
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return {
        "plan": plan,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "succeeded": completed.returncode == 0,
    }
