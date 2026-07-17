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
VALIDATION_ROOT=""
cleanup() {
  /bin/rm -rf "$BUILD_ROOT"
  if test -n "$VALIDATION_ROOT"; then /bin/rm -rf "$VALIDATION_ROOT"; fi
}
trap cleanup EXIT INT TERM

"$HERE/14-build-native-gui-probe.sh" "$BUILD_ROOT/build"
"$HERE/14-build-native-gui-probe.sh" "$BUILD_ROOT/build-second"
PROBE="$BUILD_ROOT/build/chatgpt-native-gui-probe"
PROBE_SECOND="$BUILD_ROOT/build-second/chatgpt-native-gui-probe"
test "$(/usr/bin/shasum -a 256 "$PROBE" | /usr/bin/awk '{print $1}')" = \
  "$(/usr/bin/shasum -a 256 "$PROBE_SECOND" | /usr/bin/awk '{print $1}')"
"$PROBE" --self-test
"$NODE" "$HERE/14-patch-native-picker-default.mjs" --self-test
/usr/bin/python3 "$HERE/14-project-state.py" --self-test
/bin/sh -n "$HERE/08-run-prototype.sh"
/bin/sh -n "$HERE/14-build-native-gui-probe.sh"
/bin/sh -n "$HERE/14-test-native-gui-probe.sh"
"$HERE/08-run-prototype.sh" --self-test
/usr/bin/python3 "$HERE/08-appserver-probe.py" --self-test
/usr/bin/python3 "$HERE/08-appserver-restart-probe.py" --self-test
"$NODE" "$HERE/12-cdp-gui-driver.mjs" --self-test

if "$PROBE" 2>"$BUILD_ROOT/options.stderr"; then
  echo 'optionless helper invocation unexpectedly passed' >&2
  exit 1
fi
test "$(/bin/cat "$BUILD_ROOT/options.stderr")" = \
  'native-gui-probe: missing required option: --pid'

VALIDATION_ROOT="$(mktemp -d /private/tmp/chatgpt-route-prototype-08.XXXXXX)"
mkdir -p "$VALIDATION_ROOT/Test.app/Contents/MacOS" \
  "$VALIDATION_ROOT/project/.git" "$VALIDATION_ROOT/logs"
: >"$VALIDATION_ROOT/Test.app/Contents/MacOS/ChatGPT"
chmod +x "$VALIDATION_ROOT/Test.app/Contents/MacOS/ChatGPT"
"$PROBE" \
  --pid 42 \
  --run-root "$VALIDATION_ROOT" \
  --expected-bundle "$VALIDATION_ROOT/Test.app" \
  --expected-executable "$VALIDATION_ROOT/Test.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$VALIDATION_ROOT/project" \
  --phase select-project \
  --event-log "$VALIDATION_ROOT/logs/native-gui-probe.jsonl" \
  --accept-renderer-project-picker-request \
  --validate-inputs-only
if "$PROBE" \
  --pid 42 \
  --run-root "$VALIDATION_ROOT" \
  --expected-bundle /Applications/ChatGPT.app \
  --expected-executable /Applications/ChatGPT.app/Contents/MacOS/ChatGPT \
  --fixture-root "$VALIDATION_ROOT/project" \
  --phase select-project \
  --event-log "$VALIDATION_ROOT/logs/native-gui-probe.jsonl" \
  --accept-renderer-project-picker-request \
  --validate-inputs-only 2>"$BUILD_ROOT/installed-app.stderr"; then
  echo 'installed ChatGPT.app path unexpectedly passed validation' >&2
  exit 1
fi
/usr/bin/grep -Fq 'installed ChatGPT.app is forbidden' \
  "$BUILD_ROOT/installed-app.stderr"
if "$PROBE" \
  --pid 42 \
  --run-root /private/tmp \
  --expected-bundle "$VALIDATION_ROOT/Test.app" \
  --expected-executable "$VALIDATION_ROOT/Test.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$VALIDATION_ROOT/project" \
  --phase select-project \
  --event-log "$VALIDATION_ROOT/logs/native-gui-probe.jsonl" \
  --accept-renderer-project-picker-request \
  --validate-inputs-only 2>"$BUILD_ROOT/broad-root.stderr"; then
  echo 'broad /private/tmp run root unexpectedly passed validation' >&2
  exit 1
fi
/usr/bin/grep -Fq 'run root is not an owned ticket-08 disposable root' \
  "$BUILD_ROOT/broad-root.stderr"
