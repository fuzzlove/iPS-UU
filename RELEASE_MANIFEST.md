# Release Folder Manifest

This repository has been cleaned so large local audit inputs, app bundles, IPSW files, and decompiled artifacts are not present in the release tree.

## Include

- `ips_uu/`
- `pyproject.toml`
- `requirements.txt`
- `requirements-gui.txt`
- `requirements-dev.txt`
- `README.md`
- `RESTORECTL_README.md`
- `RESTORE_RESEARCH_README.md`
- `CHANGELOG.md`
- `docs/RELEASE_CHECKLIST.md`
- `docs/screenshots/.gitkeep`
- `iPS-UU.spec`
- `scripts/`
- `assets/icons/`
- `tools/idevicerestore` when a local compiled restore executor is shipped
- `tools/cfgutil` wrapper script
- `tools/palera1n` when the External Tools/palera1n inventory workflow is included
- `tools/turdus_merula` and `tools/turdusra1n` when the Turdus Merula workflow wrapper is included
- `findings.md`
- `restore_backend_map.json`
- `CONTENTS_3UTOOLS_RESTORE_AUDIT.md`
- `CONTENTS_REQUIREMENTS.md`
- `IPSW_DOWNGRADE_LOGIC.md`
- `ipsw_downgrade_planner.py`
- `tss_replay_listener.py`

## Exclude From Release Archives

- `Contents/`
- `ContentsAS/`
- `ContentsPR/`
- `DCSD/`
- `AtlasCore2`
- `installcoordination_host_test_runner`
- `decompiled*/`
- `idevicerestore-*/`
- `turdus_m3rula_*/`
- `Frameworks/`
- `*.app/`
- `*.ipsw`
- local copied Apple tool binaries such as Apple Configurator's real `cfgutil`
- `__pycache__/`
- `.DS_Store`
- build outputs such as `build/`, `dist/`, and `*.egg-info/`
- `.pytest_cache/`

## Notes

The excluded directories and binaries are research inputs or third-party tools. They are not required for the iPS-UU CLI/GUI runtime and should not be redistributed with the app.
