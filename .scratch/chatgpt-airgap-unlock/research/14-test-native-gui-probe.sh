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
"$HERE/14-build-native-gui-probe.sh" "$BUILD_ROOT/build-second"
PROBE_SECOND="$BUILD_ROOT/build-second/chatgpt-native-gui-probe"
probe_sha256="$(/usr/bin/shasum -a 256 "$PROBE" | /usr/bin/awk '{print $1}')"
probe_second_sha256="$(/usr/bin/shasum -a 256 "$PROBE_SECOND" | /usr/bin/awk '{print $1}')"
test "$probe_sha256" = "$probe_second_sha256"
"$PROBE" --self-test
/usr/bin/python3 "$HERE/14-project-state.py" --self-test
/bin/sh -n "$HERE/08-run-prototype.sh"
/bin/sh -n "$HERE/14-build-native-gui-probe.sh"
/bin/sh -n "$HERE/14-test-native-gui-probe.sh"
"$HERE/08-run-prototype.sh" --self-test
"$NODE" "$HERE/12-cdp-gui-driver.mjs" --self-test

if "$PROBE" 2>"$BUILD_ROOT/options.stderr"; then
  echo 'optionless helper invocation unexpectedly passed' >&2
  exit 1
fi
test "$(/bin/cat "$BUILD_ROOT/options.stderr")" = \
  'native-gui-probe: missing required option: --pid'

mkdir -p "$BUILD_ROOT/nonregular-output/chatgpt-native-gui-probe"
if "$HERE/14-build-native-gui-probe.sh" \
  "$BUILD_ROOT/nonregular-output" 2>"$BUILD_ROOT/nonregular-output.stderr"; then
  echo 'non-regular build artifact path unexpectedly passed' >&2
  exit 1
fi
/usr/bin/grep -Fq 'refusing non-regular artifact path' \
  "$BUILD_ROOT/nonregular-output.stderr"

if /usr/bin/grep -Eq 'AXUIElementCreateSystemWide|AXIsProcessTrustedWithOptions|NSWorkspace|NSTask|NSAppleScript|NSClassFromString|dlopen|dlsym|objc_msgSend|osascript|tccutil|System Events|CGEventPost\(|\.post\(|(^|[^[:alnum:]_])Process\(|posix_spawn|exec[lv]|system\(|(^|[^[:alnum:]_])kill\(|terminate\(' \
  "$HERE/14-native-gui-probe.swift"; then
  echo 'forbidden global-event, prompting, launching, termination, AppleScript, or TCC API present' >&2
  exit 1
fi
test "$(/usr/bin/grep -Fc '/Applications/ChatGPT.app' "$HERE/14-native-gui-probe.swift")" -eq 1
/usr/bin/grep -Fq 'static let installedApp = "/Applications/ChatGPT.app"' \
  "$HERE/14-native-gui-probe.swift"
if /usr/bin/grep -Eq 'AXUIElementCreateSystemWide|AXIsProcessTrustedWithOptions|NSAppleScript|osascript|tccutil|System Events|/Applications/ChatGPT\.app|/usr/bin/open|open -a' \
  "$HERE/08-run-prototype.sh" "$HERE/14-build-native-gui-probe.sh"; then
  echo 'forbidden installed-app, global-AX, AppleScript, prompting, or TCC shell seam present' >&2
  exit 1
fi
if /usr/bin/grep -Eq 'child_process|execFile|spawn\(|process\.binding|process\.mainModule|window\.require|Deno\.|Bun\.|IOHID|CGEvent|NSAppleScript|osascript|tccutil|System Events' \
  "$HERE/12-cdp-gui-driver.mjs"; then
  echo 'forbidden process, global-input, AppleScript, or TCC renderer path present' >&2
  exit 1
fi
if /usr/bin/grep -Eq 'subprocess|os\.system|os\.kill|posix_spawn|exec[lv]|NSAppleScript|osascript|tccutil' \
  "$HERE/14-project-state.py"; then
  echo 'forbidden process or host-mutation project-state path present' >&2
  exit 1
fi
/usr/bin/grep -Fq 'test "$(/usr/bin/stat -f '\''%d:%i'\'' "$NATIVE_GUI_PROBE_BIN")" = "$native_gui_probe_device_inode"' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq '"transitionValidated": true' "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq 'PATH="/usr/bin:/bin:/usr/sbin:/sbin"' \
  "$HERE/14-build-native-gui-probe.sh"
/usr/bin/grep -Fq '(deny file-write* (literal (param "NATIVE_GUI_PROBE_BIN")))' \
  "$HERE/08-probe.sb"
/usr/bin/grep -Fq 'NATIVE_GUI_PROBE_PROTECTED_PATH="$RUN_ROOT/.native-gui-probe-disabled"' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq 'test ! -e "$NATIVE_GUI_PROBE_PROTECTED_PATH"' \
  "$HERE/08-run-prototype.sh"
if /usr/bin/grep -Fq 'NATIVE_GUI_PROBE_PROTECTED_PATH="/dev/null"' \
  "$HERE/08-run-prototype.sh"; then
  echo 'non-native sandbox still protects a shared device path' >&2
  exit 1
