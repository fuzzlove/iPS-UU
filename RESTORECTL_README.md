# restorectl PoC

`restorectl` is a safe proof-of-concept restore wrapper for normal
Apple-signed iOS firmware restore flows.

It does not bypass Apple signing and does not support unsigned downgrades,
SHSH/APTicket abuse, APNonce manipulation, SEP/baseband bypasses, exploit
chains, pwned DFU, private entitlement abuse, or manifest/ticket patching.

## Commands

Dry-run preflight:

```sh
restorectl restore --ipsw ./firmware.ipsw --device auto --dry-run
```

Equivalent in this source tree:

```sh
python3 -m ips_uu restorectl restore --ipsw ./firmware.ipsw --device auto --dry-run
```

Dry-run with an explicit device model:

```sh
restorectl restore --ipsw ./firmware.ipsw --device auto --product-type iPhone13,2 --dry-run
```

Inventory local restore-related tools:

```sh
restorectl tools
```

Execute a normal Apple-signed erase restore:

```sh
restorectl restore --ipsw ./firmware.ipsw --device auto --execute --i-understand-this-erases-device
```

Execution currently uses `idevicerestore` only. If `idevicerestore` is not on
`PATH`, dry-run still works and execution fails closed.

## Dry-Run Output

Dry-run prints JSON containing:

- detected device model/ProductType when available
- ECID when safely available from `ideviceinfo` or `irecovery`
- current mode: normal, recovery/DFU, unknown, or not detected
- IPSW product version and build version from `BuildManifest.plist`
- `Restore.plist` presence and top-level keys when present
- selected build identity and restore variant
- signing status and compatibility notes
- exact restore plan and `idevicerestore` command
- guardrails and limitations

## API And Tool Map

### Public or Supported Command-Line Surface Used

| Purpose | Tool | Used By PoC | Notes |
| --- | --- | --- | --- |
| Normal-mode device info | `ideviceinfo -x` | Yes | Reads ProductType, ProductVersion, BuildVersion, UniqueChipID, UDID, and DeviceName. |
| Recovery/DFU info | `irecovery -q` | Yes | Reads ECID/mode-style fields when normal lockdown is unavailable. |
| Firmware restore execution | `idevicerestore` | Yes, execute only | Performs normal Apple-signed restore. Signing/APTicket validation is delegated to the supported restore executor. |
| IPSW parsing | Python `zipfile` + `plistlib` | Yes | Reads `BuildManifest.plist` only. |
| Local tool inventory | `restorectl tools` | Yes | Detects supported and unsupported local restore-related tools without executing unsupported tools. |

### Private Or Unstable APIs Found Locally

These APIs are documented here for audit context. `restorectl` does not call
them.

| Area | Symbols or Classes Observed | Risk Notes |
| --- | --- | --- |
| MobileRestore execution | `AMRestorableDeviceRestore`, `AMRestorableDeviceRestoreWithError`, `AMRestorePerformDFURestore`, `AMRestorePerformRecoveryModeRestore` | Private APIs; require Apple-private contracts/entitlements and are not used. |
| Restore option orchestration | `ATKImager`, `ATKImagingRestorableMobileDevice`, `FFDCSDRestoreLibWrapper`, `CoreFactorySupport` restore helpers | Private/internal factory frameworks; not stable or appropriate for general restore execution. |
| Restore-mode observation | `AMRestoreRegisterForDeviceNotifications`, `AMRestoreModeDeviceCopyRestoreLog`, `AMRestoreModeDeviceGetProgress`, `AMRestoreModeDeviceCopyEcid` | Useful for understanding restore logs, but private and not used by the PoC. |
| Factory diagnostics | DCSD ramdisk, diagnostics, prevent-restores, post-restore UART sequence helpers | May alter device state or require factory environment; not executed. |
| AuthInstall and signing | `AuthInstallSigningServerURL`, `PersonalizedRestoreBundlePath`, `APTicket`, `RestoreSEP` | Sensitive signing/personalization path; PoC does not create, replay, patch, or bypass tickets. |
| MobileSync backup restore | `AMSRestoreWithApplicationsRequest`, `AMSRestoreRestoreOptionsKey` | Backup restore only, not firmware restore. Useful for log classification but not firmware execution. |

