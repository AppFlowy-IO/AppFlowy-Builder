#!/usr/bin/env bash
set -euo pipefail

workflow="${1:-.github/workflows/ios.yaml}"

if [[ ! -f "$workflow" ]]; then
  echo "iOS workflow not found: $workflow" >&2
  exit 1
fi

if ! grep -Eq 'os: \[(macos-26|macos-26-intel|macos-26-large|macos-26-xlarge)\]' "$workflow"; then
  echo "iOS workflow must run on a macOS 26 runner so Xcode 26/iOS 26 SDK is available." >&2
  exit 1
fi

if grep -Eq 'os: \[macos-(13|14|15)\]' "$workflow"; then
  echo "iOS workflow still references an older macOS runner image." >&2
  exit 1
fi

if ! grep -q 'Verify Xcode and iOS SDK version' "$workflow"; then
  echo "iOS workflow must verify Xcode and iphoneos SDK versions before building." >&2
  exit 1
fi
