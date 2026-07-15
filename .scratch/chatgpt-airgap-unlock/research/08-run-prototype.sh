#!/bin/sh
# THROWAWAY PROTOTYPE ONLY.
# Question: can exact build 26.707.71524 (5263), cold-started with only an
# isolated custom provider and no OpenAI state, reach its usable UI and local
# Responses provider while remote networking is denied?
set -eu

SOURCE_APP="/private/tmp/ChatGPT-Codex-26.707.71524-5263-extracted/ChatGPT-Codex-26.707.71524-5263.app"
REAL_HOME="${HOME:?HOME must name the operator home before isolation}"
OPTIQ="/private/tmp/chatgpt-optiq-smoke/.venv/bin/optiq"
OPTIQ_PYTHON="/private/tmp/chatgpt-optiq-smoke/.venv/bin/python"
OPTIQ_RUNTIME="$REAL_HOME/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none"
OPTIQ_SITE_PACKAGES="/private/tmp/chatgpt-optiq-smoke/.venv/lib/python3.12/site-packages"
MODEL_DIR="${MODEL_DIR:-$REAL_HOME/.cache/huggingface/hub/models--mlx-community--Qwen3.5-2B-OptiQ-4bit/snapshots/adc8669eb431e3168aeb4e320bd7b757914350e2}"
MODEL_REPO="${MODEL_DIR%/snapshots/*}"
MODEL_ID="$MODEL_DIR:no-think"
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROFILE="$HERE/08-probe.sb"
PROVIDER_PROFILE="$HERE/08-provider.sb"
GATEWAY_PROFILE="$HERE/08-gateway.sb"
OBSERVER="$HERE/08-proxy-observer.py"
CDP_OBSERVER="$HERE/08-cdp-observer.mjs"
GUI_DRIVER="$HERE/12-cdp-gui-driver.mjs"
COLD_RESUME_STATE="$HERE/12-cold-resume-state.py"
HOST_PROBE="$HERE/08-appserver-probe.py"
HOST_RESTART_PROBE="$HERE/08-appserver-restart-probe.py"
PROCESS_GROUP="$HERE/08-process-group.py"
UPSTREAM_OBSERVER="$HERE/08-upstream-observer.py"
NAMESPACE_PROBE="$HERE/08-namespace-probe.py"
NODE="${NODE:-/opt/homebrew/bin/node}"
CDP_PORT="${CDP_PORT:-49308}"
PROXY_PORT="${PROXY_PORT:-49309}"
OPTIQ_PORT="${OPTIQ_PORT:-18998}"
GATEWAY_PORT="${GATEWAY_PORT:-18999}"
UPSTREAM_OBSERVER_PORT="${UPSTREAM_OBSERVER_PORT:-18997}"
ROUTE_MODE="${ROUTE_MODE:-direct}"
REPO_ROOT="$(/usr/bin/git -C "$HERE" rev-parse --show-toplevel)"
GATEWAY_COMMIT="${GATEWAY_COMMIT:-}"
GATEWAY_REVIEWED_COMMIT="6307d37b76918c19f2e3bc0fd506434531aadeb2"
GATEWAY_FILE="tooling/codex-ns-proxy/codex-ns-proxy.py"
GATEWAY_REVIEWED_BLOB="a368b8f8e919361425763e86ca1c80fcea81825f"
GATEWAY_UPSTREAM_URL="http://127.0.0.1:$UPSTREAM_OBSERVER_PORT/v1"
GATEWAY_UPSTREAM_TIMEOUT_SECONDS="300"
GATEWAY_TERMINAL_PATTERN="[codex-ns-proxy] SSE terminal_completed=true"
PROBE_DURATION_MS="${PROBE_DURATION_MS:-20000}"
PROBE_EXPECT="${PROBE_EXPECT:-usable-ui}"
GUI_WORKFLOW="${GUI_WORKFLOW:-false}"
GUI_COLD_RESUME="${GUI_COLD_RESUME:-false}"
GUI_NATIVE_PROJECT_PICKER="${GUI_NATIVE_PROJECT_PICKER:-false}"
NATIVE_GUI_PROBE_BIN="${NATIVE_GUI_PROBE_BIN:-}"
NATIVE_GUI_PROBE_SHA256="${NATIVE_GUI_PROBE_SHA256:-}"
NATIVE_GUI_PROBE_KEY_FALLBACK="${NATIVE_GUI_PROBE_KEY_FALLBACK:-false}"
RUN_LOCK="/private/tmp/chatgpt-route-prototype-08.lock"

record_cold_handoff_phase() {
  cold_handoff_phase_name="$1"
  cold_handoff_phase_state="$2"
  if test -n "${LOG_DIR:-}" && test -d "$LOG_DIR"; then
    printf '{"kind":"phase","phase":"%s","state":"%s"}\n' \
      "$cold_handoff_phase_name" "$cold_handoff_phase_state" \
        2>/dev/null >>"$LOG_DIR/cold-handoff.jsonl" || true
  fi
}

record_top_level_signal() {
  top_level_signal_kind="$1"
  top_level_signal_status="$2"
  trap - INT TERM
  if test -n "${LOG_DIR:-}" && test -d "$LOG_DIR"; then
    printf '{"kind":"signal","signal":"%s","status":%s}\n' \
      "$top_level_signal_kind" "$top_level_signal_status" \
        2>/dev/null >>"$LOG_DIR/cold-handoff.jsonl" || true
  fi
  exit "$top_level_signal_status"
}

require_positive_terminal_delta() {
  checked_terminal_name="$1"
  checked_terminal_after="$2"
  checked_terminal_baseline="$3"
  checked_terminal_delta=$((checked_terminal_after - checked_terminal_baseline))
  if test "$checked_terminal_delta" -lt 1; then
    printf 'cold handoff %s terminal delta check failed: expected >=1, baseline=%s, after=%s, delta=%s\n' \
      "$checked_terminal_name" "$checked_terminal_baseline" "$checked_terminal_after" \
      "$checked_terminal_delta" >&2
    return 1
  fi
}

run_cold_handoff_self_test() (
  /usr/bin/python3 "$COLD_RESUME_STATE" --self-test
  self_test_root="$(mktemp -d /private/tmp/chatgpt-cold-handoff-self-test.XXXXXX)"
  trap '/bin/rm -rf "$self_test_root"' EXIT INT TERM
  LOG_DIR="$self_test_root"
  for phase in terminal-delta-capture terminal-delta-check resume-state-python stop-app-group relaunch; do
    record_cold_handoff_phase "$phase" before
    record_cold_handoff_phase "$phase" after
  done
  expected_phases="$(
    for phase in terminal-delta-capture terminal-delta-check resume-state-python stop-app-group relaunch; do
      printf '{"kind":"phase","phase":"%s","state":"before"}\n' "$phase"
      printf '{"kind":"phase","phase":"%s","state":"after"}\n' "$phase"
    done
  )"
  actual_phases="$(/bin/cat "$LOG_DIR/cold-handoff.jsonl")"
  test "$actual_phases" = "$expected_phases"
  production_phase_order="$(/usr/bin/awk '
    /^if test "\$GUI_COLD_RESUME" = true && test "\$cdp_exit" -eq 0; then$/ { handoff=1 }
    handoff && /record_cold_handoff_phase (terminal-delta-capture|terminal-delta-check|resume-state-python|stop-app-group|relaunch) (before|after)$/ {
      marker=$0
      sub(/^.*record_cold_handoff_phase /, "", marker)
      print marker
      if (marker == "relaunch after") exit
    }
  ' "$0")"
  expected_production_phase_order="$(
    for phase in terminal-delta-capture terminal-delta-check resume-state-python stop-app-group relaunch; do
      printf '%s before\n%s after\n' "$phase" "$phase"
    done
  )"
  test "$production_phase_order" = "$expected_production_phase_order"
  require_positive_terminal_delta gateway 2 0
  if require_positive_terminal_delta upstream 0 0 2>"$self_test_root/delta.stderr"; then
    echo "zero terminal delta unexpectedly passed" >&2
    return 1
  fi
  test "$(/bin/cat "$self_test_root/delta.stderr")" = \
    'cold handoff upstream terminal delta check failed: expected >=1, baseline=0, after=0, delta=0'
  if (trap - EXIT INT TERM; record_top_level_signal TERM 143); then
    echo "TERM diagnostic unexpectedly returned success" >&2
    return 1
  else
    signal_status=$?
  fi
  test "$signal_status" -eq 143
  test "$(/usr/bin/tail -n 1 "$LOG_DIR/cold-handoff.jsonl")" = \
    '{"kind":"signal","signal":"TERM","status":143}'
  printf 'cold handoff self-test passed\n'
)

