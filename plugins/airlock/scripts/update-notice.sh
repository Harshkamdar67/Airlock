#!/usr/bin/env bash
# Managed by https://github.com/Harshkamdar67/Airlock
set -u
script_dir="$(cd "${BASH_SOURCE[0]%/*}" 2>/dev/null && pwd)" || exit 0
python_is_usable() {
  local probe_output
  probe_output="$("$1" -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' 2>/dev/null)" || return 1
  [[ -z "$probe_output" ]]
}
for candidate in "${AIRLOCK_PYTHON:-}" python3 python; do
  [[ -z "$candidate" ]] && continue
  if python_is_usable "$candidate"; then
    output="$("$candidate" "$script_dir/update-notice.py" 2>/dev/null)" || exit 0
    output="${output%$'\r'}"
    [[ "$output" == '{"systemMessage":"'*'"}' ]] || exit 0
    message="${output#\{\"systemMessage\":\"}"
    message="${message%\"\}}"
    case "$message" in
      *'"'*|*'\'*|*$'\n'*|*$'\r'*) exit 0 ;;
    esac
    printf '%s\n' "$output"
    exit 0
  fi
done
exit 0
