"""Physical process-supervision client scenarios."""

import errno
import importlib.util
import os
import resource
import signal
import sys
import time
from pathlib import Path


CONFIG = globals().get("CONFIG", {})
scenario = CONFIG["scenario"]
if scenario == "same-session-descendant":
    marker = Path(CONFIG["marker"])
    if os.fork() == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        marker.write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(30)
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while not marker.exists():
        time.sleep(0.01)
    time.sleep(30)
    raise AssertionError("same-session descendant fixture returned")
if scenario == "threaded-same-session-descendant":
    marker = Path(CONFIG["marker"])
    if os.fork() == 0:
        import ctypes
        import threading

        def sleep_in_worker() -> None:
            marker.write_text(
                f"{os.getpid()} {threading.get_native_id()}",
                encoding="ascii",
            )
            time.sleep(30)

        worker = threading.Thread(target=sleep_in_worker)
        worker.start()
        while not marker.exists():
            time.sleep(0.01)
        pthread_exit = ctypes.CDLL(None).pthread_exit
        pthread_exit.argtypes = (ctypes.c_void_p,)
        pthread_exit.restype = None
        pthread_exit(None)
        raise AssertionError("threaded descendant main thread returned")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while not marker.exists():
        time.sleep(0.01)
    time.sleep(30)
    raise AssertionError("threaded same-session descendant fixture returned")

specification = importlib.util.spec_from_file_location(
    "task_witness_client_process_supervision_fixture", sys.argv[1]
)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
module._installed_root = lambda: Path(sys.argv[2])
writer_pid = None
observing_writer = False
if CONFIG.get("observe_writer"):
    pid_marker = Path(CONFIG["pid_marker"])
    reaped_marker = Path(CONFIG["reaped_marker"])
    original_start = module._start_terminal_writer
    original_fork = module.os.fork
    original_waitpid = module.os.waitpid

    def observed_start(*arguments, **keywords):
        global observing_writer
        observing_writer = True
        try:
            return original_start(*arguments, **keywords)
        finally:
            observing_writer = False

    def observed_fork():
        global writer_pid
        pid = original_fork()
        if observing_writer and pid > 0 and writer_pid is None:
            writer_pid = pid
            pid_marker.write_text(str(pid), encoding="utf-8")
        return pid

    def observed_waitpid(pid, options):
        result = original_waitpid(pid, options)
        if writer_pid is not None and result[0] == writer_pid:
            reaped_marker.write_text("reaped", encoding="utf-8")
        return result

    module._start_terminal_writer = observed_start
    module.os.fork = observed_fork
    module.os.waitpid = observed_waitpid

if scenario == "spawn-time":
    module.PROCESS_PROFILE["validation_deadline_seconds"] = 0.05
    original_fork = module.os.fork

    def delayed_fork():
        time.sleep(float(CONFIG["delay_seconds"]))
        return original_fork()

    module.os.fork = delayed_fork
elif scenario == "spawn-failure":
    count = Path(CONFIG["count"])
    original_fork = module.os.fork

    def failed_fork():
        previous = int(count.read_text()) if count.exists() else 0
        if previous == 0:
            count.write_text("1", encoding="utf-8")
            raise OSError(errno.ENOENT, "fixture spawn failure")
        return original_fork()

    module.os.fork = failed_fork
elif scenario == "post-fork-profile-error":
    target = CONFIG["target"]
    parent_pid = os.getpid()
    profile_raise_count = 0
    original_spawn_launcher = module._spawn_launcher
    original_start_writer = module._start_terminal_writer

    def fail_after_fork(_frame, event, argument):
        global profile_raise_count
        if (
            event == "c_return"
            and argument is module.os.fork
            and os.getpid() == parent_pid
        ):
            profile_raise_count += 1
            sys.setprofile(None)
            raise OSError(errno.EIO, "fixture post-fork profile failure")

    if target == "launcher":

        def profiled_spawn_launcher(*arguments, **keywords):
            sys.setprofile(fail_after_fork)
            try:
                return original_spawn_launcher(*arguments, **keywords)
            finally:
                sys.setprofile(None)

        module._spawn_launcher = profiled_spawn_launcher
    elif target == "writer":

        def profiled_start_writer(*arguments, **keywords):
            if profile_raise_count:
                return original_start_writer(*arguments, **keywords)
            sys.setprofile(fail_after_fork)
            try:
                return original_start_writer(*arguments, **keywords)
            finally:
                sys.setprofile(None)

        module._start_terminal_writer = profiled_start_writer
    else:
        raise RuntimeError(f"unsupported post-fork profile target: {target!r}")
