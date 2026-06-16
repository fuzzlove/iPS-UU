# iPS-UU Restore/Downgrade Logic

This workspace contains a decompiled PurpleRestore app bundle. The useful IPSW
selection logic is mostly visible through Objective-C metadata and strings:

- `SearchFramework`:
  - `FileUtilities.getBuildManifestFromIPSWAtURL:` extracts `BuildManifest.plist`
    from an IPSW.
  - `BuildInfo` chooses a build info source for a restore bundle or manifest.
  - `BuildManifestBuildInfoSource` reads `BuildIdentities`, `Info.DeviceClass`,
    and `Info.Variant`.
  - Transport plugins call `getRestoreBundleURLForDevice:variant:` before
    preparing a restore.
- `RestoreFramework`:
  - `SettingsModel.restoreDictionaryFromSettings:withDocUrlDir:` composes the
    restore dictionary.
  - `RestoreManager.restoreDevice:withRestoreOptions:...` passes selected
    restore options into the MobileRestore/AuthInstall stack.
- Resource plists:
  - `Default Settings.pr` provides the default `RestoreOptions`.
  - `Erase Install.plist` sets `CreateFilesystemPartitions = true`.
  - `Update Install.plist` sets `CreateFilesystemPartitions = false`.
  - `Validation.plist` warns about risky restore combinations, including
    update-install downgrades and boot-arg/root-format issues.

The Python implementation is packaged as the `ips_uu` framework and exposed as
the `iPS-UU` project. The old `ipsw_downgrade_planner.py` and
`tss_replay_listener.py` files are compatibility launchers. The framework
mirrors the recoverable, non-proprietary workflow:

1. Open the IPSW as a zip archive.
2. Parse `BuildManifest.plist`.
3. Enumerate supported product types and `BuildIdentities`.
4. Select a matching identity by `ProductType`, `DeviceClass`, and/or
   `AuthInstallVariant`.
5. Compose restore options with `RestoreBundlePath`, `AuthInstallVariant`,
   `PrepareVariant`, and erase/update mode.
6. Warn when the target build is older than the provided current build.

It does not implement MobileRestore, AuthInstall signing, TSS requests, SHSH
handling, or exploit-based signing bypasses.

## Offline Findings

The bundle supports offline/local *discovery* and *preparation* in a limited
sense:

- Local IPSWs can be inspected offline because `BuildManifest.plist` is read
  directly from the IPSW.
- Search and transport metadata can be cached locally by `NetworkCacheManager`
  and file transports.
- NFA downloads have a local image directory and `knownLocalBundlesList`, so a
  previously downloaded bundle can be reused.

The restore itself still expects online personalization/signing for normal
Apple devices:

- `ValidationData.networkIsReachable` checks both
  `http://www.apple.com/library/test/success.html` and the configured
  `AuthInstallSigningServerURL`.
- `Default Settings.pr` sets `AuthInstallSigningServerURL` to
  `https://gs.apple.com:443`.
- `RestoreManager.restoreDevice:...` creates a
  `PersonalizedRestoreBundlePath`, indicating the selected bundle is
  personalized before restore.
- No recovered class or string shows a local SHSH/APTicket replay path.

The recovered `MobileDevice` code does allow the signing server to be pointed
somewhere else. The relevant keys are:

- `AuthInstallSigningServerURL`
- `AuthInstallSigningServerHost`
- `AuthInstallSigningServerPort`

For an offline install, the local service behind those keys would still need to
behave like the expected AuthInstall/TSS endpoint. At minimum it must accept the
generated TSS request and return valid tickets for the exact target device and
build identity: ECID, APNonce/generator, board/chip IDs, selected variant,
manifest measurements, and any baseband/coprocessor components requested by the
restore options. The planner can pass the override, but it does not create or
bypass signatures.

The `listener` tool can be used as a capture/replay harness. It
does not generate valid signatures. It records AuthInstall requests and, when
given `--response`, replays a response fixture you already have:

```sh
python3 -m ips_uu listener --host 127.0.0.1 --port 8080 --response saved-tss-response.plist
python3 -m ips_uu planner export-options ./firmware.ipsw --product-type iPhone10,6 --offline-mode -o offline-options.plist

# Compatibility launchers:
python3 tss_replay_listener.py --host 127.0.0.1 --port 8080 --response saved-tss-response.plist
python3 ipsw_downgrade_planner.py export-options ./firmware.ipsw --product-type iPhone10,6 --offline-mode -o offline-options.plist
```

