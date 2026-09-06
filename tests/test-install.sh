#!/usr/bin/env bash
# Stub-based macOS and Linux installer checks. These tests do not use OAuth,
# Homebrew, or a model. They are the POSIX counterpart of tests/test-windows.ps1.
set -euo pipefail

case "$(uname -s)" in
  Darwin|Linux) ;;
  *)
    printf 'test: the POSIX installer test runs on macOS and Linux only.\n'
    exit 0
    ;;
esac

test_python="$(command -v python3 || command -v python)"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Resolve the temporary path so that the launcher path the doctor prints matches
# the one this test greps for. macOS hands out a symlinked TMPDIR with a
# trailing slash, which would otherwise produce two spellings of one path.
tmp_dir="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/airlock-install-test.XXXXXX")" && pwd -P)"
trap 'rm -rf "$tmp_dir"' EXIT

stub_dir="$tmp_dir/stubs"
install_dir="$tmp_dir/bin"
config_home="$tmp_dir/config"
agent_dir="$tmp_dir/agents"
config_dir="$config_home/airlock"
mkdir -p "$stub_dir"

# A missing Claude Code binary must stop before any managed file is written.
missing_stub_dir="$tmp_dir/missing-stubs"
mkdir -p "$missing_stub_dir"
for name in brew curl install python3 claude-code-proxy; do
  printf '#!/usr/bin/env bash\nexit 0\n' > "$missing_stub_dir/$name"
  chmod 0755 "$missing_stub_dir/$name"
done
platform_name="$(uname -s)"
printf '#!/usr/bin/env bash\nprintf "%%s\\n" %q\n' "$platform_name" > "$missing_stub_dir/uname"
chmod 0755 "$missing_stub_dir/uname"
if PATH="$missing_stub_dir:/usr/bin:/bin" \
  HOME="$tmp_dir/missing-home" \
  AIRLOCK_INSTALL_DIR="$tmp_dir/missing-bin" \
  AIRLOCK_CONFIG_DIR="$tmp_dir/missing-config" \
  bash "$repo_root/scripts/install.sh" --no-service > "$tmp_dir/missing.out" 2> "$tmp_dir/missing.err"; then
  printf 'test: installer accepted a missing Claude Code binary\n' >&2
  exit 1
fi
grep -q '^install: Claude Code is missing\.' "$tmp_dir/missing.err"
test ! -e "$tmp_dir/missing-bin/airlock"

# On macOS and Linux, a missing proxy is installed with Homebrew before its
# OAuth status is checked. The stub creates a local proxy command only.
proxy_stub_dir="$tmp_dir/proxy-missing-stubs"
mkdir -p "$proxy_stub_dir"
printf '#!/usr/bin/env bash\nprintf "%%s\\n" %q\n' "$platform_name" > "$proxy_stub_dir/uname"
printf '#!/usr/bin/env bash\nexit 0\n' > "$proxy_stub_dir/python3"
printf '#!/usr/bin/env bash\nif [[ "${1:-}" == "--version" ]]; then printf "Claude Code test\\n"; fi\nexit 0\n' > "$proxy_stub_dir/claude"
cat > "$proxy_stub_dir/brew" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "install" ]]; then
  cat > "$AIRLOCK_TEST_PROXY_TARGET" <<'PROXY'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then printf 'Proxy test\n'; fi
exit 0
PROXY
  chmod 0755 "$AIRLOCK_TEST_PROXY_TARGET"
fi
exit 0
EOF
chmod 0755 "$proxy_stub_dir/uname" "$proxy_stub_dir/python3" "$proxy_stub_dir/claude" "$proxy_stub_dir/brew"
AIRLOCK_TEST_PROXY_TARGET="$proxy_stub_dir/claude-code-proxy" \
PATH="$proxy_stub_dir:/usr/bin:/bin" \
HOME="$tmp_dir/proxy-home" \
AIRLOCK_INSTALL_DIR="$tmp_dir/proxy-bin" \
AIRLOCK_CONFIG_DIR="$tmp_dir/proxy-config" \
AIRLOCK_AGENT_DIR="$tmp_dir/proxy-agents" \
  bash "$repo_root/scripts/install.sh" --no-service > "$tmp_dir/proxy-install.out"
grep -q '^Installing raine/claude-code-proxy with Homebrew\.\.\.$' "$tmp_dir/proxy-install.out"
test -x "$tmp_dir/proxy-bin/airlock"

write_stub() {
  local name="$1"
  shift
  {
    printf '#!/usr/bin/env bash\n'
    printf '%s\n' "$@"
    printf 'exit 0\n'
  } > "$stub_dir/$name"
  chmod 0755 "$stub_dir/$name"
}

write_stub brew ''
write_stub claude 'if [[ "${1:-}" == "--version" ]]; then printf "Claude Code test\\n"; fi'
write_stub claude-code-proxy \
  'if [[ "${1:-}" == "--version" ]]; then printf "Proxy test\\n"; fi' \
  'if [[ "${1:-} ${2:-} ${3:-}" == "codex auth status" ]]; then [[ -f "$AIRLOCK_TEST_AUTH_STATE" ]]; exit; fi' \
  'if [[ "${1:-} ${2:-} ${3:-}" == "codex auth login" ]]; then : > "$AIRLOCK_TEST_AUTH_STATE"; exit 0; fi'

export PATH="$stub_dir:$PATH"
export XDG_CONFIG_HOME="$config_home"
export AIRLOCK_INSTALL_DIR="$install_dir"
export AIRLOCK_AGENT_DIR="$agent_dir"
export AIRLOCK_TEST_AUTH_STATE="$tmp_dir/proxy-authenticated"
unset AIRLOCK_CONFIG_FILE AIRLOCK_ACCESS_FILE AIRLOCK_PLUGIN_DIR AIRLOCK_MANAGED_BUNDLE_FILE

