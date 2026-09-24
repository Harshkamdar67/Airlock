#!/usr/bin/env python3
"""Offline tests for OpenRouter operating-system credential custody."""

from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
AUTH_PATH = ROOT / "bin" / "airlock_openrouter_auth.py"
SPEC = importlib.util.spec_from_file_location("airlock_openrouter_auth_test", AUTH_PATH)
assert SPEC is not None and SPEC.loader is not None
AUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTH)
SENTINEL = b"sk-or-v1-THIS_IS_A_SENTINEL_KEY_123456789"


class OpenRouterCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dpapi_file = self.root / "credentials" / "openrouter.dpapi"
        self.path_patch = patch.object(
            AUTH, "_windows_credential_path", return_value=self.dpapi_file
        )
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temporary.cleanup()

    def test_key_validation_is_bounded_ascii_and_prefix_strict(self) -> None:
        self.assertEqual(AUTH.validate_key(SENTINEL), SENTINEL)
        minimum = AUTH.KEY_PREFIX + b"a" * 16
        maximum = AUTH.KEY_PREFIX + b"a" * (
            AUTH.MAX_KEY_BYTES - len(AUTH.KEY_PREFIX)
        )
        self.assertEqual(AUTH.validate_key(minimum), minimum)
        self.assertEqual(AUTH.validate_key(maximum), maximum)
        invalid = (
            b"",
            b"sk-other-v1-" + b"a" * 32,
            b"sk-or-v1-short",
            b"sk-or-v1-" + b"a" * 16 + b"\n",
            b"sk-or-v1-" + b"a" * 16 + b" ",
            b"sk-or-v1-" + b"\x80" * 16,
            b"sk-or-v1-" + b"a" * AUTH.MAX_KEY_BYTES,
        )
        for value in invalid:
            with self.subTest(value=value[:20]):
                with self.assertRaises(AUTH.CredentialError):
                    AUTH.validate_key(value)

    def test_bounded_stdin_accepts_one_line_ending_only(self) -> None:
        for ending in (b"", b"\n", b"\r\n"):
            with self.subTest(ending=ending):
                stream = SimpleNamespace(buffer=io.BytesIO(SENTINEL + ending))
                with patch.object(AUTH.sys, "stdin", stream):
                    self.assertEqual(AUTH.read_key_from_stdin(), SENTINEL)
        stream = SimpleNamespace(buffer=io.BytesIO(SENTINEL + b"\n\n"))
        with patch.object(AUTH.sys, "stdin", stream):
            with self.assertRaises(AUTH.CredentialError):
                AUTH.read_key_from_stdin()

    def test_hidden_prompt_refuses_non_tty_instead_of_echoing(self) -> None:
        stream = SimpleNamespace(isatty=lambda: False)
        with patch.object(AUTH.sys, "stdin", stream), \
                patch.object(AUTH.sys, "stderr", stream), \
                patch.object(AUTH.getpass, "getpass") as getpass_call:
            with self.assertRaisesRegex(AUTH.CredentialError, "--stdin"):
                AUTH.read_key_hidden()
        getpass_call.assert_not_called()

    def test_linux_secret_tool_receives_key_only_on_stdin(self) -> None:
        calls: list[tuple[list[str], bytes | None]] = []

        def run(command, **kwargs):
            calls.append((command, kwargs.get("input")))
            if "lookup" in command:
                return subprocess.CompletedProcess(command, 0, SENTINEL + b"\n", b"")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(AUTH.shutil, "which", return_value="/usr/bin/secret-tool"), \
                patch.object(AUTH.subprocess, "run", side_effect=run):
            AUTH._linux_store(SENTINEL)
            self.assertEqual(AUTH._linux_load(), SENTINEL)
            self.assertTrue(AUTH._linux_delete())

        self.assertGreaterEqual(len(calls), 4)
        for command, _input in calls:
            self.assertFalse(any(
                SENTINEL.decode("ascii") in argument for argument in command
            ))
        store_call = next(item for item in calls if "store" in item[0])
        self.assertEqual(store_call[1], SENTINEL + b"\n")

    def test_linux_missing_secret_and_unavailable_backend_are_distinct(self) -> None:
        missing = subprocess.CompletedProcess(["secret-tool"], 1, b"", b"")
        with patch.object(AUTH.shutil, "which", return_value="secret-tool"), \
                patch.object(AUTH.subprocess, "run", return_value=missing):
            self.assertIsNone(AUTH._linux_load())
        unavailable = subprocess.CompletedProcess(
            ["secret-tool"], 1, b"", b"D-Bus is unavailable"
        )
        with patch.object(AUTH.shutil, "which", return_value="secret-tool"), \
                patch.object(AUTH.subprocess, "run", return_value=unavailable):
            with self.assertRaises(AUTH.CredentialBackendUnavailable):
                AUTH._linux_load()
        with patch.object(AUTH.shutil, "which", return_value=None):
            with self.assertRaises(AUTH.CredentialBackendUnavailable):
                AUTH._linux_load()

    def test_linux_logout_deletes_a_foreign_or_legacy_value(self) -> None:
        commands: list[list[str]] = []

        def run(command, **_kwargs):
            commands.append(command)
            if "lookup" in command:
                return subprocess.CompletedProcess(
                    command, 0, b"sk-legacy-abcdefghijklmnop\n", b""
                )
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(AUTH.shutil, "which", return_value="secret-tool"), \
                patch.object(AUTH.subprocess, "run", side_effect=run):
            self.assertTrue(AUTH._linux_delete())
        self.assertTrue(any("clear" in command for command in commands))

    def test_windows_file_contains_only_mock_encrypted_bytes(self) -> None:
        encrypted = b"ciphertext-without-the-key"
        with patch.object(
            AUTH, "_dpapi_protect", return_value=encrypted
        ) as protect, patch.object(
            AUTH, "_dpapi_unprotect", return_value=SENTINEL
        ) as unprotect:
            AUTH._windows_store(SENTINEL)
            protect.assert_called_once_with(SENTINEL)
            raw = self.dpapi_file.read_bytes()
            self.assertTrue(raw.startswith(AUTH.DPAPI_MAGIC))
            self.assertNotIn(SENTINEL, raw)
            self.assertEqual(AUTH._windows_load(), SENTINEL)
            unprotect.assert_called_once_with(raw[len(AUTH.DPAPI_MAGIC):])
            self.assertTrue(AUTH._windows_delete())
            self.assertFalse(AUTH._windows_delete())

    def test_windows_store_protects_directory_temporary_and_final_paths(self) -> None:
        protected: list[Path] = []
        with patch.object(AUTH, "_dpapi_protect", return_value=b"cipher"), \
                patch.object(
                    AUTH.policy,
                    "_protect_windows_path",
                    side_effect=lambda path: protected.append(Path(path)),
                ):
            AUTH._windows_store(SENTINEL)
        self.assertEqual(protected[0], self.dpapi_file.parent)
        self.assertEqual(protected[-1], self.dpapi_file)
        self.assertEqual(len(protected), 3)
        self.assertEqual(protected[1].parent, self.dpapi_file.parent)
        self.assertTrue(protected[1].name.startswith("openrouter-"))
        self.assertTrue(protected[1].name.endswith(".tmp"))

    def test_windows_operations_reject_unsafe_acls(self) -> None:
        self.dpapi_file.parent.mkdir(parents=True)
        self.dpapi_file.write_bytes(AUTH.DPAPI_MAGIC + b"cipher")
        unsafe = AUTH.policy.PolicyFileError("unsafe ACL")

        with patch.object(
            AUTH.policy,
            "_validate_windows_path_security",
            side_effect=unsafe,
        ), patch.object(AUTH, "_dpapi_unprotect") as unprotect:
            with self.assertRaisesRegex(AUTH.CredentialError, "path is unsafe"):
                AUTH._windows_load()
            unprotect.assert_not_called()
            with self.assertRaisesRegex(AUTH.CredentialError, "path is unsafe"):
                AUTH._windows_delete()
            with self.assertRaisesRegex(AUTH.CredentialError, "path is unsafe"):
                AUTH._windows_store(SENTINEL)

        self.assertTrue(self.dpapi_file.exists())
        self.assertEqual(
            self.dpapi_file.read_bytes(),
            AUTH.DPAPI_MAGIC + b"cipher",
        )

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_real_windows_dpapi_round_trip_stays_in_memory(self) -> None:
        encrypted = AUTH._dpapi_protect(SENTINEL)
        self.assertNotIn(SENTINEL, encrypted)
        self.assertEqual(AUTH._dpapi_unprotect(encrypted), SENTINEL)

    def test_windows_store_refuses_unknown_existing_file(self) -> None:
        self.dpapi_file.parent.mkdir(parents=True)
        self.dpapi_file.write_bytes(b"not managed by Airlock")
        with patch.object(AUTH, "_dpapi_protect", return_value=b"cipher"):
            with self.assertRaisesRegex(AUTH.CredentialError, "unknown file"):
                AUTH._windows_store(SENTINEL)
        self.assertEqual(self.dpapi_file.read_bytes(), b"not managed by Airlock")

    def test_windows_store_removes_temporary_file_after_replace_failure(self) -> None:
        with patch.object(AUTH, "_dpapi_protect", return_value=b"cipher"), \
                patch.object(AUTH.os, "replace", side_effect=OSError("blocked")):
            with self.assertRaisesRegex(AUTH.CredentialError, "could not be stored"):
                AUTH._windows_store(SENTINEL)
        self.assertEqual(list(self.dpapi_file.parent.glob("*.tmp")), [])

    def test_macos_missing_lookup_and_delete_are_safe(self) -> None:
        security = MagicMock()
        core = MagicMock()
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        result = (security, core, -25300, length, data, item)
        with patch.object(AUTH, "_macos_find", return_value=result):
            self.assertIsNone(AUTH._macos_load())
            self.assertFalse(AUTH._macos_delete())
        security.SecKeychainItemDelete.assert_not_called()

    def test_macos_store_passes_key_as_memory_not_command_argument(self) -> None:
        captured: list[bytes] = []

        def add_password(*arguments):
            captured.append(ctypes.string_at(arguments[6], arguments[5]))
            return 0

        security = MagicMock()
        security.SecKeychainAddGenericPassword.side_effect = add_password
        core = MagicMock()
        result = (
            security,
            core,
            -25300,
            ctypes.c_uint32(),
            ctypes.c_void_p(),
            ctypes.c_void_p(),
        )
        with patch.object(AUTH, "_macos_find", return_value=result), \
                patch.object(AUTH.subprocess, "run") as run:
            AUTH._macos_store(SENTINEL)
        run.assert_not_called()
        call = security.SecKeychainAddGenericPassword.call_args.args
        self.assertNotIn(SENTINEL, call)
        self.assertEqual(call[5], len(SENTINEL))
        self.assertEqual(captured, [SENTINEL])

    def test_cli_never_prints_the_key(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(AUTH, "read_key_hidden", return_value=SENTINEL), \
                patch.object(AUTH, "store_key") as store, \
                patch.object(AUTH, "backend_name", return_value="test-backend"), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(AUTH.main(["set-key"]), 0)
        store.assert_called_once_with(SENTINEL)
        combined = stdout.getvalue().encode() + stderr.getvalue().encode()
        self.assertNotIn(SENTINEL, combined)

    def test_invalid_argv_does_not_reflect_a_key(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                AUTH.build_parser().parse_args(["set-key", SENTINEL.decode("ascii")])
        self.assertNotIn(SENTINEL.decode("ascii"), stderr.getvalue())

    def test_status_distinguishes_unreadable_and_backend_unavailable(self) -> None:
        with patch.object(
            AUTH, "load_key", side_effect=AUTH.CredentialError("corrupt")
        ):
            self.assertEqual(AUTH.local_status(), "unreadable")
        with patch.object(
            AUTH,
            "load_key",
            side_effect=AUTH.CredentialBackendUnavailable("offline"),
        ):
            self.assertEqual(AUTH.local_status(), "backend-unavailable")

    def test_doctor_backend_probe_never_loads_the_key(self) -> None:
        stdout = io.StringIO()
        with patch.object(AUTH, "backend_name", return_value="linux-secret-service"), \
                patch.object(AUTH, "_secret_tool", return_value="secret-tool"), \
                patch.object(AUTH, "load_key") as load_key, \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(AUTH.main(["_backend-status"]), 0)
        load_key.assert_not_called()
        self.assertEqual(
            stdout.getvalue(),
            "BACKEND=linux-secret-service\nSTATE=available\n",
        )

    def test_noninteractive_logout_requires_explicit_yes(self) -> None:
        stderr = io.StringIO()
        with patch("builtins.input", side_effect=EOFError), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(AUTH.main(["logout"]), 2)
        self.assertIn("logout --yes", stderr.getvalue())

    def test_status_and_errors_disclose_no_credential_material(self) -> None:
        stdout = io.StringIO()
        with patch.object(AUTH, "load_key", return_value=SENTINEL), \
                patch.object(AUTH, "backend_name", return_value="test-backend"), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(AUTH.main(["status"]), 0)
        self.assertNotIn(SENTINEL.decode("ascii"), stdout.getvalue())
        self.assertIn("configured", stdout.getvalue())



class OpenRouterKeyLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("AIRLOCK_OPENROUTER_KEYS", None)
        self.config: dict[str, str] = {}
        self.writes: list[dict[str, object]] = []
        access = SimpleNamespace(
            read_flat_config=lambda: dict(self.config),
            write_flat_config_overrides=self._write,
        )
        self.access_patch = patch.object(AUTH, "_access_module", return_value=access)
        self.access_patch.start()

    def tearDown(self) -> None:
        self.access_patch.stop()
        self.env.stop()

    def _write(self, updates: dict[str, object]) -> None:
        self.writes.append(dict(updates))
        for key, value in updates.items():
            if value is None:
                self.config.pop(key, None)
            else:
                self.config[key] = str(value)

    def test_labels_are_short_lowercase_names(self) -> None:
        for label in ("default", "work", "team-2", "a"):
            self.assertEqual(AUTH.validate_label(label), label)
        for label in ("", "Work", "2team", "a b", "x" * 25, "../x", None):
            with self.assertRaises(AUTH.CredentialError, msg=repr(label)):
                AUTH.validate_label(label)

    def test_label_list_parsing_fails_closed(self) -> None:
        self.assertEqual(AUTH.parse_labels(" work, default "), ("work", "default"))
        for raw in ("work,work", "Work", ",", ",".join(f"k{i}" for i in range(9))):
            with self.assertRaises(AUTH.CredentialError, msg=raw):
                AUTH.parse_labels(raw)

    def test_configured_labels_default_config_and_environment(self) -> None:
        self.assertEqual(AUTH.configured_labels(), ("default",))
        self.config["AIRLOCK_OPENROUTER_KEYS"] = "default,work"
        self.assertEqual(AUTH.configured_labels(), ("default", "work"))
        os.environ["AIRLOCK_OPENROUTER_KEYS"] = "team"
        self.assertEqual(AUTH.configured_labels(), ("team",))

    def test_default_key_keeps_its_storage_names_and_labels_get_their_own(self) -> None:
        self.assertEqual(AUTH._secret_service_attributes("default"), AUTH.SECRET_SERVICE_ATTRIBUTES)
        work = AUTH._secret_service_attributes("work")
        self.assertEqual(work[-1], "openrouter-api-key-work")
        self.assertNotEqual(work, AUTH.SECRET_SERVICE_ATTRIBUTES)
        self.assertEqual(AUTH._keychain_account("default"), b"default")
        self.assertEqual(AUTH._keychain_account("work"), b"work")
        with patch.dict(os.environ, {"LOCALAPPDATA": "/tmp/local"}):
            self.assertEqual(AUTH._windows_credential_path().name, "openrouter.dpapi")
            self.assertEqual(AUTH._windows_credential_path("work").name, "openrouter-work.dpapi")

    def test_linux_labeled_key_round_trip_uses_only_its_attributes(self) -> None:
        stored: dict[tuple[str, ...], bytes] = {}

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            action = command[1]
            attributes = tuple(part for part in command[2:] if not part.startswith("--label="))
            if action == "store":
                stored[attributes] = kwargs["input"]
                return subprocess.CompletedProcess(command, 0, b"", b"")
            value = stored.get(attributes)
            if action == "lookup":
                return subprocess.CompletedProcess(command, 0 if value else 1, value or b"", b"")
            stored.pop(attributes, None)
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(AUTH.shutil, "which", return_value="secret-tool"), \
                patch.object(AUTH.subprocess, "run", side_effect=run):
            AUTH._linux_store(SENTINEL, "work")
            self.assertIsNone(AUTH._linux_load())
            self.assertEqual(AUTH._linux_load("work"), SENTINEL)
            self.assertTrue(AUTH._linux_delete("work"))
            self.assertIsNone(AUTH._linux_load("work"))

    def test_set_key_with_label_adds_it_after_an_existing_default(self) -> None:
        stdout = io.StringIO()
        with patch.object(AUTH, "read_key_hidden", return_value=SENTINEL), \
                patch.object(AUTH, "store_key") as store, \
                patch.object(AUTH, "load_key", return_value=b"sk-or-v1-EXISTING_DEFAULT_KEY_1"), \
                patch.object(AUTH, "backend_name", return_value="test-backend"), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(AUTH.main(["set-key", "--label", "work"]), 0)
        store.assert_called_once_with(SENTINEL, "work")
        self.assertEqual(self.config["AIRLOCK_OPENROUTER_KEYS"], "default,work")
        self.assertIn("OpenRouter credential work stored", stdout.getvalue())
        self.assertNotIn(SENTINEL.decode("ascii"), stdout.getvalue())

    def test_set_key_with_label_and_no_default_uses_only_that_label(self) -> None:
        with patch.object(AUTH, "read_key_hidden", return_value=SENTINEL), \
                patch.object(AUTH, "store_key"), \
                patch.object(AUTH, "load_key", return_value=None), \
                patch.object(AUTH, "backend_name", return_value="test-backend"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(AUTH.main(["set-key", "--label", "work"]), 0)
        self.assertEqual(self.config["AIRLOCK_OPENROUTER_KEYS"], "work")

    def test_environment_list_is_never_rewritten(self) -> None:
        os.environ["AIRLOCK_OPENROUTER_KEYS"] = "default"
        stdout = io.StringIO()
        with patch.object(AUTH, "read_key_hidden", return_value=SENTINEL), \
                patch.object(AUTH, "store_key"), \
                patch.object(AUTH, "backend_name", return_value="test-backend"), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(AUTH.main(["set-key", "--label", "work"]), 0)
        self.assertEqual(self.writes, [])
        self.assertIn("set in the environment", stdout.getvalue())

    def test_logout_with_label_removes_it_from_the_list(self) -> None:
        self.config["AIRLOCK_OPENROUTER_KEYS"] = "default,work"
        with patch.object(AUTH, "delete_key", return_value=True) as delete, \
                patch.object(AUTH, "backend_name", return_value="test-backend"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(AUTH.main(["logout", "--yes", "--label", "work"]), 0)
        delete.assert_called_once_with("work")
        # Only the default key is left, which needs no setting at all.
        self.assertNotIn("AIRLOCK_OPENROUTER_KEYS", self.config)

    def test_status_reports_every_configured_label(self) -> None:
        self.config["AIRLOCK_OPENROUTER_KEYS"] = "default,work"
        stdout = io.StringIO()

        def load(label: str = "default") -> bytes | None:
            return SENTINEL if label == "default" else None

        with patch.object(AUTH, "load_key", side_effect=load), \
                patch.object(AUTH, "backend_name", return_value="test-backend"), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(AUTH.main(["status"]), 0)
        self.assertEqual(stdout.getvalue(), (
            "OpenRouter credential: configured (test-backend).\n"
            "OpenRouter credential work: missing (test-backend).\n"
        ))

    def test_load_keys_returns_present_keys_in_order(self) -> None:
        self.config["AIRLOCK_OPENROUTER_KEYS"] = "work,default,team"

        def load(label: str = "default") -> bytes | None:
            return {"default": b"d", "team": b"t"}.get(label)

        with patch.object(AUTH, "load_key", side_effect=load):
            self.assertEqual(AUTH.load_keys(), (("default", b"d"), ("team", b"t")))


class OpenRouterKeyLabelConfigFileTests(unittest.TestCase):
    """The label list round-trips through the real Airlock config writer."""

    def test_real_config_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.write_text("AIRLOCK_MODEL=sol\n", encoding="utf-8")
            with patch.dict(os.environ, {"AIRLOCK_CONFIG_FILE": str(config)}):
                os.environ.pop("AIRLOCK_OPENROUTER_KEYS", None)
                AUTH._write_labels(("default", "work"))
                self.assertEqual(AUTH.configured_labels(), ("default", "work"))
                self.assertIn("AIRLOCK_MODEL=sol", config.read_text(encoding="utf-8"))
                AUTH._write_labels(None)
                self.assertEqual(AUTH.configured_labels(), ("default",))
                self.assertNotIn("AIRLOCK_OPENROUTER_KEYS", config.read_text(encoding="utf-8"))
                config.write_text("AIRLOCK_OPENROUTER_KEYS=Bad Label\n", encoding="utf-8")
                with self.assertRaises(AUTH.CredentialError):
                    AUTH.configured_labels()

if __name__ == "__main__":
    unittest.main()
