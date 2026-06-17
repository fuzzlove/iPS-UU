# PyInstaller spec for the iPS-UU desktop GUI.

from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path


block_cipher = None
optional_binaries = []
optional_datas = []
bundled_tool_names = (
    "idevicerestore",
    "idevicerestore.arm64",
    "idevicerestore.x86_64",
    "ideviceinstaller",
    "idevice_id",
    "ideviceinfo",
    "idevicepair",
    "idevicediagnostics",
    "ideviceenterrecovery",
    "irecovery",
    "idevicescreenshot",
    "cfgutil",
)
for tool_name in bundled_tool_names:
    optional_tool = f"tools/{tool_name}"
    optional_tool_path = Path(optional_tool)
    if optional_tool_path.exists() and optional_tool_path.is_file():
        optional_binaries.append((str(optional_tool_path), "tools"))
    elif optional_tool_path.exists() and optional_tool_path.is_dir():
        optional_datas.append((str(optional_tool_path), f"tools/{tool_name}"))
    nested_tool_path = Path("tools/libimobiledevice") / tool_name
    if nested_tool_path.exists():
        optional_binaries.append((str(nested_tool_path), "tools/libimobiledevice"))
source_tree_idevicerestore = Path("idevicerestore-1.0.0/src/idevicerestore")
if not Path("tools/idevicerestore").exists() and source_tree_idevicerestore.exists():
    optional_binaries.append((str(source_tree_idevicerestore), "tools"))

a = Analysis(
    ["ips_uu/gui/app.py"],
    pathex=[],
    binaries=optional_binaries,
    datas=[
        ("README.md", "."),
        ("CHANGELOG.md", "."),
        ("RELEASE_MANIFEST.md", "."),
        ("RESTORE_RESEARCH_README.md", "."),
        ("RESTORECTL_README.md", "."),
        ("findings.md", "."),
        ("restore_backend_map.json", "."),
        ("data/ios_device_map.json", "data"),
        ("assets/icons/ips-uu.icns", "assets/icons"),
        ("assets/icons/ips-uu.ico", "assets/icons"),
        ("assets/icons/png/ips-uu-icon-main-1024.png", "assets/icons/png"),
        ("assets/support/donation-qr.png", "assets/support"),
    ] + optional_datas,
    hiddenimports=collect_submodules("ips_uu"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="iPS-UU",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/icons/ips-uu.icns",
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="iPS-UU",
)
app = BUNDLE(
    coll,
    name="iPS-UU.app",
    icon="assets/icons/ips-uu.icns",
    bundle_identifier="org.ipsuu.restore-research",
    version="0.1.1",
)
