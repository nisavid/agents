"""Physical terminal-output client scenarios."""

import importlib.util
import errno
import os
import resource
import signal
import sys
import time
from pathlib import Path


CONFIG = globals().get("CONFIG", {})
specification = importlib.util.spec_from_file_location(
    "task_witness_client_terminal_output_fixture", sys.argv[1]
)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
module._installed_root = lambda: Path(sys.argv[2])
scenario = CONFIG["scenario"]

writer_pid = None
observing_writer = False
observing_accepted_writer = False
if CONFIG.get("observe_writer"):
    pid_marker = Path(CONFIG["pid_marker"])
    reaped_marker = Path(CONFIG["reaped_marker"])
    accepted_gate_armed_marker = (
        Path(CONFIG["accepted_gate_armed_marker"])
        if "accepted_gate_armed_marker" in CONFIG
        else None
    )
    original_start = module._start_terminal_writer
    original_fork = module.os.fork
    original_waitpid = module.os.waitpid
    original_arm_child_gate = module._arm_child_gate

    def observed_start(*arguments, **keywords):
        global observing_accepted_writer, observing_writer
        observing_writer = True
        invocation = arguments[2] if len(arguments) > 2 else keywords.get("invocation")
        observing_accepted_writer = invocation is not None
        try:
            return original_start(*arguments, **keywords)
        finally:
            observing_accepted_writer = False
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

    def observed_arm_child_gate(descriptor, deadline):
        armed = original_arm_child_gate(descriptor, deadline)
        if (
            armed
            and observing_accepted_writer
            and accepted_gate_armed_marker is not None
        ):
            accepted_gate_armed_marker.write_text("armed", encoding="utf-8")
        return armed

    module._start_terminal_writer = observed_start
    module.os.fork = observed_fork
    module.os.waitpid = observed_waitpid
    module._arm_child_gate = observed_arm_child_gate

if scenario == "plain":
    pass
elif scenario == "persistent-recovery-deadline":
    forked = Path(CONFIG["forked"])
    original_inventory = module._proven_open_descriptor_inventory

    def fail_deadline_clock():
        raise MemoryError("fixture persistent recovery deadline allocation")

    def arm_deadline_failure():
        inventory = original_inventory()
        if observing_writer:
            module.time.monotonic = fail_deadline_clock
        return inventory

    def unexpected_writer_fork():
        if observing_writer:
            forked.touch()
            raise AssertionError("writer fork after deadline failure")
        return original_fork()

    module._proven_open_descriptor_inventory = arm_deadline_failure
    module.os.fork = unexpected_writer_fork
elif scenario == "file-size-limit":
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
elif scenario == "writer-fork-failure":
    inherited_fork = module.os.fork

    def failed_fork():
        if observing_writer:
            raise OSError(errno.EPERM, "sensitive fork failure")
        return inherited_fork()

    module.os.fork = failed_fork
elif scenario == "nonzero-writer":
    original_exit = module.os._exit

    def fail_successful_child_exit(code):
        original_exit(1 if code == 0 else code)

    module.os._exit = fail_successful_child_exit
elif scenario == "output-deadline":
    module.PROCESS_PROFILE["accepted_output_deadline_seconds"] = 0.25
elif scenario == "context-preparation-failure":
    forked = Path(CONFIG["forked"])
    original_prepare = module._prepare_cleanup_context

    def fail_writer_context(*arguments, **keywords):
        if keywords.get("writer_seconds") is not None:
            raise MemoryError("fixture writer context preparation failure")
        return original_prepare(*arguments, **keywords)

    def unexpected_writer_fork():
        if observing_writer:
            forked.touch()
            raise AssertionError("writer fork after context preparation failure")
        return original_fork()

    module._prepare_cleanup_context = fail_writer_context
    module.os.fork = unexpected_writer_fork
elif scenario == "pid-publication":
    published = Path(CONFIG["published"])
    original_prepare = module._prepare_cleanup_context
    inherited_fork = module.os.fork
    original_sigmask = module.signal.pthread_sigmask
    writer_context = None
    fork_returned = False

    def capture_context(*arguments, **keywords):
        global writer_context
        context = original_prepare(*arguments, **keywords)
        if keywords.get("writer_seconds") is not None:
            writer_context = context
        return context

    def publication_fork():
        global fork_returned
        pid = inherited_fork()
        if observing_writer and pid > 0:
            fork_returned = True
        return pid

    def observe_first_parent_step(*arguments, **keywords):
        global fork_returned
        if fork_returned:
            if (
                writer_context is None
                or writer_context.pid is None
                or writer_context.lifecycle.state != "owned"
            ):
                raise AssertionError(
                    "writer PID was not published before parent restoration"
                )
            published.touch()
            fork_returned = False
        return original_sigmask(*arguments, **keywords)

    module._prepare_cleanup_context = capture_context
    module.os.fork = publication_fork
    module.signal.pthread_sigmask = observe_first_parent_step