elif scenario == "resource-fork":

    def failed_fork():
        raise OSError(int(sys.argv[3]), "sensitive resource failure")

    module.os.fork = failed_fork
elif scenario == "bounded-process-profile":
    module.PROCESS_PROFILE["validation_deadline_seconds"] = 1
    module.PROCESS_PROFILE["termination_grace_seconds"] = 0.1
    module.PROCESS_PROFILE["kill_reap_seconds"] = 0.5
elif scenario == "descriptor-exhaustion":
    phase = CONFIG["phase"]
    target_expression = {
        "preflight": (module, "_open_directory_chain"),
        "launcher": (module, "_cloexec_pipe"),
        "selector": (module.selectors, "DefaultSelector"),
    }
    owner, attribute = target_expression[phase]
    original_target = getattr(owner, attribute)

    def exhaust(operation):
        original_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        soft_limit = (
            64
            if original_limit[0] == resource.RLIM_INFINITY
            else min(original_limit[0], 64)
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft_limit, original_limit[1]))
        descriptors = []
        try:
            while True:
                try:
                    descriptors.append(os.open("/dev/null", os.O_RDONLY))
                except OSError as error:
                    if error.errno != errno.EMFILE:
                        raise
                    break
            return operation()
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
            resource.setrlimit(resource.RLIMIT_NOFILE, original_limit)

    def exhausted_target(*arguments, **keywords):
        if phase == "launcher":
            setattr(owner, attribute, original_target)
        return exhaust(lambda: original_target(*arguments, **keywords))

    setattr(owner, attribute, exhausted_target)
elif scenario == "output-oserror":
    phase = CONFIG["phase"]
    original_read_output = module._read_bounded_output
    original_selector = module.selectors.DefaultSelector
    original_os_target = getattr(module.os, phase, None)

    def fail(*_arguments, **_keywords):
        raise OSError(5, "sensitive output failure")

    class FailingSelector:
        def __init__(self):
            self.target = original_selector()

        def __enter__(self):
            self.target.__enter__()
            return self

        def __exit__(self, *arguments):
            result = self.target.__exit__(*arguments)
            return fail() if phase == "close" else result

        def __getattr__(self, name):
            if name == phase:
                return fail
            return getattr(self.target, name)

    def failed_read_output(*arguments):
        module.selectors.DefaultSelector = FailingSelector
        if phase in {"set_blocking", "read"}:
            setattr(module.os, phase, fail)
        try:
            return original_read_output(*arguments)
        finally:
            module.selectors.DefaultSelector = original_selector
            if original_os_target is not None:
                setattr(module.os, phase, original_os_target)

    module._read_bounded_output = failed_read_output
elif scenario == "cancellation-after-spawn":
    module.PROCESS_PROFILE["validation_deadline_seconds"] = 10
    module.PROCESS_PROFILE["termination_grace_seconds"] = 0.1
    module.PROCESS_PROFILE["kill_reap_seconds"] = 0.5
    child_pid = Path(CONFIG["child_pid"])
    original_spawn = module._spawn_launcher

    def cancelling_spawn(*arguments, **keywords):
        process = original_spawn(*arguments, **keywords)
        deadline = time.monotonic() + 3
        while not child_pid.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("launcher marker timeout")
            time.sleep(0.01)
        os.kill(os.getpid(), getattr(signal, CONFIG["signal_name"]))
        return process

    module._spawn_launcher = cancelling_spawn
