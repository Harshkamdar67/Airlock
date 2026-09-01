#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
launcher="$repo_root/bin/airlock"
stub="$repo_root/tests/stub-claude.sh"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/airlock-launcher-test.XXXXXX")"
status_server_pid=''
cleanup() {
  if [[ -n "$status_server_pid" ]]; then
    kill "$status_server_pid" 2>/dev/null || true
    wait "$status_server_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT
# The launcher keeps a window the user set themselves, so a suite that runs
# inside an Airlock session would otherwise inherit that session's window and
# read it back as the launcher's own choice. The same applies to the saved
# Agent depth and to armed Fast transition credentials.
unset CLAUDE_CODE_AUTO_COMPACT_WINDOW AIRLOCK_CONTEXT_WINDOW AIRLOCK_SESSION_ROUTER_URL \
  AIRLOCK_UPDATE_NOTICE_FILE AIRLOCK_POLICY_HELPER AIRLOCK_SESSION_SNAPSHOT \
  AIRLOCK_SESSION_SNAPSHOT_SHA256 AIRLOCK_AGENT_DEPTH \
  AIRLOCK_FAST_TRANSITION_CHANNEL AIRLOCK_FAST_TRANSITION_NONCE \
  AIRLOCK_ACCESS_HELPER
# A Grok-rooted Airlock session exports its own worker pool, and the
# environment outranks the fixture config, so the suite would otherwise carry
# that session's Grok workers into profiles the fixture disables them for.
unset AIRLOCK_GROK_MODELS
# Build a legacy config without the saved-profile keys to verify that existing
# installations keep their original OpenAI-only bare command. The Anthropic
# pool stays at the pre-Haiku default so the sonnet fallback seat keeps
# coverage; the new default seat is asserted separately below.
while IFS= read -r line; do
  case "$line" in
    AIRLOCK_DEFAULT_PROFILE=*|AIRLOCK_HYBRID_MODEL=*|AIRLOCK_ANTHROPIC_MODELS=*) continue ;;
  esac
  printf '%s\n' "$line"
done < "$repo_root/config/airlock.conf.example" > "$tmp_dir/config"
printf 'AIRLOCK_ANTHROPIC_MODELS=opus,sonnet\n' >> "$tmp_dir/config"
cat > "$tmp_dir/access.json" <<'EOF'
{
  "schema_version": 1,
  "policies": {
    "extra_usage": "ask",
    "routing": "balanced",
    "allowed_efforts": {
      "anthropic": ["low", "medium", "high", "xhigh", "max"],
      "openai": ["low", "medium", "high", "xhigh", "max"]
    }
  },
  "providers": {
    "anthropic": {
      "authenticated": true,
      "detected_plan": "unknown",
      "plan_source": "not_exposed",
      "account_metadata": {"billing_type": "test", "extra_usage_enabled": true},
      "models": {
        "opus": {"access": "unknown"}, "sonnet": {"access": "unknown"},
        "fable": {"access": "unavailable"}, "haiku": {"access": "unavailable"}
      }
    },
    "openai": {
      "authenticated": true,
      "detected_plan": "test",
      "plan_source": "test",
      "account_metadata": {"account_type": "test"},
      "models": {
        "sol": {"access": "unknown"}, "terra": {"access": "unknown"},
        "luna": {"access": "unknown"}
      }
    },
    "grok": {
      "authenticated": true,
      "detected_plan": "unknown",
      "plan_source": "proxy_status",
      "account_metadata": {},
      "models": {
        "grok": {"access": "unavailable"}, "composer": {"access": "unavailable"}
      }
    }
  }
}
EOF
export AIRLOCK_CONFIG_FILE="$tmp_dir/config"
export AIRLOCK_ACCESS_FILE="$tmp_dir/access.json"
export AIRLOCK_OPENAI_DIRECT_AGENTS_FILE="$repo_root/config/openai-direct-agents.json"
export AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE="$repo_root/config/anthropic-direct-agents.json"
export AIRLOCK_HYBRID_AGENTS_FILE="$repo_root/config/hybrid-agents.json"
export AIRLOCK_CLAUDE_AGENTS_FILE="$repo_root/config/claude-agents.json"
export AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json"
export AIRLOCK_GROK_WRAPPER_AGENTS_FILE="$repo_root/config/grok-agents.json"
export AIRLOCK_PLUGIN_DIR="$repo_root/plugins/airlock"
export AIRLOCK_MANAGED_BUNDLE_FILE="$repo_root/config/managed-bundle.json"
export AIRLOCK_SESSION_RUNTIME_DIR="$tmp_dir/runtime"
unset AIRLOCK_ROUTING_POLICY AIRLOCK_EXTRA_USAGE_POLICY

fallback_home="$tmp_dir/fallback-home"
mkdir -p "$fallback_home/.airlock"
printf 'blocked default config parent\n' > "$fallback_home/.config"
cp "$tmp_dir/config" "$fallback_home/.airlock/config"
fallback_config_output="$(
  HOME="$fallback_home" \
  AIRLOCK_CONFIG_FILE='' AIRLOCK_CONFIG_DIR='' XDG_CONFIG_HOME='' \
    "$launcher" config
)"
grep -q "^Config file: $fallback_home/.airlock/config$" <<<"$fallback_config_output"

mode_config="$tmp_dir/mode.conf"
mode_access="$tmp_dir/mode-access.json"
printf '# preserve this comment\r\nUNRELATED=value\r\nAIRLOCK_ROUTING_POLICY=balanced\r\nAIRLOCK_EXTRA_USAGE_POLICY=ask\r\n' > "$mode_config"
mode_show_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode)"
grep -q '^Routing: balanced' <<<"$mode_show_output"
grep -q '^Extra usage: ask' <<<"$mode_show_output"
grep -q '^OpenAI Fast routes: off' <<<"$mode_show_output"
grep -q '^Anthropic Fast startup: off' <<<"$mode_show_output"
grep -q '^Preset: defaults$' <<<"$mode_show_output"
grep -q 'newly launched airlock sessions' <<<"$mode_show_output"
[[ ! -e "$mode_access" ]]

for routing in balanced economy quality; do
  mode_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode "$routing")"
  grep -q "^Routing: $routing" <<<"$mode_output"
  grep -q '^Airlock mode updated\.$' <<<"$mode_output"
done

extra_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode extra-usage allow)"
grep -q '^Extra usage: allow' <<<"$extra_output"
budget_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode budget)"
grep -q '^Routing: economy' <<<"$budget_output"
grep -q '^Extra usage: never' <<<"$budget_output"
grep -q '^OpenAI Fast routes: off' <<<"$budget_output"
grep -q '^Anthropic Fast startup: off' <<<"$budget_output"
grep -q '^Preset: budget$' <<<"$budget_output"
defaults_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode defaults)"
grep -q '^Routing: balanced' <<<"$defaults_output"
grep -q '^Extra usage: ask' <<<"$defaults_output"
grep -q '^OpenAI Fast routes: off' <<<"$defaults_output"
grep -q '^Anthropic Fast startup: off' <<<"$defaults_output"
fast_all_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode fast all)"
grep -q '^OpenAI Fast routes: on' <<<"$fast_all_output"
grep -q '^Anthropic Fast startup: on' <<<"$fast_all_output"
fast_openai_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode fast openai)"
grep -q '^OpenAI Fast routes: on' <<<"$fast_openai_output"
grep -q '^Anthropic Fast startup: off' <<<"$fast_openai_output"
anthropic_fast_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode anthropic-fast on)"
grep -q '^Anthropic Fast startup: on' <<<"$anthropic_fast_output"
openai_fast_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode openai-fast off)"
grep -q '^OpenAI Fast routes: off' <<<"$openai_fast_output"
set_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode set --routing quality --extra-usage allow --failover never --anthropic-rate-limit handoff --max-agents 3 --openai-fast on --anthropic-fast off --swarm-fast on)"
grep -q '^Routing: quality' <<<"$set_output"
grep -q '^Extra usage: allow' <<<"$set_output"
grep -q '^Failover: never' <<<"$set_output"
grep -q '^Anthropic rate limits: handoff' <<<"$set_output"
grep -q '^Max concurrent top-level subagents: 3' <<<"$set_output"
grep -q '^OpenAI Fast routes: on' <<<"$set_output"
grep -q '^Anthropic Fast startup: off' <<<"$set_output"
grep -q '^Luna swarm Fast selection: on' <<<"$set_output"
grep -q '^Agent depth: 1; named Agents cannot invoke Agent' <<<"$set_output"
if grep -Eq '^(Descendants:|Repair rounds:)' <<<"$set_output"; then
  printf 'test: native mode output retained legacy delegate settings\n' >&2
  exit 1
fi
max_off_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode max-agents off)"
grep -q '^Max concurrent top-level subagents: off' <<<"$max_off_output"
max_on_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode max-agents 3)"
grep -q '^Max concurrent top-level subagents: 3' <<<"$max_on_output"
python - "$mode_config" <<'PY'
from pathlib import Path
import sys
raw = Path(sys.argv[1]).read_bytes()
assert b"# preserve this comment\r\n" in raw
assert b"UNRELATED=value\r\n" in raw
PY
[[ ! -e "$mode_access" ]]

environment_output="$(AIRLOCK_ROUTING_POLICY=economy AIRLOCK_EXTRA_USAGE_POLICY=never AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode show)"
grep -q '^Routing: economy .*environment override=economy' <<<"$environment_output"
grep -q '^Extra usage: never .*environment override=never' <<<"$environment_output"
invalid_environment_output="$(AIRLOCK_ROUTING_POLICY=invalid AIRLOCK_EXTRA_USAGE_POLICY=invalid AIRLOCK_MAX_CONCURRENT_SUBAGENTS=99 AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode show)"
grep -q '^Routing: quality' <<<"$invalid_environment_output"
grep -q '^Extra usage: allow' <<<"$invalid_environment_output"
grep -q '^Ignored invalid environment override(s):' <<<"$invalid_environment_output"

cp "$mode_config" "$tmp_dir/mode-before-invalid.conf"
if AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode set --descendants bounded >/dev/null 2>&1; then
  printf 'test: legacy descendant mode unexpectedly succeeded\n' >&2
  exit 1
fi
cmp "$mode_config" "$tmp_dir/mode-before-invalid.conf"
if AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode set >/dev/null 2>&1; then
  printf 'test: empty mode set unexpectedly succeeded\n' >&2
  exit 1
fi
cmp "$mode_config" "$tmp_dir/mode-before-invalid.conf"
if AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode set --routing invalid >/dev/null 2>&1; then
  printf 'test: invalid mode value unexpectedly succeeded\n' >&2
  exit 1
fi
cmp "$mode_config" "$tmp_dir/mode-before-invalid.conf"
if AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode fast invalid >/dev/null 2>&1; then
  printf 'test: invalid provider Fast selection unexpectedly succeeded\n' >&2
  exit 1
fi
cmp "$mode_config" "$tmp_dir/mode-before-invalid.conf"

missing_mode_config="$tmp_dir/missing/config"
missing_output="$(AIRLOCK_CONFIG_FILE="$missing_mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode budget)"
grep -q '^Preset: budget$' <<<"$missing_output"
grep -q '^# Managed by https://github.com/Harshkamdar67/Airlock$' "$missing_mode_config"
grep -q '^AIRLOCK_ROUTING_POLICY=economy$' "$missing_mode_config"
grep -q '^AIRLOCK_EXTRA_USAGE_POLICY=never$' "$missing_mode_config"

bundle_output="$("$launcher" bundle)"
grep -q '^Managed bundle is current and complete\.$' <<<"$bundle_output"

version_output="$("$launcher" version)"
IFS= read -r expected_version < "$repo_root/VERSION"
expected_version="${expected_version%$'\r'}"
if [[ "$version_output" != "Airlock $expected_version" ]]; then
  printf 'test: version output did not match VERSION\n' >&2
  exit 1
fi
update_help_output="$("$launcher" update --help)"
grep -q '^usage: airlock update' <<<"$update_help_output"

# Exercise OpenRouter launcher dispatch with a helper that cannot access a key
# or the network. Registry argument forwarding is part of the command contract.
cat > "$tmp_dir/openrouter-models-stub.py" <<'PY'
import sys
print("MOCK_OPENROUTER_MODELS=" + "|".join(sys.argv[1:]))
PY
openrouter_registry="$tmp_dir/openrouter-registry.json"
openrouter_models_output="$(
  AIRLOCK_OPENROUTER_MODELS_HELPER="$tmp_dir/openrouter-models-stub.py" \
  AIRLOCK_OPENROUTER_REGISTRY_FILE="$openrouter_registry" \
    "$launcher" openrouter models list
)"
grep -q '^MOCK_OPENROUTER_MODELS=--registry|.*|list$' <<<"$openrouter_models_output"
openrouter_presets_output="$(
  AIRLOCK_OPENROUTER_MODELS_HELPER="$tmp_dir/openrouter-models-stub.py" \
  AIRLOCK_OPENROUTER_REGISTRY_FILE="$openrouter_registry" \
    "$launcher" openrouter models presets
)"
grep -q '^MOCK_OPENROUTER_MODELS=--registry|.*|presets$' <<<"$openrouter_presets_output"
openrouter_add_preset_output="$(
  AIRLOCK_OPENROUTER_MODELS_HELPER="$tmp_dir/openrouter-models-stub.py" \
  AIRLOCK_OPENROUTER_REGISTRY_FILE="$openrouter_registry" \
    "$launcher" openrouter models add-preset kimi-k3 --yes
)"
grep -q '^MOCK_OPENROUTER_MODELS=--registry|.*|add-preset|kimi-k3|--yes$' <<<"$openrouter_add_preset_output"
models_help_output="$("$launcher" models)"
grep -q 'airlock openrouter models presets' <<<"$models_help_output"
grep -q 'airlock openrouter models add-preset' <<<"$models_help_output"
opr_help_output="$("$launcher" opr --help)"
opr_short_help_output="$("$launcher" opr -h)"
grep -q '^Usage: airlock opr \[ROUTE\] \[Claude arguments\.\.\.\]$' <<<"$opr_help_output"
[[ "$opr_short_help_output" == "$opr_help_output" ]]
grep -q 'offline registry' <<<"$opr_help_output"
if grep -q 'airlock orp' <<<"$opr_help_output"; then
  printf 'test: OPR help still advertised the old ORP command\n' >&2
  exit 1
