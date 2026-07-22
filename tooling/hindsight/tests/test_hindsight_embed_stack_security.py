import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STACK_LIB = ROOT / "lib" / "hindsight-embed-stack.zsh"
SERVICE = ROOT / "bin" / "hindsight-embed-service"


def stack_environment(root, home, *, ui_port=17979):
    inventory = root / "inventory.json"
    if not inventory.exists():
        inventory.write_text("{}\n", encoding="utf-8")
    return {
        "HOME": str(home),
        "HINDSIGHT_EMBED_UVX": "/usr/bin/true",
        "HINDSIGHT_EMBED_CONTROL_PORT": "7878",
        "HINDSIGHT_EMBED_CONTROL_HOSTNAME": "127.0.0.1",
        "HINDSIGHT_EMBED_PRIMARY_PROFILE": "core",
        "HINDSIGHT_EMBED_FLEET_PROFILES": "core",
        "HINDSIGHT_EMBED_API_BASE_PORT": "7979",
        "HINDSIGHT_EMBED_UI_BASE_PORT": str(ui_port),
        "HINDSIGHT_EMBED_UI_HOSTNAME": "127.0.0.1",
        "HINDSIGHT_EMBED_PYTHON": "/usr/bin/python3",
        "HINDSIGHT_EMBED_STOP_HELPER": str(
            ROOT / "libexec" / "hindsight-embed-stop-profile-services.py"
        ),
        "HINDSIGHT_MEMORY_CLI": str(ROOT / "bin" / "hindsight-memory"),
        "HINDSIGHT_MEMORY_STATE_DIR": str(root / "memory-state"),
        "HINDSIGHT_MEMORY_BROKER_SOCKET": str(
            root / "memory-state" / "broker.sock"
        ),
        "HINDSIGHT_EMBED_STATE_DIR": str(root / "stack-state"),
        "HINDSIGHT_EMBED_AUTOSTART_DAEMON": "true",
        "HINDSIGHT_EMBED_AUTOSTART_UI": "true",
        "HINDSIGHT_MEMORY_INVENTORY": str(inventory),
        "HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV": "TEST_DATA_TOKEN",
        "HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV": "TEST_MINT_AUTHORITY",
        "HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV": "TEST_UI_ACCESS_KEY",
        "TEST_DATA_TOKEN": "test-data-plane-token",
        "TEST_MINT_AUTHORITY": "test-mint-authority",
        "TEST_UI_ACCESS_KEY": "test-ui-access-key",
    }


