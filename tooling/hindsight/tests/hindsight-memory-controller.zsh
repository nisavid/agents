#!/usr/bin/env zsh
set -euo pipefail
unsetopt BG_NICE

repo_dir="${0:A:h:h}"
tmp_dir="$(mktemp -d)"
broker_pid=""
supervisor_pid=""
shutdown_failure_pid=""
typeset -A child_identities=()
typeset -A child_job_tokens=()
typeset -g CHILD_JOB_STATE=""

child_job_state() {
  emulate -L zsh
  zmodload zsh/parameter || return 1
  local pid="$1" state field member job_status
  CHILD_JOB_STATE=""
  for state in "${(@v)jobstates}"; do
    for field in "${(@s.:.)state}"; do
      [[ "$field" == <->=* ]] || continue
      member="${field%%=*}"
      job_status="${field#*=}"
      if [[ "$member" == "$pid" ]]; then
        CHILD_JOB_STATE="$job_status"
        return 0
      fi
    done
  done
  return 1
}

child_job_present() {
  child_job_state "$1"
}

child_job_running() {
  emulate -L zsh
  child_job_state "$1" || return 1
  [[ "$CHILD_JOB_STATE" == running || "$CHILD_JOB_STATE" == suspended ]]
}

track_child() {
  emulate -L zsh
  local pid="$1" token
  child_job_present "$pid" || return 1
  token="${pid}:${RANDOM}:${RANDOM}:${SECONDS}"
  child_identities[$pid]="$token"
  child_job_tokens[$pid]="$token"
}

child_identity_matches() {
  emulate -L zsh
  local pid="$1"
  child_token_matches "$pid" || return 1
  child_job_running "$pid"
}

child_token_matches() {
  emulate -L zsh
  local pid="$1" expected current
  expected="${child_identities[$pid]-}"
  [[ -n "$expected" ]] || return 1
  current="${child_job_tokens[$pid]-}"
  [[ "$current" == "$expected" ]]
}

clear_child() {
  emulate -L zsh
  local pid="$1"
  unset "child_identities[$pid]"
  unset "child_job_tokens[$pid]"
  [[ "$broker_pid" != "$pid" ]] || broker_pid=""
  [[ "$supervisor_pid" != "$pid" ]] || supervisor_pid=""
  [[ "$shutdown_failure_pid" != "$pid" ]] || shutdown_failure_pid=""
}

cleanup_child() {
  emulate -L zsh
  unsetopt ERR_EXIT
  local pid="$1"
  local attempt
  [[ -n "$pid" ]] || return 0
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    wait "$pid" >/dev/null 2>&1 || true
    clear_child "$pid"
    return 0
  fi
  if ! child_token_matches "$pid"; then
    clear_child "$pid"
    return 0
  fi
  if ! child_job_running "$pid"; then
    wait "$pid" >/dev/null 2>&1 || true
    clear_child "$pid"
    return 0
  fi
  kill -TERM "$pid" >/dev/null 2>&1 || true
  for attempt in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      wait "$pid" >/dev/null 2>&1 || true
      clear_child "$pid"
      return 0
    fi
    child_token_matches "$pid" || {
      clear_child "$pid"
      return 0
    }
    if ! child_job_running "$pid"; then
      wait "$pid" >/dev/null 2>&1 || true
      clear_child "$pid"
      return 0
    fi
    sleep 0.05
  done
  child_token_matches "$pid" || {
    clear_child "$pid"
    return 0
  }
  if ! child_job_running "$pid"; then
    wait "$pid" >/dev/null 2>&1 || true
    clear_child "$pid"
    return 0
  fi
  kill -KILL "$pid" >/dev/null 2>&1 || true
  for attempt in {1..40}; do
    child_job_running "$pid" || break
    sleep 0.05
  done
  if child_job_running "$pid"; then
    print -ru2 -- "hindsight-memory-controller: child ${pid} did not stop after SIGKILL"
    return 1
  fi
  wait "$pid" >/dev/null 2>&1 || true
  clear_child "$pid"
}

