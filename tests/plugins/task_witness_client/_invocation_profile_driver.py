"""Physical invocation-profile client scenarios."""

import _thread
import importlib.util
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

CONFIG = globals().get("CONFIG", {})
scenario = CONFIG["scenario"]
if scenario == "fifo-watcher":
    fifo = Path(CONFIG["fifo"])
    ready = Path(CONFIG["ready"])
    opened = Path(CONFIG["opened"])
    ready.touch()
    descriptor = os.open(fifo, os.O_WRONLY)
    os.close(descriptor)
    opened.touch()
    raise SystemExit(0)

specification = importlib.util.spec_from_file_location(
    "task_witness_client_invocation_profile_fixture", sys.argv[1]
)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
if scenario != "passwd-root":
    module._installed_root = lambda: Path(sys.argv[2])

if scenario == "resource-limit":
    import resource

    original_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, original_limit[1]))
elif scenario == "shared-lock-observation":
    attempted = Path(CONFIG["attempted"])
    original_flock = module.fcntl.flock

    def observed_flock(descriptor, operation):
        if operation == module.fcntl.LOCK_SH | module.fcntl.LOCK_NB:
            attempted.touch()
        return original_flock(descriptor, operation)

    module.fcntl.flock = observed_flock
elif scenario == "terminal-lock-drift":
    root = Path(sys.argv[2])
    replacement = Path(CONFIG["replacement"])
    original_flock = module.fcntl.flock
    did_replace = False

    def terminal_drift_flock(descriptor, operation):
        global did_replace
        if not did_replace and operation == (
            module.fcntl.LOCK_SH | module.fcntl.LOCK_NB
        ):
            os.replace(replacement, root / "activation.lock")
            did_replace = True
        return original_flock(descriptor, operation)

    moments = iter((100.0, 102.0))
    module.fcntl.flock = terminal_drift_flock
    module.time.monotonic = lambda: next(moments, 102.0)
elif scenario == "changing-descriptor-inventory":
    target = CONFIG["target"]
    target_forked = Path(CONFIG["target_forked"])
    original_inventory = module._open_descriptor_inventory_once
    original_fork = module.os.fork
    original_spawn = module._spawn_launcher
    original_start = module._start_terminal_writer
    inventory_calls = 0
    creating_launcher = False
    creating_writer = False

    def changing_inventory():
        global inventory_calls
        inventory_calls += 1
        snapshot = original_inventory()
        changing_call = 2 if target == "launcher" else 4
        return snapshot + ((999999,) if inventory_calls == changing_call else ())

    def observed_fork():
        if (target == "launcher" and creating_launcher) or creating_writer:
            target_forked.touch()
        return original_fork()

    def observed_spawn(*arguments, **keywords):
        global creating_launcher
        creating_launcher = True
        try:
            return original_spawn(*arguments, **keywords)
        finally:
            creating_launcher = False

    def observed_start(*arguments, **keywords):
        global creating_writer
        invocation = arguments[2]
        creating_writer = invocation is not None
        try:
            return original_start(*arguments, **keywords)
        finally:
            creating_writer = False

    module._open_descriptor_inventory_once = changing_inventory
    module._spawn_launcher = observed_spawn
    module._start_terminal_writer = observed_start
    module.os.fork = observed_fork
elif scenario == "persistent-recovery-deadline":
    forked = Path(CONFIG["forked"])
    original_inventory = module._proven_open_descriptor_inventory

    def fail_deadline_clock():
        raise MemoryError("fixture persistent recovery deadline allocation")

    def arm_deadline_failure():
        inventory = original_inventory()
        module.time.monotonic = fail_deadline_clock
        return inventory

    def unexpected_fork():
        forked.touch()
        raise AssertionError("launcher fork after deadline failure")

    module._proven_open_descriptor_inventory = arm_deadline_failure
    module._terminal_writer_reap_is_safe = lambda: False
    module.os.fork = unexpected_fork
elif scenario == "context-preparation-failure":
    forked = Path(CONFIG["forked"])

    def fail_context_preparation(*_arguments, **_keywords):
        raise MemoryError("fixture context preparation allocation")

    def unexpected_fork():
        forked.touch()
        raise AssertionError("launcher fork after context preparation failure")

    module._prepare_cleanup_context = fail_context_preparation
    module._terminal_writer_reap_is_safe = lambda: False
    module.os.fork = unexpected_fork
