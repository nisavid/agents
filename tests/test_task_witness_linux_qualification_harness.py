from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY / "scripts" / "prepare_task_witness_linux_qualification.py"


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "task_witness_linux_qualification_preparation",
        HELPER,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("qualification preparation helper cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class TaskWitnessLinuxQualificationHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = load_helper()

    def write_elf(
        self,
        path: Path,
        interpreters: list[str],
        *,
        dynamic: bool = False,
    ) -> None:
        identifier = b"\x7fELF\x02\x01\x01" + (b"\0" * 9)
        program_count = len(interpreters) + int(dynamic)
        program_offset = 64 if program_count else 0
        segment_offset = 64 + (56 * program_count)
        program_headers: list[bytes] = []
        segments: list[bytes] = []
        if dynamic:
            program_headers.append(struct.pack("<IIQQQQQQ", 2, 4, 0, 0, 0, 0, 0, 8))
        for interpreter in interpreters:
            raw = interpreter.encode("utf-8") + b"\0"
            program_headers.append(
                struct.pack(
                    "<IIQQQQQQ",
                    3,
                    4,
                    segment_offset,
                    0,
                    0,
                    len(raw),
                    len(raw),
                    1,
                )
            )
            segments.append(raw)
            segment_offset += len(raw)
        header = struct.pack(
            "<16sHHIQQQIHHHHHH",
            identifier,
            3,
            62,
            1,
            0,
            program_offset,
            0,
            0,
            64,
            56,
            program_count,
            64,
            0,
            0,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header + b"".join(program_headers) + b"".join(segments))

    def test_content_document_binds_exact_canonical_unsigned_bytes(self) -> None:
        unsigned = {
            "schema_version": 1,
            "contract": "example-v1",
            "nested": {"z": 2, "a": 1},
        }
        value = self.helper.content_document(unsigned)
        self.assertEqual(
            value["content_sha256"],
            hashlib.sha256(self.helper.canonical_bytes(unsigned)).hexdigest(),
        )
        self.assertEqual(
            json.loads(self.helper.canonical_bytes(value)),
            value,
        )

    def test_create_new_writer_preserves_an_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "evidence.json"
            path.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                self.helper.write_create_new(path, {"new": True})
            self.assertEqual(path.read_bytes(), b"existing")

    def test_runtime_inventory_roles_main_and_loader_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executable = root / "python3.13"
            executable.write_bytes(b"not-an-elf")
            executable.chmod(0o755)
            loader_root = root / "lib" / "task-witness-loader"
            loader_root.mkdir(parents=True)
            loader = loader_root / "libc.so.6"
            loader.write_bytes(b"not-an-elf-either")
            main = self.helper.live_entry(executable, root, executable)
            retained = self.helper.live_entry(loader, root, executable)
            self.assertEqual(main["role"], "main-executable")
            self.assertEqual(retained["role"], "runtime-resource")
            self.assertEqual(main["sha256"], hashlib.sha256(b"not-an-elf").hexdigest())

    def test_patchelf_interpreter_requires_direct_elf_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            interpreter = "/opt/task-witness/runtime/lib/ld-linux-x86-64.so.2"
            executable = root / "executable"
            shared_object = root / "shared-object"
            multiple = root / "multiple"
            malformed = root / "malformed"
            self.write_elf(executable, [interpreter], dynamic=True)
            self.write_elf(shared_object, [], dynamic=True)
            self.write_elf(multiple, [interpreter, interpreter], dynamic=True)
            malformed.write_bytes(b"\x7fELFtruncated")

            success = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"{interpreter}\n",
                stderr="",
            )
            no_interpreter = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr=f"{self.helper.PATCHELF_NO_INTERPRETER_DIAGNOSTIC}\n",
            )
            with mock.patch.object(self.helper, "run", return_value=success):
                self.assertEqual(
                    self.helper.patchelf_interpreter(executable),
                    interpreter,
                )
            with mock.patch.object(
                self.helper,
                "run",
                return_value=no_interpreter,
            ):
                self.assertIsNone(self.helper.patchelf_interpreter(shared_object))
                with self.assertRaises(self.helper.PreparationError):
                    self.helper.patchelf_interpreter(executable)
            for path in (multiple, malformed):
                with (
                    self.subTest(path=path),
                    self.assertRaises(self.helper.PreparationError),
                ):
                    self.helper.patchelf_interpreter(path)

    def test_dynamic_elf_discovery_uses_headers_and_rejects_probe_errors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            shared_object = root / "shared-object.so"
            static_object = root / "static-object"
            self.write_elf(shared_object, [], dynamic=True)
            self.write_elf(static_object, [])
            no_interpreter = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr=f"{self.helper.PATCHELF_NO_INTERPRETER_DIAGNOSTIC}\n",
            )
            unexpected = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="patchelf: unexpected inspection failure\n",
            )

            with mock.patch.object(
                self.helper,
                "run",
                return_value=no_interpreter,
            ) as run:
                self.assertEqual(
                    self.helper.dynamic_elf_files(root),
                    [shared_object],
                )
            run.assert_called_once_with(
                [
                    "/usr/bin/patchelf",
                    "--print-interpreter",
                    str(shared_object),
                ],
                check=False,
            )
            with (
                mock.patch.object(
                    self.helper,
                    "run",
                    return_value=unexpected,
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.dynamic_elf_files(root)

    def test_dependency_audit_accepts_only_the_exact_host_loader_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            executable = runtime_root / "bin" / "python"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            internal_library = retained_loader.with_name("libc.so.6")
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            for path in (
                executable,
                retained_loader,
                internal_library,
                host_loader,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=str(retained_loader),
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_paths",
                    return_value=([host_loader, internal_library], "ldd trace\n"),
                ),
            ):
                audit = self.helper.audit_runtime_elf_dependencies(
                    executable,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

            self.assertEqual(audit["interpreter"], str(retained_loader))
            self.assertEqual(audit["ldd_loader_artifact"], str(host_loader))
            self.assertEqual(
                audit["resolved_paths"],
                [str(host_loader), str(internal_library)],
            )

    def test_dependency_audit_rejects_bad_interpreters_and_other_externals(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            executable = runtime_root / "bin" / "python"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            other_internal_loader = retained_loader.with_name("other-loader.so")
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            external_library = root / "host" / "libc.so.6"
            for path in (
                executable,
                retained_loader,
                other_internal_loader,
                host_loader,
                external_library,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            cases = {
                "relative-interpreter": (
                    "lib/task-witness-loader/ld-linux-x86-64.so.2",
                    [host_loader],
                ),
                "external-interpreter": (str(host_loader), [host_loader]),
                "unapproved-internal-interpreter": (
                    str(other_internal_loader),
                    [host_loader],
                ),
                "external-library": (
                    str(retained_loader),
                    [host_loader, external_library],
                ),
            }
            for name, (interpreter, dependencies) in cases.items():
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        self.helper,
                        "patchelf_interpreter",
                        return_value=interpreter,
                    ),
                    mock.patch.object(
                        self.helper,
                        "ldd_paths",
                        return_value=(dependencies, "ldd trace\n"),
                    ),
                    self.assertRaises(self.helper.PreparationError),
                ):
                    self.helper.audit_runtime_elf_dependencies(
                        executable,
                        runtime_root,
                        retained_loader,
                        host_loader,
                        1001,
                        1001,
                    )

            retained_loader.unlink()
            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=str(retained_loader),
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_paths",
                    return_value=([host_loader], "ldd trace\n"),
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.audit_runtime_elf_dependencies(
                    executable,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

    def test_dependency_audit_rejects_interpreter_inspection_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            executable = runtime_root / "bin" / "python"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            for path in (executable, retained_loader, host_loader):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            ldd = mock.Mock(return_value=([host_loader], "ldd trace\n"))
            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    side_effect=self.helper.PreparationError(
                        "interpreter inspection failed"
                    ),
                ),
                mock.patch.object(self.helper, "ldd_paths", ldd),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.audit_runtime_elf_dependencies(
                    executable,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )
            ldd.assert_not_called()

    def test_shared_object_dependency_audit_keeps_loader_handling_exact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            shared_object = runtime_root / "lib" / "libpython.so"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            lookalike_loader = root / "lookalike" / "ld-linux-x86-64.so.2"
            for path in (
                shared_object,
                retained_loader,
                host_loader,
                lookalike_loader,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_paths",
                    return_value=([host_loader], "ldd trace\n"),
                ),
            ):
                audit = self.helper.audit_runtime_elf_dependencies(
                    shared_object,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )
            self.assertIsNone(audit["interpreter"])

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_paths",
                    return_value=([lookalike_loader], "ldd trace\n"),
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.audit_runtime_elf_dependencies(
                    shared_object,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

    def build_installed_pyyaml(self, root: Path) -> Path:
        site_packages = root / "lib" / "python3.13" / "site-packages"
        files = {
            "_yaml/__init__.py": b"from yaml._yaml import *\n",
            "yaml/__init__.py": b"__version__ = '6.0.3'\n",
            "yaml/_yaml.cpython-313-x86_64-linux-gnu.so": b"\x7fELFupstream",
            "pyyaml-6.0.3.dist-info/METADATA": b"Name: PyYAML\nVersion: 6.0.3\n",
            "pyyaml-6.0.3.dist-info/WHEEL": (
                b"Wheel-Version: 1.0\n"
                b"Root-Is-Purelib: false\n"
                b"Tag: cp313-cp313-manylinux_2_17_x86_64\n"
                b"Tag: cp313-cp313-manylinux2014_x86_64\n"
                b"Tag: cp313-cp313-manylinux_2_28_x86_64\n"
            ),
        }
        for relative, raw in files.items():
            path = site_packages / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        record_relative = "pyyaml-6.0.3.dist-info/RECORD"
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        for relative, raw in sorted(files.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=")
            writer.writerow([relative, f"sha256={digest.decode()}", len(raw)])
        writer.writerow([record_relative, "", ""])
        (site_packages / record_relative).write_text(output.getvalue())
        return site_packages

    def test_pyyaml_record_validation_binds_every_installed_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            site_packages = self.build_installed_pyyaml(Path(raw_root))
            audit = self.helper.validate_installed_wheel(site_packages)
            self.assertEqual(audit["distribution"], "PyYAML")
            self.assertEqual(audit["version"], "6.0.3")
            self.assertEqual(
                audit["native_extensions"],
                ["yaml/_yaml.cpython-313-x86_64-linux-gnu.so"],
            )

    def test_pyyaml_record_validation_rejects_an_unhashed_member(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            site_packages = self.build_installed_pyyaml(Path(raw_root))
            record = site_packages / "pyyaml-6.0.3.dist-info" / "RECORD"
            rows = list(csv.reader(io.StringIO(record.read_text())))
            rows[0][1] = ""
            output = io.StringIO(newline="")
            csv.writer(output, lineterminator="\n").writerows(rows)
            record.write_text(output.getvalue())
            with self.assertRaises(self.helper.PreparationError):
                self.helper.validate_installed_wheel(site_packages)

    def test_pyyaml_record_validation_rejects_unsafe_duplicate_and_changed_rows(
        self,
    ) -> None:
        cases = ("unsafe", "duplicate", "hash-mismatch", "wrong-native-tag")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                site_packages = self.build_installed_pyyaml(Path(raw_root))
                record = site_packages / "pyyaml-6.0.3.dist-info" / "RECORD"
                rows = list(csv.reader(io.StringIO(record.read_text())))
                if case == "unsafe":
                    rows[0][0] = "../escape"
                elif case == "duplicate":
                    rows.insert(1, list(rows[0]))
                elif case == "hash-mismatch":
                    (site_packages / "yaml" / "__init__.py").write_bytes(b"changed")
                else:
                    original = "yaml/_yaml.cpython-313-x86_64-linux-gnu.so"
                    replacement = "yaml/_yaml.cpython-313-x86_64-linux-musl.so"
                    (site_packages / original).rename(site_packages / replacement)
                    for row in rows:
                        if row[0] == original:
                            row[0] = replacement
                            break
                output = io.StringIO(newline="")
                csv.writer(output, lineterminator="\n").writerows(rows)
                record.write_text(output.getvalue())
                with self.assertRaises(self.helper.PreparationError):
                    self.helper.validate_installed_wheel(site_packages)

    def test_pyyaml_record_rebind_changes_only_the_native_extension_row(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            site_packages = self.build_installed_pyyaml(Path(raw_root))
            before = self.helper.validate_installed_wheel(site_packages)
            native = site_packages / "yaml" / "_yaml.cpython-313-x86_64-linux-gnu.so"
            native.write_bytes(b"\x7fELFsealed")
            after = self.helper.finalize_installed_wheel(site_packages, before)
            self.assertEqual(
                after["transformed_paths"],
                [native.relative_to(site_packages).as_posix()],
            )
            self.assertNotEqual(
                after["upstream_record_sha256"],
                after["final_record_sha256"],
            )
            dispositions = {
                entry["path"]: entry["disposition"] for entry in after["entries"]
            }
            self.assertEqual(
                dispositions[native.relative_to(site_packages).as_posix()],
                "operator-rewritten-elf-and-record-rebound",
            )

    def test_parser_requires_an_explicit_subcommand(self) -> None:
        with self.assertRaises(SystemExit):
            self.helper.parser().parse_args([])

    def test_context_capture_preserves_the_explicit_clean_boundary(self) -> None:
        forwarded = {
            key: f"observed-{key.lower()}" for key in self.helper.GITHUB_CONTEXT_FIELDS
        }
        forwarded.update(
            {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF": self.helper.EXPECTED_HARNESS_REF,
                "GITHUB_REPOSITORY": self.helper.EXPECTED_REPOSITORY,
                "GITHUB_SHA": "1" * 40,
                "ImageVersion": "",
                "RUNNER_ARCH": "X64",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "UNRELATED_AMBIENT_VALUE": "must-not-cross",
            }
        )
        expected = {
            key: forwarded[key]
            for key in self.helper.GITHUB_CONTEXT_FIELDS
            if forwarded[key]
        }

        with tempfile.TemporaryDirectory() as raw_root:
            output = Path(raw_root) / "github-actions-context.json"
            with mock.patch.object(self.helper, "require_root"):
                self.helper.capture_context(output, environment=forwarded)

            document = json.loads(output.read_bytes())
            self.assertEqual(document["context"], expected)
            self.assertEqual(
                document["content_sha256"],
                hashlib.sha256(
                    self.helper.canonical_bytes(
                        {
                            "schema_version": 1,
                            "contract": "task-witness-github-actions-context-v1",
                            "context": expected,
                        }
                    )
                ).hexdigest(),
            )

    def test_bootstrap_helper_survives_a_nontraversable_checkout_ancestor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            checkout = root / "private-checkout"
            source = checkout / "scripts" / HELPER.name
            source.parent.mkdir(parents=True)
            source.write_bytes(HELPER.read_bytes())
            source.chmod(0o555)
            bootstrap = root / "bootstrap"
            bootstrap.mkdir(mode=0o755)
            retained = bootstrap / HELPER.name

            stage = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(HELPER),
                    "stage-bootstrap-helper",
                    "--source",
                    str(source),
                    "--output",
                    str(retained),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(stage.returncode, 0, stage.stderr)
            self.assertEqual(retained.read_bytes(), source.read_bytes())
            self.assertEqual(retained.stat().st_uid, os.geteuid())
            self.assertEqual(retained.stat().st_gid, os.getegid())
            self.assertEqual(retained.stat().st_mode & 0o777, 0o555)

            retained_raw = retained.read_bytes()
            replacement = source.with_name("replacement.py")
            replacement.write_bytes(b"replacement")
            replacement.chmod(0o555)
            restage = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(HELPER),
                    "stage-bootstrap-helper",
                    "--source",
                    str(replacement),
                    "--output",
                    str(retained),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(restage.returncode, 0)
            self.assertEqual(retained.read_bytes(), retained_raw)

            checkout.chmod(0)
            try:
                with self.assertRaises(PermissionError):
                    subprocess.run(
                        [str(source), "--help"],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                runnable = subprocess.run(
                    [str(retained), "--help"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            finally:
                checkout.chmod(0o755)
            self.assertEqual(runnable.returncode, 0, runnable.stderr)

    def test_artifact_manifest_replays_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            workspace = Path(raw_root)
            artifact_root = workspace / "artifact"
            artifact_root.mkdir()
            (artifact_root / "nested").mkdir()
            (artifact_root / "a.txt").write_bytes(b"alpha")
            (artifact_root / "nested" / "b.txt").write_bytes(b"beta")
            manifest = workspace / "SHA256SUMS.external"

            self.helper.write_artifact_manifest(artifact_root, manifest)
            self.helper.verify_artifact_manifest(artifact_root, manifest)
            expected = (
                f"{hashlib.sha256(b'alpha').hexdigest()}  ./a.txt\n"
                f"{hashlib.sha256(b'beta').hexdigest()}  ./nested/b.txt\n"
            ).encode()
            self.assertEqual(manifest.read_bytes(), expected)

            retained_manifest = artifact_root / "SHA256SUMS"
            manifest.replace(retained_manifest)
            self.helper.verify_artifact_manifest(artifact_root, retained_manifest)
            (artifact_root / "a.txt").write_bytes(b"changed")
            with self.assertRaises(self.helper.PreparationError):
                self.helper.verify_artifact_manifest(
                    artifact_root,
                    retained_manifest,
                )

    def test_artifact_manifest_rejects_symlinks_and_self_inclusion(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            workspace = Path(raw_root)
            artifact_root = workspace / "artifact"
            artifact_root.mkdir()
            (artifact_root / "value").write_bytes(b"value")
            (artifact_root / "link").symlink_to("value")
            with self.assertRaises(self.helper.PreparationError):
                self.helper.write_artifact_manifest(
                    artifact_root,
                    workspace / "SHA256SUMS.external",
                )
            (artifact_root / "link").unlink()
            with self.assertRaises(self.helper.PreparationError):
                self.helper.write_artifact_manifest(
                    artifact_root,
                    artifact_root / "SHA256SUMS",
                )


if __name__ == "__main__":
    unittest.main()
