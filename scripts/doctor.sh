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

config_dir="$(resolve_airlock_config_dir)"
config_target="${AIRLOCK_CONFIG_FILE:-$config_dir/config}"
config_proxy_config_dir=''
config_proxy_state_home=''
config_grok_models=''
config_default_profile=''
if [[ -f "$config_target" ]]; then
  while IFS='=' read -r config_key config_value; do
    config_value="${config_value%$'\r'}"
    case "$config_key" in
      AIRLOCK_PROXY_CONFIG_DIR) config_proxy_config_dir="$config_value" ;;
      AIRLOCK_PROXY_STATE_HOME) config_proxy_state_home="$config_value" ;;
      AIRLOCK_GROK_MODELS) config_grok_models="$config_value" ;;
      AIRLOCK_DEFAULT_PROFILE) config_default_profile="$config_value" ;;
    esac
  done < "$config_target"
fi
proxy_config_dir="${CCP_CONFIG_DIR:-${AIRLOCK_PROXY_CONFIG_DIR:-$config_proxy_config_dir}}"
proxy_state_home="${XDG_STATE_HOME:-${AIRLOCK_PROXY_STATE_HOME:-$config_proxy_state_home}}"
case "$(uname -s)" in
  Darwin) default_proxy_config_dir="$HOME/.config/claude-code-proxy" ;;
  *) default_proxy_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/claude-code-proxy" ;;
esac
default_proxy_state_home="$HOME/.local/state"
if [[ -z "$proxy_config_dir" ]] && ! directory_is_writable_or_creatable "$default_proxy_config_dir"; then
  proxy_config_dir="$config_dir/claude-code-proxy"
fi
if [[ -z "$proxy_state_home" ]] && ! directory_is_writable_or_creatable "$default_proxy_state_home"; then
  proxy_state_home="$config_dir/proxy-state"
fi

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
  pass "Proxy: $(run_proxy_command --version 2>/dev/null | head -1)"
else
  fail 'claude-code-proxy is not on PATH'
fi

if command -v claude-code-proxy >/dev/null 2>&1 && run_proxy_command codex auth status >/dev/null 2>&1; then
  pass 'Codex OAuth is configured'
else
  fail 'Codex OAuth is missing or expired'
  info 'Run: airlock proxy auth login'
fi

# Grok is optional, so a missing Grok login is only a failure once the saved
# configuration actually enables Grok routes.
if [[ -n "${AIRLOCK_GROK_MODELS:-$config_grok_models}" \
  || "${AIRLOCK_DEFAULT_PROFILE:-$config_default_profile}" == 'grok' ]]; then
  grok_status_output=''
  if command -v claude-code-proxy >/dev/null 2>&1; then
    grok_status_output="$(run_proxy_command grok auth status 2>&1)" || grok_status_output=''
  fi
  if [[ -n "$grok_status_output" ]]; then
    pass 'Grok OAuth is configured'
    # The access token is short lived, but the proxy renews it from the stored
    # refresh token about five minutes before expiry. This is informational, so
    # do not advise a re-login just because the number looks small.
    grok_expires="$(printf '%s\n' "$grok_status_output" | sed -n 's/.*Expires in \([0-9][0-9]*\)s.*/\1/p' | head -1)"
    if [[ -n "$grok_expires" ]]; then
      info "Grok access token expires in $((grok_expires / 3600))h $(((grok_expires % 3600) / 60))m; the proxy renews it automatically"
    fi
  else
    fail 'Grok OAuth is missing or expired'
    info 'Run: airlock proxy grok auth login'
  fi
else
  info 'Grok workers are not enabled in the saved configuration'
fi

