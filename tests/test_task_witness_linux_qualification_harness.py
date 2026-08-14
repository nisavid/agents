from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
