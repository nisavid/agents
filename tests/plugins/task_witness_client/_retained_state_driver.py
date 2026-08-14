"""Physical retained-state client scenarios."""

import importlib.util
import os
import sys
from pathlib import Path

CONFIG = globals().get("CONFIG", {})
specification = importlib.util.spec_from_file_location(
    "task_witness_client_retained_state_fixture", sys.argv[1]
)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
root = Path(sys.argv[2])
module._installed_root = lambda: root
scenario = CONFIG["scenario"]

if scenario == "open-swap":
    target = Path(CONFIG["target"])
    replacement = Path(CONFIG["replacement"])
    marker = Path(CONFIG["marker"])
    swapped = False

    def audit(event, arguments):
        global swapped
        if event != "open" or swapped:
            return
        path = os.fspath(arguments[0])
        if path not in {str(target), target.name}:
            return
        os.replace(replacement, target)
        marker.write_text("swapped", encoding="utf-8")
        swapped = True

    sys.addaudithook(audit)
elif scenario == "launcher-aba":
    launcher = Path(CONFIG["launcher"])
    replacement = Path(CONFIG["replacement"])
    backup = Path(CONFIG["backup"])
    mode = CONFIG["mode"]
    original_launch = module._launch

    def launch(*arguments, **keywords):
        if mode == "rename-restore":
            os.replace(launcher, backup)
            os.replace(replacement, launcher)
        else:
            launcher.chmod(0o700)
            launcher.write_bytes(replacement.read_bytes())
            launcher.chmod(0o500)
        return original_launch(*arguments, **keywords)

    module._launch = launch
elif scenario == "visible-lock-identity-drift":
    marker = Path(CONFIG["marker"])
    original_launch = module._launch
    original_stat = module.os.stat
    launch_returned = False
    injected = False

    def launch(*arguments, **keywords):
        global launch_returned
        output = original_launch(*arguments, **keywords)
        launch_returned = True
        return output

    def drift_visible_lock_identity(path, *arguments, **keywords):
        global injected
        metadata = original_stat(path, *arguments, **keywords)
        if (
            launch_returned
            and not injected
            and path == "activation.lock"
            and keywords.get("dir_fd") is not None
            and keywords.get("follow_symlinks") is False
        ):
            values = list(metadata)
            values[6] = 0
            values[8] = metadata.st_mtime + 1
            values[9] = metadata.st_ctime + 1
            marker.write_text("injected", encoding="utf-8")
            injected = True
            return os.stat_result(values)
        return metadata

    module._launch = launch
    module.os.stat = drift_visible_lock_identity
elif scenario == "activation-lock-open-audit":
    marker = Path(CONFIG["marker"])
    original_open = module.os.open

    def audit_activation_lock_open(path, *arguments, **keywords):
        if path == "activation.lock":
            marker.write_text("opened", encoding="utf-8")
        return original_open(path, *arguments, **keywords)

    module.os.open = audit_activation_lock_open
elif scenario == "interpreter-sibling-churn":
    real_executable = sys.executable
    pinned_executable = CONFIG["pinned_executable"]
    interpreter_directory = Path(CONFIG["interpreter_directory"])
    churned = Path(CONFIG["churned"])
    module.sys.executable = pinned_executable
    original_execve = module.os.execve

    def substitute_execve(executable, command, environment):
        command = list(command)
        if command and command[0] == pinned_executable:
            command[0] = real_executable
            executable = real_executable
        return original_execve(executable, command, environment)

    module.os.execve = substitute_execve
    original_stat = module.os.stat
    did_churn = False

    def churn_during_stat(path, *arguments, **keywords):
        global did_churn
        metadata = original_stat(path, *arguments, **keywords)
        if not did_churn and path == interpreter_directory.name:
            did_churn = True
            sibling = interpreter_directory / "unrelated"
            sibling.write_text("temporary", encoding="utf-8")
            sibling.unlink()
            os.utime(
                interpreter_directory,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1000000),
            )
            churned.touch()
        return metadata

    module.os.stat = churn_during_stat
else:
    raise RuntimeError(f"unsupported retained-state scenario: {scenario!r}")

status = module.main(sys.argv[int(CONFIG.get("main_argv_start", 3)) :])
if scenario == "open-swap" and not swapped:
    raise SystemExit(99)
if scenario == "visible-lock-identity-drift" and not injected:
    raise SystemExit(99)
raise SystemExit(status)
