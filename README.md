# iPS-UU

iPS-UU is a Python framework for IPSW inspection and lawful Apple-signed restore research. It preserves CLI workflows while adding an optional desktop GUI for dry-run restore preflight.

## Desktop GUI

Install GUI dependencies:

```sh
python3 -m pip install -e '.[gui]'
```

Run:

```sh
ips-uu-gui
```

or:

```sh
python3 -m ips_uu gui
```

The GUI includes Dashboard, Device / Target, iOS Device Viewer, Firmware / IPSW, Restore Research / Dry Run, Restore Methods, External Tools, palera1n, Turdus Merula, Contents Requirements, Logs, Settings, and About views. It keeps execution disabled by default and uses the existing core restore research logic for dry-run planning.

### Screenshots

Placeholders for first release screenshots:

- `docs/screenshots/dashboard.png`
- `docs/screenshots/ios-device-viewer.png`
- `docs/screenshots/firmware-ipsw.png`
- `docs/screenshots/restore-dry-run.png`
- `docs/screenshots/external-tools.png`
- `docs/screenshots/palera1n.png`
- `docs/screenshots/logs.png`

## CLI

Existing CLI entry points remain available:

```sh
ips-uu --help
restore-research --help
restorectl --help
```

Research the local `Contents` bundle requirements:

```sh
restore-research requirements
restore-research methods
```

Run safe adapters for observed methods:

```sh
restore-research method-run --method-id 3utools_custom_ipsw_import --ipsw ./firmware.ipsw --dry-run
restore-research method-run --method-id 3utools_signed_firmware_query --product-type iPhone13,2
restore-research method-run --method-id 3utools_shsh_query_download --shsh-blob ./blob.shsh2 --product-type iPhone10,3 --expected-ecid 1234
restore-research method-run --method-id 3utools_super_restore_apps_backup --backup-dir ./MobileSyncBackup --dry-run
```

The SHSH/APTicket adapter is a local blob inspector only: it parses plist-style blob metadata, extracts ticket/nonce/generator fields when present, and compares optional expected values. It does not prove Apple cryptographic signature validity, fetch blobs, replay tickets, submit blobs to restore backends, or bypass signing.

Auto-detect supported local restore tools and save settings:

```sh
restore-research setup-deps --write-settings
```

If `tools/idevicerestore` is present, iPS-UU detects it automatically. Apple Configurator support is provided through `tools/cfgutil`, a local wrapper that executes Apple’s installed `/Applications/Apple Configurator.app/Contents/MacOS/cfgutil`.

The GUI defaults to dry-run only. To execute a real signed restore/update, disable `Dry-run only mode` in Settings, select an IPSW, choose `restore` or `update`, and use `Execute Signed Restore`. Execution still uses only `tools/idevicerestore` or `tools/cfgutil` and refuses bypass-style flows.

Optional public firmware metadata lookup:

```sh
restore-research signed-firmwares --product-type iPhone13,2
```

## Build

Build a PyInstaller desktop bundle:

```sh
./scripts/check_release.sh
./scripts/build_gui.sh
```

Artifacts are written under `dist/`.

## Branding Assets

Application icon assets are in `assets/icons/`:

- `ips-uu-icon.svg`: 1024x1024 master SVG.
- `ips-uu-icon-monochrome.svg`: monochrome variant.
- `ips-uu-icon-dark.svg`: dark mode variant.
- `png/`: exported 1024, 512, 256, 128, 64, 32, and 16 PNG sizes.
- `ips-uu.icns` and `ips-uu.ico`: packaged executable icon formats.

The GUI sets this icon at runtime, and the PyInstaller spec uses `ips-uu.icns` for the packaged macOS app so it does not show the default Python launcher icon.

## iOS Device Viewer

The `iOS Device Viewer` GUI tab is a clean-room connected-device panel. It uses documented/open-source libimobiledevice-style tools only:

- `idevice_id` for USB device listing.
- `ideviceinfo` for user-authorized device metadata.
- `idevicepair validate` for passive pairing/trust status.

It shows connected devices, a clean static device visual, masked UDIDs, device name, model name, serial number, logic board identifiers when exposed, ECID, model ID, firmware version, IMEI, Wi-Fi address, Bluetooth address, device storage/free space, connection status, pairing/trust status, and troubleshooting guidance. If metadata access fails because the device is locked or untrusted, the viewer prompts the user to unlock the device and tap `Trust This Computer`.

The device visual is drawn from scratch by iPS-UU and reflects metadata/status only. Live screen preview remains a placeholder and requires a supported, user-authorized capture backend; iPS-UU does not use private Apple APIs, exploit paths, jailbreak-only methods, or copied third-party behavior.

Clean-room limits:

