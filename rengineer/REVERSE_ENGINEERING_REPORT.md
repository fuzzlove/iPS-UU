# Purple Restore Static Reverse-Engineering Report

Scope: static analysis of the files in this directory, including `Purple Restore 4 (Beta)` and the newly added `PurpleRestore Classic.app`. These are extracted macOS app payloads, not source. I used local Mach-O metadata, exported/imported strings, plist metadata, and readable bundled scripts. No device was attached and no restore action was executed. The analysis stays at interoperability and restore engineering level; it does not implement activation bypasses, signing bypasses, SEP/baseband rollback bypasses, or unauthorized service access.

## Payload identity

- Main app binary: `MacOS/Purple Restore 4 (Beta)`
- CLI helper: `MacOS/restore-cmd-tool`
- Frameworks: `AppStatFramework`, `DevicesFramework`, `DownloadManagerFramework`, `LoggerFramework`, `RestoreFramework`, `RestoreSDProtocol`, `SearchFramework`, `SoftwareBundleKit`
- Plugin: `KnoxPlugin.framework`
- Build provenance from version plists: `PurpleToolbox_iosmac`, build `499`, source version `912000003000000`
- SDK/build metadata: macOS 13 internal SDK (`macosx13.0.internal`), Xcode `15A6160m`, minimum macOS `12.0`

Important extraction issue: both `Purple Restore 4 (Beta)` and `restore-cmd-tool` link `@rpath/RestoreIntegrationLayer.framework`, but that framework is not present in this folder. Running `restore-cmd-tool --help` aborts at dynamic-loader time for that missing dependency, so CLI help was recovered from embedded strings.

## Architecture

The app is Apple's internal Purple Restore 4 tooling. It is not an open-source `idevicerestore` bundle. The restore stack is built around Apple's private/internal restore frameworks and services:

- `RestoreFramework` links `MobileDevice.framework`, `DiskImages`, `DevicesFramework`, `SearchFramework`, `DownloadManagerFramework`, and `RestoreSDProtocol`.
- `DevicesFramework` links `MobileDevice.framework`, `DiskImages`, IOKit, Security, SystemConfiguration, and DiskArbitration.
- `DownloadManagerFramework` links `RestoreSDProtocol`, CacheDelete, DiskImages2, and weak `AppleConnectClient`.
- `RestoreSDProtocol` and `KnoxPlugin` include AppleConnect/SSO/Knox paths.
- `SoftwareBundleKit` and `SearchFramework` provide build search, metadata parsing, OTA bundle discovery, livability status, and local bundle indexing.

Conclusion: this payload does not expose a general downgrade bypass. It wraps normal/internal Apple restore services, Apple-internal build discovery, and private MobileDevice/AuthInstall behavior. For an Apple-internal IPS-UU deployment, those private frameworks are worth treating as optional first-class backends, assuming the host has the missing private frameworks, AppleConnect/SSO, service access, and required device authorization.

## Deployment assumptions

There are two different integration profiles:

- Public/offline profile: use local IPSW/restore-bundle metadata, public restore tools, and non-executing research checks. This avoids private framework execution.
- Apple-internal profile: private frameworks and services can be used directly, including `RestoreFramework`, `RestoreIntegrationLayer`, `MobileDevice.framework`, `RestoreSDProtocol`, `SoftwareBundleKit`, `DownloadManagerFramework`, `KnoxPlugin`, AppleConnect/SSO, Knox/NFA, and the OTA helper scripts.

The rest of this report assumes the Apple-internal profile is allowed, but still separates implementation candidates by dependency and risk.

## `restore-cmd-tool` command surface

Recovered options and help strings identify this intended CLI model:

- Device selection: `--locationID` or `--ecid`
- Restore document: path to a `.pr` restore document, with options overriding the document
- Local bundle: `--bundle`
- Personalization variant: `--variant`, defaulting to `Factory - DFU`
- Logs: restore log path, default `/tmp`
- Mode flags: `--restore`, `--ota`, `--forceKnox`
- Apple-internal auth/download: Westgate token from netrc, DAW token for Knox
- Unsupported beta flags: baseband firmware path, device map plist, FDR URLs, syscfg submission, two-stage iBoot, serial device helpers
- Generic overrides: repeated key/value restore option overrides, with typed suffixes like `:bool`, `:number`, `:url`, or `:json`