trap '
  cleanup_child "$broker_pid" || true
  cleanup_child "$supervisor_pid" || true
  cleanup_child "$shutdown_failure_pid" || true
  rm -rf -- "$tmp_dir"
' EXIT

fail() {
  print -ru2 -- "hindsight-memory-controller: $*"
  exit 1
}

sleep 60 &
identity_guard_pid=$!
broker_pid="$identity_guard_pid"
track_child "$identity_guard_pid" || fail "could not track identity-guard child job"
zsh -c 'sleep 60' 999999 &
argv_decoy_pid=$!
if child_job_present 999999; then
  kill -TERM "$argv_decoy_pid" >/dev/null 2>&1 || true
  wait "$argv_decoy_pid" >/dev/null 2>&1 || true
  fail "job membership matched a PID appearing only in another job argv"
fi
kill -TERM "$argv_decoy_pid" >/dev/null 2>&1 || true
wait "$argv_decoy_pid" >/dev/null 2>&1 || true
identity_guard_expected="${child_identities[$identity_guard_pid]}"
child_job_tokens[$identity_guard_pid]="mismatched-association"
cleanup_child "$identity_guard_pid"
kill -0 "$identity_guard_pid" >/dev/null 2>&1 ||
  fail "EXIT cleanup signaled a child after its tracked association changed"
broker_pid="$identity_guard_pid"
child_identities[$identity_guard_pid]="$identity_guard_expected"
child_job_tokens[$identity_guard_pid]="$identity_guard_expected"
cleanup_child "$identity_guard_pid"
[[ -z "$broker_pid" ]] || fail "identity-guard child cleanup was incomplete"

typeset -g REAP_STATUS=0
reap_bounded() {
  local pid="$1" label="$2" send_term="${3:-0}"
  if (( send_term != 0 )) && kill -0 "$pid" >/dev/null 2>&1; then
    child_identity_matches "$pid" || {
      clear_child "$pid"
      REAP_STATUS=0
      return 0
    }
    kill -TERM "$pid" >/dev/null 2>&1 || true
    (( send_term < 2 )) || kill -TERM "$pid" >/dev/null 2>&1 || true
  fi
  for _ in {1..100}; do
    child_job_running "$pid" || break
    sleep 0.05
  done
  if child_job_running "$pid"; then
    if child_identity_matches "$pid"; then
      kill -KILL "$pid" >/dev/null 2>&1 || true
    else
      clear_child "$pid"
      REAP_STATUS=0
      return 0
    fi
  fi
  for _ in {1..100}; do
    child_job_running "$pid" || break
    sleep 0.05
  done
  child_job_running "$pid" && fail "${label} did not stop within the bounded timeout"
  REAP_STATUS=0
  wait "$pid" || REAP_STATUS=$?
  clear_child "$pid"
}

rendered_stack_lib="$repo_dir/lib/hindsight-embed-stack.zsh"
supervisor_root_checks="$(rg -F -c '[[ "$path" == / ]] && break' "$repo_dir/bin/hindsight-embed-supervisor")"
[[ "$supervisor_root_checks" == 1 ]] ||
  fail "supervisor ancestry validation does not terminate immediately after checking root exactly once"

supervisor_acl_dir="$tmp_dir/supervisor-acl-dir"
supervisor_acl_lib="$supervisor_acl_dir/stack.zsh"
supervisor_acl_marker="$tmp_dir/supervisor-acl-sourced"
mkdir "$supervisor_acl_dir"
print -r -- "touch ${(q)supervisor_acl_marker}; exit 0" >"$supervisor_acl_lib"
chmod 600 "$supervisor_acl_lib"
chmod +a 'everyone allow read' "$supervisor_acl_dir"
if HINDSIGHT_EMBED_STACK_LIB="$supervisor_acl_lib" \
  zsh "$repo_dir/bin/hindsight-embed-supervisor" >/dev/null 2>&1; then
  fail "supervisor accepted a stack library beneath an ACL-bearing ancestor"