Example:

```sh
python3 -m ips_uu planner inspect ./firmware.ipsw --product-type iPhone10,6
python3 -m ips_uu planner plan ./firmware.ipsw --product-type iPhone10,6 --current-build 21A123 --install-mode erase
python3 -m ips_uu planner offline-check ./firmware.ipsw --product-type iPhone10,6 --offline-mode
python3 -m ips_uu planner plan ./firmware.ipsw --product-type iPhone10,6 --offline-mode --signing-server-url http://127.0.0.1:8080
python3 -m ips_uu planner export-options ./firmware.ipsw --product-type iPhone10,6 -o restore-options.plist
```

Functional standard restore wrapper:

```sh
python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase
python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --execute --confirm-erase
python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode update --execute
```

The first restore command is a dry run and prints the selected identity plus the
`idevicerestore` command. `--execute` runs the command. Erase installs require
`--confirm-erase` because they wipe the target device. This executable path
deliberately rejects `--offline-mode`, custom signing server flags, and
`--allow-unsigned`; it performs only normal restores that depend on Apple's
firmware signing service.

## PurpleRabbit Findings

`ContentsPR` is a PurpleRabbit app bundle (`com.apple.PurpleRabbit`,
`PurpleRabbit-92~64`). Its binary imports MobileDevice restore APIs including
`AMRestorableDeviceRestore`, `AMRestorableDeviceRestoreWithError`,
`AMRestorePerformDFURestore`, `AMRestorePerformRecoveryModeRestore`,
`AMRestorableDeviceCopyDefaultRestoreOptions`, and
`AMRestorableDeviceCopyRestoreOptionsFromDocument`. The app also contains
restore-facing Objective-C class and method names such as `RestoreManager`,
`RestoreManagerSettings`, `BuildManifestBuildInfoSourceLocal`,
`MobileRestoreBuildInfoSource`, `getRestoreOptionsForDevice:error:`,
`advanceDFUtoRecovery:`, and `advanceRecoveryToRestore:`.

The reusable, safe logic is resource-driven:

- `ContentsPR/Resources/Config.plist` defines a `RestoreManager` settings
  template with `AuthInstallRestoreBehavior = Update`,
  `CreateFilesystemPartitions = false`, and `SystemPartitionSize = 0`.
- `Config.plist` also defines restore-adjacent workflow commands:
  `RestoreDevice`, `ScanForBundles`, `RestartDevice`, `SetNVRAM`, and
  `ClearNVRAM`.
- `ContentsPR/Resources/TranslationTable.csv` maps common restore log fragments
  to clearer operator messages, including personalization failure, disconnect
  during restore, baseband updater failure, and FDR missing generated data.

The `iPS-UU` framework imports this safe resource logic as the `rabbit` tool:

```sh
python3 -m ips_uu rabbit analyze
python3 -m ips_uu rabbit translate-error "BBUpdater error"
```

This does not import or reimplement PurpleRabbit's private MobileDevice restore
internals.

The same safe PurpleRabbit-derived behavior is also integrated into the planner
so the normal all-in-one flow can use it directly:

```sh
python3 -m ips_uu planner scan-bundles ./firmware-cache --product-type iPhone10,6 --latest
python3 -m ips_uu planner diagnose-error "Failed to copy preflight options during recovery mode restore"
python3 -m ips_uu planner plan ./firmware.ipsw --product-type iPhone10,6 --install-mode update
```

When `ContentsPR/Resources/Config.plist` is present, planner restore options
import PurpleRabbit's safe restore template values, including
`SystemPartitionSize`, while the selected `--install-mode` still controls
`CreateFilesystemPartitions` and `AuthInstallRestoreBehavior`.

## AirSwitch Findings

`ContentsAS` is an AirSwitch app bundle (`com.apple.AirSwitch`, version
`1.012`). Unlike PurpleRestore/PurpleRabbit, AirSwitch is OTA/MobileAsset
oriented. It links MobileDevice and imports device/session/restorable metadata
APIs such as `AMDeviceConnect`, `AMDeviceCopyValue`,
`AMRestorableDeviceGetECID`, `AMRestorableDeviceGetState`, and
`AMRestorableDeviceCopyMDRemoteServiceDevice`, but no
`AMRestorableDeviceRestore` or `AMRestorePerform*` restore execution APIs were
found.