Useful embedded examples:

- `--override "AuthInstallVariant=Internal Install"`
- `--override BundleOverrides.ImageFile=~/Desktop/OS.dmg`
- `--override UpdateBaseband:bool=1`
- iBoot data override form: `iBootData=...`

The CLI waits for a selected device, extracts build and silo information, chooses a build, prepares a restore document, downloads from Knox/NFA when requested, and waits for restore completion. It reports unlock requests, restore aborts, timeout, progress state changes, and download state changes.

IPS-UU value: model typed restore-option overrides and restore-document import/export. In Apple-internal mode, `restore-cmd-tool` is a strong candidate for an executor backend once `RestoreIntegrationLayer.framework` and the Apple-internal runtime environment are present.

## Main app behavior

The GUI contains controllers and models for:

- Restore settings import/export and non-default filtering
- Restore parameter fetching and validation
- Local build selection
- Network build search
- Train/build browsing
- Restore progress steps
- Device sidebar/details
- Livability details
- OTA restore and tethered restore paths
- Restore abort and sysdiagnose collection

Notable app strings/classes:

- `RestoreSettingsModel`, `RestoreSettingsViewController`
- `RestoreParamsDataManager`, `RestoreParamsViewController`
- `RestoreProgressModel`, `RestoreProgressViewController`
- `DeviceRestoreProgressData`, `RestoreStepData`
- `LocalFilesDataModel`, `SearchResultsBuild`, `BuildSelectionData`
- `ValidationPredicate`
- `NetworkBuildParamsSelectionManager`
- `startOTARestore`, `startTetheredRestore`, `startDownloadingForRestore`
- `isDowngradeVersion`, `buildIsDOA`, `buildLivabilityState`, `isBundleNFADownloadable`

IPS-UU value: add a first-class restore session model with step progress, warnings, logs, selected build metadata, selected restore options, and validation state. In Apple-internal mode, this model should be able to consume Purple Restore callbacks or CLI status plist/log outputs directly.

## Restore framework findings

`RestoreFramework` appears to be the central MobileDevice restore orchestrator. It exposes strings/selectors for:

- Preparing devices for restore
- Preparing callbacks and restore context
- Creating customized restore settings
- Passing device passcode data when required
- Kicking off restore by device, board config, bundle path, and restore options
- Downloading/personalizing restore bundles
- Setting restore boot args and restore NVRAM metadata
- Handling passcode/unlock UI
- Canceling restore
- Capturing host, serial, device, status plist, and restore logs
- Taking snapshots for devices
- Reporting phase progress

Recovered restore step names:

- `Personalizing restore bundle`
- `Downloading restore bundle`
- `Entering recovery mode`
- `Showing Unlock Device UI`
- `Hiding Unlock Device UI`
- `DFU download`
- `Setting restore boot-args`
- `Verifying restore`
- `Updating baseband`
- `Preparing for baseband update`
- `Booting the baseband`
- `Executing iBEC to bootstrap update`
- `Finalizing NAND epoch update`
- `Creating factory restore marker`
- `Sending Apple logo to device`
- `Flashing SYSCFG`
- `Checking for uncollected logs`
- `Creating Recovery OS Partition/Container/Volume`
- `Installing recovery OS files`
- `Installing recovery OS image`

Recovered hard-stop/error semantics:

- `Cannot restore older epoch without using DFU`
- `The baseband cannot be rolled back`
- `Unpersonalized baseband firmware rejected`
- `AuthInstall error`
- `TATSU declined to authorize this request`
- `Device is not in restore mode`
- `The device has uncollected factory logs`
- `Restore aborted`

IPS-UU value: these are good user-facing preflight and post-failure categories. IPS-UU should surface downgrade/baseband/signing risk before execution, then delegate final authorization to the internal restore backend.

## Build search and metadata

`SearchFramework` and `SoftwareBundleKit` provide most of the metadata logic:

- Local IPSW/DMG/restore-bundle indexing
- `BuildManifest.plist` and `Restore.plist` extraction/parsing
- `RestoreBundles` metadata parsing
- Supported device and variant extraction from BuildManifest identities
- `Info.DeviceClass`, `DeviceClass`, and `Variant` handling
- Recent restores persistence
- DMG mounting/unmounting for metadata extraction
- Knox/NFA pointers and access-denied handling
- Livability lookups

