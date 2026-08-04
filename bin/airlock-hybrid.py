#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Launch a native Airlock Claude Code session using an exact argument vector."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import NoReturn


MAX_REQUEST_BYTES = 1024 * 1024
MAX_AGENTS_BYTES = 24 * 1024
ROUTER_START_TIMEOUT_SECONDS = 15
PROFILES = {
    "openai-pure",
    "hybrid-openai-root",
    "hybrid-anthropic-root",
}
PROTECTED_OPTIONS = {
    "--safe-mode", "--bare", "--agent", "--agents",
    "--plugin-dir", "--plugin-url", "--settings",
}
PROXY_VARIABLES = {
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK",
    "CLAUDE_CODE_SUBAGENT_MODEL",
}


def fail(message: str, exit_code: int = 1) -> NoReturn:
    print(f"airlock: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def local_app_data() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local"


def load_access_module():
    helper_path = Path(__file__).with_name("airlock-access.py")
    if not helper_path.is_file():
        fail("managed access-policy helper is missing")
    spec = importlib.util.spec_from_file_location("airlock_access", helper_path)
    if spec is None or spec.loader is None:
        fail("managed access-policy helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        fail("managed access-policy helper is invalid")
    return module


def resolve_managed_request(raw_path: str) -> Path:
    request_root = (local_app_data() / "Airlock" / "launch").resolve()
    request_path = Path(raw_path).resolve()
    try:
        request_path.relative_to(request_root)
    except ValueError:
        fail("launch request is outside the managed directory")
    if not request_path.is_file() or request_path.is_symlink():
        fail("launch request is missing or unsafe")
    if request_path.stat().st_size > MAX_REQUEST_BYTES:
        fail("launch request is too large")
    return request_path


def read_request(request_path: Path) -> dict[str, object]:
    try:
        raw_request = request_path.read_text(encoding="utf-8-sig")
    finally:
        request_path.unlink(missing_ok=True)

    try:
        request = json.loads(raw_request)
    except json.JSONDecodeError:
        fail("launch request is invalid JSON")
    if not isinstance(request, dict):
        fail("launch request must be a JSON object")
    return request


def validate_plugin_path(raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        fail("managed plugin path is invalid")
    plugin = Path(raw_path)
    manifest = plugin / ".claude-plugin" / "plugin.json"
    hooks = plugin / "hooks" / "hooks.json"
    guard = plugin / "scripts" / "agent-guard.py"
    if plugin.is_symlink() or not plugin.is_dir():
        fail("managed session plugin is missing or unsafe")
    for path in (manifest, hooks, guard):
        if path.is_symlink() or not path.is_file():
            fail(f"managed session plugin file is missing or unsafe: {path.name}")
    return plugin.resolve()


def render_agents(
    raw_catalog_files: object,
    profile: str,
    access,
    policy: dict[str, object],
) -> str:
    if not isinstance(raw_catalog_files, dict):
        fail("agent catalog map is invalid")
    rendered = access.render_profile_from_paths(
        policy,
        profile,
        raw_catalog_files,
    )
    if not rendered:
        fail("access policy disables every worker in this session profile")
    agents_json = json.dumps(rendered, separators=(",", ":"), ensure_ascii=True)
    if len(agents_json.encode("utf-8")) > MAX_AGENTS_BYTES:
        fail("policy-rendered agent configuration exceeds the safe Windows command-line limit")
    return agents_json


def managed_agent_permissions(access, agents_json: str) -> tuple[list[str], list[str]]:
    try:
        names = access.managed_agent_names_json(agents_json)
    except Exception as error:
        fail(f"managed Agent permission set is invalid: {error}")
    if not names:
        fail("managed Agent permission set is empty")
    return names, [
        "Agent(Explore)", "Agent(Plan)", "Agent(general-purpose)",
        *(f"Agent({name})" for name in names),
    ]


def managed_session_settings(access, agents_json: str) -> str:
    try:
        settings = access.managed_session_settings_json(agents_json)
        parsed = json.loads(settings)
    except Exception as error:
        fail(f"managed Auto-mode settings are invalid: {error}")
    if not isinstance(parsed, dict) or set(parsed) != {"autoMode"}:
        fail("managed Auto-mode settings have an unexpected shape")
    return settings


def validate_child_args(raw_args: object) -> list[str]:
    if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
        fail("Claude Code arguments are invalid")
    for argument in raw_args:
        option = argument.split("=", 1)[0]
        if option in PROTECTED_OPTIONS:
            fail(f"{option} is managed or incompatible with the Airlock worker guard", 2)
    return raw_args


def append_routing_guidance(child_args: list[str], guidance: str) -> list[str]:
    result: list[str] = []
    custom: list[str] = []
    index = 0
    while index < len(child_args):
        argument = child_args[index]
        if argument == "--append-system-prompt":
            if index + 1 >= len(child_args):
                fail("--append-system-prompt requires a value", 2)
            custom.append(child_args[index + 1])
            index += 2
            continue
        if argument.startswith("--append-system-prompt="):
            custom.append(argument.split("=", 1)[1])
            index += 1
            continue
        result.append(argument)
        index += 1
    combined = "\n\n".join([item for item in custom if item] + [guidance])
    result.extend(["--append-system-prompt", combined])
    return result


def require_proxy_environment(
    environment: dict[str, str], proxy_url: str, root_model: str
) -> None:
    if environment.get("ANTHROPIC_BASE_URL") != proxy_url:
        fail(f"OpenAI-backed mode requires {proxy_url}")
    if environment.get("ANTHROPIC_MODEL") != root_model or not root_model.startswith("gpt-"):
        fail("OpenAI-backed mode requires the selected GPT parent model")


def validate_router_path(raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        fail("managed router helper path is invalid")
    expected = Path(__file__).with_name("airlock-router.py").resolve()
    router = Path(raw_path)
    if router.is_symlink() or not router.is_file() or router.resolve() != expected:
        fail("managed router helper is missing or unsafe")
    return router.resolve()


def start_native_router(
    router: Path, routes: dict[str, str], proxy_url: str
) -> str:
    command = [
        sys.executable,
        str(router),
        "start",
        "--parent-pid",
        str(os.getpid()),
        "--routes-json",
        json.dumps(routes, separators=(",", ":"), ensure_ascii=True),
        "--openai-url",
        proxy_url,
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=ROUTER_START_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail("native hybrid router could not start")
    address = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(
        r"http://127\.0\.0\.1:[1-9][0-9]*", address
    ):
        fail("native hybrid router could not start securely")
    return address


def build_child_environment(
    profile: str,
    max_agents: str,
    *,
    proxy_url: str,
    root_model: str,
    root_name: str,
    context_window: str,
    route_policy: dict[str, object],
    router_url: str | None,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["AIRLOCK_ACTIVE_PROFILE"] = profile
    environment["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = "1"
    environment.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
    if max_agents == "off":
        environment.pop("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", None)
    else:
        environment["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = max_agents

    model_ids = route_policy.get("model_ids")
    agent_names = route_policy.get("agent_names")
    extra_models = route_policy.get("extra_model_ids")
    extra_agents = route_policy.get("extra_agent_names")
    if (
        not all(
            isinstance(values, list)
            and all(isinstance(value, str) and value for value in values)
            for values in (model_ids, agent_names, extra_models, extra_agents)
        )
        or not model_ids
        or not agent_names
    ):
        fail("native session allowlist is invalid")
    environment["AIRLOCK_ALLOWED_AGENT_NAMES"] = ",".join(agent_names)
    environment["AIRLOCK_ALLOWED_AGENT_MODELS"] = ",".join(model_ids)
    environment["AIRLOCK_EXTRA_USAGE_AGENT_NAMES"] = ",".join(extra_agents)
    environment["AIRLOCK_EXTRA_USAGE_AGENT_MODELS"] = ",".join(extra_models)

    if profile == "openai-pure":
        if router_url is not None:
            fail("OpenAI-only mode cannot use the hybrid router")
        require_proxy_environment(environment, proxy_url, root_model)
        environment["ANTHROPIC_DEFAULT_OPUS_MODEL"] = root_model
        environment["ANTHROPIC_DEFAULT_SONNET_MODEL"] = root_model
        environment.pop("AIRLOCK_HYBRID", None)
        environment.pop("AIRLOCK_GPT_HYBRID", None)
        return environment

    if environment.get("ANTHROPIC_API_KEY") or environment.get("ANTHROPIC_AUTH_TOKEN") not in {None, "", "unused"}:
        fail(
            "hybrid native routing requires saved Claude subscription login without "
            "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
        )
    if router_url is None:
        fail("hybrid native routing is missing its loopback router")
    for variable in PROXY_VARIABLES:
        environment.pop(variable, None)
    environment["ANTHROPIC_BASE_URL"] = router_url
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION"] = root_model
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] = (
        f"{root_name} (native hybrid route)"
    )
    environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = context_window
    environment.setdefault("CLAUDE_CODE_ALWAYS_ENABLE_EFFORT", "1")
    environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    environment["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] = "1"
    if profile == "hybrid-anthropic-root":
        environment.pop("AIRLOCK_GPT_HYBRID", None)
        environment["AIRLOCK_HYBRID"] = "1"
    else:
        environment.pop("AIRLOCK_HYBRID", None)
        environment["AIRLOCK_GPT_HYBRID"] = "1"
    return environment


def validate_max_agents(raw: object) -> str:
    if raw == "off":
        return "off"
    if (
        not isinstance(raw, str) or not raw.isdigit()
        or (len(raw) > 1 and raw.startswith("0")) or not 1 <= int(raw) <= 20
    ):
        fail("max_agents must be off or an integer from 1 to 20", 2)
    return raw


def validate_launch_marker(profile: str) -> None:
    if profile == "hybrid-anthropic-root":
        expected = "AIRLOCK_HYBRID"
    elif profile == "hybrid-openai-root":
        expected = "AIRLOCK_GPT_HYBRID"
    else:
        return
    if os.environ.get(expected) != "1":
        fail("hybrid launch marker is missing")


def required_request_string(
    request: dict[str, object], key: str, label: str
) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        fail(f"{label} is invalid")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--request-file", required=True)
    parsed = parser.parse_args()

    request = read_request(resolve_managed_request(parsed.request_file))
    profile = request.get("profile")
    if profile not in PROFILES:
        fail("session profile is invalid")
    validate_launch_marker(profile)
    if os.environ.get("CLAUDE_CODE_SAFE_MODE") == "1" or os.environ.get("CLAUDE_CODE_SIMPLE") == "1":
        fail("CLAUDE_CODE_SAFE_MODE/CLAUDE_CODE_SIMPLE would disable managed session protection", 2)
    plugin_dir = validate_plugin_path(request.get("plugin_dir"))
    max_agents = validate_max_agents(request.get("max_agents"))
    proxy_url = required_request_string(request, "proxy_url", "OpenAI proxy URL")
    root_model = required_request_string(request, "root_model", "root model")
    root_name = required_request_string(request, "root_name", "root model name")
    context_window = required_request_string(
        request, "context_window", "context window"
    )
    if not context_window.isdigit() or int(context_window) <= 0:
        fail("context window is invalid")

    raw_claude = request.get("claude")
    if not isinstance(raw_claude, str) or not raw_claude:
        fail("Claude Code executable is invalid")
    claude_path = Path(raw_claude)
    if not claude_path.is_file():
        fail(f"Claude Code executable is missing: {claude_path}")

    access = load_access_module()
    try:
        policy = access.load_or_refresh_policy()
        agents_json = render_agents(
            request.get("catalog_files"), profile, access, policy
        )
        agent_names, allowed_tools = managed_agent_permissions(access, agents_json)
        route_policy = access.session_route_policy(policy, profile)
        if agent_names != route_policy.get("agent_names"):
            fail("rendered native Agents do not match the session route policy")
        session_settings = managed_session_settings(access, agents_json)
        guidance = access.profile_guidance(policy, profile)
    except SystemExit:
        raise
    except Exception as error:
        fail(f"managed access policy could not be applied: {error}")

    router_url: str | None = None
    if profile != "openai-pure":
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            fail(
                "hybrid native routing requires saved Claude subscription login without "
                "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
            )
        routes = route_policy.get("routes")
        if not isinstance(routes, dict) or routes.get(root_model) not in {
            "openai", "anthropic"
        }:
            fail(f"hybrid root model is not enabled by the active session policy: {root_model}", 2)
        expected_provider = (
            "openai" if profile == "hybrid-openai-root" else "anthropic"
        )
        if routes[root_model] != expected_provider:
            fail("hybrid root model does not match the selected root provider", 2)
        router = validate_router_path(request.get("router_helper"))
        router_url = start_native_router(router, routes, proxy_url)

    child_args = append_routing_guidance(
        validate_child_args(request.get("args")), guidance
    )
    child_environment = build_child_environment(
        profile,
        max_agents,
        proxy_url=proxy_url,
        root_model=root_model,
        root_name=root_name,
        context_window=context_window,
        route_policy=route_policy,
        router_url=router_url,
    )

    command = [
        str(claude_path), "--settings", session_settings,
        "--plugin-dir", str(plugin_dir), "--agents", agents_json,
        "--allowedTools", *allowed_tools, *child_args,
    ]
    try:
        completed = subprocess.run(command, env=child_environment, check=False)
    except OSError as error:
        fail(f"could not start Claude Code: {error}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
