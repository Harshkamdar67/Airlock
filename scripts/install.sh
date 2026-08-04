#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="${AIRLOCK_INSTALL_DIR:-$HOME/.local/bin}"
launcher_target="$install_dir/airlock"
agent_dir="${AIRLOCK_AGENT_DIR:-$HOME/.claude/agents}"
agent_target="$agent_dir/airlock-worker.md"
config_dir="${AIRLOCK_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/airlock}"
config_target="$config_dir/config"
bundle_target="$config_dir/managed-bundle.json"
plugin_target="$config_dir/plugins/airlock"
config_source="${AIRLOCK_CONFIG_SOURCE:-$repo_root/config/airlock.conf.example}"
with_agent=0
run_login=0
skip_service=0

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [options]

Options:
  --with-agent   Install the optional high-effort custom sub-agent
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

if ! claude-code-proxy codex auth status >/dev/null 2>&1; then
  if [[ "$run_login" -eq 1 ]]; then
    claude-code-proxy codex auth login
  else
    printf '\nCodex OAuth needs interactive approval. Run:\n\n'
    printf '  claude-code-proxy codex auth login\n\n'
    printf 'Then rerun this installer. No token needs to be copied into Airlock.\n'
    exit 2
  fi
fi

if [[ "$skip_service" -eq 0 ]]; then
  brew services start claude-code-proxy >/dev/null 2>&1 || \
    brew services start raine/claude-code-proxy/claude-code-proxy >/dev/null
fi

mkdir -p "$config_dir"
if [[ ! -e "$config_target" ]]; then
  install -m 0644 "$config_source" "$config_target"
else
  printf 'Preserving existing config: %s\n' "$config_target"
fi

mkdir -p "$install_dir"
install_managed_file() {
  local source="$1"
  local target="$2"
  local mode="$3"
  if [[ -L "$target" ]]; then
    printf 'install: refusing to replace symlinked managed target: %s\n' "$target" >&2
    exit 1
  fi
  if [[ -e "$target" ]] && ! cmp -s "$source" "$target"; then
    if ! grep -qF 'Managed by https://github.com/Harshkamdar67/Airlock' "$target" &&
       ! grep -qF 'Managed by Airlock' "$target"; then
      printf 'install: refusing to overwrite existing unmanaged file: %s\n' "$target" >&2
      printf 'install: review it, move it, or choose another install/config directory.\n' >&2
      exit 1
    fi
  fi
  install -m "$mode" "$source" "$target"
}

install_managed_file "$repo_root/bin/airlock" "$launcher_target" 0755
install_managed_file "$repo_root/bin/airlock-access.py" "$install_dir/airlock-access.py" 0755
install_managed_file "$repo_root/bin/airlock-router.py" "$install_dir/airlock-router.py" 0755
install_managed_file "$repo_root/bin/airlock-hybrid.py" "$install_dir/airlock-hybrid.py" 0755
install_managed_file "$repo_root/config/openai-direct-agents.json" "$config_dir/openai-direct-agents.json" 0644
install_managed_file "$repo_root/config/anthropic-direct-agents.json" "$config_dir/anthropic-direct-agents.json" 0644
install_managed_file "$repo_root/config/hybrid-agents.json" "$config_dir/hybrid-agents.json" 0644
install_managed_file "$repo_root/config/claude-agents.json" "$config_dir/claude-agents.json" 0644

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
ensure_plugin_directory "$plugin_target/skills"
ensure_plugin_directory "$plugin_target/skills/usage"
ensure_plugin_directory "$plugin_target/scripts"
install_managed_file "$repo_root/plugins/airlock/.claude-plugin/plugin.json" "$plugin_target/.claude-plugin/plugin.json" 0644
install_managed_file "$repo_root/plugins/airlock/hooks/hooks.json" "$plugin_target/hooks/hooks.json" 0644
install_managed_file "$repo_root/plugins/airlock/skills/usage/SKILL.md" "$plugin_target/skills/usage/SKILL.md" 0644
install_managed_file "$repo_root/plugins/airlock/scripts/agent-guard.sh" "$plugin_target/scripts/agent-guard.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/agent-guard.py" "$plugin_target/scripts/agent-guard.py" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/secret-guard.sh" "$plugin_target/scripts/secret-guard.sh" 0755
install_managed_file "$repo_root/plugins/airlock/scripts/secret-guard.py" "$plugin_target/scripts/secret-guard.py" 0755
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
