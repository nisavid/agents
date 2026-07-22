#!/usr/bin/env zsh
set -euo pipefail
unsetopt BG_NICE

repo_dir="${0:A:h:h}"
tmp_dir="$(mktemp -d)"
typeset -A fixture_pids=()

fixture_process_identity() {
  emulate -L zsh
  /bin/ps -ww -p "$1" -o lstart= 2>/dev/null
}

track_fixture_pid() {
  local identity
  identity="$(fixture_process_identity "$1")" || return 1
  [[ -n "$identity" ]] || return 1
  fixture_pids[$1]="$identity"
}

track_fixture_pid_file() {
  local pid_file="$1" pid
  [[ -s "$pid_file" ]] || return 0
  pid="$(<"$pid_file")"
  [[ "$pid" == <-> ]] || return 0
  track_fixture_pid "$pid" || return 0
}

untrack_fixture_pid() {
  unset "fixture_pids[$1]"
}

cleanup_fixtures() {
  emulate -L zsh
  unsetopt ERR_EXIT
  local pid attempt
  local -a pids=("${(@k)fixture_pids}")
  for pid in "${pids[@]}"; do
    if [[ "$(fixture_process_identity "$pid")" == "${fixture_pids[$pid]}" ]]; then
      kill -TERM "$pid" >/dev/null 2>&1 || true
    else
      untrack_fixture_pid "$pid"
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  for attempt in {1..20}; do
    local any_running=0
    for pid in "${pids[@]}"; do
      [[ -n "${fixture_pids[$pid]:-}" ]] || continue
      if [[ "$(fixture_process_identity "$pid")" == "${fixture_pids[$pid]}" ]]; then
        any_running=1
      else
        untrack_fixture_pid "$pid"
        wait "$pid" >/dev/null 2>&1 || true
      fi
    done
    (( any_running )) || break
    sleep 0.05
  done
  for pid in "${pids[@]}"; do
    [[ -n "${fixture_pids[$pid]:-}" ]] || continue
    if [[ "$(fixture_process_identity "$pid")" == "${fixture_pids[$pid]}" ]]; then
      kill -KILL "$pid" >/dev/null 2>&1 || true
    else
      untrack_fixture_pid "$pid"
      wait "$pid" >/dev/null 2>&1 || true
      continue
    fi
    untrack_fixture_pid "$pid"
    wait "$pid" >/dev/null 2>&1 || true
  done
  /usr/bin/chflags -R nouchg "$tmp_dir" >/dev/null 2>&1 || true
  rm -rf -- "$tmp_dir"
}
trap cleanup_fixtures EXIT

exit_cleanup_pid_file="$tmp_dir/exit-cleanup.pid"
exit_cleanup_ready_file="$tmp_dir/exit-cleanup.ready"
exit_cleanup_status=0
(
  tmp_dir="$tmp_dir/exit-cleanup-state"
  mkdir -p "$tmp_dir"
  typeset -A fixture_pids=()
  trap cleanup_fixtures EXIT
  /bin/zsh -c '
    trap "" TERM
    print -r -- "$$" > "$1"
    touch "$2"
    sleep 60
  ' -- "$exit_cleanup_pid_file" "$exit_cleanup_ready_file" &
  exit_cleanup_pid=$!
  track_fixture_pid "$exit_cleanup_pid"
  for _ in {1..100}; do
    [[ -e "$exit_cleanup_ready_file" ]] && break
    sleep 0.01
  done
  [[ -e "$exit_cleanup_ready_file" ]]
) || exit_cleanup_status=$?
track_fixture_pid_file "$exit_cleanup_pid_file"
(( exit_cleanup_status == 0 )) || {
  print -ru2 -- "EXIT cleanup fixture did not become ready"
  exit 1
}
exit_cleanup_pid="$(<"$exit_cleanup_pid_file")"
if [[ -n "$(fixture_process_identity "$exit_cleanup_pid")" ]]; then
  kill -KILL "$exit_cleanup_pid" >/dev/null 2>&1 || true
  print -ru2 -- "EXIT cleanup left a TERM-ignoring fixture running"
  exit 1
fi
untrack_fixture_pid "$exit_cleanup_pid"

rendered_stack_lib="$repo_dir/lib/hindsight-embed-stack.zsh"

hostile_sleep_dir="$tmp_dir/hostile-sleep"
hostile_sleep_marker="$tmp_dir/hostile-sleep-ran"
mkdir "$hostile_sleep_dir"
cat >"$hostile_sleep_dir/sleep" <<'ZSH'
#!/bin/zsh
print -r -- "${HINDSIGHT_DATA_PLANE_API_KEY:-}:${HINDSIGHT_MINT_AUTHORITY_KEY:-}:${HINDSIGHT_UI_ACCESS_KEY:-}" >"$HINDSIGHT_TEST_HOSTILE_SLEEP_MARKER"
ZSH
chmod 700 "$hostile_sleep_dir/sleep"
(
  source "$rendered_stack_lib"
  export PATH="$hostile_sleep_dir:/usr/bin:/bin"
  export HINDSIGHT_DATA_PLANE_API_KEY=data-canary
  export HINDSIGHT_MINT_AUTHORITY_KEY=mint-canary
  export HINDSIGHT_UI_ACCESS_KEY=ui-canary
  export HINDSIGHT_TEST_HOSTILE_SLEEP_MARKER="$hostile_sleep_marker"
  hindsight_stack_sleep 0.01
)
[[ ! -e "$hostile_sleep_marker" ]] || {
  print -ru2 -- "stack sleep invoked a PATH-resolved child with runtime credentials"
  exit 1
}

stat_target="$tmp_dir/stat-target"
stat_link="$tmp_dir/stat-link"
stat_dangling_link="$tmp_dir/stat-dangling-link"
touch "$stat_target"
chmod 600 "$stat_target"
ln -s "$stat_target" "$stat_link"
ln -s "$tmp_dir/stat-missing" "$stat_dangling_link"
for stat_path in "$stat_link" "$stat_dangling_link"; do
  stat_fields="$({
    source "$rendered_stack_lib"
    hindsight_stack_stat_fields "$stat_path"
  })" || {
    print -ru2 -- "stack stat helper did not report symlink metadata: ${stat_path}"
    exit 1
  }
  stat_mode="${${stat_fields#*:}%%:*}"
  [[ "$stat_mode" != 600 ]] || {
    print -ru2 -- "stack stat helper followed a symlink instead of reporting it: ${stat_path}"
    exit 1
  }
done

rg -F -q 'access_key_resolver' "$repo_dir/README.md" &&
  rg -F -q 'sole authoritative binding' "$repo_dir/README.md" &&
  rg -F -q 'No environment variable, file, inline value, reusable default, or browser bootstrap' "$repo_dir/README.md" || {
  print -ru2 -- "control-plane documentation does not preserve the CLI-only browser-auth boundary"
  exit 1
}

service_lib="$tmp_dir/hindsight-embed-service.zsh"
/usr/bin/sed '/^main "\$@"$/d' \
  "$repo_dir/bin/hindsight-embed-service" > "$service_lib"
/bin/cat >> "$service_lib" <<'ZSH'
hindsight_stack_with_lifecycle_lock() {
  local callback="$1"
  shift
  "$callback" "$@"
}
hindsight_stack_stop_all() { return 0 }
ZSH
hindsight_stack_reset_desired_state() { return 0 }

test_home="$tmp_dir/home"
mkdir -p "$test_home/.hindsight/profiles"
touch "$test_home/.hindsight/profiles/present-profile.env"
export HOME="$test_home"

if (
  unset ${(M)${(k)parameters}:#HINDSIGHT_*}
  source "$rendered_stack_lib"
  hindsight_stack_load_config
) >/dev/null 2>&1; then
  print -ru2 -- "stack library accepted inferred consumer bindings"
  exit 1
fi

export HINDSIGHT_EMBED_UVX=/usr/bin/true
export HINDSIGHT_EMBED_CONTROL_PORT=7878
export HINDSIGHT_EMBED_CONTROL_HOSTNAME=127.0.0.1
export HINDSIGHT_EMBED_PRIMARY_PROFILE=present-profile
export HINDSIGHT_EMBED_FLEET_PROFILES=present-profile
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
export HINDSIGHT_EMBED_SERVICE_MANIFEST="$test_home/Library/LaunchAgents/com.example.hindsight.stack.plist"
export HINDSIGHT_EMBED_LEGACY_MANIFEST="$test_home/Library/LaunchAgents/com.example.hindsight.legacy.plist"
export HINDSIGHT_EMBED_SUPERVISOR=/usr/bin/true
export HINDSIGHT_EMBED_STACK_LIB="$rendered_stack_lib"
export HINDSIGHT_EMBED_SERVICE_LOG="$tmp_dir/supervisor.log"

default_startup_timeouts="$tmp_dir/default-startup-timeouts"
(
  unset HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS
  unset HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS
  source "$rendered_stack_lib"
  hindsight_stack_load_config
  print -r -- "daemon:$HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS" >"$default_startup_timeouts"
  print -r -- "command:$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" >>"$default_startup_timeouts"
)
[[ "$(<"$default_startup_timeouts")" == $'daemon:300\ncommand:300' ]] || {
  print -ru2 -- "stack defaults do not cover the embedded daemon's bounded startup contract"
  exit 1
}

for relative_binding in HINDSIGHT_MEMORY_STATE_DIR HINDSIGHT_MEMORY_BROKER_SOCKET; do
  if (
    export "$relative_binding"=relative/path
    source "$rendered_stack_lib"
    hindsight_stack_load_config
  ) >/dev/null 2>&1; then
    print -ru2 -- "stack accepted relative ${relative_binding}"
    exit 1
  fi
done

if (
  export HINDSIGHT_MEMORY_INVENTORY=$'/absolute/inventory.json\n--inactive'
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_TOKEN
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  export TEST_DATA_TOKEN=test-data-token
  export TEST_MINT_AUTHORITY=test-mint-authority
  export TEST_UI_ACCESS_KEY=test-ui-access-key
  source "$rendered_stack_lib"
  hindsight_stack_load_config
) >/dev/null 2>&1; then
  print -ru2 -- "stack accepted a multiline runtime inventory path"
  exit 1
fi

if (
  export HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE=$'/absolute/upgrades\n--inactive'
  source "$rendered_stack_lib"
  hindsight_stack_load_config
) >/dev/null 2>&1; then
  print -ru2 -- "stack accepted a multiline integration-upgrade state path"
  exit 1
fi

integration_upgrade_arguments="$tmp_dir/integration-upgrade-arguments"
(
  export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
  export HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE=/absolute/integration-upgrades
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_TOKEN
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  source "$rendered_stack_lib"
  hindsight_stack_broker_runtime_arguments
) > "$integration_upgrade_arguments"
[[ "$(<"$integration_upgrade_arguments")" == \
  $'--inventory\n/absolute/inventory.json\n--data-plane-token-env\nTEST_DATA_TOKEN\n--mint-authority-env\nTEST_MINT_AUTHORITY\n--integration-upgrade-state\n/absolute/integration-upgrades' ]] || {
  print -ru2 -- "stack omitted the certified integration-upgrade authority state"
  exit 1
}

if (
  export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_SHARED_AUTHORITY
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_SHARED_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  source "$rendered_stack_lib"
  hindsight_stack_load_config
) >/dev/null 2>&1; then
  print -ru2 -- "stack accepted a shared data-plane and mint-authority resolver binding"
  exit 1
fi

for resolver_role in data mint ui; do
  for reserved_resolver in \
    HINDSIGHT_MEMORY_INVENTORY \
    HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE \
    HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV \
    HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV \
    HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV \
    HINDSIGHT_API_TENANT_API_KEY \
    HINDSIGHT_CP_DATAPLANE_API_KEY \
    HINDSIGHT_CP_ACCESS_KEY; do
    if (
      export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
      export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
      export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
      export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
      case "$resolver_role" in
        data) export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV="$reserved_resolver" ;;
        mint) export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV="$reserved_resolver" ;;
        ui) export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV="$reserved_resolver" ;;
      esac
      source "$rendered_stack_lib"
      hindsight_stack_load_config
    ) >/dev/null 2>&1; then
      print -ru2 -- "stack accepted reserved ${reserved_resolver} as the ${resolver_role} credential resolver"
      exit 1
    fi
  done
done

credential_scope_results="$tmp_dir/credential-scope-results"
(
  export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  export TEST_DATA_PLANE_TOKEN=test-data-plane-token
  export TEST_MINT_AUTHORITY=test-mint-authority
  export TEST_UI_ACCESS_KEY=test-ui-access-key
  source "$rendered_stack_lib"
  for credential_scope in none api ui-proxy broker; do
    print -rn -- "${credential_scope}:"
    hindsight_stack_run_with_credential_scope "$credential_scope" \
      /bin/zsh -c '
        api_match=0
        cp_match=0
        access_match=0
        [[ "${HINDSIGHT_API_TENANT_API_KEY:-}" == test-data-plane-token ]] && api_match=1
        [[ "${HINDSIGHT_CP_DATAPLANE_API_KEY:-}" == test-data-plane-token ]] && cp_match=1
        [[ "${HINDSIGHT_CP_ACCESS_KEY:-}" == test-ui-access-key ]] && access_match=1
        print -r -- "${+TEST_DATA_PLANE_TOKEN}:${+TEST_MINT_AUTHORITY}:${+TEST_UI_ACCESS_KEY}:${+HINDSIGHT_API_TENANT_API_KEY}:${+HINDSIGHT_CP_DATAPLANE_API_KEY}:${+HINDSIGHT_CP_ACCESS_KEY}:${api_match}:${cp_match}:${access_match}"
      '
  done
) > "$credential_scope_results"
[[ "$(<"$credential_scope_results")" == \
  $'none:0:0:0:0:0:0:0:0:0\napi:0:0:0:1:0:0:1:0:0\nui-proxy:0:0:0:0:1:1:0:1:1\nbroker:1:1:0:0:0:0:0:0:0' ]] || {
  print -ru2 -- "managed child credential scopes exceeded their authority"
  exit 1
}

authorized_credential_scope_results="$tmp_dir/authorized-credential-scope-results"
(
  export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  export TEST_DATA_PLANE_TOKEN=test-data-plane-token
  export TEST_MINT_AUTHORITY=test-mint-authority
  export TEST_UI_ACCESS_KEY=test-ui-access-key
  export HINDSIGHT_API_KEY=ambient-api-key
  export HINDSIGHT_DATA_PLANE_TOKEN=ambient-data-plane-token
  export HINDSIGHT_MINT_AUTHORITY=ambient-mint-authority
  export HINDSIGHT_UI_ACCESS_KEY=ambient-ui-access-key
  source "$rendered_stack_lib"
  for credential_scope in none api ui-proxy broker preflight; do
    print -rn -- "${credential_scope}:"
    hindsight_stack_run_with_credential_scope "$credential_scope" \
      /bin/zsh -f -c '
        for name in \
          TEST_DATA_PLANE_TOKEN TEST_MINT_AUTHORITY TEST_UI_ACCESS_KEY \
          HINDSIGHT_API_KEY HINDSIGHT_DATA_PLANE_TOKEN \
          HINDSIGHT_MINT_AUTHORITY HINDSIGHT_UI_ACCESS_KEY \
          HINDSIGHT_API_TENANT_API_KEY \
          HINDSIGHT_CP_DATAPLANE_API_KEY HINDSIGHT_CP_ACCESS_KEY; do
          print -rn -- "${+parameters[$name]}"
        done
        print
      '
  done
) > "$authorized_credential_scope_results"
[[ "$(<"$authorized_credential_scope_results")" == \
  $'none:0000000000\napi:0000000100\nui-proxy:0000000011\nbroker:1100000000\npreflight:1110000000' ]] || {
  print -ru2 -- "managed child scopes retained unauthorized credential destinations"
  exit 1
}

component_credential_scopes="$tmp_dir/component-credential-scopes"
(
  export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  export TEST_DATA_PLANE_TOKEN=test-data-plane-token
  export TEST_MINT_AUTHORITY=test-mint-authority
  export TEST_UI_ACCESS_KEY=test-ui-access-key
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_ensure_profile_ports() { return 0 }
  hindsight_stack_daemon_status() { return 0 }
  hindsight_stack_ui_running() { return 1 }
  hindsight_stack_run_bounded_with_credential_scope() {
    print -r -- "$1" >> "$component_credential_scopes"
  }
  hindsight_stack_control_start
  hindsight_stack_daemon_start
  hindsight_stack_ui_start
)
[[ "$(<"$component_credential_scopes")" == $'none\napi\nui-proxy' ]] || {
  print -ru2 -- "managed Hindsight components received incorrect credential scopes"
  exit 1
}

for shared_credential_pair in data-mint data-ui mint-ui; do
  if (
    export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
    export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
    export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
    export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
    export TEST_DATA_PLANE_TOKEN=distinct-data-plane-token
    export TEST_MINT_AUTHORITY=distinct-mint-authority
    export TEST_UI_ACCESS_KEY=distinct-ui-access-key
    case "$shared_credential_pair" in
      data-mint) export TEST_MINT_AUTHORITY=distinct-data-plane-token ;;
      data-ui) export TEST_UI_ACCESS_KEY=distinct-data-plane-token ;;
      mint-ui) export TEST_UI_ACCESS_KEY=distinct-mint-authority ;;
    esac
    export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
    source "$rendered_stack_lib"
    hindsight_stack_load_config
    hindsight_stack_preflight_runtime_credentials
  ) >/dev/null 2>&1; then
    print -ru2 -- "stack accepted shared ${shared_credential_pair} credential values"
    exit 1
  fi
done

if (
  export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  export TEST_DATA_PLANE_TOKEN=distinct-data-plane-token
  export TEST_MINT_AUTHORITY=distinct-mint-authority
  unset TEST_UI_ACCESS_KEY
  export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
  source "$rendered_stack_lib"
  hindsight_stack_load_config
  hindsight_stack_preflight_runtime_credentials
) >/dev/null 2>&1; then
  print -ru2 -- "stack accepted an unavailable UI access-key credential"
  exit 1
fi

