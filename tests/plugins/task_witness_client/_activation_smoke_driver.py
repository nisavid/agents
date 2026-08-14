"""Controller-shaped inherited-lock driver for activation-smoke client tests."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
from pathlib import Path

source = Path(sys.argv[1])
installation = Path(sys.argv[2])
lock_path = installation / "activation.lock"
main_arguments = sys.argv[3:]
no_lock = main_arguments[:1] == ["--no-lock"]
if no_lock:
    main_arguments = main_arguments[1:]
separate_owner = main_arguments[:1] == ["--separate-owner"]
if separate_owner:
    main_arguments = main_arguments[1:]

if not no_lock:
    lock = os.open(
        lock_path,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    fcntl.flock(lock, fcntl.LOCK_EX)
    if separate_owner:
        owner = lock
        if owner == 3:
            owner = os.dup(owner)
            os.set_inheritable(owner, False)
            os.close(3)
        inherited = os.open(
            lock_path,
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        if inherited != 3:
            os.dup2(inherited, 3, inheritable=True)
            os.close(inherited)
        else:
            os.set_inheritable(3, True)
    elif lock != 3:
        os.dup2(lock, 3, inheritable=True)
        os.close(lock)
    else:
        os.set_inheritable(3, True)

specification = importlib.util.spec_from_file_location(
    "task_witness_client_activation_smoke_fixture",
    source,
)
if specification is None or specification.loader is None:
    raise RuntimeError("could not load task witness client")
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
module._installed_root = lambda: installation

visible_identity_marker = None
visible_identity_injected = False
if main_arguments[:1] == ["--visible-lock-identity-drift"]:
    visible_identity_marker = Path(main_arguments[1])
    main_arguments = main_arguments[2:]
    original_launch = module._launch
    original_stat = module.os.stat
    launch_returned = False

    def launch(*arguments, **keywords):
        global launch_returned
        output = original_launch(*arguments, **keywords)
        launch_returned = True
        return output

    def drift_visible_lock_identity(path, *arguments, **keywords):
        global visible_identity_injected
        metadata = original_stat(path, *arguments, **keywords)
        if (
            launch_returned
            and not visible_identity_injected
            and path == "activation.lock"
            and keywords.get("dir_fd") is not None
            and keywords.get("follow_symlinks") is False
        ):
            values = list(metadata)
            values[6] = 0
            values[8] = metadata.st_mtime + 1
            values[9] = metadata.st_ctime + 1
            visible_identity_marker.write_text("injected", encoding="utf-8")
            visible_identity_injected = True
            return os.stat_result(values)
        return metadata

    module._launch = launch
    module.os.stat = drift_visible_lock_identity

audit_marker = None
audit = {
    "probe_generations": [],
    "fd3_cloexec_restored": False,
}
if main_arguments[:1] == ["--audit"]:
    audit_marker = Path(main_arguments[1])
    main_arguments = main_arguments[2:]
    original_open = module.os.open
    original_flock = module.fcntl.flock
    original_set_inheritable = module.os.set_inheritable
    open_state = {"generation": 0}
    descriptor_generations = {}

    def audited_open(path, flags, *arguments, **keywords):
        descriptor = original_open(path, flags, *arguments, **keywords)
        if path == "activation.lock":
            open_state["generation"] += 1
            descriptor_generations[descriptor] = open_state["generation"]
        return descriptor

    def audited_flock(descriptor, operation):
        if operation == fcntl.LOCK_SH | fcntl.LOCK_NB:
            audit["probe_generations"].append(
                descriptor_generations.get(descriptor)
            )
        return original_flock(descriptor, operation)

    def audited_set_inheritable(descriptor, inheritable):
        if descriptor == 3 and inheritable is False:
            audit["fd3_cloexec_restored"] = True
        return original_set_inheritable(descriptor, inheritable)

    module.os.open = audited_open
    module.fcntl.flock = audited_flock
    module.os.set_inheritable = audited_set_inheritable

status = module.main(main_arguments)
if visible_identity_marker is not None and not visible_identity_injected:
    raise SystemExit(99)
if audit_marker is not None:
    audit_marker.write_text(
        json.dumps(audit, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
raise SystemExit(status)