if "$PROBE" \
  --pid 42 \
  --run-root "$VALIDATION_ROOT" \
  --expected-bundle "$VALIDATION_ROOT/Test.app" \
  --expected-executable /bin/sh \
  --fixture-root "$VALIDATION_ROOT/project" \
  --phase select-project \
  --event-log "$VALIDATION_ROOT/logs/native-gui-probe.jsonl" \
  --accept-renderer-project-picker-request \
  --validate-inputs-only 2>"$BUILD_ROOT/out-of-root.stderr"; then
  echo 'out-of-root executable unexpectedly passed validation' >&2
  exit 1
fi
/usr/bin/grep -Fq 'must be beneath the run root' \
  "$BUILD_ROOT/out-of-root.stderr"
/bin/rm -rf "$VALIDATION_ROOT"
VALIDATION_ROOT=""

mkdir -p "$BUILD_ROOT/nonregular-output/chatgpt-native-gui-probe"
if "$HERE/14-build-native-gui-probe.sh" \
  "$BUILD_ROOT/nonregular-output" 2>"$BUILD_ROOT/nonregular-output.stderr"; then
  echo 'non-regular build artifact path unexpectedly passed' >&2
  exit 1
fi
/usr/bin/grep -Fq 'refusing non-regular artifact path' \
  "$BUILD_ROOT/nonregular-output.stderr"

if /usr/bin/grep -Eq 'AXUIElementCreateSystemWide|AXIsProcessTrustedWithOptions|NSWorkspace|NSTask|NSAppleScript|NSPasteboard|NSClassFromString|dlopen|dlsym|objc_msgSend|osascript|tccutil|System Events|CGEvent|postToPid|(^|[^[:alnum:]_])Process\(|posix_spawn|exec[lv]|system\(|(^|[^[:alnum:]_])kill\(|terminate\(' \
  "$HERE/14-native-gui-probe.swift"; then
  echo 'forbidden global input, prompting, launching, termination, AppleScript, or TCC API present' >&2
  exit 1
fi
test "$(/usr/bin/grep -Fc '/Applications/ChatGPT.app' \
  "$HERE/14-native-gui-probe.swift")" -eq 1
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

test "$(/usr/bin/grep -Fc -- '--use-mock-keychain' \
  "$HERE/08-run-prototype.sh")" -eq 1
/usr/bin/grep -Fq 'exec "$NATIVE_GUI_PROBE_BIN" "$@"' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq 'native GUI helper exited after absence attestation' \
  "$HERE/08-run-prototype.sh"
cleanup_body="$(/usr/bin/sed -n '/^cleanup() {$/,/^}$/p' \
  "$HERE/08-run-prototype.sh")"
test "$(printf '%s\n' "$cleanup_body" | \
  /usr/bin/grep -Fc 'while test "$i" -lt 50')" -eq 1
test "$(printf '%s\n' "$cleanup_body" | \
  /usr/bin/grep -Fc '/bin/kill -KILL "$native_gui_probe_pid"')" -eq 1
/usr/bin/grep -Fq 'NDP="$NATIVE_PICKER_DEFAULT_PATH"' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq 'ElectronAsarIntegrity:Resources/app.asar:hash' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq '/usr/bin/codesign --force --sign - --identifier com.openai.codex "$APP"' \
  "$HERE/08-run-prototype.sh"
for required_negative_assertion in \
  'unpatched copied app unexpectedly has an ad-hoc outer signature' \
  'picker preparation phase pressed the final renderer control'; do
  /usr/bin/grep -Fq "$required_negative_assertion" "$HERE/08-run-prototype.sh"
done

test "$(/usr/bin/grep -Fc 'BEGIN_READ_ONLY_OPEN_PANEL_WAIT' \
  "$HERE/14-native-gui-probe.swift")" -eq 1
test "$(/usr/bin/grep -Fc 'END_READ_ONLY_OPEN_PANEL_WAIT' \
  "$HERE/14-native-gui-probe.swift")" -eq 1
readiness_body="$(/usr/bin/sed -n \
  '/BEGIN_READ_ONLY_OPEN_PANEL_WAIT/,/END_READ_ONLY_OPEN_PANEL_WAIT/p' \
  "$HERE/14-native-gui-probe.swift")"
test -n "$(printf '%s\n' "$readiness_body" | \
  /usr/bin/sed '/BEGIN_READ_ONLY_OPEN_PANEL_WAIT/d; /END_READ_ONLY_OPEN_PANEL_WAIT/d; /^[[:space:]]*$/d')"
if printf '%s\n' "$readiness_body" | /usr/bin/grep -Eq \
  'AXUIElementPerformAction|AXUIElementSetAttributeValue|CGEvent|(^|[^[:alnum:]_])press\('; then
  echo 'Open panel readiness wait contains an input action' >&2
  exit 1
