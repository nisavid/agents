#!/usr/bin/env zsh
set -euo pipefail

repo_dir="${0:A:h:h}"
tmp_dir="$(mktemp -d)"
trap '/bin/rm -rf -- "$tmp_dir"' EXIT

service_lib="$tmp_dir/hindsight-embed-service.zsh"
/usr/bin/sed '/^main "\$@"$/d' \
  "$repo_dir/bin/hindsight-embed-service" >"$service_lib"
source "$service_lib"

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
  lease_ready="$tmp_dir/lease-ready"
  lease_release="$tmp_dir/lease-release"
  (
    zmodload zsh/system
    integer held_descriptor
    zsystem flock -f held_descriptor -t 1 "$lease_file"
    touch "$lease_ready"
    while [[ ! -e "$lease_release" ]]; do sleep 0.01; done
    zsystem flock -u "$held_descriptor"
  ) &
  holder_pid=$!
  for _ in {1..100}; do [[ -e "$lease_ready" ]] && break; sleep 0.01; done
  [[ -e "$lease_ready" ]]
  integer proof_descriptor
  exec {proof_descriptor}<"$lease_file"
  HINDSIGHT_EMBED_MAINTENANCE_LEASE_DESCRIPTOR="$proof_descriptor" \
    validate_inherited_maintenance_lease
  exec {proof_descriptor}>&-
  touch "$lease_release"
  wait "$holder_pid"
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
