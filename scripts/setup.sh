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

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_dir="$(resolve_airlock_config_dir)"
config_target="$config_dir/config"

# Kept so the review screen can offer a clean start over without rebuilding
# the parsed state by hand.
setup_args=("$@")

default_profile=''
hybrid_model=''
grok_model=''
grok_models=''
grok_models_was_decided=0
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
openai_fast=''
anthropic_fast=''
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
  --default-profile PROFILE   Bare airlock: hybrid, openai, or grok
  --main-model MODEL          Backward-compatible root choice; Claude implies hybrid
  --hybrid-model MODEL        Saved hybrid orchestrator alias
  --grok-model MODEL          Saved Grok-only root: grok or composer
  --main-effort EFFORT        Starting session effort: low, medium, high, xhigh, or max
  --worker-effort EFFORT      Named model workers: inherit, low, medium, high, xhigh, or max
  --worker-pins LIST          Per-route pins such as luna=max,sonnet=high; empty clears
  --bg-model MODEL            Advanced model for the separate airlock bg command
  --bg-effort EFFORT          Advanced effort for the separate airlock bg command
  --utility-model MODEL       Advanced model for lightweight Claude Code requests
  --subagent-effort EFFORT    Effort for the optional generic airlock-worker
  --extra-usage POLICY        Extra-usage workers: ask, never, or allow
  --routing-policy POLICY     Routing objective: balanced, quality, or economy
  --max-agents VALUE          Concurrent top-level workers: off or 1..20
  --fast PROVIDERS            Fast startup: all, openai, anthropic, or off
  --openai-fast on|off        Enable or disable eligible OpenAI Fast routes
  --anthropic-fast on|off     Enable or disable Fast startup for supported Opus roots
  --swarm-fast POLICY         Advanced Luna Fast selection: auto, on, or off
  --failover-policy POLICY    Worker failover: ask, never, or allow
  --claude-plan PLAN          Claude tier: unknown, pro, max5x, or max20x
  --openai-capacity VALUE     Codex capacity override: auto, 1x, 5x, or 20x
  --anthropic-workers LIST    Enabled Claude workers; empty clears the list
  --openai-workers LIST       Enabled GPT workers; empty clears the list
  --grok-workers LIST         Enabled Grok workers; empty clears the list
  --with-agent                Install the optional generic airlock-worker
  --without-agent             Do not install the custom worker
  --login                     Run Codex OAuth for the local proxy if it is signed out
  --no-login                  Do not start Codex OAuth
  --start-service             Start the Homebrew background service
  --no-service                Do not start the Homebrew background service
  --config-only               Write configuration without installing anything
  -y, --yes                   Accept the summary without a final prompt
  -h, --help                  Show this help

Claude Code is a separate prerequisite. Setup never changes or reads its login.

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
    --grok-model) require_value "$@"; grok_model="$2"; shift ;;
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
    --fast)
      require_value "$@"
      case "$2" in
        all) openai_fast='on'; anthropic_fast='on' ;;
        openai) openai_fast='on'; anthropic_fast='off' ;;
        anthropic) openai_fast='off'; anthropic_fast='on' ;;
        off) openai_fast='off'; anthropic_fast='off' ;;
        *) printf 'setup: --fast must be all, openai, anthropic, or off\n' >&2; exit 2 ;;
      esac
      shift
      ;;
    --openai-fast) require_value "$@"; openai_fast="$2"; shift ;;
    --anthropic-fast) require_value "$@"; anthropic_fast="$2"; shift ;;
    --swarm-fast) require_value "$@"; swarm_fast="$2"; shift ;;
    --failover-policy) require_value "$@"; failover_policy="$2"; shift ;;
    --claude-plan) require_value "$@"; claude_plan="$2"; shift ;;
    --openai-capacity) require_value "$@"; openai_capacity="$2"; shift ;;
    --anthropic-workers) require_argument "$@"; anthropic_models="$2"; anthropic_models_was_decided=1; shift ;;
    --openai-workers) require_argument "$@"; openai_models="$2"; openai_models_was_decided=1; shift ;;
    --grok-workers) require_argument "$@"; grok_models="$2"; grok_models_was_decided=1; shift ;;
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

resolve_proxy_storage() {
  local default_config_dir default_state_home
  proxy_config_dir="${CCP_CONFIG_DIR:-$default_proxy_config_dir}"
  proxy_state_home="${XDG_STATE_HOME:-$default_proxy_state_home}"
  proxy_storage_display='upstream default directories'

  case "$(uname -s)" in
    Darwin) default_config_dir="$HOME/.config/claude-code-proxy" ;;
    *) default_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/claude-code-proxy" ;;
  esac
  if [[ -z "$proxy_config_dir" ]] && ! directory_is_writable_or_creatable "$default_config_dir"; then
    proxy_config_dir="$config_dir/claude-code-proxy"
    proxy_storage_display='private writable Airlock fallback'
  elif [[ -n "$proxy_config_dir" ]]; then
    proxy_storage_display='configured proxy directories'
  fi

  default_state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
  if [[ -z "$proxy_state_home" ]] && ! directory_is_writable_or_creatable "$default_state_home"; then
    proxy_state_home="$config_dir/proxy-state"
    proxy_storage_display='private writable Airlock fallback'
  elif [[ -n "$proxy_state_home" && "$proxy_storage_display" == 'upstream default directories' ]]; then
    proxy_storage_display='configured proxy directories'
  fi
}

had_existing_config=0
[[ -f "$config_target" ]] && had_existing_config=1
if [[ "$had_existing_config" -eq 1 ]]; then
  read_config_value AIRLOCK_DEFAULT_PROFILE openai
else
  read_config_value AIRLOCK_DEFAULT_PROFILE hybrid
fi
default_default_profile="$CONFIG_VALUE"
# A brand new setup recommends Sol. An existing config that simply never had
# this key must fall back to sonnet instead, because that is what both
# launchers already use for it; picking anything else would silently move the
# hybrid root the first time setup rewrites the file.
if [[ -f "$config_target" ]]; then
  read_config_value AIRLOCK_HYBRID_MODEL sonnet
else
  read_config_value AIRLOCK_HYBRID_MODEL sol
fi
default_hybrid_model="$CONFIG_VALUE"
read_config_value AIRLOCK_GROK_MODEL grok
default_grok_model="$CONFIG_VALUE"
read_config_value AIRLOCK_GROK_MODELS ''
default_grok_models="$CONFIG_VALUE"
read_config_value AIRLOCK_MODEL sol
default_main_model="$CONFIG_VALUE"
read_config_value AIRLOCK_MAIN_EFFORT high
default_main_effort="$CONFIG_VALUE"
read_config_value AIRLOCK_BG_MODEL sol
default_bg_model="$CONFIG_VALUE"
read_config_value AIRLOCK_BG_EFFORT medium
default_bg_effort="$CONFIG_VALUE"
read_config_value AIRLOCK_SMALL_FAST_MODEL 'gpt-5.6-luna'
default_utility_wire="$CONFIG_VALUE"
read_config_value AIRLOCK_WORKER_EFFORT inherit
default_worker_effort="$CONFIG_VALUE"
default_worker_pins=''
for route in sol terra luna opus sonnet fable haiku grok composer; do
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
read_config_value AIRLOCK_OPENAI_FAST off
default_openai_fast="$CONFIG_VALUE"
read_config_value AIRLOCK_ANTHROPIC_FAST off
default_anthropic_fast="$CONFIG_VALUE"
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
read_config_value AIRLOCK_PROXY_CONFIG_DIR ''
default_proxy_config_dir="$CONFIG_VALUE"
read_config_value AIRLOCK_PROXY_STATE_HOME ''
default_proxy_state_home="$CONFIG_VALUE"
resolve_proxy_storage

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

normalize_openai_model_id() {
  local model="$1"
  if [[ "$model" == gpt-* && "$model" == *'[1m]' ]]; then
    model="${model%\[1m\]}"
  fi
  printf '%s' "$model"
}

setup_model_alias() {
  local model
  model="$(normalize_openai_model_id "$1")"
  case "$model" in
    gpt-5.6-sol) printf 'sol' ;;
    gpt-5.6-sol-fast) printf 'sol-fast' ;;
    gpt-5.6-terra) printf 'terra' ;;
    gpt-5.6-luna) printf 'luna' ;;
    gpt-5.5) printf '5.5' ;;
    gpt-5.4) printf '5.4' ;;
    gpt-5.4-mini) printf 'mini' ;;
    gpt-5.3-codex) printf '5.3' ;;
    gpt-5.3-codex-spark) printf 'spark' ;;
    gpt-5.2) printf '5.2' ;;
    *) printf '%s' "$model" ;;
  esac
}