# A present proxy without Codex OAuth must stop cleanly unless login was
# explicitly allowed. The interactive login stub stores only a fake state bit.
if "$repo_root/scripts/install.sh" --no-service > "$tmp_dir/auth-missing.out" 2>&1; then
  printf 'test: installer accepted a signed-out Codex proxy\n' >&2
  exit 1
else
  auth_status=$?
fi
test "$auth_status" -eq 2
grep -q '^Codex OAuth needs interactive approval\.' "$tmp_dir/auth-missing.out"
test ! -e "$install_dir/airlock"

"$repo_root/scripts/install.sh" --login --no-service > "$tmp_dir/login.out"
test -f "$AIRLOCK_TEST_AUTH_STATE"
grep -q '^Airlock installed\.$' "$tmp_dir/login.out"

"$repo_root/scripts/install.sh" --with-agent --no-service > "$tmp_dir/install.out"
grep -q '^Airlock installed\.$' "$tmp_dir/install.out"

for relative in airlock airlock-access.py airlock_policy.py \
  airlock_openmodel.py airlock_openmodel_adapter.py \
  airlock_openrouter_auth.py airlock_openrouter_presets.py \
  airlock_openrouter_models.py airlock-update.py \
  airlock-router.py airlock-hybrid.py airlock_console.py \
  airlock_console_tools.py airlock_console_history.py; do
  if [[ ! -f "$install_dir/$relative" || ! -x "$install_dir/$relative" ]]; then
    printf 'test: installer did not place an executable %s\n' "$relative" >&2
    exit 1
  fi
done
for removed in airlock_runtime.py airlock-delegate.py airlock-workflow.py airlock-child.py airlock-check.py; do
  if [[ -e "$install_dir/$removed" ]]; then
    printf 'test: installer wrote a removed legacy file: %s\n' "$removed" >&2
    exit 1
  fi
done

for relative in config managed-bundle.json openai-direct-agents.json \
  anthropic-direct-agents.json hybrid-agents.json claude-agents.json; do
  if [[ ! -f "$config_dir/$relative" ]]; then
    printf 'test: installer missed config file %s\n' "$relative" >&2
    exit 1
  fi
done
if [[ -e "$config_dir/openrouter-registry.json" ]]; then
  printf 'test: installer enabled OpenRouter without an explicit add command\n' >&2
  exit 1
fi
if [[ -e "$config_dir/openmodel-registry.json" ]]; then
  printf 'test: installer enabled an open model without an explicit add command\n' >&2
  exit 1
fi

plugin_dir="$config_dir/plugins/airlock"
for relative in .claude-plugin/plugin.json hooks/hooks.json skills/usage/SKILL.md \
  skills/airlock-fast/SKILL.md scripts/file_safety.py; do
  if [[ ! -f "$plugin_dir/$relative" ]]; then
    printf 'test: installer missed plugin file %s\n' "$relative" >&2
    exit 1
  fi
done
# The hook scripts are launched directly by Claude Code, so the executable bit
# is part of a working install rather than a detail of the file mode.
for relative in mcp-server/airlock_web_tools.py \
  mcp-server/airlock_console_mcp.py \
  scripts/fast-session-end.sh scripts/fast-session-end.py \
  scripts/agent-guard.sh scripts/agent-guard.py scripts/secret-guard.sh \
  scripts/secret-guard.py scripts/update-notice.sh scripts/update-notice.py \
  scripts/worktree.py scripts/worktree-create.sh scripts/worktree-remove.sh; do
  if [[ ! -x "$plugin_dir/$relative" ]]; then
    printf 'test: installer did not place an executable plugin script %s\n' "$relative" >&2
    exit 1
  fi
done

if [[ ! -f "$agent_dir/airlock-worker.md" ]]; then
  printf 'test: installer missed the optional worker\n' >&2
  exit 1
fi

console_marker='Managed by https://github.com/Harshkamdar67/Airlock (console site)'
console_site="$install_dir/../share/airlock/console"
console_share="$install_dir/../share/airlock"

make_managed_console_site() {
  local directory="$1"
  local contents="$2"
  mkdir -p "$directory"
  printf '%s\n' "$console_marker" > "$directory/.airlock-managed"
  printf '%s\n' "$contents" > "$directory/index.html"
  chmod 0755 "$directory"
  chmod 0644 "$directory/.airlock-managed" "$directory/index.html"
}

run_console_install() {
  local case_root="$1"
  XDG_CONFIG_HOME="$case_root/config-home" \
  AIRLOCK_INSTALL_DIR="$case_root/bin" \
  AIRLOCK_AGENT_DIR="$case_root/agents" \
    "$repo_root/scripts/install.sh" --no-service
}

assert_no_console_staging() {
  local share="$1"
  local artifact=''
  for artifact in "$share"/.airlock-console-stage-*; do
    if [[ -e "$artifact" || -L "$artifact" ]]; then
      printf 'test: installer left Console staging behind: %s\n' "$artifact" >&2
      return 1
    fi
  done
}

assert_no_console_backups() {
  local share="$1"
  local artifact=''
  for artifact in "$share"/.airlock-console-backup-*; do
    if [[ -e "$artifact" || -L "$artifact" ]]; then
      printf 'test: installer left an unexpected Console backup: %s\n' "$artifact" >&2
      return 1
    fi
  done
}

if [[ ! -d "$console_site" || -L "$console_site" ]]; then
  printf 'test: installer missed or unsafely linked the Console site\n' >&2
  exit 1
fi
printf '%s\n' "$console_marker" | cmp -s - "$console_site/.airlock-managed"
"$test_python" - "$repo_root/console/dist" "$console_site" <<'PY'
import hashlib
import sys
from pathlib import Path


def inventory(root, ignored=()):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in ignored:
            continue
        if path.is_symlink():
            raise AssertionError(f"unsafe symlink in Console site: {relative}")
        if path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


