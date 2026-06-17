from __future__ import annotations

from pathlib import Path

from ips_uu.services import tool_resolver as resolver


def test_resolve_idevicerestore_skips_wrong_host_architecture(tmp_path: Path, monkeypatch) -> None:
    wrong_arch = tmp_path / "idevicerestore"
    wrong_arch.write_bytes(b"macho")
    wrong_arch.chmod(0o755)

    monkeypatch.setattr(resolver, "LOCAL_TOOLS_IDEVICERESTORE", wrong_arch)
    monkeypatch.setattr(resolver, "LOCAL_IDEVICERESTORE", tmp_path / "missing-source-tree")
    monkeypatch.setattr(resolver.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        resolver,
        "_binary_architectures",
        lambda path: {
            "kind": "mach-o",
            "architectures": ["x86_64"],
            "host_architecture": "arm64",
            "compatible": False,
            "reason": "missing host architecture arm64",
        },
    )

    candidates = resolver.idevicerestore_candidates()

    local = next(item for item in candidates if item["source"] == "local_tools_directory")
    assert local["present"] is True
    assert local["executable"] is True
    assert local["usable"] is False
    assert local["unusable_reason"] == "missing host architecture arm64"
    assert resolver.resolve_idevicerestore() is None


def test_resolve_idevicerestore_uses_compatible_binary(tmp_path: Path, monkeypatch) -> None:
    native = tmp_path / "idevicerestore"
    native.write_bytes(b"macho")
    native.chmod(0o755)

    monkeypatch.setattr(resolver, "LOCAL_TOOLS_IDEVICERESTORE", native)
    monkeypatch.setattr(resolver, "LOCAL_IDEVICERESTORE", tmp_path / "missing-source-tree")
    monkeypatch.setattr(resolver.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        resolver,
        "_binary_architectures",
        lambda path: {
            "kind": "mach-o",
            "architectures": ["arm64", "x86_64"],
            "host_architecture": "arm64",
            "compatible": True,
            "reason": "compatible",
        },
    )

    assert resolver.resolve_idevicerestore() == str(native)


def test_resolve_idevicerestore_rejects_wrapper_without_native_binary(tmp_path: Path, monkeypatch) -> None:
    wrapper = tmp_path / "idevicerestore"
    wrapper.write_text("#!/bin/sh\nexit 126\n", encoding="utf-8")
    wrapper.chmod(0o755)

    monkeypatch.setattr(resolver, "LOCAL_TOOLS_IDEVICERESTORE", wrapper)
    monkeypatch.setattr(resolver, "LOCAL_IDEVICERESTORE", tmp_path / "missing-source-tree")
    monkeypatch.setattr(resolver.shutil, "which", lambda _name: None)
    monkeypatch.setattr(resolver, "_host_architecture", lambda: "arm64")
    monkeypatch.setattr(
        resolver,
        "_binary_architectures",
        lambda path: {
            "kind": "script",
            "architectures": [],
            "host_architecture": "arm64",
            "compatible": True,
            "reason": "script",
        },
    )

    local = next(item for item in resolver.idevicerestore_candidates() if item["source"] == "local_tools_directory")

    assert local["usable"] is False
    assert "idevicerestore.arm64" in local["unusable_reason"]
    assert resolver.resolve_idevicerestore() is None