elif scenario == "pid-publication":
    published = Path(CONFIG["published"])
    original_prepare = module._prepare_cleanup_context
    original_fork = module.os.fork
    original_sigmask = module.signal.pthread_sigmask
    context = None
    fork_returned = False

    def capture_context(*arguments, **keywords):
        global context
        context = original_prepare(*arguments, **keywords)
        return context

    def observed_fork():
        global fork_returned
        pid = original_fork()
        if pid > 0:
            fork_returned = True
        return pid

    def observe_first_parent_step(*arguments, **keywords):
        global fork_returned
        if fork_returned:
            if (
                context is None
                or context.pid is None
                or context.lifecycle.state != "owned"
            ):
                raise AssertionError(
                    "launcher PID was not published before parent restoration"
                )
            published.touch()
            fork_returned = False
        return original_sigmask(*arguments, **keywords)

    module._prepare_cleanup_context = capture_context
    module.os.fork = observed_fork
    module.signal.pthread_sigmask = observe_first_parent_step
    module._terminal_writer_reap_is_safe = lambda: False
elif scenario == "pid-publication-failure":
    phase = CONFIG["phase"]
    child_pid_marker = Path(CONFIG["child_pid_marker"])
    cleanup_elapsed_marker = Path(CONFIG["cleanup_elapsed_marker"])
    no_waitable_marker = Path(CONFIG["no_waitable_marker"])
    numeric_signal_marker = Path(CONFIG["numeric_signal_marker"])
    original_publish = module._CleanupContext.publish_pid
    original_waitpid = module.os.waitpid
    original_wildcard = module._wildcard_reap_sole_child
    original_kill = module.os.kill
    original_killpg = module.os.killpg
    publication_error = MemoryError(f"fixture {phase} launcher PID publication")
    child_pid = None
    wait_faulted = False
    publication_error_propagated = False

    def fail_pid_publication(context, pid):
        global child_pid
        child_pid = pid
        child_pid_marker.write_text(str(pid), encoding="utf-8")
        if phase == "after-pid-store":
            context.pid = pid
        elif phase != "before-mutation":
            raise AssertionError(f"unknown publication failure phase: {phase}")
        raise publication_error

    def fail_first_wildcard_wait(pid, options):
        global wait_faulted
        if pid == -1 and not wait_faulted:
            wait_faulted = True
            raise MemoryError("fixture launcher wildcard wait")
        return original_waitpid(pid, options)

    def observed_wildcard(context):
        global publication_error_propagated
        if context.error is not publication_error:
            raise AssertionError("launcher publication failure was not recorded first")
        if context.pid is not None or context.lifecycle.state != "lost":
            raise AssertionError(
                "launcher publication failure retained signal authority"
            )
        if signal.pthread_sigmask(signal.SIG_BLOCK, set()):
            raise AssertionError("launcher publication cleanup ran before mask restore")
        started = time.monotonic()
        try:
            return original_wildcard(context)
        except module.ClientError as error:
            publication_error_propagated = error.__cause__ is publication_error
            raise
        finally:
            cleanup_elapsed_marker.write_text(
                str(time.monotonic() - started),
                encoding="utf-8",
            )

    def reject_published_pid_signal(operation, pid, number):
        if child_pid is not None and pid == child_pid:
            numeric_signal_marker.touch()
            raise AssertionError("untrusted launcher PID was signaled")
        return operation(pid, number)

    def observed_kill(pid, number):
        return reject_published_pid_signal(original_kill, pid, number)

    def observed_killpg(pid, number):
        return reject_published_pid_signal(original_killpg, pid, number)

    module._CleanupContext.publish_pid = fail_pid_publication
    module.os.waitpid = fail_first_wildcard_wait
    module._wildcard_reap_sole_child = observed_wildcard
    module.os.kill = observed_kill
    module.os.killpg = observed_killpg
    module._terminal_writer_reap_is_safe = lambda: False
