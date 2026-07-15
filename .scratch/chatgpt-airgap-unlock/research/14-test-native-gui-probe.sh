#!/bin/sh
# Deterministic no-permission tests. This script never launches or targets an app.
set -eu

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
NODE="${NODE:-$(command -v node || true)}"
if test -z "$NODE" || test ! -x "$NODE"; then
  echo 'node executable not found; set NODE or add node to PATH' >&2
  exit 1
fi
BUILD_ROOT="$(mktemp -d /private/tmp/chatgpt-native-gui-probe-test.XXXXXX)"
RUN_ROOT=""
cleanup() {
  /bin/rm -rf "$BUILD_ROOT"
  if test -n "$RUN_ROOT"; then /bin/rm -rf "$RUN_ROOT"; fi
}
trap cleanup EXIT INT TERM

"$HERE/14-build-native-gui-probe.sh" "$BUILD_ROOT/build"
PROBE="$BUILD_ROOT/build/chatgpt-native-gui-probe"
"$PROBE" --self-test
/bin/sh -n "$HERE/08-run-prototype.sh"
"$NODE" "$HERE/12-cdp-gui-driver.mjs" --self-test

if /usr/bin/grep -Eq 'AXUIElementCreateSystemWide|AXIsProcessTrustedWithOptions|NSWorkspace|tccutil|System Events' \
  "$HERE/14-native-gui-probe.swift"; then
  echo 'forbidden system-wide, prompting, launching, or TCC API present' >&2
  exit 1
fi

RUN_ROOT="$(mktemp -d /private/tmp/chatgpt-route-prototype-08.test.XXXXXX)"
mkdir -p "$RUN_ROOT/Probe.app/Contents/MacOS" "$RUN_ROOT/workspace/.git" "$RUN_ROOT/logs"
/usr/bin/ditto "$PROBE" "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT"
chmod 500 "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT"
"$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase inspect-project-picker \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --validate-inputs-only
/usr/bin/grep -Fq '"kind":"inputs-validated"' "$RUN_ROOT/logs/native-gui-probe.jsonl"

if "$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle /Applications/ChatGPT.app \
  --expected-executable /Applications/ChatGPT.app/Contents/MacOS/ChatGPT \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase inspect-project-picker \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --validate-inputs-only 2>"$BUILD_ROOT/installed.stderr"; then
  echo 'installed app unexpectedly passed validation' >&2
  exit 1
fi
/usr/bin/grep -Eq 'forbidden|beneath the run root' "$BUILD_ROOT/installed.stderr"

if "$PROBE" \
  --pid 2 \
  --run-root /private/tmp \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase inspect-project-picker \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --validate-inputs-only 2>"$BUILD_ROOT/root.stderr"; then
  echo 'broad run root unexpectedly passed validation' >&2
  exit 1
fi
/usr/bin/grep -Fq 'not an owned ticket-08 disposable root' "$BUILD_ROOT/root.stderr"

echo 'native GUI probe no-permission tests passed'
