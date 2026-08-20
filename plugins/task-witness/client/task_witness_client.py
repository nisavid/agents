"""Validate one task-evidence bundle through the canonical Task Witness launcher."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, NoReturn

_MODULE_CODE = sys._getframe().f_code

ACTIVE_CONTRACT = "task-witness-launch-active-v1"
ACTIVATION_INTENT_CONTRACT = "task-witness-activation-intent-v1"
ACTIVATION_TRANSACTION_CONTRACT = "task-witness-activation-transaction-v1"
BUNDLE_INVENTORY_CONTRACT = "task-witness-bundle-inventory-v1"
BRIDGE_MIGRATION_PROJECTION_CONTRACT = "task-witness-bridge-migration-projection-v1"
CANONICAL_PROJECTION_CONTRACT = "task-witness-canonical-projection-v2"
COMPATIBILITY_POLICY_CONTRACT = "task-witness-compatibility-policy-v2"
COMPLETE_ANCHOR_CONTRACT = "task-witness-complete-anchor-v1"
CONTROL_SURFACE_CONTRACT = "task-witness-control-surface-v1"
DEPLOYER_AUTHORIZATION_CONTRACT = "task-witness-deployer-authorization-v1"
DEPLOYMENT_RECEIPT_CONTRACT = "task-witness-deployment-receipt-v2"
LEGACY_DEPLOYMENT_RECEIPT_CONTRACT = "task-witness-deployment-receipt-v1"
ENVELOPE_CONTRACT = "task-witness-launch-envelope-v1"
FIRST_INSTALL_ROLLBACK_CONTRACT = "task-witness-first-install-rollback-v1"
MANAGER_BINDING_CONTRACT = "task-witness-manager-binding-v1"
PROCESS_PROFILE_CONTRACT = "task-witness-process-profile-v2"
ROLLBACK_RECEIPT_CONTRACT = "task-witness-rollback-receipt-v1"
RUNTIME_ARTIFACT_MANIFEST_CONTRACT = "task-witness-runtime-artifact-manifest-v2"
RUNTIME_CONTRACT = "task-witness-runtime-v1"
SMOKE_BUNDLE_CONTRACT = "task-witness-smoke-bundle-v1"
INTRINSIC_SMOKE_PROVIDER_CONTRACT = "task-witness-intrinsic-smoke-provider-v1"
SMOKE_ISSUER_CONTRACT = "task-witness-smoke-issuer-v1"
SMOKE_ISSUER_IMPLEMENTATION_CONTRACT = "task-witness-smoke-issuer-implementation-v1"
SMOKE_ISSUER_NAME = "task-witness-smoke-issuer"
SMOKE_PRODUCER_NAME = "task-witness-smoke-producer"
SMOKE_PRODUCER_IMPLEMENTATION_CONTRACT = "task-witness-smoke-producer-implementation-v1"
SMOKE_VALIDATOR_NAME = "task-witness-smoke-validator"
SOURCE_SELECTION_CONTRACT = "task-witness-source-selection-v1"
SOURCE_EVIDENCE_CONTRACT = "task-witness-source-evidence-v1"
STAGED_DEPLOYMENT_CONTRACT = "task-witness-staged-deployment-v1"
TRUST_CONTEXT_CONTRACT = "task-witness-trust-context-v2"
VALIDATOR_ARTIFACT_MANIFEST_CONTRACT = "task-witness-validator-artifact-manifest-v1"
# fmt: off
CLIENT_SOURCE_GENERATION_SHA256 = "ebef8fa79ba9491fbe24f8691f6e542699b3dd43ddc93236f208bd4f239ab936"
# fmt: on
CLIENT_RELEASE_PROFILE = "tw4-current"
FREEZE5_COMMIT_SHA1 = "96608a9b91d4dcf3f468a4fab1f0e008c9c32b36"
FREEZE5_CONTROLLER_SHA256 = (
    "8dc51b2a644e30d1f7c4f3b71711698b4130b43f1517e9f5361c6d1a0f7d6cfe"
)
FREEZE5_POLICY_SHA256 = (
    "23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245"
)
FREEZE5_CLIENT_SHA256 = (
    "778186f6a460655a8b390c831e05c233171236898663ad4155bd45695597c6cf"
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
GENERATION = re.compile(r"sha256-[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PLATFORM_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
APPLE_TEXT_ENCODING = re.compile(
    r"0x[0-9A-F]+:0x[0-9A-F]+:0x[0-9A-F]+\Z",
)
RUNTIME_PAYLOAD_SPECS = (
    ("entrypoint", "task_witness.py"),
    ("canonical", "canonical.py"),
    ("bundle-io", "bundle_io.py"),
    ("trust", "trust.py"),
)
MAX_JSON_NUMBER_CHARACTERS = 128
MAX_JSON_DEPTH = 100
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_BUNDLE_FILES = 256
MAX_BUNDLE_FILE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_CONTROL_FILE_BYTES = 4 * 1024 * 1024
MAX_INTERPRETER_BYTES = 64 * 1024 * 1024
MAX_PROC_STAT_BYTES = 4096
MAX_RUNTIME_PAYLOAD_BYTES = 1024 * 1024
MAX_VALIDATORS = 64
MAX_VALIDATOR_MODULES = 32
MAX_VALIDATOR_ARTIFACT_BYTES = 1024 * 1024
CONTROL_PREIMAGE_SPECS = (
    ("controller", "controller/task_witness_deploy.py", 0o500, MAX_CONTROL_FILE_BYTES),
    ("policy", "controller/policy.json", 0o600, MAX_DOCUMENT_BYTES),
    ("launcher", "launcher/task_witness_launch.py", 0o500, MAX_CONTROL_FILE_BYTES),
    ("client", "client/task_witness_client.py", 0o500, MAX_CONTROL_FILE_BYTES),
    (
        "smoke-bundle-manifest",
        "smoke/bundle/manifest.json",
        0o600,
        MAX_DOCUMENT_BYTES,
    ),
    ("shim", "task-witness", 0o500, MAX_CONTROL_FILE_BYTES),
)
_ACL_TYPE_EXTENDED = 0x100
_ACL_FIRST_ENTRY = 0
_ACL_NEXT_ENTRY = -1
_ACL_EXTENDED_ALLOW = 1
_ACL_EXTENDED_DENY = 2
EXIT_INVOCATION = 64
EXIT_LAUNCH = 65
EXIT_INSTALLATION = 70
EXIT_RESOURCE = 124
MINIMUM_CPYTHON_VERSION = (3, 13)
RESOURCE_ERRNOS = frozenset(
    number
    for number in (
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EMFILE", None),
        getattr(errno, "ENFILE", None),
        getattr(errno, "ENOMEM", None),
    )
    if number is not None
)
LOCK_WOULD_BLOCK_ERRNOS = frozenset(
    number
    for number in (
        getattr(errno, "EACCES", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EWOULDBLOCK", None),
    )
    if number is not None
)
ChangeToken = tuple[int, int]
FilesystemIdentity = tuple[int, int, int, int, int, int, int, int]
DirectoryRecord = tuple[
    int,
    tuple[int, int, int, int],
    int | None,
    str,
    ChangeToken | None,
]
FileRecord = tuple[
    int,
    tuple[int, int, int, int, int, int, int],
    int,
    str,
    bytes,
    str,
    int,
    bool,
]
InventoryRecord = tuple[int, frozenset[str], str]
PROCESS_PROFILE = {
    "contract": PROCESS_PROFILE_CONTRACT,
    "interpreter_flags": ["-B", "-I", "-S", "-X", "disable-remote-debug"],
    "environment": {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    },
    "cwd": "/",
    "stdin": "closed",
    "close_fds": True,
    "new_session": True,
    "restore_signals": True,
    "umask": 0o077,
    "validation_deadline_seconds": 60,
    "accepted_output_deadline_seconds": 60,
    "termination_grace_seconds": 2,
    "kill_reap_seconds": 1,
    "post_leader_pipe_drain_seconds": 1,
    "stdout_max_bytes": 4 * 1024 * 1024,
    "stderr_max_bytes": 256 * 1024,
    "diagnostic_max_bytes": 4 * 1024,
    "diagnostic_write_seconds": 0.05,
    "io_chunk_bytes": 64 * 1024,
    "shared_lock_seconds": 2,
    "exclusive_lock_seconds": 65,
}
RECEIPT_CONTRACTS = {
    "active": ACTIVE_CONTRACT,
    "runtime": RUNTIME_CONTRACT,
    "runtime_artifact_manifest": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
    "envelope": ENVELOPE_CONTRACT,
    "anchor": COMPLETE_ANCHOR_CONTRACT,
    "canonical_projection": CANONICAL_PROJECTION_CONTRACT,
    "trust_context": TRUST_CONTEXT_CONTRACT,
    "process_profile": PROCESS_PROFILE_CONTRACT,
    "source_selection": SOURCE_SELECTION_CONTRACT,
    "manager_binding": MANAGER_BINDING_CONTRACT,
    "compatibility_policy": COMPATIBILITY_POLICY_CONTRACT,
    "deployment_receipt": DEPLOYMENT_RECEIPT_CONTRACT,
    "rollback_receipt": ROLLBACK_RECEIPT_CONTRACT,
}
LEGACY_RECEIPT_CONTRACTS = {
    **RECEIPT_CONTRACTS,
    "deployment_receipt": LEGACY_DEPLOYMENT_RECEIPT_CONTRACT,
}
CURRENT_RECEIPT_PROFILE = "current-v2"
BRIDGE_LEGACY_RECEIPT_PROFILE = "bridge-legacy-v1"
ACTIVATION_TRANSACTION_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "transaction_id",
        "sequence",
        "previous_journal_sha256",
        "transaction_class",
        "phase",
        "canonical_root",
        "effective_uid",
        "activation_lock",
        "outer_maintenance_transaction_sha256",
        "stage",
        "prior",
        "candidate",
        "rollback_authority",
        "preimage",
        "pending_step",
        "smoke_handoff",
        "candidate_smoke_acceptance",
        "rollback_smoke_acceptance",
        "terminal_result",
        "content_sha256",
    }
)
BRIDGE_ACTIVATION_TRANSACTION_KEYS = ACTIVATION_TRANSACTION_KEYS | {"bridge_transition"}
CLIENT_CONTRACTS = {
    **RECEIPT_CONTRACTS,
    "activation_intent": ACTIVATION_INTENT_CONTRACT,
    "activation_transaction": ACTIVATION_TRANSACTION_CONTRACT,
    "bundle_inventory": BUNDLE_INVENTORY_CONTRACT,
    "control_surface": CONTROL_SURFACE_CONTRACT,
    "deployer_authorization": DEPLOYER_AUTHORIZATION_CONTRACT,
    "first_install_rollback": FIRST_INSTALL_ROLLBACK_CONTRACT,
    "intrinsic_smoke_provider": INTRINSIC_SMOKE_PROVIDER_CONTRACT,
    "smoke_bundle": SMOKE_BUNDLE_CONTRACT,
    "smoke_issuer": SMOKE_ISSUER_CONTRACT,
    "smoke_issuer_implementation": SMOKE_ISSUER_IMPLEMENTATION_CONTRACT,
    "smoke_producer_implementation": SMOKE_PRODUCER_IMPLEMENTATION_CONTRACT,
    "source_evidence": SOURCE_EVIDENCE_CONTRACT,
    "staged_deployment": STAGED_DEPLOYMENT_CONTRACT,
    "validator_artifact_manifest": VALIDATOR_ARTIFACT_MANIFEST_CONTRACT,
}
LEGACY_CLIENT_CONTRACTS = {
    **{
        key: value
        for key, value in CLIENT_CONTRACTS.items()
        if key != "source_evidence"
    },
    "deployment_receipt": LEGACY_DEPLOYMENT_RECEIPT_CONTRACT,
}
CANONICAL_FLAGS = {
    "debug": 0,
    "inspect": 0,
    "interactive": 0,
    "optimize": 0,
    "dont_write_bytecode": 1,
    "no_user_site": 1,
    "no_site": 1,
    "ignore_environment": 1,
    "verbose": 0,
    "bytes_warning": 0,
    "quiet": 0,
    "hash_randomization": 1,
    "isolated": 1,
    "dev_mode": False,
    "utf8_mode": 0,
}
PYTHON_RESTORED_SIGNALS = frozenset(
    getattr(signal, name)
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ")
    if hasattr(signal, name)
)
CANCELLATION_SIGNALS = tuple(
    getattr(signal, name)
    for name in ("SIGINT", "SIGTERM", "SIGHUP")
    if hasattr(signal, name)
)


class ClientError(ValueError):
    """The canonical client cannot accept this invocation or result."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


CLIENT_RESOURCE_ERROR = ClientError(
    "client resources are unavailable",
    EXIT_RESOURCE,
)
CLIENT_INTERRUPTED_ERROR = ClientError(
    "validation interrupted",
    EXIT_RESOURCE,
)
CLIENT_INSTALLATION_ERROR = ClientError(
    "client installation validation failed",
    EXIT_INSTALLATION,
)


class InvocationState:
    """Conservative diagnostic and cancellation state for one client process."""

    def __init__(self) -> None:
        self.validator_code_executed = "no"
        self.active_state_changed = "no"
        self.current_receipt = "unknown"
        self.accepted_output_may_be_visible = False
        self.cancellation_signal: int | None = None
        self.diagnostic_state = "uninstalled"
        self.original_signal_dispositions: dict[int, object] = {}
        self.signal_handler = self._request_cancellation

    def install_cancellation_handlers(self) -> None:
        self.diagnostic_state = "installing"
        try:
            for number in CANCELLATION_SIGNALS:
                self.original_signal_dispositions[number] = signal.getsignal(number)
                signal.signal(number, self.signal_handler)
        except BaseException:  # noqa: BLE001
            self.restore_cancellation_handlers_best_effort()
            self.diagnostic_state = "uninstalled"
            raise
        self.diagnostic_state = "installed"

    def restore_cancellation_handlers_best_effort(self) -> None:
        for number, disposition in self.original_signal_dispositions.items():
            try:
                signal.signal(number, disposition)
            except BaseException:  # noqa: BLE001
                continue

    def _request_cancellation(self, number: int, _frame: object) -> None:
        if self.cancellation_signal is None:
            self.cancellation_signal = number

    def raise_if_cancelled(self) -> None:
        if self.cancellation_signal is not None:
            raise CLIENT_INTERRUPTED_ERROR

    def prepare_success(self) -> set[signal.Signals]:
        cancellation_signals = set(CANCELLATION_SIGNALS)
        try:
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                cancellation_signals,
            )
            pending = signal.sigpending()
        except (AttributeError, OSError, ValueError) as error:
            raise ClientError(
                "accepted output signal transition failed",
                EXIT_RESOURCE,
            ) from error
        if self.cancellation_signal is not None or pending & cancellation_signals:
            raise CLIENT_INTERRUPTED_ERROR
        try:
            for number, disposition in self.original_signal_dispositions.items():
                signal.signal(number, disposition)
        except (OSError, ValueError) as error:
            raise ClientError(
                "accepted output signal transition failed",
                EXIT_RESOURCE,
            ) from error
        self.original_signal_dispositions.clear()
        return previous_mask

    def finish_success(self, previous_mask: set[signal.Signals]) -> None:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        except (OSError, ValueError) as error:
            raise ClientError(
                "accepted output signal transition failed",
                EXIT_RESOURCE,
            ) from error

    def note_validated_envelope(self) -> None:
        self.validator_code_executed = "yes"

    def note_receipt(self, receipt: dict[str, Any]) -> None:
        identity = receipt.get("content_sha256")
        if _digest(identity):
            self.current_receipt = f"sha256:{identity[:12]}"


class _ChildLifecycle:
    """Mutable exact-child ownership, distinct from permission to signal."""

    __slots__ = ("state",)

    def __init__(self, state: str = "owned") -> None:
        self.state = state

    @property
    def responsible(self) -> bool:
        return self.state in {"owned", "ambiguous"}

    @property
    def may_signal(self) -> bool:
        return self.state == "owned"


_RETRY_WILDCARD_CLOCK = 0
_RETRY_WILDCARD_WAIT = 1
_RETRY_GROUP_TERM_CLOCK = 2
_RETRY_GROUP_TERM = 3
_RETRY_GROUP_KILL_CLOCK = 4
_RETRY_GROUP_KILL = 5
_RETRY_DIRECT_KILL_CLOCK = 6
_RETRY_DIRECT_KILL = 7
_RETRY_GRACE_CLOCK = 8
_RETRY_EXACT_REAP_CLOCK = 9
_RETRY_EXACT_REAP = 10
_RETRY_DARWIN_WAIT_CLOCK = 11
_RETRY_DARWIN_PROBE_CLOCK = 12
_RETRY_DARWIN_GROUP_STATE = 13
_RETRY_DARWIN_KILL_CLOCK = 14
_RETRY_CONSUME_DIRECT_KILL_CLOCK = 15
_RETRY_CONSUME_DIRECT_KILL = 16
_RETRY_CONSUME_CLOCK = 17
_RETRY_CONSUME_EXACT_WAIT = 18
_RETRY_DARWIN_PRETERM_WAIT_CLOCK = 19
_RETRY_DARWIN_PRETERM_PROBE_CLOCK = 20
_RETRY_DARWIN_PRETERM_GROUP_STATE = 21
_RETRY_GROUP_TERM_OBSERVATION_CLOCK = 22
_RETRY_GROUP_TERM_OBSERVATION = 23
_RETRY_GROUP_KILL_OBSERVATION_CLOCK = 24
_RETRY_GROUP_KILL_OBSERVATION = 25
_RETRY_NON_DARWIN_GRACE_SLEEP = 26
_RETRY_DARWIN_GRACE_SLEEP = 27
_RETRY_DARWIN_KILL_SLEEP = 28
_RETRY_NON_DARWIN_WAIT_CLOCK = 29
_RETRY_NON_DARWIN_PROBE_CLOCK = 30
_RETRY_NON_DARWIN_WAIT_SLEEP = 31
_RETRY_NON_DARWIN_GROUP_STATE = 32
_RETRY_NON_DARWIN_KILL_CLOCK = 33
_RETRY_NON_DARWIN_KILL_SLEEP = 34
_RETRY_NON_DARWIN_PROC_CLOCK = 35


class _ConsumedCleanup:
    """The mutable exact result of direct-child cleanup after parent failure."""

    __slots__ = ("completed", "error", "lifecycle")

    def __init__(self) -> None:
        self.completed = False
        self.lifecycle = "lost"
        self.error: BaseException | None = None


class _CleanupContext:
    """Fixed cleanup state constructed before each launcher or writer fork."""

    __slots__ = (
        "cleanup_deadline",
        "cleanup_deadline_armed",
        "cleanup_deadline_attempted",
        "error",
        "fork_deadline_attempted",
        "fork_deadline_armed",
        "grace_cutoff",
        "kill_reap_seconds",
        "lifecycle",
        "lost_pid_deadline",
        "pid",
        "result",
        "retry_consume_clock",
        "retry_consume_direct_kill",
        "retry_consume_direct_kill_clock",
        "retry_consume_exact_wait",
        "retry_darwin_group_state",
        "retry_darwin_kill_clock",
        "retry_darwin_preterm_group_state",
        "retry_darwin_preterm_probe_clock",
        "retry_darwin_preterm_wait_clock",
        "retry_darwin_probe_clock",
        "retry_darwin_wait_clock",
        "retry_direct_kill",
        "retry_direct_kill_clock",
        "retry_exact_reap",
        "retry_exact_reap_clock",
        "retry_grace_clock",
        "retry_group_kill",
        "retry_group_kill_clock",
        "retry_group_kill_observation",
        "retry_group_kill_observation_clock",
        "retry_group_term",
        "retry_group_term_clock",
        "retry_group_term_observation",
        "retry_group_term_observation_clock",
        "retry_non_darwin_grace_sleep",
        "retry_non_darwin_group_state",
        "retry_non_darwin_kill_clock",
        "retry_non_darwin_kill_sleep",
        "retry_non_darwin_probe_clock",
        "retry_non_darwin_proc_clock",
        "retry_non_darwin_wait_clock",
        "retry_non_darwin_wait_sleep",
        "retry_darwin_grace_sleep",
        "retry_darwin_kill_sleep",
        "retry_wildcard_clock",
        "retry_wildcard_wait",
        "termination_grace_seconds",
        "wait_error",
        "wait_owned",
        "wait_status",
        "wildcard_waited",
        "writer_deadline",
        "writer_seconds",
    )

    def __init__(
        self,
        *,
        kill_reap_seconds: float,
        termination_grace_seconds: float,
        writer_seconds: float | None,
    ) -> None:
        self.error: BaseException | None = None
        self.lifecycle = _ChildLifecycle("lost")
        self.pid: int | None = None
        self.kill_reap_seconds = kill_reap_seconds
        self.termination_grace_seconds = termination_grace_seconds
        self.lost_pid_deadline = 0.0
        self.writer_deadline = 0.0
        self.writer_seconds = writer_seconds
        self.fork_deadline_armed = False
        self.fork_deadline_attempted = False
        self.cleanup_deadline = 0.0
        self.cleanup_deadline_armed = False
        self.cleanup_deadline_attempted = False
        self.grace_cutoff = 0.0
        self.retry_wildcard_clock = False
        self.retry_wildcard_wait = False
        self.retry_group_term_clock = False
        self.retry_group_term = False
        self.retry_group_term_observation_clock = False
        self.retry_group_term_observation = False
        self.retry_group_kill_clock = False
        self.retry_group_kill = False
        self.retry_group_kill_observation_clock = False
        self.retry_group_kill_observation = False
        self.retry_non_darwin_grace_sleep = False
        self.retry_non_darwin_wait_clock = False
        self.retry_non_darwin_probe_clock = False
        self.retry_non_darwin_proc_clock = False
        self.retry_non_darwin_wait_sleep = False
        self.retry_non_darwin_group_state = False
        self.retry_non_darwin_kill_clock = False
        self.retry_non_darwin_kill_sleep = False
        self.retry_darwin_grace_sleep = False
        self.retry_darwin_kill_sleep = False
        self.retry_direct_kill_clock = False
        self.retry_direct_kill = False
        self.retry_grace_clock = False
        self.retry_exact_reap_clock = False
        self.retry_exact_reap = False
        self.retry_darwin_wait_clock = False
        self.retry_darwin_probe_clock = False
        self.retry_darwin_group_state = False
        self.retry_darwin_kill_clock = False
        self.retry_darwin_preterm_wait_clock = False
        self.retry_darwin_preterm_probe_clock = False
        self.retry_darwin_preterm_group_state = False
        self.retry_consume_direct_kill_clock = False
        self.retry_consume_direct_kill = False
        self.retry_consume_clock = False
        self.retry_consume_exact_wait = False
        self.result = _ConsumedCleanup()
        self.wait_status: int | None = None
        self.wait_owned = False
        self.wait_error: BaseException | None = None
        self.wildcard_waited: int | None = None

    def record(self, error: BaseException | None) -> None:
        if self.error is None and error is not None:
            self.error = error

    def retry(self, slot: int) -> bool:
        if slot == _RETRY_WILDCARD_CLOCK:
            if self.retry_wildcard_clock:
                return False
            self.retry_wildcard_clock = True
        elif slot == _RETRY_WILDCARD_WAIT:
            if self.retry_wildcard_wait:
                return False
            self.retry_wildcard_wait = True
        elif slot == _RETRY_GROUP_TERM_CLOCK:
            if self.retry_group_term_clock:
                return False
            self.retry_group_term_clock = True
        elif slot == _RETRY_GROUP_TERM:
            if self.retry_group_term:
                return False
            self.retry_group_term = True
        elif slot == _RETRY_GROUP_KILL_CLOCK:
            if self.retry_group_kill_clock:
                return False
            self.retry_group_kill_clock = True
        elif slot == _RETRY_GROUP_KILL:
            if self.retry_group_kill:
                return False
            self.retry_group_kill = True
        elif slot == _RETRY_GROUP_TERM_OBSERVATION_CLOCK:
            if self.retry_group_term_observation_clock:
                return False
            self.retry_group_term_observation_clock = True
        elif slot == _RETRY_GROUP_TERM_OBSERVATION:
            if self.retry_group_term_observation:
                return False
            self.retry_group_term_observation = True
        elif slot == _RETRY_GROUP_KILL_OBSERVATION_CLOCK:
            if self.retry_group_kill_observation_clock:
                return False
            self.retry_group_kill_observation_clock = True
        elif slot == _RETRY_GROUP_KILL_OBSERVATION:
            if self.retry_group_kill_observation:
                return False
            self.retry_group_kill_observation = True
        elif slot == _RETRY_DIRECT_KILL_CLOCK:
            if self.retry_direct_kill_clock:
                return False
            self.retry_direct_kill_clock = True
        elif slot == _RETRY_DIRECT_KILL:
            if self.retry_direct_kill:
                return False
            self.retry_direct_kill = True
        elif slot == _RETRY_GRACE_CLOCK:
            if self.retry_grace_clock:
                return False
            self.retry_grace_clock = True
        elif slot == _RETRY_EXACT_REAP_CLOCK:
            if self.retry_exact_reap_clock:
                return False
            self.retry_exact_reap_clock = True
        elif slot == _RETRY_EXACT_REAP:
            if self.retry_exact_reap:
                return False
            self.retry_exact_reap = True
        elif slot == _RETRY_DARWIN_WAIT_CLOCK:
            if self.retry_darwin_wait_clock:
                return False
            self.retry_darwin_wait_clock = True
        elif slot == _RETRY_DARWIN_PROBE_CLOCK:
            if self.retry_darwin_probe_clock:
                return False
            self.retry_darwin_probe_clock = True
        elif slot == _RETRY_DARWIN_GROUP_STATE:
            if self.retry_darwin_group_state:
                return False
            self.retry_darwin_group_state = True
        elif slot == _RETRY_DARWIN_KILL_CLOCK:
            if self.retry_darwin_kill_clock:
                return False
            self.retry_darwin_kill_clock = True
        elif slot == _RETRY_CONSUME_DIRECT_KILL_CLOCK:
            if self.retry_consume_direct_kill_clock:
                return False
            self.retry_consume_direct_kill_clock = True
        elif slot == _RETRY_CONSUME_DIRECT_KILL:
            if self.retry_consume_direct_kill:
                return False
            self.retry_consume_direct_kill = True
        elif slot == _RETRY_CONSUME_CLOCK:
            if self.retry_consume_clock:
                return False
            self.retry_consume_clock = True
        elif slot == _RETRY_CONSUME_EXACT_WAIT:
            if self.retry_consume_exact_wait:
                return False
            self.retry_consume_exact_wait = True
        elif slot == _RETRY_DARWIN_PRETERM_WAIT_CLOCK:
            if self.retry_darwin_preterm_wait_clock:
                return False
            self.retry_darwin_preterm_wait_clock = True
        elif slot == _RETRY_DARWIN_PRETERM_PROBE_CLOCK:
            if self.retry_darwin_preterm_probe_clock:
                return False
            self.retry_darwin_preterm_probe_clock = True
        elif slot == _RETRY_DARWIN_PRETERM_GROUP_STATE:
            if self.retry_darwin_preterm_group_state:
                return False
            self.retry_darwin_preterm_group_state = True
        elif slot == _RETRY_NON_DARWIN_GRACE_SLEEP:
            if self.retry_non_darwin_grace_sleep:
                return False
            self.retry_non_darwin_grace_sleep = True
        elif slot == _RETRY_NON_DARWIN_WAIT_CLOCK:
            if self.retry_non_darwin_wait_clock:
                return False
            self.retry_non_darwin_wait_clock = True
        elif slot == _RETRY_NON_DARWIN_PROBE_CLOCK:
            if self.retry_non_darwin_probe_clock:
                return False
            self.retry_non_darwin_probe_clock = True
        elif slot == _RETRY_NON_DARWIN_WAIT_SLEEP:
            if self.retry_non_darwin_wait_sleep:
                return False
            self.retry_non_darwin_wait_sleep = True
        elif slot == _RETRY_NON_DARWIN_PROC_CLOCK:
            if self.retry_non_darwin_proc_clock:
                return False
            self.retry_non_darwin_proc_clock = True
        elif slot == _RETRY_NON_DARWIN_GROUP_STATE:
            if self.retry_non_darwin_group_state:
                return False
            self.retry_non_darwin_group_state = True
        elif slot == _RETRY_NON_DARWIN_KILL_CLOCK:
            if self.retry_non_darwin_kill_clock:
                return False
            self.retry_non_darwin_kill_clock = True
        elif slot == _RETRY_NON_DARWIN_KILL_SLEEP:
            if self.retry_non_darwin_kill_sleep:
                return False
            self.retry_non_darwin_kill_sleep = True
        elif slot == _RETRY_DARWIN_GRACE_SLEEP:
            if self.retry_darwin_grace_sleep:
                return False
            self.retry_darwin_grace_sleep = True
        elif slot == _RETRY_DARWIN_KILL_SLEEP:
            if self.retry_darwin_kill_sleep:
                return False
            self.retry_darwin_kill_sleep = True
        else:
            # Cleanup must preserve its owned-child path even if a caller passes
            # an invalid slot; converting this to an exception can abort reaping.
            return False
        return True

    def publish_pid(self, pid: int) -> None:
        self.pid = pid
        self.lifecycle.state = "owned"

    def reject_pid_publication(self, error: BaseException) -> None:
        self.record(error)
        self.lifecycle.state = "lost"
        self.pid = None

    def arm_cleanup(self, *, graceful: bool) -> bool:
        if self.cleanup_deadline_attempted:
            return self.cleanup_deadline_armed
        self.cleanup_deadline_attempted = True
        for _attempt in range(2):
            try:
                started_at = time.monotonic()
                grace_seconds = self.termination_grace_seconds if graceful else 0.0
                cleanup_deadline = started_at + grace_seconds + self.kill_reap_seconds
                self.cleanup_deadline = cleanup_deadline
                self.grace_cutoff = cleanup_deadline - self.kill_reap_seconds
                self.cleanup_deadline_armed = True
                return True
            except BaseException as error:  # noqa: BLE001
                self.record(error)
        return False

    def arm_fork_deadlines(self) -> bool:
        if self.fork_deadline_attempted:
            return self.fork_deadline_armed
        self.fork_deadline_attempted = True
        for _attempt in range(2):
            try:
                now = time.monotonic()
                self.lost_pid_deadline = now + self.kill_reap_seconds
                if self.writer_seconds is not None:
                    self.writer_deadline = now + self.writer_seconds
                self.fork_deadline_armed = True
                return True
            except BaseException as error:  # noqa: BLE001
                self.record(error)
        return False


def _cleanup_budget(value: object) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)) or value < 0:
        raise ClientError("cleanup budget is invalid", EXIT_RESOURCE)
    return float(value)


def _prepare_cleanup_context(
    *, writer_seconds: object | None = None
) -> _CleanupContext:
    kill_reap_seconds = _cleanup_budget(PROCESS_PROFILE["kill_reap_seconds"])
    termination_grace_seconds = _cleanup_budget(
        PROCESS_PROFILE["termination_grace_seconds"]
    )
    writer_budget = None if writer_seconds is None else _cleanup_budget(writer_seconds)
    return _CleanupContext(
        kill_reap_seconds=kill_reap_seconds,
        termination_grace_seconds=termination_grace_seconds,
        writer_seconds=writer_budget,
    )


def _child_lifecycle(target: object) -> _ChildLifecycle | None:
    lifecycle = getattr(target, "lifecycle", None)
    return lifecycle if isinstance(lifecycle, _ChildLifecycle) else None


def _responsible_for_child(target: object) -> bool:
    lifecycle = _child_lifecycle(target)
    return lifecycle is not None and lifecycle.responsible


def _may_signal_child(target: object) -> bool:
    lifecycle = _child_lifecycle(target)
    return lifecycle is not None and lifecycle.may_signal


def _mark_child_ambiguous(target: object) -> None:
    lifecycle = _child_lifecycle(target)
    if lifecycle is not None:
        lifecycle.state = "ambiguous"


def _mark_child_owned(target: object) -> None:
    lifecycle = _child_lifecycle(target)
    if lifecycle is not None:
        lifecycle.state = "owned"


def _mark_child_lost(target: object) -> None:
    lifecycle = _child_lifecycle(target)
    if lifecycle is not None:
        lifecycle.state = "lost"


def _mark_child_reaped(target: object) -> None:
    lifecycle = _child_lifecycle(target)
    if lifecycle is not None:
        lifecycle.state = "reaped"


