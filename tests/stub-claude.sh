#!/usr/bin/env bash
set -euo pipefail

stub_hold_pid=''
record_stub_signal() {
  local signal="$1"
  local status="$2"
  if [[ -n "$stub_hold_pid" ]]; then
    kill "$stub_hold_pid" 2>/dev/null || true
    wait "$stub_hold_pid" 2>/dev/null || true
  fi
  if [[ -n "${AIRLOCK_STUB_SIGNAL_FILE:-}" ]]; then
    printf '%s\n' "$signal" > "$AIRLOCK_STUB_SIGNAL_FILE"
  fi
  exit "$status"
}

trap 'record_stub_signal INT 130' INT
trap 'record_stub_signal TERM 143' TERM
trap 'record_stub_signal HUP 129' HUP

printf 'MODEL=%s\n' "${ANTHROPIC_MODEL:-unset}"
printf 'BASE_URL=%s\n' "${ANTHROPIC_BASE_URL:-unset}"
printf 'OPENAI_BRIDGE=%s\n' "${AIRLOCK_HYBRID:-unset}"
printf 'ANTHROPIC_BRIDGE=%s\n' "${AIRLOCK_GPT_HYBRID:-unset}"
printf 'SMALL_FAST=%s\n' "${ANTHROPIC_SMALL_FAST_MODEL:-unset}"
printf 'DEFAULT_FABLE=%s\n' "${ANTHROPIC_DEFAULT_FABLE_MODEL:-unset}"
printf 'DEFAULT_OPUS=%s\n' "${ANTHROPIC_DEFAULT_OPUS_MODEL:-unset}"
printf 'DEFAULT_SONNET=%s\n' "${ANTHROPIC_DEFAULT_SONNET_MODEL:-unset}"
printf 'DEFAULT_HAIKU=%s\n' "${ANTHROPIC_DEFAULT_HAIKU_MODEL:-unset}"
printf 'FABLE_NAME=%s\n' "${ANTHROPIC_DEFAULT_FABLE_MODEL_NAME:-unset}"
printf 'OPUS_NAME=%s\n' "${ANTHROPIC_DEFAULT_OPUS_MODEL_NAME:-unset}"
printf 'SONNET_NAME=%s\n' "${ANTHROPIC_DEFAULT_SONNET_MODEL_NAME:-unset}"
printf 'HAIKU_NAME=%s\n' "${ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME:-unset}"
printf 'FABLE_CAPS=%s\n' "${ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES:-unset}"
printf 'OPUS_CAPS=%s\n' "${ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES:-unset}"
printf 'SONNET_CAPS=%s\n' "${ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES:-unset}"
printf 'HAIKU_CAPS=%s\n' "${ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES:-unset}"
printf 'CUSTOM_CAPS=%s\n' "${ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES:-unset}"
printf 'CUSTOM_NAME=%s\n' "${ANTHROPIC_CUSTOM_MODEL_OPTION_NAME:-unset}"
printf 'ALLOWED_AGENTS=%s\n' "${AIRLOCK_ALLOWED_AGENT_NAMES-unset}"
printf 'ALLOWED_MODELS=%s\n' "${AIRLOCK_ALLOWED_AGENT_MODELS-unset}"
printf 'EXTRA_AGENTS=%s\n' "${AIRLOCK_EXTRA_USAGE_AGENT_NAMES-unset}"
printf 'EXTRA_MODELS=%s\n' "${AIRLOCK_EXTRA_USAGE_AGENT_MODELS-unset}"
if [[ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
  printf 'AUTH_TOKEN_SET=yes\n'
else
  printf 'AUTH_TOKEN_SET=no\n'
fi
printf 'EFFORT_ENV=%s\n' "${CLAUDE_CODE_EFFORT_LEVEL:-unset}"
printf 'ACTIVE_PROFILE=%s\n' "${AIRLOCK_ACTIVE_PROFILE:-unset}"
printf 'ROOT_MODEL=%s\n' "${AIRLOCK_ROOT_MODEL:-unset}"
printf 'DISCOVERY_MODEL=%s\n' "${AIRLOCK_DISCOVERY_MODEL:-unset}"
printf 'SESSION_ROUTER=%s\n' "${AIRLOCK_SESSION_ROUTER_URL:-unset}"
printf 'MAX_SUBAGENTS=%s\n' "${CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS:-unset}"
printf 'SUBAGENT_MODEL=%s\n' "${CLAUDE_CODE_SUBAGENT_MODEL:-unset}"
printf 'SPAWN_DEPTH=%s\n' "${CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH:-unset}"
printf 'PYTHON_BIN=%s\n' "${AIRLOCK_PYTHON:-unset}"
printf 'COMPACT_WINDOW=%s\n' "${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-unset}"
if [[ "${AIRLOCK_STUB_INSPECT_ROUTER:-0}" == '1' ]]; then
  python - <<'PY'
import http.client
import json
import os
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["ANTHROPIC_BASE_URL"])
if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
    raise SystemExit("stub router inspection requires a loopback URL")
connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
connection.request("GET", "/v1/models")
response = connection.getresponse()
payload = json.loads(response.read())
connection.close()
if response.status != 200:
    raise SystemExit("stub router inspection failed")
print("ROUTER_MODELS=" + ",".join(sorted(item["id"] for item in payload["data"])))
PY
fi
python - "$@" <<'PY'
import json
import sys
arguments = sys.argv[1:]
print("ARGS_JSON=" + json.dumps(arguments))
if "--settings" in arguments:
    index = arguments.index("--settings")
    settings = json.loads(arguments[index + 1])
    fast_mode = settings.get("fastMode", "inherit")
    if isinstance(fast_mode, bool):
        fast_mode = "on" if fast_mode else "off"
    print("FAST_MODE=" + str(fast_mode))
PY
for argument in "$@"; do
  printf 'ARG=%s\n' "$argument"
done

stub_hold_seconds="${AIRLOCK_STUB_HOLD_SECONDS:-0}"
stub_exit_status="${AIRLOCK_STUB_EXIT_STATUS:-0}"
if [[ ! "$stub_hold_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  printf 'stub: AIRLOCK_STUB_HOLD_SECONDS must be a non-negative number\n' >&2
  exit 2
fi
if [[ ! "$stub_exit_status" =~ ^[0-9]+$ ]] || (( stub_exit_status > 255 )); then
  printf 'stub: AIRLOCK_STUB_EXIT_STATUS must be from 0 to 255\n' >&2
  exit 2
fi
if [[ "$stub_hold_seconds" != '0' ]]; then
  sleep "$stub_hold_seconds" &
  stub_hold_pid=$!
  wait "$stub_hold_pid"
  stub_hold_pid=''
fi
exit "$stub_exit_status"