Internal/public-ish endpoints and services observed:

- `https://purplerestore.apple.com/index/v5_all_builds.plist`
- `https://purplerestore.apple.com/index/builds_information.zip`
- `https://livability-api.swe.apple.com/services/journals?...`
- `https://knox.sd.apple.com`
- NFA endpoint strings such as `https://nfap-sf.corp.apple.com:443/api/v1/nfa/ws/download/document/details?bundleId=%@`

These services are Apple-internal or corp-gated. In Apple-internal mode, they are worth adding as authenticated build-source providers. Outside that environment, they should remain disabled.

IPS-UU value: strengthen local bundle inspection and add authenticated internal build discovery. The offline-safe part is parsing local IPSW/DMG/restore bundle metadata and presenting supported devices, variants, RestoreBundle paths, BuildManifest identity count, and missing metadata errors. The Apple-internal part is Knox/NFA/PurpleRestore index lookup and livability status.

## OTA/update workflow

Purple Restore 4 contains a host-assisted OTA path that is separate from tethered IPSW restore. It uses:

- OTA build search: `KnoxOTABuild`, `OTAXMLUpdateEntry`, `SearchOTABundleManager`
- Update types: `Patch (Incremental-Update)`, `OTA Patch (Incremental)`, `OTA Full Update`
- Brain asset logic: `UpdateBrainAsset_com_apple_MobileAsset_MobileSoftwareUpdate_UpdateBrain.xml`
- OTA options: `OtaOptions.nopersonalization`, `serverurl`, `usevariant`, `sso`, `ssoToken`, `passcode`, `setdisplay`, `reboot`, `snapshot`, `forceFullUpdatePatch`
- Device-side commands over USB TCP relay, SSH, and rsync

Readable scripts in `LoggerFramework` show the device-side mechanics:

- `tcprelay.py`: starts `/usr/local/bin/tcprelay --portoffset ... --locationid ... rsync telnet ssh`
- `runRsyncBrain.sh`: copies a brain asset to the device
- `freeSpaceAndRunRsyncBundleToDevice.sh`: purges software-update assets, estimates required free space with `test_software_update -scan`, frees space with `test_software_update -space ... -delete`, and rsyncs the update bundle
- `runPrepareUpdate.sh`: runs `test_software_update <bundle> -brain <brain> <flags>`
- `runPrepareUpdateBrain.sh`: unloads software update services, kills `softwareupdated`, and runs `test_software_update -brain <brain> -purge`
- `runPrepareUpdateForProdFused*.sh`: same flow with SSO user/token
- `setURL.sh`: rewrites MobileAsset defaults and asset-server URLs, including Basejumper/Livability/Pallas paths
- `stopRunPrepareUpdate.sh`: restores inactivity settings, unloads software update services, kills `softwareupdated`, and runs `test_software_update -suspend`
- `runSysDiagnose.sh`: runs device `sysdiagnose` and copies results back
- `checkLockState.sh`: runs `keystorectl get-lock-state`
- `checkDeviceIsUp.sh`: checks `runningboardd`

Limits: this flow assumes root SSH access, `tcprelay`, `test_software_update`, `asutil`, internal asset servers, and in some cases Apple SSO. It is not a normal consumer-device downgrade mechanism, but it is highly relevant for an Apple-internal OTA/recovery workflow.

IPS-UU value: implement an internal OTA workflow module. In non-internal mode it can degrade to a checklist/preflight report. Useful features include:

- Detect whether tools required for an OTA research flow exist
- Parse local OTA/brain asset metadata when present
- Explain missing prerequisites: root SSH, relay, internal asset URLs, SSO token, matching prerequisite build
- Add log collection hooks for sysdiagnose
- In Apple-internal mode, orchestrate relay open/close, rsync, brain asset preparation, MobileAsset URL setup, `test_software_update`, suspend/cleanup, and max-inactivity restore

## Tethered downgrade / tethered restore findings

There is real tethered-restore plumbing in this bundle, but this extraction is missing the framework that likely owns execution. The evidence is stronger than a UI label:

