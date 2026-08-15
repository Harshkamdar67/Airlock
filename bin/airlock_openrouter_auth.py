#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Operating-system credential custody for an OpenRouter API key."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import warnings

import airlock_policy as policy

MAX_KEY_BYTES = 512
MAX_ENCRYPTED_BYTES = 16 * 1024
KEY_PREFIX = b"sk-or-v1-"
KEYCHAIN_SERVICE = b"dev.airlock.openrouter"
KEYCHAIN_ACCOUNT = b"default"
SECRET_SERVICE_ATTRIBUTES = (
    "service", "airlock", "credential", "openrouter-api-key"
)
DPAPI_MAGIC = b"AIRLOCK-DPAPI\x00"
DPAPI_ENTROPY = b"Airlock OpenRouter credential v1"


class CredentialError(RuntimeError):
    """Raised for safe, user-facing credential failures."""


class CredentialBackendUnavailable(CredentialError):
    """Raised when the operating-system credential backend is unavailable."""


def validate_key(raw: bytes | bytearray) -> bytes:
    if not isinstance(raw, (bytes, bytearray)):
        raise CredentialError("OpenRouter key input is invalid")
    value = bytes(raw)
    if not len(KEY_PREFIX) + 16 <= len(value) <= MAX_KEY_BYTES:
        raise CredentialError("OpenRouter key length is invalid")
    if not value.startswith(KEY_PREFIX):
        raise CredentialError("OpenRouter key prefix is invalid")
    if any(byte < 0x21 or byte > 0x7E for byte in value):
        raise CredentialError("OpenRouter key contains invalid characters")
    return value


def read_key_from_stdin() -> bytes:
    raw = sys.stdin.buffer.read(MAX_KEY_BYTES + 3)
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if len(raw) > MAX_KEY_BYTES:
        raise CredentialError("OpenRouter key input is too large")
    return validate_key(raw)


def read_key_hidden() -> bytes:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise CredentialError(
            "run set-key --stdin when no interactive terminal is available"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass("OpenRouter API key: ")
        raw = value.encode("ascii")
    except (
        EOFError,
        KeyboardInterrupt,
        UnicodeEncodeError,
        getpass.GetPassWarning,
    ) as exc:
        raise CredentialError("OpenRouter key input was not accepted") from exc
    return validate_key(raw)


def backend_name() -> str:
    if sys.platform == "darwin":
        return "macos-keychain"
    if os.name == "nt":
        return "windows-dpapi"
    if sys.platform.startswith("linux"):
        return "linux-secret-service"
    raise CredentialBackendUnavailable(
        "OpenRouter credential storage is unsupported on this platform"
    )


def _secret_tool() -> str:
    executable = shutil.which("secret-tool")
    if not executable:
        raise CredentialBackendUnavailable(
            "Linux Secret Service is unavailable because secret-tool was not found"
        )
    return executable


def _run_secret_tool(
    arguments: list[str],
    *,
    input_bytes: bytes | None = None,
    missing_is_empty: bool = False,
) -> bytes:
    command = [_secret_tool(), *arguments]
    try:
        completed = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CredentialBackendUnavailable(
            "Linux Secret Service could not be reached"
        ) from exc
    if (
        completed.returncode == 1
        and missing_is_empty
        and not completed.stderr.strip()
    ):
        return b""
    if completed.returncode != 0:
        raise CredentialBackendUnavailable(
            "Linux Secret Service rejected the credential operation"
        )
    return completed.stdout


def _linux_store(key: bytes) -> None:
    _run_secret_tool(
        ["store", "--label=Airlock OpenRouter API key", *SECRET_SERVICE_ATTRIBUTES],
        input_bytes=key + b"\n",
    )


def _linux_lookup_raw() -> bytes | None:
    value = _run_secret_tool(
        ["lookup", *SECRET_SERVICE_ATTRIBUTES], missing_is_empty=True
    )
    if not value:
        return None
    if value.endswith(b"\n"):
        value = value[:-1]
    return value


def _linux_load() -> bytes | None:
    value = _linux_lookup_raw()
    return None if value is None else validate_key(value)


def _linux_delete() -> bool:
    existing = _linux_lookup_raw()
    if existing is None:
        return False
    _run_secret_tool(
        ["clear", *SECRET_SERVICE_ATTRIBUTES], missing_is_empty=True
    )
    return True


def _load_macos_libraries():
    try:
        security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
    except OSError as exc:
        raise CredentialBackendUnavailable("macOS Keychain is unavailable") from exc
    security.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainAddGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemModifyAttributesAndData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
    security.SecKeychainItemDelete.restype = ctypes.c_int32
    security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    security.SecKeychainItemFreeContent.restype = ctypes.c_int32
    core.CFRelease.argtypes = [ctypes.c_void_p]
    core.CFRelease.restype = None
    return security, core


def _macos_find():
    security, core = _load_macos_libraries()
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(KEYCHAIN_SERVICE),
        KEYCHAIN_SERVICE,
        len(KEYCHAIN_ACCOUNT),
        KEYCHAIN_ACCOUNT,
        ctypes.byref(length),
        ctypes.byref(data),
        ctypes.byref(item),
    )
    return security, core, status, length, data, item