if test "${1:-}" = --self-test; then
  run_cold_handoff_self_test
  exit 0
fi

lock_acquired=false
release_lock() {
  if test "$lock_acquired" = true; then
    /bin/rmdir "$RUN_LOCK" 2>/dev/null || true
    lock_acquired=false
  fi
}
if /bin/mkdir "$RUN_LOCK" 2>/dev/null; then
  lock_acquired=true
else
  echo "another ticket 08 prototype run owns $RUN_LOCK" >&2
  exit 75
fi
trap 'release_lock' EXIT
trap 'record_top_level_signal INT 130' INT
trap 'record_top_level_signal TERM 143' TERM

RUN_ROOT="$(mktemp -d /private/tmp/chatgpt-route-prototype-08.XXXXXX)"
INBOUND_TOKEN="sk-optiq-inbound-$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
UPSTREAM_TOKEN="sk-optiq-upstream-$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
WRONG_INBOUND_TOKEN="sk-optiq-wrong-$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
APP="$RUN_ROOT/ChatGPT-Codex-5263-OptiQ-Probe-08.app"
APP_EXEC="$APP/Contents/MacOS/ChatGPT"
HOME_DIR="$RUN_ROOT/home"
CODEX_DIR="$RUN_ROOT/codex-home"
USER_DATA_DIR="$RUN_ROOT/electron-user-data"
TMP_DIR="$RUN_ROOT/tmp"
LOG_DIR="$RUN_ROOT/logs"
WORKSPACE_DIR="$RUN_ROOT/workspace"
HF_CACHE_DIR="$RUN_ROOT/hf-cache"
GATEWAY_EXEC=""
UPSTREAM_OBSERVER_EXEC="$RUN_ROOT/upstream-observer.py"
OBSERVER_EXEC="$RUN_ROOT/proxy-observer.py"
CDP_OBSERVER_EXEC="$RUN_ROOT/cdp-observer.mjs"
GUI_DRIVER_EXEC="$RUN_ROOT/gui-driver.mjs"
NAMESPACE_PROBE_EXEC="$RUN_ROOT/namespace-probe.py"
COLD_RESUME_STATE_EXEC="$RUN_ROOT/cold-resume-state.py"
GUI_RESUME_STATE="$LOG_DIR/gui-resume-state.json"

case "$ROUTE_MODE" in
  direct)
    CODEX_PROVIDER_TOKEN_ENV="LOCAL_OPTIQ_API_KEY"
    CODEX_PROVIDER_TOKEN="$UPSTREAM_TOKEN"
    PROVIDER_BASE_URL="http://127.0.0.1:$OPTIQ_PORT/v1"
    ;;
  gateway)
    test -n "$GATEWAY_COMMIT"
    printf '%s\n' "$GATEWAY_COMMIT" | /usr/bin/grep -Eq '^[0-9a-f]{40}$'
    test "$GATEWAY_COMMIT" = "$GATEWAY_REVIEWED_COMMIT"
    CODEX_PROVIDER_TOKEN_ENV="LOCAL_GATEWAY_API_KEY"
    CODEX_PROVIDER_TOKEN="$INBOUND_TOKEN"
    PROVIDER_BASE_URL="http://127.0.0.1:$GATEWAY_PORT/v1"
    ;;
  *) echo "unknown ROUTE_MODE: $ROUTE_MODE" >&2; exit 64 ;;
esac
case "$GUI_WORKFLOW" in
  false|true) ;;
  *) echo "GUI_WORKFLOW must be true or false" >&2; exit 64 ;;
esac
case "$GUI_COLD_RESUME" in
  false|true) ;;
  *) echo "GUI_COLD_RESUME must be true or false" >&2; exit 64 ;;
esac
case "$GUI_NATIVE_PROJECT_PICKER" in
  false|true) ;;
  *) echo "GUI_NATIVE_PROJECT_PICKER must be true or false" >&2; exit 64 ;;
esac
case "$NATIVE_GUI_PROBE_KEY_FALLBACK" in
  false|true) ;;
  *) echo "NATIVE_GUI_PROBE_KEY_FALLBACK must be true or false" >&2; exit 64 ;;
esac
if test "$GUI_COLD_RESUME" = true; then
  test "$GUI_WORKFLOW" = true
  test "$ROUTE_MODE" = gateway
