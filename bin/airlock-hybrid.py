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
WINDOWS_COMMAND_LINE_MAX_UNITS = 32_767
ROUTER_START_TIMEOUT_SECONDS = 15
# Claude Code reads the router address once, at startup, so a router that
# dies mid-session takes the whole session with it and no later request can
# succeed. The launcher outlives the router by design, so it runs the router
# helper's own watch alongside the session.
FAST_TRANSITION_CHANNEL_ENV = "AIRLOCK_FAST_TRANSITION_CHANNEL"
FAST_TRANSITION_NONCE_ENV = "AIRLOCK_FAST_TRANSITION_NONCE"
FAST_TRANSITION_MODEL = "gpt-5.6-sol-fast"
FAST_TRANSITION_ROUTE = "sol-fast"
FAST_TRANSITION_PROFILE = "openai-pure"
FAST_TRANSITION_NAME = "GPT-5.6 Sol Fast"
PROFILES = {
    "openrouter-pure",
    "openai-pure",
    "grok-pure",
    "hybrid-openai-root",
    "hybrid-anthropic-root",
    "hybrid-grok-root",
    "hybrid-openrouter-root",
}
PROXY_PURE_PROFILES = {"openai-pure", "grok-pure"}
PROXY_PURE_ROOT_PREFIXES = {"openai-pure": "gpt-", "grok-pure": "grok-"}
HYBRID_PROFILES = {
    "hybrid-openai-root",
    "hybrid-anthropic-root",
    "hybrid-grok-root",
    "hybrid-openrouter-root",
}
ROUTER_BACKED_PROFILES = HYBRID_PROFILES | {"openrouter-pure"}
ORP_CREDENTIAL_VARIABLES = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "OPENROUTER_API_KEY",
}
PROTECTED_OPTIONS = {
    "--safe-mode", "--bare", "--agent", "--agents",
    "--plugin-dir", "--plugin-url", "--settings",
    "--append-system-prompt-file",
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
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
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


def apply_auto_mode_model(environment: dict[str, str], root_model: str) -> None:
    """Point auto mode's permission classifier at a model this route serves.

    Claude Code hard-codes Claude Sonnet 5 for the classifier and ignores the
    family slots, so on any root that does not serve that exact ID every
    classification fails closed. Classifications are frequent background work,
    so default to the cheapest served model rather than the premium root: the
    small fast seat first, then the discovery model, then the root itself only
    when nothing smaller is routed. AIRLOCK_AUTO_MODE_MODEL passes through
    verbatim, and off or none keeps Claude Code's native behavior.
    """
    configured = (environment.get("AIRLOCK_AUTO_MODE_MODEL") or "").strip()
    environment.pop("CLAUDE_CODE_AUTO_MODE_MODEL", None)
    if configured.lower() in {"off", "none"}:
        return
    if configured:
        environment["CLAUDE_CODE_AUTO_MODE_MODEL"] = configured
        return
    if not root_model or root_model.startswith("claude-"):
        return
    small_model = (
        environment.get("ANTHROPIC_SMALL_FAST_MODEL")
        or environment.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
        or environment.get("AIRLOCK_DISCOVERY_MODEL")
        or root_model
    )
    environment["CLAUDE_CODE_AUTO_MODE_MODEL"] = small_model


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
        profile in {"grok-pure", "openrouter-pure"}
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
    *,
    openrouter_root_route: str | None = None,
) -> str:
    if not isinstance(raw_catalog_files, dict):
        fail("agent catalog map is invalid")
    rendered = access.render_profile_from_paths(
        policy,
        profile,
        raw_catalog_files,
        openrouter_root_route=openrouter_root_route,
    )
    if not rendered:
        fail("access policy disables every worker in this session profile")
    agents_json = json.dumps(rendered, separators=(",", ":"), ensure_ascii=True)
    if len(agents_json.encode("utf-8")) > MAX_AGENTS_BYTES:
        fail("policy-rendered agent configuration exceeds the safe Windows command-line limit")
    return agents_json


def managed_agent_permissions(
    access, agents_json: str, policy: dict[str, object]
) -> tuple[list[str], list[str]]:
    try:
        names = access.managed_agent_names_json(agents_json, policy=policy)
    except Exception as error:
        fail(f"managed Agent permission set is invalid: {error}")
    if not names:
        fail("managed Agent permission set is empty")
    return names, [
        "Agent(Explore)", "Agent(Plan)", "Agent(general-purpose)",
        *(f"Agent({name})" for name in names),
    ]