class _OwnedProcess:
    """The exact launcher child and its captured output descriptors."""

    def __init__(
        self,
        pid: int,
        stdout: Any,
        stderr: Any,
        cleanup: _CleanupContext,
    ) -> None:
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr
        self.returncode: int | None = None
        self.cleanup = cleanup
        self.lifecycle = cleanup.lifecycle

    def wait(self, *, deadline: float) -> int:
        while self.returncode is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.pid, 0)
            try:
                waited, status = _waitpid_with_lifecycle(self, self.pid)
            except InterruptedError:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.pid, 0)
                continue
            if waited == self.pid:
                self.returncode = os.waitstatus_to_exitcode(status)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(self.pid, 0)
            time.sleep(min(0.01, remaining))
        return self.returncode


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _macos_descriptor_has_allow_acl(descriptor: int) -> bool:
    if sys.platform != "darwin":
        return False
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_get_tag_type.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    libc.acl_get_tag_type.restype = ctypes.c_int
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = libc.acl_get_fd_np(descriptor, _ACL_TYPE_EXTENDED)
    if not acl:
        error = ctypes.get_errno()
        if error in {errno.ENOENT, getattr(errno, "ENOATTR", 93)}:
            return False
        raise OSError(error, os.strerror(error))
    try:
        selector = _ACL_FIRST_ENTRY
        while True:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            if libc.acl_get_entry(acl, selector, ctypes.byref(entry)) != 0:
                error = ctypes.get_errno()
                if selector == _ACL_NEXT_ENTRY and error == errno.EINVAL:
                    return False
                raise OSError(error, os.strerror(error))
            tag = ctypes.c_int()
            ctypes.set_errno(0)
            if libc.acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            if tag.value == _ACL_EXTENDED_ALLOW:
                return True
            if tag.value != _ACL_EXTENDED_DENY:
                raise OSError(errno.EINVAL, "unsupported ACL entry type")
            selector = _ACL_NEXT_ENTRY
    finally:
        libc.acl_free(acl)


def _reject_macos_allow_acl(descriptor: int, label: str) -> None:
    try:
        has_allow_acl = _macos_descriptor_has_allow_acl(descriptor)
    except OSError as error:
        raise ValueError(f"{label} ACL cannot be verified") from error
    if has_allow_acl:
        raise ValueError(f"{label} has a permissive ACL entry")


def _client_source_generation_sha256(raw: bytes) -> str:
    assignment_prefix = b"CLIENT_SOURCE_" + b'GENERATION_SHA256 = "'
    assignment_start = raw.find(assignment_prefix)
    if (
        assignment_start < 0
        or (assignment_start > 0 and raw[assignment_start - 1] != 0x0A)
        or raw.find(assignment_prefix, assignment_start + 1) >= 0
    ):
        raise ValueError("client source generation identity is ambiguous")
    digest_start = assignment_start + len(assignment_prefix)
    digest_end = digest_start + 64
    if (
        raw[digest_end : digest_end + 2] != b'"\n'
        or re.fullmatch(rb"[0-9a-f]{64}", raw[digest_start:digest_end]) is None
    ):
        raise ValueError("client source generation identity is malformed")
    normalized = raw[:digest_start] + (b"0" * 64) + raw[digest_end:]
    identity = _sha(normalized)
    if raw[digest_start:digest_end].decode("ascii") != identity:
        raise ValueError("client source generation identity does not match")
    profile_prefix = b"CLIENT_RELEASE_" + b'PROFILE = "'
    profile_start = raw.find(profile_prefix)
    if (
        profile_start < 0
        or (profile_start > 0 and raw[profile_start - 1] != 0x0A)
        or raw.find(profile_prefix, profile_start + 1) >= 0
    ):
        raise ValueError("client release profile identity is ambiguous")
    profile_value_start = profile_start + len(profile_prefix)
    profile_value_end = raw.find(b'"\n', profile_value_start)
    if profile_value_end < 0:
        raise ValueError("client release profile identity is malformed")
    try:
        profile = raw[profile_value_start:profile_value_end].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("client release profile identity is malformed") from error
    if profile not in {"tw4-current", "b1-transition"}:
        raise ValueError("client release profile identity is unsupported")
    if profile != CLIENT_RELEASE_PROFILE:
        raise ValueError("loaded client release profile disagrees")
    return identity


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _token(value: object) -> bool:
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _same_json(left: object, right: object) -> bool:
    try:
        return _canonical(left) == _canonical(right)
    except (TypeError, ValueError):
        return False


def _number(token: str, parser: Any) -> Any:
    if len(token) > MAX_JSON_NUMBER_CHARACTERS:
        raise ValueError("JSON numeric token exceeds the limit")
    value = parser(token)
    if type(value) is float and not math.isfinite(value):
        raise ValueError("JSON contains an unsupported number")
    return value


def _json(
    raw: bytes,
    label: str,
    limit: int = MAX_DOCUMENT_BYTES,
) -> dict[str, Any]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"{label} contains a duplicate key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains an unsupported number: {value}")

    if len(raw) > limit:
        raise ValueError(f"{label} exceeds the byte limit")
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
        elif byte == ord('"'):
            in_string = True
        elif byte in (ord("{"), ord("[")):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError(f"{label} nesting exceeds the depth limit")
        elif byte in (ord("}"), ord("]")):
            depth -= 1
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
            parse_int=lambda token: _number(token, int),
            parse_float=lambda token: _number(token, float),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(  # noqa: TRY004
            f"{label} must be one canonical JSON document"
        )
    try:
        canonical = _canonical(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be one canonical JSON document") from error
    if raw != canonical + b"\n":
        raise ValueError(f"{label} must be one canonical JSON document")
    return value


def _document(value: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value.get("content_sha256"), str):
        raise ValueError(f"{label} content digest is missing")  # noqa: TRY004
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    if value["content_sha256"] != _sha(_canonical(unsigned)):
        raise ValueError(f"{label} content digest mismatch")
    return value


def _no_direct_children() -> bool:
    try:
        os.waitid(
            os.P_ALL,
            0,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError:
        return True
    except OSError as error:
        return error.errno == errno.ECHILD
    return False


def _sole_cpython_thread() -> bool:
    """Require CPython's complete current-frame inventory to contain only us."""
    try:
        current_thread = threading.get_ident()
        current_frames = sys._current_frames()
        return set(current_frames) == {current_thread} and threading.active_count() == 1
    except BaseException:  # noqa: BLE001
        return False


def _module_code_objects() -> frozenset[types.CodeType] | None:
    """Return the complete code-object graph defined by this client module."""
    try:
        module_code = _MODULE_CODE
        if (
            type(module_code) is not types.CodeType
            or type(__file__) is not str
            or module_code.co_filename != __file__
        ):
            return None
        pending = list(globals().values())
        seen_values: set[int] = set()
        codes: set[types.CodeType] = {module_code}
        while pending:
            value = pending.pop()
            if value is module_code:
                continue
            identity = id(value)
            if identity in seen_values:
                continue
            seen_values.add(identity)
            if isinstance(value, types.CodeType):
                if value.co_filename != __file__:
                    continue
                codes.add(value)
                pending.extend(
                    item for item in value.co_consts if isinstance(item, types.CodeType)
                )
            elif isinstance(value, type):
                if value.__module__ == __name__:
                    pending.extend(vars(value).values())
                    annotation = getattr(value, "__annotate__", None)
                    if annotation is not None:
                        pending.append(annotation)
            elif isinstance(value, (staticmethod, classmethod)):
                pending.append(value.__func__)
            elif isinstance(value, property):
                pending.extend(
                    accessor
                    for accessor in (value.fget, value.fset, value.fdel)
                    if accessor is not None
                )
            else:
                code = getattr(value, "__code__", None)
                if isinstance(code, types.CodeType) and code.co_filename == __file__:
                    pending.append(code)
                    annotation = getattr(value, "__annotate__", None)
                    if annotation is not None:
                        pending.append(annotation)
        return frozenset(codes)
    except BaseException:  # noqa: BLE001
        return None


def _instrumentation_is_clear() -> bool:
    """Reject tracing, profiling, and monitoring of any client code object."""
    try:
        if sys.gettrace() is not None or sys.getprofile() is not None:
            return False
        if threading.gettrace() is not None or threading.getprofile() is not None:
            return False
        monitoring = sys.monitoring
        codes = _module_code_objects()
        if codes is None:
            return False
        for tool_id in range(6):
            if monitoring.get_tool(tool_id) is not None:
                return False
            global_events = monitoring.get_events(tool_id)
            if type(global_events) is not int or global_events != 0:
                return False
            for code in codes:
                local_events = monitoring.get_local_events(tool_id, code)
                if type(local_events) is not int or local_events != 0:
                    return False
    except BaseException:  # noqa: BLE001
        return False
    return True


def _canonical_client_process(invocation: InvocationState | None = None) -> bool:
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] < MINIMUM_CPYTHON_VERSION
        or sys.platform not in {"darwin", "linux"}
    ):
        return False
    required_os_primitives = (
        "P_ALL",
        "P_PID",
        "WEXITED",
        "WEXITSTATUS",
        "WIFEXITED",
        "WNOHANG",
        "WNOWAIT",
        "dup2",
        "execve",
        "fdopen",
        "fork",
        "get_inheritable",
        "kill",
        "listdir",
        "open",
        "pipe",
        "scandir",
        "set_blocking",
        "set_inheritable",
        "setsid",
        "waitid",
        "waitpid",
        "waitstatus_to_exitcode",
    )
    required_signal_primitives = (
        "getitimer",
        "ITIMER_REAL",
        "SIGALRM",
        "pthread_sigmask",
        "setitimer",
        "sigpending",
    )
    if any(not hasattr(os, name) for name in required_os_primitives) or any(
        not hasattr(signal, name) for name in required_signal_primitives
    ):
        return False
    semantic_flags = {name: getattr(sys.flags, name, None) for name in CANONICAL_FLAGS}
    environment = dict(os.environ)
    if sys.platform == "darwin":
        apple_text_encoding = environment.pop("__CF_USER_TEXT_ENCODING", None)
        if (
            apple_text_encoding is not None
            and APPLE_TEXT_ENCODING.fullmatch(apple_text_encoding) is None
        ):
            return False
    try:
        blocked_signals = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        valid_signals = signal.valid_signals()
    except (AttributeError, OSError, ValueError):
        return False
    if blocked_signals:
        return False
    for timer_name in ("ITIMER_REAL", "ITIMER_VIRTUAL", "ITIMER_PROF"):
        if not hasattr(signal, timer_name):
            continue
        try:
            timer_state = signal.getitimer(getattr(signal, timer_name))
        except (OSError, ValueError):
            return False
        if timer_state != (0.0, 0.0):
            return False
    uncatchable = {
        getattr(signal, name)
        for name in ("SIGKILL", "SIGSTOP")
        if hasattr(signal, name)
    }
    for number in valid_signals - uncatchable:
        try:
            disposition = signal.getsignal(number)
        except (OSError, ValueError):
            return False
        policy_disposition = disposition
        if invocation is not None and number in invocation.original_signal_dispositions:
            if disposition != invocation.signal_handler:
                return False
            policy_disposition = invocation.original_signal_dispositions[number]
        if number in PYTHON_RESTORED_SIGNALS:
            if policy_disposition != signal.SIG_IGN:
                return False
        elif number == getattr(signal, "SIGCHLD", None):
            if policy_disposition != signal.SIG_DFL:
                return False
        elif number in CANCELLATION_SIGNALS:
            if policy_disposition == signal.SIG_IGN:
                return False
        elif policy_disposition != signal.SIG_DFL:
            return False
    return (
        Path(sys.executable).is_absolute()
        and sys.implementation.name == "cpython"
        and not sys.warnoptions
        and sys._xoptions == {"disable-remote-debug": True}
        and _sole_cpython_thread()
        and semantic_flags == CANONICAL_FLAGS
        and _same_json(environment, PROCESS_PROFILE["environment"])
        and _instrumentation_is_clear()
        and _no_direct_children()
    )


def _installed_root() -> Path:
    return Path(pwd.getpwuid(os.geteuid()).pw_dir).joinpath(
        ".local", "libexec", "task-witness"
    )


def _absolute(value: object, label: str) -> Path:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be absolute and traversal-free")
    return path


def _descriptor_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
    )


def _filesystem_identity(metadata: os.stat_result) -> FilesystemIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _full_filesystem_identity(metadata: os.stat_result) -> list[int]:
    return list(_filesystem_identity(metadata))


def _change_token(metadata: os.stat_result) -> ChangeToken:
    return metadata.st_mtime_ns, metadata.st_ctime_ns


def _close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _open_directory_chain(
    path: Path,
    label: str,
    *,
    private_final: bool = True,
    track_final_changes: bool = True,
    required_final_mode: int | None = None,
) -> list[DirectoryRecord]:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be absolute and traversal-free")
    required = ("O_CLOEXEC", "O_NOFOLLOW", "O_DIRECTORY")
    missing = [name for name in required if not hasattr(os, name)]
    if missing:
        raise ValueError(
            f"required descriptor primitives unavailable: {', '.join(missing)}"
        )
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
    chain: list[DirectoryRecord] = []
    pending_descriptor = None
    try:
        descriptor = os.open("/", flags)
        pending_descriptor = descriptor
        root_metadata = os.fstat(descriptor)
        parts = path.parts[1:]
        chain.append(
            (
                descriptor,
                _descriptor_identity(root_metadata),
                None,
                "",
                (
                    _change_token(root_metadata)
                    if track_final_changes and not parts
                    else None
                ),
            )
        )
        pending_descriptor = None
        for index, part in enumerate(parts):
            parent = descriptor
            before = os.stat(part, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError(f"{label} contains a non-directory component")
            descriptor = os.open(part, flags, dir_fd=parent)
            pending_descriptor = descriptor
            opened = os.fstat(descriptor)
            visible = os.stat(part, dir_fd=parent, follow_symlinks=False)
            identity = _descriptor_identity(opened)
            track_changes = track_final_changes and index == len(parts) - 1
            if (
                identity != _descriptor_identity(before)
                or identity != _descriptor_identity(visible)
                or (
                    track_changes
                    and (
                        _change_token(opened) != _change_token(before)
                        or _change_token(opened) != _change_token(visible)
                    )
                )
            ):
                raise ValueError(f"{label} changed during descriptor open")
            chain.append(
                (
                    descriptor,
                    identity,
                    parent,
                    part,
                    _change_token(opened) if track_changes else None,
                )
            )
            pending_descriptor = None
        if private_final:
            root = os.fstat(chain[-1][0])
            mode = stat.S_IMODE(root.st_mode)
            if (
                root.st_uid != os.geteuid()
                or mode & 0o077
                or (required_final_mode is not None and mode != required_final_mode)
            ):
                raise ValueError(f"{label} is not current-user private")
            _reject_macos_allow_acl(chain[-1][0], label)
        return chain
    except BaseException:
        descriptors = [item[0] for item in chain]
        if pending_descriptor is not None:
            descriptors.append(pending_descriptor)
        _close_descriptors(descriptors)
        raise


def _recheck_directory_chain(
    chain: list[DirectoryRecord],
    label: str,
) -> None:
    for descriptor, identity, parent, name, change_token in chain:
        opened = os.fstat(descriptor)
        if _descriptor_identity(opened) != identity:
            raise ValueError(f"{label} descriptor identity changed")
        if change_token is not None and _change_token(opened) != change_token:
            raise ValueError(f"{label} descriptor changed")
        if change_token is not None:
            _reject_macos_allow_acl(descriptor, label)
        if parent is not None:
            visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _descriptor_identity(visible) != identity:
                raise ValueError(f"{label} visible identity changed")
            if change_token is not None and _change_token(visible) != change_token:
                raise ValueError(f"{label} visible mapping changed")


def _activation_lock_binding(root: Path, metadata: os.stat_result) -> dict[str, object]:
    return {
        "path": str(root / "activation.lock"),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "owner": metadata.st_uid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _canonical_activation_lock_metadata(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and metadata.st_size == 0
    )


def _file_descriptor_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    return (
        *_descriptor_identity(metadata),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_descriptor(
    descriptor: int,
    identity: tuple[int, int, int, int, int, int, int],
    label: str,
    limit: int,
) -> bytes:
    if os.fstat(descriptor).st_size > limit:
        raise ValueError(f"{label} exceeds the byte limit")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(64 * 1024, limit - total + 1))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError(f"{label} exceeds the byte limit")
    if _file_descriptor_identity(os.fstat(descriptor)) != identity:
        raise ValueError(f"{label} changed during descriptor read")
    return b"".join(chunks)


def _open_file_at(
    parent: int,
    name: str,
    label: str,
    limit: int,
    *,
    private: bool,
) -> tuple[int, tuple[int, int, int, int, int, int, int], bytes]:
    if "/" in name or name in {"", ".", ".."}:
        raise ValueError(f"{label} has an unsafe relative path")
    descriptor = None
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or (
            private
            and (
                before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_nlink != 1
            )
        ):
            raise ValueError(f"{label} is not an accepted regular file")
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
        identity = _file_descriptor_identity(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (
                private
                and (
                    opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) & 0o077
                    or opened.st_nlink != 1
                )
            )
            or identity != _file_descriptor_identity(before)
            or identity != _file_descriptor_identity(visible)
        ):
            raise ValueError(f"{label} changed during descriptor open")
        if private:
            _reject_macos_allow_acl(descriptor, label)
        return (
            descriptor,
            identity,
            _read_descriptor(
                descriptor,
                identity,
                label,
                limit,
            ),
        )
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _open_directory_at(
    directories: list[DirectoryRecord],
    parent: int,
    name: str,
    label: str,
    *,
    private: bool,
    required_mode: int | None = None,
) -> int:
    if "/" in name or name in {"", ".", ".."}:
        raise ValueError(f"{label} has an unsafe relative path")
    descriptor = None
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        before_mode = stat.S_IMODE(before.st_mode)
        if not stat.S_ISDIR(before.st_mode) or (
            private
            and (
                before.st_uid != os.geteuid()
                or before_mode & 0o077
                or (required_mode is not None and before_mode != required_mode)
            )
        ):
            raise ValueError(f"{label} is not an accepted directory")
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY,
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
        identity = _descriptor_identity(opened)
        opened_mode = stat.S_IMODE(opened.st_mode)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (
                private
                and (
                    opened.st_uid != os.geteuid()
                    or opened_mode & 0o077
                    or (required_mode is not None and opened_mode != required_mode)
                )
            )
            or identity != _descriptor_identity(before)
            or identity != _descriptor_identity(visible)
            or _change_token(opened) != _change_token(before)
            or _change_token(opened) != _change_token(visible)
        ):
            raise ValueError(f"{label} changed during descriptor open")
        if private:
            _reject_macos_allow_acl(descriptor, label)
        directories.append(
            (
                descriptor,
                identity,
                parent,
                name,
                _change_token(opened),
            )
        )
        return descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _capture_file(
    files: list[FileRecord],
    parent: int,
    name: str,
    label: str,
    limit: int,
    *,
    private: bool,
) -> tuple[bytes, os.stat_result]:
    descriptor, identity, raw = _open_file_at(
        parent,
        name,
        label,
        limit,
        private=private,
    )
    files.append((descriptor, identity, parent, name, raw, label, limit, private))
    return raw, os.fstat(descriptor)


