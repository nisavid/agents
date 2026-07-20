#!/usr/bin/env zsh
set -euo pipefail
unsetopt BG_NICE

repo_dir="${0:A:h:h}"
tmp_dir="$(mktemp -d)"
trap '/bin/rm -rf -- "$tmp_dir"' EXIT

service_lib="$tmp_dir/hindsight-embed-service.zsh"
/usr/bin/sed '/^main "\$@"$/d' \
  "$repo_dir/bin/hindsight-embed-service" >"$service_lib"
source "$service_lib"

stage_suffix_source="$tmp_dir/stage-suffix-source.plist"
stage_suffix_state="$tmp_dir/stage-suffix-state"
/usr/bin/plutil -create xml1 "$stage_suffix_source"
stage_suffix_path="$(
  STATE_DIR="$stage_suffix_state"
  validate_trusted_artifact() { return 0 }
  validate_manifest_contract() { return 0 }
  stage_validated_manifest \
    "$stage_suffix_source" "test manifest" stack /usr/bin/true current
)"
stage_suffix_manifest_dir="$stage_suffix_state/staged-manifests"
stage_suffix_parent="${stage_suffix_path%/*}"
stage_suffix_name="${stage_suffix_path##*/}"
stage_suffix_valid=0
stage_suffix_safe=0
[[ "$stage_suffix_parent" == "$stage_suffix_manifest_dir" &&
  -f "$stage_suffix_path" &&
  ! -L "$stage_suffix_path" ]] &&
  stage_suffix_safe=1
[[ "$stage_suffix_safe" -eq 1 &&
  "$stage_suffix_name" == *.plist &&
  "$stage_suffix_path" != "$stage_suffix_source" ]] &&
  stage_suffix_valid=1
if (( stage_suffix_safe )); then
  /usr/bin/chflags nouchg "$stage_suffix_path"
  /bin/chmod 600 "$stage_suffix_path"
  /bin/rm -f "$stage_suffix_path"
fi
(( stage_suffix_valid )) || {
  print -ru2 -- "staged launchd manifest path does not end in .plist"
  exit 1
}

bootstrap_retry_events="$tmp_dir/bootstrap-retry-events"
if ! (
  integer attempts=0
  manifest_label() { print -r -- stack }
  launchctl_bootstrap_manifest() {
    print -r -- bootstrap >>"$bootstrap_retry_events"
    (( ++attempts >= 3 )) && return 0
    return 5
  }
  launchctl_bootstrap_retry_delay() {
    print -r -- delay >>"$bootstrap_retry_events"
  }
  bootstrap_manifest "$tmp_dir/staged.plist" stack
); then
  print -ru2 -- "service bootstrap did not retry transient launchctl error 5"
  exit 1
fi
[[ "$(paste -sd, - <"$bootstrap_retry_events")" == \
  bootstrap,delay,bootstrap,delay,bootstrap ]] || {
  /bin/cat "$bootstrap_retry_events" >&2
  print -ru2 -- "service bootstrap retry sequence is not bounded and ordered"
  exit 1
}

bootstrap_exhausted_events="$tmp_dir/bootstrap-exhausted-events"
bootstrap_exhausted_result="$tmp_dir/bootstrap-exhausted-result"
(
  set +e
  manifest_label() { print -r -- stack }
  launchctl_bootstrap_manifest() {
    print -r -- bootstrap >>"$bootstrap_exhausted_events"
    return 5
  }
  launchctl_bootstrap_retry_delay() {
    print -r -- delay >>"$bootstrap_exhausted_events"
  }
  bootstrap_manifest "$tmp_dir/staged.plist" stack
  print -r -- "$?" >"$bootstrap_exhausted_result"
) &
bootstrap_exhausted_pid=$!
integer bootstrap_watchdog_attempt
for bootstrap_watchdog_attempt in {1..100}; do
  /bin/kill -0 "$bootstrap_exhausted_pid" 2>/dev/null || break
  /bin/sleep 0.01
done
if /bin/kill -0 "$bootstrap_exhausted_pid" 2>/dev/null; then
  /bin/kill -TERM "$bootstrap_exhausted_pid" 2>/dev/null || true
  wait "$bootstrap_exhausted_pid" 2>/dev/null || true
  print -ru2 -- "service bootstrap retry exhaustion timed out"
  exit 1
fi
wait "$bootstrap_exhausted_pid" 2>/dev/null || true
if [[ ! -r "$bootstrap_exhausted_result" ]] ||
  [[ "$(<"$bootstrap_exhausted_result")" == 0 ]]; then
  print -ru2 -- "service bootstrap accepted exhausted transient retries"
  exit 1
