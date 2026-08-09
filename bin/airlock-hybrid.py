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
    "grok-pure",
    "hybrid-openai-root",
    "hybrid-anthropic-root",
    "hybrid-grok-root",
}
PROXY_PURE_PROFILES = {"openai-pure", "grok-pure"}
PROXY_PURE_ROOT_PREFIXES = {"openai-pure": "gpt-", "grok-pure": "grok-"}
HYBRID_PROFILES = {
    "hybrid-openai-root",
    "hybrid-anthropic-root",
    "hybrid-grok-root",
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
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK",
    "CLAUDE_CODE_SUBAGENT_MODEL",
}


DEFAULT_GPT_EFFORT_CAPABILITIES = "effort,xhigh_effort,max_effort"


def fail(message: str, exit_code: int = 1) -> NoReturn:
    print(f"airlock: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def declare_non_claude_effort_capabilities(
    environment: dict[str, str], variable: str, model: str
) -> None:
    """Tell Claude Code which effort levels a pinned non-Claude model supports.

    Claude Code decides whether a model supports effort by matching the model ID
    against known Anthropic patterns. A pinned GPT or Grok ID matches nothing,
    which would leave /effort unavailable. Only declare for non-Claude IDs: a
    declaration disables every capability left off the list, and built-in
    detection already gets real Claude IDs right.
    """
    if not model or model.startswith("claude-"):
        return
    capabilities = (
        environment.get("AIRLOCK_NON_CLAUDE_EFFORT_CAPABILITIES")
        or environment.get("AIRLOCK_GPT_EFFORT_CAPABILITIES")
        or DEFAULT_GPT_EFFORT_CAPABILITIES
    )
    environment[f"{variable}_SUPPORTED_CAPABILITIES"] = capabilities


# Backward-compatible alias used by older call sites and tests.
declare_gpt_effort_capabilities = declare_non_claude_effort_capabilities


def configure_proxy_model_picker(
    environment: dict[str, str], profile: str, picker_models: object
) -> None:
    """Bind Claude Code's four family slots to exact models in this session.

    The Agent tool accepts only family aliases, not arbitrary exact model IDs.
    Airlock owns every slot so built-in Agents stay inside the active policy and
    inherited shell metadata cannot misroute or mislabel them.
    """
    families = ("fable", "opus", "sonnet", "haiku")
    if not isinstance(picker_models, dict) or set(picker_models) != set(families):
        fail(f"{profile} model picker map is invalid")
    for family in families:
        model = picker_models.get(family)
        if not isinstance(model, str) or not model:
            fail(f"{profile} model picker map is invalid")
        variable = f"ANTHROPIC_DEFAULT_{family.upper()}_MODEL"
        environment[variable] = model
        environment[f"{variable}_NAME"] = model
        environment[f"{variable}_DESCRIPTION"] = (
            f"Airlock exact route for Claude Code's {family.title()} slot"
        )
        environment.pop(f"{variable}_SUPPORTED_CAPABILITIES", None)
        declare_non_claude_effort_capabilities(environment, variable, model)
    if (
        profile == "grok-pure"
        or profile in HYBRID_PROFILES
        or not environment.get("ANTHROPIC_SMALL_FAST_MODEL")
    ):
        environment["ANTHROPIC_SMALL_FAST_MODEL"] = picker_models["haiku"]


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


