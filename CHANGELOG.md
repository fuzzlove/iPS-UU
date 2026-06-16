# Changelog

## Unreleased

- Prepared first-release packaging metadata, PyInstaller bundled docs/tool inventory inputs, release checklist, screenshot placeholders, and a `scripts/check_release.sh` verifier.
- Added a professional PySide6 desktop GUI for iPS-UU restore research.
- Added Dashboard, Device / Target, Firmware / IPSW, Restore Research / Dry Run, Logs, Settings, and About views.
- Added service modules for unit-testable device detection, IPSW parsing, restore dry-run planning, settings, and structured logging.
- Added dry-run-only GUI safety defaults, disabled destructive GUI execution, status cards, live logs, and clear refusal messaging.
- Added PyInstaller packaging support through `iPS-UU.spec` and `scripts/build_gui.sh`.
- Added a Contents Requirements research service, CLI command, GUI page, and documentation mapping reverse-engineered findings to safe Python implementation status.
- Added supported dependency auto-detection through `restore-research setup-deps` and a GUI Settings action without copying proprietary/private restore binaries.
- Added automatic local `tools/idevicerestore` detection and PyInstaller inclusion for normal Apple-signed restore handoff.
- Added `restore-research method-run` safe adapters for observed restore methods, with explicit blocked refusals for SHSH/private/out-of-scope flows.
- Added `tools/cfgutil` wrapper support for Apple Configurator 2 without copying Apple binaries or private frameworks.
- Enabled guarded GUI execution for normal signed restore/update operations with dry-run-only default and multiple destructive confirmations.
- Added professional iPS-UU application icon assets, including SVG variants, exported PNG sizes, `.icns`, and `.ico`.
- Replaced the default Python launcher icon by setting the iPS-UU icon at Qt runtime and in the PyInstaller macOS app bundle.
- Added a safe 3uTools-style Super Restore adapter for backup/data restore through `idevicebackup2` or `cfgutil restore-backup`.
- Added a clean-room iOS Device Viewer using libimobiledevice-style tools for USB device listing, metadata, pairing/trust status, unlock guidance, diagnostics, a static device visual, and a safe screen-preview placeholder.
- Expanded iOS Device Viewer metadata with model name, serial, logic-board identifiers, ECID, model ID, firmware version, IMEI, Wi-Fi/Bluetooth addresses, device storage/free space, and guarded restart/shutdown/recovery controls.
- Added an External Tools GUI inventory page with passive palera1n detection, version/permission/signature/hash metadata, static compatibility notes, and explicit no-execution policy.
- Added a dedicated palera1n GUI tab for guide-based external prerequisite documentation, static compatibility preflight, caveat acknowledgements, and no-command guidance logs.
- Added a clean-room SHSH/APTicket blob inspector for local plist-style blob metadata, nonce/generator field extraction, and optional expected-value comparison without restore use or signing bypass behavior.
- Added a Turdus Merula GUI workflow wrapper for tool discovery, permissions repair, device/IPSW preflight, DFU guidance, dry-run workflow planning, and session logs.
- Refactored the Turdus Merula GUI into a manual-prerequisite workflow: iPS-UU no longer presents pwnDFU/exploit execution actions, and now performs passive device-state and artifact-path validation only.
