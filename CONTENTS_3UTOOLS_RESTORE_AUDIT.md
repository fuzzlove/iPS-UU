# Contents 3uTools Restore/Flash Audit

Scope: `Contents` app bundle currently present in the repository. This audit is limited to legitimate restore workflow insights and does not implement unsigned restore, offline signing bypass, APNonce manipulation, SEP/baseband bypass, exploit chains, or private entitlement abuse.

## Identification

- Bundle: `Contents`
- App name: `3uTools`
- Bundle identifier: `com.3uTools.mac`
- Version: `9.0.027`
- Main executable: `Contents/MacOS/3uTools`
- Restore helper: `Contents/MacOS/iTunesFlash`
- Notable bundled restore libraries:
  - `Contents/Frameworks/libidevicerestore.dylib`
  - `Contents/Frameworks/libimobiledevice.dylib`
  - `Contents/Frameworks/libimobiledevice-glue.dylib`
  - `Contents/Frameworks/libirecovery.dylib`
  - `Contents/Frameworks/libusbmuxd.dylib`
  - `Contents/Frameworks/libplist2.dylib`
  - `Contents/Frameworks/libdownload.dylib`

Both `3uTools` and `iTunesFlash` are ad-hoc signed locally, with no TeamIdentifier reported by `codesign`.

## High-Level Findings

The bundle offers useful insight into how a polished restore application can be structured:

- A Qt-based front end with flash pages, firmware lists, progress logs, SHSH download/query UI, and restore-with-app flows.
- A bundled `libidevicerestore.dylib` rather than only shelling out to an external `idevicerestore` executable.
- A small `iTunesFlash` helper around Apple `MobileDevice.framework` private restore APIs.
- Dedicated restore progress, status, log, and callback surfaces.
- Local firmware cache and import flows.
- Separate modes for one-click flash, multiple flash, professional flash, DFU flash, activation, and backup/app restore.

I did not find evidence that this bundle can legitimately perform offline unsigned downgrades without Apple signing material. The strongest restore-library strings show the opposite: the restore flow queries TSS/SHSH, requires tickets, validates compatibility, and errors when SHSH/TSS/APTicket/RootTicket material is missing.

## Observed Restore Methods

| Method | Online/offline | Source | Safe iPS-UU status |
| --- | --- | --- | --- |
| Bundled `libidevicerestore` normal restore | Online | `Contents/Frameworks/libidevicerestore.dylib` | Candidate for future signed-only binding, not currently executed |
| Signed firmware discovery | Online | `libidevicerestore` strings/API, `api.ipsw.me` | Inventory only |
| Custom IPSW import and validation | Offline preflight only | `BuildManifest.plist`, `Restore.plist`, 3uTools firmware import UI | Implemented safely as dry-run metadata parsing |
| SHSH query/download UI | Online or cached artifact management | 3uTools SHSH dialogs/tasks | Inventory only; not used for restore execution |
| DFU/recovery restore flow | Online for modern signed restores | 3uTools DFU tasks and libidevicerestore DFU/recovery clients | Inventory only; delegated to supported restore tools |
| `iTunesFlash` private MobileDevice restore | Online or system-managed by private API | `Contents/MacOS/iTunesFlash` | Blocked; private API |
| Super restore / iTunes backup and app restore | Mostly offline data restore | MobileBackup2, AFC, installation_proxy, app restore tasks | Out of firmware-restore scope |

The same method catalog is available through:

```sh
python3 -m ips_uu restore-research methods
```

## Evidence: Bundled libidevicerestore

`Contents/Frameworks/libidevicerestore.dylib` exports a broad callable restore API:

- `_idevicerestore_client_new`
- `_idevicerestore_set_ipsw`
- `_idevicerestore_set_ecid`
- `_idevicerestore_set_udid`
- `_idevicerestore_set_flags`
- `_idevicerestore_set_cache_path`
- `_idevicerestore_set_progress_callback`
- `_idevicerestore_set_step_callback`
- `_idevicerestore_set_log_callback`
- `_idevicerestore_set_error_callback`
- `_idevicerestore_start`
- `_ipsw_extract_build_manifest`
- `_ipsw_extract_restore_plist`
- `_ipsw_get_signed_firmwares`
- `_ipsw_download_latest_fw`
- `_restore_device`
- `_restore_handle_progress_msg`
- `_restore_handle_status_msg`
- `_restore_send_root_ticket`
- `_restore_send_recovery_os_root_ticket`
- `_restore_send_baseband_data`
- `_restore_send_firmware_updater_data`
- `_tss_request_send`
- `_tss_response_get_ap_ticket`
- `_tss_response_get_ap_img4_ticket`
- `_tss_response_get_baseband_ticket`

Useful signed-only insight:

- A GUI can call libidevicerestore as a library and wire progress/log/error callbacks directly, instead of depending only on CLI stdout.
- The library can list signed firmware through `api.ipsw.me` and then still use Apple TSS for actual personalization.
- The library has explicit APIs for IPSW manifest parsing and Restore.plist extraction.
- The library handles normal, recovery, DFU, and restore-mode transitions.

Risk note:

- Directly binding this bundled library is less stable than calling a known installed `idevicerestore` binary. It should be treated as an optional research backend only after version checks and strict signed-only guardrails.

## Evidence: Signing And Ticket Requirements

Relevant strings from `libidevicerestore.dylib`:

