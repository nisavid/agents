#!/usr/bin/env zsh
set -euo pipefail

repo_dir="${0:A:h:h}"
events="$(mktemp)"
trap '/bin/rm -f -- "$events"' EXIT

for lifecycle_function in \
  hindsight_stack_reconcile_once \
  hindsight_stack_start_all; do
  : >"$events"
  lifecycle_status=0
  (
    source "$repo_dir/lib/hindsight-embed-stack.zsh"
    hindsight_stack_load_config() { return 0 }
    hindsight_stack_preflight_runtime_credentials() { return 0 }
    hindsight_stack_require_current_user() { return 0 }
    hindsight_stack_require_tools() { return 0 }
    hindsight_stack_require_runtime_helpers() { return 0 }
    hindsight_stack_validate_fleet() { return 0 }
    hindsight_stack_initialize_desired_state() { return 0 }
    hindsight_stack_reconcile_harness_authority() {
      print -r -- authority >>"$events"
      return 23
    }
    hindsight_stack_disable_harness_authority() {
      print -r -- disable >>"$events"
    }
    hindsight_stack_record_harness_authority() {
      print -r -- post >>"$events"
    }
    hindsight_stack_runtime_active() {
      print -r -- runtime >>"$events"
      return 1
    }
    hindsight_stack_reconcile_broker() {
      print -r -- broker >>"$events"
    }
    hindsight_stack_reconcile_control() {
      print -r -- control >>"$events"
    }
    hindsight_stack_reconcile_profile() {
      print -r -- profile >>"$events"
    }
    hindsight_stack_start_broker_dependency() {
      print -r -- broker >>"$events"
    }
    hindsight_stack_start_control_dependency() {
      print -r -- control >>"$events"
    }
    hindsight_stack_start_profile() {
      print -r -- profile >>"$events"
    }
    hindsight_stack_for_each_profile() {
      if [[ "$1" == hindsight_stack_daemon_desired_running ]]; then
        return 0
      fi
      "$1"
    }
    "$lifecycle_function"
  ) >/dev/null 2>&1 || lifecycle_status=$?
  (( lifecycle_status == 0 )) &&
    [[ "$(<"$events")" == \
      $'authority\ndisable\nruntime\nbroker\ncontrol\nprofile\npost' ]] || {
    print -ru2 -- "${lifecycle_function} did not recover dependencies before restoring fail-closed harness authority"
    exit 1
  }
done

reconciler_scopes="$(mktemp)"
trap '/bin/rm -f -- "$events" "$reconciler_scopes"' EXIT
(
  source "$repo_dir/lib/hindsight-embed-stack.zsh"
  HINDSIGHT_MEMORY_HARNESS_RECONCILER=/bin/true
  HINDSIGHT_MEMORY_HARNESS_RECONCILE_CONFIG=/private/reconcile.json
  HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=30
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_validate_trusted_executable() { return 0 }
  hindsight_stack_run_bounded_with_credential_scope() {
    print -r -- "$1:${4}" >>"$reconciler_scopes"
  }
  hindsight_stack_run_harness_reconciler pre-start
  hindsight_stack_run_harness_reconciler post-start
  hindsight_stack_run_harness_reconciler disable
)
[[ "$(<"$reconciler_scopes")" == \
  $'none:pre-start\ncontroller:post-start\nnone:disable' ]] || {
  print -ru2 -- "harness reconciliation received an incorrect credential scope"
  exit 1
}

controller_credentials="$(
  (
    export HINDSIGHT_MEMORY_INVENTORY=/private/inventory.json
    export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
    export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
    export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
    export TEST_DATA_PLANE_TOKEN=data-plane
    export TEST_MINT_AUTHORITY=mint-authority
    export TEST_UI_ACCESS_KEY=ui-access
    source "$repo_dir/lib/hindsight-embed-stack.zsh"
    hindsight_stack_run_with_credential_scope controller \
      /bin/zsh -f -c \
      'print -r -- "${+TEST_DATA_PLANE_TOKEN}:${+TEST_MINT_AUTHORITY}:${+TEST_UI_ACCESS_KEY}"'
  )
)"
[[ "$controller_credentials" == "1:0:0" ]] || {
  print -ru2 -- "controller reconciliation retained excess credentials"
  exit 1
}

print -r -- "hindsight-embed-stack-authority-recovery: PASS"
