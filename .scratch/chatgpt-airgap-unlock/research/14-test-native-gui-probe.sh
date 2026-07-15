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
/usr/bin/python3 "$HERE/08-appserver-probe.py" --self-test
/usr/bin/python3 "$HERE/08-appserver-restart-probe.py" --self-test
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
host_probe_invocations="$(/usr/bin/sed -n \
  '/if test "$GUI_WORKFLOW" = false; then/,/^fi$/p' \
  "$HERE/08-run-prototype.sh")"
test "$(printf '%s\n' "$host_probe_invocations" | \
  /usr/bin/grep -Fc '"$NATIVE_GUI_PROBE_PROTECTED_PATH"')" -eq 2

readiness_body="$(/usr/bin/sed -n \
  '/BEGIN_READ_ONLY_OPEN_PANEL_WAIT/,/END_READ_ONLY_OPEN_PANEL_WAIT/p' \
  "$HERE/14-native-gui-probe.swift")"
if printf '%s\n' "$readiness_body" | /usr/bin/grep -Eq \
  'AXUIElementPerformAction|AXUIElementSetAttributeValue|CGEvent|postCommandShiftG|(^|[^[:alnum:]_])press\('; then
  echo 'Open panel readiness wait contains an AX or input action' >&2
  exit 1
fi
process_validation_body="$(/usr/bin/sed -n \
  '/BEGIN_PROCESS_REGISTRATION_VALIDATION/,/END_PROCESS_REGISTRATION_VALIDATION/p' \
  "$HERE/14-native-gui-probe.swift")"
process_validation_file="$BUILD_ROOT/process-validation-body.txt"
printf '%s\n' "$process_validation_body" >"$process_validation_file"
test "$(printf '%s\n' "$process_validation_body" | \
  /usr/bin/grep -Fc 'try validateIdentity()')" -eq 4
for required_process_gate in SecStaticCodeCheckValidity SecCodeCopyGuestWithAttributes \
  SecCodeCheckValidity SecCodeCopyStaticCode 'staticIdentifier == dynamicIdentifier' \
  'staticUnique == dynamicUnique' 'timeoutNanoseconds: 5_000_000_000' \
  'pollMicroseconds: 100_000'; do
  /usr/bin/grep -Fq "$required_process_gate" "$process_validation_file"
done
if printf '%s\n' "$process_validation_body" | /usr/bin/grep -Eq \
  'AXUIElement|CGEvent|postToPid|pressOpenFolderMenuItem'; then
  echo 'process registration validation contains an AX or input action' >&2
  exit 1
fi
execute_body="$(/usr/bin/sed -n '/^func execute(/,/^func testElement/p' \
  "$HERE/14-native-gui-probe.swift")"
process_verified_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'let verification = try verifyProcess(' | /usr/bin/awk -F: '{print $1}')"
trust_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'guard AXIsProcessTrusted() else {' | /usr/bin/awk -F: '{print $1}')"
application_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'let application = AXUIElementCreateApplication(options.pid)' | \
  /usr/bin/awk -F: '{print $1}')"
menu_action_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'let menuReadiness = try pressOpenFolderMenuItem(' | \
  /usr/bin/awk -F: '{print $1}')"
test "$process_verified_line" -lt "$trust_line"
test "$trust_line" -lt "$application_line"
test "$application_line" -lt "$menu_action_line"
picker_request_body="$(/usr/bin/sed -n \
  '/BEGIN_NATIVE_PROJECT_PICKER_PRECONDITION/,/END_NATIVE_PROJECT_PICKER_PRECONDITION/p' \
  "$HERE/12-cdp-gui-driver.mjs")"
/usr/bin/grep -Fq 'emit("native-project-picker-precondition-ready"' \
  "$HERE/12-cdp-gui-driver.mjs"
/usr/bin/grep -Fq 'preconditionAccessibleName: "Choose project"' \
  "$HERE/12-cdp-gui-driver.mjs"
if printf '%s\n' "$picker_request_body" | /usr/bin/grep -Eq \
  'Input\.dispatch(Key|Mouse)Event|dispatchTrusted|send\(|\.click\('; then
  echo 'renderer project-picker precondition still performs an input action' >&2
  exit 1
fi
if printf '%s\n' "$picker_request_body" | /usr/bin/grep -Eq 'sleep\(|setTimeout\('; then
  echo 'renderer project-picker precondition still relies on a fixed sleep' >&2
  exit 1
fi
if /usr/bin/grep -Fq 'native-project-picker-requested' \
  "$HERE/12-cdp-gui-driver.mjs"; then
  echo 'renderer still claims that it requested the native project picker' >&2
  exit 1