def _macos_store(key: bytes) -> None:
    security, core, status, length, data, item = _macos_find()
    if data:
        ctypes.memset(data, 0, length.value)
        security.SecKeychainItemFreeContent(None, data)
    buffer = ctypes.create_string_buffer(key)
    try:
        if status == 0:
            result = security.SecKeychainItemModifyAttributesAndData(
                item, None, len(key), ctypes.cast(buffer, ctypes.c_void_p)
            )
        elif status == -25300:
            result = security.SecKeychainAddGenericPassword(
                None,
                len(KEYCHAIN_SERVICE),
                KEYCHAIN_SERVICE,
                len(KEYCHAIN_ACCOUNT),
                KEYCHAIN_ACCOUNT,
                len(key),
                ctypes.cast(buffer, ctypes.c_void_p),
                None,
            )
        else:
            raise CredentialBackendUnavailable("macOS Keychain lookup failed")
        if result != 0:
            raise CredentialBackendUnavailable("macOS Keychain write failed")
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if item:
            core.CFRelease(item)


def _macos_load() -> bytes | None:
    security, core, status, length, data, item = _macos_find()
    try:
        if status == -25300:
            return None
        if status != 0 or not data:
            raise CredentialBackendUnavailable("macOS Keychain lookup failed")
        value = ctypes.string_at(data, length.value)
        return validate_key(value)
    finally:
        if data:
            ctypes.memset(data, 0, length.value)
            security.SecKeychainItemFreeContent(None, data)
        if item:
            core.CFRelease(item)


def _macos_delete() -> bool:
    security, core, status, length, data, item = _macos_find()
    try:
        if status == -25300:
            return False
        if status != 0 or not item:
            raise CredentialBackendUnavailable("macOS Keychain lookup failed")
        if security.SecKeychainItemDelete(item) != 0:
            raise CredentialBackendUnavailable("macOS Keychain delete failed")
        return True
    finally:
        if data:
            ctypes.memset(data, 0, length.value)
            security.SecKeychainItemFreeContent(None, data)
        if item:
            core.CFRelease(item)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(value: bytes):
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return buffer, _DataBlob(len(value), buffer)


def _windows_credential_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    return root / "Airlock" / "credentials" / "openrouter.dpapi"


def _windows_crypt32():
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise CredentialBackendUnavailable("Windows DPAPI is unavailable") from exc
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _dpapi_protect(value: bytes) -> bytes:
    crypt32, kernel32 = _windows_crypt32()
    input_buffer, input_blob = _blob(value)
    entropy_buffer, entropy_blob = _blob(DPAPI_ENTROPY)
    output = _DataBlob()
    try:
        if not crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "Airlock OpenRouter API key",
            ctypes.byref(entropy_blob),
            None,
            None,
            0x1,
            ctypes.byref(output),
        ):
            raise CredentialBackendUnavailable("Windows DPAPI encryption failed")
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(input_buffer, 0, len(input_buffer))
        ctypes.memset(entropy_buffer, 0, len(entropy_buffer))
        if output.pbData:
            kernel32.LocalFree(output.pbData)


def _dpapi_unprotect(value: bytes) -> bytes:
    crypt32, kernel32 = _windows_crypt32()
    input_buffer, input_blob = _blob(value)
    entropy_buffer, entropy_blob = _blob(DPAPI_ENTROPY)
    output = _DataBlob()
    description = ctypes.c_void_p()
    try:
        if not crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            ctypes.byref(description),
            ctypes.byref(entropy_blob),
            None,
            None,
            0x1,
            ctypes.byref(output),
        ):
            raise CredentialBackendUnavailable("Windows DPAPI decryption failed")
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(input_buffer, 0, len(input_buffer))
        ctypes.memset(entropy_buffer, 0, len(entropy_buffer))
        if output.pbData:
            ctypes.memset(output.pbData, 0, output.cbData)
            kernel32.LocalFree(output.pbData)
        if description:
            kernel32.LocalFree(description)


