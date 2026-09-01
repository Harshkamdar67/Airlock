#!/usr/bin/env bash
set -euo pipefail

resolve_airlock_config_dir() {
  local standard_dir fallback_dir standard_parent
  if [[ -n "${AIRLOCK_CONFIG_DIR:-}" ]]; then
    printf '%s' "$AIRLOCK_CONFIG_DIR"
    return 0
  fi
  if [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
    printf '%s' "$XDG_CONFIG_HOME/airlock"
    return 0
  fi
  standard_dir="$HOME/.config/airlock"
  fallback_dir="$HOME/.airlock"
  standard_parent="$HOME/.config"
  if [[ -f "$fallback_dir/config" || -d "$fallback_dir" ]]; then
    printf '%s' "$fallback_dir"
  elif [[ -d "$standard_dir" && -w "$standard_dir" ]]; then
    printf '%s' "$standard_dir"
  elif [[ ! -e "$standard_dir" && -d "$standard_parent" && -w "$standard_parent" ]]; then
    printf '%s' "$standard_dir"
  elif [[ ! -e "$standard_parent" && -w "$HOME" ]]; then
    printf '%s' "$standard_dir"
  else
    printf '%s' "$fallback_dir"
  fi
}

directory_is_writable_or_creatable() {
  local directory="$1"
  local parent
  if [[ -d "$directory" ]]; then
    [[ -w "$directory" ]]
    return
  fi
  [[ ! -e "$directory" ]] || return 1
  parent="$(dirname "$directory")"
  while [[ ! -e "$parent" ]]; do
    directory="$parent"
    parent="$(dirname "$directory")"
    [[ "$parent" != "$directory" ]] || break
  done
  [[ -d "$parent" && -w "$parent" ]]
}

read_config_value() {
  local wanted_key="$1"
  local key value
  CONFIG_VALUE=''
  [[ -f "$config_target" ]] || return 0
  while IFS='=' read -r key value; do
    if [[ "$key" == "$wanted_key" ]]; then
      CONFIG_VALUE="${value%$'\r'}"
    fi
  done < "$config_target"
}

resolve_proxy_storage() {
  local configured_config_dir configured_state_home default_config_dir default_state_home
  read_config_value AIRLOCK_PROXY_CONFIG_DIR
  configured_config_dir="$CONFIG_VALUE"
  read_config_value AIRLOCK_PROXY_STATE_HOME
  configured_state_home="$CONFIG_VALUE"

  proxy_config_dir="${CCP_CONFIG_DIR:-${AIRLOCK_PROXY_CONFIG_DIR:-$configured_config_dir}}"
  proxy_state_home="${XDG_STATE_HOME:-${AIRLOCK_PROXY_STATE_HOME:-$configured_state_home}}"
  case "$(uname -s)" in
    Darwin) default_config_dir="$HOME/.config/claude-code-proxy" ;;
    *) default_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/claude-code-proxy" ;;
  esac
  default_state_home="$HOME/.local/state"

  if [[ -z "$proxy_config_dir" ]] && ! directory_is_writable_or_creatable "$default_config_dir"; then
    proxy_config_dir="$config_dir/claude-code-proxy"
  fi
  if [[ -z "$proxy_state_home" ]] && ! directory_is_writable_or_creatable "$default_state_home"; then
    proxy_state_home="$config_dir/proxy-state"
  fi
}

prepare_private_proxy_directory() {
  local directory="$1"
  [[ -n "$directory" ]] || return 0
  if [[ -L "$directory" || ( -e "$directory" && ! -d "$directory" ) ]]; then
    printf 'install: refusing unsafe proxy data directory.\n' >&2
    return 1
  fi
  if [[ ! -d "$directory" ]]; then
    (umask 077 && mkdir -p "$directory") 2>/dev/null || {
      printf 'install: Airlock could not create a private writable directory for the proxy.\n' >&2
      return 1
    }
  fi
  [[ -w "$directory" ]] || {
    printf 'install: the selected proxy data directory is not writable.\n' >&2
    return 1
  }
}