elif scenario == "unknown-launcher-pid":
    child_pid_marker = Path(CONFIG["child_pid_marker"])
    original_fork = module.os.fork
    original_waitpid = module.os.waitpid
    original_wildcard = module._wildcard_reap_sole_child
    original_inventory = module._proven_open_descriptor_inventory
    original_prepare = module._prepare_cleanup_context
    original_monotonic = module.time.monotonic
    unknown_child_pid = None
    fork_failed = False
    deadline_faulted = False
    wildcard_wait_failed = False
    wildcard_error_propagated = False
    cleanup_context = None

    def fail_one_deadline_clock():
        global deadline_faulted
        if not deadline_faulted:
            deadline_faulted = True
            raise MemoryError("fixture recovery deadline allocation")
        return original_monotonic()

    def arm_deadline_fault():
        inventory = original_inventory()
        module.time.monotonic = fail_one_deadline_clock
        return inventory

    def capture_unknown_context(*arguments, **keywords):
        global cleanup_context
        cleanup_context = original_prepare(*arguments, **keywords)
        return cleanup_context

    def fork_then_forget_pid():
        global fork_failed, unknown_child_pid
        pid = original_fork()
        if pid == 0 or fork_failed:
            return pid
        fork_failed = True
        unknown_child_pid = pid
        child_pid_marker.write_text(str(pid), encoding="utf-8")
        raise MemoryError("fixture lost launcher pid")

    def fail_first_wildcard_wait(pid, options):
        global wildcard_wait_failed
        if pid == -1 and not wildcard_wait_failed:
            wildcard_wait_failed = True
            raise MemoryError("fixture wildcard launcher wait")
        return original_waitpid(pid, options)

    def observed_wildcard(context):
        global wildcard_error_propagated
        try:
            if context is not cleanup_context:
                raise AssertionError(
                    "launcher wildcard cleanup lost its pre-fork context"
                )
            return original_wildcard(context)
        except module.ClientError as error:
            wildcard_error_propagated = isinstance(error.__cause__, MemoryError)
            raise

    def reject_numeric_signal(*_arguments, **_keywords):
        raise AssertionError("unknown launcher cleanup used a numeric signal")

    module.os.fork = fork_then_forget_pid
    module.os.waitpid = fail_first_wildcard_wait
    module._wildcard_reap_sole_child = observed_wildcard
    module._proven_open_descriptor_inventory = arm_deadline_fault
    module._prepare_cleanup_context = capture_unknown_context
    module.os.kill = reject_numeric_signal
    module.os.killpg = reject_numeric_signal
elif scenario == "gate-order":
    target = CONFIG["target"]
    child_action = Path(CONFIG["child_action"])
    observed_order = Path(CONFIG["observed_order"])
    parent_pid = os.getpid()
    order = []
    target_seen = False
    mask_restored = False
    cancellation_checked = False
    gate_armed = False
    observing_writer = False
    if target == "writer":
        original_start = module._start_terminal_writer

        def observed_start(*arguments, **keywords):
            global observing_writer
            observing_writer = True
            try:
                return original_start(*arguments, **keywords)
            finally:
                observing_writer = False

        module._start_terminal_writer = observed_start
    inherited_fork = module.os.fork
    original_mask = module.signal.pthread_sigmask
    original_cancel = module.InvocationState.raise_if_cancelled
    original_arm = module._arm_child_gate
    original_write = module.os.write

    def assert_held(stage):
        if child_action.exists():
            raise RuntimeError(f"{target} child acted before {stage}")

    def ordered_fork():
        global target_seen
        pid = inherited_fork()
        target_context = (target == "launcher" and not target_seen) or (
            target == "writer" and observing_writer
        )
        if os.getpid() == parent_pid and pid > 0 and target_context:
            assert_held("pid storage")
            target_seen = True
            order.append("pid")
        return pid

    def ordered_mask(how, signals):
        global mask_restored
        previous = original_mask(how, signals)
        if target_seen and not mask_restored and how == module.signal.SIG_SETMASK:
            assert_held("mask restoration")
            mask_restored = True
            order.append("mask")
        return previous

    def ordered_cancel(invocation):
        global cancellation_checked
        result = original_cancel(invocation)
        if target_seen and mask_restored and not cancellation_checked:
            assert_held("cancellation check")
            cancellation_checked = True
            order.append("cancellation")
        return result

    def ordered_arm(descriptor, deadline):
        global gate_armed
        if target_seen and cancellation_checked and not gate_armed:
            assert_held("gate arm")
            time.sleep(0.05)
            assert_held("gate release")
            gate_armed = True
            order.append("gate")
            observed_order.write_text(",".join(order), encoding="utf-8")
        return original_arm(descriptor, deadline)

    def observed_write(descriptor, raw):
        if target == "writer" and os.getpid() != parent_pid and descriptor == 1:
            child_action.touch()
        return original_write(descriptor, raw)

    module.os.fork = ordered_fork
    module.os.write = observed_write
    module.signal.pthread_sigmask = ordered_mask
    module.InvocationState.raise_if_cancelled = ordered_cancel
    module._arm_child_gate = ordered_arm
elif scenario == "unregistered-thread":
    forked = Path(CONFIG["forked"])
    installation_access = Path(CONFIG["installation_access"])
    thread_ready = False

    def installation_root():
        installation_access.write_text("accessed", encoding="utf-8")
        raise AssertionError("installation accessed")

    module._installed_root = installation_root

    def worker():
        global thread_ready
        thread_ready = True
        while True:
            time.sleep(1)

    _thread.start_new_thread(worker, ())
    deadline = time.monotonic() + 1
    while not thread_ready or len(sys._current_frames()) != 2:
        if time.monotonic() >= deadline:
            raise RuntimeError("low-level thread did not start")
        time.sleep(0.001)
    if threading.active_count() != 1:
        raise RuntimeError("fixture requires active_count to omit thread")

    def unexpected_fork():
        forked.touch()
        raise AssertionError("threaded client forked")

    module.os.fork = unexpected_fork