profile_isolation_home="$tmp_dir/profile-isolation-home"
mkdir -p "$profile_isolation_home/.hindsight/profiles"
for reserved_profile_key in \
  HINDSIGHT_MEMORY_INVENTORY \
  HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV \
  HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV \
  HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV \
  HINDSIGHT_API_TENANT_API_KEY \
  HINDSIGHT_CP_DATAPLANE_API_KEY \
  HINDSIGHT_CP_ACCESS_KEY; do
  print -r -- "export ${reserved_profile_key}=profile-owned-value" > \
    "$profile_isolation_home/.hindsight/profiles/present-profile.env"
  if (
    export HOME="$profile_isolation_home"
    export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
    export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
    export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
    export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
    export TEST_DATA_PLANE_TOKEN=distinct-data-plane-token
    export TEST_MINT_AUTHORITY=distinct-mint-authority
    export TEST_UI_ACCESS_KEY=distinct-ui-access-key
    export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
    source "$rendered_stack_lib"
    hindsight_stack_load_config
    hindsight_stack_preflight_runtime_credentials
  ) >/dev/null 2>&1; then
    print -ru2 -- "stack accepted controller-owned credential binding ${reserved_profile_key} in an adopted profile"
    exit 1
  fi
done
print -r -- '# isolated profile' > \
  "$profile_isolation_home/.hindsight/profiles/present-profile.env"

for partial_runtime_binding in token-only mint-only inventory-token; do
  if (
    unset HINDSIGHT_MEMORY_INVENTORY
    unset HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV
    unset HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV
    unset HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV
    case "$partial_runtime_binding" in
      token-only)
        export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_TOKEN
        ;;
      mint-only)
        export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
        ;;
      inventory-token)
        export HINDSIGHT_MEMORY_INVENTORY=/absolute/inventory.json
        export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_TOKEN
        ;;
    esac
    source "$rendered_stack_lib"
    hindsight_stack_load_config
  ) >/dev/null 2>&1; then
    print -ru2 -- "stack accepted partial ${partial_runtime_binding} runtime bindings"
    exit 1
  fi
done

if (
  export HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=not-a-timeout
  source "$rendered_stack_lib"
  hindsight_stack_load_config
) >/dev/null 2>&1; then
  print -ru2 -- "stack accepted a nonnumeric lifecycle command timeout"
  exit 1
fi

for invalid_base_port in 07979 +7979 0 65536; do
  if (
    export HINDSIGHT_EMBED_API_BASE_PORT="$invalid_base_port"
    source "$rendered_stack_lib"
    hindsight_stack_load_config
  ) >/dev/null 2>&1; then
    print -ru2 -- "stack accepted noncanonical API base port ${invalid_base_port}"
    exit 1
  fi
done
for invalid_control_port in 07878 +7878 0 65536; do
  if (
    export HINDSIGHT_EMBED_CONTROL_PORT="$invalid_control_port"
    source "$rendered_stack_lib"
    hindsight_stack_load_config
  ) >/dev/null 2>&1; then
    print -ru2 -- "stack accepted noncanonical control port ${invalid_control_port}"
    exit 1
  fi
done
for invalid_autostart in 1 0 TRUE yes; do
  if (
    export HINDSIGHT_EMBED_AUTOSTART_DAEMON="$invalid_autostart"
    source "$rendered_stack_lib"
    hindsight_stack_load_config
  ) >/dev/null 2>&1; then
    print -ru2 -- "stack accepted noncanonical autostart value ${invalid_autostart}"
    exit 1
  fi
done
if (
  export HINDSIGHT_EMBED_AUTOSTART_DAEMON=false
  export HINDSIGHT_EMBED_AUTOSTART_UI=true
  source "$rendered_stack_lib"
  hindsight_stack_load_config
) >/dev/null 2>&1; then
  print -ru2 -- "stack accepted UI autostart without daemon autostart"
  exit 1
fi

if (
  cd "$tmp_dir"
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_PROFILE_SLOT_DIR=relative-profile-slots
  hindsight_stack_profile_slot present-profile
) >/dev/null 2>&1; then
  print -ru2 -- "profile slot allocation accepted a relative directory"
  exit 1
fi
[[ ! -e "$tmp_dir/relative-profile-slots" ]] || {
  print -ru2 -- "profile slot allocation created a relative directory before validation"
  exit 1
}
untrusted_slot_parent="$tmp_dir/untrusted-slot-parent"
mkdir -m 755 "$untrusted_slot_parent"
if (
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_PROFILE_SLOT_DIR="$untrusted_slot_parent/profile-slots"
  hindsight_stack_profile_slot present-profile
) >/dev/null 2>&1; then
  print -ru2 -- "profile slot allocation accepted a non-private parent"
  exit 1
fi
[[ ! -e "$untrusted_slot_parent/profile-slots" ]] || {
  print -ru2 -- "profile slot allocation created under an untrusted parent"
  exit 1
}
user_link_target="$tmp_dir/user-link-target"
user_link_parent="$tmp_dir/user-link-parent"
mkdir -m 700 "$user_link_target"
ln -s "$user_link_target" "$user_link_parent"
if (
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_PROFILE_SLOT_DIR="$user_link_parent/profile-slots"
  hindsight_stack_profile_slot present-profile
) >/dev/null 2>&1; then
  print -ru2 -- "profile slot allocation accepted a user-owned symlink ancestor"
  exit 1
fi
[[ ! -e "$user_link_target/profile-slots" ]] || {
  print -ru2 -- "profile slot allocation followed a user-owned symlink ancestor"
  exit 1
}

sync_start_waits="$tmp_dir/sync-start-waits"
if (
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_AUTOSTART_DAEMON=true
  HINDSIGHT_EMBED_AUTOSTART_UI=false
  hindsight_stack_reconcile_sidecars() { return 0 }
  hindsight_stack_daemon_status() { return 1 }
  hindsight_stack_daemon_start() { return 1 }
  hindsight_stack_wait_daemon() { print -r -- daemon >>"$sync_start_waits" }
  hindsight_stack_start_profile
) >/dev/null 2>&1; then
  print -ru2 -- "profile start accepted a synchronous daemon-start failure"
  exit 1
fi
if (
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_AUTOSTART_DAEMON=true
  HINDSIGHT_EMBED_AUTOSTART_UI=true
  hindsight_stack_reconcile_sidecars() { return 0 }
  hindsight_stack_daemon_status() { return 0 }
  hindsight_stack_ui_status() { return 1 }
  hindsight_stack_ui_start() { return 1 }
  hindsight_stack_wait_ui() { print -r -- ui >>"$sync_start_waits" }
  hindsight_stack_start_profile
) >/dev/null 2>&1; then
  print -ru2 -- "profile start accepted a synchronous UI-start failure"
  exit 1
fi
if (
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_preflight_runtime_credentials() { return 0 }
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  hindsight_stack_require_runtime_helpers() { return 0 }
  hindsight_stack_validate_fleet() { return 0 }
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 0 }
  hindsight_stack_wait_broker() { return 0 }
  hindsight_stack_control_status() { return 1 }
  hindsight_stack_control_start() { return 1 }
  hindsight_stack_wait_control() { print -r -- control >>"$sync_start_waits" }
  hindsight_stack_start_all
) >/dev/null 2>&1; then
  print -ru2 -- "stack start accepted a synchronous control-start failure"
  exit 1
fi
[[ ! -e "$sync_start_waits" ]] || {
  print -ru2 -- "stack waited after a synchronous component-start failure"
  exit 1
}

daemon_launcher_handoff="$tmp_dir/daemon-launcher-handoff"
daemon_launcher_status=0
(
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_ensure_profile_ports() { return 0 }
  hindsight_stack_run_bounded_api() {
    print -r -- launcher-failed >>"$daemon_launcher_handoff"
    return 1
  }
  hindsight_stack_wait_daemon() {
    print -r -- daemon-healthy >>"$daemon_launcher_handoff"
    return 0
  }
  hindsight_stack_daemon_start
) || daemon_launcher_status=$?
(( daemon_launcher_status == 0 )) &&
  [[ "$(<"$daemon_launcher_handoff")" == $'launcher-failed\ndaemon-healthy' ]] || {
  print -ru2 -- "daemon start discarded a still-initializing child after its launcher returned nonzero"
  exit 1
}
: >"$daemon_launcher_handoff"
daemon_launcher_status=0
(
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_ensure_profile_ports() { return 0 }
  hindsight_stack_run_bounded_api() {
    print -r -- launcher-failed >>"$daemon_launcher_handoff"
    return 1
  }
  hindsight_stack_wait_daemon() {
    print -r -- daemon-unhealthy >>"$daemon_launcher_handoff"
    return 1
  }
  hindsight_stack_daemon_start
) || daemon_launcher_status=$?
(( daemon_launcher_status != 0 )) &&
  [[ "$(<"$daemon_launcher_handoff")" == $'launcher-failed\ndaemon-unhealthy' ]] || {
  print -ru2 -- "daemon start accepted a failed launcher without bounded readiness"
  exit 1
}

hardened_ui_restart_events="$tmp_dir/hardened-ui-restart-events"
(
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_runtime_active() { return 0 }
  hindsight_stack_preflight_runtime_credentials() { return 0 }
  hindsight_stack_daemon_status() { return 0 }
  hindsight_stack_ui_running() { return 0 }
  hindsight_stack_ui_status() { return 1 }
  hindsight_stack_ui_stop() { print -r -- stop >>"$hardened_ui_restart_events" }
  hindsight_stack_wait_stopped_for() {
    print -r -- "wait:$1" >>"$hardened_ui_restart_events"
  }
  hindsight_stack_ensure_profile_ports() { return 0 }
  hindsight_stack_run_bounded_ui_proxy() {
    print -r -- start >>"$hardened_ui_restart_events"
  }
  hindsight_stack_ui_start
)
[[ "$(<"$hardened_ui_restart_events")" == $'stop\nwait:ui\nstart' ]] || {
  print -ru2 -- "active UI start did not replace a running unauthenticated control plane"
  exit 1
}

desired_start_events="$tmp_dir/desired-start-events"
(
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_AUTOSTART_DAEMON=false
  HINDSIGHT_EMBED_AUTOSTART_UI=false
  hindsight_stack_reconcile_sidecars() { return 0 }
  hindsight_stack_desired_state() { print -r -- running }
  integer daemon_up=1
  hindsight_stack_daemon_status() { return 0 }
  hindsight_stack_reconcile_daemon() { print -r -- daemon >>"$desired_start_events" }
  hindsight_stack_reconcile_ui() { print -r -- ui >>"$desired_start_events" }
  hindsight_stack_reconcile_profile
  daemon_up=0
  hindsight_stack_daemon_status() { (( daemon_up )) }
  hindsight_stack_daemon_start() { daemon_up=1; print -r -- start-daemon >>"$desired_start_events" }
  hindsight_stack_wait_daemon() { return 0 }
  hindsight_stack_ui_start() { print -r -- start-ui >>"$desired_start_events" }
  hindsight_stack_wait_ui() { return 0 }
  hindsight_stack_ui_status() { return 1 }
  hindsight_stack_start_profile
)
for expected in daemon ui start-daemon start-ui; do
  rg -F -x -q "$expected" "$desired_start_events" || {
    print -ru2 -- "persisted running intent was ignored with autostart disabled: ${expected}"
    exit 1
  }
done

start_all_events="$tmp_dir/start-all-events"
(
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  hindsight_stack_require_runtime_helpers() { return 0 }
  hindsight_stack_validate_fleet() { print -r -- validate >>"$start_all_events" }
  hindsight_stack_initialize_desired_state() { print -r -- initialize >>"$start_all_events" }
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 0 }
  hindsight_stack_wait_broker() { print -r -- broker >>"$start_all_events" }
  hindsight_stack_control_status() { return 0 }
  hindsight_stack_wait_control() { print -r -- control >>"$start_all_events" }
  hindsight_stack_for_each_profile() { print -r -- profiles >>"$start_all_events" }
  hindsight_stack_start_all
)
[[ "$(<"$start_all_events")" == $'validate\ninitialize\nbroker\ncontrol\nprofiles' ]] || {
  print -ru2 -- "stack start did not initialize desired state after validation and before starts"
  exit 1
}

active_start_all_events="$tmp_dir/active-start-all-events"
(
  source "$rendered_stack_lib"
  HINDSIGHT_MEMORY_INVENTORY="$tmp_dir/inventory.json"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  hindsight_stack_require_runtime_helpers() { return 0 }
  hindsight_stack_preflight_runtime_credentials() { return 0 }
  hindsight_stack_validate_fleet() { print -r -- validate >>"$active_start_all_events" }
  hindsight_stack_initialize_desired_state() { print -r -- initialize >>"$active_start_all_events" }
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 0 }
  hindsight_stack_wait_broker() { print -r -- broker >>"$active_start_all_events" }
  hindsight_stack_control_status() { return 0 }
  hindsight_stack_wait_control() { print -r -- control >>"$active_start_all_events" }
  hindsight_stack_for_each_profile() { print -r -- profiles >>"$active_start_all_events" }
  hindsight_stack_start_all
)
[[ "$(<"$active_start_all_events")" == $'validate\ninitialize\ncontrol\nprofiles\nbroker' ]] || {
  print -ru2 -- "active stack start did not bring up the data plane before broker verification"
  exit 1
}

probe_timeout_calls="$tmp_dir/probe-timeout-calls"
(
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=300
  hindsight_stack_control_status() { print -r -- "wait:$1" >> "$probe_timeout_calls" }
  hindsight_stack_control_running() { print -r -- "stop:$1" >> "$probe_timeout_calls"; return 1 }
  hindsight_stack_wait_for control 7
  hindsight_stack_wait_stopped_for control 7
)
while IFS= read -r probe_call; do
  probe_timeout="${probe_call#*:}"
  (( probe_timeout >= 1 && probe_timeout <= 7 )) || {
    print -ru2 -- "wait loop allowed a probe to exceed its remaining deadline: ${probe_call}"
    exit 1
  }
done < "$probe_timeout_calls"