run_proxy_command() {
  if [[ -n "$proxy_config_dir" && -n "$proxy_state_home" ]]; then
    env CCP_CONFIG_DIR="$proxy_config_dir" XDG_STATE_HOME="$proxy_state_home" claude-code-proxy "$@"
  elif [[ -n "$proxy_config_dir" ]]; then
    env CCP_CONFIG_DIR="$proxy_config_dir" claude-code-proxy "$@"
  elif [[ -n "$proxy_state_home" ]]; then
    env XDG_STATE_HOME="$proxy_state_home" claude-code-proxy "$@"
  else
    claude-code-proxy "$@"
  fi
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="${AIRLOCK_INSTALL_DIR:-$HOME/.local/bin}"
launcher_target="$install_dir/airlock"
agent_dir="${AIRLOCK_AGENT_DIR:-$HOME/.claude/agents}"
agent_target="$agent_dir/airlock-worker.md"
config_dir="$(resolve_airlock_config_dir)"
config_target="$config_dir/config"
bundle_target="$config_dir/managed-bundle.json"
plugin_target="$config_dir/plugins/airlock"
config_source="${AIRLOCK_CONFIG_SOURCE:-$repo_root/config/airlock.conf.example}"
with_agent=0
run_login=0
skip_service=0
proxy_config_dir=''
proxy_state_home=''
resolve_proxy_storage

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [options]

Options:
  --with-agent   Install the optional custom sub-agent
  --login        Start interactive Codex OAuth if authentication is missing
  --no-service   Do not start the Homebrew background service
  -h, --help     Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-agent) with_agent=1 ;;
    --login) run_login=1 ;;
    --no-service) skip_service=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'install: unknown option %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

case "$(uname -s)" in
  Darwin|Linux) ;;
  *)
    printf 'install: automated setup supports macOS and Linux only.\n' >&2
    printf 'install: see the upstream Windows instructions linked from README.md.\n' >&2
    exit 1
    ;;
esac

for command_name in brew curl install; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'install: required command is missing: %s\n' "$command_name" >&2
    exit 1
  fi
done
if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  printf 'install: Python 3 is required for provider-aware session handling.\n' >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  printf 'install: Claude Code is missing. Install it from https://code.claude.com/docs/en/setup\n' >&2
  exit 1
fi

if ! command -v claude-code-proxy >/dev/null 2>&1; then
  printf 'Installing raine/claude-code-proxy with Homebrew...\n'
  brew install raine/claude-code-proxy/claude-code-proxy
fi

prepare_private_proxy_directory "$proxy_config_dir"
prepare_private_proxy_directory "$proxy_state_home"

if ! run_proxy_command codex auth status >/dev/null 2>&1; then
  if [[ "$run_login" -eq 1 ]]; then
    run_proxy_command codex auth login
  else
    printf '\nCodex OAuth needs interactive approval. Rerun this installer with:\n\n'
    printf '  ./scripts/install.sh --login\n\n'
    printf 'Airlock will choose the same private proxy directory automatically.\n'
    exit 2
  fi
fi

render_proxy_service_file() {
  local platform proxy_executable state_home log_dir service_file python_bin
  platform="$(uname -s)"
  proxy_executable="$(command -v claude-code-proxy)"
  state_home="${proxy_state_home:-${XDG_STATE_HOME:-$HOME/.local/state}}"
  log_dir="$state_home/claude-code-proxy"
  prepare_private_proxy_directory "$state_home"
  prepare_private_proxy_directory "$log_dir"
  case "$platform" in
    Darwin) service_file="$config_dir/claude-code-proxy.plist" ;;
    Linux) service_file="$config_dir/claude-code-proxy.service" ;;
    *) return 1 ;;
  esac
  if [[ -e "$service_file" ]] && ! grep -qF 'Managed by https://github.com/Harshkamdar67/Airlock' "$service_file"; then
    printf 'install: refusing to overwrite an unmanaged proxy service file.\n' >&2
    return 1
  fi
  if command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  else
    python_bin="$(command -v python)"
  fi
  "$python_bin" - "$platform" "$service_file" "$proxy_executable" "$proxy_config_dir" "$proxy_state_home" "$log_dir/service.log" <<'PY'
from pathlib import Path
import os
import plistlib
import sys
import tempfile

platform, target_text, executable, config_dir, state_home, log_file = sys.argv[1:]
target = Path(target_text)
environment = {}
if config_dir:
    environment["CCP_CONFIG_DIR"] = config_dir
if state_home:
    environment["XDG_STATE_HOME"] = state_home
