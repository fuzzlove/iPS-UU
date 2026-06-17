# iPS-UU

iPS-UU is a professional device servicing and research interface for iOS restore, recovery, signed downgrade analysis, app install, firmware inspection, and detailed device statistics.

One professional workspace for iOS servicing and research.

Users are responsible for their device, data, warranty status, carrier obligations, and compliance with local law.

iPS-UU is a wrapper workspace. Bundled open-source tools remain external backends; the app discovers them, shows status/version/path/license hints, builds explicit command plans, streams output, exports logs, and explains practical risks. It focuses on firmware flashing, recovery, diagnostics, and uncommon device details rather than jailbreak or boot workflows.

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

The GUI includes Dashboard, Connected Device, Firmware / IPSW, Restore Options, Restore, Signing Simulator, Purple Restore, Downgrade, Apps / Install, Logs, Tools, Settings, and About views. The Restore tab exposes a confirmation-gated `Force Signed Flash` action that first builds a backend command plan, shows the exact command or blocker, and then runs only supported signed restore backends.

The sidebar motto is `Professional IPSW matters.`

### Screenshots

Placeholders for first release screenshots:

- `docs/screenshots/dashboard.png`
- `docs/screenshots/connected-device.png`
- `docs/screenshots/firmware-ipsw.png`
- `docs/screenshots/restore-options.png`
- `docs/screenshots/restore.png`
- `docs/screenshots/signing-simulator.png`
- `docs/screenshots/downgrade.png`
- `docs/screenshots/apps-install.png`
- `docs/screenshots/tools.png`
- `docs/screenshots/logs.png`

## CLI

Existing CLI entry points remain available:

```sh
ips-uu --help
restore-research --help
restorectl --help
```

Research the local reverse-engineering bundle requirements. The command uses `Contents/` when present and falls back to `rengineer/`, including the new `iTunesFlash`/`libidevicerestore` findings as non-executable guardrails:

