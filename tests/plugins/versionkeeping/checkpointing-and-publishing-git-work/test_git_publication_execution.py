from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import shlex
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_DIR = (
    REPOSITORY / "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work"
)
SCRIPTS = SKILL_DIR / "scripts"
CLI = SCRIPTS / "execute_git_publication.py"
sys.path.insert(0, str(SCRIPTS))

import git_publication.adapter as adapter  # noqa: E402
import git_publication.execution as execution  # noqa: E402
import execute_git_publication as execution_cli  # noqa: E402
from git_publication.adapter import plan_repository  # noqa: E402
from git_publication.execution import (  # noqa: E402
    MalformedPlan,
    ReviewedPlanDigestMismatch,
    execute_repository,
    load_plan_json,
    validate_ready_plan,
)


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def write_credential_helper(
    path: Path,
    username: str,
    password: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = get ]; then\n"
        f"  printf '%s\\n' 'username={username}' 'password={password}'\n"
        "fi\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def request(start: str, source: str) -> dict:
    return {
        "schema_version": 2,
        "start_head": start,
        "source_sha": source,
        "task_owned_commits": [source],
        "adopted_commits": [],
        "removal_authorized_commits": [],
        "explicit_destination": {
            "remote": "publish",
            "ref": "refs/heads/topic",
        },
        "default_branch_policy": None,
        "allow_create": False,
        "creation_base_ref": None,
    }


