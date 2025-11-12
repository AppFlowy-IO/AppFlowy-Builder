#!/bin/bash

# Usage: ./thin_app.sh universal.app arm64 (or x86_64)

UNIVERSAL_APP="$1"
ARCH="$2"
OUTPUT_APP="${ARCH}.app"

# Copy the entire app
cp -R "$UNIVERSAL_APP" "$OUTPUT_APP"

# Find all Mach-O binaries (executables, frameworks, dylibs)
find "$OUTPUT_APP" -type f \( -perm +111 -o -name "*.dylib" -o -name "*.framework" \) | while read binary; do
    # Check if it's a Mach-O file and contains multiple architectures
    if file "$binary" | grep -q "Mach-O"; then
        archs=$(lipo -info "$binary" 2>/dev/null | grep "Architectures in the fat file")

        if [ ! -z "$archs" ]; then
            # It's a fat binary, thin it
            echo "Thinning: $binary"
            lipo "$binary" -thin "$ARCH" -output "${binary}.thin" 2>/dev/null

            if [ $? -eq 0 ]; then
                mv "${binary}.thin" "$binary"
            else
                echo "  Warning: Could not thin $binary (might not contain $ARCH)"
            fi
        fi
    fi
done

echo "Done! Created $OUTPUT_APP with $ARCH architecture only"