def managed_session_settings(
    access, agents_json: str, fast_mode: str
) -> str:
    try:
        settings = access.managed_session_settings_json(agents_json, fast_mode)
        parsed = json.loads(settings)
    except Exception as error:
        fail(f"managed Auto-mode settings are invalid: {error}")
    allowed_keys = {"autoMode"} if fast_mode == "inherit" else {"autoMode", "fastMode"}
    if not isinstance(parsed, dict) or set(parsed) != allowed_keys:
        fail("managed session settings have an unexpected shape")
    if fast_mode != "inherit" and parsed.get("fastMode") is not (fast_mode == "on"):
        fail("managed session Fast setting is invalid")
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
    environment: dict[str, str], proxy_url: str, root_model: str, profile: str
) -> None:
    # Both proxy-backed profiles share the loopback endpoint, so the root model
    # prefix is what keeps a Codex session from starting on a Grok ID and the
    # other way round.
    prefix = PROXY_PURE_ROOT_PREFIXES.get(profile)
    if prefix is None:
        fail(f"unknown proxy-backed profile: {profile}")
    if environment.get("ANTHROPIC_BASE_URL") != proxy_url:
        fail(f"{profile} requires {proxy_url}")
    if (
        environment.get("ANTHROPIC_MODEL") != root_model
        or not root_model.startswith(prefix)
    ):
        fail(f"{profile} requires a selected {prefix}* parent model")


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
    force_context_window: bool = False,
) -> dict[str, str]:
    environment = os.environ.copy()
    # A window the user set themselves outranks Airlock's default. Read it before
    # the proxy cleanup below removes it.
    user_context_window = environment.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "")
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
    environment["AIRLOCK_ROOT_MODEL"] = root_model
    discovery_model = route_policy.get("discovery_model")
    if discovery_model is not None:
        if not isinstance(discovery_model, str) or discovery_model not in model_ids:
            fail("native session discovery model is invalid")
        if discovery_model in extra_models:
            fail("native session discovery model requires confirmation")
        environment["AIRLOCK_DISCOVERY_MODEL"] = discovery_model
    else:
        environment.pop("AIRLOCK_DISCOVERY_MODEL", None)

    if profile in PROXY_PURE_PROFILES:
        if router_url is not None:
            fail(f"{profile} cannot use the hybrid router")
        environment.pop("AIRLOCK_SESSION_ROUTER_URL", None)
        require_proxy_environment(environment, proxy_url, root_model, profile)
        configure_proxy_model_picker(
            environment, profile, route_policy.get("picker_models")
        )
        environment.pop("AIRLOCK_HYBRID", None)
        environment.pop("AIRLOCK_GPT_HYBRID", None)
        environment.pop("AIRLOCK_GROK_HYBRID", None)
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
    environment["AIRLOCK_SESSION_ROUTER_URL"] = router_url
    configure_proxy_model_picker(
        environment, profile, route_policy.get("picker_models")
    )
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION"] = root_model
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] = (
        f"{root_name} (native hybrid route)"
    )
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION"] = (
        f"Selected Airlock hybrid root ({root_model})"
    )
    declare_non_claude_effort_capabilities(
        environment, "ANTHROPIC_CUSTOM_MODEL_OPTION", root_model
    )
    # Claude Code reads CLAUDE_CODE_AUTO_COMPACT_WINDOW ahead of its own per-model
    # tuning. Native Anthropic roots already carry real model-aware sizing. An
    # explicit Airlock window wins; otherwise OpenAI and Grok roots keep the
    # conservative saved fallback for the whole process.
    if user_context_window:
        environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = user_context_window
    elif force_context_window and context_window != "auto":
        environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = context_window
    elif (
        context_window != "auto"
        and profile != "hybrid-anthropic-root"
    ):
        environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = context_window
    environment.setdefault("CLAUDE_CODE_ALWAYS_ENABLE_EFFORT", "1")
    environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    environment["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] = "1"
    environment.pop("AIRLOCK_HYBRID", None)
    environment.pop("AIRLOCK_GPT_HYBRID", None)
    environment.pop("AIRLOCK_GROK_HYBRID", None)
    if profile == "hybrid-anthropic-root":
        environment["AIRLOCK_HYBRID"] = "1"
    elif profile == "hybrid-openai-root":
        environment["AIRLOCK_GPT_HYBRID"] = "1"
    elif profile == "hybrid-grok-root":
        environment["AIRLOCK_GROK_HYBRID"] = "1"
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


def context_window_is_valid(value: str) -> bool:
    """Report whether Claude Code would actually honour this window.

    Claude Code accepts 100000 to 1000000 and silently ignores anything else, so
    a value outside that range would look applied while doing nothing. The shape
    rule has to match both launchers exactly: str.isdigit is true for characters
    int() then refuses, such as a superscript two, and it also accepts leading
    zeros and a leading plus that the POSIX launcher rejects.
    """
    if value == "auto":
        return True
    if not re.fullmatch(r"[1-9][0-9]{5,6}", value):
        return False
    return 100000 <= int(value) <= 1000000


def validate_launch_marker(profile: str) -> None:
    if profile == "hybrid-anthropic-root":
        expected = "AIRLOCK_HYBRID"
    elif profile == "hybrid-openai-root":
        expected = "AIRLOCK_GPT_HYBRID"
    elif profile == "hybrid-grok-root":
        expected = "AIRLOCK_GROK_HYBRID"
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
    force_context_window = request.get("force_context_window", False)
    if not isinstance(force_context_window, bool):
        fail("context window override marker is invalid")
    fast_mode = required_request_string(request, "fast_mode", "Fast mode")
    if fast_mode not in {"inherit", "on", "off"}:
        fail("session Fast mode is invalid")
    if not context_window_is_valid(context_window):
        fail("context window must be 'auto' or a whole number from 100000 to 1000000")

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
        session_settings = managed_session_settings(access, agents_json, fast_mode)
        guidance = access.profile_guidance(policy, profile)
    except SystemExit:
        raise
    except Exception as error:
        fail(f"managed access policy could not be applied: {error}")

    router_url: str | None = None
    if profile not in PROXY_PURE_PROFILES:
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            fail(
                "hybrid native routing requires saved Claude subscription login without "
                "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
            )
        routes = route_policy.get("routes")
        if not isinstance(routes, dict) or routes.get(root_model) not in {
            "openai", "anthropic", "grok"
        }:
            fail(f"hybrid root model is not enabled by the active session policy: {root_model}", 2)
        expected_provider = {
            "hybrid-openai-root": "openai",
            "hybrid-anthropic-root": "anthropic",
            "hybrid-grok-root": "grok",
        }.get(profile)
        if expected_provider is None or routes[root_model] != expected_provider:
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
        force_context_window=force_context_window,
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