def managed_session_settings(
    access,
    agents_json: str,
    fast_mode: str,
    policy: dict[str, object],
    web_tools_script: str | None = None,
    profile: str = "",
) -> str:
    try:
        settings = access.managed_session_settings_json(
            agents_json, fast_mode, policy=policy,
            web_tools_script=web_tools_script,
            profile=profile or None,
        )
        parsed = json.loads(settings)
    except Exception as error:
        fail(f"managed Auto-mode settings are invalid: {error}")
    if profile and profile not in access.PROFILE_ROOT_PROVIDERS:
        fail("managed session settings received an unknown profile")
    allowed_keys = {"autoMode"}
    if fast_mode != "inherit":
        allowed_keys.add("fastMode")
    web_tools_entry = access.web_tools_server_entry(web_tools_script)
    web_tools_join = (
        web_tools_entry is not None
        and (not profile or access.web_tools_active_for(profile))
    )
    expected_denials = access.built_in_web_tool_denials(profile) if (
        profile and web_tools_entry is not None
    ) else []
    if web_tools_join:
        allowed_keys.add("mcpServers")
    if expected_denials:
        allowed_keys.add("permissions")
    if not isinstance(parsed, dict) or set(parsed) != allowed_keys:
        fail("managed session settings have an unexpected shape")
    if fast_mode != "inherit" and parsed.get("fastMode") is not (fast_mode == "on"):
        fail("managed session Fast setting is invalid")
    servers = parsed.get("mcpServers")
    if servers is not None and not (
        isinstance(servers, dict)
        and set(servers) == {"airlock-web-tools"}
        and isinstance(servers["airlock-web-tools"], dict)
        and isinstance(servers["airlock-web-tools"].get("command"), str)
        and servers["airlock-web-tools"].get("args") == [str(Path(web_tools_script).resolve())]
    ):
        fail("managed session web tools entry is invalid")
    permissions = parsed.get("permissions")
    if permissions is not None and not (
        isinstance(permissions, dict)
        and set(permissions) == {"allow", "deny"}
        and isinstance(permissions["allow"], list)
        and all(isinstance(entry, str) for entry in permissions["allow"])
        and permissions["allow"] == access.web_tool_allowances()
        and isinstance(permissions["deny"], list)
        and all(isinstance(entry, str) for entry in permissions["deny"])
        and set(permissions["deny"]) == set(expected_denials)
    ):
        fail("managed session web tool permissions are invalid")
    return settings


def validate_child_args(raw_args: object) -> list[str]:
    if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
        fail("Claude Code arguments are invalid")
    for argument in raw_args:
        option = argument.split("=", 1)[0]
        if option in PROTECTED_OPTIONS:
            fail(f"{option} is managed or incompatible with the Airlock worker guard", 2)
    return raw_args


def reject_openrouter_model_overrides(
    child_args: list[str], root_model: str | None = None
) -> None:
    """Reject forwarded --model flags on OpenRouter-rooted sessions.

    openrouter-pure forbids them outright: the route is the only selector. A
    hybrid OpenRouter root accepts a forwarded flag only when it restates the
    resolved root model exactly; anything else would let one argv move traffic
    off the route the user selected.
    """
    index = 0
    while index < len(child_args):
        argument = child_args[index]
        flagged = argument in {"--model", "-m"}
        valued = argument.startswith("--model=")
        if not flagged and not valued:
            index += 1
            continue
        if root_model is None:
            fail(
                "OpenRouter roots are selected by exact registry route; "
                "--model and -m cannot be forwarded",
                2,
            )
        if flagged:
            if index + 1 >= len(child_args):
                fail("--model requires a value", 2)
            value = child_args[index + 1]
            index += 2
        else:
            value = argument.split("=", 1)[1]
            index += 1
        if value != root_model:
            fail(
                "forwarded --model disagrees with the selected OpenRouter root route",
                2,
            )


def combine_routing_guidance(
    child_args: list[str], guidance: str
) -> tuple[list[str], str]:
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
    return result, combined


def windows_command_line_units(command: list[str]) -> int:
    rendered = subprocess.list2cmdline(command)
    return len(rendered.encode("utf-16-le", errors="surrogatepass")) // 2 + 1