elif scenario == "session-acknowledgement":
    setup_stage = Path(CONFIG["setup_stage"])
    launcher_pid = Path(CONFIG["launcher_pid"])
    module.PROCESS_PROFILE["kill_reap_seconds"] = 1
    parent_pid = os.getpid()
    original_close_inherited = module._close_inherited_descriptors

    def delayed_close_inherited(descriptors, preserved):
        if os.getpid() != parent_pid and not setup_stage.exists():
            setup_stage.write_text("before", encoding="utf-8")
            time.sleep(0.2)
            original_close_inherited(descriptors, preserved)
            setup_stage.write_text("after", encoding="utf-8")
            return
        return original_close_inherited(descriptors, preserved)

    original_spawn = module._spawn_launcher

    def observed_spawn(*arguments, **keywords):
        process = original_spawn(*arguments, **keywords)
        deadline = time.monotonic() + 0.3
        while not setup_stage.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        session_ready = (
            setup_stage.read_text(encoding="utf-8") == "after"
            and os.getsid(process.pid) == process.pid
            and os.getpgid(process.pid) == process.pid
        )
        launcher_pid.write_text(str(process.pid), encoding="utf-8")
        arguments[1].cancellation_signal = signal.SIGTERM
        if not session_ready:
            setup_stage.write_text("returned-before-ready", encoding="utf-8")
        return process

    module._close_inherited_descriptors = delayed_close_inherited
    module._spawn_launcher = observed_spawn
elif scenario == "pre-setsid-readiness":
    setup_stage = Path(CONFIG["setup_stage"])
    child_pid = Path(CONFIG["child_pid"])
    direct_kill = Path(CONFIG["direct_kill"])
    exact_wait = Path(CONFIG["exact_wait"])
    group_kill = Path(CONFIG["group_kill"])
    module.PROCESS_PROFILE["kill_reap_seconds"] = 0.5
    parent_pid = os.getpid()
    original_fork = module.os.fork
    original_kill = module.os.kill
    original_killpg = module.os.killpg
    original_setsid = module.os.setsid
    original_waitpid = module.os.waitpid

    def observed_fork():
        pid = original_fork()
        if pid > 0 and not child_pid.exists():
            child_pid.write_text(str(pid), encoding="utf-8")
        return pid

    def stalled_setsid():
        if os.getpid() != parent_pid and not setup_stage.exists():
            setup_stage.write_text("before", encoding="utf-8")
            time.sleep(30)
        return original_setsid()

    def observed_kill(pid, number):
        if (
            os.getpid() == parent_pid
            and child_pid.exists()
            and pid == int(child_pid.read_text())
        ):
            direct_kill.write_text(str(number), encoding="utf-8")
        return original_kill(pid, number)

    def rejected_killpg(pid, number):
        if (
            os.getpid() == parent_pid
            and child_pid.exists()
            and pid == int(child_pid.read_text())
        ):
            group_kill.write_text(str(number), encoding="utf-8")
            raise AssertionError("pre-setsid child was signaled as a group")
        return original_killpg(pid, number)

    def observed_waitpid(pid, options):
        result = original_waitpid(pid, options)
        if (
            os.getpid() == parent_pid
            and child_pid.exists()
            and pid == int(child_pid.read_text())
        ):
            exact_wait.write_text(str(result[0]), encoding="utf-8")
        return result

    module.os.fork = observed_fork
    module.os.kill = observed_kill
    module.os.killpg = rejected_killpg
    module.os.setsid = stalled_setsid
    module.os.waitpid = observed_waitpid
elif scenario in {"pregate-cleanup-failure", "pregate-cleanup-success"}:
    original_fork = module.os.fork
    original_fdopen = module.os.fdopen
    original_waitpid = module.os.waitpid
    launcher_pid = None
    wait_failed = False

    def observed_fork():
        global launcher_pid
        pid = original_fork()
        if pid > 0 and launcher_pid is None:
            launcher_pid = pid
        return pid

    def fail_stream_materialization(*arguments, **keywords):
        if launcher_pid is not None:
            raise OSError(errno.EIO, "fixture stream materialization")
        return original_fdopen(*arguments, **keywords)

    def fail_first_exact_wait(pid, options):
        global wait_failed
        if pid == launcher_pid and not wait_failed:
            wait_failed = True
            raise OSError(errno.EIO, "fixture exact cleanup")
        return original_waitpid(pid, options)

    module.os.fork = observed_fork
    module.os.fdopen = fail_stream_materialization
    if scenario == "pregate-cleanup-failure":
        module.os.waitpid = fail_first_exact_wait