utility_alias_from_wire() {
  local wire_model
  wire_model="$(normalize_openai_model_id "$1")"
  case "$wire_model" in
    gpt-5.6-sol) UTILITY_ALIAS='sol' ;;
    gpt-5.6-terra) UTILITY_ALIAS='terra' ;;
    gpt-5.6-luna) UTILITY_ALIAS='luna' ;;
    gpt-5.5) UTILITY_ALIAS='5.5' ;;
    gpt-5.4) UTILITY_ALIAS='5.4' ;;
    gpt-5.4-mini) UTILITY_ALIAS='mini' ;;
    gpt-5.3-codex) UTILITY_ALIAS='5.3' ;;
    gpt-5.3-codex-spark) UTILITY_ALIAS='spark' ;;
    gpt-5.2) UTILITY_ALIAS='5.2' ;;
    *) UTILITY_ALIAS='sol' ;;
  esac
}

set_model_info() {
  case "$1" in
    sonnet) MODEL_TITLE='Claude Sonnet 5'; MODEL_ID='claude-sonnet-5[1m]'; MODEL_DETAIL='Balanced engineering and repository work. Standard usage.' ;;
    opus) MODEL_TITLE='Claude Opus 5'; MODEL_ID='claude-opus-5[1m]'; MODEL_DETAIL='Architecture, security, and visual direction. Premium usage.' ;;
    fable) MODEL_TITLE='Claude Fable 5'; MODEL_ID='claude-fable-5[1m]'; MODEL_DETAIL='Efficient frontier work. May require extra usage.' ;;
    haiku) MODEL_TITLE='Claude Haiku 4.5'; MODEL_ID='claude-haiku-4-5-20251001'; MODEL_DETAIL='Fast bounded utility work. Economical usage.' ;;
    sol) MODEL_TITLE='GPT-5.6 Sol'; MODEL_ID='gpt-5.6-sol'; MODEL_DETAIL='Difficult implementation and integration. Premium usage.' ;;
    sol-fast) MODEL_TITLE='GPT-5.6 Sol Fast'; MODEL_ID='gpt-5.6-sol-fast'; MODEL_DETAIL='Priority-processed Sol. Eligible plans only.' ;;
    terra) MODEL_TITLE='GPT-5.6 Terra'; MODEL_ID='gpt-5.6-terra'; MODEL_DETAIL='Review and alternative reasoning. Standard usage.' ;;
    luna) MODEL_TITLE='GPT-5.6 Luna'; MODEL_ID='gpt-5.6-luna'; MODEL_DETAIL='Discovery, triage, and bounded work. Economical usage.' ;;
    5.5) MODEL_TITLE='GPT-5.5'; MODEL_ID='gpt-5.5'; MODEL_DETAIL='Supported OpenAI root.' ;;
    5.4) MODEL_TITLE='GPT-5.4'; MODEL_ID='gpt-5.4'; MODEL_DETAIL='Supported OpenAI root.' ;;
    mini) MODEL_TITLE='GPT-5.4 Mini'; MODEL_ID='gpt-5.4-mini'; MODEL_DETAIL='Small OpenAI root.' ;;
    5.3) MODEL_TITLE='GPT-5.3 Codex'; MODEL_ID='gpt-5.3-codex'; MODEL_DETAIL='Supported Codex root.' ;;
    spark) MODEL_TITLE='GPT-5.3 Codex Spark'; MODEL_ID='gpt-5.3-codex-spark'; MODEL_DETAIL='Fast supported Codex root.' ;;
    5.2) MODEL_TITLE='GPT-5.2'; MODEL_ID='gpt-5.2'; MODEL_DETAIL='Supported OpenAI root.' ;;
    grok) MODEL_TITLE='Grok 4.5'; MODEL_ID='grok-4.5'; MODEL_DETAIL='Difficult implementation and debugging. Premium usage.' ;;
    composer) MODEL_TITLE='Grok Composer 2.5 Fast'; MODEL_ID='grok-composer-2.5-fast'; MODEL_DETAIL='Discovery, triage, and bounded mechanical work. Economical usage.' ;;
    *) MODEL_TITLE="$1"; MODEL_ID="$1"; MODEL_DETAIL='Custom model.' ;;
  esac
}

model_summary() {
  set_model_info "$1"
  printf '%s (%s)' "$MODEL_TITLE" "$MODEL_ID"
}

wire_model_from_alias() {
  case "$1" in
    sol) WIRE_MODEL='gpt-5.6-sol' ;;
    sol-fast) WIRE_MODEL='gpt-5.6-sol-fast' ;;
    terra) WIRE_MODEL='gpt-5.6-terra' ;;
    luna) WIRE_MODEL='gpt-5.6-luna' ;;
    5.5) WIRE_MODEL='gpt-5.5' ;;
    5.4) WIRE_MODEL='gpt-5.4' ;;
    mini) WIRE_MODEL='gpt-5.4-mini' ;;
    5.3) WIRE_MODEL='gpt-5.3-codex' ;;
    spark) WIRE_MODEL='gpt-5.3-codex-spark' ;;
    5.2) WIRE_MODEL='gpt-5.2' ;;
    *) return 1 ;;
  esac
}

validate_default_profile() {
  case "$1" in
    openai|hybrid|grok) ;;
    *) printf 'setup: default profile must be hybrid, openai, or grok\n' >&2; exit 2 ;;
  esac
}

validate_hybrid_model() {
  case "$1" in
    sonnet|sol|terra|luna|opus|fable|haiku|grok|composer) ;;
    *) printf 'setup: unsupported hybrid orchestrator: %s\n' "$1" >&2; exit 2 ;;
  esac
}

validate_grok_model() {
  case "$1" in
    grok|composer) ;;
    *) printf 'setup: unsupported Grok orchestrator: %s\n' "$1" >&2; exit 2 ;;
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

validate_provider_fast() {
  case "$2" in
    on|off) ;;
    *) printf 'setup: %s Fast policy must be on or off\n' "$1" >&2; exit 2 ;;
  esac
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

# ---------------------------------------------------------------------------
# Presentation layer
#
# Color is an enhancement only. Every state is also carried by text, so the
# wizard reads the same with NO_COLOR, TERM=dumb, or a redirected stream.
# ---------------------------------------------------------------------------

if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != 'dumb' ]]; then
  ui_styled=1
  STYLE_BOLD=$'\033[1m'
  STYLE_DIM=$'\033[2m'
  STYLE_ACCENT=$'\033[36m'
  STYLE_GREEN=$'\033[32m'
  STYLE_RESET=$'\033[0m'
else
  ui_styled=0
  STYLE_BOLD=''
  STYLE_DIM=''
  STYLE_ACCENT=''
  STYLE_GREEN=''
  STYLE_RESET=''
fi