def validate_windows_command_line(command: list[str]) -> None:
    units = windows_command_line_units(command)
    if units > WINDOWS_COMMAND_LINE_MAX_UNITS:
        fail(
            "Claude Code launch command is too long for Windows "
            f"({units} UTF-16 units; maximum {WINDOWS_COMMAND_LINE_MAX_UNITS} "
            "including the terminator); shorten forwarded arguments or pass a "
            "long prompt through standard input",
            2,
        )


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


def validate_policy_helper_path() -> Path:
    helper = Path(__file__).with_name("airlock_policy.py")
    if helper.is_symlink() or not helper.is_file():
        fail("managed policy helper is missing or unsafe")
    return helper.resolve()


def router_start_reason(stderr: str | None) -> str:
    """Return the router's own sanitized reason, when it reported one."""

    for line in (stderr or "").splitlines():
        line = line.strip()
        if line.startswith("airlock-router: "):
            reason = line[len("airlock-router: "):][:200]
            if reason.isprintable():
                return f": {reason}"
    return ""


def run_router_start(
    router: Path,
    snapshot: Path,
    snapshot_digest: str,
    proxy_url: str | None,
    background_model: str | None = None,
    port: int = 0,
) -> tuple[str | None, int, str]:
    """Start one router. Returns its address and pid, or None with a reason.

    Never exits the process: the supervisor calls this from a thread, where
    a failed restart must end the watch rather than the session.
    """
    command = [
        sys.executable,
        str(router),
        "start",
        "--parent-pid",
        str(os.getpid()),
        "--snapshot",
        str(snapshot),
        "--snapshot-sha256",
        snapshot_digest,
        "--port",
        str(port),
        "--print-pid",
    ]
    if proxy_url:
        command.extend(["--openai-url", proxy_url])
    if background_model:
        command.extend(["--background-model", background_model])
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
        return None, 0, ""
    reported = completed.stdout.strip().splitlines()
    address = reported[0].strip() if reported else ""
    if completed.returncode != 0 or not re.fullmatch(
        r"http://127\.0\.0\.1:[1-9][0-9]*", address
    ):
        return None, 0, router_start_reason(completed.stderr)
    # A router that does not report its process id still serves the session;
    # it only loses the watch that would restart it, so this is never fatal.
    try:
        router_pid = int(reported[1].strip())
    except (IndexError, ValueError):
        router_pid = 0
    return address, max(router_pid, 0), ""


def picker_background_model(route_policy: object) -> str | None:
    """The Haiku seat this session serves, as the picker map names it.

    Claude Code asks for a Haiku model by exact ID for compaction and other
    background work. The router substitutes the seated model for it, but
    only when it knows the seat.
    """
    if not isinstance(route_policy, dict):
        return None
    picker = route_policy.get("picker_models")
    if not isinstance(picker, dict):
        return None
    model = picker.get("haiku")
    if not isinstance(model, str):
        return None
    model = model.strip()
    if not model or len(model) > 128 or not model.isprintable():
        return None
    return model


def start_native_router(
    router: Path,
    snapshot: Path,
    snapshot_digest: str,
    proxy_url: str | None,
    background_model: str | None = None,
) -> tuple[str, int]:
    address, router_pid, reason = run_router_start(
        router, snapshot, snapshot_digest, proxy_url, background_model
    )
    if address is None:
        fail(f"native session router could not start securely{reason}")
    return address, router_pid


