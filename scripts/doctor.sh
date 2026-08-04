#!/usr/bin/env bash
set -u

failures=0
claude_signed_in=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }
info() { printf 'INFO  %s\n' "$1"; }

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

config_dir="$(resolve_airlock_config_dir)"

if command -v claude >/dev/null 2>&1; then
  pass "Claude Code: $(claude --version 2>/dev/null | head -1)"
  if claude auth status >/dev/null 2>&1; then
    pass 'Claude login is configured'
    claude_signed_in=1
  else
    info 'Claude login was not detected; run: claude auth login'
    info 'OpenAI-only sessions can still work, but hybrid and Claude routes need this login.'
  fi
else
  fail 'Claude Code is not on PATH'
fi

if command -v claude-code-proxy >/dev/null 2>&1; then
  pass "Proxy: $(claude-code-proxy --version 2>/dev/null | head -1)"
else
  fail 'claude-code-proxy is not on PATH'
fi

if command -v claude-code-proxy >/dev/null 2>&1 && claude-code-proxy codex auth status >/dev/null 2>&1; then
  pass 'Codex OAuth is configured'
else
  fail 'Codex OAuth is missing or expired'
fi

proxy_url="${AIRLOCK_PROXY_URL:-http://127.0.0.1:18765}"
proxy_healthy=0
if curl --silent --fail --max-time 2 "$proxy_url/healthz" >/dev/null 2>&1; then
  pass "Proxy health: $proxy_url/healthz"
  proxy_healthy=1
else
  fail "Proxy health: $proxy_url/healthz"
fi

if command -v airlock >/dev/null 2>&1; then
  launcher_path="$(command -v airlock)"
  pass "Launcher: $launcher_path"
  if grep -qF 'AIRLOCK_CONFIG_FILE' "$launcher_path" 2>/dev/null; then
    while IFS= read -r config_line; do
      info "$config_line"
    done < <(airlock config 2>/dev/null)
    if airlock bundle >/dev/null 2>&1; then
      pass 'Managed bundle is current and complete'
    else
      fail 'Managed bundle is stale or incomplete; reinstall and start a fresh session'
    fi
    router_path="${AIRLOCK_ROUTER_HELPER:-$(dirname "$launcher_path")/airlock-router.py}"
    if [[ -f "$router_path" && ! -L "$router_path" ]]; then
      pass "Hybrid router: $router_path (session-scoped loopback)"
    else
      fail "Hybrid router is missing or unsafe: $router_path"
    fi
    if grep -qF "'usage'" "$launcher_path" 2>/dev/null; then
      while IFS= read -r usage_line; do
        info "$usage_line"
      done < <(airlock usage 2>/dev/null)
    fi
  else
    fail 'Installed launcher predates managed bundle checks; reinstall and start a fresh session'
  fi
else
  fail 'airlock is not on PATH; add ~/.local/bin to PATH'
fi

if command -v brew >/dev/null 2>&1; then
  service_output="$(brew services list 2>/dev/null | awk '$1 == "claude-code-proxy" {print $2; exit}')"
  if [[ "$service_output" == 'started' ]]; then
    pass 'Homebrew service is started'
  elif [[ -n "$service_output" ]]; then
    if [[ "$proxy_healthy" -eq 1 ]]; then
      info "Homebrew service state: $service_output; another healthy service manager is active"
    else
      fail "Homebrew service state: $service_output"
    fi
  else
    info 'Homebrew service row not found; a manually started proxy may still be healthy'
  fi
fi

plugin_dir="${AIRLOCK_PLUGIN_DIR:-$config_dir/plugins/airlock}"
if [[ -d "$plugin_dir" && ! -L "$plugin_dir" \
  && -f "$plugin_dir/.claude-plugin/plugin.json" \
  && -f "$plugin_dir/hooks/hooks.json" \
  && -f "$plugin_dir/scripts/agent-guard.py" \
  && -f "$plugin_dir/scripts/secret-guard.py" \
  && -f "$plugin_dir/scripts/file_safety.py" \
  && -f "$plugin_dir/scripts/worktree.py" \
  && -f "$plugin_dir/scripts/worktree-create.sh" \
  && -f "$plugin_dir/scripts/worktree-remove.sh" ]]; then
  pass "Session plugin: $plugin_dir"
else
  fail "Session plugin is missing or unsafe: $plugin_dir"
fi

agent_file="${AIRLOCK_AGENT_DIR:-$HOME/.claude/agents}/airlock-worker.md"
if [[ -f "$agent_file" ]]; then
  agent_effort="$(awk -F': ' '$1 == "effort" {print $2; exit}' "$agent_file")"
  pass "Custom airlock-worker effort: ${agent_effort:-inherits the session level}"
else
  info 'Optional airlock-worker is not installed'
fi

if [[ "$failures" -gt 0 ]]; then
  printf '\nDoctor found %d problem(s).\n' "$failures" >&2
  exit 1
fi

if [[ "$claude_signed_in" -eq 1 ]]; then
  printf '\nAirlock is ready. No live model request was made.\n'
else
  printf '\nAirlock OpenAI-only sessions are ready. Sign in to Claude before using hybrid or Claude routes. No live model request was made.\n'
fi