fi

open_folder_menu_body="$(/usr/bin/sed -n \
  '/BEGIN_PID_OPEN_FOLDER_MENU_PRESS/,/END_PID_OPEN_FOLDER_MENU_PRESS/p' \
  "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$open_folder_menu_body" | \
  /usr/bin/grep -Fc 'AXUIElementPerformAction(')" -eq 1
/usr/bin/grep -Fq 'menuItem, kAXPressAction as CFString' <<EOF
$open_folder_menu_body
EOF
menu_press_boundary="$(/usr/bin/sed -n \
  '/^func performValidatedMenuPress(/,/^}/p' \
  "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$menu_press_boundary" | \
  /usr/bin/grep -Fc 'try validateIdentity()')" -eq 3
menu_active_line="$(printf '%s\n' "$menu_press_boundary" | \
  /usr/bin/grep -nF 'try validateActive()' | /usr/bin/awk -F: '{print $1}')"
menu_perform_line="$(printf '%s\n' "$menu_press_boundary" | \
  /usr/bin/grep -nF 'try performPress()' | /usr/bin/awk -F: '{print $1}')"
test "$menu_active_line" -lt "$menu_perform_line"
/usr/bin/grep -Fq 'validateActive: validateActive' <<EOF
$open_folder_menu_body
EOF
for required_contract in kAXMenuBarAttribute kAXMenuBarRole kAXMenuBarItemRole \
  kAXMenuRole kAXMenuItemRole kAXEnabledAttribute kAXPressAction \
  kAXMenuItemCmdCharAttribute kAXMenuItemCmdModifiersAttribute; do
  /usr/bin/grep -Fq "$required_contract" "$HERE/14-native-gui-probe.swift"
done
if printf '%s\n' "$open_folder_menu_body" | /usr/bin/grep -Eq \
  'postToPid|activate|launchApplication|openApplication|NSWorkspace'; then
  echo 'Open Folder menu press has a broader action surface' >&2
  exit 1
fi
open_folder_request_body="$(/usr/bin/sed -n \
  '/BEGIN_PID_OPEN_FOLDER_REQUEST/,/END_PID_OPEN_FOLDER_REQUEST/p' \
  "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$open_folder_request_body" | \
  /usr/bin/grep -Fc 'let menuReadiness = try pressOpenFolderMenuItem(')" -eq 1
open_folder_press_line="$(printf '%s\n' "$open_folder_request_body" | \
  /usr/bin/grep -nF 'let menuReadiness = try pressOpenFolderMenuItem(' | \
  /usr/bin/awk -F: '{print $1}')"
open_folder_event_line="$(printf '%s\n' "$open_folder_request_body" | \
  /usr/bin/grep -nF 'try log.write("open-folder-menu-item-pressed"' | \
  /usr/bin/awk -F: '{print $1}')"
open_panel_wait_line="$(printf '%s\n' "$open_folder_request_body" | \
  /usr/bin/grep -nF 'let readiness = try waitForValidatedOpenPanel(' | \
  /usr/bin/awk -F: '{print $1}')"
test -n "$open_folder_press_line"
test -n "$open_folder_event_line"
test -n "$open_panel_wait_line"
test "$open_folder_press_line" -lt "$open_folder_event_line"
test "$open_folder_event_line" -lt "$open_panel_wait_line"
/usr/bin/grep -Fq 'try log.write("open-folder-menu-item-pressed"' \
  "$HERE/14-native-gui-probe.swift"
/usr/bin/grep -Fq '"actionCount": 1' "$HERE/14-native-gui-probe.swift"
/usr/bin/grep -Fq -- '--press-open-folder-menu-item' "$HERE/14-native-gui-probe.swift"
/usr/bin/grep -Fq -- '--press-open-folder-menu-item' "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq -- '--focus-open-panel' "$HERE/14-native-gui-probe.swift"
/usr/bin/grep -Fq -- '--focus-open-panel' "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq '"kind":"native-project-picker-precondition-ready"' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq '"kind":"open-folder-menu-item-pressed"' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq '"kind":"open-panel-validated"' \
  "$HERE/08-run-prototype.sh"
test "$(/usr/bin/grep -Fc '"$NATIVE_GUI_PROBE_BIN" "$@"' \
  "$HERE/08-run-prototype.sh")" -eq 1
/usr/bin/grep -Fq -- '--phase inspect-open-folder-menu' \
  "$HERE/08-run-prototype.sh"
/usr/bin/grep -Fq '"kind":"open-folder-menu-validated"' \
  "$HERE/08-run-prototype.sh"