```sh
restore-research requirements
restore-research requirements --root rengineer
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
- `idevicediagnostics`, `ideviceenterrecovery`, and `irecovery` for confirmed device actions and recovery-state checks.
- `idevicescreenshot` for an actual trusted-device screen preview when the device and host allow capture.

It shows connected devices, a clean static device visual, masked UDIDs, device name, model name, serial number, logic board identifiers when exposed, ECID, model ID, firmware version, hardware model, board/chip/die identifiers, device class, CPU architecture, activation state, baseband version and serial, region/color, battery state, IMEI/MEID, Wi-Fi address, Bluetooth address, device storage/free space, metadata domains queried, bundled tool status, connection status, pairing/trust status, and troubleshooting guidance. If metadata access fails because the device is locked or untrusted, the viewer prompts the user to unlock the device and tap `Trust This Computer`.

The Connected Device tab includes confirmed controls for shutdown, restart, enter recovery mode, exit recovery mode, and DFU instructions. DFU remains a physical button sequence; the app shows model-family instructions and then verifies state through the bundled tools. The screen preview uses `idevicescreenshot` and falls back to clear status text if the device is locked, untrusted, in a non-capturable mode, or the backend refuses capture.

For fuller fingerprinting, the release bundles the libimobiledevice utilities in `tools/`:

- `idevice_id`
- `ideviceinfo`
- `idevicepair`
- `idevicediagnostics`
- `ideviceenterrecovery`
- `irecovery`
- `ideviceinstaller`
- `idevicescreenshot`

The PyInstaller spec bundles these tools into the app. The release checker reports any missing tools so the packaged app does not silently lose device metadata or app-install support.

Bundled macOS Mach-O tools must be universal2 builds containing both `arm64` and `x86_64` slices so the release works on Apple Silicon and Intel Macs. Script wrappers such as `tools/cfgutil` are allowed when they delegate to host-installed tools. Run this audit before packaging:

```sh
python3 scripts/audit_tool_architectures.py
```

If a tool reports `missing arm64` or `missing x86_64`, replace it with a universal2 build or combine matching per-architecture builds with `lipo -create ... -output tools/<name>`.

When you have separate arm64 and x86_64 tool folders, stage the universal2 copies with:

```sh
./scripts/stage_universal_tools.sh /path/to/arm64-tools /path/to/x86_64-tools
```

The staging helper currently covers the libimobiledevice, libirecovery, ideviceinstaller, and idevicerestore command-line tools that must run natively on both Apple Silicon and Intel Macs.

`tools/idevicerestore` may be a small wrapper. In that layout it expects `tools/idevicerestore.arm64` on Apple Silicon and `tools/idevicerestore.x86_64` on Intel, and it prints a clear setup error when the native binary is missing instead of allowing macOS to fail with `bad CPU type in executable`.

The device visual is drawn from scratch by iPS-UU and reflects metadata/status only. Live screen preview uses supported, user-authorized capture tooling when available and otherwise falls back to clear status text; iPS-UU does not use private Apple APIs or copied third-party behavior.

Clean-room limits:

- No code, binaries, resources, or private implementation details are copied from 3uTools.
- No jailbreak, exploit, or privilege-escalation workflow is exposed.
- Subprocess calls use explicit argument arrays, timeouts, captured output, and friendly GUI errors.
- Diagnostics mask device UDIDs except the last 6 characters.
- Restart, shutdown, enter recovery, and exit recovery controls use public libimobiledevice/irecovery commands only and require explicit confirmation.

## External Tools

The `Tools` GUI tab inventories bundled and PATH-discoverable open-source tooling. Supported backend families include:

- `idevicerestore`
- `ideviceinstaller`
- libimobiledevice utilities such as `idevice_id`, `ideviceinfo`, `idevicepair`, `ideviceenterrecovery`, and `irecovery`
- future open-source restore, recovery, flashing, diagnostics, and app-install tools

For each tool, iPS-UU reports:

- Installed or missing status.
- Path.
- Executable status.
- Passive version output when available.
- Purpose.
- Supported workflows.
- Required device mode.
- Supported device families.
- Open-source license hint or bundled LICENSE/COPYING text when available.
- Diagnostics.
- Tool folder access.

The `Backend Inspector` mode reads open-source source files included in the repository and documents entry points, CLI arguments, supported devices, required files, output/error patterns, environment requirements, and workflow phases. It does not decompile closed-source binaries.

## Restore Options

The `Restore Options` tab, labeled internally as NovaCerts Restore Options, helps users understand legitimate restore, recovery, reinstall, and downgrade paths without bypassing Apple signing.

It shows current device status:

- ProductType.
- Model.
- Chip family.
- Current iOS version.
- Mode: normal, recovery, or DFU.
- ECID if available.

It evaluates available restore paths:

- Update to latest signed iOS.
- Restore latest signed iOS.
- Reinstall the same version only if that version is currently signed.
- Downgrade only if the selected target firmware is currently signed.

The firmware checker lets the user select an IPSW, parses `BuildManifest.plist`, checks device compatibility, checks public signing metadata when available, and reports one of these statuses: `Installable`, `Not installable`, `Unsupported device`, or `Signature unavailable`.

Reverse-engineering findings from the local `rengineer/` payload are integrated as documentation and safety policy. iPS-UU records that the bundled `libidevicerestore.dylib` follows a normal signed restore/TSS pipeline and that `MacOS/iTunesFlash` is a private `MobileDevice.framework` wrapper around `AMRestorableDeviceRestore`; neither becomes an unsigned downgrade or private API execution backend.

Restore-without-updating guidance is explicit: reinstalling the current iOS is a standard restore option only while that exact version remains signed; otherwise a standard restore normally moves to currently signed firmware. A device settings reset may erase user data but is not a firmware reinstall. Recovery restore normally requires signed firmware.

External restore backend support is shown transparently: tool name, supported device families, backend-defined iOS support notes, risks, command preview, dry-run availability, and log location.

## Signing Simulator

The `Signing Simulator` tab is a local-only mock signing service for UI tests, demos, screenshots, and workflow-state testing. It displays this banner:

```text
Simulation mode only. This does not contact Apple, does not generate valid SHSH/APTickets, and cannot authorize a real restore.
```

The simulator can produce JSON states such as approved, rejected, tethered-only, expired, and network error. Approved mock responses are accepted by the GUI as `restore_allowed_in_simulation_only` so the restore/flash workflow can move forward for UI testing, screenshots, demos, and application logic tests. Every response includes `simulation: true`, `ticket_type: mock`, and `valid_for_real_restore: false`.

The local mock API is available only when explicitly started with `--simulation`:

```sh
python3 -m ips_uu.services.mock_tss_service --simulation --host 127.0.0.1 --port 8765
```

Endpoints:

- `POST /mock-tss/request`
- `GET /mock-tss/status/<request_id>`
- `POST /mock-tss/decision`

Mock tickets can only be exported as `.mock.json`, such as `mock_ticket_iPhone10_5_20H240.mock.json`. The app blocks `.shsh`, `.shsh2`, `.apticket`, `.plist`, and similar real restore artifact names, and backend command runners reject `.mock.json` tickets before invoking restore tools. The `Simulate Restore/Flash` action creates a no-command plan; it never launches `idevicerestore`, `futurerestore`, `palera1n`, `turdus_merula`, `cfgutil`, or any other restore binary.

## Purple Restore Emulator

The `Purple Restore` tab is an internal-only workflow emulator for UI testing. It displays:

```text
Warning. This is to only be used by apple employees for internal use.
```

The emulator models Normal Mode, Recovery Mode, DFU Mode, Purple Restore Prepared, Ticket Requested, Ticket Approved, Restore Proceeding, Restore Failed, and Restore Complete states. It checks selected IPSW compatibility against the device ProductType and blocks cross-device firmware before any simulated proceed state.

The mock Tatsu flow is local-only and in-app only. It does not contact Apple, generate valid SHSH/APTicket data, alter trust decisions, authorize real restores, or launch restore binaries. Mock Purple Restore artifacts use `.purple.mock.json` naming and are rejected by backend command guardrails.

## Device Detection

Device detection uses multiple backends and records diagnostics for each attempt:

- Normal mode: `idevice_id -l`, `ideviceinfo`, optional `pymobiledevice3`, and macOS `system_profiler SPUSBDataType`.
- Recovery mode: `irecovery -q` and `system_profiler SPUSBDataType`.
- DFU mode: `irecovery -q`, `system_profiler SPUSBDataType`, and Apple USB vendor/product ID matching.

The normalized identity includes ProductType, ProductVersion, BuildVersion, DeviceName, ECID, CPID, BDID, model identifier, USB mode, backend used, and raw diagnostic output. The Connected Device tab includes a Device Diagnostics panel with command availability, command success/failure, stdout/stderr, detected USB entries, and a recommended fix.

Troubleshooting messages distinguish common cases:

- No normal-mode device found: unlock the device and tap Trust.
- Recovery/DFU device visible over USB, but `irecovery` is missing.
- USB device detected, but ProductType could not be resolved.
- Device appears to be in DFU mode; normal-mode tools will not identify it.
- Install libimobiledevice or configure tool paths in Settings.
- Apple Mobile Device stack not responding.

Standalone debug command:

```sh
python -m app.device_debug
```

It prints OS version, PATH, available tool paths, USB device tree excerpt, normal/recovery/DFU results, final normalized identity, and the recommended next action.

Normal, Recovery, and DFU are different USB states. Normal mode exposes lockdown metadata through libimobiledevice after trust. Recovery and DFU do not expose normal lockdown metadata; use `irecovery` and USB identifiers instead.

## Downgrade

The `Downgrade` tab provides a guided workflow:

- Select device.
- Select IPSW.
- Select backend tool.
- Check compatibility.
- Show signed restore status.
- Show risks.
- Dry run.
- Execute only after confirmation.

Successful standard downgrades require the target IPSW to be currently signed for the exact device. If Apple is no longer signing that firmware, normal restore backends will usually refuse it. Restores may still fail because of SEP, baseband, activation, cable, host, or device-mode requirements.

## Safety

Warnings are practical:

- This may erase data.
- This may update the device if the selected firmware is not signed.
- This may affect activation.
- This may void warranty.
- This may fail and require recovery.
- Check local law before use.

Normal restore execution remains guarded by explicit CLI flags and backend validation paths.

The release does not copy or redistribute Apple Configurator’s `cfgutil` binary, Apple private frameworks, 3uTools, `iTunesFlash`, or private MobileDevice restore helpers. The setup flow detects supported tools already installed on the system and uses them in place.