elif scenario == "keyboard-interrupt":
    target = CONFIG["target"]

    class InterruptingClientError(ValueError):
        def __init__(self, message, exit_code):
            if message == "validation interrupted":
                raise MemoryError("interrupted error allocation")
            super().__init__(message)

    module.ClientError = InterruptingClientError

    def interrupt(*_arguments, **_keywords):
        raise KeyboardInterrupt

    setattr(module, target, interrupt)
elif scenario == "preflight-cancellation":

    def signal_during_preflight(*_arguments, **_keywords):
        os.kill(os.getpid(), getattr(signal, CONFIG["signal_name"]))
        return True

    module._canonical_client_process = signal_during_preflight
elif scenario == "passwd-root":
    module.os.geteuid = lambda: 123
    module.pwd.getpwuid = lambda uid: SimpleNamespace(
        pw_dir="/passwd/home" if uid == 123 else "/wrong"
    )
    print(module._installed_root())
    raise SystemExit(0)
elif scenario == "composed-client":
    root = Path(sys.argv[2])
    account_home = root.parents[2]
    launcher_driver = str(CONFIG["launcher_driver"])
    mutation_audit_fd = None
    if "filesystem_mutation_audit" in CONFIG:
        mutation_audit_path = Path(CONFIG["filesystem_mutation_audit"])
        mutation_probe_path = root / ".test-filesystem-mutation-audit-probe"
        mutation_audit_fd = os.open(
            mutation_audit_path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        os.set_inheritable(mutation_audit_fd, False)

        def audit_filesystem_mutation(event, arguments):
            mutating = event in {
                "os.chmod",
                "os.chown",
                "os.link",
                "os.mkdir",
                "os.remove",
                "os.rename",
                "os.rmdir",
                "os.symlink",
                "os.truncate",
                "os.utime",
            }
            if event == "open" and len(arguments) == 3:
                flags = arguments[2]
                mutating = isinstance(flags, int) and bool(
                    flags
                    & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
                )
            if mutating:
                record = f"{event}\t{arguments!r}\n".encode(
                    "utf-8",
                    "backslashreplace",
                )
                os.write(mutation_audit_fd, record)

        sys.addaudithook(audit_filesystem_mutation)
        mutation_probe_fd = os.open(
            mutation_probe_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(mutation_probe_fd)
        os.unlink(mutation_probe_path)
    module.pwd.getpwuid = lambda _uid: SimpleNamespace(pw_dir=str(account_home))
    original_execve = module.os.execve

    def composed_execve(executable, command, environment):
        command = list(command)
        launcher = str(root / "launcher" / "task_witness_launch.py")
        if launcher in command:
            launcher_index = command.index(launcher)
            command[launcher_index : launcher_index + 1] = [
                launcher_driver,
                launcher,
                "account-home",
                str(account_home),
            ]
        return original_execve(executable, command, environment)

    module.os.execve = composed_execve
else:
    raise RuntimeError(f"unsupported invocation-profile scenario: {scenario!r}")

status = module.main(sys.argv[int(CONFIG.get("main_argv_start", 3)) :])
if scenario == "composed-client" and mutation_audit_fd is not None:
    os.close(mutation_audit_fd)
if scenario == "resource-limit":
    resource.setrlimit(resource.RLIMIT_NOFILE, original_limit)
if scenario == "unknown-launcher-pid":
    if not fork_failed or unknown_child_pid is None:
        raise RuntimeError("unknown-pid fork failure was not exercised")
    if not deadline_faulted:
        raise RuntimeError("recovery deadline fault was not exercised")
    if not wildcard_error_propagated:
        raise RuntimeError("wildcard cleanup failure was not propagated")
    try:
        original_waitpid(unknown_child_pid, module.os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise RuntimeError("unknown launcher child remained waitable")
if scenario == "pid-publication-failure":
    if original_publish is module._CleanupContext.publish_pid:
        raise RuntimeError("launcher PID publication failure was not installed")
    if child_pid is None or not wait_faulted:
        raise RuntimeError("launcher PID publication cleanup was not exercised")
    if not publication_error_propagated:
        raise RuntimeError("launcher PID publication error was not retained")
    try:
        original_waitpid(child_pid, module.os.WNOHANG)
    except ChildProcessError:
        no_waitable_marker.touch()
    else:
        raise RuntimeError("launcher child remained waitable")
raise SystemExit(status)
