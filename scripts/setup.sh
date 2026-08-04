#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_dir="${AIRLOCK_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/airlock}"
config_target="$config_dir/config"

default_profile=''
hybrid_model=''
main_model=''
main_effort=''
bg_model=''
bg_effort=''
utility_model=''
worker_effort=''
worker_pins=''
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
open_advanced=''
assume_yes=0
profile_was_explicit=0
main_model_was_explicit=0
worker_pins_were_decided=0
anthropic_models_was_decided=0
openai_models_was_decided=0
config_only=0

usage() {
  cat <<'EOF'
Usage: ./scripts/setup.sh [options]

Without options, setup.sh opens an interactive questionnaire.

Options:
  --default-profile PROFILE  Bare airlock: hybrid or openai
  --main-model MODEL          Backward-compatible root choice; Claude implies hybrid
  --hybrid-model MODEL        Saved hybrid orchestrator alias
  --main-effort EFFORT        Starting session effort: low, medium, high, xhigh, or max
  --worker-effort EFFORT      Named model workers: inherit, low, medium, high, xhigh, or max
  --worker-pins LIST          Per-route pins such as luna=max,sonnet=high; empty clears
  --bg-model MODEL            Advanced model for the separate airlock bg command
  --bg-effort EFFORT          Advanced effort for the separate airlock bg command
  --utility-model MODEL       Advanced model for lightweight Claude Code requests
  --subagent-effort EFFORT    Effort for the optional generic airlock-worker
  --extra-usage POLICY       Extra-usage workers: ask, never, or allow
  --routing-policy POLICY    Routing objective: balanced, quality, or economy
  --max-agents VALUE         Concurrent top-level workers: off or 1..20
  --swarm-fast POLICY       Luna swarm Fast processing: auto, on, or off
  --failover-policy POLICY   Worker failover: ask, never, or allow
  --claude-plan PLAN         Claude tier: unknown, pro, max5x, or max20x
  --openai-capacity VALUE    Codex capacity override: auto, 1x, 5x, or 20x
  --anthropic-workers LIST   Enabled Claude workers; empty clears the list
  --openai-workers LIST      Enabled GPT workers; empty clears the list
  --with-agent               Install the optional generic airlock-worker
  --without-agent            Do not install the custom worker
  --login                    Run browser OAuth if authentication is missing
  --no-login                 Do not start browser OAuth
  --start-service            Start the Homebrew background service
  --no-service               Do not start the Homebrew background service
  --config-only              Write configuration without installing anything
  -y, --yes                  Accept the summary without a final prompt
  -h, --help                 Show this help

Root aliases:
  Hybrid: sonnet, sol, terra, luna, opus, fable, haiku
  OpenAI-only: sol, sol-fast, terra, luna, 5.5, 5.4, mini, 5.3, spark, 5.2
EOF
}

require_value() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    printf 'setup: %s requires a value\n' "$1" >&2
    exit 2
  fi
}

require_argument() {
  if [[ $# -lt 2 ]]; then
    printf 'setup: %s requires a value\n' "$1" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --default-profile) require_value "$@"; default_profile="$2"; profile_was_explicit=1; shift ;;
    --main-model) require_value "$@"; main_model="$2"; main_model_was_explicit=1; shift ;;
    --hybrid-model) require_value "$@"; hybrid_model="$2"; shift ;;
    --main-effort) require_value "$@"; main_effort="$2"; shift ;;
    --worker-effort) require_value "$@"; worker_effort="$2"; shift ;;
    --worker-pins) require_argument "$@"; worker_pins="$2"; worker_pins_were_decided=1; shift ;;
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
    --anthropic-workers) require_argument "$@"; anthropic_models="$2"; anthropic_models_was_decided=1; shift ;;
    --openai-workers) require_argument "$@"; openai_models="$2"; openai_models_was_decided=1; shift ;;
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

had_existing_config=0
[[ -f "$config_target" ]] && had_existing_config=1
if [[ "$had_existing_config" -eq 1 ]]; then
  read_config_value AIRLOCK_DEFAULT_PROFILE openai
else
  read_config_value AIRLOCK_DEFAULT_PROFILE hybrid