marker = "Managed by https://github.com/Harshkamdar67/Airlock"
if platform == "Darwin":
    payload = plistlib.dumps(
        {
            "Label": "homebrew.mxcl.claude-code-proxy",
            "ProgramArguments": [executable, "serve", "--no-monitor"],
            "KeepAlive": True,
            "RunAtLoad": True,
            "EnvironmentVariables": environment,
            "StandardOutPath": log_file,
            "StandardErrorPath": log_file,
        },
        sort_keys=False,
    ).decode("utf-8")
    payload = payload.replace("<plist version=\"1.0\">", f"<!-- {marker} -->\n<plist version=\"1.0\">", 1)
else:
    def quote(value: str) -> str:
        value = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
        return f'"{value}"'

    lines = [
        f"# {marker}",
        "[Unit]",
        "Description=Airlock OpenAI proxy",
        "",
        "[Service]",
        f"ExecStart={quote(executable)} serve --no-monitor",
        "Restart=always",
        "RestartSec=1",
    ]
    for key, value in environment.items():
        lines.append(f"Environment={quote(f'{key}={value}')}")
    lines.extend(["", "[Install]", "WantedBy=default.target", ""])
    payload = "\n".join(lines)

target.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
  printf '%s' "$service_file"
}

start_proxy_background() {
  local proxy_executable state_home log_dir
  proxy_executable="$(command -v claude-code-proxy)"
  state_home="${proxy_state_home:-${XDG_STATE_HOME:-$HOME/.local/state}}"
  log_dir="$state_home/claude-code-proxy"
  prepare_private_proxy_directory "$state_home"
  prepare_private_proxy_directory "$log_dir"
  if [[ -n "$proxy_config_dir" && -n "$proxy_state_home" ]]; then
    nohup env CCP_CONFIG_DIR="$proxy_config_dir" XDG_STATE_HOME="$proxy_state_home" "$proxy_executable" serve --no-monitor >>"$log_dir/service.log" 2>&1 &
  elif [[ -n "$proxy_config_dir" ]]; then
    nohup env CCP_CONFIG_DIR="$proxy_config_dir" "$proxy_executable" serve --no-monitor >>"$log_dir/service.log" 2>&1 &
  elif [[ -n "$proxy_state_home" ]]; then
    nohup env XDG_STATE_HOME="$proxy_state_home" "$proxy_executable" serve --no-monitor >>"$log_dir/service.log" 2>&1 &
  else
    nohup "$proxy_executable" serve --no-monitor >>"$log_dir/service.log" 2>&1 &
  fi
}

start_proxy_service() {
  local service_file=''
  if [[ -n "$proxy_config_dir" || -n "$proxy_state_home" ]]; then
    service_file="$(render_proxy_service_file)" || return 1
    brew services stop claude-code-proxy >/dev/null 2>&1 || true
    if brew services start claude-code-proxy --file="$service_file" >/dev/null 2>&1 || \
       brew services start raine/claude-code-proxy/claude-code-proxy --file="$service_file" >/dev/null 2>&1; then
      return 0
    fi
    printf 'Homebrew could not register the private proxy service; starting it for this login session instead.\n'
    start_proxy_background
  else
    brew services start claude-code-proxy >/dev/null 2>&1 || \
      brew services start raine/claude-code-proxy/claude-code-proxy >/dev/null
  fi
}

if [[ "$skip_service" -eq 0 ]]; then
  start_proxy_service
fi

mkdir -p "$config_dir"
if [[ ! -e "$config_target" ]]; then
  install -m 0644 "$config_source" "$config_target"
else
  printf 'Preserving existing config: %s\n' "$config_target"
fi

mkdir -p "$install_dir"
airlock_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    command -v python
  fi
}

# A file whose hash matches what the installed bundle recorded for the same
# component was written by a previous Airlock release, even if it carries no
# marker string. JSON catalogs never carried one.
target_matches_installed_bundle() {
  local component="$1"
  local target="$2"
  local python_bin
  [[ -f "$bundle_target" && -f "$target" ]] || return 1
  python_bin="$(airlock_python)"
  [[ -n "$python_bin" ]] || return 1
  "$python_bin" - "$bundle_target" "$component" "$target" <<'PY'
import hashlib
import json
import sys

bundle_path, component, target = sys.argv[1:4]
try:
    with open(bundle_path, encoding="utf-8") as handle:
        recorded = json.load(handle).get("components", {}).get(component)
    if not isinstance(recorded, str) or not recorded:
        raise SystemExit(1)
    with open(target, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if digest == recorded else 1)
PY
}