ui_wait_calls="$tmp_dir/ui-wait-calls"
(
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=300
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_runtime_active() { return 0 }
  hindsight_stack_preflight_runtime_credentials() {
    print -r -- preflight >> "$ui_wait_calls"
  }
  integer ui_probe_count=0
  hindsight_stack_ui_status_probe() {
    print -r -- probe >> "$ui_wait_calls"
    (( ++ui_probe_count >= 3 ))
  }
  hindsight_stack_sleep() { return 0 }
  hindsight_stack_wait_for ui 3
)
ui_wait_events=("${(@f)$(<"$ui_wait_calls")}")
ui_wait_preflights=("${(@M)ui_wait_events:#preflight}")
ui_wait_probes=("${(@M)ui_wait_events:#probe}")
(( ${#ui_wait_preflights} == 1 )) || {
  print -ru2 -- "UI wait repeated runtime credential preflight"
  exit 1
}
(( ${#ui_wait_probes} == 3 )) || {
  print -ru2 -- "UI wait did not retain bounded repeated health probes"
  exit 1
}

for top_level_operation in hindsight_stack_wait_all hindsight_stack_stop_all hindsight_stack_status_report; do
  if (
    source "$rendered_stack_lib"
    hindsight_stack_load_config() { return 0 }
    hindsight_stack_require_current_user() { return 0 }
    hindsight_stack_require_tools() { return 1 }
    "$top_level_operation"
  ) >/dev/null 2>&1; then
    print -ru2 -- "${top_level_operation} accepted missing runtime tools"
    exit 1
  fi
done

bounded_child_script="$tmp_dir/bounded-child.zsh"
cat > "$bounded_child_script" <<'ZSH'
#!/usr/bin/env zsh
print -r -- "$$" > "$HINDSIGHT_TEST_BOUNDED_CHILD_PID"
print -r -- "$PPID" > "$HINDSIGHT_TEST_BOUNDED_WRAPPER_PID"
trap '' TERM
touch "$HINDSIGHT_TEST_BOUNDED_READY"
sleep 60
ZSH
chmod 700 "$bounded_child_script"
bounded_child_pid_file="$tmp_dir/bounded-child.pid"
bounded_wrapper_pid_file="$tmp_dir/bounded-wrapper.pid"
bounded_ready="$tmp_dir/bounded.ready"
(
  export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
  export HINDSIGHT_TEST_BOUNDED_CHILD_PID="$bounded_child_pid_file"
  export HINDSIGHT_TEST_BOUNDED_WRAPPER_PID="$bounded_wrapper_pid_file"
  export HINDSIGHT_TEST_BOUNDED_READY="$bounded_ready"
  source "$rendered_stack_lib"
  hindsight_stack_run_bounded 30 "$bounded_child_script"
) >/dev/null 2>&1 &
bounded_call_pid=$!
track_fixture_pid "$bounded_call_pid"
for _ in {1..100}; do
  [[ -e "$bounded_ready" && -s "$bounded_child_pid_file" && -s "$bounded_wrapper_pid_file" ]] && break
  sleep 0.02
done
track_fixture_pid_file "$bounded_child_pid_file"
track_fixture_pid_file "$bounded_wrapper_pid_file"
[[ -e "$bounded_ready" && -s "$bounded_child_pid_file" && -s "$bounded_wrapper_pid_file" ]] || {
  print -ru2 -- "bounded runner signal fixture did not become ready"
  exit 1
}
bounded_child_pid="$(<"$bounded_child_pid_file")"
bounded_wrapper_pid="$(<"$bounded_wrapper_pid_file")"
track_fixture_pid "$bounded_child_pid"
track_fixture_pid "$bounded_wrapper_pid"
kill -TERM "$bounded_wrapper_pid"
bounded_status=0
untrack_fixture_pid "$bounded_call_pid"
wait "$bounded_call_pid" || bounded_status=$?
[[ "$bounded_status" == 143 ]] || {
  print -ru2 -- "bounded runner signal returned ${bounded_status}, expected 143"
  exit 1
}
for pid in "$bounded_child_pid" "$bounded_wrapper_pid"; do
  for _ in {1..100}; do
    kill -0 "$pid" >/dev/null 2>&1 || break
    sleep 0.02
  done
  kill -0 "$pid" >/dev/null 2>&1 && {
    print -ru2 -- "bounded runner signal left process ${pid} running"
    exit 1
  }
  untrack_fixture_pid "$pid"
done

(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE="missing-profile"
  source "$rendered_stack_lib"
  if hindsight_stack_profile_exists; then
    print -ru2 -- "missing profile unexpectedly exists"
    exit 1
  fi
)

touch "$test_home/.hindsight/profiles/present-profile.env"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  source "$rendered_stack_lib"
  hindsight_stack_profile_exists
)

stop_control_plane_events="$tmp_dir/stop-control-plane-events"
(
  export HOME="$test_home"
  source "$rendered_stack_lib"
  hindsight_stack_for_each_profile_for_stop() { return 0 }
  hindsight_stack_broker_stop() { print -r -- broker-stop >> "$stop_control_plane_events" }
  hindsight_stack_control_stop() { print -r -- control-stop >> "$stop_control_plane_events" }
  hindsight_stack_wait_stopped_for() { print -r -- "wait:$1" >> "$stop_control_plane_events" }
  hindsight_stack_stop_all
)
[[ "$(paste -sd, - < "$stop_control_plane_events")" == \
  "broker-stop,wait:broker,control-stop,wait:control" ]] || {
  print -ru2 -- "stack stop skipped idempotent broker or control cleanup"
  exit 1
}

missing_profile_stop_events="$tmp_dir/missing-profile-stop-events"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,missing-profile"
  source "$rendered_stack_lib"
  record_stop_profile() {
    print -r -- "$HINDSIGHT_EMBED_PROFILE:$HINDSIGHT_EMBED_API_PORT:$HINDSIGHT_EMBED_UI_PORT" \
      >>"$missing_profile_stop_events"
  }
  hindsight_stack_for_each_profile_for_stop record_stop_profile
)
[[ "$(/usr/bin/wc -l <"$missing_profile_stop_events" | tr -d ' ')" == 2 ]] &&
  rg -q '^present-profile:[0-9]+:[0-9]+$' "$missing_profile_stop_events" &&
  rg -q '^missing-profile:[0-9]+:[0-9]+$' "$missing_profile_stop_events" || {
  print -ru2 -- "stack stop profile selection skipped enabled profile with missing metadata"
  exit 1
}

sidecar_deadline_args="$tmp_dir/sidecar-deadline-args"
(
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS=30
  hindsight_stack_sidecar_names() {
    print -r -- first
    print -r -- second
  }
  hindsight_stack_sidecar_status() {
    print -r -- "$2" >>"$sidecar_deadline_args"
    [[ "$1" != first ]] || sleep 2
  }
  hindsight_stack_sidecars_status 3
)
sidecar_deadlines=("${(@f)$(<"$sidecar_deadline_args")}")
(( ${#sidecar_deadlines} == 2 &&
  sidecar_deadlines[1] <= 3 &&
  sidecar_deadlines[2] < sidecar_deadlines[1] )) || {
  print -ru2 -- "sidecar status probes did not share one deadline budget"
  exit 1
}

touch "$test_home/.hindsight/profiles/second-profile.env"
mkdir -p "$test_home/.hindsight/profiles/present-profile.sidecars/reranker"
sidecar_port="$(/usr/bin/python3 -c 'import socket; sock = socket.socket(); sock.bind(("127.0.0.1", 0)); print(sock.getsockname()[1]); sock.close()')"
print -r -- "$sidecar_port" > "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/port-base"
print -r -- "/healthz" > "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/health-path"
cat > "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/start" <<'ZSH'
#!/usr/bin/env zsh
sleep 60 &
print -r -- "$!" > "$HINDSIGHT_TEST_SIDECAR_CHILD_PID"
wait
ZSH
chmod 700 "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/start"
sidecar_child_pid_file="$tmp_dir/sidecar-child.pid"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE=present-profile
  export HINDSIGHT_EMBED_PROFILE_SLOT=0
  export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
  export HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS=1
  export HINDSIGHT_TEST_SIDECAR_CHILD_PID="$sidecar_child_pid_file"
  source "$rendered_stack_lib"
  hindsight_stack_sidecar_command reranker start
) >/dev/null 2>&1; then
  print -ru2 -- "sidecar hook timeout unexpectedly succeeded"
  exit 1
fi
[[ -s "$sidecar_child_pid_file" ]] || {
  print -ru2 -- "sidecar hook did not start its descendant"
  exit 1
}
sidecar_child_pid="$(<"$sidecar_child_pid_file")"
track_fixture_pid "$sidecar_child_pid" || true
for _ in {1..40}; do
  state="$(ps -o state= -p "$sidecar_child_pid" 2>/dev/null || true)"
  [[ -z "$state" || "$state" == Z* ]] && break
  sleep 0.05
done
state="$(ps -o state= -p "$sidecar_child_pid" 2>/dev/null || true)"
[[ -z "$state" || "$state" == Z* ]] || {
  kill -KILL "$sidecar_child_pid" >/dev/null 2>&1 || true
  print -ru2 -- "sidecar hook timeout left a descendant running"
  exit 1
}
untrack_fixture_pid "$sidecar_child_pid"

cat > "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/interrupt" <<'ZSH'
#!/usr/bin/env zsh
print -r -- "$$" > "$HINDSIGHT_TEST_SIDECAR_HOOK_PID"
print -r -- "$PPID" > "$HINDSIGHT_TEST_SIDECAR_WRAPPER_PID"
/bin/zsh -c '
  trap "" TERM
  print -r -- ready > "$HINDSIGHT_TEST_SIDECAR_INTERRUPT_CHILD_READY"
  sleep 60
' &
print -r -- "$!" > "$HINDSIGHT_TEST_SIDECAR_INTERRUPT_CHILD_PID"
wait
ZSH
chmod 700 "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/interrupt"
sidecar_hook_pid_file="$tmp_dir/sidecar-hook.pid"
sidecar_wrapper_pid_file="$tmp_dir/sidecar-wrapper.pid"
sidecar_interrupt_child_pid_file="$tmp_dir/sidecar-interrupt-child.pid"
sidecar_interrupt_child_ready_file="$tmp_dir/sidecar-interrupt-child.ready"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE=present-profile
  export HINDSIGHT_EMBED_PROFILE_SLOT=0
  export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
  export HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS=30
  export HINDSIGHT_TEST_SIDECAR_HOOK_PID="$sidecar_hook_pid_file"
  export HINDSIGHT_TEST_SIDECAR_WRAPPER_PID="$sidecar_wrapper_pid_file"
  export HINDSIGHT_TEST_SIDECAR_INTERRUPT_CHILD_PID="$sidecar_interrupt_child_pid_file"
  export HINDSIGHT_TEST_SIDECAR_INTERRUPT_CHILD_READY="$sidecar_interrupt_child_ready_file"
  source "$rendered_stack_lib"
  hindsight_stack_sidecar_command reranker interrupt
) >/dev/null 2>&1 &
sidecar_interrupt_call_pid=$!
track_fixture_pid "$sidecar_interrupt_call_pid"
for _ in {1..100}; do
  [[ -s "$sidecar_hook_pid_file" && -s "$sidecar_wrapper_pid_file" && \
    -s "$sidecar_interrupt_child_pid_file" && \
    -s "$sidecar_interrupt_child_ready_file" ]] && break
  sleep 0.05
done
[[ -s "$sidecar_hook_pid_file" && -s "$sidecar_wrapper_pid_file" && \
  -s "$sidecar_interrupt_child_pid_file" && \
  -s "$sidecar_interrupt_child_ready_file" ]] || {
  track_fixture_pid_file "$sidecar_hook_pid_file"
  track_fixture_pid_file "$sidecar_wrapper_pid_file"
  track_fixture_pid_file "$sidecar_interrupt_child_pid_file"
  kill -KILL "$sidecar_interrupt_call_pid" >/dev/null 2>&1 || true
  untrack_fixture_pid "$sidecar_interrupt_call_pid"
  wait "$sidecar_interrupt_call_pid" >/dev/null 2>&1 || true
  print -ru2 -- "sidecar interruption fixture did not become ready"
  exit 1
}
sidecar_hook_pid="$(<"$sidecar_hook_pid_file")"
sidecar_wrapper_pid="$(<"$sidecar_wrapper_pid_file")"
sidecar_interrupt_child_pid="$(<"$sidecar_interrupt_child_pid_file")"
track_fixture_pid "$sidecar_hook_pid"
track_fixture_pid "$sidecar_wrapper_pid"
track_fixture_pid "$sidecar_interrupt_child_pid"
kill -TERM "$sidecar_wrapper_pid"
sidecar_interrupt_status=0
untrack_fixture_pid "$sidecar_interrupt_call_pid"
wait "$sidecar_interrupt_call_pid" || sidecar_interrupt_status=$?
[[ "$sidecar_interrupt_status" == 143 ]] || {
  print -ru2 -- "sidecar caller interruption returned ${sidecar_interrupt_status}, expected 143"
  exit 1
}
for pid in "$sidecar_hook_pid" "$sidecar_wrapper_pid" "$sidecar_interrupt_child_pid"; do
  for _ in {1..40}; do
    state="$(ps -o state= -p "$pid" 2>/dev/null || true)"
    [[ -z "$state" || "$state" == Z* ]] && break
    sleep 0.05
  done
  state="$(ps -o state= -p "$pid" 2>/dev/null || true)"
  [[ -z "$state" || "$state" == Z* ]] || {
    kill -KILL "$pid" >/dev/null 2>&1 || true
    print -ru2 -- "sidecar caller interruption left process ${pid} running"
    exit 1
  }
done
untrack_fixture_pid "$sidecar_hook_pid"
untrack_fixture_pid "$sidecar_wrapper_pid"
untrack_fixture_pid "$sidecar_interrupt_child_pid"

cat > "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/immediate-interrupt" <<'PY'
#!/usr/bin/python3
import os
import signal
import time

blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set())
with open(os.environ["HINDSIGHT_TEST_IMMEDIATE_MASK"], "w", encoding="ascii") as handle:
    handle.write("blocked\n" if signal.SIGTERM in blocked else "unblocked\n")

def terminate(_signum, _frame):
    with open(os.environ["HINDSIGHT_TEST_IMMEDIATE_GRACEFUL"], "w", encoding="ascii") as handle:
        handle.write("graceful\n")
    raise SystemExit(143)

signal.signal(signal.SIGTERM, terminate)
with open(os.environ["HINDSIGHT_TEST_IMMEDIATE_HOOK_PID"], "w", encoding="ascii") as handle:
    handle.write(f"{os.getpid()}\n")
os.kill(os.getppid(), signal.SIGTERM)
while True:
    time.sleep(60)
PY
chmod 700 "$test_home/.hindsight/profiles/present-profile.sidecars/reranker/immediate-interrupt"
immediate_hook_pid_file="$tmp_dir/immediate-hook.pid"
immediate_graceful_file="$tmp_dir/immediate-graceful"
immediate_mask_file="$tmp_dir/immediate-mask"
immediate_status=0
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE=present-profile
  export HINDSIGHT_EMBED_PROFILE_SLOT=0
  export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
  export HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS=30
  export HINDSIGHT_TEST_SIDECAR_WAIT_FOR_PENDING_TERM=1
  export HINDSIGHT_TEST_IMMEDIATE_HOOK_PID="$immediate_hook_pid_file"
  export HINDSIGHT_TEST_IMMEDIATE_GRACEFUL="$immediate_graceful_file"
  export HINDSIGHT_TEST_IMMEDIATE_MASK="$immediate_mask_file"
  source "$rendered_stack_lib"
  hindsight_stack_sidecar_command reranker immediate-interrupt
) >/dev/null 2>&1 || immediate_status=$?
pid=""
if [[ -s "$immediate_hook_pid_file" ]]; then
  pid="$(<"$immediate_hook_pid_file")"
  track_fixture_pid "$pid" || true
fi
[[ "$immediate_status" == 143 ]] || {
  print -ru2 -- "masked sidecar interruption returned ${immediate_status}, expected 143"
  exit 1
}
[[ -s "$immediate_hook_pid_file" ]] || {
  print -ru2 -- "masked sidecar interruption did not start its hook"
  exit 1
}
[[ "$(<"$immediate_mask_file")" == unblocked ]] || {
  print -ru2 -- "sidecar inherited blocked TERM before signaling its parent"
  exit 1
}
[[ "$(<"$immediate_graceful_file")" == graceful ]] || {
  print -ru2 -- "sidecar inherited the parent's temporary signal mask"
  exit 1
}
state="$(ps -o state= -p "$pid" 2>/dev/null || true)"
[[ -z "$state" || "$state" == Z* ]] || {
  kill -KILL "$pid" >/dev/null 2>&1 || true
  print -ru2 -- "masked sidecar interruption left hook ${pid} running"
  exit 1
}
untrack_fixture_pid "$pid"

(
  source "$rendered_stack_lib"
  [[ "$(hindsight_stack_http_url ::1 7977 /health)" == "http://[::1]:7977/health" ]] || {
    print -ru2 -- "central URL formatter did not bracket the IPv6 loopback host"
    exit 1
  }
  HINDSIGHT_EMBED_UI_HOSTNAME=::1
  HINDSIGHT_EMBED_UI_PORT=17979
  [[ "$(hindsight_stack_ui_url)" == "http://[::1]:17979" ]] || {
    print -ru2 -- "UI URL did not use the central IPv6 host formatter"
    exit 1
  }
)

if (
  export HINDSIGHT_EMBED_FLEET_PROFILES="profile-one,profile.one"
  source "$rendered_stack_lib"
  hindsight_stack_enabled_profiles
) >/dev/null 2>&1; then
  print -ru2 -- "fleet accepted profile names with colliding normalized environment keys"
  exit 1
fi

fleet_state="$tmp_dir/fleet-state"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,second-profile"
  export HINDSIGHT_EMBED_API_PORT=7979
  export HINDSIGHT_EMBED_UI_PORT=17979
  source "$rendered_stack_lib"

  profiles="$(hindsight_stack_enabled_profiles | paste -sd, -)"
  [[ "$profiles" == "present-profile,second-profile" ]] || {
    print -ru2 -- "fleet did not retain enabled profile order: ${profiles}"
    exit 1
  }
  hindsight_stack_require_fleet_profiles
  hindsight_stack_validate_fleet

  hindsight_stack_select_profile present-profile
  [[ "$HINDSIGHT_EMBED_PROFILE_SLOT" == 0 ]] || exit 1
  [[ "$HINDSIGHT_EMBED_API_PORT" == 7979 ]] || exit 1
  [[ "$HINDSIGHT_EMBED_UI_PORT" == 17979 ]] || exit 1
  [[ "$(hindsight_stack_sidecar_port reranker)" == "$sidecar_port" ]] || exit 1
  [[ "$(hindsight_stack_sidecar_health_url reranker)" == "http://127.0.0.1:${sidecar_port}/healthz" ]] || exit 1
  sidecar_probe="$tmp_dir/sidecar-probe"
  hindsight_stack_http_ok() {
    print -r -- "$1" > "$sidecar_probe"
    return 0
  }
  hindsight_stack_sidecars_status
  [[ "$(<"$sidecar_probe")" == "http://127.0.0.1:${sidecar_port}/healthz" ]] || {
    print -ru2 -- "sidecar readiness did not probe the slot-derived endpoint"
    exit 1
  }

  hindsight_stack_select_profile second-profile
  [[ "$HINDSIGHT_EMBED_PROFILE_SLOT" == 1 ]] || exit 1
  [[ "$HINDSIGHT_EMBED_API_PORT" == 7980 ]] || exit 1
  [[ "$HINDSIGHT_EMBED_UI_PORT" == 17980 ]] || exit 1
  export HINDSIGHT_EMBED_PROFILE_SECOND_PROFILE_API_PORT=7979
  if hindsight_stack_validate_fleet >/dev/null 2>&1; then
    print -ru2 -- "fleet collision unexpectedly validated"
    exit 1
  fi
  unset HINDSIGHT_EMBED_PROFILE_SECOND_PROFILE_API_PORT
  [[ "$HINDSIGHT_EMBED_PROFILE" == second-profile ]] || {
    print -ru2 -- "failed fleet traversal leaked the selected profile"
    exit 1
  }
  [[ "$HINDSIGHT_EMBED_PROFILE_SLOT" == 1 && "$HINDSIGHT_EMBED_API_PORT" == 7980 && "$HINDSIGHT_EMBED_UI_PORT" == 17980 ]] || {
    print -ru2 -- "failed fleet traversal leaked derived profile state"
    exit 1
  }
)

unsafe_lock_state="$tmp_dir/unsafe-lock-state"
dangling_lock_target="$tmp_dir/dangling-lock-target"
mkdir -p "$unsafe_lock_state/profile-slots"
ln -s "$dangling_lock_target" "$unsafe_lock_state/profile-slots/.allocation.lock"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$unsafe_lock_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,second-profile"
  source "$rendered_stack_lib"
  hindsight_stack_profile_slot present-profile
) >/dev/null 2>&1; then
  print -ru2 -- "profile slot allocation accepted a dangling lock symlink"
  exit 1
fi
[[ ! -e "$dangling_lock_target" ]] || {
  print -ru2 -- "profile slot allocation followed a dangling lock symlink"
  exit 1
}

unsafe_lifecycle_parent="$tmp_dir/unsafe-lifecycle-parent"
unsafe_lifecycle_state="$unsafe_lifecycle_parent/new/state"
mkdir "$unsafe_lifecycle_parent"
chmod 777 "$unsafe_lifecycle_parent"
if (
  export HINDSIGHT_EMBED_STATE_DIR="$unsafe_lifecycle_state"
  source "$rendered_stack_lib"
  hindsight_stack_prepare_lifecycle_lock
) >/dev/null 2>&1; then
  print -ru2 -- "lifecycle lock accepted an unsafe existing ancestor"
  exit 1
fi
[[ ! -e "$unsafe_lifecycle_parent/new" &&
  "$(/usr/bin/stat -f '%Lp' "$unsafe_lifecycle_parent")" == 777 ]] || {
  print -ru2 -- "lifecycle lock mutated state before validating existing ancestry"
  exit 1
}
chmod 700 "$unsafe_lifecycle_parent"

unsafe_python="$tmp_dir/unsafe-python"
/bin/cp /usr/bin/true "$unsafe_python"
/bin/chmod 777 "$unsafe_python"
if (
  export HINDSIGHT_EMBED_PYTHON="$unsafe_python"
  source "$rendered_stack_lib"
  hindsight_stack_require_tools
) >/dev/null 2>&1; then
  print -ru2 -- "runtime tool preflight accepted a writable configured Python"
  exit 1
fi