fi
bootstrap_exhausted_expected="$tmp_dir/bootstrap-exhausted-expected"
integer bootstrap_expected_attempt
for bootstrap_expected_attempt in {1..119}; do
  print -r -- bootstrap >>"$bootstrap_exhausted_expected"
  print -r -- delay >>"$bootstrap_exhausted_expected"
done
print -r -- bootstrap >>"$bootstrap_exhausted_expected"
/usr/bin/cmp -s "$bootstrap_exhausted_expected" "$bootstrap_exhausted_events" || {
  /bin/cat "$bootstrap_exhausted_events" >&2
  print -ru2 -- "service bootstrap retries exceeded their bounded contract"
  exit 1
}

run_with_service_lifecycle_lock() {
  local callback="$1"
  shift
  "$callback" "$@"
}

exercise_start() {
  local events="$1"
  (
    is_loaded() { return 1 }
    preflight_launchd_service() { print -r -- preflight >>"$events" }
    stage_validated_manifest() {
      print -r -- stage >>"$events"
      print -r -- "$tmp_dir/staged.plist"
    }
    hindsight_stack_reset_desired_state() { print -r -- reset >>"$events" }
    load_launchd_service() { print -r -- load >>"$events" }
    persist_service_manifest_snapshot() { print -r -- persist >>"$events" }
    start_launchd_service
  )
}

start_events="$tmp_dir/start-events"
exercise_start "$start_events"
[[ "$(paste -sd, - <"$start_events")" == preflight,stage,reset,load,persist ]] || {
  /bin/cat "$start_events" >&2
  print -ru2 -- "service start did not reset desired state before loading"
  exit 1
}

install_events="$tmp_dir/install-events"
(
  is_loaded() { return 1 }
  preflight_launchd_service() { print -r -- preflight >>"$install_events" }
  stage_validated_manifest() {
    print -r -- stage >>"$install_events"
    print -r -- "$tmp_dir/staged.plist"
  }
  hindsight_stack_reset_desired_state() { print -r -- reset >>"$install_events" }
  bootout_if_loaded() { print -r -- bootout >>"$install_events" }
  load_launchd_service() { print -r -- load >>"$install_events" }
  persist_service_manifest_snapshot() { print -r -- persist >>"$install_events" }
  retire_legacy_plist() { print -r -- retire >>"$install_events" }
  install_service
)
[[ "$(paste -sd, - <"$install_events")" == \
  preflight,stage,reset,bootout,load,persist,retire ]] || {
  /bin/cat "$install_events" >&2
  print -ru2 -- "service install did not reset desired state before launchd mutation"
  exit 1
}

restart_events="$tmp_dir/restart-events"
(
  is_loaded() { return 1 }
  preflight_launchd_service() { print -r -- preflight >>"$restart_events" }
  stage_validated_manifest() {
    print -r -- stage >>"$restart_events"
    print -r -- "$tmp_dir/staged.plist"
  }
  bootout_if_loaded() { print -r -- bootout >>"$restart_events" }
  hindsight_stack_stop_all() { print -r -- stop >>"$restart_events" }
  hindsight_stack_reset_desired_state() { print -r -- reset >>"$restart_events" }
  load_launchd_service() { print -r -- load >>"$restart_events" }
  persist_service_manifest_snapshot() { print -r -- persist >>"$restart_events" }
  hindsight_stack_with_lifecycle_lock() {
    local callback="$1"
    shift
    "$callback" "$@"
  }
  restart_service
)
[[ "$(paste -sd, - <"$restart_events")" == \
  preflight,stage,bootout,stop,reset,load,persist ]] || {
  /bin/cat "$restart_events" >&2
  print -ru2 -- "service restart did not perform a clean stop, reset, and load"
  exit 1
}

failed_stage_events="$tmp_dir/failed-stage-events"
if (
  STACK_LABEL=stack
  LEGACY_LABEL=legacy
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] }
  preflight_launchd_service() { print -r -- preflight >>"$failed_stage_events" }
  snapshot_service_manifest() {
    print -r -- snapshot >>"$failed_stage_events"
    print -r -- "$tmp_dir/rollback.plist"
  }
  wait_for_manifest_stack_health() { print -r -- healthy >>"$failed_stage_events" }
  stage_validated_manifest() { print -r -- stage >>"$failed_stage_events"; return 1 }
  restore_loaded_stack_health() { print -r -- restore >>"$failed_stage_events" }
  bootout_if_loaded() { print -r -- bootout >>"$failed_stage_events" }
  restart_service
) >/dev/null 2>&1; then
  print -ru2 -- "service restart accepted a failed manifest staging attempt"
  exit 1