elif scenario == "pid-publication-failure":
    phase = CONFIG["phase"]
    cleanup_elapsed_marker = Path(CONFIG["cleanup_elapsed_marker"])
    no_waitable_marker = Path(CONFIG["no_waitable_marker"])
    numeric_signal_marker = Path(CONFIG["numeric_signal_marker"])
    inherited_waitpid = module.os.waitpid
    original_wildcard = module._wildcard_reap_sole_child
    original_kill = module.os.kill
    original_killpg = module.os.killpg
    publication_error = MemoryError(f"fixture {phase} writer PID publication")
    published_pid = None
    wait_faulted = False
    publication_error_propagated = False

    def fail_pid_publication(context, pid):
        global published_pid
        if not observing_writer:
            return original_publish(context, pid)
        published_pid = pid
        if phase == "after-pid-store":
            context.pid = pid
        elif phase != "before-mutation":
            raise AssertionError(f"unknown publication failure phase: {phase}")
        raise publication_error

    def fail_first_wildcard_wait(pid, options):
        global wait_faulted
        if pid == -1 and not wait_faulted:
            wait_faulted = True
            raise MemoryError("fixture writer wildcard wait")
        return inherited_waitpid(pid, options)

    def observed_wildcard(context):
        global publication_error_propagated
        if context.error is not publication_error:
            raise AssertionError("writer publication failure was not recorded first")
        if context.pid is not None or context.lifecycle.state != "lost":
            raise AssertionError("writer publication failure retained signal authority")
        if signal.pthread_sigmask(signal.SIG_BLOCK, set()):
            raise AssertionError("writer publication cleanup ran before mask restore")
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
        if published_pid is not None and pid == published_pid:
            numeric_signal_marker.touch()
            raise AssertionError("untrusted writer PID was signaled")
        return operation(pid, number)

    def observed_kill(pid, number):
        return reject_published_pid_signal(original_kill, pid, number)

    def observed_killpg(pid, number):
        return reject_published_pid_signal(original_killpg, pid, number)

    original_publish = module._CleanupContext.publish_pid
    module._CleanupContext.publish_pid = fail_pid_publication
    module.os.waitpid = fail_first_wildcard_wait
    module._wildcard_reap_sole_child = observed_wildcard
    module.os.kill = observed_kill
    module.os.killpg = observed_killpg
elif scenario == "full-stderr-budget":
    budget_marker = Path(CONFIG["budget_marker"])
    original_start = module._start_terminal_writer

    def budgeted_start(*arguments, **keywords):
        invocation = arguments[2]
        context = arguments[3]
        if invocation is None:
            budget_marker.write_text(str(context.writer_seconds), encoding="utf-8")
        return original_start(*arguments, **keywords)

    module._start_terminal_writer = budgeted_start
elif scenario == "accepted-output-budget":
    phase = CONFIG["phase"]
    module.PROCESS_PROFILE["accepted_output_deadline_seconds"] = 0.05
    original_inventory = module._proven_open_descriptor_inventory
    original_arm = module._arm_child_gate

    def delayed_inventory():
        if phase == "preflight" and observing_writer:
            time.sleep(0.1)
        return original_inventory()

    def delayed_arm(descriptor, deadline):
        if phase == "handoff" and observing_writer:
            time.sleep(0.1)
        return original_arm(descriptor, deadline)

    module._proven_open_descriptor_inventory = delayed_inventory
    module._arm_child_gate = delayed_arm
elif scenario == "cancellation-after-output":
    original_emit = module._emit_accepted_output

    def cancelling_emit(raw, invocation):
        original_emit(raw, invocation)
        os.kill(os.getpid(), signal.SIGTERM)

    module._emit_accepted_output = cancelling_emit
