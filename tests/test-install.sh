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
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/airlock-install-test.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT

stub_dir="$tmp_dir/stubs"
install_dir="$tmp_dir/bin"
config_home="$tmp_dir/config"
agent_dir="$tmp_dir/agents"
config_dir="$config_home/airlock"
mkdir -p "$stub_dir"

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
write_stub claude-code-proxy 'if [[ "${1:-}" == "--version" ]]; then printf "Proxy test\\n"; fi'

export PATH="$stub_dir:$PATH"
export XDG_CONFIG_HOME="$config_home"
export AIRLOCK_INSTALL_DIR="$install_dir"
export AIRLOCK_AGENT_DIR="$agent_dir"
unset AIRLOCK_CONFIG_FILE AIRLOCK_ACCESS_FILE AIRLOCK_PLUGIN_DIR AIRLOCK_MANAGED_BUNDLE_FILE

"$repo_root/scripts/install.sh" --with-agent --no-service > "$tmp_dir/install.out"
grep -q '^Airlock installed\.$' "$tmp_dir/install.out"

for relative in airlock airlock-access.py airlock-router.py airlock-hybrid.py; do
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
grep -q "^PASS  Launcher: $install_dir/airlock$" "$tmp_dir/doctor.out"
grep -q '^PASS  Managed bundle is current and complete$' "$tmp_dir/doctor.out"
grep -q "^PASS  Hybrid router: $install_dir/airlock-router.py" "$tmp_dir/doctor.out"
grep -q "^PASS  Session plugin: $plugin_dir$" "$tmp_dir/doctor.out"
grep -q '^PASS  Custom airlock-worker effort: ' "$tmp_dir/doctor.out"

printf 'All installer tests passed.\n'
