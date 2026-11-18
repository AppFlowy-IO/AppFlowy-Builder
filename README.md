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

## Desktop auto-update checks

**Auto Update Workflow finishes before feed publication.** Select a tag such as
`0.15.1`. The default selection covers both Mac architectures, Windows x64 and
the Linux legacy notification.

1. Builder resolves the previous published stable version (for example,
   `0.15.0`), verifies its release installer and installs it on a standard hosted
   runner. It signs the target installer and generates the selected XML feeds.
2. A loopback HTTP server serves the candidate feed and the signed installer.
   Sparkle upgrades the installed Mac bundle and relaunches it. On Windows a
   test driver uses the previous installation’s WinSparkle DLL and matching key
   to run Inno Setup against the existing installation, then starts the updated
   executable. The tests check both requests, the installed target version and
   process startup. The app’s Flutter button and the live update API are outside
   this staging check.
3. **Verify staged upgrade** checks every selected XML filename and matches the
   installer digest to the successful upgrade evidence. The run saves the XML
   artifacts and a JSON record of their SHA-256 hashes. On a normal run Builder
   then advances its release version default. `testOnly=true` also accepts drafts
   and skips the defaults commit.
4. After the run succeeds, copy its XML artifacts unchanged to the same GitHub
   release. Run **Verify published desktop update** with the successful staging
   run ID. This read-only workflow checks the tagged and latest public URLs
   against the recorded hashes. For every selected native platform, the live
   API must return the correct version, enabled policy, exact appcast and matching
   installer for a client on the previous version. It allows up to 20 minutes
   for the server’s release cache to refresh.

| Selection | Feeds |
| --- | --- |
| `legacy` | `appcast-{os}-{arch}.xml`, discovered through GitHub’s latest release |
| `native` | `native-appcast-{os}-{arch}.xml`, discovered through the deployment’s update API |
| `both` | Both filenames for each native target, plus the Linux legacy feed when selected |

Both filenames must be attached even when their hashes match. Linux supports
only the legacy notification. See [Native updater CI](scripts/update_e2e/README.md)
for fixture coverage and rehearsal commands.

```sh
gh workflow run verify-published-desktop-update.yaml --repo AppFlowy-IO/AppFlowy-Builder \
  -f stagingRunId="$STAGING_RUN_ID"
```
