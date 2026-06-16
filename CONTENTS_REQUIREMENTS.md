# Contents Requirements Map

This document summarizes the safe implementation requirements derived from the local `Contents` application bundle research.

## Bundle Summary

- Observed app: `3uTools`
- Bundle identifier: `com.3uTools.mac`
- Observed version: `9.0.027`
- Important bundled components: `3uTools`, `iTunesFlash`, `libidevicerestore.dylib`, `libimobiledevice.dylib`, `libirecovery.dylib`, `libusbmuxd.dylib`, `libplist2.dylib`, `libdownload.dylib`, `libidm.1.0.0.dylib`

## Implemented In Python

- IPSW metadata import: offline `BuildManifest.plist` and `Restore.plist` parsing.
- Restore method catalog: visible inventory of observed restore/flash flows and safety status.
- Device discovery: safe metadata collection through available userland tools.
- Signed restore dry-run planning: compatibility checks and guarded backend handoff planning.
- Optional signed firmware metadata discovery: public metadata lookup only; Apple TSS validation is still required.

## Blocked

- Unsigned or offline iOS downgrade execution.
- SHSH/APTicket creation, replay, selection, or abuse.
- SEP/baseband mismatch bypass.
- Private `MobileDevice.framework` / AuthInstall restore execution.
- Pwned DFU, exploit chains, manifest patching, ticket patching, or firmware patching.

## Release Requirements

- `Python >= 3.10`
- `PySide6 >= 6.7` for the GUI.
- `pyinstaller >= 6.0` for local desktop packaging.
- Optional: Apple Configurator `cfgutil` for supported restore/update handoff.
- Optional: `ideviceinfo`, `irecovery`, and `idevicerestore` for device metadata and fallback comparison.

## Commands

Print the full requirements map:

```sh
restore-research requirements
```

Detect supported local restore tools and write settings:

```sh
restore-research setup-deps --write-settings
```

Query public signed firmware metadata for a device model:

```sh
restore-research signed-firmwares --product-type iPhone13,2
```

The signed firmware query is metadata only. It does not authorize a restore and does not enable offline restore. Normal restore execution still requires Apple-supported personalization and validation.

## Packaging Policy

iPS-UU does not copy or redistribute Apple Configurator, `cfgutil`, 3uTools, `iTunesFlash`, private MobileDevice helpers, or bundled third-party application binaries. Setup detects supported tools already installed on the system and stores their paths for in-place use.
