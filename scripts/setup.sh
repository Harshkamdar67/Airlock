#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_dir="${AIRLOCK_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/airlock}"
config_target="$config_dir/config"

main_model=''
main_effort=''
bg_model=''
bg_effort=''
utility_model=''
subagent_effort=''
extra_usage_policy=''
routing_policy=''
max_agents=''
swarm_fast=''
failover_policy=''
claude_plan=''
openai_capacity=''
anthropic_models=''
openai_models=''
install_agent=''
run_login=''
start_service=''
assume_yes=0
config_only=0

usage() {
  cat <<'EOF'
Usage: ./scripts/setup.sh [options]

Without options, setup.sh opens an interactive questionnaire.

Options:
  --main-model MODEL          Root model alias
  --main-effort EFFORT       Root effort: low, medium, high, xhigh, or max
  --bg-model MODEL            Model used by the airlock bg lane
  --bg-effort EFFORT         Effort used by the airlock bg lane
  --utility-model MODEL       Model for titles, token counts, and utility requests
  --subagent-effort EFFORT   Effort for the optional custom worker
  --extra-usage POLICY       Extra-usage workers: ask, never, or allow
  --routing-policy POLICY    Routing objective: balanced, quality, or economy
  --max-agents VALUE         Concurrent top-level workers: off or 1..20
  --swarm-fast POLICY       Luna swarm Fast processing: auto, on, or off
  --failover-policy POLICY   Worker failover: ask, never, or allow
  --claude-plan PLAN         Claude tier: unknown, pro, max5x, or max20x
  --openai-capacity VALUE    Codex capacity override: auto, 1x, 5x, or 20x
  --anthropic-workers LIST   Enabled Claude workers: opus,sonnet,fable,haiku
  --openai-workers LIST      Enabled GPT workers: sol,terra,luna
  --with-agent               Install the custom worker
  --without-agent            Do not install the custom worker
  --login                    Run browser OAuth if authentication is missing
  --no-login                 Do not start browser OAuth
  --start-service            Start the Homebrew background service
  --no-service               Do not start the Homebrew background service
  --config-only              Write configuration without installing anything
  -y, --yes                  Accept the summary without a final prompt
  -h, --help                 Show this help

Model aliases:
  sol, sol-fast, terra, luna, 5.5, 5.4, mini, 5.3, spark, 5.2
EOF
}

require_value() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    printf 'setup: %s requires a value\n' "$1" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --main-model) require_value "$@"; main_model="$2"; shift ;;
    --main-effort) require_value "$@"; main_effort="$2"; shift ;;
    --bg-model) require_value "$@"; bg_model="$2"; shift ;;
    --bg-effort) require_value "$@"; bg_effort="$2"; shift ;;
    --utility-model) require_value "$@"; utility_model="$2"; shift ;;
    --subagent-effort) require_value "$@"; subagent_effort="$2"; shift ;;
    --extra-usage) require_value "$@"; extra_usage_policy="$2"; shift ;;
    --routing-policy) require_value "$@"; routing_policy="$2"; shift ;;
    --max-agents) require_value "$@"; max_agents="$2"; shift ;;
    --swarm-fast) require_value "$@"; swarm_fast="$2"; shift ;;
    --failover-policy) require_value "$@"; failover_policy="$2"; shift ;;
    --claude-plan) require_value "$@"; claude_plan="$2"; shift ;;
    --openai-capacity) require_value "$@"; openai_capacity="$2"; shift ;;
    --anthropic-workers) require_value "$@"; anthropic_models="$2"; shift ;;
    --openai-workers) require_value "$@"; openai_models="$2"; shift ;;
    --with-agent) install_agent='yes' ;;
    --without-agent) install_agent='no' ;;
    --login) run_login='yes' ;;
    --no-login) run_login='no' ;;
    --start-service) start_service='yes' ;;
    --no-service) start_service='no' ;;
    --config-only) config_only=1 ;;
    -y|--yes) assume_yes=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'setup: unknown option %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