fi
chmod -N "$supervisor_acl_dir"
[[ ! -e "$supervisor_acl_marker" ]] ||
  fail "supervisor sourced a stack library beneath an ACL-bearing ancestor"

export HINDSIGHT_EMBED_UVX=/usr/bin/true
export HINDSIGHT_EMBED_CONTROL_PORT=7878
export HINDSIGHT_EMBED_CONTROL_HOSTNAME=127.0.0.1
export HINDSIGHT_EMBED_PRIMARY_PROFILE=test-profile
export HINDSIGHT_EMBED_FLEET_PROFILES=test-profile
export HINDSIGHT_EMBED_API_BASE_PORT=7979
export HINDSIGHT_EMBED_UI_BASE_PORT=17979
export HINDSIGHT_EMBED_UI_HOSTNAME=127.0.0.1
export HINDSIGHT_EMBED_PYTHON=/usr/bin/true
export HINDSIGHT_EMBED_STOP_HELPER="$repo_dir/libexec/hindsight-embed-stop-profile-services.py"
export HINDSIGHT_MEMORY_CLI="$repo_dir/bin/hindsight-memory"
export HINDSIGHT_MEMORY_STATE_DIR="$tmp_dir/memory-state"
export HINDSIGHT_MEMORY_BROKER_SOCKET="$tmp_dir/memory-state/broker.sock"
export HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/stack-state"
export HINDSIGHT_EMBED_AUTOSTART_DAEMON=true
export HINDSIGHT_EMBED_AUTOSTART_UI=true
export HINDSIGHT_EMBED_STACK_LABEL=com.example.hindsight.stack
export HINDSIGHT_EMBED_LEGACY_LABEL=com.example.hindsight.legacy
export HINDSIGHT_EMBED_SERVICE_MANIFEST="$tmp_dir/com.example.hindsight.stack.plist"
export HINDSIGHT_EMBED_LEGACY_MANIFEST="$tmp_dir/com.example.hindsight.legacy.plist"
export HINDSIGHT_EMBED_SUPERVISOR=/usr/bin/true
export HINDSIGHT_EMBED_STACK_LIB="$rendered_stack_lib"
export HINDSIGHT_EMBED_SERVICE_LOG="$tmp_dir/supervisor.log"

help_output="$(zsh "$repo_dir/bin/hindsight-embed-service" --help)"
for command in install start restart stop status logs; do
  print -r -- "$help_output" | rg -q "^[[:space:]]+${command}[[:space:]]" ||
    fail "service help lost the ${command} command"
done
if print -r -- "$help_output" | rg -v '^(Usage:|$|Commands:|[[:space:]]+(install|start|restart|stop|status|logs)[[:space:]])' | rg -q .; then
  fail "service help gained an unreviewed operator command"
fi

if env -i HOME="$tmp_dir/unbound-home" PATH=/usr/bin:/bin \
  /bin/zsh "$repo_dir/bin/hindsight-embed-service" status \
  >"$tmp_dir/unbound-service.out" 2>&1; then
  fail "service accepted inferred consumer bindings"
fi
rg -q 'missing required consumer bindings' "$tmp_dir/unbound-service.out" ||
  fail "service did not explain its required consumer bindings"

