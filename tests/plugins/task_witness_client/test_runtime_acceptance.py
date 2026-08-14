from __future__ import annotations

import subprocess
import sys
import time

from ._support import (
    CLIENT_ENVIRONMENT,
    ValidInvocationFixture,
    _TaskWitnessClientTestCase,
    bundle_identity,
    canonical_document,
    install_launcher_behavior,
    interpreter_identity,
)


class RuntimeAcceptanceTests(_TaskWitnessClientTestCase):
    def test_bundle_file_limit_accepts_exact_limit(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        for index in range(255):
            name = f"extra-{index:03d}"
            raw = b""
            fixture.bundle_files[name] = raw
            extra = fixture.bundle / name
            extra.write_bytes(raw)
            extra.chmod(0o600)

        fixture.expected_bundle_sha256 = bundle_identity(fixture.bundle_files)
        fixture._write_active_record()
        fixture._write_launcher()
        fixture._write_deployment_receipt()

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(len(fixture.bundle_files), 256)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)

    def test_bundle_file_limit_rejects_before_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_ran = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_ran,
            output=fixture.envelope_raw,
        )
        for index in range(256):
            extra = fixture.bundle / f"extra-{index:03d}"
            extra.write_bytes(b"")
            extra.chmod(0o600)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_ran.exists())

    def test_bundle_growth_past_file_limit_while_launcher_runs_is_rejected(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_started = self.root / "launcher-started"
        launcher_continue = self.root / "launcher-continue"
        install_launcher_behavior(
            fixture,
            "marker_gate",
            started=launcher_started,
            continuation=launcher_continue,
            deadline_seconds=5,
            output=fixture.envelope_raw,
        )
        process = subprocess.Popen(
            fixture.command("validate", "--bundle", str(fixture.bundle)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        try:
            deadline = time.monotonic() + 3
            while not launcher_started.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("launcher did not reach the mutation barrier")
                time.sleep(0.01)
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=5)
                self.fail(
                    "client exited before the mutation barrier: "
                    f"stdout={stdout!r}, stderr={stderr!r}"
                )
            for index in range(256):
                extra = fixture.bundle / f"extra-{index:03d}"
                extra.write_bytes(b"")
                extra.chmod(0o600)
            launcher_continue.write_text("continue", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertEqual(process.returncode, 70, stderr.decode())
        self.assertEqual(stdout, b"")

    def test_launcher_configuration_remains_data_only(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        marker = self.root / "configuration-marker"
        injected = self.root / "injected-code-ran"
        payload = f"'); __import__('pathlib').Path({str(injected)!r}).touch(); #\n"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            marker_content=payload,
            output=fixture.envelope_raw,
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(marker.read_text(encoding="utf-8"), payload)
        self.assertFalse(injected.exists())

    def test_runtime_generation_inventory_growth_while_launcher_runs_is_rejected(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_started = self.root / "launcher-started"
        launcher_continue = self.root / "launcher-continue"
        install_launcher_behavior(
            fixture,
            "marker_gate",
            started=launcher_started,
            continuation=launcher_continue,
            deadline_seconds=5,
            output=fixture.envelope_raw,
        )
        process = subprocess.Popen(
            fixture.command(
                "validate",
                "--bundle",
                str(fixture.bundle),
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        try:
            deadline = time.monotonic() + 3
            while not launcher_started.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("launcher did not reach the mutation barrier")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            unexpected = fixture.runtime_generation / "unexpected.py"
            unexpected.write_text("unexpected = True\n", encoding="utf-8")
            unexpected.chmod(0o600)
            launcher_continue.write_text("continue", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertEqual(process.returncode, 70, stderr.decode())
        self.assertEqual(stdout, b"")

    def test_historical_invocation_selects_the_receipt_authorized_context(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        historical_path, historical_digest, _ = fixture.add_historical_context()
        historical_anchor = {
            **fixture.expected_anchor,
            "trust_context_sha256": historical_digest,
            "historical": True,
        }
        historical_witness = {
            **fixture.witness,
            "trust_context_sha256": historical_digest,
            "historical": True,
        }
        historical_envelope_raw = canonical_document(
            {
                "contract": "task-witness-launch-envelope-v1",
                "anchor": historical_anchor,
                "witness": historical_witness,
            }
        )
        expected_launcher_arguments = [
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--trust-context",
            str(historical_path),
            "--historical",
        ]
        install_launcher_behavior(
            fixture,
            "expected_argv",
            expected=expected_launcher_arguments,
            output=historical_envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--historical",
            "--trust-context-sha256",
            historical_digest,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, historical_envelope_raw)

    def test_active_context_can_validate_its_own_historical_evidence(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        historical_envelope_raw = canonical_document(
            {
                "contract": "task-witness-launch-envelope-v1",
                "anchor": {
                    **fixture.expected_anchor,
                    "historical": True,
                },
                "witness": {
                    **fixture.witness,
                    "historical": True,
                },
            }
        )
        expected_launcher_arguments = [
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--trust-context",
            str(fixture.trust_context),
            "--historical",
        ]
        install_launcher_behavior(
            fixture,
            "expected_argv",
            expected=expected_launcher_arguments,
            output=historical_envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--historical",
            "--trust-context-sha256",
            fixture.trust_sha256,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, historical_envelope_raw)

    def test_launcher_witness_contract_drift_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "contract": "attacker-v1",
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_launcher_witness_extra_field_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "unexpected": True,
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_launcher_producer_extra_field_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "producer": {
                        **fixture.witness["producer"],
                        "unexpected": True,
                    },
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_launcher_missing_producer_validator_binding_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        producer = {
            key: value
            for key, value in fixture.witness["producer"].items()
            if key != "validator_contract"
        }
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "producer": producer,
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_launcher_validator_extra_field_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "validator": {
                        **fixture.witness["validator"],
                        "unexpected": True,
                    },
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_launcher_producer_and_validator_disagreement_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "validator": {
                        **fixture.witness["validator"],
                        "validator_id": "different-validator",
                    },
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_launcher_witness_must_be_authorized_by_the_selected_context(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "producer": {
                        **fixture.witness["producer"],
                        "producer_id": "unregistered-producer",
                        "implementation_sha256": "3" * 64,
                    },
                },
            }
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_every_complete_anchor_field_is_bound(self) -> None:
        cases = {
            "generation": "sha256-" + "f" * 64,
            "active_record_sha256": "f" * 64,
            "runtime_contract": "other-runtime-v1",
            "interpreter": {
                **interpreter_identity(),
                "version": {
                    **interpreter_identity()["version"],
                    "micro": sys.version_info.micro + 1,
                },
            },
            "public_release": {
                "repository": "nisavid/agents",
                "revision": "f" * 40,
            },
            "runtime_implementation_sha256": "f" * 64,
            "trust_context_sha256": "f" * 64,
            "bundle_sha256": "f" * 64,
            "historical": True,
        }
        for field, replacement in cases.items():
            with self.subTest(field=field):
                fixture = ValidInvocationFixture(self.root / field)
                fixture.replace_launcher_envelope(
                    {
                        **fixture.envelope,
                        "anchor": {
                            **fixture.expected_anchor,
                            field: replacement,
                        },
                    }
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 65, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_launcher_nonobject_projection_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "projection": ["not", "an", "object"],
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_owner_defined_projection_remains_opaque(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        projection = {
            "contract": "future-owner-projection-v99",
            "unexpected-to-task-witness": {
                "nested": [True, None, 7],
            },
        }
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "projection": projection,
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, fixture.envelope_raw)

    def test_launcher_fixed_identity_fields_are_strict(self) -> None:
        cases = {
            "producer_id_type": (
                {"producer_id": 42},
                {},
            ),
            "producer_contract_empty": (
                {"contract": ""},
                {},
            ),
            "producer_digest_invalid": (
                {"implementation_sha256": "not-a-digest"},
                {},
            ),
            "validator_id_token_invalid": (
                {"validator_id": "UPPER"},
                {"validator_id": "UPPER"},
            ),
            "validator_contract_empty": (
                {"validator_contract": ""},
                {"contract": ""},
            ),
            "validator_digest_invalid": (
                {"validator_implementation_sha256": "g" * 64},
                {"implementation_sha256": "g" * 64},
            ),
        }
        for name, (producer_changes, validator_changes) in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                fixture.replace_launcher_envelope(
                    {
                        **fixture.envelope,
                        "witness": {
                            **fixture.witness,
                            "producer": {
                                **fixture.witness["producer"],
                                **producer_changes,
                            },
                            "validator": {
                                **fixture.witness["validator"],
                                **validator_changes,
                            },
                        },
                    }
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 65, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_launcher_envelope_excessive_nesting_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        projection: dict[str, object] = {}
        for _ in range(101):
            projection = {"nested": projection}
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "projection": projection,
                },
            }
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_launcher_output_framing_is_strict(self) -> None:
        cases = {
            "leading-byte": lambda raw: b"x" + raw,
            "trailing-space": lambda raw: raw + b" ",
            "second-document": lambda raw: raw + b"{}\n",
            "invalid-utf8": lambda raw: b"\xff" + raw,
            "premature-eof": lambda raw: raw[:-1],
            "duplicate-key": lambda raw: raw.replace(
                b'"contract":"task-witness-launch-envelope-v1",',
                b'"contract":"task-witness-launch-envelope-v1",'
                b'"contract":"task-witness-launch-envelope-v1",',
                1,
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                raw = mutate(fixture.envelope_raw)
                install_launcher_behavior(
                    fixture,
                    "emit_output",
                    output=raw,
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 65, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_launcher_lone_surrogate_uses_the_canonical_document_error(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        raw = fixture.envelope_raw.replace(
            b'"contract":"task-witness-launch-envelope-v1"',
            b'"contract":"\\ud800"',
            1,
        )
        install_launcher_behavior(
            fixture,
            "emit_output",
            output=raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assert_diagnostic(
            result.stderr,
            message="launcher envelope must be one canonical JSON document",
            validator_code_executed="unknown",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action="do not retry; inspect the bundle and validator evidence",
        )

    def test_malformed_launcher_output_is_not_retried(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launch_count = self.root / "launch-count"
        install_launcher_behavior(
            fixture,
            "counted_output",
            count=launch_count,
            output=b"not-json\n",
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(launch_count.read_text(encoding="utf-8"), "1")

    def test_excessive_launcher_numeric_token_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "projection": {
                        "contract": "fixture-projection-v1",
                        "number": int("9" * 129),
                    },
                },
            }
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

        for name, exit_code, stderr in (
            ("nonzero-exit", 2, b""),
            ("nonempty-stderr", 0, b"child detail"),
        ):
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                install_launcher_behavior(
                    fixture,
                    "emit_output",
                    output=fixture.envelope_raw,
                    exit_code=exit_code,
                    stderr=stderr,
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 65, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assert_diagnostic(
                    result.stderr,
                    message="launcher rejected validation",
                    validator_code_executed="unknown",
                    active_state_changed="unknown",
                    current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
                    next_action="do not retry; inspect the bundle and validator evidence",
                )