fi
if "$launcher" openrouter unsupported >"$tmp_dir/openrouter-invalid.out" 2>"$tmp_dir/openrouter-invalid.err"; then
  printf 'test: launcher accepted an unsupported OpenRouter command\n' >&2
  exit 1
else
  openrouter_invalid_status=$?
fi
[[ "$openrouter_invalid_status" -eq 2 ]]
grep -q '^airlock: usage: airlock openrouter auth|models \.\.\.$' "$tmp_dir/openrouter-invalid.err"

if "$launcher" version unexpected >/dev/null 2>&1; then
  printf 'test: version command accepted an unexpected argument\n' >&2
  exit 1
fi
if "$launcher" update --check --yes >/dev/null 2>&1; then
  printf 'test: update command accepted conflicting arguments\n' >&2
  exit 1
fi
if AIRLOCK_ACTIVE_PROFILE=openai-pure "$launcher" update --yes >"$tmp_dir/update-active.out" 2>"$tmp_dir/update-active.err"; then
  printf 'test: update installed from an active Airlock session\n' >&2
  exit 1
fi
grep -q 'installation cannot run from an active Airlock session' "$tmp_dir/update-active.err"

models_output="$("$launcher" models)"
for removed in 'airlock delegate' 'airlock workflow'; do
  if grep -qF "$removed" <<<"$models_output"; then
    printf 'test: help still advertises removed subcommand: %s\n' "$removed" >&2
    exit 1
  fi
done
for removed in airlock-delegate airlock-workflow airlock-child airlock-check airlock_runtime; do
  if [[ -e "$repo_root/bin/$removed" || -e "$repo_root/bin/$removed.py" ]]; then
    printf 'test: legacy transport file still present: %s\n' "$removed" >&2
    exit 1
  fi
done
python - "$repo_root/config/managed-bundle.json" "$tmp_dir/stale-bundle.json" <<'PY'
import json
from pathlib import Path
import sys
value = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
value['bundle_version'] = 'old'
Path(sys.argv[2]).write_text(json.dumps(value), encoding='utf-8')
PY
if AIRLOCK_MANAGED_BUNDLE_FILE="$tmp_dir/stale-bundle.json" "$launcher" bundle >"$tmp_dir/stale.out" 2>"$tmp_dir/stale.err"; then
  printf 'test: stale managed bundle unexpectedly succeeded\n' >&2
  exit 1
fi
grep -q 'managed bundle is stale' "$tmp_dir/stale.err"
grep -q 'Reinstall Airlock, then start a fresh session' "$tmp_dir/stale.err"

usage_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" usage set --claude-plan max5x --openai-capacity 20x)"
grep -q '^Airlock usage preferences updated\.$' <<<"$usage_output"
grep -q 'configured-tier=max5x; effective-tier=max5x (source=user_override)' <<<"$usage_output"
grep -q 'capacity=20x (source=user_override' <<<"$usage_output"
usage_cached_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" AIRLOCK_ACCESS_CODEX="$tmp_dir/should-not-run" "$launcher" usage)"
grep -q '^Airlock subscription usage (sanitized)$' <<<"$usage_cached_output"
grep -q '^Refresh: failed; showing the last cached snapshot\.$' <<<"$usage_cached_output"
grep -q 'use native Claude `/usage`' <<<"$usage_cached_output"
[[ ! -e "$tmp_dir/should-not-run" ]]
usage_defaults_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" usage defaults)"
grep -q 'configured-tier=unknown; effective-tier=unknown (source=unknown)' <<<"$usage_defaults_output"
if grep -q '^AIRLOCK_\(ANTHROPIC_PLAN\|OPENAI_CAPACITY\)=' "$mode_config"; then
  printf 'test: usage defaults did not clear overrides\n' >&2
  exit 1
fi

symlink_target="$tmp_dir/symlink-target.conf"
symlink_config="$tmp_dir/symlink-config.conf"
printf 'AIRLOCK_ROUTING_POLICY=balanced\n' > "$symlink_target"
if ln -s "$symlink_target" "$symlink_config" 2>/dev/null && [[ -L "$symlink_config" ]]; then
  if AIRLOCK_CONFIG_FILE="$symlink_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode quality >/dev/null 2>&1; then
    printf 'test: symlinked mode config unexpectedly replaced\n' >&2
    exit 1
  fi
  grep -q '^AIRLOCK_ROUTING_POLICY=balanced$' "$symlink_target"
fi

if command -v powershell.exe >/dev/null 2>&1 && command -v cygpath >/dev/null 2>&1; then
  powershell_launcher="$(cygpath -w "$repo_root/bin/airlock.ps1")"
  powershell_helper="$(cygpath -w "$repo_root/bin/airlock-access.py")"
  powershell_config="$(cygpath -w "$tmp_dir/powershell-mode.conf")"
  powershell_access="$(cygpath -w "$tmp_dir/powershell-mode-access.json")"
  powershell_output="$(AIRLOCK_CONFIG_FILE="$powershell_config" AIRLOCK_ACCESS_FILE="$powershell_access" AIRLOCK_ACCESS_HELPER="$powershell_helper" powershell.exe -NoProfile -NonInteractive -File "$powershell_launcher" mode set --routing economy --extra-usage never --failover never --anthropic-rate-limit handoff --max-agents 3 --openai-fast on --anthropic-fast off --swarm-fast off)"
  grep -q '^Routing: economy' <<<"$powershell_output"
  grep -q '^Extra usage: never' <<<"$powershell_output"
  grep -q '^Failover: never' <<<"$powershell_output"
  grep -q '^Anthropic rate limits: handoff' <<<"$powershell_output"
  grep -q '^Max concurrent top-level subagents: 3' <<<"$powershell_output"
  grep -q '^OpenAI Fast routes: on' <<<"$powershell_output"
  grep -q '^Anthropic Fast startup: off' <<<"$powershell_output"
  grep -q '^Luna swarm Fast selection: off' <<<"$powershell_output"
  grep -q '^Agent depth: 1; named Agents cannot invoke Agent' <<<"$powershell_output"
  powershell_usage="$(AIRLOCK_CONFIG_FILE="$powershell_config" AIRLOCK_ACCESS_FILE="$powershell_access" AIRLOCK_ACCESS_HELPER="$powershell_helper" powershell.exe -NoProfile -NonInteractive -File "$powershell_launcher" usage set --claude-plan pro --openai-capacity 5x)"
  grep -q 'configured-tier=pro; effective-tier=pro (source=user_override)' <<<"$powershell_usage"

  printf 'import json, os, sys\nif "custom-models" in sys.argv[1:]:\n    print("[]")\nelse:\n    print("PYTHON_BIN=" + os.environ.get("AIRLOCK_PYTHON", "unset"))\n' > "$tmp_dir/powershell-python-env.py"
  powershell_env_helper="$(cygpath -w "$tmp_dir/powershell-python-env.py")"
  powershell_python_output="$(AIRLOCK_ACCESS_HELPER="$powershell_env_helper" powershell.exe -NoProfile -NonInteractive -File "$powershell_launcher" access show)"
  if grep -q '^PYTHON_BIN=unset$' <<<"$powershell_python_output"; then
    printf 'test: PowerShell launcher did not export its verified Python interpreter\n' >&2
    exit 1
  fi

  printf '@exit /b 49\r\n' > "$tmp_dir/not-python.cmd"
  powershell_invalid_python="$(cygpath -w "$tmp_dir/not-python.cmd")"
  if AIRLOCK_PYTHON="$powershell_invalid_python" AIRLOCK_ACCESS_HELPER="$powershell_env_helper" powershell.exe -NoProfile -NonInteractive -File "$powershell_launcher" access show >/dev/null 2>&1; then
    printf 'test: PowerShell launcher accepted an invalid explicit AIRLOCK_PYTHON\n' >&2
    exit 1
  fi
fi

normal_output="$(CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
stdin_stub="$tmp_dir/stdin-claude-stub.sh"
cat > "$stdin_stub" <<'EOF'
#!/usr/bin/env bash
IFS= read -r line || {
  printf 'stdin stub: launcher passed end-of-file instead of its input\n' >&2
  exit 67
}
printf 'STDIN_LINE=%s\n' "$line"
EOF
chmod +x "$stdin_stub"
stdin_output="$(
  printf 'preserved input\n' |
    AIRLOCK_REAL_CLAUDE="$stdin_stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
      "$launcher" terra -p test
)"
grep -q '^STDIN_LINE=preserved input$' <<<"$stdin_output"
closed_stdin_stub="$tmp_dir/closed-stdin-claude-stub.sh"
cat > "$closed_stdin_stub" <<'EOF'
#!/usr/bin/env bash
if IFS= read -r line; then
  printf 'closed stdin stub: unexpectedly read input: %s\n' "$line" >&2
  exit 68
fi
printf 'CLOSED_STDIN=EOF\n'
EOF
chmod +x "$closed_stdin_stub"
closed_stdin_output="$(
  AIRLOCK_REAL_CLAUDE="$closed_stdin_stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
    "$launcher" terra -p test 0<&-
)"
grep -q '^CLOSED_STDIN=EOF$' <<<"$closed_stdin_output"
grep -Fq 'exec 9<&0' "$launcher"
grep -Fq 'exec 9</dev/null' "$launcher"
grep -Fq '"$@" <&9 9<&- &' "$launcher"
notice_config_root="$tmp_dir/notice-config"
notice_output="$(AIRLOCK_CONFIG_DIR="$notice_config_root" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q "^UPDATE_NOTICE=$notice_config_root/update-notice.json$" <<<"$notice_output"
relative_notice_output="$(AIRLOCK_CONFIG_DIR='relative-notice-config' AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q "^UPDATE_NOTICE=$PWD/relative-notice-config/update-notice.json$" <<<"$relative_notice_output"
grep -q '^MODEL=gpt-5.6-sol$' <<<"$normal_output"
grep -q '^SMALL_FAST=gpt-5.6-luna$' <<<"$normal_output"
grep -q '^EFFORT_ENV=unset$' <<<"$normal_output"
# The classifier rides the small fast seat (luna), not the premium sol root.
grep -q '^AUTO_MODE_MODEL=gpt-5.6-luna$' <<<"$normal_output"
grep -q '^ALWAYS_EFFORT=1$' <<<"$normal_output"
grep -q '^ARG=high$' <<<"$normal_output"
grep -q '^OPUS_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^SONNET_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^HAIKU_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^CUSTOM_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^POLICY_HELPER=.*airlock_policy\.py$' <<<"$normal_output"
grep -q '^SNAPSHOT_SHA256=[0-9a-f]\{64\}$' <<<"$normal_output"
grep -q '^SNAPSHOT_EXISTS=yes$' <<<"$normal_output"
normal_snapshot="$(grep '^SESSION_SNAPSHOT=' <<<"$normal_output" | cut -d= -f2-)"
if [[ -z "$normal_snapshot" || -e "$normal_snapshot" ]]; then
  printf 'test: pure-session snapshot was not removed after Claude exited\n' >&2
  exit 1
fi

background_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" bg -p test)"
grep -q '^MODEL=gpt-5.6-sol$' <<<"$background_output"
grep -q '^ARG=medium$' <<<"$background_output"

override_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" bg --effort high -p test)"
if grep -q '^ARG=medium$' <<<"$override_output"; then
  printf 'test: explicit effort was not respected\n' >&2
  exit 1
fi
grep -q '^ARG=high$' <<<"$override_output"

terra_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" terra -p test)"
grep -q '^MODEL=gpt-5.6-terra$' <<<"$terra_output"

custom_config="$repo_root/tests/fixtures/custom.conf"
configured_output="$(AIRLOCK_CONFIG_FILE="$custom_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q '^MODEL=gpt-5.6-terra$' <<<"$configured_output"
grep -q '^SMALL_FAST=gpt-5.4-mini$' <<<"$configured_output"
# A configured small fast model becomes the classifier seat too.
grep -q '^AUTO_MODE_MODEL=gpt-5.4-mini$' <<<"$configured_output"
grep -q '^ARG=high$' <<<"$configured_output"
# The saved OpenAI fallback remains conservative until a >300k proof passes.
grep -q '^COMPACT_WINDOW=200000$' <<<"$configured_output"

