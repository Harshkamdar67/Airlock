#!/usr/bin/env bash
# Managed by https://github.com/Harshkamdar67/Airlock
set -euo pipefail
script_dir="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
python_is_usable() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' >/dev/null 2>&1
}
for candidate in "${AIRLOCK_PYTHON:-}" python3 python; do
  [[ -z "$candidate" ]] && continue
  if python_is_usable "$candidate"; then
    exec "$candidate" "$script_dir/worktree.py" remove
  fi
done
printf '%s\n' 'airlock worktree: no usable Python 3 interpreter was found' >&2
exit 1
