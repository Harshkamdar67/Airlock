#!/usr/bin/env bash
set -euo pipefail

printf 'MODEL=%s\n' "${ANTHROPIC_MODEL:-unset}"
printf 'BASE_URL=%s\n' "${ANTHROPIC_BASE_URL:-unset}"
printf 'OPENAI_BRIDGE=%s\n' "${AIRLOCK_HYBRID:-unset}"
printf 'ANTHROPIC_BRIDGE=%s\n' "${AIRLOCK_GPT_HYBRID:-unset}"
printf 'SMALL_FAST=%s\n' "${ANTHROPIC_SMALL_FAST_MODEL:-unset}"
printf 'DEFAULT_OPUS=%s\n' "${ANTHROPIC_DEFAULT_OPUS_MODEL:-unset}"
printf 'DEFAULT_SONNET=%s\n' "${ANTHROPIC_DEFAULT_SONNET_MODEL:-unset}"
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
printf 'MAX_SUBAGENTS=%s\n' "${CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS:-unset}"
printf 'SUBAGENT_MODEL=%s\n' "${CLAUDE_CODE_SUBAGENT_MODEL:-unset}"
printf 'SPAWN_DEPTH=%s\n' "${CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH:-unset}"
printf 'PYTHON_BIN=%s\n' "${AIRLOCK_PYTHON:-unset}"
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
print("ARGS_JSON=" + json.dumps(sys.argv[1:]))
PY
for argument in "$@"; do
  printf 'ARG=%s\n' "$argument"
done