menu_inspection_body="$(/usr/bin/sed -n \
  '/if options.phase == .inspectOpenFolderMenu {/,/^    }/p' \
  "$HERE/14-native-gui-probe.swift")"
for forbidden_inspection_action in AXUIElementPerformAction postToPid \
  waitForValidatedOpenPanel AXUIElementSetAttributeValue; do
  if printf '%s\n' "$menu_inspection_body" | /usr/bin/grep -Fq \
    "$forbidden_inspection_action"; then
    echo 'read-only menu inspection contains an action' >&2
    exit 1
  fi
done
/usr/bin/grep -Fq '"actionCount": 0' "$HERE/14-native-gui-probe.swift"
menu_wait_body="$(/usr/bin/sed -n \
  '/BEGIN_READ_ONLY_OPEN_FOLDER_MENU_WAIT/,/END_READ_ONLY_OPEN_FOLDER_MENU_WAIT/p' \
  "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$menu_wait_body" | \
  /usr/bin/grep -Fc 'try validateIdentity()')" -eq 2
if printf '%s\n' "$menu_wait_body" | /usr/bin/grep -Eq \
  'AXUIElementPerformAction|AXUIElementSetAttributeValue|postToPid|sleep\([0-9]'; then
  echo 'read-only menu readiness wait contains an action or fixed sleep' >&2
  exit 1
fi
path_entry_waits="$(/usr/bin/sed -n \
  '/BEGIN_READ_ONLY_PATH_ENTRY_WAITS/,/END_READ_ONLY_PATH_ENTRY_WAITS/p' \
  "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$path_entry_waits" | \
  /usr/bin/grep -Fc 'try validateIdentity()')" -eq 4
if printf '%s\n' "$path_entry_waits" | /usr/bin/grep -Eq \
  'AXUIElementPerformAction|AXUIElementSetAttributeValue|postToPid|Date\(|sleep\([0-9]'; then
  echo 'path-entry readiness wait contains an action, wall clock, or fixed sleep' >&2
  exit 1
fi
if /usr/bin/grep -Fq 'usleep(250_000)' "$HERE/14-native-gui-probe.swift"; then
  echo 'path-entry restoration still relies on a fixed sleep' >&2
  exit 1
fi
generic_press_body="$(/usr/bin/sed -n \
  '/^func press(_ element:/,/^}/p' "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$generic_press_body" | \
  /usr/bin/grep -Fc 'try requireSameProcess(process)')" -eq 2
test "$(printf '%s\n' "$generic_press_body" | \
  /usr/bin/grep -Fc 'AXUIElementPerformAction(element, kAXPressAction as CFString)')" -eq 1
focus_body="$(/usr/bin/sed -n \
  '/BEGIN_PID_OPEN_PANEL_FOCUS/,/END_PID_OPEN_PANEL_FOCUS/p' \
  "$HERE/14-native-gui-probe.swift")"
focus_body_file="$BUILD_ROOT/open-panel-focus-body.txt"
printf '%s\n' "$focus_body" >"$focus_body_file"
for required_focus_contract in kAXFrontmostAttribute kAXFocusedWindowAttribute \
  kAXFocusedUIElementAttribute kAXTopLevelUIElementAttribute AXUIElementGetPid \
  kAXRaiseAction AXUIElementIsAttributeSettable \
  readOpenPanelFocusSnapshot waitForOpenPanelFocus; do
  /usr/bin/grep -Fq "$required_focus_contract" "$focus_body_file"
done
test "$(printf '%s\n' "$focus_body" | \
  /usr/bin/grep -Fc 'AXUIElementSetAttributeValue(')" -eq 2
test "$(printf '%s\n' "$focus_body" | \
  /usr/bin/grep -Fc 'try requireSettableAttribute(')" -eq 1
test "$(printf '%s\n' "$focus_body" | \
  /usr/bin/grep -Fc 'try strictAttributeSettable(')" -eq 2
test "$(printf '%s\n' "$focus_body" | \
  /usr/bin/grep -Fc 'AXUIElementIsAttributeSettable(')" -eq 1
test "$(printf '%s\n' "$focus_body" | \
  /usr/bin/grep -Fc 'AXUIElementPerformAction(')" -eq 1
/usr/bin/grep -Fq 'panel, kAXRaiseAction as CFString' "$focus_body_file"
/usr/bin/grep -Fq 'application, kAXFocusedWindowAttribute as CFString' \
  "$focus_body_file"
focused_window_setter_body="$(printf '%s\n' "$focus_body" | /usr/bin/sed -n \
  '/^        setFocusedWindow: {$/,/^        })$/p')"