def _windows_store(key: bytes) -> None:
    path = _windows_credential_path()
    parent = path.parent
    temporary_path: Path | None = None
    descriptor = -1
    try:
        parent_existed = parent.exists()
        if path.is_symlink() or parent.is_symlink() or (
            parent_existed and not parent.is_dir()
        ):
            raise CredentialError("Windows credential path is unsafe")
        if parent_existed:
            policy._validate_windows_path_security(
                parent,
                require_current_owner=False,
            )
        if path.exists():
            if not path.is_file():
                raise CredentialError("Windows credential path is unsafe")
            policy._validate_windows_path_security(path)
            existing = path.read_bytes()
            if (
                len(existing) > MAX_ENCRYPTED_BYTES
                or not existing.startswith(DPAPI_MAGIC)
            ):
                raise CredentialError(
                    "Windows credential path contains an unknown file"
                )
        encrypted = DPAPI_MAGIC + _dpapi_protect(key)
        if len(encrypted) > MAX_ENCRYPTED_BYTES:
            raise CredentialError("Windows encrypted credential is too large")
        parent.mkdir(parents=True, exist_ok=True)
        if parent_existed:
            policy._validate_windows_path_security(
                parent,
                require_current_owner=False,
            )
        else:
            policy._protect_windows_path(parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="openrouter-", suffix=".tmp", dir=parent
        )
        temporary_path = Path(temporary_name)
        policy._protect_windows_path(temporary_path)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        policy._protect_windows_path(path)
    except CredentialError:
        raise
    except policy.PolicyValidationError as exc:
        raise CredentialError("Windows credential path is unsafe") from exc
    except OSError as exc:
        raise CredentialError("Windows credential could not be stored") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _windows_load() -> bytes | None:
    path = _windows_credential_path()
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise CredentialError("Windows credential path is unsafe")
        if not path.exists():
            return None
        if not path.is_file():
            raise CredentialError("Windows credential path is unsafe")
        policy._validate_windows_path_security(
            path.parent,
            require_current_owner=False,
        )
        policy._validate_windows_path_security(path)
        raw = path.read_bytes()
    except policy.PolicyValidationError as exc:
        raise CredentialError("Windows credential path is unsafe") from exc
    except OSError as exc:
        raise CredentialError("Windows credential could not be read") from exc
    if len(raw) > MAX_ENCRYPTED_BYTES or not raw.startswith(DPAPI_MAGIC):
        raise CredentialError("Windows credential data is invalid")
    return validate_key(_dpapi_unprotect(raw[len(DPAPI_MAGIC):]))


def _windows_delete() -> bool:
    path = _windows_credential_path()
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise CredentialError("Windows credential path is unsafe")
        if not path.exists():
            return False
        if not path.is_file():
            raise CredentialError("Windows credential path is unsafe")
        policy._validate_windows_path_security(
            path.parent,
            require_current_owner=False,
        )
        policy._validate_windows_path_security(path)
        path.unlink()
        return True
    except policy.PolicyValidationError as exc:
        raise CredentialError("Windows credential path is unsafe") from exc
    except OSError as exc:
        raise CredentialError("Windows credential could not be deleted") from exc


def store_key(key: bytes | bytearray) -> None:
    value = validate_key(key)
    backend = backend_name()
    if backend == "macos-keychain":
        _macos_store(value)
    elif backend == "windows-dpapi":
        _windows_store(value)
    else:
        _linux_store(value)


def load_key() -> bytes | None:
    backend = backend_name()
    if backend == "macos-keychain":
        return _macos_load()
    if backend == "windows-dpapi":
        return _windows_load()
    return _linux_load()


def delete_key() -> bool:
    backend = backend_name()
    if backend == "macos-keychain":
        return _macos_delete()
    if backend == "windows-dpapi":
        return _windows_delete()
    return _linux_delete()


def local_status() -> str:
    try:
        return "configured" if load_key() is not None else "missing"
    except CredentialBackendUnavailable:
        return "backend-unavailable"
    except CredentialError:
        return "unreadable"


def backend_status() -> tuple[str, str]:
    """Probe credential storage availability without loading the credential."""

    try:
        backend = backend_name()
        if backend == "macos-keychain":
            _load_macos_libraries()
        elif backend == "windows-dpapi":
            _windows_crypt32()
        else:
            _secret_tool()
        return backend, "available"
    except CredentialBackendUnavailable:
        try:
            backend = backend_name()
        except CredentialBackendUnavailable:
            backend = "unsupported"
        return backend, "unavailable"


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "airlock openrouter auth: invalid arguments\n")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    set_key = subparsers.add_parser("set-key", help="store an OpenRouter key")
    set_key.add_argument(
        "--stdin", action="store_true", help="read the key from bounded standard input"
    )
    subparsers.add_parser("status", help="report local credential status")
    logout = subparsers.add_parser("logout", help="delete the local credential")
    logout.add_argument("--yes", action="store_true", help="confirm deletion")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["_backend-status"]:
        backend, state = backend_status()
        print(f"BACKEND={backend}")
        print(f"STATE={state}")
        return 0 if state == "available" else 2
    arguments = build_parser().parse_args(arguments)
    try:
        if arguments.command == "set-key":
            key = read_key_from_stdin() if arguments.stdin else read_key_hidden()
            store_key(key)
            print(f"OpenRouter credential stored in {backend_name()}.")
            return 0
        if arguments.command == "status":
            state = local_status()
            print(f"OpenRouter credential: {state} ({backend_name()}).")
            return 2 if state == "backend-unavailable" else 0
        if not arguments.yes:
            try:
                answer = input(
                    "Delete the local OpenRouter credential? [y/N] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt) as exc:
                raise CredentialError(
                    "run logout --yes when no interactive terminal is available"
                ) from exc
            if answer not in {"y", "yes"}:
                print("OpenRouter credential was not changed.")
                return 0
        removed = delete_key()
        state = "deleted" if removed else "already missing"
        print(f"OpenRouter credential: {state} ({backend_name()}).")
        return 0
    except CredentialError as exc:
        print(f"airlock openrouter auth: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