- `SoftwareBundleKit` exports `SoftwareBundleKit.TetheredBuild`.
- `TetheredBuild` has `supportedDevicesAndVariants`, `defaultVariant`, `getVariants(forModel:)`, and `getPathForBundle(withModel:andVariant:)`.
- `BuildContainer` has `fetchTetheredBuildsInformation()`.
- The GUI has `startTetheredRestore(forDevice:)` and `startTetheredRestore(forDevice:withLocalFilePath:andDylibSharedCachePath:)`.
- The GUI has `startDownloadingForRestore(byTetheredBuild:shouldSearchForDylibCacheFile:dylibSharedCacheLocationPath:device:variant:andDownloadPath:)`.
- The GUI can apply tethered restore type and tethered variant across selected devices with `tryApplyRestoreTypeOnAllDevicesInCurrentSelection(withRestoreType:)` and `tryApplyTetheredVariantOnAllDevicesInCurrentSelection(variantName:)`.
- Strings show `Download Tethered Bundle`, `Download | Tethered |`, `Download Tethered file with file name:`, `Found tethered build for model`, and `Finished fetching some variants, tethered build can be selected since there are variants`.
- UI strings expose `RESTORE TYPE`, `TETHERED`, `Tethered Settings`, and `Variant: Intenal development`.
- The default tethered-looking variant is `Internal Development`.

The missing piece is `RestoreIntegrationLayer.framework`. Both `Purple Restore 4 (Beta)` and `restore-cmd-tool` import it, and the symbols name the likely execution layer:

- `RestoreIntegrationLayer.TetheredRestoreSettingsFactory.shared`
- `RestoreIntegrationLayer.TetheredRestoreSettings`
- `RestoreIntegrationLayer.RestoreHandler.shared`
- `RestoreIntegrationLayer.RestoreSettingsProtocol`
- `RestoreIntegrationLayer.RestoreDelegate`
- `RestoreIntegrationLayer.RestoreStep.isTetheredFrameworkStep()`
- `RestoreIntegrationLayer.DevicesHandler.shared`

Without that framework, this folder can identify and select tethered builds, but cannot reconstruct the actual call path that performs the tethered restore.

### Tethered feasibility model

The likely Purple Restore flow is:

1. Discover device model and current state.
2. Fetch build metadata and call `BuildContainer.fetchTetheredBuildsInformation()`.
3. Use `TetheredBuild.getVariants(forModel:)`.
4. Select an `Internal Development`-like variant, or preserve the user-selected tethered variant.
5. Resolve the tethered bundle path with `TetheredBuild.getPathForBundle(withModel:andVariant:)`.
6. Download the tethered bundle and optional dyld shared cache.
7. Read the downloaded bundle metadata.
8. Build `RestoreIntegrationLayer.TetheredRestoreSettings`.
9. Start restore through `RestoreIntegrationLayer.RestoreHandler`, which likely delegates to `RestoreFramework`/MobileDevice.
10. Track tethered-specific restore steps through `RestoreStep.isTetheredFrameworkStep()`.

This looks like a tethered install/restore workflow for internally authorized builds. It does not look like a generic unsigned downgrade bypass. The RestoreFramework still imports and uses Apple MobileDevice/AuthInstall APIs, and hard-stop strings still include:

- `AuthInstall error`
- `TATSU declined to authorize this request`
- `Boot policy SEP error`
- `Boot policy xART error`
- `The baseband cannot be rolled back`
- `Cannot restore older epoch without using DFU`
- `Caller is missing required entitlement`

So, if there is a “tethered downgrade” route here, it is probably bounded by:

- Apple-internal build metadata and tethered bundle availability for the device model.
- An `Internal Development` or equivalent variant.
- AppleConnect/SSO/Knox/NFA access where remote bundles are used.
- `RestoreIntegrationLayer.framework`.
- MobileDevice/AuthInstall authorization for the chosen build.
- DFU/recovery requirements for older epoch transitions.
- SEP/baseband/boot-policy compatibility.

### Boot-only clues

`restore-cmd-tool` also contains options that would be useful for a boot-only/tethered flow:

- `--boot`: boot an image from iBEC.
- `--bootFile`: file path of the image to boot.
- `--bootTag`: tag of image to boot, with examples `Diags` and `CFELoader`.
- `--ibootfw`: iBoot firmware needed by iBoot before booting an image.
- `--twoStageiBoot`: expect a second DFU mode.
- `--server`: signing server, defaulting in strings to `http://spidercab:8080`.
- `--demote`: request demotion.

