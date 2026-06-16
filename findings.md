# Restore Backend Research Findings

Scope: local Apple/macOS restore-related tools, frameworks, logs, and symbols. This research does not implement unsigned downgrades, signature bypasses, SEP/baseband bypasses, APNonce manipulation, pwned DFU, or private entitlement abuse.

## Summary

The maintainable non-`idevicerestore` backend found locally is Apple Configurator's `cfgutil`:

`/Applications/Apple Configurator.app/Contents/MacOS/cfgutil`

It has a bundled manpage documenting restore/update with custom IPSW input and JSON/progress output. It is the only candidate I found that is both locally Apple-provided and callable as a userland command without directly linking private MobileDevice/AuthInstall APIs.

Private restore internals are present in `MobileDevice.framework`, Apple Configurator, launchd services, and local internal tools, but they are inventory-only for this project. They are not stable public APIs and should not be used as restore executors.

## Candidate Inventory

| Candidate | Path | Purpose | Userland callable | Permissions/entitlements | Stability risk | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Apple Configurator `cfgutil` | `/Applications/Apple Configurator.app/Contents/MacOS/cfgutil` | Command-line device management, including restore/update/revive | Yes | Normal USB/device access; Apple signing and activation policy still apply | Medium | `cfgutil.1` documents `restore | update [-I path to IPSW...]`, `--ecid`, `--format JSON`, `--progress` |
| Apple Configurator GUI | `/Applications/Apple Configurator.app` | GUI restore/update workflow and Automator actions | GUI only | Apple app internals and bundled/private frameworks | Medium | Info.plist imports IPSW type; bundle includes `Restore Devices.action`; strings include `restoreDevice:` and `installFirmwareWithURLs:targetedItems:` |
| MobileDevice.framework | `/System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice` | Private restore, TSS, personalization, DFU, and service APIs | Technically loadable, not supported | Private ABI; possible privileged USB/XPC/client expectations | High | `nm`/`strings` show `AMAuthInstall*`, TSS, APNonce/SepNonce, AMRestore/restorable symbols |
| DeviceRecovery daemon | `/System/Library/LaunchDaemons/com.apple.devicerecoveryd.plist` | DeviceRecovery Mach services | No documented CLI contract | Launchd-managed service and private protocol | High | MachServices include `com.apple.DeviceRecoveryEnvironmentService`, `com.apple.DeviceRecoveryOverrideService`, `com.apple.DeviceRecoveryService` |
| Mobile software update daemon | `/System/Library/LaunchDaemons/com.apple.mobile.softwareupdated.plist` | Mobile software update service | No documented restore CLI contract | Runs as `_softwareupdate` | High | LaunchDaemon exposes `com.apple.mobile.softwareupdated` |
| libimobiledevice tools | PATH: `ideviceinfo`, `irecovery`, `idevicerestore` | Detection and restore comparison | Yes | Normal USB/device access | Low | Existing dry-run code uses `ideviceinfo -x` and `irecovery -q`; `idevicerestore` remains fallback only |
| Internal restore tools | PATH: `mobile_restore`, `prestore`, `factory_purple_restore`, `factory_demo_restore`, `goldrestore`, `goldrestore2` | Factory/internal restore-style tooling | Not supported | Unknown; likely internal/factory assumptions | Critical | Inventory only; not used by the PoC |

## API And Symbol Map

Relevant public or observable workflow surfaces:

- `cfgutil restore -I <ipsw>`: Apple Configurator CLI erase restore path.
- `cfgutil update -I <ipsw>`: Apple Configurator CLI update path.
- `cfgutil --ecid <ECID>`: documented device selection; ECID remains available in recovery mode.
- `cfgutil --format JSON --progress`: documented machine-readable progress/error reporting.
- `ideviceinfo -x`: normal-mode device metadata for dry-run only.
- `irecovery -q`: recovery/DFU metadata for dry-run only.

Relevant private or unstable surfaces, not used for execution:

