#!/usr/bin/env zsh
set -euo pipefail

repo_dir="${0:A:h:h}"
tmp_dir="$(mktemp -d)"
tmp_dir="${tmp_dir:A}"
cleanup_test_fixtures() {
  emulate -L zsh
  unsetopt ERR_EXIT
  local pid attempt
  local -a pids=()
  for pid in "${cleanup_lock_holder:-}" "${cleanup_lock_observer:-}"; do
    [[ "$pid" == <-> ]] || continue
    pids+=("$pid")
    /bin/kill -TERM "$pid" >/dev/null 2>&1 || true
  done
  for attempt in {1..20}; do
    local any_running=0
    for pid in "${pids[@]}"; do
      /bin/kill -0 "$pid" >/dev/null 2>&1 && any_running=1
    done
    (( any_running )) || break
    sleep 0.05
  done
  for pid in "${pids[@]}"; do
    /bin/kill -0 "$pid" >/dev/null 2>&1 &&
      /bin/kill -KILL "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
  done
  /usr/bin/chflags -R nouchg "$tmp_dir" >/dev/null 2>&1 || true
  /bin/chmod -R u+rwX "$tmp_dir" >/dev/null 2>&1 || true
  /bin/rm -rf -- "$tmp_dir"
}
trap cleanup_test_fixtures EXIT

library="$tmp_dir/hindsight-embed-single-bank-cleanup"
/usr/bin/sed '/^main "\$@"$/d' \
  "$repo_dir/bin/hindsight-embed-single-bank-cleanup" > "$library"
source "$library"
source "$repo_dir/lib/hindsight-embed-stack.zsh"
export HINDSIGHT_EMBED_PYTHON=/usr/bin/python3
export HINDSIGHT_CLEANUP_ARCHIVE_TIMEOUT_SECONDS=3600
export HINDSIGHT_CLEANUP_MIGRATION_TIMEOUT_SECONDS=3600

acl_helper_dir="$tmp_dir/acl-helper-dir"
acl_helper="$acl_helper_dir/helper"
mkdir "$acl_helper_dir"
print -r -- '#!/usr/bin/env zsh' >"$acl_helper"
chmod 700 "$acl_helper"
chmod +a 'everyone allow read' "$acl_helper"
if ( validate_trusted_artifact "$acl_helper" "ACL helper" executable ) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an ACL-bearing helper"
  exit 1
fi
chmod -N "$acl_helper"
chmod +a 'everyone allow read' "$acl_helper_dir"
if ( validate_trusted_artifact "$acl_helper" "ACL ancestor helper" executable ) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a helper beneath an ACL-bearing ancestor"
  exit 1
fi
chmod -N "$acl_helper_dir"

encrypt_command="$tmp_dir/encrypt-archive"
cat >"$encrypt_command" <<'EOF'
#!/usr/bin/env zsh
exec /usr/bin/tar -cf "$2" -C "$1" .
EOF
chmod 700 "$encrypt_command"
export HINDSIGHT_CLEANUP_ARCHIVE_ENCRYPT_COMMAND="$encrypt_command"
decrypt_command="$tmp_dir/decrypt-archive"
cat >"$decrypt_command" <<'EOF'
#!/usr/bin/env zsh
mkdir -p "$2"
[[ -z "${HINDSIGHT_TEST_VERIFICATION_DIR:-}" ]] ||
  print -r -- "$2" > "$HINDSIGHT_TEST_VERIFICATION_DIR"
exec /usr/bin/tar -xf "$1" -C "$2"
EOF
chmod 700 "$decrypt_command"
export HINDSIGHT_CLEANUP_ARCHIVE_DECRYPT_COMMAND="$decrypt_command"

