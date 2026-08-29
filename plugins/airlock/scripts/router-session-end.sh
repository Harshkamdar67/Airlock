#!/usr/bin/env bash
# Managed by https://github.com/Harshkamdar67/Airlock
set -u
script_dir="$(cd "${BASH_SOURCE[0]%/*}" 2>/dev/null && pwd)" || exit 0
python_is_usable() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' >/dev/null 2>&1
}
for candidate in "${AIRLOCK_PYTHON:-}" python3 python; do
  [[ -z "$candidate" ]] && continue
  if python_is_usable "$candidate"; then
    exec "$candidate" "$script_dir/router-session-end.py" 2>/dev/null
  fi
done
exit 0
