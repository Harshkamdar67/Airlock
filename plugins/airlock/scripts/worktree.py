#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Create and remove filtered native Claude Code worktrees."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import NoReturn

from file_safety import (
    FileSafetyError,
    contains_high_confidence_credential,
    env_projection,
    is_sensitive_credential_path,
    is_sensitive_env_path,
    normalize_relative_path,
    stable_regular_payload,
)

MAX_EVENT_BYTES = 1024 * 1024
MAX_TRACKED_FILE_BYTES = 64 * 1024 * 1024
MAX_TRACKED_TOTAL_BYTES = 512 * 1024 * 1024
MAX_UNTRACKED_FILES = 2_000
MAX_UNTRACKED_FILE_BYTES = 16 * 1024 * 1024
MAX_UNTRACKED_TOTAL_BYTES = 64 * 1024 * 1024
SNAPSHOT_SUBJECT = "Airlock native worktree snapshot"
BRANCH_PREFIX = "airlock-worktree-"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
GIT_ENVIRONMENT_KEYS = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
}


class WorktreeError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    print(f"airlock worktree: {message}", file=sys.stderr)
    raise SystemExit(1)


def git_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    for name in GIT_ENVIRONMENT_KEYS:
        environment.pop(name, None)
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    if extra:
        environment.update(extra)
    return environment