assert inventory(sys.argv[1]) == inventory(sys.argv[2], {".airlock-managed"})
PY
assert_no_console_staging "$console_share"
assert_no_console_backups "$console_share"

# A successful update replaces the whole managed site, while sibling data in
# the Airlock share directory survives and all swap artifacts are removed.
printf 'stale asset\n' > "$console_site/stale.txt"
mkdir -p "$console_share/unrelated"
printf 'keep me\n' > "$console_share/unrelated/notes.txt"
"$repo_root/scripts/install.sh" --no-service >/dev/null
if [[ -e "$console_site/stale.txt" ]]; then
  printf 'test: installer merged the Console site instead of replacing it\n' >&2
  exit 1
fi
grep -qxF 'keep me' "$console_share/unrelated/notes.txt"
assert_no_console_staging "$console_share"
assert_no_console_backups "$console_share"

real_mv="$(command -v mv)"

# A first install activates staging directly and never creates a backup.
first_case="$tmp_dir/console-first-install"
first_stubs="$first_case/stubs"
mkdir -p "$first_stubs"
cat > "$first_stubs/mv" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$AIRLOCK_TEST_MV_LOG"
exec "$AIRLOCK_TEST_REAL_MV" "$@"
EOF
chmod 0755 "$first_stubs/mv"
if ! (
  export PATH="$first_stubs:$PATH"
  export AIRLOCK_TEST_REAL_MV="$real_mv"
  export AIRLOCK_TEST_MV_LOG="$first_case/mv.log"
  run_console_install "$first_case"
) > "$first_case/install.out" 2> "$first_case/install.err"; then
  printf 'test: first Console site install failed\n' >&2
  exit 1
fi
if grep -qF '.airlock-console-backup-' "$first_case/mv.log"; then
  printf 'test: first Console site install created a backup\n' >&2
  exit 1
fi
assert_no_console_staging "$first_case/share/airlock"
assert_no_console_backups "$first_case/share/airlock"

# If activation fails, the old site is renamed back atomically and staging is
# removed. The managed bundle remains unwritten because it is installed last.
activation_case="$tmp_dir/console-activation-failure"
activation_stubs="$activation_case/stubs"
activation_target="$activation_case/share/airlock/console"
mkdir -p "$activation_stubs"
make_managed_console_site "$activation_target" 'old activation bytes'
cp -R "$activation_target" "$activation_case/expected-site"
cat > "$activation_stubs/mv" <<'EOF'
#!/usr/bin/env bash
if [[ "$#" -eq 2 && "$1" == */.airlock-console-stage-* && "$2" == */console ]]; then
  exit 73
fi
exec "$AIRLOCK_TEST_REAL_MV" "$@"
EOF
chmod 0755 "$activation_stubs/mv"
if (
  export PATH="$activation_stubs:$PATH"
  export AIRLOCK_TEST_REAL_MV="$real_mv"
  run_console_install "$activation_case"
) > "$activation_case/install.out" 2> "$activation_case/install.err"; then
  printf 'test: installer ignored a Console activation failure\n' >&2
  exit 1
fi
grep -qxF \
  'install: could not activate the Airlock Console site; the previous site was restored.' \
  "$activation_case/install.err"
diff -r "$activation_case/expected-site" "$activation_target" >/dev/null
assert_no_console_staging "$activation_case/share/airlock"
assert_no_console_backups "$activation_case/share/airlock"
test ! -e "$activation_case/config-home/airlock/managed-bundle.json"

# The activated marker is checked byte-for-byte before the old site is
# deleted. A command stub corrupts it after the rename to exercise rollback.
validation_case="$tmp_dir/console-validation-failure"
validation_stubs="$validation_case/stubs"
validation_target="$validation_case/share/airlock/console"
mkdir -p "$validation_stubs"
make_managed_console_site "$validation_target" 'old validation bytes'
cp -R "$validation_target" "$validation_case/expected-site"
cat > "$validation_stubs/mv" <<'EOF'
#!/usr/bin/env bash
if [[ "$#" -eq 2 && "$1" == */.airlock-console-stage-* && "$2" == */console ]]; then
  "$AIRLOCK_TEST_REAL_MV" "$@" || exit
  printf 'not exact\n' >> "$2/.airlock-managed"
  exit 0
fi
exec "$AIRLOCK_TEST_REAL_MV" "$@"
EOF
chmod 0755 "$validation_stubs/mv"
if (
  export PATH="$validation_stubs:$PATH"
  export AIRLOCK_TEST_REAL_MV="$real_mv"
  run_console_install "$validation_case"
) > "$validation_case/install.out" 2> "$validation_case/install.err"; then
  printf 'test: installer accepted an inexact activated Console marker\n' >&2
  exit 1
fi
grep -qxF \
  'install: could not activate the Airlock Console site; the previous site was restored.' \
  "$validation_case/install.err"
diff -r "$validation_case/expected-site" "$validation_target" >/dev/null
assert_no_console_staging "$validation_case/share/airlock"
assert_no_console_backups "$validation_case/share/airlock"
test ! -e "$validation_case/config-home/airlock/managed-bundle.json"

# If activation and rollback both fail, the private backup container is kept
# and named in the error. The next run recovers it before attempting an update.
rollback_case="$tmp_dir/console-rollback-failure"
rollback_stubs="$rollback_case/stubs"
rollback_target="$rollback_case/share/airlock/console"
mkdir -p "$rollback_stubs"
make_managed_console_site "$rollback_target" 'old rollback bytes'
cp -R "$rollback_target" "$rollback_case/expected-site"
cat > "$rollback_stubs/mv" <<'EOF'
#!/usr/bin/env bash
if [[ "$#" -eq 2 && "$2" == */console ]]; then
  case "$1" in
    */.airlock-console-stage-*|*/.airlock-console-backup-*/site) exit 73 ;;
  esac
