# Shared lifecycle helpers for the local Hindsight embed stack.

if (( ! ${+HINDSIGHT_EMBED_LAST_START_EPOCH} )); then
  typeset -gA HINDSIGHT_EMBED_LAST_START_EPOCH
fi

hindsight_stack_validate_seconds() {
  emulate -L zsh
  local value="$1" label="$2" maximum="$3" minimum="${4:-1}"
  [[ "$value" =~ '^(0|[1-9][0-9]*)$' ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be a canonical nonnegative integer"
    return 1
  }
  (( value >= minimum && value <= maximum )) || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be from ${minimum} to ${maximum} seconds"
    return 1
  }
}

hindsight_stack_validate_port() {
  emulate -L zsh
  local value="$1" label="$2"
  [[ "$value" =~ '^[1-9][0-9]*$' ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be a canonical positive decimal port"
    return 1
  }
  (( value <= 65535 )) || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be at most 65535"
    return 1
  }
}

hindsight_stack_runtime_active() {
  local inventory="${HINDSIGHT_MEMORY_INVENTORY:-}"
  [[
    -n "$inventory" &&
    "$inventory" == /* &&
    "$inventory" != *$'\n'* &&
    "$inventory" != *$'\r'*
  ]]
}

hindsight_stack_load_config() {
  emulate -L zsh
  setopt no_unset

  local -a missing=()
  [[ -n "${HINDSIGHT_EMBED_UVX:-}" ]] || missing+=(HINDSIGHT_EMBED_UVX)
  [[ -n "${HINDSIGHT_EMBED_CONTROL_PORT:-}" ]] || missing+=(HINDSIGHT_EMBED_CONTROL_PORT)
  [[ -n "${HINDSIGHT_EMBED_CONTROL_HOSTNAME:-}" ]] || missing+=(HINDSIGHT_EMBED_CONTROL_HOSTNAME)
  [[ -n "${HINDSIGHT_EMBED_PRIMARY_PROFILE:-}" ]] || missing+=(HINDSIGHT_EMBED_PRIMARY_PROFILE)
  [[ -n "${HINDSIGHT_EMBED_FLEET_PROFILES:-}" ]] || missing+=(HINDSIGHT_EMBED_FLEET_PROFILES)
  [[ -n "${HINDSIGHT_EMBED_API_BASE_PORT:-}" ]] || missing+=(HINDSIGHT_EMBED_API_BASE_PORT)
  [[ -n "${HINDSIGHT_EMBED_UI_BASE_PORT:-}" ]] || missing+=(HINDSIGHT_EMBED_UI_BASE_PORT)
  [[ -n "${HINDSIGHT_EMBED_UI_HOSTNAME:-}" ]] || missing+=(HINDSIGHT_EMBED_UI_HOSTNAME)
  [[ -n "${HINDSIGHT_EMBED_PYTHON:-}" ]] || missing+=(HINDSIGHT_EMBED_PYTHON)
  [[ -n "${HINDSIGHT_EMBED_STOP_HELPER:-}" ]] || missing+=(HINDSIGHT_EMBED_STOP_HELPER)
  [[ -n "${HINDSIGHT_MEMORY_CLI:-}" ]] || missing+=(HINDSIGHT_MEMORY_CLI)
  [[ -n "${HINDSIGHT_MEMORY_STATE_DIR:-}" ]] || missing+=(HINDSIGHT_MEMORY_STATE_DIR)
  [[ -n "${HINDSIGHT_MEMORY_BROKER_SOCKET:-}" ]] || missing+=(HINDSIGHT_MEMORY_BROKER_SOCKET)
  [[ -n "${HINDSIGHT_EMBED_STATE_DIR:-}" ]] || missing+=(HINDSIGHT_EMBED_STATE_DIR)
  [[ -n "${HINDSIGHT_EMBED_AUTOSTART_DAEMON:-}" ]] || missing+=(HINDSIGHT_EMBED_AUTOSTART_DAEMON)
  [[ -n "${HINDSIGHT_EMBED_AUTOSTART_UI:-}" ]] || missing+=(HINDSIGHT_EMBED_AUTOSTART_UI)
  if (( ${#missing} )); then
    print -ru2 -- "hindsight-embed-stack: missing required consumer settings: ${missing[*]}"
    return 1
  fi

  [[ "$HINDSIGHT_MEMORY_STATE_DIR" == /* ]] || {
    print -ru2 -- "hindsight-embed-stack: HINDSIGHT_MEMORY_STATE_DIR must be absolute"
    return 1
  }
  [[ "$HINDSIGHT_MEMORY_BROKER_SOCKET" == /* ]] || {
    print -ru2 -- "hindsight-embed-stack: HINDSIGHT_MEMORY_BROKER_SOCKET must be absolute"
    return 1
  }
  if [[ -n "${HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE:-}" ]]; then
    [[
      "$HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE" == /* &&
      "$HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE" != *$'\n'* &&
      "$HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE" != *$'\r'*
    ]] || {
      print -ru2 -- "hindsight-embed-stack: HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE must be an absolute single-line path"
      return 1
    }
  fi
  local runtime_binding_count=0
  [[ -n "${HINDSIGHT_MEMORY_INVENTORY:-}" ]] &&
    (( runtime_binding_count += 1 ))
  [[ -n "${HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV:-}" ]] &&
    (( runtime_binding_count += 1 ))
  [[ -n "${HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV:-}" ]] &&
    (( runtime_binding_count += 1 ))
  [[ -n "${HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV:-}" ]] &&
    (( runtime_binding_count += 1 ))
  if (( runtime_binding_count != 0 && runtime_binding_count != 4 )); then
    print -ru2 -- "hindsight-embed-stack: runtime inventory and resolver bindings must be all set or all unset"
    return 1
  fi
  if (( runtime_binding_count == 4 )); then
    hindsight_stack_runtime_active || {
      print -ru2 -- "hindsight-embed-stack: HINDSIGHT_MEMORY_INVENTORY must be an absolute single-line path"
      return 1
    }
    local resolver_name
    for resolver_name in \
      HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV \
      HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV \
      HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV; do
      [[ "${(P)resolver_name:-}" =~ '^[A-Z_][A-Z0-9_]{0,127}$' ]] || {
        print -ru2 -- "hindsight-embed-stack: ${resolver_name} must name a valid environment variable"
        return 1
      }
      case "${(P)resolver_name}" in
        HINDSIGHT_MEMORY_INVENTORY|\
        HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE|\
        HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV|\
        HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV|\
        HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV|\
        HINDSIGHT_API_TENANT_API_KEY|\
        HINDSIGHT_CP_DATAPLANE_API_KEY|\
        HINDSIGHT_CP_ACCESS_KEY)
          print -ru2 -- "hindsight-embed-stack: ${resolver_name} must not target a reserved runtime binding"
          return 1
          ;;
      esac
    done
    if [[ "$HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV" == \
        "$HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV" || \
      "$HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV" == \
        "$HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV" || \
      "$HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV" == \
        "$HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV" ]]; then
      print -ru2 -- "hindsight-embed-stack: runtime credential resolver bindings must be distinct"
      return 1
    fi
  fi

  hindsight_stack_validate_port "$HINDSIGHT_EMBED_CONTROL_PORT" HINDSIGHT_EMBED_CONTROL_PORT || return 1
  hindsight_stack_validate_port "$HINDSIGHT_EMBED_API_BASE_PORT" HINDSIGHT_EMBED_API_BASE_PORT || return 1
  hindsight_stack_validate_port "$HINDSIGHT_EMBED_UI_BASE_PORT" HINDSIGHT_EMBED_UI_BASE_PORT || return 1
  local autostart_name autostart_value
  for autostart_name in HINDSIGHT_EMBED_AUTOSTART_DAEMON HINDSIGHT_EMBED_AUTOSTART_UI; do
    autostart_value="${(P)autostart_name}"
    [[ "$autostart_value" == true || "$autostart_value" == false ]] || {
      print -ru2 -- "hindsight-embed-stack: ${autostart_name} must be exactly true or false"
      return 1
    }
  done
  if [[ "$HINDSIGHT_EMBED_AUTOSTART_DAEMON" == false &&
    "$HINDSIGHT_EMBED_AUTOSTART_UI" == true ]]; then
    print -ru2 -- "hindsight-embed-stack: UI autostart requires daemon autostart"
    return 1
  fi

  typeset -g HINDSIGHT_EMBED_PROFILE="${HINDSIGHT_EMBED_PROFILE:-$HINDSIGHT_EMBED_PRIMARY_PROFILE}"
  typeset -g HINDSIGHT_EMBED_API_PORT="${HINDSIGHT_EMBED_API_PORT:-$HINDSIGHT_EMBED_API_BASE_PORT}"
  typeset -g HINDSIGHT_EMBED_UI_PORT="${HINDSIGHT_EMBED_UI_PORT:-$HINDSIGHT_EMBED_UI_BASE_PORT}"
  typeset -g HINDSIGHT_EMBED_PROFILE_SLOT_DIR="${HINDSIGHT_EMBED_PROFILE_SLOT_DIR:-$HINDSIGHT_EMBED_STATE_DIR/profile-slots}"
  typeset -g HINDSIGHT_EMBED_DESIRED_STATE_DIR="${HINDSIGHT_EMBED_DESIRED_STATE_DIR:-$HINDSIGHT_EMBED_STATE_DIR/desired}"
  typeset -g HINDSIGHT_EMBED_CONTROL_WAIT_SECONDS="${HINDSIGHT_EMBED_CONTROL_WAIT_SECONDS:-30}"
  typeset -g HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS="${HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS:-300}"
  typeset -g HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS="${HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS:-120}"
  typeset -g HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS="${HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS:-30}"
  typeset -g HINDSIGHT_EMBED_UI_WAIT_SECONDS="${HINDSIGHT_EMBED_UI_WAIT_SECONDS:-60}"
  typeset -g HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS="${HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS:-30}"
  typeset -g HINDSIGHT_EMBED_STOP_WAIT_SECONDS="${HINDSIGHT_EMBED_STOP_WAIT_SECONDS:-30}"
  typeset -g HINDSIGHT_EMBED_START_COOLDOWN_SECONDS="${HINDSIGHT_EMBED_START_COOLDOWN_SECONDS:-20}"
  typeset -g HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS="${HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS:-300}"
  local timeout_name timeout_value timeout_max
  for timeout_name timeout_max in \
    HINDSIGHT_EMBED_CONTROL_WAIT_SECONDS 3600 \
    HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS 3600 \
    HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS 3600 \
    HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS 300 \
    HINDSIGHT_EMBED_UI_WAIT_SECONDS 3600 \
    HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS 3600 \
    HINDSIGHT_EMBED_STOP_WAIT_SECONDS 3600 \
    HINDSIGHT_EMBED_START_COOLDOWN_SECONDS 3600 \
    HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS 300; do
    timeout_value="${(P)timeout_name}"
    if [[ "$timeout_name" == HINDSIGHT_EMBED_START_COOLDOWN_SECONDS ]]; then
      hindsight_stack_validate_seconds "$timeout_value" "$timeout_name" "$timeout_max" 0 || return 1
    else
      hindsight_stack_validate_seconds "$timeout_value" "$timeout_name" "$timeout_max" || return 1
    fi
  done
}

hindsight_stack_prepare_private_state_directory() {
  emulate -L zsh
  local path="$1" label="$2"
  [[ "$path" == /* ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be absolute: ${path}"
    return 1
  }

  local existing="$path"
  while [[ ! -e "$existing" && "$existing" != / ]]; do
    existing="${existing:h}"
  done
  [[ -d "$existing" && ! -L "$existing" ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} has an unsafe existing ancestor: ${existing}"
    return 1
  }
  local owner mode
  owner="$(/usr/bin/stat -f '%u' "$existing")" || return 1
  mode="$(/usr/bin/stat -f '%Lp' "$existing")" || return 1
  (( owner == EUID || owner == 0 )) || return 1
  if (( (8#$mode & 8#0022) != 0 && !(owner == 0 && (8#$mode & 8#01000) != 0) )); then
    print -ru2 -- "hindsight-embed-stack: ${label} ancestor is writable by another user: ${existing}"
    return 1
  fi

  ( umask 077; /bin/mkdir -p "$path" ) || return 1
  local ancestor="$path"
  while true; do
    if [[ -L "$ancestor" ]]; then
      owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
      mode="$(/usr/bin/stat -f '%Lp' "$ancestor")" || return 1
      (( owner == 0 && (8#$mode & 8#0022) == 0 )) || {
        print -ru2 -- "hindsight-embed-stack: ${label} contains an untrusted symlink: ${ancestor}"
        return 1
      }
      [[ "$ancestor" == / ]] && break
      ancestor="${ancestor:h}"
      continue
    fi
    [[ -d "$ancestor" ]] || {
      print -ru2 -- "hindsight-embed-stack: ${label} contains a symlink or non-directory: ${ancestor}"
      return 1
    }
    owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
    mode="$(/usr/bin/stat -f '%Lp' "$ancestor")" || return 1
    (( owner == EUID || owner == 0 )) || return 1
    if (( (8#$mode & 8#0022) != 0 && !(owner == 0 && (8#$mode & 8#01000) != 0) )); then
      print -ru2 -- "hindsight-embed-stack: ${label} ancestry is writable by another user: ${ancestor}"
      return 1
    fi
    [[ "$ancestor" == / ]] && break
    ancestor="${ancestor:h}"
  done
  owner="$(/usr/bin/stat -f '%u' "$path")" || return 1
  (( owner == EUID )) || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be current-user-owned: ${path}"
    return 1
  }
  /bin/chmod 700 "$path"
}

hindsight_stack_desired_state_path() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local component="$1" profile="${2:-$HINDSIGHT_EMBED_PROFILE}"
  hindsight_stack_valid_profile_name "$profile" || return 1
  case "$component" in
    daemon|ui) ;;
    *)
      print -ru2 -- "hindsight-embed-stack: invalid desired-state component: ${component}"
      return 1
      ;;
  esac
  print -r -- "$HINDSIGHT_EMBED_DESIRED_STATE_DIR/profiles/${profile}/${component}"
}

hindsight_stack_prepare_desired_state_dir() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_prepare_private_state_directory \
    "$HINDSIGHT_EMBED_DESIRED_STATE_DIR" "component desired-state directory" || return 1
  hindsight_stack_prepare_private_state_directory \
    "$HINDSIGHT_EMBED_DESIRED_STATE_DIR/profiles" "component desired-state profiles directory"
}

hindsight_stack_write_private_state() {
  emulate -L zsh
  local path="$1" value="$2" parent="${1:h}" temporary
  hindsight_stack_prepare_private_state_directory "$parent" "component desired-state profile directory" || return 1
  if [[ -L "$path" ]] || { [[ -e "$path" ]] && [[ ! -f "$path" ]]; }; then
    print -ru2 -- "hindsight-embed-stack: refusing unsafe component desired-state file: ${path}"
    return 1
  fi
  temporary="$(/usr/bin/mktemp "${path}.XXXXXX")" || return 1
  if ! ( umask 077; print -r -- "$value" >| "$temporary" ) ||
    ! /bin/chmod 600 "$temporary" || ! /bin/mv -f "$temporary" "$path"; then
    /bin/rm -f "$temporary"
    return 1
  fi
  [[ -f "$path" && ! -L "$path" ]] || return 1
  [[ "$(/usr/bin/stat -f '%u:%Lp' "$path")" == "${EUID}:600" ]]
}

hindsight_stack_set_desired_state() {
  emulate -L zsh
  local component="$1" desired="$2" profile="${3:-$HINDSIGHT_EMBED_PROFILE}"
  case "$desired" in
    running|stopped) ;;
    *)
      print -ru2 -- "hindsight-embed-stack: invalid desired state: ${desired}"
      return 1
      ;;
  esac
  hindsight_stack_prepare_desired_state_dir || return 1
  local path dependency_path
  if [[ "$component" == ui && "$desired" == running ]]; then
    dependency_path="$(hindsight_stack_desired_state_path daemon "$profile")" || return 1
    hindsight_stack_write_private_state "$dependency_path" running || return 1
  elif [[ "$component" == daemon && "$desired" == stopped ]]; then
    dependency_path="$(hindsight_stack_desired_state_path ui "$profile")" || return 1
    hindsight_stack_write_private_state "$dependency_path" stopped || return 1
  fi
  path="$(hindsight_stack_desired_state_path "$component" "$profile")" || return 1
  hindsight_stack_write_private_state "$path" "$desired"
}

hindsight_stack_desired_state() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local component="$1" profile="${2:-$HINDSIGHT_EMBED_PROFILE}" path desired owner mode
  path="$(hindsight_stack_desired_state_path "$component" "$profile")" || return 1
  if [[ ! -e "$path" ]]; then
    case "$component" in
      daemon)
        hindsight_stack_enabled "$HINDSIGHT_EMBED_AUTOSTART_DAEMON" && print -r -- running || print -r -- stopped
        ;;
      ui)
        hindsight_stack_enabled "$HINDSIGHT_EMBED_AUTOSTART_UI" && print -r -- running || print -r -- stopped
        ;;
    esac
    return 0
  fi
  [[ -f "$path" && ! -L "$path" ]] || return 1
  owner="$(/usr/bin/stat -f '%u' "$path")" || return 1
  mode="$(/usr/bin/stat -f '%Lp' "$path")" || return 1
  (( owner == EUID && (8#$mode & 8#0077) == 0 )) || return 1
  desired="$(<"$path")"
  case "$desired" in
    running|stopped) print -r -- "$desired" ;;
    *) return 1 ;;
  esac
}

hindsight_stack_reset_profile_desired_state() {
  emulate -L zsh
  local profile="$1" daemon_state=stopped ui_state=stopped
  hindsight_stack_enabled "$HINDSIGHT_EMBED_AUTOSTART_DAEMON" && daemon_state=running
  hindsight_stack_enabled "$HINDSIGHT_EMBED_AUTOSTART_UI" && ui_state=running
  hindsight_stack_set_desired_state daemon "$daemon_state" "$profile" || return 1
  hindsight_stack_set_desired_state ui "$ui_state" "$profile"
}

hindsight_stack_reset_desired_state() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_for_each_profile hindsight_stack_reset_profile_desired_state ""
}

hindsight_stack_startup_id() {
  emulate -L zsh
  local audit_session boot_time
  audit_session="$(
    /bin/launchctl print "gui/${EUID}" 2>/dev/null |
      /usr/bin/awk '/^[[:space:]]*asid = / && !found { print $3; found=1 } END { if (!found) exit 1 }'
  )" || audit_session=""
  if [[ -n "$audit_session" ]]; then
    print -r -- "asid:${audit_session}"
    return 0
  fi
  boot_time="$(/usr/sbin/sysctl -n kern.boottime 2>/dev/null)" || return 1
  [[ -n "$boot_time" ]] || return 1
  print -r -- "boot:${boot_time}"
}

hindsight_stack_initialize_desired_state() {
  emulate -L zsh
  hindsight_stack_prepare_desired_state_dir || return 1
  local startup_id startup_file previous=""
  startup_id="$(hindsight_stack_startup_id)" || return 1
  startup_file="$HINDSIGHT_EMBED_DESIRED_STATE_DIR/startup-id"
  if [[ -e "$startup_file" ]]; then
    [[ -f "$startup_file" && ! -L "$startup_file" ]] || return 1
    [[ "$(/usr/bin/stat -f '%u:%Lp' "$startup_file")" == "${EUID}:600" ]] || return 1
    previous="$(<"$startup_file")"
  fi
  if [[ "$previous" != "$startup_id" ]]; then
    hindsight_stack_reset_desired_state || return 1
    hindsight_stack_write_private_state "$startup_file" "$startup_id" || return 1
  fi
}

hindsight_stack_prepare_lifecycle_lock() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local state_dir="$HINDSIGHT_EMBED_STATE_DIR"
  [[ "$state_dir" == /* && ! -L "$state_dir" ]] || {
    print -ru2 -- "hindsight-embed-stack: lifecycle state must be an absolute non-symlink path"
    return 1
  }
  hindsight_stack_validate_creation_ancestry "$state_dir" "lifecycle state" || return 1
  ( umask 077; /bin/mkdir -p "$state_dir" ) || return 1
  [[ -d "$state_dir" && ! -L "$state_dir" ]] || return 1
  /bin/chmod 700 "$state_dir" || return 1
  local owner mode ancestor="$state_dir"
  while [[ "$ancestor" != / ]]; do
    owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
    mode="$(/usr/bin/stat -f '%Lp' "$ancestor")" || return 1
    (( owner == EUID || owner == 0 )) || return 1
    if (( (8#$mode & 8#0022) != 0 && !(owner == 0 && (8#$mode & 8#01000) != 0) )); then
      print -ru2 -- "hindsight-embed-stack: lifecycle state ancestry is untrusted: ${ancestor}"
      return 1
    fi
    ancestor="${ancestor:h}"
  done
  local lock_file="${state_dir}/.lifecycle.lock"
  if [[ -L "$lock_file" ]] || { [[ -e "$lock_file" ]] && [[ ! -f "$lock_file" ]]; }; then
    print -ru2 -- "hindsight-embed-stack: refusing unsafe lifecycle lock: ${lock_file}"
    return 1
  fi
  ( umask 077; : >>"$lock_file" ) || return 1
  /bin/chmod 600 "$lock_file" || return 1
  [[ "$(/usr/bin/stat -f '%u:%Lp' "$lock_file")" == "${EUID}:600" ]] || {
    print -ru2 -- "hindsight-embed-stack: lifecycle lock must be private and current-user-owned"
    return 1
  }
  print -r -- "$lock_file"
}

hindsight_stack_validate_creation_ancestry() {
  emulate -L zsh
  local requested="$1" label="$2" candidate ancestor owner mode
  for candidate in "${requested:h}" "${requested:A:h}"; do
    ancestor="$candidate"
    while [[ ! -e "$ancestor" && ! -L "$ancestor" && "$ancestor" != / ]]; do
      ancestor="${ancestor:h}"
    done
    while [[ "$ancestor" != / ]]; do
      if [[ -L "$ancestor" ]]; then
        owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
        (( owner == 0 )) || {
          print -ru2 -- "hindsight-embed-stack: ${label} has an unsafe symlink ancestor: ${ancestor}"
          return 1
        }
        ancestor="${ancestor:h}"
        continue
      fi
      [[ -d "$ancestor" ]] || {
        print -ru2 -- "hindsight-embed-stack: ${label} has an unsafe existing ancestor: ${ancestor}"
        return 1
      }
      owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
      mode="$(/usr/bin/stat -f '%Lp' "$ancestor")" || return 1
      (( owner == EUID || owner == 0 )) || return 1
      if (( (8#$mode & 8#0022) != 0 && !(owner == 0 && (8#$mode & 8#01000) != 0) )); then
        print -ru2 -- "hindsight-embed-stack: ${label} ancestry is untrusted: ${ancestor}"
        return 1
      fi
      ancestor="${ancestor:h}"
    done
  done
}

hindsight_stack_with_lifecycle_lock() {
  emulate -L zsh
  unsetopt ERR_EXIT
  local callback="$1"
  shift
  (( $+functions[$callback] )) || return 2
  hindsight_stack_require_current_user || return 1
  local lock_file
  lock_file="$(hindsight_stack_prepare_lifecycle_lock)" || return 1
  zmodload zsh/system || {
    print -ru2 -- "hindsight-embed-stack: zsh/system is required for lifecycle locking"
    return 1
  }
  integer lock_descriptor
  zsystem flock -f lock_descriptor -t 30 "$lock_file" || {
    print -ru2 -- "hindsight-embed-stack: timed out acquiring lifecycle lock"
    return 1
  }
  "$callback" "$@"
  local result=$?
  zsystem flock -u "$lock_descriptor" || return 1
  return "$result"
}

hindsight_stack_run_with_credential_scope() {
  emulate -L zsh
  local scope="$1"
  shift
  if [[ "$scope" != none && "$scope" != api && \
    "$scope" != ui-proxy && "$scope" != broker && \
    "$scope" != preflight ]]; then
    print -ru2 -- "hindsight-embed-stack: invalid credential scope: ${scope}"
    return 2
  fi
  (
    local data_name="${HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV:-}"
    local mint_name="${HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV:-}"
    local ui_name="${HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV:-}"
    local data_credential=''
    local ui_credential=''
    local mapped_name
    if hindsight_stack_runtime_active; then
      if [[ "$scope" == api || "$scope" == ui-proxy ]]; then
        data_credential="${(P)data_name:-}"
        if [[ -z "$data_credential" || "$data_credential" == *$'\n'* || \
          "$data_credential" == *$'\r'* ]]; then
          print -ru2 -- "hindsight-embed-stack: data-plane credential resolver is unavailable"
          return 1
        fi
      fi
      if [[ "$scope" == ui-proxy ]]; then
        ui_credential="${(P)ui_name:-}"
        if [[ -z "$ui_credential" || "$ui_credential" == *$'\n'* || \
          "$ui_credential" == *$'\r'* ]]; then
          print -ru2 -- "hindsight-embed-stack: UI access-key resolver is unavailable"
          return 1
        fi
      fi
      if [[ "$scope" == broker ]]; then
        unset "$ui_name"
        for mapped_name in \
          HINDSIGHT_API_TENANT_API_KEY \
          HINDSIGHT_CP_DATAPLANE_API_KEY HINDSIGHT_CP_ACCESS_KEY; do
          [[ "$mapped_name" == "$data_name" || \
            "$mapped_name" == "$mint_name" ]] || \
            unset "$mapped_name"
        done
      elif [[ "$scope" == preflight ]]; then
        for mapped_name in \
          HINDSIGHT_API_TENANT_API_KEY \
          HINDSIGHT_CP_DATAPLANE_API_KEY HINDSIGHT_CP_ACCESS_KEY; do
          [[ "$mapped_name" == "$data_name" || \
            "$mapped_name" == "$mint_name" || \
            "$mapped_name" == "$ui_name" ]] || unset "$mapped_name"
        done
      else
        unset "$data_name" "$mint_name" "$ui_name"
        unset HINDSIGHT_API_TENANT_API_KEY \
          HINDSIGHT_CP_DATAPLANE_API_KEY HINDSIGHT_CP_ACCESS_KEY
        if [[ "$scope" == api ]]; then
          export HINDSIGHT_API_TENANT_API_KEY="$data_credential"
        elif [[ "$scope" == ui-proxy ]]; then
          export HINDSIGHT_CP_DATAPLANE_API_KEY="$data_credential"
          export HINDSIGHT_CP_ACCESS_KEY="$ui_credential"
        fi
      fi
    else
      unset HINDSIGHT_API_TENANT_API_KEY \
        HINDSIGHT_CP_DATAPLANE_API_KEY HINDSIGHT_CP_ACCESS_KEY
    fi
    "$@"
  )
}

hindsight_stack_preflight_runtime_credentials() {
  emulate -L zsh
  hindsight_stack_runtime_active || return 0
  hindsight_stack_validate_fleet_profile_credential_isolation || return 1
  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_EMBED_PYTHON" "Hindsight embed Python" executable allow-symlink || return 1
  hindsight_stack_run_with_credential_scope preflight \
    "$HINDSIGHT_EMBED_PYTHON" -I - \
    "$HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV" \
    "$HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV" \
    "$HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV" <<'PY'
import hmac
import os
import sys

values = []
for name in sys.argv[1:]:
    value = os.environ.get(name)
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(1)
    values.append(value.encode("utf-8"))
for index, value in enumerate(values):
    for other in values[index + 1:]:
        if hmac.compare_digest(value, other):
            raise SystemExit(1)
PY
}

hindsight_stack_run_bounded_with_credential_scope() {
  emulate -L zsh
  local scope="$1" timeout="$2"
  shift 2
  hindsight_stack_validate_seconds "$timeout" "bounded command timeout" 7200 || return 1
  (( $# )) || return 2
  hindsight_stack_run_with_credential_scope "$scope" \
    "$HINDSIGHT_EMBED_PYTHON" -I - "$timeout" "$@" <<'PY'
import os
import signal
import subprocess
import sys
import time

timeout = int(sys.argv[1])
handled_signals = {signal.SIGINT, signal.SIGTERM}
previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handled_signals)
process = None

class CommandSignal(BaseException):
    def __init__(self, signum):
        self.signum = signum

def handle_signal(signum, _frame):
    raise CommandSignal(signum)

def process_group_exists():
    if process is None:
        return False
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return process.poll() is None
    return True

def terminate_process_group():
    if process is None:
        return
    termination_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handled_signals)
    termination_handlers = {
        signum: signal.getsignal(signum) for signum in handled_signals
    }
    for signum in handled_signals:
        signal.signal(signum, signal.SIG_IGN)
    try:
        if not process_group_exists():
            process.wait()
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 2
        while process_group_exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if process_group_exists():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        kill_deadline = time.monotonic() + 2
        while process_group_exists() and time.monotonic() < kill_deadline:
            time.sleep(0.05)
        if process.poll() is None:
            try:
                process.wait(timeout=max(0, kill_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
    finally:
        # Unblock while the signals are ignored so a second signal cannot
        # interrupt cleanup or recursively re-enter this function.
        signal.pthread_sigmask(signal.SIG_SETMASK, termination_mask)
        for signum, handler in termination_handlers.items():
            signal.signal(signum, handler)

try:
    pass_fds = ()
    inherited_descriptor = os.environ.get(
        "HINDSIGHT_EMBED_MAINTENANCE_LEASE_DESCRIPTOR"
    )
    if inherited_descriptor is not None:
        if (
            not inherited_descriptor.isdecimal()
            or str(int(inherited_descriptor)) != inherited_descriptor
            or int(inherited_descriptor) < 3
        ):
            raise ValueError("invalid inherited maintenance lease descriptor")
        descriptor = int(inherited_descriptor)
        os.fstat(descriptor)
        pass_fds = (descriptor,)
    process = subprocess.Popen(
        sys.argv[2:],
        start_new_session=True,
        pass_fds=pass_fds,
        preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask),
    )
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    raise SystemExit(process.wait(timeout=timeout))
except subprocess.TimeoutExpired:
    terminate_process_group()
    raise SystemExit(124)
except CommandSignal as error:
    terminate_process_group()
    signal.signal(error.signum, signal.SIG_DFL)
    os.kill(os.getpid(), error.signum)
    raise SystemExit(128 + error.signum)
except BaseException:
    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    terminate_process_group()
    raise
PY
}

hindsight_stack_run_bounded() {
  hindsight_stack_run_bounded_with_credential_scope none "$@"
}

hindsight_stack_run_bounded_api() {
  hindsight_stack_run_bounded_with_credential_scope api "$@"
}

hindsight_stack_run_bounded_ui_proxy() {
  hindsight_stack_run_bounded_with_credential_scope ui-proxy "$@"
}

hindsight_stack_timestamp() {
  emulate -L zsh
  /bin/date -u "+%Y-%m-%dT%H:%M:%SZ"
}

hindsight_stack_log() {
  emulate -L zsh
  print -r -- "$(hindsight_stack_timestamp) $*"
}

hindsight_stack_require_current_user() {
  emulate -L zsh
  if (( EUID == 0 || UID == 0 )); then
    print -ru2 -- "hindsight-embed-stack: refusing to run as root"
    return 1
  fi
}

hindsight_stack_require_trusted_artifact() {
  emulate -L zsh
  local path="$1"
  local label="$2"
  local access="${3:-readable}"
  local symlink_policy="${4:-reject-symlink}"

  [[ "$path" == /* ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} must use an absolute path: ${path}"
    return 1
  }
  if [[ -L "$path" ]]; then
    [[ "$symlink_policy" == allow-symlink ]] || {
      print -ru2 -- "hindsight-embed-stack: ${label} must not be a symlink: ${path}"
      return 1
    }
    local link_owner
    link_owner="$(/usr/bin/stat -f '%u' "$path")" || return 1
    (( link_owner == EUID || link_owner == 0 )) || {
      print -ru2 -- "hindsight-embed-stack: ${label} symlink is not trusted: ${path}"
      return 1
    }
  fi

  local resolved="${path:A}"
  [[ -f "$resolved" && ! -L "$resolved" ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} must resolve to a regular file: ${path}"
    return 1
  }
  if [[ "$access" == executable ]]; then
    [[ -x "$resolved" ]] || {
      print -ru2 -- "hindsight-embed-stack: ${label} is not executable: ${path}"
      return 1
    }
  else
    [[ -r "$resolved" ]] || {
      print -ru2 -- "hindsight-embed-stack: ${label} is not readable: ${path}"
      return 1
    }
  fi

  local owner mode ancestor ancestor_root
  owner="$(/usr/bin/stat -f '%u' "$resolved")" || return 1
  mode="$(/usr/bin/stat -f '%Lp' "$resolved")" || return 1
  (( owner == EUID || owner == 0 )) || {
    print -ru2 -- "hindsight-embed-stack: ${label} owner is not trusted: ${path}"
    return 1
  }
  (( (8#$mode & 8#0022) == 0 )) || {
    print -ru2 -- "hindsight-embed-stack: ${label} is group or world writable: ${path}"
    return 1
  }

  for ancestor_root in "${path:h}" "${resolved:h}"; do
    ancestor="$ancestor_root"
    while [[ "$ancestor" != / ]]; do
      owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
      mode="$(/usr/bin/stat -f '%Lp' "$ancestor")" || return 1
      (( owner == EUID || owner == 0 )) || return 1
      if (( (8#$mode & 8#0022) != 0 && !(owner == 0 && (8#$mode & 8#01000) != 0) )); then
        print -ru2 -- "hindsight-embed-stack: ${label} ancestor is group or world writable: ${ancestor}"
        return 1
      fi
      ancestor="${ancestor:h}"
    done
  done
}

hindsight_stack_require_tools() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_EMBED_UVX" uvx executable allow-symlink || return 1
  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_EMBED_PYTHON" "Hindsight embed Python" executable allow-symlink || return 1
  if ! command -v /usr/bin/curl >/dev/null 2>&1; then
    print -ru2 -- "hindsight-embed-stack: missing /usr/bin/curl"
    return 1
  fi
  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_MEMORY_CLI" "memory controller" executable allow-symlink || return 1
  hindsight_stack_require_loopback || return 1
}

hindsight_stack_require_loopback() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  case "$HINDSIGHT_EMBED_CONTROL_HOSTNAME" in
    127.0.0.1|::1) ;;
    *)
      print -ru2 -- "hindsight-embed-stack: control hostname must be a literal loopback address"
      return 1
      ;;
  esac
  case "$HINDSIGHT_EMBED_UI_HOSTNAME" in
    127.0.0.1|::1) ;;
    *)
      print -ru2 -- "hindsight-embed-stack: UI hostname must be a literal loopback address"
      return 1
      ;;
  esac
}

hindsight_stack_require_runtime_helpers() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_EMBED_PYTHON" "Hindsight embed Python" executable allow-symlink || return 1
  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_EMBED_STOP_HELPER" "stop helper" readable allow-symlink || return 1
  hindsight_stack_run_bounded "$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" \
    "$HINDSIGHT_EMBED_PYTHON" -I "$HINDSIGHT_EMBED_STOP_HELPER" --help >/dev/null 2>&1 || {
    print -ru2 -- "hindsight-embed-stack: stop helper failed import/preflight at ${HINDSIGHT_EMBED_STOP_HELPER}"
    return 1
  }
}

hindsight_stack_valid_profile_name() {
  emulate -L zsh
  local profile="$1"
  [[ "$profile" =~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' ]]
}

hindsight_stack_enabled_profiles() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  local normalized="${HINDSIGHT_EMBED_FLEET_PROFILES//,/ }"
  local profile normalized_name
  typeset -A seen normalized_names
  for profile in ${=normalized}; do
    hindsight_stack_valid_profile_name "$profile" || {
      print -ru2 -- "hindsight-embed-stack: invalid enabled profile name: ${profile}"
      return 1
    }
    [[ -z "${seen[$profile]:-}" ]] || continue
    normalized_name="$(hindsight_stack_profile_env_prefix "$profile")"
    if [[ -n "${normalized_names[$normalized_name]:-}" ]]; then
      print -ru2 -- "hindsight-embed-stack: enabled profiles '${normalized_names[$normalized_name]}' and '${profile}' normalize to the same environment key: ${normalized_name}"
      return 1
    fi
    seen[$profile]=1
    normalized_names[$normalized_name]="$profile"
    print -r -- "$profile"
  done
}

hindsight_stack_fleet_profiles_csv() {
  emulate -L zsh
  local -a profiles
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  local IFS=,
  print -r -- "${profiles[*]}"
}

hindsight_stack_profile_enabled() {
  emulate -L zsh
  local requested="$1" profile
  local -a profiles
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  for profile in "${profiles[@]}"; do
    [[ "$profile" == "$requested" ]] && return 0
  done
  return 1
}

hindsight_stack_port_valid() {
  emulate -L zsh
  local value="$1"
  [[ "$value" == <1-65535> ]]
}

hindsight_stack_profile_env_prefix() {
  emulate -L zsh
  local value="${1:u}"
  value="${value//[^A-Z0-9]/_}"
  print -r -- "$value"
}

hindsight_stack_profile_port_override() {
  emulate -L zsh
  local profile="$1" kind="$2"
  local prefix
  prefix="$(hindsight_stack_profile_env_prefix "$profile")" || return 1
  local variable="HINDSIGHT_EMBED_PROFILE_${prefix}_${kind}_PORT"
  (( ${+parameters[$variable]} )) || return 1
  print -r -- "${(P)variable}"
}

hindsight_stack_profile_slot() {
  emulate -L zsh
  setopt no_unset
  hindsight_stack_load_config || return 1

  local profile="$1"
  hindsight_stack_valid_profile_name "$profile" || return 1
  [[ "$HINDSIGHT_EMBED_PROFILE_SLOT_DIR" == /* ]] || {
    print -ru2 -- "hindsight-embed-stack: profile slot directory must be absolute: ${HINDSIGHT_EMBED_PROFILE_SLOT_DIR}"
    return 1
  }
  local slot_parent="$HINDSIGHT_EMBED_PROFILE_SLOT_DIR"
  while [[ ! -e "$slot_parent" && "$slot_parent" != / ]]; do
    slot_parent="${slot_parent:h}"
  done
  if [[ "$slot_parent" != "$HINDSIGHT_EMBED_PROFILE_SLOT_DIR" ]]; then
    local slot_parent_owner slot_parent_mode
    slot_parent_owner="$(/usr/bin/stat -f '%u' "$slot_parent")" || return 1
    slot_parent_mode="$(/usr/bin/stat -f '%Lp' "$slot_parent")" || return 1
    (( slot_parent_owner == EUID && (8#$slot_parent_mode & 8#0077) == 0 )) || {
      print -ru2 -- "hindsight-embed-stack: profile slot parent must be private and current-user-owned: ${slot_parent}"
      return 1
    }
  fi
  hindsight_stack_prepare_private_state_directory \
    "$HINDSIGHT_EMBED_PROFILE_SLOT_DIR" "profile slot directory" || return 1

  local slot_file="$HINDSIGHT_EMBED_PROFILE_SLOT_DIR/${profile}.slot"
  local lock_file="$HINDSIGHT_EMBED_PROFILE_SLOT_DIR/.allocation.lock"
  if [[ -L "$lock_file" ]] || { [[ -e "$lock_file" ]] && [[ ! -f "$lock_file" ]]; }; then
    print -ru2 -- "hindsight-embed-stack: refusing unsafe profile slot allocation lock: ${lock_file}"
    return 1
  fi
  if ! ( umask 077; : >> "$lock_file" ) || ! /bin/chmod 600 "$lock_file"; then
    print -ru2 -- "hindsight-embed-stack: could not prepare profile slot allocation lock"
    return 1
  fi
  zmodload zsh/system || {
    print -ru2 -- "hindsight-embed-stack: zsh/system is required for profile slot allocation locking"
    return 1
  }
  integer lock_descriptor
  zsystem flock -f lock_descriptor -t 10 "$lock_file" || {
    print -ru2 -- "hindsight-embed-stack: timed out acquiring profile slot allocation lock"
    return 1
  }

  local result=0 slot temporary existing value
  typeset -A used
  if [[ -e "$slot_file" ]]; then
    if [[ ! -f "$slot_file" || -L "$slot_file" ]]; then
      print -ru2 -- "hindsight-embed-stack: refusing unsafe profile slot file: ${slot_file}"
      result=1
    elif ! /bin/chmod 600 "$slot_file"; then
      result=1
    else
      slot="$(<"$slot_file")"
      if [[ ! "$slot" =~ '^(0|[1-9][0-9]*)$' ]]; then
        print -ru2 -- "hindsight-embed-stack: invalid persisted slot for profile ${profile}"
        result=1
      fi
    fi
  else
    for existing in "$HINDSIGHT_EMBED_PROFILE_SLOT_DIR"/*.slot(N); do
      if [[ ! -f "$existing" || -L "$existing" ]]; then
        print -ru2 -- "hindsight-embed-stack: refusing unsafe loaded profile slot file: ${existing}"
        result=1
        break
      fi
      value="$(<"$existing")"
      if [[ ! "$value" =~ '^(0|[1-9][0-9]*)$' ]]; then
        print -ru2 -- "hindsight-embed-stack: invalid loaded profile slot file: ${existing}"
        result=1
        break
      fi
      used[$value]=1
    done
    if (( result == 0 )); then
      slot=0
      while [[ -n "${used[$slot]:-}" ]]; do
        (( slot += 1 ))
      done
      temporary="${slot_file}.$$.$RANDOM"
      if ! ( umask 077; print -r -- "$slot" > "$temporary" ); then
        result=1
      elif ! /bin/chmod 600 "$temporary" || ! /bin/mv "$temporary" "$slot_file"; then
        /bin/rm -f "$temporary"
        result=1
      fi
    fi
  fi
  zsystem flock -u "$lock_descriptor" || result=1
  (( result == 0 )) && print -r -- "$slot"
  return "$result"
}

hindsight_stack_select_profile() {
  emulate -L zsh
  setopt no_unset
  hindsight_stack_load_config || return 1

  local profile="$1"
  hindsight_stack_profile_enabled "$profile" || {
    print -ru2 -- "hindsight-embed-stack: profile '${profile}' is not enabled"
    return 1
  }
  hindsight_stack_profile_exists "$profile" || {
    print -ru2 -- "hindsight-embed-stack: enabled profile '${profile}' does not exist"
    return 1
  }
  local slot
  slot="$(hindsight_stack_profile_slot "$profile")" || return 1
  local api_port ui_port
  api_port="$(hindsight_stack_profile_port_override "$profile" API 2>/dev/null || print -r -- $(( HINDSIGHT_EMBED_API_BASE_PORT + slot )))"
  ui_port="$(hindsight_stack_profile_port_override "$profile" UI 2>/dev/null || print -r -- $(( HINDSIGHT_EMBED_UI_BASE_PORT + slot )))"
  hindsight_stack_port_valid "$api_port" || {
    print -ru2 -- "hindsight-embed-stack: invalid API port for profile ${profile}: ${api_port}"
    return 1
  }
  hindsight_stack_port_valid "$ui_port" || {
    print -ru2 -- "hindsight-embed-stack: invalid UI port for profile ${profile}: ${ui_port}"
    return 1
  }

  typeset -g HINDSIGHT_EMBED_PROFILE="$profile"
  typeset -g HINDSIGHT_EMBED_PROFILE_SLOT="$slot"
  typeset -g HINDSIGHT_EMBED_API_PORT="$api_port"
  typeset -g HINDSIGHT_EMBED_UI_PORT="$ui_port"
}

hindsight_stack_select_profile_for_stop() {
  emulate -L zsh
  setopt no_unset
  hindsight_stack_load_config || return 1
  local profile="$1"
  hindsight_stack_profile_enabled "$profile" || return 1
  local slot api_port ui_port
  slot="$(hindsight_stack_profile_slot "$profile")" || return 1
  api_port="$(hindsight_stack_profile_port_override "$profile" API 2>/dev/null ||
    print -r -- $(( HINDSIGHT_EMBED_API_BASE_PORT + slot )))"
  ui_port="$(hindsight_stack_profile_port_override "$profile" UI 2>/dev/null ||
    print -r -- $(( HINDSIGHT_EMBED_UI_BASE_PORT + slot )))"
  hindsight_stack_port_valid "$api_port" || return 1
  hindsight_stack_port_valid "$ui_port" || return 1
  typeset -g HINDSIGHT_EMBED_PROFILE="$profile"
  typeset -g HINDSIGHT_EMBED_PROFILE_SLOT="$slot"
  typeset -g HINDSIGHT_EMBED_API_PORT="$api_port"
  typeset -g HINDSIGHT_EMBED_UI_PORT="$ui_port"
}

typeset -ga HINDSIGHT_EMBED_PROFILE_STATE_STACK=()

hindsight_stack_push_profile_state() {
  emulate -L zsh
  HINDSIGHT_EMBED_PROFILE_STATE_STACK+=(
    "${HINDSIGHT_EMBED_PROFILE:-}|${HINDSIGHT_EMBED_PROFILE_SLOT:-}|${HINDSIGHT_EMBED_API_PORT:-}|${HINDSIGHT_EMBED_UI_PORT:-}"
  )
}

hindsight_stack_pop_profile_state() {
  emulate -L zsh
  (( ${#HINDSIGHT_EMBED_PROFILE_STATE_STACK} > 0 )) || return 0
  local state="${HINDSIGHT_EMBED_PROFILE_STATE_STACK[-1]}"
  HINDSIGHT_EMBED_PROFILE_STATE_STACK[-1]=()
  local -a values
  values=("${(@s:|:)state}")
  typeset -g HINDSIGHT_EMBED_PROFILE="${values[1]:-}"
  typeset -g HINDSIGHT_EMBED_PROFILE_SLOT="${values[2]:-}"
  typeset -g HINDSIGHT_EMBED_API_PORT="${values[3]:-}"
  typeset -g HINDSIGHT_EMBED_UI_PORT="${values[4]:-}"
}

hindsight_stack_for_each_profile() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local callback="$1"
  shift
  local profile_filter="$1"
  shift
  (( $+functions[$callback] )) || return 2
  local -a profiles
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  hindsight_stack_push_profile_state
  TRAPEXIT() { hindsight_stack_pop_profile_state }
  local profile ok=0
  for profile in "${profiles[@]}"; do
    [[ -z "$profile_filter" || "$profile_filter" == "$profile" ]] || continue
    hindsight_stack_select_profile "$profile" || {
      ok=1
      continue
    }
    "$callback" "$profile" "$@" || ok=1
  done
  return "$ok"
}

hindsight_stack_for_each_profile_for_stop() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local callback="$1"
  shift
  (( $+functions[$callback] )) || return 2
  local -a profiles
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  hindsight_stack_push_profile_state
  TRAPEXIT() { hindsight_stack_pop_profile_state }
  local profile ok=0
  for profile in "${profiles[@]}"; do
    hindsight_stack_select_profile_for_stop "$profile" || {
      ok=1
      continue
    }
    "$callback" "$profile" "$@" || ok=1
  done
  return "$ok"
}

hindsight_stack_profile_exists() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  local profile="${1:-$HINDSIGHT_EMBED_PROFILE}"
  [[ -n "$profile" ]] || return 1
  [[ -f "$HOME/.hindsight/profiles/${profile}.env" ]]
}

hindsight_stack_validate_profile_credential_isolation() {
  emulate -L zsh
  setopt extended_glob
  local profile="$1"
  hindsight_stack_valid_profile_name "$profile" || return 1
  local path="$HOME/.hindsight/profiles/${profile}.env"
  [[ -f "$path" && -r "$path" ]] || return 1

  local line key
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line##[[:space:]]#}"
    [[ -n "$line" && "$line" != \#* ]] || continue
    if [[ "$line" == export[[:space:]]* ]]; then
      line="${line#export}"
      line="${line##[[:space:]]#}"
    fi
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    key="${key%%[[:space:]]#}"
    case "$key" in
      HINDSIGHT_MEMORY_INVENTORY|\
      HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE|\
      HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV|\
      HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV|\
      HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV|\
      HINDSIGHT_API_TENANT_API_KEY|\
      HINDSIGHT_CP_DATAPLANE_API_KEY|\
      HINDSIGHT_CP_ACCESS_KEY)
        print -ru2 -- "hindsight-embed-stack: profile '${profile}' must not define controller-owned credential binding ${key}"
        return 1
        ;;
    esac
  done < "$path"
}

hindsight_stack_validate_fleet_profile_credential_isolation() {
  emulate -L zsh
  local profile
  local -a profiles
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  for profile in "${profiles[@]}"; do
    hindsight_stack_validate_profile_credential_isolation "$profile" || return 1
  done
}

hindsight_stack_require_profile() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_profile_exists && return 0

  print -ru2 -- "hindsight-embed-stack: configured profile '${HINDSIGHT_EMBED_PROFILE}' does not exist"
  print -ru2 -- "configure it before starting the stack: ${HINDSIGHT_EMBED_UVX} hindsight-embed configure --profile '${HINDSIGHT_EMBED_PROFILE}' --port '${HINDSIGHT_EMBED_API_PORT}'"
  return 1
}

hindsight_stack_require_fleet_profiles() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  local count=0 profile primary_present=0
  local -a profiles
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  for profile in "${profiles[@]}"; do
    (( count += 1 ))
    [[ "$profile" != "$HINDSIGHT_EMBED_PRIMARY_PROFILE" ]] || primary_present=1
    hindsight_stack_profile_exists "$profile" || {
      print -ru2 -- "hindsight-embed-stack: enabled profile '${profile}' does not exist"
      return 1
    }
  done
  (( count > 0 )) || {
    print -ru2 -- "hindsight-embed-stack: at least one enabled profile is required"
    return 1
  }
  (( primary_present )) || {
    print -ru2 -- "hindsight-embed-stack: primary profile '${HINDSIGHT_EMBED_PRIMARY_PROFILE}' must be enabled in the fleet"
    return 1
  }
}

hindsight_stack_enabled() {
  emulate -L zsh
  local value="${1:-}"

  case "$value" in
    true)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

hindsight_stack_http_ok() {
  emulate -L zsh
  local url="$1"
  local timeout="${2:-2}"
  hindsight_stack_validate_seconds "$timeout" "HTTP probe timeout" 3600 || return 1

  hindsight_stack_run_with_credential_scope none \
    /usr/bin/curl --noproxy '*' -fsS --max-time "$timeout" "$url" >/dev/null 2>&1
}

hindsight_stack_url_host() {
  emulate -L zsh
  local host="$1"
  if [[ "$host" == *:* && "$host" != \[*\] ]]; then
    print -r -- "[${host}]"
  else
    print -r -- "$host"
  fi
}

hindsight_stack_http_url() {
  emulate -L zsh
  local host
  host="$(hindsight_stack_url_host "$1")" || return 1
  local port="$2" path="${3:-}"
  print -r -- "http://${host}:${port}${path}"
}

hindsight_stack_ui_url() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  local host="$HINDSIGHT_EMBED_UI_HOSTNAME"
  case "$host" in
    ""|0.0.0.0|::)
      host="127.0.0.1"
      ;;
  esac
  hindsight_stack_http_url "$host" "$HINDSIGHT_EMBED_UI_PORT"
}

hindsight_stack_sidecar_root() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  print -r -- "$HOME/.hindsight/profiles/${HINDSIGHT_EMBED_PROFILE}.sidecars"
}

hindsight_stack_sidecar_names() {
  emulate -L zsh
  local root
  root="$(hindsight_stack_sidecar_root)" || return 1
  local directory name
  for directory in "$root"/*(N/); do
    name="${directory:t}"
    hindsight_stack_valid_profile_name "$name" || {
      print -ru2 -- "hindsight-embed-stack: invalid sidecar name for profile ${HINDSIGHT_EMBED_PROFILE}: ${name}"
      return 1
    }
    print -r -- "$name"
  done
}

hindsight_stack_sidecar_file_value() {
  emulate -L zsh
  local sidecar="$1" filename="$2"
  local path
  path="$(hindsight_stack_sidecar_root)/${sidecar}/${filename}" || return 1
  hindsight_stack_validate_trusted_metadata_file \
    "$path" "sidecar ${HINDSIGHT_EMBED_PROFILE}/${sidecar}/${filename}" || return 1
  local value
  value="$(<"$path")" || return 1
  print -r -- "$value"
}

hindsight_stack_validate_trusted_metadata_file() {
  emulate -L zsh
  local path="$1" label="$2"
  [[ "$path" == /* && -f "$path" && ! -L "$path" && -r "$path" ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be a readable regular file: ${path}"
    return 1
  }
  local resolved="${path:A}" owner mode ancestor ancestor_root
  [[ -f "$resolved" && ! -L "$resolved" && -r "$resolved" ]] || return 1
  owner="$(/usr/bin/stat -f '%u' "$resolved")" || return 1
  mode="$(/usr/bin/stat -f '%Lp' "$resolved")" || return 1
  (( owner == EUID || owner == 0 )) || return 1
  (( (8#$mode & 8#0022) == 0 )) || return 1
  for ancestor_root in "${path:h}" "${resolved:h}"; do
    ancestor="$ancestor_root"
    while [[ "$ancestor" != / ]]; do
      if [[ -L "$ancestor" ]]; then
        owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
        (( owner == 0 )) || return 1
      fi
      owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
      mode="$(/usr/bin/stat -f '%Lp' "$ancestor")" || return 1
      (( owner == EUID || owner == 0 )) || return 1
      if (( (8#$mode & 8#0022) != 0 && !(owner == 0 && (8#$mode & 8#01000) != 0) )); then
        return 1
      fi
      ancestor="${ancestor:h}"
    done
  done
}

hindsight_stack_sidecar_port() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local sidecar="$1" port base
  port="$(hindsight_stack_sidecar_file_value "$sidecar" port 2>/dev/null || true)"
  if [[ -z "$port" ]]; then
    base="$(hindsight_stack_sidecar_file_value "$sidecar" port-base 2>/dev/null || true)"
    hindsight_stack_port_valid "$base" || {
      print -ru2 -- "hindsight-embed-stack: sidecar ${HINDSIGHT_EMBED_PROFILE}/${sidecar} requires a valid port-base or port"
      return 1
    }
    port=$(( base + HINDSIGHT_EMBED_PROFILE_SLOT ))
  fi
  hindsight_stack_port_valid "$port" || {
    print -ru2 -- "hindsight-embed-stack: invalid sidecar port for ${HINDSIGHT_EMBED_PROFILE}/${sidecar}: ${port}"
    return 1
  }
  print -r -- "$port"
}

hindsight_stack_sidecar_health_url() {
  emulate -L zsh
  local sidecar="$1"
  local port path
  port="$(hindsight_stack_sidecar_port "$sidecar")" || return 1
  path="$(hindsight_stack_sidecar_file_value "$sidecar" health-path 2>/dev/null || print -r -- /health)" || return 1
  [[ "$path" == /* && "$path" != *[[:space:]]* ]] || {
    print -ru2 -- "hindsight-embed-stack: invalid sidecar health path for ${HINDSIGHT_EMBED_PROFILE}/${sidecar}"
    return 1
  }
  hindsight_stack_http_url 127.0.0.1 "$port" "$path"
}

hindsight_stack_validate_trusted_executable() {
  emulate -L zsh
  local executable="$1"
  local label="$2"
  [[ "$executable" == /* && -f "$executable" && ! -L "$executable" && -x "$executable" ]] || {
    print -ru2 -- "hindsight-embed-stack: ${label} must be a trusted executable regular file: ${executable}"
    return 1
  }
  local resolved="${executable:A}"
  [[ -f "$resolved" && ! -L "$resolved" && -x "$resolved" ]] || return 1
  local owner mode ancestor ancestor_root
  owner="$(/usr/bin/stat -f '%u' "$resolved")" || return 1
  mode="$(/usr/bin/stat -f '%Lp' "$resolved")" || return 1
  (( owner == EUID || owner == 0 )) || return 1
  (( (8#$mode & 8#0022) == 0 )) || return 1
  for ancestor_root in "${executable:h}" "${resolved:h}"; do
    ancestor="$ancestor_root"
    while [[ "$ancestor" != / ]]; do
      if [[ -L "$ancestor" ]]; then
        owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
        (( owner == 0 )) || return 1
      fi
      owner="$(/usr/bin/stat -f '%u' "$ancestor")" || return 1
      mode="$(/usr/bin/stat -f '%Lp' "$ancestor")" || return 1
      (( owner == EUID || owner == 0 )) || return 1
      if (( (8#$mode & 8#0022) != 0 && !(owner == 0 && (8#$mode & 8#01000) != 0) )); then
        return 1
      fi
      ancestor="${ancestor:h}"
    done
  done
}

hindsight_stack_sidecar_command() {
  emulate -L zsh
  local sidecar="$1" command="$2"
  local executable
  executable="$(hindsight_stack_sidecar_root)/${sidecar}/${command}" || return 1
  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_EMBED_PYTHON" "Hindsight embed Python" executable allow-symlink || return 1
  hindsight_stack_validate_trusted_executable \
    "$executable" "sidecar ${HINDSIGHT_EMBED_PROFILE}/${sidecar}/${command}" || return 1
  local port
  port="$(hindsight_stack_sidecar_port "$sidecar")" || return 1
  local timeout="${3:-${HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS:-30}}"
  [[ "$timeout" == <1-300> ]] || {
    print -ru2 -- "hindsight-embed-stack: sidecar command timeout must be an integer from 1 to 300 seconds"
    return 1
  }
  HINDSIGHT_EMBED_PROFILE="$HINDSIGHT_EMBED_PROFILE" \
    HINDSIGHT_EMBED_PROFILE_SLOT="$HINDSIGHT_EMBED_PROFILE_SLOT" \
    HINDSIGHT_EMBED_SIDECAR_NAME="$sidecar" \
    HINDSIGHT_EMBED_SIDECAR_PORT="$port" \
    hindsight_stack_run_with_credential_scope none \
      "$HINDSIGHT_EMBED_PYTHON" -I - "$timeout" "$executable" <<'PY'
import os
import signal
import subprocess
import sys
import time

timeout = int(sys.argv[1])
process = None
managed_signals = {signal.SIGINT, signal.SIGTERM}
previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, managed_signals)
previous_handlers = {}
child_mask = ",".join(str(int(signum)) for signum in previous_mask)
child_trampoline = r'''
import os
import signal
import sys

mask = {int(value) for value in sys.argv[1].split(",") if value}
signal.pthread_sigmask(signal.SIG_SETMASK, mask)
os.execv(sys.argv[2], [sys.argv[2]])
'''

def process_group_exists():
    if process is None:
        return False
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return process.poll() is None
    return True

def terminate_process_group():
    if process is None:
        return
    termination_mask = signal.pthread_sigmask(signal.SIG_BLOCK, managed_signals)
    termination_handlers = {
        signum: signal.getsignal(signum) for signum in managed_signals
    }
    for signum in managed_signals:
        signal.signal(signum, signal.SIG_IGN)
    try:
        if not process_group_exists():
            process.wait()
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        deadline = time.monotonic() + 2
        while process_group_exists() and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if process.poll() is None:
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
            else:
                time.sleep(min(0.05, remaining))
        if process_group_exists():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        kill_deadline = time.monotonic() + 2
        while process_group_exists() and time.monotonic() < kill_deadline:
            time.sleep(0.05)
        if process.poll() is None:
            try:
                process.wait(timeout=max(0, kill_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, termination_mask)
        for signum, handler in termination_handlers.items():
            signal.signal(signum, handler)

def interrupted(signum, _frame):
    raise SystemExit(128 + signum)

cleanup_required = False
try:
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in managed_signals
    }
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", child_trampoline, child_mask, sys.argv[2]],
            start_new_session=True,
        )
        if os.environ.get("HINDSIGHT_TEST_SIDECAR_WAIT_FOR_PENDING_TERM") == "1":
            deadline = time.monotonic() + 2
            while signal.SIGTERM not in signal.sigpending():
                if process.poll() is not None:
                    raise RuntimeError("sidecar exited before signaling its parent")
                if time.monotonic() >= deadline:
                    raise RuntimeError("sidecar did not signal its masked parent")
                time.sleep(0.001)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    status = process.wait(timeout=timeout)
except subprocess.TimeoutExpired:
    cleanup_required = True
    raise SystemExit(124)
except BaseException:
    cleanup_required = True
    raise
finally:
    try:
        if cleanup_required:
            terminate_process_group()
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, managed_signals)
        try:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
raise SystemExit(status if status >= 0 else 128 - status)
PY
}

hindsight_stack_sidecar_status() {
  emulate -L zsh
  local sidecar="$1" timeout="${2:-${HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS:-30}}"
  if [[ -x "$(hindsight_stack_sidecar_root)/${sidecar}/status" ]]; then
    hindsight_stack_sidecar_command "$sidecar" status "$timeout" >/dev/null 2>&1
  else
    hindsight_stack_http_ok "$(hindsight_stack_sidecar_health_url "$sidecar")" "$timeout"
  fi
}

hindsight_stack_sidecar_running() {
  emulate -L zsh
  local sidecar="$1"
  local port
  port="$(hindsight_stack_sidecar_port "$sidecar")" || return 1
  hindsight_stack_port_listening "$port"
}

hindsight_stack_sidecars_status() {
  emulate -L zsh
  local sidecar count=0 timeout="${1:-${HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS:-120}}"
  integer deadline=$(( $(/bin/date +%s) + timeout )) remaining
  local -a sidecars
  sidecars=("${(@f)$(hindsight_stack_sidecar_names)}") || return 1
  sidecars=("${(@)sidecars:#}")
  for sidecar in "${sidecars[@]}"; do
    (( count += 1 ))
    remaining=$(( deadline - $(/bin/date +%s) ))
    (( remaining > 0 )) || return 1
    (( remaining <= HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS )) ||
      remaining="$HINDSIGHT_EMBED_SIDECAR_COMMAND_TIMEOUT_SECONDS"
    hindsight_stack_sidecar_status "$sidecar" "$remaining" || return 1
  done
  return 0
}

hindsight_stack_wait_sidecars() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  integer deadline
  deadline=$(( $(/bin/date +%s) + HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS ))
  integer remaining sleep_seconds
  while (( (remaining = deadline - $(/bin/date +%s)) > 0 )); do
    hindsight_stack_sidecars_status "$remaining" && return 0
    sleep_seconds=$(( deadline - $(/bin/date +%s) ))
    (( sleep_seconds > 0 )) || break
    (( sleep_seconds <= 2 )) || sleep_seconds=2
    sleep "$sleep_seconds"
  done
  return 1
}

hindsight_stack_wait_sidecars_stopped() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  integer deadline
  deadline=$(( $(/bin/date +%s) + HINDSIGHT_EMBED_STOP_WAIT_SECONDS ))
  local sidecar running
  local -a sidecars
  sidecars=("${(@f)$(hindsight_stack_sidecar_names)}") || return 1
  sidecars=("${(@)sidecars:#}")
  integer remaining sleep_seconds
  while (( (remaining = deadline - $(/bin/date +%s)) > 0 )); do
    running=0
    for sidecar in "${sidecars[@]}"; do
      (( deadline - $(/bin/date +%s) > 0 )) || return 1
      hindsight_stack_sidecar_running "$sidecar" && running=1
    done
    (( running == 0 )) && return 0
    sleep_seconds=$(( deadline - $(/bin/date +%s) ))
    (( sleep_seconds > 0 )) || break
    (( sleep_seconds <= 2 )) || sleep_seconds=2
    sleep "$sleep_seconds"
  done
  return 1
}

hindsight_stack_reconcile_sidecars() {
  emulate -L zsh
  local sidecar key ok=0
  local -a sidecars
  sidecars=("${(@f)$(hindsight_stack_sidecar_names)}") || return 1
  sidecars=("${(@)sidecars:#}")
  for sidecar in "${sidecars[@]}"; do
    hindsight_stack_sidecar_status "$sidecar" && continue
    key="sidecar:${HINDSIGHT_EMBED_PROFILE}:${sidecar}"
    if ! hindsight_stack_can_start "$key"; then
      ok=1
      continue
    fi
    hindsight_stack_mark_start "$key"
    hindsight_stack_log "sidecar is not healthy; starting ${HINDSIGHT_EMBED_PROFILE}/${sidecar}"
    hindsight_stack_sidecar_command "$sidecar" start >/dev/null 2>&1 || ok=1
  done
  (( ok == 0 )) && hindsight_stack_wait_sidecars
}

hindsight_stack_stop_sidecars() {
  emulate -L zsh
  local -a sidecars
  sidecars=("${(@f)$(hindsight_stack_sidecar_names)}") || return 1
  sidecars=("${(@)sidecars:#}")
  local sidecar ok=0
  for sidecar in "${(@Oa)sidecars}"; do
    hindsight_stack_sidecar_command "$sidecar" stop >/dev/null 2>&1 || ok=1
  done
  return "$ok"
}

hindsight_stack_validate_profile_endpoints() {
  emulate -L zsh
  local profile="$1" sidecar port owner
  local -a profile_endpoints sidecars
  integer endpoint_index
  profile_endpoints=(
    "$HINDSIGHT_EMBED_API_PORT" "${profile}/api"
    "$HINDSIGHT_EMBED_UI_PORT" "${profile}/ui"
  )
  endpoint_index=1
  while (( endpoint_index <= ${#profile_endpoints} )); do
    port="${profile_endpoints[$endpoint_index]}"
    owner="${profile_endpoints[$(( endpoint_index + 1 ))]}"
    if [[ -n "${endpoints[$port]:-}" ]]; then
      print -ru2 -- "hindsight-embed-stack: endpoint collision on port ${port}: ${endpoints[$port]} and ${owner}"
      return 1
    fi
    endpoints[$port]="$owner"
    (( endpoint_index += 2 ))
  done
  sidecars=("${(@f)$(hindsight_stack_sidecar_names)}") || return 1
  sidecars=("${(@)sidecars:#}")
  for sidecar in "${sidecars[@]}"; do
    port="$(hindsight_stack_sidecar_port "$sidecar")" || return 1
    owner="${profile}/sidecar/${sidecar}"
    if [[ -n "${endpoints[$port]:-}" ]]; then
      print -ru2 -- "hindsight-embed-stack: endpoint collision on port ${port}: ${endpoints[$port]} and ${owner}"
      return 1
    fi
    endpoints[$port]="$owner"
  done
}

hindsight_stack_validate_fleet() {
  emulate -L zsh
  setopt no_unset
  hindsight_stack_load_config || return 1
  hindsight_stack_require_loopback || return 1
  hindsight_stack_require_fleet_profiles || return 1
  hindsight_stack_port_valid "$HINDSIGHT_EMBED_CONTROL_PORT" || {
    print -ru2 -- "hindsight-embed-stack: invalid machine-global control port"
    return 1
  }

  typeset -A endpoints
  endpoints[$HINDSIGHT_EMBED_CONTROL_PORT]="control"
  hindsight_stack_for_each_profile hindsight_stack_validate_profile_endpoints ""
}

hindsight_stack_run_stop_helper() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_require_runtime_helpers || return 1

  hindsight_stack_run_bounded "$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" \
    "$HINDSIGHT_EMBED_PYTHON" -I "$HINDSIGHT_EMBED_STOP_HELPER" \
    "$@"
}

hindsight_stack_stop_legacy_profile_ports() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_run_stop_helper \
    --mode normalize \
    --profile "$HINDSIGHT_EMBED_PROFILE" \
    --desired-api-port "$HINDSIGHT_EMBED_API_PORT" \
    --desired-ui-port "$HINDSIGHT_EMBED_UI_PORT" \
    --require-profile
}

hindsight_stack_stop_profile_services() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local profile="$1"

  hindsight_stack_run_stop_helper \
    --mode stop \
    --profile "$profile" \
    --desired-api-port "$HINDSIGHT_EMBED_API_PORT" \
    --desired-ui-port "$HINDSIGHT_EMBED_UI_PORT"
}

hindsight_stack_write_profile_ports() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_run_bounded "$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed profile set-env \
    "$HINDSIGHT_EMBED_PROFILE" HINDSIGHT_API_PORT "$HINDSIGHT_EMBED_API_PORT" >/dev/null 2>&1 &&
    hindsight_stack_run_bounded "$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" \
      "$HINDSIGHT_EMBED_UVX" hindsight-embed profile set-env \
      "$HINDSIGHT_EMBED_PROFILE" HINDSIGHT_EMBED_CP_PORT "$HINDSIGHT_EMBED_UI_PORT" >/dev/null 2>&1
}

hindsight_stack_ensure_profile_ports() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_stop_legacy_profile_ports || return 1
  hindsight_stack_write_profile_ports
}

hindsight_stack_port_listening() {
  emulate -L zsh
  local port="$1"

  [[ -x /usr/sbin/lsof ]] || return 1
  /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

hindsight_stack_control_status() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"

  hindsight_stack_run_bounded "$probe_timeout" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed control status \
    --port "$HINDSIGHT_EMBED_CONTROL_PORT" >/dev/null 2>&1
}

hindsight_stack_control_running() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
  integer probe_deadline=$(( $(/bin/date +%s) + probe_timeout ))

  hindsight_stack_control_status "$probe_timeout" && return 0
  probe_timeout=$(( probe_deadline - $(/bin/date +%s) ))
  if (( probe_timeout > 0 )) && hindsight_stack_http_ok \
    "$(hindsight_stack_http_url "$HINDSIGHT_EMBED_CONTROL_HOSTNAME" "$HINDSIGHT_EMBED_CONTROL_PORT")" \
    "$probe_timeout"; then
    return 0
  fi
  hindsight_stack_port_listening "$HINDSIGHT_EMBED_CONTROL_PORT"
}

hindsight_stack_broker_status() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
  local profile
  local -a profiles arguments
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  arguments=(--state-dir "$HINDSIGHT_MEMORY_STATE_DIR" broker status \
    --socket "$HINDSIGHT_MEMORY_BROKER_SOCKET")
  arguments+=("${(@f)$(hindsight_stack_broker_runtime_arguments)}")
  for profile in "${profiles[@]}"; do
    hindsight_stack_valid_profile_name "$profile" || return 1
    arguments+=(--profile "$profile")
  done
  hindsight_stack_run_bounded "$probe_timeout" \
    "$HINDSIGHT_MEMORY_CLI" "${arguments[@]}" >/dev/null 2>&1
}

hindsight_stack_broker_runtime_arguments() {
  emulate -L zsh
  if hindsight_stack_runtime_active; then
    print -r -- --inventory
    print -r -- "$HINDSIGHT_MEMORY_INVENTORY"
    print -r -- --data-plane-token-env
    print -r -- "$HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV"
    print -r -- --mint-authority-env
    print -r -- "$HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV"
    if [[ -n "${HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE:-}" ]]; then
      print -r -- --integration-upgrade-state
      print -r -- "$HINDSIGHT_MEMORY_INTEGRATION_UPGRADE_STATE"
    fi
  else
    print -r -- --inactive
  fi
}

hindsight_stack_broker_running() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  # A socket node alone can be stale or attacker-controlled. The bounded broker
  # status exchange verifies the selected profile and the broker protocol.
  hindsight_stack_broker_status "${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
}

hindsight_stack_daemon_status() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
  integer probe_deadline=$(( $(/bin/date +%s) + probe_timeout ))

  hindsight_stack_run_bounded "$probe_timeout" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed --profile "$HINDSIGHT_EMBED_PROFILE" daemon status >/dev/null 2>&1 || return 1
  probe_timeout=$(( probe_deadline - $(/bin/date +%s) ))
  (( probe_timeout > 0 )) || return 1
  hindsight_stack_http_ok \
    "$(hindsight_stack_http_url 127.0.0.1 "$HINDSIGHT_EMBED_API_PORT" /health)" "$probe_timeout"
}

hindsight_stack_daemon_running() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
  integer probe_deadline=$(( $(/bin/date +%s) + probe_timeout ))

  if hindsight_stack_run_bounded "$probe_timeout" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed --profile "$HINDSIGHT_EMBED_PROFILE" daemon status >/dev/null 2>&1; then
    return 0
  fi
  probe_timeout=$(( probe_deadline - $(/bin/date +%s) ))
  if (( probe_timeout > 0 )) && hindsight_stack_http_ok \
    "$(hindsight_stack_http_url 127.0.0.1 "$HINDSIGHT_EMBED_API_PORT" /health)" "$probe_timeout"; then
    return 0
  fi
  hindsight_stack_port_listening "$HINDSIGHT_EMBED_API_PORT"
}

hindsight_stack_ui_auth_probe() {
  emulate -L zsh
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
  hindsight_stack_validate_seconds "$probe_timeout" "UI authentication probe timeout" 3600 || return 1
  hindsight_stack_require_trusted_artifact \
    "$HINDSIGHT_EMBED_PYTHON" "Hindsight embed Python" executable allow-symlink || return 1

  local probe_program
  IFS= read -r -d '' probe_program <<'PY' || true
import hashlib
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

base_url = sys.argv[1].rstrip("/")
timeout = float(sys.argv[2])
access_key = os.environ.get("HINDSIGHT_CP_ACCESS_KEY", "")
data_plane_token = os.environ.get("HINDSIGHT_CP_DATAPLANE_API_KEY", "")


def fail(code):
    print(f"hindsight-ui-auth-probe: {code}", file=sys.stderr)
    raise SystemExit(1)


if not access_key or not data_plane_token:
    fail("CREDENTIALS_UNAVAILABLE")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener_with_cookies():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        urllib.request.HTTPCookieProcessor(jar),
    )
    return opener, jar


def request(opener, path, *, body=None, expected_status, failure_code):
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base_url + path,
        data=encoded,
        headers=headers,
        method="POST" if encoded is not None else "GET",
    )
    try:
        response = opener.open(req, timeout=timeout)
        status = response.status
        response_headers = response.headers
        response_body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = exc.headers
        response_body = exc.read(1024 * 1024 + 1)
    if status != expected_status or len(response_body) > 1024 * 1024:
        fail(failure_code)
    exposed = str(response_headers).encode("utf-8", "replace") + response_body
    for secret in (access_key, data_plane_token):
        if secret.encode("utf-8") in exposed:
            fail("SECRET_EXPOSED")
    return response_headers, response_body


anonymous, _ = opener_with_cookies()
_, version_body = request(
    anonymous,
    "/api/version",
    expected_status=200,
    failure_code="VERSION_UNAVAILABLE",
)
try:
    version = json.loads(version_body)
except (json.JSONDecodeError, TypeError):
    fail("VERSION_RESPONSE_INVALID")
if version.get("features", {}).get("access_key_auth") is not True:
    fail("ACCESS_KEY_AUTH_DISABLED")
request(
    anonymous,
    "/api/banks",
    expected_status=401,
    failure_code="ANONYMOUS_ACCESS_ACCEPTED",
)

missing, _ = opener_with_cookies()
request(
    missing,
    "/api/auth/login",
    body={},
    expected_status=401,
    failure_code="MISSING_LOGIN_ACCEPTED",
)

wrong_key = hashlib.sha256(access_key.encode("utf-8")).hexdigest()
if wrong_key == access_key:
    wrong_key += "-invalid"
wrong, _ = opener_with_cookies()
request(
    wrong,
    "/api/auth/login",
    body={"key": wrong_key},
    expected_status=401,
    failure_code="WRONG_LOGIN_ACCEPTED",
)

authenticated, cookie_jar = opener_with_cookies()
login_headers, _ = request(
    authenticated,
    "/api/auth/login",
    body={"key": access_key},
    expected_status=200,
    failure_code="VALID_LOGIN_REJECTED",
)
access_cookies = [
    value
    for value in login_headers.get_all("Set-Cookie", [])
    if value.lstrip().startswith("hindsight_cp_access=")
]
if not access_cookies or any(
    "httponly" not in {
        attribute.strip().lower()
        for attribute in value.split(";")[1:]
    }
    for value in access_cookies
):
    fail("COOKIE_NOT_HTTP_ONLY")
if not any(cookie.name == "hindsight_cp_access" for cookie in cookie_jar):
    fail("COOKIE_NOT_STORED")
request(
    authenticated,
    "/api/banks",
    expected_status=200,
    failure_code="AUTHENTICATED_ACCESS_REJECTED",
)
PY
  hindsight_stack_run_bounded_ui_proxy "$probe_timeout" \
    "$HINDSIGHT_EMBED_PYTHON" -I -c "$probe_program" \
    "$(hindsight_stack_ui_url)" "$probe_timeout"
}

hindsight_stack_ui_auth_status() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_runtime_active || return 0
  hindsight_stack_preflight_runtime_credentials || return 1
  hindsight_stack_ui_auth_probe "$@"
}

hindsight_stack_ui_status_probe() {
  emulate -L zsh
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
  integer probe_deadline=$(( $(/bin/date +%s) + probe_timeout ))

  hindsight_stack_run_bounded "$probe_timeout" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed --profile "$HINDSIGHT_EMBED_PROFILE" ui status \
    --port "$HINDSIGHT_EMBED_UI_PORT" >/dev/null 2>&1 || return 1
  probe_timeout=$(( probe_deadline - $(/bin/date +%s) ))
  (( probe_timeout > 0 )) || return 1
  if hindsight_stack_runtime_active; then
    hindsight_stack_ui_auth_probe "$probe_timeout"
  else
    hindsight_stack_http_ok "$(hindsight_stack_ui_url)" "$probe_timeout"
  fi
}

hindsight_stack_ui_status() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  if hindsight_stack_runtime_active; then
    hindsight_stack_preflight_runtime_credentials || return 1
  fi
  hindsight_stack_ui_status_probe "$@"
}

hindsight_stack_ui_running() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  local probe_timeout="${1:-$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS}"
  integer probe_deadline=$(( $(/bin/date +%s) + probe_timeout ))

  if hindsight_stack_run_bounded "$probe_timeout" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed --profile "$HINDSIGHT_EMBED_PROFILE" ui status \
    --port "$HINDSIGHT_EMBED_UI_PORT" >/dev/null 2>&1; then
    return 0
  fi
  probe_timeout=$(( probe_deadline - $(/bin/date +%s) ))
  if (( probe_timeout > 0 )) && hindsight_stack_http_ok \
    "$(hindsight_stack_ui_url)" "$probe_timeout"; then
    return 0
  fi
  hindsight_stack_port_listening "$HINDSIGHT_EMBED_UI_PORT"
}

hindsight_stack_control_start() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_preflight_runtime_credentials || return 1

  hindsight_stack_run_bounded "$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed control start --no-open \
    --port "$HINDSIGHT_EMBED_CONTROL_PORT" >/dev/null 2>&1
}

hindsight_stack_broker_process_record() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  print -r -- "${HINDSIGHT_EMBED_STATE_DIR}/broker-process.identity"
}

hindsight_stack_broker_process_identity() {
  emulate -L zsh
  local pid="$1"
  [[ "$pid" == <-> ]] || return 1
  hindsight_stack_run_with_credential_scope none \
    "$HINDSIGHT_EMBED_PYTHON" -I - "$pid" <<'PY'
import ctypes
import sys

class ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32), ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32), ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32), ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32), ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32), ("comm", ctypes.c_char * 17),
        ("name", ctypes.c_char * 33), ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32), ("pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32), ("e_tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32), ("start_tvsec", ctypes.c_uint64),
        ("start_tvusec", ctypes.c_uint64),
    ]

pid = int(sys.argv[1])
libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
info = ProcBsdInfo()
size = libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
if size != ctypes.sizeof(info) or info.pid != pid:
    raise SystemExit(1)
print(f"{info.start_tvsec}:{info.start_tvusec}")
PY
}

hindsight_stack_broker_read_process_record() {
  emulate -L zsh
  local record
  record="$(hindsight_stack_broker_process_record)" || return 1
  [[ -f "$record" && ! -L "$record" ]] || return 1
  [[ "$(/usr/bin/stat -f '%u:%Lp' "$record")" == "${EUID}:600" ]] || return 1
  local -a lines=("${(@f)$(<"$record")}")
  (( ${#lines} == 2 )) || return 1
  [[ "$lines[1]" == <-> && -n "$lines[2]" ]] || return 1
  typeset -g HINDSIGHT_STACK_BROKER_PID="$lines[1]"
  typeset -g HINDSIGHT_STACK_BROKER_IDENTITY="$lines[2]"
}

hindsight_stack_broker_identity_matches() {
  emulate -L zsh
  hindsight_stack_broker_read_process_record || return 1
  local current
  current="$(hindsight_stack_broker_process_identity "$HINDSIGHT_STACK_BROKER_PID")" || return 1
  [[ "$current" == "$HINDSIGHT_STACK_BROKER_IDENTITY" ]]
}

hindsight_stack_broker_clear_process_record() {
  emulate -L zsh
  local record
  record="$(hindsight_stack_broker_process_record)" || return 1
  /bin/rm -f "$record"
  unset HINDSIGHT_STACK_BROKER_PID HINDSIGHT_STACK_BROKER_IDENTITY
}

hindsight_stack_broker_write_process_record() {
  emulate -L zsh
  local pid="$1" identity="$2" record temporary
  record="$(hindsight_stack_broker_process_record)" || return 1
  hindsight_stack_prepare_lifecycle_lock >/dev/null || return 1
  temporary="${record}.$$.$RANDOM"
  ( umask 077; print -r -- "$pid" >"$temporary" && print -r -- "$identity" >>"$temporary" ) || return 1
  /bin/chmod 600 "$temporary" || {
    /bin/rm -f "$temporary"
    return 1
  }
  /bin/mv -f "$temporary" "$record"
}

hindsight_stack_broker_terminate_started() {
  emulate -L zsh
  unsetopt ERR_EXIT
  local pid="$1" expected="${2:-}" current
  [[ "$pid" == <-> ]] || return 1
  [[ -n "$expected" ]] || return 1
  current="$(hindsight_stack_broker_process_identity "$pid")" || return 0
  [[ "$current" == "$expected" ]] || return 1
  /bin/kill -TERM "$pid" >/dev/null 2>&1 || return 0
  local attempt
  for attempt in {1..20}; do
    current="$(hindsight_stack_broker_process_identity "$pid")" || return 0
    [[ "$current" == "$expected" ]] || return 1
    sleep 0.1
  done
  current="$(hindsight_stack_broker_process_identity "$pid")" || return 0
  [[ "$current" == "$expected" ]] || return 1
  /bin/kill -KILL "$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
  return 0
}

hindsight_stack_broker_abort_launch() {
  emulate -L zsh
  unsetopt ERR_EXIT
  local pid="$1" expected="${2:-}"
  [[ "$pid" == <-> ]] || return 1
  if [[ -n "$expected" ]]; then
    hindsight_stack_broker_terminate_started "$pid" "$expected"
    return $?
  fi
  # Before immutable identity capture, the unreaped direct child still owns
  # this PID, so it cannot be reused until this shell waits for it.
  /bin/kill -KILL "$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
}

hindsight_stack_broker_wait_launch_barrier() {
  emulate -L zsh
  local pid="$1" attempt state
  [[ "$pid" == <-> ]] || return 1
  for attempt in {1..500}; do
    state="$(/bin/ps -o state= -p "$pid" 2>/dev/null)" || {
      kill -0 "$pid" >/dev/null 2>&1 || return 1
      sleep 0.01
      continue
    }
    [[ "$state" == *T* ]] && return 0
    sleep 0.01
  done
  return 1
}

hindsight_stack_broker_terminate_recorded() {
  emulate -L zsh
  unsetopt ERR_EXIT
  hindsight_stack_broker_read_process_record || return 0
  local pid="$HINDSIGHT_STACK_BROKER_PID" expected="$HINDSIGHT_STACK_BROKER_IDENTITY"
  local current
  current="$(hindsight_stack_broker_process_identity "$pid")" || {
    hindsight_stack_broker_clear_process_record
    return 0
  }
  [[ "$current" == "$expected" ]] || {
    hindsight_stack_broker_clear_process_record || return 1
    print -ru2 -- "hindsight-embed-stack: refusing to signal broker with changed process identity: ${pid}"
    return 1
  }
  print -ru2 -- "hindsight-embed-stack: refusing to signal live recorded broker process: ${pid}"
  return 1
}

hindsight_stack_broker_remove_stale_socket() {
  emulate -L zsh
  [[ -e "$HINDSIGHT_MEMORY_BROKER_SOCKET" ]] || return 0
  [[ -S "$HINDSIGHT_MEMORY_BROKER_SOCKET" && ! -L "$HINDSIGHT_MEMORY_BROKER_SOCKET" ]] || {
    print -ru2 -- "hindsight-embed-stack: refusing unsafe broker socket cleanup: ${HINDSIGHT_MEMORY_BROKER_SOCKET}"
    return 1
  }
  hindsight_stack_broker_status 1 && {
    print -ru2 -- "hindsight-embed-stack: refusing to remove a responsive broker socket"
    return 1
  }
  hindsight_stack_broker_identity_matches && return 1
  # A status failure can mean only that the configured profile set differs.
  # Leave the socket for `broker serve`, whose profile-independent protocol
  # probe refuses responsive brokers and removes only an unresponsive node.
  return 0
}

hindsight_stack_broker_start() {
  emulate -L zsh
  setopt LOCAL_TRAPS
  unsetopt BG_NICE
  local broker_launch_owned=0 pid='' identity=''
  trap 'if (( ${broker_launch_owned:-0} )); then broker_launch_owned=0; hindsight_stack_broker_abort_launch "${pid:-}" "${identity:-}" >/dev/null 2>&1 || true; fi' EXIT
  trap 'if (( ${broker_launch_owned:-0} )); then broker_launch_owned=0; hindsight_stack_broker_abort_launch "${pid:-}" "${identity:-}" >/dev/null 2>&1 || true; fi; return 130' INT
  trap 'if (( ${broker_launch_owned:-0} )); then broker_launch_owned=0; hindsight_stack_broker_abort_launch "${pid:-}" "${identity:-}" >/dev/null 2>&1 || true; fi; return 143' TERM
  {
    hindsight_stack_load_config || return 1
    hindsight_stack_preflight_runtime_credentials || return 1

  local -a arguments
  arguments=(
    --state-dir "$HINDSIGHT_MEMORY_STATE_DIR"
    broker serve --socket "$HINDSIGHT_MEMORY_BROKER_SOCKET"
  )
  arguments+=("${(@f)$(hindsight_stack_broker_runtime_arguments)}")
  local profile
  local -a profiles
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  for profile in "${profiles[@]}"; do
    arguments+=(--profile "$profile")
  done
  hindsight_stack_broker_terminate_recorded || return 1
  hindsight_stack_broker_remove_stale_socket || return 1
  hindsight_stack_run_with_credential_scope broker \
    "$HINDSIGHT_EMBED_PYTHON" -I -c '
import os
import signal
import sys

executable = sys.argv[1]
arguments = sys.argv[1:]
os.kill(os.getpid(), signal.SIGSTOP)
os.execv(executable, arguments)
' "$HINDSIGHT_MEMORY_CLI" "${arguments[@]}" >/dev/null 2>&1 &
  pid=$!
  broker_launch_owned=1
  hindsight_stack_broker_wait_launch_barrier "$pid" || {
    broker_launch_owned=0
    hindsight_stack_broker_abort_launch "$pid"
    print -ru2 -- "hindsight-embed-stack: broker launch handshake failed"
    return 1
  }
  identity="$(hindsight_stack_broker_process_identity "$pid")" || {
    broker_launch_owned=0
    hindsight_stack_broker_abort_launch "$pid"
    print -ru2 -- "hindsight-embed-stack: could not capture immutable broker process identity"
    return 1
  }
  hindsight_stack_broker_write_process_record "$pid" "$identity" || {
    broker_launch_owned=0
    hindsight_stack_broker_abort_launch "$pid" "$identity"
    return 1
  }
  /bin/kill -CONT "$pid" >/dev/null 2>&1 || {
    broker_launch_owned=0
    hindsight_stack_broker_abort_launch "$pid" "$identity"
    hindsight_stack_broker_terminate_recorded || return 1
    return 1
  }
  if ! hindsight_stack_wait_broker; then
    broker_launch_owned=0
    hindsight_stack_broker_abort_launch "$pid" "$identity"
    hindsight_stack_broker_terminate_recorded || return 1
    hindsight_stack_broker_remove_stale_socket || true
    return 1
  fi
  hindsight_stack_broker_identity_matches || {
    broker_launch_owned=0
    hindsight_stack_broker_abort_launch "$pid" "$identity"
    hindsight_stack_broker_terminate_recorded || return 1
    return 1
  }
  disown %% >/dev/null 2>&1 || {
    broker_launch_owned=0
    hindsight_stack_broker_abort_launch "$pid" "$identity"
    hindsight_stack_broker_terminate_recorded || return 1
    return 1
  }
    broker_launch_owned=0
  } always {
    if (( broker_launch_owned )); then
      broker_launch_owned=0
      hindsight_stack_broker_abort_launch "$pid" "${identity:-}" >/dev/null 2>&1 || true
    fi
  }
}

hindsight_stack_daemon_start() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_preflight_runtime_credentials || return 1

  hindsight_stack_ensure_profile_ports || return 1
  if hindsight_stack_run_bounded_api "$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed --profile "$HINDSIGHT_EMBED_PROFILE" daemon start >/dev/null 2>&1; then
    return 0
  fi

  # The embedded launcher can reject a transient post-start stability probe
  # while leaving the detached daemon to finish initialization. Keep the
  # reusable lifecycle contract authoritative by accepting that handoff only
  # when the daemon becomes healthy within our own bounded wait.
  hindsight_stack_wait_daemon
}

hindsight_stack_ui_start() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_preflight_runtime_credentials || return 1

  hindsight_stack_daemon_status || {
    print -ru2 -- "hindsight-embed-stack: refusing to start UI for ${HINDSIGHT_EMBED_PROFILE} without a healthy daemon"
    return 1
  }

  if hindsight_stack_runtime_active && hindsight_stack_ui_running; then
    hindsight_stack_ui_status && return 0
    hindsight_stack_ui_stop || return 1
    hindsight_stack_wait_stopped_for ui "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || return 1
  fi

  hindsight_stack_ensure_profile_ports || return 1
  hindsight_stack_run_bounded_ui_proxy "$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS" \
    "$HINDSIGHT_EMBED_UVX" hindsight-embed --profile "$HINDSIGHT_EMBED_PROFILE" ui start \
    --port "$HINDSIGHT_EMBED_UI_PORT" \
    --hostname "$HINDSIGHT_EMBED_UI_HOSTNAME" >/dev/null 2>&1
}

hindsight_stack_control_stop() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_run_stop_helper \
    --mode stop-control \
    --control-port "$HINDSIGHT_EMBED_CONTROL_PORT"
}

hindsight_stack_broker_stop() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  local outer_timeout=$(( HINDSIGHT_EMBED_STOP_WAIT_SECONDS + 5 ))
  local command_result=0
  hindsight_stack_run_bounded "$outer_timeout" \
    "$HINDSIGHT_MEMORY_CLI" --state-dir "$HINDSIGHT_MEMORY_STATE_DIR" \
    broker stop --socket "$HINDSIGHT_MEMORY_BROKER_SOCKET" \
    --timeout "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || command_result=$?
  hindsight_stack_broker_terminate_recorded || return 1
  hindsight_stack_broker_remove_stale_socket || return 1
  [[ ! -e "$HINDSIGHT_MEMORY_BROKER_SOCKET" ]] || return 1
  (( command_result == 0 || command_result == 124 ))
}

hindsight_stack_daemon_stop() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_run_stop_helper \
    --mode stop-api \
    --profile "$HINDSIGHT_EMBED_PROFILE" \
    --desired-api-port "$HINDSIGHT_EMBED_API_PORT"
}

hindsight_stack_ui_stop() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  hindsight_stack_run_stop_helper \
    --mode stop-ui \
    --profile "$HINDSIGHT_EMBED_PROFILE" \
    --desired-ui-port "$HINDSIGHT_EMBED_UI_PORT"
}

hindsight_stack_can_start() {
  emulate -L zsh
  hindsight_stack_load_config || return 1

  local component="$1"
  integer now last
  now="$(/bin/date +%s)"
  last="${HINDSIGHT_EMBED_LAST_START_EPOCH[$component]:-0}"

  (( now - last >= HINDSIGHT_EMBED_START_COOLDOWN_SECONDS ))
}

hindsight_stack_mark_start() {
  emulate -L zsh
  local component="$1"

  HINDSIGHT_EMBED_LAST_START_EPOCH[$component]="$(/bin/date +%s)"
}

hindsight_stack_wait_for() {
  emulate -L zsh
  setopt no_unset

  local component="$1" timeout_value="$2"
  hindsight_stack_validate_seconds "$timeout_value" "wait timeout" 3600 || return 2
  if [[ "$component" == ui ]]; then
    hindsight_stack_load_config || return 1
    if hindsight_stack_runtime_active; then
      hindsight_stack_preflight_runtime_credentials || return 1
    fi
  fi
  integer timeout_seconds="$timeout_value"
  integer deadline
  deadline=$(( $(/bin/date +%s) + timeout_seconds ))

  local now probe_timeout sleep_seconds
  while (( (now = $(/bin/date +%s)) < deadline )); do
    probe_timeout=$(( deadline - now ))
    (( probe_timeout <= HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS )) ||
      probe_timeout="$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS"
    case "$component" in
      broker)
        hindsight_stack_broker_status "$probe_timeout" && return 0
        ;;
      control)
        hindsight_stack_control_status "$probe_timeout" && return 0
        ;;
      daemon)
        hindsight_stack_daemon_status "$probe_timeout" && return 0
        ;;
      ui)
        hindsight_stack_ui_status_probe "$probe_timeout" && return 0
        ;;
      *)
        print -ru2 -- "hindsight-embed-stack: unknown component: ${component}"
        return 2
        ;;
    esac
    now="$(/bin/date +%s)"
    sleep_seconds=$(( deadline - now ))
    (( sleep_seconds > 0 )) || break
    (( sleep_seconds <= 2 )) || sleep_seconds=2
    sleep "$sleep_seconds"
  done

  return 1
}

hindsight_stack_wait_control() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_wait_for control "$HINDSIGHT_EMBED_CONTROL_WAIT_SECONDS"
}

hindsight_stack_wait_broker() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_wait_for broker "$HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS"
}

hindsight_stack_wait_daemon() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_wait_for daemon "$HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS"
}

hindsight_stack_wait_ui() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_wait_for ui "$HINDSIGHT_EMBED_UI_WAIT_SECONDS"
}

hindsight_stack_wait_stopped_for() {
  emulate -L zsh
  setopt no_unset

  local component="$1" timeout_value="$2"
  hindsight_stack_validate_seconds "$timeout_value" "stop wait timeout" 3600 || return 2
  integer timeout_seconds="$timeout_value"
  integer deadline
  deadline=$(( $(/bin/date +%s) + timeout_seconds ))

  local now probe_timeout sleep_seconds
  while (( (now = $(/bin/date +%s)) < deadline )); do
    probe_timeout=$(( deadline - now ))
    (( probe_timeout <= HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS )) ||
      probe_timeout="$HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS"
    case "$component" in
      broker)
        hindsight_stack_broker_running "$probe_timeout" || return 0
        ;;
      control)
        hindsight_stack_control_running "$probe_timeout" || return 0
        ;;
      daemon)
        hindsight_stack_daemon_running "$probe_timeout" || return 0
        ;;
      ui)
        hindsight_stack_ui_running "$probe_timeout" || return 0
        ;;
      *)
        print -ru2 -- "hindsight-embed-stack: unknown component: ${component}"
        return 2
        ;;
    esac
    now="$(/bin/date +%s)"
    sleep_seconds=$(( deadline - now ))
    (( sleep_seconds > 0 )) || break
    (( sleep_seconds <= 2 )) || sleep_seconds=2
    sleep "$sleep_seconds"
  done

  return 1
}

hindsight_stack_reconcile_control() {
  emulate -L zsh

  if hindsight_stack_control_status; then
    return 0
  fi

  if ! hindsight_stack_can_start control; then
    return 1
  fi

  hindsight_stack_mark_start control
  hindsight_stack_log "control is not healthy; starting"
  if ! hindsight_stack_control_start; then
    hindsight_stack_log "control start command failed"
    return 1
  fi
  hindsight_stack_wait_control
}

hindsight_stack_reconcile_broker() {
  emulate -L zsh

  if hindsight_stack_broker_status; then
    hindsight_stack_broker_identity_matches && return 0
    hindsight_stack_log "broker is healthy but its process identity is not managed; refusing replacement"
    return 1
  fi
  if ! hindsight_stack_can_start broker; then
    return 1
  fi
  hindsight_stack_broker_terminate_recorded || return 1
  hindsight_stack_broker_remove_stale_socket || return 1
  hindsight_stack_mark_start broker
  hindsight_stack_log "broker is not healthy; starting"
  if ! hindsight_stack_broker_start; then
    hindsight_stack_log "broker start command failed"
    return 1
  fi
  return 0
}

hindsight_stack_reconcile_daemon() {
  emulate -L zsh

  local component="daemon:${HINDSIGHT_EMBED_PROFILE}"
  local desired
  desired="$(hindsight_stack_desired_state daemon)" || {
    hindsight_stack_log "daemon desired state is unavailable for ${HINDSIGHT_EMBED_PROFILE}"
    return 1
  }
  [[ "$desired" == running ]] || return 0

  if hindsight_stack_daemon_status; then
    return 0
  fi

  if ! hindsight_stack_can_start "$component"; then
    return 1
  fi

  hindsight_stack_mark_start "$component"
  hindsight_stack_log "daemon is not healthy; starting ${HINDSIGHT_EMBED_PROFILE}"
  if ! hindsight_stack_daemon_start; then
    hindsight_stack_log "daemon start command failed"
    return 1
  fi
  hindsight_stack_wait_daemon
}

hindsight_stack_reconcile_ui() {
  emulate -L zsh

  local component="ui:${HINDSIGHT_EMBED_PROFILE}"
  local desired
  desired="$(hindsight_stack_desired_state ui)" || {
    hindsight_stack_log "UI desired state is unavailable for ${HINDSIGHT_EMBED_PROFILE}"
    return 1
  }
  [[ "$desired" == running ]] || return 0

  if hindsight_stack_ui_status; then
    return 0
  fi

  if ! hindsight_stack_can_start "$component"; then
    return 1
  fi

  hindsight_stack_mark_start "$component"
  hindsight_stack_log "ui is not healthy; starting ${HINDSIGHT_EMBED_PROFILE}"
  if ! hindsight_stack_ui_start; then
    hindsight_stack_log "ui start command failed"
    return 1
  fi
  hindsight_stack_wait_ui
}

hindsight_stack_reconcile_profile() {
  emulate -L zsh
  local ok=0 daemon_desired ui_desired
  hindsight_stack_reconcile_sidecars || ok=1
  daemon_desired="$(hindsight_stack_desired_state daemon)" || return 1
  ui_desired="$(hindsight_stack_desired_state ui)" || return 1
  if [[ "$daemon_desired" == stopped && "$ui_desired" == running ]]; then
    hindsight_stack_log "refusing inconsistent desired state: UI requires daemon for ${HINDSIGHT_EMBED_PROFILE}"
    return 1
  fi

  # Enforce intentional stops in dependency order. A supervisor reconciliation
  # must not merely refrain from starting a component that is still running.
  if [[ "$ui_desired" == stopped ]] && hindsight_stack_ui_running; then
    hindsight_stack_log "UI is running despite stopped intent; stopping ${HINDSIGHT_EMBED_PROFILE}"
    hindsight_stack_ui_stop || ok=1
    hindsight_stack_wait_stopped_for ui "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || ok=1
  fi
  if [[ "$daemon_desired" == stopped ]] && hindsight_stack_daemon_running; then
    hindsight_stack_log "daemon is running despite stopped intent; stopping ${HINDSIGHT_EMBED_PROFILE}"
    hindsight_stack_daemon_stop || ok=1
    hindsight_stack_wait_stopped_for daemon "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || ok=1
  fi
  hindsight_stack_reconcile_daemon || ok=1
  if [[ "$ui_desired" == running ]]; then
    if hindsight_stack_daemon_status; then
      hindsight_stack_reconcile_ui || ok=1
    else
      hindsight_stack_log "UI requires a healthy daemon for ${HINDSIGHT_EMBED_PROFILE}"
      ok=1
    fi
  fi
  return "$ok"
}

hindsight_stack_reconcile_once() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_preflight_runtime_credentials || return 1
  hindsight_stack_require_current_user || return 1
  hindsight_stack_require_tools || return 1
  hindsight_stack_validate_fleet || return 1
  hindsight_stack_initialize_desired_state || return 1

  local ok=0

  if hindsight_stack_runtime_active; then
    # An active broker verifies the selected Hindsight runtime before it opens
    # its socket. Start the data plane first so that gate is meaningful.
    hindsight_stack_reconcile_control || ok=1
    hindsight_stack_for_each_profile hindsight_stack_reconcile_profile "" || ok=1
    hindsight_stack_reconcile_broker || ok=1
  else
    hindsight_stack_reconcile_broker || ok=1
    hindsight_stack_reconcile_control || ok=1
    hindsight_stack_for_each_profile hindsight_stack_reconcile_profile "" || ok=1
  fi

  return "$ok"
}

hindsight_stack_start_profile() {
  emulate -L zsh
  local ok=0 daemon_desired ui_desired
  hindsight_stack_reconcile_sidecars || ok=1
  daemon_desired="$(hindsight_stack_desired_state daemon)" || return 1
  ui_desired="$(hindsight_stack_desired_state ui)" || return 1
  if [[ "$daemon_desired" == stopped && "$ui_desired" == running ]]; then
    hindsight_stack_log "refusing inconsistent desired state: UI requires daemon for ${HINDSIGHT_EMBED_PROFILE}"
    return 1
  fi
  if [[ "$daemon_desired" == stopped ]]; then
    :
  elif hindsight_stack_daemon_status; then
    hindsight_stack_wait_daemon || ok=1
  elif hindsight_stack_daemon_start; then
    hindsight_stack_wait_daemon || ok=1
  else
    ok=1
  fi
  if [[ "$ui_desired" == stopped ]]; then
    :
  elif hindsight_stack_daemon_status; then
    if hindsight_stack_ui_status; then
      hindsight_stack_wait_ui || ok=1
    elif hindsight_stack_ui_start; then
      hindsight_stack_wait_ui || ok=1
    else
      ok=1
    fi
  else
    hindsight_stack_log "UI requires a healthy daemon for ${HINDSIGHT_EMBED_PROFILE}"
    ok=1
  fi
  return "$ok"
}

hindsight_stack_start_broker_dependency() {
  emulate -L zsh
  if hindsight_stack_broker_status; then
    hindsight_stack_broker_identity_matches || {
      print -ru2 -- "hindsight-embed-stack: refusing to replace a healthy broker with unmanaged process identity"
      return 1
    }
    hindsight_stack_wait_broker
  else
    hindsight_stack_broker_start
  fi
}

hindsight_stack_start_control_dependency() {
  emulate -L zsh
  if hindsight_stack_control_status; then
    hindsight_stack_wait_control
  else
    hindsight_stack_control_start || return 1
    hindsight_stack_wait_control
  fi
}

hindsight_stack_start_all() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_preflight_runtime_credentials || return 1
  hindsight_stack_require_current_user || return 1
  hindsight_stack_require_tools || return 1
  hindsight_stack_require_runtime_helpers || return 1
  hindsight_stack_validate_fleet || return 1
  hindsight_stack_initialize_desired_state || return 1

  if hindsight_stack_runtime_active; then
    hindsight_stack_start_control_dependency || return 1
    hindsight_stack_for_each_profile hindsight_stack_start_profile "" || return 1
    hindsight_stack_start_broker_dependency
  else
    hindsight_stack_start_broker_dependency || return 1
    hindsight_stack_start_control_dependency || return 1
    hindsight_stack_for_each_profile hindsight_stack_start_profile ""
  fi
}

hindsight_stack_wait_profile() {
  emulate -L zsh
  local profile="$1" ok=0 daemon_desired ui_desired
  print -r -- "waiting for sidecars for profile ${profile} (up to ${HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS}s)"
  hindsight_stack_wait_sidecars || ok=1
  daemon_desired="$(hindsight_stack_desired_state daemon "$profile")" || return 1
  ui_desired="$(hindsight_stack_desired_state ui "$profile")" || return 1
  [[ "$daemon_desired" != stopped || "$ui_desired" != running ]] || return 1
  if [[ "$daemon_desired" == running ]]; then
    print -r -- "waiting for daemon profile ${profile} on port ${HINDSIGHT_EMBED_API_PORT} (up to ${HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS}s)"
    hindsight_stack_wait_daemon || ok=1
  fi
  if [[ "$ui_desired" == running ]]; then
    print -r -- "waiting for UI profile ${profile} on port ${HINDSIGHT_EMBED_UI_PORT} (up to ${HINDSIGHT_EMBED_UI_WAIT_SECONDS}s)"
    hindsight_stack_wait_ui || ok=1
  fi
  return "$ok"
}

hindsight_stack_wait_all() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_require_tools || return 1

  print -r -- "waiting for broker at ${HINDSIGHT_MEMORY_BROKER_SOCKET} (up to ${HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS}s)"
  hindsight_stack_wait_broker || return 1

  print -r -- "waiting for control on port ${HINDSIGHT_EMBED_CONTROL_PORT} (up to ${HINDSIGHT_EMBED_CONTROL_WAIT_SECONDS}s)"
  hindsight_stack_wait_control || return 1

  hindsight_stack_for_each_profile hindsight_stack_wait_profile ""
}

hindsight_stack_stop_profile() {
  emulate -L zsh
  local profile="$1" ok=0
  hindsight_stack_stop_profile_services "$profile" || ok=1
  hindsight_stack_wait_stopped_for ui "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || {
    print -ru2 -- "hindsight-embed-stack: residual UI remains for profile ${profile} on port ${HINDSIGHT_EMBED_UI_PORT}"
    ok=1
  }
  hindsight_stack_wait_stopped_for daemon "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || {
    print -ru2 -- "hindsight-embed-stack: residual daemon remains for profile ${profile} on port ${HINDSIGHT_EMBED_API_PORT}"
    ok=1
  }
  hindsight_stack_stop_sidecars || ok=1
  hindsight_stack_wait_sidecars_stopped || {
    print -ru2 -- "hindsight-embed-stack: residual sidecars remain for profile ${profile}"
    ok=1
  }
  return "$ok"
}

hindsight_stack_stop_all() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_require_current_user || return 1
  hindsight_stack_require_tools || return 1
  hindsight_stack_require_runtime_helpers || return 1

  local ok=0
  hindsight_stack_for_each_profile_for_stop hindsight_stack_stop_profile || ok=1
  hindsight_stack_broker_stop || ok=1
  hindsight_stack_wait_stopped_for broker "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || {
    print -ru2 -- "hindsight-embed-stack: residual broker remains at ${HINDSIGHT_MEMORY_BROKER_SOCKET}"
    ok=1
  }
  hindsight_stack_control_stop || ok=1
  hindsight_stack_wait_stopped_for control "$HINDSIGHT_EMBED_STOP_WAIT_SECONDS" || {
    print -ru2 -- "hindsight-embed-stack: residual control remains on port ${HINDSIGHT_EMBED_CONTROL_PORT}"
    ok=1
  }
  return "$ok"
}

hindsight_stack_status_word() {
  emulate -L zsh
  local component="$1"

  case "$component" in
    broker)
      if hindsight_stack_broker_status && hindsight_stack_broker_identity_matches; then
        print -r -- "healthy"
      else
        print -r -- "down"
      fi
      ;;
    control)
      hindsight_stack_control_status && print -r -- "healthy" || print -r -- "down"
      ;;
    daemon)
      if hindsight_stack_daemon_status; then
        print -r -- "healthy"
      else
        local desired
        desired="$(hindsight_stack_desired_state daemon)" || {
          print -r -- "unknown"
          return 1
        }
        if [[ "$desired" == stopped ]] && ! hindsight_stack_daemon_running; then
          print -r -- "stopped"
        else
          print -r -- "down"
        fi
      fi
      ;;
    ui)
      if hindsight_stack_ui_status; then
        print -r -- "healthy"
      else
        local desired
        desired="$(hindsight_stack_desired_state ui)" || {
          print -r -- "unknown"
          return 1
        }
        if [[ "$desired" == stopped ]] && ! hindsight_stack_ui_running; then
          print -r -- "stopped"
        else
          print -r -- "down"
        fi
      fi
      ;;
    *)
      print -r -- "unknown"
      return 2
      ;;
  esac
}

hindsight_stack_status_profile() {
  emulate -L zsh
  local profile="$1" sidecar
  [[ -z "$requested" || "$requested" == "$profile" ]] || return 0
  daemon_health="$(hindsight_stack_status_word daemon)" || fleet_health=degraded
  [[ "$daemon_health" == healthy || "$daemon_health" == stopped ]] || fleet_health=degraded
  ui_health="$(hindsight_stack_status_word ui)" || fleet_health=degraded
  [[ "$ui_health" == healthy || "$ui_health" == stopped ]] || fleet_health=degraded
  sidecar_records=()
  local -a sidecars
  sidecars=("${(@f)$(hindsight_stack_sidecar_names)}") || {
    fleet_health=degraded
    return 1
  }
  sidecars=("${(@)sidecars:#}")
  for sidecar in "${sidecars[@]}"; do
    if hindsight_stack_sidecar_status "$sidecar"; then
      sidecar_health=healthy
    else
      sidecar_health=down
      fleet_health=degraded
    fi
    sidecar_records+=("${sidecar}=${sidecar_health}@$(hindsight_stack_sidecar_port "$sidecar")")
  done
  if (( ${#sidecar_records} == 0 )); then
    sidecar_health=none
  else
    sidecar_health="${(j:,:)sidecar_records}"
  fi
  profile_records+=("profile ${profile}: slot=${HINDSIGHT_EMBED_PROFILE_SLOT} api=${daemon_health}@${HINDSIGHT_EMBED_API_PORT} ui=${ui_health}@${HINDSIGHT_EMBED_UI_PORT} sidecars=${sidecar_health}")
}

hindsight_stack_status_report() {
  emulate -L zsh
  hindsight_stack_load_config || return 1
  hindsight_stack_require_tools || return 1
  hindsight_stack_push_profile_state
  hindsight_stack_select_profile "$HINDSIGHT_EMBED_PRIMARY_PROFILE" || {
    hindsight_stack_pop_profile_state
    return 1
  }
  print -r -- "broker: $(hindsight_stack_status_word broker) (${HINDSIGHT_MEMORY_BROKER_SOCKET})"
  print -r -- "control: $(hindsight_stack_status_word control) ($(hindsight_stack_http_url "$HINDSIGHT_EMBED_CONTROL_HOSTNAME" "$HINDSIGHT_EMBED_CONTROL_PORT"))"
  local primary_daemon_health primary_ui_health
  primary_daemon_health="$(hindsight_stack_status_word daemon)"
  primary_ui_health="$(hindsight_stack_status_word ui)"
  print -r -- "daemon: ${primary_daemon_health} (${HINDSIGHT_EMBED_PROFILE}, $(hindsight_stack_http_url 127.0.0.1 "$HINDSIGHT_EMBED_API_PORT"))"
  print -r -- "ui: ${primary_ui_health} (${HINDSIGHT_EMBED_PROFILE}, $(hindsight_stack_ui_url))"
  hindsight_stack_pop_profile_state

  local requested="${1:-}"
  local -a profiles sidecar_records profile_records
  profiles=("${(@f)$(hindsight_stack_enabled_profiles)}") || return 1
  if [[ -n "$requested" ]]; then
    hindsight_stack_profile_enabled "$requested" || {
      print -ru2 -- "hindsight-embed-stack: profile '${requested}' is not enabled"
      return 1
    }
  fi

  local fleet_health=healthy daemon_health ui_health sidecar_health
  hindsight_stack_broker_status && hindsight_stack_broker_identity_matches || fleet_health=degraded
  hindsight_stack_control_status || fleet_health=degraded
  local profile_count="${#profiles}"
  [[ -z "$requested" ]] || profile_count=1
  local profile_label=profiles
  (( profile_count == 1 )) && profile_label=profile
  hindsight_stack_for_each_profile hindsight_stack_status_profile "$requested" || fleet_health=degraded
  print -r -- "fleet: ${fleet_health} (${profile_count} enabled ${profile_label})"
  print -rl -- "${profile_records[@]}"
}
