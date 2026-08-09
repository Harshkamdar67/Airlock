#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Download and install verified Airlock GitHub releases."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from functools import total_ordering
from typing import BinaryIO, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)
import zipfile


REPOSITORY = "Harshkamdar67/Airlock"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=50"
GITHUB_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}
API_LIMIT = 2 * 1024 * 1024
CHECKSUM_LIMIT = 1024 * 1024
ARCHIVE_LIMIT = 64 * 1024 * 1024
EXTRACTED_LIMIT = 256 * 1024 * 1024
MEMBER_LIMIT = 10_000
MANIFEST_LIMIT = 64 * 1024
NOTICE_LIMIT = 4096
NOTICE_SCHEMA_VERSION = 1
NOTICE_KIND = "airlock-update-notice"
TIMEOUT_SECONDS = 20
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
CHECKSUM_PATTERN = re.compile(r"^([0-9a-fA-F]{64}) [ *]([^\r\n]+)$")
MANAGED_AGENT_MARKERS = (
    "<!-- Managed by https://github.com/Harshkamdar67/Airlock -->",
    "<!-- Managed by Airlock -->",
)
WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class UpdateError(Exception):
    """A user-facing updater failure."""


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] | None
    text: str

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        match = SEMVER_PATTERN.fullmatch(value)
        if not match:
            raise UpdateError(f"invalid semantic version: {value!r}")
        prerelease_text = match.group(4)
        prerelease = None
        if prerelease_text is not None:
            identifiers = tuple(prerelease_text.split("."))
            if any(identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0" for identifier in identifiers):
                raise UpdateError(f"invalid semantic version: {value!r}")
            prerelease = identifiers
        return cls(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            prerelease,
            value,
        )

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease is not None

    def __str__(self) -> str:
        return self.text

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        own_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if own_core != other_core:
            return own_core < other_core
        if self.prerelease is None:
            return False
        if other.prerelease is None:
            return True
        for own_identifier, other_identifier in zip(self.prerelease, other.prerelease):
            if own_identifier == other_identifier:
                continue
            own_numeric = own_identifier.isdigit()
            other_numeric = other_identifier.isdigit()
            if own_numeric and other_numeric:
                return int(own_identifier) < int(other_identifier)
            if own_numeric != other_numeric:
                return own_numeric
            return own_identifier < other_identifier
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class Release:
    version: SemVer
    tag: str
    assets: tuple[dict[str, object], ...]

    @property
    def page_url(self) -> str:
        return f"https://github.com/{REPOSITORY}/releases/tag/{self.tag}"


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class GitHubRedirectHandler(HTTPRedirectHandler):
    def __init__(self, testing_origin: tuple[str, str, int | None] | None = None):
        super().__init__()
        self.testing_origin = testing_origin

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        validate_download_url(new_url, testing_origin=self.testing_origin, redirected=True)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def testing_enabled() -> bool:
    return os.environ.get("AIRLOCK_UPDATE_TESTING") == "1"


def current_platform() -> str:
    if testing_enabled():
        override = os.environ.get("AIRLOCK_UPDATE_TEST_PLATFORM")
        if override in {"posix", "windows"}:
            return override
    return "windows" if os.name == "nt" else "posix"


def read_limited_file(path: Path, limit: int, description: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise UpdateError(f"{description} is missing or unsafe: {path}")
        if path.stat().st_size > limit:
            raise UpdateError(f"{description} is unexpectedly large")
        return path.read_bytes()
    except UpdateError:
        raise
    except OSError as error:
        raise UpdateError(f"could not read {description}: {error}") from error


def read_installed_version(manifest_path: Path) -> SemVer:
    raw = read_limited_file(manifest_path, MANIFEST_LIMIT, "managed plugin manifest")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError("managed plugin manifest is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or value.get("name") != "airlock":
        raise UpdateError("managed plugin manifest does not describe Airlock")
    version = value.get("version")
    if not isinstance(version, str):
        raise UpdateError("managed plugin manifest has no release version")
    return SemVer.parse(version)


def update_notice_payload(current: SemVer, release: Release) -> bytes:
    payload = {
        "schema_version": NOTICE_SCHEMA_VERSION,
        "kind": NOTICE_KIND,
        "checked_at": int(time.time()),
        "current_version": str(current),
        "available_version": str(release.version),
        "release_url": release.page_url,
    }
    encoded = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > NOTICE_LIMIT:
        raise UpdateError("update notice is unexpectedly large")
    return encoded


def replace_update_notice(source: Path, destination: Path) -> None:
    for attempt in range(10):
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            if os.name != "nt" or attempt == 9 or getattr(error, "winerror", None) not in {5, 32}:
                raise
            time.sleep(0.05)


def write_update_notice(path: Path | None, current: SemVer, release: Release) -> None:
    if path is None:
        return
    parent = path.parent
    temporary_path: Path | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise UpdateError(f"update notice path is unsafe: {path}")
        descriptor, temporary_text = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary_path = Path(temporary_text)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:
                os.chmod(temporary_path, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(update_notice_payload(current, release))
                handle.flush()
                os.fsync(handle.fileno())
            replace_update_notice(temporary_path, path)
            temporary_path = None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except UpdateError:
        raise
    except OSError as error:
        raise UpdateError(f"could not write update notice: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def clear_update_notice(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return
        path.unlink(missing_ok=True)
    except OSError:
        return


def releases_api_url() -> str:
    if testing_enabled() and os.environ.get("AIRLOCK_UPDATE_API_URL"):
        return os.environ["AIRLOCK_UPDATE_API_URL"]
    return RELEASES_API


def testing_origin_for(url: str) -> tuple[str, str, int | None] | None:
    if not testing_enabled():
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise UpdateError("test release API must use loopback HTTP or HTTPS")
    return parsed.scheme, parsed.hostname, parsed.port


def validate_download_url(
    url: str,
    *,
    testing_origin: tuple[str, str, int | None] | None = None,
    redirected: bool = False,
) -> None:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment:
        raise UpdateError("release asset URL contains forbidden URL fields")
    if testing_origin is not None:
        if (parsed.scheme, parsed.hostname, parsed.port) != testing_origin:
            raise UpdateError("test release asset left the loopback release origin")
        return
    if parsed.scheme != "https" or parsed.hostname not in GITHUB_HOSTS:
        where = "redirect" if redirected else "URL"
        raise UpdateError(f"release asset {where} is not an allowed GitHub HTTPS location")
    if parsed.port not in {None, 443}:
        raise UpdateError("release asset URL uses an unexpected port")


def response_length(response, limit: int, description: str) -> int | None:
    header = response.headers.get("Content-Length")
    if header is None:
        return None
    try:
        length = int(header)
    except ValueError as error:
        raise UpdateError(f"{description} returned an invalid Content-Length") from error
    if length < 0 or length > limit:
        raise UpdateError(f"{description} exceeds the {limit}-byte limit")
    return length


def copy_bounded(source: BinaryIO, destination: BinaryIO, limit: int, description: str) -> int:
    total = 0
    while True:
        chunk = source.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            return total
        total += len(chunk)
        if total > limit:
            raise UpdateError(f"{description} exceeds the {limit}-byte limit")
        destination.write(chunk)


def request_bytes(url: str, limit: int, description: str, *, allow_download_redirects: bool) -> bytes:
    testing_origin = testing_origin_for(releases_api_url())
    if allow_download_redirects:
        validate_download_url(url, testing_origin=testing_origin)
        opener = build_opener(GitHubRedirectHandler(testing_origin))
        accept = "application/octet-stream"
    else:
        if testing_origin is None and url != RELEASES_API:
            raise UpdateError("release API URL is not the managed GitHub endpoint")
        opener = build_opener(NoRedirectHandler())
        accept = "application/vnd.github+json"
    request = Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "Airlock updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            response_length(response, limit, description)
            output = io.BytesIO()
            copy_bounded(response, output, limit, description)
            return output.getvalue()
    except UpdateError:
        raise
    except HTTPError as error:
        if 300 <= error.code < 400:
            raise UpdateError(f"{description} returned a forbidden redirect") from error
        raise UpdateError(f"{description} returned HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise UpdateError(f"could not download {description}: {error}") from error


def download_file(url: str, target: Path, limit: int, description: str) -> None:
    testing_origin = testing_origin_for(releases_api_url())
    validate_download_url(url, testing_origin=testing_origin)
    opener = build_opener(GitHubRedirectHandler(testing_origin))
    request = Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "Airlock updater"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            expected_length = response_length(response, limit, description)
            with target.open("xb") as destination:
                written = copy_bounded(response, destination, limit, description)
            if expected_length is not None and written != expected_length:
                raise UpdateError(f"{description} ended before its declared length")
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except HTTPError as error:
        target.unlink(missing_ok=True)
        if 300 <= error.code < 400:
            raise UpdateError(f"{description} returned a forbidden redirect") from error
        raise UpdateError(f"{description} returned HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        target.unlink(missing_ok=True)
        raise UpdateError(f"could not download {description}: {error}") from error


def parse_release_list(raw: bytes) -> list[Release]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError("GitHub Releases returned invalid UTF-8 JSON") from error
    if not isinstance(value, list) or len(value) > 100:
        raise UpdateError("GitHub Releases returned an unexpected response")
    releases: list[Release] = []
    seen_versions: set[SemVer] = set()
    for item in value:
        if not isinstance(item, dict):
            raise UpdateError("GitHub Releases returned a malformed release")
        draft = item.get("draft")
        prerelease_flag = item.get("prerelease")
        tag = item.get("tag_name")
        assets = item.get("assets")
        if not isinstance(draft, bool) or not isinstance(prerelease_flag, bool):
            raise UpdateError("GitHub Releases returned malformed release flags")
        if draft:
            continue
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise UpdateError("GitHub Releases returned an invalid release tag")
        version = SemVer.parse(tag[1:])
        if prerelease_flag != version.is_prerelease:
            raise UpdateError(f"release {tag} has inconsistent prerelease metadata")
        if version in seen_versions:
            raise UpdateError(f"GitHub Releases returned duplicate version {version}")
        seen_versions.add(version)
        if not isinstance(assets, list) or len(assets) > 100:
            raise UpdateError(f"release {tag} has malformed assets")
        normalized_assets: list[dict[str, object]] = []
        for asset in assets:
            if not isinstance(asset, dict):
                raise UpdateError(f"release {tag} has a malformed asset")
            name = asset.get("name")
            url = asset.get("browser_download_url")
            if not isinstance(name, str) or not isinstance(url, str):
                raise UpdateError(f"release {tag} has a malformed asset")
            if not name or any(character in name for character in "/\\\x00\r\n"):
                raise UpdateError(f"release {tag} has an unsafe asset name")
            normalized_assets.append({"name": name, "url": url})
        releases.append(Release(version, tag, tuple(normalized_assets)))
    return releases


def select_release(releases: Iterable[Release], current: SemVer) -> Release | None:
    eligible = [
        release
        for release in releases
        if release.version > current
        and (current.is_prerelease or not release.version.is_prerelease)
    ]
    return max(eligible, key=lambda release: release.version) if eligible else None


def platform_archive_name(version: SemVer, platform_name: str) -> str:
    extension = ".zip" if platform_name == "windows" else ".tar.gz"
    return f"airlock-{version}{extension}"


def release_assets(release: Release, platform_name: str) -> tuple[str, str, str, str]:
    archive_name = platform_archive_name(release.version, platform_name)
    wanted = {archive_name, "SHA256SUMS"}
    found: dict[str, str] = {}
    for asset in release.assets:
        name = str(asset["name"])
        if name not in wanted:
            continue
        if name in found:
            raise UpdateError(f"release {release.tag} has duplicate {name} assets")
        found[name] = str(asset["url"])
    missing = sorted(wanted - found.keys())
    if missing:
        raise UpdateError(f"release {release.tag} is missing: {', '.join(missing)}")
    expected_prefix = f"https://github.com/{REPOSITORY}/releases/download/{release.tag}/"
    if not testing_enabled():
        for name in wanted:
            if found[name] != expected_prefix + name:
                raise UpdateError(f"release {release.tag} has an unexpected URL for {name}")
    return archive_name, found[archive_name], "SHA256SUMS", found["SHA256SUMS"]


def parse_checksum_file(raw: bytes, archive_name: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise UpdateError("SHA256SUMS is not UTF-8 text") from error
    matches: list[str] = []
    for line in text.splitlines():
        if not line:
            continue
        match = CHECKSUM_PATTERN.fullmatch(line)
        if not match:
            raise UpdateError("SHA256SUMS contains a malformed entry")
        name = match.group(2)
        if name == archive_name:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise UpdateError(f"SHA256SUMS must contain one exact entry for {archive_name}")
    return matches[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return digest.hexdigest()
                digest.update(chunk)
    except OSError as error:
        raise UpdateError(f"could not hash downloaded archive: {error}") from error


def gh_command() -> str | None:
    if testing_enabled() and "AIRLOCK_UPDATE_GH" in os.environ:
        value = os.environ["AIRLOCK_UPDATE_GH"]
        return None if value == "none" else value
    return shutil.which("gh")


def verify_attestations(paths: Iterable[Path]) -> bool:
    gh = gh_command()
    if gh is None:
        return False
    for path in paths:
        try:
            completed = subprocess.run(
                [gh, "attestation", "verify", str(path), "--repo", REPOSITORY],
                check=False,
            )
        except OSError as error:
            raise UpdateError(f"could not run GitHub attestation verification: {error}") from error
        if completed.returncode != 0:
            raise UpdateError(f"GitHub attestation verification failed for {path.name}")
    return True


def validate_member_name(name: str, expected_root: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name or len(name) > 4096:
        raise UpdateError("release archive contains an unsafe path")
    pure = PurePosixPath(name)
    if pure.is_absolute():
        raise UpdateError("release archive contains an absolute path")
    parts = pure.parts
    if not parts or parts[0] != expected_root:
        raise UpdateError("release archive has an unexpected top-level directory")
    for part in parts:
        if part in {"", ".", ".."} or len(part) > 255:
            raise UpdateError("release archive contains path traversal")
        if part[-1:] in {" ", "."} or any(character in WINDOWS_INVALID_CHARS for character in part):
            raise UpdateError("release archive contains a cross-platform unsafe path")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise UpdateError("release archive contains a reserved Windows path")
    return parts


def checked_target(extraction_root: Path, parts: tuple[str, ...]) -> Path:
    target = extraction_root.joinpath(*parts)
    try:
        target.relative_to(extraction_root)
    except ValueError as error:
        raise UpdateError("release archive path escaped the extraction directory") from error
    return target


def write_archive_member(source: BinaryIO, target: Path, size: int, executable: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise UpdateError("release archive contains duplicate paths")
    try:
        with target.open("xb") as destination:
            written = copy_bounded(source, destination, size, f"archive member {target.name}")
        if written != size:
            raise UpdateError(f"archive member {target.name} ended early")
        target.chmod(0o755 if executable else 0o644)
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except OSError as error:
        target.unlink(missing_ok=True)
        raise UpdateError(f"could not extract archive member {target.name}: {error}") from error


def extract_tar_archive(archive: Path, extraction_root: Path, expected_root: str) -> None:
    total_size = 0
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            if len(members) > MEMBER_LIMIT:
                raise UpdateError("release archive contains too many files")
            for member in members:
                parts = validate_member_name(member.name.rstrip("/"), expected_root)
                normalized = "/".join(parts).casefold()
                if normalized in seen:
                    raise UpdateError("release archive contains duplicate paths")
                seen.add(normalized)
                target = checked_target(extraction_root, parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise UpdateError("release archive contains a link or special file")
                total_size += member.size
                if member.size < 0 or total_size > EXTRACTED_LIMIT:
                    raise UpdateError("release archive expands beyond the safety limit")
                source = handle.extractfile(member)
                if source is None:
                    raise UpdateError(f"could not read archive member {member.name}")
                with source:
                    write_archive_member(source, target, member.size, bool(member.mode & 0o111))
    except UpdateError:
        raise
    except (tarfile.TarError, OSError, EOFError) as error:
        raise UpdateError(f"release tar archive is invalid: {error}") from error


def extract_zip_archive(archive: Path, extraction_root: Path, expected_root: str) -> None:
    total_size = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(archive, "r") as handle:
            members = handle.infolist()
            if len(members) > MEMBER_LIMIT:
                raise UpdateError("release archive contains too many files")
            for member in members:
                parts = validate_member_name(member.filename.rstrip("/"), expected_root)
                normalized = "/".join(parts).casefold()
                if normalized in seen:
                    raise UpdateError("release archive contains duplicate paths")
                seen.add(normalized)
                if member.flag_bits & 1:
                    raise UpdateError("release archive contains an encrypted file")
                if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise UpdateError("release archive uses an unsupported compression method")
                target = checked_target(extraction_root, parts)
                mode = (member.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if member.is_dir():
                    if file_type not in {0, stat.S_IFDIR}:
                        raise UpdateError("release archive contains an invalid directory")
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if file_type not in {0, stat.S_IFREG}:
                    raise UpdateError("release archive contains a link or special file")
                total_size += member.file_size
                if member.file_size < 0 or total_size > EXTRACTED_LIMIT:
                    raise UpdateError("release archive expands beyond the safety limit")
                with handle.open(member, "r") as source:
                    write_archive_member(source, target, member.file_size, bool(mode & 0o111))
    except UpdateError:
        raise
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as error:
        raise UpdateError(f"release ZIP archive is invalid: {error}") from error


def validate_extracted_release(release_root: Path, version: SemVer, platform_name: str) -> None:
    version_raw = read_limited_file(release_root / "VERSION", 256, "release VERSION")
    try:
        archive_version = version_raw.decode("utf-8").strip()
    except UnicodeError as error:
        raise UpdateError("release VERSION is not UTF-8 text") from error
    if archive_version != str(version):
        raise UpdateError("release archive VERSION does not match its GitHub tag")
    plugin_version = read_installed_version(
        release_root / "plugins" / "airlock" / ".claude-plugin" / "plugin.json"
    )
    if plugin_version != version:
        raise UpdateError("release plugin version does not match its GitHub tag")
    required = (
        ("scripts/install.ps1", "scripts/doctor.ps1")
        if platform_name == "windows"
        else ("scripts/install.sh", "scripts/doctor.sh")
    )
    for relative in required:
        target = release_root / Path(relative)
        if target.is_symlink() or not target.is_file():
            raise UpdateError(f"release archive is missing {relative}")


def extract_release_archive(archive: Path, extraction_root: Path, version: SemVer, platform_name: str) -> Path:
    expected_root = f"airlock-{version}"
    if platform_name == "windows":
        extract_zip_archive(archive, extraction_root, expected_root)
    else:
        extract_tar_archive(archive, extraction_root, expected_root)
    release_root = extraction_root / expected_root
    if release_root.is_symlink() or not release_root.is_dir():
        raise UpdateError("release archive did not create the expected root directory")
    validate_extracted_release(release_root, version, platform_name)
    return release_root


def managed_agent_installed() -> bool:
    agent_dir = Path(os.environ.get("AIRLOCK_AGENT_DIR", str(Path.home() / ".claude" / "agents")))
    target = agent_dir / "airlock-worker.md"
    try:
        if target.is_symlink() or not target.is_file() or target.stat().st_size > MANIFEST_LIMIT:
            return False
        prefix = target.read_text(encoding="utf-8", errors="strict")[:4096]
    except (OSError, UnicodeError):
        return False
    return any(marker in prefix for marker in MANAGED_AGENT_MARKERS)


def install_and_check(release_root: Path, platform_name: str) -> None:
    with_agent = managed_agent_installed()
    if platform_name == "windows":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            raise UpdateError("PowerShell is required to install this update")
        install_command = [
            powershell,
            "-NoProfile",
            "-File",
            str(release_root / "scripts" / "install.ps1"),
        ]
        if with_agent:
            install_command.append("-WithAgent")
        doctor_command = [
            powershell,
            "-NoProfile",
            "-File",
            str(release_root / "scripts" / "doctor.ps1"),
        ]
    else:
        bash = shutil.which("bash")
        if bash is None:
            raise UpdateError("Bash is required to install this update")
        install_command = [bash, str(release_root / "scripts" / "install.sh")]
        if with_agent:
            install_command.append("--with-agent")
        doctor_command = [bash, str(release_root / "scripts" / "doctor.sh")]
    try:
        installed = subprocess.run(install_command, check=False)
    except OSError as error:
        raise UpdateError(f"could not start the Airlock installer: {error}") from error
    if installed.returncode != 0:
        raise UpdateError(f"Airlock installer exited with status {installed.returncode}")
    try:
        checked = subprocess.run(doctor_command, check=False)
    except OSError as error:
        raise UpdateError(f"could not start Airlock Doctor: {error}") from error
    if checked.returncode != 0:
        raise UpdateError(f"Airlock Doctor exited with status {checked.returncode}")


def check_for_release(current: SemVer) -> Release | None:
    api_url = releases_api_url()
    testing_origin_for(api_url)
    raw = request_bytes(api_url, API_LIMIT, "GitHub Releases", allow_download_redirects=False)
    return select_release(parse_release_list(raw), current)


def print_release(current: SemVer, release: Release) -> None:
    print(f"Current version: {current}")
    print(f"Available version: {release.version}")
    print(f"Release: {release.page_url}")


def confirm_install() -> bool:
    if not sys.stdin.isatty():
        raise UpdateError("confirmation requires a terminal; use airlock update --yes for an explicit noninteractive install")
    try:
        answer = input("Install this verified update now? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def run_update(
    manifest: Path,
    check_only: bool,
    assume_yes: bool,
    notice_file: Path | None = None,
) -> int:
    if check_only and assume_yes:
        raise UpdateError("--check and --yes cannot be used together")
    current = read_installed_version(manifest)
    if not check_only and os.environ.get("AIRLOCK_ACTIVE_PROFILE"):
        raise UpdateError(
            "installation cannot run from an active Airlock session. Exit the session and run "
            "airlock update from your terminal. Use ! airlock update --check to check manually."
        )
    release = check_for_release(current)
    if release is None:
        clear_update_notice(notice_file)
        print(f"Airlock {current} is up to date for its release channel.")
        return 0
    if check_only:
        print_release(current, release)
        try:
            write_update_notice(notice_file, current, release)
        except UpdateError as error:
            print(
                f"Warning: {error}. Future sessions will not show this update notice.",
                file=sys.stderr,
            )
        print("Run airlock update to download, verify, and install it.")
        return 0

    platform_name = current_platform()
    archive_name, archive_url, checksum_name, checksum_url = release_assets(release, platform_name)
    with tempfile.TemporaryDirectory(prefix="airlock-update-") as temporary_text:
        temporary = Path(temporary_text)
        archive_path = temporary / archive_name
        checksum_path = temporary / checksum_name
        print(f"Downloading Airlock {release.version}...")
        download_file(checksum_url, checksum_path, CHECKSUM_LIMIT, checksum_name)
        download_file(archive_url, archive_path, ARCHIVE_LIMIT, archive_name)
        expected_checksum = parse_checksum_file(
            read_limited_file(checksum_path, CHECKSUM_LIMIT, checksum_name), archive_name
        )
        actual_checksum = sha256_file(archive_path)
        if actual_checksum != expected_checksum:
            raise UpdateError(f"SHA-256 verification failed for {archive_name}")
        attested = verify_attestations((archive_path, checksum_path))
        release_root = extract_release_archive(
            archive_path, temporary / "extracted", release.version, platform_name
        )
        print_release(current, release)
        print(f"SHA-256: verified ({actual_checksum})")
        if attested:
            print("GitHub attestation: verified")
        else:
            print("Warning: GitHub CLI is not installed, so build attestation was not verified.")
        if not assume_yes and not confirm_install():
            print("Update cancelled. No installed files were changed.")
            return 0
        install_and_check(release_root, platform_name)
    clear_update_notice(notice_file)
    print(f"Airlock updated to {release.version}. Start a fresh Airlock session.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airlock", description="Airlock release update commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    version_parser = subparsers.add_parser("version", help="show the installed Airlock version")
    version_parser.add_argument("--manifest", type=Path, required=True, help=argparse.SUPPRESS)
    update_parser = subparsers.add_parser("update", help="check for and install a verified update")
    update_parser.add_argument("--manifest", type=Path, required=True, help=argparse.SUPPRESS)
    update_parser.add_argument("--notice-file", type=Path, help=argparse.SUPPRESS)
    mode = update_parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report an available update without downloading it")
    mode.add_argument("--yes", action="store_true", help="install without an interactive confirmation")
    return parser


def main(arguments: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(arguments)
    try:
        if parsed.command == "version":
            if getattr(parsed, "check", False) or getattr(parsed, "yes", False):
                raise UpdateError("version accepts no update options")
            print(f"Airlock {read_installed_version(parsed.manifest)}")
            return 0
        return run_update(parsed.manifest, parsed.check, parsed.yes, parsed.notice_file)
    except UpdateError as error:
        print(f"airlock {parsed.command}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