- `AMAuthInstallApCreateServerRequestDictionary`
- `AMAuthInstallApImg4CreateServerRequestDictionary`
- `AMAuthInstallApImg4SetApNonceSlotID`
- `AMAuthInstallApImg4SetSepNonce`
- `AMAuthInstallApSetNonce`
- `AMAuthInstallBasebandCreateServerRequestDictionary`
- `AMRestoreDeviceDefaultProductStringDFU`
- `AMRestoreDeviceDefaultProductStringRecovery`
- MobileDevice strings including `tss_submit`, `TSS/controller?action=2`, `composeTSSRequest`, `generateNoncesForUUID:type:withReply:`, `personalizeSuperBinary:signingServer:ssoOnly:`, and `com.apple.MobileDevice.MobileRestore`.

These symbols confirm restore internals exist locally, but they are private and unstable. The PoC does not link or call them.

## Log Evidence

A local unified log query for the last hour required unsandboxed read access and returned observable MobileDevice activity, including:

- `MobileDeviceUpdater`: `Received XPC event - Mux device was attached: iPhone`
- `MobileDeviceUpdater`: `Discovered a device. Asking if the device is restorable.`
- `MobileDeviceUpdater`: `Known, supported, and restorable device. Continuing...`
- `remoted`: returned service `com.apple.RestoreRemoteServices.restoreserviced`
- `CoreDeviceService`: attempted to mount `/Library/Developer/DeveloperDiskImages/iOS_DDI/Restore/...dmg` and failed because the device was locked

No completed signed restore session was observed in that one-hour window.

## Safe Restore Workflow Map

1. Device detection

Use `ideviceinfo -x` for normal mode and `irecovery -q` for recovery/DFU metadata when available. For Apple Configurator execution, prefer ECID selection through `cfgutil --ecid <ECID>` when ECID is known.

2. IPSW validation

Open the IPSW as a zip archive, read `BuildManifest.plist`, report `ProductVersion`, `ProductBuildVersion`, `SupportedProductTypes`, and selected `BuildIdentity`. Refuse execution if the detected/requested ProductType is not supported.

3. Signing/TSS check

Dry-run cannot prove Apple signing status locally because APTickets are device, nonce, board, build, and component specific. Execution delegates signing, APTicket, APNonce, SEP, and baseband validation to `cfgutil` or `idevicerestore` and stops on any failure.

4. Erase/update decision

`restore` maps to erase restore. `update` maps to a data-preserving update where the backend and device state support it. The `cfgutil` manpage notes recovery-mode restore/update erases the device.

5. Restore handoff

Preferred non-`idevicerestore` handoff:

```sh
/Applications/Apple\ Configurator.app/Contents/MacOS/cfgutil --format JSON --progress restore -I ./firmware.ipsw
```

Fallback/comparison:

```sh
idevicerestore -e ./firmware.ipsw
```

For non-erase update-style fallback, the PoC plans `idevicerestore ./firmware.ipsw` rather than an erase restore.

6. Progress/error reporting

Use `cfgutil --progress` with JSON output when available. Treat any validation, signing, APTicket, APNonce, SEP/baseband, or restore failure as terminal.

## Hard Refusals

- Unsigned firmware or missing Apple signing validation.
- Downgrade attempts unless a supported Apple backend confirms current signing during restore.
- SEP/baseband component overrides or mismatches.
- Missing or invalid APTicket, APNonce, nonce, or personalization validation.
- Requests to patch, fake, skip, ignore, or continue past validation.
- Custom signing servers, offline signing overrides, exploit chains, pwned DFU, or private entitlement abuse.

## Recommended Cleanup And Documentation

- Keep `cfgutil` as the preferred lawful non-`idevicerestore` backend.
- Document MobileDevice/AuthInstall symbols as private inventory only.
- Keep internal/factory tools out of execution paths.
- Keep dry-run signing status explicit: not locally verified.
- Require explicit wipe consent for any execution.