configured_bg_output="$(AIRLOCK_CONFIG_FILE="$custom_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" bg -p test)"
grep -q '^MODEL=gpt-5.6-luna$' <<<"$configured_bg_output"
grep -q '^ARG=low$' <<<"$configured_bg_output"

config_output="$(AIRLOCK_CONFIG_FILE="$custom_config" "$launcher" config)"
grep -q '^Default profile: openai$' <<<"$config_output"
grep -q '^Context window: 200000 (saved fallback for OpenAI and Grok roots)$' <<<"$config_output"
grep -q '^Default command: airlock -> GPT-5.6 Terra (gpt-5.6-terra)$' <<<"$config_output"
grep -q '^Hybrid root: Claude Sonnet 5 (claude-sonnet-5\[1m\])$' <<<"$config_output"
grep -q '^OpenAI root: GPT-5.6 Terra (gpt-5.6-terra)$' <<<"$config_output"
grep -q '^Background command: airlock bg -> GPT-5.6 Luna (gpt-5.6-luna) / low effort$' <<<"$config_output"
config_alias_output="$(AIRLOCK_CONFIG_FILE="$custom_config" "$launcher" --config)"
grep -q '^Default profile: openai$' <<<"$config_alias_output"
if grep -q '^Worker descendants:' <<<"$config_output"; then
  printf 'test: config output retained broker-era descendant status\n' >&2
  exit 1
fi

pure_profile="$normal_output" python - <<'PY'
import base64
import json
import os
lines = os.environ["pure_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings_line_value = next(
    line[len("SETTINGS_JSON="):] for line in lines
    if line.startswith("SETTINGS_JSON=") and line != "SETTINGS_JSON=unreadable"
)
settings = json.loads(settings_line_value)
assert settings["autoMode"]["environment"][0] == "$defaults"
trust_context = settings["autoMode"]["environment"][1]
assert "OpenAI models" in trust_context and "Anthropic Claude" not in trust_context
assert "eligible non-ignored untracked regular files" in trust_context
assert "built-in Explore, Plan, and general-purpose Agent types" in trust_context
assert "Git-ignored or unsafe paths" in trust_context
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-sol", "airlock-terra", "airlock-luna"}
assert agents["airlock-sol"]["model"] == "gpt-5.6-sol"
assert agents["airlock-terra"]["model"] == "gpt-5.6-terra"
assert agents["airlock-luna"]["model"] == "gpt-5.6-luna"
assert all("effort" not in agent for agent in agents.values())
assert all("inherits the session level" in agent["description"] for agent in agents.values())
assert all(agent["disallowedTools"] == ["Agent"] for agent in agents.values())
assert all("tools" not in agent and "permissionMode" not in agent for agent in agents.values())
assert all("native Claude Code tools" in agent["prompt"] for agent in agents.values())
assert all("airlock-delegate" not in agent["prompt"] for agent in agents.values())
assert all("airlock-workflow" not in agent["prompt"] for agent in agents.values())
assert all("transport wrapper" not in agent["prompt"] for agent in agents.values())
assert all("Delegated task kind" not in agent["prompt"] for agent in agents.values())
assert all("result.report" not in agent["prompt"] for agent in agents.values())
assert "use run_in_background=true" in agents["airlock-luna"]["description"]
assert "useful non-overlapping batch before waiting" in agents["airlock-luna"]["description"]
assert "automatic Luna army" not in agents["airlock-sol"]["description"]
assert "--plugin-dir" in args
assert args[args.index("--plugin-dir") + 1].replace("\\", "/").endswith("/plugins/airlock")
assert "--allowedTools" in args
start = args.index("--allowedTools") + 1
allowed = []
for value in args[start:]:
    if value.startswith("--"):
        break
    allowed.append(value)
expected_allowed = ["Agent(Explore)", "Agent(Plan)", "Agent(general-purpose)", *[f"Agent({name})" for name in sorted(agents)]]
assert allowed == expected_allowed, (allowed, expected_allowed)
assert "Agent" not in allowed and "Agent(*)" not in allowed and "Agent(airlock-*)" not in allowed
assert "Bash(airlock-delegate *)" not in allowed and "Bash(airlock-workflow *)" not in allowed
assert "Skill(claude-api)" in args
assert "Skill(claude-api *)" in args
guidance = base64.b64decode(next(
    line[len("APPEND_SYSTEM_PROMPT_B64="):] for line in lines
    if line.startswith("APPEND_SYSTEM_PROMPT_B64=")
)).decode("utf-8")
assert "claude-api skill is blocked" in guidance
assert "Work directly" in guidance and "Built-in Explore, Plan, and general-purpose accept Claude Code's" in guidance
assert "pass `model=haiku` (resolved to gpt-5.6-luna)" in guidance and "Omit `model` only when inheriting the orchestrator" in guidance
assert "Use built-in Plan" in guidance
assert "Use built-in general-purpose" in guidance and "For an automatic army, launch multiple exact airlock-luna" in guidance
assert "Agent calls with `run_in_background: true`" in guidance
assert "useful non-overlapping batch before waiting" in guidance
assert "They run at the session effort unless they are pinned" in guidance
assert "Use Claude Code Workflow only when the user explicitly requests" in guidance
assert "Claude Code's Agent card, model identity, usage" in guidance
assert "Native Agent results:" in guidance and "Preserve the full technical result" in guidance
assert "semantically complete" in guidance and "result.report" not in guidance
assert "User communication:" in guidance and "exact-output or machine-readable contract" in guidance
assert "files and behavior changed" in guidance and "skipped checks and reasons" in guidance
assert "OpenAI-only profile has no Anthropic worker" in guidance
PY
grep -q '^OPENAI_BRIDGE=unset$' <<<"$normal_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$normal_output"
grep -q '^ACTIVE_PROFILE=openai-pure$' <<<"$normal_output"
grep -q '^ROOT_MODEL=gpt-5.6-sol$' <<<"$normal_output"
grep -q '^DISCOVERY_MODEL=gpt-5.6-luna$' <<<"$normal_output"
grep -q '^SESSION_ROUTER=unset$' <<<"$normal_output"
grep -q '^ALLOWED_AGENTS=airlock-luna,airlock-sol,airlock-terra$' <<<"$normal_output"
grep -q '^ALLOWED_MODELS=gpt-5.6-luna,gpt-5.6-sol,gpt-5.6-terra$' <<<"$normal_output"
grep -q '^EXTRA_AGENTS=$' <<<"$normal_output"
grep -q '^EXTRA_MODELS=$' <<<"$normal_output"
grep -q '^AUTH_TOKEN_SET=yes$' <<<"$normal_output"
grep -q '^DEFAULT_FABLE=gpt-5.6-sol$' <<<"$normal_output"
grep -q '^DEFAULT_OPUS=gpt-5.6-sol$' <<<"$normal_output"
grep -q '^DEFAULT_SONNET=gpt-5.6-terra$' <<<"$normal_output"
grep -q '^DEFAULT_HAIKU=gpt-5.6-luna$' <<<"$normal_output"
grep -q '^FABLE_NAME=gpt-5.6-sol$' <<<"$normal_output"
grep -q '^OPUS_NAME=gpt-5.6-sol$' <<<"$normal_output"
grep -q '^SONNET_NAME=gpt-5.6-terra$' <<<"$normal_output"
grep -q '^HAIKU_NAME=gpt-5.6-luna$' <<<"$normal_output"
grep -q '^FABLE_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^MAX_SUBAGENTS=unset$' <<<"$normal_output"
grep -q '^SUBAGENT_MODEL=unset$' <<<"$normal_output"
grep -q '^SPAWN_DEPTH=1$' <<<"$normal_output"

custom_prompt_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" --append-system-prompt 'first custom instruction' --append-system-prompt='second custom instruction' -p test)"
custom_prompt_profile="$custom_prompt_output" python - <<'PY'
import base64
import json
import os
from pathlib import Path
lines = os.environ["custom_prompt_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
assert args.count("--append-system-prompt-file") == 1
prompt = Path(args[args.index("--append-system-prompt-file") + 1])
assert prompt.name.startswith("guidance-") and prompt.suffix == ".txt"
prompt_text = base64.b64decode(next(
    line[len("APPEND_SYSTEM_PROMPT_B64="):] for line in lines
    if line.startswith("APPEND_SYSTEM_PROMPT_B64=")
)).decode("utf-8")
assert prompt_text.startswith("first custom instruction\n\nsecond custom instruction\n\n")
assert prompt_text.endswith("The claude-api skill is blocked by default because its large attachment can overflow an Agent context during model routing. Native routing through Airlock is not Claude API application development; do not retry that skill unless the session was launched with AIRLOCK_ALLOW_CLAUDE_API_SKILL=1.")
assert "-p" in args and "--append-system-prompt-file" in args
PY
if AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" --append-system-prompt >/dev/null 2>&1; then
  printf 'test: missing appended-system-prompt value unexpectedly launched Claude\n' >&2
  exit 1
fi

if grep -q '^PYTHON_BIN=unset$' <<<"$normal_output"; then
  printf 'test: launcher did not export its verified Python interpreter\n' >&2
  exit 1
fi

invalid_python="$tmp_dir/not-python"
printf '#!/usr/bin/env bash\nexit 49\n' > "$invalid_python"
chmod +x "$invalid_python"
if AIRLOCK_PYTHON="$invalid_python" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test >/dev/null 2>&1; then
  printf 'test: invalid explicit AIRLOCK_PYTHON unexpectedly launched Claude\n' >&2
  exit 1
fi

off_output="$(AIRLOCK_MAX_CONCURRENT_SUBAGENTS=off AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q '^MAX_SUBAGENTS=unset$' <<<"$off_output"
grep -q '^SPAWN_DEPTH=1$' <<<"$off_output"
for protected in --safe-mode --bare --agent --agents --plugin-dir --plugin-url --settings; do
  if AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" "$protected" blocked >/dev/null 2>&1; then
    printf 'test: protected option %s unexpectedly passed through\n' "$protected" >&2
    exit 1
  fi
done
for unsafe_env in CLAUDE_CODE_SAFE_MODE CLAUDE_CODE_SIMPLE; do
  if env "$unsafe_env=1" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test >/dev/null 2>&1; then
    printf 'test: unsafe environment %s unexpectedly passed through\n' "$unsafe_env" >&2
    exit 1
  fi
done

opr_registry="$tmp_dir/opr-private/openrouter-registry.json"
python - "$repo_root/bin" "$opr_registry" <<'PY'
import sys
import time
sys.path.insert(0, sys.argv[1])
import airlock_policy as policy
policy.write_openrouter_registry(sys.argv[2], {
    "schema_version": 1,
    "models": [
        {
            "route": "kimi-k3",
            "model": "moonshotai/kimi-k3",
            "endpoint_provider": "digitalocean",
            "provider_name": "DigitalOcean",
            "provider_slug": "digitalocean",
            "quantization": "unknown",
            "canonical_slug": "moonshotai/kimi-k3-20260715",
            "alias_target": None,
            "supported_parameters": ["tool_choice", "tools"],
            "expiration_date": None,
            "checked_at": int(time.time()),
            "enabled": True,
        },
        {
            "route": "deepseek-v4-flash-0731",
            "model": "deepseek/deepseek-v4-flash-0731",
            "endpoint_provider": "deepinfra/fp4",
            "provider_name": "DeepInfra",
            "provider_slug": "deepinfra",
            "quantization": "fp4",
            "canonical_slug": "deepseek/deepseek-v4-flash-0731",
            "alias_target": None,
            "supported_parameters": ["tool_choice", "tools"],
            "expiration_date": None,
            "checked_at": int(time.time()),
            "enabled": True,
        },
    ],
})
PY
opr_router="$tmp_dir/opr-router.py"
cat > "$opr_router" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path
import sys
if len(sys.argv) < 2 or sys.argv[1] != "start" or "--openai-url" in sys.argv:
    raise SystemExit(71)
marker = os.environ.get("AIRLOCK_TEST_ROUTER_MARKER")
if marker:
    Path(marker).write_text("started", encoding="utf-8")
print("http://127.0.0.1:28472")
PY
chmod +x "$opr_router"
opr_bundle="$tmp_dir/opr-managed-bundle.json"
python - "$repo_root/config/managed-bundle.json" "$opr_bundle" "$opr_router" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
bundle = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
bundle["components"]["bin/airlock-router.py"] = hashlib.sha256(
    Path(sys.argv[3]).read_bytes()
).hexdigest()
Path(sys.argv[2]).write_text(json.dumps(bundle), encoding="utf-8")
PY
legacy_opr_access_stub="$tmp_dir/legacy-opr-access-stub.py"
cat > "$legacy_opr_access_stub" <<'PY'
import os
from pathlib import Path
marker = os.environ.get("AIRLOCK_TEST_ACCESS_MARKER")
if marker:
    Path(marker).write_text("called", encoding="utf-8")
raise SystemExit(79)
PY
for saved_profile in hybrid grok; do
  saved_profile_config="$tmp_dir/opr-$saved_profile.conf"
  cp "$tmp_dir/config" "$saved_profile_config"
  printf 'AIRLOCK_DEFAULT_PROFILE=%s\n' "$saved_profile" >> "$saved_profile_config"
  saved_profile_help="$(AIRLOCK_CONFIG_FILE="$saved_profile_config" "$launcher" opr --help)"
  grep -q '^Usage: airlock opr ' <<<"$saved_profile_help"
  legacy_stdout="$tmp_dir/orp-$saved_profile.out"
  legacy_stderr="$tmp_dir/orp-$saved_profile.err"
  legacy_access_marker="$tmp_dir/orp-$saved_profile-access.marker"
  legacy_router_marker="$tmp_dir/orp-$saved_profile-router.marker"
  if AIRLOCK_CONFIG_FILE="$saved_profile_config" \
    AIRLOCK_ACCESS_HELPER="$legacy_opr_access_stub" \
    AIRLOCK_TEST_ACCESS_MARKER="$legacy_access_marker" \
    AIRLOCK_OPENROUTER_REGISTRY_FILE="$opr_registry" \
    AIRLOCK_MANAGED_BUNDLE_FILE="$opr_bundle" \
    AIRLOCK_ROUTER_HELPER="$opr_router" \
    AIRLOCK_TEST_ROUTER_MARKER="$legacy_router_marker" \
    AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
      "$launcher" orp kimi-k3 -r >"$legacy_stdout" 2>"$legacy_stderr"; then
    printf 'test: legacy ORP command unexpectedly succeeded under %s default\n' "$saved_profile" >&2
    exit 1
  else
    legacy_status=$?
  fi
  [[ "$legacy_status" -eq 2 ]]
  [[ ! -s "$legacy_stdout" ]]
  [[ ! -e "$legacy_access_marker" ]]
  [[ ! -e "$legacy_router_marker" ]]
  grep -q "^airlock: command 'orp' was renamed to 'opr'; use airlock opr\.$" "$legacy_stderr"
  saved_profile_output="$(
    ANTHROPIC_API_KEY=synthetic OPENAI_API_KEY=synthetic GROK_API_KEY=synthetic \
    OPENROUTER_API_KEY=synthetic CLAUDE_CODE_OAUTH_TOKEN=synthetic \
    AIRLOCK_CONFIG_FILE="$saved_profile_config" \
    AIRLOCK_ACCESS_HELPER="$repo_root/bin/airlock-access.py" \
    AIRLOCK_OPENROUTER_REGISTRY_FILE="$opr_registry" \
    AIRLOCK_MANAGED_BUNDLE_FILE="$opr_bundle" \
    AIRLOCK_ROUTER_HELPER="$opr_router" AIRLOCK_REAL_CLAUDE="$stub" \
      "$launcher" opr kimi-k3 -r -p morpheus-corp
  )"
  grep -q '^ACTIVE_PROFILE=openrouter-pure$' <<<"$saved_profile_output"
  grep -q '^MODEL=moonshotai/kimi-k3$' <<<"$saved_profile_output"
  grep -q '^ARG=-r$' <<<"$saved_profile_output"
  grep -q '^ARG=morpheus-corp$' <<<"$saved_profile_output"
  if grep -q "renamed to 'opr'" <<<"$saved_profile_output"; then
    printf 'test: OPR arguments containing orp triggered legacy-command rejection\n' >&2
    exit 1
  fi