unsafe_runtime_tool="$tmp_dir/unsafe-runtime-tool"
/bin/cp /usr/bin/true "$unsafe_runtime_tool"
/bin/chmod 777 "$unsafe_runtime_tool"
if (
  export HINDSIGHT_EMBED_UVX="$unsafe_runtime_tool"
  source "$rendered_stack_lib"
  hindsight_stack_require_tools
) >/dev/null 2>&1; then
  print -ru2 -- "runtime tool preflight accepted a writable uvx replacement"
  exit 1
fi
if (
  export HINDSIGHT_EMBED_PYTHON="$unsafe_runtime_tool"
  source "$rendered_stack_lib"
  hindsight_stack_require_runtime_helpers
) >/dev/null 2>&1; then
  print -ru2 -- "runtime helper preflight accepted a writable Python replacement"
  exit 1
fi

missing_lsof_error="$tmp_dir/missing-lsof.err"
if (
  source "$rendered_stack_lib"
  hindsight_stack_lsof_path() { return 1 }
  hindsight_stack_require_tools
) >/dev/null 2>"$missing_lsof_error"; then
  print -ru2 -- "runtime tool preflight accepted a missing lsof dependency"
  exit 1
fi
if [[ "$(<"$missing_lsof_error")" != *"missing lsof at /usr/bin/lsof or /usr/sbin/lsof"* ]]; then
  print -ru2 -- "missing lsof preflight did not report the expected diagnostic"
  exit 1
fi

(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE=present-profile
  export HINDSIGHT_EMBED_PRIMARY_PROFILE=present-profile
  export HINDSIGHT_EMBED_FLEET_PROFILES=second-profile
  source "$rendered_stack_lib"
  if hindsight_stack_validate_fleet >/dev/null 2>&1; then
    print -ru2 -- "fleet validation accepted a primary profile outside the enabled fleet"
    exit 1
  fi
)

invalid_reconcile_marker="$tmp_dir/invalid-reconcile-mutated"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE=present-profile
  export HINDSIGHT_EMBED_PRIMARY_PROFILE=present-profile
  export HINDSIGHT_EMBED_FLEET_PROFILES=second-profile
  source "$rendered_stack_lib"
  hindsight_stack_reconcile_broker() { touch "$invalid_reconcile_marker" }
  hindsight_stack_reconcile_control() { touch "$invalid_reconcile_marker" }
  hindsight_stack_for_each_profile() { touch "$invalid_reconcile_marker" }
  if hindsight_stack_reconcile_once >/dev/null 2>&1; then
    print -ru2 -- "reconcile accepted an invalid fleet"
    exit 1
  fi
)
[[ ! -e "$invalid_reconcile_marker" ]] || {
  print -ru2 -- "reconcile mutated runtime state before fleet validation"
  exit 1
}

for failed_preflight in current-user tools; do
  reconcile_preflight_marker="$tmp_dir/reconcile-${failed_preflight}-mutated"
  if (
    source "$rendered_stack_lib"
    hindsight_stack_load_config() { return 0 }
    hindsight_stack_require_current_user() { [[ "$failed_preflight" != current-user ]] }
    hindsight_stack_require_tools() { [[ "$failed_preflight" != tools ]] }
    hindsight_stack_validate_fleet() { touch "$reconcile_preflight_marker" }
    hindsight_stack_reconcile_broker() { touch "$reconcile_preflight_marker" }
    hindsight_stack_reconcile_once
  ) >/dev/null 2>&1; then
    print -ru2 -- "reconcile accepted failed ${failed_preflight} preflight"
    exit 1
  fi
  [[ ! -e "$reconcile_preflight_marker" ]] || {
    print -ru2 -- "reconcile mutated runtime state after failed ${failed_preflight} preflight"
    exit 1
  }
done

filtered_status="$tmp_dir/filtered-status.out"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,missing-profile"
  source "$rendered_stack_lib"
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 0 }
  hindsight_stack_control_status() { return 0 }
  hindsight_stack_daemon_status() { return 0 }
  hindsight_stack_ui_status() { return 0 }
  hindsight_stack_sidecar_names() { return 0 }
  hindsight_stack_status_report present-profile > "$filtered_status"
)
rg -F -q 'fleet: healthy (1 enabled profile)' "$filtered_status" || {
  print -ru2 -- "filtered status was degraded by an unrelated unselectable profile"
  exit 1
}

disabled_status="$tmp_dir/disabled-status.out"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile"
  export HINDSIGHT_EMBED_AUTOSTART_DAEMON=false
  export HINDSIGHT_EMBED_AUTOSTART_UI=false
  source "$rendered_stack_lib"
  hindsight_stack_set_desired_state daemon stopped present-profile
  hindsight_stack_set_desired_state ui stopped present-profile
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 0 }
  hindsight_stack_control_status() { return 0 }
  hindsight_stack_daemon_status() { return 1 }
  hindsight_stack_daemon_running() { return 1 }
  hindsight_stack_ui_status() { return 1 }
  hindsight_stack_ui_running() { return 1 }
  hindsight_stack_sidecar_names() { return 0 }
  hindsight_stack_status_report > "$disabled_status"
)
rg -F -q 'daemon: stopped' "$disabled_status" || {
  print -ru2 -- "status did not report stopped daemon intent"
  exit 1
}
rg -F -q 'ui: stopped' "$disabled_status" || {
  print -ru2 -- "status did not report stopped UI intent"
  exit 1
}
rg -F -q 'fleet: healthy (1 enabled profile)' "$disabled_status" || {
  print -ru2 -- "disabled optional components degraded fleet status"
  exit 1
}

unmanaged_broker_status="$tmp_dir/unmanaged-broker-status.out"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile"
  export HINDSIGHT_EMBED_AUTOSTART_DAEMON=false
  export HINDSIGHT_EMBED_AUTOSTART_UI=false
  source "$rendered_stack_lib"
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 1 }
  hindsight_stack_control_status() { return 0 }
  hindsight_stack_sidecar_names() { return 0 }
  hindsight_stack_status_report > "$unmanaged_broker_status"
); then
  print -ru2 -- "fleet status succeeded for a responsive broker with unmanaged identity"
  exit 1
fi
rg -F -q 'fleet: degraded (1 enabled profile)' "$unmanaged_broker_status" || {
  print -ru2 -- "fleet health accepted a responsive broker with unmanaged identity"
  exit 1
}

(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="second-profile,present-profile"
  export HINDSIGHT_EMBED_API_PORT=7979
  export HINDSIGHT_EMBED_UI_PORT=17979
  source "$rendered_stack_lib"
  hindsight_stack_select_profile present-profile
  [[ "$HINDSIGHT_EMBED_PROFILE_SLOT" == 0 ]] || {
    print -ru2 -- "persisted profile slot changed after fleet reorder"
    exit 1
  }
  hindsight_stack_select_profile second-profile
  [[ "$HINDSIGHT_EMBED_PROFILE_SLOT" == 1 ]] || exit 1
)

[[ "$(stat -f '%Lp' "$fleet_state/profile-slots/present-profile.slot")" == 600 ]] || {
  print -ru2 -- "persisted profile slot is not mode 0600"
  exit 1
}

print -r -- 01 > "$fleet_state/profile-slots/leading-zero.slot"
chmod 600 "$fleet_state/profile-slots/leading-zero.slot"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  source "$rendered_stack_lib"
  hindsight_stack_profile_slot leading-zero
) >/dev/null 2>&1; then
  print -ru2 -- "profile slot accepted a noncanonical leading-zero decimal"
  exit 1
fi
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  source "$rendered_stack_lib"
  hindsight_stack_profile_slot new-profile
) >/dev/null 2>&1; then
  print -ru2 -- "profile slot allocation ignored a noncanonical loaded slot"
  exit 1
fi
/bin/rm -f "$fleet_state/profile-slots/leading-zero.slot"

configured_python_calls="$tmp_dir/configured-python-calls"
configured_python="$tmp_dir/configured-python"
cat > "$configured_python" <<'ZSH'
#!/usr/bin/env zsh
print -r -- "${(j: :)@}" >> "$HINDSIGHT_TEST_CONFIGURED_PYTHON_CALLS"
exec /usr/bin/python3 "$@"
ZSH
chmod 700 "$configured_python"
export HINDSIGHT_TEST_CONFIGURED_PYTHON_CALLS="$configured_python_calls"
export HINDSIGHT_EMBED_PYTHON="$configured_python"
runtime_helper_preflight_args="$tmp_dir/runtime-helper-preflight-args"
(
  source "$rendered_stack_lib"
  hindsight_stack_run_bounded() { print -r -- "$@" >"$runtime_helper_preflight_args" }
  hindsight_stack_require_runtime_helpers
)
[[ "$(<"$runtime_helper_preflight_args")" == *" $configured_python -I $HINDSIGHT_EMBED_STOP_HELPER --help" ]] || {
  print -ru2 -- "runtime helper preflight did not isolate the configured Python runtime"
  exit 1
}
sidecar_dir="$test_home/.hindsight/profiles/present-profile.sidecars/reranker"
cat > "$sidecar_dir/status" <<'ZSH'
#!/usr/bin/env zsh
[[ -e "$HINDSIGHT_TEST_SIDECAR_MARKER" ]]
ZSH
cat > "$sidecar_dir/start" <<'ZSH'
#!/usr/bin/env zsh
print -r -- "${HINDSIGHT_EMBED_PROFILE}:${HINDSIGHT_EMBED_PROFILE_SLOT}:${HINDSIGHT_EMBED_SIDECAR_NAME}:${HINDSIGHT_EMBED_SIDECAR_PORT}" > "$HINDSIGHT_TEST_SIDECAR_START"
touch "$HINDSIGHT_TEST_SIDECAR_MARKER"
ZSH
cat > "$sidecar_dir/stop" <<'ZSH'
#!/usr/bin/env zsh
rm -f "$HINDSIGHT_TEST_SIDECAR_MARKER"
ZSH
chmod 700 "$sidecar_dir/status" "$sidecar_dir/start" "$sidecar_dir/stop"

chmod 666 "$sidecar_dir/port-base"
if (
  export HOME="$test_home"
  source "$rendered_stack_lib"
  hindsight_stack_sidecar_port reranker
) >/dev/null 2>&1; then
  print -ru2 -- "sidecar metadata accepted a group/world-writable file"
  exit 1
fi
chmod 600 "$sidecar_dir/port-base"

sidecar_symlink_target="$tmp_dir/sidecar-symlink-target"
cat > "$sidecar_symlink_target" <<'ZSH'
#!/usr/bin/env zsh
touch "$HINDSIGHT_TEST_SIDECAR_SYMLINK_RAN"
ZSH
chmod 700 "$sidecar_symlink_target"
ln -s "$sidecar_symlink_target" "$sidecar_dir/rejected"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_PROFILE_SLOT=0
  export HINDSIGHT_TEST_SIDECAR_SYMLINK_RAN="$tmp_dir/sidecar-symlink-ran"
  source "$rendered_stack_lib"
  hindsight_stack_sidecar_command reranker rejected
) >/dev/null 2>&1; then
  print -ru2 -- "sidecar command accepted a symlinked executable"
  exit 1
fi
[[ ! -e "$tmp_dir/sidecar-symlink-ran" ]] || {
  print -ru2 -- "sidecar command executed a symlink before rejecting it"
  exit 1
}
sidecar_symlink_home="$tmp_dir/sidecar-symlink-home"
mkdir "$sidecar_symlink_home"
ln -s "$test_home/.hindsight" "$sidecar_symlink_home/.hindsight"
sidecar_ancestor_marker="$tmp_dir/sidecar-ancestor-marker"
touch "$sidecar_ancestor_marker"
if (
  export HOME="$sidecar_symlink_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_PROFILE_SLOT=0
  export HINDSIGHT_TEST_SIDECAR_MARKER="$sidecar_ancestor_marker"
  source "$rendered_stack_lib"
  hindsight_stack_sidecar_command reranker status
) >/dev/null 2>&1; then
  print -ru2 -- "sidecar command accepted a user-owned symlinked ancestor"
  exit 1
fi
sidecar_marker="$tmp_dir/sidecar-running"
sidecar_start="$tmp_dir/sidecar-start"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,second-profile"
  export HINDSIGHT_EMBED_API_PORT=7979
  export HINDSIGHT_EMBED_UI_PORT=17979
  export HINDSIGHT_EMBED_START_COOLDOWN_SECONDS=0
  export HINDSIGHT_TEST_SIDECAR_MARKER="$sidecar_marker"
  export HINDSIGHT_TEST_SIDECAR_START="$sidecar_start"
  source "$rendered_stack_lib"
  hindsight_stack_select_profile present-profile
  hindsight_stack_reconcile_sidecars >/dev/null
  [[ "$(<"$sidecar_start")" == "present-profile:0:reranker:${sidecar_port}" ]] || exit 1
  hindsight_stack_sidecars_status
  hindsight_stack_stop_sidecars
  hindsight_stack_wait_sidecars_stopped
  [[ ! -e "$sidecar_marker" ]]
)
[[ -s "$configured_python_calls" ]] || {
  print -ru2 -- "sidecar commands did not invoke the configured Python runtime"
  exit 1
}
rg -q '(^| )-[ ]+[0-9]+[ ]+' "$configured_python_calls" || {
  print -ru2 -- "sidecar commands did not enter the configured Python trampoline"
  exit 1
}

desired_state_root="$tmp_dir/component-desired-state"
outside_desired_state="$tmp_dir/outside-component-desired-state"
mkdir -p "$outside_desired_state"
ln -s "$outside_desired_state" "$tmp_dir/linked-component-desired-state"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_DESIRED_STATE_DIR="$tmp_dir/linked-component-desired-state"
  source "$rendered_stack_lib"
  hindsight_stack_set_desired_state daemon stopped present-profile
) >/dev/null 2>&1; then
  print -ru2 -- "component desired state accepted a symlinked root"
  exit 1
fi
[[ ! -e "$outside_desired_state/profiles" ]] || {
  print -ru2 -- "component desired state followed a symlinked root before rejection"
  exit 1
}

desired_state_starts="$tmp_dir/desired-state-starts"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_DESIRED_STATE_DIR="$desired_state_root"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,second-profile"
  source "$rendered_stack_lib"
  hindsight_stack_startup_id() { print -r -- login-one }
  hindsight_stack_initialize_desired_state
  [[ "$(hindsight_stack_desired_state daemon present-profile)" == running ]]
  [[ "$(hindsight_stack_desired_state ui present-profile)" == running ]]
  [[ "$(/usr/bin/stat -f '%Lp' "$desired_state_root/profiles/present-profile/daemon")" == 600 ]]

  hindsight_stack_set_desired_state daemon stopped present-profile
  hindsight_stack_set_desired_state ui stopped present-profile
  hindsight_stack_initialize_desired_state
  [[ "$(hindsight_stack_desired_state daemon present-profile)" == stopped ]] || {
    print -ru2 -- "same-login initialization discarded an intentional daemon stop"
    exit 1
  }
  [[ "$(hindsight_stack_desired_state ui present-profile)" == stopped ]] || {
    print -ru2 -- "same-login initialization discarded an intentional UI stop"
    exit 1
  }

  hindsight_stack_daemon_status() { return 1 }
  hindsight_stack_daemon_start() { print -r -- daemon >> "$desired_state_starts" }
  hindsight_stack_wait_daemon() { return 0 }
  hindsight_stack_ui_status() { return 1 }
  hindsight_stack_ui_start() { print -r -- ui >> "$desired_state_starts" }
  hindsight_stack_wait_ui() { return 0 }
  hindsight_stack_reconcile_daemon
  hindsight_stack_reconcile_ui
  [[ ! -e "$desired_state_starts" ]] || {
    print -ru2 -- "reconcile restarted intentionally stopped components"
    exit 1
  }
  [[ "$(hindsight_stack_status_word daemon)" == down ]]
  [[ "$(hindsight_stack_status_word ui)" == down ]]
  hindsight_stack_daemon_running() { return 1 }
  hindsight_stack_ui_running() { return 1 }
  [[ "$(hindsight_stack_status_word daemon)" == stopped ]]
  [[ "$(hindsight_stack_status_word ui)" == stopped ]]

  hindsight_stack_startup_id() { print -r -- login-two }
  hindsight_stack_initialize_desired_state
  [[ "$(hindsight_stack_desired_state daemon present-profile)" == running ]]
  [[ "$(hindsight_stack_desired_state ui present-profile)" == running ]]
  hindsight_stack_reconcile_daemon
  hindsight_stack_reconcile_ui
  [[ "$(paste -sd, "$desired_state_starts")" == daemon,ui ]] || {
    print -ru2 -- "new-login reset did not restore autostart reconciliation"
    exit 1
  }

  export HINDSIGHT_EMBED_AUTOSTART_UI=false
  hindsight_stack_reset_desired_state
  [[ "$(hindsight_stack_desired_state daemon present-profile)" == running ]]
  [[ "$(hindsight_stack_desired_state ui present-profile)" == stopped ]]
  [[ "$(hindsight_stack_desired_state daemon second-profile)" == running ]]
  [[ "$(hindsight_stack_desired_state ui second-profile)" == stopped ]]
)

intentional_status="$tmp_dir/intentional-component-status"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_DESIRED_STATE_DIR="$desired_state_root"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile"
  source "$rendered_stack_lib"
  hindsight_stack_set_desired_state daemon stopped present-profile
  hindsight_stack_set_desired_state ui stopped present-profile
  hindsight_stack_require_tools() { return 0 }
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_broker_identity_matches() { return 0 }
  hindsight_stack_control_status() { return 0 }
  hindsight_stack_daemon_status() { return 1 }
  hindsight_stack_daemon_running() { return 1 }
  hindsight_stack_ui_status() { return 1 }
  hindsight_stack_ui_running() { return 1 }
  hindsight_stack_sidecar_names() { return 0 }
  hindsight_stack_status_report > "$intentional_status"
)
rg -F -q 'fleet: healthy (1 enabled profile)' "$intentional_status" || {
  print -ru2 -- "intentional component stops degraded fleet status"
  exit 1
}
rg -F -q 'api=stopped@7979 ui=stopped@17979' "$intentional_status" || {
  print -ru2 -- "fleet status did not distinguish intentional stops from failures"
  exit 1
}

corrupt_desired_state="$desired_state_root/profiles/present-profile/daemon"
print -r -- invalid > "$corrupt_desired_state"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_DESIRED_STATE_DIR="$desired_state_root"
  source "$rendered_stack_lib"
  hindsight_stack_daemon_status() { return 1 }
  hindsight_stack_daemon_start() { touch "$tmp_dir/corrupt-desired-state-started" }
  hindsight_stack_reconcile_daemon
) >/dev/null 2>&1; then
  print -ru2 -- "reconcile accepted corrupt persisted component intent"
  exit 1