fi
default_default_profile="$CONFIG_VALUE"
read_config_value AIRLOCK_HYBRID_MODEL sonnet
default_hybrid_model="$CONFIG_VALUE"
read_config_value AIRLOCK_MODEL sol
default_main_model="$CONFIG_VALUE"
read_config_value AIRLOCK_MAIN_EFFORT high
default_main_effort="$CONFIG_VALUE"
read_config_value AIRLOCK_BG_MODEL sol
default_bg_model="$CONFIG_VALUE"
read_config_value AIRLOCK_BG_EFFORT medium
default_bg_effort="$CONFIG_VALUE"
read_config_value AIRLOCK_SMALL_FAST_MODEL 'gpt-5.6-luna[1m]'
default_utility_wire="$CONFIG_VALUE"
read_config_value AIRLOCK_WORKER_EFFORT inherit
default_worker_effort="$CONFIG_VALUE"
default_worker_pins=''
for route in sol terra luna opus sonnet fable haiku; do
  key="AIRLOCK_EFFORT_$(printf '%s' "$route" | tr '[:lower:]-' '[:upper:]_')"
  read_config_value "$key" ''
  if [[ -n "$CONFIG_VALUE" ]]; then
    if [[ -n "$default_worker_pins" ]]; then default_worker_pins+=','; fi
    default_worker_pins+="$route=$CONFIG_VALUE"
  fi
done
read_config_value AIRLOCK_SUBAGENT_EFFORT inherit
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
read_config_value AIRLOCK_GPT_EFFORT_CAPABILITIES 'effort,xhigh_effort,max_effort'
gpt_effort_capabilities="$CONFIG_VALUE"

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

set_model_info() {
  case "$1" in
    sonnet) MODEL_TITLE='Claude Sonnet 5'; MODEL_ID='claude-sonnet-5'; MODEL_DETAIL='balanced engineering and repository work - standard usage' ;;
    opus) MODEL_TITLE='Claude Opus 5'; MODEL_ID='claude-opus-5'; MODEL_DETAIL='architecture, security, and visual direction - premium usage' ;;
    fable) MODEL_TITLE='Claude Fable 5'; MODEL_ID='claude-fable-5'; MODEL_DETAIL='efficient frontier work - may require extra usage' ;;
    haiku) MODEL_TITLE='Claude Haiku 4.5'; MODEL_ID='claude-haiku-4-5-20251001'; MODEL_DETAIL='fast bounded utility work - economical usage' ;;
    sol) MODEL_TITLE='GPT-5.6 Sol'; MODEL_ID='gpt-5.6-sol[1m]'; MODEL_DETAIL='difficult implementation and integration - premium usage' ;;
    sol-fast) MODEL_TITLE='GPT-5.6 Sol Fast'; MODEL_ID='gpt-5.6-sol-fast[1m]'; MODEL_DETAIL='priority-processed Sol - eligible plans only' ;;
    terra) MODEL_TITLE='GPT-5.6 Terra'; MODEL_ID='gpt-5.6-terra[1m]'; MODEL_DETAIL='review and alternative reasoning - standard usage' ;;
    luna) MODEL_TITLE='GPT-5.6 Luna'; MODEL_ID='gpt-5.6-luna[1m]'; MODEL_DETAIL='discovery, triage, and bounded work - economical usage' ;;
    5.5) MODEL_TITLE='GPT-5.5'; MODEL_ID='gpt-5.5[1m]'; MODEL_DETAIL='supported OpenAI root' ;;
    5.4) MODEL_TITLE='GPT-5.4'; MODEL_ID='gpt-5.4[1m]'; MODEL_DETAIL='supported OpenAI root' ;;
    mini) MODEL_TITLE='GPT-5.4 Mini'; MODEL_ID='gpt-5.4-mini[1m]'; MODEL_DETAIL='small OpenAI root' ;;
    5.3) MODEL_TITLE='GPT-5.3 Codex'; MODEL_ID='gpt-5.3-codex[1m]'; MODEL_DETAIL='supported Codex root' ;;
    spark) MODEL_TITLE='GPT-5.3 Codex Spark'; MODEL_ID='gpt-5.3-codex-spark'; MODEL_DETAIL='fast supported Codex root' ;;
    5.2) MODEL_TITLE='GPT-5.2'; MODEL_ID='gpt-5.2[1m]'; MODEL_DETAIL='supported OpenAI root' ;;
    *) MODEL_TITLE="$1"; MODEL_ID="$1"; MODEL_DETAIL='custom model' ;;
  esac
}

model_summary() {
  set_model_info "$1"
  printf '%s (%s)' "$MODEL_TITLE" "$MODEL_ID"
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

validate_default_profile() {
  case "$1" in
    openai|hybrid) ;;
    *) printf 'setup: default profile must be hybrid or openai\n' >&2; exit 2 ;;
  esac
}

validate_hybrid_model() {
  case "$1" in
    sonnet|sol|terra|luna|opus|fable|haiku) ;;
    *) printf 'setup: unsupported hybrid orchestrator: %s\n' "$1" >&2; exit 2 ;;
  esac
}