- No code, binaries, resources, or private implementation details are copied from 3uTools.
- No jailbreak, exploit, restore, or privilege-escalation workflow is executed.
- Subprocess calls use explicit argument arrays, timeouts, captured output, and friendly GUI errors.
- Diagnostics mask device UDIDs except the last 6 characters.
- Restart, shutdown, enter recovery, and exit recovery controls use public libimobiledevice/irecovery commands only and require explicit confirmation.

## External Tools

The `External Tools` GUI tab inventories optional local tooling without launching it. If `tools/palera1n` is present, iPS-UU reports:

- Installed or missing status.
- Passive version output when available.
- File permissions and executable bit.
- SHA-256 hash.
- `file` metadata.
- macOS `codesign` information when available.
- Static device compatibility notes based on connected-device ProductType.

iPS-UU treats palera1n as an external dependency only. It does not execute, automate, launch, wrap, simplify, or expose jailbreak actions, and it does not provide one-click jailbreak functionality.

## palera1n

The `palera1n` GUI tab is a documentation-only workflow wrapper modeled after the iOS Guide running-palera1n instructions:

- Detects `tools/palera1n` and passive metadata.
- Detects the connected device and shows static A11-and-earlier/iOS 15+ compatibility guidance.
- Shows guide-derived caveats for USB cables, Apple Silicon USB-C behavior, A9(X)/pongoOS retry behavior, and A11 passcode/SEP limitations.
- Includes a `rootless` button that runs only `palera1n --version` as a passive metadata check.
- Includes an interactive terminal-style pane with allowlisted commands only: `help`, `clear`, `version`, `rootless`, `status`, and `guide`.
- Requires acknowledgement that any palera1n command must be run outside iPS-UU.
- Saves a guidance plan and preflight log without generating or executing a palera1n command.

iPS-UU never launches palera1n, enters DFU, jailbreaks, or modifies the connected device from this tab.

## Turdus Merula

The `Turdus Merula` GUI tab is a polished workflow wrapper for the bundled `tools/turdus_merula` and `tools/turdusra1n` toolchain.

What it does:

- Detects the bundled Turdus Merula tools and reports versions when available.
- Repairs executable permissions on bundled Turdus Merula files only.
- Detects connected device mode and maps known A9(X), A10, and A10X ProductTypes.
- Parses IPSW `BuildManifest.plist` and checks ProductType compatibility.
- Shows a manual prerequisite checklist: connect device, complete the required external prerequisite outside the app, return to iPS-UU, refresh device state, and continue only after verification.
- Shows tethered restore warnings, DFU/recovery readiness, disk space, and data-loss acknowledgement checks.
- Validates optional user-supplied artifact paths, such as SHSH/blob/manifest files, for existence only.
- Generates a dry-run workflow plan and session log under the iPS-UU log directory.

Limits:

- iPS-UU does not modify or reimplement Turdus Merula internals.
- iPS-UU does not execute, wrap, automate, hide, or simplify pwnDFU, exploit, or Turdus Merula commands from the GUI.
- iPS-UU does not parse, submit, patch, replay, or generate SHSH/blob material.
- Tethered restores require this computer/tool to boot the device every time.
- Some iOS 10 cellular A10X/iPhone 7 class restores may fail activation due to baseband compatibility.

Tool placement:

- Preferred current layout: `tools/turdus_merula` and `tools/turdusra1n`.
- Folder layout also works if the binaries are placed in `tools/turdus_merula/`.

Troubleshooting:

- Missing dependency: confirm the binaries are in `tools/` and use `Refresh Tools`.
- Permission issue: use `Repair Permissions` in the Turdus Merula tab.
- DFU/recovery not detected: complete the required external prerequisite outside iPS-UU, then click `Refresh Device Mode`.
- Failed compatibility: confirm the IPSW supports the detected ProductType.

Dry-run sessions write `preflight.json`, `restore_plan.json`, `command_preview.txt`, `stdout.log`, `stderr.log`, and `summary.txt` under the iPS-UU logs folder, with a temp-folder fallback when the home log directory is not writable. Turdus Merula session summaries log `Waiting for device in required mode` instead of launching prerequisite tooling.

## Safety

iPS-UU does not support unsigned downgrades, signing bypasses, SEP/baseband bypasses, APNonce manipulation, exploit chains, pwned DFU, firmware patching, ticket patching, or private entitlement abuse. Normal restore execution remains guarded by explicit CLI flags and Apple-supported validation paths.

The release does not copy or redistribute Apple Configurator’s `cfgutil` binary, Apple private frameworks, 3uTools, `iTunesFlash`, or private MobileDevice restore helpers. The setup flow detects supported tools already installed on the system and uses them in place.