elif scenario == "unknown-writer-pid":
    forgot_pid = Path(CONFIG["forgot_pid"])
    wildcard_reaped = Path(CONFIG["wildcard_reaped"])
    unknown_signaled = Path(CONFIG["unknown_signaled"])
    inherited_fork = module.os.fork
    inherited_waitpid = module.os.waitpid
    original_wildcard = module._wildcard_reap_sole_child
    original_inventory = module._proven_open_descriptor_inventory
    original_prepare = module._prepare_cleanup_context
    original_monotonic = module.time.monotonic
    original_kill = module.os.kill
    original_killpg = module.os.killpg
    forgotten = False
    deadline_faulted = False
    wildcard_wait_failed = False
    wildcard_error_propagated = False
    writer_context = None

    def fail_one_deadline_clock():
        global deadline_faulted
        if not deadline_faulted:
            deadline_faulted = True
            raise MemoryError("fixture recovery deadline allocation")
        return original_monotonic()

    def arm_deadline_fault():
        inventory = original_inventory()
        if observing_writer:
            module.time.monotonic = fail_one_deadline_clock
        return inventory

    def capture_context(*arguments, **keywords):
        global writer_context
        context = original_prepare(*arguments, **keywords)
        if keywords.get("writer_seconds") is not None:
            writer_context = context
        return context

    def forgetting_fork():
        global forgotten
        pid = inherited_fork()
        if observing_writer and pid > 0 and not forgotten:
            forgotten = True
            forgot_pid.write_text(str(pid), encoding="utf-8")
            raise MemoryError("fixture lost writer pid")
        return pid

    def fail_first_wildcard_wait(pid, options):
        global wildcard_wait_failed
        if pid == -1 and not wildcard_wait_failed:
            wildcard_wait_failed = True
            raise MemoryError("fixture wildcard writer wait")
        return inherited_waitpid(pid, options)

    def observed_wildcard(context):
        global wildcard_error_propagated
        wildcard_reaped.touch()
        try:
            if context is not writer_context:
                raise AssertionError(
                    "writer wildcard cleanup lost its pre-fork context"
                )
            return original_wildcard(context)
        except module.ClientError as error:
            wildcard_error_propagated = isinstance(error.__cause__, MemoryError)
            raise

    def reject_unknown_signal(operation, pid, number):
        if forgot_pid.exists() and pid == int(forgot_pid.read_text()):
            unknown_signaled.touch()
            raise AssertionError("unknown writer cleanup used a numeric signal")
        return operation(pid, number)

    def observed_kill(pid, number):
        return reject_unknown_signal(original_kill, pid, number)

    def observed_killpg(pid, number):
        return reject_unknown_signal(original_killpg, pid, number)

    module.os.fork = forgetting_fork
    module.os.kill = observed_kill
    module.os.killpg = observed_killpg
    module.os.waitpid = fail_first_wildcard_wait
    module._wildcard_reap_sole_child = observed_wildcard
    module._proven_open_descriptor_inventory = arm_deadline_fault
    module._prepare_cleanup_context = capture_context
elif scenario == "post-fork-handoff":
    phase = CONFIG["phase"]
    parent_pid = os.getpid()
    original_mask = module.signal.pthread_sigmask
    injected = False

    def injecting_mask(how, signals):
        global injected
        previous = original_mask(how, signals)
        before_fork = (
            phase == "before-fork"
            and writer_pid is None
            and how == signal.SIG_BLOCK
            and set(signals) == set(module.CANCELLATION_SIGNALS)
        )
        after_fork = (
            phase != "before-fork"
            and writer_pid is not None
            and how == signal.SIG_SETMASK
        )
        if not injected and os.getpid() == parent_pid and (before_fork or after_fork):
            injected = True
            if phase == "memory":
                raise MemoryError("sensitive mask allocation")
            os.kill(parent_pid, signal.SIGTERM)
        return previous

    module.signal.pthread_sigmask = injecting_mask
elif scenario == "post-reap-memory":
    killed_after_reap = Path(CONFIG["killed_after_reap"])
    failure_injected = Path(CONFIG["failure_injected"])
    original_wait = module._wait_terminal_writer
    original_kill = module.os.kill
    injected = False

    def failing_wait(pid, deadline, invocation, context):
        global injected
        original_wait(pid, deadline, invocation, context)
        if invocation is not None and context.wait_status is not None and not injected:
            injected = True
            failure_injected.touch()
            raise MemoryError("sensitive post-reap failure")

    def observed_kill(pid, number):
        if injected and writer_pid is not None and pid == writer_pid:
            killed_after_reap.write_text(str(pid), encoding="utf-8")
            raise ProcessLookupError(pid)
        return original_kill(pid, number)

    def discard_writer(_descriptor, _raw, _mask, gate, _inherited):
        module.os._exit(0 if module._await_parent_gate(gate) else 1)

    module._wait_terminal_writer = failing_wait
    module._write_terminal_child = discard_writer
    module.os.kill = observed_kill