install_managed_file() {
  local source="$1"
  local target="$2"
  local mode="$3"
  local component="${source#"$repo_root/"}"
  if [[ -L "$target" ]]; then
    printf 'install: refusing to replace symlinked managed target: %s\n' "$target" >&2
    exit 1
  fi
  if [[ -e "$target" ]] && ! cmp -s "$source" "$target"; then
    if ! grep -qF 'Managed by https://github.com/Harshkamdar67/Airlock' "$target" &&
       ! grep -qF 'Managed by Airlock' "$target" &&
       ! target_matches_installed_bundle "$component" "$target"; then
      printf 'install: refusing to overwrite existing unmanaged file: %s\n' "$target" >&2
      printf 'install: review it, move it, or choose another install/config directory.\n' >&2
      exit 1
    fi
  fi
  install -m "$mode" "$source" "$target"
}

install_managed_file "$repo_root/bin/airlock" "$launcher_target" 0755
install_managed_file "$repo_root/bin/airlock-access.py" "$install_dir/airlock-access.py" 0755
install_managed_file "$repo_root/bin/airlock_policy.py" "$install_dir/airlock_policy.py" 0755
install_managed_file "$repo_root/bin/airlock_openrouter_auth.py" "$install_dir/airlock_openrouter_auth.py" 0755
install_managed_file "$repo_root/bin/airlock_openrouter_presets.py" "$install_dir/airlock_openrouter_presets.py" 0755
install_managed_file "$repo_root/bin/airlock_openrouter_models.py" "$install_dir/airlock_openrouter_models.py" 0755
install_managed_file "$repo_root/bin/airlock-update.py" "$install_dir/airlock-update.py" 0755
install_managed_file "$repo_root/bin/airlock-router.py" "$install_dir/airlock-router.py" 0755
install_managed_file "$repo_root/bin/airlock-hybrid.py" "$install_dir/airlock-hybrid.py" 0755
install_managed_file "$repo_root/config/openai-direct-agents.json" "$config_dir/openai-direct-agents.json" 0644
install_managed_file "$repo_root/config/anthropic-direct-agents.json" "$config_dir/anthropic-direct-agents.json" 0644
install_managed_file "$repo_root/config/hybrid-agents.json" "$config_dir/hybrid-agents.json" 0644
install_managed_file "$repo_root/config/claude-agents.json" "$config_dir/claude-agents.json" 0644
install_managed_file "$repo_root/config/grok-agents.json" "$config_dir/grok-agents.json" 0644

