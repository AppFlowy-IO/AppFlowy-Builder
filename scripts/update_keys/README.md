# Deployed updater keys

These are public verification keys, extracted from the published AppFlowy
0.14.7 macOS and Windows applications and compared with Desktop source. They
are a compatibility contract with installed clients, independent of CI secrets.
Never replace them simply because a new signing job uses a different key.
Key rotation requires a separate, reviewed migration for the installed clients.

- `macos-ed25519.pub`: `SUPublicEDKey` in the macOS application's `Info.plist`.
- `windows-dsa.pem`: `DSAPUB` / `DSAPEM` resource in the Windows executable.

Sparkle rejects a macOS update that removes `SUPublicEDKey`, even if its
archive signature, Apple code signature, and notarization are valid. Retain
the key when replacing or removing the updater implementation. The published
0.14.11 macOS apps omitted it; Desktop PR #1533 restores it for subsequent builds.
Changing XML or signing the existing DMG again cannot repair that bundle.

Windows 0.14.7 verifies DSA/SHA1 installer signatures. WinSparkle 0.9.4 still
supports these signatures, so the legacy and native feeds currently share the
same signature algorithm. Authenticode signing must finish before update signing.

The build workflows check the actual macOS bundles and the compiled Windows
key resource before packaging; Windows resources are loaded as data without
executing the application. The feed
workflow also validates the downloaded macOS app, code signing, notarization,
Windows Authenticode, and final installer signatures. Feed generation independently
verifies the signatures again so replacing bytes after signing fails closed.

Run the regression tests in an isolated Python environment:

```sh
python -m pip install -r scripts/requirements-update-validation.txt
python -m unittest discover -s scripts -p 'test_*.py' -v
```

These checks do not install AppFlowy or modify its settings. They do not replace
an end-to-end upgrade test on each operating system. Publish both selected feed
generations under their exact names, including the Intel macOS feed; Actions
artifacts alone are not public release assets.