fi
/usr/bin/grep -Fq -- '-D "NATIVE_GUI_PROBE_BIN=$NATIVE_GUI_PROBE_PROTECTED_PATH"' \
  "$HERE/08-run-prototype.sh"

readiness_body="$(/usr/bin/sed -n \
  '/BEGIN_READ_ONLY_OPEN_PANEL_WAIT/,/END_READ_ONLY_OPEN_PANEL_WAIT/p' \
  "$HERE/14-native-gui-probe.swift")"
if printf '%s\n' "$readiness_body" | /usr/bin/grep -Eq \
  'AXUIElementPerformAction|AXUIElementSetAttributeValue|CGEvent|postCommandShiftG|(^|[^[:alnum:]_])press\('; then
  echo 'Open panel readiness wait contains an AX or input action' >&2
  exit 1
fi
picker_request_body="$(/usr/bin/sed -n \
  '/BEGIN_NATIVE_PROJECT_PICKER_REQUEST/,/END_NATIVE_PROJECT_PICKER_REQUEST/p' \
  "$HERE/12-cdp-gui-driver.mjs")"
trusted_accelerator_body="$(/usr/bin/sed -n \
  '/BEGIN_TRUSTED_RENDERER_ACCELERATOR/,/END_TRUSTED_RENDERER_ACCELERATOR/p' \
  "$HERE/12-cdp-gui-driver.mjs")"
test "$(printf '%s\n' "$trusted_accelerator_body" | /usr/bin/grep -Fc 'type: "rawKeyDown"')" -eq 1
test "$(printf '%s\n' "$trusted_accelerator_body" | /usr/bin/grep -Fc 'type: "keyUp"')" -eq 1
test "$(printf '%s\n' "$trusted_accelerator_body" | /usr/bin/grep -Fc 'modifiers: 4')" -eq 2
test "$(printf '%s\n' "$trusted_accelerator_body" | /usr/bin/grep -Fc 'code: "KeyO"')" -eq 2
test "$(printf '%s\n' "$trusted_accelerator_body" | /usr/bin/grep -Fc 'key: "o"')" -eq 2
test "$(printf '%s\n' "$picker_request_body" | /usr/bin/grep -Fc \
  'await requestNativeProjectPickerFromPrecondition(expectedFixtureRoot, chooseProject);')" -eq 1
choose_project_line="$(printf '%s\n' "$picker_request_body" | \
  /usr/bin/grep -nF 'accessibleName: "Choose project"' | /usr/bin/awk -F: '{print $1}')"
picker_request_line="$(printf '%s\n' "$picker_request_body" | \
  /usr/bin/grep -nF \
  'await requestNativeProjectPickerFromPrecondition(expectedFixtureRoot, chooseProject);' | \
  /usr/bin/awk -F: '{print $1}')"
test -n "$choose_project_line"
test -n "$picker_request_line"
test "$choose_project_line" -lt "$picker_request_line"
/usr/bin/grep -Fq 'preconditionAccessibleName: "Choose project"' \
  "$HERE/12-cdp-gui-driver.mjs"
if printf '%s\n' "$picker_request_body" | /usr/bin/grep -Eq \
  'exactText: "(New project|Use an existing folder)"|dispatchTrustedClick'; then
  echo 'renderer project-picker request still depends on a feature-gated nested menu' >&2
  exit 1
fi
if printf '%s\n' "$picker_request_body" | /usr/bin/grep -Eq 'sleep\(|setTimeout\('; then
  echo 'renderer project-picker request still relies on a fixed sleep' >&2
  exit 1
fi
if printf '%s\n' "$picker_request_body" | /usr/bin/grep -Fq '.click('; then
  echo 'renderer project-picker request uses an untrusted DOM click' >&2
  exit 1
fi
test "$(/usr/bin/grep -Fc '"$NATIVE_GUI_PROBE_BIN" "$@" \' \
  "$HERE/08-run-prototype.sh")" -eq 1

sensitive_symbols="$(/usr/bin/nm -u "$PROBE" | /usr/bin/awk '{print $NF}' | \
  /usr/bin/grep -E '(^_(AX|CGEvent|IOHID|NSAppleScript|LS(Open|Launch)|posix_spawn|exec|fork|system|kill|Sec(Code|StaticCode)|proc_))|NSTask|NSWorkspace' | \
  LC_ALL=C /usr/bin/sort || true)"
expected_sensitive_symbols='_AXIsProcessTrusted
_AXUIElementCopyActionNames
_AXUIElementCopyAttributeValue
_AXUIElementCreateApplication
_AXUIElementGetTypeID
_AXUIElementPerformAction
_AXUIElementSetAttributeValue
_CGEventCreateKeyboardEvent
_CGEventPostToPid
_CGEventSetFlags
_CGEventSourceCreate
_SecCodeCheckValidity
_SecCodeCopyGuestWithAttributes
_SecCodeCopySigningInformation
_SecCodeCopyStaticCode
_SecStaticCodeCheckValidity
_SecStaticCodeCreateWithPath
_proc_pidinfo
_proc_pidpath'
test "$sensitive_symbols" = "$expected_sensitive_symbols"

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