fi
if test "$GUI_NATIVE_PROJECT_PICKER" = true; then
  test "$GUI_WORKFLOW" = true
  test -n "$NATIVE_GUI_PROBE_BIN"
  test -n "$NATIVE_GUI_PROBE_SHA256"
  test "${NATIVE_GUI_PROBE_BIN#/}" != "$NATIVE_GUI_PROBE_BIN"
  test ! -L "$NATIVE_GUI_PROBE_BIN"
  native_gui_probe_realpath="$(realpath "$NATIVE_GUI_PROBE_BIN")"
  test "$(basename "$native_gui_probe_realpath")" = chatgpt-native-gui-probe
  case "$native_gui_probe_realpath" in
    /Applications/*) echo "native GUI helper must not be inside /Applications" >&2; exit 65 ;;
  esac
  printf '%s\n' "$NATIVE_GUI_PROBE_SHA256" | /usr/bin/grep -Eq '^[0-9a-f]{64}$'
  test -x "$native_gui_probe_realpath"
  test "$(/usr/bin/shasum -a 256 "$native_gui_probe_realpath" | /usr/bin/awk '{print $1}')" = \
    "$NATIVE_GUI_PROBE_SHA256"
  /usr/bin/codesign --verify --strict "$native_gui_probe_realpath"
  NATIVE_GUI_PROBE_BIN="$native_gui_probe_realpath"
fi

test -d "$SOURCE_APP"
test -x "$OPTIQ"
test -x "$OPTIQ_PYTHON"
test -d "$OPTIQ_SITE_PACKAGES"
test "$(dirname "$(dirname "$(realpath "$OPTIQ_PYTHON")")")" = "$OPTIQ_RUNTIME"
test -x "$NODE"
test -d "$MODEL_DIR"
test "$MODEL_DIR" = "$MODEL_REPO/snapshots/adc8669eb431e3168aeb4e320bd7b757914350e2"
test "$(readlink "$MODEL_DIR/model.safetensors")" = "../../blobs/4a46dafdc0afb80deacc2dada482ff339405adabc87fcde51a43834827f0e0bf"
test "$(/usr/bin/shasum -a 256 "$MODEL_DIR/config.json" | /usr/bin/awk '{print $1}')" = "00f425adfe01970c0cb5e627fa087a702e410e7f709d786bd8eeea910cf98e25"
test "$(/usr/bin/shasum -a 256 "$MODEL_DIR/model.safetensors.index.json" | /usr/bin/awk '{print $1}')" = "14c5afd88e0e856e05f005bbb5c300d65e268277949e7a25d3b3e8fcb056475b"
test "$(/usr/bin/shasum -a 256 "$MODEL_DIR/optiq_metadata.json" | /usr/bin/awk '{print $1}')" = "fb54f8ba5ac1c14a8949b52d1d8a3d2b5c86a56afbd18996e8cf9772c9606310"
test "$(/usr/bin/shasum -a 256 "$MODEL_DIR/tokenizer_config.json" | /usr/bin/awk '{print $1}')" = "b2bed5e033438f09f22b0ce9522115b4807d9bbcc3b82bf23372cb74d93ed081"
runtime_versions="$($OPTIQ_PYTHON -c 'import importlib.metadata as m, platform; print("|".join((platform.python_version(), m.version("mlx-optiq"), m.version("mlx-lm"), m.version("mlx"))))')"
test "$runtime_versions" = "3.12.13|0.2.15|0.31.3|0.32.0"
mlx_lm_commit="$($OPTIQ_PYTHON -c 'import importlib.metadata as m, json, pathlib; p=pathlib.Path(m.distribution("mlx-lm").locate_file("mlx_lm-0.31.3.dist-info/direct_url.json")); print(json.loads(p.read_text())["vcs_info"]["commit_id"])')"
test "$mlx_lm_commit" = "ab1806e8f5d6aa035973af194a1b9198ab4754dc"
test "$(/usr/bin/shasum -a 256 "$SOURCE_APP/Contents/Resources/app.asar" | /usr/bin/awk '{print $1}')" = "d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84"
test "$(/usr/bin/shasum -a 256 "$SOURCE_APP/Contents/Resources/codex" | /usr/bin/awk '{print $1}')" = "28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c"
for port in "$CDP_PORT" "$PROXY_PORT" "$UPSTREAM_OBSERVER_PORT" "$OPTIQ_PORT" "$GATEWAY_PORT"; do
  ! /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
done

mkdir -p "$HOME_DIR" "$CODEX_DIR" "$USER_DATA_DIR" "$TMP_DIR" "$LOG_DIR" "$WORKSPACE_DIR" "$HF_CACHE_DIR"
chmod 700 "$HOME_DIR" "$CODEX_DIR" "$USER_DATA_DIR" "$TMP_DIR" "$LOG_DIR" "$WORKSPACE_DIR" "$HF_CACHE_DIR"
/usr/bin/ditto "$SOURCE_APP" "$APP"
/bin/cp "$UPSTREAM_OBSERVER" "$UPSTREAM_OBSERVER_EXEC"
/bin/cp "$OBSERVER" "$OBSERVER_EXEC"
/bin/cp "$CDP_OBSERVER" "$CDP_OBSERVER_EXEC"
/bin/cp "$GUI_DRIVER" "$GUI_DRIVER_EXEC"
/bin/cp "$NAMESPACE_PROBE" "$NAMESPACE_PROBE_EXEC"
/bin/cp "$COLD_RESUME_STATE" "$COLD_RESUME_STATE_EXEC"
chmod 500 "$UPSTREAM_OBSERVER_EXEC" "$OBSERVER_EXEC" "$CDP_OBSERVER_EXEC" \
  "$GUI_DRIVER_EXEC" "$NAMESPACE_PROBE_EXEC" "$COLD_RESUME_STATE_EXEC"
test -x "$APP_EXEC"
test ! -L "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
cd "$RUN_ROOT"

if test "$ROUTE_MODE" = gateway; then
  resolved_gateway_commit="$(/usr/bin/git -C "$REPO_ROOT" rev-parse --verify "$GATEWAY_COMMIT^{commit}")"
  test "$resolved_gateway_commit" = "$GATEWAY_COMMIT"
  gateway_blob="$(/usr/bin/git -C "$REPO_ROOT" rev-parse "$GATEWAY_COMMIT:$GATEWAY_FILE")"
  test "$gateway_blob" = "$GATEWAY_REVIEWED_BLOB"
  GATEWAY_EXEC="$RUN_ROOT/gateway.py"
  /usr/bin/git -C "$REPO_ROOT" show "$GATEWAY_COMMIT:$GATEWAY_FILE" >"$GATEWAY_EXEC"
  test "$(/usr/bin/git hash-object "$GATEWAY_EXEC")" = "$gateway_blob"
  chmod 500 "$GATEWAY_EXEC"
  {
    printf 'commit=%s\n' "$GATEWAY_COMMIT"
    printf 'file=%s\n' "$GATEWAY_FILE"
    printf 'blob=%s\n' "$gateway_blob"
    printf 'adapter=codex-namespace\n'
    printf 'upstream=%s\n' "$GATEWAY_UPSTREAM_URL"
    printf 'upstream_timeout_seconds=%s\n' "$GATEWAY_UPSTREAM_TIMEOUT_SECONDS"
    printf 'sse_heartbeat_seconds=default\n'
  } >"$LOG_DIR/gateway-source.txt"
  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    TMPDIR="$TMP_DIR" \
    LANG="en_US.UTF-8" \
    /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
      /usr/bin/python3 "$NAMESPACE_PROBE_EXEC" \
        "$GATEWAY_EXEC" "$GATEWAY_UPSTREAM_TIMEOUT_SECONDS" \
    >"$LOG_DIR/namespace-probe.json" 2>"$LOG_DIR/namespace-probe.stderr"
  /usr/bin/grep -Fq '"continuation_mapping_reused": true' "$LOG_DIR/namespace-probe.json"
  /usr/bin/grep -Fq '"second_call_reconstructed": true' "$LOG_DIR/namespace-probe.json"
  /usr/bin/grep -Fq '"upstream_timeout_seconds": 300.0' "$LOG_DIR/namespace-probe.json"
  /usr/bin/grep -Fq '"default_sse_heartbeat_seconds": 15.0' "$LOG_DIR/namespace-probe.json"
  /usr/bin/grep -Fq '"data_only_done_sentinel_recognized": true' "$LOG_DIR/namespace-probe.json"
  /usr/bin/grep -Fq '"mixed_field_done_sentinel_rejected": true' "$LOG_DIR/namespace-probe.json"
fi

cat >"$CODEX_DIR/config.toml" <<EOF
model = "$MODEL_ID"
model_provider = "local-optiq"

[model_providers.local-optiq]
name = "Local OptiQ smoke model"
base_url = "$PROVIDER_BASE_URL"
env_key = "$CODEX_PROVIDER_TOKEN_ENV"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 1
stream_max_retries = 1

[shell_environment_policy]
inherit = "all"
exclude = ["$CODEX_PROVIDER_TOKEN_ENV"]

[features]
shell_snapshot = false
EOF
if test "$GUI_WORKFLOW" = true; then
  cat >"$CODEX_DIR/AGENTS.md" <<'EOF'
# Offline renderer smoke fixture

For the renderer smoke prompts, do not use tools and do not send a preamble.
Return only the exact phase sentinel requested by the user.
EOF
fi
cat >"$WORKSPACE_DIR/README.md" <<'EOF'
# Disposable ChatGPT route fixture

This directory exists only for ticket 08 validation. The expected deterministic
model response is `LOCAL_APP_OK`.
EOF
mkdir -p "$WORKSPACE_DIR/.agents/skills/local-sentinel"
cat >"$WORKSPACE_DIR/.agents/skills/local-sentinel/SKILL.md" <<'EOF'
---
name: local-sentinel
description: Local file-only skill for the ticket 08 deterministic plumbing check.
---

Do not use tools. Reply exactly `LOCAL_APP_OK` and nothing else.
EOF
/usr/bin/git -C "$WORKSPACE_DIR" init -q
/usr/bin/git -C "$WORKSPACE_DIR" config user.name "Ivan D Vasin"
/usr/bin/git -C "$WORKSPACE_DIR" config user.email "ivan@nisavid.io"
/usr/bin/git -C "$WORKSPACE_DIR" add README.md .agents/skills/local-sentinel/SKILL.md
/usr/bin/git -C "$WORKSPACE_DIR" commit -qm "test: create disposable fixture"

optiq_pid=""
proxy_pid=""
upstream_observer_pid=""
gateway_pid=""
app_pid=""
owned_processes_exited=false
owned_listeners_closed=false

owned_pids() {
  for pid in "$app_pid" "$proxy_pid" "$upstream_observer_pid" "$gateway_pid" "$optiq_pid"; do
    if test -n "$pid"; then printf '%s\n' "$pid"; fi
  done
}

owned_group_pids() {
  for pgid in $(owned_pids); do
    /bin/ps -axo pid=,pgid= | /usr/bin/awk -v wanted="$pgid" '$2 == wanted {print $1}'
  done
}

process_group_exists() {
  pgid="$1"
  /bin/ps -axo pgid= | /usr/bin/awk -v wanted="$pgid" '$1 == wanted {found=1} END {exit !found}'
}

signal_owned() {
  signal="$1"
  pid="$2"
  if process_group_exists "$pid"; then
    /bin/kill "-$signal" -- "-$pid" 2>/dev/null || true
  fi
  /bin/kill "-$signal" "$pid" 2>/dev/null || true
}

stop_app_group() {
  stopped_pid="$app_pid"
  test -n "$stopped_pid"
  signal_owned TERM "$stopped_pid"
  i=0
  while test "$i" -lt 100 && process_group_exists "$stopped_pid"; do
    sleep 0.1
    i=$((i + 1))
  done
  if process_group_exists "$stopped_pid"; then
    signal_owned KILL "$stopped_pid"
  fi
  wait "$stopped_pid" 2>/dev/null || true
  i=0
  while test "$i" -lt 50 && process_group_exists "$stopped_pid"; do
    sleep 0.1
    i=$((i + 1))
  done
  if process_group_exists "$stopped_pid"; then
    echo "copied app process group $stopped_pid survived cold stop" >&2
    exit 70
  fi
  if /usr/sbin/lsof -nP -iTCP:"$CDP_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "copied app CDP listener survived cold stop" >&2
    exit 70
  fi
  printf 'first_app_process_group=%s\n' "$stopped_pid" >"$LOG_DIR/gui-cold-stop.txt"
  printf 'process_group_exited=true\n' >>"$LOG_DIR/gui-cold-stop.txt"
  printf 'cdp_listener_closed=true\n' >>"$LOG_DIR/gui-cold-stop.txt"
  app_pid=""
}

cleanup() {
  for pid in $(owned_pids); do signal_owned TERM "$pid"; done
  sleep 1
  for pid in $(owned_pids); do signal_owned KILL "$pid"; done
  for pid in $(owned_pids); do wait "$pid" 2>/dev/null || true; done
  release_lock
}
trap 'status=$?; trap - EXIT INT TERM; cleanup; exit "$status"' EXIT
trap 'record_top_level_signal INT 130' INT
trap 'record_top_level_signal TERM 143' TERM

printf '%s\n' "$RUN_ROOT" >"$LOG_DIR/run-root.txt"
{
  printf 'model_revision=%s\n' "adc8669eb431e3168aeb4e320bd7b757914350e2"
  printf 'model_safetensors_blob=%s\n' "4a46dafdc0afb80deacc2dada482ff339405adabc87fcde51a43834827f0e0bf"
  printf 'python=%s\n' "3.12.13"
  printf 'mlx_optiq=%s\n' "0.2.15"
  printf 'mlx_lm=%s\n' "0.31.3"
  printf 'mlx_lm_commit=%s\n' "$mlx_lm_commit"
  printf 'mlx=%s\n' "0.32.0"
  printf 'route_mode=%s\n' "$ROUTE_MODE"
  if test "$ROUTE_MODE" = gateway; then
    printf 'gateway_upstream_timeout_seconds=%s\n' "$GATEWAY_UPSTREAM_TIMEOUT_SECONDS"
    printf 'gateway_sse_heartbeat_seconds=%s\n' "default"
  else
    printf 'gateway_upstream_timeout_seconds=%s\n' "not-applicable"
    printf 'gateway_sse_heartbeat_seconds=%s\n' "not-applicable"
  fi
} >"$LOG_DIR/runtime-manifest.txt"
/usr/bin/env -i \
  PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$HOME_DIR" \
  TMPDIR="$TMP_DIR" \
  HF_HUB_CACHE="$HF_CACHE_DIR" \
  HF_HUB_OFFLINE=1 \
  TRANSFORMERS_OFFLINE=1 \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$OPTIQ_SITE_PACKAGES" \
  VIRTUAL_ENV="/private/tmp/chatgpt-optiq-smoke/.venv" \
  LANG="en_US.UTF-8" \
  /usr/bin/python3 "$PROCESS_GROUP" \
  /usr/bin/sandbox-exec -f "$PROVIDER_PROFILE" \
    -D "REAL_HOME=$REAL_HOME" \
    -D "MODEL_REPO=$MODEL_REPO" \
    -D "OPTIQ_RUNTIME=$OPTIQ_RUNTIME" \
    "$OPTIQ_RUNTIME/bin/python3.12" "$OPTIQ" serve \
    --model "$MODEL_DIR" \
    --host 127.0.0.1 \
    --port "$OPTIQ_PORT" \
    --kv-bits 4 \
    --max-concurrent 1 \
    --no-anthropic \
    --responses \
    >"$LOG_DIR/optiq.stdout" 2>"$LOG_DIR/optiq.stderr" &
optiq_pid=$!

ready=false
i=0
while test "$i" -lt 120; do
  if /usr/bin/curl -fsS "http://127.0.0.1:$OPTIQ_PORT/v1/models" >"$LOG_DIR/models.json" 2>/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "$optiq_pid" 2>/dev/null; then
    echo "OptiQ exited before becoming ready" >&2
    exit 70
  fi
  sleep 0.5
  i=$((i + 1))
done
test "$ready" = true
/usr/bin/python3 - "$LOG_DIR/models.json" "$MODEL_DIR" <<'PY'
import json
import sys

models = json.load(open(sys.argv[1]))
base = sys.argv[2]
expected = {base, *(f"{base}:{variant}" for variant in ("think", "no-think", "precise", "creative"))}
actual = {entry.get("id") for entry in models.get("data", [])}
if actual != expected:
    raise SystemExit(f"unexpected OptiQ model listing: {sorted(actual)!r}")
PY

/usr/bin/env -i \
  PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$HOME_DIR" \
  TMPDIR="$TMP_DIR" \
  LANG="en_US.UTF-8" \
  /usr/bin/python3 "$PROCESS_GROUP" \
  /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
    /usr/bin/python3 "$OBSERVER_EXEC" "$PROXY_PORT" \
  >"$LOG_DIR/proxy.jsonl" 2>"$LOG_DIR/proxy.stderr" &
proxy_pid=$!

if test "$ROUTE_MODE" = gateway; then
  : >"$LOG_DIR/upstream-auth.jsonl"
  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    TMPDIR="$TMP_DIR" \
    LANG="en_US.UTF-8" \
    EXPECTED_UPSTREAM_TOKEN="$UPSTREAM_TOKEN" \
    FORBIDDEN_INBOUND_TOKEN="$INBOUND_TOKEN" \
    /usr/bin/python3 "$PROCESS_GROUP" \
    /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
      /usr/bin/python3 "$UPSTREAM_OBSERVER_EXEC" \
        "$UPSTREAM_OBSERVER_PORT" "$OPTIQ_PORT" "$LOG_DIR/upstream-auth.jsonl" \
    >"$LOG_DIR/upstream-observer.stdout" 2>"$LOG_DIR/upstream-observer.stderr" &
  upstream_observer_pid=$!
  ready=false
  i=0
  while test "$i" -lt 100; do
    if /usr/sbin/lsof -nP -a -p "$upstream_observer_pid" -iTCP:"$UPSTREAM_OBSERVER_PORT" -sTCP:LISTEN | /usr/bin/grep -Fq "127.0.0.1:$UPSTREAM_OBSERVER_PORT"; then
      ready=true
      break
    fi
    if ! kill -0 "$upstream_observer_pid" 2>/dev/null; then
      echo "upstream observer exited before becoming ready" >&2
      exit 70
    fi
    sleep 0.1
    i=$((i + 1))
  done
  test "$ready" = true

  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    TMPDIR="$TMP_DIR" \
    LANG="en_US.UTF-8" \
    NS_PROXY_HOST="127.0.0.1" \
    NS_PROXY_PORT="$GATEWAY_PORT" \
    NS_PROXY_UPSTREAM="$GATEWAY_UPSTREAM_URL" \
    NS_PROXY_UPSTREAM_TIMEOUT="$GATEWAY_UPSTREAM_TIMEOUT_SECONDS" \
    NS_PROXY_ADAPTER="codex-namespace" \
    NS_PROXY_INBOUND_TOKEN="$INBOUND_TOKEN" \
    NS_PROXY_UPSTREAM_TOKEN="$UPSTREAM_TOKEN" \
    NS_PROXY_DEBUG=true \
    /usr/bin/python3 "$PROCESS_GROUP" \
    /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
      /usr/bin/python3 "$GATEWAY_EXEC" \
      >"$LOG_DIR/gateway.stdout" 2>"$LOG_DIR/gateway.stderr" &
  gateway_pid=$!
  ready=false
  i=0
  while test "$i" -lt 100; do
    if /usr/sbin/lsof -nP -a -p "$gateway_pid" -iTCP:"$GATEWAY_PORT" -sTCP:LISTEN | /usr/bin/grep -Fq "127.0.0.1:$GATEWAY_PORT"; then
      ready=true
      break
    fi
    if ! kill -0 "$gateway_pid" 2>/dev/null; then
      echo "gateway exited before becoming ready" >&2
      exit 70
    fi
    sleep 0.1
    i=$((i + 1))
  done
  test "$ready" = true

  printf '%s\n' '{"invalid":"MISSING_OR_WRONG_AUTH_BODY_CANARY"}' >"$TMP_DIR/gateway-negative.json"
  missing_auth_status="$(/usr/bin/curl -sS \
    --output "$LOG_DIR/gateway-missing-auth.json" \
    --write-out '%{http_code}' \
    --request POST \
    --header 'Content-Type: application/json' \
    --data-binary "@$TMP_DIR/gateway-negative.json" \
    "http://127.0.0.1:$GATEWAY_PORT/v1/responses")"
  wrong_auth_status="$(
    /usr/bin/printf 'header = "Authorization: Bearer %s"\n' "$WRONG_INBOUND_TOKEN" |
      /usr/bin/curl -sS \
        --config - \
        --output "$LOG_DIR/gateway-wrong-auth.json" \
        --write-out '%{http_code}' \
        --request POST \
        --header 'Content-Type: application/json' \
        --data-binary "@$TMP_DIR/gateway-negative.json" \
        "http://127.0.0.1:$GATEWAY_PORT/v1/responses"
  )"
  test "$missing_auth_status" = 401
  test "$wrong_auth_status" = 401
  test ! -s "$LOG_DIR/upstream-auth.jsonl"
fi

if test "$GUI_WORKFLOW" = false; then
  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    CODEX_HOME="$CODEX_DIR" \
    TMPDIR="$TMP_DIR" \
    CODEX_PROVIDER_TOKEN_ENV="$CODEX_PROVIDER_TOKEN_ENV" \
    "$CODEX_PROVIDER_TOKEN_ENV=$CODEX_PROVIDER_TOKEN" \
    /usr/bin/python3 "$HOST_PROBE" \
      "$APP/Contents/Resources/codex" \
      "$WORKSPACE_DIR" \
      "$PROFILE" \
      "$REAL_HOME" \
      "$LOG_DIR" \
      "$MODEL_ID" \
      "$PROXY_PORT" \
      >"$LOG_DIR/host-probe.stdout" 2>"$LOG_DIR/host-probe.stderr"
  THREAD_ID="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["thread_id"])' "$LOG_DIR/host-summary.json")"
  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    CODEX_HOME="$CODEX_DIR" \
    TMPDIR="$TMP_DIR" \
    CODEX_PROVIDER_TOKEN_ENV="$CODEX_PROVIDER_TOKEN_ENV" \
    "$CODEX_PROVIDER_TOKEN_ENV=$CODEX_PROVIDER_TOKEN" \
    /usr/bin/python3 "$HOST_RESTART_PROBE" \
      "$APP/Contents/Resources/codex" \
      "$WORKSPACE_DIR" \
      "$PROFILE" \
      "$REAL_HOME" \
      "$LOG_DIR" \
      "$THREAD_ID" \
      "$PROXY_PORT" \
      >"$LOG_DIR/host-restart-probe.stdout" 2>"$LOG_DIR/host-restart-probe.stderr"
fi

launch_app() {
  log_stem="$1"
  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    CFFIXED_USER_HOME="$HOME_DIR" \
    XDG_CONFIG_HOME="$HOME_DIR/.config" \
    XDG_CACHE_HOME="$HOME_DIR/.cache" \
    XDG_DATA_HOME="$HOME_DIR/.local/share" \
    CODEX_HOME="$CODEX_DIR" \
    CODEX_ELECTRON_USER_DATA_PATH="$USER_DATA_DIR" \
    TMPDIR="$TMP_DIR" \
    USER="offline-probe" \
    LOGNAME="offline-probe" \
    SHELL="/bin/sh" \
    LANG="en_US.UTF-8" \
    "$CODEX_PROVIDER_TOKEN_ENV=$CODEX_PROVIDER_TOKEN" \
    HTTP_PROXY="http://127.0.0.1:$PROXY_PORT" \
    HTTPS_PROXY="http://127.0.0.1:$PROXY_PORT" \
    ALL_PROXY="http://127.0.0.1:$PROXY_PORT" \
    NO_PROXY="127.0.0.1,localhost" \
    http_proxy="http://127.0.0.1:$PROXY_PORT" \
    https_proxy="http://127.0.0.1:$PROXY_PORT" \
    all_proxy="http://127.0.0.1:$PROXY_PORT" \
    no_proxy="127.0.0.1,localhost" \
    ELECTRON_ENABLE_LOGGING=1 \
    RUST_LOG=info \
    /usr/bin/python3 "$PROCESS_GROUP" \
    /usr/bin/sandbox-exec -f "$PROFILE" -D "REAL_HOME=$REAL_HOME" "$APP_EXEC" \
      --no-sandbox \
      --user-data-dir="$USER_DATA_DIR" \
      --remote-debugging-port="$CDP_PORT" \
      --enable-logging=stderr \
      --v=1 \
      >"$LOG_DIR/$log_stem.stdout" 2>"$LOG_DIR/$log_stem.stderr" &
  app_pid=$!
}

gateway_terminal_baseline=not-applicable
upstream_terminal_baseline=not-applicable
gateway_terminal_after_first=not-applicable
upstream_terminal_after_first=not-applicable
if test "$ROUTE_MODE" = gateway; then
  gateway_terminal_baseline="$(/usr/bin/grep -Fxc "$GATEWAY_TERMINAL_PATTERN" "$LOG_DIR/gateway.stderr" || true)"
  upstream_terminal_baseline="$(/usr/bin/grep -Fc '"upstream_terminal_completed": true' "$LOG_DIR/upstream-auth.jsonl" || true)"
  gateway_terminal_after_first="$gateway_terminal_baseline"
  upstream_terminal_after_first="$upstream_terminal_baseline"
fi
launch_app app

sleep 1
{
  /bin/ps -axo pid,ppid,pgid,state,etime,command | /usr/bin/head -n 1
  for pid in $(owned_group_pids); do
    /bin/ps -p "$pid" -o pid=,ppid=,pgid=,state=,etime=,command=
  done
} >"$LOG_DIR/processes-01s.txt"
if test "$GUI_NATIVE_PROJECT_PICKER" = true; then
  /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    TMPDIR="$TMP_DIR" \
    LANG="en_US.UTF-8" \
    /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
      "$NODE" "$GUI_DRIVER_EXEC" "$CDP_PORT" open-project-picker 30000 \
    >"$LOG_DIR/cdp-native-open.jsonl" 2>"$LOG_DIR/cdp-native-open.stderr"
  /usr/bin/grep -Fq '"kind":"native-project-picker-requested"' \
    "$LOG_DIR/cdp-native-open.jsonl"
  copied_app_pid="$(/bin/ps -axo pid=,pgid=,command= | \
    /usr/bin/awk -v wanted_group="$app_pid" -v wanted_executable="$APP_EXEC" \
      '$2 == wanted_group && $3 == wanted_executable {print $1}')"
  test -n "$copied_app_pid"
  test "$(printf '%s\n' "$copied_app_pid" | /usr/bin/wc -l | /usr/bin/tr -d ' ')" -eq 1
  set -- \
    --pid "$copied_app_pid" \
    --run-root "$RUN_ROOT" \
    --expected-bundle "$APP" \
    --expected-executable "$APP_EXEC" \
    --fixture-root "$WORKSPACE_DIR" \
    --phase select-project \
    --event-log "$LOG_DIR/native-gui-probe.jsonl"
  if test "$NATIVE_GUI_PROBE_KEY_FALLBACK" = true; then
    set -- "$@" --permit-key-fallback
  fi
  "$NATIVE_GUI_PROBE_BIN" "$@" \
    >"$LOG_DIR/native-gui-probe.stdout" 2>"$LOG_DIR/native-gui-probe.stderr"
  /usr/bin/grep -Fq '"kind":"project-selection-issued"' \
    "$LOG_DIR/native-gui-probe.jsonl"
  sleep 1
fi
if test "$GUI_WORKFLOW" = true; then
  cdp_command="$GUI_DRIVER_EXEC"
  cdp_argument="first"
  cdp_timeout="120000"
else
  cdp_command="$CDP_OBSERVER_EXEC"
  cdp_argument="$PROBE_DURATION_MS"
  cdp_timeout=""
fi
if test "$GUI_WORKFLOW" = true && /usr/bin/env -i \
  PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$HOME_DIR" \
  TMPDIR="$TMP_DIR" \
  LANG="en_US.UTF-8" \
  /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
    "$NODE" "$cdp_command" "$CDP_PORT" "$cdp_argument" "$cdp_timeout" \
  >"$LOG_DIR/cdp.jsonl" 2>"$LOG_DIR/cdp.stderr"; then
  cdp_exit=0
elif test "$GUI_WORKFLOW" = false && /usr/bin/env -i \
  PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$HOME_DIR" \
  TMPDIR="$TMP_DIR" \
  LANG="en_US.UTF-8" \
  /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
    "$NODE" "$cdp_command" "$CDP_PORT" "$cdp_argument" \
  >"$LOG_DIR/cdp.jsonl" 2>"$LOG_DIR/cdp.stderr"; then
  cdp_exit=0
else
  cdp_exit=$?
fi

if test "$GUI_COLD_RESUME" = true && test "$cdp_exit" -eq 0; then
  record_cold_handoff_phase terminal-delta-capture before
  gateway_terminal_after_first="$(/usr/bin/grep -Fxc "$GATEWAY_TERMINAL_PATTERN" "$LOG_DIR/gateway.stderr" || true)"
  upstream_terminal_after_first="$(/usr/bin/grep -Fc '"upstream_terminal_completed": true' "$LOG_DIR/upstream-auth.jsonl" || true)"
  record_cold_handoff_phase terminal-delta-capture after
  record_cold_handoff_phase terminal-delta-check before
  if ! require_positive_terminal_delta gateway "$gateway_terminal_after_first" "$gateway_terminal_baseline"; then
    record_cold_handoff_phase terminal-delta-check failed
    exit 1
  fi
  if ! require_positive_terminal_delta upstream "$upstream_terminal_after_first" "$upstream_terminal_baseline"; then
    record_cold_handoff_phase terminal-delta-check failed
    exit 1
  fi
  record_cold_handoff_phase terminal-delta-check after
  record_cold_handoff_phase resume-state-python before
  /usr/bin/python3 "$COLD_RESUME_STATE_EXEC" capture \
    "$CODEX_DIR" "$GUI_RESUME_STATE" "$LOG_DIR/cdp.jsonl"
  record_cold_handoff_phase resume-state-python after
  record_cold_handoff_phase stop-app-group before
  stop_app_group
  record_cold_handoff_phase stop-app-group after
  kill -0 "$optiq_pid"
  kill -0 "$proxy_pid"
  if test "$ROUTE_MODE" = gateway; then
    kill -0 "$upstream_observer_pid"
    kill -0 "$gateway_pid"
  fi
  record_cold_handoff_phase relaunch before
  launch_app app-restart
  sleep 1
  record_cold_handoff_phase relaunch after
  if /usr/bin/env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    HOME="$HOME_DIR" \
    TMPDIR="$TMP_DIR" \
    LANG="en_US.UTF-8" \
    /usr/bin/sandbox-exec -f "$GATEWAY_PROFILE" -D "REAL_HOME=$REAL_HOME" \
      "$NODE" "$GUI_DRIVER_EXEC" "$CDP_PORT" second 120000 "$GUI_RESUME_STATE" \
    >>"$LOG_DIR/cdp.jsonl" 2>>"$LOG_DIR/cdp.stderr"; then
    cdp_exit=0
  else
    cdp_exit=$?
  fi
  if test "$cdp_exit" -eq 0; then
    /usr/bin/python3 "$COLD_RESUME_STATE_EXEC" validate \
      "$GUI_RESUME_STATE" "$LOG_DIR/cdp.jsonl"
  fi
fi

{
  /bin/ps -axo pid,ppid,pgid,state,etime,command | /usr/bin/head -n 1
  for pid in $(owned_group_pids); do
    /bin/ps -p "$pid" -o pid=,ppid=,pgid=,state=,etime=,command=
  done
} >"$LOG_DIR/processes-final.txt"
for pid in $(owned_group_pids); do
  /usr/sbin/lsof -nP -p "$pid" >>"$LOG_DIR/lsof-final.txt" 2>/dev/null || true
done

login_wall_observed=false
if /usr/bin/grep -Fq 'Sign in to ChatGPT' "$LOG_DIR/cdp.jsonl"; then login_wall_observed=true; fi
account_read_observed=false
if /usr/bin/grep -Fq 'method=account/read' "$LOG_DIR/app.stdout"; then account_read_observed=true; fi
main_ui_observed=false
if /usr/bin/grep -Fq '"mainUi":true' "$LOG_DIR/cdp.jsonl"; then main_ui_observed=true; fi
cdp_observer_healthy=false
if test "$cdp_exit" -eq 0 && \
  /usr/bin/grep -Fq '"kind":"target"' "$LOG_DIR/cdp.jsonl" && \
  /usr/bin/grep -Fq '"kind":"renderer-state"' "$LOG_DIR/cdp.jsonl" && \
  ! /usr/bin/grep -Fq '"kind":"observer-error"' "$LOG_DIR/cdp.jsonl" && \
  ! /usr/bin/grep -Fq '"kind":"driver-error"' "$LOG_DIR/cdp.jsonl"; then
  cdp_observer_healthy=true
fi
host_sentinel_completed=not-applicable
cold_host_resume_observed=not-applicable
renderer_prompt_completed=not-applicable
renderer_tasks_observed=not-applicable
renderer_settings_observed=not-applicable
renderer_plugins_observed=not-applicable
renderer_skills_observed=not-applicable
renderer_local_skill_visible=not-applicable
renderer_model_surface_observed=not-applicable
renderer_model_metadata_matched=not-applicable
native_project_picker_exercised=not-applicable
native_permission_decision_exercised=not-applicable
native_worktree_control_exercised=not-applicable
renderer_thread_reopened=not-applicable
renderer_continuation_completed=not-applicable
renderer_same_rollout_continuation=not-applicable
copied_app_cold_stopped=not-applicable
if test "$GUI_WORKFLOW" = false; then
  host_sentinel_completed=false
  if /usr/bin/grep -Fq '"sentinel_completed": true' "$LOG_DIR/host-summary.json" && /usr/bin/grep -Fq '"sentinel_text_observed": true' "$LOG_DIR/host-summary.json"; then host_sentinel_completed=true; fi
  cold_host_resume_observed=false
  if /usr/bin/grep -Fq '"thread_resumed_after_cold_restart": true' "$LOG_DIR/host-restart-summary.json" && /usr/bin/grep -Fq '"thread_read_after_cold_restart": true' "$LOG_DIR/host-restart-summary.json"; then cold_host_resume_observed=true; fi
else
  renderer_prompt_completed=false
  renderer_tasks_observed=false
  renderer_settings_observed=false
  renderer_plugins_observed=false
  renderer_skills_observed=false
  renderer_local_skill_visible=false
  renderer_model_surface_observed=false
  renderer_model_metadata_matched=false
  native_project_picker_exercised=false
  native_permission_decision_exercised=false
  native_worktree_control_exercised=false
  if test "$GUI_NATIVE_PROJECT_PICKER" = true && \
    test -f "$LOG_DIR/native-gui-probe.jsonl" && \
    /usr/bin/grep -Fq '"kind":"project-selection-issued"' "$LOG_DIR/native-gui-probe.jsonl"; then
    native_project_picker_exercised=true
  fi
  renderer_thread_reopened=false
  renderer_continuation_completed=false
  renderer_same_rollout_continuation=false
  copied_app_cold_stopped=false
  if /usr/bin/grep -Fq '"rendererPromptCompleted":true' "$LOG_DIR/cdp.jsonl"; then renderer_prompt_completed=true; fi
  if /usr/bin/grep -Fq '"tasksSurfaceObserved":true' "$LOG_DIR/cdp.jsonl"; then renderer_tasks_observed=true; fi
  if /usr/bin/grep -Fq '"settingsSurfaceObserved":true' "$LOG_DIR/cdp.jsonl"; then renderer_settings_observed=true; fi
  if /usr/bin/grep -Fq '"pluginSurfaceObserved":true' "$LOG_DIR/cdp.jsonl"; then renderer_plugins_observed=true; fi
  if /usr/bin/grep -Fq '"skillSurfaceObserved":true' "$LOG_DIR/cdp.jsonl"; then renderer_skills_observed=true; fi
  if /usr/bin/grep -Fq '"localSkillVisible":true' "$LOG_DIR/cdp.jsonl"; then renderer_local_skill_visible=true; fi
  if /usr/bin/grep -Fq '"modelSurfaceObserved":true' "$LOG_DIR/cdp.jsonl"; then renderer_model_surface_observed=true; fi
  if /usr/bin/grep -Fq '"rendererModelMetadataMatched":true' "$LOG_DIR/cdp.jsonl"; then renderer_model_metadata_matched=true; fi
  if /usr/bin/grep -Fq '"rendererThreadReopened":true' "$LOG_DIR/cdp.jsonl"; then renderer_thread_reopened=true; fi
  if /usr/bin/grep -Fq '"rendererContinuationCompleted":true' "$LOG_DIR/cdp.jsonl"; then renderer_continuation_completed=true; fi
  if test -f "$GUI_RESUME_STATE" && /usr/bin/grep -Fq '"sameRolloutContinuationValidated": true' "$GUI_RESUME_STATE"; then renderer_same_rollout_continuation=true; fi
  if test -f "$LOG_DIR/gui-cold-stop.txt" && \
    /usr/bin/grep -Fq 'process_group_exited=true' "$LOG_DIR/gui-cold-stop.txt" && \
    /usr/bin/grep -Fq 'cdp_listener_closed=true' "$LOG_DIR/gui-cold-stop.txt"; then
    copied_app_cold_stopped=true
  fi
fi
provider_request_observed=false
if /usr/bin/grep -Fq 'POST /v1/responses HTTP/1.1" 200' "$LOG_DIR/optiq.stderr"; then provider_request_observed=true; fi
auth_json_present=false
if test -e "$CODEX_DIR/auth.json"; then auth_json_present=true; fi
shell_snapshot_absent=true
if test -d "$CODEX_DIR/shell_snapshots" && find "$CODEX_DIR/shell_snapshots" -type f -print -quit | /usr/bin/grep -q .; then
  shell_snapshot_absent=false
fi
provider_listener_observed=false
if /usr/sbin/lsof -nP -a -p "$optiq_pid" -iTCP:"$OPTIQ_PORT" -sTCP:LISTEN | /usr/bin/grep -Fq "127.0.0.1:$OPTIQ_PORT"; then provider_listener_observed=true; fi
gateway_listener_observed=not-applicable
gateway_terminal_observed=not-applicable
gateway_body_leak_observed=not-applicable
gateway_missing_auth_rejected=not-applicable
gateway_wrong_auth_rejected=not-applicable
upstream_token_replaced=not-applicable
upstream_terminal_completed=not-applicable
namespace_tool_continuation=not-applicable
gateway_terminal_count=not-applicable
upstream_terminal_count=not-applicable
gateway_renderer_terminal_delta=not-applicable
upstream_renderer_terminal_delta=not-applicable
gateway_first_renderer_terminal_delta=not-applicable
upstream_first_renderer_terminal_delta=not-applicable
gateway_continuation_terminal_delta=not-applicable
upstream_continuation_terminal_delta=not-applicable
renderer_continuation_transport_observed=not-applicable
refresh_gateway_log_observations() {
  if /usr/bin/grep -Fxq "$GATEWAY_TERMINAL_PATTERN" "$LOG_DIR/gateway.stderr"; then
    gateway_terminal_observed=true
  fi
  if /usr/bin/grep -Fq '"upstream_terminal_completed": true' "$LOG_DIR/upstream-auth.jsonl"; then
    upstream_terminal_completed=true
  fi
  if /usr/bin/grep -Fq 'Reply with exactly LOCAL_APP_OK' "$LOG_DIR/gateway.stdout" "$LOG_DIR/gateway.stderr" || \
    /usr/bin/grep -Fq 'LOCAL_APP_OK' "$LOG_DIR/gateway.stdout" "$LOG_DIR/gateway.stderr" || \
    /usr/bin/grep -Fq 'Reply exactly COLD_PHASE_ONE_OK' "$LOG_DIR/gateway.stdout" "$LOG_DIR/gateway.stderr" || \
    /usr/bin/grep -Fq 'Reply exactly COLD_PHASE_TWO_OK' "$LOG_DIR/gateway.stdout" "$LOG_DIR/gateway.stderr" || \
    /usr/bin/grep -Fq 'COLD_PHASE_ONE_OK' "$LOG_DIR/gateway.stdout" "$LOG_DIR/gateway.stderr" || \
    /usr/bin/grep -Fq 'COLD_PHASE_TWO_OK' "$LOG_DIR/gateway.stdout" "$LOG_DIR/gateway.stderr" || \
    /usr/bin/grep -Fq 'MISSING_OR_WRONG_AUTH_BODY_CANARY' "$LOG_DIR/gateway.stdout" "$LOG_DIR/gateway.stderr"; then
    gateway_body_leak_observed=true
  fi
  gateway_terminal_count="$(/usr/bin/grep -Fxc "$GATEWAY_TERMINAL_PATTERN" "$LOG_DIR/gateway.stderr" || true)"
  upstream_terminal_count="$(/usr/bin/grep -Fc '"upstream_terminal_completed": true' "$LOG_DIR/upstream-auth.jsonl" || true)"
  gateway_renderer_terminal_delta=$((gateway_terminal_count - gateway_terminal_baseline))
  upstream_renderer_terminal_delta=$((upstream_terminal_count - upstream_terminal_baseline))
  gateway_first_renderer_terminal_delta=$((gateway_terminal_after_first - gateway_terminal_baseline))
  upstream_first_renderer_terminal_delta=$((upstream_terminal_after_first - upstream_terminal_baseline))
  gateway_continuation_terminal_delta=$((gateway_terminal_count - gateway_terminal_after_first))
  upstream_continuation_terminal_delta=$((upstream_terminal_count - upstream_terminal_after_first))
  if test "$GUI_COLD_RESUME" = true && \
    test "$gateway_renderer_terminal_delta" -ge 2 && \
    test "$upstream_renderer_terminal_delta" -ge 2 && \
    test "$gateway_first_renderer_terminal_delta" -ge 1 && \
    test "$upstream_first_renderer_terminal_delta" -ge 1 && \
    test "$gateway_continuation_terminal_delta" -ge 1 && \
    test "$upstream_continuation_terminal_delta" -ge 1; then
    renderer_continuation_transport_observed=true
  fi
}
if test "$ROUTE_MODE" = gateway; then
  gateway_listener_observed=false
  gateway_terminal_observed=false
  gateway_body_leak_observed=false
  gateway_missing_auth_rejected=false
  gateway_wrong_auth_rejected=false
  upstream_token_replaced=false
  upstream_terminal_completed=false
  namespace_tool_continuation=false
  gateway_terminal_count=0
  upstream_terminal_count=0
  gateway_renderer_terminal_delta=0
  upstream_renderer_terminal_delta=0
  gateway_first_renderer_terminal_delta=0
  upstream_first_renderer_terminal_delta=0
  gateway_continuation_terminal_delta=0
  upstream_continuation_terminal_delta=0
  renderer_continuation_transport_observed=false
  if /usr/sbin/lsof -nP -a -p "$gateway_pid" -iTCP:"$GATEWAY_PORT" -sTCP:LISTEN | /usr/bin/grep -Fq "127.0.0.1:$GATEWAY_PORT"; then gateway_listener_observed=true; fi
  if test "$missing_auth_status" = 401; then gateway_missing_auth_rejected=true; fi
  if test "$wrong_auth_status" = 401; then gateway_wrong_auth_rejected=true; fi
  if /usr/bin/grep -Fq '"exact_upstream_token": true' "$LOG_DIR/upstream-auth.jsonl" && \
    ! /usr/bin/grep -Fq '"exact_upstream_token": false' "$LOG_DIR/upstream-auth.jsonl" && \
    ! /usr/bin/grep -Fq '"inbound_token_reused": true' "$LOG_DIR/upstream-auth.jsonl"; then
    upstream_token_replaced=true
  fi
  if /usr/bin/grep -Fq '"continuation_mapping_reused": true' "$LOG_DIR/namespace-probe.json" && \
    /usr/bin/grep -Fq '"second_call_reconstructed": true' "$LOG_DIR/namespace-probe.json"; then
    namespace_tool_continuation=true
  fi
  refresh_gateway_log_observations
fi
remote_socket_observed=false
if test -s "$LOG_DIR/lsof-final.txt" && /usr/bin/awk '$8 == "TCP" || $8 == "UDP" {print}' "$LOG_DIR/lsof-final.txt" | /usr/bin/grep -Ev '127\.0\.0\.1|localhost|\[::1\]|::1' | /usr/bin/grep -q .; then remote_socket_observed=true; fi
token_leak_observed=false

app_asar_after="$(/usr/bin/shasum -a 256 "$APP/Contents/Resources/app.asar" | /usr/bin/awk '{print $1}')"
codex_after="$(/usr/bin/shasum -a 256 "$APP/Contents/Resources/codex" | /usr/bin/awk '{print $1}')"
/usr/bin/codesign --verify --deep --strict "$APP"

cleanup
trap - EXIT INT TERM
if test "$ROUTE_MODE" = gateway; then
  refresh_gateway_log_observations
fi
for token in "$INBOUND_TOKEN" "$UPSTREAM_TOKEN" "$WRONG_INBOUND_TOKEN"; do
  if find "$HOME_DIR" "$CODEX_DIR" "$USER_DATA_DIR" "$TMP_DIR" "$LOG_DIR" -type f \
    -exec /usr/bin/grep -lF "$token" {} + 2>/dev/null | /usr/bin/grep -q .; then
    token_leak_observed=true
  fi
done
owned_processes_exited=true
for pid in $(owned_pids); do
  if kill -0 "$pid" 2>/dev/null || process_group_exists "$pid"; then
    owned_processes_exited=false
  fi
done
owned_listeners_closed=true
for port in "$CDP_PORT" "$PROXY_PORT" "$UPSTREAM_OBSERVER_PORT" "$OPTIQ_PORT" "$GATEWAY_PORT"; do
  if /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    owned_listeners_closed=false
  fi
done

{
  printf 'ROUTE_MODE=%s\n' "$ROUTE_MODE"
  printf 'LOGIN_WALL_OBSERVED=%s\n' "$login_wall_observed"
  printf 'ACCOUNT_READ_OBSERVED=%s\n' "$account_read_observed"
  printf 'MAIN_UI_OBSERVED=%s\n' "$main_ui_observed"
  printf 'CDP_OBSERVER_HEALTHY=%s\n' "$cdp_observer_healthy"
  printf 'HOST_SENTINEL_COMPLETED=%s\n' "$host_sentinel_completed"
  printf 'COLD_HOST_RESUME_OBSERVED=%s\n' "$cold_host_resume_observed"
  printf 'RENDERER_PROMPT_COMPLETED=%s\n' "$renderer_prompt_completed"
  printf 'RENDERER_TASKS_OBSERVED=%s\n' "$renderer_tasks_observed"
  printf 'RENDERER_SETTINGS_OBSERVED=%s\n' "$renderer_settings_observed"
  printf 'RENDERER_PLUGINS_OBSERVED=%s\n' "$renderer_plugins_observed"
  printf 'RENDERER_SKILLS_OBSERVED=%s\n' "$renderer_skills_observed"
  printf 'RENDERER_LOCAL_SKILL_VISIBLE=%s\n' "$renderer_local_skill_visible"
  printf 'RENDERER_MODEL_SURFACE_OBSERVED=%s\n' "$renderer_model_surface_observed"
  printf 'RENDERER_MODEL_METADATA_MATCHED=%s\n' "$renderer_model_metadata_matched"
  printf 'RENDERER_THREAD_REOPENED=%s\n' "$renderer_thread_reopened"
  printf 'RENDERER_CONTINUATION_COMPLETED=%s\n' "$renderer_continuation_completed"
  printf 'RENDERER_SAME_ROLLOUT_CONTINUATION=%s\n' "$renderer_same_rollout_continuation"
  printf 'COPIED_APP_COLD_STOPPED=%s\n' "$copied_app_cold_stopped"
  printf 'NATIVE_PROJECT_PICKER_EXERCISED=%s\n' "$native_project_picker_exercised"
  printf 'NATIVE_PERMISSION_DECISION_EXERCISED=%s\n' "$native_permission_decision_exercised"
  printf 'NATIVE_WORKTREE_CONTROL_EXERCISED=%s\n' "$native_worktree_control_exercised"
  printf 'PROVIDER_REQUEST_OBSERVED=%s\n' "$provider_request_observed"
  printf 'AUTH_JSON_PRESENT=%s\n' "$auth_json_present"
  printf 'SHELL_SNAPSHOT_ABSENT=%s\n' "$shell_snapshot_absent"
  printf 'PROVIDER_LISTENER_OBSERVED=%s\n' "$provider_listener_observed"
  printf 'GATEWAY_LISTENER_OBSERVED=%s\n' "$gateway_listener_observed"
  printf 'GATEWAY_TERMINAL_OBSERVED=%s\n' "$gateway_terminal_observed"
  printf 'GATEWAY_BODY_LEAK_OBSERVED=%s\n' "$gateway_body_leak_observed"
  printf 'GATEWAY_MISSING_AUTH_REJECTED=%s\n' "$gateway_missing_auth_rejected"
  printf 'GATEWAY_WRONG_AUTH_REJECTED=%s\n' "$gateway_wrong_auth_rejected"
  printf 'UPSTREAM_TOKEN_REPLACED=%s\n' "$upstream_token_replaced"
  printf 'UPSTREAM_TERMINAL_COMPLETED=%s\n' "$upstream_terminal_completed"
  printf 'NAMESPACE_TOOL_CONTINUATION=%s\n' "$namespace_tool_continuation"
  printf 'GATEWAY_TERMINAL_COUNT=%s\n' "$gateway_terminal_count"
  printf 'UPSTREAM_TERMINAL_COUNT=%s\n' "$upstream_terminal_count"
  printf 'GATEWAY_TERMINAL_BASELINE=%s\n' "$gateway_terminal_baseline"
  printf 'UPSTREAM_TERMINAL_BASELINE=%s\n' "$upstream_terminal_baseline"
  printf 'GATEWAY_RENDERER_TERMINAL_DELTA=%s\n' "$gateway_renderer_terminal_delta"
  printf 'UPSTREAM_RENDERER_TERMINAL_DELTA=%s\n' "$upstream_renderer_terminal_delta"
  printf 'GATEWAY_FIRST_RENDERER_TERMINAL_DELTA=%s\n' "$gateway_first_renderer_terminal_delta"
  printf 'UPSTREAM_FIRST_RENDERER_TERMINAL_DELTA=%s\n' "$upstream_first_renderer_terminal_delta"
  printf 'GATEWAY_CONTINUATION_TERMINAL_DELTA=%s\n' "$gateway_continuation_terminal_delta"
  printf 'UPSTREAM_CONTINUATION_TERMINAL_DELTA=%s\n' "$upstream_continuation_terminal_delta"
  printf 'RENDERER_CONTINUATION_TRANSPORT_OBSERVED=%s\n' "$renderer_continuation_transport_observed"
  printf 'MODEL_LIST_ISOLATED=true\n'
  printf 'REMOTE_SOCKET_OBSERVED=%s\n' "$remote_socket_observed"
  printf 'TOKEN_LEAK_OBSERVED=%s\n' "$token_leak_observed"
  printf 'OWNED_PROCESSES_EXITED=%s\n' "$owned_processes_exited"
  printf 'OWNED_LISTENERS_CLOSED=%s\n' "$owned_listeners_closed"
  printf 'APP_ASAR_UNCHANGED=%s\n' "$(test "$app_asar_after" = d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84 && echo true || echo false)"
  printf 'CODEX_UNCHANGED=%s\n' "$(test "$codex_after" = 28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c && echo true || echo false)"
} >"$LOG_DIR/probe-verdict.txt"

printf 'RUN_ROOT=%s\n' "$RUN_ROOT"
printf 'LOG_DIR=%s\n' "$LOG_DIR"
cat "$LOG_DIR/probe-verdict.txt"

test "$account_read_observed" = true
test "$cdp_observer_healthy" = true
if test "$GUI_WORKFLOW" = false; then
  test "$host_sentinel_completed" = true
  test "$cold_host_resume_observed" = true
else
  test "$renderer_prompt_completed" = true
  test "$renderer_tasks_observed" = true
  test "$renderer_settings_observed" = true
  test "$renderer_plugins_observed" = true
  test "$renderer_skills_observed" = true
  test "$renderer_model_surface_observed" = true
  if test "$GUI_COLD_RESUME" = true; then
    test "$copied_app_cold_stopped" = true
    test "$renderer_thread_reopened" = true
    test "$renderer_continuation_completed" = true
    test "$renderer_same_rollout_continuation" = true
  fi
  if test "$GUI_NATIVE_PROJECT_PICKER" = true; then
    test "$native_project_picker_exercised" = true
  fi
fi
test "$provider_request_observed" = true
test "$auth_json_present" = false
test "$shell_snapshot_absent" = true
test "$provider_listener_observed" = true
if test "$ROUTE_MODE" = gateway; then
  test "$gateway_listener_observed" = true
  test "$gateway_terminal_observed" = true
  test "$gateway_body_leak_observed" = false
  test "$gateway_missing_auth_rejected" = true
  test "$gateway_wrong_auth_rejected" = true
  test "$upstream_token_replaced" = true
  test "$upstream_terminal_completed" = true
  test "$namespace_tool_continuation" = true
  if test "$GUI_COLD_RESUME" = true; then
    test "$gateway_renderer_terminal_delta" -ge 2
    test "$upstream_renderer_terminal_delta" -ge 2
    test "$gateway_first_renderer_terminal_delta" -ge 1
    test "$upstream_first_renderer_terminal_delta" -ge 1
    test "$gateway_continuation_terminal_delta" -ge 1
    test "$upstream_continuation_terminal_delta" -ge 1
    test "$renderer_continuation_transport_observed" = true
  fi
fi
test "$remote_socket_observed" = false
test "$token_leak_observed" = false
test "$owned_processes_exited" = true
test "$owned_listeners_closed" = true
test "$app_asar_after" = d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84
test "$codex_after" = 28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c
case "$PROBE_EXPECT" in
  capture) ;;
  no-login) test "$login_wall_observed" = false ;;
  usable-ui) test "$login_wall_observed" = false; test "$main_ui_observed" = true ;;
  renderer-workflow) test "$GUI_WORKFLOW" = true; test "$login_wall_observed" = false; test "$main_ui_observed" = true ;;
  renderer-cold-resume) test "$GUI_COLD_RESUME" = true; test "$login_wall_observed" = false; test "$main_ui_observed" = true ;;
  renderer-native-project) test "$GUI_NATIVE_PROJECT_PICKER" = true; test "$login_wall_observed" = false; test "$main_ui_observed" = true ;;
  *) echo "unknown PROBE_EXPECT: $PROBE_EXPECT" >&2; exit 64 ;;
esac
