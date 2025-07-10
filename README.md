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

## How to use

> [!CAUTION]
> Remember, all packages are zipped. Ensure to **unzip** them before use.

- Navigate to the `Actions` tab in your repository.
- Select the workflow you wish to run.
- Click the `Run workflow` button.
- Enter the required variables as prompted.