fi
exec "$AIRLOCK_TEST_REAL_MV" "$@"
EOF
chmod 0755 "$rollback_stubs/mv"
if (
  export PATH="$rollback_stubs:$PATH"
  export AIRLOCK_TEST_REAL_MV="$real_mv"
  run_console_install "$rollback_case"
) > "$rollback_case/install.out" 2> "$rollback_case/install.err"; then
  printf 'test: installer ignored a Console rollback failure\n' >&2
  exit 1
fi
test ! -e "$rollback_target"
rollback_backups=()
for candidate in "$rollback_case/share/airlock"/.airlock-console-backup-*; do
  [[ -e "$candidate" || -L "$candidate" ]] || continue
  rollback_backups+=("$candidate")
done
test "${#rollback_backups[@]}" -eq 1
rollback_backup="${rollback_backups[0]}"
test -d "$rollback_backup/site"
test ! -L "$rollback_backup"
test ! -L "$rollback_backup/site"
"$test_python" - "$rollback_backup" <<'PY'
from pathlib import Path
import stat
import sys

assert stat.S_IMODE(Path(sys.argv[1]).stat().st_mode) == 0o700
PY
diff -r "$rollback_case/expected-site" "$rollback_backup/site" >/dev/null
grep -qE \
  'previous site retained at: .*/\.airlock-console-backup-[^/]+/site$' \
  "$rollback_case/install.err"
assert_no_console_staging "$rollback_case/share/airlock"
test ! -e "$rollback_case/config-home/airlock/managed-bundle.json"
run_console_install "$rollback_case" > "$rollback_case/recovery.out"
grep -q '^Recovered Airlock Console site backup:' "$rollback_case/recovery.out"
assert_no_console_staging "$rollback_case/share/airlock"
assert_no_console_backups "$rollback_case/share/airlock"

# A target plus one valid backup means activation committed before a crash.
# The stale backup is removed, while unrelated sibling data remains.
stale_case="$tmp_dir/console-stale-backup"
stale_target="$stale_case/share/airlock/console"
stale_backup="$stale_case/share/airlock/.airlock-console-backup-stale"
make_managed_console_site "$stale_target" 'committed target bytes'
mkdir -p "$stale_backup"
chmod 0700 "$stale_backup"
make_managed_console_site "$stale_backup/site" 'superseded backup bytes'
mkdir -p "$stale_case/share/airlock/unrelated"
printf 'survives\n' > "$stale_case/share/airlock/unrelated/notes.txt"
run_console_install "$stale_case" > "$stale_case/install.out"
grep -q '^Removed stale Airlock Console site backup:' "$stale_case/install.out"
grep -qxF 'survives' "$stale_case/share/airlock/unrelated/notes.txt"
assert_no_console_staging "$stale_case/share/airlock"
assert_no_console_backups "$stale_case/share/airlock"

# A crash can leave the exclusive backup reservation empty either before the
# target move or just after recovery. It is removable only beside a valid,
# committed target.
empty_case="$tmp_dir/console-empty-backup"
empty_target="$empty_case/share/airlock/console"
empty_backup="$empty_case/share/airlock/.airlock-console-backup-empty"
make_managed_console_site "$empty_target" 'committed beside empty reservation'
mkdir -p "$empty_backup"
chmod 0700 "$empty_backup"
run_console_install "$empty_case" > "$empty_case/install.out"
grep -q '^Removed empty Airlock Console site backup reservation:' "$empty_case/install.out"
assert_no_console_staging "$empty_case/share/airlock"
assert_no_console_backups "$empty_case/share/airlock"
test -f "$empty_case/config-home/airlock/managed-bundle.json"

# Without a committed target, even a private empty reservation is ambiguous:
# it cannot restore old content and must be preserved for inspection.
empty_absent_case="$tmp_dir/console-empty-backup-target-absent"
empty_absent_backup="$empty_absent_case/share/airlock/.airlock-console-backup-empty"
mkdir -p "$empty_absent_backup"
chmod 0700 "$empty_absent_backup"
if run_console_install "$empty_absent_case" \
  > "$empty_absent_case/install.out" 2> "$empty_absent_case/install.err"; then
  printf 'test: installer accepted an empty Console backup without a target\n' >&2
  exit 1
fi
grep -q 'refusing unsafe Airlock Console site backup' "$empty_absent_case/install.err"
test -d "$empty_absent_backup"
assert_no_console_staging "$empty_absent_case/share/airlock"
test ! -e "$empty_absent_case/config-home/airlock/managed-bundle.json"

# Empty reservations with unsafe permissions are not cleanup debris. They are
# preserved and refused even when a valid target is present.
empty_mode_case="$tmp_dir/console-empty-backup-wrong-mode"
empty_mode_target="$empty_mode_case/share/airlock/console"
empty_mode_backup="$empty_mode_case/share/airlock/.airlock-console-backup-empty"
make_managed_console_site "$empty_mode_target" 'target beside unsafe empty reservation'
mkdir -p "$empty_mode_backup"
chmod 0777 "$empty_mode_backup"
if run_console_install "$empty_mode_case" \
  > "$empty_mode_case/install.out" 2> "$empty_mode_case/install.err"; then
  printf 'test: installer removed a world-writable empty Console backup\n' >&2
  exit 1
fi
grep -q 'refusing unsafe Airlock Console site backup' "$empty_mode_case/install.err"
test -d "$empty_mode_backup"
grep -qxF 'target beside unsafe empty reservation' "$empty_mode_target/index.html"
assert_no_console_staging "$empty_mode_case/share/airlock"
test ! -e "$empty_mode_case/config-home/airlock/managed-bundle.json"