read_config_value() {
  local wanted_key="$1"
  local fallback="$2"
  local key value
  CONFIG_VALUE="$fallback"
  if [[ ! -f "$config_target" ]]; then
    return
  fi
  while IFS='=' read -r key value; do
    if [[ "$key" == "$wanted_key" ]]; then
      CONFIG_VALUE="${value%$'\r'}"
    fi
  done < "$config_target"
}

read_config_value AIRLOCK_MODEL sol
default_main_model="$CONFIG_VALUE"
read_config_value AIRLOCK_MAIN_EFFORT xhigh
default_main_effort="$CONFIG_VALUE"
read_config_value AIRLOCK_BG_MODEL sol
default_bg_model="$CONFIG_VALUE"
read_config_value AIRLOCK_BG_EFFORT medium
default_bg_effort="$CONFIG_VALUE"
read_config_value AIRLOCK_SMALL_FAST_MODEL 'gpt-5.6-sol[1m]'
default_utility_wire="$CONFIG_VALUE"
read_config_value AIRLOCK_SUBAGENT_EFFORT high
default_subagent_effort="$CONFIG_VALUE"
read_config_value AIRLOCK_EXTRA_USAGE_POLICY ask
default_extra_usage_policy="$CONFIG_VALUE"
read_config_value AIRLOCK_ROUTING_POLICY balanced
default_routing_policy="$CONFIG_VALUE"
read_config_value AIRLOCK_MAX_CONCURRENT_SUBAGENTS off
default_max_agents="$CONFIG_VALUE"
read_config_value AIRLOCK_SWARM_FAST auto
default_swarm_fast="$CONFIG_VALUE"
read_config_value AIRLOCK_FAILOVER_POLICY ask
default_failover_policy="$CONFIG_VALUE"
read_config_value AIRLOCK_ANTHROPIC_PLAN unknown
default_claude_plan="$CONFIG_VALUE"
read_config_value AIRLOCK_OPENAI_CAPACITY auto
default_openai_capacity="$CONFIG_VALUE"
read_config_value AIRLOCK_ANTHROPIC_MODELS 'opus,sonnet'
default_anthropic_models="$CONFIG_VALUE"
read_config_value AIRLOCK_OPENAI_MODELS 'sol,terra,luna'
default_openai_models="$CONFIG_VALUE"
read_config_value AIRLOCK_ANTHROPIC_EXTRA_MODELS fable
anthropic_extra_models="$CONFIG_VALUE"
read_config_value AIRLOCK_OPENAI_EXTRA_MODELS ''
openai_extra_models="$CONFIG_VALUE"

claude_plan_was_detected=0
if [[ "$default_claude_plan" == 'unknown' ]]; then
  setup_python=''
  if command -v python >/dev/null 2>&1; then
    setup_python="$(command -v python)"
  elif command -v python3 >/dev/null 2>&1; then
    setup_python="$(command -v python3)"
  fi
  if [[ -n "$setup_python" && -f "$repo_root/bin/airlock-access.py" ]]; then
    detected_claude_plan="$(AIRLOCK_CONFIG_FILE="$config_target" AIRLOCK_ACCESS_FILE="$config_dir/access.json" "$setup_python" "$repo_root/bin/airlock-access.py" capacity-tier anthropic 2>/dev/null || true)"
    case "$detected_claude_plan" in
      pro|max5x|max20x)
        default_claude_plan="$detected_claude_plan"
        claude_plan_was_detected=1
        ;;
    esac
  fi
fi

utility_alias_from_wire() {
  case "$1" in
    'gpt-5.6-sol[1m]'|gpt-5.6-sol) UTILITY_ALIAS='sol' ;;
    'gpt-5.6-terra[1m]'|gpt-5.6-terra) UTILITY_ALIAS='terra' ;;
    'gpt-5.6-luna[1m]'|gpt-5.6-luna) UTILITY_ALIAS='luna' ;;
    'gpt-5.5[1m]'|gpt-5.5) UTILITY_ALIAS='5.5' ;;
    'gpt-5.4[1m]'|gpt-5.4) UTILITY_ALIAS='5.4' ;;
    'gpt-5.4-mini[1m]'|gpt-5.4-mini) UTILITY_ALIAS='mini' ;;
    'gpt-5.3-codex[1m]'|gpt-5.3-codex) UTILITY_ALIAS='5.3' ;;
    gpt-5.3-codex-spark) UTILITY_ALIAS='spark' ;;
    'gpt-5.2[1m]'|gpt-5.2) UTILITY_ALIAS='5.2' ;;
    *) UTILITY_ALIAS='sol' ;;
  esac
}

