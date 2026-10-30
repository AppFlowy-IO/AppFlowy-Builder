# Native updater CI

`Native update E2E` runs on pull requests, pushes to main and manual dispatch.
The matrix covers macOS ARM64, macOS Intel and Windows x64 on disposable GitHub
hosted runners. No release secrets are available to the pull request tests.

The controller builds small native hosts using Sparkle 2.8.1 and WinSparkle
0.9.4, the versions used by Desktop. Dependency downloads are checksum pinned.
It generates temporary signing keys, serves a feed on loopback, and observes
the real updater replacing the host through a DMG or an Inno Setup installer.
The controller remains alive across app termination and restart.

| Case | macOS | Windows | Pass condition |
| --- | --- | --- | --- |
| Valid update | ARM64 + Intel | x64 | Final executable matches the target, old process exits, new process reads pre-update data |
| Wrong signing key | ARM64 + Intel | x64 | Native engine rejects download, installer never runs, old executable remains |
| Target removes update key | ARM64 + Intel | — | Sparkle returns signature/validation error despite a valid archive signature |
| Skip update | ARM64 + Intel | — | No installer download and no executable change |

Reports contain native events, process IDs, installer SHA256, HTTP requests,
console output, appcast and Windows installer log. They are uploaded even when
a case fails. Keys and application binaries are not uploaded. The local fixture
uses a unique bundle identifier; Windows preferences stay in memory.

## Test the next release

The existing `Auto Update Workflow` now tests the **final signed installer**
before uploading appcast artifacts or updating release version configuration.
Its macOS Intel job runs on an Intel runner so the executable can actually run.
The candidate must retain the deployed public key and native updater library.

For a rehearsal against a draft or published release, run this after the CI
change is merged (replace the example tag with the exact release tag):

```sh
gh workflow run auto_update.yaml --repo AppFlowy-IO/AppFlowy-Builder \
  -f platform='all platforms' -f updaterGeneration=both \
  -f appflowyVersion=0.14.12 -f repoOwner=AppFlowy-IO -f repoName=AppFlowy \
  -f testOnly=true
```

Use `platform=macos` or `platform=windows` to test one platform. The release must
already contain the final signed/notarized packages. Existing Builder signing
secrets are used; no new secrets or test channel are required. `testOnly` allows
drafts and skips the version-config commit. It uploads test evidence and feed
artifacts; it does not publish a release or upload assets to a release.

Candidate tests use an instrumented native source host with AppFlowy's bundle
identity and deployed public key. They download, validate and install the exact
signed release bytes, check the installed key/version/updater, and require the
installed AppFlowy process to survive 15 seconds. Sparkle relaunches AppFlowy;
on Windows the controller launches it after Inno Setup completes, because the
production installer has no `[Run]` entry. Only GitHub-hosted runners can execute
this mode, so an installed AppFlowy profile is never reused.

These tests cover the native installer boundary. They do **not** exercise the
unchanged 0.14.7 Flutter client, cloud release discovery, server compatibility
policy, the in-app consent UI, or document/database migration. A fixture data
sentinel is not a workspace migration test. Validating those requires a separate
full-client test channel and test account. The candidate key/library checks
guard the following update; they do not claim to perform candidate → N+1.

## Run fixtures locally

On macOS, with Xcode command-line tools installed:

```sh
python3 -m venv /tmp/appflowy-update-test-venv
/tmp/appflowy-update-test-venv/bin/pip install -r scripts/update_e2e/requirements.txt
/tmp/appflowy-update-test-venv/bin/python scripts/update_e2e/run.py \
  --output /tmp/appflowy-update-test-results
```

Use a new output directory on each run. On Windows, run the same Python command
from an x64 MSVC developer shell with Inno Setup 6 installed. `--scenario success`
or `--scenario bad-signature` selects one case. The runner never changes the
installed app, cloud URL, credentials, data directory or development settings.