mkdir -p "$tmp_dir/real-ancestor"
print -r -- unsafe > "$tmp_dir/real-ancestor/source"
ln -s "$tmp_dir/real-ancestor" "$tmp_dir/symlink-ancestor"
if (
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/symlink-ancestor/source" \
    archive_sources "$tmp_dir/symlink-ancestor.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an archive source through a symlinked ancestor"
  exit 1
fi

mkdir -p "$tmp_dir/nested-symlink-source"
ln -s "$tmp_dir/real-ancestor/source" "$tmp_dir/nested-symlink-source/link"
if (
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/nested-symlink-source" \
    archive_sources "$tmp_dir/nested-symlink.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a symlink inside an archive source"
  exit 1
fi

fifo_source="$tmp_dir/archive-source-fifo"
mkfifo "$fifo_source"
socket_source="$tmp_dir/archive-source-socket"
/usr/bin/python3 - "$socket_source" <<'PY'
import socket
import sys

value = socket.socket(socket.AF_UNIX)
value.bind(sys.argv[1])
value.close()
PY
for special_source in "$fifo_source" "$socket_source" /dev/null; do
  if (
    HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$special_source"
    validate_archive_plan "$tmp_dir/special-dry-run-${special_source:t}.age"
  ) >/dev/null 2>&1; then
    print -ru2 -- "cleanup dry-run accepted a special archive source: ${special_source}"
    exit 1
  fi
done
(archive_source_snapshot "$fifo_source" "$tmp_dir/fifo-copy") >/dev/null 2>&1 &
fifo_copy_pid=$!
for _ in {1..40}; do
  state="$(ps -o state= -p "$fifo_copy_pid" 2>/dev/null || true)"
  [[ -z "$state" || "$state" == Z* ]] && break
  sleep 0.05
done
state="$(ps -o state= -p "$fifo_copy_pid" 2>/dev/null || true)"
if [[ -n "$state" && "$state" != Z* ]]; then
  kill -KILL "$fifo_copy_pid" >/dev/null 2>&1 || true
  wait "$fifo_copy_pid" >/dev/null 2>&1 || true
  print -ru2 -- "cleanup blocked while rejecting a FIFO archive source"
  exit 1
fi
if wait "$fifo_copy_pid"; then
  print -ru2 -- "cleanup accepted a FIFO archive source"
  exit 1
fi

missing_source="$tmp_dir/missing-source"
if (HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$missing_source" archive_sources "$tmp_dir/missing-archive") >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a missing rollback source"
  exit 1
fi

writable_archive_parent="$tmp_dir/writable-archive-parent"
mkdir "$writable_archive_parent"
chmod 777 "$writable_archive_parent"
if (
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/real-ancestor/source" \
    archive_sources "$writable_archive_parent/archive.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a writable archive parent"
  exit 1
fi
chmod 700 "$writable_archive_parent"

mkdir -p "$tmp_dir/overlap-source"
print -r -- rollback > "$tmp_dir/overlap-source/value"
if (
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/overlap-source" \
    archive_sources "$tmp_dir/overlap-source/archive.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an archive parent equal to a canonical source"
  exit 1
fi
if (
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/overlap-source" \
    archive_sources "$tmp_dir/overlap-source/nested/archive.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an archive parent nested under a canonical source"
  exit 1
fi
[[ ! -e "$tmp_dir/overlap-source/nested" ]] || {
  print -ru2 -- "cleanup created a nested archive parent before rejecting source overlap"
  exit 1
}

mkdir -p "$tmp_dir/duplicate-one/shared" "$tmp_dir/duplicate-two/shared"
if (
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/duplicate-one/shared,$tmp_dir/duplicate-two/shared" \
    archive_sources "$tmp_dir/duplicate-archive"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted duplicate archive basenames"
  exit 1
fi

mkdir -p "$tmp_dir/archive-source/directory"
print -r -- rollback-data > "$tmp_dir/archive-source/directory/value"
print -r -- rollback-file > "$tmp_dir/archive-source/file"
readonly_source="$tmp_dir/archive-source/readonly-directory"
readonly_snapshot="$tmp_dir/readonly-directory-snapshot"
mkdir "$readonly_source"
print -r -- immutable > "$readonly_source/value"
chmod 500 "$readonly_source"
archive_source_snapshot "$readonly_source" "$readonly_snapshot"
[[ "$(/usr/bin/stat -f '%Lp' "$readonly_snapshot")" == 500 &&
  "$(<"$readonly_snapshot/value")" == immutable ]] || {
  print -ru2 -- "archive snapshot could not populate and restore a read-only directory mode"
  exit 1
}
chmod 700 "$readonly_source" "$readonly_snapshot"
archive_timeout_calls="$tmp_dir/archive-timeout-calls"
(
  hindsight_stack_run_bounded() {
    print -r -- "$1|${2:t}" >>"$archive_timeout_calls"
    shift
    "$@"
  }
  HINDSIGHT_CLEANUP_ARCHIVE_TIMEOUT_SECONDS=17 \
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/archive-source/file" \
    archive_sources "$tmp_dir/timeout-archive.age" >/dev/null
)
[[ -s "$archive_timeout_calls" ]] || {
  print -ru2 -- "cleanup archive helpers did not use the bounded runner"
  exit 1
}
if rg -v -q '^17\|' "$archive_timeout_calls"; then
  print -ru2 -- "cleanup archive helper used the wrong timeout"
  exit 1
fi
for helper in python3 encrypt-archive decrypt-archive shasum; do
  rg -F -q "|${helper}" "$archive_timeout_calls" || {
    print -ru2 -- "cleanup did not bound archive helper ${helper}"
    exit 1
  }
done
early_staging="$tmp_dir/early-failure-staging"
if (
  mktemp() {
    if [[ "$1" == -d ]]; then
      mkdir "$early_staging"
      print -r -- "$early_staging"
      return 0
    fi
    return 1
  }
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/archive-source/file" \
    archive_sources "$tmp_dir/early-failure.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted failure to reserve encrypted output"
  exit 1
fi
[[ ! -e "$early_staging" ]] || {
  print -ru2 -- "cleanup left plaintext staging after early setup failure"
  exit 1
}
mkdir "$tmp_dir/archive-source/many-files"
for index in {1..160}; do
  print -r -- "$index" > "$tmp_dir/archive-source/many-files/$index"
done
(
  ulimit -n 64
  archive_source_snapshot \
    "$tmp_dir/archive-source/many-files" "$tmp_dir/many-files-copy"
) || {
  print -ru2 -- "cleanup retained one descriptor per directory child"
  exit 1
}
[[ "$(find "$tmp_dir/many-files-copy" -type f | wc -l | tr -d ' ')" == 160 ]] || {
  print -ru2 -- "cleanup did not copy every child under a bounded descriptor limit"
  exit 1
}
print -r -- flagged-rollback > "$tmp_dir/archive-source/flagged-file"
/usr/bin/chflags uchg "$tmp_dir/archive-source/flagged-file"
flagged_output="$tmp_dir/flagged-copy.out"
if archive_source_snapshot \
  "$tmp_dir/archive-source/flagged-file" "$tmp_dir/flagged-copy" \
  >"$flagged_output" 2>&1; then
  /usr/bin/chflags nouchg "$tmp_dir/archive-source/flagged-file"
  print -ru2 -- "cleanup accepted BSD flags that plaintext staging cannot preserve safely"
  exit 1
fi
rg -F -q 'source uses unsupported BSD file flags' "$flagged_output" || {
  /usr/bin/chflags nouchg "$tmp_dir/archive-source/flagged-file"
  print -ru2 -- "cleanup did not explain its BSD file flag rejection"
  exit 1
}
[[ ! -e "$tmp_dir/flagged-copy" ]] || {
  /usr/bin/chflags nouchg "$tmp_dir/archive-source/flagged-file"
  print -ru2 -- "cleanup created plaintext staging before rejecting BSD file flags"
  exit 1
}
/usr/bin/chflags nouchg "$tmp_dir/archive-source/flagged-file"
zero_progress_python="$tmp_dir/zero-progress-python"
mkdir "$zero_progress_python"
cat >"$zero_progress_python/sitecustomize.py" <<'PY'
import os

_write_calls = 0


def zero_then_fail(_descriptor, _data):
    global _write_calls
    _write_calls += 1
    if _write_calls == 1:
        return 0
    raise OSError("write retried after making no progress")


os.write = zero_then_fail
PY
zero_progress_output="$tmp_dir/zero-progress.out"
if (
  export PYTHONPATH="$zero_progress_python"
  hindsight_cleanup_run_archive_command() {
    [[ "$1" == /usr/bin/python3 && "$2" == -I ]] || return 1
    shift 2
    /usr/bin/python3 "$@"
  }
  archive_source_snapshot \
    "$tmp_dir/archive-source/file" "$tmp_dir/zero-progress-copy"
) >"$zero_progress_output" 2>&1; then
  print -ru2 -- "cleanup archive copy accepted a zero-progress write"
  exit 1
fi
rg -F -q 'destination write made no progress' "$zero_progress_output" || {
  print -ru2 -- "cleanup archive copy retried after a zero-progress write"
  exit 1
}
reserved_output_marker="$tmp_dir/reserved-output"
reserved_staging_marker="$tmp_dir/reserved-staging"
reserved_verification_marker="$tmp_dir/reserved-verification"
reserved_encrypt_command="$tmp_dir/encrypt-reserved-archive"
cat >"$reserved_encrypt_command" <<'EOF'
#!/usr/bin/env zsh
[[ -f "$2" && ! -L "$2" ]] || exit 1
print -r -- "$2" > "$HINDSIGHT_TEST_RESERVED_OUTPUT"
print -r -- "$1" > "$HINDSIGHT_TEST_STAGING_DIR"
exec /usr/bin/tar -cf "$2" -C "$1" .
EOF
chmod 700 "$reserved_encrypt_command"
HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/archive-source/directory,$tmp_dir/archive-source/file" \
  HINDSIGHT_CLEANUP_ARCHIVE_ENCRYPT_COMMAND="$reserved_encrypt_command" \
  HINDSIGHT_TEST_RESERVED_OUTPUT="$reserved_output_marker" \
  HINDSIGHT_TEST_STAGING_DIR="$reserved_staging_marker" \
  HINDSIGHT_TEST_VERIFICATION_DIR="$reserved_verification_marker" \
  archive_sources "$tmp_dir/verified-archive.age" >/dev/null
reserved_output="$(<"$reserved_output_marker")"
[[ "$reserved_output" == "$tmp_dir"/.single-bank-cleanup.encrypted.* ]] || {
  print -ru2 -- "cleanup did not use an exclusively reserved unpredictable encrypted output"
  exit 1
}
[[ ! -e "$reserved_output" ]] || {
  print -ru2 -- "cleanup left its reserved encrypted output behind after publication"
  exit 1
}
reserved_staging="$(<"$reserved_staging_marker")"
reserved_verification="$(<"$reserved_verification_marker")"
for plaintext_dir in "$reserved_staging" "$reserved_verification"; do
  [[ "$plaintext_dir" != "$tmp_dir"/* ]] || {
    print -ru2 -- "cleanup placed ephemeral plaintext under the archive root"
    exit 1
  }
  [[ ! -e "$plaintext_dir" ]] || {
    print -ru2 -- "cleanup retained ephemeral plaintext after archive verification"
    exit 1
  }
done
mkdir "$tmp_dir/verified-archive"
/usr/bin/tar -xf "$tmp_dir/verified-archive.age" -C "$tmp_dir/verified-archive"
/usr/bin/diff -qr "$tmp_dir/archive-source/directory" "$tmp_dir/verified-archive/directory" >/dev/null
/usr/bin/cmp -s "$tmp_dir/archive-source/file" "$tmp_dir/verified-archive/file"
[[ "$(/usr/bin/stat -f '%Lp' "$tmp_dir/verified-archive.age")" == 600 ]] || {
  print -ru2 -- "encrypted archive artifact permissions are not private"
  exit 1
}

corrupt_encrypt_command="$tmp_dir/corrupt-encrypt-archive"
cat >"$corrupt_encrypt_command" <<'EOF'
#!/usr/bin/env zsh
print -r -- corrupt > "$2"
EOF
chmod 700 "$corrupt_encrypt_command"
if (
  HINDSIGHT_CLEANUP_ARCHIVE_ENCRYPT_COMMAND="$corrupt_encrypt_command" \
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/archive-source/file" \
    archive_sources "$tmp_dir/corrupt-archive.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an encrypted artifact that did not contain the verified snapshot"
  exit 1
fi
[[ ! -e "$tmp_dir/corrupt-archive.age" ]] || {
  print -ru2 -- "cleanup published an unverified encrypted artifact"
  exit 1
}

mutating_encrypt_command="$tmp_dir/mutating-encrypt-archive"
cat >"$mutating_encrypt_command" <<'EOF'
#!/usr/bin/env zsh
print -r -- attacker-controlled > "$1/file"
exec /usr/bin/tar -cf "$2" -C "$1" .
EOF
chmod 700 "$mutating_encrypt_command"
if (
  HINDSIGHT_CLEANUP_ARCHIVE_ENCRYPT_COMMAND="$mutating_encrypt_command" \
  HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/archive-source/file" \
    archive_sources "$tmp_dir/mutating-archive.age"
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted encryption-command mutation of verified staging"
  exit 1
fi
[[ ! -e "$tmp_dir/mutating-archive.age" ]] || {
  print -ru2 -- "cleanup published an archive after staging mutation"
  exit 1
}

source_file_mode="$(/usr/bin/stat -f '%Lp' "$tmp_dir/archive-source/file")"
[[ "$source_file_mode" == 600 ]] && drift_file_mode=644 || drift_file_mode=600
chmod "$drift_file_mode" "$tmp_dir/verified-archive/file"
if verify_archive_copy "$tmp_dir/archive-source/file" "$tmp_dir/verified-archive/file"; then
  print -ru2 -- "archive verification ignored file metadata drift"
  exit 1
fi
chmod "$source_file_mode" "$tmp_dir/verified-archive/file"
/usr/bin/xattr -w com.example.hindsight-test drift \
  "$tmp_dir/verified-archive/file"
if verify_archive_copy "$tmp_dir/archive-source/file" "$tmp_dir/verified-archive/file"; then
  print -ru2 -- "archive verification ignored extended-attribute drift"
  exit 1
fi
/usr/bin/xattr -d com.example.hindsight-test "$tmp_dir/verified-archive/file"
source_directory_mode="$(/usr/bin/stat -f '%Lp' "$tmp_dir/archive-source/directory")"
[[ "$source_directory_mode" == 700 ]] && drift_directory_mode=755 || drift_directory_mode=700
chmod "$drift_directory_mode" "$tmp_dir/verified-archive/directory"
if verify_archive_copy "$tmp_dir/archive-source/directory" "$tmp_dir/verified-archive/directory"; then
  print -ru2 -- "archive verification ignored directory metadata drift"
  exit 1
fi

api_python() { print -r -- /usr/bin/python3 }
api_server_port="$tmp_dir/api-server-port"
api_server_observed="$tmp_dir/api-server-observed"
HINDSIGHT_TEST_API_PORT="$api_server_port" \
HINDSIGHT_TEST_API_OBSERVED="$api_server_observed" \
  /usr/bin/python3 - <<'PY' &
import http.server
import os


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with open(os.environ["HINDSIGHT_TEST_API_OBSERVED"], "w", encoding="utf-8") as handle:
            handle.write(f"{self.path}\n{self.headers.get('Authorization', '')}\n")
        payload = b'{"banks": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
server.timeout = 5
with open(os.environ["HINDSIGHT_TEST_API_PORT"], "w", encoding="utf-8") as handle:
    handle.write(str(server.server_port))
server.handle_request()
PY
api_server_pid=$!
for _ in {1..100}; do
  [[ -s "$api_server_port" ]] && break
  sleep 0.01
done
[[ -s "$api_server_port" ]] || {
  kill "$api_server_pid" >/dev/null 2>&1 || true
  wait "$api_server_pid" >/dev/null 2>&1 || true
  print -ru2 -- "cleanup API test server did not become ready"
  exit 1
}
api_port="$(<"$api_server_port")"
api_status=0
(
  hindsight_stack_load_config() { return 0 }
  export HINDSIGHT_CLEANUP_BANKS_URL="http://127.0.0.1:${api_port}/v1/test-tenant/banks"
  export HINDSIGHT_CLEANUP_API_TOKEN=behavioral-token
  export HTTP_PROXY=http://127.0.0.1:9
  export HTTPS_PROXY=http://127.0.0.1:9
  export ALL_PROXY=http://127.0.0.1:9
  export http_proxy=http://127.0.0.1:9
  export https_proxy=http://127.0.0.1:9
  export all_proxy=http://127.0.0.1:9
  export NO_PROXY=
  export no_proxy=
  print_api_bank_counts
) >/dev/null 2>&1 || api_status=$?
wait "$api_server_pid" || true
(( api_status == 0 )) || {
  print -ru2 -- "cleanup API count request did not bypass configured proxies"
  exit 1
}
api_observed="$(<"$api_server_observed")"
[[ "$api_observed" == $'/v1/test-tenant/banks\nBearer behavioral-token' ]] || {
  print -ru2 -- "cleanup API count request did not use the configured endpoint and bearer token"
  exit 1
}
if (
  hindsight_stack_load_config() { return 0 }
  unset HINDSIGHT_CLEANUP_BANKS_URL HINDSIGHT_CLEANUP_API_TOKEN
  print_api_bank_counts
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an inferred unauthenticated API endpoint"
  exit 1
fi

if (
  unset HINDSIGHT_BANK_ID HINDSIGHT_CLEANUP_SOURCE_BANKS \
    HINDSIGHT_CLEANUP_ARCHIVE_SOURCES
  main
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted inferred migration policy"
  exit 1
fi

typeset -g HINDSIGHT_BANK_ID="canonical-bank"
typeset -g HINDSIGHT_CLEANUP_SOURCE_BANKS="legacy-one,legacy-two"
print -r -- archive-one >"$tmp_dir/archive-one"
print -r -- archive-two >"$tmp_dir/archive-two"
typeset -g HINDSIGHT_CLEANUP_ARCHIVE_SOURCES="$tmp_dir/archive-one,$tmp_dir/archive-two"
typeset -g HINDSIGHT_CLEANUP_LEGACY_PROFILES="legacy-profile-one,legacy-profile-two"
typeset -g HINDSIGHT_CLEANUP_DELETE_PROFILES="retired-profile-one,retired-profile-two"
typeset -g HINDSIGHT_CLEANUP_BANKS_URL="http://127.0.0.1:7979/v1/test-tenant/banks"
typeset -g HINDSIGHT_CLEANUP_API_TOKEN="test-token"
typeset -g HINDSIGHT_CLEANUP_ARCHIVE_ROOT="$tmp_dir/archive-root"
typeset -g HINDSIGHT_EMBED_STACK_LIB="$repo_dir/lib/hindsight-embed-stack.zsh"
typeset -g HINDSIGHT_EMBED_API_PYTHON=/usr/bin/python3
typeset -g HINDSIGHT_EMBED_MIGRATION_HELPER="$repo_dir/libexec/hindsight-embed-single-bank-migrate.py"
typeset -g HINDSIGHT_EMBED_SERVICE_COMMAND=/usr/bin/true
typeset -g HINDSIGHT_EMBED_UVX=/usr/bin/true

cleanup_lock_ready="$tmp_dir/cleanup-lock-ready"
cleanup_lock_attempted="$tmp_dir/cleanup-lock-attempted"
cleanup_lock_acquired="$tmp_dir/cleanup-lock-acquired"
cleanup_lock_release="$tmp_dir/cleanup-lock-release"
(
  hold_cleanup_lock() {
    touch "$cleanup_lock_ready"
    while [[ ! -e "$cleanup_lock_release" ]]; do sleep 0.01; done
  }
  run_with_cleanup_lock hold_cleanup_lock
) &
cleanup_lock_holder=$!
for _ in {1..100}; do
  [[ -e "$cleanup_lock_ready" ]] && break
  sleep 0.01
done
[[ -e "$cleanup_lock_ready" ]] || {
  print -ru2 -- "cleanup lifecycle lock holder did not become ready"
  exit 1
}
(
  observe_cleanup_lock() { touch "$cleanup_lock_acquired" }
  touch "$cleanup_lock_attempted"
  run_with_cleanup_lock observe_cleanup_lock
) &
cleanup_lock_observer=$!
for _ in {1..100}; do
  [[ -e "$cleanup_lock_attempted" ]] && break
  sleep 0.01
done
[[ -e "$cleanup_lock_attempted" && ! -e "$cleanup_lock_acquired" ]] || {
  print -ru2 -- "cleanup lifecycle lock admitted overlapping apply work"
  exit 1
}
sleep 0.1
/bin/kill -0 "$cleanup_lock_observer" >/dev/null 2>&1 || {
  print -ru2 -- "cleanup lifecycle lock contender exited instead of blocking"
  exit 1
}
[[ ! -e "$cleanup_lock_acquired" ]] || {
  print -ru2 -- "cleanup lifecycle lock contender acquired before holder release"
  exit 1
}
touch "$cleanup_lock_release"
wait "$cleanup_lock_holder"
cleanup_lock_holder=""
wait "$cleanup_lock_observer"
cleanup_lock_observer=""
[[ -e "$cleanup_lock_acquired" ]] || {
  print -ru2 -- "cleanup lifecycle lock did not release to the waiting workflow"
  exit 1
}

# Apply-path fixtures isolate migration semantics from the independently tested
# maintenance-flock implementation.
acquire_cleanup_maintenance_lease() {
  typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=1
}
release_cleanup_maintenance_lease() {
  typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=0
}

receipt_archive="$tmp_dir/receipt-archive.age"
print -r -- encrypted-payload > "$receipt_archive"
chmod 600 "$receipt_archive"
receipt_digest="$(cleanup_archive_digest "$receipt_archive")"
write_cleanup_commit_receipt "$receipt_archive" "$receipt_digest"
cleanup_commit_receipt_valid "$receipt_archive" "$receipt_digest" || {
  print -ru2 -- "cleanup rejected its durable digest-bound commit receipt"
  exit 1
}
receipt_path="$(cleanup_commit_receipt_path "$receipt_archive")"
receipt_payload="$(<"$receipt_path")"
if write_cleanup_commit_receipt "$receipt_archive" "$receipt_digest" >/dev/null 2>&1; then
  print -ru2 -- "cleanup commit receipt replaced an existing destination"
  exit 1
fi
[[ "$(<"$receipt_path")" == "$receipt_payload" ]] || {
  print -ru2 -- "cleanup commit receipt changed after no-replace publication failed"
  exit 1
}
receipt_temporaries=("${receipt_path:h}"/".${receipt_path:t}.tmp."*(N))
(( ${#receipt_temporaries} == 0 )) || {
  print -ru2 -- "cleanup commit receipt left a private sibling temporary"
  exit 1
}
print -r -- tampered >> "$receipt_archive"
if cleanup_commit_receipt_valid "$receipt_archive" "$(cleanup_archive_digest "$receipt_archive")"; then
  print -ru2 -- "cleanup commit receipt accepted a changed archive digest"
  exit 1
fi

untrusted_helper="$tmp_dir/untrusted-helper"
print -r -- '#!/usr/bin/env zsh' > "$untrusted_helper"
chmod 777 "$untrusted_helper"
for binding in \
  HINDSIGHT_EMBED_STACK_LIB \
  HINDSIGHT_EMBED_API_PYTHON \
  HINDSIGHT_EMBED_MIGRATION_HELPER \
  HINDSIGHT_EMBED_SERVICE_COMMAND \
  HINDSIGHT_EMBED_UVX \
  HINDSIGHT_CLEANUP_ARCHIVE_ENCRYPT_COMMAND \
  HINDSIGHT_CLEANUP_ARCHIVE_DECRYPT_COMMAND; do
  if (
    typeset -gx "${binding}=${untrusted_helper}"
    validate_cleanup_helpers 1
  ) >/dev/null 2>&1; then
    print -ru2 -- "cleanup accepted untrusted helper binding ${binding}"
    exit 1
  fi
done

for invalid_timeout in 0 01 -1 3601; do
  if (
    HINDSIGHT_CLEANUP_ARCHIVE_TIMEOUT_SECONDS="$invalid_timeout"
    validate_cleanup_timeouts
  ) >/dev/null 2>&1; then
    print -ru2 -- "cleanup accepted invalid archive timeout ${invalid_timeout}"
    exit 1
  fi
  if (
    HINDSIGHT_CLEANUP_MIGRATION_TIMEOUT_SECONDS="$invalid_timeout"
    validate_cleanup_timeouts
  ) >/dev/null 2>&1; then
    print -ru2 -- "cleanup accepted invalid migration timeout ${invalid_timeout}"
    exit 1
  fi
done

invalid_timeout_shutdown="$tmp_dir/invalid-timeout-shutdown"
if (
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  validate_cleanup_policy() { return 0 }
  stop_managed_and_legacy_services() { touch "$invalid_timeout_shutdown" }
  HINDSIGHT_CLEANUP_ARCHIVE_TIMEOUT_SECONDS=01
  apply_cleanup
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an invalid timeout before apply"
  exit 1
fi
[[ ! -e "$invalid_timeout_shutdown" ]] || {
  print -ru2 -- "cleanup stopped services before validating command timeouts"
  exit 1
}

incomplete_stack_lib="$tmp_dir/incomplete-stack.zsh"
print -r -- '# intentionally incomplete stack interface' > "$incomplete_stack_lib"
chmod 600 "$incomplete_stack_lib"
if (
  typeset -g HINDSIGHT_CLEANUP_STACK_LOADED=0
  typeset -g HINDSIGHT_EMBED_STACK_LIB="$incomplete_stack_lib"
  unfunction hindsight_stack_run_bounded 2>/dev/null || true
  load_stack_lib
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a sourced stack library with a missing required interface"
  exit 1
fi

hindsight_stack_load_config() {
  typeset -g HINDSIGHT_EMBED_PROFILE="active-profile"
}
hindsight_stack_enabled_profiles() {
  print -r -- active-profile
  print -r -- second-enabled-profile
}
hindsight_stack_valid_profile_name() {
  [[ "$1" =~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' ]]
}
load_stack_lib() { typeset -g HINDSIGHT_CLEANUP_STACK_LOADED=1 }
hindsight_stack_daemon_start() { return 0 }
hindsight_stack_wait_daemon() { return 0 }
managed_service_running() { return 0 }

canonical_delete_output="$tmp_dir/canonical-delete-output"
if (
  HINDSIGHT_CLEANUP_DELETE_PROFILES=active-profile
  hindsight_stack_enabled_profiles() { print -r -- second-enabled-profile }
  validate_delete_profiles
) >"$canonical_delete_output" 2>&1; then
  print -ru2 -- "cleanup accepted the canonical profile for deletion"
  exit 1
fi
rg -F -q 'refusing to delete canonical profile active-profile' "$canonical_delete_output" || {
  print -ru2 -- "cleanup did not explicitly reject canonical profile deletion"
  exit 1
}

migration_timeout_call="$tmp_dir/migration-timeout-call"
(
  api_python() { print -r -- /usr/bin/python3 }
  migration_helper() { print -r -- /usr/bin/true }
  hindsight_stack_run_bounded() { print -r -- "$@" >"$migration_timeout_call" }
  HINDSIGHT_CLEANUP_MIGRATION_TIMEOUT_SECONDS=23
  HINDSIGHT_CLEANUP_SOURCE_BANKS=legacy-bank
  HINDSIGHT_BANK_ID=canonical-bank
  HINDSIGHT_EMBED_PROFILE=active-profile
  run_migration_helper dry-run 0
)
[[ "$(<"$migration_timeout_call")" == '23 /usr/bin/python3 /usr/bin/true '* ]] || {
  print -ru2 -- "cleanup migration helper did not use its configured bounded timeout"
  exit 1
}

HINDSIGHT_CLEANUP_TEMPORARY_DAEMON_STARTED=0
daemon_start_output="$tmp_dir/daemon-start-output"
start_profile_daemon_only >"$daemon_start_output"
daemon_start_state="$(<"$daemon_start_output")"
[[ "$daemon_start_state" == started ]] || {
  print -ru2 -- "cleanup daemon start did not return explicit started state"
  exit 1
}
[[ "$HINDSIGHT_CLEANUP_TEMPORARY_DAEMON_STARTED" == 1 ]] || {
  print -ru2 -- "cleanup daemon start did not record current-shell ownership"
  exit 1
}

daemon_stop_marker="$tmp_dir/temporary-daemon-stopped"
daemon_failure_log="$tmp_dir/temporary-daemon-failure.log"
if (
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  archive_root_for_timestamp() { print -r -- "$tmp_dir/archive" }
  print_api_bank_counts() { return 0 }
  stop_managed_and_legacy_services() { return 0 }
  archive_sources() { return 0 }
  cleanup_archive_digest() {
    print -r -- aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  }
  profile_env_value() { return 1 }
  set_profile_bank() { return 0 }
  start_profile_daemon_only() { print -r -- started }
  wait_profile_daemon_only() { return 1 }
  stop_profile_daemon_only() {
    print -r -- stopped > "$daemon_stop_marker"
    return 0
  }
  restore_profile_bank() { return 0 }
  start_managed_stack() { return 0 }
  typeset -g allow_nonempty_target=0
  apply_cleanup
) >"$daemon_failure_log" 2>&1; then
  print -ru2 -- "cleanup accepted a temporary daemon that failed readiness"
  exit 1
fi
[[ -f "$daemon_stop_marker" ]] || {
  /bin/cat "$daemon_failure_log" >&2
  print -ru2 -- "cleanup orphaned a temporary daemon after readiness failure"
  exit 1
}

daemon_start_failure_events="$tmp_dir/daemon-start-failure-events"
daemon_start_failure_log="$tmp_dir/daemon-start-failure.log"
: >"$daemon_start_failure_events"
if (
  HINDSIGHT_BANK_ID=canonical-bank
  HINDSIGHT_EMBED_PROFILE=active-profile
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  acquire_cleanup_maintenance_lease() {
    typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=1
    print -r -- lease-acquire >>"$daemon_start_failure_events"
  }
  release_cleanup_maintenance_lease() {
    typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=0
    print -r -- lease-release >>"$daemon_start_failure_events"
  }
  run_with_cleanup_lock() { "$@" }
  validate_cleanup_timeouts() { return 0 }
  validate_cleanup_policy() { return 0 }
  archive_root_for_timestamp() { print -r -- "$tmp_dir/daemon-start-failure-archive" }
  print_api_bank_counts() { return 0 }
  validate_archive_plan() { return 0 }
  stop_managed_and_legacy_services() {
    typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
    typeset -g HINDSIGHT_CLEANUP_MANAGED_WAS_RUNNING=1
  }
  archive_sources() { return 0 }
  cleanup_archive_digest() {
    print -r -- dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
  }
  profile_env_value() { return 1 }
  set_profile_bank() { return 0 }
  hindsight_stack_load_config() { return 0 }
  hindsight_stack_daemon_start() { return 1 }
  stop_profile_daemon_only() { print -r -- stop-temp >>"$daemon_start_failure_events" }
  restore_profile_bank() { print -r -- restore-bank >>"$daemon_start_failure_events" }
  restore_service_topology() { print -r -- restore-topology >>"$daemon_start_failure_events" }
  typeset -g allow_nonempty_target=0
  apply_cleanup
) >"$daemon_start_failure_log" 2>&1; then
  print -ru2 -- "cleanup accepted a failed temporary daemon start"
  exit 1
fi
[[ "$(paste -sd, - <"$daemon_start_failure_events")" == \
  "lease-acquire,stop-temp,restore-bank,lease-release,restore-topology" ]] || {
  /bin/cat "$daemon_start_failure_log" >&2
  print -ru2 -- "cleanup did not pre-arm temporary-daemon recovery before startup"
  exit 1
}

bank_set_failure_events="$tmp_dir/bank-set-failure-events"
: >"$bank_set_failure_events"
if (
  HINDSIGHT_BANK_ID=canonical-bank
  HINDSIGHT_EMBED_PROFILE=active-profile
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  acquire_cleanup_maintenance_lease() {
    typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=1
    print -r -- lease-acquire >>"$bank_set_failure_events"
  }
  release_cleanup_maintenance_lease() {
    typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=0
    print -r -- lease-release >>"$bank_set_failure_events"
  }
  run_with_cleanup_lock() { "$@" }
  validate_cleanup_timeouts() { return 0 }
  validate_cleanup_policy() { return 0 }
  archive_root_for_timestamp() { print -r -- "$tmp_dir/bank-set-failure-archive" }
  print_api_bank_counts() { return 0 }
  validate_archive_plan() { return 0 }
  stop_managed_and_legacy_services() {
    typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
  }
  archive_sources() { return 0 }
  cleanup_archive_digest() {
    print -r -- eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
  }
  profile_env_value() { print -r -- previous-bank }
  set_profile_bank() { print -r -- set-bank >>"$bank_set_failure_events"; return 1 }
  restore_profile_bank() { print -r -- restore-bank >>"$bank_set_failure_events" }
  restore_service_topology() { print -r -- restore-topology >>"$bank_set_failure_events" }
  typeset -g allow_nonempty_target=0
  apply_cleanup
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a failed canonical bank update"
  exit 1
fi
[[ "$(paste -sd, - <"$bank_set_failure_events")" == \
  "lease-acquire,set-bank,restore-bank,lease-release,restore-topology" ]] || {
  print -ru2 -- "cleanup did not pre-arm bank restoration before changing the binding"
  exit 1
}

precommit_topology_restore="$tmp_dir/precommit-topology-restored"
if (
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  archive_root_for_timestamp() { print -r -- "$tmp_dir/precommit-archive" }
  print_api_bank_counts() { return 0 }
  stop_managed_and_legacy_services() {
    typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
    return 0
  }
  archive_sources() { return 1 }
  restore_service_topology() { touch "$precommit_topology_restore" }
  typeset -g allow_nonempty_target=0
  apply_cleanup
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a precommit archive failure"
  exit 1
fi
[[ -e "$precommit_topology_restore" ]] || {
  print -ru2 -- "cleanup did not restore the captured service topology after precommit failure"
  exit 1
}

durable_commit_events="$tmp_dir/durable-commit-events"
durable_commit_log="$tmp_dir/durable-commit.log"
if (
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  acquire_cleanup_maintenance_lease() {
    typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=1
    print -r -- lease-acquire >>"$durable_commit_events"
  }
  release_cleanup_maintenance_lease() {
    (( HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD )) || return 0
    typeset -gi HINDSIGHT_CLEANUP_MAINTENANCE_LEASE_HELD=0
    print -r -- lease-release >>"$durable_commit_events"
  }
  validate_cleanup_timeouts() { return 0 }
  validate_cleanup_policy() { return 0 }
  archive_root_for_timestamp() { print -r -- "$tmp_dir/durable-archive" }
  print_api_bank_counts() { return 0 }
  validate_archive_plan() { return 0 }
  stop_managed_and_legacy_services() {
    typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
    typeset -g HINDSIGHT_CLEANUP_MANAGED_WAS_RUNNING=1
  }
  archive_sources() { return 0 }
  cleanup_archive_digest() {
    print -r -- digest >>"$durable_commit_events"
    print -r -- bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  }
  profile_env_value() { return 1 }
  set_profile_bank() { return 0 }
  start_profile_daemon_only() { print -r -- started }
  wait_profile_daemon_only() { return 0 }
  run_migration_helper() {
    if [[ "$1" == apply ]]; then
      print -r -- migration-nonzero >>"$durable_commit_events"
      return 7
    fi
    [[ "$1" == verify-committed ]] || return 1
    print -r -- reconciliation-committed >>"$durable_commit_events"
    return 0
  }
  write_cleanup_commit_receipt() {
    print -r -- receipt >>"$durable_commit_events"
    return 1
  }
  stop_profile_daemon_only() {
    print -r -- "stop-temp:${HINDSIGHT_CLEANUP_PRECOMMIT}:${HINDSIGHT_CLEANUP_RECOVERY_NEEDED}:${should_recover:-unset}" \
      >>"$durable_commit_events"
  }
  restore_profile_bank() { print -r -- restore-bank >>"$durable_commit_events" }
  restore_service_topology() { print -r -- restore-topology >>"$durable_commit_events" }
  start_managed_stack() { print -r -- managed-start >>"$durable_commit_events" }
  typeset -g allow_nonempty_target=0
  apply_cleanup_locked
) >"$durable_commit_log" 2>&1; then
  print -ru2 -- "cleanup accepted a failed post-commit receipt record"
  exit 1
fi
[[ "$(paste -sd, - <"$durable_commit_events")" == \
  "lease-acquire,digest,migration-nonzero,reconciliation-committed,receipt,stop-temp:0:1:1,lease-release,managed-start" ]] || {
  /bin/cat "$durable_commit_events" >&2
  /bin/cat "$durable_commit_log" >&2
  print -ru2 -- "cleanup treated its receipt as the durable commit boundary"
  exit 1
}

exercise_migration_reconciliation_failure() {
  local reconcile_rc="$1" events="$2"
  (
    hindsight_stack_require_current_user() { return 0 }
    hindsight_stack_require_tools() { return 0 }
    validate_cleanup_timeouts() { return 0 }
    validate_cleanup_policy() { return 0 }
    archive_root_for_timestamp() { print -r -- "$tmp_dir/reconcile-${reconcile_rc}-archive" }
    print_api_bank_counts() { return 0 }
    validate_archive_plan() { return 0 }
    stop_managed_and_legacy_services() {
      typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
    }
    archive_sources() { return 0 }
    cleanup_archive_digest() {
      print -r -- cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    }
    profile_env_value() { print -r -- previous-bank }
    set_profile_bank() { return 0 }
    start_profile_daemon_only() { print -r -- started }
    wait_profile_daemon_only() { return 0 }
    run_migration_helper() {
      print -r -- "$1" >>"$events"
      [[ "$1" == apply ]] && return 7
      return "$reconcile_rc"
    }
    stop_profile_daemon_only() { print -r -- stop-temp >>"$events" }
    restore_profile_bank() { print -r -- restore-bank >>"$events" }
    restore_service_topology() { print -r -- restore-topology >>"$events" }
    start_managed_stack() { print -r -- managed-start >>"$events" }
    write_cleanup_commit_receipt() { print -r -- unexpected-receipt >>"$events" }
    typeset -g allow_nonempty_target=0
    apply_cleanup_locked
  ) >/dev/null 2>&1
}

not_committed_events="$tmp_dir/not-committed-events"
if exercise_migration_reconciliation_failure 3 "$not_committed_events"; then
  print -ru2 -- "cleanup accepted an explicitly uncommitted migration"
  exit 1
fi
[[ "$(paste -sd, - <"$not_committed_events")" == \
  "apply,verify-committed,stop-temp,restore-bank,restore-topology" ]] || {
  print -ru2 -- "cleanup did not restore only after proving migration was uncommitted"
  exit 1
}

indeterminate_events="$tmp_dir/indeterminate-events"
if exercise_migration_reconciliation_failure 1 "$indeterminate_events"; then
  print -ru2 -- "cleanup accepted an indeterminate migration outcome"
  exit 1
fi
[[ "$(paste -sd, - <"$indeterminate_events")" == \
  "apply,verify-committed,stop-temp" ]] || {
  print -ru2 -- "cleanup restored legacy state after indeterminate commit reconciliation"
  exit 1
}

invalid_delete_migration_marker="$tmp_dir/invalid-delete-migration"
if (
  HINDSIGHT_CLEANUP_DELETE_PROFILES="retired-profile,active-profile"
  hindsight_stack_require_current_user() { return 0 }
  hindsight_stack_require_tools() { return 0 }
  archive_root_for_timestamp() { print -r -- "$tmp_dir/invalid-delete-archive" }
  print_api_bank_counts() { return 0 }
  stop_managed_and_legacy_services() { return 0 }
  archive_sources() { return 0 }
  profile_env_value() { return 1 }
  set_profile_bank() { return 0 }
  start_profile_daemon_only() { print -r -- started }
  wait_profile_daemon_only() { return 0 }
  stop_profile_daemon_only() { return 0 }
  restore_profile_bank() { return 0 }
  start_managed_stack() { return 0 }
  run_migration_helper() { touch "$invalid_delete_migration_marker" }
  apply_cleanup
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup apply accepted deletion of the canonical active profile"
  exit 1
fi

if (
  HINDSIGHT_CLEANUP_DELETE_PROFILES="retired-profile,second-enabled-profile"
  validate_delete_profiles
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted deletion of a non-selected enabled fleet profile"
  exit 1
fi
if (
  HINDSIGHT_CLEANUP_DELETE_PROFILES="retired-profile,../escape"
  validate_delete_profiles
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted an invalid delete profile name"
  exit 1
fi
canonical_dry_run_mutation="$tmp_dir/canonical-dry-run-mutation"
if (
  HINDSIGHT_CLEANUP_LEGACY_PROFILES="legacy-profile,active-profile"
  hindsight_stack_require_tools() { return 0 }
  run_migration_helper() { touch "$canonical_dry_run_mutation" }
  dry_run
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup dry-run accepted the canonical profile as legacy"
  exit 1
fi
[[ ! -e "$canonical_dry_run_mutation" ]] || {
  print -ru2 -- "cleanup dry-run mutated before applying canonical-profile validation"
  exit 1
}
[[ ! -e "$invalid_delete_migration_marker" ]] || {
  print -ru2 -- "cleanup validated delete profiles after migration apply"
  exit 1
}

mode_file="$tmp_dir/mode"
dry_run() { print -r -- dry-run > "$mode_file" }
apply_cleanup() { print -r -- apply > "$mode_file" }

main
[[ "$(<"$mode_file")" == dry-run ]] || {
  print -ru2 -- "cleanup did not default to dry-run"
  exit 1
}

main --apply
[[ "$(<"$mode_file")" == apply ]] || {
  print -ru2 -- "cleanup did not require explicit --apply"
  exit 1
}

helper_args="$tmp_dir/helper-args"
fake_python="$tmp_dir/python"
print -r -- '#!/usr/bin/env zsh' > "$fake_python"
print -r -- 'print -r -- "$@" > "$HINDSIGHT_TEST_HELPER_ARGS"' >> "$fake_python"
chmod 700 "$fake_python"
api_python() { print -r -- "$fake_python" }
migration_helper() { print -r -- "/tmp/migration-helper.py" }
export HINDSIGHT_TEST_HELPER_ARGS="$helper_args"

run_migration_helper dry-run 0
actual="$(<"$helper_args")"
for expected in \
  '--mode dry-run' \
  '--profile active-profile' \
  '--target-bank canonical-bank' \
  '--source-bank legacy-one' \
  '--source-bank legacy-two'; do
  [[ "$actual" == *"$expected"* ]] || {
    print -ru2 -- "cleanup omitted explicit helper argument: ${expected}"
    exit 1
  }
done

legacy_stop_args="$tmp_dir/legacy-stop-args"
(
  typeset -g HINDSIGHT_CLEANUP_LEGACY_PROFILES="legacy-profile,@default"
  HINDSIGHT_EMBED_SERVICE_COMMAND=/usr/bin/true
  hindsight_cleanup_run_lifecycle_command() {
    if [[ "$1" == "$HINDSIGHT_EMBED_SERVICE_COMMAND" ]]; then
      print -r -- "service-stop|${HINDSIGHT_EMBED_MAINTENANCE_LEASE_HELD:-0}|${(j:|:)${@:2}}" >>"$legacy_stop_args"
    fi
    return 0
  }
  hindsight_stack_run_stop_helper() {
    print -r -- "${(j:|:)@}" >> "$legacy_stop_args"
  }
  hindsight_stack_ui_running() { return 1 }
  hindsight_stack_daemon_running() { return 1 }
  hindsight_stack_broker_stop() { print -r -- broker-stop >> "$legacy_stop_args" }
  hindsight_stack_broker_status() { return 1 }
  hindsight_stack_control_running() { return 1 }
  stop_managed_and_legacy_services
)
rg -F -x -q -- 'service-stop|1|stop|--maintenance-lease-held' "$legacy_stop_args" || {
  print -ru2 -- "cleanup did not use the held-maintenance-lease service stop path"
  exit 1
}
rg -F -x -q -- 'broker-stop' "$legacy_stop_args" || {
  print -ru2 -- "cleanup did not explicitly stop the runtime broker"
  exit 1
}
rg -F -x -q -- '--mode|stop|--profile|legacy-profile|--allow-unregistered-profile' "$legacy_stop_args" || {
  print -ru2 -- "cleanup omitted named legacy profile stop"
  exit 1
}
rg -F -x -q -- '--mode|stop|--profile||--allow-unregistered-profile' "$legacy_stop_args" || {
  print -ru2 -- "cleanup did not preserve the unnamed default profile stop"
  exit 1
}
if (
  capture_service_topology() { return 0 }
  validate_trusted_artifact() { return 0 }
  hindsight_cleanup_run_lifecycle_command() { return 0 }
  hindsight_stack_broker_stop() { return 0 }
  hindsight_stack_broker_status() { return 0 }
  hindsight_stack_run_stop_helper() { return 0 }
  hindsight_stack_ui_running() { return 1 }
  hindsight_stack_daemon_running() { return 1 }
  hindsight_stack_control_running() { return 1 }
  stop_managed_and_legacy_services
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted a responsive broker after shutdown"
  exit 1
fi

topology_events="$tmp_dir/topology-events"
if (
  typeset -g HINDSIGHT_CLEANUP_LEGACY_PROFILES=""
  managed_service_running() { return 2 }
  capture_service_topology
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup treated a managed-service inspection error as an absent service"
  exit 1
fi
if (
  typeset -g HINDSIGHT_CLEANUP_LEGACY_PROFILES="legacy-error"
  managed_service_running() { return 1 }
  legacy_component_running() { return 2 }
  capture_service_topology
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup treated a legacy status operational error as a stopped component"
  exit 1
fi
(
  typeset -g HINDSIGHT_CLEANUP_LEGACY_PROFILES="legacy-one,legacy-two"
  typeset -g HINDSIGHT_EMBED_SERVICE_COMMAND=fake_topology_service
  typeset -g HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=30
  typeset -g HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS=5
  typeset -g topology_capture=1
  managed_service_running() { return 0 }
  hindsight_stack_run_bounded() {
    local invocation="${(j: :)@}"
    if (( topology_capture )) && [[ "$invocation" == *"legacy-one daemon status"* ||
      "$invocation" == *"legacy-two ui status"* ]]; then
      return 0
    fi
    if (( ! topology_capture )) && [[ "$invocation" == *"legacy-one daemon status"* ]]; then
      print -r -- ready:legacy-one >>"$topology_events"
      return 0
    fi
    if (( ! topology_capture )) && [[ "$invocation" == *"legacy-two daemon status"* ]]; then
      print -r -- ready:legacy-two >>"$topology_events"
      return 0
    fi
    if [[ "$invocation" == *"legacy-one daemon start"* ]]; then
      print -r -- daemon-start:legacy-one >>"$topology_events"
      return 0
    fi
    if [[ "$invocation" == *"legacy-two ui start"* ]]; then
      print -r -- ui-start:legacy-two >>"$topology_events"
      return 0
    fi
    return 1
  }
  start_managed_stack() { print -r -- managed-start >> "$topology_events" }
  capture_service_topology
  (( HINDSIGHT_CLEANUP_MANAGED_WAS_RUNNING == 1 ))
  (( HINDSIGHT_CLEANUP_LEGACY_DAEMON_WAS_RUNNING[legacy-one] == 1 ))
  (( HINDSIGHT_CLEANUP_LEGACY_UI_WAS_RUNNING[legacy-one] == 0 ))
  (( HINDSIGHT_CLEANUP_LEGACY_DAEMON_WAS_RUNNING[legacy-two] == 0 ))
  (( HINDSIGHT_CLEANUP_LEGACY_UI_WAS_RUNNING[legacy-two] == 1 ))
  topology_capture=0
  typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
  restore_service_topology
)
topology_rendered="$(paste -sd, - <"$topology_events")"
[[ "$topology_rendered" == \
  "managed-start,daemon-start:legacy-one,ready:legacy-one,ready:legacy-two,ui-start:legacy-two" ]] || {
  /bin/cat "$topology_events" >&2
  print -ru2 -- "cleanup did not restore the exact managed and legacy service topology"
  exit 1
}

idle_loaded_topology="$tmp_dir/idle-loaded-topology"
(
  typeset -g HINDSIGHT_CLEANUP_LEGACY_PROFILES=""
  managed_service_loaded() { return 0 }
  managed_service_running() { return 1 }
  start_managed_stack() { touch "$idle_loaded_topology" }
  capture_service_topology
  (( HINDSIGHT_CLEANUP_MANAGED_WAS_LOADED == 1 ))
  (( HINDSIGHT_CLEANUP_MANAGED_WAS_RUNNING == 0 ))
  typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
  restore_service_topology
)
[[ -e "$idle_loaded_topology" ]] || {
  print -ru2 -- "cleanup did not restore an idle loaded managed LaunchAgent"
  exit 1
}

stopped_topology_start="$tmp_dir/stopped-topology-start"
(
  typeset -g HINDSIGHT_CLEANUP_TOPOLOGY_CAPTURED=1
  typeset -g HINDSIGHT_CLEANUP_MANAGED_WAS_LOADED=0
  typeset -g HINDSIGHT_CLEANUP_MANAGED_WAS_RUNNING=0
  typeset -g HINDSIGHT_CLEANUP_LEGACY_PROFILES=""
  start_managed_stack() { touch "$stopped_topology_start" }
  restore_service_topology
)
[[ ! -e "$stopped_topology_start" ]] || {
  print -ru2 -- "cleanup started a managed stack that was stopped before cleanup"
  exit 1
}

delete_events="$tmp_dir/delete-events"
fake_delete_uvx="$tmp_dir/fake-delete-uvx"
cat > "$fake_delete_uvx" <<'ZSH'
#!/usr/bin/env zsh
if [[ "$1" == hindsight-embed && "$2" == profile && "$3" == delete ]]; then
  print -r -- "delete:$4" >> "$HINDSIGHT_TEST_DELETE_EVENTS"
fi
ZSH
chmod 700 "$fake_delete_uvx"
(
  typeset -g HINDSIGHT_CLEANUP_DELETE_PROFILES="retired-one,retired-two"
  typeset -g HINDSIGHT_EMBED_UVX="$fake_delete_uvx"
  typeset -gx HINDSIGHT_TEST_DELETE_EVENTS="$delete_events"
  typeset -A profile_checks=()
  hindsight_stack_load_config() { typeset -g HINDSIGHT_EMBED_PROFILE=active-profile }
  hindsight_stack_enabled_profiles() { print -r -- active-profile }
  hindsight_stack_valid_profile_name() {
    [[ "$1" =~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' ]]
  }
  set_profile_bank() { return 0 }
  stop_profile_daemon_only() { return 0 }
  profile_name_present() {
    local profile="$1"
    profile_checks[$profile]=$(( ${profile_checks[$profile]:-0} + 1 ))
    (( profile_checks[$profile] == 1 ))
  }
  hindsight_stack_run_stop_helper() {
    print -r -- "stop:$4" >> "$delete_events"
  }
  normalize_profile_config
)
[[ "$(paste -sd, - < "$delete_events")" == \
  "stop:retired-one,delete:retired-one,stop:retired-two,delete:retired-two" ]] || {
  print -ru2 -- "cleanup did not stop every delete profile immediately before deletion"
  exit 1
}

if (
  HINDSIGHT_CLEANUP_DELETE_PROFILES="retired-profile,active-profile"
  validate_delete_profiles
) >/dev/null 2>&1; then
  print -ru2 -- "cleanup accepted deletion of the canonical active profile"
  exit 1
fi

for forbidden in claude_code Engineering claude-code; do
  [[ "$actual" != *"$forbidden"* ]] || {
    print -ru2 -- "cleanup inferred machine policy: ${forbidden}"
    exit 1
  }
done
