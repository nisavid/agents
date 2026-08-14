import importlib.util
import sys
from pathlib import Path

specification = importlib.util.spec_from_file_location(
    "task_witness_client_writer_guard_fixture",
    sys.argv[1],
)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
pid_marker = Path(sys.argv[2])
reaped_marker = Path(sys.argv[3])
phase = sys.argv[4]
original_fork = module.os.fork
original_waitpid = module.os.waitpid
original_start = module._start_terminal_writer
original_arm = module._arm_child_gate
original_wait = module._wait_terminal_writer
writer_pid = None
creating_writer = False
injected = False


def observed_fork():
    global writer_pid
    pid = original_fork()
    if pid > 0 and writer_pid is None:
        writer_pid = pid
        pid_marker.write_text(str(pid), encoding="utf-8")
    return pid


def observed_waitpid(pid, options):
    result = original_waitpid(pid, options)
    if writer_pid is not None and result[0] == writer_pid:
        reaped_marker.touch()
    return result


def start(descriptor, raw, invocation, context):
    global creating_writer, injected
    creating_writer = invocation is not None
    if phase == "deadline" and creating_writer:
        injected = True
        context.record(MemoryError("sensitive deadline allocation failure"))
    try:
        result = original_start(descriptor, raw, invocation, context)
    finally:
        creating_writer = False
    if phase == "return" and invocation is not None:
        injected = True
        raise MemoryError("sensitive return allocation failure")
    return result


def arm(descriptor, deadline):
    global injected
    if phase == "gate-false" and creating_writer:
        injected = True
        return False
    if phase == "gate" and creating_writer:
        injected = True
        raise MemoryError("sensitive gate allocation failure")
    return original_arm(descriptor, deadline)


def wait(pid, deadline, invocation, context):
    global injected
    if phase == "before-wait" and invocation is not None:
        injected = True
        raise MemoryError("sensitive wait allocation failure")
    return original_wait(pid, deadline, invocation, context)


module.os.fork = observed_fork
module.os.waitpid = observed_waitpid
module._start_terminal_writer = start
module._arm_child_gate = arm
module._wait_terminal_writer = wait
module._canonical_client_process = lambda _invocation: True
module.PROCESS_PROFILE["kill_reap_seconds"] = 0.5
module.PROCESS_PROFILE["accepted_output_deadline_seconds"] = 0.5
invocation = module.InvocationState()
try:
    module._write_terminal(1, b"accepted", invocation)
except module.ClientError as error:
    if phase == "gate-false":
        sys.stderr.buffer.write(module._diagnostic(error, invocation))
    status = error.exit_code
except MemoryError:
    status = 124
else:
    status = 0
if not injected:
    raise RuntimeError("writer failure was not injected")
raise SystemExit(status)