def run_git(
    args: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=environment or git_environment(),
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorktreeError("Git could not prepare the isolated worktree") from error
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        reason = detail[-1][:300] if detail else "Git returned a failure"
        raise WorktreeError(reason)
    return completed


def output_text(completed: subprocess.CompletedProcess[bytes]) -> str:
    try:
        return completed.stdout.decode("utf-8").strip()
    except UnicodeError as error:
        raise WorktreeError("Git returned a non-UTF-8 path") from error


def read_event() -> dict[str, object]:
    raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    if len(raw) > MAX_EVENT_BYTES:
        raise WorktreeError("hook input exceeds the safety limit")
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise WorktreeError("hook input is not valid JSON") from error
    if not isinstance(event, dict):
        raise WorktreeError("hook input must be a JSON object")
    return event


def event_path(event: dict[str, object], field: str) -> Path:
    value = event.get(field)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorktreeError(f"hook field is invalid: {field}")
    path = Path(value)
    if not path.is_absolute():
        raise WorktreeError(f"hook path must be absolute: {field}")
    return path


def repository_root(cwd: Path) -> Path:
    if not cwd.is_dir() or cwd.is_symlink():
        raise WorktreeError("hook working directory is missing or unsafe")
    root = Path(output_text(run_git(["rev-parse", "--show-toplevel"], cwd=cwd)))
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise WorktreeError("repository root is missing or unsafe")
    resolved = root.resolve()
    try:
        cwd.resolve().relative_to(resolved)
    except ValueError as error:
        raise WorktreeError("hook working directory is outside the repository") from error
    return resolved


def normalized_name(raw: object) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 64 or "\x00" in raw:
        raise WorktreeError("worktree name is invalid")
    normalized = raw.replace("\\", "/")
    parts = normalized.split("/")
    if any(
        part in {"", ".", ".."}
        or not NAME_PATTERN.fullmatch(part)
        for part in parts
    ):
        raise WorktreeError("worktree name contains an unsafe path segment")
    return "/".join(parts)


def is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        reparse_flag and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def prepare_destination(repo_root: Path, name: str) -> Path:
    root = repo_root / ".claude" / "worktrees"
    current = repo_root
    for part in (".claude", "worktrees"):
        current = current / part
        if is_link_or_reparse(current):
            raise WorktreeError("managed worktree directory crosses a link or reparse point")
        if current.exists() and not current.is_dir():
            raise WorktreeError("managed worktree directory is not a directory")
        current.mkdir(mode=0o700, exist_ok=True)
    destination = root.joinpath(*name.split("/"))
    current = root
    for part in name.split("/")[:-1]:
        current = current / part
        if is_link_or_reparse(current):
            raise WorktreeError("managed worktree path crosses a link or reparse point")
        if current.exists() and not current.is_dir():
            raise WorktreeError("managed worktree parent is not a directory")
        current.mkdir(mode=0o700, exist_ok=True)
    if os.path.lexists(destination):
        raise WorktreeError("managed worktree path already exists")
    try:
        destination.resolve(strict=False).relative_to(root.resolve())
    except ValueError as error:
        raise WorktreeError("managed worktree path escapes its repository") from error
    return destination


def tracked_entries(repo_root: Path) -> dict[str, tuple[str, str]]:
    raw = run_git(["ls-files", "-s", "-z"], cwd=repo_root).stdout
    entries: dict[str, tuple[str, str]] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        metadata, separator, raw_path = item.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise WorktreeError("Git index entry is malformed")
        mode, object_id, stage = (field.decode("ascii", "strict") for field in fields)
        if stage != "0":
            raise WorktreeError("unmerged index entries must be resolved before isolation")
        try:
            relative = raw_path.decode("utf-8")
        except UnicodeError as error:
            raise WorktreeError("repository path is not valid UTF-8") from error
        normalized = normalize_relative_path(relative)
        if normalized is None or normalized != relative.replace("\\", "/"):
            raise WorktreeError("tracked repository path is unsafe")
        entries[normalized] = (mode, object_id)
    return entries


def safe_symlink_payload(
    repo_root: Path, relative: str, path: Path
) -> bytes:
    try:
        if path.is_symlink():
            target = os.readlink(path)
            payload = os.fsencode(target)
        elif path.is_file():
            payload = path.read_bytes()
            target = os.fsdecode(payload)
        else:
            raise WorktreeError(f"tracked link is missing or unsafe: {relative}")
    except (OSError, UnicodeError) as error:
        raise WorktreeError(f"tracked link could not be inspected safely: {relative}") from error
    target_path = Path(target)
    if target_path.is_absolute():
        raise WorktreeError(f"tracked link has an absolute target: {relative}")
    resolved_target = (path.parent / target_path).resolve(strict=False)
    try:
        target_relative = str(resolved_target.relative_to(repo_root)).replace("\\", "/")
    except ValueError as error:
        raise WorktreeError(f"tracked link escapes the repository: {relative}") from error
    if (
        is_sensitive_env_path(target_relative)
        or is_sensitive_credential_path(target_relative)
    ):
        raise WorktreeError("tracked link points to a filtered sensitive file")
    return payload


def write_staging_file(staging: Path, relative: str, payload: bytes, mode: int) -> None:
    target = staging.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    os.chmod(target, mode)


def prepare_snapshot_payloads(
    repo_root: Path,
    staging: Path,
) -> tuple[dict[str, bytes], dict[str, str], list[str], int, int]:
    tracked = tracked_entries(repo_root)
    special_links: dict[str, bytes] = {}
    submodules: dict[str, str] = {}
    executable: list[str] = []
    tracked_bytes = 0
    filtered_credentials = 0
    projected_env = 0

    for relative, (git_mode, object_id) in tracked.items():
        path = repo_root.joinpath(*relative.split("/"))
        if is_sensitive_credential_path(relative):
            filtered_credentials += 1
            continue
        if git_mode == "160000":
            if path.exists() and not path.is_dir():
                raise WorktreeError(f"tracked submodule path is unsafe: {relative}")
            submodules[relative] = object_id
            continue
        if not os.path.lexists(path):
            continue
        if git_mode == "120000":
            payload = safe_symlink_payload(repo_root, relative, path)
            if contains_high_confidence_credential(relative, payload):
                filtered_credentials += 1
                continue
            special_links[relative] = payload
            continue
        if git_mode not in {"100644", "100755"}:
            raise WorktreeError(f"unsupported tracked file mode: {relative}")
        try:
            payload, local_mode = stable_regular_payload(
                repo_root,
                relative,
                maximum_bytes=MAX_TRACKED_FILE_BYTES,
            )
        except FileSafetyError as error:
            raise WorktreeError(str(error)) from error
        tracked_bytes += len(payload)
        if tracked_bytes > MAX_TRACKED_TOTAL_BYTES:
            raise WorktreeError("tracked repository content exceeds the safety limit")
        if is_sensitive_env_path(relative):
            try:
                payload = env_projection(relative, payload)
            except FileSafetyError as error:
                raise WorktreeError(str(error)) from error
            projected_env += 1
        elif contains_high_confidence_credential(relative, payload):
            filtered_credentials += 1
            continue
        write_staging_file(staging, relative, payload, local_mode)
        if git_mode == "100755":
            executable.append(relative)

    raw_untracked = run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
    ).stdout
    untracked_bytes = 0
    untracked_files = 0
    for raw_relative in sorted(item for item in raw_untracked.split(b"\0") if item):
        try:
            relative = raw_relative.decode("utf-8")
        except UnicodeError as error:
            raise WorktreeError("untracked repository path is not valid UTF-8") from error
        normalized = normalize_relative_path(relative)
        if normalized is None or normalized != relative.replace("\\", "/"):
            raise WorktreeError("untracked repository path is unsafe")
        if normalized.startswith(".claude/worktrees/"):
            continue
        untracked_files += 1
        if untracked_files > MAX_UNTRACKED_FILES:
            raise WorktreeError("eligible untracked files exceed the safety limit")
        if is_sensitive_credential_path(normalized):
            filtered_credentials += 1
            continue
        try:
            payload, local_mode = stable_regular_payload(
                repo_root,
                normalized,
                maximum_bytes=MAX_UNTRACKED_FILE_BYTES,
            )
        except FileSafetyError as error:
            raise WorktreeError(str(error)) from error
        untracked_bytes += len(payload)
        if untracked_bytes > MAX_UNTRACKED_TOTAL_BYTES:
            raise WorktreeError("eligible untracked content exceeds the safety limit")
        if is_sensitive_env_path(normalized):
            try:
                payload = env_projection(normalized, payload)
            except FileSafetyError as error:
                raise WorktreeError(str(error)) from error
            projected_env += 1
        elif contains_high_confidence_credential(normalized, payload):
            filtered_credentials += 1
            continue
        write_staging_file(staging, normalized, payload, local_mode)
        if local_mode == 0o755:
            executable.append(normalized)

    return special_links, submodules, executable, filtered_credentials, projected_env


