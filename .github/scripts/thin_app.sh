#!/bin/bash
set -euo pipefail

# Usage: thin_app.sh universal.app arm64|x86_64 [output.app]
if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 universal.app arm64|x86_64 [output.app]" >&2
  exit 1
fi

UNIVERSAL_APP="$1"
ARCH="$2"
OUTPUT_APP="${3:-$ARCH.app}"

case "$ARCH" in
  arm64|x86_64) ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

if [[ ! -d "$UNIVERSAL_APP" ]]; then
  echo "App not found: $UNIVERSAL_APP" >&2
  exit 1
fi
if [[ -e "$OUTPUT_APP" || -L "$OUTPUT_APP" ]]; then
  echo "Output already exists: $OUTPUT_APP" >&2
  exit 1
fi

# Preserve framework symlinks and inspect all regular files, including binaries
# without an executable bit or a dylib extension.
cp -R "$UNIVERSAL_APP" "$OUTPUT_APP"
while IFS= read -r -d '' binary; do
  if [[ "$(file -b "$binary")" != *Mach-O* ]]; then
    continue
  fi

  if ! xcrun lipo "$binary" -verify_arch "$ARCH"; then
    echo "Missing $ARCH slice: $binary" >&2
    exit 1
  fi

  read -r -a archs <<< "$(xcrun lipo "$binary" -archs)"
  if [[ ${#archs[@]} -gt 1 ]]; then
    echo "Thinning: $binary"
    xcrun lipo "$binary" -thin "$ARCH" -output "${binary}.thin"
    chmod "$(stat -f '%Lp' "$binary")" "${binary}.thin"
    mv "${binary}.thin" "$binary"
  fi
done < <(find "$OUTPUT_APP" -type f -print0)

echo "Created $OUTPUT_APP with $ARCH architecture only"