# A valid-looking backup container is still unsafe unless it is owned by the
# current user and has mode exactly 0700.
mode_case="$tmp_dir/console-backup-wrong-mode"
mode_target="$mode_case/share/airlock/console"
mode_backup="$mode_case/share/airlock/.airlock-console-backup-mode"
make_managed_console_site "$mode_target" 'target beside writable backup'
mkdir -p "$mode_backup"
make_managed_console_site "$mode_backup/site" 'world-writable container bytes'
chmod 0777 "$mode_backup"
if run_console_install "$mode_case" \
  > "$mode_case/install.out" 2> "$mode_case/install.err"; then
  printf 'test: installer accepted a world-writable Console backup container\n' >&2
  exit 1
fi
grep -q 'refusing unsafe Airlock Console site backup' "$mode_case/install.err"
test -d "$mode_backup/site"
grep -qxF 'target beside writable backup' "$mode_target/index.html"
assert_no_console_staging "$mode_case/share/airlock"
test ! -e "$mode_case/config-home/airlock/managed-bundle.json"

# Group- or world-writable directories and files under site are never restored
# or deleted as valid stale backups.
for writable_part in site file marker; do
  writable_case="$tmp_dir/console-backup-writable-$writable_part"
  writable_target="$writable_case/share/airlock/console"
  writable_backup="$writable_case/share/airlock/.airlock-console-backup-writable"
  make_managed_console_site "$writable_target" "target for $writable_part check"
  mkdir -p "$writable_backup"
  chmod 0700 "$writable_backup"
  make_managed_console_site "$writable_backup/site" "backup for $writable_part check"
  case "$writable_part" in
    site) chmod 0775 "$writable_backup/site" ;;
    file) chmod 0664 "$writable_backup/site/index.html" ;;
    marker) chmod 0664 "$writable_backup/site/.airlock-managed" ;;
  esac
  if run_console_install "$writable_case" \
    > "$writable_case/install.out" 2> "$writable_case/install.err"; then
    printf 'test: installer accepted a writable Console backup %s\n' "$writable_part" >&2
    exit 1
  fi
  grep -q 'refusing unsafe Airlock Console site backup' "$writable_case/install.err"
  test -d "$writable_backup/site"
  grep -qxF "target for $writable_part check" "$writable_target/index.html"
  assert_no_console_staging "$writable_case/share/airlock"
  test ! -e "$writable_case/config-home/airlock/managed-bundle.json"
done

# Exercise the owner check when the suite itself can create a foreign-owned
# container without elevating privileges.
if [[ "$(id -u)" -eq 0 ]]; then
  owner_case="$tmp_dir/console-backup-wrong-owner"
  owner_target="$owner_case/share/airlock/console"
  owner_backup="$owner_case/share/airlock/.airlock-console-backup-owner"
  make_managed_console_site "$owner_target" 'target beside foreign backup'
  mkdir -p "$owner_backup"
  chmod 0700 "$owner_backup"
  make_managed_console_site "$owner_backup/site" 'foreign-owned backup bytes'
  chown 1 "$owner_backup"
  if run_console_install "$owner_case" \
    > "$owner_case/install.out" 2> "$owner_case/install.err"; then
    printf 'test: installer accepted a foreign-owned Console backup container\n' >&2
    exit 1
  fi
  grep -q 'refusing unsafe Airlock Console site backup' "$owner_case/install.err"
  test -d "$owner_backup/site"
  grep -qxF 'target beside foreign backup' "$owner_target/index.html"
  assert_no_console_staging "$owner_case/share/airlock"
  test ! -e "$owner_case/config-home/airlock/managed-bundle.json"
fi

# Multiple backup candidates are ambiguous, so none of them or the valid
# target may be changed and the bundle marker must remain unwritten.
multiple_case="$tmp_dir/console-multiple-backups"
multiple_target="$multiple_case/share/airlock/console"
make_managed_console_site "$multiple_target" 'multiple target bytes'
cp -R "$multiple_target" "$multiple_case/expected-site"
for suffix in one two; do
  container="$multiple_case/share/airlock/.airlock-console-backup-$suffix"
  mkdir -p "$container"
  chmod 0700 "$container"
  make_managed_console_site "$container/site" "backup $suffix bytes"
done
if run_console_install "$multiple_case" \
  > "$multiple_case/install.out" 2> "$multiple_case/install.err"; then
  printf 'test: installer accepted multiple Console backups\n' >&2
  exit 1
fi
grep -q 'refusing ambiguous Airlock Console site backup state' "$multiple_case/install.err"
diff -r "$multiple_case/expected-site" "$multiple_target" >/dev/null
test -d "$multiple_case/share/airlock/.airlock-console-backup-one/site"
test -d "$multiple_case/share/airlock/.airlock-console-backup-two/site"
assert_no_console_staging "$multiple_case/share/airlock"
test ! -e "$multiple_case/config-home/airlock/managed-bundle.json"

# A backup candidate must be a plain private container with exactly one plain,
# managed site child. Unsafe candidates are refused and never followed.
unsafe_case="$tmp_dir/console-unsafe-backup"
unsafe_target="$unsafe_case/share/airlock/console"
unsafe_outside="$unsafe_case/outside"
make_managed_console_site "$unsafe_target" 'unsafe target bytes'
make_managed_console_site "$unsafe_outside" 'outside bytes'
ln -s "$unsafe_outside" \
  "$unsafe_case/share/airlock/.airlock-console-backup-unsafe"
if run_console_install "$unsafe_case" \
  > "$unsafe_case/install.out" 2> "$unsafe_case/install.err"; then
  printf 'test: installer accepted a symlinked Console backup\n' >&2
  exit 1