done
opr_output="$(
  ANTHROPIC_API_KEY=synthetic OPENAI_API_KEY=synthetic GROK_API_KEY=synthetic \
  OPENROUTER_API_KEY=synthetic CLAUDE_CODE_OAUTH_TOKEN=synthetic \
  AIRLOCK_OPENROUTER_REGISTRY_FILE="$opr_registry" \
  AIRLOCK_MANAGED_BUNDLE_FILE="$opr_bundle" \
  AIRLOCK_ROUTER_HELPER="$opr_router" AIRLOCK_REAL_CLAUDE="$stub" \
    "$launcher" opr kimi-k3 -r --effort high
)"
grep -q '^ACTIVE_PROFILE=openrouter-pure$' <<<"$opr_output"
grep -q '^MODEL=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^ROOT_MODEL=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^DISCOVERY_MODEL=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^SESSION_ROUTER=http://127.0.0.1:28472$' <<<"$opr_output"
grep -q '^DEFAULT_FABLE=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^DEFAULT_OPUS=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^DEFAULT_SONNET=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^DEFAULT_HAIKU=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^SMALL_FAST=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^FABLE_CAPS=effort,xhigh_effort,max_effort$' <<<"$opr_output"
grep -q '^OPUS_CAPS=effort,xhigh_effort,max_effort$' <<<"$opr_output"
grep -q '^SONNET_CAPS=effort,xhigh_effort,max_effort$' <<<"$opr_output"
grep -q '^HAIKU_CAPS=effort,xhigh_effort,max_effort$' <<<"$opr_output"
grep -q '^CUSTOM_CAPS=effort,xhigh_effort,max_effort$' <<<"$opr_output"
grep -q '^ALWAYS_EFFORT=1$' <<<"$opr_output"
grep -q '^AUTO_MODE_MODEL=moonshotai/kimi-k3$' <<<"$opr_output"
grep -q '^COMPACT_WINDOW=unset$' <<<"$opr_output"
grep -q '^ARG=-r$' <<<"$opr_output"
grep -q '^ARG=high$' <<<"$opr_output"
opr_profile="$opr_output" python - <<'PY'
import base64
import json
import os
lines = os.environ["opr_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-or-kimi-k3", "airlock-or-deepseek-v4-flash-0731"}
assert agents["airlock-or-kimi-k3"]["model"] == "moonshotai/kimi-k3"
assert "explicitly selected route for normal root traffic" in agents["airlock-or-kimi-k3"]["description"]
assert "extra-usage authorization" in agents["airlock-or-deepseek-v4-flash-0731"]["description"]
assert args.count("--model") == 1
assert args[args.index("--model") + 1] == "moonshotai/kimi-k3"
assert "-r" in args
# The OpenRouter-only root receives the web tools server through a real
# --mcp-config file, and the launcher removes that file after Claude exits.
assert args.count("--mcp-config") == 1
from pathlib import Path
mcp_arg = Path(args[args.index("--mcp-config") + 1])
assert mcp_arg.name.startswith("airlock-mcp-") and mcp_arg.suffix == ".json"
assert mcp_arg.exists() is False
PY
opr_mcp_path="$(awk 'found {sub(/^ARG=/, ""); print; exit} /^ARG=--mcp-config$/ {found=1}' <<<"$opr_output")"
if [[ -z "$opr_mcp_path" || -e "$opr_mcp_path" ]]; then
  printf 'test: OpenRouter-only MCP config path missing or not cleaned up\n' >&2
  exit 1
fi
grep -q '^MCP_SERVERS=airlock-web-tools$' <<<"$opr_output"
opr_snapshot="$(grep '^SESSION_SNAPSHOT=' <<<"$opr_output" | cut -d= -f2-)"
if [[ -z "$opr_snapshot" || -e "$opr_snapshot" ]]; then
  printf 'test: OpenRouter-only snapshot was not removed after Claude exited\n' >&2
  exit 1
fi
for override in '--model=moonshotai/kimi-k3' '--model' '-m'; do
  if AIRLOCK_OPENROUTER_REGISTRY_FILE="$opr_registry" "$launcher" opr kimi-k3 "$override" blocked >/dev/null 2>&1; then
    printf 'test: OpenRouter-only launch accepted model override %s\n' "$override" >&2
    exit 1
  fi
done
if AIRLOCK_OPENROUTER_REGISTRY_FILE="$opr_registry" "$launcher" opr missing-route -p test >/dev/null 2>"$tmp_dir/opr-unknown.err"; then
  printf 'test: OpenRouter-only launch accepted an unknown route\n' >&2
  exit 1
fi
grep -q 'unknown or disabled OpenRouter route' "$tmp_dir/opr-unknown.err"
if AIRLOCK_OPENROUTER_REGISTRY_FILE="$opr_registry" "$launcher" opr -r >/dev/null 2>"$tmp_dir/opr-missing.err"; then
  printf 'test: noninteractive OpenRouter-only launch accepted a missing route\n' >&2
  exit 1
fi
grep -q 'OpenRouter route is required outside an interactive terminal' "$tmp_dir/opr-missing.err"
if AIRLOCK_OPENROUTER_REGISTRY_FILE="$opr_registry" "$launcher" opr </dev/null >/dev/null 2>"$tmp_dir/opr-bare.err"; then
  printf 'test: noninteractive bare OPR launch accepted a missing route\n' >&2
  exit 1
else
  opr_bare_status=$?
fi
[[ "$opr_bare_status" -eq 2 ]]
grep -q 'OpenRouter route is required outside an interactive terminal' "$tmp_dir/opr-bare.err"

# A hybrid OpenRouter root must keep the resolved registry route after the
# resolver function returns. This is an isolated launcher test: the fake router
# accepts only hybrid arguments, the Claude stub makes no model request, and no
# real credential backend is read.
hybrid_opr_registry="$tmp_dir/hybrid-opr-private/openrouter-registry.json"
python - "$repo_root/bin" "$hybrid_opr_registry" <<'PY'
import sys
import time
sys.path.insert(0, sys.argv[1])
import airlock_policy as policy
policy.write_openrouter_registry(sys.argv[2], {
    "schema_version": 1,
    "models": [{
        "route": "ox-alpha",
        "model": "stealth/ox-alpha",
        "endpoint_provider": "stealth",
        "provider_name": "Stealth",
        "provider_slug": "stealth",
        "quantization": "unknown",
        "canonical_slug": "stealth/ox-alpha",
        "alias_target": None,
        "supported_parameters": ["tool_choice", "tools"],
        "expiration_date": None,
        "checked_at": int(time.time()),
        "enabled": True,
    }],
})
PY
hybrid_opr_router="$tmp_dir/hybrid-opr-router.py"
cat > "$hybrid_opr_router" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path
import sys
# The launcher watches the router process so a session survives its router
# being killed, so this stub answers both the start and the watch it runs.
if len(sys.argv) >= 2 and sys.argv[1] == "watch":
    marker = os.environ.get("AIRLOCK_TEST_ROUTER_WATCH_MARKER")
    if marker:
        Path(marker).write_text(" ".join(sys.argv[1:]), encoding="utf-8")
    raise SystemExit(0)
if len(sys.argv) < 2 or sys.argv[1] != "start" or "--openai-url" not in sys.argv:
    raise SystemExit(71)
if "--print-pid" not in sys.argv:
    raise SystemExit(72)
print("http://127.0.0.1:28473")
print(os.getpid())
PY
chmod +x "$hybrid_opr_router"
hybrid_opr_bundle="$tmp_dir/hybrid-opr-managed-bundle.json"
python - "$repo_root/config/managed-bundle.json" "$hybrid_opr_bundle" "$hybrid_opr_router" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
bundle = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
bundle["components"]["bin/airlock-router.py"] = hashlib.sha256(
    Path(sys.argv[3]).read_bytes()
).hexdigest()
Path(sys.argv[2]).write_text(json.dumps(bundle), encoding="utf-8")
PY
hybrid_opr_output="$(
  OPENAI_API_KEY=synthetic GROK_API_KEY=synthetic \
  OPENROUTER_API_KEY=synthetic CLAUDE_CODE_OAUTH_TOKEN=synthetic \
  AIRLOCK_OPENROUTER_REGISTRY_FILE="$hybrid_opr_registry" \
  AIRLOCK_MANAGED_BUNDLE_FILE="$hybrid_opr_bundle" \
  AIRLOCK_ROUTER_HELPER="$hybrid_opr_router" AIRLOCK_REAL_CLAUDE="$stub" \
  AIRLOCK_TEST_ROUTER_WATCH_MARKER="$tmp_dir/hybrid-opr-watch.marker" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 \
    "$launcher" hybrid ox-alpha -p test
)"
# A router-backed session watches its router, so a router killed mid-session
# is replaced instead of taking every later request with it.
if [[ ! -s "$tmp_dir/hybrid-opr-watch.marker" ]]; then
  printf 'test: the launcher did not watch the session router\n' >&2
  exit 1