validate_worker_pins() {
  local value="$1"
  local item route effort
  local seen=','
  local -a items
  [[ -z "$value" ]] && return
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
    printf 'setup: invalid worker pin list\n' >&2
    exit 2
  }
  IFS=',' read -r -a items <<< "$value"
  for item in ${items[@]+"${items[@]}"}; do
    route="${item%%=*}"
    effort="${item#*=}"
    if [[ "$route" == "$item" ]]; then
      printf 'setup: worker pin must use route=effort: %s\n' "$item" >&2
      exit 2
    fi
    case "$route" in sol|terra|luna|opus|sonnet|fable|haiku) ;; *) printf 'setup: unsupported worker pin route: %s\n' "$route" >&2; exit 2 ;; esac
    if [[ "$seen" == *",$route,"* ]]; then
      printf 'setup: duplicate worker pin route: %s\n' "$route" >&2
      exit 2
    fi
    seen+="$route,"
    validate_subagent_effort "$effort"
  done
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

# The optional sub-agent may also inherit, which leaves it following the session
# level so /effort moves it mid-session. Root and background efforts are passed
# straight to Claude Code, which has no inherit value.
validate_subagent_effort() {
  case "$1" in
    inherit) ;;
    *) validate_effort "$1" ;;
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
  for item in ${items[@]+"${items[@]}"}; do
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
    for option in ${options[@]+"${options[@]}"}; do
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
    for option in ${options[@]+"${options[@]}"}; do
      if [[ "$answer" == "$option" ]]; then
        CHOICE="$option"
        return
      fi
    done
    printf 'Please enter a listed number or value.\n' >&2
  done
}

if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != 'dumb' ]]; then
  STYLE_BOLD=$'\033[1m'
  STYLE_DIM=$'\033[2m'
  STYLE_ACCENT=$'\033[36m'
  STYLE_GREEN=$'\033[32m'
  STYLE_RESET=$'\033[0m'
else
  STYLE_BOLD=''
  STYLE_DIM=''
  STYLE_ACCENT=''
  STYLE_GREEN=''
  STYLE_RESET=''
fi

print_header() {
  printf '\n%sAIRLOCK SETUP%s\n' "$STYLE_BOLD$STYLE_ACCENT" "$STYLE_RESET"
  printf '%s\n' '------------------------------------------------------------'
  printf 'Configure one safe default command and the workers behind it.\n'
  printf '%sNo credentials or token values are stored.%s\n' "$STYLE_DIM" "$STYLE_RESET"
}

print_section() {
  local step="$1"
  local title="$2"
  local description="$3"
  printf '\n%s[%s/6] %s%s\n' "$STYLE_BOLD" "$step" "$title" "$STYLE_RESET"
  printf '%s\n' "$description"
}

