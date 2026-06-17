from __future__ import annotations

from pathlib import Path

import scripts.audit_tool_architectures as audit


def test_architecture_info_accepts_universal_macho(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "tool"
    tool.write_bytes(b"\xcf\xfa\xed\xfe")
    tool.chmod(0o755)

    def fake_run(command: list[str]) -> tuple[int, str]:
        if command[0] == "file":
            return 0, f"{tool}: Mach-O universal binary"
        return 0, "x86_64 arm64"

    monkeypatch.setattr(audit, "run", fake_run)

    result = audit.architecture_info(tool)

    assert result["ok"] is True
    assert result["architectures"] == ["arm64", "x86_64"]


def test_architecture_info_rejects_single_arch_macho(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "tool"
    tool.write_bytes(b"\xcf\xfa\xed\xfe")
    tool.chmod(0o755)

    def fake_run(command: list[str]) -> tuple[int, str]:
        if command[0] == "file":
            return 0, f"{tool}: Mach-O 64-bit executable arm64"
        return 0, "arm64"

    monkeypatch.setattr(audit, "run", fake_run)

    result = audit.architecture_info(tool)

    assert result["ok"] is False
    assert result["missing_architectures"] == ["x86_64"]


def test_architecture_info_accepts_executable_script(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)

    monkeypatch.setattr(audit, "run", lambda _command: (0, f"{tool}: POSIX shell script text executable"))

    result = audit.architecture_info(tool)

    assert result["ok"] is True
    assert result["kind"] == "script"


def test_idevicerestore_wrapper_requires_both_native_siblings(tmp_path: Path, monkeypatch) -> None:
    wrapper = tmp_path / "idevicerestore"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    x86 = tmp_path / "idevicerestore.x86_64"
    x86.write_bytes(b"macho")
    x86.chmod(0o755)

    def fake_run(command: list[str]) -> tuple[int, str]:
        target = command[-1]
        if command[0] == "file" and target.endswith("idevicerestore"):
            return 0, f"{target}: POSIX shell script text executable"
        if command[0] == "file":
            return 0, f"{target}: Mach-O 64-bit executable x86_64"
        return 0, "x86_64"

    monkeypatch.setattr(audit, "run", fake_run)

    result = audit.architecture_info(wrapper)

    assert result["ok"] is False
    assert result["kind"] == "script-wrapper"
    assert result["wrapped_tools"]["arm64"]["exists"] is False