fi
[[ ! -e "$tmp_dir/corrupt-desired-state-started" ]] || {
  print -ru2 -- "reconcile started a component with corrupt persisted intent"
  exit 1
}

reconcile_events="$tmp_dir/reconcile-events"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,second-profile"
  export HINDSIGHT_EMBED_API_PORT=7979
  export HINDSIGHT_EMBED_UI_PORT=17979
  export HINDSIGHT_EMBED_AUTOSTART_DAEMON=true
  export HINDSIGHT_EMBED_AUTOSTART_UI=true
  source "$rendered_stack_lib"
  hindsight_stack_reconcile_broker() { print -r -- broker >> "$reconcile_events" }
  hindsight_stack_reconcile_control() { print -r -- control >> "$reconcile_events" }
  hindsight_stack_reconcile_sidecars() { print -r -- "sidecars:${HINDSIGHT_EMBED_PROFILE}" >> "$reconcile_events" }
  hindsight_stack_reconcile_daemon() { print -r -- "daemon:${HINDSIGHT_EMBED_PROFILE}:${HINDSIGHT_EMBED_API_PORT}" >> "$reconcile_events" }
  hindsight_stack_daemon_status() { return 0 }
  hindsight_stack_reconcile_ui() { print -r -- "ui:${HINDSIGHT_EMBED_PROFILE}:${HINDSIGHT_EMBED_UI_PORT}" >> "$reconcile_events" }
  hindsight_stack_reconcile_once
)
for expected in \
  broker control \
  sidecars:present-profile daemon:present-profile:7979 ui:present-profile:17979 \
  sidecars:second-profile daemon:second-profile:7980 ui:second-profile:17980; do
  rg -F -x -q "$expected" "$reconcile_events" || {
    print -ru2 -- "fleet reconcile omitted ${expected}"
    exit 1
  }
done

active_reconcile_events="$tmp_dir/active-reconcile-events"
(
  export HOME="$test_home"
  export HINDSIGHT_MEMORY_INVENTORY="$tmp_dir/inventory.json"
  export HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV=TEST_DATA_PLANE_TOKEN
  export HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV=TEST_MINT_AUTHORITY
  export HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV=TEST_UI_ACCESS_KEY
  export TEST_DATA_PLANE_TOKEN=test-data-plane-token
  export TEST_MINT_AUTHORITY=test-mint-authority
  export TEST_UI_ACCESS_KEY=test-ui-access-key
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile"
  export HINDSIGHT_EMBED_API_PORT=7979
  export HINDSIGHT_EMBED_UI_PORT=17979
  export HINDSIGHT_EMBED_AUTOSTART_DAEMON=true
  export HINDSIGHT_EMBED_AUTOSTART_UI=true
  source "$rendered_stack_lib"
  hindsight_stack_reconcile_broker() { print -r -- broker >> "$active_reconcile_events" }
  hindsight_stack_reconcile_control() { print -r -- control >> "$active_reconcile_events" }
  hindsight_stack_reconcile_profile() { print -r -- profile >> "$active_reconcile_events" }
  hindsight_stack_reconcile_once
)
[[ "$(<"$active_reconcile_events")" == $'control\nprofile\nbroker' ]] || {
  print -ru2 -- "active reconcile did not bring up the data plane before broker verification"
  exit 1
}

ui_dependency_marker="$tmp_dir/ui-started-without-daemon"
(
  export HOME="$test_home"
  export HINDSIGHT_EMBED_PROFILE=present-profile
  export HINDSIGHT_EMBED_FLEET_PROFILES=present-profile
  export HINDSIGHT_EMBED_AUTOSTART_DAEMON=false
  export HINDSIGHT_EMBED_AUTOSTART_UI=true
  source "$rendered_stack_lib"
  hindsight_stack_desired_state() {
    [[ "$1" == daemon ]] && print -r -- stopped || print -r -- running
  }
  hindsight_stack_daemon_status() { return 1 }
  hindsight_stack_ensure_profile_ports() { touch "$ui_dependency_marker" }
  if hindsight_stack_ui_start >/dev/null 2>&1; then
    print -ru2 -- "explicit UI startup accepted an unhealthy daemon"
    exit 1
  fi
  if hindsight_stack_reconcile_profile >/dev/null 2>&1; then
    print -ru2 -- "UI reconciliation accepted an unhealthy daemon"
    exit 1
  fi
  if hindsight_stack_start_profile >/dev/null 2>&1; then
    print -ru2 -- "profile startup accepted an unhealthy daemon required by the UI"
    exit 1
  fi
)
[[ ! -e "$ui_dependency_marker" ]] || {
  print -ru2 -- "UI startup mutated profile state before daemon health was established"
  exit 1
}

collision_output="$tmp_dir/collision.out"
if (
  export HOME="$test_home"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,second-profile"
  export HINDSIGHT_EMBED_API_PORT=7979
  export HINDSIGHT_EMBED_UI_PORT=17979
  export HINDSIGHT_EMBED_PROFILE_SECOND_PROFILE_API_PORT=7979
  source "$rendered_stack_lib"
  hindsight_stack_validate_fleet
) >"$collision_output" 2>&1; then
  print -ru2 -- "fleet validation accepted colliding profile endpoints"
  exit 1
fi
rg -F -q 'endpoint collision on port 7979' "$collision_output" || {
  print -ru2 -- "fleet validation did not identify the colliding port"
  exit 1
}

fake_memory_cli="$tmp_dir/fake-hindsight-memory"

stale_broker_state="$tmp_dir/stale-broker-state"
mkdir -m 700 "$stale_broker_state"
/bin/sleep 60 &
stale_broker_pid=$!
track_fixture_pid "$stale_broker_pid"
print -r -- "$stale_broker_pid" >"$stale_broker_state/broker-process.identity"
print -r -- original-identity >>"$stale_broker_state/broker-process.identity"
chmod 600 "$stale_broker_state/broker-process.identity"
if (
  export HINDSIGHT_EMBED_STATE_DIR="$stale_broker_state"
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_broker_process_identity() { print -r -- replacement-identity }
  hindsight_stack_broker_terminate_recorded
) >/dev/null 2>&1; then
  print -ru2 -- "broker accepted a changed recorded process identity"
  exit 1
fi
[[ ! -e "$stale_broker_state/broker-process.identity" ]] || {
  print -ru2 -- "broker retained a stale changed-identity process record"
  exit 1
}
/bin/kill -0 "$stale_broker_pid" >/dev/null 2>&1 || {
  print -ru2 -- "broker signaled a live process after rejecting changed identity"
  exit 1
}
/bin/kill -TERM "$stale_broker_pid" >/dev/null 2>&1 || true
untrack_fixture_pid "$stale_broker_pid"
wait "$stale_broker_pid" >/dev/null 2>&1 || true

cat > "$fake_memory_cli" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import sys
import time

Path(os.environ["HINDSIGHT_TEST_BROKER_ARGS"]).write_text(
    " ".join(sys.argv[1:]), encoding="utf-8"
)
arguments = sys.argv[1:]
operation = ""
for index, argument in enumerate(arguments[:-1]):
    if argument == "broker":
        operation = arguments[index + 1]
        break
if operation != "serve":
    raise SystemExit(0 if operation in {"status", "stop"} else 2)
print("broker output must be detached", file=sys.stderr)
signal.signal(signal.SIGTERM, lambda _signal, _frame: sys.exit(0))
while True:
    time.sleep(1)
PY
chmod 700 "$fake_memory_cli"
broker_args="$tmp_dir/broker-args"
broker_output="$tmp_dir/broker-output"
broker_python_calls="$tmp_dir/broker-python-calls"
hostile_python_dir="$tmp_dir/hostile-python"
hostile_python_marker="$tmp_dir/hostile-python-ran"
hostile_sitecustomize_marker="$tmp_dir/hostile-sitecustomize-ran"
mkdir "$hostile_python_dir"
cat > "$hostile_python_dir/python3" <<'ZSH'
#!/usr/bin/env zsh
touch "$HINDSIGHT_TEST_HOSTILE_PYTHON_MARKER"
exit 97
ZSH
cat > "$hostile_python_dir/sitecustomize.py" <<'PY'
import os
from pathlib import Path

Path(os.environ["HINDSIGHT_TEST_HOSTILE_SITECUSTOMIZE_MARKER"]).touch()
PY
chmod 700 "$hostile_python_dir/python3"
(
  unsetopt BG_NICE
  export HOME="$test_home"
  export PATH="$hostile_python_dir:/usr/bin:/bin"
  export PYTHONPATH="$hostile_python_dir"
  export HINDSIGHT_EMBED_STATE_DIR="$fleet_state"
  export HINDSIGHT_EMBED_PROFILE="present-profile"
  export HINDSIGHT_EMBED_FLEET_PROFILES="present-profile,second-profile"
  export HINDSIGHT_MEMORY_CLI="$fake_memory_cli"
  export HINDSIGHT_TEST_BROKER_ARGS="$broker_args"
  export HINDSIGHT_TEST_CONFIGURED_PYTHON_CALLS="$broker_python_calls"
  export HINDSIGHT_TEST_HOSTILE_PYTHON_MARKER="$hostile_python_marker"
  export HINDSIGHT_TEST_HOSTILE_SITECUSTOMIZE_MARKER="$hostile_sitecustomize_marker"
  source "$rendered_stack_lib"
  hindsight_stack_broker_process_identity() {
    local pid="$1" record state
    record="$(hindsight_stack_broker_process_record)" || return 1
    kill -0 "$pid" >/dev/null 2>&1 || return 1
    if [[ ! -s "$record" ]]; then
      state="$(/bin/ps -o state= -p "$pid" 2>/dev/null)" || return 1
      [[ "$state" == *T* ]] || return 1
    fi
    print -r -- stable-launch-identity
  }
  hindsight_stack_wait_broker() {
    local attempt
    for attempt in {1..100}; do
      [[ -e "$broker_args" ]] && return 0
      sleep 0.01
    done
    return 1
  }
  hindsight_stack_broker_start >"$broker_output" 2>&1 || {
    /bin/cat "$broker_output" >&2
    exit 1
  }
  hindsight_stack_broker_read_process_record
  started_broker_pid="$HINDSIGHT_STACK_BROKER_PID"
  if hindsight_stack_broker_terminate_recorded >/dev/null 2>&1; then
    print -ru2 -- "recorded broker termination signaled a live external PID"
    exit 1
  fi
  /bin/kill -KILL "$started_broker_pid" >/dev/null 2>&1 || true
  wait "$started_broker_pid" >/dev/null 2>&1 || true
  for _ in {1..100}; do
    /bin/kill -0 "$started_broker_pid" >/dev/null 2>&1 || break
    sleep 0.01
  done
  hindsight_stack_broker_terminate_recorded
)
rg -F -q -- '-I -c ' "$broker_python_calls" || {
  print -ru2 -- "broker launch did not use the configured isolated Python runtime"
  exit 1
}
rg -F -q -- "-I $fake_memory_cli" "$broker_python_calls" || {
  print -ru2 -- "broker launch did not execute the controller script with the configured isolated Python runtime"
  exit 1
}
[[ ! -e "$hostile_python_marker" && ! -e "$hostile_sitecustomize_marker" ]] || {
  print -ru2 -- "broker launch executed caller-selected Python startup code"
  exit 1
}
[[ ! -s "$broker_output" ]] || {
  print -ru2 -- "broker start inherited caller output descriptors"
  exit 1
}
broker_command="$(<"$broker_args")"
[[ "$broker_command" == *'broker serve'* &&
  "$broker_command" == *'--profile present-profile --profile second-profile'* ]] || {
  print -ru2 -- "broker did not receive the complete enabled-profile fleet: ${broker_command}"
  exit 1
}

for broker_operation in status stop; do
  hostile_operation_args="$tmp_dir/hostile-broker-${broker_operation}-args"
  (
    export PATH="$hostile_python_dir:/usr/bin:/bin"
    export PYTHONPATH="$hostile_python_dir"
    export HINDSIGHT_TEST_BROKER_ARGS="$hostile_operation_args"
    export HINDSIGHT_TEST_CONFIGURED_PYTHON_CALLS="$broker_python_calls"
    export HINDSIGHT_TEST_HOSTILE_PYTHON_MARKER="$hostile_python_marker"
    export HINDSIGHT_TEST_HOSTILE_SITECUSTOMIZE_MARKER="$hostile_sitecustomize_marker"
    export HINDSIGHT_MEMORY_CLI="$fake_memory_cli"
    export HINDSIGHT_MEMORY_BROKER_SOCKET="$tmp_dir/absent-hostile-broker.sock"
    source "$rendered_stack_lib"
    hindsight_stack_load_config() { return 0 }
    hindsight_stack_enabled_profiles() { print -r -- present-profile }
    if [[ "$broker_operation" == status ]]; then
      hindsight_stack_broker_status 2
    else
      HINDSIGHT_EMBED_STOP_WAIT_SECONDS=2
      hindsight_stack_broker_terminate_recorded() { return 0 }
      hindsight_stack_broker_remove_stale_socket() { return 0 }
      hindsight_stack_broker_stop
    fi
  )
  rg -F -q -- "broker $broker_operation" "$hostile_operation_args" || {
    print -ru2 -- "broker ${broker_operation} did not reach the isolated controller script"
    exit 1
  }
  [[ ! -e "$hostile_python_marker" && ! -e "$hostile_sitecustomize_marker" ]] || {
    print -ru2 -- "broker ${broker_operation} executed caller-selected Python startup code"
    exit 1
  }
done
status_output="$tmp_dir/status.out"
if HOME="$test_home" \
  HINDSIGHT_EMBED_STACK_LIB="$rendered_stack_lib" \
  HINDSIGHT_EMBED_PROFILE="missing-profile" \
  zsh "$repo_dir/bin/hindsight-embed-service" status \
  >"$status_output" 2>&1; then
  print -ru2 -- "status unexpectedly succeeded for a missing profile"
  exit 1
fi

rg -F -q 'configured profile: missing (missing-profile)' "$status_output" || {
  print -ru2 -- "status did not report the missing profile"
  exit 1
}

stale_broker_socket="$tmp_dir/stale-broker.sock"
/usr/bin/python3 - "$stale_broker_socket" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(sys.argv[1])
sock.close()
PY
if (
  export HINDSIGHT_EMBED_PROFILE=present-profile
  export HINDSIGHT_MEMORY_BROKER_SOCKET="$stale_broker_socket"
  source "$rendered_stack_lib"
  hindsight_stack_broker_status() { return 1 }
  hindsight_stack_broker_running
) >/dev/null 2>&1; then
  print -ru2 -- "broker running accepted an unverified stale socket node"
  exit 1
fi

broker_mismatch_events="$tmp_dir/broker-mismatch-events"
(
  export HINDSIGHT_MEMORY_BROKER_SOCKET="$stale_broker_socket"
  source "$rendered_stack_lib"
  hindsight_stack_broker_status() { return 1 }
  hindsight_stack_can_start() { return 0 }
  hindsight_stack_broker_stop() { print -r -- stop >>"$broker_mismatch_events" }
  hindsight_stack_wait_stopped_for() { print -r -- wait-stopped >>"$broker_mismatch_events" }
  hindsight_stack_mark_start() { return 0 }
  hindsight_stack_log() { return 0 }
  hindsight_stack_broker_start() { print -r -- start >>"$broker_mismatch_events" }
  hindsight_stack_wait_broker() { print -r -- wait-ready >>"$broker_mismatch_events" }
  hindsight_stack_reconcile_broker
)
[[ "$(paste -sd, - <"$broker_mismatch_events")" == start &&
  -S "$stale_broker_socket" ]] || {
  print -ru2 -- "broker reconcile removed a socket after only a profile-specific status failure"
  exit 1
}

broker_stop_args="$tmp_dir/broker-stop-args"
(
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_STOP_WAIT_SECONDS=7
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_run_bounded() { print -r -- "$@" >"$broker_stop_args" }
  hindsight_stack_broker_terminate_recorded() { return 0 }
  hindsight_stack_broker_remove_stale_socket() { return 0 }
  HINDSIGHT_MEMORY_BROKER_SOCKET="$tmp_dir/absent-broker.sock"
  hindsight_stack_broker_stop
)
broker_stop_command="$(<"$broker_stop_args")"
[[ "$broker_stop_command" == "12 $configured_python -I $HINDSIGHT_MEMORY_CLI "* &&
  "$broker_stop_command" == *'--timeout 7' ]] || {
  print -ru2 -- "broker stop outer deadline did not exceed its CLI timeout: ${broker_stop_command}"
  exit 1
}

broker_status_args="$tmp_dir/broker-status-args"
(
  source "$rendered_stack_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_enabled_profiles() { print -r -- present-profile }
  hindsight_stack_run_bounded() { print -r -- "$@" >"$broker_status_args" }
  hindsight_stack_broker_status 9
)
broker_status_command="$(<"$broker_status_args")"
[[ "$broker_status_command" == "9 $configured_python -I $HINDSIGHT_MEMORY_CLI "* &&
  "$broker_status_command" == *'broker status'* ]] || {
  print -ru2 -- "broker status bypassed the configured isolated Python runtime: ${broker_status_command}"
  exit 1
}

failed_broker_cli="$tmp_dir/failed-broker-cli"
failed_broker_pid="$tmp_dir/failed-broker.pid"
unrelated_broker_job_pid="$tmp_dir/unrelated-broker-job.pid"
cat >"$failed_broker_cli" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import time

