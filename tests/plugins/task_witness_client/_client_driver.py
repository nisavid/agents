"""Physical canonical-client driver template for Task Witness tests."""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


CONFIG = globals().get("CONFIG", {"scenario": "plain"})
real_executable = sys.executable
scenario = CONFIG["scenario"]

if scenario == "module-frame-monitoring":
    import os
    import pwd
    from types import SimpleNamespace

    source = Path(sys.argv[1])
    installation_access = Path(sys.argv[2])
    configured_local_events_marker = Path(sys.argv[3])
    post_free_local_events_marker = Path(sys.argv[4])
    callback_armed_marker = Path(sys.argv[5])
    main_returned_marker = Path(sys.argv[6])
    post_main_callback_marker = Path(sys.argv[7])
    final_local_events_marker = Path(sys.argv[8])
    code = compile(source.read_bytes(), str(source), "exec")
    monitoring = sys.monitoring
    state = {"main_returned": False, "wrapped": False}
    namespace = {"__name__": "__main__", "__file__": str(source)}

    def callback(_code, _offset):
        if not state["wrapped"]:
            main = namespace.get("main")
            if callable(main):

                def wrapped_main(*arguments, **keywords):
                    result = main(*arguments, **keywords)
                    state["main_returned"] = True
                    main_returned_marker.write_text("returned", encoding="utf-8")
                    return result

                namespace["main"] = wrapped_main
                state["wrapped"] = True
                callback_armed_marker.write_text("armed", encoding="utf-8")
        elif state["main_returned"]:
            post_main_callback_marker.write_text("fired", encoding="utf-8")
            raise RuntimeError("monitoring callback ran after main returned")

    original_exit = os._exit

    def sentinel_getpwuid(_identifier):
        installation_access.write_text("accessed", encoding="utf-8")
        return SimpleNamespace(
            pw_dir=str(installation_access.with_name("nonexistent-home"))
        )

    def recording_exit(status):
        final_local_events_marker.write_text(
            str(monitoring.get_local_events(5, code)),
            encoding="utf-8",
        )
        original_exit(status)

    pwd.getpwuid = sentinel_getpwuid
    os._exit = recording_exit
    monitoring.use_tool_id(5, "fixture")
    monitoring.register_callback(5, monitoring.events.INSTRUCTION, callback)
    monitoring.set_local_events(5, code, monitoring.events.INSTRUCTION)
    configured_local_events_marker.write_text(
        str(monitoring.get_local_events(5, code)),
        encoding="utf-8",
    )
    monitoring.free_tool_id(5)
    post_free_local_events_marker.write_text(
        str(monitoring.get_local_events(5, code)),
        encoding="utf-8",
    )
    sys.argv = [str(source), "validate", "--bundle", "/tmp/bundle"]
    exec(code, namespace)
    raise RuntimeError("client module returned without terminating the process")

if scenario == "self-erasing-pre-import-instrumentation":
    trace_state = {"callback_fired": False}

    def pre_import_trace_target() -> None:
        return None

    def erase_trace_on_target_call(frame, event, _argument):
        if event == "call" and frame.f_code is pre_import_trace_target.__code__:
            trace_state["callback_fired"] = True
            sys.settrace(None)

    sys.settrace(erase_trace_on_target_call)
    pre_import_trace_target()
    if not trace_state["callback_fired"] or sys.gettrace() is not None:
        raise RuntimeError("fixture pre-import trace did not erase itself")
    Path(CONFIG["pre_import_trace_marker"]).write_text(
        "callback-fired",
        encoding="utf-8",
    )

specification = importlib.util.spec_from_file_location(
    "task_witness_client_fixture", sys.argv[1]
)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
module._installed_root = lambda: Path(sys.argv[2])