fi
grep -q 'refusing unsafe Airlock Console site backup' "$unsafe_case/install.err"
test -L "$unsafe_case/share/airlock/.airlock-console-backup-unsafe"
grep -qxF 'outside bytes' "$unsafe_outside/index.html"
assert_no_console_staging "$unsafe_case/share/airlock"
test ! -e "$unsafe_case/config-home/airlock/managed-bundle.json"
rm "$unsafe_case/share/airlock/.airlock-console-backup-unsafe"
unsafe_backup="$unsafe_case/share/airlock/.airlock-console-backup-extra"
mkdir -p "$unsafe_backup"
chmod 0700 "$unsafe_backup"
make_managed_console_site "$unsafe_backup/site" 'backup bytes'
printf 'unexpected\n' > "$unsafe_backup/extra.txt"
if run_console_install "$unsafe_case" \
  > "$unsafe_case/extra.out" 2> "$unsafe_case/extra.err"; then
  printf 'test: installer accepted extra content in a Console backup container\n' >&2
  exit 1
fi
grep -q 'refusing unsafe Airlock Console site backup' "$unsafe_case/extra.err"
test -f "$unsafe_backup/extra.txt"
test -d "$unsafe_backup/site"
assert_no_console_staging "$unsafe_case/share/airlock"
test ! -e "$unsafe_case/config-home/airlock/managed-bundle.json"

# A fresh unmanaged Console directory must stop before the bundle marker is
# installed, and the directory must remain byte-for-byte untouched.
unmanaged_case="$tmp_dir/unmanaged-console-site"
unmanaged_backup="$unmanaged_case/share/airlock/.airlock-console-backup-valid"
mkdir -p "$unmanaged_case/bin" "$unmanaged_case/share/airlock/console" "$unmanaged_backup"
chmod 0700 "$unmanaged_backup"
printf 'person-owned site\n' > "$unmanaged_case/share/airlock/console/index.html"
make_managed_console_site "$unmanaged_backup/site" 'recoverable old site'
if XDG_CONFIG_HOME="$unmanaged_case/config-home" \
  AIRLOCK_INSTALL_DIR="$unmanaged_case/bin" AIRLOCK_AGENT_DIR="$unmanaged_case/agents" \
  "$repo_root/scripts/install.sh" --no-service \
  >"$unmanaged_case/install.out" 2>"$unmanaged_case/install.err"; then
  printf 'test: installer replaced an unmanaged Console site\n' >&2
  exit 1
fi
grep -q 'refusing to replace unmanaged Airlock Console site' "$unmanaged_case/install.err"
grep -qxF 'person-owned site' "$unmanaged_case/share/airlock/console/index.html"
grep -qxF 'recoverable old site' "$unmanaged_backup/site/index.html"
test ! -e "$unmanaged_case/config-home/airlock/managed-bundle.json"

# A reparse-like target on POSIX is a symlink. It must be refused without
# touching the linked directory or writing the final bundle marker.
symlink_case="$tmp_dir/symlink-console-site"
mkdir -p "$symlink_case/bin" "$symlink_case/share/airlock" "$symlink_case/outside"
printf 'outside\n' > "$symlink_case/outside/index.html"
ln -s "$symlink_case/outside" "$symlink_case/share/airlock/console"
if XDG_CONFIG_HOME="$symlink_case/config-home" \
  AIRLOCK_INSTALL_DIR="$symlink_case/bin" AIRLOCK_AGENT_DIR="$symlink_case/agents" \
  "$repo_root/scripts/install.sh" --no-service \
  >"$symlink_case/install.out" 2>"$symlink_case/install.err"; then
  printf 'test: installer accepted a symlinked Console site\n' >&2
  exit 1
fi
grep -q 'refusing unsafe Airlock Console site directory' "$symlink_case/install.err"
grep -qxF 'outside' "$symlink_case/outside/index.html"
test ! -e "$symlink_case/config-home/airlock/managed-bundle.json"