Path(os.environ["HINDSIGHT_TEST_FAILED_BROKER_PID"]).write_text(
    str(os.getpid()), encoding="utf-8"
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
PY
chmod 700 "$failed_broker_cli"
if (
  unsetopt BG_NICE
  source "$rendered_stack_lib"
  HINDSIGHT_MEMORY_CLI="$failed_broker_cli"
  export HINDSIGHT_TEST_FAILED_BROKER_PID="$failed_broker_pid"
  HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/failed-broker-state"
  HINDSIGHT_MEMORY_BROKER_SOCKET="$tmp_dir/failed-broker.sock"
  hindsight_stack_wait_broker() {
    local attempt
    for attempt in {1..100}; do
      if [[ -e "$failed_broker_pid" ]]; then
        /bin/sleep 60 &
        print -r -- "$!" >"$unrelated_broker_job_pid"
        return 1
      fi
      sleep 0.01
    done
    return 1
  }
  hindsight_stack_broker_start
) >/dev/null 2>&1; then
  print -ru2 -- "broker start accepted a failed readiness check"
  exit 1
fi
track_fixture_pid_file "$unrelated_broker_job_pid"
unrelated_broker_job="$(<"$unrelated_broker_job_pid")"
if ! /bin/kill -0 "$unrelated_broker_job" >/dev/null 2>&1; then
  print -ru2 -- "broker launch abort terminated an unrelated current job"
  exit 1
fi
/bin/kill -TERM "$unrelated_broker_job" >/dev/null 2>&1 || true
untrack_fixture_pid "$unrelated_broker_job"
wait "$unrelated_broker_job" >/dev/null 2>&1 || true
track_fixture_pid_file "$failed_broker_pid"
failed_broker_process="$(<"$failed_broker_pid")"
if /bin/kill -0 "$failed_broker_process" >/dev/null 2>&1; then
  print -ru2 -- "broker start left its unhealthy child running"
  exit 1
fi
untrack_fixture_pid "$failed_broker_process"
[[ ! -e "$tmp_dir/failed-broker-state/broker-process.identity" ]] || {
  print -ru2 -- "broker start retained an unhealthy process identity"
  exit 1
}

invalid_broker_probe="$tmp_dir/invalid-broker-probe"
if (
  export HINDSIGHT_EMBED_FLEET_PROFILES='../invalid'
  source "$rendered_stack_lib"
  hindsight_stack_run_bounded() { touch "$invalid_broker_probe" }
  hindsight_stack_broker_status
) >/dev/null 2>&1; then
  print -ru2 -- "broker status accepted an invalid enabled profile"
  exit 1
fi
[[ ! -e "$invalid_broker_probe" ]] || {
  print -ru2 -- "broker status probed before validating enabled profiles"
  exit 1
}

mkdir -p "$test_home/Library/LaunchAgents" "$test_home/.local/bin"
touch "$HINDSIGHT_EMBED_SERVICE_MANIFEST"
touch "$test_home/.local/bin/hindsight-embed-supervisor"
chmod 700 "$test_home/.local/bin/hindsight-embed-supervisor"
runtime_helper="$tmp_dir/hindsight-embed-stop-profile-services.py"
touch "$runtime_helper"

untrusted_stack_lib="$tmp_dir/untrusted-stack.zsh"
untrusted_stack_marker="$tmp_dir/untrusted-stack-sourced"
print -r -- "touch ${(q)untrusted_stack_marker}" > "$untrusted_stack_lib"
chmod 666 "$untrusted_stack_lib"
if HINDSIGHT_EMBED_STACK_LIB="$untrusted_stack_lib" \
  zsh "$repo_dir/bin/hindsight-embed-supervisor" >/dev/null 2>&1; then
  print -ru2 -- "supervisor accepted an untrusted stack library"
  exit 1
fi
[[ ! -e "$untrusted_stack_marker" ]] || {
  print -ru2 -- "supervisor sourced an untrusted stack library before validation"
  exit 1
}
acl_stack_dir="$tmp_dir/acl-stack-dir"
acl_stack_lib="$acl_stack_dir/stack.zsh"
acl_stack_marker="$tmp_dir/acl-stack-sourced"
mkdir "$acl_stack_dir"
print -r -- "touch ${(q)acl_stack_marker}; exit 0" >"$acl_stack_lib"
chmod 600 "$acl_stack_lib"
chmod +a 'everyone allow read' "$acl_stack_lib"
if HINDSIGHT_EMBED_STACK_LIB="$acl_stack_lib" \
  zsh "$repo_dir/bin/hindsight-embed-supervisor" >/dev/null 2>&1; then
  print -ru2 -- "supervisor accepted a stack library with an ACL"
  exit 1
fi
chmod -N "$acl_stack_lib"
[[ ! -e "$acl_stack_marker" ]] || {
  print -ru2 -- "supervisor sourced a stack library with an ACL"
  exit 1
}
chmod +a 'everyone allow read' "$acl_stack_dir"
if HINDSIGHT_EMBED_STACK_LIB="$acl_stack_lib" \
  zsh "$repo_dir/bin/hindsight-embed-supervisor" >/dev/null 2>&1; then
  print -ru2 -- "supervisor accepted a stack library beneath an ACL-bearing ancestor"
  exit 1
fi
chmod -N "$acl_stack_dir"
[[ ! -e "$acl_stack_marker" ]] || {
  print -ru2 -- "supervisor sourced a stack library beneath an ACL-bearing ancestor"
  exit 1
}
chmod +a 'everyone deny write' "$acl_stack_lib"
if ! HINDSIGHT_EMBED_STACK_LIB="$acl_stack_lib" \
  zsh "$repo_dir/bin/hindsight-embed-supervisor" >/dev/null 2>&1; then
  print -ru2 -- "supervisor rejected a non-permissive deny ACL"
  exit 1
fi
chmod -N "$acl_stack_lib"
[[ -e "$acl_stack_marker" ]] || {
  print -ru2 -- "supervisor did not source a stack library with a deny ACL"
  exit 1
}
/bin/rm -f "$acl_stack_marker"
service_acl_dir="$tmp_dir/service-acl-dir"
service_acl_file="$service_acl_dir/helper"
mkdir "$service_acl_dir"
print -r -- '#!/usr/bin/env zsh' >"$service_acl_file"
chmod 700 "$service_acl_file"
chmod +a 'everyone allow read' "$service_acl_file"
if ( source "$service_lib"; validate_trusted_artifact "$service_acl_file" "ACL helper" executable ) >/dev/null 2>&1; then
  print -ru2 -- "service accepted an ACL-bearing helper"
  exit 1
fi
chmod -N "$service_acl_file"
chmod +a 'everyone allow read' "$service_acl_dir"
if ( source "$service_lib"; validate_trusted_artifact "$service_acl_file" "ACL ancestor helper" executable ) >/dev/null 2>&1; then
  print -ru2 -- "service accepted a helper beneath an ACL-bearing ancestor"
  exit 1
fi
chmod -N "$service_acl_dir"
chmod +a 'everyone deny write' "$service_acl_file"
if ! ( source "$service_lib"; validate_trusted_artifact "$service_acl_file" "deny ACL helper" executable ) >/dev/null 2>&1; then
  print -ru2 -- "service rejected a non-permissive deny ACL"
  exit 1
fi
chmod -N "$service_acl_file"
service_private_parent="$tmp_dir/service-private-parent"
service_private_directory="$service_private_parent/state"
mkdir "$service_private_parent"
chmod +a 'everyone allow read' "$service_private_parent"
if (
  source "$service_lib"
  prepare_private_directory "$service_private_directory" "test service state"
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted a private directory beneath an ACL-bearing ancestor"
  exit 1
fi
chmod -N "$service_private_parent"
chmod +a 'everyone deny write' "$service_private_parent"
if ! (
  source "$service_lib"
  prepare_private_directory "$service_private_directory" "test service state"
) >/dev/null 2>&1; then
  print -ru2 -- "service rejected a private directory beneath a non-permissive deny ACL"
  exit 1
fi
chmod -N "$service_private_parent"
if USER=definitely-not-the-current-user \
  HINDSIGHT_EMBED_STACK_LIB="$rendered_stack_lib" \
  zsh "$repo_dir/bin/hindsight-embed-supervisor" >/dev/null 2>&1; then
  print -ru2 -- "supervisor accepted a USER/uid mismatch"
  exit 1
fi

if (
  source "$service_lib"
  typeset -g STACK_LIB="$untrusted_stack_lib"
  load_stack_lib
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted an untrusted stack library"
  exit 1
fi
[[ ! -e "$untrusted_stack_marker" ]] || {
  print -ru2 -- "service sourced an untrusted stack library before validation"
  exit 1
}
trusted_artifact_link="$tmp_dir/trusted-artifact-link"
ln -s "$repo_dir/bin/hindsight-memory" "$trusted_artifact_link"
(
  source "$service_lib"
  validate_trusted_artifact "$trusted_artifact_link" "trusted executable" executable allow-symlink
)
if (
  source "$service_lib"
  validate_trusted_artifact "$trusted_artifact_link" "manifest"
) >/dev/null 2>&1; then
  print -ru2 -- "service allowed a symlink for an artifact that requires a regular file"
  exit 1
fi
untrusted_artifact_dir="$tmp_dir/untrusted-artifact-dir"
mkdir "$untrusted_artifact_dir"
chmod 777 "$untrusted_artifact_dir"
ln -s "$repo_dir/bin/hindsight-memory" "$untrusted_artifact_dir/controller"
if (
  source "$service_lib"
  validate_trusted_artifact "$untrusted_artifact_dir/controller" "memory controller" executable allow-symlink
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted an artifact through a writable ancestor"
  exit 1
fi

validated_manifest="$tmp_dir/validated-service.plist"
/usr/bin/python3 - "$validated_manifest" <<'PY'
import os
import plistlib
import sys

with open(sys.argv[1], "wb") as handle:
    required = {
        key: value for key, value in os.environ.items()
        if key in {
            "HINDSIGHT_EMBED_STACK_LIB", "HINDSIGHT_EMBED_UVX",
            "HINDSIGHT_EMBED_CONTROL_PORT", "HINDSIGHT_EMBED_CONTROL_HOSTNAME",
            "HINDSIGHT_EMBED_PRIMARY_PROFILE", "HINDSIGHT_EMBED_FLEET_PROFILES",
            "HINDSIGHT_EMBED_API_BASE_PORT", "HINDSIGHT_EMBED_UI_BASE_PORT",
            "HINDSIGHT_EMBED_UI_HOSTNAME", "HINDSIGHT_EMBED_PYTHON",
            "HINDSIGHT_EMBED_STOP_HELPER", "HINDSIGHT_MEMORY_CLI",
            "HINDSIGHT_MEMORY_STATE_DIR", "HINDSIGHT_MEMORY_BROKER_SOCKET",
            "HINDSIGHT_EMBED_STATE_DIR", "HINDSIGHT_EMBED_AUTOSTART_DAEMON",
            "HINDSIGHT_EMBED_AUTOSTART_UI",
        }
    }
    plistlib.dump({"Label": "com.example.validated", "ProgramArguments": ["/usr/bin/true"], "EnvironmentVariables": required}, handle)
PY
if (
  source "$service_lib"
  bootstrap_manifest "$validated_manifest" com.example.wrong-label
) >/dev/null 2>&1; then
  print -ru2 -- "service bootstrapped a manifest whose Label did not match the requested job"
  exit 1
fi
(
  source "$service_lib"
  STATE_DIR="$tmp_dir/staging-state"
  staged="$(stage_validated_manifest "$validated_manifest" "test manifest" com.example.validated /usr/bin/true)"
  [[ "$staged" != "$validated_manifest" &&
    "$(/usr/bin/stat -f '%Lp' "$staged")" == 400 &&
    "$(/usr/bin/stat -f '%Sf' "$staged")" == *uchg* ]]
  /usr/bin/python3 - "$validated_manifest" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "wb") as handle:
    plistlib.dump({"Label": "com.example.mutated", "ProgramArguments": ["/usr/bin/false"]}, handle)
PY
  if /usr/bin/cmp -s "$staged" "$validated_manifest"; then
    exit 1
  fi
  persist_service_manifest_snapshot "$staged"
  /usr/bin/cmp -s "$staged" "$STATE_DIR/rollback/last-known-good.plist"
  remove_staged_manifest "$staged"
) || {
  print -ru2 -- "service did not preserve an immutable validated manifest through promotion"
  exit 1
}
binding_mismatch_manifest="$tmp_dir/binding-mismatch-service.plist"
/bin/cp "$tmp_dir/staging-state/rollback/last-known-good.plist" "$binding_mismatch_manifest"
/usr/bin/python3 -I -c '
import plistlib
import sys
path = sys.argv[1]
with open(path, "rb") as handle:
    value = plistlib.load(handle)
value["EnvironmentVariables"]["HINDSIGHT_EMBED_CONTROL_PORT"] = "9999"
with open(path, "wb") as handle:
    plistlib.dump(value, handle)
' "$binding_mismatch_manifest"
if (
  source "$service_lib"
  STATE_DIR="$tmp_dir/binding-mismatch-state"
  stage_validated_manifest \
    "$binding_mismatch_manifest" "service manifest" \
    com.example.validated /usr/bin/true current
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted a current manifest with mismatched lifecycle bindings"
  exit 1
fi

lifecycle_state="$tmp_dir/lifecycle-lock-state"
lifecycle_ready="$tmp_dir/lifecycle-lock-ready"
lifecycle_observed="$tmp_dir/lifecycle-lock-observed"
lifecycle_attempted="$tmp_dir/lifecycle-lock-attempted"
lifecycle_release="$tmp_dir/lifecycle-lock-release"
(
  source "$service_lib"
  STATE_DIR="$lifecycle_state"
  hold_lifecycle_lock() {
    touch "$lifecycle_ready"
    while [[ ! -e "$lifecycle_release" ]]; do sleep 0.01; done
  }
  run_with_service_lifecycle_lock hold_lifecycle_lock
) &
lifecycle_holder_pid=$!
track_fixture_pid "$lifecycle_holder_pid"
for _ in {1..1000}; do
  [[ -e "$lifecycle_ready" ]] && break
  sleep 0.01
done
[[ -e "$lifecycle_ready" ]] || {
  print -ru2 -- "service lifecycle lock holder did not become ready"
  exit 1
}
(
  source "$rendered_stack_lib"
  HINDSIGHT_EMBED_STATE_DIR="$lifecycle_state"
  observe_lifecycle_lock() { touch "$lifecycle_observed" }
  touch "$lifecycle_attempted"
  hindsight_stack_with_lifecycle_lock observe_lifecycle_lock
) &
lifecycle_observer_pid=$!
track_fixture_pid "$lifecycle_observer_pid"
for _ in {1..1000}; do
  [[ -e "$lifecycle_attempted" ]] && break
  sleep 0.01
done
[[ -e "$lifecycle_attempted" ]] || {
  print -ru2 -- "service lifecycle lock contender did not attempt acquisition"
  exit 1
}
sleep 0.1
kill -0 "$lifecycle_observer_pid" >/dev/null 2>&1 || {
  print -ru2 -- "shared lifecycle-lock contender exited instead of blocking"
  exit 1
}
[[ ! -e "$lifecycle_observed" ]] || {
  print -ru2 -- "service lifecycle lock admitted concurrent mutation"
  exit 1
}
touch "$lifecycle_release"
untrack_fixture_pid "$lifecycle_holder_pid"
wait "$lifecycle_holder_pid"
untrack_fixture_pid "$lifecycle_observer_pid"
wait "$lifecycle_observer_pid"
[[ -e "$lifecycle_observed" ]] || {
  print -ru2 -- "service lifecycle lock did not release for the next mutation"
  exit 1
}

service_command_ready="$tmp_dir/service-command-ready"
service_command_attempted="$tmp_dir/service-command-attempted"
service_command_observed="$tmp_dir/service-command-observed"
service_command_lifecycle="$tmp_dir/service-command-lifecycle"
service_command_release="$tmp_dir/service-command-release"
(
  source "$service_lib"
  STATE_DIR="$lifecycle_state"
  stop_service() {
    run_with_service_lifecycle_lock /usr/bin/touch "$service_command_lifecycle" || return 1
    touch "$service_command_ready"
    while [[ ! -e "$service_command_release" ]]; do sleep 0.01; done
  }
  main stop
) &
service_command_holder_pid=$!
track_fixture_pid "$service_command_holder_pid"
for _ in {1..1500}; do
  [[ -e "$service_command_ready" ]] && break
  kill -0 "$service_command_holder_pid" >/dev/null 2>&1 || break
  sleep 0.01
done
[[ -e "$service_command_ready" && -e "$service_command_lifecycle" ]] || {
  print -ru2 -- "top-level service stop deadlocked while acquiring its lifecycle lock"
  exit 1
}
(
  source "$service_lib"
  STATE_DIR="$lifecycle_state"
  stop_service() { touch "$service_command_observed" }
  touch "$service_command_attempted"
  main stop
) &
service_command_observer_pid=$!
track_fixture_pid "$service_command_observer_pid"
for _ in {1..1000}; do
  [[ -e "$service_command_attempted" ]] && break
  sleep 0.01
done
[[ -e "$service_command_attempted" ]] || {
  print -ru2 -- "second top-level service stop did not attempt serialization"
  exit 1
}
sleep 0.1
kill -0 "$service_command_observer_pid" >/dev/null 2>&1 || {
  print -ru2 -- "second top-level service stop exited instead of blocking"
  exit 1
}
[[ ! -e "$service_command_observed" ]] || {
  print -ru2 -- "top-level service stops overlapped preflight or cleanup"
  exit 1
}
touch "$service_command_release"
untrack_fixture_pid "$service_command_holder_pid"
wait "$service_command_holder_pid"
untrack_fixture_pid "$service_command_observer_pid"
wait "$service_command_observer_pid"
[[ -e "$service_command_observed" ]] || {
  print -ru2 -- "top-level service stop lock did not release to its contender"
  exit 1
}

maintenance_ready="$tmp_dir/maintenance-ready"
maintenance_attempted="$tmp_dir/maintenance-attempted"
maintenance_observed="$tmp_dir/maintenance-observed"
maintenance_release="$tmp_dir/maintenance-release"
(
  source "$service_lib"
  STATE_DIR="$lifecycle_state"
  hold_maintenance() {
    touch "$maintenance_ready"
    while [[ ! -e "$maintenance_release" ]]; do sleep 0.01; done
  }
  run_with_service_maintenance_lease hold_maintenance
) &
maintenance_holder_pid=$!
track_fixture_pid "$maintenance_holder_pid"
for _ in {1..1000}; do
  [[ -e "$maintenance_ready" ]] && break
  sleep 0.01
done
[[ -e "$maintenance_ready" ]] || {
  print -ru2 -- "service maintenance holder did not become ready"
  exit 1
}
(
  source "$service_lib"
  STATE_DIR="$lifecycle_state"
  touch "$maintenance_attempted"
  run_with_service_maintenance_lease /usr/bin/touch "$maintenance_observed"
) &
maintenance_observer_pid=$!
track_fixture_pid "$maintenance_observer_pid"
for _ in {1..100}; do
  [[ -e "$maintenance_attempted" ]] && break
  sleep 0.01
done
sleep 0.1
kill -0 "$maintenance_observer_pid" >/dev/null 2>&1 || {
  print -ru2 -- "service maintenance contender exited instead of blocking"
  exit 1
}
[[ ! -e "$maintenance_observed" ]] || {
  print -ru2 -- "service start/install maintenance lease admitted overlap"
  exit 1
}
touch "$maintenance_release"
untrack_fixture_pid "$maintenance_holder_pid"
wait "$maintenance_holder_pid"
untrack_fixture_pid "$maintenance_observer_pid"
wait "$maintenance_observer_pid"
[[ -e "$maintenance_observed" ]] || {
  print -ru2 -- "service maintenance lease did not release to its contender"
  exit 1
}
for private_path in \
  "$lifecycle_state" \
  "$lifecycle_state/logs" \
  "$lifecycle_state/staged-manifests" \
  "$lifecycle_state/rollback" \
  "$lifecycle_state/legacy"; do
  [[ "$(/usr/bin/stat -f '%u:%Lp' "$private_path")" == "${EUID}:700" ]] || {
    print -ru2 -- "service did not prepare private trusted state at ${private_path}"
    exit 1
  }
done
[[ "$(/usr/bin/stat -f '%u:%Lp' "$HINDSIGHT_EMBED_SERVICE_LOG")" == "${EUID}:600" ]] || {
  print -ru2 -- "service did not prepare a private supervisor log"
  exit 1
}

legacy_start_mutation="$tmp_dir/legacy-start-mutation"
if (
  source "$service_lib"
  is_loaded() { [[ "$1" == "$LEGACY_LABEL" ]] }
  preflight_launchd_service() { touch "$legacy_start_mutation" }
  stage_validated_manifest() { touch "$legacy_start_mutation" }
  load_launchd_service() { touch "$legacy_start_mutation" }
  start_launchd_service
) >/dev/null 2>&1; then
  print -ru2 -- "service start accepted a loaded legacy launchd job"
  exit 1
fi
[[ ! -e "$legacy_start_mutation" ]] || {
  print -ru2 -- "service start mutated state before rejecting the loaded legacy job"
  exit 1
}

for command in install start restart stop logs; do
  extra_arg_mutation="$tmp_dir/extra-arg-${command}"
  if (
    source "$service_lib"
    load_stack_lib() { touch "$extra_arg_mutation" }
    run_with_service_lifecycle_lock() { touch "$extra_arg_mutation" }
    show_logs() { touch "$extra_arg_mutation" }
    main "$command" unexpected
  ) >/dev/null 2>&1; then
    print -ru2 -- "service ${command} accepted an extra argument"
    exit 1
  fi
  [[ ! -e "$extra_arg_mutation" ]] || {
    print -ru2 -- "service ${command} acted before rejecting an extra argument"
    exit 1
  }
done

invalid_config_artifact_check="$tmp_dir/invalid-config-artifact-check"
if (
  source "$service_lib"
  hindsight_stack_load_config() { return 1 }
  validate_trusted_artifact() { touch "$invalid_config_artifact_check" }
  validate_installed_files
) >/dev/null 2>&1; then
  print -ru2 -- "installed-file validation accepted a stack configuration error"
  exit 1
fi
[[ ! -e "$invalid_config_artifact_check" ]] || {
  print -ru2 -- "installed-file validation continued after a stack configuration error"
  exit 1
}

credential_preflight_artifact_check="$tmp_dir/credential-preflight-artifact-check"
if (
  source "$service_lib"
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_preflight_runtime_credentials() { return 1 }
  validate_trusted_artifact() { touch "$credential_preflight_artifact_check" }
  validate_installed_files
) >/dev/null 2>&1; then
  print -ru2 -- "installed-file validation accepted a runtime credential preflight failure"
  exit 1
fi
[[ ! -e "$credential_preflight_artifact_check" ]] || {
  print -ru2 -- "installed-file validation continued after a runtime credential preflight failure"
  exit 1
}

diagnostic_config_output="$tmp_dir/diagnostic-config-output"
(
  source "$service_lib"
  load_stack_lib() { typeset -g HINDSIGHT_EMBED_STACK_LIB_LOADED=1; return 0 }
  hindsight_stack_load_config() { return 1 }
  show_installed_file_checks
) >"$diagnostic_config_output" 2>&1 || true
rg -F -q 'stack library: failed to load or invalid' "$diagnostic_config_output" || {
  print -ru2 -- "diagnostic status marked the stack loaded before configuration succeeded"
  exit 1
}

if (
  source "$service_lib"
  STACK_LABEL='com.example.hindsight/unsafe'
  require_service_bindings
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted an unsafe launchd label"
  exit 1
fi

if (
  source "$service_lib"
  LEGACY_LABEL="$STACK_LABEL"
  require_service_bindings
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted colliding managed and legacy launchd labels"
  exit 1
fi

binding_collision_manifest="$tmp_dir/binding-collision.plist"
binding_collision_alias="$tmp_dir/binding-collision-alias.plist"
touch "$binding_collision_manifest"
ln -s "$binding_collision_manifest" "$binding_collision_alias"
if (
  source "$service_lib"
  PLIST="$binding_collision_manifest"
  LEGACY_PLIST="$binding_collision_alias"
  require_service_bindings
) >/dev/null 2>&1; then
  print -ru2 -- "service accepted managed and legacy manifests resolving to one path"
  exit 1
fi

untrusted_status_manifest="$tmp_dir/untrusted-status.plist"
/usr/bin/python3 - "$untrusted_status_manifest" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "wb") as handle:
    plistlib.dump({"Label": "com.example.untrusted"}, handle)
PY
chmod 666 "$untrusted_status_manifest"
untrusted_status_manifest_output="$tmp_dir/untrusted-status-manifest-output"
(
  source "$service_lib"
  PLIST="$untrusted_status_manifest"
  load_stack_lib() { return 1 }
  show_installed_file_checks
) >"$untrusted_status_manifest_output" 2>&1 || true
rg -F -q 'plist: missing, untrusted, or symlinked' "$untrusted_status_manifest_output" || {
  print -ru2 -- "service status trusted an unsafe manifest ownership or mode"
  exit 1
}

trusted_invalid_status_manifest="$tmp_dir/trusted-invalid-status.plist"
/bin/cp "$untrusted_status_manifest" "$trusted_invalid_status_manifest"
chmod 600 "$trusted_invalid_status_manifest"
trusted_invalid_status_output="$tmp_dir/trusted-invalid-status-output"
(
  source "$service_lib"
  PLIST="$trusted_invalid_status_manifest"
  load_stack_lib() { return 1 }
  show_installed_file_checks
) >"$trusted_invalid_status_output" 2>&1 || true
rg -F -q 'plist: ok' "$trusted_invalid_status_output" &&
  rg -F -q 'plist lint: ok' "$trusted_invalid_status_output" &&
  rg -F -q 'plist contract: failed' "$trusted_invalid_status_output" || {
  /bin/cat "$trusted_invalid_status_output" >&2
  print -ru2 -- "service status did not reject a trusted, lintable manifest with invalid lifecycle bindings"
  exit 1
}

hostile_zdotdir="$tmp_dir/hostile-zdotdir"
hostile_zsh_marker="$tmp_dir/hostile-zsh-startup-ran"
mkdir -p "$hostile_zdotdir"
print -r -- "touch ${(q)hostile_zsh_marker}" >"$hostile_zdotdir/.zshenv"
(
  source "$service_lib"
  STATE_DIR="$tmp_dir/hostile-zsh-state"
  ZDOTDIR="$hostile_zdotdir" wait_for_manifest_stack_health "$validated_manifest"
) >/dev/null 2>&1 || true
[[ ! -e "$hostile_zsh_marker" ]] || {
  print -ru2 -- "service lifecycle probe executed hostile user Zsh startup code"
  exit 1
}

snapshot_source="$tmp_dir/snapshot-source.plist"
/bin/cp "$validated_manifest" "$snapshot_source"
/bin/chmod 400 "$snapshot_source"
snapshot_path=$(
  source "$service_lib"
  STATE_DIR="$tmp_dir/snapshot-state"
  mkdir -p "$STATE_DIR/rollback"
  HINDSIGHT_EMBED_SERVICE_ROLLBACK_MANIFEST="$snapshot_source"
  snapshot_service_manifest
) || exit 1
[[ "$(/usr/bin/stat -f '%Lp' "$snapshot_path")" == 600 ]] &&
  /usr/bin/cmp -s "$snapshot_source" "$snapshot_path" || {
  print -ru2 -- "service rollback snapshot did not normalize metadata while preserving bytes"
  exit 1
}

hostile_python_dir="$tmp_dir/hostile-python"
hostile_python_marker="$tmp_dir/hostile-python-startup-ran"
mkdir -p "$hostile_python_dir"
print -r -- "open(${(qqq)hostile_python_marker}, 'w').close()" >"$hostile_python_dir/sitecustomize.py"
(
  source "$service_lib"
  STATE_DIR="$tmp_dir/persist-state"
  mkdir -p "$STATE_DIR/rollback"
  PYTHONPATH="$hostile_python_dir" persist_service_manifest_snapshot "$snapshot_source"
) || exit 1
[[ ! -e "$hostile_python_marker" ]] || {
  print -ru2 -- "service rollback persistence imported hostile Python startup code"
  exit 1
}

assert_missing_profile_blocks_mutation() {
  local command="$1"
  local mutation_marker="$tmp_dir/${command}.mutated"
  local output="$tmp_dir/${command}.out"

  if (
    export HOME="$test_home"
    export HINDSIGHT_EMBED_STACK_LIB="$rendered_stack_lib"
    export HINDSIGHT_EMBED_PROFILE="missing-profile"
    export HINDSIGHT_EMBED_UVX="/usr/bin/true"
    export HINDSIGHT_EMBED_PYTHON="/usr/bin/true"
    export HINDSIGHT_EMBED_STOP_HELPER="$runtime_helper"

    source "$service_lib"
    load_stack_lib

    bootout_if_loaded() {
      touch "$mutation_marker"
    }
    load_launchd_service() {
      touch "$mutation_marker"
    }

    case "$command" in
      start)
        start_launchd_service
        ;;
      install)
        install_service
        ;;
    esac
  ) >"$output" 2>&1; then
    print -ru2 -- "${command} unexpectedly succeeded for a missing profile"
    return 1
  fi

  if [[ -e "$mutation_marker" ]]; then
    print -ru2 -- "${command} reached a launchd mutation for a missing profile"
    return 1
  fi

  rg -F -q "configured profile 'missing-profile' does not exist" "$output" || {
    print -ru2 -- "${command} did not report the missing profile preflight"
    return 1
  }
}

assert_missing_profile_blocks_mutation start
assert_missing_profile_blocks_mutation install

healthy_start_persisted="$tmp_dir/healthy-start-persisted"
healthy_start_lock_reentered="$tmp_dir/healthy-start-lock-reentered"
(
  source "$service_lib"
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { return 1 }
  load_launchd_service() {
    run_with_service_lifecycle_lock /usr/bin/touch "$healthy_start_lock_reentered"
  }
  persist_service_manifest_snapshot() {
    print -r -- "$1" >"$healthy_start_persisted"
  }
  start_launchd_service
)
[[ -e "$healthy_start_lock_reentered" &&
  "$(<"$healthy_start_persisted")" == "$HINDSIGHT_EMBED_SERVICE_MANIFEST" ]] || {
  print -ru2 -- "start did not release lifecycle state locking before health or persist its healthy manifest"
  exit 1
}

failed_start_events="$tmp_dir/failed-start-events"
if (
  source "$service_lib"
  typeset -g stack_loaded=0
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )) }
  wait_for_manifest_stack_health() { return 1 }
  load_launchd_service() {
    stack_loaded=1
    print -r -- load-failed >> "$failed_start_events"
    return 1
  }
  bootout_if_loaded() {
    if [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )); then
      stack_loaded=0
      print -r -- unloaded >> "$failed_start_events"
    fi
  }
  start_launchd_service
) >/dev/null 2>&1; then
  print -ru2 -- "start accepted an unhealthy loaded service"
  exit 1
