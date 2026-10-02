"""Installed-app adapter for its native MCP server (Linux stdio/UDS).

Use only an existing genuine owner registration and an inherited app route.
This private app interface is version-specific; do not discover sockets or
substitute another caller if authorization fails.
"""
import json
import os
import select
import subprocess
import time
import uuid


class NativeBridgeError(RuntimeError):
    """A bounded diagnostic; never retain raw app errors or response payloads."""
    def __init__(self, kind, *, stage, rpc_code=None, recipient=None):
        super().__init__(kind)
        self.kind = kind
        self.stage = stage
        self.rpc_code = rpc_code if type(rpc_code) is int else None
        self.recipient = recipient

    def diagnostic(self):
        value = {'kind': self.kind, 'stage': self.stage}
        if self.rpc_code is not None:
            value['rpcCode'] = self.rpc_code
        if self.recipient is not None:
            value['recipient'] = self.recipient
        return value


def rpc_failure(error, stage):
    """Classify known failures without persisting potentially private messages."""
    code = error.get('code') if isinstance(error, dict) else None
    message = str(error.get('message', '')) if isinstance(error, dict) else ''
    if 'connect EPERM' in message or 'connect EACCES' in message:
        kind = 'native_socket_access_denied'
    elif 'connect ENOENT' in message or 'connect ECONNREFUSED' in message:
        kind = 'native_socket_unavailable'
    elif code == -32003 and 'permission' in message.lower():
        kind = 'native_authorization_rejected'
    elif 'Codex app tool request failed' in message:
        # The app can hide the actual caller rejection behind this error.
        # Do not claim either authorization failure or route recovery from it.
        kind = 'native_app_request_failed'
    else:
        kind = 'native_rpc_failed'
    return NativeBridgeError(kind, stage=stage, rpc_code=code)


class NativeBridge:
    def __init__(self, config, pipe):
        context = config["nativeContext"]
        self.context = context
        self.seq = 0
        self.buffer = b""
        env = dict(os.environ, CODEX_APP_TOOLS_CALLER_HOST_ID=context["host"], CODEX_APP_TOOLS_PIPE_PATH=pipe)
        self.proc = subprocess.Popen([config["nodeBinary"], config["nativeBridgeScript"]], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        try:
            self.rpc("initialize", {"protocolVersion":"2024-11-05", "capabilities":{},
                "clientInfo":{"name":"account-usage-safeguard","version":"1"}})
            self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            self.proc.stdin.flush()
        except Exception:
            self.close()
            raise

    def rpc(self, method, params):
        stage = params.get('name', method) if method == 'tools/call' else method
        self.seq += 1
        request = {"jsonrpc":"2.0", "id":self.seq, "method":method, "params":params}
        self.proc.stdin.write((json.dumps(request)+"\n").encode())
        self.proc.stdin.flush()
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            if b"\n" not in self.buffer:
                if not select.select([self.proc.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                    break
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise NativeBridgeError('native_bridge_exited', stage=stage)
                self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                value = json.loads(line)
                if value.get("id") != self.seq:
                    continue
                if "error" in value:
                    raise rpc_failure(value['error'], stage)
                return value["result"]
        raise NativeBridgeError('native_rpc_timeout', stage=stage)

    def call(self, tool, arguments):
        result = self.rpc("tools/call", {"name":tool, "arguments":arguments, "_meta":{
            "openai/threadId":self.context["threadId"],
            "openai/turnId":self.context["turnId"],
            "openai/toolCallId":"usage-guard-"+str(uuid.uuid4())}})
        if result.get("isError"):
            # Tool-returned prose can contain private thread/account data.
            raise NativeBridgeError('native_tool_rejected', stage=tool)
        texts = [x["text"] for x in result.get("content", []) if x.get("type")=="text"]
        if not texts:
            raise RuntimeError("Native app tool returned no text")
        return json.loads(texts[0])

    def send(self, target, prompt):
        reply = self.call("send_message_to_thread", {"threadId":target, "prompt":prompt})
        if reply.get("threadId") != target:
            raise RuntimeError("Native send did not confirm intended target")

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()


def verify_bridge(bridge, foreman, parent):
    """Read-only identity/route check; never creates a turn or sends a marker."""
    for recipient, thread_id, expected_title in [('foreman', foreman, 'Codex Foreman'), ('parent', parent, None)]:
        try:
            value = bridge.call('read_thread', {'threadId': thread_id, 'turnLimit': 1,
                                               'includeOutputs': False, 'maxOutputCharsPerItem': 1})
        except NativeBridgeError as error:
            error.recipient = recipient
            raise
        thread = value.get('thread', {})
        if thread.get('id') != thread_id or (expected_title and thread.get('title') != expected_title):
            raise NativeBridgeError('native_route_identity_mismatch', stage='read_thread', recipient=recipient)
    return True