# When both normal proxy parents are blocked, the installer must send the
# documented directory overrides to every upstream auth command. It must not
# inspect or copy the synthetic auth marker used by this test.
(
  fallback_home="$tmp_dir/proxy-fallback-home"
  fallback_stubs="$tmp_dir/proxy-fallback-stubs"
  fallback_log="$tmp_dir/proxy-fallback.log"
  fallback_brew_log="$tmp_dir/proxy-fallback-brew.log"
  fallback_auth_state="$tmp_dir/proxy-fallback-authenticated"
  mkdir -p "$fallback_home" "$fallback_stubs"
  printf 'blocked config parent\n' > "$fallback_home/.config"
  printf 'blocked state parent\n' > "$fallback_home/.local"
  cat > "$fallback_stubs/claude" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then printf 'Claude Code test\n'; fi
if [[ "${1:-} ${2:-}" == "auth status" ]]; then exit 0; fi
exit 0
EOF
  cat > "$fallback_stubs/claude-code-proxy" <<'EOF'
#!/usr/bin/env bash
printf '%s|%s|%s\n' "${CCP_CONFIG_DIR:-unset}" "${XDG_STATE_HOME:-unset}" "$*" >> "$AIRLOCK_TEST_PROXY_LOG"
if [[ "${1:-}" == "--version" ]]; then printf 'Proxy test\n'; exit 0; fi
if [[ "${1:-} ${2:-} ${3:-}" == "codex auth status" ]]; then
  [[ -f "$AIRLOCK_TEST_AUTH_STATE" ]]
  exit
fi
if [[ "${1:-} ${2:-} ${3:-}" == "codex auth login" ]]; then
  [[ "$CCP_CONFIG_DIR" == "$AIRLOCK_TEST_EXPECTED_CONFIG" ]]
  [[ "$XDG_STATE_HOME" == "$AIRLOCK_TEST_EXPECTED_STATE" ]]
  : > "$AIRLOCK_TEST_AUTH_STATE"
  exit 0
fi
exit 0
EOF
  cat > "$fallback_stubs/brew" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$AIRLOCK_TEST_BREW_LOG"
exit 0
EOF
  chmod 0755 "$fallback_stubs/claude" "$fallback_stubs/claude-code-proxy" "$fallback_stubs/brew"
  expected_proxy_config="$fallback_home/.airlock/claude-code-proxy"
  expected_proxy_state="$fallback_home/.airlock/proxy-state"
  HOME="$fallback_home" XDG_CONFIG_HOME='' XDG_STATE_HOME='' AIRLOCK_CONFIG_DIR='' \
  AIRLOCK_INSTALL_DIR="$fallback_home/bin" AIRLOCK_AGENT_DIR="$fallback_home/agents" \
  AIRLOCK_TEST_PROXY_LOG="$fallback_log" AIRLOCK_TEST_BREW_LOG="$fallback_brew_log" \
  AIRLOCK_TEST_AUTH_STATE="$fallback_auth_state" \
  AIRLOCK_TEST_EXPECTED_CONFIG="$expected_proxy_config" \
  AIRLOCK_TEST_EXPECTED_STATE="$expected_proxy_state" \
  PATH="$fallback_stubs:/usr/bin:/bin" \
    bash "$repo_root/scripts/install.sh" --login --no-service >/dev/null
  test -f "$fallback_auth_state"
  test -d "$expected_proxy_config"
  test -d "$expected_proxy_state"
  python3 - "$expected_proxy_config" "$expected_proxy_state" <<'PY'
from pathlib import Path
import stat
import sys
for value in sys.argv[1:]:
    assert stat.S_IMODE(Path(value).stat().st_mode) == 0o700
PY
  grep -q "^$expected_proxy_config|$expected_proxy_state|codex auth status$" "$fallback_log"
  grep -q "^$expected_proxy_config|$expected_proxy_state|codex auth login$" "$fallback_log"
  HOME="$fallback_home" XDG_CONFIG_HOME='' XDG_STATE_HOME='' AIRLOCK_CONFIG_DIR='' \
  AIRLOCK_TEST_PROXY_LOG="$fallback_log" AIRLOCK_TEST_BREW_LOG="$fallback_brew_log" \
  AIRLOCK_TEST_AUTH_STATE="$fallback_auth_state" \
  PATH="$fallback_stubs:$fallback_home/bin:/usr/bin:/bin" \
    "$fallback_home/bin/airlock" proxy auth status >/dev/null
  HOME="$fallback_home" XDG_CONFIG_HOME='' XDG_STATE_HOME='' AIRLOCK_CONFIG_DIR='' \
  AIRLOCK_TEST_PROXY_LOG="$fallback_log" AIRLOCK_TEST_BREW_LOG="$fallback_brew_log" \
  AIRLOCK_TEST_AUTH_STATE="$fallback_auth_state" AIRLOCK_PROXY_URL='http://127.0.0.1:1' \
  PATH="$fallback_stubs:$fallback_home/bin:/usr/bin:/bin" \
    bash "$repo_root/scripts/doctor.sh" > "$tmp_dir/proxy-fallback-doctor.out" 2>&1 && {
      printf 'test: fallback Doctor ignored the intentionally closed health port\n' >&2
      exit 1
    }
  grep -q '^PASS  Codex OAuth is configured$' "$tmp_dir/proxy-fallback-doctor.out"
  test "$(grep -c "^$expected_proxy_config|$expected_proxy_state|codex auth status$" "$fallback_log")" -eq 3

  HOME="$fallback_home" XDG_CONFIG_HOME='' XDG_STATE_HOME='' AIRLOCK_CONFIG_DIR='' \
  AIRLOCK_INSTALL_DIR="$fallback_home/bin" AIRLOCK_AGENT_DIR="$fallback_home/agents" \
  AIRLOCK_TEST_PROXY_LOG="$fallback_log" AIRLOCK_TEST_BREW_LOG="$fallback_brew_log" \
  AIRLOCK_TEST_AUTH_STATE="$fallback_auth_state" \
  PATH="$fallback_stubs:/usr/bin:/bin" \
    bash "$repo_root/scripts/install.sh" >/dev/null
  case "$(uname -s)" in
    Darwin)
      fallback_service_file="$fallback_home/.airlock/claude-code-proxy.plist"
      plutil -lint "$fallback_service_file" >/dev/null
      ;;
    Linux) fallback_service_file="$fallback_home/.airlock/claude-code-proxy.service" ;;
  esac
  test -f "$fallback_service_file"
  grep -qF 'Managed by https://github.com/Harshkamdar67/Airlock' "$fallback_service_file"
  grep -qF "$expected_proxy_config" "$fallback_service_file"
  grep -qF "$expected_proxy_state" "$fallback_service_file"
  grep -q "^services start claude-code-proxy --file=$fallback_service_file$" "$fallback_brew_log"
)

# A file the installer does not recognize must stop the install rather than be
# replaced, and the refusal must leave the file exactly as it was.
printf 'unmanaged launcher\n' > "$install_dir/airlock"
cp "$install_dir/airlock" "$tmp_dir/unmanaged-before"
if "$repo_root/scripts/install.sh" --no-service > "$tmp_dir/refuse.out" 2> "$tmp_dir/refuse.err"; then
  printf 'test: installer accepted an unmanaged launcher\n' >&2
  exit 1
fi
grep -q 'refusing to overwrite existing unmanaged file' "$tmp_dir/refuse.err"
cmp "$install_dir/airlock" "$tmp_dir/unmanaged-before"

# An edited launcher that still carries the managed marker is the installer's
# own file, so this one is replaced rather than refused.
{ cat "$repo_root/bin/airlock"; printf '# local edit\n'; } > "$install_dir/airlock"
"$repo_root/scripts/install.sh" --no-service > /dev/null
cmp "$install_dir/airlock" "$repo_root/bin/airlock"