fi
[[ "$(paste -sd, - < "$failed_start_events")" == "load-failed,unloaded" ]] || {
  print -ru2 -- "start left its unhealthy loaded service behind"
  exit 1
}

healthy_start_events="$tmp_dir/healthy-start-events"
healthy_start_snapshot="$tmp_dir/healthy-start-snapshot.plist"
if (
  source "$service_lib"
  typeset -g stack_loaded=1
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )) }
  snapshot_service_manifest() { print -r -- "$healthy_start_snapshot" }
  wait_for_manifest_stack_health() { [[ "$1" == "$healthy_start_snapshot" ]] }
  load_launchd_service() {
    print -r -- restart-failed >> "$healthy_start_events"
    return 1
  }
  restore_loaded_stack_health() {
    [[ "$1" == "$healthy_start_snapshot" ]] || return 1
    print -r -- restored >> "$healthy_start_events"
  }
  bootout_if_loaded() {
    print -r -- unexpectedly-unloaded >> "$healthy_start_events"
    stack_loaded=0
  }
  start_launchd_service
) >/dev/null 2>&1; then
  print -ru2 -- "failed restart of a healthy service unexpectedly reported success"
  exit 1
fi
[[ "$(paste -sd, - < "$healthy_start_events")" == "restart-failed,restored" ]] || {
  print -ru2 -- "failed restart did not restore the actually loaded healthy manifest"
  exit 1
}

unhealthy_restart_events="$tmp_dir/unhealthy-restart-events"
unhealthy_start_snapshot="$tmp_dir/unhealthy-start-snapshot.plist"
if (
  source "$service_lib"
  typeset -g stack_loaded=1
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )) }
  snapshot_service_manifest() { print -r -- "$unhealthy_start_snapshot" }
  wait_for_manifest_stack_health() {
    [[ "$1" == "$unhealthy_start_snapshot" ]] || return 0
    return 1
  }
  load_launchd_service() {
    print -r -- restart-failed >> "$unhealthy_restart_events"
    return 1
  }
  bootout_if_loaded() {
    print -r -- unloaded >> "$unhealthy_restart_events"
    stack_loaded=0
  }
  start_launchd_service
) >/dev/null 2>&1; then
  print -ru2 -- "failed unhealthy restart unexpectedly reported success"
  exit 1
fi
[[ "$(paste -sd, - < "$unhealthy_restart_events")" == "restart-failed,unloaded" ]] || {
  print -ru2 -- "failed restart preserved a service that was no longer healthy"
  exit 1
}

dual_loaded_mutation="$tmp_dir/dual-loaded-mutated"
dual_loaded_output="$tmp_dir/dual-loaded.out"
if (
  source "$service_lib"
  preflight_launchd_service() { touch "$dual_loaded_mutation" }
  is_loaded() { return 0 }
  hindsight_stack_wait_all() { touch "$dual_loaded_mutation" }
  snapshot_service_manifest() { touch "$dual_loaded_mutation" }
  validate_legacy_rollback_manifest() { touch "$dual_loaded_mutation" }
  bootout_if_loaded() { touch "$dual_loaded_mutation" }
  load_launchd_service() { touch "$dual_loaded_mutation" }
  install_service
) >"$dual_loaded_output" 2>&1; then
  print -ru2 -- "install accepted simultaneously loaded legacy and stack services"
  exit 1
fi
[[ ! -e "$dual_loaded_mutation" ]] || {
  print -ru2 -- "install mutated or snapshotted state before rejecting dual-loaded services"
  exit 1
}
rg -F -q 'refusing install while both' "$dual_loaded_output" || {
  print -ru2 -- "install did not explain the dual-loaded service conflict"
  exit 1
}

legacy_restore_marker="$tmp_dir/legacy-restored"
if (
  source "$service_lib"
  typeset -g legacy_loaded=1
  typeset -g stack_loaded=0
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  validate_legacy_rollback_manifest() { return 0 }
  manifest_program_argument() { print -r -- /usr/bin/true }
  is_loaded() {
    [[ "$1" == "$LEGACY_LABEL" ]] && (( legacy_loaded )) && return 0
    [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )) && return 0
    return 1
  }
  bootout_if_loaded() {
    [[ "$1" == "$LEGACY_LABEL" ]] && legacy_loaded=0
    [[ "$1" == "$STACK_LABEL" ]] && stack_loaded=0
    return 0
  }
  load_launchd_service() { return 1 }
  bootstrap_manifest() {
    [[ "$1" == "$LEGACY_PLIST" ]] || return 1
    legacy_loaded=1
    print -r -- "$1" > "$legacy_restore_marker"
  }
  retire_legacy_plist() {
    print -ru2 -- "legacy manifest retired after replacement failure"
    return 1
  }
  install_service
) >/dev/null 2>&1; then
  print -ru2 -- "failed replacement unexpectedly reported install success"
  exit 1
fi
[[ "$(<"$legacy_restore_marker")" == "$HINDSIGHT_EMBED_LEGACY_MANIFEST" ]] || {
  print -ru2 -- "failed replacement did not reload the prior legacy manifest"
  exit 1
}

unsafe_legacy_mutation="$tmp_dir/unsafe-legacy-mutated"
if (
  source "$service_lib"
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$LEGACY_LABEL" ]] }
  validate_legacy_rollback_manifest() { die "unsafe legacy rollback" }
  bootout_if_loaded() { touch "$unsafe_legacy_mutation" }
  load_launchd_service() { touch "$unsafe_legacy_mutation" }
  install_service
) >/dev/null 2>&1; then
  print -ru2 -- "install accepted an unsafe legacy rollback manifest"
  exit 1
fi
[[ ! -e "$unsafe_legacy_mutation" ]] || {
  print -ru2 -- "install mutated launchd before validating legacy rollback"
  exit 1
}

touch "$HINDSIGHT_EMBED_LEGACY_MANIFEST"
if (
  source "$service_lib"
  validate_legacy_rollback_manifest() { die "unsafe unloaded legacy manifest" }
  retire_legacy_plist
) >/dev/null 2>&1; then
  print -ru2 -- "legacy retirement accepted an unsafe unloaded manifest"
  exit 1
