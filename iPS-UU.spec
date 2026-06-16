# PyInstaller spec for the iPS-UU desktop GUI.

from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path


block_cipher = None
local_idevicerestore = Path("tools/idevicerestore")
source_tree_idevicerestore = Path("idevicerestore-1.0.0/src/idevicerestore")
optional_binaries = []
if local_idevicerestore.exists():
    optional_binaries.append((str(local_idevicerestore), "tools"))
elif source_tree_idevicerestore.exists():
    optional_binaries.append((str(source_tree_idevicerestore), "tools"))
for optional_tool in ("tools/palera1n", "tools/turdus_merula", "tools/turdusra1n"):
    optional_tool_path = Path(optional_tool)
    if optional_tool_path.exists():
        optional_binaries.append((str(optional_tool_path), "tools"))

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
        ("tools/cfgutil", "tools"),
        ("assets/icons/ips-uu.icns", "assets/icons"),
        ("assets/icons/ips-uu.ico", "assets/icons"),
        ("assets/icons/png/ips-uu-icon-main-1024.png", "assets/icons/png"),
    ],
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
)
