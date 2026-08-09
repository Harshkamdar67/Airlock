#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/airlock-setup-test.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT

config_dir="$tmp_dir/config"

AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --main-model terra \
  --main-effort high \
  --worker-effort inherit \
  --worker-pins luna=max,sonnet=high \
  --bg-model sol \
  --bg-effort medium \
  --utility-model mini \
  --subagent-effort xhigh \
  --extra-usage never \
  --routing-policy quality \
  --max-agents 4 \
  --fast openai \
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
grep -q '^AIRLOCK_DEFAULT_PROFILE=openai$' "$config_file"
grep -q '^AIRLOCK_HYBRID_MODEL=sol$' "$config_file"
grep -q '^AIRLOCK_MODEL=terra$' "$config_file"
grep -q '^AIRLOCK_MAIN_EFFORT=high$' "$config_file"
grep -q '^AIRLOCK_WORKER_EFFORT=inherit$' "$config_file"
grep -q '^AIRLOCK_EFFORT_LUNA=max$' "$config_file"
grep -q '^AIRLOCK_EFFORT_SONNET=high$' "$config_file"
grep -q '^AIRLOCK_BG_MODEL=sol$' "$config_file"
grep -q '^AIRLOCK_BG_EFFORT=medium$' "$config_file"
grep -q '^AIRLOCK_SMALL_FAST_MODEL=gpt-5.4-mini$' "$config_file"
grep -q '^AIRLOCK_SUBAGENT_EFFORT=xhigh$' "$config_file"
grep -q '^AIRLOCK_EXTRA_USAGE_POLICY=never$' "$config_file"
grep -q '^AIRLOCK_ROUTING_POLICY=quality$' "$config_file"
grep -q '^AIRLOCK_MAX_CONCURRENT_SUBAGENTS=4$' "$config_file"
grep -q '^AIRLOCK_OPENAI_FAST=on$' "$config_file"
grep -q '^AIRLOCK_ANTHROPIC_FAST=off$' "$config_file"
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
grep -q '^AIRLOCK_GPT_EFFORT_CAPABILITIES=effort,xhigh_effort,max_effort$' "$config_file"

configured_output="$(
  AIRLOCK_CONFIG_FILE="$config_file" \
  AIRLOCK_OPENAI_DIRECT_AGENTS_FILE="$repo_root/config/openai-direct-agents.json" \
  AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE="$repo_root/config/anthropic-direct-agents.json" \
  AIRLOCK_HYBRID_AGENTS_FILE="$repo_root/config/hybrid-agents.json" \
  AIRLOCK_CLAUDE_AGENTS_FILE="$repo_root/config/claude-agents.json" \
  AIRLOCK_GROK_DIRECT_AGENTS_FILE="$repo_root/config/grok-agents.json" \
  AIRLOCK_PLUGIN_DIR="$repo_root/plugins/airlock" \
  AIRLOCK_MANAGED_BUNDLE_FILE="$repo_root/config/managed-bundle.json" \
  AIRLOCK_REAL_CLAUDE="$repo_root/tests/stub-claude.sh" \
  AIRLOCK_SKIP_HEALTH_CHECK=1 \
    "$repo_root/bin/airlock" -p test
)"
grep -q '^MODEL=gpt-5.6-terra$' <<<"$configured_output"
grep -q '^SMALL_FAST=gpt-5.4-mini$' <<<"$configured_output"
grep -q '^ARG=high$' <<<"$configured_output"
grep -q '^MAX_SUBAGENTS=4$' <<<"$configured_output"
grep -q '^SPAWN_DEPTH=1$' <<<"$configured_output"

AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --main-model 'gpt-5.6-luna[1m]' \
  --main-effort xhigh \
  --worker-effort high \
  --worker-pins '' \
  --bg-model 'gpt-5.6-sol[1m]' \
  --bg-effort medium \
  --utility-model 'gpt-5.4-mini[1m]' \
  --subagent-effort high \
  --without-agent \
  --no-login \
  --no-service \
  --config-only \
  --yes >/dev/null

grep -q '^AIRLOCK_MODEL=luna$' "$config_file"
grep -q '^AIRLOCK_WORKER_EFFORT=high$' "$config_file"
if grep -q '^AIRLOCK_EFFORT_' "$config_file"; then
  printf 'test: explicit empty worker pins did not clear saved pins\n' >&2
  exit 1
fi
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
  --fast all --extra-usage never --config-only --yes >/dev/null 2>&1; then
  printf 'test: Anthropic Fast accepted a blocked extra-usage policy\n' >&2
  exit 1
fi
test "$before_hash" = "$(cksum "$config_file")"

if AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --openai-fast maybe --config-only --yes >/dev/null 2>&1; then
  printf 'test: invalid OpenAI Fast policy unexpectedly succeeded\n' >&2
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

if AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/setup.sh" \
  --worker-pins luna=max,luna=high --config-only --yes >/dev/null 2>&1; then
  printf 'test: duplicate worker pin unexpectedly succeeded\n' >&2
  exit 1
fi
test "$before_hash" = "$(cksum "$config_file")"

hybrid_dir="$tmp_dir/hybrid-config"
AIRLOCK_CONFIG_DIR="$hybrid_dir" "$repo_root/scripts/setup.sh" \
  --default-profile hybrid \
  --hybrid-model opus \
  --main-effort xhigh \
  --worker-effort inherit \
  --worker-pins luna=max \
  --anthropic-workers sonnet \
  --openai-workers '' \
  --without-agent \
  --no-login \
  --no-service \
  --config-only \
  --yes >"$tmp_dir/hybrid-summary.out"
grep -q '^  Default command:    airlock -> Claude Opus 5 (claude-opus-5\[1m\])$' "$tmp_dir/hybrid-summary.out"
grep -q '^  Session profile:    hybrid: Claude and GPT workers$' "$tmp_dir/hybrid-summary.out"
grep -q '^  Worker effort:      follow session; per-model pins: luna=max$' "$tmp_dir/hybrid-summary.out"
grep -q '^AIRLOCK_DEFAULT_PROFILE=hybrid$' "$hybrid_dir/config"
grep -q '^AIRLOCK_HYBRID_MODEL=opus$' "$hybrid_dir/config"
grep -q '^AIRLOCK_WORKER_EFFORT=inherit$' "$hybrid_dir/config"
grep -q '^AIRLOCK_EFFORT_LUNA=max$' "$hybrid_dir/config"
grep -q '^AIRLOCK_ANTHROPIC_MODELS=sonnet,opus$' "$hybrid_dir/config"
grep -q '^AIRLOCK_OPENAI_MODELS=$' "$hybrid_dir/config"

new_default_dir="$tmp_dir/new-default-config"
AIRLOCK_CONFIG_DIR="$new_default_dir" "$repo_root/scripts/setup.sh" \
  --no-login --no-service --config-only --yes >"$tmp_dir/new-default-summary.out"
grep -q '^  Generic worker:     no (effort: inherit)$' "$tmp_dir/new-default-summary.out"
grep -q '^AIRLOCK_DEFAULT_PROFILE=hybrid$' "$new_default_dir/config"
grep -q '^AIRLOCK_HYBRID_MODEL=sol$' "$new_default_dir/config"
grep -q '^AIRLOCK_WORKER_EFFORT=inherit$' "$new_default_dir/config"
grep -q '^AIRLOCK_OPENAI_FAST=off$' "$new_default_dir/config"
grep -q '^AIRLOCK_ANTHROPIC_FAST=off$' "$new_default_dir/config"

legacy_dir="$tmp_dir/legacy-config"
mkdir -p "$legacy_dir"
printf '%s\n' 'AIRLOCK_MODEL=terra' 'AIRLOCK_MAIN_EFFORT=high' > "$legacy_dir/config"
AIRLOCK_CONFIG_DIR="$legacy_dir" "$repo_root/scripts/setup.sh" \
  --without-agent --no-login --no-service --config-only --yes >/dev/null
grep -q '^AIRLOCK_DEFAULT_PROFILE=openai$' "$legacy_dir/config"
grep -q '^AIRLOCK_MODEL=terra$' "$legacy_dir/config"

for invalid_case in \
  '--default-profile invalid' \
  '--hybrid-model invalid' \
  '--worker-effort impossible' \
  '--worker-pins unknown=max' \
  '--worker-pins luna=impossible'; do
  set -- $invalid_case
  if AIRLOCK_CONFIG_DIR="$tmp_dir/invalid-new-options" "$repo_root/scripts/setup.sh" \
    "$1" "$2" --config-only --yes >/dev/null 2>&1; then
    printf 'test: invalid new setup option unexpectedly succeeded: %s\n' "$invalid_case" >&2
    exit 1
  fi
done

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

# Configuration-only mode must not require or invoke Claude Code, the proxy,
# either login, or a service. These stubs fail if setup touches them.
config_only_stub_dir="$tmp_dir/config-only-stubs"
mkdir -p "$config_only_stub_dir"
for name in claude claude-code-proxy; do
  cat > "$config_only_stub_dir/$name" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$0 $*" >> "$AIRLOCK_TEST_COMMAND_LOG"
exit 99
EOF
  chmod 0755 "$config_only_stub_dir/$name"
done
config_only_log="$tmp_dir/config-only-commands.log"
AIRLOCK_TEST_COMMAND_LOG="$config_only_log" \
PATH="$config_only_stub_dir:$PATH" \
AIRLOCK_CONFIG_DIR="$tmp_dir/config-only-no-tools" \
  "$repo_root/scripts/setup.sh" --config-only --no-login --no-service --yes >/dev/null