choose_rich_option() {
  local current="$1"
  local recommended="$2"
  shift 2
  local options=("$@")
  local answer default_value index spec value remainder title description badge
  default_value="${current:-$recommended}"
  while true; do
    index=1
    for spec in ${options[@]+"${options[@]}"}; do
      value="${spec%%|*}"
      remainder="${spec#*|}"
      title="${remainder%%|*}"
      description="${remainder#*|}"
      badge=''
      if [[ "$value" == "$current" ]]; then
        badge=" ${STYLE_GREEN}[current]${STYLE_RESET}"
      elif [[ "$value" == "$recommended" ]]; then
        badge=" ${STYLE_ACCENT}[recommended]${STYLE_RESET}"
      fi
      printf '  %d) %s%s\n' "$index" "$title" "$badge"
      printf '     %s%s%s\n' "$STYLE_DIM" "$description" "$STYLE_RESET"
      index=$((index + 1))
    done
    printf 'Choice [%s]: ' "$default_value"
    IFS= read -r answer
    answer="${answer:-$default_value}"
    if [[ "$answer" =~ ^[0-9]+$ ]]; then
      index=$((10#$answer))
      if (( index >= 1 && index <= ${#options[@]} )); then
        spec="${options[$((index - 1))]}"
        CHOICE="${spec%%|*}"
        return
      fi
    fi
    for spec in ${options[@]+"${options[@]}"}; do
      value="${spec%%|*}"
      if [[ "$answer" == "$value" ]]; then CHOICE="$value"; return; fi
    done
    printf 'Enter a listed number or value.\n' >&2
  done
}

csv_contains() {
  [[ ",$1," == *",$2,"* ]]
}

csv_add() {
  local list="$1"
  local value="$2"
  if csv_contains "$list" "$value"; then CSV_RESULT="$list"
  elif [[ -n "$list" ]]; then CSV_RESULT="$list,$value"
  else CSV_RESULT="$value"
  fi
}

worker_list_display() {
  local list="$1"
  local item summary
  local -a items
  DISPLAY_LIST=''
  IFS=',' read -r -a items <<< "$list"
  for item in ${items[@]+"${items[@]}"}; do
    [[ -n "$item" ]] || continue
    summary="$(model_summary "$item")"
    if [[ -n "$DISPLAY_LIST" ]]; then DISPLAY_LIST+=', '; fi
    DISPLAY_LIST+="$summary"
  done
  [[ -n "$DISPLAY_LIST" ]] || DISPLAY_LIST='none'
}

pin_for_route() {
  local pins="$1"
  local wanted="$2"
  local item
  local -a items
  PIN_VALUE='inherit'
  IFS=',' read -r -a items <<< "$pins"
  for item in ${items[@]+"${items[@]}"}; do
    if [[ "${item%%=*}" == "$wanted" ]]; then PIN_VALUE="${item#*=}"; return; fi
  done
}

add_worker_pin() {
  local route="$1"
  local effort="$2"
  [[ "$effort" == 'inherit' ]] && return
  if [[ -n "$worker_pins" ]]; then worker_pins+=','; fi
  worker_pins+="$route=$effort"
}

render_worker_pin_lines() {
  local pins="$1"
  local item route effort key
  local -a items
  IFS=',' read -r -a items <<< "$pins"
  for item in ${items[@]+"${items[@]}"}; do
    [[ -n "$item" ]] || continue
    route="${item%%=*}"
    effort="${item#*=}"
    key="AIRLOCK_EFFORT_$(printf '%s' "$route" | tr '[:lower:]-' '[:upper:]_')"
    printf '%s=%s\n' "$key" "$effort"
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

if [[ -z "$default_profile" ]]; then default_profile="$default_default_profile"; fi
if [[ -z "$hybrid_model" ]]; then hybrid_model="$default_hybrid_model"; fi

# Keep --main-model backward compatible. Without an explicit profile it still
# selects the OpenAI-only default, except that a Claude alias necessarily means
# a hybrid session.
if [[ "$main_model_was_explicit" -eq 1 ]]; then
  case "$main_model" in
    opus|sonnet|fable|haiku)
      [[ "$profile_was_explicit" -eq 1 && "$default_profile" != 'hybrid' ]] && {
        printf 'setup: a Claude main model requires --default-profile hybrid\n' >&2
        exit 2
      }
      default_profile='hybrid'
      hybrid_model="$main_model"
      main_model="$default_main_model"
      ;;
    *)
      if [[ "$profile_was_explicit" -eq 1 && "$default_profile" == 'hybrid' ]]; then
        hybrid_model="$main_model"
      else
        default_profile='openai'
      fi
      ;;
  esac
fi

if [[ "$assume_yes" -eq 0 ]]; then
  print_header
  print_section 1 'SESSION AND ORCHESTRATOR' 'Choose what bare `airlock` starts. Explicit commands always override this.'
  choose_rich_option "$default_profile" hybrid \
    'hybrid|Hybrid: Claude and GPT together|Choose any enabled Claude or GPT orchestrator and keep both worker providers available.' \
    'openai|OpenAI only|Use the local OpenAI proxy without starting the mixed-provider router.'
  default_profile="$CHOICE"

  if [[ "$default_profile" == 'hybrid' ]]; then
    printf '\n%sDefault orchestrator%s\n' "$STYLE_BOLD" "$STYLE_RESET"
    printf 'This model leads the session and decides when to use workers.\n'
    choose_rich_option "$hybrid_model" sonnet \
      'sonnet|Claude Sonnet 5|claude-sonnet-5 - balanced engineering and repository work - standard usage' \
      'sol|GPT-5.6 Sol|gpt-5.6-sol[1m] - difficult implementation and integration - premium usage' \
      'terra|GPT-5.6 Terra|gpt-5.6-terra[1m] - review and alternative reasoning - standard usage' \
      'luna|GPT-5.6 Luna|gpt-5.6-luna[1m] - discovery, triage, and bounded work - economical usage' \
      'opus|Claude Opus 5|claude-opus-5 - architecture, security, and visual direction - premium usage' \
      'fable|Claude Fable 5|claude-fable-5 - efficient frontier work - may require extra usage' \
      'haiku|Claude Haiku 4.5|claude-haiku-4-5-20251001 - fast bounded utility work - economical usage'
    hybrid_model="$CHOICE"
    main_model="$default_main_model"
  else
    printf '\n%sDefault orchestrator%s\n' "$STYLE_BOLD" "$STYLE_RESET"
    printf 'OpenAI-only sessions can still use exact GPT workers.\n'
    choose_rich_option "${main_model:-$default_main_model}" sol \
      'sol|GPT-5.6 Sol|gpt-5.6-sol[1m] - difficult implementation and integration - premium usage' \
      'terra|GPT-5.6 Terra|gpt-5.6-terra[1m] - review and alternative reasoning - standard usage' \
      'luna|GPT-5.6 Luna|gpt-5.6-luna[1m] - discovery, triage, and bounded work - economical usage' \
      'sol-fast|GPT-5.6 Sol Fast|gpt-5.6-sol-fast[1m] - priority processing on eligible plans' \
      '5.5|GPT-5.5|gpt-5.5[1m] - supported OpenAI root' \
      '5.4|GPT-5.4|gpt-5.4[1m] - supported OpenAI root' \
      'mini|GPT-5.4 Mini|gpt-5.4-mini[1m] - small OpenAI root' \
      '5.3|GPT-5.3 Codex|gpt-5.3-codex[1m] - supported Codex root' \
      'spark|GPT-5.3 Codex Spark|gpt-5.3-codex-spark - fast supported Codex root' \
      '5.2|GPT-5.2|gpt-5.2[1m] - supported OpenAI root'
    main_model="$CHOICE"
  fi

  print_section 2 'WORKER POOL' 'Choose which exact-model Agents the orchestrator may use. More is not always better.'
  printf '%sModel catalog%s\n' "$STYLE_BOLD" "$STYLE_RESET"
  printf '%sAccess depends on the connected plans and is checked again when a session starts.%s\n' "$STYLE_DIM" "$STYLE_RESET"
  for route in sonnet opus fable haiku luna terra sol; do
    set_model_info "$route"
    printf '  %s (%s)\n' "$MODEL_TITLE" "$MODEL_ID"
    printf '    %s%s%s\n' "$STYLE_DIM" "$MODEL_DETAIL" "$STYLE_RESET"
  done
  printf '\n'
  current_preset='custom'
  if [[ "$default_anthropic_models" == 'opus,sonnet' && "$default_openai_models" == 'sol,terra,luna' ]]; then current_preset='balanced'; fi
  if [[ "$default_anthropic_models" == 'sonnet' && "$default_openai_models" == 'luna' ]]; then current_preset='economy'; fi
  choose_rich_option "$current_preset" balanced \
    'balanced|Balanced pool|Claude Opus and Sonnet plus GPT Sol, Terra, and Luna. The router chooses only when useful.' \
    'economy|Economical pool|Claude Sonnet and GPT Luna. Lower relative usage with broad basic coverage.' \
    'custom|Choose models individually|Enable only the exact workers you want and see every model before saving.'
  worker_preset="$CHOICE"
  anthropic_models_was_decided=1
  openai_models_was_decided=1
  case "$worker_preset" in
    balanced) anthropic_models='opus,sonnet'; openai_models='sol,terra,luna' ;;
    economy) anthropic_models='sonnet'; openai_models='luna' ;;
    custom)
      anthropic_models=''
      openai_models=''
      for route in sonnet opus fable haiku; do
        set_model_info "$route"
        current_answer='no'; csv_contains "$default_anthropic_models" "$route" && current_answer='yes'
        ask_yes_no "Enable $MODEL_TITLE ($MODEL_ID)?" "$current_answer"
        if [[ "$ANSWER" == 'yes' ]]; then csv_add "$anthropic_models" "$route"; anthropic_models="$CSV_RESULT"; fi
      done
      for route in luna terra sol; do
        set_model_info "$route"
        current_answer='no'; csv_contains "$default_openai_models" "$route" && current_answer='yes'
        ask_yes_no "Enable $MODEL_TITLE ($MODEL_ID)?" "$current_answer"
        if [[ "$ANSWER" == 'yes' ]]; then csv_add "$openai_models" "$route"; openai_models="$CSV_RESULT"; fi
      done
      ;;
  esac

  print_section 3 'EFFORT' 'Set the starting session level and decide whether workers move with `/effort`.'
  choose_rich_option "${main_effort:-$default_main_effort}" high \
    'low|Low|Fastest and least deliberate.' \
    'medium|Medium|A lighter setting for routine work.' \
    'high|High|Recommended default for normal engineering work.' \
    'xhigh|Extra high|More deliberate and more expensive.' \
    'max|Maximum|Use only when the selected model and task justify it.'
  main_effort="$CHOICE"
  printf '\nClaude Code does not expose per-call Agent effort. The orchestrator chooses a worker,\n'
  printf 'but workers either follow the session level or keep a setup-time pin.\n\n'
  existing_worker_pins="${worker_pins:-$default_worker_pins}"
  current_effort_mode='inherit'
  [[ "${worker_effort:-$default_worker_effort}" != 'inherit' ]] && current_effort_mode='pin-all'
  [[ -n "$existing_worker_pins" ]] && current_effort_mode='custom'
  choose_rich_option "$current_effort_mode" inherit \
    'inherit|Follow session effort|Recommended. `/effort` moves the root and every unpinned worker, including mid-session.' \
    'pin-all|Pin every worker|All named workers keep one level even when the session changes.' \
    'custom|Pin selected models|Choose a level for individual enabled workers and let the rest follow the session.'
  effort_mode="$CHOICE"
  worker_pins=''
  worker_pins_were_decided=1
  case "$effort_mode" in
    inherit) worker_effort='inherit' ;;
    pin-all)
      choose_rich_option "${worker_effort:-$default_worker_effort}" high \
        'low|Low|Pin every named worker to low.' 'medium|Medium|Pin every named worker to medium.' \
        'high|High|Pin every named worker to high.' 'xhigh|Extra high|Pin every named worker to xhigh.' \
        'max|Maximum|Pin every named worker to max.'
      worker_effort="$CHOICE"
      ;;
    custom)
      worker_effort='inherit'
      for route in sonnet opus fable haiku luna terra sol; do
        if csv_contains "$anthropic_models,$openai_models" "$route"; then
          set_model_info "$route"
          pin_for_route "$existing_worker_pins" "$route"
          printf '\n%s worker effort\n' "$MODEL_TITLE"
          choose_rich_option "$PIN_VALUE" inherit \
            'inherit|Follow session|Move with `/effort`.' 'low|Low|Keep this worker at low.' \
            'medium|Medium|Keep this worker at medium.' 'high|High|Keep this worker at high.' \
            'xhigh|Extra high|Keep this worker at xhigh.' 'max|Maximum|Keep this worker at max.'
          add_worker_pin "$route" "$CHOICE"
        fi
      done
      ;;
  esac

  print_section 4 'SAFETY AND BUDGET' 'Choose conservative usage rules and a parallel-worker ceiling.'
  choose_rich_option "${extra_usage_policy:-$default_extra_usage_policy}" ask \
    'ask|Ask before extra usage|Recommended. Extra-usage routes need explicit confirmation.' \
    'never|Block extra usage|Fail closed instead of using paid extra capacity.' \
    'allow|Allow extra usage|Permit configured extra routes without another confirmation.'
  extra_usage_policy="$CHOICE"
  printf '\n%sRouting preference%s\n' "$STYLE_BOLD" "$STYLE_RESET"
  choose_rich_option "${routing_policy:-$default_routing_policy}" balanced \
    'balanced|Balanced|Use the smallest effective route while balancing quality and relative usage.' \
    'quality|Quality first|Prefer stronger eligible routes when the expected benefit justifies them.' \
    'economy|Economy first|Prefer economical eligible routes and smaller initial fan-out.'
  routing_policy="$CHOICE"
  printf '\n%sParallel workers%s\n' "$STYLE_BOLD" "$STYLE_RESET"
  choose_rich_option "${max_agents:-$default_max_agents}" off \
    'off|Claude Code default|Recommended. Do not impose an Airlock-specific ceiling.' \
    '1|One worker|Run one top-level worker at a time.' '2|Two workers|Allow two top-level workers.' \
    '3|Three workers|Allow three top-level workers.' '4|Four workers|Allow four top-level workers.' \
    '5|Five workers|Allow five top-level workers.' '10|Ten workers|Expert setting for wide independent work.'
  max_agents="$CHOICE"

  printf '\n%sAdvanced settings%s\n' "$STYLE_BOLD" "$STYLE_RESET"
  printf '%sOptional compatibility, performance, and capacity controls. Most users should keep the defaults.%s\n' "$STYLE_DIM" "$STYLE_RESET"
  ask_yes_no 'Open Advanced settings?' no
  open_advanced="$ANSWER"
  if [[ "$open_advanced" == 'yes' ]]; then
    printf '\n%sADVANCED SETTINGS%s\n' "$STYLE_BOLD" "$STYLE_RESET"
    printf '%s`airlock bg` is a separate convenience command. It does not power normal Agents.%s\n' "$STYLE_DIM" "$STYLE_RESET"
    choose_rich_option "${bg_model:-$default_bg_model}" sol \
      'sol|GPT-5.6 Sol|gpt-5.6-sol[1m] - background command default' \
      'terra|GPT-5.6 Terra|gpt-5.6-terra[1m]' 'luna|GPT-5.6 Luna|gpt-5.6-luna[1m]' \
      'mini|GPT-5.4 Mini|gpt-5.4-mini[1m]'
    bg_model="$CHOICE"
    choose_rich_option "${bg_effort:-$default_bg_effort}" medium \
      'low|Low|Low effort for airlock bg.' 'medium|Medium|Recommended for airlock bg.' \
      'high|High|High effort for airlock bg.' 'xhigh|Extra high|Extra-high effort for airlock bg.' \
      'max|Maximum|Maximum effort for airlock bg.'
    bg_effort="$CHOICE"
    printf '\n%sThe utility model handles lightweight Claude Code requests such as titles.%s\n' "$STYLE_DIM" "$STYLE_RESET"
    choose_rich_option "${utility_model:-$default_utility_model}" luna \
      'luna|GPT-5.6 Luna|gpt-5.6-luna[1m] - recommended economical utility route' \
      'terra|GPT-5.6 Terra|gpt-5.6-terra[1m]' 'sol|GPT-5.6 Sol|gpt-5.6-sol[1m]' \
      'mini|GPT-5.4 Mini|gpt-5.4-mini[1m]'
    utility_model="$CHOICE"
    choose_rich_option "${swarm_fast:-$default_swarm_fast}" auto \
      'auto|Automatic|Use Luna Fast only when sanitized plan and proxy checks prove eligibility.' \
      'off|Off|Never use Luna Fast for automatic swarms.' \
      'on|On|Require Luna Fast and fail if the route is ineligible.'
    swarm_fast="$CHOICE"
    choose_rich_option "${failover_policy:-$default_failover_policy}" ask \
      'ask|Ask before failover|Recommended. Never change model or provider silently.' \
      'never|Never fail over|Stop when the exact route fails.' \
      'allow|Allow failover|Permit policy-approved failover without asking again.'
    failover_policy="$CHOICE"
    if [[ -z "$claude_plan" ]]; then
      if [[ "$claude_plan_was_detected" -eq 1 ]]; then claude_plan="$default_claude_plan"
      else
        choose_rich_option "$default_claude_plan" unknown \
          'unknown|Automatic / unknown|Do not guess a Claude subscription tier.' \
          'pro|Claude Pro|Manual capacity hint.' 'max5x|Claude Max 5x|Manual capacity hint.' \
          'max20x|Claude Max 20x|Manual capacity hint.'
        claude_plan="$CHOICE"
      fi
    fi
    if [[ -z "$openai_capacity" ]]; then
      choose_rich_option "$default_openai_capacity" auto \
        'auto|Automatic|Use sanitized observations and clearly labeled inferences.' \
        '1x|1x|Manual capacity hint.' '5x|5x|Manual capacity hint.' '20x|20x|Manual capacity hint.'
      openai_capacity="$CHOICE"
    fi
    if [[ -z "$install_agent" ]]; then
      ask_yes_no 'Install the optional generic airlock-worker in addition to exact model workers?' no
      install_agent="$ANSWER"
    fi
    if [[ "$install_agent" == 'yes' && -z "$subagent_effort" ]]; then
      choose_rich_option "$default_subagent_effort" inherit \
        'inherit|Follow session effort|Recommended for the optional generic worker.' \
        'low|Low|Pin the optional worker to low.' 'medium|Medium|Pin the optional worker to medium.' \
        'high|High|Pin the optional worker to high.' 'xhigh|Extra high|Pin the optional worker to xhigh.' \
        'max|Maximum|Pin the optional worker to max.'
      subagent_effort="$CHOICE"
    fi
  fi