# Catalog discovery is informational only: newer gateway IDs are surfaced so a
# person can declare them in models.json; nothing is ever enabled implicitly.
if command -v claude-code-proxy >/dev/null 2>&1 \
  && [[ -n "${AIRLOCK_GROK_MODELS:-$config_grok_models}" \
    || "${AIRLOCK_DEFAULT_PROFILE:-$config_default_profile}" == 'grok' ]]; then
  discovery_python=''
  if command -v python3 >/dev/null 2>&1; then
    discovery_python="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    discovery_python="$(command -v python)"
  fi
  if [[ -n "$discovery_python" ]]; then
    models_file="${AIRLOCK_MODELS_FILE:-$config_dir/models.json}"
    declared_grok="$("$discovery_python" - "$models_file" <<'PYDISCOVERY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
models = data.get("models") if isinstance(data, dict) else None
if isinstance(models, list):
    for entry in models:
        try:
            if entry.get("provider") == "grok" and entry.get("enabled") is True:
                print(entry["id"])
        except Exception:
            pass
PYDISCOVERY
)" || declared_grok=''
    enabled_grok=" grok-4.6 grok-composer-2.5-fast $(printf '%s ' "$declared_grok")"
    while IFS= read -r catalog_line; do
      case "$catalog_line" in
        'grok:'*)
          # The proxy prints the catalog comma separated, so strip separators
          # before matching or every entry but the last keeps a trailing comma
          # and looks unrecognized.
          # shellcheck disable=SC2086
          for catalog_id in ${catalog_line#grok:}; do
            catalog_id="${catalog_id%,}"
            [ -n "$catalog_id" ] || continue
            case "$enabled_grok" in
              *" $catalog_id "*) ;;
              *) info "Available but not enabled (Grok): $catalog_id; declare it in models.json to use it" ;;
            esac
          done
          ;;
      esac
    done < <(run_proxy_command models 2>/dev/null)
  fi
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
    updater_path="${AIRLOCK_UPDATE_HELPER:-$(dirname "$launcher_path")/airlock-update.py}"
    if [[ -f "$updater_path" && ! -L "$updater_path" ]]; then
      pass "Release updater: $updater_path (manual checks only)"
    else
      fail "Release updater is missing or unsafe: $updater_path"
    fi

    # Match the launcher's probe: a candidate has to answer as Python 3 before
    # it is accepted. On Windows the python3 name is often the Microsoft Store
    # placeholder, which exists on PATH but is not an interpreter, so a probe
    # that only checks the name would pick it and every helper check would fail.
    python_bin=''
    for python_candidate in "${AIRLOCK_PYTHON:-}" python3 python; do
      [[ -n "$python_candidate" ]] || continue
      if command -v "$python_candidate" >/dev/null 2>&1 && \
        "$python_candidate" -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' >/dev/null 2>&1; then
        python_bin="$(command -v "$python_candidate")"
        break
      fi
    done
    policy_path="${AIRLOCK_POLICY_HELPER:-$(dirname "$launcher_path")/airlock_policy.py}"
    openrouter_auth_path="${AIRLOCK_OPENROUTER_AUTH_HELPER:-$(dirname "$launcher_path")/airlock_openrouter_auth.py}"
    openrouter_presets_path="${AIRLOCK_OPENROUTER_PRESETS_HELPER:-$(dirname "$launcher_path")/airlock_openrouter_presets.py}"
    openrouter_models_path="${AIRLOCK_OPENROUTER_MODELS_HELPER:-$(dirname "$launcher_path")/airlock_openrouter_models.py}"
    openrouter_registry_path="${AIRLOCK_OPENROUTER_REGISTRY_FILE:-$config_dir/openrouter-registry.json}"
    openrouter_helpers_safe=1
    for helper_path in "$policy_path" "$openrouter_auth_path" "$openrouter_presets_path" "$openrouter_models_path"; do
      if [[ ! -f "$helper_path" || -L "$helper_path" ]]; then
        fail "OpenRouter helper is missing or unsafe: $helper_path"
        openrouter_helpers_safe=0
      fi
    done
    if [[ -z "$python_bin" ]]; then
      fail 'Python 3 is required for OpenRouter checks'
      openrouter_helpers_safe=0
    fi

    openrouter_registry_state='unknown'
    openrouter_registry_count=0
    if [[ "$openrouter_helpers_safe" -eq 1 ]]; then
      openrouter_registry_output="$(
        "$python_bin" "$openrouter_models_path" \
          --registry "$openrouter_registry_path" _doctor-status 2>&1
      )"
      openrouter_registry_status=$?
      while IFS= read -r openrouter_line; do
        # A Python helper on Windows ends every line with a carriage return, and
        # command substitution only strips the final one, so drop it here or the
        # first line's value never matches.
        openrouter_line="${openrouter_line%$'\r'}"
        case "$openrouter_line" in
          STATE=*) openrouter_registry_state="${openrouter_line#STATE=}" ;;
          COUNT=*) openrouter_registry_count="${openrouter_line#COUNT=}" ;;
          MODEL=*)
            openrouter_model_line="${openrouter_line#MODEL=}"
            IFS=$'\t' read -r openrouter_route openrouter_model openrouter_endpoint openrouter_enabled <<<"$openrouter_model_line"
            info "OpenRouter route: airlock-or-$openrouter_route -> $openrouter_model via $openrouter_endpoint ($openrouter_enabled)"
            ;;
          DETAIL=*) info "OpenRouter registry detail: ${openrouter_line#DETAIL=}" ;;
        esac
      done <<<"$openrouter_registry_output"
      case "$openrouter_registry_state" in
        absent) info "OpenRouter registry is not configured: $openrouter_registry_path" ;;
        valid) pass "OpenRouter registry is valid and fresh ($openrouter_registry_count model(s)): $openrouter_registry_path" ;;
        stale) fail "OpenRouter registry metadata is stale; refresh it before starting a new session" ;;
        invalid) fail "OpenRouter registry is invalid: $openrouter_registry_path" ;;
        *)
          fail "OpenRouter registry status could not be determined: $openrouter_registry_path"
          [[ "$openrouter_registry_status" -eq 0 ]] || info 'The registry helper returned an error.'
          ;;
      esac

      openrouter_backend_output="$("$python_bin" "$openrouter_auth_path" _backend-status 2>&1)"
      openrouter_backend_status=$?
      openrouter_backend='unknown'
      openrouter_backend_state='unknown'
      while IFS= read -r openrouter_line; do
        openrouter_line="${openrouter_line%$'\r'}"
        case "$openrouter_line" in
          BACKEND=*) openrouter_backend="${openrouter_line#BACKEND=}" ;;
          STATE=*) openrouter_backend_state="${openrouter_line#STATE=}" ;;
        esac
      done <<<"$openrouter_backend_output"
      if [[ "$openrouter_backend_state" == 'available' ]]; then
        pass "OpenRouter credential backend is available: $openrouter_backend"
      elif [[ "$openrouter_registry_count" -gt 0 ]]; then
        fail "OpenRouter credential backend is unavailable: $openrouter_backend"
      else
        info "OpenRouter credential backend is unavailable: $openrouter_backend"
      fi
      info 'Doctor does not read the OpenRouter credential; run airlock openrouter auth status to check it.'
      [[ "$openrouter_backend_status" -eq 0 || "$openrouter_backend_state" == 'unavailable' ]] || \
        fail 'OpenRouter credential backend status could not be determined'
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
  && -f "$plugin_dir/skills/airlock-fast/SKILL.md" \
  && -f "$plugin_dir/scripts/fast-session-end.sh" \
  && -f "$plugin_dir/scripts/fast-session-end.py" \
  && -f "$plugin_dir/scripts/agent-guard.py" \
  && -f "$plugin_dir/scripts/secret-guard.py" \
  && -f "$plugin_dir/scripts/update-notice.sh" \
  && -f "$plugin_dir/scripts/update-notice.py" \
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
