#!/bin/sh
# Build the separately named, ad-hoc-signed Ticket 14 prototype artifact.
set -eu

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$HERE/14-native-gui-probe.swift"
OUTPUT_DIR="${1:-/private/tmp/chatgpt-native-gui-probe-build}"
OUTPUT="$OUTPUT_DIR/chatgpt-native-gui-probe"
PENDING="$OUTPUT_DIR/.chatgpt-native-gui-probe.pending"

mkdir -p "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/module-cache"
trap '/bin/rm -f "$PENDING"' EXIT INT TERM
CLANG_MODULE_CACHE_PATH="$OUTPUT_DIR/module-cache" \
SWIFT_MODULECACHE_PATH="$OUTPUT_DIR/module-cache" \
  xcrun swiftc \
  -O -whole-module-optimization \
  -framework AppKit -framework ApplicationServices -framework Security \
  "$SOURCE" -o "$PENDING"
/usr/bin/codesign --force --sign - --timestamp=none \
  --identifier io.nisavid.chatgpt-native-gui-probe "$PENDING"
/usr/bin/codesign --verify --strict "$PENDING"
chmod 500 "$PENDING"
/bin/mv -f "$PENDING" "$OUTPUT"
printf 'artifact=%s\n' "$OUTPUT"
printf 'sha256=%s\n' "$(/usr/bin/shasum -a 256 "$OUTPUT" | /usr/bin/awk '{print $1}')"