fi

main_model="${main_model:-$default_main_model}"
main_effort="${main_effort:-$default_main_effort}"
bg_model="${bg_model:-$default_bg_model}"
bg_effort="${bg_effort:-$default_bg_effort}"
utility_model="${utility_model:-$default_utility_model}"
worker_effort="${worker_effort:-$default_worker_effort}"
if [[ "$worker_pins_were_decided" -eq 0 ]]; then worker_pins="$default_worker_pins"; fi
subagent_effort="${subagent_effort:-$default_subagent_effort}"
if [[ "$anthropic_models_was_decided" -eq 0 ]]; then anthropic_models="$default_anthropic_models"; fi
if [[ "$openai_models_was_decided" -eq 0 ]]; then openai_models="$default_openai_models"; fi
extra_usage_policy="${extra_usage_policy:-$default_extra_usage_policy}"
routing_policy="${routing_policy:-$default_routing_policy}"
max_agents="${max_agents:-$default_max_agents}"
swarm_fast="${swarm_fast:-$default_swarm_fast}"
failover_policy="${failover_policy:-$default_failover_policy}"
claude_plan="${claude_plan:-$default_claude_plan}"
openai_capacity="${openai_capacity:-$default_openai_capacity}"
if [[ -z "$install_agent" ]]; then install_agent='no'; fi

