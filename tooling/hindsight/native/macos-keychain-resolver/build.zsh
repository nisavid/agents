#!/bin/zsh
set -euo pipefail

[[ $# -eq 1 && "$1" == /* ]] || {
  print -u2 -- "usage: build.zsh /absolute/output/path"
  exit 64
}

root="${0:A:h}"
output="$1"
temporary="$(/usr/bin/mktemp -d)"
trap '/bin/rm -rf -- "$temporary"' EXIT
sdk_name=macosx
if [[ -n "${HINDSIGHT_MACOS_SDK_VERSION:-}" ]]; then
  [[ "$HINDSIGHT_MACOS_SDK_VERSION" =~ '^[0-9]+([.][0-9]+)*$' ]] || {
    print -u2 -- "HINDSIGHT_MACOS_SDK_VERSION is invalid"
    exit 64
  }
  sdk_name="macosx${HINDSIGHT_MACOS_SDK_VERSION}"
fi
sdk="$(/usr/bin/xcrun --sdk "$sdk_name" --show-sdk-path)"

for architecture in arm64 x86_64; do
  /usr/bin/xcrun --sdk "$sdk_name" clang \
    -arch "$architecture" \
    -isysroot "$sdk" \
    -mmacosx-version-min=13.0 \
    -Os \
    -fobjc-arc \
    -fno-ident \
    -Wl,-reproducible \
    -Wall \
    -Wextra \
    -Werror \
    -Wno-deprecated-declarations \
    -framework Foundation \
    -framework Security \
    "$root/main.m" \
    -o "$temporary/resolver-$architecture"
done

/usr/bin/lipo -create \
  "$temporary/resolver-arm64" \
  "$temporary/resolver-x86_64" \
  -output "$temporary/resolver"
/usr/bin/codesign \
  --force \
  --sign - \
  --timestamp=none \
  "$temporary/resolver"
/usr/bin/install -m 500 "$temporary/resolver" "$output"