fi
grep -q -- '--router-pid' "$tmp_dir/hybrid-opr-watch.marker"
grep -q -- '--port 28473' "$tmp_dir/hybrid-opr-watch.marker"
grep -q '^ACTIVE_PROFILE=hybrid-openrouter-root$' <<<"$hybrid_opr_output"
grep -q '^ROOT_MODEL=stealth/ox-alpha$' <<<"$hybrid_opr_output"
grep -q '^MODEL=unset$' <<<"$hybrid_opr_output"
grep -q '^CUSTOM_NAME=OpenRouter ox-alpha (OpenRouter hybrid root)$' <<<"$hybrid_opr_output"
grep -q '^DEFAULT_OPUS=claude-opus-5\[1m\]$' <<<"$hybrid_opr_output"
grep -q '^DEFAULT_SONNET=claude-sonnet-5\[1m\]$' <<<"$hybrid_opr_output"
grep -q '^DEFAULT_HAIKU=claude-sonnet-5\[1m\]$' <<<"$hybrid_opr_output"
grep -q '^AUTO_MODE_MODEL=claude-sonnet-5\[1m\]$' <<<"$hybrid_opr_output"
grep -q '^OPENROUTER_KEY_SET=no$' <<<"$hybrid_opr_output"
grep -q '^ARG=stealth/ox-alpha$' <<<"$hybrid_opr_output"
hybrid_opr_profile="$hybrid_opr_output" python - <<'PY'
import json
import os
lines = os.environ["hybrid_opr_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
agents = json.loads(args[args.index("--agents") + 1])
assert args.count("--model") == 1
assert args[args.index("--model") + 1] == "stealth/ox-alpha"
assert agents["airlock-or-ox-alpha"]["model"] == "stealth/ox-alpha"
assert "hybrid root model" in agents["airlock-or-ox-alpha"]["description"]
assert all(
    value != "stealth/ox-alpha"
    for key, value in (
        ("fable", next(line.split("=", 1)[1] for line in lines if line.startswith("DEFAULT_FABLE="))),
        ("opus", next(line.split("=", 1)[1] for line in lines if line.startswith("DEFAULT_OPUS="))),
        ("sonnet", next(line.split("=", 1)[1] for line in lines if line.startswith("DEFAULT_SONNET="))),
        ("haiku", next(line.split("=", 1)[1] for line in lines if line.startswith("DEFAULT_HAIKU="))),
    )
), "OpenRouter root leaked into a family alias"
PY
if AIRLOCK_OPENROUTER_REGISTRY_FILE="$hybrid_opr_registry" \
  "$launcher" hybrid ox-alpha --model moonshotai/kimi-k3 -p test \
  >/dev/null 2>"$tmp_dir/hybrid-opr-mismatch.err"; then
  printf 'test: hybrid OpenRouter root accepted a mismatched --model\n' >&2
  exit 1
fi
grep -q 'forwarded --model disagrees with the selected OpenRouter root route' \
  "$tmp_dir/hybrid-opr-mismatch.err"

hybrid_openai_output="$(OPENROUTER_API_KEY=synthetic AIRLOCK_STUB_INSPECT_ROUTER=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid terra -p test)"
grep -q '^MODEL=unset$' <<<"$hybrid_openai_output"
grep -q '^OPENROUTER_KEY_SET=no$' <<<"$hybrid_openai_output"
grep -Eq '^BASE_URL=http://127\.0\.0\.1:[1-9][0-9]*$' <<<"$hybrid_openai_output"
if grep -q '^BASE_URL=http://127\.0\.0\.1:18765$' <<<"$hybrid_openai_output"; then
  printf 'test: hybrid OpenAI root bypassed the native router\n' >&2
  exit 1
fi
grep -q '^OPENAI_BRIDGE=unset$' <<<"$hybrid_openai_output"
grep -q '^ANTHROPIC_BRIDGE=1$' <<<"$hybrid_openai_output"
grep -q '^DEFAULT_FABLE=gpt-5.6-sol$' <<<"$hybrid_openai_output"
grep -q '^DEFAULT_OPUS=claude-opus-5\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^DEFAULT_SONNET=claude-sonnet-5\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^DEFAULT_HAIKU=claude-sonnet-5\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^SMALL_FAST=claude-sonnet-5\[1m\]$' <<<"$hybrid_openai_output"
# The classifier rides the small fast seat, which prefers native Claude here.
grep -q '^AUTO_MODE_MODEL=claude-sonnet-5\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^FABLE_NAME=gpt-5.6-sol$' <<<"$hybrid_openai_output"
grep -q '^OPUS_NAME=claude-opus-5\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^SONNET_NAME=claude-sonnet-5\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^HAIKU_NAME=claude-sonnet-5\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^AUTH_TOKEN_SET=no$' <<<"$hybrid_openai_output"
grep -q '^ALLOWED_AGENTS=airlock-luna,airlock-opus,airlock-sol,airlock-sonnet,airlock-terra$' <<<"$hybrid_openai_output"
grep -q '^ALLOWED_MODELS=claude-opus-5\[1m\],claude-sonnet-5\[1m\],gpt-5.6-luna,gpt-5.6-sol,gpt-5.6-terra$' <<<"$hybrid_openai_output"
grep -q '^ROUTER_MODELS=claude-opus-5,claude-opus-5\[1m\],claude-sonnet-5,claude-sonnet-5\[1m\],gpt-5.6-luna,gpt-5.6-sol,gpt-5.6-terra$' <<<"$hybrid_openai_output"
hybrid_profile="$hybrid_openai_output" python - <<'PY'
import base64
import json
import os
lines = os.environ["hybrid_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings_line_value = next(
    line[len("SETTINGS_JSON="):] for line in lines
    if line.startswith("SETTINGS_JSON=") and line != "SETTINGS_JSON=unreadable"
)
settings = json.loads(settings_line_value)
assert settings["autoMode"]["environment"][0] == "$defaults"
trust_context = settings["autoMode"]["environment"][1]
assert "OpenAI models" in trust_context and "Anthropic Claude" in trust_context
assert "eligible non-ignored untracked regular files" in trust_context
assert "built-in Explore, Plan, and general-purpose Agent types" in trust_context
assert "Git-ignored or unsafe paths" in trust_context
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-opus", "airlock-sonnet"}
assert agents["airlock-opus"]["model"] == "claude-opus-5[1m]"
assert all("effort" not in agent for agent in agents.values())
assert "tools" not in agents["airlock-opus"] and "permissionMode" not in agents["airlock-opus"]
assert "airlock-delegate" not in agents["airlock-opus"]["prompt"]
assert "native Claude Code tools" in agents["airlock-opus"]["prompt"]
assert "exact model: claude-opus-5" in agents["airlock-opus"]["description"]
assert "UI/UX design" in agents["airlock-opus"]["description"]
assert "design-system-aligned UI implementation" in agents["airlock-sonnet"]["description"]
assert agents["airlock-sol"]["model"] == "gpt-5.6-sol"
assert "tools" not in agents["airlock-sol"] and "permissionMode" not in agents["airlock-sol"]
assert "airlock-delegate" not in agents["airlock-sol"]["prompt"]
assert all(agent["disallowedTools"] == ["Agent"] for agent in agents.values())
start = args.index("--allowedTools") + 1
allowed = []
for value in args[start:]:
    if value.startswith("--"):
        break
    allowed.append(value)
expected_allowed = ["Agent(Explore)", "Agent(Plan)", "Agent(general-purpose)", *[f"Agent({name})" for name in sorted(agents)]]
assert allowed == expected_allowed, (allowed, expected_allowed)
assert "Bash(airlock-delegate *)" not in allowed and "Bash(airlock-workflow *)" not in allowed
guidance = base64.b64decode(next(
    line[len("APPEND_SYSTEM_PROMPT_B64="):] for line in lines
    if line.startswith("APPEND_SYSTEM_PROMPT_B64=")
)).decode("utf-8")
assert "one session-scoped loopback router keeps every enabled provider inside the same Claude Code process" in guidance.replace("One session", "one session")
# Grok is off unless the config or an explicit Grok root enables it, so this
# session must be told the route does not exist rather than left to guess.
assert "This session has no Grok route enabled" in guidance
assert "Grok OAuth" not in guidance
assert "Built-in Explore, Plan, and general-purpose inherit the orchestrator model" in guidance
assert "Do not add task kind, risk, or selection markers" in guidance
assert "Preserve the full technical result" in guidance and "result.report" not in guidance
assert "Named airlock-* Agents cannot invoke Agent" in guidance
assert "Keep every fan-out decision at the root" in guidance
assert "never silently retry on a different provider or model" in guidance
assert "Automatic high-volume swarms are limited to airlock-luna" in guidance
assert "They run at the session effort unless they are pinned" in guidance
assert "Difficult implementation shards must have explicit file ownership" in guidance
# The roster follows the enabled pool, so assert the rule and the one
# agent that must never appear in it rather than a fixed list.
assert "Never automatically swarm airlock-opus" in guidance
assert "Never automatically swarm airlock-luna" not in guidance
assert "substantial visual and interaction design is Anthropic-first and Opus-led" in guidance
assert "keyboard navigation, selection mechanics" in guidance
assert "Start airlock-opus" in guidance and "Use airlock-sonnet" in guidance
assert "material new interaction pattern such as keyboard selection" in guidance
assert "routing requirement when Opus is enabled and eligible" in guidance
assert "generic work-directly rule does not override it" in guidance
assert "Start with Opus for new design judgment; do not launch both by default" in guidance
assert "split the roles automatically" in guidance
assert "Do not wait for the user to request this split" in guidance
assert "Before working directly on a multi-part request" in guidance
assert "A coupled final result does not make every phase coupled" in guidance
assert "Before assigning WebSearch, check effort compatibility" in guidance
assert "User communication:" in guidance and "meaningful phase changes" in guidance
PY

hybrid_router_url=''
while IFS= read -r line; do
  if [[ "$line" == BASE_URL=* ]]; then
    hybrid_router_url="${line#BASE_URL=}"
    break
  fi
done <<<"$hybrid_openai_output"
HYBRID_ROUTER_URL="$hybrid_router_url" python - <<'PY'
import http.client
import os
import time
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["HYBRID_ROUTER_URL"])
deadline = time.monotonic() + 4
while time.monotonic() < deadline:
    connection = None
    try:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=0.2)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        response.read()
    except OSError:
        break
    finally:
        if connection is not None:
            connection.close()
    time.sleep(0.1)
else:
    raise AssertionError("hybrid router remained available after the stub session exited")
PY

# Keep the hybrid child alive beyond the router's one-second owner monitor and
# probe it repeatedly. This proves the launcher remains the stable router owner
# while Claude runs on every platform. The child's status and snapshot cleanup
# must survive the wrapper.
router_hold_output="$tmp_dir/router-hold.out"
router_hold_error="$tmp_dir/router-hold.err"
AIRLOCK_STUB_HOLD_SECONDS=3 AIRLOCK_STUB_EXIT_STATUS=37 \
  AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
  "$launcher" hybrid terra -p test >"$router_hold_output" 2>"$router_hold_error" &
router_hold_pid=$!
router_hold_url=''
for _ in $(seq 1 100); do
  router_hold_url="$(python - "$router_hold_output" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
if path.exists():
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("SESSION_ROUTER="):
            print(line.removeprefix("SESSION_ROUTER="))
            break
PY
)"
  [[ -z "$router_hold_url" ]] || break
  sleep 0.05
done
if [[ ! "$router_hold_url" =~ ^http://127\.0\.0\.1:[1-9][0-9]*$ ]]; then
  kill "$router_hold_pid" 2>/dev/null || true
  wait "$router_hold_pid" 2>/dev/null || true
  printf 'test: held hybrid session did not publish its router: %s\n' "$(<"$router_hold_error")" >&2
  exit 1
fi
router_hold_snapshot="$(grep '^SESSION_SNAPSHOT=' "$router_hold_output" | cut -d= -f2-)"
if [[ -z "$router_hold_snapshot" || ! -f "$router_hold_snapshot" ]] || \
    ! grep -q '^SNAPSHOT_EXISTS=yes$' "$router_hold_output" || \
    ! grep -q '^SNAPSHOT_SHA256=[0-9a-f]\{64\}$' "$router_hold_output"; then
  kill "$router_hold_pid" 2>/dev/null || true
  wait "$router_hold_pid" 2>/dev/null || true
  printf 'test: held hybrid session did not retain a valid policy snapshot\n' >&2
  exit 1
fi
HYBRID_ROUTER_URL="$router_hold_url" python - <<'PY'
import http.client
import os
import time
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["HYBRID_ROUTER_URL"])
for _ in range(4):
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
    connection.request("GET", "/healthz")
    response = connection.getresponse()
    response.read()
    connection.close()
    if response.status != 200:
        raise AssertionError(f"held hybrid router returned {response.status}")
    time.sleep(0.55)
PY
if wait "$router_hold_pid"; then
  printf 'test: held hybrid child status 37 was lost\n' >&2
  exit 1
else
  router_hold_status=$?
fi
if (( router_hold_status != 37 )); then
  printf 'test: held hybrid child returned %s instead of 37: %s\n' \
    "$router_hold_status" "$(<"$router_hold_error")" >&2
  exit 1
fi
if [[ -e "$router_hold_snapshot" ]]; then
  printf 'test: held hybrid session snapshot remained after child exit\n' >&2
  exit 1
fi
HYBRID_ROUTER_URL="$router_hold_url" python - <<'PY'
import http.client
import os
import time
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["HYBRID_ROUTER_URL"])
for _ in range(40):
    try:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=0.2)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        response.read()
        connection.close()
    except OSError:
        break
    time.sleep(0.1)
else:
    raise AssertionError("held hybrid router remained available after child exit")
PY

# Signals sent to the stable launcher owner must reach Claude on every platform.
router_signal_output="$tmp_dir/router-signal.out"
router_signal_error="$tmp_dir/router-signal.err"
router_signal_file="$tmp_dir/router-signal.txt"
AIRLOCK_STUB_HOLD_SECONDS=30 AIRLOCK_STUB_SIGNAL_FILE="$router_signal_file" \
  AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
  "$launcher" hybrid terra -p test >"$router_signal_output" 2>"$router_signal_error" &
router_signal_pid=$!
for _ in $(seq 1 100); do
  [[ -f "$router_signal_output" ]] && grep -q '^SESSION_ROUTER=http://127\.0\.0\.1:' "$router_signal_output" && break
  sleep 0.05
done
if ! grep -q '^SESSION_ROUTER=http://127\.0\.0\.1:' "$router_signal_output" 2>/dev/null; then
  kill "$router_signal_pid" 2>/dev/null || true
  wait "$router_signal_pid" 2>/dev/null || true
  printf 'test: signal hybrid session did not start: %s\n' "$(<"$router_signal_error")" >&2
  exit 1