However, every one of those boot/two-stage/demotion flags is marked `unsupported for beta version` in the recovered CLI help. They are useful as evidence of an intended internal boot path, but they are not evidence that this extracted beta CLI can perform a boot-only tethered downgrade.

### IPS-UU implementation implications

Worth adding:

- A `TetheredBuild` metadata provider that detects whether `SoftwareBundleKit` is present and whether `RestoreIntegrationLayer.framework` is present.
- A tethered preflight report: model, restore type, selected variant, tethered bundle path, dyld shared cache path, current mode, required mode, signing/authorization status, and missing internal dependencies.
- UI support for `Restore Type: Tethered` and `Variant: Internal Development` when the internal profile is enabled.
- Log parsing for tethered phases and `isTetheredFrameworkStep()`.
- A hard blocker when `RestoreIntegrationLayer.framework` is absent.

Not supported from this folder alone:

- Proving a generic tethered downgrade path.
- Running the tethered restore.
- Bypassing AuthInstall/TATSU, SEP, baseband, boot policy, or entitlement checks.
- Using the beta CLI boot flags, because they are explicitly unsupported in this build.

## PurpleRestore Classic findings

`PurpleRestore Classic.app` is a much older PurpleRestore build, version `477.9.2`, source version `4770902`, built as a fat Mach-O for `ppc_7400`, `i386`, `ppc64`, and `x86_64`. Unlike Purple Restore 4, it does not link the modern `RestoreIntegrationLayer.framework`; it links only system frameworks plus `Bom.framework`, `IOKit`, `Security`, `SystemConfiguration`, and Cocoa. Its restore stack appears to be statically included in the application binary.

Static strings identify source paths from `PurpleRestore-477.9.2` and embedded components such as:

- `libusbrestore/AMRestore.c`
- `libusbrestore/AMRestoreRecoveryMode.c`
- `libusbrestore/AMRAuthInstall.c`
- `libusbrestore/tssclient/lib/hashop.c`
- `DFUUSBDevice.m`
- `RecoveryModeUSBDevice.m`
- `MuxedUSBDevice.m`
- `RestoreController.m`

This is important: Classic is not just a GUI for the modern private framework. It carries an old AMRestore/libusbrestore implementation with DFU, Recovery, usbmux, IMG3 personalization, TSS request/response handling, and restore bundle parsing.

### Classic downgrade-relevant evidence

Classic contains a real legacy DFU/recovery restore path:

- DFU and recovery device classes: `DFUUSBDevice`, `RecoveryModeUSBDevice`, `AMDFUModeDevice`, `AMRecoveryModeDevice`.
- Device discovery over IOKit and usbmux, including `LocationID`, `ProductID`, `DeviceID`, and `USBMuxListenerCreate`.
- DFU file resolution for paths like `dfu/WTF...dfu`, `iBSS`, `iBEC`, and `FIRMWARE...dfu`.
- Recovery bootstrapping with `ramdisk`, `setenv boot-args`, `setenv debug-uarts`, `bootx`, and `Executing iBEC to bootstrap update`.
- Restore phases including `Personalizing restore bundle`, `DFU download`, `Sending ramdisk to device`, `Setting restore boot-args`, `Updating baseband`, `Finalizing NAND epoch update`, `Closing modem tickets`, and `Clearing NVRAM`.
- AuthInstall/TSS personalization paths including `BuildManifest.plist`, `BuildSubmission.plist`, `RestoreBehavior`, `AuthInstallVariant`, `AuthInstallRestoreBehavior`, `AuthInstallSigningServerHost`, `AuthInstallSigningServerPort`, `ApECID`, and TSS endpoint `/TSS/controller?action=2`.
- IMG3 personalization/stitching routines such as `tss_get_partial_hash`, `tss_stitch_img3`, and `failed to merge img3`.

The clearest downgrade-related string is:

- `Cannot restore older epoch without using DFU`

That means Classic understood at least one older-generation downgrade/epoch transition case where DFU was mandatory. It is evidence for a legacy DFU-required downgrade path on supported old devices/builds, not evidence for a modern forced downgrade bypass.

