"""Subprocess runner for external backend tools.

Backends are always invoked with explicit argument lists. The runner stores a
per-run session log and can stream stdout/stderr lines to a caller-supplied
callback.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ips_uu.services.logging_service import get_log_dir
from ips_uu.services.mock_tss_service import validate_command_has_no_mock_ticket


StreamCallback = Callable[[str, str], None]


@dataclass
class BackendRun:
    command: list[str]
    session_dir: Path
    process: subprocess.Popen[str] | None = None
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def command_preview(self) -> str:
        return " ".join(subprocess.list2cmdline([part]) for part in self.command)


class BackendRunner:
    def __init__(self, log_namespace: str = "backend-runs") -> None:
        self.log_namespace = log_namespace
        self.current: BackendRun | None = None

    def create_session_dir(self) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base = get_log_dir() / self.log_namespace
        try:
            path = base / stamp
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            path = Path(tempfile.gettempdir()) / "ips-uu" / "logs" / self.log_namespace / stamp
            path.mkdir(parents=True, exist_ok=True)
            return path

    def build_plan(self, command: list[str], purpose: str, risks: list[str] | None = None) -> dict[str, Any]:
        validate_command_has_no_mock_ticket(command)
        return {
            "purpose": purpose,
            "command": command,
            "command_preview": " ".join(subprocess.list2cmdline([part]) for part in command),
            "risks": risks or practical_risks(),
            "shell": False,
        }

    def run(self, command: list[str], callback: StreamCallback | None = None, timeout: int | None = None) -> dict[str, Any]:
        if not command:
            raise ValueError("command must not be empty")
        validate_command_has_no_mock_ticket(command)
        session = self.create_session_dir()
        run = BackendRun(command=list(command), session_dir=session)
        self.current = run
        (session / "command.json").write_text(json.dumps({"command": run.command, "shell": False}, indent=2), encoding="utf-8")
        (session / "command_preview.txt").write_text(run.command_preview + "\n", encoding="utf-8")

        stdout_path = session / "stdout.log"
        stderr_path = session / "stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
            process = subprocess.Popen(
                run.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                bufsize=1,
            )
            run.process = process

            def pump(stream: Any, target: list[str], log_file: Any, name: str) -> None:
                for line in iter(stream.readline, ""):
                    target.append(line)
                    log_file.write(line)
                    log_file.flush()
                    if callback:
                        callback(name, line.rstrip("\n"))

            threads = [
                threading.Thread(target=pump, args=(process.stdout, run.stdout, stdout_file, "stdout"), daemon=True),
                threading.Thread(target=pump, args=(process.stderr, run.stderr, stderr_file, "stderr"), daemon=True),
            ]
            for thread in threads:
                thread.start()
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                run.cancelled = True
                process.terminate()
                returncode = process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=2)

        result = {
            "command": run.command,
            "command_preview": run.command_preview,
            "returncode": returncode,
            "stdout": "".join(run.stdout),
            "stderr": "".join(run.stderr),
            "cancelled": run.cancelled,
            "succeeded": returncode == 0 and not run.cancelled,
            "session_dir": str(session),
        }
        (session / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result

    def cancel(self) -> dict[str, Any]:
        run = self.current
        if not run or not run.process or run.process.poll() is not None:
            return {"cancelled": False, "reason": "no active backend process"}
        run.cancelled = True
        run.process.terminate()
        return {"cancelled": True, "session_dir": str(run.session_dir)}


def practical_risks() -> list[str]:
    return [
        "This may erase data.",
        "This may require tethered boot.",
        "This may affect activation.",
        "This may void warranty.",
        "This may fail and require recovery.",
        "Check local law before use.",
    ]