wire_model_from_alias() {
  case "$1" in
    sol) WIRE_MODEL='gpt-5.6-sol[1m]' ;;
    sol-fast) WIRE_MODEL='gpt-5.6-sol-fast[1m]' ;;
    terra) WIRE_MODEL='gpt-5.6-terra[1m]' ;;
    luna) WIRE_MODEL='gpt-5.6-luna[1m]' ;;
    5.5) WIRE_MODEL='gpt-5.5[1m]' ;;
    5.4) WIRE_MODEL='gpt-5.4[1m]' ;;
    mini) WIRE_MODEL='gpt-5.4-mini[1m]' ;;
    5.3) WIRE_MODEL='gpt-5.3-codex[1m]' ;;
    spark) WIRE_MODEL='gpt-5.3-codex-spark' ;;
    5.2) WIRE_MODEL='gpt-5.2[1m]' ;;
    *) return 1 ;;
  esac
}

validate_model() {
  wire_model_from_alias "$1" || {
    printf 'setup: unsupported model alias: %s\n' "$1" >&2
    exit 2
  }
}

validate_effort() {
  case "$1" in
    low|medium|high|xhigh|max) ;;
    *)
      printf 'setup: unsupported effort: %s\n' "$1" >&2
      exit 2
      ;;
  esac
}

validate_extra_usage_policy() {
  case "$1" in
    ask|never|allow) ;;
    *) printf 'setup: unsupported extra-usage policy: %s\n' "$1" >&2; exit 2 ;;
  esac
}

validate_routing_policy() {
  case "$1" in
    balanced|quality|economy) ;;
    *) printf 'setup: unsupported routing policy: %s\n' "$1" >&2; exit 2 ;;
  esac
}

validate_max_agents() {
  if [[ "$1" == 'off' ]]; then return; fi
  if [[ ! "$1" =~ ^[1-9][0-9]?$ ]] || (( 10#$1 > 20 )); then
    printf 'setup: max agents must be off or an integer from 1 to 20\n' >&2
    exit 2
  fi
}

validate_swarm_fast() {
  case "$1" in
    auto|on|off) ;;
    *) printf 'setup: swarm Fast policy must be auto, on, or off\n' >&2; exit 2 ;;
  esac
}

validate_failover_policy() {
  case "$1" in
    ask|never|allow) ;;
    *) printf 'setup: unsupported failover policy: %s\n' "$1" >&2; exit 2 ;;
  esac
}

validate_claude_plan() {
  case "$1" in
    unknown|pro|max5x|max20x) ;;
    *) printf 'setup: unsupported Claude plan: %s\n' "$1" >&2; exit 2 ;;
  esac
}

validate_openai_capacity() {
  case "$1" in
    auto|1x|5x|20x) ;;
    *) printf 'setup: unsupported OpenAI capacity: %s\n' "$1" >&2; exit 2 ;;
  esac
}

validate_csv_subset() {
  local label="$1"
  local value="$2"
  local allowed="$3"
  local item
  local -a items
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
    printf 'setup: invalid %s list\n' "$label" >&2
    exit 2
  }
  IFS=',' read -r -a items <<< "$value"
  for item in "${items[@]}"; do
    [[ -n "$item" && ",$allowed," == *",$item,"* ]] || {
      printf 'setup: unsupported %s value: %s\n' "$label" "$item" >&2
      exit 2
    }
  done
}