Classic also contains hard stops that limit downgrade expectations:

- `The baseband cannot be rolled back`
- `Baseband bootloader is too old`
- `Unable to connect to signing server`
- `variant "%@" isn't published`
- `can't find device "%@" in manifest`
- `With a production iBoot, boot-args may be set but they are ignored and have no effect.`

So the old flow still depends on matching bundle metadata, AuthInstall/TSS authorization, device identity, and baseband compatibility. It may help IPS-UU model legacy restore behavior, but it does not remove signing or baseband rollback policy.

### Classic PR2 document model

The bundled `Contents/Resources/PR2Document.plist` is highly valuable for IPS-UU because it exposes the older restore-settings schema. Important keys and controls include:

- Device browser automation: `PRDeviceBrowser.RestoreOnConnect`, `BreakBetweenRestoreStages`, `DontExpectReboot`, `LoopUntilError`, pre/post restore scripts, and restore bundle search URLs.
- Personalized install: `RestoreOptions.AuthInstallVariant`, `AuthInstallSigningServerHost`, `AuthInstallSigningServerPort`, default host `tatsu-tss-internal.apple.com`.
- Restore components: `RestoreOptions.RestoreBundlePath`, `BootOptions.FirmwareDirectory`, `BootOptions.BootImageFile`, `BootOptions.DFUFileType`, `BootOptions.DFUFile`.
- Restore OS booting: `BootOptions.RestoreBootArgs`, `BootOptions.NORImageType`, `BootOptions.BootImageType`, `BootOptions.KernelCacheType`, `BootOptions.KernelCacheFile`.
- Hardware readiness: `RestoreOptions.MinimumBatteryVoltage`, `LetBatteryCharge`, `WaitForStorageDevice`, and `AllowUntetheredRestore`.
- Storage and filesystem operations: `EraseDataPartition`, `CreateFilesystemPartitions`, `SystemPartitionSize`, `EncryptDataPartition`, `WipeStorageDevice`.
- System/data image selection: `RestoreOptions.SystemImageType`, `RestoreOptions.SystemImage.ImageFile`, `RestoreOptions.DataImage.ImageFile`.
- Firmware/baseband controls: `FlashNOR`, `NORImageType`, `UpdateBaseband`, `ForceBasebandUpdate`, `CloseModemTickets`, `UpdateStaticEEPOnly`, `VerifyStaticEEP`, `IgnoreBadStaticEEPBackup`.
- Finish actions: `SetTimeOnDevice`, `CreateFactoryRestoreMarker`, `ReadOnlyRootFilesystem`, `ClearNVRAM`, `ClearPersistentBootArgs`, added/removed persistent boot args, `AutoBootDelay`, `BootOptions.SetRecoveryModeOutput`, and `PostRestoreAction`.

The `AllowUntetheredRestore` key is notable, but the Classic binary does not expose the newer Purple Restore 4 `TetheredBuild` model. In this older context it appears to be a restore option controlling whether the tool permits an untethered restore operation, not proof of a tethered downgrade mechanism.

### Classic device display model

Classic has a more professional, information-dense device browser than IPS-UU currently uses. Its NIB exposes a table with columns for:

- Device
- ECID
- USB Location
- Serial Number
- Restore Settings
- Bundle
- Root
- Data
- Progress

The row bindings include `value.model.boardConfig`, `value.model.ecid`, `value.model.location`, `value.model.serialNumber`, `value.model.settings.RestoreOptions.RestoreBundlePath`, `RootToInstall`, `DataImage.ImageFile`, `value.model.state`, and `progressController`. This validates the earlier IPS-UU direction: device display should be a compact restore console, not just a connected-device card.

### Classic IPS-UU implications

Worth adding:

- PR2 document import/export in addition to Purple Restore 4 `.pr` document support.
- A legacy restore-options schema that includes DFU file/type, firmware directory, boot image, boot args, NOR type, baseband options, NVRAM/boot-args changes, auto-boot delay, and post-restore action.
- A legacy downgrade preflight rule: if target metadata implies an older NAND epoch, require DFU mode and flag that this is only applicable to supported old devices/builds.
- A baseband rollback blocker even when `ForceBasebandUpdate` is set, because Classic itself says the baseband cannot be rolled back.
- A Classic-style device browser table/view with ECID, USB location, serial, board config, selected restore settings, selected bundle/root/data images, mode/state, progress, and restore-disabled reasons.
- Optional internal-only TSS/AuthInstall settings display: signing host/port, variant, restore behavior, and personalization log capture.