elif scenario == "post-reap-echild":
    echild_injected = Path(CONFIG["echild_injected"])
    killed_after_echild = Path(CONFIG["killed_after_echild"])
    inherited_waitpid = module.os.waitpid
    original_kill = module.os.kill
    injected = False

    def echild_waitpid(pid, options):
        global injected
        result = inherited_waitpid(pid, options)
        if writer_pid is not None and result[0] == writer_pid and not injected:
            injected = True
            echild_injected.touch()
            raise ChildProcessError(pid)
        return result

    def observed_kill(pid, number):
        if injected and writer_pid is not None and pid == writer_pid:
            killed_after_echild.touch()
            raise AssertionError("reaped writer pid was signaled")
        return original_kill(pid, number)

    def discard_writer(_descriptor, _raw, _mask, gate, _inherited):
        module.os._exit(0 if module._await_parent_gate(gate) else 1)

    module.os.waitpid = echild_waitpid
    module.os.kill = observed_kill
    module._write_terminal_child = discard_writer
elif scenario == "post-writer-result-fault":
    faulted = Path(CONFIG["faulted"])
    original_wait = module._wait_terminal_writer

    def fail_result_materialization(pid, deadline, invocation, context):
        original_wait(pid, deadline, invocation, context)
        if context.wait_status == 0:
            faulted.touch()
            raise RuntimeError("sensitive post-writer result failure")

    module._wait_terminal_writer = fail_result_materialization
elif scenario == "late-failure":
    phase = CONFIG["phase"]
    if phase == "selector-memory":

        def fail_selector():
            raise MemoryError("sensitive allocation failure")

        module.selectors.DefaultSelector = fail_selector
    elif phase == "bootstrap-memory":

        def fail_invocation_state():
            raise MemoryError("sensitive bootstrap allocation")

        module.InvocationState = fail_invocation_state
    elif phase == "diagnostic-memory":

        def reject_arguments(_arguments):
            raise module.ClientError(
                "client resources are unavailable",
                module.EXIT_RESOURCE,
            )

        def fail_diagnostic(_error, _invocation):
            raise MemoryError("sensitive diagnostic allocation")

        module._parse_public_arguments = reject_arguments
        module._diagnostic = fail_diagnostic
    elif phase == "finish-memory":

        def fail_finish_success(_invocation, _previous_mask):
            raise MemoryError("sensitive final allocation")

        module.InvocationState.finish_success = fail_finish_success
    elif phase == "finish-runtime":

        def fail_finish_success(_invocation, _previous_mask):
            raise RuntimeError("sensitive final transition failure")

        module.InvocationState.finish_success = fail_finish_success
    elif phase == "exception-text-memory":

        class UnprintableError(Exception):
            def __str__(self):
                raise MemoryError("sensitive error allocation")

        def reject_arguments(_arguments):
            raise UnprintableError()

        module._parse_public_arguments = reject_arguments
    else:
        accepted_writer_attempted = Path(CONFIG["accepted_writer_attempted"])
        original_emit = module._emit_accepted_output
        original_start = module._start_terminal_writer

        def drifted_emit(raw, invocation):
            signal.signal(getattr(signal, phase), signal.SIG_IGN)
            return original_emit(raw, invocation)

        def observed_start(descriptor, raw, invocation, context):
            if invocation is not None:
                accepted_writer_attempted.write_text("attempted", encoding="utf-8")
            return original_start(descriptor, raw, invocation, context)

        module._emit_accepted_output = drifted_emit
        module._start_terminal_writer = observed_start
elif scenario == "restore-signal-handler":
    marker = Path(CONFIG["marker"])

    def original_handler(_number, _frame):
        marker.write_text("restored", encoding="utf-8")

    signal.signal(signal.SIGTERM, original_handler)
else:
    raise RuntimeError(f"unsupported terminal-output scenario: {scenario!r}")

status = module.main(sys.argv[int(CONFIG.get("main_argv_start", 3)) :])
if scenario == "unknown-writer-pid":
    if not deadline_faulted:
        raise RuntimeError("recovery deadline fault was not exercised")
    if not wildcard_error_propagated:
        raise RuntimeError("wildcard cleanup failure was not propagated")
elif scenario == "pid-publication-failure":
    if published_pid is None or not wait_faulted:
        raise RuntimeError("writer PID publication cleanup was not exercised")
    if not publication_error_propagated:
        raise RuntimeError("writer PID publication error was not retained")
    try:
        inherited_waitpid(published_pid, module.os.WNOHANG)
    except ChildProcessError:
        no_waitable_marker.touch()
    else:
        raise RuntimeError("terminal writer child remained waitable")
elif scenario == "post-writer-result-fault":
    if not faulted.is_file():
        raise RuntimeError("post-writer result fault was not exercised")
elif scenario == "restore-signal-handler":
    os.kill(os.getpid(), signal.SIGTERM)
raise SystemExit(status)