test ! -e "$config_only_log"

fallback_home="$tmp_dir/fallback-home"
mkdir -p "$fallback_home"
printf 'blocked default config parent\n' > "$fallback_home/.config"
HOME="$fallback_home" AIRLOCK_CONFIG_DIR='' XDG_CONFIG_HOME='' \
  "$repo_root/scripts/setup.sh" \
  --config-only --no-login --no-service --yes > "$tmp_dir/fallback.out"
test -f "$fallback_home/.airlock/config"
grep -q '^  Proxy storage:      private writable Airlock fallback$' "$tmp_dir/fallback.out"
grep -q '^AIRLOCK_DEFAULT_PROFILE=hybrid$' "$fallback_home/.airlock/config"
grep -q "^AIRLOCK_PROXY_CONFIG_DIR=$fallback_home/.airlock/claude-code-proxy$" "$fallback_home/.airlock/config"
grep -q '^AIRLOCK_PROXY_STATE_HOME=$' "$fallback_home/.airlock/config"

# An existing config that never had AIRLOCK_HYBRID_MODEL must keep the value
# both launchers already fall back to. Writing anything else would silently
# move the hybrid root the first time setup rewrote the file.
drift_dir="$tmp_dir/hybrid-default-drift"
mkdir -p "$drift_dir"
printf 'AIRLOCK_MODEL=sol\n' > "$drift_dir/config"
AIRLOCK_CONFIG_DIR="$drift_dir" "$repo_root/scripts/setup.sh" \
  --config-only --no-login --no-service --yes >/dev/null
grep -q '^AIRLOCK_HYBRID_MODEL=sonnet$' "$drift_dir/config"
launcher_default="$(sed -n 's/.*config_hybrid_model:-\([a-z]*\).*/\1/p' "$repo_root/bin/airlock" | head -1)"
if [[ "$launcher_default" != 'sonnet' ]]; then
  printf 'test: bin/airlock hybrid fallback drifted from setup.sh: %s\n' "$launcher_default" >&2
  exit 1
fi

# Grok is opt-in everywhere except an explicit Grok profile.
grok_off_dir="$tmp_dir/grok-off"
AIRLOCK_CONFIG_DIR="$grok_off_dir" "$repo_root/scripts/setup.sh" \
  --config-only --no-login --no-service --yes --default-profile hybrid >/dev/null
grep -q '^AIRLOCK_GROK_MODELS=$' "$grok_off_dir/config"

grok_profile_dir="$tmp_dir/grok-profile"
AIRLOCK_CONFIG_DIR="$grok_profile_dir" "$repo_root/scripts/setup.sh" \
  --config-only --no-login --no-service --yes --default-profile grok >/dev/null
grep -q '^AIRLOCK_DEFAULT_PROFILE=grok$' "$grok_profile_dir/config"
grep -q '^AIRLOCK_GROK_MODEL=grok$' "$grok_profile_dir/config"
grep -q '^AIRLOCK_GROK_MODELS=grok,composer$' "$grok_profile_dir/config"

grok_workers_dir="$tmp_dir/grok-workers"
AIRLOCK_CONFIG_DIR="$grok_workers_dir" "$repo_root/scripts/setup.sh" \
  --config-only --no-login --no-service --yes \
  --default-profile hybrid --grok-workers grok,composer >/dev/null
grep -q '^AIRLOCK_GROK_MODELS=grok,composer$' "$grok_workers_dir/config"

# A saved Grok root that the worker pool omits could not start, so setup keeps
# the root inside the pool rather than writing an unlaunchable config.
grok_coerce_dir="$tmp_dir/grok-coerce"
AIRLOCK_CONFIG_DIR="$grok_coerce_dir" "$repo_root/scripts/setup.sh" \
  --config-only --no-login --no-service --yes \
  --default-profile grok --grok-model composer --grok-workers grok >/dev/null
grep -q '^AIRLOCK_GROK_MODEL=composer$' "$grok_coerce_dir/config"
grep -q '^AIRLOCK_GROK_MODELS=grok,composer$' "$grok_coerce_dir/config"

for invalid in '--grok-model bogus' '--grok-workers sonnet'; do
  # shellcheck disable=SC2086
  if AIRLOCK_CONFIG_DIR="$tmp_dir/grok-invalid" "$repo_root/scripts/setup.sh" \
    --config-only --no-login --no-service --yes $invalid >/dev/null 2>&1; then
    printf 'test: setup accepted invalid option: %s\n' "$invalid" >&2
    exit 1
  fi
done

printf 'All setup wizard tests passed.\n'