### Internal Tool Policy

Some machines may contain internal or factory restore binaries such as
`mobile_restore`, `prestore`, `factory_purple_restore`, `goldrestore`,
`goldrestore2`, or `factory_demo_restore`. `restorectl tools` reports whether
they are present, but `restorectl` does not reverse-engineer, wrap, or execute
them.

Reasons:

- behavior and required environment are private/unstable
- they may require Apple-private entitlements, factory infrastructure, or
  unpublished contracts
- they may expose restore states outside the normal documented Apple-signed flow
- using them would make this PoC harder to audit and easier to misuse

The supported execution boundary remains `idevicerestore` only.

### Callback And Session Surfaces Observed

| Surface | Where It Appears | PoC Handling |
| --- | --- | --- |
| DeviceLink attach/detach callbacks | FakeTunes `DLDeviceListenerCreateWithCallbacks`, `_DLListenerAttachedCallback`, `_DLListenerStoppedCallback` | Not called; `restorectl` uses one-shot CLI detection. |
| MobileDevice notification callbacks | PurpleFAT and FakeTunes `AMDeviceNotificationSubscribe`, `AMDSecureListenForNotifications`, notification proxy strings | Not called; no long-running device notification session is opened. |
| Restore progress/log callbacks | Framework findings around `ATKImager` progress blocks and PurpleSNIFF restore-mode progress/log copy APIs | Not called; `idevicerestore` owns progress output during execution. |
| Restore option dictionaries | PurpleRestore/PurpleRabbit/Framework resource templates with `RestoreOptions` keys | Parsed only for planning context by other iPS-UU tools; `restorectl` builds no private MobileRestore session. |
| Build identity dictionaries | IPSW `BuildManifest.plist` `BuildIdentities[*]` entries | Parsed for ProductType/build/variant compatibility and component presence. |

## Guardrails

`restorectl` enforces these project rules:

- custom signing servers are not accepted
- unsigned restore flags are not exposed
- external SEP/baseband component overrides are not accepted
- execution requires detected or requested ProductType compatibility with the IPSW
- execution refuses downgrade attempts because this PoC does not preflight-confirm
  current Apple signing
- erase execution requires `--i-understand-this-erases-device`
- manifests, tickets, APTickets, nonces, SEP, baseband, and bootchain files are
  never patched
- execution never continues after `idevicerestore` reports a signing,
  APTicket, nonce, or restore validation failure

## Signing And Compatibility Limits

The dry-run currently reports Apple signing status as
`not_verified_in_preflight` unless a supported local checker is added later.
This is intentional. The PoC does not synthesize TSS requests or rely on
third-party signing databases. Actual restore execution depends on
`idevicerestore` and Apple's normal online signing flow.

SEP/baseband compatibility is handled conservatively:

- only components bundled in the selected IPSW BuildIdentity are considered
- external SEP/baseband images are unsupported
- final validation is delegated to the normal Apple restore flow

Downgrade attempts are flagged when the target build appears older than the
current device build. Executable downgrade attempts are refused unless a future
signed-only preflight can prove the target is currently accepted by Apple's
normal signing flow.

## Non-Goals

`restorectl` does not implement:

- unsigned restores or unsigned downgrades
- offline signing bypasses
- SHSH/SHSH2/APTicket generation or abuse
- APNonce/generator manipulation
- SEP/baseband mismatch bypasses
- checkm8/pwned DFU/palera1n/ipwndfu/gaster/pongoOS flows
- private MobileRestore or AuthInstall execution
- restore ramdisk patching
- manifest patching