choose_option() {
  local label="$1"
  local recommended="$2"
  shift 2
  local options=("$@")
  local answer option index

  while true; do
    printf '\n%s\n' "$label"
    index=1
    for option in "${options[@]}"; do
      if [[ "$option" == "$recommended" ]]; then
        printf '  %d) %s (current/default)\n' "$index" "$option"
      else
        printf '  %d) %s\n' "$index" "$option"
      fi
      index=$((index + 1))
    done
    printf 'Choose [%s]: ' "$recommended"
    IFS= read -r answer
    if [[ -z "$answer" ]]; then
      CHOICE="$recommended"
      return
    fi
    if [[ "$answer" =~ ^[0-9]+$ ]]; then
      index=$((10#$answer))
      if (( index >= 1 && index <= ${#options[@]} )); then
        CHOICE="${options[$((index - 1))]}"
        return
      fi
    fi
    for option in "${options[@]}"; do
      if [[ "$answer" == "$option" ]]; then
        CHOICE="$option"
        return
      fi
    done
    printf 'Please enter a listed number or value.\n' >&2
  done
}

choose_csv_value() {
  local label="$1"
  local current="$2"
  local answer
  printf '\n%s\n' "$label"
  printf 'Comma-separated routes [%s]: ' "$current"
  IFS= read -r answer
  CSV_CHOICE="${answer:-$current}"
}

ask_yes_no() {
  local label="$1"
  local recommended="$2"
  local answer prompt
  if [[ "$recommended" == 'yes' ]]; then prompt='Y/n'; else prompt='y/N'; fi
  while true; do
    printf '%s [%s]: ' "$label" "$prompt"
    IFS= read -r answer
    answer="${answer:-$recommended}"
    case "$answer" in
      y|Y|yes|YES|Yes) ANSWER='yes'; return ;;
      n|N|no|NO|No) ANSWER='no'; return ;;
      *) printf 'Please answer yes or no.\n' >&2 ;;
    esac
  done
}

utility_alias_from_wire "$default_utility_wire"
default_utility_model="$UTILITY_ALIAS"

if [[ "$assume_yes" -eq 0 && ! -t 0 ]]; then
  printf 'setup: interactive mode needs a terminal. Use explicit options with --yes.\n' >&2
  exit 2
fi

printf 'Airlock setup wizard\n'
printf 'This stores only noncredential model, effort, routing, capacity, and worker-limit preferences. OAuth tokens never enter the config.\n'

if [[ -z "$main_model" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then main_model="$default_main_model"; else
    choose_option '1. Main model for normal airlock sessions' "$default_main_model" sol sol-fast terra luna 5.5 5.4 mini 5.3 spark 5.2
    main_model="$CHOICE"
  fi
fi

if [[ -z "$main_effort" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then main_effort="$default_main_effort"; else
    choose_option '2. Main-session reasoning effort' "$default_main_effort" low medium high xhigh max
    main_effort="$CHOICE"
  fi
fi

if [[ -z "$bg_model" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then bg_model="$default_bg_model"; else
    choose_option '3. Model for the airlock bg lane' "$default_bg_model" sol sol-fast terra luna 5.5 5.4 mini 5.3 spark 5.2
    bg_model="$CHOICE"
  fi
fi

if [[ -z "$bg_effort" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then bg_effort="$default_bg_effort"; else
    choose_option '4. Reasoning effort for the airlock bg lane' "$default_bg_effort" low medium high xhigh max
    bg_effort="$CHOICE"
  fi
fi

if [[ -z "$utility_model" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then utility_model="$default_utility_model"; else
    choose_option '5. Utility model for titles, token counts, and small background requests' "$default_utility_model" sol terra luna 5.5 5.4 mini 5.3 spark 5.2
    utility_model="$CHOICE"
  fi
fi

if [[ -z "$subagent_effort" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then subagent_effort="$default_subagent_effort"; else
    choose_option '6. Effort for the optional custom sub-agent' "$default_subagent_effort" low medium high xhigh max
    subagent_effort="$CHOICE"
  fi
fi

if [[ -z "$anthropic_models" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then anthropic_models="$default_anthropic_models"; else
    choose_csv_value '7. Enabled Claude subagents (Fable is off by default; root/orchestrator selection is separate)' "$default_anthropic_models"
    anthropic_models="$CSV_CHOICE"
  fi
fi

if [[ -z "$openai_models" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then openai_models="$default_openai_models"; else
    choose_csv_value '8. Enabled GPT subagents' "$default_openai_models"
    openai_models="$CSV_CHOICE"
  fi
fi

if [[ -z "$extra_usage_policy" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then extra_usage_policy="$default_extra_usage_policy"; else
    choose_option '9. Extra-usage model policy' "$default_extra_usage_policy" ask never allow
    extra_usage_policy="$CHOICE"
  fi
fi

if [[ -z "$routing_policy" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then routing_policy="$default_routing_policy"; else
    choose_option '10. Cross-provider routing objective' "$default_routing_policy" balanced quality economy
    routing_policy="$CHOICE"
  fi
fi

if [[ -z "$max_agents" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then max_agents="$default_max_agents"; else
    choose_option '11. Maximum concurrent managed subagents (off restores Claude Code default)' "$default_max_agents" 1 2 3 4 5 10 20 off
    max_agents="$CHOICE"
  fi
fi

if [[ -z "$swarm_fast" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then swarm_fast="$default_swarm_fast"; else
    choose_option '12. Luna swarm Fast processing (auto requires an eligible OpenAI plan)' "$default_swarm_fast" auto on off
    swarm_fast="$CHOICE"
  fi
fi

# Native named Agents do not spawn descendants. Fan-out stays at the root.
failover_policy="${failover_policy:-$default_failover_policy}"

if [[ -z "$claude_plan" ]]; then
  if [[ "$claude_plan_was_detected" -eq 1 ]]; then
    claude_plan="$default_claude_plan"
    printf '\n13. Claude subscription tier: %s (safely detected from sanitized cached metadata)\n' "$claude_plan"
  elif [[ "$assume_yes" -eq 1 ]]; then claude_plan="$default_claude_plan"; else
    choose_option '13. Claude subscription tier (choose unknown unless you know it)' "$default_claude_plan" unknown pro max5x max20x
    claude_plan="$CHOICE"
  fi
fi

if [[ -z "$openai_capacity" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then openai_capacity="$default_openai_capacity"; else
    choose_option '14. OpenAI capacity override (auto uses clearly labelled inferred mappings)' "$default_openai_capacity" auto 1x 5x 20x
    openai_capacity="$CHOICE"
  fi
fi

if [[ -z "$install_agent" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then install_agent='yes'; else
    ask_yes_no '15. Install the custom background sub-agent?' yes
    install_agent="$ANSWER"
  fi
fi

if [[ -z "$run_login" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then run_login='yes'; else
    ask_yes_no '16. Open browser OAuth during installation if needed?' yes
    run_login="$ANSWER"
  fi
fi

if [[ -z "$start_service" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then start_service='yes'; else
    ask_yes_no '17. Start the proxy automatically as a background service?' yes
    start_service="$ANSWER"
  fi
fi

validate_model "$main_model"
validate_model "$bg_model"
validate_model "$utility_model"
validate_effort "$main_effort"
validate_effort "$bg_effort"
validate_effort "$subagent_effort"
validate_extra_usage_policy "$extra_usage_policy"
validate_routing_policy "$routing_policy"
validate_max_agents "$max_agents"
validate_swarm_fast "$swarm_fast"
validate_failover_policy "$failover_policy"
validate_claude_plan "$claude_plan"
validate_openai_capacity "$openai_capacity"
validate_csv_subset 'Anthropic model' "$anthropic_models" 'opus,sonnet,fable,haiku'
validate_csv_subset 'OpenAI model' "$openai_models" 'sol,terra,luna'
if [[ -n "$anthropic_extra_models" ]]; then
  validate_csv_subset 'Anthropic extra model' "$anthropic_extra_models" 'opus,sonnet,fable,haiku'
fi
if [[ -n "$openai_extra_models" ]]; then
  validate_csv_subset 'OpenAI extra model' "$openai_extra_models" 'sol,terra,luna'
fi
wire_model_from_alias "$utility_model"
utility_wire_model="$WIRE_MODEL"

printf '\nConfiguration summary\n'
printf '  Main session:      %s / %s effort\n' "$main_model" "$main_effort"
printf '  Background lane:  %s / %s effort\n' "$bg_model" "$bg_effort"
printf '  Utility requests: %s\n' "$utility_model"
printf '  Custom sub-agent: %s (effort: %s)\n' "$install_agent" "$subagent_effort"
printf '  Claude workers:   %s\n' "$anthropic_models"
printf '  GPT workers:      %s\n' "$openai_models"
printf '  Extra usage:      %s\n' "$extra_usage_policy"
printf '  Routing policy:   %s\n' "$routing_policy"
printf '  Max top-level agents: %s\n' "$max_agents"
printf '  Luna swarm Fast:  %s\n' "$swarm_fast"
printf '  Failover policy:  %s\n' "$failover_policy"
printf '  Agent nesting:    off for named Agents; root spawn depth: 1\n'
printf '  Claude tier:      %s\n' "$claude_plan"
printf '  OpenAI capacity:  %s\n' "$openai_capacity"
printf '  Browser OAuth:    %s, if needed\n' "$run_login"
printf '  Background service: %s\n' "$start_service"
printf '  Config path:      %s\n' "$config_target"

if [[ "$assume_yes" -eq 0 ]]; then
  ask_yes_no 'Apply this configuration?' yes
  if [[ "$ANSWER" != 'yes' ]]; then
    printf 'No changes made.\n'
    exit 0
  fi
fi

mkdir -p "$config_dir"
rendered_config="$(mktemp "$config_dir/.config.XXXXXX")"
trap 'rm -f "$rendered_config"' EXIT
cat > "$rendered_config" <<EOF
# Managed by https://github.com/Harshkamdar67/Airlock
# Generated by scripts/setup.sh. Do not put credentials in this file.
AIRLOCK_MODEL=$main_model
AIRLOCK_MAIN_EFFORT=$main_effort
AIRLOCK_BG_MODEL=$bg_model
AIRLOCK_BG_EFFORT=$bg_effort
AIRLOCK_SMALL_FAST_MODEL=$utility_wire_model
AIRLOCK_SUBAGENT_EFFORT=$subagent_effort
AIRLOCK_EXTRA_USAGE_POLICY=$extra_usage_policy
AIRLOCK_ROUTING_POLICY=$routing_policy
AIRLOCK_MAX_CONCURRENT_SUBAGENTS=$max_agents
AIRLOCK_SWARM_FAST=$swarm_fast
AIRLOCK_FAILOVER_POLICY=$failover_policy
AIRLOCK_ANTHROPIC_PLAN=$claude_plan
AIRLOCK_OPENAI_CAPACITY=$openai_capacity
AIRLOCK_ANTHROPIC_MODELS=$anthropic_models
AIRLOCK_OPENAI_MODELS=$openai_models
AIRLOCK_ANTHROPIC_EXTRA_MODELS=$anthropic_extra_models
AIRLOCK_OPENAI_EXTRA_MODELS=$openai_extra_models
AIRLOCK_CONTEXT_WINDOW=272000
AIRLOCK_PROXY_URL=http://127.0.0.1:18765
EOF
chmod 0644 "$rendered_config"

if [[ -f "$config_target" ]] && ! cmp -s "$rendered_config" "$config_target"; then
  backup_path="$config_target.backup-$(date +%Y%m%d%H%M%S)-$$"
  cp -p "$config_target" "$backup_path"
  printf 'Backed up existing config to %s\n' "$backup_path"
fi
mv "$rendered_config" "$config_target"
trap - EXIT
printf 'Saved configuration to %s\n' "$config_target"

if [[ "$config_only" -eq 1 ]]; then
  printf 'Configuration-only mode complete.\n'
  exit 0
fi

install_args=()
if [[ "$install_agent" == 'yes' ]]; then install_args+=(--with-agent); fi
if [[ "$run_login" == 'yes' ]]; then install_args+=(--login); fi
if [[ "$start_service" == 'no' ]]; then install_args+=(--no-service); fi

AIRLOCK_CONFIG_DIR="$config_dir" \
AIRLOCK_SUBAGENT_EFFORT="$subagent_effort" \
  "$repo_root/scripts/install.sh" "${install_args[@]}"

printf '\nFinal verification:\n'
"$repo_root/scripts/doctor.sh"
