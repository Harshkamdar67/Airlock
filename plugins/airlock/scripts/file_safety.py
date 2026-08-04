#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Shared repository file-safety checks for native hooks."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import stat

SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
ENV_KEY_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    rb"-----BEGIN (?P<label>(?:ENCRYPTED |RSA |EC |OPENSSH )?PRIVATE KEY)-----[ \t]*\r?\n"
    rb"(?P<body>(?:[A-Za-z0-9+/=]{1,128}[ \t]*\r?\n)+)"
    rb"-----END (?P=label)-----"
)
CREDENTIAL_BASENAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".credentials.json",
    "credentials.json",
    "secrets.json",
    "service-account.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
CREDENTIAL_SUFFIXES = {".key", ".p12", ".pfx"}
CREDENTIAL_PATH_SUFFIXES = {
    "codex/auth.json",
    "claude/.credentials.json",
    "aws/credentials",
    "docker/config.json",
    "kube/config",
}
CREDENTIAL_JSON_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
}
MAX_ENV_BYTES = 1024 * 1024
MAX_KEYS = 256


class FileSafetyError(RuntimeError):
    pass


def configured_paths(variable: str) -> set[str]:
    return {
        item.strip().replace("\\", "/")
        for item in os.environ.get(variable, "").split(",")
        if item.strip()
    }


def normalize_relative_path(relative: str) -> str | None:
    normalized = relative.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        return None
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def is_sensitive_env_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    name = Path(normalized).name.casefold()
    if name in SAFE_ENV_NAMES:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    configured = configured_paths("AIRLOCK_SENSITIVE_ENV_FILES")
    return (
        normalized in configured
        or Path(normalized).name in configured
        or any(
            "/" in item and normalized.endswith("/" + item.lstrip("/"))
            for item in configured
        )
    )


def is_sensitive_credential_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/").lstrip("./")
    lowered = normalized.casefold()
    name = Path(normalized).name.casefold()
    configured = {item.casefold() for item in configured_paths("AIRLOCK_SENSITIVE_FILES")}
    return (
        name in CREDENTIAL_BASENAMES
        or Path(name).suffix in CREDENTIAL_SUFFIXES
        or any(
            lowered == suffix or lowered.endswith("/" + suffix)
            for suffix in CREDENTIAL_PATH_SUFFIXES
        )
        or lowered in configured
        or name in configured
        or any(
            "/" in item and lowered.endswith("/" + item.lstrip("/"))
            for item in configured
        )
    )


def json_contains_credential(value: object) -> bool:
    pending = [value]
    visited = 0
    while pending and visited < 10_000:
        current = pending.pop()
        visited += 1
        if isinstance(current, dict):
            for key, child in current.items():
                if (
                    isinstance(key, str)
                    and key.casefold() in CREDENTIAL_JSON_KEYS
                    and isinstance(child, str)
                    and bool(child.strip())
                ):
                    return True
                pending.append(child)
        elif isinstance(current, list):
            pending.extend(current)
    return False


def contains_private_key_block(payload: bytes) -> bool:
    for match in PRIVATE_KEY_BLOCK_PATTERN.finditer(payload):
        body = re.sub(rb"\s+", b"", match.group("body"))
        try:
            decoded = base64.b64decode(body, validate=True)
        except (ValueError, binascii.Error):
            continue
        if len(decoded) >= 32:
            return True
    return False


def contains_high_confidence_credential(relative: str, payload: bytes) -> bool:
    if contains_private_key_block(payload):
        return True
    if Path(relative).suffix.casefold() != ".json" or len(payload) > MAX_ENV_BYTES:
        return False
    try:
        decoded = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError):
        return False
    return json_contains_credential(decoded)


def env_projection(relative: str, payload: bytes) -> bytes:
    if len(payload) > MAX_ENV_BYTES:
        raise FileSafetyError(f"sensitive env file is too large to project safely: {relative}")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as error:
        raise FileSafetyError(
            f"sensitive env file is not valid UTF-8: {relative}"
        ) from error
    keys: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = ENV_KEY_PATTERN.match(line)
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        keys.append(match.group(1))
        if len(keys) >= MAX_KEYS:
            break
    projection = {
        "source_kind": "env",
        "source_name": Path(relative).name,
        "keys": keys,
        "truncated": len(keys) >= MAX_KEYS,
        "values_exposed": False,
    }
    return (
        json.dumps(projection, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("utf-8")


def stable_regular_payload(
    root: Path,
    relative: str,
    *,
    maximum_bytes: int,
) -> tuple[bytes, int]:
    normalized = normalize_relative_path(relative)
    if normalized is None:
        raise FileSafetyError(f"repository path is invalid: {relative}")
    resolved_root = root.resolve()
    path = resolved_root.joinpath(*normalized.split("/"))
    try:
        current = resolved_root
        for part in normalized.split("/"):
            current = current / part
            metadata = current.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                reparse_flag
                and getattr(metadata, "st_file_attributes", 0) & reparse_flag
            ):
                raise FileSafetyError(
                    f"repository path crosses a link or reparse point: {normalized}"
                )
        before = path.lstat()
        resolved = path.resolve(strict=True)
    except FileSafetyError:
        raise
    except OSError as error:
        raise FileSafetyError(
            f"repository path could not be inspected safely: {normalized}"
        ) from error
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise FileSafetyError(
            f"repository path escapes the checkout: {normalized}"
        ) from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise FileSafetyError(
            f"repository path is not a regular file: {normalized}"
        )
    if before.st_size > maximum_bytes:
        raise FileSafetyError(f"repository file exceeds the safety limit: {normalized}")
    try:
        payload = path.read_bytes()
        after = path.lstat()
    except OSError as error:
        raise FileSafetyError(
            f"repository path could not be read safely: {normalized}"
        ) from error
    identity_before = (
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_dev,
        before.st_ino,
    )
    identity_after = (
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_dev,
        after.st_ino,
    )
    if identity_before != identity_after or path.is_symlink():
        raise FileSafetyError(
            f"repository path changed while it was being prepared: {normalized}"
        )
    mode = 0o755 if before.st_mode & stat.S_IXUSR else 0o644
    return payload, mode