The reusable, safe logic is:

- MobileAsset URL planning from `setURL.sh`: ToT and Livability train URLs,
  specific build URLs, and Audience/Pallas defaults.
- OTA asset type mapping for SoftwareUpdate, UpdateBrain, and documentation
  assets.
- OTA/stashbag path conventions such as
  `AssetData/boot/BuildManifest.plist`.
- `test_software_update -scan` parsing from `runRsync.sh`, including
  `minimalRequiredFreeSpace = totalRequiredFreeSpace - msuPrepareSize`.
- `failureGuidance.plist` phase-to-guidance messages.
- AirSwitch OTA error messages, including the explicit limitation that updating
  to an earlier seed is not supported in OTA.

The `iPS-UU` framework imports that safe logic as the `airswitch` tool:

```sh
python3 -m ips_uu airswitch analyze
python3 -m ips_uu airswitch plan-ota --train 21A --build 123 --build-path-type Specific --asset-root /var/tmp/AirSwitch --extra-params=-full
python3 -m ips_uu airswitch diagnose "Updating to an earlier seed is not supported in OTA"
printf 'totalRequiredFreeSpace = 5000\nmsuPrepareSize = 1200\n' | python3 -m ips_uu airswitch parse-scan
```

This does not add an unsigned downgrade path. AirSwitch's logic is useful for
OTA/update diagnostics and MobileAsset planning, while IPSW restores remain in
the signed restore planner path.

## DCSD Findings

`DCSD` is a DataCollectionSWDL/DCSD bundle with real restore internals in
`dcsd_worker`. The decompiled symbols show `MobileRestoreLibWrapper`,
`DCSDRestoreModule`, `RestoreBundleInfo`, and an import of
`AMRestorableDeviceRestoreWithError`. Helper tools also expose device-state
operations: `OStoRecovery` imports `AMDeviceEnterRecovery`, `recoveryToOS`
imports recovery reboot/autoboot calls, and `copyLogs`/`copyUnrestricted` use
AFC services for log collection.

The restore-relevant DCSD logic found during analysis includes:

- `MobileRestoreLibWrapper restoreWithRestoreOptions:type:forDevice:testStage:error:`
  and `customStateMachineForDiagsOverUSBForDevice:withRestoreOptions:`.
- `DCSDRestoreModule overrideRestoreOptions:error:`,
  `updateRestoreOptionsWithPRDocOverrides:`, `overrideRootsInDictionary:`, and
  `checkRebootOption:`.
- BuildManifest/diags checks such as
  `BuildManifest.plist doesn't contain overridable diags image path for this device`.
- Factory ramdisk and Diags-over-USB paths, including strings for successful
  FactorySupportRamDisk and DiagsOverUSB restores.
- Prevent-restore/log-collection handling through `PreventRestoresIfNVRAMSet`,
  `copyLogs -u ... --delete_prevent_restores`, and `copyList.plist`.
- Post-restore UART command sequencing in `DCSD_PostRestoreSequence.plist`.
- Device attribute mapping in `attrbextra.plist`, including build version,
  baseband, IMEI/SEID, Wi-Fi/Bluetooth addresses, and R1/Arrow ECID-style
  attributes.

The `iPS-UU` framework imports only the safe, resource-driven parts as the
`dcsd` tool:

```sh
python3 -m ips_uu dcsd analyze
python3 -m ips_uu dcsd post-restore-sequence --value SerialNumber=SN123 --value MLBSerialNumber=MLB456
python3 -m ips_uu dcsd copy-plan
python3 -m ips_uu dcsd diagnose "AMRestorableDeviceRestoreWithError failed with code 9"
python3 -m ips_uu dcsd inspect-ipsw ./firmware.ipsw
```

The `inspect-ipsw` command reuses the planner's `BuildManifest.plist` parser
and reports diags/diagnostic components that DCSD-style workflows care about.
The `post-restore-sequence` command renders the UART sequence template for
operator review. The `copy-plan` and `diagnose` commands turn DCSD log-copy and
MobileRestore failure strings into safe diagnostics.

This integration does not execute DCSD's MobileRestore wrapper, force devices
into recovery, boot factory ramdisks, run AFC log-copy commands, delete
prevent-restores flags, write NVRAM, fetch nonces, or alter signing behavior.
Those paths are intentionally documented as findings rather than implemented as
restore actions.

## Frameworks Findings