def start_router_watch(
    router: Path,
    snapshot: Path,
    snapshot_digest: str,
    proxy_url: str | None,
    background_model: str | None,
    address: str,
    router_pid: int,
) -> subprocess.Popen[bytes] | None:
    """Watch the router process and put a replacement on the same address.

    The watch itself lives in the router helper, so both launchers share one
    implementation of the restart, including the snapshot digest check it
    performs on every attempt.
    """
    try:
        port = int(address.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
    command = [
        sys.executable,
        str(router),
        "watch",
        "--parent-pid",
        str(os.getpid()),
        "--router-pid",
        str(router_pid),
        "--snapshot",
        str(snapshot),
        "--snapshot-sha256",
        snapshot_digest,
        "--port",
        str(port),
    ]
    if proxy_url:
        command.extend(["--openai-url", proxy_url])
    if background_model:
        command.extend(["--background-model", background_model])
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        # A session that cannot be supervised still runs; it just loses the
        # ability to survive its router being killed.
        return None


def stop_router_watch(watch: "subprocess.Popen[bytes] | None") -> None:
    if watch is None or watch.poll() is not None:
        return
    watch.terminate()
    try:
        watch.wait(timeout=5)
    except subprocess.TimeoutExpired:
        watch.kill()
        try:
            watch.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def build_child_environment(
    profile: str,
    max_agents: str,
    *,
    proxy_url: str | None,
    root_model: str,
    root_name: str,
    context_window: str,
    route_policy: dict[str, object],
    router_url: str | None,
    policy_helper: Path,
    snapshot_path: Path,
    snapshot_digest: str,
    force_context_window: bool = False,
    preserve_fast_transition: bool = False,
    ephemeral_openai_fast: bool = False,
) -> dict[str, str]:
    environment = os.environ.copy()
    if not preserve_fast_transition:
        environment.pop(FAST_TRANSITION_CHANNEL_ENV, None)
        environment.pop(FAST_TRANSITION_NONCE_ENV, None)
    # A window the user set themselves outranks Airlock's default. Read it before
    # the proxy cleanup below removes it.
    user_context_window = environment.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "")
    environment["AIRLOCK_ACTIVE_PROFILE"] = profile
    environment["AIRLOCK_POLICY_HELPER"] = str(policy_helper)
    environment["AIRLOCK_ACCESS_HELPER"] = str(Path(__file__).with_name("airlock-access.py").resolve())
    environment["AIRLOCK_PYTHON"] = sys.executable
    environment["AIRLOCK_SESSION_SNAPSHOT"] = str(snapshot_path)
    environment["AIRLOCK_SESSION_SNAPSHOT_SHA256"] = snapshot_digest
    # Claude Code enforces the cap itself, so the configured depth is the
    # real limit. It also decides whether a worker receives the Agent tool.
    agent_depth = environment.get("AIRLOCK_AGENT_DEPTH", "1")
    if agent_depth not in {"1", "2"}:
        agent_depth = "1"
    environment["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = agent_depth
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
        if not proxy_url:
            fail(f"{profile} requires a proxy URL")
        environment.pop("AIRLOCK_SESSION_ROUTER_URL", None)
        if ephemeral_openai_fast and profile == FAST_TRANSITION_PROFILE:
            environment["ANTHROPIC_MODEL"] = root_model
        require_proxy_environment(environment, proxy_url, root_model, profile)
        configure_proxy_model_picker(
            environment, profile, route_policy.get("picker_models")
        )
        environment.pop("AIRLOCK_HYBRID", None)
        environment.pop("AIRLOCK_GPT_HYBRID", None)
        environment.pop("AIRLOCK_GROK_HYBRID", None)
        environment.pop("AIRLOCK_OPENROUTER_HYBRID", None)
        if ephemeral_openai_fast:
            environment["AIRLOCK_EPHEMERAL_OPENAI_FAST"] = "1"
        else:
            environment.pop("AIRLOCK_EPHEMERAL_OPENAI_FAST", None)
        apply_context_window(
            environment,
            profile=profile,
            context_window=context_window,
            force_context_window=force_context_window,
            user_context_window=user_context_window,
        )
        apply_auto_mode_model(environment, root_model)
        environment.setdefault("CLAUDE_CODE_ALWAYS_ENABLE_EFFORT", "1")
        return environment

    if profile == "openrouter-pure":
        if proxy_url:
            fail("openrouter-pure cannot use the subscription proxy")
        if router_url is None:
            fail("OpenRouter native routing is missing its loopback router")
        for variable in PROXY_VARIABLES | ORP_CREDENTIAL_VARIABLES:
            environment.pop(variable, None)
        environment["ANTHROPIC_BASE_URL"] = router_url
        environment["AIRLOCK_SESSION_ROUTER_URL"] = router_url
        environment["ANTHROPIC_AUTH_TOKEN"] = "unused"
        environment["ANTHROPIC_MODEL"] = root_model
        configure_proxy_model_picker(
            environment, profile, route_policy.get("picker_models")
        )
        environment["ANTHROPIC_CUSTOM_MODEL_OPTION"] = root_model
        environment["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] = (
            f"{root_name} (OpenRouter route)"
        )
        environment["ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION"] = (
            f"Selected exact OpenRouter root ({root_model})"
        )
        declare_non_claude_effort_capabilities(
            environment, "ANTHROPIC_CUSTOM_MODEL_OPTION", root_model
        )
        apply_auto_mode_model(environment, root_model)
        environment.setdefault("CLAUDE_CODE_ALWAYS_ENABLE_EFFORT", "1")
        environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        environment["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] = "1"
        environment.pop("AIRLOCK_HYBRID", None)
        environment.pop("AIRLOCK_GPT_HYBRID", None)
        environment.pop("AIRLOCK_GROK_HYBRID", None)
        environment.pop("AIRLOCK_OPENROUTER_HYBRID", None)
        return environment

    if environment.get("ANTHROPIC_API_KEY") or environment.get("ANTHROPIC_AUTH_TOKEN") not in {None, "", "unused"}:
        fail(
            "hybrid native routing requires saved Claude subscription login without "
            "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
        )
    if router_url is None:
        fail("hybrid native routing is missing its loopback router")
    # An OpenRouter root keeps its key out of the Claude Code process exactly
    # like every proxy-backed provider; wrapper traffic is routed by the loopback
    # router either way.
    # OpenRouter credentials are loaded by the loopback router from protected
    # storage and never belong in any Claude Code child process.
    environment_variables = PROXY_VARIABLES | {"OPENROUTER_API_KEY"}
    if profile == "hybrid-openrouter-root":
        environment_variables |= ORP_CREDENTIAL_VARIABLES
    for variable in environment_variables:
        environment.pop(variable, None)
    environment["ANTHROPIC_BASE_URL"] = router_url
    environment["AIRLOCK_SESSION_ROUTER_URL"] = router_url
    configure_proxy_model_picker(
        environment, profile, route_policy.get("picker_models")
    )
    if profile == "hybrid-openrouter-root":
        option_name = f"{root_name} (OpenRouter hybrid root)"
        option_description = f"Selected OpenRouter hybrid root ({root_model})"
    else:
        option_name = f"{root_name} (native hybrid route)"
        option_description = f"Selected Airlock hybrid root ({root_model})"
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION"] = root_model
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] = option_name
    environment["ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION"] = option_description
    declare_non_claude_effort_capabilities(
        environment, "ANTHROPIC_CUSTOM_MODEL_OPTION", root_model
    )
    apply_context_window(
        environment,
        profile=profile,
        context_window=context_window,
        force_context_window=force_context_window,
        user_context_window=user_context_window,
    )
    apply_auto_mode_model(environment, root_model)
    environment.setdefault("CLAUDE_CODE_ALWAYS_ENABLE_EFFORT", "1")
    environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    environment["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] = "1"
    environment.pop("AIRLOCK_HYBRID", None)
    environment.pop("AIRLOCK_GPT_HYBRID", None)
    environment.pop("AIRLOCK_GROK_HYBRID", None)
    environment.pop("AIRLOCK_OPENROUTER_HYBRID", None)
    if profile == "hybrid-anthropic-root":
        environment["AIRLOCK_HYBRID"] = "1"
    elif profile == "hybrid-openai-root":
        environment["AIRLOCK_GPT_HYBRID"] = "1"
    elif profile == "hybrid-grok-root":
        environment["AIRLOCK_GROK_HYBRID"] = "1"
    elif profile == "hybrid-openrouter-root":
        environment["AIRLOCK_OPENROUTER_HYBRID"] = "1"
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


def apply_context_window(
    environment: dict[str, str],
    *,
    profile: str,
    context_window: str,
    force_context_window: bool,
    user_context_window: str,
) -> None:
    """Declare the root window and compact threshold for this process.

    Native Anthropic roots keep Claude Code's own sizing. OpenAI and Grok
    roots keep the conservative saved fallback, and no shipped root declares
    a hard limit: an inherited CLAUDE_CODE_MAX_CONTEXT_TOKENS is dropped so it
    can never silently cap this session's workers. A user-exported compact
    window still wins.
    """
    environment.pop("CLAUDE_CODE_MAX_CONTEXT_TOKENS", None)
    if user_context_window:
        environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = user_context_window
        return
    if force_context_window:
        if context_window == "auto":
            environment.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
        else:
            environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = context_window
        return
    if context_window == "auto" or profile == "hybrid-anthropic-root":
        environment.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
        return
    environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = context_window


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
    elif profile == "hybrid-openrouter-root":
        expected = "AIRLOCK_OPENROUTER_HYBRID"
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


def fast_transition_request(
    request: dict[str, object], *, enabled: bool
) -> tuple[int, str, str, str] | None:
    """Validate parent-owned binding metadata without accepting secret request data."""
    if "fast_transition_channel" in request or "fast_transition_nonce" in request:
        fail("Fast transition credentials must not be included in the launch request")
    channel = os.environ.get(FAST_TRANSITION_CHANNEL_ENV)
    nonce = os.environ.get(FAST_TRANSITION_NONCE_ENV)
    has_environment = channel is not None or nonce is not None
    has_metadata = (
        "fast_transition_launcher_pid" in request
        or "fast_transition_cwd" in request
    )
    if not enabled:
        return None
    if not has_environment and not has_metadata:
        return None
    if not has_environment or channel is None or nonce is None:
        fail("Fast transition channel and nonce must be supplied together")
    if not has_metadata:
        fail("Fast transition parent binding is missing")
    launcher_pid = request.get("fast_transition_launcher_pid")
    cwd = request.get("fast_transition_cwd")
    if (
        isinstance(launcher_pid, bool)
        or not isinstance(launcher_pid, int)
        or not 1 <= launcher_pid <= 0xFFFFFFFF
    ):
        fail("Fast transition launcher PID is invalid")
    if (
        not isinstance(cwd, str)
        or not cwd
        or len(cwd) > 4096
        or any(character in cwd for character in "\x00\r\n")
    ):
        fail("Fast transition cwd is invalid")
    return launcher_pid, cwd, channel, nonce


def fast_transition_effort(child_args: list[str]) -> str:
    efforts: list[str] = []
    index = 0
    while index < len(child_args):
        argument = child_args[index]
        if argument == "--effort":
            if index + 1 >= len(child_args):
                fail("--effort requires a value", 2)
            efforts.append(child_args[index + 1])
            index += 2
            continue
        if argument.startswith("--effort="):
            efforts.append(argument.split("=", 1)[1])
        index += 1
    if len(efforts) != 1 or efforts[0] not in {"low", "medium", "high", "xhigh", "max"}:
        fail("Fast transition requires one valid managed effort", 2)
    return efforts[0]


def validate_root_route(
    route_policy: dict[str, object], profile: str, root_model: str
) -> None:
    routes = route_policy.get("routes")
    expected_provider = {
        "openrouter-pure": "openrouter",
        "hybrid-openai-root": "openai",
        "hybrid-anthropic-root": "anthropic",
        "hybrid-grok-root": "grok",
        "hybrid-openrouter-root": "openrouter",
    }.get(profile)
    if not isinstance(routes, dict):
        fail("session route policy is invalid", 2)
    if root_model not in routes:
        fail(
            f"session root model '{root_model}' is not enabled by the active route policy",
            2,
        )
    if expected_provider is None or routes.get(root_model) != expected_provider:
        fail("session root model does not match the selected root provider", 2)
    if profile == "openrouter-pure" and set(routes.values()) != {"openrouter"}:
        fail("OpenRouter-only session policy contains another provider", 2)


def main(
    _request: dict[str, object] | None = None,
    _access=None,
    _policy: dict[str, object] | None = None,
    *,
    _allow_transition: bool = True,
    _fast_relaunch: bool = False,
) -> int:
    if _request is None:
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--request-file", required=True)
        parsed = parser.parse_args()
        request = read_request(resolve_managed_request(parsed.request_file))
    else:
        request = _request
    profile = request.get("profile")
    if profile not in PROFILES:
        fail("session profile is invalid")
    validate_launch_marker(profile)
    if os.environ.get("CLAUDE_CODE_SAFE_MODE") == "1" or os.environ.get("CLAUDE_CODE_SIMPLE") == "1":
        fail("CLAUDE_CODE_SAFE_MODE/CLAUDE_CODE_SIMPLE would disable managed session protection", 2)
    plugin_dir = validate_plugin_path(request.get("plugin_dir"))
    max_agents = validate_max_agents(request.get("max_agents"))

    openrouter_root_route: str | None = None
    if profile == "openrouter-pure":
        raw_route = request.get("openrouter_root_route")
        if not isinstance(raw_route, str) or not raw_route:
            fail("openrouter-pure requires an exact OpenRouter root route", 2)
        openrouter_root_route = raw_route
        for forbidden in ("proxy_url", "root_model", "root_name"):
            if forbidden in request:
                fail(f"openrouter-pure launch request must not include {forbidden}", 2)
        proxy_url: str | None = None
        root_model = ""
        root_name = ""
    elif profile == "hybrid-openrouter-root":
        raw_route = request.get("openrouter_root_route")
        if not isinstance(raw_route, str) or not raw_route:
            fail("hybrid-openrouter-root requires an exact OpenRouter root route", 2)
        openrouter_root_route = raw_route
        for forbidden in ("root_model", "root_name"):
            if forbidden in request:
                fail(
                    f"hybrid-openrouter-root launch request must not include {forbidden}",
                    2,
                )
        # The wrapper catalogs keep GPT and Grok routes in the snapshot, and the
        # router refuses to start without a subscription endpoint for them.
        proxy_url = required_request_string(request, "proxy_url", "OpenAI proxy URL")
        root_model = ""
        root_name = ""
    else:
        if "openrouter_root_route" in request:
            fail("OpenRouter root route is not valid for this session profile", 2)
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

    child_args = validate_child_args(request.get("args"))
    if profile == "openrouter-pure":
        reject_openrouter_model_overrides(child_args)
    transition = fast_transition_request(
        request, enabled=_allow_transition and not _fast_relaunch
    )
    transition_effort = fast_transition_effort(child_args) if transition else None

    access = _access if _access is not None else load_access_module()
    try:
        policy = _policy if _policy is not None else access.load_or_refresh_policy()
        if profile in ("openrouter-pure", "hybrid-openrouter-root"):
            root_entry = access.resolve_openrouter_route(
                policy, str(openrouter_root_route)
            )
            root_model = root_entry.model
            root_name = f"OpenRouter {root_entry.route}"
        if profile == "hybrid-openrouter-root":
            reject_openrouter_model_overrides(child_args, root_model)
        agents_json = render_agents(
            request.get("catalog_files"),
            profile,
            access,
            policy,
            openrouter_root_route=openrouter_root_route,
        )
        agent_names, allowed_tools = managed_agent_permissions(
            access, agents_json, policy
        )
        route_policy = access.session_route_policy(
            policy,
            profile,
            openrouter_root_route=openrouter_root_route,
        )
        if agent_names != route_policy.get("agent_names"):
            fail("rendered native Agents do not match the session route policy")
        session_settings = managed_session_settings(
            access, agents_json, fast_mode, policy,
            web_tools_script=str(
                plugin_dir / "mcp-server" / "airlock_web_tools.py"
            ),
            profile=profile,
        )
        guidance = access.profile_guidance(
            policy,
            profile,
            openrouter_root_route=openrouter_root_route,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(f"managed access policy could not be applied: {error}")

    policy_helper = validate_policy_helper_path()
    router: Path | None = None
    if profile in ROUTER_BACKED_PROFILES:
        if profile in HYBRID_PROFILES and (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ):
            fail(
                "hybrid native routing requires saved Claude subscription login without "
                "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
            )
        validate_root_route(route_policy, profile, root_model)
        router = validate_router_path(request.get("router_helper"))

    artifacts: list[tuple[str, Path, str, int]] = []
    mcp_config_path: Path | None = None
    snapshot_path: Path | None = None
    snapshot_digest: str | None = None
    return_code = 1
    resume_session_id: str | None = None
    try:
        child_args, combined_guidance = combine_routing_guidance(
            child_args, guidance
        )
        if profile == "openrouter-pure":
            child_args = ["--model", root_model, *child_args]

        try:
            settings_artifact = access.write_session_artifact(
                session_settings.encode("utf-8"), "settings-", ".json"
            )
            artifacts.append(("settings", *settings_artifact))
            guidance_artifact = access.write_session_artifact(
                combined_guidance.encode("utf-8"), "guidance-", ".txt"
            )
            artifacts.append(("routing guidance", *guidance_artifact))
            # Claude Code does not start mcpServers entries from --settings on
            # every supported version, so the same single-server definition is
            # handed to --mcp-config as a real artifact file.
            web_tools_script = str(
                plugin_dir / "mcp-server" / "airlock_web_tools.py"
            )
            mcp_output = access.write_web_tools_mcp_config(
                web_tools_script, profile=profile
            )
            if mcp_output:
                mcp_path, mcp_digest, mcp_size = mcp_output.split("\t", 2)
                mcp_config_path = Path(mcp_path)
                artifacts.append((
                    "web tools MCP config",
                    mcp_config_path, mcp_digest, int(mcp_size),
                ))
        except Exception as error:
            fail(f"managed session launch files could not be created: {error}")

        settings_path = artifacts[0][1]
        guidance_path = artifacts[1][1]
        child_args.extend(["--append-system-prompt-file", str(guidance_path)])
        command = [
            str(claude_path), "--settings", str(settings_path),
            "--plugin-dir", str(plugin_dir), "--agents", agents_json,
            "--allowedTools", *allowed_tools, *child_args,
        ]
        if mcp_config_path is not None:
            command.extend(["--mcp-config", str(mcp_config_path)])
        validate_windows_command_line(command)

        try:
            snapshot_path, snapshot_digest = access.write_session_snapshot(
                policy,
                profile,
                root_model,
                openrouter_root_route=openrouter_root_route,
            )
        except Exception as error:
            fail(f"session policy snapshot could not be created: {error}")

        router_url: str | None = None
        router_background: str | None = None
        router_pid = 0
        if router is not None:
            router_background = picker_background_model(route_policy)
            router_url, router_pid = start_native_router(
                router, snapshot_path, snapshot_digest, proxy_url,
                router_background,
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
            policy_helper=policy_helper,
            snapshot_path=snapshot_path,
            snapshot_digest=snapshot_digest,
            force_context_window=force_context_window,
            preserve_fast_transition=transition is not None,
            ephemeral_openai_fast=_fast_relaunch,
        )

        router_watch: subprocess.Popen[bytes] | None = None
        if router is not None and router_url is not None and router_pid > 0:
            router_watch = start_router_watch(
                router,
                snapshot_path,
                snapshot_digest,
                proxy_url,
                router_background,
                router_url,
                router_pid,
            )

        try:
            completed = subprocess.run(command, env=child_environment, check=False)
        except OSError as error:
            if getattr(error, "winerror", None) == 206:
                fail(
                    "Windows rejected the Claude Code launch data as too long "
                    "after Airlock's command preflight",
                    2,
                )
            fail(f"could not start Claude Code: {error}")
        finally:
            # The session is over the moment Claude Code exits, so stop
            # watching before the snapshot is cleaned up and a restart could
            # race it.
            stop_router_watch(router_watch)
        return_code = completed.returncode
        if return_code == 0 and transition is not None:
            launcher_pid, transition_cwd, channel, nonce = transition
            try:
                resume_session_id = access.fast_transition_consume(
                    launcher_pid,
                    transition_cwd,
                    channel,
                    nonce,
                    if_ready=True,
                )
            except Exception as error:
                access_error = getattr(access, "AccessError", None)
                if isinstance(access_error, type) and isinstance(error, access_error):
                    fail(str(error))
                fail("Fast transition could not be consumed")
    finally:
        for label, path, digest, size in reversed(artifacts):
            try:
                access.delete_session_artifact(path, digest, size)
            except Exception:
                print(
                    f"airlock: warning: managed session {label} cleanup failed: {path}",
                    file=sys.stderr,
                )
        if snapshot_path is not None and snapshot_digest is not None:
            try:
                access.delete_session_snapshot(snapshot_path, snapshot_digest)
            except Exception:
                print(
                    f"airlock: warning: session policy snapshot cleanup failed: {snapshot_path}",
                    file=sys.stderr,
                )

    if return_code != 0 or resume_session_id is None:
        return return_code
    try:
        fast_status = access.explicit_fast_status(
            policy, FAST_TRANSITION_ROUTE, ephemeral=True
        )
    except Exception as error:
        access_error = getattr(access, "AccessError", None)
        if isinstance(access_error, type) and isinstance(error, access_error):
            fail(f"Fast transition eligibility could not be revalidated: {error}")
        fail("Fast transition eligibility could not be revalidated")
    if not isinstance(fast_status, dict) or fast_status.get("eligible") is not True:
        reason = fast_status.get("reason") if isinstance(fast_status, dict) else None
        fail(str(reason) if isinstance(reason, str) and reason else "Fast transition is not eligible")

    resumed_request = dict(request)
    for key in (
        "fast_transition_launcher_pid",
        "fast_transition_cwd",
        "openrouter_root_route",
        "router_helper",
    ):
        resumed_request.pop(key, None)
    resumed_request.update({
        "profile": FAST_TRANSITION_PROFILE,
        "root_model": FAST_TRANSITION_MODEL,
        "root_name": FAST_TRANSITION_NAME,
        "fast_mode": "off",
        "args": [
            "--model", FAST_TRANSITION_MODEL,
            "--effort", str(transition_effort),
            "--resume", resume_session_id,
        ],
    })
    return main(
        resumed_request,
        access,
        policy,
        _allow_transition=False,
        _fast_relaunch=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
