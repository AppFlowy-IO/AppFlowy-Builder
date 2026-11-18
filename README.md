# AppFlowy Builder

AppFlowy Builder is a tool designed to streamline the process of building and deploying AppFlowy applications across multiple platforms. This document outlines the necessary steps and requirements to use this tool effectively.

## Getting Started

Before you begin, ensure you have the required secrets set in your repository settings. These secrets are essential for the workflows to operate correctly. For guidance on creating secrets, see [GitHub's documentation on using secrets in GitHub Actions](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions).

### Requirements for iOS

To build for iOS, set the following secrets in your repository:

- `IOS_CERTIFICATE_BASE64`
- `IOS_KEYCHAIN_PASSWORD`
- `IOS_PROVISION_PROFILE_BASE64`
- `P12_PASSWORD`

For instructions on creating these secrets, refer to [GitHub's guide on deploying Xcode applications](https://docs.github.com/en/actions/deployment/deploying-xcode-applications/installing-an-apple-certificate-on-macos-runners-for-xcode-development).

### Requirements for Android

To build for Android, set the following secrets:

- `ANDROID_UPLOAD_KEYSTORE`
- `ANDROID_UPLOAD_KEYSTORE_KEY_PASSWORD`
- `ANDROID_UPLOAD_KEYSTORE_STORE_PASSWORD`

For instructions on creating these secrets, see [Flutter's deployment guide for Android](https://docs.flutter.dev/deployment/android#sign-the-app).

### Requirements for macOS

Every macOS build produces universal, Apple silicon (ARM64), and Intel (x86_64)
packages as `AppFlowy-<version>-macos-<architecture>.dmg` and `.zip` assets,
where `<architecture>` is `universal`, `arm64`, or `x86_64`. Each package
contains `AppFlowy.app`. The source repository's universal build script builds
both Rust targets once, then Builder thins the app for each architecture before
signing and notarizing all three packages. The release workflow's `macos_arch`
input and the standalone workflow's `arch` input default to `All`. Flutter and
Rust build caching are disabled; Rust release compiler settings are unchanged.
Auto-update generates separate ARM64 and x86_64 appcasts. As before the
ARM64-only change, the ARM64 appcast points to the universal DMG and the x86_64
appcast points to the x86_64 DMG.

To build for macOS, set the following secrets:

- `MACOS_CERTIFICATE_BASE64`
- `MACOS_CODESIGN_ID`

Refer to [GitHub's guide on deploying Xcode applications](https://docs.github.com/en/actions/deployment/deploying-xcode-applications/installing-an-apple-certificate-on-macos-runners-for-xcode-development) for details.

Optional secrets for macOS:

- `MACOS_NOTARY_PWD`
- `MACOS_NOTARY_USER`
- `MACOS_TEAM_ID`

For instructions on creating these secrets, see [this guide on notarizing a command-line tool with NotaryTool](https://scriptingosx.com/2021/07/notarize-a-command-line-tool-with-notarytool).

Example command for macOS notarization:

```sh
xcrun notarytool submit AppFlowy.dmg --apple-id [YOUR_APPLE_ID] --team-id [YOUR_TEAM_ID] --password [YOUR_APPLE_APP_SPECIFIC_PASSWORD] -v -f "json" --wait
```

### Linux architectures

The Linux workflow builds x86_64 and ARM64 natively on Ubuntu 22.04. GitHub's
`ubuntu-22.04-arm` and `ubuntu-24.04-arm` runners must be available to the
repository. The builder selects the frontend's existing
`production-linux-aarch64` profile for ARM64.

| Machine | Debian package | AppImage | Tarball |
| --- | --- | --- | --- |
| Linux x86_64 / amd64 | `AppFlowy-<version>-linux-x86_64.deb` | `AppFlowy-<version>-linux-x86_64.AppImage` | `AppFlowy-<version>-linux.tar.gz` |
| Linux ARM64 / aarch64 | `AppFlowy-<version>-linux-arm64.deb` | `AppFlowy-<version>-linux-arm64.AppImage` | `AppFlowy-<version>-linux-arm64.tar.gz` |

Each architecture also produces an RPM. The x86_64 tarball keeps its existing
name for release consumers. Linux ARM64 artifacts are suitable for ARM Ubuntu;
macOS ARM64 artifacts are a different operating-system build.

The pinned Flutter release has no Linux ARM64 SDK archive, so the ARM job
bootstraps Flutter from the same upstream release tag. It also installs the
pinned cargo-make version from source because the download action only supplies
x86_64 executables on Linux.

ARM64 Rust release builds use ThinLTO and 16 code generation units to reduce
peak compiler memory on the standard runner. Optimization level 3 is retained.

Release upload waits for both builds, native Debian installation on Ubuntu
22.04/24.04/26.04 for each architecture, and native tarball/AppImage startup
on Ubuntu 24.04. RPM conversion and architecture metadata are checked; RPM
installation is not part of these Ubuntu gates. Logs and screenshots have
architecture-specific artifact names.

See [Linux packaging checks](.github/scripts/tests/README.md) for local checks.

### Desktop update metadata

Every desktop build generates `desktop-update.json` from the checked-out
Desktop repository's `frontend/scripts/tool/generate_desktop_update_manifest.py`.
The generator reads the same compatibility table compiled into Desktop; Builder
does not maintain a separate minimum server version.

The main build workflow resolves the requested Desktop branch to one commit and
uses that SHA for every platform. It records the SHA in the Builder release
notes, uploads the JSON as an Actions artifact named `desktop-update.json`, and
attaches the file to that Builder release. Standalone macOS, Windows, and Linux
builds also upload the manifest as an Actions artifact. Both standalone Linux
architectures build from the commit resolved by the metadata preparation job.

Generation runs before desktop compilation and fails if the selected Desktop
revision lacks the generator or its compatibility policy, the version is not a
stable `X.Y.Z`, or the version exceeds the policy's reviewed range. Select a
Desktop revision containing the update feature and review its compatibility
policy before building a new release. Mobile-only builds skip metadata
generation.

When promoting Builder packages to the public `AppFlowy-IO/AppFlowy` release,
copy this generated file unchanged alongside the installers before publishing
the release. Builder does not publish to that public repository automatically.
The server discovers the public release's `desktop-update.json` asset and
exposes its `minimum_server_version` to Desktop and Admin.

To generate the same file locally, run from the Desktop repository root:

```sh
python3 frontend/scripts/tool/generate_desktop_update_manifest.py \
  --version 0.14.9 --output desktop-update.json
```

### Windows PDF release gate

Before uploading either Windows package, the Windows build job extracts the
final ZIP and silently installs the generated Inno Setup EXE into a temporary
directory on its disposable runner. It checks that both payloads declare and
contain an x64 `pdfium.dll` beside `AppFlowy.exe`, then loads each payload's
own DLL in a separate process and renders a known PDF. The check compares
black and white pixels and requires identical application/PDFium hashes across
the ZIP and installed payload. Missing DLLs, load errors, crashes, timeouts,
and incorrect pixels stop both package uploads.

The `windows-pdf-release-smoke` artifact retains package hashes, probe logs,
the installer log, and rendered PNGs. This validates native PDF packaging and
rendering; release validation still needs an inline PDF check in the actual
AppFlowy UI. The script never launches AppFlowy or changes an existing account.
Run it only on a disposable Windows runner because the installer executes.

The `Test Windows PDF release gate` workflow validates the gate with real
PDFium and small Inno Setup fixtures, including a missing DLL in each package
and a DLL that lacks PDFium's exports. Unit tests run with
`python -m unittest discover -s scripts -p test_verify_windows_pdf.py -v`.

## How to use

> [!CAUTION]
> Remember, all packages are zipped. Ensure to **unzip** them before use.

- Navigate to the `Actions` tab in your repository.
- Select the workflow you wish to run.
- Click the `Run workflow` button.
- Enter the required variables as prompted.

## Public desktop auto-updates

In **Auto Update Workflow**, select the release tag and leave the platform at
`all platforms` and `updaterGeneration` at `both` for a full rollout.

1. Builder downloads the selected signed installers, generates their appcasts,
   and exercises the generated feeds through Sparkle and WinSparkle on standard
   GitHub-hosted runners.
2. Builder attaches the selected XML files to the same release. It verifies their
   bytes through both GitHub's tagged and latest public download URLs. For native
   updates it queries the public desktop update API as the previous stable client.
   The version, enabled policy, feed URL and installer URL must match. Discovery
   can take up to 20 minutes while the service refreshes cached release metadata.
3. For native targets, a fresh runner installs the previous official release,
   opens its Update dialog and completes the native installer. The test checks
   that the old process exits, the requested version runs from the installation
   path and no browser opens. Windows starts the installed executable when Inno
   Setup leaves it closed. Each platform uploads its evidence to the run.
4. Builder advances the release version default after the selected checks pass.
   A failure after publication can leave the feeds attached. Inspect the failed
   step and its evidence before retrying.

| Dispatch | Outcome |
| --- | --- |
| Default | Publish and verify the latest stable release. The Builder credential needs contents write access to the target release repository. |
| `verifyPublishedOnly=true` | Repeat public discovery and the installed-client checks for the current rollout using its existing feeds. |
| `testOnly=true` | Test the signed installers and stage feed artifacts. Supports draft and historical releases. |

`legacy` selects `appcast-{os}-{arch}.xml`, which clients discover through
GitHub's latest release. `native` selects `native-appcast-{os}-{arch}.xml`,
which a client discovers through its server. Both names are required even when
their bytes match. Linux supports only the legacy update notification. The
installed-client test covers native macOS ARM64, macOS Intel and Windows x64.

To replay the 0.15.0 → 0.15.1 rollout while 0.15.1 is the latest release:

```sh
gh workflow run auto_update.yaml --repo AppFlowy-IO/AppFlowy-Builder \
  -f appflowyVersion=0.15.1 -f platform='all platforms' -f updaterGeneration=both \
  -f verifyPublishedOnly=true
```