fi
router_signal_url="$(python - "$router_signal_output" <<'PY'
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line.startswith("SESSION_ROUTER="):
        print(line.removeprefix("SESSION_ROUTER="))
        break
PY
)"
router_signal_snapshot="$(grep '^SESSION_SNAPSHOT=' "$router_signal_output" | cut -d= -f2-)"
if [[ -z "$router_signal_snapshot" || ! -f "$router_signal_snapshot" ]]; then
  kill "$router_signal_pid" 2>/dev/null || true
  wait "$router_signal_pid" 2>/dev/null || true
  printf 'test: signal session policy snapshot was not retained\n' >&2
  exit 1
fi
kill -TERM "$router_signal_pid"
if wait "$router_signal_pid"; then
  printf 'test: hybrid launcher swallowed TERM\n' >&2
  exit 1
else
  router_signal_status=$?
fi
if (( router_signal_status != 143 )) || [[ "$(<"$router_signal_file")" != 'TERM' ]]; then
  printf 'test: hybrid TERM forwarding failed with status %s: %s\n' \
    "$router_signal_status" "$(<"$router_signal_error")" >&2
  exit 1
fi
if [[ -e "$router_signal_snapshot" ]]; then
  printf 'test: signaled hybrid session snapshot remained after child exit\n' >&2
  exit 1
fi
HYBRID_ROUTER_URL="$router_signal_url" python - <<'PY'
import http.client
import os
import time
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["HYBRID_ROUTER_URL"])
for _ in range(40):
    try:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=0.2)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        response.read()
        connection.close()
    except OSError:
        break
    time.sleep(0.1)
else:
    raise AssertionError("signaled hybrid router remained available after child exit")
PY

hybrid_anthropic_output="$(ANTHROPIC_BASE_URL=leak ANTHROPIC_MODEL=leak AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sonnet -p test)"
grep -q '^MODEL=unset$' <<<"$hybrid_anthropic_output"
grep -Eq '^BASE_URL=http://127\.0\.0\.1:[1-9][0-9]*$' <<<"$hybrid_anthropic_output"
if grep -q '^BASE_URL=http://127\.0\.0\.1:18765$' <<<"$hybrid_anthropic_output"; then
  printf 'test: hybrid Anthropic root bypassed the native router\n' >&2
  exit 1
fi
grep -q '^OPENAI_BRIDGE=1$' <<<"$hybrid_anthropic_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$hybrid_anthropic_output"
grep -q '^DEFAULT_FABLE=gpt-5.6-sol$' <<<"$hybrid_anthropic_output"
grep -q '^DEFAULT_OPUS=claude-opus-5\[1m\]$' <<<"$hybrid_anthropic_output"
grep -q '^DEFAULT_SONNET=claude-sonnet-5\[1m\]$' <<<"$hybrid_anthropic_output"
grep -q '^DEFAULT_HAIKU=claude-sonnet-5\[1m\]$' <<<"$hybrid_anthropic_output"
grep -q '^SMALL_FAST=claude-sonnet-5\[1m\]$' <<<"$hybrid_anthropic_output"
# An Anthropic root serves the stock classifier target, so the knob stays clear.
grep -q '^AUTO_MODE_MODEL=unset$' <<<"$hybrid_anthropic_output"
# With Haiku enabled, which is the setup default now, the family slot prefers
# the cheapest Claude route instead of falling back to Sonnet.
hybrid_haiku_seat_output="$(AIRLOCK_ANTHROPIC_MODELS=opus,sonnet,haiku AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sonnet -p test)"
grep -q '^DEFAULT_HAIKU=claude-haiku-4-5-20251001$' <<<"$hybrid_haiku_seat_output"
grep -q '^SMALL_FAST=claude-haiku-4-5-20251001$' <<<"$hybrid_haiku_seat_output"
grep -q '^HAIKU_NAME=claude-haiku-4-5-20251001$' <<<"$hybrid_haiku_seat_output"
grep -q '^AUTO_MODE_MODEL=unset$' <<<"$hybrid_haiku_seat_output"
grep -q '^AUTH_TOKEN_SET=no$' <<<"$hybrid_anthropic_output"
grep -q '^ALLOWED_AGENTS=airlock-luna,airlock-opus,airlock-sol,airlock-sonnet,airlock-terra$' <<<"$hybrid_anthropic_output"
grep -q '^ALLOWED_MODELS=claude-opus-5\[1m\],claude-sonnet-5\[1m\],gpt-5.6-luna,gpt-5.6-sol,gpt-5.6-terra$' <<<"$hybrid_anthropic_output"
hybrid_profile="$hybrid_anthropic_output" python - <<'PY'
import base64
import json
import os
lines = os.environ["hybrid_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings_line_value = next(
    line[len("SETTINGS_JSON="):] for line in lines
    if line.startswith("SETTINGS_JSON=") and line != "SETTINGS_JSON=unreadable"
)
settings = json.loads(settings_line_value)
assert settings["autoMode"]["environment"][0] == "$defaults"
trust_context = settings["autoMode"]["environment"][1]
assert "OpenAI models" in trust_context and "Anthropic Claude" in trust_context
assert "eligible non-ignored untracked regular files" in trust_context
assert "built-in Explore, Plan, and general-purpose Agent types" in trust_context
assert "Git-ignored or unsafe paths" in trust_context
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-opus", "airlock-sonnet"}
assert agents["airlock-sol"]["model"] == "gpt-5.6-sol"
assert "tools" not in agents["airlock-sol"] and "permissionMode" not in agents["airlock-sol"]
assert "airlock-delegate" not in agents["airlock-sol"]["prompt"]
assert agents["airlock-sonnet"]["model"] == "claude-sonnet-5[1m]"
assert all("effort" not in agent for agent in agents.values())
assert "tools" not in agents["airlock-sonnet"] and "permissionMode" not in agents["airlock-sonnet"]
assert "airlock-delegate" not in agents["airlock-sonnet"]["prompt"]
assert all(agent["disallowedTools"] == ["Agent"] for agent in agents.values())
start = args.index("--allowedTools") + 1
allowed = []
for value in args[start:]:
    if value.startswith("--"):
        break
    allowed.append(value)
expected_allowed = ["Agent(Explore)", "Agent(Plan)", "Agent(general-purpose)", *[f"Agent({name})" for name in sorted(agents)]]
assert allowed == expected_allowed, (allowed, expected_allowed)
assert "Bash(airlock-delegate *)" not in allowed and "Bash(airlock-workflow *)" not in allowed
assert "Skill(claude-api)" in args
assert args[args.index("--model") + 1] == "claude-sonnet-5[1m]"
PY

if ANTHROPIC_AUTH_TOKEN=synthetic AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid opus -p test >/dev/null 2>&1; then
  printf 'test: hybrid route accepted an explicit Anthropic credential override\n' >&2
  exit 1
fi

reduced_config="$tmp_dir/reduced.conf"
printf '%s\n' 'AIRLOCK_ANTHROPIC_MODELS=sonnet' 'AIRLOCK_OPENAI_MODELS=sol,luna' 'AIRLOCK_ANTHROPIC_EXTRA_MODELS=' 'AIRLOCK_OPENAI_EXTRA_MODELS=' >"$reduced_config"
reduced_output="$(AIRLOCK_CONFIG_FILE="$reduced_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sol -p test)"
reduced_profile="$reduced_output" python - <<'PY'
import base64
import json
import os
lines = os.environ["reduced_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings_line_value = next(
    line[len("SETTINGS_JSON="):] for line in lines
    if line.startswith("SETTINGS_JSON=") and line != "SETTINGS_JSON=unreadable"
)
settings = json.loads(settings_line_value)
trust_context = settings["autoMode"]["environment"][1]
assert "OpenAI models" in trust_context and "Anthropic Claude" in trust_context
assert "airlock-luna, airlock-sol, airlock-sonnet" in trust_context
assert "airlock-opus" not in trust_context and "airlock-terra" not in trust_context
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-sol", "airlock-luna", "airlock-sonnet"}
start = args.index("--allowedTools") + 1
allowed = []
for value in args[start:]:
    if value.startswith("--"):
        break
    allowed.append(value)
assert allowed == [
    "Agent(Explore)",
    "Agent(Plan)",
    "Agent(general-purpose)",
    "Agent(airlock-luna)",
    "Agent(airlock-sol)",
    "Agent(airlock-sonnet)",
]
for disabled in ("Agent(airlock-terra)", "Agent(airlock-opus)", "Agent(airlock-fable)", "Agent(airlock-haiku)"):
    assert disabled not in allowed
PY
grep -q '^ALLOWED_AGENTS=airlock-luna,airlock-sol,airlock-sonnet$' <<<"$reduced_output"
grep -q '^ALLOWED_MODELS=claude-sonnet-5\[1m\],gpt-5.6-luna,gpt-5.6-sol$' <<<"$reduced_output"

for root_spec in 'sol:gpt-5.6-sol:openai' 'luna:gpt-5.6-luna:openai' 'opus:claude-opus-5[1m]:anthropic'; do
  IFS=':' read -r root_alias expected_model expected_provider <<<"$root_spec"
  root_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid "$root_alias" -p test)"
  grep -Fq "ARG=$expected_model" <<<"$root_output"
  if [[ "$expected_provider" == 'openai' ]]; then
    grep -q '^ANTHROPIC_BRIDGE=1$' <<<"$root_output"
  else
    grep -q '^OPENAI_BRIDGE=1$' <<<"$root_output"
  fi
done

# An interactive launch forwards no extra arguments, so the launcher has to
# expand an empty argument array before it inserts the root model and effort.
# The bash that macOS ships as /bin/bash rejects a bare expansion of an empty
# array under set -u, so both entry points need a no-argument run here.
bare_hybrid_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid opus)"
# File-backed flags land early in argv and user passthrough lands last, so the
# effort/model pair and the guidance flag are checked as independent elements.
grep -Fq '"--effort", "high", "--model", "claude-opus-5[1m]"' <<<"$bare_hybrid_output"
grep -Fq '"--append-system-prompt-file"' <<<"$bare_hybrid_output"
grep -q '^OPENAI_BRIDGE=1$' <<<"$bare_hybrid_output"
# Claude Code detects effort support for real Claude IDs on its own. Declaring
# capabilities here would disable everything left off the list.
grep -q '^CUSTOM_CAPS=unset$' <<<"$bare_hybrid_output"

hybrid_gpt_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sol -p test)"
grep -q '^CUSTOM_CAPS=effort,xhigh_effort,max_effort$' <<<"$hybrid_gpt_output"

# The >300k Sol proof did not pass, so OpenAI roots keep the saved fallback.
grep -q '^COMPACT_WINDOW=272000$' <<<"$hybrid_gpt_output"
grep -Eq '^SESSION_ROUTER=http://127\.0\.0\.1:[1-9][0-9]*$' <<<"$hybrid_gpt_output"
grep -q '^COMPACT_WINDOW=unset$' <<<"$hybrid_anthropic_output"
auto_window_output="$(AIRLOCK_CONTEXT_WINDOW=auto AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sol -p test)"
grep -q '^COMPACT_WINDOW=unset$' <<<"$auto_window_output"
explicit_airlock_window_output="$(AIRLOCK_CONTEXT_WINDOW=450000 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sol -p test)"
grep -q '^COMPACT_WINDOW=450000$' <<<"$explicit_airlock_window_output"
user_window_output="$(CLAUDE_CODE_AUTO_COMPACT_WINDOW=450000 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid opus -p test)"
grep -q '^COMPACT_WINDOW=450000$' <<<"$user_window_output"
# Claude Code accepts 100000 to 1000000 and silently ignores anything else.
for rejected_window in 50000 99999 1000001 2000000 0272000 +272000 ' 272000 ' notanumber; do
  if AIRLOCK_CONTEXT_WINDOW="$rejected_window" AIRLOCK_REAL_CLAUDE="$stub" \
    AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sol -p test >/dev/null 2>&1; then
    printf 'test: launcher accepted out-of-range context window %s\n' "$rejected_window" >&2
    exit 1
  fi
done
bare_openai_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher")"
# File-backed flags land early in argv and user passthrough lands last, so the
# model/effort pair and the guidance flag are checked as independent elements.
grep -Fq '"--model", "gpt-5.6-sol", "--effort", "high"' <<<"$bare_openai_output"
grep -Fq '"--append-system-prompt-file"' <<<"$bare_openai_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$bare_openai_output"
# OpenAI roots keep the conservative fallback because no long-context proof passed.
grep -q '^COMPACT_WINDOW=272000$' <<<"$bare_openai_output"
bare_unknown_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" spark -p test)"
grep -q '^MODEL=gpt-5.3-codex-spark$' <<<"$bare_unknown_output"
grep -q '^COMPACT_WINDOW=272000$' <<<"$bare_unknown_output"