fi
[[ -e "$HINDSIGHT_EMBED_LEGACY_MANIFEST" ]] || {
  print -ru2 -- "legacy retirement moved an unloaded manifest before validation"
  exit 1
}
/bin/rm -f "$HINDSIGHT_EMBED_LEGACY_MANIFEST"

missing_stack_rollback_mutation="$tmp_dir/missing-stack-rollback-mutated"
if (
  export HINDSIGHT_EMBED_STATE_DIR="$tmp_dir/no-prior-stack-rollback"
  unset HINDSIGHT_EMBED_SERVICE_ROLLBACK_MANIFEST
  source "$service_lib"
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] }
  wait_for_manifest_stack_health() { return 0 }
  bootout_if_loaded() { touch "$missing_stack_rollback_mutation" }
  load_launchd_service() { touch "$missing_stack_rollback_mutation" }
  install_service
) >/dev/null 2>&1; then
  print -ru2 -- "loaded install accepted a missing prior service definition"
  exit 1
fi
[[ ! -e "$missing_stack_rollback_mutation" ]] || {
  print -ru2 -- "loaded install mutated launchd without a prior service definition"
  exit 1
}

preserved_stack_marker="$tmp_dir/preserved-stack-unloaded"
restored_stack_marker="$tmp_dir/preserved-stack-restored"
preserved_stack_output="$tmp_dir/preserved-stack.out"
preserved_stack_snapshot="$tmp_dir/preserved-stack.plist"
print -r -- snapshot > "$preserved_stack_snapshot"
if (
  source "$service_lib"
  typeset -g stack_loaded=1
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() {
    [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded ))
  }
  bootout_if_loaded() {
    if [[ "$1" == "$STACK_LABEL" ]]; then
      stack_loaded=0
      touch "$preserved_stack_marker"
    fi
  }
  bootstrap_manifest() {
    [[ "$1" == "$preserved_stack_snapshot" ]] || return 1
    stack_loaded=1
  }
  snapshot_service_manifest() { print -r -- "$preserved_stack_snapshot" }
  wait_for_manifest_stack_health() { [[ "$1" == "$preserved_stack_snapshot" ]] }
  restore_loaded_stack_health() {
    [[ "$1" == "$preserved_stack_snapshot" ]] || return 1
    touch "$restored_stack_marker"
  }
  load_launchd_service() {
    touch "$preserved_stack_marker"
    stack_loaded=0
    return 1
  }
  install_service
) >"$preserved_stack_output" 2>&1; then
  print -ru2 -- "unhealthy replacement unexpectedly reported install success"
  exit 1
fi
[[ -e "$preserved_stack_marker" ]] || {
  print -ru2 -- "install did not reload the previously loaded replacement definition"
  exit 1
}
[[ -e "$restored_stack_marker" ]] || {
  print -ru2 -- "install did not actively restore a previously healthy stack"
  exit 1
}
rg -F -q 'restored previously healthy stack' "$preserved_stack_output" || {
  print -ru2 -- "install did not report successful healthy-stack rollback"
  exit 1
}

rollback_environment_marker="$tmp_dir/rollback-environment-marker"
rollback_install_environment_marker="$tmp_dir/rollback-install-environment-marker"
rollback_environment_stack="$tmp_dir/rollback-environment-stack.zsh"
rollback_environment_supervisor="$tmp_dir/previous-supervisor"
print -r -- '#!/usr/bin/env zsh' >"$rollback_environment_supervisor"
print -r -- 'exit 0' >>"$rollback_environment_supervisor"
chmod 700 "$rollback_environment_supervisor"
cat > "$rollback_environment_stack" <<'ZSH'
hindsight_stack_load_config() { return 0 }
hindsight_stack_require_tools() { return 0 }
hindsight_stack_require_runtime_helpers() { return 0 }
hindsight_stack_wait_all() {
  print -r -- "$HINDSIGHT_TEST_ROLLBACK_VALUE" > "$HINDSIGHT_TEST_ROLLBACK_MARKER"
  print -r -- "${HINDSIGHT_TEST_INSTALL_ONLY:-absent}" > "$HINDSIGHT_TEST_INSTALL_MARKER"
}
ZSH
chmod 600 "$rollback_environment_stack"
rollback_environment_plist="$tmp_dir/rollback-environment.plist"
ROLLBACK_STACK="$rollback_environment_stack" \
ROLLBACK_SUPERVISOR="$rollback_environment_supervisor" \
ROLLBACK_MARKER="$rollback_environment_marker" \
ROLLBACK_INSTALL_MARKER="$rollback_install_environment_marker" \
/usr/bin/python3 - "$rollback_environment_plist" <<'PY'
import os
import plistlib
import sys

with open(sys.argv[1], "wb") as handle:
    environment = {key: value for key, value in os.environ.items() if key.startswith("HINDSIGHT_")}
    environment.update({
        "HINDSIGHT_EMBED_STACK_LIB": os.environ["ROLLBACK_STACK"],
        "HINDSIGHT_TEST_ROLLBACK_MARKER": os.environ["ROLLBACK_MARKER"],
        "HINDSIGHT_TEST_ROLLBACK_VALUE": "prior-definition",
        "HINDSIGHT_TEST_INSTALL_MARKER": os.environ["ROLLBACK_INSTALL_MARKER"],
    })
    plistlib.dump(
        {
            "Label": os.environ["HINDSIGHT_EMBED_STACK_LABEL"],
            "ProgramArguments": [os.environ["ROLLBACK_SUPERVISOR"]],
            "EnvironmentVariables": environment,
        },
        handle,
    )
PY
(
  source "$service_lib"
  mkdir -p "$STATE_DIR/rollback"
  bootout_if_loaded() { return 0 }
  bootstrap_manifest() {
    [[ "$1" != "$rollback_environment_plist" ]] &&
      /usr/bin/cmp -s "$1" "$rollback_environment_plist"
  }
  is_loaded() { return 0 }
  HINDSIGHT_TEST_INSTALL_ONLY=current-install \
    restore_loaded_stack_health "$rollback_environment_plist"
)
[[ "$(<"$rollback_environment_marker")" == prior-definition ]] || {
  print -ru2 -- "rollback health check used the replacement environment"
  exit 1
}
[[ "$(<"$rollback_install_environment_marker")" == absent ]] || {
  print -ru2 -- "rollback health check inherited replacement environment"
  exit 1
}

rollback_failure_stack="$tmp_dir/rollback-failure-stack.zsh"
cat >"$rollback_failure_stack" <<'ZSH'
hindsight_stack_load_config() { return 0 }
hindsight_stack_require_tools() { return 0 }
hindsight_stack_require_runtime_helpers() { return 0 }
hindsight_stack_wait_all() { return 1 }
ZSH
chmod 600 "$rollback_failure_stack"
rollback_failure_plist="$tmp_dir/rollback-failure.plist"
ROLLBACK_STACK="$rollback_failure_stack" /usr/bin/python3 - "$rollback_failure_plist" <<'PY'
import os
import plistlib
import sys

with open(sys.argv[1], "wb") as handle:
    environment = {key: value for key, value in os.environ.items() if key.startswith("HINDSIGHT_")}
    environment["HINDSIGHT_EMBED_STACK_LIB"] = os.environ["ROLLBACK_STACK"]
    plistlib.dump(
        {
            "Label": os.environ["HINDSIGHT_EMBED_STACK_LABEL"],
            "ProgramArguments": [os.environ["HINDSIGHT_EMBED_SUPERVISOR"]],
            "EnvironmentVariables": environment,
        },
        handle,
    )
PY
rollback_failure_events="$tmp_dir/rollback-failure-events"
if (
  source "$service_lib"
  mkdir -p "$STATE_DIR/rollback"
  bootout_if_loaded() { print -r -- bootout >>"$rollback_failure_events" }
  bootstrap_manifest() { return 0 }
  is_loaded() { return 0 }
  restore_loaded_stack_health "$rollback_failure_plist"
) >/dev/null 2>&1; then
  print -ru2 -- "rollback restore accepted an unhealthy bootstrapped job"
  exit 1
fi
[[ "$(paste -sd, - <"$rollback_failure_events")" == "bootout,bootout" ]] || {
  print -ru2 -- "rollback restore did not unload the failed bootstrapped job"
  exit 1
}

unhealthy_replacement_events="$tmp_dir/unhealthy-replacement-events"
unhealthy_replacement_snapshot="$tmp_dir/unhealthy-replacement.plist"
print -r -- snapshot > "$unhealthy_replacement_snapshot"
if (
  source "$service_lib"
  typeset -g stack_loaded=1
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )) }
  hindsight_stack_wait_all() { return 1 }
  snapshot_service_manifest() {
    print -r -- "$unhealthy_replacement_snapshot"
  }
  wait_for_manifest_stack_health() { return 1 }
  load_launchd_service() {
    print -r -- replacement-failed >> "$unhealthy_replacement_events"
    return 1
  }
  bootout_if_loaded() {
    if [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )); then
      print -r -- replacement-unloaded >> "$unhealthy_replacement_events"
      stack_loaded=0
    fi
  }
  install_service
) >/dev/null 2>&1; then
  print -ru2 -- "unhealthy failed replacement unexpectedly reported success"
  exit 1
fi
[[ "$(paste -sd, - < "$unhealthy_replacement_events")" == \
  "replacement-failed,replacement-unloaded" ]] || {
  print -ru2 -- "install left a failed unhealthy replacement loaded"
  exit 1
}

reload_events="$tmp_dir/reload-events"
reload_snapshot="$tmp_dir/reload-snapshot.plist"
print -r -- snapshot > "$reload_snapshot"
(
  source "$service_lib"
  typeset -g stack_loaded=1
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() {
    [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded ))
  }
  hindsight_stack_wait_all() { return 0 }
  wait_for_manifest_stack_health() { return 0 }
  snapshot_service_manifest() { print -r -- "$reload_snapshot" }
  persist_service_manifest_snapshot() { return 0 }
  bootout_if_loaded() {
    [[ "$1" == "$STACK_LABEL" ]] || return 0
    print -r -- bootout >> "$reload_events"
    stack_loaded=0
  }
  bootstrap_manifest() {
    [[ "$1" == "$PLIST" ]] || return 1
    print -r -- "bootstrap:${1}" >> "$reload_events"
    stack_loaded=1
  }
  retire_legacy_plist() { return 0 }
  install_service
)
[[ "$(paste -sd, - < "$reload_events")" == "bootout,bootstrap:${HINDSIGHT_EMBED_SERVICE_MANIFEST}" ]] || {
  print -ru2 -- "install did not reload the loaded launchd definition via bootout/bootstrap"
  exit 1
}

persist_failure_events="$tmp_dir/persist-failure-events"
persist_failure_snapshot="$tmp_dir/persist-failure-snapshot.plist"
print -r -- snapshot > "$persist_failure_snapshot"
if (
  source "$service_lib"
  typeset -g stack_loaded=1
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )) }
  hindsight_stack_wait_all() { return 0 }
  snapshot_service_manifest() { print -r -- "$persist_failure_snapshot" }
  wait_for_manifest_stack_health() { return 0 }
  load_launchd_service() { stack_loaded=1; return 0 }
  persist_service_manifest_snapshot() {
    print -r -- persist >> "$persist_failure_events"
    return 1
  }
  restore_loaded_stack_health() {
    [[ -f "$1" ]] || return 1
    print -r -- "restore:$1" >> "$persist_failure_events"
    stack_loaded=1
  }
  install_service
) >/dev/null 2>&1; then
  print -ru2 -- "install accepted a rollback-snapshot persistence failure"
  exit 1
fi
[[ "$(paste -sd, - < "$persist_failure_events")" == "persist,restore:${persist_failure_snapshot}" ]] || {
  print -ru2 -- "install removed the rollback temporary before recovering from persistence failure"
  exit 1
}

unload_failure_events="$tmp_dir/unload-persist-failure-events"
if (
  source "$service_lib"
  typeset -g stack_loaded=0
  preflight_launchd_service() { return 0 }
  stage_validated_manifest() { print -r -- "$1" }
  is_loaded() { [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )) }
  load_launchd_service() { stack_loaded=1; return 0 }
  persist_service_manifest_snapshot() { return 1 }
  bootout_if_loaded() {
    if [[ "$1" == "$STACK_LABEL" ]] && (( stack_loaded )); then
      print -r -- unload >> "$unload_failure_events"
      stack_loaded=0
    fi
  }
  install_service
) >/dev/null 2>&1; then
  print -ru2 -- "install accepted an unpersisted first replacement"
  exit 1
fi
[[ "$(<"$unload_failure_events")" == unload ]] || {
  print -ru2 -- "install left an unpersisted first replacement loaded"
  exit 1
}

unsafe_stop_mutation="$tmp_dir/unsafe-stop-mutated"
if (
  source "$service_lib"
  load_stack_lib() { return 0 }
  hindsight_stack_load_config() { return 1 }
  bootout_if_loaded() { touch "$unsafe_stop_mutation" }
  stop_service
) >/dev/null 2>&1; then
  print -ru2 -- "stop accepted an invalid stack configuration"
  exit 1
fi
[[ -e "$unsafe_stop_mutation" ]] || {
  print -ru2 -- "stop did not unload launchd before loading stack configuration"
  exit 1
}

unsafe_stop_tools_mutation="$tmp_dir/unsafe-stop-tools-mutated"
if (
  source "$service_lib"
  load_stack_lib() { return 0 }
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_runtime_helpers() { return 0 }
  hindsight_stack_require_tools() { return 1 }
  bootout_if_loaded() { touch "$unsafe_stop_tools_mutation" }
  stop_service
) >/dev/null 2>&1; then
  print -ru2 -- "stop accepted unavailable runtime tools"
  exit 1
fi
[[ -e "$unsafe_stop_tools_mutation" ]] || {
  print -ru2 -- "stop did not unload launchd before validating runtime tools"
  exit 1
}

stop_events="$tmp_dir/stop-events"
stop_output="$tmp_dir/stop-output"
if (
  source "$service_lib"
  load_stack_lib() { return 0 }
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_runtime_helpers() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  hindsight_stack_with_lifecycle_lock() {
    local callback="$1"
    shift
    "$callback" "$@"
  }
  hindsight_stack_validate_fleet() {
    print -r -- validate-fleet >> "$stop_events"
    return 1
  }
  bootout_if_loaded() { print -r -- "bootout:$1" >> "$stop_events" }
  hindsight_stack_stop_all() {
    print -r -- cleanup >> "$stop_events"
    print -ru2 -- "residual daemon remains"
    return 1
  }
  stop_service
) >"$stop_output" 2>&1; then
  print -ru2 -- "stop reported success despite residual services"
  exit 1
fi
[[ "$(paste -sd, - < "$stop_events")" == \
  "bootout:${HINDSIGHT_EMBED_STACK_LABEL},bootout:${HINDSIGHT_EMBED_LEGACY_LABEL},cleanup" ]] || {
  print -ru2 -- "stop did not unload both launchd jobs before best-effort fleet cleanup"
  exit 1
}
rg -F -q 'residual daemon remains' "$stop_output" || {
  print -ru2 -- "stop omitted residual service reporting"
  exit 1
}

logs_root_mutation="$tmp_dir/logs-root-mutation"
if (
  source "$service_lib"
  require_current_user_identity() { die "simulated root identity" }
  prepare_service_state() { touch "$logs_root_mutation" }
  show_logs
) >/dev/null 2>&1; then
  print -ru2 -- "logs command accepted a root identity"
  exit 1
fi
[[ ! -e "$logs_root_mutation" ]] || {
  print -ru2 -- "logs command wrote filesystem state before rejecting root"
  exit 1
}

unsafe_show_tool="$tmp_dir/unsafe-show-tool"
/bin/cp /usr/bin/true "$unsafe_show_tool"
/bin/chmod 777 "$unsafe_show_tool"
unsafe_show_output="$tmp_dir/unsafe-show-output"
(
  export HINDSIGHT_EMBED_SUPERVISOR="$unsafe_show_tool"
  export HINDSIGHT_EMBED_UVX="$unsafe_show_tool"
  export HINDSIGHT_MEMORY_CLI="$unsafe_show_tool"
  source "$service_lib"
  load_stack_lib() { return 0 }
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_require_runtime_helpers() { return 1 }
  hindsight_stack_profile_exists() { return 1 }
  hindsight_stack_require_fleet_profiles() { return 1 }
  show_installed_file_checks
) >"$unsafe_show_output" 2>&1 || true
for label in supervisor uvx 'memory controller'; do
  rg -F -q "  ${label}: missing, untrusted, or not executable" "$unsafe_show_output" || {
    print -ru2 -- "status show did not trust-validate ${label}"
    exit 1
  }
done

diagnostic_status_output="$tmp_dir/diagnostic-status-output"
if (
  source "$service_lib"
  STACK_LIB="$tmp_dir/missing-stack-library.zsh"
  is_loaded() { return 1 }
  service_name() { print -r -- "$1" }
  has_disabled_override() { return 1 }
  show_status
) >"$diagnostic_status_output" 2>&1; then
  print -ru2 -- "status accepted a missing stack library"
  exit 1
fi
rg -F -q 'installed files:' "$diagnostic_status_output" || {
  print -ru2 -- "status exited instead of reporting a stack-library diagnostic"
  exit 1
}

(
  source "$service_lib"
  is_loaded() { return 1 }
  service_name() { print -r -- "$1" }
  has_disabled_override() { return 1 }
  show_installed_file_checks() { return 0 }
  hindsight_stack_status_report() { return 23 }
  typeset -g HINDSIGHT_EMBED_STACK_LIB_LOADED=1
  if show_status >/dev/null 2>&1; then
    print -ru2 -- "status ignored a stack status-report failure"
    exit 1
  fi
)

help_output="$(zsh "$repo_dir/bin/hindsight-embed-service" --help)"
print -r -- "$help_output" | rg -F -q 'status [--profile <name>]' || {
  print -ru2 -- "service help does not expose additive profile selection"
  exit 1
}

selected_profile_file="$tmp_dir/selected-profile"
(
  export HINDSIGHT_TEST_SELECTED_PROFILE_FILE="$selected_profile_file"
  source "$service_lib"
  show_status() { print -r -- "$1" > "$HINDSIGHT_TEST_SELECTED_PROFILE_FILE" }
  main status --profile second-profile
)
[[ "$(<"$selected_profile_file")" == second-profile ]] || {
  print -ru2 -- "status --profile did not select the requested enabled profile"
  exit 1
}
print -r -- "hindsight-embed-stack: PASS"
