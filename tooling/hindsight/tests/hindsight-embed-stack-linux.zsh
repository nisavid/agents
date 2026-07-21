#!/usr/bin/env zsh
set -euo pipefail
unsetopt BG_NICE

if [[ "$(uname -s)" != Linux ]]; then
  print -r -- "hindsight-embed-stack-linux: SKIP (Linux required)"
  exit 0
fi

repo_dir="${0:A:h:h}"
stack_lib="$repo_dir/lib/hindsight-embed-stack.zsh"
tmp_dir="$(mktemp -d)"
state_dir="$tmp_dir/stack-state"
memory_state_dir="$tmp_dir/memory-state"
broker_socket="$memory_state_dir/broker.sock"
broker_ready="$tmp_dir/broker-ready"
broker_log="$tmp_dir/broker.log"

cleanup() {
  emulate -L zsh
  unsetopt ERR_EXIT
  if (( $+functions[hindsight_stack_broker_read_process_record] )) &&
    hindsight_stack_broker_read_process_record >/dev/null 2>&1 &&
    hindsight_stack_broker_identity_matches >/dev/null 2>&1; then
    kill -KILL "$HINDSIGHT_STACK_BROKER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT

fake_memory_cli="$tmp_dir/hindsight-memory"
cat >"$fake_memory_cli" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import sys
import time

arguments = sys.argv[1:]
operation = ""
for index, argument in enumerate(arguments[:-1]):
    if argument == "broker":
        operation = arguments[index + 1]
        break
if not operation:
    raise SystemExit(2)

record = Path(os.environ["HINDSIGHT_TEST_STACK_STATE"]) / "broker-process.identity"
with Path(os.environ["HINDSIGHT_TEST_BROKER_LOG"]).open("a", encoding="utf-8") as log:
    log.write(f"{operation}:{' '.join(arguments)}\n")

if operation == "serve":
    Path(os.environ["HINDSIGHT_TEST_BROKER_READY"]).write_text("ready\n", encoding="utf-8")
    signal.signal(signal.SIGTERM, lambda _signal, _frame: sys.exit(0))
    while True:
        time.sleep(1)
elif operation == "status":
    if not record.is_file():
        raise SystemExit(1)
    pid = int(record.read_text(encoding="utf-8").splitlines()[0])
    os.kill(pid, 0)
elif operation == "stop":
    if not record.is_file():
        raise SystemExit(0)
    pid = int(record.read_text(encoding="utf-8").splitlines()[0])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        raise SystemExit(0)
    for _attempt in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise SystemExit(0)
        time.sleep(0.02)
    raise SystemExit(1)
else:
    raise SystemExit(2)
PY
chmod 700 "$fake_memory_cli"

export HINDSIGHT_EMBED_STATE_DIR="$state_dir"
export HINDSIGHT_MEMORY_STATE_DIR="$memory_state_dir"
export HINDSIGHT_MEMORY_BROKER_SOCKET="$broker_socket"
export HINDSIGHT_MEMORY_CLI="$fake_memory_cli"
export HINDSIGHT_EMBED_PYTHON="$(command -v python3)"
export HINDSIGHT_EMBED_FLEET_PROFILES=engineering
export HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=5
export HINDSIGHT_EMBED_STOP_WAIT_SECONDS=2
export HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS=2
export HINDSIGHT_TEST_STACK_STATE="$state_dir"
export HINDSIGHT_TEST_BROKER_READY="$broker_ready"
export HINDSIGHT_TEST_BROKER_LOG="$broker_log"
mkdir -m 700 "$state_dir" "$memory_state_dir"

source "$stack_lib"
hindsight_stack_load_config() { return 0 }
hindsight_stack_preflight_runtime_credentials() { return 0 }
hindsight_stack_prepare_lifecycle_lock() {
  local lock_file="$state_dir/.lifecycle.lock"
  : >>"$lock_file"
  chmod 600 "$lock_file"
  print -r -- "$lock_file"
}

if hindsight_stack_broker_process_identity 999999999 >/dev/null 2>&1; then
  print -ru2 -- "broker identity accepted an absent Linux process"
  exit 1
fi

if ! hindsight_stack_broker_start; then
  print -ru2 -- "broker start failed during Linux lifecycle coverage"
  [[ ! -r "$broker_log" ]] || cat "$broker_log" >&2
  exit 1
fi
[[ -s "$broker_ready" ]]
hindsight_stack_broker_read_process_record
broker_pid="$HINDSIGHT_STACK_BROKER_PID"
broker_identity="$HINDSIGHT_STACK_BROKER_IDENTITY"

boot_id="$(</proc/sys/kernel/random/boot_id)"
process_stat="$(<"/proc/${broker_pid}/stat")"
process_stat_tail="${process_stat##*\) }"
process_stat_fields=("${(@s: :)process_stat_tail}")
kernel_start_time="$process_stat_fields[20]"
[[ "$broker_identity" == "${boot_id}:${kernel_start_time}" ]] || {
  print -ru2 -- "broker identity is not bound to the Linux boot and process start"
  exit 1
}
hindsight_stack_broker_identity_matches
hindsight_stack_broker_status
[[ "$(hindsight_stack_status_word broker)" == healthy ]]

hindsight_stack_broker_write_process_record "$broker_pid" changed-identity
if hindsight_stack_broker_identity_matches; then
  print -ru2 -- "broker status accepted a changed Linux process identity"
  exit 1
fi
[[ "$(hindsight_stack_status_word broker)" == down ]]
hindsight_stack_broker_write_process_record "$broker_pid" "$broker_identity"

hindsight_stack_broker_stop
[[ ! -e "$state_dir/broker-process.identity" ]] || {
  print -ru2 -- "broker stop retained its process identity record"
  exit 1
}
if kill -0 "$broker_pid" >/dev/null 2>&1; then
  print -ru2 -- "broker stop left the managed Linux process running"
  exit 1
fi
if hindsight_stack_broker_status >/dev/null 2>&1; then
  print -ru2 -- "broker status accepted the stopped Linux process"
  exit 1
fi

print -r -- "hindsight-embed-stack-linux: PASS"
