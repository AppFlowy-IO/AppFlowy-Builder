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

## How to use

> [!CAUTION]
> Remember, all packages are zipped. Ensure to **unzip** them before use.

- Navigate to the `Actions` tab in your repository.
- Select the workflow you wish to run.
- Click the `Run workflow` button.
- Enter the required variables as prompted.
