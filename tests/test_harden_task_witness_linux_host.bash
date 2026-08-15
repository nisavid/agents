#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "usage: test_harden_task_witness_linux_host.bash TEST_ROOT UID GID" >&2
  exit 2
fi

script_path="$(realpath -- "${BASH_SOURCE[0]}")"
tests_dir="${script_path%/*}"
repository_root="${tests_dir%/*}"
hardener="$repository_root/scripts/harden_task_witness_linux_host.bash"
test_root=$1
qualification_uid=$2
qualification_gid=$3
expected_attestation="task-witness Linux no-capability probe: uid=$qualification_uid gid=$qualification_gid groups=none CapInh=0000000000000000 CapPrm=0000000000000000 CapEff=0000000000000000 CapBnd=0000000000000000 CapAmb=0000000000000000 NoNewPrivs=1"

[[ $EUID -eq 0 ]]
[[ $test_root == /* ]]
[[ ! -e $test_root && ! -L $test_root ]]
[[ -f $hardener && ! -L $hardener ]]
/usr/bin/mkdir "$test_root"
/usr/bin/chown root:root "$test_root"
/usr/bin/chmod 0755 "$test_root"

prepare_root() {
  local root=$1
  /usr/bin/mkdir "$root"
  /usr/bin/chown root:root "$root"
  /usr/bin/chmod 0755 "$root"
  /usr/bin/mkdir "$root/opt"
  /usr/bin/chown root:root "$root/opt"
  /usr/bin/chmod 0777 "$root/opt"
}

run_hardener() {
  local root=$1
  /usr/bin/env -i \
    PATH=/usr/bin:/bin \
    RUNNER_ENVIRONMENT=github-hosted \
    RUNNER_OS=Linux \
    RUNNER_ARCH=X64 \
    ImageOS=ubuntu24 \
    /usr/bin/bash \
      "$hardener" \
      harden \
      "$root" \
      "$qualification_uid" \
      "$qualification_gid"
}

run_qualifier() {
  local root=$1
  local behavior_test=$2
  local run_id=$3
  local run_attempt=$4
  /usr/bin/env -i \
    PATH=/usr/bin:/bin \
    RUNNER_ENVIRONMENT=github-hosted \
    RUNNER_OS=Linux \
    RUNNER_ARCH=X64 \
    ImageOS=ubuntu24 \
    /usr/bin/bash \
      "$hardener" \
      qualify-host \
      "$root" \
      "$behavior_test" \
      "$run_id" \
      "$run_attempt" \
      "$qualification_uid" \
      "$qualification_gid"
}

positive_root="$test_root/positive-root"
prepare_root "$positive_root"
printf 'unrelated\n' > "$positive_root/opt/unrelated"
/usr/bin/chown \
  "$qualification_uid":"$qualification_gid" \
  "$positive_root/opt/unrelated"
/usr/bin/chmod 0666 "$positive_root/opt/unrelated"
positive_attestation=$(run_hardener "$positive_root")
[[ $positive_attestation == "$expected_attestation" ]]
[[ $(
  /usr/bin/stat --format='%u:%g:%a' \
    "$positive_root" \
    "$positive_root/opt" \
    "$positive_root/opt/task-witness" \
    "$positive_root/opt/task-witness/bootstrap"
) == $'0:0:755\n0:0:755\n0:0:755\n0:0:755' ]]
[[ $(
  /usr/bin/stat --format='%u:%g:%a' "$positive_root/opt/unrelated"
) == "$qualification_uid:$qualification_gid:666" ]]
[[ $(< "$positive_root/opt/unrelated") == unrelated ]]
for directory in \
  "$positive_root" \
  "$positive_root/opt" \
  "$positive_root/opt/task-witness" \
  "$positive_root/opt/task-witness/bootstrap"
do
  probe="${directory%/}/.task-witness-no-cap-write-probe"
  [[ ! -e $probe && ! -L $probe ]]
done

boundary_root="$test_root/boundary-root"
prepare_root "$boundary_root"
set +e
/usr/bin/env -i \
  PATH=/usr/bin:/bin \
  RUNNER_ENVIRONMENT=github-hosted \
  RUNNER_OS=Linux \
  RUNNER_ARCH=X64 \
  ImageOS=ubuntu22 \
  /usr/bin/bash \
    "$hardener" \
    harden \
    "$boundary_root" \
    "$qualification_uid" \
    "$qualification_gid" \
    2> "$test_root/boundary.stderr"
boundary_status=$?
set -e
[[ $boundary_status -ne 0 ]]
[[ $(< "$test_root/boundary.stderr") == \
  'task-witness Linux host hardening: runner image is outside the qualified boundary' ]]
[[ $(/usr/bin/stat --format='%u:%g:%a' "$boundary_root/opt") == '0:0:777' ]]
[[ ! -e $boundary_root/opt/task-witness ]]

symlink_root="$test_root/symlink-root"
/usr/bin/mkdir "$symlink_root"
/usr/bin/chown root:root "$symlink_root"
/usr/bin/chmod 0755 "$symlink_root"
symlink_target="$test_root/symlink-target"
/usr/bin/mkdir "$symlink_target"
/usr/bin/chown root:root "$symlink_target"
/usr/bin/chmod 0777 "$symlink_target"
/usr/bin/ln -s "$symlink_target" "$symlink_root/opt"
set +e
run_hardener "$symlink_root" 2> "$test_root/symlink.stderr"
symlink_status=$?
set -e
[[ $symlink_status -ne 0 ]]
[[ $(< "$test_root/symlink.stderr") == \
  'task-witness Linux host hardening: directory disposition is unsafe' ]]
[[ $(/usr/bin/stat --format='%u:%g:%a' "$symlink_target") == '0:0:777' ]]
[[ ! -e $symlink_target/task-witness ]]

probe_collision_root="$test_root/probe-collision-root"
prepare_root "$probe_collision_root"
probe_collision="$probe_collision_root/opt/.task-witness-no-cap-write-probe"
/usr/bin/ln -s "$probe_collision_root/opt/missing" "$probe_collision"
set +e
probe_collision_attestation=$(run_hardener "$probe_collision_root")
probe_collision_status=$?
set -e
[[ $probe_collision_status -ne 0 ]]
[[ $probe_collision_attestation == "$expected_attestation" ]]
[[ -L $probe_collision && ! -e $probe_collision ]]

writable="$test_root/writable"
/usr/bin/mkdir "$writable"
/usr/bin/chown root:root "$writable"
/usr/bin/chmod 0777 "$writable"
set +e
/usr/bin/bash \
  "$hardener" \
  probe-directories \
  "$qualification_uid" \
  "$qualification_gid" \
  "$writable" \
  > "$test_root/writable.stdout"
writable_status=$?
set -e
[[ $writable_status -eq 92 ]]
[[ $(< "$test_root/writable.stdout") == "$expected_attestation" ]]
[[ ! -e $writable/.task-witness-no-cap-write-probe ]]

inaccessible_parent="$test_root/inaccessible-parent"
inaccessible="$inaccessible_parent/directory"
/usr/bin/mkdir "$inaccessible_parent"
/usr/bin/chown root:root "$inaccessible_parent"
/usr/bin/chmod 0700 "$inaccessible_parent"
/usr/bin/mkdir "$inaccessible"
/usr/bin/chown root:root "$inaccessible"
/usr/bin/chmod 0755 "$inaccessible"
set +e
/usr/bin/bash \
  "$hardener" \
  probe-directories \
  "$qualification_uid" \
  "$qualification_gid" \
  "$inaccessible" \
  > "$test_root/inaccessible.stdout"
inaccessible_status=$?
set -e
[[ $inaccessible_status -eq 1 ]]
[[ $(< "$test_root/inaccessible.stdout") == "$expected_attestation" ]]
[[ ! -e $inaccessible/.task-witness-no-cap-write-probe ]]
[[ ! -L $inaccessible/.task-witness-no-cap-write-probe ]]

failing_qualification_root="$test_root/failing-qualification-root"
prepare_root "$failing_qualification_root"
failing_behavior_test="$test_root/failing-behavior-test.bash"
/usr/bin/printf '%s\n' \
  '#!/usr/bin/env bash' \
  'exit 17' \
  > "$failing_behavior_test"
/usr/bin/chown root:root "$failing_behavior_test"
/usr/bin/chmod 0555 "$failing_behavior_test"
set +e
run_qualifier \
  "$failing_qualification_root" \
  "$failing_behavior_test" \
  987654321 \
  1 \
  > "$test_root/failing-qualification.stdout"
failing_qualification_status=$?
set -e
[[ $failing_qualification_status -eq 17 ]]
[[ ! -s $test_root/failing-qualification.stdout ]]
[[ $(
  /usr/bin/stat --format='%u:%g:%a' "$failing_qualification_root/opt"
) == '0:0:777' ]]
[[ ! -e $failing_qualification_root/opt/task-witness ]]
[[ ! -e /tmp/task-witness-host-hardening-test-987654321-1 ]]
[[ ! -L /tmp/task-witness-host-hardening-test-987654321-1 ]]

successful_qualification_root="$test_root/successful-qualification-root"
prepare_root "$successful_qualification_root"
successful_behavior_test="$test_root/successful-behavior-test.bash"
/usr/bin/printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  '(( $# == 3 ))' \
  'test ! -e "$1"' \
  'test ! -L "$1"' \
  '/usr/bin/mkdir "$1"' \
  '/usr/bin/chown root:root "$1"' \
  '/usr/bin/chmod 0755 "$1"' \
  '/usr/bin/printf "%s\n" "qualified behavior stub: OK"' \
  > "$successful_behavior_test"
/usr/bin/chown root:root "$successful_behavior_test"
/usr/bin/chmod 0555 "$successful_behavior_test"

collision_qualification_root="$test_root/collision-qualification-root"
prepare_root "$collision_qualification_root"
collision_sandbox=/tmp/task-witness-host-hardening-test-987654321-3
/usr/bin/ln -s /tmp/task-witness-host-hardening-missing "$collision_sandbox"
set +e
run_qualifier \
  "$collision_qualification_root" \
  "$successful_behavior_test" \
  987654321 \
  3 \
  2> "$test_root/collision-qualification.stderr"
collision_qualification_status=$?
set -e
[[ $collision_qualification_status -eq 1 ]]
[[ $(< "$test_root/collision-qualification.stderr") == \
  'task-witness Linux host hardening: behavior test root already exists' ]]
[[ $(
  /usr/bin/stat --format='%u:%g:%a' "$collision_qualification_root/opt"
) == '0:0:777' ]]
[[ ! -e $collision_qualification_root/opt/task-witness ]]
[[ -L $collision_sandbox && ! -e $collision_sandbox ]]
/usr/bin/unlink "$collision_sandbox"

successful_qualification_attestation=$(
  run_qualifier \
    "$successful_qualification_root" \
    "$successful_behavior_test" \
    987654321 \
    2
)
[[ $successful_qualification_attestation == \
  $'qualified behavior stub: OK\n'"$expected_attestation" ]]
[[ $(
  /usr/bin/stat --format='%u:%g:%a' \
    "$successful_qualification_root" \
    "$successful_qualification_root/opt" \
    "$successful_qualification_root/opt/task-witness" \
    "$successful_qualification_root/opt/task-witness/bootstrap"
) == $'0:0:755\n0:0:755\n0:0:755\n0:0:755' ]]
successful_sandbox=/tmp/task-witness-host-hardening-test-987654321-2
[[ $(/usr/bin/stat --format='%u:%g:%a' "$successful_sandbox") == '0:0:755' ]]
[[ $(/usr/bin/realpath -- "$successful_sandbox") == "$successful_sandbox" ]]
/usr/bin/rmdir "$successful_sandbox"

echo "task-witness Linux host hardening behavior: OK"
