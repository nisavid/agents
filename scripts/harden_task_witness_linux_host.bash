#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "task-witness Linux host hardening: $1" >&2
  exit 1
}

require_root() {
  [[ $EUID -eq 0 ]] || fail 'root authority is required'
}

require_identity() {
  local value=$1
  local label=$2
  [[ $value =~ ^[1-9][0-9]*$ ]] || fail "$label is invalid"
}

require_real_directory() {
  local path=$1
  local resolved
  [[ $path == /* ]] || fail 'directory path is not absolute'
  [[ -d $path && ! -L $path ]] || fail 'directory disposition is unsafe'
  resolved=$(/usr/bin/realpath -- "$path")
  [[ $resolved == "$path" ]] || fail 'directory path is not canonical'
}

probe_directories() {
  local qualification_uid=$1
  local qualification_gid=$2
  shift 2
  (( $# > 0 )) || fail 'no probe directories were provided'
  require_identity "$qualification_uid" 'qualification uid'
  require_identity "$qualification_gid" 'qualification gid'

  local directory
  for directory in "$@"; do
    require_real_directory "$directory"
  done

  /usr/bin/setpriv \
    --reuid="$qualification_uid" \
    --regid="$qualification_gid" \
    --clear-groups \
    --inh-caps=-all \
    --ambient-caps=-all \
    --bounding-set=-all \
    --no-new-privs \
    --reset-env \
    /usr/bin/env -i \
      PATH=/usr/bin:/bin \
      /usr/bin/sh -eu -c '
        expected_uid=$1
        expected_gid=$2
        shift 2
        uid_seen=0
        gid_seen=0
        groups_seen=0
        cap_inh=missing
        cap_prm=missing
        cap_eff=missing
        cap_bnd=missing
        cap_amb=missing
        no_new_privs=missing
        require_four_equal() {
          expected=$1
          shift
          /usr/bin/test "$#" -eq 4
          for observed in "$@"; do
            /usr/bin/test "$observed" = "$expected"
          done
        }
        while read -r field value remainder; do
          case $field in
            Uid:)
              require_four_equal "$expected_uid" "$value" $remainder
              uid_seen=1
              ;;
            Gid:)
              require_four_equal "$expected_gid" "$value" $remainder
              gid_seen=1
              ;;
            Groups:)
              /usr/bin/test -z "$value$remainder"
              groups_seen=1
              ;;
            CapInh:) cap_inh=$value ;;
            CapPrm:) cap_prm=$value ;;
            CapEff:) cap_eff=$value ;;
            CapBnd:) cap_bnd=$value ;;
            CapAmb:) cap_amb=$value ;;
            NoNewPrivs:) no_new_privs=$value ;;
          esac
        done < /proc/self/status
        /usr/bin/test "$uid_seen" -eq 1
        /usr/bin/test "$gid_seen" -eq 1
        /usr/bin/test "$groups_seen" -eq 1
        /usr/bin/test "$cap_inh" = 0000000000000000
        /usr/bin/test "$cap_prm" = 0000000000000000
        /usr/bin/test "$cap_eff" = 0000000000000000
        /usr/bin/test "$cap_bnd" = 0000000000000000
        /usr/bin/test "$cap_amb" = 0000000000000000
        /usr/bin/test "$no_new_privs" = 1
        /usr/bin/printf "%s\n" \
          "task-witness Linux no-capability probe: uid=$expected_uid gid=$expected_gid groups=none CapInh=$cap_inh CapPrm=$cap_prm CapEff=$cap_eff CapBnd=$cap_bnd CapAmb=$cap_amb NoNewPrivs=$no_new_privs"
        for directory in "$@"; do
          probe="${directory%/}/.task-witness-no-cap-write-probe"
          /usr/bin/test ! -e "$probe"
          /usr/bin/test ! -L "$probe"
          if /usr/bin/touch "$probe" 2>/dev/null; then
            /usr/bin/rm -f "$probe"
            exit 92
          fi
          /usr/bin/test ! -e "$probe"
          /usr/bin/test ! -L "$probe"
          /usr/bin/test ! -w "$directory"
        done
      ' \
      task-witness-host-hardening-probe \
      "$qualification_uid" \
      "$qualification_gid" \
      "$@"
}

require_hosted_boundary() {
  [[ ${RUNNER_ENVIRONMENT-} == github-hosted ]] || \
    fail 'runner environment is outside the qualified boundary'
  [[ ${RUNNER_OS-} == Linux ]] || \
    fail 'runner operating system is outside the qualified boundary'
  [[ ${RUNNER_ARCH-} == X64 ]] || \
    fail 'runner architecture is outside the qualified boundary'
  [[ ${ImageOS-} == ubuntu24 ]] || \
    fail 'runner image is outside the qualified boundary'
}

harden_host() {
  local system_root=$1
  local qualification_uid=$2
  local qualification_gid=$3
  local root_prefix
  local opt_root
  local task_root
  local bootstrap_root
  local observed

  require_hosted_boundary
  require_identity "$qualification_uid" 'qualification uid'
  require_identity "$qualification_gid" 'qualification gid'
  require_real_directory "$system_root"
  [[ $(/usr/bin/stat --format='%u:%g:%a' "$system_root") == '0:0:755' ]] || \
    fail 'system root disposition is unsafe'

  root_prefix=${system_root%/}
  opt_root="$root_prefix/opt"
  task_root="$opt_root/task-witness"
  bootstrap_root="$task_root/bootstrap"
  require_real_directory "$opt_root"
  [[ ! -e $task_root && ! -L $task_root ]] || \
    fail 'task root already exists'

  /usr/bin/chown root:root "$opt_root"
  /usr/bin/chmod 0755 "$opt_root"
  /usr/bin/mkdir "$task_root"
  /usr/bin/mkdir "$bootstrap_root"
  /usr/bin/chown root:root "$task_root" "$bootstrap_root"
  /usr/bin/chmod 0755 "$task_root" "$bootstrap_root"

  observed=$(
    /usr/bin/stat --format='%u:%g:%a' \
      "$system_root" \
      "$opt_root" \
      "$task_root" \
      "$bootstrap_root"
  )
  [[ $observed == $'0:0:755\n0:0:755\n0:0:755\n0:0:755' ]] || \
    fail 'protected root disposition is unsafe'

  probe_directories \
    "$qualification_uid" \
    "$qualification_gid" \
    "$system_root" \
    "$opt_root" \
    "$task_root" \
    "$bootstrap_root"
}

main() {
  require_root
  (( $# > 0 )) || fail 'a command is required'
  local command=$1
  shift
  case $command in
    harden)
      (( $# == 3 )) || fail 'harden requires ROOT UID GID'
      harden_host "$@"
      ;;
    probe-directories)
      (( $# >= 3 )) || fail 'probe-directories requires UID GID DIRECTORY...'
      probe_directories "$@"
      ;;
    *)
      fail 'command is invalid'
      ;;
  esac
}

main "$@"
