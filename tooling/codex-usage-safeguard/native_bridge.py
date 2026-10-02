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
                    raise RuntimeError("Native bridge exited")
                self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                value = json.loads(line)
                if value.get("id") != self.seq:
                    continue
                if "error" in value:
                    raise RuntimeError("Native MCP request failed")
                return value["result"]
        raise TimeoutError("Native MCP request timed out")

    def call(self, tool, arguments):
        result = self.rpc("tools/call", {"name":tool, "arguments":arguments, "_meta":{
            "openai/threadId":self.context["threadId"],
            "openai/turnId":self.context["turnId"],
            "openai/toolCallId":"usage-guard-"+str(uuid.uuid4())}})
        if result.get("isError"):
            raise RuntimeError("Native app tool rejected request")
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
    for thread_id, expected_title in [(foreman, 'Codex Foreman'), (parent, None)]:
        value = bridge.call('read_thread', {'threadId': thread_id, 'turnLimit': 1,
                                           'includeOutputs': False, 'maxOutputCharsPerItem': 1})
        thread = value.get('thread', {})
        if thread.get('id') != thread_id or (expected_title and thread.get('title') != expected_title):
            raise RuntimeError('native_route_identity_mismatch')
    return True
