#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
launcher="$repo_root/bin/airlock"
stub="$repo_root/tests/stub-claude.sh"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/airlock-launcher-test.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT
# Build a legacy config without the saved-profile keys to verify that existing
# installations keep their original OpenAI-only bare command.
while IFS= read -r line; do
  case "$line" in
    AIRLOCK_DEFAULT_PROFILE=*|AIRLOCK_HYBRID_MODEL=*) continue ;;
  esac
  printf '%s\n' "$line"
done < "$repo_root/config/airlock.conf.example" > "$tmp_dir/config"
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
export AIRLOCK_PLUGIN_DIR="$repo_root/plugins/airlock"
export AIRLOCK_MANAGED_BUNDLE_FILE="$repo_root/config/managed-bundle.json"
unset AIRLOCK_ROUTING_POLICY AIRLOCK_EXTRA_USAGE_POLICY

mode_config="$tmp_dir/mode.conf"
mode_access="$tmp_dir/mode-access.json"
printf '# preserve this comment\r\nUNRELATED=value\r\nAIRLOCK_ROUTING_POLICY=balanced\r\nAIRLOCK_EXTRA_USAGE_POLICY=ask\r\n' > "$mode_config"
mode_show_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode)"
grep -q '^Routing: balanced' <<<"$mode_show_output"
grep -q '^Extra usage: ask' <<<"$mode_show_output"
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
grep -q '^Preset: budget$' <<<"$budget_output"
defaults_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode defaults)"
grep -q '^Routing: balanced' <<<"$defaults_output"
grep -q '^Extra usage: ask' <<<"$defaults_output"
set_output="$(AIRLOCK_CONFIG_FILE="$mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode set --routing quality --extra-usage allow --failover never --max-agents 3 --swarm-fast on)"
grep -q '^Routing: quality' <<<"$set_output"
grep -q '^Extra usage: allow' <<<"$set_output"
grep -q '^Failover: never' <<<"$set_output"
grep -q '^Max concurrent top-level subagents: 3' <<<"$set_output"
grep -q '^Luna swarm Fast policy: on' <<<"$set_output"
grep -q '^Agent nesting: off for named Agents; root spawn depth=1' <<<"$set_output"
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

missing_mode_config="$tmp_dir/missing/config"
missing_output="$(AIRLOCK_CONFIG_FILE="$missing_mode_config" AIRLOCK_ACCESS_FILE="$mode_access" "$launcher" mode budget)"
grep -q '^Preset: budget$' <<<"$missing_output"
grep -q '^# Managed by https://github.com/Harshkamdar67/Airlock$' "$missing_mode_config"
grep -q '^AIRLOCK_ROUTING_POLICY=economy$' "$missing_mode_config"
grep -q '^AIRLOCK_EXTRA_USAGE_POLICY=never$' "$missing_mode_config"