- `ERROR: Unable to get SHSH blobs for this device`
- `ERROR: can't continue without TSS`
- `ERROR: Cannot get SHSH from the server`
- `ERROR: Unable to proceed without a TSS record.`
- `ERROR: Unable to send APTicket`
- `ERROR: Invalid SHSH.`
- `ERROR: ApTicket requested but no TSS present`
- `ERROR: Cannot send RootTicket without TSS`
- `ERROR: Unable to get ticket from TSS`
- `ERROR: Could not fetch list of signed firmwares.`
- `ERROR: No firmwares are currently being signed for %s`
- `https://gs.apple.com/TSS/controller?action=2`
- `https://api.ipsw.me/v4/device/%s`

Conclusion:

These strings do not support an offline unsigned restore conclusion. They show a normal libidevicerestore-style flow that requires TSS/APTicket/SHSH responses for modern devices and fails closed when those are missing or invalid.

## Evidence: Compatibility And Component Handling

Relevant strings/symbols:

- `_build_manifest_check_compatibility`
- `_build_manifest_get_build_identity_for_model`
- `Checking firmware is compatible`
- `ERROR: This firmware is not compatible for the iDevice`
- `Extracting Restore.plist from IPSW`
- `Extracting BuildManifest from IPSW`
- `RestoreSEP`
- `BasebandFirmware`
- `FirmwareUpdaterData`
- `SE,Ticket`
- `Rap,Ticket`
- `BMU,Ticket`
- `Baobab,Ticket`
- `Cryptex1,Ticket`

Signed-only insight:

- Our Python dry-run should keep reporting `BuildManifest.plist`, `Restore.plist`, selected identity, SEP/baseband presence, and compatibility before execution.
- Firmware updater requests for SE/Rose/Yonkers/TCON/BMU/Baseband all appear ticketed and should remain delegated to a supported restore backend.

## Evidence: 3uTools Main UI/Task Model

The main binary contains many Qt symbols for the flash workflow:

- `QPageFlash`
- `QViewToolOneFlash`
- `QViewToolMultipleFlash`
- `QViewToolProfessionalFlash`
- `QWidgetFlashing`
- `QWidgetFlashingLog`
- `QListFirmwareSelect`
- `QDialogDownloadShsh`
- `QDownloadShshModel`
- `task_dfu_flash`
- `task_dfu_firmware_import`
- `task_mflash_read`
- `task_mflash_flash`
- `task_toolbox_itunes_flash`
- `QTaskSuperRestore`
- `QTaskResotreItunes`
- `CItunesRestoreWithAppDialog`

Safe product insight:

- A restore GUI benefits from distinct modes: one-device flash, multi-device flash, professional/advanced flash, recovery/DFU helpers, logs, and firmware cache/import.
- A safe iPS-UU implementation should keep destructive execution behind CLI guardrails but can mirror the UI separation and progress model.

## Evidence: iTunesFlash Helper

`Contents/MacOS/iTunesFlash` contains strings for private Apple MobileDevice restore APIs:

- `/System/Library/PrivateFrameworks/MobileDevice.framework/Versions/A/MobileDevice`
- `AMRestorableDeviceRegisterForNotifications`
- `AMRestorableDeviceRestore`
- `AMRestoreCreateDefaultOptions`
- `AMRestorableDeviceGetECID`
- `AMRestoreEnableFileLogging`
- `AMDSetLogLevel`
- `RestoreBundlePath`
- `AuthInstallRestoreBehavior`
- `Update`
- `Erase`
- `start flash`

Risk note:

- This is a private MobileDevice.framework path. iPS-UU should not call or wrap it as an executor.
- It is useful as evidence that Apple private restore APIs can be used by some tools, but it is not a maintainable public backend.

## Evidence: Backup/App Restore And Device Management

`libidm.1.0.0.dylib` and the main binary contain many normal device-management and backup/app restore surfaces:

- `com.apple.mobilebackup2`
- `mobilebackup_request_restore`
- `com.apple.mobile.installation_proxy`
- `com.apple.afc`
- `com.apple.misagent`
- `com.apple.mobileactivationd`
- `RestoreApplications.plist`
- `CItunesRestoreWithAppDialog`
- `CSuperRestoreWithAppDialog`

Insight:

- 3uTools separates firmware restore from backup/app/content restore.
- iPS-UU should keep firmware restore research separate from MobileBackup/app restore workflows to avoid confusing firmware flashing with data restore.

## Safe Improvements For iPS-UU

Useful ideas to adopt:

- Add optional detection of bundled `libidevicerestore.dylib` as an inventory-only backend candidate.
- Keep using callbacks/progress/log concepts in the GUI.
- Keep reporting `Restore.plist` and `BuildManifest.plist` metadata.
- Add cache path visibility for IPSW and filesystem extraction if execution support is expanded.
- Keep SHSH/TSS status explicit and conservative: not locally proven unless Apple signing validation occurs.
- Keep private MobileDevice restore helper behavior documented but not executed.

Not safe to adopt:

- Private `AMRestorableDeviceRestore` execution through `iTunesFlash`.
- Any behavior that continues after missing TSS, invalid SHSH, missing APTicket, SEP/baseband ticket failure, or compatibility failure.
- Any offline signing-server override or forged/replayed ticket logic.

## Conclusion

The new `Contents` bundle gives strong implementation insight into a professional restore tool architecture. It does not provide evidence of a legitimate offline unsigned downgrade method. Its bundled restore engine still depends on Apple TSS/signing material and ticket validation, and its private `iTunesFlash` helper is not a safe or stable public backend for iPS-UU.