(
  export HOME="$tmp_dir/status-home"
  export HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/status-state"
  export HINDSIGHT_EMBED_PROFILE="test-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="test-profile"
  mkdir -p "$HOME/.hindsight/profiles"
  touch "$HOME/.hindsight/profiles/test-profile.env"
  source "$rendered_stack_lib"
  for function_name in \
    hindsight_stack_broker_status \
    hindsight_stack_broker_start \
    hindsight_stack_broker_stop \
    hindsight_stack_enabled_profiles \
    hindsight_stack_select_profile \
    hindsight_stack_validate_fleet \
    hindsight_stack_wait_broker; do
    (( ${+functions[$function_name]} )) || fail "stack library is missing ${function_name}"
  done

  hindsight_stack_control_status() { return 0 }
  hindsight_stack_daemon_status() { return 0 }
  hindsight_stack_ui_status() { return 0 }
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 0 }
  status_report="$(hindsight_stack_status_report)"
  print -r -- "$status_report" | rg -q '^broker: healthy .*broker\.sock' ||
    fail "status report does not add broker health"
  print -r -- "$status_report" | rg -q '^daemon: healthy ' ||
    fail "status report lost profile health"
  print -r -- "$status_report" | rg -q '^fleet: healthy \(1 enabled profile\)$' ||
    fail "status report does not add fleet health"
  print -r -- "$status_report" | rg -q '^profile .* slot=0 .*sidecars=none' ||
    fail "status report does not expose stable profile slot and sidecar readiness"
)

(
  export HOME="$tmp_dir/lifecycle-home"
  export HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/lifecycle-state"
  export HINDSIGHT_EMBED_PROFILE_SLOT_DIR="$HINDSIGHT_EMBED_STATE_DIR/profile-slots"
  export HINDSIGHT_EMBED_PROFILE="crash-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="crash-profile"
  mkdir -p "$HOME/.hindsight/profiles" "$HINDSIGHT_EMBED_PROFILE_SLOT_DIR"
  touch "$HOME/.hindsight/profiles/crash-profile.env"
  lock_file="$HINDSIGHT_EMBED_PROFILE_SLOT_DIR/.allocation.lock"
  lock_ready="$tmp_dir/profile-lock-ready"
  : >"$lock_file"
  zsh -c '
    zmodload zsh/system
    zsystem flock -f descriptor "$1"
    touch "$2"
    sleep 60
  ' -- "$lock_file" "$lock_ready" &
  lock_holder_pid=$!
  trap '
    [[ -z "${lock_holder_pid:-}" ]] || kill -TERM "$lock_holder_pid" 2>/dev/null || true
    [[ -z "${lock_holder_pid:-}" ]] || wait "$lock_holder_pid" 2>/dev/null || true
  ' EXIT
  for _ in {1..100}; do
    [[ -e "$lock_ready" ]] && break
    kill -0 "$lock_holder_pid" >/dev/null 2>&1 || break
    sleep 0.01
  done
  [[ -e "$lock_ready" ]] || fail "profile lock holder did not become ready"
  kill -KILL "$lock_holder_pid"
  for _ in {1..100}; do
    child_job_running "$lock_holder_pid" || break
    sleep 0.05
  done
  child_job_running "$lock_holder_pid" &&
    fail "profile lock holder did not stop after SIGKILL"
  wait "$lock_holder_pid" >/dev/null 2>&1 || true
  lock_holder_pid=""
  trap - EXIT

  source "$rendered_stack_lib"
  [[ "$(hindsight_stack_profile_slot crash-profile)" == 0 ]] ||
    fail "profile slot lock was not released after holder crash"

  sidecar_stop="$tmp_dir/sidecar-stop"
  hindsight_stack_sidecar_names() { print -r -- unhealthy-sidecar }
  hindsight_stack_sidecar_port() { print -r -- 18180 }
  hindsight_stack_sidecar_status() { return 1 }
  hindsight_stack_port_listening() { return 0 }
  hindsight_stack_sidecar_command() {
    [[ "$2" == stop ]] && print -r -- "$1" >"$sidecar_stop"
  }
  hindsight_stack_sidecar_running unhealthy-sidecar ||
    fail "sidecar liveness incorrectly followed health readiness"
  hindsight_stack_stop_sidecars
  [[ "$(<"$sidecar_stop")" == unhealthy-sidecar ]] ||
    fail "unhealthy sidecar did not receive a stop command"
)