fi
[[ "$(paste -sd, - <"$failed_stage_events")" == preflight,snapshot,healthy,stage ]] || {
  /bin/cat "$failed_stage_events" >&2
  print -ru2 -- "service restart mutated a healthy stack after staging failed"
  exit 1
}

rollback_events="$tmp_dir/rollback-events"
(
  cleanup_failed_replacement() { print -r -- cleanup >>"$rollback_events" }
  manifest_program_argument() { print -r -- /usr/bin/true }
  validate_trusted_artifact() { return 0 }
  stage_validated_manifest() {
    print -r -- stage >>"$rollback_events"
    print -r -- "$tmp_dir/staged-rollback.plist"
  }
  bootstrap_manifest() { print -r -- bootstrap >>"$rollback_events" }
  is_loaded() { return 0 }
  wait_for_manifest_stack_health() { print -r -- healthy >>"$rollback_events" }
  persist_service_manifest_snapshot() { print -r -- persist >>"$rollback_events" }
  restore_loaded_stack_health "$tmp_dir/rollback.plist"
)
[[ "$(paste -sd, - <"$rollback_events")" == cleanup,stage,bootstrap,healthy,persist ]] || {
  /bin/cat "$rollback_events" >&2
  print -ru2 -- "healthy rollback restore did not persist the durable last-known-good manifest"
  exit 1
}

failed_replacement_events="$tmp_dir/failed-replacement-events"
(
  bootout_if_loaded() { print -r -- bootout >> "$failed_replacement_events" }
  hindsight_stack_stop_all() { print -r -- stop-all >> "$failed_replacement_events" }
  hindsight_stack_with_lifecycle_lock() {
    local callback="$1"
    shift
    "$callback" "$@"
  }
  cleanup_failed_replacement
)
[[ "$(paste -sd, - <"$failed_replacement_events")" == bootout,stop-all ]] || {
  print -ru2 -- "failed replacement cleanup did not unload launchd and stop residual children"
  exit 1
}

stop_lock_events="$tmp_dir/stop-lock-events"
(
  run_with_service_maintenance_lease() {
    print -r -- maintenance >>"$stop_lock_events"
    "$@"
  }
  run_with_service_command_lock() {
    print -r -- command >>"$stop_lock_events"
    "$@"
  }
  validate_inherited_maintenance_lease() { print -r -- proof >>"$stop_lock_events" }
  stop_service() { print -r -- stop >>"$stop_lock_events" }
  run_stop_command external
  HINDSIGHT_EMBED_MAINTENANCE_LEASE_HELD=1 run_stop_command inherited
)
[[ "$(paste -sd, - <"$stop_lock_events")" == \
  maintenance,command,stop,proof,command,stop ]] || {
  /bin/cat "$stop_lock_events" >&2
  print -ru2 -- "service stop did not serialize externally and avoid lease re-entry internally"
  exit 1
}
if (
  HINDSIGHT_EMBED_MAINTENANCE_LEASE_HELD=1
  unset HINDSIGHT_EMBED_MAINTENANCE_LEASE_DESCRIPTOR
  run_stop_command inherited
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted the internal stop path without descriptor proof"
  exit 1
fi

lease_state="$tmp_dir/lease-state"
mkdir -m 700 "$lease_state"
lease_file="$lease_state/.maintenance.lock"
: >"$lease_file"
chmod 600 "$lease_file"
(
  STATE_DIR="$lease_state"
  integer lease_descriptor
  exec {lease_descriptor}<>"$lease_file"
  /usr/bin/python3 -I -c \
    'import fcntl, sys; fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX)' \
    "$lease_descriptor"
  HINDSIGHT_EMBED_MAINTENANCE_LEASE_DESCRIPTOR="$lease_descriptor" \
    validate_inherited_maintenance_lease
  /usr/bin/python3 -I -c \
    'import fcntl, sys; fcntl.flock(int(sys.argv[1]), fcntl.LOCK_UN)' \
    "$lease_descriptor"
  exec {lease_descriptor}>&-
)
if (
  STATE_DIR="$lease_state"
  exec {unlocked_descriptor}<"$lease_file"
  HINDSIGHT_EMBED_MAINTENANCE_LEASE_DESCRIPTOR="$unlocked_descriptor" \
    validate_inherited_maintenance_lease
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted an inherited descriptor without a held lease"
  exit 1
fi

help_output="$(zsh "$repo_dir/bin/hindsight-embed-service" --help)"
print -r -- "$help_output" | rg -F -q 'restart' || {
  print -ru2 -- "service help does not expose restart"
  exit 1
}

print -r -- "hindsight-embed-service-intent: PASS"