# Configs without AIRLOCK_DEFAULT_PROFILE preserve the original OpenAI-only
# bare command. New configs can save a hybrid root without changing explicit
# OpenAI aliases or the explicit `airlock openai` command.
saved_hybrid_config="$tmp_dir/saved-hybrid.conf"
cp "$custom_config" "$saved_hybrid_config"
printf '%s\n' 'AIRLOCK_DEFAULT_PROFILE=hybrid' 'AIRLOCK_HYBRID_MODEL=sonnet' >> "$saved_hybrid_config"
saved_hybrid_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q '^OPENAI_BRIDGE=1$' <<<"$saved_hybrid_output"
grep -Fq 'ARG=claude-sonnet-5' <<<"$saved_hybrid_output"
grep -q '^FAST_MODE=off$' <<<"$saved_hybrid_output"
saved_hybrid_command_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid -p test)"
grep -q '^OPENAI_BRIDGE=1$' <<<"$saved_hybrid_command_output"
grep -Fq 'ARG=claude-sonnet-5' <<<"$saved_hybrid_command_output"
saved_openai_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" openai -p test)"
grep -q '^MODEL=gpt-5.6-terra$' <<<"$saved_openai_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$saved_openai_output"
saved_exact_openai_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" openai 'gpt-5.6-luna[1m]' -p test)"
grep -q '^MODEL=gpt-5.6-luna$' <<<"$saved_exact_openai_output"
legacy_hybrid_positional_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid 'gpt-5.6-terra[1m]' -p test)"
grep -q '^ROOT_MODEL=gpt-5.6-terra$' <<<"$legacy_hybrid_positional_output"
grep -Fq 'ARG=gpt-5.6-terra' <<<"$legacy_hybrid_positional_output"
if grep -Fq 'ARG=gpt-5.6-terra[1m]' <<<"$legacy_hybrid_positional_output"; then
  printf 'test: legacy positional hybrid model reached Claude Code without normalization\n' >&2
  exit 1
fi
legacy_hybrid_flag_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid --model 'gpt-5.6-luna[1m]' -p test)"
grep -q '^ROOT_MODEL=gpt-5.6-luna$' <<<"$legacy_hybrid_flag_output"
grep -Fq 'ARG=gpt-5.6-luna' <<<"$legacy_hybrid_flag_output"
if grep -Fq 'ARG=gpt-5.6-luna[1m]' <<<"$legacy_hybrid_flag_output"; then
  printf 'test: legacy hybrid --model value reached Claude Code without normalization\n' >&2
  exit 1
fi
saved_equals_openai_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" openai '--model=gpt-5.6-sol' -p test)"
grep -q '^MODEL=gpt-5.6-sol$' <<<"$saved_equals_openai_output"
saved_background_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" background -p test)"
grep -q '^MODEL=gpt-5.6-luna$' <<<"$saved_background_output"
grep -q '^ARG=low$' <<<"$saved_background_output"
saved_alias_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" luna -p test)"
grep -q '^MODEL=gpt-5.6-luna$' <<<"$saved_alias_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$saved_alias_output"

anthropic_fast_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_ANTHROPIC_FAST=on AIRLOCK_ANTHROPIC_FAST_AUTHORIZED=yes AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid opus -p test)"
grep -q '^FAST_MODE=on$' <<<"$anthropic_fast_output"
if AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_ANTHROPIC_FAST=on AIRLOCK_EXTRA_USAGE_POLICY=never AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid opus -p test >/dev/null 2>&1; then
  printf 'test: Anthropic Fast bypassed the blocked extra-usage policy\n' >&2
  exit 1
fi

saved_gpt_config="$tmp_dir/saved-hybrid-gpt.conf"
cp "$custom_config" "$saved_gpt_config"
printf '%s\n' 'AIRLOCK_DEFAULT_PROFILE=hybrid' 'AIRLOCK_HYBRID_MODEL=terra' >> "$saved_gpt_config"
saved_gpt_output="$(AIRLOCK_CONFIG_FILE="$saved_gpt_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q '^ANTHROPIC_BRIDGE=1$' <<<"$saved_gpt_output"
grep -Fq 'ARG=gpt-5.6-terra' <<<"$saved_gpt_output"

invalid_profile_config="$tmp_dir/invalid-profile.conf"
printf '%s\n' 'AIRLOCK_DEFAULT_PROFILE=invalid' > "$invalid_profile_config"
if AIRLOCK_CONFIG_FILE="$invalid_profile_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test >/dev/null 2>&1; then
  printf 'test: invalid saved default profile unexpectedly launched\n' >&2
  exit 1
fi
invalid_hybrid_config="$tmp_dir/invalid-hybrid.conf"
printf '%s\n' 'AIRLOCK_DEFAULT_PROFILE=hybrid' 'AIRLOCK_HYBRID_MODEL=invalid' > "$invalid_hybrid_config"
if AIRLOCK_CONFIG_FILE="$invalid_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test >/dev/null 2>&1; then
  printf 'test: invalid saved hybrid model unexpectedly launched\n' >&2
  exit 1
fi
for invalid_saved_model in 'AIRLOCK_MODEL=invalid' 'AIRLOCK_BG_MODEL=invalid'; do
  invalid_model_config="$tmp_dir/invalid-model-${invalid_saved_model%%=*}.conf"
  printf '%s\n' "$invalid_saved_model" > "$invalid_model_config"
  if AIRLOCK_CONFIG_FILE="$invalid_model_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test >/dev/null 2>&1; then
    printf 'test: invalid saved model unexpectedly launched: %s\n' "$invalid_saved_model" >&2
    exit 1
  fi
done

if AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid fable -p test >/dev/null 2>&1; then
  printf 'test: unavailable Fable hybrid root unexpectedly launched\n' >&2
  exit 1
fi

if AIRLOCK_OPENAI_FAST=on AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" sol-fast -p test >/dev/null 2>&1; then
  printf 'test: Sol Fast unexpectedly bypassed the plan gate\n' >&2
  exit 1
fi
python - "$tmp_dir/access.json" "$tmp_dir/fast-access.json" <<'PY'
import json
from pathlib import Path
import sys
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
value["providers"]["openai"]["detected_plan"] = "pro"
Path(sys.argv[2]).write_text(json.dumps(value), encoding="utf-8")
PY
fast_root_output="$(AIRLOCK_ACCESS_FILE="$tmp_dir/fast-access.json" AIRLOCK_OPENAI_FAST=on AIRLOCK_PROXY_FAST_CAPABLE=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" sol-fast -p test)"
grep -q '^MODEL=gpt-5.6-sol-fast$' <<<"$fast_root_output"

config_before_fast="$(sha256sum "$AIRLOCK_CONFIG_FILE" | cut -d' ' -f1)"
ephemeral_fast_output="$(AIRLOCK_ACCESS_FILE="$tmp_dir/fast-access.json" AIRLOCK_OPENAI_FAST=off AIRLOCK_PROXY_FAST_CAPABLE=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" fast -r)"
grep -q '^MODEL=gpt-5.6-sol-fast$' <<<"$ephemeral_fast_output"
grep -q '^ARG=-r$' <<<"$ephemeral_fast_output"
grep -q '^FAST_TRANSITION=set$' <<<"$ephemeral_fast_output"
[[ "$(sha256sum "$AIRLOCK_CONFIG_FILE" | cut -d' ' -f1)" == "$config_before_fast" ]]
if AIRLOCK_ACCESS_FILE="$tmp_dir/fast-access.json" AIRLOCK_OPENAI_FAST=off AIRLOCK_PROXY_FAST_CAPABLE=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" fast --model gpt-5.6-terra >/dev/null 2>&1; then
  printf 'test: session-local Fast shortcut accepted a model override\n' >&2
  exit 1
fi
if AIRLOCK_ACCESS_FILE="$tmp_dir/access.json" AIRLOCK_OPENAI_FAST=off AIRLOCK_PROXY_FAST_CAPABLE=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" fast -r >/dev/null 2>&1; then
  printf 'test: session-local Fast shortcut bypassed the plan gate\n' >&2
  exit 1
fi

skill_opt_in_output="$(AIRLOCK_ALLOW_CLAUDE_API_SKILL=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" sol -p test)"
if grep -Fq 'ARG=Skill(claude-api)' <<<"$skill_opt_in_output"; then
  printf 'test: explicit claude-api skill opt-in was ignored\n' >&2
  exit 1
fi

if AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid choose </dev/null >/dev/null 2>&1; then
  printf 'test: noninteractive hybrid picker unexpectedly selected a root\n' >&2
  exit 1
fi

# Grok routes are opt-in, so an explicit Grok root is what turns them on.
# No AIRLOCK_STUB_INSPECT_ROUTER here: a pure profile has no router to probe,
# and BASE_URL staying on the proxy port is what proves the router never ran.
grok_output="$(AIRLOCK_REAL_CLAUDE="$stub" \
  AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" grok -p test)"
grep -q '^MODEL=grok-4.6$' <<<"$grok_output"
grep -q '^ACTIVE_PROFILE=grok-pure$' <<<"$grok_output"
grep -q '^BASE_URL=http://127\.0\.0\.1:18765$' <<<"$grok_output"
grep -q '^ALLOWED_AGENTS=airlock-composer,airlock-grok$' <<<"$grok_output"
grep -q '^ALLOWED_MODELS=grok-4.6,grok-composer-2.5-fast$' <<<"$grok_output"
grep -q '^DEFAULT_FABLE=grok-4.6$' <<<"$grok_output"
grep -q '^DEFAULT_OPUS=grok-4.6$' <<<"$grok_output"
grep -q '^DEFAULT_SONNET=grok-composer-2.5-fast$' <<<"$grok_output"
grep -q '^DEFAULT_HAIKU=grok-composer-2.5-fast$' <<<"$grok_output"
grep -q '^SMALL_FAST=grok-composer-2.5-fast$' <<<"$grok_output"
grep -q '^CUSTOM_NAME=Grok 4.6 (Grok subscription)$' <<<"$grok_output"
# Classifications ride the economical Composer seat instead of the flagship root.
grep -q '^AUTO_MODE_MODEL=grok-composer-2.5-fast$' <<<"$grok_output"
grep -q '^ALWAYS_EFFORT=1$' <<<"$grok_output"
# grok-4.6 declares its documented 500000-token window and compacts at 80%
# of it, so automatic compaction still has room for the summary request it
# has to send. Composer has no documented window and keeps the fallback.
grep -q '^COMPACT_WINDOW=400000$' <<<"$grok_output"
grep -q '^MAX_CONTEXT=500000$' <<<"$grok_output"
[[ "$(grep -c '^ACTIVE_PROFILE=' <<<"$grok_output")" -eq 1 ]]
# The bundled web tools server rides in managed settings for a Grok root,
# together with guidance steering it away from the Anthropic-only built-ins.
# Settings arrive through the stub's parsed SETTINGS_JSON line, so assertions
# read the decoded object instead of matching dump formatting.
grok_profile="$grok_output" python - <<'PY'
import base64
import json
import os

lines = os.environ["grok_profile"].splitlines()
settings = json.loads(next(
    line[len("SETTINGS_JSON="):] for line in lines
    if line.startswith("SETTINGS_JSON=") and line != "SETTINGS_JSON=unreadable"
))
assert set(settings["mcpServers"]) == {"airlock-web-tools"}
assert settings["permissions"]["allow"] == [
    "mcp__airlock-web-tools__web_search",
    "mcp__airlock-web-tools__fetch_page",
]
assert settings["permissions"]["deny"] == ["WebSearch", "WebFetch"]
# The composed guidance reaches Claude as a base64 file-backed flag, so the
# steering paragraph is asserted against the decoded prompt.
guidance = base64.b64decode(next(
    line[len("APPEND_SYSTEM_PROMPT_B64="):] for line in lines
    if line.startswith("APPEND_SYSTEM_PROMPT_B64=")
)).decode("utf-8")
assert "Web tool guidance:" in guidance
PY
# The same server definition reaches Claude through a real --mcp-config file,
# because Claude Code does not start mcpServers entries from --settings.
grep -q '^MCP_SERVERS=airlock-web-tools$' <<<"$grok_output"

web_tools_off_output="$(AIRLOCK_WEB_TOOLS=off AIRLOCK_REAL_CLAUDE="$stub" \
  AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" grok -p test)"
if grep -q 'airlock-web-tools' <<<"$web_tools_off_output"; then
  printf 'test: AIRLOCK_WEB_TOOLS=off still registered the web tools server\n' >&2
  exit 1
fi
if grep -q '^MCP_SERVERS=' <<<"$web_tools_off_output"; then
  printf 'test: AIRLOCK_WEB_TOOLS=off still passed an --mcp-config file\n' >&2
  exit 1
fi
if grep -q '"permissions"' <<<"$web_tools_off_output"; then
  printf 'test: AIRLOCK_WEB_TOOLS=off kept the web tool denials\n' >&2
  exit 1
fi
# The off switch must also omit the steering paragraph from the composed
# guidance, which the stub only reports in its base64 encoded form.
web_tools_off_profile="$web_tools_off_output" python - <<'PY'
import base64
import os

lines = os.environ["web_tools_off_profile"].splitlines()
guidance = base64.b64decode(next(
    line[len("APPEND_SYSTEM_PROMPT_B64="):] for line in lines
    if line.startswith("APPEND_SYSTEM_PROMPT_B64=")
)).decode("utf-8")
assert "Web tool guidance" not in guidance
assert "airlock-web-tools" not in guidance
PY

grok_composer_output="$(AIRLOCK_REAL_CLAUDE="$stub" \
  AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" grok composer -p test)"
