#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/airlock-setup-test.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT

config_dir="$tmp_dir/config"

AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --main-model terra \
  --main-effort high \
  --bg-model sol \
  --bg-effort medium \
  --utility-model mini \
  --subagent-effort xhigh \
  --extra-usage never \
  --routing-policy quality \
  --max-agents 4 \
  --swarm-fast on \
  --failover-policy never \
  --claude-plan max5x \
  --openai-capacity 20x \
  --with-agent \
  --no-login \
  --no-service \
  --config-only \
  --yes

config_file="$config_dir/config"
grep -q '^AIRLOCK_MODEL=terra$' "$config_file"
grep -q '^AIRLOCK_MAIN_EFFORT=high$' "$config_file"
grep -q '^AIRLOCK_BG_MODEL=sol$' "$config_file"
grep -q '^AIRLOCK_BG_EFFORT=medium$' "$config_file"
grep -q '^AIRLOCK_SMALL_FAST_MODEL=gpt-5.4-mini\[1m\]$' "$config_file"
grep -q '^AIRLOCK_SUBAGENT_EFFORT=xhigh$' "$config_file"
grep -q '^AIRLOCK_EXTRA_USAGE_POLICY=never$' "$config_file"
grep -q '^AIRLOCK_ROUTING_POLICY=quality$' "$config_file"
grep -q '^AIRLOCK_MAX_CONCURRENT_SUBAGENTS=4$' "$config_file"
grep -q '^AIRLOCK_SWARM_FAST=on$' "$config_file"
grep -q '^AIRLOCK_FAILOVER_POLICY=never$' "$config_file"
if grep -Eq '^AIRLOCK_(DESCENDANT_POLICY|MAX_DESCENDANTS_PER_WORKER|MAX_CONCURRENT_DESCENDANTS|MAX_REPAIR_ROUNDS|ANTHROPIC_DELEGATE_EFFORTS|OPENAI_DELEGATE_EFFORTS)=' "$config_file"; then
  printf 'test: setup retained legacy delegate configuration\n' >&2
  exit 1
fi
grep -q '^AIRLOCK_ANTHROPIC_PLAN=max5x$' "$config_file"
grep -q '^AIRLOCK_OPENAI_CAPACITY=20x$' "$config_file"
grep -q '^AIRLOCK_ANTHROPIC_MODELS=opus,sonnet$' "$config_file"
grep -q '^AIRLOCK_OPENAI_MODELS=sol,terra,luna$' "$config_file"
grep -q '^AIRLOCK_ANTHROPIC_EXTRA_MODELS=fable$' "$config_file"

configured_output="$(
  AIRLOCK_CONFIG_FILE="$config_file" \
  AIRLOCK_OPENAI_DIRECT_AGENTS_FILE="$repo_root/config/openai-direct-agents.json" \
  AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE="$repo_root/config/anthropic-direct-agents.json" \
  AIRLOCK_HYBRID_AGENTS_FILE="$repo_root/config/hybrid-agents.json" \
  AIRLOCK_CLAUDE_AGENTS_FILE="$repo_root/config/claude-agents.json" \
  AIRLOCK_PLUGIN_DIR="$repo_root/plugins/airlock" \
  AIRLOCK_MANAGED_BUNDLE_FILE="$repo_root/config/managed-bundle.json" \
  AIRLOCK_REAL_CLAUDE="$repo_root/tests/stub-claude.sh" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 \
    "$repo_root/bin/airlock" -p test
)"
grep -q '^MODEL=gpt-5.6-terra\[1m\]$' <<<"$configured_output"
grep -q '^SMALL_FAST=gpt-5.4-mini\[1m\]$' <<<"$configured_output"
grep -q '^ARG=high$' <<<"$configured_output"
grep -q '^MAX_SUBAGENTS=4$' <<<"$configured_output"
grep -q '^SPAWN_DEPTH=1$' <<<"$configured_output"

AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --main-model luna \
  --main-effort xhigh \
  --bg-model sol \
  --bg-effort medium \
  --utility-model sol \
  --subagent-effort high \
  --without-agent \
  --no-login \
  --no-service \
  --config-only \
  --yes >/dev/null

grep -q '^AIRLOCK_MODEL=luna$' "$config_file"
grep -q '^AIRLOCK_EXTRA_USAGE_POLICY=never$' "$config_file"
grep -q '^AIRLOCK_ROUTING_POLICY=quality$' "$config_file"
backup_count="$(find "$config_dir" -maxdepth 1 -name 'config.backup-*' | wc -l | tr -d ' ')"
test "$backup_count" -eq 1

before_hash="$(cksum "$config_file")"
if AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --main-effort impossible --config-only --yes >/dev/null 2>&1; then
  printf 'test: invalid effort unexpectedly succeeded\n' >&2
  exit 1
fi
after_hash="$(cksum "$config_file")"
test "$before_hash" = "$after_hash"

if AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --extra-usage bill-me --config-only --yes >/dev/null 2>&1; then
  printf 'test: invalid extra-usage policy unexpectedly succeeded\n' >&2
  exit 1
fi
test "$before_hash" = "$(cksum "$config_file")"

if AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --swarm-fast always --config-only --yes >/dev/null 2>&1; then
  printf 'test: invalid swarm Fast policy unexpectedly succeeded\n' >&2
  exit 1
fi
test "$before_hash" = "$(cksum "$config_file")"

if AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --anthropic-workers opus,unknown --config-only --yes >/dev/null 2>&1; then
  printf 'test: invalid Anthropic worker pool unexpectedly succeeded\n' >&2
  exit 1
fi
test "$before_hash" = "$(cksum "$config_file")"

custom_dir="$tmp_dir/custom-config"
AIRLOCK_CONFIG_DIR="$custom_dir" "$repo_root/scripts/setup.sh" \
  --anthropic-workers sonnet,haiku \
  --openai-workers sol,luna \
  --config-only --yes >/dev/null
grep -q '^AIRLOCK_ANTHROPIC_MODELS=sonnet,haiku$' "$custom_dir/config"
grep -q '^AIRLOCK_OPENAI_MODELS=sol,luna$' "$custom_dir/config"
grep -q '^AIRLOCK_MAX_CONCURRENT_SUBAGENTS=off$' "$custom_dir/config"
grep -q '^AIRLOCK_SWARM_FAST=auto$' "$custom_dir/config"

detected_dir="$tmp_dir/detected-config"
mkdir -p "$detected_dir"
printf 'AIRLOCK_ANTHROPIC_PLAN=unknown\n' > "$detected_dir/config"
cat > "$detected_dir/access.json" <<'EOF'
{
  "schema_version": 2,
  "providers": {
    "anthropic": {
      "authenticated": true,
      "detected_plan": "max20x",
      "plan_source": "status_field"
    }
  }
}
EOF
AIRLOCK_CONFIG_DIR="$detected_dir" "$repo_root/scripts/setup.sh" \
  --config-only --yes >/dev/null
grep -q '^AIRLOCK_ANTHROPIC_PLAN=max20x$' "$detected_dir/config"

printf 'All setup wizard tests passed.\n'