broker_state="$tmp_dir/broker-state"
broker_socket="$broker_state/broker.sock"
broker_log="$tmp_dir/broker.log"
mkdir -m 700 "$broker_state"
print -r -- '{"pid":999999999,"start_time":"stale-process"}' >"$broker_state/broker.pid"
chmod 600 "$broker_state/broker.pid"
python3 "$repo_dir/bin/hindsight-memory" \
  --state-dir "$broker_state" broker serve \
  --socket "$broker_socket" --profile example --inactive >"$broker_log" 2>&1 &
broker_pid=$!
track_child "$broker_pid" || fail "could not capture inactive broker child identity"
for _ in {1..100}; do
  [[ -S "$broker_socket" ]] && break
  kill -0 "$broker_pid" >/dev/null 2>&1 || break
  sleep 0.05
done
kill -0 "$broker_pid" >/dev/null 2>&1 || {
  sed -n '1,120p' "$broker_log" >&2
  fail "inactive broker exited during startup"
}
identity_pid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="ascii"))["pid"])' \
  "$broker_state/broker.pid")"
identity_start="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="ascii"))["start_time"])' \
  "$broker_state/broker.pid")"
[[ "$identity_pid" == "$broker_pid" ]] || fail "broker PID identity was not rewritten"
[[ "$identity_start" != "stale-process" ]] || fail "broker start identity remained stale"
python3 "$repo_dir/bin/hindsight-memory" \
  --state-dir "$broker_state" broker status --socket "$broker_socket" --profile example --inactive >/dev/null
[[ "$(stat -f '%Lp' "$broker_socket")" == "600" ]] || fail "broker socket is not mode 0600"
python3 "$repo_dir/bin/hindsight-memory" \
  --state-dir "$broker_state" broker stop --socket "$broker_socket" >/dev/null
reap_bounded "$broker_pid" broker
broker_pid=""
[[ ! -e "$broker_socket" ]] || fail "broker socket remains after bounded stop"
[[ ! -e "$broker_state/broker.pid" ]] || fail "broker PID remains after bounded stop"

fake_stack="$tmp_dir/fake-stack.zsh"
cat > "$fake_stack" <<'ZSH'
typeset -g HINDSIGHT_TEST_STACK_SOURCE="${${(%):-%N}:A}"
hindsight_stack_load_config() {
  typeset -g HINDSIGHT_EMBED_PROFILE="test-profile"
  typeset -g HINDSIGHT_EMBED_CONTROL_PORT="7878"
  typeset -g HINDSIGHT_EMBED_API_PORT="7979"
  typeset -g HINDSIGHT_EMBED_UI_PORT="17979"
  typeset -g HINDSIGHT_EMBED_FLEET_PROFILES="test-profile,second-profile"
  typeset -g HINDSIGHT_MEMORY_BROKER_SOCKET="$HINDSIGHT_EMBED_STATE_DIR/broker.sock"
}
hindsight_stack_log() {
  if [[ "$*" == "supervisor started "* ]]; then
    print -r -- "$* stack_source=${HINDSIGHT_TEST_STACK_SOURCE}"
  else
    print -r -- "$*"
  fi
}
hindsight_stack_enabled_profiles() { print -r -- test-profile; print -r -- second-profile }
hindsight_stack_fleet_profiles_csv() { print -r -- test-profile,second-profile }
hindsight_stack_broker_status() { return 0 }
hindsight_stack_initialize_desired_state() {
  [[ -z "${HINDSIGHT_TEST_DESIRED_INITIALIZED:-}" ]] ||
    touch "$HINDSIGHT_TEST_DESIRED_INITIALIZED"
}
hindsight_stack_reconcile_once() {
  [[ "${HINDSIGHT_TEST_RECONCILE_FAIL:-0}" == 1 ]] && return 1
  return 0
}
hindsight_stack_require_current_user() { return 0 }
hindsight_stack_require_runtime_helpers() { return 0 }
hindsight_stack_require_tools() { return 0 }
hindsight_stack_validate_fleet() { return 0 }
hindsight_stack_stop_all() { return 0 }
hindsight_stack_with_lifecycle_lock() {
  local callback="$1"
  shift
  "$callback" "$@"
}
ZSH