stty_columns() {
  local size=''
  command -v stty >/dev/null 2>&1 || return 0
  size="$(stty size 2>/dev/null)" || size=''
  if [[ "$size" =~ ^[0-9]+[[:space:]]+([0-9]+)$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
  return 0
}

stty_rows() {
  local size=''
  command -v stty >/dev/null 2>&1 || return 0
  size="$(stty size 2>/dev/null)" || size=''
  if [[ "$size" =~ ^([0-9]+)[[:space:]]+[0-9]+$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
  return 0
}

tput_columns() {
  command -v tput >/dev/null 2>&1 || return 0
  tput cols 2>/dev/null || true
  return 0
}

tput_lines() {
  command -v tput >/dev/null 2>&1 || return 0
  tput lines 2>/dev/null || true
  return 0
}

detect_terminal_columns() {
  local candidate
  terminal_columns=80
  for candidate in "$(stty_columns)" "$(tput_columns)" "${COLUMNS:-}"; do
    if [[ "$candidate" =~ ^[0-9]+$ ]] && (( candidate >= 20 )); then
      terminal_columns="$candidate"
      return 0
    fi
  done
  return 0
}

# The height is only used to decide whether one option block can be repainted
# in place without pushing earlier output off the screen.
detect_terminal_rows() {
  local candidate
  terminal_rows=24
  for candidate in "$(stty_rows)" "$(tput_lines)" "${LINES:-}"; do
    if [[ "$candidate" =~ ^[0-9]+$ ]] && (( candidate >= 8 )); then
      terminal_rows="$candidate"
      return 0
    fi
  done
  return 0
}

detect_terminal_columns
detect_terminal_rows
# Long measured lines are hard to read, and very narrow ones need a stacked
# layout instead of aligned columns.
ui_width="$terminal_columns"
(( ui_width > 72 )) && ui_width=72
(( ui_width < 36 )) && ui_width=36
ui_wrap_fields=0
# Number of whole lines written since the last reset. Only the option block
# uses it, to learn its own height before repainting itself.
ui_lines=0

# Two separate capabilities, because they have different requirements.
#
# choice_keys reads one keystroke at a time so the arrow keys can move the >
# marker. It needs a real terminal on both stdin and stdout and a TERM that is
# not 'dumb'. It writes nothing that a plain terminal cannot show.
#
# choice_repaint additionally moves the cursor to redraw the active option
# block in place. It is held to the same bar as color, so NO_COLOR, TERM=dumb,
# a redirected stream, or a noninteractive run gets no escape sequence at all.
# Without it the arrow keys still work and the block is simply reprinted below,
# which keeps every earlier line of the wizard intact.
choice_keys=0
choice_repaint=0
if [[ -t 0 && -t 1 && "${TERM:-dumb}" != 'dumb' ]]; then
  choice_keys=1
  if [[ "$ui_styled" -eq 1 ]]; then
    choice_repaint=1
  fi
fi

# Reading single keystrokes turns echo off for the length of one read. Keep a
# copy of the original terminal settings so Ctrl+C hands back a normal terminal.
# Nothing has been written to disk at this point, so the exit is always clean.
choice_tty_state=''
if [[ "$choice_keys" -eq 1 ]] && command -v stty >/dev/null 2>&1; then
  choice_tty_state="$(stty -g 2>/dev/null)" || choice_tty_state=''
fi

restore_choice_tty() {
  [[ -n "$choice_tty_state" ]] || return 0
  stty "$choice_tty_state" 2>/dev/null || true
  return 0
}

on_interrupt() {
  restore_choice_tty
  printf '\nSetup stopped. Nothing was changed.\n' >&2
  exit 130
}

trap on_interrupt INT

repeat_char() {
  local char="$1"
  local count="$2"
  local out=''
  while (( count > 0 )); do
    out="$out$char"
    count=$((count - 1))
  done
  REPEAT_RESULT="$out"
}

print_rule() {
  local char="${1:--}"
  repeat_char "$char" "$ui_width"
  printf '%s%s%s\n' "$STYLE_DIM" "$REPEAT_RESULT" "$STYLE_RESET"
}

# Line-oriented wrapper. Model IDs contain bracket characters, so globbing is
# disabled while the text is split into words.
wrap_lines() {
  local first_prefix="$1"
  local cont_prefix="$2"
  local style="$3"
  local text="$4"
  local reset='' prefix line word limit glob_was_off=0
  [[ -n "$style" ]] && reset="$STYLE_RESET"
  case "$-" in
    *f*) glob_was_off=1 ;;
    *) set -f ;;
  esac
  prefix="$first_prefix"
  limit=$((ui_width - ${#prefix}))
  (( limit < 16 )) && limit=16
  line=''
  for word in $text; do
    if [[ -z "$line" ]]; then
      line="$word"
    elif (( ${#line} + 1 + ${#word} <= limit )); then
      line="$line $word"
    else
      printf '%s%s%s%s\n' "$prefix" "$style" "$line" "$reset"
      ui_lines=$((ui_lines + 1))
      prefix="$cont_prefix"
      limit=$((ui_width - ${#prefix}))
      (( limit < 16 )) && limit=16
      line="$word"
    fi
  done
  if [[ -n "$line" ]]; then
    printf '%s%s%s%s\n' "$prefix" "$style" "$line" "$reset"
    ui_lines=$((ui_lines + 1))
  fi
  (( glob_was_off == 1 )) || set +f
  return 0
}

# Counted output. Every whole line the option block writes goes through one of
# these so the block always knows exactly how tall it is.
ui_line() {
  printf "$@"
  ui_lines=$((ui_lines + 1))
}

ui_blank() {
  printf '\n'
  ui_lines=$((ui_lines + 1))
}

wrap_text() {
  wrap_lines "$1" "$1" "$2" "$3"
}

print_notice() {
  printf '  %s!%s %s\n' "$STYLE_ACCENT" "$STYLE_RESET" "$1" >&2
}

BRAND_ROWS=(
' ###   ###  ####  #      ###   #### #   #'
'#   #   #   #   # #     #   # #     #  #'
'#####   #   ####  #     #   # #     ###'
'#   #   #   #  #  #     #   # #     #  #'
'#   #  ###  #   # #####  ###   #### #   #'
)

print_brand() {
  local row
  printf '\n'
  if (( ui_width >= 44 )); then
    for row in "${BRAND_ROWS[@]}"; do
      printf '%s%s%s\n' "$STYLE_ACCENT$STYLE_BOLD" "$row" "$STYLE_RESET"
    done
  else
    printf '%s%s%s\n' "$STYLE_ACCENT$STYLE_BOLD" 'AIRLOCK' "$STYLE_RESET"
  fi
  printf '\n'
}

print_header() {
  local heading='Airlock setup wizard'
  local version=''
  if [[ -f "$repo_root/VERSION" ]]; then
    IFS= read -r version < "$repo_root/VERSION" || version=''
  fi
  print_brand
  if [[ -n "$version" ]] && (( ${#heading} + 9 + ${#version} <= ui_width )); then
    heading="$heading   version $version"
  fi
  printf '%s%s%s\n' "$STYLE_BOLD" "$heading" "$STYLE_RESET"
  print_rule '='
  wrap_text '' '' 'Airlock runs OpenAI and Anthropic models in one Claude Code session, with credentials that never cross.'
  printf '\n'
  wrap_text '' '' 'This wizard asks six short sets of questions and ends with a review screen. Nothing on your machine changes until you accept that screen.'
  printf '\n'
  wrap_text '' '' 'It does not change native Claude Code, native Codex, global settings, global hooks, registered plugins, or MCP configuration, and it never reads, copies, or stores a login token.'
  printf '\n'
  wrap_text '' "$STYLE_DIM" 'At every question, press Enter to accept the choice marked with >. In a normal terminal the Up and Down arrow keys move that marker. You can always type a number or a name instead. Type ? to see the choices again. Press Ctrl+C to stop without changes.'
}

print_section() {
  local step="$1"
  local title="$2"
  local description="$3"
  local track left pad
  repeat_char '#' "$step"
  track="$REPEAT_RESULT"
  repeat_char '-' $((6 - step))
  track="[$track$REPEAT_RESULT]"
  left="STEP $step OF 6   $title"
  printf '\n'
  print_rule '='
  if (( ui_width >= ${#left} + ${#track} + 2 )); then
    pad=$((ui_width - ${#left} - ${#track}))
    printf '%s%s%s%*s%s%s%s\n' "$STYLE_BOLD" "$left" "$STYLE_RESET" "$pad" '' "$STYLE_ACCENT" "$track" "$STYLE_RESET"
  else
    printf '%sSTEP %s OF 6%s  %s%s%s\n' "$STYLE_BOLD" "$step" "$STYLE_RESET" "$STYLE_ACCENT" "$track" "$STYLE_RESET"
    printf '%s%s%s\n' "$STYLE_BOLD" "$title" "$STYLE_RESET"
  fi
  wrap_text '' '' "$description"
}

print_question() {
  local title="$1"
  local detail="${2:-}"
  printf '\n'
  wrap_text '' "$STYLE_BOLD" "$title"
  if [[ -n "$detail" ]]; then
    wrap_text '' "$STYLE_DIM" "$detail"
  fi
}

print_group_heading() {
  printf '\n%s%s%s\n' "$STYLE_BOLD" "$1" "$STYLE_RESET"
}

# Option specs are value|title|identifier|detail. The identifier is the exact
# model ID where one exists, and stays visible next to the readable name.
# Reads one answer from a terminal that supports single keystrokes.
#
# Sets CHOICE_KEY to up, down, enter, other, or text. For text it also sets
# CHOICE_INPUT to the whole typed line. Ctrl+C is untouched: the read runs with
# signals enabled, so it still interrupts the wizard before anything is written.
# Returns non-zero at end of input so the caller fails the same way the plain
# line reader does.
#
# CHOICE_PROMPT_OPEN reports whether the cursor is still sitting on the prompt
# line, which the caller needs to repaint the block by the right number of rows.
read_choice_key() {
  local key='' next='' char='' final='' rest=''
  CHOICE_KEY='other'
  CHOICE_INPUT=''
  CHOICE_PROMPT_OPEN=1
  IFS= read -rsn1 key || return 1
  if [[ -z "$key" ]]; then
    # A silent read swallows the newline the user pressed, so end the line here.
    printf '\n'
    CHOICE_KEY='enter'
    CHOICE_PROMPT_OPEN=0
    return 0
  fi
  if [[ "$key" == $'\033' ]]; then
    # Arrow keys arrive as ESC [ A or ESC O A. Read the introducer and then
    # drain to the final byte so a longer sequence, a function key, or a bare
    # Escape can never leak digits into the answer. The timeout keeps a lone
    # Escape from blocking.
    IFS= read -rsn1 -t 1 next || next=''
    if [[ "$next" == '[' || "$next" == 'O' ]]; then
      final="$next"
      while IFS= read -rsn1 -t 1 char; do
        [[ -n "$char" ]] || break
        final="$char"
        case "$char" in
          [A-Za-z~]) break ;;
        esac
      done
      case "$final" in
        A) CHOICE_KEY='up' ;;
        B) CHOICE_KEY='down' ;;
      esac
    fi
    return 0
  fi
  # Anything else starts a typed answer. Echo the first character by hand,
  # because it was read silently, then let the terminal's own line editing
  # collect the rest.
  printf '%s' "$key"
  IFS= read -r rest || rest=''
  CHOICE_KEY='text'
  CHOICE_INPUT="$key$rest"
  CHOICE_PROMPT_OPEN=0
  return 0
}

choose_rich_option() {
  local current="$1"
  local recommended="$2"
  shift 2
  local options=("$@")
  local answer default_value default_index default_title
  local index spec value remainder title identifier detail
  local badge badge_style row row_prefix row_text row_style extra pad
  # The > marker and the Enter default are the same thing, so moving the marker
  # is simply moving default_index.
  local repaint=0 block_lines=0 prompt_open=0 rows_up=0
  default_value="${current:-$recommended}"
  default_index=0
  default_title=''
  index=1
  for spec in ${options[@]+"${options[@]}"}; do
    value="${spec%%|*}"
    if [[ "$value" == "$default_value" && "$default_index" -eq 0 ]]; then
      default_index="$index"
      remainder="${spec#*|}"
      default_title="${remainder%%|*}"
    fi
    index=$((index + 1))
  done
  while true; do
    # Repaint in place only when the whole block plus its prompt line is known
    # to be on screen. Anything taller has scrolled, so the block is reprinted
    # below instead. Either way nothing above the block is touched.
    if (( repaint == 1 && choice_repaint == 1 && block_lines + 1 <= terminal_rows )); then
      rows_up="$block_lines"
      (( prompt_open == 1 )) || rows_up=$((rows_up + 1))
      if (( rows_up > 0 )); then
        printf '\r\033[%dA\033[J' "$rows_up"
      else
        printf '\r\033[J'
      fi
    else
      # End the prompt line first when the keystroke that got us here was not
      # echoed, then leave the usual blank line above the block.
      (( repaint == 1 && prompt_open == 1 )) && printf '\n'
      printf '\n'
    fi
    repaint=0
    prompt_open=0
    ui_lines=0
    index=1
    for spec in ${options[@]+"${options[@]}"}; do
      value="${spec%%|*}"
      remainder="${spec#*|}"
      title="${remainder%%|*}"
      remainder="${remainder#*|}"
      identifier="${remainder%%|*}"
      detail="${remainder#*|}"
      badge=''
      badge_style=''
      if [[ "$had_existing_config" -eq 1 && "$value" == "$current" ]]; then
        badge='[current]'
        badge_style="$STYLE_GREEN"
      elif [[ "$value" == "$recommended" ]]; then
        badge='[recommended]'
        badge_style="$STYLE_ACCENT"
      fi
      row_style=''
      if [[ "$value" == "$default_value" ]]; then
        printf -v row_prefix '  %s %2d) ' '>' "$index"
        row_style="$STYLE_BOLD"
      else
        printf -v row_prefix '  %s %2d) ' ' ' "$index"
      fi
      row_text="$title"
      extra=''
      if [[ -n "$identifier" ]]; then
        if (( ${#row_prefix} + ${#row_text} + 4 + ${#identifier} <= ui_width )); then
          row_text="$row_text  ($identifier)"
        else
          extra="$identifier"
        fi
      fi
      row="$row_prefix$row_text"
      if (( ${#row} > ui_width )); then
        wrap_lines "$row_prefix" '        ' "$row_style" "$row_text"
        if [[ -n "$badge" ]]; then
          ui_line '        %s%s%s\n' "$badge_style" "$badge" "$STYLE_RESET"
        fi
      elif [[ -n "$badge" ]] && (( ui_width - ${#row} - ${#badge} >= 2 )); then
        pad=$((ui_width - ${#row} - ${#badge}))
        ui_line '%s%s%s%*s%s%s%s\n' "$row_style" "$row" "$STYLE_RESET" "$pad" '' "$badge_style" "$badge" "$STYLE_RESET"
      else
        ui_line '%s%s%s\n' "$row_style" "$row" "$STYLE_RESET"
        if [[ -n "$badge" ]]; then
          ui_line '        %s%s%s\n' "$badge_style" "$badge" "$STYLE_RESET"
        fi
      fi
      if [[ -n "$extra" ]]; then
        ui_line '        %s%s%s\n' "$STYLE_DIM" "$extra" "$STYLE_RESET"
      fi
      if [[ -n "$detail" ]]; then
        wrap_text '        ' "$STYLE_DIM" "$detail"
      fi
      index=$((index + 1))
    done
    ui_blank
    if (( default_index > 0 )); then
      wrap_text '  ' '' "Press Enter to accept $default_index) $default_title"
    else
      wrap_text '  ' '' "Press Enter to accept $default_value"
    fi
    if [[ "$choice_keys" -eq 1 ]]; then
      wrap_text '  ' "$STYLE_DIM" 'The Up and Down arrow keys move the > marker.'
    fi
    # The prompt itself is identical in every mode, so a redirected or plain
    # run reads exactly the same as a rich one.
    printf '  Choice [1-%d, name, or ?]: ' "${#options[@]}"
    block_lines="$ui_lines"
    prompt_open=1
    if [[ "$choice_keys" -eq 1 ]]; then
      read_choice_key
      prompt_open="$CHOICE_PROMPT_OPEN"
      case "$CHOICE_KEY" in
        up|down)
          if [[ "$CHOICE_KEY" == 'up' ]]; then
            default_index=$((default_index - 1))
            (( default_index >= 1 )) || default_index=${#options[@]}
          else
            default_index=$((default_index + 1))
            (( default_index <= ${#options[@]} )) || default_index=1
          fi
          spec="${options[$((default_index - 1))]}"
          default_value="${spec%%|*}"
          remainder="${spec#*|}"
          default_title="${remainder%%|*}"
          repaint=1
          continue
          ;;
        other)
          # An unrecognized key just shows the list again.
          repaint=1
          continue
          ;;
        enter) answer='' ;;
        *) answer="$CHOICE_INPUT" ;;
      esac
    else
      IFS= read -r answer
      prompt_open=0
    fi
    answer="${answer:-$default_value}"
    if [[ "$answer" == '?' || "$answer" == 'help' ]]; then
      repaint=1
      continue
    fi
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
    print_notice "That is not one of the listed choices. Enter a number from 1 to ${#options[@]}, a listed name, or ? to see the list again."
  done
}

ask_yes_no() {
  local label="$1"
  local recommended="$2"
  local answer hint
  if [[ "$recommended" == 'yes' ]]; then hint='[Y/n, Enter = yes]'; else hint='[y/N, Enter = no]'; fi
  while true; do
    printf '\n'
    wrap_text '  ' "$STYLE_BOLD" "$label"
    printf '  Answer %s: ' "$hint"
    IFS= read -r answer
    answer="${answer:-$recommended}"
    case "$answer" in
      y|Y|yes|YES|Yes) ANSWER='yes'; return ;;
      n|N|no|NO|No) ANSWER='no'; return ;;
      *) print_notice 'Please answer y or n.' ;;
    esac
  done
}

ask_apply_decision() {
  local answer
  while true; do
    printf '\n'
    wrap_text '  ' '' 'Press Enter to apply. Type n to quit without changes, or s to start over.'
    printf '  Apply this configuration? [Y/n/s]: '
    IFS= read -r answer
    answer="${answer:-y}"
    case "$answer" in
      y|Y|yes|YES|Yes) APPLY_DECISION='apply'; return ;;
      n|N|no|NO|No) APPLY_DECISION='quit'; return ;;
      s|S|start|start-over|restart) APPLY_DECISION='restart'; return ;;
      *) print_notice 'Please answer y to apply, n to quit, or s to start over.' ;;
    esac
  done
}

print_field() {
  local label="$1"
  local value="$2"
  local line first
  printf -v line '  %-20s%s' "$label" "$value"
  if [[ "$ui_wrap_fields" -ne 1 ]] || (( ${#line} <= ui_width )); then
    printf '%s\n' "$line"
    return 0
  fi
  if [[ "$value" != *[[:space:]]* ]] && (( ${#value} > ui_width - 22 )); then
    printf '  %s\n' "$label"
    printf '      %s\n' "$value"
  elif (( ui_width >= 52 )); then
    printf -v first '  %-20s' "$label"
    wrap_lines "$first" '                      ' '' "$value"
  else
    printf '  %s\n' "$label"
    wrap_lines '      ' '      ' '' "$value"
  fi
  return 0
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

default_main_model="$(setup_model_alias "$default_main_model")"
default_hybrid_model="$(setup_model_alias "$default_hybrid_model")"
default_bg_model="$(setup_model_alias "$default_bg_model")"

utility_alias_from_wire "$default_utility_wire"
default_utility_model="$UTILITY_ALIAS"

if [[ "$assume_yes" -eq 0 && ! -t 0 ]]; then
  printf 'setup: interactive mode needs a terminal. Use explicit options with --yes.\n' >&2
  exit 2
fi

if [[ -z "$default_profile" ]]; then default_profile="$default_default_profile"; fi
if [[ -z "$hybrid_model" ]]; then hybrid_model="$default_hybrid_model"; fi
main_model="$(setup_model_alias "$main_model")"
hybrid_model="$(setup_model_alias "$hybrid_model")"
bg_model="$(setup_model_alias "$bg_model")"
utility_model="$(setup_model_alias "$utility_model")"

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

  print_section 1 'SESSION AND ORCHESTRATOR' 'Choose what the bare `airlock` command starts. An explicit command such as `airlock openai` always overrides this.'
  print_question 'Which session profile should bare `airlock` start?' 'Hybrid keeps several providers reachable from one session. The single-provider profiles stay on the local proxy.'
  choose_rich_option "$default_profile" hybrid \
    'hybrid|Hybrid: Claude and GPT together||Choose any enabled Claude or GPT orchestrator and keep both worker providers available. Grok can be added below.' \
    'openai|OpenAI only||Use the local OpenAI proxy without starting the mixed-provider router.' \
    'grok|Grok only||Use the local subscription proxy on a Grok login without starting the mixed-provider router.'
  default_profile="$CHOICE"

  # Grok rides the same loopback proxy as Codex but authenticates separately,
  # so it stays off until the user says they have a Grok plan. Advertising a
  # worker with no login behind it only fails one Agent call later.
  grok_enabled='no'
  if [[ "$default_profile" == 'grok' ]]; then
    grok_enabled='yes'
  elif [[ "$default_profile" == 'hybrid' ]]; then
    current_grok_answer='no'
    [[ -n "$default_grok_models" ]] && current_grok_answer='yes'
    print_question 'Grok subscription' 'Grok runs through the same local proxy as Codex but needs its own login with `airlock proxy grok auth login`. Leave this off if you do not have a Grok plan.'
    ask_yes_no 'Make Grok models available in hybrid sessions?' "$current_grok_answer"
    grok_enabled="$ANSWER"
  fi
  if [[ "$grok_enabled" == 'no' ]]; then
    case "$hybrid_model" in grok|composer) hybrid_model='' ;; esac
    case "$default_hybrid_model" in grok|composer) default_hybrid_model='sol' ;; esac
  fi

  if [[ "$default_profile" == 'grok' ]]; then
    print_question 'Default orchestrator' 'Grok-only sessions can still use exact Grok workers.'
    choose_rich_option "${grok_model:-$default_grok_model}" grok \
      'grok|Grok 4.5|grok-4.5|Difficult implementation and debugging. Premium usage.' \
      'composer|Grok Composer 2.5 Fast|grok-composer-2.5-fast|Discovery, triage, and bounded mechanical work. Economical usage.'
    grok_model="$CHOICE"
    main_model="$default_main_model"
    hybrid_model="${hybrid_model:-$default_hybrid_model}"
  elif [[ "$default_profile" == 'hybrid' ]]; then
    print_question 'Default orchestrator' 'This model leads the session and decides when to use workers. Every choice below stays available as a worker.'
    hybrid_options=(
      'sol|GPT-5.6 Sol|gpt-5.6-sol|Difficult implementation and integration. Premium usage.'
      'sonnet|Claude Sonnet 5|claude-sonnet-5[1m]|Balanced engineering and repository work. Standard usage.'
      'terra|GPT-5.6 Terra|gpt-5.6-terra|Review and alternative reasoning. Standard usage.'
      'luna|GPT-5.6 Luna|gpt-5.6-luna|Discovery, triage, and bounded work. Economical usage.'
      'opus|Claude Opus 5|claude-opus-5[1m]|Architecture, security, and visual direction. Premium usage.'
      'fable|Claude Fable 5|claude-fable-5[1m]|Efficient frontier work. May require extra usage.'
      'haiku|Claude Haiku 4.5|claude-haiku-4-5-20251001|Fast bounded utility work. Economical usage.'
    )
    if [[ "$grok_enabled" == 'yes' ]]; then
      hybrid_options+=(
        'grok|Grok 4.5|grok-4.5|Difficult implementation and debugging. Premium usage.'
        'composer|Grok Composer 2.5 Fast|grok-composer-2.5-fast|Discovery, triage, and bounded mechanical work. Economical usage.'
      )
    fi
    choose_rich_option "$hybrid_model" sol "${hybrid_options[@]}"
    hybrid_model="$CHOICE"
    main_model="$default_main_model"
  else
    print_question 'Default orchestrator' 'OpenAI-only sessions can still use exact GPT workers.'
    choose_rich_option "${main_model:-$default_main_model}" sol \
      'sol|GPT-5.6 Sol|gpt-5.6-sol|Difficult implementation and integration. Premium usage.' \
      'terra|GPT-5.6 Terra|gpt-5.6-terra|Review and alternative reasoning. Standard usage.' \
      'luna|GPT-5.6 Luna|gpt-5.6-luna|Discovery, triage, and bounded work. Economical usage.' \
      'sol-fast|GPT-5.6 Sol Fast|gpt-5.6-sol-fast|Priority processing on eligible plans only.' \
      '5.5|GPT-5.5|gpt-5.5|Supported OpenAI root.' \
      '5.4|GPT-5.4|gpt-5.4|Supported OpenAI root.' \
      'mini|GPT-5.4 Mini|gpt-5.4-mini|Small OpenAI root.' \
      '5.3|GPT-5.3 Codex|gpt-5.3-codex|Supported Codex root.' \
      'spark|GPT-5.3 Codex Spark|gpt-5.3-codex-spark|Fast supported Codex root.' \
      '5.2|GPT-5.2|gpt-5.2|Supported OpenAI root.'
    main_model="$CHOICE"
  fi

  print_section 2 'WORKER POOL' 'Choose which exact-model Agents the orchestrator may use. More is not always better.'
  print_question 'Model catalog' 'Access depends on the connected plans and is checked again when a session starts.'
  for route in sonnet opus fable haiku luna terra sol; do
    set_model_info "$route"
    if (( ${#MODEL_TITLE} + ${#MODEL_ID} + 6 <= ui_width )); then
      printf '  %s  (%s)\n' "$MODEL_TITLE" "$MODEL_ID"
    else
      printf '  %s\n' "$MODEL_TITLE"
      printf '    %s%s%s\n' "$STYLE_DIM" "$MODEL_ID" "$STYLE_RESET"
    fi
    wrap_text '    ' "$STYLE_DIM" "$MODEL_DETAIL"
  done
  current_preset='custom'
  if [[ "$default_anthropic_models" == 'opus,sonnet' && "$default_openai_models" == 'sol,terra,luna' ]]; then current_preset='balanced'; fi
  if [[ "$default_anthropic_models" == 'opus,sonnet,fable' && "$default_openai_models" == 'sol,terra,luna' ]]; then current_preset='frontier'; fi
  if [[ "$default_anthropic_models" == 'sonnet' && "$default_openai_models" == 'luna' ]]; then current_preset='economy'; fi
  print_question 'Which workers should the orchestrator be allowed to use?'
  choose_rich_option "$current_preset" balanced \
    'balanced|Balanced pool||Claude Opus 5 and Claude Sonnet 5 plus GPT Sol, Terra, and Luna. The router picks one only when it helps.' \
    'economy|Economical pool||Claude Sonnet 5 and GPT Luna. Lower relative usage with broad basic coverage.' \
    'frontier|Balanced pool plus Claude Fable 5||Everything in the balanced pool and Claude Fable 5 (claude-fable-5[1m]). Fable can use extra usage, so it is not on by default.' \
    'custom|Choose models individually||Pick any mix, including Claude Fable 5 and Claude Haiku 4.5.'
  worker_preset="$CHOICE"
  anthropic_models_was_decided=1
  openai_models_was_decided=1
  case "$worker_preset" in
    balanced) anthropic_models='opus,sonnet'; openai_models='sol,terra,luna' ;;
    frontier) anthropic_models='opus,sonnet,fable'; openai_models='sol,terra,luna' ;;
    economy) anthropic_models='sonnet'; openai_models='luna' ;;
    custom)
      anthropic_models=''
      openai_models=''
      print_question 'Choose each worker' 'Answer once per model. A disabled model can still be enabled later with `airlock config`.'
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

  # Grok sits outside the presets on purpose. It bills against a separate
  # subscription, so folding it into 'balanced' would enable a provider the
  # user may not have.
  grok_models_was_decided=1
  if [[ "$grok_enabled" == 'yes' ]]; then
    current_grok_preset='both'
    case "$default_grok_models" in
      grok) current_grok_preset='grok' ;;
      composer) current_grok_preset='composer' ;;
    esac
    print_question 'Which Grok workers should the orchestrator be allowed to use?' 'These run on your Grok subscription through the local proxy.'
    choose_rich_option "$current_grok_preset" both \
      'both|Grok 4.5 and Grok Composer 2.5 Fast||Full Grok pool: one frontier worker and one economical worker.' \
      'grok|Grok 4.5 only|grok-4.5|Difficult implementation and debugging. Premium usage.' \
      'composer|Grok Composer 2.5 Fast only|grok-composer-2.5-fast|Discovery, triage, and bounded mechanical work. Economical usage.'
    case "$CHOICE" in
      both) grok_models='grok,composer' ;;
      grok) grok_models='grok' ;;
      composer) grok_models='composer' ;;
    esac
    # A Grok root that is not itself an enabled worker cannot start, so keep
    # the saved root inside the pool the user just chose.
    if ! csv_contains "$grok_models" "$grok_model"; then
      grok_model="${grok_models%%,*}"
    fi
  else
    grok_models=''
  fi

  print_section 3 'EFFORT' 'Set the starting session level and decide whether workers move with `/effort`.'
  print_question 'Starting session effort' 'You can change this at any time inside a session with `/effort`.'
  choose_rich_option "${main_effort:-$default_main_effort}" high \
    'low|Low||Fastest and least deliberate.' \
    'medium|Medium||A lighter setting for routine work.' \
    'high|High||Recommended default for normal engineering work.' \
    'xhigh|Extra high||More deliberate and more expensive.' \
    'max|Maximum||Use only when the selected model and task justify it.'
  main_effort="$CHOICE"
  existing_worker_pins="${worker_pins:-$default_worker_pins}"
  current_effort_mode='inherit'
  [[ "${worker_effort:-$default_worker_effort}" != 'inherit' ]] && current_effort_mode='pin-all'
  [[ -n "$existing_worker_pins" ]] && current_effort_mode='custom'
  print_question 'Worker effort' 'Claude Code does not expose per-call Agent effort. The orchestrator chooses a worker, but workers either follow the session level or keep a setup-time pin.'
  choose_rich_option "$current_effort_mode" inherit \
    'inherit|Follow session effort||Recommended. `/effort` moves the root and every unpinned worker, including mid-session.' \
    'pin-all|Pin every worker||All named workers keep one level even when the session changes.' \
    'custom|Pin selected models||Choose a level for individual enabled workers and let the rest follow the session.'
  effort_mode="$CHOICE"
  worker_pins=''
  worker_pins_were_decided=1
  case "$effort_mode" in
    inherit) worker_effort='inherit' ;;
    pin-all)
      print_question 'Level for every named worker'
      choose_rich_option "${worker_effort:-$default_worker_effort}" high \
        'low|Low||Pin every named worker to low.' 'medium|Medium||Pin every named worker to medium.' \
        'high|High||Pin every named worker to high.' 'xhigh|Extra high||Pin every named worker to xhigh.' \
        'max|Maximum||Pin every named worker to max.'
      worker_effort="$CHOICE"
      ;;
    custom)
      worker_effort='inherit'
      for route in sonnet opus fable haiku luna terra sol; do
        if csv_contains "$anthropic_models,$openai_models" "$route"; then
          set_model_info "$route"
          pin_for_route "$existing_worker_pins" "$route"
          print_question "$MODEL_TITLE worker effort" "$MODEL_ID"
          choose_rich_option "$PIN_VALUE" inherit \
            'inherit|Follow session||Move with `/effort`.' 'low|Low||Keep this worker at low.' \
            'medium|Medium||Keep this worker at medium.' 'high|High||Keep this worker at high.' \
            'xhigh|Extra high||Keep this worker at xhigh.' 'max|Maximum||Keep this worker at max.'
          add_worker_pin "$route" "$CHOICE"
        fi
      done
      ;;
  esac

  print_section 4 'SAFETY AND BUDGET' 'Choose conservative usage rules and a parallel-worker ceiling.'
  print_question 'Extra usage' 'Some routes can bill beyond the included plan capacity.'
  choose_rich_option "${extra_usage_policy:-$default_extra_usage_policy}" ask \
    'ask|Ask before extra usage||Recommended. Extra-usage routes need explicit confirmation.' \
    'never|Block extra usage||Fail closed instead of using paid extra capacity.' \
    'allow|Allow extra usage||Permit configured extra routes without another confirmation.'
  extra_usage_policy="$CHOICE"
  current_fast='off'
  resolved_openai_fast="${openai_fast:-$default_openai_fast}"
  resolved_anthropic_fast="${anthropic_fast:-$default_anthropic_fast}"
  if [[ "$resolved_openai_fast" == 'on' && "$resolved_anthropic_fast" == 'on' ]]; then
    current_fast='all'
  elif [[ "$resolved_openai_fast" == 'on' ]]; then
    current_fast='openai'
  elif [[ "$resolved_anthropic_fast" == 'on' ]]; then
    current_fast='anthropic'
  fi
  while true; do
    print_question 'Fast startup' 'Choose both providers at once or control them separately. Unsupported models stay at standard speed.'
    choose_rich_option "$current_fast" off \
      'off|Off for both providers||Recommended. Start Airlock sessions at standard speed and avoid Fast-specific usage.' \
      'all|On where supported for both||Enable eligible OpenAI Fast routes and native Anthropic Fast for supported Opus roots.' \
      'openai|OpenAI only||Enable eligible OpenAI Fast routes. Claude sessions start with native Fast off.' \
      'anthropic|Anthropic only||Start supported Opus roots with native Fast. This uses paid Anthropic usage credits from the first token.'
    case "$CHOICE" in
      all) openai_fast='on'; anthropic_fast='on' ;;
      openai) openai_fast='on'; anthropic_fast='off' ;;
      anthropic) openai_fast='off'; anthropic_fast='on' ;;
      off) openai_fast='off'; anthropic_fast='off' ;;
    esac
    if [[ "$anthropic_fast" == 'on' && "$extra_usage_policy" == 'never' ]]; then
      print_notice 'Anthropic Fast uses paid usage credits and cannot be enabled while extra usage is blocked.'
      current_fast="$CHOICE"
      continue
    fi
    break
  done
  print_question 'Routing preference' 'How the orchestrator should trade quality against relative usage.'
  choose_rich_option "${routing_policy:-$default_routing_policy}" balanced \
    'balanced|Balanced||Use the smallest effective route while balancing quality and relative usage.' \
    'quality|Quality first||Prefer stronger eligible routes when the expected benefit justifies them.' \
    'economy|Economy first||Prefer economical eligible routes and smaller initial fan-out.'
  routing_policy="$CHOICE"
  print_question 'Parallel workers' 'An optional ceiling on top-level workers running at the same time.'
  choose_rich_option "${max_agents:-$default_max_agents}" off \
    'off|Claude Code default||Recommended. Do not impose an Airlock-specific ceiling.' \
    '1|One worker||Run one top-level worker at a time.' '2|Two workers||Allow two top-level workers.' \
    '3|Three workers||Allow three top-level workers.' '4|Four workers||Allow four top-level workers.' \
    '5|Five workers||Allow five top-level workers.' '10|Ten workers||Expert setting for wide independent work.'
  max_agents="$CHOICE"

  print_question 'Advanced settings' 'Optional compatibility, performance, and capacity controls. Most people should keep the defaults and skip this.'
  ask_yes_no 'Open Advanced settings?' no
  open_advanced="$ANSWER"
  if [[ "$open_advanced" == 'yes' ]]; then
    printf '\n'
    print_rule '-'
    printf '%sADVANCED SETTINGS%s\n' "$STYLE_BOLD" "$STYLE_RESET"
    wrap_text '' "$STYLE_DIM" 'Press Enter at each question to keep the value you already have.'
    print_rule '-'
    print_question 'Model for the separate `airlock bg` command' '`airlock bg` is a separate convenience command. It does not power normal Agents.'
    choose_rich_option "${bg_model:-$default_bg_model}" sol \
      'sol|GPT-5.6 Sol|gpt-5.6-sol|Background command default.' \
      'terra|GPT-5.6 Terra|gpt-5.6-terra|Review and alternative reasoning.' \
      'luna|GPT-5.6 Luna|gpt-5.6-luna|Economical background work.' \
      'mini|GPT-5.4 Mini|gpt-5.4-mini|Small OpenAI root.'
    bg_model="$CHOICE"
    print_question 'Effort for the separate `airlock bg` command'
    choose_rich_option "${bg_effort:-$default_bg_effort}" medium \
      'low|Low||Low effort for airlock bg.' 'medium|Medium||Recommended for airlock bg.' \
      'high|High||High effort for airlock bg.' 'xhigh|Extra high||Extra-high effort for airlock bg.' \
      'max|Maximum||Maximum effort for airlock bg.'
    bg_effort="$CHOICE"
    print_question 'Utility model' 'The utility model handles lightweight Claude Code requests such as titles.'
    choose_rich_option "${utility_model:-$default_utility_model}" luna \
      'luna|GPT-5.6 Luna|gpt-5.6-luna|Recommended economical utility route.' \
      'terra|GPT-5.6 Terra|gpt-5.6-terra|Standard usage.' \
      'sol|GPT-5.6 Sol|gpt-5.6-sol|Premium usage.' \
      'mini|GPT-5.4 Mini|gpt-5.4-mini|Small OpenAI root.'
    utility_model="$CHOICE"
    print_question 'Luna swarm Fast processing'
    choose_rich_option "${swarm_fast:-$default_swarm_fast}" auto \
      'auto|Automatic||Use Luna Fast only when sanitized plan and proxy checks prove eligibility.' \
      'off|Off||Never use Luna Fast for automatic swarms.' \
      'on|On||Require Luna Fast and fail if the route is ineligible.'
    swarm_fast="$CHOICE"
    print_question 'Worker failover'
    choose_rich_option "${failover_policy:-$default_failover_policy}" ask \
      'ask|Ask before failover||Recommended. Never change model or provider silently.' \
      'never|Never fail over||Stop when the exact route fails.' \
      'allow|Allow failover||Permit policy-approved failover without asking again.'
    failover_policy="$CHOICE"
    if [[ -z "$claude_plan" ]]; then
      if [[ "$claude_plan_was_detected" -eq 1 ]]; then
        claude_plan="$default_claude_plan"
        printf '\n'
        wrap_text '  ' "$STYLE_DIM" "Claude plan tier read from saved access data: $claude_plan"
      else
        print_question 'Claude plan tier' 'Only a capacity hint. Airlock never reads a subscription credential.'
        choose_rich_option "$default_claude_plan" unknown \
          'unknown|Automatic / unknown||Do not guess a Claude subscription tier.' \
          'pro|Claude Pro||Manual capacity hint.' 'max5x|Claude Max 5x||Manual capacity hint.' \
          'max20x|Claude Max 20x||Manual capacity hint.'
        claude_plan="$CHOICE"
      fi
    fi
    if [[ -z "$openai_capacity" ]]; then
      print_question 'Codex capacity' 'Only a capacity hint for planning fan-out.'
      choose_rich_option "$default_openai_capacity" auto \
        'auto|Automatic||Use sanitized observations and clearly labeled inferences.' \
        '1x|1x||Manual capacity hint.' '5x|5x||Manual capacity hint.' '20x|20x||Manual capacity hint.'
      openai_capacity="$CHOICE"
    fi
    if [[ -z "$install_agent" ]]; then
      ask_yes_no 'Install the optional generic airlock-worker in addition to exact model workers?' no
      install_agent="$ANSWER"
    fi
    if [[ "$install_agent" == 'yes' && -z "$subagent_effort" ]]; then
      print_question 'Effort for the optional generic worker'
      choose_rich_option "$default_subagent_effort" inherit \
        'inherit|Follow session effort||Recommended for the optional generic worker.' \
        'low|Low||Pin the optional worker to low.' 'medium|Medium||Pin the optional worker to medium.' \
        'high|High||Pin the optional worker to high.' 'xhigh|Extra high||Pin the optional worker to xhigh.' \
        'max|Maximum||Pin the optional worker to max.'
      subagent_effort="$CHOICE"
    fi
    print_rule '-'
    wrap_text '' "$STYLE_DIM" 'End of Advanced settings.'
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
grok_model="${grok_model:-$default_grok_model}"
if [[ "$grok_models_was_decided" -eq 0 ]]; then grok_models="$default_grok_models"; fi
# A Grok-only profile is meaningless without Grok routes, so an explicit
# --default-profile grok enables the pool the same way `airlock grok` does.
if [[ "$default_profile" == 'grok' && -z "$grok_models" ]]; then grok_models='grok,composer'; fi
extra_usage_policy="${extra_usage_policy:-$default_extra_usage_policy}"
routing_policy="${routing_policy:-$default_routing_policy}"
max_agents="${max_agents:-$default_max_agents}"
openai_fast="${openai_fast:-$default_openai_fast}"
anthropic_fast="${anthropic_fast:-$default_anthropic_fast}"
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
    grok|composer) csv_add "$grok_models" "$hybrid_model"; grok_models="$CSV_RESULT" ;;
  esac
fi
if [[ "$default_profile" == 'grok' ]]; then
  csv_add "$grok_models" "$grok_model"; grok_models="$CSV_RESULT"
fi

if [[ "$assume_yes" -eq 0 ]] && { [[ -z "$run_login" ]] || [[ -z "$start_service" ]]; }; then
  print_section 5 'INSTALLATION' 'Choose what setup may start after it saves the configuration file. The saved file holds no credentials.'
  print_question 'Two separate sign-ins are involved, and Airlock changes neither one'
  wrap_lines '  - ' '    ' '' 'Claude Code is a prerequisite. Install it and sign in with the normal `claude` command yourself. Airlock never changes or reads that sign-in.'
  wrap_lines '  - ' '    ' '' 'Codex OAuth belongs to the local `claude-code-proxy`. It is what gives Airlock access to the OpenAI models, through `claude-code-proxy codex auth login`.'
fi

if [[ -z "$run_login" ]]; then
  if [[ "$assume_yes" -eq 1 ]]; then run_login='yes'; else
    ask_yes_no 'Start Codex OAuth for the local proxy, only if the proxy reports that Codex login is missing?' yes
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
validate_grok_model "$grok_model"
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
validate_provider_fast OpenAI "$openai_fast"
validate_provider_fast Anthropic "$anthropic_fast"
if [[ "$anthropic_fast" == 'on' && "$extra_usage_policy" == 'never' ]]; then
  printf 'setup: Anthropic Fast uses paid usage credits and cannot be combined with --extra-usage never\n' >&2
  exit 2
fi
validate_swarm_fast "$swarm_fast"
validate_failover_policy "$failover_policy"
validate_claude_plan "$claude_plan"
validate_openai_capacity "$openai_capacity"
validate_csv_subset 'Anthropic model' "$anthropic_models" 'opus,sonnet,fable,haiku'
validate_csv_subset 'OpenAI model' "$openai_models" 'sol,terra,luna'
validate_csv_subset 'Grok model' "$grok_models" 'grok,composer'
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
  [[ -n "$grok_models" ]] && default_command_profile='hybrid: Claude, GPT, and Grok workers'
elif [[ "$default_profile" == 'grok' ]]; then
  default_command_model="$(model_summary "$grok_model")"
  default_command_profile='Grok only'
else
  default_command_model="$(model_summary "$main_model")"
  default_command_profile='OpenAI only'
fi
worker_list_display "$anthropic_models"
claude_worker_display="$DISPLAY_LIST"
worker_list_display "$openai_models"
gpt_worker_display="$DISPLAY_LIST"
if [[ -n "$grok_models" ]]; then
  worker_list_display "$grok_models"
  grok_worker_display="$DISPLAY_LIST"
else
  grok_worker_display='none (no Grok subscription selected)'
fi
if [[ -n "$worker_pins" ]]; then
  worker_effort_display="follow session; per-model pins: $worker_pins"
elif [[ "$worker_effort" == 'inherit' ]]; then
  worker_effort_display='follow session /effort'
else
  worker_effort_display="all pinned to $worker_effort"
fi

print_session_fields() {
  print_field 'Default command:' "airlock -> $default_command_model"
  print_field 'Session profile:' "$default_command_profile"
  print_field 'Starting effort:' "$main_effort"
}

print_worker_fields() {
  print_field 'Claude workers:' "$claude_worker_display"
  print_field 'GPT workers:' "$gpt_worker_display"
  print_field 'Grok workers:' "$grok_worker_display"
  print_field 'Worker effort:' "$worker_effort_display"
}

print_policy_fields() {
  print_field 'Extra usage:' "$extra_usage_policy"
  print_field 'Fast startup:' "OpenAI $openai_fast; Anthropic $anthropic_fast"
  print_field 'Routing preference:' "$routing_policy"
  print_field 'Parallel workers:' "$max_agents"
}

print_install_fields() {
  local codex_oauth_display="$run_login"
  [[ "$run_login" == 'yes' ]] && codex_oauth_display='yes, only if Codex login is missing'
  print_field 'Codex OAuth:' "$codex_oauth_display"
  print_field 'Proxy service:' "$start_service"
  print_field 'Proxy storage:' "$proxy_storage_display"
  print_field 'Config path:' "$config_target"
}

print_advanced_fields() {
  print_field 'Advanced:' "airlock bg -> $(model_summary "$bg_model") / $bg_effort; utility -> $(model_summary "$utility_model")"
  print_field 'Fast details:' "Luna swarm $swarm_fast; failover $failover_policy"
  print_field 'Generic worker:' "$install_agent (effort: $subagent_effort)"
}

print_apply_plan() {
  local step=1
  printf '\n%sWhen you accept, Airlock will:%s\n' "$STYLE_BOLD" "$STYLE_RESET"
  wrap_lines "  $step. " '     ' '' 'Write the configuration file shown above. An existing file that differs is backed up first.'
  step=$((step + 1))
  if [[ "$config_only" -eq 1 ]]; then
    wrap_lines "  $step. " '     ' '' 'Stop there, because --config-only was requested. Nothing is installed.'
  else
    wrap_lines "  $step. " '     ' '' 'Install the airlock launcher, helper files, and session plugin under your own user directories.'
    step=$((step + 1))
    if [[ "$run_login" == 'yes' ]]; then
      wrap_lines "  $step. " '     ' '' 'Start Codex OAuth for the local proxy, only if `claude-code-proxy codex auth status` reports that Codex login is missing.'
      step=$((step + 1))
    fi
    if [[ "$start_service" == 'yes' ]]; then
      wrap_lines "  $step. " '     ' '' 'Start the local proxy service on 127.0.0.1.'
      step=$((step + 1))
    fi
    wrap_lines "  $step. " '     ' '' 'Run the doctor check, which makes no model request.'
  fi
  printf '\n%sAirlock will not:%s\n' "$STYLE_BOLD" "$STYLE_RESET"
  wrap_lines '  - ' '    ' '' 'touch your Claude Code sign-in, native Claude Code, or native Codex'
  wrap_lines '  - ' '    ' '' 'change global settings, global hooks, registered plugins, or MCP configuration'
  wrap_lines '  - ' '    ' '' 'read, print, copy, or expose any login token; the upstream proxy keeps its OAuth data private'
  return 0
}

if [[ "$assume_yes" -eq 0 ]]; then
  print_section 6 'REVIEW' 'Nothing on your machine has changed yet. Check these settings, then accept them or start over.'
  ui_wrap_fields=1
  print_group_heading 'Session'
  print_session_fields
  print_group_heading 'Workers'
  print_worker_fields
  print_group_heading 'Safety and budget'
  print_policy_fields
  print_group_heading 'Install actions'
  print_install_fields
  if [[ "$open_advanced" == 'yes' ]]; then
    print_group_heading 'Advanced'
    print_advanced_fields
  fi
  ui_wrap_fields=0
  print_apply_plan
  ask_apply_decision
  case "$APPLY_DECISION" in
    quit)
      printf '\nNo changes made.\n'
      exit 0
      ;;
    restart)
      printf '\nStarting over. Nothing was written.\n'
      exec "${BASH:-bash}" "$0" ${setup_args[@]+"${setup_args[@]}"}
      ;;
  esac
else
  printf '\n%sCONFIGURATION SUMMARY%s\n' "$STYLE_BOLD" "$STYLE_RESET"
  print_session_fields
  print_worker_fields
  print_policy_fields
  print_install_fields
  print_advanced_fields
fi

printf '\n'
if ! mkdir -p "$config_dir" 2>/dev/null || [[ ! -d "$config_dir" || ! -w "$config_dir" ]]; then
  printf 'setup: no writable Airlock configuration directory is available:\n' >&2
  printf '  %s\n' "$config_dir" >&2
  printf 'No files were changed. Airlock does not use sudo or change directory ownership.\n' >&2
  exit 2
fi
rendered_config=''
if ! rendered_config="$(mktemp "$config_dir/.config.XXXXXX" 2>/dev/null)"; then
  printf 'setup: Airlock could not create a configuration file under:\n' >&2
  printf '  %s\n' "$config_dir" >&2
  printf 'No files were changed. Airlock does not use sudo or change directory ownership.\n' >&2
  exit 2
fi
trap 'rm -f "$rendered_config"' EXIT
cat > "$rendered_config" <<EOF
# Managed by https://github.com/Harshkamdar67/Airlock
# Generated by scripts/setup.sh. Do not put credentials in this file.
AIRLOCK_DEFAULT_PROFILE=$default_profile
AIRLOCK_HYBRID_MODEL=$hybrid_model
AIRLOCK_GROK_MODEL=$grok_model
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
AIRLOCK_OPENAI_FAST=$openai_fast
AIRLOCK_ANTHROPIC_FAST=$anthropic_fast
AIRLOCK_SWARM_FAST=$swarm_fast
AIRLOCK_FAILOVER_POLICY=$failover_policy
AIRLOCK_ANTHROPIC_PLAN=$claude_plan
AIRLOCK_OPENAI_CAPACITY=$openai_capacity
AIRLOCK_ANTHROPIC_MODELS=$anthropic_models
AIRLOCK_OPENAI_MODELS=$openai_models
AIRLOCK_GROK_MODELS=$grok_models
AIRLOCK_ANTHROPIC_EXTRA_MODELS=$anthropic_extra_models
AIRLOCK_OPENAI_EXTRA_MODELS=$openai_extra_models
# Applies to OpenAI and Grok roots only. Use auto to let Claude Code decide.
AIRLOCK_CONTEXT_WINDOW=272000
AIRLOCK_GPT_EFFORT_CAPABILITIES=$gpt_effort_capabilities
AIRLOCK_PROXY_URL=http://127.0.0.1:18765
AIRLOCK_PROXY_CONFIG_DIR=$proxy_config_dir
AIRLOCK_PROXY_STATE_HOME=$proxy_state_home
EOF
chmod 0644 "$rendered_config"

if [[ -f "$config_target" ]] && ! cmp -s "$rendered_config" "$config_target"; then
  backup_path="$config_target.backup-$(date +%Y%m%d%H%M%S)-$$"
  cp -p "$config_target" "$backup_path"
  wrap_text '' '' "Backed up existing config to $backup_path"
fi
mv "$rendered_config" "$config_target"
trap - EXIT
wrap_text '' '' "Saved configuration to $config_target"
wrap_text '' '' \
  'OpenRouter remains off until you explicitly store a key with airlock openrouter auth set-key and add an exact model and endpoint with airlock openrouter models add.'

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
AIRLOCK_CONFIG_DIR="$config_dir" "$repo_root/scripts/doctor.sh"