ensure_plugin_directory() {
  local directory="$1"
  if [[ -L "$directory" ]]; then
    printf 'install: refusing symlinked plugin directory: %s\n' "$directory" >&2
    exit 1
  fi
  if [[ -e "$directory" && ! -d "$directory" ]]; then
    printf 'install: plugin path is not a directory: %s\n' "$directory" >&2
    exit 1
  fi
  mkdir -p "$directory"
}
ensure_plugin_directory "$config_dir/plugins"
ensure_plugin_directory "$plugin_target"
ensure_plugin_directory "$plugin_target/.claude-plugin"
ensure_plugin_directory "$plugin_target/hooks"
ensure_plugin_directory "$plugin_target/mcp-server"
ensure_plugin_directory "$plugin_target/skills"
ensure_plugin_directory "$plugin_target/skills/usage"
ensure_plugin_directory "$plugin_target/skills/airlock-fast"
ensure_plugin_directory "$plugin_target/scripts"
install_managed_file "$repo_root/plugins/airlock/.claude-plugin/plugin.json" "$plugin_target/.claude-plugin/plugin.json" 0644
install_managed_file "$repo_root/plugins/airlock/hooks/hooks.json" "$plugin_target/hooks/hooks.json" 0644
install_managed_file "$repo_root/plugins/airlock/skills/usage/SKILL.md" "$plugin_target/skills/usage/SKILL.md" 0644
install_managed_file "$repo_root/plugins/airlock/skills/airlock-fast/SKILL.md" "$plugin_target/skills/airlock-fast/SKILL.md" 0644
install_managed_file "$repo_root/plugins/airlock/mcp-server/airlock_web_tools.py" "$plugin_target/mcp-server/airlock_web_tools.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/fast-session-end.sh" "$plugin_target/scripts/fast-session-end.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/fast-session-end.py" "$plugin_target/scripts/fast-session-end.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/router-session-end.sh" "$plugin_target/scripts/router-session-end.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/router-session-end.py" "$plugin_target/scripts/router-session-end.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/router-turn-notice.sh" "$plugin_target/scripts/router-turn-notice.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/router-turn-notice.py" "$plugin_target/scripts/router-turn-notice.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/agent-guard.sh" "$plugin_target/scripts/agent-guard.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/agent-guard.py" "$plugin_target/scripts/agent-guard.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/secret-guard.sh" "$plugin_target/scripts/secret-guard.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/secret-guard.py" "$plugin_target/scripts/secret-guard.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/update-notice.sh" "$plugin_target/scripts/update-notice.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/update-notice.py" "$plugin_target/scripts/update-notice.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/file_safety.py" "$plugin_target/scripts/file_safety.py" 0644
install_managed_file "$repo_root/plugins/airlock/scripts/worktree.py" "$plugin_target/scripts/worktree.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/worktree-create.sh" "$plugin_target/scripts/worktree-create.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/worktree-remove.sh" "$plugin_target/scripts/worktree-remove.sh" 0755

# Write the bundle marker last so interrupted installs fail closed as incomplete.
install_managed_file "$repo_root/config/managed-bundle.json" "$bundle_target" 0644

if [[ "$with_agent" -eq 1 ]]; then
  subagent_effort="${AIRLOCK_SUBAGENT_EFFORT:-}"
  if [[ -z "$subagent_effort" && -f "$config_target" ]]; then
    while IFS='=' read -r config_key config_value; do
      if [[ "$config_key" == 'AIRLOCK_SUBAGENT_EFFORT' ]]; then
        subagent_effort="${config_value%$'\r'}"
      fi
    done < "$config_target"
  fi
  subagent_effort="${subagent_effort:-inherit}"
  case "$subagent_effort" in
    inherit|low|medium|high|xhigh|max) ;;
    *)
      printf 'install: unsupported sub-agent effort: %s\n' "$subagent_effort" >&2
      exit 1
      ;;
  esac

  mkdir -p "$(dirname "$agent_target")"
  rendered_agent="$(mktemp "${TMPDIR:-/tmp}/airlock-agent.XXXXXX")"
  trap 'rm -f "$rendered_agent"' EXIT
  # An agent with no effort of its own follows the session level, so /effort
  # moves it mid-session. A named level pins it instead.
  if [[ "$subagent_effort" == 'inherit' ]]; then
    sed '/^effort: /d' "$repo_root/examples/agents/airlock-worker.md" > "$rendered_agent"
  else
    sed "s/^effort: .*/effort: $subagent_effort/" "$repo_root/examples/agents/airlock-worker.md" > "$rendered_agent"
  fi

  if [[ -e "$agent_target" ]] && ! cmp -s "$rendered_agent" "$agent_target"; then
    if ! grep -qF '<!-- Managed by https://github.com/Harshkamdar67/Airlock -->' "$agent_target" &&
       ! grep -qF '<!-- Managed by Airlock -->' "$agent_target"; then
      printf 'install: refusing to overwrite existing unmanaged agent: %s\n' "$agent_target" >&2
      exit 1
    fi
  fi
  install -m 0644 "$rendered_agent" "$agent_target"
  rm -f "$rendered_agent"
  trap - EXIT
fi

printf '\nAirlock installed.\n'
printf '  Launcher: %s\n' "$launcher_target"
printf '  Config:   %s\n' "$config_target"
printf '  Bundle:   %s\n' "$bundle_target"
printf '  Plugin:   %s (session-scoped; not registered globally)\n' "$plugin_target"
if [[ "$with_agent" -eq 1 ]]; then
  printf '  Agent:    %s\n' "$agent_target"
fi
printf '\nRun: %s models\n' "$launcher_target"
printf 'Then: %s\n' "$repo_root/scripts/doctor.sh"