def synthetic_snapshot(
    repo_root: Path,
    staging: Path,
    index_path: Path,
    special_links: dict[str, bytes],
    submodules: dict[str, str],
    executable: list[str],
) -> str:
    environment = git_environment({
        "GIT_INDEX_FILE": str(index_path),
        "GIT_WORK_TREE": str(staging),
        "GIT_AUTHOR_NAME": "Airlock Snapshot",
        "GIT_AUTHOR_EMAIL": "noreply@localhost",
        "GIT_COMMITTER_NAME": "Airlock Snapshot",
        "GIT_COMMITTER_EMAIL": "noreply@localhost",
    })
    run_git(["read-tree", "--empty"], cwd=repo_root, environment=environment)
    if any(staging.iterdir()):
        run_git(
            ["add", "--force", "--all", "--", "."],
            cwd=repo_root,
            environment=environment,
        )
    for relative in executable:
        run_git(
            ["update-index", "--chmod=+x", "--", relative],
            cwd=repo_root,
            environment=environment,
        )
    for relative, payload in special_links.items():
        object_id = output_text(run_git(
            ["hash-object", "-w", "--stdin"],
            cwd=repo_root,
            environment=environment,
            input_bytes=payload,
        ))
        run_git(
            ["update-index", "--add", "--cacheinfo", f"120000,{object_id},{relative}"],
            cwd=repo_root,
            environment=environment,
        )
    for relative, object_id in submodules.items():
        run_git(
            ["update-index", "--add", "--cacheinfo", f"160000,{object_id},{relative}"],
            cwd=repo_root,
            environment=environment,
        )
    tree = output_text(run_git(["write-tree"], cwd=repo_root, environment=environment))
    snapshot = output_text(run_git(
        ["commit-tree", tree, "-m", SNAPSHOT_SUBJECT],
        cwd=repo_root,
        environment=environment,
    ))
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", snapshot):
        raise WorktreeError("synthetic snapshot was not created")
    return snapshot