class ControlPlaneFixture(BaseHTTPRequestHandler):
    access_key = "test-ui-access-key"
    protected = True
    http_only = True
    leaked_value = None
    accept_missing_login = False
    additional_cookie = None
    additional_cookie_after = False

    def log_message(self, format, *args):  # noqa: A002
        return

    def _send(self, status, payload, *, cookie=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if self.additional_cookie is not None and not self.additional_cookie_after:
            self.send_header("Set-Cookie", self.additional_cookie)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        if self.additional_cookie is not None and self.additional_cookie_after:
            self.send_header("Set-Cookie", self.additional_cookie)
        if self.leaked_value is not None:
            self.send_header("X-Test-Leak", self.leaked_value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            self._send(
                200,
                {
                    "api_version": "0.8.4",
                    "features": {"access_key_auth": self.protected},
                },
            )
            return
        if self.path != "/api/banks":
            self._send(404, {"error": "not found"})
            return
        authenticated = "hindsight_cp_access=test-session" in self.headers.get(
            "Cookie", ""
        )
        if self.protected and not authenticated:
            self._send(401, {"error": "unauthorized"})
            return
        self._send(200, {"banks": []})

    def do_POST(self):
        if self.path != "/api/auth/login":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if self.accept_missing_login and "key" not in payload:
            self._send(200, {"success": True})
            return
        if not self.protected:
            self._send(503, {"error": "access key not configured"})
            return
        if payload.get("key") != self.access_key:
            self._send(401, {"error": "invalid access key"})
            return
        attributes = "; Path=/; SameSite=Lax"
        if self.http_only:
            attributes += "; HttpOnly"
        self._send(
            200,
            {"success": True},
            cookie="hindsight_cp_access=test-session" + attributes,
        )


class StackUiAuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        profile_dir = self.home / ".hindsight" / "profiles"
        profile_dir.mkdir(parents=True)
        (profile_dir / "core.env").write_text("# isolated profile\n", encoding="utf-8")
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text("{}\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def run_probe(
        self,
        *,
        protected=True,
        http_only=True,
        leak=False,
        accept_missing_login=False,
        additional_cookie=None,
        additional_cookie_after=False,
        proxy_url=None,
    ):
        data_token = "test-data-plane-token"

        class Handler(ControlPlaneFixture):
            pass

        Handler.protected = protected
        Handler.http_only = http_only
        Handler.leaked_value = data_token if leak else None
        Handler.accept_missing_login = accept_missing_login
        Handler.additional_cookie = additional_cookie
        Handler.additional_cookie_after = additional_cookie_after
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            env = stack_environment(self.root, self.home, ui_port=port)
            env["TEST_DATA_TOKEN"] = data_token
            env["TEST_UI_ACCESS_KEY"] = Handler.access_key
            if proxy_url is not None:
                env.update(
                    {
                        "http_proxy": proxy_url,
                        "HTTP_PROXY": proxy_url,
                        "https_proxy": proxy_url,
                        "HTTPS_PROXY": proxy_url,
                        "NO_PROXY": "",
                        "no_proxy": "",
                    }
                )
            return subprocess.run(
                [
                    "/bin/zsh",
                    "-c",
                    'source "$1"; hindsight_stack_ui_auth_status 5',
                    "--",
                    str(STACK_LIB),
                ],
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=10,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_accepts_authenticated_cookie_scoped_proxy_contract(self):
        result = self.run_probe()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_session_cookie_after_unrelated_cookie(self):
        result = self.run_probe(
            additional_cookie="ui_preference=compact; Path=/; SameSite=Lax"
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_non_http_only_duplicate_session_cookie_first(self):
        result = self.run_probe(
            additional_cookie="hindsight_cp_access=shadow; Path=/"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("COOKIE_NOT_HTTP_ONLY", result.stderr)

    def test_rejects_non_http_only_duplicate_session_cookie_last(self):
        result = self.run_probe(
            additional_cookie="hindsight_cp_access=shadow; Path=/",
            additional_cookie_after=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("COOKIE_NOT_HTTP_ONLY", result.stderr)

    def test_rejects_unprotected_control_plane(self):
        result = self.run_probe(protected=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ACCESS_KEY_AUTH_DISABLED", result.stderr)

    def test_rejects_browser_readable_session_cookie(self):
        result = self.run_probe(http_only=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("COOKIE_NOT_HTTP_ONLY", result.stderr)

    def test_rejects_data_plane_token_in_browser_response(self):
        result = self.run_probe(leak=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_EXPOSED", result.stderr)

    def test_rejects_control_plane_accepting_missing_login(self):
        result = self.run_probe(accept_missing_login=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MISSING_LOGIN_ACCEPTED", result.stderr)

    def test_loopback_auth_probe_never_contacts_ambient_proxy(self):
        received = []

        class SentinelProxy(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                return

            def _reject(self):
                length = int(self.headers.get("Content-Length", "0"))
                received.append(self.rfile.read(length))
                self.send_response(502)
                self.end_headers()

            do_CONNECT = _reject
            do_GET = _reject
            do_POST = _reject

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), SentinelProxy)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
            result = self.run_probe(proxy_url=proxy_url)
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(received, [])


class StackCredentialBindingTest(unittest.TestCase):
    reserved_names = (
        "HINDSIGHT_MEMORY_INVENTORY",
        "HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV",
        "HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV",
        "HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV",
        "HINDSIGHT_API_TENANT_API_KEY",
        "HINDSIGHT_CP_DATAPLANE_API_KEY",
        "HINDSIGHT_CP_ACCESS_KEY",
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.profile = self.home / ".hindsight" / "profiles" / "core.env"
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("# isolated profile\n", encoding="utf-8")
        self.env = stack_environment(self.root, self.home)

    def tearDown(self):
        self.temporary.cleanup()

    def run_stack(self, command, env=None):
        return subprocess.run(
            ["/bin/zsh", "-c", 'source "$1"; eval "$2"', "--", str(STACK_LIB), command],
            check=False,
            capture_output=True,
            env=env or self.env,
            text=True,
            timeout=10,
        )

    def test_rejects_reserved_locator_names_for_every_credential_role(self):
        roles = (
            "HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV",
            "HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV",
            "HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV",
        )
        for role in roles:
            for reserved_name in self.reserved_names:
                with self.subTest(role=role, reserved_name=reserved_name):
                    env = dict(self.env)
                    env[role] = reserved_name
                    result = self.run_stack("hindsight_stack_load_config", env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        f"{role} must not target a reserved runtime binding",
                        result.stderr,
                    )

    def test_rejects_controller_owned_keys_in_adopted_profile(self):
        for reserved_name in self.reserved_names:
            with self.subTest(reserved_name=reserved_name):
                self.profile.write_text(
                    f"export {reserved_name}=profile-owned-value\n",
                    encoding="utf-8",
                )
                result = self.run_stack(
                    "hindsight_stack_load_config; "
                    "hindsight_stack_preflight_runtime_credentials"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "must not define controller-owned credential binding "
                    f"{reserved_name}",
                    result.stderr,
                )


class ManagedServiceCredentialPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        service_source = SERVICE.read_text(encoding="utf-8")
        self.assertTrue(service_source.endswith('main "$@"\n'))
        self.service_lib = self.root / "hindsight-embed-service.zsh"
        self.service_lib.write_text(
            service_source.removesuffix('main "$@"\n'), encoding="utf-8"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_service_function(self, script):
        return subprocess.run(
            ["/bin/zsh", "-c", 'source "$1"; eval "$2"', "--", str(self.service_lib), script],
            check=False,
            capture_output=True,
            env={"HOME": str(self.root), "USER": "test-user"},
            text=True,
            timeout=10,
        )

    def test_installed_file_validation_runs_value_preflight_first(self):
        marker = self.root / "artifact-validation-ran"
        result = self.run_service_function(
            "hindsight_stack_load_config() { return 0 }; "
            "hindsight_stack_preflight_runtime_credentials() { return 1 }; "
            f'validate_trusted_artifact() {{ /usr/bin/touch "{marker}" }}; '
            "validate_installed_files"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_start_install_and_restart_do_not_mutate_after_failed_preflight(self):
        for operation in ("start_launchd_service", "install_service", "restart_service"):
            with self.subTest(operation=operation):
                marker = self.root / f"{operation}-mutation"
                result = self.run_service_function(
                    "is_loaded() { return 1 }; "
                    "preflight_launchd_service() { return 1 }; "
                    f'stage_validated_manifest() {{ /usr/bin/touch "{marker}" }}; '
                    f'bootout_if_loaded() {{ /usr/bin/touch "{marker}" }}; '
                    f'hindsight_stack_reset_desired_state() {{ /usr/bin/touch "{marker}" }}; '
                    f'load_launchd_service() {{ /usr/bin/touch "{marker}" }}; '
                    f"{operation}"
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
