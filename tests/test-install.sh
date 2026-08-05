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

for relative in airlock airlock-access.py airlock-update.py airlock-router.py airlock-hybrid.py; do
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

plugin_dir="$config_dir/plugins/airlock"
for relative in .claude-plugin/plugin.json hooks/hooks.json skills/usage/SKILL.md \
  scripts/file_safety.py; do
  if [[ ! -f "$plugin_dir/$relative" ]]; then
    printf 'test: installer missed plugin file %s\n' "$relative" >&2
    exit 1
  fi
done
# The hook scripts are launched directly by Claude Code, so the executable bit
# is part of a working install rather than a detail of the file mode.
for relative in scripts/agent-guard.sh scripts/agent-guard.py scripts/secret-guard.sh \
  scripts/secret-guard.py scripts/worktree.py scripts/worktree-create.sh \
  scripts/worktree-remove.sh; do
  if [[ ! -x "$plugin_dir/$relative" ]]; then
    printf 'test: installer did not place an executable plugin script %s\n' "$relative" >&2
    exit 1
  fi
done

if [[ ! -f "$agent_dir/airlock-worker.md" ]]; then
  printf 'test: installer missed the optional worker\n' >&2
  exit 1
fi

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
grep -q "^PASS  Session plugin: $plugin_dir$" "$tmp_dir/doctor.out"
grep -q '^PASS  Custom airlock-worker effort: ' "$tmp_dir/doctor.out"

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