Not supported from Classic alone:

- A modern tethered downgrade path.
- A generic forced downgrade.
- SEP/Cryptex/baseband rollback bypasses.
- Running Classic directly on current macOS/hardware as a backend without validating old framework/API compatibility and target device generation.

## Device management findings

`DevicesFramework` handles:

- Normal/restorable device registration
- ECID, USB location, product/build, device class/name/color/enclosure
- Recovery OS properties
- Pairing status
- Disabled restore option state per ECID
- DFU entry/reset
- Baseband chip ID
- Restore completion status
- Device aliases

It also contains strings for internal debug/probe behavior and auth-debug ticketing. Those are not appropriate to add to IPS-UU.

IPS-UU value: keep expanding safe device snapshots. Add fields for USB location ID, recovery OS version, baseband chip ID when public tools expose it, pairing/lock state, and restore-disabled reasons.

## Knox/NFA and AppleConnect

`RestoreSDProtocol`, `DownloadManagerFramework`, `SoftwareBundleKit`, `SearchFramework`, and `KnoxPlugin` include Knox/NFA support:

- Knox pointers for `restore-image`
- Build/device/variant query construction
- Knox and NFA download status tracking
- Checksum verification
- AppleConnect/SSO token lookup and verification
- NFA token retrieval
- Weak `AppleConnectClient.framework` linkage

This is Apple-internal delivery infrastructure. For an Apple-internal IPS-UU deployment, it is suitable as a backend if credentials, entitlement, network, and service access are available.

IPS-UU value: build a generic “software bundle source” interface with local IPSW/local restore-bundle/public signed-firmware metadata providers plus Apple-internal Knox/NFA providers enabled by an internal configuration flag.

## Apple-internal backends worth adding

1. Purple Restore CLI executor.
   - Wrap `MacOS/restore-cmd-tool` as an executor.
   - Required: present `RestoreIntegrationLayer.framework`, matching `DYLD_FRAMEWORK_PATH`, internal `MobileDevice.framework`, Apple-internal network/service access.
   - Inputs: ECID or USB location ID, restore document, local bundle, variant, OTA flag, force Knox flag, typed overrides, log path.
   - Outputs: restore logs, status plist/log path, progress events from stdout/stderr or callback files.

2. RestoreFramework dynamic adapter.
   - Load `RestoreFramework.framework` and call the `RestoreManager` surface if headers or bridge metadata are available.
   - Required: Objective-C bridge layer, headers or selector mapping, `RestoreIntegrationLayer`, `MobileDevice.framework`, and restore delegate implementation.
   - Value: richer progress and validation callbacks than spawning the CLI.
   - Risk: higher ABI/API drift than CLI wrapping.

3. SoftwareBundleKit/SearchFramework provider.
   - Use `SoftwareBundleKit` and `SearchFramework` to discover local, Knox, NFA, OTA, and livability-backed builds.
   - Required: AppleConnect/SSO when remote providers are enabled.
   - Value: avoids reimplementing build/variant/device mapping logic.

4. DownloadManager/Knox provider.
   - Use `DownloadManagerFramework`, `RestoreSDProtocol`, and `KnoxPlugin` for authenticated internal download and checksum verification.
   - Required: AppleConnect/SSO, Knox/NFA reachability, compatible `KnoxPlugin`, likely `DYLD_FRAMEWORK_PATH`.
   - Value: proper internal bundle retrieval instead of manual URL handling.

5. Internal OTA workflow.
   - Use the bundled `LoggerFramework` scripts as the initial implementation reference.
   - Required: `tcprelay`, root SSH to the target environment, `test_software_update`, `asutil`, MobileAsset URLs, brain/update bundle assets, and SSO where needed.
   - Value: system-update preparation, brain asset handling, free-space cleanup, rsync retry, suspend/cleanup, lock-state checks, and sysdiagnose capture.

6. Restore option document editor.
   - Import/export Purple Restore `.pr` documents and expose typed overrides.
   - Required: schema discovery from sample `.pr` docs or exported settings.
   - Value: easiest cross-backend feature because both CLI and framework paths consume restore settings.

