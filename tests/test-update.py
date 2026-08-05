#!/usr/bin/env python3
"""Offline tests for verified Airlock release updates."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "bin" / "airlock-update.py"
SPEC = importlib.util.spec_from_file_location("airlock_update", UPDATER_PATH)
assert SPEC is not None and SPEC.loader is not None
updater = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = updater
SPEC.loader.exec_module(updater)


class RouteServer:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict[str, str], bytes, float]] = {}
        self.requests: list[str] = []
        self.headers_seen: list[dict[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.requests.append(self.path)
                outer.headers_seen.append({name: value for name, value in self.headers.items()})
                path = self.path.split("?", 1)[0]
                status, headers, body, delay = outer.routes.get(
                    path, (404, {"Content-Type": "text/plain"}, b"missing", 0)
                )
                if delay:
                    time.sleep(delay)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, format_text: str, *arguments: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "RouteServer":
        self.thread.start()
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def url(self, path: str) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}{path}"

    def add(
        self,
        path: str,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        delay: float = 0,
    ) -> None:
        self.routes[path] = (status, headers or {}, body, delay)


@contextmanager
def update_environment(server: RouteServer, **values: str):
    environment = {
        "AIRLOCK_UPDATE_TESTING": "1",
        "AIRLOCK_UPDATE_API_URL": server.url("/releases"),
        "AIRLOCK_UPDATE_TEST_PLATFORM": "posix",
        "AIRLOCK_UPDATE_GH": "none",
        "AIRLOCK_ACTIVE_PROFILE": "",
    }
    environment.update(values)
    with mock.patch.dict(os.environ, environment, clear=False):
        yield


def release_payload(server: RouteServer, version: str, *, prerelease: bool = True) -> bytes:
    archive_name = f"airlock-{version}.tar.gz"
    return json.dumps(
        [
            {
                "draft": False,
                "prerelease": prerelease,
                "tag_name": f"v{version}",
                "assets": [
                    {"name": archive_name, "browser_download_url": server.url("/archive")},
                    {"name": "SHA256SUMS", "browser_download_url": server.url("/checksums")},
                ],
            }
        ]
    ).encode("utf-8")


def tar_bytes(version: str, extra: dict[str, tuple[bytes, int]] | None = None) -> bytes:
    root = f"airlock-{version}"
    members: dict[str, tuple[bytes, int]] = {
        f"{root}/VERSION": (f"{version}\n".encode(), 0o644),
        f"{root}/plugins/airlock/.claude-plugin/plugin.json": (
            json.dumps({"name": "airlock", "version": version}).encode(),
            0o644,
        ),
        f"{root}/scripts/install.sh": (
            b"#!/usr/bin/env bash\nset -eu\nprintf installed > \"$AIRLOCK_UPDATE_TEST_RESULT/install\"\n",
            0o755,
        ),
        f"{root}/scripts/doctor.sh": (
            b"#!/usr/bin/env bash\nset -eu\nprintf checked > \"$AIRLOCK_UPDATE_TEST_RESULT/doctor\"\n",
            0o755,
        ),
    }
    if extra:
        members.update(extra)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, (content, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def zip_bytes(version: str, extra: dict[str, tuple[bytes, int]] | None = None) -> bytes:
    root = f"airlock-{version}"
    members: dict[str, tuple[bytes, int]] = {
        f"{root}/VERSION": (f"{version}\n".encode(), 0o100644),
        f"{root}/plugins/airlock/.claude-plugin/plugin.json": (
            json.dumps({"name": "airlock", "version": version}).encode(),
            0o100644,
        ),
        f"{root}/scripts/install.ps1": (b"Write-Host installed\n", 0o100644),
        f"{root}/scripts/doctor.ps1": (b"Write-Host checked\n", 0o100644),
    }
    if extra:
        members.update(extra)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, (content, mode) in members.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def manifest(path: Path, version: str = "0.1.0-beta.1") -> Path:
    path.write_text(json.dumps({"name": "airlock", "version": version}), encoding="utf-8")
    return path


class SemanticVersionTests(unittest.TestCase):
    def test_semver_precedence_matches_the_standard_chain(self) -> None:
        values = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        parsed = [updater.SemVer.parse(value) for value in values]
        self.assertEqual(parsed, sorted(reversed(parsed)))
        self.assertEqual(updater.SemVer.parse("1.0.0+build.1"), updater.SemVer.parse("1.0.0+build.2"))
        self.assertEqual(hash(updater.SemVer.parse("1.0.0+one")), hash(updater.SemVer.parse("1.0.0+two")))

    def test_semver_rejects_ambiguous_versions(self) -> None:
        for value in ("v1.0.0", "01.0.0", "1.0", "1.0.0-01", "1.0.0-"):
            with self.subTest(value=value), self.assertRaises(updater.UpdateError):
                updater.SemVer.parse(value)

    def test_channel_selection(self) -> None:
        beta = updater.SemVer.parse("1.0.0-beta.1")
        stable = updater.SemVer.parse("1.0.0")
        releases = [
            updater.Release(updater.SemVer.parse("1.0.1-beta.1"), "v1.0.1-beta.1", ()),
            updater.Release(updater.SemVer.parse("1.0.1"), "v1.0.1", ()),
            updater.Release(updater.SemVer.parse("1.1.0-beta.1"), "v1.1.0-beta.1", ()),
        ]
        self.assertEqual(str(updater.select_release(releases, beta).version), "1.1.0-beta.1")
        self.assertEqual(str(updater.select_release(releases, stable).version), "1.0.1")


class ReleaseMetadataTests(unittest.TestCase):
    def test_drafts_are_ignored_and_inconsistent_flags_fail(self) -> None:
        draft = json.dumps(
            [{"draft": True, "prerelease": True, "tag_name": "v9.0.0-beta.1", "assets": []}]
        ).encode()
        self.assertEqual(updater.parse_release_list(draft), [])
        bad = json.dumps(
            [{"draft": False, "prerelease": False, "tag_name": "v1.0.0-beta.1", "assets": []}]
        ).encode()
        with self.assertRaisesRegex(updater.UpdateError, "inconsistent prerelease"):
            updater.parse_release_list(bad)

    def test_release_requires_exact_platform_assets(self) -> None:
        release = updater.Release(
            updater.SemVer.parse("1.0.0"),
            "v1.0.0",
            ({"name": "SHA256SUMS", "url": "https://github.com/x"},),
        )
        with self.assertRaisesRegex(updater.UpdateError, "missing"):
            updater.release_assets(release, "posix")

    def test_checksum_requires_one_exact_well_formed_entry(self) -> None:
        digest = "a" * 64
        self.assertEqual(
            updater.parse_checksum_file(f"{digest}  airlock-1.0.0.tar.gz\n".encode(), "airlock-1.0.0.tar.gz"),
            digest,
        )
        duplicate = f"{digest}  airlock-1.0.0.tar.gz\n{digest} *airlock-1.0.0.tar.gz\n".encode()
        with self.assertRaisesRegex(updater.UpdateError, "one exact entry"):
            updater.parse_checksum_file(duplicate, "airlock-1.0.0.tar.gz")
        with self.assertRaisesRegex(updater.UpdateError, "malformed"):
            updater.parse_checksum_file(b"not a checksum\n", "airlock-1.0.0.tar.gz")

    def test_production_download_urls_are_restricted(self) -> None:
        with self.assertRaisesRegex(updater.UpdateError, "allowed GitHub"):
            updater.validate_download_url("https://example.com/airlock.tar.gz")
        with self.assertRaisesRegex(updater.UpdateError, "forbidden URL"):
            updater.validate_download_url("https://user:pass@github.com/file")


class ArchiveSafetyTests(unittest.TestCase):
    def test_valid_tar_and_zip_extract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            version = updater.SemVer.parse("1.2.3-beta.1")
            tar_path = temporary / "release.tar.gz"
            tar_path.write_bytes(tar_bytes(str(version)))
            tar_root = updater.extract_release_archive(tar_path, temporary / "tar", version, "posix")
            self.assertTrue((tar_root / "scripts" / "install.sh").is_file())
            zip_path = temporary / "release.zip"
            zip_path.write_bytes(zip_bytes(str(version)))
            zip_root = updater.extract_release_archive(zip_path, temporary / "zip", version, "windows")
            self.assertTrue((zip_root / "scripts" / "install.ps1").is_file())

    def test_tar_rejects_traversal_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            traversal = temporary / "traversal.tar.gz"
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                info = tarfile.TarInfo("../outside")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            traversal.write_bytes(output.getvalue())
            with self.assertRaises(updater.UpdateError):
                updater.extract_tar_archive(traversal, temporary / "out", "airlock-1.0.0")

            link = temporary / "link.tar.gz"
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                info = tarfile.TarInfo("airlock-1.0.0/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                archive.addfile(info)
            link.write_bytes(output.getvalue())
            with self.assertRaisesRegex(updater.UpdateError, "link or special"):
                updater.extract_tar_archive(link, temporary / "link-out", "airlock-1.0.0")

    def test_zip_rejects_symlinks_and_duplicate_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            archive_path = temporary / "unsafe.zip"
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                link = zipfile.ZipInfo("airlock-1.0.0/link")
                link.create_system = 3
                link.external_attr = 0o120777 << 16
                archive.writestr(link, "target")
            archive_path.write_bytes(output.getvalue())
            with self.assertRaisesRegex(updater.UpdateError, "link or special"):
                updater.extract_zip_archive(archive_path, temporary / "out", "airlock-1.0.0")

            duplicate_path = temporary / "duplicate.zip"
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("airlock-1.0.0/README", "one")
                archive.writestr("airlock-1.0.0/readme", "two")
            duplicate_path.write_bytes(output.getvalue())
            with self.assertRaisesRegex(updater.UpdateError, "duplicate"):
                updater.extract_zip_archive(duplicate_path, temporary / "duplicate", "airlock-1.0.0")

    def test_archive_version_must_match_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            archive = temporary / "release.tar.gz"
            archive.write_bytes(tar_bytes("1.0.1"))
            with self.assertRaisesRegex(updater.UpdateError, "top-level"):
                updater.extract_release_archive(
                    archive, temporary / "out", updater.SemVer.parse("1.0.2"), "posix"
                )


class NetworkTests(unittest.TestCase):
    def test_api_redirect_is_rejected(self) -> None:
        with RouteServer() as server:
            server.add("/releases", b"", status=302, headers={"Location": server.url("/other")})
            with update_environment(server), self.assertRaisesRegex(updater.UpdateError, "forbidden redirect"):
                updater.check_for_release(updater.SemVer.parse("1.0.0"))

    def test_api_size_limit_and_timeout(self) -> None:
        with RouteServer() as server:
            server.add("/releases", b"[]", headers={"Content-Length": str(updater.API_LIMIT + 1)})
            with update_environment(server), self.assertRaisesRegex(updater.UpdateError, "exceeds"):
                updater.check_for_release(updater.SemVer.parse("1.0.0"))

        with RouteServer() as server:
            server.add("/releases", b"[]", delay=0.1)
            with update_environment(server), mock.patch.object(updater, "TIMEOUT_SECONDS", 0.01):
                with self.assertRaisesRegex(updater.UpdateError, "could not download"):
                    updater.check_for_release(updater.SemVer.parse("1.0.0"))


class UpdateFlowTests(unittest.TestCase):
    def prepare_server(self, server: RouteServer, version: str, archive: bytes, checksum: str | None = None) -> None:
        archive_name = f"airlock-{version}.tar.gz"
        digest = checksum or hashlib.sha256(archive).hexdigest()
        server.add("/releases", release_payload(server, version))
        server.add("/archive", archive)
        server.add("/checksums", f"{digest}  {archive_name}\n".encode())

    def test_check_only_does_not_download_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text, RouteServer() as server:
            temporary = Path(temporary_text)
            server.add("/releases", release_payload(server, "0.1.0-beta.2"))
            output = io.StringIO()
            with update_environment(server), redirect_stdout(output):
                result = updater.run_update(manifest(temporary / "plugin.json"), True, False)
            self.assertEqual(result, 0)
            self.assertEqual(server.requests, ["/releases"])
            self.assertTrue(all("Authorization" not in headers for headers in server.headers_seen))
            self.assertTrue(all("0.1.0-beta.1" not in headers.get("User-Agent", "") for headers in server.headers_seen))
            self.assertIn("Available version: 0.1.0-beta.2", output.getvalue())

    def test_no_update_reports_current_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text, RouteServer() as server:
            temporary = Path(temporary_text)
            server.add("/releases", b"[]")
            output = io.StringIO()
            with update_environment(server), redirect_stdout(output):
                result = updater.run_update(manifest(temporary / "plugin.json"), True, False)
            self.assertEqual(result, 0)
            self.assertIn("is up to date", output.getvalue())

    def test_confirmed_update_installs_and_runs_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text, RouteServer() as server:
            temporary = Path(temporary_text)
            result_dir = temporary / "result"
            result_dir.mkdir()
            archive = tar_bytes("0.1.0-beta.2")
            self.prepare_server(server, "0.1.0-beta.2", archive)
            output = io.StringIO()
            with update_environment(server, AIRLOCK_UPDATE_TEST_RESULT=str(result_dir)), redirect_stdout(output):
                result = updater.run_update(manifest(temporary / "plugin.json"), False, True)
            self.assertEqual(result, 0)
            self.assertEqual((result_dir / "install").read_text(), "installed")
            self.assertEqual((result_dir / "doctor").read_text(), "checked")
            self.assertIn("SHA-256: verified", output.getvalue())
            self.assertIn("attestation was not verified", output.getvalue())

    def test_checksum_failure_changes_no_installed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text, RouteServer() as server:
            temporary = Path(temporary_text)
            result_dir = temporary / "result"
            result_dir.mkdir()
            archive = tar_bytes("0.1.0-beta.2")
            self.prepare_server(server, "0.1.0-beta.2", archive, checksum="0" * 64)
            with update_environment(server, AIRLOCK_UPDATE_TEST_RESULT=str(result_dir)):
                with self.assertRaisesRegex(updater.UpdateError, "SHA-256 verification failed"):
                    updater.run_update(manifest(temporary / "plugin.json"), False, True)
            self.assertEqual(list(result_dir.iterdir()), [])

    def test_confirmation_decline_changes_no_installed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text, RouteServer() as server:
            temporary = Path(temporary_text)
            result_dir = temporary / "result"
            result_dir.mkdir()
            archive = tar_bytes("0.1.0-beta.2")
            self.prepare_server(server, "0.1.0-beta.2", archive)
            with update_environment(server, AIRLOCK_UPDATE_TEST_RESULT=str(result_dir)):
                with mock.patch.object(updater, "confirm_install", return_value=False):
                    result = updater.run_update(manifest(temporary / "plugin.json"), False, False)
            self.assertEqual(result, 0)
            self.assertEqual(list(result_dir.iterdir()), [])

    def test_active_session_refuses_install_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text, RouteServer() as server:
            temporary = Path(temporary_text)
            server.add("/releases", b"[]")
            with update_environment(server, AIRLOCK_ACTIVE_PROFILE="hybrid-openai-root"):
                with self.assertRaisesRegex(updater.UpdateError, "active Airlock session"):
                    updater.run_update(manifest(temporary / "plugin.json"), False, True)
            self.assertEqual(server.requests, [])

    def test_noninteractive_confirmation_requires_yes(self) -> None:
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = False
        with mock.patch.object(updater.sys, "stdin", fake_stdin):
            with self.assertRaisesRegex(updater.UpdateError, "--yes"):
                updater.confirm_install()

    def test_attestation_runs_for_both_downloads_and_failure_stops(self) -> None:
        paths = [Path("archive"), Path("SHA256SUMS")]
        successful = subprocess.CompletedProcess([], 0)
        with mock.patch.object(updater, "gh_command", return_value="gh"), mock.patch.object(
            updater.subprocess, "run", return_value=successful
        ) as run:
            self.assertTrue(updater.verify_attestations(paths))
            self.assertEqual(run.call_count, 2)
            self.assertIn("--repo", run.call_args_list[0].args[0])
        failed = subprocess.CompletedProcess([], 1)
        with mock.patch.object(updater, "gh_command", return_value="gh"), mock.patch.object(
            updater.subprocess, "run", return_value=failed
        ):
            with self.assertRaisesRegex(updater.UpdateError, "attestation verification failed"):
                updater.verify_attestations(paths)

    def test_doctor_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            root = Path(temporary_text)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "install.sh").write_text("", encoding="utf-8")
            (scripts / "doctor.sh").write_text("", encoding="utf-8")
            results = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 7)]
            with mock.patch.object(updater.shutil, "which", return_value="bash"), mock.patch.object(
                updater.subprocess, "run", side_effect=results
            ):
                with self.assertRaisesRegex(updater.UpdateError, "Doctor exited with status 7"):
                    updater.install_and_check(root, "posix")


if __name__ == "__main__":
    unittest.main()
