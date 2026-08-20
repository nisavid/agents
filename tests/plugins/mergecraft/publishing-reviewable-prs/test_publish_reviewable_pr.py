from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[4]
SCRIPTS = REPOSITORY / "plugins/mergecraft/skills/publishing-reviewable-prs/scripts"
sys.path.insert(0, str(SCRIPTS))
STATE = importlib.import_module("reviewable_pr_state")
RECEIPTS = importlib.import_module("publication_receipts")


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CREATE = load("create_reviewable_pr", "create_reviewable_pr.py")
UPDATE = load("update_reviewable_pr", "update_reviewable_pr.py")
AUDIT = load("audit_reviewable_pr", "audit_reviewable_pr.py")
REQUIRED_REVIEW = importlib.import_module("required_review")


def transition_review(mode: str, candidate_sha256: str, observation=None):
    return REQUIRED_REVIEW._make_publication_review(
        mode,
        candidate_sha256,
        observation,
        transition_validated=True,
    )


class RequiredReviewTests(unittest.TestCase):
    @staticmethod
    def canonical(value: object) -> bytes:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def test_publication_review_constructor_is_private(self) -> None:
        with self.assertRaisesRegex(
            REQUIRED_REVIEW.PublicationError, "canonical review factory"
        ):
            REQUIRED_REVIEW.PublicationReview("not-required", "a" * 64, None)

    def candidate(self, *, mode: str = "required"):
        candidate_identity = {
            "kind": "mergecraft-publication-candidate-v1",
            "value": "sha256:" + "a" * 64,
            "content_sha256": "a" * 64,
        }
        return REQUIRED_REVIEW.PublicationCandidate(
            value={
                "publication_profile": {
                    "review_mode": mode,
                    "selected_specialists": ["security"],
                }
            },
            content_sha256="a" * 64,
            candidate_identity=candidate_identity,
            review_input_identity={
                "kind": "mergecraft-review-input-v1",
                "value": "sha256:" + "b" * 64,
                "content_sha256": "b" * 64,
            },
            requirements_identity={
                "kind": "mergecraft-required-publication-review-profile-v2",
                "value": "sha256:" + "c" * 64,
                "content_sha256": "c" * 64,
            },
            body_source_raw=b"body",
            published_body="body",
        )

    def envelope(self, candidate=None) -> bytes:
        candidate = candidate or self.candidate()
        executions = {}
        for index, role in enumerate(
            (
                "critic-intent",
                "critic-runtime",
                "critic-structure",
                "specialist-security",
            )
        ):
            executions[f"execution-{index}"] = {
                "execution_id": f"execution-{index}",
                "role": role,
                "candidate": candidate.candidate_identity,
                "target": {
                    "product_family": "codex",
                    "surface": "chatgpt-codex",
                    "executor": "codex",
                    "version": "2026.08.13",
                },
                "topology": {
                    "relationship": "child",
                    "ownership": "leader-owned",
                    "transport": "native-tool",
                },
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "user_authority": None,
                "return_contract": "tricritical-raw-report-v1",
                "scope": {
                    "kind": "review-scope-v1",
                    "value": role,
                    "content_sha256": "a" * 64,
                },
                "request": {
                    "kind": "review-request-v1",
                    "value": role,
                    "content_sha256": "b" * 64,
                },
                "authority": {
                    "access": "read-only",
                    "subdelegation": False,
                    "external_action": False,
                    "evidence": {
                        "kind": "authority-evidence-v1",
                        "value": role,
                        "content_sha256": f"{index + 1:x}" * 64,
                    },
                },
                "isolation": {
                    "session": f"session-{index}",
                    "context": f"context-{index}",
                    "enforceable": True,
                },
                "assurance": {
                    "target": "product-attested",
                    "model": "product-attested",
                    "topology": "product-attested",
                    "authority": "product-attested",
                    "execution_result": "product-attested",
                    "evidence": {
                        "kind": "product-attestation-v1",
                        "value": role,
                        "content_sha256": hashlib.sha256(
                            f"assurance-{role}".encode()
                        ).hexdigest(),
                    },
                },
                "assurance_minimum": {
                    "target": "product-attested",
                    "model": "product-attested",
                    "topology": "product-attested",
                    "authority": "product-attested",
                    "execution_result": "product-attested",
                },
                "verification_contract": "review-verification-v1",
                "stop_contract": "review-stop-v1",
                "usable": True,
                "returned": {
                    "kind": "tricritical-raw-report-v1",
                    "value": role,
                    "content_sha256": f"{index + 5:x}" * 64,
                },
                "verification": {
                    "kind": "review-verification-v1",
                    "value": role,
                    "content_sha256": f"{index + 9:x}" * 64,
                },
                "stop": {
                    "kind": "review-stop-v1",
                    "value": role,
                    "content_sha256": f"{index + 12:x}" * 64,
                },
            }
        projection = {
            "schema_version": 1,
            "contract": "tricritical-terminal-review-projection-v2",
            "evidence_contract": "tricritical-terminal-review-evidence-v2",
            "manifest_sha256": "d" * 64,
            "subject": {
                "candidate": candidate.candidate_identity,
                "review_input": candidate.review_input_identity,
                "requirements": candidate.requirements_identity,
            },
            "review_profile": {
                "contract": "tricritical-review-profile-v1",
                "execution_mode": "independent",
                "required_axes": ["intent", "runtime", "structure"],
                "selected_specialists": ["security"],
            },
            "final_dispatch": {
                "contract": "rolecasting-dispatch-projection-v2",
                "evidence_contract": "rolecasting-dispatch-evidence-v2",
                "executions": executions,
            },
            "terminal": {
                "state": "clean",
                "owner": "none",
                "limitations": [],
                "missing_executions": [],
                "unresolved_actionable_findings": 0,
                "verification": {
                    "status": "passed",
                    "candidate": candidate.candidate_identity,
                    "evidence": {
                        "kind": "verification-evidence-v1",
                        "value": "passed",
                        "content_sha256": "e" * 64,
                    },
                    "unchanged": True,
                },
            },
        }
        projection["content_sha256"] = hashlib.sha256(
            self.canonical(projection)
        ).hexdigest()
        envelope = {
            "contract": "task-witness-launch-envelope-v1",
            "anchor": {
                "contract": "task-witness-complete-anchor-v1",
                "generation": "sha256-" + "1" * 64,
                "active_record_sha256": "2" * 64,
                "runtime_contract": "task-witness-runtime-v1",
                "interpreter": {
                    "executable": "/usr/bin/python3",
                    "implementation": "cpython",
                    "version": {"major": 3, "minor": 13, "micro": 7},
                },
                "public_release": {
                    "repository": "nisavid/agents",
                    "revision": "3" * 40,
                },
                "runtime_implementation_sha256": "4" * 64,
                "trust_context_sha256": "5" * 64,
                "bundle_sha256": "6" * 64,
                "historical": False,
            },
            "witness": {
                "contract": "task-witness-canonical-projection-v2",
                "bundle_sha256": "6" * 64,
                "producer": {
                    "producer_id": "tricritical-review-loop-v2",
                    "contract": "tricritical-terminal-review-evidence-v2",
                    "implementation_sha256": "7" * 64,
                    "validator_id": (
                        "tricritical-terminal-review-evidence-validator-v2"
                    ),
                    "validator_contract": ("tricritical-terminal-review-evidence-v2"),
                    "validator_implementation_sha256": "8" * 64,
                },
                "validator": {
                    "validator_id": (
                        "tricritical-terminal-review-evidence-validator-v2"
                    ),
                    "contract": "tricritical-terminal-review-evidence-v2",
                    "implementation_sha256": "8" * 64,
                },
                "projection": projection,
                "trust_context_sha256": "5" * 64,
                "historical": False,
            },
        }
        return self.canonical(envelope) + b"\n"

    def mutate_envelope(self, mutation) -> bytes:
        envelope = json.loads(self.envelope())
        mutation(envelope)
        projection = envelope["witness"]["projection"]
        projection.pop("content_sha256")
        projection["content_sha256"] = hashlib.sha256(
            self.canonical(projection)
        ).hexdigest()
        return self.canonical(envelope) + b"\n"

    def test_required_review_returns_transitively_bound_redacted_observation(
        self,
    ) -> None:
        candidate = self.candidate()
        envelope = self.envelope(candidate)
        with mock.patch.object(
            REQUIRED_REVIEW, "_invoke_task_witness", return_value=envelope
        ):
            review = REQUIRED_REVIEW.validate_required_review(
                review_mode="required",
                review_bundle_root=Path("/absolute/review-bundle"),
                candidate=candidate,
            )

        self.assertEqual(review.mode, "required")
        self.assertEqual(review.publication_candidate_sha256, "a" * 64)
        self.assertIsNotNone(review.observation)
        assert review.observation is not None
        self.assertEqual(
            review.observation.launch_envelope_sha256,
            hashlib.sha256(envelope).hexdigest(),
        )
        self.assertEqual(review.observation.tricritical_manifest_sha256, "d" * 64)
        self.assertNotIn("subject", review.as_json()["observation"])

    def test_not_required_is_explicit_and_cannot_claim_evidence(self) -> None:
        candidate = self.candidate(mode="not-required")
        review = REQUIRED_REVIEW.validate_required_review(
            review_mode="not-required",
            review_bundle_root=None,
            candidate=candidate,
        )
        self.assertEqual(review.as_json()["observation"], None)
        with self.assertRaisesRegex(
            REQUIRED_REVIEW.PublicationError, "cannot claim evidence"
        ):
            REQUIRED_REVIEW.validate_required_review(
                review_mode="not-required",
                review_bundle_root=Path("/evidence"),
                candidate=candidate,
            )

    def test_required_review_fails_closed_when_canonical_front_door_is_unavailable(
        self,
    ) -> None:
        with (
            mock.patch.object(
                REQUIRED_REVIEW,
                "_invoke_task_witness",
                side_effect=REQUIRED_REVIEW.PublicationError("front door unavailable"),
            ),
            self.assertRaisesRegex(
                REQUIRED_REVIEW.PublicationError, "front door unavailable"
            ),
        ):
            REQUIRED_REVIEW.validate_required_review(
                review_mode="required",
                review_bundle_root=Path("/absolute/review-bundle"),
                candidate=self.candidate(),
            )

    def test_required_review_rejects_historical_or_changed_under_lease(self) -> None:
        candidate = self.candidate()
        historical = json.loads(self.envelope(candidate))
        historical["anchor"]["historical"] = True
        historical["witness"]["historical"] = True
        with (
            mock.patch.object(
                REQUIRED_REVIEW,
                "_invoke_task_witness",
                return_value=self.canonical(historical) + b"\n",
            ),
            self.assertRaisesRegex(
                REQUIRED_REVIEW.PublicationError, "current complete anchor"
            ),
        ):
            REQUIRED_REVIEW.validate_required_review(
                review_mode="required",
                review_bundle_root=Path("/absolute/review-bundle"),
                candidate=candidate,
            )

        valid = self.envelope(candidate)
        with mock.patch.object(
            REQUIRED_REVIEW, "_invoke_task_witness", return_value=valid
        ):
            first = REQUIRED_REVIEW.validate_required_review(
                review_mode="required",
                review_bundle_root=Path("/absolute/review-bundle"),
                candidate=candidate,
            )
        assert first.observation is not None
        changed = json.loads(valid)
        changed["anchor"]["active_record_sha256"] = "9" * 64
        with (
            mock.patch.object(
                REQUIRED_REVIEW,
                "_invoke_task_witness",
                return_value=self.canonical(changed) + b"\n",
            ),
            self.assertRaisesRegex(
                REQUIRED_REVIEW.PublicationError,
                "changed under publication lease",
            ),
        ):
            REQUIRED_REVIEW.validate_required_review(
                review_mode="required",
                review_bundle_root=Path("/absolute/review-bundle"),
                candidate=candidate,
                expected_observation=first.observation,
            )

    def test_body_binding_distinguishes_raw_source_from_published_utf8(self) -> None:
        raw = b"line one\r\nline two\r\n"
        normalized = "line one\nline two\n"
        binding = REQUIRED_REVIEW.body_source_binding(
            kind="body",
            raw=raw,
            published=normalized,
            render_contract="literal-utf8-v1",
        )

        self.assertEqual(binding["raw_byte_length"], len(raw))
        self.assertEqual(binding["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(
            binding["published_sha256"],
            hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(binding["raw_sha256"], binding["published_sha256"])

    def test_required_review_rejects_weaker_or_changed_dispatch(self) -> None:
        def change_execution(field: str, value: object):
            def mutate(envelope: dict[str, object]) -> None:
                executions = envelope["witness"]["projection"]["final_dispatch"][
                    "executions"
                ]
                executions["execution-0"][field] = value

            return mutate

        def missing_specialist(envelope: dict[str, object]) -> None:
            del envelope["witness"]["projection"]["final_dispatch"]["executions"][
                "execution-3"
            ]

        def extra_specialist(envelope: dict[str, object]) -> None:
            executions = envelope["witness"]["projection"]["final_dispatch"][
                "executions"
            ]
            extra = json.loads(json.dumps(executions["execution-3"]))
            extra["execution_id"] = "execution-extra"
            extra["role"] = "specialist-performance"
            extra["isolation"]["session"] = "session-extra"
            extra["isolation"]["context"] = "context-extra"
            executions["execution-extra"] = extra

        def reused_isolation(envelope: dict[str, object]) -> None:
            executions = envelope["witness"]["projection"]["final_dispatch"][
                "executions"
            ]
            executions["execution-1"]["isolation"]["session"] = "session-0"

        def weaker_authority(envelope: dict[str, object]) -> None:
            executions = envelope["witness"]["projection"]["final_dispatch"][
                "executions"
            ]
            executions["execution-0"]["authority"]["external_action"] = True

        def change_nested(section: str, field: str, value: object):
            def mutate(envelope: dict[str, object]) -> None:
                executions = envelope["witness"]["projection"]["final_dispatch"][
                    "executions"
                ]
                executions["execution-0"][section][field] = value

            return mutate

        cases = {
            "model": change_execution("model", "gpt-5.6-terra"),
            "effort": change_execution("reasoning_effort", "medium"),
            "controller-observed-model": change_nested(
                "assurance", "model", "controller-observed"
            ),
            "self-reported-result": change_nested(
                "assurance", "execution_result", "self-reported"
            ),
            "weaker-assurance-minimum": change_nested(
                "assurance_minimum", "execution_result", "controller-observed"
            ),
            "codex-cli-surface": change_nested("target", "surface", "codex-cli-tui"),
            "retired-codex-desktop-surface": change_nested(
                "target", "surface", "codex-desktop"
            ),
            "peer-topology": change_nested("topology", "relationship", "peer"),
            "missing-specialist": missing_specialist,
            "extra-specialist": extra_specialist,
            "reused-isolation": reused_isolation,
            "weaker-authority": weaker_authority,
            "wrong-return-contract": change_execution(
                "return_contract", "worker-report-v1"
            ),
        }
        for label, mutation in cases.items():
            with self.subTest(label=label):
                with (
                    mock.patch.object(
                        REQUIRED_REVIEW,
                        "_invoke_task_witness",
                        return_value=self.mutate_envelope(mutation),
                    ),
                    self.assertRaisesRegex(
                        REQUIRED_REVIEW.PublicationError, "Rolecasting"
                    ),
                ):
                    REQUIRED_REVIEW.validate_required_review(
                        review_mode="required",
                        review_bundle_root=Path("/absolute/review-bundle"),
                        candidate=self.candidate(),
                    )

    def test_supervisor_stops_output_flood_at_hard_cap(self) -> None:
        started = time.monotonic()
        source = (
            f"#!{sys.executable}\nimport os\nwhile True: os.write(1, b'x' * 65536)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                self.authenticated_test_front_door(
                    Path(directory), source
                ) as observation,
                mock.patch.object(REQUIRED_REVIEW, "MAX_STDOUT_BYTES", 1024),
                mock.patch.object(REQUIRED_REVIEW, "TIMEOUT_SECONDS", 2),
                mock.patch.object(REQUIRED_REVIEW, "SETTLEMENT_SECONDS", 3),
                mock.patch.object(REQUIRED_REVIEW, "TERMINATION_GRACE_SECONDS", 0.1),
                self.assertRaisesRegex(
                    REQUIRED_REVIEW.PublicationError, "output exceeded"
                ),
            ):
                REQUIRED_REVIEW._retired_supervised_process(
                    observation, Path("/absolute/review-bundle")
                )
        self.assertLess(time.monotonic() - started, 2)

    def test_supervisor_reaps_detached_descendant_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper_pid = root / "wrapper.pid"
            child_pid = root / "child.pid"
            code = (
                f"#!{sys.executable}\n"
                "import os,time\n"
                f"open({str(wrapper_pid)!r},'w').write(str(os.getpid()))\n"
                "pid=os.fork()\n"
                "if pid == 0:\n"
                " os.setsid()\n"
                f" open({str(child_pid)!r},'w').write(str(os.getpid()))\n"
                " os.close(0); os.close(1); os.close(2)\n"
                " while True: time.sleep(1)\n"
                "while True: time.sleep(1)\n"
            )
            with (
                self.authenticated_test_front_door(root, code) as observation,
                mock.patch.object(REQUIRED_REVIEW, "TIMEOUT_SECONDS", 1.0),
                mock.patch.object(REQUIRED_REVIEW, "SETTLEMENT_SECONDS", 2),
                mock.patch.object(REQUIRED_REVIEW, "TERMINATION_GRACE_SECONDS", 0.1),
                self.assertRaisesRegex(REQUIRED_REVIEW.PublicationError, "timed out"),
            ):
                REQUIRED_REVIEW._retired_supervised_process(
                    observation, Path("/absolute/review-bundle")
                )

            for path in (wrapper_pid, child_pid):
                self.assertTrue(path.is_file())
                pid = int(path.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    self.fail(f"supervised process {pid} remained live")

    def test_supervisor_rejects_an_open_call_shape_before_launch(self) -> None:
        with (
            mock.patch.object(REQUIRED_REVIEW.subprocess, "Popen") as launch,
            self.assertRaisesRegex(
                REQUIRED_REVIEW.PublicationError, "closed internal call shape"
            ),
        ):
            REQUIRED_REVIEW._retired_supervised_process(
                object(), Path("/absolute/review-bundle")
            )
        launch.assert_not_called()

    def test_invoke_requires_canonical_envelope_framing_after_status_zero(
        self,
    ) -> None:
        source = f"#!{sys.executable}\nimport os\nos.write(1, b'{{}}\\n')\n"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.installed_front_door(home, source)
            passwd, effective, group = self.front_door_identity(home)
            with (
                passwd,
                effective,
                group,
                self.assertRaisesRegex(
                    REQUIRED_REVIEW.PublicationError, "envelope framing"
                ),
            ):
                REQUIRED_REVIEW._retired_invoke_task_witness(
                    Path("/absolute/review-bundle")
                )

    def test_invoke_accepts_exact_canonical_envelope_fixture(self) -> None:
        envelope = self.envelope()
        source = f"#!{sys.executable}\nimport os\nos.write(1, {envelope!r})\n"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.installed_front_door(home, source)
            passwd, effective, group = self.front_door_identity(home)
            with passwd, effective, group:
                self.assertEqual(
                    REQUIRED_REVIEW._retired_invoke_task_witness(
                        Path("/absolute/review-bundle")
                    ),
                    envelope,
                )

    def test_supervisor_cleanup_attempts_settlement_and_every_close(self) -> None:
        events: list[str] = []

        class FailingResource:
            def __init__(self, label: str) -> None:
                self.label = label

            def close(self) -> None:
                events.append(self.label)
                raise RuntimeError(self.label)

            def fileno(self) -> int:
                return 1

        class FailingSelector(FailingResource):
            def get_map(self) -> dict[object, object]:
                return {}

            def register(self, *_: object) -> None:
                return None

        process = mock.Mock()
        process.stdout = FailingResource("stdout")
        process.stderr = FailingResource("stderr")
        process.poll.return_value = 0
        process.wait.return_value = 0

        def settle(*_: object, **__: object) -> None:
            events.append("settle")
            raise RuntimeError("settle")

        with tempfile.TemporaryDirectory() as directory:
            with (
                self.authenticated_test_front_door(Path(directory)) as observation,
                mock.patch.object(
                    REQUIRED_REVIEW.selectors,
                    "DefaultSelector",
                    return_value=FailingSelector("selector"),
                ),
                mock.patch.object(
                    REQUIRED_REVIEW.subprocess, "Popen", return_value=process
                ),
                mock.patch.object(REQUIRED_REVIEW.os, "set_blocking"),
                mock.patch.object(REQUIRED_REVIEW, "_settle", side_effect=settle),
                self.assertRaisesRegex(
                    REQUIRED_REVIEW.PublicationError, "process cleanup failed"
                ) as raised,
            ):
                REQUIRED_REVIEW._retired_supervised_process(
                    observation, Path("/absolute/review-bundle")
                )
        self.assertEqual(events, ["settle", "selector", "stdout", "stderr"])
        self.assertEqual(len(raised.exception.__notes__), 4)

    def test_supervisor_cleanup_faults_do_not_mask_primary_error(self) -> None:
        process = mock.Mock()
        process.stdout.fileno.return_value = 1
        process.stderr.fileno.return_value = 2

        with tempfile.TemporaryDirectory() as directory:
            with (
                self.authenticated_test_front_door(Path(directory)) as observation,
                mock.patch.object(
                    REQUIRED_REVIEW.subprocess, "Popen", return_value=process
                ),
                mock.patch.object(
                    REQUIRED_REVIEW.selectors.DefaultSelector,
                    "register",
                    side_effect=KeyboardInterrupt("primary"),
                ),
                mock.patch.object(REQUIRED_REVIEW.os, "set_blocking"),
                mock.patch.object(
                    REQUIRED_REVIEW, "_settle", side_effect=RuntimeError("settle")
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "primary") as raised,
            ):
                REQUIRED_REVIEW._retired_supervised_process(
                    observation, Path("/absolute/review-bundle")
                )
        self.assertTrue(
            any("RuntimeError('settle')" in note for note in raised.exception.__notes__)
        )
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def installed_front_door(
        self, root: Path, source: str = "#!/bin/sh\nexit 0\n"
    ) -> Path:
        root.chmod(0o700)
        current = root
        for component in (".local", "libexec", "task-witness"):
            current /= component
            current.mkdir()
            current.chmod(0o700)
        leaf = current / "task-witness"
        leaf.write_text(source, encoding="utf-8")
        leaf.chmod(0o500)
        return leaf

    @contextmanager
    def authenticated_test_front_door(self, home: Path, source: str | None = None):
        self.installed_front_door(
            home, source if source is not None else "#!/bin/sh\nexit 0\n"
        )
        passwd, effective, group = self.front_door_identity(home)
        with (
            passwd,
            effective,
            group,
            REQUIRED_REVIEW._authenticated_front_door() as value,
        ):
            yield value

    def front_door_identity(self, home: Path):
        effective_uid = os.geteuid()
        return (
            mock.patch.object(
                REQUIRED_REVIEW.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(home)),
            ),
            mock.patch.object(
                REQUIRED_REVIEW.os, "geteuid", return_value=effective_uid
            ),
            mock.patch.object(REQUIRED_REVIEW.os, "getegid", return_value=os.getegid()),
        )

    def test_front_door_uses_effective_not_real_user_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            leaf = self.installed_front_door(home)
            passwd, effective, group = self.front_door_identity(home)
            with (
                passwd as passwd_lookup,
                effective,
                group,
                mock.patch.object(
                    REQUIRED_REVIEW.os, "getuid", return_value=os.geteuid() + 1
                ),
                REQUIRED_REVIEW._authenticated_front_door() as authenticated,
            ):
                self.assertEqual(authenticated.path, leaf)
            passwd_lookup.assert_called_once_with(os.geteuid())

    def test_front_door_rejects_symlink_mode_owner_and_link_drift(self) -> None:
        def symlink_leaf(home: Path, leaf: Path) -> None:
            target = leaf.with_name("target")
            leaf.rename(target)
            leaf.symlink_to(target)

        def wrong_mode(_: Path, leaf: Path) -> None:
            leaf.chmod(0o700)

        def extra_link(_: Path, leaf: Path) -> None:
            os.link(leaf, leaf.with_name("task-witness-hardlink"))

        cases = {
            "symlink": symlink_leaf,
            "mode": wrong_mode,
            "link": extra_link,
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                leaf = self.installed_front_door(home)
                mutate(home, leaf)
                passwd, effective, group = self.front_door_identity(home)
                with (
                    passwd,
                    effective,
                    group,
                    self.assertRaises(REQUIRED_REVIEW.PublicationError),
                ):
                    with REQUIRED_REVIEW._authenticated_front_door():
                        self.fail("untrusted front door was authenticated")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.installed_front_door(home)
            with (
                mock.patch.object(
                    REQUIRED_REVIEW.pwd,
                    "getpwuid",
                    return_value=SimpleNamespace(pw_dir=str(home)),
                ),
                mock.patch.object(
                    REQUIRED_REVIEW.os, "geteuid", return_value=os.geteuid() + 1
                ),
                self.assertRaisesRegex(
                    REQUIRED_REVIEW.PublicationError, "home is not private"
                ),
            ):
                with REQUIRED_REVIEW._authenticated_front_door():
                    self.fail("wrong-owner front door was authenticated")

    def test_front_door_identity_is_rechecked_after_process_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            leaf = self.installed_front_door(home)

            def replace_after_launch(*_: object) -> tuple[int, bytes, bytes]:
                leaf.chmod(0o700)
                return 0, b"{}\n", b""

            passwd, effective, group = self.front_door_identity(home)
            with (
                passwd,
                effective,
                group,
                mock.patch.object(
                    REQUIRED_REVIEW,
                    "_retired_supervised_process",
                    side_effect=replace_after_launch,
                ),
                self.assertRaisesRegex(
                    REQUIRED_REVIEW.PublicationError, "changed during validation"
                ),
            ):
                REQUIRED_REVIEW._retired_invoke_task_witness(
                    Path("/absolute/bundle")
                )

    def test_front_door_identity_is_rechecked_after_failed_process_settlement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            leaf = self.installed_front_door(home)

            def fail_after_launch(*_: object) -> tuple[int, bytes, bytes]:
                leaf.chmod(0o700)
                raise REQUIRED_REVIEW.PublicationError("validation timed out")

            passwd, effective, group = self.front_door_identity(home)
            with (
                passwd,
                effective,
                group,
                mock.patch.object(
                    REQUIRED_REVIEW,
                    "_retired_supervised_process",
                    side_effect=fail_after_launch,
                ),
                self.assertRaisesRegex(
                    REQUIRED_REVIEW.PublicationError, "changed during validation"
                ),
            ):
                REQUIRED_REVIEW._retired_invoke_task_witness(
                    Path("/absolute/bundle")
                )


class ReviewablePrStateTests(unittest.TestCase):
    @staticmethod
    def rest_pr(*, owner: str, number: int) -> dict[str, object]:
        return {
            "number": number,
            "html_url": f"https://github.com/acme/app/pull/{number}",
            "title": f"feat: widget {number}",
            "body": "body",
            "draft": True,
            "state": "open",
            "base": {
                "ref": "main",
                "sha": "a" * 40,
                "repo": {"full_name": "acme/app"},
            },
            "head": {
                "ref": "widget",
                "sha": "b" * 40,
                "repo": {
                    "full_name": f"{owner}/app-fork",
                    "owner": {"login": owner},
                },
            },
        }

    def test_open_pr_api_is_exhaustive_and_owner_qualified(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "[[]]", "")
        with mock.patch.object(STATE, "run_read", return_value=completed) as run_read:
            self.assertEqual(
                STATE.open_prs("acme/app", "main", "fork-owner:widget"), []
            )

        arguments = run_read.call_args.args[0]
        self.assertIn("--paginate", arguments)
        self.assertIn("--slurp", arguments)
        self.assertIn("base=main", arguments)
        self.assertIn("head=fork-owner:widget", arguments)
        self.assertIn("per_page=100", arguments)
        self.assertEqual(
            arguments[arguments.index("--hostname") + 1], STATE.GITHUB_HOST
        )

    def test_github_host_is_pinned_and_ambient_routes_are_scrubbed(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GH_HOST": "attacker.example", "GH_REPO": "attacker/repository"},
        ):
            environment = STATE._environment()

        self.assertNotIn("GH_HOST", environment)
        self.assertNotIn("GH_REPO", environment)
        self.assertEqual(STATE.github_repository("acme/app"), "github.com/acme/app")

    def test_owner_qualified_discovery_finds_target_beyond_thirty_same_name_prs(
        self,
    ) -> None:
        wrong_owner_prs = [
            self.rest_pr(owner="other-owner", number=index) for index in range(1, 36)
        ]
        target = self.rest_pr(owner="fork-owner", number=84)
        completed = subprocess.CompletedProcess(
            [],
            0,
            json.dumps([wrong_owner_prs[:30], wrong_owner_prs[30:] + [target]]),
            "",
        )

        with mock.patch.object(STATE, "run_read", return_value=completed):
            discovered = STATE.open_prs("acme/app", "main", "fork-owner:widget")
        with mock.patch.object(CREATE, "_open_prs", return_value=discovered):
            matches = CREATE._matching_head_prs(
                repository="acme/app",
                base="main",
                head="fork-owner:widget",
                head_owner="fork-owner",
                head_repository="fork-owner/app-fork",
            )

        self.assertEqual(len(discovered), 36)
        self.assertEqual([item["number"] for item in matches], [84])

    def test_open_pr_api_rejects_malformed_nodes(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "[[{}]]", "")

        with mock.patch.object(STATE, "run_read", return_value=completed):
            with self.assertRaisesRegex(STATE.StateReadError, "malformed PR node"):
                STATE.open_prs("acme/app", "main", "fork-owner:widget")

    def test_open_pr_api_rejects_boolean_pr_number(self) -> None:
        malformed = self.rest_pr(owner="fork-owner", number=1)
        malformed["number"] = True
        completed = subprocess.CompletedProcess([], 0, json.dumps([[malformed]]), "")

        with mock.patch.object(STATE, "run_read", return_value=completed):
            with self.assertRaisesRegex(STATE.StateReadError, "malformed PR node"):
                STATE.open_prs("acme/app", "main", "fork-owner:widget")

    def test_stored_pr_rejects_boolean_pr_number(self) -> None:
        completed = subprocess.CompletedProcess([], 0, '{"number": true}', "")

        with mock.patch.object(STATE, "run_read", return_value=completed):
            with self.assertRaisesRegex(STATE.StateReadError, "malformed PR identity"):
                STATE.stored_pr("acme/app", 1)

    def test_read_compatibility_alias_is_not_exposed(self) -> None:
        self.assertFalse(hasattr(STATE, "run"))

    def test_strict_forge_json_rejects_duplicate_keys(self) -> None:
        with self.assertRaisesRegex(STATE.StateReadError, "duplicate JSON key"):
            STATE._json_object('{"number": 1, "number": 2}', "forge")

    def test_strict_forge_json_rejects_non_finite_values(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(
                    STATE.StateReadError, "non-finite JSON value"
                ):
                    STATE._json_object(f'{{"number": {constant}}}', "forge")

    def test_identity_binds_exact_head_repository_full_name(self) -> None:
        expected = STATE.ExpectedIdentity(
            repository="acme/app",
            pr_number=42,
            base="main",
            base_oid="a" * 40,
            head="fork-owner:widget",
            head_oid="b" * 40,
            head_owner="fork-owner",
            head_repository="fork-owner/app-fork",
        )
        stored = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "baseRefName": "main",
            "baseRefOid": "a" * 40,
            "headRefName": "widget",
            "headRefOid": "b" * 40,
            "headRepositoryOwner": {"login": "fork-owner"},
            "headRepository": {"nameWithOwner": "fork-owner/other-fork"},
            "state": "OPEN",
        }
        self.assertFalse(STATE.identity_matches(stored, expected))

    def test_identity_rejects_boolean_stored_pr_number(self) -> None:
        expected = STATE.ExpectedIdentity(
            repository="acme/app",
            pr_number=1,
            base="main",
            base_oid="a" * 40,
            head="fork-owner:widget",
            head_oid="b" * 40,
            head_owner="fork-owner",
            head_repository="fork-owner/app-fork",
        )
        stored = {
            "number": True,
            "url": "https://github.com/acme/app/pull/1",
            "baseRefName": "main",
            "baseRefOid": "a" * 40,
            "headRefName": "widget",
            "headRefOid": "b" * 40,
            "headRepositoryOwner": {"login": "fork-owner"},
            "headRepository": {"nameWithOwner": "fork-owner/app-fork"},
            "state": "OPEN",
        }

        self.assertFalse(STATE.identity_matches(stored, expected))

    def test_identity_inputs_reject_boolean_and_float_pr_numbers(self) -> None:
        for malformed in (True, 1.0):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(STATE.PublicationError, "PR number"):
                    STATE.validate_identity_inputs(
                        repository="acme/app",
                        pr_number=malformed,
                        base="main",
                        base_oid="a" * 40,
                        head="fork-owner:widget",
                        head_oid="b" * 40,
                        head_owner="fork-owner",
                        head_repository="fork-owner/app-fork",
                    )

    def test_read_and_possible_mutation_timeouts_are_distinct(self) -> None:
        timeout = subprocess.TimeoutExpired(["gh"], 1)
        with mock.patch.object(subprocess, "run", side_effect=timeout):
            with self.assertRaises(STATE.StateReadError):
                STATE.run_read(["gh", "pr", "view"])
            with self.assertRaises(STATE.MutationAmbiguousError):
                STATE.run_mutation(["gh", "pr", "edit"])


class ReviewablePrFixture(unittest.TestCase):
    repository = "acme/app"
    base = "main"
    head = "fork-owner:widget"
    head_owner = "fork-owner"
    head_repository = "fork-owner/app-fork"
    base_oid = "a" * 40
    head_oid = "b" * 40
    title = "feat: widget"
    pr_number = 42
    url = "https://github.com/acme/app/pull/42"
    nonce = "nonce-42"
    review_input_schema_version = 2
    review_input_sha256 = "d" * 64

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.template_path = Path(self.temporary_directory.name) / "body.md"
        self.receipt_directory = Path(self.temporary_directory.name) / "receipts"
        self.template = (
            "<details>\n"
            f"https://github.com/acme/app/pull/{CREATE.PR_NUMBER_TOKEN}/files\n"
            "</details>\n"
        )
        self.template_path.write_text(self.template, encoding="utf-8")
        self.body = self.template.replace(CREATE.PR_NUMBER_TOKEN, str(self.pr_number))
        self.transport_body = CREATE._transport_body(self.nonce)
        self._create_review_input = mock.patch.object(
            CREATE,
            "_review_input",
            return_value=(self.review_input_schema_version, self.review_input_sha256),
        ).start()
        self._update_review_input = mock.patch.object(
            UPDATE,
            "_bind_review_input",
            return_value=(self.review_input_schema_version, self.review_input_sha256),
        ).start()
        self.addCleanup(self._create_review_input.stop)
        self.addCleanup(self._update_review_input.stop)
        self._create_candidate = mock.patch.object(
            CREATE, "_build_candidate", side_effect=self._fixture_candidate
        ).start()
        self._update_candidate = mock.patch.object(
            UPDATE, "_build_candidate", side_effect=self._fixture_candidate
        ).start()
        self._create_required_review = mock.patch.object(
            CREATE,
            "_validate_required_review",
            side_effect=self._fixture_review,
        ).start()
        self._update_required_review = mock.patch.object(
            UPDATE,
            "_validate_required_review",
            side_effect=self._fixture_review,
        ).start()
        self.addCleanup(self._create_candidate.stop)
        self.addCleanup(self._update_candidate.stop)
        self.addCleanup(self._create_required_review.stop)
        self.addCleanup(self._update_required_review.stop)

    def _fixture_candidate(self, **kwargs: object):
        operation = str(kwargs["operation"])
        review_input_raw = Path(kwargs["review_input_path"]).read_bytes()
        selected_specialists = kwargs.get("selected_specialists", [])
        review_input = {
            "schema_version": self.review_input_schema_version,
            "raw_sha256": hashlib.sha256(review_input_raw).hexdigest(),
            "content_sha256": self.review_input_sha256,
        }
        value = {
            "schema_version": 1,
            "contract": "mergecraft-publication-candidate-v1",
            "operation": operation,
            "repository": kwargs["repository"],
            "pr_number": kwargs["pr_number"],
            "base": {"ref": kwargs["base"], "oid": kwargs["base_oid"]},
            "head": {
                "ref": kwargs["head"],
                "oid": kwargs["head_oid"],
                "owner": kwargs["head_owner"],
                "repository": kwargs["head_repository"],
            },
            "title": kwargs["title"],
            "body_source": REQUIRED_REVIEW.body_source_binding(
                kind=str(kwargs["body_source_kind"]),
                raw=kwargs["body_source_raw"],
                published=str(kwargs["published_body"]),
                render_contract=(
                    "mergecraft-pr-number-token-render-v1"
                    if kwargs["body_source_kind"] == "template"
                    else "literal-utf8-v1"
                ),
            ),
            "review_input": review_input,
            "publication_profile": {
                "contract": "mergecraft-publication-profile-v1",
                "review_mode": kwargs["review_mode"],
                "selected_specialists": selected_specialists,
            },
        }
        digest = hashlib.sha256(RequiredReviewTests.canonical(value)).hexdigest()
        return REQUIRED_REVIEW.PublicationCandidate(
            value=value,
            content_sha256=digest,
            candidate_identity={
                "kind": "mergecraft-publication-candidate-v1",
                "value": f"sha256:{digest}",
                "content_sha256": digest,
            },
            review_input_identity=REQUIRED_REVIEW._identity_for(
                "mergecraft-review-input-v1", review_input
            ),
            requirements_identity=REQUIRED_REVIEW._identity_for(
                "mergecraft-required-publication-review-profile-v2",
                REQUIRED_REVIEW._required_profile(selected_specialists),
            ),
            body_source_raw=kwargs["body_source_raw"],
            published_body=str(kwargs["published_body"]),
        )

    @staticmethod
    def _fixture_review(**kwargs: object):
        candidate = kwargs["candidate"]
        observation = None
        if kwargs["review_mode"] == "required":
            observation = REQUIRED_REVIEW.RequiredReviewObservation(
                launch_envelope_sha256="1" * 64,
                generation="sha256-" + "2" * 64,
                active_record_sha256="3" * 64,
                trust_context_sha256="4" * 64,
                bundle_sha256="5" * 64,
                tricritical_manifest_sha256="6" * 64,
                tricritical_projection_sha256="7" * 64,
            )
        return transition_review(
            kwargs["review_mode"], candidate.content_sha256, observation
        )

    @property
    def expected(self):
        return CREATE.ExpectedIdentity(
            repository=self.repository,
            pr_number=self.pr_number,
            base=self.base,
            base_oid=self.base_oid,
            head=self.head,
            head_oid=self.head_oid,
            head_owner=self.head_owner,
            head_repository=self.head_repository,
        )

    def stored(
        self,
        *,
        body: str | None = None,
        title: str | None = None,
        is_draft: bool = True,
        state: str = "OPEN",
        **overrides: object,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "number": self.pr_number,
            "url": self.url,
            "title": self.title if title is None else title,
            "body": self.body if body is None else body,
            "baseRefName": self.base,
            "baseRefOid": self.base_oid,
            "headRefName": "widget",
            "headRefOid": self.head_oid,
            "headRepositoryOwner": {"login": self.head_owner},
            "headRepository": {"nameWithOwner": self.head_repository},
            "isDraft": is_draft,
            "state": state,
        }
        value.update(overrides)
        return value

    def transport(self, **overrides: object) -> dict[str, object]:
        return self.stored(body=self.transport_body, **overrides)

    def publish(self):
        return CREATE.publish(
            repository=self.repository,
            base=self.base,
            base_oid=self.base_oid,
            head=self.head,
            head_oid=self.head_oid,
            head_owner=self.head_owner,
            head_repository=self.head_repository,
            title=self.title,
            template_path=self.template_path,
            review_input_path=self.template_path,
            review_mode="not-required",
            review_bundle_root=None,
            selected_specialists=[],
            receipt_directory=self.receipt_directory,
        )


class CreateReviewablePrTests(ReviewablePrFixture):
    def required_publish(self):
        return CREATE.publish(
            repository=self.repository,
            base=self.base,
            base_oid=self.base_oid,
            head=self.head,
            head_oid=self.head_oid,
            head_owner=self.head_owner,
            head_repository=self.head_repository,
            title=self.title,
            template_path=self.template_path,
            review_input_path=self.template_path,
            review_mode="required",
            review_bundle_root=Path("/absolute/review-bundle"),
            selected_specialists=[],
            receipt_directory=self.receipt_directory,
        )

    def test_required_create_remote_drift_after_review_blocks_canonical_edit(
        self,
    ) -> None:
        drifted = self.transport(title="reviewer changed title")
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(
                CREATE, "_stored_pr", side_effect=[self.transport(), drifted]
            ),
            mock.patch.object(CREATE, "_run_mutation") as mutate,
        ):
            with self.assertRaisesRegex(CREATE.PublicationError, "after review"):
                self.required_publish()

        mutate.assert_not_called()
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_required_create_receipt_preimage_is_post_review_snapshot(self) -> None:
        assigned = self.transport()
        post_review = dict(assigned)
        after = self.stored()
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(
                CREATE,
                "_stored_pr",
                side_effect=[assigned, post_review, after],
            ),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            mock.patch.object(
                CREATE,
                "verified_transition",
                wraps=RECEIPTS.verified_transition,
            ) as transition,
        ):
            self.required_publish()

        self.assertIs(transition.call_args.kwargs["preimage"], post_review)

    def test_candidate_secret_blocks_before_create_without_echoing_value(self) -> None:
        credential_shape = "ghp_123456789012345678901234567890"
        template = self.template + f"\nCredential: {credential_shape}\n"

        with (
            mock.patch.object(Path, "read_bytes", return_value=template.encode()),
            mock.patch.object(CREATE, "_create") as create,
            self.assertRaisesRegex(
                CREATE.PublicationError, "publication is blocked"
            ) as raised,
        ):
            self.publish()

        create.assert_not_called()
        self.assertNotIn(credential_shape, str(raised.exception))

    def test_create_always_drafts_after_empty_exact_preflight(self) -> None:
        completed = subprocess.CompletedProcess([], 0, self.url, "")
        with (
            mock.patch.object(CREATE, "_matching_head_prs", return_value=[]),
            mock.patch.object(CREATE, "_run_mutation", return_value=completed) as run,
        ):
            result = CREATE._create(
                repository=self.repository,
                base=self.base,
                base_oid=self.base_oid,
                head=self.head,
                head_oid=self.head_oid,
                head_owner=self.head_owner,
                head_repository=self.head_repository,
                title=self.title,
                nonce=self.nonce,
            )
        self.assertEqual(result, (self.pr_number, self.url))
        self.assertIn("--draft", run.call_args.args[0])

    def test_stops_before_create_when_exact_head_base_pr_exists(self) -> None:
        with (
            mock.patch.object(
                CREATE, "_matching_head_prs", return_value=[self.stored()]
            ),
            mock.patch.object(CREATE, "_run_mutation") as run,
        ):
            with self.assertRaisesRegex(CREATE.PublicationError, "already exists"):
                CREATE._create(
                    repository=self.repository,
                    base=self.base,
                    base_oid=self.base_oid,
                    head=self.head,
                    head_oid=self.head_oid,
                    head_owner=self.head_owner,
                    head_repository=self.head_repository,
                    title=self.title,
                    nonce=self.nonce,
                )
        run.assert_not_called()

    def test_recovers_unique_nonce_draft_after_ambiguous_create_error(self) -> None:
        with (
            mock.patch.object(
                CREATE, "_matching_head_prs", side_effect=[[], [self.transport()]]
            ),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                side_effect=CREATE.PublicationError("network lost"),
            ),
        ):
            result = CREATE._create(
                repository=self.repository,
                base=self.base,
                base_oid=self.base_oid,
                head=self.head,
                head_oid=self.head_oid,
                head_owner=self.head_owner,
                head_repository=self.head_repository,
                title=self.title,
                nonce=self.nonce,
            )
        self.assertEqual(result, (self.pr_number, self.url))

    def test_recovery_rejects_boolean_pr_number(self) -> None:
        with mock.patch.object(
            CREATE,
            "_matching_head_prs",
            return_value=[self.transport(number=True)],
        ):
            result = CREATE._recover_created(
                repository=self.repository,
                base=self.base,
                base_oid=self.base_oid,
                head=self.head,
                head_oid=self.head_oid,
                head_owner=self.head_owner,
                head_repository=self.head_repository,
                title=self.title,
                transport_body=self.transport_body,
            )

        self.assertIsNone(result)

    def test_rejects_recovery_without_exact_nonce_and_oids(self) -> None:
        wrong = self.transport(headRefOid="c" * 40)
        with (
            mock.patch.object(CREATE, "_matching_head_prs", side_effect=[[], [wrong]]),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                side_effect=CREATE.PublicationError("network lost"),
            ),
        ):
            with self.assertRaisesRegex(CREATE.PublicationError, "ambiguous"):
                CREATE._create(
                    repository=self.repository,
                    base=self.base,
                    base_oid=self.base_oid,
                    head=self.head,
                    head_oid=self.head_oid,
                    head_owner=self.head_owner,
                    head_repository=self.head_repository,
                    title=self.title,
                    nonce=self.nonce,
                )

    def test_recovers_after_successful_create_returns_malformed_output(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "created", "")
        with (
            mock.patch.object(
                CREATE, "_matching_head_prs", side_effect=[[], [self.transport()]]
            ),
            mock.patch.object(CREATE, "_run_mutation", return_value=completed),
        ):
            result = CREATE._create(
                repository=self.repository,
                base=self.base,
                base_oid=self.base_oid,
                head=self.head,
                head_oid=self.head_oid,
                head_owner=self.head_owner,
                head_repository=self.head_repository,
                title=self.title,
                nonce=self.nonce,
            )
        self.assertEqual(result, (self.pr_number, self.url))

    def test_installs_canonical_body_with_one_mutation_and_final_read(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(CREATE, "_run_mutation", return_value=completed) as run,
            mock.patch.object(
                CREATE,
                "_stored_pr",
                side_effect=[self.transport(), self.transport(), self.stored()],
            ) as reads,
        ):
            result = self.publish()
        self.assertEqual(result, self.stored())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(reads.call_count, 3)

    def test_edit_error_never_mints_canonical_provenance(self) -> None:
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                side_effect=STATE.MutationAmbiguousError(
                    "timeout after possible mutation; do not retry"
                ),
            ) as run,
            mock.patch.object(
                CREATE,
                "_stored_pr",
                side_effect=[self.transport(), self.transport(), self.stored()],
            ),
        ):
            with self.assertRaisesRegex(
                STATE.MutationAmbiguousError, "canonical provenance was not minted"
            ):
                self.publish()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_edit_nonzero_with_matching_state_is_not_canonical(self) -> None:
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                side_effect=CREATE.PublicationError("nonzero"),
            ),
            mock.patch.object(
                CREATE,
                "_stored_pr",
                side_effect=[self.transport(), self.transport(), self.stored()],
            ),
        ):
            with self.assertRaisesRegex(
                CREATE.PublicationError, "canonical provenance was not minted"
            ):
                self.publish()
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_canonical_edit_timeout_with_unchanged_state_remains_ambiguous(
        self,
    ) -> None:
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                side_effect=STATE.MutationAmbiguousError("timeout; do not retry"),
            ) as run,
            mock.patch.object(
                CREATE,
                "_stored_pr",
                side_effect=[self.transport(), self.transport(), self.transport()],
            ),
        ):
            with self.assertRaisesRegex(STATE.MutationAmbiguousError, "do not retry"):
                self.publish()
        self.assertEqual(run.call_count, 1)

    def test_edit_failure_is_not_retried_or_rolled_back(self) -> None:
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                side_effect=CREATE.PublicationError("failed"),
            ) as run,
            mock.patch.object(
                CREATE,
                "_stored_pr",
                side_effect=[self.transport(), self.transport(), self.transport()],
            ),
        ):
            with self.assertRaisesRegex(CREATE.PublicationError, "no automatic retry"):
                self.publish()
        self.assertEqual(run.call_count, 1)

    def test_concurrent_state_blocks_mutation_or_retry(self) -> None:
        concurrent = self.stored(title="reviewer edit", body="reviewer body")
        with (
            mock.patch.object(CREATE, "_validate"),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", return_value=(42, self.url)),
            mock.patch.object(CREATE, "_run_mutation") as run,
            mock.patch.object(
                CREATE, "_stored_pr", side_effect=[self.transport(), concurrent]
            ),
        ):
            with self.assertRaisesRegex(CREATE.PublicationError, "no longer has"):
                self.publish()
        run.assert_not_called()

    def test_requires_qualified_head_before_validation_or_create(self) -> None:
        with (
            mock.patch.object(CREATE, "_validate") as validate,
            mock.patch.object(CREATE, "_create") as create,
        ):
            with self.assertRaisesRegex(CREATE.PublicationError, "OWNER:BRANCH"):
                CREATE.publish(
                    repository=self.repository,
                    base=self.base,
                    base_oid=self.base_oid,
                    head="widget",
                    head_oid=self.head_oid,
                    head_owner=self.head_owner,
                    head_repository=self.head_repository,
                    title=self.title,
                    template_path=self.template_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        validate.assert_not_called()
        create.assert_not_called()

    def test_token_template_is_validated_before_create_and_exact_body_after(
        self,
    ) -> None:
        events: list[str] = []

        def validate(*_: object, **__: object) -> None:
            events.append("validate")

        def create(**_: object) -> tuple[int, str]:
            events.append("create")
            return self.pr_number, self.url

        with (
            mock.patch.object(CREATE, "_validate", side_effect=validate),
            mock.patch.object(CREATE, "_new_nonce", return_value=self.nonce),
            mock.patch.object(CREATE, "_create", side_effect=create),
            mock.patch.object(
                CREATE,
                "_stored_pr",
                side_effect=[self.transport(), self.transport(), self.stored()],
            ),
            mock.patch.object(
                CREATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
        ):
            self.publish()

        self.assertEqual(events, ["validate", "create", "validate"])


class UpdateReviewablePrTests(ReviewablePrFixture):
    def test_existing_secret_blocks_before_mutation_without_echoing_value(self) -> None:
        secret = "ghp_123456789012345678901234567890"
        stored = self.stored(body=f"Credential: {secret}")
        with mock.patch.object(UPDATE, "_stored_pr", return_value=stored):
            with self.assertRaisesRegex(
                UPDATE.PublicationError, "pending authorized removal and rotation"
            ) as raised:
                UPDATE._preflight(
                    expected=self.expected,
                    expected_title_sha256=hashlib.sha256(
                        self.title.encode("utf-8")
                    ).hexdigest(),
                    expected_body_sha256=hashlib.sha256(
                        str(stored["body"]).encode("utf-8")
                    ).hexdigest(),
                    expected_draft=True,
                )

        self.assertNotIn(secret, str(raised.exception))

    def digest(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def desired_body_path(self) -> Path:
        path = Path(self.temporary_directory.name) / "desired.md"
        path.write_text(self.body + "updated\n", encoding="utf-8")
        return path

    def test_required_text_remote_drift_after_review_blocks_mutation(self) -> None:
        desired_path = self.desired_body_path()
        drifted = self.stored(title="reviewer changed title")
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), drifted],
            ),
            mock.patch.object(UPDATE, "_run_mutation") as mutate,
        ):
            with self.assertRaisesRegex(UPDATE.PublicationError, "preimage changed"):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title=self.title,
                    body_path=desired_path,
                    review_input_path=self.template_path,
                    review_mode="required",
                    review_bundle_root=Path("/absolute/review-bundle"),
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )

        mutate.assert_not_called()
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_required_ready_remote_drift_after_review_blocks_mutation(self) -> None:
        drifted = self.stored(body=self.body + "reviewer change\n")
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), drifted],
            ),
            mock.patch.object(UPDATE, "_run_mutation") as mutate,
        ):
            with self.assertRaisesRegex(UPDATE.PublicationError, "preimage changed"):
                UPDATE.mark_ready(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    review_input_path=self.template_path,
                    review_mode="required",
                    review_bundle_root=Path("/absolute/review-bundle"),
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )

        mutate.assert_not_called()
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_text_update_has_exact_preflight_one_write_and_final_read(self) -> None:
        desired_path = self.desired_body_path()
        desired = desired_path.read_text()
        after = self.stored(title="feat: updated", body=desired)
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), after],
            ) as reads,
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run,
        ):
            result = UPDATE.update_text(
                expected=self.expected,
                expected_title_sha256=self.digest(self.title),
                expected_body_sha256=self.digest(self.body),
                expected_draft=True,
                title="feat: updated",
                body_path=desired_path,
                review_input_path=self.template_path,
                review_mode="not-required",
                review_bundle_root=None,
                selected_specialists=[],
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(result, after)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(reads.call_count, 3)

    def test_text_update_renders_new_pr_template_for_graphite_repair(self) -> None:
        after = self.stored()
        with (
            mock.patch.object(UPDATE, "_validate_body") as validate,
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.transport(), self.transport(), after],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run,
        ):
            result = UPDATE.update_text(
                expected=self.expected,
                expected_title_sha256=self.digest(self.title),
                expected_body_sha256=self.digest(self.transport_body),
                expected_draft=True,
                title=self.title,
                body_template_path=self.template_path,
                review_input_path=self.template_path,
                review_mode="not-required",
                review_bundle_root=None,
                selected_specialists=[],
                receipt_directory=self.receipt_directory,
            )

        self.assertEqual(result, after)
        self.assertEqual(run.call_count, 1)
        validate.assert_called_once_with(
            self.body,
            self.repository,
            self.pr_number,
            self.title,
            self.template_path,
            self.template_path,
        )
        self.assertEqual(self._update_review_input.call_args.args[-1], self.template)

    def test_text_update_requires_exactly_one_body_source(self) -> None:
        with mock.patch.object(UPDATE, "_run_mutation") as run:
            with self.assertRaisesRegex(
                UPDATE.PublicationError, "exactly one of body file"
            ):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title=self.title,
                    body_path=self.template_path,
                    body_template_path=self.template_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        run.assert_not_called()

    def test_text_update_accepts_exact_noncanonical_preimage(self) -> None:
        legacy_body = "Legacy PR body without change navigation.\n"
        desired_path = self.desired_body_path()
        desired = desired_path.read_text()
        after = self.stored(title="feat: updated", body=desired)
        with (
            mock.patch.object(UPDATE, "_validate_body") as validate,
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[
                    self.stored(body=legacy_body),
                    self.stored(body=legacy_body),
                    after,
                ],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
        ):
            result = UPDATE.update_text(
                expected=self.expected,
                expected_title_sha256=self.digest(self.title),
                expected_body_sha256=self.digest(legacy_body),
                expected_draft=True,
                title="feat: updated",
                body_path=desired_path,
                review_input_path=self.template_path,
                review_mode="not-required",
                review_bundle_root=None,
                selected_specialists=[],
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(result, after)
        validate.assert_called_once_with(
            desired,
            self.repository,
            self.pr_number,
            "feat: updated",
            self.template_path,
        )

    def test_text_update_publishes_validated_snapshot(self) -> None:
        desired_path = self.desired_body_path()
        desired = desired_path.read_text()
        after = self.stored(body=desired)

        def mutate_source_after_snapshot(arguments: list[str], **_: object):
            body_file = Path(arguments[arguments.index("--body-file") + 1])
            self.assertNotEqual(body_file, desired_path)
            self.assertEqual(body_file.read_text(encoding="utf-8"), desired)
            desired_path.write_text("changed after validation\n", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), after],
            ),
            mock.patch.object(
                UPDATE, "_run_mutation", side_effect=mutate_source_after_snapshot
            ),
        ):
            result = UPDATE.update_text(
                expected=self.expected,
                expected_title_sha256=self.digest(self.title),
                expected_body_sha256=self.digest(self.body),
                expected_draft=True,
                title=self.title,
                body_path=desired_path,
                review_input_path=self.template_path,
                review_mode="not-required",
                review_bundle_root=None,
                selected_specialists=[],
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(result, after)

    def test_text_preimage_drift_stops_before_write(self) -> None:
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(UPDATE, "_stored_pr", return_value=self.stored()),
            mock.patch.object(UPDATE, "_run_mutation") as run,
        ):
            with self.assertRaisesRegex(UPDATE.PublicationError, "preimage changed"):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256="c" * 64,
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title=self.title,
                    body_path=self.desired_body_path(),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        run.assert_not_called()

    def test_body_only_scope_preserves_live_title(self) -> None:
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(UPDATE, "_stored_pr", return_value=self.stored()),
            mock.patch.object(UPDATE, "_run_mutation") as run,
        ):
            with self.assertRaisesRegex(
                UPDATE.PublicationError, "body-only edit changed the live title"
            ):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title="feat: unauthorized title",
                    body_path=self.desired_body_path(),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                    text_scope="body-only",
                )
        run.assert_not_called()

    def test_title_only_scope_preserves_live_body(self) -> None:
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(UPDATE, "_stored_pr", return_value=self.stored()),
            mock.patch.object(UPDATE, "_run_mutation") as run,
        ):
            with self.assertRaisesRegex(
                UPDATE.PublicationError, "title-only edit changed the live body"
            ):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title="feat: authorized title",
                    body_path=self.desired_body_path(),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                    text_scope="title-only",
                )
        run.assert_not_called()

    def test_text_command_error_never_mints_canonical_provenance(self) -> None:
        desired_path = self.desired_body_path()
        after = self.stored(body=desired_path.read_text())
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), after],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                side_effect=STATE.MutationAmbiguousError(
                    "timeout after possible mutation; do not retry"
                ),
            ) as run,
        ):
            with self.assertRaisesRegex(
                STATE.MutationAmbiguousError, "canonical provenance was not minted"
            ):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title=self.title,
                    body_path=desired_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_text_nonzero_with_matching_state_is_not_canonical(self) -> None:
        desired_path = self.desired_body_path()
        after = self.stored(body=desired_path.read_text())
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), after],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                side_effect=UPDATE.PublicationError("nonzero"),
            ),
        ):
            with self.assertRaisesRegex(
                UPDATE.PublicationError, "canonical provenance was not minted"
            ):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title=self.title,
                    body_path=desired_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_text_timeout_with_unchanged_state_remains_ambiguous(self) -> None:
        desired_path = self.desired_body_path()
        before = self.stored()
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE, "_stored_pr", side_effect=[before, before, before]
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                side_effect=STATE.MutationAmbiguousError("timeout; do not retry"),
            ) as run,
        ):
            with self.assertRaisesRegex(STATE.MutationAmbiguousError, "do not retry"):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title=self.title,
                    body_path=desired_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(run.call_count, 1)

    def test_text_ambiguous_drift_is_not_retried_or_rolled_back(self) -> None:
        desired_path = self.desired_body_path()
        concurrent = self.stored(title="reviewer edit", body="reviewer body")
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), concurrent],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run,
        ):
            with self.assertRaisesRegex(
                UPDATE.PublicationError, "no retry or rollback"
            ):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    expected_draft=True,
                    title=self.title,
                    body_path=desired_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(run.call_count, 1)

    def test_ready_command_error_never_mints_canonical_provenance(self) -> None:
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[
                    self.stored(),
                    self.stored(),
                    self.stored(is_draft=False),
                ],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                side_effect=STATE.MutationAmbiguousError(
                    "timeout after possible mutation; do not retry"
                ),
            ) as run,
        ):
            with self.assertRaisesRegex(
                STATE.MutationAmbiguousError, "canonical provenance was not minted"
            ):
                UPDATE.mark_ready(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_ready_nonzero_with_matching_state_is_not_canonical(self) -> None:
        before = self.stored()
        after = self.stored(is_draft=False)
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE, "_stored_pr", side_effect=[before, before, after]
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                side_effect=UPDATE.PublicationError("nonzero"),
            ),
        ):
            with self.assertRaisesRegex(
                UPDATE.PublicationError, "canonical provenance was not minted"
            ):
                UPDATE.mark_ready(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )

    def test_ready_timeout_with_unchanged_state_remains_ambiguous(self) -> None:
        before = self.stored()
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE, "_stored_pr", side_effect=[before, before, before]
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                side_effect=STATE.MutationAmbiguousError("timeout; do not retry"),
            ) as run,
        ):
            with self.assertRaisesRegex(STATE.MutationAmbiguousError, "do not retry"):
                UPDATE.mark_ready(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(run.call_count, 1)

    def test_ready_rechecks_exact_preimage_after_body_validation(self) -> None:
        concurrent = self.stored(title="reviewer edit", body="reviewer body")
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE, "_stored_pr", side_effect=[self.stored(), concurrent]
            ) as reads,
            mock.patch.object(UPDATE, "_run_mutation") as run,
        ):
            with self.assertRaisesRegex(UPDATE.PublicationError, "preimage changed"):
                UPDATE.mark_ready(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(self.body),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(reads.call_count, 2)
        run.assert_not_called()

    def test_ready_rejects_noncanonical_live_body_before_mutation(self) -> None:
        legacy_body = "Legacy PR body without change navigation.\n"
        with (
            mock.patch.object(
                UPDATE, "_stored_pr", return_value=self.stored(body=legacy_body)
            ),
            mock.patch.object(
                UPDATE,
                "_validate_body",
                side_effect=UPDATE.PublicationError("body is noncanonical"),
            ),
            mock.patch.object(UPDATE, "_run_mutation") as run,
        ):
            with self.assertRaisesRegex(UPDATE.PublicationError, "noncanonical"):
                UPDATE.mark_ready(
                    expected=self.expected,
                    expected_title_sha256=self.digest(self.title),
                    expected_body_sha256=self.digest(legacy_body),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        run.assert_not_called()


class PublicationReceiptTests(ReviewablePrFixture):
    def setUp(self) -> None:
        super().setUp()
        self.receipt_directory = Path(self.temporary_directory.name) / "receipts"
        RECEIPTS.prepare_receipt_store(self.receipt_directory)

    def transition_candidate(
        self,
        *,
        operation: str,
        final_state: dict[str, object],
        mode: str,
        expected=None,
    ):
        expected = expected or self.expected
        if operation == "create":
            pr_number: int | str = CREATE.PR_NUMBER_TOKEN
            kind = "template"
            raw = self.template.encode("utf-8")
            published = self.template
        elif operation == "mark-ready":
            pr_number = expected.pr_number
            kind = "stored-body"
            published = str(final_state["body"])
            raw = published.encode("utf-8")
        else:
            pr_number = expected.pr_number
            kind = "body"
            published = str(final_state["body"])
            raw = published.encode("utf-8")
        value = {
            "schema_version": 1,
            "contract": "mergecraft-publication-candidate-v1",
            "operation": operation,
            "repository": expected.repository,
            "pr_number": pr_number,
            "base": {"ref": expected.base, "oid": expected.base_oid},
            "head": {
                "ref": expected.head,
                "oid": expected.head_oid,
                "owner": expected.head_owner,
                "repository": expected.head_repository,
            },
            "title": str(final_state["title"]),
            "body_source": REQUIRED_REVIEW.body_source_binding(
                kind=kind,
                raw=raw,
                published=published,
                render_contract=(
                    "mergecraft-pr-number-token-render-v1"
                    if kind == "template"
                    else "literal-utf8-v1"
                ),
            ),
            "review_input": {
                "schema_version": self.review_input_schema_version,
                "raw_sha256": "e" * 64,
                "content_sha256": self.review_input_sha256,
            },
            "publication_profile": {
                "contract": "mergecraft-publication-profile-v1",
                "review_mode": mode,
                "selected_specialists": [],
            },
        }
        digest = hashlib.sha256(RequiredReviewTests.canonical(value)).hexdigest()
        candidate = REQUIRED_REVIEW.PublicationCandidate(
            value=value,
            content_sha256=digest,
            candidate_identity={
                "kind": "mergecraft-publication-candidate-v1",
                "value": f"sha256:{digest}",
                "content_sha256": digest,
            },
            review_input_identity=REQUIRED_REVIEW._identity_for(
                "mergecraft-review-input-v1", value["review_input"]
            ),
            requirements_identity=REQUIRED_REVIEW._identity_for(
                "mergecraft-required-publication-review-profile-v2",
                REQUIRED_REVIEW._required_profile([]),
            ),
            body_source_raw=raw,
            published_body=published,
        )
        return candidate

    def canonical_receipt(
        self,
        *,
        operation: str = "create",
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        root: Path | None = None,
        review=None,
    ):
        final_state = after or self.stored()
        if before is None and operation == "create":
            before = self.transport(title=final_state["title"])
        candidate = self.transition_candidate(
            operation=operation,
            final_state=final_state,
            mode=review.mode if review is not None else "not-required",
        )
        chosen_review = review or transition_review(
            "not-required", candidate.content_sha256, None
        )
        if review is not None:
            chosen_review = transition_review(
                review.mode, candidate.content_sha256, review.observation
            )
        transition = RECEIPTS.verified_transition(
            expected=self.expected,
            operation=operation,
            preimage=before or self.transport(),
            final_reread=final_state,
            review_input_schema_version=self.review_input_schema_version,
            review_input_sha256=self.review_input_sha256,
            review=chosen_review,
            candidate=candidate,
        )
        receipt_root = root or self.receipt_directory
        RECEIPTS.prepare_receipt_ledger(receipt_root, self.expected)
        with RECEIPTS.receipt_ledger_lock(receipt_root, self.expected) as lease:
            return RECEIPTS.record_verified_publication(
                root=receipt_root,
                transition=transition,
                lease=lease,
            )

    def reconciled_receipt(self, *, root: Path | None = None):
        stored = self.stored()
        transition = RECEIPTS.verified_transition(
            expected=self.expected,
            operation="reconcile",
            preimage=stored,
            final_reread=stored,
            review_input_schema_version=self.review_input_schema_version,
            review_input_sha256=self.review_input_sha256,
            review=None,
            candidate=None,
        )
        receipt_root = root or self.receipt_directory
        RECEIPTS.prepare_receipt_ledger(receipt_root, self.expected)
        with RECEIPTS.receipt_ledger_lock(receipt_root, self.expected) as lease:
            return RECEIPTS.record_reconciliation(
                root=receipt_root,
                transition=transition,
                lease=lease,
            )

    def rewrite_receipt(self, path: Path, payload: dict[str, object]) -> Path:
        unsigned = dict(payload)
        unsigned.pop("content_sha256", None)
        digest = hashlib.sha256(
            json.dumps(
                unsigned,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        payload["content_sha256"] = digest
        rewritten = path.with_name(
            f"{int(payload['sequence']):08d}-{digest}-{payload['receipt_id']}.json"
        )
        if rewritten != path:
            path.unlink(missing_ok=True)
        rewritten.write_text(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        rewritten.chmod(0o600)
        return rewritten

    def test_canonical_receipt_is_redacted_atomic_and_bound_to_final_reread(
        self,
    ) -> None:
        receipt = self.canonical_receipt()

        self.assertEqual(receipt.provenance, "canonical")
        self.assertEqual(receipt.review_input_schema_version, 2)
        self.assertEqual(receipt.review_input_sha256, self.review_input_sha256)
        self.assertEqual(
            receipt.as_json()["publisher"],
            {"name": "publishing-reviewable-prs", "version": 1},
        )
        self.assertEqual(receipt.as_json()["policy"], {"version": 1})
        receipts = RECEIPTS.load_receipts(self.receipt_directory, self.expected)
        self.assertEqual(receipts, [receipt])
        receipt_path = next(self.receipt_directory.rglob("*.json"))
        raw = receipt_path.read_text(encoding="utf-8")
        self.assertNotIn(self.title, raw)
        self.assertNotIn(self.body, raw)
        self.assertEqual(receipt_path.stat().st_mode & 0o077, 0)
        self.assertFalse(list(self.receipt_directory.rglob("*.tmp")))

    def test_v3_receipt_distinguishes_required_not_required_and_reconciliation(
        self,
    ) -> None:
        observation = REQUIRED_REVIEW.RequiredReviewObservation(
            launch_envelope_sha256="1" * 64,
            generation="sha256-" + "2" * 64,
            active_record_sha256="3" * 64,
            trust_context_sha256="4" * 64,
            bundle_sha256="5" * 64,
            tricritical_manifest_sha256="6" * 64,
            tricritical_projection_sha256="7" * 64,
        )
        required_root = Path(self.temporary_directory.name) / "required"
        RECEIPTS.prepare_receipt_store(required_root)
        required = self.canonical_receipt(
            root=required_root,
            review=transition_review("required", "8" * 64, observation),
        )
        self.assertEqual(required.schema_version, 3)
        self.assertEqual(required.summary("verified")["review"]["mode"], "required")
        self.assertEqual(
            required.summary("verified")["review"]["observation"][
                "launch_envelope_sha256"
            ],
            "1" * 64,
        )

        not_required = self.canonical_receipt()
        self.assertEqual(
            not_required.summary("verified")["review"],
            {
                "mode": "not-required",
                "publication_candidate_sha256": (
                    not_required.review.publication_candidate_sha256
                ),
                "observation": None,
            },
        )

        reconciliation_root = Path(self.temporary_directory.name) / "reconcile"
        RECEIPTS.prepare_receipt_store(reconciliation_root)
        reconciled = self.reconciled_receipt(root=reconciliation_root)
        self.assertIsNone(reconciled.review)
        self.assertEqual(
            reconciled.summary("verified")["review"],
            {"state": "unwitnessed-reconciliation"},
        )

    def test_v2_receipt_remains_chained_but_is_only_legacy_unrecorded(self) -> None:
        receipt = self.canonical_receipt()
        path = next(self.receipt_directory.rglob("*.json"))
        payload = receipt.as_json()
        payload["schema_version"] = 2
        del payload["review"]
        self.rewrite_receipt(path, payload)

        loaded = RECEIPTS.load_receipts(self.receipt_directory, self.expected)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].schema_version, 2)
        self.assertIsNone(loaded[0].review)
        self.assertEqual(
            loaded[0].summary("verified")["review"],
            {"state": "legacy-unrecorded"},
        )

    def test_receipt_ledger_rejects_v2_after_first_v3(self) -> None:
        state_a = self.stored(title="state A")
        self.canonical_receipt(after=state_a)
        state_b = self.stored(title="state B")
        self.canonical_receipt(
            operation="update-text",
            before=state_a,
            after=state_b,
        )
        for path in self.receipt_directory.rglob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload["sequence"] == 2:
                payload["schema_version"] = 2
                del payload["review"]
                self.rewrite_receipt(path, payload)
                break
        else:
            self.fail("second receipt is absent")

        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "moved backward"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

    def test_review_from_candidate_a_cannot_mint_candidate_b_provenance(
        self,
    ) -> None:
        state_a = self.stored(title="candidate A")
        state_b = self.stored(title="candidate B")
        candidate_a = self.transition_candidate(
            operation="update-text", final_state=state_a, mode="not-required"
        )
        candidate_b = self.transition_candidate(
            operation="update-text", final_state=state_b, mode="not-required"
        )
        review_a = transition_review("not-required", candidate_a.content_sha256, None)

        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "candidate evidence"):
            RECEIPTS.verified_transition(
                expected=self.expected,
                operation="update-text",
                preimage=self.stored(),
                final_reread=state_b,
                review_input_schema_version=self.review_input_schema_version,
                review_input_sha256=self.review_input_sha256,
                review=review_a,
                candidate=candidate_b,
            )

    def test_parsed_receipt_review_cannot_mint_a_new_transition(self) -> None:
        state = self.stored(title="candidate state")
        candidate = self.transition_candidate(
            operation="update-text", final_state=state, mode="not-required"
        )
        parsed = REQUIRED_REVIEW.parse_publication_review(
            transition_review("not-required", candidate.content_sha256, None).as_json()
        )

        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "candidate evidence"):
            RECEIPTS.verified_transition(
                expected=self.expected,
                operation="update-text",
                preimage=self.stored(),
                final_reread=state,
                review_input_schema_version=self.review_input_schema_version,
                review_input_sha256=self.review_input_sha256,
                review=parsed,
                candidate=candidate,
            )

    def test_receipt_write_failure_never_exposes_a_partial_final_receipt(self) -> None:
        with mock.patch.object(RECEIPTS.os, "link", side_effect=OSError("full")):
            with self.assertRaisesRegex(RECEIPTS.ReceiptError, "atomically commit"):
                self.canonical_receipt()
        self.assertFalse(list(self.receipt_directory.rglob("*.json")))

    def test_text_mutation_records_only_its_verified_final_reread(self) -> None:
        desired_path = Path(self.temporary_directory.name) / "desired.md"
        desired_path.write_text(self.body + "updated\n", encoding="utf-8")
        desired = desired_path.read_text(encoding="utf-8")
        after = self.stored(body=desired)
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), after],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
        ):
            result = UPDATE.update_text(
                expected=self.expected,
                expected_title_sha256=hashlib.sha256(
                    self.title.encode("utf-8")
                ).hexdigest(),
                expected_body_sha256=hashlib.sha256(
                    self.body.encode("utf-8")
                ).hexdigest(),
                expected_draft=True,
                title=self.title,
                body_path=desired_path,
                review_input_path=self.template_path,
                review_mode="not-required",
                review_bundle_root=None,
                selected_specialists=[],
                receipt_directory=self.receipt_directory,
            )
        receipt = RECEIPTS.load_receipts(self.receipt_directory, self.expected)[0]
        self.assertEqual(result, after)
        self.assertEqual(receipt.operation, "update-text")
        self.assertEqual(
            receipt.final_state.body_sha256,
            hashlib.sha256(desired.encode()).hexdigest(),
        )

    def test_receipt_failure_after_mutation_does_not_trigger_a_second_mutation(
        self,
    ) -> None:
        desired_path = Path(self.temporary_directory.name) / "desired.md"
        desired_path.write_text(self.body + "updated\n", encoding="utf-8")
        after = self.stored(body=desired_path.read_text(encoding="utf-8"))
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(
                UPDATE,
                "_stored_pr",
                side_effect=[self.stored(), self.stored(), after],
            ),
            mock.patch.object(
                UPDATE,
                "_run_mutation",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as mutate,
            mock.patch.object(
                UPDATE,
                "record_verified_publication",
                side_effect=RECEIPTS.ReceiptError("receipt disk unavailable"),
            ),
        ):
            with self.assertRaisesRegex(RECEIPTS.ReceiptError, "receipt disk"):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=hashlib.sha256(
                        self.title.encode("utf-8")
                    ).hexdigest(),
                    expected_body_sha256=hashlib.sha256(
                        self.body.encode("utf-8")
                    ).hexdigest(),
                    expected_draft=True,
                    title=self.title,
                    body_path=desired_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        self.assertEqual(mutate.call_count, 1)

    def test_receipt_rejects_state_that_was_not_exactly_final_reread(self) -> None:
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "invalid or unverified"):
            self.canonical_receipt(after=self.stored(headRefOid="c" * 40))

    def test_audit_returns_verified_drift_and_unavailable_without_mutation(
        self,
    ) -> None:
        receipt = self.canonical_receipt()
        with mock.patch.object(AUDIT, "stored_pr", return_value=self.stored()):
            verified = AUDIT.audit(
                expected=self.expected,
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(verified.status, "verified")
        self.assertEqual(verified.receipt, receipt)
        with mock.patch.object(
            AUDIT, "stored_pr", return_value=self.stored(title="changed")
        ):
            drift = AUDIT.audit(
                expected=self.expected,
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(drift.status, "drift")
        with mock.patch.object(
            AUDIT,
            "stored_pr",
            side_effect=STATE.StateReadError("offline"),
        ):
            unavailable = AUDIT.audit(
                expected=self.expected,
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(unavailable.status, "unavailable")

    def test_malformed_receipts_are_not_treated_as_absent(self) -> None:
        self.canonical_receipt()
        malformed = next(self.receipt_directory.rglob("*.json")).parent / "bad.json"
        malformed.write_text('{"schema_version": 1}\n', encoding="utf-8")
        malformed.chmod(0o600)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "unexpected entry"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)
        with mock.patch.object(AUDIT, "stored_pr", return_value=self.stored()):
            audit = AUDIT.audit(
                expected=self.expected,
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(audit.status, "unavailable")

    def test_reconciliation_is_redacted_and_permanently_unreceipted(self) -> None:
        receipt = self.reconciled_receipt()
        self.assertEqual(receipt.provenance, "reconciled-unreceipted")
        self.assertEqual(receipt.operation, "reconcile")
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "already matches"):
            self.reconciled_receipt()
        canonical_directory = Path(self.temporary_directory.name) / "canonical"
        RECEIPTS.prepare_receipt_store(canonical_directory)
        self.canonical_receipt(root=canonical_directory)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "already matches"):
            self.reconciled_receipt(root=canonical_directory)

    def test_reconciliation_duplicate_suppression_is_scoped_to_complete_oid_epoch(
        self,
    ) -> None:
        unchanged = self.stored()
        self.reconciled_receipt()
        new_expected = STATE.ExpectedIdentity(
            repository=self.repository,
            pr_number=self.pr_number,
            base=self.base,
            base_oid="c" * 40,
            head=self.head,
            head_oid="d" * 40,
            head_owner=self.head_owner,
            head_repository=self.head_repository,
        )
        new_epoch_state = {
            **unchanged,
            "baseRefOid": "c" * 40,
            "headRefOid": "d" * 40,
        }
        transition = RECEIPTS.verified_transition(
            expected=new_expected,
            operation="reconcile",
            preimage=new_epoch_state,
            final_reread=new_epoch_state,
            review_input_schema_version=self.review_input_schema_version,
            review_input_sha256=self.review_input_sha256,
            review=None,
            candidate=None,
        )
        RECEIPTS.prepare_receipt_ledger(self.receipt_directory, new_expected)
        with RECEIPTS.receipt_ledger_lock(
            self.receipt_directory, new_expected
        ) as lease:
            new_epoch = RECEIPTS.record_reconciliation(
                root=self.receipt_directory,
                transition=transition,
                lease=lease,
            )
        self.assertEqual(new_epoch.sequence, 2)
        self.assertEqual(new_epoch.expected.head_oid, "d" * 40)
        with RECEIPTS.receipt_ledger_lock(
            self.receipt_directory, new_expected
        ) as lease:
            with self.assertRaisesRegex(RECEIPTS.ReceiptError, "already matches"):
                RECEIPTS.record_reconciliation(
                    root=self.receipt_directory,
                    transition=transition,
                    lease=lease,
                )

    def test_reconciliation_preserves_the_existing_secret_gate(self) -> None:
        secret = "ghp_123456789012345678901234567890"
        with mock.patch.object(
            AUDIT, "stored_pr", return_value=self.stored(body=f"Credential: {secret}")
        ):
            with self.assertRaisesRegex(
                AUDIT.PublicationError, "reconciliation is blocked"
            ) as raised:
                AUDIT.reconcile(
                    expected=self.expected,
                    receipt_directory=self.receipt_directory,
                    review_input_path=self.template_path,
                )
        self.assertNotIn(secret, str(raised.exception))

    def test_reconciled_receipt_cannot_be_relabelled_canonical(self) -> None:
        receipt = self.reconciled_receipt()
        path = next(self.receipt_directory.rglob("*.json"))
        payload = receipt.as_json()
        payload["provenance"] = "canonical"
        payload["operation"] = "create"
        self.rewrite_receipt(path, payload)
        with self.assertRaisesRegex(
            RECEIPTS.ReceiptError, "transition|review evidence"
        ):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

    def test_reconcile_binds_the_live_state_to_review_input_before_receipting(
        self,
    ) -> None:
        with (
            mock.patch.object(AUDIT, "stored_pr", return_value=self.stored()),
            mock.patch.object(
                AUDIT,
                "_validate_live_state",
                return_value=(
                    self.review_input_schema_version,
                    self.review_input_sha256,
                ),
            ) as validate,
        ):
            receipt = AUDIT.reconcile(
                expected=self.expected,
                receipt_directory=self.receipt_directory,
                review_input_path=self.template_path,
            )
        self.assertEqual(receipt.provenance, "reconciled-unreceipted")
        validate.assert_called_once_with(
            expected=self.expected,
            title=self.title,
            body=self.body,
            review_input_path=self.template_path,
        )

    def test_audit_uses_only_authoritative_latest_receipt(self) -> None:
        state_a = self.stored(title="state A")
        self.canonical_receipt(after=state_a)
        state_b = self.stored(title="state B")
        self.canonical_receipt(
            operation="update-text",
            before=state_a,
            after=state_b,
        )
        with mock.patch.object(AUDIT, "stored_pr", return_value=state_a):
            result = AUDIT.audit(
                expected=self.expected,
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(result.status, "drift")
        self.assertEqual(result.receipt.sequence, 2)

    def test_record_api_rejects_arbitrary_stored_state_and_canonical_noop(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            RECEIPTS.record_verified_publication(
                root=self.receipt_directory,
                expected=self.expected,
                stored=self.stored(),
            )
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "actual state transition"):
            RECEIPTS.verified_transition(
                expected=self.expected,
                operation="update-text",
                preimage=self.stored(),
                final_reread=self.stored(),
                review_input_schema_version=self.review_input_schema_version,
                review_input_sha256=self.review_input_sha256,
                review=None,
                candidate=None,
            )

    def test_text_noop_stops_before_store_probe_or_mutation(self) -> None:
        same_body = Path(self.temporary_directory.name) / "same.md"
        same_body.write_text(self.body, encoding="utf-8")
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(UPDATE, "_stored_pr", return_value=self.stored()),
            mock.patch.object(UPDATE, "prepare_receipt_store") as prepare,
            mock.patch.object(UPDATE, "_run_mutation") as mutate,
        ):
            with self.assertRaisesRegex(UPDATE.PublicationError, "no-op"):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=hashlib.sha256(
                        self.title.encode("utf-8")
                    ).hexdigest(),
                    expected_body_sha256=hashlib.sha256(
                        self.body.encode("utf-8")
                    ).hexdigest(),
                    expected_draft=True,
                    title=self.title,
                    body_path=same_body,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        prepare.assert_not_called()
        mutate.assert_not_called()

    def test_default_root_is_continuous_across_record_and_audit(self) -> None:
        xdg = Path(self.temporary_directory.name) / "xdg-state"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
            root = RECEIPTS.prepare_receipt_store()
            RECEIPTS.prepare_receipt_ledger(root, self.expected)
            receipt = self.canonical_receipt(root=root)
            result = RECEIPTS.audit_publication(
                root=None,
                expected=self.expected,
                read_live=lambda: self.stored(),
            )
        self.assertEqual(root, xdg / "mergecraft/pr-publication-receipts")
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.receipt.receipt_id, receipt.receipt_id)
        override = Path(self.temporary_directory.name) / "migration-root"
        self.assertEqual(RECEIPTS.resolve_receipt_root(override), override)

    def test_bad_receipt_store_blocks_before_text_mutation(self) -> None:
        bad_root = Path(self.temporary_directory.name) / "bad-root"
        bad_root.mkdir(mode=0o700)
        bad_root.chmod(0o755)
        desired = Path(self.temporary_directory.name) / "changed.md"
        desired.write_text(self.body + "changed\n", encoding="utf-8")
        with (
            mock.patch.object(UPDATE, "_validate_body"),
            mock.patch.object(UPDATE, "_stored_pr", return_value=self.stored()),
            mock.patch.object(UPDATE, "_run_mutation") as mutate,
        ):
            with self.assertRaisesRegex(RECEIPTS.ReceiptError, "private owned"):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256=hashlib.sha256(
                        self.title.encode("utf-8")
                    ).hexdigest(),
                    expected_body_sha256=hashlib.sha256(
                        self.body.encode("utf-8")
                    ).hexdigest(),
                    expected_draft=True,
                    title=self.title,
                    body_path=desired,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=bad_root,
                )
        mutate.assert_not_called()

    def test_title_secret_gate_covers_create_update_ready_and_reconcile(self) -> None:
        secret = "ghp_123456789012345678901234567890"
        with mock.patch.object(CREATE, "_create") as create:
            with self.assertRaisesRegex(CREATE.PublicationError, "suspected") as raised:
                CREATE.publish(
                    repository=self.repository,
                    base=self.base,
                    base_oid=self.base_oid,
                    head=self.head,
                    head_oid=self.head_oid,
                    head_owner=self.head_owner,
                    head_repository=self.head_repository,
                    title=f"Credential {secret}",
                    template_path=self.template_path,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        create.assert_not_called()
        self.assertNotIn(secret, str(raised.exception))
        changed = Path(self.temporary_directory.name) / "changed.md"
        changed.write_text(self.body + "changed\n", encoding="utf-8")
        with mock.patch.object(UPDATE, "_run_mutation") as mutate:
            with self.assertRaisesRegex(UPDATE.PublicationError, "suspected"):
                UPDATE.update_text(
                    expected=self.expected,
                    expected_title_sha256="0" * 64,
                    expected_body_sha256="0" * 64,
                    expected_draft=True,
                    title=f"Credential {secret}",
                    body_path=changed,
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        mutate.assert_not_called()
        secret_state = self.stored(title=f"Credential {secret}")
        with mock.patch.object(UPDATE, "_stored_pr", return_value=secret_state):
            with self.assertRaisesRegex(UPDATE.PublicationError, "suspected"):
                UPDATE.mark_ready(
                    expected=self.expected,
                    expected_title_sha256=hashlib.sha256(
                        str(secret_state["title"]).encode("utf-8")
                    ).hexdigest(),
                    expected_body_sha256=hashlib.sha256(
                        self.body.encode("utf-8")
                    ).hexdigest(),
                    review_input_path=self.template_path,
                    review_mode="not-required",
                    review_bundle_root=None,
                    selected_specialists=[],
                    receipt_directory=self.receipt_directory,
                )
        with mock.patch.object(AUDIT, "stored_pr", return_value=secret_state):
            with self.assertRaisesRegex(AUDIT.PublicationError, "suspected"):
                AUDIT.reconcile(
                    expected=self.expected,
                    receipt_directory=self.receipt_directory,
                    review_input_path=self.template_path,
                )

    def test_secret_gate_covers_structured_provider_credentials(self) -> None:
        candidates = (
            "AKIAABCDEFGHIJKLMNOP",
            "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
            "xoxb-1234567890-abcdefghijklmnopqrstuvwxyz",
            "glpat-abcdefghijklmnopqrstuvwxyz012345",
            "npm_abcdefghijklmnopqrstuvwxyz012345",
            "AIzaabcdefghijklmnopqrstuvwxyz0123456789",
            "sk_live_abcdefghijklmnopqrstuvwxyz",
            "eyJabcdefghijk.abcdefghijkl.abcdefghijkl",
        )
        for candidate in candidates:
            with self.subTest(prefix=candidate.split("-", 1)[0]):
                self.assertIsNotNone(CREATE.suspected_secret_error(candidate))

    def test_production_clis_do_not_expose_receipt_root_override(self) -> None:
        for script in (
            SCRIPTS / "create_reviewable_pr.py",
            SCRIPTS / "update_reviewable_pr.py",
            SCRIPTS / "audit_reviewable_pr.py",
        ):
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("--receipt-directory", result.stdout)

    def test_oid_epoch_rollover_preserves_history_and_latest_authority(self) -> None:
        old_state = self.stored(title="old epoch")
        self.canonical_receipt(after=old_state)
        new_expected = STATE.ExpectedIdentity(
            repository=self.repository,
            pr_number=self.pr_number,
            base=self.base,
            base_oid="c" * 40,
            head=self.head,
            head_oid="d" * 40,
            head_owner=self.head_owner,
            head_repository=self.head_repository,
        )
        before = self.stored(
            title="new epoch before", baseRefOid="c" * 40, headRefOid="d" * 40
        )
        after = self.stored(
            title="new epoch after", baseRefOid="c" * 40, headRefOid="d" * 40
        )
        transition = RECEIPTS.verified_transition(
            expected=new_expected,
            operation="update-text",
            preimage=before,
            final_reread=after,
            review_input_schema_version=self.review_input_schema_version,
            review_input_sha256=self.review_input_sha256,
            review=transition_review(
                "not-required",
                self.transition_candidate(
                    operation="update-text",
                    final_state=after,
                    mode="not-required",
                    expected=new_expected,
                ).content_sha256,
                None,
            ),
            candidate=self.transition_candidate(
                operation="update-text",
                final_state=after,
                mode="not-required",
                expected=new_expected,
            ),
        )
        RECEIPTS.prepare_receipt_ledger(self.receipt_directory, new_expected)
        with RECEIPTS.receipt_ledger_lock(
            self.receipt_directory, new_expected
        ) as lease:
            latest = RECEIPTS.record_verified_publication(
                root=self.receipt_directory,
                transition=transition,
                lease=lease,
            )
        receipts = RECEIPTS.load_receipts(self.receipt_directory, new_expected)
        self.assertEqual([receipt.sequence for receipt in receipts], [1, 2])
        self.assertEqual(receipts[0].expected.head_oid, self.head_oid)
        self.assertEqual(latest.expected.head_oid, "d" * 40)
        audit = RECEIPTS.audit_publication(
            root=self.receipt_directory,
            expected=new_expected,
            read_live=lambda: after,
        )
        self.assertEqual(audit.status, "verified")

    def test_receipt_append_requires_active_exclusive_lease(self) -> None:
        transition = RECEIPTS.verified_transition(
            expected=self.expected,
            operation="create",
            preimage=self.transport(),
            final_reread=self.stored(),
            review_input_schema_version=self.review_input_schema_version,
            review_input_sha256=self.review_input_sha256,
            review=transition_review(
                "not-required",
                self.transition_candidate(
                    operation="create",
                    final_state=self.stored(),
                    mode="not-required",
                ).content_sha256,
                None,
            ),
            candidate=self.transition_candidate(
                operation="create",
                final_state=self.stored(),
                mode="not-required",
            ),
        )
        inactive = RECEIPTS.LedgerLease(
            root=self.receipt_directory,
            identity_key=f"ledger-{RECEIPTS._identity_key(self.expected)}",
            exclusive=True,
            active=False,
        )
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "active exclusive"):
            RECEIPTS.record_verified_publication(
                root=self.receipt_directory,
                transition=transition,
                lease=inactive,
            )

    def test_two_process_append_is_serialized_without_sequence_fork(self) -> None:
        child = """
import hashlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from publication_receipts import (
    prepare_receipt_ledger,
    receipt_ledger_lock,
    record_verified_publication,
    verified_transition,
)
from required_review import (
    PublicationCandidate,
    _identity_for,
    _required_profile,
    body_source_binding,
    validate_required_review,
)
from reviewable_pr_state import ExpectedIdentity
root = Path(sys.argv[2])
expected = ExpectedIdentity(
    'acme/app', 42, 'main', 'a' * 40, 'fork-owner:widget', 'b' * 40,
    'fork-owner', 'fork-owner/app-fork'
)
before = {
    'number': 42, 'url': expected.url, 'title': 'feat: widget',
    'body': 'transport', 'baseRefName': 'main', 'baseRefOid': 'a' * 40,
    'headRefName': 'widget', 'headRefOid': 'b' * 40,
    'headRepositoryOwner': {'login': 'fork-owner'},
    'headRepository': {'nameWithOwner': 'fork-owner/app-fork'},
    'isDraft': True, 'state': 'OPEN'
}
after = {**before, 'body': 'canonical'}
candidate_value = {
    'schema_version': 1, 'contract': 'mergecraft-publication-candidate-v1',
    'operation': 'update-text', 'repository': expected.repository,
    'pr_number': expected.pr_number,
    'base': {'ref': expected.base, 'oid': expected.base_oid},
    'head': {
        'ref': expected.head, 'oid': expected.head_oid,
        'owner': expected.head_owner, 'repository': expected.head_repository,
    },
    'title': after['title'],
    'body_source': body_source_binding(
        kind='body', raw=b'canonical', published='canonical',
        render_contract='literal-utf8-v1',
    ),
    'review_input': {
        'schema_version': 2, 'raw_sha256': 'e' * 64,
        'content_sha256': 'd' * 64,
    },
    'publication_profile': {
        'contract': 'mergecraft-publication-profile-v1',
        'review_mode': 'not-required', 'selected_specialists': [],
    },
}
candidate_sha = hashlib.sha256(json.dumps(
    candidate_value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
).encode('utf-8')).hexdigest()
candidate = PublicationCandidate(
    candidate_value, candidate_sha,
    {'kind': 'mergecraft-publication-candidate-v1',
     'value': 'sha256:' + candidate_sha, 'content_sha256': candidate_sha},
    _identity_for('mergecraft-review-input-v1', candidate_value['review_input']),
    _identity_for(
        'mergecraft-required-publication-review-profile-v2',
        _required_profile([]),
    ),
    b'canonical', 'canonical',
)
review_input = candidate.value['review_input']
review = validate_required_review(
    review_mode='not-required', review_bundle_root=None, candidate=candidate,
)
transition = verified_transition(
    expected=expected, operation='update-text', preimage=before, final_reread=after,
    review_input_schema_version=review_input['schema_version'],
    review_input_sha256=review_input['content_sha256'],
    review=review,
    candidate=candidate,
)
prepare_receipt_ledger(root, expected)
with receipt_ledger_lock(root, expected) as lease:
    time.sleep(0.1)
    record_verified_publication(root=root, transition=transition, lease=lease)
"""
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child,
                    str(SCRIPTS),
                    str(self.receipt_directory),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        receipts = RECEIPTS.load_receipts(self.receipt_directory, self.expected)
        self.assertEqual([receipt.sequence for receipt in receipts], [1, 2])
        self.assertLess(receipts[0].created_at, receipts[1].created_at)

    def test_backward_receipt_timestamp_fails_closed(self) -> None:
        state_a = self.stored(title="state A")
        self.canonical_receipt(after=state_a)
        state_b = self.stored(title="state B")
        self.canonical_receipt(operation="update-text", before=state_a, after=state_b)
        path = sorted(self.receipt_directory.rglob("*.json"))[1]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["created_at"] = "2000-01-01T00:00:00.000000Z"
        self.rewrite_receipt(path, payload)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "timestamps"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

    def test_reconcile_requires_identical_second_live_reread(self) -> None:
        with (
            mock.patch.object(
                AUDIT,
                "stored_pr",
                side_effect=[self.stored(), self.stored(title="concurrent")],
            ),
            mock.patch.object(
                AUDIT,
                "_validate_live_state",
                return_value=(
                    self.review_input_schema_version,
                    self.review_input_sha256,
                ),
            ),
            mock.patch.object(AUDIT, "record_reconciliation") as record,
        ):
            with self.assertRaisesRegex(AUDIT.PublicationError, "changed"):
                AUDIT.reconcile(
                    expected=self.expected,
                    receipt_directory=self.receipt_directory,
                    review_input_path=self.template_path,
                )
        record.assert_not_called()

    def test_failed_later_receipt_can_be_reconciled_after_older_canonical(self) -> None:
        state_a = self.stored(title="state A")
        self.canonical_receipt(after=state_a)
        state_b = self.stored(title="state B")
        with mock.patch.object(RECEIPTS.os, "link", side_effect=OSError("full")):
            with self.assertRaises(RECEIPTS.ReceiptError):
                self.canonical_receipt(
                    operation="update-text",
                    before=state_a,
                    after=state_b,
                )
        with mock.patch.object(AUDIT, "stored_pr", return_value=state_b):
            result = AUDIT.audit(
                expected=self.expected,
                receipt_directory=self.receipt_directory,
            )
        self.assertEqual(result.status, "drift")
        transition = RECEIPTS.verified_transition(
            expected=self.expected,
            operation="reconcile",
            preimage=state_b,
            final_reread=state_b,
            review_input_schema_version=self.review_input_schema_version,
            review_input_sha256=self.review_input_sha256,
            review=None,
            candidate=None,
        )
        with RECEIPTS.receipt_ledger_lock(
            self.receipt_directory, self.expected
        ) as lease:
            reconciled = RECEIPTS.record_reconciliation(
                root=self.receipt_directory,
                transition=transition,
                lease=lease,
            )
        self.assertEqual(reconciled.sequence, 2)
        self.assertEqual(reconciled.provenance, "reconciled-unreceipted")

    def test_failed_link_cleans_temp_and_strict_temp_is_non_evidence(self) -> None:
        with mock.patch.object(RECEIPTS.os, "link", side_effect=OSError("full")):
            with self.assertRaises(RECEIPTS.ReceiptError):
                self.canonical_receipt()
        self.assertFalse(list(self.receipt_directory.rglob("*.tmp")))
        ledger = next(
            path
            for path in self.receipt_directory.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
        pending = ledger / f".pending-{uuid.uuid4()}.tmp"
        pending.write_text("interrupted", encoding="utf-8")
        pending.chmod(0o600)
        self.assertEqual(
            RECEIPTS.load_receipts(self.receipt_directory, self.expected), []
        )
        unexpected = ledger / ".pending-invalid.tmp"
        unexpected.write_text("invalid", encoding="utf-8")
        unexpected.chmod(0o600)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "unexpected entry"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

    def test_disappearing_private_pending_temp_is_concurrent_non_evidence(
        self,
    ) -> None:
        RECEIPTS.prepare_receipt_ledger(self.receipt_directory, self.expected)
        ledger = next(
            path
            for path in self.receipt_directory.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
        pending = ledger / f".pending-{uuid.uuid4()}.tmp"
        pending.write_text("probe", encoding="utf-8")
        pending.chmod(0o600)
        original_lstat = Path.lstat

        def disappear_before_inspection(path: Path) -> os.stat_result:
            if path == pending:
                pending.unlink()
            return original_lstat(path)

        with mock.patch.object(Path, "lstat", new=disappear_before_inspection):
            self.assertEqual(
                RECEIPTS.load_receipts(self.receipt_directory, self.expected),
                [],
            )

    def test_strict_uuid_timestamp_filename_sequence_and_chain_validation(self) -> None:
        first = self.canonical_receipt()
        path = next(self.receipt_directory.rglob("*.json"))
        payload = first.as_json()
        payload["created_at"] = "2999-01-01T00:00:00.000000Z"
        path = self.rewrite_receipt(path, payload)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "future"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

        path.unlink()
        uuid_one = str(uuid.uuid1())
        payload = first.as_json()
        payload["receipt_id"] = uuid_one
        path = self.rewrite_receipt(path, payload)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "invalid value"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

        path.unlink()
        first = self.canonical_receipt()
        path = next(self.receipt_directory.rglob("*.json"))
        bad_name = path.with_name("00000009-" + path.name.split("-", 1)[1])
        path.rename(bad_name)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "filename"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

    def test_sequence_gap_and_predecessor_fork_fail_closed(self) -> None:
        state_a = self.stored(title="state A")
        self.canonical_receipt(after=state_a)
        state_b = self.stored(title="state B")
        second = self.canonical_receipt(
            operation="update-text", before=state_a, after=state_b
        )
        paths = sorted(self.receipt_directory.rglob("*.json"))
        paths[0].unlink()
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "sequence"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)

        payload = second.as_json()
        payload["sequence"] = 1
        payload["predecessor_sha256"] = "f" * 64
        self.rewrite_receipt(paths[1], payload)
        with self.assertRaisesRegex(RECEIPTS.ReceiptError, "predecessor"):
            RECEIPTS.load_receipts(self.receipt_directory, self.expected)


if __name__ == "__main__":
    unittest.main()
