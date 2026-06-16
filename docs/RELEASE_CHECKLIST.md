# iPS-UU First Release Checklist

Run before publishing a build:

```sh
./scripts/check_release.sh
./scripts/build_gui.sh
```

Manual smoke checks:

- Launch `iPS-UU.app` and confirm the custom app icon is shown.
- Open every sidebar tab at the minimum window size and fullscreen.
- Confirm `Dry-run only mode` is enabled by default.
- Confirm `iOS Device Viewer` shows an empty state with no device attached.
- Confirm `External Tools` and `palera1n` inventory missing tools cleanly when tools are absent.
- Confirm `palera1n` terminal allows only `help`, `clear`, `version`, `rootless`, `status`, and `guide`.
- Confirm real signed restore execution still requires disabling dry-run mode and explicit wipe confirmations.

Release archive exclusions:

- Do not include `Contents*/`, `*.app/`, `decompiled*/`, `idevicerestore-*/`, `turdus_m3rula_*/`, IPSWs, `.DS_Store`, `__pycache__/`, `build/`, or `dist/` source leftovers.
- Do not redistribute Apple Configurator binaries or private Apple frameworks.

Screenshot placeholders:

- `docs/screenshots/dashboard.png`
- `docs/screenshots/ios-device-viewer.png`
- `docs/screenshots/firmware-ipsw.png`
- `docs/screenshots/restore-dry-run.png`
- `docs/screenshots/external-tools.png`
- `docs/screenshots/palera1n.png`