fi
printf '%s\n' "$readiness_body" | /usr/bin/grep -Fq \
  'let openPanelWaitTimeoutNanoseconds: UInt64 = 60_000_000_000'
test "$(/usr/bin/grep -Fc 'BEGIN_PID_OPEN_PANEL_LIST_SELECTION' \
  "$HERE/14-native-gui-probe.swift")" -eq 1
test "$(/usr/bin/grep -Fc 'END_PID_OPEN_PANEL_LIST_SELECTION' \
  "$HERE/14-native-gui-probe.swift")" -eq 1
list_selection_body="$(/usr/bin/sed -n \
  '/BEGIN_PID_OPEN_PANEL_LIST_SELECTION/,/END_PID_OPEN_PANEL_LIST_SELECTION/p' \
  "$HERE/14-native-gui-probe.swift")"
test -n "$(printf '%s\n' "$list_selection_body" | \
  /usr/bin/sed '/BEGIN_PID_OPEN_PANEL_LIST_SELECTION/d; /END_PID_OPEN_PANEL_LIST_SELECTION/d; /^[[:space:]]*$/d')"
test "$(printf '%s\n' "$list_selection_body" | \
  /usr/bin/grep -Fc 'AXUIElementSetAttributeValue(')" -eq 1
test "$(printf '%s\n' "$list_selection_body" | \
  /usr/bin/grep -Fc 'try requireSameProcess(process)')" -ge 4
for contract in kAXBrowserRole ColumnView kAXListRole kAXGroupRole \
  kAXURLAttribute kAXSelectedChildrenAttribute nonSymlinkDirectoryIdentity \
  revalidateOpenPanelListSelectionToken performValidatedOpenPanelListSelectionSet \
  performValidatedOpenPanelListChooserPress requireMutationIdentity \
  validateCodeIdentity; do
  printf '%s\n' "$list_selection_body" | /usr/bin/grep -Fq "$contract"
done
mutation_identity_body="$(/usr/bin/sed -n \
  '/^func requireMutationIdentity(/,/^}/p' \
  "$HERE/14-native-gui-probe.swift")"
printf '%s\n' "$mutation_identity_body" | /usr/bin/awk '
  /try requireSameProcess\(process\)/ { pid += 1; if (pid == 1) first_pid = NR; else second_pid = NR }
  /try validateCodeIdentity\(pid: process.pid, paths: paths\)/ { code_identity = NR }
  /try requireValidatedFixtureIdentity\(paths\)/ { fixture_identity = NR }
  END { exit !(pid == 2 && first_pid < code_identity && code_identity < second_pid && second_pid < fixture_identity) }
'
if printf '%s\n' "$list_selection_body" | /usr/bin/grep -Eq \
  'CGEvent|postToPid|kAXFocused(Window|UIElement)Attribute|NSWorkspace'; then
  echo 'Open panel selection contains keyboard, focus, or broad app targeting' >&2
  exit 1
fi

picker_request_body="$(/usr/bin/sed -n \
  '/BEGIN_NATIVE_PROJECT_PICKER_REQUEST/,/END_NATIVE_PROJECT_PICKER_REQUEST/p' \
  "$HERE/12-cdp-gui-driver.mjs")"
test "$(printf '%s\n' "$picker_request_body" | \
  /usr/bin/grep -Fc 'pressUniqueVisibleExactControl(')" -eq 3
test "$(printf '%s\n' "$picker_request_body" | /usr/bin/grep -Fc '.click()')" -eq 0
printf '%s\n' "$picker_request_body" | /usr/bin/grep -Fq \
  'selector: '\''[role="menuitem"]'\'', exactText: "New project"'
printf '%s\n' "$picker_request_body" | /usr/bin/grep -Fq \
  'selector: '\''[role="menuitem"]'\'', exactText: "Use an existing folder"'
trusted_mouse_body="$(/usr/bin/sed -n \
  '/^async function pressUniqueVisibleExactControl(/,/^}/p' \
  "$HERE/12-cdp-gui-driver.mjs")"
test "$(printf '%s\n' "$trusted_mouse_body" | \
  /usr/bin/grep -Fc 'Input.dispatchMouseEvent')" -eq 2

for removed in permit-go-to-folder-menu-fallback press-open-folder-menu-item \
  focus-open-panel hidden-path-entry go-to-folder-shortcut; do
  if /usr/bin/grep -Fq "$removed" "$HERE/14-native-gui-probe.swift"; then
    echo "removed interaction path remains in helper: $removed" >&2
    exit 1
  fi