# Doctor has to verify a real install, so run it against one. The proxy port is
# closed on purpose: doctor must report that and still exit non-zero.
export PATH="$install_dir:$PATH"
export AIRLOCK_PROXY_URL='http://127.0.0.1:1'
if AIRLOCK_ACCESS_FILE="$tmp_dir/access.json" "$repo_root/scripts/doctor.sh" > "$tmp_dir/doctor.out" 2>&1; then
  printf 'test: doctor ignored an unhealthy proxy\n' >&2
  exit 1
fi
grep -q '^FAIL  Proxy health: http://127\.0\.0\.1:1/healthz$' "$tmp_dir/doctor.out"
grep -q '^PASS  Claude login is configured$' "$tmp_dir/doctor.out"
grep -q "^PASS  Launcher: $install_dir/airlock$" "$tmp_dir/doctor.out"
grep -q '^PASS  Managed bundle is current and complete$' "$tmp_dir/doctor.out"
grep -q "^PASS  Hybrid router: $install_dir/airlock-router.py" "$tmp_dir/doctor.out"
grep -q "^PASS  Release updater: $install_dir/airlock-update.py" "$tmp_dir/doctor.out"
grep -q "^PASS  Airlock Console helper: $install_dir/airlock_console.py$" "$tmp_dir/doctor.out"
grep -q "^PASS  Airlock Console tools helper: $install_dir/airlock_console_tools.py$" "$tmp_dir/doctor.out"
grep -q "^PASS  Airlock Console MCP wrapper: $plugin_dir/mcp-server/airlock_console_mcp.py$" "$tmp_dir/doctor.out"
grep -q "^PASS  Airlock Console site: $console_site$" "$tmp_dir/doctor.out"
grep -Eq '^(PASS  Airlock Console health: http://127\.0\.0\.1:4783/healthz|INFO  Airlock Console is not running at 127\.0\.0\.1:4783)$' "$tmp_dir/doctor.out"
grep -q '^INFO  OpenRouter registry is not configured: ' "$tmp_dir/doctor.out"
grep -q '^INFO  Doctor does not read the OpenRouter credential;' "$tmp_dir/doctor.out"
grep -q '^INFO  Open-model registry is not configured: ' "$tmp_dir/doctor.out"
grep -q '^INFO  Doctor did not contact a local inference server;' "$tmp_dir/doctor.out"
grep -q "^PASS  Session plugin: $plugin_dir$" "$tmp_dir/doctor.out"
grep -q '^PASS  Custom airlock-worker effort: ' "$tmp_dir/doctor.out"

openmodel_registry="$config_dir/openmodel-registry.json"
printf '%s\n' '{"base_url":"http://127.0.0.1:18095/v1"}' |
  "$install_dir/airlock_openmodel.py" --registry "$openmodel_registry" \
    endpoint add synthetic-server --stdin --max-concurrency 1 >/dev/null
printf '%s\n' \
  '{"upstream_model":"private/synthetic-model","accepted_response_models":["private/synthetic-model"]}' |
  "$install_dir/airlock_openmodel.py" --registry "$openmodel_registry" \
    add synthetic-route synthetic-server --stdin \
    --context-window 32768 --max-output-tokens 1024 \
    --streaming --tools none --no-worker >/dev/null
if AIRLOCK_ACCESS_FILE="$tmp_dir/access.json" "$repo_root/scripts/doctor.sh" > "$tmp_dir/doctor-openmodel.out" 2>&1; then
  printf 'test: doctor ignored an unhealthy proxy with a valid open-model registry\n' >&2
  exit 1
fi
grep -q '^PASS  Open-model registry is valid (1/1 endpoint(s) active, 1/1 route(s) active): ' "$tmp_dir/doctor-openmodel.out"
grep -q '^INFO  Open-model endpoint: synthetic-server (enabled, max-concurrency=1)$' "$tmp_dir/doctor-openmodel.out"
grep -q '^INFO  Open-model route: airlock-om-synthetic-route (endpoint=synthetic-server, active)$' "$tmp_dir/doctor-openmodel.out"
if grep -qE '18095|private/synthetic-model' "$tmp_dir/doctor-openmodel.out"; then
  printf 'test: doctor exposed private open-model registry data\n' >&2
  exit 1
fi
printf '%s\n' \
  '{"schema_version":1,"endpoints":[],"models":[],"private/synthetic-model":"hidden"}' \
  > "$openmodel_registry"
if AIRLOCK_ACCESS_FILE="$tmp_dir/access.json" "$repo_root/scripts/doctor.sh" > "$tmp_dir/doctor-openmodel-invalid.out" 2>&1; then
  printf 'test: doctor ignored an invalid open-model registry\n' >&2
  exit 1
fi
grep -q '^FAIL  Open-model registry is invalid: ' "$tmp_dir/doctor-openmodel-invalid.out"
if grep -q 'private/synthetic-model' "$tmp_dir/doctor-openmodel-invalid.out"; then
  printf 'test: doctor exposed private data from an invalid open-model registry\n' >&2
  exit 1
fi
rm -f "$openmodel_registry"

write_stub claude \
  'if [[ "${1:-}" == "--version" ]]; then printf "Claude Code test\\n"; fi' \
  'if [[ "${1:-} ${2:-}" == "auth status" ]]; then exit 1; fi'
if AIRLOCK_ACCESS_FILE="$tmp_dir/access.json" "$repo_root/scripts/doctor.sh" > "$tmp_dir/doctor-signed-out.out" 2>&1; then
  printf 'test: doctor ignored an unhealthy proxy while Claude was signed out\n' >&2
  exit 1
fi
grep -q '^INFO  Claude login was not detected; run: claude auth login$' "$tmp_dir/doctor-signed-out.out"
grep -q '^INFO  OpenAI-only sessions can still work, but hybrid and Claude routes need this login\.$' "$tmp_dir/doctor-signed-out.out"

printf 'All installer tests passed.\n'