# The selected orchestrator must stay routable even when a custom worker pool
# would otherwise omit its route.
if [[ "$default_profile" == 'hybrid' ]]; then
  case "$hybrid_model" in
    opus|sonnet|fable|haiku) csv_add "$anthropic_models" "$hybrid_model"; anthropic_models="$CSV_RESULT" ;;
    sol|terra|luna) csv_add "$openai_models" "$hybrid_model"; openai_models="$CSV_RESULT" ;;
  esac
fi

if [[ -z "$run_login" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then run_login='yes'; else
    print_section 5 'INSTALLATION' 'Choose what setup may start after saving the noncredential config.'
    ask_yes_no 'Open browser OAuth only if the proxy reports that login is missing?' yes
    run_login="$ANSWER"
  fi
fi

if [[ -z "$start_service" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then start_service='yes'; else
    ask_yes_no 'Start the local proxy automatically as a background service?' yes
    start_service="$ANSWER"
  fi
fi

validate_default_profile "$default_profile"
validate_hybrid_model "$hybrid_model"
validate_model "$main_model"
validate_model "$bg_model"
validate_model "$utility_model"
validate_effort "$main_effort"
validate_effort "$bg_effort"
validate_subagent_effort "$worker_effort"
validate_worker_pins "$worker_pins"
validate_subagent_effort "$subagent_effort"
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

if [[ "$default_profile" == 'hybrid' ]]; then
  default_command_model="$(model_summary "$hybrid_model")"
  default_command_profile='hybrid: Claude and GPT workers'
else
  default_command_model="$(model_summary "$main_model")"
  default_command_profile='OpenAI only'
fi
worker_list_display "$anthropic_models"
claude_worker_display="$DISPLAY_LIST"
worker_list_display "$openai_models"
gpt_worker_display="$DISPLAY_LIST"
if [[ -n "$worker_pins" ]]; then
  worker_effort_display="follow session; per-model pins: $worker_pins"
elif [[ "$worker_effort" == 'inherit' ]]; then
  worker_effort_display='follow session /effort'
else
  worker_effort_display="all pinned to $worker_effort"
fi

if [[ "$assume_yes" -eq 0 ]]; then
  print_section 6 'REVIEW' 'Nothing is changed until you confirm this screen.'
else
  printf '\n%sCONFIGURATION SUMMARY%s\n' "$STYLE_BOLD" "$STYLE_RESET"
fi
printf '  Default command:    airlock -> %s\n' "$default_command_model"
printf '  Session profile:    %s\n' "$default_command_profile"
printf '  Starting effort:    %s\n' "$main_effort"
printf '  Claude workers:     %s\n' "$claude_worker_display"
printf '  GPT workers:        %s\n' "$gpt_worker_display"
printf '  Worker effort:      %s\n' "$worker_effort_display"
printf '  Extra usage:        %s\n' "$extra_usage_policy"
printf '  Routing preference: %s\n' "$routing_policy"
printf '  Parallel workers:   %s\n' "$max_agents"
printf '  Browser login:      %s, only if needed\n' "$run_login"
printf '  Proxy service:      %s\n' "$start_service"
printf '  Config path:        %s\n' "$config_target"
if [[ "$open_advanced" == 'yes' || "$assume_yes" -eq 1 ]]; then
  printf '  Advanced:           airlock bg -> %s / %s; utility -> %s\n' "$(model_summary "$bg_model")" "$bg_effort" "$(model_summary "$utility_model")"
  printf '  Fast / failover:    %s / %s\n' "$swarm_fast" "$failover_policy"
  printf '  Generic worker:     %s (effort: %s)\n' "$install_agent" "$subagent_effort"
fi

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
AIRLOCK_DEFAULT_PROFILE=$default_profile
AIRLOCK_HYBRID_MODEL=$hybrid_model
AIRLOCK_MODEL=$main_model
AIRLOCK_MAIN_EFFORT=$main_effort
AIRLOCK_WORKER_EFFORT=$worker_effort
$(render_worker_pin_lines "$worker_pins")
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
AIRLOCK_GPT_EFFORT_CAPABILITIES=$gpt_effort_capabilities
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
  "$repo_root/scripts/install.sh" ${install_args[@]+"${install_args[@]}"}

printf '\nFinal verification:\n'
"$repo_root/scripts/doctor.sh"