elif scenario == "launcher-gate-failure":
    launcher_pid_marker = Path(CONFIG["launcher_pid"])
    parent_pid = os.getpid()
    original_fork = module.os.fork
    original_waitpid = module.os.waitpid
    original_arm_child_gate = module._arm_child_gate
    observed_launcher_pid = None
    fork_count = 0

    def observed_fork():
        global fork_count, observed_launcher_pid
        pid = original_fork()
        if pid > 0:
            fork_count += 1
            if observed_launcher_pid is None:
                observed_launcher_pid = pid
                launcher_pid_marker.write_text(str(pid), encoding="utf-8")
        return pid

    def reject_parent_gate(descriptor, deadline):
        if os.getpid() == parent_pid and fork_count == 1:
            return False
        return original_arm_child_gate(descriptor, deadline)

    module.PROCESS_PROFILE["kill_reap_seconds"] = 0.5
    module.os.fork = observed_fork
    module._arm_child_gate = reject_parent_gate
elif scenario == "writer-kill-failure":
    module.PROCESS_PROFILE["accepted_output_deadline_seconds"] = 0.25
    module.PROCESS_PROFILE["kill_reap_seconds"] = 0.5
    original_kill = module.os.kill
    writer_kill_failed = False

    def fail_first_writer_kill(pid, number):
        global writer_kill_failed
        if (
            not writer_kill_failed
            and writer_pid is not None
            and pid == writer_pid
            and number == module.signal.SIGKILL
        ):
            writer_kill_failed = True
            raise OSError(errno.EIO, "fixture writer kill failure")
        return original_kill(pid, number)

    module.os.kill = fail_first_writer_kill
elif scenario == "group-signal-failure":
    failure_marker = Path(CONFIG["failure_marker"])
    failed_signal = getattr(signal, CONFIG["failed_signal"])
    child_pid_marker = Path(CONFIG["child_pid"])
    module.PROCESS_PROFILE["validation_deadline_seconds"] = 0.5
    module.PROCESS_PROFILE["termination_grace_seconds"] = 0.05
    module.PROCESS_PROFILE["kill_reap_seconds"] = 0.5
    original_fork = module.os.fork
    original_killpg = module.os.killpg
    original_waitpid = module.os.waitpid
    launcher_pid = None
    signal_failed = False

    def observed_fork():
        global launcher_pid
        pid = original_fork()
        if pid > 0 and launcher_pid is None:
            launcher_pid = pid
            child_pid_marker.write_text(str(pid), encoding="utf-8")
        return pid

    def fail_first_group_signal(pid, number):
        global signal_failed
        if not signal_failed and number == failed_signal:
            signal_failed = True
            failure_marker.write_text(str(number), encoding="utf-8")
            raise OSError(errno.EIO, "fixture group signal failure")
        return original_killpg(pid, number)

    module.os.fork = observed_fork
    module.os.killpg = fail_first_group_signal
else:
    raise RuntimeError(f"unsupported process-supervision scenario: {scenario!r}")

status = module.main(sys.argv[int(CONFIG.get("main_argv_start", 3)) :])
if scenario == "post-fork-profile-error":
    sys.setprofile(None)
    if profile_raise_count != 1:
        raise RuntimeError(
            f"post-fork profile failure count was {profile_raise_count}, expected 1"
        )
    try:
        waited = module.os.waitpid(-1, module.os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise RuntimeError(f"post-fork child remained waitable: {waited[0]}")
elif scenario == "launcher-gate-failure":
    if observed_launcher_pid is None:
        raise RuntimeError("launcher was not created")
    try:
        original_waitpid(observed_launcher_pid, module.os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise RuntimeError("launcher remained waitable")
elif scenario == "writer-kill-failure":
    if not writer_kill_failed or writer_pid is None:
        raise RuntimeError("writer kill failure was not exercised")
    try:
        original_waitpid(writer_pid, module.os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise RuntimeError("writer remained waitable after cleanup")
elif scenario == "group-signal-failure":
    if not signal_failed or launcher_pid is None:
        raise RuntimeError("group signal failure was not exercised")
    try:
        original_waitpid(launcher_pid, module.os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise RuntimeError("launcher remained waitable after cleanup")
raise SystemExit(status)