done

/usr/bin/python3 - "$HERE/evidence/12-native-project-2026-07-16" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
index = json.loads((root / "index.json").read_text(encoding="utf-8"))
expected_files = {
    "native-project-baseline.json",
    "native-project-state.json",
    "native-gui-probe.jsonl",
    "probe-verdict.txt",
    "renderer-project-selection-confirmed.json",
    "runtime-manifest.txt",
}
assert index["schema"] == 2
assert set(index["outputs"]) == expected_files
assert index["cohort"]["nativeHelperSha256"] == (
    "a4365c91bbc160045f4cf31bfc679c5d46adfc19d4b6271a2c79bb354fd0dff5"
)
assert index["cohort"]["sourceRunRootSha256"] == hashlib.sha256(
    b"/private/tmp/chatgpt-route-prototype-08.B9nPyv"
).hexdigest()
assert {path.name for path in root.iterdir()} == expected_files | {"index.json"}
for name in expected_files:
    value = (root / name).read_bytes()
    assert index["outputs"][name] == hashlib.sha256(value).hexdigest()

baseline = json.loads((root / "native-project-baseline.json").read_text())
state = json.loads((root / "native-project-state.json").read_text())
assert baseline["schema"] == state["schema"] == 2
for key in ("fixtureSha256", "databasePathSha256", "databaseFileIdentitySha256"):
    assert baseline[key] == state[key]
assert baseline["exactThreadCount"] == 0
assert state["exactThreadCountBefore"] == 0
assert state["exactThreadCountAfter"] == 1
assert state["transitionValidated"] is True

confirmation = json.loads(
    (root / "renderer-project-selection-confirmed.json").read_text())
assert set(confirmation) == {
    "expectedFixtureSha256", "kind", "matched", "phase", "uniqueControl"
}
assert confirmation["expectedFixtureSha256"] == baseline["fixtureSha256"]
assert confirmation["matched"] is True and confirmation["uniqueControl"] is True

allowed = {
    "inputs-validated": {"bundleSha256", "fixtureSha256", "kind", "phase", "rendererProjectPickerRequestAuthorized", "runRootSha256", "schema"},
    "process-validated": {"appKitRegistrationPollCount", "executableSha256", "kind", "schema"},
    "open-panel-absence-validated": {"fixtureSha256", "kind", "schema", "visitedCount", "windowCount"},
    "open-panel-validated": {"chooserTitle", "kind", "navigation", "pollCount", "schema", "windowCount"},
    "project-selection-requested": {"fixtureSha256", "kind", "navigation", "readinessPollCount", "schema", "selectedPollCount", "selectionActionCount"},
}
events = [json.loads(line) for line in (root / "native-gui-probe.jsonl").read_text().splitlines()]
assert [event["kind"] for event in events] == list(allowed)
for event in events:
    assert set(event) == allowed[event["kind"]]
    assert event["schema"] == 1
assert events[0]["fixtureSha256"] == baseline["fixtureSha256"]
assert events[-1]["fixtureSha256"] == baseline["fixtureSha256"]

sensitive_patterns = (
    re.compile(r"\bbearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-[a-z0-9_.-]{8,}", re.IGNORECASE),
    re.compile(
        r"(?<![a-z0-9])(?:--?)?(?:[a-z0-9]+[-_])*"
        r"(?:authorization|credential|password|secret|token|api[-_]?key|"
        r"access[-_]?token|client[-_]?secret)"
        r"(?:[\"']?\s*[:=]\s*|[ \t]+)[\"']?[^\s\"']+",
        re.IGNORECASE,
    ),
    re.compile(r"https?://", re.IGNORECASE),
)
for sample in (
    "Authorization: Bearer retained-secret",
    "sk-proj-retained_secret",
    "https://remote.example.invalid/v1",
    "--token retained-secret",
    "--api-key retained-secret",
    "accessToken=retained-secret",
    "clientSecret=retained-secret",
    "Authorization: Basic retained-secret",
    "token: retained-secret",
    '{"token":"retained-secret"}',
    "OPENAI_API_KEY=retained-secret",
    "GH_TOKEN=retained-secret",
    "X-API-Key: retained-secret",
    "Proxy-Authorization: Basic retained-secret",
):
    assert any(pattern.search(sample) for pattern in sensitive_patterns), sample

for path in root.iterdir():
    assert not path.is_symlink() and path.is_file()
    text = path.read_text(encoding="utf-8")
    assert not any(pattern.search(text) for pattern in sensitive_patterns)
PY

printf 'native GUI probe deterministic tests passed\n'