supervisor_state="$tmp_dir/supervisor-state"
supervisor_log="$supervisor_state/logs/supervisor.log"
supervisor_desired_initialized="$supervisor_state/desired-initialized"
fake_stack_alias_dir="$tmp_dir/fake-stack-alias"
ln -s "${fake_stack:h}" "$fake_stack_alias_dir"
configured_fake_stack="$fake_stack_alias_dir/${fake_stack:t}"
mkdir -p "${supervisor_log:h}"
HINDSIGHT_EMBED_STACK_LIB="$configured_fake_stack" \
HINDSIGHT_EMBED_STATE_DIR="$supervisor_state" \
HINDSIGHT_EMBED_POLL_SECONDS=1 \
HINDSIGHT_TEST_DESIRED_INITIALIZED="$supervisor_desired_initialized" \
HINDSIGHT_TEST_SECRET="credential-sentinel" \
HINDSIGHT_TEST_PAYLOAD="payload-sentinel" \
zsh "$repo_dir/bin/hindsight-embed-supervisor" >>"$supervisor_log" 2>&1 &
supervisor_pid=$!
track_child "$supervisor_pid" || fail "could not capture supervisor child identity"
start_log=""
for _ in {1..100}; do
  if [[ -s "$supervisor_log" ]]; then
    start_log="$(rg --max-count 1 \
      "^supervisor started .*profiles=test-profile,second-profile.*broker_socket=$supervisor_state/broker.sock" \
      "$supervisor_log" || true)"
    [[ -n "$start_log" ]] && break
  fi
  sleep 0.05
done
[[ -n "$start_log" ]] || fail "supervisor startup record did not become ready"
[[ -e "$supervisor_desired_initialized" ]] ||
  fail "supervisor did not initialize desired component state"
reap_bounded "$supervisor_pid" supervisor 2
supervisor_status=$REAP_STATUS
supervisor_pid=""
[[ "$supervisor_status" == 143 ]] ||
  fail "supervisor TERM exit status was ${supervisor_status}, expected 143"
[[ "$start_log" == *"profiles=test-profile,second-profile"* &&
  "$start_log" == *"broker_socket=$supervisor_state/broker.sock"* ]] ||
  fail "supervisor log omits content-free broker identity"
[[ "$start_log" == *"stack_source=${fake_stack:A}"* ]] ||
  fail "supervisor did not source the pinned canonical stack-library path"
if rg -qi '(credential-sentinel|payload-sentinel|authorization:|bearer[[:space:]]|api[_-]?key)' "$supervisor_log"; then
  fail "supervisor log contains a credential or payload"
fi

for invalid_poll in 0 01 +1 -1 3601; do
  invalid_poll_log="$tmp_dir/invalid-poll-${invalid_poll//[-+]/_}.log"
  if HINDSIGHT_EMBED_STACK_LIB="$fake_stack" \
    HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/invalid-poll-state" \
    HINDSIGHT_EMBED_POLL_SECONDS="$invalid_poll" \
    zsh "$repo_dir/bin/hindsight-embed-supervisor" >"$invalid_poll_log" 2>&1; then
    fail "supervisor accepted invalid poll interval ${invalid_poll}"
  fi
  rg -q 'POLL_SECONDS must be an integer from 1 to 3600' "$invalid_poll_log" ||
    fail "supervisor did not explain its poll interval contract"
done

