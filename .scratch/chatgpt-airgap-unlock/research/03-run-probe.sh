#!/bin/sh
# RESEARCH PROBE ONLY. One-command pristine/offline launch and evidence capture.
set -eu

APP="/private/tmp/ChatGPT-Codex-5263-Offgrid-Probe-03.app"
APP_EXEC="$APP/Contents/MacOS/ChatGPT"
PROFILE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/03-probe.sb"
OBSERVER="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/03-proxy-observer.py"
CDP_OBSERVER="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/03-cdp-observer.mjs"
CDP_PORT=49303
PROXY_PORT=49304
PROBE_DURATION_MS="${PROBE_DURATION_MS:-30000}"
PROBE_EXPECT="${PROBE_EXPECT:-capture}"
REAL_HOME="${HOME:?HOME must name the operator home before isolation}"
RUN_ROOT="$(mktemp -d /private/tmp/chatgpt-offline-probe-03.XXXXXX)"
HOME_DIR="$RUN_ROOT/home"
CODEX_DIR="$RUN_ROOT/codex-home"
USER_DATA_DIR="$RUN_ROOT/electron-user-data"
TMP_DIR="$RUN_ROOT/tmp"
LOG_DIR="$RUN_ROOT/logs"

case "$(/usr/bin/python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$APP")" in
  /private/tmp/ChatGPT-Codex-5263-Offgrid-Probe-03.app) ;;
  *) echo "refusing non-disposable app path" >&2; exit 64 ;;
esac
test -x "$APP_EXEC"
test ! -L "$APP"
! /usr/sbin/lsof -nP -iTCP:"$CDP_PORT" -sTCP:LISTEN >/dev/null 2>&1
! /usr/sbin/lsof -nP -iTCP:"$PROXY_PORT" -sTCP:LISTEN >/dev/null 2>&1

mkdir -p "$HOME_DIR" "$CODEX_DIR" "$USER_DATA_DIR" "$TMP_DIR" "$LOG_DIR"
chmod 700 "$HOME_DIR" "$CODEX_DIR" "$USER_DATA_DIR" "$TMP_DIR" "$LOG_DIR"

/usr/bin/python3 "$OBSERVER" "$PROXY_PORT" >"$LOG_DIR/proxy.jsonl" 2>"$LOG_DIR/proxy.stderr" &
proxy_pid=$!
app_pid=""
cleanup() {
  if test -n "$app_pid"; then
    kill -TERM "$app_pid" 2>/dev/null || true
  fi
  kill "$proxy_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

printf '%s\n' "$RUN_ROOT" >"$LOG_DIR/run-root.txt"
cd "$RUN_ROOT"
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
  HTTP_PROXY="http://127.0.0.1:$PROXY_PORT" \
  HTTPS_PROXY="http://127.0.0.1:$PROXY_PORT" \
  ALL_PROXY="http://127.0.0.1:$PROXY_PORT" \
  NO_PROXY="" \
  ELECTRON_ENABLE_LOGGING=1 \
  RUST_LOG=info \
  /usr/bin/sandbox-exec -f "$PROFILE" -D "REAL_HOME=$REAL_HOME" "$APP_EXEC" \
    --no-sandbox \
    --user-data-dir="$USER_DATA_DIR" \
    --remote-debugging-port="$CDP_PORT" \
    --enable-logging=stderr \
    --v=1 \
    >"$LOG_DIR/app.stdout" 2>"$LOG_DIR/app.stderr" &
app_pid=$!

sleep 1
/bin/ps -axo pid,ppid,state,etime,command | /usr/bin/grep -E "PID|ChatGPT-Codex-5263-Offgrid-Probe-03|$RUN_ROOT" | /usr/bin/grep -v grep >"$LOG_DIR/processes-01s.txt" || true

node "$CDP_OBSERVER" "$CDP_PORT" "$PROBE_DURATION_MS" >"$LOG_DIR/cdp.jsonl" 2>"$LOG_DIR/cdp.stderr" || true

/bin/ps -axo pid,ppid,state,etime,command | /usr/bin/grep -E "PID|ChatGPT-Codex-5263-Offgrid-Probe-03|$RUN_ROOT" | /usr/bin/grep -v grep >"$LOG_DIR/processes-final.txt" || true
for pid in $(/bin/ps -axo pid=,command= | /usr/bin/awk -v app="$APP" -v root="$RUN_ROOT" 'index($0, app) || index($0, root) {print $1}'); do
  /usr/sbin/lsof -nP -p "$pid" >>"$LOG_DIR/lsof-final.txt" 2>/dev/null || true
done

kill -TERM "$app_pid" 2>/dev/null || true
sleep 3
kill -KILL "$app_pid" 2>/dev/null || true
wait "$app_pid" 2>/dev/null || true
app_pid=""

/usr/bin/find "$RUN_ROOT" -type f -o -type d | /usr/bin/sort >"$LOG_DIR/files-final.txt"
/usr/bin/find "$RUN_ROOT" -type f -exec /usr/bin/stat -f '%m %z %N' {} \; | /usr/bin/sort >"$LOG_DIR/file-stats-final.txt"
login_wall_observed=false
if /usr/bin/grep -Fq 'Sign in to ChatGPT Continue to sign in Sign in another way Sign up' "$LOG_DIR/cdp.jsonl"; then
  login_wall_observed=true
fi
account_read_observed=false
if /usr/bin/grep -Fq 'method=account/read' "$LOG_DIR/app.stdout"; then
  account_read_observed=true
fi
auth_json_present=false
if test -e "$CODEX_DIR/auth.json"; then
  auth_json_present=true
fi
{
  printf 'LOGIN_WALL_OBSERVED=%s\n' "$login_wall_observed"
  printf 'ACCOUNT_READ_OBSERVED=%s\n' "$account_read_observed"
  printf 'AUTH_JSON_PRESENT=%s\n' "$auth_json_present"
} >"$LOG_DIR/probe-verdict.txt"
printf 'RUN_ROOT=%s\n' "$RUN_ROOT"
printf 'LOG_DIR=%s\n' "$LOG_DIR"
cat "$LOG_DIR/probe-verdict.txt"

test "$account_read_observed" = true
test "$auth_json_present" = false
case "$PROBE_EXPECT" in
  capture) ;;
  login-wall) test "$login_wall_observed" = true ;;
  no-login) test "$login_wall_observed" = false ;;
  *) echo "unknown PROBE_EXPECT: $PROBE_EXPECT" >&2; exit 64 ;;
esac