## What is worth adding to IPS-UU

Highest-value additions:

1. Restore option document support.
   - Parse/import/export Purple Restore 4 `.pr` settings, Classic PR2 documents, or a project-native JSON equivalent.
   - Track default vs non-default options.
   - Support typed overrides: string, bool, number, URL/path, JSON.
   - Include Classic fields such as DFU file/type, firmware directory, boot image, boot args, NOR type, baseband controls, NVRAM changes, auto-boot delay, and post-restore action.
   - Guard high-impact options like baseband update, NVRAM changes, boot args, forced DFU files, and restore bundle overrides behind review.

2. Stronger local bundle inspector.
   - Accept IPSW, DMG, or extracted restore bundle paths.
   - Extract `BuildManifest.plist`, `Restore.plist`, `RestoreBundles`, supported devices, variants, board configs, product version/build, and bundle path.
   - Report metadata failures clearly.

3. Restore session progress model.
   - Represent Purple Restore’s step names as normalized IPS-UU phases.
   - Map `idevicerestore`/`cfgutil` output into phases such as personalization, recovery entry, DFU upload, baseband update, filesystem send, verify, finish, abort.
   - Persist a per-device restore session record with ECID/UDID, selected IPSW, selected variant, logs, start/end timestamps, result, and failure category.
   - Use a Classic-style connected-device table for dense restore stations: device, ECID, USB location, serial, board config, selected settings, selected bundle/root/data image, state, and progress.

4. Downgrade/restore preflight warnings.
   - Compare current build/version against target build/version.
   - Flag target older than current.
   - If legacy metadata implies an older NAND epoch, require DFU and label the path as old-device/old-build only.
   - Flag unknown signing state.
   - Flag likely baseband rollback.
   - Flag SEP/Cryptex/baseband compatibility unknown.
   - Flag device not in required mode.
   - Make the user explicitly acknowledge erase/update behavior before execution.

5. Log capture and triage.
   - Create restore log directories per device and restore UUID.
   - Save host command stdout/stderr, selected options, device snapshot, manifest summary, and restore result.
   - Categorize common failure causes: TSS/AuthInstall denial, baseband rollback, wrong mode, device locked, download failure, space failure, user abort, timeout.

6. Build source abstraction.
   - Providers: local IPSW, local restore bundle, public signed firmware metadata, existing downloaded cache.
   - Internal providers: Knox, NFA, AppleConnect, PurpleRestore indexes, livability.
   - Preserve provider requirements so users understand when internal providers are unavailable.

7. OTA research checklist.
   - Add a non-executing checklist/report for OTA flows: prerequisite build, OTA asset, brain asset, root SSH/tcprelay/test_software_update availability, free-space estimate if local metadata exists.
   - In Apple-internal mode, promote this to an executable OTA workflow with explicit target/device confirmation.

## What should not be added without internal prerequisites

- Direct execution of `restore-cmd-tool` or `Purple Restore 4` when `RestoreIntegrationLayer.framework` is missing.
- Dynamic loading of `RestoreFramework`, `RestoreIntegrationLayer`, `MobileDevice.framework`, `AppleConnectClient`, `SSOClient`, or `KnoxPlugin` without matching framework versions and a controlled internal runtime.
- Knox/NFA/Basejumper/Livability service access without Apple-internal network, credentials, and audit expectations.
- AuthDebug, probe, Astris, JTAG/SWD, factory marker, or production-fused debug flows unless those are explicitly in scope for the internal station.
- Attempts to bypass Apple signing, activation, SEP/baseband policy, or restore eligibility.
- Automatic use of root SSH scripts against devices that do not match the intended internal/rooted restore environment.

## Bottom line

The most valuable thing in this bundle is not a downgrade exploit; it is Purple Restore’s product model and internal backend layout:

- separate build discovery from restore execution,
- inspect local restore metadata before execution,
- track restore options and non-default overrides,
- validate device/build/variant combinations,
- present restore steps and logs clearly,
- fail early on downgrade/baseband/signing risks,
- keep private/internal download and restore services gated behind an Apple-internal execution profile.

That maps cleanly onto IPS-UU if the app grows two explicit modes: public/signed-restore mode and Apple-internal Purple Restore mode.