for invalid_max_failures in 0 01 +1 -1 101; do
  invalid_max_failures_log="$tmp_dir/invalid-max-failures-${invalid_max_failures//[-+]/_}.log"
  if HINDSIGHT_EMBED_STACK_LIB="$fake_stack" \
    HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/invalid-max-failures-state" \
    HINDSIGHT_EMBED_MAX_CONSECUTIVE_FAILURES="$invalid_max_failures" \
    zsh "$repo_dir/bin/hindsight-embed-supervisor" >"$invalid_max_failures_log" 2>&1; then
    fail "supervisor accepted invalid maximum failure count ${invalid_max_failures}"
  fi
  rg -q 'MAX_CONSECUTIVE_FAILURES must be an integer from 1 to 100' "$invalid_max_failures_log" ||
    fail "supervisor did not explain its maximum failure count contract"
done

for invalid_cooldown in 01 -1 3601; do
  invalid_cooldown_log="$tmp_dir/invalid-cooldown-${invalid_cooldown//-/_}.log"
  if HINDSIGHT_EMBED_STACK_LIB="$fake_stack" \
    HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/invalid-cooldown-state" \
    HINDSIGHT_EMBED_START_COOLDOWN_SECONDS="$invalid_cooldown" \
    zsh "$repo_dir/bin/hindsight-embed-supervisor" >"$invalid_cooldown_log" 2>&1; then
    fail "supervisor accepted invalid start cooldown ${invalid_cooldown}"
  fi
  rg -q 'START_COOLDOWN_SECONDS must be a canonical integer from 0 to 3600' "$invalid_cooldown_log" ||
    fail "supervisor did not explain its canonical cooldown contract"
done

failure_log="$tmp_dir/reconcile-failure.log"
if HINDSIGHT_EMBED_STACK_LIB="$fake_stack" \
  HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/reconcile-failure-state" \
  HINDSIGHT_EMBED_POLL_SECONDS=1 \
  HINDSIGHT_EMBED_MAX_CONSECUTIVE_FAILURES=2 \
  HINDSIGHT_TEST_RECONCILE_FAIL=1 \
  zsh "$repo_dir/bin/hindsight-embed-supervisor" >"$failure_log" 2>&1; then
  fail "supervisor treated repeated reconcile failure as success"
fi
rg -q 'reconcile degraded consecutive_failures=1' "$failure_log" ||
  fail "supervisor did not log degraded reconciliation"
rg -q 'fatal: reconcile failed 2 consecutive times' "$failure_log" ||
  fail "supervisor did not apply its bounded failure policy"
[[ "$(rg -c '^stopping stack$' "$failure_log")" == 1 ]] ||
  fail "supervisor EXIT cleanup was not idempotent"

shutdown_failure_stack="$tmp_dir/shutdown-failure-stack.zsh"
/usr/bin/sed 's/hindsight_stack_stop_all() { return 0 }/hindsight_stack_stop_all() { return 7 }/' \
  "$fake_stack" > "$shutdown_failure_stack"
shutdown_failure_log="$tmp_dir/shutdown-failure.log"
HINDSIGHT_EMBED_STACK_LIB="$shutdown_failure_stack" \
HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/shutdown-failure-state" \
HINDSIGHT_EMBED_POLL_SECONDS=1 \
zsh "$repo_dir/bin/hindsight-embed-supervisor" >"$shutdown_failure_log" 2>&1 &
shutdown_failure_pid=$!
track_child "$shutdown_failure_pid" || fail "could not capture shutdown-failure supervisor child identity"
for _ in {1..100}; do
  rg -q '^supervisor started ' "$shutdown_failure_log" 2>/dev/null && break
  sleep 0.05
done
rg -q '^supervisor started ' "$shutdown_failure_log" ||
  fail "shutdown-failure supervisor did not start"
reap_bounded "$shutdown_failure_pid" shutdown-failure-supervisor 1
shutdown_failure_status=$REAP_STATUS
shutdown_failure_pid=""
[[ "$shutdown_failure_status" == 1 ]] ||
  fail "supervisor discarded shutdown failure status ${shutdown_failure_status}"
rg -q '^stack stop failed$' "$shutdown_failure_log" ||
  fail "supervisor omitted shutdown failure reporting"

print -r -- "hindsight-memory-controller: PASS"
