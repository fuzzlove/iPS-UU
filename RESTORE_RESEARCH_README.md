# restore-research

`restore-research` is a safe proof-of-concept wrapper for researching lawful Apple-signed restore workflows on macOS.

It does not bypass Apple signing, SEP/baseband checks, APTicket validation, APNonce validation, activation, or device security policy. It does not patch manifests, tickets, firmware images, bootchain files, ramdisks, or trust checks.

## Commands

Dry-run a restore plan:

```sh
restore-research restore --ipsw ./firmware.ipsw --dry-run
```

Dry-run through the package dispatcher:

```sh
python3 -m ips_uu restore-research restore --ipsw ./firmware.ipsw --product-type iPhone13,2 --dry-run
```

Inventory local restore candidates:

```sh
restore-research inventory
```

Print restore/flash methods observed in the local `Contents` bundle:

```sh
restore-research methods
```

Run a safe adapter for an observed method:

```sh
restore-research method-run --method-id 3utools_custom_ipsw_import --ipsw ./firmware.ipsw --dry-run
restore-research method-run --method-id 3utools_signed_firmware_query --product-type iPhone13,2
restore-research method-run --method-id 3utools_dfu_recovery_flash --ipsw ./firmware.ipsw --dry-run
restore-research method-run --method-id 3utools_super_restore_apps_backup --backup-dir ./MobileSyncBackup --dry-run
```

The desktop GUI can also execute normal signed restore/update operations. Disable `Dry-run only mode` in Settings, select an IPSW, choose `restore` or `update`, and confirm the destructive prompts. GUI execution uses the same guarded backend plan as the CLI.

Print safe implementation requirements derived from the local `Contents` bundle:

```sh
restore-research requirements
```

Query public signed firmware metadata for a ProductType:

```sh
restore-research signed-firmwares --product-type iPhone13,2
```

Detect supported local restore tools and optionally save GUI/CLI settings:

```sh
restore-research setup-deps --write-settings
```

Execute a normal Apple-supported erase restore through Apple Configurator `cfgutil`:

```sh
restore-research restore --ipsw ./firmware.ipsw --backend cfgutil --execute --erase-device --i-understand-this-may-wipe-data
```

## Backends

Auto backend priority:

- local compiled `tools/idevicerestore`, if present
- `idevicerestore`, if installed on `PATH`
- Apple Configurator `cfgutil`, if installed

Apple Configurator backend:

- `tools/cfgutil`, an iPS-UU wrapper for `/Applications/Apple Configurator.app/Contents/MacOS/cfgutil`

Apple Configurator requirements:

- Apple Configurator installed in `/Applications`.
- Apple Configurator's own support frameworks remain inside Apple's app bundle.
- Network access may be required for Apple signing, activation, and restore validation.
- Device state must be supported by Apple Configurator for the requested action.

Private and internal tools are inventory-only. `restore-research` does not call `MobileDevice.framework`, AuthInstall private APIs, `mobile_restore`, `prestore`, `factory_purple_restore`, `goldrestore`, or similar factory tools.

`setup-deps` does not copy or bundle Apple binaries. It records supported installed tool paths, uses the local `tools/cfgutil` wrapper when Apple Configurator is installed, and leaves proprietary/private components as research-only inventory.

When a compiled `tools/idevicerestore` binary is present, iPS-UU detects it automatically and can package it into the PyInstaller app bundle. It still performs only normal Apple-signed restore handoff and does not add unsigned restore flags.

The `Contents` / 3uTools audit adds these method categories as inventory:

- online bundled `libidevicerestore` restore: usable through the safe local/PATH `idevicerestore` adapter.
- online signed firmware discovery: usable as public metadata lookup only.
- offline IPSW metadata import/preflight: usable as offline plist/compatibility inspection.
- DFU/recovery restore flow: usable only as normal signed restore handoff through supported backends.
- backup/app/data restore: usable through `idevicebackup2 restore` or `cfgutil restore-backup`.
- SHSH query/download UI: blocked.
- private `iTunesFlash` MobileDevice restore helper: blocked.

Blocked methods are not hidden placeholders. They are intentionally refused because they require SHSH/APTicket handling or private restore APIs outside iPS-UU's signed restore scope.

The `requirements` command maps these findings to Python implementation status, release dependencies, optional external tools, and blocked research areas.

## Dry-Run Output

The dry-run prints JSON with:

- detected device mode and identifiers when safely available
- IPSW product/build metadata
- selected BuildIdentity
- ProductType compatibility
- signing status as locally unverified
- selected candidate backend
- exact command/handshake plan
- hard refusal policy

## Limitations

Dry-run cannot prove Apple signing status locally. Normal restore signing is device, nonce, board, build, and component specific. Execution relies on Apple-supported tooling to perform live personalization and validation, and any validation failure is terminal.

Offline restore is not supported. A lawful normal restore requires Apple personalization for the exact device state unless the operating system/backend already has a supported cached mechanism, and none was found as a maintainable public backend in this local audit.

## Desktop GUI

iPS-UU now includes an optional PySide6 desktop interface for restore research dry-runs.

Install GUI dependencies:

```sh
python3 -m pip install -e '.[gui]'
```

Run the GUI:

```sh
ips-uu-gui
```

or:

```sh
python3 -m ips_uu.gui.app
```

The GUI preserves the CLI/core logic and keeps destructive execution disabled. It provides:

- Dashboard cards for tool status, device state, firmware/build, compatibility, and latest dry-run.
- Device / Target detection with clear empty state.
- Firmware / IPSW parsing for `BuildManifest.plist` and `Restore.plist`.
- Restore Research / Dry Run preflight steps and refusal messages.
- Restore Methods and Contents Requirements views for local reverse-engineering findings.
- Live logs with clear, export, and open-folder actions.
- Local settings for backend preference, paths, verbose logging, dry-run-only mode, and theme.
- Auto Detect Tools action for supported in-place dependency setup.

### Screenshot Placeholders

Add screenshots here after packaging the first GUI build:

- `docs/screenshots/dashboard.png`
- `docs/screenshots/device-target.png`
- `docs/screenshots/restore-dry-run.png`
- `docs/screenshots/logs.png`

### PyInstaller Build

Build a local desktop bundle:

```sh
./scripts/build_gui.sh
```

The build uses `iPS-UU.spec` and writes artifacts under `dist/`.
