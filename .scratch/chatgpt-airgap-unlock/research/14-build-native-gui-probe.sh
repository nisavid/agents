#!/bin/sh
# Build the separately named, ad-hoc-signed Ticket 14 prototype artifact.
set -eu
PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$HERE/14-native-gui-probe.swift"
umask 077
if test "$#" -eq 0; then
  OUTPUT_DIR="$(mktemp -d /private/tmp/chatgpt-native-gui-probe-build.XXXXXX)"
elif test "$#" -eq 1; then
  OUTPUT_DIR="$1"
  test "${OUTPUT_DIR#/}" != "$OUTPUT_DIR"
  test ! -L "$OUTPUT_DIR"
  mkdir -p "$OUTPUT_DIR"
  output_owner_mode="$(/usr/bin/stat -f '%u %OLp' "$OUTPUT_DIR")"
  output_owner="${output_owner_mode%% *}"
  output_mode="${output_owner_mode#* }"
  test "$output_owner" -eq "$(/usr/bin/id -u)"
  test $((0$output_mode & 0022)) -eq 0
else
  echo 'usage: 14-build-native-gui-probe.sh [ABSOLUTE_OUTPUT_DIRECTORY]' >&2
  exit 64
fi
OUTPUT="$OUTPUT_DIR/chatgpt-native-gui-probe"
PENDING="$OUTPUT_DIR/.chatgpt-native-gui-probe.pending"

mkdir -p "$OUTPUT_DIR/module-cache"
if test -L "$OUTPUT" || test -d "$OUTPUT"; then
  echo "refusing non-regular artifact path: $OUTPUT" >&2
  exit 65
fi
if test -e "$OUTPUT"; then
  test -f "$OUTPUT"
  test "$(/usr/bin/stat -f '%u' "$OUTPUT")" -eq "$(/usr/bin/id -u)"
fi
if test -e "$PENDING" || test -L "$PENDING"; then
  echo "refusing pre-existing pending artifact: $PENDING" >&2
  exit 65
fi
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
test ! -L "$OUTPUT"
test ! -d "$OUTPUT"
/bin/mv -f "$PENDING" "$OUTPUT"
test -f "$OUTPUT"
/usr/bin/codesign --verify --strict "$OUTPUT"
printf 'artifact=%s\n' "$OUTPUT"
printf 'sha256=%s\n' "$(/usr/bin/shasum -a 256 "$OUTPUT" | /usr/bin/awk '{print $1}')"