if scenario == "replace-loaded-client-generation":
    client = Path(sys.argv[1])
    source = client.read_bytes()
    generation_pattern = re.compile(
        rb'(?m)^CLIENT_SOURCE_GENERATION_SHA256 = "([0-9a-f]{64})"$'
    )
    replacement = source + b"\n# distinct deployed client generation\n"
    matches = list(generation_pattern.finditer(replacement))
    if len(matches) != 1:
        raise RuntimeError("fixture requires exactly one client generation")
    digest_start, digest_end = matches[0].span(1)
    normalized = (
        replacement[:digest_start] + (b"0" * 64) + replacement[digest_end:]
    )
    replacement = (
        replacement[:digest_start]
        + hashlib.sha256(normalized).hexdigest().encode("ascii")
        + replacement[digest_end:]
    )

    client.chmod(0o700)
    client.write_bytes(replacement)
    client.chmod(0o500)

    deployment = Path(sys.argv[2]) / "deployment.json"
    receipt = json.loads(deployment.read_bytes())
    receipt.pop("content_sha256")
    client_binding = receipt["control_set"]["client"]
    client_binding["length"] = len(replacement)
    client_binding["sha256"] = hashlib.sha256(replacement).hexdigest()
    unsigned = json.dumps(
        receipt,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    receipt["content_sha256"] = hashlib.sha256(unsigned).hexdigest()
    deployment.write_bytes(
        json.dumps(
            receipt,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    deployment.chmod(0o600)

if scenario == "substitute-interpreter":
    pinned_executable = CONFIG["pinned_executable"]
    module.sys.executable = pinned_executable
    original_execve = module.os.execve

    def substitute_execve(executable, command, environment):
        command = list(command)
        if command and command[0] == pinned_executable:
            command[0] = real_executable
            executable = real_executable
        return original_execve(executable, command, environment)

    module.os.execve = substitute_execve
elif scenario == "installation-sentinel":
    import os
    import signal
    import time

    installation_access = Path(CONFIG["installation_access"])

    def installation_root():
        installation_access.write_text("accessed", encoding="utf-8")
        raise AssertionError("installation accessed")

    module._installed_root = installation_root
    sentinel_mode = CONFIG.get("sentinel_mode", "plain")
    if sentinel_mode == "version":
        module.sys.version_info = (3, 12, 0)
    elif sentinel_mode == "signal-state":
        signal_number = getattr(module.signal, CONFIG["signal_name"])
        action = CONFIG["signal_action"]
        if action == "blocked":
            module.signal.pthread_sigmask(module.signal.SIG_BLOCK, {signal_number})
        else:
            disposition = {
                "ignored": module.signal.SIG_IGN,
                "default": module.signal.SIG_DFL,
                "custom": lambda *_arguments: None,
            }[action]
            module.signal.signal(signal_number, disposition)
    elif sentinel_mode == "unobservable-signal":
        error = OSError if CONFIG["error_name"] == "OSError" else ValueError

        def failed_getsignal(_number):
            raise error("fixture getsignal failure")

        module.signal.getsignal = failed_getsignal
    elif sentinel_mode == "timer":
        timer = getattr(module.signal, CONFIG["timer_name"])
        module.signal.setitimer(timer, 60.0, 0.0)
    elif sentinel_mode == "timer-unobservable":
        timer_failure = CONFIG["timer_failure"]
        if timer_failure == "missing":
            del module.signal.getitimer
        else:

            def failed_getitimer(_timer):
                error = OSError if timer_failure == "oserror" else ValueError
                raise error("fixture getitimer failure")

            module.signal.getitimer = failed_getitimer
    elif sentinel_mode == "preexisting-child":
        child_pid = os.fork()
        if child_pid == 0:
            while True:
                time.sleep(1)
    elif sentinel_mode == "instrumentation":
        kind = CONFIG["instrumentation_kind"]
        if kind == "trace":
            sys.settrace(lambda *_arguments: None)
        elif kind == "profile":
            sys.setprofile(lambda *_arguments: None)
        elif kind == "thread-trace":
            import threading

            threading.settrace(lambda *_arguments: None)
        elif kind == "thread-profile":
            import threading

            threading.setprofile(lambda *_arguments: None)
        elif kind == "monitoring-occupied":
            module.sys.monitoring.use_tool_id(CONFIG["tool_id"], "fixture")
        elif kind in {"monitoring-local-freed", "monitoring-property-freed"}:
            monitoring = module.sys.monitoring
            monitoring.use_tool_id(5, "fixture")
            monitoring.register_callback(5, monitoring.events.LINE, lambda *_args: None)
            code = (
                module._ChildLifecycle.responsible.fget.__code__
                if kind == "monitoring-property-freed"
                else module._parse_public_arguments.__code__
            )
            monitoring.set_local_events(
                5,
                code,
                monitoring.events.LINE,
            )
            monitoring.free_tool_id(5)
        elif kind == "monitoring-annotation-freed":
            monitoring = module.sys.monitoring
            monitoring.use_tool_id(5, "fixture")
            monitoring.register_callback(5, monitoring.events.LINE, lambda *_args: None)
            monitoring.set_local_events(
                5,
                module._absolute.__annotate__.__code__,
                monitoring.events.LINE,
            )
            monitoring.free_tool_id(5)
        elif kind == "monitoring-global-freed":
            monitoring = module.sys.monitoring
            tool_id = 5
            monitoring.use_tool_id(tool_id, "fixture")
            monitoring.register_callback(
                tool_id, monitoring.events.LINE, lambda *_args: None
            )
            monitoring.set_events(tool_id, monitoring.events.LINE)
            monitoring.free_tool_id(tool_id)
            Path(CONFIG["global_events_marker"]).write_text(
                str(monitoring.get_events(tool_id)),
                encoding="utf-8",
            )
        elif kind == "monitoring-unavailable":
            module.sys.monitoring = object()
        else:
            raise RuntimeError(f"unsupported instrumentation kind: {kind!r}")
    elif sentinel_mode != "plain":
        raise RuntimeError(f"unsupported installation sentinel: {sentinel_mode!r}")
elif scenario not in {
    "plain",
    "replace-loaded-client-generation",
    "self-erasing-pre-import-instrumentation",
}:
    raise RuntimeError(f"unsupported client-driver scenario: {scenario!r}")

status = module.main(sys.argv[int(CONFIG.get("main_argv_start", 3)) :])
if scenario == "installation-sentinel" and sentinel_mode == "timer":
    timer_state = module.signal.getitimer(timer)
    if timer_state[0] <= 0 or timer_state[1] != 0:
        raise RuntimeError("ambient timer was modified")
if scenario == "installation-sentinel" and sentinel_mode == "preexisting-child":
    os.kill(child_pid, signal.SIGKILL)
    os.waitpid(child_pid, 0)
raise SystemExit(status)