bundle_output="$("$launcher" bundle)"
grep -q '^Managed bundle is current and complete\.$' <<<"$bundle_output"

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
  powershell_output="$(AIRLOCK_CONFIG_FILE="$powershell_config" AIRLOCK_ACCESS_FILE="$powershell_access" AIRLOCK_ACCESS_HELPER="$powershell_helper" powershell.exe -NoProfile -NonInteractive -File "$powershell_launcher" mode set --routing economy --extra-usage never --failover never --max-agents 3 --swarm-fast off)"
  grep -q '^Routing: economy' <<<"$powershell_output"
  grep -q '^Extra usage: never' <<<"$powershell_output"
  grep -q '^Failover: never' <<<"$powershell_output"
  grep -q '^Max concurrent top-level subagents: 3' <<<"$powershell_output"
  grep -q '^Luna swarm Fast policy: off' <<<"$powershell_output"
  grep -q '^Agent nesting: off for named Agents; root spawn depth=1' <<<"$powershell_output"
  grep -q '^Preset: budget' <<<"$powershell_output"
  powershell_usage="$(AIRLOCK_CONFIG_FILE="$powershell_config" AIRLOCK_ACCESS_FILE="$powershell_access" AIRLOCK_ACCESS_HELPER="$powershell_helper" powershell.exe -NoProfile -NonInteractive -File "$powershell_launcher" usage set --claude-plan pro --openai-capacity 5x)"
  grep -q 'configured-tier=pro; effective-tier=pro (source=user_override)' <<<"$powershell_usage"

  printf 'import os\nprint("PYTHON_BIN=" + os.environ.get("AIRLOCK_PYTHON", "unset"))\n' > "$tmp_dir/powershell-python-env.py"
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
grep -q '^MODEL=gpt-5.6-sol\[1m\]$' <<<"$normal_output"
grep -q '^SMALL_FAST=gpt-5.6-luna\[1m\]$' <<<"$normal_output"
grep -q '^EFFORT_ENV=unset$' <<<"$normal_output"
grep -q '^ARG=high$' <<<"$normal_output"
grep -q '^OPUS_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^SONNET_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^HAIKU_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"
grep -q '^CUSTOM_CAPS=effort,xhigh_effort,max_effort$' <<<"$normal_output"

background_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" bg -p test)"
grep -q '^MODEL=gpt-5.6-sol\[1m\]$' <<<"$background_output"
grep -q '^ARG=medium$' <<<"$background_output"

override_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" bg --effort high -p test)"
if grep -q '^ARG=medium$' <<<"$override_output"; then
  printf 'test: explicit effort was not respected\n' >&2
  exit 1
fi
grep -q '^ARG=high$' <<<"$override_output"

terra_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" terra -p test)"
grep -q '^MODEL=gpt-5.6-terra\[1m\]$' <<<"$terra_output"

custom_config="$repo_root/tests/fixtures/custom.conf"
configured_output="$(AIRLOCK_CONFIG_FILE="$custom_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q '^MODEL=gpt-5.6-terra\[1m\]$' <<<"$configured_output"
grep -q '^SMALL_FAST=gpt-5.4-mini\[1m\]$' <<<"$configured_output"
grep -q '^ARG=high$' <<<"$configured_output"

configured_bg_output="$(AIRLOCK_CONFIG_FILE="$custom_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" bg -p test)"
grep -q '^MODEL=gpt-5.6-luna\[1m\]$' <<<"$configured_bg_output"
grep -q '^ARG=low$' <<<"$configured_bg_output"

config_output="$(AIRLOCK_CONFIG_FILE="$custom_config" "$launcher" config)"
grep -q '^Default profile: openai$' <<<"$config_output"
grep -q '^Default command: airlock -> GPT-5.6 Terra (gpt-5.6-terra\[1m\])$' <<<"$config_output"
grep -q '^Hybrid root: Claude Sonnet 5 (claude-sonnet-5)$' <<<"$config_output"
grep -q '^OpenAI root: GPT-5.6 Terra (gpt-5.6-terra\[1m\])$' <<<"$config_output"
grep -q '^Background command: airlock bg -> GPT-5.6 Luna (gpt-5.6-luna\[1m\]) / low effort$' <<<"$config_output"
config_alias_output="$(AIRLOCK_CONFIG_FILE="$custom_config" "$launcher" --config)"
grep -q '^Default profile: openai$' <<<"$config_alias_output"
if grep -q '^Worker descendants:' <<<"$config_output"; then
  printf 'test: config output retained broker-era descendant status\n' >&2
  exit 1
fi