printf '%s\n' "$focused_window_setter_body" | \
  /usr/bin/grep -Fq 'application, kAXFocusedWindowAttribute as CFString'
printf '%s\n' "$focused_window_setter_body" | \
  /usr/bin/grep -Fq 'panel) == .success'
if printf '%s\n' "$focus_body" | /usr/bin/grep -Eq \
  'NSWorkspace|NSRunningApplication|activateIgnoringOtherApps|AXUIElementCreateSystemWide|postToPid'; then
  echo 'Open panel focus contract contains a broader activation or input surface' >&2
  exit 1
fi
activation_body="$(/usr/bin/sed -n \
  '/BEGIN_PID_EXACT_APP_ACTIVATION/,/END_PID_EXACT_APP_ACTIVATION/p' \
  "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$activation_body" | \
  /usr/bin/grep -Fc 'requestActivation()')" -eq 1
activation_live_body="$(/usr/bin/sed -n \
  '/^func activateVerifiedApplication(/,/^}/p' \
  "$HERE/14-native-gui-probe.swift")"
for required_activation_contract in exactRunningApplication \
  'running.activate(options: [])' running.isActive kAXFrontmostAttribute \
  'try requireSameProcess(process)'; do
  /usr/bin/grep -Fq "$required_activation_contract" <<EOF
$activation_live_body
EOF
done
if printf '%s\n' "$activation_live_body" | /usr/bin/grep -Eq \
  'activateAllWindows|activateIgnoringOtherApps|NSWorkspace|runningApplicationsWithBundleIdentifier'; then
  echo 'exact application activation uses a broader target or activation option' >&2
  exit 1
fi
active_boundary_body="$(/usr/bin/sed -n \
  '/^func requireVerifiedApplicationActive(/,/^}/p' \
  "$HERE/14-native-gui-probe.swift")"
for required_active_boundary in exactRunningApplication running.isActive \
  kAXFrontmostAttribute 'try requireSameProcess(process)'; do
  /usr/bin/grep -Fq "$required_active_boundary" <<EOF
$active_boundary_body
EOF
done
activation_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'let activationReadiness = try activateVerifiedApplication(' | \
  /usr/bin/awk -F: '{print $1}')"
menu_press_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'let menuReadiness = try pressOpenFolderMenuItem(' | \
  /usr/bin/awk -F: '{print $1}')"
test "$activation_line" -lt "$menu_press_line"
focus_snapshot_body="$(/usr/bin/sed -n \
  '/^func readOpenPanelFocusSnapshot(/,/^}/p' \
  "$HERE/14-native-gui-probe.swift")"
test "$(printf '%s\n' "$focus_snapshot_body" | \
  /usr/bin/grep -Fc 'try requireExactOpenPanelCurrent(')" -eq 2
command_focus_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'let focusReadiness = try focusOpenPanel(' | \
  /usr/bin/awk -F: '{print $1}')"
command_baseline_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'let baseline = try validatedPathEntryBaseline(' | \
  /usr/bin/awk -F: '{print $1}')"
command_shortcut_line="$(printf '%s\n' "$execute_body" | \
  /usr/bin/grep -nF 'try postCommandShiftG(' | \
  /usr/bin/awk -F: '{print $1}')"
test "$command_focus_line" -lt "$command_baseline_line"
test "$command_baseline_line" -lt "$command_shortcut_line"
direct_navigation_body="$(printf '%s\n' "$execute_body" | \
  /usr/bin/sed -n '/^    case \.direct:/,/^    case \.commandShiftG:/p')"
if printf '%s\n' "$direct_navigation_body" | /usr/bin/grep -Eq \
  'focusOpenPanel|postCommandShiftG|kAXFocused(Window)?Attribute'; then
  echo 'direct navigation unexpectedly uses focus authorization or mutation' >&2
  exit 1
fi
keyboard_post_body="$(/usr/bin/sed -n \
  '/^func performValidatedKeyboardPost(/,/^}/p' \
  "$HERE/14-native-gui-probe.swift")"
keyboard_focus_line="$(printf '%s\n' "$keyboard_post_body" | \
  /usr/bin/grep -nF 'try validateFocus()' | /usr/bin/sed -n '1p' | \
  /usr/bin/awk -F: '{print $1}')"
keyboard_down_line="$(printf '%s\n' "$keyboard_post_body" | \
  /usr/bin/grep -nF 'postKeyDown()' | /usr/bin/awk -F: '{print $1}')"
keyboard_up_line="$(printf '%s\n' "$keyboard_post_body" | \
  /usr/bin/grep -nF 'postKeyUp()' | /usr/bin/awk -F: '{print $1}')"
