import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.session_bridge import (  # noqa: E402
    BridgeError,
    consume_gui_envelope,
    HookAdapter,
    JsonBridgeClient,
    SessionBridge,
    UnixBridgeServer,
    bridge_process_arguments,
    clean_transcript,
    read_bridge_locator,
    sanitized_harness_environment,
    start_bridge_process,
    write_gui_envelope,
    write_bridge_locator,
)
from hindsight_memory_control_plane.adapters import FakeAdapter  # noqa: E402
from hindsight_memory_control_plane.broker import (  # noqa: E402
    Broker,
    DEFAULT_SESSION_TTL_SECONDS,
    MAX_SESSION_TTL_SECONDS,
)
from hindsight_memory_control_plane.server import (  # noqa: E402
    JsonRpcClient,
    UnixJsonRpcServer,
)


class FakeBrokerClient:
    def __init__(self):
        self.calls = []
        self.exchange_count = 0

    def session_exchange(self, handle):
        self.exchange_count += 1
        self.calls.append(("session_exchange", handle))
        return {
            "disposition": "exchanged",
            "payload": {
                "capability": "private-capability",
                "expires_at": 99999999999,
            },
        }

    def __getattr__(self, method):
        def call(capability, **arguments):
            self.calls.append((method, capability, arguments))
            if method == "recall":
                payload = {"memories": [{"text": "Prefer current evidence."}]}
            elif method == "mental_model_fetch":
                payload = {"models": [{"id": "operator-profile", "text": "Be direct."}]}
            elif method == "reflect":
                payload = {"text": "A bounded reflection."}
            elif method == "session_status":
                payload = {"queued": 0, "writes": {"pending": [], "completed": []}}
            elif method == "session_close":
                payload = {"undrained": 0, "write_drain": "drained"}
            else:
                payload = {"watermark": [arguments["request"]["epoch"], arguments["request"]["checkpoint"]]}
            disposition = (
                "queued"
                if method in {"transcript_checkpoint", "retain_outcome"}
                else "ok"
            )
            return {"disposition": disposition, "payload": payload, "diagnostic": None}

        return call


class SessionBridgeTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeBrokerClient()
        self.bridge = SessionBridge(
            broker_client=self.client,
            handle="a" * 64,
            session_id="session-1",
            harness_id="codex",
        )

    def test_exchange_is_lazy_and_private_material_never_enters_responses(self):
        self.assertEqual(self.client.exchange_count, 0)
        response = self.bridge.dispatch(
            {"event_id": "prompt-1", "operation": "recall", "input": {"query": "deployment"}}
        )
        self.assertEqual(self.client.exchange_count, 1)
        self.assertEqual(response["disposition"], "ok")
        serialized = json.dumps(response)
        self.assertNotIn("private-capability", serialized)
        self.assertNotIn("a" * 64, serialized)
        self.assertNotIn("bank", serialized.lower())
        self.assertNotIn("endpoint", serialized.lower())

    def test_event_replay_reuses_response_without_advancing_sequence(self):
        request = {
            "event_id": "prompt-1",
            "operation": "recall",
            "input": {"query": "deployment"},
        }
        first = self.bridge.dispatch(request)
        second = self.bridge.dispatch(request)
        self.assertEqual(second, first)
        recall_calls = [call for call in self.client.calls if call[0] == "recall"]
        self.assertEqual(len(recall_calls), 1)
        self.assertEqual(recall_calls[0][2]["sequence"], 1)

    def test_event_identity_conflict_and_route_fields_fail_closed(self):
        self.bridge.dispatch(
            {"event_id": "same", "operation": "recall", "input": {"query": "one"}}
        )
        with self.assertRaisesRegex(BridgeError, "EVENT_CONFLICT"):
            self.bridge.dispatch(
                {"event_id": "same", "operation": "recall", "input": {"query": "two"}}
            )
        for forbidden in (
            "bank_id", "bankId", "apiKey", "endpoint", "url", "token", "route", "tags"
        ):
            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(
                BridgeError, "INPUT_INVALID"
            ):
                self.bridge.dispatch(
                    {
                        "event_id": f"bad-{forbidden}",
                        "operation": "recall",
                        "input": {"query": "safe", forbidden: "forged"},
                    }
                )

        with self.assertRaisesRegex(BridgeError, "INPUT_INVALID"):
            self.bridge.dispatch(
                {
                    "event_id": "noncanonical-number",
                    "operation": "recall",
                    "input": {"query": float("nan")},
                }
            )

    def test_bridge_owns_stable_epoch_documents_and_checkpoint_watermarks(self):
        first = self.bridge.dispatch(
            {
                "event_id": "checkpoint-1",
                "operation": "checkpoint",
                "input": {"content": "User: first\nAssistant: answer", "seal_epoch": True},
            }
        )
        second = self.bridge.dispatch(
            {
                "event_id": "checkpoint-2",
                "operation": "checkpoint",
                "input": {"content": "User: second\nAssistant: answer"},
            }
        )
        self.assertEqual(first["payload"]["watermark"], [0, 1])
        self.assertEqual(second["payload"]["watermark"], [1, 1])
        checkpoint_calls = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        document_ids = [
            call[2]["request"]["document_id"] for call in checkpoint_calls
        ]
        self.assertEqual(len(document_ids), 2)
        self.assertRegex(
            document_ids[0],
            r"^session-[0-9a-f]{32}:epoch:0:segment:0$",
        )
        self.assertRegex(
            document_ids[1],
            r"^session-[0-9a-f]{32}:epoch:1:segment:0$",
        )

    def test_sealed_transcript_prefix_is_not_repeated_in_the_next_epoch(self):
        first_epoch = "User: first\n\nAssistant: answer"
        self.bridge.dispatch(
            {
                "event_id": "precompact-1",
                "operation": "checkpoint",
                "input": {"content": first_epoch, "seal_epoch": True},
            }
        )
        self.bridge.dispatch(
            {
                "event_id": "checkpoint-2",
                "operation": "checkpoint",
                "input": {
                    "content": first_epoch + "\n\nUser: second\n\nAssistant: next"
                },
            }
        )
        checkpoint_calls = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        self.assertEqual(
            checkpoint_calls[-1][2]["request"]["content"],
            "User: second\n\nAssistant: next",
        )

    def test_unchanged_sealed_transcript_does_not_create_an_empty_document(self):
        content = "User: first"
        self.bridge.dispatch(
            {
                "event_id": "precompact-1",
                "operation": "checkpoint",
                "input": {"content": content, "seal_epoch": True},
            }
        )
        response = self.bridge.dispatch(
            {
                "event_id": "checkpoint-2",
                "operation": "checkpoint",
                "input": {"content": content},
            }
        )
        checkpoint_calls = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        self.assertEqual(len(checkpoint_calls), 1)
        self.assertEqual(response["payload"]["write_state"], "unchanged")

    def test_close_reports_undrained_state_without_exposing_capability(self):
        close_request = {"event_id": "close-1", "operation": "close", "input": {}}
        response = self.bridge.dispatch(close_request)
        self.assertEqual(response["payload"]["write_drain"], "drained")
        self.assertTrue(self.bridge.closed)
        self.assertEqual(self.bridge.dispatch(close_request), response)
        with self.assertRaisesRegex(BridgeError, "BRIDGE_CLOSED"):
            self.bridge.dispatch(
                {"event_id": "late", "operation": "status", "input": {}}
            )

    def test_transport_failure_stays_pending_until_same_event_is_retried(self):
        class FailingOnceBroker(FakeBrokerClient):
            def __init__(self):
                super().__init__()
                self.failed = False

            def recall(self, _capability, **_arguments):
                if not self.failed:
                    self.failed = True
                    raise OSError("broker transport unavailable")
                return {
                    "disposition": "ok",
                    "payload": {"memories": []},
                    "diagnostic": None,
                }

        broker = FailingOnceBroker()
        bridge = SessionBridge(
            broker_client=broker,
            handle="b" * 64,
            session_id="failure-isolated",
            harness_id="codex",
        )
        request = {
            "event_id": "prompt-1",
            "operation": "recall",
            "input": {"query": "first"},
        }
        with self.assertRaisesRegex(BridgeError, "BRIDGE_UPSTREAM_UNAVAILABLE"):
            bridge.dispatch(request)
        self.assertEqual(bridge.sequence, 0)
        later = bridge.dispatch(
            {"event_id": "status-2", "operation": "status", "input": {}}
        )
        self.assertEqual(later["disposition"], "ok")
        self.assertEqual(bridge.sequence, 2)
        recovered = bridge.dispatch(request)
        self.assertEqual(recovered["disposition"], "ok")
        self.assertEqual(bridge.dispatch(request), recovered)

    def test_ambiguous_close_failure_does_not_close_or_advance_bridge(self):
        original = self.client.session_close
        self.client.session_close = Mock(side_effect=OSError("ambiguous"))
        request = {"event_id": "close-1", "operation": "close", "input": {}}
        with self.assertRaisesRegex(BridgeError, "BRIDGE_UPSTREAM_UNAVAILABLE"):
            self.bridge.dispatch(request)
        self.assertFalse(self.bridge.closed)
        self.assertEqual(self.bridge.sequence, 0)
        self.client.session_close = original
        response = self.bridge.dispatch(request)
        self.assertEqual(response["disposition"], "ok")
        self.assertTrue(self.bridge.closed)

    def test_complete_epoch_is_segmented_without_dropping_prefix_or_suffix(self):
        content = (
            "User: first\n\n" + ("Assistant: useful λ\n\n" * 9000)
        ).rstrip()
        response = self.bridge.dispatch(
            {
                "event_id": "large-checkpoint",
                "operation": "checkpoint",
                "input": {"content": content},
            }
        )
        calls = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        self.assertGreater(len(calls), 1)
        self.assertEqual(
            "".join(call[2]["request"]["content"] for call in calls),
            content,
        )
        self.assertEqual(response["payload"]["segments"], len(calls))
        self.assertEqual(self.bridge.sequence, len(calls))

    def test_unchanged_and_incremental_checkpoints_use_only_changed_segments(self):
        content = ("User: first\n\n" + ("Assistant: useful\n\n" * 6000)).rstrip()
        def request(event, value):
            return {
                "event_id": event,
                "operation": "checkpoint",
                "input": {"content": value},
            }
        first = self.bridge.dispatch(request("checkpoint-1", content))
        first_count = first["payload"]["segments"]
        first_sequence = self.bridge.sequence
        unchanged = self.bridge.dispatch(request("checkpoint-2", content))
        self.assertEqual(unchanged["payload"]["write_state"], "unchanged")
        self.assertEqual(self.bridge.sequence, first_sequence)
        grown = self.bridge.dispatch(request("checkpoint-3", content + " more"))
        self.assertEqual(grown["payload"]["segments_written"], 1)
        self.assertEqual(self.bridge.sequence, first_sequence + 1)
        calls = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        self.assertEqual(len(calls), first_count + 1)

    def test_transcript_shrink_rolls_to_a_new_epoch_without_stale_segments(self):
        first = "User: old\n\nAssistant: completed"
        replacement = "User: new\n\nAssistant: restarted"
        self.bridge.dispatch(
            {
                "event_id": "checkpoint-1",
                "operation": "checkpoint",
                "input": {"content": first},
            }
        )
        self.bridge.dispatch(
            {
                "event_id": "checkpoint-2",
                "operation": "checkpoint",
                "input": {"content": replacement},
            }
        )
        calls = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        self.assertTrue(calls[0][2]["request"]["document_id"].endswith("epoch:0:segment:0"))
        self.assertTrue(calls[1][2]["request"]["document_id"].endswith("epoch:1:segment:0"))
        self.assertEqual(calls[1][2]["request"]["content"], replacement)

    def test_unchanged_checkpoint_events_preserve_a_close_reserve(self):
        content = "Assistant: stable"
        for index in range(1024):
            self.bridge.dispatch(
                {
                    "event_id": f"checkpoint-{index}",
                    "operation": "checkpoint",
                    "input": {"content": content},
                }
            )
        response = self.bridge.dispatch(
            {"event_id": "reserved-close", "operation": "close", "input": {}}
        )
        self.assertEqual(response["disposition"], "ok")
        self.assertTrue(self.bridge.closed)

    def test_middle_segment_response_is_validated_and_replayed_with_same_identity(self):
        class MalformedMiddleBroker(FakeBrokerClient):
            def __init__(self):
                super().__init__()
                self.malformed = True

            def transcript_checkpoint(self, capability, **arguments):
                self.calls.append(("transcript_checkpoint", capability, arguments))
                if arguments["request"]["document_id"].endswith("segment:1") and self.malformed:
                    self.malformed = False
                    return {
                        "disposition": "queued",
                        "payload": {"watermark": [999, 999]},
                        "diagnostic": None,
                    }
                return {
                    "disposition": "queued",
                    "payload": {
                        "watermark": [
                            arguments["request"]["epoch"],
                            arguments["request"]["checkpoint"],
                        ]
                    },
                    "diagnostic": None,
                }

        broker = MalformedMiddleBroker()
        bridge = SessionBridge(
            broker_client=broker,
            handle="e" * 64,
            session_id="middle-response",
            harness_id="codex",
        )
        content = ("Assistant: segment\n\n" * 6000).rstrip()
        request = {
            "event_id": "checkpoint",
            "operation": "checkpoint",
            "input": {"content": content},
        }
        with self.assertRaisesRegex(BridgeError, "BROKER_RESPONSE_INVALID"):
            bridge.dispatch(request)
        response = bridge.dispatch(request)
        self.assertEqual(response["disposition"], "queued")
        actions = [
            call[2]["action_id"]
            for call in broker.calls
            if call[0] == "transcript_checkpoint"
        ]
        self.assertEqual(actions[0], actions[2])

    def test_malformed_post_admission_response_keeps_replay_identity(self):
        class MalformedOnceBroker(FakeBrokerClient):
            def __init__(self):
                super().__init__()
                self.first = True

            def recall(self, capability, **arguments):
                self.calls.append(("recall", capability, arguments))
                if self.first:
                    self.first = False
                    return {"malformed": True}
                return {
                    "disposition": "ok",
                    "payload": {"memories": []},
                    "diagnostic": None,
                }

        broker = MalformedOnceBroker()
        bridge = SessionBridge(
            broker_client=broker,
            handle="f" * 64,
            session_id="post-admission",
            harness_id="codex",
        )
        request = {
            "event_id": "recall",
            "operation": "recall",
            "input": {"query": "safe"},
        }
        with self.assertRaisesRegex(BridgeError, "BROKER_RESPONSE_INVALID"):
            bridge.dispatch(request)
        bridge.dispatch(request)
        recall_calls = [call for call in broker.calls if call[0] == "recall"]
        self.assertEqual(
            [call[2]["action_id"] for call in recall_calls],
            [recall_calls[0][2]["action_id"]] * 2,
        )


class TranscriptCleaningTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_cleaner_accepts_native_shapes_and_strips_tools_and_memory_loops(self):
        transcript = self.root / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                json.dumps(value)
                for value in (
                    {"role": "user", "content": "Investigate the failure."},
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "<hindsight_memories>old memory</hindsight_memories>Verified the fix."},
                                {"type": "tool_use", "name": "shell", "input": {"secret": "never retain"}},
                            ],
                        },
                    },
                    {"type": "tool_result", "content": "raw output"},
                )
            ),
            encoding="utf-8",
        )
        cleaned = clean_transcript(transcript)
        self.assertIn("User: Investigate the failure.", cleaned)
        self.assertIn("Assistant: Verified the fix.", cleaned)
        self.assertNotIn("old memory", cleaned)
        self.assertNotIn("tool", cleaned.lower())
        self.assertNotIn("secret", cleaned)

    def test_cleaner_accepts_exact_codex_response_item_shape_without_event_duplicates(self):
        transcript = self.root / "codex.jsonl"
        transcript.write_text(
            "\n".join(
                json.dumps(value)
                for value in (
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Native prompt"}],
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "Native prompt"},
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "Native answer"},
                                {"type": "tool_call", "arguments": "must not retain"},
                            ],
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "developer",
                            "content": [{"type": "input_text", "text": "hidden policy"}],
                        },
                    },
                )
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            clean_transcript(transcript),
            "User: Native prompt\n\nAssistant: Native answer",
        )

    def test_cleaner_accepts_exact_claude_and_cursor_message_shapes(self):
        transcript = self.root / "native.jsonl"
        transcript.write_text(
            "\n".join(
                json.dumps(value)
                for value in (
                    {"type": "user", "message": {"role": "user", "content": "Claude prompt"}},
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "private reasoning"},
                                {"type": "text", "text": "Claude answer"},
                                {"type": "tool_use", "input": "private tool input"},
                            ],
                        },
                    },
                    {"role": "user", "content": [{"type": "text", "text": "Cursor prompt"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "Cursor answer"}]},
                )
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            clean_transcript(transcript),
            "User: Claude prompt\n\nAssistant: Claude answer\n\n"
            "User: Cursor prompt\n\nAssistant: Cursor answer",
        )

    def test_cleaner_rejects_writable_or_hard_linked_transcripts(self):
        transcript = self.root / "transcript.jsonl"
        transcript.write_text('{"role":"user","content":"unsafe"}\n', encoding="utf-8")
        transcript.chmod(0o622)
        with self.assertRaisesRegex(BridgeError, "TRANSCRIPT_INVALID"):
            clean_transcript(transcript)

    def test_cleaner_removes_native_controller_records_before_bounding(self):
        transcript = self.root / "long-codex.jsonl"
        records = []
        for index in range(20):
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "<environment_context>" + ("x" * 10000) + "</environment_context>",
                            }
                        ],
                    },
                }
            )
        records.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "# AGENTS.md instructions\n" + ("policy " * 1000),
                        }
                    ],
                },
            }
        )
        for index in range(180):
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": f"answer-{index}: " + ("useful " * 120),
                            }
                        ],
                    },
                }
            )
        transcript.write_text(
            "\n".join(json.dumps(record) for record in records),
            encoding="utf-8",
        )
        cleaned = clean_transcript(transcript)
        self.assertNotIn("environment_context", cleaned)
        self.assertNotIn("AGENTS.md", cleaned)
        self.assertIn("answer-0:", cleaned)
        self.assertIn("answer-179:", cleaned)
        self.assertGreater(len(cleaned.encode("utf-8")), 128 * 1024)
        transcript.chmod(0o600)
        os.link(transcript, self.root / "alias.jsonl")
        with self.assertRaisesRegex(BridgeError, "TRANSCRIPT_INVALID"):
            clean_transcript(transcript)

    def test_cleaner_filters_combined_plugins_and_agent_policy_before_bounding(self):
        transcript = self.root / "combined.jsonl"
        synthetic = (
            "<recommended_plugins>" + ("plugin " * 30000)
            + "</recommended_plugins>\n# AGENTS.md instructions\n"
            + ("secret policy " * 30000)
        )
        transcript.write_text(
            json.dumps({"role": "user", "content": synthetic})
            + "\n"
            + json.dumps({"role": "assistant", "content": "useful answer"}),
            encoding="utf-8",
        )
        self.assertEqual(clean_transcript(transcript), "Assistant: useful answer")

    def test_cleaner_streams_source_larger_than_cleaned_output_limit(self):
        transcript = self.root / "large-source.jsonl"
        synthetic = json.dumps(
            {
                "type": "tool_result",
                "content": "x" * 10000,
            }
        )
        with transcript.open("w", encoding="utf-8") as destination:
            for _ in range(900):
                destination.write(synthetic + "\n")
            destination.write(json.dumps({"role": "assistant", "content": "kept"}))
        self.assertGreater(transcript.stat().st_size, 8 * 1024 * 1024)
        self.assertEqual(clean_transcript(transcript), "Assistant: kept")

    def test_four_megabyte_checkpoint_is_segmented_completely(self):
        content = "Assistant: useful λ\n\n" * 180000
        bridge = SessionBridge(
            broker_client=FakeBrokerClient(),
            handle="9" * 64,
            session_id="performance",
            harness_id="codex",
        )
        response = bridge.dispatch(
            {
                "event_id": "large",
                "operation": "checkpoint",
                "input": {"content": content},
            }
        )
        self.assertGreater(response["payload"]["segments"], 1)
        self.assertEqual(bridge.sequence, response["payload"]["segments"])


class UnixBridgeTransportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.socket_path = self.root / "session.sock"
        self.broker = FakeBrokerClient()
        self.bridge = SessionBridge(
            broker_client=self.broker,
            handle="c" * 64,
            session_id="session-1",
            harness_id="claude-code",
        )
        self.server = UnixBridgeServer(self.socket_path, self.bridge)
        self.server.start()

    def tearDown(self):
        self.server.close()
        self.temporary.cleanup()

    def test_socket_is_user_only_and_close_drives_server_shutdown(self):
        self.assertEqual(stat.S_IMODE(self.socket_path.stat().st_mode), 0o600)
        client = JsonBridgeClient(self.socket_path)
        recalled = client.call_native(
            "claude-code",
            "recall",
            {"session_id": "session-1", "prompt": "verify"},
        )
        self.assertIn("hookSpecificOutput", recalled)
        closed = client.call_native(
            "claude-code", "close", {"session_id": "session-1"}
        )
        self.assertEqual(closed["payload"]["write_drain"], "drained")
        deadline = time.monotonic() + 1
        while self.server.running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.server.running)

    def test_socket_is_private_at_bind_before_the_chmod_safeguard(self):
        self.server.close()
        observed_modes = []

        def observe_before_chmod(path, _mode):
            observed_modes.append(stat.S_IMODE(Path(path).lstat().st_mode))

        bridge = SessionBridge(
            broker_client=FakeBrokerClient(),
            handle="7" * 64,
            session_id="private-at-bind",
            harness_id="codex",
        )
        server = UnixBridgeServer(self.socket_path, bridge)
        with patch(
            "hindsight_memory_control_plane.session_bridge.os.chmod",
            side_effect=observe_before_chmod,
        ):
            server.start()
        try:
            self.assertEqual(observed_modes, [0o600])
        finally:
            server.close()

    def test_transport_reports_content_free_bridge_errors(self):
        response = JsonBridgeClient(self.socket_path).call_raw(
            {"event_id": "bad", "operation": "recall", "input": {"bank_id": "forged"}}
        )
        self.assertEqual(response, {"ok": False, "error": "INPUT_INVALID"})

    def test_transport_rejects_native_harness_or_session_mismatch(self):
        client = JsonBridgeClient(self.socket_path)
        for harness_id, payload in (
            ("codex", {"session_id": "session-1", "prompt": "verify"}),
            ("claude-code", {"session_id": "session-2", "prompt": "verify"}),
        ):
            with self.subTest(harness_id=harness_id, payload=payload):
                with self.assertRaisesRegex(BridgeError, "INPUT_INVALID|SESSION_MISMATCH"):
                    client.call_native(harness_id, "recall", payload)

    def test_unsafe_socket_parent_is_rejected_before_binding(self):
        self.server.close()
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o755)
        with self.assertRaisesRegex(BridgeError, "SOCKET_DIRECTORY_UNSAFE"):
            UnixBridgeServer(unsafe / "bridge.sock", self.bridge).start()

    def test_existing_socket_is_never_unlinked_by_a_competing_bridge(self):
        competing = SessionBridge(
            broker_client=FakeBrokerClient(),
            handle="d" * 64,
            session_id="session-2",
            harness_id="codex",
        )
        with self.assertRaisesRegex(BridgeError, "SOCKET_PATH_INVALID"):
            UnixBridgeServer(self.socket_path, competing).start()
        self.assertTrue(self.socket_path.exists())

    def test_launcher_arguments_and_harness_environment_contain_only_locator(self):
        arguments = bridge_process_arguments(
            executable="/opt/hindsight-memory",
            state_dir="/private/state",
            bridge_socket=self.socket_path,
            broker_socket="/private/broker.sock",
            handle_fd=7,
            session_id="session-1",
            harness_id="codex",
        )
        serialized = json.dumps(arguments)
        self.assertNotIn("private-capability", serialized)
        self.assertNotIn("a" * 64, serialized)
        environment = sanitized_harness_environment(
            {
                "PATH": "/usr/bin",
                "HINDSIGHT_MEMORY_SESSION_HANDLE": "handle",
                "HINDSIGHT_MEMORY_SESSION_CAPABILITY": "capability",
                "HINDSIGHT_MEMORY_CONTROL_CAPABILITY": "control",
                "HINDSIGHT_DATA_PLANE_TOKEN": "token",
                "HINDSIGHT_UNRELATED_SETTING": "must-not-reach-harness",
                "HINDSIGHT_API_URL": "http://forged.invalid",
                "HINDSIGHT_BANK_ID": "forged-bank",
                "HINDSIGHT_MEMORY_BROKER_SOCKET": "/forged/broker.sock",
            },
            self.socket_path,
        )
        self.assertEqual(environment["HINDSIGHT_MEMORY_BRIDGE_LOCATOR"], str(self.socket_path))
        self.assertEqual(
            {key for key in environment if "HINDSIGHT" in key.upper()},
            {"HINDSIGHT_MEMORY_BRIDGE_LOCATOR"},
        )

    def test_bridge_lifetime_supports_a_bounded_full_workday_session(self):
        arguments = bridge_process_arguments(
            executable="/opt/hindsight-memory",
            state_dir="/private/state",
            bridge_socket=self.root / "workday.sock",
            broker_socket="/private/broker.sock",
            handle_fd=7,
            session_id="session-1",
            harness_id="codex",
            lifetime_seconds=DEFAULT_SESSION_TTL_SECONDS,
        )
        self.assertEqual(arguments[-1], str(float(DEFAULT_SESSION_TTL_SECONDS)))
        with self.assertRaisesRegex(BridgeError, "TIMEOUT_INVALID"):
            bridge_process_arguments(
                executable="/opt/hindsight-memory",
                state_dir="/private/state",
                bridge_socket=self.root / "too-long.sock",
                broker_socket="/private/broker.sock",
                handle_fd=7,
                session_id="session-1",
                harness_id="codex",
                lifetime_seconds=MAX_SESSION_TTL_SECONDS + 1,
            )

    def test_bridge_startup_failure_closes_both_handle_pipe_descriptors(self):
        read_descriptor, write_descriptor = os.pipe()
        with (
            patch(
                "hindsight_memory_control_plane.session_bridge.os.pipe",
                return_value=(read_descriptor, write_descriptor),
            ),
            patch(
                "hindsight_memory_control_plane.session_bridge.subprocess.Popen",
                side_effect=OSError("start failed"),
            ),
            self.assertRaisesRegex(OSError, "start failed"),
        ):
            start_bridge_process(
                executable="/opt/hindsight-memory",
                state_dir="/private/state",
                bridge_socket=self.root / "failed.sock",
                broker_socket="/private/broker.sock",
                handle="a" * 64,
                session_id="session-1",
                harness_id="codex",
            )
        for descriptor in (read_descriptor, write_descriptor):
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_handle_pipe_write_failure_reaps_the_started_bridge(self):
        process = Mock()
        process.wait.return_value = 0
        with (
            patch(
                "hindsight_memory_control_plane.session_bridge.subprocess.Popen",
                return_value=process,
            ),
            patch(
                "hindsight_memory_control_plane.session_bridge.os.write",
                side_effect=BrokenPipeError("bridge exited"),
            ),
            self.assertRaises(BrokenPipeError),
        ):
            start_bridge_process(
                executable="/opt/hindsight-memory",
                state_dir="/private/state",
                bridge_socket=self.root / "pipe-failed.sock",
                broker_socket="/private/broker.sock",
                handle="a" * 64,
                session_id="session-1",
                harness_id="codex",
            )
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)

    def test_gui_locator_artifact_is_private_contains_no_envelope_and_is_session_bound(self):
        locator_dir = self.root / "locators"
        locator_dir.mkdir(mode=0o700)
        path = write_bridge_locator(
            locator_dir,
            session_id="gui-session",
            harness_id="cursor",
            socket_path=self.socket_path,
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        serialized = path.read_text(encoding="utf-8")
        self.assertNotIn("handle", serialized.lower())
        self.assertNotIn("capability", serialized.lower())
        self.assertEqual(
            read_bridge_locator(
                locator_dir, session_id="gui-session", harness_id="cursor"
            ),
            self.socket_path,
        )
        with self.assertRaisesRegex(BridgeError, "LOCATOR_MISMATCH"):
            read_bridge_locator(
                locator_dir, session_id="gui-session", harness_id="codex"
            )

    def test_gui_locator_never_replaces_an_existing_session_binding(self):
        locator_dir = self.root / "locators"
        locator_dir.mkdir(mode=0o700)
        first = write_bridge_locator(
            locator_dir,
            session_id="gui-session",
            harness_id="cursor",
            socket_path=self.socket_path,
        )
        original = first.read_bytes()
        with self.assertRaisesRegex(BridgeError, "LOCATOR_EXISTS"):
            write_bridge_locator(
                locator_dir,
                session_id="gui-session",
                harness_id="cursor",
                socket_path=self.root / "competitor.sock",
            )
        self.assertEqual(first.read_bytes(), original)

    def test_gui_locator_handles_legal_short_writes_and_reads(self):
        locator_dir = self.root / "short-io-locators"
        locator_dir.mkdir(mode=0o700)
        real_write = os.write
        real_read = os.read

        def short_write(descriptor, payload):
            return real_write(descriptor, payload[: max(1, len(payload) // 3)])

        with patch(
            "hindsight_memory_control_plane.session_bridge.os.write",
            side_effect=short_write,
        ):
            path = write_bridge_locator(
                locator_dir,
                session_id="short-session",
                harness_id="cursor",
                socket_path=self.socket_path,
            )

        def short_read(descriptor, size):
            return real_read(descriptor, min(size, 7))

        with patch(
            "hindsight_memory_control_plane.session_bridge.os.read",
            side_effect=short_read,
        ):
            resolved = read_bridge_locator(
                locator_dir,
                session_id="short-session",
                harness_id="cursor",
            )
        self.assertEqual(resolved, self.socket_path)
        self.assertTrue(path.is_file())

    def test_gui_envelope_is_private_and_consumed_exactly_once(self):
        locator_dir = self.root / "envelopes"
        locator_dir.mkdir(mode=0o700)
        path = write_gui_envelope(
            locator_dir,
            session_id="one-use",
            harness_id="cursor",
            handle="a" * 64,
            state_dir=self.root,
            broker_socket=self.root / "broker.sock",
            bridge_dir=self.root,
            expires_at=time.time() + 30,
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with consume_gui_envelope(
            locator_dir,
            session_id="one-use",
            harness_id="cursor",
        ) as consumed:
            self.assertEqual(consumed["handle"], "a" * 64)
            with self.assertRaisesRegex(BridgeError, "ENVELOPE_UNAVAILABLE"):
                with consume_gui_envelope(
                    locator_dir,
                    session_id="one-use",
                    harness_id="cursor",
                ):
                    pass
        self.assertFalse(path.exists())
        with self.assertRaisesRegex(BridgeError, "ENVELOPE_UNAVAILABLE"):
            with consume_gui_envelope(
                locator_dir,
                session_id="one-use",
                harness_id="cursor",
            ):
                pass

    def test_gui_envelope_claim_recovers_after_consumer_process_crash(self):
        locator_dir = self.root / "crashed-envelope"
        locator_dir.mkdir(mode=0o700)
        path = write_gui_envelope(
            locator_dir,
            session_id="crashed-consumer",
            harness_id="cursor",
            handle="b" * 64,
            state_dir=self.root,
            broker_socket=self.root / "broker.sock",
            bridge_dir=self.root,
            expires_at=time.time() + 30,
        )
        script = """
import os
import sys
from hindsight_memory_control_plane.session_bridge import consume_gui_envelope

with consume_gui_envelope(
    sys.argv[1], session_id="crashed-consumer", harness_id="cursor"
):
    os._exit(17)
"""
        crashed = subprocess.run(
            [sys.executable, "-c", script, str(locator_dir)],
            env={**os.environ, "PYTHONPATH": str(LIB)},
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        self.assertFalse(path.exists())
        self.assertEqual(
            len(list(locator_dir.glob(".*.consuming.*"))), 1
        )

        with consume_gui_envelope(
            locator_dir,
            session_id="crashed-consumer",
            harness_id="cursor",
        ) as recovered:
            self.assertEqual(recovered["handle"], "b" * 64)
        self.assertEqual(list(locator_dir.glob(".*.consuming.*")), [])

    def test_stable_harness_cli_translates_native_payload_without_private_inputs(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "claude-code",
                "recall",
                "--bridge",
                str(self.socket_path),
            ],
            input=json.dumps(
                {"session_id": "session-1", "prompt": "How should I verify this?"}
            ),
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertIn(
            "Prefer current evidence.",
            output["hookSpecificOutput"]["additionalContext"],
        )

    def test_native_close_prints_final_checkpoint_and_undrained_diagnostics(self):
        transcript = self.root / "missing-transcript.jsonl"

        def undrained(_capability, **_arguments):
            return {
                "disposition": "ok",
                "payload": {"undrained": 2, "write_drain": "pending"},
                "diagnostic": None,
            }

        self.broker.session_close = undrained
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "claude-code",
                "close",
                "--bridge",
                str(self.socket_path),
            ],
            input=json.dumps(
                {
                    "session_id": "session-1",
                    "transcript_path": str(transcript),
                }
            ),
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("final checkpoint: TRANSCRIPT_UNAVAILABLE", result.stderr)
        self.assertIn("undrained writes: count=2, state='pending'", result.stderr)

    def test_native_close_isolates_locator_cleanup_failure(self):
        unsafe_locators = self.root / "unsafe-locators"
        unsafe_locators.mkdir(mode=0o755)
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "claude-code",
                "close",
                "--bridge",
                str(self.socket_path),
                "--locator-dir",
                str(unsafe_locators),
            ],
            input=json.dumps({"session_id": "session-1"}),
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOCATOR_DIRECTORY_UNSAFE", result.stderr)
        self.assertNotIn("private-capability", result.stdout + result.stderr)

    def test_ambient_hook_cli_fails_open_before_locator_resolution(self):
        for event in ("recall", "reflect"):
            with self.subTest(event=event):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "bin/hindsight-memory"),
                        "--state-dir",
                        str(self.root),
                        "harness",
                        "codex",
                        event,
                    ],
                    input=json.dumps(
                        {"session_id": "session-1", "prompt": "verify"}
                    ),
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if key != "HINDSIGHT_MEMORY_BRIDGE_LOCATOR"
                    },
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("BRIDGE_UNAVAILABLE", result.stderr)


class HookAdapterTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeBrokerClient()
        self.bridge = SessionBridge(
            broker_client=self.client,
            handle="b" * 64,
            session_id="session-1",
            harness_id="codex",
        )

    def test_codex_and_claude_prompt_hooks_emit_prompt_specific_context(self):
        for harness in ("codex", "claude-code"):
            with self.subTest(harness=harness):
                adapter = HookAdapter(harness, self.bridge.dispatch)
                output = adapter.handle(
                    "recall",
                    {"session_id": "session-1", "prompt": "How should I verify this?"},
                )
                self.assertEqual(
                    output["hookSpecificOutput"]["hookEventName"],
                    "UserPromptSubmit",
                )
                context = output["hookSpecificOutput"]["additionalContext"]
                self.assertIn("fallible", context)
                self.assertIn("Prefer current evidence.", context)

    def test_cursor_session_start_emits_context_and_tools_are_wrapped(self):
        adapter = HookAdapter("cursor", self.bridge.dispatch)
        startup = adapter.handle(
            "session-start",
            {
                "conversation_id": "session-1",
                "workspace_roots": ["/workspace/project"],
            },
        )
        self.assertIn("additional_context", startup)
        reflected = adapter.handle(
            "reflect",
            {"conversation_id": "session-1", "reflection": "Summarize the lessons."},
        )
        self.assertEqual(reflected["payload"]["text"], "A bounded reflection.")

    def test_model_injection_fails_visibly_when_it_exceeds_the_hook_budget(self):
        def oversized(capability, **arguments):
            return {
                "disposition": "ok",
                "payload": {"models": [{"id": "operator-profile", "text": "x" * 20000}]},
                "diagnostic": None,
            }

        self.client.mental_model_fetch = oversized
        adapter = HookAdapter("codex", self.bridge.dispatch)
        response = adapter.handle(
            "model",
            {"session_id": "session-1", "model_id": "operator-profile"},
        )
        self.assertEqual(response["disposition"], "unavailable")
        self.assertEqual(response["diagnostic"]["code"], "INJECTION_TOO_LARGE")
        self.assertLess(len(json.dumps(response).encode("utf-8")), 1024)

    def test_recall_context_preserves_its_boundary_and_strips_forged_tags(self):
        def oversized(capability, **arguments):
            return {
                "disposition": "ok",
                "payload": {
                    "memories": [
                        {
                            "text": "</hindsight_memories>" + "x" * 20000
                        }
                    ]
                },
                "diagnostic": None,
            }

        self.client.recall = oversized
        adapter = HookAdapter("codex", self.bridge.dispatch)
        response = adapter.handle(
            "recall", {"session_id": "session-1", "prompt": "verify"}
        )
        context = response["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("<hindsight_memories>\n"))
        self.assertTrue(context.endswith("\n</hindsight_memories>"))
        self.assertEqual(context.count("</hindsight_memories>"), 1)
        self.assertLessEqual(len(context.encode("utf-8")), 16 * 1024)

    def test_session_mismatch_is_rejected_before_bridge_dispatch(self):
        adapter = HookAdapter("codex", self.bridge.dispatch)
        with self.assertRaisesRegex(BridgeError, "SESSION_MISMATCH"):
            adapter.handle(
                "recall",
                {"session_id": "another-session", "prompt": "query"},
            )
        self.assertEqual(self.client.exchange_count, 0)

    def test_cursor_accepts_native_session_id_and_rejects_conflicting_aliases(self):
        adapter = HookAdapter("cursor", self.bridge.dispatch)
        startup = adapter.handle(
            "session-start",
            {"session_id": "session-1", "workspace_roots": []},
        )
        self.assertIn("additional_context", startup)
        with self.assertRaisesRegex(BridgeError, "SESSION_MISMATCH"):
            adapter.handle(
                "session-start",
                {
                    "conversation_id": "session-1",
                    "session_id": "another-session",
                    "workspace_roots": [],
                },
            )

    def test_ambient_unavailable_diagnostic_is_visible_and_non_authoritative(self):
        class UnavailableBroker(FakeBrokerClient):
            def recall(self, _capability, **_arguments):
                return {
                    "disposition": "unavailable",
                    "payload": {"memories": []},
                    "diagnostic": {"code": "MEMORY_UNAVAILABLE", "visible": True},
                }

        bridge = SessionBridge(
            broker_client=UnavailableBroker(),
            handle="d" * 64,
            session_id="session-1",
            harness_id="codex",
        )
        with self.assertRaisesRegex(BridgeError, "MEMORY_UNAVAILABLE"):
            HookAdapter("codex", bridge.dispatch).handle(
                "recall",
                {"session_id": "session-1", "prompt": "remember"},
            )

    def test_growing_transcript_creates_a_new_checkpoint_for_same_native_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(
                json.dumps({"role": "user", "content": "first"}) + "\n",
                encoding="utf-8",
            )
            adapter = HookAdapter("codex", self.bridge.dispatch)
            payload = {"session_id": "session-1", "transcript_path": str(transcript)}
            adapter.handle("checkpoint", payload)
            transcript.write_text(
                transcript.read_text(encoding="utf-8")
                + json.dumps({"role": "assistant", "content": "second"})
                + "\n",
                encoding="utf-8",
            )
            adapter.handle("checkpoint", payload)
        checkpoints = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(
            [call[2]["request"]["checkpoint"] for call in checkpoints], [1, 2]
        )

    def test_close_revokes_session_even_when_final_checkpoint_is_unavailable(self):
        adapter = HookAdapter("codex", self.bridge.dispatch)
        response = adapter.handle(
            "close",
            {
                "session_id": "session-1",
                "transcript_path": "/missing/transcript.jsonl",
            },
        )
        self.assertTrue(self.bridge.closed)
        self.assertEqual(response["disposition"], "ok")
        self.assertEqual(
            response["checkpoint"]["diagnostic"]["code"],
            "TRANSCRIPT_UNAVAILABLE",
        )
        self.assertEqual(
            len([call for call in self.client.calls if call[0] == "session_close"]),
            1,
        )

    def test_clean_stop_derives_and_retains_controller_owned_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(
                json.dumps({"role": "user", "content": "Fix it"})
                + "\n"
                + json.dumps(
                    {"role": "assistant", "content": "Fixed and verified."}
                ),
                encoding="utf-8",
            )
            response = HookAdapter("codex", self.bridge.dispatch).handle(
                "checkpoint",
                {
                    "session_id": "session-1",
                    "transcript_path": str(transcript),
                },
            )
        self.assertEqual(response, {})
        outcome_calls = [
            call for call in self.client.calls if call[0] == "retain_outcome"
        ]
        self.assertEqual(len(outcome_calls), 1)
        self.assertEqual(
            outcome_calls[0][2]["request"]["outcome"],
            "Fixed and verified.",
        )

    def test_close_does_not_infer_an_outcome_from_a_nonterminal_assistant(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(
                json.dumps({"role": "assistant", "content": "Earlier answer."})
                + "\n"
                + json.dumps({"role": "user", "content": "One more question"}),
                encoding="utf-8",
            )
            response = HookAdapter("codex", self.bridge.dispatch).handle(
                "close",
                {
                    "session_id": "session-1",
                    "transcript_path": str(transcript),
                },
            )
        self.assertEqual(response["disposition"], "ok")
        self.assertEqual(
            [call for call in self.client.calls if call[0] == "retain_outcome"],
            [],
        )

    def test_growing_transcript_over_one_hundred_megabytes_accepts_append(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            irrelevant = (
                json.dumps({"type": "tool_result", "content": "x" * 100_000})
                + "\n"
            ).encode("utf-8")
            with transcript.open("wb") as destination:
                for _ in range(1050):
                    destination.write(irrelevant)
            adapter = HookAdapter("codex", self.bridge.dispatch)
            payload = {"session_id": "session-1", "transcript_path": str(transcript)}
            adapter.handle("checkpoint", payload)
            with transcript.open("ab") as destination:
                destination.write(
                    (
                        json.dumps(
                            {"role": "assistant", "content": "New bounded answer."}
                        )
                        + "\n"
                    ).encode("utf-8")
                )
            adapter.handle("checkpoint", payload)
        checkpoints = [
            call for call in self.client.calls if call[0] == "transcript_checkpoint"
        ]
        self.assertEqual(checkpoints[-1][2]["request"]["content"], "Assistant: New bounded answer.")

    def test_close_reconciles_a_transient_pending_checkpoint_before_revocation(self):
        class FailingCheckpointOnce(FakeBrokerClient):
            def __init__(self):
                super().__init__()
                self.failed = False

            def transcript_checkpoint(self, capability, **arguments):
                if not self.failed:
                    self.failed = True
                    raise OSError("ambiguous checkpoint")
                return super().__getattr__("transcript_checkpoint")(
                    capability, **arguments
                )

        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(
                json.dumps({"role": "assistant", "content": "Done."}),
                encoding="utf-8",
            )
            broker = FailingCheckpointOnce()
            bridge = SessionBridge(
                broker_client=broker,
                handle="8" * 64,
                session_id="recover-close",
                harness_id="codex",
            )
            result = HookAdapter("codex", bridge.dispatch).handle(
                "close",
                {
                    "session_id": "recover-close",
                    "transcript_path": str(transcript),
                },
            )
        self.assertTrue(bridge.closed)
        self.assertEqual(result["disposition"], "ok")
        self.assertEqual(
            len([call for call in broker.calls if call[0] == "session_close"]),
            1,
        )

    def test_close_revokes_session_when_a_pending_checkpoint_stays_unavailable(self):
        class FailingCheckpoint(FakeBrokerClient):
            def transcript_checkpoint(self, _capability, **_arguments):
                raise OSError("checkpoint remains unavailable")

        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(
                json.dumps({"role": "assistant", "content": "Done."}),
                encoding="utf-8",
            )
            broker = FailingCheckpoint()
            bridge = SessionBridge(
                broker_client=broker,
                handle="9" * 64,
                session_id="forced-close",
                harness_id="codex",
            )
            result = HookAdapter("codex", bridge.dispatch).handle(
                "close",
                {
                    "session_id": "forced-close",
                    "transcript_path": str(transcript),
                },
            )
        self.assertTrue(bridge.closed)
        self.assertEqual(
            result["pending_write"]["diagnostic"]["code"],
            "BRIDGE_UPSTREAM_UNAVAILABLE",
        )
        self.assertEqual(
            len([call for call in broker.calls if call[0] == "session_close"]),
            1,
        )


class HarnessLauncherIntegrationTest(unittest.TestCase):
    def setUp(self):
        temporary_parent = Path(tempfile.gettempdir())
        if len(os.fsencode(temporary_parent)) > 32:
            short_system_temp = Path(temporary_parent.anchor) / "tmp"
            if short_system_temp.is_dir():
                temporary_parent = short_system_temp
        self.temporary = tempfile.TemporaryDirectory(
            prefix="h", dir=temporary_parent
        )
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.bridge_temporary = tempfile.TemporaryDirectory(
            prefix="b", dir=temporary_parent
        )
        self.state = self.root / "s"
        self.broker_socket = self.root / "b.sock"
        self.bridge_dir = Path(self.bridge_temporary.name)
        os.chmod(self.bridge_dir, 0o700)
        self.policy_digest = "a" * 64
        self.artifact_digest = "b" * 64
        bank = {"profile_id": "core", "bank_id": "engineering"}

        def authorize(control, requested, ttl):
            if control != "control" or requested.get("route") not in {"codex", "cursor"}:
                return {}
            return {
                **requested,
                "harness_id": requested["route"],
                "home_bank": bank,
                "trust_class": "local",
                "policy_digest": self.policy_digest,
                "artifact_digest": self.artifact_digest,
                "methods": [
                    "recall",
                    "mental_model_fetch",
                    "transcript_checkpoint",
                    "retain_outcome",
                    "reflect",
                    "session_status",
                    "session_close",
                ],
            }

        self.broker = Broker(
            state_dir=self.state,
            signing_key=b"s" * 32,
            routes={route: {"bank": bank, "adapter": FakeAdapter(endpoint={
                "profile_id": "core",
                "scheme": "http",
                "host": "127.0.0.1",
                "port": 7979,
                "tenant": "default",
            })} for route in ("codex", "cursor")},
            policy_digest=self.policy_digest,
            artifact_digest=self.artifact_digest,
            mint_authorizer=authorize,
        )
        self.server = UnixJsonRpcServer(self.broker_socket, self.broker)
        self.server.start()

    def tearDown(self):
        self.server.close()
        self.broker.shutdown()
        self.bridge_temporary.cleanup()
        self.temporary.cleanup()

    def test_cli_launcher_gives_harness_only_locator_and_closes_session(self):
        child = (
            "import json,os; print(json.dumps({"
            "'locator': bool(os.environ.get('HINDSIGHT_MEMORY_BRIDGE_LOCATOR')) ,"
            "'handle': 'HINDSIGHT_MEMORY_SESSION_HANDLE' in os.environ,"
            "'capability': 'HINDSIGHT_MEMORY_SESSION_CAPABILITY' in os.environ,"
            "'control': 'HINDSIGHT_MEMORY_CONTROL_CAPABILITY' in os.environ,"
            "'custom_control': 'CONTROL_CAPABILITY' in os.environ}))"
        )
        environment = {
            **os.environ,
            "CONTROL_CAPABILITY": "control",
            "HINDSIGHT_MEMORY_SESSION_HANDLE": "forbidden-handle",
            "HINDSIGHT_MEMORY_SESSION_CAPABILITY": "forbidden-capability",
            "HINDSIGHT_MEMORY_CONTROL_CAPABILITY": "forbidden-control",
        }
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "codex",
                "launch",
                "--broker-socket",
                str(self.broker_socket),
                "--control-capability-env",
                "CONTROL_CAPABILITY",
                "--session-id",
                "launch-session",
                "--companion-id",
                "cli-1",
                "--route",
                "codex",
                "--bridge-dir",
                str(self.bridge_dir),
                "--ttl-seconds",
                "30",
                "--command",
                sys.executable,
                "-c",
                child,
            ],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(
            observed,
            {
                "locator": True,
                "handle": False,
                "capability": False,
                "control": False,
                "custom_control": False,
            },
        )
        work = json.loads((self.state / "durable_work.json").read_text())
        self.assertTrue(
            work["sessions"]["launch-session"]["closed"],
            {"stderr": result.stderr, "work": work},
        )

    def test_real_broker_accepts_complete_segmented_epoch_and_next_sequence(self):
        client = JsonRpcClient(self.broker_socket)
        session_id = "s" * 128
        minted = client.session_mint(
            "control",
            {
                "session_id": session_id,
                "companion_id": "integration",
                "route": "codex",
            },
            ttl_seconds=30,
        )
        bridge = SessionBridge(
            broker_client=client,
            handle=minted["payload"]["handle"],
            session_id=session_id,
            harness_id="codex",
        )
        content = (
            "User: first\n\n" + ("Assistant: useful λ\n\n" * 9000)
        ).rstrip()
        checkpoint = bridge.dispatch(
            {
                "event_id": "large-checkpoint",
                "operation": "checkpoint",
                "input": {"content": content},
            }
        )
        segment_count = checkpoint["payload"]["segments"]
        self.assertGreater(segment_count, 1)
        closed = bridge.dispatch(
            {
                "event_id": "close-after-segments",
                "operation": "close",
                "input": {},
            }
        )
        self.assertEqual(closed["disposition"], "closed")
        self.assertEqual(bridge.sequence, segment_count + 1)
        work = json.loads((self.state / "durable_work.json").read_text())
        retained = [
            entry
            for entry in [*work["queue"], *work["completed"].values()]
            if entry["session_id"] == session_id
        ]
        self.assertEqual(len(retained), segment_count)

    def test_launcher_failure_after_mint_exchanges_and_closes_the_handle(self):
        invalid_bridge_dir = self.root / "not-a-directory"
        invalid_bridge_dir.write_text("blocked", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "codex",
                "launch",
                "--broker-socket",
                str(self.broker_socket),
                "--control-capability-env",
                "CONTROL_CAPABILITY",
                "--session-id",
                "failed-launch-session",
                "--companion-id",
                "cli-failed",
                "--route",
                "codex",
                "--bridge-dir",
                str(invalid_bridge_dir),
                "--ttl-seconds",
                "30",
                "--command",
                sys.executable,
                "-c",
                "pass",
            ],
            env={**os.environ, "CONTROL_CAPABILITY": "control"},
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        work = json.loads((self.state / "durable_work.json").read_text())
        self.assertTrue(work["sessions"]["failed-launch-session"]["closed"])
        self.assertEqual(list((self.state / "handles").glob("*.json")), [])

    def test_gui_stage_keeps_handle_in_bridge_until_first_native_hook(self):
        locator_dir = self.root / "locators"
        locator_dir.mkdir(mode=0o700)
        environment = {
            **os.environ,
            "TEST_HINDSIGHT_CONTROL_CAPABILITY": "control",
        }
        staged = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "cursor",
                "stage-gui",
                "--broker-socket",
                str(self.broker_socket),
                "--control-capability-env",
                "TEST_HINDSIGHT_CONTROL_CAPABILITY",
                "--session-id",
                "gui-session",
                "--companion-id",
                "gui-1",
                "--route",
                "cursor",
                "--bridge-dir",
                str(self.bridge_dir),
                "--locator-dir",
                str(locator_dir),
                "--ttl-seconds",
                "30",
            ],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)
        before = json.loads((self.state / "durable_work.json").read_text())
        self.assertNotIn("gui-session", before["sessions"])
        with self.assertRaisesRegex(BridgeError, "LOCATOR_UNAVAILABLE"):
            read_bridge_locator(
                locator_dir, session_id="gui-session", harness_id="cursor"
            )
        write_bridge_locator(
            locator_dir,
            session_id="gui-session",
            harness_id="cursor",
            socket_path=self.bridge_dir / "stale.sock",
        )
        hook = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "cursor",
                "session-start",
                "--locator-dir",
                str(locator_dir),
            ],
            input=json.dumps(
                {
                    "conversation_id": "gui-session",
                    "workspace_roots": ["/workspace/project"],
                }
            ),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertIn("additional_context", json.loads(hook.stdout))
        locator = read_bridge_locator(
            locator_dir, session_id="gui-session", harness_id="cursor"
        )
        after = json.loads((self.state / "durable_work.json").read_text())
        self.assertIn("gui-session", after["sessions"])
        JsonBridgeClient(locator).call_native(
            "cursor", "close", {"conversation_id": "gui-session"}
        )

    def test_duplicate_gui_stage_does_not_mint_or_poison_original_envelope(self):
        locator_dir = self.root / "conflicting-locators"
        locator_dir.mkdir(mode=0o700)
        command = [
            sys.executable,
            str(ROOT / "bin/hindsight-memory"),
            "--state-dir",
            str(self.root),
            "harness",
            "cursor",
            "stage-gui",
            "--broker-socket",
            str(self.broker_socket),
            "--control-capability-env",
            "TEST_HINDSIGHT_CONTROL_CAPABILITY",
            "--session-id",
            "conflicting-gui-session",
            "--companion-id",
            "gui-conflict",
            "--route",
            "cursor",
            "--bridge-dir",
            str(self.bridge_dir),
            "--locator-dir",
            str(locator_dir),
            "--ttl-seconds",
            "30",
        ]
        environment = {**os.environ, "TEST_HINDSIGHT_CONTROL_CAPABILITY": "control"}
        first = subprocess.run(
            command,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        handles_before = list((self.state / "handles").glob("*.json"))
        self.assertEqual(len(handles_before), 1)
        duplicate = subprocess.run(
            command,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(list((self.state / "handles").glob("*.json")), handles_before)
        hook = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "cursor",
                "session-start",
                "--locator-dir",
                str(locator_dir),
            ],
            input=json.dumps(
                {"conversation_id": "conflicting-gui-session", "workspace_roots": []}
            ),
            env={**os.environ, "TEST_HINDSIGHT_CONTROL_CAPABILITY": "control"},
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        locator = read_bridge_locator(
            locator_dir, session_id="conflicting-gui-session", harness_id="cursor"
        )
        self.assertEqual(list((self.state / "handles").glob("*.json")), [])
        JsonBridgeClient(locator).call_native(
            "cursor", "close", {"conversation_id": "conflicting-gui-session"}
        )

    def test_gui_stage_replaces_only_an_expired_abandoned_envelope(self):
        locator_dir = self.root / "expired-envelope"
        locator_dir.mkdir(mode=0o700)
        envelope = write_gui_envelope(
            locator_dir,
            session_id="retry-gui-session",
            harness_id="cursor",
            handle="e" * 64,
            state_dir=self.root,
            broker_socket=self.broker_socket,
            bridge_dir=self.bridge_dir,
            expires_at=time.time() + 30,
        )
        record = json.loads(envelope.read_text(encoding="utf-8"))
        record["expires_at"] = 0
        envelope.write_text(json.dumps(record), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.root),
                "harness",
                "cursor",
                "stage-gui",
                "--broker-socket",
                str(self.broker_socket),
                "--control-capability-env",
                "TEST_HINDSIGHT_CONTROL_CAPABILITY",
                "--session-id",
                "retry-gui-session",
                "--companion-id",
                "gui-retry",
                "--route",
                "cursor",
                "--bridge-dir",
                str(self.bridge_dir),
                "--locator-dir",
                str(locator_dir),
                "--ttl-seconds",
                "30",
            ],
            env={**os.environ, "TEST_HINDSIGHT_CONTROL_CAPABILITY": "control"},
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        replacement = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertNotEqual(replacement["handle"], "e" * 64)


if __name__ == "__main__":
    unittest.main()