def reviewed_plan(plan: dict) -> tuple[bytes, str]:
    plan_bytes = (
        json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    return plan_bytes, digest


def execute(repo: Path, plan: dict) -> dict:
    plan_bytes, digest = reviewed_plan(plan)
    return execute_repository(repo, plan_bytes, digest)


@contextmanager
def authenticated_https_endpoint(
    root: Path,
    username: str,
    password: str,
    advertised_sha: str,
    *,
    openssl_executable: str | Path = "/usr/bin/openssl",
    openssl_env: dict[str, str] | None = None,
):
    openssl_config = root / "openssl.cnf"
    certificate = root / "certificate.pem"
    private_key = root / "private-key.pem"
    openssl_config.write_text(
        "[req]\n"
        "distinguished_name=subject\n"
        "x509_extensions=extensions\n"
        "prompt=no\n"
        "[subject]\n"
        "CN=127.0.0.1\n"
        "[extensions]\n"
        "subjectAltName=IP:127.0.0.1\n"
        "keyUsage=digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            str(openssl_executable),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-config",
            str(openssl_config),
        ],
        env=openssl_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    expected_authorization = "Basic " + base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    authorized = threading.Event()

    def packet(payload: bytes) -> bytes:
        return f"{len(payload) + 4:04x}".encode("ascii") + payload

    service = b"# service=git-upload-pack\n"
    head = (
        f"{advertised_sha} HEAD\0symref=HEAD:refs/heads/main\n".encode("ascii")
    )
    branch = f"{advertised_sha} refs/heads/main\n".encode("ascii")
    advertisement = (
        packet(service) + b"0000" + packet(head) + packet(branch) + b"0000"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization") != expected_authorization:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="versionkeeping"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            authorized.set()
            body = advertisement
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/x-git-upload-pack-advertisement",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, private_key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"https://{host}:{port}/repository.git", certificate, authorized
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_credential_helper(
    helper: str,
    operation: str,
    credential: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [helper, operation],
        env=env,
        input=credential,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


def credential_protocol_fields(output: str) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in output.splitlines()
        if "=" in line
    )


class PublicationExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "topic", str(self.repo))
        self.start = commit(self.repo, "base")
        git(self.repo, "remote", "add", "publish", str(self.remote))
        git(self.repo, "push", "publish", f"{self.start}:refs/heads/topic")
        git(self.repo, "push", "publish", f"{self.start}:refs/heads/main")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.source = commit(self.repo, "change")
        self.plan = plan_repository(self.repo, request(self.start, self.source))
        self.assertEqual(self.plan["status"], "ready")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _linux_https_closed_config(
        self,
        trusted_helper: str,
    ) -> list[tuple[str, str]]:
        with adapter.GitRepository(self.repo) as repository:
            with mock.patch.object(adapter.sys, "platform", "linux"):
                with mock.patch.object(
                    repository,
                    "output",
                    return_value=str(Path(trusted_helper).parent),
                ):
                    with mock.patch.object(
                        adapter,
                        "_trusted_root_owned_executable",
                        side_effect=lambda path, **_kwargs: (
                            trusted_helper
                            if path == Path(trusted_helper)
                            else (_ for _ in ()).throw(OSError("untrusted"))
                        ),
                    ):
                        repository.enable_https_credentials(
                            "https://github.com/example/repository.git"
                        )

            config_count = int(repository.env["GIT_CONFIG_COUNT"])
            return [
                (
                    repository.env[f"GIT_CONFIG_KEY_{index}"],
                    repository.env[f"GIT_CONFIG_VALUE_{index}"],
                )
                for index in range(config_count)
            ]

    def test_macos_https_uses_only_the_trusted_osxkeychain_provider(self) -> None:
        trusted_helper = (
            "/Library/Developer/CommandLineTools/usr/libexec/git-core/"
            "git-credential-osxkeychain"
        )
        secret = "credential-secret-9fb4c7"

        with adapter.GitRepository(self.repo) as repository:
            with mock.patch.object(adapter.sys, "platform", "darwin"):
                with mock.patch.object(
                    repository,
                    "output",
                    return_value=str(Path(trusted_helper).parent),
                ):
                    with mock.patch.object(
                        adapter,
                        "_require_trusted_credential_helper",
                        return_value=trusted_helper,
                    ) as require_trusted:
                        with mock.patch.dict(
                            os.environ,
                            {"VERSIONKEEPING_TEST_CREDENTIAL": secret},
                            clear=False,
                        ):
                            repository.enable_https_credentials(
                                "https://github.com/example/repository.git"
                            )

            config_count = int(repository.env["GIT_CONFIG_COUNT"])
            closed_config = [
                (
                    repository.env[f"GIT_CONFIG_KEY_{index}"],
                    repository.env[f"GIT_CONFIG_VALUE_{index}"],
                )
                for index in range(config_count)
            ]

        require_trusted.assert_called_once_with(Path(trusted_helper))
        self.assertEqual(
            [value for key, value in closed_config if key == "credential.helper"],
            ["", trusted_helper],
        )
        self.assertNotIn(secret, json.dumps(closed_config))

    def test_linux_https_uses_trusted_memory_only_cache(self) -> None:
        trusted_helper = "/usr/lib/git-core/git-credential-cache"
        closed_config = self._linux_https_closed_config(trusted_helper)

        self.assertEqual(
            [value for key, value in closed_config if key == "credential.helper"],
            ["", trusted_helper],
        )
        self.assertNotIn("credential.credentialStore", dict(closed_config))

    def test_macos_accepts_the_apple_signed_helper_when_ancestry_is_mutable(
        self,
    ) -> None:
        helper = self.root.resolve() / adapter.MACOS_CREDENTIAL_HELPER
        helper.write_bytes(b"signed helper")
        helper.chmod(0o755)
        codesign = "/usr/bin/codesign"

        def trust_executable(path, **_kwargs):
            if path == helper:
                raise OSError("runner-managed Xcode ancestry")
            if path == adapter.MACOS_CODESIGN:
                return codesign
            raise AssertionError(f"unexpected trust candidate: {path}")

        with mock.patch.object(
            adapter,
            "_trusted_root_owned_executable",
            side_effect=trust_executable,
        ):
            with mock.patch.object(
                adapter.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run:
                trusted = adapter._require_trusted_credential_helper(helper)

        self.assertEqual(trusted, str(helper.resolve()))
        self.assertEqual(run.call_args.args[0][0], codesign)
        self.assertIn("anchor apple", " ".join(run.call_args.args[0]))
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin"})

    def test_macos_rejects_a_helper_without_the_apple_signature(self) -> None:
        helper = self.root.resolve() / adapter.MACOS_CREDENTIAL_HELPER
        helper.write_bytes(b"unsigned helper")
        helper.chmod(0o755)

        def trust_executable(path, **_kwargs):
            if path == helper:
                raise OSError("mutable ancestry")
            return str(path)

        with mock.patch.object(
            adapter,
            "_trusted_root_owned_executable",
            side_effect=trust_executable,
        ):
            with mock.patch.object(
                adapter.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ):
                with self.assertRaises(adapter.PolicyGate) as raised:
                    adapter._require_trusted_credential_helper(helper)

        self.assertEqual(
            raised.exception.code,
            "HTTPS_CREDENTIAL_PROVIDER_UNAVAILABLE",
        )

    def test_linux_provider_admits_only_a_root_owned_package_link(self) -> None:
        exec_path = self.root / "git-core"
        exec_path.mkdir()
        candidate = exec_path / adapter.LINUX_CREDENTIAL_HELPER
        candidate.symlink_to("git")
        observed = []

        def trust_executable(path, **kwargs):
            observed.append((path, kwargs))
            if path == candidate and kwargs.get("allow_root_owned_symlink"):
                return str(candidate)
            raise OSError("untrusted")

        with mock.patch.object(
            adapter,
            "_trusted_root_owned_executable",
            side_effect=trust_executable,
        ):
            trusted = adapter._require_linux_credential_provider(exec_path)

        self.assertEqual(trusted, str(candidate))
        self.assertTrue(observed[0][1]["allow_root_owned_symlink"])

    def test_windows_https_uses_gcm_from_the_trusted_git_installation(self) -> None:
        git_root = self.root / "Program Files" / "Git"
        git_executable = git_root / "cmd" / "git.exe"
        trusted_helper = (
            git_root / "mingw64" / "bin" / "git-credential-manager.exe"
        )
        git_executable.parent.mkdir(parents=True)
        trusted_helper.parent.mkdir(parents=True)
        git_executable.write_bytes(b"git")
        trusted_helper.write_bytes(b"gcm")

        with adapter.GitRepository(self.repo) as repository:
            repository.git_executable = str(git_executable)
            repository._windows_git_root = git_root.resolve()
            with mock.patch.object(adapter.sys, "platform", "win32"):
                with mock.patch.object(
                    repository,
                    "_configured_classes",
                    return_value=set(),
                ):
                    with mock.patch.object(
                        repository,
                        "output",
                        return_value=str(
                            git_root / "mingw64" / "libexec" / "git-core"
                        ),
                    ):
                        repository.enable_https_credentials(
                            "https://github.com/example/repository.git"
                        )

            config_count = int(repository.env["GIT_CONFIG_COUNT"])
            closed_config = [
                (
                    repository.env[f"GIT_CONFIG_KEY_{index}"],
                    repository.env[f"GIT_CONFIG_VALUE_{index}"],
                )
                for index in range(config_count)
            ]

        helpers = [
            value for key, value in closed_config if key == "credential.helper"
        ]
        self.assertEqual(helpers[0], "")
        self.assertEqual(
            helpers[1],
            str(trusted_helper.resolve()).replace(" ", "\\ "),
        )
        self.assertIn(
            ("credential.credentialStore", "wincredman"),
            closed_config,
        )

    def test_windows_repository_uses_only_the_bundled_git_runtime(self) -> None:
        program_files = self.root.resolve() / "Program Files"
        git_root = program_files / "Git"
        executables = {
            "git": git_root / "cmd" / "git.exe",
            "ssh": git_root / "usr" / "bin" / "ssh.exe",
            "false": git_root / "usr" / "bin" / "false.exe",
            "shell": git_root / "usr" / "bin" / "sh.exe",
        }
        for executable in executables.values():
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"trusted")
        (git_root / "mingw64" / "bin").mkdir(parents=True)

        with mock.patch.object(adapter.sys, "platform", "win32"):
            with mock.patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(program_files),
                    "ProgramFiles(x86)": str(self.root / "Program Files (x86)"),
                    "LOCALAPPDATA": str(self.root / "LocalAppData"),
                },
                clear=False,
            ):
                with adapter.GitRepository(self.repo) as repository:
                    observed_git = repository.git_executable
                    observed_env = repository.env

        self.assertEqual(observed_git, str(executables["git"].resolve()))
        self.assertEqual(
            observed_env["GIT_ASKPASS"],
            str(executables["false"].resolve()).replace(" ", "\\ "),
        )
        self.assertEqual(
            observed_env["SHELL"],
            str(executables["shell"].resolve()),
        )
        self.assertIn(
            str(executables["ssh"].resolve()).replace(" ", "\\ "),
            observed_env["GIT_SSH_COMMAND"],
        )
        self.assertEqual(
            observed_env["PATH"],
            ";".join(
                str((git_root / relative).resolve())
                for relative in ("cmd", "mingw64/bin", "usr/bin")
            ),
        )

    def test_windows_repository_rejects_runtime_identity_drift(self) -> None:
        program_files = self.root.resolve() / "Program Files"
        git_root = program_files / "Git"
        executables = {
            name: git_root / relative
            for name, relative in adapter.WINDOWS_GIT_RUNTIME.items()
        }
        for executable in executables.values():
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"trusted")
        (git_root / "mingw64" / "bin").mkdir(parents=True)

        with mock.patch.object(adapter.sys, "platform", "win32"):
            with mock.patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(program_files),
                    "ProgramFiles(x86)": str(self.root / "missing-x86"),
                },
                clear=False,
            ):
                repository = adapter.GitRepository(self.repo)

        try:
            executables["git"].unlink()
            executables["git"].write_bytes(b"replacement")
            with self.assertRaises(adapter.PolicyGate) as raised:
                repository._verify_windows_runtime_identity()
        finally:
            repository.close()

        self.assertEqual(
            raised.exception.code,
            "TRUSTED_WINDOWS_RUNTIME_CHANGED",
        )

    def test_windows_repository_never_mixes_registered_installations(self) -> None:
        program_files = self.root.resolve() / "Program Files"
        program_files_x86 = self.root.resolve() / "Program Files (x86)"
        git_root = program_files / "Git"
        git_root_x86 = program_files_x86 / "Git"

        git_executable = git_root / adapter.WINDOWS_GIT_RUNTIME["git"]
        git_executable.parent.mkdir(parents=True)
        git_executable.write_bytes(b"git-only")
        (git_root / "mingw64" / "bin").mkdir(parents=True)
        (git_root / "usr" / "bin").mkdir(parents=True)

        for name in ("ssh", "false", "shell"):
            executable = git_root_x86 / adapter.WINDOWS_GIT_RUNTIME[name]
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"support-only")
        (git_root_x86 / "cmd").mkdir(parents=True)
        (git_root_x86 / "mingw64" / "bin").mkdir(parents=True)

        with mock.patch.object(adapter.sys, "platform", "win32"):
            with mock.patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(program_files),
                    "ProgramFiles(x86)": str(program_files_x86),
                    "LOCALAPPDATA": str(self.root / "missing-local"),
                },
                clear=False,
            ):
                with self.assertRaises(adapter.PolicyGate) as raised:
                    adapter.GitRepository(self.repo)

        self.assertEqual(
            raised.exception.code,
            "TRUSTED_WINDOWS_RUNTIME_UNAVAILABLE",
        )

    def test_windows_registered_git_roots_ignore_current_user(self) -> None:
        observed_hives = []
        fake_key = mock.MagicMock()
        fake_winreg = mock.MagicMock()
        fake_winreg.HKEY_LOCAL_MACHINE = 1
        fake_winreg.HKEY_CURRENT_USER = 2
        fake_winreg.KEY_WOW64_64KEY = 0x0100
        fake_winreg.KEY_WOW64_32KEY = 0x0200
        fake_winreg.KEY_READ = 0x0001
        fake_winreg.REG_SZ = 1

        def open_key(hive, *_args):
            observed_hives.append(hive)
            return fake_key

        fake_winreg.OpenKey.side_effect = open_key
        fake_winreg.QueryValueEx.return_value = (r"C:\Program Files\Git", 1)

        with mock.patch.dict(sys.modules, {"winreg": fake_winreg}):
            adapter._windows_registered_git_roots()

        self.assertNotIn(fake_winreg.HKEY_CURRENT_USER, observed_hives)

    def test_windows_git_root_must_be_the_machine_program_files_install(self) -> None:
        custom_git_root = self.root.resolve() / "Custom" / "Git"
        custom_git_root.mkdir(parents=True)
        program_files = self.root.resolve() / "Program Files"

        with mock.patch.object(adapter.os, "name", "nt"):
            with mock.patch.object(
                adapter,
                "_windows_registered_git_roots",
                return_value=[custom_git_root],
            ):
                with mock.patch.object(
                    adapter,
                    "_windows_machine_program_files_roots",
                    return_value=(program_files,),
                    create=True,
                ):
                    roots = adapter._windows_git_install_roots()

        self.assertEqual(roots, ())

    def test_windows_bundle_rejects_a_mutable_acl(self) -> None:
        git_root = self.root.resolve() / "Program Files" / "Git"
        executable = git_root / adapter.WINDOWS_GIT_RUNTIME["git"]
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"git")

        with mock.patch.object(
            adapter,
            "_windows_path_has_protected_acl",
            side_effect=OSError("mutable ACL"),
            create=True,
        ):
            with self.assertRaises(OSError):
                adapter._trusted_windows_bundled_executable(
                    executable,
                    git_root,
                )

    def test_windows_acl_mask_contains_only_atomic_mutation_rights(self) -> None:
        self.assertNotIn("Modify", adapter.WINDOWS_MUTATION_RIGHTS)
        self.assertNotIn("FullControl", adapter.WINDOWS_MUTATION_RIGHTS)
        self.assertIn("WriteData", adapter.WINDOWS_MUTATION_RIGHTS)
        self.assertIn(
            "DeleteSubdirectoriesAndFiles",
            adapter.WINDOWS_MUTATION_RIGHTS,
        )

    def test_windows_bundle_checks_the_program_files_trust_anchor(self) -> None:
        git_root = self.root.resolve() / "Program Files" / "Git"
        executable = git_root / adapter.WINDOWS_GIT_RUNTIME["git"]
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"git")
        observed = []

        with mock.patch.object(
            adapter,
            "_windows_path_has_protected_acl",
            side_effect=lambda path, **kwargs: observed.append((path, kwargs)),
        ):
            adapter._trusted_windows_bundled_executable(
                executable,
                git_root,
            )

        self.assertIn((git_root.parent, {}), observed)
        self.assertIn(
            (Path(git_root.anchor), {"replacement_only": True}),
            observed,
        )

    def test_windows_repository_rejects_command_shaped_install_roots(self) -> None:
        program_files = self.root.resolve() / "Program Files;untrusted"
        git_root = program_files / "Git"
        for relative in adapter.WINDOWS_GIT_RUNTIME.values():
            executable = git_root / relative
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"untrusted")

        with mock.patch.object(adapter.sys, "platform", "win32"):
            with mock.patch.dict(
                os.environ,
                {
                    "ProgramFiles": str(program_files),
                    "ProgramFiles(x86)": str(self.root / "missing-x86"),
                    "LOCALAPPDATA": str(self.root / "missing-local"),
                },
                clear=False,
            ):
                with self.assertRaises(adapter.PolicyGate) as raised:
                    adapter.GitRepository(self.repo)

        self.assertEqual(
            raised.exception.code,
            "TRUSTED_WINDOWS_RUNTIME_UNAVAILABLE",
        )

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS")
    def test_system_macos_osxkeychain_provider_is_trusted(self) -> None:
        with adapter.GitRepository(self.repo) as repository:
            repository.enable_https_credentials(
                "https://github.com/example/repository.git"
            )
            config_count = int(repository.env["GIT_CONFIG_COUNT"])
            helpers = [
                repository.env[f"GIT_CONFIG_VALUE_{index}"]
                for index in range(config_count)
                if repository.env[f"GIT_CONFIG_KEY_{index}"]
                == "credential.helper"
            ]

        self.assertEqual(helpers[0], "")
        self.assertEqual(
            Path(helpers[1]).name,
            "git-credential-osxkeychain",
        )

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux")
    def test_system_linux_credential_cache_is_trusted_and_executable(self) -> None:
        with adapter.GitRepository(self.repo) as repository:
            exec_path = Path(repository.output(["--exec-path"]))
            helper = adapter._require_linux_credential_provider(exec_path)
            repository.enable_https_credentials(
                "https://github.com/example/repository.git"
            )
            completed = run_credential_helper(
                helper,
                "get",
                "protocol=https\nhost=cache-probe.invalid\n\n",
                repository.env,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        config_count = int(repository.env["GIT_CONFIG_COUNT"])
        self.assertEqual(
            repository.env[f"GIT_CONFIG_VALUE_{config_count - 1}"],
            adapter._credential_helper_config(helper),
        )

    @unittest.skipUnless(sys.platform == "win32", "requires Windows")
    def test_system_windows_gcm_provider_is_trusted_and_executable(self) -> None:
        with adapter.GitRepository(self.repo) as repository:
            self.assertIsNotNone(repository._windows_git_root)
            helper = adapter._require_windows_credential_provider(
                repository._windows_git_root
            )
            repository.enable_https_credentials(
                "https://github.com/example/repository.git"
            )
            completed = subprocess.run(
                [helper, "--version"],
                cwd=self.repo,
                env=repository.env,
                stdin=subprocess.DEVNULL,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        config_count = int(repository.env["GIT_CONFIG_COUNT"])
        self.assertEqual(
            repository.env[f"GIT_CONFIG_VALUE_{config_count - 1}"],
            adapter._credential_helper_config(helper),
        )

    @unittest.skipUnless(sys.platform == "win32", "requires Windows")
    def test_windows_acl_probe_rejects_a_user_writable_path(self) -> None:
        candidate = self.root / "user-writable.exe"
        candidate.write_bytes(b"mutable")

        with self.assertRaises(OSError):
            adapter._windows_path_has_protected_acl(candidate)

    @unittest.skipUnless(
        sys.platform == "darwin"
        or sys.platform.startswith("linux")
        or sys.platform == "win32",
        "requires macOS, Linux, or Windows",
    )
    def test_system_provider_authenticates_https_and_is_erased(self) -> None:
        username = "versionkeeping-native-test"
        secret = "credential-secret-" + secrets.token_hex(16)

        with adapter.GitRepository(self.repo) as repository:
            if sys.platform == "darwin":
                helper = adapter._require_trusted_credential_helper(
                    Path(repository.output(["--exec-path"]))
                    / adapter.MACOS_CREDENTIAL_HELPER
                )
                openssl = Path("/usr/bin/openssl")
            elif sys.platform == "win32":
                self.assertIsNotNone(repository._windows_git_root)
                helper = adapter._require_windows_credential_provider(
                    repository._windows_git_root
                )
                openssl = (
                    repository._windows_git_root / "usr" / "bin" / "openssl.exe"
                )
            else:
                helper = adapter._require_linux_credential_provider(
                    Path(repository.output(["--exec-path"]))
                )
                openssl = Path("/usr/bin/openssl")

            with authenticated_https_endpoint(
                self.root,
                username,
                secret,
                self.start,
                openssl_executable=openssl,
                openssl_env=repository.env,
            ) as (endpoint, certificate, authorized):
                repository._append_command_config(
                    "http.sslCAInfo",
                    str(certificate),
                )
                repository.enable_https_credentials(endpoint)
                endpoint_parts = urllib.parse.urlsplit(endpoint)
                lookup = (
                    "protocol=https\n"
                    f"host={endpoint_parts.netloc}\n"
                    f"username={username}\n\n"
                )
                credential = lookup.removesuffix("\n") + f"password={secret}\n\n"
                erased = None
                active_failure = False
                try:
                    stored = run_credential_helper(
                        helper,
                        "store",
                        credential,
                        repository.env,
                    )
                    if (
                        stored.returncode != 0
                        and os.environ.get(
                            "VERSIONKEEPING_REQUIRE_NATIVE_CREDENTIAL_ROUNDTRIP"
                        )
                        != "1"
                    ):
                        self.skipTest(
                            "native credential store is unavailable in this session"
                        )
                    self.assertEqual(
                        stored.returncode,
                        0,
                        "native credential store rejected the test credential",
                    )
                    retrieved = run_credential_helper(
                        helper,
                        "get",
                        lookup,
                        repository.env,
                    )
                    retrieved_fields = credential_protocol_fields(
                        retrieved.stdout
                    )
                    self.assertEqual(
                        retrieved.returncode,
                        0,
                        "native credential retrieval failed",
                    )
                    self.assertTrue(
                        secrets.compare_digest(
                            retrieved_fields.get("password", ""),
                            secret,
                        ),
                        "native credential did not round-trip",
                    )
                    result = adapter._run_endpoint(
                        repository,
                        endpoint,
                        ["ls-remote", "--heads"],
                    )
                except BaseException:
                    active_failure = True
                    raise
                finally:
                    try:
                        erased = run_credential_helper(
                            helper,
                            "erase",
                            lookup,
                            repository.env,
                        )
                        after_erase = run_credential_helper(
                            helper,
                            "get",
                            lookup,
                            repository.env,
                        )
                    except Exception:
                        if not active_failure:
                            raise

        self.assertIsNotNone(erased)
        self.assertEqual(erased.returncode, 0, "native credential cleanup failed")
        after_erase_fields = credential_protocol_fields(after_erase.stdout)
        self.assertFalse(
            secrets.compare_digest(
                after_erase_fields.get("password", ""),
                secret,
            ),
            "native credential cleanup left the test secret retrievable",
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr.replace(secret, "<redacted>"),
        )
        self.assertTrue(authorized.is_set())
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS")
    def test_authenticated_https_transport_keeps_credentials_inside_git(self) -> None:
        username = "versionkeeping-test"
        secret = "credential-secret-745bad"
        helper = self.root / "credential-helper"
        write_credential_helper(helper, username, secret)

        with authenticated_https_endpoint(
            self.root,
            username,
            secret,
            self.start,
        ) as (endpoint, certificate, authorized):
            with adapter.GitRepository(self.repo) as repository:
                repository._append_command_config(
                    "http.sslCAInfo",
                    str(certificate),
                )
                with mock.patch.object(adapter.sys, "platform", "darwin"):
                    with mock.patch.object(
                        adapter,
                        "_require_trusted_credential_helper",
                        return_value=str(helper),
                    ):
                        repository.enable_https_credentials(endpoint)
                result = adapter._run_endpoint(
                    repository,
                    endpoint,
                    ["ls-remote", "--heads"],
                )
                config = {
                    key: value
                    for key, value in repository.env.items()
                    if key.startswith("GIT_CONFIG_")
                }

        self.assertEqual(
            result.returncode,
            0,
            result.stderr.replace(secret, "<redacted>"),
        )
        self.assertTrue(authorized.is_set())
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)
        self.assertNotIn(secret, json.dumps(config))

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS Git and TLS")
    def test_windows_gcm_config_keeps_credentials_inside_git(self) -> None:
        username = "versionkeeping-win32"
        secret = "credential-secret-win32-07a91d"
        helper = (
            self.root
            / "win32"
            / "Program Files"
            / "git-credential-manager.exe"
        )
        write_credential_helper(helper, username, secret)

        with authenticated_https_endpoint(
            self.root,
            username,
            secret,
            self.start,
        ) as (endpoint, certificate, authorized):
            with adapter.GitRepository(self.repo) as repository:
                repository._append_command_config(
                    "http.sslCAInfo",
                    str(certificate),
                )
                with mock.patch.object(adapter.sys, "platform", "win32"):
                    with mock.patch.object(
                        adapter,
                        "_require_windows_credential_provider",
                        return_value=str(helper),
                    ):
                        repository.enable_https_credentials(endpoint)
                selected_store = repository.output(
                    ["config", "--get", "credential.credentialStore"]
                )
                selected_environment_store = repository.env[
                    "GCM_CREDENTIAL_STORE"
                ]
                result = adapter._run_endpoint(
                    repository,
                    endpoint,
                    ["ls-remote", "--heads"],
                )
                config = {
                    key: value
                    for key, value in repository.env.items()
                    if key.startswith("GIT_CONFIG_")
                }

        self.assertEqual(
            result.returncode,
            0,
            result.stderr.replace(secret, "<redacted>"),
        )
        self.assertTrue(authorized.is_set())
        self.assertEqual(selected_store, "wincredman")
        self.assertEqual(selected_environment_store, selected_store)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)
        self.assertNotIn(secret, json.dumps(config))

    def test_user_owned_osxkeychain_lookalike_is_unavailable(self) -> None:
        helper_root = self.root / "untrusted-git-core"
        helper_root.mkdir()
        helper = helper_root / "git-credential-osxkeychain"
        helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        helper.chmod(0o755)

        with adapter.GitRepository(self.repo) as repository:
            with mock.patch.object(adapter.sys, "platform", "darwin"):
                with mock.patch.object(
                    repository,
                    "output",
                    return_value=str(helper_root),
                ):
                    with self.assertRaises(adapter.PolicyGate) as raised:
                        repository.enable_https_credentials(
                            "https://github.com/example/repository.git"
                        )

        self.assertEqual(
            raised.exception.code,
            "HTTPS_CREDENTIAL_PROVIDER_UNAVAILABLE",
        )
        self.assertEqual(
            raised.exception.evidence,
            {"provider": "macos-osxkeychain"},
        )
        self.assertNotIn(str(helper_root), str(raised.exception))

    def test_linux_https_provider_unavailable_blocks_before_push(self) -> None:
        endpoint = "https://example.invalid/repository.git"
        fingerprint = self.plan["destination"]["endpoint_fingerprint"]

        with mock.patch.object(adapter.sys, "platform", "linux"):
            with mock.patch.object(
                adapter,
                "_require_linux_credential_provider",
                side_effect=adapter.PolicyGate(
                    "HTTPS_CREDENTIAL_PROVIDER_UNAVAILABLE",
                    provider="linux-credential-cache",
                ),
            ):
                with mock.patch.object(
                    execution,
                    "_endpoint",
                    return_value=(endpoint, fingerprint),
                ):
                    with mock.patch.object(
                        execution,
                        "_probe_default_branch",
                        return_value=self.plan[
                            "destination"
                        ]["default_branch_ref"],
                    ):
                        with mock.patch.object(
                            execution,
                            "_probe_ref",
                            side_effect=(self.start, self.source),
                        ):
                            with mock.patch.object(
                                execution,
                                "_run_endpoint",
                                return_value=subprocess.CompletedProcess(
                                    ["git", "push"], 0, "", ""
                                ),
                            ) as run_endpoint:
                                result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"],
            [
                {
                    "code": "HTTPS_CREDENTIAL_PROVIDER_UNAVAILABLE",
                    "evidence": {"provider": "linux-credential-cache"},
                }
            ],
        )
        run_endpoint.assert_not_called()

    def test_windows_https_provider_unavailable_blocks_before_use(self) -> None:
        git_root = self.root / "Program Files" / "Git"
        git_executable = git_root / "cmd" / "git.exe"
        git_executable.parent.mkdir(parents=True)
        git_executable.write_bytes(b"git")

        with adapter.GitRepository(self.repo) as repository:
            repository.git_executable = str(git_executable)
            repository._windows_git_root = git_root.resolve()
            with mock.patch.object(adapter.sys, "platform", "win32"):
                with mock.patch.object(
                    repository,
                    "_configured_classes",
                    return_value=set(),
                ):
                    with mock.patch.object(
                        repository,
                        "output",
                        return_value=str(
                            git_root / "mingw64" / "libexec" / "git-core"
                        ),
                    ):
                        with self.assertRaises(adapter.PolicyGate) as raised:
                            repository.enable_https_credentials(
                                "https://github.com/example/repository.git"
                            )

        self.assertEqual(
            raised.exception.code,
            "HTTPS_CREDENTIAL_PROVIDER_UNAVAILABLE",
        )
        self.assertEqual(raised.exception.evidence, {"provider": "windows-gcm"})

    def test_https_plan_receipt_and_command_config_are_credential_free(self) -> None:
        fingerprint = self.plan["destination"]["endpoint_fingerprint"]
        username = "versionkeeping-test"
        secret = "credential-secret-65db51"
        helper = self.root / "credential-helper-for-receipt"
        write_credential_helper(helper, username, secret)
        planned = plan_repository(self.repo, request(self.start, self.source))
        observed = {}
        original_enable = adapter.GitRepository.enable_https_credentials

        def enable_credentials(repository, endpoint, certificate):
            repository._append_command_config(
                "http.sslCAInfo",
                str(certificate),
            )
            return original_enable(repository, endpoint)

        def successful_push(repository, endpoint, prefix, suffix=(), **_kwargs):
            observed["config"] = {
                key: value
                for key, value in repository.env.items()
                if key.startswith("GIT_CONFIG_")
            }
            observed["command"] = [*prefix, *suffix]
            result = adapter._run_endpoint(
                repository,
                endpoint,
                ["ls-remote", "--heads"],
            )
            observed["stdout"] = result.stdout
            observed["stderr"] = result.stderr
            return result

        with authenticated_https_endpoint(
            self.root,
            username,
            secret,
            self.start,
        ) as (endpoint, certificate, authorized):
            with mock.patch.object(adapter.sys, "platform", "darwin"):
                with mock.patch.object(
                    adapter,
                    "_require_trusted_credential_helper",
                    return_value=str(helper),
                ):
                    with mock.patch.object(
                        adapter.GitRepository,
                        "enable_https_credentials",
                        autospec=True,
                        side_effect=lambda repository, selected_endpoint: (
                            enable_credentials(
                                repository,
                                selected_endpoint,
                                certificate,
                            )
                        ),
                    ):
                        with mock.patch.object(
                            execution,
                            "_endpoint",
                            return_value=(endpoint, fingerprint),
                        ):
                            with mock.patch.object(
                                execution,
                                "_probe_default_branch",
                                return_value=self.plan["destination"][
                                    "default_branch_ref"
                                ],
                            ):
                                with mock.patch.object(
                                    execution,
                                    "_probe_ref",
                                    side_effect=(self.start, self.source),
                                ):
                                    with mock.patch.object(
                                        execution,
                                        "_run_endpoint",
                                        side_effect=successful_push,
                                    ):
                                        receipt = execute(self.repo, self.plan)

        self.assertEqual(planned["status"], "ready")
        self.assertEqual(receipt["status"], "verified")
        self.assertTrue(authorized.is_set())
        self.assertNotIn(secret, json.dumps(planned))
        self.assertNotIn(secret, json.dumps(receipt))
        self.assertNotIn(secret, json.dumps(observed))

    def test_executes_once_through_captured_endpoint_alias_and_verifies(self) -> None:
        real_run = subprocess.run
        observed = []

        def recording_run(*args, **kwargs):
            command = args[0] if args else kwargs.get("args")
            if (
                command
                and command[0] == str(adapter.TRUSTED_SYSTEM_EXECUTABLES["git"])
                and kwargs.get("cwd") == str(self.repo)
            ):
                observed.append((command, kwargs["env"]))
            return real_run(*args, **kwargs)

        hostile = {
            "GIT_DIR": "/hostile/git-dir",
            "GIT_INDEX_FILE": "/hostile/index",
            "GIT_OBJECT_DIRECTORY": "/hostile/objects",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.publish.url",
            "GIT_CONFIG_VALUE_0": "https://attacker.invalid/repo",
            "GIT_SSL_NO_VERIFY": "true",
            "GIT_SSL_CAINFO": "/hostile/ca.pem",
            "GIT_SSL_CAPATH": "/hostile/ca-directory",
            "SSL_CERT_FILE": "/hostile/openssl-ca.pem",
            "SSL_CERT_DIR": "/hostile/openssl-ca-directory",
            "GIT_ASKPASS": "/hostile/askpass",
            "GIT_SSH_COMMAND": "ssh -F /hostile/config",
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            with mock.patch.object(adapter.subprocess, "run", recording_run):
                result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "verified")
        push_commands = [item for item in observed if "push" in item[0]]
        self.assertEqual(len(push_commands), 1)
        command, env = push_commands[0]
        self.assertNotIn("publish", command)
        self.assertNotIn(str(self.remote), command)
        hook_overrides = [
            item for item in command if item.startswith("core.hooksPath=")
        ]
        self.assertEqual(len(hook_overrides), 1)
        self.assertIn("versionkeeping-empty-hooks-", hook_overrides[0])
        self.assertIn("http.sslVerify=true", command)
        boundary = command.index("--")
        alias = command[boundary + 1]
        self.assertTrue(alias.startswith("versionkeeping-publication-"))
        endpoint_keys = [key for key in env if key.startswith("VERSIONKEEPING_")]
        self.assertEqual(len(endpoint_keys), 1)
        self.assertEqual(env[endpoint_keys[0]], str(self.remote))
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_INDEX_FILE", env)
        self.assertNotIn("GIT_OBJECT_DIRECTORY", env)
        config_count = int(env["GIT_CONFIG_COUNT"])
        closed_config = {
            env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
            for index in range(config_count)
        }
        self.assertNotIn("remote.publish.url", closed_config)
        self.assertEqual(closed_config["protocol.allow"], "never")
        self.assertEqual(closed_config["protocol.ext.allow"], "never")
        self.assertEqual(closed_config["credential.helper"], "")
        self.assertEqual(closed_config["push.gpgSign"], "false")
        self.assertEqual(closed_config["commit.gpgSign"], "false")
        self.assertEqual(closed_config["tag.gpgSign"], "false")
        self.assertNotIn("GIT_SSL_NO_VERIFY", env)
        self.assertNotIn("GIT_SSL_CAINFO", env)
        self.assertNotIn("GIT_SSL_CAPATH", env)
        self.assertNotIn("SSL_CERT_FILE", env)
        self.assertNotIn("SSL_CERT_DIR", env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_ASKPASS"], "false")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)

    def test_signing_helper_is_blocked_before_push(self) -> None:
        marker = self.root / "signing-helper-ran"
        secret = "signer-secret-c72941"
        helper = self.root / f"signer-{secret}"
        helper.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        helper.chmod(0o700)
        git(self.remote, "config", "receive.certNonceSeed", "test-seed")
        git(self.repo, "config", "push.gpgSign", "true")
        git(self.repo, "config", "gpg.program", str(helper))

        result = execute(self.repo, self.plan)

        self.assertFalse(marker.exists())
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"][0]["code"],
            "UNSAFE_GIT_CONFIGURATION",
        )
        self.assertNotIn(secret, json.dumps(result))

    def test_http_transport_config_does_not_block_local_publication(self) -> None:
        git(
            self.repo,
            "config",
            "http.https://example.com.sslVerify",
            "false",
        )
        git(self.repo, "config", "http.extraHeader", "X-Test: local-only")

        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["push_attempted"])
        self.assertEqual(
            git(self.remote, "rev-parse", "refs/heads/topic"),
            self.source,
        )

    def test_url_specific_tls_override_blocks_before_https_credentials(self) -> None:
        git(
            self.repo,
            "config",
            "http.https://github.com.sslVerify",
            "false",
        )

        with adapter.GitRepository(self.repo) as repository:
            with mock.patch.object(
                adapter,
                "_require_trusted_credential_helper",
            ) as require_trusted:
                with self.assertRaises(adapter.PolicyGate) as raised:
                    repository.enable_https_credentials(
                        "https://github.com/example/repository.git"
                    )

        self.assertEqual(raised.exception.code, "UNSAFE_GIT_CONFIGURATION")
        self.assertEqual(
            raised.exception.evidence,
            {"config_classes": ["http.*.ssl*"]},
        )
        require_trusted.assert_not_called()

    def test_url_specific_gcm_override_blocks_before_https_credentials(self) -> None:
        git(
            self.repo,
            "config",
            "credential.https://github.com.credentialStore",
            "plaintext",
        )

        with adapter.GitRepository(self.repo) as repository:
            with mock.patch.object(
                adapter,
                "_require_trusted_credential_helper",
            ) as require_trusted:
                with self.assertRaises(adapter.PolicyGate) as raised:
                    repository.enable_https_credentials(
                        "https://github.com/example/repository.git"
                    )

        self.assertEqual(raised.exception.code, "UNSAFE_GIT_CONFIGURATION")
        self.assertEqual(
            raised.exception.evidence,
            {"config_classes": ["credential.*"]},
        )
        require_trusted.assert_not_called()

    def test_endpoint_change_after_review_blocks_before_push(self) -> None:
        changed = self.root / "changed.git"
        git(self.root, "init", "--bare", str(changed))
        git(self.repo, "remote", "set-url", "--push", "publish", str(changed))

        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "REVIEWED_ENDPOINT_CHANGED")
        self.assertNotIn(str(changed), json.dumps(result))

    def test_default_branch_change_after_review_blocks_before_push(self) -> None:
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/topic")

        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"][0]["code"], "REVIEWED_DEFAULT_BRANCH_CHANGED"
        )

    def test_default_branch_change_during_lease_probe_blocks_before_push(self) -> None:
        original_probe = execution._probe_ref
        changed = False

        def change_default_after_probe(repo, endpoint, ref, object_format):
            nonlocal changed
            observed = original_probe(repo, endpoint, ref, object_format)
            if not changed and ref == "refs/heads/topic":
                changed = True
                git(self.remote, "symbolic-ref", "HEAD", "refs/heads/topic")
            return observed

        with mock.patch.object(
            execution, "_probe_ref", side_effect=change_default_after_probe
        ):
            result = execute(self.repo, self.plan)

        self.assertTrue(changed)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"][0]["code"], "REVIEWED_DEFAULT_BRANCH_CHANGED"
        )
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.start)

    def test_config_change_after_review_blocks_before_push(self) -> None:
        git(self.repo, "config", "branch.topic.pushRemote", "publish")
        git(self.repo, "config", "branch.topic.remote", "publish")
        git(self.repo, "config", "branch.topic.merge", "refs/heads/topic")
        implicit_request = request(self.start, self.source)
        implicit_request["explicit_destination"] = None
        reviewed = plan_repository(self.repo, implicit_request)
        self.assertEqual(reviewed["status"], "ready")
        git(self.repo, "config", "--unset", "branch.topic.pushRemote")
        git(self.repo, "config", "remote.pushDefault", "publish")

        result = execute(self.repo, reviewed)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "REVIEWED_CONFIG_CHANGED")

    def test_target_lease_change_after_review_blocks_before_push(self) -> None:
        remote_only = commit(self.repo, "remote-only")
        git(self.repo, "push", "publish", f"{remote_only}:refs/heads/topic")
        git(self.repo, "reset", "--hard", self.source)

        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "REVIEWED_LEASE_CHANGED")

    def test_remote_config_mutation_after_capture_cannot_redirect_push(self) -> None:
        redirected = self.root / "redirected.git"
        git(self.root, "init", "--bare", str(redirected))
        git(self.repo, "push", str(redirected), f"{self.start}:refs/heads/topic")
        original = execution._run_endpoint
        changed = False

        def mutate_then_run(repo, endpoint, prefix, suffix=(), **kwargs):
            nonlocal changed
            if "push" in prefix and not changed:
                changed = True
                git(
                    self.repo,
                    "remote",
                    "set-url",
                    "--push",
                    "publish",
                    str(redirected),
                )
            return original(repo, endpoint, prefix, suffix, **kwargs)

        with mock.patch.object(execution, "_run_endpoint", mutate_then_run):
            result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "verified")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)
        self.assertEqual(git(redirected, "rev-parse", "refs/heads/topic"), self.start)

    def test_push_failure_is_not_retried(self) -> None:
        original = execution._run_endpoint
        push_calls = 0

        def fail_push(repo, endpoint, prefix, suffix=(), **kwargs):
            nonlocal push_calls
            if "push" in prefix:
                push_calls += 1
                return subprocess.CompletedProcess(["git", "push"], 1, "", "failed")
            return original(repo, endpoint, prefix, suffix, **kwargs)

        with mock.patch.object(execution, "_run_endpoint", fail_push):
            result = execute(self.repo, self.plan)

        self.assertEqual(push_calls, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["push_attempted"])
        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(reason["evidence"]["failure_type"], "PUSH_FAILED")

    def test_push_timeout_is_classified_once_without_retry(self) -> None:
        real_run = subprocess.run
        push_calls = 0

        def timeout_push(*args, **kwargs):
            nonlocal push_calls
            command = args[0] if args else kwargs.get("args")
            if (
                command
                and command[0] == str(adapter.TRUSTED_SYSTEM_EXECUTABLES["git"])
                and "push" in command
            ):
                push_calls += 1
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return real_run(*args, **kwargs)

        with mock.patch.object(adapter.subprocess, "run", timeout_push):
            result = execute(self.repo, self.plan)

        self.assertEqual(push_calls, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["push_attempted"])
        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(reason["evidence"]["failure_type"], "GIT_COMMAND_TIMEOUT")

    def test_unexpected_post_push_failure_returns_unknown_without_retry(self) -> None:
        original_probe = execution._probe_ref
        probe_calls = 0

        def fail_second_probe(repo, endpoint, ref, object_format):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 2:
                raise RuntimeError("unexpected verifier failure")
            return original_probe(repo, endpoint, ref, object_format)

        with mock.patch.object(execution, "_probe_ref", side_effect=fail_second_probe):
            result = execute(self.repo, self.plan)

        self.assertEqual(probe_calls, 2)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["push_attempted"])
        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(reason["evidence"]["expected_sha"], self.source)
        self.assertIn("Re-probe", reason["evidence"]["reconciliation"])
        self.assertNotIn("unexpected verifier failure", json.dumps(result))
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)

    def test_typed_post_push_probe_failure_returns_unknown(self) -> None:
        original_probe = execution._probe_ref
        probe_calls = 0

        def fail_second_probe(repo, endpoint, ref, object_format):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 2:
                raise adapter.PolicyGate("PUSH_ENDPOINT_PROBE_FAILED")
            return original_probe(repo, endpoint, ref, object_format)

        with mock.patch.object(execution, "_probe_ref", side_effect=fail_second_probe):
            result = execute(self.repo, self.plan)

        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(
            reason["evidence"]["failure_type"], "PUSH_ENDPOINT_PROBE_FAILED"
        )
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)

    def test_observed_post_push_ref_mismatch_remains_a_known_block(self) -> None:
        original_probe = execution._probe_ref
        probe_calls = 0

        def mismatch_second_probe(repo, endpoint, ref, object_format):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 2:
                return self.start
            return original_probe(repo, endpoint, ref, object_format)

        with mock.patch.object(
            execution, "_probe_ref", side_effect=mismatch_second_probe
        ):
            result = execute(self.repo, self.plan)

        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_REF_MISMATCH")
        self.assertEqual(reason["evidence"]["observed_sha"], self.start)

    def test_pre_push_typed_timeout_remains_a_deterministic_block(self) -> None:
        with mock.patch.object(
            execution,
            "_guard_repository",
            side_effect=adapter.PolicyGate(
                "GIT_COMMAND_TIMEOUT", operation="rev-parse", timeout_seconds=120
            ),
        ):
            result = execute(self.repo, self.plan)

        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "GIT_COMMAND_TIMEOUT")

    def test_tampered_plan_is_rejected(self) -> None:
        tampered = json.loads(json.dumps(self.plan))
        tampered["push"]["refspec"] = f"{self.source}:refs/heads/other"

        with self.assertRaises(MalformedPlan):
            validate_ready_plan(tampered)

    def test_plan_json_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(MalformedPlan):
            load_plan_json(StringIO('{"schema_version":1,"schema_version":1}'))

    def test_plan_json_rejects_non_finite_values_through_both_loaders(self) -> None:
        document = '{"schema_version": NaN}'
        with self.assertRaisesRegex(MalformedPlan, "non-finite JSON value"):
            load_plan_json(StringIO(document))
        plan_bytes = document.encode()
        digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
        with self.assertRaisesRegex(MalformedPlan, "non-finite JSON value"):
            execution._load_reviewed_plan(plan_bytes, digest)

    def test_ready_plan_rejects_boolean_and_float_schema_versions(self) -> None:
        for malformed in (True, 1.0):
            with self.subTest(malformed=malformed):
                plan = json.loads(json.dumps(self.plan))
                plan["schema_version"] = malformed
                with self.assertRaisesRegex(MalformedPlan, "integer 1"):
                    validate_ready_plan(plan)

    def test_cli_internal_failure_exit_is_distinct_from_typed_block(self) -> None:
        plan_path = self.root / "plan.json"
        plan_bytes, digest = reviewed_plan(self.plan)
        plan_path.write_bytes(plan_bytes)
        arguments = [
            "--repo",
            str(self.repo),
            "--plan",
            str(plan_path),
            "--reviewed-plan-sha256",
            digest,
        ]
        with mock.patch.object(
            execution_cli,
            "execute_repository",
            return_value={"status": "blocked"},
        ):
            blocked_returncode = execution_cli.main(arguments)
        with mock.patch.object(
            execution_cli, "execute_repository", side_effect=RuntimeError("internal")
        ):
            internal_returncode = execution_cli.main(arguments)
        self.assertEqual(blocked_returncode, 1)
        self.assertEqual(internal_returncode, 3)

    def test_reviewed_digest_rejects_unowned_descendant_plan_tampering(self) -> None:
        _, reviewed_digest = reviewed_plan(self.plan)
        unowned = commit(self.repo, "unowned")
        tampered = json.loads(json.dumps(self.plan))
        tampered["request"]["source_sha"] = unowned
        tampered["source_sha"] = unowned
        tampered["outgoing_shas"].append(unowned)
        tampered["push"]["source_sha"] = unowned
        tampered["push"]["refspec"] = f"{unowned}:refs/heads/topic"
        tampered["postchecks"][0]["sha"] = unowned
        validate_ready_plan(tampered)
        tampered_bytes, _ = reviewed_plan(tampered)

        with self.assertRaises(ReviewedPlanDigestMismatch):
            execute_repository(self.repo, tampered_bytes, reviewed_digest)

        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.start)
        fresh_request = request(self.start, unowned)
        fresh_request["task_owned_commits"] = [self.source]
        fresh = plan_repository(self.repo, fresh_request)
        self.assertEqual(fresh["status"], "blocked")
        self.assertEqual(
            fresh["reasons"][0]["code"],
            "OUTGOING_COMMITS_NOT_OWNED_OR_ADOPTED",
        )
        self.assertIn(unowned, fresh["reasons"][0]["evidence"]["shas"])

    def test_post_review_hooks_path_blocks_before_pre_push_hook(self) -> None:
        hooks = self.root / "ambient-hooks"
        hooks.mkdir()
        sentinel = self.root / "pre-push-environment"
        hook = hooks / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$VERSIONKEEPING_PUBLICATION_ENDPOINT\" > "
            f"{shlex.quote(str(sentinel))}\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        git(self.repo, "config", "core.hooksPath", str(hooks))
        ambient_credential = "https://user:secret@example.invalid/repository"

        with mock.patch.dict(
            os.environ,
            {"VERSIONKEEPING_PUBLICATION_ENDPOINT": ambient_credential},
            clear=False,
        ):
            result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"],
            [
                {
                    "code": "UNSAFE_GIT_CONFIGURATION",
                    "evidence": {"config_classes": ["core.hooksPath"]},
                }
            ],
        )
        self.assertFalse(sentinel.exists())
        diagnostics = json.dumps(result)
        self.assertNotIn(str(hooks), diagnostics)
        self.assertNotIn(ambient_credential, diagnostics)

    def test_cli_duplicate_plan_key_is_malformed(self) -> None:
        plan_path = self.root / "duplicate-plan.json"
        plan_bytes = b'{"schema_version":1,"schema_version":1}'
        plan_path.write_bytes(plan_bytes)
        digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--repo",
                str(self.repo),
                "--plan",
                str(plan_path),
                "--reviewed-plan-sha256",
                digest,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "MALFORMED_PLAN")

    def test_cli_digest_mismatch_is_distinct_and_precedes_parsing(self) -> None:
        plan_path = self.root / "tampered-plan.json"
        plan_path.write_bytes(b"not json")
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--repo",
                str(self.repo),
                "--plan",
                str(plan_path),
                "--reviewed-plan-sha256",
                "sha256:" + "0" * 64,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"],
            "REVIEWED_PLAN_DIGEST_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