grep -q '^MODEL=grok-composer-2.5-fast$' <<<"$grok_composer_output"
grep -q '^COMPACT_WINDOW=272000$' <<<"$grok_composer_output"
grep -q '^MAX_CONTEXT=unset$' <<<"$grok_composer_output"
[[ "$(grep -c '^ACTIVE_PROFILE=' <<<"$grok_composer_output")" -eq 1 ]]

grok_new_id_output="$(AIRLOCK_REAL_CLAUDE="$stub" \
  AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" grok --model=grok-4.6 -p test)"
grep -q '^MODEL=grok-4.6$' <<<"$grok_new_id_output"

# Airlock runs one Grok flagship, so the older ID stays accepted as an alias
# of the shipped route instead of becoming a second route.
grok_legacy_id_output="$(AIRLOCK_REAL_CLAUDE="$stub" \
  AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" grok --model=grok-4.5 -p test)"
grep -q '^MODEL=grok-4.6$' <<<"$grok_legacy_id_output"

if AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
  "$launcher" grok --model=bogus -p test >/dev/null 2>&1; then
  printf 'test: unsupported Grok model was accepted\n' >&2
  exit 1
fi

# A declared model extends the launcher without code changes: with grok-4.7
# present and enabled in models.json, the same request resolves to that ID.
# The declared ID has to be one Airlock does not ship, or resolve_grok_model
# matches its own case first and the declaration path is never exercised.
declared_models="$(mktemp "${TMPDIR:-/tmp}/airlock-models-XXXXXX.json")"
cat >"$declared_models" <<'JSON'
{"schema_version": 1, "models": [{
  "id": "grok-4.7", "provider": "grok", "effort_ceiling": "xhigh",
  "context_window": null, "cost": "premium", "enabled": true
}]}
JSON
declared_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
  AIRLOCK_MODELS_FILE="$declared_models" \
  "$launcher" grok --model=grok-4.7 -p test)"
grep -q '^MODEL=grok-4.7$' <<<"$declared_output"
rm -f "$declared_models"

hybrid_grok_output="$(AIRLOCK_STUB_INSPECT_ROUTER=1 AIRLOCK_REAL_CLAUDE="$stub" \
  AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_GROK_WRAPPER_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid grok -p test)"
grep -q '^ACTIVE_PROFILE=hybrid-grok-root$' <<<"$hybrid_grok_output"
grep -Eq '^ROUTER_MODELS=.*grok-4\.6' <<<"$hybrid_grok_output"
grep -Eq '^ROUTER_MODELS=.*claude-opus-5' <<<"$hybrid_grok_output"
# A hybrid Grok root is still grok-4.6, so it declares the same documented
# window as the pure profile rather than the conservative fallback.
grep -q '^COMPACT_WINDOW=400000$' <<<"$hybrid_grok_output"
grep -q '^MAX_CONTEXT=500000$' <<<"$hybrid_grok_output"
# The Grok-rooted hybrid keeps working built-in WebFetch, so only WebSearch is denied.
hybrid_grok_profile="$hybrid_grok_output" python - <<'PY'
import json
import os

lines = os.environ["hybrid_grok_profile"].splitlines()
settings = json.loads(next(
    line[len("SETTINGS_JSON="):] for line in lines
    if line.startswith("SETTINGS_JSON=") and line != "SETTINGS_JSON=unreadable"
))
assert set(settings["mcpServers"]) == {"airlock-web-tools"}
assert settings["permissions"]["allow"] == [
    "mcp__airlock-web-tools__web_search",
    "mcp__airlock-web-tools__fetch_page",
]
assert settings["permissions"]["deny"] == ["WebSearch"]
PY
# The hybrid Grok root also receives the web tools through --mcp-config.
grep -q '^MCP_SERVERS=airlock-web-tools$' <<<"$hybrid_grok_output"
if grep -Fq '"WebFetch"' <<<"$hybrid_grok_output"; then
  printf 'test: hybrid Grok root denied working built-in WebFetch\n' >&2
  exit 1
fi

# A hybrid session that did not ask for Grok must not gain Grok workers.
hybrid_plain_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 \
  "$launcher" hybrid sonnet -p test)"
if grep -q 'airlock-grok' <<<"$hybrid_plain_output"; then
  printf 'test: hybrid session enabled Grok without an explicit opt-in\n' >&2
  exit 1
fi
# An Anthropic-rooted hybrid keeps Claude Code exactly as shipped: no web tools
# server, no denials, and no steering paragraph.
if grep -q '"permissions"' <<<"$hybrid_plain_output"; then
  printf 'test: Anthropic-rooted session received web tool denials\n' >&2
  exit 1
fi
if grep -q '"mcpServers"' <<<"$hybrid_plain_output"; then
  printf 'test: Anthropic-rooted session registered the web tools server\n' >&2
  exit 1
fi
if grep -q '^MCP_SERVERS=' <<<"$hybrid_plain_output"; then
  printf 'test: Anthropic-rooted session passed an --mcp-config file\n' >&2
  exit 1
fi
# The composed guidance of an Anthropic-rooted session carries no web tools
# paragraph; the stub reports it in base64 encoded form.
hybrid_plain_profile="$hybrid_plain_output" python - <<'PY'
import base64
import os

lines = os.environ["hybrid_plain_profile"].splitlines()
guidance = base64.b64decode(next(
    line[len("APPEND_SYSTEM_PROMPT_B64="):] for line in lines
    if line.startswith("APPEND_SYSTEM_PROMPT_B64=")
)).decode("utf-8")
assert "Web tool guidance" not in guidance
assert "airlock-web-tools" not in guidance
PY

status_without_router="$(AIRLOCK_SESSION_ROUTER_URL= "$launcher" status)"
[[ "$status_without_router" == 'no Airlock router is running for this terminal' ]]
if "$launcher" status unexpected >/dev/null 2>&1; then
  printf 'test: status command accepted an unexpected argument\n' >&2
  exit 1
fi

status_port_file="$tmp_dir/status-port"
python - "$status_port_file" <<'PY' &
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys

payload = {
    "instance_id": "0123456789abcdef",
    "profile": "hybrid-openai-root",
    "root_model": "gpt-5.6-sol",
    "root_provider": "openai",
    "rate_limit_cooldowns": ["gpt-5.6-terra"],
    "events": [
        {"timestamp": "2026-08-26T10:00:01Z", "kind": "session_model_pinned", "profile": "hybrid-openai-root", "model": "gpt-5.6-sol", "provider": "openai"},
        {"timestamp": "2026-08-26T10:00:02Z", "kind": "rate_limit_failover_attempted", "from_model": "gpt-5.6-sol", "to_model": "gpt-5.6-terra"},
        {"timestamp": "2026-08-26T10:00:03Z", "kind": "rate_limit_failover_succeeded", "from_model": "gpt-5.6-sol", "to_model": "gpt-5.6-terra", "hops": 1},
        {"timestamp": "2026-08-26T10:00:04Z", "kind": "rate_limit_cooldown_skipped", "model": "gpt-5.6-terra", "provider": "openai"},
        {"timestamp": "2026-08-26T10:00:05Z", "kind": "openrouter_effort_clamped", "model": "stealth/ox-alpha", "requested": "max", "forwarded": "high", "ceiling": "high"},
        {"timestamp": "2026-08-26T10:00:06Z", "kind": "sanitized_error_substituted", "model": "stealth/ox-alpha", "provider": "openrouter", "status": 403},
        {"timestamp": "2026-08-26T10:00:07Z", "kind": "rate_limit_chain_exhausted", "model": "gpt-5.6-sol", "last_model": "gpt-5.6-terra", "models_considered": 2},
        {"timestamp": "2026-08-26T10:00:08Z", "kind": "openrouter_server_tools_stripped", "model": "stealth/ox-alpha", "removed_count": 2},
        {"timestamp": "2026-08-26T10:00:09Z", "kind": "future_action_v2", "message": "SHOULD_NOT_PRINT", "credential": "SENTINEL_STATUS_SECRET"},
    ],
    "summary": [],
}
body = json.dumps(payload, separators=(",", ":")).encode("ascii")

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        if self.path != "/diagnostics":
            self.send_response(404)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

server = HTTPServer(("127.0.0.1", 0), Handler)
Path(sys.argv[1]).write_text(str(server.server_address[1]), encoding="ascii")
# status, the session-end hook, then two turn-notice runs.
for _ in range(4):
    server.handle_request()
server.server_close()
PY
status_server_pid=$!
for _ in $(seq 1 100); do
  [[ -s "$status_port_file" ]] && break
  sleep 0.02
done
if [[ ! -s "$status_port_file" ]]; then
  printf 'test: status stub did not publish its loopback port\n' >&2
  exit 1
fi
status_router_url="http://127.0.0.1:$(<"$status_port_file")"
status_with_router="$(AIRLOCK_SESSION_ROUTER_URL="$status_router_url" "$launcher" status)"
STATUS_OUTPUT="$status_with_router" python - <<'PY'
import os

lines = os.environ["STATUS_OUTPUT"].splitlines()
assert lines == [
    "Airlock router: hybrid-openai-root profile; root gpt-5.6-sol (openai)",
    "10:00:01Z - Pinned gpt-5.6-sol (openai) as the session root.",
    "10:00:02Z - gpt-5.6-sol hit a rate limit; trying gpt-5.6-terra.",
    "10:00:03Z - gpt-5.6-sol hit a rate limit; continued on gpt-5.6-terra.",
    "10:00:04Z - Skipped gpt-5.6-terra because its rate-limit cooldown is active.",
    "10:00:05Z - Clamped OpenRouter effort for stealth/ox-alpha from max to high (route ceiling high).",
    "10:00:06Z - Replaced the openrouter error for stealth/ox-alpha with a safe local message.",
    "10:00:07Z - The failover chain for gpt-5.6-sol exhausted 2 models.",
    "10:00:08Z - Removed 2 unsupported OpenRouter server tools for stealth/ox-alpha.",
    "10:00:09Z - Router action: future_action_v2.",
]
assert "SHOULD_NOT_PRINT" not in os.environ["STATUS_OUTPUT"]
assert "SENTINEL_STATUS_SECRET" not in os.environ["STATUS_OUTPUT"]
PY

python_command="$(command -v python)"
hook_output="$(
  AIRLOCK_SESSION_ROUTER_URL="$status_router_url" AIRLOCK_PYTHON="$python_command" \
    bash "$repo_root/plugins/airlock/scripts/router-session-end.sh"
)"
HOOK_OUTPUT="$hook_output" python - <<'PY'
import json
import os

value = json.loads(os.environ["HOOK_OUTPUT"])
assert set(value) == {"systemMessage"}
lines = value["systemMessage"].splitlines()
assert 1 <= len(lines) <= 6
assert lines[0] == "Airlock router: hybrid-openai-root profile; root gpt-5.6-sol (openai)"
assert lines[-1] == "10:00:09Z - Router action: future_action_v2."
assert "SHOULD_NOT_PRINT" not in value["systemMessage"]
assert "SENTINEL_STATUS_SECRET" not in value["systemMessage"]
PY
# The turn notice is the only in-session signal that another model answered,
# so it must name both models and must not repeat itself on a later turn.
turn_marker="$tmp_dir/turn-notice-session.json"
: >"$turn_marker"
turn_notice="$(
  AIRLOCK_SESSION_ROUTER_URL="$status_router_url" AIRLOCK_PYTHON="$python_command"     AIRLOCK_SESSION_SNAPSHOT="$turn_marker"     bash "$repo_root/plugins/airlock/scripts/router-turn-notice.sh"
)"
TURN_NOTICE="$turn_notice" python - <<'PY2'
import json
import os

value = json.loads(os.environ["TURN_NOTICE"])
assert set(value) == {"systemMessage"}
message = value["systemMessage"]
assert message.startswith("Airlock routing")
assert "gpt-5.6-sol was rate limited; gpt-5.6-terra answered instead." in message
# One switch, one line. A turn that hands off many times must not repeat it.
assert message.count("gpt-5.6-terra answered") == 1
assert "every replacement Airlock could try were rate limited" in message
assert "SHOULD_NOT_PRINT" not in message
assert "SENTINEL_STATUS_SECRET" not in message
PY2
repeat_notice="$(
  AIRLOCK_SESSION_ROUTER_URL="$status_router_url" AIRLOCK_PYTHON="$python_command"     AIRLOCK_SESSION_SNAPSHOT="$turn_marker"     bash "$repo_root/plugins/airlock/scripts/router-turn-notice.sh"
)"
[[ -z "$repeat_notice" ]]

wait "$status_server_pid"
status_server_pid=''

status_unreachable="$(AIRLOCK_SESSION_ROUTER_URL="$status_router_url" "$launcher" status)"
[[ "$status_unreachable" == 'no Airlock router is running for this terminal' ]]
silent_hook="$({ AIRLOCK_SESSION_ROUTER_URL="$status_router_url" AIRLOCK_PYTHON="$python_command" bash "$repo_root/plugins/airlock/scripts/router-session-end.sh"; } 2>/dev/null)"
[[ -z "$silent_hook" ]]

if AIRLOCK_SESSION_ROUTER_URL= "$launcher" session-usage >/dev/null 2>"$tmp_dir/session-usage.err"; then
  printf 'test: session usage succeeded outside a hybrid session\n' >&2
  exit 1
fi
grep -q 'available only inside an active hybrid Airlock session' "$tmp_dir/session-usage.err"

printf 'All airlock launcher tests passed.\n'