pure_profile="$normal_output" python - <<'PY'
import json
import os
lines = os.environ["pure_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings = json.loads(args[args.index("--settings") + 1])
assert settings["autoMode"]["environment"][0] == "$defaults"
trust_context = settings["autoMode"]["environment"][1]
assert "OpenAI models" in trust_context and "Anthropic Claude" not in trust_context
assert "eligible non-ignored untracked regular files" in trust_context
assert "exact built-in Explore, Plan, and general-purpose" in trust_context
assert "Git-ignored or unsafe paths" in trust_context
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-sol", "airlock-terra", "airlock-luna"}
assert agents["airlock-sol"]["model"] == "gpt-5.6-sol[1m]"
assert agents["airlock-terra"]["model"] == "gpt-5.6-terra[1m]"
assert agents["airlock-luna"]["model"] == "gpt-5.6-luna[1m]"
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
guidance = args[args.index("--append-system-prompt") + 1]
assert "claude-api skill is blocked" in guidance
assert "Work directly" in guidance and "Use exact Explore" in guidance and "Use exact Plan" in guidance
assert "Use exact general-purpose" in guidance and "For a Luna army, launch multiple exact airlock-luna" in guidance
assert "Agent calls with `run_in_background: true`" in guidance
assert "useful non-overlapping batch before waiting" in guidance
assert "Luna and eligible Luna Fast Agents run at the session effort unless they are pinned" in guidance
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
grep -q '^ALLOWED_AGENTS=airlock-luna,airlock-sol,airlock-terra$' <<<"$normal_output"
grep -q '^ALLOWED_MODELS=gpt-5.6-luna\[1m\],gpt-5.6-sol\[1m\],gpt-5.6-terra\[1m\]$' <<<"$normal_output"
grep -q '^EXTRA_AGENTS=$' <<<"$normal_output"
grep -q '^EXTRA_MODELS=$' <<<"$normal_output"
grep -q '^AUTH_TOKEN_SET=yes$' <<<"$normal_output"
grep -q '^DEFAULT_OPUS=gpt-5.6-sol\[1m\]$' <<<"$normal_output"
grep -q '^DEFAULT_SONNET=gpt-5.6-sol\[1m\]$' <<<"$normal_output"
grep -q '^MAX_SUBAGENTS=unset$' <<<"$normal_output"
grep -q '^SUBAGENT_MODEL=unset$' <<<"$normal_output"
grep -q '^SPAWN_DEPTH=1$' <<<"$normal_output"

custom_prompt_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" --append-system-prompt 'first custom instruction' --append-system-prompt='second custom instruction' -p test)"
custom_prompt_profile="$custom_prompt_output" python - <<'PY'
import json
import os
lines = os.environ["custom_prompt_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
assert args.count("--append-system-prompt") == 1
prompt = args[args.index("--append-system-prompt") + 1]
assert prompt.startswith("first custom instruction\n\nsecond custom instruction\n\n")
assert prompt.endswith("The claude-api skill is blocked by default because its large attachment can overflow an Agent context during model routing. Native routing through Airlock is not Claude API application development; do not retry that skill unless the session was launched with AIRLOCK_ALLOW_CLAUDE_API_SKILL=1.")
assert args.index("-p") < args.index("--append-system-prompt")
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

hybrid_openai_output="$(AIRLOCK_STUB_INSPECT_ROUTER=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid terra -p test)"
grep -q '^MODEL=unset$' <<<"$hybrid_openai_output"
grep -Eq '^BASE_URL=http://127\.0\.0\.1:[1-9][0-9]*$' <<<"$hybrid_openai_output"
if grep -q '^BASE_URL=http://127\.0\.0\.1:18765$' <<<"$hybrid_openai_output"; then
  printf 'test: hybrid OpenAI root bypassed the native router\n' >&2
  exit 1
fi
grep -q '^OPENAI_BRIDGE=unset$' <<<"$hybrid_openai_output"
grep -q '^ANTHROPIC_BRIDGE=1$' <<<"$hybrid_openai_output"
grep -q '^DEFAULT_OPUS=unset$' <<<"$hybrid_openai_output"
grep -q '^DEFAULT_SONNET=unset$' <<<"$hybrid_openai_output"
grep -q '^AUTH_TOKEN_SET=no$' <<<"$hybrid_openai_output"
grep -q '^ALLOWED_AGENTS=airlock-luna,airlock-opus,airlock-sol,airlock-sonnet,airlock-terra$' <<<"$hybrid_openai_output"
grep -q '^ALLOWED_MODELS=claude-opus-5,claude-sonnet-5,gpt-5.6-luna\[1m\],gpt-5.6-sol\[1m\],gpt-5.6-terra\[1m\]$' <<<"$hybrid_openai_output"
grep -q '^ROUTER_MODELS=claude-opus-5,claude-sonnet-5,gpt-5.6-luna,gpt-5.6-luna\[1m\],gpt-5.6-sol,gpt-5.6-sol\[1m\],gpt-5.6-terra,gpt-5.6-terra\[1m\]$' <<<"$hybrid_openai_output"
hybrid_profile="$hybrid_openai_output" python - <<'PY'
import json
import os
lines = os.environ["hybrid_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings = json.loads(args[args.index("--settings") + 1])
assert settings["autoMode"]["environment"][0] == "$defaults"
trust_context = settings["autoMode"]["environment"][1]
assert "OpenAI models" in trust_context and "Anthropic Claude" in trust_context
assert "eligible non-ignored untracked regular files" in trust_context
assert "exact built-in Explore, Plan, and general-purpose" in trust_context
assert "Git-ignored or unsafe paths" in trust_context
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-opus", "airlock-sonnet"}
assert agents["airlock-opus"]["model"] == "claude-opus-5"
assert all("effort" not in agent for agent in agents.values())
assert "tools" not in agents["airlock-opus"] and "permissionMode" not in agents["airlock-opus"]
assert "airlock-delegate" not in agents["airlock-opus"]["prompt"]
assert "native Claude Code tools" in agents["airlock-opus"]["prompt"]
assert "exact model: claude-opus-5" in agents["airlock-opus"]["description"]
assert "UI/UX design" in agents["airlock-opus"]["description"]
assert "design-system-aligned UI implementation" in agents["airlock-sonnet"]["description"]
assert agents["airlock-sol"]["model"] == "gpt-5.6-sol[1m]"
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
guidance = args[args.index("--append-system-prompt") + 1]
assert "one session-scoped loopback router keeps both providers inside the same Claude Code process" in guidance
assert "Built-in Explore, Plan, and general-purpose inherit the orchestrator model" in guidance
assert "Do not add task kind, risk, or selection markers" in guidance
assert "Preserve the full technical result" in guidance and "result.report" not in guidance
assert "Named airlock-* Agents cannot invoke Agent" in guidance
assert "Keep every fan-out decision at the root" in guidance
assert "never silently retry on a different provider or model" in guidance
assert "Automatic high-volume swarms remain Luna-only" in guidance
assert "Luna and eligible Luna Fast Agents run at the session effort unless they are pinned" in guidance
assert "Difficult implementation shards must have explicit file ownership" in guidance
assert "Luna Fast" in guidance and "Never automatically swarm Sol" in guidance
assert "visual and interaction design is Anthropic-first and Opus-led" in guidance
assert "Prefer airlock-opus" in guidance and "Use airlock-sonnet" in guidance
assert "Start with Opus for design judgment; do not launch both by default" in guidance
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

hybrid_anthropic_output="$(ANTHROPIC_BASE_URL=leak ANTHROPIC_MODEL=leak AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sonnet -p test)"
grep -q '^MODEL=unset$' <<<"$hybrid_anthropic_output"
grep -Eq '^BASE_URL=http://127\.0\.0\.1:[1-9][0-9]*$' <<<"$hybrid_anthropic_output"
if grep -q '^BASE_URL=http://127\.0\.0\.1:18765$' <<<"$hybrid_anthropic_output"; then
  printf 'test: hybrid Anthropic root bypassed the native router\n' >&2
  exit 1
fi
grep -q '^OPENAI_BRIDGE=1$' <<<"$hybrid_anthropic_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$hybrid_anthropic_output"
grep -q '^DEFAULT_OPUS=unset$' <<<"$hybrid_anthropic_output"
grep -q '^DEFAULT_SONNET=unset$' <<<"$hybrid_anthropic_output"
grep -q '^AUTH_TOKEN_SET=no$' <<<"$hybrid_anthropic_output"
grep -q '^ALLOWED_AGENTS=airlock-luna,airlock-opus,airlock-sol,airlock-sonnet,airlock-terra$' <<<"$hybrid_anthropic_output"
grep -q '^ALLOWED_MODELS=claude-opus-5,claude-sonnet-5,gpt-5.6-luna\[1m\],gpt-5.6-sol\[1m\],gpt-5.6-terra\[1m\]$' <<<"$hybrid_anthropic_output"
hybrid_profile="$hybrid_anthropic_output" python - <<'PY'
import json
import os
lines = os.environ["hybrid_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings = json.loads(args[args.index("--settings") + 1])
assert settings["autoMode"]["environment"][0] == "$defaults"
trust_context = settings["autoMode"]["environment"][1]
assert "OpenAI models" in trust_context and "Anthropic Claude" in trust_context
assert "eligible non-ignored untracked regular files" in trust_context
assert "exact built-in Explore, Plan, and general-purpose" in trust_context
assert "Git-ignored or unsafe paths" in trust_context
agents = json.loads(args[args.index("--agents") + 1])
assert set(agents) == {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-opus", "airlock-sonnet"}
assert agents["airlock-sol"]["model"] == "gpt-5.6-sol[1m]"
assert "tools" not in agents["airlock-sol"] and "permissionMode" not in agents["airlock-sol"]
assert "airlock-delegate" not in agents["airlock-sol"]["prompt"]
assert agents["airlock-sonnet"]["model"] == "claude-sonnet-5"
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
assert args[args.index("--model") + 1] == "claude-sonnet-5"
PY

if ANTHROPIC_AUTH_TOKEN=synthetic AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid opus -p test >/dev/null 2>&1; then
  printf 'test: hybrid route accepted an explicit Anthropic credential override\n' >&2
  exit 1
fi

reduced_config="$tmp_dir/reduced.conf"
printf '%s\n' 'AIRLOCK_ANTHROPIC_MODELS=sonnet' 'AIRLOCK_OPENAI_MODELS=sol,luna' 'AIRLOCK_ANTHROPIC_EXTRA_MODELS=' 'AIRLOCK_OPENAI_EXTRA_MODELS=' >"$reduced_config"
reduced_output="$(AIRLOCK_CONFIG_FILE="$reduced_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sol -p test)"
reduced_profile="$reduced_output" python - <<'PY'
import json
import os
lines = os.environ["reduced_profile"].splitlines()
args = json.loads(next(line[len("ARGS_JSON="):] for line in lines if line.startswith("ARGS_JSON=")))
settings = json.loads(args[args.index("--settings") + 1])
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
grep -q '^ALLOWED_MODELS=claude-sonnet-5,gpt-5.6-luna\[1m\],gpt-5.6-sol\[1m\]$' <<<"$reduced_output"

for root_spec in 'sol:gpt-5.6-sol[1m]:openai' 'luna:gpt-5.6-luna[1m]:openai' 'opus:claude-opus-5:anthropic'; do
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
grep -Fq '"--effort", "high", "--model", "claude-opus-5", "--append-system-prompt"' <<<"$bare_hybrid_output"
grep -q '^OPENAI_BRIDGE=1$' <<<"$bare_hybrid_output"
# Claude Code detects effort support for real Claude IDs on its own. Declaring
# capabilities here would disable everything left off the list.
grep -q '^CUSTOM_CAPS=unset$' <<<"$bare_hybrid_output"

hybrid_gpt_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid sol -p test)"
grep -q '^CUSTOM_CAPS=effort,xhigh_effort,max_effort$' <<<"$hybrid_gpt_output"
bare_openai_output="$(AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher")"
grep -Fq '"--model", "gpt-5.6-sol[1m]", "--effort", "high", "--append-system-prompt"' <<<"$bare_openai_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$bare_openai_output"

# Configs without AIRLOCK_DEFAULT_PROFILE preserve the original OpenAI-only
# bare command. New configs can save a hybrid root without changing explicit
# OpenAI aliases or the explicit `airlock openai` command.
saved_hybrid_config="$tmp_dir/saved-hybrid.conf"
cp "$custom_config" "$saved_hybrid_config"
printf '%s\n' 'AIRLOCK_DEFAULT_PROFILE=hybrid' 'AIRLOCK_HYBRID_MODEL=sonnet' >> "$saved_hybrid_config"
saved_hybrid_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q '^OPENAI_BRIDGE=1$' <<<"$saved_hybrid_output"
grep -Fq 'ARG=claude-sonnet-5' <<<"$saved_hybrid_output"
saved_hybrid_command_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid -p test)"
grep -q '^OPENAI_BRIDGE=1$' <<<"$saved_hybrid_command_output"
grep -Fq 'ARG=claude-sonnet-5' <<<"$saved_hybrid_command_output"
saved_openai_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" openai -p test)"
grep -q '^MODEL=gpt-5.6-terra\[1m\]$' <<<"$saved_openai_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$saved_openai_output"
saved_exact_openai_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" openai 'gpt-5.6-luna[1m]' -p test)"
grep -q '^MODEL=gpt-5.6-luna\[1m\]$' <<<"$saved_exact_openai_output"
saved_equals_openai_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" openai '--model=gpt-5.6-sol[1m]' -p test)"
grep -q '^MODEL=gpt-5.6-sol\[1m\]$' <<<"$saved_equals_openai_output"
saved_background_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" background -p test)"
grep -q '^MODEL=gpt-5.6-luna\[1m\]$' <<<"$saved_background_output"
grep -q '^ARG=low$' <<<"$saved_background_output"
saved_alias_output="$(AIRLOCK_CONFIG_FILE="$saved_hybrid_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" luna -p test)"
grep -q '^MODEL=gpt-5.6-luna\[1m\]$' <<<"$saved_alias_output"
grep -q '^ANTHROPIC_BRIDGE=unset$' <<<"$saved_alias_output"

saved_gpt_config="$tmp_dir/saved-hybrid-gpt.conf"
cp "$custom_config" "$saved_gpt_config"
printf '%s\n' 'AIRLOCK_DEFAULT_PROFILE=hybrid' 'AIRLOCK_HYBRID_MODEL=terra' >> "$saved_gpt_config"
saved_gpt_output="$(AIRLOCK_CONFIG_FILE="$saved_gpt_config" AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" -p test)"
grep -q '^ANTHROPIC_BRIDGE=1$' <<<"$saved_gpt_output"
grep -Fq 'ARG=gpt-5.6-terra[1m]' <<<"$saved_gpt_output"

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

if AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" sol-fast -p test >/dev/null 2>&1; then
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
fast_root_output="$(AIRLOCK_ACCESS_FILE="$tmp_dir/fast-access.json" AIRLOCK_PROXY_FAST_CAPABLE=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" sol-fast -p test)"
grep -q '^MODEL=gpt-5.6-sol-fast\[1m\]$' <<<"$fast_root_output"

skill_opt_in_output="$(AIRLOCK_ALLOW_CLAUDE_API_SKILL=1 AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" sol -p test)"
if grep -Fq 'ARG=Skill(claude-api)' <<<"$skill_opt_in_output"; then
  printf 'test: explicit claude-api skill opt-in was ignored\n' >&2
  exit 1
fi

if AIRLOCK_REAL_CLAUDE="$stub" AIRLOCK_SKIP_HEALTH_CHECK=1 "$launcher" hybrid choose </dev/null >/dev/null 2>&1; then
  printf 'test: noninteractive hybrid picker unexpectedly selected a root\n' >&2
  exit 1
fi

printf 'All airlock launcher tests passed.\n'