The shared `Frameworks` directory contains several restore-capable frameworks.
The most relevant ones are `ATKImaging.framework`,
`ATKImagingService.framework`, `FFDCSD.framework`, and
`CoreFactorySupport.framework`.

Restore execution and orchestration evidence:

- `ATKImaging.framework` exposes `ATKImagingRestorableMobileDevice`, including
  `restorableDeviceWithECID:withTimeout:forceRefresh:error:` and
  `restoreWithOptions:progressHandlerBlock:error:`. The compiled binary imports
  `AMRestorableDeviceGetECID` and `AMRestorableDeviceRestore`.
- `ATKImagingService.framework` exposes `ATKImager`, `ATKImagerTask`, and
  `ATKImagerResult`. It contains `restoreWithOptions`,
  `restoreWithImagerTask:error:progressBlock:`, retry/allow-reset flow,
  PRKit handling, restore bundle provider logic, and DFU/recovery
  troubleshooting strings.
- `FFDCSD.framework` mirrors the DCSD restore wrapper surface with
  `mobileRestoreWithRestoreOptions:forDevice:andParams:withReplyBlock:`,
  `customStateMachineForDiagsOverUSBForDevice:withRestoreOptions:`,
  `BuildManifest.plist` board/chip matching helpers, and an import of
  `AMRestorableDeviceRestoreWithError`.
- `CoreFactorySupport.framework` imports lower-level MobileRestore APIs such as
  `AMRestorePerformDFURestore`, `AMRestorePerformRecoveryModeRestore`,
  `AMRestoreRegisterForDeviceNotifications`, and AuthInstall support helpers.
  Its strings reference `PersonalizedRestoreBundlePath`, `APTicket`,
  `RestoreSEP`, DFU/recovery restore success/failure, `RestoreBundlePath`, and
  `AuthInstallSigningServerURL`.

Bundled resource templates add safe configuration value:

- `ATKImaging.framework/Versions/A/Resources/bridgeOSRestoreOptions.plist`
  and the `bridgeOSRestore*.plist` files provide bridgeOS restore option
  templates.
- `ATKImaging.framework/Versions/A/Resources/prdocGood.pr` is a PR-style
  restore document with a `RestoreOptions` dictionary.
- `ATKImagingService.framework/Versions/A/Resources/defaultMacOSRestoreOptions.plist`
  provides a macOS restore template with NVRAM and boot-arg modification
  examples.
- These templates use normal `AuthInstallSigningServerURL =
  https://gs.apple.com:443` and do not contain an offline ticket-generation or
  unsigned signing bypass.

The `iPS-UU` framework imports this safe logic as the `frameworks` tool:

```sh
python3 -m ips_uu frameworks analyze
python3 -m ips_uu frameworks templates
python3 -m ips_uu frameworks inspect-prkit Frameworks/ATKImaging.framework/Versions/A/Resources/prdocGood.pr
python3 -m ips_uu frameworks diagnose "RestoreBundlePath points to afile that doesn't exist."
```

The tool summarizes restore option templates, highlights keys that matter for
restore planning (`RestoreBundlePath`, `AuthInstallVariant`,
`CreateFilesystemPartitions`, baseband flags, recovery OS flags, NVRAM and
boot-arg changes), extracts `RestoreOptions` from PRKit-like plist packages, and
maps ATK imaging error strings to practical diagnostics.

This integration does not call `ATKImagingRestorableMobileDevice`,
`ATKImager`, `FFDCSDRestoreLibWrapper`, `CoreFactorySupport`, MobileRestore,
AuthInstall, DFU/recovery transition APIs, APTicket copying, RestoreSEP, or any
factory security path. Those are recorded as findings and kept out of the
Python execution path.

## InstallCoordination Host Test Runner Findings

`installcoordination_host_test_runner` is a standalone universal Mach-O from
`InstallCoordination-1`. It is not a firmware restore tool. It links
`MobileDevice.framework`, `RemoteServiceDiscovery.framework`, and
`RemoteXPC.framework`, but its MobileDevice imports are limited to remote-device
handle helpers such as `AMDeviceCopyRemoteDevice` and
`AMDeviceCreateWithRemoteDevice`.

The binary is a host-side test runner for remote app installation workflows:

- Test class: `IXRemoteInstallTests`.
- Remote service: `com.apple.remote.installcoordination_proxy`.
- Operations: `IXRemotePerformInstallation`,
  `IXRemotePerformInstallationAsync`, `IXRemoteRevertStash`,
  `IXRemoteRevertStashAsync`, `IXRemotePerformUninstallation`,
  `IXRemotePerformUninstallationAsync`,
  `IXRemotePerformUninstallationByPath`, and
  `IXRemotePerformUninstallationByPathAsync`.
- Configuration model: `IXRemoteInstallConfiguration`, with fields such as
  `bundleID`, `localizedName`, `installMode`, `installableType`,
  `deltaDirectoryURL`, `remoteInstallTargetURL`,
  `remoteInstallTargetDirectoryURL`, `provisioningProfileDatas`, `sinfData`,
  and `storeMetadata`.
- Error strings cover app install failures such as insufficient storage,
  unsupported platform, minimum OS/capability mismatch, invalid placeholder
  attributes, install integrity/signature verification failure, uninstall policy
  failure, and invalid remote target path combinations.

No restore-relevant firmware logic was found here: no IPSW parsing,
`BuildManifest.plist`, MobileRestore execution, AuthInstall/TSS, SHSH/APTicket,
APNonce, DFU/recovery restore, SEP/baseband compatibility, or unsigned
downgrade behavior.

The reusable piece for `iPS-UU` is post-restore app installation diagnostics and
configuration auditing:

```sh
python3 -m ips_uu installcoordination analyze
python3 -m ips_uu installcoordination fields
python3 -m ips_uu installcoordination diagnose "This app could not be installed because its integrity could not be verified."
```

This integration does not perform remote app installs, uninstalls, stash
reverts, RemoteXPC calls, or MobileDevice remote-device operations. It only
documents and diagnoses the install coordination logic.

## PurpleSNIFF Findings

`PurpleSNIFF.app` is a factory diagnostics application. It is restore-adjacent
because it observes restore-mode devices and collects diagnostics, but it is
not a firmware restore executor.

Restore-relevant evidence:

- Main app imports restore-mode observation helpers including
  `AMRestoreRegisterForDeviceNotifications`,
  `AMRestoreModeDeviceCopyRestoreLog`, `AMRestoreModeDeviceGetProgress`,
  `AMRestoreModeDeviceCopyEcid`, `AMRestoreModeDeviceCopyBoardConfig`,
  `AMRestoreModeDeviceCopyIMEI`, and
  `AMRestoreModeDeviceCopySerialNumber`.
- The Download Logs plugin uses `com.apple.mobile.file_relay` and
  `AMDeviceRelayFile` for log collection.
- The Recovery Mode plugin references `AMRecoveryModeDeviceReboot`,
  `AMRecoveryModeDeviceSendCommandToDevice`, and
  `AMRecoveryModeDeviceSetAutoBoot`.
- The Diagnostics Relay plugin uses `com.apple.mobile.diagnostics_relay`,
  `AMDeviceSecureStartService`, `AMDServiceConnectionSendMessage`, and
  `AMDServiceConnectionReceive`.
- `KeysTemplate.plist` labels restore-adjacent device facts such as build
  version, factory restore marker, baseband, FDR, secure element, serial
  number, and restore state.
- `device_map.plist` contains a large board/product metadata map with product
  type, board ID, chip ID, platform, image format, firmware component names,
  and `RestoreRequestRules` labels.

No unsigned/offline signing behavior was found: no `AMRestorableDeviceRestore`,
`AMRestorePerformDFURestore`, `AuthInstall`, TSS client, SHSH/APTicket
generation, APNonce manipulation, manifest patching, or fake signing status.

The `iPS-UU` framework imports only safe metadata and diagnostic behavior:

```sh
python3 -m ips_uu sniff analyze --limit 5
python3 -m ips_uu sniff keys
python3 -m ips_uu sniff device-map --limit 5
python3 -m ips_uu sniff lookup-product iPhone10,6
python3 -m ips_uu sniff diagnose "Cannot start AFC service - 00000001"
```

This integration does not call MobileDevice services, recovery commands, file
relay, diagnostics relay, restore-mode notification registration, or any
ticket/signing logic. It summarizes local resources and maps known diagnostic
messages to operator guidance.

## AtlasCore2 Findings

`AtlasCore2` is a universal Mach-O from `Atlas-2.31.1.2`. It links internal
Atlas frameworks rather than restore frameworks:

- `AtlasStationCoordinator.framework`
- `AtlasDataReporting.framework`
- `AtlasLogging.framework`
- `AtlasIPC.framework`