def _require_activation_transaction_absent(root_descriptor: int) -> None:
    try:
        os.stat(
            "transaction.json",
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    raise ValueError("activation transaction blocks public validation")


def _capture_absolute_file(
    directories: list[DirectoryRecord],
    files: list[FileRecord],
    path: Path,
    label: str,
    limit: int,
    *,
    private: bool,
) -> tuple[bytes, os.stat_result]:
    chain = _open_directory_chain(
        path.parent,
        f"{label} parent",
        private_final=private,
        track_final_changes=private,
    )
    directories.extend(chain)
    return _capture_file(
        files,
        chain[-1][0],
        path.name,
        label,
        limit,
        private=private,
    )


def _recheck_files(files: list[FileRecord]) -> None:
    for (
        descriptor,
        identity,
        parent,
        name,
        expected_raw,
        label,
        limit,
        private,
    ) in files:
        if (
            _file_descriptor_identity(
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            )
            != identity
            or _read_descriptor(descriptor, identity, label, limit) != expected_raw
        ):
            raise ValueError(f"{label} changed during validation")
        if private:
            _reject_macos_allow_acl(descriptor, label)


def _capture_inventory(
    inventories: list[InventoryRecord],
    descriptor: int,
    expected: set[str],
    label: str,
) -> None:
    actual = frozenset(os.listdir(descriptor))
    if actual != frozenset(expected):
        raise ValueError(f"{label} inventory drift")
    inventories.append((descriptor, actual, label))


def _recheck_inventories(inventories: list[InventoryRecord]) -> None:
    for descriptor, expected, label in inventories:
        if frozenset(os.listdir(descriptor)) != expected:
            raise ValueError(f"{label} inventory changed during validation")


def _bundle_names(descriptor: int) -> frozenset[str]:
    names: set[str] = set()
    with os.scandir(descriptor) as entries:
        for entry in entries:
            names.add(entry.name)
            if len(names) > MAX_BUNDLE_FILES:
                raise ValueError("bundle exceeds the file limit")
    return frozenset(names)


class _BundleSnapshot:
    def __init__(
        self,
        path: Path,
        directories: list[DirectoryRecord],
        files: dict[
            str,
            tuple[int, tuple[int, int, int, int, int, int, int], bytes],
        ],
    ) -> None:
        self.path = path
        self.directories = directories
        self.files = files
        self.root = directories[-1][0]
        inventory = [
            {
                "name": name,
                "length": len(raw),
                "sha256": _sha(raw),
            }
            for name, (_, _, raw) in sorted(files.items())
        ]
        self.sha256 = _sha(
            _canonical(
                {
                    "contract": BUNDLE_INVENTORY_CONTRACT,
                    "files": inventory,
                }
            )
        )

    @classmethod
    def open(cls, path: Path) -> _BundleSnapshot:
        directories = _open_directory_chain(path, "bundle")
        files: dict[
            str,
            tuple[int, tuple[int, int, int, int, int, int, int], bytes],
        ] = {}
        try:
            names = _bundle_names(directories[-1][0])
            total = 0
            for name in sorted(names):
                descriptor, identity, raw = _open_file_at(
                    directories[-1][0],
                    name,
                    "bundle child",
                    MAX_BUNDLE_FILE_BYTES,
                    private=True,
                )
                files[name] = (descriptor, identity, raw)
                total += len(raw)
                if total > MAX_BUNDLE_BYTES:
                    raise ValueError("bundle exceeds the byte limit")
            return cls(path, directories, files)
        except BaseException:
            _close_descriptors([item[0] for item in files.values()])
            _close_descriptors([item[0] for item in directories])
            raise

    def recheck(self) -> None:
        _recheck_directory_chain(self.directories, "bundle")
        try:
            current_names = _bundle_names(self.root)
        except ValueError as error:
            raise ValueError("bundle inventory changed during validation") from error
        if current_names != frozenset(self.files):
            raise ValueError("bundle inventory changed during validation")
        for name, (descriptor, identity, expected_raw) in self.files.items():
            _reject_macos_allow_acl(descriptor, "bundle child")
            if (
                _file_descriptor_identity(
                    os.stat(name, dir_fd=self.root, follow_symlinks=False)
                )
                != identity
                or _read_descriptor(
                    descriptor,
                    identity,
                    "bundle child",
                    MAX_BUNDLE_FILE_BYTES,
                )
                != expected_raw
            ):
                raise ValueError("bundle child changed during validation")

    def close(self) -> None:
        _close_descriptors([item[0] for item in self.files.values()])
        _close_descriptors([item[0] for item in self.directories])


def _validate_file_binding(
    value: object,
    expected_path: Path,
    raw: bytes,
    metadata: os.stat_result,
    label: str,
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "length",
        "sha256",
        "owner",
        "mode",
    }:
        raise ValueError(f"{label} identity schema drift")
    path = _absolute(value["path"], f"{label} path")
    if path != expected_path:
        raise ValueError(f"{label} path mismatch")
    if not _same_json(
        value,
        {
            "path": str(path),
            "length": len(raw),
            "sha256": _sha(raw),
            "owner": metadata.st_uid,
            "mode": stat.S_IMODE(metadata.st_mode),
        },
    ):
        raise ValueError(f"{label} identity mismatch")


def _installed_file_binding(
    path: Path,
    raw: bytes,
    metadata: os.stat_result,
) -> dict[str, object]:
    return {
        "path": str(path),
        "length": len(raw),
        "sha256": _sha(raw),
        "owner": metadata.st_uid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _historical_trust_registry(
    value: object,
    root: Path,
) -> dict[str, dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(  # noqa: TRY004
            "deployment historical trust-context registry schema drift"
        )
    registry: dict[str, dict[str, object]] = {}
    paths: set[Path] = set()
    ordered_digests: list[str] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "state"}
            or not _digest(item.get("sha256"))
            or item.get("state") not in {"historical-usable", "revoked"}
        ):
            raise ValueError(
                "deployment historical trust-context registry schema drift"
            )
        digest = item["sha256"]
        path = _absolute(item["path"], "historical trust-context path")
        if (
            item["path"] != str(path)
            or path != root / "trust" / "contexts" / f"sha256-{digest}.json"
            or digest in registry
            or path in paths
        ):
            raise ValueError("deployment historical trust-context registry mismatch")
        registry[digest] = item
        paths.add(path)
        ordered_digests.append(digest)
    if ordered_digests != sorted(ordered_digests):
        raise ValueError("deployment historical trust-context registry is unordered")
    return registry


def _exact_dict(
    value: object,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} schema drift")
    return value


def _lifecycle_entry(
    value: object,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    item = _exact_dict(
        value,
        fields | {"state", "usable_for_new_publication"},
        label,
    )
    if (
        item["state"] not in {"active", "historical-usable", "revoked"}
        or type(item["usable_for_new_publication"]) is not bool
    ):
        raise ValueError(f"{label} lifecycle is invalid")
    return item


def _validator_implementation_identity(
    contract: str,
    entrypoint: str,
    modules: list[tuple[str, str]],
) -> str:
    return _sha(
        _canonical(
            {
                "contract": VALIDATOR_ARTIFACT_MANIFEST_CONTRACT,
                "validator_contract": contract,
                "entrypoint_module": entrypoint,
                "modules": [
                    {"name": name, "content_sha256": digest} for name, digest in modules
                ],
            }
        )
    )


def _validate_retained_trust_context(
    raw: bytes,
    root: Path,
    trust_directory: int,
    directories: list[DirectoryRecord],
    files: list[FileRecord],
    inventories: list[InventoryRecord],
    label: str,
) -> dict[str, Any]:
    context = _document(_json(raw, label), label)
    if (
        set(context)
        != {
            "schema_version",
            "contract",
            "producers",
            "issuers",
            "validators",
            "content_sha256",
        }
        or type(context["schema_version"]) is not int
        or context["schema_version"] != 1
        or context["contract"] != TRUST_CONTEXT_CONTRACT
    ):
        raise ValueError(f"{label} contract mismatch")

    validators_directory = _open_directory_at(
        directories,
        trust_directory,
        "validators",
        "retained validator directory",
        private=True,
        required_mode=0o700,
    )
    validator_identities: set[tuple[str, str, str]] = set()
    validator_authorities: set[tuple[str, str]] = set()
    validator_order: list[tuple[str, str, str]] = []
    validator_lifecycles: dict[tuple[str, str, str], tuple[str, bool]] = {}
    validator_modules: dict[
        tuple[str, str, str],
        list[dict[str, object]],
    ] = {}
    validator_directories: dict[str, int] = {}
    retained_modules: dict[str, dict[str, object]] = {}
    validators = context["validators"]
    if not isinstance(validators, list) or not validators:
        raise ValueError(f"{label} validators are missing")
    for index, value in enumerate(validators):
        item_label = f"{label} validator[{index}]"
        item = _lifecycle_entry(
            value,
            {
                "validator_id",
                "contract",
                "implementation_sha256",
                "entrypoint",
                "modules",
            },
            item_label,
        )
        validator_id = item["validator_id"]
        contract = item["contract"]
        implementation = item["implementation_sha256"]
        entrypoint = item["entrypoint"]
        if (
            not _token(validator_id)
            or not _text(contract)
            or not _digest(implementation)
            or not _token(entrypoint)
        ):
            raise ValueError(f"{item_label} identity is invalid")
        identity = (validator_id, contract, implementation)
        authority = (validator_id, contract)
        if identity in validator_identities or authority in validator_authorities:
            raise ValueError(f"{label} has a duplicate validator authority")
        validator_identities.add(identity)
        validator_authorities.add(authority)
        validator_order.append(identity)
        validator_lifecycles[identity] = (
            item["state"],
            item["usable_for_new_publication"],
        )

        modules = item["modules"]
        if (
            not isinstance(modules, list)
            or not modules
            or len(modules) > MAX_VALIDATOR_MODULES
        ):
            raise ValueError(f"{item_label} module inventory is invalid")
        generation_name = f"sha256-{implementation}"
        generation_directory = validator_directories.get(generation_name)
        if generation_directory is None:
            generation_directory = _open_directory_at(
                directories,
                validators_directory,
                generation_name,
                f"{item_label} retained generation",
                private=True,
                required_mode=0o700,
            )
            validator_directories[generation_name] = generation_directory
        framed_modules: list[tuple[str, str]] = []
        module_names: set[str] = set()
        module_paths: set[Path] = set()
        module_bindings: list[dict[str, object]] = []
        total_bytes = 0
        for module_index, module_value in enumerate(modules):
            module_label = f"{item_label} module[{module_index}]"
            module = _exact_dict(
                module_value,
                {"name", "path", "sha256"},
                module_label,
            )
            name = module["name"]
            digest = module["sha256"]
            if not _token(name) or not _digest(digest):
                raise ValueError(f"{module_label} identity is invalid")
            path = _absolute(module["path"], f"{module_label} path")
            expected_path = (
                root / "trust" / "validators" / generation_name / f"{name}.py"
            )
            if (
                path != expected_path
                or name in module_names
                or path in module_paths
                or (module_index == 0 and name != entrypoint)
            ):
                raise ValueError(f"{module_label} retained path mismatch")
            module_raw, _ = _capture_file(
                files,
                generation_directory,
                path.name,
                module_label,
                MAX_VALIDATOR_ARTIFACT_BYTES,
                private=True,
            )
            if _sha(module_raw) != digest:
                raise ValueError(f"{module_label} content digest mismatch")
            total_bytes += len(module_raw)
            if total_bytes > MAX_VALIDATOR_ARTIFACT_BYTES:
                raise ValueError(f"{item_label} artifacts exceed the byte limit")
            module_names.add(name)
            module_paths.add(path)
            framed_modules.append((name, digest))
            retained_binding = {
                "name": name,
                "path": str(path),
                "length": len(module_raw),
                "sha256": digest,
            }
            prior_binding = retained_modules.get(str(path))
            if prior_binding is not None and not _same_json(
                prior_binding,
                retained_binding,
            ):
                raise ValueError(f"{module_label} retained identity disagrees")
            retained_modules[str(path)] = retained_binding
            module_bindings.append(retained_binding)
        if (
            entrypoint not in module_names
            or _validator_implementation_identity(
                contract,
                entrypoint,
                framed_modules,
            )
            != implementation
        ):
            raise ValueError(f"{item_label} implementation identity mismatch")
        _capture_inventory(
            inventories,
            generation_directory,
            {f"{name}.py" for name in module_names},
            f"{item_label} retained generation",
        )
        validator_modules[identity] = module_bindings
    if validator_order != sorted(validator_order):
        raise ValueError(f"{label} validators are not canonically ordered")

    producer_validator_bindings: dict[
        tuple[str, str, str],
        tuple[str, str, str],
    ] = {}
    producer_lifecycles: dict[tuple[str, str, str], tuple[str, bool]] = {}
    category_definitions = (
        (
            "producer",
            "producer_id",
            {
                "producer_id",
                "contract",
                "implementation_sha256",
                "validator_id",
                "validator_contract",
                "validator_implementation_sha256",
            },
        ),
        (
            "issuer",
            "issuer_id",
            {
                "issuer_id",
                "contract",
                "implementation_sha256",
                "capabilities",
            },
        ),
    )
    for category, identifier_field, fields in category_definitions:
        values = context[f"{category}s"]
        if not isinstance(values, list) or not values:
            raise ValueError(f"{label} {category}s are missing")
        identities: set[tuple[str, str, str]] = set()
        authorities: set[tuple[str, str]] = set()
        ordered_identities: list[tuple[str, str, str]] = []
        for index, value in enumerate(values):
            item_label = f"{label} {category}[{index}]"
            item = _lifecycle_entry(value, fields, item_label)
            identifier = item[identifier_field]
            contract = item["contract"]
            implementation = item["implementation_sha256"]
            if (
                not _token(identifier)
                or not _text(contract)
                or not _digest(implementation)
            ):
                raise ValueError(f"{item_label} identity is invalid")
            identity = (identifier, contract, implementation)
            authority = (identifier, contract)
            if identity in identities or authority in authorities:
                raise ValueError(f"{label} has a duplicate {category} authority")
            identities.add(identity)
            authorities.add(authority)
            ordered_identities.append(identity)
            if category == "producer":
                binding = (
                    item["validator_id"],
                    item["validator_contract"],
                    item["validator_implementation_sha256"],
                )
                if (
                    not _token(binding[0])
                    or not _text(binding[1])
                    or not _digest(binding[2])
                ):
                    raise ValueError(f"{item_label} validator binding is invalid")
                producer_validator_bindings[identity] = binding
                producer_lifecycles[identity] = (
                    item["state"],
                    item["usable_for_new_publication"],
                )
            else:
                capabilities = item["capabilities"]
                if (
                    not isinstance(capabilities, list)
                    or not capabilities
                    or len(capabilities) != len(set(capabilities))
                    or any(not _token(capability) for capability in capabilities)
                    or capabilities != sorted(capabilities)
                ):
                    raise ValueError(f"{item_label} capabilities are invalid")
        if ordered_identities != sorted(ordered_identities):
            raise ValueError(f"{label} {category}s are not canonically ordered")
    if any(
        binding not in validator_identities
        for binding in producer_validator_bindings.values()
    ):
        raise ValueError(f"{label} producer validator binding is unregistered")
    return {
        "producer_bindings": producer_validator_bindings,
        "producer_lifecycles": producer_lifecycles,
        "validator_lifecycles": validator_lifecycles,
        "validator_modules": validator_modules,
        "retained_modules": retained_modules,
        "role_inventory": {
            category: context[category]
            for category in ("producers", "issuers", "validators")
        },
    }


def _lifecycle_allows(
    lifecycle: tuple[str, bool] | None,
    *,
    historical: bool,
) -> bool:
    if lifecycle is None:
        return False
    state, usable_for_new_publication = lifecycle
    return state != "revoked" and (
        historical or (state == "active" and usable_for_new_publication)
    )


def _runtime_identity(active: dict[str, Any]) -> str:
    return _sha(
        _canonical(
            {
                "contract": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
                "runtime_contract": active["runtime_contract"],
                "entrypoint_role": "entrypoint",
                "payloads": active["payloads"],
            }
        )
    )


def _validate_active_runtime(
    active: dict[str, Any],
    root_descriptor: int,
    directories: list[DirectoryRecord],
    files: list[FileRecord],
    inventories: list[InventoryRecord],
) -> str:
    if (
        set(active)
        != {
            "schema_version",
            "contract",
            "generation",
            "runtime_contract",
            "interpreter",
            "public_release",
            "payloads",
            "content_sha256",
        }
        or type(active["schema_version"]) is not int
        or active["schema_version"] != 1
        or active["contract"] != ACTIVE_CONTRACT
        or not isinstance(active["generation"], str)
        or GENERATION.fullmatch(active["generation"]) is None
        or active["runtime_contract"] != RUNTIME_CONTRACT
    ):
        raise ValueError("active record contract mismatch")
    interpreter = active["interpreter"]
    version = interpreter.get("version") if isinstance(interpreter, dict) else None
    if (
        not isinstance(interpreter, dict)
        or set(interpreter) != {"executable", "implementation", "version"}
        or not _text(interpreter["executable"])
        or not _text(interpreter["implementation"])
        or not isinstance(version, dict)
        or set(version) != {"major", "minor", "micro"}
        or any(type(version[part]) is not int for part in ("major", "minor", "micro"))
    ):
        raise ValueError("active interpreter identity is invalid")
    release = active["public_release"]
    if (
        not isinstance(release, dict)
        or set(release) != {"repository", "revision"}
        or not isinstance(release["repository"], str)
        or REPOSITORY.fullmatch(release["repository"]) is None
        or not isinstance(release["revision"], str)
        or GIT_REVISION.fullmatch(release["revision"]) is None
    ):
        raise ValueError("active public release identity is invalid")
    payloads = active["payloads"]
    if not isinstance(payloads, list) or len(payloads) != len(RUNTIME_PAYLOAD_SPECS):
        raise ValueError("active runtime payload inventory is invalid")
    for item, (role, relative_path) in zip(payloads, RUNTIME_PAYLOAD_SPECS):
        if (
            not isinstance(item, dict)
            or set(item) != {"role", "relative_path", "length", "sha256"}
            or item["role"] != role
            or item["relative_path"] != relative_path
            or type(item["length"]) is not int
            or item["length"] < 0
            or item["length"] > MAX_RUNTIME_PAYLOAD_BYTES
            or not _digest(item["sha256"])
        ):
            raise ValueError("active runtime payload inventory is invalid")
    runtime_sha256 = _runtime_identity(active)
    if active["generation"] != f"sha256-{runtime_sha256}":
        raise ValueError("active runtime generation identity mismatch")

    generations_directory = _open_directory_at(
        directories,
        root_descriptor,
        "generations",
        "runtime generations directory",
        private=True,
        required_mode=0o700,
    )
    generation_directory = _open_directory_at(
        directories,
        generations_directory,
        active["generation"],
        "active runtime generation",
        private=True,
        required_mode=0o700,
    )
    expected_names = {relative_path for _, relative_path in RUNTIME_PAYLOAD_SPECS}
    _capture_inventory(
        inventories,
        generation_directory,
        expected_names,
        "active runtime generation",
    )
    for item in payloads:
        raw, _ = _capture_file(
            files,
            generation_directory,
            item["relative_path"],
            f"active runtime payload {item['role']}",
            MAX_RUNTIME_PAYLOAD_BYTES,
            private=True,
        )
        if len(raw) != item["length"] or _sha(raw) != item["sha256"]:
            raise ValueError("active runtime payload binding mismatch")
    return runtime_sha256


def _interpreter_identity(executable: Path, raw: bytes) -> dict[str, object]:
    return {
        "executable": str(executable),
        "implementation": sys.implementation.name,
        "version": dict(zip(("major", "minor", "micro"), sys.version_info[:3])),
        "executable_sha256": _sha(raw),
    }


def _nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _filesystem_identity_vector(value: object, label: str) -> list[int]:
    if (
        type(value) is not list
        or len(value) != 8
        or any(not _nonnegative_integer(item) for item in value)
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _sorted_token_inventory(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or len(value) > 64
        or any(not _token(item) for item in value)
        or value != sorted(value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{label} is not a sorted token inventory")
    return value


def _manifest_author(value: object, label: str) -> dict[str, Any]:
    author = _exact_dict(value, {"name", "url"}, label)
    if not _text(author["name"]) or not _text(author["url"]):
        raise ValueError(f"{label} is invalid")
    return author


def _validate_compatibility_policy(
    raw: bytes,
    *,
    receipt_profile: str = CURRENT_RECEIPT_PROFILE,
) -> dict[str, Any]:
    label = "compatibility policy"
    if receipt_profile == CURRENT_RECEIPT_PROFILE:
        expected_contracts = CLIENT_CONTRACTS
    elif receipt_profile == BRIDGE_LEGACY_RECEIPT_PROFILE:
        expected_contracts = LEGACY_CLIENT_CONTRACTS
    else:
        raise ValueError("compatibility policy receipt profile is unsupported")
    policy = _document(_json(raw, label), label)
    if (
        set(policy)
        != {
            "schema_version",
            "contract",
            "source",
            "providers",
            "control_surface",
            "content_sha256",
        }
        or type(policy["schema_version"]) is not int
        or policy["schema_version"] != 2
        or policy["contract"] != COMPATIBILITY_POLICY_CONTRACT
    ):
        raise ValueError("compatibility policy contract mismatch")
    control_surface = _exact_dict(
        policy["control_surface"],
        {"schema_version", "contract", "process_profile", "contracts"},
        "compatibility policy control surface",
    )
    process_profile = _exact_dict(
        control_surface["process_profile"],
        set(PROCESS_PROFILE),
        "compatibility policy process profile",
    )
    contracts = _exact_dict(
        control_surface["contracts"],
        set(expected_contracts),
        "compatibility policy client contracts",
    )
    if (
        type(control_surface["schema_version"]) is not int
        or control_surface["schema_version"] != 1
        or control_surface["contract"] != CONTROL_SURFACE_CONTRACT
        or not _same_json(process_profile, PROCESS_PROFILE)
        or not _same_json(contracts, expected_contracts)
    ):
        raise ValueError("compatibility policy control surface mismatch")
    source = _exact_dict(
        policy["source"],
        {
            "plugin_id",
            "mode",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "source_authority",
            "details",
        },
        "compatibility policy source",
    )
    if (
        not _token(source["plugin_id"])
        or source["mode"]
        not in {"harness_snapshot", "publisher_channel", "exact_release"}
        or not _token(source["publisher_id"])
        or not _text(source["repository_id"])
        or REPOSITORY.fullmatch(source["repository_id"]) is None
        or not _text(source["repository_url"])
        or not _token(source["source_authority"])
    ):
        raise ValueError("compatibility policy source identity is invalid")
    _manifest_author(
        source["manifest_author"],
        "compatibility policy manifest author",
    )
    if source["mode"] in {"harness_snapshot", "publisher_channel"}:
        details = _exact_dict(
            source["details"],
            {"channel", "trust_class", "lineage_id"},
            "compatibility policy source details",
        )
        if any(
            not _token(details[key]) for key in ("channel", "trust_class", "lineage_id")
        ):
            raise ValueError("compatibility policy source details are invalid")
    else:
        details = _exact_dict(
            source["details"],
            {"trust_class"},
            "compatibility policy exact-release details",
        )
        if not _token(details["trust_class"]):
            raise ValueError("compatibility policy exact-release details are invalid")

    providers = policy["providers"]
    if not isinstance(providers, list) or len(providers) > MAX_VALIDATORS:
        raise ValueError("compatibility policy provider inventory is invalid")
    provider_keys: list[str] = []
    for provider_index, value in enumerate(providers):
        provider_label = f"compatibility policy provider[{provider_index}]"
        provider = _exact_dict(
            value,
            {
                "plugin_id",
                "authority_profile",
                "producers",
                "issuers",
                "validators",
            },
            provider_label,
        )
        if not _token(provider["plugin_id"]) or not _token(
            provider["authority_profile"]
        ):
            raise ValueError(f"{provider_label} identity is invalid")
        provider_keys.append(provider["plugin_id"])
        category_schemas = {
            "producers": {
                "producer_id",
                "contract",
                "validator_id",
                "validator_contract",
                "state",
                "usable_for_new_publication",
            },
            "issuers": {
                "issuer_id",
                "contract",
                "capabilities",
                "state",
                "usable_for_new_publication",
            },
            "validators": {
                "validator_id",
                "contract",
                "state",
                "usable_for_new_publication",
            },
        }
        category_keys: dict[str, list[tuple[str, ...]]] = {
            "producers": [],
            "issuers": [],
            "validators": [],
        }
        for category, fields in category_schemas.items():
            entries = provider[category]
            if not isinstance(entries, list) or len(entries) > MAX_VALIDATORS:
                raise ValueError(f"{provider_label} {category} inventory is invalid")
            for entry_index, entry_value in enumerate(entries):
                entry_label = f"{provider_label} {category}[{entry_index}]"
                entry = _exact_dict(entry_value, fields, entry_label)
                identifier = entry[f"{category[:-1]}_id"]
                if (
                    not _token(identifier)
                    or not _text(entry["contract"])
                    or entry["state"] != "active"
                    or entry["usable_for_new_publication"] is not True
                ):
                    raise ValueError(f"{entry_label} identity is invalid")
                if category == "producers":
                    if not _token(entry["validator_id"]) or not _text(
                        entry["validator_contract"]
                    ):
                        raise ValueError(f"{entry_label} validator binding is invalid")
                    category_keys[category].append(
                        (
                            identifier,
                            entry["contract"],
                            entry["validator_id"],
                            entry["validator_contract"],
                        )
                    )
                elif category == "issuers":
                    capabilities = _sorted_token_inventory(
                        entry["capabilities"],
                        f"{entry_label} capabilities",
                    )
                    category_keys[category].append(
                        (identifier, entry["contract"], *capabilities)
                    )
                else:
                    category_keys[category].append((identifier, entry["contract"]))
            if category_keys[category] != sorted(category_keys[category]) or len(
                category_keys[category]
            ) != len(set(category_keys[category])):
                raise ValueError(
                    f"{provider_label} {category} must be sorted and unique"
                )
    if provider_keys != sorted(provider_keys) or len(provider_keys) != len(
        set(provider_keys)
    ):
        raise ValueError("compatibility policy providers must be sorted and unique")
    return policy


def _validate_receipt_source(value: object) -> dict[str, Any]:
    source = _exact_dict(
        value,
        {
            "mode",
            "plugin_id",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "release_version",
            "revision",
            "subtree_sha256",
            "source_authority",
            "details",
            "source_selection_sha256",
            "source_selection_content_sha256",
            "source_evidence",
            "agent_plugin_manifest_sha256",
            "claude_manifest_sha256",
            "provider_declaration_sha256",
            "provider_declaration_content_sha256",
        },
        "deployment source",
    )
    if (
        source["mode"] not in {"harness_snapshot", "publisher_channel", "exact_release"}
        or not _token(source["plugin_id"])
        or not _token(source["publisher_id"])
        or not _text(source["repository_id"])
        or REPOSITORY.fullmatch(source["repository_id"]) is None
        or not _text(source["repository_url"])
        or not _text(source["release_version"])
        or not isinstance(source["revision"], str)
        or GIT_REVISION.fullmatch(source["revision"]) is None
        or not _digest(source["subtree_sha256"])
        or not _token(source["source_authority"])
    ):
        raise ValueError("deployment source identity is invalid")
    _manifest_author(source["manifest_author"], "deployment source manifest author")
    mode = source["mode"]
    if mode in {"harness_snapshot", "publisher_channel"}:
        details = _exact_dict(
            source["details"],
            {"channel", "trust_class", "lineage"},
            "deployment source details",
        )
        lineage = _exact_dict(
            details["lineage"],
            {"lineage_id", "sequence"},
            "deployment source lineage",
        )
        if (
            not _token(details["channel"])
            or not _token(details["trust_class"])
            or not _token(lineage["lineage_id"])
            or not _nonnegative_integer(lineage["sequence"])
        ):
            raise ValueError("deployment source details are invalid")
    else:
        details = _exact_dict(
            source["details"],
            {"trust_class", "revision", "subtree_sha256"},
            "deployment exact-release details",
        )
        if (
            not _token(details["trust_class"])
            or details["revision"] != source["revision"]
            or details["subtree_sha256"] != source["subtree_sha256"]
        ):
            raise ValueError("deployment exact-release details are invalid")
    evidence_common = {"kind", "source_evidence_sha256"}
    if mode == "harness_snapshot":
        evidence = _exact_dict(
            source["source_evidence"],
            evidence_common
            | {
                "adapter_sha256",
                "manager_binding_sha256",
                "manager_binding_content_sha256",
                "manager_receipt_sha256",
            },
            "deployment harness source evidence",
        )
        binding_sha256 = evidence["manager_binding_sha256"]
        record_sha256 = evidence["manager_receipt_sha256"]
        digest_fields = (
            "adapter_sha256",
            "manager_binding_sha256",
            "manager_binding_content_sha256",
            "manager_receipt_sha256",
        )
    elif mode == "publisher_channel":
        evidence = _exact_dict(
            source["source_evidence"],
            evidence_common
            | {
                "resolver",
                "adapter_sha256",
                "publisher_binding_sha256",
                "publisher_binding_content_sha256",
                "publisher_record_sha256",
            },
            "deployment publisher source evidence",
        )
        if not _token(evidence["resolver"]):
            raise ValueError("deployment publisher resolver is invalid")
        binding_sha256 = evidence["publisher_binding_sha256"]
        record_sha256 = evidence["publisher_record_sha256"]
        digest_fields = (
            "adapter_sha256",
            "publisher_binding_sha256",
            "publisher_binding_content_sha256",
            "publisher_record_sha256",
        )
    else:
        evidence = _exact_dict(
            source["source_evidence"],
            evidence_common,
            "deployment exact-release source evidence",
        )
        binding_sha256 = None
        record_sha256 = None
        digest_fields = ()
    if evidence["kind"] != mode or any(
        not _digest(evidence[key]) for key in digest_fields
    ):
        raise ValueError("deployment source evidence binding is invalid")
    if binding_sha256 == EMPTY_SHA256 or record_sha256 == EMPTY_SHA256:
        raise ValueError("deployment source evidence has an empty-byte identity")
    expected_evidence_sha256 = hashlib.sha256(
        _canonical(
            {
                "contract": SOURCE_EVIDENCE_CONTRACT,
                "mode": mode,
                **(
                    {
                        "binding_sha256": binding_sha256,
                        "record_sha256": record_sha256,
                    }
                    if binding_sha256 is not None and record_sha256 is not None
                    else {}
                ),
            }
        )
    ).hexdigest()
    if evidence["source_evidence_sha256"] != expected_evidence_sha256:
        raise ValueError("deployment aggregate source evidence is invalid")
    for key in (
        "source_selection_sha256",
        "source_selection_content_sha256",
        "agent_plugin_manifest_sha256",
        "claude_manifest_sha256",
    ):
        if not _digest(source[key]):
            raise ValueError(f"deployment source {key} is invalid")
    provider_digests = (
        source["provider_declaration_sha256"],
        source["provider_declaration_content_sha256"],
    )
    if provider_digests != (None, None) and not all(
        _digest(item) for item in provider_digests
    ):
        raise ValueError("deployment source provider binding is invalid")
    return source


def _validate_bridge_legacy_receipt_source(value: object) -> dict[str, Any]:
    """Parse only the flattened harness source reached through the bridge edge."""

    source = _exact_dict(
        value,
        {
            "mode",
            "plugin_id",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "release_version",
            "revision",
            "subtree_sha256",
            "source_authority",
            "channel",
            "manager_trust_class",
            "lineage",
            "source_selection_sha256",
            "source_selection_content_sha256",
            "manager_binding_sha256",
            "manager_binding_content_sha256",
            "manager_receipt_sha256",
            "claude_manifest_sha256",
            "codex_manifest_sha256",
            "provider_declaration_sha256",
            "provider_declaration_content_sha256",
        },
        "bridge legacy deployment source",
    )
    lineage = _exact_dict(
        source["lineage"],
        {"lineage_id", "sequence"},
        "bridge legacy deployment source lineage",
    )
    if (
        source["mode"] != "harness_snapshot"
        or not _token(source["plugin_id"])
        or not _token(source["publisher_id"])
        or not _text(source["repository_id"])
        or REPOSITORY.fullmatch(source["repository_id"]) is None
        or not _text(source["repository_url"])
        or not _text(source["release_version"])
        or not isinstance(source["revision"], str)
        or GIT_REVISION.fullmatch(source["revision"]) is None
        or not _digest(source["subtree_sha256"])
        or not _token(source["source_authority"])
        or not _token(source["channel"])
        or not _token(source["manager_trust_class"])
        or not _token(lineage["lineage_id"])
        or not _nonnegative_integer(lineage["sequence"])
        or source["provider_declaration_sha256"] is not None
        or source["provider_declaration_content_sha256"] is not None
    ):
        raise ValueError("bridge legacy deployment source is invalid")
    _manifest_author(
        source["manifest_author"],
        "bridge legacy deployment source manifest author",
    )
    for key in (
        "source_selection_sha256",
        "source_selection_content_sha256",
        "manager_binding_sha256",
        "manager_binding_content_sha256",
        "manager_receipt_sha256",
        "claude_manifest_sha256",
        "codex_manifest_sha256",
    ):
        if not _digest(source[key]):
            raise ValueError(f"bridge legacy deployment source {key} is invalid")
    return {
        **source,
        "details": {
            "channel": source["channel"],
            "trust_class": source["manager_trust_class"],
            "lineage": lineage,
        },
    }


def _validate_bridge_migration_receipt(
    value: object,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("bridge migration receipt is invalid")
    execution_class = value.get("execution_class")
    required = {
        "schema_version",
        "contract",
        "edge",
        "purpose",
        "execution_class",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "transition_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    }
    if execution_class == "live-migration":
        required.add("prior_rehearsal")
    migration = _exact_dict(value, required, "bridge migration receipt")
    edge = _exact_dict(
        migration["edge"],
        {"from", "to", "via"},
        "bridge migration edge",
    )
    digest_fields = required - {
        "schema_version",
        "contract",
        "edge",
        "purpose",
        "execution_class",
        "prior_rehearsal",
    }
    if (
        type(migration["schema_version"]) is not int
        or migration["schema_version"] != 1
        or migration["contract"] != BRIDGE_MIGRATION_PROJECTION_CONTRACT
        or edge != {"from": "freeze5", "to": "tw4", "via": "bridge"}
        or migration["purpose"] != "bridge-transition"
        or execution_class not in {"isolated-rehearsal", "live-migration"}
        or any(not _digest(migration[key]) for key in digest_fields)
    ):
        raise ValueError("bridge migration receipt is invalid")
    core = {
        key: item
        for key, item in receipt.items()
        if key not in {"migration", "content_sha256"}
    }
    if migration["expected_active_receipt_core_sha256"] != _sha(_canonical(core)):
        raise ValueError("bridge migration receipt core binding mismatch")
    if execution_class == "live-migration":
        _validate_bridge_transition_projection(
            {
                key: item
                for key, item in migration.items()
                if key not in {"schema_version", "contract", "edge", "purpose"}
            }
        )
    return migration


def _validate_receipt_active_projection(
    value: object,
    source: dict[str, Any],
    root: Path,
    label: str,
) -> dict[str, Any]:
    active = _exact_dict(
        value,
        {
            "record_path",
            "record_sha256",
            "generation",
            "runtime_contract",
            "runtime_implementation_sha256",
            "public_release",
        },
        f"{label} active binding",
    )
    release = _exact_dict(
        active["public_release"],
        {"repository", "revision"},
        f"{label} active public release",
    )
    if (
        _absolute(active["record_path"], f"{label} active record path")
        != root / "active.json"
        or not _digest(active["record_sha256"])
        or not isinstance(active["generation"], str)
        or GENERATION.fullmatch(active["generation"]) is None
        or active["runtime_contract"] != RUNTIME_CONTRACT
        or not _digest(active["runtime_implementation_sha256"])
        or active["generation"] != f"sha256-{active['runtime_implementation_sha256']}"
        or not isinstance(release["repository"], str)
        or REPOSITORY.fullmatch(release["repository"]) is None
        or not isinstance(release["revision"], str)
        or GIT_REVISION.fullmatch(release["revision"]) is None
    ):
        raise ValueError(f"{label} active projection is invalid")
    if (
        source["repository_id"] != release["repository"]
        or source["revision"] != release["revision"]
    ):
        raise ValueError(f"{label} source does not bind its active public release")
    return active


def _validate_policy_source_binding(
    policy: dict[str, Any],
    source: dict[str, Any],
) -> None:
    source_details = source["details"]
    if source["mode"] in {"harness_snapshot", "publisher_channel"}:
        policy_details = {
            "channel": source_details["channel"],
            "trust_class": source_details["trust_class"],
            "lineage_id": source_details["lineage"]["lineage_id"],
        }
    else:
        policy_details = {"trust_class": source_details["trust_class"]}
    expected = {
        "plugin_id": source["plugin_id"],
        "mode": source["mode"],
        "publisher_id": source["publisher_id"],
        "manifest_author": source["manifest_author"],
        "repository_id": source["repository_id"],
        "repository_url": source["repository_url"],
        "source_authority": source["source_authority"],
        "details": policy_details,
    }
    if not _same_json(policy["source"], expected):
        raise ValueError("compatibility policy does not cover the deployment source")


def _validate_policy_receipt_binding(
    policy: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    controls = _exact_dict(
        receipt["control_set"],
        {"shim", "client", "launcher", "controller", "policy"},
        "deployment control set",
    )
    policy_binding = _exact_dict(
        receipt["compatibility_policy"],
        {"path", "length", "sha256", "owner", "mode", "content_sha256"},
        "deployment compatibility-policy binding",
    )
    control_surface = policy["control_surface"]
    expected_receipt_contracts = {
        key: control_surface["contracts"][key] for key in RECEIPT_CONTRACTS
    }
    if (
        not _same_json(
            {key: policy_binding[key] for key in controls["policy"]},
            controls["policy"],
        )
        or policy_binding["content_sha256"] != policy["content_sha256"]
        or not _same_json(
            receipt["process_profile"], control_surface["process_profile"]
        )
        or not _same_json(receipt["contracts"], expected_receipt_contracts)
    ):
        raise ValueError("deployment compatibility-policy binding mismatch")


def _validate_receipt_platform(value: object) -> dict[str, Any]:
    platform_value = _exact_dict(
        value,
        {"system", "machine", "qualified_filesystem_class"},
        "deployment platform",
    )
    if any(
        not isinstance(platform_value[key], str)
        or PLATFORM_TOKEN.fullmatch(platform_value[key]) is None
        for key in ("system", "machine", "qualified_filesystem_class")
    ):
        raise ValueError("deployment platform identity is invalid")
    current = os.uname()
    if (
        platform_value["system"] != current.sysname.lower()
        or platform_value["machine"] != current.machine.lower()
    ):
        raise ValueError("deployment platform does not match the current runtime")
    return platform_value


def _validate_receipt_runtime_closure(value: object) -> dict[str, Any]:
    closure = _exact_dict(
        value,
        {
            "supplier",
            "provenance",
            "qualification_class",
            "evidence_sha256",
            "dependency_classes",
            "qualification_content_sha256",
        },
        "deployment runtime closure",
    )
    if (
        any(
            not _token(closure[key])
            for key in ("supplier", "provenance", "qualification_class")
        )
        or not _digest(closure["evidence_sha256"])
        or not _digest(closure["qualification_content_sha256"])
    ):
        raise ValueError("deployment runtime closure identity is invalid")
    _sorted_token_inventory(
        closure["dependency_classes"],
        "deployment runtime dependency classes",
    )
    return closure


def _validate_receipt_contracts(
    value: object,
    *,
    receipt_profile: str = CURRENT_RECEIPT_PROFILE,
) -> None:
    if receipt_profile == CURRENT_RECEIPT_PROFILE:
        expected = RECEIPT_CONTRACTS
    elif receipt_profile == BRIDGE_LEGACY_RECEIPT_PROFILE:
        expected = LEGACY_RECEIPT_CONTRACTS
    else:
        raise ValueError("deployment receipt profile is unsupported")
    if not _same_json(value, expected):
        raise ValueError("deployment contract inventory mismatch")


def _validate_receipt_authorization(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("deployment authorization is not an object")
    purpose = value.get("purpose")
    required = {
        "contract",
        "purpose",
        "sha256",
        "content_sha256",
        "plan_sha256",
        "maintenance_transaction_sha256",
    }
    active_prior_purposes = {
        "routine-compatible-forward",
        "source-boundary-change",
        "complete-control-set-maintenance",
    }
    if purpose in active_prior_purposes:
        required.add("expected_active_receipt_sha256")
    authorization = _exact_dict(
        value,
        required,
        "deployment authorization",
    )
    if (
        authorization["contract"] != DEPLOYER_AUTHORIZATION_CONTRACT
        or authorization["purpose"] not in {"first-install", *active_prior_purposes}
        or any(
            not _digest(authorization[key])
            for key in (
                "sha256",
                "content_sha256",
                "plan_sha256",
                "maintenance_transaction_sha256",
            )
        )
        or (
            purpose in active_prior_purposes
            and not _digest(authorization["expected_active_receipt_sha256"])
        )
    ):
        raise ValueError("deployment authorization binding is invalid")
    return authorization


def _validate_receipt_providers(
    value: object,
    source: dict[str, Any],
    root: Path,
    active_trust: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_VALIDATORS + 1:
        raise ValueError("deployment provider inventory is invalid")
    providers: list[dict[str, Any]] = []
    keys: list[tuple[str, bool]] = []
    plugin_ids: list[str] = []
    observed_paths: set[str] = set()
    external: list[dict[str, Any]] = []
    intrinsic: list[dict[str, Any]] = []
    retained_modules = active_trust["retained_modules"]
    for provider_index, provider_value in enumerate(value):
        label = f"deployment provider[{provider_index}]"
        provider = _exact_dict(
            provider_value,
            {
                "plugin_id",
                "publisher",
                "repository",
                "authority_profile",
                "intrinsic",
                "declaration_sha256",
                "declaration_content_sha256",
                "producers",
                "issuers",
                "validators",
                "retained_modules",
            },
            label,
        )
        if (
            not _token(provider["plugin_id"])
            or not _text(provider["publisher"])
            or not _text(provider["repository"])
            or not _token(provider["authority_profile"])
            or type(provider["intrinsic"]) is not bool
            or not _digest(provider["declaration_sha256"])
            or not _digest(provider["declaration_content_sha256"])
        ):
            raise ValueError(f"{label} identity is invalid")
        if provider["intrinsic"] and (
            provider["plugin_id"] != "task-witness"
            or provider["publisher"] != "nisavid"
            or provider["repository"] != "https://github.com/nisavid/agents"
        ):
            raise ValueError(f"{label} intrinsic identity is invalid")
        for category in ("producers", "issuers", "validators"):
            roles = provider[category]
            if not isinstance(roles, list) or len(roles) > MAX_VALIDATORS:
                raise ValueError(f"{label} {category} inventory is invalid")
        modules = provider["retained_modules"]
        if (
            not isinstance(modules, list)
            or len(modules) > MAX_VALIDATORS * MAX_VALIDATOR_MODULES
        ):
            raise ValueError(f"{label} retained module inventory is invalid")
        module_paths: list[str] = []
        for module_index, module_value in enumerate(modules):
            module_label = f"{label} retained module[{module_index}]"
            module = _exact_dict(
                module_value,
                {"name", "path", "length", "sha256"},
                module_label,
            )
            path = _absolute(module["path"], f"{module_label} path")
            if (
                not _token(module["name"])
                or not _nonnegative_integer(module["length"])
                or module["length"] > MAX_VALIDATOR_ARTIFACT_BYTES
                or not _digest(module["sha256"])
                or path.parent.parent != root / "trust" / "validators"
                or GENERATION.fullmatch(path.parent.name) is None
                or path.name != f"{module['name']}.py"
                or not _same_json(retained_modules.get(str(path)), module)
            ):
                raise ValueError(f"{module_label} identity mismatch")
            module_paths.append(str(path))
            observed_paths.add(str(path))
        if module_paths != sorted(module_paths) or len(module_paths) != len(
            set(module_paths)
        ):
            raise ValueError(f"{label} retained modules must be sorted and unique")
        keys.append((provider["plugin_id"], provider["intrinsic"]))
        plugin_ids.append(provider["plugin_id"])
        (intrinsic if provider["intrinsic"] else external).append(provider)
        providers.append(provider)
    if (
        keys != sorted(keys)
        or len(keys) != len(set(keys))
        or len(plugin_ids) != len(set(plugin_ids))
    ):
        raise ValueError("deployment providers must be sorted and unique")
    if observed_paths != set(retained_modules):
        raise ValueError("deployment provider modules do not cover the trust context")
    if len(intrinsic) != 1 or intrinsic[0]["authority_profile"] != "task-witness-smoke":
        raise ValueError("deployment intrinsic smoke provider is invalid")
    provider_binding = (
        source["provider_declaration_sha256"],
        source["provider_declaration_content_sha256"],
    )
    if not external:
        if source["plugin_id"] != "task-witness" or provider_binding != (None, None):
            raise ValueError("deployment has a dangling source provider binding")
    elif (
        len(external) != 1
        or source["plugin_id"] != external[0]["plugin_id"]
        or source["publisher_id"] != external[0]["publisher"]
        or source["repository_url"] != external[0]["repository"]
        or provider_binding
        != (
            external[0]["declaration_sha256"],
            external[0]["declaration_content_sha256"],
        )
    ):
        raise ValueError("deployment source provider binding mismatch")
    return providers


def _validate_policy_provider_binding(
    policy: dict[str, Any],
    providers: list[dict[str, Any]],
    active_trust: dict[str, Any],
) -> None:
    roles = active_trust["role_inventory"]
    smoke_producers = [
        item
        for item in roles["producers"]
        if item["producer_id"] == SMOKE_PRODUCER_NAME
        and item["contract"] == SMOKE_BUNDLE_CONTRACT
    ]
    smoke_issuers = [
        item
        for item in roles["issuers"]
        if item["issuer_id"] == SMOKE_ISSUER_NAME
        and item["contract"] == SMOKE_ISSUER_CONTRACT
    ]
    smoke_validators = [
        item
        for item in roles["validators"]
        if item["validator_id"] == SMOKE_VALIDATOR_NAME
        and item["contract"] == SMOKE_BUNDLE_CONTRACT
    ]
    if (
        len(smoke_producers) != 1
        or len(smoke_issuers) != 1
        or len(smoke_validators) != 1
    ):
        raise ValueError("active trust context lacks one intrinsic smoke role set")
    smoke_producer = smoke_producers[0]
    smoke_issuer = smoke_issuers[0]
    smoke_validator = smoke_validators[0]
    smoke_validator_implementation = smoke_validator["implementation_sha256"]
    expected_producer_implementation = _sha(
        _canonical(
            {
                "contract": SMOKE_PRODUCER_IMPLEMENTATION_CONTRACT,
                "validator_implementation_sha256": smoke_validator_implementation,
            }
        )
    )
    expected_issuer_implementation = _sha(
        _canonical({"contract": SMOKE_ISSUER_IMPLEMENTATION_CONTRACT})
    )
    if (
        smoke_producer["contract"] != SMOKE_BUNDLE_CONTRACT
        or smoke_producer["implementation_sha256"] != expected_producer_implementation
        or smoke_producer["validator_id"] != SMOKE_VALIDATOR_NAME
        or smoke_producer["validator_contract"] != SMOKE_BUNDLE_CONTRACT
        or smoke_producer["validator_implementation_sha256"]
        != smoke_validator_implementation
        or smoke_issuer["contract"] != SMOKE_ISSUER_CONTRACT
        or smoke_issuer["implementation_sha256"] != expected_issuer_implementation
        or smoke_issuer["capabilities"] != ["activation-smoke"]
        or smoke_validator["contract"] != SMOKE_BUNDLE_CONTRACT
        or smoke_validator["entrypoint"] != SMOKE_VALIDATOR_NAME
        or len(smoke_validator["modules"]) != 1
        or smoke_validator["modules"][0]["name"] != SMOKE_VALIDATOR_NAME
        or any(
            item["state"] != "active" or item["usable_for_new_publication"] is not True
            for item in (smoke_producer, smoke_issuer, smoke_validator)
        )
    ):
        raise ValueError("active intrinsic smoke role binding is invalid")

    intrinsic = [item for item in providers if item["intrinsic"]]
    external = [item for item in providers if not item["intrinsic"]]
    expected_declaration_content_sha256 = _sha(
        _canonical(
            {
                "contract": INTRINSIC_SMOKE_PROVIDER_CONTRACT,
                "validator_implementation_sha256": smoke_validator_implementation,
            }
        )
    )
    expected_declaration_sha256 = _sha(
        _canonical(
            {
                "contract": INTRINSIC_SMOKE_PROVIDER_CONTRACT,
                "content_sha256": expected_declaration_content_sha256,
            }
        )
        + b"\n"
    )
    if (
        intrinsic[0]["declaration_content_sha256"]
        != expected_declaration_content_sha256
        or intrinsic[0]["declaration_sha256"] != expected_declaration_sha256
    ):
        raise ValueError("intrinsic smoke provider declaration identity mismatch")
    smoke_identity = (
        smoke_validator["validator_id"],
        smoke_validator["contract"],
        smoke_validator_implementation,
    )
    if not _same_json(
        intrinsic[0]["retained_modules"],
        active_trust["validator_modules"][smoke_identity],
    ):
        raise ValueError("intrinsic smoke provider module binding mismatch")
    intrinsic_roles = {
        "producers": [smoke_producer],
        "issuers": [smoke_issuer],
        "validators": [smoke_validator],
    }
    if any(
        not _same_json(intrinsic[0][category], expected)
        for category, expected in intrinsic_roles.items()
    ):
        raise ValueError("intrinsic smoke provider role ownership mismatch")

    category_specs = {
        "producers": (
            "producer_id",
            (
                "producer_id",
                "contract",
                "validator_id",
                "validator_contract",
                "state",
                "usable_for_new_publication",
            ),
        ),
        "issuers": (
            "issuer_id",
            (
                "issuer_id",
                "contract",
                "capabilities",
                "state",
                "usable_for_new_publication",
            ),
        ),
        "validators": (
            "validator_id",
            (
                "validator_id",
                "contract",
                "state",
                "usable_for_new_publication",
            ),
        ),
    }
    smoke_authorities = {
        "producers": (SMOKE_PRODUCER_NAME, SMOKE_BUNDLE_CONTRACT),
        "issuers": (SMOKE_ISSUER_NAME, SMOKE_ISSUER_CONTRACT),
        "validators": (SMOKE_VALIDATOR_NAME, SMOKE_BUNDLE_CONTRACT),
    }
    external_role_maps: dict[
        str,
        dict[tuple[str, str], dict[str, Any]],
    ] = {}
    for category, (identifier, _) in category_specs.items():
        external_role_maps[category] = {
            (item[identifier], item["contract"]): item
            for item in roles[category]
            if (item[identifier], item["contract"]) != smoke_authorities[category]
        }

    claimed: dict[str, set[tuple[str, str]]] = {
        category: set() for category in category_specs
    }
    policy_providers = {item["plugin_id"]: item for item in policy["providers"]}
    for provider in external:
        policy_provider = policy_providers.get(provider["plugin_id"])
        if (
            policy_provider is None
            or policy_provider["authority_profile"] != provider["authority_profile"]
            or not any(policy_provider[category] for category in category_specs)
        ):
            raise ValueError("compatibility policy provider identity mismatch")
        provider_modules: dict[str, dict[str, object]] = {}
        provider_claimed: dict[str, set[tuple[str, str]]] = {
            category: set() for category in category_specs
        }
        for category, (identifier, fields) in category_specs.items():
            expected_provider_roles: list[dict[str, Any]] = []
            for policy_role in policy_provider[category]:
                authority = (
                    policy_role[identifier],
                    policy_role["contract"],
                )
                active_role = external_role_maps[category].get(authority)
                if (
                    active_role is None
                    or authority in claimed[category]
                    or not _same_json(
                        policy_role,
                        {key: active_role[key] for key in fields},
                    )
                ):
                    raise ValueError(
                        "compatibility policy provider role binding mismatch"
                    )
                claimed[category].add(authority)
                provider_claimed[category].add(authority)
                expected_provider_roles.append(active_role)
                if category == "validators":
                    identity = (
                        active_role["validator_id"],
                        active_role["contract"],
                        active_role["implementation_sha256"],
                    )
                    for module in active_trust["validator_modules"][identity]:
                        prior = provider_modules.get(module["path"])
                        if prior is not None and not _same_json(prior, module):
                            raise ValueError(
                                "external provider module identity disagrees"
                            )
                        provider_modules[module["path"]] = module
            if not _same_json(provider[category], expected_provider_roles):
                raise ValueError("external provider role ownership mismatch")
        if any(
            (
                producer["validator_id"],
                producer["validator_contract"],
            )
            not in provider_claimed["validators"]
            for producer in policy_provider["producers"]
        ):
            raise ValueError("external producer crosses provider authority")
        if not _same_json(
            provider["retained_modules"],
            [provider_modules[path] for path in sorted(provider_modules)],
        ):
            raise ValueError("external provider module binding mismatch")
    if any(
        claimed[category] != set(external_role_maps[category])
        for category in category_specs
    ):
        raise ValueError("active trust context has unbound external roles")


def _validate_receipt_provider_semantics(
    receipt: dict[str, Any],
    source: dict[str, Any],
    root: Path,
    active_trust: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    _validate_policy_source_binding(policy, source)
    role_inventory = _exact_dict(
        receipt["role_inventory"],
        {"producers", "issuers", "validators"},
        "deployment role inventory",
    )
    if not _same_json(role_inventory, active_trust["role_inventory"]):
        raise ValueError("deployment role inventory does not bind active trust")
    providers = _validate_receipt_providers(
        receipt["providers"],
        source,
        root,
        active_trust,
    )
    _validate_policy_provider_binding(policy, providers, active_trust)
    return providers


def _validate_retained_receipts(
    receipt: dict[str, Any],
    receipt_raw: bytes,
    root: Path,
    root_descriptor: int,
    directories: list[DirectoryRecord],
    files: list[FileRecord],
    inventories: list[InventoryRecord],
    activation_lock: dict[str, object],
    activation_lock_descriptor: int,
    expected_activation_lock_identity: FilesystemIdentity,
    *,
    initial_policy: dict[str, Any] | None = None,
    initial_receipt_profile: str = CURRENT_RECEIPT_PROFILE,
) -> dict[str, Any]:
    if initial_receipt_profile not in {
        CURRENT_RECEIPT_PROFILE,
        BRIDGE_LEGACY_RECEIPT_PROFILE,
    }:
        raise ValueError("deployment receipt profile is unsupported")
    receipts_directory = _open_directory_at(
        directories,
        root_descriptor,
        "receipts",
        "retained receipt directory",
        private=True,
        required_mode=0o700,
    )
    deployment_receipt_keys = {
        "schema_version",
        "contract",
        "sequence",
        "prior_receipt_sha256",
        "canonical_root",
        "effective_uid",
        "activation_lock",
        "control_set",
        "interpreter",
        "process_profile",
        "active",
        "trust_context",
        "historical_trust_contexts",
        "platform",
        "source",
        "runtime_closure",
        "contracts",
        "providers",
        "role_inventory",
        "compatibility_policy",
        "authorization",
        "rollback",
        "smoke",
        "content_sha256",
    }
    expected_names: set[str] = set()
    live_root_identity = _full_filesystem_identity(os.fstat(root_descriptor))
    live_activation_lock_identity = _filesystem_identity(
        os.fstat(activation_lock_descriptor)
    )
    policy = initial_policy
    trust_directory: int | None = None
    contexts_directory: int | None = None
    trust_cache: dict[str, dict[str, Any]] = {}

    def validate_receipt_semantics(
        value: dict[str, Any],
        label: str,
        receipt_profile: str,
    ) -> None:
        nonlocal policy, trust_directory, contexts_directory
        if policy is None:
            controller_directory = _open_directory_at(
                directories,
                root_descriptor,
                "controller",
                "retained receipt controller directory",
                private=True,
                required_mode=0o700,
            )
            policy_raw, policy_metadata = _capture_file(
                files,
                controller_directory,
                "policy.json",
                "retained receipt compatibility policy",
                MAX_DOCUMENT_BYTES,
                private=True,
            )
            if stat.S_IMODE(policy_metadata.st_mode) != 0o600:
                raise ValueError(
                    "retained receipt compatibility policy mode is invalid"
                )
            policy = _validate_compatibility_policy(
                policy_raw,
                receipt_profile=receipt_profile,
            )
        if trust_directory is None or contexts_directory is None:
            trust_directory = _open_directory_at(
                directories,
                root_descriptor,
                "trust",
                "retained receipt trust directory",
                private=True,
                required_mode=0o700,
            )
            contexts_directory = _open_directory_at(
                directories,
                trust_directory,
                "contexts",
                "retained receipt trust-context directory",
                private=True,
                required_mode=0o700,
            )
        source = (
            _validate_receipt_source(value["source"])
            if receipt_profile == CURRENT_RECEIPT_PROFILE
            else _validate_bridge_legacy_receipt_source(value["source"])
        )
        _validate_policy_receipt_binding(policy, value)
        _validate_receipt_active_projection(
            value["active"],
            source,
            root,
            label,
        )
        trust_binding = _exact_dict(
            value["trust_context"],
            {"path", "sha256"},
            f"{label} trust-context binding",
        )
        trust_path = _absolute(
            trust_binding["path"],
            f"{label} trust-context path",
        )
        trust_sha256 = trust_binding["sha256"]
        if (
            not _digest(trust_sha256)
            or trust_path != root / "trust" / "contexts" / f"sha256-{trust_sha256}.json"
        ):
            raise ValueError(f"{label} trust-context binding mismatch")
        active_trust = trust_cache.get(trust_sha256)
        if active_trust is None:
            trust_raw, _ = _capture_file(
                files,
                contexts_directory,
                trust_path.name,
                f"{label} trust context",
                MAX_DOCUMENT_BYTES,
                private=True,
            )
            if _sha(trust_raw) != trust_sha256:
                raise ValueError(f"{label} trust-context binding mismatch")
            active_trust = _validate_retained_trust_context(
                trust_raw,
                root,
                trust_directory,
                directories,
                files,
                inventories,
                f"{label} trust context",
            )
            trust_cache[trust_sha256] = active_trust
        _validate_receipt_provider_semantics(
            value,
            source,
            root,
            active_trust,
            policy,
        )
        historical_registry = _historical_trust_registry(
            value["historical_trust_contexts"],
            root,
        )
        if trust_sha256 in historical_registry:
            raise ValueError("active trust context is duplicated in receipt history")

    def validate_receipt_header(
        value: dict[str, Any],
        label: str,
        receipt_profile: str,
    ) -> dict[str, Any]:
        if receipt_profile == CURRENT_RECEIPT_PROFILE:
            schema_version = 2
            contract = DEPLOYMENT_RECEIPT_CONTRACT
            source_validator = _validate_receipt_source
        elif receipt_profile == BRIDGE_LEGACY_RECEIPT_PROFILE:
            schema_version = 1
            contract = LEGACY_DEPLOYMENT_RECEIPT_CONTRACT
            source_validator = _validate_bridge_legacy_receipt_source
        else:
            raise ValueError("deployment receipt profile is unsupported")
        bridge_migration = (
            receipt_profile == CURRENT_RECEIPT_PROFILE and "migration" in value
        )
        expected_keys = deployment_receipt_keys | (
            {"migration"} if bridge_migration else set()
        )
        if (
            set(value) != expected_keys
            or type(value["schema_version"]) is not int
            or value["schema_version"] != schema_version
            or value["contract"] != contract
            or type(value["sequence"]) is not int
            or value["sequence"] < 1
            or (value["sequence"] == 1) != (value["prior_receipt_sha256"] is None)
            or (
                value["prior_receipt_sha256"] is not None
                and not _digest(value["prior_receipt_sha256"])
            )
            or value["canonical_root"] != str(root)
            or type(value["effective_uid"]) is not int
            or value["effective_uid"] != os.geteuid()
            or not _same_json(value["activation_lock"], activation_lock)
            or not _same_json(value["process_profile"], PROCESS_PROFILE)
        ):
            raise ValueError(f"{label} contract mismatch")
        _validate_receipt_platform(value["platform"])
        source_validator(value["source"])
        _validate_receipt_runtime_closure(value["runtime_closure"])
        _validate_receipt_contracts(
            value["contracts"],
            receipt_profile=receipt_profile,
        )
        if bridge_migration:
            if value["sequence"] != 3:
                raise ValueError("bridge migration receipt epoch is invalid")
            _validate_bridge_migration_receipt(value["migration"], value)
        return _validate_receipt_authorization(value["authorization"])

    def capture_retained(
        digest: str,
        label: str,
    ) -> tuple[bytes, os.stat_result, Path]:
        name = f"sha256-{digest}.json"
        path = root / "receipts" / name
        raw, metadata = _capture_file(
            files,
            receipts_directory,
            name,
            label,
            MAX_DOCUMENT_BYTES,
            private=True,
        )
        if _sha(raw) != digest or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError(f"{label} byte binding mismatch")
        expected_names.add(name)
        return raw, metadata, path

    deployment_sha256 = _sha(receipt_raw)
    deployment_name = f"sha256-{deployment_sha256}.json"
    retained_deployment_raw, retained_deployment_metadata = _capture_file(
        files,
        receipts_directory,
        deployment_name,
        "retained deployment receipt",
        MAX_DOCUMENT_BYTES,
        private=True,
    )
    if (
        retained_deployment_raw != receipt_raw
        or stat.S_IMODE(retained_deployment_metadata.st_mode) != 0o600
    ):
        raise ValueError("retained deployment receipt does not match the alias")
    expected_names.add(deployment_name)
    current = receipt
    current_raw = receipt_raw
    seen_receipts: set[str] = set()
    validated_receipts: dict[str, tuple[dict[str, Any], bytes]] = {}
    authority_rollback: dict[str, Any] | None = None
    current_profile = initial_receipt_profile
    bridge_transition_seen = False
    while True:
        current_sha256 = _sha(current_raw)
        if current_sha256 in seen_receipts:
            raise ValueError("deployment receipt chain contains a cycle")
        seen_receipts.add(current_sha256)
        validated_receipts[current_sha256] = (current, current_raw)
        authorization = validate_receipt_header(
            current,
            "retained deployment receipt",
            current_profile,
        )
        validate_receipt_semantics(
            current,
            "retained deployment receipt",
            current_profile,
        )
        rollback_binding = _exact_dict(
            current["rollback"],
            {"state", "path", "sha256"},
            "deployment rollback binding",
        )
        if rollback_binding["state"] not in {"absent", "active"} or not _digest(
            rollback_binding["sha256"]
        ):
            raise ValueError("deployment rollback binding is invalid")
        rollback_name = f"sha256-{rollback_binding['sha256']}.json"
        rollback_path = _absolute(
            rollback_binding["path"],
            "deployment rollback receipt path",
        )
        if (
            rollback_path != root / "receipts" / rollback_name
            or rollback_name == f"sha256-{current_sha256}.json"
        ):
            raise ValueError("deployment rollback receipt path mismatch")
        rollback_raw, _, _ = capture_retained(
            rollback_binding["sha256"],
            "rollback receipt",
        )
        rollback = _document(
            _json(rollback_raw, "rollback receipt"),
            "rollback receipt",
        )
        if authority_rollback is None:
            authority_rollback = rollback
        if rollback_binding["state"] == "absent":
            required = {
                "schema_version",
                "contract",
                "state",
                "canonical_root",
                "effective_uid",
                "activation_lock",
                "precondition",
                "deployment_receipt_absent",
                "prior_activation_unit",
                "external_dependencies",
                "smoke",
                "content_sha256",
            }
            if set(rollback) != required:
                raise ValueError("first-install rollback receipt contract mismatch")
            precondition = _exact_dict(
                rollback["precondition"],
                {"root_identity", "activation_lock_identity"},
                "first-install rollback precondition",
            )
            root_identity = _filesystem_identity_vector(
                precondition["root_identity"],
                "first-install rollback root identity",
            )
            recorded_lock_identity = _filesystem_identity_vector(
                precondition["activation_lock_identity"],
                "first-install rollback activation-lock identity",
            )
            recorded_lock = {
                "path": str(root / "activation.lock"),
                "device": recorded_lock_identity[0],
                "inode": recorded_lock_identity[1],
                "owner": recorded_lock_identity[3],
                "mode": stat.S_IMODE(recorded_lock_identity[2]),
            }
            if (
                current["sequence"] != 1
                or current["prior_receipt_sha256"] is not None
                or authorization["purpose"] != "first-install"
                or type(rollback["schema_version"]) is not int
                or rollback["schema_version"] != 1
                or rollback["contract"] != ROLLBACK_RECEIPT_CONTRACT
                or rollback["state"] != "absent"
                or rollback["canonical_root"] != str(root)
                or type(rollback["effective_uid"]) is not int
                or rollback["effective_uid"] != os.geteuid()
                or not _same_json(rollback["activation_lock"], activation_lock)
                or root_identity[:4] != live_root_identity[:4]
                or tuple(recorded_lock_identity) != expected_activation_lock_identity
                or live_activation_lock_identity != expected_activation_lock_identity
                or not _same_json(recorded_lock, rollback["activation_lock"])
                or rollback["deployment_receipt_absent"] is not True
                or rollback["prior_activation_unit"] != []
                or rollback["external_dependencies"] != []
                or not _same_json(
                    rollback["smoke"],
                    {
                        "contract": FIRST_INSTALL_ROLLBACK_CONTRACT,
                        "expected_state": "absent",
                    },
                )
            ):
                raise ValueError("first-install rollback receipt contract mismatch")
            break

        required = {
            "schema_version",
            "contract",
            "state",
            "canonical_root",
            "effective_uid",
            "activation_lock",
            "deployment_receipt_absent",
            "precondition",
            "prior_receipt",
            "prior_activation_unit",
            "selector_preimage",
            "external_dependencies",
            "smoke",
            "content_sha256",
        }
        control_maintenance = "control_preimage" in rollback
        if control_maintenance:
            required.add("control_preimage")
        if set(rollback) != required:
            raise ValueError("active rollback receipt contract mismatch")
        precondition = _exact_dict(
            rollback["precondition"],
            {
                "root_identity",
                "activation_lock_identity",
                "active_receipt_sha256",
            },
            "active rollback precondition",
        )
        root_identity = _filesystem_identity_vector(
            precondition["root_identity"],
            "active rollback root identity",
        )
        recorded_lock_identity = _filesystem_identity_vector(
            precondition["activation_lock_identity"],
            "active rollback activation-lock identity",
        )
        recorded_lock = {
            "path": str(root / "activation.lock"),
            "device": recorded_lock_identity[0],
            "inode": recorded_lock_identity[1],
            "owner": recorded_lock_identity[3],
            "mode": stat.S_IMODE(recorded_lock_identity[2]),
        }
        prior_sha256 = current["prior_receipt_sha256"]
        if (
            current["sequence"] <= 1
            or not _digest(prior_sha256)
            or authorization["purpose"]
            not in {
                "routine-compatible-forward",
                "source-boundary-change",
                "complete-control-set-maintenance",
            }
            or authorization["expected_active_receipt_sha256"] != prior_sha256
            or type(rollback["schema_version"]) is not int
            or rollback["schema_version"] != 1
            or rollback["contract"] != ROLLBACK_RECEIPT_CONTRACT
            or rollback["state"] != "active"
            or rollback["canonical_root"] != str(root)
            or type(rollback["effective_uid"]) is not int
            or rollback["effective_uid"] != os.geteuid()
            or not _same_json(rollback["activation_lock"], activation_lock)
            or rollback["deployment_receipt_absent"] is not False
            or precondition["active_receipt_sha256"] != prior_sha256
            or root_identity[:4] != live_root_identity[:4]
            or tuple(recorded_lock_identity) != expected_activation_lock_identity
            or live_activation_lock_identity != expected_activation_lock_identity
            or not _same_json(recorded_lock, rollback["activation_lock"])
        ):
            raise ValueError("active rollback receipt contract mismatch")

        prior_name = f"sha256-{prior_sha256}.json"
        prior_path = root / "receipts" / prior_name
        prior_binding = _validate_activation_file_binding_shape(
            rollback["prior_receipt"],
            prior_path,
            os.geteuid(),
            0o600,
            MAX_DOCUMENT_BYTES,
            "active rollback prior receipt",
        )
        prior_raw, prior_metadata, _ = capture_retained(
            prior_sha256,
            "prior deployment receipt",
        )
        if not _same_json(
            prior_binding,
            _installed_file_binding(prior_path, prior_raw, prior_metadata),
        ):
            raise ValueError("active rollback prior receipt binding mismatch")
        next_profile = current_profile
        if current_profile == CURRENT_RECEIPT_PROFILE and "migration" in current:
            if bridge_transition_seen:
                raise ValueError("deployment receipt bridge transition is duplicated")
            bridge_transition_seen = True
            next_profile = BRIDGE_LEGACY_RECEIPT_PROFILE
        prior = _document(
            _json(prior_raw, "prior deployment receipt"),
            "prior deployment receipt",
        )
        validate_receipt_header(
            prior,
            "prior deployment receipt",
            next_profile,
        )
        if (
            current["sequence"] != prior["sequence"] + 1
            or _sha(prior_raw) != prior_sha256
        ):
            raise ValueError("deployment receipt chain mismatch")

        prior_unit = _validate_activation_unit_shape(
            rollback["prior_activation_unit"],
            root,
            os.geteuid(),
            "active rollback prior activation unit",
        )
        expected_prior_deployment = {
            "path": str(root / "deployment.json"),
            "length": len(prior_raw),
            "sha256": prior_sha256,
            "owner": os.geteuid(),
            "mode": 0o600,
        }
        prior_active = _exact_dict(
            prior["active"],
            {
                "record_path",
                "record_sha256",
                "generation",
                "runtime_contract",
                "runtime_implementation_sha256",
                "public_release",
            },
            "prior deployment active binding",
        )
        prior_anchor = prior_unit["smoke"]["expected_anchor"]
        if (
            not _same_json(
                prior_unit["deployment_receipt"],
                expected_prior_deployment,
            )
            or not _same_json(prior_unit["control_set"], prior["control_set"])
            or not _same_json(prior_unit["smoke"], prior["smoke"])
            or prior_active["record_path"] != str(root / "active.json")
            or prior_active["record_sha256"] != prior_unit["active_record"]["sha256"]
            or prior_active["generation"] != prior_anchor["generation"]
            or prior_active["runtime_contract"] != prior_anchor["runtime_contract"]
            or prior_active["runtime_implementation_sha256"]
            != prior_anchor["runtime_implementation_sha256"]
            or not _same_json(
                prior_active["public_release"],
                prior_anchor["public_release"],
            )
            or not _same_json(
                prior["trust_context"],
                prior_unit["smoke"]["trust_context"],
            )
        ):
            raise ValueError("active rollback prior activation unit mismatch")

        prior_policy: dict[str, Any] | None = None
        if control_maintenance:
            values = rollback["control_preimage"]
            expected_roles = [item[0] for item in CONTROL_PREIMAGE_SPECS]
            if (
                not isinstance(values, list)
                or len(values) != len(CONTROL_PREIMAGE_SPECS)
                or [item.get("role") for item in values] != expected_roles
            ):
                raise ValueError("active rollback control preimage mismatch")
            prior_controls = _exact_dict(
                prior["control_set"],
                {"shim", "client", "launcher", "controller", "policy"},
                "prior deployment control set",
            )
            prior_controls = {
                **prior_controls,
                "smoke-bundle-manifest": _exact_dict(
                    prior["smoke"]["bundle"]["manifest"],
                    {"path", "length", "sha256", "owner", "mode"},
                    "prior smoke-bundle manifest binding",
                ),
            }
            control_stage_root: Path | None = None
            control_stage_paths: set[Path] = set()
            for index, (role, relative, installed_mode, limit) in enumerate(
                CONTROL_PREIMAGE_SPECS
            ):
                item = _exact_dict(
                    values[index],
                    {"role", "staged", "installed"},
                    f"active rollback control preimage[{index}]",
                )
                installed = _validate_activation_file_binding_shape(
                    item["installed"],
                    root / relative,
                    os.geteuid(),
                    installed_mode,
                    limit,
                    f"active rollback control preimage[{index}] installed",
                )
                staged_value = _exact_dict(
                    item["staged"],
                    {"path", "length", "sha256", "owner", "mode"},
                    f"active rollback control preimage[{index}] staged",
                )
                staged_path = _absolute(
                    staged_value["path"],
                    f"active rollback control preimage[{index}] staged path",
                )
                staged = _validate_activation_file_binding_shape(
                    staged_value,
                    staged_path,
                    os.geteuid(),
                    0o600,
                    limit,
                    f"active rollback control preimage[{index}] staged",
                )
                suffix = Path("preimage") / relative
                if control_stage_root is None:
                    if staged_path.parts[-len(suffix.parts) :] != suffix.parts:
                        raise ValueError("active rollback control preimage mismatch")
                    control_stage_root = staged_path.parents[len(suffix.parts) - 1]
                    if control_stage_root.is_relative_to(root) or root.is_relative_to(
                        control_stage_root
                    ):
                        raise ValueError("active rollback control preimage mismatch")
                expected_staged_path = control_stage_root / suffix
                if (
                    item["role"] != role
                    or not _same_json(installed, prior_controls[role])
                    or staged_path != expected_staged_path
                    or staged_path in control_stage_paths
                    or any(
                        staged[key] != installed[key]
                        for key in ("length", "sha256", "owner")
                    )
                ):
                    raise ValueError("active rollback control preimage mismatch")
                staged_raw, staged_metadata = _capture_absolute_file(
                    directories,
                    files,
                    staged_path,
                    f"active rollback control preimage[{index}] staged artifact",
                    limit,
                    private=True,
                )
                if (
                    len(staged_raw) != staged["length"]
                    or _sha(staged_raw) != staged["sha256"]
                    or staged_metadata.st_uid != staged["owner"]
                    or staged_metadata.st_nlink != 1
                    or stat.S_IMODE(staged_metadata.st_mode) != staged["mode"]
                ):
                    raise ValueError("active rollback control preimage mismatch")
                if role == "policy":
                    prior_policy = _validate_compatibility_policy(
                        staged_raw,
                        receipt_profile=next_profile,
                    )
                control_stage_paths.add(staged_path)
            if prior_policy is None:
                raise ValueError("active rollback control policy is unavailable")

        selectors = rollback["selector_preimage"]
        if not isinstance(selectors, list) or len(selectors) != 2:
            raise ValueError("active rollback selector preimage mismatch")
        expected_selectors = (
            ("active-record", prior_unit["active_record"]),
            ("deployment-alias", prior_unit["deployment_receipt"]),
        )
        staged_paths: set[Path] = set()
        for index, ((role, expected_installed), item_value) in enumerate(
            zip(expected_selectors, selectors)
        ):
            item = _exact_dict(
                item_value,
                {"role", "staged", "installed"},
                f"active rollback selector preimage[{index}]",
            )
            installed = _validate_activation_file_binding_shape(
                item["installed"],
                _absolute(
                    expected_installed["path"],
                    f"active rollback selector preimage[{index}] installed path",
                ),
                os.geteuid(),
                0o600,
                MAX_DOCUMENT_BYTES,
                f"active rollback selector preimage[{index}] installed",
            )
            staged_value = _exact_dict(
                item["staged"],
                {"path", "length", "sha256", "owner", "mode"},
                f"active rollback selector preimage[{index}] staged",
            )
            staged_path = _absolute(
                staged_value["path"],
                f"active rollback selector preimage[{index}] staged path",
            )
            staged = _validate_activation_file_binding_shape(
                staged_value,
                staged_path,
                os.geteuid(),
                0o600,
                MAX_DOCUMENT_BYTES,
                f"active rollback selector preimage[{index}] staged",
            )
            if (
                item["role"] != role
                or not _same_json(installed, expected_installed)
                or staged_path == Path(installed["path"])
                or staged_path in staged_paths
                or any(
                    staged[key] != installed[key]
                    for key in ("length", "sha256", "owner", "mode")
                )
            ):
                raise ValueError("active rollback selector preimage mismatch")
            staged_paths.add(staged_path)

        external = _exact_dict(
            rollback["external_dependencies"],
            {
                "interpreter",
                "runtime_closure",
                "process_profile",
                "receipt_parser",
            },
            "active rollback external dependencies",
        )
        parser = _exact_dict(
            external["receipt_parser"],
            {
                "deployment_receipt_contract",
                "rollback_receipt_contract",
                "controller",
                "client",
            },
            "active rollback receipt parser",
        )
        if (
            not _same_json(external["interpreter"], prior["interpreter"])
            or not _same_json(
                external["runtime_closure"],
                prior["runtime_closure"],
            )
            or not _same_json(external["process_profile"], prior["process_profile"])
            or parser["deployment_receipt_contract"]
            != prior["contracts"]["deployment_receipt"]
            or parser["rollback_receipt_contract"]
            != prior["contracts"]["rollback_receipt"]
            or not _same_json(parser["controller"], prior["control_set"]["controller"])
            or not _same_json(parser["client"], prior["control_set"]["client"])
            or not _same_json(rollback["smoke"], prior["smoke"])
            or not _same_json(rollback["smoke"], prior_unit["smoke"])
        ):
            raise ValueError("active rollback retained authority mismatch")
        if not control_maintenance:
            current_smoke = current["smoke"]
            prior_smoke = prior["smoke"]
            if (
                not _same_json(current["control_set"], prior["control_set"])
                or not _same_json(current["interpreter"], prior["interpreter"])
                or not _same_json(
                    current["runtime_closure"],
                    prior["runtime_closure"],
                )
                or not _same_json(current["process_profile"], prior["process_profile"])
                or not _same_json(current["platform"], prior["platform"])
                or not _same_json(
                    current["compatibility_policy"],
                    prior["compatibility_policy"],
                )
                or not _same_json(current["contracts"], prior["contracts"])
                or any(
                    not _same_json(current_smoke[field], prior_smoke[field])
                    for field in (
                        "bundle",
                        "producer",
                        "validator",
                        "expected_projection",
                    )
                )
            ):
                raise ValueError("routine deployment stable surface mismatch")
        current_history = _historical_trust_registry(
            current["historical_trust_contexts"],
            root,
        )
        prior_history = _historical_trust_registry(
            prior["historical_trust_contexts"],
            root,
        )
        expected_history = dict(prior_history)
        prior_trust = _exact_dict(
            prior["trust_context"],
            {"path", "sha256"},
            "prior deployment trust context",
        )
        current_trust = _exact_dict(
            current["trust_context"],
            {"path", "sha256"},
            "routine deployment trust context",
        )
        rebound_current = expected_history.pop(current_trust["sha256"], None)
        if rebound_current is not None and not _same_json(
            rebound_current,
            {
                "path": current_trust["path"],
                "sha256": current_trust["sha256"],
                "state": "historical-usable",
            },
        ):
            raise ValueError("routine historical trust reactivation mismatch")
        if current_trust["sha256"] != prior_trust["sha256"]:
            if prior_trust["sha256"] in expected_history:
                raise ValueError("prior active trust is already historical")
            expected_history[prior_trust["sha256"]] = {
                "path": prior_trust["path"],
                "sha256": prior_trust["sha256"],
                "state": "historical-usable",
            }
        if current_trust["sha256"] in current_history or not _same_json(
            current_history, expected_history
        ):
            raise ValueError("routine historical trust closure mismatch")
        if prior_policy is not None:
            policy = prior_policy
        current = prior
        current_raw = prior_raw
        current_profile = next_profile

    _capture_inventory(
        inventories,
        receipts_directory,
        expected_names,
        "retained receipt directory",
    )
    if initial_receipt_profile == BRIDGE_LEGACY_RECEIPT_PROFILE:
        chain = sorted(
            validated_receipts.values(),
            key=lambda item: item[0]["sequence"],
        )
        if len(chain) != 2 or [item[0]["sequence"] for item in chain] != [1, 2]:
            raise ValueError("B1 retained deployment chain is not exact")
        freeze5, freeze5_raw = chain[0]
        bridge, bridge_raw = chain[1]
        freeze5_controls = freeze5["control_set"]
        bridge_controls = bridge["control_set"]
        freeze5_source = freeze5["source"]
        bridge_source = bridge["source"]
        if (
            bridge_raw != receipt_raw
            or bridge != receipt
            or bridge["prior_receipt_sha256"] != _sha(freeze5_raw)
            or bridge_source["release_version"] != "0.1.1"
            or bridge_source["mode"] != "harness_snapshot"
            or bridge_controls["policy"]["sha256"] != FREEZE5_POLICY_SHA256
            or bridge_controls["controller"]["sha256"] == FREEZE5_CONTROLLER_SHA256
            or bridge_controls["client"]["sha256"] == FREEZE5_CLIENT_SHA256
            or freeze5_source["release_version"] != "0.1.0"
            or freeze5_source["revision"] != FREEZE5_COMMIT_SHA1
            or freeze5_source["mode"] != "harness_snapshot"
            or freeze5_controls["controller"]["sha256"] != FREEZE5_CONTROLLER_SHA256
            or freeze5_controls["policy"]["sha256"] != FREEZE5_POLICY_SHA256
            or freeze5_controls["client"]["sha256"] != FREEZE5_CLIENT_SHA256
        ):
            raise ValueError("B1 retained deployment chain is not exact")
    if authority_rollback is None:
        raise ValueError("deployment receipt rollback authority is unavailable")
    return {
        "receipt": receipt,
        "receipt_raw": receipt_raw,
        "rollback": authority_rollback,
        "receipts": validated_receipts,
    }


def _load_installation(
    bundle_sha256: str,
    root: Path,
    root_descriptor: int,
    directories: list[DirectoryRecord],
    files: list[FileRecord],
    inventories: list[InventoryRecord],
    activation_lock: dict[str, object],
    activation_lock_descriptor: int,
    activation_lock_identity: FilesystemIdentity,
    historical_context_sha256: str | None,
    invocation: InvocationState,
    retained_authority: dict[str, Any] | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    Path,
    Path,
    bool,
    dict[str, Any],
    str,
    dict[str, Any],
    dict[str, Any],
]:
    receipt_raw, receipt_metadata = _capture_file(
        files,
        root_descriptor,
        "deployment.json",
        "deployment receipt alias",
        MAX_DOCUMENT_BYTES,
        private=True,
    )
    if stat.S_IMODE(receipt_metadata.st_mode) != 0o600:
        raise ValueError("deployment receipt alias mode is invalid")
    receipt = _document(
        _json(receipt_raw, "deployment receipt alias"),
        "deployment receipt alias",
    )
    invocation.note_receipt(receipt)
    if CLIENT_RELEASE_PROFILE == "tw4-current":
        receipt_profile = CURRENT_RECEIPT_PROFILE
        expected_schema_version = 2
        expected_contract = DEPLOYMENT_RECEIPT_CONTRACT
        source_validator = _validate_receipt_source
    elif CLIENT_RELEASE_PROFILE == "b1-transition":
        receipt_profile = BRIDGE_LEGACY_RECEIPT_PROFILE
        expected_schema_version = 1
        expected_contract = LEGACY_DEPLOYMENT_RECEIPT_CONTRACT
        source_validator = _validate_bridge_legacy_receipt_source
    else:
        raise ValueError("client release profile is unsupported")
    required = {
        "schema_version",
        "contract",
        "sequence",
        "prior_receipt_sha256",
        "canonical_root",
        "effective_uid",
        "activation_lock",
        "control_set",
        "interpreter",
        "process_profile",
        "active",
        "trust_context",
        "historical_trust_contexts",
        "platform",
        "source",
        "runtime_closure",
        "contracts",
        "providers",
        "role_inventory",
        "compatibility_policy",
        "authorization",
        "rollback",
        "smoke",
        "content_sha256",
    }
    bridge_migration = (
        receipt_profile == CURRENT_RECEIPT_PROFILE and "migration" in receipt
    )
    if bridge_migration:
        required.add("migration")
    if (
        set(receipt) != required
        or type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != expected_schema_version
        or receipt["contract"] != expected_contract
        or type(receipt["sequence"]) is not int
        or receipt["sequence"] < 1
        or (receipt["sequence"] == 1 and receipt["prior_receipt_sha256"] is not None)
        or (receipt["sequence"] > 1 and not _digest(receipt["prior_receipt_sha256"]))
        or receipt["canonical_root"] != str(root)
        or type(receipt["effective_uid"]) is not int
        or receipt["effective_uid"] != os.geteuid()
        or not _same_json(receipt["activation_lock"], activation_lock)
        or not _same_json(receipt["process_profile"], PROCESS_PROFILE)
    ):
        raise ValueError("deployment receipt contract mismatch")
    _validate_receipt_platform(receipt["platform"])
    source = source_validator(receipt["source"])
    _validate_receipt_runtime_closure(receipt["runtime_closure"])
    _validate_receipt_contracts(
        receipt["contracts"],
        receipt_profile=receipt_profile,
    )
    if bridge_migration:
        if receipt["sequence"] != 3:
            raise ValueError("bridge migration receipt epoch is invalid")
        _validate_bridge_migration_receipt(receipt["migration"], receipt)
    _validate_receipt_authorization(receipt["authorization"])
    executable = Path(sys.executable).resolve(strict=True)
    executable_raw, _ = _capture_absolute_file(
        directories,
        files,
        executable,
        "deployment interpreter",
        MAX_INTERPRETER_BYTES,
        private=False,
    )
    if not _same_json(
        receipt["interpreter"],
        _interpreter_identity(executable, executable_raw),
    ):
        raise ValueError("deployment interpreter identity mismatch")
    if receipt["interpreter"]["implementation"] != "cpython":
        raise ValueError("deployment interpreter is not CPython")
    control_set = receipt["control_set"]
    if not isinstance(control_set, dict) or set(control_set) != {
        "shim",
        "client",
        "launcher",
        "controller",
        "policy",
    }:
        raise ValueError("deployment control-set schema drift")
    shim_path = root / "task-witness"
    shim_raw, shim_metadata = _capture_file(
        files,
        root_descriptor,
        "task-witness",
        "front-door shim",
        MAX_CONTROL_FILE_BYTES,
        private=True,
    )
    _validate_file_binding(
        control_set["shim"],
        shim_path,
        shim_raw,
        shim_metadata,
        "front-door shim",
    )
    if stat.S_IMODE(shim_metadata.st_mode) != 0o500:
        raise ValueError("front-door shim mode is invalid")
    client_path = root / "client" / "task_witness_client.py"
    launcher_path = root / "launcher" / "task_witness_launch.py"
    client_directory = _open_directory_at(
        directories,
        root_descriptor,
        "client",
        "client directory",
        private=True,
        required_mode=0o700,
    )
    client_raw, client_metadata = _capture_file(
        files,
        client_directory,
        "task_witness_client.py",
        "client",
        MAX_CONTROL_FILE_BYTES,
        private=True,
    )
    if _client_source_generation_sha256(client_raw) != CLIENT_SOURCE_GENERATION_SHA256:
        raise ValueError("loaded client source generation mismatch")
    _validate_file_binding(
        control_set["client"],
        client_path,
        client_raw,
        client_metadata,
        "client",
    )
    if stat.S_IMODE(client_metadata.st_mode) != 0o500:
        raise ValueError("client mode is invalid")
    launcher_directory = _open_directory_at(
        directories,
        root_descriptor,
        "launcher",
        "launcher directory",
        private=True,
        required_mode=0o700,
    )
    launcher_raw, launcher_metadata = _capture_file(
        files,
        launcher_directory,
        "task_witness_launch.py",
        "launcher",
        MAX_CONTROL_FILE_BYTES,
        private=True,
    )
    _validate_file_binding(
        control_set["launcher"],
        launcher_path,
        launcher_raw,
        launcher_metadata,
        "launcher",
    )
    if stat.S_IMODE(launcher_metadata.st_mode) != 0o500:
        raise ValueError("launcher mode is invalid")
    controller_directory = _open_directory_at(
        directories,
        root_descriptor,
        "controller",
        "controller directory",
        private=True,
        required_mode=0o700,
    )
    controller_path = root / "controller" / "task_witness_deploy.py"
    controller_raw, controller_metadata = _capture_file(
        files,
        controller_directory,
        "task_witness_deploy.py",
        "controller",
        MAX_CONTROL_FILE_BYTES,
        private=True,
    )
    _validate_file_binding(
        control_set["controller"],
        controller_path,
        controller_raw,
        controller_metadata,
        "controller",
    )
    if stat.S_IMODE(controller_metadata.st_mode) != 0o500:
        raise ValueError("controller mode is invalid")
    policy_path = root / "controller" / "policy.json"
    policy_raw, policy_metadata = _capture_file(
        files,
        controller_directory,
        "policy.json",
        "compatibility policy",
        MAX_DOCUMENT_BYTES,
        private=True,
    )
    _validate_file_binding(
        control_set["policy"],
        policy_path,
        policy_raw,
        policy_metadata,
        "compatibility policy",
    )
    if stat.S_IMODE(policy_metadata.st_mode) != 0o600:
        raise ValueError("compatibility policy mode is invalid")
    policy = _validate_compatibility_policy(
        policy_raw,
        receipt_profile=receipt_profile,
    )
    _validate_policy_source_binding(policy, source)
    _validate_policy_receipt_binding(policy, receipt)
    policy_binding = _exact_dict(
        receipt["compatibility_policy"],
        {"path", "length", "sha256", "owner", "mode", "content_sha256"},
        "deployment compatibility-policy binding",
    )
    if (
        not _same_json(
            {key: policy_binding[key] for key in control_set["policy"]},
            control_set["policy"],
        )
        or policy_binding["content_sha256"] != policy["content_sha256"]
    ):
        raise ValueError("deployment compatibility-policy binding mismatch")
    if Path(__file__) != client_path:
        raise ValueError("client path is not canonical")

    active_binding = _validate_receipt_active_projection(
        receipt["active"],
        source,
        root,
        "deployment",
    )
    active_path = root / "active.json"
    active_raw, active_metadata = _capture_file(
        files,
        root_descriptor,
        "active.json",
        "active record",
        MAX_DOCUMENT_BYTES,
        private=True,
    )
    active = _document(_json(active_raw, "active record"), "active record")
    runtime_sha256 = _validate_active_runtime(
        active,
        root_descriptor,
        directories,
        files,
        inventories,
    )
    if (
        not _same_json(
            active_binding,
            {
                "record_path": str(active_path),
                "record_sha256": _sha(active_raw),
                "generation": active["generation"],
                "runtime_contract": active["runtime_contract"],
                "runtime_implementation_sha256": runtime_sha256,
                "public_release": active["public_release"],
            },
        )
        or active["generation"] != f"sha256-{runtime_sha256}"
        or active["runtime_contract"] != RUNTIME_CONTRACT
    ):
        raise ValueError("active record binding mismatch")
    active_interpreter = dict(active.get("interpreter", {}))
    if not _same_json(
        active_interpreter,
        {
            key: receipt["interpreter"][key]
            for key in ("executable", "implementation", "version")
        },
    ):
        raise ValueError("active interpreter identity mismatch")
    trust_binding = receipt["trust_context"]
    if not isinstance(trust_binding, dict) or set(trust_binding) != {"path", "sha256"}:
        raise ValueError("deployment trust-context binding schema drift")
    trust_path = _absolute(trust_binding["path"], "trust-context path")
    if trust_path.parent != root / "trust" / "contexts":
        raise ValueError("trust-context path mismatch")
    trust_directory = _open_directory_at(
        directories,
        root_descriptor,
        "trust",
        "trust directory",
        private=True,
        required_mode=0o700,
    )
    contexts_directory = _open_directory_at(
        directories,
        trust_directory,
        "contexts",
        "trust-context directory",
        private=True,
        required_mode=0o700,
    )
    trust_raw, _ = _capture_file(
        files,
        contexts_directory,
        trust_path.name,
        "trust context",
        MAX_DOCUMENT_BYTES,
        private=True,
    )
    if trust_path.name != f"sha256-{_sha(trust_raw)}.json" or trust_binding != {
        "path": str(trust_path),
        "sha256": _sha(trust_raw),
    }:
        raise ValueError("trust-context binding mismatch")
    active_trust = _validate_retained_trust_context(
        trust_raw,
        root,
        trust_directory,
        directories,
        files,
        inventories,
        "active trust context",
    )
    _validate_receipt_provider_semantics(
        receipt,
        source,
        root,
        active_trust,
        policy,
    )
    if retained_authority is None:
        retained_authority = _validate_retained_receipts(
            receipt,
            receipt_raw,
            root,
            root_descriptor,
            directories,
            files,
            inventories,
            activation_lock,
            activation_lock_descriptor,
            activation_lock_identity,
            initial_receipt_profile=receipt_profile,
        )
    else:
        selected = retained_authority["receipts"].get(_sha(receipt_raw))
        if (
            not isinstance(selected, tuple)
            or len(selected) != 2
            or selected[1] != receipt_raw
            or not _same_json(selected[0], receipt)
        ):
            raise ValueError("activation target is outside retained authority")
    historical_registry = _historical_trust_registry(
        receipt["historical_trust_contexts"],
        root,
    )
    if trust_binding["sha256"] in historical_registry:
        raise ValueError("active trust context is duplicated in historical registry")
    if (
        historical_context_sha256 is not None
        and historical_context_sha256 != trust_binding["sha256"]
        and (
            historical_context_sha256 not in historical_registry
            or historical_registry[historical_context_sha256]["state"]
            != "historical-usable"
        )
    ):
        raise ValueError("historical trust context is not authorized")
    selected_trust_path = trust_path
    selected_trust_raw = trust_raw
    selected_trust = active_trust
    historical = historical_context_sha256 is not None
    if (
        historical_context_sha256 is not None
        and historical_context_sha256 != trust_binding["sha256"]
    ):
        selected_binding = historical_registry[historical_context_sha256]
        selected_trust_path = _absolute(
            selected_binding["path"],
            "historical trust-context path",
        )
        selected_trust_raw, _ = _capture_file(
            files,
            contexts_directory,
            selected_trust_path.name,
            "historical trust context",
            MAX_DOCUMENT_BYTES,
            private=True,
        )
        if (
            selected_trust_path.name != f"sha256-{_sha(selected_trust_raw)}.json"
            or not _same_json(
                selected_binding,
                {
                    "path": str(selected_trust_path),
                    "sha256": _sha(selected_trust_raw),
                    "state": "historical-usable",
                },
            )
        ):
            raise ValueError("historical trust-context binding mismatch")
        selected_trust = _validate_retained_trust_context(
            selected_trust_raw,
            root,
            trust_directory,
            directories,
            files,
            inventories,
            "historical trust context",
        )

    expected_anchor = {
        "contract": COMPLETE_ANCHOR_CONTRACT,
        "generation": active["generation"],
        "active_record_sha256": _sha(active_raw),
        "runtime_contract": active["runtime_contract"],
        "interpreter": active["interpreter"],
        "public_release": active["public_release"],
        "runtime_implementation_sha256": runtime_sha256,
        "trust_context_sha256": _sha(selected_trust_raw),
        "bundle_sha256": bundle_sha256,
        "historical": historical,
    }
    activation_unit = {
        "state": "active",
        "deployment_receipt": _installed_file_binding(
            root / "deployment.json",
            receipt_raw,
            receipt_metadata,
        ),
        "active_record": _installed_file_binding(
            active_path,
            active_raw,
            active_metadata,
        ),
        "control_set": control_set,
        "smoke": receipt["smoke"],
    }
    return (
        receipt,
        expected_anchor,
        launcher_path,
        selected_trust_path,
        historical,
        selected_trust,
        _sha(receipt_raw),
        activation_unit,
        retained_authority,
    )


def _validate_activation_file_binding_shape(
    value: object,
    expected_path: Path,
    expected_owner: int,
    expected_mode: int,
    byte_limit: int,
    label: str,
) -> dict[str, Any]:
    binding = _exact_dict(
        value,
        {"path", "length", "sha256", "owner", "mode"},
        label,
    )
    path = _absolute(binding["path"], f"{label} path")
    if (
        path != expected_path
        or binding["path"] != str(path)
        or type(binding["length"]) is not int
        or not 0 < binding["length"] <= byte_limit
        or not _digest(binding["sha256"])
        or type(binding["owner"]) is not int
        or binding["owner"] != expected_owner
        or type(binding["mode"]) is not int
        or binding["mode"] != expected_mode
    ):
        raise ValueError(f"{label} is invalid")
    return binding


def _validate_activation_anchor_shape(
    value: object,
    *,
    active_record_sha256: str,
    bundle_sha256: str,
    trust_context_sha256: str,
    label: str,
) -> dict[str, Any]:
    anchor = _exact_dict(
        value,
        {
            "contract",
            "generation",
            "active_record_sha256",
            "runtime_contract",
            "interpreter",
            "public_release",
            "runtime_implementation_sha256",
            "trust_context_sha256",
            "bundle_sha256",
            "historical",
        },
        label,
    )
    interpreter = _exact_dict(
        anchor["interpreter"],
        {"executable", "implementation", "version"},
        f"{label} interpreter",
    )
    executable = _absolute(
        interpreter["executable"],
        f"{label} interpreter executable",
    )
    version = _exact_dict(
        interpreter["version"],
        {"major", "minor", "micro"},
        f"{label} interpreter version",
    )
    release = _exact_dict(
        anchor["public_release"],
        {"repository", "revision"},
        f"{label} public release",
    )
    if (
        anchor["contract"] != COMPLETE_ANCHOR_CONTRACT
        or not isinstance(anchor["generation"], str)
        or GENERATION.fullmatch(anchor["generation"]) is None
        or anchor["active_record_sha256"] != active_record_sha256
        or anchor["runtime_contract"] != RUNTIME_CONTRACT
        or interpreter["executable"] != str(executable)
        or not _text(interpreter["implementation"])
        or any(
            type(version[part]) is not int or version[part] < 0
            for part in ("major", "minor", "micro")
        )
        or not isinstance(release["repository"], str)
        or REPOSITORY.fullmatch(release["repository"]) is None
        or not isinstance(release["revision"], str)
        or GIT_REVISION.fullmatch(release["revision"]) is None
        or not _digest(anchor["runtime_implementation_sha256"])
        or anchor["trust_context_sha256"] != trust_context_sha256
        or anchor["bundle_sha256"] != bundle_sha256
        or anchor["historical"] is not False
    ):
        raise ValueError(f"{label} is invalid")
    return anchor


def _validate_activation_smoke_shape(
    value: object,
    root: Path,
    effective_uid: int,
    active_record_sha256: str,
    label: str,
) -> dict[str, Any]:
    smoke = _exact_dict(
        value,
        {
            "bundle",
            "trust_context",
            "producer",
            "validator",
            "expected_projection",
            "expected_anchor",
            "expected_envelope_sha256",
        },
        label,
    )
    bundle = _exact_dict(
        smoke["bundle"],
        {"path", "sha256", "manifest"},
        f"{label} bundle",
    )
    bundle_path = _absolute(bundle["path"], f"{label} bundle path")
    if (
        bundle_path != root / "smoke" / "bundle"
        or bundle["path"] != str(bundle_path)
        or not _digest(bundle["sha256"])
    ):
        raise ValueError(f"{label} bundle is invalid")
    _validate_activation_file_binding_shape(
        bundle["manifest"],
        bundle_path / "manifest.json",
        effective_uid,
        0o600,
        MAX_DOCUMENT_BYTES,
        f"{label} manifest",
    )
    trust = _exact_dict(
        smoke["trust_context"],
        {"path", "sha256"},
        f"{label} trust context",
    )
    trust_path = _absolute(trust["path"], f"{label} trust-context path")
    if (
        trust_path != root / "trust" / "contexts" / f"sha256-{trust['sha256']}.json"
        or trust["path"] != str(trust_path)
        or not _digest(trust["sha256"])
    ):
        raise ValueError(f"{label} trust context is invalid")
    producer = _exact_dict(
        smoke["producer"],
        {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        },
        f"{label} producer",
    )
    validator = _exact_dict(
        smoke["validator"],
        {"validator_id", "contract", "implementation_sha256"},
        f"{label} validator",
    )
    if (
        producer["producer_id"] != SMOKE_PRODUCER_NAME
        or producer["contract"] != SMOKE_BUNDLE_CONTRACT
        or not _digest(producer["implementation_sha256"])
        or producer["validator_id"] != SMOKE_VALIDATOR_NAME
        or producer["validator_contract"] != SMOKE_BUNDLE_CONTRACT
        or not _digest(producer["validator_implementation_sha256"])
        or validator["validator_id"] != SMOKE_VALIDATOR_NAME
        or validator["contract"] != SMOKE_BUNDLE_CONTRACT
        or not _digest(validator["implementation_sha256"])
        or producer["validator_implementation_sha256"]
        != validator["implementation_sha256"]
        or not isinstance(smoke["expected_projection"], dict)
        or not _digest(smoke["expected_envelope_sha256"])
    ):
        raise ValueError(f"{label} authority is invalid")
    anchor = _validate_activation_anchor_shape(
        smoke["expected_anchor"],
        active_record_sha256=active_record_sha256,
        bundle_sha256=bundle["sha256"],
        trust_context_sha256=trust["sha256"],
        label=f"{label} expected anchor",
    )
    envelope = {
        "contract": ENVELOPE_CONTRACT,
        "anchor": anchor,
        "witness": {
            "contract": CANONICAL_PROJECTION_CONTRACT,
            "bundle_sha256": bundle["sha256"],
            "producer": producer,
            "validator": validator,
            "projection": smoke["expected_projection"],
            "trust_context_sha256": trust["sha256"],
            "historical": False,
        },
    }
    if _sha(_canonical(envelope) + b"\n") != smoke["expected_envelope_sha256"]:
        raise ValueError(f"{label} expected envelope binding is invalid")
    return smoke


def _validate_activation_unit_shape(
    value: object,
    root: Path,
    effective_uid: int,
    label: str,
) -> dict[str, Any]:
    unit = _exact_dict(
        value,
        {"state", "deployment_receipt", "active_record", "control_set", "smoke"},
        label,
    )
    bound_fields = ("deployment_receipt", "active_record", "control_set", "smoke")
    if unit["state"] == "absent":
        if any(unit[field] is not None for field in bound_fields):
            raise ValueError(f"{label} absent state is invalid")
        return unit
    if unit["state"] != "active" or any(unit[field] is None for field in bound_fields):
        raise ValueError(f"{label} active state is invalid")
    _validate_activation_file_binding_shape(
        unit["deployment_receipt"],
        root / "deployment.json",
        effective_uid,
        0o600,
        MAX_DOCUMENT_BYTES,
        f"{label} deployment receipt",
    )
    active_record = _validate_activation_file_binding_shape(
        unit["active_record"],
        root / "active.json",
        effective_uid,
        0o600,
        MAX_DOCUMENT_BYTES,
        f"{label} active record",
    )
    control_set = _exact_dict(
        unit["control_set"],
        {"shim", "client", "launcher", "controller", "policy"},
        f"{label} control set",
    )
    control_paths = {
        "shim": root / "task-witness",
        "client": root / "client" / "task_witness_client.py",
        "launcher": root / "launcher" / "task_witness_launch.py",
        "controller": root / "controller" / "task_witness_deploy.py",
        "policy": root / "controller" / "policy.json",
    }
    for role, path in control_paths.items():
        _validate_activation_file_binding_shape(
            control_set[role],
            path,
            effective_uid,
            0o600 if role == "policy" else 0o500,
            MAX_DOCUMENT_BYTES if role == "policy" else MAX_CONTROL_FILE_BYTES,
            f"{label} {role}",
        )
    _validate_activation_smoke_shape(
        unit["smoke"],
        root,
        effective_uid,
        active_record["sha256"],
        f"{label} smoke",
    )
    return unit


def _validate_bridge_transition_projection(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("bridge transition projection is invalid")
    execution_class = value.get("execution_class")
    required = {
        "execution_class",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "transition_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    }
    if execution_class == "live-migration":
        required.add("prior_rehearsal")
    projection = _exact_dict(value, required, "bridge transition projection")
    if execution_class not in {"isolated-rehearsal", "live-migration"} or any(
        not _digest(projection[key])
        for key in required
        if key not in {"execution_class", "prior_rehearsal"}
    ):
        raise ValueError("bridge transition projection is invalid")
    if execution_class == "live-migration":
        rehearsal = _exact_dict(
            projection["prior_rehearsal"],
            {
                "endpoint_projection_sha256",
                "transaction_sha256",
                "terminal_result_sha256",
                "active_receipt_sha256",
            },
            "bridge transition prior rehearsal",
        )
        if any(not _digest(item) for item in rehearsal.values()):
            raise ValueError("bridge transition prior rehearsal is invalid")
    return projection


def _activation_receipt_profiles(
    transaction: dict[str, Any],
) -> tuple[bool, str, str, str]:
    keys = frozenset(transaction)
    if keys == ACTIVATION_TRANSACTION_KEYS:
        bridge_transition = False
    elif keys == BRIDGE_ACTIVATION_TRANSACTION_KEYS:
        bridge_transition = True
    else:
        raise ValueError("activation transaction contract mismatch")
    phase = transaction["phase"]
    if CLIENT_RELEASE_PROFILE == "tw4-current":
        if bridge_transition:
            if phase != "candidate-smoke":
                raise ValueError("client release profile rejects activation surface")
            return (
                True,
                CURRENT_RECEIPT_PROFILE,
                CURRENT_RECEIPT_PROFILE,
                BRIDGE_LEGACY_RECEIPT_PROFILE,
            )
        if phase not in {"candidate-smoke", "rollback-smoke"}:
            raise ValueError("client release profile rejects activation surface")
        return (
            False,
            CURRENT_RECEIPT_PROFILE,
            CURRENT_RECEIPT_PROFILE,
            CURRENT_RECEIPT_PROFILE,
        )
    if CLIENT_RELEASE_PROFILE == "b1-transition":
        if bridge_transition:
            if phase != "rollback-smoke":
                raise ValueError("client release profile rejects activation surface")
            return (
                True,
                BRIDGE_LEGACY_RECEIPT_PROFILE,
                CURRENT_RECEIPT_PROFILE,
                BRIDGE_LEGACY_RECEIPT_PROFILE,
            )
        if phase != "candidate-smoke":
            raise ValueError("client release profile rejects activation surface")
        return (
            False,
            BRIDGE_LEGACY_RECEIPT_PROFILE,
            BRIDGE_LEGACY_RECEIPT_PROFILE,
            BRIDGE_LEGACY_RECEIPT_PROFILE,
        )
    raise ValueError("client release profile is unsupported")


def _validate_activation_transaction(
    raw: bytes,
    root: Path,
) -> dict[str, Any]:
    transaction = _document(
        _json(raw, "activation transaction"),
        "activation transaction",
    )
    bridge_transition, _, _, prior_receipt_profile = _activation_receipt_profiles(
        transaction
    )
    if (
        type(transaction["schema_version"]) is not int
        or transaction["schema_version"] != 1
        or transaction["contract"] != ACTIVATION_TRANSACTION_CONTRACT
        or not _digest(transaction["transaction_id"])
        or type(transaction["sequence"]) is not int
        or transaction["sequence"] < 1
        or (
            transaction["previous_journal_sha256"] is not None
            and not _digest(transaction["previous_journal_sha256"])
        )
        or (transaction["sequence"] == 1)
        != (transaction["previous_journal_sha256"] is None)
        or transaction["transaction_class"]
        not in {"routine-payload", "control-set-maintenance"}
        or transaction["phase"] not in {"candidate-smoke", "rollback-smoke"}
        or transaction["canonical_root"] != str(root)
        or type(transaction["effective_uid"]) is not int
        or transaction["effective_uid"] != os.geteuid()
        or not _digest(transaction["outer_maintenance_transaction_sha256"])
    ):
        raise ValueError("activation transaction contract mismatch")
    effective_uid = transaction["effective_uid"]
    activation_lock = _exact_dict(
        transaction["activation_lock"],
        {"path", "device", "inode", "owner", "mode"},
        "activation transaction lock binding",
    )
    lock_path = _absolute(
        activation_lock["path"],
        "activation transaction lock path",
    )
    if (
        lock_path != root / "activation.lock"
        or activation_lock["path"] != str(lock_path)
        or any(
            not _nonnegative_integer(activation_lock[field])
            for field in ("device", "inode", "owner", "mode")
        )
        or activation_lock["owner"] != effective_uid
        or activation_lock["mode"] != 0o600
    ):
        raise ValueError("activation transaction lock binding is invalid")
    stage = _exact_dict(
        transaction["stage"],
        {
            "receipt_path",
            "receipt_sha256",
            "plan_sha256",
            "authorization_sha256",
            "maintenance_transaction_sha256",
        },
        "activation transaction stage",
    )
    stage_path = _absolute(stage["receipt_path"], "activation stage receipt path")
    if (
        stage["receipt_path"] != str(stage_path)
        or any(
            not _digest(stage[field])
            for field in (
                "receipt_sha256",
                "plan_sha256",
                "authorization_sha256",
                "maintenance_transaction_sha256",
            )
        )
        or stage["maintenance_transaction_sha256"]
        != transaction["outer_maintenance_transaction_sha256"]
    ):
        raise ValueError("activation transaction stage is invalid")
    prior = _validate_activation_unit_shape(
        transaction["prior"],
        root,
        effective_uid,
        "activation transaction prior unit",
    )
    candidate = _validate_activation_unit_shape(
        transaction["candidate"],
        root,
        effective_uid,
        "activation transaction candidate unit",
    )
    rollback_authority = _exact_dict(
        transaction["rollback_authority"],
        {"receipt_path", "receipt_sha256", "target_state"},
        "activation transaction rollback authority",
    )
    rollback_path = _absolute(
        rollback_authority["receipt_path"],
        "activation rollback receipt path",
    )
    if (
        not _digest(rollback_authority["receipt_sha256"])
        or rollback_path
        != root / "receipts" / f"sha256-{rollback_authority['receipt_sha256']}.json"
        or rollback_authority["receipt_path"] != str(rollback_path)
        or rollback_authority["target_state"] != prior["state"]
    ):
        raise ValueError("activation transaction rollback authority is invalid")
    preimage = _exact_dict(
        transaction["preimage"],
        {"manifest_path", "manifest_sha256", "artifacts", "external_dependencies"},
        "activation transaction preimage",
    )
    preimage_path = _absolute(
        preimage["manifest_path"],
        "activation preimage manifest path",
    )
    if (
        preimage_path != rollback_path
        or preimage["manifest_path"] != str(preimage_path)
        or preimage["manifest_sha256"] != rollback_authority["receipt_sha256"]
    ):
        raise ValueError("activation transaction preimage is invalid")
    if prior["state"] == "absent":
        if preimage["artifacts"] != [] or preimage["external_dependencies"] != []:
            raise ValueError("activation transaction preimage is invalid")
    else:
        artifacts = preimage["artifacts"]
        if transaction["transaction_class"] == "control-set-maintenance":
            prior_controls = {
                **prior["control_set"],
                "smoke-bundle-manifest": prior["smoke"]["bundle"]["manifest"],
            }
            expected_artifacts = (
                (
                    "controller",
                    prior_controls["controller"],
                    0o500,
                    MAX_CONTROL_FILE_BYTES,
                ),
                ("policy", prior_controls["policy"], 0o600, MAX_DOCUMENT_BYTES),
                (
                    "launcher",
                    prior_controls["launcher"],
                    0o500,
                    MAX_CONTROL_FILE_BYTES,
                ),
                (
                    "client",
                    prior_controls["client"],
                    0o500,
                    MAX_CONTROL_FILE_BYTES,
                ),
                (
                    "smoke-bundle-manifest",
                    prior_controls["smoke-bundle-manifest"],
                    0o600,
                    MAX_DOCUMENT_BYTES,
                ),
                ("active-record", prior["active_record"], 0o600, MAX_DOCUMENT_BYTES),
                (
                    "deployment-alias",
                    prior["deployment_receipt"],
                    0o600,
                    MAX_DOCUMENT_BYTES,
                ),
                ("shim", prior_controls["shim"], 0o500, MAX_CONTROL_FILE_BYTES),
            )
        else:
            expected_artifacts = (
                ("active-record", prior["active_record"], 0o600, MAX_DOCUMENT_BYTES),
                (
                    "deployment-alias",
                    prior["deployment_receipt"],
                    0o600,
                    MAX_DOCUMENT_BYTES,
                ),
            )
        if not isinstance(artifacts, list) or len(artifacts) != len(expected_artifacts):
            raise ValueError("activation transaction preimage is invalid")
        staged_paths: set[Path] = set()
        for index, (
            (role, expected_installed, installed_mode, limit),
            item_value,
        ) in enumerate(zip(expected_artifacts, artifacts)):
            item = _exact_dict(
                item_value,
                {"role", "staged", "installed"},
                f"activation transaction preimage artifact[{index}]",
            )
            installed = _validate_activation_file_binding_shape(
                item["installed"],
                Path(expected_installed["path"]),
                effective_uid,
                installed_mode,
                limit,
                f"activation transaction preimage artifact[{index}] installed",
            )
            staged_value = _exact_dict(
                item["staged"],
                {"path", "length", "sha256", "owner", "mode"},
                f"activation transaction preimage artifact[{index}] staged",
            )
            staged_path = _absolute(
                staged_value["path"],
                f"activation transaction preimage artifact[{index}] staged path",
            )
            staged = _validate_activation_file_binding_shape(
                staged_value,
                staged_path,
                effective_uid,
                0o600,
                limit,
                f"activation transaction preimage artifact[{index}] staged",
            )
            if (
                item["role"] != role
                or not _same_json(installed, expected_installed)
                or staged_path == Path(installed["path"])
                or staged_path in staged_paths
                or any(
                    staged[key] != installed[key]
                    for key in ("length", "sha256", "owner")
                )
            ):
                raise ValueError("activation transaction preimage is invalid")
            staged_paths.add(staged_path)
        external = _exact_dict(
            preimage["external_dependencies"],
            {
                "interpreter",
                "runtime_closure",
                "process_profile",
                "receipt_parser",
            },
            "activation transaction preimage external dependencies",
        )
        interpreter = _exact_dict(
            external["interpreter"],
            {"executable", "implementation", "version", "executable_sha256"},
            "activation transaction preimage interpreter",
        )
        executable = _absolute(
            interpreter["executable"],
            "activation transaction preimage interpreter executable",
        )
        version = _exact_dict(
            interpreter["version"],
            {"major", "minor", "micro"},
            "activation transaction preimage interpreter version",
        )
        parser = _exact_dict(
            external["receipt_parser"],
            {
                "deployment_receipt_contract",
                "rollback_receipt_contract",
                "controller",
                "client",
            },
            "activation transaction preimage receipt parser",
        )
        _validate_activation_file_binding_shape(
            parser["controller"],
            root / "controller" / "task_witness_deploy.py",
            effective_uid,
            0o500,
            MAX_CONTROL_FILE_BYTES,
            "activation transaction preimage controller",
        )
        _validate_activation_file_binding_shape(
            parser["client"],
            root / "client" / "task_witness_client.py",
            effective_uid,
            0o500,
            MAX_CONTROL_FILE_BYTES,
            "activation transaction preimage client",
        )
        if (
            interpreter["executable"] != str(executable)
            or not _text(interpreter["implementation"])
            or any(
                type(version[part]) is not int or version[part] < 0
                for part in ("major", "minor", "micro")
            )
            or not _digest(interpreter["executable_sha256"])
            or not _same_json(external["process_profile"], PROCESS_PROFILE)
            or parser["deployment_receipt_contract"]
            != (
                LEGACY_DEPLOYMENT_RECEIPT_CONTRACT
                if prior_receipt_profile == BRIDGE_LEGACY_RECEIPT_PROFILE
                else DEPLOYMENT_RECEIPT_CONTRACT
            )
            or parser["rollback_receipt_contract"] != ROLLBACK_RECEIPT_CONTRACT
        ):
            raise ValueError("activation transaction preimage is invalid")
        _validate_receipt_runtime_closure(external["runtime_closure"])
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": transaction["transaction_class"],
        "canonical_root": transaction["canonical_root"],
        "effective_uid": effective_uid,
        "activation_lock": activation_lock,
        "outer_maintenance_transaction_sha256": transaction[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": stage,
        "prior": prior,
        "candidate": candidate,
        "rollback_authority": rollback_authority,
        "preimage": preimage,
    }
    if bridge_transition:
        identity["bridge_transition"] = _validate_bridge_transition_projection(
            transaction["bridge_transition"]
        )
    if transaction["transaction_id"] != _sha(_canonical(identity)):
        raise ValueError("activation transaction intent identity mismatch")
    handoff = _exact_dict(
        transaction["smoke_handoff"],
        {
            "target_deployment_receipt_sha256",
            "smoke_bundle_sha256",
            "smoke_trust_context_sha256",
        },
        "activation transaction smoke handoff",
    )
    if any(not _digest(value) for value in handoff.values()):
        raise ValueError("activation transaction smoke handoff is invalid")
    unit = candidate if transaction["phase"] == "candidate-smoke" else prior
    if (
        candidate["state"] != "active"
        or unit["state"] != "active"
        or (
            prior["state"] == "absent"
            and (
                transaction["transaction_class"] != "control-set-maintenance"
                or transaction["phase"] != "candidate-smoke"
            )
        )
        or prior["state"] == "active"
        and transaction["transaction_class"]
        not in {"routine-payload", "control-set-maintenance"}
        or transaction["pending_step"] is not None
        or transaction["candidate_smoke_acceptance"] is not None
        or transaction["rollback_smoke_acceptance"] is not None
        or transaction["terminal_result"] is not None
        or handoff["target_deployment_receipt_sha256"]
        != unit["deployment_receipt"]["sha256"]
        or handoff["smoke_bundle_sha256"] != unit["smoke"]["bundle"]["sha256"]
        or handoff["smoke_trust_context_sha256"]
        != unit["smoke"]["trust_context"]["sha256"]
    ):
        raise ValueError("activation transaction smoke phase is incoherent")
    return transaction


def _stage_relative_path(value: object, label: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"{label} is invalid")
    path = Path(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError(f"{label} is invalid")
    return value, path.parts


def _capture_control_maintenance_stage(
    transaction: dict[str, Any],
    root: Path,
    directories: list[DirectoryRecord],
    files: list[FileRecord],
    inventories: list[InventoryRecord],
) -> dict[str, Any] | None:
    if (
        transaction["transaction_class"] != "control-set-maintenance"
        or transaction["prior"]["state"] != "active"
    ):
        return None
    transaction_stage = transaction["stage"]
    stage_path = _absolute(
        transaction_stage["receipt_path"],
        "activation stage receipt path",
    )
    stage_root = stage_path.parent
    if (
        stage_path.name != "stage.json"
        or stage_root.is_relative_to(root)
        or root.is_relative_to(stage_root)
    ):
        raise ValueError("control maintenance stage path mismatch")
    stage_raw, stage_metadata = _capture_absolute_file(
        directories,
        files,
        stage_path,
        "control maintenance stage receipt",
        MAX_DOCUMENT_BYTES,
        private=True,
    )
    if (
        _sha(stage_raw) != transaction_stage["receipt_sha256"]
        or stage_metadata.st_uid != os.geteuid()
        or stage_metadata.st_nlink != 1
        or stat.S_IMODE(stage_metadata.st_mode) != 0o600
    ):
        raise ValueError("control maintenance stage receipt mismatch")
    stage = _document(
        _json(stage_raw, "control maintenance stage receipt"),
        "control maintenance stage receipt",
    )
    (
        bridge_stage,
        _,
        candidate_receipt_profile,
        prior_receipt_profile,
    ) = _activation_receipt_profiles(transaction)
    stage_keys = {
        "schema_version",
        "contract",
        "staging_root",
        "canonical_root",
        "plan_sha256",
        "maintenance_transaction_sha256",
        "classification",
        "authorization",
        "rollback_receipt",
        "deployment_receipt",
        "artifacts",
        "content_sha256",
    }
    if bridge_stage:
        stage_keys.add("transition_evidence")
    stage = _exact_dict(
        stage,
        stage_keys,
        "control maintenance stage receipt",
    )
    classification = _exact_dict(
        stage["classification"],
        {"outcome", "reason"},
        "control maintenance stage classification",
    )
    expected_classification = (
        {
            "outcome": "authorized-bridge-transition",
            "reason": "exact-bridge-transition-authorization",
        }
        if bridge_stage
        else {
            "outcome": "authorized-control-set-maintenance",
            "reason": "exact-deployer-authorization",
        }
    )
    if classification != expected_classification:
        raise ValueError("control maintenance stage classification mismatch")
    authorization = _exact_dict(
        stage["authorization"],
        {"sha256", "content_sha256"},
        "control maintenance stage authorization",
    )
    rollback = _exact_dict(
        stage["rollback_receipt"],
        {"path", "sha256"},
        "control maintenance staged rollback receipt",
    )
    deployment = _exact_dict(
        stage["deployment_receipt"],
        {"path", "sha256"},
        "control maintenance staged deployment receipt",
    )
    if bridge_stage:
        evidence = _exact_dict(
            stage["transition_evidence"],
            {"manifest", "authorization"},
            "bridge transition stage evidence",
        )
        expected_paths = {
            "manifest": "bridge-transition-manifest.json",
            "authorization": "bridge-transition-authorization.json",
        }
        for role, relative in expected_paths.items():
            item = _exact_dict(
                evidence[role],
                {"relative_path", "path", "length", "sha256", "owner", "mode"},
                f"bridge transition stage {role}",
            )
            expected_path = stage_root / relative
            if (
                item["relative_path"] != relative
                or item["path"] != str(expected_path)
                or item["owner"] != os.geteuid()
                or item["mode"] != 0o600
            ):
                raise ValueError("bridge transition stage evidence mismatch")
            raw, metadata = _capture_absolute_file(
                directories,
                files,
                expected_path,
                f"bridge transition stage {role}",
                MAX_DOCUMENT_BYTES,
                private=True,
            )
            if (
                len(raw) != item["length"]
                or _sha(raw) != item["sha256"]
                or metadata.st_uid != item["owner"]
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != item["mode"]
            ):
                raise ValueError("bridge transition stage evidence mismatch")
    rollback_path = _absolute(
        rollback["path"],
        "control maintenance staged rollback receipt path",
    )
    deployment_path = _absolute(
        deployment["path"],
        "control maintenance staged deployment receipt path",
    )
    candidate_sha256 = transaction["candidate"]["deployment_receipt"]["sha256"]
    if (
        type(stage["schema_version"]) is not int
        or stage["schema_version"] != 1
        or stage["contract"] != STAGED_DEPLOYMENT_CONTRACT
        or stage["staging_root"] != str(stage_root)
        or stage["canonical_root"] != str(root)
        or stage["plan_sha256"] != transaction_stage["plan_sha256"]
        or stage["maintenance_transaction_sha256"]
        != transaction_stage["maintenance_transaction_sha256"]
        or classification != expected_classification
        or authorization["sha256"] != transaction_stage["authorization_sha256"]
        or not _digest(authorization["content_sha256"])
        or rollback_path
        != root
        / "receipts"
        / f"sha256-{transaction['rollback_authority']['receipt_sha256']}.json"
        or rollback["sha256"] != transaction["rollback_authority"]["receipt_sha256"]
        or deployment_path != root / "receipts" / f"sha256-{candidate_sha256}.json"
        or deployment["sha256"] != candidate_sha256
    ):
        raise ValueError("control maintenance stage authority mismatch")

    candidate_controls = {
        **transaction["candidate"]["control_set"],
        "smoke-bundle-manifest": transaction["candidate"]["smoke"]["bundle"][
            "manifest"
        ],
    }
    prior_controls = {
        **transaction["prior"]["control_set"],
        "smoke-bundle-manifest": transaction["prior"]["smoke"]["bundle"]["manifest"],
    }
    special: dict[str, tuple[str, Path, dict[str, Any] | None]] = {}
    for role, relative, _, _ in CONTROL_PREIMAGE_SPECS:
        special[role] = (
            f"candidate/{relative}",
            root / relative,
            candidate_controls[role],
        )
        special[f"prior-{role}"] = (
            f"preimage/{relative}",
            root / relative,
            prior_controls[role],
        )
    special.update(
        {
            "active-record": (
                "candidate/active.json",
                root / "active.json",
                transaction["candidate"]["active_record"],
            ),
            "deployment-alias": (
                "candidate/deployment.json",
                root / "deployment.json",
                transaction["candidate"]["deployment_receipt"],
            ),
            "prior-active-record": (
                "preimage/active.json",
                root / "active.json",
                transaction["prior"]["active_record"],
            ),
            "prior-deployment-alias": (
                "preimage/deployment.json",
                root / "deployment.json",
                transaction["prior"]["deployment_receipt"],
            ),
            "rollback-receipt": (
                f"receipts/sha256-{rollback['sha256']}.json",
                rollback_path,
                None,
            ),
            "deployment-receipt": (
                f"receipts/sha256-{deployment['sha256']}.json",
                deployment_path,
                None,
            ),
        }
    )
    artifact_values = stage["artifacts"]
    if not isinstance(artifact_values, list) or not artifact_values:
        raise ValueError("control maintenance stage artifact inventory is invalid")
    relative_paths: list[str] = []
    relative_parts: list[tuple[str, ...]] = []
    captured: dict[str, list[tuple[dict[str, Any], bytes]]] = {}
    seen_non_module_roles: set[str] = set()
    for index, item_value in enumerate(artifact_values):
        label = f"control maintenance stage artifact[{index}]"
        item = _exact_dict(
            item_value,
            {"role", "relative_path", "staged", "installed"},
            label,
        )
        role = item["role"]
        if not _text(role) or (
            role != "validator-module" and role in seen_non_module_roles
        ):
            raise ValueError("control maintenance stage artifact roles conflict")
        if role != "validator-module":
            seen_non_module_roles.add(role)
        relative, parts = _stage_relative_path(
            item["relative_path"],
            f"{label} relative path",
        )
        relative_paths.append(relative)
        relative_parts.append(parts)
        staged_path = stage_root.joinpath(*parts)
        if role in special:
            expected_relative, installed_path, expected_binding = special[role]
            if relative != expected_relative:
                raise ValueError("control maintenance staged path binding mismatch")
        else:
            if relative.startswith(("candidate/", "preimage/")):
                raise ValueError("control maintenance staged path is unauthorized")
            installed_path = root.joinpath(*parts)
            expected_binding = None
        installed_value = _exact_dict(
            item["installed"],
            {"path", "length", "sha256", "owner", "mode"},
            f"{label} installed",
        )
        installed_mode = installed_value["mode"]
        if installed_mode not in {0o500, 0o600}:
            raise ValueError("control maintenance installed artifact mode mismatch")
        installed = _validate_activation_file_binding_shape(
            installed_value,
            installed_path,
            os.geteuid(),
            installed_mode,
            MAX_INTERPRETER_BYTES,
            f"{label} installed",
        )
        staged = _validate_activation_file_binding_shape(
            item["staged"],
            staged_path,
            os.geteuid(),
            0o600,
            MAX_INTERPRETER_BYTES,
            f"{label} staged",
        )
        if (
            expected_binding is not None and not _same_json(installed, expected_binding)
        ) or any(
            staged[key] != installed[key] for key in ("length", "sha256", "owner")
        ):
            raise ValueError("control maintenance stage artifact binding mismatch")
        raw, metadata = _capture_absolute_file(
            directories,
            files,
            staged_path,
            label,
            MAX_INTERPRETER_BYTES,
            private=True,
        )
        if (
            len(raw) != staged["length"]
            or _sha(raw) != staged["sha256"]
            or metadata.st_uid != staged["owner"]
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != staged["mode"]
        ):
            raise ValueError("control maintenance stage artifact bytes mismatch")
        captured.setdefault(role, []).append((item, raw))
    if relative_paths != sorted(relative_paths) or len(relative_paths) != len(
        set(relative_paths)
    ):
        raise ValueError("control maintenance stage artifacts are not ordered")
    if any(len(captured.get(role, [])) != 1 for role in special):
        raise ValueError("control maintenance stage artifacts are incomplete")

    transition_parts = (
        {
            ("bridge-transition-manifest.json",),
            ("bridge-transition-authorization.json",),
        }
        if bridge_stage
        else set()
    )
    file_parts = {("stage.json",), *relative_parts, *transition_parts}
    directory_parts = {
        parts[:index] for parts in relative_parts for index in range(1, len(parts))
    }
    if file_parts & directory_parts:
        raise ValueError("control maintenance stage inventory is invalid")
    expected_children: dict[tuple[str, ...], set[str]] = {
        (): {"stage.json", *(parts[0] for parts in transition_parts)}
    }
    for parts in relative_parts:
        for index, name in enumerate(parts):
            expected_children.setdefault(parts[:index], set()).add(name)
            if index + 1 < len(parts):
                expected_children.setdefault(parts[: index + 1], set())
    stage_chain = _open_directory_chain(
        stage_root,
        "control maintenance stage root",
        required_final_mode=0o700,
    )
    directories.extend(stage_chain)
    opened = {(): stage_chain[-1][0]}
    for parts in sorted(directory_parts, key=lambda value: (len(value), value)):
        opened[parts] = _open_directory_at(
            directories,
            opened[parts[:-1]],
            parts[-1],
            "control maintenance stage directory",
            private=True,
            required_mode=0o700,
        )
    for parts, expected in expected_children.items():
        _capture_inventory(
            inventories,
            opened[parts],
            expected,
            "control maintenance stage directory",
        )

    def one(role: str) -> tuple[dict[str, Any], bytes]:
        return captured[role][0]

    _, candidate_receipt_raw = one("deployment-receipt")
    _, candidate_alias_raw = one("deployment-alias")
    _, prior_receipt_raw = one("prior-deployment-alias")
    _, rollback_raw = one("rollback-receipt")
    _, candidate_policy_raw = one("policy")
    _, prior_policy_raw = one("prior-policy")
    candidate_receipt = _document(
        _json(candidate_receipt_raw, "staged candidate deployment receipt"),
        "staged candidate deployment receipt",
    )
    prior_receipt = _document(
        _json(prior_receipt_raw, "staged prior deployment receipt"),
        "staged prior deployment receipt",
    )
    candidate_policy = _validate_compatibility_policy(
        candidate_policy_raw,
        receipt_profile=candidate_receipt_profile,
    )
    prior_policy = _validate_compatibility_policy(
        prior_policy_raw,
        receipt_profile=prior_receipt_profile,
    )
    candidate_authorization = _validate_receipt_authorization(
        candidate_receipt["authorization"]
    )
    _validate_policy_receipt_binding(candidate_policy, candidate_receipt)
    _validate_policy_receipt_binding(prior_policy, prior_receipt)
    if (
        candidate_receipt_raw != candidate_alias_raw
        or _sha(candidate_receipt_raw) != candidate_sha256
        or _sha(prior_receipt_raw)
        != transaction["prior"]["deployment_receipt"]["sha256"]
        or _sha(rollback_raw) != rollback["sha256"]
        or not _same_json(
            candidate_receipt["control_set"],
            transaction["candidate"]["control_set"],
        )
        or not _same_json(
            candidate_receipt["smoke"],
            transaction["candidate"]["smoke"],
        )
        or not _same_json(
            prior_receipt["control_set"],
            transaction["prior"]["control_set"],
        )
        or not _same_json(
            prior_receipt["smoke"],
            transaction["prior"]["smoke"],
        )
        or candidate_receipt["prior_receipt_sha256"] != _sha(prior_receipt_raw)
        or not _same_json(
            candidate_receipt["rollback"],
            {"state": "active", **rollback},
        )
        or candidate_authorization["purpose"]
        not in {"source-boundary-change", "complete-control-set-maintenance"}
        or candidate_authorization["sha256"] != authorization["sha256"]
        or candidate_authorization["content_sha256"] != authorization["content_sha256"]
        or candidate_authorization["plan_sha256"] != stage["plan_sha256"]
        or candidate_authorization["maintenance_transaction_sha256"]
        != stage["maintenance_transaction_sha256"]
        or candidate_authorization["expected_active_receipt_sha256"]
        != _sha(prior_receipt_raw)
    ):
        raise ValueError("control maintenance staged receipt authority mismatch")
    if bridge_stage:
        if "migration" not in candidate_receipt:
            raise ValueError("bridge staged receipt migration is missing")
        _validate_bridge_migration_receipt(
            candidate_receipt["migration"],
            candidate_receipt,
        )
        if (
            prior_receipt.get("schema_version") != 1
            or prior_receipt.get("contract") != LEGACY_DEPLOYMENT_RECEIPT_CONTRACT
            or not _same_json(
                prior_receipt.get("contracts"),
                LEGACY_RECEIPT_CONTRACTS,
            )
        ):
            raise ValueError("bridge staged legacy receipt profile mismatch")
    return {
        "candidate_policy": candidate_policy,
        "prior_policy": prior_policy,
    }


def _probe_inherited_exclusive_lock(
    root: Path,
    root_descriptor: int,
    expected: dict[str, object],
    expected_identity: FilesystemIdentity,
) -> None:
    probe = None
    try:
        probe = os.open(
            "activation.lock",
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root_descriptor,
        )
        _recheck_activation_lock(
            root,
            root_descriptor,
            probe,
            expected,
            expected_identity,
        )
        try:
            fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in LOCK_WOULD_BLOCK_ERRNOS:
                return
            raise ValueError(
                "activation lock probe did not report would-block"
            ) from error
        raise ValueError("activation lock was not already held exclusively")
    finally:
        if probe is not None:
            os.close(probe)


def _accept_inherited_activation_lock(
    root: Path,
    root_descriptor: int,
    expected: dict[str, object],
) -> FilesystemIdentity:
    lock = 3
    try:
        fcntl.fcntl(lock, fcntl.F_GETFD)
        metadata = os.fstat(lock)
    except OSError as error:
        raise ValueError("activation smoke requires inherited descriptor 3") from error
    if (
        not _canonical_activation_lock_metadata(metadata)
        or _activation_lock_binding(root, metadata) != expected
    ):
        raise ValueError("inherited activation lock identity mismatch")
    identity = _filesystem_identity(metadata)
    _recheck_activation_lock(
        root,
        root_descriptor,
        lock,
        expected,
        identity,
    )
    _probe_inherited_exclusive_lock(
        root,
        root_descriptor,
        expected,
        identity,
    )
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise ValueError(
            "descriptor 3 does not own the inherited exclusive lock"
        ) from error
    _probe_inherited_exclusive_lock(
        root,
        root_descriptor,
        expected,
        identity,
    )
    os.set_inheritable(lock, False)
    restored_flags = fcntl.fcntl(lock, fcntl.F_GETFD)
    if os.get_inheritable(lock) or not (restored_flags & fcntl.FD_CLOEXEC):
        raise ValueError("activation lock close-on-exec could not be restored")
    return identity


def _validate_receipt_owned_smoke(
    value: object,
    root: Path,
    bundle_snapshot: _BundleSnapshot,
    trust_path: Path,
    expected_anchor: dict[str, Any],
    selected_trust: dict[str, Any],
) -> bytes:
    smoke = _exact_dict(
        value,
        {
            "bundle",
            "trust_context",
            "producer",
            "validator",
            "expected_projection",
            "expected_anchor",
            "expected_envelope_sha256",
        },
        "deployment smoke binding",
    )
    bundle = _exact_dict(
        smoke["bundle"],
        {"path", "sha256", "manifest"},
        "deployment smoke bundle binding",
    )
    bundle_path = _absolute(bundle["path"], "deployment smoke bundle path")
    manifest_record = bundle_snapshot.files.get("manifest.json")
    if (
        bundle_path != root / "smoke" / "bundle"
        or bundle_snapshot.path != bundle_path
        or bundle["sha256"] != bundle_snapshot.sha256
        or manifest_record is None
    ):
        raise ValueError("deployment smoke bundle binding mismatch")
    manifest_descriptor, _, manifest_raw = manifest_record
    _validate_file_binding(
        bundle["manifest"],
        bundle_path / "manifest.json",
        manifest_raw,
        os.fstat(manifest_descriptor),
        "deployment smoke manifest",
    )

    trust = _exact_dict(
        smoke["trust_context"],
        {"path", "sha256"},
        "deployment smoke trust-context binding",
    )
    if not _same_json(
        trust,
        {
            "path": str(trust_path),
            "sha256": expected_anchor["trust_context_sha256"],
        },
    ):
        raise ValueError("deployment smoke trust-context binding mismatch")

    roles = selected_trust["role_inventory"]
    producers = [
        item
        for item in roles["producers"]
        if item["producer_id"] == SMOKE_PRODUCER_NAME
        and item["contract"] == SMOKE_BUNDLE_CONTRACT
    ]
    validators = [
        item
        for item in roles["validators"]
        if item["validator_id"] == SMOKE_VALIDATOR_NAME
        and item["contract"] == SMOKE_BUNDLE_CONTRACT
    ]
    if len(producers) != 1 or len(validators) != 1:
        raise ValueError("deployment smoke authority is not intrinsic")
    expected_producer = {
        key: producers[0][key]
        for key in (
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        )
    }
    expected_validator = {
        key: validators[0][key]
        for key in ("validator_id", "contract", "implementation_sha256")
    }
    if (
        not _same_json(smoke["producer"], expected_producer)
        or not _same_json(smoke["validator"], expected_validator)
        or not isinstance(smoke["expected_projection"], dict)
        or not _same_json(smoke["expected_anchor"], expected_anchor)
        or not _digest(smoke["expected_envelope_sha256"])
    ):
        raise ValueError("deployment smoke expected result binding mismatch")
    expected_envelope = {
        "contract": ENVELOPE_CONTRACT,
        "anchor": expected_anchor,
        "witness": {
            "contract": CANONICAL_PROJECTION_CONTRACT,
            "bundle_sha256": bundle_snapshot.sha256,
            "producer": expected_producer,
            "validator": expected_validator,
            "projection": smoke["expected_projection"],
            "trust_context_sha256": expected_anchor["trust_context_sha256"],
            "historical": False,
        },
    }
    expected_raw = _canonical(expected_envelope) + b"\n"
    if _sha(expected_raw) != smoke["expected_envelope_sha256"]:
        raise ValueError("deployment smoke envelope digest mismatch")
    return expected_raw


def _acquire_shared_lock(
    root: Path,
    invocation: InvocationState,
) -> tuple[
    int,
    dict[str, object],
    FilesystemIdentity,
    list[DirectoryRecord],
]:
    chain = _open_directory_chain(
        root,
        "canonical root",
        required_final_mode=0o700,
    )
    descriptor = None
    try:
        root_descriptor = chain[-1][0]
        before = os.stat(
            "activation.lock",
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
        ):
            raise ValueError(
                "activation lock is not a current-user-private regular file"
            )
        descriptor = os.open(
            "activation.lock",
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root_descriptor,
        )
        opened = os.fstat(descriptor)
        visible = os.stat(
            "activation.lock",
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        identity = _descriptor_identity(opened)
        if (
            not _canonical_activation_lock_metadata(opened)
            or identity != _descriptor_identity(before)
            or identity != _descriptor_identity(visible)
        ):
            raise ValueError("activation lock changed during descriptor open")
        _reject_macos_allow_acl(descriptor, "activation lock")
        activation_lock = _activation_lock_binding(root, opened)
        activation_lock_identity = _filesystem_identity(opened)
        deadline = time.monotonic() + PROCESS_PROFILE["shared_lock_seconds"]
        while True:
            invocation.raise_if_cancelled()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                _recheck_directory_chain(chain, "canonical root")
                _recheck_activation_lock(
                    root,
                    root_descriptor,
                    descriptor,
                    activation_lock,
                    activation_lock_identity,
                )
                return (
                    descriptor,
                    activation_lock,
                    activation_lock_identity,
                    chain,
                )
            except BlockingIOError:
                _recheck_directory_chain(chain, "canonical root")
                _recheck_activation_lock(
                    root,
                    root_descriptor,
                    descriptor,
                    activation_lock,
                    activation_lock_identity,
                )
                if time.monotonic() >= deadline:
                    raise ClientError(
                        "activation lock is unavailable",
                        EXIT_RESOURCE,
                    )
                time.sleep(0.01)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        _close_descriptors([item[0] for item in chain])
        raise


def _close_quietly(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _cloexec_pipe() -> tuple[int, int]:
    read_descriptor = None
    write_descriptor = None
    try:
        read_descriptor, write_descriptor = os.pipe()
        os.set_inheritable(read_descriptor, False)
        os.set_inheritable(write_descriptor, False)
        if os.get_inheritable(read_descriptor) or os.get_inheritable(write_descriptor):
            raise OSError(errno.EIO, "pipe close-on-exec setup failed")
        return read_descriptor, write_descriptor
    except BaseException:
        _close_quietly(read_descriptor)
        _close_quietly(write_descriptor)
        raise


def _require_no_direct_children() -> None:
    if not _no_direct_children():
        raise ClientError(
            "client has a pre-existing direct child",
            EXIT_INSTALLATION,
        )


def _open_descriptor_inventory_once() -> tuple[int, ...]:
    directory_descriptor = os.open(
        "/dev/fd",
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY,
    )
    try:
        names = os.listdir(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    descriptors: set[int] = set()
    for name in names:
        if not isinstance(name, str) or not name.isascii() or not name.isdecimal():
            raise OSError(errno.EIO, "open-descriptor inventory is invalid")
        descriptor = int(name)
        try:
            fcntl.fcntl(descriptor, fcntl.F_GETFD)
        except OSError as error:
            if error.errno == errno.EBADF:
                continue
            raise
        descriptors.add(descriptor)
    return tuple(sorted(descriptors))


def _proven_open_descriptor_inventory() -> tuple[int, ...]:
    first = _open_descriptor_inventory_once()
    second = _open_descriptor_inventory_once()
    if first != second:
        raise OSError(errno.EIO, "open-descriptor inventory changed")
    return second


def _close_inherited_descriptors(
    descriptors: tuple[int, ...],
    preserved: tuple[int, ...],
) -> None:
    for descriptor in descriptors:
        if descriptor <= 2 or descriptor in preserved:
            continue
        try:
            os.close(descriptor)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise


def _publish_fork_result(context: _CleanupContext, pid: int) -> int:
    if pid > 0:
        context.publish_pid(pid)
    return pid


def _fork_and_publish(context: _CleanupContext) -> int:
    """Create a child and publish ownership in one callback-safe operation."""
    try:
        return _publish_fork_result(context, os.fork())
    except BaseException as error:  # noqa: BLE001
        context.reject_pid_publication(error)
        raise


def _publish_wait_result(
    target: object,
    pid: int,
    result: tuple[int, int],
) -> tuple[int, int]:
    waited, status = result
    if waited == pid:
        _mark_child_reaped(target)
    else:
        _mark_child_owned(target)
    return waited, status


def _waitpid_with_lifecycle(target: object, pid: int) -> tuple[int, int]:
    """Observe an exact child and publish its lifecycle before callbacks run."""
    try:
        return _publish_wait_result(target, pid, os.waitpid(pid, os.WNOHANG))
    except MemoryError:
        _mark_child_ambiguous(target)
        raise
    except ChildProcessError:
        _mark_child_lost(target)
        raise
    except OSError as error:
        if error.errno == errno.ECHILD:
            _mark_child_lost(target)
        else:
            _mark_child_ambiguous(target)
        raise
    except BaseException:  # noqa: BLE001
        lifecycle = _child_lifecycle(target)
        if lifecycle is None or lifecycle.state != "reaped":
            _mark_child_ambiguous(target)
        raise


def _publish_wildcard_wait_result(
    context: _CleanupContext,
    result: tuple[int, int],
) -> str:
    context.wildcard_waited = result[0]
    return "reaped" if result[0] > 0 else "pending"


def _wildcard_wait_step(context: _CleanupContext) -> str:
    """Classify one wildcard wait before any callback can split its result."""
    context.wildcard_waited = None
    try:
        return _publish_wildcard_wait_result(
            context,
            os.waitpid(-1, os.WNOHANG),
        )
    except InterruptedError:
        return "interrupted"
    except ChildProcessError:
        return "lost"
    except OSError as error:
        if error.errno == errno.ECHILD:
            return "lost"
        context.record(error)
        return "fault"
    except BaseException as error:  # noqa: BLE001
        context.record(error)
        return (
            "reaped"
            if context.wildcard_waited is not None and context.wildcard_waited > 0
            else "fault"
        )


def _wildcard_reap_sole_child(context: _CleanupContext) -> None:
    deadline = context.lost_pid_deadline
    while True:
        state = _wildcard_wait_step(context)
        if state in {"reaped", "lost"}:
            if context.error is not None:
                raise ClientError(
                    "sole-child cleanup failed",
                    EXIT_RESOURCE,
                ) from context.error
            return
        remaining = _remaining_with_retry(
            deadline,
            context,
            _RETRY_WILDCARD_CLOCK,
        )
        if remaining is None or remaining <= 0:
            context.record(subprocess.TimeoutExpired(-1, 0))
            raise ClientError(
                "sole-child cleanup timed out"
                if state in {"pending", "interrupted"}
                else "sole-child cleanup failed",
                EXIT_RESOURCE,
            ) from context.error
        if state == "fault" and not context.retry(_RETRY_WILDCARD_WAIT):
            raise ClientError(
                "sole-child cleanup failed",
                EXIT_RESOURCE,
            ) from context.error
        try:
            time.sleep(min(0.01, remaining))
        except BaseException as error:  # noqa: BLE001
            context.record(error)
            if not context.retry(_RETRY_WILDCARD_WAIT):
                raise ClientError(
                    "sole-child cleanup failed",
                    EXIT_RESOURCE,
                ) from context.error


def _await_parent_gate(descriptor: int, deadline: float | None = None) -> bool:
    if deadline is None:
        interval = min(float(PROCESS_PROFILE["kill_reap_seconds"]) / 2, 0.5)
        deadline = time.monotonic() + max(interval, 0.01)
    while True:
        try:
            raw = os.read(descriptor, 1)
        except (BlockingIOError, InterruptedError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.005, remaining))
            continue
        return raw == b"\1"


def _await_child_ready(
    descriptor: int,
    deadline: float,
    invocation: InvocationState,
) -> bool:
    while True:
        invocation.raise_if_cancelled()
        try:
            raw = os.read(descriptor, 1)
        except (BlockingIOError, InterruptedError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.005, remaining))
            continue
        if raw != b"\1":
            return False
        invocation.raise_if_cancelled()
        return True


def _start_child_deadline(deadline: float | None = None) -> bool:
    interval = min(float(PROCESS_PROFILE["kill_reap_seconds"]) / 2, 0.5)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    if deadline is None:
        deadline = time.monotonic() + max(interval, 0.01)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    signal.setitimer(signal.ITIMER_REAL, min(max(interval, 0.01), remaining))
    return True


def _clear_child_deadline() -> None:
    signal.setitimer(signal.ITIMER_REAL, 0)


def _arm_child_gate(descriptor: int, deadline: float) -> bool:
    while True:
        if time.monotonic() >= deadline:
            return False
        try:
            return os.write(descriptor, b"\1") == 1
        except InterruptedError:
            if time.monotonic() >= deadline:
                return False
        except OSError:
            return False


def _launcher_child(
    command: list[str],
    environment: dict[str, str],
    standard_input: int,
    standard_output: int,
    standard_error: int,
    gate: int,
    ready: int,
    previous_mask: set[signal.Signals],
    inherited_descriptors: tuple[int, ...],
    startup_deadline: float,
) -> None:
    try:
        if not _start_child_deadline(startup_deadline):
            os._exit(1)
        for number in CANCELLATION_SIGNALS:
            signal.signal(number, signal.SIG_DFL)
        for number in PYTHON_RESTORED_SIGNALS:
            signal.signal(number, signal.SIG_DFL)
        os.setsid()
        os.umask(PROCESS_PROFILE["umask"])
        os.chdir(PROCESS_PROFILE["cwd"])
        os.dup2(standard_input, 0, inheritable=True)
        os.dup2(standard_output, 1, inheritable=True)
        os.dup2(standard_error, 2, inheritable=True)
        _close_inherited_descriptors(inherited_descriptors, (gate, ready))
        if not _arm_child_gate(ready, startup_deadline):
            os._exit(1)
        os.close(ready)
        if not _await_parent_gate(gate, startup_deadline):
            os._exit(1)
        os.close(gate)
        _clear_child_deadline()
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        os.execve(command[0], command, environment)
    except BaseException:  # noqa: BLE001
        os._exit(1)


def _spawn_launcher(
    command: list[str],
    invocation: InvocationState,
) -> _OwnedProcess:
    descriptors: set[int] = set()
    standard_output_stream = None
    standard_error_stream = None
    pid = None
    fork_attempted = False
    fork_error = None
    restore_error = None
    cleanup_context: _CleanupContext | None = None
    try:
        standard_input = os.open(
            os.devnull,
            os.O_RDONLY | os.O_CLOEXEC,
        )
        descriptors.add(standard_input)
        standard_output_read, standard_output_write = _cloexec_pipe()
        descriptors.update((standard_output_read, standard_output_write))
        standard_error_read, standard_error_write = _cloexec_pipe()
        descriptors.update((standard_error_read, standard_error_write))
        gate_read, gate_write = _cloexec_pipe()
        descriptors.update((gate_read, gate_write))
        ready_read, ready_write = _cloexec_pipe()
        descriptors.update((ready_read, ready_write))
        os.set_blocking(gate_read, False)
        os.set_blocking(ready_read, False)
        cancellation_signals = set(CANCELLATION_SIGNALS)
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK,
            cancellation_signals,
        )
        try:
            invocation.raise_if_cancelled()
            if signal.sigpending() & cancellation_signals:
                raise CLIENT_INTERRUPTED_ERROR
            _require_no_direct_children()
            try:
                inherited_descriptors = _proven_open_descriptor_inventory()
            except OSError as error:
                exit_code = (
                    EXIT_RESOURCE
                    if error.errno in RESOURCE_ERRNOS
                    else EXIT_INSTALLATION
                )
                raise ClientError(
                    "client open-descriptor inventory is unavailable",
                    exit_code,
                ) from error
            cleanup_context = _prepare_cleanup_context()
            if not cleanup_context.arm_fork_deadlines():
                raise ClientError(
                    "launcher child recovery deadline is unavailable",
                    EXIT_RESOURCE,
                ) from cleanup_context.error
            interval = min(float(PROCESS_PROFILE["kill_reap_seconds"]) / 2, 0.5)
            startup_deadline = time.monotonic() + max(interval, 0.01)
            fork_attempted = True
            try:
                pid = _fork_and_publish(cleanup_context)
            except BaseException as error:  # noqa: BLE001
                fork_error = error
                cleanup_context.record(error)
        finally:
            if pid != 0:
                try:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                except BaseException as error:  # noqa: BLE001
                    restore_error = error
        if pid == 0:
            _launcher_child(
                command,
                PROCESS_PROFILE["environment"],
                standard_input,
                standard_output_write,
                standard_error_write,
                gate_read,
                ready_write,
                previous_mask,
                inherited_descriptors,
                startup_deadline,
            )
            os._exit(1)

        for descriptor in (
            standard_input,
            standard_output_write,
            standard_error_write,
            gate_read,
            ready_write,
        ):
            _close_quietly(descriptor)
            descriptors.discard(descriptor)

        if pid is None:
            _close_quietly(ready_read)
            descriptors.discard(ready_read)
            _close_quietly(gate_write)
            descriptors.discard(gate_write)
            if fork_attempted:
                _wildcard_reap_sole_child(cleanup_context)
            if restore_error is not None:
                raise restore_error
            if fork_error is not None:
                raise fork_error
            raise ClientError(
                "launcher child creation failed",
                EXIT_RESOURCE,
            )

        try:
            if restore_error is not None:
                raise restore_error
            invocation.raise_if_cancelled()
            if not _await_child_ready(ready_read, startup_deadline, invocation):
                raise ClientError(
                    "launcher child readiness failed",
                    EXIT_RESOURCE,
                )
            _close_quietly(ready_read)
            descriptors.discard(ready_read)
            standard_output_stream = os.fdopen(
                standard_output_read,
                "rb",
                buffering=0,
            )
            descriptors.discard(standard_output_read)
            standard_error_stream = os.fdopen(
                standard_error_read,
                "rb",
                buffering=0,
            )
            descriptors.discard(standard_error_read)
            process = _OwnedProcess(
                pid,
                standard_output_stream,
                standard_error_stream,
                cleanup_context,
            )
            invocation.validator_code_executed = "unknown"
            invocation.active_state_changed = "unknown"
            invocation.raise_if_cancelled()
            if not _arm_child_gate(
                gate_write,
                startup_deadline,
            ):
                raise ClientError(
                    "launcher child gate failed",
                    EXIT_RESOURCE,
                )
            _close_quietly(gate_write)
            descriptors.discard(gate_write)
            return process
        except BaseException:
            _close_quietly(gate_write)
            descriptors.discard(gate_write)
            cleanup = _consume(pid, cleanup_context)
            if standard_output_stream is not None:
                standard_output_stream.close()
            if standard_error_stream is not None:
                standard_error_stream.close()
            if not cleanup.completed or cleanup.error is not None:
                cleanup_error = cleanup.error or RuntimeError(
                    f"launcher child cleanup ended {cleanup.lifecycle}"
                )
                raise ClientError(
                    "launcher child cleanup failed",
                    EXIT_RESOURCE,
                ) from cleanup_error
            raise
    finally:
        for descriptor in descriptors:
            _close_quietly(descriptor)


def _launch(
    receipt: dict[str, Any],
    launcher: Path,
    bundle: Path,
    trust: Path,
    historical: bool,
    invocation: InvocationState,
) -> bytes:
    invocation.raise_if_cancelled()
    if not _canonical_client_process(invocation):
        raise ClientError(
            "client process profile changed before launch",
            EXIT_INSTALLATION,
        )
    command = [
        receipt["interpreter"]["executable"],
        "-B",
        "-I",
        "-S",
        "-X",
        "disable-remote-debug",
        str(launcher),
        "validate",
        "--bundle",
        str(bundle),
        "--trust-context",
        str(trust),
    ]
    if historical:
        command.append("--historical")
    deadline = time.monotonic() + PROCESS_PROFILE["validation_deadline_seconds"]
    process = None
    completed = False
    try:
        try:
            process = _spawn_launcher(command, invocation)
        except OSError as error:
            invocation.raise_if_cancelled()
            if error.errno in RESOURCE_ERRNOS:
                raise ClientError(
                    "launcher resources are unavailable",
                    EXIT_RESOURCE,
                ) from error
            raise ClientError("launcher could not be started", EXIT_LAUNCH) from error
        invocation.raise_if_cancelled()
        stdout, stderr = _read_bounded_output(process, deadline, invocation)
        invocation.raise_if_cancelled()
        completed = True
    finally:
        if process is not None:
            try:
                _finalize_process_group(
                    process,
                    graceful=not completed,
                    context=process.cleanup,
                )
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
    invocation.raise_if_cancelled()
    if process.returncode != 0 or stderr:
        raise ClientError("launcher rejected validation", EXIT_LAUNCH)
    return stdout


def _remaining_with_retry(
    deadline: float,
    context: _CleanupContext,
    retry_slot: int,
) -> float | None:
    while True:
        try:
            return deadline - time.monotonic()
        except BaseException as error:  # noqa: BLE001
            context.record(error)
            if not context.retry(retry_slot):
                return None


def _sleep_with_retry(
    seconds: float,
    context: _CleanupContext,
    retry_slot: int,
) -> bool:
    try:
        time.sleep(seconds)
    except BaseException as error:  # noqa: BLE001
        context.record(error)
        return context.retry(retry_slot)
    return True


def _signal_error(operation: Any, pid: int, number: int) -> BaseException | None:
    try:
        operation(pid, number)
        return None
    except ProcessLookupError:
        return None
    except BaseException as error:  # noqa: BLE001
        return error


def _signal_with_retry(
    operation: Any,
    pid: int,
    number: int,
    *,
    deadline: float,
    ownership: object | None,
    context: _CleanupContext,
    retry_slot: int,
    clock_retry_slot: int,
    observation_target: Any | None = None,
    observation_retry_slot: int | None = None,
    observation_clock_retry_slot: int | None = None,
) -> BaseException | None:
    while True:
        if ownership is not None and not _may_signal_child(ownership):
            return context.error
        remaining = _remaining_with_retry(deadline, context, clock_retry_slot)
        if remaining is None or remaining <= 0:
            timeout = subprocess.TimeoutExpired(pid, 0)
            context.record(timeout)
            return timeout
        action_error = _signal_error(operation, pid, number)
        if action_error is None:
            return None
        context.record(action_error)
        if (
            observation_target is not None
            and observation_retry_slot is not None
            and observation_clock_retry_slot is not None
        ):
            _observe_group_signal_ownership(
                observation_target,
                action_error,
                deadline=deadline,
                context=context,
                retry_slot=observation_retry_slot,
                clock_retry_slot=observation_clock_retry_slot,
            )
            if not _may_signal_child(observation_target):
                return action_error
        if not context.retry(retry_slot):
            return action_error


def _signal_process_group_kill_with_retry(
    process: Any,
    *,
    deadline: float,
    context: _CleanupContext,
) -> tuple[BaseException | None, bool]:
    delivered = False

    def kill_group(pid: int, number: int) -> None:
        nonlocal delivered
        os.killpg(pid, number)
        delivered = True

    error = _signal_with_retry(
        kill_group,
        process.pid,
        signal.SIGKILL,
        deadline=deadline,
        ownership=process,
        context=context,
        retry_slot=_RETRY_GROUP_KILL,
        clock_retry_slot=_RETRY_GROUP_KILL_CLOCK,
        observation_target=process,
        observation_retry_slot=_RETRY_GROUP_KILL_OBSERVATION,
        observation_clock_retry_slot=_RETRY_GROUP_KILL_OBSERVATION_CLOCK,
    )
    if error is None and not delivered:
        error = ClientError(
            "validation process group disappeared before cleanup",
            EXIT_RESOURCE,
        )
        context.record(error)
    return error, delivered


def _observe_group_signal_ownership(
    process: Any,
    error: BaseException | None,
    *,
    deadline: float,
    context: _CleanupContext,
    retry_slot: int,
    clock_retry_slot: int,
) -> None:
    if not isinstance(error, PermissionError):
        return
    while _responsible_for_child(process):
        remaining = _remaining_with_retry(deadline, context, clock_retry_slot)
        if remaining is None or remaining <= 0:
            if remaining is not None:
                context.record(subprocess.TimeoutExpired(process.pid, 0))
            _mark_child_ambiguous(process)
            return
        try:
            _observe_exact_leader_state(process)
        except BaseException as observation_error:  # noqa: BLE001
            context.record(observation_error)
            if not _responsible_for_child(process):
                return
            _mark_child_ambiguous(process)
            if not context.retry(retry_slot):
                return
            continue
        _mark_child_owned(process)
        return


def _finalize_process_group(
    process: Any,
    *,
    graceful: bool,
    context: _CleanupContext,
) -> None:
    if not context.arm_cleanup(graceful=graceful):
        raise ClientError(
            "validation process group cleanup failed",
            EXIT_RESOURCE,
        ) from context.error
    if sys.platform == "darwin":
        _finalize_darwin_process_group(process, graceful=graceful, context=context)
        return

    if graceful:
        if _may_signal_child(process):
            _signal_with_retry(
                os.killpg,
                process.pid,
                signal.SIGTERM,
                deadline=context.grace_cutoff,
                ownership=process,
                context=context,
                retry_slot=_RETRY_GROUP_TERM,
                clock_retry_slot=_RETRY_GROUP_TERM_CLOCK,
                observation_target=process,
                observation_retry_slot=_RETRY_GROUP_TERM_OBSERVATION,
                observation_clock_retry_slot=_RETRY_GROUP_TERM_OBSERVATION_CLOCK,
            )
        while _responsible_for_child(process):
            remaining = _remaining_with_retry(
                context.grace_cutoff,
                context,
                _RETRY_GRACE_CLOCK,
            )
            if remaining is None or remaining <= 0:
                break
            if not _sleep_with_retry(
                min(0.05, remaining),
                context,
                _RETRY_NON_DARWIN_GRACE_SLEEP,
            ):
                break

    quiescent = False
    kill_attempted = False
    kill_barrier = False
    while _responsible_for_child(process):
        state = "live"
        if kill_attempted:
            state = _non_darwin_group_state_with_retry(
                process,
                context.cleanup_deadline,
                context,
                wait_clock_slot=_RETRY_NON_DARWIN_WAIT_CLOCK,
                probe_clock_slot=_RETRY_NON_DARWIN_PROBE_CLOCK,
                state_retry_slot=_RETRY_NON_DARWIN_GROUP_STATE,
            )
        if state is None:
            break
        if state == "quiescent":
            if kill_barrier:
                quiescent = True
            else:
                context.record(
                    ClientError(
                        "validation process group disappeared before cleanup",
                        EXIT_RESOURCE,
                    )
                )
            break
        if _may_signal_child(process):
            action_error, delivered = _signal_process_group_kill_with_retry(
                process,
                deadline=context.cleanup_deadline,
                context=context,
            )
            kill_barrier = kill_barrier or delivered
            if action_error is not None and _may_signal_child(process):
                _signal_with_retry(
                    os.kill,
                    process.pid,
                    signal.SIGKILL,
                    deadline=context.cleanup_deadline,
                    ownership=process,
                    context=context,
                    retry_slot=_RETRY_DIRECT_KILL,
                    clock_retry_slot=_RETRY_DIRECT_KILL_CLOCK,
                )
            kill_attempted = True
        remaining = _remaining_with_retry(
            context.cleanup_deadline,
            context,
            _RETRY_NON_DARWIN_KILL_CLOCK,
        )
        if remaining is None:
            break
        if remaining <= 0:
            context.record(subprocess.TimeoutExpired(process.pid, 0))
            break
        if not _sleep_with_retry(
            min(0.01, remaining),
            context,
            _RETRY_NON_DARWIN_KILL_SLEEP,
        ):
            break

    if quiescent and _responsible_for_child(process):
        _reap_owned_process(
            process,
            context.cleanup_deadline,
            context=context,
        )
    if context.error is not None:
        raise ClientError(
            "validation process group cleanup failed",
            EXIT_RESOURCE,
        ) from context.error


def _non_darwin_process_group_state(
    process: Any,
    deadline: float,
    context: _CleanupContext,
    *,
    probe_clock_slot: int,
) -> str:
    """Classify the owned launch group with Linux process-table evidence."""
    if sys.platform != "linux":
        raise ClientError(
            "validation process group quiescence is unavailable",
            EXIT_RESOURCE,
        )
    leader_terminal = _leader_exited_without_reaping(process)
    if not _responsible_for_child(process):
        raise ClientError(
            "validation child state became unavailable",
            EXIT_RESOURCE,
        )
    remaining = _remaining_with_retry(
        deadline,
        context,
        probe_clock_slot,
    )
    if remaining is None or remaining <= 0:
        raise subprocess.TimeoutExpired(process.pid, 0)
    first_states = _linux_process_group_member_states(
        process.pid,
        deadline=deadline,
        context=context,
    )
    if not leader_terminal or any(state not in {"X", "Z"} for state in first_states):
        return "live"
    _linux_proc_remaining(deadline, context, process.pid)
    second_states = _linux_process_group_member_states(
        process.pid,
        deadline=deadline,
        context=context,
    )
    if any(state not in {"X", "Z"} for state in second_states):
        return "live"
    return "quiescent"


def _linux_proc_remaining(
    deadline: float,
    context: _CleanupContext,
    pid: int,
) -> None:
    remaining = _remaining_with_retry(
        deadline,
        context,
        _RETRY_NON_DARWIN_PROC_CLOCK,
    )
    if remaining is None or remaining <= 0:
        raise subprocess.TimeoutExpired(pid, 0)


def _linux_proc_stat(
    path: str,
    deadline: float,
    context: _CleanupContext,
    pid: int,
) -> tuple[str, int, int]:
    _linux_proc_remaining(deadline, context, pid)
    try:
        with open(path, "rb", buffering=0) as status_file:
            raw = status_file.read(MAX_PROC_STAT_BYTES + 1)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        ) from error
    if len(raw) > MAX_PROC_STAT_BYTES:
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        )
    _, separator, fields = raw.rpartition(b") ")
    values = fields.split()
    if not separator or len(values) < 4 or len(values[0]) != 1:
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        )
    try:
        state = values[0].decode("ascii")
        member_pgid = int(values[2])
        member_session = int(values[3])
    except (UnicodeDecodeError, ValueError) as error:
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        ) from error
    return state, member_pgid, member_session


def _linux_process_group_member_states(
    pgid: int,
    *,
    deadline: float,
    context: _CleanupContext,
) -> tuple[str, ...]:
    """Return every task in the owned Linux session group, or fail closed."""
    try:
        _linux_proc_remaining(deadline, context, pgid)
        with os.scandir("/proc") as entries:
            process_ids = []
            for entry in entries:
                _linux_proc_remaining(deadline, context, pgid)
                if entry.name.isdecimal() and entry.is_dir(follow_symlinks=False):
                    process_ids.append(entry.name)
    except OSError as error:
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        ) from error

    states = []
    leader_seen = False
    for process_id in process_ids:
        try:
            _, member_pgid, member_session = _linux_proc_stat(
                f"/proc/{process_id}/stat",
                deadline,
                context,
                pgid,
            )
            if member_pgid != pgid or member_session != pgid:
                continue
            _linux_proc_remaining(deadline, context, pgid)
            with os.scandir(f"/proc/{process_id}/task") as entries:
                task_ids = []
                for entry in entries:
                    _linux_proc_remaining(deadline, context, pgid)
                    if entry.name.isdecimal() and entry.is_dir(follow_symlinks=False):
                        task_ids.append(entry.name)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ClientError(
                "validation process group state is unavailable",
                EXIT_RESOURCE,
            ) from error
        for task_id in task_ids:
            try:
                state, member_pgid, member_session = _linux_proc_stat(
                    f"/proc/{process_id}/task/{task_id}/stat",
                    deadline,
                    context,
                    pgid,
                )
            except FileNotFoundError:
                continue
            if member_pgid == pgid and member_session == pgid:
                states.append(state)
                if process_id == str(pgid) and task_id == str(pgid):
                    leader_seen = True
    if not leader_seen:
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        )
    return tuple(states)


def _non_darwin_wait_for_process_group_state(
    process: Any,
    deadline: float,
    context: _CleanupContext,
    *,
    wait_clock_slot: int,
    probe_clock_slot: int,
) -> str:
    while _responsible_for_child(process):
        remaining = _remaining_with_retry(
            deadline,
            context,
            wait_clock_slot,
        )
        if remaining is None or remaining <= 0:
            raise subprocess.TimeoutExpired(process.pid, 0)
        state = _non_darwin_process_group_state(
            process,
            deadline,
            context,
            probe_clock_slot=probe_clock_slot,
        )
        if state != "interrupted":
            return state
        if not _sleep_with_retry(
            min(0.01, remaining),
            context,
            _RETRY_NON_DARWIN_WAIT_SLEEP,
        ):
            break
    raise ClientError(
        "validation child state became unavailable",
        EXIT_RESOURCE,
    )


def _non_darwin_group_state_with_retry(
    process: Any,
    deadline: float,
    context: _CleanupContext,
    *,
    wait_clock_slot: int,
    probe_clock_slot: int,
    state_retry_slot: int,
) -> str | None:
    while True:
        try:
            return _non_darwin_wait_for_process_group_state(
                process,
                deadline,
                context,
                wait_clock_slot=wait_clock_slot,
                probe_clock_slot=probe_clock_slot,
            )
        except BaseException as error:  # noqa: BLE001
            context.record(error)
            if (
                not _responsible_for_child(process)
                or isinstance(error, subprocess.TimeoutExpired)
                or not context.retry(state_retry_slot)
            ):
                return None


def _darwin_process_group_state(
    process: Any,
    deadline: float,
    context: _CleanupContext,
    *,
    probe_clock_slot: int,
) -> str:
    """Classify an owned launch group without treating an action error as proof."""
    leader_terminal = _leader_exited_without_reaping(process)
    if not _responsible_for_child(process):
        raise ClientError(
            "validation child state became unavailable",
            EXIT_RESOURCE,
        )
    remaining = _remaining_with_retry(
        deadline,
        context,
        probe_clock_slot,
    )
    if remaining is None or remaining <= 0:
        raise subprocess.TimeoutExpired(process.pid, 0)
    try:
        os.killpg(process.pid, 0)
    except InterruptedError:
        return "interrupted"
    except PermissionError as error:
        if not leader_terminal:
            leader_terminal = _leader_exited_without_reaping(process)
        if leader_terminal:
            return "quiescent"
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        ) from error
    except OSError as error:
        raise ClientError(
            "validation process group state is unavailable",
            EXIT_RESOURCE,
        ) from error
    return "live"


def _darwin_wait_for_process_group_state(
    process: Any,
    deadline: float,
    context: _CleanupContext,
    *,
    wait_clock_slot: int,
    probe_clock_slot: int,
) -> str:
    while _responsible_for_child(process):
        remaining = _remaining_with_retry(
            deadline,
            context,
            wait_clock_slot,
        )
        if remaining is None or remaining <= 0:
            raise subprocess.TimeoutExpired(process.pid, 0)
        state = _darwin_process_group_state(
            process,
            deadline,
            context,
            probe_clock_slot=probe_clock_slot,
        )
        if state != "interrupted":
            return state
        time.sleep(min(0.01, remaining))
    raise ClientError(
        "validation child state became unavailable",
        EXIT_RESOURCE,
    )


def _darwin_group_state_with_retry(
    process: Any,
    deadline: float,
    context: _CleanupContext,
    *,
    wait_clock_slot: int,
    probe_clock_slot: int,
    state_retry_slot: int,
) -> str | None:
    while True:
        try:
            return _darwin_wait_for_process_group_state(
                process,
                deadline,
                context,
                wait_clock_slot=wait_clock_slot,
                probe_clock_slot=probe_clock_slot,
            )
        except BaseException as error:  # noqa: BLE001
            context.record(error)
            if (
                not _responsible_for_child(process)
                or isinstance(error, subprocess.TimeoutExpired)
                or not context.retry(state_retry_slot)
            ):
                return None


def _reap_owned_process(
    process: Any,
    deadline: float,
    *,
    context: _CleanupContext,
) -> BaseException | None:
    while process.returncode is None and _responsible_for_child(process):
        remaining = _remaining_with_retry(
            deadline,
            context,
            _RETRY_EXACT_REAP_CLOCK,
        )
        if remaining is None:
            return context.error
        if remaining <= 0:
            context.record(subprocess.TimeoutExpired(process.pid, 0))
            return context.error
        try:
            process.wait(deadline=deadline)
        except subprocess.TimeoutExpired as error:
            context.record(error)
            return context.error
        except BaseException as error:  # noqa: BLE001
            context.record(error)
            if not _responsible_for_child(process) or not context.retry(
                _RETRY_EXACT_REAP
            ):
                return context.error
    return context.error


def _finalize_darwin_process_group(
    process: Any,
    *,
    graceful: bool,
    context: _CleanupContext,
) -> None:
    if graceful and _may_signal_child(process):
        state = _darwin_group_state_with_retry(
            process,
            context.grace_cutoff,
            context,
            wait_clock_slot=_RETRY_DARWIN_PRETERM_WAIT_CLOCK,
            probe_clock_slot=_RETRY_DARWIN_PRETERM_PROBE_CLOCK,
            state_retry_slot=_RETRY_DARWIN_PRETERM_GROUP_STATE,
        )
        if state == "live" and _may_signal_child(process):
            _signal_with_retry(
                os.killpg,
                process.pid,
                signal.SIGTERM,
                deadline=context.grace_cutoff,
                ownership=process,
                context=context,
                retry_slot=_RETRY_GROUP_TERM,
                clock_retry_slot=_RETRY_GROUP_TERM_CLOCK,
                observation_target=process,
                observation_retry_slot=_RETRY_GROUP_TERM_OBSERVATION,
                observation_clock_retry_slot=_RETRY_GROUP_TERM_OBSERVATION_CLOCK,
            )
            if _responsible_for_child(process):
                while _responsible_for_child(process):
                    remaining = _remaining_with_retry(
                        context.grace_cutoff,
                        context,
                        _RETRY_GRACE_CLOCK,
                    )
                    if remaining is None or remaining <= 0:
                        break
                    if not _sleep_with_retry(
                        min(0.05, remaining),
                        context,
                        _RETRY_DARWIN_GRACE_SLEEP,
                    ):
                        break

    kill_attempted = False
    quiescent = False
    while _responsible_for_child(process):
        state = _darwin_group_state_with_retry(
            process,
            context.cleanup_deadline,
            context,
            wait_clock_slot=_RETRY_DARWIN_WAIT_CLOCK,
            probe_clock_slot=_RETRY_DARWIN_PROBE_CLOCK,
            state_retry_slot=_RETRY_DARWIN_GROUP_STATE,
        )
        if state is None:
            break
        if state == "quiescent":
            quiescent = True
            break
        if not kill_attempted and _may_signal_child(process):
            action_error = _signal_with_retry(
                os.killpg,
                process.pid,
                signal.SIGKILL,
                deadline=context.cleanup_deadline,
                ownership=process,
                context=context,
                retry_slot=_RETRY_GROUP_KILL,
                clock_retry_slot=_RETRY_GROUP_KILL_CLOCK,
                observation_target=process,
                observation_retry_slot=_RETRY_GROUP_KILL_OBSERVATION,
                observation_clock_retry_slot=_RETRY_GROUP_KILL_OBSERVATION_CLOCK,
            )
            kill_attempted = True
            if action_error is not None and _may_signal_child(process):
                _signal_with_retry(
                    os.kill,
                    process.pid,
                    signal.SIGKILL,
                    deadline=context.cleanup_deadline,
                    ownership=process,
                    context=context,
                    retry_slot=_RETRY_DIRECT_KILL,
                    clock_retry_slot=_RETRY_DIRECT_KILL_CLOCK,
                )
        remaining = _remaining_with_retry(
            context.cleanup_deadline,
            context,
            _RETRY_DARWIN_KILL_CLOCK,
        )
        if remaining is None:
            break
        if remaining <= 0:
            context.record(subprocess.TimeoutExpired(process.pid, 0))
            break
        if not _sleep_with_retry(
            min(0.01, remaining),
            context,
            _RETRY_DARWIN_KILL_SLEEP,
        ):
            break

    if (
        not quiescent
        and not kill_attempted
        and _may_signal_child(process)
        and context.error is not None
    ):
        _signal_with_retry(
            os.kill,
            process.pid,
            signal.SIGKILL,
            deadline=context.cleanup_deadline,
            ownership=process,
            context=context,
            retry_slot=_RETRY_DIRECT_KILL,
            clock_retry_slot=_RETRY_DIRECT_KILL_CLOCK,
        )
        _reap_owned_process(
            process,
            context.cleanup_deadline,
            context=context,
        )

    if (quiescent or kill_attempted) and _responsible_for_child(process):
        _reap_owned_process(
            process,
            context.cleanup_deadline,
            context=context,
        )

    if context.error is not None:
        raise ClientError(
            "validation process group cleanup failed",
            EXIT_RESOURCE,
        ) from context.error


def _observe_exact_leader_state(process: Any) -> bool:
    try:
        terminal = (
            os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
            is not None
        )
        _mark_child_owned(process)
        return terminal
    except ChildProcessError as error:
        _mark_child_lost(process)
        raise ClientError(
            "validation child state became unavailable",
            EXIT_RESOURCE,
        ) from error
    except OSError as error:
        if error.errno == errno.ECHILD:
            _mark_child_lost(process)
        raise ClientError(
            "validation child state became unavailable",
            EXIT_RESOURCE,
        ) from error


def _leader_exited_without_reaping(process: Any) -> bool:
    try:
        return _observe_exact_leader_state(process)
    except InterruptedError:
        return False


def _read_bounded_output(
    process: Any,
    deadline: float,
    invocation: InvocationState,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("launcher pipes are unavailable")
    buffers = {
        "stdout": bytearray(),
        "stderr": bytearray(),
    }
    limits = {
        "stdout": PROCESS_PROFILE["stdout_max_bytes"],
        "stderr": PROCESS_PROFILE["stderr_max_bytes"],
    }
    pipe_drain_deadline = None
    try:
        selector = selectors.DefaultSelector()
        with selector:
            for name, stream in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream.fileno(), selectors.EVENT_READ, name)
            while selector.get_map():
                invocation.raise_if_cancelled()
                now = time.monotonic()
                if (
                    _leader_exited_without_reaping(process)
                    and pipe_drain_deadline is None
                ):
                    pipe_drain_deadline = (
                        now + PROCESS_PROFILE["post_leader_pipe_drain_seconds"]
                    )
                current_deadline = (
                    min(deadline, pipe_drain_deadline)
                    if pipe_drain_deadline is not None
                    else deadline
                )
                remaining = current_deadline - now
                if remaining <= 0:
                    message = (
                        "launcher pipes remained open after exit"
                        if pipe_drain_deadline is not None
                        else "validation timed out"
                    )
                    raise ClientError(message, EXIT_RESOURCE)
                try:
                    ready = selector.select(min(remaining, 0.05))
                except InterruptedError:
                    continue
                invocation.raise_if_cancelled()
                for key, _ in ready:
                    name = key.data
                    try:
                        chunk = os.read(key.fd, PROCESS_PROFILE["io_chunk_bytes"])
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    if len(buffers[name]) + len(chunk) > limits[name]:
                        raise ClientError(
                            f"launcher {name} exceeded the limit",
                            EXIT_RESOURCE,
                        )
                    buffers[name].extend(chunk)
    except OSError as error:
        invocation.raise_if_cancelled()
        raise ClientError(
            "validation output resources are unavailable",
            EXIT_RESOURCE,
        ) from error
    while not _leader_exited_without_reaping(process):
        invocation.raise_if_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("validation timed out", EXIT_RESOURCE)
        time.sleep(min(remaining, 0.05))
    invocation.raise_if_cancelled()
    return bytes(buffers["stdout"]), bytes(buffers["stderr"])


def _accept(
    stdout: bytes,
    expected_anchor: dict[str, Any],
    selected_trust: dict[str, Any],
) -> bytes:
    try:
        envelope = _json(
            stdout,
            "launcher envelope",
            PROCESS_PROFILE["stdout_max_bytes"],
        )
    except ValueError as error:
        raise ClientError(str(error), EXIT_LAUNCH) from error
    if (
        set(envelope) != {"contract", "anchor", "witness"}
        or envelope.get("contract") != ENVELOPE_CONTRACT
    ):
        raise ClientError("launcher envelope contract mismatch", EXIT_LAUNCH)
    if not _same_json(envelope["anchor"], expected_anchor):
        raise ClientError("launcher anchor mismatch", EXIT_LAUNCH)
    witness = envelope["witness"]
    producer = witness.get("producer") if isinstance(witness, dict) else None
    validator = witness.get("validator") if isinstance(witness, dict) else None
    if (
        not isinstance(witness, dict)
        or set(witness)
        != {
            "contract",
            "bundle_sha256",
            "producer",
            "validator",
            "projection",
            "trust_context_sha256",
            "historical",
        }
        or witness.get("contract") != CANONICAL_PROJECTION_CONTRACT
        or not isinstance(producer, dict)
        or set(producer)
        != {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        }
        or not _token(producer.get("producer_id"))
        or not _text(producer.get("contract"))
        or not _digest(producer.get("implementation_sha256"))
        or not isinstance(validator, dict)
        or set(validator)
        != {
            "validator_id",
            "contract",
            "implementation_sha256",
        }
        or not _token(validator.get("validator_id"))
        or not _text(validator.get("contract"))
        or not _digest(validator.get("implementation_sha256"))
        or {
            "validator_id": producer.get("validator_id"),
            "contract": producer.get("validator_contract"),
            "implementation_sha256": producer.get("validator_implementation_sha256"),
        }
        != validator
        or not isinstance(witness.get("projection"), dict)
        or witness.get("bundle_sha256") != expected_anchor["bundle_sha256"]
        or witness.get("trust_context_sha256")
        != expected_anchor["trust_context_sha256"]
        or witness.get("historical") is not expected_anchor["historical"]
    ):
        raise ClientError("launcher witness binding mismatch", EXIT_LAUNCH)
    producer_identity = (
        producer["producer_id"],
        producer["contract"],
        producer["implementation_sha256"],
    )
    validator_identity = (
        validator["validator_id"],
        validator["contract"],
        validator["implementation_sha256"],
    )
    historical = expected_anchor["historical"]
    if (
        selected_trust["producer_bindings"].get(producer_identity) != validator_identity
        or not _lifecycle_allows(
            selected_trust["producer_lifecycles"].get(producer_identity),
            historical=historical,
        )
        or not _lifecycle_allows(
            selected_trust["validator_lifecycles"].get(validator_identity),
            historical=historical,
        )
    ):
        raise ClientError(
            "launcher witness is not authorized by the selected trust context",
            EXIT_LAUNCH,
        )
    return stdout


def _recheck_activation_lock(
    root: Path,
    root_descriptor: int,
    lock: int,
    expected: dict[str, object],
    expected_identity: FilesystemIdentity,
) -> None:
    metadata = os.fstat(lock)
    if (
        not _canonical_activation_lock_metadata(metadata)
        or _filesystem_identity(metadata) != expected_identity
    ):
        raise ValueError("activation lock descriptor is not canonical")
    _reject_macos_allow_acl(lock, "activation lock")
    if _activation_lock_binding(root, metadata) != expected:
        raise ValueError("activation lock changed during validation")
    visible_identity = _filesystem_identity(
        os.stat(
            "activation.lock",
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
    )
    if visible_identity != expected_identity:
        raise ValueError("activation lock mapping changed during validation")


def _receipt_activation_smoke_authority_projection(
    receipt: dict[str, Any],
    rollback_receipt: dict[str, Any],
) -> dict[str, Any]:
    authorization = receipt["authorization"]
    rollback = receipt["rollback"]
    artifacts = rollback_receipt.get("selector_preimage", [])
    if "control_preimage" in rollback_receipt:
        controls = rollback_receipt["control_preimage"]
        artifacts = [*controls[:-1], *artifacts, controls[-1]]
    return {
        "outer_maintenance_transaction_sha256": authorization[
            "maintenance_transaction_sha256"
        ],
        "stage": {
            "plan_sha256": authorization["plan_sha256"],
            "authorization_sha256": authorization["sha256"],
            "maintenance_transaction_sha256": authorization[
                "maintenance_transaction_sha256"
            ],
        },
        "rollback_authority": {
            "target_state": rollback["state"],
            "receipt_path": rollback["path"],
            "receipt_sha256": rollback["sha256"],
        },
        "preimage": {
            "manifest_path": rollback["path"],
            "manifest_sha256": rollback["sha256"],
            "artifacts": artifacts,
            "external_dependencies": rollback_receipt["external_dependencies"],
        },
    }


def _validate_under_lock(
    bundle: Path,
    historical_context_sha256: str | None,
    invocation: InvocationState,
    root: Path,
    root_descriptor: int,
    lock: int,
    activation_lock: dict[str, object],
    activation_lock_identity: FilesystemIdentity,
    root_chain: list[DirectoryRecord],
    installation_files: list[FileRecord],
    installation_inventories: list[InventoryRecord],
    activation_transaction: dict[str, Any] | None,
    activation_stage_authority: dict[str, Any] | None = None,
) -> bytes:
    bundle_snapshot = None
    try:
        bundle_snapshot = _BundleSnapshot.open(bundle)
        retained_authority = None
        if activation_transaction is None:
            installation_identity = root_chain[-1][1]
            if any(
                identity == installation_identity
                for _, identity, _, _, _ in bundle_snapshot.directories
            ):
                raise ValueError("caller bundle is inside the canonical installation")
        elif activation_transaction["phase"] == "rollback-smoke":
            candidate_binding = activation_transaction["candidate"][
                "deployment_receipt"
            ]
            candidate_sha256 = candidate_binding["sha256"]
            authority_directory = _open_directory_at(
                root_chain,
                root_descriptor,
                "receipts",
                "activation authority receipt directory",
                private=True,
                required_mode=0o700,
            )
            authority_raw, authority_metadata = _capture_file(
                installation_files,
                authority_directory,
                f"sha256-{candidate_sha256}.json",
                "activation authority deployment receipt",
                MAX_DOCUMENT_BYTES,
                private=True,
            )
            if (
                len(authority_raw) != candidate_binding["length"]
                or _sha(authority_raw) != candidate_sha256
                or authority_metadata.st_uid != candidate_binding["owner"]
                or stat.S_IMODE(authority_metadata.st_mode) != candidate_binding["mode"]
            ):
                raise ValueError("activation authority receipt binding mismatch")
            authority_receipt = _document(
                _json(authority_raw, "activation authority deployment receipt"),
                "activation authority deployment receipt",
            )
            retained_authority = _validate_retained_receipts(
                authority_receipt,
                authority_raw,
                root,
                root_descriptor,
                root_chain,
                installation_files,
                installation_inventories,
                activation_lock,
                lock,
                activation_lock_identity,
                initial_policy=(
                    activation_stage_authority["candidate_policy"]
                    if activation_stage_authority is not None
                    else None
                ),
            )
        (
            receipt,
            expected_anchor,
            launcher,
            trust,
            historical,
            selected_trust,
            receipt_sha256,
            current_unit,
            retained_authority,
        ) = _load_installation(
            bundle_snapshot.sha256,
            root,
            root_descriptor,
            root_chain,
            installation_files,
            installation_inventories,
            activation_lock,
            lock,
            activation_lock_identity,
            historical_context_sha256,
            invocation,
            retained_authority,
        )
        expected_activation_envelope = None
        if activation_transaction is not None:
            unit_name = (
                "candidate"
                if activation_transaction["phase"] == "candidate-smoke"
                else "prior"
            )
            handoff = activation_transaction["smoke_handoff"]
            stage = activation_transaction["stage"]
            rollback_authority = activation_transaction["rollback_authority"]
            preimage = activation_transaction["preimage"]
            authority_receipt = retained_authority["receipt"]
            authority_raw = retained_authority["receipt_raw"]
            authority_rollback = retained_authority["rollback"]
            candidate_unit = activation_transaction["candidate"]
            authority_active = _exact_dict(
                authority_receipt["active"],
                {
                    "record_path",
                    "record_sha256",
                    "generation",
                    "runtime_contract",
                    "runtime_implementation_sha256",
                    "public_release",
                },
                "activation authority active binding",
            )
            authority_anchor = candidate_unit["smoke"]["expected_anchor"]
            journal_authority_claim = {
                "outer_maintenance_transaction_sha256": activation_transaction[
                    "outer_maintenance_transaction_sha256"
                ],
                "stage": {
                    "plan_sha256": stage["plan_sha256"],
                    "authorization_sha256": stage["authorization_sha256"],
                    "maintenance_transaction_sha256": stage[
                        "maintenance_transaction_sha256"
                    ],
                },
                "rollback_authority": rollback_authority,
                "preimage": preimage,
            }
            # Stage-receipt identity and preimage inventory are exact controller
            # recovery evidence; smoke authority comes only from this receipt.
            if (
                not _same_json(
                    activation_transaction["activation_lock"],
                    activation_lock,
                )
                or not _same_json(activation_transaction[unit_name], current_unit)
                or handoff["target_deployment_receipt_sha256"] != receipt_sha256
                or historical
                or candidate_unit["deployment_receipt"]["sha256"] != _sha(authority_raw)
                or candidate_unit["deployment_receipt"]["length"] != len(authority_raw)
                or not _same_json(
                    candidate_unit["control_set"],
                    authority_receipt["control_set"],
                )
                or not _same_json(
                    candidate_unit["smoke"],
                    authority_receipt["smoke"],
                )
                or authority_active["record_path"] != str(root / "active.json")
                or authority_active["record_sha256"]
                != candidate_unit["active_record"]["sha256"]
                or authority_active["generation"] != authority_anchor["generation"]
                or authority_active["runtime_contract"]
                != authority_anchor["runtime_contract"]
                or authority_active["runtime_implementation_sha256"]
                != authority_anchor["runtime_implementation_sha256"]
                or not _same_json(
                    authority_active["public_release"],
                    authority_anchor["public_release"],
                )
            ):
                raise ValueError("activation smoke target binding mismatch")
            if not _same_json(
                journal_authority_claim,
                _receipt_activation_smoke_authority_projection(
                    authority_receipt,
                    authority_rollback,
                ),
            ):
                raise ValueError("activation smoke receipt authority mismatch")
            expected_activation_envelope = _validate_receipt_owned_smoke(
                receipt["smoke"],
                root,
                bundle_snapshot,
                trust,
                expected_anchor,
                selected_trust,
            )
            smoke = receipt["smoke"]
            if (
                handoff["smoke_bundle_sha256"] != smoke["bundle"]["sha256"]
                or handoff["smoke_trust_context_sha256"]
                != smoke["trust_context"]["sha256"]
            ):
                raise ValueError("activation smoke handoff binding mismatch")
        _recheck_files(installation_files)
        _recheck_inventories(installation_inventories)
        bundle_snapshot.recheck()
        _recheck_directory_chain(root_chain, "installation")
        _recheck_activation_lock(
            root,
            root_descriptor,
            lock,
            activation_lock,
            activation_lock_identity,
        )
        if activation_transaction is None:
            _require_activation_transaction_absent(root_descriptor)
        invocation.raise_if_cancelled()
        stdout = _launch(
            receipt,
            launcher,
            bundle,
            trust,
            historical,
            invocation,
        )
        invocation.raise_if_cancelled()
        _recheck_files(installation_files)
        _recheck_inventories(installation_inventories)
        bundle_snapshot.recheck()
        _recheck_directory_chain(root_chain, "installation")
        _recheck_activation_lock(
            root,
            root_descriptor,
            lock,
            activation_lock,
            activation_lock_identity,
        )
        if activation_transaction is None:
            _require_activation_transaction_absent(root_descriptor)
        if (
            expected_activation_envelope is not None
            and stdout != expected_activation_envelope
        ):
            raise ClientError(
                "launcher activation envelope mismatch",
                EXIT_LAUNCH,
            )
        accepted = _accept(stdout, expected_anchor, selected_trust)
        invocation.note_validated_envelope()
        invocation.raise_if_cancelled()
        return accepted
    finally:
        if bundle_snapshot is not None:
            bundle_snapshot.close()


def _validate(
    bundle: Path,
    historical_context_sha256: str | None,
    invocation: InvocationState,
) -> bytes:
    root = _installed_root()
    invocation.raise_if_cancelled()
    (
        lock,
        activation_lock,
        activation_lock_identity,
        root_chain,
    ) = _acquire_shared_lock(root, invocation)
    root_descriptor = root_chain[-1][0]
    installation_files: list[FileRecord] = []
    installation_inventories: list[InventoryRecord] = []
    try:
        _require_activation_transaction_absent(root_descriptor)
        return _validate_under_lock(
            bundle,
            historical_context_sha256,
            invocation,
            root,
            root_descriptor,
            lock,
            activation_lock,
            activation_lock_identity,
            root_chain,
            installation_files,
            installation_inventories,
            None,
        )
    finally:
        _close_descriptors([item[0] for item in installation_files])
        os.close(lock)
        _close_descriptors([item[0] for item in root_chain])


def _validate_activation_smoke(invocation: InvocationState) -> bytes:
    root = _installed_root()
    invocation.raise_if_cancelled()
    root_chain = _open_directory_chain(
        root,
        "canonical root",
        required_final_mode=0o700,
    )
    root_descriptor = root_chain[-1][0]
    installation_files: list[FileRecord] = []
    installation_inventories: list[InventoryRecord] = []
    lock = 3
    try:
        transaction_raw, transaction_metadata = _capture_file(
            installation_files,
            root_descriptor,
            "transaction.json",
            "activation transaction",
            MAX_DOCUMENT_BYTES,
            private=True,
        )
        if stat.S_IMODE(transaction_metadata.st_mode) != 0o600:
            raise ValueError("activation transaction mode is invalid")
        transaction = _validate_activation_transaction(transaction_raw, root)
        activation_lock = _exact_dict(
            transaction["activation_lock"],
            {"path", "device", "inode", "owner", "mode"},
            "activation transaction lock binding",
        )
        activation_lock_identity = _accept_inherited_activation_lock(
            root,
            root_descriptor,
            activation_lock,
        )
        stage_authority = _capture_control_maintenance_stage(
            transaction,
            root,
            root_chain,
            installation_files,
            installation_inventories,
        )
        invocation.raise_if_cancelled()
        return _validate_under_lock(
            root / "smoke" / "bundle",
            None,
            invocation,
            root,
            root_descriptor,
            lock,
            activation_lock,
            activation_lock_identity,
            root_chain,
            installation_files,
            installation_inventories,
            transaction,
            stage_authority,
        )
    finally:
        _close_descriptors([item[0] for item in installation_files])
        _close_quietly(lock)
        _close_descriptors([item[0] for item in root_chain])


def _next_action(error: ClientError, invocation: InvocationState) -> str:
    if invocation.accepted_output_may_be_visible:
        if str(error) == "accepted output transport failed":
            return (
                "discard any visible output; do not retry; repair the caller transport"
            )
        return (
            "discard any visible output; do not retry; "
            "verify validator termination and active state"
        )
    if error.exit_code == EXIT_INVOCATION:
        return "invoke the canonical shim with documented arguments"
    if str(error) == "activation lock is unavailable":
        return "do not retry; wait for the deployment operator"
    if str(error) == "accepted output transport failed":
        return "do not retry; repair the caller transport"
    if str(error) in {
        "client resources are unavailable",
        "launcher resources are unavailable",
        "validation output resources are unavailable",
    }:
        return "do not retry; restore process and descriptor capacity"
    if error.exit_code == EXIT_RESOURCE:
        return "do not retry; verify validator termination and active state"
    if error.exit_code == EXIT_LAUNCH:
        return "do not retry; inspect the bundle and validator evidence"
    return "do not retry; ask the deployment operator to inspect the installation"


def _diagnostic(error: ClientError, invocation: InvocationState) -> bytes:
    reason = " ".join(str(error).split()).replace("|", "/")
    fields = (
        f"validator_code_executed={invocation.validator_code_executed}",
        f"active_state_changed={invocation.active_state_changed}",
        f"current_receipt={invocation.current_receipt}",
        "candidate_receipt=none",
        "rollback=not-run",
        f"next_action={_next_action(error, invocation)}",
    )
    prefix = b"task witness client rejected: "
    suffix = (" | " + " | ".join(fields)).encode("ascii")
    reason_bytes = reason.encode("ascii", "replace")
    available = PROCESS_PROFILE["diagnostic_max_bytes"] - len(prefix) - len(suffix) - 1
    return prefix + reason_bytes[: max(available, 0)] + suffix + b"\n"


def _emit_diagnostic(error: ClientError, invocation: InvocationState) -> None:
    if invocation.diagnostic_state != "installed":
        return
    try:
        descriptor = sys.stderr.fileno()
        raw = _diagnostic(error, invocation)
        _write_terminal(
            descriptor,
            raw,
            None,
        )
    except BaseException:  # noqa: BLE001
        return


def _write_terminal_child(
    descriptor: int,
    raw: bytes,
    previous_mask: set[signal.Signals],
    gate: int,
    inherited_descriptors: tuple[int, ...],
) -> None:
    try:
        _start_child_deadline()
        for number in CANCELLATION_SIGNALS:
            signal.signal(number, signal.SIG_DFL)
        _close_inherited_descriptors(
            inherited_descriptors,
            (descriptor, gate),
        )
        if not _await_parent_gate(gate):
            os._exit(1)
        os.close(gate)
        _clear_child_deadline()
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        remaining = memoryview(raw)
        while remaining:
            try:
                written = os.write(descriptor, remaining)
            except InterruptedError:
                continue
            except BlockingIOError:
                time.sleep(0.01)
                continue
            except OSError:
                os._exit(1)
            if written <= 0:
                os._exit(1)
            remaining = remaining[written:]
    except BaseException:  # noqa: BLE001
        os._exit(1)
    os._exit(0)


def _terminal_writer_reap_is_safe() -> bool:
    number = getattr(signal, "SIGCHLD", None)
    if number is None:
        return False
    try:
        return _sole_cpython_thread() and signal.getsignal(number) == signal.SIG_DFL
    except (OSError, ValueError):
        return False


def _consume(
    pid: int,
    context: _CleanupContext,
) -> _ConsumedCleanup:
    """Kill and exactly reap a still-owned direct child."""
    if context.pid != pid:
        raise ClientError("cleanup child identity is unavailable", EXIT_RESOURCE)
    if not context.arm_cleanup(graceful=False):
        context.result.lifecycle = context.lifecycle.state
        context.result.error = context.error
        return context.result
    signal_attempted = False
    while _responsible_for_child(context):
        if _may_signal_child(context) and not signal_attempted:
            _signal_with_retry(
                os.kill,
                pid,
                signal.SIGKILL,
                deadline=context.cleanup_deadline,
                ownership=context,
                context=context,
                retry_slot=_RETRY_CONSUME_DIRECT_KILL,
                clock_retry_slot=_RETRY_CONSUME_DIRECT_KILL_CLOCK,
            )
            signal_attempted = True
        remaining = _remaining_with_retry(
            context.cleanup_deadline, context, _RETRY_CONSUME_CLOCK
        )
        if remaining is None or remaining <= 0:
            context.record(subprocess.TimeoutExpired(pid, 0))
            break
        wait_deadline = context.cleanup_deadline - remaining + min(0.05, remaining)
        _wait_terminal_writer(
            pid,
            wait_deadline,
            None,
            context,
        )
        status = context.wait_status
        writer_owned = context.wait_owned
        wait_error = context.wait_error
        if wait_error is not None:
            context.record(wait_error)
            if not _responsible_for_child(context) or not context.retry(
                _RETRY_CONSUME_EXACT_WAIT
            ):
                break
            continue
        if status is not None:
            context.result.completed = True
            break
        if not writer_owned or not _responsible_for_child(context):
            break
        remaining = _remaining_with_retry(
            context.cleanup_deadline, context, _RETRY_CONSUME_CLOCK
        )
        if remaining is None or remaining <= 0:
            context.record(subprocess.TimeoutExpired(pid, 0))
            break
    context.result.lifecycle = context.lifecycle.state
    context.result.error = context.error
    return context.result


def _wait_terminal_writer(
    pid: int,
    deadline: float,
    invocation: InvocationState | None,
    context: _CleanupContext,
) -> None:
    while True:
        if invocation is not None:
            invocation.raise_if_cancelled()
        try:
            waited, status = _waitpid_with_lifecycle(context, pid)
        except InterruptedError:
            try:
                expired = time.monotonic() >= deadline
            except BaseException as error:  # noqa: BLE001
                context.wait_status = None
                context.wait_owned = _responsible_for_child(context)
                context.wait_error = error
                return
            if expired:
                context.wait_status = None
                context.wait_owned = True
                context.wait_error = None
                return
            continue
        except MemoryError as error:
            context.wait_status = None
            context.wait_owned = _responsible_for_child(context)
            context.wait_error = error
            return
        except ChildProcessError as error:
            context.wait_status = None
            context.wait_owned = False
            context.wait_error = error
            return
        except OSError as error:
            context.wait_status = None
            context.wait_owned = error.errno != errno.ECHILD and _responsible_for_child(
                context
            )
            context.wait_error = error
            return
        except Exception as error:
            context.wait_status = None
            context.wait_owned = _responsible_for_child(context)
            context.wait_error = error
            return
        if waited == pid:
            context.wait_status = status
            context.wait_owned = False
            context.wait_error = None
            return
        try:
            remaining = deadline - time.monotonic()
        except BaseException as error:  # noqa: BLE001
            context.wait_status = None
            context.wait_owned = _responsible_for_child(context)
            context.wait_error = error
            return
        if remaining <= 0:
            context.wait_status = None
            context.wait_owned = True
            context.wait_error = None
            return
        try:
            time.sleep(min(0.01, remaining))
        except BaseException as error:  # noqa: BLE001
            context.wait_status = None
            context.wait_owned = _responsible_for_child(context)
            context.wait_error = error
            return


def _start_terminal_writer(
    descriptor: int,
    raw: bytes,
    invocation: InvocationState | None,
    context: _CleanupContext,
) -> bool:
    cancellation_signals = set(CANCELLATION_SIGNALS)
    gate_read = None
    gate_write = None
    pid = None
    fork_attempted = False
    fork_error = None
    restore_error = None
    gate_error_message = (
        "accepted output signal transition failed"
        if invocation is not None
        else "diagnostic output signal transition failed"
    )
    try:
        try:
            gate_read, gate_write = _cloexec_pipe()
            os.set_blocking(gate_read, False)
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                cancellation_signals,
            )
        except (AttributeError, OSError, ValueError) as error:
            if invocation is not None:
                raise ClientError(
                    gate_error_message,
                    EXIT_RESOURCE,
                ) from error
            return False
        try:
            if invocation is not None:
                invocation.raise_if_cancelled()
                try:
                    pending = signal.sigpending()
                except (AttributeError, OSError, ValueError) as error:
                    raise ClientError(
                        gate_error_message,
                        EXIT_RESOURCE,
                    ) from error
                if pending & cancellation_signals:
                    raise CLIENT_INTERRUPTED_ERROR
            try:
                _require_no_direct_children()
            except ClientError:
                if invocation is None:
                    return False
                raise
            try:
                inherited_descriptors = _proven_open_descriptor_inventory()
            except OSError as error:
                if invocation is None:
                    return False
                raise ClientError(
                    "accepted output descriptor inventory failed",
                    EXIT_RESOURCE,
                ) from error
            if not context.arm_fork_deadlines():
                if invocation is None:
                    return False
                raise ClientError(
                    "terminal writer recovery deadline is unavailable",
                    EXIT_RESOURCE,
                ) from context.error
            fork_attempted = True
            try:
                pid = _fork_and_publish(context)
            except BaseException as error:  # noqa: BLE001
                fork_error = error
                context.record(error)
        finally:
            if pid != 0:
                try:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                except BaseException as error:  # noqa: BLE001
                    restore_error = error

        if pid == 0:
            _close_quietly(gate_write)
            gate_write = None
            _write_terminal_child(
                descriptor,
                raw,
                previous_mask,
                gate_read,
                inherited_descriptors,
            )
            os._exit(1)

        _close_quietly(gate_read)
        gate_read = None
        if pid is None:
            _close_quietly(gate_write)
            gate_write = None
            if fork_attempted:
                try:
                    _wildcard_reap_sole_child(context)
                except BaseException:
                    if invocation is None:
                        return False
                    raise
            if restore_error is not None:
                if invocation is None:
                    return False
                if isinstance(restore_error, MemoryError):
                    raise restore_error
                raise ClientError(
                    gate_error_message,
                    EXIT_RESOURCE,
                ) from restore_error
            if fork_error is not None:
                if invocation is None:
                    return False
                if isinstance(fork_error, MemoryError):
                    raise fork_error
                raise ClientError(
                    "accepted output writer could not be started",
                    EXIT_RESOURCE,
                ) from fork_error
            if invocation is None:
                return False
            raise ClientError(
                "terminal writer child creation failed",
                EXIT_RESOURCE,
            )

        if restore_error is not None:
            if invocation is None:
                return False
            if isinstance(restore_error, MemoryError):
                raise restore_error
            raise ClientError(
                gate_error_message,
                EXIT_RESOURCE,
            ) from restore_error

        if invocation is not None:
            invocation.raise_if_cancelled()
            invocation.accepted_output_may_be_visible = True
        if not _arm_child_gate(
            gate_write,
            context.writer_deadline,
        ):
            if invocation is None:
                return False
            invocation.accepted_output_may_be_visible = False
            raise ClientError(
                "terminal writer gate failed",
                EXIT_RESOURCE,
            )
        return True
    finally:
        if pid != 0:
            _close_quietly(gate_read)
            _close_quietly(gate_write)


def _write_terminal(
    descriptor: int,
    raw: bytes,
    invocation: InvocationState | None,
) -> bool:
    if invocation is None:
        if not _instrumentation_is_clear():
            return False
    elif not _canonical_client_process(invocation):
        raise ClientError(
            "client process profile changed before terminal output",
            EXIT_INSTALLATION,
        )
    if not _terminal_writer_reap_is_safe():
        return False
    budget_seconds = PROCESS_PROFILE[
        (
            "accepted_output_deadline_seconds"
            if invocation is not None
            else "diagnostic_write_seconds"
        )
    ]
    try:
        context = _prepare_cleanup_context(writer_seconds=budget_seconds)
    except BaseException:
        if invocation is None:
            return False
        raise
    try:
        if not _start_terminal_writer(
            descriptor,
            raw,
            invocation,
            context,
        ):
            return False
        if context.pid is None:
            raise RuntimeError("terminal writer ownership is unavailable")
        try:
            _wait_terminal_writer(
                context.pid,
                context.writer_deadline,
                invocation,
                context,
            )
            status = context.wait_status
            wait_error = context.wait_error
            if wait_error is not None:
                raise ClientError(
                    "accepted output transport failed",
                    EXIT_RESOURCE,
                ) from wait_error
            if status is None:
                return False
            if invocation is not None:
                invocation.raise_if_cancelled()
            if context.error is not None:
                if invocation is not None:
                    raise ClientError(
                        "accepted output transport failed",
                        EXIT_RESOURCE,
                    ) from context.error
                return False
            return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        except ClientError:
            raise
        except Exception as error:
            if invocation is not None and context.wait_status == 0:
                raise ClientError(
                    "accepted output transport failed",
                    EXIT_RESOURCE,
                ) from error
            if invocation is None:
                return False
            raise
    finally:
        if _responsible_for_child(context) and context.pid is not None:
            cleanup = _consume(context.pid, context)
            if (
                not cleanup.completed or cleanup.error is not None
            ) and invocation is not None:
                cleanup_error = cleanup.error or RuntimeError(
                    f"terminal writer cleanup ended {cleanup.lifecycle}"
                )
                raise ClientError(
                    "accepted output writer cleanup failed",
                    EXIT_RESOURCE,
                ) from cleanup_error


def _emit_accepted_output(raw: bytes, invocation: InvocationState) -> None:
    try:
        descriptor = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError) as error:
        raise ClientError(
            "accepted output transport failed",
            EXIT_RESOURCE,
        ) from error
    if not _write_terminal(descriptor, raw, invocation):
        raise ClientError(
            "accepted output transport failed",
            EXIT_RESOURCE,
        )
    invocation.raise_if_cancelled()


def _parse_public_arguments(argv: list[str]) -> tuple[Path, str | None]:
    historical_context_sha256 = None
    if len(argv) == 3 and argv[0] == "validate" and argv[1] == "--bundle":
        pass
    elif (
        len(argv) == 6
        and argv[0] == "validate"
        and argv[1] == "--bundle"
        and argv[3] == "--historical"
        and argv[4] == "--trust-context-sha256"
        and _digest(argv[5])
    ):
        historical_context_sha256 = argv[5]
    else:
        raise ClientError("invalid public arguments", EXIT_INVOCATION)
    try:
        bundle = _absolute(argv[2], "bundle")
    except ValueError as error:
        raise ClientError(str(error), EXIT_INVOCATION) from error
    return bundle, historical_context_sha256


def main(argv: list[str] | None = None) -> int:
    try:
        invocation = InvocationState()
    except MemoryError:
        return EXIT_RESOURCE
    success_mask = None
    try:
        invocation.install_cancellation_handlers()
    except BaseException:  # noqa: BLE001
        return EXIT_RESOURCE
    try:
        if not _canonical_client_process(invocation):
            raise ClientError(
                "client requires the canonical process profile",
                EXIT_INSTALLATION,
            )
        invocation.raise_if_cancelled()
        arguments = list(sys.argv[1:] if argv is None else argv)
        if arguments == ["activation-smoke"]:
            accepted = _validate_activation_smoke(invocation)
        else:
            bundle, historical_context_sha256 = _parse_public_arguments(arguments)
            invocation.raise_if_cancelled()
            accepted = _validate(bundle, historical_context_sha256, invocation)
        invocation.raise_if_cancelled()
        _emit_accepted_output(accepted, invocation)
        success_mask = invocation.prepare_success()
    except KeyboardInterrupt:
        _emit_diagnostic(CLIENT_INTERRUPTED_ERROR, invocation)
        return CLIENT_INTERRUPTED_ERROR.exit_code
    except ClientError as error:
        _emit_diagnostic(error, invocation)
        return error.exit_code
    except MemoryError:
        _emit_diagnostic(CLIENT_RESOURCE_ERROR, invocation)
        return EXIT_RESOURCE
    except BaseException as error:  # noqa: BLE001
        normalized_error = (
            CLIENT_RESOURCE_ERROR
            if isinstance(error, OSError) and error.errno in RESOURCE_ERRNOS
            else CLIENT_INSTALLATION_ERROR
        )
        _emit_diagnostic(normalized_error, invocation)
        return normalized_error.exit_code
    try:
        invocation.finish_success(success_mask)
    except ClientError as error:
        _emit_diagnostic(error, invocation)
        return error.exit_code
    except MemoryError:
        _emit_diagnostic(CLIENT_RESOURCE_ERROR, invocation)
        return EXIT_RESOURCE
    except Exception as error:
        normalized_error = (
            CLIENT_RESOURCE_ERROR
            if isinstance(error, OSError) and error.errno in RESOURCE_ERRNOS
            else CLIENT_INSTALLATION_ERROR
        )
        _emit_diagnostic(normalized_error, invocation)
        return normalized_error.exit_code
    return 0


def entrypoint_main() -> int:
    return EXIT_INSTALLATION


def _main_no_return() -> NoReturn:
    os._exit(entrypoint_main())


if __name__ == "__main__":
    _main_no_return()