test "$keyboard_focus_line" -lt "$keyboard_down_line"
test $((keyboard_down_line + 1)) -eq "$keyboard_up_line"
for required_path_entry_gate in validatedPathEntryBaseline revalidatePathEntryToken \
  'publishedPath == paths.fixture' waitForValidatedOpenPanelRestoration \
  revalidateRestoredOpenPanelToken kAXDocumentAttribute kAXURLAttribute \
  'destinations == [expectedDestination]'; do
  /usr/bin/grep -Fq "$required_path_entry_gate" "$HERE/14-native-gui-probe.swift"
done
inspection_validated_line="$(/usr/bin/grep -nF \
  'native_menu_inspection_validated=true' "$HERE/08-run-prototype.sh" | \
  /usr/bin/awk -F: '{print $1}')"
common_cleanup_line="$(/usr/bin/awk '$0 == "cleanup" {print NR}' \
  "$HERE/08-run-prototype.sh")"
inspection_success_line="$(/usr/bin/grep -nF \
  'native-menu-inspection) test "$NATIVE_GUI_PROBE_INSPECT_MENU_ONLY" = true' \
  "$HERE/08-run-prototype.sh" | /usr/bin/awk -F: '{print $1}')"
test "$inspection_validated_line" -lt "$common_cleanup_line"
test "$common_cleanup_line" -lt "$inspection_success_line"

sensitive_symbols="$(/usr/bin/nm -u "$PROBE" | /usr/bin/awk '{print $NF}' | \
  /usr/bin/grep -E '(^_(AX|CGEvent|IOHID|NSAppleScript|LS(Open|Launch)|posix_spawn|exec|fork|system|kill|Sec(Code|StaticCode)|proc_))|NSTask|NSWorkspace' | \
  LC_ALL=C /usr/bin/sort || true)"
expected_sensitive_symbols='_AXIsProcessTrusted
_AXUIElementCopyActionNames
_AXUIElementCopyAttributeValue
_AXUIElementCreateApplication
_AXUIElementGetPid
_AXUIElementGetTypeID
_AXUIElementIsAttributeSettable
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

"$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase inspect-open-folder-menu \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --validate-inputs-only

if "$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase select-project \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --validate-inputs-only 2>"$BUILD_ROOT/missing-open-folder.stderr"; then
  echo 'select-project without explicit Open Folder authorization unexpectedly passed' >&2
  exit 1
fi
/usr/bin/grep -Fq 'select-project requires --press-open-folder-menu-item' \
  "$BUILD_ROOT/missing-open-folder.stderr"
"$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase select-project \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --press-open-folder-menu-item \
  --validate-inputs-only
if "$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase select-project \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --press-open-folder-menu-item \
  --permit-key-fallback \
  --validate-inputs-only 2>"$BUILD_ROOT/missing-focus.stderr"; then
  echo 'key fallback without explicit Open panel focus authorization unexpectedly passed' >&2
  exit 1
fi
/usr/bin/grep -Fq 'select-project with key fallback requires --focus-open-panel' \
  "$BUILD_ROOT/missing-focus.stderr"
"$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase select-project \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --press-open-folder-menu-item \
  --permit-key-fallback \
  --focus-open-panel \
  --validate-inputs-only
if "$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase select-project \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --press-open-folder-menu-item \
  --focus-open-panel \
  --validate-inputs-only 2>"$BUILD_ROOT/focus-without-fallback.stderr"; then
  echo 'Open panel focus authorization without key fallback unexpectedly passed' >&2
  exit 1
fi
/usr/bin/grep -Fq -- '--focus-open-panel requires --permit-key-fallback' \
  "$BUILD_ROOT/focus-without-fallback.stderr"
if "$PROBE" \
  --pid 2 \
  --run-root "$RUN_ROOT" \
  --expected-bundle "$RUN_ROOT/Probe.app" \
  --expected-executable "$RUN_ROOT/Probe.app/Contents/MacOS/ChatGPT" \
  --fixture-root "$RUN_ROOT/workspace" \
  --phase inspect-project-picker \
  --event-log "$RUN_ROOT/logs/native-gui-probe.jsonl" \
  --press-open-folder-menu-item \
  --validate-inputs-only 2>"$BUILD_ROOT/unexpected-open-folder.stderr"; then
  echo 'inspect-project-picker with Open Folder authorization unexpectedly passed' >&2
  exit 1
fi
/usr/bin/grep -Fq -- '--press-open-folder-menu-item is only valid for select-project' \
  "$BUILD_ROOT/unexpected-open-folder.stderr"

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