def create_worktree(event: dict[str, object]) -> None:
    if event.get("hook_event_name") != "WorktreeCreate":
        raise WorktreeError("unexpected hook event")
    cwd = event_path(event, "cwd")
    repo_root = repository_root(cwd)
    name = normalized_name(event.get("name"))
    destination = prepare_destination(repo_root, name)
    session = str(event.get("session_id", ""))

    with tempfile.TemporaryDirectory(prefix="airlock-native-worktree-") as raw_temp:
        temporary = Path(raw_temp)
        staging = temporary / "staging"
        staging.mkdir(mode=0o700)
        index_path = temporary / "snapshot.index"
        special_links, submodules, executable, filtered, projected = (
            prepare_snapshot_payloads(repo_root, staging)
        )
        snapshot = synthetic_snapshot(
            repo_root,
            staging,
            index_path,
            special_links,
            submodules,
            executable,
        )

    identity = hashlib.sha256(
        f"{repo_root}\0{name}\0{session}\0{snapshot}".encode("utf-8")
    ).hexdigest()[:12]
    branch_stem = name.replace("/", "-")[:32]
    branch = f"{BRANCH_PREFIX}{branch_stem}-{identity}"
    try:
        run_git(
            ["worktree", "add", "-b", branch, str(destination), snapshot],
            cwd=repo_root,
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        run_git(["worktree", "prune"], cwd=repo_root, check=False)
        run_git(["branch", "-D", branch], cwd=repo_root, check=False)
        raise
    if filtered:
        print(
            f"Airlock filtered {filtered} credential-bearing repository file(s) from the isolated worktree.",
            file=sys.stderr,
        )
    if projected:
        print(
            f"Airlock replaced {projected} sensitive env file(s) with key-only projections in the isolated worktree.",
            file=sys.stderr,
        )
    print(str(destination.resolve()), flush=True)


def worktree_registry(cwd: Path) -> list[Path]:
    raw = output_text(run_git(["worktree", "list", "--porcelain"], cwd=cwd))
    result: list[Path] = []
    for line in raw.splitlines():
        if line.startswith("worktree "):
            candidate = Path(line[len("worktree "):])
            if candidate.is_absolute():
                result.append(candidate.resolve())
    return result


def remove_worktree(event: dict[str, object]) -> None:
    if event.get("hook_event_name") != "WorktreeRemove":
        raise WorktreeError("unexpected hook event")
    cwd = event_path(event, "cwd")
    worktree = event_path(event, "worktree_path").resolve()
    anchor = worktree if worktree.is_dir() else cwd
    registered = worktree_registry(anchor)
    if not registered:
        return
    main = registered[0]
    expected_root = (main / ".claude" / "worktrees").resolve()
    try:
        worktree.relative_to(expected_root)
    except ValueError as error:
        raise WorktreeError("refusing to remove a worktree outside the managed root") from error
    if worktree not in registered:
        return
    branch_result = run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=worktree,
        check=False,
    )
    branch = output_text(branch_result) if branch_result.returncode == 0 else ""
    if not branch.startswith(BRANCH_PREFIX):
        raise WorktreeError("refusing to remove a worktree on an unmanaged branch")
    status = run_git(["status", "--porcelain=v1", "-z"], cwd=worktree).stdout
    if status:
        raise WorktreeError("worktree contains changes and was preserved")
    count = output_text(run_git(["rev-list", "--count", "HEAD"], cwd=worktree))
    subject = output_text(run_git(["log", "-1", "--format=%s"], cwd=worktree))
    if count != "1" or subject != SNAPSHOT_SUBJECT:
        raise WorktreeError("worktree contains commits and was preserved")
    run_git(["worktree", "remove", str(worktree)], cwd=main)
    run_git(["branch", "-D", branch], cwd=main)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"create", "remove"}:
        fail("usage: worktree.py create|remove")
    try:
        event = read_event()
        if sys.argv[1] == "create":
            create_worktree(event)
        else:
            remove_worktree(event)
    except WorktreeError as error:
        fail(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