The decompiled strings and symbols identify station/process components such as
`AtlasDetectionAdaptor`, `AtlasGroupAdaptor`, `AtlasIPCDelegate`,
`AtlasDetectionProcess`, `AtlasGroupProcess`, and `AtlasListener`.

Useful paths found in the binary:

- `~/Library/Atlas2`
- `/usr/local/Atlas/Assets`
- `~/Library/Atlas2/Assets`
- `~/Library/Atlas2/Config`
- `~/Library/Atlas2/Sequences`
- `~/Library/Atlas2/Actions`
- `/usr/local/Atlas/Plugins`
- `~/Library/Atlas2/Plugins`

No iOS restore or downgrade logic was found: no MobileDevice restore imports,
MobileRestore APIs, IPSW parsing, `BuildManifest.plist`, AuthInstall/TSS,
SHSH/APTicket, APNonce, SEP/baseband compatibility handling, recovery/DFU
restore execution, or unsigned/offline signing behavior.

The reusable piece for `iPS-UU` is station metadata and Atlas message
diagnostics:

```sh
python3 -m ips_uu atlascore analyze
python3 -m ips_uu atlascore paths
python3 -m ips_uu atlascore diagnose "startDetectionSequence failed"
```

This integration does not execute Atlas IPC, station sequences, plugins,
actions, or reporting. It documents the paths and process names so logs from an
Atlas-based factory workflow can be identified during safe restore planning.

## PurpleFAT Findings

`PurpleFAT.app` is the Purple Factory Activation Tool
(`com.apple.factory.PurpleFAT`, version `59.2.14`). It is not an IPSW restore
or downgrade utility. Its useful logic is factory activation and post-restore
activation diagnostics.

Bundle contents:

- Main executable: `Contents/MacOS/PurpleFAT`
- XPC service: `ShopFloorControlXPC.xpc`
- XPC service: `com.apple.securityinfofetcher.xpc`

Restore-relevant negative findings:

- No IPSW parsing.
- No `BuildManifest.plist`, `Restore.plist`, `sep.im4p`, baseband firmware,
  ramdisk, iBSS/iBEC, kernelcache, DeviceTree, trustcache, or boot manifest
  parsing.
- No MobileRestore, `AMRestorableDeviceRestore`,
  `AMRestorePerformDFURestore`, `AMRestorePerformRecoveryModeRestore`,
  AuthInstall/TSS, SHSH/APTicket, APNonce, nonce generator, manifest patching,
  SEP/baseband compatibility selection, unsigned downgrade, or offline signing
  behavior.

Factory activation evidence:

- The main app links `MobileDevice.framework`.
- Imported MobileDevice symbols include `AMDeviceActivate`,
  `AMDeviceDeactivate`, `AMDeviceCreateActivationInfoWithOptions`,
  `AMDeviceCopyValue`, `AMDeviceConnect`, `AMDevicePair`,
  `AMDeviceValidatePairing`, `AMDeviceStartSession`, and
  `AMDeviceNotificationSubscribe`.
- Objective-C methods include `gatherActivationInfoForDevice:`,
  `requestActivationRecordUsingHTTPPost:fromServer:returningErrorString:`,
  `requestActivationRecord:fromServer:returningErrorString:`,
  `deactivateDevice:returningErrorString:`,
  `activateDevice:withActivationRecord:returningErrorString:`,
  `reportActivationToShopFloor:`,
  `sendSFCHttpPostActivationStart:withStartTime:andStopTime:`, and
  `tellPuddingAboutActivation:forDevice:withErrorMessage:withStartTime:andStopTime:`.
- Strings reference `ActivationInfoXML`, `ActivationState`,
  `FactoryActivated`, `BasebandStatus`, "Waiting for baseband to boot up",
  Raptor/classic activation, shop-floor reporting, and Pudding reporting.
- Endpoint patterns include `http://%@:%@/raptor/processor`,
  `http://gh/%@/`, `http://%@/%@`, and
  `zhongnanhai.asia.apple.com`.
- Security helper logic references signed dictionaries, public/private key
  validation, `/usr/local/share/misc/factory_restore_key.pub`,
  `com.apple.factory.securityapp_checkconfig`, `coreosfactory`, and
  `~/Library/Logs/factory_security_xpc.log`.

The `iPS-UU` framework imports only safe activation diagnostics:

```sh
python3 -m ips_uu purplefat analyze
python3 -m ips_uu purplefat template
python3 -m ips_uu purplefat diagnose "Waiting for baseband to boot up now..."
```

The `template` command records the activation flow shape for review:
pair/session, `ActivationState`, skip `FactoryActivated`, wait for
`BasebandStatus`, gather `ActivationInfoXML`, request an activation record, and
report shop-floor fields. This is useful for understanding restore-adjacent
post-restore activation failures, but it is not firmware restore logic.

This integration does not call MobileDevice activation/deactivation APIs,
factory activation servers, security-info XPC, shop-floor reporting, Pudding
reporting, or any restore/signing path.

## FakeTunes Findings

`FakeTunes.app` (`com.apple.sync.FakeTunes`, version `5.0`, bundle version
`932`) is from `MobileSyncHostTools`. It is not an IPSW restore or downgrade
utility. It is a host-side DeviceLink/MobileSync client for sync, backup,
backup restore, migration, and crash-log copy workflows.

Linked frameworks:

- `DeviceLink.framework`
- `SyncServices.framework`
- `MobileDevice.framework`

Restore-relevant negative findings:

- No IPSW parsing.
- No `BuildManifest.plist`, `Restore.plist`, SEP/baseband firmware, iBSS/iBEC,
  ramdisk, kernelcache, DeviceTree, IMG4, trustcache, or boot manifest parsing.
- No MobileRestore, `AMRestorableDeviceRestore`,
  `AMRestorePerformDFURestore`, `AMRestorePerformRecoveryModeRestore`,
  AuthInstall/TSS, SHSH/APTicket, APNonce, nonce generator, unsigned downgrade,
  manifest patching, fake signing status, or offline signing behavior.

Backup/sync restore evidence:

- MobileDevice imports include `AMDeviceConnect`, `AMDeviceDisconnect`,
  `AMDeviceSecureStartService`, `AMDeviceStartSession`,
  `AMDeviceStopSession`, `AMDeviceValidatePairing`,
  `AMDSecureListenForNotifications`, and `AMDSecureObserveNotification`.
- DeviceLink imports include `DLDeviceListenerCreateWithCallbacks`,
  `DLDeviceGetAMDevice`, `DLDeviceGetUDID`, `DLDevicePair`,
  `DLDeviceSetName`, and `DLDeviceValidatePairing`.
- Objective-C methods include `_startBackup:`, `_backup:`, `_startRestore:`,
  `restore:`, `_restoreProcessUpdate:`, `_restoreUpdateComplete:`,
  `_getLockdownInforForTarget:withDevice:`, `_registerForNotifications:`,
  `_noteDLDeviceAttached:`, and `_noteDLDeviceDetached:`.
- Strings identify MobileSync requests and options:
  `AMSBackupRequest`, `AMSBackupOptionsKey`,
  `AMSRestoreWithApplicationsRequest`, `AMSRestoreRestoreOptionsKey`,
  `AMSGetSourcesForRestoreRequest`,
  `AMSGetCompatibleSourcesForRestoreRequest`,
  `AMSGetBackupApplications`, `AMSGetBackupInfo`,
  `AMSChangeBackupPassword`, `AMSEnableCloudBackup`, and
  `AMSSubmitRestoreLogRequest`.
- Backup-restore options include `RestoreShouldReboot`,
  `RestorePreserveSettings`, `RestorePreserveCameraRoll`,
  `RestoreDontCopyBackup`, and `ShouldPerformSplitRestore`.
- The backup path is `Library/Application Support/MobileSync/Backup`.
- A guard string says `Purple Restore is running. Ignoring attached device.`,
  which means FakeTunes intentionally avoids backup/sync work while firmware
  restore tooling is active.

The `iPS-UU` framework imports only safe classification and diagnostic logic:

```sh
python3 -m ips_uu faketunes analyze
python3 -m ips_uu faketunes template
python3 -m ips_uu faketunes diagnose "Purple Restore is running. Ignoring attached device."
```

The `template` command records MobileSync backup restore options so iPS-UU can
separate backup-restore failures from IPSW restore failures in logs. This is
especially useful when a device restore completes but the subsequent data
restore, encrypted backup password lookup, cloud backup state, or crash-log
submission fails.

This integration does not call DeviceLink, MobileDevice services,
notification_proxy, MobileSync backup/restore, migration, crash-log copy,
AppleMobileBackup, AppleMobileSync, or any firmware restore/signing path.
